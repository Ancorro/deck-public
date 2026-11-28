"""This host: its settings and hosts, the Deck update, the mic's speech-to-text, and whether a web page may be
shown in the Web view's frame."""
import asyncio
import os
import re
import signal
import subprocess
from pathlib import Path

from aiohttp import web

from .. import agents, config, links, logins, sessions, settings
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
                              "transcribe": bool(config.TRANSCRIBE_URL), "computer": bool(agents.computer_config())})


async def put_settings(request):
    try:
        saved = settings.save_settings(await request.json())
    except ValueError as e:
        return bad_request(e)
    links.update_me()
    return web.json_response(saved)


async def codex_models(_):
    """List the signed-in account's models without blocking the server event loop."""
    from ..bridge.codex import live_models
    result = await asyncio.to_thread(live_models, [])
    if result is None:
        return web.json_response({"error": "Could not load Codex models on this host"}, status=503)
    return web.json_response(result)


# Settings → General → New sessions → Default model: each agent's own list, from its CLI (Codex: its account's catalog).
# OpenClaw has no list to read here (its Gateway holds the models), so the page keeps the saved choice only.
MODEL_LISTS = {"opencode": ["models"], "pi": ["--list-models"], "agy": ["models"]}


def cli_models(kind: str) -> list[dict] | None:
    """One agent's models as [{model, displayName}], or None when its CLI is missing or does not answer."""
    from ..bridge import util
    if kind == "claude":
        return [{"model": m, "displayName": m.capitalize()} for m in settings.MODELS["claude"]]
    try:
        out = subprocess.run([*util.resolve(kind), *MODEL_LISTS[kind]], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL).stdout
    except (OSError, SystemExit, subprocess.SubprocessError):
        return None
    models = []
    for line in out.splitlines():
        cols = line.split("	") if kind == "agy" else line.split()
        if kind == "pi":  # a table: provider, model, ...; the flag takes provider/id
            if len(cols) < 2 or cols[0] == "provider":
                continue
            cols = [f"{cols[0]}/{cols[1]}"]
        if cols and settings.valid_model(kind, cols[0].strip()):
            models.append({"model": cols[0].strip(), "displayName": (cols[1].strip() if len(cols) > 1 else cols[0].strip())})
    return models or None


async def _local_models(request, kind: str) -> list[dict] | None:
    if kind == "codex":
        from ..bridge.codex import live_models
        return ((await asyncio.to_thread(live_models, [])) or {}).get("data") or None
    if kind != "claude" and kind not in MODEL_LISTS:
        return None
    return await asyncio.to_thread(cli_models, kind)


_models_cache: dict[str, tuple[float, list[dict], str]] = {}


async def agent_models(request):
    """GET /api/models/{kind}: the models one agent offers. One list for the whole Deck (user 2026-11-10: "there is a
    ground truth"): the longest list any host has, so every host shows the same menu. ?here=1 is this
    host's own list (what the hosts ask each other). Kept 10 minutes."""
    import time

    import aiohttp

    from .. import auth, site
    kind = request.match_info["kind"]
    if request.query.get("here") == "1" or not auth.host_key():
        result = await _local_models(request, kind)
        if result is None:
            return web.json_response({"error": f"Could not load {kind} models on this host"}, status=503)
        return web.json_response({"data": result})
    hit = _models_cache.get(kind)
    if hit and time.time() - hit[0] < 600:
        return web.json_response({"data": hit[1], "host": hit[2]})

    async def ask(name):
        h = site.host_of(name)
        if not h:
            return None
        if h.get("current"):
            return await _local_models(request, kind)
        try:
            async with request.app["peer_http"].get(f"{h['url'].rstrip('/')}/api/models/{kind}?here=1",
                                                    headers={"Authorization": f"Bearer {auth.host_key()}"},
                                                    timeout=aiohttp.ClientTimeout(total=40, sock_connect=3)) as r:
                return (await r.json()).get("data") or None if r.status == 200 else None
        except (TimeoutError, aiohttp.ClientError, OSError, ValueError):
            return None
    names = site.order()
    # the longest list wins (hosts differ in providers signed in; user 2026-11-10 agreed); ties go to the site order
    got = [(len(r), -i, name, r) for i, (name, r) in enumerate(zip(names, await asyncio.gather(*map(ask, names)))) if r]
    if got:
        _, _, name, result = max(got, key=lambda g: g[:2])
        _models_cache[kind] = (time.time(), result, name)
        return web.json_response({"data": result, "host": name})
    return web.json_response({"error": f"Could not load {kind} models on any host"}, status=503)


async def get_logins(request):
    """Shared agent and tool sign-ins across hosts (deck/logins.py); ?here=1 checks this host only, ?fresh=1 skips cache."""
    import aiohttp

    from .. import auth, site
    fresh = request.query.get("fresh") == "1"
    if request.query.get("here") == "1" or not auth.host_key():
        return web.json_response(await asyncio.to_thread(logins.status, fresh))

    async def ask(name: str):
        h = site.host_of(name)
        if (h and h.get("current")) or (not h and name == config.HOST_NAME):
            return name, await asyncio.to_thread(logins.status, fresh)
        if not h or not h.get("url"):
            return name, None
        q = "?here=1&fresh=1" if fresh else "?here=1"
        try:
            async with request.app["peer_http"].get(
                f"{h['url'].rstrip('/')}/api/logins{q}",
                headers={"Authorization": f"Bearer {auth.host_key()}"},
                timeout=aiohttp.ClientTimeout(total=50, sock_connect=3),
            ) as r:
                data = await r.json() if r.status == 200 else None
                return name, data if isinstance(data, list) else None
        except (TimeoutError, aiohttp.ClientError, OSError, ValueError):
            return name, None

    names = site.order() or [config.HOST_NAME]
    got = [(name, items) for name, items in await asyncio.gather(*map(ask, names)) if items]
    return web.json_response(logins.merge(got))


async def start_login(request):
    """A terminal tab running a tool's own login: {session}. Refused when it is already signed in."""
    try:
        key = await asyncio.to_thread(logins.login_key, request.match_info["tool"])
        return web.json_response({"session": await asyncio.to_thread(sessions.start_run, key)})
    except ValueError as e:
        return bad_request(e)


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
    if new != old:  # restart after the response is sent; tmux, its sessions and the chats' bridges keep running
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
        web.get("/api/codex/models", codex_models),
        web.get("/api/models/{kind}", agent_models),
        web.get("/api/hosts", get_hosts),
        web.get("/api/logins", get_logins),
        web.post("/api/logins/{tool}", start_login),
        web.post("/api/transcribe", transcribe),
        web.get("/api/web/check", web_check),
        web.post("/api/update", update),
    ]
