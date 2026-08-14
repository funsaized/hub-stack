"""Verify every HUB-047 nugget span exists verbatim in the corpus.

This is the mechanical half of the grounding rule. An annotator asked for facts
about a subject will, given the chance, write down what it knows about that
subject rather than what these documents say -- and LLM judges have been shown
to fall back on memorized knowledge even when instructed to obey a supplied
reference, with prompt-level instructions failing to fix it (arXiv:2601.07506).
A fabricated fact tends to arrive with a fabricated quote attached, so
requiring the span to be findable in the named document turns that failure into
an error instead of a silent distortion of the metric.

It also enforces the rule the whole design rests on: spans come from documents
resolved by scope. This module deliberately does not import
`app.retrieval` -- a nugget set built from what retrieval surfaced would only
ever reward retrieval for surfacing it, which is how a published study
mechanically produced perfect recall for its own configuration
(arXiv:2608.03860). `tests/test_answer_eval_nuggets.py` asserts that absence.

Read-only: opens the document store through an immutable URI and writes
nothing. Exit code 1 means a span failed to verify.
"""

import argparse
import json
import os
import sqlite3
import unicodedata
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"
NUGGETS = FIXTURES / "answer_eval_nuggets.json"
QUESTIONS = FIXTURES / "answer_eval_questions.json"


def normalise(text: str) -> str:
    """Fold the differences that survive copying but change no meaning.

    Curly quotes, non-breaking spaces and the various dashes are the common
    way a correct span fails a literal comparison. Normalising them is not a
    loosening of the grounding rule -- the span must still be present, word
    for word, in the named document.
    """
    folded = unicodedata.normalize("NFKC", text)
    for source, target in (
        ("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
        ("–", "-"), ("—", "-"), (" ", " "),
    ):
        folded = folded.replace(source, target)
    return " ".join(folded.split())


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(store_path: str) -> tuple[list[dict], list[dict]]:
    nuggets = load(NUGGETS)
    questions = {q["id"]: q for q in load(QUESTIONS)["questions"]}
    db = sqlite3.connect(f"file:{store_path}?immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    cache: dict[str, str | None] = {}

    def markdown(document_id: str) -> str | None:
        if document_id not in cache:
            row = db.execute(
                "SELECT markdown FROM documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            cache[document_id] = normalise(row["markdown"] or "") if row else None
        return cache[document_id]

    checked, failures = [], []
    try:
        for entry in nuggets["questions"]:
            question_id = entry["question_id"]
            if question_id not in questions:
                failures.append({
                    "question_id": question_id, "nugget_id": None,
                    "reason": "question id is not in the question set",
                })
                continue
            for nugget in entry["nuggets"]:
                document_id = nugget["source_document_id"]
                text = markdown(document_id)
                record = {
                    "question_id": question_id, "nugget_id": nugget["id"],
                    "document_id": document_id,
                }
                if text is None:
                    failures.append({**record, "reason": "document not in corpus"})
                    continue
                if normalise(nugget["source_span"]) not in text:
                    failures.append({**record, "reason": "span not found verbatim"})
                    continue
                checked.append(record)
    finally:
        db.close()
    return checked, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--store",
        default=os.environ.get("DOCUMENT_STORE_PATH", "/app/data/documents.sqlite3"),
    )
    args = parser.parse_args()
    checked, failures = validate(args.store)
    print(json.dumps({
        "spans_verified": len(checked),
        "failures": failures,
        "passed": not failures,
    }, indent=2, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
