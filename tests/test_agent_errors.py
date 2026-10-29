"""Provider failures must reach the chat, including failures absent from normal assistant output."""
import json

import pytest

from deck.chat_bridge import Bridge, Claude


@pytest.mark.parametrize("payload, expected", [
    ({"error": "rate_limit", "error_details": "429: Too many requests"}, "429: Too many requests"),
    ({"error": "authentication_failed"}, "authentication_failed"),
    ({}, "The model request failed. Try again or choose another model."),
])
def test_claude_stop_failure_reports_error_and_clears_working(tmp_path, payload, expected):
    b = Bridge(str(tmp_path), "failure")
    c = Claude.__new__(Claude)
    c.b, c.busy, c.ysk = b, True, {"later": None}
    c.hook({"hook_event_name": "StopFailure", **payload})
    assert not c.busy
    assert json.loads(b.state_path.read_text(encoding="utf-8"))["state"] == "your_turn"
    b.out.close()
    events = [json.loads(line) for line in b.out_path.read_text(encoding="utf-8").splitlines()]
    assert any(e.get("t") == "note" and e.get("err") and e.get("text") == expected for e in events)
    assert events[-1]["s"] == "your_turn"


def test_claude_error_transcript_is_not_an_assistant_reply(tmp_path):
    b = Bridge(str(tmp_path), "failure")
    c = Claude.__new__(Claude)
    c.b, c.busy = b, False
    c.record({"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
              "message": {"id": "error-1", "content": [{"type": "text", "text": "429: Rate limited"}]}})
    assert not c.busy  # replaying an error must never turn the working spinner back on
    b.out.close()
    events = [json.loads(line) for line in b.out_path.read_text(encoding="utf-8").splitlines()]
    assert [(e["text"], e["err"]) for e in events if e.get("t") == "note"] == [("429: Rate limited", True)]
    assert not any(e.get("t") == "msg" for e in events)
