import asyncio
import json
import sys

import pytest

from deck import buttons, config, screen, server, sessions, settings
from deck.api import access, pages
from deck.api import screen as screen_api


@pytest.fixture
async def client(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
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
    monkeypatch.setattr(settings, "hosts", lambda: {"hosts": [{"name": "desk", "url": "https://desk.example", "current": False}]})
    assert (await client.get("/peer/desk/vnc/websockify")).status == 404  # not a WebSocket: nothing proxied
    assert (await client.get("/peer/desk/static/app.js")).status == 404


async def test_screen_over_iroh_shows_only_when_deck_serves_the_bridge(client, monkeypatch, tmp_path):
    # Optional features off until set up (user): no empty Screen tab where the stream can't come through.
    vnc = tmp_path / "vnc.json"
    monkeypatch.setattr(config, "VNC_FILE", str(vnc))
    assert (await (await client.get("/api/screen")).json())["enabled"] is False
    vnc.write_text('{"password": "p", "path": "/vnc/websockify"}')
    assert (await (await client.get("/api/screen")).json())["enabled"] is True
    monkeypatch.setattr(access, "via_tunnel", lambda r: True)
    monkeypatch.setattr(access, "is_local", lambda r: True)  # past the pairing check: only the tab's answer is tested
    assert (await (await client.get("/api/screen")).json())["enabled"] is False
    vnc.write_text('{"password": "p", "path": "/vnc/websockify", "bridge": "127.0.0.1:6080"}')
    assert (await (await client.get("/api/screen")).json())["enabled"] is True
    assert (await client.get("/vnc/websockify")).status == 404  # not a WebSocket: nothing piped


async def test_hosts_carry_this_hosts_iroh_key(client, monkeypatch, tmp_path):
    from deck import links
    inv = tmp_path / "inventory.toml"
    inv.write_text('[hosts.desk]\ndeck_url = "https://desk.example"\n')
    monkeypatch.setattr(config, "INVENTORY", str(inv))
    monkeypatch.setattr(config, "HOST_NAME", "desk")
    monkeypatch.setattr(links, "iroh_id", lambda: "K1")
    assert (await (await client.get("/api/hosts")).json())["hosts"][0]["iroh"] == "K1"


async def test_a_run_passes_its_one_off_argument(client, monkeypatch):
    seen = []
    monkeypatch.setattr(buttons, "load_buttons", lambda: [buttons.clean_button({"id": "cu", "label": "cu", "type": "shell", "text": 'go "$1"'})])
    monkeypatch.setattr(sessions, "start_run", lambda b: seen.append(b) or "s1")
    r = await client.post("/api/run/cu", json={"arg": "open notepad"})
    assert r.status == 200 and seen[0]["arg"] == "open notepad" and seen[0]["text"] == 'go "$1"'


async def test_phone_screen_detaches_without_a_streaming_page(client, monkeypatch):
    # User: "disable the virtual screens whenever not streaming". Keep-alives hold it; silence detaches it.
    calls = []
    monkeypatch.setattr(screen, "phone_screen", lambda action="status", w=0, h=0: calls.append(action) or {"on": action == "on"})
    monkeypatch.setattr(screen_api, "PHONE_IDLE", -1)
    app = client.server.app
    assert (await client.post("/api/screen/phone", json={"on": True, "w": 1080, "h": 1620})).status == 200
    assert app["phonestate"]["seen"] is not None
    assert (await client.post("/api/screen/phone", json={"keep": True})).status == 200
    sleeps = 0

    async def one_tick(_):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise asyncio.CancelledError
    monkeypatch.setattr(asyncio, "sleep", one_tick)
    monkeypatch.setattr(screen, "phone_screen", lambda action="status", w=0, h=0: calls.append(action) or {"on": True})
    loop_app = {"phonestate": {"seen": None}}
    with pytest.raises(asyncio.CancelledError):
        await screen_api.phone_idle_loop(loop_app)  # starts seeing it on, then (idle -1 s) detaches it
    assert calls[-1] == "off" and loop_app["phonestate"]["seen"] is None


async def test_stamp_names_the_device_behind_a_trusted_tailnet_address(monkeypatch):
    # DECK_LOCAL_IPS may list the user's other devices too (desk, laptop, phone): the stamp says which one wrote,
    # and "this PC" only for this host's own browser (user 2026-10-26: "the stamp is wrong").
    from aiohttp.test_utils import make_mocked_request
    monkeypatch.setenv("DECK_LOCAL_IPS", "100.1.1.1,100.2.2.2")
    monkeypatch.setattr(config, "HOST_NAME", "host-a")
    monkeypatch.setattr(access, "via_tunnel", lambda r: False)
    monkeypatch.setattr(access, "tailnet_device", lambda ip: {"100.1.1.1": "host-a", "100.2.2.2": "host-b"}.get(ip, ""))

    def req(**headers):
        r = make_mocked_request("POST", "/api/x", headers=headers)
        r._transport_peername = ("127.0.0.1", 5)
        monkeypatch.setattr(type(r), "remote", property(lambda self: "127.0.0.1"), raising=False)
        return r
    assert access.sender(req(**{"X-Forwarded-For": "100.2.2.2"}), "PC browser") == "host-b, PC browser"
    assert access.sender(req(**{"X-Forwarded-For": "100.1.1.1"}), "PC browser") == "this PC, PC browser"
    assert access.sender(req(), "PC browser") == "this PC, PC browser"  # straight to 127.0.0.1, no proxy
    assert access.sender(req(**{"X-Forwarded-For": "100.9.9.9"}), "PC browser") == "PC browser"  # not trusted: no device


async def test_app_link_opens_the_apk_to_an_unpaired_browser_for_a_while(aiohttp_client, monkeypatch, tmp_path):
    # The phone app can't save downloads: a paired device asks for a link that the phone's browser may open for a while.
    monkeypatch.setattr(access, "is_local", lambda r: False)  # an unpaired browser over the tunnel
    monkeypatch.setattr(access, "via_tunnel", lambda r: True)
    apk = tmp_path / "static" / "deck.apk"
    apk.parent.mkdir()
    apk.write_bytes(b"apk")
    monkeypatch.setattr(pages, "STATIC", apk.parent)  # before make_app: the static route reads it then
    client = await aiohttp_client(server.make_app())
    assert (await client.get("/static/deck.apk")).status == 401
    assert (await client.get("/static/deck.apk?t=guess")).status == 401
    assert (await client.post("/api/app/link")).status == 401
    monkeypatch.setattr(access, "is_local", lambda r: True)  # a paired device (or this host's browser) asks
    link = await (await client.post("/api/app/link")).json()
    assert link["url"].startswith("http://") and "/static/deck.apk?t=" in link["url"] and link["expires"] == 600
    monkeypatch.setattr(access, "is_local", lambda r: False)
    token = link["url"].split("?t=")[1]
    assert (await client.get(f"/static/deck.apk?t={token}")).status == 200
    assert (await client.get(f"/static/deck.apk?t={token}")).status == 200  # not one-use: an older app may swallow the first open
    assert token not in str(pages._app_links)  # the host keeps hashes only
    for h in pages._app_links:
        pages._app_links[h] = 0  # expired
    assert (await client.get(f"/static/deck.apk?t={token}")).status == 401
    assert not pages._app_links


async def test_vnc_server_stops_once_nobody_streams(monkeypatch):
    # The VNC server runs only while a page streams (user: "turn off the vnc when i am not streaming screen").
    calls = []
    monkeypatch.setattr(screen, "vnc_bridge", lambda: "127.0.0.1:6080")
    monkeypatch.setattr(screen, "vnc_service", lambda state: calls.append(state) or True)
    monkeypatch.setattr(screen_api, "VNC_IDLE", 0)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))
    app = {"vncstate": {"streams": 1, "idle": 0.0}}
    loop = asyncio.ensure_future(screen_api.vnc_idle_loop(app))
    await real_sleep(0.01)
    assert calls == []  # a stream is open
    app["vncstate"]["streams"] = 0
    await real_sleep(0.01)
    loop.cancel()
    assert calls == ["off"]  # stopped once, not every pass


def test_vnc_json_hooks_start_and_stop_the_vnc_server(tmp_path, monkeypatch):
    # A Linux host has no "Deck VNC on/off" tasks: vnc.json's vnc_on/vnc_off commands start and stop its VNC server.
    out = tmp_path / "log"
    cfg = tmp_path / "vnc.json"
    cfg.write_text(json.dumps({"password": "x", "bridge": "127.0.0.1:6080",
                               "vnc_on": [sys.executable, "-c", f"open({str(out)!r}, 'a').write('on ')"],
                               "vnc_off": [sys.executable, "-c", f"open({str(out)!r}, 'a').write('off')"]}))
    monkeypatch.setattr(config, "VNC_FILE", str(cfg))
    assert screen.vnc_service("on") is True
    assert screen.vnc_service("off") is True
    assert out.read_text() == "on off"


def test_vnc_hook_must_be_an_argument_list(tmp_path, monkeypatch):
    # A string is never handed to a shell: without a usable hook it falls back to the Windows tasks (none here).
    cfg = tmp_path / "vnc.json"
    cfg.write_text(json.dumps({"vnc_on": "touch /tmp/should-not-run"}))
    monkeypatch.setattr(config, "VNC_FILE", str(cfg))
    monkeypatch.setattr(screen, "SCHTASKS", str(tmp_path / "no-schtasks.exe"))
    monkeypatch.setattr(config, "WINDOWS", False)
    assert screen.vnc_service("on") is False
