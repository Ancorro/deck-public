"""What a pane's text says about its session: working, waiting for a choice, idle, done. Pure functions over the
captured lines, so they test without tmux."""
import re

# ---- status -----------------------------------------------------------------

IDLE_AFTER = 6  # seconds without output before a live pane counts as waiting
OPTION_RE = re.compile(r"^[\s│|]*(?:[❯›>]\s*)?(\d)[.)]\s+(.{1,60}?)\s*[│|]?\s*$")
YESNO_RE = re.compile(r"\((?:y/n|Y/n|y/N)\)|\[(?:y/n|Y/n|y/N)\]")
NOISE_RE = re.compile(r"^[\s─━│┃╹▀⬝╭╮╰╯┌┐└┘├┤═>❯›·•*✻✳✶✢\-_=|]*$")
EMPTY_PROMPT_RE = re.compile(r"^[\s│|]*[>❯›]\s*[│|]?\s*$")
MENU_HINT_RE = re.compile(r"Enter to (confirm|select)", re.I)  # arrow-key menus (no numbers)
CURSOR_RE = re.compile(r"^(\s*❯\s+)(\S.{0,60}?)\s*$")
SPINNER_RE = re.compile(r"^[*✻✳✶✢✽·]\s+\S[^()]*…\s*\(")  # Claude "* Computing… (11s · …)": chrome below it
# notices and status lines (Claude's and Codex's) under the output: not part of it
NOTICE_RE = re.compile(r"(Tip:|Update installed|Restart to update| · ctx \d+%|Context \d+% used)")
# Codex "• Working (5s • esc to interrupt)", OpenCode "esc interrupt", Pi "── ⠹ Working ──", OpenClaw "⠇ moseying… • 1s"
BUSY_RE = re.compile(r"esc (to )?interrupt|[⠁-⣿] Working|[⠁-⣿] \S+… • \d", re.I)
AGENTS = ("claude", "codex", "opencode", "pi", "agy", "openclaw")
CHROME_RE = re.compile(r"(^Pane is dead|\? for shortcuts|esc (to )?interrupt|ctrl\+p commands|[┃▣]\s+(Build|Plan)( auto)? · |"
                       r"ctrl\+[a-z] to|shift\+tab|bypass permissions on|⏵⏵|tokens? used|context left|"
                       # Pi: spinner, footer folder ("~\work", "~/deck (main)"), context use ("1.6%/1.0M (auto)")
                       r"[⠁-⣿] Working|"
                       # OpenClaw: header, session line, spinner, status ("connected | idle"), footer ("… | deliver:off | …")
                       r"^\s*openclaw tui - |^\s*session agent:|[⠁-⣿] \S+… • \d|"
                       r"^\s*(gateway )?(connected|disconnected|local ready) \| |\| deliver:o|"
                       r"[\d.]+%/[\d.]+[kM]\b|^\s*(~|[A-Za-z]:)?[\\/]\S*( \([^)]*\))?\s*$)", re.I)


def menu_question(above: list[str], first: bool = False) -> str:
    """The prompt above a menu: the nearest line asking something (Claude's trust dialog puts a
    "Security guide" link between the question and the options), else the nearest text line,
    or with first=True the top of the text block (a warning's heading)."""
    text = [x.strip(" │|") for x in above if not NOISE_RE.match(x) and not CHROME_RE.search(x)][-8:]
    fallback = (text[0] if first else text[-1]) if text else ""
    return next((x for x in reversed(text) if x.endswith("?")), fallback)


def arrow_menu(tail: list[str]) -> tuple[list[dict], str]:
    """Unnumbered menu picked with arrows + Enter (e.g. Claude's bypass-permissions warning).
    Items are the lines just above the "Enter to confirm" hint aligned with the ❯ cursor line.
    Each option carries `nav`: arrow presses from the cursor (negative = up)."""
    hint = next((i for i in range(len(tail) - 1, max(-1, len(tail) - 4), -1) if MENU_HINT_RE.search(tail[i])), None)
    if hint is None:
        return [], ""
    cur = next((i for i in range(hint - 1, max(-1, hint - 10), -1) if CURSOR_RE.match(tail[i])), None)
    if cur is None:
        return [], ""
    col = len(CURSOR_RE.match(tail[cur]).group(1))
    aligned = lambda l: i_col(l) == col and len(l.strip()) <= 60
    start = cur
    while start - 1 >= 0 and aligned(tail[start - 1]):
        start -= 1
    end = cur
    while end + 1 < hint and aligned(tail[end + 1]):
        end += 1
    items = [CURSOR_RE.match(tail[i]).group(2) if i == cur else tail[i].strip() for i in range(start, end + 1)]
    if len(items) < 2:
        return [], ""
    return ([{"key": "", "label": t, "nav": i - (cur - start)} for i, t in enumerate(items)],
            menu_question(tail[:start], first=True))


def i_col(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def snippet_lines(lines: list[str], n: int = 8) -> list[str]:
    """Last n lines of real output: stop at an agent's spinner and skip status chrome, tips and notices."""
    body = [l.rstrip() for l in lines if l.strip()][-60:]
    spin = [i for i, l in enumerate(body) if SPINNER_RE.match(l)]
    if spin:
        body = body[:spin[-1]]
    out = [l for l in body if not NOISE_RE.match(l) and not CHROME_RE.search(l) and not NOTICE_RE.search(l)]
    return out[-n:]


def analyze(lines: list[str], kind: str, idle_secs: float, dead: bool, exit_code: int | None) -> dict:
    """Classify a pane from its visible text. Pure, for testing.
    state: done | failed | needs_you (choice prompt) | your_turn (agent idle) | idle (shell idle) | working"""
    tail = [l.rstrip() for l in lines if l.strip()][-20:]
    text_lines = [l for l in tail if not NOISE_RE.match(l) and not CHROME_RE.search(l)]
    preview = text_lines[-1].strip(" │|") if text_lines else ""
    options = []
    question = ""
    if not dead:
        idx = [i for i, l in enumerate(tail) if OPTION_RE.match(l)]
        block = []  # last run of option lines, allowing one wrapped line between them
        for i in idx:
            block = block + [i] if block and i - block[-1] <= 2 else [i]
        after = tail[block[-1] + 1:] if block else []
        # A menu sits at the bottom (only short hints below) and is not followed by an empty input prompt,
        # which would mean it was a numbered list in the agent's answer.
        if block and len(after) <= 4 and not any(EMPTY_PROMPT_RE.match(l) for l in after):
            for i in block:
                m = OPTION_RE.match(tail[i])
                options.append({"key": m.group(1), "label": m.group(2).strip()})
            question = menu_question(tail[:block[0]])
            if [o["key"] for o in options] != [str(n) for n in range(1, len(options) + 1)]:
                options, question = [], ""  # not a 1..n menu
        if not options:
            options, question = arrow_menu(tail)
        if not options and tail and YESNO_RE.search(tail[-1]):
            question = tail[-1].strip()
            options = [{"key": "y", "label": "Yes", "enter": True}, {"key": "n", "label": "No", "enter": True}]
    # Agent TUIs redraw while idle (status line, tips), so pane activity says nothing for them:
    # they are working only while their spinner shows. Shells fall back to recent output.
    if kind in AGENTS:
        busy = not options and any(SPINNER_RE.match(l) or BUSY_RE.search(l) for l in tail)
    else:
        busy = idle_secs < IDLE_AFTER
    if dead:
        state = "done" if exit_code == 0 else "failed"
    elif busy:
        state = "working"
    elif options:
        state = "needs_you"
    else:
        state = "your_turn" if kind in AGENTS else "idle"
    if options and state == "needs_you":
        preview = question or preview
    snippet = "\n".join(l.strip(" │|")[:200] for l in snippet_lines(lines))
    return {"state": state, "preview": preview[:160], "snippet": snippet, "options": options if state == "needs_you" else []}


