"""Who is asking: the middlewares that let a request in (a known origin, a paired device or another host, the
pairing screen for the rest), whether it comes from this host's own browser or over the iroh tunnel, and the name
its chat messages are stamped with."""
import asyncio
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from aiohttp import web

from .. import auth, config, settings, shelf, site
from . import pages
from .common import bad_request


def via_tunnel(request) -> bool:
    sock = request.transport and request.transport.get_extra_info("sockname")
    return bool(sock) and sock[1] == config.TUNNEL_PORT


def allowed_origins() -> set[str]:
    extra = os.environ.get("DECK_ORIGINS", "")
    return {f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}", *filter(None, extra.split(","))}


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
    return name if name and name.lower() != config.HOST_NAME.lower() else "this PC"


def sender(request, via=None) -> str:
    """Who sent a chat message, for its stamp: the paired device's name (or this PC), and how (phone app, phone browser,
    PC browser)."""
    dev = request.get("device", {}).get("name") or local_name(request)
    if not dev and auth.is_host(request.headers.get("Authorization")):  # through another host's /peer proxy: its sender
        dev = request.headers.get("X-Deck-Device", "")
    return ", ".join(x for x in (str(dev)[:40], str(via or "")[:20]) if x)


# A client-only PC (Settings → Host sessions on this PC, off) starts no sessions of its own and isn't reachable over
# its iroh tunnel; its page starts new sessions on a linked PC instead (01-core.js api).
STARTS = re.compile(r"/api/(agent|shell|build|run/[^/]+)")


@web.middleware


async def auth_guard(request, handler):
    """Only paired devices (cookie) and the user's other hosts (host key) get in, once pairing is enforced."""
    p = request.path
    if not settings.settings().get("hosting", True):
        if via_tunnel(request):
            return web.json_response({"error": "this PC doesn't host sessions"}, status=403)
        if request.method == "POST" and STARTS.fullmatch(p):
            return web.json_response({"error": "this PC doesn't host sessions: link one that does (Settings → PCs)"}, status=409)
    # the page's files are open (it shows the pairing screen); the phone app isn't: its build carries the host list
    # (app/build.sh's hosts.json), so only paired devices download it, or a browser holding an app link (/api/app/link)
    if p in OPEN or p.startswith("/static/") and p != "/static/deck.apk" or auth.is_host(request.headers.get("Authorization")) \
            or p == "/static/deck.apk" and pages.app_link_ok(request.query.get("t", "")):
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
            data = await request.read() or None
            if request.path == "/api/shelf/link":  # the file is on this host, not the writer's: read it here, send it
                try:
                    body = json.loads(data or b"{}")
                    body["info"] = await asyncio.to_thread(shelf.link_info, body)
                except ValueError as e:  # bad JSON (json.JSONDecodeError is one) or no such file
                    return bad_request(e)
                data = json.dumps(body).encode()
            try:
                async with request.app["peer_http"].request(request.method, f"{w['url'].rstrip('/')}{request.path_qs}",
                                                            data=data, headers=headers,
                                                            timeout=aiohttp.ClientTimeout(total=60, sock_connect=4)) as r:
                    return web.Response(body=await r.read(), status=r.status,
                                        content_type=r.headers.get("Content-Type", "application/json").split(";")[0])
            except (TimeoutError, aiohttp.ClientError, OSError):
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
        resp.headers["X-Deck-Assets"] = pages.assets_version()
    return resp
