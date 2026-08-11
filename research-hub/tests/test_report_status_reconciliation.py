"""Crash-window reconciliation of the Redis report-status projection (HUB-031).

SQLite is authoritative for persisted reports; the Redis job record only
projects `report_status` in a separate later write. These tests persist a
report, then drop or contradict the projection (the state a crash between the
two writes leaves behind) and assert every job read reports the persisted
status.
"""

import os
import tempfile
import unittest

import redis.asyncio as redis_async

from app.document_store import DocumentStore
from app.main import public_job
from app.models import ResearchRequest
from app.research import ResearchOrchestrator, utcnow


class ReportStatusReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = redis_async.from_url(
            os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"),
            decode_responses=True,
        )
        try:
            await self.redis.ping()
        except Exception as exc:
            await self.redis.aclose()
            self.skipTest(f"Redis integration service unavailable: {exc}")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # The paths under test need only the Redis handle and the document
        # store; the search, crawl and vector clients connect eagerly and
        # stay unconstructed.
        self.orchestrator = ResearchOrchestrator.__new__(ResearchOrchestrator)
        self.orchestrator.documents = DocumentStore(
            os.path.join(self.tmp.name, "documents.sqlite3")
        )
        self.orchestrator._redis = self.redis
        self.ids = []

    async def asyncTearDown(self):
        for job_id in self.ids:
            await self.redis.delete(f"research:job:{job_id}")
            await self.redis.lrem("research:jobs", 0, job_id)
            await self.redis.lrem("research:queue:pending", 0, job_id)
        await self.redis.aclose()

    async def submit(self) -> str:
        job_id = await self.orchestrator.submit_job(
            ResearchRequest(topic="reconciliation coverage")
        )
        self.ids.append(job_id)
        return job_id

    def persist_report(self, job_id: str, status: str):
        now = utcnow()
        self.orchestrator.documents.save_report({
            "job_id": job_id, "status": status, "topic": "reconciliation coverage",
            "report_markdown": "# Report", "sources": [],
            "error": "synthesis failed" if status == "failed" else None,
            "attempts": 1, "created_at": now, "updated_at": now,
        })

    async def test_completed_report_wins_after_lost_projection_write(self):
        job_id = await self.submit()
        self.persist_report(job_id, "completed")
        # Crash window: the process died before _update_job(report_status=...)

        job = await self.orchestrator.get_job(job_id)

        self.assertEqual(job["report_status"], "completed")
        self.assertEqual(public_job(job).report_status, "completed")

    async def test_failed_report_wins_after_lost_projection_write(self):
        job_id = await self.submit()
        self.persist_report(job_id, "failed")

        job = await self.orchestrator.get_job(job_id)

        self.assertEqual(job["report_status"], "failed")
        self.assertEqual(public_job(job).report_status, "failed")

    async def test_persisted_status_wins_over_contradicting_projection(self):
        job_id = await self.submit()
        await self.orchestrator._update_job(job_id, report_status="failed")
        # A retry persisted `completed` but died before refreshing the projection.
        self.persist_report(job_id, "completed")

        job = await self.orchestrator.get_job(job_id)

        self.assertEqual(job["report_status"], "completed")
        self.assertEqual(public_job(job).report_status, "completed")

    async def test_job_without_persisted_report_is_unchanged(self):
        job_id = await self.submit()

        job = await self.orchestrator.get_job(job_id)

        self.assertNotIn("report_status", job)
        self.assertIsNone(public_job(job).report_status)


if __name__ == "__main__":
    unittest.main()
