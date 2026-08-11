# Continue the hub-stack backlog — hardening pass (HUB-003 → HUB-006 → HUB-013 → HUB-012)

Governing list:  `backlog.md` (statuses verified against the tree 2026-08-11; the
                 "Recommended order for the remaining open work" section at the
                 bottom is the sequence to follow)
Deployed state:  `docs/CURRENT_STATE.md` — read the last two sections first
                 (Phase 4 acceptance, Phase 5 hybrid retrieval)

## Position

The RAG synthesis modernization is complete and deployed: Phases 0–5 closed,
`main` at `54c4f7a` pushed, all eight containers healthy, 141 tests green, and
the three benchmarks pass (report retrieval gates, claim drafting `failures: []`,
exact-term hybrid hit@4 `1.0`). The attempt-11 report is the accepted Phase 4
artifact. Milestones 2 and 4 are effectively done (HUB-031/032 are quality
follow-ons, not this pass). What remains before the stack can be trusted as an
always-on service is the hardening tail: two P0 security items and two P1
operability items.

Work them **one at a time, in this order**, each to its own commit:

## 1. HUB-003 — Remove hardcoded credentials (P0, do first)

Verified inventory (2026-08-11):

- `docker-compose.yml:143` — `SEARXNG_SECRET=changeme_in_production`
- `searxng/settings.yml:8` — `secret_key: "changeme_in_production"` (duplicate of
  the same secret; check how SearXNG resolves env vs file before touching)
- `docker-compose.yml` ×3 — `CRAWL4AI_API_TOKEN` / `CRAWL4AI_TOKEN=hub-crawl4ai-shared-token`
  (crawl4ai, research-hub, research-worker; all three must agree)
- Open WebUI `WEBUI_SECRET_KEY` falls back to `changeme_in_production` (optional
  `webui` profile — still fix the fallback)

Approach constraints:

- Secrets move to `.env` (gitignored — verify) referenced via `${VAR:?}` -style
  required expansion or equivalent, so compose refuses to start without them.
  `.env.example` gets blank/placeholder entries and generation instructions
  (`openssl rand -hex 32`), never values.
- Rotate the deployed values as part of the change: new random values in the
  local `.env`, recreate affected containers, verify research readiness after.
- The Postgres part of the original item is obsolete (Postgres left Compose).
- Acceptance: no usable secret tracked in git (`git grep` the old values),
  compose refuses to start with missing values, fresh-setup path documented,
  full suite still green, `/readyz` all-true after rotation.

## 2. HUB-006 — Crawler SSRF guards (P0)

Already present (do not rebuild): scheme/port allowlist and canonicalization in
`app/research.py:44-58,138`, domain policy and robots handling from HUB-021.

Missing, in scope:

- Reject loopback/private/link-local/multicast/metadata destinations for IPv4
  and IPv6 (`ipaddress` module) at URL-vetting time, resolving DNS.
- The actual fetch happens inside the Crawl4AI container: validate what
  Crawl4AI reports it finally fetched (redirect landing URL) against the same
  policy, and reject the document when it landed somewhere disallowed.
- Response-size and redirect-count limits where the Crawl4AI API exposes them;
  log every rejection with job ID, normalized destination, and reason.
- Network-layer egress isolation (separate crawler network) is preferred if
  compose changes stay simple; otherwise record it as an accepted residual in
  the backlog status.
- Acceptance per backlog: tests reject direct, encoded, DNS-resolved, and
  redirect-based internal destinations; public pages still crawl (verify with
  one bounded live research job only if needed, and note it mutates the corpus
  — prefer mocked tests plus the existing corpus).

## 3. HUB-013 — Backup and restore (P1)

`documents.sqlite3` is the only irreplaceable state (canonical documents, 13
reports including the attempt-11 acceptance artifact, `chunk_fts`). Qdrant is
rebuildable from it (`python -m app.rebuild`, embedding cost only); Redis holds
job state of transient value. Scope the first pass to: scheduled consistent
SQLite backup (use the SQLite backup API or `VACUUM INTO`, not a raw file copy
of a WAL database), retention, a tested restore into a clean volume, and a
post-restore smoke check (`documents` count + one report row + `/readyz`).
Encrypt if the destination leaves the machine. Do not build Qdrant snapshot
tooling unless the SQLite path proves insufficient — rebuild is the documented
recovery for vectors.

## 4. HUB-012 — CI and pinning (P1)

- Pin the six `latest` images: ollama, qdrant, searxng, crawl4ai, dozzle,
  uptime-kuma — to the currently-running digests/tags (read them from
  `docker image inspect` so behavior does not change on recreate).
- GitHub Actions: lint/syntax, the unit suite (no-Docker subset or a Redis
  service container for DB-15 integration), `docker compose config`, and the
  research-hub image build. The live benchmarks stay local — they need Ollama
  and the corpus.
- Transitive lockfile with hashes (pip-tools/uv) that reproduces the current
  frozen set exactly — the claim-verifier model pin and torch CPU index must
  survive; `pip check` clean in the built image.

## Standing constraints

- Never touch the sealed claim-support evaluation: fixtures stay at hashes
  `1675d9ed…`, `6c94272b…`, `5c65544c…`; verifier model, revision
  `6f5cf0a2…`, threshold `0.97`, and the `evidence_union` premise format are
  frozen. No report retries are needed for any of this work, and none are
  authorized.
- The attempt-11 report and its source registry must remain byte-identical
  (Markdown `068d60b2…`, sources `d6748d76…`). Audit after any deploy.
- Verify backlog/doc claims against the code before acting on them.
- Deploy pattern per item: rebuild affected images from the clean tree,
  recreate only affected containers, SHA-verify changed modules in-container,
  `/readyz` all-true, full suite green in-container before the deploy.
- Docs discipline per item: update the backlog status to ✅ with a dated,
  verifiable summary; update `docs/CURRENT_STATE.md`; one commit per item on a
  branch off `main`, merged when the item's acceptance criteria are met.
- This is a single-user local stack: prefer the smallest change that meets the
  acceptance criteria over infrastructure. If an item's cost balloons, stop,
  record what was found in the backlog status, and move to the next item.

## Out of scope for this pass

HUB-031 and HUB-032 (quality follow-ons — HUB-032 in particular requires a new
blind evaluation set before any verifier-rule change), everything in P3, Phase
6/7 of the modernization PRD (entry conditions unmet), and extending hybrid
retrieval to `/query` (new scope; note it in the backlog if evidence appears).
