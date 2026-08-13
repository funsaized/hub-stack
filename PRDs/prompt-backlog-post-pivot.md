# Continue the hub-stack backlog — live proof, then HUB-024

Governing list:  `backlog.md` (Milestones 1–4 complete; HUB-024 open)
Deployed state:  `docs/CURRENT_STATE.md` — the claim gate is the MiniMax M3
                 judge (HUB-034); the NLI stack is decommissioned and the v2
                 seal retired; the active seal is v4
                 (`tests/fixtures/judge_seal_v4.json`, status `measured`).
Design to build:  `PRDs/hub-024-query-planning.md`

## Position

The judge pivot is closed and deployed: calibration 60/60, the sealed v4 blind
final passed every gate, and the swap was verified (SHA-identical modules,
`/readyz` all-true, attempt-11 artifacts byte-identical at 67 documents / 13
reports). Two things remain. First, **no live report has ever been generated
through the deployed judge gate** — the flip was proven structurally, not by a
production synthesis. Second, HUB-024 (adaptive query planning) was opened
2026-08-13 with a prior-art-grounded design and is not implemented.

Do them in that order. The live report is the baseline HUB-024 gets measured
against, and a broken plan is only visible against a known-good run.

## 1. Live proof of the deployed judge gate (do first)

Run ONE small research job end-to-end on a fresh topic. Never touch the
attempt-11 report or authorize a retry against it. Budget first: one report is
≤ 8 single-span drafts + ≤ 8 pair drafts + one correction round ≈ ≤ 16-20
judge calls. On quota exhaustion stop cleanly — the report stays retryable;
retry after the window resets rather than forcing it.

Record in `docs/CURRENT_STATE.md`:

- the persisted report's verified claims, or a clean fail-closed failure with
  a retryable report (either is a valid observation; anything resembling an
  unsupported acceptance is a finding, not a pass);
- that every verdict's logged served model is exactly the sealed `MiniMax-M3`;
- whether a cross-document pair verified — if so both citations display, and
  the cross-source disclaimer appears only when no pair was available;
- attempt-11 report and registry byte-identical (`068d60b2…`, `d6748d76…`),
  counts moving only by the new job's own ingestion, v4 seal hashes unchanged.

**Keep this job's numbers** (distinct domains, retained sources, documents per
source): they are the single-query baseline for item 2.

## 2. HUB-024 — adaptive query planning and iterative research

Build to `PRDs/hub-024-query-planning.md`. The operator's hard constraint,
which the prior art independently supports: **breadth is emergent from a
distinctness threshold, never a fixed sub-query count.** A candidate facet is
admitted only if its embedding cosine to the already-admitted set is below
`PLAN_FACET_DISTINCT`; `PLAN_MAX_FACETS` is a safety rail, not the mechanism.
If a topic's candidates collapse to one facet, the job issues exactly one
search and behaves as it does today — that collapse IS the complexity signal.
Do not introduce a fixed N, a trained complexity classifier, or a fixed
depth × breadth tree (Static-DRA's own stated limitation).

Stage it so each piece is separately testable and mergeable:

1. Facet admission (bounded local-LLM proposal + embedding-distinctness
   filter) + canonical-URL dedup across facets + per-job search/crawl/rounds
   budget rails. Single-round only. Behind `REPORT_QUERY_PLANNING=false`.
2. Gap-driven rounds: per-facet coverage summary → named gaps → next round's
   queries; stop on novelty saturation (`PLAN_NOVELTY_MIN` on new canonical
   URLs), then coverage, with the rails as backstop. Record the stop reason.
3. Measurement: distinct domains and represented sources per report versus
   the item-1 baseline on the same topics, recorded not asserted. Treat the
   first run as calibrating `PLAN_NOVELTY_MIN`, not validating the design
   (the open thread in the PRD: no reviewed paper shows that threshold
   transfers across topic domains).

Non-negotiables while building: every sub-query inherits source policy
(allowed/blocked domains, per-domain limit, freshness) and SSRF vetting;
canonical URLs deduplicate across facets and rounds before crawling; judge
calls per report stay bounded by the existing drafting caps (breadth must not
raise metered cost); worker lease/heartbeat/retry/idempotency semantics
unchanged; plan provenance (facets, per-round queries, new-document yield,
stop reason) lands in job progress; `REPORT_QUERY_PLANNING=false` reproduces
current behavior exactly. Acceptance measures corpus breadth, NOT report
quality — DeepWeb-Bench found retrieval is 12–14% of deep-research errors
while derivation/calibration exceed 70%, so the judge gate stays the quality
guard and no quality claim rides on this item.

Enabling it in the deployed stack is a separate operator decision after the
measurement; do not flip the default unasked.

## 3. Re-baseline watch (standing obligation, every pass)

The cloud judge is not frozen. Check the judge diagnostics of any reports
generated since the last pass: if any verdict reports a served model other
than the sealed `MiniMax-M3`, STOP trusting the gate, record the drift in the
backlog, and start a fresh blind set per the v4 protocol (tooling is on main;
new operator-annotated cases, never reuse of the consumed v4 set). The gate is
not trusted again until the new final passes. MiniMax reports no finer version
granularity; that string is the trigger.

## 4. P3 discipline

HUB-025 through HUB-030 stay closed behind their revisit triggers. Open one
ONLY if its trigger actually tripped. If items 1–3 are done and nothing
tripped, end the pass cleanly — do not manufacture work.

## Standing constraints

- Verify backlog/doc claims against the code before acting on them.
- One logical commit per item on a branch off main; CI green before merge
  (CI carries `MINIMAX_SUBSCRIPTION_KEY: ci-placeholder`).
- Judge calls are metered: budget before any run, stop cleanly on quota or
  Token Plan changes, record findings rather than forcing completion.
- The Subscription Key never appears in chat, logs, diffs, or
  `docker compose config` output (validate with `--quiet` only).
- Suite runs in-container: throwaway image tag, `tests/` and `bin/` mounted,
  Redis DB 15, placeholder MINIMAX key; never recreate the deployed
  containers to run tests.
- Any deploy follows the standard pattern: rebuild, SHA-verify deployed
  modules, `/readyz` all-true, sealed-artifact audit, docs updated.

## Out of scope

`/query` and `/rag` changes (PRD regression boundaries), retrieval ranking
(HUB-017 measured hit@4 `1.0`), the claim gate and its v4 seal, dependency
automation and vulnerability scanning (deliberate HUB-012 skips), off-machine
backup replication.
