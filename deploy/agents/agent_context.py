"""Prepare this host's Claude/Codex for Deck sessions. Idempotent; install.sh runs it and prints what it changed.

Every host ends up the same, whatever its operating system:

- Claude: no spinner tips (Deck crops the agent's footer into a status card, and tips change its height).
- With machine notes that carry agent instructions (DECK_NOTES/AGENTS.md): one pointer file, `~/AGENTS.md`, read by
  Claude (through the agents-md plugin) and by Codex (through a symlink, or a copy where links need a privilege).
- A Claude SessionStart hook that syncs the notes with the other Deck hosts, using whichever `deck-notes` this host
  has: the command on PATH, else the PC app's shim in `~/.local/bin`.
"""
import json
import os
import shutil
from pathlib import Path

HOME = Path.home()
NOTES = Path(os.path.expanduser(os.environ.get("DECK_NOTES", "~/deck-notes")))
# A pointer is already present if any of these appears: Deck's own heading, an older install's heading, or a
# hand-written pointer naming the notes folder (several hosts have one, so never append a second).
HEADERS = ("Machine notes (Deck)", "Machine administration (infra repo)", "Machine administration")
POINTER = HOME / "AGENTS.md"
BLOCK = f"""
# {HEADERS[0]}
- Machine facts live in `{str(NOTES).replace(str(HOME), "~", 1)}`. Follow its AGENTS.md.
- For machines, servers, networking and installed tools, these notes override local notes and memory. Record every admin change there.
"""
done = []
notes = (NOTES / "AGENTS.md").exists()


def deck_notes_command():
    """How this host runs deck-notes, or None. The PC app installs a shim instead of a PATH command."""
    if shutil.which("deck-notes"):
        return "deck-notes sync -q"
    shim = HOME / ".local/bin/deck-notes"
    if os.name == "nt" and (shim.with_suffix(".cmd").exists() or shim.exists()):
        return f"{shim.as_posix()} sync -q"
    if shim.exists():
        return f"{shim} sync -q"
    return None


if notes:
    # One pointer file. Claude reads ~/AGENTS.md via the agents-md plugin; Codex reads ~/.codex/AGENTS.md, which
    # links to it. An older install may have left a pointer in ~/.claude/CLAUDE.md: fold it into ~/AGENTS.md.
    text = POINTER.read_text() if POINTER.exists() else ""
    has_pointer = any(h in text for h in HEADERS) or str(NOTES) in text or NOTES.name in text
    if not has_pointer:
        with POINTER.open("a") as f:
            f.write(BLOCK)
        done.append(f"notes pointer added to {POINTER}")

    legacy = HOME / ".claude/CLAUDE.md"
    if legacy.exists() and any(h in legacy.read_text() for h in HEADERS):
        kept = "\n".join(
            ln for ln in legacy.read_text().splitlines()
            if not any(h in ln for h in HEADERS) and not ln.startswith("- Machine facts live in")
            and not ln.startswith("- For machines, servers")
        ).strip()
        if kept:
            legacy.write_text(kept + "\n")
            done.append(f"notes pointer removed from {legacy} (now in {POINTER.name})")
        else:
            legacy.unlink()
            done.append(f"{legacy} removed (its pointer is now in {POINTER.name})")

    codex = HOME / ".codex/AGENTS.md"
    codex.parent.mkdir(parents=True, exist_ok=True)
    if not (codex.is_symlink() and Path(os.path.realpath(codex)) == POINTER.resolve()):
        if codex.exists() or codex.is_symlink():
            codex.unlink()
        try:
            codex.symlink_to(POINTER)
            done.append(f"{codex} -> {POINTER}")
        except OSError:  # Windows without Developer Mode: a copy, refreshed on every install
            shutil.copyfile(POINTER, codex)
            done.append(f"{codex} copied from {POINTER} (no symlink privilege)")

p = HOME / ".claude/settings.json"
s = json.loads(p.read_text()) if p.exists() else {}
sync = deck_notes_command() if notes else None
if sync:
    starts = s.setdefault("hooks", {}).setdefault("SessionStart", [])
    existing = [h for e in starts for h in e.get("hooks", [])]
    if not any("deck-notes" in str(h.get("command", "")) for h in existing):
        starts.append({"hooks": [{"type": "command", "command": sync, "timeout": 30}]})
        done.append(f"Claude SessionStart hook: {sync}")
# Claude reads ~/AGENTS.md only with the agents-md plugin set to read both file names.
cfg = s.setdefault("pluginConfigs", {}).setdefault("cc-plugin-agents-md@builtin", {}).setdefault("options", {})
if cfg.get("instructionFiles") != "claude-md-and-agents-md":
    cfg["instructionFiles"] = "claude-md-and-agents-md"
    done.append("Claude reads AGENTS.md as well as CLAUDE.md")
if s.get("spinnerTipsEnabled") is not False:
    s["spinnerTipsEnabled"] = False
    done.append("Claude spinner tips off")
p.parent.mkdir(parents=True, exist_ok=True)
tmp = p.with_suffix(".json.tmp")
tmp.write_text(json.dumps(s, indent=2))
os.replace(tmp, p)
print("agent context: " + ("; ".join(done) if done else "already set"))
