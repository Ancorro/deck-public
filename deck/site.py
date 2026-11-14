"""One site on every node (user, 2026-10-26). The Deck's nodes stand in a fixed order (settings `order`, else the
inventory's). The highest-ranked node that is up is the writer: the one that serves the site, the only one that
changes its state. Every other node is a follower holding a byte-for-byte copy, fed by a journal of the writer's
writes. Any node can become the writer when every node above it is down, and hands back as soon as a higher one is
back and caught up. Sessions stay on the node that runs them (a process cannot be copied), and so do their chats:
they are not copied to the other nodes (user 2026-11-04: every chat line went to every node, and each one moved the
journal, so every open page reloaded the shared state every 2 seconds while any agent worked).

The journal: every change to a shared file is one entry {seq, rel, mode, size, data} in DATA/journal.jsonl, applied here and
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
import io
import json
import os
import re
import tarfile
import threading
import time
from pathlib import Path

from . import auth, config, settings

POLL = 2  # seconds between polls of the nodes above this one
MISSES = 5  # polls missed by every node above before this one serves (10 s)
KEEP = 20000  # journal entries kept on disk; a follower further behind takes a snapshot
KEEP_BYTES = 32 << 20  # and at most this much: whole shelf.json copies made a 244 MB journal that every tick parsed
#                        line by line and trim read whole, holding Deck at 1.4 GB on a small VM (2026-11-01)
_SEQ = re.compile(rb'^\{"seq": (\d+),')
# Shared files: what the journal carries, each rewritten whole.
WHOLE = ("settings.json", "buttons.json", "shelf.json", "board.json", "devices.json", "descriptions.json",
         "recent.json")
DIRS = ("shelf-files", "saves")  # whole files under these folders
PER_NODE = ("led", "folder", "pidurable", "hosting")  # settings fields a node keeps for itself (as sync.PER_HOST)

_state: dict = {"role": "follower", "seq": 0, "since": 0.0, "misses": {}, "last_seen": {}}
# journal.jsonl is appended from several threads (scan, apply, replay run in asyncio.to_thread) and rewritten
# by trim: one at a time, or lines interleave and get cut (one cut line stopped a node's site loop for good)
_jlock = threading.RLock()


# ---- order and role ----
def nodes() -> list[str]:
    """Every node's name: the inventory's Deck hosts and every linked PC (deck/links.py), this one included. A linked
    PC whose forwarder is down is still a node (settings.hosts leaves it out until the forwarder is up): the order must
    not shift while it is away, or two nodes would each think themselves first."""
    from . import links
    names = [h["name"] for h in settings.hosts()["hosts"]]
    for m in links.members():
        if m.get("hosting", True) and m["name"] not in names:
            names.append(m["name"])
    if config.HOST_NAME not in names:
        names.append(config.HOST_NAME)
    return names


def order() -> list[str]:
    """Node names, highest first: settings `order`, then every other node, in inventory order."""
    s = settings.load_settings()
    names = nodes()
    chosen = [n for n in (s.get("order") or []) if n in names]
    return chosen + [n for n in names if n not in chosen]


def above(name: str = "") -> list[str]:
    o = order()
    me = name or config.HOST_NAME
    return o[:o.index(me)] if me in o else o


def role() -> str:
    return _state["role"]


def seq() -> int:
    return _state["seq"]


def host_of(name: str) -> dict | None:
    return next((h for h in settings.hosts()["hosts"] if h["name"] == name), None)


def writer() -> dict | None:
    """The host entry serving the site: this node when it is the writer, else the highest node seen up as writer."""
    if role() == "writer":
        return host_of(config.HOST_NAME)
    for n in above():
        seen = _state["last_seen"].get(n)
        if seen and seen.get("role") == "writer" and time.time() - seen["at"] < POLL * MISSES:
            return host_of(n)
    return None


def status() -> dict:
    return {"role": role(), "seq": seq(), "since": _state["since"], "order": order(), "writer": (writer() or {}).get("name"),
            "ready": role() == "writer" or _state.get("caught_up", False),
            "saved": bool(settings.load_settings().get("order"))}  # its order is the one set, not just the inventory's


def adopt_order(got: dict) -> bool:
    """A node with no order saved (its copy lost it) takes the one a serving node has saved: else it ranks itself by
    the inventory alone, serves its own stale copy too, and two nodes write at once (user 2026-10-30: "settings reset
    on build"). False when there is nothing to take."""
    if settings.load_settings().get("order") or not (got.get("role") == "writer" and got.get("saved") and got.get("order")):
        return False
    settings.save_settings({"order": got["order"]})
    print(f"site: took the node order from the writer: {', '.join(got['order'])}", flush=True)
    return True


def journal_path() -> Path:
    return config.DATA / "journal.jsonl"


def _load_seq() -> int:
    """The last seq in the journal, read from the start of its last whole lines. Lines can be megabytes (a whole
    shelf.json copy): reading only the last 4 KB found no line start, so a restarted writer went back to seq 0 while
    its journal still began at 1455, and every follower asked for entries past its seq, was told to take a snapshot,
    took one, and asked again, forever, never naming a writer (2026-11-06: shelf and Board writes stayed local)."""
    try:
        with open(journal_path(), "rb") as f:
            for line in reversed(_last_line_starts(f)):
                n = _seq_of(line)
                if n is not None:
                    return n
    except FileNotFoundError:
        pass
    return 0


def _last_line_starts(f, want: int = 8, chunk: int = 1 << 16) -> list[bytes]:
    """The first 64 bytes of each of the last `want` lines of a binary file, oldest first. Scans backward for
    newlines in chunks (never holding a long line), then reads each line's start."""
    f.seek(0, os.SEEK_END)
    end = f.tell()
    offsets, pos = [], end
    while pos > 0 and len(offsets) < want:
        step = min(chunk, pos)
        pos -= step
        f.seek(pos)
        block = f.read(step)
        i = len(block)
        while len(offsets) < want:
            i = block.rfind(b"\n", 0, i)
            if i < 0:
                break
            if pos + i + 1 < end:  # a newline that ends the file starts no line
                offsets.append(pos + i + 1)
    if pos == 0 and len(offsets) < want and end:
        offsets.append(0)
    out = []
    for o in sorted(set(offsets)):
        f.seek(o)
        out.append(f.read(64))
    return out


def _shared(rel: str) -> bool:
    p = rel.replace("\\", "/")
    if "/" not in p:
        return p in WHOLE
    top = p.split("/", 1)[0]
    return top in DIRS


def _entry(rel: str, mode: str, data: bytes, offset: int = 0) -> dict:
    import base64
    with _jlock:
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


def _replace(tmp: Path, p: Path) -> None:
    """os.replace, retried: on Windows it fails while another process has the file open (a page's read of
    settings.json, the virus scanner), and a dropped settings write left that host on the old look and theme."""
    for wait in (0.02, 0.05, 0.1, 0.2, 0.4, 0.8):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(wait)
    os.replace(tmp, p)


def _merge_settings(data: bytes, p: Path) -> bytes:
    """The shared settings with this node's own fields kept, and the node order kept when theirs has none: without it
    a node ranks itself first and serves its own stale copy after a restart (user 2026-10-30: "settings reset")."""
    try:
        mine = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        mine = {}
    theirs = json.loads(data.decode("utf-8") or "{}")
    merged = {**{k: v for k, v in theirs.items() if k not in PER_NODE}, **{k: mine[k] for k in PER_NODE if k in mine}}
    if not merged.get("order") and mine.get("order"):
        merged["order"] = mine["order"]
    return (json.dumps(merged, indent=2) + "\n").encode()


def apply(e: dict) -> bool:
    """One journal entry from the writer, applied to this follower's copy. False when it is old (seq not past ours)."""
    import base64
    if int(e["seq"]) <= _state["seq"]:
        return False
    p = config.DATA / e["rel"]
    if not _shared(e["rel"]):  # an older writer's chat entry: chats are not copied any more
        _state["seq"] = int(e["seq"])
        with _jlock, open(journal_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(e) + "\n")
        return True
    p.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(e["data"])
    if e["rel"] == "settings.json":  # this node's own per-node fields stay
        if not data.strip():  # an empty copy (read mid-write): never wipe the settings with it
            _state["seq"] = int(e["seq"])
            with _jlock, open(journal_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(e) + "\n")
            return True
        data = _merge_settings(data, p)
    tmp = p.with_name(p.name + ".sitetmp")
    tmp.write_bytes(data)
    _replace(tmp, p)
    _state["seq"] = int(e["seq"])
    with _jlock, open(journal_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(e) + "\n")
    return True


def _seq_of(line: bytes) -> int | None:
    """A journal line's seq, read from its start (json.dumps puts it first) without parsing the whole line."""
    m = _SEQ.match(line)
    if m:
        return int(m[1])
    try:
        return int(json.loads(line)["seq"])
    except (ValueError, KeyError, TypeError):
        return None


def entries_after(n: int, limit: int = 500) -> list[dict] | None:
    """Journal entries with seq > n, oldest first; None when n is before what is kept (take a snapshot). Only the
    entries it returns are parsed: the rest are skipped by their seq."""
    out, first = [], None
    try:
        with open(journal_path(), "rb") as f:
            for line in f:
                if not line.strip():
                    continue
                seq = _seq_of(line)
                if seq is None:  # a line cut short (a write that collided on Windows): it crashed every tick,
                    continue     # so a node never handed back and served its stale settings (user 2026-10-30)
                if first is None:
                    first = seq
                    if n < first - 1:  # n is before what is kept: say so before parsing anything. Parsing 200
                        return None    # entries first (whole shelf.json copies) for an offline node every tick held
                                       # the writer's loop at 90% CPU and its pages took 12 s (2026-11-04)
                if seq > n:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
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
    out = [(rel, config.DATA / rel) for rel in WHOLE]
    for d in DIRS:
        base = config.DATA / d
        for p in sorted(base.rglob("*")) if base.exists() else []:
            if p.is_file() and not p.name.endswith((".sitetmp", ".tmp")):
                out.append((str(p.relative_to(config.DATA)).replace("\\", "/"), p))
    return out


def scan(first: bool = False) -> int:
    """Journal every shared file changed since the last scan, whole. On the first scan nothing is journaled: the
    files are the state this writer starts from, and a follower behind the journal takes a snapshot. Returns entries
    written."""
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
        data = p.read_bytes()
        if not data and rel in WHOLE:  # caught mid-write (a hand edit, an older writer's in-place write): next scan
            _seen.pop(rel, None)
            continue
        _entry(rel, "w", data)
        n += 1
    return n


def trim() -> None:
    """Keep the last KEEP entries and at most KEEP_BYTES, streaming: the journal is never read into memory whole."""
    p = journal_path()
    with _jlock:
        try:
            size = p.stat().st_size
            with open(p, "rb") as f:
                lines = sum(1 for _ in f)
        except FileNotFoundError:
            return
        if lines <= KEEP and size <= KEEP_BYTES:
            return
        drop_lines, drop_bytes = max(0, lines - KEEP), max(0, size - KEEP_BYTES)
        tmp = p.with_suffix(".tmp")
        with open(p, "rb") as f, open(tmp, "wb") as out:
            seen = 0
            for i, line in enumerate(f):
                seen += len(line)
                if i < drop_lines or seen <= drop_bytes:
                    continue
                out.write(line if line.endswith(b"\n") else line + b"\n")
        _replace(tmp, p)


def snapshot() -> bytes:
    """Every shared file, as a gzipped tar, with the seq it is at (in seq.txt)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in WHOLE:
            p = config.DATA / rel
            if p.exists():
                tar.add(str(p), arcname=rel)
        for d in DIRS:
            base = config.DATA / d
            for p in sorted(base.rglob("*")) if base.exists() else []:
                if p.is_file():
                    tar.add(str(p), arcname=str(p.relative_to(config.DATA)).replace("\\", "/"))
        s = str(_state["seq"]).encode()
        info = tarfile.TarInfo("seq.txt")
        info.size = len(s)
        tar.addfile(info, io.BytesIO(s))
    return buf.getvalue()


def restore(body: bytes) -> int:
    """A snapshot from the writer replaces this follower's shared files. Returns the seq it is now at."""
    tar = tarfile.open(fileobj=io.BytesIO(body), mode="r:gz")
    n = 0
    for m in tar.getmembers():
        rel = m.name.replace("\\", "/")
        if rel == "seq.txt":
            n = int(tar.extractfile(m).read().decode().strip() or 0)
            continue
        if not m.isfile() or rel.startswith("/") or ".." in rel.split("/") or not _shared(rel):
            continue
        p = config.DATA / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        data = tar.extractfile(m).read()
        if rel == "settings.json":
            if not data.strip():
                continue
            data = _merge_settings(data, p)
        p.write_bytes(data)
    with _jlock:
        _state["seq"] = n
        journal_path().write_text("", encoding="utf-8")
    return n


# ---- becoming the writer, and handing back ----
def become_writer() -> None:
    if _state["role"] != "writer":
        _state["role"], _state["since"] = "writer", time.time()
        _state["caught_up"] = True
        print(f"site: {config.HOST_NAME} serves the site now (seq {_state['seq']})", flush=True)


def become_follower(of: str) -> None:
    if _state["role"] != "follower" or _state.get("of") != of:
        _state["role"], _state["since"], _state["of"] = "follower", time.time(), of
        _state["caught_up"] = False
        print(f"site: {config.HOST_NAME} follows {of}", flush=True)


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
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError):
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
        if int(got.get("seq", 0)) < _state["seq"]:  # ahead of the writer (we served while it was away): its seqs
            # are not ours, so "after ours" is nothing and we kept our own stale copy (user 2026-10-30): take it whole
            async with http.get(f"{base}/api/site/snapshot", headers=_auth(),
                                timeout=aiohttp.ClientTimeout(total=600, sock_connect=3)) as s:
                body = await s.read()
            await asyncio.to_thread(restore, body)
            continue
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
    for h in [h for h in settings.hosts()["hosts"] if not h["current"]]:
        have = _sent.get(h["name"], 0)
        if have >= _state["seq"]:
            continue
        batch = await asyncio.to_thread(entries_after, have, 200)  # off the event loop: it reads the journal
        if batch is None:  # it is further behind than the journal: it pulls a snapshot itself
            continue
        try:
            async with http.post(f"{h['url'].rstrip('/')}/api/site/journal", json={"entries": batch}, headers=_auth(),
                                 timeout=aiohttp.ClientTimeout(total=30, sock_connect=2)) as r:
                if r.status == 200:
                    _sent[h["name"]] = int((await r.json()).get("seq", have))
        except (TimeoutError, aiohttp.ClientError, OSError, ValueError):
            pass


async def tick(http) -> None:
    """One round: poll the nodes above; decide the role; followers pull, the writer pushes."""
    if not settings.load_settings().get("order"):  # no order of its own: ask every node, not just those "above"
        for n in [n for n in order() if n != config.HOST_NAME]:
            got = await _poll(http, n)
            if got and await asyncio.to_thread(adopt_order, got):
                break
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
    elif all(_state["misses"].get(n, 0) >= MISSES for n in above()):
        become_writer()
    if role() == "writer":
        await asyncio.to_thread(scan)
        await _push(http)


async def _handover(http, h: dict) -> None:
    """This node wrote while a higher one was away. Replay our journal since we became writer onto it."""
    import aiohttp
    since = _state["since"]
    mine = [e for e in (await asyncio.to_thread(entries_after, 0, KEEP) or []) if e.get("at", 0) >= since]
    if not mine:
        return
    try:
        async with http.post(f"{h['url'].rstrip('/')}/api/site/replay", json={"entries": mine}, headers=_auth(),
                             timeout=aiohttp.ClientTimeout(total=120, sock_connect=3)) as r:
            await r.read()
    except (TimeoutError, aiohttp.ClientError, OSError):
        print("site: handover failed; the higher node's copy wins", flush=True)


def replay(entries: list[dict]) -> int:
    """On the returning writer: entries a lower node wrote while we were away, re-journaled here as our own so every
    follower gets them. Whole files replace ours (the lower node's view was the live one); chat entries from an older
    node are dropped."""
    import base64
    n = 0
    for e in sorted(entries, key=lambda x: int(x["seq"])):
        if e.get("mode") == "a" or not _shared(e.get("rel", "")):
            continue
        data = base64.b64decode(e["data"])
        p = config.DATA / e["rel"]
        p.parent.mkdir(parents=True, exist_ok=True)
        if e["rel"] == "settings.json":  # ours keeps its own fields and the order; an empty copy is no change
            if not data.strip():
                continue
            data = _merge_settings(data, p)
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
    last_trim = 0.0
    while True:
        try:
            if auth.host_key():
                await tick(app["peer_http"])
            if time.time() - last_trim > 600:
                last_trim = time.time()
                await asyncio.to_thread(trim)
        except Exception as e:  # the site loop must never take the server down
            print(f"site: {e!r}", flush=True)
        await asyncio.sleep(POLL)
