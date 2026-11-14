"""Closed sessions' trash: closing an agent chat keeps what starts it again (its agent, folder, YOLO setting, and the
chat's state: its conversation id and its own model and effort choices) for a month. Restore starts it again on that
conversation, as a new tab. One JSON file in DATA per host, newest first: a chat restarts on the host it ran on, so
the page lists each host's trash and sends Restore there (05-tabs.js loadTabTrash). Not a site file (deck/site.py):
shared, a follower's closed chats were overwritten by the writer's list. Plain terminals, and agents outside chat mode,
aren't kept: there is no conversation to go back to."""
import json
import os
import time
import uuid

from . import agents, buttons, config, sessions

KEEP = 30 * 86400


def path():
    return config.DATA / "session-trash-local.json"


def _migrate() -> list[dict]:
    """Once: this host's entries from the old shared file (every host's, as the site's writer last had them): those
    marked as this host's, else, unmarked, those whose folder exists here."""
    try:
        items = json.loads((config.DATA / "session-trash.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        items = []
    def here(x: dict) -> bool:
        if x.get("host"):
            return x["host"] == config.HOST_NAME
        return os.path.isdir(os.path.expanduser(config.win_path(x.get("folder") or "")))
    mine = [x for x in items if isinstance(x, dict) and here(x)]
    _save(mine)
    return mine


def load() -> list[dict]:
    """The trash, minus entries older than KEEP."""
    try:
        items = json.loads(path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        items = _migrate()
    except ValueError:
        return []
    return [x for x in items if time.time() - x.get("deleted", 0) < KEEP]


def _save(items: list[dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)  # atomic: a crash never leaves a half-written file


def keep(name: str) -> dict | None:
    """Before a session closes: its trash entry, or None when it has nothing to resume."""
    if not sessions.is_chat(name):
        return None
    st, meta = sessions.chat_state(name), sessions.session_meta(name)
    kind = meta.get("agent")
    if kind not in agents.AGENTS or not st.get("resume"):
        return None
    item = {"id": uuid.uuid4().hex[:10], "host": config.HOST_NAME, "type": kind, "label": meta.get("label") or kind,
            "title": st.get("name") or meta.get("title", ""), "folder": config.win_path(meta.get("folder", "")),
            "yolo": sessions.yolo(name), "computer": bool(meta.get("computer")),
            "state": {"resume": st["resume"], "prefs": st.get("prefs") or {}, "name": st.get("name", "")},
            "deleted": time.time()}
    _save([item, *load()])
    return item


def restore(id_: str) -> str:
    """Start a trashed session again on its conversation; the new tab's name. KeyError: no such entry."""
    items = load()
    item = next(x for x in items if x["id"] == id_)
    b = buttons.new_agent_button(item["type"], "", item["folder"], item["yolo"], bool(item.get("computer")))
    name = sessions.start_run(b, state=item["state"])
    _save([x for x in items if x["id"] != id_])
    return name


def delete(id_: str) -> None:
    _save([x for x in load() if x["id"] != id_])
