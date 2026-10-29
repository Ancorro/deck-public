"""The sessions on this host: the list, what one said, typing into one, closing it (into the trash), restarting
its agent, renaming it, moving it to another host, a new shell."""
import asyncio

from aiohttp import web

from .. import auth, describe, session_move, session_trash, sessions
from . import access
from .common import bad_request


async def get_sessions(_):
    # Off the event loop: listing takes ~0.1 s of tmux calls on Windows, which would stall every live terminal.
    return web.json_response(await asyncio.get_running_loop().run_in_executor(None, sessions.list_sessions))


async def del_session(request):
    name = request.match_info["name"]
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, session_trash.keep, name)  # an agent chat waits in the trash, restorable
    await loop.run_in_executor(None, sessions.kill_session, name)
    return web.json_response({"ok": True})


# ---- closed sessions' trash (deck/session_trash.py) ----
async def get_session_trash(_):
    return web.json_response(session_trash.load())


async def restore_session(request):
    try:
        name = await asyncio.get_running_loop().run_in_executor(None, session_trash.restore, request.match_info["id"])
    except StopIteration:
        raise web.HTTPNotFound() from None
    except (ValueError, RuntimeError) as e:
        return bad_request(e)
    return web.json_response({"session": name})


async def forget_session(request):
    session_trash.delete(request.match_info["id"])
    return web.json_response({"ok": True})


async def send_session(request):
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    if sessions.is_chat(name):  # Agents feed reply to a chat session: a message for its bridge
        if body.get("text"):
            sessions.chat_command(name, {"t": "say", "text": str(body["text"]), "from": access.sender(request, "Agents feed")})
        elif body.get("key") in ("esc", "ctrlc"):
            sessions.chat_command(name, {"t": "abort"})
        return web.json_response({"ok": True})
    try:
        nav = int(body["nav"]) if "nav" in body else None
        sessions.send_input(name, str(body.get("text", "")), str(body.get("key", "")), bool(body.get("enter")), nav)
    except (ValueError, TypeError) as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def session_text(request):
    """The session's output as plain text for copying: tmux history plus the screen (full-screen agents: the screen)."""
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    return web.json_response({"text": await asyncio.to_thread(sessions.session_output, name)})


async def session_describe(request):
    """The tab's description (deck/describe.py): {text, at}; empty until the agent's first turn after it starts."""
    d = describe.get(request.match_info["name"])
    return web.json_response({"text": d.get("text", ""), "at": d.get("at", 0)})


# ---- agents see and message the other sessions on this host, while Settings allows it ----


async def restart_session(request):
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    try:
        sessions.restart_agent(name)
    except (ValueError, RuntimeError) as e:
        return bad_request(e)
    return web.json_response({"ok": True})


async def shell_session(request):
    body = await request.json() if request.can_read_body else {}
    try:
        return web.json_response({"session": sessions.ensure_shell(new=bool(body.get("new")))})
    except RuntimeError as e:
        return bad_request(e)


async def clear_sessions(_):
    return web.json_response({"cleared": sessions.clear_finished()})


# ---- Move host (deck/session_move.py): a chat carries on on another Deck host ----
async def move_session(request):
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    try:
        return web.json_response(await session_move.send(name, str(body.get("host", ""))))
    except ValueError as e:
        return bad_request(e)


async def move_in(request):
    """From another host only (its host key)."""
    if not auth.is_host(request.headers.get("Authorization")):
        raise web.HTTPForbidden()
    try:
        return web.json_response(await asyncio.to_thread(session_move.receive, await request.read()))
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def project_session(request):
    """{folder} its agent works in, or {project} (Harness → Project); neither: reset."""
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    try:
        project = await asyncio.to_thread(sessions.set_session_project, name, str(body.get("folder") or ""),
                                          str(body.get("project") or ""))
    except ValueError as e:
        return bad_request(e)
    return web.json_response({"project": project})


async def rename_session(request):
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    body = await request.json()
    await asyncio.to_thread(sessions.rename_session, name, str(body.get("title", "")))
    return web.json_response({"ok": True})


def routes() -> list:
    return [
        web.get("/api/sessions", get_sessions),
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
        web.get("/api/sessions/{name}/describe", session_describe),
        web.post("/api/clear", clear_sessions),
        web.post("/api/shell", shell_session),
    ]
