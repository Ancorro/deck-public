"""agy's adapter (Google's Antigravity CLI): print mode, stream-json both ways."""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import util
from .adapter import Adapter
from .util import OUT_LIMIT, WINDOWS


def _clean_tool_out(out: str) -> str:
    """Drop agy's per-step timestamp header and exit-0 boilerplate so tool output reads cleanly."""
    out = re.sub(r"^(?:Created At:[^\n]*\n)?(?:Completed At:[^\n]*\n)?", "", out)
    return re.sub(r"^Command exited with code 0\.(?:\n+(?:Command output:\n)?)?", "", out).strip("\n")


class Agy(Adapter):
    """agy (Google Antigravity CLI) in print mode with stream-json both ways: one {"event": "user"} line per message,
    a turn each; out come step_update events (text deltas, tool calls) and a result per turn. It has no stop message
    and doesn't replay a resumed conversation (its steps are protobuf), so Stop restarts it on the same conversation
    and the bridge keeps its own record of the chat (AGY_LOG) to show again on a Restart or /resume."""
    YOLO = "--dangerously-skip-permissions"

    @property
    def SUMMARIES(self) -> Path:  # not at import: in a Windows pane the home is known only after util.windows_env()
        return Path.home() / ".gemini" / "antigravity-cli" / "conversation_summaries.db"

    MODEL_IN_PROTOCOL = True

    def __init__(self, *a):
        super().__init__(*a)
        self.conv = self.b.prev_resume if self.resume else ""
        self.model = self.b.prefs.get("model") or self.default_model or ""
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
        self.transcript_pos = 0
        self.transcript_tools: dict[int, dict] = {}
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
            # Start on the notes this host has; a sync waits on every peer in turn (an offline one costs its whole
            # timeout), so it runs in the background and its result is copied in for agy's later turns.
            self.copy_notes(notes)

            def sync():
                try:
                    subprocess.run([sys.executable, "-m", "deck.notes", "sync", "-q"],
                                   cwd=util.REPO, capture_output=True, timeout=60)
                except (OSError, subprocess.SubprocessError):
                    return
                self.copy_notes(notes)
            threading.Thread(target=sync, daemon=True).start()
        self.start([*util.resolve("agy"), "--input-format", "stream-json", "--output-format", "stream-json", "-p=",
                    *(["--conversation", self.conv] if self.conv else []), *([self.YOLO] if self.yolo else []),
                    *(["--model", self.model] if self.model else []),
                    *(["--mode", self.mode] if self.mode in ("accept-edits", "auto", "plan") else
                      # Ask before tools: agy 1.5.7 starts a session with no --mode in Auto, and rejects
                      # `--mode default`, so pick Default for this session only (never saved to settings.json)
                      [] if self.yolo else ["--settings", json.dumps({"agentMode": ""})]), *self.args])

    def copy_notes(self, notes: Path) -> None:
        """The machine notes as agy rules in its context folder (the --add-dir that has .agents/rules)."""
        for i, a in enumerate(self.args[:-1]):
            if a == "--add-dir":
                rules = Path(self.args[i + 1]) / ".agents" / "rules"
                if rules.is_dir():
                    host = (os.environ.get("DECK_HOST_NAME") or socket.gethostname()).lower()
                    for name in ("AGENTS.md", f"hosts/{host}.md"):
                        src = notes / name
                        try:
                            if src.exists():
                                (rules / f"infra-{Path(name).name}").write_text(src.read_text(encoding="utf-8"),
                                                                                encoding="utf-8")
                        except OSError:
                            pass

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
                out = subprocess.run([*util.resolve("agy"), "-p", text, "--output-format", "json",
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
                    when = re.sub(r"(\.\d{6})\d+", lambda m: m[1], str(when))  # fromisoformat takes at most microseconds
                    when = datetime.fromisoformat(when).astimezone().strftime("%b %d %H:%M")
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
            out = subprocess.run([*util.resolve("agy"), "-p", command, "--output-format", "json",
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
                           "Answer: " + (", ".join(answers[0]) if answers
                                         else "Question skipped; proceed with available information.")}))
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
            out = subprocess.run([*util.resolve("agy"), "models"], capture_output=True, text=True, encoding="utf-8",
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

    def tool_args(self, step: int | None, params: dict) -> dict:
        """Merge omitted args (Target, Replacement, Content, toolSummary) from agy's transcript_full.jsonl."""
        if not self.conv or step is None:
            return params
        log = self.SUMMARIES.parent / "brain" / self.conv / ".system_generated" / "logs" / "transcript_full.jsonl"
        try:
            with open(log, encoding="utf-8", errors="replace") as f:
                f.seek(self.transcript_pos)
                while line := f.readline():
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        if not line.endswith("\n"):
                            break
                        self.transcript_pos = f.tell()
                        continue
                    self.transcript_pos = f.tell()
                    base = entry.get("step_index")
                    if isinstance(base, int) and isinstance(entry.get("tool_calls"), list):
                        for i, tc in enumerate(entry["tool_calls"], 1):
                            if isinstance(tc, dict):
                                self.transcript_tools[base + i] = tc.get("args") or {}
        except OSError:
            pass
        extra = self.transcript_tools.get(step)
        if isinstance(extra, dict) and all(extra.get(k) == v for k, v in params.items()):
            return {**extra, **params}
        return params

    def event(self, ev: dict) -> None:
        kind = ev.get("event")
        if kind == "init":
            if ev.get("conversation_id") and ev["conversation_id"] != self.conv:
                self.conv = ev["conversation_id"]
                self.transcript_pos = 0
                self.transcript_tools = {}
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
                args = self.tool_args(step, info.get("parameters") or {})
                blocks = [{"kind": "tool", "id": key, "name": info.get("name") or s.get("tool_name") or "tool",
                           "args": args}]
                self.b.msg(key, blocks, final=state != "ACTIVE")
                if state != "ACTIVE":
                    err = state not in ("DONE",) or bool(info.get("error"))
                    out = info.get("output") if info.get("output") is not None else info.get("error") or ""
                    out = _clean_tool_out((out if isinstance(out, str) else json.dumps(out)).replace("\r\n", "\n").strip("\n"))
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
            # agy 1.4.11+: finished_reason says why a turn ended early (omitted when it finished normally).
            # print_timeout comes with status SUCCESS, interrupted with ERROR and error "interrupted": one note each.
            reason = r.get("finished_reason")
            early = {"print_timeout": "print timeout", "interrupted": "interrupted"}.get(reason)
            if early or reason == "error" or r.get("status") not in ("SUCCESS", None):
                for key, text in list(self.text.items()):  # the partial reply, kept as it stands
                    if text.strip():
                        self.b.msg(key, [{"kind": "text", "text": text.strip()}])
                        self.record({"t": "msg", "m": key, "blocks": [{"kind": "text", "text": text.strip()}]})
                self.text = {}
                if early:
                    self.b.note(f"agy stopped early: {early}, the reply may be partial")
                else:
                    self.b.note(r.get("error") or f"agy: {r.get('status') or reason}", err=True)
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
            for ev in util.jsonl(self.p.stdout):
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
