"""One site on every node (deck/site.py): the writer serves it and journals every change; followers hold a copy.
Host key only, except the two the page reads (the site's state, an offline node's chat)."""
import asyncio

from aiohttp import web

from .. import config, site
from .common import bad_request, host_only


# ---- one site on every node (deck/site.py): the writer serves it and journals every change; followers hold a copy.
# Host key only, except the two the page reads (site, offline chat text) ----
async def site_role(request):
    host_only(request)
    return web.json_response(site.status())


async def site_info(request):
    """For the page and the phone: who serves the site now, this node's role, and the order."""
    w = site.writer()
    return web.json_response({**site.status(), "writer_url": (w or {}).get("url", ""), "me": config.HOST_NAME})


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


async def site_replay(request):
    """A lower node wrote while this writer was away: take it into the journal."""
    host_only(request)
    if site.role() != "writer":
        return web.json_response({"error": "not the writer"}, status=409)
    body = await request.json()
    return web.json_response({"replayed": await asyncio.to_thread(site.replay, body.get("entries", []))})


async def site_offer(request):
    """A follower's own chats (its sessions write them there): journaled here for every node."""
    host_only(request)
    if site.role() != "writer":
        return web.json_response({"error": "not the writer"}, status=409)
    body = await request.json()
    return web.json_response(await asyncio.to_thread(site.take_offer, body.get("entries", [])))


async def offline_chat_text(request):
    """A dead node's chat, read only, from this node's copy of the state."""
    try:
        return web.json_response(await asyncio.to_thread(site.chat_text, request.match_info["session"]))
    except ValueError as e:
        return bad_request(e)


async def move_here(request):
    """Carry a dead node's chat on here from the copy: the session restarts on its conversation (session_move) and the
    takeover is recorded in the state, so the node closes its own tab when it is back."""
    try:
        host, session = request.match_info["host"], request.match_info["session"]
        return web.json_response(await asyncio.to_thread(site.move_here, host, session))
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


def routes() -> list:
    return [
        web.get("/api/site", site_info),
        web.get("/api/site/role", site_role),
        web.get("/api/site/journal", site_journal),
        web.post("/api/site/journal", site_journal),
        web.get("/api/site/snapshot", site_snapshot),
        web.post("/api/site/replay", site_replay),
        web.post("/api/site/offer", site_offer),
        web.get("/api/offline/{host}/{session}/text", offline_chat_text),
        web.post("/api/offline/{host}/{session}/move-here", move_here),
    ]
