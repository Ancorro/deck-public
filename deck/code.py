"""The Code view's files (user 2026-11-14: "a 'code' tab, looks like review but with text editor, and shelf is replaced
with a file tree"): the folder a session works in, listed one level at a time, and its text files read and saved.

Roots: a session's folder (the one its agent reported with PUT /api/sessions/<name>/project, else the one it started
in), or any folder on this host typed into the tree's path box (/api/code/list?root=...). Deck already gives whoever
gets in a shell here, so a folder outside the session's is no new reach; what this module guards is that a path stays
under the root it was asked for (no "..", no links out), so a typo or a crafted path can't walk elsewhere unasked.

Files come back as JSON text only, never as raw bytes from Deck's origin: an HTML or SVG file served from here would
run with the page's rights (a shell). Saves name the version they started from (sha256 of the bytes read) and are
refused when the file changed since (an agent wrote it), so an edit never silently overwrites an agent's."""
import hashlib
import os
import stat
from pathlib import Path

from . import config

MAX_BYTES = 2_000_000  # a text file bigger than this opens read-only as "too big" (the editor's limit, not disk's)
MAX_ENTRIES = 2000  # one folder's listing; the rest is counted, not sent
# Folders that are build output or caches: listed (so they're visible) but marked, and the tree leaves them shut
HEAVY = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache",
         "target", "dist", "build", ".next", ".cache", ".gradle", ".idea", ".tox"}


def native(path: str) -> Path:
    """A folder as this OS names it: ~ expanded, an MSYS path (/c/Users/...) as C:/Users/... on Windows."""
    return Path(config.win_path(os.path.expanduser(path.strip())))


def under(root: Path, rel: str) -> Path:
    """root/rel, refused if it leaves root (".." or a link that points out). rel uses / on every OS."""
    base = root.resolve()
    p = (base / rel.replace("\\", "/").lstrip("/")).resolve() if rel else base
    if p != base and not p.is_relative_to(base):
        raise ValueError("that path is outside the folder")
    return p


def check_root(path: str) -> Path:
    if not path.strip():
        raise ValueError("no folder")
    root = native(path)
    if not root.is_absolute():
        raise ValueError(f"not a full path: {path}")
    if not root.is_dir():
        raise FileNotFoundError(f"folder not found: {root}")
    return root


def listing(root: str, rel: str = "") -> dict:
    """One folder's entries: folders first, then files, by name (case-insensitive); hidden ones included (an agent's
    .claude, .github), heavy ones marked."""
    r = check_root(root)
    d = under(r, rel)
    if not d.is_dir():
        raise FileNotFoundError(f"not a folder: {rel}")
    entries, more = [], 0
    with os.scandir(d) as it:
        items = sorted(it, key=lambda e: (not _is_dir(e), e.name.lower()))
    for e in items:
        if len(entries) >= MAX_ENTRIES:
            more += 1
            continue
        is_dir = _is_dir(e)
        row = {"name": e.name, "dir": is_dir}
        if is_dir and e.name in HEAVY:
            row["heavy"] = True
        if not is_dir:
            try:
                row["size"] = e.stat().st_size
            except OSError:
                pass
        if e.is_symlink():
            row["link"] = True
        entries.append(row)
    return {"root": r.resolve().as_posix(), "path": d.relative_to(r.resolve()).as_posix() if d != r.resolve() else "",
            "entries": entries, "more": more}


def _is_dir(e: os.DirEntry) -> bool:
    try:
        return e.is_dir()
    except OSError:
        return False


def version(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:20]


def read(root: str, rel: str) -> dict:
    """A text file: {path, text, version, mtime, size, eol}; {binary: true} for one that isn't UTF-8 text, {big: true}
    past MAX_BYTES (neither carries text)."""
    p = under(check_root(root), rel)
    st = p.stat()
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"not a file: {rel}")
    out = {"path": rel.replace("\\", "/").lstrip("/"), "mtime": st.st_mtime, "size": st.st_size}
    if st.st_size > MAX_BYTES:
        return {**out, "big": True}
    data = p.read_bytes()
    out["version"] = version(data)
    if b"\0" in data[:8192]:
        return {**out, "binary": True}
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {**out, "binary": True}
    bom = text.startswith("\ufeff")
    crlf = "\r\n" in text[:65536]
    return {**out, "text": text.removeprefix("\ufeff").replace("\r\n", "\n"), "eol": "crlf" if crlf else "lf",
            **({"bom": True} if bom else {})}


def stat_of(root: str, rel: str) -> dict:
    """{mtime, size} of a file, for the open tab's watch: cheap enough to ask every couple of seconds."""
    p = under(check_root(root), rel)
    st = p.stat()
    return {"mtime": st.st_mtime, "size": st.st_size}


class Conflict(Exception):
    """The file changed since the editor read it; carries what is there now."""

    def __init__(self, now: dict):
        super().__init__("the file changed since you opened it")
        self.now = now


def write(root: str, rel: str, text: str, base: str | None, eol: str = "", bom: bool = False,
          create: bool = False) -> dict:
    """Save text to a file, keeping its line ends (eol "crlf" writes \\r\\n) and byte-order mark. base: the version the
    edit started from; the save is refused (Conflict) if the file now differs. create: a new file (refused if one is
    there). Written to a temporary file beside it, then swapped in, so a crash never leaves half a file."""
    if len(text) > MAX_BYTES:
        raise ValueError("too long to save here")
    r = check_root(root)
    p = under(r, rel)
    if not rel.strip() or p == r.resolve():
        raise ValueError("no file name")
    if create:
        if p.exists():
            raise FileExistsError(f"{rel} already exists")
        if not p.parent.is_dir():
            raise FileNotFoundError(f"folder not found: {p.parent.relative_to(r.resolve()).as_posix()}")
    else:
        if not p.is_file():
            raise FileNotFoundError(f"file not found: {rel}")
        old = p.read_bytes()
        if base is not None and version(old) != base:
            raise Conflict(read(root, rel))
    body = text.replace("\r\n", "\n")
    if eol == "crlf":
        body = body.replace("\n", "\r\n")
    data = (("\ufeff" if bom else "") + body).encode("utf-8")
    tmp = p.with_name(f".{p.name}.deck-save")
    tmp.write_bytes(data)
    try:
        if p.exists():
            try:
                os.chmod(tmp, stat.S_IMODE(p.stat().st_mode))
            except OSError:
                pass
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()
    st = p.stat()
    return {"path": rel.replace("\\", "/").lstrip("/"), "version": version(data), "mtime": st.st_mtime, "size": st.st_size}
