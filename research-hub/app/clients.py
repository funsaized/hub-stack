"""HTTP clients for external services."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CollectionConfigurationError(RuntimeError):
    """Raised when an existing Qdrant collection needs migration."""


class OllamaClient:
    """OpenAI-compatible client for Ollama."""

    def __init__(self, base_url: str, model: str, embedding_model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embedding_model = embedding_model
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0))

    async def close(self):
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/api/tags", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    async def generate(
        self, prompt: str, system: str | None = None, max_tokens: int = 1024,
        json_mode: bool = False, json_schema: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        if json_schema is not None:
            payload["format"] = json_schema
        elif json_mode:
            payload["format"] = "json"
        r = await self._client.post(f"{self.base_url}/api/generate", json=payload)
        r.raise_for_status()
        return r.json().get("response", "")

    async def chat_stream(
        self, messages: list[dict[str, str]], *, max_tokens: int = 1024,
        temperature: float = 0.2, top_p: float = 0.9,
        stop: str | list[str] | None = None,
    ) -> AsyncIterator[str]:
        """Stream assistant content from Ollama's native chat endpoint."""
        options: dict[str, Any] = {
            "num_predict": max_tokens, "temperature": temperature, "top_p": top_p,
        }
        if stop:
            options["stop"] = [stop] if isinstance(stop, str) else stop
        async with self._client.stream(
            "POST", f"{self.base_url}/api/chat",
            json={"model": self.model, "messages": messages, "stream": True,
                  "options": options},
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    yield content
                if data.get("done"):
                    break

    async def embed(self, text: str) -> list[float]:
        """Embed a single text. Returns vector."""
        r = await self._client.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.embedding_model, "prompt": text},
        )
        r.raise_for_status()
        return r.json()["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in one Ollama /api/embed request."""
        if not texts:
            return []
        r = await self._client.post(
            f"{self.base_url}/api/embed",
            json={"model": self.embedding_model, "input": texts},
        )
        r.raise_for_status()
        embeddings = r.json().get("embeddings", [])
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"Ollama returned {len(embeddings)} embeddings for {len(texts)} inputs"
            )
        return embeddings


class QdrantClient:
    """Wrapper around qdrant-client for our specific use."""

    def __init__(
        self,
        url: str,
        collection: str,
        vector_size: int = 768,
        distance=None,
        embedding_model: str = "nomic-embed-text",
        client: Any | None = None,
    ):
        from qdrant_client import QdrantClient as QC
        from qdrant_client.models import Distance, VectorParams

        self._client = client if client is not None else QC(url=url, timeout=30.0)
        self.collection = collection
        expected_distance = distance or Distance.COSINE
        if self._client.collection_exists(collection):
            self._validate_collection(
                vector_size=vector_size,
                distance=expected_distance,
                embedding_model=embedding_model,
            )
        else:
            created = self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(
                    size=vector_size,
                    distance=expected_distance,
                ),
            )
            if not created:
                self._validate_collection(
                    vector_size=vector_size,
                    distance=expected_distance,
                    embedding_model=embedding_model,
                )

    def _validate_collection(self, vector_size: int, distance, embedding_model: str) -> None:
        info = self._client.get_collection(self.collection)
        vectors = info.config.params.vectors
        if isinstance(vectors, dict):
            names = ", ".join(sorted(vectors))
            raise CollectionConfigurationError(
                "migration required for Qdrant collection "
                f"'{self.collection}': configured embedding model "
                f"'{embedding_model}' expects one unnamed vector, but the "
                f"existing collection uses named vectors: {names}. Existing data "
                "was not modified; migrate/re-embed it or configure a different "
                "collection."
            )
        existing_size = getattr(vectors, "size", None)
        existing_distance = getattr(vectors, "distance", None)

        def distance_name(value) -> str:
            return str(getattr(value, "value", value)).lower()

        if (
            existing_size != vector_size
            or distance_name(existing_distance) != distance_name(distance)
        ):
            raise CollectionConfigurationError(
                "migration required for Qdrant collection "
                f"'{self.collection}': configured embedding model "
                f"'{embedding_model}' expects vector size {vector_size} and "
                f"distance {distance_name(distance)}, but the existing collection "
                f"uses vector size {existing_size} and distance "
                f"{distance_name(existing_distance)}. Existing data was not modified; "
                "migrate/re-embed it or configure a different collection."
            )

    def health(self) -> bool:
        """Check Qdrant using its synchronous client."""
        try:
            return str(self._client.get_collections()).strip() != ""
        except Exception as exc:
            logger.warning("Qdrant health check failed: %s", exc)
            return False

    def upsert(self, points: list[dict]) -> None:
        """points: [{id, vector, payload}, ...]"""
        from qdrant_client.models import PointStruct

        structs = [
            PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
            for p in points
        ]
        self._client.upsert(collection_name=self.collection, points=structs, wait=True)

    def document_chunks(self, canonical_url: str) -> list[dict]:
        """Return stored chunk identity metadata for one canonical source URL."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        records, offset = self._client.scroll(
            collection_name=self.collection,
            scroll_filter=Filter(must=[
                FieldCondition(key="canonical_url", match=MatchValue(value=canonical_url)),
            ]),
            limit=256,
            with_payload=True,
            with_vectors=False,
        )
        result = [{"id": str(record.id), "payload": record.payload or {}} for record in records]
        while offset is not None:
            records, offset = self._client.scroll(
                collection_name=self.collection,
                scroll_filter=Filter(must=[
                    FieldCondition(key="canonical_url", match=MatchValue(value=canonical_url)),
                ]),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            result.extend(
                {"id": str(record.id), "payload": record.payload or {}}
                for record in records
            )
        return result

    def delete_document(self, canonical_url: str, *, except_document_id: str | None = None) -> None:
        """Delete every chunk for a source, optionally retaining one document version."""
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            FilterSelector,
            MatchValue,
        )

        selector = Filter(
            must=[FieldCondition(
                key="canonical_url", match=MatchValue(value=canonical_url)
            )],
            must_not=(
                [FieldCondition(
                    key="document_id", match=MatchValue(value=except_document_id)
                )]
                if except_document_id else None
            ),
        )
        self._client.delete(
            collection_name=self.collection,
            points_selector=FilterSelector(filter=selector),
            wait=True,
        )

    def search(self, vector: list[float], top_k: int = 5, filters: dict | None = None) -> list[dict]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

        qfilter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                if isinstance(value, list):
                    conditions.append(FieldCondition(key=key, match=MatchAny(any=value)))
                else:
                    conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
            qfilter = Filter(must=conditions)

        hits = self._client.search(
            collection_name=self.collection,
            query_vector=vector,
            limit=top_k,
            query_filter=qfilter,
        )
        return [
            {
                "text": h.payload.get("text", ""),
                "source_url": h.payload.get("source_url", ""),
                "source_title": h.payload.get("source_title", ""),
                "score": h.score,
                "metadata": {k: v for k, v in h.payload.items() if k not in ("text", "source_url", "source_title")},
            }
            for h in hits
        ]


class SearXNGClient:
    """Private meta-search via SearXNG."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            r = await self._client.get(self.base_url, timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    async def search(self, query: str, max_results: int = 20, language: str = "en") -> list[dict]:
        """Returns list of {url, title, snippet}."""
        try:
            r = await self._client.get(
                f"{self.base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "language": language,
                    "categories": "general",
                    "engines": "duckduckgo,startpage,brave",
                    "count": max_results,
                },
            )
            r.raise_for_status()
            data = r.json()
            results = []
            for item in data.get("results", []):
                results.append({
                    "url": item.get("url"),
                    "title": item.get("title", ""),
                    "snippet": item.get("content", ""),
                    "published_at": item.get("publishedDate") or item.get("published_at"),
                })
            return [r for r in results if r["url"]]
        except Exception as e:
            logger.warning(f"SearXNG search failed: {e}")
            return []


def crawl_markdown_text(value: Any) -> str:
    """Normalize Crawl4AI markdown across string and structured responses."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("raw_markdown", "fit_markdown", "markdown_with_citations", "markdown"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return ""


class Crawl4AIClient:
    """Web crawler via Crawl4AI's REST API."""

    def __init__(self, base_url: str, api_token: str | None = None):
        self.base_url = base_url.rstrip("/")
        headers = {}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=10.0),
            headers=headers,
        )

    async def close(self):
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/health", timeout=5.0)
            return r.status_code == 200
        except Exception:
            return False

    async def crawl(self, url: str, *, respect_robots_txt: bool = True) -> dict | None:
        """Crawl a single URL. Returns {url, title, markdown, success} or None on failure."""
        try:
            r = await self._client.post(
                f"{self.base_url}/crawl",
                json={
                    "urls": [url],
                    "crawler_config": {"check_robots_txt": respect_robots_txt},
                    "word_count_threshold": 10,
                    "extraction_strategy": "NoExtractionStrategy",
                    "chunking_strategy": {
                        "type": "RegexChunking",
                        "patterns": [r"\n\n", r"\n", r"\.\s+"],
                    },
                },
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("results", [])
            if not results:
                return None
            res = results[0]
            if not res.get("success"):
                return None
            return {
                "url": res.get("url", url),
                "title": res.get("metadata", {}).get("title", ""),
                "markdown": crawl_markdown_text(res.get("markdown", "")),
                "http_metadata": {
                    key: value for key, value in res.get("metadata", {}).items()
                    if key in {"status_code", "content_type", "etag", "last_modified", "date", "published_time"}
                },
                "success": True,
            }
        except Exception as e:
            logger.warning(f"Crawl4AI failed for {url}: {e}")
            return None
