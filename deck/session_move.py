"""Move host (Harness): an agent chat carries on on another Deck host. This host packs the session (what starts it
again, as session_trash keeps it, and the agent's own conversation file) and posts it to the other host's
/api/move-in with the host key. That host puts the file where its agent looks, starts the chat on that conversation as
a new tab, and tells the agent it has moved (MOVE_PROMPT, its first message). Only then does this host close its tab,
not into the trash: the conversation runs in one place only (user); moving it back undoes a move. Every chat agent: each
keeps a conversation in one file (Claude ~/.claude/projects/<folder>/<id>.jsonl plus a sidecar folder; Codex a rollout
under ~/.codex/sessions, resumed by its path since Codex's own index hasn't seen it; Pi a session file under
~/.pi/agent/sessions/<folder>, resumed by its path) or, for OpenCode, whose sessions live in its database, the export its
bridge writes after each turn (<session>.opencode.json), which `opencode import` takes in on the other host."""
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from . import auth, core

AGENTS = ("claude", "codex", "pi", "opencode")
MAX = 30 * 1024 * 1024  # the other host takes at most 32 MB (server client_max_size)
MOVE_PROMPT = (
    "[Deck] This conversation was just moved from host {src} to host {dst} ({when}). You are now running on {dst}, "
    "in {folder}.{fallback} Files, processes, servers and tools from {src} are not here unless synced; check paths "
    "before relying on them, and use {dst}'s machine notes. Reply in one short line that you're on {dst}, then wait "
    "for the user.")
# Moved here from a copy, its host offline (mirror.move_here)
OFFLINE_NOTE = (" {src} is offline, so this continues from the copy made {synced}; anything said after that is still "
                "on {src}.")


def slug(cwd: str) -> str:
    """Claude's folder name for a project (as chat_bridge's Claude adapter: projects())."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def portable(folder: str) -> str:
    """A folder as the other host can find it: under the home folder as ~/..., else unchanged."""
    home, f = core.HOME.as_posix(), Path(folder).as_posix()
    under = f.lower().startswith(home.lower()) if core.WINDOWS else f.startswith(home)
    return "~" + f[len(home):] if under and f[len(home):len(home) + 1] in ("", "/") else folder


def claude_files(conv: str) -> dict[str, Path]:
    """The transcript, then its sidecar folder (subagents, tool results), by their paths in the project folder."""
    found = sorted((core.HOME / ".claude" / "projects").glob(f"*/{conv}.jsonl"), key=lambda f: f.stat().st_mtime)
    if not found:
        raise ValueError("this chat's Claude transcript wasn't found")
    t = found[-1]
    files = {t.name: t}
    if t.with_suffix("").is_dir():
        files |= {f.relative_to(t.parent).as_posix(): f for f in t.with_suffix("").rglob("*") if f.is_file()}
    return files


def codex_files(conv: str) -> dict[str, Path]:
    root = core.HOME / ".codex" / "sessions"
    found = sorted(root.glob(f"**/rollout-*{conv}.jsonl"))
    if not found:
        raise ValueError("this chat's Codex rollout wasn't found")
    return {found[-1].relative_to(root).as_posix(): found[-1]}


def pi_dir() -> Path:
    return Path(os.environ.get("PI_CODING_AGENT_DIR") or core.HOME / ".pi" / "agent")


def pi_slug(folder: str) -> str:
    """Pi's folder name for a project's sessions (as chat_bridge's Pi adapter: conversations())."""
    return "--" + re.sub(r"[/\\:]", "-", re.sub(r"^[/\\]", "", folder)) + "--"


def files_of(name: str, kind: str, conv: str) -> dict[str, Path]:
    """A chat's conversation file(s), the conversation first."""
    if kind == "claude":
        return claude_files(conv)
    if kind == "codex":
        return codex_files(conv)
    f = Path(conv) if kind == "pi" else core.CHAT_DIR / f"{name}.opencode.json"
    if not f.is_file():
        raise ValueError("this chat's Pi session file wasn't found" if kind == "pi" else "OpenCode hasn't saved this chat yet")
    return {f.name if kind == "pi" else f"{conv}.json": f}


def item_of(name: str) -> dict:
    """What starts the chat `name` again on another host. ValueError: it can't move."""
    if not core.is_chat(name):
        raise ValueError("Move host works in chat sessions")
    st, meta = core.chat_state(name), core.session_meta(name)
    kind = meta.get("agent")
    if kind not in AGENTS:
        raise ValueError("Move host works in agent chats")
    if st.get("state") == "working":  # its conversation file is still being written
        raise ValueError("Wait for the agent to finish (or Stop it), then move")
    conv = st.get("resume")
    if not conv:
        raise ValueError("Nothing to move yet: say something first")
    start = core.tmux("display-message", "-p", "-t", f"={name}:", "#{pane_start_command}").stdout
    item = {"type": kind, "label": meta.get("label") or kind, "from": core.HOST_NAME,
            "folder": portable(core.win_path(meta.get("folder", ""))),
            "yolo": any(f in start for f in core.YOLO_FLAGS.get(kind, [])),
            "state": {"resume": conv, "prefs": st.get("prefs") or {}, "name": st.get("name", "")}}
    return item


def pack(name: str) -> tuple[dict, bytes]:
    """The session as its item and a gzipped tar of item.json and files/<path>. ValueError: it can't move."""
    item = item_of(name)
    kind, conv = item["type"], item["state"]["resume"]
    files = files_of(name, kind, conv)
    first = next(iter(files))  # the conversation itself; Claude's sidecar is dropped if it won't fit
    for keep in (files, {first: files[first]}):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = json.dumps(item).encode()
            info = tarfile.TarInfo("item.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            for rel, f in keep.items():
                tar.add(str(f), arcname=f"files/{rel}", recursive=False)
        if buf.tell() <= MAX:
            return item, buf.getvalue()
    raise ValueError("This conversation is too big to move")


def safe(rel: str) -> bool:
    p = PurePosixPath(rel)
    return bool(rel) and not p.is_absolute() and ".." not in p.parts and ":" not in rel and "\\" not in rel


def receive(body: bytes) -> dict:
    """On the host a chat moves to: its files placed, the chat started on its conversation. {session, folder,
    warning}; ValueError for a bad package."""
    try:
        tar = tarfile.open(fileobj=io.BytesIO(body), mode="r:gz")
        item = json.loads(tar.extractfile("item.json").read())
    except (tarfile.TarError, KeyError, AttributeError, ValueError) as e:
        raise ValueError(f"not a moved session ({e})")
    kind, conv = item.get("type"), str((item.get("state") or {}).get("resume") or "")
    if kind == "pi":  # its session file's path on the other host: here it goes under this folder's
        conv = PurePosixPath(conv.replace("\\", "/")).name
    if kind not in AGENTS or not re.fullmatch(r"[\w.-]+" if kind == "pi" else r"[\w-]+", conv):
        raise ValueError("not an agent chat")
    given = str(item.get("folder") or "~")
    folder, warning = os.path.normpath(str(core.HOME) + given[1:] if given.startswith("~") else given), ""
    if not os.path.isdir(folder):
        warning = f"{item.get('folder')} isn't on {core.HOST_NAME}, so it runs in {core.default_folder()}"
        folder = os.path.normpath(core.default_folder())
    # Where its agent looks: Claude and Pi by the folder it runs in, Codex by the path given at resume; OpenCode's export
    # waits next to the chats till `opencode import` takes it in
    root = {"claude": core.HOME / ".claude" / "projects" / slug(folder), "codex": core.HOME / ".codex" / "sessions",
            "pi": pi_dir() / "sessions" / pi_slug(folder), "opencode": core.CHAT_DIR / "moved"}[kind]
    placed = []
    for m in tar.getmembers():
        rel = m.name.removeprefix("files/")
        if m.isfile() and m.name.startswith("files/") and safe(rel):
            dest = root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(tar.extractfile(m).read())
            placed.append(dest)
    want = {"pi": conv, "opencode": f"{conv}.json"}.get(kind)
    conv_file = next((p for p in placed if (p.name == want if want else p.name.endswith(f"{conv}.jsonl"))
                      and (kind == "codex" or p.parent == root)), None)
    if not conv_file:
        raise ValueError("the package had no conversation file")
    state = {"resume": conv, "prefs": item["state"].get("prefs") or {}, "name": str(item["state"].get("name") or "")}
    if kind == "codex":
        state["moved"] = {"id": conv, "path": str(conv_file)}
    elif kind == "pi":
        state["resume"] = str(conv_file)
        refolder_pi(conv_file, folder)
    elif kind == "opencode":
        import_opencode(conv_file, folder)
    src = str(item.get("from") or "another host")
    note = MOVE_PROMPT.format(src=src, dst=core.HOST_NAME, folder=folder,
                              when=f"{datetime.now().astimezone():%a %Y-%m-%d %H:%M %z}",
                              fallback=(f" Its folder on {src}, {item.get('folder')}, isn't on this host." if warning else "")
                              + (OFFLINE_NOTE.format(src=src, synced=f"{datetime.fromtimestamp(float(item['offline'])).astimezone():%a %H:%M %z}")
                                 if item.get("offline") else ""))
    b = core.new_agent_button(kind, note, folder, bool(item.get("yolo")))
    name = core.start_run(b, state=state)
    return {"session": name, "folder": folder, "warning": warning}


def refolder_pi(f: Path, folder: str) -> None:
    """A moved Pi session file: its header names the folder it now runs in."""
    lines = f.read_text(encoding="utf-8").split("\n")
    try:
        head = json.loads(lines[0])
    except (ValueError, IndexError):
        return
    if head.get("type") == "session":
        lines[0] = json.dumps({**head, "cwd": folder}, separators=(",", ":"))
        f.write_text("\n".join(lines), encoding="utf-8")


def import_opencode(f: Path, folder: str) -> None:
    """A moved OpenCode export into this host's OpenCode, its session now in `folder` (OpenCode lists sessions by it)."""
    d = json.loads(f.read_text(encoding="utf-8"))
    d["info"] = {**d.get("info", {}), "directory": folder, "path": folder.lstrip("/\\")}
    f.write_text(json.dumps(d), encoding="utf-8")
    exe = shutil.which("opencode") or next((str(p) for p in (core.HOME / ".local/bin/opencode", core.HOME / ".opencode/bin/opencode")
                                            if p.exists()), None)
    if not exe:
        raise ValueError(f"OpenCode isn't installed on {core.HOST_NAME}")
    r = subprocess.run([exe, "import", str(f)], cwd=folder, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise ValueError(f"opencode import failed: {(r.stderr or r.stdout).strip()[-300:]}")
    f.unlink(missing_ok=True)


async def send(name: str, host: str) -> dict:
    """Move this host's session `name` to `host`: {host, session, warning}. ValueError: why it didn't move."""
    import aiohttp
    peer = next((h for h in core.hosts()["hosts"] if h["name"] == host), None)
    if not peer:
        raise ValueError(f"No Deck host named {host}")
    key = auth.host_key()
    if not key:
        raise ValueError("This host has no host key")
    _, body = await asyncio.to_thread(pack, name)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as s:
            async with s.post(peer["url"].rstrip("/") + "/api/move-in", data=body,
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/octet-stream"}) as r:
                if r.status == 404:
                    raise ValueError(f"{host}'s Deck is too old for Move host: update it first")
                res = await r.json(content_type=None)
                if r.status != 200:
                    raise ValueError(f"{host}: {res.get('error') or r.status}")
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
        raise ValueError(f"Couldn't reach {host} ({e.__class__.__name__}): is it online?")
    # It runs there now: this tab closes, and not into the trash: restoring it would run the conversation twice
    # (user). Moving it back undoes a move.
    await asyncio.to_thread(core.kill_session, name)
    return {"host": host, "session": res.get("session", ""), "warning": res.get("warning", "")}
