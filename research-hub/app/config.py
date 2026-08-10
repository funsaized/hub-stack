"""Configuration loaded from environment variables."""

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
    answer_reserve_tokens: int = 1024
    allow_custom_system_prompts: bool = False
    respect_robots_txt: bool = True


def load_config() -> Config:
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
        answer_reserve_tokens=int(os.environ.get("ANSWER_RESERVE_TOKENS", "1024")),
        allow_custom_system_prompts=os.environ.get(
            "ALLOW_CUSTOM_SYSTEM_PROMPTS", "false"
        ).lower() in {"1", "true", "yes"},
        respect_robots_txt=os.environ.get("RESPECT_ROBOTS_TXT", "true").lower()
        in {"1", "true", "yes"},
    )
