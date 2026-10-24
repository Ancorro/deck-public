import pytest

from deck import core, server  # noqa: E402


@pytest.fixture
async def client(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    monkeypatch.setenv("DECK_ORIGINS", "https://good.example")
    return await aiohttp_client(server.make_app())


async def test_foreign_origin_rejected(client):
    r = await client.post("/api/buttons", json={"label": "x", "type": "claude"}, headers={"Origin": "https://evil.example"})
    assert r.status == 403
    r = await client.get("/ws/anything", headers={"Origin": "https://evil.example"})
    assert r.status == 403


async def test_unknown_host_rejected(client):
    # DNS rebinding: evil.example resolved to 127.0.0.1 would otherwise pass as this host's own browser.
    assert (await client.get("/api/settings", headers={"Host": "evil.example:8787"})).status == 403
    for host in ("127.0.0.1:8787", "localhost", "[::1]:8787", "good.example", "pc.tail1.ts.net"):
        assert (await client.get("/api/settings", headers={"Host": host})).status == 200, host


# On Windows these fail (the raw pane never reads; the text socket 404s) and leave their tmux server running.


async def test_peer_vnc_is_websocket_only_and_other_paths_stay_closed(client, monkeypatch):
    monkeypatch.setattr(core, "hosts", lambda: {"hosts": [{"name": "desk", "url": "https://desk.example", "current": False}]})
    assert (await client.get("/peer/desk/vnc/websockify")).status == 404  # not a WebSocket: nothing proxied
    assert (await client.get("/peer/desk/static/app.js")).status == 404


async def test_screen_over_iroh_shows_only_when_deck_serves_the_bridge(client, monkeypatch, tmp_path):
    # Optional features off until set up (user): no empty Screen tab where the stream can't come through.
    vnc = tmp_path / "vnc.json"
    monkeypatch.setattr(core, "VNC_FILE", str(vnc))
    assert (await (await client.get("/api/screen")).json())["enabled"] is False
    vnc.write_text('{"password": "p", "path": "/vnc/websockify"}')
    assert (await (await client.get("/api/screen")).json())["enabled"] is True
    monkeypatch.setattr(server, "via_tunnel", lambda r: True)
    monkeypatch.setattr(server, "is_local", lambda r: True)  # past the pairing check: only the tab's answer is tested
    assert (await (await client.get("/api/screen")).json())["enabled"] is False
    vnc.write_text('{"password": "p", "path": "/vnc/websockify", "bridge": "127.0.0.1:6080"}')
    assert (await (await client.get("/api/screen")).json())["enabled"] is True
    assert (await client.get("/vnc/websockify")).status == 404  # not a WebSocket: nothing piped


async def test_hosts_carry_this_hosts_iroh_key(client, monkeypatch, tmp_path):
    from deck import links
    inv = tmp_path / "inventory.toml"
    inv.write_text('[hosts.desk]\ndeck_url = "https://desk.example"\n')
    monkeypatch.setattr(core, "INVENTORY", str(inv))
    monkeypatch.setattr(core, "HOST_NAME", "desk")
    monkeypatch.setattr(links, "iroh_id", lambda: "K1")
    assert (await (await client.get("/api/hosts")).json())["hosts"][0]["iroh"] == "K1"


async def test_a_run_passes_its_one_off_argument(client, monkeypatch):
    seen = []
    monkeypatch.setattr(core, "load_buttons", lambda: [core.clean_button({"id": "cu", "label": "cu", "type": "shell", "text": 'go "$1"'})])
    monkeypatch.setattr(core, "start_run", lambda b: seen.append(b) or "s1")
    r = await client.post("/api/run/cu", json={"arg": "open notepad"})
    assert r.status == 200 and seen[0]["arg"] == "open notepad" and seen[0]["text"] == 'go "$1"'
