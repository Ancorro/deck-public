"""The Code view's file routes (deck/code.py, deck/api/code.py): a folder listed one level at a time, text files read
as JSON (never served raw), saves that refuse to overwrite an agent's newer edit, and no way out of the folder asked for."""
import pytest

from deck import code, config, server, sessions
from deck.api import access


@pytest.fixture
async def client(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    (tmp_path / "data").mkdir()
    return await aiohttp_client(server.make_app())


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "node_modules").mkdir()
    (r / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (r / "README.md").write_bytes(b"# Title\r\nline two\r\n")
    (r / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0")
    (r / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    return r


async def test_lists_folders_first_and_marks_heavy_ones(client, repo):
    r = await client.get("/api/code/list", params={"root": str(repo)})
    assert r.status == 200
    d = await r.json()
    assert [e["name"] for e in d["entries"]] == ["node_modules", "src", "logo.png", "page.html", "README.md"]
    assert d["entries"][0] == {"name": "node_modules", "dir": True, "heavy": True}
    assert d["entries"][1] == {"name": "src", "dir": True}
    assert d["path"] == ""
    sub = await (await client.get("/api/code/list", params={"root": str(repo), "path": "src"})).json()
    assert [e["name"] for e in sub["entries"]] == ["main.py"] and sub["path"] == "src"


async def test_reads_text_as_json_with_line_ends_and_refuses_binary_and_html_is_never_served_raw(client, repo):
    r = await client.get("/api/code/file", params={"root": str(repo), "path": "README.md"})
    assert r.status == 200 and r.content_type == "application/json"
    d = await r.json()
    assert d["text"] == "# Title\nline two\n" and d["eol"] == "crlf" and d["version"]
    png = await (await client.get("/api/code/file", params={"root": str(repo), "path": "logo.png"})).json()
    assert png["binary"] is True and "text" not in png
    html = await client.get("/api/code/file", params={"root": str(repo), "path": "page.html"})
    assert html.content_type == "application/json"  # an HTML file is text in JSON, never a page on Deck's origin
    assert (await html.json())["text"] == "<script>alert(1)</script>"


async def test_paths_cannot_leave_the_folder(client, repo):
    for bad in ("../secret.txt", "src/../../secret.txt", "..", "/../secret.txt"):
        r = await client.get("/api/code/file", params={"root": str(repo), "path": bad})
        assert r.status == 400, bad
        assert "outside" in (await r.json())["error"]
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "../secret.txt", "text": "x", "base": None})
    assert r.status == 400
    assert (repo.parent / "secret.txt").read_text(encoding="utf-8") == "outside"
    assert (await client.get("/api/code/list", params={"root": str(repo), "path": ".."})).status == 400
    assert (await client.get("/api/code/list", params={"root": "relative/path"})).status == 400
    assert (await client.get("/api/code/list", params={"root": str(repo / "nope")})).status == 404


async def test_save_keeps_line_ends_and_refuses_a_stale_base(client, repo):
    d = await (await client.get("/api/code/file", params={"root": str(repo), "path": "README.md"})).json()
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "README.md", "text": "# New\nmore\n",
                                                 "base": d["version"], "eol": d["eol"]})
    assert r.status == 200
    saved = await r.json()
    assert (repo / "README.md").read_bytes() == b"# New\r\nmore\r\n"  # CRLF stays CRLF
    # an agent writes the file meanwhile: the next save from the old version is refused, with what is there now
    (repo / "README.md").write_bytes(b"agent's\r\n")
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "README.md", "text": "mine\n",
                                                 "base": saved["version"], "eol": "crlf"})
    assert r.status == 409
    body = await r.json()
    assert body["conflict"] is True and body["now"]["text"] == "agent's\n"
    assert (repo / "README.md").read_bytes() == b"agent's\r\n"
    # Overwrite (no base) replaces it
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "README.md", "text": "mine\n", "base": None,
                                                 "eol": "crlf"})
    assert r.status == 200 and (repo / "README.md").read_bytes() == b"mine\r\n"
    assert not list(repo.glob(".*.deck-save"))  # no temporary file left behind


async def test_save_keeps_a_byte_order_mark_and_creates_only_new_files(client, repo):
    (repo / "bom.txt").write_bytes("\ufeffhello\n".encode("utf-8"))
    d = await (await client.get("/api/code/file", params={"root": str(repo), "path": "bom.txt"})).json()
    assert d["text"] == "hello\n" and d["bom"] is True
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "bom.txt", "text": "bye\n", "base": d["version"],
                                                 "bom": True})
    assert r.status == 200 and (repo / "bom.txt").read_bytes() == "\ufeffbye\n".encode("utf-8")
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "src/new.py", "text": "x = 1\n", "create": True})
    assert r.status == 200 and (repo / "src" / "new.py").read_text(encoding="utf-8") == "x = 1\n"
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "src/new.py", "text": "y\n", "create": True})
    assert r.status == 409  # already there: create never overwrites
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "gone.txt", "text": "y\n", "base": None})
    assert r.status == 404  # a save without create needs the file


async def test_big_files_open_read_only_and_stat_answers_cheaply(client, repo, monkeypatch):
    monkeypatch.setattr(code, "MAX_BYTES", 10)
    (repo / "big.txt").write_text("x" * 50, encoding="utf-8")
    d = await (await client.get("/api/code/file", params={"root": str(repo), "path": "big.txt"})).json()
    assert d["big"] is True and "text" not in d
    s = await (await client.get("/api/code/stat", params={"root": str(repo), "path": "big.txt"})).json()
    assert s["size"] == 50 and s["mtime"] > 0


async def test_saves_need_a_known_origin_and_the_tunnel_needs_pairing(client, repo, monkeypatch):
    monkeypatch.setenv("DECK_ORIGINS", "https://good.example")
    r = await client.put("/api/code/file", json={"root": str(repo), "path": "README.md", "text": "x", "base": None},
                         headers={"Origin": "https://evil.example"})
    assert r.status == 403
    assert (repo / "README.md").read_bytes() == b"# Title\r\nline two\r\n"
    monkeypatch.setattr(access, "via_tunnel", lambda r: True)
    monkeypatch.setattr(access, "is_local", lambda r: False)
    for path in ("/api/code/list", "/api/code/file", "/api/code/stat"):
        assert (await client.get(path, params={"root": str(repo), "path": "README.md"})).status == 401, path
    assert (await client.put("/api/code/file", json={"root": str(repo), "path": "README.md", "text": "x"})).status == 401


def test_a_reported_folder_is_the_sessions_tree_root(monkeypatch, tmp_path):
    # PUT .../project {folder} keeps the folder; the session's row names it (the Code view's tree opens there)
    updates = {}
    monkeypatch.setattr(sessions.bridges, "exists", lambda n: True)
    monkeypatch.setattr(sessions.bridges, "update", lambda n, **kw: updates.update(kw))
    monkeypatch.setattr(sessions, "project_of", lambda f: "github.com/me/repo")
    assert sessions.set_session_project("deck-x", folder=str(tmp_path)) == "github.com/me/repo"
    assert updates["folder"] == config.win_path(str(tmp_path)) and updates["project"] == "github.com/me/repo"
    monkeypatch.setattr(sessions, "session_meta", lambda n: {"project": ""})
    sessions.set_session_project("deck-x")  # neither: back to the starting folder
    assert updates["folder"] is None and updates["project"] is None
    assert sessions.work_folder("/c/Users/x/repo") == ("C:/Users/x/repo" if config.WINDOWS else "/c/Users/x/repo")
    assert sessions.work_folder("") == ""
