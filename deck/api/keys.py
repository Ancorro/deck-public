"""Script keys (buttons) and what starts a session from the page: a key, a new agent, the key builder."""
from aiohttp import web

from .. import buttons, sessions, settings
from .common import bad_request


async def get_buttons(_):
    return web.json_response(buttons.load_buttons())


async def put_button(request):
    try:
        return web.json_response(buttons.upsert_button(await request.json()))
    except ValueError as e:
        return bad_request(e)


async def del_button(request):
    buttons.delete_button(request.match_info["id"])
    return web.json_response({"ok": True})


async def run_key(request):
    """Run a key sent whole: another host's pad launching one of its keys here (its "Run on" host)."""
    try:
        body = await request.json()
        b = {**buttons.clean_button(body), "host": "", **buttons.run_arg(body)}
        return web.json_response({"session": sessions.start_run(b)})
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def run_button(request):
    b = next((x for x in buttons.load_buttons() if x["id"] == request.match_info["id"]), None)
    if b is None:
        raise web.HTTPNotFound()
    # Optional one-off overrides from the ask sheet; the saved key is unchanged.
    body = await request.json() if request.can_read_body else {}
    b = {**b, **{k: str(body[k]).strip() for k in ("text", "cwd") if body.get(k)}}
    if "yolo" in body and b["type"] != "shell":
        b["yolo"] = bool(body["yolo"])
    b = {**b, **buttons.run_arg(body)}
    try:
        return web.json_response({"session": sessions.start_run(b)})
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def new_agent(request):
    try:
        body = await request.json()
        b = buttons.new_agent_button(str(body.get("agent", "claude")), str(body.get("text", "")).strip(),
                                  str(body.get("cwd", "")).strip(), bool(body.get("yolo", settings.settings()["yolo"])))
        return web.json_response({"session": sessions.start_run(b)})
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def build_key(request):
    try:
        body = await request.json()
        b = buttons.builder_button(str(body.get("request", "")), str(body.get("agent", "claude")))
        return web.json_response({"session": sessions.start_run(b)})
    except (ValueError, RuntimeError) as e:
        return bad_request(e)


async def get_folders(_):
    return web.json_response(settings.folders())


def routes() -> list:
    return [
        web.get("/api/buttons", get_buttons),
        web.post("/api/buttons", put_button),
        web.delete("/api/buttons/{id}", del_button),
        web.post("/api/run/{id}", run_button),
        web.post("/api/run", run_key),
        web.post("/api/agent", new_agent),
        web.post("/api/build", build_key),
        web.get("/api/folders", get_folders),
    ]
