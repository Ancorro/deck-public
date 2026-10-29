from deck import config
from deck.api.chat import chat_media_file, chat_media_headers, chat_resume_at


def test_chat_files(tmp_path, monkeypatch):
    """A page that drew n events of the same log carries on after them; another log (a restart) or a short one: all of it.
    A reply's files come from the chat's own media folder only, and none can run as a page of Deck's."""
    log = tmp_path / "s.jsonl"
    lines = [b'{"t": "clear", "log": 7}\n', b'{"t": "user", "text": "hi"}\n', b'{"t": "state", "s": "working"}\n']
    log.write_bytes(b"".join(lines))
    assert chat_resume_at(log, "7", "2") == len(lines[0]) + len(lines[1])
    assert chat_resume_at(log, "7", "3") == len(b"".join(lines))
    assert chat_resume_at(log, "8", "2") == 0
    assert chat_resume_at(log, "7", "9") == 0
    assert chat_resume_at(log, "", "2") == 0

    monkeypatch.setattr(config, "CHAT_DIR", tmp_path)
    (tmp_path / "s.media").mkdir()
    for f in ("a1.png", "page.html", "x.svg"):
        (tmp_path / "s.media" / f).write_bytes(b"x")
    (tmp_path / "secret.png").write_bytes(b"x")
    assert chat_media_file("s", "a1.png") == tmp_path / "s.media" / "a1.png"
    for name, f in (("s", "../secret.png"), ("s", "..png"), ("s", ".png"), ("..", "s.media"), ("s/..", "x"), ("s", "nope.png")):
        assert chat_media_file(name, f) is None, (name, f)
    png, html, svg = (chat_media_headers(f) for f in ("a1.png", "page.html", "x.svg"))
    assert png["Content-Type"] == "image/png" and png["Content-Disposition"] == "inline"
    for h in (html, svg):  # could hold scripts: a download, never a page
        assert h["Content-Type"] == "application/octet-stream" and h["Content-Disposition"].startswith("attachment")
    assert all(h["Content-Security-Policy"].startswith("sandbox") and h["X-Content-Type-Options"] == "nosniff" for h in (png, html, svg))
