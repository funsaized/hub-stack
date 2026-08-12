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
  MiniMax M3 judge gate (`app/judge_gate.py`) returns a structured accepted verdict with every
  cited span necessary. The one correction is reverified from scratch; public citations are
  rendered only from passing refs. Cross-document span pairs are additionally drafted and
  judged (HUB-032), so verified cross-source disagreements can be displayed with both citations.
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

**LLM-as-judge claim gate over local NLI** (HUB-034, 2026-08-12): the frozen DeBERTa NLI
service could not judge joint multi-span claims (v3 final: 0.47) and conflated metric names,
so it was replaced by a MiniMax M3 faithfulness judge validated by the sealed v4 blind
evaluation (all gates passed, including zero unsupported acceptances under adversarial
injection). The trade-offs were operator-accepted: judged evidence spans leave the machine,
and the cloud judge is not frozen — the v4 seal records the served model version, and any
change requires a fresh blind set before the gate is trusted again. Ollama remains the only
GPU consumer; the API/worker images no longer carry PyTorch.

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
- **Fail-closed claim gate (judge)**: research/all readiness includes the gate's
  configuration. The judge (`app/judge_gate.py`) keeps the verdict contract and
  fail-closed semantics — timeout, quota exhaustion, malformed or schema-violating
  output, and a missing served-model version all leave the report retryable. It is
  conjunctive with the deterministic structural checks (supports-restates-claim
  verbatim, bounded refs — it can only reject more, never admit what structure
  rejects), fences evidence as untrusted with fence-break neutralization, judges
  multi-span claims natively with per-ref necessity (padding rejection), and records
  the served model version in every verdict because a cloud judge is not frozen.
  Judged evidence spans go to the MiniMax API — a deliberate privacy exception
  documented in `docs/NETWORKING.md`. Validated by the sealed v4 blind evaluation
  (HUB-036); re-baseline on any served-model change.
- **Span-first report claims**: report synthesis drafts one claim per exact evidence
  sentence rather than drafting claims and then choosing evidence for them, so a claim can
  never be paired with a span that does not support it. Claims are compressions of their
  span - deletion only, never addition - which is what keeps them inside the entailment
  gate. `app/spans.py` decides what counts as a self-contained sentence; every offered span
  stays an exact substring of its sanitized chunk.
- **Verification matches its evaluation**: the deployed judge configuration (system prompt,
  requested model, temperature) is exactly what the sealed v4 blind final measured; the seal
  records its fingerprint and the served model version. Any change to the prompt, schema, or
  served model requires a fresh blind set before the gate is trusted again. The retired v2
  NLI evaluation and its PRD (`PRDs/research-claim-support-verification.md`) remain as the
  historical record.
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
