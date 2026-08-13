"""Live source-coverage baseline for both retrieval scopes (HUB-044).

Breadth was already counted at one point - `RetrievalDiagnostics` reports
`sources_available` and `sources_represented`, synthesis writes both into job
progress, and `hub_report_retrieval_items` observes them. That number is a
single post-cap total: it is taken after `max_chunks_per_source` has already
forced diversity, so it cannot say whether the ranking found breadth or the
cap manufactured it, and it says nothing about how breadth grows with k. It
also never existed for the corpus-wide path that HUB-043 opened.

This command measures the curve, on the real path, in both scopes:

- **Job scope**, query = the job's own topic, which is exactly the retrieval a
  report runs (`app/synthesis.py` calls `retrieve(job_id, job["topic"])`).
- **Corpus scope**, `job_id=None`, with and without the `topic`/`tags` filters
  that HUB-043 relocated into scope resolution.

Each case is retrieved twice on identical inputs: once at the deployed
`max_chunks_per_source`, once with the cap lifted to the candidate limit. The
uncapped run supplies the reachable-source denominator, and the gap between
the two curves is the answer to "is the cap doing the work, or the ranking?"

**Coverage is reported, never targeted.** It is trivially maximised by
returning one chunk per source, which strands every multi-chunk argument, so
this command has no pass/fail: it exits non-zero only on fixture drift (a job
that no longer exists, an empty scope) or a malformed manifest. Judge a
retrieval or chunking change on coverage *and* the recall numbers from
`benchmark_retrieval_exact_terms.py` together, or do not judge it.

**Denominators are named, not assumed** (`tests/coverage_at_k.py`):
`saturation_at_k` divides by what the ranking could have reached at that k;
`scope_coverage_at_k` divides by the scope, and is reported as `null`
corpus-wide, where the only honest scope denominator is the whole corpus and
any k <= the candidate limit caps the fraction near zero by arithmetic alone.
Job-scoped, the denominator is the sources holding at least one embedded
chunk, not every retained row: deduplicated sources retain a row with no
chunks (HUB-043) and no ranking can reach them.

Read-only, exactly as the exact-term probe is: it embeds queries, searches and
scrolls Qdrant, and reads SQLite through an immutable URI. It never writes,
upserts, generates, or touches reports, Redis, or the sealed fixtures.
"""

import argparse
import asyncio
import json
import os
import sqlite3
from pathlib import Path

from app.clients import OllamaClient, QdrantClient
from app.document_store import search_chunk_index
from app.retrieval import ScopedRetrievalService
from tests.coverage_at_k import DEFAULT_KS, coverage_at_k, coverage_summary, micro_average
from tests.benchmark_retrieval_exact_terms import scroll_job_chunks


DEFAULT_MANIFEST = Path(__file__).parent / "fixtures" / "retrieval_coverage_cases.json"
RETRIEVAL_COLUMNS = "document_id, canonical_url, title, fetched_at"


def load(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0.0":
        raise ValueError("schema_version must be 1.0.0")
    if not isinstance(manifest.get("provenance"), str) or not manifest["provenance"]:
        raise ValueError("provenance must be a non-empty string")
    job_cases = manifest.get("job_cases")
    corpus_cases = manifest.get("corpus_cases")
    for name, cases in (("job_cases", job_cases), ("corpus_cases", corpus_cases)):
        if not isinstance(cases, list) or not cases:
            raise ValueError(f"{name} must be a non-empty list")
    extra = manifest.get("extra_ks", [])
    if not isinstance(extra, list) or not all(
        isinstance(k, int) and k > 0 for k in extra
    ):
        raise ValueError("extra_ks must be positive integers")
    seen = set()
    for case in [*job_cases, *corpus_cases]:
        for key in ("id", "query"):
            if not isinstance(case.get(key), str) or not case[key]:
                raise ValueError(f"case {key} must be a non-empty string")
        if case["id"] in seen:
            raise ValueError(f"duplicate case id {case['id']}")
        seen.add(case["id"])
    for case in job_cases:
        if not isinstance(case.get("job_id"), str) or not case["job_id"]:
            raise ValueError(f"job case {case['id']} needs a job_id")
        if case["query"] != case.get("topic"):
            raise ValueError(
                f"job case {case['id']} query must equal its topic; a report "
                "retrieves with the job topic and nothing else"
            )
    for case in corpus_cases:
        if "job_id" in case:
            raise ValueError(f"corpus case {case['id']} must not carry a job_id")
        tags = case.get("tags")
        if tags is not None and (
            not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags)
        ):
            raise ValueError(f"corpus case {case['id']} tags must be strings")
        if case.get("topic_filter") is not None and not isinstance(
            case["topic_filter"], str
        ):
            raise ValueError(f"corpus case {case['id']} topic_filter must be a string")
    return manifest


class ReadOnlyDocuments:
    """The DocumentStore retrieval reads, over an immutable connection.

    Mirrors `documents_for_job`, `all_documents`, `documents_matching` and
    `search_chunks` including their scope contracts: `document_ids=None` means
    the whole corpus, an empty list stays an error.
    """

    def __init__(self, path: str):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(f"file:{self.path}?immutable=1", uri=True)
        db.row_factory = sqlite3.Row
        return db

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        db = self._connect()
        try:
            return [dict(row) for row in db.execute(sql, params).fetchall()]
        finally:
            db.close()

    def documents_for_job(self, job_id: str) -> list[dict]:
        columns = ", ".join(f"documents.{name}" for name in RETRIEVAL_COLUMNS.split(", "))
        return self._rows(
            f"""SELECT {columns} FROM job_sources
                JOIN documents USING(document_id)
                WHERE job_sources.job_id = ?
                ORDER BY documents.canonical_url, documents.document_id""",
            (job_id,),
        )

    def all_documents(self) -> list[dict]:
        return self._rows(
            f"""SELECT {RETRIEVAL_COLUMNS} FROM documents
                ORDER BY canonical_url, document_id"""
        )

    def documents_matching(
        self, *, topic: str | None = None, tags: list[str] | None = None,
    ) -> list[dict]:
        clauses, args = [], []
        if topic is not None:
            clauses.append("json_extract(research_metadata, '$.topic') = ?")
            args.append(topic)
        if tags:
            placeholders = ",".join("?" for _ in tags)
            clauses.append(
                f"""EXISTS (SELECT 1 FROM json_each(research_metadata, '$.tags')
                            WHERE json_each.value IN ({placeholders}))"""
            )
            args.extend(tags)
        if not clauses:
            return self.all_documents()
        # The clauses read `job_sources.research_metadata`, never the column of
        # the same name on `documents`: a page found by two jobs on different
        # topics belongs to both and only `job_sources` records the second.
        return self._rows(
            f"""SELECT {RETRIEVAL_COLUMNS} FROM documents
                WHERE document_id IN (
                    SELECT document_id FROM job_sources
                    WHERE {' AND '.join(clauses)}
                )
                ORDER BY canonical_url, document_id""",
            tuple(args),
        )

    def search_chunks(
        self, topic: str, document_ids: list[str] | None, limit: int
    ) -> list[dict]:
        if document_ids is not None and not document_ids:
            raise ValueError("lexical search requires retained source scope")
        db = self._connect()
        try:
            return search_chunk_index(db, topic, document_ids, limit)
        finally:
            db.close()


def services(ollama, qdrant, documents, candidate_limit: int, cap: int, rrf_k: int):
    return ScopedRetrievalService(
        ollama, qdrant, documents,
        candidate_limit=candidate_limit,
        max_chunks_per_source=cap,
        lexical=documents,
        rrf_k=rrf_k,
    )


async def measure(
    capped_service, uncapped_service, case: dict, ks, *, sources_in_scope: int | None,
) -> dict:
    """One case, retrieved twice on identical inputs: deployed cap, then none."""
    scope = {
        "source_topic": case.get("topic_filter"),
        "source_tags": case.get("tags"),
    }
    job_id = case.get("job_id")
    capped = await capped_service.retrieve(job_id, case["query"], **scope)
    uncapped = await uncapped_service.retrieve(job_id, case["query"], **scope)
    reachable = max(
        len({candidate.document_id for candidate in uncapped.candidates}), 1
    )
    capped_curve = coverage_summary(
        capped.candidates, sources_reachable=reachable,
        sources_in_scope=sources_in_scope, ks=ks,
    )
    uncapped_curve = coverage_at_k(
        uncapped.candidates, ks,
        sources_reachable=reachable, sources_in_scope=sources_in_scope,
    )
    return {
        "id": case["id"],
        "scope": "job" if job_id else "corpus",
        "job_id": job_id,
        "query": case["query"],
        "topic_filter": case.get("topic_filter"),
        "tags": case.get("tags"),
        "sources_in_scope_rows": capped.diagnostics.sources_available,
        "sources_reachable": reachable,
        "capped": capped_curve,
        "uncapped_curve": uncapped_curve,
        # Positive: the per-source cap bought breadth the ranking did not.
        # Zero: the ranking was already that broad and the cap is inert here.
        "breadth_bought_by_cap": [
            {
                "k": point["k"],
                "sources_at_k": point["sources_at_k"] - bare["sources_at_k"],
            }
            for point, bare in zip(capped_curve["curve"], uncapped_curve)
        ],
    }


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
    # Deployed values, not the benchmark's own: a baseline measured under
    # limits the stack does not run is a baseline of nothing.
    candidate_limit = int(os.environ.get("REPORT_RETRIEVAL_CANDIDATES", "120"))
    cap = int(os.environ.get("REPORT_MAX_CHUNKS_PER_SOURCE", "3"))
    rrf_k = int(os.environ.get("REPORT_RRF_K", "60"))
    capped_service = services(ollama, qdrant, documents, candidate_limit, cap, rrf_k)
    uncapped_service = services(
        ollama, qdrant, documents, candidate_limit, candidate_limit, rrf_k
    )
    ks = sorted({*DEFAULT_KS, *manifest.get("extra_ks", [])})

    corpus_size = len(documents.all_documents())
    drift, cases = [], []
    try:
        for case in manifest["job_cases"]:
            retained = documents.documents_for_job(case["job_id"])
            if not retained:
                drift.append(case["id"])
                continue
            chunks = scroll_job_chunks(
                qdrant, sorted({doc["document_id"] for doc in retained})
            )
            embedded = len({chunk["document_id"] for chunk in chunks})
            measured = await measure(
                capped_service, uncapped_service, case, ks,
                sources_in_scope=embedded,
            )
            cases.append({
                **measured,
                "sources_retained": len(retained),
                "sources_with_chunks": embedded,
                "chunks_in_scope": len(chunks),
            })

        for case in manifest["corpus_cases"]:
            if case.get("topic_filter") is not None or case.get("tags"):
                scoped = documents.documents_matching(
                    topic=case.get("topic_filter"), tags=case.get("tags"),
                )
                if not scoped:
                    drift.append(case["id"])
                    continue
                # A filter is a bounded scope, so its denominator is real.
                in_scope = len(scoped)
            else:
                # Unfiltered corpus: refuse to divide by 679 (see module docs).
                in_scope = None
            measured = await measure(
                capped_service, uncapped_service, case, ks,
                sources_in_scope=in_scope,
            )
            cases.append({**measured, "corpus_documents": corpus_size})
    finally:
        await ollama.close()

    job_cases = [case for case in cases if case["scope"] == "job"]
    corpus_cases = [case for case in cases if case["scope"] == "corpus"]
    return {
        "provenance": manifest["provenance"],
        "unit": "document",
        "gates": "none — coverage is reported beside recall, never instead of it",
        "limits": {
            "candidate_limit": candidate_limit,
            "max_chunks_per_source": cap,
            "rrf_k": rrf_k,
            "channels": "dense + BM25 + RRF",
        },
        "corpus_documents": corpus_size,
        "cases": cases,
        "fixture_drift": drift,
        "summary": {
            "job_scope": {
                "cases": len(job_cases),
                "capped": micro_average([case["capped"]["curve"] for case in job_cases]),
                "uncapped": micro_average(
                    [case["uncapped_curve"] for case in job_cases]
                ),
            },
            "corpus_scope": {
                "cases": len(corpus_cases),
                "capped": micro_average(
                    [case["capped"]["curve"] for case in corpus_cases]
                ),
                "uncapped": micro_average(
                    [case["uncapped_curve"] for case in corpus_cases]
                ),
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    manifest = load(args.manifest)
    report = asyncio.run(evaluate(manifest))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if report["fixture_drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
