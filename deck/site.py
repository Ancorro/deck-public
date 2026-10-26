"""One site on every node (user, 2026-10-26). The Deck's nodes stand in a fixed order (settings `order`, else the
inventory's). The highest-ranked node that is up is the writer: the one that serves the site, the only one that
changes its state. Every other node is a follower holding a byte-for-byte copy, fed by a journal of the writer's
writes. Any node can become the writer when every node above it is down, and hands back as soon as a higher one is
back and caught up. Sessions stay on the node that runs them (a process cannot be copied).

The journal: every change to a shared file goes through `write(rel, data)` (whole file) or `append(rel, data)` (a
file that only grows: chats). Each is one entry {seq, rel, mode, size, data} in DATA/journal.jsonl, applied here and
pushed to each follower over POST /api/site/journal (the host key). A follower that connects sends the seq it has
(GET /api/site/journal?after=N) and gets the rest; one too far behind (the writer's journal was trimmed) takes a full
snapshot (GET /api/site/snapshot, a tar of the shared files) and starts from its seq.

Roles: `role()` is "writer" or "follower"; `writer()` the writer's host entry. A follower answers client writes by
forwarding them to the writer (server.site_forward); reads it answers from its copy. Polling: every node polls the
nodes above it every POLL seconds; after MISSES misses of all of them it takes the writer role. A writer that sees a
higher node answer /api/site/role as writer-ready demotes itself and streams what it wrote meanwhile (its journal
tail) to the new writer, which replays it: a node that wrote while partitioned loses nothing except where both sides
changed the same whole file, where the higher node's copy wins (last writer, as today's sync)."""
import asyncio
import base64
import io
import json
import os
import tarfile
import time
from pathlib import Path

from . import auth, core

POLL = 2  # seconds between polls of the nodes above this one
MISSES = 5  # polls missed by every node above before this one serves (10 s)
KEEP = 20000  # journal entries kept on disk; a follower further behind takes a snapshot
# Shared files: what the journal carries. Chats are append-only (mode "a"); the rest are rewritten whole.
WHOLE = ("settings.json", "buttons.json", "shelf.json", "devices.json", "descriptions.json", "recent.json",
         "takeovers.json", "session-trash.json")
DIRS = ("shelf-files", "saves")  # whole files under these folders
APPEND_DIRS = ("chat",)  # <session>.jsonl and siblings: appended; a shorter file (a restart's rewrite) is sent whole
PER_NODE = ("led", "folder", "pidurable", "hosting")  # settings fields a node keeps for itself (as sync.PER_HOST)

_state: dict = {"role": "follower", "seq": 0, "since": 0.0, "misses": {}, "last_seen": {}}


# ---- order and role ----
def nodes() -> list[str]:
    """Every node's name: the inventory's Deck hosts and every linked PC (deck/links.py), this one included. A linked
    PC whose forwarder is down is still a node (core.hosts leaves it out until the forwarder is up): the order must
    not shift while it is away, or two nodes would each think themselves first."""
    from . import links
    names = [h["name"] for h in core.hosts()["hosts"]]
    for m in links.members():
        if m.get("hosting", True) and m["name"] not in names:
            names.append(m["name"])
    if core.HOST_NAME not in names:
        names.append(core.HOST_NAME)
    return names


def order() -> list[str]:
    """Node names, highest first: settings `order`, then every other node, in inventory order."""
    s = core.load_settings()
    names = nodes()
    chosen = [n for n in (s.get("order") or []) if n in names]
    return chosen + [n for n in names if n not in chosen]


def above(name: str = "") -> list[str]:
    o = order()
    me = name or core.HOST_NAME
    return o[:o.index(me)] if me in o else o


def role() -> str:
    return _state["role"]


def seq() -> int:
    return _state["seq"]


def host_of(name: str) -> dict | None:
    return next((h for h in core.hosts()["hosts"] if h["name"] == name), None)


def writer() -> dict | None:
    """The host entry serving the site: this node when it is the writer, else the highest node seen up as writer."""
    if role() == "writer":
        return host_of(core.HOST_NAME)
    for n in above():
        seen = _state["last_seen"].get(n)
        if seen and seen.get("role") == "writer" and time.time() - seen["at"] < POLL * MISSES:
            return host_of(n)
    return None


def status() -> dict:
    return {"role": role(), "seq": seq(), "since": _state["since"], "order": order(), "writer": (writer() or {}).get("name"),
            "ready": role() == "writer" or _state.get("caught_up", False)}


_running: tuple[float, set] = (0.0, set())  # this node's sessions, asked of tmux at most once a second


def running() -> set:
    global _running
    if time.time() - _running[0] > 1:
        r = core.tmux("list-sessions", "-F", "#{session_name}")
        _running = (time.time(), set(r.stdout.split()) if r.returncode == 0 else set())
    return _running[1]


def live_here(rel: str) -> bool:
    """A chat file this node's own session is writing: never overwritten by the journal or a snapshot (the bridge
    writing it is the truth; the copy is for the other nodes). Any chat/<session>.* of a session running here."""
    top, _, rest = rel.replace("\\", "/").partition("/")
    if top not in APPEND_DIRS or not rest:
        return False
    name = rest.split("/", 1)[0].split(".", 1)[0]
    return bool(name) and name.startswith(core.PREFIX) and name in running()


# ---- the journal ----
def journal_path() -> Path:
    return core.DATA / "journal.jsonl"


def _load_seq() -> int:
    try:
        with open(journal_path(), "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - 4096))
            tail = f.read().splitlines()
        for line in reversed(tail):
            if line.strip():
                return int(json.loads(line)["seq"])
    except (FileNotFoundError, ValueError, KeyError):
        pass
    return 0


def _shared(rel: str) -> bool:
    p = rel.replace("\\", "/")
    if "/" not in p:
        return p in WHOLE
    top = p.split("/", 1)[0]
    return top in DIRS or top in APPEND_DIRS


def _entry(rel: str, mode: str, data: bytes, offset: int = 0) -> dict:
    import base64
    _state["seq"] += 1
    e = {"seq": _state["seq"], "rel": rel, "mode": mode, "offset": offset, "size": len(data),
         "data": base64.b64encode(data).decode(), "at": time.time()}
    with open(journal_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(e) + "\n")
    return e



def record(rel: str, data: bytes, mode: str = "w", offset: int = 0) -> dict | None:
    """Called after a shared file changed here (by the writer, or by this node's own session bridges for chats).
    Journals it for the followers. Not shared: nothing happens."""
    if not _shared(rel):
        return None
    return _entry(rel, mode, data, offset)


def apply(e: dict) -> bool:
    """One journal entry from the writer, applied to this follower's copy. False when it is old (seq not past ours)."""
    import base64
    if int(e["seq"]) <= _state["seq"]:
        return False
    p = core.DATA / e["rel"]
    if live_here(e["rel"]):  # our own session writes this file: the journal's copy (made from our offers) is not newer
        _state["seq"] = int(e["seq"])
        with open(journal_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(e) + "\n")
        return True
    p.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(e["data"])
    if e["rel"] == "settings.json":  # this node's own per-node fields stay
        try:
            mine = json.loads(p.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            mine = {}
        theirs = json.loads(data.decode("utf-8") or "{}")
        merged = {**{k: v for k, v in theirs.items() if k not in PER_NODE}, **{k: mine[k] for k in PER_NODE if k in mine}}
        data = (json.dumps(merged, indent=2) + "\n").encode()
    if e["mode"] == "a":
        have = p.stat().st_size if p.exists() else 0
        if int(e.get("offset", 0)) != have:  # out of step: the next whole send repairs it; skip this tail
            _state["seq"] = int(e["seq"])
            _state.setdefault("broken", set()).add(e["rel"])
            return True
        with open(p, "ab") as f:
            f.write(data)
    else:
        tmp = p.with_name(p.name + ".sitetmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)
        _state.get("broken", set()).discard(e["rel"])
    _state["seq"] = int(e["seq"])
    with open(journal_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(e) + "\n")
    return True


def entries_after(n: int, limit: int = 500) -> list[dict] | None:
    """Journal entries with seq > n, oldest first; None when n is before what is kept (take a snapshot)."""
    out, first = [], None
    try:
        with open(journal_path(), encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                e = json.loads(line)
                if first is None:
                    first = int(e["seq"])
                if int(e["seq"]) > n:
                    out.append(e)
                    if len(out) >= limit:
                        break
    except FileNotFoundError:
        return [] if n == 0 else None
    if first is not None and n < first - 1:  # the entries between n and the first kept are gone: snapshot
        return None
    return out


# ---- noticing changes: shared files are written by many hands (the server, chat bridges in their own processes,
# a hand edit), so the writer journals by watching them, as sync.py watched its documents ----
_seen: dict[str, tuple[int, float]] = {}  # rel -> (size, mtime) last journaled


def _files() -> list[tuple[str, Path]]:
    out = [(rel, core.DATA / rel) for rel in WHOLE]
    for d in (*DIRS, *APPEND_DIRS):
        base = core.DATA / d
        for p in sorted(base.rglob("*")) if base.exists() else []:
            if p.is_file() and not p.name.endswith((".sitetmp", ".tmp")):
                out.append((str(p.relative_to(core.DATA)).replace("\\", "/"), p))
    return out


def _append_only(rel: str) -> bool:
    return rel.split("/", 1)[0] in APPEND_DIRS and rel.endswith(".jsonl")


def scan(first: bool = False) -> int:
    """Journal every shared file changed since the last scan. An append-only file that grew is sent as its new tail;
    one that shrank or changed earlier is sent whole. On the first scan nothing is journaled: the files are the state
    this writer starts from, and a follower behind the journal takes a snapshot. Returns entries written."""
    n = 0
    for rel, p in _files():
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        cur = (st.st_size, st.st_mtime)
        old = _seen.get(rel)
        _seen[rel] = cur
        if first or old == cur:
            continue
        if _append_only(rel) and old and st.st_size > old[0]:
            with open(p, "rb") as f:
                f.seek(old[0])
                data = f.read()
            if data.endswith(b"\n") or data == b"":
                _entry(rel, "a", data, old[0])
                n += 1
                continue
            _seen[rel] = (old[0], cur[1])  # a half-written line: take it next time
            continue
        _entry(rel, "w", p.read_bytes())
        n += 1
    return n


# ---- a follower's own chats: its sessions write them here, and only here. It offers each change to the writer
# (POST /api/site/offer), which journals it as its own, so every node's copy has every chat (user 2026-10-26: a
# sleeping PC's chats showed "no copy here") ----
_mine: dict[str, tuple[int, float]] = {}  # rel -> (size, mtime) last offered


def offers(first: bool = False) -> list[dict]:
    """Changes to the chat files of this node's own sessions since the last call, as journal-shaped entries (seq 0).
    The first call takes every live chat whole: the writer's copy may be from before this node was away."""
    out = []
    for rel, p in _files():
        if not live_here(rel):
            _mine.pop(rel, None)
            continue
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        cur = (st.st_size, st.st_mtime)
        old = _mine.get(rel)
        if old == cur:
            continue
        if not first and _append_only(rel) and old and st.st_size > old[0]:
            with open(p, "rb") as f:
                f.seek(old[0])
                data = f.read()
            if not (data.endswith(b"\n") or data == b""):
                continue  # a half-written line: next time
            _mine[rel] = cur
            out.append({"seq": 0, "rel": rel, "mode": "a", "offset": old[0], "size": len(data),
                        "data": base64.b64encode(data).decode(), "at": time.time()})
            continue
        data = p.read_bytes()
        _mine[rel] = cur
        out.append({"seq": 0, "rel": rel, "mode": "w", "offset": 0, "size": len(data),
                    "data": base64.b64encode(data).decode(), "at": time.time()})
    return out


def take_offer(entries: list[dict]) -> dict:
    """On the writer: a follower's own chats, journaled here as our own (replay does the same for a handover).
    {taken, whole: [rel]}: a tail that does not fit our copy is refused, and the follower sends that file whole."""
    taken, whole = 0, []
    for e in entries:
        if not _shared(e.get("rel", "")) or live_here(e["rel"]):
            continue
        data = base64.b64decode(e["data"])
        p = core.DATA / e["rel"]
        p.parent.mkdir(parents=True, exist_ok=True)
        if e.get("mode") == "a":
            if not p.exists() or p.stat().st_size != int(e.get("offset", 0)):
                whole.append(e["rel"])
                continue
            with open(p, "ab") as f:
                f.write(data)
            record(e["rel"], data, "a", int(e["offset"]))
        else:
            tmp = p.with_name(p.name + ".sitetmp")
            tmp.write_bytes(data)
            os.replace(tmp, p)
            record(e["rel"], data, "w")
        st = p.stat()
        _seen[e["rel"]] = (st.st_size, st.st_mtime)  # scan must not journal it a second time
        taken += 1
    return {"taken": taken, "whole": whole}


# ---- a dead node's chats, read from the copy every node holds ----
def copied_chats(host: str) -> list[dict]:
    """Session rows for the chats of `host` in this node's copy (their .state names the node that ran them), for the
    tab column when that node is down and nothing was cached of its list (it died before this node polled it)."""
    out = []
    base = core.DATA / "chat"
    for st_file in sorted(base.glob("deck-*.state")) if base.exists() else []:
        try:
            st = json.loads(st_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = st_file.name[:-len(".state")]
        chat = base / f"{name}.jsonl"
        if st.get("host") != host or not chat.exists():
            continue
        out.append({"name": name, "label": st.get("name") or name, "type": st.get("agent") or "claude", "button": "",
                    "created": int(chat.stat().st_mtime), "activity": int(chat.stat().st_mtime), "running": True,
                    "exit": None, "attached": 0, "deck": True, "chat": True, "title": st.get("name") or name,
                    "project": "", "status": {}, "state": "idle", "preview": st.get("preview", ""), "snippet": "", "options": []})
    return out


def chat_text(session: str, limit: int = 300_000) -> dict:
    """{text, size, mtime}: a chat's events rendered as text (the user's messages as "> ", the agent's replies; tool
    calls and thinking left out), from this node's copy of chat/<session>.jsonl."""
    p = core.DATA / "chat" / f"{session}.jsonl"
    out = []
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        raise ValueError("no copy of that chat here")
    msgs: dict = {}  # m index -> text so far (a msg event replaces its earlier deltas and versions)
    order_ = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("t") == "clear":
            msgs, order_ = {}, []
        elif ev.get("t") == "user":
            key = ("u", len(order_))
            msgs[key] = "> " + str(ev.get("text", "")).replace("\n", "\n> ")
            order_.append(key)
        elif ev.get("t") == "msg":
            key = ("m", ev.get("m"))
            text = "\n".join(b.get("text", "") for b in ev.get("blocks", []) if b.get("kind") == "text").strip()
            if key not in msgs:
                order_.append(key)
            msgs[key] = text
    out = [msgs[k] for k in order_ if msgs.get(k)]
    st = p.stat()
    return {"text": "\n\n".join(out)[-limit:], "size": st.st_size, "mtime": st.st_mtime}


# ---- carrying a dead node's chat on here, from the copy (step 4 of the plan) ----
def takeovers() -> list[dict]:
    try:
        items = json.loads((core.DATA / "takeovers.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return [t for t in items if time.time() - t.get("at", 0) < 30 * 86400]


def move_here(host: str, session: str) -> dict:
    """A dead node's chat carried on here: its agent conversation is rebuilt from the chat events in the copy and the
    session restarts on it (session_move.receive). Recorded in takeovers.json, which travels with the state, so the
    node closes its own tab when it is back (give_up). {session, folder, warning}."""
    from . import session_move
    chat = core.DATA / "chat" / f"{session}.jsonl"
    state_file = core.DATA / "chat" / f"{session}.state"
    try:
        st = json.loads(state_file.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        raise ValueError(f"no copy of that chat from {host} here")
    conv, kind = st.get("resume"), st.get("agent") or ""
    if not conv:
        raise ValueError("that chat had not started a conversation yet")
    if not chat.exists():
        raise ValueError(f"no copy of that chat from {host} here")
    if kind not in session_move.AGENTS:
        # the .state file carries no agent name on older bridges: the session name says (deck-<agent>-<n>)
        kind = next((a for a in session_move.AGENTS if session.startswith(f"deck-{a}-")), "")
        if not kind:
            raise ValueError("Transfer here works in Claude, Codex, OpenCode and Pi chats")
    conv_file, rel = rebuild_conversation(kind, conv, chat, st)
    item = {"type": kind, "label": st.get("name") or kind, "from": host, "folder": st.get("folder", "~"),
            "yolo": bool(st.get("yolo")), "state": {"resume": conv, "prefs": st.get("prefs") or {}, "name": st.get("name", "")},
            "offline": chat.stat().st_mtime}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = json.dumps(item).encode()
        info = tarfile.TarInfo("item.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        tar.add(str(conv_file), arcname=f"files/{rel}", recursive=False)
    res = session_move.receive(buf.getvalue())
    items = [*takeovers(), {"host": host, "session": session, "conv": conv, "by": core.HOST_NAME, "at": time.time()}]
    (core.DATA / "takeovers.json").write_text(json.dumps(items) + "\n", encoding="utf-8")
    return res


def rebuild_conversation(kind: str, conv: str, chat: Path, st: dict) -> tuple[Path, str]:
    """The agent's own conversation file for `conv`, made from the Deck chat events: enough for the agent to resume
    with the history in front of it. Claude: a transcript of user and assistant turns; Codex: a rollout; Pi and
    OpenCode: their session JSON. Written under DATA/chat/rebuilt/."""
    out_dir = core.DATA / "chat" / "rebuilt"
    out_dir.mkdir(parents=True, exist_ok=True)
    turns = []
    for line in chat.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("t") == "clear":
            turns = []
        elif ev.get("t") == "user":
            turns.append(("user", str(ev.get("text", "")), ev.get("ts", 0)))
        elif ev.get("t") == "msg":
            text = "\n".join(b.get("text", "") for b in ev.get("blocks", []) if b.get("kind") == "text").strip()
            if turns and turns[-1][0] == "assistant" and turns[-1][2] == ev.get("m"):
                turns[-1] = ("assistant", text, ev.get("m"))
            elif text:
                turns.append(("assistant", text, ev.get("m")))
    if kind == "claude":
        f = out_dir / f"{conv}.jsonl"
        with open(f, "w", encoding="utf-8") as fh:
            parent = None
            for i, (role, text, _) in enumerate(turns):
                uid = f"{conv}-{i}"
                fh.write(json.dumps({"type": role, "uuid": uid, "parentUuid": parent, "sessionId": conv,
                                     "message": {"role": role, "content": [{"type": "text", "text": text}]}}) + "\n")
                parent = uid
        return f, f.name
    if kind == "codex":
        f = out_dir / f"rollout-{conv}.jsonl"
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "session_meta", "payload": {"id": conv}}) + "\n")
            for role, text, _ in turns:
                fh.write(json.dumps({"type": "response_item", "payload": {"type": "message", "role": role,
                                                                            "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}) + "\n")
        return f, f.name
    if kind == "pi":
        f = out_dir / conv
        f.write_text(json.dumps({"messages": [{"role": r, "content": t} for r, t, _ in turns]}), encoding="utf-8")
        return f, f.name
    f = out_dir / f"{conv}.json"  # opencode export
    f.write_text(json.dumps({"id": conv, "messages": [{"role": r, "content": t} for r, t, _ in turns]}), encoding="utf-8")
    return f, f.name


def give_up() -> list[str]:
    """Back online: this node's chats another node took over meanwhile close (not into the trash: the conversation
    runs in one place only). Returns them."""
    closed = []
    for t in takeovers():
        name = str(t.get("session", ""))
        if t.get("host") != core.HOST_NAME or not core.session_exists(name):
            continue
        if core.chat_state(name).get("resume") != t.get("conv"):
            continue  # that tab is on another conversation now
        core.kill_session(name)
        closed.append(name)
    return closed


def trim() -> None:
    p = journal_path()
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    if len(lines) > KEEP:
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(lines[-KEEP:]) + "\n", encoding="utf-8")
        os.replace(tmp, p)


def snapshot() -> bytes:
    """Every shared file, as a gzipped tar, with the seq it is at (in seq.txt)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in WHOLE:
            p = core.DATA / rel
            if p.exists():
                tar.add(str(p), arcname=rel)
        for d in (*DIRS, *APPEND_DIRS):
            base = core.DATA / d
            for p in sorted(base.rglob("*")) if base.exists() else []:
                if p.is_file():
                    tar.add(str(p), arcname=str(p.relative_to(core.DATA)).replace("\\", "/"))
        s = str(_state["seq"]).encode()
        info = tarfile.TarInfo("seq.txt")
        info.size = len(s)
        tar.addfile(info, io.BytesIO(s))
    return buf.getvalue()


def restore(body: bytes) -> int:
    """A snapshot from the writer replaces this follower's shared files. Returns the seq it is now at."""
    tar = tarfile.open(fileobj=io.BytesIO(body), mode="r:gz")
    n = 0
    mine = {}
    try:
        mine = {k: v for k, v in json.loads((core.DATA / "settings.json").read_text(encoding="utf-8")).items() if k in PER_NODE}
    except (FileNotFoundError, ValueError):
        pass
    for m in tar.getmembers():
        rel = m.name.replace("\\", "/")
        if rel == "seq.txt":
            n = int(tar.extractfile(m).read().decode().strip() or 0)
            continue
        if not m.isfile() or rel.startswith("/") or ".." in rel.split("/") or not _shared(rel) or live_here(rel):
            continue
        p = core.DATA / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = tar.extractfile(m).read()
        if rel == "settings.json":
            data = (json.dumps({**json.loads(data.decode("utf-8") or "{}"), **mine}, indent=2) + "\n").encode()
        p.write_bytes(data)
    _state["seq"] = n
    _state["broken"] = set()
    journal_path().write_text("", encoding="utf-8")
    return n


# ---- becoming the writer, and handing back ----
def become_writer() -> None:
    if _state["role"] != "writer":
        _state["role"], _state["since"] = "writer", time.time()
        _state["caught_up"] = True
        print(f"site: {core.HOST_NAME} serves the site now (seq {_state['seq']})", flush=True)


def become_follower(of: str) -> None:
    if _state["role"] != "follower" or _state.get("of") != of:
        _state["role"], _state["since"], _state["of"] = "follower", time.time(), of
        _state["caught_up"] = False
        print(f"site: {core.HOST_NAME} follows {of}", flush=True)


def _auth():
    return {"Authorization": f"Bearer {auth.host_key()}"}


async def _poll(http, name: str) -> dict | None:
    import aiohttp
    h = host_of(name)
    if not h or not h.get("url"):
        return None
    try:
        async with http.get(f"{h['url'].rstrip('/')}/api/site/role", headers=_auth(),
                            timeout=aiohttp.ClientTimeout(total=POLL * 0.9, sock_connect=1.5)) as r:
            if r.status == 200:
                return await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        pass
    return None


async def _pull(http, h: dict) -> None:
    """Catch this follower up from its writer: the journal after our seq, or a snapshot when too far behind."""
    import aiohttp
    base = h["url"].rstrip("/")
    while True:
        async with http.get(f"{base}/api/site/journal", params={"after": str(_state["seq"])}, headers=_auth(),
                            timeout=aiohttp.ClientTimeout(total=60, sock_connect=3)) as r:
            if r.status == 410:  # too far behind
                async with http.get(f"{base}/api/site/snapshot", headers=_auth(),
                                    timeout=aiohttp.ClientTimeout(total=600, sock_connect=3)) as s:
                    body = await s.read()
                await asyncio.to_thread(restore, body)
                continue
            if r.status != 200:
                return
            got = await r.json()
        for e in got["entries"]:
            await asyncio.to_thread(apply, e)
        if not got.get("more"):
            _state["caught_up"] = True
            return


_sent: dict[str, int] = {}  # follower -> the seq it has acknowledged


async def _push(http) -> None:
    """The writer sends each follower the journal entries past the seq it last acknowledged. A follower that is down
    is skipped; it catches up from the journal (or a snapshot) when it polls us again."""
    import aiohttp
    for h in [h for h in core.hosts()["hosts"] if not h["current"]]:
        have = _sent.get(h["name"], 0)
        if have >= _state["seq"]:
            continue
        batch = entries_after(have, limit=200)
        if batch is None:  # it is further behind than the journal: it pulls a snapshot itself
            continue
        try:
            async with http.post(f"{h['url'].rstrip('/')}/api/site/journal", json={"entries": batch}, headers=_auth(),
                                 timeout=aiohttp.ClientTimeout(total=30, sock_connect=2)) as r:
                if r.status == 200:
                    _sent[h["name"]] = int((await r.json()).get("seq", have))
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
            pass


async def tick(http) -> None:
    """One round: poll the nodes above; decide the role; followers pull, the writer pushes."""
    ups = []
    for n in above():
        got = await _poll(http, n)
        if got:
            _state["misses"][n] = 0
            _state["last_seen"][n] = {**got, "at": time.time()}
            ups.append((n, got))
        else:
            _state["misses"][n] = _state["misses"].get(n, 0) + 1
    ready_above = next((n for n, got in ups if got.get("role") == "writer" and got.get("ready")), None)
    if ready_above:
        if role() == "writer":  # a higher node is back and serving: hand it what we wrote, then follow
            await asyncio.to_thread(scan)  # a change made this very second is journaled too, or it would be lost
            await _handover(http, host_of(ready_above))
        become_follower(ready_above)
        await _pull(http, host_of(ready_above))
        await _offer(http, host_of(ready_above))
    elif all(_state["misses"].get(n, 0) >= MISSES for n in above()):
        become_writer()
    if role() == "writer":
        await asyncio.to_thread(scan)
        await _push(http)


async def _offer(http, h: dict) -> None:
    """A follower's own chats to the writer (take_offer): what changed since the last offer, or a file whole when the
    writer's copy did not take its tail."""
    import aiohttp
    first = not _state.get("offered")
    entries = await asyncio.to_thread(offers, first)
    if not entries:
        return
    try:
        async with http.post(f"{h['url'].rstrip('/')}/api/site/offer", json={"entries": entries}, headers=_auth(),
                             timeout=aiohttp.ClientTimeout(total=60, sock_connect=3)) as r:
            if r.status != 200:
                for e in entries:
                    _mine.pop(e["rel"], None)  # not taken (the writer changed under us): offer again
                return
            got = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        for e in entries:
            _mine.pop(e["rel"], None)
        return
    _state["offered"] = True
    for rel in got.get("whole", []):
        _mine.pop(rel, None)  # offers() sends it whole next, since it has no old size for it


async def _handover(http, h: dict) -> None:
    """This node wrote while a higher one was away. Replay our journal since we became writer onto it."""
    import aiohttp
    since = _state["since"]
    mine = [e for e in (entries_after(0, limit=KEEP) or []) if e.get("at", 0) >= since]
    if not mine:
        return
    try:
        async with http.post(f"{h['url'].rstrip('/')}/api/site/replay", json={"entries": mine}, headers=_auth(),
                             timeout=aiohttp.ClientTimeout(total=120, sock_connect=3)) as r:
            await r.read()
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        print("site: handover failed; the higher node's copy wins", flush=True)


def replay(entries: list[dict]) -> int:
    """On the returning writer: entries a lower node wrote while we were away, re-journaled here as our own so every
    follower gets them. Appends land as appends; whole files replace ours (the lower node's view was the live one)."""
    import base64
    n = 0
    for e in sorted(entries, key=lambda x: int(x["seq"])):
        data = base64.b64decode(e["data"])
        p = core.DATA / e["rel"]
        p.parent.mkdir(parents=True, exist_ok=True)
        if e["mode"] == "a":
            with open(p, "ab") as f:
                f.write(data)
            record(e["rel"], data, "a", p.stat().st_size - len(data))
        else:
            p.write_bytes(data)
            record(e["rel"], data, "w")
        n += 1
    return n


async def loop(app) -> None:
    _state["seq"] = _load_seq()
    await asyncio.to_thread(scan, True)  # the files as they are: the state this node starts from
    if not above():  # top of the order: the writer from the start
        become_writer()
    await asyncio.sleep(3)
    last_trim = last_give = 0.0
    while True:
        try:
            if auth.host_key():
                await tick(app["peer_http"])
            if time.time() - last_give > 30:  # first thing after a start: was anything taken over while away?
                last_give = time.time()
                for name in await asyncio.to_thread(give_up):
                    print(f"site: {name} was taken over by another node; closed here", flush=True)
            if time.time() - last_trim > 600:
                last_trim = time.time()
                await asyncio.to_thread(trim)
        except Exception as e:  # the site loop must never take the server down
            print(f"site: {e!r}", flush=True)
        await asyncio.sleep(POLL)
