# Current deployed state

Last runtime verification: 2026-08-26 on the native Linux workstation.

## What this machine is now

A local LLM hub: native Ollama on the GPU, Open WebUI in front of it, and
containerized observability. Ten containers are running. Docker web surfaces
use `127.0.0.1`; Caddy proxies them over HTTPS to the tailnet, and Ollama is
UFW-scoped to the private LAN and Docker networks.

```
ollama.service        native model server, NVIDIA CUDA backend
hub-open-webui        chat UI -> Ollama only
hub-dozzle            container logs, read-only docker socket
hub-ollama-proxy      instrumented Ollama API -> native backend
hub-loki              retained searchable logs
hub-alloy             Docker and systemd log collector
hub-prometheus        metrics, 15s scrape, 15d retention
hub-grafana           four provisioned metrics and logs dashboards
hub-gpu-exporter      nvidia-smi -> Prometheus
hub-node-exporter     native Linux host CPU/memory/disk
hub-blackbox-exporter HTTP liveness for Ollama and the UI
caddy.service         tailnet-only HTTPS reverse proxy and ACME TLS
```

Verified after the observability rollout: Ollama native and OpenAI-compatible
generation work through the proxy, per-request metrics reach Prometheus, Loki
contains Docker and Ollama/Caddy journal streams, Grafana provisions all four
dashboards, and the model is fully GPU-resident. Although the unit requests
`OLLAMA_NUM_PARALLEL=2`, Ollama forces `qwen3.5:9b` to `llama-server -np 1`.
The five Caddy vanity routes and their five product-name aliases were previously
verified over HTTPS.

## Hardware and model

| Model | Quantization | Size | Role |
|---|---|---:|---|
| `qwen3.5:9b` | Q4_K_M | 6.6 GB | Primary local model |

Host: Ryzen 7 5800X (8 cores/16 threads), 32 GiB RAM, RTX 3080 Ti with
12 GiB VRAM, NVIDIA driver 610.57.04, and native Docker Engine on Btrfs.

Ollama runs with flash attention, an 8,192-token default context, and one loaded
model. `OLLAMA_NUM_PARALLEL=2` remains requested for future dense models, but
the hybrid SSM+attention+vision `qwen35` architecture does not support parallel
requests in Ollama 0.32.15. The server logs the limitation, starts with `-np 1`,
and serializes overlapping Open WebUI work with visible chat. The selected model
must report `100% GPU` in `ollama ps`; a partial CPU/GPU split is a failed
configuration.

Four full benchmark runs on 2026-08-26 measured about 111 tok/s wall decode
(113-121 Ollama eval tok/s), about 2.3 s for a warm 256-token generation, and
70-100 ms warm TTFT. A true unload took about 3.1 s cold, or about 2.6 s while
the GGUF remained in page cache; `llama-server` itself started in about 1.8 s.
The 5,737-token needle prompt ingested in about 1.49 s (about 3,840 tok/s) with
3/3 retrieval. Deterministic quality was 19/20 at temperature 0 and seed 42;
the stable miss was `reasoning-converse` (`no` expected, `yes` returned).

`ollama ps` reported `100% GPU` and context `8192`. Peak card use was about
7.13 GiB of 12 GiB, power peaked near the 350 W TGP (349.6 W), and temperature
peaked near 76 C. The card was power-bound, not thermally throttled or spilling
model layers to CPU. Four overlapping generations formed a serial latency
staircase of about 2.3, 4.6, 6.9, and 9.2 s; aggregate throughput remained about
the same as sequential throughput because this model has one inference slot.

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
