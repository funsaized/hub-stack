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
            ▼  canonicalize, deduplicate, domain/freshness policy, then top N
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
   │     Ollama      │  qwen3.5:9b
   │  generate       │  with cited context
   └─────────────────┘
```

## Component responsibilities

### Ollama
- Hosts `qwen3.5:9b` (Q4_K_M quantised, 6.6 GB) as the default generation model
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
- Receives `check_robots_txt` from the worker; enabled by default
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
- After successful ingestion, the worker synthesizes a stable Markdown report from
  retained SQLite evidence. Reports have an independent persisted lifecycle and can
  be retrieved or retried without entering search, crawl, or embedding again.
- Material report claims carry private exact packed-span refs and are accepted only after the
  shared CPU claim-verifier returns argmax entailment at the frozen `0.97` threshold. The one
  correction is reverified from scratch; public citations are rendered only from passing refs.
- Canonical URLs and content hashes form stable document IDs; chunk IDs also include
  chunk index and chunker version. Unchanged content is skipped and changed content is
  fully embedded/upserted before stale chunks are removed.
- Research requests may allow/block domains, cap sources per domain, and require a
  freshness window. Canonical-equivalent URLs are crawled once; accept/reject decisions
  remain visible in job progress.
- Exact Markdown and crawl metadata remain in SQLite for audit. Derived Qdrant chunks
  classify and neutralize common prompt-injection spans and expose publication/fetch,
  quality, freshness, security, and robots-policy metadata.
- RAG treats every retrieved entry as delimited untrusted evidence. A conservative
  UTF-8-byte token upper bound packs only complete entries after reserving system,
  question, and answer space; returned sources are exactly those supplied to Ollama.
  Public custom system prompts are disabled unless explicitly enabled for trusted local callers.

### Open WebUI
- Chat interface for `qwen3.5:9b` and the `research-corpus` RAG route
- Has memory, conversation history, code highlighting
- Optional Compose profile: `webui`
- Direct Ollama models provide plain chat
- The `research-corpus` OpenAI-compatible route provides conversation-aware RAG
  with bounded untrusted evidence and the exact ordered sources used for generation

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

**CPU claim verifier**: PyTorch is confined to one offline, pinned CPU service shared by the
API and worker. Ollama remains the only GPU consumer.

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
- **Fail-closed claim support**: research/all readiness includes the verifier. Timeout,
  unavailability, revision mismatch, malformed output, unresolved spans, non-entailment, and
  inputs over 512 tokens reject persistence; no input is truncated.
- **Span-first report claims**: report synthesis drafts one claim per exact evidence
  sentence rather than drafting claims and then choosing evidence for them, so a claim can
  never be paired with a span that does not support it. Claims are compressions of their
  span - deletion only, never addition - which is what keeps them inside the entailment
  gate. `app/spans.py` decides what counts as a self-contained sentence; every offered span
  stays an exact substring of its sanitized chunk.
- **Verification matches its evaluation**: a single-evidence claim is judged by exactly the
  premise/hypothesis pair the sealed final set measured. Any change to the model, revision,
  threshold, or union premise format requires a new blind final set. See
  `PRDs/research-claim-support-verification.md`.
- **Design iterations are measured offline**: `tests/benchmark_claim_drafting.py` replays
  frozen evidence through generation and verification without touching Redis, SQLite,
  Qdrant, the corpus, or the report lifecycle, so a claim-drafting change is evaluated
  before a live retry is spent on it.

## Boundaries

- No authentication on the API, so it remains loopback-only. Add an authenticated
  reverse proxy before intentionally making it remote.
- No backup strategy. Redis, Qdrant, Research-Hub, and optional-service volumes hold state.
- No rate limiting. The crawler will hammer sites if you set depth=100.
- No multi-tenant. All jobs go to the same Qdrant collection.
