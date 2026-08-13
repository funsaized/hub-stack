# Current deployed state

Last verified: 2026-08-13 on the local Windows 11 workstation.

## One retrieval path: the corpus is queryable as one base (HUB-043, 2026-08-13)

Hybrid dense+BM25+RRF retrieval used to run **only** inside a research job's
report synthesis. `/query` and `/rag` used a second, dense-only
implementation, so 679 documents collected across 62 jobs sat physically in
one index and logically in 62 silos, and the FTS5 needle channel that lifted
exact-term hit@4 from `0.6923` to `1.0` was unreachable from any corpus-wide
query.

`ScopedRetrievalService.retrieve` now takes `job_id=None` to mean the whole
corpus. The job id is a filter, not a mode: the same fusion, per-source caps
and needle channel run either way. The dense-only path in `app/query.py` is
deleted, not deprecated.

- **`/query`'s `topic_filter` and `tags_filter` survive, relocated.** They
  were Qdrant payload conditions; they are now resolved to a document scope
  from `job_sources` (`documents_matching`). The lexical channel has no
  payload to filter on, so a payload filter would have narrowed one channel
  and not the other. Verified against live metadata: "Kubernetes pod
  autoscaling and resource management" → 122 documents, tag `llm` → 9, an
  unmatched topic → 0 (not the whole corpus).
- **Job-scoped retrieval is provably unchanged.** Retrieval was fingerprinted
  over the ordered `(document_id, chunk_index, score, channels, rrf_score)`
  list for the six largest jobs — 360 selected chunks — under the deployed
  image and the new one. All six SHA-256 digests match exactly. (The earlier
  acceptance wording, "report synthesis is byte-identical", was not
  checkable: synthesis is LLM-driven. This is the deterministic thing
  underneath it.)
- **Corpus scope sends no identity filter.** Enumerating all 679 ids would be
  equivalent but grows an unbounded query with the corpus. An *empty* list
  remains an error in both `search_evidence` and `search_chunks` — that means
  a caller expected a scope and lost it, and silently widening to the corpus
  is exactly the failure the guard exists to catch.
- **Retrieval rows are projected, not whole.** The corpus holds 44 MB of
  markdown; `SELECT *` would have decoded all of it on every query.
  `documents_for_job` and `all_documents` now select identity and title only.
- **Scores report the ranking that produced them.** Under fusion `/query`
  returns the RRF score rather than a cosine — a similarity that contradicts
  the ordering would be worse. `hub_retrieval_score` still observes only
  candidates with a real cosine, and query latency moved off
  `hub_embedding_duration_seconds` (which timed the whole retrieval under a
  name meaning one part of it) onto `hub_retrieval_duration_seconds`.

Live proof against the real corpus, new image on the deployed Qdrant and
Ollama with a copy of the document store: "how does reciprocal rank fusion
combine rankings" selected 64 chunks from **33 sources**, "kubernetes
observability tracing" 65 from **42 sources**, both channels contributing and
fusing. Under the old code these queries could reach one job's sources at
most.

Prerequisite fact confirmed before deploying: all 68,072 Qdrant points carry
a `document_id` and every one resolves in SQLite, so corpus-wide retrieval
drops nothing. (33 retained documents have no Qdrant chunks — deduplicated
sources — and remain lexically reachable.)

## Live report generated through the deployed judge gate (2026-08-13)

The judge pivot had been proven structurally but no report had ever been
produced end-to-end through the deployed gate. One small research job on a
fresh topic now closes that gap.

Job `24a8a471-2d83-4e2c-b6e2-f719c53c68f9`, topic "Postgres logical
replication for major version upgrades with minimal downtime" (`depth=6`,
`max_sources=12`, `per_domain_limit=2`) — deliberately unrelated to the
attempt-11 clinical-AI corpus. Ingestion completed in ~39 s (6 retained
sources from 29 policy-accepted candidates, 446 chunks, 0 duplicates,
`robots_respected: true`); synthesis completed on **attempt 1** in ~105 s.

Gate behavior, audited from the worker's `judge_verification_diagnostic`
records:

- **8 metered judge calls** (4 single-span claims + 4 cross-document pair
  claims), well inside the 8+8 drafting caps (`MAX_SPAN_CANDIDATES`,
  `MAX_DISAGREEMENT_PAIRS`); no correction pass was needed, so
  `MAX_CORRECTION_CLAIMS` was never spent.
- **Every one of the 8 verdicts reported `served_model: "MiniMax-M3"`
  exactly.** No served-model drift; the v4 re-baseline trigger is not tripped.
- The gate discriminated rather than rubber-stamping: 6 claims accepted, 2
  pair claims rejected `padding_reference` (a reference judged unnecessary to
  the claim) — the same padding-rejection behavior the v4 blind final
  measured at 1.0.
- Two pair claims verified with per-ref necessity and **both citations are
  displayed** (`[S4][S5]`). Because cross-document pairs *were* available,
  the standing cross-source disclaimer is correctly absent; the disagreements
  section carries the ordinary "no material source disagreements" empty
  state instead.
- The report discloses its own gaps: 8 candidate sentences yielded no
  verified claim (`declined_pair=2`, `declined_span=4`, `padding_reference=2`).

Isolation verified after the run: the attempt-11 report and source registry
remain byte-identical (`068d60b2…`, `d6748d76…`), the v4 seal file is
unchanged (`762e7a19…`), and the only state movement is the new job's own
(67 → 73 documents, 13 → 14 reports).

**Single-query acquisition baseline (for HUB-024):** this job issued exactly
one SearXNG query and retained **6 sources across 6 distinct domains**
(`docs.aws.amazon.com`, `pganalyze.com`, `postgres.ai`, `runebook.dev`,
`www.pgedge.com`, `www.pistack.xyz`). Any query-planning comparison measures
against these numbers on this topic.

## Claim gate: MiniMax M3 judge deployed; NLI stack decommissioned (HUB-034)

Deployed 2026-08-12. The claim gate for report synthesis is the MiniMax M3
LLM-as-judge faithfulness gate (`app/judge_gate.py`), validated by the sealed
v4 blind evaluation (content `21465f6e…`, labels `632c30c3…`, results
`7c9ed9ac…`; all gates passed — see backlog HUB-036). The `claim-verifier`
service, `LocalClaimVerifier`, and the baked DeBERTa weights are removed; the
research-hub image no longer carries PyTorch (image ~0.4 GB, previously
multi-GB). The compose default topology is seven containers.

**The sealed v2 NLI evaluation is hereby RETIRED, explicitly and not
silently:** its result described the decommissioned
`MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli` gate at threshold 0.97 and no
longer describes any deployed behavior. Its fixtures, seal hashes, and
stdlib validation tests remain in `tests/fixtures/claim_support_*` as the
audit record. The consumed v3 blind set remains retired on the archived
branch `hub-032-cross-source-disagreement`. The active seal is v4
(`tests/fixtures/judge_seal_v4.json`, status `measured`); it records the
judge configuration fingerprint and the served model version (`MiniMax-M3`),
and a served-model change requires a fresh blind set before the gate is
trusted again.

Rider changes deployed with the same rebuild:

- FastAPI 0.115.5 → 0.141.1 (Starlette 1.6.0) — the upgrade deferred at
  HUB-012 to ride the next verifier rebuild; lockfile regenerated with
  hashes, `pip check` clean.
- Cross-source assessment (HUB-032): synthesis drafts bounded cross-document
  span pairs (ranked by shared vocabulary, at most 8) alongside single-span
  claims; pair claims are judged with per-ref necessity, verified
  disagreements display both citations, and the standing disclaimer is
  emitted only on reports where no cross-document pair was available.
- Judge configuration is required at startup: `MINIMAX_SUBSCRIPTION_KEY`
  must be present (compose `${VAR:?}` and config validation); judged
  evidence spans leave the machine (see `docs/NETWORKING.md`).

## Query planning deployed and measured — breadth met, two findings (HUB-024)

Operator-approved and deployed 2026-08-13: `REPORT_QUERY_PLANNING=true` in
`.env`, both application images rebuilt from the clean tree, research-hub and
research-worker recreated (no other container touched). Deploy verified:
`query_plan.py`, `research.py`, `config.py`, `judge_gate.py` and
`synthesis.py` SHA-256-identical across hub, worker and HEAD; `/readyz`
all-true at capability `all`; attempt-11 report and registry byte-identical
(`068d60b2…`, `d6748d76…`); v4 seal unchanged (`762e7a19…`); Redis queues
empty.

**Stage-3 measurement.** Job `b379fb3e`, same topic and same parameters as the
`24a8a471` baseline (`depth=6`, `max_sources=12`, `per_domain_limit=2`):

| | Baseline (1 query) | Planned (12 queries, 3 rounds) |
|---|---|---|
| Retained sources | 6 | 15 |
| Distinct domains | 6 | 14 |

The breadth acceptance criterion is met. Note the comparison is not
cost-neutral on the crawl axis: `depth` is a per-round allowance, so the
planned job's cap was 18 crawls against the baseline's 6. Judge calls stayed
inside the unchanged drafting caps.

Admission behaved as designed — six facets admitted at cosine 0.58–0.83, one
refused `redundant` at 0.8549 (the threshold discriminating at the margin),
one refused by the search-budget rail.

**Finding 1 — `PLAN_NOVELTY_MIN` could never fire; the metric is now
retired.**
Round novelty measured 1.0 / 1.0 / 0.983 and the run stopped on the
`max_rounds` rail, never on saturation. Cause: novelty was measured over the
whole policy-accepted candidate pool (59–93 URLs per round) while a round only
fetches `depth` documents, so almost every candidate is unseen and the ratio
pins near 1.0 regardless of real saturation. The denominator is now the
round's top-`depth` fetch window. That made the number honest but not useful,
and a prior-art pass the same day retired it as a control signal altogether:
the signal is structurally incompatible with distinctness-based admission.
Saturation now lives in facet-coverage space, with no tuned threshold at all
(see below). Novelty is still recorded per round for calibration.

**Finding 2 — facets drift off-topic as rounds progress; fixed the same
day by the two-sided admission bar and pre-issue QPP (see below).** The planner
proposed queries naming tools that do not exist (`crdb-migration-tools`,
`luupgtool`), and later-round facets pulled in HAProxy management docs,
pgBackRest release notes, a Barman manual and Datadog/Netdata monitoring pages
— none of which address Postgres major-version upgrades. Breadth rose while
relevance fell. Marginal-distinctness admission selects for *divergence*, and
at the tail the most distinct candidate is often the least relevant; a
relevance floor (not just a distinctness ceiling) is the indicated fix. This
is the PRD's named load-bearing risk, observed.

Report quality reflects the drift: the planned report's verified findings are
thinner and less on-topic than the baseline's, and no cross-document pair
verified. Per the PRD's acceptance framing (DeepWeb-Bench: retrieval is 12–14%
of errors) this does not fail the breadth criterion, and the judge gate held
throughout — nothing unsupported was accepted.

**Judge behavior during the measurement.** The first synthesis attempt failed
closed with `malformed_output`: MiniMax returned content that was not a single
JSON object (`Extra data: line 1 column 11`). The gate refused it rather than
guessing, the report stayed retryable, and one authorized retry produced the
completed attempt-2 report. The claim gate and its v4 seal were not modified
(out of scope). One consequence worth recording: because the per-claim
diagnostic is emitted only after a batch completes, the failed attempt logged
no `served_model` values, so served-model auditing has a gap on failed
batches.

## Model context raised to 16K — the evidence ceiling lifted (2026-08-13)

`OLLAMA_CONTEXT_LENGTH` and `MODEL_CONTEXT_TOKENS` both 8192 → 16384. They
must move together: the client fails closed on prompt truncation if the app
packs more than Ollama will accept.

**The GPU gate holds.** `qwen3.5:9b` stays **100% GPU** at 16384, 6.0 GB (up
from 5.7), warm throughput **104.0–104.4 tok/s** against a 104.6 baseline at
8K. The interactive deployment gate that Qwen3.6 failed is not threatened.

**Measured on one job re-synthesised three times over identical evidence**
(job `bc3e5297`, 22 sources; report retry runs synthesis only, no re-crawl):

| Context / drafting cap | Chunks selected | Spans offered | Drafted | Findings |
|---|---|---|---|---|
| 8K, 24 spans | 7 | 23 | 23 | 15 |
| 16K, 24 spans | 15 | 64 | 24 | 19 |
| **16K, 40 spans** | **15** | **64** | **40** | **30** |

Findings doubled, 15 → 30, on the same corpus. Doubling the context let
`pack_evidence` select 15 chunks instead of 7, raising span supply from 23 to
64; `REPORT_MAX_SPAN_CLAIMS` 40 then converted that supply into verified
claims. Pairs are 20 and the findings cap 40, which no longer binds at 30.

Drafting binds again (40 of 64 spans) at a ~50% verification rate, so further
raises have diminishing returns at proportional metered cost.

`/query` and `/rag` were smoke-tested after the change and behave as before —
see HUB-041 for a pre-existing `/rag` default-budget defect the smoke test
exposed.

## Report drafting scaled to the evidence ceiling (2026-08-13)

Two successive raises, each sized from measurement rather than guessed, on the
same Postgres topic:

| | Before | Display cap 20 | Drafting 24+16 |
|---|---|---|---|
| Spans offered / drafted | 25 / 16 | 25 / 16 | **23 / 23** |
| Findings displayed | 12 (capped) | 15 | **18** |
| Cross-source pair findings | 1 | 1 | **3** |
| Verified claims withheld | 3–4 | 0 | **0** |
| Report | ok, 1st | ok, 1st | **ok, 1st** |

`REPORT_MAX_FINDINGS` 12 → 20 → 30 removed the display cap as the limiter;
`REPORT_MAX_SPAN_CLAIMS` 16 → 24 and `REPORT_MAX_PAIR_CLAIMS` 12 → 16 then
consumed the evidence that was already being retrieved and discarded. Neither
change touched the gate's judging contract.

**The ceiling is now span supply, not any cap.** `drafted_span_count` equals
`span_count` at 23, so every offered span is drafted. Supply is set upstream by
`pack_evidence` fitting evidence into the 8K model context less a 2048-token
answer reserve, which is why only 7 chunks are selected from 120 retrieval
candidates. Raising the drafting caps further buys nothing.

**The next lever, and its cost.** More spans requires a larger
`MODEL_CONTEXT_TOKENS`, which must move together with Ollama's own context or
the client fails closed on prompt truncation. That risks the GPU-placement
gate `qwen3.5:9b` currently passes at 8K (~102 tok/s fully on GPU; Qwen3.6 at
45% CPU managed 3–4 tok/s and failed the interactive gate). It is a deliberate
operator decision, not a config nudge.

Metered cost rose with drafting — roughly 40 drafted claims per report against
28 — and every cap is env-tunable to walk back without a rebuild.

## Judge reliability: token allowance and timeout sized together (2026-08-13)

Three configurations, measured rather than reasoned about. The last two used
ten report retries each — retry re-runs synthesis only, so it exercises the
judge repeatedly without adding search pressure and isolates judge failures
from acquisition failures.

| Allowance / timeout | Outcome |
|---|---|
| 4096 tokens / 60 s | ~17% `empty_content` (1 of 6 full jobs) |
| 8192 tokens / 60 s | 20% `timeout` (2 of 10 retries) |
| **8192 tokens / 180 s** | **0 failures (10 of 10 retries)** |

**The first fix only moved the failure.** Raising the allowance was correctly
diagnosed — a failure logged `finish_reason=length` after 19,637 characters of
reasoning, roughly 4,900 tokens against a 4,096 allowance — but the two
parameters are coupled, and 60 s had been chosen when the allowance was 2048.
Permitting four times the output without extending the time to produce it
traded `empty_content` for `timeout` at a slightly worse rate. Only the
ten-retry test exposed that; the single prior failure could not have.

With both sized together, the previously-failing Kafka job now completes
consistently at 33–40 findings, and no `empty_content` or `timeout` appears in
the logs across the whole run.

**How much this proves.** Ten clean retries are consistent with a true failure
rate up to roughly a quarter, so this is not proof of zero. The confidence
comes from mechanism as much as count: both observed failure modes had
identified causes, and both causes are now addressed. Judge calls remain
sequential, so the raised timeout is worst-case latency per slow claim, not
per report.

Findings across the ten retries ranged 3–40, tracking corpus quality rather
than corpus size.

## Keyed search fallback deployed (ADR-002 stage 2, 2026-08-13)

Serper runs behind SearXNG, engaging only when a query returns nothing — what
a fully blocked engine pool looks like. Keyed from `SERPER_API_KEY` and inert
when unset, so acquisition is unchanged without it. It returns URLs and
snippets only; this stack does its own crawling, so providers bundling page
content would be paid for output it discards.

**Verified by removing SearXNG.** With the container stopped a job recorded
`search_providers: {"serper": 4}`, retained 17 sources and completed its
report on `kubernetes.io`, `docs.cloud.google.com` and `datadoghq.com`. With
SearXNG restored the next job recorded `{"searxng": 3}` — primacy returns
automatically. The key reaches no log line and is excluded from the config
repr.

Failure is legible rather than silent: a quota error or transport failure
returns no results and is logged with its cause, unlike the CAPTCHAs this
exists to survive. Fallback URLs pass through the unchanged source policy and
SSRF vetting.

**The live contract exposed a defect mocks could not.** Serper returns dates
as `"Oct 31, 2019"`; neither ISO nor RFC-2822 parsing accepts that, so every
result parsed as undated and any job setting `freshness_days` would have
silently rejected all of them. `parse_source_date` now handles human-readable
formats.

## Search stage 1: pacing and pre-crawl ranking (ADR-002, 2026-08-13)

Two free changes, both previously untried, decided in
`docs/ADR-002-search-provider-strategy.md`.

**Pacing.** The planner fired a plan's 3–6 queries in immediate succession
and jobs ran back to back; roughly 250 queries in bursts blocked four of six
engines. `SEARCH_PACING_SECONDS` defaults to 2.0 — seconds per job against a
~180 s job.

**Pre-crawl ranking.** The crawl cap decides which candidates are fetched, so
their order decides what the budget buys. Each facet's results are now ordered
by title+snippet relevance to the topic before interleaving. Deliberately
ranking rather than thresholding: a cutoff on snippets would need its own
calibrated number, and ranking wastes no crawl while discarding nothing.
Ranking is within each facet, never across, so interleaving still shares the
budget; a failed embed leaves the order untouched.

Measured on the topic that previously behaved worst:

| "Rust async runtime, tokio vs async-std" | Before | After |
|---|---|---|
| Documents crawled | 36 | 17 |
| Retained after the post-crawl screen | 4 | **17** |
| Discarded as off-topic | **32** | **0** |

The earlier run fetched `dictionary.com`, `thefreedictionary.com` and
`webmd.com` before discarding them. With ranking the budget went to the top of
71 scored candidates and every fetched document survived, retaining
`tokio.rs`, `docs.rs`, `users.rust-lang.org` and similar.

Stage 2 — adding Serper as a fallback behind SearXNG — remains deferred
pending an operator spend decision. Brave Search API stays rejected pending a
licensing question: its terms restrict storing API results, and this system
exists to build a persistent corpus.

## Widened search pool, load-tested (HUB-042, 2026-08-13)

Six fresh topics run back to back, probing engine health before each job so
degradation is measured rather than inferred.

| # | Topic | Engines responding | Sources | Screened out | Findings | Report |
|---|---|---|---|---|---|---|
| 1 | SQLite WAL tuning | bing, ddg | 6 | 0 | 22 | ok |
| 2 | Kafka rebalancing | brave, bing, ddg | 20 | 8 | — | **failed** |
| 3 | Rust async runtimes | bing, brave | 4 | 32 | 10 | ok |
| 4 | TLS rotation | bing | 5 | 5 | 19 | ok |
| 5 | Elasticsearch ILM | bing | 9 | 7 | 31 | ok |
| 6 | gRPC load balancing | bing | 11 | 0 | 3 | ok |

**Acquisition never failed — 6 of 6.** Before widening, a single CAPTCHA
produced "No search results found" and killed the job. Here the pool degraded
from three responding engines to bing alone across the run, and every job
still acquired. That is the resilience the widening was for; the ceiling is
raised, not removed, and bing is now carrying the load alone.

**The widened pool trades precision for availability, and the source screen is
what makes that trade viable.** Job 3 dropped 32 of 36 documents, which looked
alarming until the scores were read: bing and brave returned dictionary
definitions of "rust" and "async" plus a WebMD page. The screen kept
`rust-lang.org` and Wikipedia and dropped `dictionary.com`,
`thefreedictionary.com` and `webmd.com`. It was correct. The thin four-source
corpus reflects poor search results, not an over-aggressive filter — so the
earlier worry that HUB-038's threshold was invalidated by the engine change is
**not** borne out; if anything the screen matters more now.

**One report failed, on `empty_content`.** The judge spent its whole token
budget reasoning and returned no verdict, at the raised 4096-token allowance —
1 of 6 reports, cleanly failed and retryable. `RESPONSE_MAX_TOKENS` is the
lever if that rate proves too high; the 16K context leaves room for it.

Findings ranged 3–31 per report, tracking corpus quality rather than corpus
size: job 6 retained 11 sources but yielded 3 findings, job 5 retained 9 and
yielded 31.

## Why cross-source disagreements never fire (HUB-040, 2026-08-13)

Two hypotheses had opposite fixes, and they had been conflated: either the
corpora genuinely agree, or conflicts exist and pair selection never surfaces
them. Measurement settled it.

- **The detector works** — 5 of 5 on hand-written cases, correctly flagging a
  negation, an inverted claim and conflicting numbers.
- **The corpora contain no conflicts** — 450 cross-document span pairs sampled
  *uniformly at random*, bypassing the production ranking: 0 of 200 on the
  Postgres corpus, and 1 of 250 on the deliberately contested microservices
  corpus which on inspection is a false positive (both spans say microservices
  are more complex).

**Pair selection was never the bottleneck.** A contrast-based selector would
have changed nothing, because the pairs it would have promoted do not conflict
either. The earlier diagnosis recorded here was wrong.

**The likely cause is acquisition.** Search ranks by relevance, so results
reflect the dominant framing of a topic; every facet asks about the topic and
none asks for the minority view. The corpus is assembled to be representative,
and disagreement needs it to be adversarial. The untested fix is a
deliberately contrarian facet, and the base-rate probe is the instrument for
judging whether it works.

Until then, "No material source disagreements were identified" should be read
as a statement about the corpus, not about the sources.

## End-to-end validation of the assembled stack (2026-08-13)

Every component had been validated in isolation; none had been validated
together. Four fresh topics through the full deployed pipeline — query
planning, widened engine pool, source screening at 0.54, 16K context, 40-span
drafting, conflict-first pair drafting, judge at 8192 tokens / 180 s.

| Topic | Facets | Stop | Kept / dropped | Findings | Pair-cited | Disagreements |
|---|---|---|---|---|---|---|
| Debezium CDC connector config | 5 | `coverage_complete` | 12 / 16 | 27 | 5 | 0 |
| HTTP caching, ETag | 4 | `coverage_complete` | 15 / 6 | 27 | 4 | 0 |
| Blue-green deployment | **1** | `single_round` | 3 / 3 | 22 | 8 | 0 |
| Prometheus alerting rules | 4 | `coverage_complete` | 10 / 13 | 13 | 7 | 0 |

**4 of 4 completed on the first attempt**, no acquisition failures and no
judge failures. The screen dropped 3–16 documents per job, consistent with the
lower precision of the widened engine pool. Findings ranged 13–27.

**Collapse fired for the first time.** The blue-green topic admitted one facet
and issued a single search. HUB-039 had recorded collapse as measured false on
0-of-8 evidence; it is rare rather than impossible, and that record is
corrected.

**Disagreements remain zero, and the cause is now narrowed.** The conflict
detector ran on every pair — 75 calls across the four jobs — and judged "no
conflict" each time. A follow-up retry on a corpus chosen to be contested
(microservices versus monolith, 20 pairs) also produced none. With the
drafting escape hatch removed, the remaining cause is pair *selection*, which
still ranks by shared vocabulary and therefore surfaces pairs that combine
rather than pairs that conflict. See HUB-040.

## Source screening re-validated on the bing-era search mix (2026-08-13)

The engine widening changed what search returns, so the screen's calibration
was re-checked rather than assumed. Screened-out documents never reach the
corpus, so four fresh jobs were run with `PLAN_SOURCE_RELEVANCE=0.0` to retain
the negatives deliberately: 55 documents, labelled by the same procedure as
the v1 reference set.

**Search precision has collapsed, and the screen is now load-bearing.** The
bing-era sample is 8 on-topic, 17 marginal, **28 off-topic** — 15% on-topic
against 62% in the duckduckgo-era reference set. Widening the engine pool
bought availability at a real cost in precision.

**The screen separates that mix perfectly.** AUC on-vs-off is **1.000**:
on-topic scores run 0.765–0.818 while off-topic tops out at 0.683, a clean gap
with nothing in it.

| Threshold | On-topic kept | Off-topic dropped |
|---|---|---|
| **0.54 (deployed)** | **100%** | **75%** |
| 0.58 | 100% | 89% |
| 0.62 | 100% | 93% |

**0.54 is kept unchanged.** It already retains every on-topic document in the
new mix while removing three quarters of the junk. Raising it would drop more
off-topic here, but only 8 on-topic documents support that tail, and the much
larger v1 reference set contains on-topic documents scoring below 0.58 — so
tightening would trade a measured risk to recall for an improvement this
sample cannot justify.

Taken together with the earlier load test, the two changes are complementary:
widening the pool keeps acquisition alive when engines block, and the screen
absorbs the precision loss that comes with it.

## Source screening calibrated and enabled (HUB-038, 2026-08-13)

Enabled at `PLAN_SOURCE_RELEVANCE = 0.54`, calibrated on a 494-document
labelled reference set covering every document retained across all 38 jobs and
20 topics. Labelled on/marginal/off-topic by MiniMax with a purpose-built
prompt, not the sealed claim-gate prompt. A reference set, not ground truth: 7
of 44 repeat-labelled documents disagreed (16%).

| Scoring variant | AUC (on vs off) |
|---|---|
| Opening text, topic-anchored | 0.872 |
| **Windowed, topic-anchored (deployed)** | **0.875** |
| Windowed, facet-anchored | 0.741 |

At 0.54 the screen keeps **98.2% of on-topic documents** while removing
**35.5% of off-topic** ones; no adequately-sampled topic falls below 91%
recall, and per-topic AUC median is 0.975. Dropping nothing on a clean run is
expected, since off-topic documents are only ~14% of a corpus.

The evaluation reversed two earlier conclusions drawn from unlabelled runs:
the screen does separate, and facet anchoring — introduced to fix one
ambiguous topic — is worse overall and was reverted as overfitting to n=1.
Windowed probes turned out to be within noise of the simple opening probe.

The supposed ambiguity failure was not one: on "Transformer efficiency
improvements" the labeller independently marked electrical-transformer pages
on-topic, agreeing with the screen. The topic reads both ways; the earlier
report assumed an ML sense the topic never stated.

## Judge reliability after HUB-037 (2026-08-13)

The `reasoning_split` fix introduced a second failure mode that only surfaced
under the campaign: with reasoning routed to its own field, a reply can carry
`reasoning_content` and no `content` at all when deliberation consumes the
token budget, which raised `KeyError` and surfaced as `malformed_output`.
Missing or blank content now fails closed as a distinct `empty_content` reason
logging `finish_reason` and reasoning length, and `RESPONSE_MAX_TOKENS` rose
2048 → 4096 because reasoning counts against the budget even when split out.
Verified: two further live reports completed on the first attempt.

## Eight-topic evaluation campaign (2026-08-13) — one blocking defect found

A deliberately varied campaign against the deployed stack: six topics chosen
to stress different failure modes, plus a depth sweep. 8 jobs, 158 retained
sources. Raw per-job records were collected by a harness reading only the
public API.

| Topic class | depth | Facets | Sources / domains | Findings | Report |
|---|---|---|---|---|---|
| narrow (Redis `appendfsync`) | 6 | 4 | 21 / 18 | 12 | ok |
| broad (K8s autoscaling) | 6 | 3 | 17 / — | — | **failed** |
| contested (microservices vs monolith) | 6 | 3 | 15 / 13 | 7 | ok |
| ambiguous ("transformer efficiency") | 6 | 2 | 9 / — | — | **failed** |
| thin (OCaml effect handlers) | 6 | 3 | 11 / — | — | **failed** |
| cross-domain (detecting p-hacking) | 6 | 4 | 19 / 18 | 12 | ok |
| K8s autoscaling | 3 | 4 | 10 / 9 | 12 | ok |
| K8s autoscaling | 12 | 6 | 56 / 45 | 12 | ok |

**Blocking defect — 3 of 8 reports failed (37.5%), all one root cause.
FIXED the same day; see HUB-037 below.**
MiniMax M3 leaks the opening of its JSON verdict *into* its reasoning block:

```
<think>
…reasoning…{"
</think>
accepted": true, "reason": null, "refs": [{"id": "R1", "necessary": true}]}
```

`_THINK_BLOCK` strips `<think>…</think>` and takes the `{"` with it, leaving
`accepted": true…`, which cannot parse. The earlier `Extra data: line 1
column 11` failures were the same leak splitting at a different point. The
gate fails closed and the report stays retryable, so nothing unsupported is
ever published — but roughly a third of reports need a retry. It was left unpatched during the campaign so the batches stayed
comparable, then fixed immediately afterwards.

**Fix (HUB-037, deployed 2026-08-13).** The root cause was an integration
gap, not a parser bug: MiniMax M3 defaults to `thinking:{"type":"adaptive"}`
when the parameter is omitted and delivers reasoning inline. The request now
sends `reasoning_split: true`, routing reasoning to its own field.
Deliberately not `thinking:{"type":"disabled"}` — the sealed v4 evaluation
measured this gate with reasoning ON, so disabling it would change how the
judge decides and invalidate the seal; splitting changes only delivery, and
the seal's recorded `system_prompt_sha256` is unchanged. Because the leak
then moved to a third cut point, the parser also reconstructs a swallowed
object opening against the fixed verdict schema, accepting only
reconstructions that yield exactly the three expected keys.

**Verified:** all three previously-failing topics re-ran clean on the first
attempt, zero `malformed_output` across 27 verdicts, 303 tests green, v4 seal
and attempt-11 artifacts byte-identical.

**`PLAN_FACET_RELEVANCE = 0.55` is well calibrated and transfers.** Across 21
admitted facets the topic cosines ran 0.550–0.818 (median 0.695); the nine
facets refused `off_topic` ran 0.425–0.536. The threshold sits exactly in a
clean empirical gap, and it held on the non-CS cross-domain topic too. This
closes the PRD's transfer question with evidence rather than assumption.

**Two acceptance claims do not hold in practice:**

- *Collapse never fired* — 0 of 8 jobs collapsed, including the deliberately
  narrow Redis `appendfsync` topic, which admitted 4 facets and retained 21
  sources across 18 domains. The claimed "a narrow topic issues exactly one
  search and behaves as it did before planning" is not what the deployed
  system does; the planner always finds distinct-enough angles.
- *The multi-round machinery never engages* — all 8 jobs stopped
  `coverage_complete` in round 1. Under a per-facet crawl allowance, round 1
  always covers the plan, so the gap pass, plateau detection and
  `PLAN_MAX_ROUNDS` are effectively dead code at current settings.

**Source-level relevance is unguarded.** The relevance floor admits *facets*,
not *sources*. A relevant facet can still return off-topic documents: the
Redis corpus pulled Couchbase and Databricks docs, and the Kubernetes corpus
pulled an NIH paper. Breadth is real but not uniformly on-topic.

**Depth scales cleanly and roughly linearly** in `depth × facets`: 10 sources
at depth 3, 56 sources across 45 domains at depth 12, the latter in 190 s.

**Report quality is bounded by span selection, not by the gate.** All five
successful reports hit the 12-finding display cap with 3–4 further verified
claims withheld, so the cap now binds. But the contested topic surfaced
findings like a market-size statistic irrelevant to architecture tradeoffs,
and **zero verified source disagreements appeared across all eight jobs** —
notable for a topic set that included one chosen specifically to elicit them.
The judge verifies faithfulness, not informativeness or topicality.

## Query planning measured at scale — acceptance met (HUB-024, 2026-08-13)

Five live runs on one fixed topic (`depth=6`, `max_sources=12`,
`per_domain_limit=2`), each a controlled repeat of the `24a8a471` baseline:

| Run | Queries / rounds | Sources / domains | Off-topic domains | Findings | Report |
|---|---|---|---|---|---|
| baseline (no planning) | 1 / — | 6 / 6 | — | 6 | ok, 1st attempt |
| 2 — novelty bug | 12 / 3 | 15 / 14 | ~4 | 6 | failed, ok on retry |
| 3 — novelty window fixed | 5 / 2 | 12 / 11 | 0 | — | failed |
| 4 — coverage stopping | 5 / 1 | 6 / 5 | 1 | 5 | ok, 1st attempt |
| **5 — scaled (`70a0de86`)** | **5 / 1** | **25 / 23** | **0** | **12 (+3 withheld)** | **ok, 1st attempt** |

**The acceptance criterion is met**: 25 retained sources across 23 distinct
domains against the single-query baseline's 6/6, with no off-topic
acquisition. One round, stopped by `coverage_complete`, 25 crawled from a pool
of 70. All five admitted facets scored topic cosine 0.65–0.79, none malformed.

Run 4 is the instructive one. It fixed drift and simultaneously *failed*
breadth (6/5, below baseline), because the per-round crawl cap was `depth`
regardless of facet count — a five-facet plan saw 100 candidates and fetched
6. The breadth in runs 2 and 3 had come from extra rounds spending extra
budget, not from better queries. Making `depth` the per-facet allowance is
what converted clean facets into a wide corpus.

Report quality rose with the corpus rather than despite it: 12 displayed
findings (3 more verified but withheld by display limits) against the
baseline's 6, including a verified cross-document pair citing both sources,
and the substantive sequence-replication caveat that the baseline also found.
All 15 logged verdicts reported served model `MiniMax-M3`.

Honest limits. One displayed finding is promotional filler ("Embrace logical
replication — your database operations will thank you for it!") that passed
the gate because it *is* faithfully entailed by its span: the judge verifies
faithfulness, not informativeness, so span selection — not the gate — is the
remaining quality lever. `PLAN_FACET_RELEVANCE = 0.55` is now calibrated from
measurement rather than guessed, but on one topic only.

Isolation after the run: attempt-11 report and registry byte-identical
(`068d60b2…`, `d6748d76…`), v4 seal unchanged (`762e7a19…`), corpus grew to
124 documents / 18 reports.

## Query planning: stopping and query quality rebuilt (HUB-024, 2026-08-13)

Merged after the measurement above, grounded in a second prior-art pass (11
further arXiv abstracts fetched and read; citations in the PRD). **Not yet
re-measured live** — the deployed stack runs this code, but no planned job has
been run against it, so the drift is fixed in design and in tests, not yet
demonstrated on a real corpus.

- **Stopping moved from document space to facet-coverage space** (RAVine,
  2507.16725). A facet counts as covered once a retained document came from
  its own search, recomputed over every issued query each round. Two rules,
  neither with a tuned threshold: `coverage_complete` (every issued facet
  answered) and `coverage_plateau` (a round raised the count by zero).
  `PLAN_NOVELTY_MIN` is retired and removed from config, compose and
  `.env.example`.
- **Rounds now exist to finish covering the admitted plan, not to invent new
  angles**, so one round is the normal case and extra rounds fire only when
  the crawl allowance could not reach every facet. If completion did not end
  the loop, a gap pass inventing facets would raise the count forever.
- **The LLM gap pass is advisory**, checked only after the arithmetic signal:
  sufficiency judgements measure poorly (RaCGEval 2411.05547, baselines at
  46.7%) and unaligned models default to answering rather than declining
  (2507.04976).
- **Admission is two-sided** — a facet must be distinct from the admitted set
  *and* above `PLAN_FACET_RELEVANCE` (0.35) to the topic. Distinctness alone
  selects for divergence, which is what produced the drift.
- **Pre-issue QPP** (Diaz, 1507.03928) rejects run-together identifiers,
  out-of-range word counts and non-alphabetic soup before a search is spent,
  tuned not to fire on `PostgreSQL`, `WAL`, `pg_upgrade` or `libc/libssl`. A
  collection-IDF predictor was deliberately not built: QPP predicts against
  the collection being searched, and sub-queries search the web while our
  document frequencies are local.
- **Judge parse failures now log the raw reply** (served model, length,
  SHA-256, bounded preview) on the failure path only. The verdict contract is
  untouched and still fails closed, so the v4 seal is unaffected.

`PLAN_FACET_RELEVANCE` is the one remaining guessed threshold and ships
deliberately permissive, with every candidate's topic cosine recorded in job
provenance so it can be calibrated rather than guessed twice.

293 tests green in-container, zero skips; ruff E9/F clean; compose valid.

## Query planning mechanism (HUB-024 stages 1–2)

Built 2026-08-13, deployed the same day (see the section above).
`app/query_plan.py` holds the adaptive query planner: facet admission by
marginal distinctness, cross-facet and cross-round canonical dedup, budget
rails (stage 1), and gap-driven rounds that stop on novelty saturation with
the reason recorded (stage 2).

`REPORT_QUERY_PLANNING` defaults to `false` in config, compose and
`.env.example`; with it false the acquisition path issues the single search
it always did and never invokes the planner. Breadth, when enabled, is
emergent from `PLAN_FACET_DISTINCT` (0.85) rather than any fixed sub-query
count — `PLAN_MAX_FACETS` (8) is a rail against a pathological planner. The
crawl cap is still `depth`, so breadth would arrive at constant crawl and
judge cost; the claim gate, the v4 seal and judge-call volume per report are
untouched.

Rounds wrap search and crawl only — ingestion is untouched, so worker lease,
heartbeat, timeout, retry and idempotency semantics are unchanged. Job
progress records every query issued, every admission decision including
refusals, each round's novelty yield, and the stop reason (`single_round`,
`saturation`, `coverage`, `max_rounds`, `budget`, `planner_unavailable`), so
a degenerate plan is visible in provenance rather than inferred from a thin
report.

Verified: 275 tests green in-container with zero skips (70 new, offline and
deterministic), `ruff --select E9,F` clean, `compileall` clean, `docker
compose config` valid. With the flag false the acquisition path issues the
single search it always did and never invokes the planner, so reverting is a
one-line `.env` change plus a container recreate — no rebuild.

## Deployment model

The repository is stored on the Windows filesystem under OneDrive. Docker
Desktop runs the Linux containers through WSL2; application images and Docker
named volumes are therefore separate from the repository checkout. Editing
`research-hub/app` does not update the live API until the research-hub image is
rebuilt and its container is recreated.

Ollama is the only service with NVIDIA GPU access and uses the workstation's
RTX 3080 Ti. Qdrant corpus data, Redis job metadata, Ollama models,
Research-Hub documents, Crawl4AI, and optional UI state live in Docker named volumes.

## Model stack and WSL envelope

WSL is capped at 20 GB RAM, 12 logical processors, and 2 GB swap. Ollama uses a
30-minute keep-alive, one parallel request, and one loaded model. Research Hub,
Research Worker, and Crawl4AI share the same configurable `LLM_MODEL`.

The installed generation inventory is `qwen3.5:9b`, `qwen3.6:27b`, and the retained
`qwen2.5:7b` baseline. `nomic-embed-text`, its 768-dimension configuration, and the
`research_corpus__nomic_embed_text_768` Qdrant collection remain unchanged.

The measured default is `qwen3.5:9b`. At 8K it stayed fully on the GPU and generated
about 102 tokens/s. Qwen3.6 remains available for explicit offline/high-quality jobs,
but its 45% CPU / 55% GPU placement produced only 3.35-4.12 tokens/s and failed the
interactive deployment gate. Higher-context tuning was therefore skipped and the
operating context remains 8K. See `docs/MODELS.md` and the local benchmark artifact
under `.hermes/benchmarks/` for measurements and switching commands.

Qwen3.5 and Qwen3.6 default to thinking mode in Ollama. The Research Hub client
explicitly disables thinking for its answer-generation paths so response text is not
lost in Ollama's separate `thinking` field.

## Running services

The seven-container default Compose topology is deployed locally (the
claim-verifier service was decommissioned by HUB-034). At the latest runtime
check, Research-Hub was healthy and the dedicated worker was stable.

Research-Hub currently uses Ollama, Qdrant, Redis, SearXNG, and Crawl4AI.
The API only enqueues ingestion; the dedicated Research Worker claims and executes
jobs with leases, heartbeats, timeouts, bounded retries, and orphan reconciliation.
Open WebUI, Dozzle, Uptime Kuma, Prometheus, and Grafana are independent optional
profiles. Postgres was removed because it owned no application data.

HUB-002 is deployed: Redis, Qdrant, and Crawl4AI have no published host
ports. Research Hub, Open WebUI, SearXNG, Dozzle, and Uptime Kuma bind only to
`127.0.0.1`. Ollama also defaults to loopback, with an explicit
`OLLAMA_BIND_ADDRESS` opt-in for a trusted LAN or Tailscale interface. See
`docs/NETWORKING.md`. This workstation publishes the hub's Ollama on host port
11435 because Docker Desktop's separate model runner owns 11434. Credential
hardening remains unfinished.

## Research-Hub health contract

| Endpoint | Meaning | HTTP behavior |
|---|---|---|
| `/livez` | API process is serving requests | 200 while the process is live |
| `/health` | Backward-compatible alias of `/livez` | 200 while the process is live |
| `/readyz?capability=query` | Ollama and Qdrant can query the retained corpus | 200 ready; 503 degraded |
| `/readyz?capability=rag` | Ollama and Qdrant can retrieve and generate | 200 ready; 503 degraded |
| `/readyz?capability=research` | Redis, Ollama, Qdrant, SearXNG, Crawl4AI, and the judge claim gate (configuration check, no metered call) can support research | 200 ready; 503 degraded |
| `/readyz` | All Research-Hub dependencies | 200 ready; 503 degraded |
| `/health/full` | Diagnostic status for every dependency | 200 with `ok`, `degraded`, or `starting` in JSON |

The Docker healthcheck intentionally calls `/livez`; dependency degradation
must not cause Docker to restart a functioning API process. Operators and the
`research health` CLI use readiness instead. The CLI defaults to all
dependencies, prints failed service names, and exits nonzero when degraded.

Search or crawl failure does not make existing-corpus query/RAG unready.
Starting a new research job requires the complete research dependency set.

## Verified HUB-005 deployment

HUB-005 is implemented, tested, rebuilt, and deployed locally. Verification
performed against the rebuilt image:

- `/livez`, `/readyz`, `/readyz?capability=query`, and `/health/full` returned
  JSON responses successfully.
- The live `research health` command reported Ollama, Qdrant, Redis, SearXNG,
  and Crawl4AI ready.
- Ten automated tests passed, including degraded readiness, JSON
  serialization, synchronous Qdrant probing, query availability without
  search/crawl, CLI failure reporting, and Qdrant persistence coverage.
- The shared shell healthcheck uses Unix line endings so it executes correctly
  inside Linux containers when the checkout resides on Windows.

## Verified durable and idempotent ingestion

HUB-007 and HUB-008 are implemented, tested, rebuilt, and deployed locally.
API restarts do not own or interrupt running ingestion. Worker claims have expiring
leases and heartbeats; abandoned work is reconciled and retried, and permanent
failures reach a terminal state with the attempt error. Redis uses AOF and
`noeviction` for queue safety.

Canonical URLs plus content hashes produce stable document IDs, and stable chunk
IDs include the document, chunk index, and chunker version. Re-ingesting unchanged
content skips existing chunks; changed content is completely embedded before its
old chunks are removed. `DELETE /documents?url=...` removes every version/chunk for
a canonical source URL.

Important remaining limitations include hardcoded/default credentials, crawler
SSRF protections, backups, and CI coverage. Redis AOF improves
durability but is not a backup or a high-availability queue.

## Implemented API contract and observability

HUB-014 makes every public Pydantic model reject unknown fields. The unused
`ResearchRequest.time_limit` field was removed, and `/rag` now supports the same
`tags_filter` as `/query`. Deterministic tests validate documented payloads against
the OpenAPI models and assert clear 422 responses for unsupported fields.

HUB-015 adds request IDs, job correlation, secret-safe JSON logs, API and worker
Prometheus endpoints, version-controlled alert rules, and a provisioned Grafana
pipeline dashboard. The optional monitoring path and thresholds are documented in
`docs/OBSERVABILITY.md`.

HUB-014 and HUB-015 were rebuilt and deployed on 2026-08-09. Live verification
confirmed strict unknown-field rejection with HTTP 422, the API liveness response,
both Prometheus scrape targets, all three alert rules, and the provisioned Grafana
dashboard.

## Verified simplified Compose topology

HUB-016 is implemented and deployed locally. Default Compose starts only Ollama,
Qdrant, Redis, SearXNG, Crawl4AI, Research-Hub, and its worker. Open WebUI,
Dozzle, Uptime Kuma, and the Prometheus/Grafana pair are opt-in profiles. Postgres
is absent from Compose; its old named volume was retained rather than deleted.

All profile configurations rendered successfully. A stopped, already-pulled
default stack reached healthy API status in 39.4 seconds and used approximately
1,014 MiB in a post-start idle sample. See `docs/COMPOSE_PROFILES.md` for profile
commands, per-profile measurements, and measurement caveats.

## Verified RAG and source-policy hardening

HUB-018, HUB-020, and HUB-021 are implemented and deployed locally. RAG context
packing uses a conservative UTF-8-byte token upper bound, reserves model instructions,
question, and answer capacity, never slices an evidence entry, and returns only the
sources actually sent to generation. Retrieved text is explicitly delimited as untrusted
evidence; common injection patterns are labeled and neutralized in derived chunks while
the exact source remains inspectable in the SQLite document store. Custom system prompts
are denied by default and require an explicit trusted-local configuration opt-in.

Research jobs accept `allowed_domains`, `blocked_domains`, `per_domain_limit`, and
`freshness_days`. Search URLs are canonicalized and deduplicated before crawling,
Crawl4AI robots checking is enabled by default, and job progress records policy decisions.
Qdrant metadata separately exposes semantic score, source-quality score, publication/fetch
dates, freshness age, security labels, and robots-policy state.

## Persisted research synthesis

HUB-022 is implemented and deployed locally. Successful ingestion now produces a
durable Markdown report in the SQLite document store with scope, retained source list,
key findings, source disagreements, unknowns, and inline evidence IDs. Material findings
and disagreements are rejected unless they reference retained job documents.

`GET /research/{job_id}/report` and `research report JOB_ID` retrieve the stable artifact.
Failed synthesis is stored separately from completed ingestion and can be retried with
`POST /research/{job_id}/report/retry` or `research report JOB_ID --retry`; that path does
not invoke search, crawling, embedding, or Qdrant writes.

## Claim-support deployment and failed live gate

Material findings and disagreements now use private unique packed evidence IDs, exact spans,
and atomic support propositions. One shared CPU service verifies every required span link and
the complete claim against the span union with the frozen DeBERTa revision and `0.97`
threshold. Corrected output is independently reverified before citations or persistence.

The verifier is offline-only, batches eight checks, rejects inputs over 512 tokens without
truncation, and has a 2.5 GiB Compose memory cap. Research/all readiness requires its health
and exact revision; query/RAG readiness remains independent. Any neutral, contradiction,
low-confidence, unresolved, malformed, timeout, unavailable, over-budget, or revision-mismatch
outcome fails closed. A failed retry preserves the prior persisted report and source registry.

Commit `fb6366f` plus the reviewed uncommitted output-boundary fix is deployed to the verifier,
API, and worker. The verifier reports the exact frozen model/revision, runs with Hugging Face
and Transformers offline flags enabled, and is required by research/all readiness. The shared
Ollama client now fails explicitly on `done_reason=length` or prompt truncation, and API/worker
report completion is bounded at 2,048 tokens.

Ollama now runs with an explicit 8,192-token context. API and worker report generation use a
2,048-token completion allowance. One authorized retry advanced the authoritative report from
attempt 6 to attempt 7 and returned HTTP 200. Both the first generation and its one correction
completed without output-limit or context truncation.

Attempt 7 is completed but displays no material finding or disagreement: exact-span resolution
rejected all seven corrected claims before substantive NLI evaluation. The unsupported
SPIRIT-AI/TRIPOD-ML composite is absent, and the report explicitly records the omissions. Redis
queues, retained document/observation counts, Qdrant counts, and all three sealed evaluation
hashes stayed unchanged; worker logs show no ingestion activity. Phase 4 and the initial
Phases 0-4 modernization remain incomplete because at least one supported finding is required.
The conditional Phase 5 authorization was not activated, so Phase 5 is unstarted.

The next exact-span fix is deployed to Research Hub and Research Worker. Generated material
claims now select enumerated prompt-time span IDs instead of copying free-form evidence text;
the resolver maps each ID back to its original exact sanitized substring before the unchanged
NLI contract. Span IDs are trimmed before lookup, and validation exposes precise bounded
failure reasons rather than collapsing them into `unresolved_span`. Focused tests passed 19/19,
the complete Redis-15 suite passed 103 tests, the retrieval benchmark passed, and bounded
Hermes verification returned `ok=true`. No live retry followed this deployment: attempt 7 and
its six-source registry remain preserved, Phase 4 still requires one separately authorized
acceptance retry, and Phase 5 remains unstarted.

That single retry was subsequently authorized and consumed. Attempt 8 proved the span-ID path:
eight first-pass material claims resolved exactly and reached the frozen production NLI
verifier. Seven were neutral and one was low-confidence, so the one allowed correction ran.
The correction exhausted the 2,048-token output allowance and failed explicitly with
`done_reason=length`; no partial output was accepted. Attempt 8 is `failed`, while the attempt-7
Markdown and six-source registry remain byte-preserved. Queues, SQLite, Qdrant, worker activity,
and sealed hashes remain clean. Phase 4 still lacks a supported material finding, and no retry,
tuning, or Phase 5 work is authorized after this failure.

## Span-first claim drafting and gate alignment

Attempts 9 and 10 both produced zero verified claims. Attempt 10 was diagnosed from its
retained per-check diagnostics rather than repeated, and the causes were span
construction and generation order rather than retrieval or the NLI operating point.

Deployed changes:

- Span selection moved into `research-hub/app/spans.py`. Sentence bounds are
  abbreviation-aware, non-propositional spans are dropped before generation, and a
  sentence whose subject is an unresolved demonstrative is merged with its predecessor
  into a contiguous window or dropped when it is chunk-initial. Every offered span is
  still an exact substring of its sanitized chunk. On the six real attempt-10 chunks this
  cuts 46 offered spans to 8, removing the reference-list debris and the unresolvable
  demonstrative that caused four of the ten rejections.
- Generation is span-first. One bounded 256-token call drafts one claim from one exact
  span under a deletion-compression contract, replacing one 2,048-token call that drafted
  six claims and then chose spans for them. Wrong-span pairing is structurally impossible
  and the output-limit failure class that ended attempts 5, 6 and 8 is gone. A malformed
  or declined draft is isolated to its span instead of failing the report.
- The production verifier now judges a single-evidence claim with exactly the pair the
  sealed final evaluation measured. The redundant self-check was replaced by a string
  equality assertion and the duplicate span check was removed for single-evidence claims.
  Multi-evidence claims keep the per-span conjunct. Model, revision and threshold are
  unchanged, and all three sealed hashes still match.
- The research-hub Dockerfile bakes the pinned verifier snapshot above the app copy, so
  editing application code no longer invalidates the model-download layer.

Measured on the frozen attempt-10 evidence with the deterministic offline benchmark and
five live samples: junk rejection `0.741935`, exact substring rate `1.0`, critical span
recall `1.0`, and four to eight verified claims per run with at least one verified claim
in 5 of 5 runs. Two runs still had claims rejected by the verifier, so the gate is not
rubber-stamping. Attempt 10 produced zero verified claims from ten drafts on the same
evidence.

The automated gate is green: 118 tests with zero skips including Redis integration against
DB 15, critical Recall@4 `1.0`, citation validity `1.0`, duplicate rate `0.0`, clean
`pip check` and clean Compose validation.

Known limitation now disclosed in every report: cross-source disagreement is not assessed,
because each displayed claim must be entailed by one exact span from one source
(HUB-032).

## Phase 4 live acceptance passed (attempt 11)

The single authorized live retry on 2026-08-11 returned HTTP 200 in about 20.5 seconds
under correlation `9330511b-df54-4846-b09c-3d0e8a123736`. The authoritative clinical-AI
report advanced from attempt 10 (`failed`) to attempt 11 (`completed`) with six verified
cited material findings, each entailed by one exact evidence span at entailment
`0.986`-`0.995` against the frozen verifier's sealed `evidence_union` pair and `0.97`
threshold. One draft was correctly rejected as `neutral` (`0.0989`) for substituting
"CONSORT-AI extension" where the span said "CONSORT 2010"; its single correction redraft
declined, and two other spans yielded model declines — all disclosed in the report's
unknowns section.

Before the retry, all three service images were rebuilt from the clean tree at `53e7595`
and the deployed `spans.py`, `synthesis.py` and `claim_support.py` were verified
SHA-256-identical to HEAD in the hub, worker and verifier containers. The retry mutated
only the target report row: the six-source registry stayed byte-identical, SQLite (67
documents, 67 observations, 13 reports), Qdrant (24,465 points, 23,369 indexed vectors),
Redis queues (empty), and all three sealed evaluation hashes were unchanged, and worker
logs show no crawl, ingestion, embedding or upsert.

Phase 4 of the RAG synthesis modernization is closed with all five gate conditions green,
and HUB-019 is Done.

## Hybrid retrieval entry condition measured and met (HUB-017)

Phase 5 was initially recorded as not entered because critical Recall@4 was `1.0`, but
that number comes from a benchmark that replays pre-scored fixture candidates and never
runs an embedding or vector search — it cannot show a dense miss. The new live probe
`tests/benchmark_retrieval_exact_terms.py` drives the real embed-and-search production
path against 13 exact-term needle cases (checklist items, DOIs, consensus statistics,
software identifiers, each in at most 5 of 870 retained chunks) mined from the
authoritative corpus, validated by seven deterministic manifest tests and read-only
against all live state.

Measured 2026-08-11: dense-only hit@4 is `0.6923`. Three sentinels reached the
40-candidate pool but lost dense ranking (rank fusion recovers them); the DOI
`10.1136/bmj.g7594` sentinel never entered the pool (only lexical candidate generation
recovers it). The nine hits ranked 1–3, so dense remains the primary channel and the
gap is specifically exact identifiers.

HUB-017 is therefore Open with measured justification. Acceptance: hybrid hit@4 `1.0`
on the exact-term manifest with no regression in the dense-only report benchmark or the
sealed claim-support contract. Implementation is gated on the PRD design spike (SQLite
FTS5 vs Qdrant sparse vectors vs in-process BM25).

## Hybrid report retrieval deployed (Phase 5, HUB-017)

Phase 5 of the modernization is implemented, measured and deployed
(2026-08-11, ADR-001). Report retrieval now fuses dense embedding search with an
SQLite FTS5 needle channel under deterministic reciprocal rank fusion (`k=60`):

- `chunk_fts` lives in `documents.sqlite3`, holding sanitized derived chunks
  byte-equal to Qdrant payload text, written at ingestion, removed with document
  deletion, and rebuildable from retained documents alone via
  `python -m app.rebuild --lexical-only` (backfilled: 67 documents, 24,854 chunks).
- The lexical channel selects needle terms by document frequency measured within the
  retrieval scope (unigrams and adjacent bigram phrases, DF <= max(5, 1% of scoped
  chunks), rarest band only). User topic text is reduced to quoted alphanumeric
  tokens, so FTS5 query operators cannot inject or crash.
- Measured on the versioned exact-term manifest: dense-only hit@4 `0.6923`, hybrid
  hit@4 `1.0`, including the out-of-pool DOI case that no reranking could recover.
  141 tests pass; report retrieval and claim benchmarks are unchanged; the attempt-11
  report, Qdrant, Redis and all sealed hashes are untouched (the only persisted
  mutation is the additive `chunk_fts` table).
- `REPORT_HYBRID_RETRIEVAL` (default true) disables the channel; with it off, or when
  no needle term matches, candidate ordering is byte-identical to dense-only.
  `/query` and `/rag` remain dense-only per the PRD regression boundaries.
  *(Superseded 2026-08-13 by HUB-043: that boundary protected the regression
  surface that had become the defect. Both endpoints now use this channel.)*

Phase 6 (local reranking) is not entered: hybrid recall on the measured manifest is
`1.0`, so precision is not the bottleneck.

## Hardcoded credentials removed and rotated (HUB-003)

Deployed 2026-08-11. No secret value is tracked in git any longer:

- `SEARXNG_SECRET` and the shared Crawl4AI token (`CRAWL4AI_API_TOKEN`, also
  injected as `CRAWL4AI_TOKEN` into research-hub and the worker) now come only
  from the gitignored `.env`, referenced in `docker-compose.yml` with required
  `${VAR:?}` expansion — compose refuses to parse without them (verified: with
  `.env` absent, `docker compose config` fails with the generation hint).
- `searxng/settings.yml` no longer carries `secret_key` (the `SEARXNG_SECRET`
  env var overrides it) and `crawl4ai-config.yml` no longer carries
  `api_token` (the server enforces the env token; verified 401 without it).
- Open WebUI's static `WEBUI_SECRET_KEY` fallback was removed;
  when unset the container generates and persists a random key in its volume.
  The service is profile-gated, so it cannot use hard-required expansion.
- `.env.example` documents the required fields with blank placeholders and
  `openssl rand -hex 32` generation instructions; `SETUP.md` gained a
  "Configure secrets" step before first start.
- The deployed values were rotated: new random secrets in the local `.env`,
  searxng/crawl4ai/research-hub/research-worker recreated. Verified after
  rotation: the old token is rejected (401), the new token authenticates,
  `/readyz` is all-true, the 141-test suite passes in-container, and the
  attempt-11 report and source registry remain byte-identical
  (`068d60b2…`, `d6748d76…`) at 67 documents / 13 reports.

## Crawler SSRF guards deployed (HUB-006)

Deployed 2026-08-11. The crawler can no longer be steered at internal services:

- `app/url_policy.py` vets every crawl URL before the fetch: http/https and
  ports 80/443 only, then every DNS answer (or the IP literal) must be a
  globally routable unicast address — loopback, RFC1918/ULA, link-local
  (including `169.254.169.254`), CGNAT, multicast, reserved, and unspecified
  destinations are rejected, IPv4-mapped IPv6 is unwrapped first, one bad
  answer among many rejects (rebinding), and DNS failures fail closed.
- Because the fetch itself runs inside the Crawl4AI container, the landing URL
  Crawl4AI reports (`redirected_url`) is re-vetted after the fetch and the
  document is dropped when it landed anywhere disallowed. Documents larger
  than `CRAWL_MAX_MARKDOWN_CHARS` (default 2,000,000) are rejected.
- Every rejection logs `crawl_rejected` with job ID, normalized destination,
  and reason, and increments `hub_crawl_total{outcome="rejected"}`.
- Crawl4AI moved to a dedicated `crawler` Docker network: verified it cannot
  resolve Qdrant, SearXNG, or claim-verifier; Redis and Ollama stay reachable
  because Crawl4AI's own server config requires both (accepted residual, along
  with the unexposed redirect-count/duration limits and the DNS TOCTOU window
  — both bounded by landing-URL revalidation).
- 34 new tests (offline, resolver stubbed); the suite is now 175 tests, green
  in-container against the deployed image. Deployed modules SHA-verified in
  hub and worker; `/readyz` all-true; attempt-11 artifacts byte-identical at
  67 documents / 13 reports.

## Backup and restore operational (HUB-013)

Deployed 2026-08-11; full procedure in `docs/BACKUP.md`:

- `scripts/backup.ps1` snapshots `documents.sqlite3` with `VACUUM INTO`
  inside the running container (WAL-safe, no downtime), integrity- and
  count-checks the snapshot, copies it to the gitignored `backups/` directory
  (~46 MB), and keeps the newest 14.
- The Windows scheduled task `hub-stack-documents-backup` runs it daily at
  03:30 and logs to `backups/backup.log`; a scheduler-triggered run completed
  with result 0 on 2026-08-11. Failures exit non-zero.
- `scripts/restore.ps1` was exercised against a fresh clean volume:
  `integrity=ok documents=67 reports=13`, newest report is the attempt-11
  artifact, ~10 s recovery. `-Live` performs stop-writers → replace →
  restart → `/readyz` poll for real recovery.
- Qdrant is deliberately not snapshotted (`python -m app.rebuild` from SQLite
  is the documented recovery); Redis job state is transient. Backups remain
  on-machine and therefore unencrypted, per the backlog scope.

## CI, pinned images, and hashed lockfile (HUB-012)

Deployed 2026-08-11; Milestones 1 and 3 are now complete:

- All six formerly-`latest` compose images are pinned as `tag@sha256` to the
  digests that were running (ollama 0.32.6, qdrant v1.19.0, searxng 2026.8.4,
  crawl4ai 0.9.2, dozzle v10.6.15, uptime-kuma 1.23.17), so recreation cannot
  change behavior; every container, including the profile-gated ones, was
  recreated onto its pin.
- `research-hub/requirements.lock.txt`: pip-compile transitive lockfile with
  hashes, compiled in python:3.12-slim constrained to the deployed image's
  `pip freeze` — verified to reproduce the frozen set exactly, torch CPU
  extra index intact. The Dockerfile installs `--require-hashes`; `pip check`
  is clean in the built image; regenerate with `scripts/relock.ps1`.
- `.github/workflows/ci.yml` runs on push/PR: ruff (E9/F) + compileall, the
  full suite against a Redis 7 service container (`TEST_REDIS_URL` DB 15),
  `docker compose config` with placeholder secrets, and the research-hub
  image build with in-image `pip check`. Live benchmarks remain local-only
  (Ollama + corpus required).
- Post-deploy: 175 tests green in the lockfile-built image, `/readyz`
  all-true, sealed attempt-11 artifacts byte-identical at 67 documents /
  13 reports.

## v4 blind evaluation passed (HUB-036) — judge gate measured, still not deployed

Measured 2026-08-12 on branch `hub-036-judge-eval-v4`. The MiniMax M3 judge
gate passed the one-time operator-annotated 130-case v4 blind final on every
gate: zero unsupported acceptances (including the adversarial-injection
stratum), padding rejection 1.0, joint acceptance 25/25, cross-source
disagreement acceptance 20/20, metric-confusion rejection 10/10. Seals:
content `21465f6e…`, labels `632c30c3…`, results `7c9ed9ac…`; judge
configuration frozen before the run; served model `MiniMax-M3` throughout
(the seal records that MiniMax reports no finer version granularity — any
served-model change requires a fresh blind set before the gate is trusted).
(Historical note: at measurement time the deployed gate was still the v2 NLI
verifier; HUB-034 — see the top of this document — has since deployed the
judge as the only gate.)

## Selectable claim gate merged, not deployed (HUB-035) — historical; superseded by HUB-034

Merged 2026-08-12 on `hub-035-minimax-judge-gate`. The repository now contains
`app/judge_gate.py`, a MiniMax M3 LLM-as-judge claim-faithfulness gate selected
by `CLAIM_GATE=judge`; the default is `CLAIM_GATE=nli` and the deployed stack
was not rebuilt, so production behavior is unchanged and the sealed v2 NLI
verifier (attempt-11 artifacts, all seals) remains the live gate. The judge
shares the accepted/reason verdict contract and fail-closed retryable
semantics, keeps the deterministic structural checks as conjunctive local
guards, fences evidence as untrusted with injection-hardening instructions and
fence-break escaping, judges multi-span claims with per-ref necessity, and
records the served model version in every verdict. The Token Plan was verified
to permit single-user programmatic backend use before implementation (see
backlog HUB-035). `MINIMAX_SUBSCRIPTION_KEY` is required in `.env` by compose
(`${VAR:?}`); the value is sent only as an Authorization header. The gate
default must not flip before the v4 blind evaluation passes (HUB-036, then
HUB-034).

## Report-status projection reconciled with SQLite (HUB-031)

Deployed 2026-08-11. Milestone 4's known non-atomic write is closed:

- SQLite remains authoritative for persisted reports; the Redis job record's
  `report_status` field is still projected after synthesis, but every job
  read now derives the status from the persisted report row
  (`ResearchOrchestrator.get_job` overrides the projection with
  `DocumentStore.report_status`), so a crash between the SQLite and Redis
  writes can no longer surface a status that contradicts SQLite. Jobs with
  no persisted report pass through unchanged.
- Crash-window coverage in `tests/test_report_status_reconciliation.py`
  (real Redis DB 15 + temp document store): lost projection after a
  completed report, lost projection after a failed report, a stale
  projection contradicting a later persisted status, and the no-report
  passthrough — each asserted at both the orchestrator read and the public
  API projection.
- Post-deploy: 179 tests green in-container against the rebuilt image;
  deployed `research.py` and `document_store.py` SHA-256-identical to the
  tree in hub, worker and verifier containers; `/readyz` all-true with
  capability `all`; attempt-11 report and source registry byte-identical
  (`068d60b2…`, `d6748d76…`) at 67 documents / 13 reports; all three sealed
  claim-support hashes unchanged; Redis queues empty.
