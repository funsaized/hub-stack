# Current deployed state

Last verified: 2026-08-25 on the native Linux workstation.

## What this machine is now

A local LLM hub: native Ollama on the GPU, Open WebUI in front of it, and
containerized observability. Seven containers are running. Docker web surfaces
use `127.0.0.1`; Caddy proxies them over HTTPS to the tailnet, and Ollama is
UFW-scoped to the private LAN and Docker networks.

```
ollama.service        native model server, NVIDIA CUDA backend
hub-open-webui        chat UI -> Ollama only
hub-dozzle            container logs, read-only docker socket
hub-prometheus        metrics, 15s scrape, 15d retention
hub-grafana           dashboard "Local LLM hub"
hub-gpu-exporter      nvidia-smi -> Prometheus
hub-node-exporter     native Linux host CPU/memory/disk
hub-blackbox-exporter HTTP liveness for Ollama and the UI
caddy.service         tailnet-only HTTPS reverse proxy and ACME TLS
```

Verified after the rebuild: all five Prometheus targets `up`, both liveness
probes returning 1, all six alert rules parsing `ok`, Grafana serving the
dashboard, and inference working end to end. The five Caddy vanity routes and
their five product-name aliases were subsequently verified over HTTPS; an
Ollama generation through the proxy returned the expected model response.

## Hardware and model

| Model | Quantization | Size | Role |
|---|---|---:|---|
| `qwen3.5:9b` | Q4_K_M | 6.6 GB | Primary local model |

Host: Ryzen 7 5800X (8 cores/16 threads), 32 GiB RAM, RTX 3080 Ti with
12 GiB VRAM, NVIDIA driver 610.57.04, and native Docker Engine on Btrfs.

Ollama runs with flash attention, an 8,192-token default context, one parallel
request, and one loaded model. The selected model must report `100% GPU` in
`ollama ps`; a partial CPU/GPU split is considered a failed configuration.

The graded benchmark passed 4/4 deterministic checks, loaded cold in 3.68 s,
ingested a 4,098-token prompt at 3,174 tok/s, and generated at 86.2 tok/s over
38.7 seconds. The run reached 96% GPU utilization, 339.6 W, 74 C, and 7.52 GiB
VRAM. Host CPU peaked at 42.3% and memory at 40.0%. `ollama ps` reported `100%
GPU` and context `8192`; the card was power-bound, not thermally throttled or
spilling model layers to CPU.

Official `qwen3.8` is currently only available as a 27B/18 GB model, so it is
not installed. It cannot fit in this GPU's 12 GiB VRAM.

## Linux migration (2026-08-25)

The stack moved from Windows 11 with Docker Desktop/WSL2 to native Linux.
Docker's data now lives on the host root filesystem, so node-exporter reports
the actual 32 GiB host and disk monitoring uses `mountpoint="/"`. GPU access
requires NVIDIA Container Toolkit registered with the native Docker daemon.

The old Docker Desktop named volumes were not present in the new native Docker
data root. Models now live outside Docker under `/var/lib/ollama`; Compose
lifecycle commands cannot remove them.

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

**Kept at that time:** `hub_ollama_data` — the five installed models, ~28 GB.
That historical Docker Desktop volume was not migrated to native Linux.

**Removed from the repository:** the `research-hub/` application (~6,000
lines), `PRDs/`, the research documentation set, `searxng/`,
`crawl4ai-config.yml`, `scripts/`, `bootstrap.sh`. All of it remains in git
history; `git checkout <sha>~1 -- research-hub/` restores the application.

**RAG integration removed from Open WebUI**: the four `OPENAI_API_*` variables
that exposed the corpus as a pseudo-model named `research-corpus`, plus its
`depends_on` on the research API. Open WebUI now talks only to Ollama.

## Historical rebuild notes

Recorded because each would be easy to reintroduce.

**1. Dropping `name: hub` previously hid the model store.** Under the old
containerized Ollama deployment, changing the Compose project name selected a
different model volume. Native Ollama removes that risk, but `name: hub`
remains stable so WebUI and monitoring volumes retain their expected names.

**2. GPU containers require the NVIDIA runtime.** On native Linux, install
NVIDIA Container Toolkit and register it with Docker. Compose device
reservations then provide the GPU without hard-coding `/dev/nvidia*` paths.

**3. The GPU exporter previously panicked on field auto-detection.** A driver
field produced an invalid Prometheus metric name. The explicit field list is
retained because it also keeps metrics stable across driver updates.

## Historical platform limitation

**cAdvisor under Docker Desktop.** The old Windows deployment exposed no usable
per-container metrics, so cAdvisor was removed. Native Linux no longer has
that platform limitation, but the stack still uses node-exporter, HTTP probes,
and `docker stats` because no current dashboard requires cAdvisor data.

## Known gaps

- **No Ollama-level metrics.** Ollama exposes no Prometheus endpoint, so there
  is no tokens/sec, queue depth or model-load time in Grafana. The figures
  above were collected by `scripts/benchmark.py` from `/api/generate` fields.
- **Alerts are not routed.** No Alertmanager; they are a status page at
  `/alerts`.
- **No application authentication.** Open WebUI auth is off and Grafana allows
  anonymous admin. Their reverse-proxy routes are deliberately restricted to
  the tailnet. Ollama, Dozzle, and Prometheus also have no authentication; UFW
  and the tailnet-only Caddy listener are their network access controls.
- **CI validates configuration only.** There is no application source after
  the research stack teardown.
- **`TODO/` is untracked** and contains hand-written healthcare LLM evaluation
  research notes. It was deliberately left in place during the teardown: it is
  not in git, so deleting it would be unrecoverable.
