"""Configuration loaded from environment variables."""

import math
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    redis_url: str
    qdrant_url: str
    ollama_url: str
    llm_model: str
    embedding_model: str
    searxng_url: str
    crawl4ai_url: str
    crawl4ai_token: str
    log_level: str
    qdrant_collection: str = "research_corpus"
    embedding_dimension: int = 768
    chunk_size: int = 800
    chunk_overlap: int = 100
    worker_lease_seconds: int = 60
    worker_heartbeat_seconds: int = 15
    job_timeout_seconds: int = 1800
    job_max_attempts: int = 3
    queue_poll_seconds: int = 2
    document_store_path: str = "/app/data/documents.sqlite3"
    embedding_batch_size: int = 16
    embedding_batch_chars: int = 12000
    dependency_max_attempts: int = 3
    model_context_tokens: int = 8192
    answer_reserve_tokens: int = 2048
    allow_custom_system_prompts: bool = False
    respect_robots_txt: bool = True
    report_retrieval_candidates: int = 40
    report_max_chunks_per_source: int = 3
    report_retrieval_min_score: float | None = None
    claim_verifier_url: str = "http://claim-verifier:8001"
    claim_verifier_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not 1 <= self.report_retrieval_candidates <= 1000:
            raise ValueError("REPORT_RETRIEVAL_CANDIDATES must be between 1 and 1000")
        if not 1 <= self.report_max_chunks_per_source <= 100:
            raise ValueError("REPORT_MAX_CHUNKS_PER_SOURCE must be between 1 and 100")
        if (self.report_retrieval_min_score is not None and
                not math.isfinite(self.report_retrieval_min_score)):
            raise ValueError("REPORT_RETRIEVAL_MIN_SCORE must be finite")
        if not math.isfinite(self.claim_verifier_timeout_seconds) or self.claim_verifier_timeout_seconds <= 0:
            raise ValueError("CLAIM_VERIFIER_TIMEOUT_SECONDS must be positive and finite")


def load_config() -> Config:
    min_score = os.environ.get("REPORT_RETRIEVAL_MIN_SCORE", "").strip()
    return Config(
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        qdrant_url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        llm_model=os.environ.get("LLM_MODEL", "qwen3.5:9b"),
        embedding_model=os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
        searxng_url=os.environ.get("SEARXNG_URL", "http://localhost:8080"),
        crawl4ai_url=os.environ.get("CRAWL4AI_URL", "http://localhost:11235"),
        crawl4ai_token=os.environ.get("CRAWL4AI_TOKEN", ""),
        log_level=os.environ.get("LOG_LEVEL", "info"),
        qdrant_collection=os.environ.get("QDRANT_COLLECTION", "research_corpus"),
        embedding_dimension=int(os.environ.get("EMBEDDING_DIMENSION", "768")),
        worker_lease_seconds=int(os.environ.get("WORKER_LEASE_SECONDS", "60")),
        worker_heartbeat_seconds=int(os.environ.get("WORKER_HEARTBEAT_SECONDS", "15")),
        job_timeout_seconds=int(os.environ.get("JOB_TIMEOUT_SECONDS", "1800")),
        job_max_attempts=int(os.environ.get("JOB_MAX_ATTEMPTS", "3")),
        queue_poll_seconds=int(os.environ.get("QUEUE_POLL_SECONDS", "2")),
        document_store_path=os.environ.get("DOCUMENT_STORE_PATH", "/app/data/documents.sqlite3"),
        embedding_batch_size=int(os.environ.get("EMBEDDING_BATCH_SIZE", "16")),
        embedding_batch_chars=int(os.environ.get("EMBEDDING_BATCH_CHARS", "12000")),
        dependency_max_attempts=int(os.environ.get("DEPENDENCY_MAX_ATTEMPTS", "3")),
        model_context_tokens=int(os.environ.get("MODEL_CONTEXT_TOKENS", "8192")),
        answer_reserve_tokens=int(os.environ.get("ANSWER_RESERVE_TOKENS", "2048")),
        allow_custom_system_prompts=os.environ.get(
            "ALLOW_CUSTOM_SYSTEM_PROMPTS", "false"
        ).lower() in {"1", "true", "yes"},
        respect_robots_txt=os.environ.get("RESPECT_ROBOTS_TXT", "true").lower()
        in {"1", "true", "yes"},
        report_retrieval_candidates=int(
            os.environ.get("REPORT_RETRIEVAL_CANDIDATES", "40")
        ),
        report_max_chunks_per_source=int(
            os.environ.get("REPORT_MAX_CHUNKS_PER_SOURCE", "3")
        ),
        report_retrieval_min_score=float(min_score) if min_score else None,
        claim_verifier_url=os.environ.get(
            "CLAIM_VERIFIER_URL", "http://claim-verifier:8001"
        ),
        claim_verifier_timeout_seconds=float(
            os.environ.get("CLAIM_VERIFIER_TIMEOUT_SECONDS", "30")
        ),
    )
