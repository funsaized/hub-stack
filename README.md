# Hub Stack — Personal Compute Hub

A self-hosted, GPU-accelerated research and AI workbench running on Windows 11 + WSL2 + Docker. Built for deep research, web crawling, RAG, and agentic delivery.

## What it is

A reusable research service that searches the web, crawls results, extracts clean markdown, embeds everything in a vector store, and serves it back through a RAG chatbot. Everything runs locally on your RTX 3080 Ti. No cloud APIs required for the core flow.

## What's in the box

| Service | Host access | Purpose |
|---|---|---|
| Ollama | `127.0.0.1:11435` (configurable) | Local LLM + embeddings |
| Qdrant | Internal only | Vector DB for RAG |
| Redis | Internal only | Durable ingestion queue and job state |
| Postgres | Internal only | Relational store with pgvector |
| SearXNG | `127.0.0.1:8889` | Private meta-search |
| Crawl4AI | Internal only | Web crawler with LLM-friendly extraction |
| Research-Hub | `127.0.0.1:8000` | FastAPI orchestrator |
| Research Worker | Internal only | Durable search/crawl/embed/store worker |
| Open WebUI | `127.0.0.1:8080` | Chat interface |
| Dozzle | `127.0.0.1:8888` | Live Docker log viewer |
| Uptime Kuma | `127.0.0.1:3001` | Service health monitoring |
| Prometheus | `127.0.0.1:9090` | Pipeline metrics and alert evaluation |
| Grafana | `127.0.0.1:3002` | Provisioned Research Hub dashboard |

## Quick start

```bash
# From WSL2 Ubuntu 24.04
cd ~/hub-stack
docker compose up -d
```

First run takes 5-10 minutes (image pulls, model download). After that, the stack comes up in under 90 seconds.

## Quick verification

```bash
# Health check
curl -s http://localhost:8000/livez
curl -s http://localhost:8000/readyz?capability=query
# See docs/CURRENT_STATE.md for response semantics.

# Submit a research job
python3 test_research.py

# Query the knowledge base
python3 test_query.py

# Automated unit and integration regression tests
cd research-hub
uv run --with-requirements requirements.txt python -m unittest discover -s tests -v
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — system diagram, data flow, tech choices
- [PRD.md](PRD.md) — product requirements, goals, non-goals
- [SETUP.md](SETUP.md) — full setup from scratch, prerequisites, troubleshooting
- [USE_CASES.md](USE_CASES.md) — concrete things you can do today
- [NEXT_STEPS.md](NEXT_STEPS.md) — roadmap, deferred features, known gaps
- [docs/HEALTHCHECKS.md](docs/HEALTHCHECKS.md) — how the Docker healthcheck system works
- [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md) — dated local deployment and health-contract snapshot
- [docs/MODELS.md](docs/MODELS.md) — current model choices, swap instructions
- [docs/DOCUMENT_STORE.md](docs/DOCUMENT_STORE.md) — retained sources, deletion, and index rebuilds
- [docs/NETWORKING.md](docs/NETWORKING.md) — default bindings and optional remote Ollama access
- [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) — JSON logs, correlation IDs, metrics, dashboard, and alerts

## Hardware

Validated on:
- AMD Ryzen 7 5800X (8C/16T)
- 32 GB DDR4-3200 (dual-channel)
- NVIDIA RTX 3080 Ti (12 GB VRAM)
- WD Blue SN570 1 TB NVMe
- Windows 11 Home 24H2 (Build 26200)

## Status

The thirteen-service Compose stack is deployed locally. HUB-002 network boundaries,
HUB-005 health/readiness, and
HUB-007 through HUB-011 durable, retained, batched ingestion and tests, plus
HUB-014 strict contracts and HUB-015 observability, are implemented; see
[docs/CURRENT_STATE.md](docs/CURRENT_STATE.md).
Other P0 and later work remains tracked in [backlog.md](backlog.md).
