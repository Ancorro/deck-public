"""The adapter every agent's adapter builds on: the page's commands (messages, slash commands, answers to
questions), question cards, picks, and switching conversations."""
import json
import re
import subprocess
import threading
import time
from datetime import datetime

from . import util
from .bridge import AGENT_FROM, STAMP, Bridge


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
        if isinstance(v, dict):
            z["answers"].append([str(v["other"]).strip()])
        else:
            z["answers"].append([str(x) for x in v] if isinstance(v, list) else [str(v)])
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
        util.kill_tree(self.p)

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
