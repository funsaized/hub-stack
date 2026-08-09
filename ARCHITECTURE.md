# Architecture

## System overview

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
│  │   │   ┌───────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐ │  │ │
│  │   │   │  Ollama    │  │  Qdrant  │  │  Redis   │  │ Postgres  │ │  │ │
│  │   │   │  + GPU     │  │  vector  │  │  queue   │  │ + pgvector│ │  │ │
│  │   │   │ 11434      │  │  6333    │  │  6379    │  │  5432     │ │  │ │
│  │   │   └────────────┘  └──────────┘  └──────────┘  └───────────┘ │  │ │
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
   │  Research-Hub   │  FastAPI
   │  orchestrates   │
   └────────┬────────┘
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
- Job queue + state for the research orchestrator
- Job metadata keyed by `research:job:{uuid}`
- Job index list at `research:jobs`

### Postgres
- Not currently used by research-hub
- Available for relational data: agent memory, user prefs, audit logs
- pgvector extension available for SQL-side similarity search

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
- FastAPI orchestrator (single source of truth for the research workflow)
- Endpoints:
  - `POST /research` — submit a job, get back a job ID
  - `GET /research/{id}` — poll status
  - `GET /research` — list all jobs
  - `POST /query` — semantic search over the knowledge base
  - `POST /rag` — full RAG answer with citations
- Manages pipeline: search → crawl → chunk → embed → store
- Stores job state in Redis, vectors in Qdrant

### Open WebUI
- Chat interface for qwen2.5:7b
- Has memory, conversation history, code highlighting
- Not yet wired to the research-hub knowledge base (planned)

### Dozzle
- Live Docker log viewer on :8888
- Replaces `docker logs -f` with a searchable web UI

### Uptime Kuma
- Service health monitor on :3001
- 9 monitors configured (all 10 services except Dozzle which doesn't have a healthcheck)
- Push notifications via Discord/Telegram webhook

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
- **All UIs on host ports**: localhost works directly from the host; Tailscale handles remote access
- **gpu=nvidia, count=1**: only Ollama gets GPU; the rest run on CPU
- **Separate liveness and readiness**: Docker probes research-hub `/livez`; capability readiness and dependency diagnostics use `/readyz` and `/health/full`. See `docs/HEALTHCHECKS.md` and `docs/CURRENT_STATE.md`.

## Boundaries

- No authentication on the API (binds to 0.0.0.0). Add a reverse proxy with auth before exposing publicly.
- No backup strategy. Postgres + Redis + Qdrant volumes have the only state.
- No rate limiting. The crawler will hammer sites if you set depth=100.
- No multi-tenant. All jobs go to the same Qdrant collection.
