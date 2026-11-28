"""Sessions: script keys, shells and terminal agents in tmux; agent chats as detached bridges (deck/bridges.py).
Starting them, listing them with their state, typing into them, closing them, and what a chat session leaves in its
files (deck/chat_bridge.py writes them; this side reads them). A chat started before chats ran detached stays in its
tmux pane until it closes."""
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import bridges, config, status
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
        # A new name's bridge reads its commands from the start (deck/bridge/bridge.py): none may be left from an
        # earlier chat of the same name whose files were never removed
        (config.CHAT_DIR / f"{name}.in.jsonl").unlink(missing_ok=True)
        if state:  # the bridge resumes what this state file names
            config.CHAT_DIR.mkdir(parents=True, exist_ok=True)
            (config.CHAT_DIR / f"{name}.state").write_text(json.dumps({"state": "working", **state}), encoding="utf-8")
            b = {**b, "resume": True}
    argv = build_argv(b)
    if b.get("chat"):  # no tmux: its bridge runs detached, and outlives a server restart on its own
        bridges.start(name, argv, cwd, label=b["label"], type=b["type"], button=b.get("id", ""),
                      computer=bool(b.get("computer")), yolo=bool(b.get("yolo")))
        if b.get("id") not in ("builder", "bug"):
            remember_folder(cwd)
        return name
    # Chained in one tmux call so remain-on-exit is set before a fast script can exit.
    # A script's output also goes to a log (config.run_log): the popup shows it, and a pane's screen doesn't keep it
    # everywhere (Windows' console clears it when the script exits)
    log, out = [], []
    if b["type"] == "shell":
        config.run_log(name).parent.mkdir(parents=True, exist_ok=True)
        log = [";", "pipe-pane", "-O", "-t", name, f"exec /usr/bin/tee -a '{config.tmux_path(config.run_log(name))}' > /dev/null"]
        config.run_files(name).mkdir(parents=True, exist_ok=True)  # DECK_OUT: files for the device that pressed it
        out = ["-e", f"DECK_OUT={config.tmux_path(config.run_files(name))}"]
    r = tmux("new-session", "-d", "-s", name, "-c", config.tmux_path(cwd), "-x", "120", "-y", "40", *session_env(argv, name),
             *out, "--", *argv,
             ";", "set-option", "-t", name, "remain-on-exit", "on",
             ";", "set-option", "-t", name, "status", "off",
             ";", "set-option", "-t", name, "@deck_label", b["label"],
             ";", "set-option", "-t", name, "@deck_type", b["type"],
             ";", "set-option", "-t", name, "@deck_button", b.get("id", ""),
             ";", "set-option", "-t", name, "@deck_chat", "1" if b.get("chat") else "",
             ";", "set-option", "-t", name, "@deck_computer", "1" if b.get("computer") else "",
             ";", "set-option", "-g", "window-size", "latest",
             *log,
             *EXTENDED_KEYS)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "tmux failed")
    if b["type"] != "shell" and b["id"] not in ("builder", "bug"):
        remember_folder(cwd)
    return name


def restart_agent(name: str) -> None:
    """Restart the agent process in a run's pane (same folder, same YOLO setting), resuming its conversation."""
    if bridges.exists(name):  # a detached chat: a new bridge on the same files, once the old one is gone
        m = bridges.meta(name)
        b = {"type": m.get("type", ""), "text": "", "resume": True, "yolo": bool(m.get("yolo")), "chat": True,
             "session": name, "computer": bool(m.get("computer"))}
        if b["type"] not in AGENTS:
            raise ValueError("not an agent session")
        bridges.stop(name)
        argv = build_argv(b)
        bridges.update(name, argv=argv, pid=bridges.spawn(name, argv, m.get("cwd") or str(config.HOME)),
                       spawned=int(time.time()))
        return
    target = f"={name}:"
    fmt = lambda f: tmux("display-message", "-p", "-t", target, f).stdout.strip()
    kind = fmt("#{@deck_type}")
    if kind not in AGENTS:
        raise ValueError("not an agent session")
    start = fmt("#{pane_start_command}")
    b = {"type": kind, "text": "", "resume": True, "yolo": any(f in start for f in YOLO_FLAGS.get(kind, [])),
         "durable": "pi-durable" in start, "chat": fmt("#{@deck_chat}") == "1", "session": name,
         "computer": fmt("#{@deck_computer}") == "1"}
    if b["chat"]:  # a chat started in tmux before chats ran detached: it moves out, on the same name and files
        cwd = native_folder(fmt("#{pane_current_path}"))
        label, button, title, project = (fmt(f) for f in ("#{@deck_label}", "#{@deck_button}", "#{@deck_title}",
                                                          "#{@deck_project}"))
        tmux("kill-session", "-t", f"={name}")  # not kill_session: that deletes the chat's files
        end = time.time() + 10
        while time.time() < end and tmux("has-session", "-t", f"={name}").returncode == 0:
            time.sleep(0.1)
        time.sleep(0.5)  # its bridge sees the pane go and exits (the next one rewrites the same event log)
        bridges.start(name, build_argv(b), cwd, label=label or kind, type=kind, button=button,
                      computer=b["computer"], yolo=b["yolo"], **({"title": title} if title else {}),
                      **({"project": project} if project else {}))
        return
    argv = build_argv(b)
    r = tmux("respawn-pane", "-k", "-t", target, "-c", fmt("#{pane_current_path}") or config.WORK, *session_env(argv, name),
             "--", *argv)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "tmux failed")


def native_folder(path: str) -> str:
    """A folder tmux reports for a pane as the OS names it, if it still exists; else the default work folder. Windows:
    MSYS forms (/c/Users/x, /tmp) through the kit's cygpath."""
    if config.WINDOWS and path.startswith("/"):
        try:
            r = subprocess.run([str(Path(config.TMUX).with_name("cygpath.exe")), "-m", path], capture_output=True,
                               text=True, timeout=5, **config.OWN_CONSOLE)
            path = r.stdout.strip() or config.win_path(path)
        except OSError:
            path = config.win_path(path)
    return path if path and os.path.isdir(path) else (config.WORK if os.path.isdir(config.WORK) else str(config.HOME))


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

def pane_subtitle(title: str, path: str, label: str = "") -> str:
    """What the tab's small second line shows: the title the program set (Claude's task name,
    spinner glyphs dropped), else `label` when given (a script key's name), else the folder. tmux's default title is
    the host name."""
    title = re.sub(r"^(OC \| |π - )", "", re.sub(r"^[^\w~/(\[]+", "", title)).strip()  # OpenCode "OC | ", Pi "π - "
    title = title.removesuffix(" - " + path.rstrip("/").rsplit("/", 1)[-1])  # Pi "π - <name> - <folder>"
    if title and title != platform.node():
        return title[:60]
    if label:
        return label[:60]
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
                             "#{@deck_title}", "#{@deck_project}", "#{@deck_computer}", "#{@deck_folder}"])
STATE_RANK = {"needs_you": 0, "your_turn": 1, "working": 2, "idle": 3, "failed": 4, "done": 5}


def list_sessions() -> list[dict]:
    """Every tmux session on this host with its state (status.analyze; a chat's from its bridge), the ones that need
    you first."""
    r = tmux("list-sessions", "-F", SESSION_FORMAT)
    now = time.time()
    out = [bridge_row(n) for n in bridges.names()]
    for line in r.stdout.splitlines() if r.returncode == 0 else []:  # no tmux server running: no tmux sessions
        (name, created, dead, exit_status, attached, activity, label, kind, button, title, path, pane_id, pane_pid, chat,
         mine, project, computer, folder) = (line.split("|~|", 17) + [""])[:18]  # an older format: no folder
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
            "computer": bool(computer),  # a Computer session: the page shows this host's screen above it
            # a name the user gave the tab wins; a script with no title of its own shows its name, not the folder ("~")
            "title": mine or pane_subtitle(title, path, label if kind == "shell" and not chat else ""),
            "project": project or project_of(path),  # its main page shows atop its chat (10-shelf.js renderProjBar)
            "folder": work_folder(folder or path),  # the Code view's file tree (deck/code.py)
            "status": agent_status(pane_id, pane_pid),
            **info,
        })
    return sorted(out, key=lambda s: (STATE_RANK[s["state"]], -s["activity"]))


def bridge_row(name: str) -> dict:
    """A detached chat's row, shaped as a tmux session's: its state from its bridge, running while its lock is held."""
    m, cs = bridges.meta(name), chat_state(name)
    running = bridges.alive(name)
    created = int(m.get("created") or 0)
    try:
        activity = int((config.CHAT_DIR / f"{name}.jsonl").stat().st_mtime)
    except OSError:
        activity = created
    code = None if running else bridges.exit_code(name)
    if not running and code is None and time.time() - created < 15 and not cs:  # still starting (~1 s): not failed
        running = True
    if running:
        state = cs.get("state") if cs.get("state") in ("needs_you", "your_turn", "working") else "your_turn"
        preview = cs.get("preview", "")
    else:  # gone without saying how (killed, a crash, a reboot): failed, unless it said it was done
        state = "done" if code == 0 or (code is None and cs.get("state") == "done") else "failed"
        code = code if code is not None else (0 if state == "done" else 1)
        preview = cs.get("preview") or bridges.log_tail(name, 300).strip()
    path = config.tmux_path(m.get("cwd", ""))
    return {
        "name": name, "label": m.get("label") or name, "type": m.get("type") or "claude", "button": m.get("button", ""),
        "created": created, "activity": activity, "running": running, "exit": code, "attached": 0,
        "deck": name.startswith(config.PREFIX), "chat": True, "computer": bool(m.get("computer")),
        "title": m.get("title") or cs.get("name") or pane_subtitle("", path),
        "project": m.get("project") or project_of(path), "folder": work_folder(m.get("folder") or m.get("cwd", "")),
        "status": [],
        "state": state, "preview": preview[-160:], "snippet": "", "options": [],
    }


# ---- typing into one ----

SPECIAL_KEYS = {"enter": "Enter", "esc": "Escape", "ctrlc": "C-c", "tab": "Tab", "up": "Up", "down": "Down"}


def send_input(name: str, text: str = "", key: str = "", enter: bool = False, nav: int | None = None) -> None:
    """Type into a session without attaching (quick replies from the dashboard).
    nav: pick an arrow-key menu item that many steps from the cursor (negative = up), then Enter."""
    if bridges.exists(name):  # a detached chat has no terminal: text is a message, Esc and Ctrl+C stop its turn
        if text:
            chat_command(name, {"t": "say", "text": text})
        elif key in ("esc", "ctrlc"):
            chat_command(name, {"t": "abort"})
        return
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
    if bridges.exists(name):
        chat_command(name, {"t": "say", "text": text})
        return
    loop = asyncio.get_running_loop()
    for i in range(0, len(text), 8000):  # Windows command lines stop at 32K characters
        await loop.run_in_executor(None, send_input, name, text[i:i + 8000])
    if text:
        await asyncio.sleep(0.4)
    await loop.run_in_executor(None, lambda: send_input(name, key="enter"))


# ---- their lives ----

def session_exists(name: str) -> bool:
    return bridges.exists(name) or tmux("has-session", "-t", f"={name}").returncode == 0


def kill_session(name: str) -> None:
    if bridges.exists(name):
        bridges.remove(name)  # returns once its bridge is gone
    else:
        tmux("kill-session", "-t", f"={name}")
    files = [config.CHAT_DIR / f"{name}{x}" for x in (".jsonl", ".in.jsonl", ".state", ".hooks.jsonl", ".opencode.json")]
    files.append(config.run_log(name))
    for _ in range(20):  # Windows: the bridge holds its log open until it has seen the pane go
        try:
            for f in files:
                f.unlink(missing_ok=True)
            shutil.rmtree(config.CHAT_DIR / f"{name}.media", ignore_errors=True)  # its replies' files (fetched again if restored)
            shutil.rmtree(config.run_files(name), ignore_errors=True)  # a script's files for the device (DECK_OUT)
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
    if bridges.exists(name):
        bridges.update(name, title=title or None)
    else:
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
    if bridges.exists(name):
        return True
    return tmux("display-message", "-p", "-t", f"={name}:", "#{@deck_chat}").stdout.strip() == "1"


def session_meta(name: str) -> dict:
    """Where a session is and what runs it, for a shelf item saved from it: host, agent, tab name, title, folder,
    when it started and (a chat's) conversation id. {} for no such session."""
    if bridges.exists(name):
        m, cs = bridges.meta(name), chat_state(name)
        path = config.tmux_path(m.get("cwd", ""))  # as tmux reports a pane's folder (callers expect that form)
        meta = {"host": config.HOST_NAME, "session": name, "agent": m.get("type", ""), "label": m.get("label", ""),
                "title": m.get("title") or cs.get("name") or pane_subtitle("", path), "folder": path,
                "started": int(m.get("created") or 0), "conversation": cs.get("resume", ""),
                "project": m.get("project") or project_of(path), "computer": bool(m.get("computer"))}
        return {k: v for k, v in meta.items() if v}
    fmt = "|~|".join(["#{@deck_type}", "#{@deck_label}", "#{pane_title}", "#{pane_current_path}", "#{session_created}",
                      "#{@deck_project}", "#{@deck_computer}"])
    r = tmux("display-message", "-p", "-t", f"={name}:", fmt)
    if r.returncode or "|~|" not in r.stdout:
        return {}
    kind, label, title, path, created, project, computer = r.stdout.strip().split("|~|", 6)
    cs = chat_state(name)
    meta = {"host": config.HOST_NAME, "session": name, "agent": kind or "shell", "label": label,
            "title": cs.get("name") or pane_subtitle(title, path, label if kind == "shell" else ""), "folder": path,
            "started": int(created) if created.isdigit() else 0, "conversation": cs.get("resume", ""),
            "project": project or project_of(path), "computer": computer == "1"}
    return {k: v for k, v in meta.items() if v}


def set_session_project(name: str, folder: str = "", project: str = "") -> str:
    """The project a session works in, over the one its starting folder gives (sessions mostly start in the work
    folder): from a folder its agent works in (project_of), or a project name as given (Harness → Project); neither:
    back to its starting folder's. Raises ValueError for a folder in no project."""
    if folder:
        project = project_of(folder)
        if not project:
            raise ValueError(f"{folder} is in no project (a git repo, or a folder under home other than the work folder)")
        folder = config.win_path(os.path.expanduser(folder))  # as this OS names it; the Code view's tree opens there
    project = " ".join(project.split())[:300]
    if bridges.exists(name):
        bridges.update(name, project=project or None, folder=folder or None)
        return project or session_meta(name).get("project", "")
    tmux("set-option", "-t", f"={name}:", *(["@deck_project", project] if project else ["-u", "@deck_project"]))
    tmux("set-option", "-t", f"={name}:", *(["@deck_folder", folder] if folder else ["-u", "@deck_folder"]))
    return project or session_meta(name).get("project", "")


def work_folder(path: str) -> str:
    """The folder a session works in, as this OS writes it with forward slashes (C:/Users/x/repo; an MSYS path from
    tmux becomes one): the Code view lists it (deck/code.py)."""
    return Path(config.win_path(path)).as_posix() if path else ""


def yolo(name: str) -> bool:
    """It skips permission prompts (the YOLO setting it started with)."""
    if bridges.exists(name):
        return bool(bridges.meta(name).get("yolo"))
    start = tmux("display-message", "-p", "-t", f"={name}:", "#{pane_start_command}").stdout
    return any(f in start for f in YOLO_FLAGS.get(session_meta(name).get("agent", ""), []))


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


# A script key hands files to the device that pressed it by putting them in DECK_OUT (config.run_files): the key's
# window saves each one (a browser: Downloads; the phone app: Documents/Deck), then Deck leaves ".<file>.saved" beside
# it, so the script knows it arrived and can do something else when nothing saves it. Dotfiles aren't offered: a
# script writes ".<file>.part" and renames it when done.
RUN_FILE = re.compile(r"\w[\w .,()+=-]{0,150}")


def run_files(name: str) -> list[dict]:
    """The files a script offers now: [{file, size}], unsaved ones only."""
    try:
        found = sorted(config.run_files(name).iterdir())
    except OSError:
        return []
    names = {f.name for f in found}
    return [{"file": f.name, "size": f.stat().st_size} for f in found
            if f.is_file() and RUN_FILE.fullmatch(f.name) and f".{f.name}.saved" not in names]


def run_file(name: str, file: str) -> Path | None:
    """Session `name`'s offered file `file`, or None (gone, or a name that would leave its folder)."""
    if not name or name.startswith(".") or "/" in name or "\\" in name or not RUN_FILE.fullmatch(file):
        return None
    f = config.run_files(name) / file
    return f if f.is_file() else None


def run_file_saved(name: str, file: str) -> bool:
    """A device saved `file`: its marker for the script. False when there is no such file."""
    f = run_file(name, file)
    if f is None:
        return False
    (f.parent / f".{file}.saved").touch()
    return True


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
