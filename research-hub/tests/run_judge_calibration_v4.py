"""Calibration run of the MiniMax judge gate on the labels-by-design set (HUB-036).

Judges every case in `judge_calibration_v4.json` through the real
`JudgeClaimVerifier` (metered Token Plan calls), checkpointing each verdict so
a quota interruption resumes without re-judging completed cases. Calibration
may be repeated after prompt/schema changes — pass ``--fresh`` to discard the
checkpoint — but the sealed blind final never may (see run_judge_final_v4.py).

    python -m tests.run_judge_calibration_v4 [--fresh]

Environment: MINIMAX_SUBSCRIPTION_KEY (required; never printed),
JUDGE_BASE_URL, JUDGE_MODEL, JUDGE_TIMEOUT_SECONDS (defaults as in app.config).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.claim_support import VerifierUnavailable  # noqa: E402
from app.judge_gate import JudgeClaimVerifier  # noqa: E402
from judge_seal_v4 import judge_config_fingerprint, wire_claim  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CASES = FIXTURES / "judge_calibration_v4.json"
CHECKPOINT = FIXTURES / "judge_calibration_checkpoint_v4.json"
RESULTS = FIXTURES / "judge_calibration_results_v4.json"


async def judge_cases(cases: list[dict], verdicts: dict[str, dict]) -> tuple[int, list[str]]:
    gate = JudgeClaimVerifier(
        base_url=os.environ.get("JUDGE_BASE_URL", "https://api.minimax.io/v1"),
        api_key=os.environ["MINIMAX_SUBSCRIPTION_KEY"],
        model=os.environ.get("JUDGE_MODEL", "MiniMax-M3"),
        timeout=float(os.environ.get("JUDGE_TIMEOUT_SECONDS", "60")),
    )
    errors: list[str] = []
    calls = 0
    try:
        for case in cases:
            if case["id"] in verdicts:
                continue
            try:
                verdict = (await gate.verify_detailed([wire_claim(case)]))[0]
                calls += 1
            except VerifierUnavailable as exc:
                if exc.reason == "quota_exhausted":
                    print(f"QUOTA at {case['id']} after {calls} calls; checkpoint kept — rerun to resume.")
                    return 3, errors
                errors.append(f"{case['id']}: {exc.reason}")
                continue
            verdicts[case["id"]] = verdict
            CHECKPOINT.write_text(
                json.dumps(verdicts, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        await gate.close()
        print(f"judge calls this run: {calls}")
    return 0, errors


def summarize(cases: list[dict], verdicts: dict[str, dict]) -> dict:
    per_kind: dict[str, Counter] = {}
    mismatches = []
    for case in cases:
        verdict = verdicts[case["id"]]
        stats = per_kind.setdefault(case["kind"], Counter())
        stats["total"] += 1
        actual = "accept" if verdict["accepted"] else "reject"
        expected_reasons = case.get("expected_reasons")
        ok = actual == case["expected"] and (
            not expected_reasons or verdict["reason"] in expected_reasons)
        stats["as_designed" if ok else "off_design"] += 1
        if not verdict["accepted"]:
            stats[f'reason:{verdict["reason"]}'] += 1
        if not ok:
            mismatches.append({
                "id": case["id"], "kind": case["kind"], "expected": case["expected"],
                "expected_reasons": expected_reasons,
                "actual": actual, "reason": verdict["reason"], "notes": case["notes"],
            })
    return {
        "per_kind": {kind: dict(stats) for kind, stats in sorted(per_kind.items())},
        "off_design": mismatches,
        "served_models": sorted({v["served_model"] for v in verdicts.values() if v.get("served_model")}),
    }


def main() -> int:
    if "--fresh" in sys.argv:
        CHECKPOINT.unlink(missing_ok=True)
        RESULTS.unlink(missing_ok=True)
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    verdicts: dict[str, dict] = {}
    if CHECKPOINT.exists():
        verdicts = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
        print(f"resuming: {len(verdicts)}/{len(cases)} already judged")
    code, errors = asyncio.run(judge_cases(cases, verdicts))
    if code:
        return code
    if errors and "--accept-fail-closed" in sys.argv:
        # A case that errors on every retry is a measured outcome: the gate
        # fails closed, so the claim is never accepted. Record it as such.
        for entry in errors:
            case_id, reason = entry.split(": ", 1)
            verdicts[case_id] = {
                "accepted": False, "reason": f"fail_closed_{reason}",
                "served_model": None, "refs": None, "fail_closed": True,
            }
        errors = []
    if errors:
        print("transient errors (rerun to retry):", *errors, sep="\n  ")
        return 2
    summary = summarize(cases, verdicts)
    results = {
        "schema_version": "judge_calibration_v4",
        "judge": {
            **judge_config_fingerprint(),
            "requested_model": os.environ.get("JUDGE_MODEL", "MiniMax-M3"),
        },
        "summary": summary,
        "cases": [{
            "id": case["id"], "kind": case["kind"], "label": case["label"],
            "expected": case["expected"], **verdicts[case["id"]],
        } for case in cases],
    }
    RESULTS.write_text(
        json.dumps(results, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("results:", RESULTS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
