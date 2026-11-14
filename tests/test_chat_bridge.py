import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from deck.chat_bridge import OUT_LIMIT, Bridge, Codex


def test_concurrent_status_updates_do_not_lose_the_temporary_file(tmp_path, monkeypatch):
    bridge = Bridge(str(tmp_path), "concurrent")
    replace = Path.replace

    def slow_replace(path, target):
        time.sleep(0.001)  # let agent output and incoming commands overlap at the file swap
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", slow_replace)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda index: bridge.save(**{f"field{index}": index}), range(40)))
        saved = json.loads(bridge.state_path.read_text(encoding="utf-8"))
        assert all(saved[f"field{index}"] == index for index in range(40))
    finally:
        bridge.out.close()


def test_message_during_agent_startup_is_kept(tmp_path):
    incoming = tmp_path / "startup.in.jsonl"
    incoming.write_text(json.dumps({"t": "say", "text": "Old process"}) + "\n", encoding="utf-8")
    (tmp_path / "startup.state").write_text(json.dumps({"resume": "abc"}), encoding="utf-8")  # a restart
    b = Bridge(str(tmp_path), "startup")
    # Agy synchronizes machine notes before the command pump starts; only the earlier process's input is skipped.
    with incoming.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": "say", "text": "During startup"}) + "\n")
    assert next(b.commands()) == {"t": "say", "text": "During startup"}
    b.out.close()


def test_new_chats_first_start_keeps_lines_sent_before_it_ran(tmp_path):
    """A new chat's bridge takes seconds to start (a login shell on a small host); the page already accepts messages.
    With no state yet (a first start) nothing in its commands is an earlier process's: "/model", "/effort" and a
    message sent at once all reach it (2026-11-11: they were skipped, and the chat sat idle)."""
    incoming = tmp_path / "fresh.in.jsonl"
    lines = [{"t": "say", "text": "/model sonnet"}, {"t": "say", "text": "/effort low"}, {"t": "say", "text": "hi"}]
    incoming.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    b = Bridge(str(tmp_path), "fresh")
    got = b.commands()
    assert [next(got) for _ in lines] == lines
    b.out.close()


def test_resume_and_codex_events(tmp_path, monkeypatch):
    b = Bridge(str(tmp_path), "s")
    assert b.prev_resume == ""
    b.save(resume="abc")
    b.state("your_turn", "hi")
    b.out.close()
    b2 = Bridge(str(tmp_path), "s")  # a Restart: the new bridge reads the old state before overwriting it
    assert b2.prev_resume == "abc"
    b2.state("working")  # kept until the agent reports its id again
    assert json.loads((tmp_path / "s.state").read_text(encoding="utf-8"))["resume"] == "abc"
    monkeypatch.setattr(Codex, "start", lambda *_: None)
    monkeypatch.setattr("deck.bridge.util.resolve", lambda cmd: [cmd])  # CI has no codex installed
    c = Codex(b2, [], False, str(tmp_path))
    c.thread, c.parts = "thread", ["model", "low", "yolo"]
    def event(method, **params):
        c.event({"method": method, "params": params})

    event("turn/plan/updated", turnId="turn", plan=[{"step": "Inspect", "status": "inProgress"}])
    event("turn/plan/updated", turnId="turn", plan=[{"step": "Inspect", "status": "completed"}])
    event("account/rateLimits/updated", rateLimits={"primary": {"windowDurationMins": 300, "usedPercent": 12}})
    event("account/rateLimits/updated", rateLimits={"primary": None, "secondary": {"windowDurationMins": 10080, "usedPercent": 34}})
    event("thread/tokenUsage/updated", threadId="thread", tokenUsage={"last": {"totalTokens": 250}, "modelContextWindow": 1000})
    event("item/started", item={"type": "commandExecution", "id": "cmd", "command": "printf marker", "aggregatedOutput": "first\n"})
    event("item/commandExecution/outputDelta", itemId="cmd", delta="marker")
    assert c.outputs["cmd"] == "first\nmarker"
    event("item/commandExecution/outputDelta", itemId="cmd", delta="x" * (OUT_LIMIT * 3))
    assert len(c.outputs["cmd"]) <= OUT_LIMIT + 3
    event("item/completed", item={"type": "commandExecution", "id": "cmd", "command": "printf marker", "aggregatedOutput": "authoritative", "exitCode": 0})
    assert "cmd" not in c.outputs
    event("item/completed", item={"type": "collabAgentToolCall", "id": "agent", "tool": "spawnAgent", "status": "completed", "agentsStates": {"child": {"status": "completed"}}})
    c.asks["codex-42"] = 42
    event("serverRequest/resolved", requestId=42)
    assert not c.asks
    b2.out.close()
    events = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e.get("parts") == ["model", "low", "Context 25% used", "5h 12% used", "7d 34% used", "yolo"] for e in events)
    assert [e["blocks"][0]["text"] for e in events if e.get("m") == "plan-turn"] == ["- Working: Inspect", "- Done: Inspect"]
    assert [e["out"] for e in events if e.get("t") == "tool_end" and e["id"] == "cmd"][-1] == "authoritative"
    assert any(e.get("t") == "answered" and e["id"] == "codex-42" for e in events)


def test_claude_model_pick_does_not_change_the_hosts_default(tmp_path, monkeypatch):
    """Claude Code saves /model as its default in ~/.claude/settings.json; a Deck chat's pick is that session's only."""
    import json
    from pathlib import Path

    from deck import chat_bridge
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"model": "opus", "effortLevel": "high"}), encoding="utf-8")
    c = chat_bridge.Claude.__new__(chat_bridge.Claude)
    c.saved_defaults = {"model": "opus", "effortLevel": "high"}  # what type() keeps before typing /model
    settings.write_text(json.dumps({"model": "fable", "effortLevel": "high"}), encoding="utf-8")  # Claude's save
    c.restore_defaults()
    assert json.loads(settings.read_text(encoding="utf-8")) == {"model": "opus", "effortLevel": "high"}
    assert c.saved_defaults is None
    c.saved_defaults = {}  # the file had no model before
    settings.write_text(json.dumps({"model": "haiku"}), encoding="utf-8")
    c.restore_defaults()
    assert "model" not in json.loads(settings.read_text(encoding="utf-8"))


def test_claude_effort_pick_and_2_1_322_replies_leave_the_hosts_defaults(tmp_path, monkeypatch):
    """Claude Code 2.1.322 writes a slash command's reply as a "user" record (not system/local_command) and saves an
    /effort pick per model under modelSettings: both still go back to the host's own defaults, and the note says the
    pick is this session's (user 2026-11-11: cloud's Claude defaults had been changed by a chat's picks)."""
    from deck import chat_bridge
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    host = {"model": "fable", "theme": "dark", "modelSettings": {"claude-opus-5-5": {"effortLevel": "xhigh"}}}
    settings.write_text(json.dumps(host), encoding="utf-8")
    c = chat_bridge.Claude.__new__(chat_bridge.Claude)
    c.b = Bridge(str(tmp_path), "picks")
    c.saved_defaults, c.goal, c.pending_until = None, "", 0.0
    c.blocks, c.open_q, c.qstep, c.asks = {}, {}, {}, {}
    c.cancelled_at, c.ready, c.reading = None, True, True
    c.write = lambda s: None
    monkeypatch.setattr("time.sleep", lambda _: None)
    c.type("/model opus")
    assert c.pending_until > 0  # the next line waits for its reply
    saved = {**host, "model": "opus", "modelSettings": {"claude-opus-5-5": {"effortLevel": "medium"}}}
    settings.write_text(json.dumps(saved), encoding="utf-8")  # what Claude saves for /model, /effort
    c.record({"type": "user", "message": {"role": "user", "content":
              "<local-command-stdout>Set model to `Opus 5.5` and saved as your default for new sessions</local-command-stdout>"}})
    assert json.loads(settings.read_text(encoding="utf-8")) == host
    assert c.pending_until == 0.0
    c.b.out.flush()
    notes = [e["text"] for e in map(json.loads, c.b.out_path.read_text(encoding="utf-8").splitlines()) if e["t"] == "note"]
    assert notes == ["Set model to `Opus 5.5` for this session"]
    c.b.out.close()


def test_claude_typing_waits_for_its_input_box(tmp_path, monkeypatch):
    """Keys typed before Claude draws its input box are kept and taken in as one input: "/model opus", "/effort medium"
    and a message typed during a slow start reached it as one /model ("model=opus/effort medium[...] is market report
    ok", user 2026-11-11). type() holds them until the box shows; a Stop gives way at once."""
    import threading

    from deck import chat_bridge
    from deck.bridge.claude import Screen, input_box
    c = chat_bridge.Claude.__new__(chat_bridge.Claude)
    c.b = Bridge(str(tmp_path), "ready")
    c.cancelled_at, c.prompt_lines, c.saved_defaults = None, 1, None
    c.ready, c.reading, c.pending_until = False, True, 0.0
    c.grid = Screen(10, 40)
    sent = []
    c.write = sent.append
    t = threading.Thread(target=c.type, args=("/model opus",))
    t.start()
    time.sleep(0.4)
    assert sent == [] and t.is_alive()  # starting: nothing typed yet
    c.grid.feed("\x1b[H Claude Code\r\n" + "─" * 40 + "\r\n❯ Try \"fix lint errors\"\r\n" + "─" * 40)
    assert input_box(c.grid.lines())
    c.ready = True  # what pump() sets when the box shows
    t.join(3)
    assert "".join(sent) == "/model opus\r"
    # a choice dialog's ❯ rows are not the input box
    assert not input_box(["  Do you trust this folder?", "  ❯ 1. Yes, I trust this folder", "    2. No, exit"])
    # Windows with no TERM (a detached bridge): Claude draws its prompt as >, and the box must still be seen, or
    # every first line waits the whole READY_WAIT
    ascii_box = Screen(10, 40)
    ascii_box.feed("\x1b[H Claude Code\r\n" + "─" * 40 + "\r\n> Try \"edit <filepath> to...\"\r\n" + "─" * 40)
    assert input_box(ascii_box.lines())
    assert not input_box(["  Do you trust this folder?", "  > 1. Yes, I trust this folder", "    2. No, exit"])
    # Stop while it waits: the wait gives way (the command loop is the one waiting, so it reads the file itself)
    c.ready = False
    sent.clear()
    t = threading.Thread(target=c.type, args=("hello",))
    t.start()
    time.sleep(0.3)
    assert t.is_alive()
    with open(c.b.in_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": "abort"}) + "\n")
    t.join(3)
    assert not t.is_alive()
    c.b.out.close()


def test_claude_bar_starts_on_the_model_it_was_given(tmp_path, monkeypatch):
    """The bar's first chip is the --model Deck starts Claude on (Default model), not the host's settings file default."""
    from deck import chat_bridge
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"model": "fable"}), encoding="utf-8")
    c = chat_bridge.Claude.__new__(chat_bridge.Claude)
    c.b = Bridge(str(tmp_path), "bar")
    c.argv = ["claude", "--settings", "{}", "--model", "opus", "--effort", "medium"]
    c.effort, c.usage, c.goal, c.model, c.mode, c.yolo, c.cwd = "medium", [], "", "", "", True, str(tmp_path)
    bars = []
    c.b.bar = lambda *parts, mode="": bars.append(parts)

    class Stop(Exception):
        pass

    def no_term(*a):
        raise Stop
    monkeypatch.setattr(chat_bridge.Claude, "settings", staticmethod(lambda: {"model": "fable"}))
    monkeypatch.setattr("deck.bridge.claude.Term", no_term)
    try:
        c.run("")
    except Stop:
        pass
    assert bars[0][0] == "opus medium"
    c.b.out.close()


def test_queued_message_wrapped_as_a_paste_is_taken_in(tmp_path, monkeypatch):
    """A long message sent mid-turn (an agent's handoff) arrives as a queued_command attachment wrapped in
    <pasted_content>: the chat must get its "user" event, or the message stays shown as Sending."""
    from deck import chat_bridge
    c = chat_bridge.Claude.__new__(chat_bridge.Claude)
    got = []
    c.b = type("B", (), {"user": lambda self, t: got.append(t), "hidden": lambda self, *a: None})()
    c.blocks, c.open_q, c.qstep, c.asks = {}, {}, {}, {}
    body = "[Wed 2026-10-21 18:13 -0700 · agent in laptop/deck-claude-1] Done.\n\nURL: https://x.test/"
    c.record({"type": "attachment", "attachment": {"type": "queued_command",
                                                   "prompt": f'<pasted_content id="4">\n{body}\n</pasted_content id="4">'}})
    assert got == [body]


def test_goal_is_taken_in_and_shown_on_the_bar():
    """/goal is recorded only as a note for the agent (isMeta): the chat must still get its "user" event, or it stays
    shown as Sending, and the bar shows the goal till goal_status says it is met."""
    from deck import chat_bridge
    c = chat_bridge.Claude.__new__(chat_bridge.Claude)
    got, bars, notes = [], [], []
    c.b = type("B", (), {"user": lambda self, t: got.append(t), "hidden": lambda self, *a: None,
                         "bar": lambda self, *p, mode="": bars.append(p), "note": lambda self, t, err=False: notes.append(t)})()
    c.blocks, c.open_q, c.qstep, c.asks = {}, {}, {}, {}
    c.goal, c.model, c.mode, c.effort, c.usage = "", "claude-opus-5-5", "", "high", []
    c.record({"type": "user", "isMeta": True, "message": {"role": "user", "content":
              'A session-scoped Stop hook is now active with condition: "fix the apps". Briefly acknowledge the goal, then go.'}})
    assert got == ["/goal fix the apps"] and bars[-1] == ("Opus 5.5 high", "Goal: fix the apps")
    c.record({"type": "attachment", "attachment": {"type": "goal_status", "met": True, "condition": "fix the apps"}})
    assert bars[-1] == ("Opus 5.5 high",) and notes == ["Goal met"]


def test_codex_goal_goes_to_the_app_server_and_shows_on_the_bar():
    """Codex's /goal is a thread request in its app server (a typed "/goal" would only reach the model as text)."""
    from deck import chat_bridge
    c = chat_bridge.Codex.__new__(chat_bridge.Codex)
    sent, bars, notes, states = [], [], [], []
    c.b = type("B", (), {"bar": lambda self, *p, **k: bars.append(p), "note": lambda self, t, err=False: notes.append(t),
                         "state": lambda self, s: states.append(s)})()
    c.request = lambda m, p, then=None: sent.append((m, p)) or (then and then({"goal": {"objective": p.get("objective", "x"), "status": p["status"]}}))
    c.thread, c.goal, c.busy, c.parts, c.usage, c.limits = "t1", "", False, ["gpt", "high", ""], {}, {}
    c.slash_goal("raise coverage")
    assert sent[-1] == ("thread/goal/set", {"threadId": "t1", "origin": "user", "objective": "raise coverage", "status": "active"})
    assert bars[-1] == ("gpt", "high", "Goal: raise coverage", "") and c.busy
    c.slash_goal("pause")
    assert sent[-1][1]["status"] == "paused" and "objective" not in sent[-1][1]
    c.event({"method": "thread/goal/updated", "params": {"threadId": "t1", "goal": {"objective": "raise coverage", "status": "complete"}}})
    assert bars[-1] == ("gpt", "high", "") and notes == ["Goal met"]
    c.slash_goal("clear")
    assert sent[-1] == ("thread/goal/clear", {"threadId": "t1", "origin": "user"})


def test_opencode_bar_shows_the_sessions_cost():
    """Each reply carries its cost; the bar shows their sum (each reply's updates replace, not add), none while $0."""
    from deck import chat_bridge
    c = chat_bridge.OpenCode.__new__(chat_bridge.OpenCode)
    bars = []
    c.b = type("B", (), {"bar": lambda self, *p, mode="": bars.append(tuple(x for x in p if x))})()  # as Bridge.bar
    c.roles, c.used, c.costs, c.yolo, c.shown = {}, {}, {}, False, ["", "", ""]
    reply = {"role": "assistant", "providerID": "openrouter", "modelID": "kimi", "agent": "build"}
    c.message_info({**reply, "id": "a", "cost": 0})
    assert bars[-1] == ("kimi", "build")
    c.message_info({**reply, "id": "a", "cost": 0.01})
    c.message_info({**reply, "id": "a", "cost": 0.02})
    c.message_info({**reply, "id": "b", "cost": 0.0345})
    assert bars[-1] == ("kimi", "$0.05", "build")


def test_queued_messages_offer_what_the_agent_can_do(tmp_path, monkeypatch):
    """Edit and Send now show only where they work: Claude both, Pi Edit (its clear_queue), the rest neither."""
    from deck.chat_bridge import Claude, Pi
    monkeypatch.setattr("deck.bridge.util.resolve", lambda cmd: [cmd])
    assert Claude.QUEUE_KEYS == ["sendnow", "edit"] and Codex.QUEUE_KEYS == []
    b = Bridge(str(tmp_path), "pi")
    monkeypatch.setattr(Pi, "start", lambda *_: None)
    p = Pi(b, [], False, str(tmp_path))
    sent = []
    p.send = sent.append
    p.busy = True
    p.handle({"t": "say", "text": "wait, use the other file"})
    assert sent[-1]["type"] == "prompt" and sent[-1]["streamingBehavior"] == "steer"
    p.handle({"t": "unhold"})
    assert sent[-1]["type"] == "clear_queue" and b.waiting == []
    b.out.close()
    evs = [json.loads(x) for x in (tmp_path / "pi.jsonl").read_text(encoding="utf-8").splitlines()]
    queued = next(e for e in evs if e["t"] == "queued")
    assert queued["keys"] == ["edit"]
    assert any(e["t"] == "unqueued" and e["id"] == queued["id"] for e in evs)
