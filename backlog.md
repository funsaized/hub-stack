# Hub Stack Backlog

This backlog turns the architectural review of the 0.1.0 MVP into executable work. Priorities are ordered around the current product goal: a private, reliable, single-user research corpus and RAG service running on one machine.

## Priority definitions

- **P0 — correctness or security blocker:** fix before trusting retained data or enabling remote access.
- **P1 — daily-use reliability:** required before treating the stack as an always-on service.
- **P2 — retrieval and product quality:** improves research usefulness after the foundation is trustworthy.
- **P3 — optional expansion:** defer until usage proves the need.

---

## P0 — Correctness and security blockers

### HUB-001 — Preserve the Qdrant collection across application restarts

**Status:** ✅ Done

**Problem:** `QdrantClient.__init__()` calls `recreate_collection()`, deleting the corpus whenever research-hub starts. This contradicts the documented persistence guarantee.

**Work:**

- Replace unconditional recreation with `collection_exists()` plus `create_collection()`.
- Validate an existing collection's vector size and distance configuration at startup.
- Fail with a clear migration error when the configured embedding model is incompatible with the existing collection.
- Correct the architecture and healthcheck documentation to match the implementation.

**Acceptance criteria:**

- Insert a sentinel point, restart research-hub, and verify the point remains.
- Starting against a missing collection creates it once.
- Starting against an incompatible collection fails without deleting data.
- An automated regression test covers all three cases.

### HUB-002 — Stop publishing internal service ports to every network interface

**Status:** ✅ Done

**Problem:** Every Compose port currently binds to `0.0.0.0`, exposing Redis, Postgres, Qdrant, Ollama, Crawl4AI, and management services to the LAN.

**Work:**

- Replace host port mappings for internal services with `expose` or no published port.
- Bind host-only user surfaces to `127.0.0.1` explicitly.
- Document which surfaces may be exposed through Tailscale or a reverse proxy.
- Add separate internal and ingress networks where useful.

**Acceptance criteria:**

- Redis, Postgres, Qdrant, Ollama, and Crawl4AI cannot be reached from another LAN device.
- Intended local UIs and the API remain reachable from the host.
- `docker compose config` shows no unintended `0.0.0.0` bindings.
- Remote access documentation exposes only explicitly approved services.

### HUB-003 — Remove hardcoded credentials and insecure defaults

**Status:** ✅ Done — deployed 2026-08-11. All secrets moved to the gitignored
`.env` with required `${VAR:?}` compose expansion: `SEARXNG_SECRET` (removed from
`searxng/settings.yml`; the env var overrides the file) and `CRAWL4AI_API_TOKEN`
(single variable feeding all three compose sites; removed from
`crawl4ai-config.yml`). Open WebUI's `WEBUI_SECRET_KEY` static fallback removed —
empty means the container self-generates a persisted key (profile-gated, so hard
requirement is not possible). `.env.example` documents blank placeholders with
`openssl rand -hex 32` instructions; `SETUP.md` has a pre-start secrets step.
Deployed values rotated and containers recreated. Verified: compose refuses to
parse without the secrets, the old Crawl4AI token gets 401 and the new one
authenticates, `/readyz` all-true, 141 tests green in-container, attempt-11
report/sources byte-identical. Postgres part obsolete (Postgres left Compose).

**Problem:** SearXNG, Crawl4AI, and Open WebUI ship with hardcoded or fallback credentials.

**Work:**

- Move all secrets and shared tokens to environment variables or Docker secrets.
- Expand `.env.example` with blank placeholders only; do not include deployment-specific values.
- Require non-empty values for security-sensitive settings outside an explicit dev profile.
- Rotate existing deployed values after migration.

**Acceptance criteria:**

- No usable password, token, or secret is tracked in Git.
- The production/default profile refuses to start with placeholder values.
- A fresh setup works by copying `.env.example` and filling the documented fields.
- Secret values do not appear in `docker compose config` output committed to logs.

### HUB-004 — Restrict Dozzle's Docker control-plane access

**Status:** ✅ Done — Dozzle sits behind the optional `logs` profile, publishes only
`127.0.0.1:8888`, and sets `DOZZLE_ENABLE_ACTIONS: "false"`. The read-only socket proxy
remains unimplemented and is not required at the current single-user exposure.

**Problem:** Dozzle has the Docker socket mounted and container actions enabled while its port is published broadly.

**Work:**

- Disable `DOZZLE_ENABLE_ACTIONS` by default.
- Bind Dozzle to localhost or place it behind authenticated ingress.
- Evaluate a read-only Docker socket proxy with an explicit API allowlist.
- Make Dozzle an optional Compose profile.

**Acceptance criteria:**

- The default stack cannot start, stop, or restart containers through Dozzle.
- Dozzle is not reachable from the LAN by default.
- Removing the optional observability profile does not affect research or query flows.

### HUB-005 — Fix detailed health checks and the CLI health command

**Status:** ✅ Done

**Problem:** `health_check()` passes the async Qdrant health method to `asyncio.to_thread()`, producing an unserializable coroutine. The CLI calls `/health` but expects the `/health/full` response shape.

**Work:**

- Make the Qdrant health method consistently synchronous or consistently async.
- Add distinct `/livez` and `/readyz` endpoints.
- Keep `/health/full` as a diagnostic endpoint.
- Point the CLI at the correct endpoint and handle degraded services.
- Log dependency failures instead of swallowing every exception.

**Acceptance criteria:**

- All health responses are JSON serializable.
- `/livez` succeeds when the API process is alive.
- `/readyz` reflects the dependencies required for the requested capability.
- Existing corpus queries remain available when only search or crawl dependencies are down.
- `research health` exits nonzero and prints the failing services when readiness is degraded.

### HUB-006 — Add crawler SSRF and network-boundary protections

**Status:** ✅ Done — deployed 2026-08-11. `app/url_policy.py` vets every crawl
destination twice: before the fetch (scheme/port allowlist, then `ipaddress`-based
rejection of loopback/private/link-local/CGNAT/multicast/reserved/metadata
destinations across every DNS answer, IPv4-mapped IPv6 unwrapped, DNS failures
fail closed) and after the fetch against the landing URL Crawl4AI reports
(`redirected_url`), so redirect-based escapes drop the document. Oversized
documents are rejected (`CRAWL_MAX_MARKDOWN_CHARS`, default 2,000,000). Every
rejection logs job ID, normalized destination, and reason (`crawl_rejected`),
and increments `hub_crawl_total{outcome="rejected"}`. Network layer: Crawl4AI
now sits on a dedicated `crawler` network — it cannot resolve or reach Qdrant,
SearXNG, claim-verifier, or the observability services (verified in-container);
it retains Redis and Ollama access because its own server config requires both.
Accepted residuals: (a) Redis/Ollama remain reachable from the crawler container
— cross-protocol HTTP-to-Redis is mitigated by Redis's POST/Host: protection
and app-level vetting blocks these hosts as crawl targets; (b) no redirect-count
or request-duration limit at the Crawl4AI API (not exposed) — landing-URL
revalidation covers redirect-based SSRF; (c) DNS TOCTOU between the hub's check
and Crawl4AI's fetch — bounded by the landing-URL recheck and network isolation.
34 new tests cover direct, encoded, DNS-resolved, mixed-answer, IPv6, and
redirect-based internal destinations plus size limits; the 175-test suite is
green in-container and public pages still vet cleanly.

**Problem:** The crawler processes discovered URLs while sharing a network with databases and control-plane services. Malicious results or redirects could target internal services.

**Work:**

- Permit only HTTP and HTTPS URLs.
- Reject loopback, private, link-local, multicast, and metadata-service destinations for IPv4 and IPv6.
- Revalidate DNS results and every redirect destination.
- Add response-size, redirect-count, and request-duration limits.
- Apply per-domain concurrency and delay limits.
- Isolate crawler egress from Redis, Postgres, Qdrant, Ollama, Dozzle, and Uptime Kuma where possible.

**Acceptance criteria:**

- Tests reject direct, encoded, DNS-resolved, and redirect-based internal destinations.
- Public HTTP/HTTPS pages still crawl successfully.
- The crawler cannot reach internal data/control services at the network layer.
- Blocked attempts are logged with job ID, normalized destination, and reason.

---

## P1 — Daily-use reliability

### HUB-007 — Replace in-process background tasks with a durable worker

**Status:** ✅ Done

**Problem:** Redis stores job metadata but does not queue work. `asyncio.create_task()` loses running jobs on process restart and prevents safe multi-worker API deployment.

**Work:**

- Introduce a dedicated ingestion worker backed by Redis.
- Enqueue jobs durably from the API.
- Add claim leases, heartbeats, bounded retries, timeouts, and terminal failure states.
- Reconcile abandoned `pending` and `running` jobs after restart.
- Track and drain or release claimed work during graceful shutdown.

**Acceptance criteria:**

- Restarting the API does not interrupt a worker-owned job.
- Restarting a worker causes an abandoned job to resume or retry automatically.
- A permanently failing job reaches a terminal state with a useful error.
- Running two API workers does not execute a job twice.
- Queue behavior has automated integration coverage.

### HUB-008 — Make ingestion idempotent and deduplicate content

**Status:** ✅ Done

**Problem:** Random UUID point IDs create duplicate vectors whenever a source or topic is reprocessed.

**Work:**

- Canonicalize source URLs.
- Compute stable document IDs from canonical URL and content identity.
- Compute stable chunk IDs from document ID, chunk index, and chunker version.
- Upsert changed content and remove stale chunks from prior document versions.
- Record duplicate/skipped counts in job progress.

**Acceptance criteria:**

- Re-ingesting unchanged content does not increase point count.
- Changed content replaces the prior document version predictably.
- Deleting a document removes all associated chunks.
- Deduplication behavior is covered by repeat-ingestion tests.

### HUB-009 — Persist canonical source documents outside the vector index

**Status:** ✅ Done

**Problem:** Only chunks are retained. The system cannot reliably re-chunk, re-embed, inspect extraction, compare versions, or rebuild Qdrant.

**Work:**

- Store crawl metadata and cleaned Markdown in a persistent local document store.
- Record canonical URL, fetched time, content hash, HTTP metadata, extraction version, and job ID.
- Add a rebuild command that recreates the vector index from retained documents.
- Define retention and deletion behavior.

**Acceptance criteria:**

- Qdrant can be rebuilt from retained documents without re-crawling the web.
- Operators can inspect the exact cleaned source used for any chunk.
- Re-embedding with a new model creates a separately versioned index.
- Deleting a source removes both canonical content and derived vectors.

### HUB-010 — Batch embeddings and checkpoint Qdrant writes

**Status:** ✅ Done

**Problem:** Embeddings are generated one at a time, accumulated in memory, and written in one final upsert.

**Work:**

- Move from legacy `/api/embeddings` calls to Ollama's batched `/api/embed` endpoint.
- Bound batches by token/character budget and batch size.
- Upsert each completed batch and persist progress checkpoints.
- Retry transient embedding and Qdrant failures with backoff.
- Preserve partial progress for resumable jobs.

**Acceptance criteria:**

- A large job has bounded memory usage.
- An interruption after one or more batches resumes without recomputing completed batches.
- Batch size and latency are observable.
- An integration benchmark shows throughput improvement over sequential requests.

### HUB-011 — Add real automated tests

**Status:** ✅ Done

**Problem:** `test_research.py` and `test_query.py` are executable smoke scripts, not an isolated or repeatable test suite.

**Work:**

- Add unit tests for chunking, request validation, filters, context construction, state transitions, and failure handling.
- Add contract tests for every documented API example.
- Add integration tests for Redis and Qdrant persistence and worker recovery.
- Retain one opt-in full-stack smoke test.
- Ensure test modules do not execute network calls during collection/import.

**Acceptance criteria:**

- Tests run with one documented command and deterministic exit status.
- Unit tests do not require the full Docker stack.
- Restart persistence and abandoned-job recovery have regression coverage.
- Documentation examples are validated in CI.

### HUB-012 — Add CI and reproducible dependency management

**Status:** ✅ Done — deployed 2026-08-11 (except the two optional items noted
below). The six formerly-`latest` images are pinned to the digests that were
running (`tag@sha256`, versions read from the containers: ollama 0.32.6,
qdrant v1.19.0, searxng 2026.8.4, crawl4ai 0.9.2, dozzle v10.6.15, uptime-kuma
1.23.17) and every container was recreated onto its pin.
`research-hub/requirements.lock.txt` is a pip-compile transitive lockfile with
hashes (1,169 hashes, torch CPU extra index preserved), generated inside
python:3.12-slim against the deployed image's `pip freeze` so it reproduces the
frozen set exactly; the Dockerfile now installs with `--require-hashes`,
`pip check` is clean in the built image, and `scripts/relock.ps1` documents
regeneration. `.github/workflows/ci.yml` gates pushes/PRs on: ruff E9/F +
compileall, the full 175-test suite against a Redis 7 service container
(DB 15 integration included), `docker compose config` with placeholder
secrets, and the research-hub image build (buildx + GHA cache) followed by
in-image `pip check`. The three live benchmarks stay local by design (need
Ollama and the corpus). Not done, deliberately: automated dependency-update
PRs and vulnerability scanning (single-user local stack; revisit if exposure
grows), and the FastAPI/Starlette upgrade (would change the pinned set the
sealed evaluation ran on — bundle it with the next verifier-affecting change).

**Problem:** There is no CI, most container images use `latest` or `main`, and Python dependencies lack a transitive lock with hashes.

**Work:**

- Add CI for syntax, formatting/linting, unit tests, Compose validation, and research-hub image build.
- Pin container images to versioned tags; use digests for critical services where practical.
- Adopt a Python lockfile containing transitive versions and hashes.
- Enable automated dependency update PRs and vulnerability scanning.
- Upgrade FastAPI/Starlette and verify reported advisories against the application's exposure.

**Acceptance criteria:**

- The same commit resolves the same application and container dependencies.
- CI blocks merges on failed tests, invalid Compose configuration, or failed image builds.
- Dependency updates are isolated, reviewable changes.
- A fresh checkout builds without relying on moving `latest`/`main` tags.

### HUB-013 — Implement and test backup and restore

**Status:** ✅ Done — deployed 2026-08-11, scoped to the irreplaceable core
(`documents.sqlite3`); see `docs/BACKUP.md`. `scripts/backup.ps1` snapshots the
WAL database with `VACUUM INTO` inside the running container (no downtime),
integrity-checks and count-checks the snapshot, copies it to the gitignored
`backups/` directory, and prunes to the newest 14. The Windows scheduled task
`hub-stack-documents-backup` runs it daily at 03:30, logging to
`backups/backup.log`; failures exit non-zero and surface in the log and the
task's Last Run Result. `scripts/restore.ps1` restores into a brand-new clean
volume by default (tested 2026-08-11: `integrity=ok documents=67 reports=13`,
attempt-11 report present, ~10 s) and `-Live` performs the full swap
(stop writers → replace DB → restart → `/readyz` poll). Qdrant is deliberately
not snapshotted — `python -m app.rebuild` from SQLite is the documented vector
recovery, and Redis job state is transient. Backups stay on this machine, so
they are unencrypted by scope; encrypt before syncing anywhere off-machine.

**Problem:** The knowledge base and job state live only in local named volumes, and backup instructions are not automated or restore-tested.

**Work:**

- Back up retained source documents, Qdrant, Redis state, and configuration required for recovery.
- Automate encrypted, scheduled backups to the chosen destination.
- Add retention and failure notification.
- Implement a restore script into isolated volumes.
- Run a post-restore query smoke test.

**Acceptance criteria:**

- A scheduled backup completes without manually stopping the stack, or documented consistency controls are applied.
- A restore into empty volumes returns a known sentinel document and query result.
- Backup failures produce an actionable notification.
- Restore instructions include measured recovery time and required credentials.

### HUB-014 — Make the API contract strict and remove documentation drift

**Status:** ✅ Done

**Problem:** Unknown fields are silently discarded, `RAGRequest` lacks documented `tags_filter`, and `ResearchRequest.time_limit` is accepted but unused.

**Work:**

- Configure request models to reject unknown fields.
- Add tag filtering to RAG or remove it from documentation.
- Implement `time_limit` end to end or remove it from the request model.
- Generate or test examples against the OpenAPI contract.
- Audit architecture and setup claims against executable behavior.

**Acceptance criteria:**

- Unsupported request fields return a clear 422 response.
- Every documented request example passes an automated contract test.
- No accepted field is silently ignored.
- Persistence and healthcheck documentation matches regression-tested behavior.

### HUB-015 — Add structured application observability

**Status:** ✅ Done

**Problem:** Container logs and uptime monitoring do not expose pipeline quality, latency, or failure stages.

**Work:**

- Emit structured logs with job ID, phase, source URL/domain, duration, retry count, and failure category.
- Add metrics for search results, crawl success, chunks per source, embedding latency, upsert latency, retrieval scores, and generation latency/tokens.
- Add request/job correlation IDs.
- Establish a basic dashboard and alert thresholds.

**Acceptance criteria:**

- A failed job can be traced from API submission to the exact failed dependency and source.
- Phase-level latency and error rates are visible without parsing free-form logs.
- Logs do not expose secrets or full sensitive prompts by default.
- Metrics add negligible idle overhead on the target machine.

### HUB-016 — Simplify the default Compose topology

**Status:** ✅ Done

**Problem:** Ten always-on services create unnecessary operational and security surface for an application under 1,000 lines.

**Work:**

- Remove unused Postgres from the default stack until it owns real data.
- Move Open WebUI, Dozzle, and Uptime Kuma into optional Compose profiles.
- Keep the default path focused on Ollama, Qdrant, Redis, SearXNG, Crawl4AI, and research-hub.
- Document resource usage by profile.

**Acceptance criteria:**

- `docker compose up` starts only the services required for research and RAG.
- Optional profiles can be enabled independently.
- Removing optional services does not affect API readiness.
- Default startup time and idle resource usage are measured and documented.

---

## P2 — Retrieval and product quality

### HUB-017 — Replace dense-only retrieval with hybrid retrieval

**Status:** ✅ Done (report retrieval) — deployed 2026-08-11. SQLite FTS5 needle
channel (scoped-rarity term selection over sanitized derived chunks) fused with dense
retrieval via deterministic RRF (`k=60`) in `ScopedRetrievalService`, per
`docs/ADR-001-lexical-index-for-hybrid-retrieval.md`. Measured on the versioned
exact-term manifest: dense-only hit@4 `0.6923`, hybrid hit@4 `1.0`, no regression in
the report benchmark, claim benchmarks, or the 141-test suite. Rebuildable via
`python -m app.rebuild --lexical-only`; disable with `REPORT_HYBRID_RETRIEVAL=false`.
`/query` and `/rag` remain dense-only by design (PRD regression boundary); extending
hybrid to the query path would be new scope. Reranking (HUB-017's optional evaluation
item) not entered: hybrid recall is `1.0` on the measured manifest, so precision is
not the bottleneck (Phase 6 entry condition unmet).

**Problem:** `QueryEngine` is described as hybrid but currently performs dense-vector search only.

**Work:**

- Add sparse/lexical candidate generation alongside dense vectors.
- Fuse rankings with a documented algorithm.
- Add a configurable relevance threshold.
- Diversify results by source document to prevent one page dominating context.
- Evaluate a small reranker over the candidate set.

**Acceptance criteria:**

- A versioned evaluation set compares dense-only and hybrid retrieval.
- Exact identifiers and rare keywords improve without materially harming semantic queries.
- Low-relevance queries can abstain instead of returning arbitrary nearest neighbors.
- Final context does not exceed the configured per-document result cap.

### HUB-018 — Use token-aware context packing

**Status:** ✅ Done

**Problem:** Context is truncated using an approximate characters-per-token slice that can split chunks and produce a source list that differs from the prompt.

**Work:**

- Count tokens using the active generation model's tokenizer or a validated approximation.
- Pack complete chunks within the available prompt budget.
- Reserve tokens for system instructions, question, and answer.
- Return only sources actually included in generation context.

**Acceptance criteria:**

- No chunk or citation marker is cut mid-entry.
- Prompt size remains below the configured model context limit.
- Returned sources exactly match the context supplied to the model.
- Boundary cases have automated tests.

### HUB-019 — Validate citations and ground generated claims

**Status:** ✅ Done — structured evidence references, exact-span binding, span-first
deletion-compression drafting, and the frozen offline NLI verifier are deployed and
covered by tests. The authorized live acceptance retry (attempt 11, 2026-08-11) produced
a completed report with six verified cited findings at entailment `0.986`-`0.995`, no
citation outside supplied evidence, and no collateral mutation, closing the Phase 4 gate.

**Problem:** The prompt requests citations, but the system does not verify that citations exist or support the associated claims.

**Work:**

- Require structured citation references in generated output.
- Reject or repair citation numbers outside the supplied source set.
- Flag unsupported answers and insufficient context explicitly.
- Preserve retrieval scores and source identities with the answer.

**Acceptance criteria:**

- Answers cannot return references to nonexistent source numbers.
- Empty or weak retrieval produces an abstention rather than confident synthesis.
- Evaluation fixtures cover valid, missing, duplicated, and invalid citations.

### HUB-020 — Harden prompts against untrusted crawled content

**Status:** ✅ Done

**Problem:** Crawled pages can contain instructions that compete with the RAG system prompt.

**Work:**

- Delimit retrieved text as untrusted evidence, not instructions.
- Strip or classify common prompt-injection patterns during ingestion without destroying source fidelity.
- Add canary tests containing adversarial page content.
- Keep arbitrary user-supplied system prompts restricted to trusted local callers.

**Acceptance criteria:**

- Injection fixtures cannot override system behavior or request secrets/internal actions.
- Original source text remains inspectable for audit.
- Security failures are measurable in a repeatable evaluation set.

### HUB-021 — Add source quality, freshness, and crawl policy controls

**Status:** ✅ Done

**Problem:** The pipeline takes the first N search results without explicit authority, freshness, duplication, or domain policy.

**Work:**

- Add domain allow/block controls and per-domain limits.
- Normalize and deduplicate search results before crawling.
- Capture publish/fetch dates where available.
- Score source quality and freshness separately from semantic relevance.
- Respect robots and configurable crawl policies.

**Acceptance criteria:**

- Duplicate/canonical-equivalent URLs are crawled once.
- Jobs can require or exclude domains and freshness windows.
- Retrieval metadata exposes source and freshness signals.
- Crawl policy decisions are visible in job results.

### HUB-022 — Add a persisted research synthesis artifact

**Status:** ✅ Done

**Problem:** A completed research job creates searchable chunks but no job-level report, source comparison, or summary. The current product is a corpus builder rather than deep research.

**Work:**

- Generate a persisted job report after ingestion.
- Include scope, source list, key findings, disagreements, unknowns, and inline evidence references.
- Make report generation retryable independently of crawling/embedding.
- Expose report retrieval through API and CLI.

**Acceptance criteria:**

- Completed jobs expose a stable report without requiring a separate ad hoc RAG query.
- Every material report claim links to retained source evidence.
- Failed synthesis can be retried without re-crawling.
- Reports distinguish source disagreement and insufficient evidence.

### HUB-023 — Wire Open WebUI to the research corpus

**Status:** ✅ Done — Open WebUI reaches the corpus through the OpenAI-compatible
`research-corpus` route in `research-hub/app/openai_compat.py`, and remains optional
in Compose.

**Problem:** Open WebUI currently chats with Ollama but does not use research-hub, making it adjacent infrastructure rather than part of the research experience.

**Work:**

- Integrate Open WebUI through a supported function/tool or OpenAI-compatible adapter.
- Surface retrieved sources and citations in the conversation.
- Preserve the ability to use plain local chat without retrieval.
- Keep Open WebUI optional in Compose.

**Acceptance criteria:**

- A user can select corpus-backed chat explicitly.
- Corpus-backed responses display the same sources as the research-hub API.
- Plain chat and RAG chat remain distinguishable.
- Integration survives an Open WebUI restart without manual reconfiguration, or setup is fully scripted.

---

### HUB-031 — Reconcile the Redis report-status projection with SQLite

**Status:** ✅ Done — `report_status` is now derived from the persisted SQLite
report at job-read time (`ResearchOrchestrator.get_job` overrides the Redis
projection with `DocumentStore.report_status`), so a crash between the SQLite
and Redis writes can no longer surface a contradicting status. Crash-window
covered by `tests/test_report_status_reconciliation.py` for completed, failed,
and stale-contradicting projections.

**Problem:** SQLite is authoritative for persisted reports, but `report_status` is
projected separately into the Redis job record. The two writes are not atomic, so a
crash between them can leave a stale projection. Normal and failure paths are tested;
crash reconciliation is not.

**Work:**

- Derive `report_status` from the persisted SQLite report when a job is read, or
  reconcile the projection from the report on read.
- Cover the crash window with a test that persists a report and drops the projection.

**Acceptance criteria:**

- A job read after a lost projection write reports the persisted report status.
- No code path can display a report status that contradicts SQLite.

### HUB-032 — Support verified cross-source disagreement

**Status:** 🔴 Open — one full attempt measured and NOT accepted (2026-08-12);
the deployed system is unchanged and still discloses the limitation.

**Attempt record (2026-08-11/12, branch `hub-032-cross-source-disagreement`):**
The operator approved the protocol and blind-annotated a new sealed 120-case
final (content `e32c16d6…`, labels `91c80ede…`). The v3 rule (union premise at
the sealed 0.97 threshold plus leave-one-out necessity — a removable ref rejects
as `padding_reference`) and bounded cross-document pair drafting were
implemented and calibrated only on a separate 60-case labels-by-design set
(padding rejected 22/22, neutral 16/16, joint 14/22 on adversarial cross-topic
conjunctions; no tuning applied). The one-time final measurement
(results `8e6f8665…`, runner refuses re-runs):

- padding rejection **PASS** 30/30 (23 via `padding_reference`);
- joint acceptance **FAIL** 14/30 (0.47 vs 0.8) — the pinned NLI model scores
  many genuinely joint two-component claims below threshold on the union;
- disagreement acceptance **FAIL** 12/20 (0.6 vs 0.8);
- zero unsupported acceptances **FAIL**: `mrf3-045` (new multi-ref path welded
  a false guideline attribution at 0.9729) and `mrf3-055` (see HUB-033 — this
  one is the sealed single-ref path, not the new rule).

**Disposition:** the rule and pair drafting do not ship; nothing was deployed
(verified: containers byte-identical to main, attempt-11 artifacts and all v2
seals intact; the v2 seal is NOT retired and remains the deployed gate). The
v3 blind set is consumed.

**Pivot (2026-08-12, operator decision):** rather than upgrading the frozen NLI
model, the operator directed replacing the NLI gate with an LLM-as-judge
faithfulness gate (a standard modern RAG pattern: RAGAS-style statement-level
groundedness judging), using MiniMax M3 via the operator's Token Plan
subscription. The trade-offs were surfaced and accepted: the judge is
generative (prompt-injection surface moves into the gate and must be handled
in-design), retained corpus spans will leave the machine to MiniMax's API
(privacy-model change to document), and a cloud judge is not frozen (sealed
evaluations must record the served model version and re-baseline on change).
This item is re-scoped onto the judge gate: multi-span judging is native to an
LLM judge, which directly removes the joint-entailment bottleneck that failed
the v3 final. Blocked by HUB-035 and HUB-036; the leave-one-out NLI
implementation remains archived on `hub-032-cross-source-disagreement`.

**Original problem:** every displayed claim must be entailed by one exact span
from one source, so a disagreement that only exists *between* two sources
cannot be verified and is never generated. Reports state this limitation
explicitly rather than implying that no disagreements exist.

**Acceptance criteria (unchanged, measured under the HUB-036 protocol):**

- A claim entailed only by two spans read together can be displayed with both citations.
- A claim with one relevant and one irrelevant ref is still rejected.
- The report stops disclaiming cross-source disagreement only once it is assessed.

### HUB-033 — Sealed verifier accepts metric-name confusion (found by v3 final)

**Status:** 🟡 Folded into HUB-036 — the metric-name confusion class
(`mrf3-055`: "specificity below 50 percent" accepted at 0.9946 from a span
stating sensitivity 33% and PPV 12%) becomes a mandatory test category in the
judge-gate evaluation instead of a standalone fix to the outgoing NLI gate.
Until the judge gate ships, treat report claims naming statistical metrics
with extra caution.

### HUB-035 — MiniMax M3 LLM-as-judge claim-faithfulness gate

**Status:** ✅ Implemented and merged 2026-08-12 behind `CLAIM_GATE` (default
`nli`) — the sealed v2 NLI verifier remains the deployed gate; nothing flips
before the v4 final (HUB-036 → HUB-034).

**Token Plan verification (2026-08-12, gate condition):** the official MiniMax
docs (`platform.minimax.io/docs/token-plan/faq` and `…/token-plan/other-tools`)
permit the Subscription Key with any tool accepting a custom Base URL + API Key
against the OpenAI-compatible (`https://api.minimax.io/v1`) or
Anthropic-compatible endpoints, with no tool-type or automation restriction
documented; pay-as-you-go is only *recommended* for production. Single-user
programmatic backend use is therefore permitted; quota exhaustion (5-hour
rolling and weekly windows) fails closed and leaves reports retryable.

**Implementation record:** `app/judge_gate.py` — OpenAI-compatible
`/chat/completions` client (`MiniMax-M3`, temperature 0, bounded response),
structured JSON verdict enforced strictly (exact keys, per-ref necessity, one
refs entry per span), served model version required and recorded per verdict,
every error path (timeout, HTTP failure, 429 and in-body quota codes, malformed
or schema-violating output) raises `VerifierUnavailable` and stays retryable.
Structural checks (supports-restates-claim verbatim, bounded refs) run locally
before any API call — the judge is never consulted for a structurally invalid
claim and cannot admit padding (an accepted verdict with an unnecessary span is
downgraded to `padding_reference` locally). Evidence is fenced as untrusted
with fence-break escaping; the system prompt forbids following instructions in
evidence. 20 offline tests (httpx MockTransport) cover the fail-closed matrix,
adversarial spans, fence integrity, key hygiene (header-only, absent from
`Config` repr), and gate selection. The Subscription Key is wired from the
gitignored `.env` via required `${MINIMAX_SUBSCRIPTION_KEY:?}` expansion.

**Problem:** the frozen DeBERTa NLI gate cannot judge joint multi-span claims
(measured: v3 final joint acceptance 0.47) and conflates metric names
(HUB-033). The replacement is an LLM judge following the standard RAG
faithfulness pattern.

**Work:**

- Judge client for MiniMax M3 (Anthropic-compatible `/v1/messages` or
  OpenAI-compatible endpoint), Subscription Key injected from the gitignored
  `.env` with required `${VAR:?}` expansion. FIRST: verify the Token Plan
  permits programmatic single-user backend use (docs bless interactive tools;
  automated-backend policy is unstated) — if not, fall back to pay-as-you-go.
- Verdict contract mirroring the current one (accepted / rejection reason per
  claim) so synthesis wiring stays small: structured JSON verdict, temperature
  0, response schema enforced, malformed output fails closed, timeout and
  quota exhaustion fail closed (Token Plan quotas are 5-hour rolling and
  weekly windows; a failed report stays retryable, matching existing
  semantics). Record the served model version string in every verdict.
- Injection hardening is part of the gate, not an afterthought: evidence
  fenced as untrusted data, judge instructed to never follow instructions in
  evidence, and the deterministic structural checks that exist today
  (supports-restates-claim verbatim, span-exactness binding) stay as
  conjunctive local guards. The judge can only reject more than the structure
  allows, never admit a claim the structural checks reject.
- Multi-span (joint and disagreement) judging with per-ref necessity ("would
  the claim survive without this ref?") to preserve the padding-rejection
  property the v3 final proved out.
- Network/privacy: research-hub and worker gain egress to the MiniMax API;
  document in `NETWORKING.md` and `ARCHITECTURE.md` that retained corpus spans
  now leave the machine for judging (deliberate exception to the local-only
  premise, operator-accepted).

**Acceptance criteria:**

- Judge verdicts are structured, fail closed on every error path, and never
  bypass the local structural checks.
- Evidence containing adversarial instructions cannot flip a verdict to
  accepted (demonstrated by tests with injected spans).
- The claim-verifier service interface consumed by synthesis is unchanged or
  simplified — no report-schema change.

### HUB-036 — Judge-gate evaluation protocol and blind set (v4)

**Status:** 🔴 Open — blocked by HUB-035; gates HUB-034 and HUB-032.

**Work:**

- New operator-annotated blind set (v4) drawn from the retained corpus per the
  established protocol (the v3 set is consumed and stays retired), covering:
  single-span entailment/neutral/contradiction, joint evidence, padding refs,
  cross-source disagreement, metric-name confusion (from HUB-033), and an
  adversarial-injection stratum (evidence containing instructions).
- Calibration on a labels-by-design set only; the blind final is measured once
  against a frozen judge configuration (prompt, schema, temperature, recorded
  served-model version).
- Because the judge is a cloud model, the seal records the model version and
  the protocol defines a re-baseline trigger: on a served-model change, re-run
  the evaluation on a fresh blind set before continuing to trust the gate.

**Acceptance criteria:** zero unsupported acceptances including the injection
stratum; padding rejection 1.0; joint and disagreement acceptance ≥0.8;
metric-confusion cases rejected; results sealed with hashes as before.

### HUB-034 — Decommission the NLI claim-support stack

**Status:** 🔴 Open — last in the pivot sequence; blocked by HUB-035 + HUB-036.

**Work (only after the judge gate passes its v4 final):**

- Remove the `claim-verifier` service, `LocalClaimVerifier`, the baked DeBERTa
  weights (large image-size win), and NLI-specific tests; port the gate tests
  to the judge interface.
- Retire the v2 sealed evaluation explicitly in the docs (never silently),
  alongside the already-consumed v3 artifacts.
- Bundle the deferred FastAPI/Starlette upgrade into this rebuild (it rides
  with the next verifier rebuild per the HUB-012 deferral).
- Standard deploy pattern: rebuild, SHA-verify, `/readyz`, sealed-artifact
  audit (attempt-11 report and registry stay byte-identical).

---

## P3 — Optional expansion after sustained usage

### HUB-024 — Add query planning and iterative research

Add query decomposition, follow-up searches based on evidence gaps, stopping criteria, and budget controls. This is the work that would justify the “deep research” label.

**Revisit trigger:** users repeatedly need broader or multi-angle synthesis than a single search query produces.

### HUB-025 — Add scheduled research jobs

Add recurring jobs only after the durable worker, idempotency, and notification paths are complete.

**Revisit trigger:** at least two topics are being rerun manually on a predictable cadence.

### HUB-026 — Evaluate relational metadata storage

**Status:** Deferred — evaluate, do not pre-provision

**Decision:** Postgres is not part of the current runtime. Introduce it only when
a measured product requirement needs shared relational state or transactions that
Redis, Qdrant, and the local SQLite document store cannot provide cleanly.

**Potential owners:**

- Users, organizations, roles, API keys, and tenant boundaries.
- Projects, saved reports, schedules, notification destinations, and preferences.
- Durable audit history for job submissions, state transitions, deletions, and
  administrative actions.
- Relational source, document-version, claim, and citation provenance that needs
  joins, uniqueness constraints, or atomic multi-record updates.
- Cross-node metadata when multiple API or worker instances need concurrent access
  to the same structured records.
- Complex retention, compliance, reporting, or operational queries that are
  awkward or unsafe across Redis keys and a single-node SQLite file.

Postgres must not automatically replace Redis for queues, leases, heartbeats, or
ephemeral coordination. It must not replace Qdrant for vector retrieval unless a
separate benchmark shows pgvector meets retrieval quality, filtering, latency,
backup, and operational requirements. Exact crawled Markdown can remain in the
document store unless the adopted use case requires shared transactional access.

**Revisit triggers:**

- A second user or tenant requires durable identity and authorization data.
- Multiple Research-Hub replicas must write shared structured state concurrently.
- Source/version/citation relationships require transactional cross-entity queries.
- Operators need durable, queryable audit history or retention enforcement.
- SQLite locking, availability, query complexity, or backup behavior becomes a
  measured constraint rather than a hypothetical concern.

**Integration plan if approved:**

1. Write an architecture decision record naming the specific data Postgres owns,
   expected scale, consistency requirements, and why SQLite is insufficient.
2. Define normalized tables, foreign keys, indexes, retention rules, and an
   explicit boundary with Redis, Qdrant, and retained document content.
3. Add a pinned Postgres image as an opt-in Compose profile first; require a
   generated secret, internal-only networking, a healthcheck, and resource limits.
4. Add a pinned async database driver, connection-pool configuration, schema
   migrations, readiness behavior, and secret-safe diagnostics to Research-Hub.
5. Implement repository/service interfaces so application logic is testable
   without embedding SQL throughout API and worker code.
6. Build an idempotent migration/backfill path from existing SQLite/Redis metadata,
   including validation counts and a documented rollback window. Do not dual-write
   without an explicit reconciliation design.
7. Add transaction, concurrency, migration, degraded-readiness, backup, and restore
   tests before making Postgres part of the default profile.
8. Measure idle memory, startup time, query latency, and backup/restore time; update
   the topology and recovery documentation with observed results.

**Acceptance criteria if integrated:**

- Postgres has one documented owner and contains production data required by an
  implemented feature; it is not an empty convenience container.
- Migrations run deterministically on a new database and upgrade retained data
  without loss; rollback and restore procedures are tested.
- Credentials have no checked-in fallback, the port is not published, and logs or
  health responses do not expose secrets.
- Dependency failure has deliberate capability-specific readiness behavior and
  does not restart an otherwise live API process.
- Redis and Qdrant responsibilities remain explicit, with no accidental duplicate
  source of truth.
- The default Compose profile includes Postgres only if the core research/RAG path
  requires its owned data; otherwise it remains an optional profile.

### HUB-027 — Evaluate a knowledge graph layer

Do not add a graph until hybrid retrieval, reranking, citation validation, and a retrieval evaluation set are in place.

**Revisit trigger:** measured failures are primarily relationship/multi-hop failures that hybrid retrieval cannot solve.

### HUB-028 — Evaluate multi-tenancy

Add authentication, tenant-scoped storage, quotas, and rate limits only if the service is intentionally shared beyond a trusted single user.

**Revisit trigger:** a second user or project requires hard data isolation.

### HUB-029 — Evaluate multi-machine federation

Defer GPU routing and shared multi-machine state until one-node utilization or availability becomes a measured constraint.

**Revisit trigger:** sustained jobs exceed the single worker's throughput or downtime becomes unacceptable.

### HUB-030 — Evaluate fine-tuning

Do not fine-tune from the corpus by default. First establish data quality, licenses, evaluation criteria, and a demonstrated failure that retrieval cannot solve.

**Revisit trigger:** a stable evaluation set shows repeated domain behavior failures despite good retrieval and prompting.

---

## Recommended delivery sequence

### Milestone 1 — Safe restarts (complete)

HUB-001 ✅, HUB-002 ✅, HUB-003 ✅, HUB-004 ✅, HUB-005 ✅, HUB-006 ✅

**Exit condition:** restarting the stack preserves data, health diagnostics work, and the default deployment is not broadly exposed.

### Milestone 2 — Durable ingestion (complete)

HUB-007 ✅, HUB-008 ✅, HUB-009 ✅, HUB-010 ✅, HUB-011 ✅

**Exit condition:** jobs survive process failure, repeated ingestion is idempotent, and the corpus can be rebuilt from retained documents.

### Milestone 3 — Operable daily service (complete)

HUB-012 ✅, HUB-013 ✅, HUB-014 ✅, HUB-015 ✅, HUB-016 ✅

**Exit condition:** builds are reproducible, recovery is tested, contracts are enforced, and failures are diagnosable.

### Milestone 4 — Better answers (open: HUB-036, HUB-034, HUB-032)

HUB-017 ✅, HUB-018 ✅, HUB-019 ✅, HUB-020 ✅, HUB-021 ✅, HUB-022 ✅, HUB-023 ✅, HUB-031 ✅, HUB-032 🔴 (re-scoped), HUB-033 🟡 (folded into HUB-036), HUB-034 🔴, HUB-035 ✅ (merged behind config; NLI stays the deployed default), HUB-036 🔴

**Exit condition:** retrieval quality is evaluated, prompts and citations are hardened, and each research job produces a useful evidence-backed artifact.

### Milestone 5 — Expansion only when earned

HUB-024 through HUB-030 — all deferred behind explicit revisit triggers; none tripped.

### Recommended order for the remaining open work (2026-08-12, post-pivot)

1. **HUB-003 / HUB-006 / HUB-013 / HUB-012 / HUB-031** — ✅ done 2026-08-11 (see statuses above).
2. **HUB-035** — ✅ done 2026-08-12 (Token Plan backend use verified permitted; judge gate merged behind `CLAIM_GATE=nli` default).
3. **HUB-036** — judge-gate evaluation protocol and v4 blind set (operator annotates).
4. **HUB-034** — decommission the NLI stack and deploy the swap (bundles the FastAPI/Starlette upgrade).
5. **HUB-032** — cross-source disagreement on the judge gate, measured under HUB-036.

**Exit condition:** each expansion is justified by measured usage or a documented limitation, not by architectural possibility.
