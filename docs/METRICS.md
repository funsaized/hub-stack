# Metrics

What is collected and what it means on the native Linux host.

## The stack

```
ollama-proxy ──┐
gpu-exporter ──┤
node-exporter ─┼──> prometheus ──> grafana
blackbox ──────┘         │
                         └──> alerts.yml (visible at /alerts, not routed)

Docker API ──┐
journald ────┴──> Alloy ──> Loki ──> grafana
```

Metrics scrape interval is 15s with 15d retention. Logs retain 14d by default.

## What each source gives you

### GPU (`nvidia_smi_*`, from `gpu-exporter`)

The metrics that decide whether a model is usable.

| Metric | Why it matters |
|---|---|
| `nvidia_smi_memory_used_bytes` / `_total_bytes` | The ceiling on model size. This card has 12 GB. |
| `nvidia_smi_utilization_gpu_ratio` | 0–1, not a percentage; confirms whether generation is using the card. |
| `nvidia_smi_temperature_gpu` | Above ~83 °C expect the clock to drop. |
| `nvidia_smi_power_draw_watts` / `_limit_watts` | 350 W limit; 349.6 W benchmark peak. |
| `nvidia_smi_clocks_current_graphics_clock_hz` | Falling clock + high temp = throttling. |

Two configuration details are load-bearing and easy to get wrong:

- **NVIDIA Container Toolkit is required.** The Compose GPU reservation is
  resolved by the NVIDIA runtime registered with Docker.
- **`capabilities: [gpu, utility]`.** `gpu` alone does not put `nvidia-smi`
  in the container, and this exporter shells out to it.
- **Query fields are pinned, not `AUTO`.** With auto-detection the exporter
  may enumerate driver fields that do not produce valid Prometheus metric
  names. Pinning also keeps the dashboard contract stable across upgrades.

### Host (`node_*`, from `node-exporter`)

This reports the native Linux host: 16 logical CPUs and 32 GiB RAM. CPU use
during inference is especially useful for detecting an unintended CPU spill.

Docker data and native Ollama's `/var/lib/ollama` model store live on the root
Btrfs filesystem, so the dashboard and disk alert use `mountpoint="/"`.

### Liveness (`probe_success`, from `blackbox-exporter`)

HTTP probes of native Ollama and Open WebUI through host loopback. These say a
service is answering HTTP. They do **not** say a model will load.

### Ollama (`ollama_*`, from `ollama-proxy`)

Native Ollama listens only on `127.0.0.1:11435`. The transparent proxy owns the
existing `:11434` API, so Open WebUI, Caddy, benchmarks, OpenAI-compatible
agents, and private-LAN clients all use one measured path without endpoint
changes. It instruments native `/api/chat` and `/api/generate` JSON streams and
OpenAI-compatible `/v1/chat/completions` JSON or SSE responses.

| Metric | Why it matters |
|---|---|
| `ollama_requests_total` | Usage and failures by model, endpoint, and status. |
| `ollama_prompt_tokens_total` / `ollama_generated_tokens_total` | Input and output volume. |
| `ollama_request_duration_seconds` | End-to-end latency, including queueing. |
| `ollama_time_to_first_token_seconds` | User-visible streaming responsiveness. |
| `ollama_queue_duration_seconds` | Approximate pre-compute wait from TTFT minus load and prompt evaluation. |
| `ollama_*_evaluation_seconds_total` | Native Ollama timing volume used for prompt and decode throughput. |
| `ollama_model_*` | Loaded model size, GPU residency, and context length from `/api/ps`. |

Metrics are in-memory counters and reset when `hub-ollama-proxy` restarts;
Prometheus rate queries handle counter resets. The proxy emits structured JSON
request logs with model, status, duration, and token counts. It never records
request bodies, prompts, generated text, headers, or user identities.

Streaming OpenAI clients must request `stream_options.include_usage=true` for
token counters; latency, TTFT, status, and request counts work without it. For
Qwen reasoning through `/v1`, use `reasoning_effort: "none"` when visible output
matters more than hidden reasoning, and avoid very small `max_tokens` limits.
OpenAI `usage` supplies token volume but not Ollama evaluation durations, so
`/v1` responses do not update timing totals, last-throughput gauges, or queue
observations. User-visible serialized wait remains in TTFT and request duration.

## Container metrics

The stack does not currently run cAdvisor. The prior Docker Desktop limitation
no longer applies on native Linux, but per-container historical metrics have
not been necessary for operating this single-workload machine. Use:

- `docker stats` for a live per-container view.
- `node_*` metrics for host-wide resource pressure.
- Dozzle (http://127.0.0.1:9999) for logs.

## Logs

Alloy sends infrastructure container logs plus the native `ollama.service` and
`caddy.service` journals to Loki. `hub-open-webui` is deliberately excluded so
chat content cannot enter retained logs. Grafana's **Hub logs** dashboard
provides volume, error and warning counts, and full-text search. Dozzle remains
for immediate live Docker tailing. Set `LOKI_RETENTION` in `.env` to change the
default `336h` retention.

Loki is local-only on `127.0.0.1:3100`; it has no Caddy route. Its named volume
is persistent but not backed up.

## Alerts

In `observability/alerts.yml`, visible at http://127.0.0.1:9090/alerts. They
are **not routed anywhere** — no Alertmanager, no notifications. On a box with
one operator who is usually sitting at it, an alert that fires into a void is
still useful as a status page, and one that pages constantly is worse than
none.

| Alert | Fires when |
|---|---|
| `OllamaNotAnswering` | Ollama fails HTTP for 2m |
| `OllamaMetricsProxyDown` | Prometheus cannot scrape the instrumented API for 2m |
| `ModelPartiallyOffloaded` | A loaded model has less than 99% of its bytes in VRAM |
| `OllamaHighErrorRate` | More than 10% of generation requests fail for 5m |
| `OllamaRequestQueueBacklog` | More than one request remains active for 30s; `qwen3.5:9b` has one inference slot |
| `ObservabilityComponentDown` | A Prometheus, exporter, Loki, Alloy, or blackbox target is down for 5m |
| `OpenWebUINotAnswering` | UI fails HTTP for 5m |
| `GpuMemoryNearlyFull` | VRAM >95% for 5m — a larger model will not load |
| `GpuSustainedHighTemperature` | >85 °C for 10m — expect throttling |
| `ModelDiskNearlyFull` | <10% free on `/` — model pulls will fail |
| `HostMemoryNearlyExhausted` | <10% host RAM available for 10m |

## Reading the dashboards

Grafana provisions four dashboards at http://127.0.0.1:3000:

- **Local LLM hub** keeps the original at-a-glance GPU and host view.
- **Model usage and performance** shows request/error rates, token volume,
  timing-backed throughput, latency/TTFT percentiles, context, and GPU residency.
- **Host hardware and system** expands CPU, RAM, swap, disk IO, network, GPU,
  thermals, power, and clocks.
- **Hub logs** searches retained Docker, Ollama, and Caddy logs.

The panel worth understanding is **VRAM used vs total**. When `used`
approaches `total`, the next model either fails to load or spills to CPU. The
spill is not an error and nothing will warn you at request time. That is the
failure mode this dashboard exists to make visible.

## Apply and verify

Moving Ollama behind the proxy changes a systemd unit and must be applied in
this order so two processes never contend for `:11434`:

```bash
sudo install -Dm644 systemd/ollama.service.d/override.conf \
  /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
docker compose up -d
# Existing Prometheus and Grafana containers do not restart when only the
# contents of their bind-mounted configuration directories change.
docker compose restart prometheus grafana
curl -fsS http://127.0.0.1:11434/metrics >/dev/null
curl -fsS http://127.0.0.1:3100/ready
```

Then generate one response and check **Model usage and performance**. If the
proxy is intentionally removed, restore `OLLAMA_HOST=0.0.0.0:11434` before
stopping its container.
