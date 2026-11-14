#!/usr/bin/env bash
# Agent browser: Google Chrome + Playwright MCP for Claude and Codex.
# One Playwright MCP service per host (127.0.0.1:8931, user unit agent-browser). Every Claude/Codex session
# connects to it and gets its own isolated, headless browser context, so agents can browse in parallel.
# Logins: you sign in once in a real profile (~/.agent-chrome) with `agent-chrome` (WSLg window on Windows);
# when that window closes its cookies are copied to ~/.config/agent-browser/state.json, which every new
# agent context loads. Re-run `agent-chrome` when a site logs the agents out.
# Re-runnable. Asks for sudo once (Chrome install).
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
step() { printf '\n== %s\n' "$*"; }

step "Google Chrome (Google's apt repo)"
if ! command -v google-chrome >/dev/null; then
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor --yes -o /usr/share/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
    | sudo tee /etc/apt/sources.list.d/google-chrome.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq google-chrome-stable fonts-noto-color-emoji
fi
google-chrome --version

step "Playwright MCP"
npm config set prefix "$HOME/.local"
command -v playwright-mcp >/dev/null || npm install -g -s @playwright/mcp
STATE="$HOME/.config/agent-browser/state.json"
mkdir -p "$HOME/.agent-chrome" "$HOME/.config/agent-browser" "$HOME/.local/bin" "$HOME/.local/share/applications"
chmod 700 "$HOME/.config/agent-browser"
cat > "$HOME/.config/playwright-mcp.json" <<EOF
{
  "browser": {
    "browserName": "chromium",
    "isolated": true,
    "launchOptions": { "channel": "chrome", "headless": true },
    "contextOptions": { "storageState": "$STATE" }
  }
}
EOF
# Copies the sign-in profile's cookies + local storage into $STATE (session cookies included).
# password-store=basic: the profile's cookies are not keyring-encrypted, so headless runs can read them.
cat > "$HOME/.local/bin/agent-browser-export" <<'EOF'
#!/usr/bin/env node
const home = process.env.HOME;
const { chromium } = require(`${home}/.local/lib/node_modules/@playwright/mcp/node_modules/playwright-core`);
const fs = require("fs");
(async () => {
  const state = `${home}/.config/agent-browser/state.json`;
  const ctx = await chromium.launchPersistentContext(`${home}/.agent-chrome`,
    { channel: "chrome", headless: true, args: ["--password-store=basic"] });
  await ctx.storageState({ path: state + ".tmp" });
  await ctx.close();
  fs.chmodSync(state + ".tmp", 0o600);
  fs.renameSync(state + ".tmp", state);
  const n = JSON.parse(fs.readFileSync(state)).cookies.length;
  console.log(`agent browser logins updated: ${n} cookies -> ${state}`);
})().catch((e) => { console.error("export failed (is agent-chrome still open?):", e.message); process.exit(1); });
EOF
chmod +x "$HOME/.local/bin/agent-browser-export"
cat > "$HOME/.local/bin/agent-chrome" <<'EOF'
#!/bin/sh
# Visible Chrome on the sign-in profile. Sign in to the sites agents need, close the window, and the
# logins are copied for agents (new agent sessions pick them up; running ones keep their old copy).
: "${DISPLAY:=:0}"; export DISPLAY
google-chrome --user-data-dir="$HOME/.agent-chrome" --password-store=basic "$@"
sleep 1
"$HOME/.local/bin/agent-browser-export"
EOF
chmod +x "$HOME/.local/bin/agent-chrome"
if [ ! -f "$STATE" ]; then
  systemctl --user stop agent-browser 2>/dev/null || true  # an older setup may hold the profile
  "$HOME/.local/bin/agent-browser-export" || { echo '{"cookies":[],"origins":[]}' > "$STATE"; chmod 600 "$STATE"; }
fi
cat > "$HOME/.local/share/applications/agent-chrome.desktop" <<EOF
[Desktop Entry]
Name=Agent Chrome
Comment=Chrome profile used by Claude/Codex browser tools
Exec=$HOME/.local/bin/agent-chrome
Icon=google-chrome
Type=Application
EOF

step "Shared browser service"
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/agent-browser.service" <<EOF
[Unit]
Description=Playwright MCP for Claude/Codex (127.0.0.1:8931, isolated context per session)

[Service]
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$HOME/.local/bin/playwright-mcp --config $HOME/.config/playwright-mcp.json --host 127.0.0.1 --port 8931 --allowed-hosts 127.0.0.1:8931
Restart=on-failure

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable -q agent-browser
systemctl --user restart agent-browser
sleep 2

step "Register with Claude and Codex"
URL=http://127.0.0.1:8931/mcp
claude mcp remove playwright --scope user >/dev/null 2>&1 || true
claude mcp add --transport http --scope user playwright "$URL" >/dev/null
python3 - "$HOME" <<'PY'
import os, re, sys
home = sys.argv[1]
p = os.path.join(home, ".codex", "config.toml")
os.makedirs(os.path.dirname(p), exist_ok=True)
s = open(p).read() if os.path.exists(p) else ""
s = re.sub(r'\n?\[mcp_servers\.playwright\]\n(?:[^\[\n].*\n?)*', '\n', s)
s = s.rstrip() + '\n\n[mcp_servers.playwright]\nurl = "http://127.0.0.1:8931/mcp"\n'
open(p, "w").write(s.lstrip())
PY
claude mcp list 2>/dev/null | grep playwright || true
codex mcp list 2>/dev/null | grep playwright || true

step "Left for you"
echo "- run: agent-chrome   (sign in to the sites agents need, then close the window; logins are copied for agents)"
