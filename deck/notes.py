"""Machine notes, synced peer to peer with no master.

Every Deck host keeps a full git copy of the notes (core.NOTES) and serves it at <deck_url>/notes.git.
Peers are the other hosts in the notes' own inventory.toml (entries with deck_url). A commit pushes to every
reachable peer (post-commit hook); a session start pulls from them. A host that is down is skipped and catches
up later. Git's commit hashes chain the history, so missing or extra changes show up in status().
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from . import core

TIMEOUT = 15
HOOK = """#!/bin/sh
# Deck notes: push each commit to every reachable peer, in the background so the commit never waits.
command -v deck-notes >/dev/null 2>&1 && (deck-notes push -q >/dev/null 2>&1 &)
exit 0
"""
TEXT = {".md", ".toml", ".json", ".txt", ".sh", ".ps1", ".py", ".yml", ".yaml", ""}


def git(*args: str, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(core.NOTES), *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "timed out")


def head() -> str:
    return git("rev-parse", "--short", "HEAD").stdout.strip()


def branch() -> str:
    return git("symbolic-ref", "--short", "HEAD").stdout.strip() or "main"


def peers() -> list[tuple[str, str]]:
    """(name, git URL) for the other Deck hosts, plus the repo's own `origin` remote if it has one."""
    out = [(h["name"], h["url"].rstrip("/") + "/notes.git") for h in core.hosts()["hosts"] if not h["current"]]
    if git("remote", "get-url", "origin").returncode == 0:
        out.append(("origin", "origin"))
    return out


def init() -> str:
    """Make the notes folder a repo this Deck can serve and receive pushes into. Idempotent."""
    core.NOTES.mkdir(parents=True, exist_ok=True)
    if git("rev-parse", "--git-dir").returncode:
        git("init", "-q", "-b", "main")
        git("commit", "-q", "--allow-empty", "-m", "Notes start")
    git("config", "http.receivepack", "true")
    git("config", "receive.denyCurrentBranch", "updateInstead")  # a push updates the files here when they are clean
    hook = core.NOTES / ".git" / "hooks" / "post-commit"
    hook.write_text(HOOK)
    hook.chmod(0o755)
    return str(core.NOTES)


def clean() -> bool:
    return not git("status", "--porcelain").stdout.strip()


def host_auth(url: str) -> list[str]:
    """Peer Decks only let the user's other hosts in: send this host's shared key (auth.host_key)."""
    from . import auth
    key = auth.host_key()
    return ["-c", f"http.extraHeader=Authorization: Bearer {key}"] if key and url.startswith("http") else []


def fetch(name: str, url: str) -> bool:
    return git(*host_auth(url), "fetch", "-q", url, f"+HEAD:refs/peers/{name}").returncode == 0


def pull(quiet: bool = False) -> list[str]:
    """Fetch every reachable peer and merge what it has. Returns problems worth telling the user."""
    problems = []
    for name, url in peers():
        if not fetch(name, url):
            problems.append(f"{name}: unreachable")
            continue
        if not clean():
            problems.append(f"{name}: local notes have uncommitted edits; not merged")
            continue
        if git("merge", "-q", "--no-edit", f"refs/peers/{name}").returncode:
            git("merge", "--abort")
            problems.append(f"{name}: conflicting edits; merge by hand (git -C {core.NOTES} merge refs/peers/{name})")
    return problems


def push(quiet: bool = False) -> list[str]:
    """Send local commits to every reachable peer; on a rejection, merge that peer first and retry once."""
    problems, ref = [], f"HEAD:refs/heads/{branch()}"
    for name, url in peers():
        if git(*host_auth(url), "push", "-q", url, ref).returncode == 0:
            continue
        if fetch(name, url) and clean() and git("merge", "-q", "--no-edit", f"refs/peers/{name}").returncode == 0:
            if git(*host_auth(url), "push", "-q", url, ref).returncode == 0:
                continue
        else:
            git("merge", "--abort")
        problems.append(f"{name}: push failed (down, busy with unsaved edits, or conflicting)")
    return problems


def status() -> dict:
    """How every peer's notes compare with ours. state: same | behind (peer lacks ours) | ahead (we lack the
    peer's) | diverged | offline."""
    out = []
    for name, url in peers():
        if not fetch(name, url):
            out.append({"name": name, "state": "offline"})
            continue
        counts = git("rev-list", "--left-right", "--count", f"HEAD...refs/peers/{name}").stdout.split()
        mine, theirs = (int(x) for x in counts) if len(counts) == 2 else (0, 0)
        state = "same" if not (mine or theirs) else "diverged" if mine and theirs else "behind" if mine else "ahead"
        out.append({"name": name, "state": state, "missing_there": mine, "missing_here": theirs})
    return {"path": str(core.NOTES), "head": head(), "clean": clean(), "peers": out}


# ---- reading and editing from the Settings sheet ----

def files() -> list[str]:
    tracked = git("ls-files").stdout.splitlines()
    return sorted(f for f in tracked if Path(f).suffix in TEXT)


def resolve(rel: str) -> Path:
    p = (core.NOTES / rel).resolve()
    if core.NOTES.resolve() not in p.parents or ".git" in p.parts or Path(rel).suffix not in TEXT:
        raise ValueError("not a notes file")
    return p


def read(rel: str) -> str:
    return resolve(rel).read_text()


def write(rel: str, text: str) -> str:
    """Save one file, commit it (the hook pushes it to the peers), return the new head."""
    if not clean():
        raise ValueError("notes have uncommitted edits on this host; commit them first")
    pull()
    p = resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text.endswith("\n") else text + "\n")
    git("add", "--", rel)
    if git("diff", "--cached", "--quiet").returncode == 0:
        return head()  # unchanged
    r = git("commit", "-q", "-m", f"Edit {rel} from Deck ({core.HOST_NAME})")
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "commit failed")
    return head()


# ---- carried by the phone app ----

def push_later() -> None:
    subprocess.Popen([sys.executable, "-m", "deck.notes", "push", "-q"], cwd=Path(__file__).parent.parent,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def carry(bundle: bytes | None, edits: list[dict], device: str = "phone") -> dict:
    """The phone app visits hosts one at a time and carries the notes between them as a git bundle.
    Merge the phone's bundle, apply its offline edits ({path, text, base}: base = the head the edit was made
    on; skipped if the file changed since), push on to reachable peers, and hand back our bundle and files."""
    problems, applied, conflicts, counts = [], [], [], []
    before = git("rev-parse", "HEAD").stdout.strip()
    if bundle:
        # A closed file: Windows won't let git open one this process still holds open.
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "phone.bundle").write_bytes(bundle)
            if git("fetch", "-q", str(Path(d) / "phone.bundle"), "+HEAD:refs/peers/phone").returncode:
                problems.append("phone copy unreadable; replaced with this host's")
            elif not (counts := git("rev-list", "--left-right", "--count", "HEAD...refs/peers/phone").stdout.split()):
                problems.append("phone copy shares no history with this host's notes; not merged")
            elif not clean():
                problems.append("this host has uncommitted note edits; phone copy not merged")
            elif git("merge", "-q", "--no-edit", "refs/peers/phone").returncode:
                git("merge", "--abort")
                problems.append("phone copy conflicts with this host's notes; kept this host's")
    for e in edits or []:
        rel, base = str(e.get("path", "")), str(e.get("base", ""))
        try:
            p = resolve(rel)
        except ValueError:
            conflicts.append(rel)
            continue
        if base and git("cat-file", "-e", base + "^{commit}").returncode == 0 \
                and git("diff", "--quiet", base, "HEAD", "--", rel).returncode:
            conflicts.append(rel)  # changed here since the phone's copy: the phone keeps the edit to redo
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        text = str(e.get("text", ""))
        p.write_text(text if text.endswith("\n") else text + "\n")
        git("add", "--", rel)
        applied.append(rel)
    if applied and git("diff", "--cached", "--quiet").returncode:
        git("commit", "-q", "-m", f"Edit {', '.join(applied)} from {device} (offline)")
    if git("rev-parse", "HEAD").stdout.strip() != before:  # merges skip the post-commit hook
        push_later()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "notes.bundle"
        git("bundle", "create", "-q", str(out), "HEAD")
        data = out.read_bytes() if out.exists() else b""
    # Git's hash chain: commits each side had that the other lacked (phone_had = changes carried in from elsewhere)
    host_had, phone_had = (int(x) for x in counts) if len(counts) == 2 else (0, 0)
    return {"head": head(), "bundle": data, "files": {f: read(f) for f in files()},
            "host_had": host_had, "phone_had": phone_had,
            "applied": applied, "conflicts": conflicts, "problems": problems}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deck-notes", description="Sync machine notes with the other Deck hosts.")
    ap.add_argument("cmd", choices=["init", "pull", "push", "sync", "status"])
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "init":
        print(f"notes ready: {init()}")
        return 0
    if a.cmd == "status":
        s = status()
        if a.json:
            print(json.dumps(s))
        else:
            print(f"{s['path']} @ {s['head']}{'' if s['clean'] else ' (uncommitted edits)'}")
            for p in s["peers"]:
                print(f"  {p['name']}: {p['state']}")
        return 0
    problems = (pull() if a.cmd in ("pull", "sync") else []) + (push() if a.cmd in ("push", "sync") else [])
    for p in problems:
        if not a.quiet or "conflict" in p or "uncommitted" in p:
            print(f"deck-notes: {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
