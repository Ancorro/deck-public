"""One site on every node (deck/site.py): the journal between a writer and a follower, the order, snapshots, and
what happens when a lower node wrote while the higher one was away."""
import json

import pytest

from deck import config, settings, site


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    """Two data folders; `on(name)` switches which one deck.config and deck.site see, with its own site state."""
    dirs = {n: tmp_path / n for n in ("a", "b")}
    states = {n: {"role": "follower", "seq": 0, "since": 0.0, "misses": {}, "last_seen": {}} for n in dirs}
    for d in dirs.values():
        (d / "chat").mkdir(parents=True)
    monkeypatch.setattr(settings, "hosts", lambda: {"hosts": [{"name": "a", "url": "http://a", "current": config.HOST_NAME == "a"},
                                                          {"name": "b", "url": "http://b", "current": config.HOST_NAME == "b"}]})

    def on(name):
        monkeypatch.setattr(config, "DATA", dirs[name])
        monkeypatch.setattr(config, "HOST_NAME", name)
        monkeypatch.setattr(settings, "load_settings", lambda: json.loads((dirs[name] / "settings.json").read_text()) if (dirs[name] / "settings.json").exists() else {})
        monkeypatch.setattr(site, "_state", states[name])
        return dirs[name]
    return on


def test_order_is_the_setting_then_the_inventory(nodes):
    d = nodes("b")
    assert site.order() == ["a", "b"] and site.above() == ["a"]
    (d / "settings.json").write_text(json.dumps({"order": ["b"]}))
    assert site.order() == ["b", "a"] and site.above() == []
    nodes("a")
    assert site.above() == []  # no setting on a: inventory order, a first


def test_whole_file_writes_travel_and_per_node_settings_stay(nodes):
    a = nodes("a")
    (a / "buttons.json").write_bytes(b'[{"id": "k1"}]')
    e1 = site.record("buttons.json", b'[{"id": "k1"}]')
    (a / "settings.json").write_text(json.dumps({"alias": "Deck", "led": "#111111", "folder": "/a"}))
    e2 = site.record("settings.json", (a / "settings.json").read_bytes())
    assert site.record("env", b"x") is None  # not shared
    b = nodes("b")
    (b / "settings.json").write_text(json.dumps({"led": "#222222", "folder": "/b"}))
    assert site.apply(e1) and site.apply(e2)
    assert json.loads((b / "buttons.json").read_text()) == [{"id": "k1"}]
    s = json.loads((b / "settings.json").read_text())
    assert s["alias"] == "Deck" and s["led"] == "#222222" and s["folder"] == "/b"  # per-node fields kept
    assert site.seq() == 2
    assert not site.apply(e1)  # old: not applied twice


def test_chats_stay_on_their_node(nodes):
    """Chats are not copied (user 2026-11-04): a chat file is never journaled, applied, snapshotted or replayed, so a
    chat's output does not move the journal (which made every open page reload the shared state)."""
    a = nodes("a")
    site.become_writer()
    site.scan(True)
    (a / "chat" / "s1.jsonl").write_text('{"t":"user"}\n')
    assert site.scan() == 0 and site.seq() == 0 and site.record("chat/s1.jsonl", b"x\n", "a", 0) is None
    old = {"seq": 1, "rel": "chat/s1.jsonl", "mode": "a", "offset": 0, "size": 2, "data": "eAo=", "at": 0}
    b = nodes("b")
    assert site.apply(old) and site.seq() == 1 and not (b / "chat" / "s1.jsonl").exists()  # an older writer's entry
    nodes("a")
    snap = site.snapshot()
    nodes("b")
    site.restore(snap)
    assert not (b / "chat" / "s1.jsonl").exists()
    a = nodes("a")
    assert site.replay([old]) == 0 and site.seq() == 0


def test_journal_after_and_too_far_behind(nodes):
    nodes("a")
    for i in range(5):
        site.record("buttons.json", json.dumps([{"id": f"k{i}"}]).encode())
    assert [e["seq"] for e in site.entries_after(2)] == [3, 4, 5]
    assert site.entries_after(5) == []
    site.KEEP = 3
    site.trim()
    assert [e["seq"] for e in site.entries_after(3)] == [4, 5]
    assert site.entries_after(1) is None  # before what is kept: snapshot
    site.KEEP = 20000
    site.KEEP_BYTES = 1  # over the byte cap: only the newest entry stays
    site.trim()
    assert [e["seq"] for e in site.entries_after(4)] == [5]
    site.KEEP_BYTES = 32 << 20


def test_snapshot_restores_shared_files_and_the_seq(nodes):
    a = nodes("a")
    (a / "shelf.json").write_text("[1]")
    (a / "shelf-files").mkdir()
    (a / "shelf-files" / "x.txt").write_text("doc")
    (a / "env").write_text("SECRET=1")
    site.record("shelf.json", b"[1]")
    snap = site.snapshot()
    b = nodes("b")
    (b / "settings.json").write_text(json.dumps({"led": "#222222"}))
    assert site.restore(snap) == 1 and site.seq() == 1
    assert (b / "shelf.json").read_text() == "[1]" and (b / "shelf-files" / "x.txt").read_text() == "doc"
    assert not (b / "env").exists()  # per-node files never travel
    assert json.loads((b / "settings.json").read_text())["led"] == "#222222"


def test_replay_rejournals_what_a_lower_node_wrote_while_the_writer_was_away(nodes):
    b = nodes("b")
    site.become_writer()
    (b / "buttons.json").write_text("[9]")
    e = site.record("buttons.json", b"[9]")
    (b / "shelf.json").write_text("[2]")
    e2 = site.record("shelf.json", b"[2]")
    a = nodes("a")
    site.become_writer()
    (a / "shelf.json").write_text("[1]")
    assert site.replay([e, e2]) == 2
    assert (a / "buttons.json").read_text() == "[9]" and (a / "shelf.json").read_text() == "[2]"
    assert [x["rel"] for x in site.entries_after(0)] == ["buttons.json", "shelf.json"]  # journaled as a's own


def test_writer_and_follower_roles(nodes):
    nodes("a")
    assert site.role() == "follower" and site.writer() is None
    site.become_writer()
    assert site.role() == "writer" and site.writer()["name"] == "a" and site.status()["ready"]
    site.become_follower("b")
    assert site.role() == "follower" and site.status()["ready"] is False


def test_nodes_include_linked_pcs_whose_forwarder_is_down(nodes, monkeypatch):
    from deck import links
    nodes("a")
    monkeypatch.setattr(links, "members", lambda: [{"id": "c", "name": "c", "iroh": "K", "hosting": True},
                                                   {"id": "d", "name": "d", "iroh": "K2", "hosting": False}])
    assert site.nodes() == ["a", "b", "c"]  # c: linked, away (no forwarder); d: client only, never a node
    assert site.order() == ["a", "b", "c"]


def test_a_file_swap_blocked_for_a_moment_is_retried(tmp_path, monkeypatch):
    # Windows refuses os.replace while another process has the file open; the follower's settings.json write was
    # dropped then, and that host kept the old look and theme until the next change.
    real, calls = site.os.replace, []

    def flaky(a, b):
        calls.append(a)
        if len(calls) < 3:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real(a, b)
    monkeypatch.setattr(site.os, "replace", flaky)
    monkeypatch.setattr(site.time, "sleep", lambda s: None)
    tmp, p = tmp_path / "settings.json.sitetmp", tmp_path / "settings.json"
    tmp.write_text('{"look": "zine"}')
    site._replace(tmp, p)
    assert len(calls) == 3 and json.loads(p.read_text()) == {"look": "zine"} and not tmp.exists()


def test_a_file_swap_blocked_for_good_still_fails(tmp_path, monkeypatch):
    def stuck(a, b):
        raise PermissionError(13, "in use")
    monkeypatch.setattr(site.os, "replace", stuck)
    monkeypatch.setattr(site.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        site._replace(tmp_path / "a", tmp_path / "b")


def test_an_empty_settings_copy_never_wipes_a_follower_and_the_order_stays(nodes):
    """user 2026-10-30: "settings reset on build": the scan read settings.json mid-write as empty, every follower took
    that, lost the node order, and after a restart served its own stale copy as a second writer."""
    a = nodes("a")
    site.become_writer()
    (a / "settings.json").write_text("")
    site._seen.pop("settings.json", None)
    assert site.scan() == 0 or all(e["rel"] != "settings.json" for e in site.entries_after(0))  # empty: not journaled
    empty = {**site.record("buttons.json", b"[]"), "rel": "settings.json", "data": "", "size": 0}  # as an older writer sent
    (a / "settings.json").write_text(json.dumps({"look": "plain"}))  # no order in it
    e = site.record("settings.json", (a / "settings.json").read_bytes())
    b = nodes("b")
    (b / "settings.json").write_text(json.dumps({"look": "zine", "order": ["a", "b"], "theme": "dark"}))
    assert site.apply(empty)
    assert json.loads((b / "settings.json").read_text())["theme"] == "dark"  # untouched
    assert site.apply(e)
    s = json.loads((b / "settings.json").read_text())
    assert s["look"] == "plain" and s["order"] == ["a", "b"]


def test_a_node_with_no_order_takes_the_writers(nodes, monkeypatch):
    saved = {}
    monkeypatch.setattr(settings, "save_settings", lambda raw: saved.update(raw))
    nodes("b")
    assert not site.adopt_order({"role": "follower", "saved": True, "order": ["b", "a"]})  # only a writer's
    assert not site.adopt_order({"role": "writer", "saved": False, "order": ["b", "a"]})  # only one it has set
    assert site.adopt_order({"role": "writer", "saved": True, "order": ["b", "a"]}) and saved == {"order": ["b", "a"]}


def test_settings_are_written_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "settings_path", lambda: tmp_path / "settings.json")
    settings.save_settings({"alias": "Deck"})
    assert json.loads((tmp_path / "settings.json").read_text())["alias"] == "Deck"
    assert not (tmp_path / "settings.json.tmp").exists()


def test_a_cut_journal_line_is_skipped_not_fatal(nodes):
    """A line cut short (two writes at once, 2026-10-30) made entries_after raise on every tick: the node never handed
    back."""
    a = nodes("a")
    site.become_writer()
    site.record("buttons.json", b"[1]")
    with open(a / "journal.jsonl", "a", encoding="utf-8") as f:
        f.write('XN0J3MgZXhwZWN0ZWQ"}\n')
    site.record("buttons.json", b"[2]")
    assert [e["seq"] for e in site.entries_after(0)] == [1, 2]
    assert site._load_seq() == 2


def test_seq_survives_a_restart_when_the_last_entry_is_huge(nodes):
    """A writer restarted with a multi-megabyte last entry (a whole shelf.json) read only the last 4 KB, found no line
    start, and went back to seq 0; followers then looped on snapshots and never named a writer (2026-11-06)."""
    nodes("a")
    site.become_writer()
    site.record("buttons.json", b"[1]")
    site.record("shelf.json", b"x" * (3 << 20))
    assert site._load_seq() == 2
    with open(site.journal_path(), "a", encoding="utf-8") as f:
        f.write('XN0J3MgZXhwZWN0ZWQ"}\n')  # a cut line at the end is skipped too
    assert site._load_seq() == 2


def test_a_node_behind_the_journal_is_told_before_any_entry_is_parsed(nodes, monkeypatch):
    """An offline node at seq 0 made the writer parse 200 entries (whole shelf.json copies) every tick before learning
    it needed a snapshot: the writer's loop sat at 90% CPU and its pages took 12 s (2026-11-04)."""
    nodes("a")
    site.become_writer()
    for i in range(5):
        site.record("buttons.json", f"[{i}]".encode())
    monkeypatch.setattr(site, "KEEP", 2)
    site.trim()
    parsed = []
    real = site.json.loads
    monkeypatch.setattr(site.json, "loads", lambda b, *a, **k: parsed.append(1) or real(b, *a, **k))
    assert site.entries_after(0) is None
    assert parsed == []
    assert [e["seq"] for e in site.entries_after(3)] == [4, 5]


def test_a_follower_ahead_of_its_writer_takes_a_snapshot(nodes, monkeypatch):
    """A follower served while the writer was away and its seq ran past the writer's: pulling 'after ours' got nothing,
    so it kept its stale settings. It now takes the writer's copy whole."""
    import asyncio
    a = nodes("a")
    site.become_writer()
    (a / "settings.json").write_text(json.dumps({"yolo": True}))
    site.record("settings.json", (a / "settings.json").read_bytes())
    snap = site.snapshot()
    b = nodes("b")
    (b / "settings.json").write_text(json.dumps({"yolo": False}))
    site._state["seq"] = 999

    class R:
        def __init__(self, status, body):
            self.status, self.body = status, body
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self): return self.body
        async def read(self): return self.body

    class Http:
        def get(self, url, **kw):
            if url.endswith("/snapshot"):
                return R(200, snap)
            return R(200, {"entries": [], "more": False, "seq": 1})
    asyncio.run(site._pull(Http(), {"url": "http://a"}))
    assert json.loads((b / "settings.json").read_text())["yolo"] is True and site.seq() == 1
