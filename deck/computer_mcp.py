"""Computer use for a Deck Computer session: an MCP (Model Context Protocol) server over stdio that the session's
Claude starts (agents.computer_args). It acts on this host's own desktop, the one Deck's Screen tab streams:

- screenshot, click, double_click, right_click, move, drag, type_text, key, scroll, wait: one action each, for
  quick checks and short steps;
- run_goal: a whole multi-step task handed to the user's computer-use loop (computer.json "goal"), which is faster
  for long UI work: its own small model picks most steps.

Backends (computer.json "backend"):
- "x11": an X display (computer.json "display", e.g. ":5", an agent desktop), through pyautogui; started
  first with computer.json "start" when its X socket is missing.
- "windows": a Windows monitor (computer.json "monitor": a piece of its device name or ID, e.g. "BNQ"; unset, the
  primary), through typesafe_computer_use.windows (SendInput, per-monitor DPI).

Coordinates are screen pixels of that display or monitor, its top-left corner (0, 0), as in the screenshot (which is
scaled down for the model when large: `scale` in the result says by how much, and clicks take the screenshot's
pixels). The mouse in the top-left corner of the screen stops every action (the loop's escape hatch too).

Run by the Python named in computer.json "python" (one that has Pillow, and pyautogui on X11 or typesafe_computer_use
on Windows): `python computer_mcp.py <computer.json>`. Standard library only besides those.
"""
from __future__ import annotations

import base64
import io
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

PROTOCOL = "2025-06-18"
MAX_SIDE = 1568  # the longest side a screenshot is sent at (larger ones are scaled down, and clicks scaled back up)
CORNER = 4  # px: the mouse this close to the top-left corner stops every action
GOAL_TIMEOUT = 30 * 60  # s a run_goal may take


class Stopped(Exception):
    """The user put the mouse in the top-left corner."""


# ---- backends: the same calls on X11 and Windows ----

class X11:
    def __init__(self, cfg: dict):
        self.display = cfg.get("display") or os.environ.get("DISPLAY", ":0")
        self.start = cfg.get("start")
        os.environ["DISPLAY"] = self.display
        self.gui = None

    def ready(self):
        sock = Path(f"/tmp/.X11-unix/X{self.display.lstrip(':').split('.')[0]}")
        if not sock.exists() and self.start:
            subprocess.run([os.path.expanduser(a) for a in self.start], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
            time.sleep(3)  # the desktop draws its panels and wallpaper after its X socket appears
        if self.gui is None:
            import pyautogui
            pyautogui.FAILSAFE = False  # the corner check below does this, with a message the model can read
            pyautogui.PAUSE = 0.05
            self.gui = pyautogui
        return self.gui

    def grab(self):
        from PIL import ImageGrab
        self.ready()
        return ImageGrab.grab(xdisplay=self.display)

    def size(self):
        return tuple(self.ready().size())

    def mouse(self):
        return tuple(self.ready().position())

    def click(self, x, y, button="left", clicks=1):
        self.ready().click(x, y, clicks=clicks, interval=0.08, button=button)

    def move(self, x, y):
        self.ready().moveTo(x, y)

    def drag(self, x1, y1, x2, y2):
        g = self.ready()
        g.moveTo(x1, y1)
        g.dragTo(x2, y2, duration=0.4, button="left")

    def type(self, text):
        g = self.ready()
        # pyautogui types ASCII only: the rest goes through xdotool, which types any character
        if text.isascii():
            g.write(text, interval=0.01)
        else:
            subprocess.run(["xdotool", "type", "--delay", "10", "--", text], check=True, timeout=120)

    def key(self, keys):
        self.ready().hotkey(*keys)

    def scroll(self, x, y, lines):
        g = self.ready()
        g.moveTo(x, y)
        g.scroll(lines)


class Windows:
    def __init__(self, cfg: dict):
        if cfg.get("monitor"):
            os.environ["CU_MONITOR"] = cfg["monitor"]
        from typesafe_computer_use import windows as w  # sets per-monitor DPI awareness on import
        self.w = w

    def grab(self):
        from PIL import ImageGrab
        left, top, right, bottom = self.w.monitor_rect()
        return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)

    def size(self):
        left, top, right, bottom = self.w.monitor_rect()
        return right - left, bottom - top

    def mouse(self):
        return self.w.mouse_location()

    def _button(self, down, up, clicks):
        for _ in range(clicks):
            self.w._send(self.w._mouse(down))
            self.w._send(self.w._mouse(up))

    def click(self, x, y, button="left", clicks=1):
        if button == "left" and clicks == 1:
            return self.w.click_at((x, y))
        self.w._move((round(x), round(y)))
        flags = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}[button]
        self._button(*flags, clicks)

    def move(self, x, y):
        self.w._move((round(x), round(y)))

    def drag(self, x1, y1, x2, y2):
        self.w._move((round(x1), round(y1)))
        self.w._send(self.w._mouse(0x0002))
        for i in range(1, 11):  # in steps, so drag-and-drop targets see the pointer pass
            self.w._move((round(x1 + (x2 - x1) * i / 10), round(y1 + (y2 - y1) * i / 10)))
        self.w._send(self.w._mouse(0x0004))

    def type(self, text):
        self.w.type_text(text)

    def key(self, keys):
        if len(keys) == 1:
            self.w.press(keys[0])
        else:
            self.w.hotkey(*keys)

    def scroll(self, x, y, lines):
        self.w._move((round(x), round(y)))
        self.w._send(self.w._mouse(self.w.MOUSEEVENTF_WHEEL, self.w.wheel_delta(lines)))


# ---- the tools ----

class Computer:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.be = Windows(cfg) if cfg.get("backend") == "windows" else X11(cfg)
        self.scale = 1.0  # screenshot pixels -> screen pixels, from the last screenshot
        self.goal_lock = threading.Lock()

    def guard(self):
        try:
            x, y = self.be.mouse()
        except Exception:
            return
        if x <= CORNER and y <= CORNER:
            raise Stopped("stopped: the mouse is in the top-left corner (the user's stop). Ask the user before acting again.")

    def at(self, a: dict, kx="x", ky="y"):
        """A point from the model (screenshot pixels) in screen pixels, checked against the screen."""
        x, y = float(a[kx]) * self.scale, float(a[ky]) * self.scale
        w, h = self.be.size()
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError(f"({a[kx]}, {a[ky]}) is off the screen")
        return round(x), round(y)

    def shot(self, note: str = ""):
        img = self.be.grab().convert("RGB")
        w, h = img.size
        self.scale = max(1.0, max(w, h) / MAX_SIDE)
        if self.scale > 1:
            img = img.resize((round(w / self.scale), round(h / self.scale)))
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        text = f"{note}Screenshot {img.size[0]}x{img.size[1]}" + (
            f" (the screen is {w}x{h}; give coordinates in this image's pixels)" if self.scale > 1 else "")
        return [{"type": "text", "text": text},
                {"type": "image", "data": base64.b64encode(buf.getvalue()).decode(), "mimeType": "image/png"}]

    def after(self, did: str, a: dict):
        """What an action returns: a fresh screenshot after a short settle, unless asked not to."""
        if a.get("screenshot") is False:
            return [{"type": "text", "text": did}]
        time.sleep(float(a.get("settle", 0.6)))
        return self.shot(did + ". ")

    def call(self, name: str, a: dict):
        if name == "run_goal":
            return self.run_goal(a)
        if name == "screenshot":
            return self.shot()
        if name == "wait":
            time.sleep(min(30.0, float(a.get("seconds", 1))))
            return self.shot()
        self.guard()
        if name in ("click", "double_click", "right_click"):
            x, y = self.at(a)
            button = "right" if name == "right_click" else a.get("button", "left")
            self.be.click(x, y, button=button, clicks=2 if name == "double_click" else 1)
            return self.after(f"{name} at ({a['x']}, {a['y']})", a)
        if name == "move":
            self.be.move(*self.at(a))
            return self.after(f"moved to ({a['x']}, {a['y']})", a)
        if name == "drag":
            self.be.drag(*self.at(a, "x1", "y1"), *self.at(a, "x2", "y2"))
            return self.after("dragged", a)
        if name == "type_text":
            self.be.type(str(a["text"]))
            if a.get("enter"):
                self.be.key(["enter"])
            return self.after(f"typed {len(a['text'])} characters", a)
        if name == "key":
            keys = [k.strip().lower() for k in str(a["keys"]).replace(" ", "").split("+") if k.strip()]
            for _ in range(max(1, min(50, int(a.get("repeat", 1))))):
                self.be.key(keys)
            return self.after(f"pressed {a['keys']}", a)
        if name == "scroll":
            w, h = self.be.size()
            x, y = self.at(a) if "x" in a else (w // 2, h // 2)
            lines = int(a.get("amount", 5)) * (1 if a.get("direction", "down") == "up" else -1)
            self.be.scroll(x, y, lines)
            return self.after(f"scrolled {a.get('direction', 'down')}", a)
        raise ValueError(f"no tool {name}")

    def run_goal(self, a: dict):
        goal = self.cfg.get("goal")
        if not goal:
            raise ValueError("this host has no computer-use loop set up (computer.json \"goal\")")
        if not self.goal_lock.acquire(blocking=False):
            raise ValueError("a run_goal is already running")
        try:
            self.guard()
            argv = [os.path.expanduser(x) for x in (shlex.split(goal) if isinstance(goal, str) else goal)]
            argv += [str(a["goal"]), *self.cfg.get("goal_after", []), *([f"--steps={int(a['steps'])}"] if a.get("steps") else [])]
            t0 = time.time()
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=GOAL_TIMEOUT, stdin=subprocess.DEVNULL, cwd=self.cfg.get("goal_cwd") or None)
            out = (r.stdout + ("\n" + r.stderr if r.stderr.strip() else "")).strip()
            tail = "\n".join(out.splitlines()[-40:])
            head = f"The loop ended (exit {r.returncode}) after {time.time() - t0:.0f} s. Its last lines:\n{tail}\n\n"
        finally:
            self.goal_lock.release()
        return self.shot(head + "The screen now: ")


POINT = {"x": {"type": "number", "description": "pixels from the screenshot's left edge"},
         "y": {"type": "number", "description": "pixels from the screenshot's top edge"}}
QUIET = {"screenshot": {"type": "boolean", "description": "return a screenshot afterwards (default true)"}}


def tool(name, description, props=None, required=()):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props or {}, "required": list(required)}}


TOOLS = [
    tool("screenshot", "See the desktop now."),
    tool("click", "Left-click a point (screenshot pixels). Returns a screenshot after.", {**POINT, **QUIET}, ("x", "y")),
    tool("double_click", "Double-click a point.", {**POINT, **QUIET}, ("x", "y")),
    tool("right_click", "Right-click a point.", {**POINT, **QUIET}, ("x", "y")),
    tool("move", "Move the mouse to a point (hover).", {**POINT, **QUIET}, ("x", "y")),
    tool("drag", "Press at (x1, y1), drag to (x2, y2), let go.",
         {"x1": {"type": "number"}, "y1": {"type": "number"}, "x2": {"type": "number"}, "y2": {"type": "number"}, **QUIET},
         ("x1", "y1", "x2", "y2")),
    tool("type_text", "Type text into the focused field (any characters). enter: press Enter after.",
         {"text": {"type": "string"}, "enter": {"type": "boolean"}, **QUIET}, ("text",)),
    tool("key", "Press a key or chord, e.g. enter, esc, tab, ctrl+c, alt+tab, ctrl+shift+t, win (super).",
         {"keys": {"type": "string"}, "repeat": {"type": "integer"}, **QUIET}, ("keys",)),
    tool("scroll", "Scroll at a point (default the middle of the screen).",
         {**POINT, "direction": {"type": "string", "enum": ["up", "down"]}, "amount": {"type": "integer",
          "description": "lines, default 5"}, **QUIET}, ("direction",)),
    tool("wait", "Wait up to 30 s (a page loading), then see the desktop.", {"seconds": {"type": "number"}}),
    tool("run_goal", "Hand a whole multi-step UI task to the computer-use loop, which runs it to the end on this desktop "
         "(faster than single clicks for long work: its small model picks most steps). Say the goal plainly and "
         "completely, with any text to type. Returns the loop's last lines and a screenshot; check the result.",
         {"goal": {"type": "string"}, "steps": {"type": "integer", "description": "step budget, default the loop's"}},
         ("goal",)),
]


def instructions(cfg: dict) -> str:
    """What the session's Claude is told about the desktop it drives (Claude Code adds it to the system prompt)."""
    where = cfg.get("about") or "this host's desktop"
    return (f"This is a Deck Computer session: the computer tools act on {where}. The user watches it live above this "
            "chat and can type there too. Look before acting (screenshot), act, then check the screenshot each action "
            "returns. For a long multi-step UI task, hand it to run_goal" + (" " if cfg.get("goal") else " (not set up "
            "on this host) ") + "and check the result. The mouse in the top-left corner is the user's stop: when an "
            "action reports it, stop and ask. Ask before anything that sends, buys, deletes or posts.")


def hold(path: str):
    """Hold an exclusive lock on path while this server runs (computer.json "hold"): the agent desktop's stop script
    leaves the desktop up while a Computer session has it."""
    import fcntl
    p = Path(os.path.expanduser(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def serve(cfg: dict) -> None:
    held = hold(cfg["hold"]) if cfg.get("hold") and os.name != "nt" else None  # noqa: F841  (kept open)
    try:
        _serve(cfg)
    finally:
        if cfg.get("on_exit"):  # e.g. stop the agent desktop later, now no session holds it
            subprocess.run([os.path.expanduser(a) for a in cfg["on_exit"]], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)


def _serve(cfg: dict) -> None:
    pc = Computer(cfg)
    tools = TOOLS if cfg.get("goal") else [t for t in TOOLS if t["name"] != "run_goal"]
    out_lock = threading.Lock()

    def send(msg):
        with out_lock:
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()

    def handle(req):
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if rid is None:
            return  # a notification
        if method == "initialize":
            res = {"protocolVersion": params.get("protocolVersion", PROTOCOL), "capabilities": {"tools": {}},
                   "serverInfo": {"name": "deck-computer", "version": "1"}, "instructions": instructions(cfg)}
        elif method == "tools/list":
            res = {"tools": tools}
        elif method == "tools/call":
            try:
                res = {"content": pc.call(params.get("name", ""), params.get("arguments") or {})}
            except Stopped as e:
                res = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            except Exception as e:
                res = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
        elif method == "ping":
            res = {}
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"no method {method}"}})
            return
        send({"jsonrpc": "2.0", "id": rid, "result": res})

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        # run_goal takes minutes: on its own thread, so pings and screenshots still answer meanwhile
        if req.get("method") == "tools/call" and (req.get("params") or {}).get("name") == "run_goal":
            threading.Thread(target=handle, args=(req,), daemon=True).start()
        else:
            handle(req)


if __name__ == "__main__":
    serve(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
