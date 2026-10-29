"""The machine notes (deck/notes.py): their files, sync status, the offline bundle, and the repo over git smart HTTP."""
import asyncio
import base64
import os
import time

from aiohttp import web

from .. import config, notes
from .common import bad_request


async def notes_git(request):
    """Serve the notes repo to peer Decks (git smart HTTP via `git http-backend`); tailnet-only like the rest."""
    env = {**os.environ, "GIT_PROJECT_ROOT": str(config.NOTES), "GIT_HTTP_EXPORT_ALL": "1",
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
    return web.json_response({**r, "bundle": base64.b64encode(r["bundle"]).decode(), "host": config.HOST_NAME})


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
        new = await asyncio.to_thread(notes.write, str(body.get("path", "")), str(body.get("text", "")))
    except (ValueError, RuntimeError) as e:
        return bad_request(e)
    _notes_status["at"] = 0.0
    return web.json_response({"head": new})


# ---- pairing ----


def routes() -> list:
    return [
        web.get("/api/notes", get_notes),
        web.get("/api/notes/files", get_notes_files),
        web.get("/api/notes/bundle", get_notes_bundle),
        web.post("/api/notes/carry", post_notes_carry),
        web.get("/api/notes/file", get_notes_file),
        web.put("/api/notes/file", put_notes_file),
        web.route("*", "/notes.git/{path:.*}", notes_git),
    ]
