"""Agent and tool logins on this host (Settings → General → Logins, and the setup's login step): whether each installed CLI
(command-line tool) is signed in, by its own read-only status command, and a terminal tab that runs its own login
(user 2026-11-08: "an easy way to new CLI users to log in, they are not used to cli"; "part of a set up sequence, not
a script"; "also in settings"). Deck never sees or stores the login itself: each tool keeps its own."""
import concurrent.futures
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import config

# Each tool: its name in the page, how to find it, its status command (exit 0 = signed in, read-only: it never starts a
# login or changes one), its login command (run in a Deck terminal tab; each prints a sign-in link the tab makes
# clickable) and, for the page, what the login is for. Pi signs in inside its own app, so it is not here. agy has no
# login or status command: `agy models` answers only when signed in, and its tab opens agy itself, which asks to sign in
# (user 2026-11-10: add agy to the login list).
TOOLS = {
    "claude": {"name": "Claude Code", "status": ["claude", "auth", "status"], "login": "claude auth login",
               "what": "Anthropic account: Claude Code sessions"},
    "codex": {"name": "Codex", "status": ["codex", "login", "status"], "login": "codex login --device-auth",
              "what": "ChatGPT account: Codex sessions"},
    "opencode": {"name": "OpenCode", "status": ["opencode", "auth", "list"], "login": "opencode auth login",
                 "what": "model providers for OpenCode (its free model needs none)", "optional": True},
    "gh": {"name": "GitHub", "status": ["gh", "auth", "status"],
           "login": "printf 'Y\\n' | gh auth login --hostname github.com --git-protocol https --web",
           "what": "GitHub account: agents push code and open pull requests"},
    "agy": {"name": "agy", "status": ["agy", "models"], "login": "agy",
            "what": "Google account: agy sessions", "optional": True},
    "hf": {"name": "Hugging Face", "status": ["hf", "auth", "whoami"], "login": "hf auth login",
           "what": "Hugging Face account: models and datasets", "optional": True},
}
_cache: dict = {"at": 0.0, "list": []}
TTL = 20  # seconds a check is reused: the page polls while a login tab is open


def find(tool: str) -> str:
    """The tool's program, or "" where it isn't installed. Also the user-level folders a service's PATH may lack."""
    if path := shutil.which(tool):
        return path
    if tool == "agy":
        from .agents import agy_path
        if path := agy_path():
            return path
    for d in (".local/bin", ".opencode/bin", "AppData/Roaming/npm"):
        for name in (tool, f"{tool}.exe", f"{tool}.cmd"):
            if (p := config.HOME / d / name).is_file():
                return str(p)
    return ""


def _signed_in(tool: str, path: str) -> bool:
    argv = [path, *TOOLS[tool]["status"][1:]]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                           stdin=subprocess.DEVNULL, env={**os.environ, "NO_COLOR": "1"})
    except (OSError, subprocess.TimeoutExpired):
        return False
    if tool == "opencode":  # always exits 0: signed in when it lists a credential ("N credentials", N > 0)
        return r.returncode == 0 and any(w.isdigit() and int(w) > 0 for line in r.stdout.splitlines()
                                         if "credential" in line for w in line.split())
    if tool == "agy":  # no status command: its model list comes back only when signed in
        return r.returncode == 0 and "sign in" not in (r.stdout + r.stderr).lower() and "	" in r.stdout
    return r.returncode == 0


def status(fresh: bool = False) -> list[dict]:
    """[{tool, name, what, installed, signedin, optional}] for every tool installed here, checked in parallel."""
    if not fresh and time.time() - _cache["at"] < TTL:
        return _cache["list"]
    found = {t: find(t) for t in TOOLS}
    here = [t for t in TOOLS if found[t]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(here) or 1) as pool:
        signed = dict(zip(here, pool.map(lambda t: _signed_in(t, found[t]), here)))
    out = [{"tool": t, "name": TOOLS[t]["name"], "what": TOOLS[t]["what"], "optional": bool(TOOLS[t].get("optional")),
            "signedin": signed[t]} for t in here]
    _cache.update(at=time.time(), list=out)
    return out


def login_key(tool: str) -> dict:
    """The script key a login tab runs: the tool's own login, then its status, in the home folder."""
    if tool not in TOOLS:
        raise ValueError(f"unknown tool: {tool}")
    if not find(tool):
        raise ValueError(f"{TOOLS[tool]['name']} isn't installed on {config.HOST_NAME}")
    # Never over a working login: some tools drop the saved one as their login starts (Codex removes auth.json)
    if _signed_in(tool, find(tool)):
        raise ValueError(f"{TOOLS[tool]['name']} is already signed in on {config.HOST_NAME}")
    _cache["at"] = 0.0  # the next check is fresh
    done = f"echo; {' '.join(TOOLS[tool]['status'])} >/dev/null 2>&1 && echo 'Signed in. You can close this tab.' " \
           f"|| echo 'Not signed in yet: run it again from Settings → General → Logins.'"
    return {"id": f"login-{tool}", "label": f"{TOOLS[tool]['name']} login", "type": "shell",
            "cwd": str(Path.home()), "text": f"{TOOLS[tool]['login']}; {done}"}
