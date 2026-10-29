// ---- chat sessions: agents run under the bridge (deck/bridge/); the page shows its events as a chat ----
function noteChats(list) { chatNames = new Set(list.filter((s) => s.chat).map((s) => s.name)); }

// Recently opened chats stay loaded: their drawn log and their connection, which keeps following the chat while
// another is shown, so switching back is instant. The oldest beyond CHAT_KEEP is closed.
const chatCache = new Map(), CHAT_KEEP = 4;
let chatShell = false; // #term is set up as a chat (message box and keys in it)
// A chat's end, followed a few frames in case what's below is still being laid out
function chatToEnd(box, frames = 3) {
  box.scrollTop = box.scrollHeight;
  if (frames) requestAnimationFrame(() => { if (box.scrollHeight - box.scrollTop - box.clientHeight > 2) chatToEnd(box, frames - 1); });
}
let stackEl = null;
const chatStack = () => stackEl ||= h("div", { id: "chatstack" });
function parkChat() {
  if (picking) setPicking(false); // a selection belongs to the chat it was made in
  const v = chatView;
  v.atEnd = v.box.scrollHeight - v.box.scrollTop - v.box.clientHeight < 60;
  v.scroll = v.box.scrollTop;
  v.box.classList.add("parked"); v.status.remove(); // hidden in place, its layout kept (style.css .chatlog.parked)
  chatView = null;
}
function chatViewFor(name) {
  let v = chatCache.get(name);
  if (!v) {
    const box = h("div", { class: "chatlog" });
    const status = h("div", { class: "chatstatus", hidden: "" }, h("span", { class: "chatspin" }, h("i", { class: "dot", "aria-hidden": "true" }), h("span", { class: "spintext" }, "Working…")),
      h("button", { type: "button", onclick: () => v.socket?.send(JSON.stringify({ t: "abort" })) }, "Stop"));
    v = { name, box, status, msgs: new Map(), tools: new Map(), socket: null, scroll: 0, atEnd: true };
    deckContextBlock(v);
  }
  chatCache.delete(name); chatCache.set(name, v); // most recent last
  for (const [n, old] of chatCache) {
    if (chatCache.size <= CHAT_KEEP) break;
    if (old.socket) { old.socket.onclose = null; old.socket.close(); }
    old.box.remove();
    chatCache.delete(n);
  }
  return v;
}

function attachChat(name) {
  if (term) detach();
  if (chatView?.name !== name) {
    if (chatView) parkChat();
    else if (ws) { ws.onclose = null; ws.close(); }
    const v = chatView = chatViewFor(name);
    ws = v.socket?.readyState < 2 ? v.socket : null;
    chatShell = true;
    $("#term").classList.add("chat");
    // the message box sits in the chat, like a chat app's, with the keys just above it
    // The kept chats are stacked in one place, the same size (style.css #chatstack); the others are parked there,
    // hidden with their layout kept, so switching back to a long chat is a show, not a re-layout. Nothing already in
    // place is moved (moving an element throws its layout away).
    const t = $("#term"), stack = chatStack(), keep = new Set([...chatCache.values()].map((x) => x.box));
    if (stack.parentNode !== t || t.firstElementChild !== stack) t.prepend(stack);
    for (const el of [...t.children]) if (![stack, v.status, $(".keybar"), $("#say"), $("#agentbar"), projBar].includes(el)) el.remove();
    if (projBar.parentNode !== t) t.append(projBar); // shown at the top (style.css order), placed last so nothing moves
    for (const el of [...stack.children]) if (!keep.has(el)) el.remove();
    for (const box of keep) if (box.parentNode !== stack) stack.append(box);
    if (v.status.previousElementSibling !== stack) stack.after(v.status);
    if (v.status.nextElementSibling !== $(".keybar")) v.status.after($(".keybar"), $("#say"));
    for (const box of keep) box.classList.toggle("parked", box !== v.box);
    if (v.lastStatus) chatEvent(v.lastStatus); // its model, context and limits, as it last reported them
    else $("#agentbar").hidden = true;
    renderAttached();
    $("#say").hidden = false;
    placeHarness();
    $("#shelf").hidden = false; // its strip narrows the chat: text rewraps, so all this comes before measuring
    loadShelf(); // then renderProjBar
    renderProjBar();
    refreshBars(false); // measured now that it's on screen again (unless it's as it was left); its Hide bars change its height, so scroll after
    // Left at its bottom: the newest reply (Settings > Jump to newest replies), else where it was left
    const restore = () => { if (v.atEnd && prefs.chatjump !== false) chatToEnd(v.box); else v.box.scrollTop = v.scroll; };
    restore();
    requestAnimationFrame(() => { if (chatView === v) restore(); }); // the tab row redraws after this
  }
  connectChat(name);
}

// ---- chats kept on this device (user): each chat's events as they arrived, in IndexedDB, so a reloaded page (the phone
// app coming back) shows a chat at once from its copy and asks its host only for what came after (connectChat's
// log/from); a chat its host started over (a new log) comes whole again and replaces the copy ----
const chatStore = (() => {
  let db = null;
  const open = () => db ||= new Promise((ok, fail) => {
    const r = indexedDB.open("deck-chats", 1);
    r.onupgradeneeded = () => r.result.createObjectStore("chats");
    r.onsuccess = () => ok(r.result); r.onerror = () => fail(r.error);
  });
  const run = async (mode, fn) => {
    const d = await open();
    return new Promise((ok, fail) => {
      const t = d.transaction("chats", mode), req = fn(t.objectStore("chats"));
      t.oncomplete = () => ok(req?.result); t.onerror = () => fail(t.error);
    });
  };
  return {
    get: (k) => run("readonly", (s) => s.get(k)).catch(() => null),
    put: (k, v) => run("readwrite", (s) => s.put(v, k)).catch(() => {}),
    del: (k) => run("readwrite", (s) => s.delete(k)).catch(() => {}),
    keys: () => run("readonly", (s) => s.getAllKeys()).catch(() => []),
  };
})();
const CHAT_COPY_MAX = 25 * 1024 * 1024; // a chat bigger than this isn't kept (it would only slow the save)
const isClear = (batch) => /^\[\{"t":\s*"clear"/.test(batch);
const joinBatches = (bs) => `[${bs.map((b) => b.slice(1, -1)).filter((b) => b.trim()).join(",")}]`;
function keepChat(v, batch) { // a batch from its host: into the copy, saved a moment later
  if (isClear(batch)) { v.copy = []; v.copySize = 0; }
  if (!v.copy) return; // joined mid-log without a copy to add to: nothing whole to keep
  v.copy.push(batch); v.copySize += batch.length;
  clearTimeout(v.copyTimer);
  v.copyTimer = setTimeout(() => {
    if (v.copySize > CHAT_COPY_MAX) { chatStore.del(v.name); v.copy = null; return; }
    if (v.copy.length > 1) v.copy = [joinBatches(v.copy)];
    chatStore.put(v.name, { log: v.log, seen: v.seen, batches: v.copy, at: Date.now() });
  }, 1500);
}
async function restoreChat(v) { // the device's copy, drawn before connecting
  const rec = await chatStore.get(v.name);
  if (!rec?.batches?.length || v.log || !isClear(rec.batches[0])) return;
  const shown = chatView;
  chatView = v;
  try { drawChatBatch(joinBatches(rec.batches), v === shown); } finally { chatView = shown; }
  v.copy = [joinBatches(rec.batches)]; v.copySize = v.copy[0].length;
}
// Copies of chats that were closed go: a host's, only once that host has answered with its list (another host's
// sessions arrive a moment after the page loads, and an offline one's are unknown), checked about once a minute
let copiesPruned = 0;
async function pruneChatCopies() {
  if (Date.now() - copiesPruned < 60000 || !hostsSeen.length) return;
  copiesPruned = Date.now();
  const live = new Set(sessions.map((s) => s.name)), answered = new Set(hostsSeen.filter((x) => x.online === true).map((x) => x.name));
  for (const k of await chatStore.keys()) if (answered.has(hostOf(k)) && !live.has(k)) chatStore.del(k);
}

function connectChat(name) {
  const v = chatCache.get(name);
  if (!v || (v.socket && v.socket.readyState < 2)) return;
  if (!v.restored) { // first: what this device kept of it, then only what came after
    v.restored = true;
    restoreChat(v).catch(() => {}).finally(() => connectChat(name));
    return;
  }
  clearTimeout(reconnectTimer);
  const proto = location.protocol === "https:" ? "wss" : "ws";
  // Drawn before (a dropped connection): the host sends only what came after, unless the log is a new one
  const more = v.log ? `&log=${v.log}&from=${v.seen}` : "";
  const socket = new WebSocket(`${proto}://${location.host}${wsPath(name)}?mode=chat${more}`);
  v.socket = socket;
  if (chatView === v) ws = socket;
  // The host replays the whole log on every connection (it starts with "clear"), then follows it. A parked chat draws
  // into its own (off-screen) view.
  socket.onmessage = (e) => {
    if (v.socket !== socket) return;
    const shown = chatView;
    chatView = v;
    try { drawChatBatch(e.data, v === shown); } finally { chatView = shown; }
    keepChat(v, e.data);
  };
  socket.onclose = () => {
    if (v.socket !== socket) return;
    v.socket = null;
    if (ws === socket) ws = null;
    if (chatView === v && view === "term" && attached === name && document.visibilityState === "visible") {
      reconnectTimer = setTimeout(() => restoreTerminal(), 1500);
    }
  };
}

function drawChatBatch(data, shown) {
  {
    const b = chatView.box, stick = b.scrollHeight - b.scrollTop - b.clientHeight < 60;
    // A batch (the whole log on connecting: a thousand events and more) is drawn at once: a message sent again and
    // again as it grows is drawn only as its last version (earlier ones just hold its place), and the Hide bars,
    // which measure the page, are set once at the end.
    const evs = JSON.parse(data), last = new Map();
    evs.forEach((ev, i) => { if (ev.t === "msg") last.set(ev.m, i); });
    const v = chatView; // where it carries on after a dropped connection: the log's id and how many of its events are drawn
    for (const ev of evs) { if (ev.t === "clear" && ev.log) { v.log = ev.log; v.seen = 0; } v.seen = (v.seen || 0) + 1; }
    chatBatch = true;
    try {
      evs.forEach((ev, i) => chatEvent(ev.t === "msg" && last.get(ev.m) !== i ? { t: "msg", m: ev.m } : ev));
    } finally { chatBatch = false; }
    if (!shown) return; // parked: measured when it's shown again
    refreshBars();
    if (stick) chatToEnd(b);
  }
}

// The chat on screen (a parked one draws while chatView points at it, but isn't shown)
const chatShown = () => (chatShell && $("#term").contains(chatView?.box) && !chatView.box.classList.contains("parked") ? chatView : null);

function chatMsg(m) {
  let el = chatView.msgs.get(m);
  if (!el) {
    el = h("div", { class: "cmsg agent" });
    chatView.msgs.set(m, el);
    chatView.box.append(el);
  }
  return el;
}

// Tool calls show as one line (name and its main argument); tap for the arguments and output.
function toolArg(args) {
  const k = toolKey(args);
  return k ? args[k] : JSON.stringify(args);
}

// Opened, a tool call shows its arguments readably instead of JSON: a command as a shell line (the summary then shows
// its description), an edit as a coloured diff, a written file as its text, anything else as name and value.
const OLD_KEYS = ["old_string", "oldText", "old_str"], NEW_KEYS = ["new_string", "newText", "new_str"];
function toolBody(args) {
  const out = [], hide = new Set(["description", "edits", "replace_all", ...OLD_KEYS, ...NEW_KEYS]);
  const pick = (o, keys) => keys.map((k) => o[k]).find((v) => typeof v === "string");
  if (typeof args.command === "string") { out.push(h("pre", { class: "ccmd" }, args.command)); hide.add("command"); }
  const edits = (Array.isArray(args.edits) ? args.edits : [args]).filter((e) => pick(e, OLD_KEYS) !== undefined && pick(e, NEW_KEYS) !== undefined);
  for (const e of edits) out.push(diffPre(lineDiff(pick(e, OLD_KEYS), pick(e, NEW_KEYS))));
  if (!edits.length && typeof args.content === "string") { out.push(h("pre", {}, args.content)); hide.add("content"); }
  const main = toolKey(args);
  for (const [k, v] of Object.entries(args)) {
    if (hide.has(k) || k === main) continue;
    const s = typeof v === "string" ? v : JSON.stringify(v, null, 2);
    out.push(s.includes("\n") ? h("div", { class: "ckv" }, h("span", {}, k), h("pre", {}, s)) : h("div", { class: "ckv" }, h("span", {}, k), " ", s));
  }
  return out;
}
// A change to the machine notes repo (infra): an edit or write under a folder of its name, or a command that commits
// there; Settings > Infra changes expanded opens these
function notesChange(args) {
  const name = prefs.notesname;
  if (!name) return false;
  const has = (s) => s.split(/[\\/\s"';&|]+/).includes(name);
  const path = ["file_path", "path", "notebook_path"].map((k) => args[k]).find((v) => typeof v === "string");
  const writes = Array.isArray(args.edits) || ["content", "new_source", ...NEW_KEYS].some((k) => typeof args[k] === "string");
  if (path && writes) return has(path);
  return typeof args.command === "string" && has(args.command) && /\bgit\b[^\n]*\bcommit\b|apply_patch|\*\*\* (Update|Add) File/.test(args.command);
}
const toolKey = (args) => ["command", "pattern", "path", "file_path", "url", "query"].find((k) => typeof args[k] === "string");
// Lines of a before/after pair as [sign, line]: the common start and end trimmed to 2 lines of context, the middle
// matched line by line (longest common subsequence) when small, else all removed then all added.
function lineDiff(a, b) {
  const x = a ? a.split("\n") : [], y = b ? b.split("\n") : [];
  let p = 0, s = 0;
  while (p < x.length && p < y.length && x[p] === y[p]) p++;
  while (s < x.length - p && s < y.length - p && x[x.length - 1 - s] === y[y.length - 1 - s]) s++;
  const xm = x.slice(p, x.length - s), ym = y.slice(p, y.length - s), mid = [];
  if (xm.length * ym.length <= 40000) {
    const L = Array.from({ length: xm.length + 1 }, () => new Uint16Array(ym.length + 1));
    for (let i = xm.length - 1; i >= 0; i--) for (let j = ym.length - 1; j >= 0; j--)
      L[i][j] = xm[i] === ym[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    let i = 0, j = 0;
    while (i < xm.length || j < ym.length) {
      if (i < xm.length && j < ym.length && xm[i] === ym[j]) { mid.push([" ", xm[i]]); i++; j++; }
      else if (i < xm.length && (j === ym.length || L[i + 1][j] >= L[i][j + 1])) mid.push(["-", xm[i++]]);
      else mid.push(["+", ym[j++]]);
    }
  } else mid.push(...xm.map((l) => ["-", l]), ...ym.map((l) => ["+", l]));
  const ctx = (ls) => ls.map((l) => [" ", l]);
  return [...(p > 2 ? [["@", "…"]] : []), ...ctx(x.slice(Math.max(0, p - 2), p)), ...mid,
    ...ctx(x.slice(x.length - s, x.length - s + 2)), ...(s > 2 ? [["@", "…"]] : [])];
}
const DIFF_CLASS = { "+": "dadd", "-": "ddel", "@": "dhunk", " ": "" };
const diffPre = (lines, pre = h("pre", { class: "cdiff" })) => {
  pre.replaceChildren(...lines.map(([sign, l]) => h("span", { class: DIFF_CLASS[sign] }, l || " ")));
  return pre;
};
// Tool output; a unified diff (Codex file changes, git diff) is coloured by line.
function setToolOut(pre, text) {
  pre.dataset.raw = text;
  // a message to another session that Deck refused (its answer isn't "sent to NAME") shows as not sent
  if (pre.parentElement?.matches(".csent")) pre.parentElement.classList.toggle("unsent", !/\bsent to \S/.test(text));
  if (!/^(@@ |diff --git |\+\+\+ )/m.test(text)) { pre.classList.remove("cdiff"); pre.textContent = text; return; }
  pre.classList.add("cdiff");
  diffPre(text.replace(/\n$/, "").split("\n").map((l) =>
    [/^(\+\+\+|---|diff |index )/.test(l) ? "@" : l[0] === "+" || l[0] === "-" ? l[0] : l.startsWith("@@") ? "@" : " ", l]), pre);
}

// A file the agent's reply carries (the bridge fetches it to the chat's host, api/chat.py chat_media serves it): an
// image shows, audio and video play, anything else is a download.
function chatFile(b) {
  const size = b.size >= 1e6 ? ` (${(b.size / 1e6).toFixed(1)} MB)` : b.size ? ` (${Math.ceil(b.size / 1e3)} kB)` : "";
  if (!b.file) return h("div", { class: `cfile${b.err ? " err" : ""}` }, `${b.name}${size}: ${b.err || "fetching…"}`);
  const url = route(`/api/sessions/${encodeURIComponent(chatView.name)}/media/${encodeURIComponent(b.file)}`);
  if (/\.(png|jpe?g|gif|webp|avif|bmp)$/i.test(b.file)) {
    return h("a", { class: "cfile", href: url, target: "_blank", rel: "noopener" }, h("img", { src: url, alt: b.name, loading: "lazy" }));
  }
  const play = /^(audio|video)\//.exec(b.mime)?.[1];
  if (play) return h(play, { class: "cfile", src: url, controls: "", preload: "metadata", title: b.name });
  return h("a", { class: "cfile", href: url, download: b.name }, `⤓ ${b.name}${size}`);
}
// Settings > Thinking expanded: a plain block with no arrow to fold it; off: a folded <details>
function thinkBlock(text) {
  return prefs.thinkopen
    ? h("div", { class: "cthink" }, h("span", { class: "cthinkhd" }, "Thinking"), h("div", {}, text))
    : h("details", { class: "cthink" }, h("summary", {}, "Thinking"), h("div", {}, text));
}
function chatBlock(b) {
  if (b.kind === "text") { const el = h("div", { class: "cprose" }); el.innerHTML = chatMarkdown(b.text); return el; }
  if (b.kind === "think") return thinkBlock(b.text);
  if (b.kind === "file") return chatFile(b);
  const args = b.args || {}, desc = typeof args.description === "string" && typeof args.command === "string" ? args.description : "";
  const sent = sentTo(args, hostOf(chatView.name)); // a message to another session: shown as one, tools hidden or not
  const el = sent ? h("div", { class: "csent" }, peerLine("To", sent.to), h("span", {}, sent.text || "(message in the command)"),
    h("pre", { class: "cout" }))
    : h("details", { class: "ctool" }, h("summary", {}, h("b", {}, b.name), " ",
    h("span", { class: desc ? "carg cmd" : "carg" }, toolArg(args)), desc ? h("span", { class: "cdesc" }, desc) : ""),
    ...toolBody(args), h("pre", { class: "cout" }));
  if (!sent && notesChange(args)) { el.classList.add("cnotes"); el.open = !!prefs.notesopen; }
  const old = chatView.tools.get(b.id); // the message was sent again (a later block): keep its output and open state
  if (old) {
    const o = old.querySelector(".cout");
    setToolOut(el.querySelector(".cout"), o.dataset.raw ?? o.textContent);
    el.className = old.className; el.open = old.open;
  } else if (chatView.outs?.has(b.id)) { // its output came before its call was drawn
    const done = chatView.outs.get(b.id);
    chatView.outs.delete(b.id);
    setToolOut(el.querySelector(".cout"), done.out || "(no output)");
    el.classList.toggle("err", !!done.err);
  }
  chatView.tools.set(b.id, el);
  return el;
}

// An answered card (pressed here, in the terminal, or replaced, like a "You should know" card's): its buttons go and
// a line says what was chosen (when chosen here), else how it ended (how: the bridge's word, e.g. "Expired")
function askDone(card, choice, how) {
  const row = card?.lastElementChild;
  if (!row?.querySelector("button")) return;
  row.replaceWith(h("div", { class: "cdone" }, choice ? `→ ${choice.trim()}` : how || "Answered"));
}
function clearChatFailure(v) {
  if (v.failure) {
    v.failure.querySelector("button").disabled = true;
    v.failure.querySelector("button").textContent = "Earlier error";
    v.failure = null;
  }
}
function syncChatFailure(v) {
  if (!v.failure) return;
  const retry = v.failure.querySelector("button");
  if (retry.requested) return;
  retry.disabled = !!v.working;
  retry.textContent = v.working ? "Agent still working…" : "Try again";
  v.failure.querySelector("span").textContent = v.working
    ? "Stop the agent before trying again or choosing another model."
    : "Try again or choose another model below.";
}
function chatFailure(v, text) {
  const retry = h("button", { type: "button", title: "Ask the agent to continue from where it stopped", onclick: () => {
    if (v.failure !== card || v.working || retry.disabled) return;
    if (v.socket?.readyState !== 1) { toast("Chat disconnected. Reconnecting…"); connectChat(v.name); return; }
    v.socket.send(JSON.stringify({ t: "say", d: "Please continue from where you stopped.", via: VIA }));
    retry.requested = true; retry.disabled = true; retry.textContent = "Retry requested…";
  } }, "Try again");
  const card = h("div", { class: "cfailure", role: "alert" },
    h("strong", {}, "Agent request failed"), h("div", { class: "cfailuretext" }, text),
    h("div", { class: "cfailureacts" }, retry,
      h("span", {}, "Try again or choose another model below.")));
  // Some agents report the same failure through both a hook and their transcript. Keep one current card.
  if (v.failure?.isConnected) v.failure.replaceWith(card);
  else v.box.append(card);
  v.failure = card;
  syncChatFailure(v);
}
function chatEvent(ev) {
  const v = chatView, box = v.box;
  if (ev.t === "clear") { box.replaceChildren(); v.failure = null; v.working = false; v.msgs.clear(); v.tools.clear(); v.outs?.clear(); v.turn = 0; deckContextBlock(v); }
  else if (ev.t === "hidden") box.append(debugBlock(ev.why, ev.text)); // debug view only
  else if (ev.t === "user") { // the agent took it in: the ones it carries stop showing as sending
    clearChatFailure(v);
    const flat = (s) => s.replace(/\s+/g, " ").trim(), said = flat(ev.text);
    for (const q of box.querySelectorAll(".csending")) if (said.includes(flat(q.firstChild.textContent))) q.remove();
    box.append(userBubble(ev.text, ev.ts, AGENT_FROM.test(ev.from || "") ? ev.from : ""));
    if (ev.raw) box.append(debugBlock("as the agent got it", ev.raw, true));
  }
  else if (ev.t === "delta") { // streamed text, plain until the finished message replaces it
    const el = chatMsg(ev.m), key = `${ev.kind}${ev.i}`;
    let blk = [...el.children].find((c) => c.dataset.k === key);
    if (!blk) {
      blk = h("div", { class: ev.kind === "text" ? "cprose live" : "cthink live", "data-k": key });
      el.append(blk);
    }
    blk.textContent += ev.d;
  } else if (ev.t === "msg") {
    const el = chatMsg(ev.m);
    if (ev.blocks) { // none: a version a later one in this batch replaces; it only holds the message's place
      el.replaceChildren(...ev.blocks.map(chatBlock));
      el.blocks = ev.blocks; // for Save turn / Save messages
    }
  } else if (ev.t === "tool_end") {
    const el = v.tools.get(ev.id);
    if (el) {
      setToolOut(el.querySelector(".cout"), ev.out || "(no output)");
      el.classList.toggle("err", !!ev.err);
    } else (v.outs ||= new Map()).set(ev.id, ev); // its call isn't drawn yet (later in this batch): kept for it
  } else if (ev.t === "state") {
    v.working = ev.s === "working";
    if (v.working) clearChatFailure(v);
    else syncChatFailure(v);
    v.status.hidden = ev.s !== "working";
    const acts = ev.s !== "working" && ev.ts && box.querySelector(`.cturnacts[data-acts="${v.turn}"]`);
    if (acts) { // when the reply finished (just the time: user)
      (acts.querySelector(".cts") || acts.appendChild(h("span", { class: "cts" }))).textContent = stampTime(ev.ts);
    }
  }
  else if (ev.t === "menu") { if (menuReq?.rid === ev.rid) fillMenu(ev); } // the tool row's menu (openMenu)
  else if (ev.t === "status" && (v.lastStatus = ev) && v !== chatShown()) { /* parked: shown when it is */ }
  else if (ev.t === "status") { // model, effort, context, limits, mode: the same chips as a terminal agent's status line
    const bar = $("#agentbar");
    // a PC has room: the parts as the agent words them (user); a phone gets the short forms
    const mode = (wide.matches ? { bypassPermissions: "bypass permissions", acceptEdits: "accept edits", default: "" }
      : { bypassPermissions: "bypass", acceptEdits: "edits", default: "" })[ev.mode] ?? ev.mode;
    const chips = [...(wide.matches ? ev.parts : ev.parts.map(shortPart)), mode].filter(Boolean);
    bar.hidden = !chips.length;
    bar.replaceChildren(...chips.map((p, i) =>
      h("span", { class: `fpart${i === 0 ? " model" : ""}${mode && i === chips.length - 1 ? " mode" : ""}` }, p)));
  }
  else if (ev.t === "note") {
    if (ev.err) chatFailure(v, ev.text);
    else box.append(h("div", { class: "cnote" }, ev.text));
  }
  else if (ev.t === "ask") { // an agent's question: the answer goes back to it. multi: options toggle, then Submit;
    // other: a box for an answer of your own (the message box answers it too); skip: decline (null)
    const answer = (value, shown) => { ws?.send(JSON.stringify({ t: "answer", id: ev.id, value })); askDone(card, shown); };
    const title = h("div", { class: "cprose" });
    title.innerHTML = chatMarkdown(ev.title);
    const picked = new Set();
    const submit = ev.multi && h("button", { type: "button", class: "csubmit", disabled: true,
      onclick: () => { const v = ev.options.filter((o) => picked.has(o)); answer(v, v.join(", ")); } }, "Submit");
    const opts = ev.options.map((o) => h("button", { type: "button", "aria-pressed": ev.multi ? "false" : null, onclick: (e) => {
      if (String(ev.id).startsWith("ysk-") && o === "Chat about it") attachCard(ev.title);
      if (!ev.multi) return answer(o, o);
      picked.has(o) ? picked.delete(o) : picked.add(o);
      e.currentTarget.setAttribute("aria-pressed", String(picked.has(o)));
      submit.disabled = !picked.size;
    } }, o));
    const own = ev.other && h("input", { type: "text", class: "cother", placeholder: "Type something…",
      onkeydown: (e) => { if (e.key === "Enter" && own.value.trim()) { e.preventDefault(); answer({ other: own.value.trim() }, own.value); } } });
    const skip = ev.skip && h("button", { type: "button", class: "cskip", onclick: () => answer(null, "Skipped") }, "Skip");
    const card = h("div", { class: "cask", "data-id": ev.id }, title,
      h("div", {}, ...opts, ...[submit, own, skip].filter(Boolean)));
    box.append(card);
  } else if (ev.t === "answered") {
    for (const c of box.querySelectorAll(`.cask[data-id="${CSS.escape(ev.id)}"]`)) askDone(c, "", ev.how);
  } else if (ev.t === "queued") { // typed while it works: gone to the agent, waiting till it takes it in
    const waiting = () => [...box.querySelectorAll(".csending")];
    const act = (label, then) => h("button", { type: "button", onclick: () => {
      waiting().forEach((q) => q.querySelectorAll(".qacts button").forEach((b) => { b.disabled = true; }));
      then();
    } }, label);
    // Claude's own queue: Send now (all of them, now), Edit (all of them back, into the message box)
    // (another session's agent's message has no Edit of its own; the user's Edit brings it back marked as that agent's)
    const acts = ev.keys ? [act("Send now", () => ws?.send(JSON.stringify({ t: "sendnow" }))),
      ...ev.from ? [] : [act("Edit", () => {
        const msg = $("#msg"), texts = waiting().map((q) => (q.dataset.from ? `[from ${q.dataset.from}] ` : "") + q.firstChild.textContent);
        ws?.send(JSON.stringify({ t: "unhold" }));
        msg.value = [...texts, msg.value].filter(Boolean).join("\n");
        msg.oninput?.({ target: msg });
        msg.focus();
      })]] : [h("span", { class: "qnote" }, "Sending…")];
    box.append(h("div", { class: `cmsg user cqueued csending${ev.from ? " cpeer" : ""}`, "data-id": ev.id,
      ...(ev.from ? { "data-from": ev.from } : {}) }, h("span", {}, splitAttached(ev.text)[0]), attachedFold(splitAttached(ev.text)[1]), ev.from ? peerLine("From", ev.from) : "",
      h("div", { class: "qacts" }, ...acts)));
  } else if (ev.t === "unqueued") box.querySelector(`.cqueued[data-id="${CSS.escape(ev.id)}"]`)?.remove();
  if (ev.t === "user") { // each reply gets a bar under its message that folds the reply away
    const n = v.turn = (v.turn || 0) + 1;
    box.append(h("button", { type: "button", class: "cturn", "data-for": n, onclick: () => foldTurn(n) }));
    const save = (kind, label) => h("button", { type: "button", onclick: (e) => saveTurn(n, kind, e.target) }, label);
    box.append(h("div", { class: "cturnacts", "data-acts": n, hidden: "" },
      h("button", { type: "button", class: "cturnhide", onclick: () => foldTurn(n) }, "Hide reply"),
      save("turn", "Save turn"), save("nl", "Save messages")));
    if (!chatBatch) turnBar(n);
  }
  if (v.turn && /^(delta|msg|note|ask)$/.test(ev.t)) { // the reply's new parts join its turn (folded, if it is)
    const fresh = []; // new parts are at the end: walk back to the last tagged part, the message or its bar
    for (let el = box.lastElementChild; el && !el.dataset.turn && !el.matches(".cturn, .cmsg.user:not(.cqueued)"); el = el.previousElementSibling) {
      if (!el.matches(".cturnacts, .cqueued")) fresh.push(el);
    }
    for (const el of fresh) {
      el.dataset.turn = v.turn;
      el.classList.toggle("folded", box.querySelector(`.cturn[data-for="${v.turn}"]`)?.classList.contains("shut"));
    }
    const acts = box.querySelector(`.cturnacts[data-acts="${v.turn}"]`);
    if (acts) { // its buttons stay at the turn's end, and show once it has something in it (text or a tool call)
      if (acts.hidden) acts.hidden = ![...box.querySelectorAll(`:scope > [data-turn="${v.turn}"]`)]
        .some((el) => el.textContent.trim() || el.querySelector(".ctool"));
      box.append(acts);
    }
    if (!chatBatch) turnBar(v.turn);
  }
  for (const q of box.querySelectorAll(".cqueued")) if (q !== box.lastElementChild) box.append(q); // sending ones stay last
}
// A sent message: tap it for "Back to this", which puts "Let's go back to this" and the message in the message box to
// edit and send (nothing is sent by itself).
// A chat timestamp: the time today, else the date too
function stampTime(ts) {
  const d = new Date(ts * 1000), t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return d.toDateString() === new Date().toDateString() ? t : `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${t}`;
}
// Debug view (Settings): what Deck adds and the chat leaves out, dimmed and labelled; hidden otherwise (style.css)
function debugBlock(why, text, open = false) {
  return h("details", { class: "cdebug", ...(open ? { open: "" } : {}) }, h("summary", {}, `Deck debug · ${why}`), h("pre", {}, text));
}
// The instructions Deck gives the agent, at the top of each chat (from the chat's own host)
function deckContextBlock(v) {
  const block = debugBlock("Deck's instructions to the agent", "…");
  v.box.prepend(block);
  api("GET", `${viaHost(v.name)}/api/deck-context`).then(({ text }) => { block.querySelector("pre").textContent = text; })
    .catch(() => { block.querySelector("pre").textContent = "(couldn't load them from this chat's host)"; });
}
// Messages between sessions (/api/agent/sessions/NAME/send): one from another session's agent is outlined, with
// "From" and that session above it (from: "agent in HOST/NAME" or "another agent", chat_bridge.AGENT_FROM); one this
// agent sent shows the same way, "To" (chatBlock). The session is a tap away while it's open.
const AGENT_FROM = /^(agent in .+|another agent)$/;
function peerLine(dir, who) {
  const full = who.replace(/^agent in /, ""), s = full.includes("/") && sessions.find((x) => x.name === full);
  const name = s ? `${s.title || s.label}${hostOf(full) !== HOME_HOST ? ` · ${hostOf(full)}` : ""}` : full;
  return h("div", { class: "cfrom" }, `${dir} `, s ? h("button", { type: "button", class: "csess", title: full,
    onclick: (e) => { e.stopPropagation(); openSession(full); } }, name) : h("span", {}, name));
}
// A tool call that messages another session: whom to (HOST/NAME) and what (best effort: curl's JSON, PowerShell's
// @{text=...}); null for any other call
const SEND_RE = /(?:\/peer\/([^/\s'"]+))?\/api\/agent\/sessions\/([^/\s'"?]+)\/send/;
function sentTo(args, host) {
  const m = typeof args.command === "string" && args.command.match(SEND_RE);
  if (!m) return null;
  const c = args.command.replace(/'\\''/g, "'").replace(/'"(\$\{?\w+\}?)"'/g, "$1"); // a ' or a $VAR inside a shell's '...'
  let text = "", t = c.match(/"text"\s*:\s*"((?:[^"\\]|\\.)*)"/);
  try { text = t ? JSON.parse(`"${t[1]}"`) : ""; } catch { text = t[1]; }
  if (!t && (t = c.match(/\btext\s*=\s*(?:'((?:[^']|'')*)'|"([^"]*)")/))) text = t[1] !== undefined ? t[1].replace(/''/g, "'") : t[2];
  return { to: `${m[1] ? decodeURIComponent(m[1]) : host}/${decodeURIComponent(m[2])}`, text };
}
function userBubble(text, ts, from = "") {
  // A slash command or an interruption isn't a message: a small centred label (user); still a user message to the
  // turn logic, so a command that starts work heads its turn as a message would
  const t = text.trim(), [said, rest] = splitAttached(text);
  if (/^\/[a-z][\w:-]*(\s[^\n]*)?$/i.test(t) || /^\[Request interrupted by user/.test(t)) {
    return h("div", { class: "cmsg user cslash" }, h("span", {}, t.startsWith("[") ? "Interrupted" : t),
      ts ? h("span", { class: "cts" }, `· ${stampTime(ts)}`) : "");
  }
  const back = h("button", { type: "button", onclick: (e) => {
    e.stopPropagation();
    const msg = $("#msg");
    msg.value = `Let's go back to this:\n"${said}"\n`;
    msg.oninput({ target: msg });
    msg.focus();
    msg.setSelectionRange(msg.value.length, msg.value.length);
    el.classList.remove("open");
  } }, "↩ Back to this");
  const el = h("div", { class: `cmsg user${from ? " cpeer" : ""}`, onclick: () => {
    if (!getSelection().isCollapsed) return; // selecting text to copy, not tapping
    el.classList.toggle("open");
  } }, h("span", {}, said), attachedFold(rest), from ? peerLine("From", from) : "", ts ? h("div", { class: "cts" }, stampTime(ts)) : "",
  h("div", { class: "uacts" }, back));
  return el;
}
// Shelf items sent with a message (withAttached) fold shut under it, one line naming them; a tap opens them (user)
const ATTACHED = "\n\n---\nAttached:\n\n";
function splitAttached(text) {
  const cut = text.indexOf(ATTACHED);
  return cut < 0 ? [text, ""] : [text.slice(0, cut), text.slice(cut + ATTACHED.length).trim()];
}
function attachedFold(rest) {
  if (!rest) return "";
  const titles = [...rest.matchAll(/^## \d+\. (.+)$/gm)].map((m) => m[1]);
  const body = h("div", { class: "cprose" });
  body.innerHTML = chatMarkdown(rest);
  return h("details", { class: "cattached", onclick: (e) => e.stopPropagation() },
    h("summary", {}, `Attached (${titles.length || 1}): ${titles.join(", ") || "shelf items"}`), body);
}
function foldTurn(n) {
  const box = chatView.box, bar = box.querySelector(`.cturn[data-for="${n}"]`), shut = !bar.classList.contains("shut");
  bar.classList.toggle("shut", shut);
  for (const el of box.querySelectorAll(`:scope > [data-turn="${n}"], .cturnacts[data-acts="${n}"]`)) el.classList.toggle("folded", shut);
  turnBar(n);
  if (shut && bar.getBoundingClientRect().top < box.getBoundingClientRect().top) bar.scrollIntoView({ block: "start" });
}
let chatBatch = false; // drawing a batch of events: the Hide bars wait for refreshBars at its end
function turnBar(n) {
  const bar = chatView.box.querySelector(`.cturn[data-for="${n}"]`);
  if (bar) setBars([measureBar(bar)]);
}
// Every reply's Hide bar, all measured first and then set: one layout for the page, not one per bar. The chat's parts
// are grouped by turn in one pass (a long chat has thousands, and a search per bar made switching to it slow), and a
// chat back at the width and font it was left at keeps its bars as they are.
function refreshBars(force = true) {
  const v = chatView, box = v.box, font = parseFloat(getComputedStyle(box).fontSize), key = `${box.clientWidth}/${font}`;
  if (!force && v.barsKey === key) return;
  v.barsKey = key;
  const parts = new Map();
  for (const el of box.children) if (el.dataset.turn) (parts.get(el.dataset.turn) || parts.set(el.dataset.turn, []).get(el.dataset.turn)).push(el);
  setBars([...box.querySelectorAll(":scope > .cturn")].map((bar) => measureBar(bar, parts.get(bar.dataset.for) || [], font)));
}
// A reply's parts and height. Its bar goes above it only when it's long (12 lines or more on screen); a short one has
// just the Hide under it. A hidden reply keeps its bar, to open it again.
function measureBar(bar, els = chatView.box.querySelectorAll(`:scope > [data-turn="${bar.dataset.for}"]`),
  font = parseFloat(getComputedStyle(chatView.box).fontSize)) {
  const parts = els.length;
  let long = true;
  if (parts && !bar.classList.contains("shut")) {
    const drawn = [...els].map((el) => el.getBoundingClientRect()).filter((r) => r.height); // empty parts take no space
    const height = drawn.length ? Math.max(...drawn.map((r) => r.bottom)) - Math.min(...drawn.map((r) => r.top)) : 0;
    long = height >= 12 * 1.45 * font;
  }
  return { bar, parts, long };
}
function setBars(list) {
  for (const { bar, parts, long } of list) {
    bar.hidden = !parts || !long;
    bar.textContent = bar.classList.contains("shut") ? `Reply hidden · ${parts} part${parts === 1 ? "" : "s"}` : "Hide reply";
  }
}
