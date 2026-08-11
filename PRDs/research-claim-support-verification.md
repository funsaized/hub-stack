# Claim-level support verification research spike

Status: final blind evaluation remains sealed and the minimal production verifier is deployed.
The one approved final live retry advanced the authoritative report to attempt 5 but failed
closed on malformed generation JSON before NLI. Phase 4 remains unaccepted; no further retry
or Phase 5 work is approved.

Date: 2026-08-11

## Decision summary

The current report gate is a provenance gate, not a semantic-support gate. A material claim
must not be persisted merely because its structured source IDs resolve to represented retained
evidence. The minimum safe target is:

1. request one claim per structured material item;
2. request exact internal evidence-span references for that claim;
3. deterministically resolve those references to packed evidence;
4. run a fail-closed local entailment verifier over the entire claim and the union of its cited
   spans;
5. render public Markdown citations only after verification; and
6. persist only claims that pass.

The original 28-case model-selection spike identified
`MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` but did not justify production. The expanded
calibration phase froze a stricter `0.97` operating threshold using 41 calibration cases: zero
of 27 unsupported cases were accepted and 13 of 14 supported cases were retained. The exact
SPIRIT-AI/TRIPOD-ML regression remained neutral and rejected. The 405-case final partition
has now received two isolated reviews, adjudication, validation, and hash sealing. Its
one-time pinned offline run accepted zero of 325 unsupported claims and 79 of 80 supported
claims. Stakeholders subsequently approved a `>=95%` retention floor, `<=40 ms` median CPU
latency per scored claim at batch eight, and `<=2.5 GiB` peak working set. The frozen final
result passes those gates at `98.75%`, `31.694 ms`, and `2,027.0 MiB`; the final partition and
result remain unchanged.

Prompt-only changes, structured IDs, atomization, and exact span presence are useful input
constraints, but none independently establishes entailment.

## Production implementation boundary

The implemented private path is:

`structured parse -> exact packed-span resolution -> pinned local NLI -> one complete-object correction -> complete re-verification -> retain passing claims -> render citations -> persist`

The API and worker share one CPU verifier sidecar. Its image contains only revision
`6f5cf0a2b59cabb106aca4c287eed12e357e90eb` of
`MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli`; runtime model access is offline/local-only.
The acceptance rule is fixed at argmax entailment and entailment score `>=0.97`, with batch
size eight and a 512-token maximum. Each exact span must entail its private atomic `supports`
proposition, the complete claim must entail each proposition, and the union of exact spans
must entail the complete claim. This rejects unrelated padding while permitting necessary
multi-span and multi-source support.

Neutral, contradiction, entailment below `0.97`, unresolved or malformed refs, malformed
verifier output, over-budget input, timeout, unavailable service, and revision mismatch all
fail closed. Inputs are tokenized with `truncation=False`; there is no citation-only fallback.
Only passing resolved refs produce public `[S#]` citations. A corrected object is resolved and
verified from scratch. Service-level verifier failures use the existing failed-report lifecycle,
which retains the previous Markdown and source registry on retry.

## Observed failure and boundary

The live generated claim stated that SPIRIT-AI works with TRIPOD-ML before full clinical
trials. Its cited represented evidence said, separately, that:

- SPIRIT-AI and CONSORT-AI concern AI clinical trials; and
- TRIPOD-ML and STARD-AI concern development and validation of diagnostic or predictive
  models.

Neither span, nor their union, states the asserted SPIRIT-AI/TRIPOD-ML relationship. The
claim is neutral with respect to the supplied evidence. It passed because
`research-hub/app/synthesis.py::_validate_claims()` checks shape, citation syntax, and the
represented-source allowlist, then immediately renders citations. It never compares claim
meaning with evidence meaning.

The correct integration boundary is after `_parse_json()` has produced structured material
items and before `_validate_claims()` converts them to Markdown strings. Verification must
not touch discovery, crawling, ingestion, retained Markdown, embeddings, Qdrant contents, or
the public report/source schemas.

## Claim-level support contract

### Definition

For material claim `C` and its cited exact evidence spans `E = {e1 ... en}`, claim-level
support means:

> Assuming only the supplied spans are true, plus ordinary linguistic and explicitly permitted
> arithmetic interpretation, the entire scoped claim must be true.

This is the same three-way decision shape used by NLI and scientific claim-verification work:
`entailment/support`, `contradiction/refute`, or `neutral/not enough information`. SciFact
requires both a support/refute decision and evidentiary rationales, rather than treating a
resolving citation as proof.[1] MedNLI likewise defines entailment, contradiction, and neutral
for premise-hypothesis pairs, but its premises are clinical-history sentences and its
annotation instructions allow medical knowledge and common sense; that differs from this
project's evidence-only policy.[2] ALCE's statement-level citation-entailment check similarly
concatenates all passages cited for a statement and asks whether the statement is supported
based solely on those passages.[11]

Operational rules:

- **Entailment:** eligible for persistence only when the verifier's argmax label is entailment
  and the entailment score meets an acceptance threshold calibrated on a separate set.
- **Contradiction:** reject the current claim.
- **Neutral:** reject the current claim. Related entities, plausible relationships, and
  bibliography titles are neutral unless the asserted proposition is stated or entailed.
- **Uncertain/low confidence:** reject. A low-confidence entailment is not support.
- **Outside knowledge:** prohibited even when medically or scientifically true.
- **Scope preservation:** quantities, comparators, polarity, modality, population, time,
  outcome, and causal language are part of the claim and must all be supported.

### Must one chunk entail the entire claim?

No. The union of the claim's exact cited spans may entail the claim. Requiring one chunk to
carry everything would wrongly reject genuine multi-sentence and multi-source findings.
Health fact-checking research explicitly treats combination of multiple and potentially
conflicting evidence as a separate problem.[3]

The safe rule is stricter than simple union membership:

- every span must be selected from the exact packed generation context;
- the verifier sees only the claim's cited spans, not all represented chunks;
- the complete union must entail the complete claim;
- every cited span must support a necessary claim component or explicit corroboration;
  unrelated citation padding is rejected;
- the cited set is bounded (proposed maximum: three spans and 512 verifier tokens); and
- source citations in public Markdown are derived from passing span references, not copied
  from untrusted model-provided source IDs.

If a required union exceeds the verifier budget, the claim is rejected rather than silently
truncated. Long-context models can be evaluated as a later retention optimization, but a
large context must not weaken the false-accept gate.

### Atomic claims and genuine multi-source claims

Generation should split compound prose into atomic claims. An atomic claim has one primary
predicate with fixed subject, scope, polarity, modality, comparator, quantity, outcome, and
time. A sentence joining an observed association to a causal conclusion, or joining one
supported clause to one unsupported clause, is not atomic.

Atomization is a retention and auditability rule, not the semantic safety guarantee. A model
can still emit an atomic but invented relationship, as the SPIRIT-AI/TRIPOD-ML failure shows.
Therefore:

- do not automatically split claims in the verifier;
- evaluate the whole emitted claim;
- reject a compound claim if any material clause lacks support; and
- permit genuine joint evidence when the union entails the full proposition, such as two
  cohort-specific spans supporting “the intervention reduced errors in both cohorts.”

## Deterministic evaluation set

Machine-readable fixtures:

- `research-hub/tests/fixtures/claim_support_cases.json`
- `research-hub/tests/fixtures/claim_support_long_context_cases.json`

Research runner:

- `research-hub/tests/benchmark_claim_support.py`

The primary set contains 28 non-PHI cases:

- 10 entailments;
- 4 contradictions;
- 14 neutral cases;
- 18 unsupported cases in the false-support denominator; and
- 14 unsupported cases marked critical.

It includes the exact SPIRIT-AI/TRIPOD-ML failure class plus direct support, numeric
paraphrase, negation in both directions, neutral related entities, causal and quantifier
overreach, wrong entity/number/status, fragmented evidence, bibliography-only evidence,
partially supported compounds, comparisons, single-source multi-sentence inference, and
joint multi-source support.

The separate two-case long-context probe places decisive support after repeated irrelevant
material and places two related standards at opposite ends of a long neutral premise. It is
diagnostic only and is not mixed into the primary rates.

### Metrics

- **False-support rate (FSR):** unsupported claims accepted / all unsupported claims.
- **Critical FSR:** critical unsupported claims accepted / critical unsupported claims.
- **Supported-claim retention:** supported claims accepted / all supported claims.
- **Latency:** median of seven warm GPU/CPU runs, batch size eight, divided by 28 only for the
  per-claim display.
- **Memory:** model parameter bytes and peak CUDA reserved memory. Process working-set values
  are recorded by the runner but are platform/runtime dependent.

FSR is the primary selection metric. The spike used the same predeclared conservative policy
for every NLI model: argmax must be entailment and entailment probability must be at least
`0.90`. These raw softmax values are not assumed calibrated probabilities.

### Reproduction environment

- Windows host, Python 3.11.15
- NVIDIA RTX 3080 Ti, 12,288 MiB
- `torch==2.8.0+cu128`
- `transformers==4.55.2`
- `sentencepiece==0.2.1`
- `psutil==7.0.0`
- batch size 8, one warm-up, seven measured runs, seed 0

The benchmark performs local inference only. It does not crawl, ingest, embed retained
sources, call report generation, mutate Qdrant/SQLite, or persist a report.

## Minimal approach comparison

| Approach | Measured FSR | Supported retention | Added latency / memory | Operational complexity | Disposition |
|---|---:|---:|---|---|---|
| Prompt-only plus current represented-source validation | 18/18 = 100% | 10/10 = 100% | approximately zero | Low | Baseline only; no semantic guarantee |
| Atomic claim plus exact represented span presence, without entailment | 18/18 = 100% | 10/10 = 100% | negligible | Low-medium | Necessary provenance improvement, still insufficient |
| Exact extractive claim match | 0/18 = 0% | 1/10 = 10% | negligible | Low | Safe but loses most synthesized/paraphrased findings |
| Local NLI alone, best candidate at 0.90 | 0/18 = 0% | 8/10 = 80% | GPU 3.392 ms/claim; CPU 46.081 ms/claim; 703.5 MiB parameters | Medium | Promising, but premise provenance must also be constrained |
| Atomic claims + exact spans + local NLI | 0/18 = 0% | 8/10 = 80% | Same measured NLI cost plus span validation | Medium-high | Recommended target after a larger blind evaluation |

“Atomic plus exact span presence” accepts all fixture pairs because every case deliberately
has an exact evidence premise; presence does not distinguish support from contradiction or
neutrality. The extractive row measures an exact normalized claim-to-span proxy. A product
that emits raw selected passages can preserve more evidence coverage, but it ceases to be a
claim synthesis report and does not provide comparable supported-claim retention.

## NLI candidate comparison

| Model | Domain/provenance and context | License / revision posture | Primary FSR | Critical FSR | Retention | GPU ms/claim / peak | CPU ms/claim | Result |
|---|---|---|---:|---:|---:|---:|---:|---|
| `pritamdeka/PubMedBERT-MNLI-MedNLI` | PubMedBERT, then MNLI and MedNLI; 512 positions; card reports validation accuracy up to 86.67% but leaves evaluation details incomplete.[8] | No license declared; pin `f1b6ce2...` if used for research only | 44.44% | 57.14% | 70% | 1.730 / 514 MiB | 29.566 | Reject: severe neutral false acceptance and unclear license |
| `tasksource/deberta-small-long-nli` | Multi-task/fact-verification training; 1,680 positions; card reports FEVER-NLI 71.7 and doc-NLI 75.0.[5] | Apache-2.0; pin `9a77395...` | 0% | 0% | 50% | 1.836 / 670 MiB | 22.004 | Reject as primary: safe on spike but excessive supported loss |
| `tasksource/deberta-base-long-nli` | Multi-task/fact-verification training; 1,280 positions; card reports FEVER-NLI 79.4 and doc-NLI 90.0.[6] | Apache-2.0; pin `04dcf11...` | 11.11% | 14.29% | 70% | 2.887 / 870 MiB | 43.457 | Reject: accepted causal and significance overclaims |
| `cross-encoder/nli-deberta-v3-base` | SNLI + MultiNLI; 512 positions.[7] | Apache-2.0; pin `6c749ce...` | 5.56% | 7.14% | 80% | 2.833 / 870 MiB | 43.913 | Reject: accepted causal overreach |
| `MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` | MNLI + FEVER-NLI + ANLI (763,913 pairs); 512 positions; card reports 90.3 MNLI, 77.7 FEVER-NLI, and 57.9 ANLI-all accuracy.[4] | MIT; pin `6f5cf0a...` | 0% | 0% | 80% | 3.392 / 870 MiB | 46.081 | Best candidate; advance only to larger blind evaluation |

DeBERTa-v3-base has 12 layers, hidden size 768, 86M backbone parameters, and a large 128K
vocabulary; its base card reports 90.6/90.7 MNLI matched/mismatched development accuracy.[10]
The measured classifier parameter footprint was 703.5 MiB. The smaller PubMedBERT model was
faster and domain-trained but performed much worse under the evidence-only false-accept
objective. This is consistent with the policy mismatch: MedNLI annotation permits clinical
knowledge, while this verifier must not fill missing relationships from domain knowledge.[2]

A newer gated `shidey/deberta-v3-mednli-scifact-open-sentence-nli` card self-reports 74.5%
on a 110-example SciFact-Open test and 87.0% MedNLI accuracy, but access requires accepting
conditions, no usable license is declared in the retrieved metadata, the card contains
inconsistent duplicate cross-domain result rows, and its 256-token setup is shorter than the
proposed bound.[9] It was not benchmarked and is not a production candidate.

### Long-context and multi-evidence interpretation

The primary set directly measures multi-evidence behavior. The winning model retained the
genuine two-source case and rejected both the live SPIRIT-AI/TRIPOD-ML composition and the
other false multi-source relationship. The small long-NLI model rejected the false
relationships but also rejected the genuine multi-source claim.

The exact live failure was correctly classified `neutral` with entailment score `0.2048`.
The broader operating point is still threshold-sensitive: in a sweep over the complete set,
`0.80` and `0.70` each accepted 1/18 unsupported claims, while `0.50` accepted 2/18. The
candidate result therefore depends on a conservative operating point that must be calibrated
and tested independently.

The long-context cards advertise 1,680 and 1,280 positions for the tasksource small and base
models, versus 512 for the other candidates.[5][6] Production should not select a weaker
false-support model merely for a larger context. Exact cited spans should normally fit within
512 tokens. Over-budget claims must fail closed or be reduced to exact spans before NLI; no
silent head/tail truncation is acceptable.

The separate long-context probe confirmed that the larger advertised window did not solve
the conservative operating-point trade-off. At `0.90`, the tasksource small, tasksource base,
cross-encoder, and winning Moritz model all rejected the long supported-at-end case and
correctly rejected the long false relationship. PubMedBERT accepted both, yielding 100%
retention but also 100% FSR on this two-case diagnostic. The probe is too small for a rate
claim, but it supports span reduction over selecting a weaker model for context length.

## Ollama and workstation interaction

A non-persisting one-token probe loaded the deployed `qwen3.5:9b` Q4_K_M model at a 4,096-token
context. Ollama reported 5,490,081,790 bytes (5,235.7 MiB) in VRAM. Total GPU use was 7,477
MiB before NLI. The winning NLI model reserved 870 MiB, remained runnable while Qwen stayed
loaded, and Qwen was still present in `/api/ps` afterward. Additive measured usage leaves
approximately 3,941 MiB (3.85 GiB) on the 12 GiB card in this configuration.

This was loaded co-residency, not concurrent generation. The report path is sequential, so
that is the relevant initial case. It does not establish safety for `qwen3.6:27b`, larger
contexts, parallel report generation, or future GPU services.

Recommendation: deploy the first verifier on CPU, preferably as one internal service shared
by the API and worker. The measured CPU upper bound was 1.290 seconds for all 28 claims in
four batches; a pinned-revision verification run measured a 1,652.9 MiB peak process working
set. This is small relative to the observed 13.6-second authoritative generation call and
fits the 32 GiB workstation. CPU placement avoids CUDA-image growth, duplicated GPU
reservations, and contention with Ollama. GPU remains an optional later optimization after
production load measurement.

## Failure behavior

- **Neutral or low-confidence:** omit the claim.
- **Contradiction:** omit the original claim immediately. The existing single correction call
  may generate a replacement, but the rejected wording is never retained.
- **One correction total:** aggregate all first-pass rejection reasons into the existing one
  complete-object correction. Verify the regenerated object from scratch. After that,
  independently omit every non-passing material claim.
- **Verifier unavailable or model/revision mismatch:** fail closed. Do not fall back to
  citation-only or prompt-only acceptance. Mark report generation failed using the existing
  failure lifecycle and preserve the previous persisted Markdown/source registry on retry.
- **Over-token evidence, unresolved span, invalid span/source mapping, or malformed verifier
  output:** treat as unsupported and include a bounded rejection reason.
- **Unknowns:** may record a generic count of omitted claims or verifier unavailability, but
  must not restate the rejected factual proposition as an “unknown.”

## Expanded deterministic evaluation gate (2026-08-11)

### Audit of the original 28 cases

All 28 premise/claim pairs were re-read under the evidence-only support definition. There
were no duplicate IDs or duplicate normalized premise/claim pairs. Two corrections were
justified:

1. `supported-clinical-abbreviation` expanded `Cr` to `creatinine (Cr)` in the evidence. The
   original entailment depended on outside clinical abbreviation knowledge prohibited by the
   policy; the claim and intended healthcare-language challenge remain unchanged.
2. `neutral-quantifier-overreach` changed from neutral to contradiction and its category to
   `quantifier`. Exactly three of four sites improving excludes all four improving.

The audited distribution is 10 entailments, 5 contradictions, and 13 neutrals: still 18
unsupported cases. `supported-two-sentence-single-source` is annotation-dependent on the
policy's explicit allowance for arithmetic (240 split equally means 120 per group), and
`supported-adjacent-fragments` depends on resolving the repeated endpoint phrase across two
evidence items. Both remain entailments. No other correction was justified.

The claim and both evidence strings in `live-spirit-tripod-unsupported-relation` remain exact.
Its neutral label and critical status are protected by the deterministic validator.

The original fixture did not adequately cover missing qualifiers, double negation, population,
intervention, comparator, outcome, unit, confidence-interval, modality, temporal, study-stage,
narrower-scope, conflicting-evidence, evidence-order, irrelevant-padding, or over-budget
behavior. Coarse legacy categories also prevented useful per-category measurement.

### Versioned corpus and annotation controls

The v2 JSON Schema separates `calibration`, `final_blind`, and `critical_regression`
partitions and distinguishes `draft` from `adjudicated` cases. Each adjudicated case records
the diagnostic three-way label, rationale, material claim components, and exact supporting,
refuting, or relevant spans. The validator resolves every recorded span as a literal substring
of its indexed evidence and rejects duplicate IDs, unknown labels/categories, malformed
evidence, PHI-permitted cases, missing requested categories, and premise/claim leakage across
partitions.

The checked-in composition is:

| Artifact | State | Entailment | Contradiction | Neutral | Purpose |
|---|---|---:|---:|---:|---|
| Legacy v1 | audited | 10 | 5 | 13 | Historical 28-case comparison only |
| v2 calibration | frozen annotations | 14 | 7 | 20 | Threshold selection only |
| v2 critical regression | frozen annotation | 0 | 0 | 1 | Exact live failure, never threshold tuning |
| v2 final blind draft | unlabeled draft | unknown | unknown | unknown | 405 cases awaiting two reviews and adjudication |

The draft generator creates 325 intended unsupported strata and 80 supported controls, shuffles
them with a fixed seed, and removes labels and stratum names from reviewer cases. These intended
counts are a construction target, not gold truth. The final set becomes valid only after two
reviewers independently label each case without model output or calibration access, an
adjudicator resolves disagreements, categories and exact spans are recorded, the validator
passes, and the resulting corpus hash is sealed. No final case was used for model or threshold
selection. All text is synthetic or public and explicitly contains no PHI.

The calibration set has 41 cases and covers all 38 requested categories. Some categories are
necessarily asymmetric (for example, contradiction and causal overreach are unsupported), so
the final draft balances supported controls against 25 unsupported transformation families
rather than forcing logically invalid label symmetry.

### Benchmark and threshold calibration

The research runner now requires an immutable 40-character model revision, sets offline mode,
uses `local_files_only=True`, and rejects rather than truncates inputs beyond the model token
budget. It reports false-support and critical false-support rates, supported retention,
argmax three-way precision/recall and confusion, uncalibrated NLL/Brier/ECE, risk-coverage,
per-category rates, repeat determinism, latency, parameter memory, CPU working set, and CUDA
peak allocation/reservation. Raw softmax outputs are explicitly scores, not calibrated
probabilities.

Only the calibration partition was swept at every threshold from `0.50` through `0.99` in
`0.01` increments. The primary objective was zero false acceptance; the secondary objective
was maximum supported retention; equal results used the higher threshold. Unsupported passes
changed as follows (the machine output retains a row for every candidate threshold):

| Thresholds | Unsupported cases accepted |
|---|---|
| 0.50-0.56 | `cal-wrong-unit-001`, `cal-causal-001`, `cal-conflict-001` |
| 0.57-0.95 | `cal-causal-001`, `cal-conflict-001` |
| 0.96 | `cal-conflict-001` |
| 0.97-0.99 | none |

The frozen threshold is `0.97`, bound to calibration SHA-256
`bedef419b91cea1d3c1b670437d54249fcaa99c76c8414f7d857f08292549aaf`, model revision
`6f5cf0a2b59cabb106aca4c287eed12e357e90eb`, and the argmax-entailment requirement.

### Expanded calibration measurements

Pinned offline CPU run, batch size eight, one warm-up and seven measured repeats:

| Gate metric at 0.97 | Result |
|---|---:|
| False-support rate | 0/27 = 0% |
| Critical false-support rate | 0/27 = 0% |
| Supported-claim retention | 13/14 = 92.8571% |
| One-sided 95% zero-failure FSR upper bound | 10.5019% |
| Exact repeated scores | yes; maximum delta 0.0 |
| Median CPU latency | 37.596 ms/scored claim; 1,503.842 ms/40 scored cases |
| CPU p95 | 1,548.973 ms/40 scored cases |
| Parameter memory | 703.5 MiB |
| CPU peak working set | 2,025.7 MiB |
| CUDA peak allocation/reservation | 0/0 MiB (CPU run) |
| Over-budget behavior | one supported long case rejected; no truncation |

The complete argmax confusion matrix is independent of the stricter acceptance threshold:

| Gold \ predicted | Contradiction | Entailment | Neutral | Unavailable |
|---|---:|---:|---:|---:|
| Contradiction | 5 | 1 | 1 | 0 |
| Entailment | 0 | 13 | 0 | 1 |
| Neutral | 5 | 4 | 11 | 0 |

| Class | Precision | Recall |
|---|---:|---:|
| Contradiction | 0.500000 | 0.714286 |
| Entailment | 0.722222 | 0.928571 |
| Neutral | 0.916667 | 0.550000 |

Diagnostic raw-softmax metrics were NLL `0.754148`, multiclass Brier score `0.425006`, and
10-bin ECE `0.212037`. They demonstrate poor calibration and must not be interpreted as
probability guarantees. Per-category measurements are emitted machine-readably; at the frozen
threshold every unsupported category had 0% observed false support, all supported categories
retained 100% except `long_context`/`over_budget` (0%) and the aggregate `scientific` category
(75%). These denominators are tiny and are diagnostics, not category-level performance claims.

The exact SPIRIT-AI/TRIPOD-ML regression was run only after threshold freeze. Across seven
repeats it was classified neutral with scores: entailment `0.204776`, neutral `0.785296`, and
contradiction `0.009928`; it was rejected with no truncation. Offline pinned-revision startup
succeeded. Alternative candidates were not rerun: without an adjudicated final set they cannot
change the blocked implementation decision, while the original spike already showed either
false accepts or materially worse retention.

The model provenance used by the runner matches the official cards: the winning model is MIT
licensed and trained on MNLI, FEVER-NLI, and ANLI; both tasksource long-NLI cards declare
Apache-2.0 and long/multi-task NLI training; the cross-encoder card declares Apache-2.0 and
SNLI/MultiNLI training; and the PubMedBERT card says MNLI followed by MedNLI but declares no
license.[4][5][6][7][8] SciFact's primary paper requires support/refute evidence rationales,
MedNLI explicitly allowed medical knowledge and common sense (unlike this policy), and the
health fact-checking paper treats multiple and conflicting evidence as a separate problem.[1][2][3]

### Explicit implementation gate

Production implementation is recommended only when one sealed final evaluation satisfies all
of the following:

- zero critical false accepts;
- at least 299 adjudicated unsupported cases and zero false accepts, giving a one-sided 95%
  zero-failure upper bound below 1%;
- the approved supported-retention floor of `>=95%`;
- deterministic repeated decisions and acceptably stable scores;
- the approved CPU budgets of `<=40 ms` median per scored claim at batch eight and
  `<=2.5 GiB` peak working set;
- no silent evidence truncation and fail-closed over-budget handling;
- successful offline startup at the exact pinned revision; and
- production tests proving fail-closed behavior when the verifier is unavailable.

All quantitative and stakeholder gates passed, and production fail-closed availability tests
are implemented. Owner review and any separately approved live retry remain outstanding. Any
threshold or model change requires a new untouched final set; the sealed final partition must
not be used for further tuning or candidate comparison.

### Verification

- Every fixture JSON artifact passed `python -m json.tool`.
- The deterministic validator passed the legacy audit, v2 annotation/span/category and leakage
  checks, draft reproduction, calibration/threshold binding, final corpus/seal hash binding,
  and final result/model/revision/threshold binding.
- All four benchmark/validation scripts compiled and six focused unit tests passed.
- The exact requested containerized Research Hub suite passed 87 tests in 2.848 seconds.
- `git diff --check` passed.
- Final `git status` showed no change under `research-hub/app/` or to
  `research-hub/requirements.txt`. No report retry or live corpus mutation was run.

## Final blind evaluation (2026-08-11)

### Independent review and adjudication

Two isolated review processes received separate copies of the same blinded package. It
contained only opaque `review-NNNN` IDs, claim/evidence text, the three labels, and the
evidence-only policy. It omitted draft IDs, categories, critical flags, generator strata,
calibration data/results, model outputs/scores, and threshold-selection information. Each
process completed all 405 cases before comparison and could not inspect the other review. The
common package SHA-256 was
`57e3675d7c6ae570e748551e771b5e1c7498a4657d2fa97577af7e112ae8725d`.

Reviewer A assigned 80 entailment, 91 contradiction, and 234 neutral labels. Reviewer B
assigned 80 entailment, 104 contradiction, and 221 neutral labels. They agreed on 392/405
labels (`96.7901%`; Cohen's kappa `0.945410`) and disagreed on 13. Every disagreement was the
same study-stage pattern: evidence that an exploratory feasibility study began versus a claim
that a completed phase III trial proved efficacy. Adjudication resolved all 13 as neutral
because the earlier stage does not establish that a later phase III result is false.

One agreement from each of the 35 construction families was spot-checked. That check found a
systematic 13-case agreement error in the lexical-overlap family: “no mortality result was
reported” does not entail that no mortality result was found. Those final labels changed from
contradiction to neutral, with the evidence recorded as merely relevant. Both original reviews
remain embedded in every final case. The final distribution is 80 entailment, 78 contradiction,
and 247 neutral: 325 unsupported cases, all marked critical. The eight genuine multi-source
supported controls are also critical. All 405 cases are synthetic, `phi=false`, and preserve
the draft claim/evidence text exactly.

The draft was not overwritten. The frozen corpus is
`research-hub/tests/fixtures/claim_support_final_v2.json`, corpus ID
`claim-support-final-blind-2026-08-11`, SHA-256
`1675d9ede5425dad37e6b8168886b91234b56896171882b704fb3bd6f9e490dc`.
The separate seal records the draft/package hashes, reviewer marginals, agreement, adjudication,
composition, and corpus hash.

### One-time frozen run

The final partition was run once after sealing. The command used pinned
`MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` revision
`6f5cf0a2b59cabb106aca4c287eed12e357e90eb`, CPU, batch size eight, local-files-only startup,
and the frozen `0.97` threshold. One warm-up and seven measured deterministic repeats occurred
inside that single run. No model, threshold, label, or corpus change followed the result. The
exact machine output is `research-hub/tests/fixtures/claim_support_final_results_v2.json`
(SHA-256 `5c65544cf381335174e5ee4f00aca28941a3c5134568a4ecc0388a2353a129b5`).

| Frozen-gate metric | Final result |
|---|---:|
| False-support rate | 0/325 = 0% |
| Critical false-support rate | 0/325 = 0% |
| Supported-claim retention | 79/80 = 98.75% |
| Accepted coverage | 79/405 = 19.5062% |
| One-sided 95% zero-failure FSR upper bound | 0.9175% |
| Unsupported cases accepted | none |
| Over-budget cases | none; maximum 512 tokens, no silent truncation |
| Repeat determinism | seven repeats; exact scores; maximum delta 0.0 |
| Offline pinned startup | succeeded; model load 0.584 s |
| Median CPU latency | 12,835.881 ms/set; 31.694 ms/scored claim |
| CPU p95 | 13,053.186 ms/set |
| Parameter memory | 703.5 MiB |
| CPU peak working set | 2,027.0 MiB |
| RSS after inference / load delta | 1,772.2 / 1,179.9 MiB |
| CUDA peak allocation / reservation | 0 / 0 MiB; CPU run |

The sole supported rejection was `final-draft-0239`, an irrelevant-padding control. Its argmax
was entailment but its entailment score `0.959341` was below `0.97` (neutral `0.032067`,
contradiction `0.008592`).

Argmax confusion, independent of threshold acceptance:

| Gold \ predicted | Contradiction | Entailment | Neutral | Unavailable |
|---|---:|---:|---:|---:|
| Contradiction | 65 | 13 | 0 | 0 |
| Entailment | 0 | 80 | 0 | 0 |
| Neutral | 91 | 26 | 130 | 0 |

| Class | Precision | Recall |
|---|---:|---:|
| Contradiction | 0.416667 | 0.833333 |
| Entailment | 0.672269 | 1.000000 |
| Neutral | 1.000000 | 0.526316 |

Raw-softmax diagnostics were NLL `0.834496`, multiclass Brier `0.485016`, and 10-bin ECE
`0.249498`. They are diagnostics, not calibrated probabilities.

Per-category frozen-threshold results:

| Category | Cases | FSR | Retention |
|---|---:|---:|---:|
| adversarial_lexical_overlap | 65 | 0% | n/a |
| bibliography_only | 13 | 0% | n/a |
| broader_scope | 13 | 0% | n/a |
| causal_overreach | 13 | 0% | n/a |
| conflicting_evidence | 13 | 0% | n/a |
| contradiction | 52 | 0% | n/a |
| exact_support | 16 | n/a | 93.75% |
| faithful_paraphrase | 24 | n/a | 100% |
| false_multi_source_relation | 13 | 0% | n/a |
| fragmented_object | 13 | 0% | n/a |
| fragmented_subject | 13 | 0% | n/a |
| genuine_multi_source | 8 | n/a | 100% |
| healthcare | 86 | 0% | 100% |
| irrelevant_padding | 8 | n/a | 87.5% |
| missing_qualifier | 39 | 0% | n/a |
| modality | 13 | 0% | n/a |
| narrower_scope | 16 | n/a | 100% |
| negation | 21 | 0% | 100% |
| neutral_related | 39 | 0% | n/a |
| partial_compound | 13 | 0% | n/a |
| quantifier | 42 | 0% | 100% |
| scientific | 123 | 0% | 100% |
| study_stage_overreach | 13 | 0% | n/a |
| supported_compound | 8 | n/a | 100% |
| temporal_overreach | 13 | 0% | n/a |
| wrong_comparator | 13 | 0% | n/a |
| wrong_confidence_interval | 21 | 0% | 100% |
| wrong_entity | 13 | 0% | n/a |
| wrong_intervention | 13 | 0% | n/a |
| wrong_number | 34 | 0% | 100% |
| wrong_outcome | 26 | 0% | n/a |
| wrong_population | 13 | 0% | n/a |
| wrong_statistical_significance | 26 | 0% | n/a |
| wrong_unit | 21 | 0% | 100% |

The result artifact records risk/coverage at every threshold from `0.50` through `0.99` in
`0.01` increments. FSR first reached zero at `0.89`; coverage/retention were
`0.197531`/`1.0` through `0.95`, `0.195062`/`0.9875` at the frozen `0.97`,
`0.182716`/`0.925` at `0.98`, and `0.135802`/`0.6875` at `0.99`. These rows are reporting only;
the final partition did not select or alter the threshold.

The separately frozen SPIRIT-AI/TRIPOD-ML critical regression was not rerun as part of final
selection. Its exact prior pinned result remains: predicted neutral and rejected at `0.97`,
with entailment `0.204776`, neutral `0.785296`, contradiction `0.009928`, 89 input tokens,
no truncation, and seven exact repeats.

### Statistical and release limitations

- The exact one-sided zero-failure bound treats 325 trials as independent Bernoulli draws.
  These are synthetic, templated cases with 13 close variants per unsupported family, so the
  `0.9175%` bound does not establish the same error rate for natural production claims.
- The 80 supported controls are also synthetic; the observed `98.75%` retention is not a
  stakeholder-approved product floor.
- The poor NLL/Brier/ECE and argmax entailment precision show that raw scores are not
  calibrated and safety depends on the frozen conservative acceptance rule.
- No final case exceeded the token budget, so this partition confirms no silent truncation but
  does not independently exercise over-budget fail-closed behavior. Calibration retains that
  deterministic regression.
- CPU latency and working set are one-host measurements. CUDA zeros mean CUDA was not used,
  not that the model has no GPU cost.

The quantitative false-support, determinism, truncation, offline-startup, approved retention,
latency, and memory conditions passed. The production verifier and availability tests now
implement the outline below. Do not tune against final failures or change `0.97`.

## Implemented production design

### 1. Expand and freeze the evaluation gate

- Have two reviewers independently label a blinded, versioned set and adjudicate differences.
- Add at least 299 unsupported cases if the release claim is a one-sided 95% upper bound below
  1% after zero false accepts.
- Stratify healthcare/scientific claims, negation, quantities, causality, comparisons,
  bibliography-only evidence, fragmentation, exact live failures, multi-source support, and
  adversarial entity swaps.
- Keep threshold calibration and final test partitions separate. Do not tune on the final
  gate.
- Report per-class precision/recall, confusion matrices, NLL, Brier score, expected
  calibration error, and risk-coverage curves; treat raw softmax values as scores until the
  target-domain calibration set establishes otherwise.
- Require zero critical false accepts, report contradiction/neutral confusion separately,
  and set a supported-retention floor only after the set is representative.

### 2. Add internal evidence identities and exact spans

Extend only the private generation schema. Each material item should contain:

- `text`;
- one or more internal `evidence_refs`;
- each ref's packed evidence ID; and
- an exact supporting substring or deterministic character offsets within that packed,
  sanitized evidence entry; and
- a private atomic `supports` proposition stating the claim component carried by that span.

Validate substring/offset identity before NLI. Map each passing evidence ref back to its
existing source ID, retained document ID, canonical URL, and chunk index. Derive public
`[S#]` citations from that mapping. Do not expose the internal fields in report APIs.

For multi-span claims, batch three checks: each span entails its `supports` proposition, the
full claim entails or contains each required `supports` proposition, and the union of spans
entails the full claim. This permits genuine joint support while rejecting unrelated citation
padding. Corroborating duplicate support may map to the same proposition. Any failed link
rejects the complete claim.

### 3. Add a transport-neutral verifier seam

Introduce a small claim-verifier interface used by synthesis after structured parsing. A CPU
sidecar is preferred so the API retry path and worker post-ingestion path share one pinned
model instance. Configuration should include:

- exact model ID and immutable revision;
- offline/local-files-only mode after image build;
- device and batch size;
- maximum premise tokens and maximum evidence refs;
- entailment threshold; and
- request timeout and health state.

The service returns label scores and bounded reason codes, never free-text rationales used as
facts. Pin all weights in the image or an immutable artifact cache; startup must fail if the
revision is unavailable.

### 4. Integrate before rendering and persistence

Refactor the current private path into:

`parse -> resolve spans -> verify claims -> one correction -> verify again -> retain -> render citations -> persist`

`key_findings` and `disagreements` are verified. Existing public Markdown, report objects,
source registries, attempt counts, previous-report preservation, and source provenance remain
unchanged. No ingestion/retrieval/corpus mutation is involved.

### 5. Add deterministic tests and bounded telemetry

Required regression tests:

- exact SPIRIT-AI/TRIPOD-ML claim is neutral and omitted;
- supported single-span, multi-span, and genuine multi-source claims remain;
- contradiction, negation, numeric swap, causal overreach, bibliography-only, fragmentation,
  wrong entity, and mixed compound are omitted;
- source IDs are rendered only from passing evidence refs;
- each evidence ref supports a necessary claim component; unrelated citation padding fails;
- a correction cannot bypass verification;
- verifier unavailable, timeout, wrong revision, and over-token input fail closed;
- a failed retry preserves the previous report;
- public response schemas remain byte-compatible; and
- no crawl, source embedding, upsert, or document mutation occurs.

Add bounded counters for verifier label, low-confidence rejection, span-resolution failure,
unavailable/timeout, correction outcome, and latency. Do not use claim text, topic, URL, job
ID, or source ID as metric labels.

## Acceptance recommendation

The larger sealed evaluation and approved stakeholder budgets support owner review of the
implemented gate. Phase 4 acceptance still requires a separately approved live report retry
whose every displayed material claim passes exact evidence resolution and the semantic gate.
Phase 5 retrieval work remains unrelated and unapproved.

## Final controlled live acceptance gate (2026-08-11)

Commit `fb6366fd1a17f4c3aa5daa24e668420d8ce12588` was deployed only to
`claim-verifier`, `research-hub`, and `research-worker`. All three processes were stable with
zero restarts; research readiness was `ok`, `/health/full` reported
`claim_verifier=true`, verifier health returned the exact frozen model and revision, and
`HF_HUB_OFFLINE=1` plus `TRANSFORMERS_OFFLINE=1` confirmed offline runtime access.

Exactly one authorized retry was issued for authoritative job
`4b8acd0f-088f-4b97-92fc-f52b69b8a3ee` at `2026-08-11T11:12:23.6262358Z`.
It returned HTTP 502 after 32.292422 seconds. Report attempt 4 advanced to attempt 5 with
status `failed`, update time `2026-08-11T11:12:55.192164Z`, and error
`Expecting ',' delimiter: line 57 column 6 (char 3647)`. Both the first generation and the
single bounded correction consumed the full 1,024-token output allowance and produced the
same JSON parse failure. Ollama reported `truncated=0` with 2,057 and 2,159 prompt tokens in
its 4,096-token slot. No claim reached exact-span resolution or the verifier; verifier logs
contain health probes only. There was no citation-only fallback.

The failed-attempt lifecycle preserved the complete attempt-4 Markdown and six-source registry.
That preserved report still displays the five previously audited supported findings and the
unsupported SPIRIT-AI/TRIPOD-ML-before-full-clinical-trials composite. All nine inline citation
references resolve to the prior represented `[S4]`, `[S5]`, and `[S6]` evidence, but citation
resolution does not make the sixth composite semantically supported. Attempt 5 produced no new
displayed finding or disagreement and therefore cannot satisfy the production NLI acceptance
gate.

The mutation audit remained clean apart from the expected report attempt/status/error and job
timestamp updates: Redis pending/processing queues stayed `0/0`; SQLite stayed at 67 documents,
67 job/source observations, six authoritative observations, and 13 reports; Qdrant stayed at
24,465 points, 23,369 indexed vectors, and six segments. Worker logs contained no crawl,
ingestion, retained-document embedding, or upsert activity. The sealed corpus, seal, and result
SHA-256 values remained
`1675d9ede5425dad37e6b8168886b91234b56896171882b704fb3bd6f9e490dc`,
`6c94272b771843325684bee9b6afb22e66c2c4fa42f849f88568fc3ee081f2f2`, and
`5c65544cf381335174e5ee4f00aca28941a3c5134568a4ecc0388a2353a129b5`.

Phase 4 and the initial Phases 0-4 modernization remain incomplete. Do not tune the frozen
model or threshold, issue another retry, rerun the sealed evaluation, or begin Phase 5 without
new explicit owner approval.

## Structured-output boundary follow-up (2026-08-11)

New authorization allowed one deterministic output-boundary fix and one attempt-6 retry. The
shared Ollama client now inspects the native response before returning generated text: it raises
an explicit error for `done_reason=length` and fails closed for `truncated=true`. The deployed
completion allowance increased from 1,024 to 1,536 tokens without changing the verifier,
revision, threshold, claim contract, strict parser, or correction limit.

The single retry began at `2026-08-11T11:39:02.8712367Z` and returned HTTP 409 after
28.068818 seconds. Its only generation used 1,846 prompt tokens and all 1,536 completion tokens
inside the 4,096-token slot (3,382 total), with `truncated=0` and `done_reason=length`. The new
boundary therefore failed clearly as designed, before JSON parsing, exact-span resolution, or
NLI. No correction or verifier request occurred, and no second retry was issued.

Attempt 6 is failed with `Ollama generation stopped at the 1536-token output limit
(done_reason=length)`. The prior Markdown and six sources remain byte-identical with SHA-256
`cd92f77159fc3369bd51b95e5658f98f0c1b5534e2d078b2d45367c7828decb7` and
`d6748d76ba27f783c709d54d73e617273e43a04399d7b42901a37f588d00aefe`.
Consequently, the preserved unsupported SPIRIT-AI/TRIPOD-ML composite is still not verifier-
approved. Queue, SQLite, Qdrant, worker, and sealed-evaluation audits remained clean.

Phase 4 remains incomplete. The condition for the Phase 5 design spike was not met; no hybrid-
retrieval work was started.

## 8,192-token context follow-up (2026-08-11)

New authorization raised Ollama's native runtime context from 4,096 to 8,192 and the existing
completion allowance from 1,536 to 2,048 tokens. No verifier, threshold, evidence contract,
parser, correction, or retrieval behavior changed.

The single attempt-7 retry returned HTTP 200. Its first and correction generations used
1,779+872 and 1,865+1,073 prompt/completion tokens respectively in an 8,192-token slot, both
with `truncated=0`. This resolves the prior output-length failure.

Phase 4 nevertheless remains incomplete. Exact-span resolution rejected one first-pass claim
and all seven corrected material claims (`unresolved_span=8` total), leaving zero claims for
substantive NLI evaluation. The report correctly omitted all seven claims, contains no material
finding or disagreement, and excludes the unsupported SPIRIT-AI/TRIPOD-ML composite. The source
registry is unchanged; Redis, SQLite, Qdrant, worker, and sealed-hash audits remained clean.
The conditional Phase 5 authorization was therefore not activated.

## Deterministic exact-span selector follow-up (2026-08-11)

The next RCA confirmed that retrieval and structured JSON completion succeeded, but free-form
span copying remained outside the JSON schema's guarantees. The validator also compared the
raw model span before trimming it, and the single `unresolved_span` metric combined multiple
failure classes.

Research synthesis now assigns exact prompt-time span IDs to sentence/line substrings. The
private generation schema constrains the model to an enumerated `span_id`; the resolver trims
that ID, maps it to the original exact sanitized substring, and sends the unchanged resolved
`span` plus atomic `supports` proposition to the frozen verifier. Invalid IDs and mappings fail
closed with precise bounded reasons. No fuzzy matching, permissive parser, dependency, corpus
rebuild, retrieval change, verifier change, or public API change was introduced.

The focused suite passed 19 tests and the complete isolated suite passed 103. Research Hub and
Research Worker were rebuilt and deployed healthy; the running verifier remained untouched.
No acceptance retry was run. Attempt 7 remains preserved, so Phase 4 still awaits a separately
authorized single retry and Phase 5 remains unstarted.

## Attempt-8 acceptance result (2026-08-11)

The one authorized retry confirmed that deterministic span IDs fixed the previous validation
barrier. The first generation completed normally, eight material claims resolved to exact
source substrings, and all eight reached the frozen production verifier. Seven were neutral
and one was below the `0.97` entailment threshold, so none qualified for display.

The existing single correction then exhausted the 2,048-token completion allowance and failed
explicitly with `done_reason=length`. No partial output was parsed, no claim or citation was
persisted, and no second retry was issued. Attempt 8 is failed while the attempt-7 Markdown and
source registry remain byte-preserved. Corpus, queue, SQLite, Qdrant, worker, and sealed-hash
audits remained clean. Phase 4 therefore remains incomplete and Phase 5 remains unstarted.

## Gate alignment with the sealed evaluation (2026-08-11)

The sealed final evaluation and the deployed gate were not the same rule. This section
records the discrepancy, the correction, and why the seal survives it.

### What the seal measured

`tests/benchmark_claim_support.py:78` constructs one NLI pair per sealed case:

    premise    = "Evidence 1: <text> Evidence 2: <text> ..."
    hypothesis = <claim>

Every number in the frozen run above - zero of 325 unsupported accepted, 79 of 80
supported retained, the `0.97` argmax-entailment operating point - describes that single
pair.

### What production ran

`LocalClaimVerifier.verify` emitted three pairs for a single-evidence claim and rejected
the claim if any one of them fell below `0.97`:

| role | premise | hypothesis | sealed |
| --- | --- | --- | --- |
| `span_support` | `span` | `supports` | no |
| `claim_support` | `text` | `supports` | no |
| `evidence_union` | `"Evidence 1: <span>"` | `text` | yes |

Synthesis always sets `supports` to the claim text, so `claim_support` compared a string
with itself. On all ten attempt-10 claims it returned entailment between `0.987841` and
`0.995008` and rejected nothing. `span_support` is `evidence_union` without the
`"Evidence 1: "` prefix - the same proposition drawn a second time.

The deployed acceptance rule was therefore `min(three draws) >= 0.97` against a seal that
certified `one draw >= 0.97`. The retention rate the project quoted for the running gate
had never been measured on it.

### Correction

- `supports` must restate the claim verbatim. This is now a string-equality assertion,
  which is strictly stronger than the NLI self-check it replaces and costs no inference.
  A mismatch fails closed as `malformed_claim`.
- A single-evidence claim is judged by `evidence_union` alone: the sealed pair, with the
  sealed premise format. `test_sealed_union_premise_format_is_preserved` asserts the
  literal `"Evidence 1: <span>"` premise reaches the model, so the linkage between the
  benchmark and the production path is now enforced by a test rather than by inspection.
- A multi-evidence claim keeps a per-span conjunct alongside the union, so an irrelevant
  padding ref cannot ride along on a union that entails the claim.

### Why the seal still holds

Only entailment is ever accepted, and adding conjuncts can only reject more. The
multi-evidence path is therefore strictly stricter than the sealed rule, and the
single-evidence path is the sealed rule exactly. Nothing the sealed final set rejected can
now be accepted, so the zero-unsupported-acceptance result carries over without a new
blind set.

The model, revision, threshold, batch size, 512-token no-truncation budget, offline
loading and fail-closed reasons are unchanged. The sealed corpus, seal and result hashes
are unchanged at
`1675d9ede5425dad37e6b8168886b91234b56896171882b704fb3bd6f9e490dc`,
`6c94272b771843325684bee9b6afb22e66c2c4fa42f849f88568fc3ee081f2f2` and
`5c65544cf381335174e5ee4f00aca28941a3c5134568a4ecc0388a2353a129b5`.

### Standing constraint

Changing the model, the revision, the threshold, or the `evidence_union` premise format
still invalidates the seal and still requires a new untouched final set. Removing the
multi-evidence per-span conjunct would also require one, because that path would then
become more permissive than what was measured. See HUB-032.

## Sources

[1] https://aclanthology.org/2020.emnlp-main.609 — Fact or Fiction: Verifying Scientific Claims
[2] https://physionet.org/content/mednli — MedNLI
[3] https://aclanthology.org/2023.bionlp-1.20 — Multiple Evidence Combination for Health Fact-Checking
[4] https://huggingface.co/MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli — DeBERTa-v3-base MNLI/FEVER/ANLI model card
[5] https://huggingface.co/tasksource/deberta-small-long-nli — DeBERTa-small-long-NLI model card
[6] https://huggingface.co/tasksource/deberta-base-long-nli — DeBERTa-base-long-NLI model card
[7] https://huggingface.co/cross-encoder/nli-deberta-v3-base — Cross-encoder NLI DeBERTa-v3-base model card
[8] https://huggingface.co/pritamdeka/PubMedBERT-MNLI-MedNLI — PubMedBERT MNLI/MedNLI model card
[9] https://huggingface.co/shidey/deberta-v3-mednli-scifact-open-sentence-nli — Gated DeBERTa MedNLI/SciFact model card
[10] https://huggingface.co/microsoft/deberta-v3-base — Microsoft DeBERTa-v3-base model card
[11] https://ar5iv.labs.arxiv.org/html/2305.14627 — ALCE: Automatic Benchmark for LLM Generations with Citations
