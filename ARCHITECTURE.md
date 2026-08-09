# Architecture

## System overview

The default runtime is Ollama, Qdrant, Redis, SearXNG, Crawl4AI,
Research-Hub, and its worker. UI and operations components shown below are
optional Compose profiles. Postgres is not part of the runtime; HUB-026 defines
the evidence and integration work required before adopting it.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Windows 11 host                                 │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      WSL2 Ubuntu 24.04                              │ │
│  │                                                                    │ │
│  │   ┌──────────────────────────────────────────────────────────────┐  │ │
│  │   │                    Docker Desktop                             │  │ │
│  │   │                                                             │  │ │
│  │   │   ┌───────────┐  ┌──────────┐  ┌──────────┐                │  │ │
│  │   │   │  Ollama    │  │  Qdrant  │  │  Redis   │                │  │ │
│  │   │   │  + GPU     │  │  vector  │  │  queue   │                │  │ │
│  │   │   │ 11434      │  │  6333    │  │  6379    │                │  │ │
│  │   │   └────────────┘  └──────────┘  └──────────┘                │  │ │
│  │   │                                                             │  │ │
│  │   │   ┌───────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐ │  │ │
│  │   │   │  SearXNG   │  │ Crawl4AI │  │ Research │  │ Open      │ │  │ │
│  │   │   │  search    │  │  crawl   │  │  -Hub    │  │ WebUI     │ │  │ │
│  │   │   │ 8889       │  │ 11235    │  │  8000    │  │  8080     │ │  │ │
│  │   │   └────────────┘  └──────────┘  └──────────┘  └───────────┘ │  │ │
│  │   │                                                             │  │ │
│  │   │   ┌───────────┐  ┌──────────┐                              │  │ │
│  │   │   │  Dozzle    │  │ Uptime   │  (observability layer)      │  │ │
│  │   │   │  8888      │  │ Kuma     │                              │  │ │
│  │   │   │            │  │  3001    │                              │  │ │
│  │   │   └────────────┘  └──────────┘                              │  │ │
│  │   └─────────────────────────────────────────────────────────────┘  │ │
│  │                                                                    │ │
│  │   ┌────────────────────────────────────────────────────────────┐    │ │
│  │   │  Developer tools (host filesystem)                         │    │ │
│  │   │  fnm, uv, rustup, gh, fzf, ripgrep, fd, bat, eza,         │    │ │
│  │   │  zoxide, btop, lazydocker, jq, tmux, git                  │    │ │
│  │   └────────────────────────────────────────────────────────────┘    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  BIOS: DOCP enabled (RAM @ 3200 MT/s), Above 4G Decoding, Re-Size BAR │
└─────────────────────────────────────────────────────────────────────────┘
```

## Deep research pipeline

```
   user input
   "research: deep learning optimizers"
        │
        ▼ (POST /research)
   ┌─────────────────┐
   │  Research-Hub   │  FastAPI enqueues only
   └────────┬────────┘
            │ durable enqueue
            ▼
   ┌──────────────────┐
   │ Redis + worker   │  claim lease, heartbeat, retry, recovery
   └────────┬─────────┘
            │
            ▼
   ┌─────────────────┐
   │    SearXNG      │  private search
   │  (DuckDuckGo)   │  returns 20 URLs
   └────────┬────────┘
            │
            ▼  filter top N (depth parameter)
   ┌─────────────────┐
   │    Crawl4AI     │  Playwright + LLM extraction
   │  parallel 4x    │  returns markdown
   └────────┬────────┘
            │
            ▼
   ┌─────────────────┐
   │  chunker.py     │  ~800 char chunks, 100 overlap
   │  semantic       │  paragraph-aware
   └────────┬────────┘
            │
            ▼
   ┌─────────────────┐
   │     Ollama      │  nomic-embed-text
   │  embeddings     │  768-dim vectors
   └────────┬────────┘
            │
            ▼
   ┌─────────────────┐
   │    Qdrant       │  upsert with metadata
   │  research_corpus│  topic, tags, source, timestamp
   └─────────────────┘


later ─────────────────────────

   user query
   "what are the best sparse optimizers?"
        │
        ▼ (POST /query OR /rag)
   ┌─────────────────┐
   │  Research-Hub   │
   │  embed query    │  nomic-embed-text
   │  search Qdrant  │  top 5 similar
   └────────┬────────┘
            │
            ▼ (if /rag)
   ┌─────────────────┐
   │     Ollama      │  qwen2.5:7b
   │  generate       │  with cited context
   └─────────────────┘
```

## Component responsibilities

### Ollama
- Hosts `qwen2.5:7b` (Q4_K_M quantised, 4.7 GB) for generation
- Hosts `nomic-embed-text` for embeddings
- OpenAI-compatible API on port 11434
- Shares GPU with the rest of the system

### Qdrant
- Vector DB for RAG over crawled content
- Single collection `research_corpus`, 768-dim cosine distance
- Created on first startup, persists across restarts (named volume)

### Redis
- Durable FIFO queue + state for the research API and dedicated worker
- Job metadata keyed by `research:job:{uuid}`
- Job index list at `research:jobs`
- Pending/processing lists and expiring claim leases prevent duplicate execution
- AOF persistence and `noeviction` protect queue records; heartbeats, bounded retries,
  timeouts, and periodic reconciliation recover abandoned work

### SearXNG
- Private meta-search proxy
- Currently configured to use DuckDuckGo only
- Returns JSON for the crawler, has web UI on :8889

### Crawl4AI
- Headless browser crawler with LLM-aware extraction
- Returns clean markdown (no nav, ads, scripts)
- Defaults to 4 concurrent crawlers
- Authenticated via `CRAWL4AI_API_TOKEN` env var

### Research-Hub
- FastAPI API process; accepts and durably enqueues research jobs
- Endpoints:
  - `POST /research` — submit a job, get back a job ID
  - `GET /research/{id}` — poll status
  - `GET /research` — list all jobs
  - `POST /query` — semantic search over the knowledge base
  - `POST /rag` — full RAG answer with citations
- A separate `research-worker` owns search → crawl → chunk → embed → store
- Stores job state in Redis, vectors in Qdrant
- Canonical URLs and content hashes form stable document IDs; chunk IDs also include
  chunk index and chunker version. Unchanged content is skipped and changed content is
  fully embedded/upserted before stale chunks are removed.

### Open WebUI
- Chat interface for qwen2.5:7b
- Has memory, conversation history, code highlighting
- Not yet wired to the research-hub knowledge base (planned)
- Optional Compose profile: `webui`

### Dozzle
- Live Docker log viewer on :8888
- Replaces `docker logs -f` with a searchable web UI
- Optional Compose profile: `logs`; container actions are disabled

### Prometheus and Grafana
- Prometheus scrapes the API and dedicated worker without a push gateway
- Grafana provisions a pipeline dashboard from version-controlled JSON
- Alert rules cover job failures, API errors, and slow generation
- See `docs/OBSERVABILITY.md` for the metric and correlation contract
- Optional Compose profile: `observability`

### Uptime Kuma
- Service health monitor on :3001
- Core service uptime monitors are configured manually; pipeline metrics and
  alert evaluation live in Prometheus/Grafana
- Push notifications via Discord/Telegram webhook
- Optional Compose profile: `uptime`

## Why these choices

**Ollama over vLLM/TGI**: Easier to operate, supports both generation and embedding, no proxy needed.

**Qdrant over pgvector/Weaviate**: Pure vector DB, fast, has nice filtering by metadata, named volume means data survives restarts.

**Crawl4AI over Scrapy/Playwright-direct**: Handles JS-rendered pages, returns already-cleaned markdown, integrates with our LLM for extraction (future).

**SearXNG over direct Google/Bing**: No API keys, no rate limits from one provider, harder to get IP-banned.

**Redis over RabbitMQ/Postgres-MQ**: Overkill for our throughput. Redis is already small and fast.

**PyTorch-free stack**: No ML framework dependencies in the orchestrator. Ollama owns the GPU.

## Notable design decisions

- **Named volumes for all stateful services**: data survives `docker compose down && up`
- **No compose-level healthcheck override for research-hub**: Dockerfile's curl-based healthcheck is used (wget has IPv6 issues on slim images)
- **Qdrant collection is persistent and validated on startup**: research-hub creates a missing collection once, preserves an existing collection, and refuses to start without modifying data when its vector size or distance is incompatible with the configured embedding model
- **Loopback-only ingress by default**: local UIs and the API bind to `127.0.0.1`;
  Redis, Qdrant, and Crawl4AI have no published host ports. Ollama is
  loopback-only unless `OLLAMA_BIND_ADDRESS` selects a trusted interface.
- **gpu=nvidia, count=1**: only Ollama gets GPU; the rest run on CPU
- **Separate liveness and readiness**: Docker probes research-hub `/livez`; capability readiness and dependency diagnostics use `/readyz` and `/health/full`. See `docs/HEALTHCHECKS.md` and `docs/CURRENT_STATE.md`.
- **API/worker separation**: restarting or scaling the API cannot interrupt claimed work.
  Worker leases and reconciliation resume abandoned jobs after a worker failure.

## Boundaries

- No authentication on the API, so it remains loopback-only. Add an authenticated
  reverse proxy before intentionally making it remote.
- No backup strategy. Redis, Qdrant, Research-Hub, and optional-service volumes hold state.
- No rate limiting. The crawler will hammer sites if you set depth=100.
- No multi-tenant. All jobs go to the same Qdrant collection.
