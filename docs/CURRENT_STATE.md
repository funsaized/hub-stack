# Current deployed state

Last verified: 2026-08-09 on the local Windows 11 workstation.

## Deployment model

The repository is stored on the Windows filesystem under OneDrive. Docker
Desktop runs the Linux containers through WSL2; application images and Docker
named volumes are therefore separate from the repository checkout. Editing
`research-hub/app` does not update the live API until the research-hub image is
rebuilt and its container is recreated.

Ollama is the only service with NVIDIA GPU access and uses the workstation's
RTX 3080 Ti. Qdrant corpus data, Redis job metadata, Ollama models,
Research-Hub documents, Crawl4AI, and optional UI state live in Docker named volumes.

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
