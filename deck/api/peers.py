"""Every host's sessions in one column: the other hosts' lists are polled while a page wants them, and a call on
another host's session goes through /peer/<host>/ with the host key (the page stays on the host that served it)."""
import asyncio
import re
import time

from aiohttp import WSMsgType, web

from .. import auth, config, sessions, settings, site
from . import access, pages

# ---- every host's sessions in one column; another host's session opens through /peer/<host>/ (user) ----
PEER_WANTED = 20  # peers are polled only while a page asked for every host's sessions this recently (seconds)


async def get_all_sessions(request):
    """{hosts: [{name, online, sessions}]}: this host's live, the others' from the cache peers_loop keeps (an offline
    host keeps its last list, online false; null: not reached yet)."""
    request.app["peerstate"]["wanted"] = time.time()
    local = await asyncio.get_running_loop().run_in_executor(None, sessions.list_sessions)
    seen = request.app["peerstate"]["seen"]
    out = [{"name": h["name"], "online": True, "sessions": local} if h["current"] else
           {"name": h["name"], **seen.get(h["name"], {"online": None, "sessions": []})} for h in settings.hosts()["hosts"]]
    # a linked PC whose forwarder is down is not in settings.hosts(): still a node, offline, its chats in the copy
    for name in site.nodes():
        if not any(h["name"] == name for h in out) and name != config.HOST_NAME:
            out.append({"name": name, "online": False, "sessions": seen.get(name, {}).get("sessions", [])})
    for h in out:
        if h["online"] is False:  # its chats are in every node's copy of the state: readable, and Transfer here works
            if not h["sessions"]:  # it died before this node polled it (a restart here): its chats from the copy
                h["sessions"] = await asyncio.to_thread(site.copied_chats, h["name"])
            for s in h["sessions"]:
                if s.get("chat"):
                    f = config.CHAT_DIR / f"{s['name']}.jsonl"
                    if f.exists():
                        s["copy"] = f.stat().st_mtime
    if not any(h["name"] == config.HOST_NAME for h in out):  # a host missing from the inventory still lists its own
        out.insert(0, {"name": config.HOST_NAME, "online": True, "sessions": local})
    # sync: the journal position, so an open page reloads shelf, scripts and settings when the state changed
    return web.json_response({"current": config.HOST_NAME, "hosts": out, "sync": {"seq": site.seq(), "role": site.role()},
                              "site": site.status()})


PEER_MISSES = 3  # failed polls in a row before a host shows offline: one slow reply on train Wi-Fi is not an outage (user)


async def refresh_peer(app, h: dict) -> None:
    """One other host's session list into the cache (offline after PEER_MISSES failures in a row: its last list, marked so)."""
    import aiohttp
    try:
        async with app["peer_http"].get(h["url"].rstrip("/") + "/api/sessions", timeout=aiohttp.ClientTimeout(total=4),
                                        headers={"Authorization": f"Bearer {auth.host_key()}"}) as r:
            if r.status == 200:
                app["peerstate"]["seen"][h["name"]] = {"online": True, "sessions": await r.json(), "misses": 0}
                return
    except (TimeoutError, aiohttp.ClientError, OSError, ValueError):
        pass
    old = app["peerstate"]["seen"].get(h["name"], {})
    misses = old.get("misses", 0) + 1
    app["peerstate"]["seen"][h["name"]] = {"online": False if misses >= PEER_MISSES else old.get("online"),
                                           "sessions": old.get("sessions", []), "misses": misses}


async def peers_loop(app):
    while True:
        if time.time() - app["peerstate"]["wanted"] < PEER_WANTED and auth.host_key():
            await asyncio.gather(*(refresh_peer(app, h) for h in settings.hosts()["hosts"] if not h["current"]))
        await asyncio.sleep(3)


async def peer_proxy(request):
    """/peer/<host>/api/... and /peer/<host>/ws/...: the same call on another Deck host, with the host key. The page
    works on another host's sessions through the host that served it (the phone app reaches only that one). Also
    /peer/<host>/vnc/...: that host's desktop for the Screen tab (its VNC bridge, on its origin), a WebSocket only."""
    import aiohttp
    _, _, name, tail = request.rel_url.raw_path.split("/", 3)  # tail still URL-encoded, as the page sent it
    peer = next((h for h in settings.hosts()["hosts"] if h["name"] == request.match_info["host"] and not h["current"]), None)
    if not peer or not tail.startswith(("api/", "ws/", "vnc/")):
        raise web.HTTPNotFound()
    if tail.startswith("vnc/") and request.headers.get("Upgrade", "").lower() != "websocket":
        raise web.HTTPNotFound()
    base, key = peer["url"].rstrip("/"), auth.host_key()
    headers = {"Authorization": f"Bearer {key}", "X-Deck-Device": access.sender(request)}
    if request.headers.get("Upgrade", "").lower() == "websocket":
        url = re.sub(r"^http", "ws", base) + "/" + tail
        try:  # no size limit: a long chat's history comes as one message
            timeout = aiohttp.ClientWSTimeout(ws_close=5) if hasattr(aiohttp, "ClientWSTimeout") else 5  # aiohttp < 3.10
            up = await request.app["peer_http"].ws_connect(url, params=request.query, headers=headers, heartbeat=25,
                                                           max_msg_size=0, timeout=timeout)
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise web.HTTPBadGateway(text=f"{request.match_info['host']} is offline") from None
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)

        async def pipe(a, b):
            async for m in a:
                if m.type == WSMsgType.TEXT:
                    await b.send_str(m.data)
                elif m.type == WSMsgType.BINARY:
                    await b.send_bytes(m.data)
        tasks = [asyncio.ensure_future(pipe(ws, up)), asyncio.ensure_future(pipe(up, ws))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in tasks:
            t.cancel()
        await up.close()
        await ws.close()
        return ws
    if request.content_type and request.can_read_body:
        headers["Content-Type"] = request.headers["Content-Type"]
    try:
        async with request.app["peer_http"].request(request.method, f"{base}/{tail}", params=request.query,
                                                    data=await request.read() or None, headers=headers,
                                                    timeout=aiohttp.ClientTimeout(total=150, sock_connect=5)) as r:
            body, ctype = await r.read(), r.headers.get("Content-Type", "application/json")
            status = r.status
    except (TimeoutError, aiohttp.ClientError, OSError):
        return web.json_response({"error": f"{request.match_info['host']} is offline"}, status=502)
    if request.method != "GET":  # a change there (closed, renamed, new): the column shows it at the next poll
        await refresh_peer(request.app, peer)
    # This host's assets version, not the peer's: the page runs this host's code
    return web.Response(status=status, body=body, headers={"Content-Type": ctype, "X-Deck-Assets": pages.assets_version()})


def routes() -> list:
    return [
        web.get("/api/sessions/all", get_all_sessions),
        web.route("*", "/peer/{host}/{tail:.*}", peer_proxy),
    ]
