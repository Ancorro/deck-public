"""Deck's web API: one aiohttp application from the route modules here, guarded by the middlewares in access.py.
Each module owns one area (its routes list says which paths); make_app joins them and starts the background loops
(peers, the site, linked PCs, alerts, descriptions, the screen's idle timers)."""
import asyncio

from aiohttp import web

from .. import alerts, describe
from .. import links as linked_pcs
from .. import site as site_state
from . import (
    access,
    agents,
    chat,
    host,
    keys,
    links,
    notes,
    pages,
    pairing,
    peers,
    saves,
    screen,
    sessions,
    shelf,
    site,
    terminal,
)

ROUTE_MODULES = (keys, sessions, peers, site, screen, chat, agents, saves, shelf, host, links, pairing, notes, terminal, pages)


def make_app() -> web.Application:
    app = web.Application(middlewares=[access.origin_guard, access.auth_guard, access.site_forward, access.revalidate],
                          client_max_size=32 * 1024 * 1024)  # notes bundles
    app["origins"] = access.allowed_origins()
    app.add_routes([r for m in ROUTE_MODULES for r in m.routes()])  # pages last: its static route catches the rest of /static/

    async def start(app):
        import aiohttp
        loop = asyncio.get_running_loop()
        app["peer_http"] = aiohttp.ClientSession()
        app["peerstate"] = {"wanted": 0.0, "seen": {}}  # the other hosts' session lists (peers.peers_loop)
        app["phonestate"] = {"seen": None}  # last keep-alive from a page streaming the phone screen
        app["vncstate"] = {"streams": 0, "idle": 0.0}  # open /vnc/ streams; when the last one closed
        app["tasks"] = [loop.create_task(t) for t in (
            alerts.watch(app),  # phone alerts when an agent session is waiting (DECK_NTFY)
            describe.watch(app),  # tab descriptions
            peers.peers_loop(app),  # every host's sessions (get_all_sessions) and the /peer proxy
            site_state.loop(app),
            linked_pcs.loop(app),
            screen.phone_idle_loop(app),
            screen.vnc_idle_loop(app),
        )]

    async def stop(app):
        for t in app["tasks"]:
            t.cancel()
        linked_pcs.stop_all()
        await app["peer_http"].close()

    app.on_startup.append(start)
    app.on_cleanup.append(stop)
    return app
