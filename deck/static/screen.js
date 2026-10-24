// Screen tab: the host's desktop over VNC (noVNC). Only connected while the tab is open and toggled on.
import RFB from "https://cdn.jsdelivr.net/npm/@novnc/novnc@1.7.0/core/rfb.js";

const $ = (s) => document.querySelector(s);
const KEY = { enter: 0xff0d, backspace: 0xff08, esc: 0xff1b, tab: 0xff09 };
let cfg = null, rfb = null, wanted = false, zoom = 1;
// The desktop shown: this host's, or (none here) the first other Deck host that has one, reached through /peer/<host>/
let via = "";
const at = (path) => (via ? `/peer/${encodeURIComponent(via)}${path}` : path);
const MAX_ZOOM = 8;

async function init() {
  try { cfg = await (await fetch("/api/screen")).json(); } catch { cfg = { enabled: false }; }
  if (!cfg.enabled) {
    try {
      const { hosts } = await (await fetch("/api/hosts")).json();
      for (const h of hosts.filter((x) => !x.current)) {
        const c = await fetch(`/peer/${encodeURIComponent(h.name)}/api/screen`).then((r) => r.json()).catch(() => null);
        if (c?.enabled) { cfg = c; via = h.name; break; }
      }
    } catch { /* no other hosts */ }
  }
  $("#screentab").hidden = !cfg.enabled; // not set up (or not reachable this way, cfg.why): no tab at all
  $("#screenhost").textContent = via;
  $("#screenhost").hidden = !via;
  if (!cfg.enabled) return;
  try { phone = await (await fetch(at("/api/screen/phone"))).json(); } catch { phone = null; }
  $("#screenphonectl").hidden = !phone;
  $("#screenphone").checked = !!phone?.on;
  $("#screenwins").hidden = !phone?.on;
}

// ---- phone screen: a portrait virtual monitor on the host, shown alone and filling the frame ----
// The host adds it at the listed size closest to this frame's shape (never taller), then the stream zooms to it.
let phone = null;
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
    $("#screenphone").checked = !!phone?.on;
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

function status(text) { $("#screenstatus").textContent = text; }

function connect() {
  if (rfb || !cfg?.enabled) return;
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

function disconnect() {
  if (rfb) rfb.disconnect();
  rfb = null;
}

function syncToggle() {
  $("#screentoggle").textContent = rfb ? "Stop" : "Stream";
  $("#screentoggle").className = rfb ? "ghost" : "primary";
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

// Called by app.js on every tab change: stream only while the Screen tab is visible.
window.deckScreen = {
  onShow(view) {
    if (view === "screen" && wanted) connect();
    if (view !== "screen") disconnect();
    syncToggle();
  },
};

$("#screentoggle").onclick = () => {
  wanted = !rfb;
  wanted ? connect() : disconnect();
  syncToggle();
};
$("#screenphone").onchange = (e) => setPhone(e.target.checked);
$("#screencontrol").onchange = (e) => { if (rfb) { rfb.viewOnly = !e.target.checked; applyZoom(); } };
$("#screenzoom").onclick = () => {
  const box = $("#screen");
  setZoom(zoom > 1 ? 1 : realSize(), box.clientWidth / 2, box.clientHeight / 2);
};

// Phone keyboards can't type into the VNC canvas, so typed text goes through sendKey.
$("#screensay").onsubmit = (ev) => {
  ev.preventDefault();
  if (!rfb) return;
  for (const ch of $("#screentext").value) {
    const cp = ch.codePointAt(0);
    rfb.sendKey(cp < 0x100 ? cp : 0x01000000 + cp);
  }
  $("#screentext").value = "";
};
for (const b of document.querySelectorAll("#screenkeys button[data-sk]")) {
  b.onclick = () => rfb && rfb.sendKey(KEY[b.dataset.sk]);
}

init();
