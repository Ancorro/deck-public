"""Session descriptions (Settings, off by default): after each agent turn, a small model reads what was new and rewrites
a description of at most 50 words: Claude Haiku, or OpenAI's through Codex. Each tab has its own model session (claude -p
--resume, codex exec resume) that gets every turn, so it keeps the story.

Shown in the tab's right-click menu (Description) and to agents that list the sessions (/api/agent/sessions).
Saved in DATA/descriptions.json: {session: {text, model (claude|codex), sid (its session id), sent (chat: characters sent), at}}."""
import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid

from . import core

WORDS = 50
POLL = 10  # seconds between looks at the sessions
TAIL = 6000  # most characters of a turn sent (its end matters most)
FIRST = """You keep a short description of one agent session in Deck (a phone control panel for coding agents), so the
user and other agents can tell what it is about at a glance. After each of its turns you get what was new. Reply with
only the updated description: at most {words} words, plain text, no preamble. Say what the session is working on, what
it has done, and what it is waiting on, if anything. Keep what still matters from before.

Session "{label}" ({kind}), titled "{title}". Its latest turn:

{turn}"""
NEXT = "Its next turn:\n\n{turn}\n\nReply with only the updated description (at most {words} words)."
log = logging.getLogger(__name__)
_lock = threading.Lock()  # the file
_busy: set[str] = set()  # sessions being described now


def load() -> dict:
    try:
        return json.loads((core.DATA / "descriptions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(name: str) -> dict:
    return load().get(name) or {}


def _save(name: str, entry: dict | None) -> None:
    with _lock:
        d = load()
        if entry is None:
            d.pop(name, None)
        else:
            d[name] = entry
        core.DATA.mkdir(parents=True, exist_ok=True)
        (core.DATA / "descriptions.json").write_text(json.dumps(d, indent=1), encoding="utf-8")


def _turn(s: dict, entry: dict) -> tuple[str, int]:
    """What's new since the last description, and the chat position to start from next time."""
    if s.get("chat"):
        text = core.chat_text(s["name"])
        new = text[entry.get("sent", 0):] if entry.get("sent", 0) <= len(text) else text
        return new[-TAIL:], len(text)
    return core.session_output(s["name"])[-TAIL:], 0  # a terminal's history scrolls: its end stands for the turn


CODEX_MODEL = os.environ.get("DECK_DESCRIBE_CODEX_MODEL", "gpt-5.6-luna")  # OpenAI's fast, cheap Codex model


def _run(argv: list[str], prompt: str) -> subprocess.CompletedProcess | None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX_THREAD", "DECK_SESSION"))}
    cwd = core.DATA / "describe"  # its sessions live in their own project folder, apart from the user's
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        return subprocess.run(argv, input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=180,
                              cwd=str(cwd), env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def _ask(model: str, prompt: str, sid: str | None) -> tuple[str, str]:
    """(description, the model's session id to resume next turn); sid None starts a new session."""
    global CODEX_MODEL
    if not (exe := shutil.which(model)):
        return "", ""
    if model == "codex":  # --json: its thread id comes in the first event
        opts = ["--json", "--skip-git-repo-check", *(["-m", CODEX_MODEL] if CODEX_MODEL else []),
                "-c", "model_reasoning_effort=low", "-c", 'sandbox_mode="read-only"']
        r = _run([exe, "exec", "resume", *opts, sid, "-"] if sid else [exe, "exec", *opts, "-"], prompt)
        if r and r.returncode and CODEX_MODEL and not sid:  # a fresh start failed: model gone or not on the account
            log.warning("codex failed with model %s; using its default model from now on", CODEX_MODEL)
            CODEX_MODEL = ""
            return _ask(model, prompt, sid)
        text = ""
        for line in (r.stdout if r else "").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "thread.started":
                sid = ev.get("thread_id") or sid
            elif ev.get("type") == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
                text = ev["item"].get("text", "")
    else:
        new = sid or str(uuid.uuid4())
        r = _run([exe, "-p", "--model", "haiku", "--resume" if sid else "--session-id", new], prompt)
        sid, text = new, r.stdout if r else ""
    words = text.strip().split()
    return (" ".join(words[:WORDS]), sid or "") if r and r.returncode == 0 and words else ("", "")


def update(s: dict) -> str:
    """Describe session s (a list_sessions entry) from its latest turn; returns the description ("" if none came)."""
    name, model = s["name"], core.settings().get("describer", "claude")
    entry = get(name)
    turn, sent = _turn(s, entry)
    if not turn.strip():
        return entry.get("text", "")
    first = FIRST.format(words=WORDS, label=s.get("label", name), kind=s.get("type", ""), title=s.get("title", ""),
                         turn=turn)
    sid = entry.get("sid") if entry.get("model") == model else None  # the describer changed: a new session
    text, sid = _ask(model, NEXT.format(turn=turn, words=WORDS) if sid else first, sid)
    if not text and entry.get("sid"):  # its session is gone (cleared, another machine): start a new one
        text, sid = _ask(model, first, None)
    if text:
        _save(name, {"text": text, "model": model, "sid": sid, "sent": sent, "at": int(time.time())})
    return text or entry.get("text", "")


def turns(seen: dict, sessions: list[dict]) -> list[dict]:
    """Agent sessions whose turn just ended (working -> anything else), updating seen ({name: state})."""
    out = []
    for s in sessions:
        if s["type"] not in core.AGENTS:
            continue
        prev, seen[s["name"]] = seen.get(s["name"]), s["state"]
        if prev == "working" and s["state"] != "working":
            out.append(s)
    for name in set(seen) - {s["name"] for s in sessions}:
        del seen[name]
    return out


async def watch(_app) -> None:
    """Runs for the server's life (started in make_app); describes only while Settings has it on."""
    loop, seen = asyncio.get_running_loop(), {}

    async def one(s):
        try:
            await loop.run_in_executor(None, update, s)
        except Exception:
            log.exception("describe %s", s["name"])
        finally:
            _busy.discard(s["name"])

    while True:
        try:
            if not core.settings().get("describe"):  # off: no tmux calls (slow on Windows) until it's on
                seen.clear()
                await asyncio.sleep(POLL)
                continue
            sessions = await loop.run_in_executor(None, core.list_sessions)
            for s in turns(seen, sessions):
                if s["name"] not in _busy:
                    _busy.add(s["name"])
                    loop.create_task(one(s))
            names, old = {s["name"] for s in sessions}, time.time() - 30 * 86400
            for gone, e in load().items():  # a tab closed a month ago (a closed one can come back from the trash)
                if gone not in names and e.get("at", 0) < old:
                    _save(gone, None)
        except Exception:
            log.exception("describe")
        await asyncio.sleep(POLL)
