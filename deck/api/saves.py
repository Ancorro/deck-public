"""Saved sessions (deck/saves.py): agents' handoff notes, kept on every host; Save writes one, Load hands one over."""
import asyncio

from aiohttp import web

from .. import saves, sessions
from .common import bad_request


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
        raise web.HTTPNotFound() from None
    asyncio.get_running_loop().create_task(saves.push(s))
    return web.json_response({"ok": True})


async def save_session(request):
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    if sessions.is_chat(name):  # saves type into the agent's terminal
        return bad_request(ValueError("Save isn't available in chat sessions yet"))
    try:
        return web.json_response({"id": await saves.start_save(name)})
    except ValueError as e:
        return bad_request(e)


async def load_session(request):
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    try:
        await saves.load(name, str(body.get("id", "")))
    except KeyError:
        raise web.HTTPNotFound() from None
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"ok": True})


def routes() -> list:
    return [
        web.get("/api/saves", get_saves),
        web.post("/api/saves", put_save),
        web.get("/api/saves/{id}", get_save),
        web.post("/api/saves/{id}/delete", delete_save),
        web.post("/api/sessions/{name}/save", save_session),
        web.post("/api/sessions/{name}/load", load_session),
    ]
