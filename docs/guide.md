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
- [Shelf](#shelf)
- [Documents and Review](#documents-and-review)
- [Settings](#settings)
- [Hosts](#hosts)
- [Linked PCs](#linked-pcs)
- [Machine notes](#machine-notes)
- [Phone app](#phone-app)
- [Phone alerts](#phone-alerts)
- [Screen tab](#screen-tab)
- [Agent browser](#agent-browser)
- [Reference](#reference)

## Overview

| Item | Value |
|---|---|
| Server | `python3 -m deck.server`, bound to `127.0.0.1:8787` (`DECK_HOST`, `DECK_PORT`) |
| Tunnel port | `127.0.0.1:8788` (`DECK_TUNNEL_PORT`); `deck-iroh` forwards here |
| Linux, including WSL (Windows Subsystem for Linux) | `deck` and `deck-iroh` systemd user services |
| Windows | Run by the PC app; sessions use the bundled MSYS2 tmux |
| Sessions | tmux sessions; they survive Deck restarts and updates |
| Clients | Deck Android app, or any browser (installable with "Add to Home screen") |

Ways in:

| Path | Needs | Reaches |
|---|---|---|
| iroh | The Deck phone app; no account, any network. See [iroh tunnel](install.md#iroh-tunnel) | The tunnel port; pairing is always required |
| Tailscale | `tailscale serve` in front of port 8787, giving `https://<host>.<tailnet>.ts.net` | Phone app or any browser |
| SSH (Secure Shell) | Any SSH client | `tmux attach -t <name>` |

The Android app (`app/`, see [Phone app](install.md#phone-app)) is downloadable from the host after `app/build.sh` has built it: Settings → More → App, shown only in an Android browser. Dependencies and versions: [Dependencies](install.md#dependencies).

## Interface

### Header

| Element | Behaviour |
|---|---|
| Name | The Deck's name (Settings → Deck → Name), else the host name. Tap to open Settings |
| Power LED (light-emitting diode) | This host's colour (see [Hosts](#hosts)) |
| View tabs | Terminal; Review and Web (PC only); Screen (only when set up) |
| Readout | `NN REQ` when NN sessions need you; `NO LINK` when the host is unreachable. Hidden otherwise |
| PC buttons | Layouts (save the current column layout as a button, or delete one), Fullscreen (drops the margins; Minimize restores them), Keys and message box (hides both; not shown in chats) |

Opening Deck always lands on Terminal. It restores the session last open on any device of the same class (phones share one, PCs another; phone app or browser), else the newest running session, else a new shell.

A browser on a mouse-driven screen gets the PC layout at the plain URL (Uniform Resource Locator). `/?pc` forces the PC layout and `/?pc=0` forces the phone layout; the choice is remembered per browser.

### Tab column

The column left of the terminal. Shut, it is a strip of tab icons; tap it or `›` to open it (over the screen's edge on a phone, beside it on a PC; remembered per browser).

Contents, top to bottom:

1. **Scripts bar**: the Scripts fold toggle (`scriptshut`) and `+` (new script). Shown when Settings → Advanced → Scripts is on or any script exists.
2. **Script keys**: one row each (icon, name, run clock while running). See [Scripts](#scripts).
3. **`+`**: opens New session: Terminal and each agent set up on that host (Claude, Codex, OpenCode, Pi, agy, OpenClaw; `agents` in `GET /api/settings`). With Settings → New sessions → Choose agent each time off (`agentpick`), `+` skips the picker and starts Default agent (`agent`), falling back to the picker on a host where that agent is not set up. Holding `+` (or right-clicking it on a PC) always opens the picker.
4. **Session tabs**, oldest first. With more than one host, tabs are grouped under each host's name (this host first, "offline" when unreachable), each group with its own `+`.
5. **Trash**: closed agent chats (see [Closing a session](#closing-a-session)).

Tab appearance:

- The agent logo takes a colour derived from the session name (the same on every screen); the open tab is washed in that colour.
- Text is the title the program set (for example the Claude, Codex, OpenCode or Pi task name), else the folder. Long titles scroll like a ticker on touch screens and end in "…" on a PC (full title in the tooltip).
- Orange: the session needs you. Small dot: it is waiting for you.

Tab actions:

| Action | Effect |
|---|---|
| Drag a tab | Reorders (mouse: immediately; touch: after a hold). Order is shared (`tabs`) |
| Hold without moving (touch) | Arrange mode: tabs wiggle, any drag moves one, `+` reads Done |
| Right-click a tab (PC) | Rename, Description, Move host (agent chats), Close |
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

**Message box**: type and Send (adds Enter). One tap on Send submits on finger lift and closes the keyboard. Text is kept if the terminal is disconnected. With a keyboard, Enter sends and Shift+Enter inserts a newline. On a PC the box has a tool row: Harness, Resume and Brain on the left; Mode (agy chats), Model and Effort (chats), `+` (attach files, chats), mic and Send on the right. On a PC the box can be collapsed with its chevron (`layout.msgshut`). Settings → Advanced → Message box off hides it; phones then type straight into the terminal.

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

The tab column, the terminal and, in chats, the shelf sit side by side. Drag the terminal's left or right edge to leave an empty margin; double-click an edge to remove it. Settings → Advanced → Resizable session pane turns this off (`resize`). Column widths and flags are shared (`layout`); saved layouts are `layouts`.

### Web view (PC)

Shows a web page beside the chat. While it is open, links clicked anywhere in Deck open there (Ctrl, Cmd or Shift-click still opens a browser tab). The host checks whether the site allows framing (`GET /api/web/check`); a site that refuses opens in one reused browser pop-up window laid over the pane instead.

### Text terminal (experimental)

Settings → Advanced → Text terminal (`textterm`); on by default on Windows hosts, where a live tmux client runs in a ConPTY (Windows console pseudo-terminal) that redraws and garbles the screen.

Instead of attaching a tmux client, the host polls the pane with `capture-pane` (every 0.1 s, 0.4 s when idle) and redraws only changed rows in the same xterm.js. Keys go in with `send-keys`; the page size with `resize-window` (released on close). Nothing re-attaches on resize. Lost: the program's own terminal modes (mouse clicks, cursor shape). The wheel scrolls on the host instead; pastes go through tmux (`paste-buffer -p`, bracketed when the program asks).

### Agent status bar (deprecated)

Applies only to Claude and Codex tabs still running in a terminal; new agent sessions are chats. Settings → Advanced → Agent status bar (`crop`).

- The agent's input box and status lines (everything below the spinner/token line, or below the conversation when idle) are cropped out and shown as one fixed-height row of chips above the terminal, for example `Opus 5.5 med · ctx 18% · $38 · 5h 17% · wk 2% · bypass`. Codex "left" limits are shown as % used. Chips too wide shrink with an ellipsis, so the bar never resizes the agent.
- Claude and Codex tabs get 14 extra rows hidden below the frame, so that space holds conversation. Claude's footer is 6 rows idle and up to 13 while working; a taller footer leaves a blank band at the top. The extra rows are fixed per tab and set only when the terminal is sized (attach, window resize), since resizing an agent mid-session makes it redraw and garble its footer.
- Claude's values come from Deck's status line script `deploy/agents/agent-statusline.py` (installed by `install.sh` unless you have your own), which writes `~/.cache/deck-status/<pane>.json`; Deck trusts the file only when its writer runs in that pane. Codex's values come from its footer text.
- Menus and permission questions are never covered. Spinner tips are turned off (`spinnerTipsEnabled`, set by `deploy/agents/agent_context.py`) so the footer keeps its height.
- Off: the full agent footer, no chips.

## Sessions

### Names

| Kind | tmux session name |
|---|---|
| Key or agent run | `deck-<label>-<HHMMSS>` (suffix `-2`, `-3`… on collision) |
| Terminals | `deck-shell`, `deck-shell-2`, … |

Finished runs stay until killed (`remain-on-exit`). `POST /api/clear` kills finished Deck sessions. Script output is also logged to `DATA/runs/<session>.log`.

### Closing a session

Tap the open tab once (it turns red), then again within 3 s to close that session; the tab below opens. On a PC, right-click → Close.

A closed agent chat goes to the column's **Trash** for 30 days (`DATA/session-trash.json`, `deck/session_trash.py`). Restore starts it again on its conversation as a new tab, with its own model and effort choices. Terminals, and agents outside chat view, are not kept.

### Chat view

Every new agent session (Claude, Codex, OpenCode, Pi, agy, OpenClaw) starts as a chat. Agents in a terminal are deprecated: the `chat` setting is hidden and ignored (on Windows a tmux pane gives native programs pipes, not a terminal, so agents' own screens cannot take keys). An agent tab started in a terminal earlier keeps it on Restart until closed.

A chat shows:

- Message bubbles and streamed replies (Claude's replies arrive a block at a time).
- Tool calls that wrap; tap one for arguments and output. Settings → Chat → Tool calls in chats off shows only messages (`chattools`).
- Thinking, folded unless Settings → Chat → Thinking expanded (`thinkopen`). Edits to the machine notes repository are shown opened when Settings → Advanced → Infra changes expanded is on (`notesopen`).
- A status bar with model, effort and mode.
- A Stop button while the agent works; Esc and ^C also stop it.
- Permission prompts and agent questions as cards, one at a time: options as buttons (multi-select: toggles, then Submit), a Type something box (the message box answers too) and Skip (declines). Question sources: Claude's AskUserQuestion, Codex's request_user_input in Plan mode, OpenCode's question tool, Pi extensions' select, confirm and input, OpenClaw's `ask_user`.
- Error cards for failed agent requests, with details and **Try again** (asks the agent to continue; you can also change the model). Claude's automatic retries show here too; Stop cancels a retry and clears the working indicator even when Claude sends no completion event.
- Files attached with `+`: uploaded to `DATA/chat/<session>.uploads/` on the session's host (`POST /api/sessions/{name}/upload`); the message names the path.

Settings that affect chats: Jump to newest replies (`chatjump`: returning to a chat you left at its bottom jumps to the newest reply), Debug view (`debug`: also shows what Deck adds and hides: its instructions to the agent, stamps, reminders, hook output). Text size follows Settings → Appearance → Text size.

Naming: a chat is named by the agent (Claude, OpenCode, Pi) or, until then, by its first message (Codex never names one). Renaming the tab renames the conversation in the agent's own lists.

Slash commands: the bridge turns `/compact`, `/resume`, `/new`, `/model` and `/effort` into each agent's own call and passes other slash commands to the agent. Resume lists this folder's past conversations; the picked one continues in the same chat. A session's Model, Effort and mode (Claude's `/mode`; agy's Mode button) choices persist across Restart, Resume and New.

Not available in chats yet: Durable Pi, Save (Saves can load into a chat).

#### How chats run

A chat is still a tmux session, so it survives Deck updates. `deck/chat_bridge.py` runs in the pane and starts the agent in its machine-readable mode:

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

Settings → New sessions. Applied to new sessions and restarts, synced to every host. A chat's own Model or Effort pick (or `/model`, `/effort`) overrides it for that session only. "Agent default" leaves the agent's own.

| Agent | Model (`model`) | Effort (`effort`) levels | Effort flag |
|---|---|---|---|
| Claude | `fable`, `opus`, `sonnet`, `haiku` (`--model`) | `low` `medium` `high` `xhigh` `max` | `--effort` |
| Codex | — | `minimal` `low` `medium` `high` `xhigh` | `-c model_reasoning_effort=` |
| Pi | — | `off` `minimal` `low` `medium` `high` `xhigh` `max` | `--thinking` |
| agy | — | `low` `medium` `high` | Folded into agy's model ID (identifier) |
| OpenCode | — | None (levels differ per model; `/effort` in a chat picks one) | — |

Claude Code saves a `/model` pick as its own default in `~/.claude/settings.json`; the bridge restores the host's previous value.

### Skip permissions (YOLO, "you only live once")

Off by default. Settings → New sessions → Skip permissions (YOLO) by default turns it on for new agent sessions and keys (`yolo`); the ask sheet and each agent key have their own toggle (not Pi). The agent then runs commands and edits files without asking.

| Agent | Flag or equivalent |
|---|---|
| Claude | `--dangerously-skip-permissions` (asks once to accept bypass mode on first use) |
| Codex | `--dangerously-bypass-approvals-and-sandbox`; in a chat, never-ask plus full access |
| OpenCode | `--auto`; in a chat, auto-allow |
| agy | `--dangerously-skip-permissions` |
| Pi | None: Pi never asks |

### Move host

Harness or a tab's right-click menu → Move host, in an agent chat (`deck/session_move.py`, `POST /api/sessions/{name}/move`). The chat continues on the chosen host on the same conversation, and the agent is told it moved. Supported: Claude, Codex, Pi, OpenCode.

Deck carries the agent's conversation file (Claude's transcript, Codex's rollout, Pi's session file) or, for OpenCode, the export its bridge writes after each turn (`<session>.opencode.json`), which the other host's `opencode import` takes in. The tab here closes (not into the Trash), so the conversation runs in one place only; moving it back undoes the move. The receiving host takes at most 32 MB (megabytes).

### Offline hosts' chats

Hosts that share a host key (linked PCs, or inventory hosts) copy their open agent chats to each other whenever one changed and the agent is not mid-reply (`deck/mirror.py`; `DATA/mirrors/`, dropped after 30 days).

When a host is offline its tabs stay in the column, marked offline. Tapping one opens it read-only from this host's copy (your messages and the agent's replies, no tool calls) with **Transfer here**. After confirmation, Transfer here continues the chat on this host from that copy and tells the agent it moved. Anything said on the original host after the copy stays there; when that host returns it closes its own tab (it finds the takeover in `GET /api/takeovers`), so the conversation runs in one place. With no copy, the tap says so.

### Saves

Harness → Save (agent sessions in a terminal) asks the agent to write a handoff note, which is copied to every other host (`deck/saves.py`, `DATA/saves/`). Harness → Saves lists them; Load hands one to the open agent session, chats included. A deleted save can be restored for 60 days.

### Session descriptions

Settings → Chat → Session descriptions (`describe`, off by default). After each agent turn, a small model rewrites a description of the session in at most 50 words: what it is working on, what it has done, what it waits on (`deck/describe.py`). At most the last 6000 characters of each turn are sent.

| Described by (`describer`) | Runs | Notes |
|---|---|---|
| Claude Haiku (`claude`) | `claude -p` | On that CLI's (command-line interface) own login |
| OpenAI (`codex`) | `codex exec` | `DECK_DESCRIBE_CODEX_MODEL` overrides the model (default `gpt-5.6-luna`) |

Each tab keeps one describer session, resumed every turn. The description shows in the tab's right-click menu (Description, PC) and to agents that list sessions. Stored in `DATA/descriptions.json`; a closed tab's entry goes after 30 days.

### Agents can see sessions

Settings → Advanced → Agents can see sessions (`sessionagents`, off by default). Lets agents list, read and message other sessions on every host. Every agent's Deck note describes the routes:

| Route | Effect |
|---|---|
| `GET /api/agent/sessions` | Lists every host's agent sessions, with descriptions, as Markdown. `?here=1`: this host only |
| `GET /api/agent/sessions/{name}` | End of one session's output. `?chars=` (default 6000, range 200–50000) |
| `POST /api/agent/sessions/{name}/send` | Body `{"text", "from"}`. Types the message in, marked as from that agent, not the user. Agent sessions only |

Errors: `400` for text starting with `/`, an empty text, a message to oneself, or a non-agent target; `409` when the target is waiting on your answer to a question; `403` while the setting is off on the answering host. Another host's sessions: the same paths under `/peer/{host}`; each host answers by its own setting.

In a chat, a message between sessions is outlined in both: "To" and the target in the sender's chat (read from its send command), "From" in the receiver's. Tap the session name to open it. Copy text and the read route mark it `[from agent in HOST/NAME, not the user]`. The sender is whoever the agent claims to be; Deck does not check. The setting syncs to your other hosts. It is not a security boundary: see [security.md](security.md#good-to-know).

### Brain

The Brain button (beside Resume) opens a menu for the brain chat: a session working in the Brain's data repository (Settings → Advanced → Brain data, `brain`: a project such as `github.com/you/second-brain`, or a folder in one; the brain chat is one started in that folder).

| Item | Effect |
|---|---|
| Select messages | Tap several chat messages, then Send to Brain: asks the brain chat which source they come from (a link or path, or that it is unsure). Ingests nothing |
| Lint wiki | Sends a lint job to the brain chat |
| Ingest newest | Sends an ingest job for the newest uncited file in `raw/` |

### Projects

A session's project is its git repository (or a folder under home). Agents report a change with `PUT /api/sessions/{name}/project`; Harness → Project sets it by hand. A shelf link can be made its project's main page; the newest one is shown in a bar atop every chat in that project. Settings → Projects lists each project's main page, with `×` to unset it.

## Agents

The `+` picker, the key editor and the key builder offer only the agents set up on the host: Claude Code, Codex and OpenCode where their command is found, Pi, agy and OpenClaw where installed.

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

Settings → Advanced → Durable Pi (`pidurable`, per host; shown only where `pi-durable` is installed). Runs Pi as pi's experimental durable agent on the Claude bridge (pi-claude-bridge): every step is saved as it happens, so after a crash or a killed pane, Restart (`pi-durable --continue || pi-durable`, never plain pi) finishes the interrupted turn. Chats do not use it, and every new Pi session is a chat, so the switch currently has no effect: a legacy terminal Pi tab's Restart keeps whichever binary started it.

- Sessions are kept apart from regular Pi sessions, in `~/.pi/agent/experimental/durable-sessions`.
- Setup: `deploy/agents/pi-durable/setup.py`, run by both installers; needs pi and pi-claude-bridge. It checks out pi's source at the installed version in `~/.pi/durable-src` (about 320 MB, rebuilt when pi's version changes), applies `durable.patch`, adds `bridge-main.ts`, and installs `pi-durable` in `~/.local/bin` (plus `.cmd` on Windows). Where the system Node cannot run TypeScript (older than 22.19, or a distribution build such as Ubuntu's) it fetches Node 22 into `~/.pi/durable-node`. Where something is missing it removes `pi-durable`, which hides the switch.
- Starts only on pi's default model when that is a Claude bridge model (it exits rather than fall back to another provider), and refuses any request whose system prompt the bridge cannot match to a captured prompt, so nothing goes out as third-party use. `PI_DURABLE_CAPTURE_CHECK=1 CLAUDE_BRIDGE_DEBUG=1` logs what Claude Code would receive instead of sending.
- Not available in durable mode: other pi extensions, prompt templates, the session picker, images.

### agy

Google's Antigravity CLI `agy`; install and sign in with Google's installer. Shown where `agy` is on the `PATH` or in Google's default install location.

- Runs as a chat through its print mode (`--input-format stream-json --output-format stream-json`): one turn per message, tool calls and their output, the conversation's title as the tab name.
- YOLO is `--dangerously-skip-permissions`; otherwise agy's own `toolPermission` setting decides. Its headless mode cannot show native permission prompts: when agy blocks a tool, the chat offers to allow tools for the session and retry the last message.
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

Agent keys default to Settings → New sessions → Default folder (`folder`, per host), else `$DECK_WORK` (`~/work`).

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

Agent sessions started from Deck get a short note (`DECK_CONTEXT` in `deck/core.py`). It covers:

- The host name.
- Message stamps: each chat message starts with `[when · device, how]`, where how is phone app, phone browser or PC browser (judged from the page's touch screen and width). On a phone, or with no stamp (the terminal view adds none), replies stay phone-sized: short, one question at a time, tap-button answers, plain URLs, no QR (Quick Response) codes.
- Stop and ask when stuck.
- How to read the shelf, pin a page to it, link a file to it, and set the session's project.
- How to list, read and message other sessions.
- When the machine notes have an `AGENTS.md`: where they are, plus `PREFERENCES.md` and `PROFILE.md` in full.

Deck's agent routes answer only while the user allows it (the shelf's Agents box, Settings → Advanced → Agents can see sessions).

| Agent | Delivery |
|---|---|
| Claude | `--append-system-prompt-file DATA/agent-context.md` |
| Pi | `--append-system-prompt DATA/agent-context.md` |
| Codex | Terminal: `-c developer_instructions=...`; chat: `--deck-context DATA/codex-context.md` to the bridge |
| OpenCode | `OPENCODE_CONFIG_CONTENT` with `instructions` pointing at `DATA/opencode-context.md` (rewritten at each start) |
| agy | `--add-dir DATA/agy-context` |

Panes also get `DECK_SESSION=1` and `DECK_SESSION_NAME` (the session's name, used as the `from` of its messages). With machine notes, `install.sh` also points the host's Claude and Codex at them and adds a Claude SessionStart hook that runs `deck-notes sync` (`deploy/agents/agent_context.py`).

## Shelf

A column right of the chat (`deck/shelf.py`, `DATA/shelf.json`, synced to every host). Settings → Advanced → Shelf: new items go at the bottom or top (`shelfnew`).

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

**Agents** (the shelf's own checkbox, `shelfagents`, off by default) lets agents on the host read the shelf (`GET /api/shelf/agent`; `?id=ID` one item, `?all=1` everything).

## Documents and Review

**+ File** on the shelf adds PDF (Portable Document Format) files, Word files (`.docx`) and text files, 30 MB at most. The browser extracts the text (pdf.js from the CDN (content delivery network) for PDFs; a scanned PDF has none) and the shelf keeps it, so it syncs and agents can read it like any item. The file stays on the host it was added on (`DATA/shelf-files/<id>`, its text beside it as `<id>.txt`); other hosts open it through that host.

**Review** (PC view tab) puts the chat on the left and a reader on the right, with a tab per open item. Drag a document or saved turn in from the shelf, or drop a file from the computer (it goes on the shelf too). Drag the divider to share the width; double-click it for half and half. **Notes** opens a machine notes file. **Tell agent** (`readernote`) names the open documents in your next message.

| Format | Display | Editing |
|---|---|---|
| PDF | Pages with selectable text | — |
| Word | Formatted (mammoth.js) | Read-only |
| Markdown | Formatted | Rich editor (below); **Markdown** switches to the raw text |
| LaTeX, BibTeX (`.tex`, `.sty`, `.cls`, `.ltx`, `.bib`) | Syntax colours | Plain text |
| Other text | As is | Plain text; Save (or Ctrl+S) writes the file on its host and the text agents read |

`+` and `−` zoom. **+ New** makes a Markdown document on the shelf.

Markdown editor: TipTap (from esm.sh, which provides one ProseMirror), reading Markdown with marked and writing it back with turndown. Dark page with a toolbar (headings, bold, italic, strikethrough, code, links, lists, quotes, code blocks); saved a moment after each change; the title is the document's name. Nothing is saved unless something was edited. Markdown it cannot represent (tables, checkbox lists, raw HTML (Hypertext Markup Language), footnotes) opens as plain text instead.

A **linked document** is a file on its host shown as itself, not a copy. A save in the reader writes the file (keeping its line endings), and the reader redraws within 2 s when the file changes on disk (an agent's edit, git, a build rewriting a PDF), unless it is being edited.

Text selected in the reader goes with the next message as a quote (with its page); `×` above the message box drops it. Dragging a document onto the chat attaches its file's path when the chat runs on that host (agents read the file), else its text.

## Settings

Tap the Deck name. One page; every control saves the moment it changes (`PUT /api/settings` with just that field) and applies live. There is no Save or Cancel; Done closes it. An invalid value (for example a missing folder) shows its error and is not saved.

`GET /api/settings` returns all settings with defaults filled in, plus read-only fields (`fresh`, `agents` (agent types set up on this host), `pi`, `agy`, `openclaw`, `durable`, `chatagents`, `notesname`, `transcribe`). `PUT` updates only the fields sent. Defaults are not stored: `DATA/settings.json` holds only changed values. Settings sync to every host, field by field, except the per-host keys marked below.

### Settings page

| Section | Control | Key |
|---|---|---|
| Deck | Name (the whole Deck's, on every host; at most 24 characters) | `alias` |
| Deck | Hosts: a name and colour per host, set from any host | `hostnames` |
| New sessions | Choose agent each time; Default agent (shown when it is off; agents set up on this host only) | `agentpick`, `agent` |
| New sessions | Default folder (replaces `~/work` for agent sessions) | `folder` |
| New sessions | Skip permissions (YOLO) by default | `yolo` |
| New sessions | Default model (Claude) | `model` |
| New sessions | Default effort (Claude, Codex, Pi, agy) | `effort` |
| Chat | Tool calls in chats | `chattools` |
| Chat | Thinking expanded | `thinkopen` |
| Chat | Jump to newest replies | `chatjump` |
| Chat | Session descriptions; Described by | `describe`, `describer` |
| Appearance | Theme: System / Light / Dark | `theme` |
| Appearance | Text size (10–36 px), kept apart for phones and PCs | `font`, `pcfont` |
| Appearance | Quick keys | `hidekeys` |
| Projects | Each project's main page | (shelf items) |
| More | Devices (paired phones and PCs), PCs ([Linked PCs](#linked-pcs)), Notes, Guide (illustrated how-to), App (Android download, Android browsers only) | — |
| Advanced | Open another host when this one is down (phone app only; stored in the app) | — |
| Advanced | Host sessions on this PC | `hosting` |
| Advanced | Agents can see sessions | `sessionagents` |
| Advanced | Durable Pi | `pidurable` |
| Advanced | Infra changes expanded | `notesopen` |
| Advanced | Scripts (show the Scripts bar) | `scripts` |
| Advanced | Debug view | `debug` |
| Advanced | Brain data | `brain` |
| Advanced | Text terminal | `textterm` |
| Advanced | Message box | `composer` |
| Advanced | Agent status bar | `crop` |
| Advanced | Resizable session pane (PC only) | `resize` |
| Advanced | Shelf: new items go Bottom / Top | `shelfnew` |

Advanced is folded on phones and open on a PC.

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
| `readernote` | Boolean (Review's Tell agent) | `true` | Shared |
| `debug` | Boolean | `false` | Shared |
| `describe` | Boolean | `false` | Shared |
| `describer` | `claude`, `codex` | `claude` | Shared |
| `sessionagents` | Boolean | `false` | Shared |
| `shelfagents` | Boolean (the shelf's Agents box) | `false` | Shared |
| `shelfnew` | `bottom`, `top` | `bottom` | Shared |
| `brain` | Project name, or a folder (stored as its project) | `""` | Shared |
| `hosting` | Boolean; off makes a client-only PC | `true` | Per host |
| `pidurable` | Boolean | `false` | Per host |
| `textterm` | Boolean | `true` on Windows, else `false` | Shared |
| `composer` | Boolean | `true` | Shared |
| `crop` | Boolean | `true` | Shared |
| `resize` | Boolean | `true` | Shared |
| `scripts` | Boolean | `false` | Shared |
| `theme` | `system`, `light`, `dark` (`paper`: legacy, shown as Light) | `system` | Shared |
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

## Hosts

One page serves every host. Their sessions share the tab column through this host's `/peer/{host}` proxy, so Deck never sends you to another host's own site. The header shows the Deck's name, the same on every host; the tab column names each host.

Host list (`GET /api/hosts`):

- Every `[hosts.*]` entry with a `deck_url` in the machine notes' `inventory.toml` (`DECK_INVENTORY`, default `$DECK_NOTES/inventory.toml`; TOML is Tom's Obvious Minimal Language).
- Plus the linked PCs. A host in both uses the inventory's address.
- The current host is `DECK_HOST_NAME` (default: the system host name).

Every host shows the whole network, so there is no main host.

**Colour**: Settings → Deck → Hosts sets any host's name and colour (`hostnames`), else the inventory's `led`, else the next unused palette colour.

**Phone app host list**:

- The Deck appears as one entry, named after it. Every host opened tells the app the Deck's name and hosts, with each one's iroh key where it has one (`deck.json` in the app's files).
- The entry opens the host used last. When it does not answer within 2 s, the app tries the Deck's other hosts (most recently used first) and says which were down. Settings → Advanced → Open another host when this one is down (in the app, on by default, per phone) controls this.
- A host with both a Tailscale address and an iroh key tries the Tailscale address first, then iroh.
- A host first saved from a pairing code (by its iroh key) joins the entry once opened; a host outside the Deck keeps its own row.
- A pairing link never fails over (its code belongs to one host). Paired devices sync, so pairing once lets the phone into every host.
- Back on a host's page closes an open dialog, else returns to the host list.

## Linked PCs

Settings → More → PCs links Decks into one group over iroh, with no Tailscale or machine notes (`deck/links.py`).

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

Settings → Advanced → Host sessions on this PC, off (`hosting`). A new install asks on first open, on the PC itself. Such a PC starts no sessions of its own:

- Its page starts new sessions on the first linked PC that hosts (`/peer/{host}/api/...`).
- The server refuses session starts (`POST /api/agent`, `/api/shell`, `/api/build`, `/api/run/{id}`) with `409`.
- Its iroh tunnel port answers `403` to everything.
- Other members leave it out of their host lists (its `hosting` in `links.json`, which follows the setting).

The setting is per PC and does not sync.

## Machine notes

A git repository every host keeps a full copy of, with no master (`deck/notes.py`). Location `DECK_NOTES` (default `~/deck-notes`); its `inventory.toml` lists the hosts.

- Each host serves its copy at `/notes.git` (`git http-backend`, behind the same pairing and host key as the rest).
- Commits push to every reachable peer (post-commit hook). Claude sessions start with `deck-notes sync` (pull from peers, then push).
- A down host is skipped and catches up later.

| Command | Effect |
|---|---|
| `deck-notes init` | Sets a host up |
| `deck-notes pull`, `push`, `sync` | Pull from peers, push to peers, or both |
| `deck-notes status` | Each peer: in sync, missing our changes, has changes we lack, out of sync, or offline |

Settings → More → Notes shows the same status and reads and edits the files (save = commit + push). On phones a service worker keeps the app and the latest notes bundle, so Deck opens offline and Notes shows the phone's copy read-only.

## Phone app

The Android app (`app/`, Capacitor; `app/push.sh` installs it over adb, the Android Debug Bridge) gives host pages its native plugins. `app/native/MainActivity.java` adds Capacitor's bridge for the `allowNavigation` hosts.

**Notes carrying**: on every host open, the page trades notes with the host (`POST /api/notes/carry`). It sends the phone's git bundle and edits queued offline; the host merges them (pushing on to reachable peers) and returns its bundle and files, which the app keeps in its own storage (`notes/` in app data). Hosts never need to be up together. The reply counts commits each side lacked, and the page says when the phone brought changes from other hosts. The host list's **Notes** reads and edits the phone's copy with no host; edits wait and are saved by the next host opened. An edit to a file that host changed since is refused and stays marked for review.

**Chat carrying**: the page keeps a copy of every chat the host can give it (its own agent chats and its copies of other hosts'; `/api/carry/chats`, at most 30 days old) and hands the next host opened the ones it lacks. The host list's **Chats** reads them with no host (Pi and OpenCode chats from an app built after 2026-10-13).

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

- Connects only while the tab is open and Stream is on. View-only unless Control is ticked.
- Typed text goes through `sendKey` (phone keyboards cannot type into the canvas).
- The VNC server must be loopback-only, bridged by websockify.

Setup: [screen.md](screen.md).

### Phone screen (WSL hosts)

With the [Virtual Display Driver](https://github.com/VirtualDrivers/Virtual-Display-Driver) installed on Windows ([setup](screen.md#phone-screen-wsl-hosts); its `C:\VirtualDisplayDriver\vdd_settings.xml` lists portrait modes, for example 1080×1620…2400), the Screen bar shows **Phone**:

- On: attaches that monitor to the right of the others at the mode closest to the frame's shape, and zooms the stream to it at 250% scaling (phone-sized text). Off detaches it.
- While on, **Windows** lists the desktop's app windows: tap one to move it onto the phone screen maximized, again to send it back. Elevated windows such as Task Manager cannot be moved.
- Implementation: `deck/win/phonescreen.ps1` (SetDisplayConfig + ChangeDisplaySettingsEx, no admin) via `/api/screen/phone` and `/api/screen/windows`.

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
| `DECK_SESSION` | Every Deck pane | `1` |
| `DECK_SESSION_NAME` | Every Deck pane | The tmux session name |
| `OPENCODE_CONFIG_CONTENT` | OpenCode panes | Deck's OpenCode config: `instructions`, the agent browser MCP (where installed), the sampling plugin |
| `DECK_SAMPLING` | OpenCode panes | Path to `settings.json`, read by the sampling plugin |
| `DECK_SAMPLING_SKIP` | OpenCode panes | Path to `opencode-sampling-skip.json` |

### Data files

All in `DATA` (`~/.config/deck`). "Synced" files are kept identical across hosts by `deck/sync.py` (item by item, deletions as tombstones kept 60 days; settings field by field).

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
| `sync.json` | Sync versions and tombstones | No |
| `chat/<session>.jsonl`, `.in.jsonl`, `.hooks.jsonl`, `.state`, `.uploads/` | Chat events, input queue, Claude hooks, saved state, uploads | No |
| `chat/agy/<conversation>.jsonl` | agy chat copies | No |
| `mirrors/` | Copies of other hosts' open agent chats (30 days) | Copied, not merged |
| `takeovers.json` | Chats moved here from an offline host (30 days) | No |
| `session-trash.json` | Closed agent chats (30 days) | No |
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

All routes require a paired device, the host's own browser, or the host key, except the open routes: `/`, `/sw.js`, `/static/*` (not `/static/deck.apk`), `/api/me`, `/api/pair/claim`, `/api/link/claim`, `/api/link/wait`. "Host key" marks routes that accept only another host's host key.

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
| GET | `/api/folders` | Folder chips: default, recent, projects |

**Sessions**

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/sessions` | This host's sessions |
| GET | `/api/sessions/all` | Every host's sessions (others from a cache) |
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
| GET | `/api/shelf` | List items (documents' text truncated to 3000 characters) |
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
| GET | `/api/me` | This device's pairing state |
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
| GET | `/api/sync` | Sync status (host key) |
| GET, POST | `/api/sync/{doc}` | Read or push `shelf`, `buttons`, `settings`, `links`, `devices` (host key) |
| POST | `/api/mirror/{host}/{session}` | Receive a chat copy (host key) |
| POST | `/api/mirror/{host}/{session}/move-here` | Transfer an offline host's chat here |
| GET | `/api/mirror/{host}/{session}/text` | Read an offline host's chat from the copy |
| GET | `/api/takeovers` | Chats taken over from this host's peers (host key) |
| GET, POST | `/api/carry/chats` | Phone's chat copies: list, or hand one over |
| GET | `/api/carry/chats/{host}/{session}` | One chat copy |

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
