"""Agents see and message the user's other sessions (Settings → Agents can see sessions), and read the
instructions Deck gives every agent."""
import asyncio

from aiohttp import web

from .. import agents, auth, config, describe, sessions, settings
from .common import bad_request


async def get_deck_context(_):
    """The instructions Deck gives every agent (agents.deck_context); the chat's debug mode shows them."""
    return web.json_response({"text": agents.deck_context()})


def agent_sessions_gate(request):
    """This host's own setting, for its own agents and other hosts' alike (each host decides for its sessions)."""
    if not settings.settings().get("sessionagents"):
        raise web.HTTPForbidden(text="The user hasn't let agents see other sessions "
                                    "(Deck: Settings > Agents can see sessions).\n")


def agent_session_lines(sessions: list[dict], descs: dict) -> list[str]:
    out = []
    for s in sessions:
        if s["type"] == "terminal":
            continue
        line = f"- `{s['name']}`: {s.get('title') or s['label']} ({s['type']}, {s['state']})"
        if desc := (descs.get(s["name"]) or {}).get("text"):
            line += f"\n  {desc}"
        out.append(line)
    return out or ["(none)"]


async def agent_sessions(request):
    """The sessions as an agent reads them: markdown, one per line, with its description. Without ?here=1, the other
    hosts' too (each lists its own), under their host's heading."""
    import aiohttp
    agent_sessions_gate(request)
    live, descs = await asyncio.to_thread(sessions.list_sessions), describe.load()
    out = [f"# {config.HOST_NAME} (this host)", "", *agent_session_lines(live, descs)]
    if request.query.get("here") == "1" or not auth.host_key():
        return web.Response(text="\n".join(out) + "\n", content_type="text/markdown")

    async def peer(h):
        try:
            async with request.app["peer_http"].get(h["url"].rstrip("/") + "/api/agent/sessions?here=1",
                                                    headers={"Authorization": f"Bearer {auth.host_key()}"},
                                                    timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    return (await r.text()).split("\n", 2)[-1].strip()  # its own heading off
                if r.status == 403:
                    return "(that host doesn't let agents see its sessions)"
        except (TimeoutError, aiohttp.ClientError, OSError):
            pass
        return "(offline)"
    peers = [h for h in settings.hosts()["hosts"] if not h["current"]]
    for h, text in zip(peers, await asyncio.gather(*map(peer, peers))):
        out += ["", f"# {h['name']} (reach its sessions at /peer/{h['name']}/api/agent/sessions/NAME)", "", text]
    return web.Response(text="\n".join(out) + "\n", content_type="text/markdown")


async def agent_session_read(request):
    """The end of one session's output: ?chars= (default 6000)."""
    agent_sessions_gate(request)
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound(text=f"no session {name}\n")
    try:
        n = max(200, min(50_000, int(request.query.get("chars") or 6000)))
    except ValueError as e:
        return bad_request(e)
    text = (await asyncio.to_thread(sessions.session_output, name))[-n:]
    desc = describe.get(name).get("text")
    return web.Response(text=(f"Description: {desc}\n\n" if desc else "") + text, content_type="text/markdown")


async def agent_session_send(request):
    """An agent messages another session: {text, from (its DECK_SESSION_NAME)}. Typed in and submitted, marked as
    from that agent so the reader knows it isn't the user."""
    agent_sessions_gate(request)
    name = request.match_info["name"]
    if not sessions.session_exists(name):
        raise web.HTTPNotFound(text=f"no session {name}\n")
    try:  # an agent's shell quoting often breaks the JSON: say so, not a bare 500
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return bad_request(ValueError(
            'the body isn\'t a JSON object like {"text": "...", "from": "HOST/SESSION"}. PowerShell drops the quotes in '
            "curl.exe -d's JSON: use Invoke-RestMethod -Method Post -ContentType application/json -Uri URL "
            "-Body (@{text='...'; from=\"HOST/$env:DECK_SESSION_NAME\"} | ConvertTo-Json)"))
    text, frm = str(body.get("text", "")).strip(), " ".join(str(body.get("from") or "").replace("]", ")").split())[:80]
    if not text:
        return bad_request(ValueError("text is empty"))
    if text.startswith("/"):  # a chat would run it as the user's command (/compact, /resume), with no sender on it
        return bad_request(ValueError("text starts with / (a command): reword it"))
    if frm in (name, f"{config.HOST_NAME}/{name}"):  # from: host/session
        return bad_request(ValueError("that's your own session"))
    if sessions.session_meta(name).get("agent") not in agents.AGENTS:  # a shell would run the message as a command
        return bad_request(ValueError(f"{name} isn't an agent session"))
    if any(s["name"] == name and s["state"] == "needs_you" for s in await asyncio.to_thread(sessions.list_sessions)):
        # the text would go into its open question as the user's answer
        raise web.HTTPConflict(text=f"{name} is waiting on the user's answer to a question: try again later\n")
    # chat_bridge.AGENT_FROM knows an agent's message by these words: the chat labels it, so no one takes it for the user's
    who = f"agent in {frm}" if frm else "another agent"
    if sessions.is_chat(name):
        sessions.chat_command(name, {"t": "say", "text": text, "from": who})
    else:
        await sessions.type_line(name, f"[from {who}, not the user] {text}")
    return web.Response(text=f"sent to {name}\n")


# ---- the shelf (deck/shelf.py) ----


def routes() -> list:
    return [
        web.get("/api/deck-context", get_deck_context),
        web.get("/api/agent/sessions", agent_sessions),
        web.get("/api/agent/sessions/{name}", agent_session_read),
        web.post("/api/agent/sessions/{name}/send", agent_session_send),
    ]
