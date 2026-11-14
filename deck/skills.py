"""One skills folder for every agent (user 2026-11-01: "ensure deck has a skill system, dir"). Each skill in
`config.SKILLS` (a folder holding SKILL.md) is linked into the folder each installed agent CLI reads skills from, so a
skill added once reaches Claude Code, Codex, Pi, OpenCode and agy, in Deck or a plain terminal. Runs at server start
and before each agent session; `python -m deck.skills` runs it by hand and prints what it did.

Links are symlinks, or directory junctions on Windows (no admin rights or developer mode needed). Deck only replaces
or removes a link that points into the skills folder; anything else of the same name is left alone and reported."""
import os
import sys
from pathlib import Path

from . import config

# Where each agent CLI finds user skills (checked live 2026-11-01 with a throwaway skill in each).
# Codex, Pi and OpenCode all read the shared Agent Skills folder; agy reads only its own.
ROOTS = {"claude": ".claude/skills", "codex": ".agents/skills", "pi": ".agents/skills",
         "opencode": ".agents/skills", "agy": ".gemini/config/skills"}


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _link_target(p: Path) -> str | None:
    """Where a symlink or junction points, unresolved; None when p is not one."""
    try:
        return os.readlink(p)
    except (OSError, ValueError):
        return None


def _ours(p: Path, src: Path) -> bool:
    """A link Deck made: it points into the skills folder (directly, not through another link)."""
    t = _link_target(p)
    if t is None:
        return False
    t = t.removeprefix("\\\\?\\")
    return os.path.normcase(os.path.dirname(os.path.abspath(t))) == os.path.normcase(os.path.abspath(src))


def _unlink(p: Path) -> None:
    if config.WINDOWS and p.is_dir():  # a junction is removed as a directory, without touching its target
        os.rmdir(p)
    else:
        p.unlink()


def _make_link(link: Path, target: Path) -> None:
    if config.WINDOWS:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


def skill_names(src: Path) -> list[str]:
    try:
        return sorted(d.name for d in src.iterdir() if (d / "SKILL.md").is_file())
    except OSError:
        return []


def roots(agents_list: list[str]) -> list[Path]:
    """The skill folders of the installed agents, each once."""
    out = []
    for a in agents_list:
        r = config.HOME / ROOTS[a] if a in ROOTS else None
        if r and r not in out:
            out.append(r)
    return out


def ensure(agents_list: list[str] | None = None, src: Path | None = None) -> list[str]:
    """Link every skill into every installed agent's folder; returns one line per change or conflict."""
    if agents_list is None:
        from .agents import agents_installed
        agents_list = agents_installed()
    src = Path(src or config.SKILLS)
    names = skill_names(src)
    if not names:
        return []
    report = []
    for root in roots(agents_list):
        if _same(root, src):  # the whole folder already is the skills folder
            continue
        try:
            root.mkdir(parents=True, exist_ok=True)
            entries = list(root.iterdir())
        except OSError as e:
            report.append(f"{root}: {e}")
            continue
        for p in entries:  # a skill deleted from the source: drop Deck's link to it
            if p.name not in names and _ours(p, src) and not (src / p.name).exists():
                try:
                    _unlink(p)
                    report.append(f"removed {p}")
                except OSError as e:
                    report.append(f"{p}: {e}")
        for name in names:
            link, target = root / name, src / name
            if _same(link, target):
                continue
            if os.path.lexists(link):
                if not _ours(link, src):
                    report.append(f"skipped {link}: already there and not Deck's")
                    continue
                _unlink(link)
            try:
                _make_link(link, target)
                report.append(f"linked {link}")
            except OSError as e:
                report.append(f"{link}: {e}")
    return report


def ensure_quietly() -> None:
    """For server start and agent launch: a skills problem never stops a session."""
    try:
        ensure()
    except Exception:
        pass


# ---- the shelf's Deck files → Skills: each skill's text files, to read and edit in the reader ----
TEXT = {".md", ".txt", ".py", ".sh", ".ps1", ".toml", ".json", ".yml", ".yaml", ".tex", ".sty", ".cls", ".bib", ".js", ".html",
        ".css", ""}
SKIP = {"__pycache__", "node_modules", ".venv", "venv"}
MAX_FILES = 60  # per skill: a skill that is a whole repo (reports) lists its first 60
MAX_BYTES = 2_000_000


def _text_files(root: Path) -> list[str]:
    """A skill's text files, relative and with /, SKILL.md first, then by depth and name; hidden folders skipped."""
    out = []
    for dirpath, dirs, names in os.walk(root, followlinks=True):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP)
        rel = Path(dirpath).relative_to(root)
        if len(rel.parts) > 3:
            dirs[:] = []
            continue
        out += [(rel / n).as_posix() for n in names if not n.startswith(".") and Path(n).suffix.lower() in TEXT]
    out.sort(key=lambda f: (f != "SKILL.md", f.count("/"), f.lower()))
    return out[:MAX_FILES]


def _description(md: Path) -> str:
    """The description in SKILL.md's front matter (a plain or a folded `>-` value), one line."""
    try:
        lines = md.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    out, on = [], False
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        if on:
            if ln[:1] in (" ", "\t"):
                out.append(ln.strip())
                continue
            break
        if ln.startswith("description:"):
            v = ln.split(":", 1)[1].strip()
            if v and v not in (">", ">-", "|", "|-"):
                return v.strip("'\"")
            on = True
    return " ".join(out)


def listing(src: Path | None = None) -> dict:
    """Every skill in the skills folder: {path, skills: [{name, description, files}]}."""
    src = Path(src or config.SKILLS)
    return {"path": str(src), "skills": [{"name": n, "description": _description(src / n / "SKILL.md"),
                                          "files": _text_files(src / n)} for n in skill_names(src)]}


def resolve(rel: str, src: Path | None = None) -> Path:
    """A skill's file from "name/path": inside that skill's folder (which may itself be a link elsewhere)."""
    src = Path(src or config.SKILLS)
    name, _, rest = str(rel).replace("\\", "/").partition("/")
    if name not in skill_names(src) or not rest:
        raise ValueError("not a skill file")
    root = (src / name).resolve()
    p = (root / rest).resolve()
    if root not in p.parents or p.suffix.lower() not in TEXT or not p.is_file() \
            or any(x.startswith(".") for x in p.relative_to(root).parts):
        raise ValueError("not a skill file")
    return p


def read(rel: str, src: Path | None = None) -> str:
    p = resolve(rel, src)
    if p.stat().st_size > MAX_BYTES:
        raise ValueError("too big to show")
    return p.read_bytes().decode("utf-8", errors="replace")


def write(rel: str, text: str, src: Path | None = None) -> None:
    """Save a skill's file, keeping its line ends (CRLF stays CRLF). No commit: the skills folder is the user's repo."""
    p = resolve(rel, src)
    if len(text) > MAX_BYTES:
        raise ValueError("too long")
    crlf = b"\r\n" in p.read_bytes()[:65536]
    text = text.replace("\r\n", "\n")
    p.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))


if __name__ == "__main__":
    lines = ensure()
    print("\n".join(lines) if lines else f"skills up to date ({len(skill_names(config.SKILLS))} in {config.SKILLS})")
    sys.exit(0)
