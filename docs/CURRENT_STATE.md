# Current deployed state

Last verified: 2026-08-10 on the local Windows 11 workstation.

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

The seven-container default Compose topology is deployed locally. At the latest
runtime check, Research-Hub was healthy and the dedicated worker was stable.

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
| `/readyz?capability=research` | Redis, Ollama, Qdrant, SearXNG, and Crawl4AI can ingest research | 200 ready; 503 degraded |
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
