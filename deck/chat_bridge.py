"""Chat sessions: runs an agent in its machine-readable mode inside a tmux pane and turns its output into Deck chat
events, so the phone shows a chat instead of a terminal. Stdlib only, plus pywinpty for Claude on Windows (the
server's Python has it); started by core.build_argv as

    python chat_bridge.py <agent> <chat dir> <session> <resume 0|1> <first message> -- <agent args...>

Files in <chat dir> (the server tails and appends them, see server.terminal_chat):
    <session>.jsonl     events for the page, rewritten at each start: {"t": "clear"} then the history, then live ones
    <session>.in.jsonl  commands from the page: {"t": "say", "text"}, {"t": "abort"}, {"t": "answer", "id", "value"},
                        {"t": "sendnow"} (the waiting messages, now), {"t": "unhold"} (take them all back to edit)
    <session>.state     {"state", "preview", "name"} for the Agents feed and the tab title, and "resume": the agent's
                        own id for the conversation, read back at a restart (Bridge.prev_resume)
    <session>.hooks.jsonl  Claude's hook input (`chat_bridge.py hook <chat dir> <session>` appends it)
    <session>.media/    files the agent's replies carry (OpenClaw's images and attachments), for the page through
                        server.chat_media

Events: clear | user {text} | delta {m, i, kind, d} (streamed text / thinking) | msg {m, blocks} (an assistant message
so far; replaces its deltas) | tool_end {id, out, err} | state {s} | note {text, err} | name {text} | status {parts}
(model, effort, mode: the bar above the chat) | ask {id, title, options, multi?, other?, skip?} (answer value: a
label, a list of labels (multi), {other: text}, or null to skip) | answered {id, how?} (how: its end, e.g. Expired) |
queued {id, text, keys} (typed while it works: sent at once, waiting till its "user" event; keys: Send now and Edit
work) | unqueued {id} (taken back).
Blocks: {kind: text|think, text} or {kind: tool, id, name, args} or {kind: file, name, mime, size, file?, err?} (an
image or attachment: file is its name in <session>.media once fetched, err why it couldn't be).
Each agent has an adapter mapping its own protocol onto these: Pi (pi --mode rpc), Claude (run interactively in a
pseudo-terminal: its transcript and hooks), Codex (codex app-server), OpenCode (opencode serve),
agy (print mode, stream-json both ways),
OpenClaw (its Gateway, through deploy/agents/openclaw-bridge)."""
import base64
import http.client
import json
import mimetypes
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

WINDOWS = sys.platform == "win32"
OUT_LIMIT = 4000  # characters of tool output kept per call


def resolve(cmd: str) -> list[str]:
    """argv for a CLI. Windows: npm-style .cmd shims go through cmd.exe, which mangles arguments holding quotes or
    newlines (the Deck context), so run what the shim runs: its .js under node, or its .exe."""
    path = shutil.which(cmd)
    if not path and cmd == "agy":
        # This file is also launched directly by tmux, without the package on sys.path.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from deck.core import agy_path
        path = agy_path()
    if not path:
        raise SystemExit(f"{cmd} not found")
    if WINDOWS and path.lower().endswith(".cmd"):
        m = re.search(r'%~?dp0%?\\?([^"%\r\n]+?\.(js|exe))\b', Path(path).read_text(errors="replace"))
        if m:
            target = str(Path(path).parent / m.group(1))
            return [shutil.which("node") or "node", target] if m.group(2) == "js" else [target]
    return [path]


EDIT_TOOLS = {"edit", "write", "multiedit", "notebookedit", "apply_patch", "patch"}


PASTED = re.compile(r'\s*<pasted_content id="([^"]*)">\n?(.*?)\n?</pasted_content(?: id="\1")?>\s*', re.S)


def unpaste(text: str) -> str:
    """A message as typed: Claude Code wraps a long paste (how the bridge types every message) in <pasted_content> tags."""
    return PASTED.sub(lambda m: m[2], text).strip() if "<pasted_content" in text else text


def edited_path(tool: dict) -> str:
    """The file a tool call writes (Claude's Edit/Write, Codex's file changes, Pi's and OpenCode's edit/write), else ""."""
    if str(tool.get("name", "")).lower() not in EDIT_TOOLS or not isinstance(tool.get("args"), dict):
        return ""
    a = tool["args"]
    p = a.get("file_path") or a.get("filePath") or a.get("notebook_path") or a.get("path") or ""
    return str(p).split(", ")[0] if p else ""


def repo_root(path: str, cwd: str) -> str:
    """The git repo (a folder holding .git, a worktree's .git file too) a path is in, as a native path; "" for none."""
    if not path:
        return ""
    if WINDOWS and (m := re.match(r"^/([a-zA-Z])(/.*)?$", path)):  # MSYS /c/Users/... from a Bash tool
        path = f"{m[1].upper()}:{m[2] or '/'}"
    p = Path(cwd, os.path.expanduser(path))
    for d in [p, *p.parents]:
        try:
            if (d / ".git").exists():
                return str(d)
        except OSError:
            return ""
    return ""


def show(text: str) -> None:
    """The pane's own view: a plain transcript, for anyone who opens it as a terminal. Windows: ASCII only (a native
    program's output reaches an MSYS pane through a pipe that mangles anything else)."""
    sys.stdout.write(text)
    sys.stdout.flush()


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def jsonl(stream):
    """Records from a JSONL pipe, split on LF only (JSON strings may hold U+2028)."""
    buf = b""
    while chunk := stream.read1(65536):
        buf += chunk
        *lines, buf = buf.split(b"\n")
        for line in lines:
            try:
                yield json.loads(line)
            except ValueError:
                pass


class Bridge:
    def __init__(self, folder: str, session: str):
        self.session = session
        self.repo, self.edits = "", {}  # the repo the session's project follows, and file edits per repo (follow_repo)
        d = Path(folder)
        d.mkdir(parents=True, exist_ok=True)
        self.out_path, self.in_path, self.state_path = (d / f"{session}{x}" for x in (".jsonl", ".in.jsonl", ".state"))
        # Capture before the adapter starts: startup can sync notes for seconds, while the page already accepts input.
        self.command_pos = self.in_path.stat().st_size if self.in_path.exists() else 0
        self.media = d / f"{session}.media"  # made when a reply first carries a file; kept across starts (a cache)
        self.out = open(self.out_path, "w", encoding="utf-8")  # a new log each start; the page sees "clear" first
        self.lock = threading.Lock()
        try:  # the previous start's state: its "resume" id is the conversation a Restart continues
            prev = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}
        self.prev_resume = str(prev.get("resume") or "") if isinstance(prev, dict) else ""
        self.started_before = isinstance(prev, dict) and bool(prev)  # a chat of ours: no resume id means a new one, not the newest
        # the session's own choices (/model, /effort, /mode): every start applies them, so Restart, /resume and /new keep them
        self.prefs = prev.get("prefs") if isinstance(prev, dict) and isinstance(prev.get("prefs"), dict) else {}
        self.status = {"state": "working", "preview": "", "name": "", "resume": self.prev_resume, "prefs": self.prefs}
        # Moved here from another host (session_move.py): {id, path} of a Codex rollout its index hasn't seen yet
        self.moved = prev.get("moved") if isinstance(prev, dict) and isinstance(prev.get("moved"), dict) else {}
        if self.moved:
            self.status["moved"] = self.moved
        self.closed = False  # the pane is gone: a restart's new bridge may already own the files
        self.switch_to = None  # set by Adapter.switch: main() restarts the agent on that conversation ("" = a new one)
        self.waiting = []  # messages typed while it works, not yet taken in: [(id, text)] (user() drops them)

    def emit(self, ev: dict) -> None:
        with self.lock:
            if self.closed:
                return
            ev.setdefault("ts", round(time.time()))  # when it happened: the page shows it on messages and replies
            self.out.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.out.flush()

    def save(self, **fields) -> None:
        """The .state file: the Agents feed's state and preview, the session's name (its tab title), and resume: the
        agent's own id for this conversation (session id, thread id, session file), so a Restart continues this one
        and not merely the folder's newest."""
        # Agent output and the command pump can save together. Serialize the shared temporary file too: otherwise
        # one thread renames it away before the other's replace, surfacing a false send failure in the chat.
        with self.lock:
            if "resume" in fields and fields["resume"] == self.status.get("resume"):
                del fields["resume"]  # unchanged (Pi reports it after every turn)
                if not fields:
                    return
            if "preview" in fields:
                fields["preview"] = " ".join(fields["preview"].split())[:160]
            self.status.update(fields)
            if self.closed:
                return
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.status, ensure_ascii=False), encoding="utf-8")
            # Windows: the server may briefly hold the old file open while reading session state.
            for _ in range(25):
                try:
                    tmp.replace(self.state_path)
                    return
                except PermissionError:
                    time.sleep(0.02)

    def pref(self, **fields) -> None:
        """Remember a choice (model, effort, mode) for this session's later starts."""
        self.prefs.update(fields)
        self.save(prefs=dict(self.prefs))

    def state(self, s: str, preview: str | None = None) -> None:
        self.emit({"t": "state", "s": s})
        self.save(state=s, **({} if preview is None else {"preview": preview}))

    def user(self, text: str) -> None:
        raw = text  # as the agent got it, stamp and all: the page's debug mode shows it
        m = STAMP.match(text)  # sent from Deck: its stamp says when and from where (a resumed conversation's too)
        if m:
            text = text[m.end():]
        said = " ".join(text.split())
        self.waiting = [w for w in self.waiting if " ".join(w[1].split()) not in said]
        agent = bool(m and AGENT_FROM.fullmatch(m[2] or ""))
        if not self.status["name"] and said and not said.startswith(("/", "[Deck]")):
            self.name(name_from(said))  # till the agent names it (Codex never does; OpenCode's titler can fail)
        self.emit({"t": "user", "text": text, **({"raw": raw} if raw != text else {}),
                   **({"ts": round(datetime.strptime(m[1], "%a %Y-%m-%d %H:%M %z").timestamp()), "from": m[2] or ""} if m else {})})
        show(f"\n\x1b[1m> {f'[from {m[2]}, not the user] ' if agent else ''}{text}\x1b[0m\n")

    def hidden(self, why: str, text) -> None:
        """Something the agent got that the chat doesn't show (a system reminder, hook output, a command's wrapper): the
        page shows it only in its debug mode (Settings → Debug)."""
        text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1)
        if text and text.strip():
            self.emit({"t": "hidden", "why": why, "text": text[:20000]})

    def delta(self, m, i, kind: str, d: str) -> None:
        if d:
            self.emit({"t": "delta", "m": m, "i": i, "kind": kind, "d": d})

    def msg(self, m, blocks: list[dict], final: bool = True) -> None:
        """An assistant message (all its blocks so far). final: show it in the pane and take its text as the preview."""
        blocks = [x for x in blocks if x["kind"] in ("tool", "file") or x.get("text")]
        self.emit({"t": "msg", "m": m, "blocks": blocks})
        if not final:
            return
        for x in blocks:
            if x["kind"] == "tool":
                self.follow_repo(x)
        for x in blocks:
            if x["kind"] == "text":
                show(f"\n{x['text']}\n")
            elif x["kind"] == "file":
                show(f"[{x['name']}]\n")
            elif x["kind"] == "tool":
                show(f"\x1b[90m- {x['name']} {json.dumps(x['args'])[:100]}\x1b[0m\n")
        text = next((x["text"] for x in reversed(blocks) if x["kind"] == "text"), "")
        if text:
            self.save(preview=text)

    def follow_repo(self, tool: dict) -> None:
        """The session's project follows the repo its agent edits files in (sessions mostly start in the work folder,
        and agents forget to tell Deck), so its main-page bar shows that project's page. Edits only: reading another
        repo's notes (infra's AGENTS.md at the start) is not working in it. Once it follows one, another takes two edits:
        a note recorded in another repo (infra's PREFERENCES.md) doesn't move it."""
        root = repo_root(edited_path(tool), os.getcwd())
        if not root:
            return
        self.edits[root] = self.edits.get(root, 0) + 1
        if root == self.repo or (self.repo and self.edits[root] < 2):
            return
        self.repo = root
        def put():
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{os.environ.get('DECK_PORT', '8787')}/api/sessions/{urllib.parse.quote(self.session)}/project",
                    data=json.dumps({"folder": root}).encode(), method="PUT", headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10).close()
            except OSError:
                pass  # the server is restarting, or the folder is in no project: the next edit elsewhere tries again
        threading.Thread(target=put, daemon=True).start()

    def tool_end(self, id_, out, err: bool = False) -> None:
        out = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        if len(out) > OUT_LIMIT:
            out = out[:OUT_LIMIT // 2] + "\n…\n" + out[-OUT_LIMIT // 2:]
        self.emit({"t": "tool_end", "id": id_, "out": out, "err": bool(err)})

    def note(self, text: str, err: bool = False) -> None:
        self.emit({"t": "note", "text": text, "err": err})

    def name(self, name: str) -> None:
        if name and name != self.status["name"]:
            self.emit({"t": "name", "text": name})
            self.save(name=name)

    def bar(self, *parts, mode: str = "") -> None:
        """The status bar's chips; mode (a permission mode) goes last, coloured like the terminal's."""
        self.emit({"t": "status", "parts": [str(p) for p in parts if p], **({"mode": mode} if mode else {})})

    def ask(self, id_, title: str, options: list[str], **more) -> None:
        """A question card. more: multi (options toggle, then Submit), other (a box to type an answer), skip."""
        self.emit({"t": "ask", "id": id_, "title": title, "options": options, **more})
        self.state("needs_you", title)

    def answered(self, id_, how: str = "") -> None:
        """A card's question is done; how: what its card says when no choice was made here (default "Answered")."""
        self.emit({"t": "answered", "id": id_, **({"how": how} if how else {})})

    def commands(self):
        """Commands appended after this start (older ones were for the previous process)."""
        pos = self.command_pos
        while True:
            try:
                with open(self.in_path, "rb") as f:
                    f.seek(pos)
                    data = f.read()
            except FileNotFoundError:
                data = b""
            if b"\n" in data:
                chunk = data[:data.rindex(b"\n") + 1]
                pos += len(chunk)
                for line in chunk.splitlines():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        pass
            else:
                time.sleep(0.15)


# What Deck puts before each message it sends an agent: "[Thu 2026-10-08 14:03 -0700 · Android phone, phone app] "
def name_from(text: str) -> str:
    """A chat's name from its first message, as Deck's Pi extension makes one (deploy/agents/pi-name.ts)."""
    line = " ".join(text.strip().split("\n")[0].split())
    if len(line) <= 40:
        return line
    cut = line[:40]
    return (cut[:cut.rindex(" ")] if cut.rfind(" ") > 20 else cut) + "…"


# /goal: Claude Code records it only as this note for the agent, never as the typed command (user: "goal is bugged")
GOAL_SET = re.compile(r'A session-scoped Stop hook is now active with condition: "(.*?)"\. Briefly acknowledge', re.S)
STAMP = re.compile(r"\[(\w{3} \d{4}-\d\d-\d\d \d\d:\d\d [+-]\d{4})(?: · ([^\]]*))?\] ?")
# The stamp's source when another session's agent sent the message (server.py agent_session_send): the page labels it,
# and the pane and Copy text mark it, so no one takes it for the user's (app.js and core.chat_text match it too)
AGENT_FROM = re.compile(r"agent in .+|another agent")


class Adapter:
    """One agent. command() takes the page's commands, run() drives the agent until it exits (its exit code)."""
    YOLO = ""  # the agent's skip-permissions flag in args: the adapter applies it itself

    def __init__(self, b: Bridge, args: list[str], resume: bool, cwd: str):
        self.b, self.resume, self.cwd, self.busy = b, resume, cwd, False
        self.yolo = self.YOLO in args
        self.args = [a for a in args if a != self.YOLO]
        self.p = None  # the agent's process
        self.wlock = threading.Lock()
        self.picks = {}  # pick() asks still open: id -> (then, {label: value})
        self.quiz = None  # questions() asked and not all answered: their cards go one at a time
        self.menu = None  # the page's tool row asked for a small menu (its request id): the next pick() goes there

    def start(self, argv: list[str]) -> None:
        self.p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=self.cwd)

    def send(self, obj: dict) -> None:
        with self.wlock:
            self.p.stdin.write(json.dumps(obj).encode() + b"\n")
            self.p.stdin.flush()

    def working(self, on: bool) -> None:
        if on != self.busy:
            self.busy = on
            self.b.state("working" if on else "your_turn")

    # Slash commands typed in the chat (the Harness buttons send /compact and /resume): "/name arg" runs slash_<name>
    # when the adapter has one; anything else goes to the agent as a message.
    def handle(self, c: dict) -> None:
        if c.get("t") == "menu":  # the page's tool row (Resume, Model, Effort): no message, choices in a menu there
            m = re.match(r"^/([a-z][\w-]*)\s*(.*)$", str(c.get("text", "")).strip(), re.S)
            fn = m and getattr(self, "slash_" + m.group(1).replace("-", "_"), None)
            self.menu = str(c.get("rid", "")) if fn else None
            if fn:
                fn(m.group(2).strip())
            else:
                self.b.emit({"t": "menu", "rid": c.get("rid"), "options": []})
            return
        if c.get("t") == "say":
            self.menu = None  # a typed command's choices go to the chat
        text = str(c.get("text", "")).strip() if c.get("t") == "say" else ""
        if text and re.fullmatch(r"/+", text):
            # a stray "/" (user 2026-10-26): typed into Claude's terminal it opens the command picker, and the Enter
            # after it ran whichever command stood first in the list. Nothing to run: say so and drop it.
            self.b.note("A lone / isn't a command: type the command's name after it, or send a message")
            return
        src = " ".join(str(c.get("from") or "").replace("]", ")").split())
        # the agent sees when, and from which device, each message was sent; not into an open question's dialog,
        # where the stamp's digits would pick options
        if text and not text.startswith("/") and not self.asking():
            c = {**c, "text": f"[{datetime.now().astimezone():%a %Y-%m-%d %H:%M %z}{' · ' + src if src else ''}] {text}"}
        m = re.match(r"^/([a-z][\w-]*)\s*(.*)$", text, re.S)
        fn = m and getattr(self, "slash_" + m.group(1).replace("-", "_"), None)
        if fn:
            self.b.user(text)
            fn(m.group(2).strip())
        elif text and self.quiz and self.quiz["qs"][self.quiz["i"]].get("other", True):
            self.quiz_answer({"other": text})  # its questions are open: typed text answers the current one
        elif text and self.quiz:
            self.b.note("Pick an option above, or Skip")
        elif c.get("t") == "answer" and self.quiz and c.get("id") == self.quiz["aid"]:
            self.quiz_answer(c.get("value"))
        elif text and self.asking():  # the agent's question is open: the text answers it (the adapter's command)
            self.command(c)
        elif text and self.busy:  # typed while it works: straight to the agent, as its own box does (it reads it at its
            # next step); the page shows it as waiting till the agent takes it in (its "user" event)
            qid = f"q-{time.monotonic_ns()}"
            self.b.waiting.append((qid, text))
            self.b.emit({"t": "queued", "id": qid, "text": text, "keys": self.QUEUE_KEYS,
                         **({"from": src} if AGENT_FROM.fullmatch(src) else {})})
            self.command(c)
        elif c.get("t") == "sendnow":  # the waiting messages, now (the agent's own send-now)
            self.send_now()
        elif c.get("t") == "unhold":  # Edit: all the waiting messages back out of the agent's queue (the page has their text)
            self.take_back()
        elif c.get("t") == "answer" and c.get("id") in self.picks:
            then, values = self.picks.pop(c["id"])
            self.b.answered(c["id"])
            v = c.get("value")
            if v in values:
                then(values[v])
            else:
                self.b.state("your_turn")
        else:
            if text and c.get("t") == "say":  # shown at once, as sending, till its "user" event (Claude's comes seconds later)
                self.b.emit({"t": "queued", "id": f"q-{time.monotonic_ns()}", "text": text, "keys": False,
                             **({"from": src} if AGENT_FROM.fullmatch(src) else {})})
            self.command(c)

    QUEUE_KEYS = False  # the agent has a queue the page can send now or take back from (send_now, take_back)

    def asking(self) -> bool:
        """The agent's own question is open: typed text answers it (no time stamp: it's an answer, not a message)."""
        return bool(self.quiz)

    def send_now(self) -> None:
        pass

    def take_back(self) -> None:
        pass

    def pick(self, title: str, options: list[tuple[str, object]], then) -> None:
        """Ask the user to choose (buttons in the chat); then(value) runs with the chosen option's value."""
        if not options:
            if self.menu is not None:
                self.b.emit({"t": "menu", "rid": self.menu, "options": []})
                self.menu = None
            else:
                self.b.note("Nothing to choose from")
            return
        labels = {}
        for label, value in options:
            label = " ".join(STAMP.sub("", str(label)).split())[:80] or "(untitled)"  # a conversation's first message: no stamp
            while label in labels:
                label += " "
            labels[label] = value
        pid = f"pick-{time.monotonic_ns()}"
        self.picks[pid] = (then, labels)
        if self.menu is not None:  # asked from the page's tool row: a small menu on its button
            self.b.emit({"t": "menu", "rid": self.menu, "id": pid, "title": title, "options": list(labels)})
            self.menu = None
        else:
            self.b.ask(pid, title, list(labels))

    def questions(self, key: str, qs: list[dict], then) -> None:
        """The agent's own questions, a card each in turn. qs: {question, options: [labels], multi, other}. then(answers)
        runs once all are answered (a list of label lists, one per question, typed answers as labels) or with None when
        one is skipped."""
        self.quiz = {"key": key, "qs": qs, "i": 0, "answers": [], "then": then}
        self.quiz_card()

    def quiz_card(self) -> None:
        z = self.quiz
        q, n = z["qs"][z["i"]], len(z["qs"])
        first, nl, rest = str(q.get("question", "")).partition("\n")  # the count ends its first line (notes may follow)
        title = first + (f" ({z['i'] + 1} of {n})" if n > 1 else "") + nl + rest
        z["aid"] = f"{z['key']}#{z['i']}"
        self.b.ask(z["aid"], title, [str(o) for o in q.get("options") or []], multi=bool(q.get("multi")),
                   other=bool(q.get("other", True)), skip=True)

    def quiz_answer(self, v) -> None:
        z, self.quiz = self.quiz, None
        self.b.answered(z["aid"])
        if v is None:
            self.b.state("working" if self.busy else "your_turn")
            return z["then"](None)
        z["answers"].append([str(v["other"]).strip()] if isinstance(v, dict) else [str(x) for x in v] if isinstance(v, list) else [str(v)])
        z["i"] += 1
        if z["i"] < len(z["qs"]):
            self.quiz = z
            self.quiz_card()
        else:
            self.b.state("working" if self.busy else "your_turn")  # a command's dialog has no turn to go back to
            z["then"](z["answers"])

    def quiz_drop(self, key: str, how: str = "") -> None:
        """Its questions were answered or withdrawn elsewhere (the agent's own UI, a timeout): the card goes, saying how."""
        if self.quiz and self.quiz["key"] == key:
            self.b.answered(self.quiz["aid"], how)
            self.quiz = None

    def switch(self, resume_id: str) -> None:
        """Continue another conversation (resume_id) or start a new one (""): main() ends this agent and starts it again."""
        self.b.switch_to = resume_id
        self.b.status["name"] = ""  # named again by that conversation (its first message, or the agent)
        self.kill()

    def kill(self) -> None:
        kill_tree(self.p)

    def conversations(self) -> list[tuple[str, str]]:
        """This folder's past conversations, newest first: (label, resume id). For /resume."""
        return []

    def slash_resume(self, arg: str) -> None:
        convs = self.conversations()
        if arg:
            convs = [c for c in convs if arg.lower() in c[0].lower() or arg == c[1]]
        self.pick("Resume which conversation?", convs[:12], self.switch)

    def slash_new(self, _arg: str) -> None:
        self.switch("")

    slash_clear = slash_new


def kill_tree(p) -> None:
    if p is None or p.poll() is not None:
        return
    if WINDOWS:  # the agents' launchers start them as children; take the whole tree
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
    else:
        p.terminate()


class Pi(Adapter):
    """pi --mode rpc: JSONL commands on stdin, responses and session events on stdout (Pi's docs/rpc.md)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.m, self.asks, self.n, self.pending, self.provider = 0, {}, 0, {}, ""
        own = self.b.prev_resume if self.resume else ""  # its session file; else the folder's newest
        self.start([*resolve("pi"), "--mode", "rpc", *(["--session", own] if own else ["--continue"] if self.resume else []),
                    *self.args])

    def rpc(self, cmd: dict, then=None) -> None:
        """A command whose response matters: then(data) runs on success (a failure shows as a note)."""
        self.n += 1
        self.pending[f"deck-{self.n}"] = then
        self.send({"id": f"deck-{self.n}", **cmd})

    def conversations(self) -> list[tuple[str, str]]:
        """Pi's session files for this folder (no RPC lists them), labelled like its /resume: the session's name, or
        else its first message. The folder is the running session's own (Pi may be told to keep them elsewhere)."""
        cur = self.b.status.get("resume") or ""
        agent_dir = Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi" / "agent")
        d = Path(cur).parent if cur.endswith(".jsonl") else \
            agent_dir / "sessions" / ("--" + re.sub(r"[/\\:]", "-", re.sub(r"^[/\\]", "", self.cwd)) + "--")
        out = []
        for f in sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:40]:
            name = first = ""
            for line in f.read_bytes().splitlines():
                if b'"session_info"' in line or (not first and b'"role":"user"' in line):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("type") == "session_info":
                        name = e.get("name") or name
                    elif e.get("type") == "message" and (e.get("message") or {}).get("role") == "user":
                        first = text_of(e["message"].get("content")).strip()
            if (name or first) and str(f) != cur:  # not this one
                out.append((f"{time.strftime('%b %d %H:%M', time.localtime(f.stat().st_mtime))}  {name or first}", str(f)))
        return out

    def slash_compact(self, arg: str) -> None:
        def done(data):
            before, after = data.get("tokensBefore"), data.get("estimatedTokensAfter")
            self.b.note(f"Compacted ({before} → about {after} tokens)" if before and after else "Compacted")
        self.rpc({"type": "compact", **({"customInstructions": arg} if arg else {})}, done)

    def slash_model(self, arg: str) -> None:
        """/model: pick from Pi's models; /model <name>: the one matching it (or a pick among several)."""
        def choose(data):
            models = data.get("models") or []
            if arg:
                a, full = arg.lower(), lambda m: f"{m.get('provider')}/{m.get('id')}".lower()
                models = [m for m in models if a in (str(m.get("id")).lower(), full(m), str(m.get("name")).lower())] or \
                         [m for m in models if a in f"{full(m)} {m.get('name')}".lower()]
                if not models:
                    self.b.note(f"No model matches {arg}", err=True)
                    return
                if len(models) == 1:
                    self.set_model(models[0])
                    return
            models = sorted(models, key=lambda m: m.get("provider") != self.provider)  # the current provider's first
            self.pick("Which model?", [(f"{m.get('name') or m.get('id')} ({m.get('provider')})", m) for m in models[:16]],
                      self.set_model)
        self.rpc({"type": "get_available_models"}, choose)

    def set_model(self, m: dict) -> None:
        def done(_):
            self.b.pref(model={"provider": m.get("provider"), "id": m.get("id")})
            self.send({"type": "get_state"})  # its reply redraws the status bar
        self.rpc({"type": "set_model", "provider": m.get("provider"), "modelId": m.get("id")}, done)

    def slash_thinking(self, arg: str) -> None:
        if arg:  # thinking_level_changed redraws the bar
            self.rpc({"type": "set_thinking_level", "level": arg}, lambda _: self.b.pref(effort=arg))
        else:
            self.rpc({"type": "get_available_thinking_levels"},
                     lambda data: self.pick("Thinking level?", [(x, x) for x in data.get("levels") or []], self.slash_thinking))

    slash_effort = slash_thinking  # Pi's effort is its thinking level

    def command(self, c: dict) -> None:
        t = c.get("t")
        if t == "rename":  # the tab renamed: so is the session, as /resume and Pi's own lists show it
            self.rpc({"type": "set_session_name", "name": str(c.get("title", ""))})
            return
        if t == "say" and str(c.get("text", "")).strip():
            cmd = {"type": "prompt", "message": str(c["text"])}
            if self.busy:
                cmd["streamingBehavior"] = "steer"  # typed while working: delivered before its next model call
            self.send(cmd)
        elif t == "abort":
            self.send({"type": "abort"})
        elif t == "answer" and c.get("id") in self.asks:
            method = self.asks.pop(c["id"])
            v = c.get("value")
            res = {"cancelled": True} if v is None else {"confirmed": v == "Yes"} if method == "confirm" else {"value": v}
            self.send({"type": "extension_ui_response", "id": c["id"], **res})
            self.b.answered(c["id"])

    def message(self, msg: dict, live: bool) -> None:
        """A finished message (live, or from the history)."""
        role = msg.get("role")
        if role == "user":
            self.b.user(text_of(msg.get("content")))
        elif role == "assistant":
            if not live:
                self.m += 1
            blocks = []
            for c in msg.get("content") or []:
                if c.get("type") == "text":
                    blocks.append({"kind": "text", "text": c.get("text", "")})
                elif c.get("type") == "thinking":
                    blocks.append({"kind": "think", "text": c.get("thinking", "")})
                elif c.get("type") == "toolCall":
                    blocks.append({"kind": "tool", "id": c.get("id"), "name": c.get("name"), "args": c.get("arguments") or {}})
            self.b.msg(self.m, blocks)
            if msg.get("stopReason") == "aborted" or "operation was aborted" in str(msg.get("errorMessage") or ""):
                self.b.note("Stopped")  # a stopped turn can be saved as an error ("This operation was aborted")
            elif msg.get("stopReason") == "error":
                self.b.note(msg.get("errorMessage") or "The model returned an error", err=True)
        elif role == "toolResult" and not live:  # live ones came as tool_execution_end
            self.b.tool_end(msg.get("toolCallId"), text_of(msg.get("content")), msg.get("isError"))
        elif role == "bashExecution":
            self.b.note(f"$ {msg.get('command')}\n{msg.get('output', '')[-OUT_LIMIT:]}")

    def event(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "response":
            data = ev.get("data") or {}
            then = self.pending.pop(ev.get("id"), None)
            if then and ev.get("success"):
                then(data)
            elif ev.get("command") == "get_messages" and ev.get("success"):
                for msg in data.get("messages", []):
                    self.message(msg, live=False)
                self.b.state("your_turn")
            elif ev.get("command") == "get_state" and ev.get("success"):
                model = data.get("model") or {}
                self.provider = model.get("provider") or self.provider
                self.b.bar(model.get("name") or model.get("id"), data.get("thinkingLevel"))
                self.b.name(data.get("sessionName"))  # a resumed session keeps its name
                if data.get("sessionFile"):
                    self.b.save(resume=data["sessionFile"])
            elif not ev.get("success"):
                self.b.note(ev.get("error") or "command failed", err=True)
        elif t == "agent_start":
            self.working(True)
        elif t == "agent_settled":
            self.working(False)
            self.send({"type": "get_state"})  # the model or thinking level may have changed (/model)
        elif t == "message_start" and (ev.get("message") or {}).get("role") == "assistant":
            self.m += 1
        elif t == "message_update":
            u = ev.get("assistantMessageEvent") or {}
            if u.get("type") in ("text_delta", "thinking_delta"):
                self.b.delta(self.m, u.get("contentIndex", 0), "text" if u["type"] == "text_delta" else "think", u.get("delta", ""))
        elif t == "message_end":
            self.message(ev.get("message") or {}, live=True)
        elif t == "tool_execution_end":
            r = ev.get("result") or {}
            self.b.tool_end(ev.get("toolCallId"), text_of(r.get("content")) if isinstance(r, dict) else str(r), ev.get("isError"))
        elif t == "session_info_changed":
            self.b.name(ev.get("name"))
        elif t == "thinking_level_changed":
            self.send({"type": "get_state"})
        elif t == "auto_retry_start":
            self.b.note(f"Retrying ({ev.get('errorMessage', '')})")
        elif t == "compaction_start":
            self.b.note("Compacting the conversation…")
        elif t == "extension_ui_request":
            method = ev.get("method")
            if method in ("select", "confirm"):
                self.asks[ev["id"]] = method
                title = "\n".join(x for x in (ev.get("title"), ev.get("message")) if x)
                self.b.ask(ev["id"], title, ev.get("options") if method == "select" else ["Yes", "No"], skip=True)
            elif method in ("input", "editor"):  # a text answer: the card's box, or the message box
                rid = ev["id"]
                title = "\n".join(x for x in (ev.get("title"), ev.get("message")) if x)
                self.questions(rid, [{"question": title, "options": []}], lambda answers: self.send(
                    {"type": "extension_ui_response", "id": rid, **({"value": answers[0][0]} if answers else {"cancelled": True})}))
            elif method == "notify":
                self.b.note(ev.get("message", ""), err=ev.get("notifyType") == "error")
        elif t == "extension_error":
            self.b.note(f"Extension error: {ev.get('error')}", err=True)

    def run(self, first: str) -> int:
        m, effort = self.b.prefs.get("model") or {}, self.b.prefs.get("effort")
        if m.get("id"):  # this session's earlier picks
            self.rpc({"type": "set_model", "provider": m.get("provider"), "modelId": m["id"]})
        if effort:
            self.rpc({"type": "set_thinking_level", "level": effort})
        self.send({"type": "get_state"})
        self.send({"type": "get_messages"})
        if first:
            self.send({"type": "prompt", "message": first})
        for ev in jsonl(self.p.stdout):
            self.event(ev)
        return self.p.wait()


OPENCLAW_BRIDGE = Path(__file__).resolve().parent.parent / "deploy" / "agents" / "openclaw-bridge"
MEDIA_LIMIT = 100_000_000  # bytes of one file a reply carries


def without_media_lines(text: str) -> str:
    """An OpenClaw reply's text without its "MEDIA:<path>" lines (outside code blocks): the Gateway delivers those
    files as the message's attachments, which the chat shows."""
    out, code = [], False
    for line in text.split("\n"):
        code ^= line.startswith("```")
        if code or not re.match(r" {0,3}MEDIA:\S", line):
            out.append(line)
    return "\n".join(out).strip("\n") if len(out) < text.count("\n") + 1 else text


class OpenClaw(Adapter):
    """An OpenClaw Gateway's one shared conversation ("main"), through deploy/agents/openclaw-bridge/bridge.mjs: a Node helper
    on OpenClaw's gateway client that sends the Gateway's requests and passes its events on (JSON lines both ways).
    Every device's chat shows the same conversation, live: the user's messages come back as session.message events,
    whoever sent them. The agent's ask_user questions show as question cards (they need the operator.questions scope,
    which bridge.mjs asks the Gateway for once); the files its replies carry are fetched into the chat's media folder."""
    KEY = "main"
    QUESTIONS = "operator.questions"
    RESCOPED = 3  # bridge.mjs's exit once the questions scope is approved: started again, it connects with it

    def __init__(self, *a):
        super().__init__(*a)
        self.m, self.n, self.pending = 0, 0, {}
        self.open = None  # the assistant message being streamed: {"m", "blocks"} (tool cards, then its text)
        self.ended = None  # the turn's last message, shown when its run ended: its final copy can come after that
        self.first, self.hello, self.pairing, self.upgrade = "", False, "", ""
        self.http = ""  # the Gateway's web origin (its hello): managed media download from there
        # live replies whose text had MEDIA: lines, by message id: (m, blocks). The Gateway attaches those files by
        # rewriting the stored message a moment later, which only a sessions.changed event says; chat.history has it.
        self.media_wait = {}
        self.asks = []  # ask_user questions waiting for the one on screen to be answered (Gateway question records)
        self.seen = set()  # user messages shown: the Gateway can send one twice (as it takes it in, then as stored)
        old = OPENCLAW_BRIDGE.parent.parent / "openclaw-bridge"  # its place before deploy/agents/: packages it installed
        try:  # a file held open there (Windows) leaves them: npm installs afresh below
            if (old / "node_modules").is_dir() and not (OPENCLAW_BRIDGE / "node_modules").exists():
                shutil.move(str(old / "node_modules"), str(OPENCLAW_BRIDGE / "node_modules"))
            if old.is_dir() and not any(old.iterdir()):
                old.rmdir()
        except OSError:
            pass
        if not (OPENCLAW_BRIDGE / "node_modules" / "@openclaw" / "gateway-client").exists():
            self.b.note("Installing Deck's OpenClaw link (once)…")
            # Windows: through cmd (resolve() would take npm.cmd's first script, npm-prefix.js); fixed arguments, no quoting
            npm = "npm ci --ignore-scripts --no-audit --no-fund"
            r = subprocess.run(npm if WINDOWS else npm.split(), shell=WINDOWS, cwd=OPENCLAW_BRIDGE, capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f"npm ci in {OPENCLAW_BRIDGE} failed: {(r.stderr or r.stdout).strip()[-500:]}")
        self.argv = [*resolve("node"), str(OPENCLAW_BRIDGE / "bridge.mjs")]
        self.start(self.argv)

    def rpc(self, method: str, params: dict, then=None, fail=None) -> None:
        """A Gateway request: then(payload) runs on success, fail(error) on failure (without one: a note)."""
        self.n += 1
        rid = f"deck-{self.n}"
        self.pending[rid] = (then, fail)
        self.send({"id": rid, "method": method, "params": params})

    def say(self, text: str) -> None:
        self.rpc("chat.send", {"sessionKey": self.KEY, "message": text, "idempotencyKey": f"deck-{time.time_ns()}"})

    def command(self, c: dict) -> None:
        t = c.get("t")
        if t == "say" and str(c.get("text", "")).strip():
            self.say(str(c["text"]))
        elif t == "abort":
            self.rpc("chat.abort", {"sessionKey": self.KEY})

    def session_info(self, info: dict, defaults: dict | None = None) -> None:
        model = (info or {}).get("model") or (defaults or {}).get("model")
        self.b.bar(model, (info or {}).get("thinkingLevel"))
        self.working(bool((info or {}).get("hasActiveRun")))

    def slash_model(self, arg: str) -> None:
        """/model: pick from the Gateway's models; /model <name>: the one matching it. Back on a Claude model, the
        conversation's runtime pin goes too: a turn on GPT pins OpenClaw's own runtime, which has no Claude login."""
        def choose(data):
            models = [x for x in data.get("models") or [] if x.get("available", True)]
            full = lambda x: f"{x.get('provider')}/{x.get('id')}"
            if arg:
                a = arg.lower()
                hit = [x for x in models if a in (str(x.get("id")).lower(), full(x).lower(), str(x.get("alias", "")).lower())] \
                    or [x for x in models if a in f"{full(x)} {x.get('name')}".lower()]
                if not hit:
                    return self.set_model(arg)  # let the Gateway judge it
                if len(hit) == 1:
                    return self.set_model(full(hit[0]))
                models = hit
            self.pick("Which model?", [(f"{x.get('name') or x.get('id')} ({x.get('provider')})", full(x)) for x in models[:16]],
                      self.set_model)
        self.rpc("models.list", {"sessionKey": self.KEY}, choose)

    def set_model(self, ref: str) -> None:
        claude = ref.startswith(("anthropic/", "claude-cli/")) or ref.split("/")[-1].startswith("claude")
        self.rpc("sessions.patch", {"key": self.KEY, "model": ref, **({"agentRuntime": None} if claude else {})},
                 lambda _: self.rpc("chat.history", {"sessionKey": self.KEY, "limit": 1},
                                    lambda h: self.session_info(h.get("sessionInfo") or {}, h.get("defaults"))))

    def slash_compact(self, _arg: str) -> None:
        self.rpc("sessions.compact", {"key": self.KEY}, lambda _: self.b.note("Compacted"))

    def slash_new(self, _arg: str) -> None:
        self.b.note("OpenClaw keeps one shared conversation for every device; /new isn't available here")

    slash_clear = slash_new

    def slash_resume(self, _arg: str) -> None:
        self.b.note("OpenClaw keeps one shared conversation for every device: there is nothing else to resume")

    def blocks(self, content) -> tuple[list[dict], list[tuple]]:
        """Chat blocks from an OpenClaw message's content, and the tool results it carries: (id, output, error)."""
        if isinstance(content, str):
            return [{"kind": "text", "text": content}], []
        blocks, results = [], []
        for c in content or []:
            kind = c.get("type")
            if kind == "text":
                blocks.append({"kind": "text", "text": without_media_lines(c.get("text", ""))})
            elif kind == "thinking":
                blocks.append({"kind": "think", "text": c.get("thinking") or c.get("text", "")})
            elif kind in ("toolcall", "toolCall", "tool_use"):
                blocks.append({"kind": "tool", "id": c.get("id"), "name": str(c.get("name", "")).removeprefix("mcp__openclaw__"),
                               "args": c.get("arguments") or c.get("input") or {}})
            elif kind == "tool_result":
                results.append((c.get("tool_use_id"), text_of(c.get("content")), c.get("is_error") or c.get("isError")))
            elif kind in ("image", "video", "audio", "attachment"):  # a reply's file (MEDIA:, the message tool's media)
                f = (c.get("attachment") or {}) if kind == "attachment" else c
                blocks.append({"kind": "file", "art": str(f.get("artifactId") or ""), "mime": str(f.get("mimeType") or ""),
                               "name": str(f.get("label") or f.get("fileName") or f.get("alt") or kind), "size": f.get("sizeBytes") or 0})
        return blocks, results

    def fetch(self, m, blocks: list[dict]) -> None:
        """A message's files, into the chat's media folder: artifacts.download gives the bytes, or (the Gateway's managed
        media) a short-lived ticket URL on its web origin, fetched off this thread. The page never talks to the Gateway.
        Ones fetched before are there at once; the rest show as each lands."""
        for x in blocks:
            if x["kind"] != "file" or "file" in x or "err" in x:
                continue
            ext = Path(x["name"]).suffix
            if not re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext):
                ext = mimetypes.guess_extension(x["mime"]) or ""
            f = re.sub(r"[^\w-]", "_", x["art"])[:120] + ext
            if not x["art"]:
                x["err"] = "the Gateway gave no artifact for it"
            elif (self.b.media / f).is_file():
                x["file"] = f
            else:
                self.rpc("artifacts.download", {"sessionKey": self.KEY, "artifactId": x["art"]},
                         lambda r, x=x, f=f: self.landed(m, blocks, x, f, r),
                         lambda e, x=x: self.landed(m, blocks, x, "", {"error": e}))

    def landed(self, m, blocks: list[dict], x: dict, f: str, r: dict) -> None:
        if f and r.get("url") and not r.get("data") and self.http:
            url = urllib.parse.urljoin(self.http + "/", r["url"])
            return threading.Thread(target=self.landed, args=(m, blocks, x, f, {"get": url}), daemon=True).start()
        try:
            if r.get("get"):
                with urllib.request.urlopen(r["get"], timeout=60) as res:
                    data = res.read(MEDIA_LIMIT + 1)
                if len(data) > MEDIA_LIMIT:
                    raise ValueError(f"over {MEDIA_LIMIT // 1_000_000} MB")
            elif f and r.get("encoding") == "base64" and r.get("data"):
                data = base64.b64decode(r["data"])
            else:
                raise ValueError(r.get("error") or "the Gateway sent no bytes for it")
            self.b.media.mkdir(exist_ok=True)
            part = self.b.media / f"{f}.part"
            part.write_bytes(data)
            part.replace(self.b.media / f)
            x["file"] = f
        except (OSError, ValueError) as e:  # urllib's errors are OSErrors
            x["err"] = str(e)
        self.b.msg(m, blocks, final=False)

    def copy_of_next(self, msg: dict, nxt: dict) -> bool:
        """A reply with MEDIA: lines is stored twice: as the model wrote it, then with its files attached (same text)."""
        if msg.get("role") != "assistant" or nxt.get("role") != "assistant":
            return False
        a, b = self.blocks(msg.get("content"))[0], self.blocks(nxt.get("content"))[0]
        text = lambda bl: "\n".join(x["text"] for x in bl if x["kind"] == "text").strip()
        return all(x["kind"] == "text" for x in a) and any(x["kind"] == "file" for x in b) and text(a) == text(b)

    def attached(self, h: dict) -> None:
        """Replies in media_wait whose stored copy now carries their files: drawn again with them."""
        for msg in h.get("messages") or []:
            mid = (msg.get("__openclaw") or {}).get("id")
            if mid not in self.media_wait:
                continue
            blocks, _ = self.blocks(msg.get("content"))
            if not any(x["kind"] == "file" for x in blocks):
                continue  # not rewritten yet: the next sessions.changed looks again
            m, old = self.media_wait.pop(mid)
            ids = {x.get("id") for x in blocks if x["kind"] == "tool"}
            blocks = [x for x in old if x["kind"] == "tool" and x.get("id") not in ids] + blocks
            self.fetch(m, blocks)
            self.b.msg(m, blocks)

    def asked(self, q: dict) -> None:
        """An ask_user question record (question.requested, or open at connect: question.list): its questions go to the
        chat as cards in turn; a record asked while another is on screen waits for it."""
        if q.get("status", "pending") != "pending" or not q.get("id") or (q.get("sessionKey") or "").split(":")[-1] != self.KEY:
            return
        if q["id"] in [x["id"] for x in self.asks] or (self.quiz and self.quiz["key"] == q["id"]):
            return
        if any(x.get("isSecret") or x.get("secretStore") for x in q.get("questions") or []):
            self.b.note("OpenClaw asks for a secret: answer it in OpenClaw's Control UI or TUI (Deck doesn't take secrets)", err=True)
            return
        self.asks.append(q)
        self.next_ask()

    def next_ask(self) -> None:
        if self.quiz or not self.asks:
            return
        q = self.asks.pop(0)
        def card(x):  # options' descriptions go under the question, the options themselves are its buttons
            more = "".join(f"\n- **{o.get('label')}**: {o['description']}" for o in x.get("options") or [] if o.get("description"))
            return {"question": f"{x.get('question', '')}{more}", "options": [str(o.get("label", "")) for o in x.get("options") or []],
                    "multi": bool(x.get("multiSelect")), "other": bool(x.get("isOther") or not x.get("options"))}  # ask_user's take text
        self.questions(q["id"], [card(x) for x in q.get("questions") or []], lambda answers: self.answer(q, answers))

    def answer(self, q: dict, answers) -> None:
        """question.resolve: each question's picks as its options' values (a typed answer as typed); Skip cancels. Refused
        (an answer it doesn't take): said, and asked again while it's still open."""
        def refused(err):
            self.b.note(f"OpenClaw didn't take that answer: {err}", err=True)
            self.rpc("question.get", {"id": q["id"]}, lambda r: self.asked(r.get("question") or {}), lambda _: None)
        if answers is None:
            self.rpc("question.resolve", {"id": q["id"], "cancel": True}, fail=refused)
        else:
            got = {}
            for x, picked in zip(q.get("questions") or [], answers):
                value = {str(o.get("label", "")): str(o.get("value") or o.get("label", "")) for o in x.get("options") or []}
                got[x.get("questionId")] = [value.get(p, p) for p in picked]
            self.rpc("question.resolve", {"id": q["id"], "answers": {"answers": got}}, fail=refused)
        self.next_ask()

    def unasked(self, qid: str, status: str) -> None:
        """question.resolved: answered (here or on another device), cancelled (the run stopped) or expired."""
        self.asks = [x for x in self.asks if x["id"] != qid]
        if self.quiz and self.quiz["key"] == qid:
            self.quiz_drop(qid, {"cancelled": "Cancelled", "expired": "Expired"}.get(status, ""))
            self.b.state("working" if self.busy else "your_turn")
        self.next_ask()

    def message(self, msg: dict, live: bool) -> None:
        role = msg.get("role")
        key = str(msg.get("idempotencyKey") or (msg.get("__openclaw") or {}).get("idempotencyKey") or "").removesuffix(":user")
        if role == "user" and key and key in self.seen:
            return
        self.seen.add(key)
        if role == "user":  # a CLI runtime (claude-cli) stores its tool results as user messages: no bubble for those
            text = msg["content"] if isinstance(msg.get("content"), str) else text_of(msg.get("content"))
            for r in self.blocks(msg.get("content"))[1]:
                self.b.tool_end(*r)
            if text.strip():
                self.b.user(text)
        elif role == "assistant":
            blocks, results = self.blocks(msg.get("content"))
            streamed = self.open or (self.ended if live else None)
            if live and streamed:  # the streamed one: its tool cards stay, its text is now final
                m = streamed["m"]
                ids = {x.get("id") for x in blocks if x["kind"] == "tool"}
                blocks = [x for x in streamed["blocks"] if x["kind"] == "tool" and x.get("id") not in ids] + blocks
                self.open = self.ended = None
            else:
                self.m += 1
                m = self.m
            self.fetch(m, blocks)
            self.b.msg(m, blocks)
            said = [c.get("text", "") for c in msg.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
            mid = (msg.get("__openclaw") or {}).get("id")
            if live and mid and any(without_media_lines(t) != t for t in said):
                self.media_wait[mid] = (m, blocks)
            for r in results:
                self.b.tool_end(*r)
            if msg.get("stopReason") == "error" and msg.get("errorMessage"):
                self.b.note(msg["errorMessage"], err=True)
        elif role in ("toolResult", "tool"):
            self.b.tool_end(msg.get("toolCallId"), text_of(msg.get("content")), msg.get("isError"))
        elif role == "custom" and not live:
            self.b.note(text_of(msg.get("content")) if not isinstance(msg.get("content"), str) else msg["content"])

    def streaming(self) -> dict:
        if not self.open:
            self.m += 1
            self.open = {"m": self.m, "blocks": []}
        return self.open

    def event(self, name: str, p: dict) -> None:
        if (p.get("sessionKey") or "").split(":")[-1] not in ("", self.KEY):
            return  # another conversation's (a subagent's)
        if name == "session.message":
            self.message(p.get("message") or {}, live=True)
        elif name == "sessions.changed" and p.get("phase") == "message" and self.media_wait:
            self.rpc("chat.history", {"sessionKey": self.KEY, "limit": 20}, self.attached)
        elif name == "question.requested":
            self.asked(p)
        elif name == "question.resolved":
            self.unasked(str(p.get("id") or ""), str(p.get("status") or ""))
        elif name == "chat":
            state = p.get("state")
            if state == "delta":
                o = self.streaming()
                if p.get("replace") or p.get("message"):  # a whole snapshot: its text replaces what streamed so far
                    text = p.get("deltaText", "") if p.get("replace") else text_of((p.get("message") or {}).get("content"))
                    o["blocks"] = [x for x in o["blocks"] if x["kind"] == "tool"] + [{"kind": "text", "text": text}]
                    self.b.msg(o["m"], o["blocks"], final=False)
                else:
                    if not o["blocks"] or o["blocks"][-1]["kind"] != "text":
                        o["blocks"].append({"kind": "text", "text": ""})
                    o["blocks"][-1]["text"] += p.get("deltaText", "")
                    self.b.delta(o["m"], len(o["blocks"]) - 1, "text", p.get("deltaText", ""))
            elif state == "error":
                self.b.note(p.get("errorMessage") or "OpenClaw error", err=True)
                self.working(False)
            elif state == "aborted":
                self.b.note("Stopped")
                self.working(False)
        elif name == "agent":
            stream, d = p.get("stream"), p.get("data") or {}
            if stream == "lifecycle" and d.get("phase") == "start":
                self.ended = None
                self.working(True)
            elif stream == "lifecycle" and d.get("phase") in ("end", "error"):
                if self.open:  # streamed text no final message replaced (yet: it may still come, see message())
                    for x in self.open["blocks"]:
                        if x["kind"] == "text":
                            x["text"] = without_media_lines(x["text"])
                    self.b.msg(self.open["m"], self.open["blocks"])
                    self.open, self.ended = None, self.open
                self.working(False)
            elif stream == "tool" and d.get("phase") == "start":
                o = self.streaming()
                o["blocks"].append({"kind": "tool", "id": d.get("toolCallId"), "name": str(d.get("name", "")).removeprefix("mcp__openclaw__"),
                                    "args": d.get("args") or {}})
                self.b.msg(o["m"], o["blocks"], final=False)
            elif stream == "tool" and d.get("phase") == "result":
                self.b.tool_end(d.get("toolCallId"), text_of(d.get("result")) if not isinstance(d.get("result"), str) else d["result"],
                                d.get("isError"))

    def on_hello(self, hello: dict) -> None:
        again, self.hello, self.http = self.hello, True, str(hello.get("http") or "")
        self.rpc("sessions.messages.subscribe", {"key": self.KEY})
        def questions():  # asked before this connection (or while it was down): after the history, so they come last
            if self.QUESTIONS in (hello.get("scopes") or []):
                self.rpc("question.list", {}, lambda r: [self.asked(q) for q in r.get("questions") or []])
        if again:  # back after a drop: what was said meanwhile shows on the next open
            return questions()
        def history(h):
            msgs = h.get("messages") or []
            for i, msg in enumerate(msgs):
                if not self.copy_of_next(msg, msgs[i + 1] if i + 1 < len(msgs) else {}):
                    self.message(msg, live=False)
            self.session_info(h.get("sessionInfo") or {}, h.get("defaults"))
            if not (h.get("sessionInfo") or {}).get("hasActiveRun"):
                self.b.state("your_turn")
            questions()
            if self.first:
                self.say(self.first)
        self.rpc("chat.history", {"sessionKey": self.KEY, "limit": 100}, history)

    def events(self):
        """bridge.mjs's output, across its restart for the questions scope (its next hello is a reconnect's)."""
        while True:
            yield from jsonl(self.p.stdout)
            if self.p.wait() != self.RESCOPED:
                return
            self.start(self.argv)

    def run(self, first: str) -> int:
        self.first = first
        for ev in self.events():
            t = ev.get("t")
            if t == "res":
                then, fail = self.pending.pop(ev.get("id"), (None, None))
                if not ev.get("ok") and fail:
                    fail(str(ev.get("error") or "OpenClaw request failed"))
                elif not ev.get("ok"):
                    self.b.note(str(ev.get("error") or "OpenClaw request failed"), err=True)
                elif then:
                    then(ev.get("payload") or {})
            elif t == "event":
                self.event(str(ev.get("event")), ev.get("payload") or {})
            elif t == "hello":
                self.on_hello(ev)
            elif t == "upgrade" and ev.get("requestId") != self.upgrade:
                self.upgrade = ev.get("requestId") or ""
                self.b.note("For OpenClaw's questions (ask_user) to show here, approve Deck's request on the Gateway host: "
                            f"`openclaw devices approve {self.upgrade}` (once; the chat works meanwhile)")
            elif t == "pairing" and ev.get("requestId") != self.pairing:  # the client retries: say it once
                self.pairing = ev.get("requestId") or ""
                self.b.note("This device waits for approval on the OpenClaw Gateway host: "
                            f"`openclaw devices approve {ev.get('requestId') or '<request id>'}` there (it connects by itself then)", err=True)
                self.b.state("needs_you", "OpenClaw: approve this device")
            elif t == "error":
                self.b.note(str(ev.get("message")), err=True)
        return self.p.wait()


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


class Claude(Adapter):
    """Claude as a person runs it (in a Term), so it has every tool. Its transcript
    (~/.claude/projects/<folder>/<session id>.jsonl) becomes the chat; hooks given with --settings (`chat_bridge.py
    hook` appends their input to <session>.hooks.jsonl) tell the bridge the session and when each turn starts and ends;
    the page's messages are typed into its terminal. Nothing streams: text shows a block at a time."""
    YOLO = "--dangerously-skip-permissions"
    HOOKS = ("SessionStart", "UserPromptSubmit", "Stop", "StopFailure", "Notification", "PreToolUse")
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
        self.goal = ""  # the /goal in force: set_goal
        self.saved_model = None  # ~/.claude/settings.json's model before this session's /model: see slash_model
        self.ysk = {"mtime": 0, "card": None, "ask": None, "later": None}  # "You should know" card: see heads_up()
        self.usage = []  # the bar's context, cost and limit chips, from its status line (statusline_main)
        self.quiet = time.time()  # when it last went idle: transcript records older than this don't start a turn
        self.retry_tail, self.retry_reported = "", False
        self.prompt_lines, self.cancelled_at = 1, None
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
        hook = f'"{Path(py).as_posix()}" "{Path(__file__).resolve().as_posix()}" hook ' \
               f'"{b.out_path.parent.as_posix()}" "{session}"'
        hooks = {e: [{**({"matcher": "AskUserQuestion"} if e == "PreToolUse" else {}),
                      "hooks": [{"type": "command", "command": hook}]}] for e in self.HOOKS}
        line = {"type": "command", "command": hook.replace(" hook ", " statusline ", 1)}
        own = b.prev_resume if resume else ""
        if own and not (self.projects() / f"{own}.jsonl").exists():  # nothing was said in it, or it's gone: a new one
            own, self.resume = "", False
        conv = ["--resume", own] if own else ["--continue"] if self.resume else []
        self.argv = [*resolve("claude"), "--settings", json.dumps({"hooks": hooks, "statusLine": line}), *conv,
                     *([self.YOLO] if self.yolo else []), *self.args,
                     *(["--model", pr["model"]] if pr.get("model") else []),
                     *(["--effort", self.effort] if self.effort else []),
                     *(["--permission-mode", pr["mode"]] if pr.get("mode") else [])]

    # ---- its terminal ----
    def write(self, s: str) -> None:
        with self.wlock:
            self.p.write(s)

    def type(self, text: str) -> None:
        """Pasted (bracketed: newlines stay in the message), then Enter on its own. A slash command is typed a key at a
        time: pasted, or sent in one burst (which Claude also takes for a paste), it would be a message."""
        if self.cancelled_at is not None:
            # A cancelled retry restores its prompt into Claude's composer. The next Deck message replaces it.
            time.sleep(max(0, 0.3 - (time.monotonic() - self.cancelled_at)))
            for _ in range(2 * self.prompt_lines + 2):
                self.write("\x15")
                time.sleep(0.02)
            self.cancelled_at = None
        self.prompt_lines = text.count("\n") + 1
        if re.match(r"/[\w:-]+( [^\n]*)?$", text):
            for ch in text:
                self.write(ch)
                time.sleep(0.03)
        else:
            self.write("\x1b[200~" + text + "\x1b[201~")
        time.sleep(0.3)
        self.write("\r")

    def pump(self) -> None:
        """Its output: drained (it blocks when nobody reads), ConPTY's start-up query answered, and the end of the
        screen kept to spot the folder trust question (asked before anything reaches the transcript), which it accepts."""
        asked = WINDOWS
        while data := self.p.read():
            if asked and "\x1b[c" in data:
                asked = False
                self.write("\x1b[?1;2c")
            self.screen = (self.screen + ANSI.sub(" ", data))[-3000:]  # words are placed by cursor moves, not spaces
            self.retry_notice(data)
            if not self.trust_asked and re.search(r"trust\s+this\s+folder", self.screen):
                self.trust_asked = True  # the user starts these sessions in their own folders: trust, don't ask
                time.sleep(0.3)
                self.write("\x1b[B\r")
                self.b.note(f"Trusted this folder: {self.cwd}")

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
    QUEUE_KEYS = True

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
        Deck's default is Settings → Default model, so the host's own is put back once Claude reports the save."""
        if arg:
            self.b.pref(model=arg)
            self.saved_model = (self.settings().get("model"),)  # before the pick: (None,) when the file had none
            self.type(f"/model {arg}")
        else:
            self.pick("Which model?", [(m, m) for m in ("default", "fable", "opus", "sonnet", "haiku")], self.slash_model)

    def restore_default_model(self) -> None:
        """Undo Claude's "saved as your default for new sessions": the settings file's model as it was before /model."""
        prev = getattr(self, "saved_model", None)
        if prev is None:
            return
        self.saved_model = None
        path = Path.home() / ".claude" / "settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("model") == prev[0]:
            return
        if prev[0] is None:
            data.pop("model", None)
        else:
            data["model"] = prev[0]
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
        elif e in ("Stop", "StopFailure"):  # StopFailure: the turn ended on an API error
            if e == "StopFailure":
                self.b.note(str(h.get("error_details") or h.get("error") or "The model request failed. Try again or choose another model."), err=True)
            self.working(False)
        elif e == "PreToolUse" and h.get("tool_name") == "AskUserQuestion" and h.get("tool_use_id"):
            # Hooks are read before the transcript. Show its preceding explanation before the question card;
            # the tool call itself may not reach the transcript until the question is answered.
            self.read_transcript()
            self.open_q[h["tool_use_id"]] = h.get("tool_input") or {}
            self.ask_open()
        elif e == "Notification" and h.get("notification_type") == "permission_prompt" and not self.open_q:
            aid = f"perm-{time.monotonic_ns()}"
            self.asks[aid] = {"Allow": "1", "Deny": "\x1b"}
            self.b.ask(aid, str(h["message"]), ["Allow", "Deny"])

    def record(self, ev: dict) -> None:
        """One transcript record."""
        t = ev.get("type")
        if ev.get("isSidechain"):
            return  # subagents' own steps: their result shows as the Task tool's output
        if ev.get("isMeta") or ev.get("isCompactSummary"):  # injected for the agent, not typed: debug mode only
            content = (ev.get("message") or {}).get("content")
            texts = [content] if isinstance(content, str) else [c.get("text", "") for c in content or [] if c.get("type") == "text"]
            for x in texts:
                self.b.hidden("compaction summary" if ev.get("isCompactSummary") else "added for the agent", x)
                if (g := GOAL_SET.search(x)) and not ev.get("isCompactSummary"):  # /goal: shown as typed (no longer
                    self.b.user(f"/goal {g[1]}")  # "Sending…"), and the bar's Goal chip till it is met or cleared
                    self.set_goal(g[1])
            return
        if t == "assistant":
            msg = ev.get("message") or {}
            if ev.get("isApiErrorMessage"):
                self.b.note(text_of(msg.get("content")) or str(ev.get("error") or "The model request failed"), err=True)
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
                    self.b.tool_end(tid, text_of(c.get("content")), c.get("is_error"))
                    self.open_q.pop(tid, None)
                    self.qstep.pop(tid, None)
                    for aid in [a for a, x in self.asks.items() if isinstance(x, dict) and x.get("q") == tid]:
                        del self.asks[aid]  # answered in the terminal, not with its buttons
                        self.b.answered(aid)
            texts = [content] if isinstance(content, str) else [c.get("text", "") for c in content or [] if c.get("type") == "text"]
            for x in map(unpaste, texts):  # wrappers (system reminders, slash commands, hook output) the chat leaves out
                if x.strip().startswith("<"):
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
            text = unpaste(str(text_of(ev["attachment"].get("prompt")) or "")).strip()
            if text and not text.startswith("<"):
                self.b.user(text)
        elif t == "system" and ev.get("subtype") == "local_command":
            m = re.match(r"<local-command-(stdout|stderr)>(.*)</local-command-\1>$", str(ev.get("content", "")), re.S)
            if m and m.group(2).strip():  # a slash command's reply: "Set model to ..."
                reply = m.group(2).strip()
                if reply.startswith(("Goal cleared", "No goal set")):
                    self.set_goal("")
                if "saved as your default" in reply:  # /model: this session's pick, not the host's default
                    self.restore_default_model()
                    reply = reply.replace(" and saved as your default for new sessions", " for this session")
                self.b.note(reply, err=m.group(1) == "stderr")
        elif t == "system" and ev.get("subtype") == "compact_boundary":
            self.b.note("Conversation compacted")
        elif t == "permission-mode" and ev.get("permissionMode"):
            self.mode = ev["permissionMode"]
        elif t in ("custom-title", "ai-title"):
            self.b.name(ev.get("customTitle") or ev.get("aiTitle") or "")

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
        self.redraw(self.b.prefs.get("model") or self.settings().get("model") or "Claude",
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


def hook_main(folder: str, session: str) -> int:
    """`chat_bridge.py hook <chat dir> <session>`: Claude's hooks (Claude.HOOKS) pass their input on stdin; it goes to
    <session>.hooks.jsonl for the bridge. Prints nothing: a SessionStart hook's output joins Claude's context."""
    try:
        line = json.dumps(json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace")), ensure_ascii=False)
    except ValueError:
        return 0
    with open(Path(folder) / f"{session}.hooks.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return 0


def statusline_main(folder: str, session: str) -> int:
    """`chat_bridge.py statusline <chat dir> <session>`: Claude's status line (given with --settings). Its context
    and plan limits (no cost: user) go to <session>.hooks.jsonl as a "DeckStatus" record when they change: the chat's bar shows
    them, as the terminal's status line does. Prints the same, for anyone looking at the terminal."""
    try:
        d = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except ValueError:
        return 0
    sys.stdout.reconfigure(encoding="utf-8")  # Windows pipes default to cp1252
    k = lambda n: f"{n / 1e6:g}M" if n >= 1e6 else f"{round(n / 1e3)}k"
    cw, parts = d.get("context_window") or {}, []
    if cw.get("used_percentage") is not None:
        size, used = cw.get("context_window_size"), cw.get("total_input_tokens")
        parts.append(f"ctx {cw['used_percentage']}%" + (f" {k(used)}/{k(size)}" if used is not None and size else ""))
    for key, name in (("five_hour", "5h"), ("seven_day", "wk")):
        pct = ((d.get("rate_limits") or {}).get(key) or {}).get("used_percentage")
        if pct is not None:
            parts.append(f"{name} {round(pct)}%")
    print(" · ".join(parts))
    last = Path(folder) / f"{session}.statusline"
    line = json.dumps({"hook_event_name": "DeckStatus", "parts": parts})
    try:
        if last.read_text(encoding="utf-8") == line:
            return 0
    except OSError:
        pass
    last.write_text(line, encoding="utf-8")
    with open(Path(folder) / f"{session}.hooks.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return 0


def shell_command(cmd: str) -> str:
    """Codex runs commands through a shell; show what ran inside it. Its command line is POSIX-quoted, single and double
    quotes mixed ('a '"'b'"' c' for a 'b' c), so it is split as a shell would."""
    try:
        parts = shlex.split(cmd)
    except ValueError:
        parts = []
    if len(parts) >= 2 and parts[-2] in ("-Command", "-lc", "-c"):
        return parts[-1]
    m = re.search(r"""-(?:Command|lc|c) (['"])(.*)\1\s*$""", cmd, re.S)
    return m.group(2) if m else cmd


class Codex(Adapter):
    """codex app-server: JSON-RPC over stdio. A thread is the conversation, a turn one request; items (messages,
    commands, file changes, tool calls) start, stream and complete; approvals are requests from the server."""
    YOLO = "--dangerously-bypass-approvals-and-sandbox"

    def __init__(self, *a):
        super().__init__(*a)
        self.n, self.pending, self.thread, self.turn, self.queue, self.asks = 0, {}, None, None, [], {}
        self.parts = ["", "", ""]  # the bar: model, effort, mode
        self.goal = ""  # the bar's Goal chip (set_goal)
        self.usage, self.limits, self.outputs = {}, {}, {}
        self.model, self.effort = self.b.prefs.get("model"), self.b.prefs.get("effort")  # /model, /effort: sent with each turn
        self.nlock = threading.Lock()  # requests go from both threads: run()'s and the commands'
        self.fresh = False  # already fell back to a new thread
        self.instructions = ""
        if "--deck-context" in self.args:
            i = self.args.index("--deck-context")
            self.instructions = Path(self.args[i + 1]).read_text(encoding="utf-8")
            del self.args[i:i + 2]
        self.start([*resolve("codex"), "app-server", *self.args])

    def request(self, method: str, params: dict, then=None) -> None:
        with self.nlock:
            self.n += 1
            n = self.n
            self.pending[n] = then
        self.send({"jsonrpc": "2.0", "id": n, "method": method, "params": params})

    def policy(self) -> dict:
        policy = {"approvalPolicy": "never", "sandbox": "danger-full-access"} if self.yolo else \
            {"approvalPolicy": "on-request", "sandbox": "workspace-write"}
        return {**policy, **({"developerInstructions": self.instructions} if self.instructions else {})}

    def bar(self) -> None:
        parts = self.parts[:2]
        window = self.usage.get("modelContextWindow")
        used = (self.usage.get("last") or {}).get("totalTokens")
        if window and used is not None:
            parts.append(f"Context {round(100 * used / window)}% used")
        for key in ("primary", "secondary"):
            limit = self.limits.get(key) or {}
            if limit.get("usedPercent") is None:
                continue
            minutes = limit.get("windowDurationMins")
            label = f"{minutes // 1440}d" if minutes and minutes % 1440 == 0 else \
                f"{minutes // 60}h" if minutes and minutes % 60 == 0 else f"{minutes}m" if minutes else key
            parts.append(f"{label} {limit['usedPercent']}% used")
        self.b.bar(*parts, *([self.goal] if self.goal else []), self.parts[2])

    def rate_limits(self, res: dict) -> None:
        # Rolling updates are sparse: unavailable metadata doesn't erase the previous snapshot.
        self.limits.update({k: v for k, v in (res.get("rateLimits") or {}).items() if v is not None})
        self.bar()

    def command(self, c: dict) -> None:
        t = c.get("t")
        if t == "rename" and self.thread:  # the tab renamed: so is the thread, as /resume and Codex's own lists show it
            self.request("thread/name/set", {"threadId": self.thread, "name": str(c.get("title", ""))})
            return
        if t == "say" and str(c.get("text", "")).strip():
            if not self.thread:
                self.queue.append(str(c["text"]))
                return
            inp = [{"type": "text", "text": str(c["text"])}]
            if self.turn:  # typed while working: steers the running turn
                self.request("turn/steer", {"threadId": self.thread, "expectedTurnId": self.turn, "input": inp})
            else:
                self.request("turn/start", {"threadId": self.thread, "input": inp, **({"model": self.model} if self.model else {}),
                                            **({"effort": self.effort} if self.effort else {})})
                self.working(True)
        elif t == "abort" and self.turn:
            self.request("turn/interrupt", {"threadId": self.thread, "turnId": self.turn})
        elif t == "answer" and c.get("id") in self.asks:
            rid, v = self.asks.pop(c["id"]), c.get("value")
            if isinstance(rid, tuple):  # extra permissions: granted for this turn or the session, or none
                rid, asked = rid
                grant = {"permissions": asked, "scope": "session" if v == "Allow for session" else "turn"}
                self.send({"jsonrpc": "2.0", "id": rid, "result": grant if v in ("Allow", "Allow for session") else {"permissions": {}}})
            else:
                decision = {"Allow": "accept", "Allow for session": "acceptForSession"}.get(v, "decline")
                self.send({"jsonrpc": "2.0", "id": rid, "result": {"decision": decision}})
            self.b.answered(c["id"])
            self.b.state("working")

    def item(self, it: dict, done: bool) -> None:
        kind, iid = it.get("type"), it.get("id")
        if kind == "userMessage":
            if done:
                self.b.user(text_of(it.get("content")))
        elif kind in ("agentMessage", "plan"):
            if done:
                self.b.msg(iid, [{"kind": "text", "text": it.get("text", "")}])
        elif kind == "reasoning":
            if done:
                self.b.msg(iid, [{"kind": "think", "text": "\n\n".join(it.get("summary") or it.get("content") or [])}])
        elif kind == "contextCompaction":
            self.b.note("Compacted the conversation" if done else "Compacting the conversation…")
        elif kind in ("commandExecution", "fileChange", "mcpToolCall", "webSearch", "dynamicToolCall",
                      "collabAgentToolCall", "collabToolCall", "imageView"):
            if kind == "commandExecution":
                name, args = "shell", {"command": shell_command(it.get("command", ""))}
            elif kind == "fileChange":
                name, args = "edit", {"path": ", ".join(ch.get("path", "") for ch in it.get("changes") or [])}
            elif kind == "webSearch":
                name, args = "web_search", it.get("action") or {"query": it.get("query", "")}
            elif kind in ("collabAgentToolCall", "collabToolCall"):
                name = it.get("tool") or "agent"
                args = {k: it[k] for k in ("prompt", "receiverThreadIds", "receiverThreadId", "model", "reasoningEffort") if it.get(k)}
            elif kind == "imageView":
                name, args = "view_image", {"path": it.get("path", "")}
            else:
                name, args = ".".join(x for x in (it.get("server") or it.get("namespace"), it.get("tool")) if x), it.get("arguments") or {}
            self.b.msg(iid, [{"kind": "tool", "id": iid, "name": name, "args": args}], final=done)
            if kind == "commandExecution" and not done and it.get("aggregatedOutput"):
                # A fast first chunk can be bundled into item/started instead of a separate delta.
                out = it["aggregatedOutput"]
                if len(out) > OUT_LIMIT:
                    out = out[:OUT_LIMIT // 2] + "\n…\n" + out[-OUT_LIMIT // 2:]
                self.outputs[iid] = out
                self.b.tool_end(iid, out)
            if done:
                if kind == "commandExecution":
                    self.b.tool_end(iid, it.get("aggregatedOutput") if it.get("aggregatedOutput") is not None else self.outputs.get(iid, ""),
                                    it.get("exitCode") not in (0, None) or it.get("status") == "failed")
                    self.outputs.pop(iid, None)
                elif kind == "fileChange":
                    self.b.tool_end(iid, "\n".join(ch.get("diff", "") for ch in it.get("changes") or []), it.get("status") == "failed")
                elif kind == "mcpToolCall":
                    self.b.tool_end(iid, it.get("error") or it.get("result") or "", bool(it.get("error")))
                elif kind == "dynamicToolCall":
                    self.b.tool_end(iid, it.get("contentItems") or "", it.get("success") is False)
                elif kind in ("collabAgentToolCall", "collabToolCall"):
                    self.b.tool_end(iid, it.get("agentsStates") or it.get("agentStatus") or it.get("status") or "", it.get("status") == "failed")
                else:
                    self.b.tool_end(iid, "")

    def thread_ready(self, res: dict) -> None:
        th = res.get("thread") or {}
        self.thread = th.get("id")
        if self.thread:
            self.b.save(resume=self.thread)
        self.parts = [self.model or res.get("model"), self.effort or res.get("reasoningEffort"),
                      "yolo" if self.yolo else res.get("sandbox", {}).get("type") if isinstance(res.get("sandbox"), dict) else res.get("sandbox")]
        self.bar()
        if not self.parts[1]:  # the model's own default: Codex leaves it out, so the bar showed no effort
            def default(res):
                m = next((m for m in (res or {}).get("data") or [] if self.parts[0] in (m.get("model"), m.get("id"))), None)
                if m and m.get("defaultReasoningEffort") and not self.parts[1]:
                    self.parts[1] = m["defaultReasoningEffort"]
                    self.bar()
            self.request("model/list", {}, default)
        self.b.name(th.get("name"))
        for turn in th.get("turns") or []:
            for it in turn.get("items") or []:
                self.item(it, done=True)
        self.b.state("your_turn")
        for text in self.queue:
            self.command({"t": "say", "text": text})
        self.queue = []

    def started(self, _res=None) -> None:
        self.send({"jsonrpc": "2.0", "method": "initialized"})
        self.request("account/rateLimits/read", {}, self.rate_limits)
        if self.resume and self.b.prev_resume:  # this session's own thread (by its file, if it moved here)
            path = self.b.moved.get("path") if self.b.moved.get("id") == self.b.prev_resume else ""
            self.request("thread/resume", {"threadId": self.b.prev_resume, **({"path": path} if path else {}),
                                           "cwd": self.cwd, **self.policy()}, self.thread_ready)
        elif self.resume:  # no saved thread: the folder's newest
            self.request("thread/list", {"cwd": self.cwd, "limit": 1, "sortKey": "updated_at"}, self.resume_newest)
        else:
            self.request("thread/start", {"cwd": self.cwd, **self.policy()}, self.thread_ready)

    def resume_newest(self, res: dict) -> None:
        data = (res or {}).get("data") or []
        if not data:  # nothing to continue in this folder: a new conversation
            self.request("thread/start", {"cwd": self.cwd, **self.policy()}, self.thread_ready)
            return
        self.request("thread/resume", {"threadId": data[0]["id"], "cwd": self.cwd, **self.policy()}, self.thread_ready)

    @staticmethod
    def threads(res: dict) -> list[tuple[str, str]]:
        """A thread/list reply as conversations: (title, thread id), subagents' threads left out."""
        return [(th.get("name") or th.get("preview") or "", th["id"]) for th in (res or {}).get("data") or []
                if th.get("id") and not th.get("parentThreadId")]

    def slash_resume(self, arg: str) -> None:
        # the base's conversations() is synchronous, but Codex answers on run()'s thread: pick when the list arrives
        def then(res):
            convs = self.threads(res)
            if arg:
                convs = [c for c in convs if arg.lower() in c[0].lower() or arg == c[1]]
            self.pick("Resume which conversation?", convs[:12], self.switch)
        self.request("thread/list", {"cwd": self.cwd, "limit": 30, "sortKey": "updated_at"}, then)

    def slash_compact(self, _arg: str) -> None:
        if self.thread:
            self.request("thread/compact/start", {"threadId": self.thread})

    def slash_goal(self, arg: str) -> None:
        """/goal <objective>: Codex keeps working till it is met; /goal clear, pause, resume; /goal alone: the goal now.
        A typed /goal would only reach the model as text: the app server takes it as a thread request."""
        if not self.thread:
            return self.b.note("The session must start before you can set a goal.")
        word = arg.lower()
        if word in ("clear", "stop"):
            return self.request("thread/goal/clear", {"threadId": self.thread, "origin": "user"})
        if not arg:
            return self.request("thread/goal/get", {"threadId": self.thread},
                                lambda res: self.b.note(self.goal_text(res.get("goal")) or "No goal is currently set."))
        st = {"pause": "paused", "resume": "active"}.get(word)
        self.request("thread/goal/set", {"threadId": self.thread, "origin": "user",
                                         **({"status": st} if st else {"objective": arg, "status": "active"})},
                     lambda res: self.set_goal(res.get("goal")))
        if not st or st == "active":
            self.working(True)  # it starts on the goal at once

    @staticmethod
    def goal_text(g) -> str:
        return f"Goal ({g.get('status')}): {g.get('objective')}" if g else ""

    def set_goal(self, g) -> None:
        """The bar's Goal chip: the objective while active, its status otherwise; gone once complete or cleared."""
        if g and g.get("status") == "complete" and self.goal:
            self.b.note("Goal met")
        if not g or g.get("status") == "complete":
            self.goal = ""
        else:
            o = str(g.get("objective") or "")
            self.goal = f"Goal{'' if g.get('status') == 'active' else ' ' + str(g.get('status'))}: {o[:60]}{'…' if len(o) > 60 else ''}"
        self.bar()

    def set_model(self, model: str) -> None:
        self.model = self.parts[0] = model  # Codex takes it with the next turn
        self.b.pref(model=model)
        self.bar()
        self.b.note(f"Model: {model} (from the next message)")
        self.b.state("your_turn")

    def set_effort(self, effort: str) -> None:
        self.effort = self.parts[1] = effort
        self.b.pref(effort=effort)
        self.bar()
        self.b.note(f"Effort: {effort} (from the next message)")
        self.b.state("your_turn")

    def slash_effort(self, arg: str) -> None:
        """/effort [level]: the levels the current model supports (model/list)."""
        def then(res):
            models = (res or {}).get("data") or []
            cur = next((m for m in models if self.parts[0] in (m.get("model"), m.get("id"))), None) or \
                next((m for m in models if m.get("isDefault")), {})
            levels = [o.get("reasoningEffort") for o in cur.get("supportedReasoningEfforts") or [] if o.get("reasoningEffort")]
            if arg and (arg in levels or not levels):
                self.set_effort(arg)
            else:
                if arg:
                    self.b.note(f"{cur.get('displayName') or self.parts[0]} takes: {', '.join(levels)}", err=True)
                self.pick("Effort?", [(x, x) for x in levels], self.set_effort)
        self.request("model/list", {}, then)

    def slash_model(self, arg: str) -> None:
        def then(res):
            models = [(m.get("displayName") or m.get("model"), m.get("model") or m.get("id"))
                      for m in (res or {}).get("data") or [] if not m.get("hidden")]
            hits = [m for m in models if arg.lower() in m[0].lower() or arg.lower() in m[1].lower()]
            exact = [m for m in hits if arg.lower() in (m[0].lower(), m[1].lower())]
            if arg and len(exact or hits) == 1:
                self.set_model((exact or hits)[0][1])
            else:
                self.pick("Which model?", hits or models, self.set_model)
        self.request("model/list", {}, then)

    def event(self, ev: dict) -> None:
        method, params = ev.get("method"), ev.get("params") or {}
        if method and "id" in ev:  # a request from the server
            if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                key = f"codex-{ev['id']}"
                self.asks[key] = ev["id"]
                what = shell_command(params.get("command") or "") if "command" in method else "Apply file changes?"
                title = "\n".join(x for x in ("Allow?" if "command" in method else "", what, params.get("reason")) if x)
                self.b.ask(key, title, ["Allow", "Allow for session", "Deny"])
            elif method == "item/permissions/requestApproval":
                key = f"codex-{ev['id']}"
                self.asks[key] = (ev["id"], params.get("permissions") or {})
                perms = params.get("permissions") or {}
                what = ", ".join(x for x in (
                    "network access" if (perms.get("network") or {}).get("enabled") else "",
                    "files: " + ", ".join(str(e.get("path") or e) for e in (perms.get("fileSystem") or {}).get("entries") or []) if (perms.get("fileSystem") or {}).get("entries") else "") if x)
                self.b.ask(key, "\n".join(x for x in ("Allow more access?", what, params.get("reason")) if x), ["Allow", "Allow for session", "Deny"])
            elif method == "item/tool/requestUserInput":  # its request_user_input tool: questions, answered by id
                qs, rid = params.get("questions") or [], ev["id"]
                ids = [q.get("id") for q in qs]
                self.questions(f"codex-{rid}", [{"question": q.get("question") or q.get("header", ""),
                                                 "options": [o.get("label", "") for o in q.get("options") or []],
                                                 "other": bool(q.get("isOther")) or not q.get("options")} for q in qs],
                               lambda answers: self.send({"jsonrpc": "2.0", "id": rid, "result": {"answers": {
                                   i: {"answers": a} for i, a in zip(ids, answers or [])}}}))
            else:
                self.send({"jsonrpc": "2.0", "id": ev["id"], "error": {"code": -32601, "message": "not supported by Deck"}})
        elif method:
            if method == "turn/started":
                self.turn = (params.get("turn") or {}).get("id")
                self.working(True)
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                self.turn = None
                if self.quiz:  # an unanswered question goes with its turn
                    self.quiz_drop(self.quiz["key"])
                if turn.get("status") == "interrupted":
                    self.b.note("Stopped")
                elif turn.get("status") == "failed":
                    self.b.note(((turn.get("error") or {}).get("message")) or "The turn failed", err=True)
                self.working(False)
            elif method in ("item/started", "item/completed"):
                self.item(params.get("item") or {}, done=method == "item/completed")
            elif method in ("item/agentMessage/delta", "item/plan/delta"):
                self.b.delta(params.get("itemId"), 0, "text", params.get("delta", ""))
            elif method == "item/reasoning/summaryTextDelta":
                self.b.delta(params.get("itemId"), params.get("summaryIndex", 0), "think", params.get("delta", ""))
            elif method == "item/reasoning/textDelta":
                self.b.delta(params.get("itemId"), params.get("contentIndex", 0), "think", params.get("delta", ""))
            elif method == "item/commandExecution/outputDelta":
                iid = params.get("itemId")
                out = self.outputs.get(iid, "") + params.get("delta", "")
                if len(out) > OUT_LIMIT:
                    out = out[:OUT_LIMIT // 2] + "\n…\n" + out[-OUT_LIMIT // 2:]
                self.outputs[iid] = out
                self.b.tool_end(iid, out)
            elif method == "turn/plan/updated":
                labels = {"pending": "Pending", "inProgress": "Working", "completed": "Done"}
                text = "\n".join(f"- {labels.get(s.get('status'), s.get('status', ''))}: {s.get('step', '')}" for s in params.get("plan") or [])
                self.b.msg(f"plan-{params.get('turnId')}", [{"kind": "text", "text": "\n\n".join(x for x in (params.get("explanation"), text) if x)}])
            elif method == "thread/tokenUsage/updated" and params.get("threadId") == self.thread:
                self.usage = params.get("tokenUsage") or {}
                self.bar()
            elif method == "account/rateLimits/updated":
                self.rate_limits(params)
            elif method == "serverRequest/resolved":
                key = f"codex-{params.get('requestId')}"
                resolved = key in self.asks or bool(self.quiz and self.quiz["key"] == key)
                if self.quiz and self.quiz["key"] == key:
                    self.quiz_drop(key, "Resolved")
                if key in self.asks:
                    self.asks.pop(key)
                    self.b.answered(key, "Resolved")
                if resolved:
                    self.b.state("working" if self.busy else "your_turn")
            elif method in ("warning", "configWarning"):
                self.b.note(params.get("message") or params.get("summary") or "Codex warning")
            elif method == "thread/name/updated":
                self.b.name(params.get("threadName"))
            elif method == "thread/goal/updated" and params.get("threadId") == self.thread:
                self.set_goal(params.get("goal"))
            elif method == "thread/goal/cleared" and params.get("threadId") == self.thread:
                self.set_goal(None)
            elif method == "thread/settings/updated" and params.get("threadId") == self.thread:
                st = params.get("threadSettings") or {}
                self.parts[:2] = st.get("model") or self.parts[0], st.get("effort") or self.parts[1]
                self.bar()
            elif method == "error":
                self.b.note(((params.get("error") or {}).get("message")) or "Codex error", err=True)
        elif "id" in ev:  # a response to one of ours
            then = self.pending.pop(ev["id"], None)
            if "error" in ev:
                msg = (ev["error"] or {}).get("message") or "Codex error"
                if not self.thread and then == self.thread_ready and self.resume and not self.fresh:
                    # the saved thread can't be reopened (deleted, or it never got a message): start a new one
                    self.fresh = True
                    self.b.note(f"Couldn't continue that conversation ({msg}): a new one")
                    self.request("thread/start", {"cwd": self.cwd, **self.policy()}, self.thread_ready)
                    return
                self.b.note(msg, err=True)
                if not self.thread and then in (self.thread_ready, self.resume_newest):
                    self.b.state("failed")
            elif then:
                then(ev.get("result") or {})

    def run(self, first: str) -> int:
        self.request("initialize", {"clientInfo": {"name": "deck", "title": "Deck", "version": "1"}}, self.started)
        if first:
            self.queue.append(first)
        for ev in jsonl(self.p.stdout):
            self.event(ev)
        return self.p.wait()


class OpenCode(Adapter):
    """opencode serve: an HTTP API on a loopback port plus a server-sent event stream. Messages are made of parts
    (text, reasoning, tool) that are created, streamed (text deltas) and updated."""
    YOLO = "--auto"

    def __init__(self, *a):
        super().__init__(*a)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.sid, self.roles, self.parts, self.users = None, {}, {}, set()
        self.model = self.b.prefs.get("model")  # /model's pick: {providerID, modelID}, sent with each prompt
        self.variant = self.b.prefs.get("effort")  # /effort's pick: the model's variant (OpenCode's effort), likewise
        self.used = {}  # the model of the latest reply: what /compact summarizes with
        self.said, self.resent = "", 0.0  # the last message sent; when it went again without sampling settings
        self.shown = ["", "", ""]  # the bar: model, variant, mode
        self.costs = {}  # each reply's cost in dollars, by message id: the bar's session cost chip
        self.p = subprocess.Popen([*resolve("opencode"), "serve", "--hostname", "127.0.0.1", "--port", str(self.port), *self.args],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, cwd=self.cwd)

    def http(self, method: str, path: str, body=None, timeout: float = 30):
        req = urllib.request.Request(self.base + path, method=method, headers={"content-type": "application/json"},
                                     data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(req, timeout=timeout) as f:
            data = f.read()
        return json.loads(data) if data.strip() else None

    def command(self, c: dict) -> None:
        t = c.get("t")
        if not self.sid:
            return
        if t == "rename":  # the tab renamed: so is the conversation, as /resume and OpenCode's own lists show it
            self.http("PATCH", f"/session/{self.sid}", {"title": str(c.get("title", ""))})
            return
        if t == "say" and str(c.get("text", "")).strip():
            self.said = str(c["text"])
            self.http("POST", f"/session/{self.sid}/prompt_async", {"parts": [{"type": "text", "text": str(c["text"])}],
                                                                    **({"model": self.model} if self.model else {}),
                                                                    **({"variant": self.variant} if self.variant else {})})
            self.working(True)
        elif t == "abort":
            self.http("POST", f"/session/{self.sid}/abort", {})
        elif t == "answer":
            reply = {"Allow": "once", "Always": "always"}.get(c.get("value"), "reject")
            self.http("POST", f"/session/{self.sid}/permissions/{c.get('id')}", {"response": reply})
            self.b.answered(c.get("id"))
            self.b.state("working")

    def part(self, p: dict) -> None:
        mid = p.get("messageID")
        self.parts.setdefault(mid, {})[p.get("id")] = p
        if self.roles.get(mid) == "user":
            if p.get("type") == "text" and not p.get("synthetic") and p["id"] not in self.users:
                self.users.add(p["id"])
                self.b.user(p.get("text", ""))
            return
        if p.get("type") in ("text", "reasoning") and not (p.get("time") or {}).get("end"):
            return  # still streaming: its deltas show it
        blocks, final = [], True
        for q in self.parts[mid].values():
            if q.get("type") == "text":
                blocks.append({"kind": "text", "text": q.get("text", "")})
            elif q.get("type") == "reasoning":
                blocks.append({"kind": "think", "text": q.get("text", "")})
            elif q.get("type") == "tool":
                st = q.get("state") or {}
                blocks.append({"kind": "tool", "id": q.get("callID"), "name": q.get("tool"), "args": st.get("input") or {}})
                final = final and st.get("status") in ("completed", "error")
        self.b.msg(mid, blocks, final=final)
        st = p.get("state") or {}
        if p.get("type") == "tool" and st.get("status") in ("completed", "error"):
            self.b.tool_end(p.get("callID"), st.get("output") or st.get("error") or "", st.get("status") == "error")

    def message_info(self, info: dict) -> None:
        self.roles[info.get("id")] = info.get("role")
        if info.get("role") == "assistant":
            if info.get("providerID") and info.get("modelID"):
                self.used = {"providerID": info["providerID"], "modelID": info["modelID"]}
            if isinstance(info.get("cost"), (int, float)):
                self.costs[info.get("id")] = info["cost"]
            if not info.get("summary"):  # a /compact summary's agent ("compaction") isn't the session's mode
                self.shown = [info.get("modelID"), info.get("variant"), "yolo" if self.yolo else info.get("agent") or info.get("mode")]
            self.bar()
            if (info.get("error") or {}).get("name") == "MessageAbortedError":
                self.b.note("Stopped")
            elif info.get("error") and (info.get("time") or {}).get("completed"):
                err = info["error"]
                if self.sampling_rejected(err):
                    return
                self.b.note(((err.get("data") or {}).get("message")) or err.get("name") or "OpenCode error", err=True)

    def bar(self) -> None:
        """Model, variant, the session's cost so far (none while it's $0: free models), mode."""
        cost = sum(self.costs.values())
        self.b.bar(*self.shown[:2], f"${cost:.2f}" if cost >= 0.005 else "", self.shown[2])

    def event(self, ev: dict) -> None:
        t, pr = ev.get("type"), ev.get("properties") or {}
        sid = pr.get("sessionID") or (pr.get("info") or {}).get("sessionID") or (pr.get("part") or {}).get("sessionID")
        if sid and sid != self.sid:
            return
        if t == "message.updated":
            self.message_info(pr.get("info") or {})
        elif t == "message.part.updated":
            self.part(pr.get("part") or {})
        elif t == "message.part.delta" and pr.get("field") == "text":
            kind = (self.parts.get(pr.get("messageID"), {}).get(pr.get("partID")) or {}).get("type")
            self.b.delta(pr.get("messageID"), pr.get("partID"), "think" if kind == "reasoning" else "text", pr.get("delta", ""))
        elif t == "session.status":
            self.working((pr.get("status") or {}).get("type") == "busy")
            if (pr.get("status") or {}).get("type") == "idle":
                self.save_export()
        elif t == "session.idle":
            self.working(False)
            self.save_export()
        elif t == "session.updated":
            title = (pr.get("info") or {}).get("title", "")
            if title and not title.startswith("New session"):
                self.b.name(title)
        elif t == "permission.asked":
            if self.yolo:
                self.http("POST", f"/session/{self.sid}/permissions/{pr.get('id')}", {"response": "once"})
                return
            what = ", ".join(pr.get("patterns") or [])
            self.b.ask(pr.get("id"), f"Allow {pr.get('permission')}?\n{what}".strip(), ["Allow", "Always", "Deny"])
        elif t == "question.asked" and pr.get("sessionID") in (None, self.sid):  # its question tool
            qid = pr.get("id")
            self.questions(qid, [{"question": q.get("question") or q.get("header", ""), "multi": bool(q.get("multiple")),
                                  "options": [o.get("label", "") for o in q.get("options") or []],
                                  "other": q.get("custom", True) and not q.get("multiple")} for q in pr.get("questions") or []],
                           lambda answers: self.http("POST", f"/question/{qid}/reject") if answers is None else
                           self.http("POST", f"/question/{qid}/reply", {"answers": answers}))
        elif t in ("question.replied", "question.rejected"):  # answered or dismissed in another OpenCode client
            self.quiz_drop(pr.get("requestID"))
        elif t == "session.error":
            err = pr.get("error") or {}
            if err.get("name") != "MessageAbortedError" and not self.sampling_rejected(err):
                self.b.note(((err.get("data") or {}).get("message")) or err.get("name") or "OpenCode error", err=True)

    SAMPLING_ERROR = re.compile(r"temperature|top[_ -]?[pk]\b|sampling", re.I)

    def sampling_rejected(self, err: dict) -> bool:
        """A model that refuses Settings → OpenCode sampling though OpenCode lists it as taking a temperature: put it in
        the skip list (deploy/agents/opencode-sampling.js leaves it alone from then on) and send the message again. True: the
        error is handled, don't show it (both of OpenCode's reports of it: the failed reply and session.error)."""
        msg = ((err.get("data") or {}).get("message")) or ""
        skip_file = os.environ.get("DECK_SAMPLING_SKIP")
        if not (skip_file and self.SAMPLING_ERROR.search(msg)):
            return False
        if time.time() - self.resent < 5:  # the other report of the one just handled
            return True
        try:
            if not json.loads(Path(os.environ["DECK_SAMPLING"]).read_text()).get("sampling"):
                return False  # no sampling settings: the error is about something else
        except (KeyError, OSError, ValueError):
            return False
        model = "{providerID}/{modelID}".format(**self.used) if self.used else ""
        try:
            skip = json.loads(Path(skip_file).read_text())
        except (OSError, ValueError):
            skip = []
        if not model or model in skip or not self.said:
            return False
        Path(skip_file).write_text(json.dumps(skip + [model]))
        self.resent = time.time()
        self.b.note(f"{model} doesn't take the sampling settings (Settings → OpenCode sampling): sent again without them")
        self.command({"t": "say", "text": self.said})
        return True

    @staticmethod
    def last_variant(model: str) -> str:
        """The variant OpenCode last used with model ("provider/id")."""
        try:
            return (json.loads((Path.home() / ".local" / "state" / "opencode" / "model.json").read_text()).get("variant") or {}).get(model, "")
        except (OSError, ValueError, AttributeError):
            return ""

    def configured_model(self) -> tuple[str, str]:
        """The model and variant a new session uses (OpenCode's last pick), until its first reply names them."""
        try:
            st = json.loads((Path.home() / ".local" / "state" / "opencode" / "model.json").read_text())
            m = (st.get("recent") or [{}])[0]
            name = "/".join(x for x in (m.get("providerID"), m.get("modelID")) if x)
            return name, (st.get("variant") or {}).get(name, "")
        except (OSError, ValueError, AttributeError):
            return "", ""

    def run(self, first: str) -> int:
        model, variant = self.configured_model()
        if "/" in model:
            self.used = dict(zip(("providerID", "modelID"), model.split("/", 1)))
        if self.model:  # this session's own picks
            model = "/".join((self.model.get("providerID", ""), self.model.get("modelID", "")))
            variant = self.last_variant(model)
        self.shown = [model or "OpenCode", self.variant or variant, "yolo" if self.yolo else "build"]
        self.bar()  # its replies correct it
        for _ in range(240):  # the server takes a few seconds to come up
            if self.p.poll() is not None:
                return self.p.returncode
            try:
                sessions = self.http("GET", "/session?" + urllib.parse.urlencode({"directory": self.cwd}))
                break
            except urllib.error.HTTPError as e:  # it's up but refuses (e.g. a bad OPENCODE_CONFIG_CONTENT): say why
                self.b.note(f"OpenCode's server: {e.code} {e.read().decode(errors='replace')[:500]}", err=True)
                self.p.kill()
                return 1
            except OSError:  # not listening yet
                time.sleep(0.25)
        else:
            self.b.note("OpenCode's server didn't start", err=True)
            self.p.kill()
            return 1
        mine = sorted((s for s in sessions or [] if not s.get("parentID")), key=lambda s: (s.get("time") or {}).get("updated", 0))
        own = None
        if self.resume and self.b.prev_resume:  # this session's own conversation; else the folder's newest
            try:
                own = self.http("GET", f"/session/{urllib.parse.quote(self.b.prev_resume)}")
            except urllib.error.HTTPError:
                pass  # deleted
        own = own or (mine[-1] if self.resume and mine else None)
        if own:
            self.sid = own["id"]
            self.b.name(own.get("title", "") if not own.get("title", "").startswith("New session") else "")
            for m in self.http("GET", f"/session/{self.sid}/message") or []:
                self.message_info(m.get("info") or {})
                for p in m.get("parts") or []:
                    self.part(p)
        else:
            self.sid = self.http("POST", "/session", {})["id"]
        self.b.save(resume=self.sid)
        self.b.state("your_turn")
        self.save_export()
        events = urllib.request.urlopen(self.base + "/event")  # subscribed before the first message goes
        if first:
            self.command({"t": "say", "text": first})
        try:
            for line in events:
                if line.startswith(b"data:"):
                    try:
                        self.event(json.loads(line[5:]))
                    except ValueError:
                        pass
        except (OSError, http.client.HTTPException):  # the server is gone (exited, or killed by /resume, /new)
            pass
        return self.p.wait()

    def save_export(self) -> None:
        """The conversation as `opencode export` writes it, next to the chat's log: what Move host and the other hosts'
        copies of this chat carry (its sessions live in OpenCode's database, not a file)."""
        f = self.b.out_path.with_name(self.b.out_path.stem + ".opencode.json")
        try:
            data = json.dumps({"info": self.http("GET", f"/session/{self.sid}"),
                               "messages": self.http("GET", f"/session/{self.sid}/message") or []})
            if not f.exists() or f.read_text(encoding="utf-8") != data:
                f.with_suffix(".tmp").write_text(data, encoding="utf-8")
                f.with_suffix(".tmp").replace(f)
        except (OSError, ValueError, http.client.HTTPException):
            pass  # the next turn's end tries again

    def conversations(self) -> list[tuple[str, str]]:
        sessions = self.http("GET", "/session?" + urllib.parse.urlencode({"directory": self.cwd})) or []
        sessions = sorted((s for s in sessions if not s.get("parentID") and s["id"] != self.sid),
                          key=lambda s: -(s.get("time") or {}).get("updated", 0))[:12]
        return [(self.title_of(s), s["id"]) for s in sessions]

    def title_of(self, s: dict) -> str:
        """Its title; untitled (OpenCode's titler failed: its small model, say), its first message."""
        title = s.get("title") or ""
        if title and not title.startswith("New session"):
            return title
        try:
            for m in self.http("GET", f"/session/{s['id']}/message") or []:  # oldest first (limit= keeps the newest)
                if (m.get("info") or {}).get("role") == "user":
                    text = " ".join(p.get("text", "") for p in m.get("parts") or [] if p.get("type") == "text" and not p.get("synthetic"))
                    if text.strip():
                        return STAMP.sub("", text.strip(), count=1)
        except (OSError, ValueError):
            pass
        return title or s.get("slug") or ""

    def slash_compact(self, _arg: str) -> None:
        if not self.sid or not self.used:
            self.b.note("Nothing to compact yet")
            return
        sid, used = self.sid, self.used

        def go():  # it answers when the summary is written: off the commands' thread, so Stop still works
            try:
                self.http("POST", f"/session/{sid}/summarize", used, timeout=600)
            except (OSError, http.client.HTTPException) as e:
                self.b.note(f"Couldn't compact: {e}", err=True)
        threading.Thread(target=go, daemon=True).start()

    def set_model(self, model: dict) -> None:
        self.model, self.variant = model, None  # variants are per model
        self.b.pref(model=model, effort=None)
        self.shown[:2] = model["modelID"], ""
        self.bar()
        self.b.note(f"Model: {model['providerID']}/{model['modelID']} (from the next message)")
        self.b.state("your_turn")

    def set_variant(self, variant: str) -> None:
        self.variant = self.shown[1] = variant
        self.b.pref(effort=variant)
        self.bar()
        self.b.note(f"Effort: {variant} (from the next message)")
        self.b.state("your_turn")

    def slash_effort(self, arg: str) -> None:
        """/effort [variant]: the variants (effort levels) of the model in use."""
        m = self.model or self.used
        if not m:
            self.b.note("No model yet: send a message first, or pick one with /model")
            return
        res = self.http("GET", "/config/providers?" + urllib.parse.urlencode({"directory": self.cwd})) or {}
        prov = next((p for p in res.get("providers") or [] if p.get("id") == m.get("providerID")), {})
        levels = list(((prov.get("models") or {}).get(m.get("modelID")) or {}).get("variants") or {})
        if not levels:
            self.b.note(f"{m.get('modelID')} has no effort levels", err=True)
        elif arg in levels:
            self.set_variant(arg)
        else:
            self.pick("Effort?", [(x, x) for x in levels], self.set_variant)

    def slash_model(self, arg: str) -> None:
        if not self.sid:
            return
        res = self.http("GET", "/config/providers?" + urllib.parse.urlencode({"directory": self.cwd})) or {}
        models = [(f"{p['id']}/{mid}", {"providerID": p["id"], "modelID": mid})
                  for p in res.get("providers") or [] for mid in p.get("models") or {}]
        hits = [m for m in models if arg.lower() in m[0].lower()]
        exact = [m for m in hits if arg.lower() in (m[0].lower(), m[1]["modelID"].lower())]
        if arg and len(exact or hits) == 1:
            self.set_model((exact or hits)[0][1])
        elif len(hits) <= 20:
            self.pick("Which model?", hits, self.set_model)
        elif arg:
            self.b.note(f"{len(hits)} models match: narrow it down")
        else:  # too many to list: OpenCode's recent and favourite picks, and each provider's default
            try:
                st = json.loads((Path.home() / ".local" / "state" / "opencode" / "model.json").read_text())
            except (OSError, ValueError):
                st = {}
            short = [*(st.get("recent") or []), *(st.get("favorite") or []),
                     *({"providerID": p, "modelID": m} for p, m in (res.get("default") or {}).items())]
            short = [m for m in models if m[1] in short]
            self.pick(f"Which model? ({len(models)} in all: /model <name> to search them)", short, self.set_model)


class Agy(Adapter):
    """agy (Google Antigravity CLI) in print mode with stream-json both ways: one {"event": "user"} line per message,
    a turn each; out come step_update events (text deltas, tool calls) and a result per turn. It has no stop message
    and doesn't replay a resumed conversation (its steps are protobuf), so Stop restarts it on the same conversation
    and the bridge keeps its own record of the chat (AGY_LOG) to show again on a Restart or /resume."""
    YOLO = "--dangerously-skip-permissions"

    @property
    def SUMMARIES(self) -> Path:  # not at import: in a Windows pane the home is known only after windows_env()
        return Path.home() / ".gemini" / "antigravity-cli" / "conversation_summaries.db"

    def __init__(self, *a):
        super().__init__(*a)
        self.conv = self.b.prev_resume if self.resume else ""
        self.model = self.b.prefs.get("model") or ""
        self.sent = []  # messages written to agy, not yet taken in (its user_input step)
        self.mode = self.b.prefs.get("mode") or ""
        if "yolo" in self.b.prefs:
            self.yolo = bool(self.b.prefs["yolo"])
        self.last_input = ""
        self.compacting, self.compact_text = False, ""
        self.notes_synced = False
        self.default_effort = ""
        if "--effort" in self.args:
            index = self.args.index("--effort")
            if index + 1 < len(self.args):
                self.default_effort = self.args[index + 1]
                del self.args[index:index + 2]
        self.text = {}  # step index -> its reply text so far (steps are numbered across the whole conversation)
        self.stopping = False
        self.limits, self.limits_at = [], 0.0  # the bar's plan-limit chips (usage()), and when they were last asked for
        if self.resume and not self.conv:  # no saved conversation: the folder's newest
            convs = self.conversations()
            self.conv = convs[0][1] if convs else ""
        self.spawn()

    def spawn(self) -> None:
        if not self.model:
            current = self.native_command("/model")
            self.model = current.get("id") or ""
            if self.default_effort and re.search(r"-(low|medium|high)$", self.model):
                self.model = re.sub(r"-(low|medium|high)$", "-" + self.default_effort, self.model)
        notes = Path(os.path.expanduser(os.environ.get("DECK_NOTES", "~/deck-notes")))
        if not self.notes_synced and not self.conv and (notes / "AGENTS.md").exists():
            self.notes_synced = True
            self.b.note("Starting agy: syncing machine notes…")
            try:
                subprocess.run([sys.executable, "-m", "deck.notes", "sync", "-q"],
                               cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
            for i, a in enumerate(self.args[:-1]):
                if a == "--add-dir":
                    rules = Path(self.args[i + 1]) / ".agents" / "rules"
                    if rules.is_dir():
                        for name in ("AGENTS.md", "PREFERENCES.md", "PROFILE.md", "RESOURCES.md"):
                            src = notes / name
                            if src.exists():
                                (rules / f"infra-{name}").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        self.start([*resolve("agy"), "--input-format", "stream-json", "--output-format", "stream-json", "-p=",
                    *(["--conversation", self.conv] if self.conv else []), *([self.YOLO] if self.yolo else []),
                    *(["--model", self.model] if self.model else []),
                    *(["--mode", self.mode] if self.mode in ("accept-edits", "auto", "plan") else []), *self.args])

    def log_path(self) -> Path:
        return self.b.out_path.parent / "agy" / f"{self.conv}.jsonl"

    def record(self, ev: dict) -> None:
        if self.conv:
            self.log_path().parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path(), "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def replay(self) -> None:
        try:
            lines = self.log_path().read_text(encoding="utf-8").splitlines() if self.conv else []
        except OSError:
            lines = []
        if self.conv and not lines:
            self.b.note("Continuing an agy conversation (its earlier messages aren't shown here)")
        for line in lines:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("t") == "user":
                self.b.user(ev.get("text", ""))
            elif ev.get("t") == "msg":
                self.b.msg(ev.get("m"), ev.get("blocks") or [])
            elif ev.get("t") == "tool_end":
                self.b.tool_end(ev.get("id"), ev.get("out", ""), ev.get("err"))

    def handle(self, c: dict) -> None:
        text = str(c.get("text") or "").strip()
        command = text.split(maxsplit=1)[0] if text else ""
        if c.get("t") == "say" and command in {
                "/help", "/usage", "/quota", "/skills", "/agents", "/credits", "/config", "/settings",
                "/permissions", "/hooks", "/plugin", "/plugins", "/changelog"}:
            self.b.user(text)
            self.record({"t": "user", "text": text})
            try:
                out = subprocess.run([*resolve("agy"), "-p", text, "--output-format", "json",
                                      *(["--model", self.model] if self.model else [])], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=30, cwd=self.cwd)
                result = json.loads(out.stdout)
                if result.get("status") == "ERROR":
                    raise ValueError(result.get("error") or "agy command failed")
                response = result.get("response") or json.dumps((result.get("command") or {}).get("data") or {},
                                                               ensure_ascii=False, indent=2)
                key, blocks = f"command-{time.monotonic_ns()}", [{"kind": "text", "text": response.strip()}]
                self.b.msg(key, blocks)
                self.record({"t": "msg", "m": key, "blocks": blocks})
            except (OSError, subprocess.SubprocessError, ValueError) as e:
                self.b.note(f"agy {command}: {e}", err=True)
            return
        super().handle(c)

    def command(self, c: dict) -> None:
        t = c.get("t")
        if t == "say" and str(c.get("text", "")).strip():
            text = str(c["text"])
            self.last_input = text
            self.sent.append(text)
            self.send({"event": "user", "message": {"content": text}})  # while it works: agy runs it next
            self.working(True)
        elif t == "abort" and self.busy:
            self.compacting, self.compact_text = False, ""
            self.stopping = True
            self.kill()  # run() starts it again on this conversation

    def conversations(self) -> list[tuple[str, str]]:
        """This folder's conversations from agy's own summary index, newest first."""
        import sqlite3
        try:
            db = sqlite3.connect(f"file:{self.SUMMARIES.as_posix()}?mode=ro", uri=True)
            rows = db.execute("select conversation_id, title, preview, last_modified_time, workspace_uris from "
                              "conversation_summaries where parent_conversation_id = '' order by last_modified_time desc "
                              "limit 200").fetchall()
            db.close()
        except Exception:
            return []
        here = os.path.normcase(os.path.normpath(self.cwd))
        out = []
        for cid, title, preview, when, uris in rows:
            try:
                paths = [urllib.parse.unquote(urllib.parse.urlparse(u).path).lstrip("/" if WINDOWS else "")
                         for u in json.loads(uris or "[]")]
            except ValueError:
                paths = []
            if cid != self.conv and any(os.path.normcase(os.path.normpath(p)) == here for p in paths):
                try:  # stored in UTC, with 7 digits of seconds' fractions
                    when = datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", lambda m: m[1], str(when))).astimezone().strftime("%b %d %H:%M")
                except (ValueError, OSError, OverflowError):  # some carry year 1, which Windows can't localize
                    when = str(when)[:16]
                out.append((f"{when}  {title or preview or cid}", cid))
        return out

    def title(self) -> str:
        import sqlite3
        try:
            db = sqlite3.connect(f"file:{self.SUMMARIES.as_posix()}?mode=ro", uri=True)
            row = db.execute("select title from conversation_summaries where conversation_id = ?", (self.conv,)).fetchone()
            db.close()
            return row[0] if row else ""
        except Exception:
            return ""

    def native_command(self, command: str) -> dict:
        """Read agy's own configuration without sending a conversational turn."""
        try:
            out = subprocess.run([*resolve("agy"), "-p", command, "--output-format", "json",
                                  *(["--model", self.model] if self.model else [])],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=20, cwd=self.cwd)
            result = json.loads(out.stdout)
            if result.get("status") == "ERROR":
                raise ValueError(result.get("error") or "agy command failed")
            return (result.get("command") or {}).get("data") or {}
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            self.b.note(f"Couldn't read agy's {command}: {e}", err=True)
            return {}

    def slash_mode(self, arg: str) -> None:
        modes = [("Ask before tools", "ask"), ("Accept edits", "accept-edits"),
                 ("Auto approve tools", "auto"), ("Plan", "plan")]
        if arg not in {value for _, value in modes}:
            return self.pick("Agent mode", modes, self.slash_mode)
        self.mode = "" if arg == "ask" else arg
        self.yolo = arg == "auto"
        self.b.pref(mode=self.mode, yolo=self.yolo)
        self.bar()
        self.b.note(f"Mode: {arg}")
        self.restart()

    def slash_compact(self, arg: str) -> None:
        if self.busy:
            self.b.note("Stop the current turn before compacting")
            return
        if not self.conv:
            self.b.note("Nothing to compact yet")
            return
        self.compacting, self.compact_text = True, ""
        self.b.note("Compacting: preparing a handoff for a fresh conversation…")
        self.send({"event": "user", "message": {"content":
            "[Deck compaction] Summarize this conversation so a fresh instance can continue it. Include the user's "
            "goal and constraints, decisions, files and paths, work completed, unresolved issues, and next steps. "
            "Preserve exact commands and identifiers needed to continue. Do not use tools or carry out any work. "
            "Return only the handoff summary." + (f" Focus: {arg}" if arg else "")}})
        self.working(True)

    def approve_and_retry(self, choice: str) -> None:
        if choice != "allow":
            self.b.state("your_turn")
            return
        self.yolo, self.mode = True, "auto"
        self.b.pref(mode="auto", yolo=True)
        self.b.next_first = self.last_input
        self.switch(self.conv)

    def question_response(self, text: str) -> bool:
        match = re.search(r"<deck-question>\s*(.*?)\s*</deck-question>", text, re.S)
        if not match:
            return False
        try:
            question = json.loads(match[1])
            title, options = str(question["question"]), question.get("options") or []
            if not isinstance(options, list) or not all(isinstance(option, str) for option in options):
                return False
        except (ValueError, KeyError, TypeError):
            return False
        self.record({"t": "msg", "m": f"question-{time.monotonic_ns()}",
                     "blocks": [{"kind": "text", "text": title + ("\n" + " / ".join(options) if options else "")} ]})
        self.questions(f"agy-question-{time.monotonic_ns()}",
                       [{"question": title, "options": options, "other": True}],
                       lambda answers: self.command({"t": "say", "text":
                           "Answer: " + (", ".join(answers[0]) if answers else "Question skipped; proceed with available information.")}))
        return True

    def slash_effort(self, arg: str) -> None:
        data = self.native_command("/effort")
        if not data:
            if self.menu is not None:
                self.b.emit({"t": "menu", "rid": self.menu, "options": [], "message": "Couldn't read agy's effort settings"})
                self.menu = None
            return
        levels = data.get("available") or []
        if not data.get("adjustable"):
            if self.menu is not None:
                self.b.emit({"t": "menu", "rid": self.menu, "options": [], "message": "This model has fixed reasoning effort"})
                self.menu = None
            else:
                self.b.note("This model has fixed reasoning effort")
            return
        if arg in levels:
            self.set_effort(arg)
        else:
            if arg:
                self.b.note(f"Effort must be one of: {', '.join(levels)}", err=True)
            self.pick("Reasoning effort", [(level, level) for level in levels], self.set_effort)

    def set_effort(self, effort: str) -> None:
        # agy's model IDs already encode effort. Conflicting --model and --effort flags fail at startup.
        model = re.sub(r"-(low|medium|high|xhigh|max)$", "", self.model)
        self.set_model(f"{model}-{effort}")

    def slash_model(self, arg: str) -> None:
        try:  # one "id<TAB>name" line per model
            out = subprocess.run([*resolve("agy"), "models"], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=60).stdout
        except (OSError, subprocess.TimeoutExpired) as e:
            self.b.note(f"Couldn't list agy's models: {e}", err=True)
            return
        models = [(name.strip() or mid.strip(), mid.strip()) for mid, _, name in
                  (line.partition("\t") for line in out.splitlines()) if mid.strip()]
        hits = [m for m in models if arg.lower() in f"{m[0]} {m[1]}".lower()] if arg else models
        if arg and len(hits) == 1:
            self.set_model(hits[0][1])
        else:
            self.pick("Which model?", hits or models, self.set_model)

    def set_model(self, model: str) -> None:
        """agy takes its model at start: restart it on this conversation with the new one."""
        self.model, self.limits_at = model, 0.0
        self.b.pref(model=model)
        self.bar()
        self.usage()
        self.b.note(f"Model: {model}")
        self.restart()

    def restart(self) -> None:
        self.stopping = True
        self.kill()

    def bar(self, mode: str = "") -> None:
        self.permission_mode = mode or getattr(self, "permission_mode", "")
        effort = re.search(r"-(low|medium|high|xhigh|max)$", self.model)
        self.b.bar(self.model or "agy default", *([effort[1]] if effort else []), *self.limits,
                   mode="yolo" if self.yolo else self.mode or self.permission_mode or "ask")

    def usage(self) -> None:
        """Plan limits, from agy's /usage (print mode answers it at once, without a turn): the used share of each
        5-hour and weekly bucket for the model's group (Gemini, or Claude and GPT; Argon also has its own weekly
        bucket), as Claude's bar shows them. With agy's default model, every group. At most once a minute."""
        if time.time() - self.limits_at < 60:
            return
        self.limits_at = time.time()

        def go():
            try:
                out = subprocess.run([*resolve("agy"), "-p", "/usage", "--output-format", "json"], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=60, cwd=self.cwd).stdout
                groups = (json.loads(out).get("command") or {}).get("data", {}).get("groups") or []
            except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
                return
            m = self.model.lower()
            want = ("claude", "gpt") if "claude" in m or "gpt" in m else ("gemini", "argon") if m else ()
            chips = []
            for g in groups:
                name = str(g.get("name", "")).lower()
                if want and not any(w in name for w in want):
                    continue
                if "argon" in name and "argon" not in m and m:
                    continue
                tag = "argon " if "argon" in name else "" if want else "3p " if "claude" in name else "gem "
                for k in sorted(g.get("buckets") or [], key=lambda k: k.get("window") != "5h"):
                    if k.get("remaining_fraction") is not None:
                        win = {"5h": "5h", "weekly": "wk"}.get(k.get("window"), k.get("window") or "")
                        chips.append(f"{tag}{win} {round(100 * (1 - k['remaining_fraction']))}%")
            self.limits = chips
            self.bar()
        threading.Thread(target=go, daemon=True).start()

    def event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "init":
            if ev.get("conversation_id") and ev["conversation_id"] != self.conv:
                self.conv = ev["conversation_id"]
            self.b.save(resume=self.conv)
            self.bar((ev.get("init") or {}).get("permission_mode", ""))
        elif kind == "step_update":
            s = ev.get("step_update") or {}
            step, state, typ = s.get("step_index"), s.get("state"), s.get("step_type")
            key = f"agy-{step}"
            if typ == "user_input":
                if self.sent and not self.compacting:
                    text = self.sent.pop(0)
                    self.b.user(text)
                    self.record({"t": "user", "text": text})
                self.working(True)
            elif typ == "agent_response":
                d = s.get("text_delta") or ""
                self.text[key] = self.text.get(key, "") + d
                if not self.compacting:
                    self.b.delta(key, 0, "text", d)
                if state == "DONE" and self.text.get(key, "").strip():  # (an empty one comes before a tool call)
                    response = self.text.pop(key).strip()
                    if self.compacting:
                        self.compact_text += response + "\n"
                    elif self.question_response(response):
                        self.b.msg(key, [])
                    else:
                        blocks = [{"kind": "text", "text": response}]
                        self.b.msg(key, blocks)
                        self.record({"t": "msg", "m": key, "blocks": blocks})
            elif typ == "tool":
                info = s.get("tool_info") or {}
                blocks = [{"kind": "tool", "id": key, "name": info.get("name") or s.get("tool_name") or "tool",
                           "args": info.get("parameters") or {}}]
                self.b.msg(key, blocks, final=state != "ACTIVE")
                if state != "ACTIVE":
                    err = state not in ("DONE",) or bool(info.get("error"))
                    out = info.get("output") if info.get("output") is not None else info.get("error") or ""
                    out = (out if isinstance(out, str) else json.dumps(out)).replace("\r\n", "\n").strip("\n")
                    self.record({"t": "msg", "m": key, "blocks": blocks})
                    short = out if len(out) <= OUT_LIMIT else out[:OUT_LIMIT // 2] + "\n…\n" + out[-OUT_LIMIT // 2:]
                    self.record({"t": "tool_end", "id": key, "out": short, "err": err})
                    self.b.tool_end(key, out, err)
            else:
                self.b.hidden(f"agy {typ} step", s)
        elif kind == "result":
            r = ev.get("result") or {}
            if r.get("command"):
                command = r["command"]
                if self.sent:
                    self.b.user(self.sent.pop(0))
                response = r.get("response") or json.dumps(command.get("data") or {}, ensure_ascii=False, indent=2)
                blocks = [{"kind": "text", "text": response.strip()}]
                key = f"command-{time.monotonic_ns()}"
                self.b.msg(key, blocks)
                self.record({"t": "msg", "m": key, "blocks": blocks})
            if self.compacting:
                self.compacting = False
                summary = self.compact_text.strip() or str(r.get("response") or "").strip()
                if summary and r.get("status") == "SUCCESS":
                    self.b.next_first = ("[Deck] The conversation was compacted. Continue from this handoff. "
                        "Acknowledge in one short sentence, then wait for the user's next message.\n\n" + summary)
                    self.working(False)
                    self.switch("")
                    return
                self.b.note("Compaction failed; the original conversation is still open", err=True)
            denied = r.get("denied_actions") or []
            if denied:
                self.working(False)
                actions = ", ".join(str(action.get("display_name") or action.get("action")) for action in denied)
                self.b.note(f"agy blocked {actions}: its headless mode cannot display native permission prompts.", err=True)
                self.pick("Allow tools for this session and retry the last message?",
                          [("Allow tools for this session and retry", "allow"), ("Keep current permissions", "keep")],
                          self.approve_and_retry)
            if r.get("status") not in ("SUCCESS", None):
                self.b.note(r.get("error") or f"agy: {r.get('status')}", err=True)
                self.sent = []
                for qid, _ in self.b.waiting:
                    self.b.emit({"t": "unqueued", "id": qid})
                self.b.waiting = []
            if not self.sent and not self.quiz and not denied:
                self.working(False)
            self.b.name(self.title())
            self.usage()
        else:
            self.b.hidden("agy event", ev)

    def run(self, first: str) -> int:
        self.replay()
        self.bar()
        self.usage()
        self.b.state("your_turn")
        if first:
            self.command({"t": "say", "text": first})
        while True:
            for ev in jsonl(self.p.stdout):
                self.event(ev)
            code = self.p.wait()
            if not self.stopping:
                return code
            # Stop or a new model: agy again on the same conversation (what it hadn't taken in is dropped)
            self.stopping, self.sent = False, []
            for key, text in list(self.text.items()):
                if text.strip():
                    self.b.msg(key, [{"kind": "text", "text": text.strip()}])
                    self.record({"t": "msg", "m": key, "blocks": [{"kind": "text", "text": text.strip()}]})
            self.text = {}
            if self.b.waiting:  # typed while it worked: they went down with that agy, so the page takes them back
                for qid, _ in self.b.waiting:
                    self.b.emit({"t": "unqueued", "id": qid})
                self.b.note(f"Not sent: {len(self.b.waiting)} waiting message(s)")
                self.b.waiting = []
            if self.busy:
                self.b.note("Stopped")
            self.working(False)
            self.spawn()


ADAPTERS = {"pi": Pi, "claude": Claude, "codex": Codex, "opencode": OpenCode, "agy": Agy, "openclaw": OpenClaw}


def windows_env() -> None:
    """A native program in an MSYS tmux pane gets HOME but not USERPROFILE or APPDATA; Python (Path.home) and the
    agents look for their settings and logins through those."""
    home = os.environ.get("HOME", "")
    if not WINDOWS or not re.match(r"^[A-Za-z]:", home):
        return
    os.environ.setdefault("USERPROFILE", home)
    os.environ.setdefault("HOMEDRIVE", home[:2])
    os.environ.setdefault("HOMEPATH", home[2:])
    os.environ.setdefault("APPDATA", os.path.join(home, "AppData", "Roaming"))
    os.environ.setdefault("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))


def main(argv: list[str]) -> int:
    if argv[:1] == ["hook"]:
        return hook_main(*argv[1:3])
    if argv[:1] == ["statusline"]:
        return statusline_main(*argv[1:3])
    sys.stdout.reconfigure(encoding="ascii" if WINDOWS else "utf-8", errors="replace")  # see show()
    windows_env()
    agent, folder, session, resume, first = argv[:5]
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    b = Bridge(folder, session)
    import platform
    host = os.environ.get("DECK_HOST_NAME") or platform.node()  # as deck.core names this node (the bridge runs on its own)
    b.save(agent=agent, folder=os.getcwd(), host=host)  # a takeover from the state copy when this node is down (deck/site.py)
    b.emit({"t": "clear", "log": time.time_ns()})  # this log's id: a page reconnecting to it carries on (server.py)
    cur = []  # the running adapter (replaced by /resume and /new)
    try:
        # Restart continues this chat's own conversation; the folder's newest only for a chat with no state yet
        # (one /new left empty has none to continue, and "newest" could be another live session's)
        cur.append(ADAPTERS[agent](b, args, resume == "1" and (bool(b.prev_resume) or not b.started_before), os.getcwd()))
    except SystemExit as e:  # the agent isn't installed
        b.note(str(e), err=True)
        b.state("failed")
        return 1

    def pump():
        for c in b.commands():
            try:
                cur[-1].handle(c)
            except (OSError, ValueError) as e:
                b.note(f"Couldn't send that: {e}", err=True)

    def watch():
        """Stdin ends when the pane closes (Kill, Restart). The bridge must go with it: on Windows nothing else ends
        it (it shares tmux's hidden console, which stays), and it would keep writing to the restarted one's log."""
        while sys.stdin.buffer.read1(4096):
            pass
        with b.lock:
            b.closed = True
        cur[-1].kill()
        os._exit(0)
    threading.Thread(target=pump, daemon=True).start()
    threading.Thread(target=watch, daemon=True).start()
    while True:
        code = cur[-1].run(first)
        if b.switch_to is None:
            break
        # /resume or /new: the same agent again, on the chosen conversation (the page starts over at "clear")
        cur[-1].kill()
        b.prev_resume, b.switch_to, first = b.switch_to, None, getattr(b, "next_first", "")
        b.next_first = ""
        b.status.update(resume=b.prev_resume, preview="")
        b.emit({"t": "clear"})
        cur.append(ADAPTERS[agent](b, args, bool(b.prev_resume), os.getcwd()))
    b.note(f"{agent} exited ({code})", err=bool(code))
    b.state("done" if code == 0 else "failed")
    return code

if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(code)  # not sys.exit: the watch thread is blocked reading stdin, and shutdown would wait on it
