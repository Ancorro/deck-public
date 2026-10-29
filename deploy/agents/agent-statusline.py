#!/usr/bin/env python3
"""Install Claude Code + Codex status lines for this user. Idempotent; Linux, WSL, Windows. Run by install.sh.

Claude: model + effort · ctx % used/size · 5h % · wk %  (statusLine -> ~/.claude/bin/statusline.py;
        also writes ~/.cache/deck-status/<tmux pane>.json for Deck's status chips. An existing status line of
        your own is kept; Deck then shows Claude's chips only from its footer text.)
Codex:  [tui] status_line = model-with-reasoning, context-used, five-hour-limit, weekly-limit
"""
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
SCRIPT = r'''#!/usr/bin/env python3
"""Claude Code status line: model effort · context used · plan usage. Installed by Deck's deploy/agents/agent-statusline.py."""
import json
import os
import sys


def k(n):
    return f"{n / 1e6:g}M" if n >= 1e6 else f"{round(n / 1e3)}k"


sys.stdout.reconfigure(encoding="utf-8")  # Windows pipes default to cp1252
d = json.loads(sys.stdin.read() or "{}")
model = (d.get("model") or {}).get("display_name") or "?"
effort = (d.get("effort") or {}).get("level")
if effort:
    model += f" {effort}"
cw = d.get("context_window") or {}
pct, used, size = cw.get("used_percentage"), cw.get("total_input_tokens"), cw.get("context_window_size")
ctx = f"ctx {pct}%" if pct is not None else "ctx ?"
if used is not None and size:
    ctx += f" {k(used)}/{k(size)}"
parts = [model, ctx]
# Plan usage limits (Pro/Max), present after the session's first API reply.
rl = d.get("rate_limits") or {}
for key, name in (("five_hour", "5h"), ("seven_day", "wk")):
    p = (rl.get(key) or {}).get("used_percentage")
    if p is not None:
        parts.append(f"{name} {round(p)}%")
print(" · ".join(parts))
# Deck shows these in its own status card (the terminal cuts the line at its width): one file per tmux pane.
def tmux_pane():
    # Claude runs this without the TMUX_* variables; the Claude process above us still has them (Linux /proc).
    pid = os.getpid()
    for _ in range(6):
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                env = dict(x.split(b"=", 1) for x in f.read().split(b"\0") if b"=" in x)
            if b"TMUX_PANE" in env:
                return env[b"TMUX_PANE"].decode().lstrip("%"), (os.getppid() if pid == os.getpid() else pid)
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return "", 0
    return "", 0


# pid: the process that holds TMUX_PANE; Deck checks it really runs in that pane (the variable can be inherited stale).
pane, pid = tmux_pane()
if pane.isdigit():
    try:
        out = os.path.join(os.path.expanduser("~"), ".cache", "deck-status")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, pane + ".json"), "w", encoding="utf-8") as f:
            json.dump({"parts": parts, "pid": pid}, f)
    except OSError:
        pass
'''
CODEX_LINE = ('status_line = ["model-with-reasoning", "context-used", '
              '"five-hour-limit", "weekly-limit"]')


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# Claude
script = os.path.join(HOME, ".claude", "bin", "statusline.py")
write(script, SCRIPT)
if os.name == "nt":
    command = f'"{sys.executable}" "{script}"'
else:
    os.chmod(script, 0o755)
    command = "~/.claude/bin/statusline.py"
settings = os.path.join(HOME, ".claude", "settings.json")
s = json.load(open(settings, encoding="utf-8")) if os.path.exists(settings) else {}
mine = (s.get("statusLine") or {}).get("command", "")
if mine and "statusline.py" not in mine:
    print(f"claude: keeping your own status line ({mine})")
else:
    s["statusLine"] = {"type": "command", "command": command, "padding": 0}
    write(settings, json.dumps(s, indent=2) + "\n")

# Codex
config = os.path.join(HOME, ".codex", "config.toml")
toml = open(config, encoding="utf-8").read() if os.path.exists(config) else ""
m = re.search(r"(?m)^\[tui\]\s*$", toml)
if not m:
    toml = f"[tui]\n{CODEX_LINE}\n\n" + toml
else:
    end = re.search(r"(?m)^\[", toml[m.end():])
    body = toml[m.end(): m.end() + end.start()] if end else toml[m.end():]
    if re.search(r"(?m)^status_line\s*=", body):
        body = re.sub(r"(?m)^status_line\s*=.*$", CODEX_LINE, body)
    else:
        body = "\n" + CODEX_LINE + body
    toml = toml[: m.end()] + body + (toml[m.end() + end.start():] if end else "")
write(config, toml)
print(f"status lines in place (claude: {command}; codex: {config})")
