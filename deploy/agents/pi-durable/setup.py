"""Install or update durable Pi (pi-durable, experimental): pi's own source at the installed pi's version, with Deck's
patch and bridge-main.ts, run on the Claude bridge. Used by install.sh (Linux) and windows-setup.py. Re-runnable: it
clones and builds (~320 MB) only when pi's version changes, and a build that fails keeps the previous one. Where pi,
pi-claude-bridge is missing it removes pi-durable, which hides Deck's Durable Pi switch. Where the system Node can't
run TypeScript (older than 22.19, or a distro build without it, like Ubuntu's) it fetches the official Node 22 into
~/.pi/durable-node for pi-durable alone.
    python3 deploy/agents/pi-durable/setup.py
"""
import hashlib
import io
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()
SRC = Path(os.environ.get("PI_DURABLE_SRC", HOME / ".pi" / "durable-src"))
BIN = HOME / ".local" / "bin"
NODE = HOME / ".pi" / "durable-node"  # official Node, only where the system one can't strip TypeScript
ENV = dict(os.environ)  # PATH gains NODE/bin when it's used, so npm and its scripts run on it too
WINDOWS = sys.platform == "win32"
LAUNCHERS = ["pi-durable", "pi-durable.mjs", *(["pi-durable.cmd"] if WINDOWS else [])]
NO_WINDOW = 0x08000000 if WINDOWS else 0  # no console flashing up for each command


def run(*args, cwd: Path | None = None) -> str:
    r = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True, creationflags=NO_WINDOW, env=ENV)
    if r.returncode:
        cmd = " ".join([Path(str(args[0])).stem, *map(str, args[1:3])])
        raise RuntimeError(f"{cmd}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout.strip()


def tool(name: str, *extra: Path) -> str:
    found = shutil.which(name) or next((str(p) for p in extra if p.exists()), None)
    if not found:
        raise RuntimeError(f"{name} not found")
    return found


def remove(path: Path) -> None:
    def writable(func, p, _exc):  # git's pack files are read-only on Windows
        os.chmod(p, 0o700)
        func(p)
    if path.exists():  # onexc is 3.12+ (Windows Deck runs uv's 3.12); Linux needs no handler and may run 3.11
        shutil.rmtree(path, **({"onexc": writable} if WINDOWS else {}))


def runs_typescript(node: str) -> bool:
    """pi's source runs on Node's own type stripping (22.19+, and not in builds without it, e.g. Ubuntu's nodejs)."""
    version = run(node, "--version").lstrip("v")
    return tuple(map(int, version.split(".")[:2])) >= (22, 19) and run(node, "-p", "process.features.typescript") != "false"


def official_node() -> str:
    """The latest official Node 22 in NODE (Linux and macOS), checked against nodejs.org's SHASUMS256.txt."""
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if WINDOWS or not arch:
        raise RuntimeError(f"no official Node download for {sys.platform}/{platform.machine()}")
    node = NODE / "bin" / "node"
    if node.exists() and runs_typescript(str(node)):
        return str(node)
    base = "https://nodejs.org/dist/latest-v22.x/"
    sums = urllib.request.urlopen(base + "SHASUMS256.txt", timeout=60).read().decode()
    plat = "darwin" if sys.platform == "darwin" else "linux"
    digest, name = next(line.split() for line in sums.splitlines() if line.endswith(f"-{plat}-{arch}.tar.xz"))
    print(f"pi-durable: fetching {name} (the system Node can't run TypeScript)", flush=True)
    data = urllib.request.urlopen(base + name, timeout=300).read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise RuntimeError(f"{name}: checksum mismatch")
    new = NODE.with_name(NODE.name + ".new")
    remove(new)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as tar:
        tar.extractall(new, filter="tar") if hasattr(tarfile, "data_filter") else tar.extractall(new)
    remove(NODE)
    (new / name.removesuffix(".tar.xz")).rename(NODE)
    remove(new)
    return str(node)


def build(version: str, git: str, npm: str) -> None:
    """pi's source at `version`, built beside the current one and swapped in only once it builds and takes the patch."""
    new = SRC.with_name(SRC.name + ".new")
    remove(new)
    run(git, "clone", "-q", "--depth", "1", "--branch", f"v{version}", "https://github.com/earendil-works/pi", new)
    run(npm, "ci", "--no-audit", "--no-fund", cwd=new)
    run(npm, "run", "hydrate:model-data", cwd=new)
    run(git, "-C", new, "apply", "--check", HERE / "durable.patch")
    remove(SRC)
    new.rename(SRC)
    (SRC / ".deck-pi-version").write_text(version + "\n")


def main() -> None:
    pi = tool("pi", HOME / ".pi" / "agent" / "bin" / ("pi.cmd" if WINDOWS else "pi"))
    version = run(pi, "--version").splitlines()[-1].strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"unexpected pi --version: {version}")
    node = shutil.which("node")
    if not (node and runs_typescript(node)):  # pi's minimum; also covers registerHooks and type stripping
        node = official_node()
        ENV["PATH"] = str(Path(node).parent) + os.pathsep + ENV.get("PATH", "")
    elif NODE.exists():
        remove(NODE)  # the system Node can do it now
    if not (HOME / ".pi" / "agent" / "npm" / "node_modules" / "pi-claude-bridge").is_dir():
        raise RuntimeError("pi-claude-bridge is not installed (pi install npm:pi-claude-bridge)")
    git = tool("git")
    npm = shutil.which("npm", path=ENV["PATH"]) or tool("npm")
    stamp = SRC / ".deck-pi-version"
    built = stamp.read_text().strip() if stamp.exists() else None
    if built != version:
        print(f"pi-durable: building pi {version} from source in {SRC}", flush=True)
        try:
            build(version, git, npm)
        except RuntimeError as e:
            if built is None:
                raise
            print(f"pi-durable: kept pi {built}; pi {version} failed: {e}")
            return
    # Every run: a Deck update's patch and bridge-main.ts land without a rebuild.
    run(git, "-C", SRC, "checkout", "-q", "--", ".")
    run(git, "-C", SRC, "apply", HERE / "durable.patch")
    shutil.copy(HERE / "bridge-main.ts", SRC / "packages" / "coding-agent" / "src" / "experimental" / "durable")
    BIN.mkdir(parents=True, exist_ok=True)
    for name in LAUNCHERS:
        shutil.copy(HERE / name, BIN / name)
    (BIN / "pi-durable").chmod(0o755)
    print(f"pi-durable: ready (pi {version})")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as e:
        for name in LAUNCHERS:
            (BIN / name).unlink(missing_ok=True)
        print(f"pi-durable: not installed: {e}")
        sys.exit(1)
