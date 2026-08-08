"""HTTP clients for external services."""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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

    async def generate(self, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if system:
            payload["system"] = system
        r = await self._client.post(f"{self.base_url}/api/generate", json=payload)
        r.raise_for_status()
        return r.json().get("response", "")

    async def embed(self, text: str) -> list[float]:
        """Embed a single text. Returns vector."""
        r = await self._client.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.embedding_model, "prompt": text},
        )
        r.raise_for_status()
        return r.json()["embedding"]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts. Calls sequentially for simplicity."""
        return [await self.embed(t) for t in texts]


class QdrantClient:
    """Wrapper around qdrant-client for our specific use."""

    def __init__(self, url: str, collection: str):
        from qdrant_client import QdrantClient as QC
        from qdrant_client.models import Distance, VectorParams

        self._client = QC(url=url, timeout=30.0)
        self.collection = collection
        self._client.recreate_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=768, distance=Distance.COSINE),
        )

    async def health(self) -> bool:
        try:
            # QdrantClient is sync; run in thread if needed
            return str(self._client.get_collections()).strip() != ""
        except Exception:
            return False

    def upsert(self, points: list[dict]) -> None:
        """points: [{id, vector, payload}, ...]"""
        from qdrant_client.models import PointStruct

        structs = [
            PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
            for p in points
        ]
        self._client.upsert(collection_name=self.collection, points=structs, wait=True)

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
                })
            return [r for r in results if r["url"]]
        except Exception as e:
            logger.warning(f"SearXNG search failed: {e}")
            return []


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

    async def crawl(self, url: str) -> dict | None:
        """Crawl a single URL. Returns {url, title, markdown, success} or None on failure."""
        try:
            r = await self._client.post(
                f"{self.base_url}/crawl",
                json={
                    "urls": [url],
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
                "markdown": res.get("markdown", ""),
                "success": True,
            }
        except Exception as e:
            logger.warning(f"Crawl4AI failed for {url}: {e}")
            return None
