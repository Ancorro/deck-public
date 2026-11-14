"""Antigravity's documented stream protocol, plus Deck's persisted chat and Windows discovery."""
import io
import json

import pytest

from deck import agents, buttons, config
from deck.chat_bridge import Agy, Bridge, resolve

REAL_SPAWN = Agy.spawn  # the fixture stubs it out


@pytest.fixture
def agy(tmp_path, monkeypatch):
    monkeypatch.setattr(Agy, "spawn", lambda self: None)
    monkeypatch.setattr(Agy, "usage", lambda self: None)
    monkeypatch.setattr(Agy, "title", lambda self: "")
    b = Bridge(str(tmp_path), "agy-test")
    a = Agy(b, [], False, str(tmp_path))
    a.p = type("Process", (), {"stdin": io.BytesIO()})()
    yield a
    b.out.close()


def events(a):
    return [json.loads(line) for line in a.b.out_path.read_text(encoding="utf-8").splitlines()]


def step(a, index, typ, state="DONE", **fields):
    a.event({"event": "step_update", "step_update": {
        "step_index": index, "step_type": typ, "state": state, **fields}})


def test_streams_turns_tools_and_replays_after_restart(agy):
    a = agy
    a.event({"event": "init", "conversation_id": "conversation-1", "init": {"permission_mode": "request-review"}})
    a.command({"t": "say", "text": "Read a file"})
    assert json.loads(a.p.stdin.getvalue()) == {"event": "user", "message": {"content": "Read a file"}}
    step(a, 0, "user_input")
    step(a, 1, "agent_response", "ACTIVE", text_delta="Here ")
    step(a, 1, "agent_response", text_delta="it is.")
    step(a, 2, "tool", tool_info={"name": "read_file", "parameters": {"path": "sample"}, "output": "one\r\ntwo\r\n"})
    a.event({"event": "result", "result": {"status": "SUCCESS"}})
    assert not a.busy and not a.sent
    original = events(a)
    assert [e["d"] for e in original if e["t"] == "delta"] == ["Here ", "it is."]
    assert next(e for e in original if e["t"] == "tool_end")["out"] == "one\ntwo"
    a.b.out.close()
    b = Bridge(str(a.b.out_path.parent), "agy-test")
    resumed = Agy(b, [], True, a.cwd)
    assert resumed.conv == "conversation-1"
    resumed.replay()
    replay = events(resumed)
    assert [e["text"] for e in replay if e["t"] == "user"] == ["Read a file"]
    assert next(e for e in replay if e["t"] == "msg")["blocks"] == [{"kind": "text", "text": "Here it is."}]
    assert next(e for e in replay if e["t"] == "tool_end")["out"] == "one\ntwo"
    b.out.close()


def test_error_returns_control_and_next_prompt_works(agy):
    a = agy
    a.event({"event": "init", "conversation_id": "conversation-1"})
    a.command({"t": "say", "text": "First"})
    step(a, 0, "user_input")
    a.event({"event": "result", "result": {"status": "ERROR", "error": "Rate limited"}})
    assert not a.busy
    assert any(e.get("err") and e.get("text") == "Rate limited" for e in events(a))
    a.command({"t": "say", "text": "Try again"})
    step(a, 1, "user_input")
    step(a, 2, "agent_response", text_delta="Recovered")
    a.event({"event": "result", "result": {"status": "SUCCESS"}})
    assert not a.busy and not a.sent


def test_stop_restarts_same_conversation_and_accepts_next_prompt(agy):
    a = agy
    a.conv = "conversation-1"
    a.command({"t": "say", "text": "First"})
    step(a, 0, "user_input")
    step(a, 1, "agent_response", "ACTIVE", text_delta="Partial")
    killed = []
    a.kill = lambda: killed.append(True)
    a.command({"t": "abort"})
    assert killed and a.stopping
    class Process:
        stdout = io.BytesIO()
        def wait(self):
            return 0
    a.p = Process()
    spawned = []
    def spawn():
        spawned.append(a.conv)
        a.p = Process()
    a.spawn = spawn
    assert a.run("") == 0
    assert spawned == ["conversation-1"] and not a.busy
    assert any(e.get("blocks") == [{"kind": "text", "text": "Partial"}] for e in events(a))
    a.p.stdin = io.BytesIO()
    a.command({"t": "say", "text": "Next"})
    assert a.busy and a.sent == ["Next"]


def test_model_selection_persists_and_restarts(agy):
    a = agy
    killed = []
    a.kill = lambda: killed.append(True)
    a.conv = "conversation-1"
    a.set_model("gemini-example")
    assert a.model == a.b.prefs["model"] == "gemini-example"
    assert a.conv == "conversation-1" and a.stopping and killed


def test_effort_menu_uses_native_choices_and_keeps_the_conversation(agy):
    a = agy
    a.model, a.conv = "gemini-4-argon-medium", "conversation-1"
    a.native_command = lambda command: {"adjustable": True, "current": "medium", "available": ["low", "medium", "high"]}
    killed = []
    a.kill = lambda: killed.append(True)
    a.handle({"t": "menu", "text": "/effort", "rid": "effort-menu"})
    menu = next(e for e in events(a) if e.get("rid") == "effort-menu")
    assert menu["options"] == ["low", "medium", "high"]
    a.handle({"t": "answer", "id": menu["id"], "value": "high"})
    assert a.model == a.b.prefs["model"] == "gemini-4-argon-high"
    assert a.conv == "conversation-1" and killed and a.stopping
    assert any(e.get("parts", [])[:2] == ["gemini-4-argon-high", "high"] for e in events(a))


def test_fixed_effort_model_explains_why_there_are_no_choices(agy):
    a = agy
    a.native_command = lambda command: {"adjustable": False}
    a.handle({"t": "menu", "text": "/effort", "rid": "fixed"})
    menu = next(e for e in events(a) if e.get("rid") == "fixed")
    assert menu["options"] == [] and "fixed reasoning effort" in menu["message"]


def test_effort_change_persists_across_bridge_restart(agy):
    a = agy
    a.model, a.conv = "claude-opus-5-5-medium", "conversation-1"
    a.kill = lambda: None
    a.b.save(resume=a.conv)
    a.set_effort("high")
    b = Bridge(str(a.b.out_path.parent), "agy-test")
    try:
        resumed = Agy(b, [], True, a.cwd)
        assert resumed.model == "claude-opus-5-5-high"
        assert resumed.conv == "conversation-1"
    finally:
        b.out.close()


@pytest.mark.parametrize("windows", [False, True])
def test_default_install_found_without_updated_path(tmp_path, monkeypatch, windows):
    monkeypatch.setattr(config, "WINDOWS", windows)
    monkeypatch.setattr(config, "HOME", tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(agents.shutil, "which", lambda _: None)
    binary = tmp_path / "local/agy/bin/agy.exe" if windows else tmp_path / ".local/bin/agy"
    assert not agents.agy_available()
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"")
    assert agents.agy_available()
    assert resolve("agy") == [str(binary)]


def test_agy_key_launches_chat_with_context(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "NOTES", tmp_path / "notes")
    monkeypatch.setattr(config, "CHAT_DIR", tmp_path / "chat")
    monkeypatch.setattr(agents, "deck_context", lambda: "Deck context")
    key = buttons.clean_button({"label": "Antigravity", "type": "agy", "text": "Hello"})
    key.update(chat=True, session="agy-test")
    argv = agents.build_argv(key)
    assert "agy" in argv and "--add-dir" in argv
    context = (tmp_path / "agy-context/AGENTS.md").read_text(encoding="utf-8")
    assert context.startswith("Deck context\n")
    assert "<deck-question>" in context


def test_mode_control_persists_without_init_overwriting_plan_mode(agy):
    a = agy
    a.kill = lambda: None
    a.handle({"t": "menu", "text": "/mode", "rid": "mode"})
    menu = next(e for e in events(a) if e.get("rid") == "mode")
    a.handle({"t": "answer", "id": menu["id"], "value": "Plan"})
    assert a.mode == a.b.prefs["mode"] == "plan"
    assert not a.yolo and not a.b.prefs["yolo"]
    a.event({"event": "init", "init": {"permission_mode": "request-review"}})
    assert a.mode == "plan"
    assert next(e for e in reversed(events(a)) if e["t"] == "status")["mode"] == "plan"


@pytest.mark.parametrize("mode,yolo,expect", [
    ("", False, ["--settings", '{"agentMode": ""}']), ("plan", False, ["--mode", "plan"]),
    ("accept-edits", False, ["--mode", "accept-edits"]), ("auto", True, ["--mode", "auto"])])
def test_ask_mode_starts_agy_in_default_not_auto(agy, monkeypatch, mode, yolo, expect):
    """agy 1.5.7 made Auto the default for sessions with no --mode, so Ask before tools must ask for Default."""
    a = agy
    a.model, a.mode, a.yolo, a.notes_synced = "gemini-test", mode, yolo, True
    started = []
    monkeypatch.setattr(a, "start", started.append, raising=False)
    REAL_SPAWN(a)
    argv = started[0]
    i = argv.index(expect[0])
    assert argv[i:i + 2] == expect
    assert ("--settings" in argv) == (expect[0] == "--settings")
    assert (Agy.YOLO in argv) == yolo


def test_native_slash_command_uses_separate_print_call(agy, monkeypatch):
    import subprocess

    from deck.bridge import util
    a = agy
    monkeypatch.setattr(util, "resolve", lambda _: ["agy"])
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return type("Result", (), {"stdout": json.dumps({"status": "SUCCESS", "response": "Available commands"})})()
    monkeypatch.setattr(subprocess, "run", run)
    a.handle({"t": "say", "text": "/help"})
    assert calls[0][:3] == ["agy", "-p", "/help"]
    assert any(e.get("blocks") == [{"kind": "text", "text": "Available commands"}] for e in events(a))
    assert not a.sent and not a.busy


def test_native_error_before_user_input_restores_control(agy):
    a = agy
    a.command({"t": "say", "text": "/bad-command"})
    a.event({"event": "result", "result": {"status": "ERROR", "error": "Unavailable command"}})
    assert not a.sent and not a.busy
    a.command({"t": "say", "text": "Next"})
    assert a.sent == ["Next"]


def test_permission_denial_requires_explicit_session_approval(agy):
    a = agy
    a.yolo = False
    a.conv, a.last_input = "conversation-1", "Run echo hello"
    a.kill = lambda: None
    a.event({"event": "result", "result": {"status": "SUCCESS", "denied_actions": [{"action": "command"}]}})
    question = next(e for e in events(a) if e["t"] == "ask")
    assert not a.yolo
    a.handle({"t": "answer", "id": question["id"], "value": "Allow tools for this session and retry"})
    assert a.yolo and a.b.prefs["yolo"] and a.b.next_first == "Run echo hello"
    assert a.b.switch_to == "conversation-1"


def test_question_card_answers_continue_the_conversation(agy):
    a = agy
    response = '<deck-question>{"question":"Choose a colour", "options":["Red","Blue"]}</deck-question>'
    step(a, 1, "agent_response", text_delta=response)
    a.event({"event": "result", "result": {"status": "SUCCESS"}})
    question = next(e for e in events(a) if e["t"] == "ask")
    assert question["options"] == ["Red", "Blue"]
    a.handle({"t": "answer", "id": question["id"], "value": "Blue"})
    assert "Blue" in json.loads(a.p.stdin.getvalue())["message"]["content"]


def test_compact_creates_a_fresh_conversation_with_a_handoff(agy):
    a = agy
    a.conv = "conversation-1"
    a.kill = lambda: None
    a.slash_compact("")
    assert a.compacting and a.busy
    step(a, 1, "user_input")
    step(a, 2, "agent_response", text_delta="Goal: fix Deck. Next: check the phone controls.")
    a.event({"event": "result", "result": {"status": "SUCCESS"}})
    assert a.b.switch_to == ""
    assert "Goal: fix Deck" in a.b.next_first
    assert not any(e["t"] == "delta" for e in events(a))


def test_start_does_not_wait_for_notes_sync(tmp_path, monkeypatch):
    """A sync waits on each peer in turn (an offline one costs its full timeout): agy must start without it."""
    import subprocess
    import threading
    import time

    from deck.bridge import util
    notes = tmp_path / "notes"
    (notes / "hosts").mkdir(parents=True)
    (notes / "AGENTS.md").write_text("notes v1", encoding="utf-8")
    rules = tmp_path / "ctx/.agents/rules"
    rules.mkdir(parents=True)
    monkeypatch.setenv("DECK_NOTES", str(notes))
    monkeypatch.setattr(util, "resolve", lambda _: ["agy"])
    monkeypatch.setattr(Agy, "usage", lambda self: None)
    monkeypatch.setattr(Agy, "title", lambda self: "")
    monkeypatch.setattr(Agy, "native_command", lambda self, c: {"id": "m"})
    started, release, synced = [], threading.Event(), threading.Event()
    monkeypatch.setattr(Agy, "start", lambda self, argv: started.append(time.monotonic()))
    def slow_sync(argv, **kw):
        release.wait(5)
        (notes / "AGENTS.md").write_text("notes v2", encoding="utf-8")
        synced.set()
        return subprocess.CompletedProcess(argv, 0)
    monkeypatch.setattr(subprocess, "run", slow_sync)
    b = Bridge(str(tmp_path), "agy-test")
    try:
        Agy(b, ["--add-dir", str(tmp_path / "ctx")], False, str(tmp_path))
        assert started  # started while the sync is still waiting
        assert (rules / "infra-AGENTS.md").read_text(encoding="utf-8") == "notes v1"
        release.set()
        assert synced.wait(5)
        for _ in range(50):
            if (rules / "infra-AGENTS.md").read_text(encoding="utf-8") == "notes v2":
                break
            time.sleep(0.05)
        assert (rules / "infra-AGENTS.md").read_text(encoding="utf-8") == "notes v2"
    finally:
        release.set()
        b.out.close()


@pytest.mark.parametrize("result, text, err", [
    ({"status": "SUCCESS", "finished_reason": "print_timeout"}, "agy stopped early: print timeout, the reply may be partial", False),
    ({"status": "ERROR", "error": "interrupted", "finished_reason": "interrupted"},
     "agy stopped early: interrupted, the reply may be partial", False),
    ({"status": "SUCCESS", "error": "Backend failed", "finished_reason": "error"}, "Backend failed", True),
])
def test_finished_reason_marks_an_early_end_once_and_drops_the_queue(agy, result, text, err):
    """agy 1.4.11+ says why a turn ended early in finished_reason (shapes as agy 1.4.12 sends them)."""
    a = agy
    a.command({"t": "say", "text": "Write an essay"})
    step(a, 0, "user_input")
    step(a, 1, "agent_response", "ACTIVE", text_delta="Rivers are")
    a.command({"t": "say", "text": "Queued"})
    a.b.waiting = [("q1", "Queued")]
    a.event({"event": "result", "result": result})
    notes = [e for e in events(a) if e["t"] == "note"]
    assert [(n["text"], bool(n.get("err"))) for n in notes] == [(text, err)]
    assert not a.sent and not a.b.waiting and not a.busy
    assert any(e["t"] == "unqueued" and e["id"] == "q1" for e in events(a))
    assert any(e.get("blocks") == [{"kind": "text", "text": "Rivers are"}] for e in events(a))


def test_normal_end_without_finished_reason_adds_no_note(agy):
    a = agy
    a.command({"t": "say", "text": "Hi"})
    step(a, 0, "user_input")
    step(a, 1, "agent_response", text_delta="Hello")
    a.event({"event": "result", "result": {"status": "SUCCESS"}})
    assert not [e for e in events(a) if e["t"] == "note"] and not a.busy



def test_default_model_consumed_once_and_session_pick_wins(agy, monkeypatch):
    a = Agy(agy.b, ["--model", "gemini-test-high"], False, agy.cwd)
    assert a.model == "gemini-test-high" and "--model" not in a.args
    agy.b.prefs["model"] = "session-model"
    a = Agy(agy.b, ["--model", "gemini-test-high"], True, agy.cwd)
    assert a.model == "session-model"


def test_tool_calls_enrich_from_transcript_and_strip_headers(agy, tmp_path, monkeypatch):
    monkeypatch.setattr(Agy, "SUMMARIES", property(lambda self: tmp_path / "antigravity-cli" / "conversation_summaries.db"))
    log = tmp_path / "antigravity-cli" / "brain" / "conversation-1" / ".system_generated" / "logs" / "transcript_full.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join([
        json.dumps({"step_index": 1, "tool_calls": [
            {"name": "run_command", "args": {"CommandLine": "git status", "toolSummary": "Check git status"}},
            {"name": "edit_file", "args": {"Path": "app.py", "Target": "old", "Replacement": "new"}},
        ]}),
    ]) + "\n", encoding="utf-8")
    a = agy
    a.event({"event": "init", "conversation_id": "conversation-1"})
    step(a, 2, "tool", tool_info={
        "name": "run_command",
        "parameters": {"CommandLine": "git status"},
        "output": "Created At: 2026-11-14T09:00:00-08:00\nCompleted At: 2026-11-14T09:00:01-08:00\nCommand exited with code 0.\n\nCommand output:\nclean\n",
    })
    step(a, 3, "tool", tool_info={"name": "edit_file", "parameters": {"Path": "app.py"}, "output": "ok"})
    msgs = [e for e in events(a) if e["t"] == "msg"]
    assert msgs[0]["blocks"][0]["args"] == {"CommandLine": "git status", "toolSummary": "Check git status"}
    assert msgs[1]["blocks"][0]["args"] == {"Path": "app.py", "Target": "old", "Replacement": "new"}
    ends = [e for e in events(a) if e["t"] == "tool_end"]
    assert ends[0]["out"] == "clean"
    assert ends[1]["out"] == "ok"
