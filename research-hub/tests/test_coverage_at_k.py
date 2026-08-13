"""The coverage@k breadth diagnostic (HUB-044)."""

import unittest

from tests.coverage_at_k import (
    DEFAULT_KS,
    coverage_at_k,
    coverage_summary,
    micro_average,
    source_of,
)


def ranking(*document_ids: str) -> list[dict]:
    return [
        {"document_id": document_id, "chunk_index": index}
        for index, document_id in enumerate(document_ids)
    ]


def at(points: list[dict], k: int) -> dict:
    return next(point for point in points if point["k"] == k)


class Unit(unittest.TestCase):
    def test_the_unit_is_the_document_not_the_chunk(self):
        chunks = ranking("a", "a", "a")

        self.assertEqual([source_of(chunk) for chunk in chunks], ["a", "a", "a"])
        points = coverage_at_k(chunks, [3], sources_reachable=1)
        self.assertEqual(at(points, 3)["sources_at_k"], 1)

    def test_reads_dataclass_candidates_as_well_as_rows(self):
        from app.retrieval import EvidenceCandidate

        candidate = EvidenceCandidate(
            text="t", canonical_url="https://example.test/a", source_title="A",
            document_id="doc-a", chunk_index=0, score=0.5, metadata={},
        )

        self.assertEqual(source_of(candidate), "doc-a")


class CoverageCurve(unittest.TestCase):
    def test_counts_distinct_sources_in_each_top_k_prefix(self):
        points = coverage_at_k(
            ranking("a", "a", "b", "c"), [1, 2, 3, 4], sources_reachable=3
        )

        self.assertEqual([point["sources_at_k"] for point in points], [1, 1, 2, 3])

    def test_saturation_measures_breadth_against_what_k_allowed(self):
        # Four chunks, four sources: as broad as any ranking can be at k<=4.
        points = coverage_at_k(ranking("a", "b", "c", "d"), [2, 4], sources_reachable=4)

        self.assertEqual([point["saturation_at_k"] for point in points], [1.0, 1.0])

    def test_saturation_ceiling_is_the_pool_not_k_once_k_exceeds_it(self):
        # Two reachable sources: coverage@4 of 2 is the maximum, not a half.
        points = coverage_at_k(ranking("a", "b", "a", "b"), [4], sources_reachable=2)

        self.assertEqual(at(points, 4)["attainable_sources_at_k"], 2)
        self.assertEqual(at(points, 4)["saturation_at_k"], 1.0)

    def test_the_reachable_pool_must_be_named_never_inferred(self):
        # Inferring it from the ranking would score every tail k a free 1.0.
        with self.assertRaises(TypeError):
            coverage_at_k(ranking("a", "b"), [2])

    def test_scope_coverage_is_null_when_no_scope_denominator_is_meaningful(self):
        # Corpus-wide: the caller declines to divide by 679 (HUB-044).
        points = coverage_at_k(ranking("a", "b"), [2], sources_reachable=2)

        self.assertIsNone(at(points, 2)["scope_coverage_at_k"])

    def test_scope_coverage_divides_by_the_named_scope_when_given(self):
        points = coverage_at_k(
            ranking("a", "b"), [2], sources_reachable=2, sources_in_scope=8
        )

        self.assertEqual(at(points, 2)["scope_coverage_at_k"], 0.25)

    def test_a_short_ranking_reports_the_chunks_it_actually_had(self):
        points = coverage_at_k(ranking("a", "b"), [4], sources_reachable=4)

        # Not silently reported as coverage@2: k=4 asked for four chunks and
        # the ranking supplied two, so 2/4 saturation is the honest number.
        self.assertEqual(at(points, 4)["chunks_at_k"], 2)
        self.assertEqual(at(points, 4)["saturation_at_k"], 0.5)

    def test_empty_ranking_covers_nothing(self):
        points = coverage_at_k([], [1, 4], sources_reachable=10, sources_in_scope=10)

        self.assertEqual([point["sources_at_k"] for point in points], [0, 0])
        self.assertEqual([point["saturation_at_k"] for point in points], [0.0, 0.0])

    def test_points_are_sorted_deduplicated_and_validated(self):
        points = coverage_at_k(ranking("a", "b", "c"), [3, 1, 3], sources_reachable=3)

        self.assertEqual([point["k"] for point in points], [1, 3])
        with self.assertRaises(ValueError):
            coverage_at_k(ranking("a"), [0], sources_reachable=1)
        with self.assertRaises(ValueError):
            coverage_at_k(ranking("a"), [1], sources_reachable=0)

    def test_default_ks_span_the_deployed_candidate_pool(self):
        self.assertEqual(DEFAULT_KS[0], 1)
        self.assertEqual(max(DEFAULT_KS), 40)


class TheTradeoffThisMetricCannotSee(unittest.TestCase):
    def test_one_chunk_per_source_maximises_coverage_and_proves_nothing(self):
        """Why coverage is never a target and never replaces recall.

        Both rankings hold six chunks drawn from the same six-source pool.
        The first spreads them over six sources and scores a perfect 1.0; the
        second concentrates on two and scores 0.333. Coverage prefers the
        first *whatever the chunks say* — including when the second is the one
        that carried the whole argument. A change that moves this number is
        judged with recall in hand, or it is not judged.
        """
        broad = coverage_at_k(
            ranking("a", "b", "c", "d", "e", "f"), [6], sources_reachable=6
        )
        deep = coverage_at_k(
            ranking("a", "a", "a", "b", "b", "b"), [6], sources_reachable=6
        )

        self.assertEqual(at(broad, 6)["saturation_at_k"], 1.0)
        self.assertEqual(at(deep, 6)["saturation_at_k"], round(2 / 6, 6))


class Summary(unittest.TestCase):
    def test_summary_records_the_curve_and_the_denominators_it_used(self):
        summary = coverage_summary(
            ranking("a", "a", "b"), sources_in_scope=10, sources_reachable=5, ks=[3],
        )

        self.assertEqual(summary["chunks_selected"], 3)
        self.assertEqual(summary["sources_represented"], 2)
        self.assertEqual(summary["sources_in_scope"], 10)
        self.assertEqual(summary["sources_reachable"], 5)
        self.assertEqual(summary["chunks_per_represented_source"], 1.5)
        self.assertEqual(at(summary["curve"], 3)["scope_coverage_at_k"], 0.2)

    def test_summary_of_an_empty_ranking_divides_by_nothing(self):
        summary = coverage_summary([], sources_reachable=4, ks=[1])

        self.assertIsNone(summary["chunks_per_represented_source"])


class MicroAverage(unittest.TestCase):
    def test_pools_cases_by_summing_counts(self):
        curves = [
            coverage_at_k(ranking("a", "b"), [2], sources_reachable=2),
            coverage_at_k(ranking("c", "c"), [2], sources_reachable=4),
        ]

        pooled = at(micro_average(curves), 2)

        self.assertEqual(pooled["cases"], 2)
        self.assertEqual(pooled["sources_at_k"], 3)
        self.assertEqual(pooled["attainable_sources_at_k"], 4)
        self.assertEqual(pooled["saturation_at_k"], 0.75)

    def test_a_short_case_cannot_inflate_the_pool_with_a_free_1_0(self):
        # Averaging rates would score the one-chunk case 1.0 and report 0.75;
        # summing counts reports the two sources actually shown across five
        # attainable slots.
        curves = [
            coverage_at_k(ranking("a"), [4], sources_reachable=1),
            coverage_at_k(ranking("b", "b", "b", "b"), [4], sources_reachable=4),
        ]

        pooled = at(micro_average(curves), 4)

        self.assertEqual(pooled["sources_at_k"], 2)
        self.assertEqual(pooled["attainable_sources_at_k"], 5)
        self.assertEqual(pooled["saturation_at_k"], 0.4)

    def test_no_curves_pools_to_nothing(self):
        self.assertEqual(micro_average([]), [])


if __name__ == "__main__":
    unittest.main()
