"""Where Deck lives on this host: its folders and files, the host's name, the platform it runs on, and the
environment its sessions need. Every module reads these through this module (config.DATA, never a copied name), so
a test can point Deck at a temporary folder by patching one place."""
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

PREFIX = "deck-"  # every tmux session Deck starts is named deck-...
HOME = Path.home()
DATA = Path(os.environ.get("DECK_DATA", HOME / ".config" / "deck"))


def _load_env_file() -> None:
    """The service gets ~/.config/deck/env from systemd; CLI tools (deck-notes, deck-key) read it here."""
    try:
        lines = (DATA / "env").read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        k, sep, v = line.strip().partition("=")
        if sep and k.startswith("DECK_") and k not in os.environ:
            os.environ[k] = v


_load_env_file()
WORK = os.environ.get("DECK_WORK", str(HOME / "work"))  # default folder for agent keys
KEYS_DIR = os.path.join(WORK, "deck-keys")  # where the key-builder agent writes scripts
HOST_NAME = os.environ.get("DECK_HOST_NAME") or platform.node()
# Machine notes: a git repo every Deck host keeps a full copy of and syncs with its peers (deck/notes.py).
NOTES = Path(os.path.expanduser(os.environ.get("DECK_NOTES", str(HOME / "deck-notes"))))
INVENTORY = os.environ.get("DECK_INVENTORY", str(NOTES / "inventory.toml"))
VNC_FILE = os.environ.get("DECK_VNC_FILE", str(DATA / "vnc.json"))  # {"password", "path"}; absent = no Screen tab

# Windows (the PC app, no WSL): sessions run in the MSYS2 tmux and bash the app ships (DECK_MSYS: the kit's root,
# desktop/msys-kit.sh). Agents are the native Windows builds; Claude Code uses Git for Windows' bash for its tools.
WINDOWS = sys.platform == "win32"
TMUX = "tmux"
BASH = "bash"
if WINDOWS:
    _kit = Path(os.environ.get("DECK_MSYS", Path(__file__).parent.parent / "msys")) / "usr" / "bin"
    TMUX = str(_kit / "tmux.exe")
    # The kit's own bash, by path: PATH finds Git for Windows' first, whose MSYS runtime can't see this tmux's
    # terminal, so a pane's shell got pipes and typed input never reached it.
    BASH = (_kit / "bash.exe").as_posix()
    _local = os.environ.get("LOCALAPPDATA", str(HOME))
    _path = [str(_kit), str(HOME / ".local" / "bin"), os.path.join(os.environ.get("APPDATA", ""), "npm"),
             str(HOME / ".pi" / "agent" / "bin")]
    # `uv run --with` puts throwaway cache venvs first on PATH; agents' `python` then had no packages and lost installs
    _inherited = [p for p in os.environ.get("PATH", "").split(os.pathsep)
                  if p and not re.search(r"[\\/]uv[\\/]cache[\\/]|[\\/]deck[\\/]server[\\/]\.venv", p, re.I)]
    os.environ.pop("VIRTUAL_ENV", None)
    os.environ.update({
        "PATH": os.pathsep.join([p for p in _path if p not in _inherited]
                                + _inherited),  # a restarted server inherits the last one's PATH
        "HOME": str(HOME),  # MSYS2's ~ would otherwise be inside the kit
        "TMUX_TMPDIR": os.path.join(_local, "deck", "tmux"),  # the kit may be read-only (Program Files)
        "MSYS": "noglob",  # MSYS programs started from Windows would glob and brace-expand their arguments
        "MSYS2_PATH_TYPE": "inherit",  # login bash keeps Windows' PATH (where the agents are)
        "CHERE_INVOKING": "1",  # and stays in the session's folder
        "LANG": os.environ.get("LANG") or "C.UTF-8",  # else tmux treats the agents' output as ASCII
    })
    os.makedirs(os.environ["TMUX_TMPDIR"], exist_ok=True)
    _git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
    if _git_bash.exists():
        os.environ.setdefault("CLAUDE_CODE_GIT_BASH_PATH", str(_git_bash))
# Windows: tmux gets its own (hidden) console. Sharing Deck's would let a Ctrl+C sent in a pane stop Deck itself.
OWN_CONSOLE = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
CHAT_DIR = DATA / "chat"  # chat sessions' event logs, command queues and state (deck/chat_bridge.py)


# The server: its address and ports, and what the environment adds
HOST = os.environ.get("DECK_HOST", "127.0.0.1")  # tailscale serve fronts it; never bind the LAN
PORT = int(os.environ.get("DECK_PORT", "8787"))
# deck-iroh (tunnel/) forwards here: requests on this port come from the internet, so they're never "local" and always
# need a paired device, even before the first one is approved.
TUNNEL_PORT = int(os.environ.get("DECK_TUNNEL_PORT", str(PORT + 1)))
# Local speech-to-text for the browser mic (POST audio file -> {"text"}, e.g. WhisperWriter's /transcribe); unset =
# browsers use their own speech recognition.
TRANSCRIBE_URL = os.environ.get("DECK_TRANSCRIBE_URL", "").strip()
IROH_ID = HOME / ".config/deck/iroh.id"  # this host's iroh key, while deck-iroh runs (pairing over iroh)
REPO = Path(__file__).resolve().parent.parent  # this checkout (the update route pulls it)


def tmux_path(path) -> str:
    """A folder as tmux takes it: on Windows the MSYS form (C:\\Users\\x -> /c/Users/x); tmux ignores a drive path."""
    path = Path(path).as_posix()
    return f"/{path[0].lower()}{path[2:]}" if WINDOWS and re.match(r"^[a-zA-Z]:/", path) else path


def win_path(path: str) -> str:
    """An MSYS path (/c/Users/...) as Windows writes it (C:/Users/...); other paths unchanged."""
    m = re.match(r"^/([a-zA-Z])(/|$)", path) if WINDOWS else None
    return f"{m.group(1).upper()}:/{path[3:]}" if m else path


def settings_path() -> Path:
    return DATA / "settings.json"


def buttons_path() -> Path:
    return DATA / "buttons.json"


def recent_path() -> Path:
    return DATA / "recent.json"


def run_log(name: str) -> Path:
    """Where a script key's output is kept (its pane pipes it there)."""
    return DATA / "runs" / f"{name}.log"
