"""Button store and tmux run logic. No web dependencies, so it is unit-testable."""
import asyncio
import json
import os
import platform
import tomllib
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

TYPES = ("shell", "claude", "codex", "opencode", "pi", "agy", "openclaw")
# "Skip permissions" (YOLO): the agent runs tools and commands without asking. Pi never asks, so it has no flag.
YOLO_FLAGS = {"claude": ["--dangerously-skip-permissions"], "codex": ["--dangerously-bypass-approvals-and-sandbox"],
              "opencode": ["--auto"], "agy": ["--dangerously-skip-permissions"]}
# Settings → Default effort: each agent's own levels and the flag that sets one (none: the agent's own default).
# OpenCode has none: its levels (variants) differ per model; /effort in a chat picks one.
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"), "codex": ("minimal", "low", "medium", "high", "xhigh"),
           "pi": ("off", "minimal", "low", "medium", "high", "xhigh", "max"), "agy": ("low", "medium", "high")}
EFFORT_FLAG = {"claude": lambda x: ["--effort", x], "codex": lambda x: ["-c", f"model_reasoning_effort={x}"],
               "pi": lambda x: ["--thinking", x], "agy": lambda x: ["--effort", x]}
# Settings → Default model: Claude's aliases and the flag that sets one. Synced between hosts like the rest of Settings,
# and never changed by a chat's /model (that pick is the session's own: the bridge undoes Claude's "saved as default").
MODELS = {"claude": ("fable", "opus", "sonnet", "haiku")}
MODEL_FLAG = {"claude": lambda x: ["--model", x]}
# OpenCode sampling per agent (Settings → OpenCode sampling), applied by an OpenCode plugin. Ranges: OpenRouter's.
SAMPLING = {"temperature": (0.0, 2.0), "top_p": (0.0, 1.0), "top_k": (0, 1000)}
SAMPLING_AGENTS = ("plan", "build")
PREFIX = "deck-"
HOME = Path.home()
DATA = Path(os.environ.get("DECK_DATA", HOME / ".config" / "deck"))


def _load_env_file() -> None:
    """The service gets ~/.config/deck/env from systemd; CLI tools (deck-notes, deck-key) read it here."""
    try:
        lines = (DATA / "env").read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        k, sep, v = line.strip().partition("=")
        if sep and k.startswith("DECK_") and k not in os.environ:
            os.environ[k] = v


_load_env_file()
WORK = os.environ.get("DECK_WORK", str(HOME / "work"))  # default folder for agent keys
KEYS_DIR = os.path.join(WORK, "deck-keys")  # where the key-builder agent writes scripts
HOST_NAME = os.environ.get("DECK_HOST_NAME") or platform.node()
# Machine notes: a git repo every Deck host keeps a full copy of and syncs with its peers (deck/notes.py).
NOTES = Path(os.path.expanduser(os.environ.get("DECK_NOTES", str(HOME / "deck-notes"))))
INVENTORY = os.environ.get("DECK_INVENTORY", str(NOTES / "inventory.toml"))
VNC_FILE = os.environ.get("DECK_VNC_FILE", str(DATA / "vnc.json"))  # {"password", "path"}; absent = no Screen tab

# Windows (the PC app, no WSL): sessions run in the MSYS2 tmux and bash the app ships (DECK_MSYS: the kit's root,
# desktop/msys-kit.sh). Agents are the native Windows builds; Claude Code uses Git for Windows' bash for its tools.
WINDOWS = sys.platform == "win32"
TMUX = "tmux"
BASH = "bash"
if WINDOWS:
    _kit = Path(os.environ.get("DECK_MSYS", Path(__file__).parent.parent / "msys")) / "usr" / "bin"
    TMUX = str(_kit / "tmux.exe")
    # The kit's own bash, by path: PATH finds Git for Windows' first, whose MSYS runtime can't see this tmux's
    # terminal, so a pane's shell got pipes and typed input never reached it.
    BASH = (_kit / "bash.exe").as_posix()
    _local = os.environ.get("LOCALAPPDATA", str(HOME))
    _path = [str(_kit), str(HOME / ".local" / "bin"), os.path.join(os.environ.get("APPDATA", ""), "npm"),
             str(HOME / ".pi" / "agent" / "bin")]
    # `uv run --with` puts throwaway cache venvs first on PATH; agents' `python` then had no packages and lost installs
    _inherited = [p for p in os.environ.get("PATH", "").split(os.pathsep)
                  if p and not re.search(r"[\\/]uv[\\/]cache[\\/]|[\\/]deck[\\/]server[\\/]\.venv", p, re.I)]
    os.environ.pop("VIRTUAL_ENV", None)
    os.environ.update({
        "PATH": os.pathsep.join([p for p in _path if p not in _inherited]
                                + _inherited),  # a restarted server inherits the last one's PATH
        "HOME": str(HOME),  # MSYS2's ~ would otherwise be inside the kit
        "TMUX_TMPDIR": os.path.join(_local, "deck", "tmux"),  # the kit may be read-only (Program Files)
        "MSYS": "noglob",  # MSYS programs started from Windows would glob and brace-expand their arguments
        "MSYS2_PATH_TYPE": "inherit",  # login bash keeps Windows' PATH (where the agents are)
        "CHERE_INVOKING": "1",  # and stays in the session's folder
        "LANG": os.environ.get("LANG") or "C.UTF-8",  # else tmux treats the agents' output as ASCII
    })
    os.makedirs(os.environ["TMUX_TMPDIR"], exist_ok=True)
    _git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
    if _git_bash.exists():
        os.environ.setdefault("CLAUDE_CODE_GIT_BASH_PATH", str(_git_bash))


def tmux_path(path) -> str:
    """A folder as tmux takes it: on Windows the MSYS form (C:\\Users\\x -> /c/Users/x); tmux ignores a drive path."""
    path = Path(path).as_posix()
    return f"/{path[0].lower()}{path[2:]}" if WINDOWS and re.match(r"^[a-zA-Z]:/", path) else path


def win_path(path: str) -> str:
    """An MSYS path (/c/Users/...) as Windows writes it (C:/Users/...); other paths unchanged."""
    m = re.match(r"^/([a-zA-Z])(/|$)", path) if WINDOWS else None
    return f"{m.group(1).upper()}:/{path[3:]}" if m else path


# ---- buttons ----------------------------------------------------------------

def buttons_path() -> Path:
    return DATA / "buttons.json"


def load_buttons() -> list[dict]:
    try:
        # Keys of a retired type (e.g. "orchid") are ignored.
        return [b for b in json.loads(buttons_path().read_text()) if b.get("type") in TYPES]
    except FileNotFoundError:
        return []


def save_buttons(buttons: list[dict]) -> None:
    p = buttons_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(buttons, indent=2) + "\n")
    tmp.replace(p)  # atomic: a crash never leaves a half-written file


def clean_button(raw: dict) -> dict:
    """Validate user input into a stored button. Raises ValueError."""
    label = str(raw.get("label", "")).strip()
    kind = raw.get("type")
    if not label:
        raise ValueError("label required")
    if kind not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    text = str(raw.get("text", "")).strip()
    if kind == "shell" and not text:
        raise ValueError("shell button needs a command")
    return {
        "id": raw.get("id") or uuid.uuid4().hex[:8],
        "label": label,
        "type": kind,
        "cwd": str(raw.get("cwd", "")).strip(),
        "text": text,
        "yolo": kind != "shell" and str(raw.get("yolo", False)).lower() in ("1", "true", "on"),
        "host": str(raw.get("host", "")).strip(),  # run on this Deck host (its /peer proxy); "" = the host serving the pad
    }


def asks(b: dict) -> bool:
    """A script key that takes what the user types when it runs: its command uses "$1"."""
    return b.get("type") == "shell" and bool(re.search(r"\$(1|\{1\}|@)", b.get("text", "")))


def run_arg(body: dict) -> dict:
    """The one-off argument of a run (an asking script key's input), or nothing."""
    arg = str(body.get("arg", "")).strip()
    return {"arg": arg} if arg else {}


def upsert_button(raw: dict) -> dict:
    b = clean_button(raw)
    buttons = load_buttons()
    idx = next((i for i, x in enumerate(buttons) if x["id"] == b["id"]), None)
    if idx is None:
        buttons.append(b)
    else:
        buttons[idx] = b
    save_buttons(buttons)
    return b


def delete_button(button_id: str) -> None:
    save_buttons([b for b in load_buttons() if b["id"] != button_id])


# ---- runs -------------------------------------------------------------------

DECK_CONTEXT = """You are running inside Deck, the user's control panel for agents, on host {host}. A message the user sends from Deck's chat starts with [when · device, how], how being phone app, phone browser or PC browser. When it says phone, or there is no such stamp (Deck's terminal view adds none):
- Keep replies short and scannable. No wide tables or long code dumps unless asked.
- Ask at most one question at a time. Numbered options and y/n prompts appear as tap buttons.
- Give links as plain URLs; no QR codes (they are already on the phone).
Always:
- If you are stuck (blocked, unsure, or repeating attempts that fail), stop and ask the user instead of guessing or looping.
- The user's shelf (chat they saved, in folders): `curl -s http://127.0.0.1:{port}/api/shelf/agent` lists it (`?id=ID`: one item, `?all=1`: everything), if they allow agents to read it. Read it when they mention the shelf or a saved item.
- Pin a web page you made or found (an artifact, docs, a dashboard) to the shelf: `curl -s http://127.0.0.1:{port}/api/shelf/pin -H 'Content-Type: application/json' -d '{{"url": "...", "title": "...", "note": "one line"}}'`. The same url again updates its item. Keep one artifact page per project and add to it (same url) instead of making a new artifact for each thing. Make it the project's main page, shown in a bar atop the chat of every session in this project (its git repo), by adding `"main": true, "session": "'"$DECK_SESSION_NAME"'", "folder": "<the repo's path>"` to the pin.
- Put a file the user wants to read or edit in Deck (a paper's source, its PDF) on the shelf as the file itself, not a copy: `curl -s http://127.0.0.1:{port}/api/shelf/link -H 'Content-Type: application/json' -d '{{"path": "<full path>", "title": "...", "session": "'"$DECK_SESSION_NAME"'"}}'`. Their edits in the reader write the file; your edits (and rebuilds) show in the reader within seconds.
- When you start working in a project's repo, outside the folder you started in, tell Deck (its main-page bar follows): `curl -s -X PUT http://127.0.0.1:{port}/api/sessions/$DECK_SESSION_NAME/project -H 'Content-Type: application/json' -d '{{"folder": "<the repo's path>"}}'`.
- The user's other sessions, on this host and their other hosts, if the user allows it: `curl -s http://127.0.0.1:{port}/api/agent/sessions` lists them, `/api/agent/sessions/NAME` reads one's recent output (another host's: `/peer/HOST/api/agent/sessions/NAME`), and `curl -s http://127.0.0.1:{port}/api/agent/sessions/NAME/send -H 'Content-Type: application/json' -d '{{"text": "...", "from": "{host}/'"$DECK_SESSION_NAME"'"}}'` messages it (same /peer/HOST prefix for another host). Message another session only for a clear handoff or when the user asks; answer an agent's message only if it asks you something.
- Editing a Google Doc the user names a part of: read it first; replaceAllText only text that appears once in it, else delete and insert at that one spot's indexes (with the read's revision id).{notes}"""
# Only when the machine notes repo has agent instructions (DECK_NOTES/AGENTS.md).
NOTES_LINE = "\n- Machine facts and admin notes live in {path}: follow its AGENTS.md and record any admin change there."


# The notes files every agent follows, given in full at its start: told only to read them, agents skipped it whenever
# the first message was a quick question (user)
NOTES_FILES = ("PREFERENCES.md", "PROFILE.md", "RESOURCES.md")


def deck_context() -> str:
    notes = ""
    if (NOTES / "AGENTS.md").exists():
        notes = NOTES_LINE.format(path=str(NOTES).replace(str(HOME), "~", 1))
        for name in NOTES_FILES:
            try:
                text = (NOTES / name).read_text(encoding="utf-8").strip()
                notes += f"\n\n# {name} (from {NOTES.name}, as of this session's start; follow it)\n\n{text}"
            except OSError:
                pass
    return DECK_CONTEXT.format(host=HOST_NAME, notes=notes, port=os.environ.get("DECK_PORT", "8787"))


def pi_available() -> bool:
    """The Pi key shows only where pi is installed (Deck's setup installs it)."""
    return bool(shutil.which("pi")) or any((HOME / p).exists() for p in (".local/bin/pi", ".pi/agent/bin/pi", ".pi/agent/bin/pi.cmd"))


def agy_available() -> bool:
    """The agy (Google Antigravity CLI) key shows only where agy is installed."""
    return bool(agy_path())


def agy_path() -> str:
    """Also find Google's default install before a running service inherits the updated PATH."""
    if path := shutil.which("agy"):
        return path
    path = (Path(os.environ.get("LOCALAPPDATA", HOME / "AppData" / "Local")) / "agy" / "bin" / "agy.exe"
            if WINDOWS else HOME / ".local" / "bin" / "agy")
    return str(path) if path.is_file() else ""


def openclaw_available() -> bool:
    """The OpenClaw key shows only where openclaw is installed (its TUI needs a running OpenClaw Gateway)."""
    return bool(shutil.which("openclaw"))


def agents_installed() -> list[str]:
    """The agent types set up on this host, in picker order: + and Default agent offer only these (user)."""
    found = {"claude": bool(shutil.which("claude")) or (HOME / ".local/bin/claude").exists(),
             "codex": bool(shutil.which("codex")) or (HOME / ".local/bin/codex").exists(),
             "opencode": bool(shutil.which("opencode")) or any((HOME / p).exists() for p in (".local/bin/opencode", ".opencode/bin/opencode")),
             "pi": pi_available(), "agy": agy_available(), "openclaw": openclaw_available()}
    return [a for a in AGENT_ORDER if found[a]]


def pi_durable_available() -> bool:
    """Settings → Durable Pi shows only where Deck's setup installed pi-durable (deploy/agents/pi-durable)."""
    return bool(shutil.which("pi-durable")) or (HOME / ".local/bin/pi-durable").exists()


def agy_context() -> Path:
    """agy has no system-prompt flag, but loads AGENTS.md and .agents/rules/*.md from any `--add-dir` folder: write the
    Deck note there and, when the machine notes repo has agent instructions, copy its instruction files in."""
    d = DATA / "agy-context"
    rules = d / ".agents" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (d / "AGENTS.md").write_text(deck_context() + "\n\n" +
        "Deck question interface: the native ask_question tool is skipped in headless mode. When you need the "
        "user to choose or clarify, instead reply with <deck-question> followed by a JSON object with keys "
        "question (a string) and options (an array of short strings), then </deck-question>. Deck renders "
        "a question card and sends the answer as the next user message. Ask one question at a time.\n",
        encoding="utf-8")
    for f in rules.glob("*.md"):
        f.unlink(missing_ok=True)
    if (NOTES / "AGENTS.md").exists():
        for name in ("AGENTS.md", "PREFERENCES.md", "PROFILE.md", "RESOURCES.md"):
            src = NOTES / name
            if src.exists():
                (rules / f"infra-{name}").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return d


def context_args(kind: str) -> list[str]:
    """Tell Claude/Codex/agy they are in a Deck session (env vars alone are invisible to them)."""
    text = deck_context()
    if kind in ("claude", "pi"):  # as a file: the note (with infra's preferences) nearly filled tmux's 16 KB command limit
        note = DATA / "agent-context.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(text + "\n", encoding="utf-8")
        return ["--append-system-prompt-file" if kind == "claude" else "--append-system-prompt", note.as_posix()]
    if kind == "codex":
        return ["-c", "developer_instructions=" + json.dumps(text)]  # a JSON string is a valid TOML string
    if kind == "agy":
        return ["--add-dir", str(agy_context())]
    return []


def env_prefix(kind: str) -> list[str]:
    """OpenCode has no system-prompt flag: point its `instructions` at a file holding the Deck note (and give it the
    agent browser, where this host has one)."""
    if kind != "opencode":
        return []
    note = DATA / "opencode-context.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(deck_context() + "\n")
    config = {"instructions": [note.as_posix()]}
    # The agent browser (deploy/agents/agent-browser.sh registers it for Claude and Codex): OpenCode gets it here
    if (HOME / ".config" / "playwright-mcp.json").exists():
        config["mcp"] = {"playwright": {"type": "remote", "url": "http://127.0.0.1:8931/mcp"}}
    # Settings → OpenCode sampling: its plugin reads them from the settings file at each request, and skips the models
    # that take none (the skip list, which the chat bridge keeps)
    config["plugin"] = [SAMPLING_PLUGIN.as_uri()]
    # Forward slashes: the MSYS env/bash in front of it unescape one level of backslashes, leaving invalid JSON
    # ("C:\Users") that makes OpenCode answer every request with a 400 ConfigJsonError.
    return ["env", f"DECK_SAMPLING={settings_path().as_posix()}", f"DECK_SAMPLING_SKIP={(DATA / 'opencode-sampling-skip.json').as_posix()}",
            "OPENCODE_CONFIG_CONTENT=" + json.dumps(config)]


SAMPLING_PLUGIN = Path(__file__).resolve().parent.parent / "deploy" / "agents" / "opencode-sampling.js"
PI_NAME = Path(__file__).resolve().parent.parent / "deploy" / "agents" / "pi-name.ts"  # Pi extension: session name from the first message
# Chat view (Settings): these agents run under deck/chat_bridge.py in their machine-readable mode, and the page shows
# a chat instead of a terminal. The bridge keeps its event log and command queue in CHAT_DIR (server.terminal_chat).
CHAT_AGENTS = ("claude", "codex", "opencode", "pi", "agy", "openclaw")
AGENT_ORDER = CHAT_AGENTS  # + picker order
CHAT_BRIDGE = Path(__file__).resolve().parent / "chat_bridge.py"
CHAT_DIR = DATA / "chat"


def build_argv(b: dict) -> list[str]:
    """Command for the tmux pane. Login bash so ~/.local/bin is on PATH.
    User text is passed as an argument ("$@"), never spliced into the script."""
    kind, text = b["type"], b["text"]
    if kind == "shell":  # an asking key's input is "$1", an argument (never spliced into the script)
        return [BASH, "-lc", text, *(["deck", b["arg"]] if b.get("arg") else [])]
    yolo = YOLO_FLAGS.get(kind, []) if b.get("yolo") else []
    yolo = [*yolo, *model_args(kind), *effort_args(kind)]  # Settings defaults; a chat's own /model, /effort pick overrides them
    if b.get("chat"):  # the agent's own arguments go after --; its adapter turns the YOLO flag into its own settings
        if kind == "codex":  # send the full context over app-server's protocol, beyond tmux's command limit
            note = DATA / "codex-context.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(deck_context() + "\n", encoding="utf-8")
            context = ["--deck-context", note.as_posix()]
        else:
            context = context_args(kind)
        args = [*context, *(["-e", str(PI_NAME)] if kind == "pi" else []), *yolo, *b.get("extra", [])]
        return [*env_prefix(kind), BASH, "-lc", 'exec "$@"', "deck", tmux_path(sys.executable), str(CHAT_BRIDGE), kind,
                str(CHAT_DIR), b["session"], "1" if b.get("resume") else "0", text, "--", *args]
    cmd = "pi-durable" if kind == "pi" and b.get("durable") else kind  # same arguments as pi
    name = ["-e", str(PI_NAME)] if cmd == "pi" else []  # names the session, so the tab shows a name
    args = [*context_args(kind), *name, *yolo, *b.get("extra", [])]
    if kind == "openclaw":  # its TUI on the Gateway's one ongoing conversation ("main"): every tab and Restart continue it
        return [BASH, "-lc", 'exec openclaw tui "$@"', "deck", "--session", "main", *(["--message", text] if text else [])]
    if b.get("resume"):
        # Restart: continue the folder's most recent conversation; if there is none (it errors), start a fresh one.
        resume = {"claude": "--continue", "codex": "resume --last", "opencode": "--continue", "pi": "--continue", "agy": "--continue"}[kind]
        return [*env_prefix(kind), BASH, "-lc", f'{cmd} {resume} "$@" || exec {cmd} "$@"', "deck", *args]
    if text:  # OpenCode's positional is a project folder; its first message goes in --prompt (agy: -i, interactive)
        args += {"opencode": ["--prompt", text], "agy": ["-i", text]}.get(kind, [text])
    return [*env_prefix(kind), BASH, "-lc", f'exec {cmd} "$@"', "deck", *args]


def effort_args(kind: str) -> list[str]:
    """The agent's flag for its Settings default effort, or nothing (the agent's own default)."""
    level = (load_settings().get("effort") or {}).get(kind)
    return EFFORT_FLAG[kind](level) if level in EFFORTS.get(kind, ()) else []


def model_args(kind: str) -> list[str]:
    """The agent's flag for its Settings default model, or nothing (the agent's own default)."""
    model = (load_settings().get("model") or {}).get(kind)
    return MODEL_FLAG[kind](model) if model in MODELS.get(kind, ()) else []


def resolve_cwd(b: dict) -> str:
    default = str(HOME) if b["type"] == "shell" else default_folder()
    return os.path.expanduser(b["cwd"] or default)


# ---- folders ----------------------------------------------------------------

def recent_path() -> Path:
    return DATA / "recent.json"


def remember_folder(cwd: str) -> None:
    try:
        recent = json.loads(recent_path().read_text())
    except (FileNotFoundError, ValueError):
        recent = []
    recent = [cwd] + [r for r in recent if r != cwd]
    recent_path().parent.mkdir(parents=True, exist_ok=True)
    recent_path().write_text(json.dumps(recent[:8]))


def folders() -> dict:
    """Folder chips for the ask sheet: default, recently used, and projects under WORK."""
    try:
        recent = json.loads(recent_path().read_text())
    except (FileNotFoundError, ValueError):
        recent = []
    try:
        projects = sorted(e.path for e in os.scandir(WORK) if e.is_dir() and not e.name.startswith("."))
    except FileNotFoundError:
        projects = []
    tilde = lambda p: p.replace(str(HOME), "~", 1) if p.startswith(str(HOME)) else p
    return {"default": tilde(default_folder()), "recent": [tilde(r) for r in recent if os.path.isdir(os.path.expanduser(r))],
            "projects": [tilde(p) for p in projects]}


# ---- hosts -------------------------------------------------------------------

# Power-LED colour per host: inventory `led` wins; others take the next unused palette colour.
LED_PALETTE = ["#ff4f12", "#31d158", "#3d8bff", "#b26bff", "#ffc21a", "#22c7b8", "#ff5c8a"]


# Settings sheet choices and their defaults (a missing key means the default).
CHOICES = {"agent": ("claude", "codex", "opencode", "pi", "agy", "openclaw"),
           "start": ("term", "deck", "agents"), "theme": ("system", "light", "dark", "paper"), "shelfnew": ("bottom", "top")}
# termw: the terminal's kept width (app.js applyLayout); b*: the widths before Fullscreen, for Minimize
LAYOUT_WIDTHS = ("padw", "feedw", "padcw", "feedcw", "termw", "bpadcw", "bfeedcw", "btermw")
LAYOUT_FLAGS = ("nopad", "nofeed", "nobottom", "full", "msgshut")
QUICK_KEYS = ("harness", "esc", "tab", "stab", "ctrlc", "up", "down", "left", "right", "enter")
DEFAULTS = {"agent": "claude", "agentpick": True, "folder": "", "yolo": False, "hosting": True, "debug": False, "resize": True, "start": "term",
            "theme": "system", "font": 13, "pcfont": 13, "crop": True, "composer": True, "textterm": WINDOWS, "pidurable": False,
            "chat": False, "shelfnew": "bottom", "shelfagents": False, "sessionagents": False, "describe": False, "describer": "claude", "chattools": True, "thinkopen": False, "notesopen": False, "chatjump": True,
            "readernote": True, "brain": ""}
# textterm: Windows hosts default to the text terminal; a live client there goes through ConPTY, which redraws the
# screen itself and garbles it.


def settings() -> dict:
    """All settings with defaults filled in."""
    return {**DEFAULTS, "alias": "", "led": "", "pad": [], "hidekeys": [], "hostnames": {}, **load_settings()}


def default_folder() -> str:
    return os.path.expanduser(load_settings().get("folder") or WORK)


def settings_path() -> Path:
    return DATA / "settings.json"


def load_settings() -> dict:
    """This Deck's settings (Settings sheet): alias = the whole Deck's name, shown as the title; LED colour. Override the inventory."""
    try:
        return json.loads(settings_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def clean_layout(raw):
    """Desktop columns: widths in px, and which are collapsed."""
    if not isinstance(raw, dict):
        raise ValueError("layout must be an object")
    try:
        return {k: bool(v) if k in LAYOUT_FLAGS else max(0, min(2000, int(v)))
                for k, v in raw.items() if k in LAYOUT_FLAGS + LAYOUT_WIDTHS and v}
    except (TypeError, ValueError):
        raise ValueError("layout widths must be numbers") from None


def save_settings(raw: dict) -> dict:
    """Update only the fields given; an empty alias/led/pad clears that field."""
    s = load_settings()
    if "alias" in raw:
        s["alias"] = str(raw["alias"]).strip()[:24]
    if "hostnames" in raw:  # {host: {alias, led}}: any host's name and colour, set from any host; shared by the Deck (user)
        if not isinstance(raw["hostnames"], dict):
            raise ValueError("hostnames must be an object of host: {alias, led}")
        names = dict(s.get("hostnames") or {})
        for host, v in raw["hostnames"].items():
            v = v if isinstance(v, dict) else {}
            entry = {"alias": str(v.get("alias", "")).strip()[:24], "led": str(v.get("led", "")).strip().lower()}
            if entry["led"] and not re.fullmatch(r"#[0-9a-f]{6}", entry["led"]):
                raise ValueError("colour must look like #ff4f12")
            entry = {k: x for k, x in entry.items() if x}
            if entry:
                names[str(host)[:64]] = entry
            else:
                names.pop(str(host), None)
        s["hostnames"] = names
    if "led" in raw:
        s["led"] = str(raw["led"]).strip().lower()
        if s["led"] and not re.fullmatch(r"#[0-9a-f]{6}", s["led"]):
            raise ValueError("colour must look like #ff4f12")
    if "pad" in raw:  # key order on the Pad: built-in ids and saved key ids
        if not isinstance(raw["pad"], list):
            raise ValueError("pad must be a list of key ids")
        s["pad"] = [str(x)[:40] for x in raw["pad"]][:200]
    for k, allowed in CHOICES.items():
        if k in raw:
            if raw[k] not in allowed:
                raise ValueError(f"{k} must be one of {allowed}")
            s[k] = raw[k]
    if "folder" in raw:
        s["folder"] = str(raw["folder"]).strip()[:200]
        if s["folder"] and not os.path.isdir(os.path.expanduser(s["folder"])):
            raise ValueError(f"folder not found: {s['folder']}")
    if "brain" in raw:  # the Brain's data (app.js Brain): a project, or a folder in one, kept as its project name
        v = " ".join(str(raw["brain"]).split())[:300]
        if v and os.path.isdir(os.path.expanduser(v)):
            v = project_of(os.path.expanduser(v))
            if not v:
                raise ValueError(f"{raw['brain']} is in no project (a git repo, or a folder under home)")
        elif v.startswith(("/", "~", ".")):
            raise ValueError(f"folder not found: {v}")
        s["brain"] = v
    if "yolo" in raw:
        s["yolo"] = bool(raw["yolo"])
    if "debug" in raw:  # chats show what Deck adds and hides (its instructions, stamps, reminders, hook output)
        s["debug"] = bool(raw["debug"])
    if "hosting" in raw:  # off: a client-only PC (no sessions of its own; new ones start on a linked PC)
        s["hosting"] = bool(raw["hosting"])
    if "order" in raw:  # the nodes, highest first: the first one up serves the site and writes its state (deck/site.py)
        if not isinstance(raw["order"], list) or not all(isinstance(x, str) for x in raw["order"]):
            raise ValueError("order must be a list of host names")
        s["order"] = [x for x in dict.fromkeys(raw["order"]) if x][:16]
    if "effort" in raw:  # {agent: level}: merged into the saved ones; "" clears that agent's (its own default)
        if not isinstance(raw["effort"], dict):
            raise ValueError("effort must be an object of agent: level")
        eff = dict(s.get("effort") or {})
        for kind, level in raw["effort"].items():
            if kind not in EFFORTS or level not in ("", *EFFORTS[kind]):
                raise ValueError(f"{kind} effort must be one of {EFFORTS.get(kind, ())}")
            if level:
                eff[kind] = level
            else:
                eff.pop(kind, None)
        s["effort"] = eff
    if "model" in raw:  # {agent: alias}: merged into the saved ones; "" clears that agent's (its own default)
        if not isinstance(raw["model"], dict):
            raise ValueError("model must be an object of agent: model")
        models = dict(s.get("model") or {})
        for kind, model in raw["model"].items():
            if kind not in MODELS or model not in ("", *MODELS[kind]):
                raise ValueError(f"{kind} model must be one of {MODELS.get(kind, ())}")
            if model:
                models[kind] = model
            else:
                models.pop(kind, None)
        s["model"] = models
    if "openrouter" in raw:  # shared provider routing for every Deck OpenCode request to OpenRouter
        routing = raw["openrouter"]
        if not isinstance(routing, dict):
            raise ValueError("openrouter must be an object of provider routing preferences")
        for key, value in routing.items():
            if key == "sort" and value in ("price", "latency", "throughput"):
                continue
            if key == "data_collection" and value in ("allow", "deny"):
                continue
            if key == "zdr" and isinstance(value, bool):
                continue
            if key in ("preferred_min_throughput", "preferred_max_latency"):
                values = value if isinstance(value, dict) else {"p50": value}
                if values and all(k in ("p50", "p75", "p90", "p99") and isinstance(v, (int, float))
                                  and not isinstance(v, bool) and 0 < v < float("inf") for k, v in values.items()):
                    continue
            raise ValueError(f"invalid OpenRouter routing preference: {key}")
        s["openrouter"] = routing
    if "sampling" in raw:  # {agent: {param: number}} for OpenCode's agents, merged; "" or None clears that param
        if not isinstance(raw["sampling"], dict) or not all(isinstance(v, dict) for v in raw["sampling"].values()):
            raise ValueError("sampling must be an object of agent: {param: number}")
        samp = {a: dict(v) for a, v in (s.get("sampling") or {}).items()}
        for agent, params in raw["sampling"].items():
            if agent not in SAMPLING_AGENTS:
                raise ValueError(f"sampling agent must be one of {SAMPLING_AGENTS}")
            for k, v in params.items():
                if k not in SAMPLING:
                    raise ValueError(f"sampling setting must be one of {tuple(SAMPLING)}")
                if v in ("", None):
                    samp.get(agent, {}).pop(k, None)
                    continue
                lo, hi = SAMPLING[k]
                try:
                    v = int(v) if k == "top_k" else float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"{k} must be a number") from None
                if not lo <= v <= hi:
                    raise ValueError(f"{k} must be from {lo} to {hi}")
                samp.setdefault(agent, {})[k] = v
        s["sampling"] = {a: v for a, v in samp.items() if v}
    if "pidurable" in raw:  # Pi sessions run pi-durable (deploy/agents/pi-durable) where it's installed
        s["pidurable"] = bool(raw["pidurable"])
    if "chatjump" in raw:  # back in a chat left at its bottom: on, at the newest reply; off, where it was left
        s["chatjump"] = bool(raw["chatjump"])
    if "readernote" in raw:  # messages name the documents open in the reader beside the chat; off: never
        s["readernote"] = bool(raw["readernote"])
    if "chattools" in raw:  # chats show the agent's tool calls (commands, edits); off: only the conversation
        s["chattools"] = bool(raw["chattools"])
    if "thinkopen" in raw:  # chats show the agent's thinking expanded; off: folded under "Thinking"
        s["thinkopen"] = bool(raw["thinkopen"])
    if "notesopen" in raw:  # chats show edits to the machine notes repo (DECK_NOTES) opened; off: folded like the rest
        s["notesopen"] = bool(raw["notesopen"])
    if "shelfagents" in raw:  # agents may read the shelf (GET /api/shelf/agent; the shelf's own checkbox)
        s["shelfagents"] = bool(raw["shelfagents"])
    if "sessionagents" in raw:  # agents may list, read and message the other sessions (/api/agent/sessions)
        s["sessionagents"] = bool(raw["sessionagents"])
    if "describe" in raw:  # a small model keeps a description of each agent session (deck/describe.py)
        s["describe"] = bool(raw["describe"])
    if "describer" in raw:  # which: claude (Haiku) or codex (OpenAI's small model)
        if raw["describer"] not in ("claude", "codex"):
            raise ValueError("describer must be claude or codex")
        s["describer"] = raw["describer"]
    if "chat" in raw:  # agents in CHAT_AGENTS start as chat sessions (deck/chat_bridge.py)
        s["chat"] = bool(raw["chat"])
    if "resize" in raw:  # desktop: drag the terminal's edges to resize the side columns
        s["resize"] = bool(raw["resize"])
    if "crop" in raw:  # agent tabs: hide the agent's own footer, show its status as chips
        s["crop"] = bool(raw["crop"])
    if "composer" in raw:  # Terminal message box; off = type straight into the terminal
        s["composer"] = bool(raw["composer"])
    # Page state shared by every browser and the Deck app on this host, so each opens where the last one left off.
    if "tabfolders" in raw:  # folders in the tab list: [{id, title, open, tabs: session names}] (app.js renderChips)
        if not isinstance(raw["tabfolders"], list):
            raise ValueError("tabfolders must be a list")
        s["tabfolders"] = [{"id": str(f["id"])[:24], "title": " ".join(str(f.get("title") or "Folder").split())[:40] or "Folder",
                            "open": bool(f.get("open", True)), "tabs": [str(t)[:120] for t in f.get("tabs") or []][:200]}
                           for f in raw["tabfolders"][:50] if isinstance(f, dict) and f.get("id")]
    if "scriptshut" in raw:  # the tab column's scripts half folded away (app.js applyScriptShut)
        s["scriptshut"] = bool(raw["scriptshut"])
    if "agentpick" in raw:  # + asks which agent (on), or starts Default agent `agent` straight away (off; user)
        s["agentpick"] = bool(raw["agentpick"])
    if "scripts" in raw:  # the Scripts bar shows when on, or when a script exists (app.js applyScriptShut; user)
        s["scripts"] = bool(raw["scripts"])
    if "hostshut" in raw:  # hosts whose sessions the tab column hides under their header (app.js toggleHost)
        if not isinstance(raw["hostshut"], list):
            raise ValueError("hostshut must be a list of host names")
        s["hostshut"] = [str(x)[:80] for x in raw["hostshut"]][:50]
    if "tabs" in raw:  # Terminal tab order (session names)
        if not isinstance(raw["tabs"], list):
            raise ValueError("tabs must be a list of session names")
        s["tabs"] = [str(x)[:80] for x in raw["tabs"]][:200]
    for k in ("session", "pcsession"):  # the session the Terminal shows: phones, wide screens (PC app window, browsers)
        if k in raw:
            s[k] = str(raw[k])[:80]
    if "textterm" in raw:  # experimental text terminal (server.terminal_text)
        s["textterm"] = bool(raw["textterm"])
    if "layout" in raw:  # desktop columns: widths in px, and which are collapsed
        s["layout"] = clean_layout(raw["layout"])
    if "layouts" in raw:  # saved layouts, each a header button that restores it (app.js renderLayouts)
        if not isinstance(raw["layouts"], list):
            raise ValueError("layouts must be a list")
        s["layouts"] = [{"id": str(x["id"])[:24], "name": " ".join(str(x.get("name") or "Layout").split())[:40] or "Layout",
                         "layout": clean_layout(x.get("layout") or {})}
                        for x in raw["layouts"][:12] if isinstance(x, dict) and x.get("id")]
    if "hidekeys" in raw:  # Terminal quick keys the user turned off
        if not isinstance(raw["hidekeys"], list) or not set(raw["hidekeys"]) <= set(QUICK_KEYS):
            raise ValueError(f"hidekeys must be a list from {QUICK_KEYS}")
        s["hidekeys"] = [k for k in QUICK_KEYS if k in raw["hidekeys"]]
    for k in ("font", "pcfont"):  # terminal font size: phones, wide screens (PC app window, browsers)
        if k in raw:
            try:
                s[k] = max(10, min(36, int(raw[k])))
            except (TypeError, ValueError):
                raise ValueError("font must be a number") from None
    # Drop cleared and default values so the file only holds what was changed.
    # Keep an explicitly cleared routing object so settings sync also clears it on the other hosts.
    s = {k: v for k, v in s.items() if k == "openrouter" or
         (v not in ("", [], {}, None) and DEFAULTS.get(k, object()) != v)}
    settings_path().parent.mkdir(parents=True, exist_ok=True)
    settings_path().write_text(json.dumps(s, indent=2) + "\n")
    return s


def hosts() -> dict:
    """Hosts that run Deck, from the machine notes inventory (inventory.toml entries with deck_url)."""
    try:
        with open(INVENTORY, "rb") as f:
            inv = tomllib.load(f).get("hosts", {})
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        inv = {}
    decks = {n: h for n, h in inv.items() if h.get("deck_url")}
    # Linked PCs (links.py) reach each other through local iroh forwarders; the inventory's address wins for a host in
    # both. A member whose forwarder isn't up yet is left out (callers use its url).
    from . import links
    for m in links.members():
        if m["name"] in decks or not (m["name"] == HOST_NAME or m in links.others()):
            continue
        url = "" if m["name"] == HOST_NAME else links.peer_url(m)
        if url is not None:
            decks[m["name"]] = {"deck_url": url, "linked": True, "iroh": m.get("iroh", "")}
    taken = {h["led"].lower() for h in decks.values() if h.get("led")}
    spare = iter([c for c in LED_PALETTE if c not in taken] + LED_PALETTE)
    out = [{"name": n, "aka": h.get("aka", ""), "url": h["deck_url"], "current": n == HOST_NAME,
            "led": h.get("led") or next(spare), **({"linked": True} if h.get("linked") else {})} for n, h in decks.items()]
    mine = load_settings()
    for x in out:
        if x["current"]:
            x["led"] = mine.get("led") or x["led"]
            # its iroh key: the phone app reaches this host with no Tailscale (app/www: the Deck's hosts by key)
            if links.iroh_id():
                x["iroh"] = links.iroh_id()
    for x in out:  # names and colours set in Settings → Hosts win over the inventory's (user)
        set_ = (mine.get("hostnames") or {}).get(x["name"], {})
        x["aka"] = set_.get("alias") or x["aka"]
        x["led"] = set_.get("led") or x["led"]
    led = next((x["led"] for x in out if x["current"]), None) or mine.get("led") or LED_PALETTE[0]
    return {"current": HOST_NAME, "alias": mine.get("alias", ""), "led": led, "palette": LED_PALETTE,
            "pad": mine.get("pad", []), "hosts": out}


# ---- screen -------------------------------------------------------------------

def screen() -> dict:
    """Screen tab config. The VNC server is loopback-only; the bridge is served on this origin at `path`."""
    try:
        cfg = json.loads(Path(VNC_FILE).read_text())
    except (FileNotFoundError, ValueError):
        return {"enabled": False}
    # bridge: websockify's local address ("127.0.0.1:6080"); Deck then serves it at /vnc/ itself (server.vnc_bridge),
    # so the tab works over iroh too. Without it the bridge lives in `tailscale serve`, which the tunnel can't reach.
    return {"enabled": True, "path": cfg.get("path", "/vnc/websockify"), "password": cfg.get("password", ""),
            "bridge": bool(cfg.get("bridge"))}


def vnc_bridge() -> str:
    """websockify's local address from vnc.json, or "" (none: the Screen tab isn't served over iroh)."""
    try:
        return str(json.loads(Path(VNC_FILE).read_text()).get("bridge", ""))
    except (FileNotFoundError, ValueError):
        return ""


SCHTASKS = "/mnt/c/Windows/System32/schtasks.exe"


def vnc_service(state: str) -> bool:
    """Start ("on") or stop ("off") the VNC server through the admin tasks "Deck VNC on/off" (win/vnc-setup.ps1), so
    it runs only while a page streams (user: "turn off the vnc when i am not streaming screen"). False: no such task."""
    exe = os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "System32", "schtasks.exe") if WINDOWS else SCHTASKS
    if not os.path.exists(exe):
        return False
    r = subprocess.run([exe, "/run", "/tn", f"Deck VNC {state}"], capture_output=True, text=True, timeout=15, **OWN_CONSOLE)
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
    if WINDOWS:  # Deck on Windows itself: the same PowerShell 5.1, by its Windows path
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
    r = subprocess.run(args, capture_output=True, text=True, timeout=30, **OWN_CONSOLE)
    out = r.stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except (IndexError, ValueError):
        if action == "status":
            return None
        raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[0] if (r.stderr or r.stdout).strip() else "phone screen failed")


# ---- key builder ------------------------------------------------------------

BUILDER_PROMPT = """You are the Deck key builder. Read ./AGENTS.md first; it explains how to write the script and register the key.
The user wants this key:

{request}"""


def builder_button(request: str, agent: str = "claude") -> dict:
    request = request.strip()
    if not request:
        raise ValueError("describe the key you want")
    if agent not in AGENTS:
        raise ValueError("agent must be claude, codex, opencode, pi, agy or openclaw")
    b = clean_button({"id": "builder", "label": f"Key builder ({agent})", "type": agent, "cwd": KEYS_DIR,
                      "text": BUILDER_PROMPT.format(request=request)})
    if agent == "codex":  # deck-key writes buttons.json, outside Codex's workspace sandbox
        b["extra"] = ["-c", f"sandbox_workspace_write.writable_roots=[{json.dumps(DATA.as_posix())}]"]  # TOML: no backslashes
    return b


def new_agent_button(agent: str, text: str = "", cwd: str = "", yolo: bool = False) -> dict:
    """A one-off agent session from the pad's New key; blank text = empty interactive session."""
    label = {"claude": "Claude", "codex": "Codex", "opencode": "OpenCode", "pi": "Pi", "agy": "agy", "openclaw": "OpenClaw"}.get(agent)
    if not label:
        raise ValueError("agent must be claude, codex, opencode, pi, agy or openclaw")
    return clean_button({"id": "new", "label": label, "type": agent, "cwd": cwd, "text": text, "yolo": yolo})


def session_name(b: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", b["label"].lower()).strip("-")[:24] or b["id"]
    base = f"{PREFIX}{slug}-{time.strftime('%H%M%S')}"
    name, n = base, 2
    while session_exists(name):  # two starts of the same key within one second
        name, n = f"{base}-{n}", n + 1
    return name


def msys_command(argv: list[str]) -> str:
    """Windows command line for an MSYS program. MSYS splits it with its own rules (Windows' \\" escapes don't
    apply; ' and " both quote), so every argument goes in single quotes, with a ' itself as "'"."""
    def arg(a: str) -> str:
        return "".join("\"'\"" if p == "'" else f"'{p}'" for p in re.findall(r"'|[^']+", a)) or "''"
    return " ".join([f'"{argv[0]}"', *map(arg, argv[1:])])


def session_env(argv: list[str], name: str = "") -> list[str]:
    """tmux -e flags for a pane running argv. Windows: MSYS bash rewrites /-looking arguments (a
    path in the first message) into Windows paths when it starts claude.exe; excluded here by their exact text,
    not with "*", which the agents' own Git Bash would inherit."""
    env = ["-e", "DECK_SESSION=1"]  # agents: they run inside Deck
    if name:  # who an agent is when it messages another session (/api/agent/sessions)
        env += ["-e", f"DECK_SESSION_NAME={name}"]
    keep = [a for a in argv if "/" in a and ";" not in a] if WINDOWS else []
    return env + (["-e", "MSYS2_ARG_CONV_EXCL=" + ";".join(keep)] if keep else [])


# Windows: tmux gets its own (hidden) console. Sharing Deck's would let a Ctrl+C sent in a pane stop Deck itself.
OWN_CONSOLE = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}


def tmux(*args: str) -> subprocess.CompletedProcess:
    argv = [TMUX, *args]
    try:
        return subprocess.run(msys_command(argv) if WINDOWS else argv, capture_output=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=10, **OWN_CONSOLE)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([TMUX, *args], 124, "", "tmux timed out")


# Modified keys (Shift+Enter) reach programs that ask for them (Pi warns without it). Server-wide; set with each new
# session so an already running server gets it too.
EXTENDED_KEYS = (";", "set-option", "-s", "extended-keys", "on")


def start_tmux() -> None:
    """Windows: start the tmux server up front, with no pipes. A server forked by a later call would keep that call's
    output pipes open, and the call would never return. It stays up with no sessions, as on Linux it would restart."""
    if WINDOWS:
        # MSYS=noglob is for this side only (Windows -> tmux.exe): panes don't get it.
        # PATH too: the tmux server outlives Deck restarts and would keep handing panes its first, stale PATH.
        subprocess.run([TMUX, "start-server", ";", "set-option", "-g", "exit-empty", "off",
                        ";", "set-environment", "-g", "-u", "MSYS",
                        ";", "set-environment", "-g", "PATH", os.environ["PATH"]], timeout=10, **OWN_CONSOLE,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
            CHAT_DIR.mkdir(parents=True, exist_ok=True)
            (CHAT_DIR / f"{name}.state").write_text(json.dumps({"state": "working", **state}), encoding="utf-8")
            b = {**b, "resume": True}
    # Chained in one tmux call so remain-on-exit is set before a fast script can exit.
    argv = build_argv(b)
    # A script's output also goes to a log (run_log): the popup shows it, and a pane's screen doesn't keep it
    # everywhere (Windows' console clears it when the script exits)
    log = [";", "pipe-pane", "-O", "-t", name, f"exec /usr/bin/tee -a '{tmux_path(run_log(name))}' > /dev/null"] if b["type"] == "shell" else []
    if log:
        run_log(name).parent.mkdir(parents=True, exist_ok=True)
    r = tmux("new-session", "-d", "-s", name, "-c", tmux_path(cwd), "-x", "120", "-y", "40", *session_env(argv, name),
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


def run_log(name: str) -> Path:
    return DATA / "runs" / f"{name}.log"


ANSI = re.compile(r"\x1b(\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(\x07|\x1b\\)|[()][0-9A-Za-z]|[=>78DEHM])")


def run_text(name: str, limit: int = 200_000) -> str | None:
    """A script's output from its log (its end, if long), as plain text: escape codes gone, each line as last drawn."""
    try:
        with open(run_log(name), "rb") as f:
            f.seek(max(0, f.seek(0, 2) - limit))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    lines = ANSI.sub("", raw).replace("\r\n", "\n").split("\n")
    return "\n".join(line.rsplit("\r", 1)[-1] for line in lines)


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
    r = tmux("respawn-pane", "-k", "-t", target, "-c", fmt("#{pane_current_path}") or WORK, *session_env(argv, name),
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
        r = tmux("new-session", "-d", "-s", name, "-c", tmux_path(HOME), "-x", "120", "-y", "40",
                 *session_env([], name), "--", BASH, "-l",
                 ";", "set-option", "-t", name, "@deck_label", label,
                 ";", "set-option", "-t", name, "@deck_type", "terminal",
                 ";", "set-option", "-t", name, "status", "off",
                 ";", "set-option", "-g", "window-size", "latest",
                 *EXTENDED_KEYS)
        if r.returncode:
            raise RuntimeError(r.stderr.strip() or "tmux failed")
    return name


# ---- status -----------------------------------------------------------------

IDLE_AFTER = 6  # seconds without output before a live pane counts as waiting
OPTION_RE = re.compile(r"^[\s│|]*(?:[❯›>]\s*)?(\d)[.)]\s+(.{1,60}?)\s*[│|]?\s*$")
YESNO_RE = re.compile(r"\((?:y/n|Y/n|y/N)\)|\[(?:y/n|Y/n|y/N)\]")
NOISE_RE = re.compile(r"^[\s─━│┃╹▀⬝╭╮╰╯┌┐└┘├┤═>❯›·•*✻✳✶✢\-_=|]*$")
EMPTY_PROMPT_RE = re.compile(r"^[\s│|]*[>❯›]\s*[│|]?\s*$")
MENU_HINT_RE = re.compile(r"Enter to (confirm|select)", re.I)  # arrow-key menus (no numbers)
CURSOR_RE = re.compile(r"^(\s*❯\s+)(\S.{0,60}?)\s*$")
SPINNER_RE = re.compile(r"^[*✻✳✶✢✽·]\s+\S[^()]*…\s*\(")  # Claude "* Computing… (11s · …)": chrome below it
NOTICE_RE = re.compile(r"(Tip:|Update installed|Restart to update| · ctx \d+%|Context \d+% used)")  # incl. Claude/Codex status lines
# Codex "• Working (5s • esc to interrupt)", OpenCode "esc interrupt", Pi "── ⠹ Working ──", OpenClaw "⠇ moseying… • 1s"
BUSY_RE = re.compile(r"esc (to )?interrupt|[⠁-⣿] Working|[⠁-⣿] \S+… • \d", re.I)
AGENTS = ("claude", "codex", "opencode", "pi", "agy", "openclaw")
CHROME_RE = re.compile(r"(^Pane is dead|\? for shortcuts|esc (to )?interrupt|ctrl\+p commands|[┃▣]\s+(Build|Plan)( auto)? · |ctrl\+[a-z] to|shift\+tab|bypass permissions on|⏵⏵|tokens? used|context left|"
                       # Pi: spinner, footer folder ("~\work", "~/deck (main)"), context use ("1.6%/1.0M (auto)")
                       r"[⠁-⣿] Working|"
                       # OpenClaw: header, session line, spinner, status ("connected | idle"), footer ("… | deliver:off | …")
                       r"^\s*openclaw tui - |^\s*session agent:|[⠁-⣿] \S+… • \d|^\s*(gateway )?(connected|disconnected|local ready) \| |\| deliver:o|[\d.]+%/[\d.]+[kM]\b|^\s*(~|[A-Za-z]:)?[\\/]\S*( \([^)]*\))?\s*$)", re.I)


def menu_question(above: list[str], first: bool = False) -> str:
    """The prompt above a menu: the nearest line asking something (Claude's trust dialog puts a
    "Security guide" link between the question and the options), else the nearest text line,
    or with first=True the top of the text block (a warning's heading)."""
    text = [x.strip(" │|") for x in above if not NOISE_RE.match(x) and not CHROME_RE.search(x)][-8:]
    fallback = (text[0] if first else text[-1]) if text else ""
    return next((x for x in reversed(text) if x.endswith("?")), fallback)


def arrow_menu(tail: list[str]) -> tuple[list[dict], str]:
    """Unnumbered menu picked with arrows + Enter (e.g. Claude's bypass-permissions warning).
    Items are the lines just above the "Enter to confirm" hint aligned with the ❯ cursor line.
    Each option carries `nav`: arrow presses from the cursor (negative = up)."""
    hint = next((i for i in range(len(tail) - 1, max(-1, len(tail) - 4), -1) if MENU_HINT_RE.search(tail[i])), None)
    if hint is None:
        return [], ""
    cur = next((i for i in range(hint - 1, max(-1, hint - 10), -1) if CURSOR_RE.match(tail[i])), None)
    if cur is None:
        return [], ""
    col = len(CURSOR_RE.match(tail[cur]).group(1))
    aligned = lambda l: i_col(l) == col and len(l.strip()) <= 60
    start = cur
    while start - 1 >= 0 and aligned(tail[start - 1]):
        start -= 1
    end = cur
    while end + 1 < hint and aligned(tail[end + 1]):
        end += 1
    items = [CURSOR_RE.match(tail[i]).group(2) if i == cur else tail[i].strip() for i in range(start, end + 1)]
    if len(items) < 2:
        return [], ""
    return ([{"key": "", "label": t, "nav": i - (cur - start)} for i, t in enumerate(items)],
            menu_question(tail[:start], first=True))


def i_col(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def snippet_lines(lines: list[str], n: int = 8) -> list[str]:
    """Last n lines of real output: stop at an agent's spinner and skip status chrome, tips and notices."""
    body = [l.rstrip() for l in lines if l.strip()][-60:]
    spin = [i for i, l in enumerate(body) if SPINNER_RE.match(l)]
    if spin:
        body = body[:spin[-1]]
    out = [l for l in body if not NOISE_RE.match(l) and not CHROME_RE.search(l) and not NOTICE_RE.search(l)]
    return out[-n:]


def analyze(lines: list[str], kind: str, idle_secs: float, dead: bool, exit_code: int | None) -> dict:
    """Classify a pane from its visible text. Pure, for testing.
    state: done | failed | needs_you (choice prompt) | your_turn (agent idle) | idle (shell idle) | working"""
    tail = [l.rstrip() for l in lines if l.strip()][-20:]
    text_lines = [l for l in tail if not NOISE_RE.match(l) and not CHROME_RE.search(l)]
    preview = text_lines[-1].strip(" │|") if text_lines else ""
    options = []
    question = ""
    if not dead:
        idx = [i for i, l in enumerate(tail) if OPTION_RE.match(l)]
        block = []  # last run of option lines, allowing one wrapped line between them
        for i in idx:
            block = block + [i] if block and i - block[-1] <= 2 else [i]
        after = tail[block[-1] + 1:] if block else []
        # A menu sits at the bottom (only short hints below) and is not followed by an empty input prompt,
        # which would mean it was a numbered list in the agent's answer.
        if block and len(after) <= 4 and not any(EMPTY_PROMPT_RE.match(l) for l in after):
            for i in block:
                m = OPTION_RE.match(tail[i])
                options.append({"key": m.group(1), "label": m.group(2).strip()})
            question = menu_question(tail[:block[0]])
            if [o["key"] for o in options] != [str(n) for n in range(1, len(options) + 1)]:
                options, question = [], ""  # not a 1..n menu
        if not options:
            options, question = arrow_menu(tail)
        if not options and tail and YESNO_RE.search(tail[-1]):
            question = tail[-1].strip()
            options = [{"key": "y", "label": "Yes", "enter": True}, {"key": "n", "label": "No", "enter": True}]
    # Agent TUIs redraw while idle (status line, tips), so pane activity says nothing for them:
    # they are working only while their spinner shows. Shells fall back to recent output.
    if kind in AGENTS:
        busy = not options and any(SPINNER_RE.match(l) or BUSY_RE.search(l) for l in tail)
    else:
        busy = idle_secs < IDLE_AFTER
    if dead:
        state = "done" if exit_code == 0 else "failed"
    elif busy:
        state = "working"
    elif options:
        state = "needs_you"
    else:
        state = "your_turn" if kind in AGENTS else "idle"
    if options and state == "needs_you":
        preview = question or preview
    snippet = "\n".join(l.strip(" │|")[:200] for l in snippet_lines(lines))
    return {"state": state, "preview": preview[:160], "snippet": snippet, "options": options if state == "needs_you" else []}


def pane_subtitle(title: str, path: str) -> str:
    """What the tab's small second line shows: the title the program set (Claude's task name,
    spinner glyphs dropped), else the folder. tmux's default title is the host name."""
    title = re.sub(r"^(OC \| |π - )", "", re.sub(r"^[^\w~/(\[]+", "", title)).strip()  # OpenCode "OC | ", Pi "π - "
    title = title.removesuffix(" - " + path.rstrip("/").rsplit("/", 1)[-1])  # Pi "π - <name> - <folder>"
    if title and title != platform.node():
        return title[:60]
    path, home = win_path(path), str(Path.home()).replace("\\", "/")
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


def list_sessions() -> list[dict]:
    # Not tabs: tmux on Windows prints them as "_".
    fmt = "|~|".join(["#{session_name}", "#{session_created}", "#{pane_dead}", "#{pane_dead_status}",
                     "#{session_attached}", "#{window_activity}", "#{@deck_label}", "#{@deck_type}", "#{@deck_button}",
                     "#{pane_title}", "#{pane_current_path}", "#{pane_id}", "#{pane_pid}", "#{@deck_chat}", "#{@deck_title}",
                     "#{@deck_project}"])
    r = tmux("list-sessions", "-F", fmt)
    if r.returncode:  # no server running = no sessions
        return []
    now = time.time()
    out = []
    for line in r.stdout.splitlines():
        name, created, dead, status, attached, activity, label, kind, button, title, path, pane_id, pane_pid, chat, mine, project =             line.split("|~|", 15)
        exit_code = int(status) if status else None
        pane = tmux("capture-pane", "-p", "-t", f"={name}:").stdout.splitlines()
        info = analyze(pane, kind, now - int(activity or created), dead == "1", exit_code)
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
            "deck": name.startswith(PREFIX),
            "chat": bool(chat),
            "title": mine or pane_subtitle(title, path),  # a name the user gave the tab wins
            "project": project or project_of(path),  # its main page shows atop its chat (app.js renderProjBar)
            "status": agent_status(pane_id, pane_pid),
            **info,
        })
    rank = {"needs_you": 0, "your_turn": 1, "working": 2, "idle": 3, "failed": 4, "done": 5}
    return sorted(out, key=lambda s: (rank[s["state"]], -s["activity"]))


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


def session_exists(name: str) -> bool:
    return tmux("has-session", "-t", f"={name}").returncode == 0


def kill_session(name: str) -> None:
    tmux("kill-session", "-t", f"={name}")
    files = [CHAT_DIR / f"{name}{x}" for x in (".jsonl", ".in.jsonl", ".state", ".hooks.jsonl", ".opencode.json")] + [run_log(name)]
    for _ in range(20):  # Windows: the bridge holds its log open until it has seen the pane go
        try:
            for f in files:
                f.unlink(missing_ok=True)
            shutil.rmtree(CHAT_DIR / f"{name}.media", ignore_errors=True)  # its replies' files (fetched again if restored)
            return
        except PermissionError:
            time.sleep(0.1)


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
    meta = {"host": HOST_NAME, "session": name, "agent": kind or "shell", "label": label,
            "title": cs.get("name") or pane_subtitle(title, path), "folder": path,
            "started": int(created) if created.isdigit() else 0, "conversation": cs.get("resume", ""), "project": project or project_of(path)}
    return {k: v for k, v in meta.items() if v}


_projects: dict[str, tuple[float, str]] = {}  # folder -> (when worked out, project)


def project_of(folder: str) -> str:
    """A folder's project, the same name on every host, clone and worktree: its git origin as host/owner/repo
    (github.com/ancorro/deck); no remote: its repo root (else the folder) under home as ~/...; "" for home itself and
    the default work folder. Its main page is a shelf link marked main for it (deck/shelf.py). Cached a minute."""
    if not folder:
        return ""
    hit = _projects.get(folder)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    native, key = win_path(os.path.expanduser(folder)), ""
    try:
        r = subprocess.run(["git", "-C", native, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
        url = r.stdout.strip() if r.returncode == 0 else ""
        m = re.match(r"^(?:[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/|[\w.-]+@([^:/]+):)(.+?)(?:\.git)?/*$", url, re.I)
        if m:
            key = f"{m[1] or m[2]}/{m[3]}".lower()
        else:
            r = subprocess.run(["git", "-C", native, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
            root = Path(r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else native)
            if root.is_relative_to(HOME) and root.resolve() not in (HOME.resolve(), Path(WORK).resolve()):
                key = "~/" + root.relative_to(HOME).as_posix()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    _projects[folder] = (time.time(), key)
    return key


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


def chat_state(name: str) -> dict:
    """A chat session's {state, preview, name} as its bridge last wrote them; {} before it has."""
    try:
        return json.loads((CHAT_DIR / f"{name}.state").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def session_output(name: str) -> str:
    """A session's output as plain text: a chat's conversation, a script's log, else tmux history plus the screen."""
    if is_chat(name):
        return chat_text(name) + "\n"
    if (log := run_text(name)) is not None:  # a script: everything it printed (run_log)
        return log.rstrip() + "\n"
    return tmux("capture-pane", "-p", "-J", "-S", "-2000", "-t", f"={name}:").stdout.rstrip() + "\n"


def chat_text(name: str) -> str:
    """A chat session's conversation as plain text (Copy text), from its bridge's event log."""
    out = []
    try:
        lines = (CHAT_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
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
    CHAT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHAT_DIR / f"{name}.in.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(cmd, ensure_ascii=False) + "\n")
