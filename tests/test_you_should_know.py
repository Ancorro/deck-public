"""Claude's "You should know" card in the chat. Its "Turn off suggestions" pressed the card's hotkeys 4 4, which landed
in the prompt as text when the card took no keys: 7 messages began "44" (2026-10-31 to 11-21) and the plugin stayed on.
Now that button turns the plugin off in Claude's settings, and other buttons press keys only while the card shows."""
import json
from pathlib import Path

from deck.bridge.claude import Screen
from deck.chat_bridge import Bridge, Claude

LINE = "The probe run uses the old grasp, so a gain over v1 is not memory alone."


def claude(tmp_path, monkeypatch, card_on_screen=True):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    c = Claude.__new__(Claude)
    c.b = Bridge(str(tmp_path / "chat"), "ysk")
    c.open_q, c.asks, c.busy, c.dialog = {}, {}, False, None
    c.ysk = {"mtime": 0, "card": ("offer", LINE, None, None, None), "ask": "ysk-1", "later": None}
    c.asks["ysk-1"] = dict(Claude.YSK_KEYS["offer"])
    c.grid = Screen(45, 160)
    c.grid.feed("\x1b[2J\x1b[H❯ \r\n")
    if card_on_screen:  # as Claude draws it: the star, the tag, then the line wrapped at the terminal's width
        c.grid.feed("\x1b[30;1H✦ tip · The probe run uses the old\r\n  grasp, so a gain over v1\r\n")
    c.sent = []
    c.write = c.sent.append
    return c


def events(c):
    c.b.out.flush()
    return [json.loads(line) for line in c.b.out_path.read_text(encoding="utf-8").splitlines()]


def test_turn_off_suggestions_sets_the_plugin_off_and_types_nothing(tmp_path, monkeypatch):
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus", "enabledPlugins": {"other@x": True,
                                                                        Claude.YSK_PLUGIN: True}}), encoding="utf-8")
    c = claude(tmp_path, monkeypatch)
    c.command({"t": "answer", "id": "ysk-1", "value": "Turn off suggestions"})
    assert c.sent == []
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["enabledPlugins"] == {"other@x": True, Claude.YSK_PLUGIN: False} and data["model"] == "opus"
    assert c.ysk["off"] and c.ysk["ask"] is None
    # later cards from the plugin's store no longer reach the chat
    store = tmp_path / ".claude" / "plugins" / "store"
    store.mkdir(parents=True)
    (store / "cc-plugin-you-should-know_builtin-1.json").write_text("{}", encoding="utf-8")
    before = len(events(c))
    c.heads_up()
    assert len(events(c)) == before


def test_turn_off_works_with_no_settings_file(tmp_path, monkeypatch):
    c = claude(tmp_path, monkeypatch, card_on_screen=False)
    (tmp_path / ".claude").mkdir()
    c.command({"t": "answer", "id": "ysk-1", "value": "Turn off suggestions"})
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert data == {"enabledPlugins": {Claude.YSK_PLUGIN: False}} and c.sent == []


def test_a_card_button_presses_its_key_only_while_the_card_shows(tmp_path, monkeypatch):
    c = claude(tmp_path, monkeypatch)
    c.command({"t": "answer", "id": "ysk-1", "value": "Knew this already"})
    assert c.sent == ["2"]
    gone = claude(tmp_path, monkeypatch, card_on_screen=False)
    gone.command({"t": "answer", "id": "ysk-1", "value": "Knew this already"})
    assert gone.sent == []
    assert any(e["t"] == "note" and "left the terminal" in e.get("text", "") for e in events(gone))
