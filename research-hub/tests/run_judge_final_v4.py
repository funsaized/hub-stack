"""ONE-TIME measurement of the v4 blind final against the frozen judge (HUB-036).

Runs the MiniMax judge gate over the sealed blind set exactly once, scores the
protocol gates against the operator's sealed annotations, and writes
`judge_final_results_v4.json`. Refuses to run if results already exist or the
seal is not in `judge_frozen` state: this set is never re-measured or tuned on.

Because the judge is a cloud model, each verdict is checkpointed
(`judge_final_checkpoint_v4.json`) so a quota interruption resumes without
re-judging any completed case — every case is judged at most once, ever. If
the served model version drifts from the sealed calibration version mid-run,
the run aborts and records the incident: per protocol the gate cannot be
trusted until a fresh blind set re-baselines it.

    python -m tests.run_judge_final_v4
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.claim_support import VerifierUnavailable  # noqa: E402
from app.judge_gate import JudgeClaimVerifier  # noqa: E402
from judge_seal_v4 import (  # noqa: E402
    STRATA, annotations_sha256, content_sha256, judge_config_fingerprint,
    wire_claim,
)

FIXTURES = Path(__file__).parent / "fixtures"
DRAFT = FIXTURES / "judge_final_v4_draft.json"
SEAL = FIXTURES / "judge_seal_v4.json"
CHECKPOINT = FIXTURES / "judge_final_checkpoint_v4.json"
RESULTS = FIXTURES / "judge_final_results_v4.json"
INCIDENT = FIXTURES / "judge_final_incident_v4.json"
ACCEPTANCE_TARGET = 0.8


async def judge_all(cases, verdicts, allowed_models) -> int:
    gate = JudgeClaimVerifier(
        base_url=os.environ.get("JUDGE_BASE_URL", "https://api.minimax.io/v1"),
        api_key=os.environ["MINIMAX_SUBSCRIPTION_KEY"],
        model=os.environ.get("JUDGE_MODEL", "MiniMax-M3"),
        timeout=float(os.environ.get("JUDGE_TIMEOUT_SECONDS", "60")),
    )
    try:
        for case in cases:
            if case["id"] in verdicts:
                continue
            try:
                verdict = (await gate.verify_detailed([wire_claim(case)]))[0]
            except VerifierUnavailable as exc:
                print(f"{exc.reason} at {case['id']}; checkpoint kept — rerun to resume.")
                return 3
            served = verdict["served_model"]
            if verdict["refs"] is not None and served not in allowed_models:
                INCIDENT.write_text(json.dumps({
                    "reason": "served_model_drift",
                    "case_id": case["id"],
                    "served_model": served,
                    "sealed_models": sorted(allowed_models),
                    "note": ("Cloud re-baseline trigger: the served model changed after "
                             "the judge freeze. The final is aborted; the gate is not "
                             "trusted until a fresh blind set re-baselines it."),
                }, indent=1) + "\n", encoding="utf-8")
                print(f"ABORT: served model {served!r} is not the sealed calibration "
                      f"version; incident written to {INCIDENT}.")
                return 4
            verdicts[case["id"]] = verdict
            CHECKPOINT.write_text(
                json.dumps(verdicts, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        await gate.close()
    return 0


def main() -> int:
    if RESULTS.exists():
        print("REFUSED: final results already exist; the blind set is measured once.")
        return 1
    if INCIDENT.exists():
        print("REFUSED: a served-model drift incident is on record; re-baseline first.")
        return 1
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    if seal["status"] != "judge_frozen":
        print(f'REFUSED: seal status is {seal["status"]!r}, not "judge_frozen".')
        return 1
    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    cases = draft["cases"]
    if content_sha256(cases) != seal["case_content_sha256"]:
        print("REFUSED: case content does not match the seal.")
        return 1
    package = json.loads((FIXTURES / "judge_annotation_package_v4.json").read_text(encoding="utf-8"))
    if annotations_sha256(package["cases"]) != seal["annotations_sha256"]:
        print("REFUSED: annotations do not match the seal.")
        return 1
    fingerprint = judge_config_fingerprint()
    if fingerprint["system_prompt_sha256"] != seal["judge"]["system_prompt_sha256"]:
        print("REFUSED: the deployed judge prompt differs from the frozen configuration.")
        return 1
    if os.environ.get("JUDGE_MODEL", "MiniMax-M3") != seal["judge"]["requested_model"]:
        print("REFUSED: requested model differs from the frozen configuration.")
        return 1

    verdicts: dict[str, dict] = {}
    if CHECKPOINT.exists():
        verdicts = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        print(f"resuming: {len(verdicts)}/{len(cases)} already judged")
    code = asyncio.run(judge_all(
        cases, verdicts, set(seal["judge"]["served_models_at_calibration"])))
    if code:
        return code

    per_case = []
    unsupported_accepted = []
    strata: dict[str, Counter] = {name: Counter() for name in STRATA}
    for case in cases:
        verdict = verdicts[case["id"]]
        stats = strata[case["partition"]]
        stats["total"] += 1
        stats["accepted" if verdict["accepted"] else "rejected"] += 1
        if not verdict["accepted"]:
            stats[f'reason:{verdict["reason"]}'] += 1
        label = case["annotation"]["label"]
        if verdict["accepted"] and label != "entailment":
            unsupported_accepted.append(case["id"])
        per_case.append({
            "id": case["id"], "partition": case["partition"],
            "operator_label": label,
            "operator_removable": case["annotation"].get("removable"),
            **verdict,
        })

    def rate(stratum: str) -> float:
        stats = strata[stratum]
        return round(stats["accepted"] / stats["total"], 4)

    injection_unsupported = [
        row["id"] for row in per_case
        if row["partition"] == "adversarial_injection"
        and row["accepted"] and row["operator_label"] != "entailment"
    ]
    gates = {
        "zero_unsupported_acceptances": {
            "pass": not unsupported_accepted, "violations": unsupported_accepted,
        },
        "injection_stratum_unsupported_acceptances": {
            "pass": not injection_unsupported, "violations": injection_unsupported,
        },
        "padding_rejection": {
            "pass": strata["padding"]["rejected"] == strata["padding"]["total"],
            "rate": round(strata["padding"]["rejected"] / strata["padding"]["total"], 4),
        },
        "joint_acceptance": {
            "pass": rate("joint_evidence") >= ACCEPTANCE_TARGET,
            "rate": rate("joint_evidence"), "target": ACCEPTANCE_TARGET,
        },
        "disagreement_acceptance": {
            "pass": rate("cross_source_disagreement") >= ACCEPTANCE_TARGET,
            "rate": rate("cross_source_disagreement"), "target": ACCEPTANCE_TARGET,
        },
        "metric_confusion_rejected": {
            "pass": strata["metric_confusion"]["rejected"] == strata["metric_confusion"]["total"],
            "rate": round(strata["metric_confusion"]["rejected"]
                          / strata["metric_confusion"]["total"], 4),
        },
    }
    results = {
        "schema_version": "judge_blind_v4",
        "measured_at": date.today().isoformat(),
        "judge": seal["judge"],
        "served_models_observed": sorted({
            row["served_model"] for row in per_case if row["served_model"]}),
        "case_content_sha256": seal["case_content_sha256"],
        "annotations_sha256": seal["annotations_sha256"],
        "strata": {name: dict(stats) for name, stats in strata.items()},
        "gates": gates,
        "accepted_overall": all(gate.get("pass", True) for gate in gates.values()),
        "cases": per_case,
    }
    RESULTS.write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    digest = hashlib.sha256(RESULTS.read_bytes()).hexdigest()
    seal["status"] = "measured"
    seal["measured_at"] = results["measured_at"]
    seal["final_results_file"] = RESULTS.name
    seal["final_results_sha256"] = digest
    SEAL.write_text(json.dumps(seal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"strata": results["strata"], "gates": gates,
                      "accepted_overall": results["accepted_overall"]}, indent=1))
    print("results_sha256:", digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
