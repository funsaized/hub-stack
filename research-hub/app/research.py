"""Research job orchestration: search -> crawl -> chunk -> embed -> store."""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis_async

from .config import Config
from .clients import OllamaClient, QdrantClient, SearXNGClient, Crawl4AIClient
from .models import JobStatus, ResearchRequest

logger = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Simple recursive chunker. Splits on paragraph boundaries first."""
    if not text or not text.strip():
        return []

    # Normalize whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    chunks: list[str] = []

    # First split by paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}".strip() if current else para
        else:
            if current:
                chunks.append(current)
            # If single paragraph exceeds chunk size, split by sentence
            if len(para) > chunk_size:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                current = ""
                for sent in sentences:
                    if len(current) + len(sent) + 1 <= chunk_size:
                        current = f"{current} {sent}".strip() if current else sent
                    else:
                        if current:
                            chunks.append(current)
                        if len(sent) > chunk_size:
                            # Hard-split very long sentences
                            for i in range(0, len(sent), chunk_size - overlap):
                                chunks.append(sent[i:i + chunk_size])
                            current = ""
                        else:
                            current = sent
            else:
                current = para

    if current:
        chunks.append(current)

    # Apply overlap: prepend tail of previous chunk to next
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-overlap:] if len(chunks[i - 1]) > overlap else chunks[i - 1]
            overlapped.append(f"{prev_tail} {chunks[i]}".strip())
        chunks = overlapped

    return [c.strip() for c in chunks if c.strip()]


class ResearchOrchestrator:
    """Runs research jobs and persists state to Redis."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ollama = OllamaClient(cfg.ollama_url, cfg.llm_model, cfg.embedding_model)
        self.qdrant = QdrantClient(
            cfg.qdrant_url,
            cfg.qdrant_collection,
            vector_size=cfg.embedding_dimension,
            embedding_model=cfg.embedding_model,
        )
        self.searxng = SearXNGClient(cfg.searxng_url)
        self.crawl4ai = Crawl4AIClient(cfg.crawl4ai_url, cfg.crawl4ai_token or None)
        self._redis: redis_async.Redis | None = None

    async def init(self):
        self._redis = redis_async.from_url(self.cfg.redis_url, decode_responses=True)

    async def close(self):
        await self.ollama.close()
        await self.searxng.close()
        await self.crawl4ai.close()
        if self._redis:
            await self._redis.close()

    async def health_check(self) -> dict[str, bool]:
        """Check every dependency and return only JSON-safe booleans."""
        async def check(name: str, operation) -> bool:
            try:
                return bool(await operation())
            except Exception:
                logger.exception("%s health check failed", name)
                return False

        async def redis_health() -> bool:
            return bool(self._redis and await self._redis.ping())

        ollama_ok, qdrant_ok, redis_ok, searxng_ok, crawl4ai_ok = await asyncio.gather(
            check("ollama", self.ollama.health),
            check("qdrant", lambda: asyncio.to_thread(self.qdrant.health)),
            check("redis", redis_health),
            check("searxng", self.searxng.health),
            check("crawl4ai", self.crawl4ai.health),
        )
        services = {
            "ollama": ollama_ok,
            "qdrant": qdrant_ok,
            "redis": redis_ok,
            "searxng": searxng_ok,
            "crawl4ai": crawl4ai_ok,
        }
        return {**services, "all_ok": all(services.values())}

    def _job_key(self, job_id: str) -> str:
        return f"research:job:{job_id}"

    def _job_index_key(self) -> str:
        return "research:jobs"

    async def _update_job(self, job_id: str, **fields):
        if not self._redis:
            return
        key = self._job_key(job_id)
        data = await self._redis.get(key)
        if not data:
            return
        job = json.loads(data)
        job.update(fields)
        job["updated_at"] = utcnow()
        await self._redis.set(key, json.dumps(job))

    async def submit_job(self, req: ResearchRequest) -> str:
        if not self._redis:
            raise RuntimeError("Orchestrator not initialized")
        job_id = str(uuid.uuid4())
        now = utcnow()
        job = {
            "job_id": job_id,
            "topic": req.topic,
            "depth": req.depth,
            "max_sources": req.max_sources,
            "language": req.language,
            "tags": req.tags,
            "status": JobStatus.PENDING.value,
            "created_at": now,
            "updated_at": now,
            "progress": {"phase": "queued"},
            "error": None,
            "sources_count": 0,
            "chunks_count": 0,
        }
        await self._redis.set(self._job_key(job_id), json.dumps(job))
        await self._redis.lpush(self._job_index_key(), job_id)
        # Run in background
        asyncio.create_task(self._run_job(job_id))
        return job_id

    async def get_job(self, job_id: str) -> dict | None:
        if not self._redis:
            return None
        data = await self._redis.get(self._job_key(job_id))
        if not data:
            return None
        return json.loads(data)

    async def list_jobs(self, limit: int = 50) -> list[dict]:
        if not self._redis:
            return []
        ids = await self._redis.lrange(self._job_index_key(), 0, limit - 1)
        jobs = []
        for jid in ids:
            job = await self.get_job(jid)
            if job:
                jobs.append(job)
        return jobs

    async def _run_job(self, job_id: str):
        try:
            job = await self.get_job(job_id)
            if not job:
                return
            topic = job["topic"]
            depth = job["depth"]
            max_sources = job["max_sources"]
            language = job["language"]
            tags = job["tags"]

            # Phase 1: Search
            await self._update_job(job_id, status=JobStatus.SEARCHING.value,
                                   progress={"phase": "searching", "topic": topic})
            search_results = await self.searxng.search(topic, max_results=max_sources, language=language)
            if not search_results:
                await self._update_job(job_id, status=JobStatus.FAILED.value,
                                       error="No search results found")
                return

            # Take top N URLs by depth
            urls_to_crawl = [r["url"] for r in search_results[:depth]]
            await self._update_job(job_id, progress={
                "phase": "searching_done",
                "candidate_urls": len(urls_to_crawl),
            })

            # Phase 2: Crawl (concurrent)
            await self._update_job(job_id, status=JobStatus.CRAWLING.value,
                                   progress={"phase": "crawling", "crawled": 0, "total": len(urls_to_crawl)})
            crawl_results = []
            sem = asyncio.Semaphore(4)  # 4 concurrent

            async def crawl_one(url: str, idx: int):
                async with sem:
                    res = await self.crawl4ai.crawl(url)
                    if res:
                        crawl_results.append(res)
                    await self._update_job(job_id, progress={
                        "phase": "crawling",
                        "crawled": len(crawl_results),
                        "total": len(urls_to_crawl),
                    })

            await asyncio.gather(*[crawl_one(u, i) for i, u in enumerate(urls_to_crawl)])
            await self._update_job(job_id, sources_count=len(crawl_results))

            if not crawl_results:
                await self._update_job(job_id, status=JobStatus.FAILED.value,
                                       error="No pages crawled successfully")
                return

            # Phase 3: Chunk + Embed
            await self._update_job(job_id, status=JobStatus.EMBEDDING.value,
                                   progress={"phase": "embedding", "embedded": 0, "total": 0})

            all_chunks: list[dict[str, Any]] = []
            for res in crawl_results:
                md = res.get("markdown", "")
                if not md:
                    continue
                chunks = chunk_text(md, self.cfg.chunk_size, self.cfg.chunk_overlap)
                for chunk in chunks:
                    all_chunks.append({
                        "text": chunk,
                        "source_url": res["url"],
                        "source_title": res.get("title", ""),
                    })

            await self._update_job(job_id, progress={
                "phase": "embedding",
                "embedded": 0,
                "total": len(all_chunks),
            })

            # Embed in batches (sequential to avoid overloading Ollama)
            points: list[dict] = []
            for i, chunk in enumerate(all_chunks):
                try:
                    vector = await self.ollama.embed(chunk["text"])
                except Exception as e:
                    logger.warning(f"Embedding failed for chunk {i}: {e}")
                    continue
                point_id = str(uuid.uuid4())
                points.append({
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "text": chunk["text"],
                        "source_url": chunk["source_url"],
                        "source_title": chunk["source_title"],
                        "topic": topic,
                        "tags": tags,
                        "job_id": job_id,
                        "ingested_at": utcnow(),
                    },
                })
                if (i + 1) % 10 == 0:
                    await self._update_job(job_id, progress={
                        "phase": "embedding",
                        "embedded": i + 1,
                        "total": len(all_chunks),
                    })

            # Phase 4: Store in Qdrant
            if points:
                # Run sync qdrant upsert in a thread
                await asyncio.to_thread(self.qdrant.upsert, points)

            await self._update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                chunks_count=len(points),
                progress={
                    "phase": "completed",
                    "sources_count": len(crawl_results),
                    "chunks_ingested": len(points),
                },
            )

        except Exception as e:
            logger.exception(f"Job {job_id} failed")
            await self._update_job(job_id, status=JobStatus.FAILED.value, error=str(e))


async def ensure_embedding_model(ollama: OllamaClient, model: str):
    """Pull the embedding model if not already present."""
    import httpx
    async with httpx.AsyncClient(timeout=300.0) as client:
        r = await client.get(f"{ollama.base_url}/api/tags")
        if r.status_code != 200:
            return
        data = r.json()
        existing = {m["name"].split(":")[0] for m in data.get("models", [])}
        if model.split(":")[0] not in existing:
            logger.info(f"Pulling embedding model: {model}")
            r = await client.post(
                f"{ollama.base_url}/api/pull",
                json={"name": model},
                timeout=300.0,
            )
            r.raise_for_status()
