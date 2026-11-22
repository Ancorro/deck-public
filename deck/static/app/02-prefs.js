// ---- prefs: the settings that shape this page (theme, look, font, columns), cached in localStorage so they apply
// before the server answers; the page's views (Board, Terminal, Review, Web, Screen) and the PC's column layout ----
let chatShape = ""; // the prefs the chat's heights depend on, as last applied
function applyPrefs() {
  // Agent choices (key editor, ask sheet) only for agents set up on this host; + follows on its next render (user).
  const setUp = agentsOn(prefs);
  for (const a of Object.keys(AGENT_NAME))
    for (const el of document.querySelectorAll(`input[value=${a}]`)) el.closest("label").style.display = setUp.includes(a) ? "" : "none";
  $("#setdurable").style.display = prefs.durable && prefs.pi !== false ? "" : "none"; // only where pi-durable is installed
  $("#docsendon").checked = $("#codesendon").checked = prefs.readernote !== false; // the reader's and Code's "Tell agent" boxes
  $("#term").classList.toggle("notools", prefs.chattools === false); // chats without their tool calls
  $("#app").classList.toggle("debug", !!prefs.debug); // chats show Deck's hidden text too (style.css .cdebug)
  // Replies change height when the font or what a chat shows changes: only then are the Hide bars measured again
  // (a long chat's measure is the dear part of this, and a theme change doesn't need it)
  const shape = `${font()}|${uiZoom()}|${uiText()}|${prefs.chattools}|${prefs.debug}|${prefs.thinkopen}|${prefs.notesopen}`;
  if (chatView && shape !== chatShape) refreshBars();
  chatShape = shape;
  applyLook();
  document.documentElement.dataset.theme = shownTheme();
  if (term) term.options.theme = termTheme(); // the terminal takes the pane's colours
  if (split2.term) { split2.term.options.theme = termTheme(); split2.term.options.fontSize = termFont(); split2.fit.fit(); } // and the second column
  if (typeof codeRetheme === "function") codeRetheme(); // and the Code view's editor
  document.documentElement.style.setProperty("--uizoom", uiZoom());
  document.documentElement.style.setProperty("--uitext", uiText());
  if (term && term.options.fontSize !== termFont()) { term.options.fontSize = termFont(); fit?.fit(); }
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
  applyLayout();
  applyScriptShut();
}
// The look (Settings → Appearance → Look): one stylesheet over style.css and its fonts, swapped in place (the head's
// inline script put the saved one in before the first paint); the browser's theme colour follows the page.
function applyLook() {
  const looks = window.DECK_LOOKS || {}, root = document.documentElement;
  const look = prefs.look in looks ? prefs.look : "pad";
  if (root.dataset.look !== look) {
    root.dataset.look = look;
    const v = document.querySelector('meta[name="deck-assets"]')?.content;
    const sheet = $("#lookcss") || document.head.appendChild(h("link", { rel: "stylesheet", id: "lookcss" }));
    sheet.onload = () => { if (term) term.options.theme = termTheme(); fit?.fit(); themeColor(); };
    sheet.href = `/static/looks/${look}.css${v ? `?v=${v}` : ""}`;
    $("#lookfonts")?.remove();
    if (looks[look]) document.head.append(h("link", { rel: "stylesheet", id: "lookfonts", href: looks[look] }));
  }
  themeColor();
}
function themeColor() {
  requestAnimationFrame(() => {
    const m = document.querySelector('meta[name="theme-color"]');
    if (m) m.content = getComputedStyle(document.body).backgroundColor;
  });
}
// Settings this page is saving, kept over the host's copy until the host surely has them (user: "swapping themes and
// light mode bug and not stay"): a follower gets a save from the writer a moment after the save returns, and a
// reload in between (the sync poll's loadPrefs) put the old look and theme back on the page.
const savingPrefs = new Map(); // key -> {value, until}
const SAVE_SETTLE_MS = 8000;
function holdPrefs(fields) {
  const marks = {};
  for (const k of Object.keys(fields)) { marks[k] = { value: fields[k], until: Infinity }; savingPrefs.set(k, marks[k]); }
  return (ok) => { // the save is done: keep the value a while longer (ok), or drop it (refused)
    for (const [k, m] of Object.entries(marks)) {
      if (savingPrefs.get(k) !== m) continue; // a newer save of this key holds it now
      if (ok) m.until = Date.now() + SAVE_SETTLE_MS; else savingPrefs.delete(k);
    }
  };
}
function withSaving(fresh) {
  const now = Date.now();
  for (const [k, m] of savingPrefs) {
    if (m.until < now) savingPrefs.delete(k);
    else fresh[k] = m.value;
  }
  return fresh;
}
async function loadPrefs() {
  try {
    prefs = withSaving(await api("GET", "/api/settings"));
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
  const done = holdPrefs(fields);
  api("PUT", "/api/settings", fields).then(() => done(true), () => done(true)); // kept on this page until the next save
}
// The PC's layout flags are shared too, so the PC app window and a browser on the PC look alike. The session pane
// always fills the window, the tab column and the shelf at its edges (user 2026-11-21: "make the side columns always
// at the edge, no more adjsut margin"); the margins' old widths (padcw, feedcw, termw, full) are ignored.
function applyLayout() {
  const l = prefs.layout || {};
  $("#app").classList.toggle("nobottom", !!l.nobottom);
  $("#togglebottom").setAttribute("aria-pressed", !l.nobottom);
  applyMsgShut();
  renderLayouts();
}
function saveLayout(k, v) { saveState({ layout: { ...(prefs.layout || {}), [k]: v || undefined } }); }
sysDark.addEventListener("change", applyPrefs);

// ---- views ----
// The view tabs in the top bar: Terminal (with Review or Web beside the chat on a PC) and Screen; the tab column
// and the Pad show beside every view. The open session is kept apart for phones and wide screens: one session on a phone and the PC at once would keep
// resizing between their sizes (tmux has one size per window), garbling both.
const sessionKey = () => wide.matches ? "pcsession" : "session";
wide.addEventListener("change", () => term && applyPrefs());
const isVisible = (id, v) => id === `view-${wide.matches && v !== "board" ? "term" : v}`;
let bootRestore = true; // the saved session may have ended while the page was shut: check before attaching
function show(v, connect = true) {
  if (v === "agents" || v === "deck") v = "term"; // retired views: Agents, and the Pad (a column now)
  view = v;
  // Deck opens on the last view used (user 2026-11-04: "make deck always open on the last view used")
  try { localStorage.setItem("deck-view", v === "term" && screenOn && !screenAuto ? "screen" : v); } catch { /* this visit only */ }
  $("#chips").hidden = v !== "term";
  for (const el of document.querySelectorAll(".view")) el.hidden = !isVisible(el.id, v);
  const on = v !== "term" ? v : screenOn ? "screen" : webOn ? "web" : codeOn ? "code" : reviewOn ? "review" : "term";
  for (const t of document.querySelectorAll(".tabbar button")) t.classList.toggle("on", t.dataset.view === on);
  if (connect) {
    if (v === "term") {
      if (bootRestore) { bootRestore = false; if (attached) restoreTerminal(); else attachNewest(); } // first Terminal since boot (the Board opens first)
      else if (attached) attach(attached); else attachNewest();
    }
    else detach();
  }
  window.deckScreen?.onShow(v === "term" && screenOn ? "screen" : v);
  split2Apply(); // the PC's second terminal column shows with the Terminal view only (06-terminal.js)
  if (v === "board") loadBoard(true);
  render();
}
// Review and Web are the Terminal view with a document or a web page beside the chat (11-review.js setReview, setWeb);
// Screen is the Terminal view with the host's desktop above the chat (setScreen), so the agent can be messaged while
// watching (user 2026-11-02: "fix screen stream so can chat to agent while looking")
// screenAuto: shown for a Computer session (computerScreen), not by the tab; screenFor: the Computer session whose
// host screen.js shows (retried each render until screen.js, a module loaded after this script, is there);
// screenDismissed: the Computer session whose screen the user shut
let screenOn = false, screenAuto = false, screenFor = "", screenDismissed = "";
function setScreen(on) {
  screenOn = on;
  if (!on) screenAuto = false;
  $("#view-screen").hidden = $("#screengrip").hidden = !on;
  $("#app").classList.toggle("screening", on);
  // The shelf folds to its strip while the desktop shows, without changing what is remembered for it
  $("#shelf").classList.toggle("shut", on || !shelfOpen);
  if (on && codeOn) setCode(false); // the Code view's file tree and editor make way for the desktop
}
// A Computer session (Claude driving its host's desktop) shows that host's screen above its chat, streaming (user
// 2026-11-02: "it should be a session on the machine"); another tab puts back what was there
function computerScreen() {
  const s = view === "term" && attached ? sessions.find((x) => x.name === attached) : null;
  if (s?.computer && screenDismissed !== s.name) {
    if (!screenOn) { setWeb(false); setReview(false); setCode(false); setScreen(true); screenAuto = true; }
    if (screenFor !== s.name && window.deckScreen) { screenFor = s.name; window.deckScreen.follow(s.host, true); }
  } else {
    screenFor = "";
    if (!screenAuto) return;
    setScreen(false); window.deckScreen?.onShow("term");
  }
  for (const t of document.querySelectorAll(".tabbar button")) t.classList.toggle("on", t.dataset.view === (screenOn ? "screen" : "term"));
}
for (const t of document.querySelectorAll(".tabbar button")) t.onclick = () => {
  const v = t.dataset.view;
  const cur = attached && sessions.find((x) => x.name === attached);
  screenDismissed = cur?.computer && v !== "screen" ? cur.name : ""; // shut its screen: it stays shut for that tab
  if (["term", "review", "code", "web", "screen"].includes(v)) {
    setWeb(v === "web"); setReview(v === "review"); setCode(v === "code"); setScreen(v === "screen");
  }
  show(["review", "code", "web", "screen"].includes(v) ? "term" : v);
};
// The divider under the screen: drag to share the height between the desktop and the chat (the screen takes the
// larger share by default; user: "just bigger"); double-click: back to that
{
  const grip = $("#screengrip");
  const split = (f) => $("#app").style.setProperty("--scrfr", f);
  try { const f = +localStorage.getItem("deck-screen-split"); if (f > 0.1 && f < 0.9) split(f); } catch { /* default */ }
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("drag"); $("#app").classList.add("resizing");
    const top = $("#view-screen").getBoundingClientRect().top, bottom = $("#term").getBoundingClientRect().bottom;
    let f = null, frame = 0;
    const move = (ev) => {
      f = Math.min(0.9, Math.max(0.1, (ev.clientY - top) / (bottom - top)));
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; split(f); });
    };
    const up = () => {
      grip.removeEventListener("pointermove", move);
      cancelAnimationFrame(frame);
      grip.classList.remove("drag"); $("#app").classList.remove("resizing");
      if (f === null) return;
      split(f); fit?.fit();
      try { localStorage.setItem("deck-screen-split", f.toFixed(3)); } catch { /* this visit only */ }
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up, { once: true });
    grip.addEventListener("pointercancel", up, { once: true });
  });
  grip.addEventListener("dblclick", () => { $("#app").style.removeProperty("--scrfr"); fit?.fit(); try { localStorage.removeItem("deck-screen-split"); } catch { /* fine */ } });
}

// Saved layouts (PC header): the Layouts button saves the columns as they are now, which adds a button that brings
// them back, or deletes a saved one (user). Right-click a layout's button (or its Edit entry in the Layouts menu) to
// rename it in place or save the current columns over it (user 2026-11-03: "let me edit and rename layouts")
// The retired margins' keys (applyLayout) don't count, so a layout saved with margins matches the same one without
const MARGIN_KEYS = new Set(["padcw", "feedcw", "termw", "bpadcw", "bfeedcw", "btermw", "full"]);
const layoutKeys = (l) => JSON.stringify(Object.entries(l || {}).filter(([k]) => !MARGIN_KEYS.has(k)).sort());
function sameLayout(a, b) { return layoutKeys(a) === layoutKeys(b); }
// A saved layout keeps what the Layouts menu lists beside the columns: UI size, UI text and Text size (user 2026-11-15:
// "saved layouts does not save all the settings listed there")
const layoutSizes = () => ({ [uiKey()]: Math.round(uiZoom() * 100), [uiTextKey()]: Math.round(uiText() * 100), [fontKey()]: font() });
const layoutNow = () => ({ layout: { ...(prefs.layout || {}) }, sizes: layoutSizes() });
const isLayoutNow = (x) => sameLayout(x.layout, prefs.layout) &&
  Object.entries(x.sizes || {}).every(([k, v]) => (prefs[k] ?? (/font/.test(k) ? 13 : 100)) === v);
function restoreLayout(x) {
  saveState({ layout: { ...x.layout } });
  applyLayout();
  const sizes = Object.fromEntries(Object.entries(x.sizes || {}).filter(([k, v]) => prefs[k] !== v));
  if (Object.keys(sizes).length) saveSetting(sizes).then(renderLayouts);
}
// A plain number reads "Layout 2" (not a bare number: user "vague button"); a name the user gave shows as given
const layoutLabel = (x) => /^\d+$/.test(x.name) ? `Layout ${x.name}` : x.name;
function renderLayouts() {
  const box = $("#layouts");
  if (!box) return;
  box.replaceChildren(...(prefs.layouts || []).map((x) => h("button", {
    class: "paneltoggle", type: "button", title: `Restore ${layoutLabel(x)} (right-click: rename, save over)`,
    "aria-pressed": isLayoutNow(x), "data-layout": x.id,
    onclick: () => restoreLayout(x),
    oncontextmenu: (e) => { e.preventDefault(); layoutMenu(x, e.clientX, e.clientY); },
  }, layoutLabel(x))));
}
function layoutMenu(x, px, py) {
  popMenu(px, py, (item) => [
    item("Rename", () => renameLayout(x.id)),
    item("Save current layout here", () =>
      saveLayouts((prefs.layouts || []).map((y) => y.id === x.id ? { ...y, ...layoutNow() } : y))),
    item("Delete", (btn) => {
      if (!confirmTwice(btn)) return false; // stays open: click again to delete
      saveLayouts((prefs.layouts || []).filter((y) => y.id !== x.id));
    })]);
}
// In place, as a tab's rename: Enter or leaving the box saves, Escape keeps the old name, empty keeps it too
function renameLayout(id) {
  const btn = $(`#layouts [data-layout="${id}"]`), x = (prefs.layouts || []).find((y) => y.id === id);
  if (!btn || !x) return;
  const input = h("input", { class: "tabname layoutname", value: x.name, "aria-label": "Layout name", maxlength: "30" });
  input.style.width = `${Math.max(8, x.name.length + 2)}ch`;
  btn.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    if (save && name && name !== x.name) saveLayouts((prefs.layouts || []).map((y) => y.id === id ? { ...y, name } : y));
    else renderLayouts();
  };
  input.addEventListener("input", () => { input.style.width = `${Math.max(8, input.value.length + 2)}ch`; });
  input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
}
function saveLayouts(list) { saveState({ layouts: list }); renderLayouts(); }
// The Layouts menu also sizes the page: UI size zooms all of it, UI text sizes its own text (not chats or terminals),
// Text size is the terminals' and chats' font
function sizeRow(label, text, step) {
  const val = h("output", {}, text());
  const btn = (sign, name) => h("button", { type: "button", class: "sizebtn", "aria-label": `${label} ${name}`,
    onclick: () => { step(sign); val.textContent = text(); } }, sign < 0 ? "−" : "+");
  return h("div", { class: "sizerow" }, h("span", {}, label), btn(-1, "smaller"), val, btn(1, "bigger"));
}
const UI_SIZES = [75, 80, 90, 100, 110, 125, 150];
$("#layoutbtn").onclick = (e) => {
  const r = e.currentTarget.getBoundingClientRect(), list = prefs.layouts || [];
  popMenu(r.left, r.bottom + 4, (item) => [
    sizeRow("UI size", () => `${Math.round(uiZoom() * 100)}%`, (d) => {
      const now = Math.round(uiZoom() * 100);
      const next = d > 0 ? UI_SIZES.find((x) => x > now) ?? now : UI_SIZES.findLast((x) => x < now) ?? now;
      if (next === now) return;
      saveSetting({ [uiKey()]: next });
      $("#layoutbtn").click(); // the menu again, under its button at the new size
    }),
    sizeRow("UI text", () => `${Math.round(uiText() * 100)}%`, (d) => {
      const now = Math.round(uiText() * 100);
      const next = d > 0 ? UI_SIZES.find((x) => x > now) ?? now : UI_SIZES.findLast((x) => x < now) ?? now;
      if (next === now) return;
      saveSetting({ [uiTextKey()]: next });
      $("#layoutbtn").click(); // the menu again, its text at the new size
    }),
    sizeRow("Text size", () => `${font()}px`, (d) => {
      const next = Math.max(10, Math.min(36, font() + d));
      if (next !== font()) saveSetting({ [fontKey()]: next });
    }),
    item("Save this layout", () => {
      const used = new Set(list.map((x) => x.name));
      let n = 1;
      while (used.has(String(n))) n++;
      saveLayouts([...list, { id: Date.now().toString(36), name: String(n), ...layoutNow() }]);
    }),
    ...list.map((x) => item(`Edit ${layoutLabel(x)}…`, () => layoutMenu(x, r.left, r.bottom + 4)))]);
};
// Review's divider: drag to share the width between the chat and the reader; double-click: half and half
{
  const grip = $("#docgrip");
  // the shares as flex factors (the base layout) and as grid tracks (a look that lays the row out as a grid)
  const split = (f) => { const a = $("#app").style; a.setProperty("--chatfr", f); a.setProperty("--docfr", 1 - f); a.setProperty("--chatcol", `${f}fr`); a.setProperty("--doccol", `${1 - f}fr`); };
  try { const f = +localStorage.getItem("deck-review-split"); if (f > 0.15 && f < 0.85) split(f); } catch { /* half and half */ }
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    grip.classList.add("drag"); $("#app").classList.add("resizing");
    const pane = webOn ? $("#webpane") : codeOn ? $("#codepane") : $("#docpane"); // the reader, the editor, or the Web view's page
    const t = $("#term").getBoundingClientRect(), d = pane.getBoundingClientRect(), g = grip.getBoundingClientRect();
    const a = t.left, b = d.right;
    // the shares split what's left after each side's padding and borders (flex-basis 0), so the line lands true
    const fixed = (el) => { const s = getComputedStyle(el); return ["paddingLeft", "paddingRight", "borderLeftWidth", "borderRightWidth"].reduce((n, k) => n + parseFloat(s[k]), 0); };
    const padT = fixed($("#term")) * uiZoom(), free = t.width + d.width - padT - fixed(pane) * uiZoom();
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
// Desktop: collapse the keys and message box under the terminal (shared layout).
$("#togglebottom").onclick = () => { saveLayout("nobottom", !$("#app").classList.contains("nobottom")); applyLayout(); };
// PC: the message box collapses to one line (its toggle sits above its top-right corner; user)
function applyMsgShut() {
  const shut = !!prefs.layout?.msgshut && wide.matches;
  $("#say").classList.toggle("shut", shut);
  $("#msgshut").setAttribute("aria-pressed", shut);
  $("#msgshut").title = shut ? "Expand the message box" : "Collapse the message box";
  fit?.fit();
}
$("#msgshut").onclick = () => { saveLayout("msgshut", !$("#say").classList.contains("shut")); applyMsgShut(); };
wide.addEventListener("change", applyMsgShut);
wide.addEventListener("change", () => show(view === "board" ? view : "term"));

// A PC has no key row (style.css): the message box gets a tool row, as in Claude's web app (Harness and Resume on the
// left; Model, Effort, the mic and Send on the right). A phone, or a PC with the message box off (Settings), keeps
// Harness first in the keys and the mic and Send beside the box.
function placeHarness() {
  // each move is a layout, so nothing already where it goes is moved (applyPrefs calls this on every change)
  const kids = (parent, nodes, fromEnd) => { const c = [...parent.children]; return nodes.every((n, i) => c[fromEnd ? c.length - nodes.length + i : i] === n); };
  const append = (parent, ...nodes) => { if (!kids(parent, nodes, true)) parent.append(...nodes); };
  const prepend = (parent, ...nodes) => { if (!kids(parent, nodes, false)) parent.prepend(...nodes); };
  const after = (ref, node) => { if (ref.nextElementSibling !== node) ref.after(node); };
  const before = (ref, node) => { if (ref.previousElementSibling !== node) ref.before(node); };
  const send = $("#say button[type=submit]");
  if (wide.matches && !$("#say").hidden) {
    append($("#say .msgfield"), $("#msgmenu"), $(".msgbar"));
    prepend($("#say .mbl"), $("#harnessbtn"));
    append($("#say .mbr"), $("#upbtn"), $("#mic"), send);
  } else {
    // a phone or narrow window: the tool row (Resume, Brain, Model, Effort) sits in the key row after Harness; + left
    // of the message box (user)
    prepend($(".keybar .keys"), $("#harnessbtn"), $(".msgbar"));
    append($(".keybar"), $("#msgmenu"));
    append($("#say .msgside"), $("#upbtn"), $("#mic"));
    append($("#say"), send);
  }
  // A chat's Working… and Stop: on a phone, at the top bar's right end, beside the view tabs (user: the key row had no room);
  // on a PC, together just above the message box (user)
  // A chat's status line: on a PC at the foot of the chat, under the message box (user); a terminal's above the screen
  // in its column; a phone's above the whole row, full width
  if (wide.matches && $("#term").classList.contains("chat")) append($("#term"), $("#agentbar"));
  else if (wide.matches) before($("#term"), $("#agentbar"));
  else before($("#view-term .termwrap"), $("#agentbar"));
  if (chatView && $("#term").classList.contains("chat")) {
    if (wide.matches) after(chatView.box, chatView.status);
    else append($(".top .brand"), chatView.status);
  }
  if ($("#say").hidden) append($(".keys .kright"), $("#upbtn"));
  for (const b of document.querySelectorAll("#say .chatonly, .msgbar .chatonly, #upbtn"))
    b.hidden = !$("#term").classList.contains("chat") || (b.classList.contains("agyonly") && sessions.find((s) => s.name === attached)?.type !== "agy");
  // another session: its menu goes. Only then: a settings reload (any Board card, shelf pin or setting on any host
  // runs this) closed an open Model menu while agy was still listing its models (user: "can't change model in mobile")
  if (menuReq && menuSession !== attached) closeMenu();
}
wide.addEventListener("change", placeHarness);
