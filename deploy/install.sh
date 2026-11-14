#!/usr/bin/env bash
# Install or update Deck on a Linux host (Ubuntu, incl. WSL). Re-runnable.
#   bash deploy/install.sh [https://<host>.<tailnet>.ts.net]
# The URL is only for Tailscale access (optional): the phone app reaches Deck over iroh without it.
# Asks for sudo once (apt, linger). Afterwards: log in to claude and codex, then pair the phone with `deck-pair`.
# Optional environment:
#   DECK_NOTES_REPO  git URL of a machine notes repo to clone to ~/deck-notes (shared with your other Deck hosts)
#   DECK_BUILD_TUNNEL=1  build deck-iroh with Rust instead of downloading the latest release's binary
set -euo pipefail

URL="${1:-}"
HOST_NAME="${URL#https://}"; HOST_NAME="${HOST_NAME%%.*}"; HOST_NAME="${HOST_NAME:-$(hostname)}"
DECK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # the checkout this script runs from (any folder)
ENVF="$HOME/.config/deck/env"
export PATH="$HOME/.local/bin:$PATH"
is_wsl() { grep -qi microsoft /proc/version; }
step() { printf '\n== %s\n' "$*"; }

python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null \
  || { echo "Deck needs Python 3.11 or newer (Ubuntu 24.04 or later)." >&2; exit 1; }

step "System packages (sudo)"
sudo apt-get update -qq
sudo apt-get install -y -qq git tmux python3-aiohttp python3-pytest python3-pytest-aiohttp python3-pytest-asyncio python3-qrcode nodejs npm build-essential
sudo loginctl enable-linger "$USER"   # user services run without a login session

step "Deck code"
# WSL: reuse Windows' Git credential manager for private repos, unless a helper is already set.
if is_wsl && [ -z "$(git config --global credential.helper || true)" ] \
   && [ -x "/mnt/c/Program Files/Git/mingw64/bin/git-credential-manager.exe" ]; then
  git config --global credential.helper "/mnt/c/Program\ Files/Git/mingw64/bin/git-credential-manager.exe"
fi
if [ -e "$DECK/.git" ]; then   # a git checkout updates itself; a copied folder stays as it is
  git -C "$DECK" pull -q --ff-only
  git -C "$DECK" config core.autocrlf false
fi
# Deck commits to the notes and key-builder repos, so git needs an identity: yours if set, else a local one.
[ -n "$(git config --global user.name || true)" ] || git config --global user.name "$USER"
[ -n "$(git config --global user.email || true)" ] || git config --global user.email "$USER@$(hostname)"

step "Agents (Claude Code, Codex, Pi)"
command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
npm config set prefix "$HOME/.local"   # global npm installs go to ~/.local (no sudo)
command -v codex >/dev/null || npm install -g -s @openai/codex
[ -x "$HOME/.pi/agent/bin/pi" ] || curl -fsSL https://pi.dev/install.sh | sh </dev/null
[ -x "$HOME/.pi/agent/bin/pi" ] && ln -sf "$HOME/.pi/agent/bin/pi" "$HOME/.local/bin/pi"   # its launcher follows links
python3 "$DECK/deploy/agents/pi-durable/setup.py" || true   # durable Pi (experimental); says why when it skips

step "Workspace + key builder"
mkdir -p "$HOME/work/deck-keys/scripts" "$HOME/work/deck-keys/.claude" "$HOME/.config/deck" "$HOME/.local/bin"
cp "$DECK/deploy/agents/deck-keys/AGENTS.md" "$DECK/deploy/agents/deck-keys/CLAUDE.md" "$HOME/work/deck-keys/"
cp "$DECK/deploy/agents/deck-keys/settings.json" "$HOME/work/deck-keys/.claude/settings.json"
if [ ! -d "$HOME/work/deck-keys/.git" ]; then
  (cd "$HOME/work/deck-keys" && git init -q -b main && printf '.claude/settings.local.json\n' > .gitignore && git add -A && git commit -qm "Deck key builder workspace")
fi
for tool in deck-key deck-notes deck-pair; do
  ln -sf "$DECK/deploy/$tool" "$HOME/.local/bin/$tool"   # links, so each finds the checkout (and updates with it)
done
python3 "$DECK/deploy/agents/trust.py" "$HOME/work" "$HOME/work/deck-keys"

step "Settings"
# ~/.config/deck/env: per-host settings. Re-runs only set what's given and keep everything else.
touch "$ENVF"
set_env() { { grep -v "^$1=" "$ENVF" || true; echo "$1=$2"; } > "$ENVF.new"; mv "$ENVF.new" "$ENVF"; }
grep -q '^DECK_HOST_NAME=' "$ENVF" || set_env DECK_HOST_NAME "$HOST_NAME"
[ -z "$URL" ] || set_env DECK_ORIGINS "$URL"
# Machine notes (optional): a git repo of notes about your machines, shared by your Deck hosts (deck/notes.py).
if [ -n "${DECK_NOTES_REPO:-}" ] && ! grep -q '^DECK_NOTES=' "$ENVF"; then
  [ -d "$HOME/deck-notes/.git" ] || git clone -q "$DECK_NOTES_REPO" "$HOME/deck-notes"
  set_env DECK_NOTES "$HOME/deck-notes"
fi
set -a; . "$ENVF"; set +a
deck-notes init

step "Agent context + status lines"
python3 "$DECK/deploy/agents/agent_context.py"
python3 "$DECK/deploy/agents/agent-statusline.py"   # Claude/Codex status lines; Claude's also feeds Deck's status chips

step "Service"
mkdir -p "$HOME/.config/systemd/user"
sed "s|^WorkingDirectory=.*|WorkingDirectory=$DECK|" "$DECK/deploy/deck.service" > "$HOME/.config/systemd/user/deck.service"   # run from this checkout
systemctl --user daemon-reload
systemctl --user enable -q deck
systemctl --user restart deck
sleep 1 && curl -fsS -o /dev/null http://127.0.0.1:8787/ && echo "Deck running on 127.0.0.1:8787"

step "iroh tunnel (phone app on any network)"
# The latest release's binary, checked against its SHA256SUMS; else (no release, other arch, DECK_BUILD_TUNNEL) build it.
REL="https://github.com/Ancorro/deck-public/releases/latest/download"; BIN="deck-iroh-linux-$(uname -m)"; TMP="$(mktemp -d)"
if [ -z "${DECK_BUILD_TUNNEL:-}" ] && curl -fsSL -o "$TMP/$BIN" "$REL/$BIN" && curl -fsSL -o "$TMP/SHA256SUMS" "$REL/SHA256SUMS" \
   && (cd "$TMP" && grep " $BIN\$" SHA256SUMS | sha256sum -c --quiet); then
  install -m 755 "$TMP/$BIN" "$HOME/.local/bin/deck-iroh"
else
  echo "Building deck-iroh (no prebuilt binary for $(uname -m), or DECK_BUILD_TUNNEL set)"
  [ -x "$HOME/.cargo/bin/cargo" ] || curl -sSf https://sh.rustup.rs | sh -s -- -y -q --profile minimal
  (cd "$DECK/tunnel" && "$HOME/.cargo/bin/cargo" build -q --release --bin deck-iroh)
  install -m 755 "$DECK/tunnel/target/release/deck-iroh" "$HOME/.local/bin/deck-iroh"
fi
rm -rf "$TMP"
cp "$DECK/deploy/deck-iroh.service" "$HOME/.config/systemd/user/deck-iroh.service"
systemctl --user daemon-reload
systemctl --user enable -q deck-iroh
systemctl --user restart deck-iroh

step "Left for you"
command -v claude >/dev/null && { claude auth status 2>/dev/null | grep -q '"loggedIn": true' || echo "- run: claude   (log in)"; }
[ -f "$HOME/.codex/auth.json" ] || echo "- run: codex login"
[ -s "$HOME/.pi/agent/auth.json" ] || grep -qs defaultProvider "$HOME/.pi/agent/settings.json" || echo "- run: pi, then /login (or /model)"
echo "- pair your phone: run deck-pair and scan the code (only paired devices get in)"
is_wsl && echo "- keep WSL running after logon (see $DECK/docs/install.md, 'WSL')"
if [ -n "$URL" ]; then
  if is_wsl; then echo "- Tailscale access: in Windows PowerShell, tailscale serve --bg 8787"; else echo "- Tailscale access: sudo tailscale serve --bg 8787"; fi
  echo "- several Deck hosts: give each a deck_url (\"$URL\" for this one) in the notes' inventory.toml; the host switcher reads it"
  echo "- host key: copy ~/.config/deck/host-key (mode 600) from one of your hosts, or run deck-pair here first"
fi
