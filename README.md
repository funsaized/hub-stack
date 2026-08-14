# hub-stack

A local LLM hub on one machine: Ollama with a GPU, a chat UI, logs, and
metrics that show what the box is doing while a model runs.

Nothing here calls out to a hosted model. Every port binds to `127.0.0.1`.

## Run it

```bash
docker compose up -d
```

No `.env` is required — every variable has a default. Copy `.env.example` if
you want to change ports, bind addresses or Ollama's runtime settings.

| Surface | URL | What it is |
|---|---|---|
| Ollama API | http://127.0.0.1:11435 | The model server. **Note the port: 11435 on the host, 11434 inside.** |
| Open WebUI | http://127.0.0.1:8080 | Chat interface. Auth is **off** — see below. |
| Grafana | http://127.0.0.1:3000 | Dashboard "Local LLM hub". Anonymous admin, no login. |
| Prometheus | http://127.0.0.1:9090 | Raw metrics and alert state. |
| Dozzle | http://127.0.0.1:9999 | Container logs, live. |

## What runs

| Container | Purpose |
|---|---|
| `hub-ollama` | Model server, reserves one NVIDIA GPU |
| `hub-open-webui` | Chat UI, talks to Ollama and nothing else |
| `hub-dozzle` | Log viewer over a read-only Docker socket |
| `hub-prometheus` | Metrics store, 15d retention |
| `hub-grafana` | Dashboard |
| `hub-gpu-exporter` | GPU utilization, VRAM, temperature, power |
| `hub-node-exporter` | Host CPU, memory, disk |
| `hub-blackbox-exporter` | HTTP liveness probes for Ollama and the UI |

## Hardware and what actually fits

Measured on this machine, 2026-08-14 — an **RTX 3080 Ti with 12 GB VRAM**,
driver 610.88, WSL2 backend, 20 GB RAM visible to the Docker VM.

| Model | Size | Throughput | Fits in VRAM? |
|---|---|---|---|
| `qwen3.5:9b` | 6.6 GB | **103 tok/s** | yes (~8.5 GB resident) |
| `qwen3.6:27b` | 17 GB | **2.8 tok/s** | **no** — spills to CPU |

That difference is 37×, and it is the single most important operating fact
about this box: **a model larger than about 10 GB will run, and will be too
slow to use.** The 27B model is installed and should be treated as
unavailable unless you are willing to wait.

`nomic-embed-text` (274 MB) is retained; it is an embedding model, not a chat
model, and produces no useful output in the chat UI.

## Security posture

- Every published port binds to `127.0.0.1`. Set `OLLAMA_BIND_ADDRESS` (or
  the other `*_BIND_ADDRESS` variables) to a Tailscale or trusted-LAN address
  only when remote access is genuinely wanted.
- **Open WebUI authentication is off** (`WEBUI_AUTH=false`), an explicit
  choice for a single-user box on localhost. Set `WEBUI_AUTH=true` before
  exposing port 8080 to anything.
- Grafana allows anonymous admin with the login form disabled. Same caveat.
- Dozzle mounts the Docker socket **read-only**.

## History

This repository previously held a private research-corpus RAG stack —
multi-engine search, crawling, a vector store, hybrid retrieval and an
LLM-as-judge claim gate. It was removed on 2026-08-14 along with its data.
The code is in git history; `git log --diff-filter=D --name-only` will find
it, and `git checkout <sha>~1 -- research-hub/` restores it.

See `docs/CURRENT_STATE.md` for what was torn down and what was kept.
