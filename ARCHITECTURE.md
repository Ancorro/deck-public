# Deck architecture

Target: a native phone app that controls agents and terminals on the user's own machines, with no accounts, no virtual private network (VPN) to set up and no open ports. This document mixes what is built with what is planned; every part carries a status.

| Status | Meaning |
|---|---|
| **Built** | In the code today. |
| **Partial** | Some of it is built; the rest is listed. |
| **Planned** | Not built; the intended design. |
| **Not planned** | Worked out, but nobody is building it. |

Today: one Python web server per host (the `deck` systemd user service on Linux, the PC app on Windows), reached by paired devices only, over the iroh tunnel or Tailscale, with an Android app. Install: [docs/install.md](docs/install.md). Repository layout: [docs/development.md](docs/development.md#repository-layout).

## Components

| Component | Runs on | Job | Status |
|---|---|---|---|
| Host server | each PC | Sessions, agents, keys, notes, pairing, updates. `deck/server.py` with the API package `deck/api/` (aiohttp). Sessions run in tmux on every operating system (an MSYS2 tmux on Windows); on Windows the terminal client and Claude's chat bridge run in a Windows pseudo console (ConPTY, via `pywinpty`). | **Built** |
| PC app | Windows (supported target); Linux and macOS | `desktop/`, Go: keeps the server and `deck-iroh serve` running and restarts them; logon autostart (Windows only: the `HKEY_CURRENT_USER` (`HKCU`) Run value `Deck`). No window or tray; the user interface (UI) is the page at `http://127.0.0.1:8787/?pc`. On Linux and macOS the binary builds and runs without autostart (macOS needs Homebrew's `tmux`); on Linux, `deploy/install.sh` sets up systemd services instead. | **Partial**: Windows only gets autostart and the bundled MSYS2 kit; continuous integration (CI) compiles Linux and macOS builds as a check and ships none; no installer, no code signing, no self-update of `deck.exe`/`deck-iroh.exe`. |
| Phone app, Android | Android | `app/` (Capacitor): host list (Settings → More → Hosts), pairing by Quick Response (QR) code, notes carrying, dictation, iroh tunnel. Host pages are each host's own Deck page. | **Built**, without agent sign-ins. |
| Phone app, iOS | iOS | See [iOS](#ios). | **Not planned** |
| Connectivity | host and phone | `tunnel/`: reach a host by its public key, direct when possible, relayed when not. | **Built** (iroh) |
| Relays | public servers | Forward encrypted traffic when no direct path works; see no content. Deck uses n0's public relays (`presets::N0`). | **Built** (public relays); a self-hosted relay setting is **Planned**. |

## Connectivity: keys, not accounts

- **Built.** Every host has an iroh key pair (`~/.config/deck/iroh.key`, made on first run); its public key (`iroh.id`) is its address. No usernames, passwords or cloud accounts.
- **Built.** Phones and home PCs sit behind routers and carrier network address translation (NAT) and accept no incoming connections. iroh finds the host by key through n0's Domain Name System (DNS) records, punches through when it can, and relays through n0's relays when it can't.
- **Built.** Host: `deck-iroh serve` accepts iroh connections (Application-Layer Protocol Negotiation (ALPN) id `deck/tcp/1`, one bidirectional stream per Transmission Control Protocol (TCP) connection) and pipes each to the server's tunnel port, 8788 (`DECK_TUNNEL_PORT`). Every request on that port counts as remote and needs a paired device.
- **Built.** Phone: the app links the same crate as `libdeck_tunnel.so` through the Java Native Interface (JNI), listens on `127.0.0.1` and forwards the WebView's connections to the host.
- **Built.** Tailscale stays an optional transport; it is not a dependency.

## Pairing

**Built** (`deck/auth.py`, [docs/security.md](docs/security.md)):

1. A paired device (Settings → More → Devices), the host's own browser, or `deck-pair` makes a one-time code, shown as a QR code or link. It expires after 5 minutes (`CODE_TTL = 300`) and carries the host's iroh key when `deck-iroh` runs.
2. The new device claims the code with its name.
3. Whoever made the code approves or refuses the device.
4. The device gets a random secret in an `HttpOnly` cookie (`deck_device`, 10 years); the host stores only its hash. The secret travels inside the tunnel's or Tailscale's encryption.
5. Any paired device can remove the others (Settings → More → Devices). Approved devices sync to every host that shares the host key.

Enforcement: over the tunnel, pairing is always required. Elsewhere `DECK_AUTH` sets it: `on` (default), `auto` (open until the first device is approved), `off`. The host's own browser (loopback) always gets in.

**Planned**, replacing the cookie secret:

- Per-device key pairs; the QR code adds a relay hint.
- Mutual key authentication on every connection, using a proven protocol (Noise, as in WireGuard, or libsodium); no home-made cryptography.
- App lock (Face ID, fingerprint or personal identification number (PIN)), a re-check before risky actions (open terminal, skip permissions, edit notes), and auto-lock after inactivity.

Pairing stays mandatory, not a setting: the host gives a full shell, so the device check is the security.

## Host-to-host

**Built.** No host is the main one; each serves everything and shares state with the others.

| Part | Module | What it does |
|---|---|---|
| Host key | `auth.py` | Shared secret (`~/.config/deck/host-key`) that hosts present to each other; full access to every host that holds it. |
| Linked PCs | `links.py` | Join PCs into one group over iroh with no Tailscale or notes inventory: a 5-minute link code, approved by the PC that made it, hands over the host key. Removing a member rotates the key. |
| Sync | `sync.py` | Shelf, scripts, settings, linked PCs and paired devices are the same on every host: pushed on change (`POST /api/sync/<doc>`), compared every minute, merged item by item with tombstones. |
| Chat copies | `mirror.py` | Each host copies its open agent chats to the others, so an offline host's chat can carry on elsewhere. |
| Session move | `session_move.py` | Moves an agent chat to another host (`/api/move-in`). |
| Host list | `settings.hosts()` | The notes inventory's hosts with `deck_url`, plus linked PCs through local `deck-iroh connect` forwarders. |

## Machine notes

Short, durable facts about the user's machines (what runs where, how to fix it, decisions), read by every agent. Rules: only durable facts that can't be derived; each note under 30 words; never secrets (where a credential lives, yes; its value, no).

- **Built.** Every host keeps a full git copy (`deck/notes.py`) and serves it at `/notes.git`. A commit pushes to every reachable peer (post-commit hook); a session start pulls. Peers are the inventory's hosts plus the repo's `origin` remote, if any.
- **Built.** The phone carries the notes between hosts (`notes.carry`, `POST /api/notes/carry`, `carryNotes` in `14-start.js`):
  - The phone stores a git bundle (`notes/bundle.b64`) and a plain-text copy (`notes/copy.json`) for offline reading in the app's Notes view. It needs no git of its own.
  - On each host visit the phone sends its bundle and queued offline edits; the host merges, applies the edits, pushes to reachable peers, and returns its new bundle.
  - An offline edit to a file that changed on the host since is refused and stays queued, marked, for the user to redo. Merge conflicts keep the host's copy and are reported, never silently dropped.
- **Built.** An optional always-on peer: any git remote set as the notes repo's `origin`.
- **Built.** The inventory is optional; linked PCs need none.

## Agent sign-ins

**Planned.**

- The host checks which agents (Claude Code, Codex, OpenCode) are signed in.
- Sign in once (Claude `setup-token`, Codex device login); the phone stores the tokens encrypted.
- Pairing a new host: "Copy sign-ins" sends them over the paired channel. Relays never see them.

## Updates

- **Built.** `POST /api/update` pulls the checkout (`git pull --ff-only`) and restarts the server if the commit changed; tmux sessions survive. `deploy/update-all` calls it on every host in the inventory and every linked PC, with the host key.
- **Planned.** The phone triggering updates on every paired host.

## iOS

**Not planned.** Capacitor builds for iOS from the same web code and config. What would change:

| Part | Android today | iOS plan |
|---|---|---|
| Build and install | Java Development Kit (JDK) 21, Android software development kit (SDK) and native development kit (NDK), Rust with `cargo-ndk`; `app/build.sh`, `app/push.sh` over Android Debug Bridge (adb). | Needs macOS and Xcode, or a cloud macOS build (GitHub Actions macOS runner, Codemagic) installed through TestFlight. TestFlight and the App Store need the Apple Developer Program ($99/year); a free account allows only 7-day installs from a Mac. |
| Plugins on host pages | `MainActivity` injects Capacitor's script (`addDocumentStartJavaScript`, by reflection) into trusted origins only: saved hosts and the tunnel ports the app opened. An untrusted `*.ts.net` page opens the app's host list instead of loading; other loopback ports are refused. | Same idea in Swift: a `WKUserScript` for those origins (`CAPBridgeViewController` subclass). WebKit's App-Bound Domains may need the host domains in `Info.plist`. |
| Dictation | `DictationPlugin.java`: Android's on-device recognizer (Android application programming interface (API) level 33+, when available) with formatting and one segmented session; otherwise the default recognizer. | `DictationPlugin.swift` with the same events (`partial`, `segment`, `end`, `error`): `SFSpeechRecognizer` with `requiresOnDeviceRecognition` and `addsPunctuation` (iOS 16+), restarted at the ~1 minute task limit. Needs microphone and speech recognition usage strings in `Info.plist`. |
| Notes and chat carrying | `@capacitor/filesystem` | Same plugin, unchanged. |
| Connectivity | iroh tunnel (`libdeck_tunnel.so`), or the Tailscale app | Tailscale has an iOS app; the tunnel needs Swift bindings. |
| Background | None; phone alerts go through ntfy. | iOS suspends background apps: live terminals reconnect on return, as now; push notifications would use Apple Push Notification service (APNs). |

Order: the web UI in iOS Safari first (a cheap check of layout and gestures), then the Capacitor iOS shell with plugin injection, then Dictation in Swift.

## Migration from the original design

| Step | Status |
|---|---|
| 1. Pairing on the server (`deck/auth.py`), so tailnet access no longer means full access. | **Built** |
| 2. The iroh tunnel in front of the server; Tailscale stays an option. | **Built** |
| 3. The Android app: a thin native shell around the web UI, with notes and chat carrying and pairing. | **Built** |
| 4. The PC app (`desktop/`): starts Deck at logon on Windows; also builds and runs on Linux and macOS, without autostart. | **Partial**: no installer, tray, code signing or self-update; CI ships Windows builds only. |
| 5. The notes inventory optional (linked PCs need none); the Screen tab shown only when set up. | **Built** |
