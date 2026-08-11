"""FastAPI app for the research hub."""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import load_config
from .models import (
    ChatCompletionChoice, ChatCompletionRequest, ChatCompletionResponse,
    ChatMessage, JobInfo, ModelInfo, ModelList, QueryRequest, QueryResponse,
    RAGRequest, RAGResponse, ResearchReport, ResearchRequest,
)
from .openai_compat import CORPUS_MODEL, sse_chunk
from .research import ResearchOrchestrator, canonicalize_url, ensure_embedding_model
from .query import QueryEngine
from .observability import REQUESTS, REQUEST_LATENCY, configure_logging, correlation_id

configure_logging(load_config().log_level)
logger = logging.getLogger("research-hub")

cfg = load_config()
orchestrator: ResearchOrchestrator | None = None
query_engine: QueryEngine | None = None


def public_job(job: dict) -> JobInfo:
    """Project persisted records onto the strict public contract.

    Older deployments stored request fields alongside job state. Keeping the
    response projection explicit lets those records remain inspectable without
    weakening unknown-field rejection for API callers.
    """
    return JobInfo(**{
        name: job[name] for name in JobInfo.model_fields if name in job
    })


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, query_engine
    logger.info("Starting research hub...")
    orchestrator = ResearchOrchestrator(cfg)
    await orchestrator.init()
    query_engine = QueryEngine(
        orchestrator.ollama, orchestrator.qdrant,
        model_context_tokens=cfg.model_context_tokens,
        answer_reserve_tokens=cfg.answer_reserve_tokens,
        allow_custom_system_prompts=cfg.allow_custom_system_prompts,
    )
    # Ensure embedding model is available
    try:
        await ensure_embedding_model(orchestrator.ollama, cfg.embedding_model)
    except Exception as e:
        logger.warning(f"Could not ensure embedding model: {e}")
    yield
    logger.info("Shutting down...")
    if orchestrator:
        await orchestrator.close()


app = FastAPI(
    title="Research Hub",
    description="Reusable deep research service: search, crawl, embed, store, query, RAG.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def correlate_and_measure(request: Request, call_next):
    supplied = request.headers.get("X-Request-ID", "")
    request_id = supplied if 0 < len(supplied) <= 128 else str(uuid.uuid4())
    token = correlation_id.set(request_id)
    started = time.monotonic()
    status = 500
    route = request.url.path
    try:
        response = await call_next(request)
        status = response.status_code
        route_obj = request.scope.get("route")
        route = getattr(route_obj, "path", route)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        elapsed = time.monotonic() - started
        REQUESTS.labels(request.method, route, str(status)).inc()
        REQUEST_LATENCY.labels(request.method, route).observe(elapsed)
        logger.info("http_request", extra={"duration_seconds": round(elapsed, 4)})
        correlation_id.reset(token)


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
async def health():
    """Backward-compatible alias for the process liveness probe."""
    return await livez()


@app.get("/livez")
async def livez():
    """Process-only liveness probe; does not inspect dependencies."""
    return {"status": "ok", "service": "research-hub"}


READINESS_REQUIREMENTS = {
    "all": {"ollama", "qdrant", "redis", "searxng", "crawl4ai", "claim_verifier"},
    "query": {"ollama", "qdrant"},
    "rag": {"ollama", "qdrant"},
    "research": {"ollama", "qdrant", "redis", "searxng", "crawl4ai", "claim_verifier"},
}


@app.get("/readyz")
async def readyz(
    response: Response,
    capability: str = Query(default="all", pattern="^(all|query|rag|research)$"),
):
    """Readiness for all services or for one requested API capability."""
    if not orchestrator:
        response.status_code = 503
        return {"status": "starting", "capability": capability, "services": {}}
    checks = await orchestrator.health_check()
    required = READINESS_REQUIREMENTS[capability]
    services = {name: checks[name] for name in sorted(required)}
    ready = all(services.values())
    if not ready:
        response.status_code = 503
    return {
        "status": "ok" if ready else "degraded",
        "capability": capability,
        "services": services,
    }


@app.get("/health/full")
async def health_full():
    """Detailed health check including backing services."""
    if not orchestrator:
        return {"status": "starting", "services": {}}
    checks = await orchestrator.health_check()
    return {"status": "ok" if checks["all_ok"] else "degraded", "services": checks}


@app.post("/research", response_model=JobInfo)
async def submit_research(req: ResearchRequest):
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    job_id = await orchestrator.submit_job(req)
    logger.info("job_submitted", extra={"job_id": job_id, "phase": "queued"})
    job = await orchestrator.get_job(job_id)
    return public_job(job)


@app.get("/research/{job_id}", response_model=JobInfo)
async def get_research_status(job_id: str):
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    job = await orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return public_job(job)


@app.get("/research/{job_id}/report", response_model=ResearchReport)
async def get_research_report(job_id: str):
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    if not await orchestrator.get_job(job_id):
        raise HTTPException(404, f"Job {job_id} not found")
    report = await orchestrator.get_report(job_id)
    if not report:
        raise HTTPException(404, f"Report for job {job_id} not found")
    return ResearchReport(**report)


@app.post("/research/{job_id}/report/retry", response_model=ResearchReport)
async def retry_research_report(job_id: str):
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    try:
        report = await orchestrator.generate_report(job_id)
        await orchestrator._update_job(job_id, report_status="completed")
        return ResearchReport(**report)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        await orchestrator._update_job(job_id, report_status="failed")
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        await orchestrator._update_job(job_id, report_status="failed")
        logger.exception("report_retry_failed", extra={"job_id": job_id, "phase": "synthesis"})
        raise HTTPException(502, f"Report synthesis failed: {exc}") from exc


@app.get("/research", response_model=list[JobInfo])
async def list_research_jobs(limit: int = 50):
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    jobs = await orchestrator.list_jobs(limit=limit)
    return [public_job(job) for job in jobs]


@app.delete("/documents")
async def delete_document(url: str = Query(..., min_length=1)):
    """Remove every indexed chunk belonging to one canonical source URL."""
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    canonical_url = canonicalize_url(url)
    chunks = await asyncio.to_thread(
        orchestrator.qdrant.document_chunks, canonical_url
    )
    if not chunks:
        raise HTTPException(404, f"Document not found: {canonical_url}")
    await asyncio.to_thread(
        orchestrator.qdrant.delete_document, canonical_url
    )
    documents_deleted = await asyncio.to_thread(
        orchestrator.documents.delete_url, canonical_url
    )
    return {"canonical_url": canonical_url, "chunks_deleted": len(chunks),
            "documents_deleted": documents_deleted}


@app.get("/documents/{document_id}")
async def inspect_document(document_id: str):
    """Return exact retained Markdown and extraction metadata for a chunk source."""
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    document = await asyncio.to_thread(orchestrator.documents.get, document_id)
    if not document:
        raise HTTPException(404, f"Document {document_id} not found")
    return document


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    if not query_engine:
        raise HTTPException(503, "Query engine not ready")
    return await query_engine.search(req)


@app.post("/rag", response_model=RAGResponse)
async def rag(req: RAGRequest):
    if not query_engine:
        raise HTTPException(503, "Query engine not ready")
    try:
        return await query_engine.rag(req)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@app.get("/v1/models", response_model=ModelList)
async def list_models():
    return ModelList(data=[ModelInfo(id=CORPUS_MODEL)])


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    if not query_engine:
        raise HTTPException(503, "Query engine not ready")
    if req.model != CORPUS_MODEL:
        raise HTTPException(404, f"Model {req.model!r} not found")
    try:
        prepared = await query_engine.prepare_chat(req.messages)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    async def content_stream():
        first_token_at = None
        generation_started = time.perf_counter()
        try:
            async for token in query_engine.stream_prepared_chat(
                prepared, max_tokens=req.max_tokens, temperature=req.temperature,
                top_p=req.top_p, stop=req.stop,
            ):
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                yield token
        finally:
            logger.info(
                "corpus_chat",
                extra={
                    "phase": "generation",
                    "query": prepared.query[:100],
                    "rewrite_ms": round(prepared.timings["rewrite_ms"], 1),
                    "retrieval_ms": round(prepared.timings["retrieval_ms"], 1),
                    "ttft_ms": round(
                        ((first_token_at or time.perf_counter()) - generation_started) * 1000, 1
                    ),
                    "generation_ms": round(
                        (time.perf_counter() - generation_started) * 1000, 1
                    ),
                    "sources_count": len(prepared.sources),
                },
            )

    if not req.stream:
        answer = "".join([token async for token in content_stream()])
        answer += query_engine.format_sources(prepared.sources)
        return ChatCompletionResponse(
            id=completion_id, created=created, model=CORPUS_MODEL,
            choices=[ChatCompletionChoice(
                message=ChatMessage(role="assistant", content=answer)
            )],
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )

    async def event_stream():
        yield sse_chunk(completion_id, created, {"role": "assistant"})
        async for token in content_stream():
            yield sse_chunk(completion_id, created, {"content": token})
        sources = query_engine.format_sources(prepared.sources)
        if sources:
            yield sse_chunk(completion_id, created, {"content": sources})
        yield sse_chunk(completion_id, created, {}, finish_reason="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/")
async def root():
    return {
        "service": "research-hub",
        "version": "0.1.0",
        "endpoints": [
            "GET /health",
            "GET /livez",
            "GET /readyz?capability=all|query|rag|research",
            "GET /health/full (detailed checks)",
            "POST /research {topic, depth, max_sources, language, tags}",
            "GET /research/{job_id}",
            "GET /research/{job_id}/report",
            "POST /research/{job_id}/report/retry",
            "GET /research",
            "DELETE /documents?url={source_url}",
            "GET /documents/{document_id}",
            "POST /query {query, top_k, topic_filter, tags_filter}",
            "POST /rag {query, top_k, topic_filter, tags_filter, max_context_tokens}",
            "GET /v1/models",
            "POST /v1/chat/completions",
        ],
    }
