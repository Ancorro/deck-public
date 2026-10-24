"""Prepare this host's Claude/Codex for Deck sessions. Idempotent; install.sh runs it and prints what it changed.

- Claude: no spinner tips (Deck crops the agent's footer into a status card, and tips change its height).
- With machine notes that carry agent instructions (DECK_NOTES/AGENTS.md): point Claude and Codex at them, and sync
  the notes with the other Deck hosts when a Claude session starts (`deck-notes sync`).
"""
import json
import os
from pathlib import Path

HOME = Path.home()
NOTES = Path(os.path.expanduser(os.environ.get("DECK_NOTES", "~/deck-notes")))
HEADERS = ("Machine notes (Deck)", "Machine administration (infra repo)")  # the second: older installs
BLOCK = f"""
# {HEADERS[0]}
- Machine facts live in `{str(NOTES).replace(str(HOME), "~", 1)}`. Follow its AGENTS.md.
- For machines, servers, networking and installed tools, these notes override local notes and memory. Record every admin change there.
"""
done = []
notes = (NOTES / "AGENTS.md").exists()

if notes:
    for path in (HOME / ".claude/CLAUDE.md", HOME / ".codex/AGENTS.md"):
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text() if path.exists() else ""
        if not any(h in text for h in HEADERS):
            with path.open("a") as f:
                f.write(BLOCK)
            done.append(f"notes pointer added to {path}")

p = HOME / ".claude/settings.json"
s = json.loads(p.read_text()) if p.exists() else {}
if notes and os.name != "nt":  # Windows (PC app) has no deck-notes command
    hook = {"type": "command", "command": "deck-notes sync -q", "timeout": 30}
    starts = s.setdefault("hooks", {}).setdefault("SessionStart", [])
    if not any(h.get("command") == hook["command"] for e in starts for h in e.get("hooks", [])):
        starts.append({"hooks": [hook]})
        done.append("Claude SessionStart hook: deck-notes sync")
if s.get("spinnerTipsEnabled") is not False:
    s["spinnerTipsEnabled"] = False
    done.append("Claude spinner tips off")
p.parent.mkdir(parents=True, exist_ok=True)
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps(s, indent=2))
os.replace(tmp, p)
print("agent context: " + ("; ".join(done) if done else "already set"))
