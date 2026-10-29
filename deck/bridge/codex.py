"""Codex's adapter: codex app-server (JSON-RPC over stdio)."""
import re
import shlex
import threading
from pathlib import Path

from . import util
from .adapter import Adapter
from .util import OUT_LIMIT


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
        self.start([*util.resolve("codex"), "app-server", *self.args])

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
                self.request("turn/start", {"threadId": self.thread, "input": inp,
                                            **({"model": self.model} if self.model else {}),
                                            **({"effort": self.effort} if self.effort else {})})
                self.working(True)
        elif t == "abort" and self.turn:
            self.request("turn/interrupt", {"threadId": self.thread, "turnId": self.turn})
        elif t == "answer" and c.get("id") in self.asks:
            rid, v = self.asks.pop(c["id"]), c.get("value")
            if isinstance(rid, tuple):  # extra permissions: granted for this turn or the session, or none
                rid, asked = rid
                grant = {"permissions": asked, "scope": "session" if v == "Allow for session" else "turn"}
                self.send({"jsonrpc": "2.0", "id": rid,
                           "result": grant if v in ("Allow", "Allow for session") else {"permissions": {}}})
            else:
                decision = {"Allow": "accept", "Allow for session": "acceptForSession"}.get(v, "decline")
                self.send({"jsonrpc": "2.0", "id": rid, "result": {"decision": decision}})
            self.b.answered(c["id"])
            self.b.state("working")

    def item(self, it: dict, done: bool) -> None:
        kind, iid = it.get("type"), it.get("id")
        if kind == "userMessage":
            if done:
                self.b.user(util.text_of(it.get("content")))
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
                keys = ("prompt", "receiverThreadIds", "receiverThreadId", "model", "reasoningEffort")
                args = {k: it[k] for k in keys if it.get(k)}
            elif kind == "imageView":
                name, args = "view_image", {"path": it.get("path", "")}
            else:
                name = ".".join(x for x in (it.get("server") or it.get("namespace"), it.get("tool")) if x)
                args = it.get("arguments") or {}
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
                    out = it.get("aggregatedOutput")
                    self.b.tool_end(iid, out if out is not None else self.outputs.get(iid, ""),
                                    it.get("exitCode") not in (0, None) or it.get("status") == "failed")
                    self.outputs.pop(iid, None)
                elif kind == "fileChange":
                    self.b.tool_end(iid, "\n".join(ch.get("diff", "") for ch in it.get("changes") or []),
                                    it.get("status") == "failed")
                elif kind == "mcpToolCall":
                    self.b.tool_end(iid, it.get("error") or it.get("result") or "", bool(it.get("error")))
                elif kind == "dynamicToolCall":
                    self.b.tool_end(iid, it.get("contentItems") or "", it.get("success") is False)
                elif kind in ("collabAgentToolCall", "collabToolCall"):
                    self.b.tool_end(iid, it.get("agentsStates") or it.get("agentStatus") or it.get("status") or "",
                                    it.get("status") == "failed")
                else:
                    self.b.tool_end(iid, "")

    def thread_ready(self, res: dict) -> None:
        th = res.get("thread") or {}
        self.thread = th.get("id")
        if self.thread:
            self.b.save(resume=self.thread)
        self.parts = [self.model or res.get("model"), self.effort or res.get("reasoningEffort"),
                      "yolo" if self.yolo else
                      res.get("sandbox", {}).get("type") if isinstance(res.get("sandbox"), dict) else res.get("sandbox")]
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
            status = "" if g.get("status") == "active" else " " + str(g.get("status"))
            self.goal = f"Goal{status}: {o[:60]}{'…' if len(o) > 60 else ''}"
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
                entries = (perms.get("fileSystem") or {}).get("entries") or []
                what = ", ".join(x for x in (
                    "network access" if (perms.get("network") or {}).get("enabled") else "",
                    "files: " + ", ".join(str(e.get("path") or e) for e in entries) if entries else "") if x)
                title = "\n".join(x for x in ("Allow more access?", what, params.get("reason")) if x)
                self.b.ask(key, title, ["Allow", "Allow for session", "Deny"])
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
                text = "\n".join(f"- {labels.get(s.get('status'), s.get('status', ''))}: {s.get('step', '')}"
                                 for s in params.get("plan") or [])
                self.b.msg(f"plan-{params.get('turnId')}",
                           [{"kind": "text", "text": "\n\n".join(x for x in (params.get("explanation"), text) if x)}])
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
        for ev in util.jsonl(self.p.stdout):
            self.event(ev)
        return self.p.wait()
