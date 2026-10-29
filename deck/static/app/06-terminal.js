// ---- the terminal: the open session on screen (xterm.js over a WebSocket to its host), what goes to it (keys, the
// message box's lines, touch scrolling), and the agent footer cropped into a status bar ----
// Each tab's last screen (plain text), shown at once when switching back while its live view loads (phones: the
// tunnel's round trips). Text mode only: its first frame redraws the whole screen anyway.
const lastScreens = new Map();
function snapshotScreen() {
  const b = term?.buffer.active;
  if (!b || !attached) return;
  const rows = [];
  for (let i = 0; i < term.rows; i++) rows.push(b.getLine(b.viewportY + i)?.translateToString(true) ?? "");
  lastScreens.set(attached, rows);
}
function openSession(name) {
  name = fullName(name); // a new session's name from its host's API is bare
  if (attached !== name) {
    if (term && textTerm() && !chatNames.has(name)) {
      // Keep the terminal (rebuilding it is slow on a phone); swap the connection and show the cached screen.
      snapshotScreen();
      clearTimeout(reconnectTimer);
      if (ws) { ws.onclose = null; ws.close(); ws = null; }
      term.reset();
      const rows = lastScreens.get(name);
      if (rows) term.write("\x1b[90m" + rows.join("\r\n").trimEnd() + "\x1b[0m");
    } else if (!(chatView && chatNames.has(name))) detach();
    // (chat to chat: attachChat parks the one on screen, and its stack stays in the page; emptying it here made the
    // browser lay out a long chat all over again on the way back, ~250 ms)
  }
  useDraft(name);
  attached = name;
  show("term");
  attach(name);
}

// Fallback when nothing is selected: the newest running session. With none the screen stays empty; it no longer opens
// a shell on the host (user): + opens one.
async function attachNewest() {
  try {
    await poll();
    const newest = sessions.filter((s) => s.running).sort((a, b) => b.created - a.created)[0];
    if (!newest) return;
    useDraft(newest.name); attached = newest.name; attach(newest.name); poll();
  }
  catch (e) { toast(e.message); }
}

// The terminal's colours: the pane's background and text (the look's --pane-bg and --pane-text, plain hex values),
// the host's LED as the cursor; a light pane darkens the ANSI colours that vanish on it
const LIGHT_ANSI = { black: "#1f1f1d", white: "#6b675f", brightWhite: "#3a3833", yellow: "#9a6b00", brightYellow: "#8a5d00",
  green: "#1c7c33", brightGreen: "#16682a", cyan: "#0e6f7c", brightCyan: "#0b5c67", blue: "#1f5fd1", magenta: "#8b3fb0" };
function termTheme() {
  const cs = getComputedStyle($("#term")), v = (k, d) => cs.getPropertyValue(k).trim() || d;
  const light = shownTheme() === "light";
  const background = v("--pane-bg", light ? "#efe9dc" : "#121311"), foreground = v("--pane-text", light ? "#1f1f1d" : "#ebe7dc");
  return { background, foreground, cursor: led, cursorAccent: background, selectionBackground: `${led}44`, ...(light ? LIGHT_ANSI : {}) };
}

function attach(name) {
  name = fullName(name); // saved before names carried their host
  if (prefs[sessionKey()] !== name) saveState({ [sessionKey()]: name });
  if (chatNames.has(name)) { attachChat(name); renderChips(); return; }
  if (chatView) detach();
  if (term) { connectTerminal(name); return; }
  term = new Terminal({
    fontSize: font(), fontFamily: "'IBM Plex Mono', 'Noto Sans Symbols 2', ui-monospace, monospace", cursorBlink: true, scrollback: 5000,
    theme: termTheme(),
  });
  fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  // URLs in the output are links: a click opens them like a chat link (the Web pane when it's open, else a tab)
  if (window.WebLinksAddon) term.loadAddon(new WebLinksAddon.WebLinksAddon((ev, url) => { ev.preventDefault(); openLink(url); }));
  const host = h("div", { class: "termhost" });
  $("#term").replaceChildren(host);
  term.open(host);
  // Phone keyboards replace whole words/composition buffers. A live terminal
  // cannot apply those edits reliably; compose in a normal textarea instead.
  setTermTyping();
  phoneTyping(host);
  fit.fit();
  connectTerminal(name);
  // Agents (Claude) turn on mouse reporting; on a touch screen every tap and swipe would reach them as clicks at
  // that spot (and cancel the scroll). Phones scroll with swipes (below) and type in the message box, so drop them.
  const coarse = window.matchMedia("(pointer: coarse)").matches;
  const MOUSE = /^(\x1b\[<\d+;\d+;\d+[Mm]|\x1b\[M[\s\S]{3})+$/;
  term.onData((d) => { if (!(coarse && MOUSE.test(d))) send(d); });
  // Text mode: the page never sees the program turn on mouse reporting, and xterm.js would turn the wheel into arrow
  // keys on its alternate screen; scroll on the host instead (tmux history, or wheel events for full-screen agents).
  term.attachCustomWheelEventHandler((ev) => {
    if (!textTerm()) return true;
    scrollBy(ev.deltaMode === 1 ? ev.deltaY : ev.deltaY / lineHeight());
    return false;
  });
  term.onWriteParsed(scheduleCrop);
  term.onResize(scheduleCrop);
  renderChips();
}

function setTermTyping() {
  const box = window.matchMedia("(pointer: coarse)").matches && prefs.composer !== false;
  term.textarea.readOnly = box;
  if (box) term.textarea.setAttribute("inputmode", "none"); else term.textarea.removeAttribute("inputmode");
}

// Message box off, phone keyboard typing straight into the terminal: autocorrect and suggestions rewrite words already
// typed (a composition), which xterm.js turns into doubled or lost text. Instead the page keeps the keyboard's own
// text and sends only the difference from what was sent before: backspaces for what changed, then the new letters.
// Enter, and keys the keyboard sends as plain key events (Backspace on an empty box), still go through xterm.js.
let typingReset = () => {}; // phoneTyping: the box's text no longer matches the line (quick keys, paste)
function phoneTyping(host) {
  const box = term.textarea;
  let sent = "";
  const on = () => window.matchMedia("(pointer: coarse)").matches && prefs.composer === false;
  const sync = () => {
    const now = box.value;
    let p = 0;
    while (p < sent.length && p < now.length && sent[p] === now[p]) p++;
    const d = "\x7f".repeat([...sent.slice(p)].length) + now.slice(p);
    if (d) send(d);
    sent = now;
  };
  const reset = () => { box.value = ""; sent = ""; };
  for (const type of ["compositionstart", "compositionupdate", "compositionend", "input"]) {
    host.addEventListener(type, (ev) => {
      if (!on() || ev.target !== box) return;
      ev.stopImmediatePropagation();
      // Some keyboards type Enter as a line break in the box: that's Enter for the terminal (a bare \n is a newline
      // inside an agent's input, not Submit).
      if (box.value.includes("\n")) { box.value = box.value.replace(/\n/g, ""); sync(); send("\r"); reset(); return; }
      if (type === "input" || type === "compositionend") sync();
    }, true);
  }
  host.addEventListener("keydown", (ev) => {
    if (!on() || ev.target !== box) return;
    if (ev.isComposing || ev.keyCode === 229 || (ev.key === "Backspace" && box.value)) { ev.stopImmediatePropagation(); return; }
    if (ev.key === "Enter") setTimeout(reset); // the line is done: start the next one from an empty box
  }, true);
  // xterm.js empties the box itself on blur, paste, Enter and ^C; a quick key changes the line behind its back.
  typingReset = () => { if (on()) reset(); };
  box.addEventListener("blur", typingReset);
  box.addEventListener("paste", () => setTimeout(typingReset));
}

// ---- agent footer: crop the agent's own input box + status lines, show them as a card instead ----
// Typing goes through the message box, so the agent's prompt box only takes space, and its status line is cut at the
// terminal's width. Covered: everything below the spinner/token line (or below the conversation when idle).
// Claude: from the input box's top rule (a ─── line right above "❯"; on Windows ">", and the rule may carry text). Codex: from its "›" prompt, when its footer
// (Context … / ? for shortcuts) follows. Menus (permission questions) never match, so they stay visible.
const SPINNER = /…\s*\(\d+[hms]|\(\d+[hms][^)]*esc to interrupt/; // Claude "Wibbling… (18s · …)", Codex "Working (5s • esc…"
function footerCrop(lines, kind) {
  const n = lines.length;
  let start = -1;
  for (let i = n - 1; i >= Math.max(1, n - 16) && start < 0; i--) {
    if (kind === "claude" && /^\s*[❯>]/.test(lines[i]) && /^\s*─{2,}/.test(lines[i - 1])) start = i - 1;
    if (kind === "codex" && /^\s*›/.test(lines[i])
      && lines.slice(i + 1, i + 5).some((l) => /Context \d+%|for shortcuts|context left/i.test(l))) start = i;
  }
  if (start < 0) return null;
  for (let k = start - 1; k >= Math.max(0, start - 6); k--) {
    if (SPINNER.test(lines[k])) { start = k + 1; break; }
  }
  const top = start;
  while (start > 0 && !lines[start - 1].trim()) start--; // blank rows above go too
  const gap = top - start; // a big gap = short conversation (Codex keeps its prompt at the bottom)
  // Card text: footer lines after the prompt box (status line, mode), minus rules, hints and the prompt itself.
  const tail = lines.slice(start).map((l) => l.trim())
    .filter((l) => l && !/^[─━]{2,}|^[❯›>]|for shortcuts|^⎿|Tip:/.test(l) && !SPINNER.test(l));
  return { start, gap, tail };
}

// Status chips kept short enough for one row on a phone: Claude and Codex wording, limits as % used.
function shortPart(p) {
  let m;
  if (/^Goal[^:]*: /.test(p)) return "Goal"; // the /goal in force: its words only on a PC
  if ((m = p.match(/^(?:ctx|Context) (\d+)% (used)?/i))) return `ctx ${m[1]}%`;
  if ((m = p.match(/^Context (\d+)% left/i))) return `ctx ${100 - m[1]}%`;
  if ((m = p.match(/^\$(\d+)(\.\d*)?$/))) return +m[1] >= 10 || !m[2] || m[2].length < 3 ? `$${m[1]}` : p; // "$42." = cut
  if ((m = p.match(/^(5h|five-hour|weekly|wk)\D*(\d+)% (left|used)?/i)))
    return `${/^(5h|five)/i.test(m[1]) ? "5h" : "wk"} ${m[3] === "left" ? 100 - m[2] : m[2]}%`;
  return p.replace(/\b(default)\b/, "").replace(/\bmedium\b/, "med").replace(/\s*\((\d+[KM]) context\)/i, " $1")
    .replace(/\s+/g, " ").trim();
}
function shortMode(m) {
  m = m.replace(/^[^\w]+/, "").replace(/\s*\(.*$/, "").replace(/\s+on$/, "");
  return { "bypass permissions": "bypass", "accept edits": "edits", "plan mode": "plan" }[m] || m;
}

let cropRaf = 0, extraRows = 0;
function scheduleCrop() { if (!cropRaf) cropRaf = requestAnimationFrame(() => { cropRaf = 0; applyCrop(); }); }
function applyCrop() {
  const hostEl = $("#term .termhost");
  if (!term || !hostEl) return;
  const s = sessions.find((x) => x.name === attached);
  const kind = s?.type;
  const buf = term.buffer.active;
  const lines = [];
  for (let i = 0; i < term.rows; i++) lines.push(buf.getLine(buf.viewportY + i)?.translateToString(true) ?? "");
  // tmux copy mode (scrolling a non-full-screen agent): its position "[12/1989]" sits top right. Keep the frame where
  // it was so the history slides under it instead of jumping as the footer scrolls out of view.
  if (/\[\d+\/\d+\]\s*$/.test(lines[0])) return;
  const crop = extraRows && buf.viewportY === buf.baseY ? footerCrop(lines, kind) : null;
  const xt = hostEl.querySelector(".xterm");
  const rowH = hostEl.querySelector(".xterm-screen").clientHeight / term.rows;
  // No crop (a menu, a shell, history): line the terminal's bottom up with the frame's; extra rows go off the top.
  // Crop: the last line that matters sits on the frame's bottom edge and the agent's footer falls below it
  // (the tab's extra rows fill the space it took, see sendSize).
  xt.style.transform = crop ? `translateY(${hostEl.clientHeight - crop.start * rowH}px)`
    : extraRows ? `translateY(${-extraRows * rowH}px)` : "";
  if (!crop) return; // the bar keeps its last chips while a menu is up
  // Uncut status parts from the agent's status line script when we have them; else the terminal's line.
  const mode = crop.tail.find((l) => /permissions|accept edits|plan mode|auto mode|full access/i.test(l));
  const statusLine = crop.tail.find((l) => l !== mode && l.includes("·"));
  const parts = (s.status?.length ? s.status : (statusLine || "").split(/\s+·\s+/))
    .map((p) => p.replace(/\s*…$/, "").trim()).filter(Boolean); // the terminal cuts its line with "…"
  const chips = [...parts.map(shortPart), mode && shortMode(mode)].filter(Boolean);
  const bar = $("#agentbar"), key = attached + JSON.stringify(chips);
  if (bar.dataset.key === key) return;
  bar.dataset.key = key;
  bar.replaceChildren(...chips.map((c, i) =>
    h("span", { class: `fpart${i === 0 ? " model" : ""}${mode && i === chips.length - 1 ? " mode" : ""}` }, c)));
}

function connectTerminal(name) {
  if (ws && ws.readyState < 2) return;
  clearTimeout(reconnectTimer);
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}${wsPath(name)}${textTerm() ? "?mode=text" : ""}`);
  ws = socket;
  socket.binaryType = "arraybuffer";
  // Text mode's first frame clears the screen itself, so a cached screen stays up until the live one replaces it.
  socket.onopen = () => { if (ws === socket) { if (!textTerm()) term.reset(); sendSize(); } };
  socket.onmessage = (e) => { if (ws === socket) term.write(new Uint8Array(e.data)); };
  socket.onclose = () => {
    if (ws !== socket) return;
    ws = null;
    term?.write("\r\n\x1b[90m[reconnecting…]\x1b[0m\r\n");
    if (view === "term" && attached === name && document.visibilityState === "visible") {
      reconnectTimer = setTimeout(() => restoreTerminal(), 1500);
    }
  };
}

async function restoreTerminal() {
  if (view !== "term" || !attached) return;
  const name = attached;
  try {
    const live = await listSessions();
    noteChats(live);
    if (view !== "term" || attached !== name) return;
    if (!live.some((s) => s.name === name)) {
      detach(); attached = null; saveState({ [sessionKey()]: "" });
      toast("That session has ended");
      if (view === "term") await attachNewest();
      return;
    }
    attach(name);
  } catch {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(restoreTerminal, 3000);
  }
}

// Text terminal (experimental, shared setting): the host sends the session's text (polled) instead of a tmux client's
// output, so nothing re-attaches on resize. See terminal_text in deck/api/terminal.py.
function textTerm() { return prefs.textterm === true; }

function detach() {
  clearTimeout(reconnectTimer);
  if (chatView) parkChat(); // a chat stays loaded and connected (chatCache); its socket isn't closed here
  else if (ws) { ws.onclose = null; ws.close(); }
  ws = null;
  if (term) { term.dispose(); term = null; }
  fit = null; // its terminal is gone: a late refit (layout, Review, font) would read the disposed one
  if (chatShell) {
    chatShell = false;
    document.querySelector(":is(.keybar, #say, .brand) .chatstatus")?.remove(); // Working… / Stop stay with the chat
    $("#term").after($(".keybar")); // the keys back under the screen
    $(".keybar").after($("#say")); // the message box back below the keys, before the chat goes
    $("#attach").hidden = true; // attached items wait for the next chat
    $("#term").before($("#agentbar")); // out of the chat before it's emptied (placeHarness places it)
    $("#term").replaceChildren();
    $("#term").classList.remove("chat");
    projBar.hidden = true;
    $("#say").hidden = prefs.composer === false;
    placeHarness();
    $("#agentbar").hidden = true;
    delete $("#agentbar").dataset.key;
    $("#shelf").hidden = true;
  }
}

// ---- input to the session ----
function send(d) { if (ws?.readyState !== 1) return false; ws.send(JSON.stringify({ t: "in", d })); return true; }
// A line to submit (message box, slash commands): the host types it into the session, then Enter on its own (Claude
// takes a burst as a paste, and Enter inside a paste is a newline). Inner \n (Ctrl-J) is a newline in agent input.
// via: how it was sent, for the message's stamp (the server adds the device's name). Agents go by it: on a phone they
// keep replies short (agents.DECK_CONTEXT). A touch screen narrower than the PC layout counts as a phone.
const VIA = window.Capacitor ? "phone app" : matchMedia("(pointer: coarse)").matches && !wide.matches ? "phone browser" : "PC browser";
function say(d) { if (ws?.readyState !== 1) return false; ws.send(JSON.stringify({ t: "say", d, via: VIA })); return true; }
// Desktop: Back (button, Alt+←, mouse back) scrolls the terminal up to the previous prompt instead of leaving
// for whichever host was open before. A spare history entry catches it; phones keep their normal back gesture.
if (wide.matches) {
  history.pushState({ deckBack: true }, "");
  window.addEventListener("popstate", () => {
    if (!wide.matches) return;
    history.pushState({ deckBack: true }, "");
    if (view === "term" && ws?.readyState === 1) ws.send(JSON.stringify({ t: "prevprompt" }));
  });
}
function sendSize() {
  if (!term || ws?.readyState !== 1) return;
  fit.fit();
  // Agent tabs get rows more than fit: the cropped footer sits in them, below the frame (applyCrop). Claude's is 6 rows
  // idle and 7-13 working (spinner tip, survey, a prompt typed in its own box); a footer taller than the extra rows pulls the screen
  // down and leaves a blank band at the top. Fixed per tab and set only here (attach / window resize): resizing an
  // agent mid-session makes it redraw and garbles its footer.
  const kind = sessions.find((x) => x.name === attached)?.type;
  extraRows = (kind === "claude" || kind === "codex") && prefs.crop !== false ? 14 : 0;
  // The status bar shows for agent tabs only, set here so its space never changes mid-session (no resize churn).
  if ($("#agentbar").hidden !== !extraRows) { $("#agentbar").hidden = !extraRows; fit.fit(); }
  if (extraRows) term.resize(term.cols, term.rows + extraRows);
  ws.send(JSON.stringify({ t: "resize", cols: term.cols, rows: term.rows }));
}
// tmux owns the alternate screen/history, so mobile swipes scroll its copy mode (or send wheel events to full-screen
// agents, see scroll_terminal). The page follows the finger, sends at most one scroll per frame, and keeps going
// after a flick, slowing down like a native list; a new touch stops it.
let termTouch = null, fling = 0, scrollOwed = 0, scrollRaf = 0;
const termSurface = $("#term");
const lineHeight = () => Math.max(1, termSurface.clientHeight / (term?.rows || 1));
function scrollBy(lines) {
  scrollOwed += lines;
  if (!scrollRaf) scrollRaf = requestAnimationFrame(() => {
    scrollRaf = 0;
    const n = Math.trunc(scrollOwed);
    if (!n || !term) return;
    scrollOwed -= n;
    if (term.buffer.active.type === "alternate") {
      if (ws?.readyState === 1) ws.send(JSON.stringify({ t: "scroll", lines: n }));
    } else term.scrollLines(n);
  });
}
// The terminal repaints as it scrolls and replaces the row under the finger; the browser keeps sending that touch to
// the removed row, outside the page. So the handlers also sit on the touched element itself (each event runs once).
termSurface.addEventListener("touchstart", (ev) => {
  cancelAnimationFrame(fling); fling = 0;
  termTouch = ev.touches.length === 1 ? { y: ev.touches[0].clientY, moved: false, track: [] } : null;
  const el = ev.target;
  if (!termTouch || el === termSurface) return;
  el.addEventListener("touchmove", termMove, { passive: false });
  el.addEventListener("touchend", termEnd);
  el.addEventListener("touchcancel", termEnd);
  termTouch.el = el;
}, { passive: true, capture: true });
function termMove(ev) {
  if (ev.deckSeen) return;
  ev.deckSeen = true;
  if (!termTouch || ev.touches.length !== 1 || !term) return;
  const y = ev.touches[0].clientY;
  const delta = termTouch.y - y;
  if (!termTouch.moved && Math.abs(delta) < 8) return;
  termTouch.moved = true;
  termTouch.y = y;
  ev.preventDefault();
  ev.stopImmediatePropagation();
  const now = performance.now();
  termTouch.track = termTouch.track.filter((p) => now - p.t < 100).concat({ t: now, y });
  scrollBy(delta / lineHeight());
}
function termEnd(ev) {
  if (ev.deckSeen) return;
  ev.deckSeen = true;
  const el = termTouch?.el;
  if (el) {
    el.removeEventListener("touchmove", termMove);
    el.removeEventListener("touchend", termEnd);
    el.removeEventListener("touchcancel", termEnd);
  }
  const tr = ev.type === "touchend" && termTouch?.moved ? termTouch.track : [];
  termTouch = null;
  if (tr.length < 2 || performance.now() - tr[tr.length - 1].t > 60) return; // finger stopped before lifting
  let v = (tr[0].y - tr[tr.length - 1].y) / Math.max(1, tr[tr.length - 1].t - tr[0].t); // px/ms, + = toward newer
  if (Math.abs(v) < 0.3) return;
  let last = performance.now();
  const step = (now) => {
    const dt = Math.min(50, now - last); last = now;
    scrollBy((v * dt) / lineHeight());
    v *= Math.pow(0.996, dt); // about 1s to settle from a quick flick
    fling = Math.abs(v) > 0.03 ? requestAnimationFrame(step) : 0;
  };
  fling = requestAnimationFrame(step);
}
termSurface.addEventListener("touchmove", termMove, { passive: false, capture: true });
termSurface.addEventListener("touchend", termEnd, { capture: true });
termSurface.addEventListener("touchcancel", termEnd, { capture: true });
// The host hears the new size once it settles: every frame of a drag would refit the terminal and resize the session
// (on Windows that starts a new tmux client each time), which made dragging the Review divider or a window crawl
let sizeTimer = 0;
new ResizeObserver(() => { clearTimeout(sizeTimer); sizeTimer = setTimeout(sendSize, 120); }).observe($("#term"));
for (const k of document.querySelectorAll(".keys button[data-k]")) k.onclick = () => { typingReset(); send(KEYS[k.dataset.k]); };
