"""Unit and integration-style coverage for HUB-009 and HUB-010."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from app.clients import OllamaClient
from app.document_store import DocumentStore
from app.rebuild import versioned_collection
from app.research import embedding_batches


class DocumentStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DocumentStore(str(Path(self.temp.name) / "documents.sqlite3"))

    def tearDown(self):
        self.temp.cleanup()

    def document(self):
        return {
            "document_id": "doc-1", "canonical_url": "https://example.com/a",
            "source_url": "https://example.com/a?ref=search", "title": "Exact source",
            "markdown": "# Retained\n\nExact cleaned text.", "content_hash": "abc",
            "fetched_at": "2026-08-09T00:00:00+00:00",
            "http_metadata": {"status_code": 200}, "extraction_version": "v1",
            "job_id": "job-1", "research_metadata": {"topic": "test", "tags": ["a"]},
            "created_at": "2026-08-09T00:00:00+00:00",
        }

    def test_exact_markdown_metadata_and_checkpoint_survive_reopen(self):
        self.store.save(self.document())
        self.store.set_checkpoint("index-v1", "doc-1", "chunks-v1", 2, 4, "now")
        reopened = DocumentStore(str(self.store.path))
        self.assertEqual(reopened.get("doc-1")["markdown"], self.document()["markdown"])
        self.assertEqual(reopened.get("doc-1")["http_metadata"]["status_code"], 200)
        self.assertEqual(reopened.checkpoint("index-v1", "doc-1", "chunks-v1"), 2)

    def test_delete_removes_source_and_cascades_checkpoints(self):
        self.store.save(self.document())
        self.store.set_checkpoint("index-v1", "doc-1", "chunks-v1", 1, 1, "now")
        self.assertEqual(self.store.delete_url("https://example.com/a"), 1)
        self.assertIsNone(self.store.get("doc-1"))
        self.assertEqual(self.store.checkpoint("index-v1", "doc-1", "chunks-v1"), 0)


class EmbeddingBatchTests(unittest.IsolatedAsyncioTestCase):
    def test_batches_obey_size_character_budget_and_resume_offset(self):
        chunks = ["aaaa", "bbbb", "cc", "ddd"]
        self.assertEqual(
            list(embedding_batches(chunks, 1, max_size=2, max_chars=6)),
            [(1, ["bbbb", "cc"]), (3, ["ddd"])],
        )

    async def test_ollama_uses_single_batched_embed_contract(self):
        client = object.__new__(OllamaClient)
        client.base_url = "http://ollama"
        client.embedding_model = "model"
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"embeddings": [[1.0], [2.0]]}
        client._client = Mock(post=AsyncMock(return_value=response))
        result = await client.embed_batch(["one", "two"])
        self.assertEqual(result, [[1.0], [2.0]])
        client._client.post.assert_awaited_once_with(
            "http://ollama/api/embed", json={"model": "model", "input": ["one", "two"]}
        )

    def test_reembedding_collection_is_model_versioned(self):
        self.assertEqual(
            versioned_collection("research_corpus", "nomic-embed-text:latest", 768),
            "research_corpus__nomic_embed_text_latest_768",
        )


if __name__ == "__main__":
    unittest.main()
