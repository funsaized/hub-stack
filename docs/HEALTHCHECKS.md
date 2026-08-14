# Healthchecks

Two services carry Docker healthchecks; the rest are left plain because a
failing exporter is visible in Prometheus (`up == 0`) and adding a healthcheck
would only duplicate that signal.

| Service | Method | Why |
|---|---|---|
| `ollama` | `bash /healthcheck/healthcheck.sh 11434` | The image has bash; the helper uses bash `/dev/tcp`, no extra binaries |
| `open-webui` | inline bash `/dev/tcp` probe | Same technique, no helper mount needed |

`open-webui` also declares `depends_on: ollama: condition: service_healthy`,
so it will not start until Ollama answers.

## The helper scripts

- `healthcheck/healthcheck.sh` — bash `/dev/tcp` probe, mounted read-only
  into containers that have bash.
- `healthcheck/healthcheck-py3.py` — Python socket probe, retained for images
  that ship `sh` and Python but not bash. Nothing currently uses it.

## Rules learned the hard way

1. **If the image has bash and the `/healthcheck` mount**: use
   `bash /healthcheck/healthcheck.sh <port>`. Fastest, no dependencies.
2. **If the image has only `sh` plus Python**: use `healthcheck-py3.py`.
3. **Never use `wget` on slim images** — IPv4/IPv6 resolution of `localhost`
   fails inconsistently and produces flapping healthchecks.

## Liveness is also checked from outside

`blackbox-exporter` probes `hub-ollama:11434` and `hub-open-webui:8080` over
HTTP every 15s, and the `OllamaNotAnswering` / `OpenWebUINotAnswering` alerts
fire from those probes. That is a deliberate second opinion: a Docker
healthcheck tells the daemon whether to restart a container, while the probe
records history you can look at afterwards in Prometheus.
