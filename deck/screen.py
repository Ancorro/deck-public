"""The Screen tab: the VNC server and its bridge, and the phone screen (a virtual monitor on a Windows or WSL host)."""
import json
import os
import re
import socket
import subprocess
from pathlib import Path

from . import config

# ---- screen -------------------------------------------------------------------

def screen() -> dict:
    """Screen tab config. The VNC server is loopback-only; the bridge is served on this origin at `path`."""
    try:
        cfg = json.loads(Path(config.VNC_FILE).read_text())
    except (FileNotFoundError, ValueError):
        return {"enabled": False}
    # bridge: websockify's local address ("127.0.0.1:6080"); Deck then serves it at /vnc/ itself (server.vnc_bridge),
    # so the tab works over iroh too. Without it the bridge lives in `tailscale serve`, which the tunnel can't reach.
    return {"enabled": True, "path": cfg.get("path", "/vnc/websockify"), "password": cfg.get("password", ""),
            "bridge": bool(cfg.get("bridge"))}


def vnc_bridge() -> str:
    """websockify's local address from vnc.json, or "" (none: the Screen tab isn't served over iroh)."""
    try:
        return str(json.loads(Path(config.VNC_FILE).read_text()).get("bridge", ""))
    except (FileNotFoundError, ValueError):
        return ""


SCHTASKS = "/mnt/c/Windows/System32/schtasks.exe"
VNC_HOOK_SECONDS = 120  # how long a vnc.json vnc_on/vnc_off command may take (it may start a whole desktop)


def vnc_service(state: str) -> bool:
    """Start ("on") or stop ("off") the VNC server, so it runs only while a page streams (user: "turn off the vnc when i
    am not streaming screen"): vnc.json's `vnc_on`/`vnc_off` command when it has one (any host; an argument list, run
    without a shell), else the admin tasks "Deck VNC on/off" (win/vnc-setup.ps1). False: neither is set up."""
    try:
        hook = json.loads(Path(config.VNC_FILE).read_text()).get(f"vnc_{state}")
    except (FileNotFoundError, ValueError):
        hook = None
    if isinstance(hook, list) and hook and all(isinstance(a, str) for a in hook):
        try:
            subprocess.run([os.path.expanduser(a) for a in hook], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=VNC_HOOK_SECONDS, **config.OWN_CONSOLE)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return True
    exe = os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "System32", "schtasks.exe") if config.WINDOWS else SCHTASKS
    if not os.path.exists(exe):
        return False
    r = subprocess.run([exe, "/run", "/tn", f"Deck VNC {state}"], capture_output=True, text=True, timeout=15,
                       **config.OWN_CONSOLE)
    return r.returncode == 0


def vnc_listening(addr: str = "127.0.0.1:5900") -> bool:
    host, _, port = addr.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=0.5).close()
        return True
    except OSError:
        return False


POWERSHELL ="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
PHONESCREEN = Path(__file__).parent / "win" / "phonescreen.ps1"


def phone_screen(action: str = "status", w: int = 0, h: int = 0) -> dict | list | None:
    """Screen tab's phone screen: a portrait virtual monitor (Virtual Display Driver) on a Windows or WSL host.
    None when this host has none. Returns {on, ox, oy, x, y, w, h, modes} in physical (VNC) pixels. "windows" lists
    the desktop's app windows [{hwnd, title, app, phone}]; "move" (w = hwnd, h = 1 to the phone, 0 back) moves one."""
    if config.WINDOWS:  # Deck on Windows itself: the same PowerShell 5.1, by its Windows path
        root = os.environ.get("SystemRoot", "C:/Windows")
        ps, win = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), str(PHONESCREEN)
    else:
        ps = POWERSHELL
        if not os.path.exists(ps):
            return None
        win = subprocess.run(["wslpath", "-w", str(PHONESCREEN)], capture_output=True, text=True).stdout.strip()
    args = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win, action]
    if action in ("on", "move"):
        args += [str(int(w)), str(int(h))]
    r = subprocess.run(args, capture_output=True, text=True, timeout=30, **config.OWN_CONSOLE)
    out = r.stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except (IndexError, ValueError):
        if action == "status":
            return None
        said = (r.stderr or r.stdout).strip()
        raise RuntimeError(said.splitlines()[0] if said else "phone screen failed") from None




# ---- the goal row: a computer-use run started from the Screen tab (user 2026-11-02: "make a button so i can
# stop/resume, give goals") ----
# The run is computer.json's "goal" command with the goal appended. Deck talks to it through two files: control (it
# writes "run", "pause" or "stop"; the loop reads it between steps) and status (the loop's state, step and note). A loop
# that doesn't read them still runs; Stop then ends its process group.

GOAL_DIR = config.DATA / "desk-goal"
_goal = {"proc": None}


def _goal_files() -> tuple[Path, Path, Path]:
    return GOAL_DIR / "control", GOAL_DIR / "status.json", GOAL_DIR / "run.log"


def _alive(pid) -> bool:
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        if config.WINDOWS:
            # os.kill(pid, 0) terminates a Windows process; query its handle instead.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if not handle:
                return False
            try:
                return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT: still running
            finally:
                kernel.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, ValueError, TypeError, OSError):
        return False


def _goal_running() -> bool:
    """A run is live: the one this Deck started, or, after a Deck restart (the run outlives it), the pid in status."""
    proc = _goal["proc"]
    if proc:
        return proc.poll() is None
    try:
        st = json.loads(_goal_files()[1].read_text())
    except (FileNotFoundError, ValueError, OSError):
        return False
    return st.get("state") in ("running", "paused") and _alive(st.get("pid"))


# Screen settings (the ⚙ in the Screen bar; user 2026-11-02: "no opus, let me set the paid model", "need a new
# setttings menu for the sreen view"): the loop's models and step budget, per host, passed to the run as environment.
GOAL_DEFAULTS = {"api": "claude-code", "model": "sonnet", "effort": "medium", "fast": True, "steps": 40, "icons": True}
GOAL_APIS = ("claude-code", "codex", "opencode")
GOAL_EFFORTS = ("", "low", "medium")  # scripted Claude calls go no higher than medium (typesafe-computer-use cli_writer)


def goal_settings(new: dict | None = None) -> dict:
    path = GOAL_DIR / "settings.json"
    try:
        cur = {**GOAL_DEFAULTS, **json.loads(path.read_text())}
    except (FileNotFoundError, ValueError):
        cur = dict(GOAL_DEFAULTS)
    if new is None:
        try:
            sid = (GOAL_DIR / "session").read_text().strip()
        except FileNotFoundError:
            sid = ""
        from .agents import computer_config
        return {**cur, "apis": list(GOAL_APIS), "efforts": list(GOAL_EFFORTS), "session": sid,
                "icons_available": bool(((computer_config() or {}).get("icons") or {}).get("url"))}
    if new.get("reset_session"):
        goal_session(reset=True)
    if "api" in new:
        if new["api"] not in GOAL_APIS:
            raise ValueError(f"api must be one of {', '.join(GOAL_APIS)}")
        cur["api"] = new["api"]
    if "model" in new:
        model = str(new["model"]).strip()
        if not re.fullmatch(r"[\w./:@+-]{0,80}", model):
            raise ValueError("model: letters, digits and . / : @ + - only")
        cur["model"] = model
    if "effort" in new:
        if new["effort"] not in GOAL_EFFORTS:
            raise ValueError("effort must be low, medium or empty")
        cur["effort"] = new["effort"]
    if "fast" in new:
        cur["fast"] = bool(new["fast"])
    if "steps" in new:
        cur["steps"] = max(1, min(200, int(new["steps"])))
    if "icons" in new:
        cur["icons"] = bool(new["icons"])
    GOAL_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: cur[k] for k in GOAL_DEFAULTS}))
    return goal_settings()


# The paid model is one Claude session per goal: kept across a goal's steps, new at each Run (user 2026-11-02: "sonnet is 1
# session, unless i reset. it gets the normal deck context and info about the computer use"; then "reset per goal").
LOOP_BRIEF = """# You are the computer-use loop's vision model

You drive the agent desktop on {host}{display} (the user's own browser profile and logins), one action per call.
Each call brings the goal, a screenshot, the numbered items on screen, recent actions and your notes. A small fast
model (SubQ) takes the confident clicks between your calls and reads your notes, so keep the plan there. The user
watches in Deck's Screen view and can pause the run, use the desktop, and resume.

This conversation lasts for one goal, across its steps; the next goal starts a new one.

- Never invent facts about the user: name, address, dates, answers about them. Use the context above (infra notes, the
  user's files below) or stop with "fail" and say exactly what is missing, so the user can add it to the goal.
- Upload the user's files by their paths below (the file dialog's path box takes a full path).
- Never create accounts, pay, or press a final Submit/Send for an application unless the goal says to; stop on the
  review page and say what you filled.
"""


def _display(cfg: dict) -> str:
    """Where the desktop is, for the brief: computer.json "display" (X11) or "monitor" (Windows), if set."""
    for key, label in (("display", "display"), ("monitor", "monitor")):
        if cfg.get(key):
            return f" ({label} {cfg[key]})"
    return ""


def _files_text(cfg: dict) -> str:
    """The text of computer.json "context_files" (files the run may need, such as a CV for a form: the paid model has
    no tools to read them), each under its path; PDFs through pdftotext."""
    out = []
    for name in cfg.get("context_files") or []:
        path = Path(os.path.expanduser(name))
        try:
            if path.suffix.lower() == ".pdf":
                text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True,
                                      timeout=30).stdout
            else:
                text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            continue
        if text.strip():
            out.append(f"## {name}\n\n{text.strip()[:20000]}")
    return ("\n\n# The user's files (as of this run's start)\n\n" + "\n\n".join(out)) if out else ""


def goal_context() -> Path:
    """The paid session's appended system prompt: Deck's context (the infra notes included), the loop's brief and the
    user's files named in computer.json "context_files"."""
    from .agents import computer_config, deck_context
    GOAL_DIR.mkdir(parents=True, exist_ok=True)
    path = GOAL_DIR / "context.md"
    cfg = computer_config() or {}
    brief = LOOP_BRIEF.format(host=config.HOST_NAME, display=_display(cfg))
    path.write_text(deck_context() + "\n\n" + brief + _files_text(cfg), encoding="utf-8")
    return path


def goal_session(reset: bool = False) -> str:
    """The paid session's id (a UUID); reset starts a new one at the next call."""
    import uuid
    path = GOAL_DIR / "session"
    if not reset:
        try:
            sid = path.read_text().strip()
            if sid:
                return sid
        except FileNotFoundError:
            pass
    GOAL_DIR.mkdir(parents=True, exist_ok=True)
    sid = str(uuid.uuid4())
    path.write_text(sid)
    return sid


def _goal_env() -> dict:
    st = goal_settings()
    env = {"CLICKER_WRITER_API": st["api"], "CLICKER_ANSWER_MODEL": st["model"],
           "CLICKER_ANSWER_REASONING": st["effort"], "LOOP_FAST": "1" if st["fast"] else ""}
    from .agents import computer_config
    icons = (computer_config() or {}).get("icons") or {}
    if st["icons"] and icons.get("url"):  # OmniParser on another host's GPU (computer.json "icons": {url, start})
        env |= {"LOOP_ICONS": icons["url"], "LOOP_ICONS_START": icons.get("start", "")}
    if st["api"] == "claude-code":
        env |= {"CLICKER_CLAUDE_SESSION": goal_session(), "CLICKER_CLAUDE_DIR": str(GOAL_DIR / "claude"),
                "CLICKER_CLAUDE_CONTEXT": str(goal_context())}
    return env


def goal_status() -> dict:
    """{available, running, state, goal, step, steps, note}: the latest run (state "idle" before any)."""
    from .agents import computer_config
    cfg = computer_config() or {}
    control, status, _ = _goal_files()
    running = _goal_running()
    try:
        st = json.loads(status.read_text())
    except (FileNotFoundError, ValueError):
        st = {}
    if running and not st:
        st = {"state": "starting"}
    if not running and st.get("state") in ("running", "paused", "starting"):
        st["state"] = "ended"  # the process is gone without its last report (crashed or killed)
    if running:
        try:
            want = control.read_text().strip()
        except OSError:
            want = "run"
        if want == "stop":
            st["state"] = "stopping"
        elif want == "pause" and st.get("state") == "running":
            st["state"] = "pausing"  # until the current step finishes
    cost = _run_cost(st.get("log"))
    return {"available": bool(cfg.get("goal")), "running": running, "state": st.get("state", "idle"), "cost": cost,
            "goal": st.get("goal", ""), "step": st.get("step", 0), "steps": st.get("steps", 0), "note": st.get("note", "")}


def _run_cost(log) -> dict | None:
    """The run's cost so far (the last call's running totals in loop.jsonl), {fast, vision, total} in USD."""
    if not log:
        return None
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            tail = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        if '"cost_total"' in line:
            try:
                c = json.loads(line)["cost_total"] or {}
            except (ValueError, KeyError):
                continue
            return {**c, "total": round(sum(v for v in c.values() if isinstance(v, (int, float))), 4)}
    return None


def goal_start(goal: str, steps: int | None = None) -> dict:
    from .agents import computer_config
    cfg = computer_config() or {}
    if not cfg.get("goal"):
        raise ValueError("no computer-use loop on this host (computer.json \"goal\")")
    if _goal_running():
        raise ValueError("a goal is already running: stop it first")
    goal = goal.strip()
    if not goal:
        raise ValueError("empty goal")
    GOAL_DIR.mkdir(parents=True, exist_ok=True)
    control, status, log = _goal_files()
    control.write_text("run")
    status.unlink(missing_ok=True)
    cmd = cfg["goal"]
    argv = [os.path.expanduser(x) for x in (cmd.split() if isinstance(cmd, str) else cmd)]
    argv += [goal, *cfg.get("goal_after", []), f"--steps={int(steps or goal_settings()['steps'])}"]
    goal_session(reset=True)  # a new paid-model session per goal (user 2026-11-02: "reset per goal")
    env = {**os.environ, **_goal_env(), "DESK_CONTROL": str(control), "DESK_STATUS": str(status)}
    with open(log, "ab") as out:
        _goal["proc"] = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
                                         cwd=cfg.get("goal_cwd") or None, start_new_session=os.name != "nt")
    return goal_status()


def goal_control(what: str) -> dict:
    """"pause", "resume" or "stop" the running goal."""
    if not _goal_running():
        raise ValueError("no goal is running")
    proc = _goal["proc"]
    control, status, _ = _goal_files()
    if what not in ("pause", "resume", "stop"):
        raise ValueError(f"unknown action {what!r}")
    control.write_text({"resume": "run"}.get(what, what))
    if what == "stop" and proc and not status.exists():  # a loop that doesn't read the control file: end it
        _kill(proc)
    return goal_status()


def _kill(proc) -> None:
    import signal
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def goal_log_image(name: str) -> Path | None:
    """A screenshot a vision call saw (calls/<name> beside the latest run's log), or None."""
    if not re.fullmatch(r"\d{1,6}\.jpg", name):
        return None
    try:
        path = Path(json.loads(_goal_files()[1].read_text())["log"]).parent / "calls" / name
    except (FileNotFoundError, ValueError, KeyError, OSError):
        return None
    return path if path.is_file() else None


def goal_log(limit: int = 400) -> list[dict]:
    """The latest run's steps for the View log panel, from its loop.jsonl: time, who decided, the action and its text,
    and the model's note; the run's end with its note."""
    _, status, _ = _goal_files()
    try:
        path = Path(json.loads(status.read_text())["log"])
        if not path.is_absolute():
            from .agents import computer_config
            path = Path(os.path.expanduser((computer_config() or {}).get("goal_cwd") or "~")) / path
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, ValueError, KeyError, OSError):
        return []
    out = []
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        ev = d.get("event")
        if ev == "decide":
            out.append({"t": d.get("t"), "kind": "step", "step": d.get("i"), "by": d.get("by", ""),
                        "action": d.get("action", ""), "text": str(d.get("text") or "")[:300], "note": d.get("note", "")})
        elif ev == "act" and d.get("result") and out and out[-1]["kind"] == "step":
            out[-1]["result"] = str(d["result"])[:200]
        elif ev == "call":
            sent = dict(d.get("sent") or {})
            image = sent.pop("image", None)
            out.append({"t": d.get("t"), "kind": "call", "n": d.get("n"), "by": d.get("by", ""), "model": d.get("model", ""),
                        "seconds": d.get("seconds"), "sent": sent, "reply": d.get("reply") or {},
                        "usage": d.get("usage"), "cost_total": d.get("cost_total"),
                        "image": Path(image).name if image else None})
        elif ev == "plan":
            out.append({"t": d.get("t"), "kind": "plan", "goal": d.get("goal", ""), "steps": d.get("steps") or [],
                        "missing": d.get("missing") or []})
        elif ev in ("start", "end"):
            out.append({"t": d.get("t"), "kind": ev, "note": d.get("note") or d.get("goal", ""), "result": d.get("result", "")})
    return out[-limit:]
