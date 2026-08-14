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

**Status:** ✅ Done 2026-08-12 — closed by the v4 final plus the HUB-034
deploy. Gate-side: joint two-span claims 25/25 accepted, cross-source
disagreement 20/20 accepted, one-relevant-plus-one-irrelevant-ref claims
rejected 20/20 under the sealed v4 evaluation. Report-side (deployed with
HUB-034): synthesis drafts bounded cross-document span pairs, verified pair
claims display both citations, and the standing disclaimer is emitted only
when no cross-document pair was available for assessment.

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

**Status:** ✅ Resolved by the v4 final (2026-08-12): the judge gate rejected
all 10 metric-confusion cases (each as `contradiction`), and calibration
rejected all 8 designed metric-confusion kinds. The class remains a mandatory
stratum for any future re-baseline set. Original record follows.

**Superseded status:** 🟡 Folded into HUB-036 — the metric-name confusion class
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

**Status:** ✅ Done 2026-08-12 — the one-time v4 blind final PASSED every gate.

**Final record (results `7c9ed9ac…`, content seal `21465f6e…`, labels seal
`632c30c3…`, served model `MiniMax-M3` throughout, judge config frozen before
the run):** zero unsupported acceptances (injection stratum included);
padding rejection 20/20 (19 `padding_reference`); joint acceptance **25/25**
(v3 NLI: 0.47); disagreement acceptance **20/20** (v3 NLI: 0.6); metric
confusion 10/10 rejected; single-span 15/15 accepted, 15/15 neutral and 10/10
contradiction rejected; injection 13/15 rejected with the only two
acceptances being operator-labeled entailment (incl. one whose payload
ordered rejection — the judge followed the evidence, not the instruction).
Operational note: ~10 of ~140 metered calls returned malformed output and
were retried; every retry failed closed first, and each case was recorded at
most once. The gate is measured fit to replace the NLI stack: HUB-034 is
unblocked.

**Progress record (branch `hub-036-judge-eval-v4`):**

- Operator approved the v4 protocol: 130 blind cases across eight strata
  (single entailment 15 / neutral 15 / contradiction 10, joint 25, padding 20,
  cross-source disagreement 20, metric-name confusion 10, adversarial
  injection 15), with the cloud re-baseline trigger and gates as specified.
- Calibration (labels-by-design, 60 cases incl. injection and HUB-033
  metric-confusion kinds, reusing v3 joint/padding/neutral designs) measured
  against the real MiniMax API: **60/60 as designed** — joint 10/10 accepted
  (the v3 NLI joint bottleneck is gone at calibration level), padding 10/10
  rejected as `padding_reference`, metric confusion 8/8 rejected, injection
  12/12 (9 accept-forcing payloads on unsupported claims all rejected; 3
  supported claims retained incl. a reject-forcing payload). No tuning
  applied; the judge config freezes exactly as HUB-035 deployed it. One field
  fix ships with this branch: the M3 endpoint inlines a leading
  `<think>` block in message content, which the gate now strips before its
  strict JSON parse (fail-closed semantics unchanged; measured live —
  the pre-fix gate failed closed on 100% of responses).
  MiniMax reports the served model only as `MiniMax-M3` (no finer version
  string); the seal records this as the drift-detection granularity.
- v4 blind set drafted from the retained corpus (deterministically verified:
  exact chunk substrings, chunk SHA-256 bindings, cross-document pairs, no
  reuse of calibration spans or consumed-v3 claims), reviewed case-by-case,
  frozen and shuffled (seed 20260812), content sealed
  `21465f6e…`. Annotation package: `judge_annotation_package_v4.json`.
- Next: operator annotates blind → `seal_judge_annotations_v4` (labels seal +
  judge freeze) → ONE-TIME `run_judge_final_v4` (checkpointed, refuses
  re-runs, aborts on served-model drift).

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

**Status:** ✅ Done — operator-authorized and deployed 2026-08-12.

**Deploy record:** `claim-verifier` service, `LocalClaimVerifier`, the baked
DeBERTa weights, and NLI-specific tests removed (image ~0.4 GB, previously
multi-GB with torch; `torch`/`transformers`/`sentencepiece` dropped from the
lockfile). The judge (`app/judge_gate.py`) is the only claim gate; config
requires `MINIMAX_SUBSCRIPTION_KEY` at startup. FastAPI 0.115.5 → 0.141.1
(Starlette 1.6.0) bundled per the HUB-012 deferral; hashed lockfile
regenerated, `pip check` clean. The v2 seal is retired explicitly in
`docs/CURRENT_STATE.md` (fixtures retained as audit record); the v4 seal is
active. Report-side HUB-032 behavior shipped (cross-document pair drafting
ported from the archived v3 branch, judged with per-ref necessity; the
disagreement disclaimer survives only when no cross-document pair was
available). Deploy verified: compose topology seven containers with
`hub-claim-verifier` removed, deployed `judge_gate.py`/`synthesis.py`/
`research.py`/`config.py` SHA-256-identical to the tree in hub and worker,
`/readyz` all-true, attempt-11 report and registry byte-identical
(`068d60b2…`, `d6748d76…`) at 67 documents / 13 reports, 205 tests green in
the deployed image.

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

### HUB-024 — Adaptive query planning and iterative research

**Status:** 🟢 DONE — 2026-08-13. Stages 1–3 complete, deployed, and measured
at scale. **Acceptance met**: 25 retained sources across 23 distinct domains
against the single-query baseline's 6/6 on the same topic and parameters, with
zero off-topic acquisition and 12 displayed findings against the baseline's 6.
Design: `PRDs/hub-024-query-planning.md` (16 arXiv abstracts for the original
design, 11 more for the stopping/query-quality redesign; citations in the PRD).

**Stage 3 measurement (job `b379fb3e` vs baseline `24a8a471`).** Same topic,
`depth=6`, `max_sources=12`, `per_domain_limit=2`. Twelve queries over three
rounds retained 15 sources across 14 domains against the baseline's 6/6. Not
cost-neutral on crawls: `depth` is a per-round allowance, so the planned cap
was 18 crawls to the baseline's 6. Judge calls stayed inside the unchanged
drafting caps. Admission discriminated at the margin (one candidate refused
`redundant` at cosine 0.8549 against the 0.85 bar).

**Finding 1 — novelty metric was mis-specified; fixed.** Rounds measured
novelty 1.0 / 1.0 / 0.983 and stopped on the `max_rounds` rail, never on
saturation. Novelty was computed over the whole policy-accepted candidate pool
(59–93 URLs/round) while a round fetches only `depth` documents, so the ratio
pins near 1.0 whatever the true saturation. The denominator is now the round's
top-`depth` fetch window, with the full pool kept in provenance as `pool`.
That correction made the number honest but still not useful, and the metric
was retired outright the same day — see "Stage 2 as built, then redesigned".

**Finding 2 — relevance drift across rounds; fixed by the two-sided bar.** The
planner proposed queries naming non-existent tools (`crdb-migration-tools`,
`luupgtool`), and later facets pulled in HAProxy docs, pgBackRest release
notes, a Barman manual and Datadog/Netdata monitoring pages that do not
address the topic. Breadth rose while relevance fell, and the planned report's
verified findings are thinner and less on-topic than the baseline's.
Marginal-distinctness admission selects for *divergence*; at the tail the most
distinct candidate is often the least relevant. Indicated fix: admit on a
relevance floor (cosine to the topic above a minimum) **as well as** the
distinctness ceiling, so a facet must be both new and on-topic. This is the
PRD's named load-bearing risk, now observed rather than hypothesised.

**Stage 1 as built.** `app/query_plan.py` proposes candidate facets in one
bounded local-LLM call, embeds `[topic, *candidates]` in a single
`embed_batch`, and greedily admits a candidate only while its max cosine to
the admitted set is below `PLAN_FACET_DISTINCT`. The topic is always facet 0,
so a plan can only widen the pre-planning search, never replace it. Facet
results are round-robin interleaved (concatenating would let facet 1 consume
the whole `depth` cap) and passed through the existing `apply_source_policy`
in **one** pass — which is what makes cross-facet canonical dedup, allow/block
lists, per-domain limits and freshness apply to every sub-query by
construction rather than by reimplementation. SSRF vetting is per-URL in
`crawl_one` and therefore unchanged. Crawl count is still bounded by `depth`,
so breadth arrives at constant crawl and judge cost.

**Stage 2 as built, then redesigned (2026-08-13).** A round loop wraps search
and crawl only; ingestion still runs once, unchanged, over the accumulated
crawl results — restructuring it would risk the lease/heartbeat/idempotency
semantics the acceptance criteria require untouched. `seen_canonical` dedups
across rounds, so no document is fetched twice, and a collapsed plan never
opens a second round.

Stopping was rebuilt after a second prior-art pass (11 further arXiv abstracts
read; citations in the PRD). `PLAN_NOVELTY_MIN` is **retired**: document
novelty could never fire, because admitting only distinct queries guarantees
distinct documents, so the ratio pins near 1.0 whatever the true state of the
research (observed live at 1.0 / 1.0 / 0.983). Saturation is now measured in
facet-coverage space per RAVine (2507.16725): a facet is covered once a
retained document came from its own search, recomputed over every issued query
each round. Two threshold-free rules — `coverage_complete` (every facet
answered) and `coverage_plateau` (a round raised the count by zero) — with
`PLAN_MAX_ROUNDS` as the rail.

**Rounds therefore exist to finish covering the admitted plan, not to invent
new angles**, which makes one round the normal case. This is load-bearing: if
completion did not end the loop, a gap pass that keeps inventing facets would
raise the count forever — novelty's runaway in a different hat. The LLM gap
pass is now **advisory**, checked only after the arithmetic signal, because
sufficiency judgements measure poorly (RaCGEval 2411.05547, 46.7%) and
unaligned models default to answering rather than declining (2507.04976).

Recorded stop reasons: `single_round`, `coverage_complete`,
`coverage_plateau`, `max_rounds`, `budget`, `coverage`, `planner_unavailable`.

**Query quality (2026-08-13).** Admission is two-sided: a facet must be both
distinct from the admitted set and above `PLAN_FACET_RELEVANCE` to the topic,
fixing the observed drift into HAProxy/Barman/monitoring pages. Pre-issue QPP
(Diaz 1507.03928) rejects run-together identifiers, out-of-range word counts
and non-alphabetic soup before a search is spent — tuned not to fire on
`PostgreSQL`, `WAL`, `pg_upgrade` or `libc/libssl`. A collection-IDF predictor
was deliberately not built: QPP predicts against the collection being
searched, and sub-queries search the web while our document frequencies are
local. Planner and gap prompts now forbid run-together tokens and inventing
tool names.

Every planner failure mode degrades to the single-query plan or ends the
rounds with the reason recorded, never a failed job. 293 tests pass
in-container, zero skips.

**Remaining, none blocking:** (1) the whole measurement is one topic — a
second topic would test whether `PLAN_FACET_RELEVANCE = 0.55` transfers;
(2) span selection, not the claim gate, is now the quality lever: one
displayed finding was promotional filler that passed the gate because it is
faithfully entailed, and the judge verifies faithfulness rather than
informativeness; (3) facet coverage grades its own homework — a facet nobody
proposed is invisible to the stop signal (recorded as the PRD's open thread).

**Trigger record:** a research job issues exactly one SearXNG query, so the
retained corpus for a report contains only what that phrasing surfaced. The
narrowness is on the acquisition side, not retrieval (hybrid retrieval already
measures hit@4 `1.0` on the exact-term manifest, HUB-017). It also caps the
cross-source machinery deployed in HUB-034: pair drafting can only find
disagreements between sources that were crawled, and one query tends to return
sources that agree.

**Methodology (breadth is emergent, never a fixed count):**

- **Marginal-distinctness admission.** One bounded local-LLM call proposes
  candidate facet queries; each is embedded with the deployed
  `nomic-embed-text` and admitted only if its max cosine similarity to the
  admitted set is below `PLAN_FACET_DISTINCT`. Breadth is whatever survives
  the threshold — a narrow topic admits one facet and behaves exactly as
  today; `PLAN_MAX_FACETS` is a safety rail, not the mechanism. (ScoreGate's
  threshold-not-top-K principle lifted from chunk selection to planning;
  Adaptive-RAG's complexity routing falls out of single-facet collapse.)
- **Gap-driven rounds.** After each round, one bounded call reads a per-facet
  coverage summary (retained documents, distinct domains) and names what is
  still uncovered; only those gaps become the next round's queries (KiRAG).
- **Stopping — coverage first, rails last.** *(Original design; superseded
  2026-08-13 — novelty saturation was implemented, measured and retired
  because it cannot fire under distinctness-based admission. Coverage was
  promoted to primary, in facet space, and the LLM gap pass demoted to
  advisory. See the status section above.)*

**Prior-art traps the design explicitly avoids:** fixed depth × breadth
parameters (Static-DRA's own limitation); expecting breadth to raise report
quality (DeepWeb-Bench: retrieval is 12–14% of errors, derivation/calibration
exceed 70% — so acceptance measures corpus breadth, not report quality, and
the judge gate stays the quality guard); redundant tool calls (HotelQuEST —
canonical-URL dedup across facets before crawling is mandatory).

**Acceptance criteria:** more distinct domains and represented sources per
report than the single-query baseline on a fixed topic set, recorded not
asserted; a single-facet topic issues exactly one search; every sub-query
inherits source policy and SSRF vetting with canonical dedup across facets and
rounds; judge calls per report stay bounded by existing drafting caps; worker
lease/retry/idempotency semantics unchanged; plan provenance (facets, queries,
new-document yield, stop reason) recorded in job progress;
`REPORT_QUERY_PLANNING=false` reproduces current behavior exactly.

### HUB-037 — Judge verdict parsing fails when MiniMax leaks JSON into its reasoning stream

**Status:** ✅ DONE — fixed and verified 2026-08-13. All three topics that
failed in the evaluation campaign now complete on the first attempt, with zero
`malformed_output` occurrences across 27 verdicts.

**Root cause.** MiniMax M3 defaults to `thinking: {"type": "adaptive"}` when
the parameter is omitted — which it was — and delivers that reasoning inline
in `content`. The model sometimes begins emitting the JSON verdict while still
reasoning, so stripping the reasoning also removed the object's opening and
the verdict could not parse.

**Fix, in two parts.** `reasoning_split: true` routes reasoning to its own
`reasoning_content` field so `content` is the verdict alone. Deliberately
*not* `thinking: {"type": "disabled"}`: the sealed v4 blind evaluation
measured this gate with reasoning ON because that is the default, so
disabling it would change how the judge decides and the seal would no longer
describe the deployed gate. Splitting changes only delivery — confirmed by the
seal's recorded `system_prompt_sha256` being unchanged.

That alone was not sufficient. With reasoning separated the leak simply moved
(`{"accepted` landed on the reasoning side), the third distinct cut point in
as many observations. Pattern-matching each shape was chasing a moving target,
so the parser now restores the known prefixes of the fixed verdict schema and
accepts a reconstruction only when it parses to exactly the three expected
keys. Values remain the model's own; anything that does not validate fails
closed.

**Verified:** 303 tests green in-container; v4 seal byte-identical
(`762e7a19…`) with every recorded hash intact and 130/130 annotations
well-formed; attempt-11 artifacts byte-identical; three previously-failing
topics re-run clean, with zero `malformed_output` across 27 verdicts.

**Follow-up, same day — the fix introduced a second failure mode.** With
reasoning routed to its own field, a reply can carry `reasoning_content` and
no `content` at all when deliberation consumes the token budget; the code
assumed content always exists and raised `KeyError`, surfacing as another
`malformed_output`. It only appeared under the HUB-038 campaign, after this
item had been marked done. Missing or blank content now fails closed under a
distinct `empty_content` reason logging `finish_reason` and reasoning length,
and `RESPONSE_MAX_TOKENS` rose 2048 → 4096 because reasoning counts against
the budget even when split out. Two further live reports completed first
attempt. The lesson worth keeping: "three clean runs" was not enough evidence
to close a probabilistic failure.

### HUB-038 — Source relevance screening: calibrated and ENABLED

**Status:** ✅ DONE 2026-08-13. Enabled at `PLAN_SOURCE_RELEVANCE = 0.54`,
calibrated on a 494-document labelled reference set.

**Method.** Every document retained across all 38 jobs (494 job/document
pairs, 20 topics) was scored locally and labelled `on_topic` / `marginal` /
`off_topic` by MiniMax using a purpose-built prompt — deliberately not the
sealed claim-gate prompt, which judges faithfulness and would conflate two
questions. 280 on-topic, 113 marginal, 62 off-topic; 38 unparsed and 1 error
excluded. **A reference set, not ground truth and not a seal:** among 44
documents labelled more than once, 7 disagreed (16%), so treat differences
under a few points as noise.

**The evaluation overturned two conclusions previously drawn by eyeballing
two unlabelled runs.**

1. *The screen works.* Topic-anchored AUC is **0.875** on-vs-off, per-topic
   median **0.975** across the six topics carrying a usable negative sample.
   The earlier "cannot separate" verdict was an artefact of reading raw score
   lists without labels.
2. *Facet anchoring was a mistake and is reverted.* On identical rows it
   scored **0.741 against the topic's 0.857**, and 0.426 — worse than random —
   on the Postgres topic. It won only on the single ambiguous topic it had
   been tuned against: overfitting to n=1.
3. *Windowed probes are not the improvement they were credited as.* Opening
   0.872 versus windowed 0.875 is noise at n=342. Kept because it costs only
   local embeddings and is more robust to boilerplate openings, but no longer
   claimed as the fix.

**Operating point.** 0.54 keeps 98.2% of on-topic documents (5 lost of 280)
while removing 35.5% of off-topic ones, with no adequately-sampled topic below
91% recall. Chosen for recall over aggression: dropping a correct source costs
more than keeping a stray one. Dropping nothing on a clean run is expected —
off-topic documents are ~14% of a corpus.

**The "ambiguity failure" was not one.** On "Transformer efficiency
improvements" the reference labeller independently marked electrical-transformer
vendor pages `on_topic`, agreeing with the screen. The topic genuinely reads
both ways; the earlier report of an inverted ranking assumed an ML sense the
topic never stated. That is an underspecified-input issue, not a screen defect.

**Recorded as future work.** Facet anchoring is genuinely better on ambiguous
topics (0.972 vs 0.827 there). Routing by *detected* ambiguity — a bimodal
score distribution within one corpus — is the obvious refinement, left
unguessed.

### HUB-039 — Collapse and multi-round machinery do not engage in practice

**Status:** ✅ RESOLVED 2026-08-13 by correcting the documentation rather than
forcing the behavior. Both observations were real; neither turned out to be a
defect worth engineering around.

**Collapse — claim withdrawn, then partially reinstated.** Collapse fired in 0
of 8 evaluation jobs, including a deliberately narrow topic that admitted 4
facets and retained 21 sources, so the PRD bullet claiming narrow topics issue
exactly one search was struck through as measured false. **Corrected
2026-08-13:** it subsequently fired once, on "Zero downtime blue green
deployment strategies" (`facets=1`, `stop=single_round`). Collapse is
therefore rare rather than impossible, and the honest statement is that it
cannot be relied on as a cost-control property — not that it never happens.

**Rounds — kept as a documented safety net.** All 8 jobs stopped
`coverage_complete` in round 1, so the gap pass and `PLAN_MAX_ROUNDS` never
executed. They are not dead code: they fire when a facet's own search returns
nothing, which the unit suite exercises directly
(`test_an_uncovered_facet_opens_a_gap_driven_second_round`,
`test_a_round_that_covers_nothing_new_stops_the_research`). At a generous
crawl allowance round 1 simply covers the plan, which is the desired outcome.
Deleting the machinery would remove recovery for the thin-coverage case for no
benefit, so it stays — now documented as rarely-reached rather than as the
normal path.

**No code change.** The fix was that the docs claimed more than the system
does.

### HUB-040 — Cross-source disagreements: the corpora contain no conflicts

**Status:** 🟡 REFRAMED 2026-08-13. The drafting fix is deployed and correct.
The remaining cause is **not** pair selection — that diagnosis was stated
twice and is wrong. Measurement shows the corpora simply contain nothing to
find, which makes this an acquisition problem, not a synthesis one.

**What was fixed and stays fixed.** Pair drafting used to ask, in one call,
for either a conflict or a combined fact, and always took the easier branch.
Conflict is now decided first in its own bounded call and the claim is drafted
on the decided branch. That was a real defect and is resolved.

**The detector works.** Checked against hand-written cases it scored 5 of 5,
correctly flagging a negation ("sequences are replicated" vs "does not
transfer sequence values"), an inverted claim, and conflicting numbers, while
correctly rejecting two merely-related pairs.

**The corpora contain no conflicts.** Cross-document span pairs were sampled
**uniformly at random** — deliberately ignoring the production ranking, so the
estimate carries no selection bias:

| Corpus | Sampled pairs | Conflicts |
|---|---|---|
| Microservices versus monolith (chosen as contested) | 250 | 1, and that one a false positive |
| Postgres logical replication | 200 | 0 |

The single positive paired "microservices don't reduce the complexity of an
application" with "one of the biggest advantages … is simplicity" — both
saying microservices are more complex. They agree.

**So pair selection was never the bottleneck.** No selector can surface a
conflict from a corpus that does not contain one, and building a
contrast-based selector would have changed nothing. The earlier reasoning —
that shared-vocabulary ranking picks combinable pairs — is plausible and still
unrefuted, but it is not what is stopping disagreements, because the pairs it
passes over do not conflict either.

**The likely real cause: acquisition seeks consensus.** A search engine ranks
by relevance to a query, so the top results reflect the dominant framing of a
topic. Every facet asks about the topic; none asks for the minority view.
The corpus is assembled to be representative, and disagreement requires it to
be adversarial.

**Testable next step, unvalidated:** have the planner admit a deliberately
contrarian facet — criticism of, arguments against, when it fails — so the
corpus contains the dissenting source by construction. This fits the existing
facet architecture rather than adding machinery, and the base-rate probe
(`conflict_base_rate` methodology above) is exactly how to tell whether it
worked: rerun it on a corpus built with a contrarian facet and see whether the
rate moves off zero.

**Report wording is now actively misleading.** "No material source
disagreements were identified" reads as a finding about the sources. It is a
statement about a corpus assembled not to contain any.

### HUB-041 — `/rag` returned nothing at its default context budget

**Status:** ✅ DONE 2026-08-13.

`RAGRequest.max_context_tokens` defaulted to **3000** while
`answer_reserve_tokens` is **2048**, leaving under 1000 tokens for the system
prompt, the question and the evidence combined. Evidence never fit, so a
default `/rag` call always answered "No relevant information fits within the
model context budget" with zero sources. Measured: 0 sources at 3000, 3 at
6000, 4 at 12000, with retrieval healthy throughout.

**Fix.** The default is now `None`, meaning the model's full context, and the
budget is `min(requested or model_context, model_context)`. A fixed default
could never be correct, because it has to exceed an answer reserve that is
deployment configuration. Explicit values, including deliberately small ones,
are still honoured.

The failure message now names the numbers and the way out — how many passages
were retrieved, the budget, the reserve, and that raising or omitting
`max_context_tokens` fixes it — instead of stating only that nothing fit.

### HUB-042 — Search engines exhaust under sustained job volume

**Status:** ✅ DONE 2026-08-13 — ADR-002 stages 1 and 2 deployed and verified.
Pacing and pre-crawl ranking cut wasted crawls (36 fetched for 4 kept became
17 for 17 on the worst topic), and a keyed Serper fallback now covers a fully
blocked engine pool, proven by stopping SearXNG and watching a job complete on
`{"serper": 4}`. Previously: 🟡 MITIGATED 2026-08-13 by widening the engine pool and surfacing
suspension. Not closed: the underlying quota problem is unsolved, and one
follow-up is now open (see the calibration note below).

**Root cause was worse than reported.** `searxng/settings.yml` disabled bing,
google, brave and startpage, so despite the client requesting
`duckduckgo,startpage,brave`, the stack ran on **duckduckgo alone**. A single
CAPTCHA therefore took down all acquisition, and every upstream appeared
suspended at once:

```
brave      Suspended: too many requests
duckduckgo CAPTCHA
startpage  Suspended: CAPTCHA
google cse Suspended: too many requests
```

**Mitigation.** Enabled bing, brave, startpage, mojeek and qwant alongside
duckduckgo — keyless engines only; google needs an API key and suspends
immediately without one. The engine list is now `SEARXNG_ENGINES`, so it can
be changed without a rebuild. `SearXNGClient` also logs
`searxng_engines_unavailable` with the per-engine reason whenever a search
returns nothing while engines are unresponsive, so suspension is no longer
indistinguishable from an empty web.

**Verified with five of six engines still blocked.** A live search returned 10
results via bing while brave, duckduckgo, qwant, startpage and google cse were
all CAPTCHA'd or suspended, and a full research job completed on the first
attempt. That is the whole point: one survivor keeps acquisition alive.

**Load-tested over six fresh topics (2026-08-13).** Acquisition succeeded 6 of
6 while the pool degraded from three responding engines to bing alone; before
widening, one CAPTCHA killed the job outright. Five of six reports completed;
one failed on `empty_content`, cleanly and retryably.

**The widened pool trades precision for availability.** Drop rates rose sharply
(0–32 documents per job) because bing and brave return lower-precision results
— for "Rust async runtime tokio async-std" they returned dictionary definitions
of "rust" and "async" plus a WebMD page. The source screen kept `rust-lang.org`
and Wikipedia and dropped `dictionary.com` and `webmd.com`, which is it working
exactly as intended. The earlier concern that HUB-038's threshold had been
invalidated by the engine change is **not** borne out: the screen is more
load-bearing now, not miscalibrated. Rebuilding the reference set on the new
mix would still be worthwhile, but it is no longer urgent.

**`google cse` disabled 2026-08-13.** SearXNG's engine takes no API key: it
scrapes the JSONP CSE-element endpoint with a hardcoded third-party CX shared
by every install, so it was permanently rate-limited and reported "too many
requests" on every search ever made. Removing it recovered a cleaner picture —
35 results across duckduckgo, bing and brave with only qwant and startpage
blocked.

**Strategy decided in `docs/ADR-002-search-provider-strategy.md`.** The
official Bing Search API was retired 2025-08-11, so bing — our most productive
engine — is a scraper with no official fallback. The ADR's decisive criterion
is that this stack needs URLs and snippets, not page content: it already owns
crawling, so providers bundling extraction (Tavily, Exa, Firecrawl) would be
paid for output we discard.

Stage 1, free and untried: per-engine outgoing limits, pacing within a job,
and screening search results on title and snippet **before** crawling. Stage 2,
only if stage 1 measurement still shows failures: Serper as a fallback behind
SearXNG. Brave Search API is rejected pending an operator licensing decision —
its terms restrict storing API results, and this system exists to build a
persistent corpus.

### HUB-043 — Retrieval is job-scoped; the corpus cannot be queried as one base

**Status:** ✅ DONE — implemented and verified 2026-08-13. See
`docs/CURRENT_STATE.md`, "One retrieval path".

`ScopedRetrievalService.retrieve(job_id, topic)` took a job id as its first
argument, so hybrid dense+BM25 fusion, per-source caps and the FTS5 needle
channel ran **only** during one job's report synthesis. `/query` and `/rag`
used a separate, simpler, dense-only path.

The system therefore had two retrieval implementations and the better one
could not see the corpus: 679 documents sat physically in one index and
logically in 62 silos. The exact-term channel that HUB-017 measured lifting
hit@4 from `0.6923` to `1.0` — the one that recovered a DOI no reranking
could reach — was unavailable to every corpus-wide query.

This was the gap between the system and the stated goal of a searchable,
cross-referenced knowledge base, and a prerequisite for HUB-044 through
HUB-046.

**Approach.** Make `job_id` an optional filter rather than a required
argument, unscope the lexical channel, and route `/query` and `/rag` through
the same service. This *deletes* a duplicate implementation rather than
adding one. Note it deliberately crosses the PRD boundary that held `/query`
and `/rag` unchanged — that boundary protected a regression surface which is
now the thing to fix.

**Acceptance:** one retrieval path; corpus-wide queries use dense+BM25+RRF;
job-scoped report synthesis is byte-identical to today on a retried report.

**Outcome.** All three met, with the third criterion corrected: "byte-identical
synthesis" is not checkable because synthesis is LLM-driven. The deterministic
thing underneath it was checked instead — retrieval fingerprinted over the
ordered `(document_id, chunk_index, score, channels, rrf_score)` list for the
six largest jobs (360 chunks) under the deployed image and the new one, all
six digests identical.

Two things the analysis had not anticipated:

- **The filters had to move, not just widen.** `topic_filter`/`tags_filter`
  were Qdrant payload conditions. Left there, they would have narrowed the
  dense channel while the lexical channel searched the whole corpus. They now
  resolve to a document scope from `job_sources`, which is also the more
  correct source: a page found by two jobs on different topics belongs to
  both, and only `job_sources` records the second.
- **Unscoping made row width matter.** `documents_for_job` read whole rows;
  corpus-wide that meant decoding 44 MB of markdown per query to obtain three
  identity columns. Both accessors are now projected.

Live: "how does reciprocal rank fusion combine rankings" now draws 64 chunks
from 33 sources across jobs; "kubernetes observability tracing" 65 from 42.
Unblocks HUB-044 through HUB-046.

### HUB-044 — Retrieval breadth is a single post-cap total, not a curve

**Status:** ✅ DONE — measured and baselined 2026-08-13. See
`docs/CURRENT_STATE.md`, "Retrieval breadth measured".

**Two corrections to this entry, made before the work started.**

*Breadth was not untracked.* `RetrievalDiagnostics` has computed
`sources_available` and `sources_represented` since HUB-024; `app/synthesis.py`
writes both into job progress and `hub_report_retrieval_items` carries both as
labelled histogram observations. The original wording ("nothing tracks it")
was wrong.

*The motivating case reproduces, one stage later than recorded.* "15 chunks
from 7 of 22 sources" (job `bc3e5297`) does not reproduce at the retrieval
stage, which now selects **44 chunks from 15 of 22**. It reproduces almost
exactly at the stage the original observation was actually made — the packed
evidence the model reads — which is **15 chunks from 8 of 22**. (This entry
first recorded the retrieval-stage number alone and concluded the case was
stale; corrected 2026-08-13 after measuring every stage. The coverage work
stands; it measures one stage too early. See HUB-049.)

**What was actually missing.** The tracked number is a single post-cap total.
It is taken after `max_chunks_per_source` has already forced diversity, so it
cannot distinguish breadth the ranking found from breadth the cap
manufactured; it says nothing about how breadth grows with k; and it never
existed at all for the corpus-wide path HUB-043 opened one day earlier.
Neither retrieval benchmark reported breadth, so no chunking or retrieval
change could be judged on it.

Graph-Aware Late Chunking (arXiv 2603.22633) argues ranking metrics
systematically undervalue breadth: content-similarity methods scored the
highest MRR while always retrieving from a single document section, and
structure-aware methods reached up to 15.6x more sections. It proposes
coverage metrics (SecCov@k, CS Recall) alongside MRR/Recall@k.

**Approach.** One shared metric (`tests/coverage_at_k.py`) reported beside
recall in both retrieval benchmarks, plus a live read-only baseline command
over both scopes. The unit is the **document**, not the paper's section:
chunking is a fixed 800/100 split, so `chunk_index` marks position rather than
structure and section coverage would restate `chunks_selected`. Every case is
retrieved twice — at the deployed cap and with the cap lifted — because the
gap between those two curves is the question the existing number cannot
answer.

**Acceptance:** coverage@k reported next to recall in both benchmarks;
a baseline recorded on real jobs and real corpus-wide queries; retrieval
behaviour unchanged.

**Outcome.** All three met. Nothing under `app/` changed — this item adds a
number and touches no deployed module. Coverage gates nothing anywhere, and a
test asserts the gate set is still exactly the two relevance gates: coverage
is maximised by returning one chunk per source, so a target on it would reward
shredding every multi-chunk argument.

Three findings the entry had not anticipated:

- **The candidate pool, not the cap or the ranking, is the binding ceiling.**
  In five of six jobs the 120-candidate dense+BM25 pool reaches fewer sources
  than the job has embedded: p-hacking **8 of 19**, kafka 13 of 20,
  microservices 15 of 22, kubernetes 39 of 55, redis 18 of 20. No selection
  policy can recover a source the pool never proposed. That reframes HUB-045.
- **The cap is load-bearing corpus-wide, and the paper's failure mode
  reproduces exactly.** On "how does reciprocal rank fusion combine rankings"
  the uncapped fused ranking draws its first **eleven** chunks from a *single*
  document; coverage@8 is 1 source uncapped and 5 capped. Pooled corpus-wide
  saturation@16 is `0.712` capped against `0.562` uncapped.
- **`sources_available` is not the ceiling it looks like.** It counts retained
  rows including deduplicated sources with no Qdrant chunks (HUB-043), so job
  `48c9247e` shows 56 available where only 55 are reachable in principle and
  39 in practice.

Unblocks HUB-045 with a number it can be judged on — and points it at pool
composition first.

### HUB-049 — The context budget, not retrieval, decides what a report reads

**Status:** 🟡 IMPLEMENTED BEHIND A FLAG, NOT ADOPTED — `EVIDENCE_PACKING`
defaults to `rank`. Measured effect is small; the measurement that could
justify flipping it does not exist yet (HUB-047).

**The finding.** Raising `REPORT_RETRIEVAL_CANDIDATES` from 120 to 600 lifts
pool source reach substantially on real jobs — kafka 13 → 20 of 20,
microservices 15 → 21 of 22, p-hacking 8 → 15 of 19 — and the packed evidence
the model actually reads **does not move**: ~19 chunks from 6–8 sources at
every pool size. Everything upstream of `pack_evidence` is invisible
downstream. This also explains HUB-044's motivating case, which reproduces at
the packed stage and not at the retrieval stage.

**Prior art** (checked 2026-08-13, 16 papers read):

- arXiv:2607.00725 names this exact failure: once a fixed window forces
  evidence to be discarded, retrieval recall stops predicting accuracy, and
  the quantity that matters is what survives into context. It reframes packing
  as budgeted submodular maximization and beats top-k truncation at equal or
  lower token cost.
- arXiv:2512.25052 (AdaGReS) supplies a training-free rule: greedy selection
  by relevance minus redundancy against what is already packed, with the
  trade-off weight derived from pool statistics rather than tuned.
- arXiv:2410.05983 is the reason **not** to simply raise the context window:
  more retrieved passages helps and then hurts, an inverted-U driven by
  plausible-but-wrong hard negatives.
- arXiv:2603.22633 is the reason **not** to pack for source coverage: its
  structure-aware method achieved far broader coverage worth roughly 0.01 F1.
  Coverage stays a diagnostic (HUB-044), never a target.

**Approach.** `pack_by_marginal_gain` in `app/context.py`, selected by
`EVIDENCE_PACKING=marginal_gain`. Two deliberate departures from the prior
art, both conservative and both recorded in the code: redundancy is lexical
token overlap rather than embedding cosine, because what gets packed is a
propositional *span* rewritten out of a chunk and the stored chunk vector no
longer describes it; and the relevance/redundancy weight is budget pressure,
so a budget that admits every candidate makes the two packers **identical** —
there is no knob and no behaviour change where there is no scarcity.

**Measured, six real jobs, deployed limits.** Three jobs unchanged (p-hacking
identical by construction — 16 candidates all fit). Three jobs move by 1–2
chunks and **+1 source**: redis 13 → 14, postgres 10 → 11, kubernetes 14 → 15.
That is a small effect, reported as measured rather than tuned upward to look
better. Two readings, and the evidence does not yet separate them:

1. Lexical overlap cannot see paraphrase, so the redundancy the paper targets
   is largely invisible to this implementation. Embedding-cosine redundancy is
   the next variant to try.
2. Redundancy may simply not be the binding problem here: `propositional_spans`
   already discards non-self-contained text and the per-source cap already
   limits repetition, so the top of the ranking may genuinely be diverse. On
   kubernetes-56, 93 selected chunks become 70 propositional spans and 20
   packed — the 50 dropped are dropped by **budget**, not by redundancy.

**Do not adopt on these numbers.** Both readings predict small movement, and
nothing here says whether the swapped chunks are better. That requires an
answer-level evaluation (HUB-047), which every paper read validates on and
which this system does not have.

### HUB-045 — Chunk embeddings lose their document context

**Status:** 🔴 OPEN — unblocked by HUB-044, and redirected by it. The coverage
baseline says the binding ceiling is **candidate-pool composition**, not the
per-source cap: in five of six real jobs the 120-candidate dense+BM25 pool
proposes chunks from fewer sources than the job has embedded (p-hacking 8 of
19). Chunking may still be the cause — isolated 800/100 chunks from one
document can crowd a pool — but that is now a hypothesis with a measurement
attached rather than an assumption. Judge any change on coverage@k *and* the
exact-term recall numbers together; coverage alone is maximised by returning
one chunk per source.

Chunking is a fixed 800/100 recursive split applied identically to API
reference tables, Q&A pages, marketing copy and academic PDFs, and each chunk
is embedded in isolation so it loses the context of its surrounding passage.

Late Chunking (arXiv 2409.04701) embeds the whole document once with a
long-context model and pools per chunk afterwards, reporting better retrieval
without retraining and without changing chunk boundaries — a low-risk swap at
the embedding step. **Precondition:** the embedding model's context window
must cover typical documents; that is the load-bearing assumption to verify
first.

Adaptive Chunking (arXiv 2603.25333) is the more thorough alternative —
per-document metrics selecting among chunkers, correctness 62–64% → 72% — but
adds five metrics and multiple splitters, which is disproportionate until
HUB-044 shows chunking is the limit.

### HUB-046 — Cross-document linking: bridge entities, not shared vocabulary

**Status:** 🔴 OPEN — supersedes the mechanism half of HUB-040.

Nothing connects a document to any other: no entity linking, no cross-job
deduplication, no path for "what do we know about X across everything". The
one cross-document mechanism that exists pairs evidence spans by **shared
vocabulary**, which optimises for combinability rather than relatedness and
produced zero genuine conflicts across 450 sampled pairs.

Entity-centered Cross-document RE (arXiv 2210.16541) connects documents
through *bridge entities* that co-occur with both targets, filtering out the
noisy surrounding text that lexical overlap admits. Sequential Cross-Document
Coreference (arXiv 2104.08413) supplies the cost shape: incremental
mention-to-cluster scoring rather than exhaustive pairwise comparison, linear
rather than quadratic.

**Take the ideas, not the models** — both assume labelled supervision this
corpus does not have. Entity keys are also what deduplication and cross-job
aggregation both need, so this is the cheapest first step toward either.

### HUB-047 — No end-to-end retrieval evaluation set

**Status:** 🔴 OPEN — **promoted: this is now the gate on retrieval and
synthesis work, not a follow-on** (2026-08-13, after the HUB-049 prior-art
pass).

There are benchmarks for exact-term recall, claim support and source
screening, but nothing measuring whether the knowledge base answers real
questions well. Every retrieval decision to date has been judged on a proxy.

Two proxies are now known to be insufficient, from the prior art rather than
from opinion: arXiv:2607.00725 shows retrieval recall stops predicting
accuracy once a context budget forces evidence to be discarded — which is this
system's measured condition — and arXiv:2603.22633 shows large source-coverage
gains worth roughly 0.01 F1, so coverage does not stand in either. HUB-049 is
implemented and cannot be adopted for exactly this reason; HUB-045 would face
the same wall.

**Approach.** A held-out question set with known-good source documents,
scored on answer correctness and source coverage. HUB-044 is its first
metric. This is what would let HUB-045, HUB-048 and HUB-049's flag be decided
by measurement rather than argument.

**Open design question, unanswered by the prior art.** Every paper read
validates on short-answer QA with gold spans. This system's output is
long-form cited synthesis over a private, growing corpus, where "correct" is
not a string match. Settle the scoring design before building the set — the
existing sealed judge protocol (v4) is the closest thing already in the repo
and is the obvious starting point.

### HUB-048 — Knowledge-graph go/no-go, decided by measurement

**Status:** 🔴 OPEN — replaces the open-ended evaluation in HUB-027.

RAG vs. GraphRAG (arXiv 2502.11371) benchmarks both under a unified protocol
and finds GraphRAG's advantage is task- and dataset-dependent rather than
universal, with graph construction adding nontrivial LLM preprocessing cost —
material on one workstation with a 9B local model.

**Approach.** Run that protocol on this corpus: existing hybrid retrieval
against a minimal graph prototype, on representative aggregation queries, and
let the measured delta decide. Do not build the graph first.

**Blocked by HUB-043 and HUB-047**: comparing against a corpus-wide baseline
requires one to exist, and deciding by measurement requires an evaluation set.

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

**Superseded 2026-08-13 by HUB-048**, which replaces the open-ended
evaluation with a measured protocol. Note the stated precondition is **not
met**: hybrid retrieval exists for jobs but not corpus-wide (HUB-043), so
"hybrid retrieval first" has not actually happened for the queries a graph
would serve.

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

### Milestone 4 — Better answers — ✅ COMPLETE (2026-08-12)

HUB-017 ✅, HUB-018 ✅, HUB-019 ✅, HUB-020 ✅, HUB-021 ✅, HUB-022 ✅, HUB-023 ✅, HUB-031 ✅, HUB-032 ✅ (v4 final + HUB-034 report-side), HUB-033 ✅ (v4 metric-confusion stratum 10/10), HUB-034 ✅ (judge deployed; NLI decommissioned), HUB-035 ✅, HUB-036 ✅ (v4 final passed all gates)

**Exit condition:** retrieval quality is evaluated, prompts and citations are hardened, and each research job produces a useful evidence-backed artifact.

### Milestone 5 — Expansion only when earned

HUB-043 ✅ (2026-08-13 — one retrieval path; the corpus is queryable as one
base), HUB-044 ✅ (2026-08-13 — source coverage at k reported beside recall in
both retrieval benchmarks and baselined live over both scopes; the entry's own
claims were corrected first), HUB-045 🔴 (late chunking), HUB-046 🔴
(bridge-entity cross-document linking), HUB-047 🔴 (end-to-end retrieval
evaluation set), HUB-048 🔴 (knowledge-graph go/no-go by measurement).
HUB-024 ✅ (2026-08-13 — deployed and measured: 25 sources / 23 domains
against the 6/6 single-query baseline, zero off-topic acquisition; design and
citations in `PRDs/hub-024-query-planning.md`). HUB-025 through HUB-030 remain
deferred behind their explicit revisit triggers; none tripped as of
2026-08-13.

### Recommended order for the remaining open work (2026-08-12, post-pivot)

1. **HUB-003 / HUB-006 / HUB-013 / HUB-012 / HUB-031** — ✅ done 2026-08-11 (see statuses above).
2. **HUB-035** — ✅ done 2026-08-12 (Token Plan backend use verified permitted; judge gate merged behind `CLAIM_GATE=nli` default).
3. **HUB-036** — ✅ done 2026-08-12 (v4 blind final passed every gate; results sealed `7c9ed9ac…`).
4. **HUB-034** — ✅ done 2026-08-12 (operator-authorized; judge deployed as the only gate, NLI stack decommissioned, FastAPI/Starlette upgraded, deploy fully verified).
5. **HUB-032** — ✅ done 2026-08-12 (v4 final gate-side + HUB-034 report-side pair drafting and disclaimer logic).

The pivot sequence is complete; Milestone 4 is closed.

6. **HUB-024** — ✅ done 2026-08-13 (operator-approved and enabled; stages 1–3 complete, deployed and measured at scale — 25 sources / 23 domains against the 6/6 baseline with zero off-topic acquisition).

**Exit condition:** each expansion is justified by measured usage or a documented limitation, not by architectural possibility.
