import json

import pytest

from deck import chat_bridge


def claude(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_bridge, "resolve", lambda _: ["claude"])
    b = chat_bridge.Bridge(str(tmp_path), "stop-test")
    c = chat_bridge.Claude(b, [], False, str(tmp_path))
    writes = []
    c.write = writes.append
    return b, c, writes


def test_stop_without_interruption_record_clears_working(tmp_path, monkeypatch):
    """Escape cancels Claude's 429 retry without a Stop hook or interruption transcript."""
    b, c, writes = claude(tmp_path, monkeypatch)
    try:
        c.working(True)
        c.command({"t": "abort"})
        assert writes == ["\x1b"]
        assert not c.busy
        assert b.status["state"] == "your_turn"
        quiet = c.quiet

        # A delayed assistant record from the cancelled request must not start another turn.
        c.record({"type": "assistant", "timestamp": "2000-01-01T00:00:00Z",
                  "message": {"id": "old", "content": [{"type": "text", "text": "old reply"}]}})
        assert not c.busy
        assert c.quiet == quiet
        c.hook({"hook_event_name": "Stop"})  # a normal turn may still report its stop later
        assert not c.busy

        # The same session can accept the next prompt, rather than needing a restart.
        monkeypatch.setattr(chat_bridge.time, "sleep", lambda _: None)
        c.command({"t": "say", "text": "Try again"})
        assert c.busy
        assert writes[-2:] == ["\x1b[200~Try again\x1b[201~", "\r"]
        assert writes[1:-2] == ["\x15"] * 4
    finally:
        b.out.close()


def test_stop_write_failure_keeps_session_working(tmp_path, monkeypatch):
    b, c, _ = claude(tmp_path, monkeypatch)
    try:
        c.working(True)
        def broken_write(_):
            raise OSError("terminal is unavailable")
        c.write = broken_write
        with pytest.raises(OSError, match="terminal is unavailable"):
            c.command({"t": "abort"})
        assert c.busy
        assert b.status["state"] == "working"
    finally:
        b.out.close()


def test_repeated_stop_does_not_emit_duplicate_state(tmp_path, monkeypatch):
    b, c, writes = claude(tmp_path, monkeypatch)
    try:
        c.working(True)
        c.command({"t": "abort"})
        c.command({"t": "abort"})
    finally:
        b.out.close()
    events = [json.loads(line) for line in b.out_path.read_text(encoding="utf-8").splitlines()]
    assert writes == ["\x1b", "\x1b"]
    assert [e["s"] for e in events if e.get("t") == "state"] == ["working", "your_turn"]


def test_terminal_retry_notice_handles_chunks_and_deduplicates(tmp_path, monkeypatch):
    b, c, _ = claude(tmp_path, monkeypatch)
    try:
        c.working(True)
        for chunk in ["\x1b[3", "1m✻ API error · Ret", "rying in 3s · attempt 1/10\x1b[0m"]:
            c.retry_notice(chunk)
        c.retry_notice("API error · Retrying in 2s · attempt 1/10")
        c.retry_notice("API error · Retrying in 6s · attempt 2/10")
        assert c.busy and b.status["state"] == "working"
        c.command({"t": "abort"})
        # Old terminal output after Stop cannot reopen an error card.
        c.retry_notice("API error · Retrying in 3s · attempt 1/10")
        c.working(True)
        c.retry_notice("unrelated output")
        assert not c.retry_reported  # the previous turn's terminal buffer was discarded
        c.retry_notice("API error · Retrying in 3s · attempt 1/10")
    finally:
        b.out.close()
    events = [json.loads(line) for line in b.out_path.read_text(encoding="utf-8").splitlines()]
    errors = [e for e in events if e.get("t") == "note" and e.get("err")]
    assert len(errors) == 2
    assert errors[0]["text"] == "Claude: API error · Retrying in 3s · attempt 1/10. Stop cancels the retry."


def test_next_prompt_clears_cancelled_multiline_draft(tmp_path, monkeypatch):
    b, c, writes = claude(tmp_path, monkeypatch)
    monkeypatch.setattr(chat_bridge.time, "sleep", lambda _: None)
    try:
        c.command({"t": "say", "text": "first\nsecond\nthird"})
        c.command({"t": "abort"})
        writes.clear()
        c.command({"t": "say", "text": "replacement"})
        assert writes == ["\x15"] * 8 + ["\x1b[200~replacement\x1b[201~", "\r"]
        writes.clear()
        c.command({"t": "say", "text": "next"})
        assert writes == ["\x1b[200~next\x1b[201~", "\r"]
    finally:
        b.out.close()


def test_initial_prompt_line_count_is_kept_for_cancellation(tmp_path, monkeypatch):
    b, c, writes = claude(tmp_path, monkeypatch)
    monkeypatch.setattr(chat_bridge.time, "sleep", lambda _: None)
    monkeypatch.setattr(c, "redraw", lambda *a: None)
    monkeypatch.setattr(c, "pump", lambda: None)
    monkeypatch.setattr(chat_bridge, "Term", lambda *a: type("Term", (), {"wait": lambda self: 0})())
    try:
        assert c.run("first\nsecond") == 0
        c.command({"t": "abort"})
        writes.clear()
        c.command({"t": "say", "text": "replacement"})
        assert writes == ["\x15"] * 6 + ["\x1b[200~replacement\x1b[201~", "\r"]
    finally:
        b.out.close()
