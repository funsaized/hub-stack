# Network exposure

Docker web surfaces bind to the Linux host's loopback interface. Native Ollama
is intentionally available on the private LAN.

| Surface | Default host address | Purpose |
|---|---:|---|
| Ollama API | `0.0.0.0:11434` | Native server; UFW-scoped to LAN and Docker |
| Open WebUI | `127.0.0.1:8080` | Chat UI |
| Grafana | `127.0.0.1:3000` | Dashboard |
| Prometheus | `127.0.0.1:9090` | Metrics and alert state |
| Dozzle | `127.0.0.1:9999` | Container logs |

`gpu-exporter` and `node-exporter` publish metrics only on host loopback.
Prometheus and `blackbox-exporter` use host networking but also listen only on
loopback. Open WebUI discovers the current Compose bridge gateway at startup
and reaches native Ollama there.

## Outbound

**None, in normal operation.** No component in this stack calls a hosted API.
Outbound traffic happens only when you pull a model or an image.

This is a change from the previous deployment, which called the MiniMax API to
judge claim faithfulness and ran a crawler against arbitrary web pages. Both
are gone (see `CURRENT_STATE.md`), and with them the `crawler` network that
existed to sandbox the crawler.

## Ollama exposure

Ollama binds `0.0.0.0:11434` through the tracked systemd override. UFW limits
access to the private LAN and local Docker networks:

```bash
sudo ufw allow from 192.168.1.0/24 to any port 11434 proto tcp
sudo ufw allow from 172.16.0.0/12 to any port 11434 proto tcp
```

UFW must be active with default-deny incoming before Ollama starts. Change the
first CIDR when the LAN changes, and change the Docker CIDR if custom address
pools are configured. IPv6 access is intentionally denied. Ollama has no
authentication; these firewall rules are its access control.

## Exposing another port deliberately

Set the relevant `*_BIND_ADDRESS` to a Tailscale or trusted-LAN address.

Before doing this for **Open WebUI**, set `WEBUI_AUTH=true`. Authentication is
off by default, which is safe on loopback and is not safe anywhere else.

Grafana is configured for anonymous admin access with the login form disabled
(`GF_AUTH_ANONYMOUS_ENABLED`, `GF_AUTH_DISABLE_LOGIN_FORM`). Reverse that
before exposing port 3000.

Anything that can reach Ollama can load models, generate, and delete models.
Only allow networks you control.
