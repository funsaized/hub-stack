"""HUB-038 source-screening calibration benchmark.

Three modes, so a threshold decision can be re-derived rather than trusted:

    python tests/benchmark_source_screening.py analyse
        Offline. Recomputes AUC and the threshold sweep from the committed
        reference set. No network, no model, no metered call.

    python tests/benchmark_source_screening.py score > scores.jsonl
        Rescores every retained document with the live embedding model.
        Local only. Run this after changing the scoring function.

    python tests/benchmark_source_screening.py label < scores.jsonl > out.jsonl
        Relabels documents with MiniMax. METERED -- roughly one call per
        document. Only needed when building a new reference set.

The reference set at ``tests/fixtures/source_screening_reference_v1.jsonl``
covers every document retained across 38 jobs and 20 topics as of
2026-08-13. It is a REFERENCE SET, not ground truth and not a seal: labels
come from MiniMax, and among 44 documents labelled more than once 7 disagreed
(16%). Treat AUC differences under ~0.05 as noise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURE = Path(__file__).with_name("fixtures") / "source_screening_reference_v1.jsonl"
VARIANTS = ("opening_score", "windowed_topic_score", "windowed_facet_score")
SWEEP = (0.45, 0.50, 0.52, 0.54, 0.56, 0.58, 0.60, 0.62, 0.64, 0.66)
DECIDED_LABELS = ("on_topic", "marginal", "off_topic")


def load(path: Path = FIXTURE) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def auc(positive: list[float], negative: list[float]) -> float | None:
    """Probability a random on-topic document outranks a random off-topic one."""
    if not positive or not negative:
        return None
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0
        for p in positive for n in negative
    )
    return wins / (len(positive) * len(negative))


def variant_auc(rows: list[dict], field: str) -> tuple[float | None, int]:
    usable = [r for r in rows
              if r["label"] in ("on_topic", "off_topic") and r.get(field) is not None]
    on = [r[field] for r in usable if r["label"] == "on_topic"]
    off = [r[field] for r in usable if r["label"] == "off_topic"]
    return auc(on, off), len(usable)


def sweep(rows: list[dict], field: str) -> list[dict]:
    on = [r[field] for r in rows
          if r["label"] == "on_topic" and r.get(field) is not None]
    off = [r[field] for r in rows
           if r["label"] == "off_topic" and r.get(field) is not None]
    table = []
    for threshold in SWEEP:
        table.append({
            "threshold": threshold,
            "on_topic_kept": sum(1 for x in on if x >= threshold) / len(on),
            "off_topic_dropped": sum(1 for x in off if x < threshold) / len(off),
            "on_topic_lost": sum(1 for x in on if x < threshold),
        })
    return table


def analyse() -> None:
    rows = [r for r in load() if r["label"] in DECIDED_LABELS]
    print(f"reference set: {len(rows)} labelled documents, "
          f"{len({r['topic'] for r in rows})} topics")
    counts = {label: sum(1 for r in rows if r["label"] == label)
              for label in DECIDED_LABELS}
    print(f"  labels: {counts}\n")

    print("separation by scoring variant (on_topic vs off_topic)")
    for field in VARIANTS:
        score, n = variant_auc(rows, field)
        print(f"  {field:<22} n={n:<4} AUC={score:.3f}" if score is not None
              else f"  {field:<22} n={n:<4} AUC=  n/a")

    print("\nthreshold sweep on the deployed variant (windowed_topic_score)")
    print(f"  {'thr':>5} {'on kept':>9} {'off dropped':>12} {'good lost':>10}")
    for row in sweep(rows, "windowed_topic_score"):
        print(f"  {row['threshold']:>5.2f} {row['on_topic_kept']:>8.1%} "
              f"{row['off_topic_dropped']:>12.1%} {row['on_topic_lost']:>10d}")

    print("\nper-topic separation (topics with >= 3 off_topic documents)")
    by_topic: dict[str, list[dict]] = {}
    for row in rows:
        by_topic.setdefault(row["topic"], []).append(row)
    for topic, group in sorted(by_topic.items(), key=lambda kv: -len(kv[1])):
        off = [r for r in group if r["label"] == "off_topic"]
        if len(off) < 3:
            continue
        score, _n = variant_auc(group, "windowed_topic_score")
        print(f"  {topic[:52]:<52} n={len(group):<4} AUC={score:.3f}")


def score() -> None:
    """Rescore every retained document. Local embeddings only."""
    import asyncio
    import sqlite3

    from app.clients import OllamaClient
    from app.config import load_config
    from app.query_plan import cosine
    from app.research import _topic_probe_windows

    async def main() -> None:
        cfg = load_config()
        ollama = OllamaClient(cfg.ollama_url, cfg.llm_model, cfg.embedding_model)
        store = sqlite3.connect(cfg.document_store_path)
        rows = store.execute("""
            select r.topic, d.canonical_url, d.title, d.markdown
            from job_sources j
            join research_reports r on r.job_id = j.job_id
            join documents d on d.document_id = j.document_id
        """).fetchall()
        topics = sorted({row[0] for row in rows})
        anchors = dict(zip(topics, await ollama.embed_batch(topics)))
        for topic, url, title, markdown in rows:
            windows = _topic_probe_windows({"title": title, "markdown": markdown})
            vectors = await ollama.embed_batch(windows)
            print(json.dumps({
                "topic": topic, "url": url, "title": (title or "")[:200],
                "chars": len(markdown or ""),
                "windowed_topic_score": round(
                    max(cosine(v, anchors[topic]) for v in vectors), 5),
            }), flush=True)
        await ollama.close()

    asyncio.run(main())


def label() -> None:
    print("Relabelling is metered and rebuilds the reference set; it is "
          "intentionally not automated here. See the HUB-038 backlog entry "
          "for the prompt and procedure used to build v1.", file=sys.stderr)
    raise SystemExit(2)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "analyse"
    {"analyse": analyse, "score": score, "label": label}[mode]()
