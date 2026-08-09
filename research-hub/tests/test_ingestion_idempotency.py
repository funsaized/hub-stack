"""Repeat-ingestion regression tests for HUB-008."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from qdrant_client import QdrantClient as RawQdrantClient

from app.clients import QdrantClient
from app import main
from app.research import (
    canonicalize_url,
    chunk_identity,
    document_identity,
    document_is_complete,
    ResearchOrchestrator,
)


class IngestionIdentityTests(unittest.TestCase):
    def test_url_and_ids_are_stable(self):
        first = "HTTPS://Example.COM:443/a/../article/?b=2&utm_source=x&a=1#part"
        second = "https://example.com/article?a=1&b=2"
        self.assertEqual(canonicalize_url(first), second)
        self.assertEqual(document_identity(first, "same"), document_identity(second, "same"))
        document_id = document_identity(first, "same")[2]
        self.assertEqual(chunk_identity(document_id, 0), chunk_identity(document_id, 0))
        self.assertNotEqual(chunk_identity(document_id, 0), chunk_identity(document_id, 1))

    def test_only_an_exact_chunk_set_is_an_unchanged_document(self):
        document_id = document_identity("https://example.com/page", "same")[2]
        complete = [{
            "id": chunk_identity(document_id, index),
            "payload": {"document_id": document_id},
        } for index in range(2)]
        self.assertTrue(document_is_complete(complete, document_id, 2))
        self.assertFalse(document_is_complete(complete[:1], document_id, 2))
        self.assertFalse(document_is_complete(
            complete + [{"id": "old", "payload": {"document_id": "old"}}],
            document_id,
            2,
        ))


class QdrantDeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.backend = RawQdrantClient(":memory:")
        self.client = QdrantClient(
            "http://unused", "corpus", vector_size=2, client=self.backend
        )

    def tearDown(self):
        self.backend.close()

    def points(self, url, content, count):
        canonical_url, content_hash, document_id = document_identity(url, content)
        return canonical_url, document_id, [{
            "id": chunk_identity(document_id, index),
            "vector": [float(index), 1.0],
            "payload": {
                "canonical_url": canonical_url,
                "document_id": document_id,
                "content_hash": content_hash,
                "chunk_index": index,
                "text": f"chunk {index}",
            },
        } for index in range(count)]

    def test_repeat_upsert_does_not_increase_count(self):
        url, _, points = self.points("https://example.com/page", "v1", 2)
        self.client.upsert(points)
        self.client.upsert(points)
        self.assertEqual(len(self.client.document_chunks(url)), 2)

    def test_changed_document_replaces_stale_chunks(self):
        url, _, old = self.points("https://example.com/page", "v1", 3)
        self.client.upsert(old)
        _, current_id, current = self.points(url, "v2", 1)
        self.client.upsert(current)
        self.client.delete_document(url, except_document_id=current_id)
        records = self.client.document_chunks(url)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["payload"]["document_id"], current_id)

    def test_delete_document_removes_all_chunks(self):
        url, _, points = self.points("https://example.com/page", "v1", 2)
        self.client.upsert(points)
        self.client.delete_document(url)
        self.assertEqual(self.client.document_chunks(url), [])


class FailedEmbeddingSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_embedding_failure_does_not_replace_prior_version(self):
        subject = object.__new__(ResearchOrchestrator)
        subject.cfg = SimpleNamespace(chunk_size=8, chunk_overlap=0)
        subject.get_job = AsyncMock(return_value={
            "topic": "topic", "depth": 1, "max_sources": 1,
            "language": "en", "tags": [],
        })
        subject._update_job = AsyncMock()
        subject.searxng = Mock(search=AsyncMock(return_value=[{
            "url": "https://example.com/page",
        }]))
        subject.crawl4ai = Mock(crawl=AsyncMock(return_value={
            "url": "https://example.com/page",
            "title": "Page",
            "markdown": "first.\n\nsecond.",
        }))
        subject.ollama = Mock(embed=AsyncMock(side_effect=[[1.0, 0.0], RuntimeError("embed down")]))
        subject.qdrant = Mock()
        subject.qdrant.document_chunks.return_value = [{
            "id": "prior", "payload": {"document_id": "prior-version"},
        }]

        with self.assertRaisesRegex(RuntimeError, "embed down"):
            await subject.run_job("job-id")

        subject.qdrant.upsert.assert_not_called()
        subject.qdrant.delete_document.assert_not_called()


class DocumentDeletionEndpointTests(unittest.TestCase):
    def setUp(self):
        self.previous = main.orchestrator
        qdrant = Mock()
        qdrant.document_chunks.return_value = [
            {"id": "one", "payload": {}}, {"id": "two", "payload": {}}
        ]
        self.qdrant = qdrant
        main.orchestrator = Mock(qdrant=qdrant)
        self.client = TestClient(main.app)

    def tearDown(self):
        main.orchestrator = self.previous

    def test_delete_canonicalizes_url_and_reports_removed_chunks(self):
        response = self.client.delete(
            "/documents", params={"url": "HTTPS://Example.com:443/page/?utm_source=x"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "canonical_url": "https://example.com/page",
            "chunks_deleted": 2,
        })
        self.qdrant.delete_document.assert_called_once_with("https://example.com/page")

    def test_delete_missing_document_is_404(self):
        self.qdrant.document_chunks.return_value = []
        response = self.client.delete(
            "/documents", params={"url": "https://example.com/missing"}
        )
        self.assertEqual(response.status_code, 404)
        self.qdrant.delete_document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
