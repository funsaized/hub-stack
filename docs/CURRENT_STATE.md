# Current deployed state

Last verified: 2026-08-14 on the local Windows 11 workstation.

## What this machine is now

A local LLM hub: Ollama on the GPU, Open WebUI in front of it, and metrics
showing what the box is doing. Eight containers, all healthy, all ports on
`127.0.0.1`.

```
hub-ollama            model server, 1x NVIDIA GPU reserved
hub-open-webui        chat UI -> Ollama only
hub-dozzle            container logs, read-only docker socket
hub-prometheus        metrics, 15s scrape, 15d retention
hub-grafana           dashboard "Local LLM hub"
hub-gpu-exporter      nvidia-smi -> Prometheus
hub-node-exporter     WSL2 VM CPU/memory/disk
hub-blackbox-exporter HTTP liveness for Ollama and the UI
```

Verified after the rebuild: all five Prometheus targets `up`, both liveness
probes returning 1, all six alert rules parsing `ok`, Grafana serving the
dashboard, and inference working end to end.

## Measured performance

| Model | Size | Throughput | VRAM |
|---|---|---|---|
| `qwen3.5:9b` | 6.6 GB | **103.2 tok/s** | ~8.5 GB resident, fits |
| `qwen3.6:27b` | 17 GB | **2.8 tok/s** | pegged at 12.07 GB, spills to CPU |

GPU: RTX 3080 Ti, 12 GB, driver 610.88, 350 W limit. Idle ~21 W / 40 °C;
under load 88% utilization / 58 °C.

**The 37x gap is the operating constraint of this machine.** Nothing warns you
at request time when a model does not fit — it simply gets unusably slow. The
VRAM panel on the dashboard exists to make that visible before you wait.

## The teardown (2026-08-14)

The private research-corpus RAG stack was removed at the operator's
instruction, along with all of its data.

**Removed from Compose:** `research-hub`, `research-worker`, `searxng`,
`crawl4ai`, `qdrant`, `redis`, `uptime-kuma`.

**Deleted, irrecoverably:** `hub_research_hub_data` (679 crawled documents,
54 reports, 758 source observations, 71,125 lexical index rows),
`hub_qdrant_data` (68,072 vectors), `hub_redis_data`, `hub_crawl4ai_data`,
`hub_uptime_kuma_data`, `hub_postgres_data` (orphaned since Postgres left
Compose), and the SQLite backups under `backups/`. Prometheus and Grafana
volumes were also dropped and rebuilt, so metric history starts at the
teardown.

**Kept:** `hub_ollama_data` — the five installed models, ~28 GB.

**Removed from the repository:** the `research-hub/` application (~6,000
lines), `PRDs/`, the research documentation set, `searxng/`,
`crawl4ai-config.yml`, `scripts/`, `bootstrap.sh`. All of it remains in git
history; `git checkout <sha>~1 -- research-hub/` restores the application.

**RAG integration removed from Open WebUI**: the four `OPENAI_API_*` variables
that exposed the corpus as a pseudo-model named `research-corpus`, plus its
`depends_on` on the research API. Open WebUI now talks only to Ollama.

## Three things that broke during the rebuild, and why

Recorded because each would be easy to reintroduce.

**1. Dropping `name: hub` silently emptied the model store.** Compose derives
volume names from the project name. The rewritten file omitted the top-level
`name:`, so the project became `hub-stack` (the directory name) and Compose
created a fresh, empty `hub-stack_ollama_data`. The stack came up healthy with
zero models — `/api/tags` returned `{"models":[]}` — while the real volume sat
untouched. Restoring `name: hub` brought all five models back. The line now
carries a comment saying it is load-bearing.

**2. The GPU exporter cannot use explicit device mappings under WSL2.**
`/dev/nvidiactl` and `/dev/nvidia0` do not exist; naming them fails the
container at startup. The nvidia driver reservation with
`capabilities: [gpu, utility]` is the mechanism that works.

**3. The GPU exporter panics on field auto-detection.** With
`--query-field-names=AUTO` it enumerates every field driver 610.88 offers, and
`power_smoothing.curr_profile.ramp_down_rate [W/s]` is not a valid Prometheus
metric name. The field list is now pinned explicitly.

## What was tried and abandoned

**cAdvisor.** Intended for per-container CPU and memory. Under Docker Desktop
it emits a single root-cgroup series with `--docker_only=true`, and nothing at
all without it — tested with `/var/lib/docker` and `/dev/disk` mounted and
`--privileged`. Docker Desktop's VM does not expose what cAdvisor needs. It
was removed rather than shipped emitting one meaningless number; an HTTP
liveness probe covers "is Ollama up", and `docker stats` covers the rest.

## Known gaps

- **No Ollama-level metrics.** Ollama exposes no Prometheus endpoint, so there
  is no tokens/sec, queue depth or model-load time in Grafana. The figures
  above were read by hand from `/api/generate` response fields.
- **Alerts are not routed.** No Alertmanager; they are a status page at
  `/alerts`.
- **No authentication anywhere.** Open WebUI auth is off and Grafana allows
  anonymous admin, both deliberate for a localhost single-user box. Both must
  change before any port is exposed.
- **CI still runs** (`.github/workflows/ci.yml`) but its Python lint and test
  steps have no source tree to act on now that `research-hub/` is gone.
- **`TODO/` is untracked** and contains hand-written healthcare LLM evaluation
  research notes. It was deliberately left in place during the teardown: it is
  not in git, so deleting it would be unrecoverable.
