"""Unit and integration-style coverage for HUB-009 and HUB-010."""

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.clients import OllamaClient, crawl_markdown_text
from app.document_store import DocumentStore
from app.rebuild import versioned_collection
from app.research import ResearchOrchestrator, embedding_batches


class CrawlResponseTests(unittest.TestCase):
    def test_structured_markdown_prefers_complete_raw_text(self):
        self.assertEqual(crawl_markdown_text({
            "raw_markdown": "complete source",
            "fit_markdown": "short source",
        }), "complete source")

    def test_unknown_markdown_shape_is_ignored(self):
        self.assertEqual(crawl_markdown_text({"raw_markdown": {"bad": True}}), "")


class RetryWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def test_keyword_arguments_are_forwarded(self):
        operation = AsyncMock(return_value="ok")
        owner = SimpleNamespace(
            cfg=SimpleNamespace(dependency_max_attempts=1)
        )
        result = await ResearchOrchestrator._retry_async(
            owner, operation, "value", flag=True
        )
        self.assertEqual(result, "ok")
        operation.assert_awaited_once_with("value", flag=True)


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

    def test_unchanged_document_remains_visible_to_every_observing_job(self):
        first_observation = self.document()
        second_observation = {
            **first_observation,
            "job_id": "job-2",
            "fetched_at": "2026-08-10T00:00:00+00:00",
            "research_metadata": {"topic": "second topic", "tags": ["b"]},
        }

        self.store.save(first_observation)
        self.store.observe_job_source(
            "job-1", "doc-1", first_observation["fetched_at"],
            first_observation["research_metadata"],
        )
        self.store.save(second_observation)
        self.store.observe_job_source(
            "job-2", "doc-1", second_observation["fetched_at"],
            second_observation["research_metadata"],
        )

        self.assertEqual(self.store.get("doc-1")["job_id"], "job-1")

        self.assertEqual(
            [doc["document_id"] for doc in self.store.documents_for_job("job-1")],
            ["doc-1"],
        )
        self.assertEqual(
            [doc["document_id"] for doc in self.store.documents_for_job("job-2")],
            ["doc-1"],
        )
        with sqlite3.connect(self.store.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM job_sources").fetchone()[0], 2)

    def test_existing_database_backfills_historical_job_observation(self):
        self.store.save(self.document())
        with sqlite3.connect(self.store.path) as db:
            db.execute("DROP TABLE IF EXISTS job_sources")

        migrated = DocumentStore(str(self.store.path))
        reopened = DocumentStore(str(self.store.path))

        with sqlite3.connect(self.store.path) as db:
            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("job_sources", tables)
            indexes = {
                row[1] for row in db.execute("PRAGMA index_list(job_sources)").fetchall()
            }
            self.assertIn("job_sources_document_idx", indexes)
            observations = db.execute("""
                SELECT document_id, observed_at, research_metadata
                FROM job_sources WHERE job_id = ?
            """, ("job-1",)).fetchone()
            observation_count = db.execute(
                "SELECT COUNT(*) FROM job_sources WHERE job_id = ?", ("job-1",)
            ).fetchone()[0]
        self.assertEqual(observations[:2], (
            "doc-1", "2026-08-09T00:00:00+00:00",
        ))
        self.assertEqual(json.loads(observations[2]), self.document()["research_metadata"])
        self.assertEqual(observation_count, 1)
        self.assertEqual(
            [doc["document_id"] for doc in migrated.documents_for_job("job-1")],
            ["doc-1"],
        )
        self.assertEqual(
            [doc["document_id"] for doc in reopened.documents_for_job("job-1")],
            ["doc-1"],
        )


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
