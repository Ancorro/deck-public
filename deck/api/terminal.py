"""The terminal WebSocket (/ws/<session>): a tmux client in a pseudo-terminal piped to the page (Windows: a ConPTY
client, replaced on resize), or text mode (no client: the pane's text polled and redrawn). A chat session's socket is
chat.terminal_chat."""
import asyncio
import json
import os
import re
import signal
import struct
import threading
import time

from aiohttp import WSMsgType, web

from .. import config, sessions
from . import chat

if config.WINDOWS:
    from winpty import PtyProcess
else:
    import fcntl
    import pty
    import termios

async def terminal(request):
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound()
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)
    if request.query.get("mode") == "chat":
        return await chat.terminal_chat(ws, request, name, request.query.get("log", ""), request.query.get("from", ""))
    if request.query.get("mode") == "text":
        return await terminal_text(ws, name)
    if config.WINDOWS:
        return await terminal_windows(ws, name)

    pid, fd = pty.fork()
    if pid == 0:  # child: become a tmux client on this session
        os.environ["TERM"] = "xterm-256color"
        os.execvp("tmux", ["tmux", "attach-session", "-t", f"={name}"])

    loop = asyncio.get_running_loop()

    def on_output():
        try:
            data = os.read(fd, 65536)
        except OSError:
            data = b""
        if data:
            asyncio.ensure_future(ws.send_bytes(data))
        else:  # client exited (session killed)
            loop.remove_reader(fd)
            asyncio.ensure_future(ws.close())

    loop.add_reader(fd, on_output)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") == "resize":
                set_size(fd, int(m["cols"]), int(m["rows"]))
            else:
                await terminal_input(name, m, lambda d: os.write(fd, d.encode()))
    finally:
        loop.remove_reader(fd)
        try:
            os.kill(pid, signal.SIGHUP)  # detaches the tmux client; the session keeps running
        except ProcessLookupError:
            pass
        os.close(fd)
        await loop.run_in_executor(None, os.waitpid, pid, 0)
    return ws


async def terminal_input(name: str, m: dict, write) -> None:
    """A terminal socket message other than resize: keys (write them), a message box line (say), scroll, or Back.
    say goes straight into the session, not through this socket's client: Send closes the phone keyboard, the resize
    replaces the Windows client, and keys still queued in the old one were lost or overtaken by the Enter."""
    if m.get("t") in ("in", "say"):
        target = f"={name}:"
        if sessions.tmux("display-message", "-p", "-t", target, "#{pane_in_mode}").stdout.strip() == "1":
            sessions.tmux("send-keys", "-t", target, "-X", "cancel")
        if m["t"] == "in":
            write(m["d"])
        else:
            await sessions.type_line(name, str(m.get("d", "")))
    elif m.get("t") == "scroll":
        scroll_terminal(name, int(m.get("lines", 0)))
    elif m.get("t") == "prevprompt":
        await asyncio.get_running_loop().run_in_executor(None, prev_prompt, name)  # short sleeps; keep the loop free


def set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


PROMPT_LINE = re.compile(r"^\s*❯\s+\S")  # a prompt the user sent, as Claude Code / Codex show it in the transcript


def prev_prompt(name: str) -> None:
    """Desktop Back: scroll up to the user's previous prompt.
    Full-screen agents (no tmux history): wheel up until a prompt not on screen before reaches the top third.
    Shells: tmux copy-mode search back for a shell prompt."""
    target = f"={name}:"
    flags = sessions.tmux("display-message", "-p", "-t", target,
                          "#{alternate_on} #{mouse_any_flag} #{pane_height}").stdout.split()
    if flags[:2] == ["1", "1"]:
        height = int(flags[2])
        screen = lambda: sessions.tmux("capture-pane", "-p", "-t", target).stdout.splitlines()
        seen = {l.strip() for l in screen() if PROMPT_LINE.match(l)}
        for _ in range(60):  # at most ~300 lines up
            sessions.tmux("send-keys", "-t", target, "-l", "\x1b[<64;1;1M" * 5)
            time.sleep(0.05)
            for line in screen()[: max(3, height // 3)]:
                if PROMPT_LINE.match(line) and line.strip() not in seen:
                    return
        return
    if sessions.tmux("display-message", "-p", "-t", target, "#{pane_in_mode}").stdout.strip() != "1":
        sessions.tmux("copy-mode", "-t", target)
    # Step off the current line first, so the empty prompt being typed at (or the last match) isn't found again;
    # no wrap-around, so Back stops at the oldest prompt instead of jumping to the bottom.
    sessions.tmux("set-option", "-w", "-t", target, "wrap-search", "off")
    sessions.tmux("send-keys", "-t", target, "-X", "cursor-up")
    sessions.tmux("send-keys", "-t", target, "-X", "search-backward", r"[$#❯>] ")


PAGE_SCROLL: dict[str, int] = {}  # swipe lines not yet worth a PageUp/PageDown, per session


def scroll_terminal(name: str, lines: int) -> None:
    """Scroll tmux history (positive lines move toward newer output)."""
    target = f"={name}:"
    lines = max(-100, min(100, lines))
    if not lines:
        return
    # Full-screen apps with mouse reporting (Claude Code) keep no tmux history: send them wheel events instead.
    flags = sessions.tmux("display-message", "-p", "-t", target,
                      "#{alternate_on} #{mouse_any_flag} #{pane_in_mode} #{pane_height}").stdout.split()
    if flags[:3] == ["1", "1", "0"]:
        wheel = f"\x1b[<{64 if lines < 0 else 65};1;1M"
        sessions.tmux("send-keys", "-t", target, "-l", wheel * abs(lines))
        return
    if flags[:3] == ["1", "0", "0"]:
        # Full screen without mouse reporting (Pi): no history and no wheel; its viewport pages with PageUp/PageDown.
        # Swipes arrive a few lines at a time, so they add up to a page (half the pane) before a key goes.
        page = max(4, int(flags[3] if len(flags) > 3 and flags[3].isdigit() else 24) // 2)
        owed = PAGE_SCROLL.get(name, 0) + lines
        keys, owed = int(owed / page), owed - int(owed / page) * page
        PAGE_SCROLL[name] = owed
        if keys:
            sessions.tmux("send-keys", "-t", target, *(["PPage" if keys < 0 else "NPage"] * abs(keys)))
        return
    if lines < 0:
        sessions.tmux("copy-mode", "-t", target)
    elif sessions.tmux("display-message", "-p", "-t", target, "#{pane_in_mode}").stdout.strip() != "1":
        return
    sessions.tmux("send-keys", "-t", target, "-N", str(abs(lines)), "-X", "scroll-up" if lines < 0 else "scroll-down")
    if lines > 0 and sessions.tmux("display-message", "-p", "-t", target, "#{scroll_position}").stdout.strip() == "0":
        sessions.tmux("send-keys", "-t", target, "-X", "cancel")


# ---- Windows: a ConPTY client per size ----

# Device attributes under ConPTY: a new ConPTY asks its terminal ESC[c and ignores input until answered, and answers
# coming back from xterm.js reach the pane as typed text ("1;2c0;276;0c", once per attach; a resize re-attaches).
# So the bridge answers ConPTY's own query itself and drops xterm.js's answers.
DA_REPLY = re.compile(r"\x1b\[[?>][0-9;]*c")


async def terminal_windows(ws, name: str):
    """Windows: the tmux client runs in a ConPTY (pywinpty). Resizing a ConPTY under MSYS2's tmux can hang the tmux
    server, so a resize starts a new client at the new size instead (the old one detaches; the screen redraws)."""
    loop = asyncio.get_running_loop()
    client = None

    def attach(rows: int, cols: int):
        """Start a client at this size and make it the current one; returns the one it replaces."""
        nonlocal client
        old = client
        p = client = PtyProcess.spawn([config.TMUX, "attach-session", "-t", f"={name}"], dimensions=(rows, cols),
                                      env=dict(os.environ, TERM="xterm-256color"))

        def pump():  # ConPTY reads block: a thread per client. Only the current one reaches the phone (a replaced
            asked = True  # one's goodbye, "[detached]" and a terminal reset, would land on the new screen)
            while True:
                try:
                    data = p.read(65536)
                    if asked and "\x1b[c" in data:  # ConPTY's start-up query: answer as a VT100 (what xterm.js says)
                        asked = False
                        data = data.replace("\x1b[c", "", 1)
                        p.write("\x1b[?1;2c")
                except Exception:  # EOF, or closed by detach
                    break
                if data and client is p:
                    loop.call_soon_threadsafe(lambda d=data: asyncio.ensure_future(ws.send_bytes(d.encode())))
            if client is p:  # client exited on its own (session killed), not replaced by a resize
                loop.call_soon_threadsafe(lambda: asyncio.ensure_future(ws.close()))
        threading.Thread(target=pump, daemon=True).start()
        return old

    def write(data: str) -> None:
        try:
            client.write(data)
        except EOFError:  # this client just exited (session ended, or replaced by a resize); its pump closes the socket
            pass

    def detach(p) -> None:
        try:
            p.terminate(force=True)  # the session keeps running
        except Exception:
            pass

    size = None

    def first_attach():  # the page sends its size on open; attach at that size (or a default if none comes)
        nonlocal size
        if client is None and not ws.closed:
            size = (40, 120)
            attach(*size)
    loop.call_later(2, first_attach)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") == "resize":
                new = (max(2, int(m["rows"])), max(2, int(m["cols"])))
                if new != size:
                    size = new
                    old = attach(*size)
                    if old:  # not at once: it may still hold keys it took (a Send right before the keyboard closes)
                        loop.call_later(1.5, lambda o=old: loop.run_in_executor(None, detach, o))
            elif client or m.get("t") != "in":  # only raw keys need the client
                if m.get("t") == "in":
                    m["d"] = DA_REPLY.sub("", m.get("d", ""))
                    if not m["d"]:
                        continue
                await terminal_input(name, m, write)
    finally:
        old, client = client, None
        if old:
            await loop.run_in_executor(None, detach, old)
    return ws


# ---- text mode (experimental): no tmux client; the page shows the pane's text, polled ----


# ---- text mode (experimental): no tmux client; the page shows the pane's text, polled ----

PANE_META = ("#{cursor_x},#{cursor_y},#{cursor_flag},#{pane_in_mode},#{scroll_position},#{history_size},"
             "#{pane_width},#{pane_height},#{alternate_on},#{mouse_any_flag}")


def capture(name: str, offset: int = 0, height: int = 0, pre: list[str] = ()) -> tuple[list[str], list[int]] | None:
    """The pane's rows (with colour escapes) and PANE_META as numbers, in one tmux call; None if the session is gone.
    offset: rows scrolled back in copy mode (capture-pane shows the live screen, not copy mode's view).
    pre: tmux commands (each ending in ";") run first in the same call, e.g. a scroll (a tmux call costs ~50 ms on
    Windows)."""
    t = f"={name}:"
    span = ["-S", str(-offset), "-E", str(height - 1 - offset)] if offset else []
    r = sessions.tmux(*pre, "capture-pane", "-p", "-e", "-N", *span, "-t", t, ";", "display-message", "-p", "-t", t, PANE_META)
    if r.returncode:
        return None
    rows = r.stdout.split("\n")[:-1]
    return rows[:-1], [int(v or 0) for v in rows[-1].split(",")]


def text_frame(prev: list[str] | None, rows: list[str], meta: list[int]) -> str:
    """Escapes that turn the page's terminal from prev (its rows; None = start over) into rows: only changed rows are
    rewritten, each erased first and drawn in place (no wrap), so nothing flickers. Copy mode gets tmux's [pos/size]."""
    cx, cy, cursor, in_mode, pos, size, width = meta[:7]
    if in_mode and rows:
        tag = f"[{pos}/{size}]"
        rows = [*rows]
        rows[0] += f"\x1b[0m\x1b[1;{max(1, width - len(tag) + 1)}H\x1b[30;43m{tag}"
    out = [] if prev is not None else ["\x1b[?1049h\x1b[?7l\x1b[0m\x1b[H\x1b[2J"]
    old = prev or []
    out += [f"\x1b[{i + 1};1H\x1b[0m\x1b[2K{row}" for i, row in enumerate(rows) if i >= len(old) or old[i] != row]
    out += [f"\x1b[{i + 1};1H\x1b[0m\x1b[2K" for i in range(len(rows), len(old))]
    show = cursor and not in_mode
    out.append(f"\x1b[0m\x1b[{cy + 1};{cx + 1}H\x1b[?25{'h' if show else 'l'}")
    return "".join(out)


def scroll_commands(name: str, lines: int, meta: list[int]) -> tuple[list[str], int] | None:
    """Text mode: the tmux commands that scroll the pane's history by lines (positive = toward newer), to run in the
    capture's call, and the copy-mode position they leave. None for full-screen apps (scroll_terminal handles them)."""
    in_mode, pos, size, alt = meta[3], meta[4], meta[5], meta[8]
    if alt and not in_mode:
        return None
    t = f"={name}:"
    pos = pos if in_mode else 0
    if lines < 0:
        n = min(-lines, size - pos)
        return (["copy-mode", "-t", t, ";", "send-keys", "-t", t, "-N", str(n), "-X", "scroll-up", ";"], pos + n) if n \
            else ([], pos)
    if not in_mode:
        return [], 0
    if lines >= pos:  # back at the live screen
        return ["send-keys", "-t", t, "-X", "cancel", ";"], 0
    return ["send-keys", "-t", t, "-N", str(lines), "-X", "scroll-down", ";"], pos - lines


def resize_keeping_place(name: str, cols: int, rows: int, meta: list[int] | None) -> None:
    """resize-window. tmux's copy mode jumps to the newest line on a resize, so scroll back to where it was."""
    t = f"={name}:"
    now = sessions.tmux("resize-window", "-t", t, "-x", str(cols), "-y", str(rows), ";",
                    "display-message", "-p", "-t", t, "#{pane_in_mode} #{scroll_position}").stdout.split()
    if meta and meta[3] and now[:1] == ["1"] and (gap := meta[4] - int(now[1] or 0)) > 0:
        sessions.tmux("send-keys", "-t", t, "-N", str(gap), "-X", "scroll-up")


# Arrow/Home/End keys go by name: tmux then sends them in the form the program asked for (normal or application cursor
# keys), which the page can't know without the program's own output.
NAMED_KEYS = {f"\x1b{p}{c}": k for p in "[O" for c, k in zip("ABCDHF", ("Up", "Down", "Right", "Left", "Home", "End"))}


KEY_SPLIT = re.compile(r"(\x1b[\[O][A-DHF])")


def type_keys(name: str, data: str) -> None:
    """Text mode: keys from the page, typed with send-keys in one tmux call (there is no client to write them to)."""
    t = f"={name}:"
    esc = lambda s: s[:-1] + "\\;" if s.endswith(";") else s  # tmux takes a trailing ";" as a command separator
    if len(data) > 1 and re.search(r"[\r\n]", data) and "\x1b" not in data:
        # A paste (typing never sends a line break with more): tmux brackets it if the program asked for that, so an
        # agent takes it as one paste instead of submitting each line.
        for i in range(0, len(data), 8000):  # Windows command lines stop at 32K characters
            sessions.tmux("set-buffer", "-b", "deck-paste", *(["-a"] if i else []), "--", esc(data[i:i + 8000]))
        sessions.tmux("copy-mode", "-q", "-t", t, ";", "paste-buffer", "-p", "-d", "-b", "deck-paste", "-t", t)
        return
    args = ["copy-mode", "-q", "-t", t]  # leave copy mode first (no-op outside it)
    for part in filter(None, KEY_SPLIT.split(data)):
        args.append(";")
        if part in NAMED_KEYS:
            args += ["send-keys", "-t", t, NAMED_KEYS[part]]
        else:
            args += ["send-keys", "-t", t, "-l", "--", esc(part)]
    sessions.tmux(*args)


async def terminal_text(ws, name: str):
    """Text mode: polls the pane's text (capture-pane) and redraws changed rows; keys go in with send-keys and the page's
    size with resize-window. No client is attached, so nothing breaks on resize or reconnect (Windows ConPTY). Costs
    ~0.1 s of latency (0.4 s when idle) and the program's own terminal modes (mouse, cursor shape)."""
    loop = asyncio.get_running_loop()
    kick = asyncio.Event()
    redraw = sized = False
    want, have = None, None  # this page's size (cols, rows); the pane's, as last captured
    meta, owed = None, 0  # PANE_META as last captured; swipe lines not yet scrolled

    async def poll():
        nonlocal redraw, have, meta, owed
        shown, offset, height, changed = None, 0, 0, 0.0
        while not ws.closed:
            # Swipes arrive every frame; those that came in during the last capture go as one scroll, in the same
            # tmux call as the next capture (no queue of calls falling behind the finger).
            pre = []
            if owed and meta:
                lines, owed = max(-500, min(500, owed)), 0
                step = scroll_commands(name, lines, meta)
                if step is None:
                    await loop.run_in_executor(None, scroll_terminal, name, lines)
                else:
                    pre, offset = step
            snap = await loop.run_in_executor(None, capture, name, offset, height, pre)
            if snap is None:  # session ended
                await ws.close()
                return
            rows, meta = snap
            have = (meta[6], meta[7])
            pos, height = (meta[4] if meta[3] else 0), meta[7]
            if pos != offset:  # copy mode moved: capture its view right away
                offset = pos
                continue
            if redraw:  # the page reset its screen (resize)
                shown, redraw = None, False
            if shown is None or shown != (rows, meta):
                await ws.send_bytes(text_frame(shown and shown[0], rows, meta).encode())
                shown, changed = (rows, meta), loop.time()
            try:
                await asyncio.wait_for(kick.wait(), 0.1 if loop.time() - changed < 3 else 0.4)
            except TimeoutError:
                pass
            kick.clear()

    poller = asyncio.ensure_future(poll())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") != "resize" and want and have and have != want:
                # Another screen showing this session resized it: the one being used gets its size back.
                await loop.run_in_executor(None, resize_keeping_place, name, *want, meta)
                have, redraw = want, True
            if m.get("t") == "resize":
                cols, rows = max(2, int(m["cols"])), max(2, int(m["rows"]))
                want = (cols, rows)
                await loop.run_in_executor(None, resize_keeping_place, name, cols, rows, meta)
                sized = redraw = True
            elif m.get("t") == "scroll":
                owed += int(m.get("lines", 0))
            elif m.get("t") == "in":
                await loop.run_in_executor(None, type_keys, name, m.get("d", ""))
            else:
                await terminal_input(name, m, lambda d: None)
            kick.set()
    finally:
        poller.cancel()
        if sized:  # resize-window pinned the size (window-size manual): live clients size it again
            await loop.run_in_executor(None, sessions.tmux, "set-option", "-w", "-u", "-t", f"={name}:", "window-size")
    return ws


def routes() -> list:
    return [
        web.get("/ws/{name}", terminal),
    ]
