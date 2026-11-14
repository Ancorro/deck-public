"""deck/computer_mcp.py: the MCP protocol and the tools, over a fake desktop (no screen is touched)."""
import json

import pytest

from deck import computer_mcp as cm

PIL = pytest.importorskip("PIL.Image")


class Fake:
    def __init__(self):
        self.did, self.pos = [], (500, 500)

    def grab(self):
        return PIL.new("RGB", (3136, 1764), "white")

    def size(self):
        return 3136, 1764

    def mouse(self):
        return self.pos

    def click(self, x, y, button="left", clicks=1):
        self.did.append(("click", x, y, button, clicks))

    def key(self, keys):
        self.did.append(("key", *keys))

    def type(self, text):
        self.did.append(("type", text))


def computer(cfg=None, cls=None):
    cls = cls or cm.Computer
    c = cls.__new__(cls)
    c.cfg, c.be, c.scale = cfg or {}, Fake(), 1.0
    c.goal_lock = cm.threading.Lock()
    return c


def test_a_large_screen_is_sent_smaller_and_clicks_scale_back():
    c = computer()
    out = c.shot()
    assert "1568x882" in out[0]["text"] and out[1]["type"] == "image" and c.scale == 2.0
    c.call("click", {"x": 100, "y": 50, "screenshot": False})
    c.call("key", {"keys": "Ctrl + Shift + T", "screenshot": False})
    assert c.be.did == [("click", 200, 100, "left", 1), ("key", "ctrl", "shift", "t")]
    with pytest.raises(ValueError):
        c.call("click", {"x": 5000, "y": 5, "screenshot": False})


def test_the_mouse_in_the_corner_stops_actions_but_not_looking():
    c = computer()
    c.be.pos = (0, 2)
    with pytest.raises(cm.Stopped):
        c.call("type_text", {"text": "hi", "screenshot": False})
    assert c.be.did == [] and c.call("screenshot", {})[1]["type"] == "image"


def test_run_goal_is_offered_only_where_a_loop_is_set_up(monkeypatch, capsys):
    real = cm.Computer
    monkeypatch.setattr(cm, "Computer", lambda cfg: computer(cfg, real))
    for cfg, has in (({}, False), ({"goal": ["run.sh"]}, True)):
        monkeypatch.setattr("sys.stdin", iter(['{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n']))
        cm._serve(cfg)
        names = [t["name"] for t in json.loads(capsys.readouterr().out)["result"]["tools"]]
        assert ("run_goal" in names) == has and "screenshot" in names
