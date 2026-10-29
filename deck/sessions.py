"""tmux sessions: starting a key or an agent in one, listing them with their state, typing into them, closing them,
and what a chat session leaves in its files (deck/chat_bridge.py writes them; this side reads them)."""
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import config, status
from .agents import AGENTS, CHAT_AGENTS, YOLO_FLAGS, build_argv, pi_durable_available, resolve_cwd
from .projects import project_of
from .settings import remember_folder, settings

# ---- tmux ----

def msys_command(argv: list[str]) -> str:
    """Windows command line for an MSYS program. MSYS splits it with its own rules (Windows' \\" escapes don't
    apply; ' and " both quote), so every argument goes in single quotes, with a ' itself as "'"."""
    def arg(a: str) -> str:
        return "".join("\"'\"" if p == "'" else f"'{p}'" for p in re.findall(r"'|[^']+", a)) or "''"
    return " ".join([f'"{argv[0]}"', *map(arg, argv[1:])])


def tmux(*args: str) -> subprocess.CompletedProcess:
    argv = [config.TMUX, *args]
    try:
        return subprocess.run(msys_command(argv) if config.WINDOWS else argv, capture_output=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=10, **config.OWN_CONSOLE)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([config.TMUX, *args], 124, "", "tmux timed out")
    except OSError as e:  # no tmux (or a PATH entry this user can't read): no sessions, not a crashed request
        return subprocess.CompletedProcess([config.TMUX, *args], 127, "", f"tmux: {e.strerror or e}")


# Modified keys (Shift+Enter) reach programs that ask for them (Pi warns without it). Server-wide; set with each new
# session so an already running server gets it too.
EXTENDED_KEYS = (";", "set-option", "-s", "extended-keys", "on")


def start_tmux() -> None:
    """Windows: start the tmux server up front, with no pipes. A server forked by a later call would keep that call's
    output pipes open, and the call would never return. It stays up with no sessions, as on Linux it would restart."""
    if config.WINDOWS:
        # MSYS=noglob is for this side only (Windows -> tmux.exe): panes don't get it.
        # PATH too: the tmux server outlives Deck restarts and would keep handing panes its first, stale PATH.
        subprocess.run([config.TMUX, "start-server", ";", "set-option", "-g", "exit-empty", "off",
                        ";", "set-environment", "-g", "-u", "MSYS",
                        ";", "set-environment", "-g", "PATH", os.environ["PATH"]], timeout=10, **config.OWN_CONSOLE,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---- starting sessions ----

def session_name(b: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", b["label"].lower()).strip("-")[:24] or b["id"]
    base = f"{config.PREFIX}{slug}-{time.strftime('%H%M%S')}"
    name, n = base, 2
    while session_exists(name):  # two starts of the same key within one second
        name, n = f"{base}-{n}", n + 1
    return name


def session_env(argv: list[str], name: str = "") -> list[str]:
    """tmux -e flags for a pane running argv. Windows: MSYS bash rewrites /-looking arguments (a
    path in the first message) into Windows paths when it starts claude.exe; excluded here by their exact text,
    not with "*", which the agents' own Git Bash would inherit."""
    env = ["-e", "DECK_SESSION=1"]  # agents: they run inside Deck
    if name:  # who an agent is when it messages another session (/api/agent/sessions)
        env += ["-e", f"DECK_SESSION_NAME={name}"]
    keep = [a for a in argv if "/" in a and ";" not in a] if config.WINDOWS else []
    return env + (["-e", "MSYS2_ARG_CONV_EXCL=" + ";".join(keep)] if keep else [])


def start_run(b: dict, state: dict | None = None) -> str:
    """Start b in a new tmux session; its name. state: a chat's saved state (session_trash.py) to carry on from: its
    conversation and its own choices."""
    cwd = resolve_cwd(b)
    if not os.path.isdir(cwd):
        raise ValueError(f"folder not found: {cwd}")
    name = session_name(b)
    if b["type"] == "pi":
        b = {**b, "durable": settings()["pidurable"] and pi_durable_available()}
    # Agents always start as a chat. Their terminal mode is deprecated (the "chat" setting is ignored and hidden): on
    # Windows a tmux pane gives native programs pipes, not a terminal. A session started in it before keeps it on Restart.
    if b["type"] in CHAT_AGENTS:
        b = {**b, "chat": True, "session": name}
        if state:  # the bridge resumes what this state file names
            config.CHAT_DIR.mkdir(parents=True, exist_ok=True)
            (config.CHAT_DIR / f"{name}.state").write_text(json.dumps({"state": "working", **state}), encoding="utf-8")
            b = {**b, "resume": True}
    # Chained in one tmux call so remain-on-exit is set before a fast script can exit.
    argv = build_argv(b)
    # A script's output also goes to a log (config.run_log): the popup shows it, and a pane's screen doesn't keep it
    # everywhere (Windows' console clears it when the script exits)
    log = []
    if b["type"] == "shell":
        config.run_log(name).parent.mkdir(parents=True, exist_ok=True)
        log = [";", "pipe-pane", "-O", "-t", name, f"exec /usr/bin/tee -a '{config.tmux_path(config.run_log(name))}' > /dev/null"]
    r = tmux("new-session", "-d", "-s", name, "-c", config.tmux_path(cwd), "-x", "120", "-y", "40", *session_env(argv, name),
             "--", *argv,
             ";", "set-option", "-t", name, "remain-on-exit", "on",
             ";", "set-option", "-t", name, "status", "off",
             ";", "set-option", "-t", name, "@deck_label", b["label"],
             ";", "set-option", "-t", name, "@deck_type", b["type"],
             ";", "set-option", "-t", name, "@deck_button", b.get("id", ""),
             ";", "set-option", "-t", name, "@deck_chat", "1" if b.get("chat") else "",
             ";", "set-option", "-g", "window-size", "latest",
             *log,
             *EXTENDED_KEYS)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "tmux failed")
    if b["type"] != "shell" and b["id"] != "builder":
        remember_folder(cwd)
    return name


def restart_agent(name: str) -> None:
    """Restart the agent process in a run's pane (same folder, same YOLO setting), resuming its conversation."""
    target = f"={name}:"
    fmt = lambda f: tmux("display-message", "-p", "-t", target, f).stdout.strip()
    kind = fmt("#{@deck_type}")
    if kind not in AGENTS:
        raise ValueError("not an agent session")
    start = fmt("#{pane_start_command}")
    b = {"type": kind, "text": "", "resume": True, "yolo": any(f in start for f in YOLO_FLAGS.get(kind, [])),
         "durable": "pi-durable" in start, "chat": fmt("#{@deck_chat}") == "1", "session": name}
    argv = build_argv(b)
    r = tmux("respawn-pane", "-k", "-t", target, "-c", fmt("#{pane_current_path}") or config.WORK, *session_env(argv, name),
             "--", *argv)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "tmux failed")


SHELL_SESSION = "deck-shell"


def ensure_shell(new: bool = False) -> str:
    """The Terminal tab's persistent login shell; new=True opens another one (Shell 2, 3, ...)."""
    name, label = SHELL_SESSION, "Shell"
    if new:
        n = 2
        while session_exists(f"{SHELL_SESSION}-{n}"):
            n += 1
        name, label = f"{SHELL_SESSION}-{n}", f"Shell {n}"
    if not session_exists(name):
        r = tmux("new-session", "-d", "-s", name, "-c", config.tmux_path(config.HOME), "-x", "120", "-y", "40",
                 *session_env([], name), "--", config.BASH, "-l",
                 ";", "set-option", "-t", name, "@deck_label", label,
                 ";", "set-option", "-t", name, "@deck_type", "terminal",
                 ";", "set-option", "-t", name, "status", "off",
                 ";", "set-option", "-g", "window-size", "latest",
                 *EXTENDED_KEYS)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or "tmux failed")
    return name



# ---- listing them ----

def pane_subtitle(title: str, path: str) -> str:
    """What the tab's small second line shows: the title the program set (Claude's task name,
    spinner glyphs dropped), else the folder. tmux's default title is the host name."""
    title = re.sub(r"^(OC \| |π - )", "", re.sub(r"^[^\w~/(\[]+", "", title)).strip()  # OpenCode "OC | ", Pi "π - "
    title = title.removesuffix(" - " + path.rstrip("/").rsplit("/", 1)[-1])  # Pi "π - <name> - <folder>"
    if title and title != platform.node():
        return title[:60]
    path, home = config.win_path(path), str(Path.home()).replace("\\", "/")
    return ("~" + path[len(home):]) if path.startswith(home) else path


def agent_status(pane: str, pane_pid: str) -> list[str]:
    """Claude's status line parts, uncut (deploy/agents/agent-statusline.py writes them per tmux pane); [] when unknown.
    Trusted only if the writer runs inside this pane (a TMUX_PANE inherited from elsewhere can point anywhere)."""
    try:
        d = json.loads((Path.home() / ".cache/deck-status" / (pane.lstrip("%") + ".json")).read_text())
        pid = int(d.get("pid") or 0)
        for _ in range(8):
            if pid == int(pane_pid):
                return [str(p) for p in d["parts"]]
            if pid <= 1:
                break
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    return []


# What list_sessions asks tmux for each session, in this order ("|~|" between them: not tabs, which tmux on Windows
# prints as "_")
SESSION_FORMAT = "|~|".join(["#{session_name}", "#{session_created}", "#{pane_dead}", "#{pane_dead_status}",
                             "#{session_attached}", "#{window_activity}", "#{@deck_label}", "#{@deck_type}", "#{@deck_button}",
                             "#{pane_title}", "#{pane_current_path}", "#{pane_id}", "#{pane_pid}", "#{@deck_chat}",
                             "#{@deck_title}", "#{@deck_project}"])
STATE_RANK = {"needs_you": 0, "your_turn": 1, "working": 2, "idle": 3, "failed": 4, "done": 5}


def list_sessions() -> list[dict]:
    """Every tmux session on this host with its state (status.analyze; a chat's from its bridge), the ones that need
    you first."""
    r = tmux("list-sessions", "-F", SESSION_FORMAT)
    if r.returncode:  # no server running = no sessions
        return []
    now = time.time()
    out = []
    for line in r.stdout.splitlines():
        (name, created, dead, exit_status, attached, activity, label, kind, button, title, path, pane_id, pane_pid, chat,
         mine, project) = line.split("|~|", 15)
        exit_code = int(exit_status) if exit_status else None
        pane = tmux("capture-pane", "-p", "-t", f"={name}:").stdout.splitlines()
        info = status.analyze(pane, kind, now - int(activity or created), dead == "1", exit_code)
        if chat:  # the bridge knows the state and name; its pane is only a transcript
            cs = chat_state(name)
            if dead != "1" and cs.get("state") in ("needs_you", "your_turn", "working"):
                info.update(state=cs["state"], preview=cs.get("preview", ""), options=[])
            title = cs.get("name") or title
        out.append({
            "name": name,
            "label": label or name,
            "type": kind or "shell",
            "button": button,
            "created": int(created),
            "activity": int(activity or created),
            "running": dead != "1",
            "exit": exit_code,
            "attached": int(attached),
            "deck": name.startswith(config.PREFIX),
            "chat": bool(chat),
            "title": mine or pane_subtitle(title, path),  # a name the user gave the tab wins
            "project": project or project_of(path),  # its main page shows atop its chat (10-shelf.js renderProjBar)
            "status": agent_status(pane_id, pane_pid),
            **info,
        })
    return sorted(out, key=lambda s: (STATE_RANK[s["state"]], -s["activity"]))


# ---- typing into one ----

SPECIAL_KEYS = {"enter": "Enter", "esc": "Escape", "ctrlc": "C-c", "tab": "Tab", "up": "Up", "down": "Down"}


def send_input(name: str, text: str = "", key: str = "", enter: bool = False, nav: int | None = None) -> None:
    """Type into a session without attaching (quick replies from the dashboard).
    nav: pick an arrow-key menu item that many steps from the cursor (negative = up), then Enter."""
    target = f"={name}:"
    if nav is not None:
        for _ in range(min(abs(nav), 20)):
            tmux("send-keys", "-t", target, "Down" if nav > 0 else "Up")
        tmux("send-keys", "-t", target, "Enter")
        return
    if text:  # "--": text may start with "-"; tmux takes a trailing ";" as a command separator unless escaped
        tmux("send-keys", "-t", target, "-l", "--", text[:-1] + "\\;" if text.endswith(";") else text)
    if key:
        if key not in SPECIAL_KEYS:
            raise ValueError(f"unknown key {key}")
        tmux("send-keys", "-t", target, SPECIAL_KEYS[key])
    if enter:
        tmux("send-keys", "-t", target, "Enter")


async def type_line(name: str, text: str) -> None:
    """Type a line into a session and submit it. Enter goes separately: sent with the text, agents take it as a pasted
    newline. tmux runs off the event loop (each call is a process start, slow on Windows)."""
    loop = asyncio.get_running_loop()
    for i in range(0, len(text), 8000):  # Windows command lines stop at 32K characters
        await loop.run_in_executor(None, send_input, name, text[i:i + 8000])
    if text:
        await asyncio.sleep(0.4)
    await loop.run_in_executor(None, lambda: send_input(name, key="enter"))


# ---- their lives ----

def session_exists(name: str) -> bool:
    return tmux("has-session", "-t", f"={name}").returncode == 0


def kill_session(name: str) -> None:
    tmux("kill-session", "-t", f"={name}")
    files = [config.CHAT_DIR / f"{name}{x}" for x in (".jsonl", ".in.jsonl", ".state", ".hooks.jsonl", ".opencode.json")]
    files.append(config.run_log(name))
    for _ in range(20):  # Windows: the bridge holds its log open until it has seen the pane go
        try:
            for f in files:
                f.unlink(missing_ok=True)
            shutil.rmtree(config.CHAT_DIR / f"{name}.media", ignore_errors=True)  # its replies' files (fetched again if restored)
            return
        except PermissionError:
            time.sleep(0.1)


def clear_finished() -> int:
    dead = [s["name"] for s in list_sessions() if s["deck"] and not s["running"]]
    for n in dead:
        kill_session(n)
    return len(dead)


def rename_session(name: str, title: str) -> None:
    """The tab's own name (right-click a tab on a PC), over the title its program sets; empty: back to that title."""
    title = " ".join(title.split())[:80]
    tmux("set-option", "-t", f"={name}:", *(["@deck_title", title] if title else ["-u", "@deck_title"]))
    # A Claude chat's conversation takes the name too, as Claude's own /rename writes it (a custom-title line in its
    # transcript), so Resume (Deck's and claude --resume) lists it by that name
    if title and is_chat(name) and session_meta(name).get("agent") in ("codex", "opencode", "pi"):
        chat_command(name, {"t": "rename", "title": title})  # its bridge renames the conversation in the agent's way
    conv = chat_state(name).get("resume") if title and is_chat(name) and session_meta(name).get("agent") == "claude" else ""
    if conv:
        from .session_move import claude_files
        try:
            transcript = next(iter(claude_files(conv).values()))
            with open(transcript, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": conv}) + "\n")
        except (ValueError, OSError):
            pass  # nothing said yet (no transcript): the tab keeps its name all the same


# ---- what they are ----

def is_chat(name: str) -> bool:
    return tmux("display-message", "-p", "-t", f"={name}:", "#{@deck_chat}").stdout.strip() == "1"


def session_meta(name: str) -> dict:
    """Where a session is and what runs it, for a shelf item saved from it: host, agent, tab name, title, folder,
    when it started and (a chat's) conversation id. {} for no such session."""
    fmt = "|~|".join(["#{@deck_type}", "#{@deck_label}", "#{pane_title}", "#{pane_current_path}", "#{session_created}",
                      "#{@deck_project}"])
    r = tmux("display-message", "-p", "-t", f"={name}:", fmt)
    if r.returncode or "|~|" not in r.stdout:
        return {}
    kind, label, title, path, created, project = r.stdout.strip().split("|~|", 5)
    cs = chat_state(name)
    meta = {"host": config.HOST_NAME, "session": name, "agent": kind or "shell", "label": label,
            "title": cs.get("name") or pane_subtitle(title, path), "folder": path,
            "started": int(created) if created.isdigit() else 0, "conversation": cs.get("resume", ""),
            "project": project or project_of(path)}
    return {k: v for k, v in meta.items() if v}


def set_session_project(name: str, folder: str = "", project: str = "") -> str:
    """The project a session works in, over the one its starting folder gives (sessions mostly start in the work
    folder): from a folder its agent works in (project_of), or a project name as given (Harness → Project); neither:
    back to its starting folder's. Raises ValueError for a folder in no project."""
    if folder:
        project = project_of(folder)
        if not project:
            raise ValueError(f"{folder} is in no project (a git repo, or a folder under home other than the work folder)")
    project = " ".join(project.split())[:300]
    tmux("set-option", "-t", f"={name}:", *(["@deck_project", project] if project else ["-u", "@deck_project"]))
    return project or session_meta(name).get("project", "")


# ---- their output ----

ANSI = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)|[()][0-9A-Za-z]|[=>78DEHM])")


def run_text(name: str, limit: int = 200_000) -> str | None:
    """A script's output from its log (its end, if long), as plain text: escape codes gone, each line as last drawn."""
    try:
        with open(config.run_log(name), "rb") as f:
            f.seek(max(0, f.seek(0, 2) - limit))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = ANSI.sub("", raw).replace("\r\n", "\n").split("\n")
    return "\n".join(line.rsplit("\r", 1)[-1] for line in lines)


def session_output(name: str) -> str:
    """A session's output as plain text: a chat's conversation, a script's log, else tmux history plus the screen."""
    if is_chat(name):
        return chat_text(name) + "\n"
    if (log := run_text(name)) is not None:  # a script: everything it printed (config.run_log)
        return log.rstrip() + "\n"
    return tmux("capture-pane", "-p", "-J", "-S", "-2000", "-t", f"={name}:").stdout.rstrip() + "\n"


def chat_state(name: str) -> dict:
    """A chat session's {state, preview, name} as its bridge last wrote them; {} before it has."""
    try:
        return json.loads((config.CHAT_DIR / f"{name}.state").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def chat_text(name: str) -> str:
    """A chat session's conversation as plain text (Copy text), from its bridge's event log."""
    out = []
    try:
        lines = (config.CHAT_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    msgs = {}  # message id -> its latest blocks: a message's events each carry it so far
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("t") == "clear":  # a (re)start: the history follows again
            out, msgs = [], {}
        elif ev.get("t") == "user":  # another session's agent's message marked (its stamp's source: chat_bridge.AGENT_FROM)
            frm = ev.get("from") or ""
            mark = f"[from {frm}, not the user] " if re.fullmatch(r"agent in .+|another agent", frm) else ""
            out.append("> " + mark + ev["text"])
        elif ev.get("t") == "msg" and ev.get("blocks") is not None:
            if ev.get("m") not in msgs:
                out.append(("msg", ev.get("m")))
            msgs[ev.get("m")] = ev["blocks"]
        elif ev.get("t") == "note":
            out.append(f"({ev['text']})")
    text = []
    for x in out:
        if isinstance(x, tuple):
            text += [b["text"] if b["kind"] == "text" else f"[{b['name']}]" for b in msgs[x[1]] if b["kind"] != "think"]
        else:
            text.append(x)
    return "\n\n".join(t for t in text if t.strip())


def chat_command(name: str, cmd: dict) -> None:
    """Queue a command for a chat session's bridge (it reads its .in.jsonl)."""
    config.CHAT_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.CHAT_DIR / f"{name}.in.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(cmd, ensure_ascii=False) + "\n")
