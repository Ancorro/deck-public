"""The column pushed, not polled (deck/api/live.py): one listing of this host's sessions for every page, a socket
that sends the whole column when any of it changes, and other hosts' lists pushed in with the host key."""
import asyncio
import json

import pytest

from deck import auth, config, server, sessions, settings
from deck.api import live

HOST = {"Authorization": "Bearer k-test"}


@pytest.fixture
async def client(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(auth, "host_key", lambda: "k-test")
    monkeypatch.setattr(live, "LIST_EVERY", 0.05)
    monkeypatch.setattr(live, "PAGE_EVERY", 0.02)
    monkeypatch.setattr(settings, "hosts", lambda: {"hosts": [{"name": "here", "url": "http://here", "current": True},
                                                          {"name": "there", "url": "http://127.0.0.1:9", "current": False}]})
    state = {"list": [{"name": "deck-a", "deck": True, "activity": 1}]}
    calls = {"n": 0}

    def fake_list():
        calls["n"] += 1
        return [dict(s, activity=calls["n"]) for s in state["list"]]  # activity moves each time: not a change
    monkeypatch.setattr(sessions, "list_sessions", fake_list)
    c = await aiohttp_client(server.make_app())
    c.state, c.calls = state, calls
    return c


async def next_column(ws, timeout=2.0):
    return json.loads((await asyncio.wait_for(ws.receive(), timeout)).data)


async def test_one_listing_serves_every_page(client):
    before = client.calls["n"]
    for _ in range(20):
        assert [s["name"] for s in await (await client.get("/api/sessions")).json()] == ["deck-a"]
    assert client.calls["n"] - before < 5  # the requests read the listing; they do not each list the sessions


async def test_the_socket_sends_the_column_now_and_again_only_on_a_change(client):
    ws = await client.ws_connect("/api/sessions/stream")
    first = await next_column(ws)
    here = next(h for h in first["hosts"] if h["name"] == "here")
    assert [s["name"] for s in here["sessions"]] == ["deck-a"]
    with pytest.raises(TimeoutError):  # activity moving is no change: nothing more is sent
        await next_column(ws, 0.4)
    client.state["list"] = [{"name": "deck-a", "deck": True}, {"name": "deck-b", "deck": True}]
    got = await next_column(ws)
    assert [s["name"] for s in next(h for h in got["hosts"] if h["name"] == "here")["sessions"]] == ["deck-a", "deck-b"]
    await ws.close()


async def test_another_hosts_push_reaches_the_open_pages(client):
    ws = await client.ws_connect("/api/sessions/stream")
    await next_column(ws)
    assert (await client.post("/api/sessions/peer", json={"host": "there", "sessions": []})).status == 403  # host key only
    r = await client.post("/api/sessions/peer", json={"host": "there", "sessions": [{"name": "deck-x", "deck": True}]},
                          headers=HOST)
    assert r.status == 200
    got = await next_column(ws)
    there = next(h for h in got["hosts"] if h["name"] == "there")
    assert there["online"] is True and [s["name"] for s in there["sessions"]] == ["deck-x"]
    r = await client.post("/api/sessions/peer", json={"host": "nobody", "sessions": []}, headers=HOST)
    assert r.status == 404  # only this site's hosts
    await ws.close()


async def test_a_session_change_shows_at_once_not_at_the_next_tick(client, monkeypatch):
    monkeypatch.setattr(live, "LIST_EVERY", 30)  # only a poke can list again within this test
    await asyncio.sleep(0.1)
    ws = await client.ws_connect("/api/sessions/stream")
    await next_column(ws)
    client.state["list"] = [{"name": "deck-new", "deck": True}]
    monkeypatch.setattr(sessions, "kill_session", lambda name: None)
    monkeypatch.setattr("deck.session_trash.keep", lambda name: None)
    await client.delete("/api/sessions/deck-a")
    got = await next_column(ws, 1.0)
    assert [s["name"] for s in next(h for h in got["hosts"] if h["name"] == "here")["sessions"]] == ["deck-new"]
    await ws.close()
