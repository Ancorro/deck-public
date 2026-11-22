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

// ---- settings: three tabs; every control saves itself as it changes ----
// Settings → Hosts → Names and colours: every host's name and LED colour, set from any host (user: alias the hosts,
// change their colour). Shared by the whole Deck (settings.save_settings hostnames); the machine's own name stays
// underneath, with how this host reaches it now (via: iroh, tailscale or address).
const hostLabel = (name) => hostList.find((x) => x.name === name)?.aka || name;
function renderSwatches() {
  const save = (name, alias, colour) => saveSetting({ hostnames: { [name]: { alias, led: colour } } });
  $("#sethosts").replaceChildren(...hostList.map((x) => {
    const alias = h("input", { value: x.aka || "", placeholder: x.name, maxlength: "24", autocomplete: "off", "aria-label": `${x.name}'s name` });
    const pick = h("input", { type: "color", value: x.led, "aria-label": `${x.name}'s colour` });
    const wheel = h("label", { class: "wheel", title: "Colour", style: `--c: ${x.led}` }, pick);
    alias.onchange = () => save(x.name, alias.value, pick.value);
    pick.onchange = () => save(x.name, alias.value, pick.value); // change, not input: one save per pick
    return h("div", { class: "sethost" }, wheel, h("div", { class: "sethostname" }, alias, h("small", { class: "sethint" }, x.name + (x.current ? " · this PC" : x.via ? ` · ${x.via}` : ""))));
  }));
}
// Settings → Hosts → Site order (user 2026-10-26): the nodes highest first; ▲ moves one up. Saved as `order`; the
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
// Settings → General → Projects (user): every project's main page, the newest link marked main for it (as the project
// bar picks)
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
// Settings → General → Logins and the setup's login step (user 2026-11-08: "an easy way to new CLI users to log in",
// "part of a set up sequence", "also in settings"): each host's agents and tools (deck/logins.py) as signed in, or a Log
// in that opens a tab running the tool's own login. Hosts that hold no sessions (client only) or don't answer are left out.
const hostApi = (host) => host === HOME_HOST ? "" : `/peer/${encodeURIComponent(host)}`;
let loginWatch = 0;
async function renderLogins(box, { fresh = false, only = "" } = {}) {
  const hosts = only ? [only] : [HOME_HOST, ...hostList.map((x) => x.name).filter((n) => n !== HOME_HOST)];
  if (!box.childElementCount) box.replaceChildren(h("small", { class: "sethint" }, "Checking…"));
  const lists = await Promise.all(hosts.map((host) =>
    api("GET", `${hostApi(host)}/api/logins${fresh ? "?fresh=1" : ""}`).then((l) => [host, l], () => [host, null])));
  const rows = [];
  for (const [host, list] of lists) {
    if (!list?.length) continue;
    if (hosts.length > 1) rows.push(h("div", { class: "loginhost" }, hostLabel(host)));
    for (const t of list) rows.push(h("div", { class: "setlogin" },
      h("span", { class: "loginname" }, t.name),
      h("span", { class: "loginwhat", title: t.what }, t.signedin ? t.what : `${t.what}${t.optional ? " (optional)" : ""}`),
      t.signedin ? h("span", { class: "loginok" }, "✓ Signed in")
        : h("button", { type: "button", class: "ghost small", onclick: () => startLogin(host, t, box, only) }, "Log in")));
  }
  box.replaceChildren(...(rows.length ? rows : [h("small", { class: "sethint" }, "no agents or tools found on the hosts that answered")]));
}
async function startLogin(host, t, box, only) {
  try {
    const { session } = await api("POST", `${hostApi(host)}/api/logins/${t.tool}`);
    const name = host === HOME_HOST ? session : `${host}/${session}`;
    // Open it once the column lists it: attaching earlier shows an empty terminal
    for (let i = 0; i < 20 && !sessions.some((s) => s.name === fullName(name)); i++) {
      await poll(); await new Promise((r) => setTimeout(r, 250));
    }
    $("#settings").close(); $("#loginask").close();
    openSession(name);
    toast(`${t.name}: click the sign-in link in this tab, then approve in the browser`);
    // Re-check while the tab signs in, so Settings shows it when the user comes back
    clearInterval(loginWatch);
    let n = 0;
    loginWatch = setInterval(async () => {
      const l = await api("GET", `${hostApi(host)}/api/logins?fresh=1`).catch(() => null);
      if (l?.find((x) => x.tool === t.tool)?.signedin) { clearInterval(loginWatch); toast(`${t.name} is signed in on ${hostLabel(host)}`); renderLogins(box, { only }); }
      else if (++n > 60) clearInterval(loginWatch); // 10 minutes
    }, 10000);
  } catch (e) { toast(e.message); }
}
// Settings → General → New sessions → Default model: a menu per agent (user 2026-11-10: "not a drop down for each
// agent"), filled from what the agent offers on this host (/api/models/KIND; Codex: its account's catalog). A saved
// choice the list lacks stays in the menu. A list that does not load leaves the saved choice and Agent default.
// Computer (a Computer session, which runs Claude): Claude's list; blank, it starts on Claude's model.
const MODEL_AGENTS = ["claude", "codex", "opencode", "pi", "agy", "openclaw", "computer"];
const MODEL_LIST = { computer: "claude" };
async function loadModelDefaults(kind) {
  const select = $("#setform").elements[`model-${kind}`];
  const blank = kind === "computer" ? "Claude's model" : "Agent default";
  const fill = (models, first = blank) => {
    const current = prefs.model?.[kind] || "";
    select.replaceChildren(h("option", { value: "" }, first),
      ...models.map((m) => h("option", { value: m.model }, m.displayName || m.model)));
    if (current && !models.some((m) => m.model === current)) select.append(h("option", { value: current }, current));
    select.value = current;
  };
  // One list for the whole Deck, the same on every host (user 2026-11-10: "there is a ground truth"): the server
  // answers with the first host's in the site order that has one. It can take seconds, so the menu says Loading… until then: the saved choice alone must not look like the whole list.
  fill([], "Loading…"); select.disabled = true;
  try {
    const { data } = await api("GET", `/api/models/${MODEL_LIST[kind] || kind}`);
    fill((data || []).filter((m) => m.model));
  } catch {
    fill([]);
    const list = MODEL_LIST[kind] || kind;
    $("#seterr").textContent = `Could not load ${AGENT_NAME[list] || list} models on any host.`;
  }
  select.disabled = false;
}
function openSettings() {
  $("#setform details.setsec").open = wide.matches;
  let tab = "look"; // Appearance comes first (user 2026-11-08: "make appearance the first tab")
  try { tab = ["general", "hosts"].includes(localStorage.getItem("deck-settab")) ? localStorage.getItem("deck-settab") : "look"; } catch { /* first visit */ }
  setTab(tab);
  $("#setalias").value = deckName;
  $("#setalias").placeholder = hostCurrent;
  renderSwatches();
  $("#seterr").textContent = "";
  const f = $("#setform").elements;
  f.theme.value = prefs.theme === "paper" ? "light" : prefs.theme;
  f.look.value = prefs.look in (window.DECK_LOOKS || {}) ? prefs.look : "pad";
  f.shelfnew.value = prefs.shelfnew || "bottom";
  f.folder.value = prefs.folder; f.brain.value = prefs.brain || ""; f.yolo.checked = prefs.yolo; f.pidurable.checked = !!prefs.pidurable;
  f.chatjump.checked = prefs.chatjump !== false; f.chattools.checked = prefs.chattools !== false; f.thinkopen.checked = !!prefs.thinkopen; f.notesopen.checked = !!prefs.notesopen; f.sessionagents.checked = !!prefs.sessionagents; f.describe.checked = !!prefs.describe; f.describer.value = prefs.describer || "claude"; f.crop.checked = prefs.crop !== false; f.composer.checked = prefs.composer !== false; f.font.value = font();
  f.agentpick.checked = prefs.agentpick !== false;
  const agents = agentsOn(prefs); // Default agent offers only agents set up on this host
  $("#setagent").replaceChildren(...agents.map((a) => h("option", { value: a }, AGENT_NAME[a])));
  f.agent.value = agents.includes(prefs.agent) ? prefs.agent : agents[0] || "";
  $("#setagentwrap").hidden = prefs.agentpick !== false || !agents.length;
  f.textterm.checked = textTerm(); f.chat.checked = !!prefs.chat; f.hosting.checked = prefs.hosting !== false; f.debug.checked = !!prefs.debug; f.scripts.checked = !!prefs.scripts;
  for (const k of ["claude", "codex", "pi", "agy"]) f[`effort-${k}`].value = prefs.effort?.[k] || "";
  for (const k of MODEL_AGENTS) f[`model-${k}`].value = prefs.model?.[k] || "";
  for (const k of MODEL_AGENTS) loadModelDefaults(k);
  $("#setfontval").textContent = `${font()}px`;
  renderKeyChips();
  renderProjects();
  renderLogins($("#setlogins"));
  renderAccent();
  $("#settings").showModal();
}
// Save one change: applied on this page at once (a theme or a font must not wait for the host, which may be a
// tunnel away), then sent to the host; if that fails, the page goes back to what the host has and says so.
let savedTimer;
async function saveSetting(fields) {
  $("#seterr").textContent = "";
  const before = Object.fromEntries(Object.keys(fields).map((k) => [k, prefs[k]]));
  const wasText = textTerm();
  Object.assign(prefs, fields);
  try { localStorage.setItem("deck-prefs", JSON.stringify(prefs)); } catch { /* private mode */ }
  const done = holdPrefs(fields);
  applyPrefs();
  if ("thinkopen" in fields) for (const d of document.querySelectorAll(".cthink:not(.live)")) // the chat on screen too
    d.replaceWith(thinkBlock(d.lastChild.textContent));
  if ("notesopen" in fields) for (const d of document.querySelectorAll(".ctool.cnotes")) d.open = !!prefs.notesopen;
  if (textTerm() !== wasText && attached) { const n = attached; detach(); attach(n); }
  if ("agentpick" in fields) $("#setagentwrap").hidden = fields.agentpick || !agentsOn(prefs).length;
  try {
    await api("PUT", "/api/settings", fields);
  } catch (err) {
    done(false);
    Object.assign(prefs, before);
    applyPrefs();
    for (const [k, v] of Object.entries(before)) { const el = $("#setform").elements[k]; if (el && typeof v !== "object") el.value = v; }
    $("#seterr").textContent = err.message; toast(err.message);
    return;
  }
  done(true);
  if ("brain" in fields) { prefs.brain = (await api("GET", "/api/settings")).brain; $("#setform").elements.brain.value = prefs.brain; }
  if ("alias" in fields || "led" in fields || "hostnames" in fields) { await loadHosts(); renderSwatches(); renderChips(); }
  clearTimeout(savedTimer);
  $("#setsaved").textContent = "Saved";
  $("#setsaved").classList.add("on");
  savedTimer = setTimeout(() => $("#setsaved").classList.remove("on"), 1200);
}
$("#setform").addEventListener("change", (e) => {
  const el = e.target;
  if (!el.name || el.name === "appfailover") return; // the colour picker and the app's own switch (14-start.js) save themselves
  if (el.name.startsWith("model-")) {
    const kind = el.name.slice(6), model = { ...prefs.model };
    el.value = el.value.trim();
    if (el.value) model[kind] = el.value; else delete model[kind];
    // Send the cleared key too: the server merges, while this page keeps all agents' choices.
    return saveSetting({ model: { ...model, [kind]: el.value } });
  }
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
// Settings tabs: Appearance, General, Hosts; the last one picked opens next time (this browser). With one host the Hosts
// tab is greyed out (user 2026-11-08: "hosts tab should not be interact if only 1 host"): it shows only how to add one,
// and PCs (linking a second) sits in Settings → General → More instead. Nothing a one-host Deck uses lives only there
// ("ensure nothing is lost by this for 1 host users"): this PC's colour is Settings → Appearance → Accent colour; Devices,
// Host sessions on this PC and Agents can see sessions are in General.
const oneHost = () => hostList.length < 2;
function setTab(tab) {
  const one = oneHost();
  const hostsTab = document.querySelector('#settabs [data-tab="hosts"]');
  hostsTab.classList.toggle("off", one);
  hostsTab.title = one ? "For a Deck with more than one host: link another PC in Settings → General → More → PCs" : "";
  $("#sethostsone").hidden = !one; $("#sethostsmany").hidden = one;
  $("#linkbtnone").hidden = !one;
  if (one && tab === "hosts") tab = "look";
  $("#setform").dataset.tab = tab;
  for (const b of document.querySelectorAll("#settabs [data-tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
  try { localStorage.setItem("deck-settab", tab); } catch { /* this visit only */ }
}
for (const b of document.querySelectorAll("#settabs [data-tab]")) b.onclick = () => {
  if (b.dataset.tab === "hosts" && oneHost()) return toast("Hosts applies once this Deck has more than one host: link another PC in Settings → General → More → PCs");
  setTab(b.dataset.tab); $("#settings").scrollTop = 0;
};
$("#linkbtnone").onclick = () => $("#linkbtn").onclick();
// Settings → Appearance → Accent colour: this PC's colour (its LED), the same value as its row in Settings → Hosts →
// Names and colours
function renderAccent() {
  const me = hostList.find((x) => x.current) || { name: hostCurrent, aka: "", led };
  $("#setaccent").value = /^#[0-9a-f]{6}$/i.test(me.led || "") ? me.led : "#f1690e";
  $("#setaccentwheel").style.setProperty("--c", $("#setaccent").value);
  $("#setaccent").onchange = () => {
    const c = $("#setaccent").value;
    $("#setaccentwheel").style.setProperty("--c", c);
    // led: the colour a Deck with no host list uses (settings.hosts); hostnames: this host's row once there are several
    saveSetting({ led: c, hostnames: { [me.name]: { alias: me.aka || "", led: c } } }).then(loadHosts);
  };
}
$("#guidebtn").onclick = () => { $("#guide").showModal(); $("#guide").scrollTop = 0; }; // its only button is at the end: don't open scrolled to it
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
$("#codesendon").onchange = (e) => saveState({ readernote: e.target.checked }); // and the Code view's open files
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
