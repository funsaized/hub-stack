"""Regression tests for HUB-005 health and readiness behavior."""

import unittest
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from app import main
from app.research import ResearchOrchestrator


class HealthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.previous = main.orchestrator
        self.orchestrator = Mock()
        self.orchestrator.health_check = AsyncMock(return_value={
            "ollama": True, "qdrant": True, "redis": True,
            "searxng": False, "crawl4ai": False, "all_ok": False,
        })
        main.orchestrator = self.orchestrator
        self.client = TestClient(main.app)

    def tearDown(self):
        main.orchestrator = self.previous

    def test_livez_is_independent_of_dependencies(self):
        response = self.client.get("/livez")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.orchestrator.health_check.assert_not_awaited()

    def test_all_readiness_is_degraded_and_json_serializable(self):
        response = self.client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertFalse(response.json()["services"]["searxng"])

    def test_query_remains_ready_without_search_or_crawl(self):
        response = self.client.get("/readyz?capability=query")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["services"], {"ollama": True, "qdrant": True})

    def test_full_health_remains_diagnostic(self):
        response = self.client.get("/health/full")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "degraded")


class OrchestratorHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_qdrant_check_and_dependency_failures_are_booleans(self):
        subject = object.__new__(ResearchOrchestrator)
        subject.ollama = Mock(health=AsyncMock(return_value=True))
        subject.qdrant = Mock(health=Mock(return_value=True))
        subject.searxng = Mock(health=AsyncMock(side_effect=RuntimeError("down")))
        subject.crawl4ai = Mock(health=AsyncMock(return_value=True))
        subject._redis = Mock(ping=AsyncMock(return_value=True))

        result = await subject.health_check()

        self.assertTrue(result["qdrant"])
        self.assertFalse(result["searxng"])
        self.assertFalse(result["all_ok"])
        self.assertTrue(all(isinstance(value, bool) for value in result.values()))


if __name__ == "__main__":
    unittest.main()
