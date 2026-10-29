// ---- prefs: the settings that shape this page (theme, look, font, columns), cached in localStorage so they apply
// before the server answers; the page's views (Terminal, Review, Web, Screen) and the PC's column layout ----
let chatShape = ""; // the prefs the chat's heights depend on, as last applied
function applyPrefs() {
  // Agent choices (key editor, ask sheet) only for agents set up on this host; + follows on its next render (user).
  const setUp = agentsOn(prefs);
  for (const a of Object.keys(AGENT_NAME))
    for (const el of document.querySelectorAll(`input[value=${a}]`)) el.closest("label").style.display = setUp.includes(a) ? "" : "none";
  $("#setdurable").style.display = prefs.durable && prefs.pi !== false ? "" : "none"; // only where pi-durable is installed
  $("#docsendon").checked = prefs.readernote !== false; // the reader's "Tell agent" box
  $("#term").classList.toggle("notools", prefs.chattools === false); // chats without their tool calls
  $("#app").classList.toggle("debug", !!prefs.debug); // chats show Deck's hidden text too (style.css .cdebug)
  // Replies change height when the font or what a chat shows changes: only then are the Hide bars measured again
  // (a long chat's measure is the dear part of this, and a theme change doesn't need it)
  const shape = `${font()}|${prefs.chattools}|${prefs.debug}|${prefs.thinkopen}|${prefs.notesopen}`;
  if (chatView && shape !== chatShape) refreshBars();
  chatShape = shape;
  applyLook();
  document.documentElement.dataset.theme = shownTheme();
  if (term) term.options.theme = termTheme(); // the terminal takes the pane's colours
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
// Desktop columns (widths, collapsed) are shared too, so the PC app window and a browser on the PC look alike.
function applyLayout() {
  const l = prefs.layout || {};
  for (const k of ["padcw", "feedcw"]) {
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
  $("#togglefull").setAttribute("aria-pressed", !!l.full);
  $("#togglefull").setAttribute("title", l.full ? "Minimize: the margins as before" : "Fullscreen: no margins");
  $("#app").classList.toggle("nobottom", !!l.nobottom);
  $("#togglebottom").setAttribute("aria-pressed", !l.nobottom);
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
sysDark.addEventListener("change", applyPrefs);

// ---- views ----
// The view tabs in the top bar: Terminal (with Review or Web beside the chat on a PC) and Screen; the tab column
// and the Pad show beside every view. The open session is kept apart for phones and wide screens: one session on a phone and the PC at once would keep
// resizing between their sizes (tmux has one size per window), garbling both.
const sessionKey = () => wide.matches ? "pcsession" : "session";
wide.addEventListener("change", () => term && applyPrefs());
const isVisible = (id, v) => id === `view-${wide.matches && v !== "screen" ? "term" : v}`;
function show(v, connect = true) {
  if (v === "agents" || v === "deck") v = "term"; // retired views: Agents, and the Pad (a column now)
  view = v;
  $("#chips").hidden = v !== "term";
  for (const el of document.querySelectorAll(".view")) el.hidden = !isVisible(el.id, v);
  for (const t of document.querySelectorAll(".tabbar button")) t.classList.toggle("on", t.dataset.view === (v === "term" && (reviewOn || webOn) ? (webOn ? "web" : "review") : v));
  if (connect) {
    if (v === "term") { if (attached) attach(attached); else attachNewest(); }
    else detach();
  }
  window.deckScreen?.onShow(v);
  render();
}
// Review and Web are the Terminal view with a document or a web page beside the chat (11-review.js setReview, setWeb)
for (const t of document.querySelectorAll(".tabbar button")) t.onclick = () => {
  if (["term", "review", "web"].includes(t.dataset.view)) { setWeb(t.dataset.view === "web"); setReview(t.dataset.view === "review"); }
  show(["review", "web"].includes(t.dataset.view) ? "term" : t.dataset.view);
};

// ---- the PC's columns (shared layout: applyLayout) ----
// Desktop: drag the terminal's left or right edge for an empty margin beside it (widths shared: applyLayout). Each
// margin has its own width: --padcw on the left, --feedcw on the right (named for the columns they once held).
const COL = { pad: 640, feed: 760 };
for (const side of ["pad", "feed"]) {
  const max = COL[side], cprop = `--${side}cw`;
  const grip = $(`#grip${side}`);
  grip.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    // Drag in px: pin both margins at their current widths and drop the shares (saveCols restores them)
    const [pad, , feed] = gridCols();
    $("#app").style.setProperty("--padcw", `${pad}px`); $("#app").style.setProperty("--feedcw", `${feed}px`);
    for (const c of ["l", "m", "r"]) $("#app").style.removeProperty(`--col${c}`);
    grip.classList.add("drag"); $("#app").classList.add("resizing");
    const box = $("#app").getBoundingClientRect();
    const move = (ev) => {
      const raw = side === "pad" ? ev.clientX - box.left : box.right - ev.clientX;
      $("#app").style.setProperty(cprop, `${Math.round(Math.max(0, Math.min(max, raw)))}px`);
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
  // the shares as flex factors (the base layout) and as grid tracks (a look that lays the row out as a grid)
  const split = (f) => { const a = $("#app").style; a.setProperty("--chatfr", f); a.setProperty("--docfr", 1 - f); a.setProperty("--chatcol", `${f}fr`); a.setProperty("--doccol", `${1 - f}fr`); };
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
wide.addEventListener("change", () => show(view === "screen" ? "screen" : "term"));

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
  closeMenu(); // another session: its menu goes
}
wide.addEventListener("change", placeHarness);
