"""Structured, secret-safe logs and low-overhead Prometheus metrics."""

import contextvars
import json
import logging
import time
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge, Histogram

correlation_id = contextvars.ContextVar("correlation_id", default="-")

REQUESTS = Counter("hub_http_requests_total", "HTTP requests", ["method", "route", "status"])
REQUEST_LATENCY = Histogram("hub_http_request_duration_seconds", "HTTP latency", ["method", "route"])
JOBS = Counter("hub_jobs_total", "Jobs by outcome", ["outcome", "failure_category"])
JOB_PHASE_LATENCY = Histogram("hub_job_phase_duration_seconds", "Ingestion phase latency", ["phase"])
SEARCH_RESULTS = Histogram("hub_search_results", "Search results returned", buckets=(0, 1, 5, 10, 20, 50, 100))
CRAWLS = Counter("hub_crawl_total", "Crawl attempts", ["outcome"])
CHUNKS = Histogram("hub_chunks_per_source", "Chunks produced per source", buckets=(0, 1, 5, 10, 25, 50, 100, 250))
EMBED_LATENCY = Histogram("hub_embedding_duration_seconds", "Embedding batch latency")
UPSERT_LATENCY = Histogram("hub_upsert_duration_seconds", "Qdrant upsert latency")
RETRIEVAL_SCORE = Histogram("hub_retrieval_score", "Retrieved cosine scores", buckets=(0, .25, .5, .7, .8, .9, 1))
GENERATION_LATENCY = Histogram("hub_generation_duration_seconds", "LLM generation latency")
GENERATION_TOKENS = Counter("hub_generation_tokens_total", "Generated tokens reported or estimated")
REPORT_RETRIEVAL_ITEMS = Histogram(
    "hub_report_retrieval_items", "Report retrieval and packing counts", ["kind"],
    buckets=(0, 1, 2, 5, 10, 20, 40, 100, 250, 1000),
)
REPORT_SYNTHESIS = Counter("hub_report_synthesis_total", "Report synthesis outcomes", ["outcome"])
REPORT_CLAIMS_REJECTED = Counter(
    "hub_report_claims_rejected_total", "Rejected report claims", ["reason"]
)
REPORT_GENERATION_LATENCY = Histogram(
    "hub_report_generation_duration_seconds", "Report generation latency"
)
REPORT_VERIFIER = Counter(
    "hub_report_verifier_total", "Claim verifier outcomes", ["outcome"]
)
REPORT_VERIFIER_LATENCY = Histogram(
    "hub_report_verifier_duration_seconds", "Claim verifier request latency"
)
REPORT_CORRECTION = Counter(
    "hub_report_correction_total", "Report correction outcomes", ["outcome"]
)
ACTIVE_JOBS = Gauge("hub_active_jobs", "Jobs currently executing")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", correlation_id.get()),
        }
        for key in ("job_id", "phase", "source_url", "source_domain", "duration_seconds",
                    "retry_count", "failure_category", "outcome",
                    "retrieval_candidates", "selected_chunks", "sources_available",
                    "sources_represented", "drafted_spans", "verified_claims",
                    "rejected_claims", "no_supported_findings",
                    "diagnostic"):
            value = getattr(record, key, None)
            if value is not None:
                data[key] = value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def configure_logging(level: str = "INFO"):
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())


class phase_timer:
    def __init__(self, phase: str, logger: logging.Logger, **fields):
        self.phase, self.logger, self.fields = phase, logger, fields

    def __enter__(self):
        self.started = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, traceback):
        elapsed = time.monotonic() - self.started
        JOB_PHASE_LATENCY.labels(self.phase).observe(elapsed)
        fields = {**self.fields, "phase": self.phase, "duration_seconds": round(elapsed, 4)}
        if exc:
            fields["failure_category"] = type(exc).__name__
            self.logger.error("phase_failed", extra=fields, exc_info=(exc_type, exc, traceback))
        else:
            self.logger.info("phase_completed", extra=fields)
