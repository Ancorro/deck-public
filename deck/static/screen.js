// Screen tab: the host's desktop over VNC (noVNC). Only connected while the tab is open and toggled on.
// noVNC (about 50 modules, 600 KB) loads the first time it connects, not with every page (user 2026-11-04: "laggy").
// The import map in index.html keeps each module's integrity hash, so the late import is checked the same.
const NOVNC = "https://cdn.jsdelivr.net/npm/@novnc/novnc@1.7.0/core/rfb.js";
let RFB = null;

const $ = (s) => document.querySelector(s);
const KEY = { enter: 0xff0d, backspace: 0xff08, esc: 0xff1b, tab: 0xff09 };
let cfg = null, rfb = null, wanted = false, zoom = 1;
// The desktop shown: this host's or another Deck host's, reached through /peer/<host>/. The picker lists every host
// with a screen; the choice is kept per browser, and with none kept it is this host's, else the first that has one.
let via = "", screens = [];
const at = (path) => (via ? `/peer/${encodeURIComponent(via)}${path}` : path);
const MAX_ZOOM = 8;

async function findScreens() {
  const found = [];
  const mine = await fetch("/api/screen").then((r) => r.json()).catch(() => ({ enabled: false }));
  let hosts = [];
  try { hosts = (await (await fetch("/api/hosts")).json()).hosts; } catch { /* no other hosts */ }
  const me = hosts.find((h) => h.current)?.name || "";
  if (mine.enabled) found.push({ name: me, via: "", cfg: mine });
  const others = await Promise.all(hosts.filter((h) => !h.current).map(async (h) => {
    const c = await fetch(`/peer/${encodeURIComponent(h.name)}/api/screen`).then((r) => r.json()).catch(() => null);
    return c?.enabled ? { name: h.name, via: h.name, cfg: c } : null;
  }));
  return found.concat(others.filter(Boolean));
}

async function init() {
  screens = await findScreens();
  let kept = null;
  try { kept = localStorage.getItem("deck.screenhost"); } catch { /* storage blocked */ }
  const pick = screens.find((x) => x.via === kept) || screens[0];
  cfg = pick ? pick.cfg : { enabled: false };
  via = pick ? pick.via : "";
  const sel = $("#screenhost");
  sel.replaceChildren(...screens.map((x) => new Option(x.name || "this host", x.via, false, x.via === via)));
  sel.hidden = screens.length < 2 && !via;
  sel.disabled = screens.length < 2;
  $("#screentab").hidden = !cfg.enabled; // not set up (or not reachable this way, cfg.why): no tab at all
  if (!cfg.enabled) return;
  try { phone = await (await fetch(at("/api/screen/phone"))).json(); } catch { phone = null; }
  $("#screenphonectl").hidden = !phone;
  try { wantPhone = localStorage.getItem("deck.screenphone") === "1"; } catch { wantPhone = !!phone?.on; }
  $("#screenphone").checked = wantPhone;
  $("#screenwins").hidden = !phone?.on;
  goalRefresh();
}
// The goal row follows the host shown (defined below; a function declaration, so callable from here).
function goalRefresh() { if (typeof renderGoal === "function") { renderGoal(null); pollGoal(); logKey = ""; pollLog(); } }

// ---- phone screen: a portrait virtual monitor on the host, shown alone and filling the frame ----
// The host adds it at the listed size closest to this frame's shape (never taller), then the stream zooms to it.
// The Phone box is a preference: the monitor is attached only while streaming (user: "disable the virtual screens
// whenever not streaming"), and the host detaches it itself if this page stops checking in (closed, phone asleep).
let phone = null, wantPhone = false;
function pickMode() {
  const box = $("#screen"), want = box.clientHeight / box.clientWidth;
  const modes = phone.modes.map((m) => m.split("x").map(Number)).sort((a, b) => a[1] / a[0] - b[1] / b[0]);
  return modes.filter(([w, h]) => h / w <= want + 0.02).pop() || modes[0];
}
async function setPhone(on) {
  $("#screenphone").disabled = true;
  status("…");
  try {
    const [w, h] = on ? pickMode() : [0, 0];
    const r = await fetch(at("/api/screen/phone"), { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ on, w, h }) });
    const res = await r.json();
    if (!r.ok) throw new Error(res.error || r.statusText);
    phone = res;
  } catch (e) {
    window.toast?.(`Phone screen: ${e.message}`);
  } finally {
    $("#screenphone").disabled = false;
    $("#screenphone").checked = wantPhone;
    $("#screenwins").hidden = !phone?.on;
  }
  // The desktop changed size: reconnect so the stream picks up the new layout.
  if (rfb) { disconnect(); connect(); } else status("OFF");
  syncToggle();
}
// Scale the whole desktop so the phone monitor is as wide as the frame (or as tall, if that fits better) and scroll
// to it. Real layout pixels like the zoom, so taps land where they look.
function fitPhone() {
  if (!phone?.on || !rfb) return;
  const fw = rfb._fbWidth, fh = rfb._fbHeight;
  if (!fw || !fh) { setTimeout(fitPhone, 200); return; }
  const box = $("#screen"), inner = $("#screenvnc");
  const s = Math.min(box.clientWidth / phone.w, box.clientHeight / phone.h);
  zoom = fw * s / box.clientWidth;
  inner.style.width = `${fw * s}px`; inner.style.height = `${fh * s}px`;
  box.scrollLeft = (phone.x - phone.ox) * s - (box.clientWidth - phone.w * s) / 2;
  box.scrollTop = (phone.y - phone.oy) * s - (box.clientHeight - phone.h * s) / 2;
  applyZoom();
}
window.addEventListener("resize", () => fitPhone());

// Windows don't follow the phone screen when it appears: this list moves them onto it (maximized) and back.
function listWins(wins) {
  const rows = wins.map((w) => {
    const b = document.createElement("button");
    b.className = "hostrow";
    b.append(Object.assign(document.createElement("b"), { textContent: w.title }),
      Object.assign(document.createElement("span"), { textContent: w.app }));
    if (w.phone) b.append(Object.assign(document.createElement("em"), { textContent: "on phone" }));
    b.onclick = () => moveWin(w);
    return b;
  });
  $("#winlist").replaceChildren(...rows);
  if (!rows.length) $("#winlist").textContent = "No windows open.";
}
async function moveWin(w) {
  for (const b of $("#winlist").children) b.disabled = true;
  try {
    const r = await fetch(at("/api/screen/windows"), { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hwnd: w.hwnd, phone: !w.phone }) });
    const res = await r.json();
    if (!r.ok) throw new Error(res.error || r.statusText);
    if (!w.phone) { $("#wins").close(); return; }  // on the phone screen now: show it
    listWins(res);
  } catch (e) {
    window.toast?.(`Move window: ${e.message}`);
    for (const b of $("#winlist").children) b.disabled = false;
  }
}
$("#screenwins").onclick = async () => {
  $("#winlist").textContent = "Loading…";
  $("#wins").showModal();
  try { listWins(await (await fetch(at("/api/screen/windows"))).json()); }
  catch (e) { $("#winlist").textContent = `Couldn't list windows: ${e.message}`; }
};
$("#winsclose").onclick = () => $("#wins").close();

// Another host's desktop: stop this one (and its phone screen), then show that one, streaming again if this was.
$("#screenhost").onchange = async (e) => {
  const streaming = !!rfb;
  stopStream();
  try { localStorage.setItem("deck.screenhost", e.target.value); } catch { /* storage blocked */ }
  const pick = screens.find((x) => x.via === e.target.value);
  if (!pick) return;
  cfg = pick.cfg; via = pick.via;
  try { phone = await (await fetch(at("/api/screen/phone"))).json(); } catch { phone = null; }
  $("#screenphonectl").hidden = !phone;
  $("#screenwins").hidden = !phone?.on;
  goalRefresh();
  if (streaming) startStream();
};

function status(text) { $("#screenstatus").textContent = text; }

async function connect() {
  if (rfb || !cfg?.enabled) return;
  if (!RFB) {
    status("…");
    const asked = stopped;
    try { RFB = (await import(NOVNC)).default; } catch { status("NO VNC"); return; }
    if (rfb || stopped !== asked) return; // connected meanwhile, or stopped while it loaded
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const me = rfb = new RFB($("#screenvnc"), `${proto}://${location.host}${at(cfg.path)}`, { credentials: { password: cfg.password } });
  rfb.viewOnly = !$("#screencontrol").checked;
  applyZoom();
  rfb.addEventListener("connect", () => { status("LIVE"); fitPhone(); });
  // A reconnect (phone screen on/off) replaces rfb before the old one reports closing: only the current one counts.
  rfb.addEventListener("disconnect", (e) => { if (rfb !== me) return; rfb = null; status(e.detail.clean ? "OFF" : "LOST"); syncToggle(); });
  rfb.addEventListener("credentialsrequired", () => status("AUTH?"));
  status("…");
}

let stopped = 0; // counts disconnects: a connect still loading noVNC gives up if one came meanwhile
function disconnect() {
  stopped++;
  // rfb is cleared first, so this connection's own disconnect event is ignored above: the status is set here.
  if (rfb) { rfb.disconnect(); status("OFF"); }
  rfb = null;
}

// Stream start and stop, with the phone screen attached only in between.
async function startStream() {
  if (wantPhone && phone && !phone.on) await setPhone(true);  // connects when done
  connect();
}
function stopStream() {
  const streaming = !!rfb;  // only the page that streams detaches it: another device may be using it
  disconnect();
  if (streaming && phone?.on) setPhone(false);
}
setInterval(() => {
  if (rfb && phone?.on) fetch(at("/api/screen/phone"), { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep: true }) }).catch(() => {});
}, 60000);
window.addEventListener("pagehide", () => {
  if (rfb && phone?.on) fetch(at("/api/screen/phone"), { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ on: false }), keepalive: true }).catch(() => {});
});

function syncToggle() {
  $("#screentoggle").textContent = rfb ? "Stop" : "Stream";
  $("#screentoggle").className = rfb ? "ghost" : "primary";
  $("#view-screen").classList.toggle("live", !!rfb);  // live: the floating bar fades while the pointer is away
}

// Zoom: noVNC always fits the desktop to #screenvnc, which is `zoom` times the size of #screen; #screen scrolls over
// it. Real pixels, no CSS transform, so clicks in Control mode still land where they look. Pinch to zoom and drag to
// pan with Control off (with it on, touches drive the desktop); the Zoom button jumps between Fit and real size.
function applyZoom() {
  $("#screenzoom").textContent = zoom > 1 ? "Fit" : "Zoom";
  if (rfb) Object.assign(rfb, { scaleViewport: true, clipViewport: false, dragViewport: false });
}

function setZoom(z, mx, my) {
  const box = $("#screen"), inner = $("#screenvnc");
  z = Math.min(MAX_ZOOM, Math.max(1, z));
  // Keep the point under (mx, my) (relative to #screen) still while the size changes.
  const fx = (box.scrollLeft + mx) / zoom, fy = (box.scrollTop + my) / zoom;
  zoom = z;
  inner.style.width = inner.style.height = z === 1 ? "" : `${z * 100}%`;
  box.scrollLeft = fx * z - mx;
  box.scrollTop = fy * z - my;
  applyZoom();
}

function realSize() {
  const box = $("#screen"), w = rfb?._fbWidth, h = rfb?._fbHeight;
  return w && h ? Math.max(w / box.clientWidth, h / box.clientHeight) : 3;
}

// With Control off, touches are taken in the capture phase, before noVNC. With Control on they all go to the
// desktop and the zoom stays where it was.
let gesture = null;
const pts = (t) => [...t].map((p) => ({ x: p.clientX, y: p.clientY }));
function local(p) { const r = $("#screen").getBoundingClientRect(); return { x: p.x - r.left, y: p.y - r.top }; }
function mid(a, b) { return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }; }
function ours() { return !rfb || rfb.viewOnly; }

function touchStart(ev) {
  if (!ours()) return;
  ev.stopPropagation(); ev.preventDefault();
  const p = pts(ev.touches);
  gesture = p.length >= 2
    ? { pinch: true, d: Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y), z: zoom, at: mid(p[0], p[1]) }
    : { pinch: false, at: p[0] };
}
function touchMove(ev) {
  if (!gesture) return;
  ev.stopPropagation(); ev.preventDefault();
  const p = pts(ev.touches), box = $("#screen");
  if (gesture.pinch && p.length >= 2) {
    const m = mid(p[0], p[1]), d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
    box.scrollLeft -= m.x - gesture.at.x; box.scrollTop -= m.y - gesture.at.y;  // two fingers also pan
    const l = local(m);
    setZoom(gesture.z * d / gesture.d, l.x, l.y);
    gesture.at = m;
  } else if (!gesture.pinch) {
    box.scrollLeft -= p[0].x - gesture.at.x; box.scrollTop -= p[0].y - gesture.at.y;
    gesture.at = p[0];
  }
}
function touchEnd(ev) {
  if (!gesture) return;
  ev.stopPropagation(); ev.preventDefault();
  if (ev.touches.length === 0) gesture = null;
  else if (gesture.pinch && ev.touches.length === 1) gesture = { pinch: false, at: pts(ev.touches)[0] };
}
const screenBox = $("#screen");
screenBox.addEventListener("touchstart", touchStart, { capture: true, passive: false });
screenBox.addEventListener("touchmove", touchMove, { capture: true, passive: false });
screenBox.addEventListener("touchend", touchEnd, { capture: true, passive: false });
screenBox.addEventListener("touchcancel", touchEnd, { capture: true, passive: false });

// Called by the page (02-prefs.js show) on every tab change: stream only while the Screen (over the chat) is visible.
window.deckScreen = {
  // A Computer session's host: show that host's desktop (the picker follows), streaming when it opens (start)
  async follow(host, start) {
    await ready;
    const pick = screens.find((x) => (x.via || window.deckPage?.home) === host);
    if (!pick) return;
    if (pick.via !== via) {
      const streaming = !!rfb;
      stopStream();
      cfg = pick.cfg; via = pick.via;
      $("#screenhost").value = via;
      try { phone = await (await fetch(at("/api/screen/phone"))).json(); } catch { phone = null; }
      $("#screenphonectl").hidden = !phone;
      $("#screenwins").hidden = !phone?.on;
      goalRefresh();
      if (streaming) start = true;
    }
    if (start && !rfb) { wanted = true; startStream(); }
    sayTo = DESK; renderTo();  // the box under the screen types on the desktop; the chat below messages the agent
    syncToggle();
  },
  onShow(view) {
    if (view === "screen") { renderTo(); pollGoal(); }
    if (view === "screen" && wanted && !rfb) startStream();
    if (view !== "screen") stopStream();
    syncToggle();
  },
};

$("#screentoggle").onclick = () => {
  wanted = !rfb;
  wanted ? startStream() : stopStream();
  syncToggle();
};
$("#screenphone").onchange = (e) => {
  wantPhone = e.target.checked;
  try { localStorage.setItem("deck.screenphone", wantPhone ? "1" : "0"); } catch { /* per-device convenience only */ }
  if (rfb) setPhone(wantPhone);  // not streaming: applied at the next Stream
};
// Chat: hide the chat's history under the screen, keeping its message box (remembered per browser)
function setSolo(solo) {
  $("#app").classList.toggle("scrsolo", solo);
  $("#screenchat").setAttribute("aria-pressed", String(!solo));
  $("#screenchat").className = solo ? "ghost" : "primary";
  try { localStorage.setItem("deck.screensolo", solo ? "1" : "0"); } catch { /* per-device convenience only */ }
}
try { setSolo(localStorage.getItem("deck.screensolo") === "1"); } catch { setSolo(false); }
$("#screenchat").onclick = () => setSolo(!$("#app").classList.contains("scrsolo"));
$("#screencontrol").onchange = (e) => { if (rfb) { rfb.viewOnly = !e.target.checked; applyZoom(); } };
$("#screenzoom").onclick = () => {
  const box = $("#screen");
  setZoom(zoom > 1 ? 1 : realSize(), box.clientWidth / 2, box.clientHeight / 2);
};

// ---- the goal row: a goal for the host's computer-use loop, paused (the desktop is the user's meanwhile), resumed or
// stopped between its steps (deck/screen.py goal_*). Shown when the host has a loop (computer.json "goal"). ----
var goalSt = null;  // var: init() may render before this line runs
async function goalCall(body) {
  const r = await fetch(at("/api/screen/goal"), body ? { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) } : {});
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { window.toast?.(j.error || "goal: failed"); return null; }
  return j;
}
var GOAL_LABEL = { starting: "STARTING", running: "RUNNING", pausing: "PAUSING…", paused: "PAUSED", stopping: "STOPPING…",
  stopped: "STOPPED", done: "DONE", fail: "FAILED", ended: "ENDED", "out of steps": "OUT OF STEPS", idle: "" };
function renderGoal(st) {
  goalSt = st;
  const row = $("#screengoal");
  row.hidden = !st?.available;
  if (!st?.available) return;
  const live = st.running, paused = st.state === "paused" || st.state === "pausing";
  $("#goalrun").hidden = live;
  $("#goaltext").disabled = live;
  if (live && document.activeElement !== $("#goaltext")) $("#goaltext").value = st.goal;
  $("#goalpause").hidden = !live || st.state === "stopping";
  $("#goalpause").textContent = paused ? "Resume" : "Pause";
  $("#goalstop").hidden = !live || st.state === "stopping";
  const label = GOAL_LABEL[st.state] ?? st.state.toUpperCase();
  const step = st.steps ? ` ${st.step}/${st.steps}` : "";
  const cost = st.cost ? ` · $${Number(st.cost.total || 0).toFixed(3)}` : "";
  $("#goalstate").textContent = label ? `${label}${step}${cost}${st.note ? ` · ${st.note}` : ""}` : "";
  $("#goalstate").title = (st.cost ? `Cost so far: vision $${Number(st.cost.vision || 0).toFixed(4)}, plan $${Number(st.cost.plan || 0).toFixed(4)}, fast $${Number(st.cost.fast || 0).toFixed(4)} (Claude Code's API-price estimate)\n` : "") + (st.note || "");
  $("#goalstate").classList.toggle("alert", paused);
}
async function pollGoal() {
  if (!cfg?.enabled || $("#view-screen").hidden) return;
  renderGoal(await goalCall().catch(() => null));
}
$("#screengoal").onsubmit = async (e) => {
  e.preventDefault();
  const goal = $("#goaltext").value.trim();
  if (!goal) return;
  const st = await goalCall({ goal });
  if (st) renderGoal(st);
};
$("#goalpause").onclick = async () => {
  const paused = goalSt && (goalSt.state === "paused" || goalSt.state === "pausing");
  const st = await goalCall({ action: paused ? "resume" : "pause" });
  if (st) renderGoal(st);
};
$("#goalstop").onclick = async () => { const st = await goalCall({ action: "stop" }); if (st) renderGoal(st); };
setInterval(pollGoal, 2000);

// ---- Screen settings: the paid model, effort, the fast model, steps (deck/screen.py goal_settings) ----
const MODEL_HINTS = { "claude-code": ["sonnet", "haiku", "opus"], codex: ["gpt-6-luna", "gpt-6"], opencode: ["opencode/mimo-v2.6-flash-free"] };
function fillModels(api) {
  $("#ssmodels").replaceChildren(...(MODEL_HINTS[api] || []).map((m) => new Option(m, m)));
}
$("#screenset").onclick = async () => {
  const st = await fetch(at("/api/screen/goal/settings")).then((r) => r.json()).catch(() => null);
  if (!st) { window.toast?.("Screen settings: not reachable"); return; }
  $("#sshost").textContent = via || "";
  $("#ssapi").replaceChildren(...st.apis.map((a) => new Option(a, a, false, a === st.api)));
  $("#sseffort").replaceChildren(...st.efforts.map((e) => new Option(e || "the CLI's default", e, false, e === st.effort)));
  $("#ssmodel").value = st.model;
  fillModels(st.api);
  $("#ssfast").checked = !!st.fast;
  $("#ssicons").checked = !!st.icons;
  $("#ssiconsrow").hidden = !st.icons_available;
  $("#sssteps").value = st.steps;
  $("#screensettings").showModal();
};
$("#ssapi").onchange = (e) => { fillModels(e.target.value); $("#ssmodel").value = MODEL_HINTS[e.target.value]?.[0] || ""; };
$("#ssclose").onclick = () => $("#screensettings").close();
$("#sssave").onclick = async () => {
  const r = await fetch(at("/api/screen/goal/settings"), { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api: $("#ssapi").value, model: $("#ssmodel").value.trim(), effort: $("#sseffort").value,
      fast: $("#ssfast").checked, icons: $("#ssicons").checked, steps: Number($("#sssteps").value) || 40 }) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { window.toast?.(j.error || "Screen settings: not saved"); return; }
  $("#screensettings").close();
  window.toast?.(`Next run: ${j.model || "default"} via ${j.api}${j.fast ? ", SubQ first" : ""}${j.icons && j.icons_available ? ", icons" : ""}, ${j.steps} steps`);
};

// View log: the run's steps (deck/screen.py goal_log) in the shelf's place, refreshed while open and a run is live.
var logOpen = false, logKey = "", openCalls = new Set();  // var: goalRefresh may run first
function showLog(on) {
  logOpen = on;
  $("#goallog").hidden = !on;
  document.body.classList.toggle("goallog", on);
  $("#goallogbtn").setAttribute("aria-pressed", on ? "true" : "false");
  try { localStorage.setItem("deck.goallog", on ? "1" : "0"); } catch { /* per-browser only */ }
  if (on) { logKey = ""; pollLog(); }
}
async function pollLog() {
  if (!logOpen) return;
  const rows = await fetch(at("/api/screen/goal/log")).then((r) => r.json()).catch(() => null);
  if (!rows) return;
  $("#goallogstate").textContent = $("#goalstate").textContent.split(" · ")[0];
  const key = JSON.stringify(rows);
  if (key === logKey) return;
  logKey = key;
  const list = $("#goalloglist"), atEnd = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  const clock = (t) => (t ? new Date(Number(t) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "");
  const el = (tag, cls, text) => { const e = document.createElement(tag); e.className = cls; e.textContent = text; return e; };
  const pretty = (v) => (typeof v === "string" ? v : Array.isArray(v) ? v.join("\n") : JSON.stringify(v, null, 2));
  // A model call (the loop's "call" event): model, seconds and the reply's gist; opened, everything it was sent and
  // answered, with the screenshot the vision model saw. Kept open across refreshes.
  const call = (r) => {
    const d = document.createElement("details");
    d.className = `glcall gl${r.by}`;
    d.dataset.n = r.n;
    if (openCalls.has(String(r.n))) d.open = true;
    d.addEventListener("toggle", () => (d.open ? openCalls.add(String(r.n)) : openCalls.delete(String(r.n))));
    const rep = r.reply || {};
    const gist = r.by === "plan" ? `plan: ${rep.goal || ""}` : r.by === "fast" ? `${rep.kind}${rep.item != null ? ` #${rep.item}` : ""} (${rep.confidence})`
      : `${rep.action || ""}${rep.item != null && rep.item !== "" ? ` #${rep.item}` : ""}${rep.text ? `: ${String(rep.text).slice(0, 80)}` : ""}`;
    const sum = document.createElement("summary");
    const u = r.usage || {};
    const toks = u.input_tokens != null ? ` · ${(u.input_tokens || 0) + (u.cache_read_tokens || 0) + (u.cache_write_tokens || 0)} in / ${u.output_tokens || 0} out` : "";
    const usd = u.cost_usd != null ? ` · $${Number(u.cost_usd).toFixed(4)}` : "";
    sum.append(el("span", "glhead", `${clock(r.t)} · call ${r.n} · ${r.by} · ${r.model} · ${r.seconds}s${toks}${usd}`), el("div", "glact", gist));
    d.append(sum);
    d.addEventListener("toggle", () => {
      if (!d.open || d.querySelector(".glbody")) return;
      const body = el("div", "glbody", "");
      if (r.image) {
        const img = document.createElement("img");
        img.src = at(`/api/screen/goal/image/${r.image}`); img.loading = "lazy"; img.alt = "What the model saw";
        img.onclick = () => window.open(img.src, "_blank", "noopener");
        body.append(img);
      }
      body.append(el("div", "gllabel", "Reply"), el("pre", "glpre", pretty(rep)));
      for (const [k, v] of Object.entries(r.sent || {})) {
        if (v == null || v === "" || (Array.isArray(v) && !v.length)) continue;
        body.append(el("div", "gllabel", `Sent: ${k}`), el("pre", "glpre", pretty(v)));
      }
      d.append(body);
    });
    if (d.open) d.dispatchEvent(new Event("toggle"));
    return d;
  };
  list.replaceChildren(...rows.map((r) => {
    if (r.kind === "call") return call(r);
    const box = el("div", `glstep${r.kind === "end" ? " glend" : ""}`, "");
    if (r.kind === "plan") {
      box.classList.add("glplan");
      box.append(el("div", "glhead", `${clock(r.t)} · plan`), el("div", "glnote", r.goal));
      const ol = document.createElement("ol");
      for (const step of r.steps) ol.append(el("li", "", step));
      box.append(ol);
      for (const [name, list] of [["Missing", r.missing]]) {
        if (list?.length) box.append(el("div", `gllabel${name === "Missing" ? " glmissing" : ""}`, name), el("div", "glnote", list.join(" · ")));
      }
      return box;
    }
    if (r.kind === "start") { box.append(el("div", "glhead", `${clock(r.t)} · start`), el("div", "glnote", r.note)); return box; }
    if (r.kind === "end") { box.append(el("div", "glhead", `${clock(r.t)} · end: ${r.result}`), el("div", "glnote", r.note)); return box; }
    box.append(el("div", "glhead", `${clock(r.t)} · step ${r.step} · ${r.by}`),
      el("div", "glact", `${r.action}${r.text ? `: ${r.text}` : ""}${r.result ? `  → ${r.result}` : ""}`));
    if (r.note) box.append(el("div", "glnote", r.note));
    return box;
  }));
  if (atEnd) list.scrollTop = list.scrollHeight;
}
$("#goallogbtn").onclick = () => showLog(!logOpen);
$("#goallogclose").onclick = () => showLog(false);
setInterval(() => { if (logOpen && (goalSt?.running || !logKey)) pollLog(); }, 2000);
try { if (localStorage.getItem("deck.goallog") === "1") showLog(true); } catch { /* storage blocked */ }

// The box under the screen sends to what the picker beside it names (user 2026-10-31: "message a session under the
// screen view"): the desktop (typed through sendKey, since phone keyboards can't type into the VNC canvas), any running
// session on any host, or a new Claude session started with the message. The page's own script (app.js) lends its
// session list and calls (window.deckPage); the choice is kept per browser, else the open tab's session.
const DESK = "", NEW = "+new";
let sayTo = DESK, fresh = "";  // fresh: a session just started here, not in the page's list until its next poll
try { sayTo = localStorage.getItem("deck.screento") ?? (window.deckPage?.attached() || DESK); } catch { /* storage blocked */ }
const sessName = (s) => `${s.title || s.label}${s.host && s.host !== window.deckPage?.home ? ` · ${s.host}` : ""}`;
function renderTo() {
  const live = (window.deckPage?.sessions() || []).filter((s) => s.running && !s.offline);
  if (live.some((s) => s.name === fresh)) fresh = "";
  if (sayTo !== DESK && sayTo !== NEW && sayTo !== fresh && !live.some((s) => s.name === sayTo)) sayTo = DESK;  // it ended
  if (fresh && sayTo === fresh && !live.some((s) => s.name === fresh)) live.push({ name: fresh, label: "New Claude session" });
  const sel = $("#screento"), key = [sayTo, ...live.map((s) => `${s.name}\t${sessName(s)}`)].join("\n");
  if (sel.dataset.key !== key) {
    sel.dataset.key = key;
    sel.replaceChildren(new Option("Desktop", DESK, false, sayTo === DESK),
      ...live.map((s) => new Option(sessName(s), s.name, false, s.name === sayTo)),
      new Option("+ New Claude session", NEW, false, sayTo === NEW));
  }
  $("#screentext").placeholder = sayTo === DESK ? "Type on the desktop…" : sayTo === NEW ? "Start Claude with…" : "Message the session…";
}
$("#screento").onchange = (e) => {
  sayTo = e.target.value;
  try { localStorage.setItem("deck.screento", sayTo); } catch { /* per-device convenience only */ }
  renderTo();
};
setInterval(() => { if (!$("#view-screen").hidden) renderTo(); }, 2000);
const VIA = window.Capacitor ? "phone app" : matchMedia("(pointer: coarse)").matches ? "phone browser" : "PC browser";
$("#screensay").onsubmit = async (ev) => {
  ev.preventDefault();
  const box = $("#screentext"), text = box.value;
  if (sayTo === DESK) {
    if (!rfb) return;
    for (const ch of text) {
      const cp = ch.codePointAt(0);
      rfb.sendKey(cp < 0x100 ? cp : 0x01000000 + cp);
    }
    box.value = "";
    return;
  }
  if (!text.trim()) return;
  box.value = "";
  try {
    if (sayTo === NEW) {
      const { session } = await window.deckPage.api("POST", "/api/agent", { agent: "claude", text });
      sayTo = fresh = window.deckPage.fullName(session);
      try { localStorage.setItem("deck.screento", sayTo); } catch { /* per-device convenience only */ }
      window.toast?.("Started a Claude session");
    } else {
      await window.deckPage.api("POST", `/api/sessions/${encodeURIComponent(sayTo)}/send`, { text, enter: true, via: VIA });
      window.toast?.("Sent");
    }
  } catch (e) { box.value = text; window.toast?.(`Not sent: ${e.message}`); }
  window.deckPage?.poll();
  renderTo();
};
for (const b of document.querySelectorAll("#screenkeys button[data-sk]")) {
  b.onclick = () => rfb && rfb.sendKey(KEY[b.dataset.sk]);
}

const ready = init();
