"""One Deck on every host (user): the shelf, the scripts and the settings are the same on each. A host that changes one
sends it to the others (POST /api/sync/<doc> with the host key); every minute each host also compares versions with
the others, so one that was offline catches up. Changes are noticed by the files' mtimes, so every way of writing them
counts (the server, deck-pair, a hand edit).

Each document has a version (when it last changed, here or on the host it came from). Merging:
- shelf, scripts, linked PCs, paired devices: item by item (by id). Every item either side has is kept, unless either side deleted it: a removal
  is remembered as a tombstone (id: when) for KEEP_GONE and travels with the document. An item both have: the newer
  document's copy. The order is the newer document's, the other's extra items after it.
- settings: field by field, the newer document's shared fields winning; each host keeps its own colour, default folder, Durable Pi and hosting.
Paired devices (user: a device paired on one host is paired on all): only approved devices travel, as their id, name,
secret hash and creation time; codes, devices waiting for approval and last use stay on each host. Removing a device
removes it everywhere. Its version moves only when that content changes, not when a device is used.
The first time a host syncs, its documents' versions are their files' mtimes: the newest settings win, and the shelf
and scripts are joined (nothing is lost)."""
import asyncio
import json
import time

from . import auth, core

PER_HOST = ("led", "folder", "pidurable", "hosting")  # the alias is the whole Deck's name (user): it syncs
DOCS = {"shelf": lambda: core.DATA / "shelf.json", "buttons": core.buttons_path, "settings": core.settings_path,
        "links": lambda: core.DATA / "links.json", "devices": auth.store_path}
LISTS = ("shelf", "buttons", "links", "devices")
DEVICE_FIELDS = ("id", "name", "hash", "created")
EVERY = 60  # seconds between full comparisons with every other host
KEEP_GONE = 60 * 86400
_seen: dict[str, float] = {}  # doc -> the file mtime this host last accounted for


def state_path():
    return core.DATA / "sync.json"


def load_state() -> dict:
    try:
        st = json.loads(state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        st = {}
    for k in ("ver", "ids", "gone"):
        st.setdefault(k, {})
    return st


def save_state(st: dict) -> None:
    now = time.time()
    st["gone"] = {d: {i: t for i, t in g.items() if now - t < KEEP_GONE} for d, g in st["gone"].items()}
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(st) + "\n", encoding="utf-8")
    tmp.replace(state_path())


def _mtime(doc: str) -> float:
    try:
        return DOCS[doc]().stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _raw(doc: str):
    if doc == "devices":
        with auth.store(write=False) as d:
            return [{k: x[k] for k in DEVICE_FIELDS if k in x} for x in d["devices"] if not x.get("pending")]
    try:
        return json.loads(DOCS[doc]().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {} if doc == "settings" else []


def read(doc: str):
    data = _raw(doc)
    return {k: v for k, v in data.items() if k not in PER_HOST} if doc == "settings" else data


def _ids(items: list) -> list:
    return [x.get("id") for x in items if isinstance(x, dict) and x.get("id")]


def _write(doc: str, data) -> None:
    if doc == "settings":
        mine = _raw(doc)
        data = {**{k: v for k, v in data.items() if k not in PER_HOST}, **{k: mine[k] for k in PER_HOST if k in mine}}
    if doc == "devices":
        _write_devices(data)
        _seen[doc] = _mtime(doc)
        return
    p = DOCS[doc]()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".synctmp")
    tmp.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)
    _seen[doc] = _mtime(doc)  # written from another host's: not a change to send back


def _write_devices(data: list) -> None:
    """The merged approved devices; this host's pending ones and each device's last use stay."""
    with auth.store() as d:
        mine = {x["id"]: x for x in d["devices"]}
        kept = [{k: x[k] for k in DEVICE_FIELDS} | {"last_seen": mine.get(x["id"], {}).get("last_seen", 0)}
                for x in data if all(isinstance(x.get(k), (str, int, float)) for k in DEVICE_FIELDS)]
        d["devices"] = kept + [x for x in d["devices"] if x.get("pending") and x["id"] not in {y["id"] for y in kept}]


def _digest(doc: str) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(_raw(doc), sort_keys=True).encode()).hexdigest()


def check() -> list[str]:
    """Docs changed here since last noticed: a new version, and tombstones for removed items. Returns them."""
    st, changed, now = load_state(), [], time.time()
    first = not state_path().exists()
    dirty = first
    for doc in DOCS:
        m = _mtime(doc)
        if doc not in _seen:  # first look since the server started
            _seen[doc] = m
            if first or doc not in st["ver"]:  # never synced: the file's own age is its version
                st["ver"][doc] = m
                if doc in LISTS:
                    st["ids"][doc] = _ids(_raw(doc))
                if doc == "devices":
                    st.setdefault("digest", {})[doc] = _digest(doc)
                dirty = True
                continue
            if m <= st["ver"][doc]:
                continue  # unchanged while the server was down
        elif m <= _seen[doc]:
            continue
        _seen[doc] = m
        if doc == "devices":  # a device's last use rewrites the file: only a change in what syncs counts
            dg = _digest(doc)
            if st.setdefault("digest", {}).get(doc) == dg:
                continue
            st["digest"][doc] = dg
            dirty = True
        st["ver"][doc] = now
        if doc in LISTS:
            cur = _ids(_raw(doc))
            gone = st["gone"].setdefault(doc, {})
            for i in set(st["ids"].get(doc, [])) - set(cur):
                gone[i] = now
            st["ids"][doc] = cur
        changed.append(doc)
    if changed or dirty:
        save_state(st)
    return changed


def _dead(x: dict, gone: dict) -> bool:
    """Removed, unless the item was (re)made after that: one with an "at" time (a linked PC linked again) outlives it."""
    return x.get("id") in gone and float(gone[x["id"]]) >= float(x.get("at", 0))


def merge_items(mine: list, my_ver: float, theirs: list, their_ver: float, gone: dict) -> list:
    newer, older = (theirs, mine) if their_ver > my_ver else (mine, theirs)
    have = set(_ids(newer))
    return [x for x in newer if not _dead(x, gone)] + \
           [x for x in older if x.get("id") not in have and not _dead(x, gone)]


def apply(doc: str, data, their_ver: float, their_gone: dict | None = None) -> bool:
    """Another host's version of doc merged in. True if this host's copy changed."""
    if doc not in DOCS:
        raise ValueError("no such document")
    check()  # a change made here just now gets its version first
    st = load_state()
    my_ver = float(st["ver"].get(doc, 0))
    if doc == "settings":
        if not isinstance(data, dict):
            raise ValueError("settings must be an object")
        if their_ver <= my_ver:
            return False
        merged = {**read(doc), **data}  # field by field: a field only this host has stays
        if merged != read(doc):
            _write(doc, merged)
        st["ver"][doc] = their_ver
        save_state(st)
        return True
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError(f"{doc} must be a list of items")
    gone = {**st["gone"].get(doc, {}), **{str(k): float(v) for k, v in (their_gone or {}).items()}}
    mine = _raw(doc)
    out = merge_items(mine, my_ver, data, their_ver, gone)
    st["gone"][doc] = gone
    if out != mine:
        _write(doc, out)
    st["ids"][doc] = _ids(out)
    if doc == "devices":
        st.setdefault("digest", {})[doc] = _digest(doc)
    # The result has items they lacked: it is a new version, and goes back to them (and everyone)
    st["ver"][doc] = time.time() if out != data else max(my_ver, their_ver)
    save_state(st)
    return out != mine


def status() -> dict:
    check()
    return {doc: float(load_state()["ver"].get(doc, 0)) for doc in DOCS}


def package(doc: str) -> dict:
    st = load_state()
    return {"version": float(st["ver"].get(doc, 0)), "data": read(doc), "gone": st["gone"].get(doc, {})}


def _auth():
    return {"Authorization": f"Bearer {auth.host_key()}"}


def _timeout():
    import aiohttp
    return aiohttp.ClientTimeout(total=15, sock_connect=4)


def _peers():
    return [h for h in core.hosts()["hosts"] if not h["current"]]


async def send(http, h: dict, doc: str) -> None:
    async with http.post(f"{h['url'].rstrip('/')}/api/sync/{doc}", json=await asyncio.to_thread(package, doc),
                         headers=_auth(), timeout=_timeout()) as r:
        await r.read()


async def push(http, doc: str) -> None:
    for h in _peers():
        try:
            await send(http, h, doc)
        except Exception:  # offline or an older Deck: the minute's comparison catches it up
            pass


async def reconcile(http) -> None:
    """Compare with every other host: take what's newer there, send what's newer here."""
    for h in _peers():
        base = h["url"].rstrip("/")
        try:
            async with http.get(f"{base}/api/sync", headers=_auth(), timeout=_timeout()) as r:
                if r.status != 200:
                    continue
                theirs = await r.json()
            mine = await asyncio.to_thread(status)
            for doc in DOCS:
                tv, mv = float(theirs.get(doc, 0)), mine[doc]
                if tv > mv:
                    async with http.get(f"{base}/api/sync/{doc}", headers=_auth(), timeout=_timeout()) as r:
                        got = await r.json()
                    await asyncio.to_thread(apply, doc, got["data"], float(got["version"]), got.get("gone"))
                    if load_state()["ver"].get(doc, 0) > tv:  # merged into something new: back it goes
                        await send(http, h, doc)
                elif mv > tv:
                    await send(http, h, doc)
        except Exception:
            continue


async def loop(app) -> None:
    last = 0.0
    while True:
        try:
            for doc in await asyncio.to_thread(check):
                await push(app["peer_http"], doc)
            if time.time() - last > EVERY:
                last = time.time()
                await reconcile(app["peer_http"])
        except Exception as e:  # syncing must never take the server down
            print(f"sync: {e!r}", flush=True)
        await asyncio.sleep(2)
