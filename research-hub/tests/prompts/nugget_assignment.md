# Nugget assignment prompt (HUB-047)

Run once per (report, nugget) pair when scoring. This is a matching task over
text supplied in the prompt, so it runs on the **local** model and costs no
metered judge calls — that is a design constraint, not an optimization: an
evaluation that spends metered budget per run cannot be run often enough to be
useful.

Assignment must be calibrated against blind human labels before any score
computed from it is quoted. Lexical matching is not sufficient — it silently
misses paraphrased-but-correct coverage — which is why this is a model and why
its agreement has to be measured.

---

```text
You are given a REPORT and one FACT. Decide whether the report states that
fact. Judge only against the report text; do not use outside knowledge, and do
not reward a report for being about the right topic.

Answer with one label:
  "supported" — the report states this fact, in any wording.
  "partial"   — the report gestures at it but omits what makes it the fact
                (e.g. names the mechanism but not the condition or value).
  "absent"    — the report does not state it.

Output JSON only: {"label": "...", "evidence": "<quote from the report, or null>"}
```

---

## Scoring

Per question:

- `vital_nugget_recall` = vital nuggets labelled `supported` / all vital
  nuggets. **The primary number.**
- `weighted_nugget_recall` = vital counted double, okay counted once.
- `all_nugget_recall` = unweighted over every nugget.
- `partial_rate` = reported separately. `partial` counts as **zero** in every
  recall figure; crediting half a fact would let a report that gestures at
  everything beat one that establishes anything.

A nugget counts as `supported` only when the report text carrying it belongs to
a claim that **passed the claim gate**. Otherwise completeness could be bought
with unverified assertions, which is the failure the gate exists to prevent.

Report these beside `citation_validity` and never blended into it. The two axes
trade against each other, and a single blended score hides the trade a change
needs to be judged on.

## What this instrument may be used for

Ranking **configurations** — is `EVIDENCE_PACKING=marginal_gain` better than
`rank`, does late chunking help, does a knowledge graph earn its keep.

Not for judging a single report. Nugget scoring tracks human judgment at the
system level and is noisy per topic (arXiv:2504.15068, arXiv:2509.26184), so a
per-report number from it is noise and must never gate publication.
