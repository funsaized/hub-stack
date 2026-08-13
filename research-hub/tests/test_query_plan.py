"""Adaptive query planning tests (HUB-024, stage 1).

Fully offline and deterministic: the planner LLM and the embedding model are
replaced by stubs, so no case performs a live generation, embedding or search.

The load-bearing property under test is that breadth is EMERGENT from the
distinctness threshold and never a fixed sub-query count -- including the
collapse case, where the plan must reduce to exactly the pre-planning single
search.
"""

import asyncio
import json

import pytest

from app.config import Config
from app.query_plan import (
    PLANNER_MAX_CANDIDATES,
    FacetDecision,
    QueryPlan,
    RoundRecord,
    acquisition_provenance,
    admit_facets,
    cosine,
    coverage_summary,
    greedy_admit,
    interleave,
    normalize_candidate,
    facet_coverage,
    novelty_ratio,
    query_defects,
    parse_candidates,
    plan_gap_round,
    plan_queries,
    should_continue,
    single_query_plan,
)


class StubOllama:
    """Records calls so tests can assert the planner was (or was not) invoked.

    ``reply_queue`` / ``vector_queue`` serve successive calls, which is how a
    multi-round job's opening plan and its later gap passes are distinguished.
    """

    def __init__(self, reply=None, vectors=None, generate_error=None,
                 embed_error=None, reply_queue=None, vector_queue=None):
        self._reply = reply
        self._vectors = vectors
        self._reply_queue = list(reply_queue) if reply_queue is not None else None
        self._vector_queue = (
            list(vector_queue) if vector_queue is not None else None
        )
        self._generate_error = generate_error
        self._embed_error = embed_error
        self.generate_calls = []
        self.embed_calls = []

    async def generate(self, prompt, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self._generate_error:
            raise self._generate_error
        if self._reply_queue is not None:
            return self._reply_queue.pop(0) if self._reply_queue else '{"facets": []}'
        return self._reply

    async def embed_batch(self, texts):
        self.embed_calls.append(list(texts))
        if self._embed_error:
            raise self._embed_error
        if self._vector_queue is not None:
            # Both the planner and the pre-crawl ranker call embed_batch. Only
            # serve a queued set when its size matches the request, so the
            # ranker cannot consume vectors staged for a later planning round.
            if self._vector_queue and len(self._vector_queue[0]) == len(texts):
                return self._vector_queue.pop(0)
            return [E1] * len(texts)
        return self._vectors


def run(coro):
    return asyncio.run(coro)


# Orthogonal unit vectors: maximally distinct. Repeats of the same vector are
# maximally redundant. Both make admission decisions exact rather than fuzzy.
E1 = [1.0, 0.0, 0.0, 0.0]
E2 = [0.0, 1.0, 0.0, 0.0]
E3 = [0.0, 0.0, 1.0, 0.0]
E4 = [0.0, 0.0, 0.0, 1.0]


# --- cosine -----------------------------------------------------------------

def test_cosine_identical_orthogonal_and_degenerate():
    assert cosine(E1, E1) == pytest.approx(1.0)
    assert cosine(E1, E2) == pytest.approx(0.0)
    # A zero-norm vector has no direction; it must not divide by zero.
    assert cosine(E1, [0.0, 0.0, 0.0, 0.0]) == 0.0


def test_cosine_rejects_mismatched_dimensions():
    with pytest.raises(ValueError):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


# --- candidate parsing ------------------------------------------------------

def test_parse_candidates_normalizes_deduplicates_and_orders():
    raw = ('{"facets": ["  replication  mechanism ", "replication mechanism", '
           '"REPLICATION MECHANISM", "failure modes of the upgrade"]}')
    assert parse_candidates(raw) == [
        "replication mechanism", "failure modes of the upgrade",
    ]


def test_parse_candidates_drops_unusable_entries():
    raw = '{"facets": ["short", 42, null, "a usable facet query"]}'
    assert parse_candidates(raw) == ["a usable facet query"]


def test_parse_candidates_caps_candidate_count():
    facets = [f"distinct facet query number {i}" for i in range(100)]
    raw = '{"facets": [%s]}' % ", ".join(f'"{f}"' for f in facets)
    assert len(parse_candidates(raw)) == PLANNER_MAX_CANDIDATES


@pytest.mark.parametrize("raw", [
    "not json at all",
    '{"wrong_key": []}',
    '["a bare array"]',
    '{"facets": "not an array"}',
])
def test_parse_candidates_rejects_malformed_replies(raw):
    with pytest.raises(ValueError):
        parse_candidates(raw)


def test_normalize_candidate_bounds():
    assert normalize_candidate("tiny") is None
    assert normalize_candidate("x" * 5000) is None
    assert normalize_candidate("a reasonable query") == "a reasonable query"


# --- admission: breadth is emergent ----------------------------------------

def test_all_candidates_redundant_collapses_to_a_single_search():
    """The complexity signal: a narrow topic issues exactly one search."""
    plan = admit_facets(
        "narrow topic", ["one paraphrase of it", "another paraphrase of it"],
        [E1, E1, E1], distinct=0.85, relevance=0.0, max_facets=8,
    )
    assert plan.queries == ["narrow topic"]
    assert plan.collapsed is True
    assert plan.stop_reason == "collapse"
    assert [d.reason for d in plan.decisions] == [
        "topic_seed", "redundant", "redundant",
    ]


def test_distinct_candidates_widen_the_plan():
    plan = admit_facets(
        "broad topic", ["first distinct facet", "second distinct facet", "third distinct facet"],
        [E1, E2, E3, E4], distinct=0.85, relevance=0.0, max_facets=8,
    )
    assert plan.queries == ["broad topic", "first distinct facet", "second distinct facet", "third distinct facet"]
    assert plan.collapsed is False
    assert plan.stop_reason == "candidates_exhausted"


def test_breadth_is_not_a_fixed_count_at_one_threshold():
    """Same threshold, different evidence -> different breadth. No fixed N."""
    narrow = admit_facets("t", ["cand one x", "cand two x", "cand three x"], [E1, E1, E1, E1],
                          distinct=0.85, relevance=0.0, max_facets=8)
    middling = admit_facets("t", ["cand one x", "cand two x", "cand three x"], [E1, E1, E2, E1],
                            distinct=0.85, relevance=0.0, max_facets=8)
    wide = admit_facets("t", ["cand one x", "cand two x", "cand three x"], [E1, E2, E3, E4],
                        distinct=0.85, relevance=0.0, max_facets=8)
    assert [len(p.queries) for p in (narrow, middling, wide)] == [1, 2, 4]


def test_admission_compares_against_the_whole_admitted_set():
    """A candidate redundant with facet 1 is refused even if distinct from the
    topic -- otherwise near-duplicate facets would both be admitted."""
    plan = admit_facets(
        "topic", ["first distinct facet", "near duplicate of a"], [E1, E2, E2],
        distinct=0.85, relevance=0.0, max_facets=8,
    )
    assert plan.queries == ["topic", "first distinct facet"]
    assert plan.decisions[-1].reason == "redundant"
    assert plan.decisions[-1].max_cosine == pytest.approx(1.0)


def test_threshold_governs_admission_at_the_boundary():
    # cos = 0.8 for these two vectors: admitted at 0.85, refused at 0.75.
    near = [0.8, 0.6, 0.0, 0.0]
    admitted = admit_facets("t", ["a boundary candidate"], [E1, near], distinct=0.85, relevance=0.0, max_facets=8)
    refused = admit_facets("t", ["a boundary candidate"], [E1, near], distinct=0.75, relevance=0.0, max_facets=8)
    assert len(admitted.queries) == 2
    assert len(refused.queries) == 1


def test_max_facets_is_a_rail_and_is_recorded_not_silent():
    plan = admit_facets(
        "topic", ["first distinct facet", "second distinct facet", "third distinct facet"], [E1, E2, E3, E4],
        distinct=0.85, relevance=0.0, max_facets=2,
    )
    assert plan.queries == ["topic", "first distinct facet"]
    assert plan.stop_reason == "max_facets"
    # The truncated candidates are still visible in provenance.
    assert [d.reason for d in plan.decisions if not d.admitted] == [
        "max_facets", "max_facets",
    ]


def test_topic_is_always_the_first_query():
    plan = admit_facets("the topic", ["first distinct facet"], [E1, E2],
                        distinct=0.85, relevance=0.0, max_facets=8)
    assert plan.queries[0] == "the topic"
    assert plan.decisions[0] == FacetDecision(
        query="the topic", admitted=True, reason="topic_seed",
    )


def test_admit_facets_rejects_inconsistent_inputs():
    with pytest.raises(ValueError):
        admit_facets("t", ["a", "b"], [E1, E2], distinct=0.85, relevance=0.0, max_facets=8)
    with pytest.raises(ValueError):
        admit_facets("t", [], [E1], distinct=0.85, relevance=0.0, max_facets=0)


# --- plan_queries: degradation is always to today's behavior ---------------

def test_plan_queries_admits_distinct_facets():
    ollama = StubOllama(
        reply='{"facets": ["facet alpha here", "facet beta here"]}',
        vectors=[E1, E2, E3],
    )
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, relevance=0.0, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic", "facet alpha here", "facet beta here"]
    assert ollama.embed_calls == [["a topic", "facet alpha here",
                                   "facet beta here"]]


def test_planner_generation_failure_degrades_to_one_search():
    ollama = StubOllama(generate_error=RuntimeError("ollama down"))
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, relevance=0.0, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "planner_unavailable"


def test_planner_malformed_reply_degrades_to_one_search():
    ollama = StubOllama(reply="I cannot help with that.")
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, relevance=0.0, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "planner_unavailable"


def test_embedding_failure_degrades_to_one_search():
    ollama = StubOllama(reply='{"facets": ["facet alpha here"]}',
                        embed_error=RuntimeError("embed down"))
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, relevance=0.0, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "planner_unavailable"


def test_wrong_embedding_count_degrades_to_one_search():
    ollama = StubOllama(reply='{"facets": ["facet alpha here"]}',
                        vectors=[E1])  # missing the candidate's vector
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, relevance=0.0, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "planner_unavailable"


def test_planner_returning_no_facets_is_a_collapse_not_a_failure():
    ollama = StubOllama(reply='{"facets": []}')
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, relevance=0.0, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "collapse"
    assert ollama.embed_calls == []


def test_search_budget_of_one_spends_no_planner_call():
    ollama = StubOllama(reply='{"facets": ["facet alpha here"]}', vectors=[E1, E2])
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, relevance=0.0, max_facets=8,
                            search_budget=1))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "budget"
    assert ollama.generate_calls == []


def test_search_budget_bounds_the_plan_below_max_facets():
    ollama = StubOllama(
        reply='{"facets": ["facet alpha here", "facet beta here"]}',
        vectors=[E1, E2, E3],
    )
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, relevance=0.0, max_facets=8,
                            search_budget=2))
    assert len(plan.queries) == 2
    assert plan.stop_reason == "max_facets"


# --- merge order ------------------------------------------------------------

def test_interleave_shares_the_crawl_budget_across_facets():
    """Concatenation would let facet 1 consume the whole depth cap."""
    merged = interleave([
        [{"url": "a1"}, {"url": "a2"}],
        [{"url": "b1"}, {"url": "b2"}],
    ])
    assert [r["url"] for r in merged] == ["a1", "b1", "a2", "b2"]


def test_interleave_handles_uneven_and_empty_facets():
    merged = interleave([[{"url": "a1"}], [], [{"url": "c1"}, {"url": "c2"}]])
    assert [r["url"] for r in merged] == ["a1", "c1", "c2"]
    assert interleave([]) == []
    assert interleave([[], []]) == []


def test_interleave_of_a_single_facet_is_the_identity():
    """The flag-off path runs through this function; it must not reorder."""
    results = [{"url": f"u{i}"} for i in range(5)]
    assert interleave([results]) == results


# --- provenance and the disabled path --------------------------------------

def test_single_query_plan_is_collapsed_with_its_reason_recorded():
    plan = single_query_plan("a topic", "planning_disabled")
    assert plan.queries == ["a topic"]
    assert plan.collapsed is True
    assert plan.provenance() == {
        "queries": ["a topic"],
        "facet_count": 1,
        "collapsed": True,
        "stop_reason": "planning_disabled",
        "decisions": [],
    }


def test_provenance_exposes_every_admission_decision():
    plan = admit_facets("topic", ["first distinct facet", "a duplicate query"], [E1, E2, E2],
                        distinct=0.85, relevance=0.0, max_facets=8)
    provenance = plan.provenance()
    assert provenance["facet_count"] == 2
    assert provenance["collapsed"] is False
    assert provenance["decisions"] == [
        {"query": "topic", "admitted": True, "reason": "topic_seed",
         "max_cosine": None, "topic_cosine": None, "defects": []},
        {"query": "first distinct facet", "admitted": True, "reason": "distinct",
         "max_cosine": 0.0, "topic_cosine": 0.0, "defects": []},
        {"query": "a duplicate query", "admitted": False, "reason": "redundant",
         "max_cosine": 1.0, "topic_cosine": 0.0, "defects": []},
    ]


def test_query_plan_is_json_serializable_for_job_progress():
    import json
    plan = QueryPlan(queries=["a", "b"],
                     decisions=[FacetDecision("a", True, "topic_seed")],
                     stop_reason="candidates_exhausted")
    assert json.loads(json.dumps(plan.provenance()))["facet_count"] == 2


# --- acquisition path: how many searches a job actually issues -------------

class ProbeStop(RuntimeError):
    """Sentinel: acquisition finished, stop before ingestion."""


def _acquisition_probe(monkeypatch, *, planning, plan_reply=None, vectors=None,
                       results_per_query=3, reply_queue=None,
                       vector_queue=None, results_for=None, **config_overrides):
    """Drive ResearchOrchestrator.run_job through acquisition, then stop.

    Crawls succeed against a stub (no DNS, no fetch) so facet coverage is
    real, and ingestion raises a sentinel immediately after. The orchestrator
    is built without __init__ because only the acquisition collaborators are
    under test.
    """
    from app import research as research_module
    from app.research import ResearchOrchestrator

    async def allow_everything(_url):
        return None

    monkeypatch.setattr(research_module, "vet_destination_async",
                        allow_everything)

    searched: list[str] = []
    progress: dict = {}

    class Searx:
        async def search(self, query, max_results=20, language="en"):
            # Host names derive from call order, not hash(), so the policy
            # record is identical on every run and interpreter.
            facet_index = len(searched)
            searched.append(query)
            if results_for is not None:
                return [{"url": u} for u in results_for(query, facet_index)]
            return [
                {"url": f"https://f{facet_index}-r{i}.example.com/p"}
                for i in range(results_per_query)
            ]

    class Crawler:
        async def crawl(self, url, *, respect_robots_txt=True):
            return {"url": url, "title": "t", "markdown": "body text"}

    class Documents:
        def save(self, *_args, **_kwargs):
            raise ProbeStop("acquisition complete")

    orchestrator = object.__new__(ResearchOrchestrator)
    # The synthetic facet vectors are orthogonal unit vectors, so their topic
    # cosine is 0.0 by construction. Cases that are not about the relevance
    # floor make it inert rather than contort their fixtures around it.
    config_overrides.setdefault("plan_facet_relevance", 0.0)
    config_overrides.setdefault("search_pacing_seconds", 0.0)
    orchestrator.cfg = _config(report_query_planning=planning,
                               **config_overrides)
    orchestrator.ollama = StubOllama(reply=plan_reply, vectors=vectors,
                                     reply_queue=reply_queue,
                                     vector_queue=vector_queue)
    orchestrator.searxng = Searx()

    class NoFallback:
        """The keyed fallback is unset in tests: SearXNG-only behaviour."""
        configured = False

        async def search(self, *_args, **_kwargs):
            return []

    orchestrator.serper = NoFallback()
    orchestrator.crawl4ai = Crawler()
    orchestrator.documents = Documents()

    async def get_job(job_id):
        return {"topic": "a research topic", "depth": 10, "max_sources": 12,
                "language": "en", "tags": [], "per_domain_limit": 2}

    async def update_job(job_id, **fields):
        progress.update(fields.get("progress") or {})

    orchestrator.get_job = get_job
    orchestrator._update_job = update_job

    with pytest.raises(ProbeStop):
        run(orchestrator.run_job("job-1"))
    return searched, progress


def test_planning_disabled_issues_exactly_one_search_and_no_planner_call(
        monkeypatch):
    """The flag-off path: one search on the bare topic, planner never invoked."""
    searched, progress = _acquisition_probe(monkeypatch, planning=False)
    assert searched == ["a research topic"]
    assert progress["query_plan"]["stop_reason"] == "planning_disabled"
    assert progress["query_plan"]["collapsed"] is True


def test_collapsed_plan_issues_exactly_one_search(monkeypatch):
    """A topic whose candidates collapse is equivalent to today's job."""
    searched, progress = _acquisition_probe(
        monkeypatch, planning=True,
        plan_reply='{"facets": ["a paraphrase of the topic"]}',
        vectors=[E1, E1],
    )
    assert searched == ["a research topic"]
    assert progress["query_plan"]["stop_reason"] == "collapse"


def test_admitted_facets_issue_one_search_each(monkeypatch):
    searched, progress = _acquisition_probe(
        monkeypatch, planning=True,
        plan_reply='{"facets": ["facet alpha here", "facet beta here"]}',
        vectors=[E1, E2, E3],
    )
    assert searched == ["a research topic", "facet alpha here",
                        "facet beta here"]
    assert progress["query_plan"]["facet_count"] == 3
    assert progress["query_plan"]["collapsed"] is False


def test_planner_failure_still_acquires_on_the_single_query(monkeypatch):
    """A dead planner must not fail the job -- it degrades to one search."""
    searched, progress = _acquisition_probe(
        monkeypatch, planning=True, plan_reply="not json",
    )
    assert searched == ["a research topic"]
    assert progress["query_plan"]["stop_reason"] == "planner_unavailable"


def test_every_sub_query_result_passes_through_the_source_policy(monkeypatch):
    """Cross-facet results are policy-vetted and canonically deduplicated in
    one pass, so no facet can smuggle an unvetted URL into the crawl set."""
    searched, progress = _acquisition_probe(
        monkeypatch, planning=True,
        plan_reply='{"facets": ["facet alpha here", "facet beta here"]}',
        vectors=[E1, E2, E3],
    )
    assert len(searched) == 3
    decisions = progress["crawl_policy"]
    # Every candidate from every facet is represented in the policy record.
    assert len(decisions) == 9
    assert all(d["canonical_url"].startswith("https://") for d in decisions)


# --- pre-issue QPP: score a query before spending a search on it -----------

def test_run_together_words_are_rejected_before_a_search_is_spent():
    """The exact malformation the 2026-08-13 planner emitted."""
    assert "run_together_words" in query_defects(
        "postgresql streamingReplication physicalToLogical"
    )


@pytest.mark.parametrize("query", [
    "Postgres logical replication for major version upgrades",
    "PostgreSQL WAL archive retention policy",
    "how to run pg_upgrade without downtime",
    "upgrading from 14 to 16 with libc/libssl changes",
])
def test_legitimate_queries_are_not_flagged(query):
    """Capitalised names, acronyms, underscores and slashes are all normal."""
    assert query_defects(query) == []


@pytest.mark.parametrize("query,defect", [
    ("two words", "too_few_words"),
    (" ".join(["word"] * 40), "too_many_words"),
    ("--- +++ === 123 456", "low_alphabetic_ratio"),
])
def test_query_defects_flags_unusable_shapes(query, defect):
    assert defect in query_defects(query)


def test_malformed_candidates_are_refused_and_recorded():
    plan = admit_facets(
        "topic", ["camelCase soupHere now", "a well formed facet"],
        [E1, E2, E3], distinct=0.85, relevance=0.0, max_facets=8,
    )
    assert plan.queries == ["topic", "a well formed facet"]
    refused = plan.decisions[1]
    assert refused.reason == "malformed"
    assert refused.defects == ("run_together_words",)


def test_defects_are_recorded_even_on_admitted_queries():
    """QPP output is provenance, not just a gate -- it must be calibratable."""
    plan = admit_facets("topic", ["a well formed facet"], [E1, E2],
                        distinct=0.85, relevance=0.0, max_facets=8)
    assert plan.decisions[1].as_dict()["defects"] == []


# --- the two-sided bar: distinct AND on-topic -------------------------------

def test_off_topic_candidate_is_refused_however_distinct_it_is():
    """The 2026-08-13 drift: maximally distinct is often least relevant."""
    plan = admit_facets(
        "topic", ["a totally unrelated subject"], [E1, E2],
        distinct=0.85, relevance=0.35, max_facets=8,
    )
    assert plan.queries == ["topic"]
    refused = plan.decisions[1]
    assert refused.reason == "off_topic"
    assert refused.topic_cosine == pytest.approx(0.0)


def test_a_candidate_must_clear_both_halves_of_the_bar():
    # Related to the topic (cos 0.6) but not a duplicate of it -> admitted.
    related = [0.6, 0.8, 0.0, 0.0]
    plan = admit_facets("topic", ["a related distinct facet"], [E1, related],
                        distinct=0.85, relevance=0.35, max_facets=8)
    assert len(plan.queries) == 2
    assert plan.decisions[1].topic_cosine == pytest.approx(0.6)
    # Too close to the topic -> redundant, even though it is plainly relevant.
    plan = admit_facets("topic", ["a near restatement of topic"], [E1, E1],
                        distinct=0.85, relevance=0.35, max_facets=8)
    assert plan.queries == ["topic"]
    assert plan.decisions[1].reason == "redundant"


def test_relevance_floor_applies_to_gap_queries_too():
    ollama = StubOllama(reply='{"facets": ["an off topic gap query"]}',
                        vectors=[E4])
    fresh, _, decisions, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 3, 2)], ["topic"], [E1],
        distinct=0.85, relevance=0.35, max_total=12,
    ))
    assert fresh == []
    assert reason == "coverage"
    assert decisions[0].reason == "off_topic"


def test_config_rejects_a_relevance_floor_above_the_distinctness_ceiling():
    """Otherwise no candidate could satisfy both halves and breadth dies."""
    with pytest.raises(ValueError):
        _config(plan_facet_relevance=0.9, plan_facet_distinct=0.85)


# --- stage 2: coverage saturation and the stop decision --------------------

def test_novelty_ratio_is_the_new_fraction_and_safe_when_empty():
    """Still recorded for calibration; no longer controls anything."""
    assert novelty_ratio(2, 8) == pytest.approx(0.25)
    assert novelty_ratio(0, 0) == 0.0


def test_facet_coverage_counts_facets_holding_a_retained_document():
    query_results = {
        "q1": {"https://a", "https://b"},
        "q2": {"https://c"},
        "q3": {"https://d"},
    }
    covered, issued = facet_coverage(query_results, {"https://a", "https://c"})
    assert (covered, issued) == (2, 3)


def test_facet_coverage_credits_a_facet_a_later_round_answered():
    """Coverage is recomputed over every issued query, not just this round's."""
    query_results = {"early facet": {"https://x"}}
    assert facet_coverage(query_results, set()) == (0, 1)
    assert facet_coverage(query_results, {"https://x"}) == (1, 1)


def test_rounds_continue_while_each_one_covers_another_facet():
    keep_going, reason = should_continue(
        round_index=1, covered_facets=3, issued_facets=5,
        previous_covered_facets=0, max_rounds=3,
    )
    assert (keep_going, reason) == (True, "continue")


def test_a_round_that_covers_no_new_facet_stops_the_research():
    keep_going, reason = should_continue(
        round_index=2, covered_facets=4, issued_facets=6,
        previous_covered_facets=4, max_rounds=5,
    )
    assert (keep_going, reason) == (False, "coverage_plateau")


def test_plateau_is_attributed_before_the_round_budget():
    """When both would fire, the stop is the research converging."""
    keep_going, reason = should_continue(
        round_index=3, covered_facets=4, issued_facets=6,
        previous_covered_facets=4, max_rounds=3,
    )
    assert (keep_going, reason) == (False, "coverage_plateau")


def test_round_budget_stops_a_still_productive_search():
    keep_going, reason = should_continue(
        round_index=3, covered_facets=9, issued_facets=12,
        previous_covered_facets=4, max_rounds=3,
    )
    assert (keep_going, reason) == (False, "max_rounds")


def test_the_stop_rule_needs_no_tuned_threshold():
    """A round raising the covered count by one is progress; by zero is not."""
    assert should_continue(round_index=2, covered_facets=5, issued_facets=9,
                           previous_covered_facets=4, max_rounds=9)[0] is True
    assert should_continue(round_index=2, covered_facets=4, issued_facets=9,
                           previous_covered_facets=4, max_rounds=9)[0] is False


def test_every_facet_answered_ends_the_research():
    """Rounds finish covering the plan; they do not invent new angles."""
    keep_going, reason = should_continue(
        round_index=1, covered_facets=4, issued_facets=4,
        previous_covered_facets=0, max_rounds=5,
    )
    assert (keep_going, reason) == (False, "coverage_complete")


# --- stage 2: gap-driven rounds --------------------------------------------

def test_gap_queries_are_held_to_the_bar_against_every_issued_query():
    """A gap query redundant with round 1's facets is refused, not issued."""
    ollama = StubOllama(reply='{"facets": ["a redundant gap query", "genuine gap here"]}',
                        vectors=[E2, E4])
    fresh, vectors, decisions, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 3, 2), ("first distinct facet", 1, 1)],
        ["topic", "first distinct facet"], [E1, E2],
        distinct=0.85, relevance=0.0, max_total=12,
    ))
    assert fresh == ["genuine gap here"]
    assert reason == "continue"
    assert [d.reason for d in decisions] == ["redundant", "distinct"]
    assert len(vectors) == 3


def test_gap_pass_returning_nothing_is_the_coverage_stop():
    ollama = StubOllama(reply='{"facets": []}')
    fresh, _, _, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 5, 4)], ["topic"], [E1],
        distinct=0.85, relevance=0.0, max_total=12,
    ))
    assert fresh == []
    assert reason == "coverage"


def test_gap_pass_whose_every_proposal_is_covered_is_also_coverage():
    ollama = StubOllama(reply='{"facets": ["already covered here"]}',
                        vectors=[E1])
    fresh, _, _, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 5, 4)], ["topic"], [E1],
        distinct=0.85, relevance=0.0, max_total=12,
    ))
    assert fresh == []
    assert reason == "coverage"


def test_gap_pass_failure_ends_rounds_without_failing_the_job():
    ollama = StubOllama(generate_error=RuntimeError("ollama down"))
    fresh, _, _, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 5, 4)], ["topic"], [E1],
        distinct=0.85, relevance=0.0, max_total=12,
    ))
    assert fresh == []
    assert reason == "planner_unavailable"


def test_gap_pass_respects_the_search_budget_without_calling_the_planner():
    ollama = StubOllama(reply='{"facets": ["a gap query here"]}', vectors=[E4])
    fresh, _, _, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 1, 1)], ["topic", "b", "c"], [E1, E2, E3],
        distinct=0.85, relevance=0.0, max_total=3,
    ))
    assert fresh == []
    assert reason == "budget"
    assert ollama.generate_calls == []


def test_coverage_summary_names_each_query_and_its_yield():
    rendered = coverage_summary("the topic", [("the topic", 4, 3),
                                              ("a facet", 0, 0)])
    assert "the topic" in rendered
    assert '"a facet": 0 document(s) from 0 domain(s)' in rendered


def test_greedy_admit_rejects_inconsistent_inputs():
    with pytest.raises(ValueError):
        greedy_admit(["a"], [E1], ["b"], [], distinct=0.85, relevance=0.0, max_total=4)
    with pytest.raises(ValueError):
        greedy_admit(["a"], [], ["b"], [E2], distinct=0.85, relevance=0.0, max_total=4)
    with pytest.raises(ValueError):
        greedy_admit(["a"], [E1], ["b"], [E2], distinct=0.85, relevance=0.0, max_total=0)


# --- stage 2: rounds through the acquisition path ---------------------------

def test_a_collapsed_plan_never_opens_a_second_round(monkeypatch):
    """The equivalence guarantee: a simple topic is one search, full stop."""
    searched, progress = _acquisition_probe(
        monkeypatch, planning=True,
        reply_queue=['{"facets": ["a paraphrase of the topic"]}'],
        vector_queue=[[E1, E1]],
    )
    assert searched == ["a research topic"]
    assert progress["query_plan"]["acquisition_stop_reason"] == "single_round"
    assert len(progress["query_plan"]["rounds"]) == 1


def test_crawl_allowance_scales_with_the_number_of_facets(monkeypatch):
    """Breadth has to buy fetches or it buys nothing.

    Regression for the 2026-08-13 run 4, where a five-facet plan saw 100
    candidates and crawled 6 -- the single-query budget -- so planning bought
    query diversity and no additional sources at all.
    """
    def results_for(query, facet_index):
        return [f"https://f{facet_index}-r{i}.example.com/p" for i in range(8)]

    _searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=['{"facets": ["facet alpha here", "facet beta here"]}'],
        vector_queue=[[E1, E2, E3]],
    )
    # depth is 10 per facet across 3 facets, so the round may fetch far more
    # than one query's worth.
    assert progress["query_plan"]["rounds"][0]["crawled"] == 24


def test_a_collapsed_plan_keeps_the_single_query_crawl_budget(monkeypatch):
    """The equivalence guarantee covers cost, not just query count."""
    def results_for(query, facet_index):
        return [f"https://f{facet_index}-r{i}.example.com/p" for i in range(30)]

    _searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=['{"facets": ["one paraphrase of it"]}'],
        vector_queue=[[E1, E1]],
    )
    # depth is 10 and the plan collapsed, so exactly one query's budget.
    assert progress["query_plan"]["rounds"][0]["crawled"] == 10


def test_the_job_wide_crawl_rail_still_binds(monkeypatch):
    def results_for(query, facet_index):
        return [f"https://f{facet_index}-r{i}.example.com/p" for i in range(20)]

    _searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=['{"facets": ["facet alpha here", "facet beta here"]}'],
        vector_queue=[[E1, E2, E3]],
        plan_crawl_budget=7,
    )
    assert progress["query_plan"]["rounds"][0]["crawled"] == 7


def test_every_facet_covered_in_one_round_never_opens_a_second(monkeypatch):
    """The normal case: one round answers the plan, so the research ends."""
    searched, progress = _acquisition_probe(
        monkeypatch, planning=True,
        reply_queue=['{"facets": ["facet alpha here", "facet beta here"]}',
                     '{"facets": ["the gap query here"]}'],
        vector_queue=[[E1, E2, E3], [E4]],
        plan_max_rounds=3,
    )
    assert searched == ["a research topic", "facet alpha here",
                        "facet beta here"]
    plan = progress["query_plan"]
    assert plan["acquisition_stop_reason"] == "coverage_complete"
    assert plan["rounds"][0]["covered_facets"] == 3
    assert plan["rounds"][0]["issued_facets"] == 3


def test_an_uncovered_facet_opens_a_gap_driven_second_round(monkeypatch):
    """Rounds exist to finish covering the plan the budget could not reach."""
    def results_for(query, facet_index):
        if facet_index == 2:
            return []  # this facet found nothing, so it stays uncovered
        return [f"https://f{facet_index}-r{i}.example.com/p" for i in range(3)]

    searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=['{"facets": ["facet alpha here", "facet beta here"]}',
                     '{"facets": ["the gap query here"]}'],
        vector_queue=[[E1, E2, E3], [E4]],
        plan_max_rounds=2,
    )
    assert searched == ["a research topic", "facet alpha here",
                        "facet beta here", "the gap query here"]
    plan = progress["query_plan"]
    assert [r["index"] for r in plan["rounds"]] == [1, 2]
    assert plan["rounds"][0]["covered_facets"] == 2
    assert plan["rounds"][0]["issued_facets"] == 3
    # Round 2 covers the gap facet but facet beta is still empty, so the
    # research ends on the rail rather than on completion.
    assert plan["acquisition_stop_reason"] == "max_rounds"


def test_a_round_that_covers_nothing_new_stops_the_research(monkeypatch):
    def results_for(query, facet_index):
        if facet_index in (2, 3):
            return []  # facet beta and the gap query both find nothing
        return [f"https://f{facet_index}-r{i}.example.com/p" for i in range(3)]

    searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=['{"facets": ["facet alpha here", "facet beta here"]}',
                     '{"facets": ["the gap query here"]}'],
        vector_queue=[[E1, E2, E3], [E4]],
        plan_max_rounds=5,
    )
    assert len(searched) == 4
    plan = progress["query_plan"]
    assert plan["rounds"][1]["covered_facets"] == 2
    # Two rounds below the rail: nothing new was answered.
    assert plan["acquisition_stop_reason"] == "coverage_plateau"


def test_no_document_is_fetched_twice_across_rounds(monkeypatch):
    """Round 2 re-surfacing a round-1 URL must not re-select it for crawl."""
    def results_for(query, facet_index):
        if facet_index == 2:
            return []
        if facet_index < 3:
            return [f"https://f{facet_index}-r{i}.example.com/p"
                    for i in range(3)]
        # The gap round returns two known URLs and two genuinely new ones.
        return ["https://f0-r0.example.com/p", "https://f1-r0.example.com/p",
                "https://new1.example.com/p", "https://new2.example.com/p"]

    searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=['{"facets": ["facet alpha here", "facet beta here"]}',
                     '{"facets": ["the gap query here"]}'],
        vector_queue=[[E1, E2, E3], [E4]],
        plan_max_rounds=2,
    )
    assert len(searched) == 4
    round_two = progress["query_plan"]["rounds"][1]
    assert round_two["pool"] == 4
    assert round_two["new_candidates"] == 2
    assert round_two["crawled"] == 2


def test_novelty_is_recorded_over_the_fetch_window_not_the_whole_pool(
        monkeypatch):
    """Novelty no longer controls anything, but it must still be recorded
    honestly: over the round's top-`depth` fetch window, never the pool.

    Measured against the pool it pins near 1.0 -- which is exactly why it
    could never work as a stop signal (observed live at 1.0 / 1.0 / 0.983
    across three rounds that had plainly stopped making progress).
    """
    retained = [f"https://shared{i}.example.com/p" for i in range(10)]

    def results_for(query, facet_index):
        if facet_index == 2:
            return []
        if facet_index < 3:
            return retained
        # Round 2 re-surfaces all ten at the top, then a long unseen tail it
        # will never reach.
        return retained + [f"https://tail{i}.example.com/p" for i in range(40)]

    searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=['{"facets": ["facet alpha here", "facet beta here"]}',
                     '{"facets": ["the gap query here"]}'],
        vector_queue=[[E1, E2, E3], [E4]],
        plan_max_rounds=5,
    )
    round_two = progress["query_plan"]["rounds"][1]
    assert round_two["pool"] == 50           # the pool is almost all unseen...
    assert round_two["candidates"] == 10     # ...but the fetch window is not
    assert round_two["new_candidates"] == 0
    assert round_two["novelty"] == 0.0


def test_planning_disabled_records_a_single_round_and_no_gap_pass(monkeypatch):
    searched, progress = _acquisition_probe(monkeypatch, planning=False)
    assert searched == ["a research topic"]
    plan = progress["query_plan"]
    assert plan["acquisition_stop_reason"] == "single_round"
    assert len(plan["rounds"]) == 1
    assert plan["rounds"][0]["queries"] == ["a research topic"]


# --- provenance across rounds ----------------------------------------------

def test_acquisition_provenance_records_rounds_and_the_stop_reason():
    plan = QueryPlan(queries=["topic", "first distinct facet"],
                     decisions=[FacetDecision("topic", True, "topic_seed")],
                     stop_reason="candidates_exhausted")
    provenance = acquisition_provenance(
        plan, issued_queries=["topic", "first distinct facet", "gap q"],
        decisions=list(plan.decisions),
        rounds=[RoundRecord(1, ["topic", "first distinct facet"], 10, 10, 1.0,
                            8, 40, 2, 3),
                RoundRecord(2, ["gap q"], 6, 1, 1 / 6, 1, 55, 3, 3)],
        stop_reason="saturation",
    )
    assert provenance["queries"] == ["topic", "first distinct facet", "gap q"]
    assert provenance["facet_count"] == 2
    assert provenance["acquisition_stop_reason"] == "saturation"
    assert provenance["rounds"][1] == {
        "index": 2, "queries": ["gap q"], "candidates": 6,
        "new_candidates": 1, "novelty": 0.1667, "crawled": 1, "pool": 55,
        "covered_facets": 3, "issued_facets": 3,
    }


def test_acquisition_provenance_is_json_serializable():
    import json
    plan = QueryPlan(queries=["topic"], stop_reason="collapse")
    payload = acquisition_provenance(
        plan, issued_queries=["topic"], decisions=[],
        rounds=[RoundRecord(1, ["topic"], 3, 3, 1.0, 2, 3, 1, 1)],
        stop_reason="single_round",
    )
    assert json.loads(json.dumps(payload))["rounds"][0]["crawled"] == 2


def test_plan_vectors_never_reach_provenance():
    """Embeddings are carried between rounds but must not bloat job progress."""
    plan = QueryPlan(queries=["topic"], stop_reason="collapse",
                     vectors=[E1])
    assert "vectors" not in plan.provenance()
    assert "vectors" not in acquisition_provenance(
        plan, issued_queries=["topic"], decisions=[], rounds=[],
        stop_reason="single_round",
    )


# --- HUB-038: source relevance screening ------------------------------------

def _screener(monkeypatch, vectors=None, embed_error=None, floor=0.30):
    from app.research import ResearchOrchestrator
    orchestrator = object.__new__(ResearchOrchestrator)
    orchestrator.cfg = _config(plan_source_relevance=floor)
    orchestrator.ollama = StubOllama(vectors=vectors, embed_error=embed_error)
    return orchestrator


def _doc(url, title="t", markdown="body"):
    return {"url": url, "title": title, "markdown": markdown,
            "policy_metadata": {"canonical_url": url}}


def test_probe_windows_sample_the_whole_document_not_its_opening():
    """Regression for the inverted ranking measured 2026-08-13: probing only
    the opening scored redis.io below generic tutorials, because reference
    docs begin with navigation while blog posts begin by restating the topic.
    """
    from app.research import _topic_probe_windows
    body = "".join(f"section {i} " * 40 for i in range(10))
    windows = _topic_probe_windows({"title": "T", "markdown": body})
    assert len(windows) > 1
    assert windows[0].startswith("T\n")
    # The last window is drawn from deep in the document, not near the start.
    assert "section 9" in windows[-1] or "section 8" in windows[-1]


def test_probe_windows_handle_short_and_empty_documents():
    from app.research import _topic_probe_windows
    assert _topic_probe_windows({"title": "T", "markdown": ""}) == ["T"]
    short = _topic_probe_windows({"title": "T", "markdown": "tiny body"})
    assert short and all(isinstance(w, str) for w in short)


def test_documents_are_scored_against_the_raw_topic():
    """Anchoring reverted after the 494-document labelled evaluation.

    Facet anchoring scored AUC 0.741 against the topic's 0.857 on identical
    rows, and worse than random (0.426) on one topic. It won only on the single
    ambiguous topic it had been tuned against.
    """
    from app.research import ResearchOrchestrator
    subject = object.__new__(ResearchOrchestrator)
    subject.cfg = _config(plan_source_relevance=0.5)
    # One anchor is embedded (the topic), then each document's windows.
    subject.ollama = StubOllama(vectors=[E1, E1, E2])
    kept, _screening = run(subject._screen_sources(
        "a topic", [_doc("https://on.example"), _doc("https://off.example")],
        anchor_queries=["a topic", "a facet that must not be used"],
    ))
    assert [d["url"] for d in kept] == ["https://on.example"]
    # Exactly one anchor was embedded, not the facets.
    assert subject.ollama.embed_calls[0][0] == "a topic"


def test_off_topic_documents_are_dropped_before_ingestion(monkeypatch):
    """The measured case: an on-topic facet still returned Couchbase docs."""
    subject = _screener(monkeypatch, vectors=[E1, E1, E2])
    kept, screening = run(subject._screen_sources(
        "a topic", [_doc("https://on-topic.example"),
                    _doc("https://off.example")],
    ))
    assert [d["url"] for d in kept] == ["https://on-topic.example"]
    assert screening["applied"] is True
    assert (screening["kept"], screening["dropped"]) == (1, 1)


def test_every_document_score_is_recorded_for_calibration(monkeypatch):
    subject = _screener(monkeypatch, vectors=[E1, E1, E2])
    _kept, screening = run(subject._screen_sources(
        "a topic", [_doc("https://a.example"), _doc("https://b.example")],
    ))
    assert screening["scores"] == [
        {"url": "https://a.example", "topic_cosine": 1.0, "kept": True},
        {"url": "https://b.example", "topic_cosine": 0.0, "kept": False},
    ]


def test_screening_failure_keeps_every_document(monkeypatch):
    """Losing the corpus to a flaky embed call is worse than a stray source."""
    subject = _screener(monkeypatch, embed_error=RuntimeError("embed down"))
    docs = [_doc("https://a.example"), _doc("https://b.example")]
    kept, screening = run(subject._screen_sources("a topic", docs))
    assert kept == docs
    assert screening["applied"] is False
    assert screening["reason"] == "embedding_unavailable"


def test_a_screen_that_would_empty_the_job_is_not_applied(monkeypatch):
    """That means the threshold is wrong here, not that nothing was found."""
    subject = _screener(monkeypatch, vectors=[E1, E2, E2])
    docs = [_doc("https://a.example"), _doc("https://b.example")]
    kept, screening = run(subject._screen_sources("a topic", docs))
    assert kept == docs
    assert screening["applied"] is False
    assert screening["reason"] == "would_empty_job"
    # The scores are still recorded, so the bad threshold is visible.
    assert all(s["kept"] is False for s in screening["scores"])


def test_a_wrong_embedding_count_keeps_every_document(monkeypatch):
    subject = _screener(monkeypatch, vectors=[E1, E1])
    docs = [_doc("https://a.example"), _doc("https://b.example")]
    kept, screening = run(subject._screen_sources("a topic", docs))
    assert kept == docs
    assert screening["applied"] is False


def test_a_zero_floor_screens_nothing(monkeypatch):
    subject = _screener(monkeypatch, vectors=[E1, E1, E2], floor=0.0)
    docs = [_doc("https://a.example"), _doc("https://b.example")]
    kept, _screening = run(subject._screen_sources("a topic", docs))
    assert kept == docs


# --- config surface ---------------------------------------------------------

def _config(**overrides):
    base = dict(
        redis_url="redis://localhost:6379/0", qdrant_url="http://q",
        ollama_url="http://o", llm_model="m", embedding_model="e",
        searxng_url="http://s", crawl4ai_url="http://c", crawl4ai_token="t",
        log_level="info", judge_api_key="placeholder",
    )
    base.update(overrides)
    return Config(**base)


def test_query_planning_defaults_are_off_and_inert():
    cfg = _config()
    assert cfg.report_query_planning is False
    assert cfg.plan_facet_distinct == 0.85
    assert cfg.plan_facet_relevance == 0.55
    assert cfg.plan_source_relevance == 0.54
    assert cfg.plan_max_facets == 12
    assert cfg.plan_max_rounds == 4
    assert cfg.plan_search_budget == 24
    assert cfg.plan_crawl_budget == 150


@pytest.mark.parametrize("overrides", [
    {"plan_facet_distinct": 0.0},
    {"plan_facet_distinct": 1.5},
    {"plan_max_facets": 0},
    {"plan_max_facets": 100},
    {"plan_search_budget": 0},
    {"plan_crawl_budget": 0},
    {"plan_max_rounds": 0},
    {"plan_source_relevance": -0.1},
    {"plan_source_relevance": 1.0},
    {"plan_max_rounds": 50},
])
def test_planning_config_rejects_out_of_range_rails(overrides):
    with pytest.raises(ValueError):
        _config(**overrides)


# --- ADR-002 stage 1: pre-crawl ranking and search pacing -------------------

def _ranker(vectors=None, embed_error=None):
    from app.research import ResearchOrchestrator
    subject = object.__new__(ResearchOrchestrator)
    subject.cfg = _config()
    subject.ollama = StubOllama(vectors=vectors, embed_error=embed_error)
    return subject


def _result(url, title="t", snippet="s"):
    return {"url": url, "title": title, "snippet": snippet}


def test_snippet_ranking_orders_each_facet_by_relevance():
    """The crawl cap decides what gets fetched, so order decides what the
    budget buys."""
    subject = _ranker(vectors=[E1, E2, E1])  # topic, then two results
    ranked, provenance = run(subject._rank_by_snippet(
        "a topic", [[_result("https://off.example"),
                     _result("https://on.example")]],
    ))
    assert [r["url"] for r in ranked[0]] == ["https://on.example",
                                             "https://off.example"]
    assert provenance["applied"] is True
    assert provenance["candidates"] == 2


def test_snippet_ranking_never_ranks_across_facets():
    """Cross-facet ranking would let one facet consume the whole crawl cap,
    undoing the interleave that shares it."""
    # topic, then flat result order: a-weak, a-strong, b-strong, b-weak.
    subject = _ranker(vectors=[E1, E2, E1, E1, E2])
    ranked, _p = run(subject._rank_by_snippet(
        "a topic",
        [[_result("https://a-weak.example"), _result("https://a-strong.example")],
         [_result("https://b-strong.example"), _result("https://b-weak.example")]],
    ))
    assert len(ranked) == 2
    assert [r["url"] for r in ranked[0]] == ["https://a-strong.example",
                                             "https://a-weak.example"]
    assert [r["url"] for r in ranked[1]] == ["https://b-strong.example",
                                             "https://b-weak.example"]


def test_snippet_ranking_discards_nothing():
    """Ranking, not thresholding: no candidate is dropped on a guessed cutoff."""
    subject = _ranker(vectors=[E1, E2, E3, E4])
    facet = [_result(f"https://{i}.example") for i in range(3)]
    ranked, _p = run(subject._rank_by_snippet("a topic", [facet]))
    assert sorted(r["url"] for r in ranked[0]) == sorted(r["url"] for r in facet)


def test_snippet_ranking_failure_leaves_the_order_untouched():
    subject = _ranker(embed_error=RuntimeError("embed down"))
    facet = [_result("https://a.example"), _result("https://b.example")]
    ranked, provenance = run(subject._rank_by_snippet("a topic", [facet]))
    assert ranked == [facet]
    assert provenance["applied"] is False
    assert provenance["reason"] == "embedding_unavailable"


def test_search_pacing_default_is_set_and_bounded():
    assert _config().search_pacing_seconds == 2.0
    with pytest.raises(ValueError):
        _config(search_pacing_seconds=-1.0)
    with pytest.raises(ValueError):
        _config(search_pacing_seconds=99.0)


# --- ADR-002 stage 2: the keyed search fallback -----------------------------

def _serper(handler, key="test-serper-key"):
    import httpx
    from app.clients import SerperClient
    subject = SerperClient(key)
    subject._client = httpx.AsyncClient(
        base_url="https://google.serper.dev",
        transport=httpx.MockTransport(handler),
    )
    return subject


def test_serper_maps_organic_results_to_the_shared_shape():
    import httpx

    def handler(request):
        assert request.headers["X-API-KEY"] == "test-serper-key"
        body = json.loads(request.content)
        assert body["q"] == "a query"
        return httpx.Response(200, json={"organic": [
            {"title": "T", "link": "https://a.example", "snippet": "S",
             "date": "2026-01-02", "position": 1},
            {"title": "U", "link": "https://b.example", "snippet": "V"},
        ]})

    results = run(_serper(handler).search("a query", max_results=10))
    assert results == [
        {"url": "https://a.example", "title": "T", "snippet": "S",
         "published_at": "2026-01-02"},
        {"url": "https://b.example", "title": "U", "snippet": "V",
         "published_at": None},
    ]


def test_serper_is_inert_without_a_key():
    """Unset means acquisition behaves exactly as it did before."""
    from app.clients import SerperClient
    subject = SerperClient("")
    assert subject.configured is False
    assert run(subject.search("a query")) == []


def test_serper_quota_error_returns_no_results_rather_than_raising():
    import httpx
    subject = _serper(lambda _r: httpx.Response(429, json={"message": "quota"}))
    assert run(subject.search("a query")) == []


def test_serper_transport_failure_returns_no_results():
    import httpx

    def handler(request):
        raise httpx.ConnectError("down", request=request)

    assert run(_serper(handler).search("a query")) == []


def test_serper_drops_entries_without_a_link():
    import httpx
    subject = _serper(lambda _r: httpx.Response(200, json={"organic": [
        {"title": "no link"}, {"title": "ok", "link": "https://c.example"},
    ]}))
    assert [r["url"] for r in run(subject.search("q"))] == ["https://c.example"]


def test_serper_key_is_excluded_from_config_repr():
    """The key must never reach a log through a Config repr."""
    assert "shhh" not in repr(_config(serper_api_key="shhh"))


def test_human_readable_search_dates_parse():
    """Regression: Serper returns "Oct 31, 2019". Parsed as undated, every one
    of its results is rejected as stale_or_undated whenever a job sets
    freshness_days -- a defect no mocked response would have exposed."""
    from app.research import parse_source_date
    assert parse_source_date("Oct 31, 2019").year == 2019
    assert parse_source_date("Dec 14, 2023").month == 12
    assert parse_source_date("27 January 2025").day == 27
    # The shapes that already worked must keep working.
    assert parse_source_date("2026-01-02").year == 2026
    assert parse_source_date("Thu, 31 Oct 2019 00:00:00 +0000").year == 2019
    assert parse_source_date("not a date at all") is None
    assert parse_source_date(None) is None
