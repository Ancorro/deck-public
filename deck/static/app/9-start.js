// ---- phone app: the phone carries the notes from host to host ----
// Usually only one host is up at a time, but the same phone visits them all. On every open the app hands this host
// its copy (git bundle) plus edits queued offline (in the app's host list → Notes), and keeps what comes back.
// Stored in the app's own files (Capacitor Filesystem), shared by every host's page and the host list.
const Fs = window.Capacitor?.Plugins?.Filesystem;
const fsRead = async (path) => {
  try { return (await Fs.readFile({ path, directory: "DATA", encoding: "utf8" })).data; } catch { return null; }
};
const fsWrite = (path, data) => Fs.writeFile({ path, data, directory: "DATA", encoding: "utf8", recursive: true });
// The app's own settings (app/www app.json: failover), shown in Settings → Advanced in the app only (user)
if (Fs) {
  const box = $("#setfailover"), sw = box.querySelector("input");
  box.hidden = false;
  fsRead("app.json").then((raw) => { sw.checked = (JSON.parse(raw || "{}").failover) !== false; });
  sw.onchange = async () => {
    const prefs = JSON.parse((await fsRead("app.json")) || "{}");
    prefs.failover = sw.checked;
    await fsWrite("app.json", JSON.stringify(prefs));
  };
}
async function carryNotes() {
  if (!Fs) return;
  try {
    const edits = JSON.parse((await fsRead("notes/edits.json")) || "[]");
    const r = await api("POST", "/api/notes/carry", { bundle: await fsRead("notes/bundle.b64"), edits, device: "phone" });
    await fsWrite("notes/bundle.b64", r.bundle);
    await fsWrite("notes/copy.json", JSON.stringify({ head: r.head, host: r.host, at: Date.now(), files: r.files }));
    // Applied edits leave the queue; ones the host refused stay, marked, until redone in the host list's Notes.
    const now = JSON.parse((await fsRead("notes/edits.json")) || "[]"); // re-read: keeps edits made meanwhile
    const sent = new Set(edits.map((e) => e.path + "\n" + e.base));
    const left = now.filter((e) => !(sent.has(e.path + "\n" + e.base) && r.applied.includes(e.path)))
      .map((e) => r.conflicts.includes(e.path) && sent.has(e.path + "\n" + e.base) ? { ...e, conflict: true } : e);
    await fsWrite("notes/edits.json", JSON.stringify(left));
    if (r.phone_had) toast(`Notes: brought ${r.phone_had} change${r.phone_had > 1 ? "s" : ""} from other hosts to ${r.host}`);
    if (r.applied.length) toast(`Notes: saved ${r.applied.length} offline edit${r.applied.length > 1 ? "s" : ""}`);
    if (r.conflicts.length) toast(`Notes: ${r.conflicts.join(", ")} changed on ${r.host} since your offline edit; redo it in the host list → Notes`);

    for (const p of r.problems) toast(`Notes: ${p}`);
  } catch (e) { console.warn("notes carry", e); }
}
carryNotes();
// The phone app lists the Deck as one entry (user: no separate host sites): every open tells it the Deck's name and
// its hosts' addresses and iroh keys, which it keeps (deck.json) and tries in turn (a key works with no Tailscale).
// Linked PCs' addresses are this host's own forwarders, useless from the phone: they go by key alone.
async function carryDeck() {
  if (!Fs) return;
  try {
    const info = await api("GET", "/api/hosts");
    const hosts = info.hosts.map((x) => {
      const url = !x.linked && /^https:\/\//.test(x.url) ? new URL(x.url).origin : x.iroh ? `iroh:${x.iroh}` : "";
      return url && { name: x.name, url, ...(x.iroh ? { key: x.iroh } : {}) };
    }).filter(Boolean);
    let order = [];
    try { order = (await api("GET", "/api/site")).order || []; } catch { /* an older host */ }
    await fsWrite("deck.json", JSON.stringify({ name: info.alias || info.current, hosts, order }));
  } catch (e) { console.warn("deck carry", e); }
}
carryDeck();
// Phone app: Back on this page closes an open dialog, else leaves the app; its host list is Settings → More → Hosts.

setInterval(() => { if (document.visibilityState === "visible") poll(); }, 2000);
// Run clocks tick every second between polls (polls alone made them jump 2s at a time).
setInterval(() => { for (const el of document.querySelectorAll("[data-clock]")) el.textContent = clock(+el.dataset.clock); }, 1000);
let startingTerminal = true;
function resumePage() {
  if (startingTerminal) return;
  if (document.visibilityState !== "visible") {
    // A hidden page (PC app in the tray, phone app in the background) lets go of its terminal: each terminal is a tmux
    // client, and tmux sizes the window to the latest one, so an idle hidden one at another size garbles the visible one.
    if (ws && !chatView) { ws.onclose = null; ws.close(); ws = null; } // a chat keeps its connection: no tmux client
    return;
  }
  poll();
  if (view === "term" && attached && chatView?.name === attached) {
    // a chat: a fresh connection (a suspended phone's may claim OPEN, dead) that sends only what's new, drawn on top
    const v = chatView;
    if (v.socket) { v.socket.onclose = null; v.socket.close(); v.socket = null; ws = null; }
    connectChat(attached);
  } else if (view === "term" && attached) {
    // A suspended mobile socket may still claim OPEN even after the network died.
    detach();
    restoreTerminal();
  }
}
document.addEventListener("visibilitychange", resumePage);
window.addEventListener("pageshow", resumePage);
window.addEventListener("online", resumePage);
// Landing view from Settings (default Terminal); restore the saved session only after validation.
(async () => {
  await prefsReady; // the host's open session and tab order, not this page's cached copy
  const oldSession = localStorage.getItem("deck-session");
  try { // once: this browser's state from before it moved to the host; kept until a host that stores it has it
    const moved = {}, l = {}, tabs = JSON.parse(localStorage.getItem("deck-tabs") || "null");
    if (tabs && !prefs.tabs) moved.tabs = tabs;
    if (oldSession && !prefs[sessionKey()]) moved[sessionKey()] = oldSession;
    const cols = { padw: 0, padcw: 0, feedcw: 0, nopad: 1, nobottom: 1 };
    for (const [k, flag] of Object.entries(cols)) {
      const v = localStorage.getItem(`deck-${k}`);
      if (flag ? v === "1" : +v) l[k] = flag ? true : +v;
    }
    if (Object.keys(l).length && !prefs.layout) moved.layout = l;
    if (Object.keys(moved).length) {
      const saved = await api("PUT", "/api/settings", moved);
      if (!Object.keys(moved).every((k) => k in saved)) throw new Error("this host doesn't store page state yet");
      saveState(moved);
      tabOrder = prefs.tabs || [];
      applyLayout();
    }
    for (const k of ["session", "tabs", "textterm", ...Object.keys(cols)]) localStorage.removeItem(`deck-${k}`);
  } catch { /* try again next time */ }
  attached = fullName(prefs[sessionKey()] || oldSession || null);
  useDraft(attached);
  show("term", false);
  try {
    if (attached) await restoreTerminal();
    else await attachNewest();
  } finally { startingTerminal = false; }
})();
