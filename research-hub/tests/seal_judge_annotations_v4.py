"""Seal the operator's v4 blind annotations and freeze the judge configuration.

Run AFTER the operator finishes annotating `judge_annotation_package_v4.json`
(validate first with `python -m tests.validate_judge_annotations_v4`). This:

1. verifies the package still matches the sealed case content,
2. copies each annotation into the draft fixture,
3. seals `annotations_sha256`,
4. freezes the judge configuration (system-prompt SHA-256, requested model,
   temperature) plus the served model versions observed at calibration —
   the cloud re-baseline reference — and sets the seal to `judge_frozen`.

After this, the ONE-TIME final is armed: python -m tests.run_judge_final_v4

    python -m tests.seal_judge_annotations_v4
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from judge_seal_v4 import (  # noqa: E402
    annotations_sha256, content_sha256, judge_config_fingerprint,
)

FIXTURES = Path(__file__).parent / "fixtures"
DRAFT = FIXTURES / "judge_final_v4_draft.json"
PACKAGE = FIXTURES / "judge_annotation_package_v4.json"
SEAL = FIXTURES / "judge_seal_v4.json"
CALIBRATION_RESULTS = FIXTURES / "judge_calibration_results_v4.json"


def main() -> int:
    check = subprocess.run(
        [sys.executable, "-m", "tests.validate_judge_annotations_v4"],
        cwd=Path(__file__).parent.parent)
    if check.returncode != 0:
        print("REFUSED: annotations incomplete or malformed (see validator output).")
        return 1
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    if seal["status"] != "draft_frozen":
        print(f'REFUSED: seal status is {seal["status"]!r}, not "draft_frozen".')
        return 1
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    package_content = [
        {"id": c["id"], "claim": c["claim"],
         "evidence": [{"span": span} for span in c["spans"]]}
        for c in package["cases"]
    ]
    if content_sha256(package_content) != seal["case_content_sha256"]:
        print("REFUSED: package case content was edited after the freeze.")
        return 1

    draft = json.loads(DRAFT.read_text(encoding="utf-8"))
    annotations = {c["id"]: c["annotation"] for c in package["cases"]}
    for case in draft["cases"]:
        case["annotation"] = annotations[case["id"]]
        case["status"] = "annotated"
    draft["status"] = "annotated_sealed"

    calibration = json.loads(CALIBRATION_RESULTS.read_text(encoding="utf-8"))
    seal.update({
        "status": "judge_frozen",
        "annotated_at": date.today().isoformat(),
        "annotations_sha256": annotations_sha256(package["cases"]),
        "judge_frozen_at": date.today().isoformat(),
        "judge": {
            **judge_config_fingerprint(),
            "requested_model": os.environ.get("JUDGE_MODEL", "MiniMax-M3"),
            "served_models_at_calibration": calibration["summary"]["served_models"],
            "note": ("MiniMax reports served model without finer version granularity; "
                     "drift detection is limited to this string. Any change still "
                     "aborts the final and requires a fresh blind set."),
        },
        "calibration_results_file": CALIBRATION_RESULTS.name,
    })
    DRAFT.write_text(json.dumps(draft, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    SEAL.write_text(json.dumps(seal, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("annotations sealed:", seal["annotations_sha256"])
    print("judge frozen; the one-time final is armed: python -m tests.run_judge_final_v4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
