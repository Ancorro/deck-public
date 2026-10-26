"""Deck web server: button API, tmux session API, and a WebSocket <-> `tmux attach` PTY bridge."""
import asyncio
import base64
import gzip
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path
from html import escape as html_escape
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

from . import alerts, auth, core, describe, links, notes, saves, session_move, session_trash, shelf, site

if core.WINDOWS:
    from winpty import PtyProcess
else:
    import fcntl
    import pty
    import termios

from aiohttp import web_fileresponse  # noqa: E402
# Phone app download (app/build.sh): Android only offers to install it with this type. aiohttp 3.10+ keeps its own
# table; older ones (Ubuntu 24.04's 3.9) use the mimetypes module's.
if hasattr(web_fileresponse, "CONTENT_TYPES"):
    web_fileresponse.CONTENT_TYPES.add_type("application/vnd.android.package-archive", ".apk")
else:
    mimetypes.add_type("application/vnd.android.package-archive", ".apk")

HOST = os.environ.get("DECK_HOST", "127.0.0.1")  # tailscale serve fronts it; never bind the LAN
PORT = int(os.environ.get("DECK_PORT", "8787"))
# deck-iroh (tunnel/) forwards here: requests on this port come from the internet, so they're never "local" and always
# need a paired device, even before the first one is approved.
TUNNEL_PORT = int(os.environ.get("DECK_TUNNEL_PORT", str(PORT + 1)))
# Local speech-to-text for the browser mic (POST audio file -> {"text"}, e.g. WhisperWriter's /transcribe); unset =
# browsers use their own speech recognition.
TRANSCRIBE_URL = os.environ.get("DECK_TRANSCRIBE_URL", "").strip()
IROH_ID = Path.home() / ".config/deck/iroh.id"


def via_tunnel(request) -> bool:
    sock = request.transport and request.transport.get_extra_info("sockname")
    return bool(sock) and sock[1] == TUNNEL_PORT
STATIC = Path(__file__).parent / "static"
try:  # commit this server is running (none for the PC app's bundled copy)
    RUNNING = subprocess.run(["git", "-C", str(Path(__file__).parent.parent), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
except OSError:  # no git
    RUNNING = ""


def allowed_origins() -> set[str]:
    extra = os.environ.get("DECK_ORIGINS", "")
    return {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", *filter(None, extra.split(","))}


def known_host(host: str) -> bool:
    """The Host header names this Deck: an IP address or localhost, a DECK_ORIGINS address, or a tailnet name. Anything
    else is a DNS-rebinding page (evil.example resolved to 127.0.0.1), which would otherwise pass as this host's own
    browser."""
    name = urlsplit(f"//{host}").hostname or ""
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    origins = {urlsplit(o).hostname for o in os.environ.get("DECK_ORIGINS", "").split(",") if o}
    return name in ("localhost", *origins) or name.endswith(".ts.net")


@web.middleware
async def origin_guard(request, handler):
    if not known_host(request.host):
        raise web.HTTPForbidden(text=f"host {request.host} not allowed (add its address to DECK_ORIGINS)")
    # WebSockets bypass CORS, so any page on a tailnet device could drive the shell without this.
    # No Origin = not a browser, so no cross-site risk.
    unsafe = request.method not in ("GET", "HEAD") or "/ws/" in request.path
    origin = request.headers.get("Origin")
    # Over the iroh tunnel the page's origin is the phone's own forwarder, http://127.0.0.1:<port>: allow it when it is
    # the very address the request was sent to (same origin).
    tunnel_page = via_tunnel(request) and origin == f"http://{request.host}" and request.host.startswith("127.0.0.1:")
    if unsafe and origin and origin not in request.app["origins"] and not tunnel_page:
        raise web.HTTPForbidden(text=f"origin {origin} not allowed")
    return await handler(request)


def is_local(request) -> bool:
    """A browser on this host itself: loopback straight to Deck, or through `tailscale serve` from one of this host's own
    tailnet addresses (DECK_LOCAL_IPS; serve appends the real client as the last X-Forwarded-For entry)."""
    if request.remote not in ("127.0.0.1", "::1") or via_tunnel(request):
        return False
    xff = request.headers.get("X-Forwarded-For")
    if xff is None:
        return "Tailscale-User-Login" not in request.headers
    own = {ip.strip() for ip in os.environ.get("DECK_LOCAL_IPS", "").split(",") if ip.strip()}
    return xff.split(",")[-1].strip() in own


# Reachable without pairing: the page itself (it shows the pairing screen), the pairing claim, and another PC claiming
# a link code and collecting the answer (it needs the code's secret, then the token the claim gave it).
OPEN = ("/", "/sw.js", "/api/me", "/api/pair/claim", "/api/link/claim", "/api/link/wait")


_WHOIS: dict = {}  # tailnet ip -> (device name, when looked up)


def tailnet_device(ip: str) -> str:
    """The tailnet device at ip, by `tailscale whois` (cached an hour); "" when unknown or tailscale is missing."""
    name, at = _WHOIS.get(ip, ("", 0))
    if time.time() - at < 3600:
        return name
    ts = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        out = subprocess.run([ts, "whois", "--json", ip], capture_output=True, text=True, timeout=3)
        name = json.loads(out.stdout)["Node"].get("ComputedName", "") if out.returncode == 0 else ""
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        name = ""
    _WHOIS[ip] = (name, time.time())
    return name


def local_name(request) -> str:
    """The stamp for a browser trusted without pairing: "this PC" from this host's own browser, else the tailnet device's name
    (DECK_LOCAL_IPS may list the user's other devices too; user 2026-10-26: "the stamp is wrong")."""
    if not is_local(request):
        return ""
    xff = request.headers.get("X-Forwarded-For")
    name = tailnet_device(xff.split(",")[-1].strip()) if xff else ""
    return name if name and name.lower() != core.HOST_NAME.lower() else "this PC"


def sender(request, via=None) -> str:
    """Who sent a chat message, for its stamp: the paired device's name (or this PC), and how (phone app, phone browser, PC browser)."""
    dev = request.get("device", {}).get("name") or local_name(request)
    if not dev and auth.is_host(request.headers.get("Authorization")):  # through another host's /peer proxy: its sender
        dev = request.headers.get("X-Deck-Device", "")
    return ", ".join(x for x in (str(dev)[:40], str(via or "")[:20]) if x)


# A client-only PC (Settings → Host sessions on this PC, off) starts no sessions of its own and isn't reachable over
# its iroh tunnel; its page starts new sessions on a linked PC instead (app.js api).
STARTS = re.compile(r"/api/(agent|shell|build|run/[^/]+)")


@web.middleware
async def auth_guard(request, handler):
    """Only paired devices (cookie) and the user's other hosts (host key) get in, once pairing is enforced."""
    p = request.path
    if not core.settings().get("hosting", True):
        if via_tunnel(request):
            return web.json_response({"error": "this PC doesn't host sessions"}, status=403)
        if request.method == "POST" and STARTS.fullmatch(p):
            return web.json_response({"error": "this PC doesn't host sessions: link one that does (Settings → PCs)"}, status=409)
    # the page's files are open (it shows the pairing screen); the phone app isn't: its build carries the host list
    # (app/build.sh's hosts.json), so only paired devices download it, or a browser holding an app link (/api/app/link)
    if p in OPEN or p.startswith("/static/") and p != "/static/deck.apk" or auth.is_host(request.headers.get("Authorization")) \
            or p == "/static/deck.apk" and app_link_ok(request.query.get("t", "")):
        return await handler(request)
    dev = auth.device_for(request.cookies.get(auth.COOKIE))
    if dev and not dev.get("pending"):
        request["device"] = dev
    if not (auth.enforced() or via_tunnel(request)) or is_local(request):
        return await handler(request)
    if not dev:
        return web.json_response({"error": "this device is not paired", "unpaired": True}, status=401)
    if dev.get("pending"):
        return web.json_response({"error": "waiting for approval", "pending": True}, status=403)
    return await handler(request)


# Calls that change shared state. On a follower they go to the writer (deck/site.py: one writer), and the page gets
# the writer's answer; the journal brings the change back. Session starts and sends stay local: sessions are per node.
WRITES = re.compile(r"/api/(buttons|settings|shelf|saves|session-trash|devices)(/|$)")


@web.middleware
async def site_forward(request, handler):
    if request.method in ("POST", "PUT", "DELETE") and WRITES.match(request.path) and site.role() != "writer" \
            and not auth.is_host(request.headers.get("Authorization")):
        w = site.writer()
        if w and not w.get("current"):
            import aiohttp
            headers = {"Authorization": f"Bearer {auth.host_key()}", "X-Deck-Device": sender(request)}
            if request.content_type and request.can_read_body:
                headers["Content-Type"] = request.headers["Content-Type"]
            try:
                async with request.app["peer_http"].request(request.method, f"{w['url'].rstrip('/')}{request.path_qs}",
                                                            data=await request.read() or None, headers=headers,
                                                            timeout=aiohttp.ClientTimeout(total=60, sock_connect=4)) as r:
                    return web.Response(body=await r.read(), status=r.status,
                                        content_type=r.headers.get("Content-Type", "application/json").split(";")[0])
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                pass  # the writer just went away: apply here; the next writer takes this node's journal in (replay)
    return await handler(request)


@web.middleware
async def revalidate(request, handler):
    # Phones (esp. home-screen PWAs) otherwise cache the page heuristically and keep running stale UI.
    resp = await handler(request)
    if not resp.prepared:  # never inside another site's frame: this host's own browser gets in unpaired (clickjacking)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    if request.path == "/" or request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    elif request.path.startswith("/api/"):  # an open page reloads when this changes (it would run stale code)
        resp.headers["X-Deck-Assets"] = assets_version()
    return resp


def assets_version() -> str:
    return str(max(int(f.stat().st_mtime) for f in [*STATIC.iterdir(), *APP_PARTS.iterdir()] if f.is_file()))


# The page's script, kept as parts by area (deck/static/app/, in APP_ORDER) and served joined as /static/app.js: one
# script, so the page runs as it always has (functions hoist across all of it; nothing runs between the parts).
APP_PARTS = STATIC / "app"
APP_ORDER = ("1-core", "2-pad", "3-terminal", "4-chat", "5-shelf", "6-review", "7-editor", "8-settings", "9-start")


def app_js() -> str:
    return "\n".join((APP_PARTS / f"{n}.js").read_text(encoding="utf-8").rstrip("\n") + "\n" for n in APP_ORDER)


async def app_script(request):
    # Joined text has no file to revalidate against: the assets version is its ETag, so an unchanged page costs a 304
    tag = f'"{assets_version()}"'
    if request.headers.get("If-None-Match") == tag:
        return web.Response(status=304, headers={"ETag": tag})
    return web.Response(text=await asyncio.to_thread(app_js), content_type="text/javascript", headers={"ETag": tag})


def bad_request(e: Exception):
    return web.json_response({"error": str(e)}, status=400)


async def get_buttons(_):
    return web.json_response(core.load_buttons())


async def put_button(request):
    try:
        return web.json_response(core.upsert_button(await request.json()))
    except ValueError as e:
        return bad_request(e)


async def del_button(request):
    core.delete_button(request.match_info["id"])
    return web.json_response({"ok": True})


async def run_key(request):
    """Run a key sent whole: another host's pad launching one of its keys here (its "Run on" host)."""
    try:
        body = await request.json()
        return web.json_response({"session": core.start_run({**core.clean_button(body), "host": "", **core.run_arg(body)})})
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def run_button(request):
    b = next((x for x in core.load_buttons() if x["id"] == request.match_info["id"]), None)
    if b is None:
        raise web.HTTPNotFound()
    # Optional one-off overrides from the ask sheet; the saved key is unchanged.
    body = await request.json() if request.can_read_body else {}
    b = {**b, **{k: str(body[k]).strip() for k in ("text", "cwd") if body.get(k)}}
    if "yolo" in body and b["type"] != "shell":
        b["yolo"] = bool(body["yolo"])
    b = {**b, **core.run_arg(body)}
    try:
        return web.json_response({"session": core.start_run(b)})
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def get_screen(request):
    cfg = core.screen()
    # Over iroh the tab works only when Deck serves the bridge itself (vnc.json "bridge"); else it stays hidden (user:
    # optional features off until set up, never an empty panel)
    if cfg["enabled"] and via_tunnel(request) and not cfg["bridge"]:
        return web.json_response({"enabled": False, "why": "Screen over iroh needs \"bridge\" in vnc.json (docs/screen.md)"})
    return web.json_response(cfg)


async def vnc_bridge(request):
    """/vnc/...: the Screen tab's WebSocket, piped to websockify on loopback (vnc.json "bridge"), so the tab works on
    every way in, iroh included. Paired devices only, like every other route."""
    bridge = core.vnc_bridge()
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
    if await asyncio.to_thread(core.vnc_listening):
        return
    if not await asyncio.to_thread(core.vnc_service, "on"):
        return  # no "Deck VNC on" task: the server is always on (docs/screen.md)
    for _ in range(40):
        await asyncio.sleep(0.25)
        if await asyncio.to_thread(core.vnc_listening):
            return


async def pipe_vnc(request, bridge):
    import aiohttp
    await vnc_up()
    try:
        up = await request.app["peer_http"].ws_connect(f"ws://{bridge}/{request.match_info['tail']}", protocols=("binary",),
                                                       max_msg_size=0)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        raise web.HTTPBadGateway(text="the VNC bridge isn't running")
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
        if not state["streams"] and time.time() - state["idle"] > VNC_IDLE and core.vnc_bridge():
            state["idle"] = float("inf")  # stopped: nothing to do until a stream comes and goes
            try:
                await asyncio.to_thread(core.vnc_service, "off")
            except (OSError, subprocess.SubprocessError):
                pass
        await asyncio.sleep(15)


PHONE_IDLE = 180  # s without a streaming page's keep-alive before the phone screen is detached


async def phone_screen(request):
    """GET: the phone screen's state (null if this host has none). POST {on, w, h}: attach at w×h, or detach.
    POST {keep: true}: a page still streaming to it (every minute), so phone_idle_loop leaves it attached."""
    if request.method == "GET":
        return web.json_response(await asyncio.to_thread(core.phone_screen))
    body = await request.json()
    if body.get("keep"):
        request.app["phonestate"]["seen"] = time.time()
        return web.json_response({"ok": True})
    try:
        if body.get("on"):
            res = await asyncio.to_thread(core.phone_screen, "on", int(body["w"]), int(body["h"]))
            request.app["phonestate"]["seen"] = time.time()
        else:
            res = await asyncio.to_thread(core.phone_screen, "off")
            request.app["phonestate"]["seen"] = None
    except (KeyError, ValueError, RuntimeError) as e:
        return bad_request(e)
    return web.json_response(res)


async def phone_idle_loop(app):
    """The phone screen is a real monitor to Windows (windows land on it, the desktop is wider), so it stays attached
    only while a page streams it (user: "disable the virtual screens whenever not streaming"). The page detaches it on
    Stop; this catches a closed tab or a sleeping phone, and one left on before Deck started."""
    try:
        on = (await asyncio.to_thread(core.phone_screen) or {}).get("on")
    except (OSError, subprocess.SubprocessError):
        on = False
    app["phonestate"]["seen"] = time.time() if on else None
    while True:
        await asyncio.sleep(30)
        seen = app["phonestate"]["seen"]
        if seen is not None and time.time() - seen > PHONE_IDLE:
            app["phonestate"]["seen"] = None
            try:
                await asyncio.to_thread(core.phone_screen, "off")
            except (OSError, subprocess.SubprocessError, RuntimeError):
                pass


async def phone_windows(request):
    """GET: the host desktop's app windows. POST {hwnd, phone}: move one onto the phone screen (or back)."""
    if request.method == "GET":
        return web.json_response(await asyncio.to_thread(core.phone_screen, "windows"))
    body = await request.json()
    try:
        res = await asyncio.to_thread(core.phone_screen, "move", int(body["hwnd"]), 1 if body.get("phone") else 0)
    except (KeyError, ValueError, RuntimeError) as e:
        return bad_request(e)
    return web.json_response(res)


async def get_hosts(_):
    return web.json_response(core.hosts())


async def notes_git(request):
    """Serve the notes repo to peer Decks (git smart HTTP via `git http-backend`); tailnet-only like the rest."""
    env = {**os.environ, "GIT_PROJECT_ROOT": str(core.NOTES), "GIT_HTTP_EXPORT_ALL": "1",
           "PATH_INFO": "/.git/" + request.match_info["path"], "REQUEST_METHOD": request.method,
           "QUERY_STRING": request.query_string, "CONTENT_TYPE": request.headers.get("Content-Type", ""),
           "REMOTE_ADDR": request.remote or "", "REMOTE_USER": "deck"}
    if request.headers.get("Content-Encoding"):
        env["HTTP_CONTENT_ENCODING"] = request.headers["Content-Encoding"]
    if request.headers.get("Git-Protocol"):
        env["GIT_PROTOCOL"] = request.headers["Git-Protocol"]
    body = await request.read()
    env["CONTENT_LENGTH"] = str(len(body))
    p = await asyncio.create_subprocess_exec("git", "http-backend", env=env, stdin=asyncio.subprocess.PIPE,
                                             stdout=asyncio.subprocess.PIPE)
    out, _ = await p.communicate(body)
    head, _, payload = out.partition(b"\r\n\r\n")
    status, headers = 200, {}
    for line in head.decode("latin-1").split("\r\n"):
        k, _, v = line.partition(":")
        if k.lower() == "status":
            status = int(v.split()[0])
        elif k:
            headers[k] = v.strip()
    return web.Response(status=status, body=payload, headers=headers)


_notes_status = {"at": 0.0, "data": None}


async def get_notes(request):
    """Sync status against every peer (fetches, so cached for a minute unless ?fresh=1)."""
    if request.query.get("fresh") or time.time() - _notes_status["at"] > 60:
        _notes_status["data"] = await asyncio.get_running_loop().run_in_executor(None, notes.status)
        _notes_status["at"] = time.time()
    return web.json_response(_notes_status["data"])


async def get_notes_bundle(_):
    """Every notes file in one response: the phone keeps it (service worker cache) as its offline copy."""
    def bundle():
        return {"head": notes.head(), "files": {f: notes.read(f) for f in notes.files()}}
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, bundle))


async def post_notes_carry(request):
    """Phone app: trade notes copies (see notes.carry). Body {bundle: base64 | null, edits: [...], device}."""
    body = await request.json()
    try:
        bundle = base64.b64decode(body["bundle"]) if body.get("bundle") else None
    except ValueError as e:
        return bad_request(e)
    r = await asyncio.get_running_loop().run_in_executor(
        None, notes.carry, bundle, body.get("edits") or [], str(body.get("device") or "phone")[:40])
    return web.json_response({**r, "bundle": base64.b64encode(r["bundle"]).decode(), "host": core.HOST_NAME})


async def service_worker(_):
    # Served from / so its scope covers the whole page.
    return web.FileResponse(STATIC / "sw.js", headers={"Cache-Control": "no-cache", "Content-Type": "text/javascript"})


def frame_blocked(headers) -> bool:
    """A page's response headers forbid showing it in another site's frame (X-Frame-Options, CSP frame-ancestors)."""
    xfo = headers.get("X-Frame-Options", "").strip().lower()
    if xfo in ("deny", "sameorigin") or xfo.startswith("allow-from"):
        return True
    for csp in headers.getall("Content-Security-Policy", []):
        for d in csp.split(";"):
            parts = d.split()
            if parts and parts[0].lower() == "frame-ancestors":
                return "*" not in parts[1:]
    return False


async def web_check(request):  # the Web view: may this page be shown in a frame? null when it can't be reached
    import aiohttp
    url = request.query.get("url", "")
    if not re.fullmatch(r"https?://[^\s/]+\S*", url):
        return bad_request(ValueError("url must be an http(s) address"))
    try:
        async with request.app["peer_http"].get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=8),
                                                headers={"User-Agent": "Mozilla/5.0 (Deck web view check)"}) as r:
            return web.json_response({"frameable": not frame_blocked(r.headers)})
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return web.json_response({"frameable": None})


async def get_notes_files(_):
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, notes.files))


async def get_notes_file(request):
    try:
        return web.json_response({"path": request.query["path"], "text": notes.read(request.query.get("path", ""))})
    except (ValueError, KeyError, FileNotFoundError) as e:
        return bad_request(e)


async def put_notes_file(request):
    body = await request.json()
    try:
        new = await asyncio.get_running_loop().run_in_executor(None, notes.write, str(body.get("path", "")), str(body.get("text", "")))
    except (ValueError, RuntimeError) as e:
        return bad_request(e)
    _notes_status["at"] = 0.0
    return web.json_response({"head": new})


# ---- pairing ----

# App links: the phone app can't save a download itself (its page is a WebView), so Settings → More → App asks for a
# link that the phone's browser, unpaired, may open for ten minutes: the APK carries the host list, so a link is
# short-lived and random (hashes kept, not tokens). Not one-use: an older app's WebView may swallow the first open.
APP_LINK_TTL = 600
_app_links: dict[str, float] = {}  # sha256(token) -> expiry


def app_link_ok(token: str) -> bool:
    now = time.time()
    for h, exp in list(_app_links.items()):
        if exp < now:
            _app_links.pop(h, None)
    return bool(token) and hashlib.sha256(token.encode()).hexdigest() in _app_links


async def app_link(request):
    """POST /api/app/link (a paired device or this host's browser): a short-lived link to /static/deck.apk."""
    if not (STATIC / "deck.apk").exists():
        return web.json_response({"error": "no app build on this host (app/build.sh, or a release's APK at deck/static/deck.apk)"}, status=404)
    token = secrets.token_urlsafe(24)
    _app_links[hashlib.sha256(token.encode()).hexdigest()] = time.time() + APP_LINK_TTL
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme
    url = f"{scheme}://{request.host}/static/deck.apk?t={token}"
    return web.json_response({"url": url, "expires": APP_LINK_TTL})


async def get_me(request):
    dev = auth.device_for(request.cookies.get(auth.COOKIE), touch=False)
    return web.json_response({"enforced": (auth.enforced() or via_tunnel(request)) and not is_local(request), "local": is_local(request),
                              "device": dev and {k: dev[k] for k in ("id", "name")},
                              "pending": bool(dev and dev.get("pending"))})


def _approver(request) -> str:
    # Before the first device is approved (open mode) an unpaired browser can make codes; it approves as "open".
    # This host's own browser approves as "host", like `deck-pair`.
    return request.get("device", {}).get("id") or ("host" if is_local(request) else "open")


async def pair_start(request):
    # iroh: this host's key when deck-iroh runs, so the QR can pair the app over iroh (no Tailscale needed).
    try:
        iroh = IROH_ID.read_text().strip()
    except OSError:
        iroh = None
    # url: this host's address for other devices (inventory deck_url); a page opened at 127.0.0.1 can't use its own.
    url = next((h["url"] for h in core.hosts()["hosts"] if h["current"]), None)
    return web.json_response(auth.new_code(_approver(request)) | {"iroh": iroh, "name": core.HOST_NAME, "url": url})


async def pair_claim(request):
    body = await request.json()
    try:
        token = auth.claim(str(body.get("code", "")), str(body.get("name", "")))
    except ValueError as e:
        return bad_request(e)
    resp = web.json_response({"ok": True})
    local = request.host.split(":")[0] in ("127.0.0.1", "localhost")
    resp.set_cookie(auth.COOKIE, token, max_age=10 * 365 * 86400, httponly=True, secure=not local, samesite="Strict")
    return resp


async def pair_pending(request):
    return web.json_response({"device": auth.pending(request.query.get("code", ""))})


async def pair_decide(request):
    body = await request.json()
    try:
        auth.decide(str(body.get("code", "")), _approver(request), bool(body.get("allow")))
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def pair_qr(request):
    import io
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(request.query.get("url", ""), image_factory=qrcode.image.svg.SvgPathImage, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return web.Response(body=buf.getvalue(), content_type="image/svg+xml")


# ---- linked PCs (links.py): A makes a code and approves; B pastes it ----

async def link_start(_):
    try:
        return web.json_response(links.new_code())
    except ValueError as e:
        return bad_request(e)


async def link_claim(request):
    body = await request.json()
    try:
        token = links.claim(str(body.get("secret", "")), str(body.get("name", "")), str(body.get("iroh", "")),
                            bool(body.get("hosting", True)))
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"token": token, "host": core.HOST_NAME})


async def link_wait(request):
    try:
        return web.json_response(links.wait(request.query.get("secret", ""), request.query.get("token", "")))
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=404)


async def link_pending(request):
    return web.json_response({"pc": links.pending(request.query.get("secret", ""))})


async def link_decide(request):
    body = await request.json()
    try:
        links.decide(str(body.get("secret", "")), bool(body.get("allow")))
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def link_join(request):
    if request.method == "GET":
        return web.json_response({**links.joining, "why": links.can_join()})
    body = await request.json()
    try:
        return web.json_response(await links.join(request.app["peer_http"], str(body.get("code", ""))))
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def link_rekey(request):
    if not auth.is_host(request.headers.get("Authorization")):  # only another member, with the group's key
        return web.json_response({"error": "members only"}, status=403)
    body = await request.json()
    try:
        links.rekey(str(body.get("key", "")), str(body.get("removed", "")))
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def link_remove(request):
    try:
        return web.json_response(await links.remove(request.app["peer_http"], request.match_info["name"]))
    except ValueError as e:
        return bad_request(e)


async def link_leave(_):
    links.leave()
    return web.json_response({"ok": True})


async def get_deck_context(_):  # the instructions Deck gives every agent (core.deck_context): debug mode shows them
    return web.json_response({"text": core.deck_context()})


async def get_links(_):
    return web.json_response({"me": core.HOST_NAME, "members": links.members(), "iroh": bool(links.iroh_id())})


async def get_devices(request):
    me = request.get("device", {}).get("id")
    return web.json_response([d | {"me": d["id"] == me} for d in auth.devices()])


async def del_device(request):
    auth.remove(request.match_info["id"])
    return web.json_response({"ok": True})


async def get_settings(_):
    s = core.settings()
    # fresh: nothing saved yet (a new install), so the page asks whether this PC hosts sessions.
    return web.json_response({**s, "fresh": not core.settings_path().exists(), "pi": core.pi_available(), "agy": core.agy_available(), "openclaw": core.openclaw_available(), "agents": core.agents_installed(), "chatagents": core.CHAT_AGENTS,
                              "durable": core.pi_durable_available(), "notesname": core.NOTES.name, "transcribe": bool(TRANSCRIBE_URL)})


async def transcribe(request):
    if not TRANSCRIBE_URL:
        return web.json_response({"error": "no DECK_TRANSCRIBE_URL on this host"}, status=404)
    import aiohttp
    audio = await request.read()
    try:
        async with request.app["peer_http"].post(TRANSCRIBE_URL, data=audio, timeout=aiohttp.ClientTimeout(total=120, sock_connect=3),
                                                 headers={"Content-Type": request.content_type}) as r:
            return web.json_response(await r.json(content_type=None), status=r.status)
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        return web.json_response({"error": f"speech-to-text not reachable ({e.__class__.__name__}): is it running?"}, status=502)


async def put_settings(request):
    try:
        saved = core.save_settings(await request.json())
    except ValueError as e:
        return bad_request(e)
    links.update_me()
    return web.json_response(saved)


async def get_folders(_):
    return web.json_response(core.folders())


async def new_agent(request):
    try:
        body = await request.json()
        b = core.new_agent_button(str(body.get("agent", "claude")), str(body.get("text", "")).strip(),
                                  str(body.get("cwd", "")).strip(), bool(body.get("yolo", core.settings()["yolo"])))
        return web.json_response({"session": core.start_run(b)})
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def build_key(request):
    try:
        body = await request.json()
        b = core.builder_button(str(body.get("request", "")), str(body.get("agent", "claude")))
        return web.json_response({"session": core.start_run(b)})
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def get_sessions(_):
    # Off the event loop: listing takes ~0.1 s of tmux calls on Windows, which would stall every live terminal.
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, core.list_sessions))


# ---- every host's sessions in one column; another host's session opens through /peer/<host>/ (user) ----
PEER_WANTED = 20  # peers are polled only while a page asked for every host's sessions this recently (seconds)


async def get_all_sessions(request):
    """{hosts: [{name, online, sessions}]}: this host's live, the others' from the cache peers_loop keeps (an offline
    host keeps its last list, online false; null: not reached yet)."""
    request.app["peerstate"]["wanted"] = time.time()
    local = await asyncio.get_running_loop().run_in_executor(None, core.list_sessions)
    seen = request.app["peerstate"]["seen"]
    out = [{"name": h["name"], "online": True, "sessions": local} if h["current"] else
           {"name": h["name"], **seen.get(h["name"], {"online": None, "sessions": []})} for h in core.hosts()["hosts"]]
    # a linked PC whose forwarder is down is not in core.hosts(): still a node, offline, its chats in the copy
    for name in site.nodes():
        if not any(h["name"] == name for h in out) and name != core.HOST_NAME:
            out.append({"name": name, "online": False, "sessions": seen.get(name, {}).get("sessions", [])})
    for h in out:
        if h["online"] is False:  # its chats are in every node's copy of the state: readable, and Transfer here works
            if not h["sessions"]:  # it died before this node polled it (a restart here): its chats from the copy
                h["sessions"] = await asyncio.to_thread(site.copied_chats, h["name"])
            for s in h["sessions"]:
                if s.get("chat"):
                    f = core.CHAT_DIR / f"{s['name']}.jsonl"
                    if f.exists():
                        s["copy"] = f.stat().st_mtime
    if not any(h["name"] == core.HOST_NAME for h in out):  # a host missing from the inventory still lists its own
        out.insert(0, {"name": core.HOST_NAME, "online": True, "sessions": local})
    # sync: the journal position, so an open page reloads shelf, scripts and settings when the state changed
    return web.json_response({"current": core.HOST_NAME, "hosts": out, "sync": {"seq": site.seq(), "role": site.role()},
                              "site": site.status()})


PEER_MISSES = 3  # failed polls in a row before a host shows offline: one slow reply on train Wi-Fi is not an outage (user)


async def refresh_peer(app, h: dict) -> None:
    """One other host's session list into the cache (offline after PEER_MISSES failures in a row: its last list, marked so)."""
    import aiohttp
    try:
        async with app["peer_http"].get(h["url"].rstrip("/") + "/api/sessions", headers={"Authorization": f"Bearer {auth.host_key()}"},
                                        timeout=aiohttp.ClientTimeout(total=4)) as r:
            if r.status == 200:
                app["peerstate"]["seen"][h["name"]] = {"online": True, "sessions": await r.json(), "misses": 0}
                return
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError):
        pass
    old = app["peerstate"]["seen"].get(h["name"], {})
    misses = old.get("misses", 0) + 1
    app["peerstate"]["seen"][h["name"]] = {"online": False if misses >= PEER_MISSES else old.get("online"),
                                           "sessions": old.get("sessions", []), "misses": misses}


async def peers_loop(app):
    while True:
        if time.time() - app["peerstate"]["wanted"] < PEER_WANTED and auth.host_key():
            await asyncio.gather(*(refresh_peer(app, h) for h in core.hosts()["hosts"] if not h["current"]))
        await asyncio.sleep(3)


async def peer_proxy(request):
    """/peer/<host>/api/... and /peer/<host>/ws/...: the same call on another Deck host, with the host key. The page
    works on another host's sessions through the host that served it (the phone app reaches only that one). Also
    /peer/<host>/vnc/...: that host's desktop for the Screen tab (its VNC bridge, on its origin), a WebSocket only."""
    import aiohttp
    _, _, name, tail = request.rel_url.raw_path.split("/", 3)  # tail still URL-encoded, as the page sent it
    peer = next((h for h in core.hosts()["hosts"] if h["name"] == request.match_info["host"] and not h["current"]), None)
    if not peer or not tail.startswith(("api/", "ws/", "vnc/")):
        raise web.HTTPNotFound()
    if tail.startswith("vnc/") and request.headers.get("Upgrade", "").lower() != "websocket":
        raise web.HTTPNotFound()
    base, key = peer["url"].rstrip("/"), auth.host_key()
    headers = {"Authorization": f"Bearer {key}", "X-Deck-Device": sender(request)}
    if request.headers.get("Upgrade", "").lower() == "websocket":
        url = re.sub(r"^http", "ws", base) + "/" + tail
        try:  # no size limit: a long chat's history comes as one message
            up = await request.app["peer_http"].ws_connect(url, params=request.query, headers=headers, heartbeat=25, max_msg_size=0,
                                                           timeout=aiohttp.ClientWSTimeout(ws_close=5)
                                                           if hasattr(aiohttp, "ClientWSTimeout") else 5)  # aiohttp < 3.10
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            raise web.HTTPBadGateway(text=f"{request.match_info['host']} is offline")
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
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
        return web.json_response({"error": f"{request.match_info['host']} is offline"}, status=502)
    if request.method != "GET":  # a change there (closed, renamed, new): the column shows it at the next poll
        await refresh_peer(request.app, peer)
    # This host's assets version, not the peer's: the page runs this host's code
    return web.Response(status=status, body=body, headers={"Content-Type": ctype, "X-Deck-Assets": assets_version()})


async def del_session(request):
    name = request.match_info["name"]
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, session_trash.keep, name)  # an agent chat waits in the trash, restorable
    await loop.run_in_executor(None, core.kill_session, name)
    return web.json_response({"ok": True})


# ---- closed sessions' trash (deck/session_trash.py) ----
async def get_session_trash(_):
    return web.json_response(session_trash.load())


async def restore_session(request):
    try:
        name = await asyncio.get_running_loop().run_in_executor(None, session_trash.restore, request.match_info["id"])
    except StopIteration:
        raise web.HTTPNotFound()
    except (ValueError, RuntimeError) as e:
        return bad_request(e)
    return web.json_response({"session": name})


async def forget_session(request):
    session_trash.delete(request.match_info["id"])
    return web.json_response({"ok": True})


async def send_session(request):
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    if core.is_chat(name):  # Agents feed reply to a chat session: a message for its bridge
        if body.get("text"):
            core.chat_command(name, {"t": "say", "text": str(body["text"]), "from": sender(request, "Agents feed")})
        elif body.get("key") in ("esc", "ctrlc"):
            core.chat_command(name, {"t": "abort"})
        return web.json_response({"ok": True})
    try:
        nav = int(body["nav"]) if "nav" in body else None
        core.send_input(name, str(body.get("text", "")), str(body.get("key", "")), bool(body.get("enter")), nav)
    except (ValueError, TypeError) as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def session_text(request):
    """The session's output as plain text for copying: tmux history plus the screen (full-screen agents: the screen)."""
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    return web.json_response({"text": await asyncio.to_thread(core.session_output, name)})


async def session_describe(request):
    """The tab's description (deck/describe.py): {text, at}; empty until the agent's first turn after it starts."""
    d = describe.get(request.match_info["name"])
    return web.json_response({"text": d.get("text", ""), "at": d.get("at", 0)})


# ---- agents see and message the other sessions on this host, while Settings allows it ----

def agent_sessions_gate(request):
    """This host's own setting, for its own agents and other hosts' alike (each host decides for its sessions)."""
    if not core.settings().get("sessionagents"):
        raise web.HTTPForbidden(text="The user hasn't let agents see other sessions (Deck: Settings > Agents can see sessions).\n")


def agent_session_lines(sessions: list[dict], descs: dict) -> list[str]:
    out = []
    for s in sessions:
        if s["type"] == "terminal":
            continue
        line = f"- `{s['name']}`: {s.get('title') or s['label']} ({s['type']}, {s['state']})"
        if desc := (descs.get(s["name"]) or {}).get("text"):
            line += f"\n  {desc}"
        out.append(line)
    return out or ["(none)"]


async def agent_sessions(request):
    """The sessions as an agent reads them: markdown, one per line, with its description. Without ?here=1, the other
    hosts' too (each lists its own), under their host's heading."""
    import aiohttp
    agent_sessions_gate(request)
    sessions, descs = await asyncio.to_thread(core.list_sessions), describe.load()
    out = [f"# {core.HOST_NAME} (this host)", "", *agent_session_lines(sessions, descs)]
    if request.query.get("here") == "1" or not auth.host_key():
        return web.Response(text="\n".join(out) + "\n", content_type="text/markdown")

    async def peer(h):
        try:
            async with request.app["peer_http"].get(h["url"].rstrip("/") + "/api/agent/sessions?here=1",
                                                    headers={"Authorization": f"Bearer {auth.host_key()}"},
                                                    timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    return (await r.text()).split("\n", 2)[-1].strip()  # its own heading off
                if r.status == 403:
                    return "(that host doesn't let agents see its sessions)"
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            pass
        return "(offline)"
    peers = [h for h in core.hosts()["hosts"] if not h["current"]]
    for h, text in zip(peers, await asyncio.gather(*map(peer, peers))):
        out += ["", f"# {h['name']} (reach its sessions at /peer/{h['name']}/api/agent/sessions/NAME)", "", text]
    return web.Response(text="\n".join(out) + "\n", content_type="text/markdown")


async def agent_session_read(request):  # the end of one session's output: ?chars= (default 6000)
    agent_sessions_gate(request)
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound(text=f"no session {name}\n")
    try:
        n = max(200, min(50_000, int(request.query.get("chars") or 6000)))
    except ValueError as e:
        return bad_request(e)
    text = (await asyncio.to_thread(core.session_output, name))[-n:]
    desc = describe.get(name).get("text")
    return web.Response(text=(f"Description: {desc}\n\n" if desc else "") + text, content_type="text/markdown")


async def agent_session_send(request):
    """An agent messages another session: {text, from (its DECK_SESSION_NAME)}. Typed in and submitted, marked as
    from that agent so the reader knows it isn't the user."""
    agent_sessions_gate(request)
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound(text=f"no session {name}\n")
    try:  # an agent's shell quoting often breaks the JSON: say so, not a bare 500
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return bad_request(ValueError(
            'the body isn\'t a JSON object like {"text": "...", "from": "HOST/SESSION"}. PowerShell drops the quotes in '
            "curl.exe -d's JSON: use Invoke-RestMethod -Method Post -ContentType application/json -Uri URL "
            "-Body (@{text='...'; from=\"HOST/$env:DECK_SESSION_NAME\"} | ConvertTo-Json)"))
    text, frm = str(body.get("text", "")).strip(), " ".join(str(body.get("from") or "").replace("]", ")").split())[:80]
    if not text:
        return bad_request(ValueError("text is empty"))
    if text.startswith("/"):  # a chat would run it as the user's command (/compact, /resume), with no sender on it
        return bad_request(ValueError("text starts with / (a command): reword it"))
    if frm in (name, f"{core.HOST_NAME}/{name}"):  # from: host/session
        return bad_request(ValueError("that's your own session"))
    if core.session_meta(name).get("agent") not in core.AGENTS:  # a shell would run the message as a command
        return bad_request(ValueError(f"{name} isn't an agent session"))
    if any(s["name"] == name and s["state"] == "needs_you" for s in await asyncio.to_thread(core.list_sessions)):
        # the text would go into its open question as the user's answer
        raise web.HTTPConflict(text=f"{name} is waiting on the user's answer to a question: try again later\n")
    # chat_bridge.AGENT_FROM knows an agent's message by these words: the chat labels it, so no one takes it for the user's
    who = f"agent in {frm}" if frm else "another agent"
    if core.is_chat(name):
        core.chat_command(name, {"t": "say", "text": text, "from": who})
    else:
        await core.type_line(name, f"[from {who}, not the user] {text}")
    return web.Response(text=f"sent to {name}\n")


# ---- the shelf (deck/shelf.py) ----

DOC_PREVIEW = 3000  # characters of a document's text in the shelf's list (a PDF's runs to 100k+); the rest: GET /api/shelf/{id}


async def get_shelf(_):
    items = shelf.load()
    for it in items:
        if it.get("kind") == "doc" and len(it.get("text") or "") > DOC_PREVIEW:
            it["text"], it["cut"] = it["text"][:DOC_PREVIEW], len(it["text"])
    return web.json_response(items)


async def get_shelf_item(request):  # one item whole (a document's full text); a linked file's as it is on disk now
    items = shelf.load()
    it = next((x for x in items if x.get("id") == request.match_info["id"]), None)
    if not it:
        raise web.HTTPNotFound()
    if (it.get("file") or {}).get("linked") and it["file"].get("host") == core.HOST_NAME \
            and await asyncio.to_thread(shelf.refresh_linked, it):
        await asyncio.to_thread(shelf.save_item, it)
    return web.json_response(it)


async def link_shelf(request):  # a file on this host as a document, not a copy: {path, title, session}
    try:
        body = await request.json()
        meta = core.session_meta(str(body.get("session", ""))) if body.get("session") else {}
        return web.json_response(await asyncio.to_thread(shelf.link_file, body, meta,
                                                         core.settings().get("shelfnew") == "top"))
    except ValueError as e:
        return bad_request(e)


async def shelf_mtime(request):  # a linked file's change time, which the reader polls to redraw it when it changes
    f = shelf.file_path(request.match_info["id"])
    if f is None:
        raise web.HTTPNotFound()
    st = f.stat()
    return web.json_response({"mtime": st.st_mtime, "size": st.st_size})


async def put_shelf(request):
    try:
        body = await request.json()
        meta = core.session_meta(str(body.get("session", ""))) if body.get("session") else {}
        return web.json_response(shelf.add(body, meta, top=core.settings().get("shelfnew") == "top"))
    except ValueError as e:
        return bad_request(e)


async def pin_shelf(request):  # an agent pins a web page: {url, title, note, session}; the same url updates its item
    try:
        body = await request.json()
        name = str(body.get("session", ""))
        if body.get("main") and body.get("folder") and core.session_exists(name):  # the session works in that project now
            core.set_session_project(name, folder=str(body["folder"]))
        meta = core.session_meta(name) if name else {}
        return web.json_response(shelf.pin(body, meta, top=core.settings().get("shelfnew") == "top"))
    except ValueError as e:
        return bad_request(e)


async def put_shelf_file(request):  # a document's file: the raw bytes, ?name=&type=
    try:
        data = await request.read()
        return web.json_response(await asyncio.to_thread(shelf.put_file, request.match_info["id"],
                                                         request.query.get("name", ""), request.query.get("type", ""), data))
    except ValueError as e:
        return bad_request(e)


async def edit_shelf_text(request):  # a text document saved from the reader: {text}
    try:
        text = str((await request.json()).get("text", ""))
        return web.json_response(await asyncio.to_thread(shelf.edit_text, request.match_info["id"], text))
    except ValueError as e:
        return bad_request(e)


async def get_shelf_file(request):
    f = shelf.file_path(request.match_info["id"])
    if f is None:
        raise web.HTTPNotFound(text="that document's file is on another host, or gone")
    it = next((x for x in shelf.load() if x["id"] == request.match_info["id"]), {})
    return web.FileResponse(f, headers={"Content-Type": (it.get("file") or {}).get("type") or "application/octet-stream",
                                        "Cache-Control": "no-cache" if (it.get("file") or {}).get("linked")
                                        else "private, max-age=3600"})


async def del_shelf(request):  # ?forever=1 skips the trash
    shelf.delete(request.match_info["id"], request.query.get("forever") == "1")
    return web.json_response({"ok": True})


async def restore_shelf(request):
    shelf.restore(request.match_info["id"])
    return web.json_response({"ok": True})


async def arrange_shelf(request):  # a drag: {order: [{id, folder}], folders: [{id, title}]}
    body = await request.json()
    shelf.arrange(body.get("order") or [], body.get("folders") or [])
    return web.json_response({"ok": True})


async def agent_shelf(request):  # agents on this host read the shelf, while the shelf's checkbox allows it
    if not core.settings().get("shelfagents"):
        return web.Response(status=403, text="The user hasn't let agents read the shelf (Deck: shelf > Agents can read).\n")
    q = request.query
    return web.Response(text=shelf.for_agent(q.get("id", ""), q.get("all") == "1"), content_type="text/markdown")


async def autoname_shelf(request):  # a folder named from its contents (slow: asks Claude)
    try:
        return web.json_response({"title": await asyncio.to_thread(shelf.autoname, request.match_info["id"])})
    except ValueError as e:
        return bad_request(e)


async def main_shelf(request):  # {project}: this link is that project's main page; "": not any more
    try:
        shelf.main(request.match_info["id"], str((await request.json()).get("project", "")))
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def rename_shelf(request):
    shelf.rename(request.match_info["id"], (await request.json()).get("title", ""))
    return web.json_response({"ok": True})


# ---- saved sessions (deck/saves.py) ----

async def get_saves(_):
    return web.json_response(saves.list_saves())


async def get_save(request):
    s = saves.get(request.match_info["id"])
    if not s:
        raise web.HTTPNotFound()
    return web.json_response(s)


async def put_save(request):
    """A save pushed by another host (or a client)."""
    try:
        return web.json_response(saves.store(await request.json()))
    except (ValueError, TypeError) as e:
        return bad_request(e)


async def delete_save(request):
    """{deleted: true|false}: delete (restorable for two months) or restore, here and on the other hosts."""
    body = await request.json()
    try:
        s = saves.set_deleted(request.match_info["id"], bool(body.get("deleted", True)))
    except KeyError:
        raise web.HTTPNotFound()
    asyncio.get_running_loop().create_task(saves.push(s))
    return web.json_response({"ok": True})


async def save_session(request):
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    if core.is_chat(name):  # saves type into the agent's terminal
        return bad_request(ValueError("Save isn't available in chat sessions yet"))
    try:
        return web.json_response({"id": await saves.start_save(name)})
    except ValueError as e:
        return bad_request(e)


async def load_session(request):
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    try:
        await saves.load(name, str(body.get("id", "")))
    except KeyError:
        raise web.HTTPNotFound()
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"ok": True})


# ---- Move host (deck/session_move.py): a chat carries on on another Deck host ----
async def move_session(request):
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    try:
        return web.json_response(await session_move.send(name, str(body.get("host", ""))))
    except ValueError as e:
        return bad_request(e)


async def move_in(request):  # from another host only (its host key)
    if not auth.is_host(request.headers.get("Authorization")):
        raise web.HTTPForbidden()
    try:
        return web.json_response(await asyncio.to_thread(session_move.receive, await request.read()))
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def project_session(request):  # {folder} its agent works in, or {project} (Harness → Project); neither: reset
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    try:
        project = await asyncio.to_thread(core.set_session_project, name, str(body.get("folder") or ""),
                                          str(body.get("project") or ""))
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"project": project})


async def rename_session(request):
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    await asyncio.to_thread(core.rename_session, name, str(body.get("title", "")))
    return web.json_response({"ok": True})


def host_only(request) -> None:
    if not auth.is_host(request.headers.get("Authorization")):
        raise web.HTTPForbidden()


# ---- one site on every node (deck/site.py): the writer serves it and journals every change; followers hold a copy.
# Host key only, except the two the page reads (site, offline chat text) ----
async def site_role(request):
    host_only(request)
    return web.json_response(site.status())


async def site_info(request):
    """For the page and the phone: who serves the site now, this node's role, and the order."""
    w = site.writer()
    return web.json_response({**site.status(), "writer_url": (w or {}).get("url", ""), "me": core.HOST_NAME})


async def site_journal(request):
    host_only(request)
    if request.method == "GET":  # a follower pulling what it misses
        after = int(request.query.get("after", "0") or 0)
        got = await asyncio.to_thread(site.entries_after, after)
        if got is None:
            return web.json_response({"error": "too far behind: take a snapshot"}, status=410)
        return web.json_response({"entries": got, "more": len(got) >= 500, "seq": site.seq()})
    body = await request.json()  # the writer pushing
    if site.role() == "writer":  # two writers: the lower one should not be pushing; refuse, it will demote
        return web.json_response({"error": "this node is the writer", "seq": site.seq()}, status=409)
    for entry in body.get("entries", []):
        await asyncio.to_thread(site.apply, entry)
    return web.json_response({"seq": site.seq()})


async def site_snapshot(request):
    host_only(request)
    return web.Response(body=await asyncio.to_thread(site.snapshot), content_type="application/gzip")


async def site_replay(request):  # a lower node wrote while this writer was away: take it into the journal
    host_only(request)
    if site.role() != "writer":
        return web.json_response({"error": "not the writer"}, status=409)
    body = await request.json()
    return web.json_response({"replayed": await asyncio.to_thread(site.replay, body.get("entries", []))})


async def site_offer(request):  # a follower's own chats (its sessions write them there): journaled here for every node
    host_only(request)
    if site.role() != "writer":
        return web.json_response({"error": "not the writer"}, status=409)
    body = await request.json()
    return web.json_response(await asyncio.to_thread(site.take_offer, body.get("entries", [])))


async def offline_chat_text(request):  # a dead node's chat, read only, from this node's copy of the state
    try:
        return web.json_response(await asyncio.to_thread(site.chat_text, request.match_info["session"]))
    except ValueError as e:
        return bad_request(e)


async def move_here(request):
    """Carry a dead node's chat on here from the copy: the session restarts on its conversation (session_move) and the
    takeover is recorded in the state, so the node closes its own tab when it is back."""
    try:
        return web.json_response(await asyncio.to_thread(site.move_here, request.match_info["host"], request.match_info["session"]))
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def restart_session(request):
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    try:
        core.restart_agent(name)
    except (ValueError, RuntimeError) as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def shell_session(request):
    body = await request.json() if request.can_read_body else {}
    try:
        return web.json_response({"session": core.ensure_shell(new=bool(body.get("new")))})
    except RuntimeError as e:
        return bad_request(e)


async def clear_sessions(_):
    return web.json_response({"cleared": core.clear_finished()})


async def update(_):
    """Pull the latest Deck code and, if it changed, restart this service (runs survive: KillMode=process)."""
    repo = Path(__file__).parent.parent

    async def git(*args):
        p = await asyncio.create_subprocess_exec("git", "-C", str(repo), *args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
        out = (await p.communicate())[0].decode().strip()
        return p.returncode, out

    code, out = await git("pull", "--ff-only", "-q")
    if code:
        return web.json_response({"error": out or "git pull failed"}, status=500)
    # Compare with the code this server started on: the checkout may already be ahead (committed here).
    old, new = RUNNING, (await git("rev-parse", "--short", "HEAD"))[1]
    if new != old:  # restart after the response is sent; tmux and its sessions keep running either way
        asyncio.get_running_loop().call_later(0.5, restart_windows if core.WINDOWS else lambda: os.spawnlp(
            os.P_NOWAIT, "systemctl", "systemctl", "--user", "restart", "deck"))  # systemd kills only python
    return web.json_response({"host": core.HOST_NAME, "old": old, "new": new, "restarting": new != old})


def restart_windows() -> None:
    """Windows (the PC app's server folder is a git checkout): stop this server. Under the PC app (DECK_SUPERVISED) the
    app starts the new one from the updated checkout; otherwise start it here first. The new one waits for the ports
    (bind). Same command as the PC app's (desktop/main.go, python())."""
    if not os.environ.get("DECK_SUPERVISED"):
        repo = Path(__file__).parent.parent
        uv = repo.parent / "uv.exe"  # bundled next to the PC app
        log = open(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "deck" / "server.log", "a")
        subprocess.Popen([str(uv) if uv.exists() else "uv", "run", "--no-project", "--python", "3.12", "--with",
                          "aiohttp", "--with", "qrcode", "--with", "pywinpty", "python", "-m", "deck.server"], cwd=repo,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    signal.raise_signal(signal.SIGINT)  # run_app's graceful stop (closes the terminal sockets)


def set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


PROMPT_LINE = re.compile(r"^\s*❯\s+\S")  # a prompt the user sent, as Claude Code / Codex show it in the transcript


def prev_prompt(name: str) -> None:
    """Desktop Back: scroll up to the user's previous prompt.
    Full-screen agents (no tmux history): wheel up until a prompt not on screen before reaches the top third.
    Shells: tmux copy-mode search back for a shell prompt."""
    target = f"={name}:"
    flags = core.tmux("display-message", "-p", "-t", target, "#{alternate_on} #{mouse_any_flag} #{pane_height}").stdout.split()
    if flags[:2] == ["1", "1"]:
        height = int(flags[2])
        screen = lambda: core.tmux("capture-pane", "-p", "-t", target).stdout.splitlines()
        seen = {l.strip() for l in screen() if PROMPT_LINE.match(l)}
        for _ in range(60):  # at most ~300 lines up
            core.tmux("send-keys", "-t", target, "-l", "\x1b[<64;1;1M" * 5)
            time.sleep(0.05)
            for row, line in enumerate(screen()[: max(3, height // 3)]):
                if PROMPT_LINE.match(line) and line.strip() not in seen:
                    return
        return
    if core.tmux("display-message", "-p", "-t", target, "#{pane_in_mode}").stdout.strip() != "1":
        core.tmux("copy-mode", "-t", target)
    # Step off the current line first, so the empty prompt being typed at (or the last match) isn't found again;
    # no wrap-around, so Back stops at the oldest prompt instead of jumping to the bottom.
    core.tmux("set-option", "-w", "-t", target, "wrap-search", "off")
    core.tmux("send-keys", "-t", target, "-X", "cursor-up")
    core.tmux("send-keys", "-t", target, "-X", "search-backward", r"[$#❯>] ")


PAGE_SCROLL: dict[str, int] = {}  # swipe lines not yet worth a PageUp/PageDown, per session


def scroll_terminal(name: str, lines: int) -> None:
    """Scroll tmux history (positive lines move toward newer output)."""
    target = f"={name}:"
    lines = max(-100, min(100, lines))
    if not lines:
        return
    # Full-screen apps with mouse reporting (Claude Code) keep no tmux history: send them wheel events instead.
    flags = core.tmux("display-message", "-p", "-t", target,
                      "#{alternate_on} #{mouse_any_flag} #{pane_in_mode} #{pane_height}").stdout.split()
    if flags[:3] == ["1", "1", "0"]:
        wheel = f"\x1b[<{64 if lines < 0 else 65};1;1M"
        core.tmux("send-keys", "-t", target, "-l", wheel * abs(lines))
        return
    if flags[:3] == ["1", "0", "0"]:
        # Full screen without mouse reporting (Pi): no history and no wheel; its viewport pages with PageUp/PageDown.
        # Swipes arrive a few lines at a time, so they add up to a page (half the pane) before a key goes.
        page = max(4, int(flags[3] if len(flags) > 3 and flags[3].isdigit() else 24) // 2)
        owed = PAGE_SCROLL.get(name, 0) + lines
        keys, owed = int(owed / page), owed - int(owed / page) * page
        PAGE_SCROLL[name] = owed
        if keys:
            core.tmux("send-keys", "-t", target, *(["PPage" if keys < 0 else "NPage"] * abs(keys)))
        return
    if lines < 0:
        core.tmux("copy-mode", "-t", target)
    elif core.tmux("display-message", "-p", "-t", target, "#{pane_in_mode}").stdout.strip() != "1":
        return
    core.tmux("send-keys", "-t", target, "-N", str(abs(lines)), "-X", "scroll-up" if lines < 0 else "scroll-down")
    if lines > 0 and core.tmux("display-message", "-p", "-t", target, "#{scroll_position}").stdout.strip() == "0":
        core.tmux("send-keys", "-t", target, "-X", "cancel")


async def terminal_input(name: str, m: dict, write) -> None:
    """A terminal socket message other than resize: keys (write them), a message box line (say), scroll, or Back.
    say goes straight into the session, not through this socket's client: Send closes the phone keyboard, the resize
    replaces the Windows client, and keys still queued in the old one were lost or overtaken by the Enter."""
    if m.get("t") in ("in", "say"):
        target = f"={name}:"
        if core.tmux("display-message", "-p", "-t", target, "#{pane_in_mode}").stdout.strip() == "1":
            core.tmux("send-keys", "-t", target, "-X", "cancel")
        if m["t"] == "in":
            write(m["d"])
        else:
            await core.type_line(name, str(m.get("d", "")))
    elif m.get("t") == "scroll":
        scroll_terminal(name, int(m.get("lines", 0)))
    elif m.get("t") == "prevprompt":
        await asyncio.get_running_loop().run_in_executor(None, prev_prompt, name)  # short sleeps; keep the loop free


async def terminal(request):
    name = request.match_info["name"]
    if not core.session_exists(name):
        raise web.HTTPNotFound()
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    if request.query.get("mode") == "chat":
        return await terminal_chat(ws, request, name, request.query.get("log", ""), request.query.get("from", ""))
    if request.query.get("mode") == "text":
        return await terminal_text(ws, name)
    if core.WINDOWS:
        return await terminal_windows(ws, name)

    pid, fd = pty.fork()
    if pid == 0:  # child: become a tmux client on this session
        os.environ["TERM"] = "xterm-256color"
        os.execvp("tmux", ["tmux", "attach-session", "-t", f"={name}"])

    loop = asyncio.get_running_loop()

    def on_output():
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        if data:
            asyncio.ensure_future(ws.send_bytes(data))
        else:  # client exited (session killed)
            loop.remove_reader(fd)
            asyncio.ensure_future(ws.close())

    loop.add_reader(fd, on_output)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") == "resize":
                set_size(fd, int(m["cols"]), int(m["rows"]))
            else:
                await terminal_input(name, m, lambda d: os.write(fd, d.encode()))
    finally:
        loop.remove_reader(fd)
        try:
            os.kill(pid, signal.SIGHUP)  # detaches the tmux client; the session keeps running
        except ProcessLookupError:
            pass
        os.close(fd)
        await loop.run_in_executor(None, os.waitpid, pid, 0)
    return ws


# Device attributes under ConPTY: a new ConPTY asks its terminal ESC[c and ignores input until answered, and answers
# coming back from xterm.js reach the pane as typed text ("1;2c0;276;0c", once per attach; a resize re-attaches).
# So the bridge answers ConPTY's own query itself and drops xterm.js's answers.
DA_REPLY = re.compile(r"\x1b\[[?>][0-9;]*c")


async def terminal_windows(ws, name: str):
    """Windows: the tmux client runs in a ConPTY (pywinpty). Resizing a ConPTY under MSYS2's tmux can hang the tmux
    server, so a resize starts a new client at the new size instead (the old one detaches; the screen redraws)."""
    loop = asyncio.get_running_loop()
    client = None

    def attach(rows: int, cols: int):
        """Start a client at this size and make it the current one; returns the one it replaces."""
        nonlocal client
        old = client
        p = client = PtyProcess.spawn([core.TMUX, "attach-session", "-t", f"={name}"], dimensions=(rows, cols),
                                      env=dict(os.environ, TERM="xterm-256color"))

        def pump():  # ConPTY reads block: a thread per client. Only the current one reaches the phone (a replaced
            asked = True  # one's goodbye, "[detached]" and a terminal reset, would land on the new screen)
            while True:
                try:
                    data = p.read(65536)
                    if asked and "\x1b[c" in data:  # ConPTY's start-up query: answer as a VT100 (what xterm.js says)
                        asked = False
                        data = data.replace("\x1b[c", "", 1)
                        p.write("\x1b[?1;2c")
                except Exception:  # EOF, or closed by detach
                    break
                if data and client is p:
                    loop.call_soon_threadsafe(lambda d=data: asyncio.ensure_future(ws.send_bytes(d.encode())))
            if client is p:  # client exited on its own (session killed), not replaced by a resize
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(ws.close()))
        threading.Thread(target=pump, daemon=True).start()
        return old

    def write(data: str) -> None:
        try:
            client.write(data)
        except EOFError:  # this client just exited (session ended, or replaced by a resize); its pump closes the socket
            pass

    def detach(p) -> None:
        try:
            p.terminate(force=True)  # the session keeps running
        except Exception:
            pass

    size = None

    def first_attach():  # the page sends its size on open; attach at that size (or a default if none comes)
        nonlocal size
        if client is None and not ws.closed:
            size = (40, 120)
            attach(*size)
    loop.call_later(2, first_attach)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") == "resize":
                new = (max(2, int(m["rows"])), max(2, int(m["cols"])))
                if new != size:
                    size = new
                    old = attach(*size)
                    if old:  # not at once: it may still hold keys it took (a Send right before the keyboard closes)
                        loop.call_later(1.5, lambda o=old: loop.run_in_executor(None, detach, o))
            elif client or m.get("t") != "in":  # only raw keys need the client
                if m.get("t") == "in":
                    m["d"] = DA_REPLY.sub("", m.get("d", ""))
                    if not m["d"]:
                        continue
                await terminal_input(name, m, write)
    finally:
        old, client = client, None
        if old:
            await loop.run_in_executor(None, detach, old)
    return ws


# ---- text mode (experimental): no tmux client; the page shows the pane's text, polled ----

PANE_META = ("#{cursor_x},#{cursor_y},#{cursor_flag},#{pane_in_mode},#{scroll_position},#{history_size},"
             "#{pane_width},#{pane_height},#{alternate_on},#{mouse_any_flag}")


def capture(name: str, offset: int = 0, height: int = 0, pre: list[str] = ()) -> tuple[list[str], list[int]] | None:
    """The pane's rows (with colour escapes) and PANE_META as numbers, in one tmux call; None if the session is gone.
    offset: rows scrolled back in copy mode (capture-pane shows the live screen, not copy mode's view).
    pre: tmux commands (each ending in ";") run first in the same call, e.g. a scroll (a tmux call costs ~50 ms on
    Windows)."""
    t = f"={name}:"
    span = ["-S", str(-offset), "-E", str(height - 1 - offset)] if offset else []
    r = core.tmux(*pre, "capture-pane", "-p", "-e", "-N", *span, "-t", t, ";", "display-message", "-p", "-t", t, PANE_META)
    if r.returncode:
        return None
    rows = r.stdout.split("\n")[:-1]
    return rows[:-1], [int(v or 0) for v in rows[-1].split(",")]


def text_frame(prev: list[str] | None, rows: list[str], meta: list[int]) -> str:
    """Escapes that turn the page's terminal from prev (its rows; None = start over) into rows: only changed rows are
    rewritten, each erased first and drawn in place (no wrap), so nothing flickers. Copy mode gets tmux's [pos/size]."""
    cx, cy, cursor, in_mode, pos, size, width = meta[:7]
    if in_mode and rows:
        tag = f"[{pos}/{size}]"
        rows = [*rows]
        rows[0] += f"\x1b[0m\x1b[1;{max(1, width - len(tag) + 1)}H\x1b[30;43m{tag}"
    out = [] if prev is not None else ["\x1b[?1049h\x1b[?7l\x1b[0m\x1b[H\x1b[2J"]
    old = prev or []
    out += [f"\x1b[{i + 1};1H\x1b[0m\x1b[2K{row}" for i, row in enumerate(rows) if i >= len(old) or old[i] != row]
    out += [f"\x1b[{i + 1};1H\x1b[0m\x1b[2K" for i in range(len(rows), len(old))]
    show = cursor and not in_mode
    out.append(f"\x1b[0m\x1b[{cy + 1};{cx + 1}H\x1b[?25{'h' if show else 'l'}")
    return "".join(out)


def scroll_commands(name: str, lines: int, meta: list[int]) -> tuple[list[str], int] | None:
    """Text mode: the tmux commands that scroll the pane's history by lines (positive = toward newer), to run in the
    capture's call, and the copy-mode position they leave. None for full-screen apps (scroll_terminal handles them)."""
    in_mode, pos, size, alt = meta[3], meta[4], meta[5], meta[8]
    if alt and not in_mode:
        return None
    t = f"={name}:"
    pos = pos if in_mode else 0
    if lines < 0:
        n = min(-lines, size - pos)
        return (["copy-mode", "-t", t, ";", "send-keys", "-t", t, "-N", str(n), "-X", "scroll-up", ";"], pos + n) if n \
            else ([], pos)
    if not in_mode:
        return [], 0
    if lines >= pos:  # back at the live screen
        return ["send-keys", "-t", t, "-X", "cancel", ";"], 0
    return ["send-keys", "-t", t, "-N", str(lines), "-X", "scroll-down", ";"], pos - lines


def resize_keeping_place(name: str, cols: int, rows: int, meta: list[int] | None) -> None:
    """resize-window. tmux's copy mode jumps to the newest line on a resize, so scroll back to where it was."""
    t = f"={name}:"
    now = core.tmux("resize-window", "-t", t, "-x", str(cols), "-y", str(rows), ";",
                    "display-message", "-p", "-t", t, "#{pane_in_mode} #{scroll_position}").stdout.split()
    if meta and meta[3] and now[:1] == ["1"] and (gap := meta[4] - int(now[1] or 0)) > 0:
        core.tmux("send-keys", "-t", t, "-N", str(gap), "-X", "scroll-up")


# Arrow/Home/End keys go by name: tmux then sends them in the form the program asked for (normal or application cursor
# keys), which the page can't know without the program's own output.
NAMED_KEYS = {f"\x1b{p}{c}": k for p in "[O" for c, k in zip("ABCDHF", ("Up", "Down", "Right", "Left", "Home", "End"))}
KEY_SPLIT = re.compile(r"(\x1b[\[O][A-DHF])")


def type_keys(name: str, data: str) -> None:
    """Text mode: keys from the page, typed with send-keys in one tmux call (there is no client to write them to)."""
    t = f"={name}:"
    esc = lambda s: s[:-1] + "\\;" if s.endswith(";") else s  # tmux takes a trailing ";" as a command separator
    if len(data) > 1 and re.search(r"[\r\n]", data) and "\x1b" not in data:
        # A paste (typing never sends a line break with more): tmux brackets it if the program asked for that, so an
        # agent takes it as one paste instead of submitting each line.
        for i in range(0, len(data), 8000):  # Windows command lines stop at 32K characters
            core.tmux("set-buffer", "-b", "deck-paste", *(["-a"] if i else []), "--", esc(data[i:i + 8000]))
        core.tmux("copy-mode", "-q", "-t", t, ";", "paste-buffer", "-p", "-d", "-b", "deck-paste", "-t", t)
        return
    args = ["copy-mode", "-q", "-t", t]  # leave copy mode first (no-op outside it)
    for part in filter(None, KEY_SPLIT.split(data)):
        args.append(";")
        if part in NAMED_KEYS:
            args += ["send-keys", "-t", t, NAMED_KEYS[part]]
        else:
            args += ["send-keys", "-t", t, "-l", "--", esc(part)]
    core.tmux(*args)


async def terminal_text(ws, name: str):
    """Text mode: polls the pane's text (capture-pane) and redraws changed rows; keys go in with send-keys and the page's
    size with resize-window. No client is attached, so nothing breaks on resize or reconnect (Windows ConPTY). Costs
    ~0.1 s of latency (0.4 s when idle) and the program's own terminal modes (mouse, cursor shape)."""
    loop = asyncio.get_running_loop()
    kick = asyncio.Event()
    redraw = sized = False
    want, have = None, None  # this page's size (cols, rows); the pane's, as last captured
    meta, owed = None, 0  # PANE_META as last captured; swipe lines not yet scrolled

    async def poll():
        nonlocal redraw, have, meta, owed
        shown, offset, height, changed = None, 0, 0, 0.0
        while not ws.closed:
            # Swipes arrive every frame; those that came in during the last capture go as one scroll, in the same
            # tmux call as the next capture (no queue of calls falling behind the finger).
            pre = []
            if owed and meta:
                lines, owed = max(-500, min(500, owed)), 0
                step = scroll_commands(name, lines, meta)
                if step is None:
                    await loop.run_in_executor(None, scroll_terminal, name, lines)
                else:
                    pre, offset = step
            snap = await loop.run_in_executor(None, capture, name, offset, height, pre)
            if snap is None:  # session ended
                await ws.close()
                return
            rows, meta = snap
            have = (meta[6], meta[7])
            pos, height = (meta[4] if meta[3] else 0), meta[7]
            if pos != offset:  # copy mode moved: capture its view right away
                offset = pos
                continue
            if redraw:  # the page reset its screen (resize)
                shown, redraw = None, False
            if shown is None or shown != (rows, meta):
                await ws.send_bytes(text_frame(shown and shown[0], rows, meta).encode())
                shown, changed = (rows, meta), loop.time()
            try:
                await asyncio.wait_for(kick.wait(), 0.1 if loop.time() - changed < 3 else 0.4)
            except asyncio.TimeoutError:
                pass
            kick.clear()

    poller = asyncio.ensure_future(poll())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") != "resize" and want and have and have != want:
                # Another screen showing this session resized it: the one being used gets its size back.
                await loop.run_in_executor(None, resize_keeping_place, name, *want, meta)
                have, redraw = want, True
            if m.get("t") == "resize":
                cols, rows = max(2, int(m["cols"])), max(2, int(m["rows"]))
                want = (cols, rows)
                await loop.run_in_executor(None, resize_keeping_place, name, cols, rows, meta)
                sized = redraw = True
            elif m.get("t") == "scroll":
                owed += int(m.get("lines", 0))
            elif m.get("t") == "in":
                await loop.run_in_executor(None, type_keys, name, m.get("d", ""))
            else:
                await terminal_input(name, m, lambda d: None)
            kick.set()
    finally:
        poller.cancel()
        if sized:  # resize-window pinned the size (window-size manual): live clients size it again
            await loop.run_in_executor(None, core.tmux, "set-option", "-w", "-u", "-t", f"={name}:", "window-size")
    return ws


def chat_resume_at(path, log: str, seen: str) -> int:
    """Where a page that has drawn `seen` events of log `log` (its first event's id) carries on: the byte after them, or 0
    (the whole log) when the log is another one now (a restart) or shorter."""
    if not log or not seen.isdigit():
        return 0
    try:
        with open(path, "rb") as f:
            first = f.readline()
            if not first.endswith(b"\n") or str(json.loads(first).get("log")) != log:
                return 0
            pos, n = len(first), 1
            while n < int(seen):
                line = f.readline()
                if not line.endswith(b"\n"):
                    return 0
                pos, n = pos + len(line), n + (1 if line.strip() else 0)
            return pos
    except (OSError, ValueError, AttributeError):
        return 0


# Files an agent's replies carry (chat_bridge.py: <session>.media/): images, audio and video show in the chat, the rest
# download. They come from the agent, so none may act as a page of Deck's: a sandbox CSP and no type sniffing, and only
# raster images, audio and video inline (an SVG or HTML file could hold scripts).
INLINE_MEDIA = re.compile(r"image/(png|jpeg|gif|webp|avif|bmp)|(audio|video)/[\w.+-]+")


def chat_media_file(name: str, file: str):
    """Session `name`'s chat media file `file`, or None (gone, or a name that would leave its folder)."""
    if not name or name.startswith(".") or "/" in name or "\\" in name or not re.fullmatch(r"\w[\w-]{0,119}(\.[A-Za-z0-9]{1,8})?", file):
        return None
    f = core.CHAT_DIR / f"{name}.media" / file
    return f if f.is_file() else None


def chat_media_headers(file: str) -> dict:
    kind = mimetypes.guess_type(file)[0] or "application/octet-stream"
    inline = bool(INLINE_MEDIA.fullmatch(kind))
    return {"Content-Type": kind if inline else "application/octet-stream", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "private, max-age=86400",
            "Content-Disposition": "inline" if inline else f'attachment; filename="{file}"'}


async def chat_media(request):
    f = chat_media_file(request.match_info["name"], request.match_info["file"])
    if f is None:
        raise web.HTTPNotFound()
    return web.FileResponse(f, headers=chat_media_headers(f.name))


async def chat_upload(request):  # a file sent from the chat's + button: raw bytes, ?name=; the message names its path
    name = request.match_info["name"]
    if not core.session_exists(name) or not core.is_chat(name):
        raise web.HTTPNotFound(text="no such chat")
    data = await request.read()
    if not data:
        return bad_request("empty file")
    base = re.sub(r"[^\w .,()+-]", "_", request.query.get("name", ""))[:150].strip(" .") or "file"

    def save():
        d = core.CHAT_DIR / f"{name}.uploads"
        d.mkdir(parents=True, exist_ok=True)
        stem, dot, ext = base.rpartition(".") if "." in base else (base, "", "")
        f, n = d / base, 1
        while f.exists():  # never overwrite an earlier upload of the same name
            n += 1
            f = d / (f"{stem} ({n}).{ext}" if dot else f"{base} ({n})")
        f.write_bytes(data)
        return {"path": str(f), "name": f.name, "size": len(data), "host": core.HOST_NAME}
    return web.json_response(await asyncio.to_thread(save))


async def terminal_chat(ws, request, name: str, log: str = "", seen: str = ""):
    """Chat sessions (deck/chat_bridge.py): send the bridge's event log, then follow it; the page's messages go to the
    bridge's command queue. A restart rewrites the log, so a shorter file means start over. A page reconnecting to the
    same log (its id, and how many events it drew) gets only the rest."""
    path = core.CHAT_DIR / f"{name}.jsonl"

    async def follow():
        pos, buf = chat_resume_at(path, log, seen), b""
        while not ws.closed:
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size < pos:
                pos, buf = 0, b""
            if size > pos:
                with open(path, "rb") as f:
                    f.seek(pos)
                    data = f.read(size - pos)
                pos += len(data)
                *lines, buf = (buf + data).split(b"\n")
                if lines:
                    await ws.send_str("[" + ",".join(x.decode("utf-8", "replace") for x in lines if x.strip()) + "]")
            await asyncio.sleep(0.1)

    task = asyncio.get_running_loop().create_task(follow())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") == "say":
                if not str(m.get("d", "")).strip():
                    continue  # an empty send (an Enter on an empty box): nothing for the agent
                core.chat_command(name, {"t": "say", "text": str(m.get("d", "")), "from": sender(request, m.get("via"))})
            elif m.get("t") == "in" and m.get("d") in ("\x1b", "\x03"):  # Esc / ^C keys: stop the agent
                core.chat_command(name, {"t": "abort"})
            elif m.get("t") in ("abort", "answer", "sendnow", "unhold"):
                core.chat_command(name, {k: m[k] for k in ("t", "id", "value") if k in m})
            elif m.get("t") == "menu":  # the tool row's small menus (Resume, Model, Effort): choices come back as an event
                core.chat_command(name, {"t": "menu", "text": str(m.get("text", "")), "rid": str(m.get("rid", ""))})
    finally:
        task.cancel()
    return ws


async def index(_):
    # Version the asset URLs so a phone holding stale cached copies fetches the new ones.
    v = assets_version()
    host = html_escape(core.HOST_NAME)  # the page names its sessions host/name (app.js fullName)
    html = (STATIC / "index.html").read_text().replace(
        "</head>", f'<meta name="deck-assets" content="{v}">\n<meta name="deck-host" content="{host}">\n</head>', 1)
    for name in ("style.css", "app.js", "screen.js"):
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={v}"')
    return web.Response(text=html, content_type="text/html")


def make_app() -> web.Application:
    app = web.Application(middlewares=[origin_guard, auth_guard, site_forward, revalidate], client_max_size=32 * 1024 * 1024)  # notes bundles
    app["origins"] = allowed_origins()
    app.add_routes([
        web.get("/", index),
        web.get("/api/buttons", get_buttons),
        web.post("/api/buttons", put_button),
        web.delete("/api/buttons/{id}", del_button),
        web.post("/api/run/{id}", run_button),
        web.post("/api/run", run_key),
        web.get("/api/sessions", get_sessions),
        web.get("/api/sessions/all", get_all_sessions),
        web.get("/api/site", site_info),
        web.get("/api/site/role", site_role),
        web.get("/api/site/journal", site_journal),
        web.post("/api/site/journal", site_journal),
        web.get("/api/site/snapshot", site_snapshot),
        web.post("/api/site/replay", site_replay),
        web.post("/api/site/offer", site_offer),
        web.get("/api/offline/{host}/{session}/text", offline_chat_text),
        web.post("/api/offline/{host}/{session}/move-here", move_here),
        web.route("*", "/peer/{host}/{tail:.*}", peer_proxy),
        web.get("/vnc/{tail:.*}", vnc_bridge),
        web.delete("/api/sessions/{name}", del_session),
        web.get("/api/session-trash", get_session_trash),
        web.post("/api/session-trash/{id}/restore", restore_session),
        web.delete("/api/session-trash/{id}", forget_session),
        web.post("/api/sessions/{name}/send", send_session),
        web.post("/api/sessions/{name}/restart", restart_session),
        web.post("/api/sessions/{name}/move", move_session),
        web.post("/api/sessions/{name}/rename", rename_session),
        web.put("/api/sessions/{name}/project", project_session),
        web.post("/api/move-in", move_in),
        web.get("/api/sessions/{name}/text", session_text),
        web.get("/api/sessions/{name}/media/{file}", chat_media),
        web.post("/api/sessions/{name}/upload", chat_upload),
        web.get("/api/sessions/{name}/describe", session_describe),
        web.get("/api/agent/sessions", agent_sessions),
        web.get("/api/agent/sessions/{name}", agent_session_read),
        web.post("/api/agent/sessions/{name}/send", agent_session_send),
        web.post("/api/sessions/{name}/save", save_session),
        web.post("/api/sessions/{name}/load", load_session),
        web.get("/api/shelf", get_shelf),
        web.get("/api/shelf/agent", agent_shelf),
        web.get("/api/shelf/{id}", get_shelf_item),
        web.post("/api/shelf", put_shelf),
        web.post("/api/shelf/pin", pin_shelf),
        web.post("/api/shelf/link", link_shelf),
        web.get("/api/shelf/{id}/mtime", shelf_mtime),
        web.delete("/api/shelf/{id}", del_shelf),
        web.put("/api/shelf/{id}/file", put_shelf_file),
        web.get("/api/shelf/{id}/file", get_shelf_file),
        web.put("/api/shelf/{id}/text", edit_shelf_text),
        web.post("/api/shelf/{id}/restore", restore_shelf),
        web.post("/api/shelf/{id}/rename", rename_shelf),
        web.post("/api/shelf/{id}/main", main_shelf),
        web.post("/api/shelf/{id}/autoname", autoname_shelf),
        web.post("/api/shelf-arrange", arrange_shelf),
        web.get("/api/saves", get_saves),
        web.post("/api/saves", put_save),
        web.get("/api/saves/{id}", get_save),
        web.post("/api/saves/{id}/delete", delete_save),
        web.post("/api/clear", clear_sessions),
        web.post("/api/shell", shell_session),
        web.get("/api/folders", get_folders),
        web.get("/api/hosts", get_hosts),
        web.get("/api/links", get_links),
        web.get("/api/deck-context", get_deck_context),
        web.post("/api/link/start", link_start),
        web.post("/api/link/claim", link_claim),
        web.get("/api/link/wait", link_wait),
        web.get("/api/link/pending", link_pending),
        web.post("/api/link/decide", link_decide),
        web.route("*", "/api/link/join", link_join),
        web.post("/api/link/rekey", link_rekey),
        web.delete("/api/links/{name}", link_remove),
        web.post("/api/link/leave", link_leave),
        web.get("/api/settings", get_settings),
        web.get("/api/me", get_me),
        web.post("/api/app/link", app_link),
        web.post("/api/pair/start", pair_start),
        web.post("/api/pair/claim", pair_claim),
        web.get("/api/pair/pending", pair_pending),
        web.post("/api/pair/decide", pair_decide),
        web.get("/api/pair/qr.svg", pair_qr),
        web.get("/api/devices", get_devices),
        web.delete("/api/devices/{id}", del_device),
        web.get("/api/notes", get_notes),
        web.get("/api/notes/files", get_notes_files),
        web.get("/api/web/check", web_check),
        web.get("/api/notes/bundle", get_notes_bundle),
        web.post("/api/notes/carry", post_notes_carry),
        web.get("/sw.js", service_worker),
        web.get("/api/notes/file", get_notes_file),
        web.put("/api/notes/file", put_notes_file),
        web.route("*", "/notes.git/{path:.*}", notes_git),
        web.put("/api/settings", put_settings),
        web.get("/api/screen", get_screen),
        web.get("/api/screen/phone", phone_screen),
        web.post("/api/screen/phone", phone_screen),
        web.get("/api/screen/windows", phone_windows),
        web.post("/api/screen/windows", phone_windows),
        web.post("/api/build", build_key),
        web.post("/api/agent", new_agent),
        web.post("/api/update", update),
        web.post("/api/transcribe", transcribe),
        web.get("/ws/{name}", terminal),
        web.get("/static/app.js", app_script),  # before the static files: app.js is its parts joined
        web.static("/static", STATIC),
    ])

    async def start_alerts(app):  # phone alerts when an agent session is waiting (DECK_NTFY)
        app["alerts"] = asyncio.get_running_loop().create_task(alerts.watch(app))
        app["describe"] = asyncio.get_running_loop().create_task(describe.watch(app))  # tab descriptions

    async def stop_alerts(app):
        app["alerts"].cancel()
        app["describe"].cancel()
    async def start_peers(app):  # every host's sessions (get_all_sessions) and the /peer proxy
        import aiohttp
        app["peer_http"] = aiohttp.ClientSession()
        app["peerstate"] = {"wanted": 0.0, "seen": {}}
        app["peers"] = asyncio.get_running_loop().create_task(peers_loop(app))
        app["site"] = asyncio.get_running_loop().create_task(site.loop(app))
        app["links"] = asyncio.get_running_loop().create_task(links.loop(app))
        app["phonestate"] = {"seen": None}  # last keep-alive from a page streaming the phone screen
        app["phone"] = asyncio.get_running_loop().create_task(phone_idle_loop(app))
        app["vncstate"] = {"streams": 0, "idle": 0.0}  # open /vnc/ streams; when the last one closed
        app["vnc"] = asyncio.get_running_loop().create_task(vnc_idle_loop(app))

    async def stop_peers(app):
        app["peers"].cancel()
        app["site"].cancel()
        app["links"].cancel()
        app["phone"].cancel()
        app["vnc"].cancel()
        links.stop_all()
        await app["peer_http"].close()
    app.on_startup.append(start_alerts)
    app.on_cleanup.append(stop_alerts)
    app.on_startup.append(start_peers)
    app.on_cleanup.append(stop_peers)
    return app


def bind(addr) -> socket.socket:
    """A listening socket, retried for a while: after an update the old server may still be letting go of it."""
    for _ in range(60):
        try:
            return socket.create_server(addr)
        except OSError:
            time.sleep(0.25)
    return socket.create_server(addr)


async def restart_on_dead_listener(_app) -> None:
    """Windows' asyncio closes a listening socket for good when one accept fails (WinError 64: a client reset while it
    waited in the backlog), leaving a running server that answers nothing on that port. Restart instead."""
    def handler(loop, context):
        loop.default_exception_handler(context)
        if context.get("message") == "Accept failed on a socket":
            loop.set_exception_handler(None)  # once: the other port may fail too while stopping
            restart_windows()
    asyncio.get_running_loop().set_exception_handler(handler)


if __name__ == "__main__":
    # Open terminal sockets would hold a stop/restart for aiohttp's default 60s; phones reconnect on their own.
    # Deck's own port, and the tunnel's (loopback only: deck-iroh connects to it).
    core.start_tmux()
    socks = [bind((HOST, PORT)), bind(("127.0.0.1", TUNNEL_PORT))]
    app = make_app()
    if core.WINDOWS:
        app.on_startup.append(restart_on_dead_listener)
    web.run_app(app, sock=socks, shutdown_timeout=2)
