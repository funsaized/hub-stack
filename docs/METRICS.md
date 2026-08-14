# Metrics

What is collected, what it means, and — importantly — what does **not** work
on this platform. Everything below was measured on the running stack on
2026-08-14, not inferred from documentation.

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
| `nvidia_smi_utilization_gpu_ratio` | 0–1, not a percentage. ~0.88 during generation. |
| `nvidia_smi_temperature_gpu` | Above ~83 °C expect the clock to drop. |
| `nvidia_smi_power_draw_watts` / `_limit_watts` | 350 W limit; ~21 W idle. |
| `nvidia_smi_clocks_current_graphics_clock_hz` | Falling clock + high temp = throttling. |

Two configuration details are load-bearing and easy to get wrong:

- **No `devices:` mapping.** Under WSL2 there is no `/dev/nvidiactl` or
  `/dev/nvidia0` to bind. Naming them makes the container fail to start with
  `error gathering device information`. The `deploy.resources.reservations.devices`
  nvidia driver entry is what works — it is what `--gpus all` resolves to.
- **`capabilities: [gpu, utility]`.** `gpu` alone does not put `nvidia-smi`
  in the container, and this exporter shells out to it.
- **Query fields are pinned, not `AUTO`.** With auto-detection the exporter
  enumerates every field the driver offers, and on driver 610.88 one of them
  (`power_smoothing.curr_profile.ramp_down_rate [W/s]`) is not a valid
  Prometheus metric name, which panics the process at startup.

### Host (`node_*`, from `node-exporter`)

**This reports the WSL2 VM, not Windows.** That is still the machine Ollama
runs on, so it is the right thing to watch — but do not read `node_memory_*`
as your PC's RAM. The VM sees 20 GB.

Disk lives at `mountpoint="/var/lib"` (the Docker Desktop data disk, ~940 GB
free), which is where image layers and the `ollama_data` volume — the models —
actually sit. There is no meaningful `/` on this platform.

### Liveness (`probe_success`, from `blackbox-exporter`)

HTTP probes of `hub-ollama:11434` and `hub-open-webui:8080`. These say a
service is answering HTTP. They do **not** say a model will load.

## What does not work here: cAdvisor

Per-container CPU and memory would normally come from cAdvisor. **It returns
nothing usable under Docker Desktop on Windows.** Measured: with
`--docker_only=true` it emits a single series, `container_memory_usage_bytes{id="/"}`.
Run again without `--docker_only`, with `/var/lib/docker` and `/dev/disk`
mounted, it emits **zero** `container_memory_usage_bytes` series at all.

Docker Desktop's VM does not expose the cgroup and container metadata cAdvisor
expects. It was removed rather than left in place emitting one meaningless
number.

The practical substitutes:

- `docker stats` for a live per-container view.
- `node_*` metrics, since Ollama dominates the VM — when the VM is busy, that
  is Ollama.
- Dozzle (http://127.0.0.1:9999) for logs.

## What is not measured, and would need work

**Ollama exposes no Prometheus endpoint.** There is no scrape for tokens per
second, queue depth, model load time, or context utilization. The throughput
numbers in the README came from the `/api/generate` response body
(`eval_count`, `eval_duration`), read by hand.

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
| `ModelDiskNearlyFull` | <10% free on `/var/lib` — model pulls will fail |
| `HostMemoryNearlyExhausted` | <10% RAM available in the VM for 10m |

## Reading the dashboard

Grafana → "Local LLM hub" (http://127.0.0.1:3000). Top row is the at-a-glance
state: is Ollama up, VRAM used, GPU utilization, temperature, disk free.

The panel worth understanding is **VRAM used vs total**. When `used`
approaches `total`, the next model either fails to load or spills to CPU. The
spill is not an error and nothing will warn you at request time — it just gets
about 37× slower. That is the failure mode this dashboard exists to make
visible.
