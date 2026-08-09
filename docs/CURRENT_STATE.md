# Current deployed state

Last verified: 2026-08-09 on the local Windows 11 workstation.

## Deployment model

The repository is stored on the Windows filesystem under OneDrive. Docker
Desktop runs the Linux containers through WSL2; application images and Docker
named volumes are therefore separate from the repository checkout. Editing
`research-hub/app` does not update the live API until the research-hub image is
rebuilt and its container is recreated.

Ollama is the only service with NVIDIA GPU access and uses the workstation's
RTX 3080 Ti. Qdrant corpus data, Redis job metadata, Ollama models, Postgres,
Open WebUI, Crawl4AI, and Uptime Kuma state live in Docker named volumes.

## Running services

The full eleven-service Compose topology is deployed locally. At the last check,
all eleven containers were running and every service with a configured Docker
healthcheck was healthy. Dozzle has no container healthcheck.

Research-Hub currently uses Ollama, Qdrant, Redis, SearXNG, and Crawl4AI.
The API only enqueues ingestion; the dedicated Research Worker claims and executes
jobs with leases, heartbeats, timeouts, bounded retries, and orphan reconciliation.
Postgres, Open WebUI, Dozzle, and Uptime Kuma are adjacent services; Postgres is
not currently part of the research/query data path.

Host ports are published on all interfaces by the current Compose file. Access
from another machine still depends on Windows Firewall and network routing, but
these bindings must not be treated as localhost-only. This is tracked by the
unfinished P0 network and credential items in `backlog.md`.

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

Important remaining limitations include LAN-wide port bindings, hardcoded/default
credentials, crawler SSRF protections, backups, and CI coverage. Redis AOF improves
durability but is not a backup or a high-availability queue.
