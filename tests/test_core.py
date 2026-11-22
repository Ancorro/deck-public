import json

import pytest

from deck import agents, buttons, config, sessions, settings


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)


def test_openrouter_settings():
    routing = {"sort": "price", "data_collection": "deny", "zdr": True,
               "preferred_min_throughput": {"p50": 50}, "preferred_max_latency": {"p50": 3}}
    settings.save_settings({"openrouter": routing})
    settings.save_settings({"debug": True})
    assert settings.settings()["openrouter"] == routing
    for invalid in ([], {"zdr": "true"}, {"sort": "cheapest"}, {"data_collection": "private"},
                    {"preferred_max_latency": {"p50": -3}}, {"preferred_min_throughput": True},
                    {"preferred_max_latency": {"p100": 3}}, {"preferred_max_latency": float("nan")}):
        with pytest.raises(ValueError):
            settings.save_settings({"openrouter": invalid})
        assert settings.settings()["openrouter"] == routing
    assert settings.save_settings({"openrouter": {}})["openrouter"] == {}


def test_ui_size_is_a_clamped_percent_kept_apart_for_phones_and_pcs():
    assert settings.settings()["ui"] == settings.settings()["pcui"] == 100
    settings.save_settings({"pcui": 125})
    settings.save_settings({"ui": 500})
    assert (settings.settings()["ui"], settings.settings()["pcui"]) == (150, 125)
    with pytest.raises(ValueError):
        settings.save_settings({"ui": "big"})


def test_ui_text_is_a_clamped_percent_kept_apart_for_phones_and_pcs():
    assert settings.settings()["uitext"] == settings.settings()["pcuitext"] == 100
    settings.save_settings({"pcuitext": 125})
    settings.save_settings({"uitext": 10})
    assert (settings.settings()["uitext"], settings.settings()["pcuitext"]) == (75, 125)
    with pytest.raises(ValueError):
        settings.save_settings({"uitext": "big"})


def test_host_names_and_colours_set_from_settings(monkeypatch, tmp_path):
    inv = tmp_path / "hosts.toml"
    inv.write_text('[hosts.pc]\ndeck_url = "http://pc"\naka = "desk"\n[hosts.vm]\ndeck_url = "http://vm"\n')
    monkeypatch.setattr(config, "INVENTORY", str(inv))
    monkeypatch.setattr(config, "HOST_NAME", "pc")
    settings.save_settings({"hostnames": {"vm": {"alias": " cloud ", "led": "#112233"}}})
    with pytest.raises(ValueError):
        settings.save_settings({"hostnames": {"vm": {"led": "red"}}})
    hosts = {h["name"]: h for h in settings.hosts()["hosts"]}
    assert (hosts["vm"]["aka"], hosts["vm"]["led"]) == ("cloud", "#112233")
    assert hosts["pc"]["aka"] == "desk"  # not set: the inventory's
    assert settings.settings()["hostnames"] == {"vm": {"alias": "cloud", "led": "#112233"}}
    settings.save_settings({"hostnames": {"vm": {"alias": "", "led": ""}}})  # both cleared: back to the inventory's
    assert settings.settings()["hostnames"] == {}


def test_prompt_is_an_argument_not_script(monkeypatch):
    b = buttons.clean_button({"label": "x", "type": "claude", "text": 'say "hi"; rm -rf ~'})
    argv = agents.build_argv(b)
    assert argv[:3] == [config.BASH, "-lc", 'exec claude "$@"']
    assert argv[-1] == 'say "hi"; rm -rf ~'
    # Full preferences can exceed tmux's command limit: Codex chat carries a path instead.
    context = "Deck preferences 'quoted' \\ paths\n" * 2000
    monkeypatch.setattr(agents, "deck_context", lambda: context)
    b.update(type="codex", chat=True, session="test")
    argv = agents.build_argv(b)
    assert len(" ".join(argv)) < 4000
    from pathlib import Path
    assert Path(argv[argv.index("--deck-context") + 1]).read_text(encoding="utf-8") == context + "\n"


def test_pairing_flow(monkeypatch):
    from deck import auth
    monkeypatch.delenv("DECK_AUTH", raising=False)
    assert auth.enforced()  # default: paired devices only, even before the first
    monkeypatch.setenv("DECK_AUTH", "true")
    assert auth.enforced()  # an unknown value (a typo) keeps pairing on
    monkeypatch.setenv("DECK_AUTH", "auto")
    assert not auth.enforced()  # auto: open until the first device is approved
    c = auth.new_code("host")
    token = auth.claim(c["code"], "Pixel")
    assert auth.device_for(token)["pending"] and not auth.enforced()
    with pytest.raises(ValueError):
        auth.claim(c["code"], "again")  # one use
    with pytest.raises(ValueError):
        auth.decide(c["code"], "someone-else", True)  # only the code's maker approves
    assert auth.pending(c["code"])["name"] == "Pixel"
    auth.decide(c["code"], "host", True)
    dev = auth.device_for(token)
    assert dev and not dev.get("pending") and auth.enforced()
    assert auth.device_for("wrong") is None
    c2 = auth.new_code(dev["id"])
    t2 = auth.claim(c2["code"], "Laptop")
    auth.decide(c2["code"], dev["id"], False)  # refused = removed
    assert auth.device_for(t2) is None
    auth.remove(dev["id"])
    assert auth.devices() == [] and not auth.enforced()
    assert not auth.is_host("Bearer x")
    key = auth.ensure_host_key()
    assert auth.is_host(f"Bearer {key}") and not auth.is_host("Bearer nope")


def test_msys_command_quotes_every_argument():
    # MSYS's own command-line splitting: everything single-quoted, a ' itself as "'"; argv[0] in double quotes.
    cmd = sessions.msys_command([r"C:\Program Files\Deck\tmux.exe", "a b", "it's", "", 'exec claude "$@"'])
    assert cmd == '''"C:\\Program Files\\Deck\\tmux.exe" 'a b' 'it'"'"'s' '' 'exec claude "$@"\''''


def test_send_input_text_is_literal(monkeypatch):
    sent = []
    monkeypatch.setattr(sessions, "tmux", lambda *a: sent.append(a[-2:]))
    sessions.send_input("x", "-h")
    sessions.send_input("x", "cd a;")
    assert sent == [("--", "-h"), ("--", r"cd a\;")]  # tmux turns a trailing "\;" into ";"


def test_default_model_setting_is_a_launch_flag_and_validated():
    settings.save_settings({"model": {"claude": "opus"}})
    assert agents.model_args("claude") == ["--model", "opus"]
    b = buttons.clean_button({"label": "x", "type": "claude", "text": "hi"})
    argv = agents.build_argv(b)
    assert argv[argv.index("--model") + 1] == "opus"
    with pytest.raises(ValueError):
        settings.save_settings({"model": {"claude": "--bad flag"}})
    with pytest.raises(ValueError):
        settings.save_settings({"model": "opus"})
    settings.save_settings({"model": {"claude": ""}})  # Agent default again
    assert agents.model_args("claude") == [] and "--model" not in agents.build_argv(b)
    assert not settings.settings().get("model")


def test_computer_sessions_have_their_own_default_model(monkeypatch):
    """Settings → General → New sessions → Default model, its Computer row: a Computer session's model, also on Restart (which
    rebuilds the key without one); blank, it takes Claude's; plain Claude sessions keep Claude's."""
    monkeypatch.setattr(agents, "computer_args", lambda: [])  # no computer.json in the test's data folder
    flag = lambda argv: argv[argv.index("--model") + 1] if "--model" in argv else None
    plain = buttons.clean_button({"label": "x", "type": "claude", "text": "hi"})
    computer = {**plain, "computer": True}
    settings.save_settings({"model": {"claude": "opus", "computer": "sonnet"}})
    assert flag(agents.build_argv(computer)) == "sonnet"
    assert flag(agents.build_argv(plain)) == "opus"
    assert flag(agents.build_argv({**computer, "chat": True, "session": "s"})) == "sonnet"
    assert flag(agents.build_argv({**computer, "model": "haiku"})) == "haiku"  # a key's own model still wins
    settings.save_settings({"model": {"computer": ""}})  # blank: Claude's
    assert flag(agents.build_argv(computer)) == "opus"
    with pytest.raises(ValueError):
        settings.save_settings({"model": {"computer": "--bad flag"}})
    settings.save_settings({"model": {"claude": ""}})


def test_bug_button_is_opus_medium_in_the_checkout_over_settings():
    settings.save_settings({"model": {"claude": "haiku"}, "effort": {"claude": "max"}})
    b = buttons.bug_button("  the Send button does nothing  ")
    assert b["cwd"] == str(config.REPO) and b["yolo"] and "the Send button does nothing" in b["text"]
    argv = agents.build_argv({**b, "chat": True, "session": "s"})
    assert argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--effort") + 1] == "medium"
    with pytest.raises(ValueError):
        buttons.bug_button(" ")


def test_reader_note_setting_defaults_on_and_saves_off():
    assert settings.settings()["readernote"] is True
    settings.save_settings({"readernote": 0})
    assert settings.settings()["readernote"] is False


def test_an_asking_script_key_gets_its_input_as_an_argument():
    b = buttons.clean_button({"label": "cu", "type": "shell", "text": 'desk-goal "$1" --act'})
    assert buttons.asks(b) and not buttons.asks({**b, "text": "ls"}) and not buttons.asks({**b, "type": "claude"})
    argv = agents.build_argv({**b, **buttons.run_arg({"arg": 'open notes; rm -rf ~'})})
    assert argv == [config.BASH, "-lc", 'desk-goal "$1" --act', "deck", 'open notes; rm -rf ~']
    assert agents.build_argv(b) == [config.BASH, "-lc", 'desk-goal "$1" --act'] and buttons.run_arg({"arg": " "}) == {}


def test_saved_layouts_are_cleaned_and_capped():
    s = settings.save_settings({"layouts": [{"id": "a", "name": "  1 ", "layout": {"msgshut": 1, "padw": "300", "junk": 5}},
                                        {"name": "no id"}, "x"] + [{"id": str(i)} for i in range(20)]})
    assert s["layouts"][0] == {"id": "a", "name": "1", "layout": {"msgshut": True, "padw": 300}}
    assert len(s["layouts"]) == 10 and s["layouts"][1] == {"id": "0", "name": "Layout", "layout": {}}
    with pytest.raises(ValueError):
        settings.save_settings({"layouts": [{"id": "b", "layout": {"padw": "wide"}}]})
    assert not settings.save_settings({"layouts": []}).get("layouts")


def test_the_retired_margins_are_dropped():
    # the PC's side columns sit at the window's edges, with no margins (user 2026-11-21): their widths and Fullscreen go,
    # on save and from settings saved before
    margins = {"padcw": 293, "feedcw": 372, "termw": 1662, "bpadcw": 1, "bfeedcw": 1, "btermw": 1, "full": True}
    s = settings.save_settings({"layout": {**margins, "msgshut": 1}})
    assert s["layout"] == {"msgshut": True}
    config.settings_path().write_text(json.dumps({"layout": {**margins, "nobottom": True},
                                                  "layouts": [{"id": "a", "name": "1", "layout": {**margins, "msgshut": True}}]}))
    s = settings.settings()
    assert s["layout"] == {"nobottom": True} and s["layouts"] == [{"id": "a", "name": "1", "layout": {"msgshut": True}}]
    assert "resize" not in s


def test_saved_layouts_keep_the_menu_sizes():
    # the Layouts menu lists UI size, UI text and Text size: a saved layout keeps them too (bug report 2026-11-15)
    s = settings.save_settings({"layouts": [{"id": "a", "name": "1", "layout": {"msgshut": 1},
                                             "sizes": {"pcui": "125", "pcuitext": 300, "pcfont": 15, "junk": 1}}]})
    assert s["layouts"][0]["sizes"] == {"pcui": 125, "pcuitext": 150, "pcfont": 15}
    with pytest.raises(ValueError):
        settings.save_settings({"layouts": [{"id": "b", "sizes": {"pcui": "big"}}]})


def test_agentpick_setting_saves_and_agents_installed_lists_known_types(monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda cmd: "/bin/" + cmd if cmd in ("codex", "pi") else None)
    monkeypatch.setattr(agents, "agy_available", lambda: False)
    monkeypatch.setattr(config, "HOME", agents.Path("/nonexistent-home"))
    assert agents.agents_installed() == ["codex", "pi"]
    assert settings.settings()["agentpick"] is True
    settings.save_settings({"agentpick": False, "agent": "codex"})
    assert settings.settings()["agentpick"] is False and settings.settings()["agent"] == "codex"


def test_computer_session_is_claude_with_this_hosts_desktop_tools(tmp_path, monkeypatch):
    # User 2026-11-02: computer use "should be a session on the machine": Claude with the desktop as MCP tools
    monkeypatch.setattr(config, "COMPUTER_FILE", str(tmp_path / "computer.json"))
    with pytest.raises(ValueError):  # not set up here: no Computer sessions
        agents.build_argv({**buttons.new_agent_button("claude", computer=True), "chat": True, "session": "s"})
    assert agents.computer_config() is None
    (tmp_path / "computer.json").write_text('{"python": "/usr/bin/python3", "backend": "x11", "display": ":5"}')
    b = buttons.new_agent_button("claude", computer=True)
    assert b["label"] == "Computer" and b["computer"]
    argv = agents.build_argv({**b, "chat": True, "session": "s"})
    mcp = __import__("json").loads((tmp_path / "computer-mcp.json").read_text())["mcpServers"]["computer"]
    assert argv[argv.index("--mcp-config") + 1] == (tmp_path / "computer-mcp.json").as_posix()
    assert "--allowedTools=mcp__computer" in argv  # one argument: as two it would swallow the first message
    assert mcp["command"] == "/usr/bin/python3" and mcp["args"][0].endswith("computer_mcp.py")
    with pytest.raises(ValueError):  # Claude only
        buttons.new_agent_button("codex", computer=True)
    assert "--mcp-config" not in agents.build_argv({**buttons.new_agent_button("claude"), "chat": True, "session": "s"})


@pytest.mark.parametrize("kind,model", [("claude", "claude-custom-1"), ("codex", "gpt-test"),
    ("opencode", "openrouter/vendor/model"), ("pi", "provider/model"), ("agy", "gemini-test-high"),
    ("openclaw", "anthropic/claude-test")])
def test_every_agent_default_merges_clears_and_launches(kind, model):
    settings.save_settings({"model": {"claude": "opus"}})
    saved = settings.save_settings({"model": {kind: model}})
    assert saved["model"][kind] == model
    assert agents.model_args(kind) == ["--model", model]
    if kind != "claude":
        assert saved["model"]["claude"] == "opus"
    b = buttons.clean_button({"label": "test", "type": kind, "text": ""})
    argv = agents.build_argv({**b, "chat": True, "session": "model-test"})
    assert argv[argv.index("--model") + 1] == model
    settings.save_settings({"model": {kind: ""}})
    assert agents.model_args(kind) == []


@pytest.mark.parametrize("model", [None, [], {}, 3, "has space", "--flag", "a\n", "x" * 257])
def test_default_model_rejects_invalid_values(model):
    with pytest.raises(ValueError):
        settings.save_settings({"model": {"codex": model}})



def test_openclaw_terminal_applies_model_through_gateway_before_opening_tui():
    settings.save_settings({"model": {"openclaw": "anthropic/claude-test"}})
    b = buttons.clean_button({"label": "test", "type": "openclaw", "text": "hello"})
    argv = agents.build_argv(b)
    assert "sessions.patch" in argv[2] and "&&" in argv[2]
    import json
    assert json.loads(argv[4])["model"] == "anthropic/claude-test"
    assert argv[-2:] == ["--message", "hello"]



def test_agy_model_and_effort_are_one_model_variant():
    settings.save_settings({"model": {"agy": "gemini-test-low"}, "effort": {"agy": "high"}})
    b = buttons.clean_button({"label": "test", "type": "agy", "text": ""})
    argv = agents.build_argv({**b, "chat": True, "session": "s"})
    assert argv[argv.index("--model") + 1] == "gemini-test-high"
    assert "--effort" not in argv


def test_a_script_tab_shows_its_name_when_its_program_sets_no_title(monkeypatch):
    """A script key's pane has only tmux's default title (the host name): its tab showed "~" (the folder) instead of
    the script's name (2026-11-08)."""
    import platform
    from pathlib import Path

    from deck import sessions
    home = str(Path.home()).replace("\\", "/")
    host = platform.node()
    assert sessions.pane_subtitle(host, home, "Claude token") == "Claude token"
    assert sessions.pane_subtitle("vim notes.md", home, "Claude token") == "vim notes.md"  # the program's own title wins
    assert sessions.pane_subtitle(host, home) == "~"  # terminals and agents keep the folder
    assert sessions.pane_subtitle(host, home + "/src/deck") == "~/src/deck"


def test_second_terminal_column_session_saves_and_clears():
    settings.save_settings({"pcsession2": "pc/deck-shell-2"})
    assert settings.settings()["pcsession2"] == "pc/deck-shell-2"
    settings.save_settings({"pcsession2": ""})  # one column again
    assert "pcsession2" not in settings.settings() or settings.settings()["pcsession2"] == ""
