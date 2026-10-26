
import pytest

from deck import core


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)


def test_openrouter_settings():
    routing = {"sort": "price", "data_collection": "deny", "zdr": True,
               "preferred_min_throughput": {"p50": 50}, "preferred_max_latency": {"p50": 3}}
    core.save_settings({"openrouter": routing})
    core.save_settings({"debug": True})
    assert core.settings()["openrouter"] == routing
    for invalid in ([], {"zdr": "true"}, {"sort": "cheapest"}, {"data_collection": "private"},
                    {"preferred_max_latency": {"p50": -3}}, {"preferred_min_throughput": True},
                    {"preferred_max_latency": {"p100": 3}}, {"preferred_max_latency": float("nan")}):
        with pytest.raises(ValueError):
            core.save_settings({"openrouter": invalid})
        assert core.settings()["openrouter"] == routing
    assert core.save_settings({"openrouter": {}})["openrouter"] == {}


def test_host_names_and_colours_set_from_settings(monkeypatch, tmp_path):
    inv = tmp_path / "hosts.toml"
    inv.write_text('[hosts.pc]\ndeck_url = "http://pc"\naka = "desk"\n[hosts.vm]\ndeck_url = "http://vm"\n')
    monkeypatch.setattr(core, "INVENTORY", str(inv))
    monkeypatch.setattr(core, "HOST_NAME", "pc")
    core.save_settings({"hostnames": {"vm": {"alias": " cloud ", "led": "#112233"}}})
    with pytest.raises(ValueError):
        core.save_settings({"hostnames": {"vm": {"led": "red"}}})
    hosts = {h["name"]: h for h in core.hosts()["hosts"]}
    assert (hosts["vm"]["aka"], hosts["vm"]["led"]) == ("cloud", "#112233")
    assert hosts["pc"]["aka"] == "desk"  # not set: the inventory's
    assert core.settings()["hostnames"] == {"vm": {"alias": "cloud", "led": "#112233"}}
    core.save_settings({"hostnames": {"vm": {"alias": "", "led": ""}}})  # both cleared: back to the inventory's
    assert core.settings()["hostnames"] == {}


def test_prompt_is_an_argument_not_script(monkeypatch):
    b = core.clean_button({"label": "x", "type": "claude", "text": 'say "hi"; rm -rf ~'})
    argv = core.build_argv(b)
    assert argv[:3] == [core.BASH, "-lc", 'exec claude "$@"']
    assert argv[-1] == 'say "hi"; rm -rf ~'
    # Full preferences can exceed tmux's command limit: Codex chat carries a path instead.
    context = "Deck preferences 'quoted' \\ paths\n" * 2000
    monkeypatch.setattr(core, "deck_context", lambda: context)
    b.update(type="codex", chat=True, session="test")
    argv = core.build_argv(b)
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
    cmd = core.msys_command([r"C:\Program Files\Deck\tmux.exe", "a b", "it's", "", 'exec claude "$@"'])
    assert cmd == '''"C:\\Program Files\\Deck\\tmux.exe" 'a b' 'it'"'"'s' '' 'exec claude "$@"\''''


def test_send_input_text_is_literal(monkeypatch):
    sent = []
    monkeypatch.setattr(core, "tmux", lambda *a: sent.append(a[-2:]))
    core.send_input("x", "-h")
    core.send_input("x", "cd a;")
    assert sent == [("--", "-h"), ("--", r"cd a\;")]  # tmux turns a trailing "\;" into ";"


def test_default_model_setting_is_a_launch_flag_and_validated():
    core.save_settings({"model": {"claude": "opus"}})
    assert core.model_args("claude") == ["--model", "opus"]
    b = core.clean_button({"label": "x", "type": "claude", "text": "hi"})
    argv = core.build_argv(b)
    assert argv[argv.index("--model") + 1] == "opus"
    with pytest.raises(ValueError):
        core.save_settings({"model": {"claude": "gpt-9"}})
    with pytest.raises(ValueError):
        core.save_settings({"model": "opus"})
    core.save_settings({"model": {"claude": ""}})  # Agent default again
    assert core.model_args("claude") == [] and "--model" not in core.build_argv(b)
    assert not core.settings().get("model")


def test_reader_note_setting_defaults_on_and_saves_off():
    assert core.settings()["readernote"] is True
    core.save_settings({"readernote": 0})
    assert core.settings()["readernote"] is False


def test_an_asking_script_key_gets_its_input_as_an_argument():
    b = core.clean_button({"label": "cu", "type": "shell", "text": 'desk-goal "$1" --act'})
    assert core.asks(b) and not core.asks({**b, "text": "ls"}) and not core.asks({**b, "type": "claude"})
    argv = core.build_argv({**b, **core.run_arg({"arg": 'open notes; rm -rf ~'})})
    assert argv == [core.BASH, "-lc", 'desk-goal "$1" --act', "deck", 'open notes; rm -rf ~']
    assert core.build_argv(b) == [core.BASH, "-lc", 'desk-goal "$1" --act'] and core.run_arg({"arg": " "}) == {}


def test_saved_layouts_are_cleaned_and_capped():
    s = core.save_settings({"layouts": [{"id": "a", "name": "  1 ", "layout": {"full": 1, "padcw": "300", "junk": 5}},
                                        {"name": "no id"}, "x"] + [{"id": str(i)} for i in range(20)]})
    assert s["layouts"][0] == {"id": "a", "name": "1", "layout": {"full": True, "padcw": 300}}
    assert len(s["layouts"]) == 10 and s["layouts"][1] == {"id": "0", "name": "Layout", "layout": {}}
    with pytest.raises(ValueError):
        core.save_settings({"layouts": [{"id": "b", "layout": {"padcw": "wide"}}]})
    assert not core.save_settings({"layouts": []}).get("layouts")


def test_agentpick_setting_saves_and_agents_installed_lists_known_types(monkeypatch):
    monkeypatch.setattr(core.shutil, "which", lambda cmd: "/bin/" + cmd if cmd in ("codex", "pi") else None)
    monkeypatch.setattr(core, "agy_available", lambda: False)
    monkeypatch.setattr(core, "HOME", core.Path("/nonexistent-home"))
    assert core.agents_installed() == ["codex", "pi"]
    assert core.settings()["agentpick"] is True
    core.save_settings({"agentpick": False, "agent": "codex"})
    assert core.settings()["agentpick"] is False and core.settings()["agent"] == "codex"
