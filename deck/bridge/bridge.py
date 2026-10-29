"""The Bridge: the chat's files (its event log, command queue and state) and the events every adapter emits
through it."""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import util
from .util import OUT_LIMIT, WINDOWS

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


# The stamp's source when another session's agent sent the message (api/agents.py agent_session_send): the page labels it,
# and the pane and Copy text mark it, so no one takes it for the user's (4-chat.js and sessions.chat_text match it too)
AGENT_FROM = re.compile(r"agent in .+|another agent")


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
        # A chat of ours: no resume id means a new one, not the newest.
        self.started_before = isinstance(prev, dict) and bool(prev)
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
                   **({"ts": round(datetime.strptime(m[1], "%a %Y-%m-%d %H:%M %z").timestamp()),
                       "from": m[2] or ""} if m else {})})
        util.show(f"\n\x1b[1m> {f'[from {m[2]}, not the user] ' if agent else ''}{text}\x1b[0m\n")

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
                util.show(f"\n{x['text']}\n")
            elif x["kind"] == "file":
                util.show(f"[{x['name']}]\n")
            elif x["kind"] == "tool":
                util.show(f"\x1b[90m- {x['name']} {json.dumps(x['args'])[:100]}\x1b[0m\n")
        text = next((x["text"] for x in reversed(blocks) if x["kind"] == "text"), "")
        if text:
            self.save(preview=text)

    def follow_repo(self, tool: dict) -> None:
        """The session's project follows the repo its agent edits files in (sessions mostly start in the work folder,
        and agents forget to tell Deck), so its main-page bar shows that project's page. Edits only: reading another
        repo's notes (infra's AGENTS.md at the start) is not working in it. Once it follows one, another takes two edits:
        a note recorded in another repo (infra's AGENTS.md) doesn't move it."""
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
                    f"http://127.0.0.1:{os.environ.get('DECK_PORT', '8787')}"
                    f"/api/sessions/{urllib.parse.quote(self.session)}/project",
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
