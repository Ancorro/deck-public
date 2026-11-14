"""Chat sessions without tmux: each agent chat's bridge (deck/chat_bridge.py) runs as a process of its own, detached
from the server so it outlives a server restart or update, and is listed from a registry folder.

DATA/bridges/<session>.json  what started it (agent, folder, label, argv) and what the user set (title, project)
DATA/bridges/<session>.lock  held by the running bridge (an OS lock, gone when its process ends, whatever the cause)
DATA/bridges/<session>.exit  its exit code, written as it ends
DATA/bridges/<session>.log   its own stdout and stderr (a crash on start shows here)

Not under DATA/chat: the site copies that folder to the other nodes, which would then list these chats as their own.
The bridge's event log, command queue and state stay in DATA/chat as before (sessions.chat_*)."""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import config

if config.WINDOWS:
    import msvcrt
else:
    import fcntl

# A bridge started here: its lock, which it holds while it runs (deck/bridge/main.py hold_lock)
LOCK_ENV = "DECK_BRIDGE_LOCK"

# What spawn runs: start the bridge (argv in argv[1] as JSON, its output to the log) and print its pid. Windows: a
# hidden console of its own (its agents' console programs share it, and a Ctrl+C in it can't reach the server), its
# own process group, and out of the server's job object if it has one (the PC app's uv run); breakaway is refused in
# a job that forbids it, then without. Elsewhere: a session of its own (systemd's KillMode=process leaves it be).
LAUNCHER = r'''
import json, subprocess, sys
a = json.loads(sys.argv[1])
log = open(a["log"], "ab")
kw = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True)
if sys.platform == "win32":
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        p = subprocess.Popen(a["argv"], creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:
        p = subprocess.Popen(a["argv"], creationflags=flags, **kw)
else:
    p = subprocess.Popen(a["argv"], start_new_session=True, **kw)
print(p.pid)
'''


def folder() -> Path:
    return config.DATA / "bridges"


def _path(name: str, ext: str) -> Path:
    return folder() / f"{name}{ext}"


def exists(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and _path(name, ".json").exists()


def names() -> list[str]:
    d = folder()
    return sorted(p.name[:-len(".json")] for p in d.glob("*.json")) if d.exists() else []


def meta(name: str) -> dict:
    try:
        return json.loads(_path(name, ".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def update(name: str, **fields) -> None:
    """Set fields in its registry entry (None removes one)."""
    m = meta(name)
    if not m:
        return
    for k, v in fields.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    tmp = _path(name, ".json.tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_path(name, ".json"))


def alive(name: str) -> bool:
    """Its bridge is running: something holds its lock. Survives pid reuse (a pid alone would not, after a reboot)."""
    try:
        f = open(_path(name, ".lock"), "a+b")
    except OSError:
        return False
    try:
        if config.WINDOWS:
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return True
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            return False
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        f.close()


def exit_code(name: str) -> int | None:
    try:
        return int(_path(name, ".exit").read_text().strip())
    except (OSError, ValueError):
        return None


def split_env(argv: list[str]) -> tuple[list[str], dict]:
    """argv without a leading `env K=V ...` (agents.env_prefix), and those variables. Run natively, not through MSYS's
    env, which would rewrite /-looking arguments on Windows."""
    env = {}
    if argv[:1] == ["env"]:
        i = 1
        while i < len(argv) and "=" in argv[i] and not argv[i].startswith("-"):
            k, _, v = argv[i].partition("=")
            env[k] = v
            i += 1
        argv = argv[i:]
    return argv, env


def spawn(name: str, argv: list[str], cwd: str) -> int:
    """Start the bridge detached from this server; its pid. Its lock, exit code and log go in the registry folder."""
    argv, extra = split_env(argv)
    folder().mkdir(parents=True, exist_ok=True)
    for ext in (".exit",):
        _path(name, ext).unlink(missing_ok=True)
    env = {**os.environ, **extra, "DECK_SESSION": "1", "DECK_SESSION_NAME": name, LOCK_ENV: str(_path(name, ".lock")),
           "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    # TMUX*: from a server started inside tmux, it would say this bridge runs in a pane. MSYS=noglob is the server's
    # own (config.py, for calling tmux); in the agents' Git Bash it breaks the quoting of Claude's hook commands, so
    # no hook ran (the tmux server unsets it for panes the same way: sessions.start_tmux)
    for k in ("TMUX", "TMUX_PANE", "MSYS"):
        env.pop(k, None)
    # Through a launcher that starts the bridge and exits at once, so the bridge has no living parent: a tree kill of
    # the server (taskkill /T, which follows parent ids) leaves it running, as it left tmux's server
    r = subprocess.run([python(), "-c", LAUNCHER, json.dumps({"argv": argv, "log": str(_path(name, ".log"))})],
                       cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
                       **config.OWN_CONSOLE)
    try:
        return int(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise OSError(f"couldn't start the chat: {(r.stderr or r.stdout).strip()[-500:]}") from None


def start(name: str, argv: list[str], cwd: str, **fields) -> None:
    """Register a chat and start its bridge. fields: what the session list shows (label, type, button, computer...)."""
    folder().mkdir(parents=True, exist_ok=True)
    m = {"name": name, "cwd": cwd, "argv": argv, "created": int(time.time()), **fields}
    _path(name, ".json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    try:
        update(name, pid=spawn(name, argv, cwd))
    except OSError:
        _path(name, ".json").unlink(missing_ok=True)
        raise


def stop(name: str, wait: float = 5.0) -> None:
    """End its bridge (and the agent under it): a quit command first, so the bridge closes its agent itself; then its
    process tree. Returns once its lock is free (a restart's new bridge rewrites the same event log)."""
    # Just started (a Kill right after Start or Restart): its bridge may not hold the lock yet. Wait for it to take
    # it or to end, so a running bridge is never left with no tab
    end = time.time() + 15
    while not alive(name) and starting(name) and time.time() < end:
        time.sleep(0.1)
    if not alive(name):
        return
    from .sessions import chat_command  # the bridge reads its .in.jsonl
    chat_command(name, {"t": "quit"})
    end = time.time() + wait
    while time.time() < end and alive(name):
        time.sleep(0.1)
    pid = int(meta(name).get("pid") or 0)
    if alive(name) and pid:  # the lock is held, so this pid is still the bridge's
        if config.WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True, **config.OWN_CONSOLE)
        else:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
        end = time.time() + 3
        while time.time() < end and alive(name):
            time.sleep(0.1)


def starting(name: str) -> bool:
    """Its bridge was started in the last 15 s and has neither taken its lock nor ended (no exit code)."""
    m = meta(name)
    started = max(int(m.get("created") or 0), int(m.get("spawned") or 0))
    return bool(m.get("pid")) and exit_code(name) is None and time.time() - started < 15


def remove(name: str) -> None:
    """Stop it and forget it (its chat files are sessions.kill_session's)."""
    stop(name)
    # The entry first: once it is gone the chat is gone from the list. A lock or log a killed process still holds may
    # outlive this (Windows frees it a moment later); a stale one is harmless (alive tests the lock, not the file)
    for ext in (".json", ".lock", ".exit", ".log", ".json.tmp"):
        for _ in range(50):  # Windows: a file the dying bridge still holds (5 s)
            try:
                _path(name, ext).unlink(missing_ok=True)
                break
            except PermissionError:
                time.sleep(0.1)


def log_tail(name: str, limit: int = 4000) -> str:
    try:
        with open(_path(name, ".log"), "rb") as f:
            f.seek(max(0, f.seek(0, 2) - limit))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def python() -> str:
    """The Python a bridge runs on: the server's own (it has pywinpty on Windows)."""
    return sys.executable or shutil.which("python3") or "python3"
