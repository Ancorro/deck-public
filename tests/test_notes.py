"""deck/notes.py: a peer that is down is skipped after one short probe, not tried by git."""
import socket
import time

from deck import notes


def test_down_peer_is_skipped_without_git(monkeypatch):
    """A sync tried a host that was off with git, up to three times, one peer after another (21 s each on Windows), so
    Claude's 30 s SessionStart hook ran out on every start (2026-11-11). Now one probe per web peer, all at once."""
    notes._probed.clear()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    live_port = srv.getsockname()[1]
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()  # nothing listens here: refused at once
    peers = [("live", f"http://127.0.0.1:{live_port}/notes.git"), ("dead", f"http://127.0.0.1:{dead_port}/notes.git"),
             ("origin", "origin")]
    monkeypatch.setattr(notes, "peers", lambda: peers)
    calls = []

    def fake_git(*args, timeout=notes.TIMEOUT):
        calls.append(args)
        return notes.subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(notes, "git", fake_git)
    assert notes.up(peers) == {"live": True, "dead": False, "origin": True}

    problems = notes.pull() + notes.push()
    assert problems == ["dead: unreachable", "dead: unreachable"]
    tried = " ".join(" ".join(a) for a in calls)
    assert f":{dead_port}/" not in tried  # git never ran for the dead peer
    assert f":{live_port}/" in tried and "origin" in tried
    srv.close()


def test_probe_is_short_and_cached(monkeypatch):
    """A host that doesn't answer costs PROBE seconds once; pull and push in the same sync reuse the answer."""
    notes._probed.clear()
    tries = []

    def slow(addr, timeout):
        tries.append(addr)
        time.sleep(0.05)
        raise OSError("timed out")
    monkeypatch.setattr(notes.socket, "create_connection", slow)
    ps = [(f"h{i}", f"https://h{i}.example:8787/notes.git") for i in range(4)]
    t = time.monotonic()
    assert notes.up(ps) == {f"h{i}": False for i in range(4)}
    assert time.monotonic() - t < 0.15  # all at once, not one after another
    assert notes.up(ps) == {f"h{i}": False for i in range(4)}
    assert len(tries) == 4  # the second call is answered from the cache


def test_untracked_files_do_not_stop_a_sync(monkeypatch, tmp_path):
    """An uncommitted new file in the notes (a session's script, a .claude/ worktree) counted as an uncommitted edit,
    so pulls skipped every merge and pushes stopped too (2026-11-19). Untracked files now pass both ways, on this host
    and on the peer; an edit to a tracked file still stops the merge."""
    def g(repo, *args):
        r = notes.subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                                 capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    def commit(repo, name):
        (repo / name).write_text(name + "\n")
        g(repo, "add", name)
        g(repo, "commit", "-q", "-m", name)

    peer, here = tmp_path / "peer", tmp_path / "here"
    peer.mkdir()
    g(peer, "init", "-q", "-b", "main")
    g(peer, "config", "receive.denyCurrentBranch", "updateInstead")
    commit(peer, "a.md")
    g(tmp_path, "clone", "-q", str(peer), str(here))
    monkeypatch.setattr(notes.config, "NOTES", here)
    monkeypatch.setattr(notes, "peers", lambda: [("peer", str(peer))])
    monkeypatch.setattr(notes, "host_auth", lambda url: [])

    (here / "stray.py").write_text("work in progress\n")
    (peer / "peer-stray.txt").write_text("theirs\n")
    commit(peer, "b.md")
    assert notes.clean()
    assert notes.pull() == []
    assert (here / "b.md").exists() and (here / "stray.py").exists()

    commit(here, "c.md")
    assert notes.push() == []
    assert (peer / "c.md").exists() and (peer / "peer-stray.txt").exists()

    (here / "a.md").write_text("edited\n")
    commit(peer, "d.md")
    assert not notes.clean()
    assert notes.pull() == ["peer: local notes have uncommitted edits; not merged"]


def test_stuck_notes_post_and_clear_a_board_alert(monkeypatch, tmp_path):
    """A sync that skipped the merge for uncommitted edits only printed to stderr, which the SessionStart hook hides,
    so a host read 7-commit-old notes for two days (2026-11-13 to 11-15). It posts a Board alert, and clears it after."""
    monkeypatch.setattr(notes.config, "DATA", tmp_path)
    monkeypatch.setattr(notes, "git", lambda *a, timeout=notes.TIMEOUT:
                        notes.subprocess.CompletedProcess(a, 0, " M bin/x.sh\n?? stray/\n", ""))
    calls = []

    def fake_board(method, path, body=None):
        calls.append((method, path, body))
        return {"id": "card1"} if method == "POST" else {"ok": True}
    monkeypatch.setattr(notes, "_board", fake_board)

    notes.alert(["laptop: unreachable"])  # down peers are normal: no card
    assert calls == []
    notes.alert(["desk: local notes have uncommitted edits; not merged"])
    method, path, body = calls[-1]
    assert (method, path, body["kind"]) == ("POST", "/api/board", "alert")
    assert body["key"] == "notes-stuck-" + notes.config.HOST_NAME
    assert "bin/x.sh" in body["text"] and "stray/" in body["text"]
    notes.alert([])  # merged again: the card goes
    assert calls[-1][:2] == ("DELETE", "/api/board/card1")
    assert not (tmp_path / "notes-alert.json").exists()
    notes.alert([])  # nothing left to remove
    assert len(calls) == 2
