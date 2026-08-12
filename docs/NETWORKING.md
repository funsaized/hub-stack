# Network exposure and optional remote Ollama access

The default deployment publishes only user-facing surfaces, all on the Windows
host's loopback interface. Databases and ingestion dependencies remain reachable
between containers by Compose DNS but have no host or LAN listener.

| Surface | Default host address | Purpose |
|---|---:|---|
| Ollama | `127.0.0.1:11435` | Local model API; optionally configurable |
| Research Hub | `127.0.0.1:8000` | Research/query API |
| Open WebUI (`webui`) | `127.0.0.1:8080` | Optional chat UI |
| Dozzle (`logs`) | `127.0.0.1:8888` | Optional container logs |
| SearXNG | `127.0.0.1:8889` | Search UI |
| Uptime Kuma (`uptime`) | `127.0.0.1:3001` | Optional monitoring UI |

Redis, Qdrant, and Crawl4AI publish no host ports. Use `docker compose
exec` for maintenance and Compose service names such as `hub-qdrant:6333` for
container-to-container monitoring.

## Outbound exception: MiniMax judge gate (HUB-034)

Research Hub and the Research Worker call the MiniMax API
(`https://api.minimax.io`) to judge claim faithfulness during report
synthesis — since HUB-034 (2026-08-12) this is the only claim gate. It is a
deliberate, operator-accepted exception to the local-only premise: retained
corpus evidence spans and drafted claims leave the machine for judging. The
Subscription Key lives only in the gitignored `.env` (required `${VAR:?}`
expansion) and is sent only as an Authorization header — it never appears in
logs, diagnostics, or request bodies. No inbound surface changes.

## Allow Ollama from another device

Ollama has no authentication in this deployment. Prefer binding its published
port to one specific trusted interface instead of every interface.

1. Find the Windows host's LAN or Tailscale IPv4 address.
2. Set that exact address in `.env`, for example:

   ```dotenv
   OLLAMA_BIND_ADDRESS=100.101.102.103
   OLLAMA_HOST_PORT=11435
   ```

3. Recreate only Ollama:

   ```bash
   docker compose up -d --force-recreate ollama
   docker compose port ollama 11434
   ```

4. Permit TCP 11435 (or `OLLAMA_HOST_PORT`) in Windows Firewall only for the
   trusted subnet or Tailscale interface/profile. Test
   `http://HOST_ADDRESS:11435/api/tags` remotely.

Using `OLLAMA_BIND_ADDRESS=0.0.0.0` exposes Ollama on every host interface and is
not recommended. Never forward the Ollama host port from the router or expose it directly
to the public internet. Keep Research Hub and the management UIs loopback-only;
put an authenticated reverse proxy in front of any surface that must be remote.

To return Ollama to host-only access, set `OLLAMA_BIND_ADDRESS=127.0.0.1` (or
remove it from `.env`) and recreate the service again.

## Verify bindings

```bash
docker compose ps
docker compose config
```

Expected published addresses are `127.0.0.1`, except Ollama when explicitly
configured otherwise. No `ports` entry should exist for Redis, Qdrant,
or Crawl4AI.

The hub defaults to host port 11435 because Docker Desktop's built-in model runner
commonly reserves `127.0.0.1:11434`. Container-to-container traffic still uses
`ollama:11434`. Change `OLLAMA_HOST_PORT` only after checking for a host conflict.
