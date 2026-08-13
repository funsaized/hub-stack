# HUB-024 — Adaptive query planning and iterative research

Status: stage 1 implemented 2026-08-13 behind `REPORT_QUERY_PLANNING=false`;
stages 2–3 open. Operator-opened 2026-08-13 (revisit trigger tripped — reports
are built from whatever a single search phrasing surfaced).
Prior art reviewed: 16 arXiv abstracts, fetched and read 2026-08-13 (citations
at the end; every claim below is attributed to a fetched abstract or marked as
this design's own choice).

## Problem

A research job issues exactly ONE SearXNG query. Everything downstream —
retrieval, span selection, cross-document pair drafting, the judge gate — can
only work with the documents that one phrasing happened to surface. The
narrowness is on the **acquisition** side, not the retrieval side: hybrid
dense+FTS5 retrieval already measures hit@4 `1.0` on the exact-term manifest
(HUB-017), so the corpus is searched well; it is *assembled* narrowly.

This also caps the cross-source machinery deployed in HUB-034: pair drafting
can only find disagreements between sources that were actually crawled, and a
single query tends to return sources that agree with each other.

## The methodology, and why breadth is not a constant

The operator's constraint — *no arbitrary fixed sub-query count* — matches the
prior art's own finding. Static-DRA (2512.03887) makes Depth and Breadth
user-tunable parameters and its own limitation is exactly that: fixed
parameters cannot respond to what the evidence turns out to need. The modern
alternative is **cardinality by threshold, not by count** — ScoreGate
(2606.14269) replaces fixed top-K chunk selection with a score-fusion cutoff,
and reports better efficiency at equal quality. This design lifts that
principle from chunk selection up to query planning.

### 1. Breadth is emergent (marginal-distinctness admission)

One bounded local-LLM call proposes candidate facet queries for the topic —
distinct *information needs*, not paraphrases. Each candidate is embedded with
the already-deployed `nomic-embed-text` and admitted only if its maximum
cosine similarity to the already-admitted set is below `PLAN_FACET_DISTINCT`
(design default 0.85). Admission stops when the next candidate adds no
distinct retrieval intent.

Consequences, which are the point: a narrow factual topic admits one facet and
the job behaves exactly as it does today (no planning overhead, no extra
searches); a broad topic admits as many facets as it genuinely has angles. The
cap (`PLAN_MAX_FACETS`) exists as a safety rail against a pathological
planner, never as the mechanism that decides breadth.

Complexity routing supports this: Adaptive-RAG (2403.14403) selects among
no-retrieval / single-step / multi-step per query complexity and reports
efficiency and accuracy gains over uniform pipelines. Here the collapse of all
candidates into one facet *is* the complexity signal — no separate classifier
to train, and no cost paid on simple topics.

### 2. Rounds are gap-driven, not depth-numbered

After each round's ingestion, one bounded call reads a **coverage summary**
(per facet: retained document count, distinct domains) and names the facets
still uncovered and the sub-questions still unanswered. Only those become the
next round's queries. This is KiRAG's mechanism (2502.18397): reason over what
was retrieved to identify knowledge gaps explicitly, and let each gap drive
the next retrieval. REPAIR (2601.04618) makes the same move with reasoning
plans as feedback signals into adaptive retrieval, and Tree of Reviews
(2404.14464) structures it as expand / reject / accept decisions per node with
pruning — this design keeps the expand/reject decision but stays flat (a
per-facet round list), because a tree buys depth control we do not need and
costs an orchestration layer we would have to test.

### 3. Stopping: saturation first, coverage second, budget last

> **SUPERSEDED 2026-08-13.** Novelty saturation was implemented, measured, and
> retired — it could never fire under distinctness-based admission. Coverage
> was promoted from secondary to primary, in facet space. The original
> reasoning is kept below for the record; the implemented design is in
> "Implementation record" further down.

- **Novelty saturation (primary).** Stop when a round's yield of *new
  canonical URLs* falls below `PLAN_NOVELTY_MIN` (design default: fewer than
  20% of that round's retained documents are new, or fewer than 2 new
  documents). KAIR (2601.16462) progressively updates anchoring indices across
  iterative retrieval rounds; saturation in what a round newly anchors is the
  natural stop signal, and canonical-URL identity is this system's cheapest
  and most reliable instance of it (canonical URLs and content hashes already
  form stable document IDs).
- **Coverage (secondary).** Stop when every admitted facet has at least one
  retained document and the gap pass names nothing further.
- **Budget rails (backstop).** Hard caps on rounds, searches, crawls, and
  wall-clock, per job and configurable. AdaRankLLM (2604.15621) argues for
  learned necessity signals over fixed iteration budgets; this design follows
  that for the *decision* while keeping hard caps as a safety net, because an
  unbounded crawl loop on a workstation is an availability problem, not a
  quality one.

### 4. What the prior art says NOT to do

- **Do not expect breadth to fix report quality.** DeepWeb-Bench (2605.21482)
  evaluated nine frontier models and found retrieval failures account for only
  12–14% of errors while derivation and calibration failures exceed 70%.
  Acceptance for this item is therefore measured as *corpus breadth*
  (distinct domains, represented sources), not as report quality; the judge
  gate remains the quality guard.
- **Do not let rounds multiply tool calls carelessly.** HotelQuEST
  (2602.23949) found LLM agents beat traditional retrievers on accuracy but at
  substantially higher cost from redundant tool calls and routing that fails
  to match query complexity to capability. Canonical-URL dedup *across* facets
  before crawling is mandatory, not an optimization.
- **Do not adopt fixed depth × breadth.** (Static-DRA's own stated limitation.)

## Scope

In scope: research-job acquisition only (`app/research.py` planning + the
worker's search/crawl loop), job progress provenance, config surface, tests
and a deterministic breadth benchmark.

Out of scope: `/query` and `/rag` (PRD regression boundaries, unchanged);
retrieval ranking (HUB-017 measured `1.0` on its manifest); the claim gate and
its v4 seal (untouched — this changes what gets crawled, not how claims are
judged); any change to judge-call volume per report.

## Config surface (all default OFF/inert until measured)

| Variable | Default | Meaning |
|---|---|---|
| `REPORT_QUERY_PLANNING` | `false` | Master switch; off = today's single-query behavior, byte-identical |
| `PLAN_FACET_DISTINCT` | `0.85` | Max cosine similarity for admitting a new facet |
| `PLAN_MAX_FACETS` | `8` | Safety rail, not the breadth mechanism |
| `PLAN_MAX_ROUNDS` | `3` | Budget backstop |
| `PLAN_FACET_RELEVANCE` | `0.35` | Min cosine to the topic; the relevance half of the two-sided bar |
| `PLAN_SEARCH_BUDGET` / `PLAN_CRAWL_BUDGET` | `12` / `40` | Per-job hard caps |

## Acceptance criteria

- On a fixed topic set, planned jobs retain **more distinct domains and more
  represented sources** per report than the single-query baseline on the same
  topics; the comparison is recorded, not asserted.
- A topic whose candidates collapse to one facet issues exactly one search:
  no planning overhead on simple topics, and the resulting job is equivalent
  to today's path.
- Every sub-query inherits the full HUB-020/021 source policy (allowed/blocked
  domains, per-domain limit, freshness) and SSRF vetting (HUB-006); canonical
  URLs are deduplicated across facets and rounds before crawling, so no
  document is fetched twice.
- Judge calls per report stay bounded by the existing drafting caps —
  breadth must not increase metered cost.
- Worker semantics unchanged: leases, heartbeats, timeouts, bounded retries,
  idempotent re-ingestion.
- The plan is auditable: admitted facets, per-round queries, new-document
  yield, and the stop reason (`saturation` / `coverage` / `budget`) are
  recorded in job progress.
- `REPORT_QUERY_PLANNING=false` reproduces current behavior exactly.

## Implementation record — stages 1 and 2 (2026-08-13)

Built: facet admission, cross-facet and cross-round canonical dedup, budget
rails (stage 1); gap-driven rounds with novelty-saturation stopping and a
recorded stop reason (stage 2). All behind `REPORT_QUERY_PLANNING=false`. Not
built: the breadth measurement against the single-query baseline (stage 3),
which is gated on the operator's decision to enable the flag.

`app/query_plan.py` holds the mechanism; `app/research.py` phase 1 consumes
it. Three choices are worth recording because they are what keep the
acceptance criteria true by construction rather than by assertion:

1. **The topic is always facet 0.** A plan can only ever widen the
   pre-planning search, never replace it — so a degenerate planner cannot
   steer acquisition away from what the user actually asked, and the collapse
   case is literally the old query.
2. **One `apply_source_policy` pass over the merged candidates.** Canonical
   dedup across facets, allow/block lists, per-domain limits and freshness are
   not reimplemented for planning; sub-queries inherit HUB-020/021 because
   they flow through the same tested function. SSRF vetting is per-URL in
   `crawl_one` and never saw a change.
3. **Round-robin interleave, and `depth` still caps crawls.** Concatenating
   facet results would let facet 1 consume the whole crawl budget and silently
   collapse a multi-facet plan back to a single-facet corpus. Because the
   crawl cap is unchanged, breadth arrives at *constant* crawl and judge cost —
   which is what keeps "judge calls per report stay bounded by the existing
   drafting caps" true without any new accounting.

Stage 2 adds a round loop around **search and crawl only**; ingestion still
runs once, unchanged, over the accumulated crawl results. This deliberately
departs from the "after each round's ingestion" wording above: restructuring
the ingest block would put the worker's lease, heartbeat and idempotency
semantics at risk for no gain, and the acceptance criteria require those
unchanged. `should_continue` checks saturation before the round rail, so a
stop is attributed to the evidence running dry whenever both would have fired.

**Stopping was redesigned on 2026-08-13 after prior-art review** (11 further
arXiv abstracts fetched and read; citations below). Document novelty is
retired as a control signal and `PLAN_NOVELTY_MIN` is gone. It could never
fire: admission only admits *distinct* queries, distinct queries return
distinct documents, so novelty is pinned near 1.0 by construction whatever
the true state of the research (observed live at 1.0 / 1.0 / 0.983 across
three rounds that had visibly stopped making progress). Correcting the
denominator to the fetch window made the number honest but not useful — the
signal is structurally incompatible with distinctness-based admission.

Saturation is now measured in **facet-coverage space**, following RAVine
(2507.16725), which scores attributable coverage of a query's distinct
sub-points rather than document novelty. A facet is covered once at least one
retained document came from its own search; coverage is recomputed over every
issued query each round, because a document fetched later can answer an
earlier facet. Two threshold-free rules:

- `coverage_complete` — every issued facet holds evidence.
- `coverage_plateau` — a round raised the covered count by zero.

**Rounds therefore exist to finish covering the admitted plan, not to invent
new angles**, which makes a single round the normal case and extra rounds the
exception that fires when the crawl allowance could not reach every facet in
one pass. This is load-bearing: if completion did not end the loop, a gap
pass that keeps inventing facets would keep raising the count forever — the
same runaway that novelty produced, wearing a different hat.

The LLM gap pass is **advisory**, checked only after the arithmetic signal. It
proposes what to ask next and can end the research by declining, but it can no
longer be the primary stop, because sufficiency judgements of exactly this
kind measure poorly (RaCGEval, 2411.05547, baselines at 46.7%) and unaligned
models default to answering rather than declining (2507.04976). Novelty is
still recorded per round for calibration; it controls nothing. The full
candidate-pool count is retained in provenance as `pool`.

Gap queries are admitted by the same `greedy_admit` used for opening facets,
compared against **every query the job has already issued** rather than just
its own round — so a gap query cannot re-ask an earlier facet. A collapsed
plan never opens a second round, which is what keeps "a single-facet topic
issues exactly one search" literally true rather than approximately true.

Failure policy: every planner failure (unreachable model, malformed JSON,
mismatched embedding count) degrades to the single-query plan and records
`planner_unavailable` as the stop reason. A gap-pass failure ends the rounds
with the reason recorded and keeps everything already acquired. The planner
never fails a job.

Stop reasons recorded in job progress: `single_round` (collapsed or planning
off), `saturation`, `coverage`, `max_rounds`, `budget`, `planner_unavailable`.

Verification: 274 tests pass in-container with zero skips, 69 of them new and
fully offline (planner LLM and embeddings stubbed, no live search). Beyond the
unit cases, ten drive `run_job`'s acquisition directly and assert the
criteria: flag off issues exactly one search and never calls the planner; a
collapsed plan issues exactly one search and never opens a second round;
admitted facets issue one search each; a dead planner still acquires on the
single query; every facet's results appear in the one policy record; a
widened plan runs a gap-driven second round; a round that resurfaces known
sources stops on saturation below the rail; and a URL seen in round 1 is not
re-selected for crawl in round 2.

Deliberately unmeasured: `PLAN_FACET_DISTINCT = 0.85` is still the design
default. The open thread below is unresolved — the first measurement calibrates
the thresholds, it does not validate the design.

## Load-bearing risk

The planner is a generative component sitting upstream of acquisition: a bad
plan quietly narrows or skews the corpus rather than failing loudly. Mitigation
— the single-facet collapse path is the identity function on today's behavior,
every rail is a hard cap, and the stop reason is recorded per job so a
degenerate plan is visible in provenance rather than inferred from a thin
report.

## Open thread (design-review checkpoint before shipping)

None of the reviewed abstracts establishes how a saturation threshold
transfers across topic domains — whether `PLAN_NOVELTY_MIN = 0.2` means the
same thing for a niche clinical question as for a broad engineering topic.
Treat the first measurement as calibration of that threshold, not as
validation of the design.

**Closed 2026-08-13, by retiring the threshold entirely.** The question was
moot: the metric was structurally broken before any threshold could matter,
and the replacement (facet-coverage completion and plateau) has no tuned
threshold to transfer. One guessed threshold remains — `PLAN_FACET_RELEVANCE`
— and it ships deliberately permissive with every candidate's topic cosine
recorded, so it can be measured rather than guessed a second time.

**New open thread: facet coverage grades its own homework.** RAVine computes
coverage against labelled answer points; we compute it against self-generated
facets. A facet nobody proposed is invisible to the metric, so a narrow plan
reads as "fully covered" exactly when it is worst. The relevance floor and
pre-issue QPP narrow what gets proposed, which makes this *more* acute, not
less. Worth a checkpoint before coverage becomes load-bearing for anything
beyond stopping.

**New open thread: distinctness alone is the wrong admission criterion.** The
measurement showed facets drifting off-topic as rounds progress — the planner
proposed non-existent tool names, and later facets acquired HAProxy,
pgBackRest, Barman and monitoring-vendor pages that do not address the topic.
Marginal-distinctness admission maximises divergence from what is already
admitted, and nothing in it pulls back toward the topic; the most distinct
candidate available is frequently the least relevant one. ScoreGate's
threshold-not-top-K principle transfers, but it presumed a relevance-ranked
candidate set underneath. The indicated fix is a two-sided bar: admit a facet
only if cosine-to-the-admitted-set is below `PLAN_FACET_DISTINCT` **and**
cosine-to-the-topic is above a new `PLAN_FACET_RELEVANCE` floor. Not
implemented; measure before and after.

## Citations (fetched from arXiv 2026-08-13)

Primary mechanism:
- 2502.18397 — KiRAG: Knowledge-Driven Iterative Retriever for Enhancing
  Retrieval-Augmented Generation (https://arxiv.org/abs/2502.18397) —
  gap identification drives the next retrieval.
- 2606.14269 — ScoreGate: Adaptive Chunk Selection for RAG via Dual-Score
  Statistical Fusion (https://arxiv.org/abs/2606.14269) — adaptive cardinality
  by threshold instead of fixed top-K; lifted here to planning.
- 2601.16462 — Finding What Matters: Anchoring Context Knowledge with Evolving
  Indices for Iterative Retrieval (https://arxiv.org/abs/2601.16462) —
  round-over-round saturation as a stop signal.

Supporting evidence:
- 2403.14403 — Adaptive-RAG (https://arxiv.org/abs/2403.14403) — route by
  complexity instead of a uniform pipeline.
- 2604.15621 — AdaRankLLM (https://arxiv.org/abs/2604.15621) — learned
  necessity signals over fixed iteration budgets.
- 2601.04618 — REPAIR (https://arxiv.org/abs/2601.04618) — reasoning plans as
  feedback into adaptive retrieval.
- 2404.14464 — Tree of Reviews (https://arxiv.org/abs/2404.14464) — per-node
  expand / reject / accept with pruning.

Failure modes to avoid:
- 2605.21482 — DeepWeb-Bench (https://arxiv.org/abs/2605.21482) — retrieval is
  12–14% of errors; derivation/calibration exceed 70%.
- 2602.23949 — HotelQuEST (https://arxiv.org/abs/2602.23949) — redundant tool
  calls and complexity-mismatched routing dominate cost.
- 2512.03887 — Static-DRA (https://arxiv.org/abs/2512.03887) — fixed Depth and
  Breadth parameters cannot adapt to what the evidence needs.

Stopping and query-quality redesign (fetched and read 2026-08-13, second pass):
- 2507.16725 — RAVine: Reality-Aligned Evaluation for Agentic Search
  (https://arxiv.org/abs/2507.16725) — **primary mechanism**: score
  attributable coverage of a query's distinct sub-points, and evaluate the
  iterative process, not just the final answer. Lifted here as facet-coverage
  saturation replacing document novelty.
- 2411.05547 — Assessing the Answerability of Queries in Retrieval-Augmented
  Code Generation (https://arxiv.org/abs/2411.05547) — **failure mode to
  avoid**: judging whether retrieved evidence suffices is a hard task,
  baselines at 46.7%; do not make an LLM sufficiency call the primary stop.
- 2507.04976 — Can Video LLMs Refuse to Answer? Alignment for Answerability
  (https://arxiv.org/abs/2507.04976) — **failure mode to avoid**: models do
  not decline unless aligned to; a zero-shot gap pass inherits an
  answer-anyway bias, so it must be advisory rather than load-bearing.
- 1507.03928 — Pseudo-Query Reformulation (https://arxiv.org/abs/1507.03928)
  — **supporting evidence**: score a candidate reformulation with a
  performance predictor *before* issuing it, so weak reformulations are
  pruned pre-search. Adopted as pre-issue QPP.
- 2403.17421 — MA4DIV: Multi-Agent RL for Search Result Diversification
  (https://arxiv.org/abs/2403.17421) — **supporting evidence**: diversity
  must be relevance-discounted (α-NDCG), not raw. Motivates the two-sided
  admission bar; the MARL machinery itself is not adopted.
- 2405.17658 — Generative Query Reformulation using Ensemble Prompting
  (https://arxiv.org/abs/2405.17658) — supporting evidence: a critic pass
  filters generated reformulations for quality.

Read but not adopted (recorded so a later pass need not re-read them):
2509.22449 (unanswerability via linear activation directions — needs
white-box hidden states, unavailable behind Ollama), 2604.09666 (GraphRAG vs
agentic search benchmark — fixes retrieval budgets as a control variable),
2604.17931 (LiteResearcher — train-time agentic RL, not a runtime signal),
2309.12546 (PMAN — answerability scoring needs an answer already in hand),
1601.04615 (term-provenance in human session logs),
2410.20286 (Quam — recall via document-similarity graph, single-query scope),
2607.15283 (biomedical question-type routing, predefined categories),
2511.03214 (LGM — concept meta-relations for ambiguous terms),
2412.12559 (EXIT — post-retrieval extractive compression),
2601.06551 (L-RAG — entropy gating to skip retrieval),
2506.21506 (Mind2Web 2 — agent-as-judge evaluation of agentic search).
