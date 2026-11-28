"""Settings → General → Logins (deck/logins.py): each installed tool's sign-in state, and a login tab that never runs over a
working login (user 2026-11-08: "an easy way to new CLI users to log in")."""
import subprocess

import pytest

from deck import config, logins, server, sessions


@pytest.fixture(autouse=True)
def fresh_cache():
    logins._cache.update(at=0.0, list=[])


def fake_tools(monkeypatch, installed: dict[str, int], opencode_out: str = ""):
    """installed: tool -> its status command's exit code (missing tools aren't installed)."""
    monkeypatch.setattr(logins, "find", lambda t: f"/bin/{t}" if t in installed else "")

    def run(argv, **_):
        tool = argv[0].rsplit("/", 1)[-1]
        out = opencode_out if tool == "opencode" else ""
        return subprocess.CompletedProcess(argv, installed[tool], out, "")
    monkeypatch.setattr(logins.subprocess, "run", run)


def test_status_lists_only_installed_tools_with_their_state(monkeypatch):
    fake_tools(monkeypatch, {"claude": 0, "codex": 1, "gh": 0})
    got = {t["tool"]: t["signedin"] for t in logins.status()}
    assert got == {"claude": True, "codex": False, "gh": True}


def test_opencode_counts_as_signed_in_only_with_a_credential(monkeypatch):
    fake_tools(monkeypatch, {"opencode": 0}, opencode_out="Credentials ~/.local/share/opencode/auth.json\n└  0 credentials\n")
    assert logins.status(fresh=True)[0]["signedin"] is False
    fake_tools(monkeypatch, {"opencode": 0}, opencode_out="●  OpenRouter api\n└  3 credentials\n")
    assert logins.status(fresh=True)[0]["signedin"] is True


def test_login_is_refused_when_already_signed_in_unknown_or_missing(monkeypatch):
    # Codex deletes its saved login as `codex login` starts: a Log in over a working one would sign the user out
    fake_tools(monkeypatch, {"codex": 0, "claude": 1})
    with pytest.raises(ValueError, match="already signed in"):
        logins.login_key("codex")
    with pytest.raises(ValueError, match="unknown tool"):
        logins.login_key("rm -rf")
    with pytest.raises(ValueError, match="isn't installed"):
        logins.login_key("gh")
    key = logins.login_key("claude")
    assert key["type"] == "shell" and key["text"].startswith("claude auth login;")


@pytest.fixture
async def client(aiohttp_client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    return await aiohttp_client(server.make_app())


async def test_routes_list_and_start_a_login_tab(client, monkeypatch):
    fake_tools(monkeypatch, {"claude": 1, "gh": 0})
    started = []
    monkeypatch.setattr(sessions, "start_run", lambda key: started.append(key) or "deck-claude-code-login-1")
    r = await client.get("/api/logins?fresh=1")
    assert r.status == 200 and [t["tool"] for t in await r.json()] == ["claude", "gh"]
    r = await client.post("/api/logins/claude")
    assert r.status == 200 and (await r.json())["session"] == "deck-claude-code-login-1"
    assert started[0]["label"] == "Claude Code login"
    assert (await client.post("/api/logins/gh")).status == 400  # signed in: no tab


def test_merge_combines_per_host_logins_in_tools_order():
    per_host = [
        ("cor-desk", [
            {"tool": "hf", "name": "Hugging Face", "what": "Hugging Face account", "optional": True, "signedin": True},
            {"tool": "claude", "name": "Claude Code", "what": "Anthropic account", "optional": False, "signedin": True},
            {"tool": "agy", "name": "Gemini", "what": "Google account: Gemini (agy) sessions", "optional": True, "signedin": True},
        ]),
        ("cloud-trader", [
            {"tool": "claude", "name": "Claude Code", "what": "Anthropic account", "optional": False, "signedin": True},
            {"tool": "gh", "name": "GitHub", "what": "GitHub account", "optional": False, "signedin": False},
        ]),
        ("stevenslaptop", [
            {"tool": "claude", "name": "Claude Code", "what": "Anthropic account", "optional": False, "signedin": True},
            {"tool": "gh", "name": "GitHub", "what": "GitHub account", "optional": False, "signedin": True},
            {"tool": "codex", "name": "Codex", "what": "ChatGPT account", "optional": False, "signedin": False},
        ]),
    ]
    merged = logins.merge(per_host)
    assert [t["tool"] for t in merged] == ["claude", "codex", "gh", "agy", "hf"]
    by_tool = {t["tool"]: t for t in merged}
    assert by_tool["claude"]["signedin"] is True and by_tool["claude"]["missing"] == []
    assert by_tool["claude"]["hosts"] == ["cor-desk", "cloud-trader", "stevenslaptop"]
    assert by_tool["gh"]["signedin"] is False and by_tool["gh"]["missing"] == ["cloud-trader"]
    assert by_tool["codex"]["signedin"] is False and by_tool["codex"]["missing"] == ["stevenslaptop"]
    assert by_tool["agy"]["name"] == "Gemini" and by_tool["agy"]["signedin"] is True


async def test_route_merges_peers_and_supports_here_param(client, monkeypatch):
    from deck import auth, site

    fake_tools(monkeypatch, {"claude": 0, "gh": 0})
    monkeypatch.setattr(auth, "host_key", lambda: "k-test")
    monkeypatch.setattr(site, "order", lambda: ["cor-desk", "stevenslaptop", "offline"])
    monkeypatch.setattr(site, "host_of", lambda name: {
        "cor-desk": {"name": "cor-desk", "url": "http://me", "current": True},
        "stevenslaptop": {"name": "stevenslaptop", "url": "http://laptop", "current": False},
        "offline": {"name": "offline", "url": "http://down", "current": False},
    }.get(name))

    peer_calls = []

    class Resp:
        def __init__(self, status, data):
            self.status = status
            self._data = data

        async def json(self):
            return self._data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class HTTP:
        async def close(self):
            pass

        def get(self, url, **_kw):
            peer_calls.append(url)
            if url.startswith("http://laptop/"):
                return Resp(200, [
                    {"tool": "claude", "name": "Claude Code", "what": "Anthropic", "optional": False, "signedin": True},
                    {"tool": "gh", "name": "GitHub", "what": "GitHub", "optional": False, "signedin": False},
                ])
            raise OSError("down")

    client.app._state["peer_http"] = HTTP()

    r_here = await client.get("/api/logins?here=1&fresh=1")
    assert r_here.status == 200
    here_data = await r_here.json()
    assert [t["tool"] for t in here_data] == ["claude", "gh"]
    assert all("missing" not in t for t in here_data)
    assert peer_calls == []

    r_merged = await client.get("/api/logins?fresh=1")
    assert r_merged.status == 200
    merged = {t["tool"]: t for t in await r_merged.json()}
    assert merged["claude"]["signedin"] is True and merged["claude"]["missing"] == []
    assert merged["gh"]["signedin"] is False and merged["gh"]["missing"] == ["stevenslaptop"]
    assert any(u == "http://laptop/api/logins?here=1&fresh=1" for u in peer_calls)

