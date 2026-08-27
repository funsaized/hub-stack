# hub-stack

A local LLM hub on one machine: native Ollama with a GPU, plus a containerized
chat UI, logs, and metrics that show what the box is doing while a model runs.

Nothing here calls out to a hosted model. Docker web surfaces bind to
`127.0.0.1`; Caddy makes them available to the tailnet, and Ollama is
restricted to private networks by UFW.

## Run it

The host needs native Ollama, a working NVIDIA driver, Docker Engine, Compose,
UFW, Tailscale, Caddy with the Netlify DNS module, and NVIDIA Container Toolkit
for the GPU metrics exporter. On Arch Linux:

```bash
sudo pacman -S --needed ollama-cuda nvidia-container-toolkit ufw
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
sudo ufw default deny incoming
sudo ufw allow from 192.168.1.0/24 to any port 11434 proto tcp \
  comment allow-ollama-private-lan
sudo ufw allow from 172.16.0.0/12 to any port 11434 proto tcp \
  comment allow-ollama-docker
sudo ufw --force enable
sudo install -Dm644 systemd/ollama.service.d/override.conf \
  /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl enable --now ollama
OLLAMA_HOST=http://127.0.0.1:11435 ollama pull qwen3.5:9b
docker compose up -d
```

Apply the firewall rules before starting Ollama. Adjust the LAN CIDR if yours
differs. The Docker rule is required for Open WebUI; adjust it too if Docker's
address pools are outside the default `172.16.0.0/12` range.

No `.env` is required — every variable has a default. Copy `.env.example` if
you want to change Docker web ports or bind addresses. Ollama runtime settings
live in `systemd/ollama.service.d/override.conf`.

| Surface | URL | What it is |
|---|---|---|
| Ollama API | http://127.0.0.1:11434 | Instrumented model API; also available on the private LAN. |
| Open WebUI | http://127.0.0.1:8080 | Chat interface. Auth is **off** — see below. |
| Grafana | http://127.0.0.1:3000 | Model, hardware, system, and log dashboards. Anonymous admin, no login. |
| Prometheus | http://127.0.0.1:9090 | Raw metrics and alert state. |
| Dozzle | http://127.0.0.1:9999 | Container logs, live. |

The Caddy routes are available over Tailscale with HTTPS:

| Surface | Vanity URL |
|---|---|
| Open WebUI | https://hub.nzxt.dev.s11a.com |
| Grafana | https://dashboard.nzxt.dev.s11a.com |
| Ollama API | https://ollama.nzxt.dev.s11a.com |
| Dozzle | https://logs.nzxt.dev.s11a.com |
| Prometheus | https://metrics.nzxt.dev.s11a.com |

Product-name aliases (`openwebui`, `grafana`, `models`, `dozzle`, and
`prometheus`) are also configured. The checked-in
`caddy/conf.d/hub-stack.caddy` route snippet is imported by the host Caddyfile;
see `docs/NETWORKING.md` for installation details.

## What runs

| Service | Purpose |
|---|---|
| `ollama.service` | Native model server using the NVIDIA GPU |
| `hub-ollama-proxy` | Stable Ollama API plus request, token, latency, and model metrics |
| `hub-open-webui` | Chat UI, talks to Ollama and nothing else |
| `hub-dozzle` | Log viewer over a read-only Docker socket |
| `hub-prometheus` | Metrics store, 15d retention |
| `hub-loki` | Searchable logs, 14d retention |
| `hub-alloy` | Docker and systemd log collector |
| `hub-grafana` | Provisioned metrics and logs dashboards |
| `hub-gpu-exporter` | GPU utilization, VRAM, temperature, power |
| `hub-node-exporter` | Host CPU, memory, disk |
| `hub-blackbox-exporter` | HTTP liveness probes for Ollama and the UI |
| `caddy.service` | Tailnet-only HTTPS reverse proxy |

## Hardware and what actually fits

Current host: native Linux, Ryzen 7 5800X, 32 GiB RAM, and an **RTX 3080 Ti
with 12 GiB VRAM**. The selected model is `qwen3.5:9b` (Q4_K_M, 6.6 GB): it is
the newest Qwen generation that leaves safe headroom for an 8K KV cache while
remaining fully GPU-resident.

| Candidate | Size | Decision |
|---|---|---|
| `qwen3:8b` | 5.2 GB | Fits, but is the older Qwen 3 generation |
| `qwen3.5:9b` | 6.6 GB | **Selected:** newer, multimodal, and fits fully |
| `qwen3:14b` | 9.3 GB | Too little KV-cache and desktop VRAM headroom |
| `qwen3.8:27b` | 18 GB | Newest official release, but cannot fit in VRAM |

The official Qwen 3.8 release currently has no small model; its only Ollama
size is 27B. Keep model weights below about 9 GB on this desktop GPU. Larger
models can silently spill to CPU and become much slower.

Flash attention is forced and the default context is 8,192 tokens. Confirm
full offload after loading the model:

```bash
ollama ps
# PROCESSOR must report: 100% GPU
```

Run the end-to-end inference and resource benchmark with:

```bash
python3 scripts/benchmark.py
```

The benchmark uses only the Python standard library and emits a versioned JSON
report. It measures:

- deterministic functional accuracy across arithmetic, reasoning, knowledge,
  instruction following, extraction, and code understanding;
- cold-start latency, streaming time to first token, user-visible latency, and
  Ollama's prompt and decode token rates;
- synthetic context retrieval and prompt-ingestion scaling at increasing
  lengths, with Ollama's actual prompt token counts reported;
- warm-run distributions, sustained throughput and performance drift;
- concurrent-client latency, request rate, and aggregate token throughput;
- model offload, context configuration, GPU/host pressure, and estimated GPU
  energy from Prometheus; and
- enough model, runtime, hardware, and benchmark metadata to reproduce and
  compare runs.

Progress goes to stderr and the report goes to stdout, so it can be captured
directly:

```bash
python3 scripts/benchmark.py --output benchmark.json > /dev/null
```

Use `--help` to select another model or endpoint, adjust run counts and
concurrency, or skip expensive sections. Report raw values rather than the old
A-F grades: uncalibrated thresholds hide useful differences and do not transfer
between models or hardware. The included accuracy checks are a deterministic
regression suite, not a claim of broad model intelligence; use a recognized
domain benchmark when evaluating a model for a specific workload.

This hub requests `OLLAMA_NUM_PARALLEL=2` so a future dense model can use it.
Ollama 0.32.15 cannot honor that setting for the hybrid `qwen35` architecture:
`qwen3.5:9b` starts `llama-server` with `-np 1`, so overlapping Open WebUI work
serializes the visible stream. This is an architecture limit, not a VRAM limit;
the model peaks around 7.13 GiB of the 12 GiB card with an 8K context. Record
both the requested setting and observed server parallelism when comparing runs.

## Security posture

- Docker web surfaces bind to `127.0.0.1`. The metrics proxy intentionally
  listens on `0.0.0.0:11434`; UFW restricts it to this private LAN and Docker
  networks. Native Ollama is reachable only on host loopback `:11435`.
- **Open WebUI authentication is off** (`WEBUI_AUTH=false`), an explicit
  choice for a single-user box. Caddy exposes it only to the tailnet; enable
  authentication before allowing access from any less-trusted network.
- Grafana allows anonymous admin with the login form disabled. Its Caddy route
  has the same tailnet-only trust boundary.
- Ollama, Dozzle, and Prometheus also have no application authentication; their
  Caddy routes rely on the same tailnet boundary.
- Dozzle mounts the Docker socket read-only.
- Alloy runs as root with the Docker socket mounted read-only, excludes Open
  WebUI to avoid retaining chat content, and reads the Ollama and Caddy systemd
  journals. Proxy request logs contain timings and token counts, never prompts
  or generated text.
- Treat Dozzle and Alloy as Docker-daemon privileged despite `:ro`: Unix socket
  API access is not made read-only by a bind-mount flag. Neither exposes a
  public control endpoint in this stack.

## History

This repository previously held a private research-corpus RAG stack —
multi-engine search, crawling, a vector store, hybrid retrieval and an
LLM-as-judge claim gate. It was removed on 2026-08-14 along with its data.
The code is in git history; `git log --diff-filter=D --name-only` will find
it, and `git checkout <sha>~1 -- research-hub/` restores it.

See `docs/CURRENT_STATE.md` for what was torn down and what was kept.
