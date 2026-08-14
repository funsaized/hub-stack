"""Marginal-gain evidence packing behind EVIDENCE_PACKING (HUB-049)."""

import unittest

from app.context import pack_by_marginal_gain, render_entry, token_count
from app.retrieval import EvidenceCandidate, candidate_relevance, pack_evidence


def candidate(document_id: str, text: str, score: float = 0.0, rrf: float | None = None):
    metadata = {} if rrf is None else {"rrf_score": rrf}
    return EvidenceCandidate(
        text=text,
        canonical_url=f"https://example.test/{document_id}",
        source_title=document_id.upper(),
        document_id=document_id,
        chunk_index=0,
        score=score,
        metadata=metadata,
    )


def render(index, value) -> str:
    return render_entry(index, value.source_title, value.canonical_url, value.text)


def pack(values, budget, **kwargs):
    return pack_by_marginal_gain(
        values, render, budget,
        relevance=candidate_relevance, text=lambda value: value.text,
        **kwargs,
    )


def entry_cost(values) -> int:
    """Budget that admits exactly all of `values` when packed in order."""
    total = 0
    for index, value in enumerate(values, 1):
        total += token_count(render(index, value)) + (2 if index > 1 else 0)
    return total


class DegeneratesToRankOrder(unittest.TestCase):
    def test_identical_to_rank_order_when_the_budget_fits_everything(self):
        values = [
            candidate("a", "alpha beta gamma", score=0.9),
            candidate("a", "alpha beta gamma delta", score=0.8),
            candidate("b", "entirely different subject matter", score=0.7),
        ]
        budget = entry_cost(values)

        selected, context = pack(values, budget)

        # No scarcity to arbitrate, so redundancy costs nothing and the two
        # packers must agree exactly -- the flag is a no-op here by design.
        from app.context import pack_complete_entries
        rank_selected, rank_context = pack_complete_entries(values, render, budget)
        self.assertEqual([v.text for v in selected], [v.text for v in rank_selected])
        self.assertEqual(context, rank_context)

    def test_empty_input_packs_nothing(self):
        self.assertEqual(pack([], 10_000), ([], ""))

    def test_equal_relevance_packs_in_arrival_order(self):
        values = [candidate(name, f"text {name}", score=0.5) for name in "abcd"]

        selected, _ = pack(values, entry_cost(values))

        self.assertEqual([v.document_id for v in selected], ["a", "b", "c", "d"])


class RedundancyIsPenalisedOnlyUnderPressure(unittest.TestCase):
    """A two-slot budget over a near-duplicate pair and two distinct spans."""

    def setUp(self):
        self.values = [
            candidate("a", "horizontal pod autoscaler scales replicas on cpu", 0.90),
            candidate("a", "horizontal pod autoscaler scales replicas on cpu use", 0.88),
            candidate("b", "vertical autoscaling adjusts container resource", 0.86),
            candidate("c", "unrelated tail candidate anchoring the score range", 0.10),
        ]
        self.budget = entry_cost(self.values[:2])

    def test_rank_order_spends_both_slots_on_the_duplicate_pair(self):
        from app.context import pack_complete_entries

        selected, _ = pack_complete_entries(self.values, render, self.budget)

        self.assertEqual([v.document_id for v in selected], ["a", "a"])

    def test_marginal_gain_gives_the_second_slot_to_the_distinct_span(self):
        selected, _ = pack(self.values, self.budget)

        self.assertEqual([v.document_id for v in selected], ["a", "b"])

    def test_the_top_ranked_span_is_still_taken_first(self):
        # Redundancy re-spends the budget; it never demotes the best evidence.
        selected, _ = pack(self.values, self.budget)

        self.assertEqual(selected[0].text, self.values[0].text)

    def test_a_large_relevance_gap_outweighs_redundancy(self):
        # The distinct alternative here is the range floor, so its normalised
        # relevance is 0 and the near-duplicate rightly keeps the slot.
        values = [
            self.values[0],
            self.values[1],
            candidate("c", "unrelated tail candidate anchoring the score range", 0.10),
        ]

        selected, _ = pack(values, entry_cost(values[:2]))

        self.assertEqual([v.document_id for v in selected], ["a", "a"])

    def test_selection_is_deterministic(self):
        first, _ = pack(self.values, self.budget)
        second, _ = pack(self.values, self.budget)

        self.assertEqual([v.text for v in first], [v.text for v in second])


class BudgetIsNeverExceeded(unittest.TestCase):
    def test_packed_context_stays_within_budget(self):
        values = [candidate(f"d{i}", f"span number {i} " * 20, 1.0 - i / 100) for i in range(30)]

        for budget in (0, 200, 1000, 5000):
            with self.subTest(budget=budget):
                _, context = pack(values, budget)

                self.assertLessEqual(token_count(context), max(budget, 0))

    def test_an_entry_larger_than_the_budget_is_skipped_not_truncated(self):
        values = [candidate("big", "x" * 5000, 0.9), candidate("small", "tiny", 0.1)]
        budget = entry_cost([values[1]]) + 10

        selected, _ = pack(values, budget)

        self.assertEqual([v.document_id for v in selected], ["small"])


class Relevance(unittest.TestCase):
    def test_fused_candidates_rank_by_rrf_not_by_a_meaningless_cosine(self):
        lexical_only = candidate("a", "text", score=0.0, rrf=0.03)
        dense = candidate("b", "other", score=0.0, rrf=0.01)

        self.assertGreater(candidate_relevance(lexical_only), candidate_relevance(dense))

    def test_unfused_candidates_fall_back_to_the_cosine(self):
        self.assertEqual(candidate_relevance(candidate("a", "text", score=0.42)), 0.42)


class PackEvidenceFlag(unittest.TestCase):
    def setUp(self):
        self.values = [
            candidate("a", "kubernetes horizontal pod autoscaler scales replicas on cpu", 0.90),
            candidate("a", "kubernetes horizontal pod autoscaler scales replicas on cpu load", 0.89),
            candidate("b", "vertical pod autoscaler instead adjusts container resource requests", 0.60),
        ]

    def evidence(self, packing: str, context_limit: int):
        return pack_evidence(
            self.values, system="S", question="q",
            context_limit=context_limit, answer_reserve=0, packing=packing,
        )

    def test_default_is_the_deployed_rank_packer(self):
        import inspect

        self.assertEqual(
            inspect.signature(pack_evidence).parameters["packing"].default, "rank"
        )

    def test_modes_agree_when_the_budget_is_generous(self):
        rank, _ = self.evidence("rank", 100_000)
        gain, _ = self.evidence("marginal_gain", 100_000)

        self.assertEqual([v.text for v in rank], [v.text for v in gain])

    def test_an_unknown_mode_is_rejected_rather_than_silently_ignored(self):
        with self.assertRaises(ValueError):
            self.evidence("diverse", 100_000)


def config(**overrides):
    from app.config import Config

    base = dict(
        redis_url="redis://localhost:6379/0", qdrant_url="http://q",
        ollama_url="http://o", llm_model="m", embedding_model="e",
        searxng_url="http://s", crawl4ai_url="http://c", crawl4ai_token="t",
        log_level="info", judge_api_key="placeholder",
    )
    base.update(overrides)
    return Config(**base)


class ConfigFlag(unittest.TestCase):
    def test_the_deployed_default_is_rank(self):
        self.assertEqual(config().evidence_packing, "rank")

    def test_config_rejects_an_unknown_packing_mode(self):
        with self.assertRaises(ValueError):
            config(evidence_packing="round_robin")

    def test_config_accepts_both_supported_modes(self):
        for mode in ("rank", "marginal_gain"):
            with self.subTest(mode=mode):
                self.assertEqual(config(evidence_packing=mode).evidence_packing, mode)


if __name__ == "__main__":
    unittest.main()
