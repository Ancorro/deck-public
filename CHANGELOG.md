# Changelog

Notable changes. Deck updates from `main`; releases are tags with prebuilt binaries
([Releases](https://github.com/Ancorro/deck-public/releases)).

## Unreleased

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
