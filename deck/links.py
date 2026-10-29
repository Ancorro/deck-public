"""Linked PCs: Decks that join one group over iroh, with no Tailscale or machine notes (user).

PC A (Settings → PCs → Link a PC) shows a link code: its iroh key and a one-time secret (5 minutes). PC B pastes it
(Link to a PC): B reaches A through a local `deck-iroh connect` forwarder, claims the code with its name, and waits.
Only A approves. Then B gets the group's host key and its member list, and the members reach each other through the
same forwarders: settings.hosts() gives each linked member the forwarder's address, so everything that talks to other hosts
(sessions, sync, moves) works unchanged. The member list (links.json) syncs like the scripts.

The host key gives full access to every member, so removing one makes a new key: the PC doing it hands the new key
(with the old one) to every other host it can reach, then switches. The removed PC, and any member that was offline or
client only (nothing can reach those), keep only the old key: they're out until linked again ("Leave" first).
"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path

from . import auth, config, settings

CODE_TTL = 300
_codes: dict[str, dict] = {}  # secret hash -> a link code this host made, while it lasts (in memory: 5 minutes)
_fwd: dict[str, dict] = {}  # iroh key -> {"url": forwarder address or None, "proc": the deck-iroh process}
joining: dict = {}  # this host joining another: {"state": "pending" | "ok" | "refused" | "error", "host", "error"}


def path() -> Path:
    return config.DATA / "links.json"


def iroh_id() -> str:
    try:
        return (Path.home() / ".config/deck/iroh.id").read_text().strip()
    except OSError:
        return ""


def members() -> list[dict]:
    """[{id, name, iroh, hosting}], id = name; this host included once it is linked."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return [m for m in data if isinstance(m, dict) and m.get("name")] if isinstance(data, list) else []


def save(items: list[dict]) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path())


def add(*new: dict) -> None:
    """Members added or updated, stamped now: a removal synced from before then doesn't take them out again."""
    by = {m["id"]: m for m in members()}
    for m in new:
        by[m["id"]] = {**m, "at": time.time()}
    save(list(by.values()))


def hosting() -> bool:
    return settings.settings().get("hosting", True)


def me() -> dict:
    return {"id": config.HOST_NAME, "name": config.HOST_NAME, "iroh": iroh_id(), "hosting": hosting()}


def update_me() -> None:
    """After Settings → Host sessions changes: the members learn it (links.json syncs)."""
    mine = next((m for m in members() if m["name"] == config.HOST_NAME), None)
    if mine and {k: mine.get(k) for k in me()} != me():
        add(me())


def peer_url(m: dict) -> str | None:
    """The local forwarder's address for a linked member, once deck-iroh has opened it."""
    return (_fwd.get(m["iroh"]) or {}).get("url")


def others() -> list[dict]:
    return [m for m in members() if m["name"] != config.HOST_NAME and m.get("hosting", True) and m.get("iroh")]


# ---- forwarders: one `deck-iroh connect` per other member ----

def iroh_exe() -> str | None:
    found = [os.environ.get("DECK_IROH", ""), shutil.which("deck-iroh") or "", str(Path.home() / ".local/bin/deck-iroh")]
    if config.WINDOWS:
        found.insert(1, os.path.join(os.environ.get("LOCALAPPDATA", ""), "Deck", "deck-iroh.exe"))
    return next((p for p in found if p and os.path.isfile(p)), None)


async def forward(key: str, wait: float = 0) -> str | None:
    """Start (or keep) the forwarder to this iroh key; with wait, up to that many seconds for its address."""
    f = _fwd.get(key)
    if not f or f["proc"].returncode is not None:
        exe = iroh_exe()
        if not exe:
            raise RuntimeError("deck-iroh not found: install Deck's iroh tunnel on this PC")
        # deck-iroh reads HOME, which Windows doesn't set
        proc = await asyncio.create_subprocess_exec(exe, "connect", key, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL,
                                                    env={**os.environ, "HOME": str(Path.home())})
        f = _fwd[key] = {"url": None, "proc": proc}

        async def read_url():
            line = (await proc.stdout.readline()).decode().strip()
            if re.fullmatch(r"http://127\.0\.0\.1:\d+/?", line):
                f["url"] = line.rstrip("/")
        asyncio.ensure_future(read_url())
    end = time.time() + wait
    while not f["url"] and time.time() < end and f["proc"].returncode is None:
        await asyncio.sleep(0.2)
    return f["url"]


async def loop(app) -> None:
    """Keep a forwarder to every other member (restarted if it exits); drop those no longer linked."""
    while True:
        try:
            want = {m["iroh"] for m in others()}
            for key in want:
                await forward(key)
            for key in set(_fwd) - want - {joining.get("iroh")}:
                stop(key)
        except Exception as e:  # never take the server down
            print(f"links: {e!r}", flush=True)
        await asyncio.sleep(3)


def stop(key: str) -> None:
    f = _fwd.pop(key, None)
    if f and f["proc"].returncode is None:
        f["proc"].kill()


def stop_all() -> None:
    for key in list(_fwd):
        stop(key)


# ---- removing a member, leaving ----

def _set_key(key: str) -> None:
    auth.host_key_path().parent.mkdir(parents=True, exist_ok=True)
    auth.host_key_path().write_text(key + "\n")
    os.chmod(auth.host_key_path(), 0o600)


def rekey(key: str, removed: str) -> None:
    """The group's new host key, from the member that removed `removed` (called with the old key)."""
    if not re.fullmatch(r"[\w-]{32,}", key):
        raise ValueError("bad key")
    gone = next((m for m in members() if m["name"] == removed), None)
    save([m for m in members() if m["name"] != removed])
    if gone and gone.get("iroh"):
        stop(gone["iroh"])
    _set_key(key)


async def remove(http, name: str) -> dict:
    """Take a PC out of the group: a new host key for every other host this one can reach. Returns who missed it."""
    import aiohttp
    if name == config.HOST_NAME:
        raise ValueError("to take this PC out, use Leave")
    if not any(m["name"] == name for m in members()):
        raise ValueError(f"{name} isn't linked")
    old, new, missed = auth.host_key(), secrets.token_urlsafe(32), []
    for h in [h for h in settings.hosts()["hosts"] if not h["current"] and h["name"] != name]:
        try:
            async with http.post(f"{h['url'].rstrip('/')}/api/link/rekey", json={"key": new, "removed": name},
                                 headers={"Authorization": f"Bearer {old}"}, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    raise ValueError(r.status)
        except (TimeoutError, aiohttp.ClientError, ValueError):
            missed.append(h["name"])
    rekey(new, name)
    clients = [m["name"] for m in members() if m["name"] != config.HOST_NAME and not m.get("hosting", True)]
    return {"missed": missed, "relink": clients}


def leave() -> None:
    """This PC leaves its group: no members, and a host key of its own (the group's is no use to it any more)."""
    for key in list(_fwd):
        stop(key)
    save([])  # leaving removes nobody from the group (the members' lists travel with the site's state, deck/site.py)
    _set_key(secrets.token_urlsafe(32))


# ---- this host's link codes (PC A) ----

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _code(secret: str) -> dict | None:
    now = time.time()
    for k in [k for k, c in _codes.items() if c["expires"] < now and c.get("state") != "ok"]:
        del _codes[k]
    return _codes.get(_hash(secret))


def new_code() -> dict:
    key = iroh_id()
    if not key:
        raise ValueError("this PC's iroh tunnel isn't running (deck-iroh), so other PCs can't reach it")
    secret = secrets.token_hex(8)
    _codes[_hash(secret)] = {"expires": time.time() + CODE_TTL, "claim": None}
    return {"code": f"deck-{key}-{secret}", "expires": int(time.time() + CODE_TTL)}


def parse(code: str) -> tuple[str, str]:
    m = re.fullmatch(r"deck-([0-9a-f]{64})-([0-9a-f]{16})", code.strip())
    if not m:
        raise ValueError("that isn't a link code (it starts with deck- and comes from Link a PC on the other PC)")
    return m.group(1), m.group(2)


def claim(secret: str, name: str, key: str, hosting: bool) -> str:
    """PC B redeems a code: it gets a token to wait with until A decides."""
    c = _code(secret)
    if not c or c["claim"]:
        raise ValueError("link code is wrong, used or expired; make a new one")
    name = str(name).strip()[:40]
    if not re.fullmatch(r"[\w.-]+", name):
        raise ValueError("bad PC name")
    if name == config.HOST_NAME or any(m["name"] == name for m in members()) or \
            any(h["name"] == name for h in settings.hosts()["hosts"]):
        raise ValueError(f"a PC named {name} is already in this group")
    if hosting and not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("a PC that hosts needs its iroh key")
    token = secrets.token_urlsafe(24)
    c["claim"] = {"name": name, "iroh": key, "hosting": bool(hosting), "token": _hash(token)}
    return token


def pending(secret: str) -> dict | None:
    c = _code(secret)
    return {"name": c["claim"]["name"], "hosting": c["claim"]["hosting"]} if c and c["claim"] and "state" not in c else None


def decide(secret: str, allow: bool) -> None:
    c = _code(secret)
    if not c or not c["claim"] or "state" in c:
        raise ValueError("nothing to approve for this code")
    c["state"] = "ok" if allow else "refused"
    if allow:
        auth.ensure_host_key()
        b = c["claim"]
        add(me(), {"id": b["name"], "name": b["name"], "iroh": b["iroh"], "hosting": b["hosting"]})
    c["expires"] = time.time() + 60  # B has a minute to collect the answer


def wait(secret: str, token: str) -> dict:
    """What B gets: still pending, refused, or the host key and the members."""
    c = _code(secret)
    if not c or not c["claim"] or not secrets.compare_digest(c["claim"]["token"], _hash(token)):
        raise ValueError("unknown link request")
    state = c.get("state", "pending")
    if state != "ok":
        return {"state": state}
    del _codes[_hash(secret)]  # once
    return {"state": "ok", "host": config.HOST_NAME, "host_key": auth.host_key(), "members": members()}


# ---- joining another host's group (PC B) ----

def can_join() -> str:
    """Why this PC can't join a group, or ""."""
    if [m for m in members() if m["name"] != config.HOST_NAME] or [h for h in settings.hosts()["hosts"] if not h["current"]]:
        return "this PC is already linked with other PCs"
    return ""


async def join(http, code: str) -> dict:
    """Claim A's code through a forwarder, then wait for A's answer in the background."""
    key, secret = parse(code)
    if why := can_join():
        raise ValueError(why)
    if key == iroh_id():
        raise ValueError("that code is from this PC; enter it on the other one")
    if joining.get("state") in ("connecting", "pending"):
        raise ValueError("already linking with another PC")
    joining.clear()
    joining.update({"state": "connecting", "iroh": key})  # loop() keeps this forwarder while linking
    try:
        return await _claim(http, key, secret)
    except Exception:
        joining.clear()
        stop(key)
        raise


async def _claim(http, key: str, secret: str) -> dict:
    import aiohttp
    url = await forward(key, wait=20)
    if not url:
        raise ValueError("couldn't reach that PC (is Deck running there?)")
    body = {"secret": secret, "name": config.HOST_NAME, "iroh": iroh_id(), "hosting": hosting()}
    for tries in range(5):  # a fresh iroh connection can fail while the other PC's address is still being found
        try:
            async with http.post(f"{url}/api/link/claim", json=body, timeout=aiohttp.ClientTimeout(total=30)) as r:
                got = await r.json()
                if r.status != 200:
                    raise ValueError(got.get("error") or f"the other PC said {r.status}")
            break
        except (TimeoutError, aiohttp.ClientError) as e:
            if tries == 4:
                raise ValueError(f"couldn't reach that PC: {e!r}") from e
            await asyncio.sleep(3)
    joining.update({"state": "pending", "host": got.get("host", "")})
    asyncio.ensure_future(_await_answer(http, url, secret, got["token"]))
    return dict(joining)


async def _await_answer(http, url: str, secret: str, token: str) -> None:
    import aiohttp
    end = time.time() + CODE_TTL + 60
    while time.time() < end:
        await asyncio.sleep(2)
        try:
            async with http.get(f"{url}/api/link/wait", params={"secret": secret, "token": token},
                                timeout=aiohttp.ClientTimeout(total=15)) as r:
                got = await r.json()
        except (TimeoutError, aiohttp.ClientError, ValueError):
            continue
        if r.status != 200:
            joining.update({"state": "error", "error": got.get("error", f"status {r.status}")})
            joining.pop("iroh", None)
            return
        if got["state"] == "pending":
            continue
        if got["state"] == "ok":
            _set_key(got["host_key"])
            add(*[m for m in got["members"] if isinstance(m, dict) and m.get("name")], me())
        else:
            joining.pop("iroh", None)  # refused: its forwarder goes
        joining["state"] = got["state"]
        return
    joining.update({"state": "error", "error": "no answer from the other PC"})
    joining.pop("iroh", None)
