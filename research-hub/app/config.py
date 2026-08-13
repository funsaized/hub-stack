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
    report_retrieval_candidates: int = 120
    report_max_chunks_per_source: int = 3
    report_retrieval_min_score: float | None = None
    # Synthesis breadth. With a planned corpus of 30+ sources the drafting
    # caps, not the corpus, become the limit on how much a report can say.
    report_max_span_claims: int = 16
    report_max_pair_claims: int = 12
    report_max_findings: int = 12
    report_max_disagreements: int = 4
    report_max_corrections: int = 6
    report_hybrid_retrieval: bool = True
    report_rrf_k: int = 60
    # Adaptive query planning (HUB-024). Off by default: with the master switch
    # false the acquisition path is byte-identical to the single-query path.
    # PLAN_MAX_FACETS is a safety rail against a pathological planner, never
    # the mechanism that decides breadth -- that is PLAN_FACET_DISTINCT.
    report_query_planning: bool = False
    plan_facet_distinct: float = 0.85
    # Permissive by design pending calibration: the two-sided bar's relevance
    # half only rejects the clearly-unrelated tail today, and every candidate's
    # topic cosine is recorded so a real threshold can be measured rather than
    # guessed (the novelty metric was guessed once already).
    plan_facet_relevance: float = 0.55
    plan_max_facets: int = 12
    plan_max_rounds: int = 4
    plan_search_budget: int = 24
    plan_crawl_budget: int = 150
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
        for name in ("report_max_span_claims", "report_max_pair_claims",
                     "report_max_findings", "report_max_disagreements",
                     "report_max_corrections"):
            if not 1 <= getattr(self, name) <= 64:
                raise ValueError(f"{name.upper()} must be between 1 and 64")
        if self.crawl_max_markdown_chars < 1:
            raise ValueError("CRAWL_MAX_MARKDOWN_CHARS must be positive")
        if not 0.0 < self.plan_facet_distinct <= 1.0:
            raise ValueError("PLAN_FACET_DISTINCT must be in (0.0, 1.0]")
        if not 0.0 <= self.plan_facet_relevance < 1.0:
            raise ValueError("PLAN_FACET_RELEVANCE must be in [0.0, 1.0)")
        if self.plan_facet_relevance >= self.plan_facet_distinct:
            raise ValueError(
                "PLAN_FACET_RELEVANCE must be below PLAN_FACET_DISTINCT, "
                "otherwise no candidate can satisfy both halves of the bar"
            )
        if not 1 <= self.plan_max_facets <= 32:
            raise ValueError("PLAN_MAX_FACETS must be between 1 and 32")
        if not 1 <= self.plan_max_rounds <= 10:
            raise ValueError("PLAN_MAX_ROUNDS must be between 1 and 10")
        if not 1 <= self.plan_search_budget <= 64:
            raise ValueError("PLAN_SEARCH_BUDGET must be between 1 and 64")
        if not 1 <= self.plan_crawl_budget <= 500:
            raise ValueError("PLAN_CRAWL_BUDGET must be between 1 and 500")


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
            os.environ.get("REPORT_RETRIEVAL_CANDIDATES", "120")
        ),
        report_max_chunks_per_source=int(
            os.environ.get("REPORT_MAX_CHUNKS_PER_SOURCE", "3")
        ),
        report_retrieval_min_score=float(min_score) if min_score else None,
        report_max_span_claims=int(os.environ.get("REPORT_MAX_SPAN_CLAIMS", "16")),
        report_max_pair_claims=int(os.environ.get("REPORT_MAX_PAIR_CLAIMS", "12")),
        report_max_findings=int(os.environ.get("REPORT_MAX_FINDINGS", "12")),
        report_max_disagreements=int(
            os.environ.get("REPORT_MAX_DISAGREEMENTS", "4")
        ),
        report_max_corrections=int(os.environ.get("REPORT_MAX_CORRECTIONS", "6")),
        report_hybrid_retrieval=os.environ.get(
            "REPORT_HYBRID_RETRIEVAL", "true"
        ).lower() in {"1", "true", "yes"},
        report_rrf_k=int(os.environ.get("REPORT_RRF_K", "60")),
        report_query_planning=os.environ.get(
            "REPORT_QUERY_PLANNING", "false"
        ).lower() in {"1", "true", "yes"},
        plan_facet_distinct=float(os.environ.get("PLAN_FACET_DISTINCT", "0.85")),
        plan_facet_relevance=float(os.environ.get("PLAN_FACET_RELEVANCE", "0.55")),
        plan_max_facets=int(os.environ.get("PLAN_MAX_FACETS", "12")),
        plan_max_rounds=int(os.environ.get("PLAN_MAX_ROUNDS", "4")),
        plan_search_budget=int(os.environ.get("PLAN_SEARCH_BUDGET", "24")),
        plan_crawl_budget=int(os.environ.get("PLAN_CRAWL_BUDGET", "150")),
        judge_base_url=os.environ.get("JUDGE_BASE_URL", "https://api.minimax.io/v1"),
        judge_model=os.environ.get("JUDGE_MODEL", "MiniMax-M3"),
        judge_api_key=os.environ.get("MINIMAX_SUBSCRIPTION_KEY", ""),
        judge_timeout_seconds=float(os.environ.get("JUDGE_TIMEOUT_SECONDS", "60")),
    )
