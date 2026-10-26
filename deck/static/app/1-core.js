"use strict";
// The page's script, in parts by area (deck/static/app/, joined in server.APP_ORDER and served as /static/app.js by
// server.app_script): core (helpers, API, pairing, tabs, polling), pad, terminal, chat, shelf, review, editor, settings, start.
const $ = (s) => document.querySelector(s);
const KEYS = { esc: "\x1b", tab: "\t", stab: "\x1b[Z", ctrlc: "\x03", up: "\x1b[A", down: "\x1b[B", left: "\x1b[D", right: "\x1b[C", enter: "\r" };
const TEXT_LABEL = { shell: "Command", claude: "Prompt (optional)", codex: "Prompt (optional)", opencode: "Prompt (optional)", pi: "Prompt (optional)", agy: "Prompt (optional)", openclaw: "Prompt (optional)" };
const ICON = { shell: "›_", claude: "✳", codex: "❖", opencode: "▣", pi: "π", agy: "◭", openclaw: "✺", terminal: "$" };
// A script key that takes what the user types when it runs: its command uses "$1" (core.asks)
const asksArg = (b) => b?.type === "shell" && /\$(1|\{1\}|@)/.test(b.text || "");
const AGENT_NAME = { claude: "Claude", codex: "Codex", opencode: "OpenCode", pi: "Pi", agy: "agy", openclaw: "OpenClaw" };
// Claude/Codex use the brand marks from the SVG sprite in index.html, OpenClaw its crab; other types are text glyphs.
// Agents set up on a host: its settings' `agents` list, or (from a host older than that list) the pi/agy/openclaw flags.
function agentsOn(s) {
  return Array.isArray(s.agents) ? s.agents
    : Object.keys(AGENT_NAME).filter((a) => !["pi", "agy", "openclaw"].includes(a) || s[a] !== false);
}
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
const LIVE = new Set(["needs_you", "your_turn", "working", "idle"]);

let view = "deck";
let buttons = [];
let sessions = [];
// Every host's sessions (/api/sessions/all), named host/name; another host's carry host, and offline when it is
let syncSeen = "";
let hostsSeen = []; // each host's last answer: online true, false (its last list kept), or null (not reached yet)
async function listSessions() {
  const all = await api("GET", "/api/sessions/all");
  hostsSeen = all.hosts;
  const v = String(all.sync?.seq ?? ""); // the site's journal moved: shelf, scripts or settings changed somewhere
  if (syncSeen && v !== syncSeen) { loadPrefs(); loadButtons(); loadHosts(); if (shelfOpen || chatShell) loadShelf(); }
  syncSeen = v;
  showSite(all.site);
  return all.hosts.flatMap((x) => x.sessions.filter((s) => s.deck).map((s) => ({ ...s, name: `${x.name}/${s.name}`,
    host: x.name, offline: x.online === false })));
}
let seenNeeds = new Set();
// One site on every node (deck/site.py): this page may be served by a follower (its node is not the first one up).
// It works the same (writes go to the writer through this node), so nothing is shown here (user 2026-10-26: the
// banner was in the way); Settings → Deck → Site order says which node serves.
let siteNow = null;
function showSite(site) { siteNow = site || null; }
let reconnectTimer;
let term, fit, ws, attached = null; // attached = session name in the Terminal tab
let reviewOn = false; // Review: the Terminal view with a document beside the chat (PC)
// Chat sessions (see "chat sessions" below). Declared up here because applyPrefs() runs at load and reads chatView.
let chatNames = new Set(), chatView = null; // chatView: the open chat {name, box, status, msgs, tools}
let menuReq = null; // the tool row's open menu (openMenu): {rid, btn}
let led = "#ff4f12"; // host LED colour from /api/hosts; also the terminal cursor
// Settings that shape this page (theme, start view, defaults). Cached so the theme and start view
// apply before the server answers; /api/settings refreshes them.
let prefs = { agent: "claude", folder: "", yolo: false, start: "term", theme: "system", font: 13, pcfont: 13 };
try { Object.assign(prefs, JSON.parse(localStorage.getItem("deck-prefs") || "{}")); } catch { /* defaults */ }
// Up here because applyPrefs() runs at load and reads the font size.
const wide = window.matchMedia("(min-width: 1100px)");
// Light is the one light look (light frame and light panes); System picks Light or Dark. "paper" was Light panes.
const sysDark = window.matchMedia("(prefers-color-scheme: dark)");
const shownTheme = () => ({ system: sysDark.matches ? "dark" : "light", paper: "light" })[prefs.theme] || prefs.theme;
// A phone and the PC each keep their own font size.
const fontKey = () => wide.matches ? "pcfont" : "font";
const font = () => prefs[fontKey()] ?? 13;
function applyPrefs() {
  // Agent choices (key editor, ask sheet) only for agents set up on this host; + follows on its next render (user).
  const setUp = agentsOn(prefs);
  for (const a of Object.keys(AGENT_NAME))
    for (const el of document.querySelectorAll(`input[value=${a}]`)) el.closest("label").style.display = setUp.includes(a) ? "" : "none";
  $("#setdurable").style.display = prefs.durable && prefs.pi !== false ? "" : "none"; // only where pi-durable is installed
  $("#docsendon").checked = prefs.readernote !== false; // the reader's "Tell agent" box
  $("#term").classList.toggle("notools", prefs.chattools === false); // chats without their tool calls
  $("#app").classList.toggle("debug", !!prefs.debug); // chats show Deck's hidden text too (style.css .cdebug)
  if (chatView) refreshBars(); // replies change height
  document.documentElement.dataset.theme = shownTheme();
  if (term) term.options.theme = termTheme(); // Light: a light terminal too
  if (term && term.options.fontSize !== font()) { term.options.fontSize = font(); fit?.fit(); }
  document.documentElement.style.setProperty("--chatfont", `${font() + 1}px`); // chats follow the terminal font size
  // Message box off: phones type straight into the terminal (its keyboard edits can be rough). Hiding it resizes
  // #term (its ResizeObserver resends the size); the crop setting changes the agent's rows, so resize only then.
  $("#say").hidden = prefs.composer === false && !chatView; // a chat always has its message box
  placeHarness();
  if (term) setTermTyping();
  if (term && (prefs.crop !== false) !== !!extraRows && ["claude", "codex"].includes(sessions.find((x) => x.name === attached)?.type)) {
    sendSize(); scheduleCrop();
  }
  for (const k of document.querySelectorAll("#view-term .keys [data-q]")) k.hidden = (prefs.hidekeys || []).includes(k.dataset.q);
  // Resizing off: no grips, and the columns go back to their default widths.
  $("#app").classList.toggle("noresize", prefs.resize === false);
  applyLayout();
  applyScriptShut();
}
async function loadPrefs() {
  try {
    prefs = await api("GET", "/api/settings");
    try { localStorage.setItem("deck-prefs", JSON.stringify(prefs)); } catch { /* private mode */ }
    if (!tabDrag) tabOrder = prefs.tabs || [];
    applyPrefs();
  } catch { /* keep cached */ }
}
// Page state shared by every browser and the Deck app on this host (open session, tab order, text terminal): kept in
// the host's settings, so a phone's browser opens where the app left off. Column widths stay per screen.
function saveState(fields) {
  Object.assign(prefs, fields);
  try { localStorage.setItem("deck-prefs", JSON.stringify(prefs)); } catch { /* private mode */ }
  api("PUT", "/api/settings", fields).catch(() => { /* kept on this page until the next save */ });
}
// Desktop columns (widths, collapsed) are shared too, so the PC app window and a browser on the PC look alike.
function applyLayout() {
  const l = prefs.layout || {};
  for (const k of ["padw", "padcw", "feedcw"]) {
    if (l[k] && prefs.resize !== false) $("#app").style.setProperty(`--${k}`, `${l[k]}px`);
    else $("#app").style.removeProperty(`--${k}`);
  }
  // Margins dragged: the terminal keeps the width it had and the margins share what's left in their dragged ratio, so
  // resizing the window changes the margins, not the terminal (a window narrower than it shrinks the terminal instead).
  const keep = l.termw && (l.padcw || l.feedcw) && prefs.resize !== false;
  // Margins saved before the terminal's width was: take the width they leave it now, once
  if (!l.termw && (l.padcw || l.feedcw) && prefs.resize !== false && wide.matches) { saveCols(); return; }
  const cols = keep ? { l: `minmax(0, ${l.padcw || 0}fr)`, m: `minmax(0, ${l.termw}px)`, r: `minmax(0, ${l.feedcw || 0}fr)` } : {};
  for (const c of ["l", "m", "r"]) {
    if (cols[c]) $("#app").style.setProperty(`--col${c}`, cols[c]);
    else $("#app").style.removeProperty(`--col${c}`);
  }
  $("#togglefull")?.setAttribute("aria-pressed", !!l.full);
  $("#togglefull")?.setAttribute("title", l.full ? "Minimize: the margins as before" : "Fullscreen: no margins");
  for (const cls of ["nopad", "nobottom"]) {
    $("#app").classList.toggle(cls, !!l[cls]);
    $(`#toggle${cls.slice(2)}`)?.setAttribute("aria-pressed", !l[cls]);
  }
  applyMsgShut();
  renderLayouts();
}
function saveLayout(k, v) { saveState({ layout: { ...(prefs.layout || {}), [k]: v || undefined } }); }
// Desktop columns as laid out now, in px: [left margin, terminal, right margin].
const gridCols = () => getComputedStyle($("#app")).gridTemplateColumns.split(" ").map(parseFloat);
// After a margin drag: keep both margins and the terminal's width (applyLayout turns the margins into shares).
function saveCols() {
  const [pad, main, feed] = gridCols().map(Math.round);
  saveState({ layout: { ...(prefs.layout || {}), padcw: pad || undefined, feedcw: feed || undefined, termw: pad || feed ? main : undefined } });
  applyLayout();
}
applyPrefs();
sysDark.addEventListener("change", applyPrefs);
// The terminal's colours: a dark screen, or a light one in Light
const termTheme = () => shownTheme() === "light"
  ? { background: "#efe9dc", foreground: "#1f1f1d", cursor: led, cursorAccent: "#efe9dc", selectionBackground: `${led}44`,
      black: "#1f1f1d", white: "#6b675f", brightWhite: "#3a3833", yellow: "#9a6b00", brightYellow: "#8a5d00",
      green: "#1c7c33", brightGreen: "#16682a", cyan: "#0e6f7c", brightCyan: "#0b5c67", blue: "#1f5fd1", magenta: "#8b3fb0" }
  : { background: "#121311", foreground: "#ebe7dc", cursor: led, cursorAccent: "#121311", selectionBackground: `${led}44` };

// Tiny element builder; all user text goes through textContent.
// A trash can (outline icon, in the text colour): emoji draw differently on every device
function trashIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "ticon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = '<path d="M4 7h16M10 11v6M14 11v6M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"/>';
  return svg;
}

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
// the host that served it (its /peer proxy, server.py peer_proxy). The page stays on its own host (user).
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

// ---- pairing: this device must be paired before it may use the Deck ----
let pairCode = null;
function showPairGate(mode) {
  if (mode === "help" && pairCode) return;  // holding a code: the page's other requests failing mustn't hide its form
  const g = $("#pairgate");
  $("#pairclaim").hidden = mode !== "claim";
  $("#pairwait").hidden = mode !== "wait";
  $("#pairhelp").hidden = mode !== "help";
  if (!g.open) g.showModal();
  if (mode === "wait") waitForApproval();
}
$("#pairgate").addEventListener("cancel", (e) => e.preventDefault()); // can't be dismissed, but the app can leave
$("#pairleave").hidden = !window.Capacitor;
$("#pairleavebtn").onclick = () => { location.href = "https://localhost/#hosts"; };  // the app's host list (app/www)
let approvalTimer = 0;
function waitForApproval() {
  clearInterval(approvalTimer);
  approvalTimer = setInterval(async () => {
    const me = await fetch("/api/me").then((r) => r.json()).catch(() => null);
    if (me?.device && !me.pending) { clearInterval(approvalTimer); location.replace("/"); }
    if (me && !me.device) { clearInterval(approvalTimer); pairCode = null; showPairGate("help"); $("#pairerr").textContent = "Pairing was refused."; }
  }, 2000);
}
$("#pairform").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("POST", "/api/pair/claim", { code: pairCode, name: $("#pairname").value });
    pairCode = null;
    history.replaceState(null, "", "/");
    showPairGate("wait");
  } catch (err) { $("#pairerr").textContent = err.message; }
};
function pairFromHash() {
  if (!location.hash.startsWith("#pair=")) return false;
  const q = new URLSearchParams(location.hash.slice(1));
  pairCode = q.get("pair");
  // Scanned with the phone camera, the link lands in the browser: offer to pass it to the installed Deck app, over
  // iroh when the host runs it (deck://pair: the app saves the host by its key), else this same address.
  $("#pairapp").hidden = !!window.Capacitor || !/Android/i.test(navigator.userAgent);
  const iroh = q.get("iroh"), enc = encodeURIComponent;
  $("#pairapplink").href = iroh
    ? `intent://pair?key=${iroh}&name=${enc(q.get("name") || location.hostname.split(".")[0])}&code=${enc(pairCode)}#Intent;scheme=deck;package=dev.deck.app;end`
    : `intent://${location.host}/?pair=${enc(pairCode)}#Intent;scheme=https;package=dev.deck.app;end`;
  $("#pairname").value = /iPhone|iPad/.test(navigator.userAgent) ? "iPhone" : /Android/.test(navigator.userAgent) ? "Android phone" : "Browser";
  for (const d of document.querySelectorAll("dialog[open]")) if (d.id !== "pairgate") d.close();
  showPairGate("claim");
  return true;
}
// A pairing link opened inside an already-loaded Deck (e.g. tapped in Settings → Devices on the phone app itself).
window.addEventListener("hashchange", pairFromHash);
(async () => {
  if (pairFromHash()) return;
  const me = await fetch("/api/me").then((r) => r.json()).catch(() => null);
  if (me?.pending) showPairGate("wait");
  else if (me?.enforced && !me.device) showPairGate("help");
})();

// Settings → Devices: list, remove, and pair a new device (QR + approve).
async function renderDevices() {
  const list = await api("GET", "/api/devices").catch(() => []);
  const ago = (t) => (t ? `${since(t)} ago` : "never");
  $("#devlist").replaceChildren(...(list.length ? list.map((d) => h("div", { class: "hostrow" },
    h("b", {}, d.name), h("span", {}, d.pending ? "waiting" : `used ${ago(d.last_seen)}`),
    d.me ? h("em", {}, "this one") : h("button", { type: "button", class: "ghost small", onclick: async () => {
      if (!confirm(`Remove ${d.name}? It will need pairing again.`)) return;
      await api("DELETE", `/api/devices/${d.id}`).catch((e) => toast(e.message));
      renderDevices();
    } }, "Remove"))) : [h("p", { class: "hint" }, "No devices paired yet.")]));
}
let pairPoll = 0, shownCode = null;
$("#devbtn").onclick = () => { $("#pairbox").hidden = true; renderDevices(); $("#devices").showModal(); };
$("#devclose").onclick = () => $("#devices").close();  // a shown code keeps waiting: the Allow dialog still pops up
$("#pairnew").onclick = async () => {
  try {
    const { code, iroh, name, url } = await api("POST", "/api/pair/start");
    shownCode = code;
    // The QR opens on another device: 127.0.0.1 (this PC's own browser) means nothing there, so use the host's address.
    const origin = url && /^(127\.|localhost$|\[::1\]$)/.test(location.hostname) ? url.replace(/\/$/, "") : location.origin;
    // Opens in the phone's browser; with deck-iroh running it also carries the host's iroh key, so "Pair the Deck
    // app instead" there pairs the app over iroh (works on any network afterwards, no Tailscale).
    const link = `${origin}/#pair=${code}${iroh ? `&iroh=${iroh}&name=${encodeURIComponent(name)}` : ""}`;
    $("#pairqr").src = `/api/pair/qr.svg?url=${encodeURIComponent(link)}`;
    $("#pairlink").href = link; $("#pairlink").textContent = link;
    // Tapped on this same device (e.g. the phone app pairing itself): stay in this page, never hand off to a browser.
    $("#pairlink").onclick = (e) => { e.preventDefault(); location.hash = link.split("#")[1]; };
    $("#pairask").textContent = "Waiting for the new device to scan…";
    $("#pairbox").hidden = false;
    clearInterval(pairPoll);
    const until = Date.now() + 5 * 60 * 1000;  // the code's lifetime
    pairPoll = setInterval(async () => {
      if (Date.now() > until) return clearInterval(pairPoll);
      const { device } = await api("GET", `/api/pair/pending?code=${encodeURIComponent(code)}`).catch(() => ({}));
      if (!device) return;
      clearInterval(pairPoll);
      $("#pairask").textContent = `"${device.name}" is asking to join.`;
      $("#pairapprovetext").textContent = `Allow "${device.name}" to use this Deck?`;
      $("#pairapprove").showModal();
    }, 2000);
  } catch (e) { toast(e.message); }
};
// The PC app's tray "Pair a phone…" opens Deck at #devices-pair: straight to a new pairing code.
function devicesPairFromHash() {
  if (location.hash !== "#devices-pair") return;
  history.replaceState(null, "", "/");
  $("#devbtn").onclick();
  $("#pairnew").onclick();
}
window.addEventListener("hashchange", devicesPairFromHash);
devicesPairFromHash();
for (const [btn, allow] of [["#pairallow", true], ["#pairdeny", false]]) $(btn).onclick = async () => {
  $("#pairapprove").close();
  try {
    await api("POST", "/api/pair/decide", { code: shownCode, allow });
    toast(allow ? "Device allowed" : "Device denied");
  } catch (e) { toast(e.message); }
  $("#pairbox").hidden = true;
  renderDevices();
};
$("#pairapprove").addEventListener("cancel", (e) => e.preventDefault()); // answer it: Allow or Deny

// Settings → PCs: linked PCs (deck/links.py). This PC makes a code and approves it, or pastes another PC's code.
let linkPoll = 0, joinPoll = 0, linkSecret = "";
async function renderLinks() {
  const { me, members = [] } = await api("GET", "/api/links").catch(() => ({}));
  const act = (label, run) => h("button", { type: "button", class: "ghost small", onclick: async () => {
    try { await run(); } catch (e) { toast(e.message); }
    renderLinks(); loadHosts();
  } }, label);
  $("#linklist").replaceChildren(...(members.length ? members.map((m) => h("div", { class: "hostrow" },
    h("b", {}, m.name), m.hosting === false ? h("span", {}, "client only") : null, m.name === me ? h("em", {}, "this one") : null,
    m.name === me ? act("Leave", async () => {
      if (!confirm("Take this PC out of the group? It keeps its own sessions; link it again to rejoin.")) return;
      await api("POST", "/api/link/leave");
    }) : act("Remove", async () => {
      if (!confirm(`Remove ${m.name}? The other PCs get a new key, so it can't reach them any more.`)) return;
      const { missed, relink } = await api("DELETE", `/api/links/${encodeURIComponent(m.name)}`);
      const out = [...missed, ...relink];
      toast(out.length ? `Removed. Not reached, so link again (Leave first): ${out.join(", ")}` : `Removed ${m.name}`);
    })))
    : [h("p", { class: "hint" }, "Not linked with other PCs yet.")]));
}
$("#linkbtn").onclick = () => { $("#linkbox").hidden = $("#joinbox").hidden = true; renderLinks(); $("#links").showModal(); };
$("#linkclose").onclick = () => $("#links").close(); // a shown code keeps waiting: the Allow dialog still pops up
$("#linknew").onclick = async () => {
  try {
    const { code } = await api("POST", "/api/link/start");
    linkSecret = code.split("-").pop();
    $("#linkcode").value = code;
    $("#linkask").textContent = "Waiting for the other PC…";
    $("#joinbox").hidden = true; $("#linkbox").hidden = false;
    $("#linkcode").select();
    clearInterval(linkPoll);
    const until = Date.now() + 5 * 60 * 1000;
    linkPoll = setInterval(async () => {
      if (Date.now() > until) { $("#linkask").textContent = "Code expired."; return clearInterval(linkPoll); }
      const { pc } = await api("GET", `/api/link/pending?secret=${linkSecret}`).catch(() => ({}));
      if (!pc) return;
      clearInterval(linkPoll);
      $("#linkask").textContent = `"${pc.name}" is asking to link.`;
      $("#linkapprovetext").textContent = `Link "${pc.name}" with this PC? It will be able to run anything here, and on every PC linked with this one.`;
      $("#linkapprove").showModal();
    }, 2000);
  } catch (e) { toast(e.message); }
};
for (const [btn, allow] of [["#linkallow", true], ["#linkdeny", false]]) $(btn).onclick = async () => {
  $("#linkapprove").close();
  try {
    await api("POST", "/api/link/decide", { secret: linkSecret, allow });
    toast(allow ? "PC linked" : "Link refused");
  } catch (e) { toast(e.message); }
  $("#linkbox").hidden = true;
  renderLinks(); loadHosts();
};
$("#linkapprove").addEventListener("cancel", (e) => e.preventDefault());
$("#linkjoin").onclick = async () => {
  $("#linkbox").hidden = true; $("#joinbox").hidden = false;
  const { why } = await api("GET", "/api/link/join").catch(() => ({}));
  $("#joinstate").textContent = why || "";
  $("#joincode").focus();
};
$("#joingo").onclick = async () => {
  try {
    $("#joinstate").textContent = "Reaching the other PC…";
    const j = await api("POST", "/api/link/join", { code: $("#joincode").value });
    $("#joinstate").textContent = `Waiting for ${j.host || "the other PC"} to allow it…`;
    clearInterval(joinPoll);
    joinPoll = setInterval(async () => {
      const s = await api("GET", "/api/link/join").catch(() => ({}));
      if (s.state === "pending") return;
      clearInterval(joinPoll);
      $("#joinstate").textContent = s.state === "ok" ? `Linked with ${s.host}.` : s.state === "refused" ? "The other PC refused." : s.error || "Linking failed.";
      if (s.state === "ok") { $("#joincode").value = ""; $("#joinbox").hidden = true; toast(`Linked with ${s.host}`); renderLinks(); loadHosts(); }
    }, 2000);
  } catch (e) { $("#joinstate").textContent = e.message; }
};

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

// ---- tabs ----
// Desktop (wide): Pad and Terminal side by side; the main column shows Terminal or Screen.
// The open session is kept apart for phones and wide screens: one session on a phone and the PC at once would keep
// resizing between their sizes (tmux has one size per window), garbling both.
const sessionKey = () => wide.matches ? "pcsession" : "session";
wide.addEventListener("change", () => term && applyPrefs());
const isVisible = (id, v) => wide.matches
  ? id === "view-deck" || id === `view-${v === "screen" ? "screen" : "term"}`
  : id === `view-${v}`;
function show(v, connect = true) {
  if (v === "agents" || v === "deck") v = "term"; // retired views: Agents, and the Pad (a column now)
  view = v;
  $("#chips").hidden = v !== "term";
  localStorage.setItem("deck-view", v);
  for (const el of document.querySelectorAll(".view")) el.hidden = !isVisible(el.id, v);
  for (const t of document.querySelectorAll(".tabbar button")) t.classList.toggle("on", t.dataset.view === (v === "term" && (reviewOn || webOn) ? (webOn ? "web" : "review") : v));
  if (connect) {
    if (v === "term") { if (attached) attach(attached); else attachNewest(); }
    else detach();
  }
  window.deckScreen?.onShow(v);
  render();
}
// Review is the Terminal view with a document beside the chat (setReview, below)
for (const t of document.querySelectorAll(".tabbar button")) t.onclick = () => {
  if (["term", "review", "web"].includes(t.dataset.view)) { setWeb(t.dataset.view === "web"); setReview(t.dataset.view === "review"); }
  show(["review", "web"].includes(t.dataset.view) ? "term" : t.dataset.view);
};

// The view tabs sit in the header beside the host name (phone and PC alike), always shown.
$("#hostbtn").after($(".tabbar"));
// Desktop: drag the terminal's left edge to resize the Pad column, its right edge for an empty margin (widths shared:
// applyLayout). A collapsed Pad leaves an empty margin too, dragged the same way; each margin has its own width
// (--padcw, and --feedcw on the right: the name of the retired Agents column it replaced; default 0).
const COL = { pad: ["--padw", 150, 640], feed: [null, 0, 760] };
for (const side of ["pad", "feed"]) {
  const [prop, min, max] = COL[side];
  const cprop = `--${side}cw`;
  const grip = $(`#grip${side}`);
  const margin = () => true; // the Pad lives in the terminal's own column: both grips size empty margins
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    // Drag in px: pin both margins at their current widths and drop the shares (saveCols restores them)
    const [pad, , feed] = gridCols();
    $("#app").style.setProperty("--padcw", `${pad}px`); $("#app").style.setProperty("--feedcw", `${feed}px`);
    for (const c of ["l", "m", "r"]) $("#app").style.removeProperty(`--col${c}`);
    grip.classList.add("drag"); $("#app").classList.add("resizing");
    const box = $("#app").getBoundingClientRect();
    const collapsed = margin();
    const [p, lo] = collapsed ? [cprop, 0] : [prop, min];
    const move = (ev) => {
      const raw = side === "pad" ? ev.clientX - box.left : box.right - ev.clientX;
      $("#app").style.setProperty(p, `${Math.round(Math.max(lo, Math.min(max, raw)))}px`);
    };
    const up = () => {
      grip.removeEventListener("pointermove", move);
      grip.classList.remove("drag"); $("#app").classList.remove("resizing");
      saveCols();
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up, { once: true });
    grip.addEventListener("pointercancel", up, { once: true });
  });
  grip.addEventListener("dblclick", () => { // double-click: back to the default width (a margin: none)
    const [pad, , feed] = gridCols();
    $("#app").style.setProperty("--padcw", `${side === "pad" ? 0 : pad}px`);
    $("#app").style.setProperty("--feedcw", `${side === "feed" ? 0 : feed}px`);
    for (const c of ["l", "m", "r"]) $("#app").style.removeProperty(`--col${c}`);
    saveCols();
  });
}
// Fullscreen (PC header): the margins beside the terminal go; Minimize brings back the widths from before (user)
$("#togglefull").onclick = () => {
  const l = prefs.layout || {};
  saveState({ layout: l.full
    ? { ...l, full: undefined, padcw: l.bpadcw, feedcw: l.bfeedcw, termw: l.btermw, bpadcw: undefined, bfeedcw: undefined, btermw: undefined }
    : { ...l, full: true, bpadcw: l.padcw, bfeedcw: l.feedcw, btermw: l.termw, padcw: undefined, feedcw: undefined, termw: undefined } });
  applyLayout();
};
// Saved layouts (PC header): the Layouts button saves the columns as they are now, which adds a button that brings
// them back, or deletes a saved one (user)
function sameLayout(a, b) { return JSON.stringify(Object.entries(a || {}).sort()) === JSON.stringify(Object.entries(b || {}).sort()); }
function renderLayouts() {
  const box = $("#layouts");
  if (!box) return;
  box.replaceChildren(...(prefs.layouts || []).map((x) => h("button", {
    class: "paneltoggle", type: "button", title: `Restore layout ${x.name}`, "aria-pressed": sameLayout(x.layout, prefs.layout),
    onclick: () => { saveState({ layout: { ...x.layout } }); applyLayout(); },
  }, `Layout ${x.name}`))); // named, not a bare number (user: "vague button")
}
function saveLayouts(list) { saveState({ layouts: list }); renderLayouts(); }
$("#layoutbtn").onclick = (e) => {
  const r = e.currentTarget.getBoundingClientRect(), list = prefs.layouts || [];
  popMenu(r.left, r.bottom + 4, (item) => [
    item("Save this layout", () => {
      const used = new Set(list.map((x) => x.name));
      let n = 1;
      while (used.has(String(n))) n++;
      saveLayouts([...list, { id: Date.now().toString(36), name: String(n), layout: { ...(prefs.layout || {}) } }]);
    }),
    ...list.map((x) => item(`Delete ${x.name}`, (btn) => {
      if (!confirmTwice(btn)) return false; // stays open: click again to delete
      saveLayouts((prefs.layouts || []).filter((y) => y.id !== x.id));
    }))]);
};
// Review's divider: drag to share the width between the chat and the reader; double-click: half and half
{
  const grip = $("#docgrip");
  const split = (f) => { $("#app").style.setProperty("--chatfr", f); $("#app").style.setProperty("--docfr", 1 - f); };
  try { const f = +localStorage.getItem("deck-review-split"); if (f > 0.15 && f < 0.85) split(f); } catch { /* half and half */ }
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("drag"); $("#app").classList.add("resizing");
    const pane = webOn ? $("#webpane") : $("#docpane"); // the reader, or the Web view's page
    const t = $("#term").getBoundingClientRect(), d = pane.getBoundingClientRect(), g = grip.getBoundingClientRect();
    const a = t.left, b = d.right;
    // the shares split what's left after each side's padding and borders (flex-basis 0), so the line lands true
    const fixed = (el) => { const s = getComputedStyle(el); return ["paddingLeft", "paddingRight", "borderLeftWidth", "borderRightWidth"].reduce((n, k) => n + parseFloat(s[k]), 0); };
    const padT = fixed($("#term")), free = t.width + d.width - padT - fixed(pane);
    let f = null;
    // Live: the chat and the reader follow the pointer, one layout a frame (a long chat re-lays out in ~10 ms); the
    // terminal's refit waits for letting go, and the reader's pages stretch and redraw sharp once it stops (refitDoc)
    let frame = 0;
    const move = (ev) => {
      f = Math.min(0.85, Math.max(0.15, (ev.clientX - g.width / 2 - a - padT) / free));
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; split(f); });
    };
    const up = () => {
      grip.removeEventListener("pointermove", move);
      cancelAnimationFrame(frame);
      grip.classList.remove("drag"); $("#app").classList.remove("resizing");
      if (f === null) return; // a click, not a drag: the split stays
      split(f); fit?.fit();
      try { localStorage.setItem("deck-review-split", f.toFixed(3)); } catch { /* this visit only */ }
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up, { once: true });
    grip.addEventListener("pointercancel", up, { once: true });
  });
  grip.addEventListener("dblclick", () => { split(0.5); try { localStorage.removeItem("deck-review-split"); } catch { /* fine */ } });
}
// Desktop: collapse the Pad column and the keys + message box under the terminal (shared layout).
for (const cls of ["nopad", "nobottom"]) {
  $(`#toggle${cls.slice(2)}`).onclick = () => { saveLayout(cls, !$("#app").classList.contains(cls)); applyLayout(); };
}
// PC: the message box collapses to one line (its toggle sits above its top-right corner; user)
function applyMsgShut() {
  const shut = !!prefs.layout?.msgshut && wide.matches;
  $("#say").classList.toggle("shut", shut);
  $("#msgshut").setAttribute("aria-pressed", shut);
  $("#msgshut").title = shut ? "Expand the message box" : "Collapse the message box";
  fit?.fit();
}
$("#msgshut").onclick = () => { saveLayout("msgshut", !$("#say").classList.contains("shut")); applyMsgShut(); };
applyLayout();
wide.addEventListener("change", applyMsgShut);
wide.addEventListener("change", () => show(view === "screen" ? "screen" : "term"));

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
