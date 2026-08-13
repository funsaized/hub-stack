# ADR-002 — Search provider strategy for research acquisition

Date: 2026-08-13
Status: Accepted — stages 1 and 2 deployed and verified 2026-08-13
Item: HUB-042 (`backlog.md`)

## Context

Acquisition is the system's binding constraint. Retrieval measures hit@4 `1.0`
on its manifest (HUB-017) and the claim gate completed 10 of 10 consecutive
runs after HUB-037, but neither matters when no search results arrive. On
2026-08-13, after roughly 150–250 queries in a day, four of six engines were
simultaneously CAPTCHA'd or suspended and jobs failed with "No search results
found".

Two facts reframe the problem beyond "we got rate-limited".

**The stack ran on one engine for its entire life.** `searxng/settings.yml`
disabled bing, google, brave and startpage, so despite the client requesting
three engines, only duckduckgo was ever queried. A single CAPTCHA was
therefore a total outage. Widening to six restored resilience: a live search
returned results via bing while five others were blocked.

**`google cse` could never have worked.** SearXNG's `google_cse` engine sets
`require_api_key: False` and hardcodes `CX = "partner-pub-8993703457585266…"`,
a third-party Custom Search ID shared by every SearXNG install. It is not the
official Custom Search JSON API and accepts no key. It reported "too many
requests" on every search ever made and is now disabled.

**The official Bing Search API is gone.** Microsoft retired the entire family
on 2025-08-11 and directs customers to Azure AI Agents grounding, a platform
commitment rather than an API. Our most productive engine, bing, is a
*scraper* with no official fallback — a single point of failure by
construction.

**Precision fell as availability rose.** A screen-inert sample of the
bing-era corpus measured 8 on-topic, 17 marginal and 28 off-topic — 15%
on-topic against 62% for the duckduckgo-era reference set. The source screen
absorbs this (AUC `1.000`, keeps 98–100% of on-topic while dropping 75% of
off-topic), but only after the crawl budget has been spent.

## What this system actually needs from a search provider

This is the decisive criterion and it eliminates most of the market. The
stack already owns crawling (Crawl4AI), sanitisation, chunking, embedding,
hybrid retrieval and a claim gate. **It needs URLs and enough snippet to
screen them. It does not need page content.**

Providers that bundle extraction — Tavily, Exa, Firecrawl — price that
bundling in, and their content would be discarded on arrival in favour of our
own crawl. They are the wrong shape regardless of quality.

## Candidates against the decision criteria

| Criterion | SearXNG scrapers (today) | + pacing & backoff | Serper | Brave Search API | Tavily / Exa / Firecrawl | Azure AI Agents |
|---|---|---|---|---|---|---|
| Marginal cost | none | none | 2,500 free, then paid | $5/1k, $5 free monthly (~1k queries) | $5–8/1k plus content fees | Reported 40–483% above retired Bing pricing |
| Returns what we need (URLs + snippet) | yes | yes | yes | yes | yes, plus content we discard | yes, inside an agent framework |
| Survives sustained volume | no — measured failure | partly; untested | yes, documented quota | yes, documented quota | yes | yes |
| Failure mode | silent CAPTCHA | silent CAPTCHA, less often | quota error | quota error | quota error | quota error |
| Queries leave the machine | yes (to engines) | yes | yes | yes | yes | yes |
| Storage rights for a persistent corpus | n/a | n/a | not restricted in published material | **restricted — storage requires a plan granting it** | varies | varies |
| Vendor stability | n/a | n/a | independent | independent | Tavily acquired by Nebius 2026-02-10 | Microsoft, but a platform commitment |
| Integration effort | none | config only | one client, ~50 lines | one client | client + discarding content | project, resource group, model deployment |

## Spike validations

- **Widening works, and one survivor is enough.** With brave, duckduckgo,
  qwant, startpage and google cse all blocked, a live search returned 10
  results via bing and a full research job completed first attempt.
- **`google cse` is unusable**: engine source read in-container; no key
  parameter exists. Disabling it immediately restored a cleaner picture — 35
  results across duckduckgo, bing and brave with only qwant and startpage
  blocked.
- **Specialized engines never block but are domain-bound.** `it` and
  `science` categories returned zero unresponsive engines. Their results were
  unusable off-domain: `mdn` returned ten USB-API pages for a Postgres query
  by matching "version major"; `arxiv` returned particle physics. Only
  `stackoverflow` contributed a relevant result. Adding categories wholesale
  would degrade precision that the screen then pays to remove.
- **No pacing is configured.** SearXNG supports per-engine outgoing limits and
  documents severe backoff defaults for CAPTCHA and access-denied, and its
  own guidance is to keep the engine list lean and back off aggressively. Our
  deployment overrides none of it: the planner fires 3–6 searches with no
  delay and jobs run back to back.
- **Our IP reputation is favourable.** This is a residential workstation, not
  a hyperscaler range — the ranges engines block hardest. Blocking here is
  therefore volume-and-burst driven, which is the part we control.

## Decision

**Stage 1 — exhaust the free levers first, because they are untried.**

1. Keep the widened keyless pool (bing, brave, duckduckgo, mojeek, qwant,
   startpage); `google` and `google cse` stay disabled.
2. Configure SearXNG per-engine outgoing limits and pace searches within a
   job, rather than firing a plan's 3–6 queries in immediate succession.
3. Screen search results on title and snippet **before** crawling. This is the
   highest-leverage change available at zero marginal cost: it cuts crawls
   wasted on results the post-crawl screen already rejects, and it makes
   noisier engines affordable rather than expensive.
4. Report engine suspension distinctly from an empty web (already shipped).

**Stage 2 — add one keyed provider as a fallback, not a replacement, if
stage 1 measurement still shows acquisition failures.**

Use **Serper** for that role: it returns organic URLs and snippets — exactly
our need and nothing we would discard — offers 2,500 free queries without a
card, and publishes no storage restriction. Keep SearXNG primary so the
private path stays default and the paid path is insurance, mirroring the
"one survivor keeps acquisition alive" property that widening already proved.

## Rejected

- **Brave Search API**, despite being the closest drop-in for the engine we
  already lean on. Its terms restrict storing API results without a plan that
  grants storage rights, and this system exists to build a persistent corpus.
  That is a licensing question for the operator, not an engineering one, and
  it should be resolved before adoption rather than after.
- **Tavily, Exa, Firecrawl.** They bundle content extraction the stack already
  performs with Crawl4AI, so their differentiator is cost we would pay and
  then discard. Tavily's acquisition by Nebius in February 2026 adds
  continuity risk on top.
- **Azure AI Agents grounding.** Microsoft's own migration path from the
  retired Bing APIs, but it is a platform adoption — project, resource group,
  model deployment — for a system that wants a URL list, at reported costs far
  above the API it replaces.
- **Adding `it` and `science` engine categories wholesale.** Measured: they do
  not block, but off-domain results are junk, and the screen would pay to
  remove what the engines pay nothing to produce.
- **A search cache.** Would have cut most of today's load, since topics were
  repeated. Explicitly declined by the operator.

## Stage 1 outcome (2026-08-13)

Deployed: pacing at `SEARCH_PACING_SECONDS=2.0`, and per-facet ranking of
search results by title+snippet relevance before interleaving and before the
crawl cap is applied.

Measured on "Rust async runtime differences between tokio and async-std", the
topic that previously behaved worst:

| | Before stage 1 | After stage 1 |
|---|---|---|
| Documents crawled | 36 | 17 |
| Retained after the post-crawl screen | 4 | **17** |
| Discarded as off-topic | **32** | **0** |

The earlier run spent 36 crawls to keep 4 sources, discarding
`dictionary.com`, `thefreedictionary.com` and `webmd.com` after fetching
them. With ranking, the crawl budget went to the top of 71 scored candidates
(best `0.92`, median `0.731`, worst `0.418`) and every fetched document
survived the screen. The retained domains are `tokio.rs`, `docs.rs`,
`users.rust-lang.org`, `rustify.rs`, `rustfaq.org` and similar.

The post-crawl screen is not redundant — it remains the guard for documents
whose snippet flatters them — but it now has little left to remove.

## Stage 2 outcome (2026-08-13)

Serper deployed as a fallback behind SearXNG, keyed from `SERPER_API_KEY` and
inert when unset.

**Verified by taking SearXNG away.** With the container stopped, a job
recorded `search_providers: {"serper": 4}` — all four facet queries served by
the fallback — retained 17 sources and completed its report, drawing on
`kubernetes.io`, `docs.cloud.google.com`, `datadoghq.com` and `signoz.io`.
With SearXNG restored the next job recorded `{"searxng": 3}`, so primacy
returns automatically and the paid path stays insurance rather than default.
The key appears in no log line.

**The live contract exposed a defect no mock could.** Serper returns dates as
`"Oct 31, 2019"`, which neither ISO nor RFC-2822 parsing accepts, so every
Serper result parsed as undated. Any job setting `freshness_days` would have
rejected all of them as `stale_or_undated` — silently, since the results
would simply be absent. `parse_source_date` now accepts human-readable
formats after the existing two, leaving previously-parsing shapes untouched.

Two earlier attempts to force the fallback failed instructively and are worth
recording: pointing `SEARXNG_ENGINES` at a nonexistent engine did nothing
because SearXNG falls back to its category defaults, and overriding
`SEARXNG_URL` in `.env` did nothing because `docker-compose.yml` hardcodes it.
Stopping the container is the honest simulation.

## Acceptance

- A run of comparable volume to 2026-08-13 (roughly 30 jobs, 150+ queries)
  completes with no job failing on "No search results found".
- Pre-crawl screening measurably reduces crawls per job without reducing
  retained on-topic sources, judged against the HUB-038 reference method.
- If stage 2 is adopted: SearXNG remains the default path, and the fallback
  engages only when every configured engine reports unresponsive.
