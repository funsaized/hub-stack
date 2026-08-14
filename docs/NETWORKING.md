# Network exposure

Every published port binds to the Windows host's loopback interface. Nothing
in this stack listens on the LAN by default, and there are no inbound
surfaces beyond the five below.

| Surface | Default host address | Purpose |
|---|---:|---|
| Ollama API | `127.0.0.1:11435` | Model server (container port is 11434) |
| Open WebUI | `127.0.0.1:8080` | Chat UI |
| Grafana | `127.0.0.1:3000` | Dashboard |
| Prometheus | `127.0.0.1:9090` | Metrics and alert state |
| Dozzle | `127.0.0.1:9999` | Container logs |

`gpu-exporter`, `node-exporter` and `blackbox-exporter` publish no host ports.
Prometheus reaches them over Compose DNS (`gpu-exporter:9835` and so on).

## Outbound

**None, in normal operation.** No component in this stack calls a hosted API.
Outbound traffic happens only when you pull a model or an image.

This is a change from the previous deployment, which called the MiniMax API to
judge claim faithfulness and ran a crawler against arbitrary web pages. Both
are gone (see `CURRENT_STATE.md`), and with them the `crawler` network that
existed to sandbox the crawler.

## Exposing a port deliberately

Set the relevant `*_BIND_ADDRESS` to a Tailscale or trusted-LAN address:

```bash
OLLAMA_BIND_ADDRESS=100.x.y.z    # Tailscale interface
```

Before doing this for **Open WebUI**, set `WEBUI_AUTH=true`. Authentication is
off by default, which is safe on loopback and is not safe anywhere else.

Grafana is configured for anonymous admin access with the login form disabled
(`GF_AUTH_ANONYMOUS_ENABLED`, `GF_AUTH_DISABLE_LOGIN_FORM`). Reverse that
before exposing port 3000.

Ollama itself has **no authentication of any kind**. Anything that can reach
port 11434 can load models, generate, and delete models. Only put it on a
network you control.
