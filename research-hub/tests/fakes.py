"""Shared doubles for the retrieval boundary.

Since HUB-043 the query layer talks to ``ScopedRetrievalService`` rather than
straight to Qdrant, so tests that used to stub ``qdrant.search`` stub this
instead. Keeping one double means a change to the retrieval contract breaks
in one place rather than three.
"""

from app.retrieval import (
    EvidenceCandidate, RetrievalDiagnostics, RetrievedEvidence,
)


def candidate(text: str, url: str, title: str, score: float, *,
              document_id: str = "", chunk_index: int = 0,
              metadata: dict | None = None) -> EvidenceCandidate:
    return EvidenceCandidate(
        text=text, canonical_url=url, source_title=title,
        document_id=document_id or url, chunk_index=chunk_index,
        score=score, metadata=metadata or {},
    )


class FakeRetrieval:
    """Returns fixed candidates and records the scope it was asked for."""

    def __init__(self, candidates=None):
        self.candidates = list(candidates or [])
        self.calls = []

    async def retrieve(self, job_id, query, *, source_topic=None,
                       source_tags=None):
        self.calls.append({
            "job_id": job_id, "query": query,
            "source_topic": source_topic, "source_tags": source_tags,
        })
        return RetrievedEvidence(
            list(self.candidates),
            RetrievalDiagnostics(
                candidates_considered=len(self.candidates),
                chunks_selected=len(self.candidates),
                sources_available=len({c.document_id for c in self.candidates}),
                sources_represented=len({c.document_id for c in self.candidates}),
                min_selected_score=min(
                    (c.score for c in self.candidates), default=None),
                max_selected_score=max(
                    (c.score for c in self.candidates), default=None),
            ),
        )
