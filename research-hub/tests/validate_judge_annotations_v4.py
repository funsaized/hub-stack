"""Progress/completeness validator for the operator's v4 blind annotations.

Run at any time while annotating `judge_annotation_package_v4.json`; the
operator fills each case's `annotation` per `CLAIM_SUPPORT_ANNOTATION.md`:

    {"label": "entailment|neutral|contradiction",
     "removable": ["yes"|"no", ...]        # one per span, 2-span cases only
     "rationale": "<one sentence>",
     "supporting": ["<exact substring of a span>", ...],
     "refuting": ["<exact substring of a span>", ...]}

Only annotation shape is validated — never against the judge or any labels —
so running this cannot unblind anything.

    python -m tests.validate_judge_annotations_v4
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LABELS = {"entailment", "neutral", "contradiction"}
PACKAGE = Path(__file__).parent / "fixtures" / "judge_annotation_package_v4.json"


def main() -> int:
    package = json.loads(PACKAGE.read_text(encoding="utf-8"))
    pending, problems = [], []
    for case in package["cases"]:
        annotation = case.get("annotation")
        if not annotation:
            pending.append(case["id"])
            continue
        spans = case["spans"]

        def bad(reason: str) -> None:
            problems.append(f'{case["id"]}: {reason}')

        if annotation.get("label") not in LABELS:
            bad("label must be entailment, neutral, or contradiction")
        if not str(annotation.get("rationale", "")).strip():
            bad("missing rationale")
        if len(spans) > 1:
            removable = annotation.get("removable")
            if (not isinstance(removable, list) or len(removable) != len(spans)
                    or any(value not in {"yes", "no"} for value in removable)):
                bad('removable must list "yes"/"no" once per span')
        for kind in ("supporting", "refuting"):
            quotes = annotation.get(kind)
            if not isinstance(quotes, list):
                bad(f"{kind} must be a list (empty is fine)")
                continue
            for quote in quotes:
                if not isinstance(quote, str) or not any(quote in span for span in spans):
                    bad(f"{kind} quote is not an exact substring of any span: {quote!r}")
        unknown = set(annotation) - {"label", "removable", "rationale", "supporting", "refuting"}
        if unknown:
            bad(f"unknown annotation fields: {sorted(unknown)}")

    total = len(package["cases"])
    print(f"annotated: {total - len(pending)}/{total}")
    if pending:
        print("pending:", ", ".join(pending[:15]), "..." if len(pending) > 15 else "")
    if problems:
        print("problems:")
        for problem in problems:
            print("  " + problem)
        return 1
    if pending:
        return 2
    print("all annotations complete and well-formed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
