"""Saved sessions: an agent's handoff note, kept on every Deck host and loadable into any agent session.

Save asks the session's agent to write the note to a file (SAVE_PROMPT). A watcher picks the file up, stores it in
DATA/saves and copies it to every other Deck host (POST <deck_url>/api/saves with the host key; offline hosts miss it).
Load writes the note to a file on this host and asks the current agent to read it and carry on (one typed line, so a
long note is never typed key by key into the agent; in a chat session, a message for its bridge).
Delete is soft: a deleted save can be restored for KEEP_DELETED, then it is purged. Deletes and restores are copied to
the other hosts too, so every host shows the same list.
"""
import asyncio
import json
import re
import secrets
import time
from pathlib import Path

from . import auth, config, sessions, settings

SAVE_PROMPT = (
    "Save this session for a handoff: write a markdown note that lets another agent, possibly on another machine, "
    "continue this work. Start with a one-line '# ' title, then: goal, current state, key decisions and why, the "
    "files, repos, branches and folders involved (full paths), commands to resume, open questions, and the next step. "
    "Write it to {path} and do nothing else.")
LOAD_PROMPT = ("Read the handoff note at {path} (saved from a {agent} session on {host}) and continue that work from "
               "where it left off.")
AGENTS = ("claude", "codex", "opencode", "pi", "agy", "openclaw")
WAIT = 15 * 60  # how long a save waits for the agent to write its note
MAX_TEXT = 200_000
KEEP_DELETED = 60 * 86400  # two months
ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")


def saves_dir() -> Path:
    return config.DATA / "saves"


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)


def title_of(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    first = next((l for l in lines if l.startswith("# ")), lines[0] if lines else "Saved session")
    return first.lstrip("# ").strip()[:80] or "Saved session"


def store(save: dict) -> dict:
    """Keep a save (made here or pushed by another host). Raises ValueError on a malformed one."""
    sid, text = str(save.get("id", "")), save.get("text")
    if not ID.fullmatch(sid):
        raise ValueError("bad save id")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError("save text missing or too long")
    s = {"id": sid, "text": text, "title": str(save.get("title") or title_of(text))[:80],
         "host": str(save.get("host", ""))[:40], "agent": str(save.get("agent", ""))[:20],
         "folder": str(save.get("folder", ""))[:300], "created": int(save.get("created") or time.time()),
         "deleted": int(save.get("deleted") or 0)}
    saves_dir().mkdir(parents=True, exist_ok=True)
    (saves_dir() / f"{sid}.json").write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    return s


def get(sid: str) -> dict | None:
    if not ID.fullmatch(sid):
        return None
    try:
        return json.loads((saves_dir() / f"{sid}.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def list_saves() -> list[dict]:
    """Newest first, without the note text (a short preview instead); deleted ones carry `deleted` (a time).
    Purges saves deleted more than KEEP_DELETED ago."""
    out = []
    for f in saves_dir().glob("*.json"):
        if (s := get(f.stem)):
            if s.get("deleted") and time.time() - s["deleted"] > KEEP_DELETED:
                f.unlink(missing_ok=True)
                continue
            body = " ".join(l for l in s["text"].splitlines()[1:] if l.strip())
            out.append({k: v for k, v in s.items() if k != "text"} | {"preview": body[:160]})
    return sorted(out, key=lambda s: -s["created"])


def set_deleted(sid: str, deleted: bool) -> dict:
    """Delete (restorable for KEEP_DELETED) or restore a save. Raises KeyError if there is no such save."""
    s = get(sid)
    if not s:
        raise KeyError(sid)
    return store(s | {"deleted": int(time.time()) if deleted else 0})


def session_info(name: str) -> tuple[str, str]:
    """The session's agent type and folder."""
    out = sessions.tmux("display-message", "-p", "-t", f"={name}:", "#{@deck_type}|#{pane_current_path}").stdout.strip()
    kind, _, folder = out.partition("|")
    if config.WINDOWS and (m := re.fullmatch(r"/([a-zA-Z])(/.*)?", folder)):  # MSYS tmux reports /c/Users/...
        folder = f"{m[1].upper()}:" + (m[2] or "/").replace("/", "\\")
    return kind, folder


async def start_save(name: str) -> str:
    """Ask the agent in this session for its handoff note; a watcher stores and shares it once written."""
    kind, folder = session_info(name)
    if kind not in AGENTS:
        raise ValueError("Save works in Claude, Codex, OpenCode and Pi sessions")
    sid = new_id()
    path = saves_dir() / "pending" / f"{sid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    await sessions.type_line(name, SAVE_PROMPT.format(path=path))
    meta = {"id": sid, "host": config.HOST_NAME, "agent": kind, "folder": folder}
    asyncio.get_running_loop().create_task(watch(path, meta))
    return sid


async def watch(path: Path, meta: dict, wait: float = WAIT, settle: float = 3) -> dict | None:
    """Wait for the note: stored once it exists and has stopped changing for `settle` seconds."""
    end, last = time.time() + wait, None
    while time.time() < end:
        await asyncio.sleep(1)
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        sig = (st.st_size, st.st_mtime)
        if st.st_size and sig == last and time.time() - st.st_mtime >= settle:
            s = store(meta | {"text": path.read_text(encoding="utf-8", errors="replace"), "created": int(time.time())})
            path.unlink(missing_ok=True)
            await push(s)
            return s
        last = sig
    return None


async def push(save: dict) -> list[str]:
    """Copy a save to every other Deck host; returns the hosts that took it."""
    import aiohttp
    key = auth.host_key()
    peers = [h for h in settings.hosts()["hosts"] if not h["current"]]
    if not key or not peers:
        return []

    async def one(session, h):
        try:
            async with session.post(h["url"].rstrip("/") + "/api/saves", json=save,
                                    headers={"Authorization": f"Bearer {key}"}) as r:
                return h["name"] if r.status == 200 else None
        except (TimeoutError, aiohttp.ClientError, OSError):
            return None
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        return [n for n in await asyncio.gather(*(one(session, h) for h in peers)) if n]


async def load(name: str, sid: str) -> None:
    """Hand a save to the agent in this session: the note goes to a file here, the agent is asked to read it."""
    s = get(sid)
    if not s:
        raise KeyError(sid)
    if session_info(name)[0] not in AGENTS:
        raise ValueError("Load works in Claude, Codex, OpenCode and Pi sessions")
    path = saves_dir() / "loaded" / f"{sid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s["text"], encoding="utf-8")
    prompt = LOAD_PROMPT.format(path=path, agent=s["agent"] or "agent", host=s["host"] or "another host")
    if sessions.is_chat(name):
        sessions.chat_command(name, {"t": "say", "text": prompt})
    else:
        await sessions.type_line(name, prompt)
