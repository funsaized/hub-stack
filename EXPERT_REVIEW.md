# Expert Review

## Executive assessment

This is a strong single-user MVP with unusually good foundations: durable worker leases, idempotent chunk IDs, bounded embedding batches, strict request models, prompt-injection handling, retained source documents, health separation, and useful metrics. The full 54-test suite passes, including Redis lease/recovery integration tests.

It is not yet ready to become a reusable boundary for Hermes agents or a remotely accessible API. The main blockers are:

1. No authentication or authorization.
2. Crawler SSRF and network-isolation gaps.
3. A document ownership model that breaks cross-job provenance.
4. A rebuild path that does not reproduce the live index.
5. Unbounded requests, job history, logs, source versions, and reconciliation work.

I would address those before adding more integrations.

## Prioritized backlog

| Priority | Work item | Why it comes now |
|---|---|---|
| P0 | **Harden the API boundary** | Every endpoint is unauthenticated, including document inspection/deletion and report retry. Open WebUI sends `hub-local`, but the API never validates it. Add service-account API keys with scopes such as `corpus:read`, `research:submit`, `jobs:read`, `documents:delete`, and `admin`. Keep loopback binding as defense-in-depth. See `research-hub/app/main.py` and `docker-compose.yml`. |
| P0 | **Remove default credentials and introduce real secret management** | SearXNG, Crawl4AI, and Open WebUI ship checked-in or fallback secrets. Require values at startup, rotate the deployed values, and inject them through `.env`/Docker secrets. |
| P0 | **Close crawler SSRF and isolate the hostile-web tier** | URL policy only checks scheme/hostname strings. It does not reject private, loopback, link-local, metadata, multicast, IPv6, DNS-rebinding, or unsafe redirect destinations. Crawl4AI runs a browser against hostile pages on the same Docker network as Redis, Qdrant, Ollama, and the API. Add DNS/IP validation on every redirect and outbound network policy; place Crawl4AI on a separate network with only the minimum proxy/API access. |
| P0 | **Replace mutable `documents.job_id` with job/document observations** | An unchanged recrawl updates the document's single `job_id` to the newest job. Older jobs can lose evidence for report retries, while deduplicated Qdrant chunks retain stale topic/tags from the earlier job. Introduce immutable `documents`, `document_versions`, `research_jobs`, and a many-to-many `job_sources`/`observations` table. |
| P0 | **Make rebuild semantically identical to ingestion** | Rebuild currently indexes every retained historical version, omits injection sanitization and important metadata, and loads all documents into memory. Its default collection name can also get a duplicated model suffix because Compose already supplies a versioned name. Extract one shared document-to-points pipeline, select the current eligible version per URL, stream rows, and activate with a Qdrant alias. |
| P0 | **Add admission control and hard resource ceilings** | Chat message content/count, tags, domains, and overall HTTP body size are effectively unbounded. Crawled Markdown has no byte limit and all crawl results remain in memory until crawling finishes. Add reverse-proxy/body limits, model-field cardinality limits, maximum extracted page bytes, content-type rules, per-key quotas, query/generation semaphores, and queue-depth limits. |
| P0 | **Automate backups and prove restoration** | SQLite is the canonical source corpus, Redis owns job discoverability/state, and Qdrant is expensive derived state. Implement SQLite online backups, Redis persistence capture, optional Qdrant snapshots, encryption, retention, failure alerts, and a recurring restore test. Current documentation is procedural rather than automated. |
| P1 | **Bound Redis history and eliminate lifetime-wide reconciliation** | Completed jobs never expire, the job index grows forever, and worker reconciliation scans every historical job every lease interval. That becomes an O(total jobs) operation every minute. Archive terminal job metadata into the relational store, keep Redis focused on queued/in-flight state, use TTLs, and reconcile only pending/processing indexes. |
| P1 | **Add atomic job state transitions and fencing** | `_update_job` is a read-modify-write without CAS, so concurrent crawl progress can regress or overwrite fields. Lease loss also cannot cancel an already-running `asyncio.to_thread` Qdrant write. Use Lua/CAS state transitions, monotonic progress counters, lease fencing tokens, and per-canonical-URL locks before scaling beyond one worker. |
| P1 | **Move synthesis to an independent durable queue** | Synthesis is described as independent but still executes inside the ingestion task and its global timeout. A timeout after the job is marked completed can leave report state inconsistent, and the retrying worker skips completed jobs. Create a separate synthesis job/status/lease and make manual retry enqueue it idempotently. |
| P1 | **Prepare Qdrant for filtered corpus growth** | The live collection has 558 points, no payload indexes, and no HNSW vectors yet because the threshold is 10,000. Create payload indexes for workspace, canonical URL, document ID, topic, tags, and job/source relationships. Define collection alias cutover and garbage collection for old collections. Benchmark before and after the 10,000-point indexing transition. |
| P1 | **Correct the model context contract** | Configuration assumes 8,192 tokens, but live Ollama reports qwen2.5 loaded with a 4,096-token context. Packing can therefore exceed the actual runtime context. Set/send `num_ctx`, verify it during readiness, reserve the requested output size, and replace the one-token-per-byte approximation with the model tokenizer or a calibrated conservative estimator. |
| P1 | **Add worker and capacity observability** | Research readiness proves dependencies are reachable, not that a worker is alive or processing. Add worker heartbeat, oldest-job age, pending/processing/dead-letter counts, Redis memory/AOF size, disk-free space, Qdrant point/storage counts, crawler rejection/failure ratios, Ollama queue latency, and model/VRAM state. |
| P1 | **Set retention and log-rotation policies** | Source versions are retained forever, Redis jobs never expire, old Qdrant collections remain, and Docker logging has no configured rotation. Define retention separately for evidence versions, reports, failed jobs, operational metrics, logs, crawler caches, and superseded indexes. |
| P1 | **Harden containers and the supply chain** | Research Hub runs as root with a writable filesystem and no memory/PID limits, capability drops, or `no-new-privileges`. Several images use mutable `latest` tags; Python dependencies have exact direct versions but no transitive lock/hashes. Pin image digests, add a lockfile/SBOM/CVE gate, run non-root, use read-only filesystems/tmpfs where possible, and limit CPU/RAM/PIDs. Replace direct Docker-socket mounts for Dozzle/Uptime Kuma with a constrained socket proxy or remove them. |
| P2 | **Create an integration-neutral application layer** | Separate use cases from FastAPI and provider clients: `SubmitResearch`, `QueryCorpus`, `GenerateAnswer`, `InspectDocument`, and `DeleteDocument`. Keep native REST, OpenAI compatibility, and a future Hermes tool/MCP adapter as thin transports over those services. |
| P2 | **Version the native API and add integration reliability features** | Introduce `/api/v1`, idempotency keys for Hermes retries, cursor pagination, cancellation, job priority, webhook/SSE completion events, and structured error codes. Avoid an `X-Tenant` header without server-enforced identity. |
| P2 | **Improve retrieval only after building an evaluation set** | Dense-only nearest-neighbor search has no minimum score, so irrelevant evidence is always returned. Build a representative query/citation dataset, then evaluate score thresholds, hybrid lexical/vector search, deduplication, reranking, and recency/quality weighting. |

## Recommended target boundary

```text
Hermes agents ─┐
Open WebUI ────┬─> Auth + quotas + request limits
Direct API ────┘             │
                             v
              Versioned application services
                 │          │          │
              Job queue  Corpus repo  Query service
                 │          │          │
             workers     SQLite/DB   Qdrant/Ollama
                 │
          isolated crawler network
```

For a personal system, use one physical corpus with enforced `workspace_id` metadata rather than one Qdrant collection per agent. Give each integration a separate key and scope. Open WebUI should generally be query-only; Hermes may get research submission and job-read access; the administrative CLI alone should receive deletion and maintenance scopes.

## Current resource and growth profile

The live stack is healthy, but the full twelve-container deployment currently uses roughly 3.7 GiB of Docker-visible RAM. The largest consumers were:

- Ollama: 1.85 GiB host RAM plus about 5.4 GB of reported GPU model allocation.
- Open WebUI: 730 MiB.
- Crawl4AI: 447 MiB.
- API and worker: approximately 108 MiB and 89 MiB.

Persistent/image footprint is dominated by software rather than the corpus: Open WebUI and Crawl4AI images are about 7 GB and 6 GB, the Ollama image is 6.27 GB, and Ollama models occupy about 4.96 GB.

The current corpus is tiny:

- SQLite: 389 KB, nine documents, approximately 336,000 Markdown characters.
- Active Qdrant collection: 558 points and about 5.8 MB on disk.
- Legacy collection: another 528 points and 5.2 MB.
- Redis: 13 keys, 436 KB dataset, 411 KB AOF.

At 768 dimensions, the raw vector alone is about 3 KB per chunk. With text payload, metadata, indexes, segments, and WAL, planning on several kilobytes more per chunk is reasonable. One million chunks will therefore be multiple gigabytes even before backups and old collections. Retained Markdown versions, Redis history, and Docker logs currently grow without policy.

## Verification notes

- The complete 54-test suite passed in the built image, including Redis lease/recovery integration tests.
- `pip check` reports a consistent installed Python environment.
- Missing high-value tests include cross-job document reuse, rebuild equivalence/latest-version selection, SSRF/DNS rebinding, request-size limits, multi-worker same-URL races, timeout during synthesis, and backup restoration.
- An external CVE scan was not run because the available Docker scanner would transmit private image/package metadata to an external service. Current vulnerability status therefore remains unverified.
