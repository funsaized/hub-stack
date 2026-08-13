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
    admit_facets,
    cosine,
    interleave,
    normalize_candidate,
    parse_candidates,
    plan_queries,
    single_query_plan,
)


class StubOllama:
    """Records calls so tests can assert the planner was (or was not) invoked."""

    def __init__(self, reply=None, vectors=None, generate_error=None,
                 embed_error=None):
        self._reply = reply
        self._vectors = vectors
        self._generate_error = generate_error
        self._embed_error = embed_error
        self.generate_calls = []
        self.embed_calls = []

    async def generate(self, prompt, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        if self._generate_error:
            raise self._generate_error
        return self._reply

    async def embed_batch(self, texts):
        self.embed_calls.append(list(texts))
        if self._embed_error:
            raise self._embed_error
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
                       results_per_query=3):
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
            return [
                {"url": f"https://f{facet_index}-r{i}.example.com/p"}
                for i in range(results_per_query)
            ]

    orchestrator = object.__new__(ResearchOrchestrator)
    orchestrator.cfg = _config(report_query_planning=planning)
    orchestrator.ollama = StubOllama(reply=plan_reply, vectors=vectors)
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
    assert cfg.plan_search_budget == 12
    assert cfg.plan_crawl_budget == 40


@pytest.mark.parametrize("overrides", [
    {"plan_facet_distinct": 0.0},
    {"plan_facet_distinct": 1.5},
    {"plan_max_facets": 0},
    {"plan_max_facets": 100},
    {"plan_search_budget": 0},
    {"plan_crawl_budget": 0},
])
def test_planning_config_rejects_out_of_range_rails(overrides):
    with pytest.raises(ValueError):
        _config(**overrides)
