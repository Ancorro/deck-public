"""One site on every node (deck/site.py): the journal between a writer and a follower, the order, snapshots, and
what happens when a lower node wrote while the higher one was away."""
import json

import pytest

from deck import core, site


@pytest.fixture
def nodes(tmp_path, monkeypatch):
    """Two data folders; `on(name)` switches which one deck.core and deck.site see, with its own site state."""
    dirs = {n: tmp_path / n for n in ("a", "b")}
    states = {n: {"role": "follower", "seq": 0, "since": 0.0, "misses": {}, "last_seen": {}} for n in dirs}
    for d in dirs.values():
        (d / "chat").mkdir(parents=True)
    monkeypatch.setattr(core, "hosts", lambda: {"hosts": [{"name": "a", "url": "http://a", "current": core.HOST_NAME == "a"},
                                                          {"name": "b", "url": "http://b", "current": core.HOST_NAME == "b"}]})

    monkeypatch.setattr(site, "running", lambda: set())  # no tmux here: no session of this node's own, unless a test says

    def on(name):
        monkeypatch.setattr(core, "DATA", dirs[name])
        monkeypatch.setattr(core, "HOST_NAME", name)
        monkeypatch.setattr(core, "load_settings", lambda: json.loads((dirs[name] / "settings.json").read_text()) if (dirs[name] / "settings.json").exists() else {})
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


def test_chat_appends_replay_exactly_and_a_gap_is_skipped_until_a_whole_send(nodes):
    a = nodes("a")
    f = a / "chat" / "s1.jsonl"
    f.write_bytes(b'{"t":"user"}\n')
    e1 = site.record("chat/s1.jsonl", b'{"t":"user"}\n', "a", 0)
    with open(f, "ab") as fh:
        fh.write(b'{"t":"msg"}\n')
    e2 = site.record("chat/s1.jsonl", b'{"t":"msg"}\n', "a", len(b'{"t":"user"}\n'))
    e3 = site.record("chat/s1.jsonl", f.read_bytes(), "w")  # a restart rewrote it: whole
    b = nodes("b")
    site.apply(e1)
    site.apply(e2)
    assert (b / "chat" / "s1.jsonl").read_bytes() == b'{"t":"user"}\n{"t":"msg"}\n'
    # a follower that missed e2 cannot place e3's predecessor: it skips the tail, and the next whole send repairs it
    nodes("a")
    e4 = site.record("chat/s1.jsonl", b'{"t":"late"}\n', "a", 999)
    nodes("b")
    assert site.apply(e4) and "chat/s1.jsonl" in site._state["broken"]
    assert site.apply(e3) is False  # e3's seq is below e4's: already past it
    e5 = {**e3, "seq": 5}
    assert site.apply(e5) and "chat/s1.jsonl" not in site._state["broken"]


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


def test_snapshot_restores_shared_files_and_the_seq(nodes):
    a = nodes("a")
    (a / "shelf.json").write_text("[1]")
    (a / "shelf-files").mkdir()
    (a / "shelf-files" / "x.txt").write_text("doc")
    (a / "chat" / "s1.jsonl").write_text('{"t":"user"}\n')
    (a / "env").write_text("SECRET=1")
    site.record("shelf.json", b"[1]")
    snap = site.snapshot()
    b = nodes("b")
    (b / "settings.json").write_text(json.dumps({"led": "#222222"}))
    assert site.restore(snap) == 1 and site.seq() == 1
    assert (b / "shelf.json").read_text() == "[1]" and (b / "shelf-files" / "x.txt").read_text() == "doc"
    assert (b / "chat" / "s1.jsonl").read_text() == '{"t":"user"}\n'
    assert not (b / "env").exists()  # per-node files never travel
    assert json.loads((b / "settings.json").read_text())["led"] == "#222222"


def test_replay_rejournals_what_a_lower_node_wrote_while_the_writer_was_away(nodes):
    b = nodes("b")
    site.become_writer()
    (b / "chat" / "s9.jsonl").write_bytes(b'{"t":"user"}\n')
    e = site.record("chat/s9.jsonl", b'{"t":"user"}\n', "a", 0)
    (b / "shelf.json").write_text("[2]")
    e2 = site.record("shelf.json", b"[2]")
    a = nodes("a")
    site.become_writer()
    (a / "shelf.json").write_text("[1]")
    assert site.replay([e, e2]) == 2
    assert (a / "chat" / "s9.jsonl").read_bytes() == b'{"t":"user"}\n' and (a / "shelf.json").read_text() == "[2]"
    assert [x["rel"] for x in site.entries_after(0)] == ["chat/s9.jsonl", "shelf.json"]  # journaled as a's own


def test_writer_and_follower_roles(nodes):
    nodes("a")
    assert site.role() == "follower" and site.writer() is None
    site.become_writer()
    assert site.role() == "writer" and site.writer()["name"] == "a" and site.status()["ready"]
    site.become_follower("b")
    assert site.role() == "follower" and site.status()["ready"] is False


def test_a_follower_offers_its_own_chats_and_the_writer_journals_them(nodes, monkeypatch):
    b = nodes("b")
    f = b / "chat" / "deck-claude-1.jsonl"
    f.write_bytes(b'{"t":"user"}\n')
    monkeypatch.setattr(site, "running", lambda: {"deck-claude-1"})
    first = site.offers(True)
    assert [(e["rel"], e["mode"]) for e in first] == [("chat/deck-claude-1.jsonl", "w")]
    with open(f, "ab") as fh:
        fh.write(b'{"t":"msg"}\n')
    tail = site.offers()
    assert [(e["rel"], e["mode"], e["offset"]) for e in tail] == [("chat/deck-claude-1.jsonl", "a", len(b'{"t":"user"}\n'))]
    assert site.offers() == []  # nothing new
    a = nodes("a")
    monkeypatch.setattr(site, "running", lambda: set())
    site.become_writer()
    assert site.take_offer(first) == {"taken": 1, "whole": []}
    assert site.take_offer(tail) == {"taken": 1, "whole": []}
    assert (a / "chat" / "deck-claude-1.jsonl").read_bytes() == b'{"t":"user"}\n{"t":"msg"}\n'
    assert [(e["rel"], e["mode"]) for e in site.entries_after(0)] == [("chat/deck-claude-1.jsonl", "w"), ("chat/deck-claude-1.jsonl", "a")]
    assert site.scan() == 0  # taken in already: not journaled a second time
    # a tail that does not fit the writer's copy is refused and asked for whole
    late = {**tail[0], "offset": 999}
    assert site.take_offer([late]) == {"taken": 0, "whole": ["chat/deck-claude-1.jsonl"]}


def test_the_journal_never_overwrites_a_chat_this_node_is_writing(nodes, monkeypatch):
    a = nodes("a")
    (a / "chat" / "deck-claude-2.jsonl").write_bytes(b"old\n")
    e = site.record("chat/deck-claude-2.jsonl", b"old\n", "w")
    b = nodes("b")
    live = b / "chat" / "deck-claude-2.jsonl"
    live.write_bytes(b"mine\n")
    monkeypatch.setattr(site, "running", lambda: {"deck-claude-2"})
    assert site.apply(e) and site.seq() == 1
    assert live.read_bytes() == b"mine\n"  # the bridge here is the truth
    nodes("a")
    monkeypatch.setattr(site, "running", lambda: set())
    snap = site.snapshot()
    nodes("b")
    monkeypatch.setattr(site, "running", lambda: {"deck-claude-2"})
    site.restore(snap)
    assert live.read_bytes() == b"mine\n"


def test_nodes_include_linked_pcs_whose_forwarder_is_down(nodes, monkeypatch):
    from deck import links
    nodes("a")
    monkeypatch.setattr(links, "members", lambda: [{"id": "c", "name": "c", "iroh": "K", "hosting": True},
                                                   {"id": "d", "name": "d", "iroh": "K2", "hosting": False}])
    assert site.nodes() == ["a", "b", "c"]  # c: linked, away (no forwarder); d: client only, never a node
    assert site.order() == ["a", "b", "c"]
