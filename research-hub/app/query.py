"""Query layer: search the knowledge base, run RAG via Ollama."""

import time

from .clients import OllamaClient, QdrantClient
from .models import QueryRequest, QueryResponse, QueryChunk, RAGRequest, RAGResponse
from .observability import EMBED_LATENCY, GENERATION_LATENCY, GENERATION_TOKENS, RETRIEVAL_SCORE


class QueryEngine:
    """Hybrid search + RAG using Ollama for embeddings and generation."""

    def __init__(self, ollama: OllamaClient, qdrant: QdrantClient):
        self.ollama = ollama
        self.qdrant = qdrant

    async def search(self, req: QueryRequest) -> QueryResponse:
        started = time.monotonic()
        vector = await self.ollama.embed(req.query)
        EMBED_LATENCY.observe(time.monotonic() - started)
        filters: dict = {}
        if req.topic_filter:
            filters["topic"] = req.topic_filter
        if req.tags_filter:
            filters["tags"] = req.tags_filter

        hits = await _run_sync(self.qdrant.search, vector, req.top_k, filters if filters else None)
        chunks = [QueryChunk(**h) for h in hits]
        for chunk in chunks:
            RETRIEVAL_SCORE.observe(chunk.score)
        context = "\n\n---\n\n".join(
            f"[{i+1}] {c.source_title} ({c.source_url})\n{c.text}" for i, c in enumerate(chunks)
        )
        return QueryResponse(query=req.query, chunks=chunks, context=context)

    async def rag(self, req: RAGRequest) -> RAGResponse:
        # Get relevant chunks
        query_req = QueryRequest(
            query=req.query,
            top_k=req.top_k,
            topic_filter=req.topic_filter,
            tags_filter=req.tags_filter,
        )
        sr = await self.search(query_req)

        if not sr.chunks:
            return RAGResponse(
                query=req.query,
                answer="No relevant information found in the knowledge base.",
                sources=[],
                model=self.ollama.model,
            )

        # Build the prompt
        system = req.system_prompt or (
            "You are a research assistant. Answer the user's question using ONLY the provided context. "
            "Cite sources inline using [1], [2], etc. If the context is insufficient, say so."
        )
        context = sr.context[: req.max_context_tokens * 4]  # rough char-to-token cutoff
        prompt = f"Context:\n{context}\n\nQuestion: {req.query}\n\nAnswer:"

        started = time.monotonic()
        answer = await self.ollama.generate(prompt, system=system, max_tokens=1024)
        GENERATION_LATENCY.observe(time.monotonic() - started)
        GENERATION_TOKENS.inc(max(1, len(answer) // 4))
        return RAGResponse(
            query=req.query,
            answer=answer,
            sources=sr.chunks,
            model=self.ollama.model,
        )


async def _run_sync(func, *args, **kwargs):
    """Run a sync function in a thread (for qdrant client)."""
    import asyncio
    return await asyncio.to_thread(func, *args, **kwargs)
