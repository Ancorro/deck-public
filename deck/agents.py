# ruff: noqa: E501  (the context prompt is prose: its lines stay whole)
"""The agents Deck runs (Claude Code, Codex, OpenCode, Pi, agy, OpenClaw): which are installed here, the flags
that set their permissions, model and effort, the context Deck gives each one, and the command line that starts one
in a tmux pane (build_argv)."""
import json
import os
import re
import shutil
import sys
from pathlib import Path

from . import config, skills
from .settings import EFFORTS, default_folder, load_settings, valid_model

# The agents, in the picker's order. Every one runs as a chat (deck/chat_bridge.py) when started from Deck.
AGENTS = ("claude", "codex", "opencode", "pi", "agy", "openclaw")
CHAT_AGENTS = AGENTS
TYPES = ("shell", *AGENTS)  # a script key's types
# "Skip permissions" (YOLO): the agent runs tools and commands without asking. Pi never asks, so it has no flag.
YOLO_FLAGS = {"claude": ["--dangerously-skip-permissions"], "codex": ["--dangerously-bypass-approvals-and-sandbox"],
              "opencode": ["--auto"], "agy": ["--dangerously-skip-permissions"]}
# The flag that continues the folder's most recent conversation (a terminal session's Restart)
RESUME_FLAG = {"claude": "--continue", "codex": "resume --last", "opencode": "--continue", "pi": "--continue", "agy": "--continue"}
# The flag that sets an agent's effort level or model (settings.EFFORTS, settings.MODELS list the levels and models)
EFFORT_FLAG = {"claude": lambda x: ["--effort", x], "codex": lambda x: ["-c", f"model_reasoning_effort={x}"],
               "pi": lambda x: ["--thinking", x], "agy": lambda x: ["--effort", x]}
MODEL_FLAG = {kind: lambda x: ["--model", x] for kind in AGENTS}
# What Deck ships for the agents (deploy/agents/) and where the bridge is
_DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "agents"
SAMPLING_PLUGIN = _DEPLOY / "opencode-sampling.js"  # OpenCode: Harness → Sampling, applied per request
PI_NAME = _DEPLOY / "pi-name.ts"  # Pi extension: the session's name from its first message
CHAT_BRIDGE = Path(__file__).resolve().parent / "chat_bridge.py"
COMPUTER_MCP = Path(__file__).resolve().parent / "computer_mcp.py"  # a Computer session's desktop tools

# ---- the context every agent gets ----

DECK_CONTEXT = """You are running inside Deck, the user's control panel for agents, on host {host}. A message the user sends from Deck's chat starts with [when · device, how], how being phone app, phone browser or PC browser. When it says phone, or there is no such stamp (Deck's terminal view adds none):
- Keep replies short and scannable. No wide tables or long code dumps unless asked.
- Ask at most one question at a time. Numbered options and y/n prompts appear as tap buttons.
- Give links as plain URLs; no QR codes (they are already on the phone).
Always:
- If you are stuck (blocked, unsure, or repeating attempts that fail), stop and ask the user instead of guessing or looping.
- When you ask the user something, use your question tool (AskUserQuestion, ask_question, or Deck's question card) with the choices as options, so they show as buttons; a yes/no question offers Yes and No, and every question includes a way to say no (user 2026-11-15: "deck sessions with a qustions feature should use then presenting options or Yes/No"). Put the context inside the question: Deck hides chat text written just before it.
- The user's shelf (chat they saved, in folders): `curl -s http://127.0.0.1:{port}/api/shelf/agent` lists it (`?id=ID`: one item, `?all=1`: everything), if they allow agents to read it. Read it when they mention the shelf or a saved item.
- Pin a web page you made or found (an artifact, docs, a dashboard) to the shelf: `curl -s http://127.0.0.1:{port}/api/shelf/pin -H 'Content-Type: application/json' -d '{{"url": "...", "title": "...", "note": "one line"}}'`. The same url again updates its item. Keep one artifact page per project and add to it (same url) instead of making a new artifact for each thing. Make it the project's main page, shown in a bar atop the chat of every session in this project (its git repo), by adding `"main": true, "session": "'"$DECK_SESSION_NAME"'", "folder": "<the repo's path>"` to the pin.
- Post a card to the user's Board (Deck's first view) when something is worth a glance: `curl -s http://127.0.0.1:{port}/api/board -H 'Content-Type: application/json' -d '{{"kind": "...", "title": "...", "text": "...", "session": "'"$DECK_SESSION_NAME"'"}}'`. Kinds: note (short Markdown text), link (`url`, title optional), list (one item per line of text), status (a run's progress: title and one line), alert (needs the user). Add `"key": "..."` to update that card instead of adding one. Each project has one status card, `"kind": "status", "key": "status-<repo folder name>"`, shared by every session in it: update it (title: the repo, text: where things stand, 30 words max) after each large step of your work.
- Shelf, Board and pin calls answer with the saved item (its `id`). A 503 with `"retry": true` means Deck's main copy is out of reach from this host: nothing was saved, so retry in a few seconds; never report an item as added without its `id`.
- Put a file the user wants to read or edit in Deck (a paper's source, its PDF) on the shelf as the file itself, not a copy: `curl -s http://127.0.0.1:{port}/api/shelf/link -H 'Content-Type: application/json' -d '{{"path": "<full path>", "title": "...", "session": "'"$DECK_SESSION_NAME"'"}}'`. Their edits in the reader write the file; your edits (and rebuilds) show in the reader within seconds.
- When you start working in a project's repo, outside the folder you started in, tell Deck (its main-page bar follows): `curl -s -X PUT http://127.0.0.1:{port}/api/sessions/$DECK_SESSION_NAME/project -H 'Content-Type: application/json' -d '{{"folder": "<the repo's path>"}}'`.
- The user's other sessions, on this host and their other hosts, if the user allows it: `curl -s http://127.0.0.1:{port}/api/agent/sessions` lists them, `/api/agent/sessions/NAME` reads one's recent output (another host's: `/peer/HOST/api/agent/sessions/NAME`), and `curl -s http://127.0.0.1:{port}/api/agent/sessions/NAME/send -H 'Content-Type: application/json' -d '{{"text": "...", "from": "{host}/'"$DECK_SESSION_NAME"'"}}'` messages it (same /peer/HOST prefix for another host). Message another session only for a clear handoff or when the user asks; answer an agent's message only if it asks you something.
- Editing a Google Doc the user names a part of: read it first; replaceAllText only text that appears once in it, else delete and insert at that one spot's indexes (with the read's revision id).{notes}"""
# Only when the machine notes repo has agent instructions (DECK_NOTES/AGENTS.md).
NOTES_LINE = "\n- Machine facts and admin notes live in {path}: follow its AGENTS.md and record any admin change there."


def notes_files() -> list[str]:
    """The notes every agent follows, given in full at its start (told only to read them, agents skipped it whenever
    the first message was a quick question): the repo's one AGENTS.md and this host's short hosts/<name>.md (user)."""
    return ["AGENTS.md", f"hosts/{config.HOST_NAME.lower()}.md"]


def deck_context() -> str:
    notes, repo = "", config.NOTES
    if (repo / "AGENTS.md").exists():
        notes = NOTES_LINE.format(path=str(repo).replace(str(config.HOME), "~", 1))
        for name in notes_files():
            try:
                text = (repo / name).read_text(encoding="utf-8").strip()
                notes += f"\n\n# {name} (from {repo.name}, as of this session's start; follow it)\n\n{text}"
            except OSError:
                pass
    return DECK_CONTEXT.format(host=config.HOST_NAME, notes=notes, port=os.environ.get("DECK_PORT", "8787"))


# ---- which agents are installed here ----



def pi_available() -> bool:
    """The Pi key shows only where pi is installed (Deck's setup installs it)."""
    own = (".local/bin/pi", ".pi/agent/bin/pi", ".pi/agent/bin/pi.cmd")
    return bool(shutil.which("pi")) or any((config.HOME / p).exists() for p in own)


def agy_available() -> bool:
    """The agy (Google Antigravity CLI) key shows only where agy is installed."""
    return bool(agy_path())


def agy_path() -> str:
    """Also find Google's default install before a running service inherits the updated PATH."""
    if path := shutil.which("agy"):
        return path
    path = (Path(os.environ.get("LOCALAPPDATA", config.HOME / "AppData" / "Local")) / "agy" / "bin" / "agy.exe"
            if config.WINDOWS else config.HOME / ".local" / "bin" / "agy")
    return str(path) if path.is_file() else ""


def openclaw_available() -> bool:
    """The OpenClaw key shows only where openclaw is installed (its TUI needs a running OpenClaw Gateway)."""
    return bool(shutil.which("openclaw"))


def agents_installed() -> list[str]:
    """The agent types set up on this host, in picker order: + and Default agent offer only these (user)."""
    home = config.HOME
    found = {"claude": bool(shutil.which("claude")) or (home / ".local/bin/claude").exists(),
             "codex": bool(shutil.which("codex")) or (home / ".local/bin/codex").exists(),
             "opencode": bool(shutil.which("opencode"))
             or any((home / p).exists() for p in (".local/bin/opencode", ".opencode/bin/opencode")),
             "pi": pi_available(), "agy": agy_available(), "openclaw": openclaw_available()}
    return [a for a in AGENTS if found[a]]


# ---- Computer sessions: Claude with this host's desktop as tools (computer_mcp.py), shown above the chat ----

def computer_config() -> dict | None:
    """This host's computer.json (config.COMPUTER_FILE), or None when computer use isn't set up here."""
    try:
        cfg = json.loads(Path(config.COMPUTER_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    return cfg if isinstance(cfg, dict) and cfg.get("python") else None


def computer_args() -> list[str]:
    """Claude's flags for a Computer session: the desktop tools as an MCP server (in a file: JSON on the command line
    goes through tmux and MSYS), allowed without asking at each click (the user watches it above the chat, and the
    mouse in the top-left corner stops it)."""
    cfg = computer_config()
    if not cfg:
        raise ValueError("computer use isn't set up on this host (computer.json, docs/guide.md#computer-sessions)")
    mcp = {"mcpServers": {"computer": {"command": os.path.expanduser(cfg["python"]),
                                       "args": [COMPUTER_MCP.as_posix(), Path(config.COMPUTER_FILE).as_posix()]}}}
    path = config.DATA / "computer-mcp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mcp), encoding="utf-8")
    # --allowedTools=X, one argument: as two, the flag (which takes a list) would also swallow the first message,
    # which the bridge passes after these
    return ["--mcp-config", path.as_posix(), "--allowedTools=mcp__computer"]


def pi_durable_available() -> bool:
    """Settings → General → Advanced → Durable Pi shows only where Deck's setup installed pi-durable (deploy/agents/pi-durable)."""
    return bool(shutil.which("pi-durable")) or (config.HOME / ".local/bin/pi-durable").exists()


# ---- how each agent is started ----


def agy_context() -> Path:
    """agy has no system-prompt flag, but loads AGENTS.md and .agents/rules/*.md from any `--add-dir` folder: write the
    Deck note there and, when the machine notes repo has agent instructions, copy its instruction files in."""
    d = config.DATA / "agy-context"
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
    if (config.NOTES / "AGENTS.md").exists():
        for name in notes_files():
            src = config.NOTES / name
            if src.exists():
                (rules / f"infra-{Path(name).name}").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return d


def context_args(kind: str) -> list[str]:
    """Tell Claude/Codex/agy they are in a Deck session (env vars alone are invisible to them)."""
    text = deck_context()
    if kind in ("claude", "pi"):  # as a file: the note (with infra's preferences) nearly filled tmux's 16 KB command limit
        note = config.DATA / "agent-context.md"
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
    note = config.DATA / "opencode-context.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(deck_context() + "\n")
    config_ = {"instructions": [note.as_posix()]}
    # The agent browser (deploy/agents/agent-browser.sh registers it for Claude and Codex): OpenCode gets it here
    if (config.HOME / ".config" / "playwright-mcp.json").exists():
        config_["mcp"] = {"playwright": {"type": "remote", "url": "http://127.0.0.1:8931/mcp"}}
    # Harness → Sampling: its plugin reads them from the settings file at each request, and skips the models
    # that take none (the skip list, which the chat bridge keeps)
    config_["plugin"] = [SAMPLING_PLUGIN.as_uri()]
    # Forward slashes: the MSYS env/bash in front of it unescape one level of backslashes, leaving invalid JSON
    # ("C:\Users") that makes OpenCode answer every request with a 400 ConfigJsonError.
    return ["env", f"DECK_SAMPLING={config.settings_path().as_posix()}",
            f"DECK_SAMPLING_SKIP={(config.DATA / 'opencode-sampling-skip.json').as_posix()}",
            "OPENCODE_CONFIG_CONTENT=" + json.dumps(config_)]


def build_argv(b: dict) -> list[str]:
    """Command for the tmux pane (a chat's: for its detached bridge, deck/bridges.py). Login bash so ~/.local/bin is
    on PATH.
    User text is passed as an argument ("$@"), never spliced into the script."""
    kind, text = b["type"], b["text"]
    if kind == "shell":  # an asking key's input is "$1", an argument (never spliced into the script)
        return [config.BASH, "-lc", text, *(["deck", b["arg"]] if b.get("arg") else [])]
    skills.ensure_quietly()  # a skill added since the server started reaches this agent too
    yolo = YOLO_FLAGS.get(kind, []) if b.get("yolo") else []
    # Settings defaults, or the key's own (the bug button's); a chat's own /model, /effort pick overrides them. A
    # Computer session has its own Default model row; blank, it takes Claude's. Read here, so Restart keeps it.
    picked = b.get("model") or ((load_settings().get("model") or {}).get("computer") if b.get("computer") else None)
    model, effort = model_args(kind, picked), effort_args(kind, b.get("effort"))
    if kind == "agy" and model and effort:  # agy encodes effort in its model ID; conflicting flags fail
        model[1] = re.sub(r"-(low|medium|high)$", "-" + effort[1], model[1])
        effort = []
    yolo = [*yolo, *model, *effort]
    if b.get("chat"):  # the agent's own arguments go after --; its adapter turns the YOLO flag into its own settings
        if kind == "codex":  # send the full context over app-server's protocol, beyond tmux's command limit
            note = config.DATA / "codex-context.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(deck_context() + "\n", encoding="utf-8")
            context = ["--deck-context", note.as_posix()]
        else:
            context = context_args(kind)
        args = [*context, *(["-e", str(PI_NAME)] if kind == "pi" else []), *yolo, *b.get("extra", []),
                *(computer_args() if b.get("computer") and kind == "claude" else [])]
        # Run detached by deck/bridges.py, not in tmux. Windows: natively (no MSYS bash to rewrite /-looking
        # arguments); elsewhere through a login bash, for the PATH a person's shell has (~/.local/bin, nvm)
        bridge = [sys.executable, str(CHAT_BRIDGE), kind, str(config.CHAT_DIR), b["session"],
                  "1" if b.get("resume") else "0", text, "--", *args]
        login = [] if config.WINDOWS else [config.BASH, "-lc", 'exec "$@"', "deck"]
        return [*env_prefix(kind), *login, *bridge]
    cmd = "pi-durable" if kind == "pi" and b.get("durable") else kind  # same arguments as pi
    name = ["-e", str(PI_NAME)] if cmd == "pi" else []  # names the session, so the tab shows a name
    args = [*context_args(kind), *name, *yolo, *b.get("extra", [])]
    if kind == "openclaw":  # its TUI on the Gateway's one ongoing conversation ("main"): every tab and Restart continue it
        tui = ["--session", "main", *(["--message", text] if text else [])]
        model = model_args(kind, b.get("model"))
        if model:
            ref = model[1]
            claude = ref.startswith(("anthropic/", "claude-cli/")) or ref.split("/")[-1].startswith("claude")
            params = json.dumps({"key": "main", "model": ref, **({"agentRuntime": None} if claude else {})})
            return [config.BASH, "-lc", 'openclaw gateway call sessions.patch --params "$1" >/dev/null && shift && exec openclaw tui "$@"',
                    "deck", params, *tui]
        return [config.BASH, "-lc", 'exec openclaw tui "$@"', "deck", *tui]
    if b.get("resume"):
        # Restart: continue the folder's most recent conversation; if there is none (it errors), start a fresh one.
        resume = RESUME_FLAG[kind]
        return [*env_prefix(kind), config.BASH, "-lc", f'{cmd} {resume} "$@" || exec {cmd} "$@"', "deck", *args]
    if text:  # OpenCode's positional is a project folder; its first message goes in --prompt (agy: -i, interactive)
        args += {"opencode": ["--prompt", text], "agy": ["-i", text]}.get(kind, [text])
    return [*env_prefix(kind), config.BASH, "-lc", f'exec {cmd} "$@"', "deck", *args]


def effort_args(kind: str, level: str | None = None) -> list[str]:
    """The agent's flag for level, else its Settings default effort, or nothing (the agent's own default)."""
    level = level or (load_settings().get("effort") or {}).get(kind)
    return EFFORT_FLAG[kind](level) if level in EFFORTS.get(kind, ()) else []


def model_args(kind: str, model: str | None = None) -> list[str]:
    """The agent's flag for model, else its Settings default model, or nothing (the agent's own default)."""
    model = model or (load_settings().get("model") or {}).get(kind)
    return MODEL_FLAG[kind](model) if valid_model(kind, model) else []


def resolve_cwd(b: dict) -> str:
    """The folder a key runs in: its own, else home for a script and the default folder for an agent."""
    default = str(config.HOME) if b["type"] == "shell" else default_folder()
    return os.path.expanduser(b["cwd"] or default)


