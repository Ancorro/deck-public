"""The shelf: pieces of chat kept from a reply (Save turn: the whole turn; Save messages: just the agent's words), listed
beside the chat. One JSON file in DATA, in display order (new items last, or first: Settings > Shelf). Dragging
reorders it and groups items into folders (an item's "folder" is a folder item's id; no folders in folders); Autoname
names a folder from its contents. Delete moves an item, or a folder with its contents, to the trash, restorable for a
month.

A document (kind "doc": a PDF or text file added with + File) holds its text, read in the browser, for agents and
search; the file itself stays on the host it was added on (DATA/shelf-files/<id>, plus <id>.txt with the text), shown
in the Review view. Its "file": {name, type, size, host, path}.

A link (kind "link": a web page an agent pinned, POST /api/shelf/pin) holds its "url", a title and a note (text); one
item per address, so pinning a page again updates it (a project keeps one artifact page and adds to it). One link can be
its project's main page ("project": project_of's name, "main": when it was made main), shown atop the chat of every
session in that project; the newest main wins, as hosts may each make one before they sync."""
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from . import config
from .projects import project_of

KINDS = ("turn", "nl", "doc")
MAX_TEXT = 200_000
MAX_DOC_TEXT = 2_000_000  # a document's text (a long PDF)
MAX_FILE = 30 * 1024 * 1024
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOC_TYPES = ("application/pdf", "text/", DOCX)
KEEP_DELETED = 30 * 86400


def path():
    return config.DATA / "shelf.json"


def load() -> list[dict]:
    """Every item, the trash's too (with "deleted": when); trash older than KEEP_DELETED is dropped."""
    try:
        items = json.loads(path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return [x for x in items if time.time() - x.get("deleted", time.time()) < KEEP_DELETED]


def _save(items: list[dict]) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)  # atomic: a crash never leaves a half-written file


def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def add(raw: dict, meta: dict | None = None, top: bool = False) -> dict:
    """Validate and store one item, with the session it came from (meta: host, agent, folder, ...; the page adds
    the model it shows): below the others, or above them (top: Settings > Shelf). Raises ValueError."""
    kind, text = raw.get("kind"), str(raw.get("text", "")).strip()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if not text:
        raise ValueError("nothing to save")
    if len(text) > (MAX_DOC_TEXT if kind == "doc" else MAX_TEXT):
        raise ValueError("too long")
    item = {"id": _new_id(), "kind": kind, "text": text, "session": str(raw.get("session", ""))[:200],
            "title": str(raw.get("title", "")).strip()[:200], "at": time.time()}
    meta = {**(meta or {}), **({"model": str(raw["model"])[:100]} if raw.get("model") else {})}
    if meta:
        item["meta"] = meta
    _save([item, *load()] if top else [*load(), item])
    return item


def pin(raw: dict, meta: dict | None = None, top: bool = False) -> dict:
    """An agent pins a web page: {url, title, note}. The same address again updates that item (title, note, time) and
    brings it back from the trash instead of adding another. Raises ValueError."""
    url = str(raw.get("url", "")).strip()
    if len(url) > 2000 or not re.fullmatch(r"https?://[^\s/]+\S*", url):
        raise ValueError("url must be an http(s) address")
    project = str(raw.get("project") or (raw.get("folder") and project_of(str(raw["folder"])))
                  or (meta or {}).get("project") or "")[:300] if raw.get("main") else ""
    if raw.get("main") and not project:
        raise ValueError("main needs a project: pass your session ($DECK_SESSION_NAME) from a folder in a git repo")
    title = str(raw.get("title", "")).strip()[:200] or urlsplit(url).hostname or url
    note = str(raw.get("note", "")).strip()[:MAX_TEXT]
    items = load()
    it = next((x for x in items if x.get("kind") == "link" and x.get("url") == url), None)
    if it:
        it.update(title=title, text=note or it.get("text", ""), at=time.time())
        it.pop("deleted", None)
    else:
        it = {"id": _new_id(), "kind": "link", "url": url, "text": note, "title": title,
              "session": str(raw.get("session", ""))[:200], "at": time.time(), **({"meta": meta} if meta else {})}
        items = [it, *items] if top else [*items, it]
    if project:
        _make_main(items, it, project)
    _save(items)
    return it


def _make_main(items: list[dict], it: dict, project: str) -> None:
    for x in items:
        if x is not it and x.get("project") == project:
            x.pop("main", None)
    it.update(project=project, main=time.time())


def main(item_id: str, project: str) -> None:
    """A link made its project's main page (Main for … on its card), or no longer (project "": the bar's ×)."""
    items = load()
    it = next((x for x in items if x["id"] == item_id and x.get("kind") == "link"), None)
    if it is None:
        raise ValueError("no such page on the shelf")
    if project:
        _make_main(items, it, str(project)[:300])
    else:
        it.pop("main", None)
    _save(items)


def arrange(order: list, new_folders: list) -> None:
    """A drag's result: order is [{id, folder}] for the shelf's live items, top to bottom; new_folders are
    [{id, title}] made by dropping one item on another. Items not named keep their place after these (the trash, or one
    saved meanwhile goes last). A folder left empty goes."""
    items = load()
    known = {x["id"] for x in items}
    for f in new_folders or []:
        fid = str(f.get("id", ""))
        if fid and fid not in known:
            items.append({"id": fid, "kind": "folder", "text": "", "title": str(f.get("title") or "Folder")[:200],
                          "at": time.time()})
    by_id = {x["id"]: x for x in items}
    folders = {x["id"] for x in items if x.get("kind") == "folder" and not x.get("deleted")}
    placed = []
    for o in order or []:
        x = by_id.pop(str(o.get("id", "")), None)
        if x is None:
            continue
        folder = str(o.get("folder") or "")
        if folder in folders and x.get("kind") != "folder":
            x["folder"] = folder
        else:
            x.pop("folder", None)
        placed.append(x)
    rest = [x for x in items if x["id"] in by_id]
    new = [x for x in rest if not x.get("deleted") and x.get("kind") != "folder" and not x.get("folder")]
    out = [*placed, *new, *[x for x in rest if x not in new]]
    used = {x.get("folder") for x in out if not x.get("deleted")}
    _save([x for x in out if x.get("kind") != "folder" or x.get("deleted") or x["id"] in used])


def rename(item_id: str, title: str) -> None:
    items = load()
    for x in items:
        if x["id"] == item_id:
            x["title"] = str(title).strip()[:200] or x.get("title", "")
    _save(items)


NAME_PROMPT = ("These are notes saved together in one folder. Name the folder in 2 to 5 plain words that say what they "
               "share. Reply with the name only: no quotes, no full stop.\n\n{notes}")
STOP = frozenset("the a an and or of to in on for with from at by is are was be this that these those it its as into about "  # noqa: SIM905
                 "what how why when which your you i we our not no yes can will just also then than more most".split())


def autoname(folder_id: str) -> str:
    """Name a folder from its contents (Claude's small model, else the words its items' titles share); saved as its
    title. Raises ValueError for a folder with nothing in it."""
    kids = [x for x in load() if x.get("folder") == folder_id and not x.get("deleted")]
    if not kids:
        raise ValueError("the folder is empty")
    notes = "\n\n".join(f"- {x.get('title', '')}\n{x['text'][:400]}" for x in kids[:20])
    name = _ask_claude(NAME_PROMPT.format(notes=notes)) or _common_words(kids)
    rename(folder_id, name)
    return name


def _ask_claude(prompt: str) -> str:
    """One answer from `claude -p` (Haiku), or "" when it can't run or says nothing usable."""
    exe = shutil.which("claude")
    if not exe:
        return ""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}  # not a child of a running session
    try:
        r = subprocess.run([exe, "-p", "--model", "haiku"], input=prompt, capture_output=True, text=True,
                           encoding="utf-8", timeout=90, cwd=str(config.DATA), env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    line = next((l for l in r.stdout.splitlines() if l.strip()), "")
    name = re.sub(r"^[\"'*#\s]+|[\"'*.\s]+$", "", line)
    return name[:60] if r.returncode == 0 and 0 < len(name.split()) <= 8 else ""


def _common_words(kids: list[dict]) -> str:
    words = Counter(w for x in kids for w in set(re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", x.get("title", "").lower()))
                    if w not in STOP)
    shared = [w for w, n in words.most_common() if n > 1][:3]  # words several titles share, else the first few
    return " ".join(w.capitalize() for w in shared or [w for w, _ in words.most_common(3)]) or "Folder"


def for_agent(item_id: str = "", full: bool = False) -> str:
    """The shelf as an agent reads it (GET /api/shelf/agent): an index of folders and items (id, title, kind, a first
    line), one item in full (item_id), or everything in full."""
    live = [x for x in load() if not x.get("deleted")]
    if item_id:
        x = next((x for x in live if x["id"] == item_id and x.get("kind") != "folder"), None)
        if not x:
            return f"No shelf item {item_id}.\n"
        return "\n\n".join(filter(None, (f"# {x.get('title') or 'Saved item'}", x.get("url"), x["text"]))) + "\n"
    folders = {x["id"]: x for x in live if x.get("kind") == "folder"}

    def entry(x):
        what = {"turn": "whole turn", "doc": "document", "link": "pinned page"}.get(x["kind"], "agent text")
        head = f"- `{x['id']}` {x.get('title') or 'Saved item'} ({what})"
        if x.get("url"):
            head += f" <{x['url']}>"
        if x.get("file"):
            head += f" [file on {x['file']['host']}: {x['file']['path']}]"
        if full:
            return f"{head}\n\n{x['text']}\n"
        first = next((l.strip() for l in x["text"].splitlines() if l.strip()), "")[:120]
        return f"{head}: {first}"

    out = ["# The user's shelf", ""]
    for x in live:
        if x.get("kind") == "folder":
            out += ["", f"## Folder: {x.get('title') or 'Folder'}", *[entry(k) for k in live if k.get("folder") == x["id"]], ""]
        elif not x.get("folder") or x["folder"] not in folders:
            out.append(entry(x))
    if len(out) == 2:
        out.append("(empty)")
    if not full:
        out += ["", "One in full: ?id=ID. Everything: ?all=1."]
    return "\n".join(out).rstrip() + "\n"


def files_dir():
    return config.DATA / "shelf-files"


def put_file(item_id: str, name: str, ctype: str, data: bytes) -> dict:
    """A document's file, kept on this host; its text beside it (<id>.txt) for agents that read files."""
    items = load()
    it = next((x for x in items if x["id"] == item_id and x.get("kind") == "doc"), None)
    if it is None:
        raise ValueError("no such document")
    if not ctype.startswith(DOC_TYPES):
        raise ValueError("only PDFs, Word files and text files")
    if len(data) > MAX_FILE:
        raise ValueError("file too big (30 MB at most)")
    files_dir().mkdir(parents=True, exist_ok=True)
    f = files_dir() / item_id
    f.write_bytes(data)
    (files_dir() / f"{item_id}.txt").write_text(it["text"], encoding="utf-8")
    it["file"] = {"name": re.sub(r"[^\w .,()+-]", "_", name)[:200] or "file", "type": ctype[:100], "size": len(data),
                  "host": config.HOST_NAME, "path": str(f)}
    _save(items)
    _sweep(items)
    return it


def edit_text(item_id: str, text: str) -> dict:
    """A document edited in the reader: its text, and its file too when that is a text file on this host."""
    items = load()
    it = next((x for x in items if x["id"] == item_id and x.get("kind") == "doc"), None)
    if it is None:
        raise ValueError("no such document")
    if len(text) > MAX_DOC_TEXT:
        raise ValueError("too long")
    f, ftype = file_path(item_id), (it.get("file") or {}).get("type", "")
    if it.get("file") and not ftype.startswith("text/"):
        raise ValueError("only text documents can be edited")
    if it.get("file") and f is None:
        raise ValueError(f"its file is on {it['file']['host']}: edit it there")
    it["text"], it["at"] = text, time.time()
    if f is not None and it["file"].get("linked"):  # the file itself, keeping its line ends (CRLF in a Windows repo)
        crlf = b"\r\n" in f.read_bytes()[:65536]
        f.write_bytes((text.replace("\r\n", "\n").replace("\n", "\r\n") if crlf else text).encode("utf-8"))
        it["file"].update(size=f.stat().st_size, mtime=f.stat().st_mtime)
    elif f is not None:
        f.write_bytes(text.encode("utf-8"))
        it["file"]["size"] = f.stat().st_size
        (files_dir() / f"{item_id}.txt").write_text(text, encoding="utf-8")
    _save(items)
    return it


def link_info(raw: dict) -> dict:
    """The file a link names (raw["path"]), read on the host that has it: {"file": {...}, "text": ...}. A follower reads
    it here and hands it to the writer, whose disk doesn't have it (link_file's info). Raises ValueError."""
    f = Path(str(raw.get("path", ""))).expanduser()
    if not f.is_absolute() or not f.is_file():
        raise ValueError("path must be an existing file, given in full")
    f = f.resolve()
    st = f.stat()
    if st.st_size > MAX_FILE:
        raise ValueError("file too big (30 MB at most)")
    ctype = _link_type(f)
    return {"file": {"name": f.name, "type": ctype, "size": st.st_size, "host": config.HOST_NAME, "path": str(f),
                     "linked": True, "mtime": st.st_mtime},
            "text": _linked_text(f, ctype)}


def peer_link_info(info) -> dict:
    """A link_info another host sent (host key only), kept to its fields. Raises ValueError."""
    fi = (info or {}).get("file") if isinstance(info, dict) else None
    if not isinstance(fi, dict) or not all(isinstance(fi.get(k), str) and fi[k] for k in ("name", "type", "host", "path")):
        raise ValueError("bad linked file")
    if not fi["type"].startswith(DOC_TYPES):
        raise ValueError("only PDFs, Word files and text files")
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    return {"file": {"name": fi["name"][:200], "type": fi["type"][:100], "size": num(fi.get("size")), "host": fi["host"][:100],
                     "path": fi["path"][:4096], "linked": True, "mtime": num(fi.get("mtime"))},
            "text": str(info.get("text", ""))[:MAX_DOC_TEXT]}


def link_file(raw: dict, meta: dict | None = None, top: bool = False, info: dict | None = None) -> dict:
    """A document that is a file on a host, not a copy (POST /api/shelf/link {path, title}): its "file" has
    "linked": true, the host and the real path, so the reader shows the file as it is now and a save writes the file
    itself (a paper's source in its repo, its PDF beside it). The same file again updates that item. info: the file as
    its own host read it (link_info), when that host isn't this one. Raises ValueError."""
    info = info or link_info(raw)
    fi = info["file"]
    items = load()
    it = next((x for x in items if x.get("kind") == "doc" and (x.get("file") or {}).get("linked")
               and x["file"].get("host") == fi["host"] and x["file"].get("path") == fi["path"]), None)
    if it is None:
        it = {"id": _new_id(), "kind": "doc", "text": "", "session": str(raw.get("session", ""))[:200], "at": time.time(),
              **({"meta": meta} if meta else {})}
        items = [it, *items] if top else [*items, it]
    it.pop("deleted", None)
    it["title"] = str(raw.get("title", "")).strip()[:200] or fi["name"]
    it["file"] = dict(fi)
    it["text"] = info["text"]
    _save(items)
    return it


def _link_type(f: Path) -> str:
    ext = f.suffix.lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext == ".docx":
        return DOCX
    if ext in (".md", ".markdown"):
        return "text/markdown"
    if ext in (".html", ".htm"):
        return "text/html"
    try:
        f.read_bytes()[:65536].decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("only PDFs, Word files and text files") from None
    return "text/plain"


def _linked_text(f: Path, ctype: str) -> str:
    """A linked file's text for agents and search: a text file's own (line ends as LF, as the reader edits it); a PDF's
    or Word file's is read in the browser, so it keeps a pointer to the file."""
    if ctype.startswith("text/"):
        return f.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")[:MAX_DOC_TEXT]
    return f"(linked file {f}: open it in the reader, or read the file)"


def save_item(it: dict) -> None:
    """One item, changed, back into the shelf."""
    items = load()
    _save([it if x["id"] == it["id"] else x for x in items])


def refresh_linked(it: dict) -> bool:
    """A linked document after its file changed on disk (an editor, git, a build): its text and size from the file
    now. True when it changed; the caller saves."""
    f = Path(it["file"]["path"])
    if not f.is_file():
        return False
    st = f.stat()
    if st.st_mtime == it["file"].get("mtime") and st.st_size == it["file"].get("size"):
        return False
    it["file"].update(size=st.st_size, mtime=st.st_mtime)
    it["text"], it["at"] = _linked_text(f, it["file"]["type"]), time.time()
    return True


def file_path(item_id: str):
    """This host's copy of a document's file (or the file itself, for a linked one), or None."""
    if not re.fullmatch(r"[\w-]+", item_id):
        return None
    it = next((x for x in load() if x["id"] == item_id), None)
    if it and (it.get("file") or {}).get("linked"):
        f = Path(it["file"]["path"])
        return f if it["file"].get("host") == config.HOST_NAME and f.is_file() else None
    f = files_dir() / item_id
    return f if f.is_file() else None


def _sweep(items: list[dict]) -> None:
    """Files whose document is gone (out of the trash, or deleted for good)."""
    keep = {x["id"] for x in items}
    for f in files_dir().glob("*"):
        if f.name.split(".")[0] not in keep:
            f.unlink(missing_ok=True)


def delete(item_id: str, forever: bool = False) -> None:
    """To the trash (restorable for KEEP_DELETED), or gone. A folder takes its contents with it."""
    items = load()
    gone = {item_id} | {x["id"] for x in items if x.get("folder") == item_id and not x.get("deleted")}
    if forever:
        items = [x for x in items if x["id"] not in gone]
    now = time.time()
    for x in items:
        if x["id"] in gone:
            x["deleted"] = now
    _save(items)


def restore(item_id: str) -> None:
    """Back from the trash; a folder brings back what went with it. An item whose folder is gone goes to the top
    level."""
    items = load()
    me = next((x for x in items if x["id"] == item_id), None)
    if me is None:
        return
    when = me.get("deleted")
    for x in items:
        if x is me or (me.get("kind") == "folder" and x.get("folder") == item_id and x.get("deleted") == when):
            x.pop("deleted", None)
    live = {x["id"] for x in items if x.get("kind") == "folder" and not x.get("deleted")}
    for x in items:
        if x.get("folder") and x["folder"] not in live and not x.get("deleted"):
            x.pop("folder")
    _save(items)
