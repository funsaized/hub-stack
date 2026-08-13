# Continue the hub-stack backlog — post-pivot steady state

Governing list:  `backlog.md` (Milestones 1–4 complete, 2026-08-12)
Deployed state:  `docs/CURRENT_STATE.md` — the claim gate is the MiniMax M3
                 judge (HUB-034); the NLI stack is decommissioned and the v2
                 seal is retired; the active seal is v4
                 (`tests/fixtures/judge_seal_v4.json`, status `measured`).

## Position

The judge-pivot sequence (HUB-035 → HUB-036 → HUB-034 → HUB-032) is closed:
calibration 60/60, the sealed v4 blind final passed every gate, and the
deployed swap was verified (SHA-identical modules, `/readyz` all-true,
attempt-11 artifacts byte-identical at 67 documents / 13 reports). What has
NOT happened yet: no live report has been generated through the deployed
judge gate — the flip was proven structurally, not with a production
synthesis run. Report synthesis now spends metered judge calls (Token Plan,
5-hour rolling + weekly windows) and judged evidence spans leave the machine.

## 1. Live proof of the deployed judge gate (do first)

Run ONE small research job end-to-end (a fresh topic; never touch the
attempt-11 report or authorize a retry against it). Budget before starting:
one report ≈ up to 8 single-span drafts + up to 8 pair drafts ≈ ≤ 16 judge
calls plus one correction round; abort cleanly on quota exhaustion (the
report stays retryable — retry after the window resets rather than forcing).
Verify and record in `docs/CURRENT_STATE.md`:

- the persisted report contains judge-verified claims (or a clean fail-closed
  failure with a retryable report — either is a valid observation; an
  unsupported-looking acceptance is NOT and must be recorded as a finding);
- every verdict's logged served model is exactly the sealed `MiniMax-M3`;
- if a cross-document pair verified, both citations display; the
  cross-source disclaimer appears only when no pair was available;
- attempt-11 report and registry stay byte-identical (`068d60b2…`,
  `d6748d76…`), Qdrant/Redis/SQLite counts move only by the new job's own
  ingestion, and all v4 seal hashes are unchanged.

## 2. Re-baseline watch (standing obligation, every pass)

The cloud judge is not frozen. Check the judge diagnostics of any reports
generated since the last pass: if any verdict reports a served model other
than the sealed `MiniMax-M3`, STOP trusting the gate, record the drift in the
backlog, and start a fresh blind set per the established v4 protocol (the
tooling — `judge_seal_v4.py`, drafting/validation/seal/runner scripts — is on
main; a new set means new operator-annotated cases, never reuse of the
consumed v4 set). The gate is not trusted again until the new final passes.
MiniMax reports no finer version granularity; that string is the trigger.

## 3. P3 discipline

HUB-024 through HUB-030 stay closed behind their revisit triggers (see
backlog): open one ONLY if its trigger has actually tripped (e.g. repeated
user need for multi-angle synthesis, sustained scheduled-research demand).
If nothing tripped and the live proof is recorded, end the pass cleanly —
do not manufacture work.

## Standing constraints

- Verify backlog/doc claims against the code before acting on them.
- One logical commit per item on a branch off main; CI green before merge
  (CI needs the `MINIMAX_SUBSCRIPTION_KEY: ci-placeholder` env already wired).
- Judge calls are metered: budget before any run, stop cleanly on quota or
  Token Plan changes, and record findings rather than forcing completion.
  Re-check Token Plan terms if judge usage patterns change materially.
- The Subscription Key never appears in chat, logs, diffs, or
  `docker compose config` output (validate with `--quiet` only).
- Suite runs in-container: throwaway image, tests+bin mounted, Redis DB 15,
  placeholder MINIMAX key; never recreate the deployed containers for tests.

## Out of scope

P3 items without tripped triggers, `/query` and `/rag` changes,
dependency-update automation and vulnerability scanning (deliberate HUB-012
skips), off-machine backup replication.
