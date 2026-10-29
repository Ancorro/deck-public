# Security

Deck gives whoever gets in a shell on your computer. It lets in only paired devices, the host's own browser, and your other hosts (with the host key).

To report a vulnerability: [SECURITY.md](../SECURITY.md).

## Ways in

Deck binds `127.0.0.1` only (`DECK_HOST`, default `127.0.0.1`; the tunnel port is always loopback).

| Way in | Who can reach it | Pairing |
|---|---|---|
| Loopback, port 8787 | Every process and user account on the host | Not required: counts as the host's own browser |
| `tailscale serve` → 8787 | Members of your tailnet | Required, except from this host's own tailnet IPs (`DECK_LOCAL_IPS`) |
| `deck-iroh` → tunnel port 8788 | Anyone with the host's iroh key | Always required, even before the first device is approved (`via_tunnel`) |
| Another reverse proxy → 8788 | Whoever the proxy lets in | Always required; see [Reverse proxies](#reverse-proxies) |
| Host key (`Authorization: Bearer <key>`) | Your other hosts | Not required: full access |

## Pairing and access

### Who gets in

`auth_guard` in `deck/api/access.py` lets a request through when any of these holds:

1. The path is open: `/`, `/sw.js`, `/api/me`, `/api/pair/claim`, `/api/link/claim`, `/api/link/wait`, or `/static/*` except `/static/deck.apk` (the page has to show the pairing screen; the APK, Android Package, carries the building host's host list), which also opens with a fresh app link's token (`?t=`, below).
2. It carries the host key.
3. It comes from the host's own browser (`is_local`): from `127.0.0.1` or `::1`, not on the tunnel port, and either with no `X-Forwarded-For` and no `Tailscale-User-Login` header, or with the last `X-Forwarded-For` entry in `DECK_LOCAL_IPS`.
4. Pairing isn't enforced (`DECK_AUTH`, below) and the request isn't on the tunnel port.
5. It carries the cookie of an approved device.

Otherwise: 401 (`unpaired`), or 403 for a device still awaiting approval.

### Pairing

1. A one-time code is created by the host (`deck-pair`, or Settings → More → Devices → Pair a device in the host's own browser) or by a paired device. It expires after 5 minutes and can be claimed once.
2. The new device claims it and receives a random secret (32 bytes, URL-safe) in the cookie `deck_device`: `HttpOnly`, `SameSite=Strict`, `Secure` except on `127.0.0.1` / `localhost`, max age 10 years.
3. Whoever made the code approves or refuses the device (`deck-pair` prompts `Allow "<name>"? [y/N]`).

The host stores only the secret's SHA-256 hash, in `~/.config/deck/devices.json` (mode 600; `deck/auth.py`).

Paired devices are part of the site's state, so they reach every node (`deck/site.py`, host key only): a device paired on one host is paired on all, and removing it anywhere removes it everywhere. Only approved devices travel (id, name, hash, creation time); codes, devices awaiting approval and last-use times stay on their host.

### `DECK_AUTH`

| Value | Effect |
|---|---|
| `on` (default) | Paired devices only (plus the host's own browser and host-key requests) |
| `auto` | Open until the first device is approved, then `on` |
| `off` | No pairing on port 8787 |
| anything else (a typo included) | `on` |

`auto` and `off` let in anyone who reaches port 8787, such as your whole tailnet. Use them only on a trusted network. The tunnel port requires pairing regardless.

### Origin and Host checks

`origin_guard` in `deck/api/access.py`:

- **Host header**: rejected (403) unless it is an IP address, `localhost`, a `DECK_ORIGINS` host name, or a `*.ts.net` name. This blocks DNS (Domain Name System) rebinding: a web page whose name resolves to `127.0.0.1` would otherwise count as the host's own browser.
- **Origin header**: for methods other than GET/HEAD and for every `/ws/` path (WebSockets bypass CORS, cross-origin resource sharing), an `Origin` must be `http://127.0.0.1:<port>`, `http://localhost:<port>`, or listed in `DECK_ORIGINS`. Over the tunnel, the phone's own forwarder origin (`http://127.0.0.1:<port>`, same as the request's Host) is allowed. Requests without an `Origin` (not a browser) pass.

### Host key

Hosts talk to each other with a shared host key, `~/.config/deck/host-key` (mode 600): updates (`/api/update`), notes, the site's journal, other hosts' sessions through `/peer/<host>`. It grants full access to every host that holds it. Copy it only to your own hosts.

## Reverse proxies

nginx, Caddy, Cloudflare Tunnel, ngrok, an SSH (Secure Shell) forward, or any other proxy:

1. Point it at the tunnel port, `127.0.0.1:8788` (`DECK_TUNNEL_PORT`), which always requires a paired device.
2. Add the proxy's public origin to `DECK_ORIGINS` in `~/.config/deck/env`, then restart Deck.

Never point a proxy at 8787: a request from loopback with no `X-Forwarded-For` counts as the host's own browser and gets in without pairing, and nginx sends no `X-Forwarded-For` by default.

## Phone app

Capacitor's bridge answers every `allowNavigation` origin (`*.ts.net`, `127.0.0.1`, `localhost`), and `*.ts.net` includes anyone's Tailscale Funnel pages. So only saved hosts and the tunnel ports the app opened load in the app and get its plugins (files, microphone, tunnel): `app/native/HostsPlugin.java` and `MainActivity.guard`. Only the app's host list can change the trusted hosts. A link to a host that isn't saved (`deck://pair`, a `ts.net` link) opens the host list, which asks before adding it.

The app allows cleartext HTTP to `127.0.0.1` only (the tunnel forwarder; the iroh connection itself is encrypted).

## Good to know

- **Local trust.** Deck trusts every process and every user account on the host: anything that can reach `127.0.0.1:8787` gets in as the host's own browser. Run it on a computer only you use.
- **Framing.** Deck's pages refuse to load inside another site's frame (`X-Frame-Options: DENY`, CSP, Content Security Policy, `frame-ancestors 'none'`), since the host's own browser gets in without pairing.
- **Devices.** Any paired device can remove the others (Settings → More → Devices). Device cookies last 10 years; remove a lost phone in Settings → More → Devices.
- **Linking a PC** (Settings → More → PCs) hands it the host key once the PC that made the link code approves. The code's secret alone isn't enough: whoever claims it first gets a token, and only that token collects the key, once, within a minute of approval.
- **Removing a PC** (Settings → More → PCs → **Remove**): the PC doing it makes a new host key and sends it, with the old one, to every other member it can reach, so the removed PC's copy stops working. Members that were offline or client-only (unreachable) are cut off too: on each, **Leave**, then link it again.
- **CDN libraries.** The page loads libraries from CDNs (content delivery networks). Those from jsDelivr and cdnjs must match pinned hashes (subresource integrity, SRI: `integrity` attributes, an import map for modules, the pdf.js worker fetched with its hash), so a CDN serving changed files can't run code in Deck; the library just doesn't load. Not covered: the Markdown editor (TipTap, marked, turndown from esm.sh, which builds per browser, so a fixed hash would break it) and Google Fonts (CSS and fonts only). Browsers without import-map integrity support (older Safari and Firefox) load the Screen tab's noVNC and pdf.js unchecked.
- **Word files** on the shelf are shown with scripts, styles, frames, objects, forms and event handlers removed, and only `http(s):`, `mailto:`, `#` links and `data:image/` sources kept.
- **APK download.** `/static/deck.apk` requires a paired device, since `app/build.sh` puts your host list in it. The phone app can't save downloads, so Settings → More → App there asks for an app link (`POST /api/app/link`, paired devices only): `/static/deck.apk?t=<24 random bytes>`, which any browser may open for 10 minutes. The host keeps only the token's hash, in memory.
- **Skip permissions (YOLO, "you only live once")** is off by default. Turned on (Settings → New sessions → Skip permissions (YOLO) by default), new agent sessions run tools and commands without asking (`--dangerously-skip-permissions` for Claude Code, `--dangerously-bypass-approvals-and-sandbox` for Codex, `--auto` for OpenCode). Pi never asks, so it has no such flag.
- **Agents can see sessions** and the shelf's **Agents can read** (Settings, both off by default) decide whether Deck answers agents' calls (`/api/agent/sessions`, `/api/shelf/agent`; 403 while off). Every agent's Deck note mentions them either way. Neither is a security boundary: any process on the host reaches Deck on loopback as the host's own browser, so it can already read and type into every session, and other hosts' through `/peer/<host>`. Turned on, they invite agents to read and message every session on every host, including ones running with YOLO.
- **Session descriptions** (Settings, off by default) send the end of each agent turn's output (up to 6000 characters) to the chosen describer, Claude Haiku (Anthropic) or OpenAI's model through Codex, under that command-line tool's login.
- **Phone alerts** (`DECK_NTFY`) go through the ntfy server you choose; on ntfy.sh the topic name is the only protection.
- **The site's state.** Every node holds a copy of the whole site's state, chats included (`~/.config/deck`, fed by the writer's journal), so an offline node's chat can carry on elsewhere. A node that is lost holds every chat.
- **Tunnel key on Windows.** `deck-iroh` creates `iroh.key` with mode 0600 on Linux; on Windows it uses the folder's default permissions.
