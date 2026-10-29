"""The Settings sheet: the choices, defaults and validation of every setting, the recent folders, and the hosts of
this Deck (its inventory and linked PCs)."""
import json
import os
import re
import tomllib

from . import config
from .config import settings_path  # noqa: F401  (re-exported: the settings file's path, as callers know it)
from .projects import project_of

# ---- the Settings sheet ----

# Settings → Default effort: each agent's own levels (agents.EFFORT_FLAG sets one; none: the agent's own default).
# OpenCode has none: its levels (variants) differ per model; /effort in a chat picks one.
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"), "codex": ("minimal", "low", "medium", "high", "xhigh"),
           "pi": ("off", "minimal", "low", "medium", "high", "xhigh", "max"), "agy": ("low", "medium", "high")}
# Settings → Default model: Claude's aliases. Synced between hosts like the rest of Settings, and never changed by a
# chat's /model (that pick is the session's own: the bridge undoes Claude's "saved as default").
MODELS = {"claude": ("fable", "opus", "sonnet", "haiku")}
# OpenCode sampling per agent (Settings → OpenCode sampling), applied by an OpenCode plugin. Ranges: OpenRouter's.
SAMPLING = {"temperature": (0.0, 2.0), "top_p": (0.0, 1.0), "top_k": (0, 1000)}
SAMPLING_AGENTS = ("plan", "build")

# Settings sheet choices and their defaults (a missing key means the default).
CHOICES = {"agent": ("claude", "codex", "opencode", "pi", "agy", "openclaw"),
           "start": ("term", "deck", "agents"), "theme": ("system", "light", "dark", "paper"), "shelfnew": ("bottom", "top"),
           # the page's look (deck/static/looks/), shared like the theme
           "look": ("pad", "rack", "blueprint", "zine", "plain", "message")}
# termw: the terminal's kept width (02-prefs.js applyLayout); b*: the widths before Fullscreen, for Minimize
LAYOUT_WIDTHS = ("padw", "feedw", "padcw", "feedcw", "termw", "bpadcw", "bfeedcw", "btermw")
LAYOUT_FLAGS = ("nopad", "nofeed", "nobottom", "full", "msgshut")
QUICK_KEYS = ("harness", "esc", "tab", "stab", "ctrlc", "up", "down", "left", "right", "enter")
DEFAULTS = {"agent": "claude", "agentpick": True, "folder": "", "yolo": False, "hosting": True, "debug": False,
            "resize": True, "start": "term", "theme": "system", "look": "pad", "font": 13, "pcfont": 13, "crop": True,
            "composer": True, "textterm": config.WINDOWS, "pidurable": False, "chat": False, "shelfnew": "bottom",
            "shelfagents": False, "sessionagents": False, "describe": False, "describer": "claude", "chattools": True,
            "thinkopen": False, "notesopen": False, "chatjump": True, "readernote": True, "brain": ""}
# textterm: Windows hosts default to the text terminal; a live client there goes through ConPTY, which redraws the
# screen itself and garbles it.


def settings() -> dict:
    """All settings with defaults filled in."""
    return {**DEFAULTS, "alias": "", "led": "", "pad": [], "hidekeys": [], "hostnames": {}, **load_settings()}


def default_folder() -> str:
    return os.path.expanduser(load_settings().get("folder") or config.WORK)


def load_settings() -> dict:
    """This Deck's settings as saved (the Settings sheet; alias = the whole Deck's name, led its colour: these override the
    inventory's). {} before anything was saved."""
    try:
        return json.loads(config.settings_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def clean_layout(raw):
    """Desktop columns: widths in px, and which are collapsed."""
    if not isinstance(raw, dict):
        raise ValueError("layout must be an object")
    try:
        return {k: bool(v) if k in LAYOUT_FLAGS else max(0, min(2000, int(v)))
                for k, v in raw.items() if k in LAYOUT_FLAGS + LAYOUT_WIDTHS and v}
    except (TypeError, ValueError):
        raise ValueError("layout widths must be numbers") from None


def save_settings(raw: dict) -> dict:
    """Update only the fields given; an empty alias/led/pad clears that field."""
    s = load_settings()
    if "alias" in raw:
        s["alias"] = str(raw["alias"]).strip()[:24]
    if "hostnames" in raw:  # {host: {alias, led}}: any host's name and colour, set from any host; shared by the Deck (user)
        if not isinstance(raw["hostnames"], dict):
            raise ValueError("hostnames must be an object of host: {alias, led}")
        names = dict(s.get("hostnames") or {})
        for host, v in raw["hostnames"].items():
            v = v if isinstance(v, dict) else {}
            entry = {"alias": str(v.get("alias", "")).strip()[:24], "led": str(v.get("led", "")).strip().lower()}
            if entry["led"] and not re.fullmatch(r"#[0-9a-f]{6}", entry["led"]):
                raise ValueError("colour must look like #ff4f12")
            entry = {k: x for k, x in entry.items() if x}
            if entry:
                names[str(host)[:64]] = entry
            else:
                names.pop(str(host), None)
        s["hostnames"] = names
    if "led" in raw:
        s["led"] = str(raw["led"]).strip().lower()
        if s["led"] and not re.fullmatch(r"#[0-9a-f]{6}", s["led"]):
            raise ValueError("colour must look like #ff4f12")
    if "pad" in raw:  # key order on the Pad: built-in ids and saved key ids
        if not isinstance(raw["pad"], list):
            raise ValueError("pad must be a list of key ids")
        s["pad"] = [str(x)[:40] for x in raw["pad"]][:200]
    for k, allowed in CHOICES.items():
        if k in raw:
            if raw[k] not in allowed:
                raise ValueError(f"{k} must be one of {allowed}")
            s[k] = raw[k]
    if "folder" in raw:
        s["folder"] = str(raw["folder"]).strip()[:200]
        if s["folder"] and not os.path.isdir(os.path.expanduser(s["folder"])):
            raise ValueError(f"folder not found: {s['folder']}")
    if "brain" in raw:  # the Brain's data (09-composer.js Brain): a project, or a folder in one, kept as its project name
        v = " ".join(str(raw["brain"]).split())[:300]
        if v and os.path.isdir(os.path.expanduser(v)):
            v = project_of(os.path.expanduser(v))
            if not v:
                raise ValueError(f"{raw['brain']} is in no project (a git repo, or a folder under home)")
        elif v.startswith(("/", "~", ".")):
            raise ValueError(f"folder not found: {v}")
        s["brain"] = v
    if "yolo" in raw:
        s["yolo"] = bool(raw["yolo"])
    if "debug" in raw:  # chats show what Deck adds and hides (its instructions, stamps, reminders, hook output)
        s["debug"] = bool(raw["debug"])
    if "hosting" in raw:  # off: a client-only PC (no sessions of its own; new ones start on a linked PC)
        s["hosting"] = bool(raw["hosting"])
    if "order" in raw:  # the nodes, highest first: the first one up serves the site and writes its state (deck/site.py)
        if not isinstance(raw["order"], list) or not all(isinstance(x, str) for x in raw["order"]):
            raise ValueError("order must be a list of host names")
        s["order"] = [x for x in dict.fromkeys(raw["order"]) if x][:16]
    if "effort" in raw:  # {agent: level}: merged into the saved ones; "" clears that agent's (its own default)
        if not isinstance(raw["effort"], dict):
            raise ValueError("effort must be an object of agent: level")
        eff = dict(s.get("effort") or {})
        for kind, level in raw["effort"].items():
            if kind not in EFFORTS or level not in ("", *EFFORTS[kind]):
                raise ValueError(f"{kind} effort must be one of {EFFORTS.get(kind, ())}")
            if level:
                eff[kind] = level
            else:
                eff.pop(kind, None)
        s["effort"] = eff
    if "model" in raw:  # {agent: alias}: merged into the saved ones; "" clears that agent's (its own default)
        if not isinstance(raw["model"], dict):
            raise ValueError("model must be an object of agent: model")
        models = dict(s.get("model") or {})
        for kind, model in raw["model"].items():
            if kind not in MODELS or model not in ("", *MODELS[kind]):
                raise ValueError(f"{kind} model must be one of {MODELS.get(kind, ())}")
            if model:
                models[kind] = model
            else:
                models.pop(kind, None)
        s["model"] = models
    if "openrouter" in raw:  # shared provider routing for every Deck OpenCode request to OpenRouter
        routing = raw["openrouter"]
        if not isinstance(routing, dict):
            raise ValueError("openrouter must be an object of provider routing preferences")
        for key, value in routing.items():
            if key == "sort" and value in ("price", "latency", "throughput"):
                continue
            if key == "data_collection" and value in ("allow", "deny"):
                continue
            if key == "zdr" and isinstance(value, bool):
                continue
            if key in ("preferred_min_throughput", "preferred_max_latency"):
                values = value if isinstance(value, dict) else {"p50": value}
                if values and all(k in ("p50", "p75", "p90", "p99") and isinstance(v, (int, float))
                                  and not isinstance(v, bool) and 0 < v < float("inf") for k, v in values.items()):
                    continue
            raise ValueError(f"invalid OpenRouter routing preference: {key}")
        s["openrouter"] = routing
    if "sampling" in raw:  # {agent: {param: number}} for OpenCode's agents, merged; "" or None clears that param
        if not isinstance(raw["sampling"], dict) or not all(isinstance(v, dict) for v in raw["sampling"].values()):
            raise ValueError("sampling must be an object of agent: {param: number}")
        samp = {a: dict(v) for a, v in (s.get("sampling") or {}).items()}
        for agent, params in raw["sampling"].items():
            if agent not in SAMPLING_AGENTS:
                raise ValueError(f"sampling agent must be one of {SAMPLING_AGENTS}")
            for k, v in params.items():
                if k not in SAMPLING:
                    raise ValueError(f"sampling setting must be one of {tuple(SAMPLING)}")
                if v in ("", None):
                    samp.get(agent, {}).pop(k, None)
                    continue
                lo, hi = SAMPLING[k]
                try:
                    v = int(v) if k == "top_k" else float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"{k} must be a number") from None
                if not lo <= v <= hi:
                    raise ValueError(f"{k} must be from {lo} to {hi}")
                samp.setdefault(agent, {})[k] = v
        s["sampling"] = {a: v for a, v in samp.items() if v}
    if "pidurable" in raw:  # Pi sessions run pi-durable (deploy/agents/pi-durable) where it's installed
        s["pidurable"] = bool(raw["pidurable"])
    if "chatjump" in raw:  # back in a chat left at its bottom: on, at the newest reply; off, where it was left
        s["chatjump"] = bool(raw["chatjump"])
    if "readernote" in raw:  # messages name the documents open in the reader beside the chat; off: never
        s["readernote"] = bool(raw["readernote"])
    if "chattools" in raw:  # chats show the agent's tool calls (commands, edits); off: only the conversation
        s["chattools"] = bool(raw["chattools"])
    if "thinkopen" in raw:  # chats show the agent's thinking expanded; off: folded under "Thinking"
        s["thinkopen"] = bool(raw["thinkopen"])
    if "notesopen" in raw:  # chats show edits to the machine notes repo (DECK_NOTES) opened; off: folded like the rest
        s["notesopen"] = bool(raw["notesopen"])
    if "shelfagents" in raw:  # agents may read the shelf (GET /api/shelf/agent; the shelf's own checkbox)
        s["shelfagents"] = bool(raw["shelfagents"])
    if "sessionagents" in raw:  # agents may list, read and message the other sessions (/api/agent/sessions)
        s["sessionagents"] = bool(raw["sessionagents"])
    if "describe" in raw:  # a small model keeps a description of each agent session (deck/describe.py)
        s["describe"] = bool(raw["describe"])
    if "describer" in raw:  # which: claude (Haiku) or codex (OpenAI's small model)
        if raw["describer"] not in ("claude", "codex"):
            raise ValueError("describer must be claude or codex")
        s["describer"] = raw["describer"]
    if "chat" in raw:  # agents in CHAT_AGENTS start as chat sessions (deck/chat_bridge.py)
        s["chat"] = bool(raw["chat"])
    if "resize" in raw:  # desktop: drag the terminal's edges to resize the side columns
        s["resize"] = bool(raw["resize"])
    if "crop" in raw:  # agent tabs: hide the agent's own footer, show its status as chips
        s["crop"] = bool(raw["crop"])
    if "composer" in raw:  # Terminal message box; off = type straight into the terminal
        s["composer"] = bool(raw["composer"])
    # Page state shared by every browser and the Deck app on this host, so each opens where the last one left off.
    if "tabfolders" in raw:  # folders in the tab list: [{id, title, open, tabs: session names}] (05-tabs.js renderChips)
        if not isinstance(raw["tabfolders"], list):
            raise ValueError("tabfolders must be a list")
        s["tabfolders"] = [{"id": str(f["id"])[:24], "title": " ".join(str(f.get("title") or "Folder").split())[:40] or "Folder",
                            "open": bool(f.get("open", True)), "tabs": [str(t)[:120] for t in f.get("tabs") or []][:200]}
                           for f in raw["tabfolders"][:50] if isinstance(f, dict) and f.get("id")]
    if "scriptshut" in raw:  # the tab column's scripts half folded away (04-pad.js applyScriptShut)
        s["scriptshut"] = bool(raw["scriptshut"])
    if "agentpick" in raw:  # + asks which agent (on), or starts Default agent `agent` straight away (off; user)
        s["agentpick"] = bool(raw["agentpick"])
    if "scripts" in raw:  # the Scripts bar shows when on, or when a script exists (04-pad.js applyScriptShut; user)
        s["scripts"] = bool(raw["scripts"])
    if "hostshut" in raw:  # hosts whose sessions the tab column hides under their header (05-tabs.js toggleHost)
        if not isinstance(raw["hostshut"], list):
            raise ValueError("hostshut must be a list of host names")
        s["hostshut"] = [str(x)[:80] for x in raw["hostshut"]][:50]
    if "tabs" in raw:  # Terminal tab order (session names)
        if not isinstance(raw["tabs"], list):
            raise ValueError("tabs must be a list of session names")
        s["tabs"] = [str(x)[:80] for x in raw["tabs"]][:200]
    for k in ("session", "pcsession"):  # the session the Terminal shows: phones, wide screens (PC app window, browsers)
        if k in raw:
            s[k] = str(raw[k])[:80]
    if "textterm" in raw:  # experimental text terminal (server.terminal_text)
        s["textterm"] = bool(raw["textterm"])
    if "layout" in raw:  # desktop columns: widths in px, and which are collapsed
        s["layout"] = clean_layout(raw["layout"])
    if "layouts" in raw:  # saved layouts, each a header button that restores it (02-prefs.js renderLayouts)
        if not isinstance(raw["layouts"], list):
            raise ValueError("layouts must be a list")
        s["layouts"] = [{"id": str(x["id"])[:24], "name": " ".join(str(x.get("name") or "Layout").split())[:40] or "Layout",
                         "layout": clean_layout(x.get("layout") or {})}
                        for x in raw["layouts"][:12] if isinstance(x, dict) and x.get("id")]
    if "hidekeys" in raw:  # Terminal quick keys the user turned off
        if not isinstance(raw["hidekeys"], list) or not set(raw["hidekeys"]) <= set(QUICK_KEYS):
            raise ValueError(f"hidekeys must be a list from {QUICK_KEYS}")
        s["hidekeys"] = [k for k in QUICK_KEYS if k in raw["hidekeys"]]
    for k in ("font", "pcfont"):  # terminal font size: phones, wide screens (PC app window, browsers)
        if k in raw:
            try:
                s[k] = max(10, min(36, int(raw[k])))
            except (TypeError, ValueError):
                raise ValueError("font must be a number") from None
    # Drop cleared and default values so the file only holds what was changed.
    # Keep an explicitly cleared routing object so settings sync also clears it on the other hosts.
    s = {k: v for k, v in s.items() if k == "openrouter" or
         (v not in ("", [], {}, None) and DEFAULTS.get(k, object()) != v)}
    config.settings_path().parent.mkdir(parents=True, exist_ok=True)
    config.settings_path().write_text(json.dumps(s, indent=2) + "\n")
    return s


# ---- this Deck's hosts ----

# Power-LED colour per host: inventory `led` wins; others take the next unused palette colour.
LED_PALETTE = ["#ff4f12", "#31d158", "#3d8bff", "#b26bff", "#ffc21a", "#22c7b8", "#ff5c8a"]


def hosts() -> dict:
    """Hosts that run Deck, from the machine notes inventory (inventory.toml entries with deck_url)."""
    try:
        with open(config.INVENTORY, "rb") as f:
            inv = tomllib.load(f).get("hosts", {})
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        inv = {}
    decks = {n: h for n, h in inv.items() if h.get("deck_url")}
    # Linked PCs (links.py) reach each other through local iroh forwarders; the inventory's address wins for a host in
    # both. A member whose forwarder isn't up yet is left out (callers use its url).
    from . import links
    for m in links.members():
        if m["name"] in decks or not (m["name"] == config.HOST_NAME or m in links.others()):
            continue
        url = "" if m["name"] == config.HOST_NAME else links.peer_url(m)
        if url is not None:
            decks[m["name"]] = {"deck_url": url, "linked": True, "iroh": m.get("iroh", "")}
    taken = {h["led"].lower() for h in decks.values() if h.get("led")}
    spare = iter([c for c in LED_PALETTE if c not in taken] + LED_PALETTE)
    out = [{"name": n, "aka": h.get("aka", ""), "url": h["deck_url"], "current": n == config.HOST_NAME,
            "led": h.get("led") or next(spare), **({"linked": True} if h.get("linked") else {})} for n, h in decks.items()]
    mine = load_settings()
    for x in out:
        if x["current"]:
            x["led"] = mine.get("led") or x["led"]
            # its iroh key: the phone app reaches this host with no Tailscale (app/www: the Deck's hosts by key)
            if links.iroh_id():
                x["iroh"] = links.iroh_id()
    for x in out:  # names and colours set in Settings → Hosts win over the inventory's (user)
        set_ = (mine.get("hostnames") or {}).get(x["name"], {})
        x["aka"] = set_.get("alias") or x["aka"]
        x["led"] = set_.get("led") or x["led"]
    led = next((x["led"] for x in out if x["current"]), None) or mine.get("led") or LED_PALETTE[0]
    return {"current": config.HOST_NAME, "alias": mine.get("alias", ""), "led": led, "palette": LED_PALETTE,
            "pad": mine.get("pad", []), "hosts": out}



# ---- folders: the ask sheet's chips ----

def remember_folder(cwd: str) -> None:
    try:
        recent = json.loads(config.recent_path().read_text())
    except (FileNotFoundError, ValueError):
        recent = []
    recent = [cwd] + [r for r in recent if r != cwd]
    config.recent_path().parent.mkdir(parents=True, exist_ok=True)
    config.recent_path().write_text(json.dumps(recent[:8]))


def folders() -> dict:
    """Folder chips for the ask sheet: default, recently used, and projects under WORK."""
    try:
        recent = json.loads(config.recent_path().read_text())
    except (FileNotFoundError, ValueError):
        recent = []
    try:
        projects = sorted(e.path for e in os.scandir(config.WORK) if e.is_dir() and not e.name.startswith("."))
    except FileNotFoundError:
        projects = []
    home = str(config.HOME)

    def tilde(p: str) -> str:
        return p.replace(home, "~", 1) if p.startswith(home) else p

    return {"default": tilde(default_folder()), "recent": [tilde(r) for r in recent if os.path.isdir(os.path.expanduser(r))],
            "projects": [tilde(p) for p in projects]}


