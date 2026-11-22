"""Each agent's adapter, offline: Pi, Codex, OpenCode and OpenClaw driven with their own protocols' records (agy and
Claude have their own files), the contract every adapter shares, and the command line each agent starts with.
tests/live_agents.py runs the real CLIs."""
import io
import json

import pytest

from deck import agents, buttons, config
from deck.bridge import adapter as adapter_mod
from deck.bridge import codex as codex_mod
from deck.bridge import openclaw as openclaw_mod
from deck.bridge import opencode as opencode_mod
from deck.bridge import util
from deck.chat_bridge import Bridge, Codex, OpenClaw, OpenCode, Pi


def events(b):
    return [json.loads(line) for line in b.out_path.read_text(encoding="utf-8").splitlines()]


def sent(a):
    """What the adapter wrote to the agent's stdin, as records."""
    return [json.loads(line) for line in a.p.stdin.getvalue().splitlines()]


class Proc:
    def __init__(self):
        self.stdin, self.stdout = io.BytesIO(), io.BytesIO()

    def poll(self):
        return 0

    def wait(self):
        return 0


@pytest.fixture
def bridge(tmp_path):
    b = Bridge(str(tmp_path), "adapter-test")
    yield b
    b.out.close()


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """No agent is run: CI has none installed, and starting one would spend a model call."""
    monkeypatch.setattr(util, "resolve", lambda cmd: [cmd])
    monkeypatch.setattr(codex_mod, "live_models", lambda args: None)
    monkeypatch.setattr(adapter_mod.Adapter, "start", lambda self, argv: setattr(self, "p", Proc()) or
                        setattr(self, "argv_started", argv))
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", lambda argv, **k: Proc())
    link = tmp_path / "openclaw-bridge"
    (link / "node_modules" / "@openclaw" / "gateway-client").mkdir(parents=True)
    monkeypatch.setattr(openclaw_mod, "OPENCLAW_BRIDGE", link)


# ---- Pi (pi --mode rpc) ----

@pytest.fixture
def pi(offline, bridge, tmp_path):
    return Pi(bridge, [], False, str(tmp_path))


def test_pi_prompt_steer_abort_and_rename(pi):
    pi.command({"t": "say", "text": "hello"})
    pi.event({"type": "agent_start"})
    pi.command({"t": "say", "text": "also this"})
    pi.command({"t": "abort"})
    pi.command({"t": "rename", "title": "My chat"})
    s = sent(pi)
    assert s[0] == {"type": "prompt", "message": "hello"}
    assert s[1] == {"type": "prompt", "message": "also this", "streamingBehavior": "steer"}
    assert s[2] == {"type": "abort"}
    assert s[3]["type"] == "set_session_name" and s[3]["name"] == "My chat"


def test_pi_stream_state_and_tools(pi):
    b = pi.b
    pi.event({"type": "agent_start"})
    pi.event({"type": "message_start", "message": {"role": "assistant"}})
    pi.event({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Hi", "contentIndex": 0}})
    pi.event({"type": "message_end", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "Hi there"}, {"type": "toolCall", "id": "t1", "name": "read", "arguments": {"path": "x"}}]}})
    pi.event({"type": "tool_execution_end", "toolCallId": "t1", "result": {"content": [{"type": "text", "text": "data"}]}})
    pi.event({"type": "agent_settled"})
    pi.event({"type": "response", "command": "get_state", "success": True, "data": {
        "model": {"provider": "anthropic", "id": "claude-x", "name": "Claude X"}, "thinkingLevel": "high",
        "sessionName": "Named", "sessionFile": "/s/a.jsonl"}})
    ev = events(b)
    assert [e["s"] for e in ev if e["t"] == "state"] == ["working", "your_turn"]
    assert any(e["t"] == "delta" and e["d"] == "Hi" for e in ev)
    msg = [e for e in ev if e["t"] == "msg"][-1]
    assert msg["blocks"][0] == {"kind": "text", "text": "Hi there"} and msg["blocks"][1]["name"] == "read"
    assert next(e for e in ev if e["t"] == "tool_end")["out"] == "data"
    assert ev[-2]["parts"] == ["Claude X", "high"] if ev[-2]["t"] == "status" else any(
        e.get("parts") == ["Claude X", "high"] for e in ev)
    assert b.status["name"] == "Named" and b.status["resume"] == "/s/a.jsonl"
    assert sent(pi)[-1] == {"type": "get_state"}  # after settling: the model may have changed


def test_pi_aborted_and_error_turns_say_so(pi):
    pi.event({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "aborted"}})
    pi.event({"type": "message_end", "message": {"role": "assistant", "content": [], "stopReason": "error",
                                                 "errorMessage": "overloaded"}})
    notes = [(e["text"], e["err"]) for e in events(pi.b) if e["t"] == "note"]
    assert notes == [("Stopped", False), ("overloaded", True)]


def test_pi_extension_questions_round_trip(pi):
    pi.event({"type": "extension_ui_request", "id": "q1", "method": "select", "title": "Pick", "options": ["a", "b"]})
    pi.event({"type": "extension_ui_request", "id": "q2", "method": "confirm", "title": "Sure?"})
    ev = events(pi.b)
    assert [e["options"] for e in ev if e["t"] == "ask"] == [["a", "b"], ["Yes", "No"]]
    pi.handle({"t": "answer", "id": "q1", "value": "b"})
    pi.handle({"t": "answer", "id": "q2", "value": "Yes"})
    pi.event({"type": "extension_ui_request", "id": "q3", "method": "input", "title": "Name?"})
    pi.handle({"t": "say", "text": "Ada"})  # a typed answer, not a message: no stamp
    s = sent(pi)
    assert s[0] == {"type": "extension_ui_response", "id": "q1", "value": "b"}
    assert s[1] == {"type": "extension_ui_response", "id": "q2", "confirmed": True}
    assert s[2] == {"type": "extension_ui_response", "id": "q3", "value": "Ada"}


def test_pi_model_and_thinking_picks_are_kept(pi):
    def reply(data):  # answer the last rpc() as Pi would
        rid = sent(pi)[-1]["id"]
        pi.event({"type": "response", "id": rid, "success": True, "data": data})
    pi.slash_model("sonnet")
    reply({"models": [{"provider": "anthropic", "id": "sonnet", "name": "Sonnet"},
                      {"provider": "openai", "id": "gpt", "name": "GPT"}]})
    assert sent(pi)[-1]["type"] == "set_model" and sent(pi)[-1]["modelId"] == "sonnet"
    reply({})
    pi.slash_thinking("low")
    reply({})
    assert pi.b.prefs == {"model": {"provider": "anthropic", "id": "sonnet"}, "effort": "low"}
    pi.slash_model("nothing-like-it")
    reply({"models": [{"provider": "anthropic", "id": "sonnet"}]})
    assert events(pi.b)[-1] == {**events(pi.b)[-1], "t": "note", "err": True}


def test_pi_conversations_list_named_and_first_message(pi, tmp_path, monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
    import re
    d = tmp_path / "agent" / "sessions" / ("--" + re.sub(r"[/\\:]", "-", re.sub(r"^[/\\]", "", pi.cwd)) + "--")
    d.mkdir(parents=True)
    (d / "a.jsonl").write_text(json.dumps({"type": "session_info", "name": "Named one"}) + "\n", encoding="utf-8")
    user = {"type": "message", "message": {"role": "user", "content": "first words"}}
    (d / "b.jsonl").write_text(json.dumps(user, separators=(",", ":")) + "\n", encoding="utf-8")  # as Pi writes it
    labels = sorted(label.split("  ", 1)[1] for label, _ in pi.conversations())
    assert labels == ["Named one", "first words"]


# ---- Codex (codex app-server) ----

@pytest.fixture
def codex(offline, bridge, tmp_path):
    return Codex(bridge, [], False, str(tmp_path))


def respond(c, result):
    """Answer Codex's last request with result."""
    rid = [x for x in sent(c) if "method" in x and "id" in x][-1]["id"]
    c.event({"jsonrpc": "2.0", "id": rid, "result": result})


def test_codex_start_queues_until_thread_then_turns_and_steers(codex):
    codex.run("first message")  # stdout is empty: it only sends initialize
    respond(codex, {})
    assert [x.get("method") for x in sent(codex)][-3:] == ["initialized", "account/rateLimits/read", "thread/start"]
    assert sent(codex)[-1]["params"]["approvalPolicy"] == "on-request"
    respond(codex, {"thread": {"id": "th1"}, "model": "gpt-x", "reasoningEffort": "high"})
    turn = sent(codex)[-1]
    assert turn["method"] == "turn/start" and turn["params"]["input"][0]["text"] == "first message"
    codex.event({"method": "turn/started", "params": {"turn": {"id": "turn1"}}})
    codex.command({"t": "say", "text": "more"})
    assert sent(codex)[-1]["method"] == "turn/steer" and sent(codex)[-1]["params"]["expectedTurnId"] == "turn1"
    codex.command({"t": "abort"})
    assert sent(codex)[-1]["method"] == "turn/interrupt"
    codex.event({"method": "turn/completed", "params": {"turn": {"status": "interrupted"}}})
    assert codex.turn is None and not codex.busy and events(codex.b)[-2]["text"] == "Stopped"
    assert codex.b.status["resume"] == "th1"


def test_codex_stop_before_turn_started_interrupts_once_id_arrives(codex):
    codex.thread = "th1"
    codex.command({"t": "say", "text": "hello"})
    codex.command({"t": "abort"})
    assert codex.stop_pending
    codex.event({"method": "turn/started", "params": {"turn": {"id": "turn1"}}})
    assert sent(codex)[-1]["method"] == "turn/interrupt"
    assert sent(codex)[-1]["params"] == {"threadId": "th1", "turnId": "turn1"}
    assert not codex.stop_pending


def test_codex_yolo_policy_and_dev_instructions(offline, bridge, tmp_path):
    note = tmp_path / "ctx.md"
    note.write_text("Deck note", encoding="utf-8")
    c = Codex(bridge, ["--deck-context", str(note), Codex.YOLO], False, str(tmp_path))
    assert c.yolo and c.args == []
    assert c.policy() == {"approvalPolicy": "never", "sandbox": "danger-full-access", "developerInstructions": "Deck note"}


@pytest.mark.parametrize("choice,decision", [("Allow", "accept"), ("Allow for session", "acceptForSession"),
                                             ("Deny", "decline")])
def test_codex_approvals(codex, choice, decision):
    codex.event({"method": "item/commandExecution/requestApproval", "id": 7, "params": {"command": "rm x"}})
    ask = next(e for e in events(codex.b) if e["t"] == "ask")
    assert ask["id"] == "codex-7" and "rm x" in ask["title"]
    codex.handle({"t": "answer", "id": "codex-7", "value": choice})
    assert sent(codex)[-1] == {"jsonrpc": "2.0", "id": 7, "result": {"decision": decision}}


def test_codex_extra_permissions_scope(codex):
    perms = {"network": {"enabled": True}}
    codex.event({"method": "item/permissions/requestApproval", "id": 8, "params": {"permissions": perms}})
    codex.handle({"t": "answer", "id": "codex-8", "value": "Allow for session"})
    assert sent(codex)[-1]["result"] == {"permissions": perms, "scope": "session"}


def test_codex_user_input_questions_answer_by_id(codex):
    codex.event({"method": "item/tool/requestUserInput", "id": 9, "params": {"questions": [
        {"id": "color", "question": "Color?", "options": [{"label": "red"}, {"label": "blue"}]},
        {"id": "size", "question": "Size?", "options": []}]}})
    codex.handle({"t": "answer", "id": "codex-9#0", "value": "blue"})
    codex.handle({"t": "say", "text": "large"})
    assert sent(codex)[-1]["result"] == {"answers": {"color": {"answers": ["blue"]}, "size": {"answers": ["large"]}}}


def test_codex_unreopenable_thread_starts_a_new_one_once(offline, bridge, tmp_path):
    bridge.save(resume="gone")
    b = Bridge(str(tmp_path), "adapter-test")
    c = Codex(b, [], True, str(tmp_path))
    c.run("")
    respond(c, {})
    assert sent(c)[-1]["method"] == "thread/resume" and sent(c)[-1]["params"]["threadId"] == "gone"
    c.event({"jsonrpc": "2.0", "id": sent(c)[-1]["id"], "error": {"message": "no rollout"}})
    assert sent(c)[-1]["method"] == "thread/start" and c.fresh
    b.out.close()


def test_codex_model_and_effort_go_with_the_next_turn(codex):
    codex.thread = "th"
    codex.slash_model("mini")
    respond(codex, {"data": [{"model": "gpt-big", "displayName": "Big"}, {"model": "gpt-mini", "displayName": "Mini"}]})
    codex.slash_effort("low")
    respond(codex, {"data": [{"model": "gpt-mini", "supportedReasoningEfforts": [{"reasoningEffort": "low"}]}]})
    codex.command({"t": "say", "text": "go"})
    p = sent(codex)[-1]["params"]
    assert p["model"] == "gpt-mini" and p["effort"] == "low"
    assert codex.b.prefs == {"model": "gpt-mini", "effort": "low"}


def test_codex_model_menu_refreshes_each_time_and_uses_live_effort(codex, monkeypatch):
    calls = []
    def live(_args):
        calls.append(1)
        return {"data": [{"model": f"new-{len(calls)}", "displayName": f"New {len(calls)}",
                           "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}]}
    monkeypatch.setattr(codex_mod, "live_models", live)
    codex.slash_model("new-1")
    assert codex.model == "new-1"
    codex.slash_model("new-2")
    assert codex.model == "new-2"
    codex.parts[0] = "new-3"
    codex.slash_effort("high")
    assert codex.effort == "high" and len(calls) == 3


def test_live_codex_catalog_uses_current_account_and_skips_hidden(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
        "access_token": "test-token", "account_id": "test-account"}}))
    monkeypatch.setattr(codex_mod.util, "resolve", lambda _: ["codex"])
    monkeypatch.setattr(codex_mod.subprocess, "run", lambda *a, **k: type("Result", (), {"stdout": "codex-cli 1.2.3"})())
    requests = []
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(json.dumps({"models": [
                {"slug": "new", "display_name": "New", "visibility": "list",
                 "supported_reasoning_levels": [{"effort": "high"}]},
                {"slug": "hidden", "visibility": "hide"}]}).encode())
    monkeypatch.setattr(codex_mod.urllib.request, "build_opener", lambda *_: Opener())
    result = codex_mod.live_models([])
    assert result["data"][0]["model"] == "new" and len(result["data"]) == 1
    assert result["data"][0]["supportedReasoningEfforts"] == [{"reasoningEffort": "high"}]
    assert requests[0].full_url.endswith("client_version=1.2.3")
    assert requests[0].get_header("Chatgpt-account-id") == "test-account"
    (tmp_path / "config.toml").write_text('model_provider = "custom"\n')
    assert codex_mod.live_models([]) is None and len(requests) == 1


# ---- OpenCode (opencode serve) ----

@pytest.fixture
def opencode(offline, bridge, tmp_path):
    o = OpenCode(bridge, [], False, str(tmp_path))
    o.calls = []
    o.replies = {}
    o.http = lambda method, path, body=None, timeout=30: o.calls.append((method, path, body)) or o.replies.get(path)
    o.sid = "ses1"
    return o


def test_opencode_prompt_carries_the_sessions_picks(opencode):
    opencode.model, opencode.variant = {"providerID": "p", "modelID": "m"}, "high"
    opencode.command({"t": "say", "text": "hi"})
    opencode.command({"t": "abort"})
    assert opencode.calls[0] == ("POST", "/session/ses1/prompt_async",
                                 {"parts": [{"type": "text", "text": "hi"}], "model": {"providerID": "p", "modelID": "m"},
                                  "variant": "high"})
    assert opencode.calls[1][:2] == ("POST", "/session/ses1/abort") and opencode.busy


def test_opencode_permissions_ask_or_auto_allow(opencode):
    opencode.event({"type": "permission.asked", "properties": {"id": "per1", "sessionID": "ses1", "permission": "bash",
                                                               "patterns": ["ls"]}})
    assert next(e for e in events(opencode.b) if e["t"] == "ask")["options"] == ["Allow", "Always", "Deny"]
    opencode.handle({"t": "answer", "id": "per1", "value": "Always"})
    assert opencode.calls[-1] == ("POST", "/session/ses1/permissions/per1", {"response": "always"})
    opencode.yolo = True
    opencode.event({"type": "permission.asked", "properties": {"id": "per2", "sessionID": "ses1"}})
    assert opencode.calls[-1] == ("POST", "/session/ses1/permissions/per2", {"response": "once"})
    opencode.event({"type": "permission.asked", "properties": {"id": "per3", "sessionID": "other"}})
    assert opencode.calls[-1][1].endswith("per2")  # another session's: not ours to answer


def test_opencode_question_tool_reply_and_reject(opencode):
    q = {"id": "que1", "sessionID": "ses1", "questions": [{"question": "Which?", "options": [{"label": "A"}, {"label": "B"}]}]}
    opencode.event({"type": "question.asked", "properties": q})
    opencode.handle({"t": "answer", "id": "que1#0", "value": "B"})
    assert opencode.calls[-1] == ("POST", "/question/que1/reply", {"answers": [["B"]]})
    opencode.event({"type": "question.asked", "properties": {**q, "id": "que2"}})
    opencode.handle({"t": "answer", "id": "que2#0", "value": None})
    assert opencode.calls[-1][:2] == ("POST", "/question/que2/reject")


def test_opencode_streams_parts_into_one_message(opencode):
    o = opencode
    o.event({"type": "message.updated", "properties": {"info": {"id": "m1", "role": "assistant", "sessionID": "ses1"}}})
    o.event({"type": "message.part.updated", "properties": {"part": {"id": "p1", "messageID": "m1", "type": "text",
                                                                     "text": "", "time": {}}}})
    o.event({"type": "message.part.delta", "properties": {"messageID": "m1", "partID": "p1", "field": "text", "delta": "Hel"}})
    o.event({"type": "message.part.updated", "properties": {"part": {"id": "p1", "messageID": "m1", "type": "text",
                                                                     "text": "Hello", "time": {"end": 1}}}})
    o.event({"type": "message.part.updated", "properties": {"part": {
        "id": "p2", "messageID": "m1", "type": "tool", "callID": "c1", "tool": "bash",
        "state": {"status": "completed", "input": {"command": "ls"}, "output": "a b"}}}})
    o.event({"type": "session.idle", "properties": {}})
    ev = events(o.b)
    assert any(e["t"] == "delta" and e["d"] == "Hel" for e in ev)
    assert [e for e in ev if e["t"] == "msg"][-1]["blocks"] == [
        {"kind": "text", "text": "Hello"}, {"kind": "tool", "id": "c1", "name": "bash", "args": {"command": "ls"}}]
    assert next(e for e in ev if e["t"] == "tool_end")["out"] == "a b"


def test_opencode_sampling_refusal_skips_the_model_and_resends_once(opencode, tmp_path, monkeypatch):
    settings, skip = tmp_path / "settings.json", tmp_path / "skip.json"
    settings.write_text(json.dumps({"sampling": {"build": {"temperature": 0.3}}}))
    monkeypatch.setenv("DECK_SAMPLING", str(settings))
    monkeypatch.setenv("DECK_SAMPLING_SKIP", str(skip))
    o = opencode
    o.used, o.said = {"providerID": "p", "modelID": "m"}, "hello"
    err = {"name": "APIError", "data": {"message": "temperature is not supported"}}
    o.event({"type": "session.error", "properties": {"error": err}})
    o.event({"type": "session.error", "properties": {"error": err}})  # its second report of the same refusal
    assert json.loads(skip.read_text()) == ["p/m"]
    assert [c for c in o.calls if c[1].endswith("prompt_async")] == [
        ("POST", "/session/ses1/prompt_async", {"parts": [{"type": "text", "text": "hello"}]})]
    assert not [e for e in events(o.b) if e["t"] == "note" and e["err"]]


def test_opencode_model_and_effort_picks(opencode):
    o = opencode
    o.replies["/config/providers?" + __import__("urllib.parse").parse.urlencode({"directory": o.cwd})] = {
        "providers": [{"id": "p", "models": {"m": {"variants": {"low": {}, "high": {}}}, "n": {}}}]}
    o.slash_model("p/m")
    o.slash_effort("high")
    assert o.b.prefs == {"model": {"providerID": "p", "modelID": "m"}, "effort": "high"}
    o.slash_model("p/n")
    assert o.variant is None and o.b.prefs["effort"] is None  # variants are per model


# ---- OpenClaw (its Gateway, through deploy/agents/openclaw-bridge) ----

@pytest.fixture
def openclaw(offline, bridge, tmp_path):
    return OpenClaw(bridge, [], False, str(tmp_path))


def rpc_reply(c, payload, ok=True):
    rid = sent(c)[-1]["id"]
    then, fail = c.pending.pop(rid)
    if ok and then:
        then(payload)
    elif not ok and fail:
        fail(payload)


def test_openclaw_hello_replays_history_then_sends_the_first_message(openclaw):
    c = openclaw
    c.first = "hi"
    c.on_hello({"scopes": []})
    assert [x["method"] for x in sent(c)] == ["sessions.messages.subscribe", "chat.history"]
    rpc_reply(c, {"messages": [{"role": "user", "content": "earlier"},
                               {"role": "assistant", "content": [{"type": "text", "text": "reply"}]}],
                  "sessionInfo": {"model": "claude-x", "thinkingLevel": "high"}})
    ev = events(c.b)
    assert [e["text"] for e in ev if e["t"] == "user"] == ["earlier"]
    assert any(e.get("parts") == ["claude-x", "high"] for e in ev) and ev[-1] == {**ev[-1], "t": "state", "s": "your_turn"}
    assert sent(c)[-1]["method"] == "chat.send" and sent(c)[-1]["params"]["message"] == "hi"


def test_openclaw_streams_a_turn_and_stops(openclaw):
    c = openclaw
    c.event("agent", {"stream": "lifecycle", "data": {"phase": "start"}})
    c.event("agent", {"stream": "tool", "data": {"phase": "start", "toolCallId": "t1", "name": "mcp__openclaw__read"}})
    c.event("chat", {"state": "delta", "deltaText": "Hel"})
    c.event("chat", {"state": "delta", "deltaText": "lo"})
    c.event("agent", {"stream": "lifecycle", "data": {"phase": "end"}})
    c.event("chat", {"state": "aborted"})
    c.event("chat", {"state": "delta", "deltaText": "x", "sessionKey": "agent:sub"})  # a subagent's: not shown
    ev = events(c.b)
    last = [e for e in ev if e["t"] == "msg"][-1]["blocks"]
    assert last[0]["name"] == "read" and last[1] == {"kind": "text", "text": "Hello"}
    assert [e["s"] for e in ev if e["t"] == "state"] == ["working", "your_turn"]
    assert [e["text"] for e in ev if e["t"] == "note"] == ["Stopped"]
    c.command({"t": "abort"})
    assert sent(c)[-1]["method"] == "chat.abort"


def test_openclaw_ask_user_question_card(openclaw):
    c = openclaw
    c.asked({"id": "q1", "sessionKey": "agent:main:main", "questions": [{"question": "Go?", "options": [{"label": "Yes"}]}]})
    assert next(e for e in events(c.b) if e["t"] == "ask")["options"] == ["Yes"]
    c.handle({"t": "answer", "id": events(c.b)[-2]["id"] if events(c.b)[-1]["t"] != "ask" else events(c.b)[-1]["id"],
              "value": "Yes"})
    assert sent(c)[-1]["method"].startswith("question.")
    c.asked({"id": "q2", "sessionKey": "main", "questions": [{"question": "Key?", "isSecret": True}]})
    assert "secret" in [e for e in events(c.b) if e["t"] == "note"][-1]["text"]


def test_openclaw_one_conversation_only(openclaw):
    openclaw.handle({"t": "say", "text": "/new"})
    openclaw.handle({"t": "say", "text": "/resume"})
    notes = [e["text"] for e in events(openclaw.b) if e["t"] == "note"]
    assert len(notes) == 2 and all("one shared conversation" in n for n in notes)
    assert openclaw.b.switch_to is None


@pytest.mark.parametrize("text,out", [
    ("Here\nMEDIA:/tmp/a.png", "Here"),
    ("```\nMEDIA:/keep\n```", "```\nMEDIA:/keep\n```"),
    ("plain", "plain"),
])
def test_openclaw_media_lines_are_dropped_outside_code(text, out):
    assert openclaw_mod.without_media_lines(text) == out


# ---- what every adapter shares (adapter.Adapter) ----

ADAPTERS = {"pi": Pi, "codex": Codex, "opencode": OpenCode, "openclaw": OpenClaw}


@pytest.fixture(params=list(ADAPTERS))
def any_adapter(request, offline, bridge, tmp_path):
    a = ADAPTERS[request.param](bridge, [], False, str(tmp_path))
    a.got = []
    a.command = a.got.append
    return a


def test_messages_get_the_stamp_and_lone_slash_is_dropped(any_adapter):
    a = any_adapter
    a.handle({"t": "say", "text": "hello", "from": "PC browser"})
    a.handle({"t": "say", "text": "/"})
    assert len(a.got) == 1 and a.got[0]["text"].endswith("· PC browser] hello")
    assert "A lone / isn't a command" in [e for e in events(a.b) if e["t"] == "note"][-1]["text"]


@pytest.mark.parametrize("cls", [Pi, OpenCode])  # Codex lists threads over its protocol; OpenClaw has one conversation
def test_resume_pick_switches_conversation(offline, bridge, tmp_path, cls):
    a = cls(bridge, [], False, str(tmp_path))
    a.conversations = lambda: [("Oct 30  same", "id1"), ("Oct 30  same", "id2")]
    killed = []
    a.kill = lambda: killed.append(1)
    a.handle({"t": "say", "text": "/resume"})
    ask = [e for e in events(a.b) if e["t"] == "ask"][-1]
    assert len(set(ask["options"])) == 2  # same labels kept apart
    a.handle({"t": "answer", "id": ask["id"], "value": ask["options"][1]})
    assert a.b.switch_to == "id2" and killed


def test_menu_request_with_no_command_answers_empty(any_adapter):
    any_adapter.handle({"t": "menu", "text": "/nosuch", "rid": "r1"})
    assert events(any_adapter.b)[-1] == {**events(any_adapter.b)[-1], "t": "menu", "rid": "r1", "options": []}


# ---- the command line each agent starts with (agents.build_argv) ----

@pytest.fixture
def launch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "CHAT_DIR", tmp_path / "chat")
    monkeypatch.setattr(agents, "deck_context", lambda: "Deck context")


@pytest.mark.parametrize("kind", agents.AGENTS)
def test_every_agent_starts_as_a_chat_with_context_and_yolo(launch, tmp_path, kind):
    key = buttons.clean_button({"label": kind, "type": kind, "text": "Hello"})
    key.update(chat=True, session=f"{kind}-1", yolo=True)
    argv = agents.build_argv(key)
    args = argv[argv.index("--") + 1:]
    assert argv[argv.index(str(agents.CHAT_BRIDGE)) + 1] == kind and "Hello" in argv
    for flag in agents.YOLO_FLAGS.get(kind, []):
        assert flag in args
    expect = {"claude": "--append-system-prompt-file", "pi": "--append-system-prompt", "codex": "--deck-context",
              "agy": "--add-dir"}.get(kind)
    if expect:
        assert expect in args
    if kind == "opencode":
        cfg = next(a for a in argv if a.startswith("OPENCODE_CONFIG_CONTENT="))
        conf = json.loads(cfg.split("=", 1)[1])
        assert "\\" not in cfg and conf["instructions"][0].endswith("opencode-context.md")
    if kind in ("claude", "pi", "codex", "opencode"):
        name = {"claude": "agent-context.md", "pi": "agent-context.md", "codex": "codex-context.md",
                "opencode": "opencode-context.md"}[kind]
        assert (tmp_path / name).read_text(encoding="utf-8").startswith("Deck context")


@pytest.mark.parametrize("kind", [k for k in agents.AGENTS if k in agents.EFFORT_FLAG])
def test_effort_setting_reaches_the_agent(launch, kind, monkeypatch):
    monkeypatch.setattr(agents, "effort_args", lambda k, *_: agents.EFFORT_FLAG[k]("high") if k == kind else [])
    key = buttons.clean_button({"label": kind, "type": kind, "text": "x"})
    key.update(chat=True, session="e")
    assert agents.EFFORT_FLAG[kind]("high")[-1] in agents.build_argv(key)


@pytest.mark.parametrize("cls,default,expected", [
    (Codex, "gpt-test", "gpt-test"),
    (OpenCode, "openrouter/vendor/model", {"providerID": "openrouter", "modelID": "vendor/model"}),
])
def test_protocol_model_default_is_not_a_server_flag(offline, bridge, tmp_path, cls, default, expected):
    a = cls(bridge, ["--model", default], False, str(tmp_path))
    assert a.model == expected
    assert "--model" not in a.args
    bridge.prefs["model"] = "session-model" if cls is Codex else {"providerID": "p", "modelID": "session"}
    a = cls(bridge, ["--model", default], True, str(tmp_path))
    assert a.model == bridge.prefs["model"]


def test_openclaw_default_applied_before_history_and_first_prompt(offline, bridge, tmp_path):
    a = OpenClaw(bridge, ["--model", "anthropic/claude-test"], False, str(tmp_path))
    a.first = "hello"
    a.on_hello({})
    requests = sent(a)
    patch = next(q for q in requests if q.get("method") == "sessions.patch")
    assert patch["params"]["model"] == "anthropic/claude-test"
    assert not any(q.get("method") == "chat.send" for q in requests)
    a.pending[patch["id"]][0]({})
    assert sent(a)[-1]["method"] == "chat.history"


def test_typed_answer_to_an_agents_question_shows_on_its_card(bridge):
    # bug report 2026-11-18: an answer typed in the message box showed nowhere in the chat
    a = adapter_mod.Adapter.__new__(adapter_mod.Adapter)
    a.b, a.busy, a.menu, a.picks, a.quiz = bridge, False, None, {}, None
    got = []
    a.questions("k", [{"question": "Name?", "options": ["A"], "other": True}, {"question": "Sure?", "options": ["Yes"]}],
                got.append)
    a.handle({"t": "say", "text": "Ada"})
    a.handle({"t": "answer", "id": "k#1", "value": "Yes"})
    bridge.out.flush()
    assert [(e["id"], e.get("choice")) for e in events(bridge) if e["t"] == "answered"] == [("k#0", "Ada"), ("k#1", "Yes")]
    assert got == [[["Ada"], ["Yes"]]]
