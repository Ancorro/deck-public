"""Pairing (deck/auth.py): who this browser is, pairing codes and their approval, the paired devices."""
from aiohttp import web

from .. import auth, config, settings
from . import access
from .common import bad_request


async def get_me(request):
    dev = auth.device_for(request.cookies.get(auth.COOKIE), touch=False)
    local = access.is_local(request)
    return web.json_response({"enforced": (auth.enforced() or access.via_tunnel(request)) and not local, "local": local,
                              "device": dev and {k: dev[k] for k in ("id", "name")},
                              "pending": bool(dev and dev.get("pending"))})


def _approver(request) -> str:
    # Before the first device is approved (open mode) an unpaired browser can make codes; it approves as "open".
    # This host's own browser approves as "host", like `deck-pair`.
    return request.get("device", {}).get("id") or ("host" if access.is_local(request) else "open")


async def pair_start(request):
    # iroh: this host's key when deck-iroh runs, so the QR can pair the app over iroh (no Tailscale needed).
    try:
        iroh = config.IROH_ID.read_text().strip()
    except OSError:
        iroh = None
    # url: this host's address for other devices (inventory deck_url); a page opened at 127.0.0.1 can't use its own.
    url = next((h["url"] for h in settings.hosts()["hosts"] if h["current"]), None)
    return web.json_response(auth.new_code(_approver(request)) | {"iroh": iroh, "name": config.HOST_NAME, "url": url})


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


async def get_devices(request):
    me = request.get("device", {}).get("id")
    return web.json_response([d | {"me": d["id"] == me} for d in auth.devices()])


async def del_device(request):
    auth.remove(request.match_info["id"])
    return web.json_response({"ok": True})


def routes() -> list:
    return [
        web.get("/api/me", get_me),
        web.post("/api/pair/start", pair_start),
        web.post("/api/pair/claim", pair_claim),
        web.get("/api/pair/pending", pair_pending),
        web.post("/api/pair/decide", pair_decide),
        web.get("/api/pair/qr.svg", pair_qr),
        web.get("/api/devices", get_devices),
        web.delete("/api/devices/{id}", del_device),
    ]
