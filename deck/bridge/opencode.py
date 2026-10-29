"""OpenCode's adapter: opencode serve (its HTTP API and event stream)."""
import http.client
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from . import util
from .adapter import Adapter
from .bridge import STAMP


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
        argv = [*util.resolve("opencode"), "serve", "--hostname", "127.0.0.1", "--port", str(self.port), *self.args]
        self.p = subprocess.Popen(argv,
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
                self.shown = [info.get("modelID"), info.get("variant"),
                              "yolo" if self.yolo else info.get("agent") or info.get("mode")]
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
            state = json.loads((Path.home() / ".local" / "state" / "opencode" / "model.json").read_text())
            return (state.get("variant") or {}).get(model, "")
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
                    text = " ".join(p.get("text", "") for p in m.get("parts") or []
                                    if p.get("type") == "text" and not p.get("synthetic"))
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
