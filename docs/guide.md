# Deck reference

Deck is a web app that runs coding agents and shells in tmux sessions on one or more hosts and lets you control them from a phone or a PC (personal computer) browser. This document describes every screen, setting, file and HTTP (Hypertext Transfer Protocol) route. Installation, ports and server environment variables are in [install.md](install.md); access control is in [security.md](security.md).

Conventions: "PC" means a screen at least 1100 pixels wide driven by a mouse; "phone" means anything narrower. `DATA` is the data folder, `~/.config/deck` unless `DECK_DATA` is set.

## Contents

- [Overview](#overview)
- [Interface](#interface)
- [Sessions](#sessions)
- [Agents](#agents)
- [Button types](#button-types)
- [Key builder](#key-builder)
- [Agent context](#agent-context)
- [Board](#board)
- [Shelf](#shelf)
- [Documents and Review](#documents-and-review)
- [Settings](#settings)
- [Hosts](#hosts)
- [Linked PCs](#linked-pcs)
- [Machine notes](#machine-notes)
- [Phone app](#phone-app)
- [Phone alerts](#phone-alerts)
- [Screen tab](#screen-tab)
- [Computer sessions](#computer-sessions)
- [Agent browser](#agent-browser)
- [Reference](#reference)

## Overview

| Item | Value |
|---|---|
| Server | `python3 -m deck.server`, bound to `127.0.0.1:8787` (`DECK_HOST`, `DECK_PORT`) |
| Tunnel port | `127.0.0.1:8788` (`DECK_TUNNEL_PORT`); `deck-iroh` forwards here |
| Linux, including WSL (Windows Subsystem for Linux) | `deck` and `deck-iroh` systemd user services |
| Windows | Run by the PC app; sessions use the bundled MSYS2 tmux |
| Sessions | Agent chats: detached bridge processes; terminals and script keys: tmux sessions. Both survive Deck restarts and updates |
| Clients | Deck Android app, or any browser (installable with "Add to Home screen") |

Ways in:

| Path | Needs | Reaches |
|---|---|---|
| iroh | The Deck phone app; no account, any network. See [iroh tunnel](install.md#iroh-tunnel) | The tunnel port; pairing is always required |
| Tailscale | `tailscale serve` in front of port 8787, giving `https://<host>.<tailnet>.ts.net` | Phone app or any browser |
| SSH (Secure Shell) | Any SSH client | `tmux attach -t <name>` |

The Android app (`app/`, see [Phone app](install.md#phone-app)) is downloadable from the host after `app/build.sh` has built it (or a release's APK was placed at `deck/static/deck.apk`): Settings → General → More → App. In an Android browser it downloads; in the app itself, which can't save downloads, it shows a link for the phone's browser, good for 10 minutes (`POST /api/app/link`). Dependencies and versions: [Dependencies](install.md#dependencies).

## Interface

### Header

| Element | Behaviour |
|---|---|
| Name | The Deck's name (Settings → General → Deck → Name), else the host name. Tap to open Settings |
| Power LED (light-emitting diode) | This host's colour (see [Hosts](#hosts)) |
| View tabs | Terminal; Review, Code and Web (PC only); Screen (only when set up) |
| Readout | `NN REQ` when NN sessions need you; `NO LINK` when the host is unreachable. Hidden otherwise |
| PC buttons | Layouts (UI size 75–150%, UI text 75–150% and Text size, then save the current column layout as a button; each saved one has Edit: Rename, Save current columns here, Delete. Right-clicking a layout's button opens the same menu), Fullscreen (drops the margins; Minimize restores them), Keys and message box (hides both; not shown in chats) |
| Bug (top right) | Type what went wrong; a new Claude session (Opus, medium effort, YOLO) starts in Deck's checkout, fixes it, pushes, and runs `deploy/update-all` |

Opening Deck always lands on Terminal. It restores the session last open on any device of the same class (phones share one, PCs another; phone app or browser), else the newest running session, else a new shell.

A browser on a mouse-driven screen gets the PC layout at the plain URL (Uniform Resource Locator). `/?pc` forces the PC layout and `/?pc=0` forces the phone layout; the choice is remembered per browser.

### Tab column

The column left of the terminal. Shut, it is a strip of tab icons (on a PC, each with the start of its title under it); tap it or `›` to open it (over the screen's edge on a phone, beside it on a PC; remembered per browser).

Contents, top to bottom:

1. **Scripts bar**: the Scripts fold toggle (`scriptshut`) and `+` (new script). Shown when Settings → General → Advanced → Scripts is on or any script exists.
2. **Script keys**: one row each (icon, name, run clock while running). See [Scripts](#scripts).
3. **`+`**: opens New session: Terminal and each agent set up on that host (Claude, Codex, OpenCode, Pi, agy, OpenClaw; `agents` in `GET /api/settings`). With Settings → General → New sessions → Choose agent each time off (`agentpick`), `+` skips the picker and starts Default agent (`agent`), falling back to the picker on a host where that agent is not set up. Holding `+` (or right-clicking it on a PC) always opens the picker.
4. **Session tabs**, oldest first. With more than one host, tabs are grouped under each host's name (this host first, "offline" when unreachable), each group with its own `+`.
5. **Trash**: closed agent chats (see [Closing a session](#closing-a-session)).

Tab appearance:

- The agent logo takes a colour derived from the session name (the same on every screen); the open tab is washed in that colour.
- Text is the title the program set (for example the Claude, Codex, OpenCode or Pi task name), else the script's name for a script key, else the folder. Long titles scroll like a ticker on touch screens and end in "…" on a PC (full title in the tooltip).
- Orange: the session needs you. Small dot: it is waiting for you.

Tab actions:

| Action | Effect |
|---|---|
| Drag a tab | Reorders (mouse: immediately; touch: after a hold). Order is shared (`tabs`) |
| Right-click (PC) or hold without moving (touch) | Opens the tab menu: Rename, Description, Move host (agent chats), Arrange (touch), Close |
| Click a host name | Folds that host's tabs away, showing a count (`hostshut`) |

### Tab folders

| Action | Effect |
|---|---|
| Drop a tab on the middle of another | Makes a folder of the two |
| Drop a tab on a folder header, or just under an open folder's header or tabs | Adds it |
| Drag a tab out (above the folder, or between other tabs) | Removes it |
| Click a header | Opens or shuts the folder |
| Right-click (PC) or long-press a header | Rename |

A shut folder lights up when a tab inside is open or needs you. Folders are shared (`tabfolders`).

### Scripts

| Action | Effect |
|---|---|
| Tap a script key | Runs it; output shows live in a window (Stop, Open in terminal, Close) |
| Tap an agent key | Opens its session, or starts one; a key with no saved prompt asks first (see [Button types](#button-types)) |
| Hold, release | Edit (Delete is in the editor) |
| Hold, drag | Move. Order is saved as `pad`; new keys land after their default neighbour |
| Right-click (PC) | Run / Edit / Delete (Delete asks for a second click) |

A shell key whose command uses `$1`, `${1}` or `$@` asks for an argument on tap. The editor's **Run on** picks the host a key runs on. Keys are stored in `DATA/buttons.json` and sync to every host.

### Terminal input

**Key row** (phone, or PC with the message box off): Harness, Esc, Tab, ⇧Tab, ^C, ↑ ↓ ← →, ⏎. Settings → Appearance → Quick keys picks which show (`hidekeys`).

**Message box**: type and Send (adds Enter). One tap on Send submits on finger lift and closes the keyboard. Text is kept if the terminal is disconnected. With a keyboard, Enter sends and Shift+Enter inserts a newline. On a PC the box has a tool row: Harness, Resume and Brain on the left; Mode (agy chats), Model and Effort (chats), `+` (attach files, chats), mic and Send on the right. On a phone `+` sits left of the box. On a PC the box can be collapsed with its chevron (`layout.msgshut`). Settings → General → Advanced → Message box off hides it; phones then type straight into the terminal.

**Harness** pop-up:

| Item | Where | Effect |
|---|---|---|
| Compact, Resume | All agents | Types `/compact` or `/resume` + Enter (chats: the bridge's own call) |
| New, Model, Effort | Chats | `/new`, `/model`, `/effort` |
| Sampling | OpenCode | See [OpenCode](#opencode) |
| Project | All | Sets the session's project (see [Projects](#projects)) |
| Restart | Agents | Relaunches the agent in its folder with the same YOLO (skip permissions) setting, continuing its conversation (see [Restart](#restart)) |
| Copy text | All | Last 2000 lines as plain text in a dialog: long-press to select; Copy all / Copy selection |
| Save, Saves | Agents | See [Saves](#saves) |
| Move host | Agent chats | See [Move host](#move-host) |

**Scrolling**: swipe the terminal to browse history; it follows the finger and keeps going after a flick. Shells scroll tmux scrollback; full-screen apps such as Claude Code receive mouse-wheel events. Tapping the terminal does not open the keyboard (tap the message box). On touch screens taps and swipes never reach the program as mouse clicks. Scrolling to the bottom or typing resumes live output.

**Mic** (PC only; phones use the keyboard's dictation): dictates into the message box with the browser's speech recognition (Chrome sends audio to Google; needs mic permission). With `DECK_TRANSCRIBE_URL` set in `DATA/env`, the browser records until tapped again and the host transcribes instead; Google is not used. The endpoint takes a POSTed audio file and returns `{"text"}` (for example WhisperWriter's `/transcribe`). In the phone app, where the mic button shows, the app's Dictation plugin is used (see [Phone app](#phone-app)).

### PC layout

The tab column, the terminal and, in chats, the shelf sit side by side. Drag the terminal's left or right edge to leave an empty margin; double-click an edge to remove it. Settings → General → Advanced → Resizable session pane turns this off (`resize`). Column widths and flags are shared (`layout`); saved layouts are `layouts`.

### Code view (PC)

Puts the chat on the left, a code editor in the middle and, in the shelf's place, a file tree of the folder the open session works in: the folder its agent last reported (`PUT /api/sessions/<name>/project {folder}`, which Deck's agent note asks agents to call when they move to a repo), else the one it started in. The tree is on that session's host, through `/peer/<host>` for another host's. The path box at its top shows the folder; type another folder and press Enter to show it instead (it stays until ⌂ goes back to the session's folder), ↑ goes up one folder, ↻ reads it again. Build and cache folders (`.git`, `node_modules`, `.venv`, `__pycache__` and the like) are listed dimmed and start shut.

A file opens as a tab in the editor (CodeMirror 6, loaded from jsDelivr the first time, every module pinned by hash): syntax colours for about 40 languages, line numbers, folding, search (Ctrl+F), multiple cursors and bracket matching. **Ctrl+S** or **Save** writes the file on its host, keeping its line ends (CRLF stays CRLF) and byte-order mark; a tab with unsaved edits shows ● and asks for a second × before it closes, and Deck's reload after an update waits for it. Each save names the version it started from: when an agent changed the file meanwhile, the host refuses the save and a bar offers **Reload theirs** or **Overwrite with mine**. The open file is checked every 2 s: an agent's edit shows by itself when the tab has no unsaved edits, and the bar appears when it has. Files over 2 MB and binary files open as a note, not in the editor. Selecting code puts it with your next message as a quote naming the file and lines; **Tell agent** (`readernote`, shared with Review) names the open files, by full path, in your next message. **Wrap** wraps long lines (per browser).

### Web view (PC)

Shows a web page beside the chat. While it is open, links clicked anywhere in Deck open there (Ctrl, Cmd or Shift-click still opens a browser tab). The host checks whether the site allows framing (`GET /api/web/check`); a site that refuses opens in one reused browser pop-up window laid over the pane instead. In the Deck app window (`deck-app.exe`) the page opens in a browser laid over the pane, so every site loads, with the window's own logins.

### Text terminal (experimental)

Settings → General → Advanced → Text terminal (`textterm`); on by default on Windows hosts, where a live tmux client runs in a ConPTY (Windows console pseudo-terminal) that redraws and garbles the screen.

Instead of attaching a tmux client, the host polls the pane with `capture-pane` (every 0.1 s, 0.4 s when idle) and redraws only changed rows in the same xterm.js. Keys go in with `send-keys`; the page size with `resize-window` (released on close). Nothing re-attaches on resize. Lost: the program's own terminal modes (mouse clicks, cursor shape). The wheel scrolls on the host instead; pastes go through tmux (`paste-buffer -p`, bracketed when the program asks).

### Agent status bar (deprecated)

Applies only to Claude and Codex tabs still running in a terminal; new agent sessions are chats. Settings → General → Advanced → Agent status bar (`crop`).

- The agent's input box and status lines (everything below the spinner/token line, or below the conversation when idle) are cropped out and shown as one fixed-height row of chips above the terminal, for example `Opus 5.5 med · ctx 18% · $38 · 5h 17% · wk 2% · bypass`. Codex "left" limits are shown as % used. Chips too wide shrink with an ellipsis, so the bar never resizes the agent.
- Claude and Codex tabs get 14 extra rows hidden below the frame, so that space holds conversation. Claude's footer is 6 rows idle and up to 13 while working; a taller footer leaves a blank band at the top. The extra rows are fixed per tab and set only when the terminal is sized (attach, window resize), since resizing an agent mid-session makes it redraw and garble its footer.
- Claude's values come from Deck's status line script `deploy/agents/agent-statusline.py` (installed by `install.sh` unless you have your own), which writes `~/.cache/deck-status/<pane>.json`; Deck trusts the file only when its writer runs in that pane. Codex's values come from its footer text.
- Menus and permission questions are never covered. Spinner tips are turned off (`spinnerTipsEnabled`, set by `deploy/agents/agent_context.py`) so the footer keeps its height.
- Off: the full agent footer, no chips.

## Sessions

### Names

| Kind | Session name |
|---|---|
| Key run or agent chat | `deck-<label>-<HHMMSS>` (suffix `-2`, `-3`… on collision) |
| Terminals | `deck-shell`, `deck-shell-2`, … |

Finished runs and chats stay until killed (tmux's `remain-on-exit`; a chat's registry entry). `POST /api/clear` kills finished Deck sessions. Script output is also logged to `DATA/runs/<session>.log`.

### Closing a session

Right-click the tab (on a PC (personal computer)) or hold it without moving (on a touch screen) → **Close**; when closing the open tab, the tab below opens.

A closed agent chat goes to the column's **Trash** for 30 days (`DATA/session-trash-local.json` on the host it ran on, `deck/session_trash.py`). Restore starts it again on its conversation as a new tab, with its own model and effort choices. Terminals, and agents outside chat view, are not kept.

### Chat view

Every new agent session (Claude, Codex, OpenCode, Pi, agy, OpenClaw) starts as a chat. Agents in a terminal are deprecated: the `chat` setting is hidden and ignored (on Windows a tmux pane gives native programs pipes, not a terminal, so agents' own screens cannot take keys). An agent tab started in a terminal earlier keeps it on Restart until closed.

A chat shows:

- Message bubbles and streamed replies (Claude's replies arrive a block at a time).
- Tool calls that wrap; tap one for arguments and output. Settings → General → Chat → Tool calls in chats off shows only messages (`chattools`).
- Thinking, folded unless Settings → General → Chat → Thinking expanded (`thinkopen`). Edits to the machine notes repository are shown opened when Settings → General → Advanced → Infra changes expanded is on (`notesopen`).
- A status bar with model, effort and mode.
- A Stop button while the agent works; Esc and ^C also stop it.
- Permission prompts and agent questions as cards, one at a time: options as buttons (multi-select: toggles, then Submit), a Type something box (the message box answers too) and Skip (declines). Question sources: Claude's AskUserQuestion, Codex's request_user_input in Plan mode, OpenCode's question tool, Pi extensions' select, confirm and input, OpenClaw's `ask_user`.
- Error cards for failed agent requests, with details and **Try again** (asks the agent to continue; you can also change the model). Claude's automatic retries show here too; Stop cancels a retry and clears the working indicator even when Claude sends no completion event.
- Messages typed while the agent works, marked as waiting until it takes them in. **Edit** takes them back into the message box (Claude, and Pi through its `clear_queue`); **Send now** makes Claude take them in at once. Codex, OpenCode, agy and OpenClaw take them in on their own, so these show *Sending…*.
- Tap one of your messages for **↩ Back to this**: "Let's go back to this" and the message go into the message box to edit and send. Edit and Back to this open the phone's keyboard.
- Files attached with `+`: uploaded to `DATA/chat/<session>.uploads/` on the session's host (`POST /api/sessions/{name}/upload`); the message names the path.

Settings that affect chats: Jump to newest replies (`chatjump`: returning to a chat you left at its bottom jumps to the newest reply), Debug view (`debug`: also shows what Deck adds and hides: its instructions to the agent, stamps, reminders, hook output). Text size follows Settings → Appearance → Text size.

Naming: a chat is named by the agent (Claude, OpenCode, Pi) or, until then, by its first message (Codex never names one). Renaming the tab renames the conversation in the agent's own lists.

Slash commands: the bridge turns `/compact`, `/resume`, `/new`, `/model` and `/effort` into each agent's own call and passes other slash commands to the agent. Resume lists this folder's past conversations; the picked one continues in the same chat. A session's Model, Effort and mode (Claude's `/mode`; agy's Mode button) choices persist across Restart, Resume and New.

Claude chats: the bridge types into Claude's terminal only once its input box has shown (up to 90 s at start), and after a `/model` or `/effort` waits for its reply (up to 20 s) before typing the next line. Lines sent while Claude is still starting wait their turn; Stop ends the wait. Typed earlier, Claude took them all as one input.

For Codex signed in with ChatGPT, Model and Effort refresh the account's model list when opened. If the refresh is unavailable, Deck uses Codex's own list.

Not available in chats yet: Durable Pi, Save (Saves can load into a chat).

#### How chats run

A chat is not a tmux session. Its bridge, `deck/chat_bridge.py` (the package `deck/bridge/`), runs as a process of its own, detached from the server (`deck/bridges.py`), so it survives Deck restarts and updates. It starts the agent in its machine-readable mode:

| Agent | Mode |
|---|---|
| Pi | `pi --mode rpc` |
| Codex | `codex app-server` |
| OpenCode | `opencode serve` |
| agy | `agy -p= --input-format stream-json --output-format stream-json` |
| OpenClaw | Gateway connection through `deploy/agents/openclaw-bridge/bridge.mjs` |
| Claude | Interactive, in the bridge's own pseudo-terminal (ConPTY via pywinpty on Windows) |

Claude runs as a person runs it, with every tool (`-p` lacks some, for example Artifact). The bridge follows its transcript (`~/.claude/projects/<folder>/<session id>.jsonl`), learns turns and questions from hooks passed with `--settings` (`<session>.hooks.jsonl`), and types the page's messages in.

The bridge writes the chat as events to `DATA/chat/<session>.jsonl` (the page's chat socket replays and follows it) and reads the page's messages from `DATA/chat/<session>.in.jsonl`. Each agent has an adapter in the bridge.

Each chat has a registry entry in `DATA/bridges/` (not in `chat/`, which other nodes copy): `<session>.json` (agent, folder, label, YOLO, the tab's title and project, the bridge's pid), `<session>.lock` (held by the bridge while it runs; the tab shows it running while the lock is held), `<session>.exit` (its exit code) and `<session>.log` (its own output, where a crash on start shows). Kill and Restart queue `{"t": "quit"}` for the bridge, which closes its agent and exits; after 5 s Deck ends its process tree. `deck stop` ends every bridge too. Chats started before this change stay in their tmux panes until closed or restarted; Restart moves one out of tmux on the same conversation. After a reboot a chat's tab stays, as failed, and Restart continues its conversation.

### Restart

Harness → Restart relaunches the session's agent in its folder with the same YOLO setting. A chat resumes its own conversation. A legacy terminal tab continues the folder's latest conversation, or starts fresh if there is none:

| Agent | Terminal restart command |
|---|---|
| Claude | `claude --continue` |
| Codex | `codex resume --last` |
| OpenCode | `opencode --continue` |
| Pi | `pi --continue` (Durable Pi: `pi-durable --continue \|\| pi-durable`) |
| agy | `agy --continue` |
| OpenClaw | `openclaw tui --session main` |

### Default model and effort

Settings → General → New sessions. Applied to new sessions and restarts, synced to every host. A chat's own Model or Effort pick (or `/model`, `/effort`) overrides it for that session only. "Agent default" leaves the agent's own.

| Agent | Model (`model`) | Effort (`effort`) levels | Effort flag |
|---|---|---|---|
| Claude | `fable`, `opus`, `sonnet`, `haiku` (`--model`) | `low` `medium` `high` `xhigh` `max` | `--effort` |
| Codex | Model ID (`--model`) | `minimal` `low` `medium` `high` `xhigh` | `-c model_reasoning_effort=` |
| Pi | Model ID (`--model`) | `off` `minimal` `low` `medium` `high` `xhigh` `max` | `--thinking` |
| agy | Model ID (`--model`) | `low` `medium` `high` | Folded into agy's model ID (identifier) |
| OpenCode | `provider/model` | None (levels differ per model; `/effort` in a chat picks one) | — |
| Computer | Claude's models (`--model`); blank: Claude's default model | Claude's | `--effort` |

Pick a model in Settings → General → New sessions → Default model for each agent: each menu lists what that agent offers, the same list on every host: the longest any host has (OpenClaw's lists only a choice saved earlier); Agent default uses its own default. **Computer** is the model a [Computer session](#computer-sessions) starts on (Claude's list; blank, it uses Claude's row); its effort is Claude's. OpenClaw applies its default to the Gateway's shared conversation when a chat starts. A chat's Model pick overrides its default and survives Restart. Model IDs must be available to that agent on the host running it.

Claude Code saves a `/model` or `/effort` pick as its own default in `~/.claude/settings.json` (`model`, and `effortLevel` or per model under `modelSettings`); the bridge restores the host's previous values once Claude confirms the pick, and the running chat keeps it.

### Skip permissions (YOLO, "you only live once")

Off by default. Settings → General → New sessions → Skip permissions (YOLO) by default turns it on for new agent sessions and keys (`yolo`); the ask sheet and each agent key have their own toggle (not Pi). The agent then runs commands and edits files without asking.

| Agent | Flag or equivalent |
|---|---|
| Claude | `--dangerously-skip-permissions` (asks once to accept bypass mode on first use) |
| Codex | `--dangerously-bypass-approvals-and-sandbox`; in a chat, never-ask plus full access |
| OpenCode | `--auto`; in a chat, auto-allow |
| agy | `--dangerously-skip-permissions` |
| Pi | None: Pi never asks |

### Move host

Harness or a tab's menu (right-click on a PC, hold on touch) → Move host, in an agent chat (`deck/session_move.py`, `POST /api/sessions/{name}/move`). The chat continues on the chosen host on the same conversation, and the agent is told it moved. Supported: Claude, Codex, Pi, OpenCode.

Deck carries the agent's conversation file (Claude's transcript, Codex's rollout, Pi's session file) or, for OpenCode, the export its bridge writes after each turn (`<session>.opencode.json`), which the other host's `opencode import` takes in. The tab here closes (not into the Trash), so the conversation runs in one place only; moving it back undoes the move. The receiving host takes at most 32 MB (megabytes).

### One site on every node

Deck is one site. Its nodes (the hosts) stand in an order, Settings → Hosts → Site order (`order`, else the inventory's order); the first node in it that is up serves the site and is the **writer**: the only node that changes the site's state (settings, scripts, shelf, board, devices, saves). Every other node is a **follower** holding a byte-for-byte copy, fed by the writer's journal (`deck/site.py`; `DATA/journal.jsonl`, one entry per changed file, sent whole). A follower's page works: it reads from the copy, and its writes (`/api/settings`, `/api/buttons`, `/api/shelf`, `/api/saves`, `/api/devices`) go to the writer through it. Sessions stay on the node that runs them, and so do their chats and each node's session trash; every node runs its own, and the tab column shows them all.

The tab column is pushed, not polled (`deck/api/live.py`): each host lists its sessions once a second, and at once after a call that starts, closes, renames or restarts one; a change goes to the other hosts at once (`POST /api/sessions/peer`), and each open page holds one socket (`/api/sessions/stream`) that gets the whole column when any of it changes. The page falls back to asking every 2 s when it has no socket (an older host), else every 30 s; hosts still ask each other every 5 s, as a fallback for an older host or one that came back.

Every node polls the nodes above it in the order every 2 s. When all of them have missed 5 polls (10 s) it becomes the writer; it hands back as soon as a higher node answers as the writer again, after replaying to it what it wrote meanwhile (`POST /api/site/replay`). A node that was off catches up from the writer's journal (`GET /api/site/journal?after=`), or from a snapshot of the shared files when it is further behind than the journal keeps (`GET /api/site/snapshot`, 20,000 entries). A follower's header says which node serves, with a link to it.

Chats are not part of the site's state: they stay on the node that runs them, and a page reads one from its node (through `/peer/<host>/` for another node's). A node still on older code may offer its chats (`POST /api/site/offer`); the writer takes nothing. A linked PC (`deck/links.py`) is a node too, even while its forwarder is down, so the order does not shift when one is away. Nothing here needs Tailscale: the nodes talk over whatever address the host list gives them (a tailnet name or a local iroh forwarder).

### Offline nodes' chats

When a node is offline its tabs stay in the column, marked offline, with the last list this node had. Its chats stay on it until it is back: tapping one says so. (Until 2026-11-04 every chat was copied to every node, so an offline node's chat could be read and carried on elsewhere; that copying kept every page reloading the shared state while any agent worked, and was removed.) Move host carries a chat to another node while both are up.

### Saves

Harness → Save (agent sessions in a terminal) asks the agent to write a handoff note, which is copied to every other host (`deck/saves.py`, `DATA/saves/`). Harness → Saves lists them; Load hands one to the open agent session, chats included. A deleted save can be restored for 60 days.

### Session descriptions

Settings → General → Chat → Session descriptions (`describe`, off by default). After each agent turn, a small model rewrites a description of the session in at most 50 words: what it is working on, what it has done, what it waits on (`deck/describe.py`). At most the last 6000 characters of each turn are sent.

| Described by (`describer`) | Runs | Notes |
|---|---|---|
| Claude Haiku (`claude`) | `claude -p` | On that CLI's (command-line interface) own login |
| OpenAI (`codex`) | `codex exec` | `DECK_DESCRIBE_CODEX_MODEL` overrides the model (default `gpt-5.6-luna`) |

Each tab keeps one describer session, resumed every turn. The description shows in the tab's menu (Description, right-click on PC or hold on touch) and to agents that list sessions. Stored in `DATA/descriptions.json`; a closed tab's entry goes after 30 days.

### Agents can see sessions

Settings → General → Advanced → Agents can see sessions (`sessionagents`, off by default). Lets agents list, read and message other sessions on every host. Every agent's Deck note describes the routes:

| Route | Effect |
|---|---|
| `GET /api/agent/sessions` | Lists every host's agent sessions, with descriptions, as Markdown. `?here=1`: this host only |
| `GET /api/agent/sessions/{name}` | End of one session's output. `?chars=` (default 6000, range 200–50000) |
| `POST /api/agent/sessions/{name}/send` | Body `{"text", "from"}`. Types the message in, marked as from that agent, not the user. Agent sessions only |

Errors: `400` for text starting with `/`, an empty text, a message to oneself, or a non-agent target; `409` when the target is waiting on your answer to a question; `403` while the setting is off on the answering host. Another host's sessions: the same paths under `/peer/{host}`; each host answers by its own setting.

In a chat, a message between sessions is outlined in both: "To" and the target in the sender's chat (read from its send command), "From" in the receiver's. Tap the session name to open it. Copy text and the read route mark it `[from agent in HOST/NAME, not the user]`. The sender is whoever the agent claims to be; Deck does not check. The setting syncs to your other hosts. It is not a security boundary: see [security.md](security.md#good-to-know).

### Brain

The Brain button (beside Resume) opens a menu for the brain chat: a session working in the Brain's data repository (Settings → General → Advanced → Brain data, `brain`: a project such as `github.com/you/second-brain`, or a folder in one; the brain chat is one started in that folder).

| Item | Effect |
|---|---|
| Select messages | Tap several chat messages, then Send to Brain: asks the brain chat which source they come from (a link or path, or that it is unsure). Ingests nothing |
| Lint wiki | Sends a lint job to the brain chat |
| Ingest newest | Sends an ingest job for the newest uncited file in `raw/` |

### Projects

A session's project is its git repository (or a folder under home). Agents report a change with `PUT /api/sessions/{name}/project`; Harness → Project sets it by hand. A shelf link can be made its project's main page; the newest one is shown in a bar atop every chat in that project. Settings → General → Projects lists each project's main page, with `×` to unset it.

## Agents

The `+` picker, the key editor and the key builder offer only the agents set up on the host: Claude Code, Codex and OpenCode where their command is found, Pi, agy and OpenClaw where installed.

### Claude Code

- Runs in its own terminal; the chat reads its transcript and hooks. A tool approval is an Allow / Deny card.
- Other dialogs it stops on (Claude Code 2.1.317's "Session paused": a refused message, or a model that needs usage credits) show as a card with the dialog's own text and options, read off its screen; a pick presses the arrow keys to that option and Enter. A dialog Deck cannot read is a note to answer it in the terminal. Answered in the terminal, the card closes.

### OpenCode

- Install: `curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path && ln -sf ~/.opencode/bin/opencode ~/.local/bin/opencode`.
- Works without login on OpenCode Zen's free model; `opencode auth login` adds providers.
- First message goes in `--prompt`.
- **Sampling** (Harness → Sampling): temperature (0–2), top_p (0–1) and top_k (0–1000) for OpenCode's `plan` and `build` agents, saved in Settings (`sampling`, synced to every host) and applied by the plugin `deploy/agents/opencode-sampling.js` from the next message. Blank uses the model's default; models that take no temperature are skipped, as are models listed in `DATA/opencode-sampling-skip.json` (the bridge adds a model there when it rejects sampling).
- **OpenRouter routing** (`openrouter` setting, API (application programming interface) only, synced): provider preferences merged into every OpenRouter request from Deck's OpenCode sessions, including helper agents, by the same plugin. Other providers keep their own routing. Settings are read on each request.

  | Field | Values |
  |---|---|
  | `sort` | `price`, `latency`, `throughput` |
  | `data_collection` | `allow`, `deny` |
  | `zdr` | Boolean (zero data retention) |
  | `preferred_min_throughput`, `preferred_max_latency` | Object of `p50`/`p75`/`p90`/`p99` to a positive number, or a bare number (meaning `p50`) |

  Example, cheapest first, no data collection, zero data retention, preferring median throughput of at least 50 tokens/s and median time to first token of at most 3 s:

  ```json
  {"openrouter":{"sort":"price","data_collection":"deny","zdr":true,"preferred_min_throughput":{"p50":50},"preferred_max_latency":{"p50":3}}}
  ```

  Send it with `PUT /api/settings`. Performance thresholds are preferences, so slower fallbacks remain possible. `{}` clears the override (kept as `{}` so the clear syncs).

### Pi

- [pi](https://github.com/earendil-works/pi) coding agent. Deck's setup installs it: Linux `install.sh` (pi.dev installer, linked into `~/.local/bin`), Windows `windows-setup.py` (`install.ps1`).
- Never asks permission, so no YOLO setting.
- Deck's note goes in `--append-system-prompt`; Restart uses `--continue`.
- Model: pick in Pi (`/model`, or `defaultProvider`/`defaultModel` in `~/.pi/agent/settings.json`).

### Durable Pi (experimental)

Settings → General → Advanced → Durable Pi (`pidurable`, per host; shown only where `pi-durable` is installed). Runs Pi as pi's experimental durable agent on the Claude bridge (pi-claude-bridge): every step is saved as it happens, so after a crash or a killed pane, Restart (`pi-durable --continue || pi-durable`, never plain pi) finishes the interrupted turn. Chats do not use it, and every new Pi session is a chat, so the switch currently has no effect: a legacy terminal Pi tab's Restart keeps whichever binary started it.

- Sessions are kept apart from regular Pi sessions, in `~/.pi/agent/experimental/durable-sessions`.
- Setup: `deploy/agents/pi-durable/setup.py`, run by both installers; needs pi and pi-claude-bridge. It checks out pi's source at the installed version in `~/.pi/durable-src` (about 320 MB, rebuilt when pi's version changes), applies `durable.patch`, adds `bridge-main.ts`, and installs `pi-durable` in `~/.local/bin` (plus `.cmd` on Windows). Where the system Node cannot run TypeScript (older than 22.19, or a distribution build such as Ubuntu's) it fetches Node 22 into `~/.pi/durable-node`. Where something is missing it removes `pi-durable`, which hides the switch.
- Starts only on pi's default model when that is a Claude bridge model (it exits rather than fall back to another provider), and refuses any request whose system prompt the bridge cannot match to a captured prompt, so nothing goes out as third-party use. `PI_DURABLE_CAPTURE_CHECK=1 CLAUDE_BRIDGE_DEBUG=1` logs what Claude Code would receive instead of sending.
- Not available in durable mode: other pi extensions, prompt templates, the session picker, images.

### agy

Google's Antigravity CLI `agy`; install and sign in with Google's installer. Shown where `agy` is on the `PATH` or in Google's default install location.

- Runs as a chat through its print mode (`--input-format stream-json --output-format stream-json`): one turn per message, tool calls and their output, the conversation's title as the tab name.
- YOLO is `--dangerously-skip-permissions`; otherwise agy's own `toolPermission` setting decides. Ask before tools starts agy with `--settings '{"agentMode": ""}'` (Default mode, this session only), since agy 1.5.7 starts in Auto when no mode is given. Its headless mode cannot show native permission prompts: when agy blocks a tool, the chat offers to allow tools for the session and retry the last message.
- Mode (message box): `accept-edits`, `auto`, `plan`.
- No stop message, so Stop restarts agy on the same conversation. `/model` (choices from `agy models`) restarts it with `--model`.
- agy does not replay a resumed conversation, so the bridge keeps its own copy of each chat (`DATA/chat/agy/<conversation>.jsonl`) to show again after Restart or `/resume`. `/resume` lists the folder's conversations from agy's `conversation_summaries.db`.
- Context: Deck's note goes in `DATA/agy-context/AGENTS.md`, passed with `--add-dir`. When the machine notes have an `AGENTS.md`, its `AGENTS.md`, `PREFERENCES.md`, `PROFILE.md` and `RESOURCES.md` are copied into `DATA/agy-context/.agents/rules/` (synced with `deck-notes sync` at session start).

### OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) (`openclaw`). Install it and set up its Gateway with `openclaw onboard --install-daemon`, or point it at another host's Gateway (`gateway.mode remote`, `gateway.remote.url`, `gateway.remote.token`). Shown only where `openclaw` is on the `PATH`.

- Runs as a chat on the Gateway's one shared conversation (`main`): every host's OpenClaw chat shows the same conversation live, whoever sent each message, so closing a tab loses nothing.
- `deploy/agents/openclaw-bridge/bridge.mjs` (Node, on `@openclaw/gateway-client`; its packages install on first use) holds the Gateway connection. The adapter sends `chat.send`, `chat.abort` (Stop) and `chat.history`, and shows `chat` and `agent` events: streamed text, tool cards with output.
- Each host is its own device to the Gateway (key in `DATA/openclaw-device.json`). A new device needs `openclaw devices approve <requestId>` on the Gateway host once; the chat says so.
- `/model` picks from the Gateway's models (returning to a Claude model also clears the conversation's runtime pin, which a GPT (OpenAI model) turn sets). `/compact` compacts. `/new` and `/resume` are unavailable (one conversation).
- Questions (`ask_user`) show as question cards: tap an option, type an answer (the message box answers too), or Skip. Answered on another device, the card closes; an expired or cancelled one says so. They need the Gateway's `operator.questions` scope, which Deck requests once per host; approve it with the `openclaw devices approve <requestId>` the chat shows (the chat works meanwhile).
- Files in replies (`MEDIA:` lines, the message tool's attachments) show in the chat: images inline, audio and video playable, anything else as a download. The host's bridge fetches each from the Gateway (`artifacts.download`, then the Gateway's short-lived media link) into the chat's folder, so the page never talks to the Gateway or sees its token.
- Not yet supported: approvals, effort.
- The agent works in its own workspace (`~/.openclaw/workspace` on the Gateway host), not the session's folder.
- A tab started as a terminal earlier keeps it on Restart: `openclaw tui --session main`.

### Codex chat

- Plans update in place as steps progress; shell output arrives while commands run.
- The status bar shows context use and the available account usage windows.
- Agent calls, image-viewer calls, web searches, warnings, and resolved approval or question cards appear in the chat.
- These come from Codex's reported events; unavailable usage values are omitted.

## Button types

A key's type sets what it runs, in its Folder.

| Type | Runs | Legacy terminal command |
|---|---|---|
| Shell script | `bash -lc <command>` (default folder `~`) | — |
| Claude prompt | A chat; the prompt is its first message | `claude "<prompt>"` |
| Codex prompt | A chat; the prompt is its first message | `codex "<prompt>"` |
| OpenCode prompt | A chat; the prompt is its first message | `opencode --prompt "<prompt>"` |
| Pi prompt | A chat; the prompt is its first message | `pi "<prompt>"` |
| agy prompt | A chat; the prompt is its first message | `agy -i "<prompt>"` |
| OpenClaw prompt | A chat on its Gateway's `main` conversation; the prompt is its next message | `openclaw tui --session main --message "<prompt>"` |

Agent keys default to Settings → General → New sessions → Default folder (`folder`, per host), else `$DECK_WORK` (`~/work`).

An agent key with no saved prompt opens an ask sheet on tap: one-off prompt (blank starts an empty session), YOLO, and folder chips (default, recent, projects under `~/work`). Recent folders are kept in `DATA/recent.json`.

## Key builder

New script → **Or have an agent build it**. Starts the chosen agent (Claude, Codex, OpenCode, Pi or agy) in `~/work/deck-keys` with the request. With more than one host, **Build on** chips pick the host: another host builds through this host's `/peer` proxy, so the agent, script and key all live on that host and its tab opens here.

- Rules: `deploy/agents/deck-keys/AGENTS.md` (imported by `CLAUDE.md`), copied into the folder on deploy.
- The agent writes scripts to that folder (a git repository), tests them, and registers keys with `deck-key` (`deck/cli.py`; wrapper in `~/.local/bin`).

```
deck-key list
deck-key add --label L --type <type> --text T [--cwd DIR] [--id ID]
deck-key remove ID
```

`--type` accepts `shell`, `claude`, `codex`, `opencode`, `pi`, `agy`, `openclaw`. `add` with a matching `--id` updates that key.

## Agent context

Agent sessions started from Deck get a short note (`DECK_CONTEXT` in `deck/agents.py`). It covers:

- The host name.
- Message stamps: each chat message starts with `[when · device, how]`, where how is phone app, phone browser or PC browser (judged from the page's touch screen and width). On a phone, or with no stamp (the terminal view adds none), replies stay phone-sized: short, one question at a time, tap-button answers, plain URLs, no QR (Quick Response) codes.
- Stop and ask when stuck.
- How to read the shelf, pin a page to it, link a file to it, and set the session's project.
- How to list, read and message other sessions.
- When the machine notes have an `AGENTS.md`: where they are, plus `PREFERENCES.md` and `PROFILE.md` in full.

Deck's agent routes answer only while the user allows it (the shelf's Agents box, Settings → General → Advanced → Agents can see sessions).

| Agent | Delivery |
|---|---|
| Claude | `--append-system-prompt-file DATA/agent-context.md` |
| Pi | `--append-system-prompt DATA/agent-context.md` |
| Codex | Terminal: `-c developer_instructions=...`; chat: `--deck-context DATA/codex-context.md` to the bridge |
| OpenCode | `OPENCODE_CONFIG_CONTENT` with `instructions` pointing at `DATA/opencode-context.md` (rewritten at each start) |
| agy | `--add-dir DATA/agy-context` |

Panes also get `DECK_SESSION=1` and `DECK_SESSION_NAME` (the session's name, used as the `from` of its messages). With machine notes, `install.sh` also points the host's Claude and Codex at them and adds a Claude SessionStart hook that runs `deck-notes sync` (`deploy/agents/agent_context.py`).

## Board

Deck's first view on a browser's first visit; after that Deck opens on the last view used (user 2026-11-04) (top bar → **Board**, the last tab; user 2026-10-31: "make it the first view"; 2026-11-02: "make the board last view tab"). One board for every session and host (`deck/board.py`, `DATA/board.json`, synced to every node). It works like a cork board: square cards stay where they are dragged until removed. A card not yet moved takes the first free spot.

| Action | Effect |
|---|---|
| Drag a card (anywhere on it) | Moves it there and puts it on top (`PUT /api/board/{id} {x, y, z}`) |
| Drag a card's lower-right corner, or pinch on it (PC: Ctrl+wheel or trackpad pinch over it) | Resizes that card, 30% to 300% (`{s}`); it stays square |
| Pinch on the board (PC: Ctrl+wheel); toolbar − / % / + / fit | Zooms the whole board, kept per device |
| Toolbar **+** | Adds your own card: pick Note, Link or List; each asks only for what it needs |
| A card's **×** (top right; on a PC it shows on hover), or drop the card on the trash can | Removes it; tap the trash can to restore one (kept 30 days). There is no Clear all |
| Tap a note's text | Shows all of it (a square card cuts long text) |
| Tap a list item's box | Ticks it (`{tick: line}`) |
| A card's session line | Opens that session (dimmed once it has ended) |

The toolbar sits at the lower right. The tab shows a count of cards posted since you last looked.

Card types (`kind`): `note` (Markdown text, title optional), `link` (`url`, title optional), `list` (one item per line of `text`, `[x] ` before a done one), `status` (a run's progress; blue edge), `alert` (needs you; red edge). Agents post with `POST /api/board {kind, title, text, url, key, session}`; the same `session` and `key` again update that card in place (it keeps its spot and size), so a run keeps one status card current. `GET /api/board` lists every card, newest first, trashed ones with `deleted`; `DELETE /api/board/{id}` trashes one (`?forever=1`: gone), `POST /api/board/{id}/restore` brings it back.

## Shelf

A column right of the chat (`deck/shelf.py`, `DATA/shelf.json`, synced to every host). On a phone, shut, it is a strip as wide as the tab strip on the left; open, it lies over the chat's right side as wide as the open tab column, so the chat shows beside it. Settings → General → Advanced → Shelf: new items go at the bottom or top (`shelfnew`).

| Source | Item |
|---|---|
| **Save turn** on a reply | Your message, the reply, tool calls and their output |
| **Save messages** on a reply | The agent's words only |
| **+ File** | A document (see [Documents and Review](#documents-and-review)) |
| Agent: `POST /api/shelf/pin` | A web page link; one item per address, so pinning again updates it. Can be made its project's main page |
| Agent: `POST /api/shelf/link {path, title}` | A linked document (see below) |

| Action | Effect |
|---|---|
| Drag an item | Reorder |
| Drop on another item | Makes a folder (no nested folders); Autoname names it from its contents |
| Drop on the chat | Attaches it to the next message |
| Delete | Moves it to the shelf's trash for 30 days (a folder takes its contents) |

**Deck files** sits at the shelf's top: an accent-edged, dashed tray with a monospace file tree, so it never looks like a saved item. It is read from disk each time it opens, never stored on the shelf, and does not sync. **Skills** lists each skill in the skills folder ([Skills](#skills)) with its description and up to 60 text files (hidden folders skipped). A file opens in Review as a tab (on a phone, over the screen); Edit saves it to the skill's own file, keeping its line endings, with no commit. ↻ reads the folders again.

**Agents** (the shelf's own checkbox, `shelfagents`, off by default) lets agents on the host read the shelf (`GET /api/shelf/agent`; `?id=ID` one item, `?all=1` everything).

## Documents and Review

**+ File** on the shelf adds PDF (Portable Document Format) files, Word files (`.docx`) and text files, 30 MB at most. The browser extracts the text (pdf.js from the CDN (content delivery network) for PDFs; a scanned PDF has none) and the shelf keeps it, so it syncs and agents can read it like any item. The file stays on the host it was added on (`DATA/shelf-files/<id>`, its text beside it as `<id>.txt`); other hosts open it through that host.

**Review** (PC view tab) puts the chat on the left and a reader on the right, with a tab per open item. Drag a document or saved turn in from the shelf, or drop a file from the computer (it goes on the shelf too). Drag the divider to share the width; double-click it for half and half. **Notes** opens a machine notes file. **↻** shows the open document as saved now. The reader also redraws by itself within 2 s when an agent or another device changes a text document, unless you are editing it. **Download** saves the open document's file (a PDF as the PDF, from whichever host has it); a document's shelf card has **Download** too, on a PC and a phone. **Tell agent** (`readernote`) names the open documents in your next message.

| Format | Display | Editing |
|---|---|---|
| PDF | Pages with selectable text | — |
| Word | Formatted (mammoth.js) | Read-only |
| Markdown | Formatted | Rich editor (below); **Markdown** switches to the raw text |
| LaTeX, BibTeX (`.tex`, `.sty`, `.cls`, `.ltx`, `.bib`) | Syntax colours | Plain text |
| Other text | As is | Plain text; Save (or Ctrl+S) writes the file on its host and the text agents read |

`+` and `−` zoom. **+ New** makes a Markdown document on the shelf.

Markdown editor: TipTap (from esm.sh, which provides one ProseMirror), reading Markdown with marked and writing it back with turndown. Dark page with a toolbar (headings, bold, italic, strikethrough, code, links, lists, quotes, code blocks); saved a moment after each change; the title is the document's name. Nothing is saved unless something was edited. Markdown it cannot represent (tables, checkbox lists, raw HTML (Hypertext Markup Language), footnotes) opens as plain text instead.

A **linked document** is a file on its host shown as itself, not a copy. Any node can link a file on its own disk: a follower reads the file and sends it to the writer with the request, so the item records the follower as the file's host. A save in the reader writes the file (keeping its line endings), and the reader redraws within 2 s when the file changes on disk (an agent's edit, git, a build rewriting a PDF), unless it is being edited.

Text selected in the reader goes with the next message as a quote (with its page); `×` above the message box drops it. Dragging a document onto the chat attaches its file's path when the chat runs on that host (agents read the file), else its text.

## Settings

Tap the Deck name. One page; every control saves the moment it changes (`PUT /api/settings` with just that field) and applies live. There is no Save or Cancel; Done closes it. An invalid value (for example a missing folder) shows its error and is not saved.

`GET /api/settings` returns all settings with defaults filled in, plus read-only fields (`fresh`, `agents` (agent types set up on this host), `pi`, `agy`, `openclaw`, `durable`, `chatagents`, `notesname`, `transcribe`). `PUT` updates only the fields sent. Defaults are not stored: `DATA/settings.json` holds only changed values. Settings sync to every host, field by field, except the per-host keys marked below.

### Settings page

Three tabs: **Appearance** (Look, Accent colour, Theme, Text size, Quick keys; the first, and where Settings opens until another is picked), **General** and **Hosts**, what only matters with more than one host (names and colours, Site order, Open another host when this one is down, PCs). The last tab picked opens next time, per browser. With one host the Hosts tab is greyed out and can't be opened: nothing in it applies yet, and PCs (linking a second) sits in Settings → General → More instead. Nothing a one-host Deck uses is only there: this PC's colour is Settings → Appearance → Accent colour, and Devices, Host sessions on this PC and Agents can see sessions are in General.

Every control is the same kind of row: its name, a short hint under it, and the control on the right (a switch, a menu, a short field), or under the name for a wide one (Name, Default folder, Brain data, Text size, Look, Theme, Quick keys, Shelf). Rows that open something (More's Devices, Notes, Guide, App; PCs) end in ›. Default model and Default effort are small headings over one row per agent. Deck's messages and docs name a setting by its whole path, tab, then section, then row (Settings → General → Advanced → Scripts); a tab with one section (Appearance, Hosts) skips the section (Settings → Appearance → Look).

Sections not marked (tab) are in General, in this order on a PC; on a phone General shows More, Chat and New sessions first.

| Section | Control | Key |
|---|---|---|
| Deck | Name (the whole Deck's, on every host; at most 24 characters) | `alias` |
| Hosts (tab) | Names and colours: a name and colour per host, set from any host | `hostnames` |
| Hosts (tab) | Site order | `order` |
| Hosts (tab) | Open another host when this one is down (phone app only; stored in the app) | — |
| Hosts (tab) | PCs ([Linked PCs](#linked-pcs)); with one host, in Settings → General → More | — |
| Logins | Each host's agents and tools (Claude Code, Codex, OpenCode, GitHub, agy, Hugging Face): signed in, or **Log in** (see [Logins](#logins)) | — |
| New sessions | Choose agent each time; Default agent (shown when it is off; agents set up on this host only) | `agentpick`, `agent` |
| New sessions | Skip permissions (YOLO) by default | `yolo` |
| New sessions | Default folder (replaces `~/work` for agent sessions) | `folder` |
| New sessions | Default model (every agent, and Computer sessions) | `model` |
| New sessions | Default effort (Claude, Codex, Pi, agy) | `effort` |
| Chat | Tool calls in chats | `chattools` |
| Chat | Thinking expanded | `thinkopen` |
| Chat | Jump to newest replies | `chatjump` |
| Chat | Session descriptions; Described by | `describe`, `describer` |
| Appearance (tab) | Look: Pad / Rack / Blueprint / Zine / Plain / Message (see [Looks](#looks)) | `look` |
| Appearance (tab) | Accent colour: this PC's colour (the same as its row in Hosts) | `led`, `hostnames` |
| Appearance (tab) | Theme: System / Light / Dark | `theme` |
| Appearance (tab) | Text size (10–36 px), kept apart for phones and PCs; also in the Layouts menu | `font`, `pcfont` |
| Layouts menu (PC header) | UI size: the whole page zoomed, 75–150%, kept apart for phones and PCs | `ui`, `pcui` |
| Layouts menu (PC header) | UI text: the page's own text (tabs, buttons, menus, settings, shelf, board) without zooming the rest, 75–150%; chats and terminals keep Text size. Kept apart for phones and PCs | `uitext`, `pcuitext` |
| Appearance (tab) | Quick keys | `hidekeys` |
| Projects | Each project's main page | (shelf items) |
| More | Devices (paired phones and PCs), Notes, Guide (illustrated how-to), App (Android download, Android browsers only) | — |
| Advanced | Host sessions on this PC | `hosting` |
| Advanced | Agents can see sessions | `sessionagents` |
| Advanced | Scripts (show the Scripts bar) | `scripts` |
| Advanced | Message box | `composer` |
| Advanced | Resizable session pane (PC only; not shown on phones) | `resize` |
| Advanced | Infra changes expanded | `notesopen` |
| Advanced | Debug view | `debug` |
| Advanced | Text terminal | `textterm` |
| Advanced | Agent status bar | `crop` |
| Advanced | Durable Pi | `pidurable` |
| Advanced | Shelf: new items go Bottom / Top | `shelfnew` |
| Advanced | Brain data | `brain` |

Advanced is folded on phones and open on a PC.

### Logins

Settings → General → Logins, and the second step of a new install's setup (after **Host sessions**), list each host's installed agents and tools with their sign-in state (`deck/logins.py`). Each is checked with its own read-only status command (`claude auth status`, `codex login status`, `opencode auth list`, `gh auth status`, `hf auth whoami`), in parallel; a result is reused for 20 s. **Log in** opens a terminal tab on that host running the tool's own login (`claude auth login`, `codex login --device-auth`, `opencode auth login`, `gh auth login --web`, `hf auth login`); its sign-in link is clickable, and the tab says when the tool is signed in. Deck never sees or keeps the login: each tool stores its own.

**Log in** is refused for a tool that is already signed in: some logins drop the saved one as they start (Codex deletes `~/.codex/auth.json`). agy has no login command: its **Log in** tab opens agy, which asks to sign in. Pi signs in inside its own app (`/login`), so it is not listed. Hosts that don't answer, or run an older Deck, are left out.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/logins` | This host's tools: `[{tool, name, what, optional, signedin}]`; `?fresh=1` skips the 20 s cache |
| POST | `/api/logins/{tool}` | Start a login tab: `{session}`; 400 when the tool is unknown, missing or already signed in |

### Settings keys

| Key | Type / values | Default | Scope |
|---|---|---|---|
| `alias` | String, ≤ 24 chars | `""` (host name shown) | Shared |
| `hostnames` | `{host: {alias, led}}`, `led` as `#rrggbb`; merged | `{}` | Shared |
| `led` | `#rrggbb`; this host's colour (API only; overridden by `hostnames`) | `""` | Per host |
| `pad` | List of key IDs (script key order) | `[]` | Shared |
| `folder` | Existing folder | `""` (`$DECK_WORK`) | Per host |
| `yolo` | Boolean | `false` | Shared |
| `model` | `{agent: alias}`; merged; `""` clears | — | Shared |
| `effort` | `{agent: level}`; merged; `""` clears | — | Shared |
| `chattools` | Boolean | `true` | Shared |
| `thinkopen` | Boolean | `false` | Shared |
| `notesopen` | Boolean | `false` | Shared |
| `chatjump` | Boolean | `true` | Shared |
| `readernote` | Boolean (Review's and Code's Tell agent) | `true` | Shared |
| `debug` | Boolean | `false` | Shared |
| `describe` | Boolean | `false` | Shared |
| `describer` | `claude`, `codex` | `claude` | Shared |
| `sessionagents` | Boolean | `false` | Shared |
| `shelfagents` | Boolean (the shelf's Agents box) | `false` | Shared |
| `shelfnew` | `bottom`, `top` | `bottom` | Shared |
| `brain` | Project name, or a folder (stored as its project) | `""` | Shared |
| `hosting` | Boolean; off makes a client-only PC | `true` | Per host |
| `order` | List of host names, highest first: the first one up serves the site | `[]` (inventory order; a node with none takes the serving node's) | Shared |
| `pidurable` | Boolean | `false` | Per host |
| `textterm` | Boolean | `true` on Windows, else `false` | Shared |
| `composer` | Boolean | `true` | Shared |
| `crop` | Boolean | `true` | Shared |
| `resize` | Boolean | `true` | Shared |
| `scripts` | Boolean | `false` | Shared |
| `theme` | `system`, `light`, `dark` (`paper`: legacy, shown as Light) | `system` | Shared |
| `look` | `pad`, `rack`, `blueprint`, `zine`, `plain`, `message` | `pad` | Shared |
| `font`, `pcfont` | Integer, clamped to 10–36 | `13` | Shared |
| `hidekeys` | Subset of `harness`, `esc`, `tab`, `stab`, `ctrlc`, `up`, `down`, `left`, `right`, `enter` | `[]` | Shared |
| `sampling` | `{plan\|build: {temperature, top_p, top_k}}`; merged; `""` or `null` clears a value | — | Shared |
| `openrouter` | Object (see [OpenCode](#opencode)) | — | Shared |
| `agentpick` | Boolean: `+` asks which agent; off starts `agent` | `true` | Shared |
| `agent` | Default agent: `+` with `agentpick` off, and the key builder | `claude` | Shared |
| `start` | `term`, `deck`, `agents`. Legacy: Deck always opens on Terminal | `term` | Shared |
| `chat` | Boolean. Legacy: hidden and ignored | `false` | Shared |

Page state, written by the page so every browser and the phone app open where the last one left off:

| Key | Holds |
|---|---|
| `session` | Open session on phones |
| `pcsession` | Open session on PCs. Kept apart from `session` because one session shown on a phone and a PC at once would keep resizing between their sizes |
| `tabs` | Tab order |
| `tabfolders` | Tab folders: `[{id, title, open, tabs}]` |
| `hostshut` | Folded hosts |
| `scriptshut` | Scripts folded away |
| `layout` | PC columns: widths (`padw`, `feedw`, `padcw`, `feedcw`, `termw`, and `bpadcw`, `bfeedcw`, `btermw` for Minimize) and flags (`nopad`, `nofeed`, `nobottom`, `full`, `msgshut`) |
| `layouts` | Saved layouts: `[{id, name, layout}]`, at most 12 |

### Looks

Settings → Appearance → Look picks the page's whole style and layout; Theme (System / Light / Dark) applies to each. The page is one structure (`deck/static/style.css`: layout, behaviour, every colour and font a token) with one stylesheet per look in `deck/static/looks/`, loaded over it before the first paint and swapped in place when the setting changes. The host's LED colour is the accent in every look. The key row and the message box always sit under the screen column, never under the tab column or the shelf.

| Look | What it is |
|---|---|
| Pad | The console, where Deck began (a Stream Deck): a matte body with the displays recessed into it, and the sessions as a grid of square keys, each with its agent's mark in the session's colour, its name and a status LED; the open one lit. Scripts are keys too. Keycaps that press, a dot-matrix readout, tracked mono labels; a chat reads as a log on the display. Archivo and IBM Plex Mono. |
| Rack | A channel strip: the sessions run across the top of the screen as modules in a rack, each with an LED, a silkscreen label and a meter that runs while it works; the scripts are patch buttons at the strip's start, the trash a module at its end. Graphite panels with rails; Light is the silver faceplate edition, its displays still dark. Barlow Semi Condensed and IBM Plex Mono. |
| Blueprint | A drafting sheet: grid paper under everything in one ink (Prussian blue on white; in Dark, the blueprint itself). The tab column is the parts list, a ruled table with item numbers; the agent's status line is the title block, boxed cells in the sheet's corner; your messages are revisions marked with a triangle; tool calls are callouts with a leader; registration marks on the corners. IBM Plex Sans and Roboto Mono. |
| Zine | Cut and pasted: 2px rules, hard offset shadows, block type for the masthead and headings (Anton), a grotesk for reading. The host's colour is a marker pen: it fills the open tab, highlights bold text and the Settings headings, underlines what needs you; your messages are black blocks; tool calls sit in dotted boxes; the shelf's clippings sit slightly askew. In Dark the paper is black and the shadows are in the marker's colour. |
| Plain | One surface: the columns are ruled off by lines, not boxes, and the screen is the page. The system's own type on a short scale, weights 400 and 500, a reading measure for prose; a chat is a transcript (the agent's words plain, yours in a thin frame). The accent marks only the open tab and the Working dot; buttons that act are ink on paper. No web fonts. |
| Message | A text thread. The tab column is a conversation list (a round avatar in the session's colour, the name, the host; a dot when it needs you; the open one filled in the host's colour); the chat is bubbles with tails, yours on the right, the agent's grey on the left, "Delivered" and the time under yours, tool calls and thinking as the small centred lines a thread shows between messages; the message box is a pill with a round Send; the status is a red badge. The system's type at a phone's sizes; dialogs are sheets with grouped rows. No web fonts. |

## Hosts

One page serves every host. Their sessions share the tab column through this host's `/peer/{host}` proxy, so Deck never sends you to another host's own site. The header shows the Deck's name, the same on every host; the tab column names each host.

Host list (`GET /api/hosts`):

- Every `[hosts.*]` entry with a `deck_url` in the machine notes' `inventory.toml` (`DECK_INVENTORY`, default `$DECK_NOTES/inventory.toml`; TOML is Tom's Obvious Minimal Language).
- Plus the linked PCs. A host in both uses the inventory's address.
- The current host is `DECK_HOST_NAME` (default: the system host name).

**iroh between inventory hosts.** An entry may also carry `iroh = "<64-hex key>"`, the host's iroh key (its `~/.config/deck/iroh.id`). Each other host with a key then gets a local `deck-iroh connect` forwarder, checked every 1.5 s with `GET /api/site/role` and the host key:

- After 3 answers in a row, this host's server reaches it through the forwarder: session lists, `/peer/{host}`, site sync, moves, saves and notes.
- At the first miss it goes back to the `deck_url` (Tailscale), so a dead route is off within about 3 s. A follower takes over the site only after 10 s without the writer, so the switch back always comes first.
- Browsers and the phone app still get the `deck_url`. `GET /api/hosts` gives each other host `url` (the server's route now), `web` (its `deck_url`), `via` (`iroh`, `tailscale` or `address`) and `iroh` (its key). Settings → Hosts → Names and colours shows `via` under each name.
- `deploy/update-all` and `deck-notes` run outside the server and always use the `deck_url`.
- Removing the `iroh` lines puts every host back on its `deck_url` within seconds, without a restart.
- A forwarder ends with its server, even one killed with no cleanup: on Windows it joins a job that closes with the server, on Linux it gets `SIGTERM` when the server dies.

Every host shows the whole network. One of them serves the site at a time, the first in the site order that is up (see [One site on every node](#one-site-on-every-node)); the others hold its state.

**Colour**: Settings → Hosts → Names and colours sets any host's name and colour (`hostnames`), else the inventory's `led`, else the next unused palette colour.

**Phone app host list** (Settings → General → More → Hosts in the app; the app opens the Deck straight away):

- The Deck appears as one entry, named after it, with its nodes listed under it in the site order. Every host opened tells the app the Deck's name, hosts and order, with each host's iroh key where it has one (`deck.json` in the app's files).
- The entry opens the Deck's nodes in the site order (the first one up serves the site); a node that does not answer within 2 s hands over to the next, and the app says which were down. Tapping a node under the entry opens that one. Settings → Hosts → Open another host when this one is down (in the app only) turns the hand-over off.
- A host with both a Tailscale address and an iroh key tries the Tailscale address first, then iroh.
- A host first saved from a pairing code (by its iroh key) joins the entry once opened; a host outside the Deck keeps its own row.
- A pairing link never fails over (its code belongs to one host). Paired devices sync, so pairing once lets the phone into every host.
- Back on a host's page closes an open dialog, else leaves the app.

## Linked PCs

Settings → General → More → PCs links Decks into one group over iroh, with no Tailscale or machine notes (`deck/links.py`).

1. On PC A, **Link a PC** shows a link code: A's iroh key and a one-time secret, valid 5 minutes.
2. On PC B, **Link to a PC** takes the code. B reaches A through a local `deck-iroh connect` forwarder, claims the code, and waits.
3. Only A approves.
4. B receives the group's host key and member list (`DATA/links.json`, synced like scripts).

Every member keeps a forwarder to each of the others, which is the address the host list gives them. Sessions from every PC share one tab list; moves and sync work as with Tailscale. Joining needs a PC that is not linked yet. A member's own sessions always run on it.

| Action | Effect |
|---|---|
| **Remove** | Takes a PC out; the remaining members get a new host key |
| **Leave** | Takes this PC out; it gets a key of its own |

A removal syncs as a dated tombstone, so a PC linked again afterwards stays.

### Client only

Settings → General → Advanced → Host sessions on this PC, off (`hosting`). A new install asks on first open, on the PC itself. Such a PC starts no sessions of its own:

- Its page starts new sessions on the first linked PC that hosts (`/peer/{host}/api/...`).
- The server refuses session starts (`POST /api/agent`, `/api/shell`, `/api/build`, `/api/run/{id}`) with `409`.
- Its iroh tunnel port answers `403` to everything.
- Other members leave it out of their host lists (its `hosting` in `links.json`, which follows the setting).

The setting is per PC and does not sync.

## Skills

One folder of skills for every agent (`deck/skills.py`). Location `DECK_SKILLS` (default `~/.config/deck/skills`); each skill is a folder holding a `SKILL.md`. At server start and before each agent session, Deck links every skill into the skills folder of each agent CLI installed on the host, so a skill added once reaches all of them, inside Deck or in a plain terminal.

| Agent | Skills folder it reads |
|---|---|
| Claude Code | `~/.claude/skills` |
| Codex, Pi, OpenCode | `~/.agents/skills` (the shared Agent Skills folder) |
| agy | `~/.gemini/config/skills` |

- Links are symlinks, or directory junctions on Windows (no admin rights needed).
- Deck replaces or removes only links that point into `DECK_SKILLS`. A real folder of the same name stays, and is reported.
- A deleted skill's links go at the next run. A folder that already is `DECK_SKILLS` (itself a link to it) is left as is.
- OpenClaw keeps its skills on its Gateway (`openclaw skills install`), so it is not linked.
- `python -m deck.skills` (from the checkout) runs it now and prints each change.
- To share skills across hosts, point `DECK_SKILLS` at a git repo every host clones.
- The shelf's Deck files → Skills shows and edits them ([Shelf](#shelf)).

## Machine notes

A git repository every host keeps a full copy of, with no master (`deck/notes.py`). Location `DECK_NOTES` (default `~/deck-notes`); its `inventory.toml` lists the hosts.

- Each host serves its copy at `/notes.git` (`git http-backend`, behind the same pairing and host key as the rest).
- Commits push to every reachable peer (post-commit hook). Claude sessions start with `deck-notes sync` (pull from peers, then push).
- A down host is skipped and catches up later: every host is checked at once with a 3-second connect before git runs, so one that is off costs about 3 s per sync.

| Command | Effect |
|---|---|
| `deck-notes init` | Sets a host up |
| `deck-notes pull`, `push`, `sync` | Pull from peers, push to peers, or both |
| `deck-notes status` | Each peer: in sync, missing our changes, has changes we lack, out of sync, or offline |

Settings → General → More → Notes shows the same status and reads and edits the files (save = commit + push). On phones a service worker keeps the app and the latest notes bundle, so Deck opens offline and Notes shows the phone's copy read-only.

## Phone app

The Android app (`app/`, Capacitor; `app/push.sh` installs it over adb, the Android Debug Bridge) gives host pages its native plugins. `app/native/MainActivity.java` adds Capacitor's bridge for the `allowNavigation` hosts.

**Notes carrying**: on every host open, the page trades notes with the host (`POST /api/notes/carry`). It sends the phone's git bundle and edits queued offline; the host merges them (pushing on to reachable peers) and returns its bundle and files, which the app keeps in its own storage (`notes/` in app data). Hosts never need to be up together. The reply counts commits each side lacked, and the page says when the phone brought changes from other hosts. The host list's **Notes** reads and edits the phone's copy with no host; edits wait and are saved by the next host opened. An edit to a file that host changed since is refused and stays marked for review.

**Dictation**: where the mic button shows, the app uses its Dictation plugin (`app/native/DictationPlugin.java`; the WebView has no Speech API). On Android 13+ it uses Google's on-device recognizer, with formatting (punctuation, capitals) and one session that keeps listening through pauses. Without an on-device model for the language it starts the model download and uses the regular recognizer meanwhile.

## Phone alerts

1. Set `DECK_NTFY=https://ntfy.sh/<random topic>` in `DATA/env` and restart Deck.
2. Subscribe to that topic in the [ntfy](https://ntfy.sh) app.

When an agent tab finishes working (after 20 s or more of work) or stops on a question, the phone gets a notification (`deck/alerts.py`): tab name, state and host only, never terminal text. Tapping it opens Deck. The topic name is the only protection on ntfy.sh, so make it long and random, or use your own ntfy server.

## Screen tab

Optional, per host. When `DATA/vnc.json` (`DECK_VNC_FILE`) exists, a Screen tab streams the host desktop with noVNC 1.7 (from the CDN). VNC is Virtual Network Computing.

| `vnc.json` field | Meaning |
|---|---|
| `password` | VNC password |
| `path` | Where the websockify bridge is served on Deck's origin (default `/vnc/websockify`; for example `tailscale serve --set-path /vnc`) |
| `bridge` | websockify's local address (for example `127.0.0.1:6080`). Deck then serves it at `/vnc/` itself, so the tab also works over iroh; without it the tab is hidden over iroh |
| `vnc_on`, `vnc_off` | Commands (argument lists, run without a shell) that start the VNC server when a stream opens and stop it a minute after the last one closes; without them, the Windows **Deck VNC on/off** tasks |

- The desktop sits above the open tab's chat (phone and PC alike), so the agent can be messaged while watching. It takes
  the larger share of the height; drag the divider between them to change it (remembered per browser), double-click for
  the default.
- More room for the desktop: the bar (Stream, Zoom, Control, host, ⚙) floats over the stream's top edge instead of
  taking a row, and with a mouse it fades while streaming until the pointer is over the screen. The shelf folds to its
  strip while the Screen view is open (its remembered state is kept). The desktop's default share of the height is 80%.
- **Chat** (in the bar) hides the chat's history under the screen, leaving its message box, so the desktop takes the
  rest of the height; again brings it back. Remembered per browser (`deck.screensolo`).
- Connects only while the tab is open and Stream is on. View-only unless Control is ticked.
- Host picker (beside Control, shown when more than one host has a screen): every Deck host with a Screen tab set up, this
  one first; another host's desktop comes through `/peer/<host>/`. Switching stops the stream and streams the chosen
  host's if one was playing. Remembered per browser (`deck.screenhost`); with nothing remembered it is this host's, else
  the first host that has one.
- The box under the screen is hidden (user 2026-11-02: "remove the chat bar below window"); its code remains. When shown, it sends to what the picker beside it names: **Desktop** types the text on the desktop through
  `sendKey` (phone keyboards cannot type into the canvas); a running session (any host) gets it as a message, stamped
  like a chat message; **+ New Claude session** starts one with it and then sends there. Above the box, the picked
  The choice is remembered per browser (`deck.screento`); with none it is the open tab's session.
- Goal row (under the desktop, where the box was; shown when the host has a computer-use loop, `computer.json` `goal`): type a goal and
  **Run** starts the loop with it (40 steps; one run per host; `POST /api/screen/goal {goal, steps}`). **Pause** holds
  it after the current step, so the desktop is yours (tick Control); **Resume** goes on from the screen as you left it;
  **Stop** ends it after the current step. Beside them: state, step and the model's latest note. Deck and the loop talk
  through `DATA/desk-goal/control` (Deck writes `run`, `pause` or `stop`; the loop reads it between steps) and
  `status.json` (the loop's state), with the loop's output in `run.log`; the loop gets their paths as `DESK_CONTROL` and
  `DESK_STATUS`. A loop that reads neither still runs, and Stop ends its process group. **View log** opens the run's
  steps (time, who decided, the action and its result, the model's note) and every model call (fast or vision, the model,
  seconds, the reply; tap one for everything it was sent and the screenshot the vision model saw) in the shelf's place, live while it runs
  (`GET /api/screen/goal/log`, from the loop's `loop.jsonl`); × or the button again brings the shelf back.
- ⚙ (end of the Screen bar) opens Screen settings for the host shown (user 2026-11-02: "no opus, let me set the paid
  model", "need a new setttings menu for the sreen view"): the paid model the loop asks when its fast model is unsure
  (the agent CLI, `claude-code`, `codex` or `opencode`, and the model name; default `sonnet`), its effort (low, medium,
  or the CLI's default), whether SubQ decides first, whether to find icons (OmniParser; shown only when the host's `computer.json` names an icons server), and steps per goal (40). Its rows look like Settings', but it has Save and Cancel: nothing applies until Save, and then from the next Run. Saved in `DATA/desk-goal/settings.json`
  (`GET`/`POST /api/screen/goal/settings`) and passed to the next run as `CLICKER_WRITER_API`, `CLICKER_ANSWER_MODEL`,
  `CLICKER_ANSWER_REASONING` and `LOOP_FAST`.
- The paid model is one Claude Code session per goal (user 2026-11-02: "sonnet is 1 session, unless i reset. it gets the
  normal deck context and info about the computer use"; then "reset per goal"): every screen-reading call of a run
  resumes it, and each Run starts a new one. Claude Code's advisor (a stronger model it may consult) is off for these
  calls. Its system prompt gets Deck's context (machine notes included) and the
  loop's brief (`DATA/desk-goal/context.md`: never invent facts about the user, no accounts or final Submit unless
  asked) and the text of `computer.json` `context_files` (files a goal may need, such as a CV; PDFs through pdftotext), since it has no tools. Each goal starts with a plan call (the goal restated, its steps, and missing facts; user 2026-11-02: "no rules or
  facts"); the
  plan becomes the notes both models read each step, and a step that needs a missing fact stops the run and names it.
- Cost: every call's tokens and cost (Claude Code's API-price estimate; SubQ at `LOOP_FAST_PRICE`, default 0) in View
  log, and the run's total in the goal row.
- The VNC server must be loopback-only, bridged by websockify.

Setup: [screen.md](screen.md).

### Phone screen (WSL hosts)

With the [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver) installed on Windows ([setup](screen.md#phone-screen-wsl-hosts); its `C:\VirtualDisplayDriver\vdd_settings.xml` lists portrait modes, for example 1080×1620…2400), the Screen bar shows **Phone**:

- On: attaches that monitor to the right of the others at the mode closest to the frame's shape, and zooms the stream to it at 250% scaling (phone-sized text). Off detaches it.
- While on, **Windows** lists the desktop's app windows: tap one to move it onto the phone screen maximized, again to send it back. Elevated windows such as Task Manager cannot be moved.
- Implementation: `deck/win/phonescreen.ps1` (SetDisplayConfig + ChangeDisplaySettingsEx, no admin) via `/api/screen/phone` and `/api/screen/windows`.

## Computer sessions

A Computer session is Claude with a host's desktop as tools, started on that host (user 2026-11-02: "it should be a
session on the machine"). **+** (or a host header's +) offers **Computer** where the host has `DATA/computer.json`
(`DECK_COMPUTER_FILE`). Opening its tab shows that host's screen above the chat, streaming (the Screen tab's picker
follows it); the tab bar's Terminal hides it for that tab, Screen brings it back.

- Model: Settings → General → New sessions → Default model, its Computer row (`model.computer`); blank, Claude's. A
  chat's own Model pick overrides it; Restart keeps it. The Screen goal row's loop has its own models (⚙ in the Screen bar).
- The tools (`deck/computer_mcp.py`, an MCP (Model Context Protocol) server over stdio, allowed without asking):
  `screenshot`, `click`, `double_click`, `right_click`, `move`, `drag`, `type_text`, `key` (chords like `ctrl+c`),
  `scroll`, `wait`, and `run_goal`, which hands a whole multi-step task to the host's computer-use loop and returns its
  last lines and a screenshot. Each action returns a screenshot after it. Screens larger than 1568 px are sent scaled
  down; coordinates are the screenshot's pixels.
- Stop: the mouse in the top-left corner of the driven screen makes every action fail with a message telling Claude to
  stop and ask. Stop in the chat ends the turn, as in any chat.

| `computer.json` field | Meaning |
|---|---|
| `python` | The Python that runs the server: Pillow, plus pyautogui (X11) or `typesafe_computer_use` (Windows) |
| `backend` | `windows` (a monitor, through SendInput) or `x11` (an X display, through pyautogui) |
| `monitor` | Windows: a piece of the monitor's device name or ID (`BNQ`); unset, the primary |
| `display`, `start` | X11: the display (`:5`), and a command that starts it when its socket is missing |
| `hold`, `on_exit` | X11: a lock file held while the session runs (the desktop's stop script leaves it up), and a command run when it ends |
| `goal`, `goal_after` | The loop `run_goal` runs: the command, the goal appended, then `goal_after`, then `--steps=N` if asked. Without it there is no `run_goal` |
| `about` | One line telling Claude which desktop it drives |
| `context_files` | Files whose text goes in the Screen goal row's paid-model context (the resume) |
## Agent browser

Headless Chrome for agents through Playwright MCP (Model Context Protocol).

**Linux**: `bash deploy/agents/agent-browser.sh` (any host; sudo once).

- Installs Google Chrome and one Playwright MCP service per host (user unit `agent-browser`, `http://127.0.0.1:8931/mcp`), registered for Claude and Codex. OpenCode sessions started from Deck get it through Deck's OpenCode config (`OPENCODE_CONFIG_CONTENT`). Pi has no MCP support.
- Each agent session gets its own isolated headless context, so agents browse in parallel.
- Logins: `agent-chrome` opens the sign-in profile `~/.agent-chrome` visibly (a WSLg, WSL graphics, window on Windows). When it closes, `agent-browser-export` copies its cookies and local storage to `~/.config/agent-browser/state.json` (mode 0600), which every new agent context loads.
- Re-run `agent-chrome` when a site logs agents out; cookie refreshes made by agents are not saved back.
- Sites with bot checks may block headless agents (eBay showed a captcha).

**Windows (PC app)**: `powershell -ExecutionPolicy Bypass -File deploy\agents\agent-browser.ps1` (no admin) does the same with Windows Chrome and Node: logon task "Agent browser" (`conhost --headless`), MCP `playwright` for Windows Claude and Codex, sign-in profile `~\.agent-chrome` opened by Start menu → Agent Chrome (`~\.local\bin\agent-chrome.cmd`), logins in `~\.config\agent-browser\state.json`.

## Reference

Ports, server environment variables and installed files: [install.md](install.md#reference).

### Session environment variables

| Variable | Set on | Value |
|---|---|---|
| `DECK_SESSION` | Every Deck pane and chat | `1` |
| `DECK_SESSION_NAME` | Every Deck pane and chat | The session name |
| `OPENCODE_CONFIG_CONTENT` | OpenCode panes | Deck's OpenCode config: `instructions`, the agent browser MCP (where installed), the sampling plugin |
| `DECK_SAMPLING` | OpenCode panes | Path to `settings.json`, read by the sampling plugin |
| `DECK_SAMPLING_SKIP` | OpenCode panes | Path to `opencode-sampling-skip.json` |

### Data files

All in `DATA` (`~/.config/deck`). "Synced" files are the site's state: the writer changes them and its journal copies them to every other node (`deck/site.py`); settings keep their per-node fields (`led`, `folder`, `pidurable`, `hosting`) on each node.

| File | Contents | Synced |
|---|---|---|
| `env` | Per-host environment (`KEY=VALUE`) | No |
| `settings.json` | Changed settings and page state | Yes, except per-host keys |
| `buttons.json` | Script and agent keys | Yes |
| `shelf.json` | Shelf items and its trash | Yes |
| `shelf-files/<id>`, `<id>.txt` | Shelf documents and their text | No (served by their host) |
| `links.json` | Linked PCs | Yes |
| `devices.json` | Paired devices (approved ones sync) | Yes |
| `host-key` | Shared host key | No |
| `chat/<session>.jsonl`, `.in.jsonl`, `.hooks.jsonl`, `.state`, `.uploads/` | Chat events, input queue, Claude hooks, saved state, uploads | No |
| `chat/agy/<conversation>.jsonl` | agy chat copies | No |
| `bridges/<session>.json`, `.lock`, `.exit`, `.log` | Each chat's bridge: registry entry, run lock, exit code, output | No |
| `journal.jsonl` | The site's journal: every change to a shared file, newest last (20,000 kept) | Written by the writer, replayed by followers |
| `session-trash-local.json` | Closed agent chats (30 days), this host's; `session-trash.json` is the old shared copy, read once | No |
| `saves/` | Handoff notes | Copied to every host |
| `descriptions.json` | Session descriptions | No |
| `describe/` | Describer model sessions' working folder | No |
| `recent.json` | Recent folders (8) | No |
| `runs/<session>.log` | Script output | No |
| `agent-context.md`, `codex-context.md`, `opencode-context.md`, `agy-context/` | Agent notes, rewritten at each start | No |
| `opencode-sampling-skip.json` | Models that reject sampling | No |
| `openclaw-device.json` | This host's OpenClaw Gateway device key | No |
| `vnc.json` | Screen tab config | No |
| `iroh.key`, `iroh.id` | Tunnel secret and public key | No |

### HTTP routes

All routes require a paired device, the host's own browser, or the host key, except the open routes: `/`, `/sw.js`, `/static/*` (not `/static/deck.apk`, unless it carries a fresh app link's token), `/api/me`, `/api/pair/claim`, `/api/link/claim`, `/api/link/wait`. "Host key" marks routes that accept only another host's host key.

**Page and assets**

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | The page |
| GET | `/static/app.js` | `deck/static/app/*.js` joined in order (ETag, entity tag: assets version) |
| GET | `/static/*` | Static files |
| GET | `/sw.js` | Service worker |
| GET | `/ws/{name}` | Terminal or chat WebSocket |

**Keys and runs**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/buttons` | List keys |
| POST | `/api/buttons` | Create or update a key |
| DELETE | `/api/buttons/{id}` | Delete a key |
| POST | `/api/run/{id}` | Run a saved key |
| POST | `/api/run` | Run a key sent whole (another host's "Run on") |
| POST | `/api/agent` | Start an agent session `{agent, text, cwd, yolo}` |
| POST | `/api/shell` | Open a terminal (`{new: true}` for another) |
| POST | `/api/build` | Start the key builder `{request, agent}` |
| POST | `/api/bug` | Start the bug fixer `{report}`: Opus at medium effort in Deck's checkout |
| GET | `/api/folders` | Folder chips: default, recent, projects |

**Sessions**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/sessions` | This host's sessions |
| GET | `/api/sessions/all` | Every host's sessions (others from a cache) |
| GET (WebSocket) | `/api/sessions/stream` | The same, sent again each time any of it changes (the page's tab column) |
| POST | `/api/sessions/peer` | Another host's session list, pushed when it changes (host key) |
| DELETE | `/api/sessions/{name}` | Close a session (agent chats go to the Trash) |
| POST | `/api/sessions/{name}/send` | Type into a session |
| POST | `/api/sessions/{name}/restart` | Restart its agent |
| POST | `/api/sessions/{name}/move` | Move host |
| POST | `/api/sessions/{name}/rename` | Rename the tab (empty: back to the program's title) |
| PUT | `/api/sessions/{name}/project` | Set its project `{folder}` or `{project}` |
| GET | `/api/sessions/{name}/text` | Plain-text output (Copy text) |
| GET | `/api/sessions/{name}/media/{file}` | A chat's media file |
| POST | `/api/sessions/{name}/upload` | Upload a file to a chat (`?name=`) |
| GET | `/api/sessions/{name}/describe` | Its description `{text, at}` |
| POST | `/api/sessions/{name}/save` | Save (terminal agent sessions) |
| POST | `/api/sessions/{name}/load` | Load a save into it |
| POST | `/api/clear` | Kill finished sessions |
| GET | `/api/session-trash` | Closed agent chats |
| POST | `/api/session-trash/{id}/restore` | Restore one |
| DELETE | `/api/session-trash/{id}` | Forget one |
| POST | `/api/move-in` | Receive a moved chat (host key) |
| POST | `/api/transcribe` | Forward audio to `DECK_TRANSCRIBE_URL` |

**Agent routes** (see [Agents can see sessions](#agents-can-see-sessions), [Shelf](#shelf))

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/agent/sessions` | List sessions |
| GET | `/api/agent/sessions/{name}` | Read one |
| POST | `/api/agent/sessions/{name}/send` | Message one |
| GET | `/api/shelf/agent` | Read the shelf |
| GET | `/api/deck-context` | The note Deck gives agents (Debug view) |

**Shelf**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/shelf` | List items (each long text cut to 3000 characters, `cut` its full length) |
| POST | `/api/shelf` | Add an item |
| GET | `/api/shelf/{id}` | One item, in full |
| DELETE | `/api/shelf/{id}` | To the trash, or gone |
| POST | `/api/shelf/pin` | Pin a web page `{url, title, note, session}` (`main`, `folder` make it a main page) |
| POST | `/api/shelf/link` | Link a file `{path, title, session}` |
| PUT, GET | `/api/shelf/{id}/file` | Upload or download a document's file |
| GET | `/api/shelf/{id}/mtime` | A linked document's modification time and size |
| PUT | `/api/shelf/{id}/text` | Save a document's text |
| POST | `/api/shelf/{id}/restore` | Restore from trash |
| POST | `/api/shelf/{id}/rename` | Rename |
| POST | `/api/shelf/{id}/main` | Make (or unset, `project: ""`) a project's main page |
| POST | `/api/shelf/{id}/autoname` | Name a folder from its contents |
| POST | `/api/shelf-arrange` | Apply a drag `{order, folders}` |
| GET | `/api/skills` | The skills folder: each skill's name, description and text files |
| GET, PUT | `/api/skills/file` | Read (`?path=skill/file`) or save (`{path, text}`) a skill's file |
| GET | `/api/code/list` | Code view: one folder's entries (`?root=FOLDER&path=REL`), folders first, build and cache folders marked `heavy` |
| GET | `/api/code/file` | Code view: a text file as JSON (`text`, `version`, `eol`, `bom`); `binary` or `big` (over 2 MB) without text. Never served as raw bytes |
| GET | `/api/code/stat` | Code view: a file's `mtime` and `size` (the open tab's 2 s check) |
| PUT | `/api/code/file` | Code view: save `{root, path, text, base, eol, bom, create}`; 409 with the file as it is now when it changed since `base` (null overwrites); `create` makes a new file and never overwrites |

**Saves**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/saves` | List |
| POST | `/api/saves` | Store a save (from another host or a client) |
| GET | `/api/saves/{id}` | One save |
| POST | `/api/saves/{id}/delete` | Delete or restore |

**Settings, hosts and devices**

| Method | Path | Purpose |
|---|---|---|
| GET, PUT | `/api/settings` | Read or update settings |
| GET | `/api/hosts` | Hosts, colours, palette, key order |
| GET | `/api/logins` | Each installed agent and tool's sign-in state ([Logins](#logins)) |
| POST | `/api/logins/{tool}` | Open a tab running that tool's login; refused when it is signed in |
| GET | `/api/me` | This device's pairing state |
| POST | `/api/app/link` | A link to `/static/deck.apk` that an unpaired browser may open for 10 minutes (the phone app can't save downloads) |
| POST | `/api/pair/start` | Make a pairing code |
| POST | `/api/pair/claim` | Claim a code |
| GET | `/api/pair/pending` | Devices waiting for approval |
| POST | `/api/pair/decide` | Approve or deny |
| GET | `/api/pair/qr.svg` | Pairing QR code |
| GET | `/api/devices` | Paired devices |
| DELETE | `/api/devices/{id}` | Remove a device |
| POST | `/api/update` | Pull the latest code and restart if it changed |
| GET | `/api/web/check` | May a URL be framed (Web view) |

**Linked PCs**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/links` | Members |
| POST | `/api/link/start` | Make a link code |
| POST | `/api/link/claim` | Claim a code (open) |
| GET | `/api/link/wait` | Collect the answer (open; needs the claim's token) |
| GET | `/api/link/pending` | Claims waiting for approval |
| POST | `/api/link/decide` | Approve or refuse |
| GET, POST | `/api/link/join` | Joining state; join with a code |
| POST | `/api/link/rekey` | Receive a new host key (host key) |
| DELETE | `/api/links/{name}` | Remove a member |
| POST | `/api/link/leave` | Leave the group |

**Sync between hosts**

| Method | Path | Purpose |
|---|---|---|
| ANY | `/peer/{host}/{tail}` | Proxy to another host |
| GET | `/api/site` | Who serves the site, this node's role, the order |
| GET | `/api/site/role` | This node's role and journal position (host key) |
| GET, POST | `/api/site/journal` | Pull entries after a position, or receive the writer's (host key) |
| GET | `/api/site/snapshot` | Every shared file, as a tar (host key) |
| POST | `/api/site/replay` | Entries a lower node wrote while this writer was away (host key) |
| POST | `/api/site/offer` | An older node's chat offer: answered as taken nothing (host key) |

**Notes**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/notes` | Sync status per peer (cached a minute; `?fresh=1`) |
| GET | `/api/notes/files` | File list |
| GET, PUT | `/api/notes/file` | Read or save (commit + push) a file |
| GET | `/api/notes/bundle` | Every file in one response (offline copy) |
| POST | `/api/notes/carry` | Phone app's bundle and offline edits |
| ANY | `/notes.git/{path}` | `git http-backend` |

**Screen**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/screen` | Screen tab config |
| GET | `/vnc/{tail}` | websockify bridge (when `bridge` is set) |
| GET, POST | `/api/screen/phone` | Phone screen status, on, off |
| GET, POST | `/api/screen/windows` | List or move desktop windows |
