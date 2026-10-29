"""The bridge's entry point: start the agent's adapter in this pane and run it until it exits; `hook` and
`statusline` are what Claude Code's hooks call."""
import os
import sys
import threading
import time

from . import util
from .agy import Agy
from .bridge import Bridge
from .claude import Claude
from .codex import Codex
from .hooks import hook_main, statusline_main
from .openclaw import OpenClaw
from .opencode import OpenCode
from .pi import Pi
from .util import WINDOWS

ADAPTERS = {"pi": Pi, "claude": Claude, "codex": Codex, "opencode": OpenCode, "agy": Agy, "openclaw": OpenClaw}


def main(argv: list[str]) -> int:
    if argv[:1] == ["hook"]:
        return hook_main(*argv[1:3])
    if argv[:1] == ["statusline"]:
        return statusline_main(*argv[1:3])
    sys.stdout.reconfigure(encoding="ascii" if WINDOWS else "utf-8", errors="replace")  # see util.show()
    util.windows_env()
    agent, folder, session, resume, first = argv[:5]
    args = argv[argv.index("--") + 1:] if "--" in argv else []
    b = Bridge(folder, session)
    import platform
    host = os.environ.get("DECK_HOST_NAME") or platform.node()  # as deck.config names this node (the bridge runs on its own)
    b.save(agent=agent, folder=os.getcwd(), host=host)  # a takeover from the state copy when this node is down (deck/site.py)
    b.emit({"t": "clear", "log": time.time_ns()})  # this log's id: a page reconnecting to it carries on (api/chat.py)
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
