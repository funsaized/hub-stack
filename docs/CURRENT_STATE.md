# Current deployed state

Last verified: 2026-08-11 on the local Windows 11 workstation.

## Deployment model

The repository is stored on the Windows filesystem under OneDrive. Docker
Desktop runs the Linux containers through WSL2; application images and Docker
named volumes are therefore separate from the repository checkout. Editing
`research-hub/app` does not update the live API until the research-hub image is
rebuilt and its container is recreated.

Ollama is the only service with NVIDIA GPU access and uses the workstation's
RTX 3080 Ti. Qdrant corpus data, Redis job metadata, Ollama models,
Research-Hub documents, Crawl4AI, and optional UI state live in Docker named volumes.

## Model stack and WSL envelope

WSL is capped at 20 GB RAM, 12 logical processors, and 2 GB swap. Ollama uses a
30-minute keep-alive, one parallel request, and one loaded model. Research Hub,
Research Worker, and Crawl4AI share the same configurable `LLM_MODEL`.

The installed generation inventory is `qwen3.5:9b`, `qwen3.6:27b`, and the retained
`qwen2.5:7b` baseline. `nomic-embed-text`, its 768-dimension configuration, and the
`research_corpus__nomic_embed_text_768` Qdrant collection remain unchanged.

The measured default is `qwen3.5:9b`. At 8K it stayed fully on the GPU and generated
about 102 tokens/s. Qwen3.6 remains available for explicit offline/high-quality jobs,
but its 45% CPU / 55% GPU placement produced only 3.35-4.12 tokens/s and failed the
interactive deployment gate. Higher-context tuning was therefore skipped and the
operating context remains 8K. See `docs/MODELS.md` and the local benchmark artifact
under `.hermes/benchmarks/` for measurements and switching commands.

Qwen3.5 and Qwen3.6 default to thinking mode in Ollama. The Research Hub client
explicitly disables thinking for its answer-generation paths so response text is not
lost in Ollama's separate `thinking` field.

## Running services

The eight-container default Compose topology is deployed locally. At the latest
runtime check, Research-Hub and the claim verifier were healthy and the dedicated
worker was stable.

Research-Hub currently uses Ollama, Qdrant, Redis, SearXNG, and Crawl4AI.
The API only enqueues ingestion; the dedicated Research Worker claims and executes
jobs with leases, heartbeats, timeouts, bounded retries, and orphan reconciliation.
Open WebUI, Dozzle, Uptime Kuma, Prometheus, and Grafana are independent optional
profiles. Postgres was removed because it owned no application data.

HUB-002 is deployed: Redis, Qdrant, and Crawl4AI have no published host
ports. Research Hub, Open WebUI, SearXNG, Dozzle, and Uptime Kuma bind only to
`127.0.0.1`. Ollama also defaults to loopback, with an explicit
`OLLAMA_BIND_ADDRESS` opt-in for a trusted LAN or Tailscale interface. See
`docs/NETWORKING.md`. This workstation publishes the hub's Ollama on host port
11435 because Docker Desktop's separate model runner owns 11434. Credential
hardening remains unfinished.

## Research-Hub health contract

| Endpoint | Meaning | HTTP behavior |
|---|---|---|
| `/livez` | API process is serving requests | 200 while the process is live |
| `/health` | Backward-compatible alias of `/livez` | 200 while the process is live |
| `/readyz?capability=query` | Ollama and Qdrant can query the retained corpus | 200 ready; 503 degraded |
| `/readyz?capability=rag` | Ollama and Qdrant can retrieve and generate | 200 ready; 503 degraded |
| `/readyz?capability=research` | Redis, Ollama, Qdrant, SearXNG, Crawl4AI, and the claim verifier can support research | 200 ready; 503 degraded |
| `/readyz` | All Research-Hub dependencies | 200 ready; 503 degraded |
| `/health/full` | Diagnostic status for every dependency | 200 with `ok`, `degraded`, or `starting` in JSON |

The Docker healthcheck intentionally calls `/livez`; dependency degradation
must not cause Docker to restart a functioning API process. Operators and the
`research health` CLI use readiness instead. The CLI defaults to all
dependencies, prints failed service names, and exits nonzero when degraded.

Search or crawl failure does not make existing-corpus query/RAG unready.
Starting a new research job requires the complete research dependency set.

## Verified HUB-005 deployment

HUB-005 is implemented, tested, rebuilt, and deployed locally. Verification
performed against the rebuilt image:

- `/livez`, `/readyz`, `/readyz?capability=query`, and `/health/full` returned
  JSON responses successfully.
- The live `research health` command reported Ollama, Qdrant, Redis, SearXNG,
  and Crawl4AI ready.
- Ten automated tests passed, including degraded readiness, JSON
  serialization, synchronous Qdrant probing, query availability without
  search/crawl, CLI failure reporting, and Qdrant persistence coverage.
- The shared shell healthcheck uses Unix line endings so it executes correctly
  inside Linux containers when the checkout resides on Windows.

## Verified durable and idempotent ingestion

HUB-007 and HUB-008 are implemented, tested, rebuilt, and deployed locally.
API restarts do not own or interrupt running ingestion. Worker claims have expiring
leases and heartbeats; abandoned work is reconciled and retried, and permanent
failures reach a terminal state with the attempt error. Redis uses AOF and
`noeviction` for queue safety.

Canonical URLs plus content hashes produce stable document IDs, and stable chunk
IDs include the document, chunk index, and chunker version. Re-ingesting unchanged
content skips existing chunks; changed content is completely embedded before its
old chunks are removed. `DELETE /documents?url=...` removes every version/chunk for
a canonical source URL.

Important remaining limitations include hardcoded/default credentials, crawler
SSRF protections, backups, and CI coverage. Redis AOF improves
durability but is not a backup or a high-availability queue.

## Implemented API contract and observability

HUB-014 makes every public Pydantic model reject unknown fields. The unused
`ResearchRequest.time_limit` field was removed, and `/rag` now supports the same
`tags_filter` as `/query`. Deterministic tests validate documented payloads against
the OpenAPI models and assert clear 422 responses for unsupported fields.

HUB-015 adds request IDs, job correlation, secret-safe JSON logs, API and worker
Prometheus endpoints, version-controlled alert rules, and a provisioned Grafana
pipeline dashboard. The optional monitoring path and thresholds are documented in
`docs/OBSERVABILITY.md`.

HUB-014 and HUB-015 were rebuilt and deployed on 2026-08-09. Live verification
confirmed strict unknown-field rejection with HTTP 422, the API liveness response,
both Prometheus scrape targets, all three alert rules, and the provisioned Grafana
dashboard.

## Verified simplified Compose topology

HUB-016 is implemented and deployed locally. Default Compose starts only Ollama,
Qdrant, Redis, SearXNG, Crawl4AI, Research-Hub, and its worker. Open WebUI,
Dozzle, Uptime Kuma, and the Prometheus/Grafana pair are opt-in profiles. Postgres
is absent from Compose; its old named volume was retained rather than deleted.

All profile configurations rendered successfully. A stopped, already-pulled
default stack reached healthy API status in 39.4 seconds and used approximately
1,014 MiB in a post-start idle sample. See `docs/COMPOSE_PROFILES.md` for profile
commands, per-profile measurements, and measurement caveats.

## Verified RAG and source-policy hardening

HUB-018, HUB-020, and HUB-021 are implemented and deployed locally. RAG context
packing uses a conservative UTF-8-byte token upper bound, reserves model instructions,
question, and answer capacity, never slices an evidence entry, and returns only the
sources actually sent to generation. Retrieved text is explicitly delimited as untrusted
evidence; common injection patterns are labeled and neutralized in derived chunks while
the exact source remains inspectable in the SQLite document store. Custom system prompts
are denied by default and require an explicit trusted-local configuration opt-in.

Research jobs accept `allowed_domains`, `blocked_domains`, `per_domain_limit`, and
`freshness_days`. Search URLs are canonicalized and deduplicated before crawling,
Crawl4AI robots checking is enabled by default, and job progress records policy decisions.
Qdrant metadata separately exposes semantic score, source-quality score, publication/fetch
dates, freshness age, security labels, and robots-policy state.

## Persisted research synthesis

HUB-022 is implemented and deployed locally. Successful ingestion now produces a
durable Markdown report in the SQLite document store with scope, retained source list,
key findings, source disagreements, unknowns, and inline evidence IDs. Material findings
and disagreements are rejected unless they reference retained job documents.

`GET /research/{job_id}/report` and `research report JOB_ID` retrieve the stable artifact.
Failed synthesis is stored separately from completed ingestion and can be retried with
`POST /research/{job_id}/report/retry` or `research report JOB_ID --retry`; that path does
not invoke search, crawling, embedding, or Qdrant writes.

## Claim-support deployment and failed live gate

Material findings and disagreements now use private unique packed evidence IDs, exact spans,
and atomic support propositions. One shared CPU service verifies every required span link and
the complete claim against the span union with the frozen DeBERTa revision and `0.97`
threshold. Corrected output is independently reverified before citations or persistence.

The verifier is offline-only, batches eight checks, rejects inputs over 512 tokens without
truncation, and has a 2.5 GiB Compose memory cap. Research/all readiness requires its health
and exact revision; query/RAG readiness remains independent. Any neutral, contradiction,
low-confidence, unresolved, malformed, timeout, unavailable, over-budget, or revision-mismatch
outcome fails closed. A failed retry preserves the prior persisted report and source registry.

Commit `fb6366f` plus the reviewed uncommitted output-boundary fix is deployed to the verifier,
API, and worker. The verifier reports the exact frozen model/revision, runs with Hugging Face
and Transformers offline flags enabled, and is required by research/all readiness. The shared
Ollama client now fails explicitly on `done_reason=length` or prompt truncation, and API/worker
report completion is bounded at 2,048 tokens.

Ollama now runs with an explicit 8,192-token context. API and worker report generation use a
2,048-token completion allowance. One authorized retry advanced the authoritative report from
attempt 6 to attempt 7 and returned HTTP 200. Both the first generation and its one correction
completed without output-limit or context truncation.

Attempt 7 is completed but displays no material finding or disagreement: exact-span resolution
rejected all seven corrected claims before substantive NLI evaluation. The unsupported
SPIRIT-AI/TRIPOD-ML composite is absent, and the report explicitly records the omissions. Redis
queues, retained document/observation counts, Qdrant counts, and all three sealed evaluation
hashes stayed unchanged; worker logs show no ingestion activity. Phase 4 and the initial
Phases 0-4 modernization remain incomplete because at least one supported finding is required.
The conditional Phase 5 authorization was not activated, so Phase 5 is unstarted.

The next exact-span fix is deployed to Research Hub and Research Worker. Generated material
claims now select enumerated prompt-time span IDs instead of copying free-form evidence text;
the resolver maps each ID back to its original exact sanitized substring before the unchanged
NLI contract. Span IDs are trimmed before lookup, and validation exposes precise bounded
failure reasons rather than collapsing them into `unresolved_span`. Focused tests passed 19/19,
the complete Redis-15 suite passed 103 tests, the retrieval benchmark passed, and bounded
Hermes verification returned `ok=true`. No live retry followed this deployment: attempt 7 and
its six-source registry remain preserved, Phase 4 still requires one separately authorized
acceptance retry, and Phase 5 remains unstarted.

That single retry was subsequently authorized and consumed. Attempt 8 proved the span-ID path:
eight first-pass material claims resolved exactly and reached the frozen production NLI
verifier. Seven were neutral and one was low-confidence, so the one allowed correction ran.
The correction exhausted the 2,048-token output allowance and failed explicitly with
`done_reason=length`; no partial output was accepted. Attempt 8 is `failed`, while the attempt-7
Markdown and six-source registry remain byte-preserved. Queues, SQLite, Qdrant, worker activity,
and sealed hashes remain clean. Phase 4 still lacks a supported material finding, and no retry,
tuning, or Phase 5 work is authorized after this failure.

## Span-first claim drafting and gate alignment

Attempts 9 and 10 both produced zero verified claims. Attempt 10 was diagnosed from its
retained per-check diagnostics rather than repeated, and the causes were span
construction and generation order rather than retrieval or the NLI operating point.

Deployed changes:

- Span selection moved into `research-hub/app/spans.py`. Sentence bounds are
  abbreviation-aware, non-propositional spans are dropped before generation, and a
  sentence whose subject is an unresolved demonstrative is merged with its predecessor
  into a contiguous window or dropped when it is chunk-initial. Every offered span is
  still an exact substring of its sanitized chunk. On the six real attempt-10 chunks this
  cuts 46 offered spans to 8, removing the reference-list debris and the unresolvable
  demonstrative that caused four of the ten rejections.
- Generation is span-first. One bounded 256-token call drafts one claim from one exact
  span under a deletion-compression contract, replacing one 2,048-token call that drafted
  six claims and then chose spans for them. Wrong-span pairing is structurally impossible
  and the output-limit failure class that ended attempts 5, 6 and 8 is gone. A malformed
  or declined draft is isolated to its span instead of failing the report.
- The production verifier now judges a single-evidence claim with exactly the pair the
  sealed final evaluation measured. The redundant self-check was replaced by a string
  equality assertion and the duplicate span check was removed for single-evidence claims.
  Multi-evidence claims keep the per-span conjunct. Model, revision and threshold are
  unchanged, and all three sealed hashes still match.
- The research-hub Dockerfile bakes the pinned verifier snapshot above the app copy, so
  editing application code no longer invalidates the model-download layer.

Measured on the frozen attempt-10 evidence with the deterministic offline benchmark and
five live samples: junk rejection `0.741935`, exact substring rate `1.0`, critical span
recall `1.0`, and four to eight verified claims per run with at least one verified claim
in 5 of 5 runs. Two runs still had claims rejected by the verifier, so the gate is not
rubber-stamping. Attempt 10 produced zero verified claims from ten drafts on the same
evidence.

The automated gate is green: 118 tests with zero skips including Redis integration against
DB 15, critical Recall@4 `1.0`, citation validity `1.0`, duplicate rate `0.0`, clean
`pip check` and clean Compose validation.

Known limitation now disclosed in every report: cross-source disagreement is not assessed,
because each displayed claim must be entailed by one exact span from one source
(HUB-032).

## Phase 4 live acceptance passed (attempt 11)

The single authorized live retry on 2026-08-11 returned HTTP 200 in about 20.5 seconds
under correlation `9330511b-df54-4846-b09c-3d0e8a123736`. The authoritative clinical-AI
report advanced from attempt 10 (`failed`) to attempt 11 (`completed`) with six verified
cited material findings, each entailed by one exact evidence span at entailment
`0.986`-`0.995` against the frozen verifier's sealed `evidence_union` pair and `0.97`
threshold. One draft was correctly rejected as `neutral` (`0.0989`) for substituting
"CONSORT-AI extension" where the span said "CONSORT 2010"; its single correction redraft
declined, and two other spans yielded model declines — all disclosed in the report's
unknowns section.

Before the retry, all three service images were rebuilt from the clean tree at `53e7595`
and the deployed `spans.py`, `synthesis.py` and `claim_support.py` were verified
SHA-256-identical to HEAD in the hub, worker and verifier containers. The retry mutated
only the target report row: the six-source registry stayed byte-identical, SQLite (67
documents, 67 observations, 13 reports), Qdrant (24,465 points, 23,369 indexed vectors),
Redis queues (empty), and all three sealed evaluation hashes were unchanged, and worker
logs show no crawl, ingestion, embedding or upsert.

Phase 4 of the RAG synthesis modernization is closed with all five gate conditions green,
and HUB-019 is Done.

## Hybrid retrieval entry condition measured and met (HUB-017)

Phase 5 was initially recorded as not entered because critical Recall@4 was `1.0`, but
that number comes from a benchmark that replays pre-scored fixture candidates and never
runs an embedding or vector search — it cannot show a dense miss. The new live probe
`tests/benchmark_retrieval_exact_terms.py` drives the real embed-and-search production
path against 13 exact-term needle cases (checklist items, DOIs, consensus statistics,
software identifiers, each in at most 5 of 870 retained chunks) mined from the
authoritative corpus, validated by seven deterministic manifest tests and read-only
against all live state.

Measured 2026-08-11: dense-only hit@4 is `0.6923`. Three sentinels reached the
40-candidate pool but lost dense ranking (rank fusion recovers them); the DOI
`10.1136/bmj.g7594` sentinel never entered the pool (only lexical candidate generation
recovers it). The nine hits ranked 1–3, so dense remains the primary channel and the
gap is specifically exact identifiers.

HUB-017 is therefore Open with measured justification. Acceptance: hybrid hit@4 `1.0`
on the exact-term manifest with no regression in the dense-only report benchmark or the
sealed claim-support contract. Implementation is gated on the PRD design spike (SQLite
FTS5 vs Qdrant sparse vectors vs in-process BM25).

## Hybrid report retrieval deployed (Phase 5, HUB-017)

Phase 5 of the modernization is implemented, measured and deployed
(2026-08-11, ADR-001). Report retrieval now fuses dense embedding search with an
SQLite FTS5 needle channel under deterministic reciprocal rank fusion (`k=60`):

- `chunk_fts` lives in `documents.sqlite3`, holding sanitized derived chunks
  byte-equal to Qdrant payload text, written at ingestion, removed with document
  deletion, and rebuildable from retained documents alone via
  `python -m app.rebuild --lexical-only` (backfilled: 67 documents, 24,854 chunks).
- The lexical channel selects needle terms by document frequency measured within the
  retrieval scope (unigrams and adjacent bigram phrases, DF <= max(5, 1% of scoped
  chunks), rarest band only). User topic text is reduced to quoted alphanumeric
  tokens, so FTS5 query operators cannot inject or crash.
- Measured on the versioned exact-term manifest: dense-only hit@4 `0.6923`, hybrid
  hit@4 `1.0`, including the out-of-pool DOI case that no reranking could recover.
  141 tests pass; report retrieval and claim benchmarks are unchanged; the attempt-11
  report, Qdrant, Redis and all sealed hashes are untouched (the only persisted
  mutation is the additive `chunk_fts` table).
- `REPORT_HYBRID_RETRIEVAL` (default true) disables the channel; with it off, or when
  no needle term matches, candidate ordering is byte-identical to dense-only.
  `/query` and `/rag` remain dense-only per the PRD regression boundaries.

Phase 6 (local reranking) is not entered: hybrid recall on the measured manifest is
`1.0`, so precision is not the bottleneck.

## Hardcoded credentials removed and rotated (HUB-003)

Deployed 2026-08-11. No secret value is tracked in git any longer:

- `SEARXNG_SECRET` and the shared Crawl4AI token (`CRAWL4AI_API_TOKEN`, also
  injected as `CRAWL4AI_TOKEN` into research-hub and the worker) now come only
  from the gitignored `.env`, referenced in `docker-compose.yml` with required
  `${VAR:?}` expansion — compose refuses to parse without them (verified: with
  `.env` absent, `docker compose config` fails with the generation hint).
- `searxng/settings.yml` no longer carries `secret_key` (the `SEARXNG_SECRET`
  env var overrides it) and `crawl4ai-config.yml` no longer carries
  `api_token` (the server enforces the env token; verified 401 without it).
- Open WebUI's `WEBUI_SECRET_KEY` fallback `changeme_in_production` was removed;
  when unset the container generates and persists a random key in its volume.
  The service is profile-gated, so it cannot use hard-required expansion.
- `.env.example` documents the required fields with blank placeholders and
  `openssl rand -hex 32` generation instructions; `SETUP.md` gained a
  "Configure secrets" step before first start.
- The deployed values were rotated: new random secrets in the local `.env`,
  searxng/crawl4ai/research-hub/research-worker recreated. Verified after
  rotation: the old token is rejected (401), the new token authenticates,
  `/readyz` is all-true, the 141-test suite passes in-container, and the
  attempt-11 report and source registry remain byte-identical
  (`068d60b2…`, `d6748d76…`) at 67 documents / 13 reports.
