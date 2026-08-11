"""Validate claim-support fixtures, annotations, partitions, and legacy audit invariants."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

try:
    from .build_claim_support_final_draft import build
    from .claim_support_eval import case_fingerprint, normalize, validate_manifest
except ImportError:
    from build_claim_support_final_draft import build
    from claim_support_eval import case_fingerprint, normalize, validate_manifest


ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures"
SPIRIT_EVIDENCE = [
    "SPIRIT-AI and CONSORT-AI Working Group Reporting guidelines for clinical trial reports for interventions involving artificial intelligence: the CONSORT-AI Extension.",
    "Investigators seeking to report studies developing and validating the diagnostic and predictive properties of AI models should refer to TRIPOD-ML and STARD-AI, both of which are currently under development.",
]
SPIRIT_CLAIM = "SPIRIT-AI works with TRIPOD-ML before full clinical trials."


def audit_legacy(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    if len(cases) != 28:
        raise ValueError("legacy fixture must retain exactly 28 cases")
    ids = [case["id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("legacy fixture contains duplicate IDs")
    pairs = [(normalize(case["claim"]), tuple(normalize(item) for item in case["evidence"])) for case in cases]
    if len(pairs) != len(set(pairs)):
        raise ValueError("legacy fixture contains duplicate premise/claim pairs")
    labels = Counter(case["gold"] for case in cases)
    if labels != {"entailment": 10, "contradiction": 5, "neutral": 13}:
        raise ValueError(f"unexpected audited label distribution: {labels}")
    spirit = next(case for case in cases if case["id"] == "live-spirit-tripod-unsupported-relation")
    if spirit["claim"] != SPIRIT_CLAIM or spirit["evidence"] != SPIRIT_EVIDENCE or spirit["gold"] != "neutral" or not spirit["critical"]:
        raise ValueError("critical SPIRIT-AI/TRIPOD-ML regression changed")
    return {
        "cases": len(cases), "labels": dict(labels), "duplicates": 0,
        "corrections": [
            "supported-clinical-abbreviation: expanded Cr in evidence to remove outside-knowledge dependence",
            "neutral-quantifier-overreach: neutral -> contradiction because exactly three of four excludes all four",
        ],
        "annotation_sensitive": [
            "supported-two-sentence-single-source: relies on explicitly permitted arithmetic",
            "supported-adjacent-fragments: relies on resolving the repeated endpoint phrase across evidence items",
        ],
        "critical_regression_preserved": True,
    }


def main() -> int:
    schema = json.loads((FIXTURES / "claim_support_corpus_v2.schema.json").read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or schema.get("$id") is None:
        raise ValueError("invalid versioned JSON schema declaration")
    legacy = audit_legacy(FIXTURES / "claim_support_cases.json")
    calibration = json.loads((FIXTURES / "claim_support_calibration_v2.json").read_text(encoding="utf-8"))
    final_draft_path = FIXTURES / "claim_support_final_blind_draft_v2.json"
    final_draft = json.loads(final_draft_path.read_text(encoding="utf-8"))
    final_path = FIXTURES / "claim_support_final_v2.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    summaries = [
        validate_manifest(calibration),
        validate_manifest(final_draft),
        validate_manifest(final, require_final_ready=True),
    ]
    if final_draft != build():
        raise ValueError("final draft is not reproducible from the checked-in generator")
    if summaries[1]["draft_final_cases"] != 405:
        raise ValueError("final draft must contain 405 cases")
    if [(case["claim"], case["evidence"]) for case in final["cases"]] != [
        (case["claim"], case["evidence"]) for case in final_draft["cases"]
    ]:
        raise ValueError("frozen final claim/evidence content differs from the blind draft")
    seal = json.loads((FIXTURES / "claim_support_final_seal_v2.json").read_text(encoding="utf-8"))
    final_hash = hashlib.sha256(final_path.read_bytes()).hexdigest()
    labels = Counter(case["annotation"]["label"] for case in final["cases"])
    if (
        final.get("status") != "frozen"
        or seal.get("status") != "frozen"
        or seal.get("corpus_sha256") != final_hash
        or seal.get("corpus_id") != final.get("corpus_id")
        or seal.get("composition", {}).get("labels") != dict(labels)
        or seal.get("composition", {}).get("unsupported") != 325
        or seal.get("agreement", {}).get("disagreements") != 13
    ):
        raise ValueError("frozen final seal does not match the adjudicated corpus")
    threshold = json.loads((FIXTURES / "claim_support_threshold_v2.json").read_text(encoding="utf-8"))
    result = json.loads((FIXTURES / "claim_support_final_results_v2.json").read_text(encoding="utf-8"))
    result_totals = result.get("metrics", {}).get("totals", {})
    if (
        result.get("corpus") != {"id": final["corpus_id"], "partition": "final_blind", "cases": 405}
        or result.get("model", {}).get("id") != threshold.get("model")
        or result.get("model", {}).get("revision") != threshold.get("revision")
        or result.get("model", {}).get("offline_local_files_only") is not True
        or result.get("model", {}).get("startup_succeeded") is not True
        or result.get("policy", {}).get("threshold") != 0.97
        or result_totals.get("supported") != 80
        or result_totals.get("unsupported") != 325
        or result_totals.get("accepted_supported") != 79
        or result_totals.get("accepted_unsupported") != 0
        or result_totals.get("accepted_critical_unsupported") != 0
        or len(result.get("predictions", [])) != 405
        or result.get("determinism", {}).get("exact_scores_repeated") is not True
        or result.get("budget", {}).get("silent_truncation") is not False
        or result.get("budget", {}).get("over_budget_ids") != []
    ):
        raise ValueError("final result does not match the frozen corpus/model/threshold")
    calibration_hash = hashlib.sha256((FIXTURES / "claim_support_calibration_v2.json").read_bytes()).hexdigest()
    if threshold.get("status") != "frozen" or threshold.get("threshold") != 0.97 or threshold.get("calibration_sha256") != calibration_hash:
        raise ValueError("frozen threshold does not match calibration corpus")
    fingerprints: dict[str, str] = {}
    for manifest in (calibration, final_draft):
        for case in manifest["cases"]:
            fingerprint = case_fingerprint(case)
            if fingerprint in fingerprints and fingerprints[fingerprint] != case["partition"]:
                raise ValueError(f"partition leakage: {case['id']}")
            fingerprints[fingerprint] = case["partition"]
    print(json.dumps({
        "legacy_audit": legacy,
        "v2": summaries,
        "final_review": {
            "sha256": final_hash,
            "labels": dict(labels),
            "independently_reviewed": 405,
            "reviewer_agreements": seal["agreement"]["agreements"],
            "reviewer_disagreements": seal["agreement"]["disagreements"],
            "result_sha256": hashlib.sha256((FIXTURES / "claim_support_final_results_v2.json").read_bytes()).hexdigest(),
        },
        "frozen_threshold": {"value": threshold["threshold"], "calibration_sha256": calibration_hash},
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
