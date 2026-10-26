# Optional features

Deck works out of the box with agent chats, terminals, the tab column and the shelf. Everything on this page is off,
hidden or not installed until you turn it on. Settings are in the page (tap the Deck name); `DATA/env` means
`~/.config/deck/env` on the host, and changes there need a Deck restart. Full details are in the
[reference](guide.md) and [install guide](install.md).

| Feature | What it adds | Turn it on | Default |
|---|---|---|---|
| [Tailscale access](#tailscale-access) | Reach Deck from any browser on your tailnet | `tailscale serve` on the host | Off (the iroh tunnel is used) |
| [Linked PCs](#linked-pcs) | Several PCs in one tab list, with chats that move between them | Settings → More → PCs | Off |
| [Machine notes](#machine-notes) | A shared git repository of notes every host and agent reads | `DECK_NOTES`, `inventory.toml` | Empty repository |
| [Phone alerts](#phone-alerts) | A notification when an agent finishes or asks a question | `DECK_NTFY` in `DATA/env` | Off |
| [Extra agents](#extra-agents) | OpenCode, agy and OpenClaw in the `+` picker | Install the agent | Hidden until installed |
| [Default agent](#default-agent) | `+` starts your usual agent with no picker | Settings → New sessions | Off (the picker asks) |
| [Skip permissions (YOLO)](#skip-permissions-yolo) | Agents run commands without asking | Settings → New sessions | Off |
| [Scripts](#scripts) | One-tap keys for commands and saved prompts | Settings → Advanced → Scripts | Hidden |
| [Agents can see sessions](#agents-can-see-sessions) | Agents list, read and message your other sessions | Settings → Advanced | Off |
| [Agents read the shelf](#agents-read-the-shelf) | Agents read your saved items | The shelf's Agents box | Off |
| [Session descriptions](#session-descriptions) | A short summary of each session, written by a small model | Settings → Chat | Off |
| [Screen tab](#screen-tab) | The host's desktop, streamed over VNC | `vnc.json` | Hidden |
| [Agent browser](#agent-browser) | Headless Chrome for agents, with your logins | `agent-browser.sh` | Not installed |
| [Host dictation](#host-dictation) | Speech-to-text on your own server instead of Google | `DECK_TRANSCRIBE_URL` | Off |
| [OpenRouter routing and sampling](#openrouter-routing-and-sampling) | Provider and sampling settings for OpenCode | Settings API, Harness → Sampling | Off |
| [Brain](#brain) | Send chat messages to a notes wiki chat | Settings → Advanced → Brain data | Off |
| [Client-only PC](#client-only-pc) | A PC that controls others but runs no sessions | Settings → Advanced → Host sessions | Off (every PC hosts) |
| [Experimental](#experimental) | Text terminal, Durable Pi | Settings → Advanced | See below |

## Access

### Tailscale access

The default way in is the iroh tunnel built into the phone app: no account and no open ports. If you already use
[Tailscale](https://tailscale.com), it lets any browser on your tailnet reach Deck.

1. On the host: `sudo tailscale serve --bg 8787`.
2. Install or re-run with the URL: `bash deploy/install.sh https://<host>.<tailnet>.ts.net`.
3. Pair each device as usual. `DECK_AUTH` controls whether tailnet devices must pair ([security](security.md)).

Never point a reverse proxy at port 8787; use the tunnel port 8788 and list it in `DECK_ORIGINS`.

### Linked PCs

Links several PCs into one Deck over iroh, with no Tailscale and no machine notes. Every PC's sessions share one tab
list, scripts, shelf and settings, and agent chats can move between PCs (Harness → Move host).

1. On PC A: Settings → More → PCs → **Link a PC**. A code appears, valid for 5 minutes.
2. On PC B: Settings → More → PCs → **Link to a PC**, then enter the code.
3. Approve on PC A.

Details: [Linked PCs](guide.md#linked-pcs).

### Machine notes

A git repository of notes that every host keeps a full copy of (`~/deck-notes` by default, `DECK_NOTES`). Agents
started from Deck are pointed at it when it has an `AGENTS.md`, and get its `PREFERENCES.md` and `PROFILE.md` in
full, so one set of instructions reaches every agent on every host. Its `inventory.toml` can also list hosts reached
over Tailscale. Edit it from Settings → More → Notes. Details: [Machine notes](guide.md#machine-notes).

## Agents

### Phone alerts

1. Add `DECK_NTFY=https://ntfy.sh/<long random topic>` to `DATA/env` and restart Deck.
2. Subscribe to that topic in the [ntfy](https://ntfy.sh) app.

Alerts carry the tab name, state and host only, never terminal text. The topic name is the only protection on
ntfy.sh, so make it long and random, or run your own ntfy server.

### Extra agents

The Linux installer installs Claude Code, Codex and Pi. Others appear in the `+` picker once installed on the host:

| Agent | Install | Notes |
|---|---|---|
| OpenCode | See [OpenCode](guide.md#opencode) | Works without a login on OpenCode Zen's free model |
| agy (Google's Antigravity CLI, command-line interface) | Google's installer | [agy](guide.md#agy) |
| OpenClaw | `openclaw onboard --install-daemon` | One shared conversation through its Gateway; [OpenClaw](guide.md#openclaw) |

### Default agent

`+` normally asks which agent to start, offering only the agents set up on that host. Turn off Settings → New
sessions → Choose agent each time and pick a Default agent: `+` then starts it straight away. On a host where that
agent is not set up, the picker still opens. Hold `+` (right-click on a PC) to open the picker anyway.

### Skip permissions (YOLO)

YOLO ("you only live once") mode lets agents run commands and edit files without asking. Settings → New sessions →
Skip permissions (YOLO) by default turns it on for new sessions; the New session sheet and each agent key have their
own toggle. Use it only in folders where a mistake is cheap.

### Scripts

One-tap keys at the top of the tab column. A key runs a shell command or starts an agent with a saved prompt.
Turn on Settings → Advanced → Scripts, then tap `+` to add one, or let an agent write and test the script for you
(**Or have an agent build it**). Keys sync to every host. Details: [Scripts](guide.md#scripts).

### Agents can see sessions

Settings → Advanced → Agents can see sessions lets agents list your sessions on every host, read their recent output
and send them messages. Messages between agents are outlined in both chats. Deck does not check who sent a message,
so this is a convenience, not a security boundary. Details: [Agents can see sessions](guide.md#agents-can-see-sessions).

### Agents read the shelf

The Agents box on the shelf lets agents on the host read your saved replies and documents. Agents can always pin
pages and link files to the shelf; this box only controls reading.

### Session descriptions

Settings → Chat → Session descriptions. After each agent turn a small model (Claude Haiku, or an OpenAI model through
Codex) rewrites a summary of the session in at most 50 words. It shows in the tab's menu and to agents that list
sessions. Up to the last 6000 characters of each turn are sent to that model.

## Host extras

### Screen tab

Streams the host's desktop with noVNC (VNC: Virtual Network Computing). You run a VNC server on loopback and a
websockify bridge, then describe them in `DATA/vnc.json`. View-only unless Control is ticked. Setup:
[screen.md](screen.md).

### Agent browser

Headless Chrome for agents through Playwright MCP (Model Context Protocol), with your site logins.

- Linux: `bash deploy/agents/agent-browser.sh`.
- Windows: `powershell -ExecutionPolicy Bypass -File deploy\agents\agent-browser.ps1`.

Run `agent-chrome` to sign in to sites once; every agent context then loads those cookies.
Details: [Agent browser](guide.md#agent-browser).

### Host dictation

The PC mic button uses the browser's speech recognition, which in Chrome sends audio to Google. Set
`DECK_TRANSCRIBE_URL` in `DATA/env` to a speech-to-text endpoint that takes a POSTed audio file and returns
`{"text"}` (for example WhisperWriter's `/transcribe`), and the host transcribes instead. The phone app always uses
Android's on-device recognizer where available.

### OpenRouter routing and sampling

For OpenCode only. Harness → Sampling sets temperature, top_p and top_k. The `openrouter` setting sets provider
preferences (price or speed order, no data collection, zero data retention) for every OpenRouter request from Deck's
OpenCode sessions. Details: [OpenCode](guide.md#opencode).

### Brain

Settings → Advanced → Brain data names a notes repository (a "second brain" wiki). The message box's Brain button then
sends selected chat messages, lint jobs and ingest jobs to the chat working in that repository.

### Client-only PC

Settings → Advanced → Host sessions on this PC, off. The PC controls your other linked PCs but starts no sessions of
its own. A new install asks on first open.

## Experimental

These may change or be removed.

| Feature | Where | State |
|---|---|---|
| Text terminal | Settings → Advanced → Text terminal | On by default on Windows hosts; redraws the pane by polling instead of attaching tmux |
| Durable Pi | Settings → Advanced → Durable Pi | Shown only where `pi-durable` installed; has no effect yet, since every new Pi session is a chat |
| Agent status bar | Settings → Advanced → Agent status bar | Deprecated; affects only old terminal-mode Claude and Codex tabs |
