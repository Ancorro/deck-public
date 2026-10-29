"""The site's routes on a running server: pulling the journal, pushing to a follower, snapshots, the site answer the
page reads, and a follower forwarding a client's write to the writer."""
import asyncio
import json

import pytest

from deck import auth, config, server, settings, site
from deck.api import access


@pytest.fixture
async def node(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "CHAT_DIR", tmp_path / "chat")
    (tmp_path / "chat").mkdir()
    monkeypatch.setenv("DECK_ORIGINS", "https://good.example")
    monkeypatch.setattr(site, "_state", {"role": "follower", "seq": 0, "since": 0.0, "misses": {}, "last_seen": {}})
    monkeypatch.setattr(site, "running", lambda: set())

    async def idle(app):  # the site loop would make this lone node the writer a moment after the app starts: a race
        await asyncio.Event().wait()
    monkeypatch.setattr(site, "loop", idle)
    monkeypatch.setattr(auth, "host_key", lambda: "k-test")
    monkeypatch.setattr(auth, "is_host", lambda a: a == "Bearer k-test")
    monkeypatch.setattr(access, "is_local", lambda r: True)
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
    assert s["role"] == "follower" and s["me"] == config.HOST_NAME and "order" in s
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
    monkeypatch.setattr(settings, "hosts", lambda: {"hosts": [{"name": "w", "url": "http://w", "current": False},
                                                          {"name": config.HOST_NAME, "url": "http://me", "current": True}]})
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


async def test_a_followers_file_link_reaches_the_writer_with_the_file_read_here(node, monkeypatch, tmp_path):
    """POST /api/shelf/link on a follower: the file is on the follower's disk, not the writer's, so the follower reads
    it and sends it along ("info"); the writer stores that without looking for the path itself."""
    client, data = node
    sent = []

    class R:
        status = 200
        headers = {"Content-Type": "application/json"}
        async def read(self): return b'{"forwarded": true}'
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class HTTP:
        async def close(self): pass
        def request(self, method, url, **kw):
            sent.append((url, json.loads(kw["data"])))
            return R()
    monkeypatch.setattr(settings, "hosts", lambda: {"hosts": [{"name": "w", "url": "http://w", "current": False},
                                                          {"name": config.HOST_NAME, "url": "http://me", "current": True}]})
    site._state["last_seen"]["w"] = {"role": "writer", "ready": True, "at": __import__("time").time()}
    monkeypatch.setattr(site, "above", lambda name="": ["w"])
    client.app._state["peer_http"] = HTTP()
    doc = tmp_path / "paper" / "notes.md"
    doc.parent.mkdir()
    doc.write_text("# Notes\n")
    r = await client.post("/api/shelf/link", json={"path": str(doc), "title": "Notes"})
    assert r.status == 200 and len(sent) == 1
    url, body = sent[0]
    assert url == "http://w/api/shelf/link" and body["path"] == str(doc)
    assert body["info"]["file"] == {**body["info"]["file"], "host": config.HOST_NAME, "path": str(doc.resolve()),
                                    "linked": True, "type": "text/markdown"}
    assert body["info"]["text"] == "# Notes\n"
    # a path this host doesn't have fails here, before anything reaches the writer
    r = await client.post("/api/shelf/link", json={"path": str(tmp_path / "gone.pdf")})
    assert r.status == 400 and len(sent) == 1
    # the writer's side: it stores the follower's file as is, though the path isn't on its disk
    site.become_writer()
    info = {"file": {"name": "paper.pdf", "type": "application/pdf", "size": 9, "host": "other-pc",
                     "path": "/elsewhere/paper.pdf", "mtime": 1.0}, "text": "(linked file)"}
    r = await client.post("/api/shelf/link", json={"path": "/elsewhere/paper.pdf", "title": "Paper", "info": info},
                          headers=HOST)
    it = await r.json()
    assert r.status == 200 and it["file"]["host"] == "other-pc" and it["file"]["linked"] and it["title"] == "Paper"
    again = await (await client.post("/api/shelf/link", json={"path": "/elsewhere/paper.pdf", "info": info},
                                     headers=HOST)).json()
    assert again["id"] == it["id"]  # the same file again: the same item
    # only another host may hand over a file this one doesn't have; a client's "info" is ignored
    r = await client.post("/api/shelf/link", json={"path": "/elsewhere/paper.pdf", "info": info})
    assert r.status == 400


async def test_the_writer_takes_a_followers_chat_offer(node):
    client, data = node
    e = {"seq": 0, "rel": "chat/deck-claude-9.jsonl", "mode": "w", "offset": 0, "size": 3, "data": "WzFd", "at": 0}
    assert (await client.post("/api/site/offer", json={"entries": [e]}, headers=HOST)).status == 409  # a follower: not mine to journal
    site.become_writer()
    r = await client.post("/api/site/offer", json={"entries": [e]}, headers=HOST)
    assert r.status == 200 and (await r.json()) == {"taken": 1, "whole": []}
    assert (data / "chat" / "deck-claude-9.jsonl").read_text() == "[1]"
    assert (await client.post("/api/site/offer", json={"entries": []})).status == 403  # host key only
