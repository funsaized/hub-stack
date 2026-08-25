# Network exposure

Docker web surfaces bind to the Linux host's loopback interface. Caddy proxies
selected surfaces over HTTPS on the Tailscale interface. Native Ollama is also
intentionally available directly on the private LAN.

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

## Tailscale HTTPS

Caddy binds its hub routes only to the host's Tailscale address. Wildcard DNS
and ACME certificates provide these private-network URLs:

| Surface | Primary URL | Alias |
|---|---|---|
| Open WebUI | `https://hub.nzxt.dev.s11a.com` | `openwebui` |
| Grafana | `https://dashboard.nzxt.dev.s11a.com` | `grafana` |
| Ollama API | `https://ollama.nzxt.dev.s11a.com` | `models` |
| Dozzle | `https://logs.nzxt.dev.s11a.com` | `dozzle` |
| Prometheus | `https://metrics.nzxt.dev.s11a.com` | `prometheus` |

Install the tracked route snippet and pass the machine's Tailscale address as
its Caddy import argument:

```bash
sudo install -d -m 0755 /etc/caddy/conf.d
sudo install -m 0644 caddy/conf.d/hub-stack.caddy \
  /etc/caddy/conf.d/hub-stack.caddy
sudo install -D -m 0644 caddy/systemd/override.conf \
  /etc/systemd/system/caddy.service.d/override.conf
sudo systemctl daemon-reload
```

The host's `/etc/caddy/Caddyfile` must contain:

```caddyfile
import conf.d/*.caddy <tailscale-address>
```

This host uses a Caddy build with the Netlify DNS module and loads
`NETLIFY_AUTH_TOKEN` from a root-owned systemd environment file. Validate and
reload after changing the route snippet:

```bash
sudo /usr/local/bin/caddy-netlify validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

The tracked systemd override intentionally omits Caddy's `--environ` flag,
which would print the Netlify token to the system journal.

Open WebUI, Grafana, Ollama, Dozzle, and Prometheus do not authenticate these
requests. Tailnet membership is therefore the access-control boundary. Do not
bind these routes to a public or untrusted interface without enabling
application-level authentication.

## Outbound

No model or application request calls a hosted API. Outbound traffic normally
occurs only when pulling a model or image, or when Caddy issues and renews TLS
certificates through the ACME and Netlify DNS APIs.

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

Prefer adding a route to the tailnet-only Caddy listener. To expose a Docker
port directly, set the relevant `*_BIND_ADDRESS` to a trusted-LAN address.

Before allowing **Open WebUI** on a less-trusted network, set
`WEBUI_AUTH=true`. Authentication is off by default.

Grafana is configured for anonymous admin access with the login form disabled
(`GF_AUTH_ANONYMOUS_ENABLED`, `GF_AUTH_DISABLE_LOGIN_FORM`). Reverse that
before exposing it beyond the tailnet.

Anything that can reach Ollama can load models, generate, and delete models.
Only allow networks you control.
