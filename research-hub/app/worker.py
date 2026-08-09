"""Durable Redis-backed ingestion worker."""

import asyncio
import json
import logging
import signal
import time
import uuid
from prometheus_client import start_http_server

import redis.asyncio as redis_async

from .config import Config, load_config
from .models import JobStatus
from .research import ResearchOrchestrator
from .observability import ACTIVE_JOBS, JOBS, configure_logging, correlation_id

logger = logging.getLogger(__name__)


class LeaseLostError(RuntimeError):
    """Raised when another worker may now own the job."""


class IngestionWorker:
    """Claims one job at a time and keeps ownership alive with a Redis lease."""

    pending_key = "research:queue:pending"
    processing_key = "research:queue:processing"
    job_index_key = "research:jobs"

    def __init__(self, cfg: Config, orchestrator: ResearchOrchestrator | None = None):
        self.cfg = cfg
        self.orchestrator = orchestrator or ResearchOrchestrator(cfg)
        self.redis: redis_async.Redis | None = None
        self.worker_id = str(uuid.uuid4())
        self.stopping = asyncio.Event()
        self.current_job: str | None = None
        self.current_token: str | None = None

    @staticmethod
    def job_key(job_id: str) -> str:
        return f"research:job:{job_id}"

    @staticmethod
    def lease_key(job_id: str) -> str:
        return f"research:lease:{job_id}"

    async def init(self):
        await self.orchestrator.init()
        self.redis = self.orchestrator._redis
        await self.reconcile()

    async def close(self):
        if self.current_job and self.current_token:
            await self.release(self.current_job, self.current_token, "Worker stopped; job requeued")
        await self.orchestrator.close()

    async def reconcile(self):
        """Return expired claims and orphaned pending jobs to the durable queue."""
        assert self.redis
        for job_id in set(await self.redis.lrange(self.processing_key, 0, -1)):
            if not await self.redis.exists(self.lease_key(job_id)):
                await self.redis.lrem(self.processing_key, 0, job_id)
                job = await self.orchestrator.get_job(job_id)
                if job and job.get("status") not in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
                    await self.orchestrator._update_job(
                        job_id, status=JobStatus.PENDING.value,
                        progress={"phase": "requeued", "reason": "expired worker lease"},
                    )
                    await self.redis.lpush(self.pending_key, job_id)

        queued = set(await self.redis.lrange(self.pending_key, 0, -1))
        processing = set(await self.redis.lrange(self.processing_key, 0, -1))
        for job_id in await self.redis.lrange(self.job_index_key, 0, -1):
            job = await self.orchestrator.get_job(job_id)
            if job and job.get("status") == JobStatus.PENDING.value and job_id not in queued | processing:
                await self.redis.lpush(self.pending_key, job_id)
                queued.add(job_id)

    async def claim(self) -> tuple[str, str] | None:
        assert self.redis
        job_id = await self.redis.brpoplpush(
            self.pending_key, self.processing_key, timeout=self.cfg.queue_poll_seconds
        )
        if not job_id:
            return None
        token = f"{self.worker_id}:{uuid.uuid4()}"
        claimed = await self.redis.set(
            self.lease_key(job_id), token, ex=self.cfg.worker_lease_seconds, nx=True
        )
        if not claimed:
            # A duplicate queue entry must never result in duplicate execution.
            await self.redis.lrem(self.processing_key, 1, job_id)
            return None
        return job_id, token

    async def heartbeat(self, job_id: str, token: str):
        assert self.redis
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        while True:
            await asyncio.sleep(self.cfg.worker_heartbeat_seconds)
            if not await self.redis.eval(
                script, 1, self.lease_key(job_id), token, self.cfg.worker_lease_seconds
            ):
                raise LeaseLostError(f"Lost lease for job {job_id}")

    async def finish(self, job_id: str, token: str):
        assert self.redis
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          redis.call('del', KEYS[1])
          return redis.call('lrem', KEYS[2], 0, ARGV[2])
        end
        return 0
        """
        await self.redis.eval(script, 2, self.lease_key(job_id), self.processing_key, token, job_id)

    async def release(self, job_id: str, token: str, reason: str):
        assert self.redis
        # Ownership check and list movement are atomic, preventing two workers requeueing it.
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
          redis.call('del', KEYS[1])
          redis.call('lrem', KEYS[2], 0, ARGV[2])
          redis.call('lpush', KEYS[3], ARGV[2])
          return 1
        end
        return 0
        """
        if await self.redis.eval(
            script, 3, self.lease_key(job_id), self.processing_key, self.pending_key, token, job_id
        ):
            await self.orchestrator._update_job(
                job_id, status=JobStatus.PENDING.value,
                progress={"phase": "requeued", "reason": reason},
            )

    async def process(self, job_id: str, token: str):
        context_token = correlation_id.set(job_id)
        job = await self.orchestrator.get_job(job_id)
        if not job or job.get("status") in {JobStatus.COMPLETED.value, JobStatus.FAILED.value}:
            await self.finish(job_id, token)
            correlation_id.reset(context_token)
            return
        attempts = int(job.get("attempts", 0)) + 1
        await self.orchestrator._update_job(job_id, attempts=attempts, error=None)
        heartbeat = asyncio.create_task(self.heartbeat(job_id, token))
        ACTIVE_JOBS.inc()
        logger.info("job_started", extra={"job_id": job_id, "phase": "worker", "retry_count": attempts - 1})
        execution = asyncio.create_task(asyncio.wait_for(
            self.orchestrator.run_job(job_id), timeout=self.cfg.job_timeout_seconds
        ))
        try:
            done, _ = await asyncio.wait(
                {execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                # Stop immediately: after lease loss any further external writes could
                # overlap with the replacement worker's retry.
                await heartbeat
            await execution
            await self.finish(job_id, token)
            JOBS.labels("completed", "none").inc()
        except LeaseLostError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            logger.exception("Aborting job after lease loss")
        except asyncio.CancelledError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            await self.release(job_id, token, "Worker shutdown")
            raise
        except Exception as exc:
            category = type(exc).__name__
            message = f"Attempt {attempts}/{self.cfg.job_max_attempts} failed: {exc}"
            if attempts >= self.cfg.job_max_attempts:
                await self.orchestrator._update_job(
                    job_id, status=JobStatus.FAILED.value,
                    error=message, progress={"phase": "failed", "attempts": attempts},
                )
                await self.finish(job_id, token)
                JOBS.labels("failed", category).inc()
            else:
                await self.release(job_id, token, message)
                await self.orchestrator._update_job(job_id, error=message)
        finally:
            ACTIVE_JOBS.dec()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            correlation_id.reset(context_token)

    async def run(self):
        await self.init()
        last_reconcile = time.monotonic()
        try:
            while not self.stopping.is_set():
                if time.monotonic() - last_reconcile >= self.cfg.worker_lease_seconds:
                    await self.reconcile()
                    last_reconcile = time.monotonic()
                claim = await self.claim()
                if not claim:
                    continue
                self.current_job, self.current_token = claim
                await self.process(*claim)
                self.current_job = self.current_token = None
        finally:
            await self.close()

    def stop(self):
        self.stopping.set()


async def main():
    cfg = load_config()
    configure_logging(cfg.log_level)
    start_http_server(9000)
    worker = IngestionWorker(cfg)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
