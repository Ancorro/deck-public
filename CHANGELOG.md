# Changelog

Notable changes. Deck updates from `main`; releases are tags with prebuilt binaries
([Releases](https://github.com/Ancorro/deck-public/releases)).

## Unreleased

### Changes
- Code view (PC view tab, between Review and Web): the chat, a code editor (CodeMirror 6) and, in the shelf's place, a file tree of the folder the open session works in, on its host. Ctrl+S saves; a save refuses to overwrite a file an agent changed meanwhile and offers Reload theirs or Overwrite with mine; an open file an agent changes reloads by itself unless it has unsaved edits; Deck's reload after an update waits for unsaved edits. Sessions report the folder their agent moved to (`PUT .../project {folder}`), which the tree opens.
- Deck app window (Windows): `deck-app.exe` opens Deck in its own window (WebView2) with a taskbar button and a Start menu entry (`deck shortcut`); links to other sites open in the default browser, the title bar follows light and dark, and the Web view loads any site with the window's own logins.
- A new Deck mark, Stack: a deck of session cards, the live one in front in Deck's orange with its LED, for the browser tab (now versioned, so a cached old icon is replaced), the home-screen icon (with a full-bleed maskable version) and the app window.
- Settings → General → New sessions → Default model has a Computer row: the model Computer sessions start on (Claude's list; blank uses Claude's).
- Phones: the shut shelf is as narrow as the shut tab strip in every look (Plain and Message made it 28-30 px).
- Phones: the open shelf is as wide as the open tab column (at most 48%, 210 px), so the chat shows beside it.

## 0.5.0 (2026-11-14)

### Changes
- Chat UI refactor: consecutive tool and thinking steps group tightly together, fenced code blocks render as cards with a language bar and Copy button, headings and lists have cleaner spacing and hanging indents, Plain centers chats into an 820 px column on wide screens, minimal single-line tool call rows are kept across looks, and `agy` tool calls render in the same style (`$ cmd` + description, colored diffs, and written file content).
- Dark mode menus and `<select>` option lists use opaque surface and text colours across all looks.
- Closing a session tab lives in the tab menu (right-click on PC, hold on touch) rather than tapping the active tab twice.
- agy chats in Ask before tools (the default for a new chat) ask again before running a tool: agy 1.5.7 made Auto its default mode, so tools ran without asking while the bar showed `ask`. Deck now starts agy in Default mode for that session.
- Claude chats on Windows start in seconds again: the first message waited 90 s, because Claude draws its prompt as `>` there and Deck looked only for `❯` before typing.
- `deck-notes sync` skips a host that is down after one 3-second check of all hosts at once, instead of trying it with git up to three times; a host off for days made every Claude start wait out its 30 s SessionStart hook.
- Layouts menu: UI text (75–150%) sizes the page's own text (tabs, buttons, menus, settings, shelf, board) without zooming the layout; chats and terminals keep Text size.
- The shelf has a Deck files area at its top, styled apart from saved items: Skills lists each skill in the skills folder (`DECK_SKILLS`) with its description and text files; a file opens in Review (the whole text on a phone), and Edit saves it to the skill's own file.
- Claude chats on a host where Claude starts slowly no longer merge the first lines: `/model`, `/effort` and a message sent before Claude's input box showed reached it as one `/model` whose argument was all three, and the request failed. Each line now waits for the input box, and a line after `/model` or `/effort` waits for its reply.
- A Claude chat's `/model` or `/effort` pick no longer changes the host's own Claude defaults with Claude Code 2.1.322, which records slash-command replies differently and saves effort per model.
- Messages sent to a new chat in its first seconds, before its bridge had started (a few seconds on a small host), are no longer dropped.
- A Claude chat's status bar starts on the model it was started with (Settings → General → New sessions → Default model), not the host's Claude default.
- Board cards have an × to remove them and a corner handle to resize them by dragging.
- Settings → General → Logins lists agy: signed in when its model list answers; Log in opens agy to sign in.
- The header shows the word "deck" after the name in every look (Plain, Message and Zine hid it), with a line under the name.
- Settings → General → New sessions → Default model is a menu for every agent, listing the models that agent offers (one list for the whole Deck: the longest any host has).
- Settings looks the same throughout: every control is one kind of row (name, hint under it, control on the right, or under it for a wide one), in every look, on phones and PCs; field names no longer switch to small capitals. Default model and Default effort are headings over their rows. Default folder and Brain data follow the switches in their sections, and Advanced groups its switches. Screen settings uses the same rows and switches (it keeps Save and Cancel). Resizable session pane, PC only, no longer shows on phones. Every place Deck names a setting reads Settings → tab → section → row, and points where the setting is now.
- PC: Settings → General's two columns are balanced: New sessions sits under Deck and Logins, Chat starts the right column.
- The rich document editor's page fills the reader column, with no wide side margins.
- PC: the collapsed session column is wider (52 px, was 40-42).
- PC: the collapsed session column shows the start of each tab's title under its icon.
- Session tabs no longer carry their host's colour dot; the host headers in the column and the session trash keep theirs.
- A session tab's corner shows a small spinner while it works, instead of a ring while it is idle.
- Settings has three tabs: Appearance (Look, Accent colour, Theme, Text size, Quick keys; first), General and Hosts (what only matters with more than one host: names and colours, site order, the phone app's fallback, linking PCs). With one host, Hosts is greyed out and PCs sits in General → More. Done is larger.
- Appearance → Accent colour sets this PC's colour; before, a Deck with one host had no way to change it.
- Logins: Settings → Logins and a new setup step show each host's agents and tools (Claude Code, Codex, OpenCode, GitHub, Hugging Face) as signed in, or **Log in**, which opens a tab running that tool's own sign-in.
- Review: a text document on the shelf (or a machine notes file) redraws within 2 s when an agent changes it, as linked files already did; a ↻ button reloads the open document by hand.
- Hosts in the machine notes inventory can talk over iroh: an entry's `iroh` key routes this host's traffic to it (sessions, sync, moves) through a local `deck-iroh connect` forwarder while that answers, and back to its `deck_url` within about 3 s when it doesn't. Settings → Hosts shows which one each host uses now.
- A `deck-iroh connect` forwarder now ends with its server, even when the server is killed with no cleanup; before, it kept running.
- A script key's tab shows the script's name when the program in it sets no title; before, it showed the folder (often "~").
- Faster tab column: sessions are pushed to open pages and between hosts as they change, instead of each page asking every 2 seconds and each host every 3. A new or closed tab shows in about a quarter of a second, on this host or another. Each host lists its sessions once a second for every page, not once per page. An open page makes about a tenth of the requests it did.
- The Screen tab's desktop viewer (noVNC, about 50 files) loads when Screen first connects, not with every page.
- Removed: reading and continuing an offline host's chat from a copy (Transfer here). Chats are no longer copied to every host. Every line an agent wrote went to every host and counted as a change to the shared state, so each open page re-downloaded settings, scripts, hosts and the shelf (about 1.2 MB a minute) every 2 seconds while any agent worked. Move host still carries a chat between hosts that are up.
- Agent chats no longer run in tmux. Each chat's bridge is a process of its own, detached from the server, so it still survives Deck restarts and updates, with no MSYS2 tmux or bash in between on Windows. Terminals and script keys stay in tmux. Chats already open keep their tmux pane until closed. A chat has no terminal view; a bridge that fails on start shows its output as the tab's preview. After a reboot, chats stay in the tab column as failed, and Restart continues them. Restart on a chat still in tmux moves it out of tmux, on the same conversation.
- Settings: edit the default model for every agent using an alias or model ID. Blank restores the agent default; session model picks remain separate.
- Deck opens on the last view you used (Board, Terminal or Screen), kept per browser. A first visit still opens on the Board.
- Layouts: rename a saved layout in place, or save the current columns over it. Right-click its header button, or pick Edit in the Layouts menu. A named layout's button shows the name you gave it.
- Board: each project has one status card (key `status-<repo>`) that every session in the project updates, instead of one per session. The agent prompt now tells each session to update it after each large step.
- Shelf: PDFs and other documents download on a PC. A document's card has Download beside Review, and the Review reader has a Download button for the open document; both save the file itself (a PDF as the PDF), from whichever host has it.
- Screen: more room for the desktop. The bar floats over the stream and fades while you watch, the shelf folds while Screen is open, the desktop takes 80% of the height by default, and a new Chat button hides the chat history so only its message box stays.

### Fixes
- Trash: closing a chat on a host that isn't the site's writer put nothing in the Trash, since the writer's copy of the shared trash file replaced it, and Restore went to the writer's host. Each host now keeps its own trash again (`session-trash-local.json`), started from its entries in the old shared file.
- Windows: checking whether a computer-use run survived a Deck restart queries its process handle; the Unix signal probe could terminate it on Windows.
- Codex model and effort menus refresh the ChatGPT account's list when opened, so models do not disappear behind a stale Codex cache.
- Slow pages on a node writing for others: while a node was offline or far behind, the writer parsed up to 200 journal entries for it every 2 seconds on the server's main loop before finding it needed a snapshot. Every request waited behind that (up to 12 seconds for the session list). It now checks first, and reads the journal off the main loop.
- Claude chats: on a busy machine the chat could go back to "your turn" before the reply showed, or stay on Working. Claude Code 2.1.318 can write the reply to its transcript seconds after the Stop hook runs. A turn now ends on the transcript's end-of-turn record (turn_duration), with or without the Stop hook, or 10 seconds after Stop if that record never comes.
- Claude chats: Claude Code 2.1.317's "Session paused" dialogs (a refused message: retry on the fallback model or edit the prompt; a model that needs usage credits: switch models or set up credits) showed as an Allow / Deny card whose buttons pressed the wrong keys. The card now shows the dialog's own text and options, read off Claude's screen, and presses the option picked; one it cannot read says to answer in the terminal. The live agent test asks Claude for Haiku, so an account without usage credits no longer stops it on that dialog.
- agy chats: a turn agy cut short (its `--print-timeout`, from agy 1.4.11's `finished_reason`) looked complete. Deck now notes "agy stopped early: print timeout" (or interrupted), keeps the partial reply, and hands back the waiting messages.
- Terminal: on a phone it could send tmux one column too many after the Board became the first view (the terminal opened while its font was still loading, and measured the fallback's cells); it now measures again once the font is in.

### Screen
- The desktop sits above the chat on a phone and a PC, with a divider to share the height, so you can message the agent while watching it work.
- Goal row under the desktop, for a host with a computer-use loop (`computer.json` `goal`): give the loop a goal, Pause it (the desktop is yours meanwhile), Resume or Stop it. View log shows each step and every model call with what it was sent; the row shows the run's cost. Screen settings (⚙) pick the loop's paid model, its effort, whether the fast model decides first, and steps per goal. Each goal starts one paid-model session with Deck's context and the files named in `computer.json` `context_files`.
- Message a session from under the screen: a picker beside the box chooses Desktop (type on it, as before), any running session, or a new Claude session, and the picked session's latest line shows above the box, so you can watch an agent work the desktop and talk to it in one view.

### Computer sessions
- **+** offers **Computer** on a host whose `computer.json` sets it up: a Claude session with that host's desktop as tools (screenshot, click, type, key, scroll, drag, and `run_goal`, which hands a whole task to the host's computer-use loop), through an MCP server (`deck/computer_mcp.py`). Opening its tab shows that host's screen above the chat. Moving the mouse to the top-left corner stops every action.

### Board
- A new first view, Board: one board for every session and host, of square cards that stay where you drag them. Agents post cards to it (`POST /api/board`, types note, link, list, status and alert; a `key` keeps one card per run current). You add notes, links and lists with +, pinch a card to resize it, pinch the board to zoom, tick list items, and drop a card on the trash can to remove it (restorable for 30 days).

### Skills
- One skills folder for every agent: Deck links each skill in `DECK_SKILLS` (default `~/.config/deck/skills`) into the skills folders of Claude Code, Codex, Pi, OpenCode and agy, at server start and before each agent session. `python -m deck.skills` runs it by hand.

### Site
- Settings no longer reset after an update: settings are saved whole (the sync once copied a half-written, empty file to every node), an empty copy never replaces a node's settings, and a node that lost the node order takes it from the serving node instead of serving its own stale copy (which also turned off bypass permissions for new chats there).
- A node whose journal had a line cut short (two writes at once) stopped handing the site back and kept serving its own stale settings: the journal now takes one write at a time and skips a broken line.
- A node that served while the higher one was away now takes the serving node's copy whole when it follows again, instead of keeping its own stale settings.

### Chat
- Edit on a waiting message and ↩ Back to this open the keyboard on a phone (the box had kept it shut). Pi chats get Edit for messages typed while Pi works (its `clear_queue`). Each agent's waiting messages show only the buttons that work for it.
- A new agy chat starts at once: it no longer waits about 30 seconds on the machine-notes sync when a host is offline (the sync now runs in the background).

### Bug button
- A bug icon at the top right of the header: type a report and a new Claude session (Opus, medium effort) fixes Deck in its own checkout, then pushes and deploys to every host (`POST /api/bug`).

### Looks
- UI size: the Layouts menu (top right on a PC) zooms the whole page from 75% to 150%, with Text size beside it. The terminal stays sharp (drawn at a bigger font, not stretched).
- Chat text spreads as wide as the chat in every look: Pad, Rack, Blueprint, Zine and Plain no longer cap a line at about 70 characters.
- Settings → Appearance → Look: five complete looks, each its own layout as well as its own style, each with Light and Dark. **Pad**: the console, with the sessions as a grid of lit keys (where Deck began), recessed displays, keycaps, a dot-matrix readout, a chat that reads as a log. **Rack**: a channel strip, the sessions across the top with LEDs and running meters, the scripts as patch buttons. **Blueprint**: a drafting sheet on grid paper, the tab column a parts list, the status line a title block, messages as revisions. **Zine**: cut and pasted, hard rules and hard shadows, block type, the host's colour as a marker pen. **Plain**: one surface ruled into columns, the system's type, a chat as a transcript. The page loads the saved look before its first paint and swaps looks in place; the browser's theme colour follows the page. Shared by the whole Deck, like the theme (`look`).
- The stylesheet is one structural sheet (`style.css`) with every colour, font, radius and shadow a token, plus one sheet per look (`deck/static/looks/`); the light theme is a token set, not a block of overrides. Fonts load per look.
- The key row and the message box sit under the screen column only (the terminal or chat), not across the tab column and the shelf.
- One more look. **Message**: a text thread (a conversation list with avatars, bubbles with tails, "Delivered" and the time, a pill to type in, sheets with grouped rows).
- Theme, look and every other setting apply the moment they're changed, then save; before, the page waited for the host's answer (a tunnel away, seconds) and re-measured every chat reply for a theme change. A page set to System no longer flashes dark before the script runs on a light OS.
- The agent's thinking is set near the chat's own size in every look (0.95 of it); before, most looks set it at 12px. Tool calls stay small.
- No colour bars on one side of a thing anywhere: your messages and the agent's questions are filled panels, the open tab is filled or outlined, a lit key is outlined in its colour, folders' tabs step in beside a hairline.

### Fixes
- Deck opens faster: the page, its script and style and the API (Application Programming Interface) lists go gzip-compressed (about 900 KB down to under 200 KB per open), and the shelf list cuts every long saved item (not only documents) to a preview, fetching the rest when a card opens.
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
- No light theme paints a surface pure white (Message did).

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
