"""Chat sessions (deck/chat_bridge.py): the WebSocket that sends the bridge's event log and follows it, and the files
a chat carries (the agent's media, the user's uploads)."""
import asyncio
import json
import mimetypes
import re

from aiohttp import WSMsgType, web

from .. import config, sessions
from . import access
from .common import bad_request


async def terminal_chat(ws, request, name: str, log: str = "", seen: str = ""):
    """Chat sessions (deck/chat_bridge.py): send the bridge's event log, then follow it; the page's messages go to the
    bridge's command queue. A restart rewrites the log, so a shorter file means start over. A page reconnecting to the
    same log (its id, and how many events it drew) gets only the rest."""
    path = config.CHAT_DIR / f"{name}.jsonl"

    async def follow():
        pos, buf = chat_resume_at(path, log, seen), b""
        while not ws.closed:
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size < pos:
                pos, buf = 0, b""
            if size > pos:
                with open(path, "rb") as f:
                    f.seek(pos)
                    data = f.read(size - pos)
                pos += len(data)
                *lines, buf = (buf + data).split(b"\n")
                if lines:
                    await ws.send_str("[" + ",".join(x.decode("utf-8", "replace") for x in lines if x.strip()) + "]")
            await asyncio.sleep(0.1)

    task = asyncio.get_running_loop().create_task(follow())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") == "say":
                if not str(m.get("d", "")).strip():
                    continue  # an empty send (an Enter on an empty box): nothing for the agent
                sessions.chat_command(name, {"t": "say", "text": str(m.get("d", "")),
                                             "from": access.sender(request, m.get("via"))})
            elif m.get("t") == "in" and m.get("d") in ("\x1b", "\x03"):  # Esc / ^C keys: stop the agent
                sessions.chat_command(name, {"t": "abort"})
            elif m.get("t") in ("abort", "answer", "sendnow", "unhold"):
                sessions.chat_command(name, {k: m[k] for k in ("t", "id", "value") if k in m})
            elif m.get("t") == "menu":  # the tool row's small menus (Resume, Model, Effort): choices come back as an event
                sessions.chat_command(name, {"t": "menu", "text": str(m.get("text", "")), "rid": str(m.get("rid", ""))})
    finally:
        task.cancel()
    return ws


def chat_resume_at(path, log: str, seen: str) -> int:
    """Where a page that has drawn `seen` events of log `log` (its first event's id) carries on: the byte after them, or 0
    (the whole log) when the log is another one now (a restart) or shorter."""
    if not log or not seen.isdigit():
        return 0
    try:
        with open(path, "rb") as f:
            first = f.readline()
            if not first.endswith(b"\n") or str(json.loads(first).get("log")) != log:
                return 0
            pos, n = len(first), 1
            while n < int(seen):
                line = f.readline()
                if not line.endswith(b"\n"):
                    return 0
                pos, n = pos + len(line), n + (1 if line.strip() else 0)
            return pos
    except (OSError, ValueError, AttributeError):
        return 0


# Files an agent's replies carry (chat_bridge.py: <session>.media/): images, audio and video show in the chat, the rest
# download. They come from the agent, so none may act as a page of Deck's: a sandbox CSP and no type sniffing, and only
# raster images, audio and video inline (an SVG or HTML file could hold scripts).
INLINE_MEDIA = re.compile(r"image/(png|jpeg|gif|webp|avif|bmp)|(audio|video)/[\w.+-]+")


def chat_media_file(name: str, file: str):
    """Session `name`'s chat media file `file`, or None (gone, or a name that would leave its folder)."""
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        return None
    if not re.fullmatch(r"\w[\w-]{0,119}(\.[A-Za-z0-9]{1,8})?", file):
        return None
    f = config.CHAT_DIR / f"{name}.media" / file
    return f if f.is_file() else None


def chat_media_headers(file: str) -> dict:
    kind = mimetypes.guess_type(file)[0] or "application/octet-stream"
    inline = bool(INLINE_MEDIA.fullmatch(kind))
    return {"Content-Type": kind if inline else "application/octet-stream", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "private, max-age=86400",
            "Content-Disposition": "inline" if inline else f'attachment; filename="{file}"'}


async def chat_media(request):
    f = chat_media_file(request.match_info["name"], request.match_info["file"])
    if f is None:
        raise web.HTTPNotFound()
    return web.FileResponse(f, headers=chat_media_headers(f.name))


async def chat_upload(request):
    """A file sent from the chat's + button (raw bytes, ?name=): saved in the chat's folder; the message names its path."""
    name = request.match_info["name"]
    if not sessions.session_exists(name) or not sessions.is_chat(name):
        raise web.HTTPNotFound(text="no such chat")
    data = await request.read()
    if not data:
        return bad_request("empty file")
    base = re.sub(r"[^\w .,()+-]", "_", request.query.get("name", ""))[:150].strip(" .") or "file"

    def save():
        d = config.CHAT_DIR / f"{name}.uploads"
        d.mkdir(parents=True, exist_ok=True)
        stem, dot, ext = base.rpartition(".") if "." in base else (base, "", "")
        f, n = d / base, 1
        while f.exists():  # never overwrite an earlier upload of the same name
            n += 1
            f = d / (f"{stem} ({n}).{ext}" if dot else f"{base} ({n})")
        f.write_bytes(data)
        return {"path": str(f), "name": f.name, "size": len(data), "host": config.HOST_NAME}
    return web.json_response(await asyncio.to_thread(save))


def routes() -> list:
    return [
        web.get("/api/sessions/{name}/media/{file}", chat_media),
        web.post("/api/sessions/{name}/upload", chat_upload),
    ]
