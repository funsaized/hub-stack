# Research RAG Synthesis Modernization Progress

Companion execution log for
[`research-rag-synthesis-modernization.md`](research-rag-synthesis-modernization.md).

## 2026-08-10 — Phase 0: baseline and executable specification

Status: ready for Phase 0 review; production behavior is unchanged.

Scope selected:

- Phase 0 only, because the PRD defines it as the first gated unit of work.
- No Phase 1 schema or ingestion changes were made.
- No research jobs, crawls, embeddings, or Qdrant upserts were launched.

Baseline captured before edits:

- Git 2.54.0 is installed at the Hermes-managed path
  `C:\Users\saigu\AppData\Local\hermes\git\cmd\git.exe`. The initial sandboxed command
  could not resolve or inspect that external path; a direct approved invocation confirmed
  `git status --short --branch` reports `## main`. No reset, checkout, or other operation
  that could disturb pre-existing work was performed.
- Existing synthesis suite: 3 tests passed in 0.129 seconds.
- Existing full Research Hub suite: 58 tests passed in 0.648 seconds with no skips,
  using the application image mounted at `/app` and Compose Redis database 15.

Executable specifications added:

- `test_relevant_evidence_after_long_prefix_is_used` retains a unique sentinel after a
  prefix longer than the current synthesis excerpt allocation and supplies a deterministic
  mocked retrieval hit for that sentinel. It is expected to fail until report synthesis
  consumes retrieved chunks instead of document prefixes.
- `test_unchanged_document_remains_visible_to_every_observing_job` records the same
  canonical document for two jobs and expects both jobs to retain access. It is expected
  to fail while `documents.job_id` is mutable exclusive ownership.
- `test_existing_database_backfills_historical_job_observation` simulates an existing
  pre-`job_sources` database, reopens it twice, and expects an idempotent observation
  backfill. It is expected to fail until the Phase 1 migration exists.

Phase 0 gate evidence:

- The late-evidence test fails with: `retrieved late-evidence sentinel was absent from
  the generation prompt`.
- The cross-job reuse test fails because Job A returns `[]` after Job B saves the same
  document, instead of retaining `doc-1`.
- The legacy migration test fails because `job_sources` is absent after two store
  initializations.
- The complete post-specification suite runs 61 tests: the 58 baseline tests still pass,
  and exactly the three new executable specifications fail as expected.

Next proposed work (not started): Phase 1 — immutable job/source observations.

## 2026-08-10 — Phase 1: immutable job/source observations

Status: ready for Phase 1 review; Phase 2 scoped retrieval and Phase 3 synthesis changes
were not started.

Pre-edit checks:

- `git status --short --branch` reported `## main` and the existing modification to
  this progress file. That change was preserved and this entry was appended.
- The three Phase 0 specifications failed for the documented reasons: Job A lost
  `doc-1`, the legacy database had no `job_sources` table, and the late-evidence
  sentinel was absent from the synthesis prompt.
- After direct-store tests were changed to use the explicit observation API, the new
  tests failed before production edits because `observe_job_source` did not exist and
  ingestion did not call it.

Files changed:

- `research-hub/app/document_store.py`: added the idempotent `job_sources` table and
  `(document_id, job_id)` index, deterministic legacy backfill, explicit observation
  upsert, non-transferring canonical saves, and observation-joined job reads.
- `research-hub/app/research.py`: records each job/source observation immediately after
  retaining the canonical document and before the unchanged-document fast path.
- `research-hub/tests/test_documents_and_batches.py`: uses explicit observations and
  verifies two-job retention, one canonical row, two observations, legacy timestamp and
  metadata backfill, index creation, and repeated-initialization idempotency.
- `research-hub/tests/test_ingestion_idempotency.py`: verifies unchanged ingestion records
  its observation without embedding.
- `research-hub/tests/test_synthesis.py`: direct fixtures now use the explicit observation
  API; synthesis and public behavior are otherwise unchanged.
- `PRDs/research-rag-synthesis-modernization-progress.md`: recorded this Phase 1 gate.

Test outputs (application container mounted at `/app`, Compose Redis database 15):

- Observation plus ingestion-idempotency gate: 11 tests passed in 0.128 seconds.
- Existing synthesis lifecycle gate: 3 tests passed in 0.137 seconds.
- Complete suite: 62 tests ran in 0.785 seconds; 61 passed with no skips and exactly one
  expected failure, `test_relevant_evidence_after_long_prefix_is_used`. All four Redis
  worker integration tests ran and passed.
- The expected remaining failure still reports: `retrieved late-evidence sentinel was
  absent from the generation prompt`. It belongs to Phase 3.
- The repository CRLF-aware `git diff --check` completed with exit code 0 and no
  whitespace errors.

Phase 1 acceptance:

- Existing document rows remain readable and `documents.job_id` remains present.
- An unchanged version retains one canonical Markdown row while Job A and Job B each
  retain an immutable association to it.
- Canonical saves no longer transfer the legacy `documents.job_id` owner.
- `documents_for_job(job_id)` reads through `job_sources` in stable canonical URL and
  document ID order, so older-job report lookup still finds the reused source.
- Legacy rows backfill from `fetched_at` with `created_at` as a deterministic fallback,
  preserve research metadata, and repeated initialization does not duplicate rows.
- Ingestion observes sources before checking whether embedding can be skipped.
- No public API, retrieval, report synthesis, crawl, embedding, Qdrant data, job, commit,
  or push was changed or launched.

Remaining risks:

- A legacy database can recover only the single association still present in
  `documents.job_id`; associations overwritten before this migration cannot be inferred.
- Qdrant job/topic payloads remain historical and mutable in meaning. Phase 2 must scope
  retrieval through SQLite observations rather than those payload fields.

Next proposed work (not started): Phase 2 — scoped retrieval domain service, after Phase
1 review approval.

## 2026-08-10 — Phase 2: scoped retrieval domain service

Status: ready for Phase 2 review; report synthesis remains unchanged and Phase 3 was not
started.

Pre-edit checks:

- `git status --short --branch` reported `## main`; there were no existing working-tree
  changes to disturb.
- Five Phase 1 observation and unchanged-ingestion tests passed in 0.119 seconds.
- The baseline complete suite ran 62 tests in 0.776 seconds: 61 passed, all four Redis
  worker integration tests ran, and the late-evidence synthesis specification was the
  single expected failure.
- The first focused Phase 2 run failed before production edits because
  `app.retrieval` did not exist.

Files changed:

- `research-hub/app/clients.py`: added an internal `search_evidence()` path that requires
  retained canonical URL and/or document ID scope, uses Qdrant `MatchAny`, and returns
  document ID, canonical URL, chunk index, score, title, text, and security metadata.
- `research-hub/app/retrieval.py`: added transport-neutral scoped retrieval types and
  service behavior with one topic embedding, defensive retained-identity checks,
  deterministic score/tie ordering, exact duplicate removal, per-source caps, an
  optional score threshold, sanitization, and diagnostics.
- `research-hub/app/context.py`: centralized the existing sanitization and complete-entry
  packing primitives so query and internal retrieval retain identical conservative token
  accounting and untrusted-evidence delimiters.
- `research-hub/app/query.py`: delegates only its existing context primitives to the
  shared implementation; `/query`, `/rag`, and corpus chat behavior remain unchanged.
- `research-hub/app/research.py`: constructs the scoped retrieval service from the
  existing SQLite document store and Phase 2 settings; synthesis does not call it yet.
- `research-hub/app/config.py`, `docker-compose.yml`, and `.env.example`: added and
  bounded only `REPORT_RETRIEVAL_CANDIDATES`, `REPORT_MAX_CHUNKS_PER_SOURCE`, and the
  disabled-by-default `REPORT_RETRIEVAL_MIN_SCORE`. Context packing deliberately reuses
  the existing model context and answer reserve settings.
- `research-hub/tests/test_retrieval.py`: added deterministic Qdrant and domain-service
  coverage for retained scope, late selection, one embedding, duplicates, ties,
  per-source limits, thresholding, sanitization, diagnostics, complete-entry packing,
  and configuration validation.
- `PRDs/research-rag-synthesis-modernization-progress.md`: recorded this Phase 2 gate.

Test outputs (application container mounted at `/app`, Compose Redis database 15):

- Focused retrieval gate: 9 tests passed in 0.080 seconds.
- Retrieval plus existing context, `/query`, `/rag`, and OpenAI-compatible regression
  slice: 18 tests passed in 0.136 seconds before the two configuration assertions were
  added; both added assertions pass in the final focused gate.
- Complete suite: 71 tests ran in 0.860 seconds; 70 passed with no skips and exactly one
  expected failure, `test_relevant_evidence_after_long_prefix_is_used`. All four Redis
  worker integration tests ran and passed against the healthy Compose Redis service.
- The expected remaining failure still reports: `retrieved late-evidence sentinel was
  absent from the generation prompt`. The retrieval service selects that sentinel in its
  own test, but synthesis still uses document prefixes until Phase 3.
- `docker compose config --quiet` completed with exit code 0.
- Container `python -m pip check` reported `No broken requirements found.`
- The repository CRLF-aware `git diff --check` completed with exit code 0 and no
  whitespace errors.

Phase 2 acceptance:

- Retrieval scope comes from `DocumentStore.documents_for_job()`, which reads the Phase 1
  `job_sources` join. Qdrant `job_id` and topic payloads are neither filtered on nor
  trusted.
- Qdrant scope uses retained canonical URLs and document IDs with `MatchAny`; returned
  candidates are also checked against the exact retained ID/URL pair.
- A retrieval with retained sources embeds the topic exactly once and performs no source
  embedding, crawl, upsert, or model generation.
- Late-document evidence can be selected, scores and ties are stable, exact duplicate
  text is removed, per-source limits are enforced, and the optional score threshold is
  disabled by default.
- Selected evidence is sanitized again at retrieval time, including older or rebuilt
  payloads, and packing keeps complete delimited entries under conservative token bounds.
- Diagnostics report considered and selected candidates, available and represented
  sources, and selected score bounds.
- Existing public request/response models, `/query`, `/rag`, OpenAI-compatible chat,
  ingestion idempotency, observations, report lifecycle, and worker behavior remain
  unchanged.
- No research job, crawl, retained-data re-embedding, public API change, synthesis change,
  commit, push, BM25, reranking, MMR, query decomposition, GraphRAG, or map-reduce work
  occurred.

Remaining risks:

- Report generation does not consume the retrieval service until Phase 3; the intentional
  late-evidence synthesis failure remains the review boundary.
- Dense retrieval quality has not yet been measured against a representative manifest;
  the minimum score therefore remains disabled as required.
- No live Ollama/Qdrant report retry or latency measurement was run because this phase
  prohibited launching research jobs and changing synthesis.

Next proposed work (not started): Phase 3 — retrieval-based report synthesis, only after
Phase 2 review approval.
