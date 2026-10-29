"""The page and its files: index.html with the assets version in it, the page script joined from its parts,
the service worker, the app download (APK) and the short-lived links that let a phone's browser fetch it."""
import asyncio
import hashlib
import secrets
import time
from html import escape as html_escape
from pathlib import Path

from aiohttp import web

from .. import config

STATIC = Path(__file__).resolve().parent.parent / "static"


def assets_version() -> str:
    """The newest change time among the page's files: the ETag of the joined script, and what the page reloads on."""
    return str(max(int(f.stat().st_mtime) for f in [*STATIC.iterdir(), *APP_PARTS.iterdir(), *LOOKS.iterdir()] if f.is_file()))


# The page's script, kept as parts by concern (deck/static/app/, in APP_ORDER) and served joined as /static/app.js: one
# script, so functions hoist across all of it. A part's own top-level code touches only what that part declares; the
# boot (14-start.js) comes last, so nothing reads another part's top-level state before it exists.
APP_PARTS = STATIC / "app"
APP_ORDER = ("01-core", "02-prefs", "03-pairing", "04-pad", "05-tabs", "06-terminal", "07-chat", "08-markdown",
             "09-composer", "10-shelf", "11-review", "12-editor", "13-settings", "14-start")
LOOKS = STATIC / "looks"  # one stylesheet per look (Settings → Appearance → Look), over style.css


def app_js() -> str:
    return "\n".join((APP_PARTS / f"{n}.js").read_text(encoding="utf-8").rstrip("\n") + "\n" for n in APP_ORDER)


async def app_script(request):
    # Joined text has no file to revalidate against: the assets version is its ETag, so an unchanged page costs a 304
    tag = f'"{assets_version()}"'
    if request.headers.get("If-None-Match") == tag:
        return web.Response(status=304, headers={"ETag": tag})
    return web.Response(text=await asyncio.to_thread(app_js), content_type="text/javascript", headers={"ETag": tag})


async def index(_):
    # Version the asset URLs so a phone holding stale cached copies fetches the new ones.
    v = assets_version()
    host = html_escape(config.HOST_NAME)  # the page names its sessions host/name (01-core.js fullName)
    # The metas go first in <head>: the inline script there reads the version to load the look's stylesheet
    metas = f'<meta charset="utf-8">\n<meta name="deck-assets" content="{v}">\n<meta name="deck-host" content="{host}">'
    html = (STATIC / "index.html").read_text().replace('<meta charset="utf-8">', metas, 1)
    for name in ("style.css", "app.js", "screen.js"):
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={v}"')
    return web.Response(text=html, content_type="text/html")


async def service_worker(_):
    # Served from / so its scope covers the whole page.
    return web.FileResponse(STATIC / "sw.js", headers={"Cache-Control": "no-cache", "Content-Type": "text/javascript"})


# App links: the phone app can't save a download itself (its page is a WebView), so Settings → More → App asks for a
# link that the phone's browser, unpaired, may open for ten minutes: the APK carries the host list, so a link is
# short-lived and random (hashes kept, not tokens). Not one-use: an older app's WebView may swallow the first open.
APP_LINK_TTL = 600


_app_links: dict[str, float] = {}  # sha256(token) -> expiry


def app_link_ok(token: str) -> bool:
    now = time.time()
    for h, exp in list(_app_links.items()):
        if exp < now:
            _app_links.pop(h, None)
    return bool(token) and hashlib.sha256(token.encode()).hexdigest() in _app_links


async def app_link(request):
    """POST /api/app/link (a paired device or this host's browser): a short-lived link to /static/deck.apk."""
    if not (STATIC / "deck.apk").exists():
        return web.json_response({"error": "no app build on this host (app/build.sh, or a release's APK at "
                                           "deck/static/deck.apk)"}, status=404)
    token = secrets.token_urlsafe(24)
    _app_links[hashlib.sha256(token.encode()).hexdigest()] = time.time() + APP_LINK_TTL
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme
    url = f"{scheme}://{request.host}/static/deck.apk?t={token}"
    return web.json_response({"url": url, "expires": APP_LINK_TTL})


def routes() -> list:
    return [
        web.get("/", index),
        web.get("/sw.js", service_worker),
        web.post("/api/app/link", app_link),
        web.get("/static/app.js", app_script),  # before the static files: app.js is its parts joined
        web.static("/static", STATIC),
    ]
