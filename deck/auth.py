"""Device pairing: who may use this Deck.

A device pairs once by scanning a QR code (one-time code, 5 minutes) and waiting for approval from whoever made the
code (`deck-pair` on the host, or Settings on an already-paired device). It then holds a random secret in a cookie;
the host stores only its hash. Hosts reach each other (notes sync, updates) with a shared host key.

Enforcement (DECK_AUTH): "on" (default) = paired devices only (this host's own browser always gets in, and pairs the
first device); "auto" = open until the first device is approved, then on; "off".
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import config

CODE_TTL = 300
COOKIE = "deck_device"


def store_path():
    return config.DATA / "devices.json"


def host_key_path():
    return config.DATA / "host-key"


@contextmanager
def store(write: bool = True):
    """devices.json under a lock (the server and `deck-pair` both write it); write=False for plain reads."""
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "w") as lock:
        if config.WINDOWS:
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)  # released when the file closes
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(p.read_text())
        except (FileNotFoundError, ValueError):
            data = {}
        data.setdefault("devices", [])
        data["codes"] = [c for c in data.get("codes", []) if c["expires"] > time.time()]
        yield data
        if not write:
            return
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.chmod(tmp, 0o600)
        tmp.replace(p)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def enforced() -> bool:
    mode = os.environ.get("DECK_AUTH", "on").strip().lower()
    if mode != "auto":  # anything but off or auto (a typo, "true", "1") keeps pairing on
        return mode != "off"
    with store(write=False) as d:
        return any(not x.get("pending") for x in d["devices"])


# ---- pairing ----

def new_code(by: str) -> dict:
    """One-time pairing code; `by` names who must approve (a device id, or "host" for deck-pair)."""
    code = secrets.token_urlsafe(9)
    with store() as d:
        d["codes"].append({"hash": _hash(code), "expires": time.time() + CODE_TTL, "by": by, "claim": None})
    return {"code": code, "expires": int(time.time() + CODE_TTL)}


def claim(code: str, name: str) -> str:
    """The new device redeems a code: it gets a secret right away, usable once approved."""
    token = secrets.token_urlsafe(32)
    with store() as d:
        c = next((c for c in d["codes"] if c["hash"] == _hash(code)), None)
        if not c or c["claim"]:
            raise ValueError("pairing code is wrong, used or expired; make a new one")
        dev = {"id": uuid.uuid4().hex[:8], "name": (name.strip() or "Device")[:40], "hash": _hash(token),
               "created": int(time.time()), "last_seen": 0, "pending": True}
        d["devices"].append(dev)
        c["claim"] = dev["id"]
    return token


def pending(code: str) -> dict | None:
    """The device waiting on this code, for the approver to see."""
    with store(write=False) as d:
        c = next((c for c in d["codes"] if c["hash"] == _hash(code)), None)
        dev = c and next((x for x in d["devices"] if x["id"] == c["claim"]), None)
        return {"id": dev["id"], "name": dev["name"]} if dev else None


def decide(code: str, by: str, allow: bool) -> None:
    with store() as d:
        c = next((c for c in d["codes"] if c["hash"] == _hash(code)), None)
        if not c or c["by"] != by or not c["claim"]:
            raise ValueError("nothing to approve for this code")
        if allow:
            for x in d["devices"]:
                if x["id"] == c["claim"]:
                    x.pop("pending", None)
        else:
            d["devices"] = [x for x in d["devices"] if x["id"] != c["claim"]]
        d["codes"].remove(c)


# ---- checking requests ----

def device_for(token: str | None, touch: bool = True) -> dict | None:
    """The device a cookie belongs to (pending included; callers check `pending`)."""
    if not token:
        return None
    h = _hash(token)
    find = lambda d: next((x for x in d["devices"] if secrets.compare_digest(x["hash"], h)), None)
    with store(write=False) as d:
        dev = find(d)
    if dev and touch and time.time() - dev.get("last_seen", 0) > 60:  # record last use about once a minute
        with store() as d:
            if (x := find(d)):
                x["last_seen"] = int(time.time())
    return dict(dev) if dev else None


def host_key() -> str:
    """Shared by the user's hosts (notes sync, updates). Copy ~/.config/deck/host-key to each host."""
    try:
        return host_key_path().read_text().strip()
    except FileNotFoundError:
        return ""


def ensure_host_key() -> str:
    if not host_key():
        host_key_path().parent.mkdir(parents=True, exist_ok=True)
        host_key_path().write_text(secrets.token_urlsafe(32) + "\n")
        os.chmod(host_key_path(), 0o600)
    return host_key()


def is_host(authorization: str | None) -> bool:
    key = host_key()
    return bool(key and authorization and secrets.compare_digest(authorization, f"Bearer {key}"))


def devices() -> list[dict]:
    with store(write=False) as d:
        return [{k: x[k] for k in ("id", "name", "created", "last_seen")} | {"pending": bool(x.get("pending"))}
                for x in d["devices"]]


def remove(dev_id: str) -> None:
    with store() as d:
        d["devices"] = [x for x in d["devices"] if x["id"] != dev_id]


# ---- deck-pair: pair a device from the host itself ----

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deck-pair", description="Pair a phone or browser with this Deck.")
    ap.add_argument("url", nargs="?", help="this Deck's address (default: first DECK_ORIGINS entry)")
    a = ap.parse_args(argv)
    base = a.url or os.environ.get("DECK_ORIGINS", "").split(",")[0] or "http://127.0.0.1:8787"
    c = new_code("host")
    link = f"{base.rstrip('/')}/#pair={c['code']}"
    # With deck-iroh running the link also carries the host's iroh key: the page's "Pair the Deck app instead" then
    # pairs the app over iroh.
    try:
        link += f"&iroh={(Path.home() / '.config/deck/iroh.id').read_text().strip()}&name={config.HOST_NAME}"
    except OSError:
        pass
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(link)
        q.print_ascii(invert=True)
    except ImportError:
        pass
    print(f"\nScan, or open on the device: {link}\nValid 5 minutes, once. Waiting for the device…")
    while time.time() < c["expires"]:
        p = pending(c["code"])
        if p:
            ok = input(f'Allow "{p["name"]}"? [y/N] ').strip().lower() == "y"
            decide(c["code"], "host", ok)
            print("Paired." if ok else "Refused.")
            return 0 if ok else 1
        time.sleep(1)
    print("Code expired.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
