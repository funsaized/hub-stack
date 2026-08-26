# Healthchecks

Open WebUI carries a Docker healthcheck; the rest are left plain because a
failing exporter is visible in Prometheus (`up == 0`) and adding a healthcheck
would only duplicate that signal.

| Service | Method | Why |
|---|---|---|
| `open-webui` | inline bash `/dev/tcp` probe | No extra binary or helper mount needed |

Native Ollama is supervised by systemd. Check it with `systemctl status
ollama`.

## Rules learned the hard way

1. **Use bash `/dev/tcp` when the image has bash.** It needs no extra binary.
2. **Never use `wget` on slim images** — IPv4/IPv6 resolution of `localhost`
   fails inconsistently and produces flapping healthchecks.

## Liveness is also checked from outside

`blackbox-exporter` probes the instrumented Ollama API and Open WebUI through host loopback
every 15s. The `OllamaNotAnswering` / `OpenWebUINotAnswering` alerts fire from
those probes. This records liveness history in Prometheus independently of
systemd and Docker state.
