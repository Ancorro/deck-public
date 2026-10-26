# Installing Deck

Quick start: [README](../README.md#get-started). This page is the reference.

| Platform | Runs Deck with | Section |
|---|---|---|
| Ubuntu 24.04+ (incl. Windows Subsystem for Linux, WSL) | `deck` and `deck-iroh` systemd user services | [Linux](#linux) |
| Windows, no WSL | PC app (`deck.exe`) with a bundled MSYS2 (Minimal SYStem 2) tmux | [PC app](#pc-app) |
| Android 7.0+ phone | Phone app (Android Package, APK) or any browser | [Phone app](#phone-app) |

Ways in: the [iroh tunnel](#iroh-tunnel) (phone app, any network) and, optionally, Tailscale (`tailscale serve`). Deck itself listens on 127.0.0.1 only. Access rules: [security.md](security.md).

## Linux

### Install

Requires Ubuntu 24.04 or later (Python 3.11+; the installer exits otherwise) and sudo.

1. Clone the repo. Any folder works; the installer uses the checkout it runs from.
   ```bash
   git clone https://github.com/Ancorro/deck-public ~/deck
   ```
2. Run the installer. Optional argument: this host's Tailscale URL.
   ```bash
   bash ~/deck/deploy/install.sh                                # iroh only
   bash ~/deck/deploy/install.sh https://<host>.<tailnet>.ts.net  # also Tailscale
   ```
3. Do what the installer's "Left for you" list prints:
   - `claude` (log in), `codex login`, `pi` then `/login` (or `/model`).
   - `deck-pair`, then scan the QR code with the phone and approve it.
   - WSL: keep WSL running, see [WSL](#wsl).
   - With a URL: run `sudo tailscale serve --bg 8787` (on WSL: `tailscale serve --bg 8787` in Windows PowerShell); give the host a `deck_url` in the machine notes' `inventory.toml`; copy `~/.config/deck/host-key` (mode 600) from another of your hosts, or run `deck-pair` here first.

The installer is re-runnable. Installer environment variables:

| Variable | Effect |
|---|---|
| `DECK_NOTES_REPO=<git url>` | Clone a shared machine notes repo to `~/deck-notes` and set `DECK_NOTES` (only if `DECK_NOTES` isn't set yet). |
| `DECK_BUILD_TUNNEL=1` | Build `deck-iroh` with Rust instead of downloading it. |

### What `install.sh` does

1. Checks for Python 3.11+.
2. `apt-get install`: `git tmux python3-aiohttp python3-pytest python3-pytest-aiohttp python3-pytest-asyncio python3-qrcode nodejs npm build-essential`; `loginctl enable-linger $USER` (user services run without a login session).
3. Updates the checkout (`git pull --ff-only`, if it is a git checkout) and sets `core.autocrlf false`. On WSL, reuses Windows' Git Credential Manager if no credential helper is set.
4. Sets global git `user.name` / `user.email` to `$USER` / `$USER@<hostname>`, only if unset (Deck commits to the notes and key-builder repos).
5. Installs agents if missing: Claude Code (`claude.ai/install.sh`), Codex (`npm install -g @openai/codex`, npm prefix set to `~/.local`), Pi (`pi.dev/install.sh`), then Durable Pi (`deploy/agents/pi-durable/setup.py`, experimental; skips with a reason).
6. Creates `~/work` and the key-builder workspace `~/work/deck-keys` (a git repo), marks both trusted for the agents (`deploy/agents/trust.py`), and links `deck-key`, `deck-notes`, `deck-pair` into `~/.local/bin`.
7. Writes `~/.config/deck/env`: `DECK_HOST_NAME` (if unset; from the URL's first label, else `hostname`), `DECK_ORIGINS` (the URL, if given), `DECK_NOTES` (see above). Other lines are kept. Runs `deck-notes init`.
8. Runs `deploy/agents/agent_context.py` (Claude: spinner tips off; a pointer to the machine notes when they have agent instructions) and `deploy/agents/agent-statusline.py` (Claude and Codex status lines; Claude's feeds Deck's status chips).
9. Installs, enables and restarts `deck.service`, then checks `http://127.0.0.1:8787/`.
10. Installs `deck-iroh` to `~/.local/bin`: downloads `deck-iroh-linux-$(uname -m)` and `SHA256SUMS` from the latest published release and checks the hash; if that fails (no release, other architecture) or `DECK_BUILD_TUNNEL` is set, installs Rust (rustup, minimal profile) if missing and builds it. Then installs, enables and restarts `deck-iroh.service`.

### Services

Both are systemd user units in `~/.config/systemd/user/`.

| Unit | Runs | Settings |
|---|---|---|
| `deck` | `/usr/bin/python3 -m deck.server` | `WorkingDirectory` = the checkout (rewritten by `install.sh`; default `%h/deck`); `EnvironmentFile=-%h/.config/deck/env`; `Restart=on-failure`; `KillMode=process` (a restart kills only Python, so tmux and its sessions survive). |
| `deck-iroh` | `%h/.local/bin/deck-iroh serve` | `After=deck.service`; `Restart=always`; `RestartSec=5`. |

### Update

| Change | Command |
|---|---|
| Server or page code | `git -C ~/deck pull && systemctl --user restart deck` (sessions survive). |
| `deck-iroh` | Re-run `install.sh`, or `(cd ~/deck/tunnel && cargo build --release) && install -m 755 ~/deck/tunnel/target/release/deck-iroh ~/.local/bin/ && systemctl --user restart deck-iroh`. |
| Every host | `~/deck/deploy/update-all` (below). |

`deploy/update-all` sends `POST /api/update` with the host key (`Authorization: Bearer <host-key>`) to every host in the machine notes inventory (`inventory.toml` entries with `deck_url`) and every linked PC; this host goes direct to `http://127.0.0.1:$DECK_PORT`. Each host runs `git pull --ff-only` and restarts if its commit changed. Exit status is 1 if any host failed. No app build is needed for server or page changes.

`install.sh` downloads `deck-iroh` from `releases/latest`, which only resolves published releases; a draft release (see [Releases](#releases)) is skipped.

## WSL

WSL stops when idle. To keep it running:

1. Task Scheduler, current user, trigger "At log on", action: `conhost.exe --headless wsl.exe -d Ubuntu --exec /bin/sleep infinity`.
2. In `%USERPROFILE%\.wslconfig`, set `autoMemoryReclaim=dropCache` under `[experimental]` so the always-on virtual machine returns idle cache to Windows.

Agents in WSL use their own Linux Claude Code and Codex logins. Windows files are under `/mnt/c/`.

## PC app

`deck.exe` (Go, `desktop/`) keeps this PC's Deck server and iroh tunnel running. It has no window or tray; the UI is `http://127.0.0.1:8787/?pc` in the default browser, which this PC opens without pairing. Built for Windows; it also builds for Linux and macOS.

### Install on Windows

Prerequisite: [Git for Windows](https://git-scm.com) (for the clone; first-run setup installs it with winget if missing, but the clone comes first).

1. Clone the server code. Clone before unzipping: the zip's `server\msys` would make the folder non-empty.
   ```bat
   git clone https://github.com/Ancorro/deck-public %LOCALAPPDATA%\Deck\server
   ```
2. Download `deck-<tag>-windows.zip` and `SHA256SUMS` from a [release](#releases). Check the hash: `Get-FileHash deck-<tag>-windows.zip` (PowerShell), compare with `SHA256SUMS`.
3. Unzip into `%LOCALAPPDATA%\Deck` (it adds `deck.exe`, `deck-iroh.exe`, `uv.exe`, `server\msys`, `NOTICE`, `LICENSE`).
4. Choose the server version:
   - Matching the zip: `git -C %LOCALAPPDATA%\Deck\server checkout <tag>`. This is a detached HEAD, so `/api/update` (`git pull --ff-only`) fails until you switch back.
   - Following `main` (updates through `/api/update` and `deploy/update-all`): stay on `main`.
5. `%LOCALAPPDATA%\Deck\deck.exe install` (logon autostart, no admin).
6. `%LOCALAPPDATA%\Deck\deck.exe open`. First run sets up the user (below) and takes a few minutes.
7. Pair the phone: `deck open devices-pair`, or Settings → More → Devices → Pair a device.

Without a release, use a CI (continuous integration) run's artifacts instead: `deck-windows` (`deck.exe`, `uv.exe`, `server/msys`) and `deck-iroh-windows`, see [Build](#build).

Layout of `%LOCALAPPDATA%\Deck`:

| Path | What |
|---|---|
| `deck.exe` | PC app |
| `deck-iroh.exe` | iroh tunnel |
| `uv.exe` | Runs the server with its own Python |
| `server\` | Git checkout of this repo |
| `server\msys\` | MSYS2 kit (gitignored), about 30 MB |

### Commands

| Command | Effect |
|---|---|
| `deck` / `deck open [fragment]` | Open Deck in the default browser, starting `deck run` first if Deck isn't up (waits up to 90 s). `deck open devices-pair` opens a new pairing code. |
| `deck window [fragment]` | Windows, experimental: the same in a WebView2 window; falls back to the browser without the WebView2 runtime. |
| `deck run` | Start and supervise the server and tunnel (what autostart runs). |
| `deck stop` | Stop `deck run`, the server and the tunnel; on Windows also `tmux kill-server` (ends the sessions). |
| `deck install` / `deck uninstall` | Windows only: set or delete the `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value `Deck` = `conhost.exe --headless deck.exe run` (no console window, no admin). |

### What `deck run` does

1. Exits if another `deck run` is running (pid file).
2. If Deck already answers on `127.0.0.1:8787` (for example the `deck` service), waits until it has been gone for 5 s in a row, then takes over.
3. Windows: runs `deploy/windows-setup.py` once per app version (stamp: `~/.config/deck/app-version`). On failure Deck starts anyway and the next start retries.
4. Starts `deck-iroh serve --key ~/.config/deck/iroh.key --to 127.0.0.1:<port+1> --id-file ~/.config/deck/iroh.id`, if `deck-iroh` is found (next to `deck.exe`, `~/.local/bin`, `~/.cargo/bin`, or `PATH`).
5. Starts the server from `server\` next to `deck.exe` (or the repo, when run from `desktop/`): `uv run --no-project --python 3.12 --with aiohttp --with qrcode [--with pywinpty] python -m deck.server`. uv fetches Python 3.12 and the packages on first run.
6. Restarts either process when it exits: after 1 s, doubling to at most 60 s while it keeps failing within a minute.

Child environment: the app's environment minus `TMUX`, `TMUX_PANE`, `CLAUDECODE`; plus `PYTHONUTF8=1`, the lines of `~/.config/deck/env`, and for the server `DECK_PORT`, `DECK_TUNNEL_PORT` (port + 1), `DECK_SUPERVISED=1`.

The server stops itself when Windows' asyncio closes a listening socket after a failed accept (WinError 64); `deck run` then restarts it.

| File | What |
|---|---|
| `%LOCALAPPDATA%\deck\server.log` | Server, tunnel and app log; rotated to `server.log.old` past 10 MB |
| `%LOCALAPPDATA%\deck\deck.pid` | `deck run`'s pid |
| `%LOCALAPPDATA%\deck\tmux\` | `TMUX_TMPDIR` (tmux sockets) |
| `~/.config/deck/app-version` | Version that last ran `windows-setup.py` |

### First-run setup (`deploy/windows-setup.py`)

1. Git for Windows: `winget install --id Git.Git` if missing (Claude Code needs its bash); sets git `user.name` / `user.email` to `%USERNAME%` / `%USERNAME%@<host>` if unset.
2. Agents if missing: Claude Code (`claude.ai/install.ps1`), Codex (`npm install -g @openai/codex`, only if npm exists), Pi (`pi.dev/install.ps1`); then Durable Pi.
3. `DECK_HOST_NAME` in `~/.config/deck/env` (if unset; lowercased `%COMPUTERNAME%`), `~/work` (trusted), `deck.notes init` (if git exists), `deploy/agents/agent_context.py`.

### How Windows runs sessions

- The server runs natively under uv's Python with `pywinpty`. Sessions run in the MSYS2 tmux and bash in `server\msys` (override: `DECK_MSYS`), built by `desktop/msys-kit.sh`: tmux, bash, coreutils, grep, sed, gawk, findutils, less, which, ncurses, diffutils and the DLLs they load. `cygwin-console-helper.exe` and `getprocaddr64.exe` give native agents (Claude Code, Codex) a real console and Ctrl+C.
- `deck/core.py` sets `HOME` to the Windows profile, keeps Windows' `PATH` (`MSYS2_PATH_TYPE=inherit`), puts `TMUX_TMPDIR` under `%LOCALAPPDATA%\deck`, turns off MSYS argument globbing (`MSYS=noglob`) and path conversion for path-like arguments, and quotes tmux arguments for MSYS (`msys_command`).
- The phone's terminal is a tmux client in a ConPTY (Windows pseudo console). A resize starts a new client at the new size, because resizing a ConPTY can hang MSYS2's tmux.

### Updating the PC app

- Server and page: `/api/update` (and `deploy/update-all`) pulls `server\` and stops the server (`DECK_SUPERVISED`); `deck run` starts the new code. Sessions are kept.
- `deck.exe` or `deck-iroh.exe`, keeping sessions:
  1. `taskkill /T /F /IM deck.exe` (ends the app, server and tunnel; tmux isn't in that process tree).
  2. Copy the new files into `%LOCALAPPDATA%\Deck`.
  3. `deck run`.

### Not yet supported

- No installer, no code signing, no self-update of `deck.exe` or `deck-iroh.exe`.
- macOS: tmux isn't bundled; install Homebrew's `tmux`.
- Windows: Claude's status chips (they find their pane through `/proc`), the key builder's `deck-key` tool, Phone screen (needs a WSL host, see [screen.md](screen.md#phone-screen-wsl-hosts)).

### Testing next to a real install

Run a second Deck without touching the real one:

1. `DECK_PORT=8790`.
2. Unset `TMUX` and `TMUX_PANE` (inside a Deck pane, `$TMUX` points at the real tmux server).
3. Point `HOME`, `USERPROFILE` and `DECK_DATA` at a temp folder (the tunnel then gets its own key).
4. Point `LOCALAPPDATA` at a short temp path: `core.py` puts `TMUX_TMPDIR` under it (Unix socket paths are limited to about 108 characters) and `deck.exe` its log and pid file.
5. To skip first-run setup, write the temp `~/.config/deck/app-version` first: its installers (Pi's, for one) find the real profile, not the temp one.

## Phone app

Capacitor 8 Android shell in `app/`. Package `dev.deck.app`; `minSdkVersion` 24 (Android 7.0), `targetSdkVersion` 36; 64-bit ARM (`arm64-v8a`) only; portrait only.

- **Host list** (`app/www/index.html`): Tailscale URLs (`https://…ts.net`, typed in) and iroh hosts (added from a pairing link, see [iroh tunnel](#iroh-tunnel)). A local build also seeds it from the building host's host list (`hosts.json`, gitignored); a release build starts empty. The app opens the Deck's first node straight away; the list is Settings → More → Hosts.
- **Pages**: the app opens the chosen host's live web UI, so UI changes ship with the host, not the app.
- **Trust**: only saved hosts and open tunnel ports load in the app and get its plugins (`HostsPlugin`, `MainActivity.guard`). A link to an unsaved host (`deck://pair`, a `ts.net` link) opens the host list, which asks before adding it. Details: [security.md](security.md#phone-app).
- **Native code** (`app/native/`): `MainActivity` (plugins on host pages; handles `deck://pair` and `https://*.ts.net` links), `DictationPlugin` (on-device speech), `TunnelPlugin` + `Tunnel` (iroh), `HostsPlugin` (trusted hosts).
- **Permissions** added by `app/build.sh`: `RECORD_AUDIO` (dictation), `CAMERA` (QR scanning); cleartext HTTP to `127.0.0.1` only.

### Get the app

- Release: install `deck-<version>.apk` from a [release](#releases) (present only when the release was signed).
- Your own build: `app/build.sh` on a host, then Settings → More → App on the phone (paired devices only). A release's APK copied to `deck/static/deck.apk` on a host works the same way.
- Updating the installed app: Settings → More → App in the app shows a 10-minute link; open it in the phone's browser and install the download over the app (an app built after 2026-10-26 opens the browser itself).

A release APK and a local debug build are signed with different keys; switching between them requires uninstalling first.

### Local build

Needs the [phone app build dependencies](#dependencies).

1. `app/build.sh`: regenerates `android/`, builds the Rust library `libdeck_tunnel.so` and a debug APK, and copies it to `deck/static/deck.apk` (served at `/static/deck.apk` to paired devices only, since it carries your host list). `DECK_ABIS="arm64-v8a x86_64"` adds the emulator ABI (needs `rustup target add x86_64-linux-android`).
2. Optional: `app/push.sh` builds and installs it over adb (Android Debug Bridge, Wireless debugging). Phone address: `DECK_PHONE` or `~/.config/deck/phone` (`host` or `host:port`, default port 5555; after a phone restart it finds the new port with nmap). One-time phone setup: Developer options → Wireless debugging → pair.

### Release build

With `DECK_APP_VERSION` set (CI sets it to the tag), `app/build.sh` makes a public build:

- Empty host list (hosts come from pairing links).
- `versionName` = the version without `v`; `versionCode` = major × 10000 + minor × 100 + patch (`0.2.0` → 200), so releases install over each other.
- Output `dist/deck-<version>.apk`, not published at `/static/deck.apk`.
- Signed with `DECK_KEYSTORE` (password `DECK_KEYSTORE_PASS`, alias `DECK_KEY_ALIAS`, default `deck`) when set; otherwise debug-signed.

CI signs when the repo has the secrets `DECK_KEYSTORE_B64` (keystore, base64) and `DECK_KEYSTORE_PASS` (store and key password, alias `deck`). Without them the APK is debug-signed and left out of the release.

To create the signing key once:

1. `keytool -genkeypair -keystore deck.keystore -alias deck -keyalg RSA -keysize 4096 -validity 36500`
2. `base64 -w0 deck.keystore | gh secret set DECK_KEYSTORE_B64`
3. `gh secret set DECK_KEYSTORE_PASS`
4. Back the keystore up offline. Without it, no update installs over the published app. (`*.keystore` is gitignored.)

## iroh tunnel

Reaches a host's Deck from the phone app on any network, with no Tailscale, account or port forwarding. [iroh](https://iroh.computer) 1.x dials a host by its public key: n0's discovery service finds it, and the connection goes direct (hole punching) when it can and through n0's public relays when it can't. iroh encrypts end to end (QUIC with TLS, Transport Layer Security) either way.

| Side | Component | Behaviour |
|---|---|---|
| Host | `deck-iroh serve` (Rust, `tunnel/`) | Secret key in `~/.config/deck/iroh.key` (created on first run; mode 0600 on Linux, default permissions on Windows). Public key printed and written to `~/.config/deck/iroh.id`. Each incoming stream (ALPN, application-layer protocol negotiation, `deck/tcp/1`; one stream per TCP connection, HTTP and WebSocket alike) is piped to the tunnel port, default `127.0.0.1:8788`. Options: `--key FILE`, `--to ADDR`, `--id-file FILE`. |
| Phone | `libdeck_tunnel.so` (same crate, via JNI, Java Native Interface: `Tunnel.open(key)` → port) | Listens on `127.0.0.1:<port>` and forwards each connection over one iroh connection to the host, reconnecting when it drops. Port = 20000 + (first two key bytes mod 20000), else a free port, so it is stable across launches; the page's origin and pairing cookie belong to `http://127.0.0.1:<port>`. |
| Testing | `deck-iroh connect <host key>` | Prints a local URL that reaches that host's Deck. |

Run by the `deck-iroh` service on Linux and by `deck run` in the PC app.

**Tunnel port rules** (`deck/server.py`, `via_tunnel`): a request on the tunnel port is never treated as local and always needs a paired device, even before the first device is approved. Anyone can dial the key; only paired devices get past the pairing screen. A PC with hosting turned off answers tunnel requests with 403.

### Pairing over iroh

1. On the host: Settings → More → Devices → Pair a device, or `deck-pair [url]`. With `iroh.id` present the link is `https://<host>/#pair=<code>&iroh=<host key>&name=<host>`.
2. On the phone, either:
   - Deck app → **Scan a pairing code** (camera and bundled jsQR, `app/www/vendor/`; Android's BarcodeDetector needs a Play Services download), or paste the link into the app's Add box. The app saves the host by its key and claims over iroh.
   - Phone camera: opens the link in the browser (cameras don't open custom schemes like `deck://`). Tap **Pair the Deck app instead**, which hands `deck://pair?key=…&name=…&code=…` to the app via an Android `intent://` link; the app saves the host, opens its tunnel and shows the claim screen. Without the app installed, the browser pairs itself over Tailscale.
3. Approve the device on the host.

A verified Android App Link on a public domain would skip the browser step; not implemented.

## Reference

### Ports

| Port | Bound to | What | Set by |
|---|---|---|---|
| 8787 | `127.0.0.1` | Deck (UI, API, WebSockets) | `DECK_PORT`; address `DECK_HOST` |
| 8788 | `127.0.0.1` | Tunnel port: `deck-iroh` forwards here; always requires pairing | `DECK_TUNNEL_PORT` (default `DECK_PORT` + 1) |
| 20000–39999 | Phone `127.0.0.1` | Phone app's per-host tunnel forwarder | Derived from the host key |
| 5900 | `127.0.0.1` | VNC (Virtual Network Computing) server for the Screen tab (optional) | [screen.md](screen.md) |
| 6080 | `127.0.0.1` | websockify bridge for the Screen tab (optional) | [screen.md](screen.md) |
| 8931 | `127.0.0.1` | Playwright MCP for the agent browser (optional) | `deploy/agents/agent-browser.sh` |

### Environment variables

Set per host in `~/.config/deck/env` (`KEY=VALUE` lines). The `deck` service loads every line; the PC app passes every line to its children; CLI tools (`deck-notes`, `deck-key`) read the `DECK_*` lines.

| Variable | Default | Effect |
|---|---|---|
| `DECK_HOST_NAME` | hostname | This host's name |
| `DECK_ORIGINS` | none | Comma-separated extra origins (URLs) allowed for browser requests and as Host names; first entry is `deck-pair`'s default URL |
| `DECK_AUTH` | `on` | `on`: paired devices only; `auto`: open until the first device is approved; `off`: no pairing. Any other value means `on`. See [security.md](security.md) |
| `DECK_LOCAL_IPS` | none | Comma-separated tailnet IPs of this host; requests through `tailscale serve` from these count as this host's own browser |
| `DECK_PORT` | `8787` | Deck's port |
| `DECK_TUNNEL_PORT` | `DECK_PORT` + 1 | Tunnel port |
| `DECK_HOST` | `127.0.0.1` | Bind address for `DECK_PORT`. The tunnel port is always on `127.0.0.1`. These docs assume the default |
| `DECK_DATA` | `~/.config/deck` | Data folder (devices, host key, settings) |
| `DECK_NOTES` | `~/deck-notes` | Machine notes repo |
| `DECK_INVENTORY` | `$DECK_NOTES/inventory.toml` | Host inventory |
| `DECK_WORK` | `~/work` | Default folder for agent keys |
| `DECK_VNC_FILE` | `$DECK_DATA/vnc.json` | Screen tab config |
| `DECK_IROH` | none | Path to `deck-iroh` for linked-PC forwarders; otherwise `PATH`, `~/.local/bin`, and on Windows `%LOCALAPPDATA%\Deck\deck-iroh.exe` |
| `DECK_DESCRIBE_CODEX_MODEL` | `gpt-5.6-luna` | Codex model for session descriptions |
| `DECK_NTFY` | none | ntfy topic URL for phone alerts |
| `DECK_TRANSCRIBE_URL` | none | Local speech-to-text endpoint for the browser mic |
| `DECK_MSYS` | `server\msys` | Windows: MSYS2 kit root |
| `DECK_SUPERVISED` | unset | Set by the PC app: `/api/update` stops the server instead of restarting it |

Build and installer variables: `DECK_NOTES_REPO`, `DECK_BUILD_TUNNEL` ([Install](#install)); `DECK_APP_VERSION`, `DECK_KEYSTORE`, `DECK_KEYSTORE_PASS`, `DECK_KEY_ALIAS`, `DECK_ABIS`, `DECK_PHONE` ([Phone app](#phone-app)).

### Files

| Path | What |
|---|---|
| `~/.config/deck/env` | Per-host settings |
| `~/.config/deck/host-key` | Shared host key (mode 600); full access to every host that has it |
| `~/.config/deck/devices.json` | Paired devices (secret hashes) and pending codes (mode 600) |
| `~/.config/deck/iroh.key`, `iroh.id` | Tunnel secret and public key |
| `~/.config/deck/vnc.json` | Screen tab config (optional) |
| `~/.config/deck/mirrors/` | Copies of other hosts' open agent chats (kept 30 days) |
| `~/.config/systemd/user/deck.service`, `deck-iroh.service` | Linux services |
| `~/.local/bin/deck-iroh` | Tunnel binary (Linux) |
| `~/.local/bin/deck-pair`, `deck-key`, `deck-notes` | Links into the checkout's `deploy/` |
| `~/work`, `~/work/deck-keys` | Agent workspace and key-builder repo |

## Releases

`.github/workflows/release.yml`. Plain pushes never build.

**Version tag** (`v*`, e.g. `v0.2.0`): builds everything and creates a **draft** release titled `Deck <tag>`. Publish it on GitHub; until then `install.sh` and `releases/latest` don't see it.

| Asset | Contents |
|---|---|
| `deck-<tag>-windows.zip` (e.g. `deck-v0.2.0-windows.zip`) | `deck.exe`, `deck-iroh.exe`, `uv.exe`, `server/msys` (MSYS2 kit), `NOTICE`, `LICENSE`; unzip into `%LOCALAPPDATA%\Deck` |
| `deck-iroh-linux-x86_64`, `deck-iroh-linux-aarch64` | Linux tunnel binaries, built on Ubuntu 22.04 (older glibc hosts run them); `install.sh` downloads these |
| `deck-<version>.apk` (e.g. `deck-0.2.0.apk`) | Phone app; only when `DECK_KEYSTORE_B64` is set |
| `SHA256SUMS` | SHA-256 of every asset above |

**Manual run** (Actions → release → Run workflow): always builds the `deck-windows` artifact; inputs `tunnel` and `apk` add `deck-iroh-windows`, `deck-iroh-linux-x86_64`, `deck-iroh-linux-aarch64` and `deck-apk` (version `0.0.1`). Results are workflow artifacts, not a release. The `version` input (default 0.0.1) names a manual APK; give it a version above the installed app's for Android to install it over.

Every run also runs `go vet` and `go test` on `desktop/` and checks it builds for Linux and macOS (arm64).

### Build

| Output | Command |
|---|---|
| `deck.exe` | `cd desktop && go build` (Go 1.22+, no cgo; one dependency, `github.com/jchv/go-webview2`, Windows only). Cross-compile: `GOOS=windows go build`. |
| `deck-iroh.exe` | On Windows: `cd tunnel && cargo build --release --bin deck-iroh`. From Linux: `cargo xwin build --release --target x86_64-pc-windows-msvc --bin deck-iroh` (clang, lld, `cargo install cargo-xwin`). |
| `deck-iroh` (Linux) | `cd tunnel && cargo build --release --bin deck-iroh` |
| MSYS2 kit | In an MSYS2 shell: `bash desktop/msys-kit.sh [out dir]` (CI builds it on the Windows runner) |
| APK | `app/build.sh`, see [Phone app](#phone-app) |

## Dependencies

**Host (Deck itself)**, installed by `deploy/install.sh` on Ubuntu (incl. WSL):

| What | Version | For |
|---|---|---|
| Python | 3.11+ (tested 3.14) | Server (`tomllib`) |
| aiohttp (`python3-aiohttp`) | tested 3.13 | HTTP and WebSocket server |
| qrcode (`python3-qrcode`) | any | Pairing QR codes (optional; without it, links only) |
| tmux | 3.4+ (tested 3.6) | Every run and terminal session |
| git | any | Updates (`/api/update`), machine notes sync |
| Node.js + npm | 20+ | Installing Codex; building the app |
| Claude Code, Codex, Pi, OpenCode | latest | Agents (each optional; OpenCode is installed by hand) |
| Rust (rustup, stable) + gcc | 1.85+ (2024 edition) | Building `deck-iroh` when no prebuilt binary fits (iroh 1.3, tokio, anyhow) |
| Tailscale | any | Optional second way in (`tailscale serve`) |
| pytest, pytest-aiohttp, pytest-asyncio | distribution packages | Tests |

**Windows (PC app)**: `uv` fetches Python 3.12 with `aiohttp`, `qrcode` and `pywinpty`; Git for Windows; npm (optional, for Codex); the WebView2 runtime only for `deck window`.

**Browser side** (loaded from CDNs, content delivery networks; nothing to install):

| Library | Version | Source | For |
|---|---|---|---|
| xterm.js, addon-fit | 5.5.0, 0.10.0 | jsDelivr | Terminal |
| KaTeX | 0.16.22 | jsDelivr | Math in chats |
| pdf.js (`pdfjs-dist`) | 4.10.38 | jsDelivr | PDFs |
| noVNC | 1.7.0 | jsDelivr | Screen tab |
| mammoth.js | 1.8.0 | cdnjs | Word files |
| TipTap, marked, turndown | 2.11.5, 15.0.7, 7.2.0 | esm.sh | Markdown editor |
| Archivo, IBM Plex Mono | | Google Fonts | Fonts |

Libraries from jsDelivr and cdnjs must match a pinned hash (subresource integrity, SRI) or they don't load. After changing a version, `deploy/cdn-sri` prints the new hashes.

**Screen tab** (optional, per host): a loopback-only VNC server and websockify, see [screen.md](screen.md). **Phone screen** (WSL hosts): Windows PowerShell 5.1 (built in) and the [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver) (MttVDD, 25.7+; admin once to install).

**Agent browser** (optional): Google Chrome and Playwright MCP (Model Context Protocol), `deploy/agents/agent-browser.sh`.

**Building the PC app**: Go 1.22+ (`deck.exe`); Rust for `deck-iroh.exe`; CI fetches `uv` and builds the MSYS2 kit.

**Building the phone app** (only on the machine that builds it): JDK (Java Development Kit) 21; Android SDK (software development kit) platform 36 and build-tools (`ANDROID_HOME`, default `~/Android/sdk`); Android NDK (native development kit), any recent (the newest under `$ANDROID_HOME/ndk` is used); Rust with `rustup target add aarch64-linux-android` and `cargo install cargo-ndk`; Node.js + npm (Capacitor 8.5, `@capacitor/filesystem` 8). `push.sh` also needs adb (platform-tools) and nmap.
