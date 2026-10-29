"""Script keys (buttons): the store, validation, and the two built-in keys (the key builder, a new agent session)."""
import json
import re
import uuid

from . import config
from .agents import AGENTS, TYPES


def load_buttons() -> list[dict]:
    try:
        # Keys of a retired type (e.g. "orchid") are ignored.
        return [b for b in json.loads(config.buttons_path().read_text()) if b.get("type") in TYPES]
    except FileNotFoundError:
        return []


def save_buttons(buttons: list[dict]) -> None:
    p = config.buttons_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(buttons, indent=2) + "\n")
    tmp.replace(p)  # atomic: a crash never leaves a half-written file


def clean_button(raw: dict) -> dict:
    """Validate user input into a stored button. Raises ValueError."""
    label = str(raw.get("label", "")).strip()
    kind = raw.get("type")
    if not label:
        raise ValueError("label required")
    if kind not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    text = str(raw.get("text", "")).strip()
    if kind == "shell" and not text:
        raise ValueError("shell button needs a command")
    return {
        "id": raw.get("id") or uuid.uuid4().hex[:8],
        "label": label,
        "type": kind,
        "cwd": str(raw.get("cwd", "")).strip(),
        "text": text,
        "yolo": kind != "shell" and str(raw.get("yolo", False)).lower() in ("1", "true", "on"),
        "host": str(raw.get("host", "")).strip(),  # run on this Deck host (its /peer proxy); "" = the host serving the pad
    }


def asks(b: dict) -> bool:
    """A script key that takes what the user types when it runs: its command uses "$1"."""
    return b.get("type") == "shell" and bool(re.search(r"\$(1|\{1\}|@)", b.get("text", "")))


def run_arg(body: dict) -> dict:
    """The one-off argument of a run (an asking script key's input), or nothing."""
    arg = str(body.get("arg", "")).strip()
    return {"arg": arg} if arg else {}


def upsert_button(raw: dict) -> dict:
    b = clean_button(raw)
    buttons = load_buttons()
    idx = next((i for i, x in enumerate(buttons) if x["id"] == b["id"]), None)
    if idx is None:
        buttons.append(b)
    else:
        buttons[idx] = b
    save_buttons(buttons)
    return b


def delete_button(button_id: str) -> None:
    save_buttons([b for b in load_buttons() if b["id"] != button_id])



# ---- the built-in keys: the key builder, and a new agent session ----

AGENT_LABELS = {"claude": "Claude", "codex": "Codex", "opencode": "OpenCode", "pi": "Pi", "agy": "agy", "openclaw": "OpenClaw"}

BUILDER_PROMPT = """You are the Deck key builder. Read ./AGENTS.md first; it explains how to write the script and \
register the key.
The user wants this key:

{request}"""


def builder_button(request: str, agent: str = "claude") -> dict:
    request = request.strip()
    if not request:
        raise ValueError("describe the key you want")
    if agent not in AGENTS:
        raise ValueError(f"agent must be one of {', '.join(AGENTS)}")
    b = clean_button({"id": "builder", "label": f"Key builder ({agent})", "type": agent, "cwd": config.KEYS_DIR,
                      "text": BUILDER_PROMPT.format(request=request)})
    if agent == "codex":  # deck-key writes buttons.json, outside Codex's workspace sandbox
        roots = json.dumps(config.DATA.as_posix())  # TOML: no backslashes
        b["extra"] = ["-c", f"sandbox_workspace_write.writable_roots=[{roots}]"]
    return b


def new_agent_button(agent: str, text: str = "", cwd: str = "", yolo: bool = False) -> dict:
    """A one-off agent session from the pad's New key; blank text = empty interactive session."""
    label = AGENT_LABELS.get(agent)
    if not label:
        raise ValueError(f"agent must be one of {', '.join(AGENTS)}")
    return clean_button({"id": "new", "label": label, "type": agent, "cwd": cwd, "text": text, "yolo": yolo})


