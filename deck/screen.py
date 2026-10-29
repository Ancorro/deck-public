"""The Screen tab: the VNC server and its bridge, and the phone screen (a virtual monitor on a Windows or WSL host)."""
import json
import os
import socket
import subprocess
from pathlib import Path

from . import config

# ---- screen -------------------------------------------------------------------

def screen() -> dict:
    """Screen tab config. The VNC server is loopback-only; the bridge is served on this origin at `path`."""
    try:
        cfg = json.loads(Path(config.VNC_FILE).read_text())
    except (FileNotFoundError, ValueError):
        return {"enabled": False}
    # bridge: websockify's local address ("127.0.0.1:6080"); Deck then serves it at /vnc/ itself (server.vnc_bridge),
    # so the tab works over iroh too. Without it the bridge lives in `tailscale serve`, which the tunnel can't reach.
    return {"enabled": True, "path": cfg.get("path", "/vnc/websockify"), "password": cfg.get("password", ""),
            "bridge": bool(cfg.get("bridge"))}


def vnc_bridge() -> str:
    """websockify's local address from vnc.json, or "" (none: the Screen tab isn't served over iroh)."""
    try:
        return str(json.loads(Path(config.VNC_FILE).read_text()).get("bridge", ""))
    except (FileNotFoundError, ValueError):
        return ""


SCHTASKS = "/mnt/c/Windows/System32/schtasks.exe"
VNC_HOOK_SECONDS = 120  # how long a vnc.json vnc_on/vnc_off command may take (it may start a whole desktop)


def vnc_service(state: str) -> bool:
    """Start ("on") or stop ("off") the VNC server, so it runs only while a page streams (user: "turn off the vnc when i
    am not streaming screen"): vnc.json's `vnc_on`/`vnc_off` command when it has one (any host; an argument list, run
    without a shell), else the admin tasks "Deck VNC on/off" (win/vnc-setup.ps1). False: neither is set up."""
    try:
        hook = json.loads(Path(config.VNC_FILE).read_text()).get(f"vnc_{state}")
    except (FileNotFoundError, ValueError):
        hook = None
    if isinstance(hook, list) and hook and all(isinstance(a, str) for a in hook):
        try:
            subprocess.run([os.path.expanduser(a) for a in hook], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=VNC_HOOK_SECONDS, **config.OWN_CONSOLE)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return True
    exe = os.path.join(os.environ.get("SystemRoot", "C:/Windows"), "System32", "schtasks.exe") if config.WINDOWS else SCHTASKS
    if not os.path.exists(exe):
        return False
    r = subprocess.run([exe, "/run", "/tn", f"Deck VNC {state}"], capture_output=True, text=True, timeout=15,
                       **config.OWN_CONSOLE)
    return r.returncode == 0


def vnc_listening(addr: str = "127.0.0.1:5900") -> bool:
    host, _, port = addr.rpartition(":")
    try:
        socket.create_connection((host, int(port)), timeout=0.5).close()
        return True
    except OSError:
        return False


POWERSHELL ="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
PHONESCREEN = Path(__file__).parent / "win" / "phonescreen.ps1"


def phone_screen(action: str = "status", w: int = 0, h: int = 0) -> dict | list | None:
    """Screen tab's phone screen: a portrait virtual monitor (Virtual Display Driver) on a Windows or WSL host.
    None when this host has none. Returns {on, ox, oy, x, y, w, h, modes} in physical (VNC) pixels. "windows" lists
    the desktop's app windows [{hwnd, title, app, phone}]; "move" (w = hwnd, h = 1 to the phone, 0 back) moves one."""
    if config.WINDOWS:  # Deck on Windows itself: the same PowerShell 5.1, by its Windows path
        root = os.environ.get("SystemRoot", "C:/Windows")
        ps, win = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), str(PHONESCREEN)
    else:
        ps = POWERSHELL
        if not os.path.exists(ps):
            return None
        win = subprocess.run(["wslpath", "-w", str(PHONESCREEN)], capture_output=True, text=True).stdout.strip()
    args = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", win, action]
    if action in ("on", "move"):
        args += [str(int(w)), str(int(h))]
    r = subprocess.run(args, capture_output=True, text=True, timeout=30, **config.OWN_CONSOLE)
    out = r.stdout.strip().splitlines()
    try:
        return json.loads(out[-1])
    except (IndexError, ValueError):
        if action == "status":
            return None
        said = (r.stderr or r.stdout).strip()
        raise RuntimeError(said.splitlines()[0] if said else "phone screen failed") from None


