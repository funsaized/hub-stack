"""Offline, pinned-revision claim-support NLI research benchmark."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import statistics
import time

import psutil
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

try:
    from .claim_support_eval import (
        LABELS, evidence_text, evaluate_predictions, load_cases, select_threshold,
        threshold_sweep, zero_failure_upper_bound,
    )
except ImportError:
    from claim_support_eval import (
        LABELS, evidence_text, evaluate_predictions, load_cases, select_threshold,
        threshold_sweep, zero_failure_upper_bound,
    )


DEFAULT_MANIFEST = Path(__file__).parent / "fixtures" / "claim_support_calibration_v2.json"
MODEL_PROVENANCE = {
    "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli": {
        "license": "MIT", "revision": "6f5cf0a2b59cabb106aca4c287eed12e357e90eb",
        "training": "MNLI, FEVER-NLI, ANLI",
    },
    "tasksource/deberta-small-long-nli": {
        "license": "Apache-2.0", "revision": "9a77395d4d3751be9e2a69c4ae318491d9b3fffb",
        "training": "multi-task NLI and fact verification",
    },
    "tasksource/deberta-base-long-nli": {
        "license": "Apache-2.0", "revision": "04dcf11f844b07bc57015169fca2b7d6df8299d5",
        "training": "multi-task NLI and fact verification",
    },
    "cross-encoder/nli-deberta-v3-base": {
        "license": "Apache-2.0", "revision": "6c749ce3425cd33b46d187e45b92bbf96ee12ec7",
        "training": "SNLI and MultiNLI",
    },
    "pritamdeka/PubMedBERT-MNLI-MedNLI": {
        "license": "not declared", "revision": "f1b6ce2e0d49f295b4cbcdc56c01b5fab6d068ab",
        "training": "PubMedBERT fine-tuned on MNLI and MedNLI",
    },
}
LABEL_OVERRIDES = {
    "pritamdeka/PubMedBERT-MNLI-MedNLI": ["contradiction", "entailment", "neutral"],
    "cross-encoder/nli-deberta-v3-base": ["contradiction", "entailment", "neutral"],
    "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli": ["entailment", "neutral", "contradiction"],
}
MAX_LENGTH_OVERRIDES = {
    "tasksource/deberta-small-long-nli": 1680,
    "tasksource/deberta-base-long-nli": 1280,
}


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def label_names(model_name: str, model) -> list[str]:
    if model_name in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[model_name]
    names = [str(model.config.id2label[index]).lower() for index in range(model.config.num_labels)]
    if set(names) != set(LABELS):
        raise ValueError(f"unrecognized labels for {model_name}: {names}")
    return names


def premise(case: dict) -> str:
    return " ".join(f"Evidence {index + 1}: {evidence_text(text)}" for index, text in enumerate(case["evidence"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--partition", choices=("calibration", "final_blind", "critical_regression"), default="calibration")
    parser.add_argument("--model", default="MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli", choices=tuple(MODEL_PROVENANCE))
    parser.add_argument("--revision")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--entailment-threshold", type=float)
    parser.add_argument("--frozen-threshold", type=Path)
    parser.add_argument("--max-calibration-fsr", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    provenance = MODEL_PROVENANCE[args.model]
    revision = args.revision or provenance["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be an immutable 40-character commit hash")
    if args.partition == "final_blind" and not args.frozen_threshold:
        raise ValueError("final evaluation requires --frozen-threshold; tuning on final is forbidden")
    if args.frozen_threshold:
        frozen = json.loads(args.frozen_threshold.read_text(encoding="utf-8"))
        if frozen.get("model") != args.model or frozen.get("revision") != revision:
            raise ValueError("frozen threshold model/revision mismatch")
        threshold = float(frozen["threshold"])
    else:
        threshold = args.entailment_threshold

    os.environ.update({"TOKENIZERS_PARALLELISM": "false", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    if args.device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable")

    manifest, cases = load_cases(args.manifest, args.partition)
    if not cases:
        raise ValueError(f"no adjudicated {args.partition} cases; draft final data cannot be evaluated")
    process = psutil.Process()
    rss_before = process.memory_info().rss
    load_started = time.perf_counter()
    load_kwargs = {"revision": revision, "local_files_only": True}
    tokenizer = AutoTokenizer.from_pretrained(args.model, **load_kwargs)
    model = AutoModelForSequenceClassification.from_pretrained(args.model, **load_kwargs)
    model.eval().to(args.device)
    load_seconds = time.perf_counter() - load_started
    rss_after_load = process.memory_info().rss
    labels = label_names(args.model, model)
    parameter_bytes = sum(parameter.numel() * parameter.element_size() for parameter in model.parameters())
    max_length = MAX_LENGTH_OVERRIDES.get(args.model, min(getattr(model.config, "max_position_embeddings", 512), 512))
    premises = [premise(case) for case in cases]
    claims = [case["claim"] for case in cases]
    lengths = [len(tokenizer(text, claim, truncation=False)["input_ids"]) for text, claim in zip(premises, claims)]
    runnable = [index for index, length in enumerate(lengths) if length <= max_length]

    def infer() -> tuple[dict[int, list[float]], float]:
        probabilities: dict[int, list[float]] = {}
        started = time.perf_counter()
        with torch.inference_mode():
            for offset in range(0, len(runnable), args.batch_size):
                indexes = runnable[offset:offset + args.batch_size]
                encoded = tokenizer(
                    [premises[index] for index in indexes], [claims[index] for index in indexes],
                    padding=True, truncation=False, return_tensors="pt",
                ).to(args.device)
                logits = model(**encoded).logits
                for index, scores in zip(indexes, torch.softmax(logits, dim=-1).detach().cpu().tolist()):
                    probabilities[index] = scores
        if args.device == "cuda":
            torch.cuda.synchronize()
        return probabilities, time.perf_counter() - started

    infer()
    if args.device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    runs: list[float] = []
    run_probabilities: list[dict[int, list[float]]] = []
    for _ in range(args.repeats):
        probabilities, elapsed = infer()
        run_probabilities.append(probabilities)
        runs.append(elapsed)
    final_probabilities = run_probabilities[-1]
    max_repeat_delta = max((abs(score - run_probabilities[0][index][column]) for run in run_probabilities[1:] for index, scores in run.items() for column, score in enumerate(scores)), default=0.0)

    predictions = []
    for index, case in enumerate(cases):
        if index not in final_probabilities:
            predictions.append({"predicted": "unavailable", "scores": None, "status": "over_budget", "input_tokens": lengths[index]})
            continue
        raw = final_probabilities[index]
        scores = {label: score for label, score in zip(labels, raw)}
        predictions.append({
            "predicted": max(scores, key=scores.get), "scores": scores,
            "status": "scored", "input_tokens": lengths[index],
        })

    candidates = [round(value / 100, 2) for value in range(50, 100)]
    sweep = threshold_sweep(cases, predictions, candidates)
    selected = select_threshold(sweep, args.max_calibration_fsr) if args.partition == "calibration" and threshold is None else None
    if selected:
        threshold = selected["threshold"]
    if threshold is None:
        raise ValueError("non-calibration evaluation requires --entailment-threshold or --frozen-threshold")
    metrics = evaluate_predictions(cases, predictions, threshold)
    for prediction in metrics["predictions"]:
        if prediction["scores"]:
            prediction["scores"] = {label: round(score, 6) for label, score in prediction["scores"].items()}
    memory = process.memory_info()
    unsupported = metrics["totals"]["unsupported"]
    report = {
        "schema_version": "2.0.0",
        "corpus": {"id": manifest["corpus_id"], "partition": args.partition, "cases": len(cases)},
        "model": {"id": args.model, "revision": revision, "license": provenance["license"], "training": provenance["training"], "offline_local_files_only": True, "startup_succeeded": True},
        "policy": {"accept": "argmax entailment AND entailment score >= frozen threshold", "threshold": threshold, "raw_softmax_calibrated": False},
        "calibration": {"objective": "minimize false acceptance, then maximize supported retention", "candidate_thresholds": candidates, "selected": selected, "sweep": sweep} if args.partition == "calibration" else None,
        "metrics": {key: value for key, value in metrics.items() if key != "predictions"},
        "risk_coverage": [{
            "threshold": row["threshold"],
            "accepted_coverage": round(sum(prediction["predicted"] == "entailment" and prediction.get("scores") and prediction["scores"]["entailment"] >= row["threshold"] for prediction in predictions) / len(predictions), 6),
            "false_support_rate": row["false_support_rate"],
            "supported_retention": row["supported_claim_retention"],
        } for row in sweep],
        "statistical_bound": {"confidence": 0.95, "zero_failure_false_support_upper_bound": round(zero_failure_upper_bound(unsupported), 6) if unsupported and metrics["totals"]["accepted_unsupported"] == 0 else None},
        "determinism": {"repeats": args.repeats, "max_score_delta": max_repeat_delta, "exact_scores_repeated": max_repeat_delta == 0.0},
        "latency": {
            "device": args.device, "batch_size": args.batch_size, "model_load_seconds": round(load_seconds, 3),
            "median_all_scored_ms": round(statistics.median(runs) * 1000, 3),
            "median_per_scored_claim_ms": round(statistics.median(runs) * 1000 / max(len(runnable), 1), 3),
            "p95_all_scored_ms": round(percentile(runs, .95) * 1000, 3),
        },
        "memory": {
            "parameter_mib": round(parameter_bytes / 2**20, 1),
            "cpu_peak_working_set_mib": round(getattr(memory, "peak_wset", memory.rss) / 2**20, 1),
            "rss_after_inference_mib": round(memory.rss / 2**20, 1),
            "rss_load_delta_mib": round((rss_after_load - rss_before) / 2**20, 1),
            "cuda_peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1) if args.device == "cuda" else 0.0,
            "cuda_peak_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1) if args.device == "cuda" else 0.0,
        },
        "budget": {"max_tokens": max_length, "over_budget_ids": [case["id"] for case, length in zip(cases, lengths) if length > max_length], "silent_truncation": False},
        "predictions": metrics["predictions"],
    }
    rendered = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
