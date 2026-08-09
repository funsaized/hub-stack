"""Opt-in live Ollama benchmark: sequential legacy calls versus `/api/embed`."""

import asyncio
import os
import time

import httpx


async def main() -> None:
    base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
    texts = [f"Benchmark research chunk {index}: " + "bounded batching " * 30
             for index in range(16)]
    async with httpx.AsyncClient(timeout=120) as client:
        # Exclude model load from both measurements.
        warmup = await client.post(
            f"{base_url}/api/embed", json={"model": model, "input": ["warmup"]}
        )
        warmup.raise_for_status()
        started = time.perf_counter()
        for value in texts:
            response = await client.post(
                f"{base_url}/api/embeddings", json={"model": model, "prompt": value}
            )
            response.raise_for_status()
        sequential = time.perf_counter() - started

        started = time.perf_counter()
        response = await client.post(
            f"{base_url}/api/embed", json={"model": model, "input": texts}
        )
        response.raise_for_status()
        batched = time.perf_counter() - started

    print({
        "chunks": len(texts), "sequential_seconds": round(sequential, 3),
        "batched_seconds": round(batched, 3),
        "speedup": round(sequential / batched, 2),
    })


if __name__ == "__main__":
    asyncio.run(main())
