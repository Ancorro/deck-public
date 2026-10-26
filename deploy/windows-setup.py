"""Set up this Windows user for Deck: what install.sh does on Linux, minus the services (the PC app runs Deck).
The PC app runs it with the server's Python on first start and after each app update. Re-runnable.
Prints "STEP <text>" lines, which the app shows as progress.
    python deploy/windows-setup.py <host name>
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
DECK = Path(__file__).resolve().parent.parent
HOST_NAME = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COMPUTERNAME", "pc").lower()
NO_WINDOW = 0x08000000  # no console flashing up for each command


def step(text: str) -> None:
    print(f"STEP {text}", flush=True)


def run(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW, **kw)


def find(exe: str, *extra: Path) -> str | None:
    return shutil.which(exe) or next((str(p) for p in extra if p.exists()), None)


step("Checking Git for Windows")
git = find("git", Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "cmd" / "git.exe")
if not git:  # Claude Code needs Git Bash; the notes are a git repo
    step("Installing Git for Windows (approve the Windows prompt)")
    try:
        run("winget", "install", "--id", "Git.Git", "-e", "--silent", "--accept-source-agreements",
            "--accept-package-agreements")
    except OSError:  # no winget: Deck still runs; Claude Code asks for Git itself
        step("Install Git for Windows (git-scm.com), then restart Deck")
    git = find("git", Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "cmd" / "git.exe")
if git:
    # Deck commits to the notes repo, so git needs an identity: yours if set, else a local one.
    if not run(git, "config", "--global", "user.name").stdout.strip():
        run(git, "config", "--global", "user.name", os.environ.get("USERNAME", "deck"))
    if not run(git, "config", "--global", "user.email").stdout.strip():
        run(git, "config", "--global", "user.email", f"{os.environ.get('USERNAME', 'deck')}@{HOST_NAME}")
    os.environ["PATH"] = str(Path(git).parent) + os.pathsep + os.environ["PATH"]

step("Setting up agents (Claude Code, Codex, Pi)")
local_bin = HOME / ".local" / "bin"
if not find("claude", local_bin / "claude.exe"):
    run("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "irm https://claude.ai/install.ps1 | iex")
npm = find("npm")
if npm and not find("codex", Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"):
    run(npm, "install", "-g", "-s", "@openai/codex")
if not (HOME / ".pi" / "agent" / "bin" / "pi.cmd").exists():
    run("powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
        "irm https://pi.dev/install.ps1 | iex")
step("Setting up durable Pi (experimental)")  # builds pi from source when pi's version changes
run(sys.executable, str(DECK / "deploy" / "agents" / "pi-durable" / "setup.py"))

step("Settings")
data = HOME / ".config" / "deck"
data.mkdir(parents=True, exist_ok=True)
env = data / "env"
lines = env.read_text().splitlines() if env.exists() else []
if not any(l.startswith("DECK_HOST_NAME=") for l in lines):
    env.write_text("\n".join(lines + [f"DECK_HOST_NAME={HOST_NAME}"]) + "\n")
(HOME / "work").mkdir(exist_ok=True)
py = sys.executable
run(py, str(DECK / "deploy" / "agents" / "trust.py"), str(HOME / "work"))
if git:
    run(py, "-m", "deck.notes", "init", cwd=DECK)
run(py, str(DECK / "deploy" / "agents" / "agent_context.py"))
# Not agent-statusline.py: Deck can't match its per-pane files to panes on Windows yet (no /proc), and it would point
# Claude at this Python, which lives in uv's cache.
step("Done")
