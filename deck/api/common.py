"""What every route module shares: the error answers."""
from aiohttp import web

from .. import auth


def bad_request(e: Exception):
    return web.json_response({"error": str(e)}, status=400)


def host_only(request) -> None:
    if not auth.is_host(request.headers.get("Authorization")):
        raise web.HTTPForbidden()
