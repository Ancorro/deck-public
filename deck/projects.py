"""A folder's project: the name that is the same for it on every host (its git origin, else its place under home).
Shelf links marked main for a project show atop the chat of every session working in it."""
import os
import re
import subprocess
import time
from pathlib import Path

from . import config

_projects: dict[str, tuple[float, str]] = {}  # folder -> (when worked out, project)


def project_of(folder: str) -> str:
    """A folder's project, the same name on every host, clone and worktree: its git origin as host/owner/repo
    (github.com/ancorro/deck); no remote: its repo root (else the folder) under home as ~/...; "" for home itself and
    the default work folder. Its main page is a shelf link marked main for it (deck/shelf.py). Cached a minute."""
    if not folder:
        return ""
    hit = _projects.get(folder)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    native, key = config.win_path(os.path.expanduser(folder)), ""
    try:
        r = subprocess.run(["git", "-C", native, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=5)
        url = r.stdout.strip() if r.returncode == 0 else ""
        m = re.match(r"^(?:[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/|[\w.-]+@([^:/]+):)(.+?)(?:\.git)?/*$", url, re.I)
        if m:
            key = f"{m[1] or m[2]}/{m[3]}".lower()
        else:
            r = subprocess.run(["git", "-C", native, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
            root = Path(r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else native)
            if root.is_relative_to(config.HOME) and root.resolve() not in (config.HOME.resolve(), Path(config.WORK).resolve()):
                key = "~/" + root.relative_to(config.HOME).as_posix()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    _projects[folder] = (time.time(), key)
    return key
