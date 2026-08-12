"""Redis integration coverage for the durable ingestion queue (HUB-007)."""

import json
import os
import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock

import redis.asyncio as redis_async

from app.config import Config
from app.worker import IngestionWorker, LeaseLostError


class StubOrchestrator:
    def __init__(self, backend, failure=None):
        self._redis = backend
        self.failure = failure
        self.run_job = AsyncMock(side_effect=failure)

    async def init(self):
        pass

    async def close(self):
        pass

    async def get_job(self, job_id):
        value = await self._redis.get(f"research:job:{job_id}")
        return json.loads(value) if value else None

    async def _update_job(self, job_id, **fields):
        job = await self.get_job(job_id)
        if job:
            job.update(fields)
            await self._redis.set(f"research:job:{job_id}", json.dumps(job))


class WorkerRedisIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = redis_async.from_url(
            os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"), decode_responses=True
        )
        try:
            await self.redis.ping()
        except Exception as exc:
            await self.redis.aclose()
            self.skipTest(f"Redis integration service unavailable: {exc}")
        self.ids = []

    async def asyncTearDown(self):
        for job_id in self.ids:
            await self.redis.delete(f"research:job:{job_id}", f"research:lease:{job_id}")
            await self.redis.lrem("research:jobs", 0, job_id)
            await self.redis.lrem("research:queue:pending", 0, job_id)
            await self.redis.lrem("research:queue:processing", 0, job_id)
        await self.redis.aclose()

    def worker(self, orchestrator, attempts=3):
        cfg = Config(
            redis_url="redis://localhost:6379/15", qdrant_url="", ollama_url="",
            llm_model="", embedding_model="", searxng_url="", crawl4ai_url="",
            crawl4ai_token="", log_level="info", judge_api_key="test-key",
            worker_lease_seconds=30,
            worker_heartbeat_seconds=5, job_timeout_seconds=5,
            job_max_attempts=attempts, queue_poll_seconds=1,
        )
        subject = IngestionWorker(cfg, orchestrator)
        subject.redis = self.redis
        return subject

    async def add_job(self, status="pending"):
        job_id = str(uuid.uuid4())
        self.ids.append(job_id)
        await self.redis.set(
            f"research:job:{job_id}",
            json.dumps({"job_id": job_id, "status": status, "attempts": 0}),
        )
        await self.redis.lpush("research:jobs", job_id)
        await self.redis.lpush("research:queue:pending", job_id)
        return job_id

    async def test_duplicate_queue_entries_have_single_claim(self):
        job_id = await self.add_job()
        await self.redis.lpush("research:queue:pending", job_id)
        worker = self.worker(StubOrchestrator(self.redis))

        first = await worker.claim()
        second = await worker.claim()

        self.assertEqual(first[0], job_id)
        self.assertIsNone(second)

    async def test_expired_worker_claim_is_requeued_after_restart(self):
        job_id = await self.add_job()
        first_worker = self.worker(StubOrchestrator(self.redis))
        await first_worker.claim()
        await self.redis.delete(f"research:lease:{job_id}")  # simulate process death/TTL expiry

        restarted_worker = self.worker(StubOrchestrator(self.redis))
        await restarted_worker.reconcile()

        self.assertIn(job_id, await self.redis.lrange("research:queue:pending", 0, -1))
        self.assertNotIn(job_id, await self.redis.lrange("research:queue:processing", 0, -1))
        self.assertEqual((await restarted_worker.orchestrator.get_job(job_id))["status"], "pending")

    async def test_permanent_failure_reaches_terminal_state_with_error(self):
        job_id = await self.add_job()
        orchestrator = StubOrchestrator(self.redis, RuntimeError("crawler unavailable"))
        worker = self.worker(orchestrator, attempts=1)
        claim = await worker.claim()

        await worker.process(*claim)

        job = await orchestrator.get_job(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("crawler unavailable", job["error"])
        self.assertEqual(job["attempts"], 1)
        self.assertFalse(await self.redis.exists(f"research:lease:{job_id}"))

    async def test_lease_loss_cancels_active_execution_without_marking_failed(self):
        job_id = await self.add_job()
        orchestrator = StubOrchestrator(self.redis)
        cancelled = asyncio.Event()

        async def long_running_job(_job_id):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        orchestrator.run_job = AsyncMock(side_effect=long_running_job)
        worker = self.worker(orchestrator)
        worker.heartbeat = AsyncMock(side_effect=LeaseLostError("lease expired"))
        claim = await worker.claim()

        await worker.process(*claim)

        self.assertTrue(cancelled.is_set())
        job = await orchestrator.get_job(job_id)
        self.assertNotEqual(job["status"], "failed")


if __name__ == "__main__":
    unittest.main()
