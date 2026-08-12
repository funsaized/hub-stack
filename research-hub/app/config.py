"""Configuration loaded from environment variables."""

import math
import os
from dataclasses import dataclass, field


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
    crawl_max_markdown_chars: int = 2_000_000
    report_retrieval_candidates: int = 40
    report_max_chunks_per_source: int = 3
    report_retrieval_min_score: float | None = None
    report_hybrid_retrieval: bool = True
    report_rrf_k: int = 60
    # The claim gate is the MiniMax M3 judge (HUB-034; sealed v4 final passed).
    judge_base_url: str = "https://api.minimax.io/v1"
    judge_model: str = "MiniMax-M3"
    # Subscription Key — excluded from repr so the secret can never reach logs.
    judge_api_key: str = field(default="", repr=False)
    judge_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not 1 <= self.report_rrf_k <= 1000:
            raise ValueError("REPORT_RRF_K must be between 1 and 1000")
        if not 1 <= self.report_retrieval_candidates <= 1000:
            raise ValueError("REPORT_RETRIEVAL_CANDIDATES must be between 1 and 1000")
        if not 1 <= self.report_max_chunks_per_source <= 100:
            raise ValueError("REPORT_MAX_CHUNKS_PER_SOURCE must be between 1 and 100")
        if (self.report_retrieval_min_score is not None and
                not math.isfinite(self.report_retrieval_min_score)):
            raise ValueError("REPORT_RETRIEVAL_MIN_SCORE must be finite")
        if not self.judge_api_key:
            raise ValueError("MINIMAX_SUBSCRIPTION_KEY is required (judge claim gate)")
        if not math.isfinite(self.judge_timeout_seconds) or self.judge_timeout_seconds <= 0:
            raise ValueError("JUDGE_TIMEOUT_SECONDS must be positive and finite")
        if self.crawl_max_markdown_chars < 1:
            raise ValueError("CRAWL_MAX_MARKDOWN_CHARS must be positive")


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
        crawl_max_markdown_chars=int(
            os.environ.get("CRAWL_MAX_MARKDOWN_CHARS", "2000000")
        ),
        report_retrieval_candidates=int(
            os.environ.get("REPORT_RETRIEVAL_CANDIDATES", "40")
        ),
        report_max_chunks_per_source=int(
            os.environ.get("REPORT_MAX_CHUNKS_PER_SOURCE", "3")
        ),
        report_retrieval_min_score=float(min_score) if min_score else None,
        report_hybrid_retrieval=os.environ.get(
            "REPORT_HYBRID_RETRIEVAL", "true"
        ).lower() in {"1", "true", "yes"},
        report_rrf_k=int(os.environ.get("REPORT_RRF_K", "60")),
        judge_base_url=os.environ.get("JUDGE_BASE_URL", "https://api.minimax.io/v1"),
        judge_model=os.environ.get("JUDGE_MODEL", "MiniMax-M3"),
        judge_api_key=os.environ.get("MINIMAX_SUBSCRIPTION_KEY", ""),
        judge_timeout_seconds=float(os.environ.get("JUDGE_TIMEOUT_SECONDS", "60")),
    )
