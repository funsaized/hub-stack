Docker healthchecks in this stack are inconsistent because each container image has different available binaries. The general pattern:

## Rules

1. **If the image has bash + the `/healthcheck` mount**: use `bash /healthcheck/healthcheck.sh <port>` (uses bash /dev/tcp, fastest)
2. **If the image has only sh**: use `python3 /healthcheck/healthcheck-py3.py <port>` (SearXNG)
3. **If the image has curl**: bake it into the Dockerfile and use that (research-hub)
4. **If the image has its own CLI**: use it (postgres, redis)
5. **Never use wget on slim images**: IPv6/IPv4 resolution fails on `localhost`

## Helper scripts

`healthcheck/healthcheck.sh` — bash /dev/tcp probe
`healthcheck/healthcheck-py3.py` — Python socket probe

Both mounted via `./healthcheck:/healthcheck:ro` into the container.

## Per-service

| Service | Method | Why |
|---|---|---|
| ollama | bash `/healthcheck/healthcheck.sh 11434` | has bash |
| qdrant | bash `/healthcheck/healthcheck.sh 6333` | has bash |
| redis | `redis-cli ping` | built-in |
| postgres | `pg_isready -U hub -d hub` | built-in |
| searxng | python3 socket probe | no bash |
| crawl4ai | bash `/healthcheck/healthcheck.sh 11235` | has bash |
| research-hub | Dockerfile `curl -f http://localhost:8000/health` | has curl, IPv6-safe |
| open-webui | sh `echo > /dev/tcp/localhost/8080` | has bash actually, but compose uses sh-style |
| dozzle | (none) | no healthcheck section |
| uptime-kuma | (none) | relies on its own UI |

## Why compose-level healthcheck overrides fail

If a compose file has `healthcheck:`, it overrides the Dockerfile's HEALTHCHECK. So once you set `wget` in compose, the Dockerfile's curl version is ignored. The research-hub Dockerfile has a working curl healthcheck but the compose was overriding it — fixed by removing the compose-level block.

## Troubleshooting

Container shows "unhealthy" but the endpoint works:

```bash
# Check the actual healthcheck command
docker inspect <container> --format '{{json .Config.Healthcheck}}'

# Run the healthcheck manually inside the container
docker exec <container> <the-test-command>

# Check the latest health log
docker inspect <container> --format '{{json .State.Health}}'
```

Common causes:
- Binary missing (wget on image that doesn't have it → use curl)
- IPv6/IPv4 mismatch (wget on localhost resolves to ::1, service listens on 0.0.0.0)
- Healthcheck runs before start_period expires (default 30s, research-hub needs 60s)
