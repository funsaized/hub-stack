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

## 2026-08-10 - Phase 3: retrieval-based report synthesis

Status: Phase 3 gate is green and ready for review; Phase 4 and optional retrieval
enhancements were not started.

Pre-edit checks:

- `git status --short --branch` reported `## main`, and HEAD was the requested clean
  Phase 2 baseline commit `34f173a` (`feat(research): add scoped retrieval service`).
- Focused retrieval baseline: 9 tests passed in 0.086 seconds.
- Complete baseline: 71 tests ran in 0.863 seconds; 70 passed with no skips and
  `test_relevant_evidence_after_long_prefix_is_used` was the single expected failure.
  All four Redis worker integration tests ran against healthy Compose Redis.
- The first expanded synthesis specification run executed 8 tests in 0.356 seconds.
  Four failed for the intended pre-implementation reasons: document prefixes still hid
  the late sentinel, empty retrieval still called generation, unrepresented `[S2]` was
  accepted, and shared complete-entry delimiters were absent.

Files changed:

- `research-hub/app/synthesis.py`: builds the stable SQLite source registry before
  retrieval, packs only scoped sanitized candidates, maps packed entries to stable source
  IDs, validates findings and disagreements against represented sources, returns explicit
  insufficient-evidence content without generation, and preserves two attempts, omission,
  retry, previous-report, attempt-count, and failure-persistence behavior.
- `research-hub/app/retrieval.py` and `research-hub/app/context.py`: let report packing
  render stable `[S#]` identities plus exact retained document IDs while leaving existing
  query packing behavior unchanged.
- `research-hub/app/observability.py`: adds bounded report retrieval counts, outcome and
  claim-rejection counters, report generation latency, and structured count/outcome fields;
  no topic, URL, job ID, or free text is used as a Prometheus label.
- `research-hub/tests/test_synthesis.py`: covers late evidence, exact source mapping,
  unrepresented and invalid citations, empty retrieval, packing-to-zero, sanitization,
  complete entries, correction/omission, retry isolation, attempt counts, previous-report
  protection, and failed-report persistence.
- `PRDs/research-rag-synthesis-modernization-progress.md`: records this gate.

Test and verification outputs (application container mounted at `/app`):

- Final focused synthesis gate: 10 tests passed in 0.563 seconds.
- Synthesis, retrieval, context security, `/query`, `/rag`, and OpenAI-compatible
  regression slice: 44 tests passed in 0.740 seconds.
- Final complete suite against Compose Redis database 15: 77 tests passed in 1.217 seconds,
  with no failures or skips. Existing observation, ingestion-idempotency, report lifecycle,
  Qdrant collection, public contract, and all four Redis worker tests passed.
- Container `python -m pip check`: `No broken requirements found.`
- Repository CRLF-aware `git diff --check`: exit code 0 with no whitespace errors.

Phase 3 acceptance:

- The retrieved late sentinel reaches generation; synthesis no longer reads retained
  Markdown prefixes or uses a prefix fallback.
- The source registry remains the deterministic SQLite observation order and preserves
  exact `[S#]` to retained document ID and canonical URL mappings. The generation context
  contains only completely packed selected entries, and each report entry includes that
  exact source identity.
- Findings and disagreements may cite only source IDs represented in packed generation
  context. Uncited, invalid, and merely registered-but-unrepresented citations trigger the
  existing correction attempt and are omitted if correction still fails.
- Zero retrieval candidates and candidates that all exceed the context budget produce an
  explicit insufficient-evidence report without calling generation.
- Retrieval-time prompt-injection sanitization and shared complete-entry packing remain
  active. Report retry performs one topic embedding but no search, crawl, source-chunk
  embedding, Qdrant upsert, or ingestion invocation.
- Bounded metrics/logs distinguish retrieval and packing counts, available and represented
  sources, supported/insufficient/claims-rejected/failed outcomes, rejection reasons, no
  supported findings, and report generation duration.
- Report GET/retry, `/query`, `/rag`, OpenAI-compatible, ingestion, observation, persistence,
  and Redis worker contracts remain unchanged.
- No research job or live retry was launched. No BM25, reranking, MMR, query decomposition,
  GraphRAG, map-reduce, dependency, fixture manifest, or Phase 4 work was added.

Remaining risks:

- Dense retrieval quality and production latency still need the Phase 4 deterministic
  evaluation and separately approved live retry; neither belongs to this gate.
- Retrieval/generation diagnostics are metrics and structured logs only, so they are not
  persisted or exposed through the unchanged public report schema.

Next proposed work (not started): Phase 4 evaluation, only after Phase 3 review approval.

## 2026-08-10 - Phase 4: deterministic evaluation harness and regression verification

Status: the automated Phase 4 gate is green and ready for review. The live deployed
report-retry gate remains deferred pending explicit deployment approval; Phase 5 and all
optional retrieval enhancements were not started.

Pre-edit checks:

- `git status --short --branch` reported `## main...origin/main` with no existing
  changes, and `git rev-parse --short HEAD` reported the requested Phase 3 baseline
  commit `5bcaea1`.
- The entire PRD, this progress log, and the Phase 4, retrieval, sanitization, packing,
  synthesis, citation-validation, Qdrant-scope, configuration, test, and CLI paths were
  read before editing.
- Compose Redis was healthy. The unchanged baseline command
  `docker compose run --rm --no-deps -e TEST_REDIS_URL=redis://hub-redis:6379/15
  -v "${PWD}\research-hub:/app" research-hub python -m unittest discover -s tests -v`
  ran 77 tests in 1.238 seconds with no failures or skips; all four Redis worker tests
  executed and passed.
- No pre-existing worktree changes were reset, overwritten, or removed.

TDD evidence:

- The first focused run, `docker compose run --rm --no-deps
  -v "${PWD}\research-hub:/app" research-hub python -m unittest
  tests.test_report_retrieval_benchmark -v`, ran two tests and failed because
  `tests.benchmark_report_retrieval` did not yet exist, so the expected JSON command
  output was absent.
- After the minimum command was added, the same focused run passed both tests in 1.596
  seconds. The tests run the command twice and require byte-identical JSON, then mutate
  the manifest separately to prove both a critical-sentinel miss and an invalid expected
  citation exit with status 1 and identify the failed gate in JSON.

Files changed:

- `research-hub/tests/fixtures/report_retrieval_cases.json`: adds a synthetic, non-PHI
  manifest with late-document evidence, multiple chunks from one source, overlapping
  evidence, an irrelevant source, exact duplicates, prompt-injection-like text, tied
  scores, and a retained document observed by two fixture jobs.
- `research-hub/tests/benchmark_report_retrieval.py`: adds the stdlib-only deterministic
  JSON command. It drives the production `ScopedRetrievalService`, sanitization,
  complete-entry packer, stable source mapping, and synthesis citation validators rather
  than reproducing those behaviors.
- `research-hub/tests/test_report_retrieval_benchmark.py`: verifies exact metrics,
  repeatable output, a successful exit, and nonzero recall- and citation-gate exits.
- `PRDs/research-rag-synthesis-modernization-progress.md`: records this Phase 4 gate.

Deterministic evaluation output:

- Command: `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app"
  research-hub python -m tests.benchmark_report_retrieval`.
- Exit code: 0.
- Aggregate metrics: Recall@4 `1.0`, critical Recall@4 `1.0`, Precision@4 `0.5`,
  reciprocal rank `1.0`, source coverage `1.0`, duplicate rate `0.0`, citation validity
  `1.0`, and unsupported-claim rejection count `4`.
- Both cases independently report Recall@4 `1.0`, reciprocal rank `1.0`, source coverage
  `1.0`, duplicate rate `0.0`, citation validity `1.0`, and two unsupported claims
  rejected. JSON gates report `critical_recall_at_k: true`, `citation_validity: true`,
  and `passed: true`.

Full automated verification:

- `docker compose build research-hub research-worker`: exit code 0; both images built.
- `docker compose run --rm --no-deps
  -e TEST_REDIS_URL=redis://hub-redis:6379/15
  -v "${PWD}\research-hub:/app" research-hub
  python -m unittest discover -s tests -v`: 79 tests passed in 2.652 seconds with no
  failures or skips. Synthesis, retrieval, `/query`, `/rag`, OpenAI-compatible,
  ingestion-idempotency, observation, report lifecycle, Qdrant, and all four Redis
  worker tests passed.
- `docker compose run --rm --no-deps research-hub python -m pip check`:
  `No broken requirements found.`
- `hermes verify --json`: exit code 0 with `"ok": true`; its Compose build passed and
  readiness returned HTTP 200. An initial invocation produced no output before its
  120-second wrapper timeout; the orphaned verifier process was terminated, and the
  immediate clean rerun completed in 1.5 seconds.
- `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff
  --check`: exit code 0 with no whitespace errors.

Phase 4 automated acceptance:

- Critical fixture Recall@4 is `1.0`; citation validity is `100%`.
- JSON serialization is stable and contains every required retrieval/citation metric.
  Critical recall and citation validity independently enforce nonzero failure exits.
- The harness uses production retrieval, sanitization, packing, and citation validation;
  it performs no network request, generation, crawl, embedding of retained documents,
  Qdrant upsert, report retry, or research job launch.
- No production or public API code changed, so public reports, `/query`, `/rag`, and
  OpenAI-compatible contracts remain unchanged and are covered by the complete suite.
- No LLM judge, BM25, reranking, MMR, query decomposition, GraphRAG, map-reduce,
  dependency, generated evaluation output, database, secret, or Phase 5 work was added.

Deferred live checks requiring explicit deployment approval:

- Task 4.3 remains manual and unexecuted. Retrying the authoritative clinical-AI
  standards job, binary-classifier statistics job, fact-extraction job, and usable
  confidence/fairness job would mutate deployed report state and requires explicit
  deployment approval. No live retry, recrawl, corpus upsert, or retained-document
  re-embedding was attempted in this implementation session.
- Consequently, live selected-source/chunk counts, supported/rejected claim counts,
  citation resolution, and latency before/after values remain unrecorded. This is the
  only outstanding non-automated Phase 4 gate.

Remaining risks:

- The checked-in cases are deliberately small and synthetic. They prove the observed
  failure class and deterministic invariants, but do not establish broader production
  dense-retrieval quality thresholds or healthcare relevance.
- Production retrieval latency and stochastic local-model behavior remain unmeasured
  until the separately approved deployed retries run.

Next action: review the automated Phase 4 commit and explicitly approve deployment/live
report retries if the manual gate should proceed. Phase 5 remains unstarted.

## 2026-08-10 - Phase 4 manual gate continuation

Status: blocked at the Phase 4 review gate because explicit deployment approval was not
provided. Phase 4 is not yet reviewed and accepted; Phase 5 was not started.

Pre-edit checks:

- The entire PRD and this progress log were read before editing.
- `git status --short --branch` reported `## main...origin/main` with no changes, and
  `git rev-parse --short HEAD` reported the requested commit `190dd71`.
- No existing changes were reset, overwritten, or removed.

Automated Phase 4 gate reproduction:

- `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app" research-hub
  python -m tests.benchmark_report_retrieval`: exit code 0. Aggregate critical Recall@4
  was `1.0`, citation validity was `1.0`, and the JSON `passed` gate was `true`.
- `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app" research-hub
  python -m unittest tests.test_report_retrieval_benchmark -v`: 2 tests passed in
  1.533 seconds. Both the critical-recall and citation-validity failure-exit checks passed.

Manual gate disposition:

- No explicit deployment approval was available, so none of the four PRD-selected
  retained jobs was retried.
- No research job, crawl, retained-document re-embedding, Qdrant upsert, corpus mutation,
  or deployed report-state mutation occurred.
- Live selected source/chunk counts, supported/rejected claims, citation validity, and
  latency remain unrecorded and deferred until explicit deployment approval is provided.

Final verification:

- `docker compose build research-hub research-worker`: exit code 0; both images built.
- `docker compose run --rm --no-deps
  -e TEST_REDIS_URL=redis://hub-redis:6379/15
  -v "${PWD}\research-hub:/app" research-hub
  python -m unittest discover -s tests -v`: 79 tests passed in 2.591 seconds with no
  failures or skips; all four Compose Redis worker integration tests ran and passed.
- `docker compose run --rm --no-deps research-hub python -m pip check`:
  `No broken requirements found.`
- `hermes verify --json`: exit code 0 with `"ok": true`; its Compose build passed and
  readiness returned HTTP 200.
- `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff
  --check`: exit code 0 with no whitespace errors.

Files changed:

- `PRDs/research-rag-synthesis-modernization-progress.md`: records the reproduced
  automated gate, required release checks, and the unapproved manual-gate blocker.

Acceptance and risks:

- The deterministic Phase 4 gate remains green, and public report, `/query`, `/rag`, and
  OpenAI-compatible contracts are unchanged because no production code changed.
- Phase 4 cannot satisfy its live authoritative-source acceptance criterion until the
  approved retries are completed and reviewed.
- The synthetic evaluation still does not demonstrate dense retrieval misses on exact or
  specialized terms. Phase 5 also lacks explicit user approval, so all three Phase 5
  prerequisites remain unsatisfied.

Next action: stop at the Phase 4 review gate. With explicit deployment approval, retry
only the four PRD-selected retained jobs and record the required live evidence. Do not
begin Phase 5 without separate explicit approval after Phase 4 acceptance and measured
dense-retrieval misses.

## 2026-08-10 - Phase 4 approved live report retries

Status: the four approved retained reports were retried exactly once through the deployed
report-retry endpoint. Three reports improved, but the authoritative report still contains
no supported cited finding. Phase 4 therefore remains unaccepted and Phase 5 was not
started.

Approval and pre-retry checks:

- The owner explicitly replied `yes i approve the phase 4` after being asked to approve
  the four deployed Phase 4 report retries. This was treated as deployment approval for
  those retries, not advance acceptance of their results and not Phase 5 approval.
- The entire PRD and this progress log were read. `git status --short --branch` reported
  `## main...origin/main`; local HEAD and configured upstream both resolved to
  `c146e8c1e6f07d05f1a51d0e7be4224eb33a93b4`.
- The deterministic command
  `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app" research-hub
  python -m tests.benchmark_report_retrieval` exited 0 with critical Recall@4 `1.0`,
  citation validity `1.0`, and `passed: true`. The paired command
  `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app" research-hub
  python -m unittest tests.test_report_retrieval_benchmark -v` passed both tests in
  1.718 seconds.
- `GET http://127.0.0.1:8000/research?limit=100` confirmed the original six healthcare
  jobs still total 9,698 chunks and the authoritative follow-up has 870 chunks. Before
  reports were captured with `GET /research/{job_id}/report`. The confidence job was
  selected for the combined confidence/fairness category because it was the sole original
  report with supported findings and therefore met the PRD's usable-source qualifier.

Approved retry commands:

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/research/4b8acd0f-088f-4b97-92fc-f52b69b8a3ee/report/retry" -TimeoutSec 180
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/research/d8fe8902-ecf9-4785-b19b-d2cd3de25086/report/retry" -TimeoutSec 180
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/research/6944b80b-9fd4-4422-b700-5ab3003b2c4c/report/retry" -TimeoutSec 180
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/research/ba92d5f9-fc26-44fe-83f6-30e033aa7f93/report/retry" -TimeoutSec 180
```

Per-job live evidence:

- Authoritative clinical-AI standards, job
  `4b8acd0f-088f-4b97-92fc-f52b69b8a3ee`: 40 candidates, six packed chunks, six
  available sources, and three represented sources. Selected evidence was DECIDE-AI
  `[S5]` chunk 46, the Communications Medicine systematic review `[S6]` chunks 59 and
  110, and CONSORT-AI `[S4]` chunks 95, 38, and 97. Before: zero findings and four
  omitted uncited claims. After: zero findings and three final omitted uncited claims;
  the log counted four uncited rejections across the rejected first attempt and final
  omissions. No material citation was emitted. Topic embedding took 2.571 seconds,
  inferred retrieval/persistence overhead excluding generation was 2.623 seconds, two
  generation calls totaled 14.923 seconds, and synthesis totaled 17.546 seconds
  (host-observed HTTP latency 16.631 seconds).
- Binary-classifier statistics, job
  `d8fe8902-ecf9-4785-b19b-d2cd3de25086`: 40 candidates, six packed chunks, six
  available sources, and three represented sources. Selected evidence was TRIAGE `[S4]`
  chunks 73, 387, and 71; the prospective ADHD triage study `[S3]` chunks 155 and 144;
  and the diagnostic-metrics review `[S2]` chunk 31. Before: zero findings and four
  omitted uncited claims. After: four findings covering multidimensional evaluation,
  screening sensitivity/specificity tradeoffs, safety/workload thresholding, and
  threshold dependence. One first-attempt invalid-source citation was rejected and
  corrected; no final claim was omitted. Topic embedding took 3.070 seconds, inferred
  non-generation overhead 3.126 seconds, two generation calls 11.582 seconds, synthesis
  14.708 seconds, and host-observed HTTP latency 13.997 seconds.
- Fact extraction/assertion classification, job
  `6944b80b-9fd4-4422-b700-5ab3003b2c4c`: 40 candidates, four packed chunks, four
  available sources, and two represented sources. Selected evidence was the assertion
  detection paper `[S1]` chunks 13, 43, and 51 and the multiclass tutorial `[S4]` chunk
  351. Before: zero findings and two omitted uncited claims. After: two findings, both
  supported by `[S1]`, covering assertion categories and the available pretrained model
  categories; no claim was rejected. Topic embedding took 3.238 seconds, inferred
  non-generation overhead 3.299 seconds, generation 8.273 seconds, synthesis 11.572
  seconds, and host-observed HTTP latency 11.050 seconds.
- Confidence/selective prediction, job
  `ba92d5f9-fc26-44fe-83f6-30e033aa7f93`: 40 candidates, six packed chunks, six
  available sources, and three represented sources. Selected evidence was Selective LLM
  Prediction `[S5]` chunks 25, 5, and 3; the clinical self-confidence study `[S6]`
  chunks 18 and 49; and the clinical extraction methods paper `[S4]` chunk 52. Before:
  two findings and one omitted uncited claim. After: four findings covering poor clinical
  self-confidence calibration, abstention/risk control, multidimensional clinical
  evaluation, and coverage-accuracy inversion; no claim was rejected. Topic embedding
  took 2.849 seconds, inferred non-generation overhead 2.900 seconds, generation 8.014
  seconds, synthesis 10.913 seconds, and host-observed HTTP latency 10.448 seconds.

Citation and mutation audit:

- A read-only diagnostic used the deployed `ResearchOrchestrator`,
  `ScopedRetrievalService.retrieve()`, and `pack_evidence()` against each retained job to
  reproduce the exact source IDs, document IDs, chunk indexes, scores, and sanitized text
  supplied by the retry path. It performed topic-query embeddings only; it did not embed
  retained documents, generate reports, crawl, or upsert.
- All 11 final material citation references across the binary, fact-extraction, and
  confidence reports resolve to the cited source ID and exact selected retained document
  and chunk. Manual claim-to-chunk review found each final claim supported by that supplied
  text. Final material citation validity was therefore 11/11 (`100%`). The authoritative
  report had no material citation to validate because it had no supported finding.
- `docker compose logs --since '2026-08-10T23:25:00Z' research-worker` filtered for
  `job_started`, `phase_completed`, `crawl_completed`, `upsert`, and `embed` returned
  `NO_INGESTION_ACTIVITY_MATCHES`. Job source/chunk counts remained 6/870, 6/1700,
  4/1047, and 6/1582. No new job, crawl, retained-document re-embedding, Qdrant upsert,
  or corpus mutation occurred.

Acceptance and risks:

- The live authoritative-source acceptance criterion failed: relevant chunks from three
  retained authoritative sources reached generation, but the model emitted no supported
  cited finding. Phase 4 must not be accepted despite the deterministic gate and the other
  three improved reports.
- Ollama live logs showed `n_ctx_slot = 4096` while the application packs against
  `MODEL_CONTEXT_TOKENS=8192`. The selected prompts were below the observed live slot, so
  no truncation was reported, but this configuration mismatch remains a deployment risk.
- Dense retrieval found the specialized guideline terms and relevant late chunks in this
  live check; this run did not demonstrate the exact/specialized-term misses required to
  justify Phase 5. Phase 5 also lacks separate explicit owner approval.
- Public report, `/query`, `/rag`, and OpenAI-compatible contracts were unchanged. No LLM
  judge, BM25, reranking, MMR, query decomposition, GraphRAG, map-reduce, dependency, or
  production-code change was introduced.

Final verification:

- `docker compose run --rm --no-deps
  -e TEST_REDIS_URL=redis://hub-redis:6379/15
  -v "${PWD}\research-hub:/app" research-hub
  python -m unittest discover -s tests -v`: 79 tests passed in 2.595 seconds with no
  failures or skips; all four Compose Redis worker integration tests ran and passed.
- `docker compose run --rm --no-deps research-hub python -m pip check`:
  `No broken requirements found.`
- `hermes verify --json`: exit code 0 with `"ok": true`; the Compose build passed and
  readiness returned HTTP 200.
- `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff
  --check`: exit code 0 with no whitespace error. Git emitted only the expected warning
  that the progress file's working-copy LF endings will be converted to CRLF when touched.

Files changed:

- `PRDs/research-rag-synthesis-modernization-progress.md`: recorded deployment approval,
  the four one-time retry outcomes, selected retained evidence, claim/citation audit,
  timings, acceptance failure, and risks.

Next action: stop at the Phase 4 review gate. Diagnose or change authoritative generation
only with a new, explicit instruction; do not retry the retained jobs again implicitly and
do not begin Phase 5.

## 2026-08-10 - Phase 4 authoritative citation-correction diagnosis

Status: a deterministic repository defect in authoritative correction was reproduced and
fixed with one focused test. The deployed authoritative report was not retried because the
new approval field was not explicitly completed `YES`. Phase 4 remains unaccepted and
Phase 5 was not started.

Pre-edit checks and reproduced baseline:

- The entire PRD and this progress log were read before editing. No repository-local
  `AGENTS.md` was present.
- `git status --short --branch` reported `## main...origin/main` with no changes. Local
  `HEAD` and its configured upstream both resolved to
  `835e38edf6ad1875f8e053102f60eaac3c529d66` on `main`.
- `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app" research-hub
  python -m tests.benchmark_report_retrieval` exited 0. Critical Recall@4 was `1.0`,
  citation validity was `1.0`, and `passed` was `true`.
- `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app" research-hub
  python -m unittest tests.test_report_retrieval_benchmark -v` passed both tests in
  1.470 seconds.
- All callers of report generation, shared packing, validation, and Ollama generation were
  traced before editing. Report generation is called only by the retry endpoint and the
  post-ingestion report step; this change touches only their shared synthesis prompt.

Deterministic diagnosis:

- The authoritative retry supplied only `[S4]`, `[S5]`, and `[S6]`, but the initial prompt
  instructed the model with examples `[S1]` and `[S1][S2]`. After the first uncited claim
  was rejected, the corrective prompt again used unavailable `[S1]` as its only literal
  example. Strict validation correctly rejected every source outside the represented set.
  The correction instruction and validator were therefore internally contradictory for
  this retained-evidence selection.
- Existing Ollama logs for the two authoritative attempts showed 1,987 and 2,038 prompt
  tokens, 174 and 239 generated tokens, `n_ctx_slot = 4096`, and `truncated = 0` for both
  calls. The configured 8,192/allocated 4,096 mismatch did not truncate this retry and was
  not its cause, though it remains a deployment risk.
- The deployed Ollama version is `0.32.6`. A synthetic, non-report probe sent the same
  `think: false` request option with an anchored JSON-schema pattern requiring `[S4]`.
  Qwen 3.5 ignored the schema, returned the requested uncited value plus extra prose, and
  consumed the 128-token output limit. The `/api/chat` endpoint behaved the same way.
  Omitting `think` or requesting low thinking consumed the entire 1,024-token production
  budget in 5,322 characters of thinking and returned an empty response with
  `done_reason: length`. This matches the open Ollama qwen3.5 structured-output defect
  documented at https://github.com/ollama/ollama/issues/14645. Toggling thinking or relying
  on a stronger schema was therefore not a safe repository correction.

TDD and files changed:

- `research-hub/tests/test_synthesis.py`: added
  `test_correction_names_only_represented_citation_ids`. Its fixture represents only
  `[S4]`, makes the first generation uncited, and corrects only when the retry names the
  actual allowed citation. Before the production edit it failed because the final report
  contained no supported finding and omitted the uncited claim. After the edit it passes
  and also proves neither attempt advertises unavailable `[S1]`.
- `research-hub/app/synthesis.py`: removed hard-coded source-number examples from the
  initial prompt and made the existing one correction attempt list only the sorted source
  IDs represented in packed evidence. Citation validation, sanitization, packing, two-call
  limit, omission behavior, persistence, and public APIs are unchanged.
- `PRDs/research-rag-synthesis-modernization-progress.md`: records this diagnosis, tests,
  verification, acceptance disposition, and remaining risks.
- The first focused command, `docker compose run --rm --no-deps
  -v "${PWD}\research-hub:/app" research-hub python -m unittest
  tests.test_synthesis.SynthesisTests.test_correction_names_only_represented_citation_ids
  -v`, failed for the intended reason. The same command passed after the two-line prompt
  behavior change. The complete focused synthesis module then passed 11 tests in 0.623
  seconds.

Final automated verification:

- The deterministic benchmark remained byte-stable with critical Recall@4 `1.0`, citation
  validity `1.0`, and `passed: true` after the fix. Its two focused tests passed in 1.681
  seconds.
- `docker compose build research-hub research-worker`: exit code 0; both images built.
- `docker compose run --rm --no-deps
  -e TEST_REDIS_URL=redis://hub-redis:6379/15
  -v "${PWD}\research-hub:/app" research-hub
  python -m unittest discover -s tests -v`: 80 tests passed in 2.639 seconds with no
  failures or skips; all four Compose Redis worker integration tests ran and passed.
- `docker compose run --rm --no-deps research-hub python -m pip check` reported
  `No broken requirements found.`
- The exact `hermes verify --json` command was attempted twice and its wrapper timed out
  after 304 seconds both times without output. The isolated Hermes build phase returned
  `ok: true`; direct `/` and `/readyz` checks both returned HTTP 200 with Ollama, Qdrant,
  Redis, SearXNG, and Crawl4AI healthy. The saved ignored Hermes recipe has `port: null`.
  `hermes verify --json --port 8000 --timeout 120 --ready-timeout 60` completed in 1.6
  seconds with `"ok": true`, a successful Compose build, and readiness HTTP 200. A
  temporary attempt to supply port 8000 through the ignored manifest was restored; no
  local manifest change remains. The requirement is satisfied with the explicit discovered
  port, while the exact no-port wrapper hang remains a Hermes tooling risk.

Acceptance and gate disposition:

- The deterministic failure mechanism and smallest correction are test-covered. Strict
  citation validation still requires every material finding or disagreement to cite exact
  represented evidence; no finding was injected, fabricated, or hard-coded.
- No crawl, research job, retained-document embedding, Qdrant upsert, corpus mutation,
  binary/fact/confidence retry, or authoritative retry occurred. The synthetic Ollama
  probes used no retained evidence and persisted no report.
- The new deployment approval field was blank, so it was treated as not approved. Live
  acceptance remains deferred: only job `4b8acd0f-088f-4b97-92fc-f52b69b8a3ee` may be
  retried after an explicit `YES`.
- Phase 4 remains unaccepted until an approved authoritative retry produces supported cited
  findings from at least two supplied retained sources and every displayed citation resolves
  to the exact selected evidence. Dense retrieval already found the specialized evidence,
  so Phase 5 remains both unapproved and unjustified.

## 2026-08-11 - Phase 4 authoritative retry gate continuation

Status: stopped at the Phase 4 review gate. The deployment approval field remained the
literal `[YES/NO must be explicitly completed by the owner]` placeholder, so it was
treated as `NO`. The authoritative report was not retried, Phase 4 remains unaccepted,
and Phase 5 was not started.

Pre-action checks:

- The entire PRD and this progress log were read before repository inspection. No
  repository-local `AGENTS.md` was present.
- `git status --short --branch` reported `## main...origin/main` with no changes.
  `git branch --show-current` reported `main`; `git rev-parse HEAD` and
  `git rev-parse '@{upstream}'` both reported
  `551f672638ace9ea3076e17d53db10d6a52fa174`; the configured upstream was
  `origin/main`. The commands used the Hermes-managed Git executable because `git` was
  not resolvable from the sandboxed PowerShell process.
- No existing worktree change was reset, overwritten, or removed.

Automated Phase 4 reproduction:

- `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app" research-hub
  python -m tests.benchmark_report_retrieval`: exit code 0. Aggregate Recall@4 and
  critical Recall@4 were `1.0`, Precision@4 was `0.5`, reciprocal rank and source
  coverage were `1.0`, duplicate rate was `0.0`, citation validity was `1.0`,
  unsupported-claim rejection count was `4`, and `passed` was `true`.
- `docker compose run --rm --no-deps -v "${PWD}\research-hub:/app" research-hub
  python -m unittest tests.test_report_retrieval_benchmark tests.test_synthesis -v`:
  13 tests passed in 2.014 seconds. This included both benchmark failure-gate tests and
  `test_correction_names_only_represented_citation_ids`.

Deployed-code provenance:

- The running `research-hub` API and `research-worker` were healthy/running. SHA-256
  manifests for all 15 top-level `/app/app/*.py` files in each container exactly matched
  the clean local `551f672` checkout. In particular, all three copies of `synthesis.py`
  had SHA-256
  `0b3ab7189b8b49eda5ebff7c9538ae5b7d9dc41286cae4d2e446f5d53869b4ea`.
  The containers do not embed Git metadata, so the complete source manifest was used to
  verify deployed revision content.

Final verification:

- `docker compose build research-hub research-worker`: exit code 0; both current-commit
  images built successfully from cache. Running containers were not recreated.
- `docker compose run --rm --no-deps
  -e TEST_REDIS_URL=redis://hub-redis:6379/15
  -v "${PWD}\research-hub:/app" research-hub
  python -m unittest discover -s tests -v`: 80 tests passed in 2.818 seconds with no
  failures or skips; all four Compose Redis worker integration tests ran and passed.
- `docker compose run --rm --no-deps research-hub python -m pip check` reported
  `No broken requirements found.`
- The known `port: null` no-port Hermes path was not repeated. The explicit command
  `hermes verify --json --port 8000 --timeout 120 --ready-timeout 60` exited 0 in 2.4
  seconds with `"ok": true`; its Compose build passed and readiness returned HTTP 200.
- `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff
  --check`: exit code 0 with no whitespace errors. Git emitted only the expected warning
  that this progress file's working-copy LF endings will be converted to CRLF when touched.

Approval, mutation, acceptance, and risks:

- `POST /research/4b8acd0f-088f-4b97-92fc-f52b69b8a3ee/report/retry` was not called.
  The binary, fact-extraction, and confidence reports were also not retried. No report
  was captured because capture and retry evidence collection are conditional on explicit
  `YES` approval.
- No research job, crawl, retained-document embedding, Qdrant upsert, corpus mutation,
  or deployed report-state mutation was invoked. The benchmark and tests use deterministic
  fixtures/mocks; build and verification did not recreate the running containers.
- Deterministic critical Recall@4 remains `1.0` and citation validity remains `100%`, but
  the required post-correction authoritative live result is deferred. Phase 4 cannot be
  accepted until one explicitly approved retry produces supported cited findings from at
  least two supplied retained sources and every displayed material citation resolves to
  exact selected retained evidence.
- The 4,096-token deployed Ollama context allocation versus the configured 8,192-token
  packing budget remains an operational risk. The synthetic fixture set still does not
  establish broader healthcare retrieval quality, and dense retrieval has not shown the
  miss required to justify Phase 5.

Files changed:

- `PRDs/research-rag-synthesis-modernization-progress.md`: records the unapproved retry
  disposition, deployed source provenance, repeated deterministic gates, full release
  verification, deferred live evidence, and remaining risks.

Next action: stop at the Phase 4 review gate. Retry only authoritative job
`4b8acd0f-088f-4b97-92fc-f52b69b8a3ee`, exactly once, after the owner explicitly enters
`YES`; do not begin Phase 5.

## 2026-08-11 - Phase 4 approved corrected authoritative retry

Status: the owner explicitly approved one retry of authoritative job
`4b8acd0f-088f-4b97-92fc-f52b69b8a3ee`. The corrected deployed code was used and the
endpoint was called exactly once. The retry again produced no supported cited finding,
so Phase 4 remains unaccepted and Phase 5 was not started.

Approval and pre-retry state:

- The owner replied: `YES deploy/use the corrected code and retry authoritative job
  4b8acd0f-088f-4b97-92fc-f52b69b8a3ee exactly once.` This approved no other report
  retry and did not approve Phase 5.
- The entire PRD and progress log had been read. `git status --short --branch` reported
  `## main...origin/main` with no changes. Local `HEAD` and its configured upstream both
  resolved to `85f3efd21acdaea392229e98f9563ed64c18d40d`; correction commit
  `551f672638ace9ea3076e17d53db10d6a52fa174` was its immediate ancestor.
- SHA-256 manifests for all 15 top-level application Python files in both running
  containers matched the clean checkout. Local, API, and worker `synthesis.py` all had
  SHA-256 `0b3ab7189b8b49eda5ebff7c9538ae5b7d9dc41286cae4d2e446f5d53869b4ea`.
- The pre-retry report was completed at attempt 2 with no supported findings, no source
  disagreements, and three omitted uncited material claims. Its six-source registry was
  unchanged. The job reported six sources and 870 chunks.
- Before the retry, SQLite contained 67 documents, 67 `job_sources` rows, and six source
  associations for this job. Qdrant was green with 24,465 points, 23,369 indexed vectors,
  and six segments.
- Immediately before the retry, the deterministic benchmark again exited 0 with critical
  Recall@4 `1.0`, citation validity `1.0`, and `passed: true`. The combined benchmark and
  synthesis command passed 13 tests in 1.982 seconds, including
  `test_correction_names_only_represented_citation_ids`.

Single approved retry:

- At `2026-08-11T07:13:38.0077511Z`, exactly one
  `POST /research/4b8acd0f-088f-4b97-92fc-f52b69b8a3ee/report/retry` was issued. It
  returned HTTP 200; the host stopwatch measured 16.7342 seconds. No timeout or second
  POST occurred.
- The persisted report advanced from attempt 2 to attempt 3 and completed at
  `2026-08-11T07:13:54.050475Z`. It still contains no supported material finding and no
  material source disagreement. Its one substantive unknown says the supplied evidence
  does not make the operational validation differences between TRIPOD-ML and the broader
  STARD-AI/TRIPOD-AI guidance explicit. Four final uncited material claims were omitted.
- The runtime synthesis event reported outcome `claims_rejected`, 40 retrieval candidates,
  six selected chunks, six available sources, three represented sources, five total
  uncited rejections, zero invalid-source rejections, and `no_supported_findings: true`.
  The five total comprise the first call's validation rejection plus four final claims
  omitted after the correction call.

Exact represented evidence:

- The represented citation IDs were exactly `[S4]`, `[S5]`, and `[S6]`. A read-only
  diagnostic reran the deployed `ScopedRetrievalService.retrieve()` and `pack_evidence()`
  path to reproduce the selected context. It made one additional topic query embedding
  only; it did not generate a report, embed retained documents, crawl, or upsert.
- `[S5]`, document `52f965f2-3be9-5484-8929-46a253f5752f`, canonical URL
  `https://www.nature.com/articles/s41591-022-01772-9`, chunk 46, score `0.847417`:

  > in live clinical settings (that is, the supported decisions have an actual effect on patient care). Whereas TRIPOD-AI, STARD-AI, SPIRIT-AI and CONSORT-AI are specific to particular study designs, DECIDE-AI is focused on the evaluation stage and does not prescribe a fixed study design. ### Box 1 Methodological challenges of the AI-based decision support system evaluation
  > The clinical evaluation of AI-based decision support systems presents several methodological challenges, all of which will likely be encountered at early stage.

- `[S6]`, document `49bf662c-cb09-596b-9a2e-d7a970287e9a`, canonical URL
  `https://www.nature.com/articles/s43856-024-00492-0`, chunk 59, score `0.8197498`:

  > ing STARD-AI[15](https://www.nature.com/articles/s43856-024-00492-0#ref-CR15 "Sounderajah, V. et al. Developing a reporting guideline for artificial intelligence-centred diagnostic test accuracy studies: the STARD-AI protocol. BMJ Open. 11, e047709 \(2021\)."), TRIPOD-AI[16](https://www.nature.com/articles/s43856-024-00492-0#ref-CR16 "Collins, G. S. et al. Protocol for development of a reporting guideline \(TRIPOD-AI\) and risk of bias tool \(PROBAST-AI\) for diagnostic and prognostic prediction model studies based on artificial intelligence. BMJ Open. 11, e048008 \(2021\)."), and PRISMA-AI[17](https://www.nature.com/articles/s43856-024-00492-0#ref-CR17 "Cacciamani, G. E. et al. PRISMA AI reporting guidelines for systematic reviews and meta-analyses on AI in healthcare. Nat. Med. 29, 14–15 \(2023\).").

- `[S4]`, document `b80b2ef1-eb1b-5d4e-a399-3c471034a506`, canonical URL
  `https://www.nature.com/articles/s41591-020-1034-x`, chunk 95, score `0.80571586`:

  > t consideration (Supplementary Table [2](https://www.nature.com/articles/s41591-020-1034-x#MOESM1)). This extension is aimed particularly at investigators and readers reporting or appraising clinical trials; however, it may also serve as useful guidance for developers of AI interventions in earlier validation stages of an AI system. Investigators seeking to report studies developing and validating the diagnostic and predictive properties of AI models should refer to TRIPOD-ML (Transparent Reporting of a Multivariable Prediction Model for Individual Prognosis or Diagnosis–Machine Learning) and STARD-AI (Standards for Reporting Diagnostic Accuracy Studies–Artificial Intelligence), both of which are currently under development[32](https://www.nature.com/articles/s41591-020-1034-x#ref-CR32 "Collins, G. S. & Moons, K. G. M. Reporting of artificial intelligence prediction models.

- `[S4]`, the same retained document and canonical URL, chunk 38, score `0.80567974`:

  > https://www.nature.com/articles/s41591-020-1034-x#ref-CR35 "CONSORT-AI and SPIRIT-AI Steering Group. Reporting guidelines for clinical trials evaluating artificial intelligence interventions are needed. Nat. Med. 25, 1467–1468 \(2019\).")), and the two guidelines were registered as reporting guidelines under development on the EQUATOR library of reporting guidelines in May 2019. Both guidelines were developed in accordance with the EQUATOR Network’s methodological framework[37](https://www.nature.com/articles/s41591-020-1034-x#ref-CR37 "Moher, D., Schulz, K. F., Simera, I. & Altman, D. G. Guidance for developers of health research reporting guidelines. PLoS Med. 7, e1000217 \(2010\)."). The SPIRIT-AI and CONSORT-AI Steering Group, consisting of 15 international experts, was formed to oversee the conduct and methodology of the study.

- `[S4]`, the same retained document and canonical URL, chunk 97, score `0.80489826`:

  > on reporting of evaluation studies in Health Informatics. Int. J. Med. Inform. 78, 1–9 \(2009\)."). The CONSORT-AI extension is expected to encourage careful early planning of AI interventions for clinical trials and this, in conjunction with SPIRIT-AI, should help to improve the quality of trials for AI interventions. The development of the CONSORT-AI guidance does not include additional items within the discussion section of trial reports. The guidance provided by CONSORT 2010 on trial limitations, generalizability and interpretation were deemed to be translatable to trials for AI interventions. There is also recognition that AI is a rapidly evolving field, and there will be the need to update CONSORT-AI as the technology, and newer applications for it, develop.

- `[S6]`, document `49bf662c-cb09-596b-9a2e-d7a970287e9a`, canonical URL
  `https://www.nature.com/articles/s43856-024-00492-0`, chunk 110, score `0.7941949`:

  > =Moy%2CL&author=Kahn%2CCE)
  > 38. Liu, X., Rivera, S. C., Moher, D., Calvert, M. J. & Denniston, A. K. SPIRIT-AI and CONSORT-AI Working Group Reporting guidelines for clinical trial reports for interventions involving artificial intelligence: the CONSORT-AI Extension. _BMJ._ **370** , m3164 (2020).

- All six supplied entries had an empty sanitization security-label list. The exact
  generation context used untrusted-evidence delimiters and the stable retained document
  IDs above; no unsanitized retained Markdown was supplied.

Generation calls and timings:

- One query embedding used 36 prompt tokens in an Ollama 2,048-token embedding context,
  took 2.5727 seconds, and reported `truncated = 0`. The following scoped Qdrant search
  returned in approximately 0.0292 seconds based on adjacent application response times.
- Generation call 1 used 1,982 prompt tokens and generated 192 tokens. Ollama reported
  a 4,096-token slot, `truncated = 0`, and HTTP processing time 12.2039 seconds. Its
  response caused one uncited-claim validation rejection and the bounded correction call.
- Generation call 2 used 2,043 prompt tokens and generated 243 tokens. Ollama reported
  the same 4,096-token slot, `truncated = 0`, and HTTP processing time 3.0116 seconds.
  The correction named only `[S4]`, `[S5]`, and `[S6]`; four remaining uncited material
  claims were omitted.
- Total Ollama generation HTTP processing time was 15.2154 seconds. Application synthesis
  took 17.8398 seconds and middleware measured the retry request at 17.8472 seconds; the
  independent host stopwatch measured 16.7342 seconds. The timing-source discrepancy is
  retained rather than normalized away.
- The application packed against configured `MODEL_CONTEXT_TOKENS=8192` with a 1,024-token
  answer reserve, while Ollama allocated 4,096 tokens. Both prompts were below the live
  allocation and neither was truncated.
- The exact initial prompt was deterministically reproduced with the six entries above.
  Raw JSON bodies returned by the two generation calls are neither persisted nor logged,
  so they cannot be recovered after this one-time retry without issuing a forbidden second
  request. The audit therefore records both calls' token/timing data, validator outcomes,
  final persisted content, and the exact represented input, but not the raw rejected JSON.

Citation and mutation audit:

- The final report displays no material finding or disagreement citation. There is
  therefore no displayed material citation to resolve; the six-item source registry still
  resolves to its retained documents, but it is not evidence of a supported finding.
- Before and after the retry, the job remained at six sources and 870 chunks; SQLite
  remained at 67 documents, 67 job/source associations, and six associations for this job;
  Qdrant remained at 24,465 points, 23,369 indexed vectors, and six segments.
- Bounded API logs show one topic `/api/embeddings` request, one Qdrant `points/search`,
  two Ollama `/api/generate` requests, and the single approved retry endpoint. Worker logs
  since the pre-retry boundary contain no `job_started`, `phase_completed`,
  `crawl_completed`, `ingest`, `embed`, or `upsert` event.
- No research job, crawl, retained-document embedding, Qdrant upsert, or corpus mutation
  occurred. The expected report persistence and job `updated_at`/`report_status` update
  were the only deployed state changes. The binary, fact-extraction, and confidence
  reports were not retried.

Post-retry verification:

- The deterministic benchmark again exited 0 with critical Recall@4 `1.0`, citation
  validity `1.0`, and `passed: true`. The combined benchmark/synthesis command passed 13
  tests in 1.987 seconds.
- The complete containerized suite against Compose Redis ran 80 tests in 2.696 seconds
  with no failures or skips; all four Redis worker integration tests ran and passed.
- Containerized `python -m pip check` reported `No broken requirements found.`
- `hermes verify --json --port 8000 --timeout 120 --ready-timeout 60` exited 0 with
  `"ok": true`; its Compose build passed and readiness returned HTTP 200.
- The CRLF-aware `git diff --check` exited 0 with no whitespace errors. Git emitted only
  the expected warning that this file's working-copy LF endings will convert to CRLF when
  touched.

Acceptance, deferred evidence, and risks:

- Phase 4 live acceptance failed. Although dense retrieval again supplied relevant chunks
  from three retained sources, the report contains supported cited findings from zero
  sources rather than the required minimum of two. Phase 4 remains unaccepted.
- Deterministic citation validity remains `100%`, but the live report emitted zero material
  citations, so it cannot demonstrate live claim-to-evidence citation validity.
- Raw per-call generation JSON remains an observability gap because the service does not
  persist or log rejected model bodies. No additional retry is authorized to fill it.
- The 8,192 configured versus 4,096 allocated context mismatch remains an operational
  risk, although it caused no truncation here. Dense retrieval continued to find the
  specialized terms, so Phase 5 remains unapproved and unjustified.

Files changed:

- `PRDs/research-rag-synthesis-modernization-progress.md`: records approval, the one-time
  retry, exact supplied evidence, model and HTTP diagnostics, mutation audit, failed live
  acceptance, verification, deferred raw outputs, and remaining risks.

Next action: stop at the Phase 4 review gate. Do not retry this or any other report again
without new explicit approval, and do not begin Phase 5.
