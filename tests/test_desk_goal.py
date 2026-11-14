"""The Screen goal row (deck/screen.py goal_*): start a run, pause, resume and stop it through the control file."""
import sys
import time

import pytest

from deck import agents, screen

# A stand-in loop that speaks the agent-desk.py protocol: a step every 0.1 s, holding while control says pause.
FAKE = r'''
import json, os, sys, time
c, s = os.environ["DESK_CONTROL"], os.environ["DESK_STATUS"]
goal = sys.argv[1]
def rep(state, step): open(s, "w").write(json.dumps({"state": state, "step": step, "steps": 99, "goal": goal, "note": "n"}))
for step in range(1, 100):
    while open(c).read() == "pause":
        rep("paused", step - 1); time.sleep(0.05)
    if open(c).read() == "stop":
        rep("stopped", step); break
    rep("running", step); time.sleep(0.1)
'''


def wait(pred, t=5):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def desk(tmp_path, monkeypatch):
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path / "goal")
    monkeypatch.setattr(agents, "computer_config", lambda: {"python": "x", "goal": [sys.executable, str(fake)]})
    yield
    p = screen._goal["proc"]
    if p and p.poll() is None:
        p.kill()
    screen._goal["proc"] = None


def test_run_pause_resume_stop(desk):
    st = screen.goal_start("apply to a job")
    assert st["available"] and st["running"]
    assert wait(lambda: screen.goal_status()["state"] == "running")
    screen.goal_control("pause")
    assert wait(lambda: screen.goal_status()["state"] == "paused")
    held = screen.goal_status()["step"]
    time.sleep(0.4)
    assert screen.goal_status()["step"] == held  # no steps while paused
    screen.goal_control("resume")
    assert wait(lambda: screen.goal_status()["step"] > held)
    screen.goal_control("stop")
    assert wait(lambda: not screen.goal_status()["running"])
    st = screen.goal_status()
    assert st["state"] == "stopped" and st["goal"] == "apply to a job"


def test_one_run_at_a_time_and_no_empty_goal(desk):
    screen.goal_start("a")
    with pytest.raises(ValueError):
        screen.goal_start("b")
    screen.goal_control("stop")
    assert wait(lambda: not screen.goal_status()["running"])
    with pytest.raises(ValueError):
        screen.goal_start("  ")


def test_no_loop_here(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path)
    monkeypatch.setattr(agents, "computer_config", lambda: None)
    assert screen.goal_status()["available"] is False
    with pytest.raises(ValueError):
        screen.goal_start("x")


def test_log_reads_the_runs_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path)
    log = tmp_path / "loop.jsonl"
    rows = [{"t": "1", "event": "start", "goal": "g"},
            {"t": "2", "event": "decide", "i": "1", "by": "vision", "action": "key", "text": "super", "note": "open"},
            {"t": "3", "event": "act", "step": "1", "result": "pressed"},
            {"t": "4", "event": "end", "result": "done", "note": "finished"}]
    log.write_text("\n".join(__import__("json").dumps(r) for r in rows) + "\nnot json\n")
    (tmp_path / "status.json").write_text(__import__("json").dumps({"log": str(log)}))
    out = screen.goal_log()
    assert [r["kind"] for r in out] == ["start", "step", "end"]
    assert out[1]["action"] == "key" and out[1]["result"] == "pressed" and out[1]["note"] == "open"
    assert out[2]["note"] == "finished"


def test_a_run_outlives_a_deck_restart(desk):
    screen.goal_start("keep going")
    assert wait(lambda: screen.goal_status()["state"] == "running")
    proc, screen._goal["proc"] = screen._goal["proc"], None  # Deck restarted: no handle, only the status file
    import json as j
    st = screen._goal_files()[1]
    d = j.loads(st.read_text())
    d["pid"] = proc.pid  # the fake does not write its pid
    st.write_text(j.dumps(d))
    assert screen.goal_status()["running"]
    screen.goal_control("stop")
    proc.wait(5)


def test_log_shows_each_model_call_and_its_screenshot(tmp_path, monkeypatch):
    import json as j
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path)
    (tmp_path / "calls").mkdir()
    (tmp_path / "calls" / "0001.jpg").write_bytes(b"jpg")
    log = tmp_path / "loop.jsonl"
    log.write_text(j.dumps({"t": 1, "event": "call", "n": 1, "by": "vision", "model": "opus", "seconds": 5.1,
                            "sent": {"goal": "g", "image": str(tmp_path / "calls" / "0001.jpg")}, "reply": {"action": "click"}}))
    (tmp_path / "status.json").write_text(j.dumps({"log": str(log)}))
    (c,) = screen.goal_log()
    assert c["kind"] == "call" and c["model"] == "opus" and c["image"] == "0001.jpg" and "image" not in c["sent"]
    assert screen.goal_log_image("0001.jpg") == tmp_path / "calls" / "0001.jpg"
    assert screen.goal_log_image("../status.json") is None


def test_settings_pick_the_paid_model_for_the_next_run(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path)
    assert screen.goal_settings()["model"] == "sonnet"  # not opus by default
    screen.goal_settings({"api": "codex", "model": "gpt-6-luna", "effort": "low", "fast": False, "steps": 12})
    env = screen._goal_env()
    assert env == {"CLICKER_WRITER_API": "codex", "CLICKER_ANSWER_MODEL": "gpt-6-luna", "CLICKER_ANSWER_REASONING": "low",
                   "LOOP_FAST": ""}
    assert screen.goal_settings()["steps"] == 12
    for bad in ({"api": "x"}, {"model": "a b; rm"}, {"effort": "max"}):
        with pytest.raises(ValueError):
            screen.goal_settings(bad)


def test_the_paid_model_session_carries_decks_context_and_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path)
    monkeypatch.setattr(agents, "deck_context", lambda: "DECK CONTEXT")
    env = screen._goal_env()
    sid = env["CLICKER_CLAUDE_SESSION"]
    assert screen._goal_env()["CLICKER_CLAUDE_SESSION"] == sid  # kept across runs
    text = open(env["CLICKER_CLAUDE_CONTEXT"]).read()
    assert text.startswith("DECK CONTEXT") and "Never invent facts about the user" in text
    screen.goal_settings({"reset_session": True})
    assert screen._goal_env()["CLICKER_CLAUDE_SESSION"] != sid
    screen.goal_settings({"api": "codex"})
    assert "CLICKER_CLAUDE_SESSION" not in screen._goal_env()  # only Claude Code keeps a session


def test_status_carries_the_runs_cost(tmp_path, monkeypatch):
    import json as j
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path)
    log = tmp_path / "loop.jsonl"
    log.write_text("\n".join(j.dumps(r) for r in [
        {"event": "call", "n": 1, "by": "plan", "cost_total": {"plan": 0.01, "fast": 0.0, "vision": 0.0}},
        {"event": "call", "n": 2, "by": "vision", "cost_total": {"plan": 0.01, "fast": 0.0, "vision": 0.02}}]))
    (tmp_path / "status.json").write_text(j.dumps({"state": "done", "log": str(log)}))
    assert screen.goal_status()["cost"]["total"] == 0.03


def test_the_users_files_go_in_the_paid_models_context(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path)
    monkeypatch.setattr(agents, "deck_context", lambda: "CTX")
    notes = tmp_path / "facts.md"
    notes.write_text("Phone 555-0100")
    monkeypatch.setattr(agents, "computer_config", lambda: {"python": "x", "goal": ["x"], "context_files": [str(notes), "/nope.pdf"]})
    text = screen.goal_context().read_text()
    assert "Phone 555-0100" in text and "/nope.pdf" not in text


def test_each_goal_starts_a_new_paid_session(desk):
    screen.goal_start("one")
    first = (screen.GOAL_DIR / "session").read_text()
    screen.goal_control("stop")
    assert wait(lambda: not screen.goal_status()["running"])
    screen.goal_start("two")
    assert (screen.GOAL_DIR / "session").read_text() != first
    screen.goal_control("stop")
    assert wait(lambda: not screen.goal_status()["running"])


def test_the_brief_names_the_configured_display_not_a_fixed_one(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "GOAL_DIR", tmp_path)
    monkeypatch.setattr(agents, "deck_context", lambda: "CTX")
    monkeypatch.setattr(agents, "computer_config", lambda: {"python": "x", "goal": ["x"], "display": ":7"})
    assert "(display :7)" in screen.goal_context().read_text()
    monkeypatch.setattr(agents, "computer_config", lambda: {"python": "x", "goal": ["x"]})
    text = screen.goal_context().read_text()
    assert "display" not in text.split("You drive the agent desktop on", 1)[1].split("\n", 1)[0]
