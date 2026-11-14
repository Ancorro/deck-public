"""The board (user 2026-10-31: "a dashboard view in deck... items posted by sessions", "like a cork board (in function),
1 global"): one board for every session and host, Deck's first view. Cards are pinned where they are dragged (x, y in
board pixels, z for which lies on top); a card not yet moved takes the first free spot. One JSON file in DATA
(board.json, shared by every node).

A card: {id, key, x, y, z, s, title, text, kind, url, session, at, meta}. Its kind is its type, and each type shows only
what it needs (user: "reduce fields in card, types instead"): note (text, a title optional), link (url, a title
optional), list (a title, then one item per line of text, "[x] " before a done one; tapping one ticks it), status (a
run's progress, title and a line of text) and alert (something that needs the user). "result" is taken as a note.
`key` (optional, the agent's own name for it) makes a post an update: the same session and key again replace that
card's content and bring it to the top, so a run can keep one status card current instead of posting a new one. A
project's card (key "status-<repo>") is shared: any session in that project updates the same card (user 2026-11-03:
"each session should update its project item on board").
The board is persistent (user 2026-10-31: "this is persistent, no clear all"): cards stay until the user drops one on
the trash can, which keeps it KEEP_DELETED ("deleted": when) to be restored, as the shelf's trash."""
import json
import re
import time
import uuid
from urllib.parse import urlsplit

from . import config

KINDS = ("note", "link", "list", "status", "alert")
MAX_TEXT = 20_000
MIN_SIZE, MAX_SIZE = 0.3, 3.0  # a card's size, pinched
KEEP_DELETED = 30 * 86400
PROJECT_KEY = "status-"  # a project's card: "status-<repo>"


def path():
    return config.DATA / "board.json"


def load() -> list[dict]:
    try:
        cards = json.loads(path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    if not isinstance(cards, list):
        return []
    return [c for c in cards if time.time() - c.get("deleted", time.time()) < KEEP_DELETED]


def _save(cards: list[dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cards, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)  # atomic, as shelf.json


def post(raw: dict, meta: dict | None = None) -> dict:
    """Validate and store one card (or update the session's card with the same key). Raises ValueError."""
    title = str(raw.get("title", "")).strip()[:200]
    text = str(raw.get("text", "")).strip()
    url = str(raw.get("url") or "").strip()
    kind = str(raw.get("kind") or ("link" if url and not text else "note"))
    kind = "note" if kind == "result" else kind
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if url and (len(url) > 2000 or not re.fullmatch(r"https?://[^\s/]+\S*", url)):
        raise ValueError("url must be an http(s) address")
    if kind == "link" and not url:
        raise ValueError("a link card needs a url")
    if not (title or text or url):
        raise ValueError("a card needs a title or text")
    if len(text) > MAX_TEXT:
        raise ValueError(f"text over {MAX_TEXT} characters")
    session = str(raw.get("session", ""))[:200]
    key = str(raw.get("key") or "").strip()[:100]
    cards = load()
    shared = key.startswith(PROJECT_KEY)  # one card per project, whichever session posts
    old = next((c for c in cards if key and c.get("key") == key and (shared or c.get("session") == session)
                and not c.get("deleted")), None)
    card = {"id": old["id"] if old else uuid.uuid4().hex[:10], "title": title or (urlsplit(url).hostname if url else ""),
            "text": text, "kind": kind, "session": session, "at": time.time(),
            **({"key": key} if key else {}), **({"url": url} if url else {}), **({"meta": meta} if meta else {})}
    if old:
        card["first"] = old.get("first", old["at"])
        card.update({k: old[k] for k in ("x", "y", "z", "s") if k in old})  # an update stays where it was pinned
    _save([card, *(c for c in cards if c is not old)])
    return card


def move(card_id: str, raw: dict) -> None:
    """A card dragged: {x, y} where it is pinned, and z (on top of the others); pinched: s, its size (1: normal); or
    a list's item ticked: tick, the line's index."""
    cards = load()
    for c in cards:
        if c.get("id") == card_id:
            for k in ("x", "y", "z"):
                if isinstance(raw.get(k), (int, float)):
                    c[k] = max(0, min(int(raw[k]), 100_000))
            if c.get("kind") == "list" and isinstance(raw.get("tick"), int):  # an item ticked, or unticked
                lines = c.get("text", "").split("\n")
                if 0 <= raw["tick"] < len(lines):
                    ln = lines[raw["tick"]]
                    lines[raw["tick"]] = ln[4:] if ln.startswith("[x] ") else f"[x] {ln}"
                    c["text"] = "\n".join(lines)
            if isinstance(raw.get("s"), (int, float)):  # its size, pinched (user: "let me shrink cards by pinch")
                c["s"] = round(max(MIN_SIZE, min(float(raw["s"]), MAX_SIZE)), 3)
    _save(cards)


def delete(card_id: str, forever: bool = False) -> None:
    """To the trash can (restorable for KEEP_DELETED), or gone."""
    cards = load()
    if forever:
        cards = [c for c in cards if c.get("id") != card_id]
    for c in cards:
        if c.get("id") == card_id:
            c["deleted"] = time.time()
    _save(cards)


def restore(card_id: str) -> None:
    cards = load()
    for c in cards:
        if c.get("id") == card_id:
            c.pop("deleted", None)
    _save(cards)
