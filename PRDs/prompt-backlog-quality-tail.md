# Continue the hub-stack backlog — quality tail (HUB-031, then HUB-032 gated)

Governing list:  `backlog.md` (all P0/P1 ✅ as of 2026-08-11; open: HUB-031, HUB-032)
Deployed state:  `docs/CURRENT_STATE.md` — read the last four sections
                 (HUB-003/006/013/012) plus the Phase 4/5 sections

## Position

Milestones 1 and 3 are complete: secrets required from .env, SSRF guards and
crawler egress isolation deployed, daily VACUUM-INTO backups with a tested
restore, images pinned to digests, hashed lockfile, and CI green on main.
The suite is 175 tests. What remains is the quality tail of Milestone 4.

## 1. HUB-031 — Reconcile the Redis report-status projection (do first)

Known non-atomic write, not a live defect. SQLite is authoritative for
persisted reports; `report_status` is separately projected into the Redis job
record (see `_update_job(..., report_status=...)` call sites after synthesis).
A crash between the SQLite write and the Redis write leaves a stale projection.

- Verify the current write/read paths in the code before changing anything.
- Preferred fix per backlog: derive `report_status` from the persisted SQLite
  report at job-read time (or reconcile the projection on read) so no code
  path can display a status that contradicts SQLite.
- Test the crash window: persist a report, drop/corrupt the projection, read
  the job, assert the persisted status wins. Cover completed AND failed
  report states.
- Acceptance: backlog HUB-031 criteria; full suite green in-container;
  standard deploy pattern (rebuild, SHA-verify, /readyz, sealed-artifact
  audit); backlog + CURRENT_STATE updated; one commit, merged to main, CI green.

## 2. HUB-032 — Verified cross-source disagreement (GATED — stop before starting)

Hard precondition: any change to the multi-ref verifier rule invalidates the
sealed claim-support evaluation. The sealed final (fixtures `1675d9ed…`,
`6c94272b…`, `5c65544c…`) measured the union premise only and MUST NOT be
reused for tuning or re-measured under a changed rule.

Before writing any code, STOP and present to the operator:

- the design for distinguishing padding refs from genuine joint evidence,
- the plan for drafting disagreement claims from a bounded pair of spans,
- the blind-set protocol: how many cases, drawn from which corpus documents,
  and what the operator must annotate blind before any rule change is tuned.

Do not proceed until the operator approves the protocol and agrees to
annotate. If declined, record the decision in the backlog and stop cleanly.

Acceptance (from backlog): a claim entailed only by two spans read together
displays with both citations; one-relevant-plus-one-irrelevant refs still
reject; reports stop disclaiming cross-source disagreement only once it is
assessed — all measured on the NEW sealed set, with the old seal retired
explicitly in the docs, never silently.

## Standing constraints

- Attempt-11 report and source registry stay byte-identical (`068d60b2…`,
  `d6748d76…`) through HUB-031; audit after any deploy. For HUB-032, no
  report retries are authorized until the new evaluation protocol is approved.
- The FastAPI/Starlette upgrade deferred from HUB-012 may be bundled into
  HUB-032's rebuild if and only if the new blind set is being sealed anyway;
  otherwise leave it deferred.
- Verify backlog/doc claims against the code before acting on them.
- One commit per item on a branch off main; CI must be green before merge.
- If HUB-032's cost balloons, stop, record findings in the backlog, and end
  the pass rather than forcing it.

## Out of scope

Everything in P3 (no revisit triggers tripped), dependency-update automation
and vulnerability scanning (recorded as deliberate skips in HUB-012),
off-machine backup replication, and extending hybrid retrieval to `/query`.
