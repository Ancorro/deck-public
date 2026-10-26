"""The site's routes on a running server: pulling the journal, pushing to a follower, snapshots, the site answer the
page reads, and a follower forwarding a client's write to the writer."""
import json

import pytest

from deck import auth, core, server, site


@pytest.fixture
async def node(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setattr(core, "CHAT_DIR", tmp_path / "chat")
    (tmp_path / "chat").mkdir()
    monkeypatch.setenv("DECK_ORIGINS", "https://good.example")
    monkeypatch.setattr(site, "_state", {"role": "follower", "seq": 0, "since": 0.0, "misses": {}, "last_seen": {}})
    monkeypatch.setattr(site, "running", lambda: set())
    monkeypatch.setattr(auth, "host_key", lambda: "k-test")
    monkeypatch.setattr(auth, "is_host", lambda a: a == "Bearer k-test")
    monkeypatch.setattr(server, "is_local", lambda r: True)
    return await aiohttp_client(server.make_app()), tmp_path


HOST = {"Authorization": "Bearer k-test"}


async def test_journal_pull_push_and_snapshot(node):
    client, data = node
    (data / "buttons.json").write_text("[]")
    site.record("buttons.json", b"[]")
    r = await client.get("/api/site/journal", params={"after": "0"}, headers=HOST)
    got = await r.json()
    assert r.status == 200 and [e["rel"] for e in got["entries"]] == ["buttons.json"] and got["seq"] == 1
    assert (await client.get("/api/site/journal", headers=HOST)).status == 200
    assert (await client.get("/api/site/journal")).status == 403  # host key only
    # a push from the writer lands in the copy
    e = {"seq": 2, "rel": "shelf.json", "mode": "w", "offset": 0, "size": 3, "data": "WzFd", "at": 0}  # "[1]"
    r = await client.post("/api/site/journal", json={"entries": [e]}, headers=HOST)
    assert (await r.json())["seq"] == 2 and (data / "shelf.json").read_text() == "[1]"
    r = await client.get("/api/site/snapshot", headers=HOST)
    assert r.status == 200 and r.headers["Content-Type"].startswith("application/gzip")
    # too far behind: 410, take the snapshot
    site.KEEP = 1
    site.trim()
    assert (await client.get("/api/site/journal", params={"after": "0"}, headers=HOST)).status == 410
    site.KEEP = 20000


async def test_site_answer_for_the_page_and_a_writer_refuses_pushes(node):
    client, _ = node
    r = await client.get("/api/site")
    s = await r.json()
    assert s["role"] == "follower" and s["me"] == core.HOST_NAME and "order" in s
    site.become_writer()
    assert (await (await client.get("/api/site")).json())["role"] == "writer"
    r = await client.post("/api/site/journal", json={"entries": []}, headers=HOST)
    assert r.status == 409


async def test_a_follower_forwards_a_clients_write_to_the_writer(node, monkeypatch):
    client, data = node
    forwarded = []

    class R:
        status = 200
        headers = {"Content-Type": "application/json"}
        async def read(self): return b'{"forwarded": true}'
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class HTTP:
        async def close(self): pass
        def request(self, method, url, **kw):
            forwarded.append((method, url))
            return R()
    monkeypatch.setattr(core, "hosts", lambda: {"hosts": [{"name": "w", "url": "http://w", "current": False},
                                                          {"name": core.HOST_NAME, "url": "http://me", "current": True}]})
    site._state["last_seen"]["w"] = {"role": "writer", "ready": True, "at": __import__("time").time()}
    monkeypatch.setattr(site, "above", lambda name="": ["w"])
    client.app["peer_http"].close = HTTP.close  # swapped under a started app: fine for a test double
    client.app._state["peer_http"] = HTTP()
    r = await client.put("/api/settings", json={"alias": "x"})
    assert r.status == 200 and (await r.json()) == {"forwarded": True}
    assert forwarded == [("PUT", "http://w/api/settings")]
    assert not (data / "settings.json").exists()  # nothing written here: the journal brings it
    # a session start stays local (sessions are per node): not forwarded
    assert (await client.get("/api/sessions")).status == 200 and len(forwarded) == 1


async def test_the_writer_takes_a_followers_chat_offer(node):
    client, data = node
    e = {"seq": 0, "rel": "chat/deck-claude-9.jsonl", "mode": "w", "offset": 0, "size": 3, "data": "WzFd", "at": 0}
    assert (await client.post("/api/site/offer", json={"entries": [e]}, headers=HOST)).status == 409  # a follower: not mine to journal
    site.become_writer()
    r = await client.post("/api/site/offer", json={"entries": [e]}, headers=HOST)
    assert r.status == 200 and (await r.json()) == {"taken": 1, "whole": []}
    assert (data / "chat" / "deck-claude-9.jsonl").read_text() == "[1]"
    assert (await client.post("/api/site/offer", json={"entries": []})).status == 403  # host key only
