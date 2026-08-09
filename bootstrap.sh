#!/usr/bin/env bash
# Day 1 dev tools bootstrap for WSL2 Ubuntu 24.04
# Idempotent: safe to re-run

set -euo pipefail

echo "=== Updating apt cache ==="
sudo apt-get update -qq

echo "=== Installing system packages ==="
sudo apt-get install -y -qq \
  build-essential ca-certificates curl gnupg lsb-release unzip wget \
  jq tmux zstd uidmap dbus-user-session btop

echo "=== Creating paths ==="
mkdir -p ~/bin ~/projects

# === fnm (Node version manager) ===
if ! command -v fnm >/dev/null; then
  echo "=== Installing fnm ==="
  curl -fsSL https://fnm.vercel.app/install | bash -s -- --skip-shell
fi

# === uv (Python package manager) ===
if ! command -v uv >/dev/null; then
  echo "=== Installing uv ==="
  curl -LsSf https://astral.sh/uv/install.sh | sh -s -- --no-modify-path
fi

# === Rust ===
if ! command -v rustup >/dev/null; then
  echo "=== Installing rustup ==="
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
fi

# === GitHub CLI ===
if ! command -v gh >/dev/null; then
  echo "=== Installing GitHub CLI ==="
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null
  sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq gh
fi

# === Modern CLI tools via cargo (fd, bat, eza, ripgrep, zoxide, lazydocker) ===
if command -v cargo >/dev/null; then
  # cargo install can be slow; only install missing ones
  for pkg in fd-find bat ripgrep zoxide eza; do
    if ! command -v "${pkg}" >/dev/null && ! command -v "${pkg/fd-find/fd}" >/dev/null; then
      echo "=== Installing ${pkg} via cargo ==="
      cargo install "${pkg}" --locked 2>&1 | tail -2 || true
    fi
  done
  if ! command -v lazydocker >/dev/null; then
    echo "=== Installing lazydocker ==="
    cargo install lazydocker --locked 2>&1 | tail -2 || true
  fi
fi

# === fzf (fuzzy finder) ===
if [ ! -d ~/.fzf ]; then
  echo "=== Installing fzf ==="
  git clone --depth 1 https://github.com/junegunn/fzf.git ~/.fzf
  ~/.fzf/install --all --no-bash --no-fish
fi

# === Add tool paths to bashrc (idempotent) ===
BASHRC="$HOME/.bashrc"
{
  echo ""
  echo "# === Hub stack dev tools ==="
  echo 'export PATH="$HOME/.local/bin:$PATH"'  # uv
  echo 'export PATH="$HOME/.cargo/bin:$PATH"'  # rust
  echo 'export PATH="$HOME/.fnm:$PATH"'        # fnm
  echo 'eval "$(fnm env --use-on-cd --shell bash)" 2>/dev/null || true'
  echo 'source ~/.fzf.bash 2>/dev/null || true'
  echo '[ -f ~/.fzf/shell/key-bindings.bash ] && source ~/.fzf/shell/key-bindings.bash'
} >> "$BASHRC"

echo ""
echo "=== Done. Tool versions: ==="
echo "node:  $(node --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "fnm:   $(fnm --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "uv:    $(uv --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "rust:  $(rustc --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "gh:    $(gh --version 2>/dev/null | head -1 || echo 'NOT INSTALLED')"
echo "fd:    $(fd --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "bat:   $(bat --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "eza:   $(eza --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "rg:    $(rg --version 2>/dev/null | head -1 || echo 'NOT INSTALLED')"
echo "zoxide:$(zoxide --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "btop:  $(btop --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "tmux:  $(tmux -V 2>/dev/null || echo 'NOT INSTALLED')"
echo "jq:    $(jq --version 2>/dev/null || echo 'NOT INSTALLED')"
echo "fzf:   $(fzf --version 2>/dev/null || echo 'NOT INSTALLED')"
