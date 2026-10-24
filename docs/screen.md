# Screen tab setup

The Screen tab streams a host's desktop with noVNC. Deck doesn't run a VNC (Virtual Network Computing) server itself: you provide one on loopback, bridge it to a WebSocket with websockify, and tell Deck where the bridge is.

| Piece | Address | Provided by |
|---|---|---|
| VNC server | `127.0.0.1:5900` | You (TightVNC, x11vnc, wayvnc) |
| websockify | `127.0.0.1:6080` → `127.0.0.1:5900` | You |
| Deck's bridge | `/vnc/` on Deck's origin → `127.0.0.1:6080` | Deck (`server.vnc_bridge`, paired devices only) |
| Config | `~/.config/deck/vnc.json` (override: `DECK_VNC_FILE`) | You |

## Setup

1. **Run a VNC server on loopback only**, with a VNC password.
   - Windows (including Windows Subsystem for Linux (WSL) hosts): TightVNC 2.8 as a service, `winget install GlavSoft.TightVNC`. As a service it also shows the secure desktop (User Account Control (UAC) prompts, lock screen). In its settings:
     1. Turn off the HTTP viewer (port 5800).
     2. Allow loopback connections only.
     3. Don't add a firewall exception.
   - Linux desktop: `x11vnc -localhost` or `wayvnc 127.0.0.1`.
2. **Run websockify**:
   ```bash
   pip install websockify
   websockify 127.0.0.1:6080 127.0.0.1:5900
   ```
   Start it at logon: on Windows, a Task Scheduler task running `pythonw -m websockify 127.0.0.1:6080 127.0.0.1:5900`; on Linux, a systemd user unit.
3. **Write `~/.config/deck/vnc.json`** with mode 600:
   ```json
   {"password": "<VNC password>", "path": "/vnc/websockify", "bridge": "127.0.0.1:6080"}
   ```

The Screen tab appears once the file exists (Deck reads it on each `/api/screen` request; no server restart). Reload the page if it doesn't show.

| Key | Default | Meaning |
|---|---|---|
| `password` | `""` | VNC password, sent to the page for noVNC |
| `path` | `/vnc/websockify` | WebSocket path on Deck's origin |
| `bridge` | none | websockify's address. With it, Deck serves the stream at `/vnc/` itself, so the tab works on every way in, the iroh tunnel included |

Other hosts' desktops are reachable through `/peer/<host>/vnc/` (WebSocket only).

### Without `bridge`

Older setups serve websockify on Deck's origin some other way, for example:

```bash
tailscale serve --bg --set-path /vnc http://127.0.0.1:6080
```

Over the iroh tunnel the tab is then hidden (`/api/screen` reports `enabled: false`), because the tunnel can't reach that path.

## Phone screen (WSL hosts)

A portrait virtual monitor sized to the phone, toggled from the Screen bar (**Phone**). Needs a WSL host: Deck drives it through Windows PowerShell 5.1 at `/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe` (`deck/win/phonescreen.ps1`).

One-time setup on Windows:

1. Install the [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver) (MttVDD, 25.7+):
   1. `winget install VirtualDrivers.Virtual-Display-Driver`
   2. As admin, add the driver with its `devcon`: `devcon install MttVDD.inf Root\MttVDD`.
2. In `C:\VirtualDisplayDriver\vdd_settings.xml`, set one monitor and list portrait modes, for example 1080×1620, 1080×1800, 1080×1920, 1080×2160, 1080×2400.

Deck then, without admin:

- Attaches the monitor to the right of the others at the mode closest to the phone's frame, sets it to 250% scaling, and detaches it when **Phone** is turned off.
- **Windows** lists the desktop's app windows; tap one to move it onto the phone screen maximized, again to send it back. Elevated windows (Task Manager) can't be moved.

Routes: `/api/screen/phone`, `/api/screen/windows`.

To remove the driver: Device Manager → Display adapters → Virtual Display Driver → Uninstall.
