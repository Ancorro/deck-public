"""The board (deck/board.py): cards sessions post, shown in the Board view."""
from aiohttp import web

from .. import board
from .common import bad_request
from .shelf import session_meta


async def get_board(_):
    """Every card, the trash can's too (with "deleted")."""
    return web.json_response(board.load())


async def post_board(request):
    """A session posts a card: {title, text, kind, url, key, session}; the same session and key update it."""
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("send a JSON object")
        return web.json_response(board.post(body, session_meta(body)))
    except ValueError as e:
        return bad_request(e)


async def move_card(request):
    """A card dragged on the board: {x, y, z}."""
    body = await request.json()
    board.move(request.match_info["id"], body if isinstance(body, dict) else {})
    return web.json_response({"ok": True})


async def del_card(request):
    """To the trash can; ?forever=1: gone."""
    board.delete(request.match_info["id"], request.query.get("forever") == "1")
    return web.json_response({"ok": True})


async def restore_card(request):
    board.restore(request.match_info["id"])
    return web.json_response({"ok": True})


def routes() -> list:
    return [
        web.get("/api/board", get_board),
        web.post("/api/board", post_board),
        web.put("/api/board/{id}", move_card),
        web.delete("/api/board/{id}", del_card),
        web.post("/api/board/{id}/restore", restore_card),
    ]
