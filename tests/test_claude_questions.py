import json

from deck.chat_bridge import Bridge, Claude


def claude_transcript(tmp_path):
    c = Claude.__new__(Claude)
    c.b = Bridge(str(tmp_path), "question")
    c.path, c.pos = tmp_path / "transcript.jsonl", 0
    c.blocks, c.open_q, c.qstep, c.asks = {}, {}, {}, {}
    c.busy = True
    c.redraw = lambda: None
    return c


def events(c):
    c.b.out.flush()
    return [json.loads(line) for line in c.b.out_path.read_text(encoding="utf-8").splitlines()]


def test_explanation_is_shown_before_question_and_not_replayed(tmp_path):
    c = claude_transcript(tmp_path)
    explanation = "Here is the explanation.\n\nChoose the next step below."
    record = {"type": "assistant", "message": {"id": "reply", "content": [
        {"type": "text", "text": explanation}]}}
    c.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    c.hook({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "question",
            "tool_input": {"questions": [{"question": "Which step?", "options": [{"label": "Continue"}]}]}})
    got = events(c)
    assert [e["t"] for e in got if e["t"] in ("msg", "ask")] == ["msg", "ask"]
    assert next(e for e in got if e["t"] == "msg")["blocks"] == [{"kind": "text", "text": explanation}]
    assert c.read_transcript() == []
    assert events(c) == got
    c.b.out.close()


def test_partial_transcript_line_waits_for_completion_without_losing_question(tmp_path):
    c = claude_transcript(tmp_path)
    record = {"type": "assistant", "message": {"id": "reply", "content": [
        {"type": "text", "text": "More context"}]}}
    c.path.write_text(json.dumps(record), encoding="utf-8")
    c.hook({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "question",
            "tool_input": {"questions": [{"question": "Continue?"}]}})
    assert c.pos == 0
    assert [e["t"] for e in events(c) if e["t"] in ("ask", "msg")] == ["ask"]
    with c.path.open("a", encoding="utf-8") as f:
        f.write("\n")
    assert len(c.read_transcript()) == 1
    assert [e["t"] for e in events(c) if e["t"] in ("ask", "msg")] == ["ask", "msg"]
    assert c.open_ask() == "question#0"
    c.b.out.close()


def preview_question(tmp_path):
    c = claude_transcript(tmp_path)
    c.cancelled_at, c.wlock = None, __import__("threading").Lock()
    sent = []
    c.write = sent.append
    c.keys = lambda ks: sent.extend(ks)
    c.type = lambda text: sent.append(("type", text))
    q = {"question": "Which mode?", "options": [{"label": "A", "preview": "a"}, {"label": "B", "preview": "b"}]}
    c.open_q, c.qstep = {"q": {"questions": [q, q]}}, {"q": 0}
    c.ask_step("q")
    return c, sent


def test_preview_question_pick_presses_enter(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    c, sent = preview_question(tmp_path)
    c.answer_q("q#0", "B")
    assert sent == ["2", "\r"]  # side-by-side previews: a number only moves the cursor
    assert c.open_ask() == "q#1"
    c.b.out.close()


def test_preview_question_typed_answer_goes_by_chat_about_this(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    c, sent = preview_question(tmp_path)
    c.answer_q("q#0", {"other": "set opus to default"})
    assert sent == ["2", "\x1b[B", "\r", ("type", "set opus to default")]  # no "Type something" row there
    assert c.open_ask() is None
    c.b.out.close()
