# Deck user guide

Short answers to "how do I…" for a new user. Each task gives the steps and links to the full description in the [reference](guide.md). Installation is in [install.md](install.md); optional features and how to turn them on are in [optional-features.md](optional-features.md).

Deck also has a built-in illustrated Guide: tap the Deck's name at the top to open Settings, then More → Guide.

"Phone" means a narrow touch screen; "PC" (personal computer) means a wide, mouse-driven screen. Some controls differ between the two.

## Contents

- [Get your phone in](#get-your-phone-in)
- [Find your way around](#find-your-way-around)
- [Start an agent chat](#start-an-agent-chat)
- [Answer questions and permission prompts](#answer-questions-and-permission-prompts)
- [Let an agent work without asking (YOLO)](#let-an-agent-work-without-asking-yolo)
- [Change the model or effort](#change-the-model-or-effort)
- [Stop, restart or resume an agent](#stop-restart-or-resume-an-agent)
- [Use a terminal](#use-a-terminal)
- [Add a script key](#add-a-script-key)
- [Close a tab, and get it back](#close-a-tab-and-get-it-back)
- [Organize tabs](#organize-tabs)
- [Keep things on the shelf and read them in Review](#keep-things-on-the-shelf-and-read-them-in-review)
- [Link a second PC](#link-a-second-pc)
- [Move a chat to another host](#move-a-chat-to-another-host)
- [Get phone alerts](#get-phone-alerts)
- [Remove a lost phone](#remove-a-lost-phone)

## Get your phone in

Deck admits only paired devices, plus the host's own browser. Pair once per phone; paired devices sync, so one pairing lets the phone into every host of your Deck.

1. Install the Android app: the APK (Android Package) from the latest release, or your own build. See [Get the app](install.md#get-the-app).
2. On the host, open Deck, tap its name to open Settings, then More → Devices → Pair a device. Or run `deck-pair` in a shell on the host. A QR (Quick Response) code appears; it is valid for 5 minutes.
3. In the Deck app, tap **Scan a pairing code** and scan it. If you scan with the phone's camera instead, the link opens in the browser: tap **Pair the Deck app instead**.
4. Approve the phone on the host.

The app reaches the host over the iroh tunnel, from any network, with no account. A phone browser without the app can also pair, but it reaches the host only through Tailscale. Details: [Pairing over iroh](install.md#pairing-over-iroh), [Pairing](security.md#pairing).

## Find your way around

1. The **tab column** on the left lists every session, grouped under each host's name. On a phone, tap the strip or `›` to open it.
2. An orange tab needs you; a small dot means it is waiting for you.
3. The header shows the Deck's name. With more than one host, each host has its own LED (light-emitting diode) colour.
4. Tap the Deck's name to open Settings. Every control saves as soon as it changes; Done closes the page.

Deck opens on the session you had open last on the same kind of device. Details: [Interface](guide.md#interface), [Settings page](guide.md#settings-page).

## Start an agent chat

1. Tap `+` in the tab column. With several hosts, use the `+` under the host the agent should run on.
2. Pick an agent. Only agents set up on that host are listed. To skip this step, turn off Settings → New sessions → Choose agent each time and pick a Default agent; then hold `+` (right-click on a PC) when you want the list.
3. Type in the message box and tap Send. With a keyboard, Enter sends and Shift+Enter starts a new line.

Every agent session is a chat: streamed replies, tool calls you can tap for detail, and thinking folded away. On a PC, `+` in the message box attaches files. The agent's tmux session keeps running when the phone sleeps or loses signal. Details: [Chat view](guide.md#chat-view), [Agents](guide.md#agents).

## Answer questions and permission prompts

When an agent asks something or wants permission, the chat shows a card, one at a time.

1. Tap an option. For a multi-select question, toggle the options, then tap Submit.
2. Or type an answer in the card's Type something box, or in the message box.
3. Tap Skip to decline.

The tab turns orange while a card waits. If a request fails, an error card offers **Try again**. Details: [Chat view](guide.md#chat-view).

## Let an agent work without asking (YOLO)

Skip permissions, called YOLO (you only live once), lets the agent run commands and edit files without asking you first. It is off by default.

1. For one session: tick **Skip permissions (YOLO)** in the sheet an agent key opens, or in the key's editor.
2. For every new session: Settings → New sessions → Skip permissions (YOLO) by default.

Risk: a YOLO agent can change or delete anything your user account can, with no prompt to stop it. Use it in folders and on machines where that is acceptable. Pi never asks, so it has no YOLO switch. Details: [Skip permissions](guide.md#skip-permissions-yolo-you-only-live-once), [Good to know](security.md#good-to-know).

## Change the model or effort

1. For one chat: on a PC, use Model and Effort in the message box; anywhere, open Harness → Model or Effort, or type `/model` or `/effort`.
2. For new sessions: Settings → New sessions → Default model and Default effort.

A chat keeps its own choice across Restart, Resume and New. Details: [Default model and effort](guide.md#default-model-and-effort).

## Stop, restart or resume an agent

1. **Stop**: tap Stop while the agent works. Esc and ^C also stop it.
2. **Restart**: Harness → Restart. The agent relaunches in its folder with the same YOLO setting and continues the same conversation, picking up new settings or updates.
3. **Resume an older conversation**: Harness → Resume, or `/resume`, then pick one from this folder's past conversations.
4. **Shrink a long conversation**: Harness → Compact, or `/compact`.

Harness sits left of Esc in the key row; on a PC it is in the message box. Details: [Terminal input](guide.md#terminal-input), [Restart](guide.md#restart).

## Use a terminal

1. Tap `+`, then Terminal.
2. Type in the message box and tap Send, or use the key row: Esc, Tab, ⇧Tab, ^C, arrows, ⏎.
3. Swipe the terminal to scroll back; scroll to the bottom or type to return to live output.

Settings → Appearance → Quick keys picks which keys show. Details: [Terminal input](guide.md#terminal-input).

## Add a script key

A script key runs a shell command or starts an agent with a prompt in one tap.

1. Turn on Settings → Advanced → Scripts if the Scripts bar is not showing.
2. Tap `+` on the Scripts bar at the top of the tab column.
3. Pick **Or have an agent build it** and describe the key, and an agent writes and tests the script. Or choose a type yourself (Shell, or an agent prompt), fill in the command or prompt, and Save.
4. Tap the key to run it. Its time shows while it runs; the output opens in a window.

Hold a key and let go to edit it; hold and drag to move it. On a PC, right-click for Run, Edit and Delete. An agent key with no saved prompt asks for one each time. Details: [Scripts](guide.md#scripts), [Button types](guide.md#button-types), [Key builder](guide.md#key-builder).

## Close a tab, and get it back

1. Tap the open tab once: it turns red.
2. Tap it again within 3 seconds to close that session. Tap anything else to cancel.
3. On a PC you can also right-click the tab → Close.

A closed agent chat goes to **Trash** at the bottom of the tab column for 30 days. Restore it there to continue the conversation in a new tab. Terminals are not kept. Details: [Closing a session](guide.md#closing-a-session).

## Organize tabs

1. Drag a tab to reorder it. On a touch screen, hold first.
2. Drop a tab on the middle of another to make a folder; drag it out to remove it.
3. On a PC, right-click a tab to rename it.
4. Tap a host's name to fold its tabs away.

Details: [Tab column](guide.md#tab-column), [Tab folders](guide.md#tab-folders).

## Keep things on the shelf and read them in Review

The shelf is the column right of a chat. Its contents sync to every host.

1. Tap **Save turn** on a reply to keep it with its tool calls, or **Save messages** for the agent's words only.
2. Tap **+ File** to add a PDF (Portable Document Format), Word or text file.
3. Drag a shelf item onto the chat to attach it to your next message.
4. On a PC, open the **Review** view tab. Drag a document from the shelf into the reader on the right; the chat stays on the left.

In Review, text you select goes with your next message as a quote, and Markdown documents open in an editor. Deleted items stay in the shelf's trash for 30 days. Details: [Shelf](guide.md#shelf), [Documents and Review](guide.md#documents-and-review).

## Link a second PC

Linked PCs form one Deck: one tab list, with shared scripts, shelf, settings and paired devices. No PC is the main one, and no Tailscale is needed.

1. On the first PC: Settings → More → PCs → **Link a PC**. A link code appears, valid for 5 minutes.
2. On the second PC, which must not be linked yet: Settings → More → PCs → **Link to a PC**, and enter the code.
3. Approve on the first PC.

**Remove** takes another PC out; **Leave** takes this one out. A PC can also be client only, starting its sessions on a linked PC (Settings → Advanced → Host sessions on this PC, off). Details: [Linked PCs](guide.md#linked-pcs), [Client only](guide.md#client-only).

## Move a chat to another host

1. In the agent chat, open Harness → Move host. On a PC, you can also right-click the tab → Move host.
2. Pick the host.

The chat continues there on the same conversation, and the tab here closes. Supported for Claude, Codex, Pi and OpenCode.

If a host goes offline, its tabs stay in the column, marked offline. Tap one to read this host's copy, then **Transfer here** to continue the chat on this host. Details: [Move host](guide.md#move-host), [Offline hosts' chats](guide.md#offline-hosts-chats).

## Get phone alerts

Deck can notify your phone, through [ntfy](https://ntfy.sh), when an agent finishes or stops on a question. The notification carries the tab name, state and host, never terminal text.

1. Set it up on the host as described in [optional-features.md](optional-features.md).
2. Subscribe to the same topic in the ntfy app.

On the public ntfy.sh server the topic name is the only protection, so make it long and random. Details: [Phone alerts](guide.md#phone-alerts).

## Remove a lost phone

1. On any paired device: Settings → More → Devices.
2. Remove the lost phone.

Removing a device on one host removes it from every host. Details: [Good to know](security.md#good-to-know).
