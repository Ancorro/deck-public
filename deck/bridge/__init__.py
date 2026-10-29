"""Chat sessions: runs an agent in its machine-readable mode inside a tmux pane and turns its output into Deck chat
events, so the phone shows a chat instead of a terminal. Stdlib only, plus pywinpty for Claude on Windows (the
server's Python has it); started by agents.build_argv as

    python chat_bridge.py <agent> <chat dir> <session> <resume 0|1> <first message> -- <agent args...>

Files in <chat dir> (the server tails and appends them, see server.terminal_chat):
    <session>.jsonl     events for the page, rewritten at each start: {"t": "clear"} then the history, then live ones
    <session>.in.jsonl  commands from the page: {"t": "say", "text"}, {"t": "abort"}, {"t": "answer", "id", "value"},
                        {"t": "sendnow"} (the waiting messages, now), {"t": "unhold"} (take them all back to edit)
    <session>.state     {"state", "preview", "name"} for the Agents feed and the tab title, and "resume": the agent's
                        own id for the conversation, read back at a restart (Bridge.prev_resume)
    <session>.hooks.jsonl  Claude's hook input (`chat_bridge.py hook <chat dir> <session>` appends it)
    <session>.media/    files the agent's replies carry (OpenClaw's images and attachments), for the page through
                        server.chat_media

Events: clear | user {text} | delta {m, i, kind, d} (streamed text / thinking) | msg {m, blocks} (an assistant message
so far; replaces its deltas) | tool_end {id, out, err} | state {s} | note {text, err} | name {text} | status {parts}
(model, effort, mode: the bar above the chat) | ask {id, title, options, multi?, other?, skip?} (answer value: a
label, a list of labels (multi), {other: text}, or null to skip) | answered {id, how?} (how: its end, e.g. Expired) |
queued {id, text, keys} (typed while it works: sent at once, waiting till its "user" event; keys: Send now and Edit
work) | unqueued {id} (taken back).
Blocks: {kind: text|think, text} or {kind: tool, id, name, args} or {kind: file, name, mime, size, file?, err?} (an
image or attachment: file is its name in <session>.media once fetched, err why it couldn't be).
Each agent has an adapter mapping its own protocol onto these: Pi (pi --mode rpc), Claude (run interactively in a
pseudo-terminal: its transcript and hooks), Codex (codex app-server), OpenCode (opencode serve),
agy (print mode, stream-json both ways),
OpenClaw (its Gateway, through deploy/agents/openclaw-bridge)."""
