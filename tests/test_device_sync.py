import os
import time

import pytest

from deck import auth, core, sync


@pytest.fixture
def hosts(tmp_path, monkeypatch):
    """Two hosts' data folders; `on(name)` switches which one deck.core sees."""
    dirs = {n: tmp_path / n for n in ("a", "b")}
    for d in dirs.values():
        d.mkdir()

    def on(name):
        monkeypatch.setattr(core, "DATA", dirs[name])
        sync._seen.clear()
    return on


def pair(name):
    code = auth.new_code("host")["code"]
    token = auth.claim(code, name)
    auth.decide(code, "host", True)
    return token


def bump(path):
    """mtime resolution differs by filesystem: make a write count as newer."""
    t = time.time() + 5
    os.utime(path, (t, t))


def hand_over(src, dst, on):
    on(src)
    sync.check()
    pkg = sync.package("devices")
    on(dst)
    sync.check()
    return sync.apply("devices", pkg["data"], pkg["version"], pkg["gone"])


def test_a_device_paired_on_one_host_works_on_the_other(hosts):
    hosts("b")
    sync.check()
    hosts("a")
    sync.check()
    token = pair("phone")
    bump(auth.store_path())
    assert hand_over("a", "b", hosts)
    dev = auth.device_for(token, touch=False)
    assert dev and dev["name"] == "phone" and not dev.get("pending")


def test_waiting_devices_and_codes_stay_on_their_host(hosts):
    hosts("a")
    sync.check()
    auth.claim(auth.new_code("host")["code"], "stranger")
    bump(auth.store_path())
    assert sync.package("devices")["data"] == []
    hosts("b")
    sync.check()
    with auth.store(write=False) as d:
        assert d["devices"] == [] and d["codes"] == []


def test_using_a_device_is_not_a_change_to_sync(hosts):
    hosts("a")
    sync.check()
    token = pair("phone")
    bump(auth.store_path())
    assert sync.check() == ["devices"]
    with auth.store() as d:
        d["devices"][0]["last_seen"] = int(time.time())
    t = time.time() + 10
    os.utime(auth.store_path(), (t, t))
    assert sync.check() == []
    assert auth.device_for(token, touch=False)


def test_removing_a_device_removes_it_everywhere(hosts):
    hosts("b")
    sync.check()
    hosts("a")
    sync.check()
    token = pair("phone")
    bump(auth.store_path())
    hand_over("a", "b", hosts)
    hosts("a")
    auth.remove(auth.device_for(token, touch=False)["id"])
    t = time.time() + 20
    os.utime(auth.store_path(), (t, t))
    hand_over("a", "b", hosts)
    assert auth.device_for(token, touch=False) is None
