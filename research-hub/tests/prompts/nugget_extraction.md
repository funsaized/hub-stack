# Nugget extraction prompt (HUB-047)

Run once per question. Input is ONE question plus the full text of the
documents **resolved by scope** — `documents_for_job` for a job-scoped
question, `documents_matching` for a topic- or tag-filtered one. Never feed it
retrieval output and never feed it a generated report: the pipeline under test
must not choose the yardstick it is measured against.

Output goes into `tests/fixtures/answer_eval_nuggets.json` and every
`source_span` is checked verbatim against the corpus by
`tests/validate_answer_eval_nuggets.py`.

Use an annotator from a different model family than the generator under test
(`qwen3.5:9b`). Costs no metered judge calls.

---

```text
You are building an evaluation reference for a research-report system.

CONTEXT
A system answers research questions by retrieving from a private corpus of
crawled documents and producing a long-form report of claims, each bound to a
source citation. Its claims are already verified against the sources it cites,
so accuracy is covered. What is NOT measured is completeness: a report that
states three true but trivial facts, and omits everything that mattered,
currently scores perfectly. Your job is to produce the reference that catches
that.

TASK
You are given ONE question and the FULL TEXT of the source documents available
to answer it. Produce the list of atomic facts that a good answer to this
question must or should contain — "nuggets". A later automated step will check
which nuggets a generated report actually contains.

RULES
1. Grounded. Every nugget must be supported by a verbatim span from the
   supplied documents. Quote that span exactly, character for character, and
   name the document it came from. Spans are checked mechanically against the
   corpus; a nugget whose span cannot be found is discarded. Prefer a short
   span with plain punctuation — curly quotes, em dashes and non-breaking
   spaces survive copying badly and are the most common reason a span fails
   verification.
2. Documents only. Do NOT add facts you know to be true from your own
   knowledge but that are not stated in the supplied documents — not even
   well-established ones, and not even if the documents are wrong. You are
   recording what this corpus supports, not what is true about the world. This
   is the single most important rule and the most common way this task fails.
3. Atomic. One fact per nugget. The test: could a report contain this nugget
   while missing the next one? If not, they are one nugget.
4. Answer-bearing, not topic-bearing. "Uses a stabilization window to avoid
   flapping" is a nugget. "Discusses flapping" is not — it names a subject
   rather than stating something.
5. Phrasing-independent. A report will use different words. State the fact, not
   a string to match.
6. Deduplicated. If two nuggets would be satisfied by the same sentence of a
   report, merge them.
7. Importance. Label each nugget:
   - "vital"  — a good answer to this question is incomplete without it.
   - "okay"   — genuinely worth having, but its absence does not make the
                answer wrong or incomplete.
   Be strict with "vital". If most nuggets are vital, the label carries no
   information. Aim for roughly 10-15 nuggets per question, of which 4-8 vital.

DO NOT
- Write nuggets that restate the question.
- Write meta-requirements ("should cite sources", "should be well organized").
  Only facts about the subject matter.
- Write the answer, summarize the documents, or grade anything.
- Infer a fact by combining documents unless the combination is itself stated.

OUTPUT
JSON only, no commentary:

{
  "question": "<the question, verbatim>",
  "nuggets": [
    {
      "id": "N1",
      "text": "<the fact, one sentence, phrasing-independent>",
      "importance": "vital" | "okay",
      "source_document_id": "<id of the document the span came from>",
      "source_span": "<verbatim quote from that document, <= 300 characters>"
    }
  ]
}
```

---

## After the run

1. `python -m tests.validate_answer_eval_nuggets` — every span must verify.
   A failure is a rule-2 signal as often as a copying slip: a model inventing a
   plausible fact tends to invent a plausible quote for it.
2. Hand-check ~25 nuggets across questions. That converts the annotator's error
   rate from an assumption into a number, at a fraction of annotating by hand.
3. Note in the fixture which documents in scope carried nothing — off-topic
   retention is a finding about acquisition, not a defect in the reference.
