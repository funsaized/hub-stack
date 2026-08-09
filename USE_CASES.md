# Use Cases

Concrete things you can do with the hub right now, with copy-paste examples.

## 1. Research a topic

Research submissions are durable across API restarts. Re-running a topic is safe:
unchanged pages are skipped, while changed pages replace their prior chunks.

```bash
./research-hub/bin/research submit "transformer attention mechanisms" --depth 10 --tags ml nlp
```

Then poll:
```bash
./research-hub/bin/research status <job_id>
```

Or use it with `--wait` to block until done.

## 2. Build a knowledge base over time

Run multiple research jobs on related topics, tag them, then query across the whole body:

```bash
./research-hub/bin/research submit "Python asyncio" --tags python
./research-hub/bin/research submit "Python multiprocessing" --tags python
./research-hub/bin/research submit "Python concurrency patterns" --tags python

# Query across all Python-related research
curl -X POST http://localhost:8000/rag \
  -H 'Content-Type: application/json' \
  -d '{"query": "how should I structure concurrent Python?", "tags_filter": ["python"], "top_k": 5}'
```

## 3. Use it as a research agent backend

```python
import httpx

async def research_for_agent(topic: str) -> dict:
    """Submit a research job, wait for it, return the knowledge base summary."""
    base = "http://localhost:8000"

    # Submit
    job = (await httpx.AsyncClient().post(
        f"{base}/research",
        json={"topic": topic, "depth": 5, "tags": ["agent-call"]},
    )).json()

    # Poll
    while True:
        status = (await httpx.AsyncClient().get(
            f"{base}/research/{job['job_id']}"
        )).json()
        if status["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(5)

    # Query
    return (await httpx.AsyncClient().post(
        f"{base}/rag",
        json={"query": topic, "top_k": 5},
    )).json()
```

Your agent can call this function before answering questions about current events.

## 4. Chat with the knowledge base via Open WebUI

Open WebUI at http://localhost:8080 is a chat interface for qwen2.5:7b. As of this version, it doesn't query the knowledge base directly. To make it work:

1. Use Open WebUI for general chat
2. Switch to a terminal with the research CLI for knowledge-base queries

OR, planned future: configure Open WebUI's "Functions" feature to query /rag for every message.

## 5. Maintain a personal wiki

Treat Qdrant as a versioned, semantic personal wiki:

```bash
# Add entries
./research-hub/bin/research submit "React hooks best practices" --tags wiki react
./research-hub/bin/research submit "Python async vs threading" --tags wiki python

# Search
./research-hub/bin/research query "how does useEffect cleanup work?" --topic "React hooks"
```

The tags filter lets you scope queries.

## 6. Technical research before a project

```bash
# Before starting a new project
./research-hub/bin/research submit "PostgreSQL vs SQLite for local apps" --depth 15 --tags db decision
./research-hub/bin/research submit "Auth patterns for solo SaaS" --depth 15 --tags auth decision

# Question the knowledge base
./research-hub/bin/research rag "what's the simplest auth for a new local-first app?"
```

## 7. Background research for blog posts

```bash
# While you're writing
./research-hub/bin/research submit "LLM inference optimization techniques" --depth 20 --tags blog perf

# Pull quotes from the corpus
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "KV cache optimization", "tags_filter": ["blog", "perf"], "top_k": 10}'
```

Then write your post and cite from the corpus.

## 8. Monitor system health

Use Uptime Kuma's alert integrations:

- **Discord webhook**: get a ping in your dev Discord when something's down
- **Telegram bot**: get a push notification on your phone
- **Email**: traditional, works for non-immediate alerts

Set up:
1. Uptime Kuma → Settings → Notifications → "+ Add"
2. Configure your provider
3. Per monitor → Notifications tab → enable

## 9. Develop new agent patterns

The research-hub API is a clean Python codebase. You can:

- Add new endpoints (`/summarize`, `/compare`, `/extract-entities`)
- Change the RAG prompt for different output styles
- Add a different embedding model per tenant
- Schedule recurring research jobs via cron or a Python scheduler

The code is in `research-hub/app/` — readable, ~600 lines total.

## 10. Run a self-hosted search engine

SearXNG gives you Google-style search without tracking:

http://localhost:8889

Configure it under SearXNG's settings (mounted from `searxng/settings.yml`).

## 11. Replace scoped SaaS

Specific things this stack can replace:

- **Notion AI** → research + RAG
- **Perplexity** → research-hub + query
- **ChatGPT Plus** → Open WebUI + qwen2.5:7b
- **Google Alerts** → scheduled research jobs (with cron)
- **Phind / DevDocs** → SearXNG + RAG over tech docs

## 12. Backup strategies

The stateful data lives in named volumes:
- `hub_ollama_data` (5 GB+, models)
- `hub_qdrant_data` (knowledge base, ~10 MB per 100 jobs)
- `hub_redis_data` (job state, ~1 MB)
- `hub_uptime_kuma_data` (Kuma config, ~10 MB)

To back up:
```bash
# Stop the stack first to ensure consistency
docker compose stop

# Copy volumes
docker run --rm -v hub_qdrant_data:/data -v $(pwd):/backup alpine tar czf /backup/qdrant-backup.tar.gz /data
docker run --rm -v hub_redis_data:/data -v $(pwd):/backup alpine tar czf /backup/redis-backup.tar.gz /data

# Restart
docker compose up -d
```

Or use `restic` to a remote destination (Backblaze B2, S3, etc.).

## 13. Multi-machine federation

Run this on multiple machines, share the SearXNG/Qdrant/Redis services. Each workstation pulls a model into its own GPU but shares the knowledge base.

(TODO: not yet implemented.)

## 14. Workflow automation with n8n

Add n8n to the docker-compose stack to build visual workflows:

```yaml
n8n:
  image: n8nio/n8n
  ports:
    - "5678:5678"
  volumes:
    - n8n_data:/home/node/.n8n
```

Then build workflows like:
- "Every Monday at 9am, research 'AI news this week' and email me a summary"
- "When a new doc appears in /shared/docs, ingest it into the knowledge base"

## 15. Out-of-band testing

Run the test scripts standalone to verify the stack after any change:

```bash
python3 test_research.py
python3 test_query.py
```

Save these in CI to verify the stack is healthy before deploying changes.
