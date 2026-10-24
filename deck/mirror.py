"""Chat copies on every host (user): each host copies its open agent chats' conversations to the others whenever one
has changed and the agent isn't mid-reply, so when a host is offline its chats can carry on elsewhere. A conversation
file only grows, so after the first copy only its new end travels (POST /api/mirror, with the offset the other host
already has; a mismatch, or a different conversation, sends it whole). OpenCode's export is rewritten, not grown: it
always goes whole.

Takeover: on a host whose peer is offline, Move here starts that chat from its copy (session_move.receive, told it
moved and from when) and records the takeover. The original host, back online, finds the record (GET /api/takeovers
on its peers, each sync round) and closes its own tab (not into its trash), so one conversation never runs twice."""
import asyncio
import gzip
import io
import json
import re
import tarfile
import time
from pathlib import Path

from . import auth, core, session_move

EVERY = 10  # seconds between looks for changed chats
KEEP = 30 * 86400  # copies and takeover records older than this go
NAME = re.compile(r"[\w-][\w.-]*")
STAMP = re.compile(r"^\[\w{3} \d{4}-\d\d-\d\d \d\d:\d\d [+-]\d{4}[^\]]*\] ?")  # as chat_bridge.STAMP  # never "." or "..": host and session are path parts
_sent: dict[tuple, int] = {}  # (peer, session, conv) -> bytes that peer has
_done: dict[str, float] = {}  # session -> conversation mtime every peer has


def root() -> Path:
    return core.DATA / "mirrors"


def _paths(host: str, session: str) -> tuple[Path, Path]:
    if not (NAME.fullmatch(host) and NAME.fullmatch(session)):
        raise ValueError("bad host or session name")
    d = root() / host
    return d / f"{session}.json", d / f"{session}.data"


def chats() -> list[tuple[str, dict, Path]]:
    """This host's open chats that can be copied: (session, item, conversation file)."""
    out = []
    for s in core.list_sessions():
        if not (s["chat"] and s["running"] and s["type"] in session_move.AGENTS) or s.get("state") == "working":
            continue
        try:
            item = session_move.item_of(s["name"])
            files = session_move.files_of(s["name"], item["type"], item["state"]["resume"])
        except (ValueError, KeyError):
            continue
        rel, f = next(iter(files.items()))
        out.append((s["name"], {**item, "rel": rel, "title": s.get("title") or item.get("label"),
                                **({"whole": True} if item["type"] == "opencode" else {})}, f))
    return out


# ---- the host keeping a copy ----
def take(host: str, session: str, meta: dict, offset: int, data: bytes) -> int:
    """Another host's chat, or its new end. Returns the bytes now kept; ValueError when offset doesn't fit (send all)."""
    jpath, dpath = _paths(host, session)
    try:
        old = json.loads(jpath.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        old = {}
    have = dpath.stat().st_size if dpath.exists() and old else 0
    same = old.get("state", {}).get("resume") == meta.get("state", {}).get("resume")
    if offset and (not same or offset != have):
        raise ValueError(f"have {have if same else 0}")
    jpath.parent.mkdir(parents=True, exist_ok=True)
    with open(dpath, "ab" if offset else "wb") as f:
        f.write(data)
    size = dpath.stat().st_size
    jpath.write_text(json.dumps({**meta, "size": size, "synced": time.time()}), encoding="utf-8")
    return size


def copies(host: str) -> dict:
    """session -> when last copied, for one host's chats kept here."""
    out = {}
    for j in (root() / host).glob("*.json") if NAME.fullmatch(host) else []:
        try:
            out[j.stem] = json.loads(j.read_text(encoding="utf-8")).get("synced", 0)
        except ValueError:
            pass
    return out


def prune() -> None:
    now = time.time()
    for j in root().glob("*/*.json"):
        if now - j.stat().st_mtime > KEEP:
            j.unlink(missing_ok=True)
            j.with_suffix(".data").unlink(missing_ok=True)


# ---- the phone's copies (user): it keeps every chat it has seen, to read offline and to hand to the next host ----
def carry_list() -> list[dict]:
    """Every chat this host can give the phone: its own open chats and its copies of other hosts'."""
    out = []
    for session, item, f in chats():
        out.append({"host": core.HOST_NAME, "session": session, "conv": item["state"]["resume"], "type": item["type"],
                    "title": item.get("title", ""), "synced": f.stat().st_mtime, "size": f.stat().st_size})
    for j in root().glob("*/*.json"):
        try:
            m = json.loads(j.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"host": j.parent.name, "session": j.stem, "conv": m["state"]["resume"], "type": m["type"],
                    "title": m.get("title", ""), "synced": m.get("synced", 0), "size": m.get("size", 0)})
    return out


def carry_get(host: str, session: str) -> dict:
    """One chat for the phone: {item, data} (data: the conversation, gzipped, base64)."""
    import base64
    if host == core.HOST_NAME:
        found = next(((item, f) for s, item, f in chats() if s == session), None)
        if not found:
            raise ValueError("no such chat here")
        item, f = found
        item = {**item, "synced": f.stat().st_mtime}
    else:
        jpath, f = _paths(host, session)
        try:
            item = json.loads(jpath.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            raise ValueError("no copy of that chat here")
    return {"item": {**item, "host": host, "session": session},
            "data": base64.b64encode(gzip.compress(f.read_bytes(), 5)).decode()}


def carry_put(raw: dict) -> bool:
    """A chat the phone carried here: kept as a copy when this host has none of it, or an older one. True if kept."""
    import base64
    item = raw.get("item") or {}
    host, session = str(item.get("host", "")), str(item.get("session", ""))
    if host == core.HOST_NAME or not item.get("rel"):
        return False  # its own chats: the live one is newer
    jpath, _ = _paths(host, session)
    try:
        old = json.loads(jpath.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        old = {}
    same = old.get("state", {}).get("resume") == item.get("state", {}).get("resume")
    if old and same and old.get("synced", 0) >= float(item.get("synced") or 0):
        return False
    meta = {k: v for k, v in item.items() if k not in ("host", "session", "synced", "size")}
    take(host, session, meta, 0, gzip.decompress(base64.b64decode(raw.get("data", ""))))
    kept = json.loads(jpath.read_text(encoding="utf-8"))
    jpath.write_text(json.dumps({**kept, "synced": float(item.get("synced") or kept["synced"])}), encoding="utf-8")
    return True


def copy_text(host: str, session: str, limit: int = 300_000) -> dict:
    """An offline host's chat, read only, from its copy: {title, type, synced, text} (the user's messages as "> ",
    the agent's replies; tool calls and thinking left out)."""
    jpath, dpath = _paths(host, session)
    try:
        meta = json.loads(jpath.read_text(encoding="utf-8"))
        lines = dpath.read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, ValueError):
        raise ValueError(f"no copy of that chat from {host} here")
    out = []
    if meta["type"] == "opencode":  # its export: one JSON, {info, messages: [{info, parts}]}
        try:
            msgs = json.loads("\n".join(lines)).get("messages") or []
        except (ValueError, AttributeError):
            msgs = []
        lines = [json.dumps({"opencode": m}) for m in msgs]
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if meta["type"] == "opencode":
            m = e["opencode"]
            role = (m.get("info") or {}).get("role")
            texts = [p.get("text", "") for p in m.get("parts") or [] if p.get("type") == "text" and not p.get("synthetic")]
        elif meta["type"] == "pi":  # its session file: message entries
            m = (e.get("message") or {}) if e.get("type") == "message" else {}
            role, content = m.get("role"), m.get("content")
            texts = [content] if isinstance(content, str) else [b.get("text", "") for b in content or []
                                                               if isinstance(b, dict) and b.get("type") == "text"]
        elif meta["type"] == "codex":  # its rollout: response items
            p = e.get("payload") or {}
            role, content = p.get("role"), p.get("content") if p.get("type") == "message" else None
            texts = [b.get("text", "") for b in content or [] if b.get("type") in ("input_text", "output_text")]
        else:  # Claude's transcript
            m = e.get("message") or {}
            role, content = m.get("role"), m.get("content")
            if e.get("isMeta") or e.get("type") not in ("user", "assistant"):
                continue
            texts = [content] if isinstance(content, str) else [b.get("text", "") for b in content or []
                                                                   if isinstance(b, dict) and b.get("type") == "text"]
        text = STAMP.sub("", "\n".join(t for t in texts if t.strip()).strip(), count=1)  # Deck's stamp on what was typed
        if not text or role not in ("user", "assistant") or text.startswith(("<environment_context", "<user_instructions",
                                                                               "<permissions", "# AGENTS.md")):
            continue
        out.append("> " + text.replace("\n", "\n> ") if role == "user" else text)
    return {"title": meta.get("title", ""), "type": meta["type"], "synced": meta.get("synced", 0),
            "text": "\n\n".join(out)[-limit:]}


# ---- takeover ----
def takeovers_path() -> Path:
    return core.DATA / "takeovers.json"


def takeovers() -> list[dict]:
    try:
        items = json.loads(takeovers_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return [t for t in items if time.time() - t.get("at", 0) < KEEP]


def move_here(host: str, session: str) -> dict:
    """Carry on an offline host's chat here from its copy; {session, folder, warning}."""
    jpath, dpath = _paths(host, session)
    try:
        meta = json.loads(jpath.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        raise ValueError(f"no copy of that chat from {host} here")
    item = {**meta, "offline": meta.get("synced")}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = json.dumps(item).encode()
        info = tarfile.TarInfo("item.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        tar.add(str(dpath), arcname=f"files/{meta['rel']}", recursive=False)
    res = session_move.receive(buf.getvalue())
    items = [*takeovers(), {"host": host, "session": session, "conv": meta["state"]["resume"], "by": core.HOST_NAME,
                            "at": time.time()}]
    takeovers_path().write_text(json.dumps(items) + "\n", encoding="utf-8")
    jpath.unlink(missing_ok=True)
    dpath.unlink(missing_ok=True)
    return res


def give_up(records: list[dict]) -> list[str]:
    """Back online: this host's chats another host took over meanwhile close into the trash. Returns them."""
    closed = []
    for t in records:
        name = str(t.get("session", ""))
        if t.get("host") != core.HOST_NAME or not core.session_exists(name):
            continue
        if core.chat_state(name).get("resume") != t.get("conv"):
            continue  # that tab is on another conversation now
        core.kill_session(name)  # not into the trash: restoring it would run the conversation twice
        closed.append(name)
    return closed


# ---- the host sending copies ----
def _auth():
    return {"Authorization": f"Bearer {auth.host_key()}"}


async def _send(http, h: dict, session: str, item: dict, f: Path) -> bool:
    import aiohttp
    conv = item["state"]["resume"]
    key = (h["name"], session, conv)
    size = f.stat().st_size
    for offset in (0,) if item.get("whole") else (_sent.get(key, 0), 0):
        if offset > size:
            continue
        with open(f, "rb") as fh:
            fh.seek(offset)
            data = fh.read(size - offset)
        meta = json.dumps({**item, "offset": offset})
        try:
            async with http.post(f"{h['url'].rstrip('/')}/api/mirror/{core.HOST_NAME}/{session}", data=gzip.compress(data, 5),
                                 headers={**_auth(), "X-Deck-Mirror": meta, "Content-Type": "application/gzip"},
                                 timeout=aiohttp.ClientTimeout(total=300, sock_connect=4)) as r:
                if r.status == 200:
                    _sent[key] = (await r.json())["size"]
                    return True
                if r.status != 409:
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, KeyError):
            return False
    return False


async def round_(http) -> None:
    peers = [h for h in core.hosts()["hosts"] if not h["current"]]
    if not peers or not auth.host_key():
        return
    for session, item, f in await asyncio.to_thread(chats):
        m = f.stat().st_mtime
        if _done.get(session) == m:
            continue
        ok = [await _send(http, h, session, item, f) for h in peers]
        if all(ok):
            _done[session] = m


async def check_takeovers(http) -> None:
    import aiohttp
    for h in [h for h in core.hosts()["hosts"] if not h["current"]]:
        try:
            async with http.get(f"{h['url'].rstrip('/')}/api/takeovers", headers=_auth(),
                                timeout=aiohttp.ClientTimeout(total=8, sock_connect=4)) as r:
                if r.status == 200:
                    closed = await asyncio.to_thread(give_up, await r.json())
                    for name in closed:
                        print(f"mirror: {name} was taken over by {h['name']}; closed here", flush=True)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            pass


async def loop(app) -> None:
    last_take = 0.0
    await asyncio.sleep(5)
    while True:
        try:
            if time.time() - last_take > 30:  # first thing after a start: was anything taken over while away?
                last_take = time.time()
                await check_takeovers(app["peer_http"])
                await asyncio.to_thread(prune)
            await round_(app["peer_http"])
        except Exception as e:  # copying must never take the server down
            print(f"mirror: {e!r}", flush=True)
        await asyncio.sleep(EVERY)
