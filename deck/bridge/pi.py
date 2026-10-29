"""Pi's adapter: pi --mode rpc (JSONL commands on stdin, responses and session events on stdout)."""
import json
import os
import re
import time
from pathlib import Path

from . import util
from .adapter import Adapter
from .util import OUT_LIMIT


class Pi(Adapter):
    """pi --mode rpc: JSONL commands on stdin, responses and session events on stdout (Pi's docs/rpc.md)."""

    def __init__(self, *a):
        super().__init__(*a)
        self.m, self.asks, self.n, self.pending, self.provider = 0, {}, 0, {}, ""
        own = self.b.prev_resume if self.resume else ""  # its session file; else the folder's newest
        self.start([*util.resolve("pi"), "--mode", "rpc", *(["--session", own] if own else ["--continue"] if self.resume else []),
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
                        first = util.text_of(e["message"].get("content")).strip()
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
            self.b.user(util.text_of(msg.get("content")))
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
            self.b.tool_end(msg.get("toolCallId"), util.text_of(msg.get("content")), msg.get("isError"))
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
                kind = "text" if u["type"] == "text_delta" else "think"
                self.b.delta(self.m, u.get("contentIndex", 0), kind, u.get("delta", ""))
        elif t == "message_end":
            self.message(ev.get("message") or {}, live=True)
        elif t == "tool_execution_end":
            r = ev.get("result") or {}
            self.b.tool_end(ev.get("toolCallId"), util.text_of(r.get("content")) if isinstance(r, dict) else str(r),
                            ev.get("isError"))
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
                    {"type": "extension_ui_response", "id": rid,
                     **({"value": answers[0][0]} if answers else {"cancelled": True})}))
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
        for ev in util.jsonl(self.p.stdout):
            self.event(ev)
        return self.p.wait()
