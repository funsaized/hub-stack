"""Stdlib-only validation and metrics for claim-support research fixtures."""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any


LABELS = ("entailment", "contradiction", "neutral")
PARTITIONS = ("calibration", "final_blind", "critical_regression")
REQUIRED_CATEGORIES = {
    "exact_support", "faithful_paraphrase", "contradiction", "neutral_related",
    "missing_qualifier", "negation", "double_negation", "wrong_entity",
    "wrong_population", "wrong_intervention", "wrong_comparator", "wrong_outcome",
    "wrong_number", "wrong_unit", "wrong_confidence_interval",
    "wrong_statistical_significance", "causal_overreach", "temporal_overreach",
    "study_stage_overreach", "modality", "quantifier", "narrower_scope",
    "broader_scope", "bibliography_only", "fragmented_subject",
    "fragmented_object", "supported_compound", "partial_compound",
    "genuine_multi_source", "false_multi_source_relation", "conflicting_evidence",
    "evidence_order", "irrelevant_padding", "long_context", "over_budget",
    "healthcare", "scientific", "adversarial_lexical_overlap",
}


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def evidence_text(value: str | dict[str, Any]) -> str:
    if isinstance(value, str):
        return value
    if set(value) != {"prefix", "repeat", "count", "suffix"}:
        raise ValueError(f"malformed generated evidence: {value!r}")
    if not isinstance(value["count"], int) or not 1 <= value["count"] <= 10_000:
        raise ValueError("generated evidence count must be 1..10000")
    if not all(isinstance(value[key], str) for key in ("prefix", "repeat", "suffix")):
        raise ValueError("generated evidence text fields must be strings")
    return " ".join(part for part in (
        value["prefix"], " ".join([value["repeat"]] * value["count"]), value["suffix"]
    ) if part)


def case_fingerprint(case: dict[str, Any]) -> str:
    value = json.dumps({
        "claim": normalize(case["claim"]),
        "evidence": [normalize(evidence_text(item)) for item in case["evidence"]],
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _check_span(case: dict[str, Any], span: dict[str, Any], kind: str) -> None:
    if set(span) != {"evidence_index", "text"}:
        raise ValueError(f"{case['id']}: malformed {kind} span")
    index = span["evidence_index"]
    if not isinstance(index, int) or not 0 <= index < len(case["evidence"]):
        raise ValueError(f"{case['id']}: {kind} span evidence_index is invalid")
    if not isinstance(span["text"], str) or not span["text"]:
        raise ValueError(f"{case['id']}: {kind} span text is empty")
    if span["text"] not in evidence_text(case["evidence"][index]):
        raise ValueError(f"{case['id']}: {kind} span does not resolve exactly")


def validate_manifest(manifest: dict[str, Any], *, require_final_ready: bool = False) -> dict[str, Any]:
    if manifest.get("schema_version") != "2.0.0":
        raise ValueError("schema_version must be 2.0.0")
    if manifest.get("policy", {}).get("accept_label") != "entailment":
        raise ValueError("only entailment may be accepted")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases must be a non-empty list")

    ids: set[str] = set()
    fingerprints: dict[str, str] = {}
    declared_categories = set(manifest.get("coverage", {}).get("required_categories", REQUIRED_CATEGORIES))
    unknown_declared = declared_categories - REQUIRED_CATEGORIES
    if unknown_declared:
        raise ValueError(f"unknown required categories: {sorted(unknown_declared)}")
    covered: set[str] = set()
    counts: Counter[tuple[str, str]] = Counter()
    for case in cases:
        required = {"id", "partition", "status", "claim", "evidence", "categories", "critical", "phi"}
        missing = required - set(case)
        if missing:
            raise ValueError(f"case missing fields {sorted(missing)}")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id or case_id in ids:
            raise ValueError(f"duplicate or invalid id: {case_id!r}")
        ids.add(case_id)
        if case["partition"] not in PARTITIONS:
            raise ValueError(f"{case_id}: invalid partition")
        if case["status"] not in {"draft", "adjudicated"}:
            raise ValueError(f"{case_id}: invalid status")
        if not isinstance(case["claim"], str) or not case["claim"].strip():
            raise ValueError(f"{case_id}: malformed claim")
        if not isinstance(case["evidence"], list) or not case["evidence"]:
            raise ValueError(f"{case_id}: malformed evidence")
        for item in case["evidence"]:
            if not evidence_text(item).strip() or "\x00" in evidence_text(item):
                raise ValueError(f"{case_id}: malformed evidence")
        if case["phi"] is not False:
            raise ValueError(f"{case_id}: fixtures must explicitly contain no PHI")
        if not isinstance(case["categories"], list) or (case["status"] == "adjudicated" and not case["categories"]):
            raise ValueError(f"{case_id}: adjudicated categories must be non-empty")
        unknown = set(case["categories"]) - REQUIRED_CATEGORIES
        if unknown:
            raise ValueError(f"{case_id}: unknown categories {sorted(unknown)}")
        covered.update(case["categories"])

        fingerprint = case_fingerprint(case)
        prior_partition = fingerprints.get(fingerprint)
        if prior_partition and prior_partition != case["partition"]:
            raise ValueError(f"{case_id}: partition leakage from {prior_partition}")
        fingerprints[fingerprint] = case["partition"]

        if case["status"] == "adjudicated":
            annotation = case.get("annotation", {})
            label = annotation.get("label")
            if label not in LABELS:
                raise ValueError(f"{case_id}: invalid adjudicated label")
            if not annotation.get("rationale") or not annotation.get("material_components"):
                raise ValueError(f"{case_id}: missing rationale or material components")
            for kind in ("supporting_spans", "refuting_spans"):
                spans = annotation.get(kind)
                if not isinstance(spans, list):
                    raise ValueError(f"{case_id}: missing {kind}")
                for span in spans:
                    _check_span(case, span, kind)
            for span in annotation.get("relevant_spans", []):
                _check_span(case, span, "relevant_spans")
            counts[(case["partition"], label)] += 1
            if case["partition"] == "final_blind":
                reviews = case.get("reviews", [])
                if len(reviews) != 2 or len({review.get("reviewer_id") for review in reviews}) != 2:
                    raise ValueError(f"{case_id}: final cases require two independent reviewers")
                if any(review.get("label") not in LABELS for review in reviews):
                    raise ValueError(f"{case_id}: invalid reviewer label")
                for review in reviews:
                    if not review.get("rationale") or not review.get("material_components"):
                        raise ValueError(f"{case_id}: incomplete independent review")
                    for kind in ("supporting_spans", "refuting_spans", "relevant_spans"):
                        spans = review.get(kind, [])
                        if not isinstance(spans, list):
                            raise ValueError(f"{case_id}: malformed reviewer {kind}")
                        for span in spans:
                            _check_span(case, span, f"reviewer {kind}")
        elif case["partition"] != "final_blind":
            raise ValueError(f"{case_id}: only final_blind cases may remain draft")

    missing_categories = declared_categories - covered
    if missing_categories:
        raise ValueError(f"missing categories: {sorted(missing_categories)}")
    final_unsupported = counts[("final_blind", "contradiction")] + counts[("final_blind", "neutral")]
    final_supported = counts[("final_blind", "entailment")]
    if require_final_ready and (final_unsupported < 299 or final_supported == 0):
        raise ValueError("final gate requires at least 299 unsupported and representative supported cases")
    return {
        "cases": len(cases),
        "categories": len(covered),
        "partitions": {partition: sum(case["partition"] == partition for case in cases) for partition in PARTITIONS},
        "draft_final_cases": sum(case["partition"] == "final_blind" and case["status"] == "draft" for case in cases),
        "adjudicated_final_supported": final_supported,
        "adjudicated_final_unsupported": final_unsupported,
        "final_ready": final_unsupported >= 299 and final_supported > 0,
    }


def load_cases(path: Path, partition: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    cases = [case for case in manifest["cases"] if case["status"] == "adjudicated"]
    if partition:
        cases = [case for case in cases if case["partition"] == partition]
    return manifest, cases


def evaluate_predictions(cases: list[dict[str, Any]], predictions: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    if len(cases) != len(predictions):
        raise ValueError("prediction count does not match case count")
    confusion = {gold: {predicted: 0 for predicted in (*LABELS, "unavailable")} for gold in LABELS}
    accepted_supported = accepted_unsupported = accepted_critical = 0
    supported = unsupported = critical_unsupported = 0
    nll = brier = 0.0
    ece_rows: list[tuple[float, int]] = []
    category_rows: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    normalized_predictions: list[dict[str, Any]] = []
    for case, prediction in zip(cases, predictions):
        gold = case["annotation"]["label"]
        scores = prediction.get("scores")
        predicted = prediction.get("predicted", "unavailable")
        if predicted not in (*LABELS, "unavailable"):
            raise ValueError(f"{case['id']}: invalid predicted label")
        entailment = scores.get("entailment", 0.0) if scores else 0.0
        accepted = predicted == "entailment" and entailment >= threshold
        confusion[gold][predicted] += 1
        is_supported = gold == "entailment"
        if is_supported:
            supported += 1
            accepted_supported += accepted
        else:
            unsupported += 1
            accepted_unsupported += accepted
            if case["critical"]:
                critical_unsupported += 1
                accepted_critical += accepted
        for category in case["categories"]:
            category_rows[category].append((is_supported, accepted))
        if scores:
            gold_score = max(scores[gold], 1e-15)
            nll -= math.log(gold_score)
            brier += sum((scores[label] - (label == gold)) ** 2 for label in LABELS)
            confidence = max(scores.values())
            ece_rows.append((confidence, int(predicted == gold)))
        normalized_predictions.append({**prediction, "accepted": accepted, "gold": gold, "id": case["id"]})

    class_metrics = {}
    for label in LABELS:
        tp = confusion[label][label]
        fp = sum(confusion[gold][label] for gold in LABELS if gold != label)
        fn = sum(confusion[label][predicted] for predicted in (*LABELS, "unavailable") if predicted != label)
        class_metrics[label] = {
            "precision": round(tp / (tp + fp), 6) if tp + fp else 0.0,
            "recall": round(tp / (tp + fn), 6) if tp + fn else 0.0,
        }
    ece = 0.0
    for lower in (index / 10 for index in range(10)):
        bucket = [(confidence, correct) for confidence, correct in ece_rows if lower <= confidence < lower + 0.1 or (lower == 0.9 and confidence == 1)]
        if bucket:
            ece += len(bucket) / len(ece_rows) * abs(sum(row[0] for row in bucket) / len(bucket) - sum(row[1] for row in bucket) / len(bucket))
    per_category = {}
    for category, rows in sorted(category_rows.items()):
        category_supported = sum(row[0] for row in rows)
        category_unsupported = len(rows) - category_supported
        per_category[category] = {
            "cases": len(rows),
            "false_support_rate": round(sum(accepted for is_supported, accepted in rows if not is_supported) / category_unsupported, 6) if category_unsupported else None,
            "supported_retention": round(sum(accepted for is_supported, accepted in rows if is_supported) / category_supported, 6) if category_supported else None,
        }
    scored = len(ece_rows)
    return {
        "threshold": threshold,
        "rates": {
            "false_support_rate": round(accepted_unsupported / unsupported, 6) if unsupported else 0.0,
            "critical_false_support_rate": round(accepted_critical / critical_unsupported, 6) if critical_unsupported else 0.0,
            "supported_claim_retention": round(accepted_supported / supported, 6) if supported else 0.0,
        },
        "totals": {
            "supported": supported, "unsupported": unsupported,
            "critical_unsupported": critical_unsupported,
            "accepted_supported": accepted_supported,
            "accepted_unsupported": accepted_unsupported,
            "accepted_critical_unsupported": accepted_critical,
            "scored": scored,
        },
        "confusion_matrix": confusion,
        "per_class": class_metrics,
        "uncalibrated_scores": {
            "warning": "Raw softmax scores are not calibrated probabilities.",
            "nll": round(nll / scored, 6) if scored else None,
            "brier": round(brier / scored, 6) if scored else None,
            "ece_10_bin": round(ece, 6) if scored else None,
        },
        "per_category": per_category,
        "predictions": normalized_predictions,
    }


def threshold_sweep(cases: list[dict[str, Any]], predictions: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    rows = []
    for threshold in thresholds:
        report = evaluate_predictions(cases, predictions, threshold)
        accepted = report["predictions"]
        rows.append({
            "threshold": threshold,
            **report["rates"],
            "unsupported_passes": [row["id"] for row in accepted if row["accepted"] and row["gold"] != "entailment"],
            "critical_unsupported_passes": [row["id"] for row in accepted if row["accepted"] and row["gold"] != "entailment" and next(case for case in cases if case["id"] == row["id"])["critical"]],
        })
    return rows


def select_threshold(rows: list[dict[str, Any]], max_false_support_rate: float = 0.0) -> dict[str, Any]:
    eligible = [row for row in rows if row["false_support_rate"] <= max_false_support_rate]
    if not eligible:
        raise ValueError("no threshold satisfies the false-support constraint")
    return max(eligible, key=lambda row: (row["supported_claim_retention"], row["threshold"]))


def zero_failure_upper_bound(total: int, confidence: float = 0.95) -> float:
    if total <= 0:
        raise ValueError("total must be positive")
    return 1 - (1 - confidence) ** (1 / total)


def self_check() -> None:
    cases = [
        {"id": "s", "annotation": {"label": "entailment"}, "critical": False, "categories": ["exact_support"]},
        {"id": "u", "annotation": {"label": "neutral"}, "critical": True, "categories": ["neutral_related"]},
    ]
    predictions = [
        {"predicted": "entailment", "scores": {"entailment": .9, "neutral": .05, "contradiction": .05}},
        {"predicted": "neutral", "scores": {"entailment": .1, "neutral": .8, "contradiction": .1}},
    ]
    report = evaluate_predictions(cases, predictions, .9)
    assert report["rates"] == {"false_support_rate": 0.0, "critical_false_support_rate": 0.0, "supported_claim_retention": 1.0}
    assert round(zero_failure_upper_bound(299), 6) < .01
    assert round(zero_failure_upper_bound(298), 6) > .01


if __name__ == "__main__":
    self_check()
