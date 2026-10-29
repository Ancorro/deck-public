"""Phone alerts: when an agent session finishes working (your turn) or stops on a question (needs you), post to ntfy.

DECK_NTFY (in ~/.config/deck/env) is the topic URL, e.g. https://ntfy.sh/deck-<random>; the phone's ntfy app
subscribes to it. Unset = no alerts. Only the tab's name, its state and the host go out (no terminal text)."""
import asyncio
import logging
import os
import time
from urllib.parse import urlsplit

import aiohttp

from . import agents, config, sessions, settings

URL = os.environ.get("DECK_NTFY", "").strip()
POLL = 10  # seconds between looks at the sessions
WORKED = 20  # seconds of work before its end is worth a ping (skips quick blips)
WORDS = {"your_turn": "your turn", "needs_you": "needs you"}
log = logging.getLogger(__name__)


def changes(seen: dict, sessions: list[dict], now: float) -> list[dict]:
    """Agent sessions that just went waiting, updating seen ({name: (state, since)}). The first look only records."""
    first, out = not seen, []
    for s in sessions:
        if s["type"] not in agents.AGENTS:
            continue
        state, (prev, since) = s["state"], seen.get(s["name"], (None, now))
        if state != prev:
            seen[s["name"]] = (state, now)
            if not first and state in WORDS and (prev == "working" and now - since >= WORKED or state == "needs_you"):
                out.append(s)
    for name in set(seen) - {s["name"] for s in sessions}:
        del seen[name]
    return out


def message(s: dict, host: str, click: str | None) -> dict:
    """The ntfy JSON publish body for session s."""
    parts = urlsplit(URL)
    body = {"topic": parts.path.strip("/"), "title": f"{s.get('title') or s['label']}: {WORDS[s['state']]}",
            "message": f"{s['label']} on {host}", "tags": ["bell" if s["state"] == "needs_you" else "white_check_mark"],
            "priority": 4 if s["state"] == "needs_you" else 3}
    if click:
        body["click"] = click
    return body


async def watch(_app) -> None:
    """Runs for the server's life (started in make_app) when DECK_NTFY is set."""
    if not URL:
        return
    loop, seen = asyncio.get_running_loop(), {}
    parts = urlsplit(URL)
    base = f"{parts.scheme}://{parts.netloc}/"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
        while True:
            try:
                live = await loop.run_in_executor(None, sessions.list_sessions)
                hosts = await loop.run_in_executor(None, settings.hosts)
                me = next((h for h in hosts["hosts"] if h["current"]), {})
                for s in changes(seen, live, time.time()):
                    await http.post(base, json=message(s, me.get("name") or config.HOST_NAME, me.get("url")))
            except Exception:  # a failed look or post: try again next round
                log.exception("phone alert")
            await asyncio.sleep(POLL)
