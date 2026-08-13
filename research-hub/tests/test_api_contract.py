"""Deterministic API request/response contract coverage for HUB-011."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.models import QueryRequest, RAGRequest, ResearchRequest
from app.query import QueryEngine
from fakes import FakeRetrieval, candidate


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
                           "max_context_tokens": 12000},
        }
        schema_names = main.app.openapi()["components"]["schemas"]
        for name, payload in examples.items():
            with self.subTest(schema=name):
                self.assertIn(name, schema_names)
                {"ResearchRequest": ResearchRequest, "QueryRequest": QueryRequest,
                 "RAGRequest": RAGRequest}[name](**payload)

    def test_legacy_persisted_job_fields_are_projected_out(self):
        job = main.public_job({
            "job_id": "legacy", "topic": "legacy topic", "status": "completed",
            "created_at": "2026-08-08T00:00:00+00:00",
            "updated_at": "2026-08-08T01:00:00+00:00",
            "depth": 3, "max_sources": 5, "language": "en", "tags": ["old"],
        })
        self.assertEqual(job.job_id, "legacy")
        self.assertNotIn("depth", job.model_dump())


class QueryConstructionTests(unittest.IsolatedAsyncioTestCase):
    """The filters survived HUB-043; what changed is where they are applied.

    They used to be Qdrant payload conditions on a dense-only search. They are
    now a source scope, because the lexical channel has no payload to filter
    and both channels must see the same corpus.
    """

    def engine(self, retrieval, **kwargs):
        ollama = Mock(model="model", embed=AsyncMock(return_value=[1.0]),
                      generate=AsyncMock(return_value="answer"))
        return QueryEngine(ollama, Mock(), retrieval, **kwargs)

    async def test_filters_and_context_are_constructed_from_results(self):
        retrieval = FakeRetrieval([
            candidate("Evidence", "https://example.com", "Source", .9),
        ])
        response = await self.engine(retrieval).search(QueryRequest(
            query="valid question", topic_filter="topic", tags_filter=["tag"]
        ))
        self.assertEqual(retrieval.calls, [{
            "job_id": None, "query": "valid question",
            "source_topic": "topic", "source_tags": ["tag"],
        }])
        self.assertIn("[1] Source (https://example.com)\nEvidence", response.context)

    async def test_rag_passes_tag_filter_to_retrieval(self):
        retrieval = FakeRetrieval([
            candidate("Evidence", "https://example.com", "Source", .9),
        ])
        await self.engine(retrieval).rag(RAGRequest(
            query="valid question", tags_filter=["tag"]
        ))
        self.assertEqual(retrieval.calls[0]["source_tags"], ["tag"])
        self.assertIsNone(retrieval.calls[0]["source_topic"])

    async def test_query_never_scopes_itself_to_a_single_job(self):
        """HUB-043: /query searches the corpus, not one job's sources."""
        retrieval = FakeRetrieval()
        await self.engine(retrieval).search(QueryRequest(query="a question"))
        self.assertIsNone(retrieval.calls[0]["job_id"])

    async def test_top_k_bounds_what_a_wider_candidate_pool_returns(self):
        """The pool is sized for fusion quality; top_k is the caller's cut."""
        retrieval = FakeRetrieval([
            candidate(f"Evidence {index}", f"https://example.com/{index}",
                      f"Source {index}", 0.9 - index / 100)
            for index in range(10)
        ])
        response = await self.engine(retrieval).search(
            QueryRequest(query="a question", top_k=3)
        )
        self.assertEqual(len(response.chunks), 3)
        self.assertEqual(response.chunks[0].text, "Evidence 0")

    async def test_fused_rank_score_is_reported_not_a_contradicting_cosine(self):
        retrieval = FakeRetrieval([
            candidate("Evidence", "https://example.com", "Source", 0.0,
                      metadata={"rrf_score": 0.0312,
                                "retrieval_channels": ["lexical"]}),
        ])
        response = await self.engine(retrieval).search(
            QueryRequest(query="a question")
        )
        self.assertEqual(response.chunks[0].score, 0.0312)


class RetryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_failure_updates_job_projection_before_409(self):
        previous = main.orchestrator
        subject = SimpleNamespace(
            generate_report=AsyncMock(side_effect=RuntimeError("output limit")),
            _update_job=AsyncMock(),
        )
        main.orchestrator = subject
        self.addCleanup(setattr, main, "orchestrator", previous)

        with self.assertRaises(HTTPException) as raised:
            await main.retry_research_report("job-1")

        self.assertEqual(raised.exception.status_code, 409)
        subject._update_job.assert_awaited_once_with(
            "job-1", report_status="failed"
        )


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


def test_rag_default_context_budget_leaves_room_for_evidence():
    """Regression for HUB-041.

    max_context_tokens defaulted to 3000 against a 2048-token answer reserve,
    so a default /rag call had under 1000 tokens for the system prompt, the
    question and the evidence combined -- evidence never fit and every default
    call returned zero sources. The default now tracks the model context.
    """
    from app.models import RAGRequest

    request = RAGRequest(query="a question about the corpus")
    assert request.max_context_tokens is None

    # An explicit value is still honoured, including a deliberately small one.
    assert RAGRequest(query="q", max_context_tokens=900).max_context_tokens == 900
