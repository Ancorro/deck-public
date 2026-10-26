// ---- host switcher ----
// The host's colour fills the accent (your messages, Send): text on it dark when the colour is light (user: "text on
// light color not legible"), by WCAG relative luminance (0.18: where dark and light text read about equally)
function setPower(c) {
  const root = document.documentElement.style, m = /^#([0-9a-f]{6})$/i.exec(c || "");
  root.setProperty("--power", c);
  if (!m) return root.removeProperty("--on-accent");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255).map((v) => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  root.setProperty("--on-accent", 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.18 ? "#121311" : "#fff6f0");
}
let hostList = [], hostCurrent = "", deckName = "", palette = [];
async function loadHosts() {
  try {
    const info = await api("GET", "/api/hosts");
    const { current, hosts } = info;
    const before = hostList.map((x) => x.name).join();
    hostList = hosts; hostCurrent = current; deckName = info.alias || ""; palette = info.palette || [];
    if (hostList.map((x) => x.name).join() !== before) loadTabTrash(); // the shared trash takes in every host's
    loadSite();
    padOrder = info.pad || []; renderPad();
    if (info.led) led = info.led;
    setPower(led);
    if (term) term.options.theme = termTheme();
    // The Deck's name is the whole network's (user): every host shows it, and the tab column names the hosts.
    $("#hostname").textContent = deckName || current;
    document.title = `${deckName || current} deck`;
    try { localStorage.setItem("deck-name", JSON.stringify({ name: deckName || current, led })); } catch { /* private mode */ }
  } catch { /* header keeps its placeholder */ }
}
// The last name and LED colour seen, until /api/hosts answers (an iroh page's first load takes seconds): no "…"
try {
  const seen = JSON.parse(localStorage.getItem("deck-name") || "null");
  if (seen) { $("#hostname").textContent = seen.name; setPower(seen.led); }
} catch { /* first visit */ }
$("#hostbtn").onclick = () => openSettings(); // the name opens Settings

// ---- settings: one page; every control saves itself as it changes ----
// Settings → Deck → Hosts: every host's name and LED colour, set from any host (user: alias the hosts, change their
// colour). Shared by the whole Deck (core.save_settings hostnames); the machine's own name stays underneath.
const hostLabel = (name) => hostList.find((x) => x.name === name)?.aka || name;
function renderSwatches() {
  const save = (name, alias, colour) => saveSetting({ hostnames: { [name]: { alias, led: colour } } });
  $("#sethosts").replaceChildren(...hostList.map((x) => {
    const alias = h("input", { value: x.aka || "", placeholder: x.name, maxlength: "24", autocomplete: "off", "aria-label": `${x.name}'s name` });
    const pick = h("input", { type: "color", value: x.led, "aria-label": `${x.name}'s colour` });
    const wheel = h("label", { class: "wheel", title: "Colour", style: `--c: ${x.led}` }, pick);
    alias.onchange = () => save(x.name, alias.value, pick.value);
    pick.onchange = () => save(x.name, alias.value, pick.value); // change, not input: one save per pick
    return h("div", { class: "sethost" }, wheel, h("div", { class: "sethostname" }, alias, h("small", { class: "sethint" }, x.name + (x.current ? " · this PC" : ""))));
  }));
}
// Settings → Deck → Site order (user 2026-10-26): the nodes highest first; ▲ moves one up. Saved as `order`; the
// writer is the first node in it that answers (deck/site.py). The hint names who serves now.
function renderOrder() {
  const order = [...(prefs.order || [])].filter((n) => hostList.some((x) => x.name === n));
  for (const x of hostList) if (!order.includes(x.name)) order.push(x.name);
  $("#setorder").replaceChildren(...order.map((name, i) =>
    h("div", { class: "sethost" },
      h("button", { type: "button", class: "ghost small", title: "Move up", disabled: i === 0 ? "" : null, onclick: () => {
        const next = [...order]; [next[i - 1], next[i]] = [next[i], next[i - 1]];
        saveSetting({ order: next }).then(() => { prefs.order = next; renderOrder(); });
      } }, "▲"),
      h("div", { class: "sethostname" }, h("b", {}, `${i + 1}. ${hostLabel(name)}`),
        h("small", { class: "sethint" }, name + (name === siteWriter ? " · serves the site now" : "") + (name === hostCurrent ? " · this PC" : ""))))));
}
let siteWriter = "";
async function loadSite() {
  try { const s = await api("GET", "/api/site"); siteWriter = s.writer || ""; } catch { siteWriter = ""; }
  renderOrder();
}
function renderKeyChips() {
  const hidden = new Set(prefs.hidekeys || []);
  $("#setkeys").replaceChildren(...[...document.querySelectorAll("#view-term .keys [data-q]")].map((k) =>
    h("button", { type: "button", class: `fchip${hidden.has(k.dataset.q) ? "" : " on"}`, onclick: () => {
      if (hidden.has(k.dataset.q)) hidden.delete(k.dataset.q); else hidden.add(k.dataset.q);
      saveSetting({ hidekeys: [...hidden] }).then(renderKeyChips);
    } }, k.textContent)));
}
// Settings → Projects (user): every project's main page, the newest link marked main for it (as the project bar picks)
function renderProjects() {
  const mains = new Map();
  for (const it of shelfLive.filter((x) => x.kind === "link" && x.main && x.project).sort((a, b) => b.main - a.main))
    if (!mains.has(it.project)) mains.set(it.project, it);
  $("#setprojects").replaceChildren(...(mains.size ? [...mains].sort(([a], [b]) => a.localeCompare(b)).map(([p, it]) =>
    h("div", { class: "setproject" },
      h("span", { class: "projname", title: p }, p.split("/").pop()),
      h("a", { href: it.url, target: "_blank", rel: "noopener noreferrer", title: it.url }, it.title || it.url),
      h("button", { type: "button", class: "ghost small", title: "Not its main page any more", onclick: () =>
        api("POST", `/api/shelf/${it.id}/main`, { project: "" }).then(loadShelf).then(renderProjects, (err) => toast(err.message)) }, "×")))
    : [h("small", { class: "sethint" }, "none yet: a pinned page's \"Main for …\" on the shelf makes it one")]));
}
// Advanced (rare, PC-side settings): folded on phones, open on a PC where the two columns have room
function openSettings() {
  $("#setform details.setsec").open = wide.matches;
  $("#setalias").value = deckName;
  $("#setalias").placeholder = hostCurrent;
  renderSwatches();
  $("#seterr").textContent = "";
  const f = $("#setform").elements;
  for (const k of ["start", "theme"]) f[k].value = prefs[k] === "paper" ? "light" : prefs[k];
  f.shelfnew.value = prefs.shelfnew || "bottom";
  f.folder.value = prefs.folder; f.brain.value = prefs.brain || ""; f.yolo.checked = prefs.yolo; f.pidurable.checked = !!prefs.pidurable; f.resize.checked = prefs.resize !== false;
  f.chatjump.checked = prefs.chatjump !== false; f.chattools.checked = prefs.chattools !== false; f.thinkopen.checked = !!prefs.thinkopen; f.notesopen.checked = !!prefs.notesopen; f.sessionagents.checked = !!prefs.sessionagents; f.describe.checked = !!prefs.describe; f.describer.value = prefs.describer || "claude"; f.crop.checked = prefs.crop !== false; f.composer.checked = prefs.composer !== false; f.font.value = font();
  f.agentpick.checked = prefs.agentpick !== false;
  const agents = agentsOn(prefs); // Default agent offers only agents set up on this host
  $("#setagent").replaceChildren(...agents.map((a) => h("option", { value: a }, AGENT_NAME[a])));
  f.agent.value = agents.includes(prefs.agent) ? prefs.agent : agents[0] || "";
  $("#setagentwrap").hidden = prefs.agentpick !== false || !agents.length;
  f.textterm.checked = textTerm(); f.chat.checked = !!prefs.chat; f.hosting.checked = prefs.hosting !== false; f.debug.checked = !!prefs.debug; f.scripts.checked = !!prefs.scripts;
  for (const k of ["claude", "codex", "pi", "agy"]) f[`effort-${k}`].value = prefs.effort?.[k] || "";
  f["model-claude"].value = prefs.model?.claude || "";
  $("#setfontval").textContent = `${font()}px`;
  renderKeyChips();
  renderProjects();
  $("#settings").showModal();
}
// Save one change and apply it on this page; the header shows "Saved" (or the field's error below).
let savedTimer;
async function saveSetting(fields) {
  $("#seterr").textContent = "";
  try {
    await api("PUT", "/api/settings", fields);
  } catch (err) { $("#seterr").textContent = err.message; toast(err.message); return; }
  const wasText = textTerm();
  Object.assign(prefs, fields);
  if ("brain" in fields) { prefs.brain = (await api("GET", "/api/settings")).brain; $("#setform").elements.brain.value = prefs.brain; }
  try { localStorage.setItem("deck-prefs", JSON.stringify(prefs)); } catch { /* private mode */ }
  applyPrefs();
  if ("thinkopen" in fields) for (const d of document.querySelectorAll(".cthink:not(.live)")) // the chat on screen too
    d.replaceWith(thinkBlock(d.lastChild.textContent));
  if ("notesopen" in fields) for (const d of document.querySelectorAll(".ctool.cnotes")) d.open = !!prefs.notesopen;
  if (textTerm() !== wasText && attached) { const n = attached; detach(); attach(n); }
  if ("agentpick" in fields) $("#setagentwrap").hidden = fields.agentpick || !agentsOn(prefs).length;
  if ("alias" in fields || "led" in fields || "hostnames" in fields) { await loadHosts(); renderSwatches(); renderChips(); }
  clearTimeout(savedTimer);
  $("#setsaved").textContent = "Saved";
  $("#setsaved").classList.add("on");
  savedTimer = setTimeout(() => $("#setsaved").classList.remove("on"), 1200);
}
$("#setform").addEventListener("change", (e) => {
  const el = e.target;
  if (!el.name || el.name === "appfailover") return; // the colour picker and the app's own switch (9-start.js) save themselves
  if (el.name.startsWith("model-")) return saveSetting({ model: { [el.name.slice(6)]: el.value } }); // Default model: the server merges
  if (el.name.startsWith("effort-")) { // Default effort: one agent's level, merged into the others
    const kind = el.name.slice(7), effort = { ...prefs.effort };
    if (el.value) effort[kind] = el.value; else delete effort[kind];
    return saveSetting({ effort }); // the server merges; this page keeps the whole object
  }
  const value = el.type === "checkbox" ? el.checked : el.type === "range" ? +el.value : el.type === "radio" ? el.value : el.value.trim();
  saveSetting({ [el.name === "font" ? fontKey() : el.name]: value });
});
$("#setform").onsubmit = (e) => { e.preventDefault(); document.activeElement?.blur(); }; // Enter in a text box: blur saves it
$("#setform").elements.font.oninput = (e) => { $("#setfontval").textContent = `${e.target.value}px`; };
// Touch: the font slider moves only on a sideways drag along it. A finger landing on it while scrolling Settings (or a
// plain tap) changes nothing; the slider itself takes no touches (style.css), its row reads the gesture.
{
  const range = $("#setform").elements.font, row = range.closest("label");
  let start = null, sliding = false;
  const to = (x) => {
    const r = range.getBoundingClientRect(), lo = +range.min, hi = +range.max;
    range.value = Math.round(lo + Math.min(1, Math.max(0, (x - r.left) / r.width)) * (hi - lo));
    range.dispatchEvent(new Event("input", { bubbles: true }));
  };
  row.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") { start = { x: e.clientX, y: e.clientY }; sliding = false; } });
  row.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = Math.abs(e.clientX - start.x), dy = Math.abs(e.clientY - start.y);
    if (!sliding && dx > 8 && dx > dy) sliding = true;
    else if (!sliding && dy > 8) start = null; // a scroll
    if (sliding) to(e.clientX);
  });
  const end = () => {
    if (sliding) range.dispatchEvent(new Event("change", { bubbles: true })); // saves it
    start = null; sliding = false;
  };
  row.addEventListener("pointerup", end);
  row.addEventListener("pointercancel", () => { start = null; sliding = false; });
}
$("#setclose").onclick = () => $("#settings").close();
$("#guidebtn").onclick = () => $("#guide").showModal();
// The Android app (a thin shell around this page): web changes reach it without reinstalling. In an Android browser
// App downloads the APK. In the app itself, which can't save a download, App makes a short-lived link
// (/api/app/link) for the phone's browser; an app built after 2026-10-26 opens it there itself.
$("#apkbtn").hidden = !!window.Capacitor || !/Android/i.test(navigator.userAgent);
$("#appbtn").hidden = !window.Capacitor;
$("#appbtn").onclick = async () => {
  const d = $("#applink");
  $("#applinkurl").value = ""; $("#applinkerr").textContent = "";
  d.showModal();
  try {
    const { url } = await api("POST", "/api/app/link");
    $("#applinkurl").value = url;
    $("#applinkopen").href = url;
  } catch (err) { $("#applinkerr").textContent = err.message; }
};
$("#applinkcopy").onclick = () => copyText($("#applinkurl").value).then(
  () => { $("#applinkcopy").textContent = "Copied ✓"; }, () => { $("#applinkerr").textContent = "Can't copy: select the link and copy it"; });
$("#applinkurl").onclick = (e) => e.target.select();
// Phone app: its host list (app/www) lives here, not on a start screen (user 2026-10-26); Back on this page leaves the app
$("#hostsbtn").hidden = !window.Capacitor;
$("#hostsbtn").onclick = () => { location.href = "https://localhost/#hosts"; };
$("#guideclose").onclick = () => $("#guide").close();

// ---- notes: read / edit the synced machine notes ----
const SYNC_WORD = { same: "in sync", behind: "missing our changes", ahead: "has changes we lack", diverged: "out of sync", offline: "offline" };
async function showNotesSync(fresh) {
  try {
    const s = await api("GET", `/api/notes${fresh ? "?fresh=1" : ""}`);
    const bad = s.peers.filter((p) => p.state !== "same" && p.name !== "origin");
    $("#notessync").replaceChildren(`${s.head || "no notes yet"} · `,
      ...s.peers.map((p, i) => h("span", { class: p.state === "same" ? "" : "warn" },
        `${i ? ", " : ""}${p.name} ${SYNC_WORD[p.state] || p.state}`)));
    $("#notessync").classList.toggle("bad", bad.length > 0);
  } catch {
    $("#notessync").textContent = `Offline: showing this phone's copy (${notesBundle?.head || "none yet"})`;
    $("#notessync").classList.add("bad");
  }
}
// All notes in one bundle; the service worker keeps the last one, so the phone can read them offline.
let notesBundle = null;
function loadNote() {
  $("#noteserr").textContent = "";
  $("#notestext").value = notesBundle?.files[$("#notesfile").value] ?? "";
}
$("#notesbtn").onclick = async () => {
  $("#notes").showModal();
  $("#notessync").textContent = "Checking other hosts…";
  try {
    notesBundle = await api("GET", "/api/notes/bundle");
    showNotesSync(true);
    const list = Object.keys(notesBundle.files).sort();
    const keep = $("#notesfile").value;
    $("#notesfile").replaceChildren(...list.map((f) => h("option", { value: f }, f)));
    $("#notesfile").value = list.includes(keep) ? keep : list.includes("AGENTS.md") ? "AGENTS.md" : list[0] || "";
    loadNote();
  } catch (e) { $("#noteserr").textContent = `No copy of the notes on this device yet (${e.message})`; }
};
$("#notesfile").onchange = loadNote;
$("#notesreview").hidden = !wide.matches;
$("#notesreview").onclick = () => {
  const path = $("#notesfile").value;
  if (!path) return;
  $("#notes").close();
  setReview(true); show("term"); openDoc(`note:${path}`);
};
$("#docnotes").onclick = () => $("#notesbtn").click();
$("#docsendon").onchange = (e) => saveState({ readernote: e.target.checked }); // messages name the reader's open documents
$("#notesclose").onclick = () => $("#notes").close();
$("#notesform").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("PUT", "/api/notes/file", { path: $("#notesfile").value, text: $("#notestext").value });
    notesBundle = await api("GET", "/api/notes/bundle");
    toast("Saved; pushing to the other hosts");
    setTimeout(() => showNotesSync(true), 3000);
  } catch (err) { $("#noteserr").textContent = err.message; }
};

// ---- ask sheet: one-off prompt + folder for agent keys, or a key-builder request ----
const ask = $("#ask");
let asking = null; // the key, {fresh, type} (built-in Claude/Codex key) or "build"
function pickFolder(p) {
  $("#askcwd").value = p;
  for (const c of $("#folderchips").children) c.classList.toggle("on", c.dataset.path === p);
}
async function openAsk(target) {
  asking = target;
  const build = target === "build", fresh = !!target.fresh;
  $("#askicon").replaceChildren(icon(build ? "claude" : target.type));
  $("#asktitle").textContent = build ? "Build a key" : fresh ? "New session" : target.label;
  $("#askprompt").value = "";
  const arg = !build && !fresh && asksArg(target);
  $("#askprompt").placeholder = build
    ? "e.g. a key that shows disk space and the 5 biggest folders in ~/work"
    : fresh ? "First message (optional, blank = empty session)"
    : arg ? "What should it do?"
    : `What should ${AGENT_NAME[target.type] || "Claude"} do? (blank = empty session)`;
  $("#askerr").textContent = "";
  $("#askfolders").hidden = build || arg;
  $("#askyolowrap").hidden = build || arg || (!fresh && target.type === "pi");
  $("#askyolo").checked = !build && (fresh ? prefs.yolo : (target.yolo ?? true));
  // The builder starts on Default agent, else the first agent set up here that can build keys (not OpenClaw)
  const builders = agentsOn(prefs).filter((a) => a !== "openclaw");
  const builderAgent = builders.includes(prefs.agent) ? prefs.agent : builders[0] || "claude";
  if (build || fresh) $("#askform").querySelector(`input[name=agent][value=${fresh ? target.type : builderAgent}]`).checked = true;
  $("#askagent").hidden = !build && !fresh;
  $("#askhostwrap").hidden = !build || hostList.length < 2;
  if (build) {
    $("#askhosts").replaceChildren(...hostList.map((x) => {
      const chip = h("button", { type: "button", class: `fchip${x.current ? " on" : ""}`, onclick: () => {
        for (const c of $("#askhosts").children) c.classList.toggle("on", c === chip);
      } }, x.name);
      chip.dataset.host = x.name; chip.dataset.current = x.current ? "1" : "";
      return chip;
    }));
  }
  $("#folderchips").replaceChildren();
  ask.showModal();
  $("#askprompt").focus();
  if (build || arg) return;
  try {
    const f = await api("GET", "/api/folders");
    const list = [...new Set([fresh ? "" : target.cwd, f.default, ...f.recent, ...f.projects].filter(Boolean))].slice(0, 10);
    $("#askcwd").placeholder = f.default;
    $("#folderchips").replaceChildren(...list.map((p) => {
      const chip = h("button", { type: "button", class: "fchip", onclick: () => pickFolder(p) }, p.replace(/^~\/work\//, ""));
      chip.dataset.path = p;
      return chip;
    }));
    pickFolder((!fresh && target.cwd) || f.recent[0] || f.default);
  } catch { /* chips are a convenience */ }
}
$("#askcancel").onclick = () => ask.close();
$("#askform").onsubmit = async (ev) => {
  ev.preventDefault();
  const text = $("#askprompt").value.trim();
  try {
    if (asking === "build") {
      const agent = $("#askform").querySelector("input[name=agent]:checked").value;
      const target = $("#askhosts .fchip.on");
      if (target && !target.dataset.current) {
        // Another host: build there through the /peer proxy, staying on this page (user: one site for every host)
        const at = target.dataset.host;
        const { session } = await api("POST", `/peer/${encodeURIComponent(at)}/api/build`, { request: text, agent });
        ask.close(); await poll(); openSession(`${at}/${session}`);
        return;
      }
      const { session } = await api("POST", "/api/build", { request: text, agent });
      ask.close(); await poll(); openSession(session);
    } else if (asking.fresh) {
      const agent = $("#askform").querySelector("input[name=agent]:checked").value;
      const { session } = await api("POST", "/api/agent", { agent, text, cwd: $("#askcwd").value.trim(), yolo: $("#askyolo").checked });
      ask.close(); await poll(); openSession(session);
    } else {
      const b = asking;
      if (asksArg(b)) {  // its input goes in as "$1"; the saved command stays
        if (!text) { $("#askerr").textContent = "say what it should do"; return; }
        ask.close();
        await launch(b, { arg: text });
        return;
      }
      ask.close();
      await launch(b, { text, cwd: $("#askcwd").value.trim(), yolo: $("#askyolo").checked });
    }
  } catch (e) { $("#askerr").textContent = e.message; }
};

// ---- edit dialog ----
const dlg = $("#edit"), form = $("#editform");
let editing = null;
function openEdit(b) {
  editing = b;
  form.reset();
  $("#editerr").textContent = "";
  $("#edittitle").textContent = b ? "Edit script" : "New script";
  $("#del").hidden = $("#again").hidden = !b;
  $("#buildwith").hidden = !!b;
  form.elements.id.value = b?.id || "";
  form.elements.label.value = b?.label || "";
  form.elements.text.value = b?.text || "";
  form.elements.cwd.value = b?.cwd || "";
  const runOn = form.elements.host; // this host first, then every other known host (and a saved one no longer listed)
  const names = [...new Set([HOME_HOST, ...hostList.map((x) => x.name), ...(b?.host ? [b.host] : [])])];
  runOn.replaceChildren(...names.map((n) => h("option", { value: n === HOME_HOST ? "" : n }, n === HOME_HOST ? `${n} (this host)` : n)));
  runOn.value = b?.host && b.host !== HOME_HOST ? b.host : "";
  form.elements.yolo.checked = b?.yolo ?? prefs.yolo;
  form.querySelector(`input[name=type][value=${b?.type || "claude"}]`).checked = true;
  syncLabel();
  dlg.showModal();
}
function syncLabel() {
  $("#textlabel").textContent = TEXT_LABEL[form.elements.type.value];
  $("#edityolo").hidden = ["shell", "pi"].includes(form.elements.type.value); // Pi never asks permission
}
for (const r of form.querySelectorAll("input[name=type]")) r.onchange = syncLabel;
$("#save").onclick = async (ev) => {
  ev.preventDefault();
  try { await api("POST", "/api/buttons", Object.fromEntries(new FormData(form))); dlg.close(); loadButtons(); }
  catch (e) { $("#editerr").textContent = e.message; }
};
$("#del").onclick = async () => {
  if (!confirmTwice($("#del"))) return;
  await api("DELETE", `/api/buttons/${form.elements.id.value}`);
  dlg.close(); loadButtons();
};
$("#again").onclick = () => { dlg.close(); if (editing) launch(editing); };
$("#buildwith").onclick = () => { dlg.close(); openAsk("build"); };

// Inline two-tap confirm (no browser dialogs).
function confirmTwice(btn) {
  if (btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = btn.dataset.label; return true; }
  btn.dataset.label = btn.textContent; btn.dataset.armed = "1"; btn.textContent = "Tap again";
  setTimeout(() => { if (btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = btn.dataset.label; } }, 3000);
  return false;
}

function toast(text) {
  const el = h("div", { class: "toast" }, text);
  document.body.append(el);
  setTimeout(() => el.remove(), 4000);
}
window.toast = toast;  // for screen.js

loadButtons();
loadHosts();
const prefsReady = loadPrefs();
// A new install, opened on the PC itself: host sessions here, or client only (then straight to linking a PC).
prefsReady.then(async () => {
  if (!prefs.fresh) return;
  const me = await fetch("/api/me").then((r) => r.json()).catch(() => null);
  if (me?.local) $("#hostask").showModal();
});
for (const [btn, hosting] of [["#hostaskyes", true], ["#hostaskno", false]]) $(btn).onclick = async () => {
  $("#hostask").close();
  await saveSetting({ hosting });
  if (!hosting) { $("#linkbtn").onclick(); $("#linkjoin").onclick(); }
};
$("#hostask").addEventListener("cancel", (e) => e.preventDefault()); // answer it
// Offline copy: the service worker keeps the app and the latest notes bundle (fetched here on every open).
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").then(() => fetch("/api/notes/bundle")).catch(() => {});
}
poll();
