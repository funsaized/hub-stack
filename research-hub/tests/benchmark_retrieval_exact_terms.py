"""Live exact-term retrieval probe for the HUB-017 hybrid-retrieval decision.

The deterministic report-retrieval benchmark replays pre-scored candidates
through the selection pipeline, so its Recall@4 can never show a dense
embedding miss. This command measures the real production retrieval path -
one nomic-embed-text embedding per query and one scoped Qdrant search -
against exact-term cases mined from the retained corpus: checklist item
numbers, DOIs, consensus statistics and software identifiers that each occur
in at most three of the job's chunks. A lexical channel finds these needles
by construction, so every dense miss reported here is direct evidence for
the Phase 5 / HUB-017 entry condition, and hybrid retrieval is accepted when
it lifts hit_at_k to 1.0 on this manifest without regressing the dense-only
report benchmark.

Read-only: it embeds queries, searches and scrolls Qdrant, and reads SQLite
through an immutable URI connection. It never writes, upserts, generates,
or touches reports, Redis, or the sealed evaluation fixtures.

Per case it reports whether the sentinel chunk entered the scoped candidate
pool, whether it survived selection into the top-k, its rank, and how many
chunks a trivial lexical scan finds for the same terms - measured twice, once
dense-only and once through the hybrid FTS5+RRF path, so the Phase 5
acceptance comparison runs on identical queries. Exit code 1 means fixture
drift (a term no longer occurs in the corpus) or a malformed manifest -
never a retrieval miss; misses are the measurement, not a failure.

Source coverage at k rides alongside (HUB-044), on the same queries and the
same two channels, so a needle-recall number is never read without the
breadth number beside it. It gates nothing here either: this command's only
non-zero exit is fixture drift. Each channel's reachable-source denominator
comes from the pool it actually drew on - the raw Qdrant hits for dense,
those plus the BM25 hits for hybrid - so no extra embedding is spent to
measure breadth.
"""

import argparse
import asyncio
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

from app.clients import OllamaClient, QdrantClient
from app.document_store import search_chunk_index
from app.retrieval import ScopedRetrievalService
from tests.coverage_at_k import DEFAULT_KS, coverage_at_k, micro_average


DEFAULT_MANIFEST = Path(__file__).parent / "fixtures" / "retrieval_exact_term_cases.json"
CATEGORIES = {"checklist_item", "doi", "consensus_statistic", "software_identifier"}


def load(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0.0":
        raise ValueError("schema_version must be 1.0.0")
    if not isinstance(manifest.get("job_id"), str) or not manifest["job_id"]:
        raise ValueError("job_id must be a non-empty string")
    if not isinstance(manifest.get("top_k"), int) or manifest["top_k"] < 1:
        raise ValueError("top_k must be a positive integer")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")
    seen_ids = set()
    for case in cases:
        for key in ("id", "category", "query", "expected_document_id"):
            if not isinstance(case.get(key), str) or not case[key]:
                raise ValueError(f"case {key} must be a non-empty string")
        if case["id"] in seen_ids:
            raise ValueError(f"duplicate case id {case['id']}")
        seen_ids.add(case["id"])
        if case["category"] not in CATEGORIES:
            raise ValueError(f"case {case['id']} has unknown category {case['category']}")
        terms = case.get("exact_terms")
        if not isinstance(terms, list) or not terms or not all(
            isinstance(term, str) and term for term in terms
        ):
            raise ValueError(f"case {case['id']} exact_terms must be non-empty strings")
        if not any(term.lower() in case["query"].lower() for term in terms):
            raise ValueError(
                f"case {case['id']} query must quote one of its exact terms; "
                "paraphrased queries do not probe the lexical channel"
            )
        if not isinstance(case.get("expected_chunk_index"), int) or case["expected_chunk_index"] < 0:
            raise ValueError(f"case {case['id']} expected_chunk_index must be a non-negative integer")
    return manifest


class ReadOnlyDocuments:
    """Mirror of the DocumentStore read paths over an immutable connection."""

    def __init__(self, path: str):
        self.path = path

    def _rows(self, sql: str, params: tuple) -> list[dict]:
        db = sqlite3.connect(f"file:{self.path}?immutable=1", uri=True)
        try:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(sql, params).fetchall()]
        finally:
            db.close()

    def documents_for_job(self, job_id: str) -> list[dict]:
        return self._rows(
            """SELECT documents.* FROM job_sources
               JOIN documents USING(document_id)
               WHERE job_sources.job_id = ?
               ORDER BY documents.canonical_url, documents.document_id""",
            (job_id,),
        )

    def search_chunks(self, topic: str, document_ids: list[str], limit: int) -> list[dict]:
        if not document_ids:
            raise ValueError("lexical search requires retained source scope")
        db = sqlite3.connect(f"file:{self.path}?immutable=1", uri=True)
        try:
            db.row_factory = sqlite3.Row
            return search_chunk_index(db, topic, document_ids, limit)
        finally:
            db.close()


class RecordingQdrant:
    """Delegate to the real client while retaining the raw scoped hits."""

    def __init__(self, qdrant: QdrantClient):
        self._qdrant = qdrant
        self.last_hits: list[dict] = []

    def search_evidence(self, vector, limit, **scope) -> list[dict]:
        self.last_hits = self._qdrant.search_evidence(vector, limit, **scope)
        return self.last_hits


def scroll_job_chunks(qdrant: QdrantClient, document_ids: list[str]) -> list[dict]:
    from qdrant_client.models import FieldCondition, Filter, MatchAny

    chunks = []
    offset = None
    while True:
        points, offset = qdrant._client.scroll(
            collection_name=qdrant.collection,
            scroll_filter=Filter(must=[
                FieldCondition(key="document_id", match=MatchAny(any=document_ids))
            ]),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        chunks.extend(
            {
                "document_id": point.payload.get("document_id"),
                "chunk_index": point.payload.get("chunk_index"),
                "text": point.payload.get("text", ""),
            }
            for point in points
        )
        if offset is None:
            return chunks


def _at(curve: list[dict], k: int) -> dict:
    return next(point for point in curve if point["k"] == k)


def sentinel_matches(candidate, case: dict) -> bool:
    if candidate.document_id != case["expected_document_id"]:
        return False
    if candidate.chunk_index == case["expected_chunk_index"]:
        return True
    return any(term.lower() in candidate.text.lower() for term in case["exact_terms"])


async def evaluate(manifest: dict) -> dict:
    ollama = OllamaClient(
        os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        os.environ.get("LLM_MODEL", "qwen3.5:9b"),
        os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
    )
    qdrant = QdrantClient(
        os.environ.get("QDRANT_URL", "http://localhost:6333"),
        os.environ.get("QDRANT_COLLECTION", "research_corpus__nomic_embed_text_768"),
    )
    documents = ReadOnlyDocuments(
        os.environ.get("DOCUMENT_STORE_PATH", "/app/data/documents.sqlite3")
    )
    recorder = RecordingQdrant(qdrant)
    limits = {
        "candidate_limit": int(os.environ.get("REPORT_RETRIEVAL_CANDIDATES", "40")),
        "max_chunks_per_source": int(os.environ.get("REPORT_MAX_CHUNKS_PER_SOURCE", "3")),
    }
    dense_service = ScopedRetrievalService(ollama, recorder, documents, **limits)
    hybrid_service = ScopedRetrievalService(
        ollama, recorder, documents, lexical=documents,
        rrf_k=int(os.environ.get("REPORT_RRF_K", "60")), **limits,
    )

    job_id = manifest["job_id"]
    top_k = manifest["top_k"]
    retained = documents.documents_for_job(job_id)
    retained_ids = sorted({doc["document_id"] for doc in retained})
    chunks = scroll_job_chunks(qdrant, retained_ids)
    # The job's real breadth ceiling: retained sources that own at least one
    # embedded chunk. Deduplicated sources retain a row and no chunks
    # (HUB-043), so `len(retained)` overstates what retrieval can reach.
    sources_with_chunks = len({chunk["document_id"] for chunk in chunks})

    coverage_ks = sorted({*DEFAULT_KS, top_k})
    drift = []
    results = []
    curves: list[tuple[list[dict], list[dict]]] = []
    try:
        for case in manifest["cases"]:
            lexical = [
                chunk for chunk in chunks
                if any(term.lower() in chunk["text"].lower() for term in case["exact_terms"])
            ]
            if not any(
                chunk["document_id"] == case["expected_document_id"] for chunk in lexical
            ):
                drift.append(case["id"])
                continue

            dense_retrieved = await dense_service.retrieve(job_id, case["query"])
            dense_selected = dense_retrieved.candidates[:top_k]
            dense_pool = {
                hit.get("document_id") for hit in recorder.last_hits
            } & set(retained_ids)
            lexical_pool = {
                hit["document_id"]
                for hit in documents.search_chunks(
                    case["query"], retained_ids, limits["candidate_limit"]
                )
            } & set(retained_ids)
            pool_hit = any(
                hit.get("document_id") == case["expected_document_id"]
                and (
                    hit.get("chunk_index") == case["expected_chunk_index"]
                    or any(
                        term.lower() in str(hit.get("text", "")).lower()
                        for term in case["exact_terms"]
                    )
                )
                for hit in recorder.last_hits
            )
            dense_ranks = [
                rank for rank, candidate in enumerate(dense_selected, 1)
                if sentinel_matches(candidate, case)
            ]
            hybrid_retrieved = await hybrid_service.retrieve(job_id, case["query"])
            hybrid_selected = hybrid_retrieved.candidates[:top_k]
            hybrid_ranks = [
                rank for rank, candidate in enumerate(hybrid_selected, 1)
                if sentinel_matches(candidate, case)
            ]
            dense_curve = coverage_at_k(
                dense_retrieved.candidates, coverage_ks,
                sources_reachable=max(len(dense_pool), 1),
                sources_in_scope=sources_with_chunks,
            )
            hybrid_curve = coverage_at_k(
                hybrid_retrieved.candidates, coverage_ks,
                sources_reachable=max(len(dense_pool | lexical_pool), 1),
                sources_in_scope=sources_with_chunks,
            )
            results.append({
                "id": case["id"],
                "category": case["category"],
                "lexical_chunks": len(lexical),
                "candidates_considered": len(recorder.last_hits),
                "in_candidate_pool": pool_hit,
                "hit_at_k": bool(dense_ranks),
                "rank": dense_ranks[0] if dense_ranks else None,
                "hybrid_hit_at_k": bool(hybrid_ranks),
                "hybrid_rank": hybrid_ranks[0] if hybrid_ranks else None,
                "dense_sources_reachable": len(dense_pool),
                "hybrid_sources_reachable": len(dense_pool | lexical_pool),
                "dense_coverage_at_top_k": _at(dense_curve, top_k),
                "hybrid_coverage_at_top_k": _at(hybrid_curve, top_k),
            })
            curves.append((dense_curve, hybrid_curve))
    finally:
        await ollama.close()

    hits = sum(result["hit_at_k"] for result in results)
    hybrid_hits = sum(result["hybrid_hit_at_k"] for result in results)
    pool_hits = sum(result["in_candidate_pool"] for result in results)
    by_category = {}
    for category, count in sorted(Counter(r["category"] for r in results).items()):
        category_results = [r for r in results if r["category"] == category]
        by_category[category] = {
            "cases": count,
            "hit_at_k": sum(r["hit_at_k"] for r in category_results),
            "hit_rate": round(sum(r["hit_at_k"] for r in category_results) / count, 6),
            "hybrid_hit_at_k": sum(r["hybrid_hit_at_k"] for r in category_results),
            "hybrid_hit_rate": round(
                sum(r["hybrid_hit_at_k"] for r in category_results) / count, 6
            ),
        }
    return {
        "provenance": manifest["provenance"],
        "job_id": job_id,
        "top_k": top_k,
        "chunks_scanned": len(chunks),
        "sources_with_chunks": sources_with_chunks,
        "sources_retained": len(retained),
        "cases": results,
        "fixture_drift": drift,
        "coverage": {
            "unit": "document",
            "gates": "none — reported beside recall, never instead of it (HUB-044)",
            "dense": micro_average([curve for curve, _ in curves]),
            "hybrid": micro_average([curve for _, curve in curves]),
        },
        "summary": {
            "total_cases": len(results),
            "dense_hit_at_k": hits,
            "dense_hit_rate": round(hits / len(results), 6) if results else 0.0,
            "hybrid_hit_at_k": hybrid_hits,
            "hybrid_hit_rate": round(hybrid_hits / len(results), 6) if results else 0.0,
            "in_candidate_pool": pool_hits,
            "pool_rate": round(pool_hits / len(results), 6) if results else 0.0,
            "by_category": by_category,
            "misses": [result["id"] for result in results if not result["hit_at_k"]],
            "hybrid_misses": [
                result["id"] for result in results if not result["hybrid_hit_at_k"]
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    manifest = load(args.manifest)
    report = asyncio.run(evaluate(manifest))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["fixture_drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
