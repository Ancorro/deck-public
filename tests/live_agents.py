"""Each installed agent CLI, for real: one short turn and its /model menu, started as Deck starts a chat
(sessions.start_run: the chat bridge, run detached), so a CLI update that changes its protocol shows up here.
Spends one small model call per agent, so it runs only by name: `bash tests/run.sh live` (every installed agent) or
`DECK_LIVE_AGENTS=codex,pi bash tests/run.sh live`. The agents keep the real home (their logins); Deck's data and
tmux server are temporary (run.sh sets DECK_DATA and DECK_LIVE_TMUX), so real sessions are never touched."""
import json
import os
import socket
import time

import pytest

from deck import agents, bridges, buttons, config, sessions, settings

# config sets Deck's own tmux folder on Windows: this test's server goes in its own (run.sh makes it), never the real one
if not os.environ.get("DECK_LIVE_TMUX"):
    raise RuntimeError("run it with `bash tests/run.sh live`: it needs its own tmux folder (DECK_LIVE_TMUX)")
os.environ["TMUX_TMPDIR"] = os.environ["DECK_LIVE_TMUX"]
TURN_TIMEOUT = float(os.environ.get("DECK_LIVE_TIMEOUT", "240"))
WORD = "pong"
# Claude's default may be Fable, which on a plan without usage credits stops on a "Session paused" dialog (2.1.317)
# before any reply: the test checks the protocol, not the account, so it asks a model every plan includes
LIVE_MODEL = {"claude": os.environ.get("DECK_LIVE_CLAUDE_MODEL", "haiku")}


def gateway_up() -> bool:
    """OpenClaw talks to its Gateway; with none running there is nothing to check."""
    try:
        socket.create_connection(("127.0.0.1", int(os.environ.get("OPENCLAW_GATEWAY_PORT", "18789"))), 2).close()
        return True
    except OSError:
        return False


def chosen() -> list[str]:
    want = [a for a in os.environ.get("DECK_LIVE_AGENTS", "").split(",") if a]
    found = agents.agents_installed()
    picked = [a for a in (want or found) if a in found]
    return [a for a in picked if a != "openclaw" or gateway_up()]


class Chat:
    """One chat session, started through Deck's own start_run, and its event log."""

    def __init__(self, agent: str, cwd: str, first: str):
        sessions.start_tmux()
        defaults = json.loads(os.environ.get("DECK_LIVE_DEFAULT_MODELS", "{}"))
        if defaults:
            settings.save_settings({"model": defaults})
        key = buttons.clean_button({"label": f"live {agent}", "type": agent, "text": first, "cwd": cwd})
        if agent in LIVE_MODEL:
            key["model"] = LIVE_MODEL[agent]
        self.name = sessions.start_run(key)
        self.out = config.CHAT_DIR / f"{self.name}.jsonl"
        self.inp = config.CHAT_DIR / f"{self.name}.in.jsonl"
        self.state = config.CHAT_DIR / f"{self.name}.state"

    def events(self) -> list[dict]:
        try:
            lines = self.out.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def screen(self) -> str:
        """The bridge's own output (its log), for a failure's message."""
        return bridges.log_tail(self.name, 1500)

    def wait_for(self, done, timeout: float = TURN_TIMEOUT) -> list[dict]:
        end = time.time() + timeout
        while time.time() < end:
            ev = self.events()
            if done(ev):
                return ev
            if any(e.get("t") == "state" and e.get("s") == "failed" for e in ev):
                break
            time.sleep(0.5)
        ev = self.events()
        notes = [e["text"] for e in ev if e.get("t") == "note"]
        pytest.fail(f"timed out or failed; notes: {notes}; last events: {ev[-6:]}; bridge log: {self.screen()}")

    def send(self, cmd: dict) -> None:
        with open(self.inp, "a", encoding="utf-8") as f:
            f.write(json.dumps(cmd) + "\n")

    def close(self) -> None:
        sessions.kill_session(self.name)


def replied(ev: list[dict]) -> bool:
    """A reply holding the word, and its turn over."""
    said = [i for i, e in enumerate(ev) if e.get("t") == "msg" and any(
        WORD in str(b.get("text", "")).lower() for b in e.get("blocks") or [])]
    return bool(said) and any(e.get("t") == "state" and e.get("s") == "your_turn" for e in ev[said[0]:])


@pytest.mark.parametrize("agent", chosen())
def test_agent_cli_turn_and_model_menu(agent, tmp_path):
    cwd = tmp_path / "work"
    cwd.mkdir()
    c = Chat(agent, str(cwd), f"Reply with only the word {WORD}. Do not use any tools.")
    try:
        ev = c.wait_for(replied)
        errors = [e["text"] for e in ev if e.get("t") == "note" and e.get("err")]
        assert not errors, errors
        assert any(e.get("t") == "user" for e in ev), "the message never showed as the user's"
        assert any(e.get("t") == "status" and e.get("parts") for e in ev), "no status bar (model) from the agent"
        default = json.loads(os.environ.get("DECK_LIVE_DEFAULT_MODELS", "{}")).get(agent)
        if default:
            assert any(default in " ".join(e.get("parts") or []) for e in ev if e.get("t") == "status"), ev
        assert json.loads(c.state.read_text(encoding="utf-8")).get("resume"), \
            "no conversation id saved: Restart could not continue it"
        if agent != "claude":  # Claude's /model is its own terminal dialog, not a list Deck reads
            c.send({"t": "menu", "text": "/model", "rid": "live-model"})
            ev = c.wait_for(lambda ev: any(e.get("t") == "menu" and e.get("rid") == "live-model" for e in ev), 90)
            menu = next(e for e in ev if e.get("t") == "menu" and e.get("rid") == "live-model")
            assert menu.get("options"), f"/model listed no models: {menu}"
            expected = os.environ.get("DECK_LIVE_EXPECT_MODEL", "")
            if agent == "codex" and expected:
                assert any(expected.lower() in option.lower() for option in menu["options"]), menu
        # Restart: the old bridge goes and a new one continues the same conversation on the same files
        conv = json.loads(c.state.read_text(encoding="utf-8")).get("resume")
        sessions.restart_agent(c.name)
        assert bridges.alive(c.name) or bridges.starting(c.name)
        c.wait_for(lambda ev: any(e.get("t") == "state" and e.get("s") == "your_turn" for e in ev), 120)
        assert json.loads(c.state.read_text(encoding="utf-8")).get("resume") == conv, "Restart lost the conversation"
    finally:
        c.close()


@pytest.mark.skipif("agy" not in chosen(), reason="agy not installed or not chosen")
def test_agy_ask_mode_asks_before_a_tool(tmp_path):
    """agy 1.5.7 made Auto the default for a session with no mode: a fresh chat (Ask before tools) must still stop
    on a tool and show Deck's approval card, not run it (unless the user set toolPermission=always-proceed)."""
    from pathlib import Path
    cfg = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    if cfg.is_file():
        try:
            if json.loads(cfg.read_text(encoding="utf-8")).get("toolPermission") == "always-proceed":
                pytest.skip("agy user settings.json sets toolPermission=always-proceed")
        except (OSError, ValueError):
            pass
    cwd = tmp_path / "work"
    cwd.mkdir()
    c = Chat("agy", str(cwd), "Use your shell tool to run exactly: git --version . Report its output.")
    try:
        def card(ev):
            return any(e.get("t") == "ask" and ("Allow" in (e.get("options") or []) or "Allow tools" in json.dumps(e))
                       for e in ev)

        def ran(ev):  # the tool's output came back and the turn ended
            out = [i for i, e in enumerate(ev) if e.get("t") == "tool_end" and "git version" in str(e.get("out"))]
            return bool(out) and any(e.get("t") == "state" and e.get("s") == "your_turn" for e in ev[out[0]:])
        ev = c.wait_for(lambda ev: card(ev) or ran(ev))
        assert card(ev) and not ran(ev), f"no approval card; the tool ran without asking: {ev[-6:]}"
    finally:
        c.close()
