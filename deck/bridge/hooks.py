"""What Claude Code's hooks and status line hand the bridge (chat_bridge.py hook / statusline <chat dir> <session>):
appended to the session's files for the Claude adapter to read."""
import json
import sys
from pathlib import Path


def hook_main(folder: str, session: str) -> int:
    """`chat_bridge.py hook <chat dir> <session>`: Claude's hooks (Claude.HOOKS) pass their input on stdin; it goes to
    <session>.hooks.jsonl for the bridge. Prints nothing: a SessionStart hook's output joins Claude's context."""
    try:
        line = json.dumps(json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace")), ensure_ascii=False)
    except ValueError:
        return 0
    with open(Path(folder) / f"{session}.hooks.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return 0


def statusline_main(folder: str, session: str) -> int:
    """`chat_bridge.py statusline <chat dir> <session>`: Claude's status line (given with --settings). Its context
    and plan limits (no cost: user) go to <session>.hooks.jsonl as a "DeckStatus" record when they change: the chat's bar shows
    them, as the terminal's status line does. Prints the same, for anyone looking at the terminal."""
    try:
        d = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except ValueError:
        return 0
    sys.stdout.reconfigure(encoding="utf-8")  # Windows pipes default to cp1252
    k = lambda n: f"{n / 1e6:g}M" if n >= 1e6 else f"{round(n / 1e3)}k"
    cw, parts = d.get("context_window") or {}, []
    if cw.get("used_percentage") is not None:
        size, used = cw.get("context_window_size"), cw.get("total_input_tokens")
        parts.append(f"ctx {cw['used_percentage']}%" + (f" {k(used)}/{k(size)}" if used is not None and size else ""))
    for key, name in (("five_hour", "5h"), ("seven_day", "wk")):
        pct = ((d.get("rate_limits") or {}).get(key) or {}).get("used_percentage")
        if pct is not None:
            parts.append(f"{name} {round(pct)}%")
    print(" · ".join(parts))
    last = Path(folder) / f"{session}.statusline"
    line = json.dumps({"hook_event_name": "DeckStatus", "parts": parts})
    try:
        if last.read_text(encoding="utf-8") == line:
            return 0
    except OSError:
        pass
    last.write_text(line, encoding="utf-8")
    with open(Path(folder) / f"{session}.hooks.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return 0
