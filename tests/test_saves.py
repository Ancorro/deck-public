
import pytest

from deck import core, saves  # noqa: E402


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DATA", tmp_path)
    pushed = []

    async def fake_push(s):
        pushed.append(s)
        return []
    monkeypatch.setattr(saves, "push", fake_push)
    return pushed


def note(sid="20261004-010203-abcd", **kw):
    return {"id": sid, "text": "# Fix the arm\n\nGoal: pick things.\nNext: calibrate.", "host": "pc-a",
            "agent": "claude"} | kw


@pytest.mark.parametrize("bad", [{"id": "../x"}, {"text": ""}, {"text": "x" * (saves.MAX_TEXT + 1)}])
def test_store_rejects_bad_saves(bad):
    with pytest.raises(ValueError):
        saves.store(note() | bad)
