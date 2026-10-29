"""The API package as a whole: every route module registers, and the page's parts are served joined in order."""
import re

import pytest

from deck import config, server
from deck.api import ROUTE_MODULES, pages


@pytest.fixture
async def client(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
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
