# Setup

## Prerequisites

### Hardware (validated)
- AMD Ryzen 7 5800X or similar 8C/16T CPU
- 32 GB DDR4-3200 (dual-channel)
- NVIDIA RTX 3080 Ti (12 GB VRAM) or RTX 4070+ equivalent
- 500 GB+ NVMe SSD (you need 80 GB for images + model weights)
- Windows 11 Home/Pro 24H2

### Software
- Windows 11 with WSL2 enabled
- Ubuntu 24.04 in WSL2 (Microsoft Store)
- Docker Desktop for Windows
- GitHub CLI (optional, for dev workflow)

## 1. Windows host setup

### Enable WSL2
```powershell
# Run in PowerShell as Administrator
wsl --install
wsl --set-default-version 2
```

### Install Ubuntu 24.04
```powershell
wsl --install -d Ubuntu-24.04
```

### Verify WSL2 is running
```powershell
wsl -l -v
# Should show Ubuntu with version 2
```

### Enable BIOS features (for performance)
Reboot → enter BIOS → enable:
- **DOCP** (or XMP) — RAM runs at rated speed (3200 MT/s instead of 2400)
- **Above 4G Decoding** — required for large GPU BAR
- **Re-Size BAR** — 3-8% inference speedup

### Install Docker Desktop
1. Download from docker.com
2. Install with WSL2 backend (default)
3. Restart
4. Accept the license agreement
5. Wait for "Engine running" indicator

## 2. WSL2 Ubuntu setup

Open Ubuntu from Start menu (or `wsl -d Ubuntu` from PowerShell).

### Install system packages
```bash
sudo apt-get update
sudo apt-get install -y build-essential ca-certificates curl gnupg lsb-release unzip wget jq tmux zstd uidmap dbus-user-session btop
```

### Install Rust toolchain
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
. "$HOME/.cargo/env"
```

### Install Node version manager (fnm)
```bash
curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell
```

### Install Python package manager (uv)
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path
```

### Install GitHub CLI
```bash
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
sudo apt-get update
sudo apt-get install -y gh
```

### Install modern CLI tools via cargo
```bash
cargo install fd-find bat ripgrep zoxide eza lazydocker --locked
```

### Install fzf
```bash
git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
~/.fzf/install --all --no-bash --no-fish
```

### Set up bashrc
Add to `~/.bashrc`:

```bash
# === Hub stack dev tools ===
export PATH="$HOME/.local/bin:$PATH"
export PATH="$HOME/.cargo/bin:$PATH"
export PATH="$HOME/.fnm:$PATH"
[ -f ~/.fzf.bash ] && source ~/.fzf.bash
[ -f ~/.fzf/shell/key-bindings.bash ] && source ~/.fzf/shell/key-bindings.bash
if command -v fnm >/dev/null 2>&1; then
  eval "$(fnm env --use-on-cd --shell bash)"
fi
```

Reload: `source ~/.bashrc`

### Install lazydocker binary (curl fallback)
```bash
curl -fsSL https://github.com/jesseduffield/lazydocker/releases/download/v0.23.3/lazydocker_0.23.3_Linux_x86_64.tar.gz | tar -xz -C /tmp/
sudo mv /tmp/lazydocker /usr/local/bin/lazydocker && chmod +x /usr/local/bin/lazydocker
```

## 3. Docker setup

### Set up Docker PATH for WSL
On Windows, Docker's CLI lives at `C:\Program Files\Docker\Docker\resources\bin\docker.exe`. From WSL, the equivalent path is `/mnt/c/Program Files/Docker/Docker/resources/bin/`.

Add to `~/.bashrc`:
```bash
# Docker CLI from Windows host
export PATH="/mnt/c/Program Files/Docker/Docker/resources/bin:/mnt/c/Program Files/Docker/cli-plugins:$PATH"
```

### Verify docker works
```bash
docker --version
docker compose version
docker run --rm hello-world
```

The hello-world should print "Hello from Docker!".

### Push Docker PATH into systemd-managed shell
Some WSL setups use a non-interactive shell that doesn't load `.bashrc`. If you get `docker: command not found`, run:

```bash
# Add to ~/.bash_profile or ~/.profile
source ~/.bashrc
```

## 4. Clone the repo

```bash
cd ~
git clone https://github.com/<your-username>/hub-stack.git
cd hub-stack
```

## 5. Configure secrets

Compose refuses to start until the required secrets exist in `.env`:

```bash
cp .env.example .env
# Fill in the required values (see the comments in .env.example):
sed -i "s/^SEARXNG_SECRET=$/SEARXNG_SECRET=$(openssl rand -hex 32)/" .env
sed -i "s/^CRAWL4AI_API_TOKEN=$/CRAWL4AI_API_TOKEN=$(openssl rand -hex 32)/" .env
```

`.env` is gitignored — never commit it. `WEBUI_SECRET_KEY` may stay empty;
Open WebUI generates and persists its own key when unset.

## 6. Start the stack

```bash
docker compose up -d
```

First run takes longer while the verifier image is built:
- Downloads/builds the images for eight required services (size varies with image versions)
- Bakes the immutable claim-verifier model revision into the Research Hub image; runtime model
  access is offline-only
- Builds the research-hub image locally
- Research Hub pulls `nomic-embed-text` (~300 MB) on first run
- Pull `qwen3.5:9b` separately before selecting the default generation path

On the reference workstation, an already-pulled stopped stack reached API health
in 39.4 seconds. First-time image and model downloads take longer.

### Verify
```bash
docker compose ps
# Required services with healthchecks should become healthy
```

The Research Worker and Claim Verifier have no host ports. The worker consumes the durable Redis queue and
shares the Research-Hub image. After application changes, rebuild both processes:

```bash
docker compose up -d --build claim-verifier research-hub research-worker
docker compose logs --tail=100 research-worker
```

Worker behavior can be tuned in `.env` with `WORKER_LEASE_SECONDS`,
`WORKER_HEARTBEAT_SECONDS`, `JOB_TIMEOUT_SECONDS`, and `JOB_MAX_ATTEMPTS`.
Embedding batches are bounded by `EMBEDDING_BATCH_SIZE` and
`EMBEDDING_BATCH_CHARS`; completed Qdrant writes are checkpointed in the retained
document store. See `docs/DOCUMENT_STORE.md` for inspection and rebuilds.
`CLAIM_VERIFIER_TIMEOUT_SECONDS` controls only the fail-closed request timeout. The model,
revision, CPU device, batch size eight, 512-token limit, and `0.97` threshold are frozen in
code and require a new untouched evaluation before change.
The heartbeat must remain shorter than the lease. On shutdown, the worker drains
its current task or releases it; after an unclean stop, lease expiry and periodic
reconciliation requeue the job.

Pipeline metrics and alert rules are optional. Start them with
`docker compose --profile observability up -d`, then open Grafana at
http://localhost:3002 for the provisioned dashboard or Prometheus at
http://localhost:9090. See `docs/OBSERVABILITY.md` for metric names, structured
log fields, correlation behavior, and thresholds.

## 7. Set up Uptime Kuma monitors

To use corpus-backed chat, start Open WebUI with:

```bash
docker compose --profile webui up -d
```

Open http://localhost:8080 and select `research-corpus`. Direct Ollama models
do not query the retained research corpus.

Start it first with `docker compose --profile uptime up -d`. Other optional
services only resolve from Kuma when their profiles are also running.

1. Open http://localhost:3001
2. Create admin account on first visit
3. Add the core service monitors:

| Name | Type | URL |
|---|---|---|
| Ollama | HTTP(s) | http://ollama:11434/api/tags |
| Qdrant | HTTP(s) | http://qdrant:6333/healthz |
| Redis | TCP Port | redis:6379 |
| Dozzle | HTTP(s) | http://dozzle:8080/ |
| SearXNG | HTTP(s) | http://searxng:8080/ |
| Crawl4AI | HTTP(s) | http://crawl4ai:11235/health |
| Research-Hub | HTTP(s) | http://research-hub:8000/livez |
| Open-WebUI | HTTP(s) | http://open-webui:8080/ |

Uptime Kuma shares the Compose network, so use service DNS names rather than
host-published ports. Internal dependencies intentionally have no host listener.

## 8. Verify the pipeline

```bash
# Submit a research job
python3 test_research.py

# Query the knowledge base
python3 test_query.py

# Or use the CLI
chmod +x research-hub/bin/research
./research-hub/bin/research submit "your topic" --depth 5
./research-hub/bin/research list
./research-hub/bin/research rag "your question"
```

## Troubleshooting

### "Docker daemon not running"
Open Docker Desktop. Wait for the green "Engine running" indicator.

### "Cannot connect to the Docker daemon"
The PATH is wrong. Run:
```bash
export PATH="/mnt/c/Program Files/Docker/Docker/resources/bin:/mnt/c/Program Files/Docker/cli-plugins:$PATH"
docker version
```

### "qwen3.5:9b not found" from inside a container
The model exists on disk but is not in Ollama's running index. Restart Ollama:
```bash
docker compose restart ollama
# Then pull via API (not via docker exec)
curl -X POST http://localhost:11435/api/pull -d '{"name":"qwen3.5:9b"}'
```

### "Crawl4AI failed" in research jobs
Crawl4AI binds to 127.0.0.1 by default. Make sure the compose has:
```yaml
crawl4ai:
  volumes:
    - ./crawl4ai-config.yml:/app/config.yml:ro
  environment:
    - CRAWL4AI_API_TOKEN=${CRAWL4AI_API_TOKEN:?}  # value comes from .env
    - APP_HOST=0.0.0.0
```
Also confirm `CRAWL4AI_API_TOKEN` is set in `.env` — research-hub and the worker
authenticate to Crawl4AI with the same value.

### Healthcheck stays "unhealthy" but the endpoint works
Different containers have different binaries. See docs/HEALTHCHECKS.md.

### Out of VRAM
You're loading too many models. Check:
```bash
docker exec hub-ollama ollama ps
```
Keep only models under 6 GB loaded simultaneously.

### Lost data after restart
Make sure you're using `docker compose up -d` (which keeps volumes) not `docker compose down -v` (which deletes volumes).

## Optional: Tailscale for remote access

```bash
# Install
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Authenticate via the URL it prints
# Then your machine is reachable at <hostname>.tail-<hash>.ts.net
```

The stack remains loopback-only after installing Tailscale. See
`docs/NETWORKING.md` for optional remote Ollama access. Use an authenticated
reverse proxy for remote UI or Research API access.

## Optional: Discord/Telegram alerts

In Uptime Kuma → Settings → Notifications → "+ Add" → Discord or Telegram.

Paste the webhook URL, test, save. Then enable on each monitor.

## Next steps

- Add 9 monitors in Uptime Kuma
- Review Prometheus alerts and the Grafana pipeline dashboard
- Run a research job and verify RAG works
- Tailscale for remote access
- See NEXT_STEPS.md for the roadmap
