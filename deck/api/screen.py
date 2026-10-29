"""The Screen tab (deck/screen.py): the VNC bridge served on this origin (so it works over iroh), the VNC server
run only while a page streams, and the phone screen (a virtual monitor) attached only while watched."""
import asyncio
import subprocess
import time

from aiohttp import WSMsgType, web

from .. import screen
from . import access
from .common import bad_request


async def get_screen(request):
    cfg = screen.screen()
    # Over iroh the tab works only when Deck serves the bridge itself (vnc.json "bridge"); else it stays hidden (user:
    # optional features off until set up, never an empty panel)
    if cfg["enabled"] and access.via_tunnel(request) and not cfg["bridge"]:
        return web.json_response({"enabled": False, "why": "Screen over iroh needs \"bridge\" in vnc.json (docs/screen.md)"})
    return web.json_response(cfg)


async def vnc_bridge(request):
    """/vnc/...: the Screen tab's WebSocket, piped to websockify on loopback (vnc.json "bridge"), so the tab works on
    every way in, iroh included. Paired devices only, like every other route."""
    bridge = screen.vnc_bridge()
    if not bridge or request.headers.get("Upgrade", "").lower() != "websocket":
        raise web.HTTPNotFound()
    vnc = request.app["vncstate"]
    vnc["streams"] += 1
    try:
        return await pipe_vnc(request, bridge)
    finally:
        vnc["streams"] -= 1
        vnc["idle"] = time.time()


async def vnc_up():
    """Start the VNC server (stopped while nobody streams, vnc_idle_loop) and wait for it to listen."""
    if await asyncio.to_thread(screen.vnc_listening):
        return
    if not await asyncio.to_thread(screen.vnc_service, "on"):
        return  # no "Deck VNC on" task: the server is always on (docs/screen.md)
    for _ in range(40):
        await asyncio.sleep(0.25)
        if await asyncio.to_thread(screen.vnc_listening):
            return


async def pipe_vnc(request, bridge):
    import aiohttp
    await vnc_up()
    try:
        up = await request.app["peer_http"].ws_connect(f"ws://{bridge}/{request.match_info['tail']}", protocols=("binary",),
                                                       max_msg_size=0)
    except (TimeoutError, aiohttp.ClientError, OSError):
        raise web.HTTPBadGateway(text="the VNC bridge isn't running") from None
    ws = web.WebSocketResponse(protocols=("binary",), max_msg_size=0)
    await ws.prepare(request)

    async def pipe(a, b):
        async for m in a:
            if m.type == WSMsgType.BINARY:
                await b.send_bytes(m.data)
            elif m.type == WSMsgType.TEXT:
                await b.send_str(m.data)
    tasks = [asyncio.ensure_future(pipe(ws, up)), asyncio.ensure_future(pipe(up, ws))]
    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in tasks:
        t.cancel()
    await up.close()
    await ws.close()
    return ws


VNC_IDLE = 60  # s after the last stream closes before the VNC server is stopped (a reconnect reuses it)


async def vnc_idle_loop(app):
    """The VNC server runs only while a page streams (user: "turn off the vnc when i am not streaming screen"): stopped
    a minute after the last /vnc/ stream closes, and at start when nothing streams. Needs the "Deck VNC on/off" tasks."""
    state = app["vncstate"]
    while True:
        if not state["streams"] and time.time() - state["idle"] > VNC_IDLE and screen.vnc_bridge():
            state["idle"] = float("inf")  # stopped: nothing to do until a stream comes and goes
            try:
                await asyncio.to_thread(screen.vnc_service, "off")
            except (OSError, subprocess.SubprocessError):
                pass
        await asyncio.sleep(15)


PHONE_IDLE = 180  # s without a streaming page's keep-alive before the phone screen is detached


async def phone_screen(request):
    """GET: the phone screen's state (null if this host has none). POST {on, w, h}: attach at w×h, or detach.
    POST {keep: true}: a page still streaming to it (every minute), so phone_idle_loop leaves it attached."""
    if request.method == "GET":
        return web.json_response(await asyncio.to_thread(screen.phone_screen))
    body = await request.json()
    if body.get("keep"):
        request.app["phonestate"]["seen"] = time.time()
        return web.json_response({"ok": True})
    try:
        if body.get("on"):
            res = await asyncio.to_thread(screen.phone_screen, "on", int(body["w"]), int(body["h"]))
            request.app["phonestate"]["seen"] = time.time()
        else:
            res = await asyncio.to_thread(screen.phone_screen, "off")
            request.app["phonestate"]["seen"] = None
    except (KeyError, ValueError, RuntimeError) as e:
        return bad_request(e)
    return web.json_response(res)


async def phone_idle_loop(app):
    """The phone screen is a real monitor to Windows (windows land on it, the desktop is wider), so it stays attached
    only while a page streams it (user: "disable the virtual screens whenever not streaming"). The page detaches it on
    Stop; this catches a closed tab or a sleeping phone, and one left on before Deck started."""
    try:
        on = (await asyncio.to_thread(screen.phone_screen) or {}).get("on")
    except (OSError, subprocess.SubprocessError):
        on = False
    app["phonestate"]["seen"] = time.time() if on else None
    while True:
        await asyncio.sleep(30)
        seen = app["phonestate"]["seen"]
        if seen is not None and time.time() - seen > PHONE_IDLE:
            app["phonestate"]["seen"] = None
            try:
                await asyncio.to_thread(screen.phone_screen, "off")
            except (OSError, subprocess.SubprocessError, RuntimeError):
                pass


async def phone_windows(request):
    """GET: the host desktop's app windows. POST {hwnd, phone}: move one onto the phone screen (or back)."""
    if request.method == "GET":
        return web.json_response(await asyncio.to_thread(screen.phone_screen, "windows"))
    body = await request.json()
    try:
        res = await asyncio.to_thread(screen.phone_screen, "move", int(body["hwnd"]), 1 if body.get("phone") else 0)
    except (KeyError, ValueError, RuntimeError) as e:
        return bad_request(e)
    return web.json_response(res)


def routes() -> list:
    return [
        web.get("/api/screen", get_screen),
        web.get("/vnc/{tail:.*}", vnc_bridge),
        web.get("/api/screen/phone", phone_screen),
        web.post("/api/screen/phone", phone_screen),
        web.get("/api/screen/windows", phone_windows),
        web.post("/api/screen/windows", phone_windows),
    ]
