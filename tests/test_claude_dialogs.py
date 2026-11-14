"""Claude Code's "Session paused" dialogs (2.1.317): a permission_prompt notification whose choices are not Allow/Deny.
The card shows the options read off the terminal and presses the arrow keys to the one picked."""
import json

from deck.bridge.claude import Screen, screen_dialog
from deck.chat_bridge import Bridge, Claude

# Claude Code 2.1.317 drawing its usage-credits dialog (recorded at 160x45): cursor moves, not spaces or newlines
CREDITS = (
    "\x1b[2J\x1b[H\r\x1b[6B\x1b[38;5;246m❯ \x1b[38;5;231mreply with the word pong\x1b[39m\r\x1b[2B\x1b[38;5;220m" + "─" * 160 +
    "\r\x1b[2C\x1b[1B\x1b[1mFable 5.1 now uses usage credits\r\x1b[2C\x1b[2B\x1b[22m*\x1b[6GChecking\x1b[15Gusage"
    "\x1b[21Gcredits…\r\x1b[2C\x1b[2B\x1b[3mEnter to confirm · Esc to cancel\r\x1b[27B\x1b[K\r\x1b[1B\x1b[K\x1b[45;1H"
    "\x1b[H\r\x1b[2C\x1b[11BFable 5.1 run\x1b[17G on\x1b[21Gusage credits,\x1b[36Gpurchased\x1b[46Gseparately\x1b[57Gfrom"
    "\x1b[62Gyour\x1b[67Gplan.\r\x1b[2C\x1b[2B\x1b[38;5;246mYou don't have usage credits yet.\r\x1b[2C\x1b[2B\x1b[38;5;153m❯"
    "\x1b[5GSwitch to Opus 5.5 and continue\r\x1b[4C\x1b[1B\x1b[39mSet\x1b[9Gup\x1b[12Gusage\x1b[18Gcredits\x1b[26Gon"
    "\x1b[29Gclaude.ai\r\x1b[2C\x1b[3B\x1b[3mEnter to confirm · Esc to cancel\x1b[23m\x1b[39m\x1b[45;1H\x1b[16;3H")
WANT = ("Fable 5.1 now uses usage credits\nFable 5.1 runs on usage credits, purchased separately from your plan.\n"
        "You don't have usage credits yet.", ["Switch to Opus 5.5 and continue", "Set up usage credits on claude.ai"], 0)


def test_dialog_is_read_off_the_screen_however_reads_split_it():
    for step in (1, 3, 7, 97, len(CREDITS)):  # a read can end inside an escape sequence
        s = Screen(45, 160)
        for i in range(0, len(CREDITS), step):
            s.feed(CREDITS[i:i + step])
        assert screen_dialog(s.lines()) == WANT, step


def test_refusal_dialog_numbered_options_and_marker_below_the_first():
    lines = ["─" * 40, "  Session paused", "  This message was flagged.", "    1. Switch to Opus 5.5",
             "  ❯ 2. Edit prompt and retry with Fable 5.1", "", "  Enter to confirm · Esc to cancel"]
    assert screen_dialog(lines) == ("Session paused\nThis message was flagged.",
                                    ["Switch to Opus 5.5", "Edit prompt and retry with Fable 5.1"], 1)
    assert screen_dialog(["❯ hello", "nothing to confirm"]) is None


def claude(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    c = Claude.__new__(Claude)
    c.b = Bridge(str(tmp_path), "dialog")
    c.open_q, c.asks, c.busy, c.ysk, c.dialog = {}, {}, True, {"later": None, "card": None}, None
    c.grid = Screen(45, 160)
    c.sent = []
    c.write = c.sent.append
    return c


def events(c):
    c.b.out.flush()
    return [json.loads(line) for line in c.b.out_path.read_text(encoding="utf-8").splitlines()]


def test_session_paused_card_shows_the_dialogs_options_and_presses_to_them(tmp_path, monkeypatch):
    c = claude(tmp_path, monkeypatch)
    c.grid.feed(CREDITS)
    c.hook({"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "Session paused"})
    ask = next(e for e in events(c) if e["t"] == "ask")
    assert ask["options"] == WANT[1] and ask["title"].startswith("**Session paused**\n\nFable 5.1 now uses")
    c.command({"t": "answer", "id": ask["id"], "value": "Set up usage credits on claude.ai"})
    assert c.sent == ["\x1b[B", "\r"]
    c.b.out.close()


def test_unreadable_dialog_is_a_note_to_answer_in_the_terminal(tmp_path, monkeypatch):
    c = claude(tmp_path, monkeypatch)
    c.dialog_ask("Session paused", wait=0)
    got = events(c)
    assert not any(e["t"] == "ask" for e in got)
    assert any(e["t"] == "note" and "answer it there" in e["text"] for e in got)
    assert got[-1]["s"] == "needs_you"
    c.b.out.close()


def test_dialog_card_greys_once_the_dialog_is_gone(tmp_path, monkeypatch):
    c = claude(tmp_path, monkeypatch)
    c.grid.feed(CREDITS)
    c.dialog_ask("Session paused")
    aid = c.dialog[0]
    c.grid.feed("\x1b[2J")
    c.dialog_gone()
    assert c.dialog and aid in c.asks  # one blank frame of a redraw is not an answer
    c.dialog = (c.dialog[0], c.dialog[1], 0)
    c.dialog_gone()
    assert c.dialog is None and aid not in c.asks
    assert any(e["t"] == "answered" and e["id"] == aid for e in events(c))
    c.b.out.close()


def test_tool_approvals_stay_allow_deny(tmp_path, monkeypatch):
    c = claude(tmp_path, monkeypatch)
    for msg in ("Claude needs your permission to use Bash", "Claude needs your permission"):
        c.hook({"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": msg})
    asks = [e for e in events(c) if e["t"] == "ask"]
    assert [a["options"] for a in asks] == [["Allow", "Deny"]] * 2
    c.b.out.close()
