"""Linked PCs (deck/links.py): one PC makes a code and approves, another pastes it; leaving and removing."""
from aiohttp import web

from .. import auth, config, links
from .common import bad_request


async def get_links(_):
    return web.json_response({"me": config.HOST_NAME, "members": links.members(), "iroh": bool(links.iroh_id())})


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
    return web.json_response({"token": token, "host": config.HOST_NAME})


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


def routes() -> list:
    return [
        web.get("/api/links", get_links),
        web.post("/api/link/start", link_start),
        web.post("/api/link/claim", link_claim),
        web.get("/api/link/wait", link_wait),
        web.get("/api/link/pending", link_pending),
        web.post("/api/link/decide", link_decide),
        web.route("*", "/api/link/join", link_join),
        web.post("/api/link/rekey", link_rekey),
        web.delete("/api/links/{name}", link_remove),
        web.post("/api/link/leave", link_leave),
    ]
