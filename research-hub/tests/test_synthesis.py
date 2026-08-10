"""HUB-022 persisted synthesis coverage."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        self.store.observe_job_source(
            "job-1", "doc-1", "2026-08-09T00:00:00+00:00", {"topic": "topic"}
        )
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
        self.assertEqual(
            self.orchestrator.ollama.generate.await_args.kwargs["json_schema"]["required"],
            ["key_findings", "disagreements", "unknowns"],
        )

    async def test_relevant_evidence_after_long_prefix_is_used(self):
        sentinel = "LATE_EVIDENCE_SENTINEL: the intervention reduced errors by 37 percent."
        canonical_url = "https://example.com/late-evidence"
        self.store.save({
            "document_id": "doc-late", "canonical_url": canonical_url,
            "source_url": canonical_url, "title": "Late Evidence",
            "markdown": f"{'Irrelevant introduction. ' * 1200}\n\n{sentinel}",
            "content_hash": "late-hash",
            "fetched_at": "2026-08-09T01:00:00+00:00", "http_metadata": {},
            "extraction_version": "v1", "job_id": "job-late",
            "research_metadata": {"topic": "late evidence topic"},
            "created_at": "2026-08-09T01:00:00+00:00",
        })
        self.store.observe_job_source(
            "job-late", "doc-late", "2026-08-09T01:00:00+00:00",
            {"topic": "late evidence topic"},
        )
        self.orchestrator.get_job.return_value = {
            "job_id": "job-late", "topic": "late evidence topic",
            "status": "completed",
        }
        self.orchestrator.ollama.embed = AsyncMock(return_value=[0.1, 0.2])
        candidate = {
            "text": sentinel, "source_url": canonical_url,
            "canonical_url": canonical_url, "source_title": "Late Evidence",
            "document_id": "doc-late", "chunk_index": 42, "score": 0.99,
            "metadata": {
                "canonical_url": canonical_url, "document_id": "doc-late",
                "chunk_index": 42,
            },
        }
        self.orchestrator.qdrant = Mock(
            search=Mock(return_value=[candidate]),
            search_evidence=Mock(return_value=[candidate]),
        )

        await generate_report(self.orchestrator, "job-late")

        generation_prompt = self.orchestrator.ollama.generate.await_args.args[0]
        self.assertTrue(
            sentinel in generation_prompt,
            "retrieved late-evidence sentinel was absent from the generation prompt",
        )

    async def test_retry_does_not_invoke_ingestion_and_increments_attempts(self):
        await generate_report(self.orchestrator, "job-1")
        report = await generate_report(self.orchestrator, "job-1")
        self.assertEqual(report["attempts"], 2)
        self.assertEqual(self.orchestrator.ollama.generate.await_count, 2)

    async def test_uncited_material_claim_is_omitted_after_correction(self):
        self.orchestrator.ollama.generate.return_value = (
            '{"key_findings":["Unsupported"],"disagreements":[],"unknowns":[]}'
        )
        report = await generate_report(self.orchestrator, "job-1")
        self.assertEqual(report["status"], "completed")
        self.assertNotIn("Unsupported", report["report_markdown"])
        self.assertIn("omitted because", report["report_markdown"])
        self.assertEqual(self.orchestrator.ollama.generate.await_count, 2)
        self.assertIn(
            "previous response was rejected",
            self.orchestrator.ollama.generate.await_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
