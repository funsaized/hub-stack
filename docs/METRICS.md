# Metrics

What is collected and what it means on the native Linux host.

## The stack

```
gpu-exporter ──┐
node-exporter ─┼──> prometheus ──> grafana ("Local LLM hub")
blackbox ──────┘         │
                         └──> alerts.yml (visible at /alerts, not routed)
```

Scrape interval 15s, retention 15d. All five targets verified `up`.

## What each source gives you

### GPU (`nvidia_smi_*`, from `gpu-exporter`)

The metrics that decide whether a model is usable.

| Metric | Why it matters |
|---|---|
| `nvidia_smi_memory_used_bytes` / `_total_bytes` | The ceiling on model size. This card has 12 GB. |
| `nvidia_smi_utilization_gpu_ratio` | 0–1, not a percentage. Peaked at 0.96 during the graded run. |
| `nvidia_smi_temperature_gpu` | Above ~83 °C expect the clock to drop. |
| `nvidia_smi_power_draw_watts` / `_limit_watts` | 350 W limit; 339.6 W benchmark peak. |
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

## Container metrics

The stack does not currently run cAdvisor. The prior Docker Desktop limitation
no longer applies on native Linux, but per-container historical metrics have
not been necessary for operating this single-workload machine. Use:

- `docker stats` for a live per-container view.
- `node_*` metrics for host-wide resource pressure.
- Dozzle (http://127.0.0.1:9999) for logs.

## What is not measured, and would need work

**Ollama exposes no Prometheus endpoint.** There is no scrape for tokens per
second, queue depth, model load time, or context utilization. The throughput
numbers in the README came from the `/api/generate` response body
(`eval_count`, `eval_duration`) and are graded by `scripts/benchmark.py`.

If per-request performance matters later, the options are a sidecar that
polls `/api/ps` and turns it into metrics, or a proxy in front of `:11434`
that records timings. Neither exists today.

## Alerts

In `observability/alerts.yml`, visible at http://127.0.0.1:9090/alerts. They
are **not routed anywhere** — no Alertmanager, no notifications. On a box with
one operator who is usually sitting at it, an alert that fires into a void is
still useful as a status page, and one that pages constantly is worse than
none.

| Alert | Fires when |
|---|---|
| `OllamaNotAnswering` | Ollama fails HTTP for 2m |
| `OpenWebUINotAnswering` | UI fails HTTP for 5m |
| `GpuMemoryNearlyFull` | VRAM >95% for 5m — a larger model will not load |
| `GpuSustainedHighTemperature` | >85 °C for 10m — expect throttling |
| `ModelDiskNearlyFull` | <10% free on `/` — model pulls will fail |
| `HostMemoryNearlyExhausted` | <10% host RAM available for 10m |

## Reading the dashboard

Grafana → "Local LLM hub" (http://127.0.0.1:3000). Top row is the at-a-glance
state: is Ollama up, VRAM used, GPU utilization, temperature, disk free.

The panel worth understanding is **VRAM used vs total**. When `used`
approaches `total`, the next model either fails to load or spills to CPU. The
spill is not an error and nothing will warn you at request time. That is the
failure mode this dashboard exists to make visible.
