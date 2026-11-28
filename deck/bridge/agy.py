"""agy's adapter (Google's Antigravity CLI): Agent Client Protocol (ACP, JSON-RPC over stdio)."""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from . import util
from .adapter import Adapter
from .util import WINDOWS

EFFORT_RE = re.compile(r"-(low|medium|high|xhigh|max)$")
INFO_COMMANDS = {
    "/help", "/usage", "/quota", "/skills", "/agents", "/credits", "/config", "/settings",
    "/permissions", "/hooks", "/plugin", "/plugins", "/changelog",
}


def _clean_tool_out(out: str) -> str:
    """Drop agy's per-step timestamp header and exit-0 boilerplate so tool output reads cleanly."""
    out = re.sub(r"^(?:Created At:[^\n]*\n)?(?:Completed At:[^\n]*\n)?", "", out)
    return re.sub(r"^Command exited with code 0\.(?:\n+(?:Command output:\n)?)?", "", out).strip("\n")


def _tool_output(u: dict) -> str:
    """Extract readable output from an ACP tool_call / tool_call_update payload."""
    parts = []
    for item in u.get("content") or []:
        if not isinstance(item, dict):
            continue
        t = item.get("type")
        if t == "content" and isinstance(item.get("content"), dict) and item["content"].get("type") == "text":
            parts.append(str(item["content"].get("text") or ""))
        elif t == "text" and item.get("text"):
            parts.append(str(item["text"]))
    raw = u.get("rawOutput")
    if not parts and raw is not None and raw != {} and raw != "":
        if isinstance(raw, str):
            parts.append(raw)
        elif isinstance(raw, dict):
            text = raw.get("output") or raw.get("stdout") or raw.get("content") or raw.get("error") or raw.get("message")
            parts.append(text if isinstance(text, str) else json.dumps(raw, ensure_ascii=False))
        else:
            parts.append(json.dumps(raw, ensure_ascii=False))
    text = "\n".join(p for p in parts if p).replace("\r\n", "\n")
    return _clean_tool_out(text).strip("\n")


def _parse_elicitation(schema: dict) -> list[dict]:
    """Turn an ACP elicitation/create JSON Schema into Deck question specs plus key/value maps."""
    props = (schema or {}).get("properties") or {}
    out = []
    for key, prop in props.items():
        if key.endswith("_other") or not isinstance(prop, dict):
            continue
        title = str(prop.get("title") or prop.get("description") or key)
        other_key = f"{key}_other" if f"{key}_other" in props else ""
        if prop.get("type") == "array" and isinstance(prop.get("items"), dict):
            choices = prop["items"].get("anyOf") or prop["items"].get("oneOf") or []
            opts = [str(c.get("title") or c.get("const")) for c in choices if isinstance(c, dict)]
            cmap = {str(c.get("title") or c.get("const")): c.get("const", c.get("title")) for c in choices if isinstance(c, dict)}
            out.append({"key": key, "other_key": other_key, "question": title, "options": opts,
                        "multi": True, "other": bool(other_key), "map": cmap})
        else:
            choices = prop.get("oneOf") or prop.get("anyOf") or []
            opts = [str(c.get("title") or c.get("const")) for c in choices if isinstance(c, dict)]
            cmap = {str(c.get("title") or c.get("const")): c.get("const", c.get("title")) for c in choices if isinstance(c, dict)}
            out.append({"key": key, "other_key": other_key, "question": title, "options": opts,
                        "multi": False, "other": bool(other_key) or not opts, "map": cmap})
    return out


class Agy(Adapter):
    """agy (Google Antigravity CLI) over ACP (`agy acp --stdio`, JSON-RPC over stdio): streaming text and thinking,
    native tool calls, native permission prompts, native ask_question elicitations, live model/effort/mode switching,
    native /compact and Stop (`session/cancel`), context window usage, and full session history replay (`session/load`)."""
    YOLO = "--dangerously-skip-permissions"
    MODEL_IN_PROTOCOL = True

    @property
    def SUMMARIES(self) -> Path:  # not at import: on Windows home is known only after util.windows_env()
        return Path.home() / ".gemini" / "antigravity-cli" / "conversation_summaries.db"

    def __init__(self, *a):
        super().__init__(*a)
        self.conv = self.b.prev_resume if self.resume else ""
        self.model = self.b.prefs.get("model") or self.default_model or ""
        self.effort = self.b.prefs.get("effort") or ""
        self.mode = self.b.prefs.get("mode") or ""
        if "yolo" in self.b.prefs:
            self.yolo = bool(self.b.prefs["yolo"])
        self.allow_all = False
        self.default_effort = ""
        if "--effort" in self.args:
            index = self.args.index("--effort")
            if index + 1 < len(self.args):
                self.default_effort = self.args[index + 1]
                del self.args[index:index + 2]
        if not self.effort and self.default_effort:
            self.effort = self.default_effort
        self.instructions = ""
        if "--deck-context" in self.args:
            i = self.args.index("--deck-context")
            if i + 1 < len(self.args):
                try:
                    self.instructions = Path(self.args[i + 1]).read_text(encoding="utf-8")
                except OSError:
                    self.instructions = ""
                del self.args[i:i + 2]
        self.n, self.pending, self.nlock = 0, {}, threading.Lock()
        self.ready = False
        self.replaying = False
        self.queue: list[str] = []
        self.asks: dict[str, tuple] = {}
        self.active_turns = 0
        self.seq = 0
        self.cur_kind, self.cur_key, self.cur_buf = "", "", ""
        self.user_buf = ""
        self.tools: dict[str, dict] = {}
        self.config_options: dict[str, dict] = {}
        self.available_models: list[tuple[str, str]] = []
        self.current_mode = ""
        self.ctx_used: int | None = None
        self.ctx_size: int | None = None
        self.limits: list[str] = []
        self.limits_at = 0.0
        self.notes_synced = False
        self.restarting = False
        self.spawn()

    def spawn(self) -> None:
        notes = Path(os.path.expanduser(os.environ.get("DECK_NOTES", "~/deck-notes")))
        if not self.notes_synced and not self.conv and (notes / "AGENTS.md").exists():
            self.notes_synced = True
            self.copy_notes(notes)

            def sync():
                try:
                    subprocess.run([sys.executable, "-m", "deck.notes", "sync", "-q"],
                                   cwd=util.REPO, capture_output=True, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    return
                self.copy_notes(notes)
            threading.Thread(target=sync, daemon=True).start()
        self.start([*util.resolve("agy"), "acp", "--stdio",
                    *([self.YOLO] if self.yolo else []), *self.args])

    def copy_notes(self, notes: Path) -> None:
        """Keep legacy --add-dir rules updated with valid YAML frontmatter if an --add-dir context folder is passed."""
        for i, a in enumerate(self.args[:-1]):
            if a == "--add-dir":
                rules = Path(self.args[i + 1]) / ".agents" / "rules"
                if rules.is_dir():
                    host = (os.environ.get("DECK_HOST_NAME") or socket.gethostname()).lower()
                    for name in ("AGENTS.md", f"hosts/{host}.md"):
                        src = notes / name
                        try:
                            if src.exists():
                                body = src.read_text(encoding="utf-8")
                                hdr = f'---\ntrigger: always_on\ndescription: "Machine notes from {name}."\n---\n'
                                (rules / f"infra-{Path(name).name}").write_text(hdr + body, encoding="utf-8")
                        except OSError:
                            pass

    def request(self, method: str, params: dict, then=None) -> int:
        with self.nlock:
            self.n += 1
            n = self.n
            self.pending[n] = then
        self.send({"jsonrpc": "2.0", "id": n, "method": method, "params": params})
        return n

    def _meta(self) -> dict:
        if not self.instructions:
            return {}
        return {"_meta": {"antigravity": {"systemPrompt": {"append": self.instructions}}}}

    def _apply_config_options(self, opts: list[dict] | None) -> None:
        for opt in opts or []:
            if not isinstance(opt, dict) or not opt.get("id"):
                continue
            oid = str(opt["id"])
            self.config_options[oid] = opt
            val = opt.get("currentValue")
            if oid == "model" and val:
                self.model = str(val)
                if opt.get("options"):
                    self.available_models = [
                        (str(o.get("name") or o.get("value")), str(o.get("value")))
                        for o in opt["options"] if isinstance(o, dict) and o.get("value")
                    ]
            elif oid == "thought_level" and val:
                self.effort = str(val)
            elif oid == "mode" and val:
                self.current_mode = str(val)

    def _flush_user(self) -> None:
        if self.user_buf:
            text, self.user_buf = self.user_buf, ""
            if text.strip():
                self.b.user(text)

    def _flush_cur(self) -> None:
        if self.cur_kind and self.cur_buf:
            kind, key, text = self.cur_kind, self.cur_key, self.cur_buf
            self.cur_kind, self.cur_key, self.cur_buf = "", "", ""
            if text.strip():
                self.b.msg(key, [{"kind": kind, "text": text.strip()}])
        else:
            self.cur_kind, self.cur_key, self.cur_buf = "", "", ""

    def _sync_session_prefs(self) -> None:
        """Apply saved or default model, effort, and mode right after session/new or session/load."""
        if not self.conv:
            return
        cur_model = (self.config_options.get("model") or {}).get("currentValue") or ""
        if self.model and self.model != cur_model:
            def on_model(res, err):
                if not err and isinstance(res, dict):
                    self._apply_config_options(res.get("configOptions"))
                    self.bar()
            self.request("session/set_config_option",
                         {"sessionId": self.conv, "configId": "model", "value": self.model}, on_model)
        cur_effort = (self.config_options.get("thought_level") or {}).get("currentValue") or ""
        want_effort = self.effort
        if not want_effort and (m := EFFORT_RE.search(self.model)):
            want_effort = m[1]
        if want_effort and want_effort != cur_effort:
            avail = {str(o.get("value")) for o in (self.config_options.get("thought_level") or {}).get("options") or []
                     if isinstance(o, dict)}
            if not avail or want_effort in avail:
                def on_effort(res, err):
                    if not err and isinstance(res, dict):
                        self._apply_config_options(res.get("configOptions"))
                        self.bar()
                self.request("session/set_config_option",
                             {"sessionId": self.conv, "configId": "thought_level", "value": want_effort}, on_effort)
        want_mode = self.mode if self.mode in ("accept-edits", "auto", "plan") else ("" if self.yolo else "default")
        if want_mode and want_mode != self.current_mode:
            def on_mode(_res, err):
                if not err:
                    self.current_mode = want_mode
                    self.bar()
            self.request("session/set_mode", {"sessionId": self.conv, "modeId": want_mode}, on_mode)

    def session_ready(self, res: dict | None, err: dict | None) -> None:
        self.replaying = False
        self._flush_user()
        self._flush_cur()
        if err or not isinstance(res, dict):
            if self.resume and self.conv:
                # Stale or missing conversation ID: fall back to a fresh session.
                self.conv = ""
                self.resume = False
                self.request("session/new", {"cwd": self.cwd, "mcpServers": [], **self._meta()}, self.session_ready)
                return
            msg = (err or {}).get("message") if isinstance(err, dict) else "Failed to start agy ACP session"
            self.b.note(str(msg), err=True)
            self.b.state("your_turn")
            return
        sid = res.get("sessionId") or self.conv
        if sid:
            self.conv = str(sid)
            self.b.save(resume=self.conv)
        models = (res.get("models") or {}).get("availableModels") or []
        if models:
            self.available_models = [
                (str(m.get("name") or m.get("modelId")), str(m.get("modelId")))
                for m in models if isinstance(m, dict) and m.get("modelId")
            ]
        modes = res.get("modes") or {}
        if modes.get("currentModeId"):
            self.current_mode = str(modes["currentModeId"])
        # Preserve caller-requested model/effort before reading server defaults, then sync if needed.
        want_model, want_effort = self.model, self.effort
        self._apply_config_options(res.get("configOptions"))
        if want_model:
            self.model = want_model
        if want_effort:
            self.effort = want_effort
        self._sync_session_prefs()
        self.ready = True
        self.bar()
        self.usage()
        self.b.state("your_turn")
        queued, self.queue = self.queue, []
        for text in queued:
            self.command({"t": "say", "text": text})

    def started(self, res: dict | None, err: dict | None) -> None:
        if err:
            self.b.note(str(err.get("message") or "agy ACP initialize failed"), err=True)
            self.b.state("your_turn")
            return
        if self.resume and not self.conv:
            convs = self.conversations()
            self.conv = convs[0][1] if convs else ""
        if self.resume and self.conv:
            self.replaying = True
            self.request("session/load", {"sessionId": self.conv, "cwd": self.cwd, "mcpServers": [], **self._meta()},
                         self.session_ready)
        else:
            self.request("session/new", {"cwd": self.cwd, "mcpServers": [], **self._meta()}, self.session_ready)

    def handle(self, c: dict) -> None:
        text = str(c.get("text") or "").strip()
        command = text.split(maxsplit=1)[0] if text else ""
        if c.get("t") == "say" and command in INFO_COMMANDS:
            self.b.user(text)
            try:
                out = subprocess.run([*util.resolve("agy"), "-p", text, "--output-format", "json",
                                      *(["--model", self.model] if self.model else [])], capture_output=True,
                                     text=True, encoding="utf-8", errors="replace", timeout=30, cwd=self.cwd)
                result = json.loads(out.stdout)
                if result.get("status") == "ERROR":
                    raise ValueError(result.get("error") or "agy command failed")
                response = result.get("response") or json.dumps((result.get("command") or {}).get("data") or {},
                                                               ensure_ascii=False, indent=2)
                self.b.msg(f"command-{time.monotonic_ns()}", [{"kind": "text", "text": response.strip()}])
            except (OSError, subprocess.SubprocessError, ValueError) as e:
                self.b.note(f"agy {command}: {e}", err=True)
            return
        super().handle(c)

    def _send_prompt(self, text: str, *, show_user: bool = True) -> None:
        if show_user:
            self.b.user(text)
        self.active_turns += 1
        self.working(True)

        def on_turn(res, err):
            self._flush_cur()
            self.active_turns = max(0, self.active_turns - 1)
            if err:
                msg = err.get("message") if isinstance(err, dict) else str(err)
                self.b.note(str(msg or "agy turn failed"), err=True)
                for qid, _ in self.b.waiting:
                    self.b.emit({"t": "unqueued", "id": qid})
                self.b.waiting = []
            elif isinstance(res, dict):
                stop = res.get("stopReason")
                if stop == "cancelled":
                    self.b.note("Stopped")
                elif stop and stop not in ("end_turn", "max_tokens", "refusal"):
                    self.b.note(f"agy stopped: {stop}")
            if self.active_turns == 0 and not self.quiz and not self.asks:
                self.working(False)
            self.usage()

        self.request("session/prompt", {"sessionId": self.conv, "prompt": [{"type": "text", "text": text}]}, on_turn)

    def command(self, c: dict) -> None:
        t = c.get("t")
        if t == "say" and str(c.get("text", "")).strip():
            text = str(c["text"])
            if not self.ready or not self.conv:
                self.queue.append(text)
                return
            self._send_prompt(text)
        elif t == "abort" and self.busy and self.conv:
            self.send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self.conv}})
        elif t == "answer" and c.get("id") in self.asks:
            req_id, opt_map = self.asks.pop(c["id"])
            val = c.get("value")
            if val == "Allow all for session":
                self.allow_all = True
                opt_id = opt_map.get("Allow all for session")
            else:
                opt_id = opt_map.get(val)
            outcome = {"outcome": "selected", "optionId": opt_id} if opt_id is not None else {"outcome": "cancelled"}
            self.send({"jsonrpc": "2.0", "id": req_id, "result": {"outcome": outcome}})
            self.b.answered(c["id"], value=val)
            self.b.state("working" if self.active_turns > 0 else "your_turn")

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
                try:
                    when = re.sub(r"(\.\d{6})\d+", lambda m: m[1], str(when))
                    when = datetime.fromisoformat(when).astimezone().strftime("%b %d %H:%M")
                except (ValueError, OSError, OverflowError):
                    when = str(when)[:16]
                out.append((f"{when}  {title or preview or cid}", cid))
        return out

    def slash_resume(self, arg: str) -> None:
        if not self.ready:
            super().slash_resume(arg)
            return

        def then(res, err):
            convs = []
            if not err and isinstance(res, dict):
                here = os.path.normcase(os.path.normpath(self.cwd))
                for s in res.get("sessions") or []:
                    cid = s.get("sessionId")
                    if not cid or cid == self.conv:
                        continue
                    scwd = s.get("cwd") or ""
                    if scwd and os.path.normcase(os.path.normpath(scwd)) != here:
                        continue
                    title = s.get("title") or cid
                    convs.append((str(title), str(cid)))
            if not convs:
                convs = self.conversations()
            if arg:
                convs = [c for c in convs if arg.lower() in c[0].lower() or arg == c[1]]
            self.pick("Resume which conversation?", convs[:12], self.switch)

        self.request("session/list", {"cwd": self.cwd}, then)

    def slash_mode(self, arg: str) -> None:
        modes = [("Ask before tools", "ask"), ("Accept edits", "accept-edits"),
                 ("Auto approve tools", "auto"), ("Plan", "plan")]
        if arg not in {value for _, value in modes}:
            return self.pick("Agent mode", modes, self.slash_mode)
        was_yolo = self.yolo
        self.mode = "" if arg == "ask" else arg
        self.yolo = arg == "auto"
        self.allow_all = False
        self.b.pref(mode=self.mode, yolo=self.yolo)
        # Switching out of --dangerously-skip-permissions requires restarting agy acp without the flag.
        if was_yolo and not self.yolo:
            self.bar()
            self.b.note(f"Mode: {arg}")
            self.restart()
            return
        mode_id = "default" if arg == "ask" else arg
        self.current_mode = mode_id
        if self.ready and self.conv:
            def on_mode(_res, err):
                if err:
                    self.b.note(f"Couldn't switch mode: {err.get('message') or err}", err=True)
                self.bar()
            self.request("session/set_mode", {"sessionId": self.conv, "modeId": mode_id}, on_mode)
        self.bar()
        self.b.note(f"Mode: {arg}")

    def slash_compact(self, arg: str) -> None:
        if self.busy:
            self.b.note("Stop the current turn before compacting")
            return
        if not self.ready or not self.conv:
            self.b.note("Nothing to compact yet")
            return
        self.b.note("Compacting the conversation…")
        self._send_prompt(f"/compact {arg}".strip(), show_user=False)

    def slash_effort(self, arg: str) -> None:
        opt = self.config_options.get("thought_level") or {}
        levels = [str(o.get("value")) for o in opt.get("options") or [] if isinstance(o, dict) and o.get("value")]
        if not levels:
            if self.menu is not None:
                self.b.emit({"t": "menu", "rid": self.menu, "options": [],
                             "message": "This model has fixed reasoning effort"})
                self.menu = None
            else:
                self.b.note("This model has fixed reasoning effort")
            return
        if arg in levels:
            self.set_effort(arg)
        else:
            if arg:
                self.b.note(f"Effort must be one of: {', '.join(levels)}", err=True)
            labels = [(str(o.get("name") or o.get("value")), str(o.get("value")))
                      for o in opt.get("options") or [] if isinstance(o, dict) and o.get("value")]
            self.pick("Reasoning effort", labels, self.set_effort)

    def set_effort(self, effort: str) -> None:
        self.effort = effort
        self.b.pref(effort=effort)
        if self.ready and self.conv:
            def on_effort(res, err):
                if err:
                    self.b.note(f"Couldn't set effort: {err.get('message') or err}", err=True)
                elif isinstance(res, dict):
                    self._apply_config_options(res.get("configOptions"))
                self.bar()
            self.request("session/set_config_option",
                         {"sessionId": self.conv, "configId": "thought_level", "value": effort}, on_effort)
        self.bar()
        self.b.note(f"Effort: {effort}")

    def slash_model(self, arg: str) -> None:
        models = list(self.available_models)
        if not models:
            try:
                out = subprocess.run([*util.resolve("agy"), "models"], capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", timeout=60).stdout
                models = [(name.strip() or mid.strip(), mid.strip()) for mid, _, name in
                          (line.partition("\t") for line in out.splitlines()) if mid.strip()]
            except (OSError, subprocess.TimeoutExpired) as e:
                self.b.note(f"Couldn't list agy's models: {e}", err=True)
                return
        hits = [m for m in models if arg.lower() in f"{m[0]} {m[1]}".lower()] if arg else models
        if arg and len(hits) == 1:
            self.set_model(hits[0][1])
        else:
            self.pick("Which model?", hits or models, self.set_model)

    def set_model(self, model: str) -> None:
        self.model, self.limits_at = model, 0.0
        self.b.pref(model=model)
        if self.ready and self.conv:
            def on_model(res, err):
                if err:
                    self.b.note(f"Couldn't set model: {err.get('message') or err}", err=True)
                elif isinstance(res, dict):
                    self._apply_config_options(res.get("configOptions"))
                self.bar()
            self.request("session/set_config_option",
                         {"sessionId": self.conv, "configId": "model", "value": model}, on_model)
        self.bar()
        self.usage()
        self.b.note(f"Model: {model}")

    def restart(self) -> None:
        self.restarting = True
        self.kill()

    def bar(self) -> None:
        parts = [self.model or "agy default"]
        effort = self.effort
        if not effort and (m := EFFORT_RE.search(self.model)):
            effort = m[1]
        if effort:
            parts.append(effort)
        if self.ctx_size and self.ctx_used is not None:
            parts.append(f"Context {round(100 * self.ctx_used / self.ctx_size)}% used")
        parts.extend(self.limits)
        mode = "yolo" if self.yolo else self.mode or self.current_mode or "ask"
        if mode == "default":
            mode = "ask"
        self.b.bar(*parts, mode=mode)

    def usage(self) -> None:
        """Plan limits from agy's /usage (5h and weekly quota chips), at most once a minute."""
        if time.time() - self.limits_at < 60:
            return
        self.limits_at = time.time()

        def go():
            try:
                out = subprocess.run([*util.resolve("agy"), "-p", "/usage", "--output-format", "json"], capture_output=True,
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

    def _on_update(self, u: dict) -> None:
        kind = u.get("sessionUpdate")
        if kind == "user_message_chunk":
            self._flush_cur()
            c = u.get("content") or {}
            if c.get("type") == "text":
                self.user_buf += str(c.get("text") or "")
            return
        self._flush_user()
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            c = u.get("content") or {}
            if c.get("type") != "text":
                return
            d = str(c.get("text") or "")
            if not d:
                return
            bkind = "think" if kind == "agent_thought_chunk" else "text"
            if self.cur_kind != bkind:
                self._flush_cur()
                self.seq += 1
                self.cur_kind, self.cur_key, self.cur_buf = bkind, f"agy-{self.seq}", ""
            self.cur_buf += d
            if not self.replaying:
                self.b.delta(self.cur_key, 0, bkind, d)
        elif kind in ("tool_call", "tool_call_update"):
            self._flush_cur()
            tid = str(u.get("toolCallId") or f"tool-{self.seq}")
            prev = self.tools.get(tid) or {}
            name = str(u.get("name") or prev.get("name") or u.get("title") or "tool")
            args = u.get("rawInput") or u.get("args") or prev.get("args") or {}
            if not isinstance(args, dict):
                args = {"value": args}
            self.tools[tid] = {"name": name, "args": args}
            status = str(u.get("status") or "in_progress")
            done = status in ("completed", "failed", "cancelled")
            self.b.msg(f"tool-{tid}", [{"kind": "tool", "id": tid, "name": name, "args": args}], final=done)
            if done:
                out = _tool_output(u)
                self.b.tool_end(tid, out, err=(status != "completed"))
        elif kind == "usage_update":
            if u.get("used") is not None:
                self.ctx_used = int(u["used"])
            if u.get("size") is not None:
                self.ctx_size = int(u["size"])
            self.bar()
        elif kind == "session_info_update":
            if u.get("title"):
                self.b.name(str(u["title"]))
        elif kind == "current_mode_update":
            if u.get("currentModeId"):
                self.current_mode = str(u["currentModeId"])
                self.bar()
        elif kind == "config_option_update":
            self._apply_config_options(u.get("configOptions"))
            self.bar()
        elif kind == "available_commands_update":
            pass
        else:
            self.b.hidden(f"agy acp {kind}", u)

    def _on_server_request(self, req_id, method: str, params: dict) -> None:
        if method == "session/request_permission":
            tc = params.get("toolCall") or {}
            opts = [o for o in params.get("options") or [] if isinstance(o, dict) and o.get("optionId")]
            allow_opt = next((o["optionId"] for o in opts if "allow" in str(o.get("kind") or o.get("optionId")).lower()),
                             opts[0]["optionId"] if opts else None)
            if (self.yolo or self.allow_all) and allow_opt is not None:
                self.send({"jsonrpc": "2.0", "id": req_id,
                           "result": {"outcome": {"outcome": "selected", "optionId": allow_opt}}})
                return
            name = tc.get("name") or "tool"
            title = tc.get("title") or name
            args = tc.get("rawInput") or tc.get("args") or {}
            detail = args.get("CommandLine") or args.get("Path") or args.get("Url") or ""
            prompt = f"Allow {name}? {detail or title}".strip()
            labels, opt_map = [], {}
            for o in opts:
                label = str(o.get("name") or o["optionId"])
                labels.append(label)
                opt_map[label] = o["optionId"]
            if allow_opt is not None and "Allow all for session" not in opt_map:
                labels.append("Allow all for session")
                opt_map["Allow all for session"] = allow_opt
            aid = f"acp-perm-{req_id}"
            self.asks[aid] = (req_id, opt_map)
            self.b.ask(aid, prompt, labels)
        elif method == "elicitation/create":
            specs = _parse_elicitation(params.get("requestedSchema") or {})
            if not specs:
                self.send({"jsonrpc": "2.0", "id": req_id, "result": {"action": "decline"}})
                return

            def on_answers(answers):
                if answers is None:
                    self.send({"jsonrpc": "2.0", "id": req_id, "result": {"action": "decline"}})
                    return
                content = {}
                for spec, ans in zip(specs, answers):
                    cmap = spec["map"]
                    if spec["multi"]:
                        picked = [cmap[x] for x in ans if x in cmap]
                        extra = [x for x in ans if x not in cmap]
                        if picked:
                            content[spec["key"]] = picked
                        if extra and spec["other_key"]:
                            content[spec["other_key"]] = ", ".join(extra)
                    else:
                        val = ans[0] if ans else ""
                        if val in cmap:
                            content[spec["key"]] = cmap[val]
                        elif val and spec["other_key"]:
                            content[spec["other_key"]] = val
                        elif val:
                            content[spec["key"]] = val
                self.send({"jsonrpc": "2.0", "id": req_id, "result": {"action": "accept", "content": content}})

            qs = [{"question": s["question"], "options": s["options"], "multi": s["multi"], "other": s["other"]}
                  for s in specs]
            self.questions(f"acp-elicit-{req_id}", qs, on_answers)
        else:
            self.send({"jsonrpc": "2.0", "id": req_id,
                       "error": {"code": -32601, "message": f"Unsupported client method: {method}"}})

    def event(self, msg: dict) -> None:
        if "method" in msg and "id" in msg:
            self._on_server_request(msg["id"], str(msg["method"]), msg.get("params") or {})
        elif "id" in msg:
            with self.nlock:
                then = self.pending.pop(msg["id"], None)
            if then:
                then(msg.get("result"), msg.get("error"))
        elif msg.get("method") == "session/update":
            upd = (msg.get("params") or {}).get("update")
            if isinstance(upd, dict):
                self._on_update(upd)
        else:
            self.b.hidden("agy acp event", msg)

    def _init_acp(self) -> None:
        self.ready = False
        self.active_turns = 0
        self.asks = {}
        self.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"elicitation": {"form": {}}},
            "clientInfo": {"name": "deck", "version": "1.0"},
        }, self.started)

    def run(self, first: str) -> int:
        if first:
            self.queue.append(first)
        self.bar()
        self._init_acp()
        while True:
            for msg in util.jsonl(self.p.stdout):
                self.event(msg)
            code = self.p.wait()
            if not self.restarting:
                return code
            self.restarting = False
            self._flush_cur()
            self.working(False)
            self.resume = bool(self.conv)
            self.spawn()
            self._init_acp()
