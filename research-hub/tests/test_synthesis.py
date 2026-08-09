"""HUB-022 persisted synthesis coverage."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.document_store import DocumentStore
from app.synthesis import generate_report


class SynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DocumentStore(str(Path(self.temp.name) / "documents.sqlite3"))
        self.store.save({
            "document_id": "doc-1", "canonical_url": "https://example.com/source",
            "source_url": "https://example.com/source", "title": "Evidence",
            "markdown": "Supported retained evidence.", "content_hash": "hash",
            "fetched_at": "2026-08-09T00:00:00+00:00", "http_metadata": {},
            "extraction_version": "v1", "job_id": "job-1",
            "research_metadata": {"topic": "topic"},
            "created_at": "2026-08-09T00:00:00+00:00",
        })
        self.orchestrator = SimpleNamespace(
            documents=self.store,
            cfg=SimpleNamespace(answer_reserve_tokens=1000, model_context_tokens=8192),
            ollama=SimpleNamespace(generate=AsyncMock(return_value=(
                '{"key_findings":["Finding [S1]"],'
                '"disagreements":[],"unknowns":["More evidence is needed."]}'
            ))),
            get_job=AsyncMock(return_value={
                "job_id": "job-1", "topic": "topic", "status": "completed"
            }),
        )

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_report_is_persisted_with_stable_evidence_links(self):
        report = await generate_report(self.orchestrator, "job-1")
        reopened = DocumentStore(str(self.store.path)).get_report("job-1")
        self.assertEqual(report["status"], "completed")
        self.assertIn("Finding [S1]", reopened["report_markdown"])
        self.assertEqual(reopened["sources"][0]["document_id"], "doc-1")

    async def test_retry_does_not_invoke_ingestion_and_increments_attempts(self):
        await generate_report(self.orchestrator, "job-1")
        report = await generate_report(self.orchestrator, "job-1")
        self.assertEqual(report["attempts"], 2)
        self.assertEqual(self.orchestrator.ollama.generate.await_count, 2)

    async def test_uncited_material_claim_fails_and_is_retryable(self):
        self.orchestrator.ollama.generate.return_value = (
            '{"key_findings":["Unsupported"],"disagreements":[],"unknowns":[]}'
        )
        with self.assertRaises(ValueError):
            await generate_report(self.orchestrator, "job-1")
        self.assertEqual(self.store.get_report("job-1")["status"], "failed")


if __name__ == "__main__":
    unittest.main()
