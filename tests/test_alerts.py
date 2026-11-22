"""Phone alerts: a question gets the same alert as any other stop (user 2026-11-18: "remove deck special notify on quesionts")."""
from deck import alerts


def s(state, name="t1"):
    return {"name": name, "type": "claude", "state": state, "label": name}


def run(steps):
    """steps: (time, state) looks at one agent session; returns the alerts each look gave."""
    seen = {}
    return [alerts.changes(seen, [s(state)], now) for now, state in steps]


def test_short_work_then_question_is_quiet():
    assert run([(0, "your_turn"), (1, "working"), (6, "needs_you")])[-1] == []


def test_long_work_then_question_alerts_like_any_stop():
    out = run([(0, "your_turn"), (1, "working"), (40, "needs_you")])[-1]
    assert [x["state"] for x in out] == ["needs_you"]
    body = alerts.message(out[0], "host", None)
    assert body["priority"] == 3 and body["tags"] == ["white_check_mark"]
    assert body["title"] == "t1: needs you"


def test_question_without_work_is_quiet():
    assert run([(0, "your_turn"), (30, "needs_you")])[-1] == []


def test_long_work_then_your_turn_unchanged():
    out = run([(0, "your_turn"), (1, "working"), (40, "your_turn")])[-1]
    assert [x["state"] for x in out] == ["your_turn"]
    assert alerts.message(out[0], "host", None)["priority"] == 3
