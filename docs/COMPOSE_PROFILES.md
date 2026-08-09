# Compose topology and profiles

HUB-016 keeps the default deployment limited to the research and RAG data path.
`docker compose up -d` starts seven containers: Ollama, Qdrant, Redis, SearXNG,
Crawl4AI, the Research-Hub API, and its dedicated ingestion worker.

Postgres is no longer defined because no application component used it. Its
existing `hub_postgres_data` named volume was deliberately not deleted during the
deployment change. It never held application-owned data, is not part of the
backup set, and may be inspected and removed separately by an operator.

## Optional profiles

| Profile | Added services | Command | Measured idle memory |
|---|---|---|---:|
| default | Seven required runtime containers | `docker compose up -d` | 1,014 MiB |
| `webui` | Open WebUI | `docker compose --profile webui up -d` | +678 MiB |
| `logs` | Dozzle (actions disabled) | `docker compose --profile logs up -d` | +25 MiB |
| `uptime` | Uptime Kuma | `docker compose --profile uptime up -d` | +119 MiB |
| `observability` | Prometheus and Grafana | `docker compose --profile observability up -d` | +75 MiB |

The `webui` profile configures two chat paths: direct Ollama models and the
`research-corpus` OpenAI-compatible model backed by Research-Hub retrieval.

Profiles are independent and can be combined. The figures are one
`docker stats --no-stream` sample on the documented Windows 11/WSL2 workstation
on 2026-08-09, rounded to MiB. Ollama accounted for 341 MiB of the default sample
after restart; memory changes when a model is loaded. These are reference points,
not limits.

From a stopped, already-pulled deployment, the default profile reached a healthy
Research-Hub API in 39.4 seconds. A warm application rebuild/recreate took 4.9
seconds. Image download and first-time model pull time is excluded.

Removing or stopping any optional profile does not affect Research-Hub readiness.
Use `docker compose config --services` to inspect the default and
`docker compose --profile <name> config --services` to inspect a profile.
