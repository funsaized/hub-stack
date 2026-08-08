"""FastAPI app for the research hub."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .config import load_config
from .models import ResearchRequest, JobInfo, QueryRequest, QueryResponse, RAGRequest, RAGResponse
from .research import ResearchOrchestrator, ensure_embedding_model
from .query import QueryEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("research-hub")

cfg = load_config()
orchestrator: ResearchOrchestrator | None = None
query_engine: QueryEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator, query_engine
    logger.info("Starting research hub...")
    orchestrator = ResearchOrchestrator(cfg)
    await orchestrator.init()
    query_engine = QueryEngine(orchestrator.ollama, orchestrator.qdrant)
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


@app.get("/health")
async def health():
    """Liveness probe."""
    if not orchestrator:
        return {"status": "starting"}
    return {"status": "ok", "service": "research-hub"}


@app.get("/health/full")
async def health_full():
    """Detailed health check including backing services."""
    if not orchestrator:
        return {"status": "starting"}
    checks = await orchestrator.health_check()
    return {"status": "ok" if checks["all_ok"] else "degraded", "services": checks}


@app.post("/research", response_model=JobInfo)
async def submit_research(req: ResearchRequest):
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    job_id = await orchestrator.submit_job(req)
    job = await orchestrator.get_job(job_id)
    return JobInfo(**job)


@app.get("/research/{job_id}", response_model=JobInfo)
async def get_research_status(job_id: str):
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    job = await orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id} not found")
    return JobInfo(**job)


@app.get("/research", response_model=list[JobInfo])
async def list_research_jobs(limit: int = 50):
    if not orchestrator:
        raise HTTPException(503, "Orchestrator not ready")
    jobs = await orchestrator.list_jobs(limit=limit)
    return [JobInfo(**j) for j in jobs]


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    if not query_engine:
        raise HTTPException(503, "Query engine not ready")
    return await query_engine.search(req)


@app.post("/rag", response_model=RAGResponse)
async def rag(req: RAGRequest):
    if not query_engine:
        raise HTTPException(503, "Query engine not ready")
    return await query_engine.rag(req)


@app.get("/")
async def root():
    return {
        "service": "research-hub",
        "version": "0.1.0",
        "endpoints": [
            "GET /health",
            "GET /health/full (detailed checks)",
            "POST /research {topic, depth, max_sources, language, tags}",
            "GET /research/{job_id}",
            "GET /research",
            "POST /query {query, top_k, topic_filter, tags_filter}",
            "POST /rag {query, top_k, topic_filter, max_context_tokens}",
        ],
    }
