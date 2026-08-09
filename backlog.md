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

**Problem:** SearXNG, Crawl4AI, Open WebUI, and Postgres ship with hardcoded or fallback credentials.

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

## P3 — Optional expansion after sustained usage

### HUB-024 — Add query planning and iterative research

Add query decomposition, follow-up searches based on evidence gaps, stopping criteria, and budget controls. This is the work that would justify the “deep research” label.

**Revisit trigger:** users repeatedly need broader or multi-angle synthesis than a single search query produces.

### HUB-025 — Add scheduled research jobs

Add recurring jobs only after the durable worker, idempotency, and notification paths are complete.

**Revisit trigger:** at least two topics are being rerun manually on a predictable cadence.

### HUB-026 — Evaluate relational metadata storage

Evaluate Postgres only when job/source relationships, audit history, or complex retention queries exceed what Redis plus the document store can manage cleanly.

**Revisit trigger:** source/version metadata requires transactional cross-entity queries or durable audit history.

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

### Milestone 1 — Safe restarts

HUB-001, HUB-002, HUB-003, HUB-004, HUB-005, HUB-006

**Exit condition:** restarting the stack preserves data, health diagnostics work, and the default deployment is not broadly exposed.

### Milestone 2 — Durable ingestion

HUB-007, HUB-008, HUB-009, HUB-010, HUB-011

**Exit condition:** jobs survive process failure, repeated ingestion is idempotent, and the corpus can be rebuilt from retained documents.

### Milestone 3 — Operable daily service

HUB-012, HUB-013, HUB-014, HUB-015, HUB-016

**Exit condition:** builds are reproducible, recovery is tested, contracts are enforced, and failures are diagnosable.

### Milestone 4 — Better answers

HUB-017, HUB-018, HUB-019, HUB-020, HUB-021, HUB-022, HUB-023

**Exit condition:** retrieval quality is evaluated, prompts and citations are hardened, and each research job produces a useful evidence-backed artifact.

### Milestone 5 — Expansion only when earned

HUB-024 through HUB-030

**Exit condition:** each expansion is justified by measured usage or a documented limitation, not by architectural possibility.
