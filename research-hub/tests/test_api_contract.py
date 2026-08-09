"""Deterministic API request/response contract coverage for HUB-011."""

import unittest
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.models import QueryRequest, RAGRequest, ResearchRequest
from app.query import QueryEngine


class RequestValidationTests(unittest.TestCase):
    def test_research_depth_and_topic_are_validated(self):
        with self.assertRaises(ValidationError):
            ResearchRequest(topic="no", depth=0)

    def test_query_filter_shape_is_retained(self):
        request = QueryRequest(query="valid question", topic_filter="topic", tags_filter=["tag"])
        self.assertEqual(request.tags_filter, ["tag"])

    def test_all_request_models_reject_unknown_fields(self):
        for model, payload in (
            (ResearchRequest, {"topic": "valid topic", "time_limit": "30s"}),
            (QueryRequest, {"query": "valid query", "typo": True}),
            (RAGRequest, {"query": "valid query", "typo": True}),
        ):
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError):
                model(**payload)

    def test_documented_request_examples_match_openapi_models(self):
        examples = {
            "ResearchRequest": {"topic": "deep learning optimizers", "depth": 5,
                                "max_sources": 10, "language": "en", "tags": ["ml"]},
            "QueryRequest": {"query": "best sparse optimizers", "top_k": 5,
                             "topic_filter": "optimizers", "tags_filter": ["ml"]},
            "RAGRequest": {"query": "compare the evidence", "top_k": 5,
                           "topic_filter": "optimizers", "tags_filter": ["ml"],
                           "max_context_tokens": 3000},
        }
        schema_names = main.app.openapi()["components"]["schemas"]
        for name, payload in examples.items():
            with self.subTest(schema=name):
                self.assertIn(name, schema_names)
                {"ResearchRequest": ResearchRequest, "QueryRequest": QueryRequest,
                 "RAGRequest": RAGRequest}[name](**payload)


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

    async def test_rag_passes_tag_filter_to_retrieval(self):
        ollama = Mock(model="model", embed=AsyncMock(return_value=[1.0]),
                      generate=AsyncMock(return_value="answer"))
        qdrant = Mock(search=Mock(return_value=[{
            "text": "Evidence", "source_url": "https://example.com",
            "source_title": "Source", "score": .9, "metadata": {},
        }]))
        await QueryEngine(ollama, qdrant).rag(RAGRequest(
            query="valid question", tags_filter=["tag"]
        ))
        qdrant.search.assert_called_once_with([1.0], 5, {"tags": ["tag"]})


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

    def test_unknown_api_field_returns_clear_422(self):
        response = self.client.post("/research", json={
            "topic": "valid topic", "unsupported": True,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"][0]["type"], "extra_forbidden")

    def test_request_id_is_returned_and_metrics_are_exposed(self):
        response = self.client.get("/livez", headers={"X-Request-ID": "contract-test"})
        self.assertEqual(response.headers["X-Request-ID"], "contract-test")
        metrics = self.client.get("/metrics")
        self.assertIn("hub_http_requests_total", metrics.text)


if __name__ == "__main__":
    unittest.main()
