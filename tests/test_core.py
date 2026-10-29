
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
        settings.save_settings({"model": {"claude": "gpt-9"}})
    with pytest.raises(ValueError):
        settings.save_settings({"model": "opus"})
    settings.save_settings({"model": {"claude": ""}})  # Agent default again
    assert agents.model_args("claude") == [] and "--model" not in agents.build_argv(b)
    assert not settings.settings().get("model")


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
    s = settings.save_settings({"layouts": [{"id": "a", "name": "  1 ", "layout": {"full": 1, "padcw": "300", "junk": 5}},
                                        {"name": "no id"}, "x"] + [{"id": str(i)} for i in range(20)]})
    assert s["layouts"][0] == {"id": "a", "name": "1", "layout": {"full": True, "padcw": 300}}
    assert len(s["layouts"]) == 10 and s["layouts"][1] == {"id": "0", "name": "Layout", "layout": {}}
    with pytest.raises(ValueError):
        settings.save_settings({"layouts": [{"id": "b", "layout": {"padcw": "wide"}}]})
    assert not settings.save_settings({"layouts": []}).get("layouts")


def test_agentpick_setting_saves_and_agents_installed_lists_known_types(monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda cmd: "/bin/" + cmd if cmd in ("codex", "pi") else None)
    monkeypatch.setattr(agents, "agy_available", lambda: False)
    monkeypatch.setattr(config, "HOME", agents.Path("/nonexistent-home"))
    assert agents.agents_installed() == ["codex", "pi"]
    assert settings.settings()["agentpick"] is True
    settings.save_settings({"agentpick": False, "agent": "codex"})
    assert settings.settings()["agentpick"] is False and settings.settings()["agent"] == "codex"
