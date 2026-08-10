# Research RAG Synthesis Modernization Implementation Plan

> **Executor:** Codex CLI using GPT-5.6 Sol with reasoning effort set to `high`.
>
> **Execution state:** Planning only. Do not implement from this document until explicitly instructed.
>
> **For Codex:** Read this entire PRD, repository instructions, and every referenced file before editing. Execute phase-by-phase with TDD. Do not broaden scope, launch research jobs, commit, push, or advance to a later phase without satisfying the current phase gate and receiving the requested review.

**Goal:** Convert Research Hub report generation from document-prefix truncation into auditable, retrieval-based evidence synthesis while preserving durable reports, exact source provenance, local-only inference, citation validation, and backward-compatible APIs.

**Architecture:** Establish immutable job-to-source observations, introduce a shared scoped retrieval layer, and make report synthesis select relevant sanitized chunks from each job’s retained evidence instead of reading the beginning of each document. Add deterministic retrieval/generation diagnostics first; defer hybrid retrieval, map-reduce synthesis, and agentic discovery until measured baseline gates justify them.

**Tech stack:** Python 3.12, FastAPI, Ollama (`qwen3.5:9b`, `nomic-embed-text`), Qdrant, SQLite, Redis, `unittest`, Docker Compose, Prometheus.

---

## 1. Why this work exists

The research pipeline successfully completed an initial healthcare LLM evaluation wave:

- 6/6 jobs completed.
- 6/6 reports completed.
- 34 documents and 9,698 chunks were indexed.
- Cross-corpus RAG retrieved relevant evidence.
- Five of six persisted reports produced no supported material findings.

A targeted follow-up job then retained six authoritative sources, including EQUATOR, CONSORT-AI, TRIPOD+AI, DECIDE-AI, and Nature publications. It indexed 870 chunks but again generated no supported findings.

The root cause is in `research-hub/app/synthesis.py`: report evidence is assembled from `doc["markdown"][:excerpt_chars]`. Relevant sections deeper in long pages never reach the generation model. This is prefix truncation, not retrieval-augmented synthesis.

The current `/rag` flow performs better because `research-hub/app/query.py` embeds a question, retrieves relevant Qdrant chunks, packs complete bounded evidence entries, and passes exactly those entries to Ollama.

This PRD makes report synthesis use the same architectural principle without coupling reports directly to the public query transport.

## 2. Product goals

### G1 — Relevant evidence selection

Reports must synthesize chunks selected for relevance to the research topic from the full retained source content, including evidence located late in long documents.

### G2 — Stable and auditable provenance

Every material claim must retain a stable citation to an exact retained document. Retrying synthesis must not crawl, embed source documents again, or mutate the corpus.

### G3 — Correct cross-job ownership

A document reused by multiple research jobs must remain observable by every job that used it. A later unchanged crawl must not remove an earlier job’s report evidence.

### G4 — Explicit failure diagnosis

The system must distinguish:

- no relevant evidence retrieved;
- evidence retrieved but no supported claims generated;
- citation validation rejection;
- generation or dependency failure.

### G5 — Measurable RAG quality

Add deterministic fixtures and metrics that separate retrieval quality from generation quality. Do not rely exclusively on an LLM judge.

### G6 — Controlled evolution

Deliver the minimum safe retrieval-based report path first. Hybrid retrieval, reranking, map-reduce, query decomposition, and GraphRAG are later gated phases—not prerequisites hidden inside Phase 1.

## 3. Non-goals

The executor must not implement these during the initial delivery:

- Knowledge graphs or GraphRAG.
- Autonomous query decomposition or evidence-gap follow-up.
- New search providers.
- Cloud-hosted rerankers or evaluator models.
- DeepEval or Microsoft Agent Framework integration.
- Changes to the public `/query`, `/rag`, or OpenAI-compatible response contracts.
- A new UI.
- Corpus-wide re-embedding unless a test proves it is required.
- Automatic launching of Wave 2 or any additional research seed.
- Claims that the resulting reports are clinically validated.
- Unrelated API authentication, crawler SSRF, backup, or container-hardening backlog items.

These are valid roadmap concerns but must remain separate changes.

## 4. Current architecture and constraints

### 4.1 Current research flow

```text
POST /research
  → Redis durable queue
  → SearXNG search
  → deterministic source policy
  → Crawl4AI Markdown
  → SQLite canonical document retention
  → paragraph-aware chunking
  → Ollama embeddings
  → Qdrant chunks
  → report synthesis from document prefixes
```

### 4.2 Current query flow

```text
POST /rag or corpus chat
  → embed query
  → Qdrant dense-vector top-k
  → complete-entry token packing
  → bounded untrusted evidence prompt
  → Ollama generation
  → exact ordered source list
```

### 4.3 Current persistence problem

`research-hub/app/document_store.py` stores one mutable `documents.job_id`. On an unchanged recrawl, `save()` updates that field to the latest job. Consequently:

- `documents_for_job(old_job_id)` can lose evidence;
- report retry for an earlier job can become incomplete;
- Qdrant payload can retain stale topic/job metadata;
- filtering report retrieval solely by Qdrant `job_id` is unsafe.

The report modernization must first introduce a many-to-many job/source observation model.

### 4.4 Runtime constraints

- One RTX 3080 Ti with 12 GB VRAM.
- Generation and embeddings share Ollama/GPU capacity.
- Default model context is configured as 8,192 tokens, but runtime context compatibility must remain separately verified.
- The worker processes one claimed ingestion job at a time.
- Reports can already be retried independently through `POST /research/{job_id}/report/retry`.
- The application has no additional retrieval dependencies beyond Qdrant.
- Public API schemas reject unknown fields.

## 5. Target architecture

### 5.1 Near-term target implemented by this PRD

```text
Completed research job
    ↓
Immutable job_sources observations
    ↓
Stable source registry from SQLite
    ↓
Embed research topic once
    ↓
Scoped Qdrant retrieval across retained canonical URLs
    ↓
Relevance ordering + per-source cap + complete-entry token packing
    ↓
Sanitized evidence entries mapped to stable [S#] source IDs
    ↓
Structured findings/disagreements/unknowns generation
    ↓
Citation validation and persisted report diagnostics
```

### 5.2 Later gated target

```text
Research objective
    ↓
Planner: subquestions and targeted searches
    ↓
Source policy: authority/date/domain/document type
    ↓
Canonical evidence + immutable observations
    ↓
Context-aware chunks
    ↓
Dense vector + lexical/BM25 candidates
    ↓
Fusion + local reranking + calibrated threshold
    ↓
Map: supported source-level claims
    ↓
Reduce: findings/disagreements/unknowns
    ↓
Claim-to-evidence verification
    ↓
Evaluation and evidence-gap follow-up
```

The later target is included for compatibility planning. Do not implement it during the minimum initial phase.

## 6. Design decisions

### D1 — Shared retrieval domain service

Create `research-hub/app/retrieval.py`. It must contain transport-neutral retrieval and context-selection behavior used by report synthesis. Do not call FastAPI endpoints internally and do not duplicate `QueryEngine` request models.

Proposed internal types:

```python
@dataclass(frozen=True)
class EvidenceCandidate:
    text: str
    canonical_url: str
    source_title: str
    document_id: str
    chunk_index: int
    score: float
    metadata: dict[str, Any]

@dataclass(frozen=True)
class RetrievalDiagnostics:
    candidates_considered: int
    chunks_selected: int
    sources_available: int
    sources_represented: int
    min_selected_score: float | None
    max_selected_score: float | None

@dataclass(frozen=True)
class RetrievedEvidence:
    candidates: list[EvidenceCandidate]
    diagnostics: RetrievalDiagnostics
```

Codex may adjust names after inspecting neighboring style, but these responsibilities must remain explicit.

### D2 — Retrieve by canonical source scope, not mutable job payload

Use SQLite `job_sources` to obtain the job’s document IDs and canonical URLs. Qdrant retrieval must filter by the retained canonical URLs or document IDs, not by Qdrant `job_id` alone.

### D3 — One topic embedding per report attempt

Embed the research topic once. Reuse that vector for scoped Qdrant searches. Report retry may create this query embedding but must not re-embed source chunks.

### D4 — Diversity without forced irrelevant evidence

Retrieve a larger candidate pool than the final context. Apply:

- stable descending relevance order;
- deterministic tie-breaking;
- configurable maximum chunks per source;
- exact duplicate removal;
- complete-entry token packing;
- no forced inclusion of a source below the eventual relevance policy.

The stable source registry may include a source not represented in the final evidence prompt. Citations may reference only evidence actually supplied to the model.

### D5 — No magic threshold before evaluation

Add score diagnostics and a configurable optional threshold. Default behavior should preserve candidates until a golden evaluation set establishes a justified threshold. Do not invent `0.7`, `0.5`, or another production cutoff from the observed sample.

### D6 — Preserve exact retained evidence

The exact Markdown remains in SQLite. Derived chunks remain sanitized before model use. Citation/source output must reference retained document identity and canonical URL.

### D7 — Backward-compatible report contract

Do not remove or rename existing `ResearchReport` fields. Retrieval diagnostics may initially be logged/metric-only. If persistence is necessary, add a nullable JSON diagnostics column with an in-place SQLite migration and keep public serialization unchanged unless explicitly approved.

### D8 — Retrieval-based synthesis before map-reduce

First prove that scoped retrieval fixes the observed failure. Map-reduce adds generation calls, latency, intermediate-state design, and new failure semantics. It must be a later phase gated by evaluation results.

## 7. Acceptance criteria

### 7.1 Functional acceptance

1. A test document with relevant evidence beyond a long irrelevant prefix is retrieved and supplied to report generation.
2. Report retry uses existing retained documents and Qdrant vectors; it does not invoke SearXNG, Crawl4AI, source chunk embedding, or Qdrant upsert.
3. A source reused by two jobs remains available to both jobs after the second observation.
4. The source registry remains stable and deterministic for a job.
5. Every material finding and disagreement contains at least one valid `[S#]` citation.
6. Every citation refers to evidence supplied to the generation model, not merely a document present in the registry.
7. Unsupported material claims are rejected or omitted under the existing correction behavior.
8. Empty or below-threshold retrieval produces an explicit insufficient-evidence report state/content, not an uncited answer.
9. Existing report GET and retry API contracts remain compatible.
10. Prompt-injection sanitization and complete-entry token packing remain active.

### 7.2 Deterministic retrieval acceptance

Create a checked-in fixture set containing:

- relevant evidence at the end of a long document;
- multiple chunks from one source;
- two sources with overlapping evidence;
- one irrelevant source;
- one prompt-injection-like span;
- tied retrieval scores;
- a reused document observed by two jobs.

For this fixture set:

- relevant-chunk Recall@k is `1.0` for the critical sentinel cases;
- no exact duplicate chunk appears twice;
- selected chunks never exceed the per-source cap;
- ordering is deterministic across repeated runs;
- token packing includes only complete evidence entries;
- source/citation mappings are stable;
- sanitization removes configured injection patterns from model context.

### 7.3 Generation acceptance

Using mocked deterministic Ollama output:

- report generation persists cited findings;
- invalid citations fail validation;
- uncited claims trigger one correction attempt;
- claims still uncited after correction are omitted and counted;
- retrieval diagnostics distinguish zero candidates from generation rejection;
- retry increments attempts and preserves previous report content until replacement succeeds, matching current lifecycle expectations.

Using the live local model as a manual, non-CI acceptance check:

- retry the authoritative-standards job after deployment;
- confirm the generation prompt contains relevant CONSORT-AI/TRIPOD+AI/DECIDE-AI chunks rather than page headers alone;
- confirm the report contains supported material findings from at least two retained sources;
- confirm every displayed citation resolves to the corresponding retained document;
- record the result, but do not make a stochastic exact sentence count a unit-test assertion.

### 7.4 Quality baseline acceptance

Create a small version-controlled evaluation manifest with queries, expected document IDs or canonical URLs, and expected sentinel passages. Report at minimum:

- Recall@k;
- Precision@k;
- reciprocal rank or nDCG;
- source coverage;
- duplicate rate;
- citation validity rate;
- unsupported-claim rejection count.

Phase 1 passes only when all critical expected passages are retrieved and citation validity is `100%` on deterministic fixtures. Broader numeric production thresholds are deferred until the evaluation set is representative.

### 7.5 Performance and reliability acceptance

- Topic embedding occurs once per report attempt.
- Report retry performs no crawl and no corpus upsert.
- Retrieval and packing complete within 5 seconds on the current small corpus, excluding Ollama generation, measured locally rather than asserted in unit tests.
- Existing job ingestion semantics remain unchanged.
- Existing reports remain readable after schema migration.
- Migration is idempotent and safe on an existing SQLite database.
- A failed new synthesis attempt preserves a diagnosable failed report state and does not corrupt canonical documents or Qdrant data.

### 7.6 Test and repository acceptance

- New targeted tests pass.
- Full Research Hub test suite passes in the built container.
- `pip check` passes in the built image.
- `hermes verify --json` reports `ok: true`.
- `git diff --check` passes using the repository’s CRLF-aware whitespace configuration where required.
- No secrets, generated evaluation output, SQLite database, or raw research artifact is committed.
- No research seed is launched as part of automated tests.

## 8. Implementation phases

## Phase 0 — Baseline and executable specification

**Objective:** Capture the observed failure as deterministic tests before changing production behavior.

### Task 0.1 — Confirm repository and runtime baseline

**Read:**

- `research-hub/app/synthesis.py`
- `research-hub/app/query.py`
- `research-hub/app/clients.py`
- `research-hub/app/document_store.py`
- `research-hub/app/research.py`
- `research-hub/tests/test_synthesis.py`
- `research-hub/tests/test_context_security_policy.py`
- `research-hub/tests/test_ingestion_idempotency.py`
- `docker-compose.yml`

**Actions:**

1. Run `git status --short --branch`.
2. Preserve user changes; do not reset `.gitignore`.
3. Run the current targeted synthesis tests in the built container.
4. Run the full suite against Compose Redis and record the count and failures.
5. Do not modify code during baseline capture.

**Verified planning baseline:** 58 tests pass with no skips when run in the Linux application image against Compose Redis. The documented host-side `uv run` command currently produces four Windows file-lock cleanup errors for open SQLite/WAL files; it is useful for quick iteration but is not the release gate. The canonical gate mounts the complete `research-hub` tree so `tests/test_cli_health.py` can load `/app/bin/research`.

### Task 0.2 — Add a failing late-evidence synthesis test

**Modify:** `research-hub/tests/test_synthesis.py`

Add a document whose Markdown contains a prefix substantially longer than the old excerpt allocation and a unique relevant sentinel near the end. Mock retrieval dependencies so the intended chunk can be selected deterministically.

The first test must prove the current implementation fails because the sentinel is absent from the generation prompt.

**Test command:**

```text
docker compose run --rm --no-deps \
  -e TEST_REDIS_URL=redis://hub-redis:6379/15 \
  -v "$PWD/research-hub:/app" \
  research-hub \
  python -m unittest tests.test_synthesis.SynthesisTests.test_relevant_evidence_after_long_prefix_is_used -v
```

The command above is Bash syntax for the Hermes terminal. If presenting it for the user to paste into PowerShell, convert line continuations and `$PWD` appropriately; do not silently substitute the host Python environment for the canonical gate.

### Task 0.3 — Add failing cross-job observation tests

**Modify:** `research-hub/tests/test_documents_and_batches.py` or create `research-hub/tests/test_document_observations.py` if that gives clearer ownership.

Prove:

1. Job A observes document version X.
2. Job B later observes unchanged document version X.
3. Both `documents_for_job(A)` and `documents_for_job(B)` return X.
4. Existing databases migrate without losing historical job association where recoverable.

### Phase 0 gate

Stop and review when:

- the late-evidence test fails for the intended reason;
- the cross-job observation test fails for the intended mutable-ownership reason;
- no production behavior has changed.

## Phase 1 — Immutable job/source observations

**Objective:** Make source provenance correct before report retrieval depends on it.

### Task 1.1 — Add `job_sources` schema and migration

**Modify:** `research-hub/app/document_store.py`

Add an idempotent table similar to:

```sql
CREATE TABLE IF NOT EXISTS job_sources (
    job_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    research_metadata TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, document_id)
);
CREATE INDEX IF NOT EXISTS job_sources_document_idx
    ON job_sources(document_id, job_id);
```

Migration requirements:

- Existing `documents.job_id` rows are backfilled into `job_sources` with a deterministic observed timestamp derived from available fields.
- Migration is safe to run on every startup.
- Do not drop `documents.job_id` in this change; preserve backward compatibility and allow a later cleanup migration.

### Task 1.2 — Persist observations without transferring ownership

**Modify:**

- `research-hub/app/document_store.py`
- `research-hub/app/research.py`

Separate immutable document save from job observation. An unchanged document must create or refresh only the `(job_id, document_id)` observation, not transfer exclusive ownership.

Prefer an explicit method such as:

```python
observe_job_source(job_id, document_id, observed_at, research_metadata)
```

Avoid hidden observation side effects that make rebuild behavior ambiguous.

### Task 1.3 — Read documents through the observation join

**Modify:** `research-hub/app/document_store.py`

Change `documents_for_job(job_id)` to join `job_sources` to `documents` and preserve deterministic ordering.

### Task 1.4 — Make tests green

Run targeted observation, ingestion idempotency, and report retry tests. Confirm both jobs retain evidence.

### Phase 1 gate

- Cross-job reuse test passes.
- Existing document rows remain readable.
- No canonical Markdown is duplicated for an unchanged version.
- Report retry for an older job still finds its source after a newer observation.

## Phase 2 — Scoped retrieval domain service

**Objective:** Add reusable evidence retrieval without changing public query behavior.

### Task 2.1 — Expose full Qdrant candidate metadata

**Modify:** `research-hub/app/clients.py`

The internal retrieval service needs document ID, canonical URL, chunk index, score, title, text, and security metadata. Either:

- extend `QdrantClient.search()` without changing current public response mapping; or
- add a new clearly named internal method such as `search_evidence()`.

Do not make synthesis parse `QueryChunk.metadata` indirectly if a typed internal candidate is clearer.

Support filters for a set of canonical URLs or document IDs using Qdrant `MatchAny`. Preserve existing query tests.

### Task 2.2 — Create retrieval types and deterministic selection

**Create:** `research-hub/app/retrieval.py`

Implement:

- scoped candidate retrieval;
- exact duplicate removal;
- stable score ordering with deterministic ties;
- configurable candidate pool;
- configurable per-source maximum;
- optional minimum score;
- diagnostics;
- bounded complete-entry packing or integration with a shared packer.

Do not implement BM25, a reranker, MMR, GraphRAG, or query decomposition in this phase.

### Task 2.3 — Share safe evidence rendering

**Modify:**

- `research-hub/app/query.py`
- `research-hub/app/retrieval.py`
- possibly create `research-hub/app/context.py` if both report and query rendering would otherwise duplicate security-critical logic.

Preserve these invariants:

- complete entries only;
- conservative token accounting;
- explicit untrusted-evidence delimiters;
- exact source list equals supplied evidence;
- no accidental custom system prompt path.

Avoid a broad refactor of query behavior. Extract only stable common primitives proven by tests.

### Task 2.4 — Add configuration

**Modify:**

- `research-hub/app/config.py`
- `docker-compose.yml`
- `.env.example` only if operators are expected to tune the value.

Candidate settings:

- `REPORT_RETRIEVAL_CANDIDATES`
- `REPORT_MAX_CHUNKS_PER_SOURCE`
- `REPORT_MAX_CONTEXT_TOKENS` or reuse the existing context limit deliberately
- `REPORT_RETRIEVAL_MIN_SCORE` as optional/disabled until calibrated

Validate bounds. Do not add knobs that no code uses.

### Task 2.5 — Add retrieval unit tests

**Create:** `research-hub/tests/test_retrieval.py`

Cover:

- scoped URL filtering;
- late sentinel selection;
- duplicate removal;
- deterministic ties;
- per-source cap;
- optional threshold;
- complete-entry budget behavior;
- sanitization;
- diagnostics.

### Phase 2 gate

- Retrieval tests pass deterministically.
- Topic embedding is called once.
- Existing `/query` and `/rag` tests pass unchanged.
- No model generation is needed for retrieval unit tests.

## Phase 3 — Retrieval-based report synthesis

**Objective:** Replace document-prefix evidence construction while preserving report lifecycle and citation validation.

### Task 3.1 — Replace prefix excerpts

**Modify:** `research-hub/app/synthesis.py`

Remove the `doc["markdown"][:excerpt_chars]` evidence path. Use the scoped retrieval service against the job’s observed documents.

Build the source registry first, then map each selected candidate to its registry ID. The prompt must include only selected sanitized candidates and enough source metadata for citation.

### Task 3.2 — Tighten citation-to-context validation

Current validation checks only that `[S#]` is within the source registry range. Add validation that a cited source ID was represented in the actual generation context.

A report may list all observed sources in its Sources section, but a claim may cite only sources whose evidence was supplied.

### Task 3.3 — Preserve correction and retry semantics

Maintain:

- two bounded generation attempts;
- schema-constrained JSON output;
- omission of unsupported claims after correction;
- independent report retry;
- persisted attempt count;
- no ingestion invocation.

Add diagnostics for:

- zero retrieved chunks;
- zero represented sources;
- uncited claim rejection;
- invalid source citation rejection;
- no supported findings after correction.

### Task 3.4 — Add report observability

**Modify:**

- `research-hub/app/observability.py`
- `research-hub/app/synthesis.py`

Add bounded-cardinality metrics/log fields such as:

- report retrieval candidates;
- selected chunks;
- available/represented source counts;
- synthesis outcome (`supported`, `insufficient_evidence`, `claims_rejected`, `failed`);
- report generation duration.

Never use job ID, source URL, topic, or patient-like free text as Prometheus labels.

### Task 3.5 — Make synthesis tests green

Expand `research-hub/tests/test_synthesis.py` to cover:

- late evidence;
- citation to represented source;
- invalid citation;
- no candidates;
- retry without ingestion;
- prompt injection sanitization;
- stable persisted source registry;
- previous correction behavior.

### Phase 3 gate

- All deterministic functional acceptance criteria pass.
- Existing report APIs remain compatible.
- A report retry cannot cite an unrepresented source.
- No document prefixes are used as a fallback unless explicitly approved and tested.

## Phase 4 — Evaluation harness and live regression

**Objective:** Prove that the change fixes the observed class of failure without relying on anecdote.

### Task 4.1 — Add a version-controlled retrieval fixture manifest

**Create:** `research-hub/tests/fixtures/report_retrieval_cases.json`

Each case should include:

- query/topic;
- candidate chunks or a fixture corpus;
- expected document/source identity;
- expected sentinel text;
- critical/noncritical flag.

Use synthetic or public non-PHI content only.

### Task 4.2 — Add deterministic evaluation command

**Create:** `research-hub/tests/benchmark_report_retrieval.py` or a small module under `research-hub/app/` only if production code needs it.

Output machine-readable JSON containing retrieval and citation metrics. Exit nonzero when critical sentinel recall or citation validity gates fail.

Do not use an LLM judge for the primary gate.

### Task 4.3 — Run live report retries manually

After deployment, retry selected existing jobs through:

```text
POST /research/{job_id}/report/retry
```

Use at least:

- the authoritative clinical-AI standards job;
- one binary-classifier statistics job;
- one fact-extraction job;
- one confidence/fairness job with usable retained sources.

Record before/after:

- selected source count;
- selected chunk count;
- supported findings;
- rejected claims;
- valid citations;
- latency.

Do not recrawl solely for this acceptance step.

### Task 4.4 — Run full verification

Required commands, adjusted only to match discovered project conventions:

```text
docker compose build research-hub research-worker
docker compose run --rm --no-deps \
  -e TEST_REDIS_URL=redis://hub-redis:6379/15 \
  -v "$PWD/research-hub:/app" \
  research-hub \
  python -m unittest discover -s tests -v
docker compose run --rm --no-deps research-hub python -m pip check
hermes verify --json
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

The full test gate must execute all Redis integration tests; skips caused by an unreachable `TEST_REDIS_URL` do not satisfy acceptance.

### Phase 4 gate

- Deterministic evaluation gates pass.
- Full suite passes.
- Live authoritative-source report contains supported cited findings.
- No citation resolves outside supplied evidence.
- Verification output is saved in the implementation session summary, not committed as generated noise.

## Phase 5 — Optional hybrid retrieval

**Do not start unless Phase 4 retrieval metrics show dense retrieval misses exact or specialized terms.**

### Required design spike

Compare:

1. SQLite FTS5 over derived chunks;
2. Qdrant sparse vectors in a migrated collection;
3. a local in-process BM25 index suitable for corpus size.

Decision criteria:

- no cloud dependency;
- reproducible rebuild;
- persistence and migration complexity;
- exact-term recall;
- operational memory;
- compatibility with canonical chunk identity;
- testability.

Write an ADR before implementation. The likely lightweight candidate is SQLite FTS5, but do not select it without validating extension availability in `python:3.12-slim` and defining how derived chunks remain rebuildable.

If approved:

- add lexical retrieval;
- fuse dense and lexical rankings with deterministic reciprocal rank fusion;
- evaluate against the same fixture manifest;
- enable only if measured retrieval improves without unacceptable noise.

## Phase 6 — Optional local reranking

**Do not start unless hybrid candidate recall is adequate and precision remains the bottleneck.**

Requirements:

- local-only;
- no PHI transmission;
- bounded candidate count;
- explicit latency budget;
- deterministic fallback;
- separate evaluation of retriever recall and reranker precision.

Do not reuse the production generation model as an unvalidated judge by default.

## Phase 7 — Optional map-reduce report synthesis

**Do not start unless reports with many sources remain incomplete after retrieval-based synthesis.**

Target design:

```text
source/batch evidence
  → map to cited atomic claims
  → validate claims and source IDs
  → persist or retain bounded intermediate claims
  → reduce supported claims into findings/disagreements/unknowns
  → verify final citations
```

Before implementation, define:

- intermediate claim schema;
- retry and partial-failure semantics;
- token/call limits;
- concurrency compatible with one local GPU;
- whether intermediate claims require SQLite persistence;
- duplicate and contradiction handling;
- deterministic provenance from final claim to retained chunk.

GraphRAG remains out of scope unless evaluation demonstrates a need for whole-corpus thematic or relationship reasoning.

## 9. Files expected to change

### Required initial implementation

- `research-hub/app/document_store.py`
- `research-hub/app/research.py`
- `research-hub/app/clients.py`
- `research-hub/app/synthesis.py`
- `research-hub/app/config.py`
- `research-hub/app/observability.py`
- `research-hub/app/query.py` only for narrowly shared safe-context primitives
- `research-hub/app/retrieval.py` (new)
- `research-hub/tests/test_synthesis.py`
- `research-hub/tests/test_documents_and_batches.py` or `test_document_observations.py`
- `research-hub/tests/test_retrieval.py` (new)
- `research-hub/tests/fixtures/report_retrieval_cases.json` (new)
- `research-hub/tests/benchmark_report_retrieval.py` (new)
- `docker-compose.yml`
- `.env.example` if new operator-facing settings are enabled
- architecture/current-state documentation after behavior is verified

### Should not change in the initial implementation

- public request/response models unless backward-compatible diagnostics are explicitly approved;
- SearXNG configuration;
- Crawl4AI configuration;
- embedding model or vector dimension;
- Open WebUI adapter behavior;
- research seed documents under ignored `TODO/`;
- unrelated infrastructure services.

## 10. Test strategy

### Unit tests

- SQLite migration and observation ownership.
- Scoped retrieval and filtering.
- Candidate ordering/deduplication/diversity.
- Context budget and complete entries.
- Citation-to-represented-evidence validation.
- Correction behavior.
- Empty evidence and dependency failures.
- Prompt-injection sanitization.

### Integration tests

- Existing Redis worker tests.
- Existing Qdrant local/in-memory tests.
- Report retry after cross-job document reuse.
- Report retry with mocked Ollama and real local Qdrant client where feasible.

### Manual local acceptance

- Existing retained authoritative documents.
- Real Ollama topic embedding and generation.
- Report retry only; no recrawl.
- Verify source URLs against stored documents.

### Regression boundaries

- `/query` results remain stable unless shared internal rendering has an intentional test-covered change.
- `/rag` still returns exactly supplied chunks.
- OpenAI-compatible chat continues streaming with ordered sources.
- Ingestion idempotency and stale-chunk deletion remain unchanged.
- Worker retry/lease behavior remains unchanged.

## 11. Risks and mitigations

### Risk — Job/source migration loses evidence

**Mitigation:** Add idempotent backfill tests before changing reads; preserve `documents.job_id` during transition.

### Risk — Filtering by stale Qdrant job metadata

**Mitigation:** Scope retrieval by canonical URLs/document IDs from SQLite observations, not Qdrant `job_id`.

### Risk — Retrieval overrepresents one long source

**Mitigation:** Candidate oversampling plus deterministic per-source cap; measure source coverage.

### Risk — Forced source diversity injects irrelevant evidence

**Mitigation:** Do not guarantee one chunk per source below a calibrated threshold; distinguish registry membership from represented evidence.

### Risk — A magic relevance threshold drops valid evidence

**Mitigation:** Default optional threshold off; derive from versioned evaluation cases.

### Risk — Shared query/report refactor weakens security

**Mitigation:** Extract only tested complete-entry rendering and sanitization; preserve delimiters and exact-source invariants.

### Risk — Stochastic live acceptance is flaky

**Mitigation:** Make deterministic mocked tests the release gate; use live jobs as reviewed evidence, not exact CI assertions.

### Risk — Map-reduce overloads the local model

**Mitigation:** Defer until needed; design bounded calls and concurrency for one GPU.

### Risk — Scope expands into a full agentic research platform

**Mitigation:** Honor phase gates and non-goals. Stop after Phase 4 unless the user explicitly approves later phases.

## 12. Codex high-reasoning execution protocol

Codex GPT-5.6 Sol on `high` should follow this sequence:

1. Restate the chosen phase and acceptance criteria before editing.
2. Inspect definitions and all call sites; do not infer APIs from names.
3. Run the baseline and preserve the result.
4. Write one failing test for one behavior.
5. Run it and confirm the failure reason.
6. Implement the smallest coherent production change.
7. Run targeted tests.
8. Review the diff for provenance, security, and backward compatibility.
9. Repeat for the next behavior.
10. Run the phase gate.
11. Stop and provide:
    - files changed;
    - tests and real outputs;
    - acceptance criteria satisfied;
    - unresolved risks;
    - proposed next phase.

Additional constraints:

- Do not use broad regex edits or reformat unrelated files.
- Do not add a dependency until checking the manifest and proving it is required.
- Do not modify or print secrets.
- Do not commit or push unless explicitly instructed in the execution session.
- If commits are later requested, group them logically by provenance migration, retrieval service, synthesis integration, and evaluation—not one commit per tiny step.
- Stop after approximately three unsuccessful attempts on the same test/file and report the root blocker.
- Do not launch Codex recursively.
- Do not launch research jobs as part of implementation; manual retries happen only after explicit deployment approval.

## 13. Definition of done

The initial modernization is done when Phases 0–4 pass and all of the following are true:

- report evidence comes from scoped relevant chunks, never document prefixes;
- reused documents remain attached to every observing job;
- report citations resolve only to evidence supplied to the model;
- insufficient retrieval is explicit and diagnosable;
- deterministic retrieval/citation metrics exist;
- targeted and full tests pass;
- the authoritative-source report succeeds on manual retry without recrawling;
- public APIs remain compatible;
- no later optional phase has been implemented without approval.

## 14. Open questions requiring owner decisions

These do not block Phase 0 but must be resolved before their relevant phase:

1. Should retrieval diagnostics become public API fields or remain internal metrics/logs?
2. What maximum report latency is acceptable for interactive retry?
3. Should reports attempt balanced coverage of all retained sources or only above-threshold sources?
4. What representative golden evaluation set will be maintained beyond synthetic fixtures?
5. Is SQLite FTS5 acceptable as the future lexical index, or should Qdrant own dense and sparse retrieval?
6. Which local reranker, if any, fits the 12 GB GPU and privacy constraints?
7. Should map-stage claims be persisted for audit and partial retry?
8. Who approves production relevance and fairness thresholds for healthcare research use?

## 15. External architecture references

1. Microsoft, “Retrieval-augmented generation in Azure AI Search”: https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview
2. Microsoft, “Hybrid search using vectors and full text in Azure AI Search”: https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview
3. Anthropic, “Introducing Contextual Retrieval”: https://www.anthropic.com/engineering/contextual-retrieval
4. Microsoft GraphRAG, “Global Search”: https://microsoft.github.io/graphrag/query/global_search
5. Ru et al., “RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation”: https://arxiv.org/abs/2408.08067
