"use strict";
// The page's script, in parts by area (deck/static/app/, joined in api/pages.py APP_ORDER and served as /static/app.js):
// core (helpers, state, the API), prefs (settings, look, layout), pairing, pad, tabs, terminal, chat, markdown,
// composer (the message box), shelf, review, editor, settings, start (boot).
//
// Core: small helpers, the page's shared state, the API (every host's sessions through this host), polling.
const $ = (s) => document.querySelector(s);
const KEYS = { esc: "\x1b", tab: "\t", stab: "\x1b[Z", ctrlc: "\x03", up: "\x1b[A", down: "\x1b[B", left: "\x1b[D", right: "\x1b[C", enter: "\r" };
const TEXT_LABEL = { shell: "Command", claude: "Prompt (optional)", codex: "Prompt (optional)", opencode: "Prompt (optional)", pi: "Prompt (optional)", agy: "Prompt (optional)", openclaw: "Prompt (optional)" };
const ICON = { shell: "›_", claude: "✳", codex: "❖", opencode: "▣", pi: "π", agy: "◭", openclaw: "✺", terminal: "$" };
// A script key that takes what the user types when it runs: its command uses "$1" (buttons.asks)
const asksArg = (b) => b?.type === "shell" && /\$(1|\{1\}|@)/.test(b.text || "");
const AGENT_NAME = { claude: "Claude", codex: "Codex", opencode: "OpenCode", pi: "Pi", agy: "agy", openclaw: "OpenClaw" };
// Agents set up on a host: its settings' `agents` list, or (from a host older than that list) the pi/agy/openclaw flags.
function agentsOn(s) {
  return Array.isArray(s.agents) ? s.agents
    : Object.keys(AGENT_NAME).filter((a) => !["pi", "agy", "openclaw"].includes(a) || s[a] !== false);
}
// Claude/Codex use the brand marks from the SVG sprite in index.html, OpenClaw its crab; other types are text glyphs.
function icon(type) {
  if (type !== "claude" && type !== "codex" && type !== "openclaw") return ICON[type] || "›_";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "logo");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#logo-${type}`);
  svg.append(use);
  return svg;
}
// Each session's logo gets its own colour (its tab and its Agents card), so tabs of the same agent tell apart. Picked
// by a hash of its name, so every screen agrees; a colour an older session already has moves to the next free one
// (and may move back when that session closes).
const TINTS = ["#ff6a33", "#3d8bff", "#1fa855", "#a070ff", "#ec4f8f", "#10a5b5", "#c99400"];
function tint(name) {
  const taken = new Map();
  for (const s of [...sessions].sort((a, b) => a.created - b.created || (a.name < b.name ? -1 : 1))) {
    let i = [...s.name].reduce((x, c) => (x * 31 + c.charCodeAt(0)) >>> 0, 7) % TINTS.length;
    for (let k = 0; k < TINTS.length && [...taken.values()].includes(i); k++) i = (i + 1) % TINTS.length;
    taken.set(s.name, i);
    if (s.name === name) break;
  }
  return TINTS[taken.get(name) ?? 0];
}
const AGENT_TYPES = Object.keys(AGENT_NAME); // sessions that are agents (not shells or terminals)

// ---- shared state ----
let view = "deck"; // the view on screen: "term" (Terminal, with Review or Web beside it) or "screen"
let buttons = []; // the Pad's script keys (/api/buttons)
let sessions = []; // every host's sessions (/api/sessions/all), named host/name; another host's carry host, and offline when it is
let hostsSeen = []; // each host's last answer: online true, false (its last list kept), or null (not reached yet)
let syncSeen = ""; // the site journal's position at the last poll: when it moves, the shared data is loaded again
let seenNeeds = new Set(); // sessions already buzzed for needing you
let term, fit, ws, attached = null; // the xterm.js terminal, its fit addon, the open socket; attached = the open session's name
let reconnectTimer;
let reviewOn = false; // Review: the Terminal view with a document beside the chat (PC)
let chatNames = new Set(), chatView = null; // chat sessions by name; chatView: the open chat {name, box, status, msgs, tools}
let menuReq = null; // the tool row's open menu (openMenu): {rid, btn}
let led = "#ff4f12"; // the host's LED colour from /api/hosts; also the terminal cursor
// Settings that shape this page (theme, look, font, defaults), cached so they apply before the server answers;
// /api/settings refreshes them (02-prefs.js).
let prefs = { agent: "claude", folder: "", yolo: false, start: "term", theme: "system", font: 13, pcfont: 13 };
try { Object.assign(prefs, JSON.parse(localStorage.getItem("deck-prefs") || "{}")); } catch { /* defaults */ }
const wide = window.matchMedia("(min-width: 1100px)"); // the PC layout
// Light is the one light look (light frame and light panes); System picks Light or Dark. "paper" was Light panes.
const sysDark = window.matchMedia("(prefers-color-scheme: dark)");
const shownTheme = () => ({ system: sysDark.matches ? "dark" : "light", paper: "light" })[prefs.theme] || prefs.theme;
// A phone and the PC each keep their own font size.
const fontKey = () => wide.matches ? "pcfont" : "font";
const font = () => prefs[fontKey()] ?? 13;

// One site on every node (deck/site.py): this page may be served by a follower (its node is not the first one up).
// It works the same (writes go to the writer through this node), so nothing is shown for it (user 2026-10-26: the
// banner was in the way); Settings → Deck → Site order says which node serves.
async function listSessions() {
  const all = await api("GET", "/api/sessions/all");
  hostsSeen = all.hosts;
  const v = String(all.sync?.seq ?? ""); // the site's journal moved: shelf, scripts or settings changed somewhere
  if (syncSeen && v !== syncSeen) { loadPrefs(); loadButtons(); loadHosts(); if (shelfOpen || chatShell) loadShelf(); }
  syncSeen = v;
  return all.hosts.flatMap((x) => x.sessions.filter((s) => s.deck).map((s) => ({ ...s, name: `${x.name}/${s.name}`,
    host: x.name, offline: x.online === false })));
}

// Tiny element builder; all user text goes through textContent.
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (v != null) el.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k instanceof Node ? k : String(k));
  return el;
}

// A trash can (outline icon, in the text colour): emoji draw differently on every device
function trashIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "ticon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = '<path d="M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"/>';
  return svg;
}

// The page's own asset version; the server sends its current one on every API answer. After an update, an open page
// (phones keep it open for days) would run old code against the new server, so it reloads once nothing is in progress.
const ASSETS = document.querySelector('meta[name="deck-assets"]')?.content;
let reloadTimer = 0;
function reloadWhenIdle() {
  if (reloadTimer) return;
  reloadTimer = setInterval(() => {
    if (document.visibilityState === "visible" && !$("#msg")?.value && !document.querySelector("dialog[open]")) location.reload();
  }, 2000);
}
// One Deck on every host: the page lists every host's sessions, named host/name, and works on another host's through
// the host that served it (its /peer proxy, api/peers.py). The page stays on its own host (user).
const HOME_HOST = document.querySelector('meta[name="deck-host"]')?.content || "";
const fullName = (n) => !n || n.includes("/") ? n : `${HOME_HOST}/${n}`;
const hostOf = (n) => n.includes("/") ? n.slice(0, n.indexOf("/")) : HOME_HOST;
const realName = (n) => n.includes("/") ? n.slice(n.indexOf("/") + 1) : n;
const viaHost = (n) => hostOf(n) === HOME_HOST ? "" : `/peer/${encodeURIComponent(hostOf(n))}`;
// A session's own calls go to its host: /api/sessions/<host/name>/... -> [/peer/<host>]/api/sessions/<name>/...
function route(path) {
  const m = path.match(/^\/api\/sessions\/([^/?]+)(.*)$/);
  const n = m && decodeURIComponent(m[1]);
  return n && n.includes("/") ? `${viaHost(n)}/api/sessions/${encodeURIComponent(realName(n))}${m[2]}` : path;
}
const wsPath = (n) => `${viaHost(n)}/ws/${encodeURIComponent(realName(n))}`;
// A client-only PC (Settings → Host sessions on this PC, off) starts new sessions on a linked PC that hosts.
const STARTS = /^\/api\/(agent|shell|build|run\/[^/?]+)$/;
async function api(method, path, body) {
  const at = method === "POST" && STARTS.test(path) && prefs.hosting === false
    ? hostList.find((x) => !x.current && x.url)?.name || "" : "";
  const r = await fetch(at ? `/peer/${encodeURIComponent(at)}${path}` : route(path),
    { method, headers: body ? { "Content-Type": "application/json" } : {}, body: body && JSON.stringify(body) });
  const v = r.headers.get("X-Deck-Assets");
  if (ASSETS && v && v !== ASSETS) reloadWhenIdle();
  const data = await r.json().catch(() => ({}));
  if (data.unpaired || data.pending) showPairGate(data.pending ? "wait" : "help");
  if (!r.ok) throw new Error(data.error || r.statusText);
  if (at && typeof data.session === "string" && !data.session.includes("/")) data.session = `${at}/${data.session}`;
  return data;
}

// Run time as a counter: 04:12, 1:07:30.
function clock(t) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  const p = (n) => String(n).padStart(2, "0");
  return s < 3600 ? `${p(Math.floor(s / 60))}:${p(s % 60)}` : `${Math.floor(s / 3600)}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`;
}

function since(t) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d`;
}

// Inline two-tap confirm (no browser dialogs).
function confirmTwice(btn) {
  if (btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = btn.dataset.label; return true; }
  btn.dataset.label = btn.textContent; btn.dataset.armed = "1"; btn.textContent = "Tap again";
  setTimeout(() => { if (btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = btn.dataset.label; } }, 3000);
  return false;
}

function toast(text) {
  const stack = $("#toasts") || document.body.appendChild(h("div", { id: "toasts", role: "status", "aria-live": "polite" }));
  const el = h("div", { class: "toast" }, text);
  stack.append(el);
  setTimeout(() => el.remove(), 4000);
}
window.toast = toast;  // for screen.js

// The clipboard API needs a secure page; the app's iroh pages and plain-http hosts aren't: a hidden box's copy instead
async function copyText(text) {
  if (window.isSecureContext && navigator.clipboard) return navigator.clipboard.writeText(text);
  const t = h("textarea", { style: "position:fixed;opacity:0" }); t.value = text;
  document.body.append(t); t.select();
  const ok = document.execCommand("copy"); t.remove();
  if (!ok) throw new Error("copy refused");
}

// A small menu at x, y: items(item) gives its buttons; a run returning false keeps the menu open
function popMenu(x, y, items) {
  $("#tabmenu")?.remove();
  const item = (text, run) => h("button", { type: "button", onclick: async (e) => {
    const btn = e.currentTarget;
    if (run.length && await run(btn) === false) return;
    menu.remove();
    if (!run.length) run();
  } }, text);
  const menu = h("div", { id: "tabmenu", class: "hpop tabmenu", role: "menu" }, ...items(item));
  document.body.append(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, innerWidth - r.width - 8)}px`;
  menu.style.top = `${Math.min(y, innerHeight - r.height - 8)}px`;
  const away = (e) => {
    if (e.type === "keydown" ? e.key !== "Escape" : menu.contains(e.target)) return;
    menu.remove();
    for (const ev of ["pointerdown", "keydown"]) removeEventListener(ev, away, true);
  };
  for (const ev of ["pointerdown", "keydown"]) addEventListener(ev, away, true);
}

// ---- polling ----
async function poll() {
  try {
    sessions = await listSessions();
    pruneChatCopies();
    noteChats(sessions);
    render();
    renderProjBar();
    scheduleCrop(); // status card follows the session's latest status
  } catch {
    setPulse("NO LINK", "offline");
  }
}

// Shown only when it matters: something needs you, or the host is unreachable (no LIVE count or STANDBY: user)
function setPulse(text, cls) {
  $("#pulse").hidden = cls !== "alert" && cls !== "offline";
  $("#pulse").className = `lcd ${cls}`;
  $("#pulsetext").textContent = text;
}

function render() {
  const runs = sessions.filter((s) => s.type !== "terminal");
  const live = runs.filter((s) => s.state === "working").length; // idle / waiting sessions are not "live"
  const needs = runs.filter((s) => s.state === "needs_you");
  const pad2 = (n) => String(n).padStart(2, "0");
  if (needs.length) setPulse(`${pad2(needs.length)} REQ`, "alert");
  else if (live) setPulse(`${pad2(live)} LIVE`, "live");
  else setPulse("STANDBY", "");
  for (const s of needs) if (!seenNeeds.has(s.name)) navigator.vibrate?.([60, 40, 60]);
  seenNeeds = new Set(needs.map((s) => s.name));

  renderPad(); // the Pad column shows beside every screen
  if (view === "term") renderChips();
}
