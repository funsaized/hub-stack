"""Pydantic models for the research API."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Public API model: reject misspellings instead of silently discarding them."""

    model_config = ConfigDict(extra="forbid")


class JobStatus(str, Enum):
    PENDING = "pending"
    SEARCHING = "searching"
    CRAWLING = "crawling"
    EMBEDDING = "embedding"
    COMPLETED = "completed"
    FAILED = "failed"


class ResearchRequest(ContractModel):
    topic: str = Field(..., min_length=3, max_length=500, description="The research topic/query")
    depth: int = Field(default=10, ge=1, le=100, description="Number of URLs to crawl")
    max_sources: int = Field(default=20, ge=1, le=100, description="Max search results to consider")
    language: str = Field(default="en", description="Search language code")
    tags: list[str] = Field(default_factory=list, description="Optional tags for the job")
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    per_domain_limit: int = Field(default=2, ge=1, le=20)
    freshness_days: Optional[int] = Field(default=None, ge=1, le=36500)


class JobInfo(ContractModel):
    job_id: str
    topic: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    progress: dict = Field(default_factory=dict)
    error: Optional[str] = None
    sources_count: int = 0
    chunks_count: int = 0


class QueryRequest(ContractModel):
    query: str = Field(..., min_length=3, max_length=500)
    top_k: int = Field(default=5, ge=1, le=50)
    topic_filter: Optional[str] = None
    tags_filter: Optional[list[str]] = None


class QueryChunk(ContractModel):
    text: str
    source_url: str
    source_title: str
    score: float
    metadata: dict = Field(default_factory=dict)


class QueryResponse(ContractModel):
    query: str
    chunks: list[QueryChunk]
    context: str = Field(..., description="Concatenated chunks for RAG prompting")


class RAGRequest(ContractModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    topic_filter: Optional[str] = None
    tags_filter: Optional[list[str]] = None
    max_context_tokens: int = Field(default=3000, ge=128, le=128000)
    system_prompt: Optional[str] = None


class RAGResponse(ContractModel):
    query: str
    answer: str
    sources: list[QueryChunk]
    model: str
