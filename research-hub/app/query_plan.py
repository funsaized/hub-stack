"""Adaptive query planning for research acquisition (HUB-024, stage 1).

A research job historically issued exactly one SearXNG query, so the whole
downstream pipeline could only ever work with the documents that one phrasing
surfaced. This module widens *acquisition* only; retrieval, span selection and
the claim gate are untouched.

The governing constraint is that **breadth is emergent, never a fixed
sub-query count**. One bounded local-LLM call proposes candidate facet
queries; a candidate is admitted only while its maximum cosine similarity to
the already-admitted set stays below ``PLAN_FACET_DISTINCT``. Admission stops
when the next candidate adds no distinct retrieval intent, so a narrow factual
topic admits only the topic itself and the job issues exactly one search --
the identity function on the pre-planning path. ``PLAN_MAX_FACETS`` is a
safety rail against a pathological planner, not the mechanism that decides
breadth.

The planner is a generative component upstream of acquisition, so every
failure mode here degrades to the single-query plan rather than failing the
job or silently skewing the corpus: a plan that could not be built is
recorded as such in job provenance.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# One bounded call; the planner emits short queries, never prose.
PLANNER_MAX_TOKENS = 512
# Rail on what we will even consider embedding, independent of the config cap.
PLANNER_MAX_CANDIDATES = 24
FACET_MIN_CHARS = 8
FACET_MAX_CHARS = 300

PLANNER_SYSTEM = (
    "You decompose a research topic into distinct information needs. "
    "You reply with JSON only."
)

PLANNER_RULES = """Propose search queries that cover genuinely DIFFERENT
information needs within the topic.

Rules:
- Each query must seek information the others do not. Rephrasings, synonyms
  and word-order variants of the same need are useless here.
- If the topic is narrow enough that one query already covers it, return an
  empty list. That is a correct and expected answer, not a failure.
- Each query is a search-engine query: keywords or a short question, no
  boolean operators, no site: filters, no quotes.
- Cover angles such as mechanism, procedure, failure modes, constraints,
  comparisons and evidence -- but only where the topic actually has them.

Return JSON: {"facets": ["query one", "query two"]}"""

FACET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "facets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["facets"],
}


@dataclass(frozen=True)
class FacetDecision:
    """Why one candidate query was admitted or refused. Recorded per job."""

    query: str
    admitted: bool
    reason: str
    max_cosine: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "admitted": self.admitted,
            "reason": self.reason,
            "max_cosine": (
                None if self.max_cosine is None else round(self.max_cosine, 4)
            ),
        }


@dataclass(frozen=True)
class QueryPlan:
    """The searches a job will issue, plus the audit trail for why."""

    queries: list[str]
    decisions: list[FacetDecision] = field(default_factory=list)
    stop_reason: str = "collapse"
    # Embeddings of the admitted queries, carried so a later round can be held
    # to the same distinctness bar against every query already issued. Never
    # serialized into provenance.
    vectors: list[list[float]] = field(default_factory=list, repr=False)

    @property
    def collapsed(self) -> bool:
        """True when the plan is today's single-query behavior."""
        return len(self.queries) <= 1

    def provenance(self) -> dict[str, Any]:
        return {
            "queries": list(self.queries),
            "facet_count": len(self.queries),
            "collapsed": self.collapsed,
            "stop_reason": self.stop_reason,
            "decisions": [d.as_dict() for d in self.decisions],
        }


def single_query_plan(topic: str, reason: str) -> QueryPlan:
    """The identity plan: one search, exactly as the pre-planning path ran."""
    return QueryPlan(queries=[topic], stop_reason=reason)


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; 0.0 for a degenerate (zero-norm) vector."""
    if len(a) != len(b):
        raise ValueError("cosine similarity needs equal-length vectors")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def normalize_candidate(value: Any) -> str | None:
    """Reduce one proposed facet to a usable query, or drop it."""
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    if not FACET_MIN_CHARS <= len(collapsed) <= FACET_MAX_CHARS:
        return None
    return collapsed


def parse_candidates(raw: str) -> list[str]:
    """Parse the planner's JSON reply into ordered unique candidate queries."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("planner reply was not valid JSON")
    if not isinstance(payload, dict) or not isinstance(payload.get("facets"), list):
        raise ValueError("planner reply lacked a 'facets' array")
    candidates: list[str] = []
    seen: set[str] = set()
    for item in payload["facets"][:PLANNER_MAX_CANDIDATES]:
        normalized = normalize_candidate(item)
        if normalized is None:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        candidates.append(normalized)
    return candidates


def greedy_admit(
    seed_queries: list[str],
    seed_vectors: list[list[float]],
    candidates: list[str],
    candidate_vectors: list[list[float]],
    *,
    distinct: float,
    max_total: int,
) -> tuple[list[str], list[list[float]], list[FacetDecision], str]:
    """Admit candidates that add retrieval intent the seed set does not cover.

    Shared by first-round facet admission and later-round gap admission, so a
    gap query is held to exactly the same distinctness bar as an opening
    facet -- and is compared against every query the job has already issued,
    not just the ones from its own round.

    Returns ``(queries, vectors, decisions, stop_reason)``.
    """
    if len(candidate_vectors) != len(candidates):
        raise ValueError("expected one vector per candidate")
    if len(seed_vectors) != len(seed_queries):
        raise ValueError("expected one vector per seed query")
    if max_total < 1:
        raise ValueError("max_total must be at least 1")

    queries = list(seed_queries)
    vectors = list(seed_vectors)
    decisions: list[FacetDecision] = []
    stop_reason = "candidates_exhausted"

    for index, candidate in enumerate(candidates):
        if len(queries) >= max_total:
            # The rail tripped: record every remaining candidate as
            # unconsidered so a plan truncated by the cap is visible in
            # provenance rather than inferred from a thin corpus.
            stop_reason = "max_facets"
            decisions.extend(
                FacetDecision(query=c, admitted=False, reason="max_facets")
                for c in candidates[index:]
            )
            break
        vector = candidate_vectors[index]
        similarity = (
            max(cosine(vector, v) for v in vectors) if vectors else 0.0
        )
        if similarity < distinct:
            queries.append(candidate)
            vectors.append(vector)
            decisions.append(
                FacetDecision(
                    query=candidate, admitted=True, reason="distinct",
                    max_cosine=similarity,
                )
            )
        else:
            decisions.append(
                FacetDecision(
                    query=candidate, admitted=False, reason="redundant",
                    max_cosine=similarity,
                )
            )
    return queries, vectors, decisions, stop_reason


def admit_facets(
    topic: str,
    candidates: list[str],
    vectors: list[list[float]],
    *,
    distinct: float,
    max_facets: int,
) -> QueryPlan:
    """Greedily admit candidates that add retrieval intent the plan lacks.

    ``vectors`` is the embedding of ``[topic, *candidates]``. The topic is
    always facet 0, so the plan can only ever widen the pre-planning search,
    never replace it.
    """
    if len(vectors) != len(candidates) + 1:
        raise ValueError("expected one vector per candidate plus the topic")

    queries, admitted_vectors, decisions, stop_reason = greedy_admit(
        [topic], [vectors[0]], candidates, vectors[1:],
        distinct=distinct, max_total=max_facets,
    )
    decisions = [
        FacetDecision(query=topic, admitted=True, reason="topic_seed"),
        *decisions,
    ]

    if len(queries) == 1 and stop_reason == "candidates_exhausted":
        # Every candidate collapsed into the topic: this is the complexity
        # signal, and the job now behaves exactly as it did before planning.
        stop_reason = "collapse"
    return QueryPlan(
        queries=queries, decisions=decisions, stop_reason=stop_reason,
        vectors=admitted_vectors,
    )


async def plan_queries(
    ollama,
    topic: str,
    *,
    distinct: float,
    max_facets: int,
    search_budget: int,
    job_id: str | None = None,
) -> QueryPlan:
    """Build the acquisition plan for one topic.

    Never raises: a planner or embedding failure degrades to the single-query
    plan with the failure recorded as the stop reason.
    """
    # The search budget is a hard rail on issued searches, so it bounds the
    # plan itself rather than being checked after the fact.
    effective_max = max(1, min(max_facets, search_budget))
    if effective_max == 1:
        return single_query_plan(topic, "budget")

    try:
        raw = await ollama.generate(
            f"{PLANNER_RULES}\n\nTopic: {topic}",
            system=PLANNER_SYSTEM,
            max_tokens=PLANNER_MAX_TOKENS,
            json_schema=FACET_SCHEMA,
            diagnostic_stage="query_plan",
        )
        candidates = parse_candidates(raw)
    except Exception as exc:
        logger.warning(
            "query_plan_unavailable",
            extra={"job_id": job_id, "failure_reason": type(exc).__name__},
        )
        return single_query_plan(topic, "planner_unavailable")

    if not candidates:
        # The planner was asked to return nothing for an already-narrow topic.
        return single_query_plan(topic, "collapse")

    try:
        vectors = await ollama.embed_batch([topic, *candidates])
    except Exception as exc:
        logger.warning(
            "query_plan_embedding_unavailable",
            extra={"job_id": job_id, "failure_reason": type(exc).__name__},
        )
        return single_query_plan(topic, "planner_unavailable")

    try:
        return admit_facets(
            topic, candidates, vectors,
            distinct=distinct, max_facets=effective_max,
        )
    except ValueError as exc:
        logger.warning(
            "query_plan_admission_failed",
            extra={"job_id": job_id, "failure_reason": str(exc)},
        )
        return single_query_plan(topic, "planner_unavailable")


GAP_SYSTEM = (
    "You identify what a research corpus is still missing. "
    "You reply with JSON only."
)

GAP_RULES = """Below is what a research job has retrieved so far, per query.

Name the sub-questions the corpus does NOT yet answer, as search queries.

Rules:
- Target gaps, not more of what is already covered. A query that would
  return the same sources again is worthless here.
- A query covering zero retrieved documents is the strongest gap signal.
- If the corpus already covers the topic, return an empty list. That is a
  correct answer and ends the research.
- Each query is a search-engine query: keywords or a short question, no
  boolean operators, no site: filters, no quotes.

Return JSON: {"facets": ["query one", "query two"]}"""


@dataclass(frozen=True)
class RoundRecord:
    """What one acquisition round issued and yielded. Recorded per job."""

    index: int
    queries: list[str]
    #: Size of the novelty window -- the round's top-`depth` candidates, i.e.
    #: the documents it would actually fetch. NOT the whole candidate pool.
    candidates: int
    new_candidates: int
    novelty: float
    crawled: int
    #: Full count of policy-accepted candidates, kept for auditability so the
    #: window can be read in context.
    pool: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "queries": list(self.queries),
            "candidates": self.candidates,
            "new_candidates": self.new_candidates,
            "novelty": round(self.novelty, 4),
            "crawled": self.crawled,
            "pool": self.pool,
        }


def novelty_ratio(new_candidates: int, candidates: int) -> float:
    """Fraction of a round's fetch window never seen before.

    The window is the round's top-`depth` policy-accepted candidates -- the
    documents it would actually fetch -- and never the full candidate pool.
    Measured against the pool the ratio is meaningless: a round surfaces far
    more candidates than it can crawl, so almost all of them are unseen and
    novelty pins near 1.0 whether or not the research has saturated (the
    2026-08-13 live measurement recorded 1.0 / 1.0 / 0.983 across three rounds
    that had visibly stopped finding relevant material). Bounding the
    denominator by the crawl allowance restores the signal the PRD intended.
    """
    if candidates <= 0:
        return 0.0
    return new_candidates / candidates


def should_continue(
    *,
    round_index: int,
    new_candidates: int,
    candidates: int,
    max_rounds: int,
    novelty_min: float,
    min_new_documents: int = 2,
) -> tuple[bool, str]:
    """Decide whether another round is warranted, and say why not.

    Saturation first, budget last -- the ordering the PRD requires, so a stop
    is attributed to the evidence running dry rather than to a rail whenever
    both would have fired.
    """
    ratio = novelty_ratio(new_candidates, candidates)
    if new_candidates < min_new_documents or ratio < novelty_min:
        return False, "saturation"
    if round_index >= max_rounds:
        return False, "max_rounds"
    return True, "continue"


def coverage_summary(topic: str, per_query: list[tuple[str, int, int]]) -> str:
    """Render the per-query coverage the gap pass reasons over."""
    lines = [f"Topic: {topic}", "", "Coverage so far:"]
    for query, documents, domains in per_query:
        lines.append(
            f"- \"{query}\": {documents} document(s) from {domains} domain(s)"
        )
    return "\n".join(lines)


async def plan_gap_round(
    ollama,
    topic: str,
    per_query: list[tuple[str, int, int]],
    issued_queries: list[str],
    issued_vectors: list[list[float]],
    *,
    distinct: float,
    max_total: int,
    job_id: str | None = None,
) -> tuple[list[str], list[list[float]], list[FacetDecision], str]:
    """Propose and admit the next round's gap-driven queries.

    Never raises: a failure yields no queries, which ends the rounds with the
    reason recorded rather than failing an otherwise healthy job.
    """
    if max_total <= len(issued_queries):
        return [], issued_vectors, [], "budget"
    try:
        raw = await ollama.generate(
            f"{GAP_RULES}\n\n{coverage_summary(topic, per_query)}",
            system=GAP_SYSTEM,
            max_tokens=PLANNER_MAX_TOKENS,
            json_schema=FACET_SCHEMA,
            diagnostic_stage="query_plan_gap",
        )
        candidates = parse_candidates(raw)
    except Exception as exc:
        logger.warning(
            "query_plan_gap_unavailable",
            extra={"job_id": job_id, "failure_reason": type(exc).__name__},
        )
        return [], issued_vectors, [], "planner_unavailable"

    if not candidates:
        # The gap pass was asked to return nothing once the corpus covers the
        # topic; that is the coverage stop, not a failure.
        return [], issued_vectors, [], "coverage"

    try:
        candidate_vectors = await ollama.embed_batch(candidates)
        if len(candidate_vectors) != len(candidates):
            raise ValueError("embedding count mismatch")
        queries, vectors, decisions, _ = greedy_admit(
            issued_queries, issued_vectors, candidates, candidate_vectors,
            distinct=distinct, max_total=max_total,
        )
    except Exception as exc:
        logger.warning(
            "query_plan_gap_admission_failed",
            extra={"job_id": job_id, "failure_reason": type(exc).__name__},
        )
        return [], issued_vectors, [], "planner_unavailable"

    fresh = queries[len(issued_queries):]
    if not fresh:
        # Every proposed gap was already covered by an issued query.
        return [], vectors, decisions, "coverage"
    return fresh, vectors, decisions, "continue"


def acquisition_provenance(
    plan: QueryPlan,
    *,
    issued_queries: list[str],
    decisions: list[FacetDecision],
    rounds: list[RoundRecord],
    stop_reason: str,
) -> dict[str, Any]:
    """The full audit trail for one job's acquisition, for job progress.

    A degenerate plan has to be visible in provenance rather than inferred
    from a thin report, so this records every query issued, every admission
    decision including refusals, each round's novelty yield, and why the
    rounds stopped.
    """
    return {
        "queries": list(issued_queries),
        "facet_count": len(plan.queries),
        "collapsed": plan.collapsed,
        "stop_reason": plan.stop_reason,
        "acquisition_stop_reason": stop_reason,
        "rounds": [r.as_dict() for r in rounds],
        "decisions": [d.as_dict() for d in decisions],
    }


def interleave(results_by_facet: list[list[dict]]) -> list[dict]:
    """Round-robin facet results so the crawl budget is shared across facets.

    Concatenating would let the first facet consume the whole ``depth`` cap and
    silently reduce a multi-facet plan to a single-facet corpus. Each facet
    keeps its own internal ranking. Canonical-URL deduplication is deliberately
    left to ``apply_source_policy``, which is the single place that also
    applies allow/block lists, per-domain limits and freshness.
    """
    merged: list[dict] = []
    for row in range(max((len(r) for r in results_by_facet), default=0)):
        for results in results_by_facet:
            if row < len(results):
                merged.append(results[row])
    return merged
