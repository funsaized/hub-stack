"""Source coverage at k — the breadth diagnostic that sits beside recall.

**Coverage is a diagnostic, never a target.** Coverage@k is trivially
maximised by returning one chunk from each of k different sources, which is
also one of the worst things a retriever can do: it strands every multi-chunk
argument halfway through. A retrieval or chunking change is judged on
coverage *and* recall together, or it is not judged. Nothing in this module
returns a pass/fail, and no benchmark gate reads it — that is deliberate, and
any future gate on coverage alone is a bug, not an upgrade.

**The unit is the document.** Graph-Aware Late Chunking (arXiv 2603.22633)
counts *sections within* documents (SecCov@k) because its retriever knows
where sections begin. This corpus does not: chunking is a fixed 800/100
recursive split (HUB-045), so `chunk_index` marks position, not structure, and
a "section" here would just be a chunk — making section coverage a restatement
of `chunks_selected`. The document is also the unit every neighbouring
mechanism already uses: `max_chunks_per_source` caps per document, `[S#]`
citations attribute per document, and acquisition retains per document. If
HUB-045 ever gives chunks real structure, this is the line to revisit.

**Three numbers, because one denominator would be a lie.** `sources_at_k` is a
count and needs no denominator; the two fractions each name theirs:

- `saturation_at_k = sources_at_k / min(k, sources_reachable)` — breadth as a
  fraction of the most that *this ranking over this pool* could have shown at
  this k. Well-defined in every scope, so it is the comparable number.
- `scope_coverage_at_k = sources_at_k / sources_in_scope` — breadth against
  the sources the scope made eligible. Meaningful job-scoped, where the scope
  is the handful of sources a report may cite. Meaningless corpus-wide, where
  the denominator is the whole 679-document corpus and any k ≤ 40 caps the
  fraction near zero by arithmetic alone; pass `sources_in_scope=None` there
  and the field is reported as `null` rather than as a number that looks like
  a failure.

`sources_in_scope` is also not the ceiling it appears to be job-scoped: 33
retained documents have no Qdrant chunks (deduplicated sources, HUB-043), so
retrieval cannot reach them and those jobs cannot reach 1.0.

`sources_reachable` is required and has no default on purpose. The obvious
default — the sources present in the ranking being measured — makes
saturation 1.0 at every k past the end of that ranking, by construction,
which is the exact species of free perfect score this module exists to avoid.
Callers name it instead: the distinct sources in the fused candidate pool,
which the retrieval benchmarks obtain by running the same query once with the
per-source cap lifted.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence


# Dense candidate pools are 40 (benchmark default) to 120 (deployed) chunks
# wide; the low k values are where a report's evidence actually comes from.
DEFAULT_KS: tuple[int, ...] = (1, 2, 3, 4, 5, 8, 10, 16, 20, 32, 40)


def source_of(candidate: Any) -> str:
    """The coverage unit of one candidate: its document."""
    if isinstance(candidate, dict):
        return str(candidate["document_id"])
    return str(candidate.document_id)


def coverage_at_k(
    candidates: Sequence[Any],
    ks: Iterable[int] = DEFAULT_KS,
    *,
    sources_reachable: int,
    sources_in_scope: int | None = None,
) -> list[dict]:
    """Breadth of the top-k prefix of one ranking, one point per k.

    ``candidates`` is a ranking, most relevant first: the selected chunks of a
    ``RetrievedEvidence``, or any list carrying ``document_id``. Points whose
    k exceeds the ranking are still reported, with ``chunks_at_k`` recording
    how short the ranking fell — a ranking of 6 chunks has no coverage@10, and
    silently reporting its coverage@6 under that name would overstate it.
    """
    if sources_in_scope is not None and sources_in_scope < 0:
        raise ValueError("sources_in_scope cannot be negative")
    if sources_reachable < 1:
        raise ValueError("sources_reachable must be positive")

    points = []
    for k in sorted({int(value) for value in ks}):
        if k < 1:
            raise ValueError("k must be positive")
        prefix = candidates[:k]
        sources = len({source_of(candidate) for candidate in prefix})
        # Not `min(k, pool, len(prefix))`: a ranking that ran out of chunks
        # scores below 1.0 here on purpose. It failed to show breadth it was
        # asked for, and crediting it for the chunks it happened to have is
        # how a metric quietly stops measuring anything.
        attainable = min(k, sources_reachable)
        points.append({
            "k": k,
            "chunks_at_k": len(prefix),
            "sources_at_k": sources,
            "attainable_sources_at_k": attainable,
            "saturation_at_k": _ratio(sources, attainable),
            "scope_coverage_at_k": (
                _ratio(sources, sources_in_scope)
                if sources_in_scope is not None else None
            ),
        })
    return points


def coverage_summary(
    candidates: Sequence[Any],
    *,
    sources_reachable: int,
    sources_in_scope: int | None = None,
    ks: Iterable[int] = DEFAULT_KS,
) -> dict:
    """A whole-ranking breadth record: the curve plus its context."""
    sources = len({source_of(candidate) for candidate in candidates})
    return {
        "chunks_selected": len(candidates),
        "sources_represented": sources,
        "sources_in_scope": sources_in_scope,
        "sources_reachable": sources_reachable,
        "chunks_per_represented_source": _ratio(len(candidates), sources),
        "curve": coverage_at_k(
            candidates, ks,
            sources_reachable=sources_reachable,
            sources_in_scope=sources_in_scope,
        ),
    }


def micro_average(curves: Sequence[Sequence[dict]]) -> list[dict]:
    """Pool coverage points across cases by summing, never by averaging rates.

    A case whose ranking ran out of chunks would otherwise contribute a
    saturation of 1.0 at every k and pull the mean up; summing counts lets a
    short ranking contribute only the breadth it actually had.
    """
    totals: dict[int, dict] = {}
    for curve in curves:
        for point in curve:
            entry = totals.setdefault(point["k"], {
                "k": point["k"], "cases": 0, "chunks_at_k": 0,
                "sources_at_k": 0, "attainable_sources_at_k": 0,
            })
            entry["cases"] += 1
            for name in ("chunks_at_k", "sources_at_k", "attainable_sources_at_k"):
                entry[name] += point[name]
    return [
        {**entry, "saturation_at_k": _ratio(
            entry["sources_at_k"], entry["attainable_sources_at_k"]
        )}
        for _, entry in sorted(totals.items())
    ]


def _ratio(numerator: int, denominator: int | None) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 6)
