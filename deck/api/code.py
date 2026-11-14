"""The Code view (deck/code.py): a folder's entries, and its text files read and saved. Text goes out as JSON only (a
file served raw from Deck's origin would run as the page if it were HTML or SVG). Saves are PUT, so origin_guard checks
who sent them; another host's files come through /peer/<host>/api/code/... like every other call."""
import asyncio

from aiohttp import web

from .. import code
from .common import bad_request


def _err(e: Exception):
    if isinstance(e, FileNotFoundError):
        return web.json_response({"error": str(e)}, status=404)
    if isinstance(e, (PermissionError, IsADirectoryError, NotADirectoryError)):
        return web.json_response({"error": str(e) or "not allowed"}, status=403)
    return bad_request(e)


async def code_list(request):
    """GET /api/code/list?root=FOLDER&path=REL: one folder's entries (folders first)."""
    q = request.query
    try:
        return web.json_response(await asyncio.to_thread(code.listing, q.get("root", ""), q.get("path", "")))
    except (OSError, ValueError) as e:
        return _err(e)


async def code_file(request):
    """GET /api/code/file?root=FOLDER&path=REL: a text file as {text, version, eol, ...}."""
    q = request.query
    try:
        return web.json_response(await asyncio.to_thread(code.read, q.get("root", ""), q.get("path", "")))
    except (OSError, ValueError) as e:
        return _err(e)


async def code_stat(request):
    """GET /api/code/stat?root=FOLDER&path=REL: {mtime, size}, for the open tab's watch."""
    q = request.query
    try:
        return web.json_response(await asyncio.to_thread(code.stat_of, q.get("root", ""), q.get("path", "")))
    except (OSError, ValueError) as e:
        return _err(e)


async def code_save(request):
    """PUT /api/code/file {root, path, text, base, eol, bom, create}: saved, or 409 with what is there now when the
    file changed since `base` (an agent wrote it)."""
    try:
        b = await request.json()
    except ValueError as e:
        return bad_request(e)
    base = b.get("base")
    try:
        out = await asyncio.to_thread(code.write, str(b.get("root", "")), str(b.get("path", "")), str(b.get("text", "")),
                                      None if base is None else str(base), str(b.get("eol", "")), bool(b.get("bom")),
                                      bool(b.get("create")))
    except code.Conflict as c:
        return web.json_response({"error": str(c), "conflict": True, "now": c.now}, status=409)
    except FileExistsError as e:
        return web.json_response({"error": str(e)}, status=409)
    except (OSError, ValueError) as e:
        return _err(e)
    return web.json_response(out)


def routes() -> list:
    return [
        web.get("/api/code/list", code_list),
        web.get("/api/code/file", code_file),
        web.get("/api/code/stat", code_stat),
        web.put("/api/code/file", code_save),
    ]
