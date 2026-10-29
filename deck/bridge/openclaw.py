"""OpenClaw's adapter: its Gateway, through deploy/agents/openclaw-bridge (one connection, JSON lines)."""
import base64
import mimetypes
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import util
from .adapter import Adapter
from .util import WINDOWS

OPENCLAW_BRIDGE = util.REPO / "deploy" / "agents" / "openclaw-bridge"


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
            # Windows: through cmd (util.resolve() would take npm.cmd's first script, npm-prefix.js); fixed arguments, no quoting
            npm = "npm ci --ignore-scripts --no-audit --no-fund"
            r = subprocess.run(npm if WINDOWS else npm.split(), shell=WINDOWS, cwd=OPENCLAW_BRIDGE,
                               capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f"npm ci in {OPENCLAW_BRIDGE} failed: {(r.stderr or r.stdout).strip()[-500:]}")
        self.argv = [*util.resolve("node"), str(OPENCLAW_BRIDGE / "bridge.mjs")]
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
                results.append((c.get("tool_use_id"), util.text_of(c.get("content")), c.get("is_error") or c.get("isError")))
            elif kind in ("image", "video", "audio", "attachment"):  # a reply's file (MEDIA:, the message tool's media)
                f = (c.get("attachment") or {}) if kind == "attachment" else c
                blocks.append({"kind": "file", "art": str(f.get("artifactId") or ""), "mime": str(f.get("mimeType") or ""),
                               "name": str(f.get("label") or f.get("fileName") or f.get("alt") or kind),
                               "size": f.get("sizeBytes") or 0})
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
            self.b.note("OpenClaw asks for a secret: answer it in OpenClaw's Control UI or TUI (Deck doesn't take secrets)",
                        err=True)
            return
        self.asks.append(q)
        self.next_ask()

    def next_ask(self) -> None:
        if self.quiz or not self.asks:
            return
        q = self.asks.pop(0)
        def card(x):  # options' descriptions go under the question, the options themselves are its buttons
            more = "".join(f"\n- **{o.get('label')}**: {o['description']}" for o in x.get("options") or []
                           if o.get("description"))
            return {"question": f"{x.get('question', '')}{more}",
                    "options": [str(o.get("label", "")) for o in x.get("options") or []],
                    "multi": bool(x.get("multiSelect")),
                    "other": bool(x.get("isOther") or not x.get("options"))}  # ask_user's take text
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
            text = msg["content"] if isinstance(msg.get("content"), str) else util.text_of(msg.get("content"))
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
            self.b.tool_end(msg.get("toolCallId"), util.text_of(msg.get("content")), msg.get("isError"))
        elif role == "custom" and not live:
            self.b.note(util.text_of(msg.get("content")) if not isinstance(msg.get("content"), str) else msg["content"])

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
                    text = p.get("deltaText", "") if p.get("replace") else util.text_of((p.get("message") or {}).get("content"))
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
                o["blocks"].append({"kind": "tool", "id": d.get("toolCallId"),
                                    "name": str(d.get("name", "")).removeprefix("mcp__openclaw__"),
                                    "args": d.get("args") or {}})
                self.b.msg(o["m"], o["blocks"], final=False)
            elif stream == "tool" and d.get("phase") == "result":
                result = d.get("result")
                self.b.tool_end(d.get("toolCallId"), result if isinstance(result, str) else util.text_of(result),
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
            yield from util.jsonl(self.p.stdout)
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
                            f"`openclaw devices approve {ev.get('requestId') or '<request id>'}` there "
                            "(it connects by itself then)", err=True)
                self.b.state("needs_you", "OpenClaw: approve this device")
            elif t == "error":
                self.b.note(str(ev.get("message")), err=True)
        return self.p.wait()
