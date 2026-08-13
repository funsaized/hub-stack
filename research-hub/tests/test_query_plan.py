"""Adaptive query planning tests (HUB-024, stage 1).

Fully offline and deterministic: the planner LLM and the embedding model are
replaced by stubs, so no case performs a live generation, embedding or search.

The load-bearing property under test is that breadth is EMERGENT from the
distinctness threshold and never a fixed sub-query count -- including the
collapse case, where the plan must reduce to exactly the pre-planning single
search.
"""

import asyncio

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
    novelty_ratio,
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
            return self._vector_queue.pop(0) if self._vector_queue else []
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
        "narrow topic", ["paraphrase one", "paraphrase two"],
        [E1, E1, E1], distinct=0.85, max_facets=8,
    )
    assert plan.queries == ["narrow topic"]
    assert plan.collapsed is True
    assert plan.stop_reason == "collapse"
    assert [d.reason for d in plan.decisions] == [
        "topic_seed", "redundant", "redundant",
    ]


def test_distinct_candidates_widen_the_plan():
    plan = admit_facets(
        "broad topic", ["facet a", "facet b", "facet c"],
        [E1, E2, E3, E4], distinct=0.85, max_facets=8,
    )
    assert plan.queries == ["broad topic", "facet a", "facet b", "facet c"]
    assert plan.collapsed is False
    assert plan.stop_reason == "candidates_exhausted"


def test_breadth_is_not_a_fixed_count_at_one_threshold():
    """Same threshold, different evidence -> different breadth. No fixed N."""
    narrow = admit_facets("t", ["a", "b", "c"], [E1, E1, E1, E1],
                          distinct=0.85, max_facets=8)
    middling = admit_facets("t", ["a", "b", "c"], [E1, E1, E2, E1],
                            distinct=0.85, max_facets=8)
    wide = admit_facets("t", ["a", "b", "c"], [E1, E2, E3, E4],
                        distinct=0.85, max_facets=8)
    assert [len(p.queries) for p in (narrow, middling, wide)] == [1, 2, 4]


def test_admission_compares_against_the_whole_admitted_set():
    """A candidate redundant with facet 1 is refused even if distinct from the
    topic -- otherwise near-duplicate facets would both be admitted."""
    plan = admit_facets(
        "topic", ["facet a", "near duplicate of a"], [E1, E2, E2],
        distinct=0.85, max_facets=8,
    )
    assert plan.queries == ["topic", "facet a"]
    assert plan.decisions[-1].reason == "redundant"
    assert plan.decisions[-1].max_cosine == pytest.approx(1.0)


def test_threshold_governs_admission_at_the_boundary():
    # cos = 0.8 for these two vectors: admitted at 0.85, refused at 0.75.
    near = [0.8, 0.6, 0.0, 0.0]
    admitted = admit_facets("t", ["c"], [E1, near], distinct=0.85, max_facets=8)
    refused = admit_facets("t", ["c"], [E1, near], distinct=0.75, max_facets=8)
    assert len(admitted.queries) == 2
    assert len(refused.queries) == 1


def test_max_facets_is_a_rail_and_is_recorded_not_silent():
    plan = admit_facets(
        "topic", ["facet a", "facet b", "facet c"], [E1, E2, E3, E4],
        distinct=0.85, max_facets=2,
    )
    assert plan.queries == ["topic", "facet a"]
    assert plan.stop_reason == "max_facets"
    # The truncated candidates are still visible in provenance.
    assert [d.reason for d in plan.decisions if not d.admitted] == [
        "max_facets", "max_facets",
    ]


def test_topic_is_always_the_first_query():
    plan = admit_facets("the topic", ["facet a"], [E1, E2],
                        distinct=0.85, max_facets=8)
    assert plan.queries[0] == "the topic"
    assert plan.decisions[0] == FacetDecision(
        query="the topic", admitted=True, reason="topic_seed",
    )


def test_admit_facets_rejects_inconsistent_inputs():
    with pytest.raises(ValueError):
        admit_facets("t", ["a", "b"], [E1, E2], distinct=0.85, max_facets=8)
    with pytest.raises(ValueError):
        admit_facets("t", [], [E1], distinct=0.85, max_facets=0)


# --- plan_queries: degradation is always to today's behavior ---------------

def test_plan_queries_admits_distinct_facets():
    ollama = StubOllama(
        reply='{"facets": ["facet alpha here", "facet beta here"]}',
        vectors=[E1, E2, E3],
    )
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic", "facet alpha here", "facet beta here"]
    assert ollama.embed_calls == [["a topic", "facet alpha here",
                                   "facet beta here"]]


def test_planner_generation_failure_degrades_to_one_search():
    ollama = StubOllama(generate_error=RuntimeError("ollama down"))
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "planner_unavailable"


def test_planner_malformed_reply_degrades_to_one_search():
    ollama = StubOllama(reply="I cannot help with that.")
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "planner_unavailable"


def test_embedding_failure_degrades_to_one_search():
    ollama = StubOllama(reply='{"facets": ["facet alpha here"]}',
                        embed_error=RuntimeError("embed down"))
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "planner_unavailable"


def test_wrong_embedding_count_degrades_to_one_search():
    ollama = StubOllama(reply='{"facets": ["facet alpha here"]}',
                        vectors=[E1])  # missing the candidate's vector
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "planner_unavailable"


def test_planner_returning_no_facets_is_a_collapse_not_a_failure():
    ollama = StubOllama(reply='{"facets": []}')
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, max_facets=8,
                            search_budget=12))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "collapse"
    assert ollama.embed_calls == []


def test_search_budget_of_one_spends_no_planner_call():
    ollama = StubOllama(reply='{"facets": ["facet alpha here"]}', vectors=[E1, E2])
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, max_facets=8,
                            search_budget=1))
    assert plan.queries == ["a topic"]
    assert plan.stop_reason == "budget"
    assert ollama.generate_calls == []


def test_search_budget_bounds_the_plan_below_max_facets():
    ollama = StubOllama(
        reply='{"facets": ["facet alpha here", "facet beta here"]}',
        vectors=[E1, E2, E3],
    )
    plan = run(plan_queries(ollama, "a topic", distinct=0.85, max_facets=8,
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
    plan = admit_facets("topic", ["facet a", "dupe"], [E1, E2, E2],
                        distinct=0.85, max_facets=8)
    provenance = plan.provenance()
    assert provenance["facet_count"] == 2
    assert provenance["collapsed"] is False
    assert provenance["decisions"] == [
        {"query": "topic", "admitted": True, "reason": "topic_seed",
         "max_cosine": None},
        {"query": "facet a", "admitted": True, "reason": "distinct",
         "max_cosine": 0.0},
        {"query": "dupe", "admitted": False, "reason": "redundant",
         "max_cosine": 1.0},
    ]


def test_query_plan_is_json_serializable_for_job_progress():
    import json
    plan = QueryPlan(queries=["a", "b"],
                     decisions=[FacetDecision("a", True, "topic_seed")],
                     stop_reason="candidates_exhausted")
    assert json.loads(json.dumps(plan.provenance()))["facet_count"] == 2


# --- acquisition path: how many searches a job actually issues -------------

def _acquisition_probe(monkeypatch, *, planning, plan_reply=None, vectors=None,
                       results_per_query=3, reply_queue=None,
                       vector_queue=None, results_for=None, **config_overrides):
    """Drive ResearchOrchestrator.run_job through search, then stop.

    Every crawl destination is refused by a stubbed SSRF policy, so the job
    raises after acquisition without any DNS lookup, fetch or ingestion. The
    orchestrator is built without __init__ because only the acquisition
    collaborators are under test.
    """
    from app import research as research_module
    from app.research import ResearchOrchestrator
    from app.url_policy import DestinationNotAllowed

    monkeypatch.setattr(
        research_module, "vet_destination_async",
        lambda url: _raise_not_allowed(DestinationNotAllowed, url),
    )

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

    orchestrator = object.__new__(ResearchOrchestrator)
    orchestrator.cfg = _config(report_query_planning=planning,
                               **config_overrides)
    orchestrator.ollama = StubOllama(reply=plan_reply, vectors=vectors,
                                     reply_queue=reply_queue,
                                     vector_queue=vector_queue)
    orchestrator.searxng = Searx()

    async def get_job(job_id):
        return {"topic": "a research topic", "depth": 10, "max_sources": 12,
                "language": "en", "tags": [], "per_domain_limit": 2}

    async def update_job(job_id, **fields):
        progress.update(fields.get("progress") or {})

    orchestrator.get_job = get_job
    orchestrator._update_job = update_job

    with pytest.raises(RuntimeError, match="No pages crawled successfully"):
        run(orchestrator.run_job("job-1"))
    return searched, progress


def _raise_not_allowed(exc_type, url):
    async def raiser():
        raise exc_type(url, "test_stub")
    return raiser()


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


# --- stage 2: novelty saturation and the stop decision ---------------------

def test_novelty_ratio_is_the_new_fraction_and_safe_when_empty():
    assert novelty_ratio(2, 8) == pytest.approx(0.25)
    assert novelty_ratio(0, 0) == 0.0


def test_rounds_continue_while_each_one_still_finds_new_sources():
    keep_going, reason = should_continue(
        round_index=1, new_candidates=8, candidates=10,
        max_rounds=3, novelty_min=0.2,
    )
    assert (keep_going, reason) == (True, "continue")


def test_rounds_stop_when_novelty_falls_below_the_threshold():
    keep_going, reason = should_continue(
        round_index=1, new_candidates=1, candidates=20,
        max_rounds=3, novelty_min=0.2,
    )
    assert (keep_going, reason) == (False, "saturation")


def test_rounds_stop_on_too_few_new_documents_even_at_high_novelty():
    """1 of 1 is ratio 1.0 but yields almost nothing: still saturated."""
    keep_going, reason = should_continue(
        round_index=1, new_candidates=1, candidates=1,
        max_rounds=3, novelty_min=0.2,
    )
    assert (keep_going, reason) == (False, "saturation")


def test_saturation_is_attributed_before_the_round_budget():
    """When both would fire, the stop is the evidence running dry."""
    keep_going, reason = should_continue(
        round_index=3, new_candidates=0, candidates=10,
        max_rounds=3, novelty_min=0.2,
    )
    assert (keep_going, reason) == (False, "saturation")


def test_round_budget_stops_a_still_productive_search():
    keep_going, reason = should_continue(
        round_index=3, new_candidates=10, candidates=10,
        max_rounds=3, novelty_min=0.2,
    )
    assert (keep_going, reason) == (False, "max_rounds")


# --- stage 2: gap-driven rounds --------------------------------------------

def test_gap_queries_are_held_to_the_bar_against_every_issued_query():
    """A gap query redundant with round 1's facets is refused, not issued."""
    ollama = StubOllama(reply='{"facets": ["redundant gap", "genuine gap here"]}',
                        vectors=[E2, E4])
    fresh, vectors, decisions, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 3, 2), ("facet a", 1, 1)],
        ["topic", "facet a"], [E1, E2],
        distinct=0.85, max_total=12,
    ))
    assert fresh == ["genuine gap here"]
    assert reason == "continue"
    assert [d.reason for d in decisions] == ["redundant", "distinct"]
    assert len(vectors) == 3


def test_gap_pass_returning_nothing_is_the_coverage_stop():
    ollama = StubOllama(reply='{"facets": []}')
    fresh, _, _, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 5, 4)], ["topic"], [E1],
        distinct=0.85, max_total=12,
    ))
    assert fresh == []
    assert reason == "coverage"


def test_gap_pass_whose_every_proposal_is_covered_is_also_coverage():
    ollama = StubOllama(reply='{"facets": ["already covered here"]}',
                        vectors=[E1])
    fresh, _, _, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 5, 4)], ["topic"], [E1],
        distinct=0.85, max_total=12,
    ))
    assert fresh == []
    assert reason == "coverage"


def test_gap_pass_failure_ends_rounds_without_failing_the_job():
    ollama = StubOllama(generate_error=RuntimeError("ollama down"))
    fresh, _, _, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 5, 4)], ["topic"], [E1],
        distinct=0.85, max_total=12,
    ))
    assert fresh == []
    assert reason == "planner_unavailable"


def test_gap_pass_respects_the_search_budget_without_calling_the_planner():
    ollama = StubOllama(reply='{"facets": ["a gap query here"]}', vectors=[E4])
    fresh, _, _, reason = run(plan_gap_round(
        ollama, "topic", [("topic", 1, 1)], ["topic", "b", "c"], [E1, E2, E3],
        distinct=0.85, max_total=3,
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
        greedy_admit(["a"], [E1], ["b"], [], distinct=0.85, max_total=4)
    with pytest.raises(ValueError):
        greedy_admit(["a"], [], ["b"], [E2], distinct=0.85, max_total=4)
    with pytest.raises(ValueError):
        greedy_admit(["a"], [E1], ["b"], [E2], distinct=0.85, max_total=0)


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


def test_a_widened_plan_runs_a_gap_driven_second_round(monkeypatch):
    searched, progress = _acquisition_probe(
        monkeypatch, planning=True,
        reply_queue=[
            '{"facets": ["facet alpha here", "facet beta here"]}',
            '{"facets": ["the gap query here"]}',
        ],
        vector_queue=[[E1, E2, E3], [E4]],
        plan_max_rounds=2,
    )
    assert searched == ["a research topic", "facet alpha here",
                        "facet beta here", "the gap query here"]
    plan = progress["query_plan"]
    assert [r["index"] for r in plan["rounds"]] == [1, 2]
    assert plan["rounds"][1]["queries"] == ["the gap query here"]
    assert plan["acquisition_stop_reason"] == "max_rounds"
    assert plan["queries"][-1] == "the gap query here"


def test_a_round_that_resurfaces_known_sources_stops_on_saturation(monkeypatch):
    """Round 2 returns exactly round 1's URLs, so nothing new is acquired."""
    def results_for(query, facet_index):
        return [f"https://shared{i}.example.com/p" for i in range(3)]

    searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=[
            '{"facets": ["facet alpha here", "facet beta here"]}',
            '{"facets": ["the gap query here"]}',
        ],
        vector_queue=[[E1, E2, E3], [E4]],
        plan_max_rounds=3,
    )
    # Round 1's facets all return the same three URLs, which dedup to three
    # genuinely new sources -- so round 1 is productive and a gap round runs.
    # Round 2 resurfaces only those three, yields nothing new, and stops the
    # research two rounds below the max_rounds rail.
    assert searched == ["a research topic", "facet alpha here",
                        "facet beta here", "the gap query here"]
    plan = progress["query_plan"]
    assert plan["acquisition_stop_reason"] == "saturation"
    assert plan["rounds"][0]["new_candidates"] == 3
    assert plan["rounds"][1]["candidates"] == 3
    assert plan["rounds"][1]["new_candidates"] == 0
    assert plan["rounds"][1]["crawled"] == 0


def test_no_document_is_fetched_twice_across_rounds(monkeypatch):
    """Round 2 re-surfacing a round-1 URL must not re-select it for crawl."""
    def results_for(query, facet_index):
        if facet_index < 3:
            return [f"https://f{facet_index}-r{i}.example.com/p"
                    for i in range(3)]
        # The gap round returns two known URLs and two genuinely new ones.
        return ["https://f0-r0.example.com/p", "https://f1-r0.example.com/p",
                "https://new1.example.com/p", "https://new2.example.com/p"]

    searched, progress = _acquisition_probe(
        monkeypatch, planning=True, results_for=results_for,
        reply_queue=[
            '{"facets": ["facet alpha here", "facet beta here"]}',
            '{"facets": ["the gap query here"]}',
        ],
        vector_queue=[[E1, E2, E3], [E4]],
        plan_max_rounds=2,
    )
    assert len(searched) == 4
    round_two = progress["query_plan"]["rounds"][1]
    assert round_two["candidates"] == 4
    assert round_two["new_candidates"] == 2
    assert round_two["novelty"] == pytest.approx(0.5)


def test_planning_disabled_records_a_single_round_and_no_gap_pass(monkeypatch):
    searched, progress = _acquisition_probe(monkeypatch, planning=False)
    assert searched == ["a research topic"]
    plan = progress["query_plan"]
    assert plan["acquisition_stop_reason"] == "single_round"
    assert len(plan["rounds"]) == 1
    assert plan["rounds"][0]["queries"] == ["a research topic"]


# --- provenance across rounds ----------------------------------------------

def test_acquisition_provenance_records_rounds_and_the_stop_reason():
    plan = QueryPlan(queries=["topic", "facet a"],
                     decisions=[FacetDecision("topic", True, "topic_seed")],
                     stop_reason="candidates_exhausted")
    provenance = acquisition_provenance(
        plan, issued_queries=["topic", "facet a", "gap q"],
        decisions=list(plan.decisions),
        rounds=[RoundRecord(1, ["topic", "facet a"], 10, 10, 1.0, 8),
                RoundRecord(2, ["gap q"], 6, 1, 1 / 6, 1)],
        stop_reason="saturation",
    )
    assert provenance["queries"] == ["topic", "facet a", "gap q"]
    assert provenance["facet_count"] == 2
    assert provenance["acquisition_stop_reason"] == "saturation"
    assert provenance["rounds"][1] == {
        "index": 2, "queries": ["gap q"], "candidates": 6,
        "new_candidates": 1, "novelty": 0.1667, "crawled": 1,
    }


def test_acquisition_provenance_is_json_serializable():
    import json
    plan = QueryPlan(queries=["topic"], stop_reason="collapse")
    payload = acquisition_provenance(
        plan, issued_queries=["topic"], decisions=[],
        rounds=[RoundRecord(1, ["topic"], 3, 3, 1.0, 2)],
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
    assert cfg.plan_max_facets == 8
    assert cfg.plan_max_rounds == 3
    assert cfg.plan_novelty_min == 0.2
    assert cfg.plan_search_budget == 12
    assert cfg.plan_crawl_budget == 40


@pytest.mark.parametrize("overrides", [
    {"plan_facet_distinct": 0.0},
    {"plan_facet_distinct": 1.5},
    {"plan_max_facets": 0},
    {"plan_max_facets": 100},
    {"plan_search_budget": 0},
    {"plan_crawl_budget": 0},
    {"plan_max_rounds": 0},
    {"plan_max_rounds": 50},
    {"plan_novelty_min": -0.1},
    {"plan_novelty_min": 1.5},
])
def test_planning_config_rejects_out_of_range_rails(overrides):
    with pytest.raises(ValueError):
        _config(**overrides)
