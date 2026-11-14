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
