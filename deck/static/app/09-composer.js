// ---- the message box and its tools: slash keys and menus, Harness, Brain, saves, Move host, Send, dictation,
// drafts, and the shelf items attached to the next message ----
// ---- the tool row: slash-command keys and their menus ----
// Slash-command keys (Claude/Codex): typed and submitted like a message.
for (const k of document.querySelectorAll("#harness button[data-cmd], .msgbar button[data-cmd]")) k.onclick = () => {
  showHarness(false);
  if (k.closest(".msgbar") && chatShell) { if (menuReq?.btn === k) closeMenu(); else openMenu(k); return; }
  if (!say(k.dataset.cmd)) toast("Terminal not connected yet");
};
// The tool row's Resume, Model and Effort in a chat: the bridge runs the command quietly and sends its choices back
// (chatEvent "menu", matched by request id: replays of old ones are ignored); they show as a small menu on the button,
// and a choice answers like a chat card's button would. Nothing goes into the chat.
function openMenu(btn) {
  closeMenu();
  if (ws?.readyState !== 1) { toast("Chat not connected yet"); return; }
  const rid = `m${Date.now()}`, pop = $("#msgmenu");
  menuReq = { rid, btn };
  btn.setAttribute("aria-expanded", "true");
  pop.replaceChildren(h("div", { class: "mmnote" }, "…"));
  pop.hidden = false;
  placeMenu();
  ws.send(JSON.stringify({ t: "menu", text: btn.dataset.cmd, rid }));
  setTimeout(() => { // a chat started before menus existed ignores the request
    if (menuReq?.rid === rid && !pop.querySelector("button")) {
      pop.replaceChildren(h("div", { class: "mmnote" }, "No answer: Harness → Restart gives this chat the menus"));
      placeMenu();
    }
  }, 4000);
}
function fillMenu(ev) {
  const pop = $("#msgmenu");
  pop.replaceChildren(...(ev.options?.length ? [
    ...(ev.title ? [h("div", { class: "mmtitle" }, ev.title)] : []),
    ...ev.options.map((o) => h("button", { type: "button", onclick: () => {
      ws?.send(JSON.stringify({ t: "answer", id: ev.id, value: o }));
      closeMenu();
    } }, o.trim()))] : [h("div", { class: "mmnote" }, ev.message || "Nothing to choose from")]));
  placeMenu();
}
function placeMenu() { // above the box, lined up with its button (the right-hand ones by their right edge)
  const { btn } = menuReq || {}, pop = $("#msgmenu"), box = pop.parentElement;
  if (!btn) return;
  const b = btn.getBoundingClientRect(), o = box.getBoundingClientRect(); // the key row scrolls: measure on screen
  const right = !!btn.closest(".mbr") && wide.matches, l = b.left - o.left, r = o.right - b.right;
  pop.style.left = right ? "auto" : `${l}px`;
  pop.style.right = right ? `${Math.max(0, r)}px` : "auto";
}
function closeMenu() {
  $("#msgmenu").hidden = true;
  menuReq?.btn.setAttribute("aria-expanded", "false");
  menuReq = null;
}
function showHarness(on) {
  // Chat sessions: New and Model (the bridge's /new, /model) in place of Save, which reads a terminal
  for (const b of document.querySelectorAll("#harness .chatonly")) b.hidden = !chatView;
  $("#savesess").hidden = !!chatView;
  $("#sampling").hidden = sessions.find((x) => x.name === attached)?.type !== "opencode";
  $("#harness").hidden = !on;
  $("#harnessbtn").setAttribute("aria-expanded", on);
}
$("#harnessbtn").onclick = () => showHarness($("#harness").hidden);
// Harness → Sampling: OpenCode's sampling settings on the open session's host (its Settings, deck/settings.py SAMPLING);
// each field saves on change, "" clears it
$("#sampling").onclick = async () => {
  showHarness(false);
  const at = viaHost(attached), f = $("#sampform").elements;
  $("#samperr").textContent = "";
  $("#samphost").textContent = hostOf(attached);
  try {
    const { sampling = {} } = await api("GET", `${at}/api/settings`);
    for (const a of ["plan", "build"]) for (const k of ["temperature", "top_p", "top_k"]) f[`${a}-${k}`].value = sampling[a]?.[k] ?? "";
  } catch (err) { toast(err.message); return; }
  $("#sampdlg").dataset.at = at;
  $("#sampdlg").showModal();
};
$("#sampform").addEventListener("change", async (e) => {
  const [agent, k] = e.target.name.split("-");
  $("#samperr").textContent = "";
  try {
    await api("PUT", `${$("#sampdlg").dataset.at}/api/settings`, { sampling: { [agent]: { [k]: e.target.value.trim() } } });
  } catch (err) { $("#samperr").textContent = err.message; }
});
$("#sampform").onsubmit = (e) => { e.preventDefault(); document.activeElement?.blur(); };
$("#sampclose").onclick = () => $("#sampdlg").close();
// Harness → Project: a folder (path-like) or a project's name; Starting folder's drops the override
$("#projectbtn").onclick = () => {
  showHarness(false);
  if (!attached) return;
  const known = new Set([...sessions.map((s) => s.project), ...shelfLive.filter((x) => x.main).map((x) => x.project)].filter(Boolean));
  $("#projlist").replaceChildren(...[...known].sort().map((p) => h("option", { value: p })));
  $("#projform").elements.project.value = sessions.find((x) => x.name === attached)?.project || "";
  $("#projerr").textContent = "";
  $("#projdlg").dataset.name = attached;
  $("#projdlg").showModal();
};
async function saveProject(value) {
  const v = value.trim(), body = /^([~/]|[A-Za-z]:[\\/])/.test(v) ? { folder: v } : { project: v };
  try {
    await api("PUT", `/api/sessions/${$("#projdlg").dataset.name}/project`, body);
  } catch (err) { $("#projerr").textContent = err.message; return; }
  $("#projdlg").close();
  poll();
}
$("#projform").onsubmit = (e) => { e.preventDefault(); saveProject(e.target.elements.project.value); };
$("#projreset").onclick = () => saveProject("");
$("#projclose").onclick = () => $("#projdlg").close();
// Brain (beside Resume): a menu for the brain chat, the session working in the Brain's data repo (Settings → Brain data,
// a project such as github.com/owner/second-brain; a chat started in its folder). Select messages: tap chat messages to
// pick several, then Send to Brain asks the brain chat which source they come from (it ingests nothing). Lint wiki and
// Ingest newest go to the brain chat too, not the open one.
const BRAIN_ASKS = {
  find: "These messages are from another Deck chat. Do not ingest them or edit anything. Find the source they come from or "
    + "rely on (a paper, article, page or file: check raw/ and wiki/ first, then search). Tell me which source it is, with "
    + "its link or path, or say you are unsure and why.",
  lint: "Lint the wiki: run python3 sb.py lint, fix what it reports, and note the pass in wiki/log.md.",
  ingest: "Ingest the newest file in raw/ that no wiki page cites yet: update the wiki pages, index.md, log.md and "
    + "overview.md, and cite raw/<file> for every claim.",
};
let picking = false;
function setPicking(on) {
  picking = on;
  const box = chatView?.box;
  box?.classList.toggle("picking", on);
  if (!on) for (const m of box?.querySelectorAll(".picked") ?? []) m.classList.remove("picked");
  $("#selbar").hidden = !on;
  countPicked();
}
function countPicked() {
  $("#selcount").textContent = `${chatView?.box.querySelectorAll(".cmsg.picked").length ?? 0} selected`;
}
function msgText(el) { // a user message's text, or an agent message's prose (not its tool calls)
  if (el.classList.contains("user")) return el.firstChild?.textContent ?? "";
  return [...el.querySelectorAll(".cprose")].map((p) => p.textContent).join("\n\n");
}
document.addEventListener("click", (e) => { // capture: a picked message does nothing else while picking
  if (!picking) return;
  const m = e.target.closest(".cmsg");
  if (!m || !chatView?.box.contains(m)) return;
  e.preventDefault();
  e.stopPropagation();
  m.classList.toggle("picked");
  countPicked();
}, true);
async function toBrain(text) {
  if (!prefs.brain) { toast("Set Settings → Brain data first"); return false; }
  const s = sessions.find((x) => x.project === prefs.brain);
  if (!s) { toast("No brain chat: start one in the Brain data folder"); return false; }
  try {
    await api("POST", `${viaHost(s.name)}/api/agent/sessions/${encodeURIComponent(realName(s.name))}/send`,
      { text, from: "Deck Brain" });
    toast("Sent to the brain chat");
    return true;
  } catch (err) { toast(`Not sent: ${err.message}`); return false; }
}
$("#brainbtn").onclick = () => {
  const btn = $("#brainbtn"), pop = $("#msgmenu");
  if (menuReq?.btn === btn) return closeMenu();
  closeMenu();
  menuReq = { rid: "brain", btn };
  btn.setAttribute("aria-expanded", "true");
  const item = (label, fn) => h("button", { type: "button", onclick: () => { closeMenu(); fn(); } }, label);
  pop.replaceChildren(
    item("Select messages", () => chatView ? setPicking(true) : toast("Select messages works in a chat")),
    item("Lint wiki", () => toBrain(BRAIN_ASKS.lint)),
    item("Ingest newest", () => toBrain(BRAIN_ASKS.ingest)));
  pop.hidden = false;
  placeMenu();
};
$("#selcancel").onclick = () => setPicking(false);
$("#selsend").onclick = async () => {
  const picked = [...chatView.box.querySelectorAll(".cmsg.picked")];
  if (!picked.length) return toast("Tap some messages first");
  const said = picked.map(msgText).filter(Boolean).join("\n\n---\n\n");
  if (await toBrain(`${BRAIN_ASKS.find}\n\n${said}`)) setPicking(false);
};

// ---- Harness: the session's own controls ----
// Restart the agent process in this session, continuing its conversation (picks up new config, plugins, updates).
$("#restartagent").onclick = async () => {
  showHarness(false);
  if (!attached) return toast("No session open");
  try { await api("POST", `/api/sessions/${encodeURIComponent(attached)}/restart`); toast("Restarting…"); }
  catch (e) { toast(e.message); }
};
// Copy text: touch on the terminal scrolls, so text is selected and copied in a dialog instead (newest at the bottom).
$("#copytext").onclick = async () => {
  showHarness(false);
  if (!attached) return toast("No session open");
  try {
    const { text } = await api("GET", `/api/sessions/${encodeURIComponent(attached)}/text`);
    const body = $("#copybody");
    body.value = text;
    $("#copy").showModal();
    body.scrollTop = body.scrollHeight;
  } catch (e) { toast(e.message); }
};
// ---- saved sessions: Save asks the agent for a handoff note (kept on every Deck host); Saves lists them ----
const inAgent = () => !!attached && AGENT_TYPES.includes(sessions.find((x) => x.name === attached)?.type);
$("#savesess").onclick = async () => {
  showHarness(false);
  if (!inAgent()) return toast("Open a Claude, Codex, OpenCode, Pi, agy or OpenClaw session to save it");
  try {
    await api("POST", `/api/sessions/${encodeURIComponent(attached)}/save`);
    toast("Asked the agent for a handoff note: it shows in Saves on every host once written");
  } catch (e) { toast(e.message); }
};
async function renderSaves() {
  const list = await api("GET", "/api/saves").catch(() => []);
  const canLoad = inAgent();
  const row = (s) => h("div", { class: "hostrow saverow" },
    h("b", {}, s.title),
    h("span", {}, `${s.host} · ${s.agent} · ${s.deleted ? `deleted ${since(s.deleted)}` : since(s.created)} ago`),
    s.preview ? h("p", {}, s.preview) : null,
    h("div", { class: "saveacts" }, s.deleted
      ? h("button", { type: "button", class: "ghost small", onclick: () => setSaveDeleted(s, false) }, "Restore")
      : [h("button", { type: "button", class: "ghost small", onclick: () => setSaveDeleted(s, true) }, "Delete"),
        canLoad ? h("button", { type: "button", class: "primary small", onclick: () => loadSave(s) }, "Load") : null]));
  const live = list.filter((s) => !s.deleted), gone = list.filter((s) => s.deleted);
  $("#savelist").replaceChildren(...(live.length ? live.map(row)
    : [h("p", { class: "hint" }, "No saves yet. In an agent session, Harness → Save writes one.")]));
  $("#savedeleted").hidden = !gone.length;
  $("#savedeleted summary").textContent = `Deleted (${gone.length})`;
  $("#savedellist").replaceChildren(...gone.map(row));
}
async function setSaveDeleted(s, deleted) {
  try { await api("POST", `/api/saves/${s.id}/delete`, { deleted }); } catch (e) { toast(e.message); }
  renderSaves();
}
async function loadSave(s) {
  try {
    await api("POST", `/api/sessions/${encodeURIComponent(attached)}/load`, { id: s.id });
    $("#saves").close();
    toast(`Loaded "${s.title}" into this session`);
  } catch (e) { toast(e.message); }
}
$("#loadsess").onclick = () => { showHarness(false); renderSaves(); $("#saves").showModal(); };
// Move host: the chat carries on on another host (deck/session_move.py), told it moved; this tab closes into Trash
$("#movesess").onclick = async () => {
  showHarness(false);
  const s = sessions.find((x) => x.name === attached);
  if (!s || !s.chat || !AGENT_TYPES.includes(s.type)) return toast("Move host works in agent chats");
  await loadHosts();
  const others = hostList.filter((x) => x.name !== s.host);
  $("#movelist").replaceChildren(...(others.length ? others.map((x) => h("button", { type: "button", class: "hostrow",
    onclick: () => moveTo(x.name) }, h("b", {}, x.name), x.aka ? h("span", {}, x.aka) : null))
    : [h("p", { class: "hint" }, "No other hosts.")]));
  $("#movehost").showModal();
};
async function moveTo(host) {
  const name = attached;
  $("#movelist").replaceChildren(h("p", { class: "hint" }, `Moving to ${host}…`));
  try {
    const r = await api("POST", `/api/sessions/${encodeURIComponent(name)}/move`, { host });
    $("#movehost").close();
    toast(`Moved to ${host}: open it there${r.warning ? `. ${r.warning}` : ""}`);
    await poll(); loadTabTrash();
  } catch (e) { $("#movehost").close(); toast(e.message); }
}
$("#moveclose").onclick = () => $("#movehost").close();
$("#savesclose").onclick = () => $("#saves").close();
document.addEventListener("selectionchange", () => {
  const body = $("#copybody");
  if ($("#copy").open) $("#copyall").textContent = body.selectionEnd > body.selectionStart ? "Copy selection" : "Copy all";
});
$("#copyall").onclick = async () => {
  const body = $("#copybody");
  const part = body.selectionEnd > body.selectionStart ? body.value.slice(body.selectionStart, body.selectionEnd) : body.value;
  try { await navigator.clipboard.writeText(part); }
  catch { body.select(); document.execCommand("copy"); }
  toast(part === body.value ? "Copied all" : "Copied selection");
};
// Any tap outside the Harness pop-up or a menu, or Escape, closes it.
document.addEventListener("pointerdown", (ev) => {
  if (!$("#harness").hidden && !ev.target.closest("#harness, #harnessbtn")) showHarness(false);
  if (menuReq && !ev.target.closest("#msgmenu, .msgbar button[data-cmd], #brainbtn")) closeMenu();
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && menuReq) closeMenu(); });
// ---- Send ----
// Touch: send on finger lift, closing the keyboard. Waiting for click is flaky
// on phones (IME commits and viewport resizes swallow it, needing several taps).
let touchSentAt = 0;
const sendBtn = $("#say button[type=submit]");
const isTouch = (ev) => ev.pointerType === "touch" || ev.pointerType === "pen";
sendBtn.addEventListener("pointerdown", (ev) => { if (isTouch(ev)) ev.preventDefault(); });
sendBtn.addEventListener("pointerup", (ev) => {
  if (!isTouch(ev)) return;
  ev.preventDefault();
  touchSentAt = Date.now();
  // Drop the keyboard (so the terminal is visible) before reading the box: that ends its composition, the word that
  // autocorrect / suggestions are still editing. Cleared mid-composition, the keyboard put old text back.
  $("#msg").blur();
  submitMsg();
});
$("#say").onsubmit = (ev) => {
  ev.preventDefault();
  if (ev.submitter && Date.now() - touchSentAt < 800) return; // the click that follows a touch send
  submitMsg();
};
function submitMsg() {
  const msg = $("#msg"), typed = withAttached(msg.value);
  if (chatView && !typed.trim()) return; // a chat takes no empty message (a terminal's is its Enter key)
  const text = withReader(typed);
  if (!say(text)) { toast("Terminal not connected yet"); return; }
  msg.value = ""; fitMsg(msg); keepDraft();
  attachedItems = [];
  renderAttached();
}

// Touch: only a tap on the box itself opens the keyboard; Send, dictation or stray focus never do.
if (window.matchMedia("(pointer: coarse)").matches) {
  const msg = $("#msg");
  msg.setAttribute("inputmode", "none");
  msg.addEventListener("pointerdown", () => msg.removeAttribute("inputmode"));
  msg.addEventListener("blur", () => msg.setAttribute("inputmode", "none"));
}
// Mic: Chrome speech recognition (Google's servers) straight into the message box, no keyboard needed (or the host's
// local speech-to-text, below).
// Keeps listening through pauses (each phrase is a session; we restart) until tapped again.
const Speech = window.SpeechRecognition || window.webkitSpeechRecognition;
const Dictation = window.Capacitor?.Plugins?.Dictation; // phone app (app/native/DictationPlugin.java)
if (Dictation) {
  // Google's on-device recognizer with punctuation, one session through pauses; phrases land in the box as spoken.
  const mic = $("#mic"), msg = $("#msg");
  let listening = false, base = "", done = "";
  const put = (partial) => {
    msg.value = base + [done, partial].filter(Boolean).join(" ");
    fitMsg(msg);
  };
  const start = () => {
    base = msg.value ? msg.value.replace(/\s*$/, " ") : ""; done = "";
    Dictation.start({ language: navigator.language || "en-US" }).catch((e) => { toast(`Mic: ${e.message}`); stop(); });
  };
  const stop = () => {
    if (!listening) return;
    listening = false; mic.classList.remove("on");
    Dictation.stop().catch(() => {});
  };
  Dictation.addListener("partial", ({ text }) => { if (listening) put(text); });
  Dictation.addListener("segment", ({ text }) => {
    if (!listening || !text) return;
    done = [done, text].filter(Boolean).join(" ");
    put("");
  });
  // The session can end on its own (long silence, or one phrase on phones without on-device speech): keep going.
  Dictation.addListener("end", () => { if (listening) start(); });
  Dictation.addListener("error", ({ code, message }) => {
    if (!listening) return;
    if (code === 6 || code === 7) { start(); return; } // silence / nothing caught: listen again
    toast(`Mic: ${message}`); stop();
  });
  mic.hidden = false;
  mic.onclick = () => {
    if (listening) { stop(); return; }
    listening = true; mic.classList.add("on"); start();
  };
  $("#say").addEventListener("submit", stop);
  sendBtn.addEventListener("pointerup", stop);
} else if (Speech || navigator.mediaDevices?.getUserMedia) {
  const mic = $("#mic"), msg = $("#msg");
  let rec = null, listening = false;
  // Whisper: a host with local speech-to-text (prefs.transcribe, DECK_TRANSCRIBE_URL) records here and transcribes
  // there when tapped again; Google's recognizer is never used then.
  let media = null, keep = true;
  const record = async () => {
    const base = msg.value ? msg.value.replace(/\s*$/, " ") : "";
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch (e) { toast(`Mic: ${e.message}`); stop(); return; }
    if (!listening) { stream.getTracks().forEach((t) => t.stop()); return; }
    const chunks = [];
    media = new MediaRecorder(stream);
    media.ondataavailable = (ev) => chunks.push(ev.data);
    media.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      media = null;
      if (!keep) return;
      mic.disabled = true;
      try {
        const r = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": chunks[0]?.type || "audio/webm" }, body: new Blob(chunks) });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.error || r.statusText);
        const text = (data.text || "").trim();
        if (text) { msg.value = (msg.value.startsWith(base) ? msg.value : base) + text; fitMsg(msg); }
      } catch (e) { toast(`Whisper: ${e.message}`); }
      mic.disabled = false;
    };
    media.start();
  };
  const stop = (send) => {
    listening = false; mic.classList.remove("on"); rec?.stop();
    keep = send !== true; // Send discards an unfinished recording, so it can't land in the cleared box
    if (media?.state === "recording") media.stop();
  };
  const listen = () => {
    const base = msg.value ? msg.value.replace(/\s*$/, " ") : "";
    rec = new Speech();
    rec.lang = navigator.language || "en-US";
    // One phrase per session, restarted in onend: Android's continuous mode repeats earlier words in each result.
    rec.continuous = false;
    rec.interimResults = true;
    rec.onresult = (ev) => {
      if (!listening) return; // late results after Send/stop
      msg.value = base + ev.results[ev.results.length - 1][0].transcript.trim();
      fitMsg(msg);
    };
    rec.onerror = (ev) => {
      if (ev.error === "no-speech" || ev.error === "aborted") return;
      toast(`Mic: ${ev.error}`); stop();
    };
    rec.onend = () => { rec = null; if (listening) listen(); };
    try { rec.start(); } catch (e) { toast(e.message); stop(); }
  };
  mic.hidden = false;
  mic.onclick = () => {
    if (listening) { stop(); return; }
    if (!prefs.transcribe && !Speech) { toast("Mic: no speech recognition in this browser"); return; }
    listening = true; mic.classList.add("on");
    if (prefs.transcribe) record(); else listen();
  };
  // Sending ends dictation so leftover words don't land in the cleared box.
  $("#say").addEventListener("submit", () => stop(true));
  sendBtn.addEventListener("pointerup", () => stop(true));
}
// Keyboard + mouse: Enter sends, Shift+Enter adds a line.
$("#msg").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && window.matchMedia("(pointer: fine)").matches) {
    ev.preventDefault();
    submitMsg();
  }
});
// The message box grows with its text, wrapped lines included, up to 12 lines on a PC and 8 on a phone (user); past
// that it scrolls (style.css sets its least height)
function fitMsg(m) {
  m.style.height = "auto";
  const cs = getComputedStyle(m), pad = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
  const max = (wide.matches ? 12 : 8) * parseFloat(cs.lineHeight) + pad;
  m.style.height = `${Math.min(m.scrollHeight, max)}px`;
  m.style.overflowY = m.scrollHeight > max ? "auto" : "hidden";
}
// Each session keeps its own unsent message (user): the box holds the open session's draft, saved on this device and
// dropped after a month untouched.
let drafts = {}, draftFor = null, draftTimer; // draftFor: the session the box's text belongs to
try { drafts = JSON.parse(localStorage.getItem("deck-drafts") || "{}") || {}; } catch { /* none */ }
for (const [k, d] of Object.entries(drafts)) if (!(d?.at > Date.now() - 30 * 86400e3)) delete drafts[k];
function keepDraft() {
  clearTimeout(draftTimer);
  if (!draftFor) return;
  const v = $("#msg").value;
  if (v.trim()) drafts[draftFor] = { text: v, at: Date.now() }; else delete drafts[draftFor];
  try { localStorage.setItem("deck-drafts", JSON.stringify(drafts)); } catch { /* this visit only */ }
}
function useDraft(name) {
  if (name === draftFor) return;
  keepDraft();
  draftFor = name;
  const msg = $("#msg");
  msg.value = (name && drafts[name]?.text) || "";
  fitMsg(msg);
}
$("#msg").oninput = (ev) => { fitMsg(ev.target); clearTimeout(draftTimer); draftTimer = setTimeout(keepDraft, 400); };
addEventListener("pagehide", keepDraft);

// What the reader (Review) has open goes with a message when it changed since the last one sent to that session (user):
// the agent learns which documents are open and which is showing, without being told again on every message
const readerSent = new Map();
function withReader(text) {
  const tabs = reviewOn && prefs.readernote !== false ? [...document.querySelectorAll("#doctabs .doctab")] : [];
  const note = tabs.length ? `[Deck: the reader beside this chat has open: ${tabs.map((t) =>
    t.classList.contains("on") ? `${t.title} (showing)` : t.title).join(", ")}]` : "";
  if (!text.trim() || !attached || (readerSent.get(attached) ?? "") === note) return text; // never a note on its own
  readerSent.set(attached, note);
  return note ? `${text.trim()}\n\n${note}` : text;
}

// ---- shelf items attached to the next message: dragged from the shelf onto the chat, one line each above the
// message box (tap: show or hide its text; ×: take it off). Sent after the message, numbered, in the order shown.
let attachedItems = [];
const attachBox = $("#attach");
function attachFromShelf(id) {
  const it = shelfLive.find((x) => x.id === id);
  if (!it) return;
  const add = it.kind === "folder" ? shelfLive.filter((x) => x.folder === id) : [it];
  for (const x of add) if (!attachedItems.some((a) => a.id === x.id)) {
    const a = { ...x, open: false };
    attachedItems.push(a);
    if (x.cut) fullItem(x).then((f) => { a.text = f.text; delete a.cut; }); // in well before it's sent
  }
  renderAttached();
}
// "You should know" → Chat about it: the card goes with the next message, like a paste (the bridge only dismisses it in
// the terminal: the card's own hotkey misses a message typed while Claude works)
function attachCard(title) {
  const text = title.replace(/\n\n\*Chat about it:.*\*$/s, "").replace(/^\*\*You should know:\*\* /, "");
  attachedItems = attachedItems.filter((a) => a.kind !== "card");
  attachedItems.push({ id: "card", kind: "card", open: false, title: text.split("\n")[0].slice(0, 120), text });
  renderAttached();
  $("#msg").focus();
}
// Files from the computer or phone (the message box's + button, or dropped on the chat): saved on the session's host in
// its chat folder, then attached like shelf items; the message gives the agent each file's path to read (user)
async function uploadToChat(files) {
  const name = attached;
  if (!name || !chatView) return toast("Open a chat first");
  for (const f of files) {
    if (f.size > 30 * 1024 * 1024) { toast(`${f.name}: too big (30 MB at most)`); continue; }
    const row = { id: `up-${Date.now()}-${Math.random()}`, kind: "upload", open: false, title: `${f.name} (uploading…)`, text: "", fixed: true };
    attachedItems.push(row); renderAttached();
    try {
      const r = await fetch(route(`/api/sessions/${encodeURIComponent(name)}/upload?name=${encodeURIComponent(f.name)}`),
        { method: "POST", body: f });
      const out = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(out.error || "upload failed");
      Object.assign(row, { title: out.name, fixed: false,
        text: `Uploaded to ${out.host}: ${out.path} (${out.size >= 1048576 ? (out.size / 1048576).toFixed(1) + " MB" : Math.ceil(out.size / 1024) + " KB"})` });
    } catch (e) {
      attachedItems = attachedItems.filter((a) => a !== row);
      toast(`${f.name}: ${e.message || "upload failed"}`);
    }
    renderAttached();
  }
}
$("#upbtn").onclick = () => $("#upfile").click();
$("#upfile").onchange = (e) => { const files = [...e.target.files]; e.target.value = ""; uploadToChat(files); };
const hasFiles = (e) => e.dataTransfer?.types.includes("Files") && $("#term").classList.contains("chat");
$("#view-term").addEventListener("dragover", (e) => { if (hasFiles(e)) { e.preventDefault(); $("#term").classList.add("dropchat"); } });
$("#view-term").addEventListener("dragleave", (e) => { if (!e.currentTarget.contains(e.relatedTarget)) $("#term").classList.remove("dropchat"); });
$("#view-term").addEventListener("drop", (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  $("#term").classList.remove("dropchat");
  uploadToChat([...e.dataTransfer.files]);
});

function renderAttached() {
  attachBox.hidden = !attachedItems.length;
  attachBox.replaceChildren(...attachedItems.map((it, i) => {
    const body = h("div", { class: "cprose attbody" });
    if (it.open) body.innerHTML = chatMarkdown(it.text);
    return h("div", { class: `att${it.open ? " open" : ""}` },
      h("div", { class: "atthead" },
        h("button", { type: "button", class: "atttoggle", onclick: () => { it.open = !it.open; renderAttached(); } },
          h("span", { class: `shelfkind k-${it.kind}` }, { nl: "NL", doc: "Doc", quote: "Quote", link: "Link", card: "Card", upload: "File" }[it.kind] || "Turn"), " ",
          it.title || it.text.split("\n")[0].slice(0, 120)),
        it.fixed ? null : h("button", { type: "button", class: "attx", "aria-label": "Remove", onclick: () => { attachedItems.splice(i, 1); renderAttached(); } }, "×")),
      it.open ? body : null);
  }));
}
function withAttached(text) {
  const items = attachedItems;
  if (!items.length) return text;
  const parts = items.map((it, i) => it.kind === "doc" ? docPart(it, i)
    : it.kind === "link" ? `## ${i + 1}. Pinned page: ${it.title}\n\n${it.url}${it.text ? `\n\n${it.text}` : ""}`
    : it.kind === "card" ? `## ${i + 1}. You should know (card)\n\n${it.text}`
    : it.kind === "upload" ? `## ${i + 1}. File: ${it.title}\n\n${it.text}`
    : it.kind === "quote" ? `## ${i + 1}. Highlighted in ${it.from}\n\n${it.text.split("\n").map((l) => `> ${l}`).join("\n")}`
    : `## ${i + 1}. ${it.title || "Saved " + (it.kind === "nl" ? "reply" : "turn")}\n\n${it.text}`);
  return `${text.trim()}\n\n---\nAttached:\n\n${parts.join("\n\n")}`.trim();
}
// A document attached to a message: its file's path when the chat runs on the host that has it (agents read files,
// PDFs included), else its text
function docPart(it, i) {
  const head = `## ${i + 1}. Document: ${it.title || it.file?.name || "untitled"}`;
  if (it.file && attached && hostOf(attached) === it.file.host) {
    return `${head}\n\nThe file: ${it.file.path}\nIts text: ${it.file.path}.txt`;
  }
  const cut = 100000;
  return `${head}\n\n${it.text.slice(0, cut)}${it.text.length > cut ? "\n\n(cut here: the rest is on the shelf)" : ""}`;
}
