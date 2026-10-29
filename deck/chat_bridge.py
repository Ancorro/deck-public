"""Chat sessions: the bridge between an agent running in a tmux pane and Deck's chat (the package deck/bridge).

This file is what the pane runs (agents.build_argv starts `python chat_bridge.py ...`, and Claude Code's hooks call
`python chat_bridge.py hook|statusline ...`), by path, so it puts the checkout on sys.path itself. Stdlib only, plus
pywinpty for Claude on Windows (the server's Python has it). The package's docstring describes the files and events."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deck.bridge import main as _main
from deck.bridge.adapter import Adapter  # noqa: F401
from deck.bridge.agy import Agy  # noqa: F401
from deck.bridge.bridge import AGENT_FROM, STAMP, Bridge  # noqa: F401
from deck.bridge.claude import Claude  # noqa: F401
from deck.bridge.codex import Codex  # noqa: F401
from deck.bridge.openclaw import OpenClaw  # noqa: F401
from deck.bridge.opencode import OpenCode  # noqa: F401
from deck.bridge.pi import Pi  # noqa: F401
from deck.bridge.util import OUT_LIMIT, WINDOWS, resolve  # noqa: F401

main = _main.main

if __name__ == "__main__":
    code = main(sys.argv[1:])
    sys.stdout.flush()
    os._exit(code)  # not sys.exit: the watch thread is blocked reading stdin, and shutdown would wait on it
