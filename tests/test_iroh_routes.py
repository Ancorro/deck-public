"""The inventory's hosts over iroh (deck/links.py routes, settings.hosts): a host with an `iroh` key is reached through
its forwarder while that answers, else its deck_url; browsers and the phone app always get the deck_url (user
2026-11-08: "move off the ts deck? to iroh")."""
import os
import subprocess
import sys
import time

import pytest

from deck import config, links, settings

KX, KY = "a" * 64, "b" * 64


@pytest.fixture
def inv(tmp_path, monkeypatch):
    p = tmp_path / "inventory.toml"
    p.write_text(f'[hosts.me]\ndeck_url = "https://me.x.ts.net"\niroh = "{"c" * 64}"\n'
                 f'[hosts.x]\ndeck_url = "https://x.x.ts.net"\niroh = "{KX}"\n'
                 f'[hosts.y]\ndeck_url = "https://y.x.ts.net"\niroh = "not-a-key"\n'
                 f'[hosts.z]\ndeck_url = "http://10.0.0.5:8787"\n')
    monkeypatch.setattr(config, "INVENTORY", str(p))
    monkeypatch.setattr(config, "HOST_NAME", "me")
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(links, "_fwd", {})
    monkeypatch.setattr(links, "_route", {})
    return p


def by_name():
    return {h["name"]: h for h in settings.hosts()["hosts"]}


def test_only_other_hosts_with_a_real_key_are_routed(inv):
    assert links.inventory_keys() == {"x": KX}


def test_a_host_goes_over_iroh_after_answers_in_a_row_and_back_at_the_first_miss(inv):
    links._fwd[KX] = {"url": "http://127.0.0.1:20001", "proc": None}
    h = by_name()
    assert (h["x"]["url"], h["x"]["via"], h["x"]["iroh"]) == ("https://x.x.ts.net", "tailscale", KX)
    for _ in range(links.ROUTE_OK - 1):
        links.record(KX, True)
    assert by_name()["x"]["via"] == "tailscale"  # not yet: one answer could be luck
    links.record(KX, True)
    h = by_name()
    assert (h["x"]["url"], h["x"]["via"], h["x"]["web"]) == ("http://127.0.0.1:20001", "iroh", "https://x.x.ts.net")
    links.record(KX, False)  # one miss: back to its address at once
    assert by_name()["x"]["url"] == "https://x.x.ts.net"


def test_the_switch_back_beats_the_sites_takeover():
    """A dead route must be off within the time a follower waits before it takes over as writer (deck/site.py), or two
    nodes would write: one wait between checks plus one timed-out check."""
    from deck import site
    assert links.ROUTE_POLL * 2 < site.POLL * site.MISSES / 2


def test_hosts_without_a_key_and_this_host_keep_their_address(inv):
    h = by_name()
    assert h["z"]["url"] == "http://10.0.0.5:8787" and h["z"]["via"] == "address" and "iroh" not in h["z"]
    assert h["y"]["via"] == "tailscale" and "iroh" not in h["y"]
    assert "via" not in h["me"] and h["me"]["url"] == "https://me.x.ts.net"


def test_the_page_and_phone_get_the_hosts_own_address():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    start = (root / "deck/static/app/14-start.js").read_text(encoding="utf-8")
    assert "const web = x.web || x.url;" in start and "new URL(web).origin" in start
    site_api = (root / "deck/api/site.py").read_text(encoding="utf-8")
    assert '"writer_url": w.get("web") or w.get("url", "")' in site_api


async def test_a_failed_check_counts_as_a_miss(inv, aiohttp_client):
    import aiohttp
    from aiohttp import web

    from deck import auth

    async def role(_):
        return web.json_response({"role": "writer"})
    app = web.Application()
    app.router.add_get("/api/site/role", role)
    c = await aiohttp_client(app)
    auth.ensure_host_key()
    links._fwd[KX] = {"url": str(c.make_url("")).rstrip("/"), "proc": None}
    async with aiohttp.ClientSession() as http:
        for _ in range(links.ROUTE_OK):
            await links.check(http, "x", KX)
        assert by_name()["x"]["via"] == "iroh"
        links._fwd[KX]["url"] = "http://127.0.0.1:9"  # nothing listens: refused
        await links.check(http, "x", KX)
    assert by_name()["x"]["via"] == "tailscale"


@pytest.mark.skipif(not (sys.platform == "win32" or sys.platform.startswith("linux")), reason="Windows and Linux only")
def test_a_forwarder_ends_when_its_server_is_killed(tmp_path):
    """The server is killed with no cleanup (taskkill /F, SIGKILL): its forwarder must not keep running."""
    child = tmp_path / "child.pid"
    code = f"""
import asyncio, sys
sys.path.insert(0, {str(config.REPO)!r})
from deck import config, links
async def main():
    p = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(60)",
                                             preexec_fn=links._die_with_parent())
    if config.WINDOWS:
        links._end_with_me(p.pid)
    open({str(child)!r}, "w").write(str(p.pid))
    await asyncio.sleep(60)
asyncio.run(main())
"""
    server = subprocess.Popen([sys.executable, "-c", code])
    try:
        for _ in range(100):
            if child.exists() and child.read_text():
                break
            time.sleep(0.1)
        pid = int(child.read_text())
        assert alive(pid)
        server.kill()
        server.wait()
        for _ in range(50):
            if not alive(pid):
                break
            time.sleep(0.1)
        assert not alive(pid), "the forwarder outlived its server"
    finally:
        server.kill()


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        return f'"{pid}"' in out.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return open(f"/proc/{pid}/stat").read().split()[2] != "Z"
