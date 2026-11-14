"""deck/skills.py: one skills folder linked into every installed agent CLI's own skills folder."""
import os

from deck import config, skills


def _skill(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\nbody\n")
    return d


def _setup(tmp_path, monkeypatch):
    home, src = tmp_path / "home", tmp_path / "skills"
    home.mkdir()
    src.mkdir()
    monkeypatch.setattr(config, "HOME", home)
    return home, src


def test_links_each_skill_into_each_installed_agents_folder(tmp_path, monkeypatch):
    home, src = _setup(tmp_path, monkeypatch)
    _skill(src, "reports")
    (src / "notes.txt").write_text("not a skill")
    out = skills.ensure(["claude", "codex", "pi", "agy"], src)
    for root in (".claude/skills", ".agents/skills", ".gemini/config/skills"):
        assert (home / root / "reports" / "SKILL.md").read_text().startswith("---")
        assert not (home / root / "notes.txt").exists()
    assert len(out) == 3  # codex and pi share .agents/skills: one link
    assert skills.ensure(["claude", "codex", "pi", "agy"], src) == []  # second run: nothing to do


def test_only_installed_agents_get_links(tmp_path, monkeypatch):
    home, src = _setup(tmp_path, monkeypatch)
    _skill(src, "reports")
    skills.ensure(["codex"], src)
    assert (home / ".agents/skills/reports").exists()
    assert not (home / ".claude").exists() and not (home / ".gemini").exists()


def test_leaves_a_skill_that_is_not_its_own_alone(tmp_path, monkeypatch):
    home, src = _setup(tmp_path, monkeypatch)
    _skill(src, "reports")
    mine = _skill(home / ".claude/skills", "reports")
    (mine / "SKILL.md").write_text("the user's own")
    out = skills.ensure(["claude"], src)
    assert (mine / "SKILL.md").read_text() == "the user's own"
    assert out and out[0].startswith("skipped")


def test_removes_its_link_when_the_skill_is_deleted(tmp_path, monkeypatch):
    home, src = _setup(tmp_path, monkeypatch)
    gone = _skill(src, "old")
    _skill(src, "reports")
    skills.ensure(["claude"], src)
    for f in gone.iterdir():
        f.unlink()
    gone.rmdir()
    out = skills.ensure(["claude"], src)
    assert not os.path.lexists(home / ".claude/skills/old")
    assert (home / ".claude/skills/reports").exists()
    assert any(line.startswith("removed") for line in out)


def test_folder_that_already_is_the_skills_folder_is_left_as_is(tmp_path, monkeypatch):
    home, src = _setup(tmp_path, monkeypatch)
    _skill(src, "reports")
    (home / ".claude").mkdir()
    skills._make_link(home / ".claude/skills", src)
    assert skills.ensure(["claude"], src) == []


def test_missing_or_empty_source_does_nothing(tmp_path, monkeypatch):
    home, src = _setup(tmp_path, monkeypatch)
    assert skills.ensure(["claude"], tmp_path / "nope") == []
    assert skills.ensure(["claude"], src) == []
    assert not (home / ".claude").exists()


def test_listing_reads_descriptions_and_text_files(tmp_path):
    src = tmp_path / "skills"
    d = _skill(src, "reports")
    (d / "SKILL.md").write_text("---\nname: reports\ndescription: >-\n  Write reports\n  and papers.\n---\nbody\n")
    (d / "refs").mkdir()
    (d / "refs" / "a.md").write_text("a")
    (d / "logo.png").write_bytes(b"\x89PNG")
    (d / ".cache").mkdir()
    (d / ".cache" / "x.md").write_text("x")
    (src / "loose").mkdir()  # no SKILL.md: not a skill
    out = skills.listing(src)
    assert out["path"] == str(src)
    assert out["skills"] == [{"name": "reports", "description": "Write reports and papers.", "files": ["SKILL.md", "refs/a.md"]}]


def test_skill_files_stay_inside_their_skill(tmp_path):
    src = tmp_path / "skills"
    _skill(src, "a")
    _skill(src, "b")
    (tmp_path / "secret.md").write_text("no")
    (src / "a" / ".hidden.md").write_text("no")
    assert skills.read("a/SKILL.md", src).startswith("---")
    for bad in ("a/../b/SKILL.md", "a/../../secret.md", str(tmp_path / "secret.md"), f"a/{tmp_path / 'secret.md'}",
                "nope/SKILL.md", "a", "a/.hidden.md", "a/missing.md"):
        try:
            skills.resolve(bad, src)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_a_linked_skill_reads_and_saves_keeping_line_ends(tmp_path):
    src, elsewhere = tmp_path / "skills", tmp_path / "repo"
    src.mkdir()
    _skill(tmp_path, "repo")
    (elsewhere / "SKILL.md").write_bytes(b"---\r\nname: repo\r\n---\r\nold\r\n")
    skills._make_link(src / "repo", elsewhere)  # a skill linked in from another folder (a junction on Windows)
    assert skills.read("repo/SKILL.md", src) == "---\r\nname: repo\r\n---\r\nold\r\n"
    skills.write("repo/SKILL.md", "---\nname: repo\n---\nnew é\n", src)
    assert (elsewhere / "SKILL.md").read_bytes() == "---\r\nname: repo\r\n---\r\nnew é\r\n".encode()
