"""This host: its settings and hosts, the Deck update, the mic's speech-to-text, and whether a web page may be
shown in the Web view's frame."""
import asyncio
import os
import re
import signal
import subprocess
from pathlib import Path

from aiohttp import web

from .. import agents, config, links, settings
from .common import bad_request


def git_head() -> str:
    """The checkout's commit, short; "" with no git (the PC app's bundled copy)."""
    try:
        r = subprocess.run(["git", "-C", str(config.REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        return r.stdout.strip()
    except OSError:
        return ""


RUNNING_COMMIT = git_head()  # the commit this server started on: the update route compares against it


async def get_settings(_):
    """Every setting, and what this host has: the agents installed, whether the mic can transcribe, and `fresh` (nothing
    saved yet, a new install: the page asks whether this PC hosts sessions)."""
    s = settings.settings()
    return web.json_response({**s, "fresh": not settings.settings_path().exists(), "pi": agents.pi_available(),
                              "agy": agents.agy_available(), "openclaw": agents.openclaw_available(),
                              "agents": agents.agents_installed(), "chatagents": agents.CHAT_AGENTS,
                              "durable": agents.pi_durable_available(), "notesname": config.NOTES.name,
                              "transcribe": bool(config.TRANSCRIBE_URL)})


async def put_settings(request):
    try:
        saved = settings.save_settings(await request.json())
    except ValueError as e:
        return bad_request(e)
    links.update_me()
    return web.json_response(saved)


async def get_hosts(_):
    return web.json_response(settings.hosts())


async def transcribe(request):
    """The message box's mic, through the host's speech-to-text (DECK_TRANSCRIBE_URL): the audio in, {text} out."""
    if not config.TRANSCRIBE_URL:
        return web.json_response({"error": "no DECK_TRANSCRIBE_URL on this host"}, status=404)
    import aiohttp
    audio = await request.read()
    try:
        async with request.app["peer_http"].post(config.TRANSCRIBE_URL, data=audio,
                                                 headers={"Content-Type": request.content_type},
                                                 timeout=aiohttp.ClientTimeout(total=120, sock_connect=3)) as r:
            return web.json_response(await r.json(content_type=None), status=r.status)
    except (TimeoutError, OSError, aiohttp.ClientError, ValueError) as e:
        return web.json_response({"error": f"speech-to-text not reachable ({e.__class__.__name__}): is it running?"},
                                 status=502)


def frame_blocked(headers) -> bool:
    """A page's response headers forbid showing it in another site's frame (X-Frame-Options, CSP frame-ancestors)."""
    xfo = headers.get("X-Frame-Options", "").strip().lower()
    if xfo in ("deny", "sameorigin") or xfo.startswith("allow-from"):
        return True
    for csp in headers.getall("Content-Security-Policy", []):
        for d in csp.split(";"):
            parts = d.split()
            if parts and parts[0].lower() == "frame-ancestors":
                return "*" not in parts[1:]
    return False


async def web_check(request):
    """The Web view: may this page be shown in a frame? {frameable: true|false|null} (null: it couldn't be reached)."""
    import aiohttp
    url = request.query.get("url", "")
    if not re.fullmatch(r"https?://[^\s/]+\S*", url):
        return bad_request(ValueError("url must be an http(s) address"))
    try:
        async with request.app["peer_http"].get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=8),
                                                headers={"User-Agent": "Mozilla/5.0 (Deck web view check)"}) as r:
            return web.json_response({"frameable": not frame_blocked(r.headers)})
    except (TimeoutError, aiohttp.ClientError, ValueError):
        return web.json_response({"frameable": None})


async def update(_):
    """Pull the latest Deck code and, if it changed, restart this service (runs survive: KillMode=process)."""
    async def git(*args):
        p = await asyncio.create_subprocess_exec("git", "-C", str(config.REPO), *args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
        out = (await p.communicate())[0].decode().strip()
        return p.returncode, out

    code, out = await git("pull", "--ff-only", "-q")
    if code:
        return web.json_response({"error": out or "git pull failed"}, status=500)
    # Compare with the code this server started on: the checkout may already be ahead (committed here).
    old, new = RUNNING_COMMIT, (await git("rev-parse", "--short", "HEAD"))[1]
    if new != old:  # restart after the response is sent; tmux and its sessions keep running either way
        asyncio.get_running_loop().call_later(0.5, restart_windows if config.WINDOWS else lambda: os.spawnlp(
            os.P_NOWAIT, "systemctl", "systemctl", "--user", "restart", "deck"))  # systemd kills only python
    return web.json_response({"host": config.HOST_NAME, "old": old, "new": new, "restarting": new != old})


def restart_windows() -> None:
    """Windows (the PC app's server folder is a git checkout): stop this server. Under the PC app (DECK_SUPERVISED) the
    app starts the new one from the updated checkout; otherwise start it here first. The new one waits for the ports
    (bind). Same command as the PC app's (desktop/main.go, python())."""
    if not os.environ.get("DECK_SUPERVISED"):
        uv = config.REPO.parent / "uv.exe"  # bundled next to the PC app
        log = open(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "deck" / "server.log", "a")
        subprocess.Popen([str(uv) if uv.exists() else "uv", "run", "--no-project", "--python", "3.12", "--with",
                          "aiohttp", "--with", "qrcode", "--with", "pywinpty", "python", "-m", "deck.server"], cwd=config.REPO,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    signal.raise_signal(signal.SIGINT)  # run_app's graceful stop (closes the terminal sockets)


def routes() -> list:
    return [
        web.get("/api/settings", get_settings),
        web.put("/api/settings", put_settings),
        web.get("/api/hosts", get_hosts),
        web.post("/api/transcribe", transcribe),
        web.get("/api/web/check", web_check),
        web.post("/api/update", update),
    ]
