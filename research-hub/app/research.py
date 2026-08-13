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
from .clients import (
    Crawl4AIClient, OllamaClient, QdrantClient, SearXNGClient, SerperClient,
)
from .context import classify_and_sanitize
from .models import JobStatus, ResearchRequest
from .document_store import DocumentStore
from .url_policy import DestinationNotAllowed, vet_destination_async
from .query_plan import (
    RoundRecord,
    acquisition_provenance,
    cosine,
    facet_coverage,
    interleave,
    novelty_ratio,
    plan_gap_round,
    plan_queries,
    should_continue,
    single_query_plan,
)
from .retrieval import ScopedRetrievalService
from .judge_gate import JudgeClaimVerifier
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


def _query_coverage_rows(
    queries: list[str], query_results: dict[str, set[str]], retained: set[str],
) -> list[tuple[str, int, int]]:
    """Per-query (documents, distinct domains) for the gap pass to reason over.

    A query is credited with every retained document its own search surfaced,
    including documents a later round fetched -- coverage is about what the
    corpus answers, not about which query got there first. A facet showing
    zero documents is the strongest gap signal the pass has.
    """
    rows: list[tuple[str, int, int]] = []
    for query in queries:
        hit = query_results.get(query, set()) & retained
        domains = {urlsplit(url).hostname or "" for url in hit}
        rows.append((query, len(hit), len(domains)))
    return rows


SOURCE_PROBE_WINDOWS = 6
SOURCE_PROBE_CHARS = 500


def _topic_probe_windows(result: dict) -> list[str]:
    """Evenly spaced passages used to judge whether a document is on topic.

    Sampling across the whole document rather than its opening is the point:
    an opening probe measures how promptly a page restates its subject, which
    rewards blog intros and penalises reference documentation that starts with
    navigation.
    """
    title = (result.get("title") or "").strip()
    body = (result.get("markdown") or "").strip()
    if not body:
        return [title or ""]
    stride = max(1, (len(body) - SOURCE_PROBE_CHARS) // SOURCE_PROBE_WINDOWS)
    starts = sorted({
        min(index * stride, max(0, len(body) - SOURCE_PROBE_CHARS))
        for index in range(SOURCE_PROBE_WINDOWS)
    })
    windows = [body[start:start + SOURCE_PROBE_CHARS] for start in starts]
    # The title rides with the first window so a well-named page is not judged
    # on body text alone.
    windows[0] = f"{title}\n{windows[0]}" if title else windows[0]
    return windows


async def evaluate_crawl_result(result: dict, max_markdown_chars: int) -> None:
    """Reject a fetched document that landed on a disallowed destination or is
    oversized. Raises DestinationNotAllowed with the offending destination."""
    landing = result.get("final_url") or result.get("url", "")
    await vet_destination_async(landing)
    if len(result.get("markdown", "")) > max_markdown_chars:
        raise DestinationNotAllowed(landing, "response_too_large")


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


def build_claim_gate(cfg: Config):
    """The MiniMax judge is the claim gate (HUB-034; v4 final passed 2026-08-12).

    The NLI verifier service and its sealed v2 evaluation are retired — see
    docs/CURRENT_STATE.md. The judge was validated by the sealed v4 blind
    evaluation and re-baselines on any served-model change."""
    return JudgeClaimVerifier(
        base_url=cfg.judge_base_url,
        api_key=cfg.judge_api_key,
        model=cfg.judge_model,
        timeout=cfg.judge_timeout_seconds,
    )


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
        self.searxng = SearXNGClient(
            cfg.searxng_url, getattr(cfg, "searxng_engines", None)
        )
        self.serper = SerperClient(
            getattr(cfg, "serper_api_key", ""),
            getattr(cfg, "serper_base_url", "https://google.serper.dev"),
        )
        self.crawl4ai = Crawl4AIClient(cfg.crawl4ai_url, cfg.crawl4ai_token or None)
        self.documents = DocumentStore(cfg.document_store_path)
        self.retrieval = ScopedRetrievalService(
            self.ollama,
            self.qdrant,
            self.documents,
            candidate_limit=cfg.report_retrieval_candidates,
            max_chunks_per_source=cfg.report_max_chunks_per_source,
            min_score=cfg.report_retrieval_min_score,
            lexical=self.documents if cfg.report_hybrid_retrieval else None,
            rrf_k=cfg.report_rrf_k,
        )
        self.claim_verifier = build_claim_gate(cfg)
        self._redis: redis_async.Redis | None = None

    async def init(self):
        self._redis = redis_async.from_url(self.cfg.redis_url, decode_responses=True)

    async def close(self):
        await self.ollama.close()
        await self.searxng.close()
        await self.serper.close()
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
        job = json.loads(data)
        # SQLite is authoritative for persisted reports. The Redis projection
        # is a separate later write, so a crash between the two can strand a
        # stale value; deriving from the persisted row here means no reader
        # can see a report status that contradicts SQLite.
        report_status = await asyncio.to_thread(self.documents.report_status, job_id)
        if report_status is not None:
            job["report_status"] = report_status
        return job

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

    async def _rank_by_snippet(
        self, topic: str, results_by_facet: list[list[dict]], *,
        job_id: str | None = None,
    ) -> tuple[list[list[dict]], dict]:
        """Order each facet's results by snippet relevance before interleaving.

        The crawl cap decides which candidates get fetched, so ordering decides
        what the budget is spent on. Ranking rather than thresholding is
        deliberate: a cutoff on title+snippet would need its own calibrated
        number, and snippets score differently from the full documents
        PLAN_SOURCE_RELEVANCE was measured on. Ranking wastes no crawl on junk
        without ever discarding a candidate on a guessed threshold.

        Ranks WITHIN each facet, never across them, so interleaving still
        shares the budget between facets (ADR-002 stage 1).
        """
        flat = [r for facet in results_by_facet for r in facet]
        if not flat:
            return results_by_facet, {"applied": False, "reason": "no_results"}
        probes = [topic] + [
            (r.get("title") or "") + "\n" + (r.get("snippet") or "")
            for r in flat
        ]
        try:
            vectors = await self.ollama.embed_batch(probes)
            if len(vectors) != len(probes):
                raise RuntimeError("embedding count mismatch")
        except Exception as exc:
            logger.warning("snippet_ranking_unavailable", extra={
                "job_id": job_id, "failure_reason": type(exc).__name__,
            })
            return results_by_facet, {"applied": False,
                                      "reason": "embedding_unavailable"}

        topic_vector = vectors[0]
        scores = {
            id(result): cosine(vector, topic_vector)
            for result, vector in zip(flat, vectors[1:])
        }
        ranked = [
            sorted(facet, key=lambda r: scores[id(r)], reverse=True)
            for facet in results_by_facet
        ]
        ordered = sorted(scores.values(), reverse=True)
        return ranked, {
            "applied": True, "reason": "ranked", "candidates": len(flat),
            "best": round(ordered[0], 4), "worst": round(ordered[-1], 4),
            "median": round(ordered[len(ordered) // 2], 4),
        }

    async def _screen_sources(
        self, topic: str, results: list[dict], *, job_id: str | None = None,
        anchor_queries: list[str] | None = None,
    ) -> tuple[list[dict], dict]:
        """Drop retained documents that are not about the topic.

        Scores each document's title plus opening text against the topic with
        the already-deployed embedding model — one extra local batch call.

        Two deliberate safety properties. A screening failure keeps every
        document, because losing the corpus to a flaky embed call is far worse
        than a few off-topic sources. And a screen that would empty the job
        keeps everything too: that means the threshold is wrong for this
        topic, not that the research found nothing.
        """
        floor = getattr(self.cfg, "plan_source_relevance", 0.0)
        windows = [_topic_probe_windows(r) for r in results]
        # Anchor on the RAW TOPIC. Facet anchoring was tried and reverted: on a
        # 494-document labelled reference set it scored AUC 0.741 against the
        # topic's 0.857 on identical rows, and was worse than random (0.426) on
        # one topic. It won only on the single ambiguous topic it had been
        # tuned against -- overfitting to n=1. `anchor_queries` is kept in the
        # signature because facet anchoring remains the better choice for
        # detectably ambiguous topics, which is recorded as future work rather
        # than guessed at now.
        anchors = [topic]
        probes = list(anchors) + [w for doc in windows for w in doc]
        try:
            vectors = await self.ollama.embed_batch(probes)
            if len(vectors) != len(probes):
                raise RuntimeError("embedding count mismatch")
        except Exception as exc:
            logger.warning("source_screening_unavailable", extra={
                "job_id": job_id, "failure_reason": type(exc).__name__,
            })
            return results, {"applied": False, "reason": "embedding_unavailable",
                             "kept": len(results), "dropped": 0, "scores": []}

        anchor_vectors = vectors[:len(anchors)]
        scored: list[tuple[dict, float]] = []
        cursor = len(anchors)
        for result, doc_windows in zip(results, windows):
            span = vectors[cursor:cursor + len(doc_windows)]
            cursor += len(doc_windows)
            # A document is on topic if ANY substantial passage of it is. Its
            # opening is not representative: reference documentation begins
            # with navigation boilerplate while blog posts begin by restating
            # the topic, which scored redis.io below generic tutorials when
            # only the opening was probed (measured 2026-08-13).
            scored.append((result, max(
                (cosine(window, anchor)
                 for window in span for anchor in anchor_vectors),
                default=0.0,
            )))
        kept = [result for result, score in scored if score >= floor]
        scores = [
            {"url": (r.get("policy_metadata") or {}).get("canonical_url")
             or r.get("url", ""),
             "topic_cosine": round(score, 4), "kept": score >= floor}
            for r, score in scored
        ]
        if not kept:
            logger.warning("source_screening_would_empty_job", extra={
                "job_id": job_id, "floor": floor,
            })
            return results, {"applied": False, "reason": "would_empty_job",
                             "kept": len(results), "dropped": 0,
                             "floor": floor, "scores": scores}

        # `diagnostic` is one of the few extras the JSON formatter passes
        # through; bare keys are silently dropped.
        logger.info("source_screening_completed", extra={
            "job_id": job_id, "phase": "ingest",
            "diagnostic": {"kept": len(kept), "dropped": len(results) - len(kept),
                           "floor": floor},
        })
        return kept, {"applied": True, "reason": "screened", "floor": floor,
                      "kept": len(kept), "dropped": len(results) - len(kept),
                      "scores": scores}

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
            # Acquisition breadth is emergent (HUB-024): the planner admits a
            # facet only while it adds retrieval intent the plan lacks. With
            # planning disabled -- or whenever candidates collapse into the
            # topic -- the plan is exactly [topic], so the loop below issues the
            # single search the pre-planning path issued.
            planning_enabled = getattr(self.cfg, "report_query_planning", False)
            if planning_enabled:
                plan = await plan_queries(
                    self.ollama, topic,
                    distinct=self.cfg.plan_facet_distinct,
                    relevance=self.cfg.plan_facet_relevance,
                    max_facets=self.cfg.plan_max_facets,
                    search_budget=self.cfg.plan_search_budget,
                    job_id=job_id,
                )
            else:
                plan = single_query_plan(topic, "planning_disabled")
            logger.info("query_plan_built", extra={
                "job_id": job_id, "phase": "search",
                "facet_count": len(plan.queries),
                "plan_stop_reason": plan.stop_reason,
            })

            # Rounds only run for a plan that actually admitted breadth. A
            # collapsed plan must issue exactly one search, which is what keeps
            # a simple topic equivalent to the pre-planning path.
            rounds_enabled = planning_enabled and not plan.collapsed
            # Breadth has to buy fetches or it buys nothing. `depth` is the
            # per-FACET allowance for a planned job, so a five-facet plan can
            # retain five facets' worth of sources in one round instead of
            # interleaving them into a single-query budget -- which is what
            # made the 2026-08-13 run 4 collapse back to baseline breadth
            # (100 candidates seen, 6 crawled). PLAN_CRAWL_BUDGET is the
            # job-wide rail. A collapsed plan keeps the single-query budget
            # exactly.
            total_crawl_cap = (
                self.cfg.plan_crawl_budget if rounds_enabled else depth
            )

            crawl_results = []
            policy_decisions: list[dict] = []
            plan_decisions = list(plan.decisions)
            rounds: list[RoundRecord] = []
            seen_canonical: set[str] = set()
            issued_queries = list(plan.queries)
            issued_vectors = list(plan.vectors)
            search_pacing = getattr(self.cfg, "search_pacing_seconds", 0.0)
            snippet_ranking: dict | None = None
            search_providers: Counter = Counter()
            query_results: dict[str, set[str]] = {}
            covered_facets = 0
            issued_facets = 0
            round_queries = list(plan.queries)
            crawls_attempted = 0
            acquisition_stop = "single_round"
            crawl_phase_started = time.monotonic()
            sem = asyncio.Semaphore(4)  # 4 concurrent

            async def crawl_one(url: str, idx: int, round_selected: list[dict]):
                async with sem:
                    domain = urlsplit(url).hostname or "unknown"
                    started = time.monotonic()
                    # SSRF policy, pass 1: vet scheme, port, and every DNS
                    # answer for the requested URL before the crawler sees it.
                    try:
                        await vet_destination_async(url)
                    except DestinationNotAllowed as exc:
                        CRAWLS.labels("rejected").inc()
                        logger.warning("crawl_rejected", extra={
                            "job_id": job_id, "phase": "crawl", "source_url": url,
                            "source_domain": domain,
                            "normalized_destination": exc.destination,
                            "rejection_reason": exc.reason,
                        })
                        return
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
                    # SSRF policy, pass 2: reject the document when the fetch
                    # landed somewhere disallowed (redirects) or is oversized.
                    if res:
                        try:
                            await evaluate_crawl_result(
                                res,
                                getattr(self.cfg, "crawl_max_markdown_chars", 2_000_000),
                            )
                        except DestinationNotAllowed as exc:
                            CRAWLS.labels("rejected").inc()
                            logger.warning("crawl_rejected", extra={
                                "job_id": job_id, "phase": "crawl",
                                "source_url": url, "source_domain": domain,
                                "normalized_destination": exc.destination,
                                "rejection_reason": exc.reason,
                            })
                            return
                    CRAWLS.labels("success" if res else "failed").inc()
                    logger.info("crawl_completed", extra={
                        "job_id": job_id, "phase": "crawl", "source_url": url,
                        "source_domain": domain,
                        "duration_seconds": round(time.monotonic() - started, 4),
                    })
                    if res:
                        res["policy_metadata"] = round_selected[idx]
                        crawl_results.append(res)
                    await self._update_job(job_id, progress={
                        "phase": "crawling",
                        "crawled": len(crawl_results),
                        "total": crawls_attempted,
                    })

            round_index = 0
            while round_queries:
                round_index += 1
                phase = "search"
                await self._update_job(job_id, status=JobStatus.SEARCHING.value,
                                       progress={"phase": "searching", "topic": topic,
                                                 "round": round_index})
                with phase_timer("search", logger, job_id=job_id):
                    # Sequential and paced. Firing a plan's queries in
                    # immediate succession is what CAPTCHAs an engine:
                    # SearXNG's own guidance is to back off, and this stack
                    # ran ~250 queries in bursts before four of six engines
                    # blocked (ADR-002 stage 1). Seconds per job against a
                    # ~180s job is a rounding error.
                    results_by_facet = []
                    for index, query in enumerate(round_queries):
                        if index and search_pacing > 0:
                            await asyncio.sleep(search_pacing)
                        facet_results = await self.searxng.search(
                            query, max_results=max_sources, language=language
                        )
                        # SearXNG stays primary. The keyed fallback engages
                        # only when a query returns nothing -- which is what a
                        # fully blocked engine pool looks like -- so the
                        # private path remains the default and the paid path
                        # is insurance (ADR-002 stage 2).
                        if not facet_results and self.serper.configured:
                            facet_results = await self.serper.search(
                                query, max_results=max_sources, language=language
                            )
                            search_providers["serper" if facet_results
                                             else "none"] += 1
                            if facet_results:
                                logger.info("search_fallback_used", extra={
                                    "job_id": job_id, "phase": "search",
                                    "diagnostic": {"provider": "serper",
                                                   "results": len(facet_results)},
                                })
                        else:
                            search_providers[
                                "searxng" if facet_results else "none"] += 1
                        results_by_facet.append(facet_results)
                # Spend the crawl cap on the most relevant candidates rather
                # than on whatever the engine happened to rank first.
                if planning_enabled:
                    results_by_facet, snippet_ranking = await self._rank_by_snippet(
                        topic, results_by_facet, job_id=job_id,
                    )
                merged = interleave(results_by_facet)
                if round_index == 1:
                    SEARCH_RESULTS.observe(len(merged))
                    if not merged:
                        raise RuntimeError("No search results found")

                # One policy pass per round is what makes every sub-query
                # inherit the full HUB-020/021 source policy and deduplicate
                # canonical URLs across facets; `seen_canonical` extends that
                # deduplication across rounds, so no document is fetched twice.
                accepted, decisions = apply_source_policy(merged, request)
                policy_decisions.extend(decisions)
                # Saturation is measured over the documents this round would
                # actually fetch -- its top `depth` results -- not over the
                # whole candidate pool. A round surfaces far more candidates
                # than it can crawl, so a pool-wide denominator pins novelty
                # near 1.0 and the signal can never fire (measured 2026-08-13:
                # 1.0 / 1.0 / 0.983 over three rounds that had plainly
                # saturated). Computed before `seen_canonical` is updated.
                novelty_window = accepted[:depth]
                new_in_window = [
                    r for r in novelty_window
                    if r["canonical_url"] not in seen_canonical
                ]
                fresh = [
                    r for r in accepted if r["canonical_url"] not in seen_canonical
                ]
                round_allowance = (
                    depth * max(1, len(round_queries)) if rounds_enabled else depth
                )
                round_cap = max(
                    0, min(round_allowance, total_crawl_cap - crawls_attempted)
                )
                selected_results = fresh[:round_cap]
                for result in selected_results:
                    seen_canonical.add(result["canonical_url"])
                urls_to_crawl = [r["url"] for r in selected_results]
                if round_index == 1 and not urls_to_crawl:
                    raise RuntimeError("No search results passed crawl policy")
                crawls_attempted += len(urls_to_crawl)
                for query, facet_results in zip(round_queries, results_by_facet):
                    query_results[query] = {
                        canonicalize_url(r.get("url", "")) for r in facet_results
                    }
                await self._update_job(job_id, progress={
                    "phase": "searching_done",
                    "round": round_index,
                    "candidate_urls": len(urls_to_crawl),
                    "crawl_policy": policy_decisions,
                    "query_plan": acquisition_provenance(
                        plan, issued_queries=issued_queries,
                        decisions=plan_decisions, rounds=rounds,
                        stop_reason=acquisition_stop,
                    ),
                })

                # Phase 2: Crawl (concurrent)
                phase = "crawl"
                await self._update_job(
                    job_id, status=JobStatus.CRAWLING.value,
                    progress={"phase": "crawling", "crawled": len(crawl_results),
                              "total": crawls_attempted, "round": round_index},
                )
                crawled_before = len(crawl_results)
                await asyncio.gather(*[
                    crawl_one(u, i, selected_results)
                    for i, u in enumerate(urls_to_crawl)
                ])
                retained_canonical = {
                    (r.get("policy_metadata") or {}).get("canonical_url")
                    or canonicalize_url(r.get("url", ""))
                    for r in crawl_results
                }
                previous_covered = covered_facets
                covered_facets, issued_facets = facet_coverage(
                    query_results, retained_canonical
                )
                rounds.append(RoundRecord(
                    index=round_index, queries=list(round_queries),
                    candidates=len(novelty_window),
                    new_candidates=len(new_in_window),
                    novelty=novelty_ratio(len(new_in_window),
                                          len(novelty_window)),
                    crawled=len(crawl_results) - crawled_before,
                    pool=len(accepted),
                    covered_facets=covered_facets, issued_facets=issued_facets,
                ))

                round_queries = []
                if not rounds_enabled:
                    break
                # Coverage plateau first, rails next, the LLM last. The gap
                # pass is advisory: it can propose what to ask, and it can end
                # the research by declining, but it is checked only after the
                # arithmetic signal, because sufficiency judgements of exactly
                # this kind measure poorly (RaCGEval, 2411.05547) and unaligned
                # models default to answering rather than declining
                # (2507.04976).
                continue_rounds, reason = should_continue(
                    round_index=round_index,
                    covered_facets=covered_facets,
                    issued_facets=issued_facets,
                    previous_covered_facets=previous_covered,
                    max_rounds=self.cfg.plan_max_rounds,
                )
                if not continue_rounds:
                    acquisition_stop = reason
                    break
                if crawls_attempted >= total_crawl_cap:
                    acquisition_stop = "budget"
                    break
                gap_queries, issued_vectors, gap_decisions, gap_reason = (
                    await plan_gap_round(
                        self.ollama, topic,
                        _query_coverage_rows(issued_queries, query_results,
                                             retained_canonical),
                        issued_queries, issued_vectors,
                        distinct=self.cfg.plan_facet_distinct,
                        relevance=self.cfg.plan_facet_relevance,
                        max_total=self.cfg.plan_search_budget,
                        job_id=job_id,
                    )
                )
                plan_decisions.extend(gap_decisions)
                if not gap_queries:
                    acquisition_stop = gap_reason
                    break
                issued_queries.extend(gap_queries)
                round_queries = gap_queries

            logger.info("acquisition_completed", extra={
                "job_id": job_id, "phase": "search",
                "rounds": len(rounds), "queries_issued": len(issued_queries),
                "plan_stop_reason": acquisition_stop,
            })
            # Record the settled plan as soon as acquisition ends, so a job
            # that fails during ingestion still shows why it stopped searching.
            await self._update_job(job_id, progress={
                "phase": "acquisition_done",
                "crawl_policy": policy_decisions,
                "query_plan": acquisition_provenance(
                    plan, issued_queries=issued_queries,
                    decisions=plan_decisions, rounds=rounds,
                    stop_reason=acquisition_stop,
                ),
            })
            crawl_phase_elapsed = time.monotonic() - crawl_phase_started
            JOB_PHASE_LATENCY.labels("crawl").observe(crawl_phase_elapsed)
            logger.info("phase_completed", extra={
                "job_id": job_id, "phase": "crawl",
                "duration_seconds": round(crawl_phase_elapsed, 4),
            })
            # HUB-038: the facet relevance floor admits QUERIES, not the
            # documents they return, so an entirely on-topic facet can still
            # retain off-topic sources (measured 2026-08-13: Couchbase and
            # Databricks docs in a Redis corpus, an NIH paper in a Kubernetes
            # one). Screen documents against the topic before ingestion, where
            # it saves embedding, drafting slots and metered judge calls rather
            # than only tidying the source list.
            source_screening: dict | None = None
            if planning_enabled and crawl_results:
                crawl_results, source_screening = await self._screen_sources(
                    topic, crawl_results, job_id=job_id,
                    anchor_queries=plan.queries,
                )

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
                # Lexical rows must be byte-equal to the sanitized Qdrant payload
                # text so cross-channel dedup by text stays exact.
                await asyncio.to_thread(
                    self.documents.replace_chunks, document_id,
                    [classify_and_sanitize(chunk)[0] for chunk in chunks],
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
                    "source_screening": source_screening,
                    "snippet_ranking": snippet_ranking,
                    "search_providers": dict(search_providers),
                    "query_plan": acquisition_provenance(
                        plan, issued_queries=issued_queries,
                        decisions=plan_decisions, rounds=rounds,
                        stop_reason=acquisition_stop,
                    ),
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
