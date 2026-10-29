"""What every adapter needs: the platform, finding an agent's program, reading its JSONL, the pane's own transcript."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
REPO = Path(__file__).resolve().parents[2]  # the checkout: deck/bridge/util.py -> deck -> repo
SCRIPT = REPO / "deck" / "chat_bridge.py"  # what panes and Claude's hooks run, by path
OUT_LIMIT = 4000  # characters of tool output kept per call


def resolve(cmd: str) -> list[str]:
    """argv for a CLI. Windows: npm-style .cmd shims go through cmd.exe, which mangles arguments holding quotes or
    newlines (the Deck context), so run what the shim runs: its .js under node, or its .exe."""
    path = shutil.which(cmd)
    if not path and cmd == "agy":
        from deck.agents import agy_path  # the checkout is on sys.path (chat_bridge.py puts it there)
        path = agy_path()
    if not path:
        raise SystemExit(f"{cmd} not found")
    if WINDOWS and path.lower().endswith(".cmd"):
        m = re.search(r'%~?dp0%?\\?([^"%\r\n]+?\.(js|exe))\b', Path(path).read_text(errors="replace"))
        if m:
            target = str(Path(path).parent / m.group(1))
            return [shutil.which("node") or "node", target] if m.group(2) == "js" else [target]
    return [path]


def show(text: str) -> None:
    """The pane's own view: a plain transcript, for anyone who opens it as a terminal. Windows: ASCII only (a native
    program's output reaches an MSYS pane through a pipe that mangles anything else)."""
    sys.stdout.write(text)
    sys.stdout.flush()


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def jsonl(stream):
    """Records from a JSONL pipe, split on LF only (JSON strings may hold U+2028)."""
    buf = b""
    while chunk := stream.read1(65536):
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            try:
                yield json.loads(line)
            except ValueError:
                pass


def kill_tree(p) -> None:
    if p is None or p.poll() is not None:
        return
    if WINDOWS:  # the agents' launchers start them as children; take the whole tree
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        p.terminate()


def windows_env() -> None:
    """A native program in an MSYS tmux pane gets HOME but not USERPROFILE or APPDATA; Python (Path.home) and the
    agents look for their settings and logins through those."""
    home = os.environ.get("HOME", "")
    if not WINDOWS or not re.match(r"^[A-Za-z]:", home):
        return
    os.environ.setdefault("USERPROFILE", home)
    os.environ.setdefault("HOMEDRIVE", home[:2])
    os.environ.setdefault("HOMEPATH", home[2:])
    os.environ.setdefault("APPDATA", os.path.join(home, "AppData", "Roaming"))
    os.environ.setdefault("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))
