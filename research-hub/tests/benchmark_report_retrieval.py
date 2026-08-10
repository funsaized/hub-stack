"""Deterministic Phase 4 retrieval and citation evaluation command."""

import argparse
import asyncio
import json
from pathlib import Path

from app.retrieval import ScopedRetrievalService, pack_evidence
from app.synthesis import ClaimValidationError, _retain_cited_claims, _validate_claims


DEFAULT_MANIFEST = Path(__file__).parent / "fixtures" / "report_retrieval_cases.json"


class FixtureDocuments:
    def __init__(self, documents: list[dict]):
        self.documents = documents

    def documents_for_job(self, job_id: str) -> list[dict]:
        return sorted(
            [doc for doc in self.documents if job_id in doc["observed_by_jobs"]],
            key=lambda doc: (doc["canonical_url"], doc["document_id"]),
        )


class FixtureOllama:
    async def embed(self, _topic: str) -> list[float]:
        return [1.0, 0.0]


class FixtureQdrant:
    def __init__(self, candidates: list[dict]):
        self.candidates = candidates

    def search_evidence(self, _vector, limit, **_scope) -> list[dict]:
        return self.candidates[:limit]


def ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


async def evaluate_case(manifest: dict, case: dict) -> tuple[dict, dict]:
    documents = FixtureDocuments(manifest["documents"])
    retained = documents.documents_for_job(case["job_id"])
    service = ScopedRetrievalService(
        FixtureOllama(),
        FixtureQdrant(case["candidates"]),
        documents,
        candidate_limit=manifest["candidate_limit"],
        max_chunks_per_source=manifest["max_chunks_per_source"],
    )
    retrieved = await service.retrieve(case["job_id"], case["topic"])
    selected = retrieved.candidates[:manifest["top_k"]]
    expected = case["expected_sentinels"]

    def matches(candidate, sentinel) -> bool:
        return (
            candidate.document_id == sentinel["document_id"]
            and sentinel["text"] in candidate.text
        )

    sentinel_hits = sum(
        any(matches(candidate, sentinel) for candidate in selected)
        for sentinel in expected
    )
    relevant_ranks = [
        index for index, candidate in enumerate(selected, 1)
        if any(matches(candidate, sentinel) for sentinel in expected)
    ]
    relevant_chunks = len(relevant_ranks)
    expected_sources = set(case["expected_sources"])
    represented_expected_sources = expected_sources & {
        candidate.document_id for candidate in selected
    }
    duplicates = len(selected) - len({candidate.text for candidate in selected})

    source_ids = {
        document["document_id"]: f"S{index}"
        for index, document in enumerate(retained, 1)
    }
    packed, _context = pack_evidence(
        selected,
        system="Synthetic deterministic evaluation.",
        question=case["topic"],
        context_limit=8192,
        answer_reserve=1024,
        source_ids=source_ids,
    )
    represented = {int(source_ids[item.document_id][1:]) for item in packed}
    valid_citations = 0
    for claim in case["valid_claims"]:
        try:
            _validate_claims([claim], represented, "evaluation")
            valid_citations += 1
        except ClaimValidationError:
            pass
    _kept, rejected = _retain_cited_claims(
        case["unsupported_claims"], represented, "evaluation"
    )

    counts = {
        "citation_total": len(case["valid_claims"]),
        "citation_valid": valid_citations,
        "duplicates": duplicates,
        "expected_sources": len(expected_sources),
        "relevant_chunks": relevant_chunks,
        "selected_chunks": len(selected),
        "sentinel_hits": sentinel_hits,
        "sentinel_total": len(expected),
        "sources_hit": len(represented_expected_sources),
        "unsupported_rejections": sum(rejected.values()),
    }
    metrics = {
        "citation_validity": ratio(valid_citations, counts["citation_total"]),
        "critical": bool(case["critical"]),
        "duplicate_rate": ratio(duplicates, len(selected)),
        "id": case["id"],
        "precision_at_k": ratio(relevant_chunks, len(selected)),
        "recall_at_k": ratio(sentinel_hits, len(expected)),
        "reciprocal_rank": round(1 / relevant_ranks[0], 6) if relevant_ranks else 0.0,
        "source_coverage": ratio(len(represented_expected_sources), len(expected_sources)),
        "unsupported_claim_rejection_count": counts["unsupported_rejections"],
    }
    return metrics, counts


async def evaluate(manifest: dict) -> dict:
    if manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    if not isinstance(manifest.get("documents"), list) or not isinstance(
        manifest.get("cases"), list
    ):
        raise ValueError("manifest documents and cases must be lists")
    for name in ("top_k", "candidate_limit", "max_chunks_per_source"):
        if not isinstance(manifest.get(name), int) or manifest[name] < 1:
            raise ValueError(f"manifest {name} must be a positive integer")

    evaluated = [await evaluate_case(manifest, case) for case in manifest["cases"]]
    cases = [item[0] for item in evaluated]
    counts = [item[1] for item in evaluated]
    total = lambda name: sum(item[name] for item in counts)
    critical = [
        item for item, case in zip(counts, manifest["cases"]) if case["critical"]
    ]
    critical_hits = sum(item["sentinel_hits"] for item in critical)
    critical_total = sum(item["sentinel_total"] for item in critical)
    citation_validity = ratio(total("citation_valid"), total("citation_total"))
    critical_recall = ratio(critical_hits, critical_total)
    metrics = {
        "citation_validity": citation_validity,
        "critical_recall_at_k": critical_recall,
        "duplicate_rate": ratio(total("duplicates"), total("selected_chunks")),
        "precision_at_k": ratio(total("relevant_chunks"), total("selected_chunks")),
        "recall_at_k": ratio(total("sentinel_hits"), total("sentinel_total")),
        "reciprocal_rank": round(
            sum(case["reciprocal_rank"] for case in cases) / len(cases), 6
        ) if cases else 0.0,
        "source_coverage": ratio(total("sources_hit"), total("expected_sources")),
        "unsupported_claim_rejection_count": total("unsupported_rejections"),
    }
    gates = {
        "citation_validity": citation_validity == 1.0,
        "critical_recall_at_k": critical_recall == 1.0,
    }
    gates["passed"] = all(gates.values())
    return {
        "cases": cases,
        "gates": gates,
        "metrics": metrics,
        "schema_version": 1,
        "top_k": manifest["top_k"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = asyncio.run(evaluate(manifest))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["gates"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
