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
