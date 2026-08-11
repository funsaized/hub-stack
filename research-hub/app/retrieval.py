"""Transport-neutral retrieval scoped to retained job sources."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, replace
from typing import Any

from .context import (
    classify_and_sanitize,
    pack_complete_entries,
    render_entry,
    render_prompt,
    token_count,
)


@dataclass(frozen=True)
class EvidenceCandidate:
    text: str
    canonical_url: str
    source_title: str
    document_id: str
    chunk_index: int
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RetrievalDiagnostics:
    candidates_considered: int
    chunks_selected: int
    sources_available: int
    sources_represented: int
    min_selected_score: float | None
    max_selected_score: float | None


@dataclass(frozen=True)
class RetrievedEvidence:
    candidates: list[EvidenceCandidate]
    diagnostics: RetrievalDiagnostics


class ScopedRetrievalService:
    def __init__(
        self,
        ollama,
        qdrant,
        documents,
        *,
        candidate_limit: int = 40,
        max_chunks_per_source: int = 3,
        min_score: float | None = None,
        lexical=None,
        rrf_k: int = 60,
    ):
        if candidate_limit < 1 or max_chunks_per_source < 1:
            raise ValueError("retrieval limits must be positive")
        if min_score is not None and not math.isfinite(min_score):
            raise ValueError("minimum retrieval score must be finite")
        if rrf_k < 1:
            raise ValueError("reciprocal rank fusion constant must be positive")
        self.ollama = ollama
        self.qdrant = qdrant
        self.documents = documents
        self.candidate_limit = candidate_limit
        self.max_chunks_per_source = max_chunks_per_source
        self.min_score = min_score
        self.lexical = lexical
        self.rrf_k = rrf_k

    async def retrieve(self, job_id: str, topic: str) -> RetrievedEvidence:
        retained = await asyncio.to_thread(self.documents.documents_for_job, job_id)
        retained_by_id = {value["document_id"]: value for value in retained}
        if not retained_by_id:
            return RetrievedEvidence([], _diagnostics(0, [], 0))

        urls = sorted({value["canonical_url"] for value in retained})
        document_ids = sorted(retained_by_id)
        vector = await self.ollama.embed(topic)
        hits = await asyncio.to_thread(
            self.qdrant.search_evidence,
            vector,
            self.candidate_limit,
            canonical_urls=urls,
            document_ids=document_ids,
        )

        candidates: list[EvidenceCandidate] = []
        for hit in hits:
            source = retained_by_id.get(hit.get("document_id"))
            if not source or hit.get("canonical_url") != source["canonical_url"]:
                continue
            score = float(hit["score"])
            if self.min_score is not None and score < self.min_score:
                continue
            text, labels = classify_and_sanitize(hit.get("text", ""))
            metadata = dict(hit.get("metadata") or {})
            metadata["security_labels"] = list(dict.fromkeys([
                *(metadata.get("security_labels") or []), *labels,
            ]))
            candidates.append(EvidenceCandidate(
                text=text,
                canonical_url=source["canonical_url"],
                source_title=source.get("title") or hit.get("source_title", ""),
                document_id=source["document_id"],
                chunk_index=int(hit.get("chunk_index", -1)),
                score=score,
                metadata=metadata,
            ))

        candidates.sort(key=lambda value: (
            -value.score,
            value.canonical_url,
            value.document_id,
            value.chunk_index,
            value.text,
        ))

        lexical_hits = []
        if self.lexical is not None:
            lexical_hits = await asyncio.to_thread(
                self.lexical.search_chunks, topic, document_ids, self.candidate_limit
            )
            candidates = _fuse_rankings(
                candidates, lexical_hits, retained_by_id, self.rrf_k
            )

        selected: list[EvidenceCandidate] = []
        seen_text: set[str] = set()
        source_counts: dict[str, int] = {}
        for candidate in candidates:
            if candidate.text in seen_text:
                continue
            count = source_counts.get(candidate.document_id, 0)
            if count >= self.max_chunks_per_source:
                continue
            selected.append(candidate)
            seen_text.add(candidate.text)
            source_counts[candidate.document_id] = count + 1

        return RetrievedEvidence(
            selected,
            _diagnostics(len(hits) + len(lexical_hits), selected, len(retained_by_id)),
        )


def _fuse_rankings(
    dense: list[EvidenceCandidate],
    lexical_hits: list[dict],
    retained_by_id: dict[str, dict],
    rrf_k: int,
) -> list[EvidenceCandidate]:
    """Deterministic reciprocal rank fusion keyed by canonical chunk identity."""
    if not lexical_hits:
        return dense

    fused: dict[tuple[str, int], float] = {}
    channels: dict[tuple[str, int], list[str]] = {}
    by_key: dict[tuple[str, int], EvidenceCandidate] = {}

    for rank, candidate in enumerate(dense, 1):
        key = (candidate.document_id, candidate.chunk_index)
        fused[key] = fused.get(key, 0.0) + 1.0 / (rrf_k + rank)
        channels.setdefault(key, []).append("dense")
        by_key.setdefault(key, candidate)

    for rank, hit in enumerate(lexical_hits, 1):
        source = retained_by_id.get(hit["document_id"])
        if not source:
            continue
        key = (hit["document_id"], hit["chunk_index"])
        fused[key] = fused.get(key, 0.0) + 1.0 / (rrf_k + rank)
        channels.setdefault(key, []).append("lexical")
        if key not in by_key:
            text, labels = classify_and_sanitize(hit.get("text", ""))
            by_key[key] = EvidenceCandidate(
                text=text,
                canonical_url=source["canonical_url"],
                source_title=source.get("title") or "",
                document_id=hit["document_id"],
                chunk_index=int(hit["chunk_index"]),
                score=0.0,
                metadata={"security_labels": labels},
            )

    candidates = [
        replace(
            by_key[key],
            metadata={
                **by_key[key].metadata,
                "retrieval_channels": channels[key],
                "rrf_score": round(fused[key], 6),
            },
        )
        for key in fused
    ]
    candidates.sort(key=lambda value: (
        -value.metadata["rrf_score"],
        value.canonical_url,
        value.document_id,
        value.chunk_index,
        value.text,
    ))
    return candidates


def _diagnostics(
    considered: int,
    selected: list[EvidenceCandidate],
    sources_available: int,
) -> RetrievalDiagnostics:
    scores = [value.score for value in selected]
    return RetrievalDiagnostics(
        candidates_considered=considered,
        chunks_selected=len(selected),
        sources_available=sources_available,
        sources_represented=len({value.document_id for value in selected}),
        min_selected_score=min(scores) if scores else None,
        max_selected_score=max(scores) if scores else None,
    )


def pack_evidence(
    candidates: list[EvidenceCandidate],
    *,
    system: str,
    question: str,
    context_limit: int,
    answer_reserve: int,
    source_ids: dict[str, str] | None = None,
    packed_ids: bool = False,
) -> tuple[list[EvidenceCandidate], str]:
    """Pack complete sanitized evidence entries within a conservative budget."""
    budget = context_limit - answer_reserve - token_count(system) - token_count(
        render_prompt("", question)
    )
    return pack_complete_entries(
        candidates,
        lambda index, value: render_entry(
            f"E{index}" if packed_ids else source_ids[value.document_id] if source_ids else index,
            value.source_title,
            value.canonical_url,
            value.text,
            value.document_id if source_ids or packed_ids else None,
        ),
        budget,
    )
