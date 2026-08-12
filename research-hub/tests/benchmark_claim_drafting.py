"""Offline claim-drafting evaluation for frozen report evidence.

Every Phase 4 design iteration before this one was measured by issuing a live
report retry: one stochastic sample, binary feedback, and a mutated report
lifecycle. This command replays frozen evidence instead.

  --offline (default)  Deterministic. Measures span selection only: junk
                       rejection, exact-substring fidelity, source coverage and
                       the critical spans that must survive. Needs no service.

  --live               Adds real generation and real verification for `--repeat`
                       samples and reports verified-claim yield. Talks only to
                       Ollama /api/generate and the claim verifier /verify. It
                       never reads or writes Redis, SQLite, Qdrant, a report, or
                       the corpus, and never issues a report retry.
"""

import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from app.judge_gate import JudgeClaimVerifier
from app.clients import OllamaClient
from app.spans import MAX_SPAN_CHARS, propositional_spans, sentence_bounds
from app.synthesis import _draft_claims


DEFAULT_MANIFEST = Path(__file__).parent / "fixtures" / "claim_drafting_cases.json"


def load(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "1.0.0":
        raise ValueError("schema_version must be 1.0.0")
    for key in ("documents", "chunks", "expected_spans"):
        if not isinstance(manifest.get(key), list) or not manifest[key]:
            raise ValueError(f"{key} must be a non-empty list")
    return manifest


def span_sources(manifest: dict) -> list[dict]:
    """Reproduce what synthesis would offer the model, in the same order."""
    sources: list[dict] = []
    for index, chunk in enumerate(manifest["chunks"], 1):
        candidate = SimpleNamespace(metadata={"source_text": chunk["text"]})
        for span in propositional_spans(chunk["text"]):
            sources.append({
                "span_id": f"P{len(sources) + 1}",
                "evidence_id": f"E{index}",
                "source_id": f"S{index}",
                "span": span,
                "candidate": candidate,
                "source_title": chunk["source_title"],
                "url": chunk["canonical_url"],
                "document_id": chunk["document_id"],
            })
    return sources


def offline_metrics(manifest: dict) -> dict:
    sources = span_sources(manifest)
    offered = sum(len(sentence_bounds(chunk["text"])) for chunk in manifest["chunks"])
    selected = [source["span"] for source in sources]
    exact = sum(
        source["span"] in source["candidate"].metadata["source_text"]
        for source in sources
    )
    expected = set(manifest["expected_spans"])
    return {
        "candidate_sentences": offered,
        "propositional_spans": len(selected),
        "junk_rejection_rate": round(1 - len(selected) / offered, 6) if offered else 0.0,
        "exact_substring_rate": round(exact / len(selected), 6) if selected else 1.0,
        "sources_represented": len({source["document_id"] for source in sources}),
        "sources_available": len(manifest["documents"]),
        "critical_span_recall": round(
            len(expected & set(selected)) / len(expected), 6
        ) if expected else 1.0,
        "max_span_chars": max((len(span) for span in selected), default=0),
        "over_budget_spans": sum(len(span) > MAX_SPAN_CHARS for span in selected),
    }


def check_offline(metrics: dict) -> list[str]:
    failures = []
    if metrics["exact_substring_rate"] != 1.0:
        failures.append("a selected span is not an exact substring of its chunk")
    if metrics["critical_span_recall"] != 1.0:
        failures.append("a critical evidence span was filtered out")
    if metrics["over_budget_spans"]:
        failures.append("a selected span exceeds the verifier premise budget")
    if not metrics["propositional_spans"]:
        failures.append("no propositional span survived selection")
    return failures


async def live_metrics(manifest: dict, repeat: int, limit: int) -> dict:
    ollama = OllamaClient(
        os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        os.environ.get("LLM_MODEL", "qwen3.5:9b"),
        os.environ.get("EMBEDDING_MODEL", "nomic-embed-text"),
    )
    # HUB-034: the live gate is the MiniMax judge; this benchmark now spends
    # metered judge calls (drafted claims per run, subscription windows apply).
    verifier = JudgeClaimVerifier(
        base_url=os.environ.get("JUDGE_BASE_URL", "https://api.minimax.io/v1"),
        api_key=os.environ["MINIMAX_SUBSCRIPTION_KEY"],
        model=os.environ.get("JUDGE_MODEL", "MiniMax-M3"),
        timeout=float(os.environ.get("JUDGE_TIMEOUT_SECONDS", "60")),
    )
    orchestrator = SimpleNamespace(ollama=ollama, claim_verifier=verifier)
    sources = span_sources(manifest)[:limit]
    samples = []
    try:
        if not await verifier.health():
            raise RuntimeError("judge gate is not configured (MINIMAX_SUBSCRIPTION_KEY)")
        for run in range(1, repeat + 1):
            drafted, draft_rejections = await _draft_claims(
                orchestrator, sources, stage="benchmark_claim_drafting",
            )
            reasons = (
                await verifier.verify([claim for _kind, claim in drafted])
                if drafted else []
            )
            outcomes = Counter(reason or "verified" for reason in reasons)
            outcomes.update(draft_rejections)
            samples.append({
                "run": run,
                "verified": sum(reason is None for reason in reasons),
                "outcomes": dict(outcomes),
                "claims": [
                    {
                        "span_id": claim["evidence_refs"][0]["span_id"],
                        "kind": kind,
                        "claim": claim["text"],
                        "reason": reason,
                    }
                    for (kind, claim), reason in zip(drafted, reasons)
                ],
            })
    finally:
        await ollama.close()
        await verifier.close()
    verified = [sample["verified"] for sample in samples]
    return {
        "spans_drafted": len(sources),
        "runs": repeat,
        "verified_min": min(verified, default=0),
        "verified_max": max(verified, default=0),
        "runs_with_at_least_one_verified_claim": sum(count > 0 for count in verified),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    manifest = load(args.manifest)
    report = {"provenance": manifest["provenance"], "offline": offline_metrics(manifest)}
    failures = check_offline(report["offline"])
    if args.live and not failures:
        report["live"] = asyncio.run(live_metrics(manifest, args.repeat, args.limit))
    report["failures"] = failures
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
