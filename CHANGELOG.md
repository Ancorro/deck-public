# Changelog

Notable changes. Deck updates from `main`; releases are tags with prebuilt binaries
([Releases](https://github.com/Ancorro/deck-public/releases)).

## Unreleased

### Looks
- Settings → Appearance → Look: five complete looks, each its own layout as well as its own style, each with Light and Dark. **Pad**: the console, with the sessions as a grid of lit keys (where Deck began), recessed displays, keycaps, a dot-matrix readout, a chat that reads as a log. **Rack**: a channel strip, the sessions across the top with LEDs and running meters, the scripts as patch buttons. **Blueprint**: a drafting sheet on grid paper, the tab column a parts list, the status line a title block, messages as revisions. **Zine**: cut and pasted, hard rules and hard shadows, block type, the host's colour as a marker pen. **Plain**: one surface ruled into columns, the system's type, a chat as a transcript. The page loads the saved look before its first paint and swaps looks in place; the browser's theme colour follows the page. Shared by the whole Deck, like the theme (`look`).
- The stylesheet is one structural sheet (`style.css`) with every colour, font, radius and shadow a token, plus one sheet per look (`deck/static/looks/`); the light theme is a token set, not a block of overrides. Fonts load per look.
- The key row and the message box sit under the screen column only (the terminal or chat), not across the tab column and the shelf.
- One more look. **Message**: a text thread (a conversation list with avatars, bubbles with tails, "Delivered" and the time, a pill to type in, sheets with grouped rows).
- Theme, look and every other setting apply the moment they're changed, then save; before, the page waited for the host's answer (a tunnel away, seconds) and re-measured every chat reply for a theme change. A page set to System no longer flashes dark before the script runs on a light OS.
- No colour bars on one side of a thing anywhere: your messages and the agent's questions are filled panels, the open tab is filled or outlined, a lit key is outlined in its colour, folders' tabs step in beside a hairline.

### Fixes
- A startup error on every page load (`hostList` read before it existed) is gone; the session trash loads once the host list is known.
- A host without `tmux` (or with a PATH entry the service can't read) answers `/api/sessions` with no sessions instead of a 500.
- The Guide opens at its top, not scrolled to its only button.
- Toasts stack instead of covering each other.
- The tab column's and shelf's collapse buttons, the Send button and the message box's collapse toggle are drawn icons, not glyphs that sat off-centre in some fonts.
- Keyboard focus shows as a ring in the accent everywhere.
- The retired Pad view, its toggle and the hidden "Open on" setting are gone from the page.
- `tests/test_server_site.py` no longer races the site loop (the fixture keeps it idle).

### Code (no change in behaviour)
- `deck/core.py` is split by concern: `config.py` (paths, environment, platform), `settings.py`, `agents.py` (agent command lines, the Deck context), `buttons.py`, `sessions.py` (tmux), `status.py` (pane analysis), `projects.py`, `screen.py`.
- `deck/server.py` keeps only the process (bind, restart, `main`); the API is the package `deck/api/`, one module per area, each with a `routes()` list, joined by `make_app()`. The request guards are `deck/api/access.py`.
- `deck/chat_bridge.py` is now the package `deck/bridge/` (the bridge, one adapter per agent, Claude's hooks); the script a pane runs stays where agents and hooks find it.
- The page script is in fourteen parts by concern (`deck/static/app/`, `01-core` to `14-start`), with everything that runs at load in `14-start.js`; the order is `api/pages.py APP_ORDER`. Dead code is gone (the ask sheet's unused "new session" mode, an unused `.splitline` rule, unread state).
- `ruff.toml` holds the Python lint settings; `bash tests/run.sh lint` runs ruff and a syntax check of the page script. `tests/test_server_api.py` checks that every route module registers and the parts are served in order.

### Shelf
- Download on a document gives the file itself (the PDF), from whichever host has it. Before, a linked document downloaded as its extracted text, and an uploaded one on another host failed.
- An agent on a follower can link a file again (`POST /api/shelf/link`). The follower forwarded the request to the writer, which looked for the path on its own disk and answered "path must be an existing file". The follower now reads the file and sends it along, and the item records the follower as the file's host.

### Screen
- `vnc.json` can name `vnc_on` and `vnc_off` commands, so any host (Linux too, not only Windows' scheduled tasks) starts its VNC server when a stream opens and stops it a minute after the last one closes.
- Stop shows OFF again; it stayed on LIVE after the stream closed.
- A host picker in the Screen bar: show any host's desktop, not just this one's (before, another host's showed only when this one had none). Remembered per browser. The bar fits a phone's width with the picker in it.

### Fixes (look and theme)
- A look or theme picked in Settings stays. The sync poll reloaded the host's settings before a follower had the save, putting the old look back; the page now keeps what it is saving until the host has it. On Windows a follower also dropped the settings write when the file was briefly open elsewhere; the file swap is now retried.
- Your own messages sit on the right and are set apart from the agent's in every look (Pad, Rack, Blueprint, Zine and Plain stretched them across like the agent's text).
- Plain's light theme is cream, not white.

## 0.4.0 (2026-10-26)

### Multi-host
- One site on every node: every chat is in every node's copy. A follower sends its own chats to the writer (`POST /api/site/offer`); before, only the serving node's files were journaled, so a sleeping PC's chats were missing from the copies ("no copy of this here"). Linked PCs count as nodes while away, and a write made in the second before a hand-back is kept. None of it needs Tailscale.
- A node restarted after another went down still lists that node's chats, from the copy.
- The "X serves the site now; this is Y's copy" banner on a follower's page is gone; Settings → Deck → Site order says which node serves.
- `+` on another host with Choose agent each time off starts the default agent there without flashing the agent picker; the picker shows only when that agent isn't set up there.

### Phone app
- Settings → More → App in the app (which can't save downloads) shows a 10-minute link to the APK for the phone's browser (`POST /api/app/link`); the next app build hands such downloads to the browser itself.

### Phone app (needs a new app build)
- The host list moved into Settings → More → Hosts; the app opens the Deck straight away and Back leaves the app. The Deck's nodes are listed under its entry in the site order and can be opened one by one. The old Chats copies view is gone (the one-site copies replace it).

### Settings
- Done is a full-size button.

### Chat
- A message that is only `/` is dropped with a note instead of reaching the agent: typed into Claude Code's terminal it opened the command picker, and the Enter after it ran the first command listed.
- Phone chat: Working… and Stop in the top bar are easier to see: a small pulsing accent dot for Working…, Stop outlined in red; both fit beside two view tabs.
- Phone chat: `+` (attach files) moved from the key row's right end to the left of the message box.

### Screen
- The phone screen (virtual monitor) is attached only while streaming; **Phone** is now a per-device preference, and the host detaches an unwatched one after 3 minutes.
- With `deck/win/phonescreen-setup.ps1` (admin once), the Virtual Display Driver itself is enabled only while streaming, so Windows doesn't list a phantom monitor.

## 0.3.0 (2026-10-23)

### Docs
- README rewritten for app users; new [user guide](docs/user-guide.md), [optional features](docs/optional-features.md) page and [agent guide](AGENTS.md). Settings paths corrected across the docs and the in-app Guide.

### Multi-host
- One page for every host: tabs, Settings → Deck → Hosts and Build on another host all go through the `/peer` proxy (no hand-off to a host's own page).
- Paired devices sync between hosts: pair once, every host admits the device; removal on any host removes it everywhere.
- The Deck's name (Settings → Deck → Name, was Alias) syncs to every host. Each host has a name and LED colour (Settings → Deck → Hosts), set from any host.
- One session trash for the whole Deck: closed chats from every host, each marked with its host; Restore and Delete act on that host.
- Keys carry a "Run on" host, so a key synced from another host runs where its script lives.
- New session checks the selected host for installed agents.

### Agents and chats
- New session lists only the agents set up on the host. Settings → New sessions → Choose agent each time, off, makes `+` start the Default agent straight away; holding `+` (right-click on a PC) still opens the picker.
- OpenClaw agent type: a chat on its Gateway's shared `main` conversation (`deploy/agents/openclaw-bridge`), with question cards and inline images and files.
- agy (Google's Antigravity CLI): mode selection, native Effort levels, default effort, question cards, visible native commands, actionable headless permission denials, compaction into a fresh conversation with a handoff; the model variant survives restarts; Google's standard install directory is detected before PATH updates.
- `/goal` for Claude and Codex chats (Codex: set, pause, resume, clear via its app server); a Goal chip shows the goal in force. `/goal` no longer sticks on "Sending…".
- Messages sent while an agent is starting are kept; status saves are serialized (fixes a false send error during agy startup).
- Harness → Sampling (OpenCode): `temperature`, `top_p`, `top_k` for the plan and build agents through an OpenCode plugin; models that reject them are retried without them.
- `openrouter` setting: OpenRouter provider preferences, synced across hosts, applied to every OpenRouter request from OpenCode sessions.
- Chat Markdown: `[label](url)` links (http, https, mailto), `>` quotes, `~~strikethrough~~`, `<details><summary>` folds; other HTML stays text.
- Settings → Chat: Thinking expanded shows the agent's thinking unfolded.

### Projects, shelf and documents
- Project main pages: a shelf link made main (by an agent's pin or Main for … on its card) shows in a bar atop every session in that project (a session's git repo), on every host. Settings → Projects lists them.
- Machine notes (infra) open in the Review reader; Save or Ctrl+S commits and pushes to the other hosts.
- Web view (PC): a web page beside the chat; links clicked in Deck open there. Sites that refuse framing open in a browser window laid over the pane.
- Shelf: Open and Download on items; Copy on plain-http and iroh pages; folders as headings; a mark per item kind; a fixed slot for a pinned page's ↗; the Copy row at the top of an opened item.
- Brain: Select messages → Send to Brain, Lint wiki and Ingest newest, beside Resume (Settings → Brain data names the repo).

### Interface
- Phones: Working… and Stop in the top bar, a full-width status line, a slimmer key row, a growing one-line message box, Settings cards that fit a 360 px screen.
- Settings reordered into Appearance, Devices / PCs / Notes / Guide / App, Chat, New sessions, Deck and Projects; rare switches under Advanced. The default Agent picker is gone (+ asks each time).
- PC: saved layouts (Layouts button; numbered header buttons restore them); one 20 px chat gutter; a click on the tab column's empty space no longer shuts it.
- Tab column: a Scripts bar above the hosts, shown when enabled or when a script exists; tab folders rename again (right-click: Rename, Ungroup).
- Text on the host colour turns dark when the colour is light.

### Phone app (needs a new app build)
- The Deck is one entry; its hosts are tried in turn (most recently used first) when the chosen one is down. A switch turns this off.
- A 2 s host wait (was 4); CDN scripts and fonts load from cache on restart; "open another host" moved to Settings → Advanced.

### Internals and docs
- The page script is split by area (`deck/static/app/`), joined by the server into `/static/app.js`.
- Agent add-ons moved from `deploy/` to `deploy/agents/`.
- Releases are titled "Deck vX.Y.Z".
- Documentation rewritten as a technical reference (README, guide, install, security, development, architecture).

## 0.2.0 (2026-10-15)
- Each session keeps its own unsent message: switching tabs swaps the message box's draft (kept on the device).
- Releases: Linux `deck-iroh` (x86_64, aarch64), a signed Android app and `SHA256SUMS` on each version tag;
  `install.sh` downloads the tunnel instead of building it with Rust.
- Security: `SECURITY.md`; CDN libraries pinned to hashes (SRI); pages can't be framed; installer checks Python 3.11+;
  the app download needs a paired device; CI actions pinned to commits.
- Another host shows offline only after 3 failed checks in a row, so slow links (train Wi-Fi) stop flickering.
- Agents can list, read and message other sessions, and pin web pages to the shelf (Settings opt-ins).
- Session descriptions by Claude Haiku or OpenAI (Codex), a Settings toggle.
- Reader: tabs, PDFs with selectable text, Word files, a Markdown editor; Review view with a draggable divider.
- Tab column: per-host headers and +, tab folders, foldable scripts, idle rings; one Light theme.
- Chats kept on the device, so reloads and switching are quick; slash commands show as labels.
- Windows: the Terminal tab and shell keys take typed input (panes run the kit's own bash, not Git for Windows').
- Agents always start as a chat; their terminal mode is deprecated (the Chat view setting is hidden and ignored).
- agy (Google's Antigravity CLI) as an agent type: chats through its stream-json print mode (always a chat on Windows).
- Removed: computer use (work in progress).

## 0.1.0 (2026-10-09)
First tagged version.
