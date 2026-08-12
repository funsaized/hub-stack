# Continue the hub-stack backlog — judge-gate pivot (HUB-035 → HUB-036 → HUB-034 → HUB-032)

Governing list:  `backlog.md` (post-pivot recommended order, 2026-08-12)
Deployed state:  `docs/CURRENT_STATE.md` — the deployed claim gate is still the
                 sealed v2 NLI verifier; nothing from the v3 attempt shipped.

## Position

Milestones 1–3 complete; Milestone 4 open on the judge pivot. The v3 blind
final measured the NLI leave-one-out rule and failed acceptance (joint 0.47,
disagreement 0.6, two unsupported acceptances); that work is archived unmerged
on `hub-032-cross-source-disagreement` and the v3 set is consumed. The
operator directed replacing the NLI gate with a MiniMax M3 LLM-as-judge
faithfulness gate (RAGAS-style, standard RAG pattern). M3 exposes
OpenAI-compatible `/v1/chat/completions` and Anthropic-compatible
`/v1/messages`. The operator's MiniMax Subscription Key is already in the
gitignored `.env` — confirm its variable name there, wire it with required
`${VAR:?}` expansion, and never echo the value into chat, logs, or committed
compose output.

## 1. HUB-035 — MiniMax M3 judge gate (do first)

- FIRST verify the Token Plan permits programmatic single-user backend use
  (account page / current docs; quotas are 5-hour rolling + weekly windows).
  If prohibited, STOP and ask the operator whether to switch the judge to
  pay-as-you-go before writing code.
- Implement per backlog HUB-035: judge client, structured JSON verdict with
  the same accepted/reason contract synthesis consumes today, temperature 0,
  served-model version recorded per verdict, every error path (timeout,
  quota, malformed output, schema violation) fails closed and leaves the
  report retryable.
- Injection hardening is in-scope, not deferred: evidence fenced as untrusted,
  judge instructed to ignore instructions in evidence, and the existing local
  structural checks (supports-restates-claim verbatim, span-exactness) stay as
  conjunctive guards — the judge can reject more than structure allows, never
  admit what structure rejects. Add tests with adversarial spans.
- Merge behind configuration: the gate defaults to the deployed NLI verifier;
  the judge is selectable (env flag) so main stays deployable with production
  behavior unchanged until HUB-034 flips it. Offline unit tests mock the API
  (httpx MockTransport, as in existing client tests). Full suite green
  in-container; one logical commit; CI green before merge.

## 2. HUB-036 — v4 evaluation protocol (GATED — stop before sealing)

Before drafting any blind case, STOP and present to the operator: strata and
counts (single-span entailment/neutral/contradiction, joint, padding,
cross-source disagreement, metric-name confusion, adversarial injection),
what the operator must annotate blind, and the re-baseline trigger for a
non-frozen cloud judge (seal records the served model version; a version
change requires a fresh blind set before the gate is trusted again). Do not
proceed without approval and an annotation commitment. Calibration uses a
labels-by-design set only; freeze the judge configuration (prompt, schema,
temperature, model version) before the ONE-TIME v4 final; the runner must
refuse re-runs. Gates: zero unsupported acceptances including the injection
stratum; padding rejection 1.0; joint and disagreement acceptance ≥ 0.8;
metric-confusion cases rejected.

## 3. HUB-034 — decommission the NLI stack (only after v4 passes)

Remove the claim-verifier service, `LocalClaimVerifier`, baked DeBERTa
weights, and NLI-specific tests; flip the gate default to the judge; retire
the v2 seal explicitly in the docs (never silently) alongside the consumed v3
artifacts; bundle the deferred FastAPI/Starlette upgrade into this rebuild.
Standard deploy pattern: rebuild, SHA-verify deployed modules, `/readyz`
all-true, sealed-artifact audit (attempt-11 report `068d60b2…` and registry
`d6748d76…` byte-identical), backlog + CURRENT_STATE updated, CI green,
merged.

## 4. HUB-032 — cross-source disagreement (measured, not re-implemented)

Multi-span judging lands with HUB-035 and is measured by the v4 final's joint
and disagreement strata; HUB-032's unchanged acceptance criteria are settled
by those results plus the report-side behavior (both citations displayed;
disclaimer removed only when pair assessment ran). Close or record it from
the v4 outcome — no separate evaluation.

## Standing constraints

- Verify backlog/doc claims against the code before acting on them.
- The deployed system keeps the sealed v2 gate until HUB-034; audit attempt-11
  artifacts after any deploy.
- One logical commit per item on a branch off main; CI green before merge.
- Judge calls are metered (subscription windows): budget calibration and the
  final measurement; if quota or Token Plan terms block the work, or cost
  balloons, stop, record findings in the backlog, and end the pass cleanly.
- The secret value never appears in chat, logs, diffs, or `docker compose
  config` output.

## Out of scope

Everything in P3 (no revisit triggers tripped), `/query` and `/rag` changes,
dependency-update automation and vulnerability scanning (deliberate HUB-012
skips), off-machine backup replication.
