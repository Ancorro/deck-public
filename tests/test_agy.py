"""Antigravity's ACP protocol adapter, context injection, effort levels, and Windows discovery."""
import io
import json

import pytest

from deck import agents, buttons, config, settings
from deck.bridge.bridge import edited_path
from deck.chat_bridge import Agy, Bridge, resolve

REAL_SPAWN = Agy.spawn  # the fixture stubs it out


@pytest.fixture
def agy(tmp_path, monkeypatch):
    monkeypatch.setattr(Agy, "spawn", lambda self: None)
    monkeypatch.setattr(Agy, "usage", lambda self: None)
    b = Bridge(str(tmp_path), "agy-test")
    a = Agy(b, [], False, str(tmp_path))
    a.p = type("Process", (), {"stdin": io.BytesIO()})()
    yield a
    b.out.close()


def sent(a):
    return [json.loads(line) for line in a.p.stdin.getvalue().decode("utf-8").splitlines() if line.strip()]


def events(a):
    return [json.loads(line) for line in a.b.out_path.read_text(encoding="utf-8").splitlines()]


def ready_session(a, sid="session-1", *, model="gemini-4-neon", effort="high", mode="default",
                  efforts=("low", "medium", "high", "xhigh")):
    opts = [
        {"id": "model", "currentValue": model, "options": [
            {"value": "gemini-4-neon", "name": "Gemini 4 Neon"},
            {"value": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"},
            {"value": "orchestrator", "name": "Orchestrator"},
        ]},
        {"id": "mode", "currentValue": mode, "options": [
            {"value": "default", "name": "Default"},
            {"value": "accept-edits", "name": "Accept edits"},
            {"value": "auto", "name": "Auto"},
            {"value": "plan", "name": "Plan"},
        ]},
    ]
    if efforts:
        opts.append({"id": "thought_level", "currentValue": effort,
                     "options": [{"value": e, "name": e.title()} for e in efforts]})
    a.session_ready({"sessionId": sid, "modes": {"currentModeId": mode}, "configOptions": opts}, None)


def upd(a, update):
    a.event({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": a.conv, "update": update}})


def test_streams_thinking_text_tools_usage_and_title(agy):
    a = agy
    ready_session(a)
    a.command({"t": "say", "text": "Read a file"})
    reqs = sent(a)
    prompt_req = next(r for r in reqs if r.get("method") == "session/prompt")
    assert prompt_req["params"]["prompt"] == [{"type": "text", "text": "Read a file"}]
    assert a.busy

    upd(a, {"sessionUpdate": "session_info_update", "title": "File Inspection"})
    upd(a, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "Checking "}})
    upd(a, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "file."}})
    upd(a, {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "name": "read_file",
            "status": "in_progress", "rawInput": {"Path": "sample.txt"}})
    upd(a, {"sessionUpdate": "tool_call_update", "toolCallId": "tc-1", "name": "read_file",
            "status": "completed", "rawInput": {"Path": "sample.txt"},
            "rawOutput": {"output": "Created At: 2026-11-14T09:00:00-08:00\nCompleted At: 2026-11-14T09:00:01-08:00\n"
                                    "Command exited with code 0.\n\nCommand output:\none\r\ntwo\r\n"}})
    upd(a, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Here "}})
    upd(a, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "it is."}})
    upd(a, {"sessionUpdate": "usage_update", "used": 20000, "size": 1000000})
    a.event({"jsonrpc": "2.0", "id": prompt_req["id"], "result": {"stopReason": "end_turn"}})

    assert not a.busy
    evs = events(a)
    assert any(e["t"] == "name" and e["text"] == "File Inspection" for e in evs)
    assert [e["d"] for e in evs if e["t"] == "delta" and e["kind"] == "think"] == ["Checking ", "file."]
    assert [e["d"] for e in evs if e["t"] == "delta" and e["kind"] == "text"] == ["Here ", "it is."]
    assert any(e.get("blocks") == [{"kind": "think", "text": "Checking file."}] for e in evs)
    assert any(e.get("blocks") == [{"kind": "text", "text": "Here it is."}] for e in evs)
    assert next(e for e in evs if e["t"] == "tool_end")["out"] == "one\ntwo"
    assert any(e["t"] == "status" and "Context 2% used" in e["parts"] for e in evs)


def test_session_load_replays_history_and_falls_back_on_error(agy):
    a = agy
    a.resume, a.conv = True, "old-session"
    a.started({}, None)
    load_req = next(r for r in sent(a) if r.get("method") == "session/load")
    assert load_req["params"]["sessionId"] == "old-session"
    assert a.replaying

    upd(a, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "Hi"}})
    upd(a, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello!"}})
    a.event({"jsonrpc": "2.0", "id": load_req["id"], "result": {"sessionId": "old-session", "configOptions": []}})
    assert not a.replaying
    evs = events(a)
    assert [e["text"] for e in evs if e["t"] == "user"] == ["Hi"]
    assert any(e.get("blocks") == [{"kind": "text", "text": "Hello!"}] for e in evs)
    assert not any(e["t"] == "delta" for e in evs)

    # Error on session/load falls back to session/new
    a.p.stdin = io.BytesIO()
    a.resume, a.conv = True, "missing-session"
    a.session_ready(None, {"code": -32002, "message": "not found"})
    assert sent(a)[-1]["method"] == "session/new"


def test_stop_sends_session_cancel_without_restarting(agy):
    a = agy
    ready_session(a)
    a.command({"t": "say", "text": "Write an essay"})
    prompt_req = next(r for r in sent(a) if r.get("method") == "session/prompt")
    killed = []
    a.kill = lambda: killed.append(True)
    a.command({"t": "abort"})
    assert not killed
    cancel = sent(a)[-1]
    assert cancel == {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "session-1"}}
    a.event({"jsonrpc": "2.0", "id": prompt_req["id"], "result": {"stopReason": "cancelled"}})
    assert not a.busy
    assert any(e["t"] == "note" and e["text"] == "Stopped" for e in events(a))


def test_error_returns_control_and_next_prompt_works(agy):
    a = agy
    ready_session(a)
    a.command({"t": "say", "text": "First"})
    req1 = next(r for r in sent(a) if r.get("method") == "session/prompt")
    a.event({"jsonrpc": "2.0", "id": req1["id"], "error": {"code": -32000, "message": "Rate limited"}})
    assert not a.busy
    assert any(e.get("err") and e.get("text") == "Rate limited" for e in events(a))
    a.command({"t": "say", "text": "Try again"})
    assert a.busy


def test_model_and_effort_switch_in_place_without_restart(agy):
    a = agy
    ready_session(a)
    killed = []
    a.kill = lambda: killed.append(True)
    a.handle({"t": "menu", "text": "/effort", "rid": "effort-menu"})
    menu = next(e for e in events(a) if e.get("rid") == "effort-menu")
    assert menu["options"] == ["Low", "Medium", "High", "Xhigh"]
    a.handle({"t": "answer", "id": menu["id"], "value": "Xhigh"})
    req = sent(a)[-1]
    assert req["method"] == "session/set_config_option"
    assert req["params"] == {"sessionId": "session-1", "configId": "thought_level", "value": "xhigh"}
    assert a.effort == a.b.prefs["effort"] == "xhigh" and not killed

    a.set_model("gemini-3.8-flash")
    req = sent(a)[-1]
    assert req["method"] == "session/set_config_option"
    assert req["params"] == {"sessionId": "session-1", "configId": "model", "value": "gemini-3.8-flash"}
    assert a.model == a.b.prefs["model"] == "gemini-3.8-flash" and not killed


def test_fixed_effort_model_explains_why_there_are_no_choices(agy):
    a = agy
    ready_session(a, model="orchestrator", effort="", efforts=())
    a.handle({"t": "menu", "text": "/effort", "rid": "fixed"})
    menu = next(e for e in events(a) if e.get("rid") == "fixed")
    assert menu["options"] == [] and "fixed reasoning effort" in menu["message"]


def test_native_permission_request_allow_and_allow_all(agy):
    a = agy
    a.yolo = False
    ready_session(a)
    a.active_turns = 1
    a.event({
        "jsonrpc": "2.0", "id": "p1", "method": "session/request_permission",
        "params": {
            "sessionId": "session-1",
            "toolCall": {"name": "run_command", "title": "git --version", "rawInput": {"CommandLine": "git --version"}},
            "options": [
                {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
            ],
        },
    })
    ask = next(e for e in events(a) if e["t"] == "ask")
    assert "git --version" in ask["title"]
    assert ask["options"] == ["Allow", "Deny", "Allow all for session"]
    a.handle({"t": "answer", "id": ask["id"], "value": "Allow all for session"})
    assert sent(a)[-1] == {"jsonrpc": "2.0", "id": "p1", "result": {"outcome": {"outcome": "selected", "optionId": "allow"}}}
    assert a.allow_all

    # Subsequent request auto-approves without prompting
    a.event({
        "jsonrpc": "2.0", "id": "p2", "method": "session/request_permission",
        "params": {
            "sessionId": "session-1",
            "toolCall": {"name": "run_command", "title": "echo hi"},
            "options": [{"optionId": "allow", "name": "Allow", "kind": "allow_once"}],
        },
    })
    assert sent(a)[-1] == {"jsonrpc": "2.0", "id": "p2", "result": {"outcome": {"outcome": "selected", "optionId": "allow"}}}


def test_native_elicitation_single_and_multi_select(agy):
    a = agy
    ready_session(a)
    a.event({
        "jsonrpc": "2.0", "id": "e1", "method": "elicitation/create",
        "params": {
            "sessionId": "session-1", "mode": "form", "message": "2 questions",
            "requestedSchema": {
                "type": "object",
                "properties": {
                    "q1": {"type": "string", "title": "Colour?",
                           "oneOf": [{"const": "1", "title": "Red"}, {"const": "2", "title": "Blue"}]},
                    "q1_other": {"type": "string", "title": "Other"},
                    "q2": {"type": "array", "title": "Formats?",
                           "items": {"anyOf": [{"const": "1", "title": "JSON"}, {"const": "2", "title": "YAML"}]}},
                    "q2_other": {"type": "string", "title": "Other"},
                },
            },
        },
    })
    ask1 = [e for e in events(a) if e["t"] == "ask"][-1]
    assert ask1["options"] == ["Red", "Blue"] and not ask1["multi"]
    a.handle({"t": "answer", "id": ask1["id"], "value": "Blue"})
    ask2 = [e for e in events(a) if e["t"] == "ask"][-1]
    assert ask2["options"] == ["JSON", "YAML"] and ask2["multi"]
    a.handle({"t": "answer", "id": ask2["id"], "value": ["JSON", "YAML"]})
    assert sent(a)[-1] == {
        "jsonrpc": "2.0", "id": "e1",
        "result": {"action": "accept", "content": {"q1": "2", "q2": ["1", "2"]}},
    }


def test_mode_switch_in_place_and_restarts_when_leaving_yolo(agy):
    a = agy
    ready_session(a)
    killed = []
    a.kill = lambda: killed.append(True)
    a.handle({"t": "menu", "text": "/mode", "rid": "mode"})
    menu = next(e for e in events(a) if e.get("rid") == "mode")
    a.handle({"t": "answer", "id": menu["id"], "value": "Plan"})
    assert a.mode == a.b.prefs["mode"] == "plan" and not a.yolo and not killed
    assert sent(a)[-1]["method"] == "session/set_mode"
    assert sent(a)[-1]["params"] == {"sessionId": "session-1", "modeId": "plan"}

    # Leaving YOLO restarts without --dangerously-skip-permissions
    a.yolo = True
    a.slash_mode("ask")
    assert not a.yolo and a.restarting and killed


def test_compact_runs_natively_in_place(agy):
    a = agy
    ready_session(a)
    a.slash_compact("keep decisions")
    req = sent(a)[-1]
    assert req["method"] == "session/prompt"
    assert req["params"]["prompt"] == [{"type": "text", "text": "/compact keep decisions"}]
    assert a.busy


def test_native_info_slash_command_uses_print_call(agy, monkeypatch):
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
    assert not a.busy


def test_spawn_uses_acp_stdio_and_passes_yolo_flag(agy, monkeypatch):
    a = agy
    a.yolo, a.notes_synced = True, True
    started = []
    monkeypatch.setattr(a, "start", started.append, raising=False)
    REAL_SPAWN(a)
    assert "acp" in started[0] and "--stdio" in started[0] and Agy.YOLO in started[0]


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


def test_agy_key_launches_chat_with_deck_context_and_supports_xhigh(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "NOTES", tmp_path / "notes")
    monkeypatch.setattr(config, "CHAT_DIR", tmp_path / "chat")
    monkeypatch.setattr(agents, "deck_context", lambda: "Deck context")
    assert "xhigh" in settings.EFFORTS["agy"] and "max" in settings.EFFORTS["agy"]
    key = buttons.clean_button({"label": "Antigravity", "type": "agy", "text": "Hello"})
    key.update(chat=True, session="agy-test", model="gemini-4-neon-high", effort="xhigh")
    argv = agents.build_argv(key)
    assert "agy" in argv and "--deck-context" in argv
    assert "gemini-4-neon-xhigh" in argv
    assert (tmp_path / "agy-context.md").read_text(encoding="utf-8").strip() == "Deck context"


def test_legacy_agy_context_writes_valid_rule_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    notes = tmp_path / "notes"
    (notes / "hosts").mkdir(parents=True)
    (notes / "AGENTS.md").write_text("Fleet rules", encoding="utf-8")
    monkeypatch.setattr(config, "NOTES", notes)
    d = agents.agy_context()
    rule = (d / ".agents/rules/infra-AGENTS.md").read_text(encoding="utf-8")
    assert rule.startswith("---\ntrigger: always_on\n")
    assert "Fleet rules" in rule
    assert "<deck-question>" not in (d / "AGENTS.md").read_text(encoding="utf-8")


def test_edited_path_recognizes_agy_edit_and_write_tools():
    assert edited_path({"name": "edit_file", "args": {"Path": "/repo/app.py"}}) == "/repo/app.py"
    assert edited_path({"name": "write_file", "args": {"Path": "/repo/new.py"}}) == "/repo/new.py"


def test_default_model_consumed_once_and_session_pick_wins(agy):
    a = Agy(agy.b, ["--model", "gemini-test-high"], False, agy.cwd)
    assert a.model == "gemini-test-high" and "--model" not in a.args
    agy.b.prefs["model"] = "session-model"
    a = Agy(agy.b, ["--model", "gemini-test-high"], True, agy.cwd)
    assert a.model == "session-model"
