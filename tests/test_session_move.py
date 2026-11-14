import io
import json
import tarfile

import pytest

from deck import config, session_move, sessions, settings


def chat(monkeypatch, tmp_path, kind, folder, state="your_turn"):
    monkeypatch.setattr(config, "HOME", tmp_path / "src")
    monkeypatch.setattr(config, "HOST_NAME", "pc-a")
    monkeypatch.setattr(sessions, "is_chat", lambda name: True)
    monkeypatch.setattr(sessions, "chat_state", lambda name: {"state": state, "resume": "conv-9", "prefs": {"model": "opus"}, "name": "Fix tabs"})
    monkeypatch.setattr(sessions, "session_meta", lambda name: {"agent": kind, "label": kind.title(), "folder": folder})
    monkeypatch.setattr(sessions, "tmux", lambda *a: type("R", (), {"stdout": "claude --dangerously-skip-permissions"})())


def arrive(monkeypatch, tmp_path, body, folders=("work",)):
    """Receive on the other host: its own home, with the folders it has."""
    dst = tmp_path / "dst"
    for f in folders:
        (dst / f).mkdir(parents=True)
    monkeypatch.setattr(config, "HOME", dst)
    monkeypatch.setattr(config, "HOST_NAME", "pc-b")
    monkeypatch.setattr(settings, "default_folder", lambda: str(dst / "work"))
    started = []
    monkeypatch.setattr(sessions, "start_run", lambda b, state=None: started.append((b, state)) or "deck-claude-2")
    return dst, started, session_move.receive(body)


def test_refuses_a_working_chat_and_bad_packages(tmp_path, monkeypatch):
    chat(monkeypatch, tmp_path, "claude", "/w", state="working")
    with pytest.raises(ValueError, match="finish"):
        session_move.pack("s")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:  # a path escaping its folder is skipped
        for name, data in (("item.json", json.dumps({"type": "claude", "state": {"resume": "c1"}}).encode()),
                           ("files/../../evil.jsonl", b"x"), ("files/c1/../../c1.jsonl", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="no conversation"):
        arrive(monkeypatch, tmp_path, buf.getvalue())
    assert not (tmp_path / "evil.jsonl").exists()
    with pytest.raises(ValueError):
        session_move.receive(b"not a tar")
