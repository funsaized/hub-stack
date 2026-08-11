"""Rebuild a versioned Qdrant index entirely from retained source documents."""

import argparse
import asyncio
import logging
import re

from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from .clients import OllamaClient, QdrantClient
from .config import load_config
from .context import classify_and_sanitize
from .document_store import DocumentStore
from .research import CHUNKER_VERSION, chunk_identity, chunk_text, embedding_batches, utcnow


def rebuild_lexical_index() -> dict:
    """Rebuild the FTS5 lexical index from retained documents alone.

    No embedding or Qdrant access: chunk texts derive deterministically from
    stored markdown, and rows must be byte-equal to the sanitized Qdrant
    payload text, so this is safe to run against a live corpus at any time.
    """
    cfg = load_config()
    store = DocumentStore(cfg.document_store_path)
    documents = chunks_written = 0
    for document in store.iter_documents():
        chunks = chunk_text(document["markdown"], cfg.chunk_size, cfg.chunk_overlap)
        store.replace_chunks(
            document["document_id"],
            [classify_and_sanitize(chunk)[0] for chunk in chunks],
        )
        documents += 1
        chunks_written += len(chunks)
        logging.info("lexical indexed document=%s chunks=%s",
                     document["document_id"], len(chunks))
    return {"documents": documents, "chunks_written": chunks_written}


def versioned_collection(base: str, model: str, dimension: int) -> str:
    model_slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    return f"{base}__{model_slug}_{dimension}"


async def rebuild(collection: str | None = None) -> dict:
    cfg = load_config()
    target = collection or versioned_collection(
        cfg.qdrant_collection, cfg.embedding_model, cfg.embedding_dimension
    )
    store = DocumentStore(cfg.document_store_path)
    ollama = OllamaClient(cfg.ollama_url, cfg.llm_model, cfg.embedding_model)
    qdrant = QdrantClient(
        cfg.qdrant_url, target, cfg.embedding_dimension,
        embedding_model=cfg.embedding_model,
    )
    documents = batches = chunks_written = 0
    try:
        for document in store.iter_documents():
            chunks = chunk_text(document["markdown"], cfg.chunk_size, cfg.chunk_overlap)
            store.replace_chunks(
                document["document_id"],
                [classify_and_sanitize(chunk)[0] for chunk in chunks],
            )
            existing = await asyncio.to_thread(
                qdrant.document_chunks, document["canonical_url"]
            )
            current_ids = {
                item["id"] for item in existing
                if item["payload"].get("document_id") == document["document_id"]
            }
            completed = 0
            while (completed < len(chunks) and
                   chunk_identity(document["document_id"], completed) in current_ids):
                completed += 1
            for start, batch in embedding_batches(
                chunks, completed, cfg.embedding_batch_size, cfg.embedding_batch_chars
            ):
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(cfg.dependency_max_attempts),
                    wait=wait_exponential(multiplier=.25, max=4), reraise=True,
                ):
                    with attempt:
                        vectors = await ollama.embed_batch(batch)
                metadata = document.get("research_metadata", {})
                points = [{
                    "id": chunk_identity(document["document_id"], start + offset),
                    "vector": vector,
                    "payload": {
                        "text": text, "source_url": document["canonical_url"],
                        "source_title": document["title"],
                        "canonical_url": document["canonical_url"],
                        "content_hash": document["content_hash"],
                        "document_id": document["document_id"],
                        "chunk_index": start + offset,
                        "chunker_version": CHUNKER_VERSION,
                        "topic": metadata.get("topic", ""),
                        "tags": metadata.get("tags", []),
                        "job_id": document["job_id"], "ingested_at": utcnow(),
                    },
                } for offset, (text, vector) in enumerate(zip(batch, vectors))]
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(cfg.dependency_max_attempts),
                    wait=wait_exponential(multiplier=.25, max=4), reraise=True,
                ):
                    with attempt:
                        await asyncio.to_thread(qdrant.upsert, points)
                completed = start + len(batch)
                store.set_checkpoint(
                    target, document["document_id"], CHUNKER_VERSION,
                    completed, len(chunks), utcnow(),
                )
                batches += 1
                chunks_written += len(batch)
                logging.info("indexed document=%s chunks=%s/%s batch=%s",
                             document["document_id"], completed, len(chunks), len(batch))
            documents += 1
    finally:
        await ollama.close()
    return {"collection": target, "documents": documents,
            "batches": batches, "chunks_written": chunks_written}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", help="Explicit destination collection")
    parser.add_argument(
        "--lexical-only", action="store_true",
        help="Rebuild only the FTS5 lexical index; no embedding or Qdrant access",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.lexical_only:
        print(rebuild_lexical_index())
        return
    print(asyncio.run(rebuild(args.collection)))


if __name__ == "__main__":
    main()
