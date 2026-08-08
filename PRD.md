# PRD — Personal Compute Hub

## Vision

A self-hosted, GPU-accelerated research workstation that runs entirely on a single consumer desktop. Optimised for: deep research, knowledge base building, and agentic delivery. No cloud dependencies for the core flow.

## Goals

1. **Deep research as a service**: `POST /research {topic}` returns a knowledge base entry covering N sources, ready for RAG queries.
2. **Reusable knowledge base**: every research job expands a shared, queryable index that improves over time.
3. **Privacy by default**: all data stays local. No browsing history leaks to Google, no prompts sent to OpenAI.
4. **Cheap to run**: zero monthly cost after the hardware purchase. Power draw is the only ongoing expense.
5. **Reliable enough for daily use**: 24/7 uptime, self-recovering, observable.

## Non-goals

- **Production multi-tenant SaaS**: this is a single-user tool. No auth, no tenancy, no rate-limiting.
- **Replacing a corporate RAG platform**: no Notion/Confluence integrations, no enterprise SSO.
- **Mobile-first**: the admin UI is web-based but designed for desktop. Mobile access is via Tailscale.
- **Mission-critical workloads**: if Ollama crashes mid-job, the job fails. There's no retry queue yet.

## Success criteria

- **Speed**: a research job on 10 sources completes in <2 minutes
- **Quality**: RAG answers cite their sources with [1], [2], ... inline and are factually grounded
- **Reliability**: stack comes back up after a reboot without manual intervention
- **Observability**: 1-minute to diagnose a service issue via Uptime Kuma + Dozzle
- **Reproducibility**: `git clone && docker compose up` produces a working stack (minus model download time)

## User personas

**Single persona**: a knowledge worker who wants to research topics deeply, save the research, and query it later. This is a personal tool, not a product.

## Top user stories

1. **As a researcher**: I submit a topic and get back a knowledge base I can query with natural language.
2. **As a developer**: I want to automate research jobs via API and trigger them from cron or webhooks.
3. **As an agent builder**: I want a clean API surface that lets my agents query the knowledge base and submit new research.
4. **As a learner**: I want to explore the same topic from multiple angles by re-running research with different tags.

## Functional requirements

### Research submission
- POST endpoint accepts topic, depth (URLs to crawl), max_sources (search results), tags
- Returns job ID immediately
- Job runs in background, status pollable
- Optional: callback URL for completion

### Search + crawl
- SearXNG for query expansion (DuckDuckGo, others)
- Crawl4AI for JavaScript-rendered pages
- Configurable concurrency (currently 4)
- Domain allow-list / block-list (TODO)

### Embedding
- Nomic embed (768-dim)
- Batch processing (currently sequential — TODO make parallel)
- GPU via Ollama

### Storage
- Qdrant for vectors
- Redis for job state
- Postgres available but unused

### Query
- POST /query returns top-k chunks with metadata
- POST /rag returns a generated answer with citations
- Optional: filter by topic, tags, date range

### Observability
- Dozzle for logs
- Uptime Kuma for service health
- 9 monitors configured

## Non-functional requirements

- **Latency**: query < 1s, RAG < 30s for 5-chunk context
- **Data persistence**: survive `docker compose down && up`
- **Startup time**: < 90s for fully-healthy cold start
- **Memory**: research-hub Python process < 500 MB at idle
- **GPU**: qwen2.5:7b + nomic-embed fit in 12 GB VRAM with 6 GB headroom

## Constraints

- One user, one machine
- RTX 3080 Ti (12 GB) is the GPU ceiling
- Single NVMe SSD (1 TB)
- No budget for cloud services
- No time to learn Kubernetes

## Risks

- **Ollama API quirks**: model indexing has been an issue (resolvable by re-pulling via API after restart)
- **Crawl4AI auth**: requires explicit token, easy to forget
- **Healthcheck inconsistencies**: each container has different binaries, requires custom scripts
- **Data loss on container recreate**: Qdrant collection recreation bug (FIXED — see docs/HEALTHCHECKS.md)
- **No backups**: all state in named volumes; if the disk dies, the knowledge base dies

## Versioning

- **0.1.0**: working MVP, all 10 services up, end-to-end pipeline validated
- **0.2.0**: Tailscale + remote access (planned)
- **0.3.0**: workflow automation (cron jobs, topic feeds)
- **0.4.0**: Open WebUI wired to knowledge base
- **1.0.0**: hardened, backed up, documented for handoff
