"""The shelf (deck/shelf.py): its items, documents and their files, pinned pages, folders."""
import asyncio

from aiohttp import web

from .. import auth, config, sessions, settings, shelf
from .common import bad_request

DOC_PREVIEW = 3000  # characters of a document's text in the shelf's list (a PDF's runs to 100k+); the rest: GET /api/shelf/{id}


def new_on_top() -> bool:
    """Settings → Shelf: new items go to the top (else the bottom)."""
    return settings.settings().get("shelfnew") == "top"


def session_meta(body: dict) -> dict:
    """The session an item was saved from, as the body names it (its host, agent, folder...), or {}."""
    name = str(body.get("session", ""))
    return sessions.session_meta(name) if name else {}


async def get_shelf(_):
    """Every item, a long document's text cut to a preview (`cut`: its full length; GET /api/shelf/{id} has it all)."""
    items = shelf.load()
    for it in items:
        if it.get("kind") == "doc" and len(it.get("text") or "") > DOC_PREVIEW:
            it["text"], it["cut"] = it["text"][:DOC_PREVIEW], len(it["text"])
    return web.json_response(items)


async def get_shelf_item(request):
    """One item whole (a document's full text); a linked file's as it is on disk now."""
    items = shelf.load()
    it = next((x for x in items if x.get("id") == request.match_info["id"]), None)
    if not it:
        raise web.HTTPNotFound()
    if (it.get("file") or {}).get("linked") and it["file"].get("host") == config.HOST_NAME \
            and await asyncio.to_thread(shelf.refresh_linked, it):
        await asyncio.to_thread(shelf.save_item, it)
    return web.json_response(it)


async def link_shelf(request):
    """A file on this host as a document, not a copy: {path, title, session}."""
    try:
        body = await request.json()
        # From another host (its site_forward, host key): the file as that host read it ("info"); the path is there
        info = shelf.peer_link_info(body.get("info")) if auth.is_host(request.headers.get("Authorization"))             and "info" in body else None
        return web.json_response(await asyncio.to_thread(shelf.link_file, body, session_meta(body), new_on_top(), info))
    except ValueError as e:
        return bad_request(e)


async def shelf_mtime(request):
    """A linked file's change time, which the reader polls to redraw it when it changes."""
    f = shelf.file_path(request.match_info["id"])
    if f is None:
        raise web.HTTPNotFound()
    st = f.stat()
    return web.json_response({"mtime": st.st_mtime, "size": st.st_size})


async def put_shelf(request):
    try:
        body = await request.json()
        return web.json_response(shelf.add(body, session_meta(body), top=new_on_top()))
    except ValueError as e:
        return bad_request(e)


async def pin_shelf(request):
    """An agent pins a web page: {url, title, note, session}; the same url updates its item."""
    try:
        body = await request.json()
        name = str(body.get("session", ""))
        if body.get("main") and body.get("folder") and sessions.session_exists(name):  # the session works in that project now
            sessions.set_session_project(name, folder=str(body["folder"]))
        return web.json_response(shelf.pin(body, session_meta(body), top=new_on_top()))
    except ValueError as e:
        return bad_request(e)


async def put_shelf_file(request):
    """A document's file: the raw bytes, ?name=&type=."""
    try:
        data = await request.read()
        return web.json_response(await asyncio.to_thread(shelf.put_file, request.match_info["id"],
                                                         request.query.get("name", ""), request.query.get("type", ""), data))
    except ValueError as e:
        return bad_request(e)


async def edit_shelf_text(request):
    """A text document saved from the reader: {text}."""
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


async def del_shelf(request):
    """?forever=1 skips the trash."""
    shelf.delete(request.match_info["id"], request.query.get("forever") == "1")
    return web.json_response({"ok": True})


async def restore_shelf(request):
    shelf.restore(request.match_info["id"])
    return web.json_response({"ok": True})


async def arrange_shelf(request):
    """A drag: {order: [{id, folder}], folders: [{id, title}]}."""
    body = await request.json()
    shelf.arrange(body.get("order") or [], body.get("folders") or [])
    return web.json_response({"ok": True})


async def agent_shelf(request):
    """Agents on this host read the shelf, while the shelf's checkbox allows it."""
    if not settings.settings().get("shelfagents"):
        return web.Response(status=403, text="The user hasn't let agents read the shelf (Deck: shelf > Agents can read).\n")
    q = request.query
    return web.Response(text=shelf.for_agent(q.get("id", ""), q.get("all") == "1"), content_type="text/markdown")


async def autoname_shelf(request):
    """A folder named from its contents (slow: asks Claude)."""
    try:
        return web.json_response({"title": await asyncio.to_thread(shelf.autoname, request.match_info["id"])})
    except ValueError as e:
        return bad_request(e)


async def main_shelf(request):
    """{project}: this link is that project's main page; "": not any more."""
    try:
        shelf.main(request.match_info["id"], str((await request.json()).get("project", "")))
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def rename_shelf(request):
    shelf.rename(request.match_info["id"], (await request.json()).get("title", ""))
    return web.json_response({"ok": True})


# ---- saved sessions (deck/saves.py) ----


def routes() -> list:
    return [
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
    ]
