# Next Steps

The current 0.1.0 MVP is working end-to-end. Here's the roadmap, prioritized by what you'll actually use.

## Tier 1 — do this week

### 1. Authenticated Tailscale ingress
**Why**: selectively reach approved surfaces without exposing the whole stack.
**Effort**: 10 minutes
**Steps**:
1. Install Tailscale on the host
2. Sign in
3. Install Tailscale on your phone
4. Follow `docs/NETWORKING.md`; expose only Ollama on a trusted interface, or put
   authenticated ingress in front of an approved UI/API.

### 2. Rotate exposed secrets
**Why**: ships with default credentials (`changeme_in_production`).
**Effort**: 30 minutes
**Steps**:
1. Copy `.env.example` → `.env`
2. Generate proper passwords: `openssl rand -hex 32`
3. Update `docker-compose.yml` to reference `${POSTGRES_PASSWORD}` etc.
4. Restart

### 3. Real startup script
**Why**: stack should auto-resume after reboot.
**Effort**: 15 minutes
**Steps**:
1. Create a `start.sh` script in the repo
2. Add it to Windows Task Scheduler (trigger: at logon)
3. Include a smoke test that fails if the stack isn't healthy after 3 minutes

### 4. Backup posture
**Why**: the knowledge base is the only thing of value here. Lose it, lose everything.
**Effort**: 1 hour
**Steps**:
1. Schedule daily `docker run ... alpine tar czf` for qdrant + redis volumes
2. Upload to a cloud bucket (Backblaze B2 is $5/TB/month)
3. Verify restore works — actually run a smoke test from a backup

## Tier 2 — build next month

### 5. RAG-enable Open WebUI
**Why**: so you can chat with the knowledge base from the chat UI.
**Effort**: 1-2 hours
**Steps**:
1. Open WebUI has a "Functions" feature
2. Write a function that intercepts user messages, queries /rag, prepends results
3. Test in Open WebUI

### 6. Cron-based research jobs
**Why**: "research this every Monday morning".
**Effort**: 2 hours
**Steps**:
1. Add a `cron` service to docker-compose with host networking
2. Config file mounted from `./cron/research.crontab`
3. Each entry: `0 9 * * 1 research submit "AI news this week" --tags weekly`

### 7. Web UI for research submissions
**Why**: the CLI is fine for you, but if you want to share the system, you need a UI.
**Effort**: 4-6 hours
**Steps**:
1. Pick a stack: Flask + HTMX, or Streamlit, or Next.js
2. Forms: topic, depth, tags, max-sources
3. Live status polling
4. Result viewer with cited sources

### 8. Langfuse for LLM tracing
**Why**: see exactly what the model is seeing, fix bad RAG outputs.
**Effort**: 2 hours
**Steps**:
1. Add Langfuse to docker-compose
2. Instrument research-hub with the Langfuse SDK
3. Web UI shows every prompt/completion with timing + cost

### 9. Domain allow/block lists for the crawler
**Why**: don't crawl reddit/spam sites, do crawl trusted domains.
**Effort**: 1 hour
**Steps**:
1. Add `allowed_domains` and `blocked_domains` to research-hub config
2. Filter the search results before crawling
3. Per-job override

## Tier 3 — nice to have

### 10. Discord/Telegram bot for agentic delivery
**Why**: trigger research from anywhere, get results back.
**Effort**: 3-4 hours
**Steps**:
1. Bot framework: `python-telegram-bot` or `discord.py`
2. Commands: `/research <topic>`, `/query <text>`, `/rag <question>`
3. Long-running handlers poll the research-hub job status
4. Notifications when jobs complete

### 11. Multi-tenant (if you ever share)
**Why**: separate knowledge bases per project or user.
**Effort**: 1-2 days
**Steps**:
1. Add a `X-Tenant` header to API requests
2. Scope Qdrant collection names by tenant
3. Add auth (JWT, or simpler: API key per tenant)
4. Per-tenant rate limits

### 12. Replace SearXNG with direct API search
**Why**: SearXNG's scrapers get rate-limited; direct API is more reliable.
**Effort**: 1 day
**Steps**:
1. Decide: Brave Search API, Tavily, SerpAPI
2. Add the API key to .env
3. Replace SearXNGClient with a search provider abstraction
4. SearXNG becomes an optional fallback

### 13. Streaming responses
**Why**: RAG answers shouldn't wait for the full generation before showing.
**Effort**: 2 hours
**Steps**:
1. Switch `/rag` to SSE (server-sent events)
2. Open WebUI needs to support OpenAI-compatible streaming API
3. CLI client handles streaming tokens

### 14. Web search enrichment for RAG
**Why**: RAG sometimes hallucinates; web search result appended can fix.
**Effort**: 1 day
**Steps**:
1. After generating a RAG answer, run a web search for the same query
2. Append the top 3 results to the context
3. Regenerate the answer with the augmented context

### 15. KG (knowledge graph) layer
**Why**: vectors find similar chunks; KGs find related entities.
**Effort**: 1-2 weeks
**Steps**:
1. Pick a KG library: Neo4j, Memgraph, or just Postgres + recursive CTEs
2. Extract entities + relations from each chunk (LLM call)
3. Store alongside Qdrant
4. Hybrid retrieval: KG traversal + vector search

## Tier 4 — future

### 16. Multi-machine federation
Each machine has its own GPU but shares the knowledge base. Routing decisions based on which GPU is free.

### 17. Voice input
Whisper-cpp for STT, qwen2.5 for response, pyttsx3 or piper for TTS. Phone-call-style research.

### 18. Browser extension
Highlight any text on a webpage → research it → see the corpus entry right there.

### 19. Mobile app
Native iOS/Android app for the research hub. Probably a PWA first.

### 20. Automate model fine-tuning
Use the knowledge base to fine-tune a small model on your domain. Run Ollama with the custom adapter.

## Known gaps

- **No backups**: state-loss risk is real
- **No auth**: if you expose this on the public internet, anyone can use it
- **No rate limiting**: a malicious job could exhaust resources
- **No job cancellation**: once a job starts, you have to wait
- **No queue priority**: all jobs are FIFO
- **Limited incremental indexing**: URL/content deduplication is implemented, but there is no crawl-level conditional HTTP refresh
- **No CI/CD**: automated local unit/integration tests exist, but CI is not configured
