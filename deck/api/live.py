"""Session lists pushed, not polled (user 2026-11-04: "deck so laggy and delayed").

One loop lists this host's sessions every LIST_EVERY seconds and keeps the result (sessions.list_sessions costs tmux
calls, about 0.05 s on Windows; once a second for the whole host instead of once per open page every 2 s). When the
list changes it is pushed at once to the other hosts (POST /api/sessions/peer, host key), which keep it as that host's
list. Pages hold one socket (/api/sessions/stream) and get the whole column (every host's list, as
/api/sessions/all answers) when any part of it changes: a session here, another host's push, or the site's journal
(the board, settings, scripts). peers.peers_loop still polls the other hosts, now as a fallback (an older host that
does not push, one that came back)."""
import asyncio
import json
import time

from aiohttp import WSMsgType, web

from .. import auth, sessions, settings, site
from .common import host_only

LIST_EVERY = 1.0  # seconds between listings of this host's sessions
PAGE_EVERY = 0.25  # seconds between checks for a change to send to the open pages


def state(app) -> dict:
    return app["livestate"]


def local(app) -> list:
    """This host's sessions as last listed (listing them first if nothing has been yet)."""
    return state(app)["sessions"]


def poke(app) -> None:
    """List again now, not at the next tick: a request just started, closed, renamed or restarted a session."""
    if "livestate" in app:
        app["livestate"]["poke"].set()


async def list_loop(app) -> None:
    """List this host's sessions every LIST_EVERY s, or at once when poked; a change bumps the version and goes to
    the other hosts."""
    loop = asyncio.get_running_loop()
    st = state(app)
    while True:
        st["poke"].clear()
        try:
            got = await loop.run_in_executor(None, sessions.list_sessions)
            # what changes on its own every second (activity times, clocks) is not a change worth sending
            key = json.dumps([{k: v for k, v in s.items() if k not in ("activity",)} for s in got], sort_keys=True)
            st["sessions"] = got
            if key != st["key"]:
                st["key"] = key
                st["version"] += 1
                st["ready"].set()
                loop.create_task(push_to_peers(app, got))
        except Exception as e:  # the list loop must never stop: the column would freeze
            print(f"live: {e!r}", flush=True)
        try:
            await asyncio.wait_for(st["poke"].wait(), LIST_EVERY)
        except TimeoutError:
            pass


async def push_to_peers(app, got: list) -> None:
    """This host's new list to every other host, so their pages show it now, not at their next poll."""
    import aiohttp
    key = auth.host_key()
    if not key:
        return
    me = next((h["name"] for h in settings.hosts()["hosts"] if h["current"]), "")
    body = {"host": me, "sessions": got}

    async def one(h):
        try:
            async with app["peer_http"].post(h["url"].rstrip("/") + "/api/sessions/peer", json=body,
                                             headers={"Authorization": f"Bearer {key}"},
                                             timeout=aiohttp.ClientTimeout(total=4, sock_connect=2)) as r:
                await r.read()  # an older host answers 404: it polls us instead
        except (TimeoutError, aiohttp.ClientError, OSError):
            pass
    await asyncio.gather(*(one(h) for h in settings.hosts()["hosts"] if not h["current"]))


async def peer_push(request):
    """Another host's new session list (host key)."""
    host_only(request)
    body = await request.json()
    name = str(body.get("host", ""))
    if not any(h["name"] == name and not h["current"] for h in settings.hosts()["hosts"]):
        raise web.HTTPNotFound(text="not one of this site's hosts")
    request.app["peerstate"]["seen"][name] = {"online": True, "sessions": body.get("sessions") or [], "misses": 0}
    request.app["peerstate"]["changed"] = time.time()
    return web.json_response({"ok": True})


def stamp(app) -> tuple:
    """What a page's copy depends on: this host's list, the other hosts' lists, the site's journal."""
    peers = app["peerstate"]
    return (state(app)["version"], peers.get("changed", 0.0),
            json.dumps({k: v.get("online") for k, v in peers["seen"].items()}, sort_keys=True), site.seq())


async def stream(request):
    """A page's live column: the whole answer of /api/sessions/all now, then again each time it changes."""
    from .peers import all_sessions
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    app = request.app
    sent = None
    reader = asyncio.ensure_future(ws.receive())  # the page sends nothing; this notices it closing
    try:
        while not ws.closed:
            app["peerstate"]["wanted"] = time.time()  # the fallback poll of the other hosts runs while a page looks
            now = stamp(app)
            if now != sent:
                sent = now
                await ws.send_str(json.dumps(await all_sessions(app, local(app))))
            done, _ = await asyncio.wait([reader], timeout=PAGE_EVERY)
            if done:
                m = reader.result()
                if m.type in (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED, WSMsgType.ERROR):
                    break
                reader = asyncio.ensure_future(ws.receive())
    except (ConnectionResetError, RuntimeError):
        pass
    finally:
        reader.cancel()
    return ws


def routes() -> list:
    return [
        web.get("/api/sessions/stream", stream),
        web.post("/api/sessions/peer", peer_push),
    ]
