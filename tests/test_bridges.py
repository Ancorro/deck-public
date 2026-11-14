"""Agent chats without tmux (deck/bridges.py): a bridge runs detached, is listed while it holds its lock, outlives the
process that started it, and ends on Kill. A stand-in bridge (the real one needs an agent) holds the lock and reads
its command queue as deck/bridge/main.py does."""
import json
import os
import subprocess
import sys
import time

import pytest

from deck import agents, bridges, config, sessions

FAKE = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from deck.bridge.main import hold_lock, write_exit
lock = hold_lock()
q = os.path.join(sys.argv[2], sys.argv[3] + ".in.jsonl")
while True:
    try:
        lines = open(q, encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    if any(json.loads(l).get("t") == "quit" for l in lines):
        write_exit(0)
        os._exit(0)
    time.sleep(0.05)
'''


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path)
    monkeypatch.setattr(config, "CHAT_DIR", tmp_path / "chat")
    monkeypatch.setattr(sessions, "tmux", lambda *a: subprocess.CompletedProcess(a, 1, "", "no tmux"))


def fake_argv(tmp_path, name):
    script = tmp_path / "fake_bridge.py"
    script.write_text(FAKE, encoding="utf-8")
    return [sys.executable, str(script), str(config.REPO), str(config.CHAT_DIR), name]


def wait(cond, secs=10.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_a_detached_chat_is_listed_while_it_runs_and_gone_after_kill(tmp_path):
    name = "deck-chat-1"
    bridges.start(name, fake_argv(tmp_path, name), str(tmp_path), label="Claude", type="claude", yolo=True)
    assert wait(lambda: bridges.alive(name))
    row = next(s for s in sessions.list_sessions() if s["name"] == name)
    assert row["chat"] and row["running"] and row["type"] == "claude" and row["state"] == "your_turn"
    assert sessions.session_exists(name) and sessions.is_chat(name) and sessions.yolo(name)
    sessions.rename_session(name, "my chat")
    assert next(s for s in sessions.list_sessions() if s["name"] == name)["title"] == "my chat"
    sessions.kill_session(name)
    assert not bridges.alive(name) and not sessions.session_exists(name)
    assert all(s["name"] != name for s in sessions.list_sessions())


def test_a_bridge_outlives_the_process_that_started_it(tmp_path):
    """A server restart (or update) must not take the chats with it: start one from a process that then exits."""
    name = "deck-chat-2"
    code = (f"import sys; sys.path.insert(0, {str(config.REPO)!r})\n"
            f"from deck import bridges, config; from pathlib import Path\n"
            f"config.DATA = Path({str(tmp_path)!r}); config.CHAT_DIR = config.DATA / 'chat'\n"
            f"bridges.start({name!r}, {fake_argv(tmp_path, name)!r}, {str(tmp_path)!r}, type='claude')\n")
    subprocess.run([sys.executable, "-c", code], check=True, timeout=30)
    try:
        assert wait(lambda: bridges.alive(name))
        time.sleep(0.5)
        assert bridges.alive(name)
    finally:
        bridges.remove(name)
    assert not bridges.alive(name)


def test_a_bridge_that_ended_shows_its_exit_and_stays_till_cleared(tmp_path):
    name = "deck-chat-3"
    bridges.start(name, fake_argv(tmp_path, name), str(tmp_path), type="codex")
    assert wait(lambda: bridges.alive(name))
    sessions.chat_command(name, {"t": "quit"})  # as if it ended on its own
    assert wait(lambda: not bridges.alive(name))
    row = next(s for s in sessions.list_sessions() if s["name"] == name)
    assert not row["running"] and row["exit"] == 0 and row["state"] == "done"
    assert sessions.clear_finished() == 1 and not sessions.session_exists(name)


def test_a_bridge_killed_without_a_word_is_failed(tmp_path):
    name = "deck-chat-4"
    (tmp_path / "bridges").mkdir()
    (tmp_path / "bridges" / f"{name}.json").write_text(json.dumps({"name": name, "type": "pi", "cwd": str(tmp_path)}))
    row = next(s for s in sessions.list_sessions() if s["name"] == name)
    assert not row["running"] and row["state"] == "failed"


def test_a_chat_command_line_has_no_tmux_or_msys_in_front_on_windows(monkeypatch):
    monkeypatch.setattr(config, "WINDOWS", True)
    argv = agents.build_argv({"type": "claude", "text": "hi", "chat": True, "session": "s", "label": "c", "cwd": ""})
    assert argv[0] == sys.executable and config.BASH not in argv


def test_env_prefix_moves_into_the_environment():
    argv, env = bridges.split_env(["env", "A=1", 'B={"x": "C:\\\\y"}', "python", "-c", "x=1"])
    assert argv == ["python", "-c", "x=1"] and env == {"A": "1", "B": '{"x": "C:\\\\y"}'}


def test_a_bridge_gets_no_msys_or_tmux_variables(tmp_path, monkeypatch):
    """MSYS=noglob (the server's, for calling tmux) broke the quoting of Claude's hook commands in its Git Bash."""
    monkeypatch.setenv("MSYS", "noglob")
    monkeypatch.setenv("TMUX", "/tmp/x,1,0")
    out = tmp_path / "env.json"
    code = f"import json, os; json.dump(dict(os.environ), open({str(out)!r}, 'w'))"
    bridges.start("deck-env", [sys.executable, "-c", code], str(tmp_path), type="claude")
    assert wait(lambda: out.exists() and out.stat().st_size > 0)
    time.sleep(0.2)
    env = json.loads(out.read_text())
    assert "MSYS" not in env and "TMUX" not in env
    assert env["DECK_SESSION_NAME"] == "deck-env" and env["DECK_SESSION"] == "1"


def test_a_bridge_has_no_living_parent(tmp_path):
    """A tree kill of the server (taskkill /T, by parent ids) must not reach it: its launcher has already exited."""
    name = "deck-chat-orphan"
    bridges.start(name, fake_argv(tmp_path, name), str(tmp_path), type="claude")
    try:
        assert wait(lambda: bridges.alive(name))
        pid = bridges.meta(name)["pid"]
        if config.WINDOWS:
            parent = windows_parent(pid)
            assert parent and not windows_running(parent), "its launcher is still running: a tree kill would reach it"
        else:
            assert int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[1]) != os.getpid()
    finally:
        bridges.remove(name)


def windows_parent(pid: int) -> int:
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]
    k = ctypes.windll.kernel32
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k.CreateToolhelp32Snapshot(2, 0)
    e = Entry()
    e.dwSize = ctypes.sizeof(Entry)
    ok = k.Process32First(snap, ctypes.byref(e))
    try:
        while ok:
            if e.th32ProcessID == pid:
                return e.th32ParentProcessID
            ok = k.Process32Next(snap, ctypes.byref(e))
    finally:
        k.CloseHandle(snap)
    return 0


def windows_running(pid: int) -> bool:
    return f" {pid} " in subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout


def test_restart_moves_a_tmux_chat_out_of_tmux(tmp_path, monkeypatch):
    """A chat open in tmux before the update: Restart kills its pane (keeping its files) and starts it detached."""
    name = "deck-old-chat"
    opts = {"#{@deck_type}": "claude", "#{pane_start_command}": "... --dangerously-skip-permissions",
            "#{@deck_chat}": "1", "#{@deck_computer}": "", "#{pane_current_path}": str(tmp_path),
            "#{@deck_label}": "Claude", "#{@deck_button}": "b1", "#{@deck_title}": "kept title", "#{@deck_project}": ""}
    calls, live = [], {"pane": True}

    def fake_tmux(*a):
        calls.append(a)
        if a[0] == "display-message":
            return subprocess.CompletedProcess(a, 0, opts.get(a[-1], "") + "\n", "")
        if a[0] == "kill-session":
            live["pane"] = False
        if a[0] == "has-session":
            return subprocess.CompletedProcess(a, 0 if live["pane"] else 1, "", "")
        return subprocess.CompletedProcess(a, 0, "", "")
    monkeypatch.setattr(sessions, "tmux", fake_tmux)
    config.CHAT_DIR.mkdir(parents=True)
    (config.CHAT_DIR / f"{name}.state").write_text(json.dumps({"resume": "conv-1"}))
    started = {}
    monkeypatch.setattr(bridges, "start", lambda n, argv, cwd, **f: started.update(name=n, argv=argv, cwd=cwd, **f))
    sessions.restart_agent(name)
    assert ("kill-session", "-t", f"={name}") in calls and not any(c[0] == "respawn-pane" for c in calls)
    assert started["name"] == name and started["yolo"] and started["title"] == "kept title"
    assert (config.CHAT_DIR / f"{name}.state").exists(), "Restart must keep the chat's files (its conversation id)"


def test_a_new_chat_is_not_failed_while_it_starts(tmp_path):
    name = "deck-starting"
    (tmp_path / "bridges").mkdir()
    (tmp_path / "bridges" / f"{name}.json").write_text(json.dumps({"name": name, "type": "pi", "created": int(time.time())}))
    row = next(s for s in sessions.list_sessions() if s["name"] == name)
    assert row["running"] and row["state"] != "failed"


def test_a_pane_folder_that_is_gone_falls_back_to_one_that_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WORK", str(tmp_path))
    assert sessions.native_folder("/no/such/place") == str(tmp_path)
    assert sessions.native_folder(str(tmp_path)) == str(tmp_path)


def test_kill_right_after_start_waits_for_the_bridge(tmp_path):
    """A Kill before the new bridge takes its lock must still end it, not leave it running with no tab."""
    name = "deck-quick-kill"
    slow = tmp_path / "slow.py"
    slow.write_text("import time, runpy, sys; time.sleep(1.5); sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0])")
    bridges.start(name, [sys.executable, str(slow), *fake_argv(tmp_path, name)[1:]], str(tmp_path), type="claude")
    assert not bridges.alive(name)  # still starting
    sessions.kill_session(name)
    time.sleep(2.5)
    assert not bridges.alive(name) and not sessions.session_exists(name)
    assert all(s["name"] != name for s in sessions.list_sessions())


def test_the_trash_is_this_hosts_own_and_starts_from_its_share_of_the_old_file(tmp_path, monkeypatch):
    from deck import session_trash, site
    monkeypatch.setattr(config, "HOST_NAME", "here")
    old = [{"id": "a", "folder": str(tmp_path), "deleted": time.time()},  # its folder is here: this host's
           {"id": "b", "folder": "/home/nobody/work", "deleted": time.time()},  # another host's
           {"id": "c", "host": "here", "folder": "/x", "deleted": time.time()},
           {"id": "d", "host": "there", "folder": str(tmp_path), "deleted": time.time()}]
    (tmp_path / "session-trash.json").write_text(json.dumps(old))
    assert [x["id"] for x in session_trash.load()] == ["a", "c"]
    assert not site._shared("session-trash-local.json") and not site._shared("session-trash.json")
    name = "deck-trash"
    (tmp_path / "bridges").mkdir()
    (tmp_path / "bridges" / f"{name}.json").write_text(json.dumps({"name": name, "type": "claude", "cwd": str(tmp_path)}))
    config.CHAT_DIR.mkdir()
    (config.CHAT_DIR / f"{name}.state").write_text(json.dumps({"resume": "conv"}))
    assert session_trash.keep(name)["host"] == "here"
    assert [x["id"] for x in session_trash.load()][1:] == ["a", "c"]
