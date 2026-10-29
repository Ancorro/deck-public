"""Deck's web server: `python -m deck.server` serves the API (deck/api) on this host's port and the tunnel's. The
systemd unit (deploy/deck.service) and the PC app (desktop/) run it this way."""
import asyncio
import socket
import time

from aiohttp import web

from . import config, sessions
from .api import make_app
from .api.host import restart_windows

__all__ = ["make_app", "bind", "main"]


def bind(addr) -> socket.socket:
    """A listening socket, retried for a while: after an update the old server may still be letting go of it."""
    for _ in range(60):
        try:
            return socket.create_server(addr)
        except OSError:
            time.sleep(0.25)
    return socket.create_server(addr)


async def restart_on_dead_listener(_app) -> None:
    """Windows' asyncio closes a listening socket for good when one accept fails (WinError 64: a client reset while it
    waited in the backlog), leaving a running server that answers nothing on that port. Restart instead."""
    def handler(loop, context):
        loop.default_exception_handler(context)
        if context.get("message") == "Accept failed on a socket":
            loop.set_exception_handler(None)  # once: the other port may fail too while stopping
            restart_windows()
    asyncio.get_running_loop().set_exception_handler(handler)


def main() -> None:
    # Open terminal sockets would hold a stop/restart for aiohttp's default 60s; phones reconnect on their own.
    # Deck's own port, and the tunnel's (loopback only: deck-iroh connects to it).
    sessions.start_tmux()
    socks = [bind((config.HOST, config.PORT)), bind(("127.0.0.1", config.TUNNEL_PORT))]
    app = make_app()
    if config.WINDOWS:
        app.on_startup.append(restart_on_dead_listener)
    web.run_app(app, sock=socks, shutdown_timeout=2)


if __name__ == "__main__":
    main()
