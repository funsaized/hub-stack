"""Research job orchestration: search -> crawl -> chunk -> embed -> store."""

import asyncio
import hashlib
import json
import logging
import re
import uuid
import posixpath
import time
from collections import Counter
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as redis_async

from .config import Config
from .clients import OllamaClient, QdrantClient, SearXNGClient, Crawl4AIClient
from .context import classify_and_sanitize
from .models import JobStatus, ResearchRequest
from .document_store import DocumentStore
from .retrieval import ScopedRetrievalService
from .claim_support import ClaimVerifierClient
from .observability import (
    CHUNKS, CRAWLS, EMBED_LATENCY, JOB_PHASE_LATENCY, SEARCH_RESULTS,
    UPSERT_LATENCY, phase_timer,
)
from tenacity import (
    AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential,
)

logger = logging.getLogger(__name__)

CHUNKER_VERSION = "recursive-v1"
EXTRACTION_VERSION = "crawl4ai-markdown-v1"
TRACKING_QUERY_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def canonicalize_url(url: str) -> str:
    """Normalize a web URL into a stable source identity."""
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    path = posixpath.normpath(parsed.path or "/")
    if not path.startswith("/"):
        path = f"/{path}"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(sorted(
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_PARAMETERS
    ))
    return urlunsplit((scheme, hostname, path, query, ""))


def document_identity(url: str, content: str) -> tuple[str, str, str]:
    canonical_url = canonicalize_url(url)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{canonical_url}\n{content_hash}"))
    return canonical_url, content_hash, document_id


def chunk_identity(document_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(
        uuid.UUID(document_id), f"{CHUNKER_VERSION}:{chunk_index}"
    ))


def document_is_complete(existing: list[dict], document_id: str, chunk_count: int) -> bool:
    """True only when Qdrant contains exactly every expected chunk for this version."""
    expected_ids = {chunk_identity(document_id, index) for index in range(chunk_count)}
    actual_ids = {
        item["id"] for item in existing
        if item["payload"].get("document_id") == document_id
    }
    return actual_ids == expected_ids and len(existing) == len(expected_ids)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_domain(value: str) -> str:
    return value.strip().lower().lstrip(".")


def domain_matches(hostname: str, configured: set[str]) -> bool:
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in configured)


def parse_source_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def source_quality_score(result: dict) -> float:
    """Transparent authority/completeness heuristic, separate from relevance."""
    host = (urlsplit(result.get("url", "")).hostname or "").lower()
    score = 0.5
    if host.endswith((".gov", ".edu")):
        score += 0.25
    if result.get("title"):
        score += 0.1
    if result.get("snippet"):
        score += 0.1
    return round(min(score, 1.0), 3)


def apply_source_policy(results: list[dict], req: ResearchRequest,
                        now: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Normalize, deduplicate, filter, and annotate ordered search results."""
    allowed = {normalize_domain(v) for v in req.allowed_domains if normalize_domain(v)}
    blocked = {normalize_domain(v) for v in req.blocked_domains if normalize_domain(v)}
    domain_counts: Counter[str] = Counter()
    seen: set[str] = set()
    accepted: list[dict] = []
    decisions: list[dict] = []
    cutoff = (now or datetime.now(timezone.utc))
    for result in results:
        original = result.get("url", "")
        canonical = canonicalize_url(original)
        host = (urlsplit(canonical).hostname or "").lower()
        reason = "accepted"
        published = parse_source_date(result.get("published_at"))
        if not host or urlsplit(canonical).scheme not in {"http", "https"}:
            reason = "invalid_url"
        elif canonical in seen:
            reason = "duplicate"
        elif allowed and not domain_matches(host, allowed):
            reason = "not_allowed"
        elif domain_matches(host, blocked):
            reason = "blocked"
        elif domain_counts[host] >= req.per_domain_limit:
            reason = "domain_limit"
        elif req.freshness_days and (
            published is None or (cutoff - published).days > req.freshness_days
        ):
            reason = "stale_or_undated"
        decision = {"url": original, "canonical_url": canonical, "domain": host,
                    "decision": reason}
        decisions.append(decision)
        seen.add(canonical)
        if reason != "accepted":
            continue
        domain_counts[host] += 1
        enriched = dict(result, url=canonical, canonical_url=canonical,
                        published_at=published.isoformat() if published else None,
                        quality_score=source_quality_score(result),
                        freshness_days=(cutoff - published).days if published else None)
        accepted.append(enriched)
    return accepted, decisions


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
        self.documents = DocumentStore(cfg.document_store_path)
        self.retrieval = ScopedRetrievalService(
            self.ollama,
            self.qdrant,
            self.documents,
            candidate_limit=cfg.report_retrieval_candidates,
            max_chunks_per_source=cfg.report_max_chunks_per_source,
            min_score=cfg.report_retrieval_min_score,
        )
        self.claim_verifier = ClaimVerifierClient(
            cfg.claim_verifier_url, cfg.claim_verifier_timeout_seconds
        )
        self._redis: redis_async.Redis | None = None

    async def init(self):
        self._redis = redis_async.from_url(self.cfg.redis_url, decode_responses=True)

    async def close(self):
        await self.ollama.close()
        await self.searxng.close()
        await self.crawl4ai.close()
        await self.claim_verifier.close()
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

        ollama_ok, qdrant_ok, redis_ok, searxng_ok, crawl4ai_ok, verifier_ok = await asyncio.gather(
            check("ollama", self.ollama.health),
            check("qdrant", lambda: asyncio.to_thread(self.qdrant.health)),
            check("redis", redis_health),
            check("searxng", self.searxng.health),
            check("crawl4ai", self.crawl4ai.health),
            check("claim_verifier", self.claim_verifier.health),
        )
        services = {
            "ollama": ollama_ok,
            "qdrant": qdrant_ok,
            "redis": redis_ok,
            "searxng": searxng_ok,
            "crawl4ai": crawl4ai_ok,
            "claim_verifier": verifier_ok,
        }
        return {**services, "all_ok": all(services.values())}

    def _job_key(self, job_id: str) -> str:
        return f"research:job:{job_id}"

    def _job_index_key(self) -> str:
        return "research:jobs"

    def _pending_queue_key(self) -> str:
        return "research:queue:pending"

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
            "allowed_domains": req.allowed_domains,
            "blocked_domains": req.blocked_domains,
            "per_domain_limit": req.per_domain_limit,
            "freshness_days": req.freshness_days,
            "status": JobStatus.PENDING.value,
            "created_at": now,
            "updated_at": now,
            "progress": {"phase": "queued"},
            "error": None,
            "sources_count": 0,
            "chunks_count": 0,
            "attempts": 0,
        }
        # The transaction makes a visible job and its queue entry durable together.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(self._job_key(job_id), json.dumps(job))
            pipe.lpush(self._job_index_key(), job_id)
            pipe.lpush(self._pending_queue_key(), job_id)
            await pipe.execute()
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

    async def get_report(self, job_id: str) -> dict | None:
        return await asyncio.to_thread(self.documents.get_report, job_id)

    async def generate_report(self, job_id: str) -> dict:
        # Local import keeps synthesis isolated from ingestion and avoids a module cycle.
        from .synthesis import generate_report
        return await generate_report(self, job_id)

    async def run_job(self, job_id: str):
        """Execute a claimed job. Queue ownership and retries belong to IngestionWorker."""
        phase = "load"
        try:
            job = await self.get_job(job_id)
            if not job:
                return
            topic = job["topic"]
            depth = job["depth"]
            max_sources = job["max_sources"]
            language = job["language"]
            tags = job["tags"]
            respect_robots = getattr(self.cfg, "respect_robots_txt", True)
            request = ResearchRequest(
                topic=topic, depth=depth, max_sources=max_sources,
                language=language, tags=tags,
                allowed_domains=job.get("allowed_domains", []),
                blocked_domains=job.get("blocked_domains", []),
                per_domain_limit=job.get("per_domain_limit", 2),
                freshness_days=job.get("freshness_days"),
            )

            # Phase 1: Search
            phase = "search"
            await self._update_job(job_id, status=JobStatus.SEARCHING.value,
                                   progress={"phase": "searching", "topic": topic})
            with phase_timer("search", logger, job_id=job_id):
                search_results = await self.searxng.search(
                    topic, max_results=max_sources, language=language
                )
            SEARCH_RESULTS.observe(len(search_results))
            if not search_results:
                raise RuntimeError("No search results found")

            policy_results, policy_decisions = apply_source_policy(search_results, request)
            selected_results = policy_results[:depth]
            urls_to_crawl = [r["url"] for r in selected_results]
            if not urls_to_crawl:
                raise RuntimeError("No search results passed crawl policy")
            await self._update_job(job_id, progress={
                "phase": "searching_done",
                "candidate_urls": len(urls_to_crawl),
                "crawl_policy": policy_decisions,
            })

            # Phase 2: Crawl (concurrent)
            phase = "crawl"
            await self._update_job(job_id, status=JobStatus.CRAWLING.value,
                                   progress={"phase": "crawling", "crawled": 0, "total": len(urls_to_crawl)})
            crawl_results = []
            crawl_phase_started = time.monotonic()
            sem = asyncio.Semaphore(4)  # 4 concurrent

            async def crawl_one(url: str, idx: int):
                async with sem:
                    domain = urlsplit(url).hostname or "unknown"
                    started = time.monotonic()
                    try:
                        res = await self.crawl4ai.crawl(
                            url, respect_robots_txt=respect_robots
                        )
                    except Exception:
                        CRAWLS.labels("failed").inc()
                        logger.exception("crawl_failed", extra={
                            "job_id": job_id, "phase": "crawl", "source_url": url,
                            "source_domain": domain,
                            "duration_seconds": round(time.monotonic() - started, 4),
                            "failure_category": "dependency_error",
                        })
                        raise
                    CRAWLS.labels("success" if res else "failed").inc()
                    logger.info("crawl_completed", extra={
                        "job_id": job_id, "phase": "crawl", "source_url": url,
                        "source_domain": domain,
                        "duration_seconds": round(time.monotonic() - started, 4),
                    })
                    if res:
                        res["policy_metadata"] = selected_results[idx]
                        crawl_results.append(res)
                    await self._update_job(job_id, progress={
                        "phase": "crawling",
                        "crawled": len(crawl_results),
                        "total": len(urls_to_crawl),
                    })

            await asyncio.gather(*[crawl_one(u, i) for i, u in enumerate(urls_to_crawl)])
            crawl_phase_elapsed = time.monotonic() - crawl_phase_started
            JOB_PHASE_LATENCY.labels("crawl").observe(crawl_phase_elapsed)
            logger.info("phase_completed", extra={
                "job_id": job_id, "phase": "crawl",
                "duration_seconds": round(crawl_phase_elapsed, 4),
            })
            await self._update_job(job_id, sources_count=len(crawl_results))

            if not crawl_results:
                raise RuntimeError("No pages crawled successfully")

            # Phase 3: retain canonical documents, then chunk/embed in bounded batches.
            phase = "ingest"
            await self._update_job(job_id, status=JobStatus.EMBEDDING.value,
                                   progress={"phase": "embedding", "embedded": 0, "total": 0})

            duplicate_sources = 0
            ingest_phase_started = time.monotonic()
            skipped_chunks = 0
            chunks_ingested = 0
            total_batch_seconds = 0.0
            batches_completed = 0
            for res in crawl_results:
                md = res.get("markdown", "")
                if not md:
                    continue
                policy_metadata = res.setdefault("policy_metadata", {})
                if not policy_metadata.get("published_at"):
                    http_metadata = res.get("http_metadata", {})
                    published = parse_source_date(
                        http_metadata.get("published_time")
                        or http_metadata.get("date")
                        or http_metadata.get("last_modified")
                    )
                    if published:
                        policy_metadata["published_at"] = published.isoformat()
                        policy_metadata["freshness_days"] = max(
                            0, (datetime.now(timezone.utc) - published).days
                        )
                chunks = chunk_text(md, self.cfg.chunk_size, self.cfg.chunk_overlap)
                CHUNKS.observe(len(chunks))
                canonical_url, content_hash, document_id = document_identity(res["url"], md)
                fetched_at = utcnow()
                research_metadata = {"topic": topic, "tags": tags}
                await asyncio.to_thread(self.documents.save, {
                    "document_id": document_id,
                    "canonical_url": canonical_url,
                    "source_url": res["url"],
                    "title": res.get("title", ""),
                    "markdown": md,
                    "content_hash": content_hash,
                    "fetched_at": fetched_at,
                    "http_metadata": res.get("http_metadata", {}),
                    "extraction_version": EXTRACTION_VERSION,
                    "job_id": job_id,
                    "research_metadata": research_metadata,
                    "created_at": fetched_at,
                })
                await asyncio.to_thread(
                    self.documents.observe_job_source,
                    job_id, document_id, fetched_at, research_metadata,
                )
                existing = await asyncio.to_thread(
                    self.qdrant.document_chunks, canonical_url
                )
                if document_is_complete(existing, document_id, len(chunks)):
                    duplicate_sources += 1
                    skipped_chunks += len(chunks)
                    continue
                # Qdrant is authoritative if either store was restored independently.
                current_ids = {
                    item["id"] for item in existing
                    if item["payload"].get("document_id") == document_id
                }
                completed = 0
                while completed < len(chunks) and chunk_identity(document_id, completed) in current_ids:
                    completed += 1
                for start, batch in embedding_batches(
                    chunks, completed, self.cfg.embedding_batch_size,
                    self.cfg.embedding_batch_chars,
                ):
                    started = time.monotonic()
                    embed_started = time.monotonic()
                    vectors = await self._retry_async(self.ollama.embed_batch, batch)
                    EMBED_LATENCY.observe(time.monotonic() - embed_started)
                    points = []
                    for offset, (chunk, vector) in enumerate(zip(batch, vectors)):
                        chunk_index = start + offset
                        safe_chunk, security_labels = classify_and_sanitize(chunk)
                        points.append({
                            "id": chunk_identity(document_id, chunk_index),
                            "vector": vector,
                            "payload": {
                                "text": safe_chunk, "source_url": canonical_url,
                                "source_title": res.get("title", ""),
                                "canonical_url": canonical_url, "content_hash": content_hash,
                                "document_id": document_id, "chunk_index": chunk_index,
                                "chunker_version": CHUNKER_VERSION, "topic": topic,
                                "tags": tags, "job_id": job_id, "ingested_at": utcnow(),
                                "published_at": res.get("policy_metadata", {}).get("published_at"),
                                "fetched_at": fetched_at,
                                "source_quality_score": res.get("policy_metadata", {}).get("quality_score", 0.5),
                                "source_freshness_days": res.get("policy_metadata", {}).get("freshness_days"),
                                "security_labels": security_labels,
                                "robots_respected": respect_robots,
                            },
                        })
                    upsert_started = time.monotonic()
                    await self._retry_async(asyncio.to_thread, self.qdrant.upsert, points)
                    UPSERT_LATENCY.observe(time.monotonic() - upsert_started)
                    completed = start + len(batch)
                    await asyncio.to_thread(
                        self.documents.set_checkpoint, self.qdrant.collection,
                        document_id, CHUNKER_VERSION, completed, len(chunks), utcnow(),
                    )
                    elapsed = time.monotonic() - started
                    batches_completed += 1
                    total_batch_seconds += elapsed
                    chunks_ingested += len(batch)
                    await self._update_job(job_id, progress={
                        "phase": "embedding", "embedded": chunks_ingested,
                        "batch_size": len(batch), "batch_seconds": round(elapsed, 3),
                        "batches_completed": batches_completed,
                    })
                if completed == len(chunks):
                    await self._retry_async(
                        asyncio.to_thread, self.qdrant.delete_document,
                        canonical_url, except_document_id=document_id,
                    )

            ingest_phase_elapsed = time.monotonic() - ingest_phase_started
            JOB_PHASE_LATENCY.labels("ingest").observe(ingest_phase_elapsed)
            logger.info("phase_completed", extra={
                "job_id": job_id, "phase": "ingest",
                "duration_seconds": round(ingest_phase_elapsed, 4),
            })
            await self._update_job(
                job_id,
                status=JobStatus.COMPLETED.value,
                chunks_count=chunks_ingested,
                progress={
                    "phase": "completed",
                    "sources_count": len(crawl_results),
                    "chunks_ingested": chunks_ingested,
                    "duplicate_sources": duplicate_sources,
                    "chunks_skipped": skipped_chunks,
                    "batches_completed": batches_completed,
                    "average_batch_seconds": round(
                        total_batch_seconds / batches_completed, 3
                    ) if batches_completed else 0,
                    "crawl_policy": policy_decisions,
                    "robots_respected": respect_robots,
                },
            )
            # Synthesis is deliberately outside ingestion failure semantics. A failed
            # report remains independently retryable without crawling or embedding.
            try:
                await self.generate_report(job_id)
                await self._update_job(job_id, report_status="completed")
            except Exception as exc:
                logger.exception("report_synthesis_failed", extra={
                    "job_id": job_id, "phase": "synthesis",
                    "failure_category": type(exc).__name__,
                })
                await self._update_job(job_id, report_status="failed")

        except Exception as e:
            source = locals().get("res")
            source_url = source.get("url") if isinstance(source, dict) else None
            logger.exception("job_pipeline_failed", extra={
                "job_id": job_id, "phase": phase, "source_url": source_url,
                "failure_category": type(e).__name__,
            })
            raise

    async def _retry_async(self, operation, *args, **kwargs):
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.cfg.dependency_max_attempts),
            wait=wait_exponential(multiplier=0.25, min=0.25, max=4),
            retry=retry_if_exception_type(Exception), reraise=True,
        ):
            with attempt:
                return await operation(*args, **kwargs)


def embedding_batches(
    chunks: list[str], start: int, max_size: int, max_chars: int,
):
    """Yield bounded `(start_index, texts)` batches without retaining vectors."""
    index = start
    while index < len(chunks):
        batch: list[str] = []
        chars = 0
        while index + len(batch) < len(chunks) and len(batch) < max_size:
            value = chunks[index + len(batch)]
            if batch and chars + len(value) > max_chars:
                break
            batch.append(value)
            chars += len(value)
        yield index, batch
        index += len(batch)


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
