"""The API package as a whole: every route module registers, and the page's parts are served joined in order."""
import asyncio
import re

import pytest

from deck import config, server, site
from deck.api import ROUTE_MODULES, pages


@pytest.fixture
async def client(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    # This node is the writer from the start: the site loop would make it one only a moment after the app starts (a
    # race), and on a host whose inventory lists other hosts, never; shared writes then answer 503 "retry"
    monkeypatch.setattr(site, "_state", {"role": "writer", "seq": 0, "since": 0.0, "misses": {}, "last_seen": {}})

    async def idle(app):
        await asyncio.Event().wait()
    monkeypatch.setattr(site, "loop", idle)
    return await aiohttp_client(server.make_app())


def test_every_route_module_registers_distinct_paths():
    seen = {}
    for m in ROUTE_MODULES:
        routes = m.routes()
        assert routes, m.__name__
        for r in routes:
            key = (getattr(r, "method", "STATIC"), getattr(r, "path", None) or r.prefix)
            assert key not in seen, f"{m.__name__} and {seen[key]} both register {key}"
            seen[key] = m.__name__
    assert ROUTE_MODULES[-1] is pages  # its static route catches the rest of /static/


async def test_page_settings_and_script_answer(client):
    assert (await client.get("/")).status == 200
    r = await client.get("/api/settings")
    assert r.status == 200 and "agent" in await r.json()
    r = await client.get("/static/app.js")
    assert r.status == 200 and r.headers["ETag"]
    assert (await client.get("/static/app.js", headers={"If-None-Match": r.headers["ETag"]})).status == 304


def test_script_parts_match_the_folder_and_run_in_order():
    # APP_ORDER names every part once, in the folder's sorted order, and the parts declare before start.js runs them
    files = sorted(p.stem for p in pages.APP_PARTS.glob("*.js"))
    assert list(pages.APP_ORDER) == files
    assert files[0].endswith("core") and files[-1].endswith("start")
    assert all(re.fullmatch(r"\d\d-[a-z]+", f) for f in files)
    text = pages.app_js()
    assert text.startswith('"use strict";')
    assert text.count('"use strict"') == 1  # one script: a second directive mid-file would be a plain string


async def test_page_and_lists_go_gzipped(client):
    # "deck laggy to open": the script, style and lists went uncompressed on every open
    for path in ("/", "/static/app.js", "/static/style.css", "/api/settings"):
        r = await client.get(path, headers={"Accept-Encoding": "gzip"})
        assert r.status == 200 and r.headers.get("Content-Encoding") == "gzip", path
        assert await r.read()  # decompressed by the client
    r = await client.get("/static/app.js", headers={"Accept-Encoding": "identity"})  # not asked for: as is
    assert "Content-Encoding" not in r.headers and len(await r.read()) > 100_000
    r = await client.get("/static/icon.svg", headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"})
    assert "Content-Encoding" not in r.headers


async def test_shelf_list_cuts_every_long_text(client):
    long = "x" * 10_000
    from deck import shelf
    turn = shelf.add({"kind": "turn", "text": long}, {})
    short = shelf.add({"kind": "turn", "text": "hi"}, {})
    gone = shelf.add({"kind": "turn", "text": long}, {})
    shelf.delete(gone["id"], False)  # a trashed one too: it came whole before
    items = {it["id"]: it for it in await (await client.get("/api/shelf")).json()}
    for i in (turn["id"], gone["id"]):
        assert len(items[i]["text"]) == 3000 and items[i]["cut"] == 10_000
    assert items[short["id"]]["text"] == "hi" and "cut" not in items[short["id"]]
    assert (await (await client.get(f"/api/shelf/{turn['id']}")).json())["text"] == long


async def test_board_posts_updates_by_key_and_removes(client):
    # A session posts cards; the same session and key update its card in place and bring it to the top
    r = await client.post("/api/board", json={"title": "Run 1", "text": "epoch 1", "kind": "status", "key": "run1",
                                               "session": "s1"})
    first = await r.json()
    assert r.status == 200 and first["kind"] == "status"
    await client.post("/api/board", json={"title": "Done", "kind": "result", "session": "s2"})
    r = await client.post("/api/board", json={"title": "Run 1", "text": "epoch 2", "kind": "status", "key": "run1",
                                               "session": "s1"})
    assert (await r.json())["id"] == first["id"]
    cards = await (await client.get("/api/board")).json()
    assert [c["text"] for c in cards] == ["epoch 2", ""] and cards[0]["first"] == first["at"]
    # another session's same key is its own card
    await client.post("/api/board", json={"title": "x", "key": "run1", "session": "s3"})
    assert len(await (await client.get("/api/board")).json()) == 3
    # a project's card (status-<repo>) is shared: another session in the project updates it
    proj = await (await client.post("/api/board", json={"title": "repo", "text": "a", "kind": "status",
                                                        "key": "status-repo", "session": "s1"})).json()
    r = await client.post("/api/board", json={"title": "repo", "text": "b", "kind": "status", "key": "status-repo",
                                               "session": "s4"})
    assert (await r.json())["id"] == proj["id"]
    await client.delete(f"/api/board/{proj['id']}?forever=1")
    for bad in ({}, {"title": "t", "kind": "loud"}, {"title": "t", "url": "javascript:alert(1)"}, {"kind": "link", "title": "t"}):
        assert (await client.post("/api/board", json=bad)).status == 400
    await client.delete(f"/api/board/{first['id']}")  # to the trash can, restorable
    trashed = next(c for c in await (await client.get("/api/board")).json() if c["id"] == first["id"])
    assert trashed["deleted"]
    await client.post(f"/api/board/{first['id']}/restore")
    assert "deleted" not in next(c for c in await (await client.get("/api/board")).json() if c["id"] == first["id"])
    await client.delete(f"/api/board/{first['id']}?forever=1")
    assert first["id"] not in [c["id"] for c in await (await client.get("/api/board")).json()]
    assert (await client.post("/api/board/clear")).status in (404, 405)  # persistent: no clear all (user)


async def test_board_cards_stay_where_pinned(client):
    card = await (await client.post("/api/board", json={"title": "t", "key": "k", "session": "s"})).json()
    assert (await client.put(f"/api/board/{card['id']}", json={"x": 400, "y": -5, "z": 3})).status == 200
    await client.post("/api/board", json={"title": "t2", "key": "k", "session": "s"})  # an update keeps its spot
    c = (await (await client.get("/api/board")).json())[0]
    assert (c["title"], c["x"], c["y"], c["z"]) == ("t2", 400, 0, 3)
    await client.put(f"/api/board/{card['id']}", json={"s": 0.1})  # pinched small: held to the smallest size
    c = (await (await client.get("/api/board")).json())[0]
    assert (c["s"], c["x"]) == (0.3, 400)


async def test_board_list_ticks_and_link_type(client):
    lst = await (await client.post("/api/board", json={"kind": "list", "title": "Todo", "text": "a\nb"})).json()
    await client.put(f"/api/board/{lst['id']}", json={"tick": 1})
    await client.put(f"/api/board/{lst['id']}", json={"tick": 9})  # out of range: nothing
    cards = await (await client.get("/api/board")).json()
    assert cards[0]["text"] == "a\n[x] b"
    await client.put(f"/api/board/{lst['id']}", json={"tick": 1})
    assert (await (await client.get("/api/board")).json())[0]["text"] == "a\nb"
    link = await (await client.post("/api/board", json={"url": "https://example.com/x"})).json()
    assert (link["kind"], link["title"]) == ("link", "example.com")
    assert (await (await client.post("/api/board", json={"kind": "result", "text": "r"})).json())["kind"] == "note"


async def test_skills_list_read_and_save(client, tmp_path, monkeypatch):
    src = tmp_path / "skills" / "demo"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_bytes(b"---\nname: demo\ndescription: A demo.\n---\nhi\n")
    monkeypatch.setattr(config, "SKILLS", tmp_path / "skills")
    got = await (await client.get("/api/skills")).json()
    assert got["skills"] == [{"name": "demo", "description": "A demo.", "files": ["SKILL.md"]}]
    r = await client.get("/api/skills/file", params={"path": "demo/SKILL.md"})
    assert (await r.json())["text"].endswith("hi\n")
    assert (await client.put("/api/skills/file", json={"path": "demo/SKILL.md", "text": "changed\n"})).status == 200
    assert (src / "SKILL.md").read_bytes() == b"changed\n"
    assert (await client.get("/api/skills/file", params={"path": "demo/../../x.md"})).status == 400
    assert (await client.put("/api/skills/file", json={"path": "../config.json", "text": "x"})).status == 400


async def test_script_hands_files_to_the_device_that_pressed_it(client, tmp_path, monkeypatch):
    # A script key's DECK_OUT files: listed with its output, fetched as a download, and marked once a window saved one
    # (user 2026-11-23: the X video key "should try to go to the client machine, (drive if cant)")
    from deck import sessions
    monkeypatch.setattr(sessions, "session_exists", lambda name: True)
    monkeypatch.setattr(sessions, "session_output", lambda name: "done\n")
    assert await (await client.get("/api/sessions/deck-a/text")).json() == {"text": "done\n"}  # no folder: no files
    out = config.run_files("deck-a")
    out.mkdir(parents=True)
    (out / "clip 1.mp4").write_bytes(b"video")
    (out / ".clip 2.mp4.part").write_bytes(b"half")  # still being written: not offered
    (tmp_path / "secret.txt").write_bytes(b"x")
    r = await (await client.get("/api/sessions/deck-a/text")).json()
    assert r["files"] == [{"file": "clip 1.mp4", "size": 5}]
    r = await client.get("/api/sessions/deck-a/files/clip%201.mp4")
    assert r.status == 200 and await r.read() == b"video"
    assert r.headers["Content-Type"] == "application/octet-stream" and r.headers["Content-Disposition"].startswith("attachment")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    for bad in ("..%2Fsecret.txt", ".clip%202.mp4.part", "nope.mp4", "%2E%2E"):
        assert (await client.get(f"/api/sessions/deck-a/files/{bad}")).status == 404, bad
    assert sessions.run_file("..", "deck-a.files") is None and sessions.run_file("a/b", "x") is None
    assert (await client.post("/api/sessions/deck-a/files/nope.mp4/saved")).status == 404
    assert (await client.post("/api/sessions/deck-a/files/clip%201.mp4/saved")).status == 200
    assert (out / ".clip 1.mp4.saved").exists()  # what the script waits for
    assert "files" not in await (await client.get("/api/sessions/deck-a/text")).json()  # saved: offered no more
