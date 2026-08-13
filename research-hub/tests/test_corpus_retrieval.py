"""HUB-043: hybrid retrieval works corpus-wide, not only inside one job.

The job id is a filter, not a mode. The same fusion, per-source caps and
needle channel must run either way — the whole point is that a corpus-wide
query gets the retrieval quality that previously existed only during a
report.

Fully offline: SQLite in a temp directory, a stub embedder and a stub Qdrant.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from app.document_store import DocumentStore
from app.retrieval import ScopedRetrievalService


def run(coro):
    return asyncio.run(coro)


class StubOllama:
    async def embed(self, _text):
        return [1.0, 0.0, 0.0, 0.0]


class StubQdrant:
    """Records the filters it was given, so scoping is asserted not assumed."""

    def __init__(self, hits):
        self._hits = hits
        self.calls = []

    def search_evidence(self, _vector, limit, *, canonical_urls=None,
                        document_ids=None):
        self.calls.append({"canonical_urls": canonical_urls,
                           "document_ids": document_ids, "limit": limit})
        allowed = set(document_ids) if document_ids is not None else None
        return [
            hit for hit in self._hits
            if allowed is None or hit["document_id"] in allowed
        ]


class CorpusRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = DocumentStore(str(Path(self.temp.name) / "documents.sqlite3"))
        self.hits = []
        for index in (1, 2):
            document_id = f"doc-{index}"
            url = f"https://example.com/{index}"
            self.store.save({
                "document_id": document_id, "canonical_url": url,
                "source_url": url, "title": f"Source {index}",
                "markdown": f"body {index}", "content_hash": f"hash-{index}",
                "fetched_at": "2026-08-13T00:00:00+00:00", "http_metadata": {},
                "extraction_version": "v1", "job_id": f"job-{index}",
                "research_metadata": {"topic": "topic"},
                "created_at": "2026-08-13T00:00:00+00:00",
            })
            self.store.observe_job_source(
                f"job-{index}", document_id, "2026-08-13T00:00:00+00:00",
                {"topic": "topic"},
            )
            self.store.replace_chunks(
                document_id, [f"a distinctive phrase about widget{index} here"]
            )
            self.hits.append({
                "document_id": document_id, "canonical_url": url,
                "source_title": f"Source {index}", "chunk_index": 0,
                "score": 0.9 - index * 0.1,
                "text": f"a distinctive phrase about widget{index} here",
                "metadata": {},
            })

    def service(self, qdrant, lexical=True):
        return ScopedRetrievalService(
            StubOllama(), qdrant, self.store,
            candidate_limit=10, max_chunks_per_source=3,
            lexical=self.store if lexical else None,
        )

    def test_job_scope_still_returns_only_that_job(self):
        """The existing contract, unchanged."""
        qdrant = StubQdrant(self.hits)
        evidence = run(self.service(qdrant).retrieve("job-1", "widget1"))
        self.assertEqual(
            sorted({c.document_id for c in evidence.candidates}), ["doc-1"])

    def test_job_scope_still_passes_identity_filters_to_qdrant(self):
        qdrant = StubQdrant(self.hits)
        run(self.service(qdrant).retrieve("job-1", "widget1"))
        call = qdrant.calls[0]
        self.assertEqual(call["document_ids"], ["doc-1"])
        self.assertEqual(call["canonical_urls"], ["https://example.com/1"])

    def test_corpus_scope_reaches_documents_from_every_job(self):
        """The point of the change: 530 documents across 49 jobs, one base."""
        qdrant = StubQdrant(self.hits)
        evidence = run(self.service(qdrant).retrieve(None, "distinctive phrase"))
        self.assertEqual(
            sorted({c.document_id for c in evidence.candidates}),
            ["doc-1", "doc-2"],
        )

    def test_corpus_scope_sends_no_identity_filter(self):
        """Passing every id would be equivalent but grows without bound."""
        qdrant = StubQdrant(self.hits)
        run(self.service(qdrant).retrieve(None, "distinctive phrase"))
        call = qdrant.calls[0]
        self.assertIsNone(call["document_ids"])
        self.assertIsNone(call["canonical_urls"])

    def test_corpus_scope_still_caps_chunks_per_source(self):
        """Fusion and caps are not a job-only feature."""
        qdrant = StubQdrant(self.hits)
        service = ScopedRetrievalService(
            StubOllama(), qdrant, self.store,
            candidate_limit=10, max_chunks_per_source=1, lexical=self.store,
        )
        evidence = run(service.retrieve(None, "distinctive phrase"))
        per_source = {}
        for candidate in evidence.candidates:
            per_source[candidate.document_id] = per_source.get(
                candidate.document_id, 0) + 1
        self.assertTrue(all(count <= 1 for count in per_source.values()))

    def test_corpus_scope_runs_the_lexical_needle_channel(self):
        """The exact-term channel that recovered a DOI must reach the corpus."""
        rows = self.store.search_chunks("widget2", None, 10)
        self.assertEqual([row["document_id"] for row in rows], ["doc-2"])

    def test_empty_scope_list_is_still_an_error(self):
        """None means corpus; [] means a caller lost its scope."""
        with self.assertRaises(ValueError):
            self.store.search_chunks("widget1", [], 10)

    def test_topic_filter_narrows_the_corpus_to_matching_documents(self):
        """/query's topic_filter, now a source scope rather than a payload
        condition -- the lexical channel has no payload to filter on."""
        matched = self.store.documents_matching(topic="topic")
        self.assertEqual(len(matched), 2)
        self.assertEqual(self.store.documents_matching(topic="other"), [])

    def test_tag_filter_matches_any_supplied_tag(self):
        self.store.observe_job_source(
            "job-1", "doc-1", "2026-08-13T00:00:00+00:00",
            {"topic": "topic", "tags": ["ml", "rust"]},
        )
        self.assertEqual(
            [d["document_id"] for d in self.store.documents_matching(tags=["rust"])],
            ["doc-1"],
        )
        self.assertEqual(self.store.documents_matching(tags=["absent"]), [])

    def test_documents_with_no_tags_are_not_matched_by_a_tag_filter(self):
        """setUp records research_metadata without a tags key at all."""
        self.assertEqual(self.store.documents_matching(tags=["anything"]), [])

    def test_a_topic_filter_scopes_both_channels_identically(self):
        qdrant = StubQdrant(self.hits)
        run(self.service(qdrant).retrieve(
            None, "distinctive phrase", source_topic="topic"))
        self.assertEqual(qdrant.calls[0]["document_ids"], ["doc-1", "doc-2"])

    def test_a_filter_that_matches_nothing_returns_nothing(self):
        """Not the whole corpus: an unmatched filter must not widen."""
        qdrant = StubQdrant(self.hits)
        evidence = run(self.service(qdrant).retrieve(
            None, "distinctive phrase", source_topic="unrelated"))
        self.assertEqual(evidence.candidates, [])
        self.assertEqual(qdrant.calls, [])

    def test_job_scope_and_source_filters_are_exclusive(self):
        """Report synthesis never filters; a caller doing both is confused."""
        with self.assertRaises(ValueError):
            run(self.service(StubQdrant(self.hits)).retrieve(
                "job-1", "widget1", source_topic="topic"))

    def test_an_empty_corpus_returns_nothing_rather_than_failing(self):
        empty = DocumentStore(str(Path(self.temp.name) / "empty.sqlite3"))
        service = ScopedRetrievalService(
            StubOllama(), StubQdrant([]), empty, lexical=empty,
        )
        evidence = run(service.retrieve(None, "anything"))
        self.assertEqual(evidence.candidates, [])
