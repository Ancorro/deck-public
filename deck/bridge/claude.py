"""Claude Code's adapter: run interactively in a pseudo-terminal (its own UI drives permissions and questions), read
through its transcript and hooks."""
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import util
from .adapter import Adapter
from .bridge import GOAL_SET, Bridge, unpaste
from .util import WINDOWS


def claude_blocks(content) -> list[dict]:
    out = []
    for c in content if isinstance(content, list) else []:
        if c.get("type") == "text":
            out.append({"kind": "text", "text": c.get("text", "")})
        elif c.get("type") == "thinking":
            out.append({"kind": "think", "text": c.get("thinking", "")})
        elif c.get("type") == "tool_use":
            out.append({"kind": "tool", "id": c.get("id"), "name": c.get("name"), "args": c.get("input") or {}})
    return out


class Term:
    """A program in a pseudo-terminal of its own, so it runs as it does for a person (Claude's -p mode lacks some tools,
    e.g. Artifact). Windows: a ConPTY (pywinpty: Deck's server runs with it, and the bridge with the server's Python);
    elsewhere the pty module. Not the tmux pane itself: under MSYS2's tmux a native program sees pipes, not a terminal."""
    ROWS, COLS = 40, 120

    def __init__(self, argv: list[str], cwd: str, env: dict):
        if WINDOWS:
            from winpty import PtyProcess
            self.p = PtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=(self.ROWS, self.COLS))
            self.pid = self.p.pid
        else:
            import fcntl
            import pty
            import struct
            import termios
            self.pid, self.fd = pty.fork()
            if self.pid == 0:
                os.chdir(cwd)
                os.execvpe(argv[0], argv, env)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", self.ROWS, self.COLS, 0, 0))

    def read(self) -> str:
        """Output so far ("" at the end)."""
        try:
            if WINDOWS:
                return self.p.read(65536) or " "
            return os.read(self.fd, 65536).decode("utf-8", errors="replace")
        except (EOFError, OSError):
            return ""

    def write(self, s: str) -> None:
        if WINDOWS:
            self.p.write(s)
        else:
            os.write(self.fd, s.encode())

    def wait(self) -> int:
        if WINDOWS:
            while self.p.isalive():
                time.sleep(0.2)
            return self.p.exitstatus or 0
        return os.waitstatus_to_exitcode(os.waitpid(self.pid, 0)[1])

    def kill(self) -> None:
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.pid)], capture_output=True)
        else:
            try:
                os.kill(self.pid, 15)
            except OSError:
                pass


# Set by a Claude session for its own children: inherited, they make a new Claude a "child" (no transcript, no
# session of its own). A chat started from inside a Claude session must not pass them on.
CLAUDE_SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT",
                       "CLAUDE_PID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                       "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_EFFORT", "CLAUDE_CODE_EXECPATH")


ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Z]|\x1b.")


class Screen:
    """Just enough of a terminal to read a dialog back: Claude's Ink UI (user interface) draws by moving the cursor,
    and redraws only what changed, so the text alone loses which column a row starts at and skips letters already
    there. Cursor moves, erases and printable characters; everything else is ignored."""
    CSI = re.compile(r"\x1b\[([0-9;?<>=]*)[ -/]*([@-~])")
    WHOLE = re.compile(r"\x1b(?:\[[0-9;?<>=]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-Z]|[^\[\]()])")

    def __init__(self, rows: int, cols: int):
        self.rows, self.cols = rows, cols
        self.grid = [[" "] * cols for _ in range(rows)]
        self.r = self.c = 0
        self.rest = ""  # an escape sequence cut off at the end of a read: it ends in the next

    def feed(self, data: str) -> None:
        data, self.rest = self.rest + data, ""
        if (cut := data.rfind("\x1b")) >= 0 and len(data) - cut < 64 and not self.WHOLE.match(data, cut):
            data, self.rest = data[:cut], data[cut:]
        pos = 0
        for m in ANSI.finditer(data):
            self.text(data[pos:m.start()])
            pos = m.end()
            if (c := self.CSI.fullmatch(m[0])) and not c[1].startswith(("?", "<", ">", "=")):
                self.csi([int(x) if x.isdigit() else 0 for x in c[1].split(";")], c[2])
        self.text(data[pos:])

    def text(self, s: str) -> None:
        for ch in s:
            if ch == "\r":
                self.c = 0
            elif ch == "\n":
                self.down()
            elif ch == "\b":
                self.c = max(0, self.c - 1)
            elif ch >= " ":
                if self.c >= self.cols:
                    self.c = 0
                    self.down()
                self.grid[self.r][self.c] = ch
                self.c += 1

    def down(self) -> None:
        if self.r == self.rows - 1:
            self.grid = self.grid[1:] + [[" "] * self.cols]
        else:
            self.r += 1

    def csi(self, ps: list, f: str) -> None:
        n = max(1, ps[0])
        if f in "AF":
            self.r = max(0, self.r - n)
        elif f in "BE":
            self.r = min(self.rows - 1, self.r + n)
        elif f == "C":
            self.c = min(self.cols - 1, self.c + n)
        elif f == "D":
            self.c = max(0, self.c - n)
        if f in "EF":
            self.c = 0
        elif f in "G`":
            self.c = min(self.cols - 1, n - 1)
        elif f == "d":
            self.r = min(self.rows - 1, n - 1)
        elif f in "Hf":
            self.r = min(self.rows - 1, n - 1)
            self.c = min(self.cols - 1, max(1, ps[1] if len(ps) > 1 else 1) - 1)
        elif f == "K":
            a, b = {0: (self.c, self.cols), 1: (0, self.c + 1)}.get(ps[0], (0, self.cols))
            self.grid[self.r][a:b] = [" "] * (b - a)
        elif f == "J":
            rows = {0: range(self.r + 1, self.rows), 1: range(self.r)}.get(ps[0], range(self.rows))
            for r in rows:
                self.grid[r] = [" "] * self.cols
            if ps[0] in (0, 1):
                self.csi([ps[0]], "K")

    def lines(self) -> list[str]:
        return ["".join(r).rstrip() for r in self.grid]


RULE = re.compile(r"\s*[─━]{8,}\s*")
# Claude's prompt mark: ❯, or > where it draws ASCII (on Windows with no TERM, as Deck's detached bridges start it).
# Matching ❯ alone never saw the box there, so every new chat's first line waited the whole READY_WAIT (2026-11-11)
PROMPT = ("❯", ">")


def input_box(lines: list[str]) -> bool:
    """Claude's input box is on the screen: a row starting ❯ (or >) right under a rule (a choice dialog's ❯ rows are
    indented, so they are not)."""
    return any(line.startswith(PROMPT) and RULE.fullmatch(lines[i - 1]) for i, line in enumerate(lines) if i)


def screen_dialog(lines: list[str]):
    """The choice dialog at the bottom of Claude's screen (rows of options, one marked ❯, above "Enter to confirm"):
    (its text above the options, [option labels], index of the marked one), or None while none shows."""
    end = next((i for i in range(len(lines) - 1, -1, -1) if "Enter to confirm" in lines[i]), None)
    mark = next((i for i in range(end - 1, -1, -1) if lines[i].lstrip().startswith("❯")), None) if end else None
    if mark is None:
        return None
    col = lines[mark].index("❯") + 2  # each option's label starts in this column; the marker sits two to its left
    indent = lambda s: len(s) - len(s.lstrip())
    is_opt = lambda i: i == mark or (lines[i].strip() and indent(lines[i]) == col)
    top = mark
    while top > 0 and is_opt(top - 1):
        top -= 1
    bot = mark
    while bot + 1 < end and is_opt(bot + 1):
        bot += 1
    labels = [re.sub(r"^\d+\.\s+", "", lines[i].replace("❯", "", 1).strip()) for i in range(top, bot + 1)]
    head = []
    for line in reversed(lines[:top]):  # up to the dialog's top rule
        if re.fullmatch(r"\s*[─━]{8,}\s*", line):
            break
        head.insert(0, " ".join(line.split()))
    return "\n".join(h for h in head if h), labels, mark - top


class Claude(Adapter):
    """Claude as a person runs it (in a Term), so it has every tool. Its transcript
    (~/.claude/projects/<folder>/<session id>.jsonl) becomes the chat; hooks given with --settings (`chat_bridge.py
    hook` appends their input to <session>.hooks.jsonl) tell the bridge the session and when each turn starts and ends;
    the page's messages are typed into its terminal. Nothing streams: text shows a block at a time."""
    YOLO = "--dangerously-skip-permissions"
    HOOKS = ("SessionStart", "UserPromptSubmit", "Stop", "StopFailure", "Notification", "PreToolUse")
    STOP_WAIT = 10.0  # seconds after the Stop hook to wait for the turn's turn_duration record
    EFFORTS = ("low", "medium", "high", "xhigh", "max")

    def __init__(self, b: Bridge, args: list[str], resume: bool, cwd: str):
        super().__init__(b, args, resume, cwd)
        session = b.out_path.name.removesuffix(".jsonl")
        self.hooks_path = b.out_path.with_name(f"{session}.hooks.jsonl")
        self.hooks_pos = self.hooks_path.stat().st_size if self.hooks_path.exists() else 0  # this start's hooks only
        self.path, self.pos = None, 0  # the transcript followed, and how far it has been read
        self.blocks, self.open_q, self.asks = {}, {}, {}  # message id -> blocks; open AskUserQuestion ids; asks shown
        self.qstep = {}  # open AskUserQuestion id -> the question its dialog is on (one card each, in turn)
        self.model, self.mode, self.screen, self.trust_asked = "", "", "", False
        self.grid = Screen(Term.ROWS, Term.COLS)  # the terminal as drawn: a choice dialog's options (dialog_ask)
        self.dialog = None  # the open card for a choice dialog read off the screen: (ask id, its labels)
        self.goal = ""  # the /goal in force: set_goal
        self.ysk = {"mtime": 0, "card": None, "ask": None, "later": None}  # "You should know" card: see heads_up()
        self.usage = []  # the bar's context, cost and limit chips, from its status line (statusline_main)
        self.quiet = time.time()  # when it last went idle: transcript records older than this don't start a turn
        self.retry_tail, self.retry_reported = "", False
        self.prompt_lines, self.cancelled_at = 1, None
        self.stopping = None  # when the Stop hook came, while its turn_duration record is awaited: see hook()
        self.ready = False  # its input box hasn't shown yet (wait_ready)
        pr = b.prefs
        if "--effort" in self.args:  # Deck's Settings default: this session's own /effort pick wins over it
            i = self.args.index("--effort")
            default, self.args[i:i + 2] = self.args[i + 1:i + 2] or [""], []
        else:
            default = [""]
        self.effort = pr.get("effort") or default[0]
        # The hook and status line need only the stdlib: run them on the base Python. The server's own may be a
        # temporary environment (the PC app's uv run) that a server restart deletes, and with it every running
        # chat's hooks: Stop never arrived and the tab said Working forever
        py = getattr(sys, "_base_executable", "") or sys.executable
        hook = f'"{Path(py).as_posix()}" "{util.SCRIPT.as_posix()}" hook ' \
               f'"{b.out_path.parent.as_posix()}" "{session}"'
        hooks = {e: [{**({"matcher": "AskUserQuestion"} if e == "PreToolUse" else {}),
                      "hooks": [{"type": "command", "command": hook}]}] for e in self.HOOKS}
        line = {"type": "command", "command": hook.replace(" hook ", " statusline ", 1)}
        own = b.prev_resume if resume else ""
        if own and not (self.projects() / f"{own}.jsonl").exists():  # nothing was said in it, or it's gone: a new one
            own, self.resume = "", False
        conv = ["--resume", own] if own else ["--continue"] if self.resume else []
        self.argv = [*util.resolve("claude"), "--settings", json.dumps({"hooks": hooks, "statusLine": line}), *conv,
                     *([self.YOLO] if self.yolo else []), *self.args,
                     *(["--model", pr["model"]] if pr.get("model") else []),
                     *(["--effort", self.effort] if self.effort else []),
                     *(["--permission-mode", pr["mode"]] if pr.get("mode") else [])]

    # ---- its terminal ----
    def write(self, s: str) -> None:
        with self.wlock:
            self.p.write(s)

    READY_WAIT = 90.0  # seconds typing waits at most for Claude's input box at start (slowest seen: 36 s)
    PENDING_WAIT = 20.0  # seconds a /model or /effort may take to answer before the next line goes anyway
    # Typing waits for these (wait_ready); __init__ sets them for a start: its input box has shown; it is still reading
    # its terminal; the time a /model or /effort typed must have answered by
    ready, reading, pending_until = True, True, 0.0
    saved_defaults = None  # ~/.claude/settings.json's defaults before this session's /model or /effort

    def wait_ready(self) -> None:
        """Hold typing until Claude can take it. At start, until its input box first shows (pump sets `ready`): before
        that Claude keeps the keys it gets, each Enter a newline, and takes them all as one input, so on a slow start
        "/model opus", "/effort medium" and a message typed meanwhile reached it as one /model whose argument was all
        three, which the proxy refused (user 2026-11-11: "cloud not working with claude"). Only at start: later a
        dialog may cover the box, and a typed answer must not wait for it. Then, after a /model or /effort, until its
        reply (local_reply): keys typed while it still runs are lost ("/effort low" arrived as "rt low")."""
        stop = getattr(self.b, "stop_asked", lambda: False)
        end = time.monotonic() + self.READY_WAIT
        while not self.ready and self.reading and time.monotonic() < end and not stop():
            time.sleep(0.1)
        self.ready = True  # once: after the wait, whatever ended it, the next line goes straight in
        while self.reading and time.monotonic() < self.pending_until and not stop():
            time.sleep(0.1)
        self.pending_until = 0.0

    def type(self, text: str) -> None:
        """Pasted (bracketed: newlines stay in the message), then Enter on its own. A slash command is typed a key at a
        time: pasted, or sent in one burst (which Claude also takes for a paste), it would be a message. Each waits for
        Claude's input box (wait_ready)."""
        self.wait_ready()
        if self.cancelled_at is not None:
            # A cancelled retry restores its prompt into Claude's composer. The next Deck message replaces it.
            time.sleep(max(0, 0.3 - (time.monotonic() - self.cancelled_at)))
            for _ in range(2 * self.prompt_lines + 2):
                self.write("\x15")
                time.sleep(0.02)
            self.cancelled_at = None
        self.prompt_lines = text.count("\n") + 1
        if re.match(r"/(model|effort) \S", text) and self.saved_defaults is None:  # what restore_defaults puts back
            s = self.settings()
            self.saved_defaults = {k: s[k] for k in self.DEFAULT_KEYS if k in s}
        if re.match(r"/[\w:-]+( [^\n]*)?$", text):
            for ch in text:
                self.write(ch)
                time.sleep(0.03)
        else:
            self.write("\x1b[200~" + text + "\x1b[201~")
        time.sleep(0.3)
        self.write("\r")
        if re.match(r"/(model|effort) \S", text):  # the next line waits for its reply (wait_ready)
            self.pending_until = time.monotonic() + self.PENDING_WAIT

    def pump(self) -> None:
        """Its output: drained (it blocks when nobody reads), ConPTY's start-up query answered, the screen kept to see
        when its input box first shows (wait_ready) and to spot the folder trust question (asked before anything
        reaches the transcript), which it accepts."""
        asked = WINDOWS
        while data := self.p.read():
            if asked and "\x1b[c" in data:
                asked = False
                self.write("\x1b[?1;2c")
            self.screen = (self.screen + ANSI.sub(" ", data))[-3000:]  # words are placed by cursor moves, not spaces
            self.grid.feed(data)
            if not self.ready and input_box(self.grid.lines()):
                self.ready = True
            self.retry_notice(data)
            if not self.trust_asked and re.search(r"trust\s+this\s+folder", self.screen):
                self.trust_asked = True  # the user starts these sessions in their own folders: trust, don't ask
                time.sleep(0.3)
                self.write("\x1b[B\r")
                self.b.note(f"Trusted this folder: {self.cwd}")
        self.reading = False  # it exited: nothing is waited for (wait_ready)

    def retry_notice(self, data: str) -> None:
        """Retries before a reply appear only in the terminal, never in Claude's transcript or hooks."""
        if not self.busy or self.retry_reported:
            return
        self.retry_tail = (self.retry_tail + data)[-1500:]
        text = ANSI.sub(" ", self.retry_tail)
        m = re.search(r"API error\s*·\s*Retrying in\s+\S+\s*·\s*attempt\s+\d+/\d+(?!\d)", text, re.I)
        if m:
            self.retry_reported = True
            self.b.note("Claude: " + " ".join(m[0].split()) + ". Stop cancels the retry.", err=True)

    # ---- its queue (messages typed while it works) ----
    QUEUE_KEYS = ["sendnow", "edit"]

    def send_now(self) -> None:
        """Its send-now key (Ctrl+X Ctrl+S): the queued messages reach it now; a running command goes to the background."""
        self.write("\x18")
        time.sleep(0.2)
        self.write("\x13")

    def asking(self) -> bool:
        return bool(self.open_q)

    def take_back(self) -> None:
        """Up pulls every queued message into its input; Ctrl+U, twice a line, empties it (Ctrl+L doesn't). Already taken
        in, Up brings back the last prompt instead, which is emptied the same way."""
        waiting, self.b.waiting = self.b.waiting, []
        if not waiting:
            return
        self.write("\x1b[A")
        time.sleep(0.6)
        for _ in range(2 * sum(t.count("\n") + 1 for _, t in waiting) + 2):
            self.write("\x15")
            time.sleep(0.02)
        for qid, _ in waiting:
            self.b.emit({"t": "unqueued", "id": qid})

    # ---- the page's commands ----
    def command(self, c: dict) -> None:
        t = c.get("t")
        if t == "say" and str(c.get("text", "")).strip() and (aid := self.open_ask()):
            # typed while a question is open: its "Type something" answer
            if self.asks[aid].get("multi"):
                self.b.note("Pick the options above and Submit, or Skip")
            else:
                self.answer_q(aid, {"other": str(c["text"])})
        elif t == "say" and str(c.get("text", "")).strip():
            self.type(str(c["text"]))
            self.working(True)
        elif t == "abort":
            self.write("\x1b")
            self.cancelled_at = time.monotonic()
            # Escape also cancels a rate-limit retry before any assistant reply. Claude writes no interruption
            # record or Stop hook in that case, so acknowledge the user's cancellation here.
            self.working(False)
        elif t == "answer" and isinstance(self.asks.get(c.get("id")), dict) and "q" in self.asks[c["id"]]:
            self.answer_q(c["id"], c.get("value"))
        elif t == "answer" and c.get("id") in self.asks:
            ask = self.asks.pop(c["id"])
            self.b.answered(c["id"])
            card = c["id"].startswith("ysk-")
            # Chat about it: the page sends the card with the next message itself, so here it only goes away (the
            # card's own "2" attaches it to the next prompt, which a message typed mid-turn misses)
            keys = ask.get("Dismiss" if card and c.get("value") == "Chat about it" else c.get("value"))
            keys = keys if isinstance(keys, list) else [keys] if keys else []
            if card and self.busy:  # mid-turn the card takes no hotkeys: they would land in the prompt as text
                self.ysk["later"] = (self.ysk["card"], keys)
            else:
                self.keys(keys)
            if not card:  # a card's buttons don't start a turn
                self.b.state("working")

    def switch(self, resume_id: str) -> None:
        self.type(f"/resume {resume_id}" if resume_id else "/clear")

    def slash_model(self, arg: str) -> None:
        """This session's model. Claude Code saves a /model pick as its default for new sessions (~/.claude/settings.json);
        Deck's default is Settings → General → New sessions → Default model, so the host's own is put back once Claude
        reports the save (restore_defaults)."""
        if arg:
            self.b.pref(model=arg)
            self.type(f"/model {arg}")
        else:
            self.pick("Which model?", [(m, m) for m in ("default", "fable", "opus", "sonnet", "haiku")], self.slash_model)

    # What a /model or /effort saves in ~/.claude/settings.json: the model, and the effort (2.1.322: per model, under
    # modelSettings; before, effortLevel)
    DEFAULT_KEYS = ("model", "effortLevel", "modelSettings")

    def restore_defaults(self) -> None:
        """Undo Claude's "saved as your default for new sessions": the settings file's model and effort as they were
        before this session's /model or /effort (type() keeps them just before it types one). The running session
        keeps its pick: Claude reads the file only as it starts."""
        prev = getattr(self, "saved_defaults", None)
        if prev is None:
            return
        self.saved_defaults = None
        path = Path.home() / ".claude" / "settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or all(data.get(k) == prev.get(k) for k in self.DEFAULT_KEYS):
            return
        for k in self.DEFAULT_KEYS:
            if k in prev:
                data[k] = prev[k]
            else:
                data.pop(k, None)
        tmp = path.with_suffix(".deck-tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            return

    def slash_effort(self, arg: str) -> None:
        if not arg:
            self.pick("Effort?", [(x, x) for x in self.EFFORTS], self.slash_effort)
        elif arg not in self.EFFORTS:
            self.b.note(f"Effort is one of {', '.join(self.EFFORTS)}", err=True)
        else:
            self.effort = arg
            self.b.pref(effort=arg)
            self.type(f"/effort {arg}")
            self.redraw()

    def keys(self, keys: list[str]) -> None:
        for i, k in enumerate(keys):
            time.sleep(0.3 if i else 0)  # one key at a time: a burst is a paste
            self.write(k)

    # ---- what Claude does ----
    def working(self, on: bool) -> None:
        self.stopping = None
        if on and not self.busy:
            self.retry_tail, self.retry_reported = "", False
        if not on:
            self.quiet = time.time()
        super().working(on)
        if not on and self.ysk["later"]:  # a card button pressed mid-turn: press it now, if that card still shows
            (card, keys), self.ysk["later"] = self.ysk["later"], None
            if card == self.ysk["card"]:
                self.keys(keys)

    def fresh(self, ev: dict) -> bool:
        """A record written since it last went idle (not a replayed or already-ended turn's)."""
        try:
            return datetime.fromisoformat(ev["timestamp"].replace("Z", "+00:00")).timestamp() > self.quiet
        except (KeyError, AttributeError, ValueError):
            return False

    def hook(self, h: dict) -> None:
        e = h.get("hook_event_name")
        if e == "SessionStart" and h.get("transcript_path") and Path(h["transcript_path"]) != self.path:
            if self.path is not None:  # /clear, /resume: another conversation
                self.b.emit({"t": "clear"})
            self.path, self.pos, self.blocks, self.open_q, self.qstep = Path(h["transcript_path"]), 0, {}, {}, {}
            self.quiet = time.time()  # its earlier records are history
            self.b.save(resume=h.get("session_id") or "", **({"preview": ""} if self.pos == 0 and self.path else {}))
        elif e == "DeckStatus":
            self.usage = h.get("parts") or []
            self.redraw()
        elif e == "UserPromptSubmit":
            self.working(True)
        elif e == "StopFailure":  # the turn ended on an API error
            self.read_transcript()
            self.b.note(str(h.get("error_details") or h.get("error")
                            or "The model request failed. Try again or choose another model."), err=True)
            self.working(False)
        elif e == "Stop":
            # Claude Code 2.1.318 can write the reply to the transcript seconds after Stop runs (a busy machine): ended
            # here, "your turn" came before the reply. The turn ends on its turn_duration record (written after Stop),
            # or STOP_WAIT seconds after Stop if none comes
            self.read_transcript()
            if self.busy and self.stopping is None:
                self.stopping = time.monotonic()
        elif e == "PreToolUse" and h.get("tool_name") == "AskUserQuestion" and h.get("tool_use_id"):
            # Hooks are read before the transcript. Show its preceding explanation before the question card;
            # the tool call itself may not reach the transcript until the question is answered.
            self.read_transcript()
            self.open_q[h["tool_use_id"]] = h.get("tool_input") or {}
            self.ask_open()
        elif e == "Notification" and h.get("notification_type") == "permission_prompt" and not self.open_q:
            if self.TOOL_PROMPT.match(str(h.get("message", ""))):
                aid = f"perm-{time.monotonic_ns()}"
                self.asks[aid] = {"Allow": "1", "Deny": "\x1b"}
                self.b.ask(aid, str(h["message"]), ["Allow", "Deny"])
            else:  # "Session paused" and other dialogs whose choices are not Allow or Deny
                self.dialog_ask(str(h.get("message") or "Claude needs your input"))

    # A tool approval's notification; any other permission_prompt is a dialog of its own choices. Claude Code 2.1.317
    # sends "Session paused" for two: a refused message (retry on the fallback model, or edit the prompt) and a model
    # that needs usage credits (continue on credits, or switch models)
    TOOL_PROMPT = re.compile(r"Claude needs your permission\b|.* needs your (permission|approval) to use ")
    DOWN, UP = "\x1b[B", "\x1b[A"

    def dialog_ask(self, why: str, wait: float = 3.0) -> None:
        """The dialog on Claude's screen as a card: its own options, each pressing the arrow keys to it, then Enter. One
        it cannot read (still drawing after `wait` seconds, or laid out another way) is a note to answer in the terminal."""
        end = time.monotonic() + wait
        while (d := screen_dialog(self.grid.lines())) is None and time.monotonic() < end:
            time.sleep(0.2)
        if d is None:
            self.b.note(f"Claude: {why}. Its question shows only in the terminal: answer it there.", err=True)
            self.b.state("needs_you", why)
            return
        text, labels, at = d
        aid = f"dlg-{time.monotonic_ns()}"
        self.asks[aid] = {x: [self.DOWN if i > at else self.UP] * abs(i - at) + ["\r"] for i, x in enumerate(labels)}
        self.dialog = (aid, labels)
        self.b.ask(aid, f"**{why}**\n\n{text}" if text else why, labels)

    def dialog_gone(self) -> None:
        """Its card goes grey once the dialog leaves the screen (answered in the terminal, or by its card)."""
        if not self.dialog:
            return
        if (d := screen_dialog(self.grid.lines())) is not None and d[1] == self.dialog[1]:
            self.dialog = self.dialog[:2]
        elif len(self.dialog) == 2:
            self.dialog = (*self.dialog, time.monotonic())  # gone for a full second, not one frame of a redraw
        elif time.monotonic() - self.dialog[2] > 1:
            aid, self.dialog = self.dialog[0], None
            if self.asks.pop(aid, None) is not None:
                self.b.answered(aid)

    def record(self, ev: dict) -> None:
        """One transcript record."""
        t = ev.get("type")
        if ev.get("isSidechain"):
            return  # subagents' own steps: their result shows as the Task tool's output
        if ev.get("isMeta") or ev.get("isCompactSummary"):  # injected for the agent, not typed: debug mode only
            content = (ev.get("message") or {}).get("content")
            texts = [content] if isinstance(content, str) else [c.get("text", "") for c in content or []
                                                                 if c.get("type") == "text"]
            for x in texts:
                self.b.hidden("compaction summary" if ev.get("isCompactSummary") else "added for the agent", x)
                if (g := GOAL_SET.search(x)) and not ev.get("isCompactSummary"):  # /goal: shown as typed (no longer
                    self.b.user(f"/goal {g[1]}")  # "Sending…"), and the bar's Goal chip till it is met or cleared
                    self.set_goal(g[1])
            return
        if t == "assistant":
            msg = ev.get("message") or {}
            if ev.get("isApiErrorMessage"):
                self.b.note(util.text_of(msg.get("content")) or str(ev.get("error") or "The model request failed"), err=True)
                return
            if msg.get("model") and not msg["model"].startswith("<"):
                self.model = msg["model"]
            if not self.busy and self.fresh(ev):  # a turn no prompt started (a background task's report, say)
                self.working(True)
            new = claude_blocks(msg.get("content"))
            self.blocks.setdefault(msg.get("id"), []).extend(new)
            self.b.msg(msg.get("id"), self.blocks[msg.get("id")])
            for x in new:
                if x["kind"] == "tool" and x["name"] == "AskUserQuestion":
                    self.open_q[x["id"]] = x["args"]
        elif t == "user":
            content = (ev.get("message") or {}).get("content")
            for c in content if isinstance(content, list) else []:
                if c.get("type") == "tool_result":
                    tid = c.get("tool_use_id")
                    self.b.tool_end(tid, util.text_of(c.get("content")), c.get("is_error"))
                    self.open_q.pop(tid, None)
                    self.qstep.pop(tid, None)
                    for aid in [a for a, x in self.asks.items() if isinstance(x, dict) and x.get("q") == tid]:
                        del self.asks[aid]  # answered in the terminal, not with its buttons
                        self.b.answered(aid)
            texts = [content] if isinstance(content, str) else [c.get("text", "") for c in content or []
                                                                 if c.get("type") == "text"]
            for x in map(unpaste, texts):  # wrappers (system reminders, slash commands, hook output) the chat leaves out
                if x.strip().startswith("<local-command-"):  # Claude Code 2.1.322 writes a slash command's reply here
                    self.local_reply(x.strip())
                elif x.strip().startswith("<"):
                    self.b.hidden("added for the agent", x)
            for text in self.typed(ev):
                self.b.user(text)
                if text.startswith("[Request interrupted by user") and self.fresh(ev):  # Esc: no Stop hook follows
                    self.working(False)
        elif t == "attachment" and (ev.get("attachment") or {}).get("type") == "goal_status":
            a = ev["attachment"]
            self.set_goal("" if a.get("met") else str(a.get("condition") or ""))
            if a.get("met"):
                self.b.note("Goal met")
        elif t == "attachment" and (ev.get("attachment") or {}).get("type") not in (None, "queued_command"):
            a = ev["attachment"]  # hook output, files and reminders Claude Code attached to the turn
            self.b.hidden(f"attachment: {a.get('type')}", {k: v for k, v in a.items() if k != "type"})
        elif t == "attachment" and (ev.get("attachment") or {}).get("type") == "queued_command":
            # typed while it worked (Send now): Claude takes it in mid-turn as an attachment, not a "user" record. A long
            # one (an agent's handoff) comes wrapped in <pasted_content>: unwrapped, or it stayed "Sending…" on the page
            text = unpaste(str(util.text_of(ev["attachment"].get("prompt")) or "")).strip()
            if text and not text.startswith("<"):
                self.b.user(text)
        elif t == "system" and ev.get("subtype") == "local_command":  # before 2.1.322, a slash command's reply
            self.local_reply(str(ev.get("content", "")))
        elif t == "system" and ev.get("subtype") == "turn_duration":
            if self.busy and self.fresh(ev):  # the turn's last record: over, Stop hook or not
                self.working(False)
        elif t == "system" and ev.get("subtype") == "compact_boundary":
            self.b.note("Conversation compacted")
        elif t == "permission-mode" and ev.get("permissionMode"):
            self.mode = ev["permissionMode"]
        elif t in ("custom-title", "ai-title"):
            self.b.name(ev.get("customTitle") or ev.get("aiTitle") or "")

    def local_reply(self, content: str) -> None:
        """A slash command's reply, "<local-command-stdout>Set model to …</local-command-stdout>", as a note. /model and
        /effort say "saved as your default for new sessions": for a Deck chat it is this session's pick only, so the host's
        defaults go back (restore_defaults); either reply also lets the next line be typed (wait_ready)."""
        m = re.match(r"<local-command-(stdout|stderr)>(.*)</local-command-\1>$", content, re.S)
        if not (m and m.group(2).strip()):
            return
        reply = m.group(2).strip()
        if reply.startswith(("Goal cleared", "No goal set")):
            self.set_goal("")
        self.pending_until = 0.0  # the command ran (or failed): the next line can go
        if "saved as your default" in reply:
            self.restore_defaults()
            reply = reply.replace(" and saved as your default for new sessions", " for this session")
            reply = reply.replace(" (saved as your default for new sessions)", " (this session)")
        self.b.note(reply, err=m.group(1) == "stderr")

    def ask_open(self) -> None:
        """AskUserQuestion: a card per question, in turn, as its dialog goes from one to the next."""
        for qid in self.open_q:
            if qid not in self.qstep:
                self.qstep[qid] = 0
                self.ask_step(qid)

    @staticmethod
    def q_labels(q: dict) -> list:
        return [str(o.get("label", "")) for o in q.get("options") or []][:8]

    def ask_step(self, qid: str) -> None:
        qs, i = self.open_q[qid].get("questions") or [{}], self.qstep[qid]
        q, aid = qs[i], f"{qid}#{i}"
        multi = bool(q.get("multiSelect"))
        self.asks[aid] = {"q": qid, "multi": multi}
        title = str(q.get("question", "")) + (f" ({i + 1} of {len(qs)})" if len(qs) > 1 else "")
        # typing into a multi-select's own "Type something" row doesn't take (tried), so it gets no box; a question with
        # previews has no such row, so its typed answer goes by "Chat about this" (answer_q)
        self.b.ask(aid, title, self.q_labels(q), multi=multi, other=not multi, skip=True)

    @staticmethod
    def has_previews(q: dict) -> bool:
        """Options with previews: Claude's dialog shows them side by side, where a number only moves the cursor (Enter
        picks), and there is no "Type something" row."""
        return not q.get("multiSelect") and any(o.get("preview") for o in q.get("options") or [] if isinstance(o, dict))

    def open_ask(self):
        """The card of the question its dialog is on, if one is open."""
        return next((a for a, x in self.asks.items() if isinstance(x, dict) and x.get("q") in self.open_q), None)

    def answer_q(self, aid: str, v) -> None:
        """One question's answer, as keys into Claude's dialog: an option's number (a single choice moves on by
        itself); a multi-select's numbers, then → to leave it; "Type something" (the number after the options), the
        text pasted, Enter. None: Esc, which declines the whole call. Several questions, or a multi-select, end on a
        review screen whose 1 submits."""
        qid = self.asks.pop(aid)["q"]
        self.b.answered(aid)
        if qid not in self.open_q:
            return
        qs, i = self.open_q[qid].get("questions") or [{}], self.qstep[qid]
        q, labels = qs[i], self.q_labels(qs[i])
        if v is None:
            self.qstep[qid] = len(qs)
            self.write("\x1b")
            return
        preview = self.has_previews(q)
        if isinstance(v, dict) and str(v.get("other", "")).strip() and preview:
            # no "Type something" row: "Chat about this" (below the options) ends the dialog with the picks so far,
            # then the text goes as a message
            self.qstep[qid] = len(qs)
            self.keys([str(len(labels)), "\x1b[B", "\r"])
            time.sleep(1)
            self.type(str(v["other"]).strip())
            self.b.state("working")
            return
        if isinstance(v, dict) and str(v.get("other", "")).strip():
            keys = [str(len(labels) + 1), "\x1b[200~" + str(v["other"]).strip() + "\x1b[201~", "\r"]
        elif v in labels and preview:
            keys = [str(labels.index(v) + 1), "\r"]
        elif isinstance(v, list) and q.get("multiSelect"):
            keys = [str(labels.index(x) + 1) for x in v if x in labels] + ["\x1b[C"]
        elif v in labels:
            keys = [str(labels.index(v) + 1)]
        else:
            return
        last = i == len(qs) - 1
        if last and (len(qs) > 1 or q.get("multiSelect")):
            keys.append("1")
        for k in keys:
            self.write(k)
            time.sleep(0.4)  # one at a time: a burst is a paste; and each screen draws before the next key
        if last:
            self.b.state("working")
        else:
            self.qstep[qid] = i + 1
            self.ask_step(qid)

    @staticmethod
    def follow(path: Path, pos: int) -> tuple:
        """Complete JSON lines appended to path since pos: (records, new pos)."""
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                data = f.read()
        except FileNotFoundError:
            return [], pos
        if b"\n" not in data:
            return [], pos
        data = data[:data.rindex(b"\n") + 1]
        out = []
        for line in data.split(b"\n"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out, pos + len(data)

    def read_transcript(self) -> list:
        """Drain available records before any question cards, advancing the cursor exactly once."""
        if self.path is None:
            return []
        recs, self.pos = self.follow(self.path, self.pos)
        for r in recs:
            self.record(r)
        if recs:
            self.ask_open()
            self.redraw()
        return recs

    def run(self, first: str) -> int:
        self.prompt_lines = first.count("\n") + 1
        # The model it starts on: the session's pick, else the --model it is given (Deck's Default model), else the
        # host's own. The settings file's alone showed a host default it was not running (cloud: "fable" for opus)
        flag = self.argv[self.argv.index("--model") + 1] if "--model" in self.argv[:-1] else ""
        self.redraw(self.b.prefs.get("model") or flag or self.settings().get("model") or "Claude",
                    self.b.prefs.get("mode") or ("bypassPermissions" if self.yolo else "default"))
        env = {k: v for k, v in os.environ.items() if k not in CLAUDE_SESSION_VARS}
        self.p = Term([*self.argv, *([first] if first else [])], self.cwd, env)
        self.b.state("working" if first else "your_turn")
        out = threading.Thread(target=self.pump, daemon=True)
        out.start()
        while out.is_alive():
            hooks, self.hooks_pos = self.follow(self.hooks_path, self.hooks_pos)
            for h in hooks:
                self.hook(h)
            recs = self.read_transcript()
            self.heads_up()
            self.dialog_gone()
            if self.stopping is not None and time.monotonic() - self.stopping > self.STOP_WAIT:
                self.working(False)  # Stop came but no turn_duration record: it ends anyway
            if not (hooks or recs):
                time.sleep(0.2)
        return self.p.wait()

    # "You should know" card buttons, by the card's state: label -> the hotkey(s) its terminal card takes
    YSK_KEYS = {
        "offer": {"Learn more": "1", "Knew this already": "2", "Dismiss": "0", "Turn off suggestions": ["4", "4"]},
        "explained": {"Understood": "1", "Chat about it": "2", "Dismiss": "0"},
        "asked": {"Helpful": "1", "Not relevant": "2", "Didn't understand": "3", "Dismiss": "0"},
        "asked_again": {"That was helpful": "1", "Didn't understand": "2", "Dismiss": "0"},
        "ready": {"Helpful": "1", "Not helpful": "2", "Didn't read": "3"},
        "failed": {"OK": "0"},
    }

    def heads_up(self) -> None:
        """Claude's "You should know" plugin: a side agent whose card shows above the terminal's prompt, out of the
        chat's sight. Its store keeps each session's card (view: offer, explaining, explained, asked, ...); this
        conversation's goes to the chat as a question whose buttons press the card's hotkeys in the terminal."""
        store = next((Path.home() / ".claude" / "plugins" / "store").glob("cc-plugin-you-should-know_*.json"), None)
        y = self.ysk
        try:
            if store is None or self.path is None or store.stat().st_mtime_ns == y["mtime"]:
                return
            y["mtime"] = store.stat().st_mtime_ns
            d = json.loads(store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        v = next((x.get("view") for x in d.get("sessions") or [] if x.get("id") == self.path.stem), None) or {}
        kind, line = v.get("kind"), str(v.get("line") or "")
        if kind == "asked" and v.get("iteration") is not None:
            kind = "asked_again"
        card = (kind, line, v.get("text"), v.get("url"), v.get("reason"))
        if card == y["card"]:
            return
        y["card"] = card
        if y["ask"]:  # the old card's buttons go grey: it changed in the terminal, or a button was pressed
            self.asks.pop(y["ask"], None)
            self.b.answered(y["ask"])
            y["ask"] = None
        if kind in ("explaining", "making"):
            self.b.note("You should know: one moment…")
        if kind not in self.YSK_KEYS:
            return
        title = {
            "offer": f"**You should know:** {line}",
            "explained": f"**You should know:** {line}\n\n{v.get('text') or ''}".rstrip(),
            "asked": f"Was this useful? {line}",
            "asked_again": f"Was the explanation useful? {line}",
            "ready": f"Learning page ready: {v.get('url') or ''}",
            "failed": "You should know: that didn't work" + (f" ({v['reason']})" if v.get("reason") else ""),
        }[kind]
        if kind == "explained":
            title += "\n\n*Chat about it: your next message goes to Claude with this card.*"
        y["n"] = y.get("n", 0) + 1  # the clock alone repeats within ~15 ms on Windows
        aid = f"ysk-{time.monotonic_ns()}-{y['n']}"
        y["ask"] = aid
        self.asks[aid] = dict(self.YSK_KEYS[kind])
        self.b.emit({"t": "ask", "id": aid, "title": title, "options": list(self.YSK_KEYS[kind])})  # not "needs you"

    def kill(self) -> None:
        if self.p is not None:
            self.p.kill()

    # ---- transcripts ----
    def projects(self) -> Path:
        """Where Claude keeps this folder's transcripts, one <session id>.jsonl each."""
        return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", self.cwd)

    def conversations(self) -> list:
        """This folder's transcripts, labelled like claude --resume: its title (one set with /rename, else the one it
        generated) or else the first message. Transcripts run to megabytes: only their ends are read (titles are
        rewritten as the conversation goes on, so the newest is near the end)."""
        out = []
        for f in sorted(self.projects().glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:40]:
            with open(f, "rb") as fh:
                head = fh.read(256 * 1024)
                fh.seek(max(0, f.stat().st_size - 64 * 1024))
                tail = fh.read()
            titles, first = {}, ""
            for line in (head + b"\n" + tail).splitlines():
                if not (b'-title"' in line or (not first and b'"type":"user"' in line)):
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") in ("custom-title", "ai-title"):
                    titles[e["type"]] = e.get("customTitle") or e.get("aiTitle") or titles.get(e["type"], "")
                elif e.get("type") == "user" and not first:
                    first = next(iter(self.typed(e)), "")
            if first and f.stem != self.b.status.get("resume"):  # not a stub with nothing said, nor this one
                label = titles.get("custom-title") or titles.get("ai-title") or first
                out.append((f"{time.strftime('%b %d %H:%M', time.localtime(f.stat().st_mtime))}  {label}", f.stem))
        return out

    @staticmethod
    def typed(ev: dict) -> list:
        """What the user typed in a transcript's "user" record: not tool results, slash command or hook wrappers, the
        summary a compaction leaves, or a subagent's prompt."""
        if ev.get("isSidechain") or ev.get("isMeta") or ev.get("isCompactSummary") or ev.get("parent_tool_use_id"):
            return []
        content = (ev.get("message") or {}).get("content")
        texts = [content] if isinstance(content, str) else [c.get("text", "") for c in content or [] if c.get("type") == "text"]
        texts = [unpaste(x) for x in texts]
        return [x for x in texts if x.strip() and not x.startswith("<")]

    # ---- the bar ----
    def redraw(self, model: str = "", mode: str = "") -> None:
        """The bar as the terminal's status line starts it: "Opus 5.5 xhigh", then the permission mode."""
        self.model, self.mode = model or self.model, mode or self.mode
        m = re.match(r"claude-([a-z]+)-(\d+)-(\d{1,2})(?!\d)", self.model)
        name = f"{m[1].capitalize()} {m[2]}.{m[3]}" if m else self.model
        self.b.bar(f"{name} {self.effort or self.settings().get('effortLevel', '')}".strip(), *self.usage,
                   *([f"Goal: {self.goal[:60]}{'…' if len(self.goal) > 60 else ''}"] if self.goal else []), mode=self.mode)

    def set_goal(self, goal: str) -> None:
        """The /goal in force (Claude Code's Stop hook), as a chip on the bar."""
        if goal != self.goal:
            self.goal = goal
            self.redraw()

    @staticmethod
    def settings() -> dict:
        try:
            return json.loads((Path.home() / ".claude" / "settings.json").read_text())
        except (OSError, ValueError):
            return {}
