"""Deterministic API request/response contract coverage for HUB-011."""

import unittest
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.models import QueryRequest, ResearchRequest
from app.query import QueryEngine


class RequestValidationTests(unittest.TestCase):
    def test_research_depth_and_topic_are_validated(self):
        with self.assertRaises(ValidationError):
            ResearchRequest(topic="no", depth=0)

    def test_query_filter_shape_is_retained(self):
        request = QueryRequest(query="valid question", topic_filter="topic", tags_filter=["tag"])
        self.assertEqual(request.tags_filter, ["tag"])


class QueryConstructionTests(unittest.IsolatedAsyncioTestCase):
    async def test_filters_and_context_are_constructed_from_results(self):
        ollama = Mock(embed=AsyncMock(return_value=[1.0]))
        qdrant = Mock(search=Mock(return_value=[{
            "text": "Evidence", "source_url": "https://example.com",
            "source_title": "Source", "score": .9, "metadata": {},
        }]))
        response = await QueryEngine(ollama, qdrant).search(QueryRequest(
            query="valid question", topic_filter="topic", tags_filter=["tag"]
        ))
        qdrant.search.assert_called_once_with(
            [1.0], 5, {"topic": "topic", "tags": ["tag"]}
        )
        self.assertIn("[1] Source (https://example.com)\nEvidence", response.context)


class DocumentContractTests(unittest.TestCase):
    def setUp(self):
        self.previous = main.orchestrator
        documents = Mock()
        documents.get.return_value = {
            "document_id": "doc-1", "markdown": "exact", "http_metadata": {},
        }
        main.orchestrator = Mock(documents=documents)
        self.client = TestClient(main.app)

    def tearDown(self):
        main.orchestrator = self.previous

    def test_document_inspection_example(self):
        response = self.client.get("/documents/doc-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["markdown"], "exact")


if __name__ == "__main__":
    unittest.main()
