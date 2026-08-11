# ADR-001 — SQLite FTS5 as the lexical index for hybrid report retrieval

Date: 2026-08-11
Status: Accepted
Phase: RAG synthesis modernization, Phase 5 (`PRDs/research-rag-synthesis-modernization.md:726`)

## Context

The live exact-term probe (`tests/benchmark_retrieval_exact_terms.py`, 2026-08-11)
measured the real embed-and-search production path against 13 needle cases mined from
the retained authoritative corpus. Dense-only hit@4 was `0.6923`. Three sentinel chunks
entered the 40-candidate pool but lost dense ranking; one (DOI `10.1136/bmj.g7594`)
never entered the pool, so no reranking of existing candidates can recover it. The
Phase 5 entry condition — dense retrieval misses exact or specialized terms — is met on
measured evidence. The PRD requires a design spike over three candidates and an ADR
before implementation.

## Candidates against the decision criteria

| Criterion | SQLite FTS5 | Qdrant sparse vectors | In-process BM25 |
|---|---|---|---|
| No cloud dependency | Yes | Yes | Yes |
| Reproducible rebuild | From SQLite documents alone via `chunk_text` + `CHUNKER_VERSION` (same derivation `app/rebuild.py` already uses) | Requires collection migration and full re-ingestion through Ollama | Rebuilt on every process start from a full corpus scan |
| Persistence and migration complexity | One `CREATE VIRTUAL TABLE IF NOT EXISTS` in the existing store; no service, no volume, no migration | New collection schema, dual-write or re-ingest, sparse model choice | None persisted; state duplicated per process (API + worker) |
| Exact-term recall | Verified in-image: `"item 13b"`, `10.1136/bmj.g7594`, `"80% threshold"`, `DelphiManager` all match via unicode61 phrase/token queries with built-in `bm25()` | Depends on sparse encoder vocabulary; strong in principle | Equivalent lexical math to FTS5 |
| Operational memory | Index pages in the existing SQLite file (tens of MB at 24k chunks) | Larger collection footprint in Qdrant | Full token index resident in both API and worker processes |
| Compatibility with canonical chunk identity | Rows keyed `(document_id, chunk_index)`, identical to Qdrant payload identity | Compatible | Compatible |
| Testability | Deterministic, in-memory SQLite in unit tests; no service | Requires live/in-memory Qdrant plus sparse encoding | Deterministic but adds a dependency (`rank_bm25` or hand-rolled) |

## Spike validations

- `python:3.12-slim` runtime image ships SQLite 3.46.1 with `ENABLE_FTS5` compiled in;
  `CREATE VIRTUAL TABLE ... USING fts5(...)` succeeds in the deployed container.
- unicode61 tokenization matches all four measured dense misses, including the raw DOI
  string quoted as a phrase (punctuation is stripped consistently on both sides).
- `bm25(chunk_fts)` gives deterministic ranking; ties are broken by explicit
  `ORDER BY bm25(chunk_fts), document_id, chunk_index`.
- Derived chunks are rebuildable from the document store alone: `app/rebuild.py`
  already re-derives byte-identical chunks from `documents.markdown` using
  `chunk_text(markdown, chunk_size, chunk_overlap)` and `CHUNKER_VERSION`-stamped
  identities, with no Qdrant or embedding dependency for the text itself.

## Decision

Use SQLite FTS5 in the existing document store as the lexical channel.

- A `chunk_fts` virtual table (`document_id` and `chunk_index` unindexed, sanitized
  chunk `text` indexed, unicode61 tokenizer) lives in `documents.sqlite3` beside the
  canonical documents.
- Rows store the **sanitized** chunk text (`classify_and_sanitize` output), byte-equal
  to the Qdrant payload text, so cross-channel deduplication by text remains exact.
- Ingestion writes a document's full chunk set in one transaction immediately after the
  document row is saved; `DELETE /documents` removes FTS rows in the same operation as
  document rows; `python -m app.rebuild --lexical-only` rebuilds the whole index from
  retained documents without embedding calls (backfill for the existing corpus).
- Topic strings are reduced to quoted alphanumeric tokens joined with `OR` before
  `MATCH`, so FTS5 query-syntax metacharacters in user topics cannot inject operators
  or raise parse errors.
- Fusion is deterministic reciprocal rank fusion (`k = 60`) over the dense-ranked and
  lexical-ranked lists keyed by `(document_id, chunk_index)`, followed by the existing
  sanitize/dedup/per-source-cap selection unchanged. With the lexical channel disabled
  (`REPORT_HYBRID_RETRIEVAL=false`) or empty, ordering is byte-identical to dense-only.
- Scope: report retrieval (`ScopedRetrievalService`) only. `/query` and `/rag` remain
  dense-only; regression boundaries in the PRD keep their behavior stable.

## Rejected

- **Qdrant sparse vectors** — requires a migrated collection and full re-ingestion for
  a corpus whose lexical needs are served by an index that already has a home in
  SQLite; highest migration complexity of the three with no recall advantage on the
  measured misses.
- **In-process BM25** — adds a dependency or bespoke scoring code, duplicates index
  state in every process, and persists nothing; equivalent recall to FTS5 with strictly
  worse operational properties here.

## Acceptance

Hybrid retrieval ships enabled only if, on the same fixture manifest
(`tests/fixtures/retrieval_exact_term_cases.json`): hybrid hit@4 is `1.0`, the
dense-only report retrieval benchmark and full test suite show no regression, and the
sealed claim-support contract is untouched.
