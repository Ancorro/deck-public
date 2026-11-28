// ---- the shelf: a column beside the chat holding saved turns (Save turn: everything; Save messages: the agent's words) ----
// A turn as Markdown. "nl" keeps only the agent's text (tables and math included); "turn" adds your message, tool
// calls with their output, notes and questions.
function turnText(n, kind) {
  const box = chatView.box, out = [], nl = kind === "nl";
  const head = box.querySelector(`.cturn[data-for="${n}"]`)?.previousElementSibling;
  const asked = head?.querySelector("span")?.textContent || "";
  if (!nl && asked) out.push(`**${head.matches(".cpeer") ? head.querySelector(".cfrom").textContent : "You"}:** ${asked}`);
  for (const el of box.querySelectorAll(`:scope > [data-turn="${n}"]`)) {
    if (el.blocks) {
      for (const b of el.blocks) {
        if (b.kind === "text" && b.text?.trim()) out.push(b.text.trim());
        else if (b.kind === "tool" && !nl) {
          const o = chatView.tools.get(b.id)?.querySelector(".cout"), res = (o?.dataset.raw ?? o?.textContent ?? "").trim();
          out.push(`**${b.name}** \`${toolArg(b.args || {}).replace(/`/g, "'").slice(0, 300)}\`` +
            (res ? `\n\`\`\`\n${res.length > 4000 ? `${res.slice(0, 4000)}\n…` : res}\n\`\`\`` : ""));
        }
      }
    } else if (el.matches(".cmsg.agent") && el.textContent.trim()) out.push(el.textContent.trim()); // still streaming
    else if (!nl && el.matches(".cnote, .cask")) out.push(`*${el.querySelector(".cprose")?.textContent || el.textContent}*`);
  }
  return { text: out.join("\n\n"), title: asked.split("\n")[0].slice(0, 120) };
}
async function saveTurn(n, kind, btn) {
  const { text, title } = turnText(n, kind);
  try {
    const model = $("#agentbar .model")?.textContent || ""; // the model as its status bar shows it
    await api("POST", "/api/shelf", { kind, text, title, session: chatView.name, model });
    btn.textContent = "Saved ✓";
    loadShelf();
  } catch (e) { btn.textContent = `Failed: ${e.message}`; }
}
let shelfOpen = !matchMedia("(max-width: 700px)").matches;
try { const s = localStorage.getItem("deck-shelf"); if (s) shelfOpen = s === "open"; } catch { /* default */ }
let shelfLive = [], shelfTrashOpen = false;
const shelfOpenIds = new Set(); // items and folders left open, kept across redraws
// The shelf's list cuts a long text ("cut": its full length); this fetches it whole
async function fullItem(it) {
  if (!it?.cut) return it;
  try { return await api("GET", `/api/shelf/${it.id}`); } catch { return it; }
}
async function loadShelf() {
  const shelf = $("#shelf");
  if (!screenOn) shelf.classList.toggle("shut", !shelfOpen); // the Screen view keeps it folded (02-prefs.js setScreen)
  $("#shelfagents").checked = !!prefs.shelfagents;
  let items = [];
  try { items = await api("GET", "/api/shelf"); } catch { return; }
  const live = shelfLive = items.filter((it) => !it.deleted), trash = items.filter((it) => it.deleted);
  const folders = new Set(live.filter((it) => it.kind === "folder").map((it) => it.id));
  const act = (label, method, path) => h("button", { type: "button", onclick: async () => {
    await api(method, path).catch(() => {});
    loadShelf();
  } }, label);
  // The bar's trash icon: the first tap arms it (red), a second within 3 s moves the item (a folder: with its
  // contents) to the trash
  const bin = (it) => {
    let armed = 0;
    const b = h("button", { type: "button", class: "shelfdel", "aria-label": "Delete", onclick: (e) => {
      e.preventDefault(); e.stopPropagation(); // not the bar's open/close
      if (armed) { clearTimeout(armed); api("DELETE", `/api/shelf/${it.id}`).catch(() => {}).then(loadShelf); return; }
      b.classList.add("armed");
      armed = setTimeout(() => { armed = 0; b.classList.remove("armed"); }, 3000);
    } }, trashIcon());
    return b;
  };
  const keepOpen = (el, it) => {
    el.open = shelfOpenIds.has(it.id);
    el.addEventListener("toggle", () => { if (el.open) shelfOpenIds.add(it.id); else shelfOpenIds.delete(it.id); });
  };
  const card = (it, ...acts) => {
    const body = h("div", { class: "cprose" });
    const el = h("details", { class: `shelfitem kind-${it.kind || "turn"}`, "data-id": it.id },
      h("summary", {}, it.deleted ? null : bin(it), // first, so it floats right of a long title
        h("span", { class: "shelfglyph", "aria-hidden": "true" }), // what it is, small (user): chat, document or page
        h("span", { class: "shelftitle" }, it.title || it.text.split("\n")[0].slice(0, 120)),
        it.kind === "link" ? h("a", { class: "shelfopen", href: it.url, target: "_blank", rel: "noopener noreferrer", title: it.url,
          "aria-label": "Open page", onclick: (e) => e.stopPropagation() }, "↗") : null, // a pinned page: open it without opening the card
      ),
      h("div", { class: "shelfacts" }, ...acts), body, metaBox(it)); // Copy and the rest above the text (user)
    const fill = async () => {
      if (!el.open || body.innerHTML) return;
      if (it.kind === "doc") return body.append(h("pre", { class: "doctext" }, it.text.slice(0, 3000) + (it.cut ? "\n…" : "")));
      const f = await fullItem(it); // a long saved turn comes cut in the list
      if (body.innerHTML) return;
      body.innerHTML = chatMarkdown(f.text);
      if (it.kind === "link") body.prepend(h("a", { class: "shelfurl", href: it.url, target: "_blank", rel: "noopener noreferrer" }, it.url));
    };
    el.addEventListener("toggle", fill);
    if (!it.deleted) { keepOpen(el, it); fill(); }
    // In Review, a double click on its bar opens the item in the reader (its two clicks toggle the card back as it was)
    if (!it.deleted && it.kind !== "link") el.firstChild.addEventListener("dblclick", (e) => {
      if (!reviewOn || e.target.closest(".shelfdel")) return;
      getSelection()?.removeAllRanges(); // not the word under the pointer
      openDoc(it.id);
    });
    return el;
  };
  const copy = (it) => h("button", { type: "button", onclick: (e) => {
    fullItem(it).then((f) => copyText(it.kind === "link" ? it.url : f.text)).then(() => { e.target.textContent = "Copied ✓"; }, () => { e.target.textContent = "Can't copy"; });
  } }, "Copy");
  // A folder: made by dropping one item on another; Rename names it
  const folder = (f) => {
    const kids = live.filter((it) => it.folder === f.id);
    const name = h("input", { class: "shelfname", value: f.title || "Folder", hidden: "", "aria-label": "Folder name" });
    let saved = false;
    const save = () => {
      if (saved) return;
      saved = true;
      api("POST", `/api/shelf/${f.id}/rename`, { title: name.value }).catch(() => {}).then(loadShelf);
    };
    name.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { saved = true; loadShelf(); } });
    name.addEventListener("blur", save);
    const el = h("details", { class: "shelfitem shelffolder", "data-id": f.id },
      h("summary", {}, bin(f), h("span", { class: "shelftitle" }, f.title || "Folder"), h("span", { class: "shelfcount" }, ` ${kids.length}`)),
      name, ...kids.map((it) => card(it, copy(it), ...docActs(it), ...mainAct(it))),
      h("div", { class: "shelfacts" }, h("button", { type: "button", onclick: () => { name.hidden = false; name.focus(); name.select(); } }, "Rename"),
        h("button", { type: "button", onclick: async (e) => { // a name from what's inside (the host asks Claude)
          const b = e.target;
          b.disabled = true; b.textContent = "Naming…";
          try { await api("POST", `/api/shelf/${f.id}/autoname`); loadShelf(); }
          catch (err) { b.disabled = false; b.textContent = "Autoname"; toast(err.message); }
        } }, "Autoname")));
    keepOpen(el, f);
    return el;
  };
  // Deleted items wait in the trash (a month, then the host drops them): Restore puts one back
  const can = trash.length ? h("details", { class: "shelftrash", ontoggle: (e) => { shelfTrashOpen = e.target.open; } },
    h("summary", {}, trashIcon(), ` Trash (${trash.length})`),
    ...trash.map((it) => card(it, act("Restore", "POST", `/api/shelf/${it.id}/restore`),
      act("Delete forever", "DELETE", `/api/shelf/${it.id}?forever=1`)))) : null;
  if (can) can.open = shelfTrashOpen;
  renderDeckFiles();
  $("#shelflist").replaceChildren(
    ...live.filter((it) => !it.folder || !folders.has(it.folder)).map((it) => it.kind === "folder" ? folder(it) : card(it, copy(it), ...docActs(it), ...mainAct(it))),
    ...(can ? [can] : []));
  renderProjBar();
}

// ---- Deck files: Deck's own files at the shelf's top, apart from saved items (user 2026-11-11: "a deck files area in
// shelf, with a skills sub folder, look diff from the other items"). Read from disk on each open (GET /api/skills,
// deck/skills.py), never stored on the shelf; a file opens in the reader as a "skill:" tab (11-review.js noteDoc)
const deckFilesOpen = new Set();
try { for (const k of JSON.parse(localStorage.getItem("deck-files-open") || "[]")) deckFilesOpen.add(k); } catch { /* none */ }
let deckSkills = null;
function deckFold(key, cls, head, kids) {
  const el = h("details", { class: cls }, h("summary", {}, ...head), ...kids);
  el.open = deckFilesOpen.has(key);
  el.addEventListener("toggle", () => {
    if (el.open === deckFilesOpen.has(key)) return;
    if (el.open) deckFilesOpen.add(key); else deckFilesOpen.delete(key);
    try { localStorage.setItem("deck-files-open", JSON.stringify([...deckFilesOpen])); } catch { /* this visit only */ }
    if (key === "deck" && el.open && !deckSkills) loadDeckFiles();
  });
  return el;
}
async function loadDeckFiles() {
  try { deckSkills = await api("GET", "/api/skills"); } catch { deckSkills = { skills: [], error: true }; }
  renderDeckFiles();
}
async function openSkillFile(path) {
  const id = `skill:${path}`;
  if (wide.matches) { setReview(true); show("term"); return openDoc(id); }
  try { // a phone: no reader beside the chat, so the file over the screen
    const { text } = await api("GET", `/api/skills/file?path=${encodeURIComponent(path)}`);
    readFull({ id, kind: "doc", title: path, text });
  } catch (e) { toast(e.message); }
}
function renderDeckFiles() {
  const box = $("#deckfiles");
  if (deckFilesOpen.has("deck") && !deckSkills) { loadDeckFiles(); return; }
  const list = deckSkills?.skills || [];
  const file = (s, f) => h("button", { type: "button", class: "dfile", title: `${s.name}/${f}`, onclick: () => openSkillFile(`${s.name}/${f}`) },
    h("span", { class: "dfdir" }, f.includes("/") ? f.slice(0, f.lastIndexOf("/") + 1) : ""), f.split("/").pop());
  const skill = (s) => deckFold(`skill:${s.name}`, "dskill",
    [h("span", { class: "dname" }, s.name), h("span", { class: "dcount" }, String(s.files.length))],
    [s.description ? h("div", { class: "ddesc", title: s.description }, s.description) : null, ...s.files.map((f) => file(s, f))]);
  const skills = deckFold("skills", "dsub",
    [h("span", { class: "dfoldname" }, "Skills"), h("span", { class: "dcount" }, deckSkills ? String(list.length) : "")],
    [deckSkills?.path ? h("div", { class: "dpath", title: deckSkills.path }, shortPath(deckSkills.path)) : null,
      ...(deckSkills?.error ? [h("div", { class: "dnone" }, "Couldn't read the skills folder")]
        : list.length ? list.map(skill) : deckSkills ? [h("div", { class: "dnone" }, "No skills (DECK_SKILLS)")] : [])]);
  const reload = h("button", { type: "button", class: "dreload", "aria-label": "Read again", title: "Read the folders again",
    onclick: (e) => { e.preventDefault(); e.stopPropagation(); deckSkills = null; deckFilesOpen.add("deck"); loadDeckFiles(); } }, "↻");
  box.replaceChildren(deckFold("deck", "deckarea", [h("span", { class: "dglyph", "aria-hidden": "true" }), h("span", { class: "dtitle" }, "Deck files"), reload], [skills]));
}

// A shelf item's session, as saved with it (deck/sessions.py session_meta): at the bottom of the opened item, one small
// line (model or agent, folder, host, when saved) that opens to every detail
const shortPath = (p) => (p || "").replace(/\\/g, "/").replace(/^(\/home\/[^/]+|\/Users\/[^/]+|[A-Za-z]:\/Users\/[^/]+|\/[a-z]\/Users\/[^/]+)(?=\/|$)/, "~")
  .split("/").slice(-2).join("/");
const when = (t) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
function metaLine(it) {
  const m = it.meta || {};
  return [m.model || AGENT_NAME[m.agent] || m.agent, shortPath(m.folder), m.host, it.at && when(it.at)].filter(Boolean).join(" · ");
}
function metaBox(it) {
  const m = it.meta;
  if (!m) return null;
  const rows = [["Host", m.host], ["Agent", AGENT_NAME[m.agent] || m.agent], ["Model", m.model], ["Tab", m.title || m.label],
    ["Folder", m.folder], ["Session", m.session], ["Started", m.started && when(m.started)],
    ["Conversation", m.conversation], ["Saved", it.at && when(it.at)]].filter(([, v]) => v);
  return h("details", { class: "shelfmeta" }, h("summary", {}, metaLine(it)),
    h("dl", { class: "shelfmetafull" }, ...rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)])));
}

// ---- dragging on the shelf: by an item's bar (touch: held first, so a swipe still scrolls). Dropped on the top or
// bottom of another item it goes before or after it; on its middle, the two go in a folder (onto a folder: into it).
let shelfDrag = null;
document.addEventListener("touchmove", (e) => { if (shelfDrag?.on) e.preventDefault(); }, { passive: false });
$("#shelflist").addEventListener("pointerdown", (ev) => {
  const bar = ev.target.closest("summary"), el = bar?.parentElement;
  if (!el?.dataset.id || el.closest(".shelftrash") || ev.button > 0 || ev.target.closest(".shelfdel")) return;
  const d = shelfDrag = { el, bar, id: el.dataset.id, x: ev.clientX, y: ev.clientY, on: false, drop: null, pid: ev.pointerId };
  const begin = () => {
    d.on = true;
    el.classList.add("dragging");
    // a label follows the finger, so an item can be carried out of the shelf (onto the chat: attached)
    d.ghost = h("div", { class: "shelfghost" }, bar.querySelector(".shelftitle")?.textContent || "Item");
    document.body.append(d.ghost);
    navigator.vibrate?.(15);
  };
  d.timer = ev.pointerType === "mouse" ? 0 : setTimeout(begin, 350);
  const move = (e) => {
    if (e.pointerId !== d.pid) return;
    if (!d.on) {
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 8) { if (d.timer) stop(); else begin(); }
      return;
    }
    d.ghost.style.transform = `translate(${e.clientX / uiZoom() + 12}px, ${e.clientY / uiZoom() - 14}px)`;
    shelfTarget(d, e.clientX, e.clientY);
    const list = $("#shelflist"), r = list.getBoundingClientRect(); // near an edge: scroll
    if (e.clientY < r.top + 30) list.scrollTop -= 10; else if (e.clientY > r.bottom - 30) list.scrollTop += 10;
  };
  const stop = (e) => {
    clearTimeout(d.timer);
    removeEventListener("pointermove", move);
    removeEventListener("pointerup", stop);
    removeEventListener("pointercancel", stop);
    shelfDrag = null;
    if (d.on) shelfDragEnd = Date.now(); // its release isn't a tap on the shelf's empty space
    el.classList.remove("dragging");
    d.ghost?.remove();
    clearDropMarks();
    if (!d.on) return;
    bar.addEventListener("click", (c) => c.preventDefault(), { once: true, capture: true }); // the drop isn't a tap
    if (e?.type !== "pointerup" || !d.drop) return;
    if (d.drop.reader) { // a pinned page opens in a browser tab (most sites won't show inside another page)
      const it = shelfLive.find((x) => x.id === d.id);
      if (it?.kind === "link") openLink(it.url); else openDoc(d.id);
    }
    else if (d.drop.chat) attachFromShelf(d.id);
    else shelfMove(d.id, d.drop.id, d.drop.zone);
  };
  addEventListener("pointermove", move);
  addEventListener("pointerup", stop);
  addEventListener("pointercancel", stop);
});
$("#shelflist").addEventListener("contextmenu", (e) => { if (e.target.closest("summary")) e.preventDefault(); });
const clearDropMarks = () => document.querySelectorAll(".drop-before, .drop-after, .drop-into, .drop-end, .dropchat")
  .forEach((x) => x.classList.remove("drop-before", "drop-after", "drop-into", "drop-end", "dropchat"));

// Where a drag would drop: {id, zone: before | after | into}, marked on the target; {chat: true} over the chat;
// {reader: true} over the Review reader
function shelfTarget(d, x, y) {
  clearDropMarks();
  const under = document.elementFromPoint(x, y);
  if (under?.closest("#docpane")) { // the reader (Review): opens there, in a tab
    d.drop = { reader: true };
    $("#docpane").classList.add("dropchat");
    return;
  }
  if (under?.closest("#term.chat") && !under.closest("#shelf")) {
    d.drop = { chat: true };
    $("#term").classList.add("dropchat");
    return;
  }
  let t = under?.closest("#shelflist details[data-id]");
  if (t?.closest(".shelftrash") || t === d.el || d.el.contains(t)) t = null;
  const dragFolder = d.el.classList.contains("shelffolder");
  if (t && dragFolder && t.parentElement.closest(".shelffolder")) t = t.parentElement.closest(".shelffolder"); // no folders in folders
  d.drop = null;
  if (!t) {
    if (under?.closest("#shelflist") && !under.closest(".shelftrash") && !dragFolder) { // empty space: the end of the top level
      d.drop = { id: null, zone: "end" };
      $("#shelflist").classList.add("drop-end");
    }
    return;
  }
  const r = t.querySelector("summary").getBoundingClientRect(), inFolder = !!t.parentElement.closest(".shelffolder");
  const canInto = !dragFolder && !inFolder;
  const zone = canInto && y > r.top + r.height * .25 && y < r.bottom - r.height * .25 ? "into"
    : y < r.top + r.height / 2 ? "before" : "after";
  d.drop = { id: t.dataset.id, zone };
  t.classList.add(`drop-${zone}`);
}

// A drop: the new order (and any new folder) goes to the host, then the shelf redraws
function shelfMove(id, targetId, zone) {
  const live = shelfLive, folders = new Set(live.filter((x) => x.kind === "folder").map((x) => x.id));
  const top = live.filter((x) => !x.folder || !folders.has(x.folder))
    .map((x) => ({ x, kids: x.kind === "folder" ? live.filter((k) => k.folder === x.id) : null }));
  let node = null; // take the dragged one out
  for (const [i, n] of top.entries()) {
    if (n.x.id === id) { node = top.splice(i, 1)[0]; break; }
    const k = n.kids?.findIndex((c) => c.id === id) ?? -1;
    if (k >= 0) { node = { x: n.kids.splice(k, 1)[0], kids: null }; break; }
  }
  if (!node) return;
  const made = [];
  const ti = top.findIndex((n) => n.x.id === targetId);
  if (zone === "end") top.push(node);
  else if (ti >= 0) {
    const t = top[ti];
    if (zone === "into" && t.kids) t.kids.push(node.x);
    else if (zone === "into") {
      const f = { id: `f${Math.random().toString(16).slice(2, 11)}`, kind: "folder", title: "Folder" };
      made.push({ id: f.id, title: f.title });
      top.splice(ti, 1, { x: f, kids: [t.x, node.x] });
    } else top.splice(zone === "after" ? ti + 1 : ti, 0, node);
  } else {
    const p = top.find((n) => n.kids?.some((c) => c.id === targetId));
    if (!p || node.kids) return;
    const k = p.kids.findIndex((c) => c.id === targetId);
    p.kids.splice(zone === "before" ? k : k + 1, 0, node.x);
  }
  const order = top.flatMap((n) => [{ id: n.x.id }, ...(n.kids || []).map((c) => ({ id: c.id, folder: n.x.id }))]);
  if (made.length) shelfOpenIds.add(made[0].id);
  api("POST", "/api/shelf-arrange", { order, folders: made }).catch(() => {}).then(loadShelf);
}
$("#shelfhead").onclick = () => {
  shelfOpen = !shelfOpen;
  try { localStorage.setItem("deck-shelf", shelfOpen ? "open" : "shut"); } catch { /* this visit only */ }
  $("#shelf").classList.toggle("shut", !shelfOpen);
  if (shelfOpen) loadShelf(); // saved meanwhile, from here or another device
};
// Phones: a tap on the open shelf's empty space (not an item or a control) shuts it, like the Pad column
let shelfDragEnd = 0;
$("#shelf").addEventListener("click", (e) => {
  if (wide.matches || !shelfOpen || Date.now() - shelfDragEnd < 500) return;
  if (e.target === $("#shelf") || e.target === $("#shelflist")) $("#shelfhead").click();
});
// Phones: an open column (tabs or shelf) lies over the chat; a tap anywhere outside it shuts it, and does only that
document.addEventListener("click", (e) => {
  if (wide.matches || e.target.closest("dialog")) return;
  let shut = false;
  if (padOpen && !e.target.closest("#padcol")) { $("#padhead").click(); shut = true; }
  if (shelfOpen && !e.target.closest("#shelf") && Date.now() - shelfDragEnd >= 500) { $("#shelfhead").click(); shut = true; }
  if (shut) { e.preventDefault(); e.stopPropagation(); }
}, true);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && chatView) loadShelf(); });
// Agents on this host may read the shelf (GET /api/shelf/agent; every agent's Deck context says how)
$("#shelfagents").onchange = (e) => saveSetting({ shelfagents: e.target.checked });

// ---- documents: PDFs and text files on the shelf (kind "doc"; + File, or Open file in Review) ----
// The browser reads the text (pdf.js for PDFs, loaded the first time one is needed); the host keeps the file.
// pdf.min.mjs is checked by index.html's import map; the worker, which an import map doesn't reach, is fetched with
// its hash and run from a blob (deploy/cdn-sri prints both)
const PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build";
const PDFJS_WORKER_SRI = "sha384-ToeVvShCxKc6CEvhHeMt0Q8A06pSPDbAlngO9nokrDmh914gk/pYd0N7D0a4Lz2o";
let pdfjsLib = null;
async function loadPdfjs() {
  if (!pdfjsLib) {
    const lib = await import(`${PDFJS}/pdf.min.mjs`);
    const worker = await (await fetch(`${PDFJS}/pdf.worker.min.mjs`, { integrity: PDFJS_WORKER_SRI })).text();
    lib.GlobalWorkerOptions.workerSrc = URL.createObjectURL(new Blob([worker], { type: "text/javascript" }));
    pdfjsLib = lib;
  }
  return pdfjsLib;
}
const isPdf = (f) => f.type === "application/pdf" || /\.pdf$/i.test(f.name);
// HTML from a file (mammoth doesn't sanitise its output): no scripts, frames or event handlers, and links and images
// only to http(s), mailto and in-page anchors (images also data:), since this page can run commands on the host
function safeHtml(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  for (const el of doc.body.querySelectorAll("script, style, iframe, frame, object, embed, link, meta, base, form")) el.remove();
  for (const el of doc.body.querySelectorAll("*")) {
    for (const a of [...el.attributes]) {
      const v = a.value.trim().toLowerCase();
      if (a.name.startsWith("on") || (["href", "src", "xlink:href", "action", "formaction"].includes(a.name)
          && !(/^(https?:|mailto:|#)/.test(v) || (a.name === "src" && v.startsWith("data:image/"))))) el.removeAttribute(a.name);
    }
    if (el.tagName === "A") el.setAttribute("rel", "noopener noreferrer");
  }
  return [...doc.body.childNodes];
}

// Word files: mammoth.js turns a .docx into HTML to read and pulls its text for agents (loaded the first time it's needed)
const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const isDocx = (f) => f.type === DOCX || /\.docx$/i.test(f.name);
let mammothLib = null;
function loadMammoth() {
  mammothLib ||= new Promise((ok, fail) => document.head.append(h("script", {
    src: "https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.8.0/mammoth.browser.min.js", integrity: "sha384-/cXAMbzovUIKbBERjPmR3SnPTh8siWr5lsvFYj1Uq4XP0yaJUZJmsh0YXyGv5P0y", crossorigin: "anonymous",
    onload: () => ok(window.mammoth), onerror: (e) => { mammothLib = null; fail(e); } })));
  return mammothLib;
}
async function pdfText(data) {
  const doc = await (await loadPdfjs()).getDocument({ data }).promise;
  const pages = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const c = await (await doc.getPage(i)).getTextContent();
    pages.push(`--- page ${i} ---\n${c.items.map((x) => x.str + (x.hasEOL ? "\n" : "")).join("")}`);
  }
  return pages.join("\n\n");
}
async function addDoc(file) {
  const data = new Uint8Array(await file.arrayBuffer());
  if (data.length > 30 * 1024 * 1024) throw new Error(`${file.name}: too big (30 MB at most)`);
  const type = isPdf(file) ? "application/pdf" : isDocx(file) ? DOCX : file.type.startsWith("text/") ? file.type : "text/plain";
  let text = isPdf(file) ? await pdfText(data.slice()) // pdf.js takes its copy
    : isDocx(file) ? (await (await loadMammoth()).extractRawText({ arrayBuffer: data.buffer.slice(0) })).value
      : new TextDecoder().decode(data);
  if (!text.trim()) text = `(${file.name}: no text found${isPdf(file) ? "; a scanned PDF?" : ""})`;
  if (text.length > 2000000) text = `${text.slice(0, 2000000)}\n\n(cut: too long)`;
  const it = await api("POST", "/api/shelf", { kind: "doc", text, title: file.name });
  const r = await fetch(`/api/shelf/${it.id}/file?name=${encodeURIComponent(file.name)}&type=${encodeURIComponent(type)}`,
    { method: "PUT", body: data });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(out.error || "upload failed");
  return out;
}
$("#docfile").onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  let last = null;
  for (const f of files) {
    try { toast(`Reading ${f.name}…`); last = await addDoc(f); }
    catch (err) { toast(err.message || `${f.name}: couldn't read it`); }
  }
  if (shelfOpen) loadShelf();
  if (last) { toast(files.length > 1 ? `${files.length} files on the shelf` : `${last.title} is on the shelf`); if (reviewOn) openDoc(last.id); }
};
$("#shelffile").onclick = () => $("#docfile").click();

// ---- the project bar and a card's actions ----
// The project's main page (deck/shelf.py main): the newest shelf link marked main for the open chat's project
// (deck/projects.py project_of: its git repo), in a slim bar atop the chat; every session in the project shows the same one
const projBar = h("div", { class: "projbar", hidden: "" });
const chatProject = () => chatShell ? sessions.find((x) => x.name === attached)?.project || "" : "";
function renderProjBar() {
  const p = chatProject();
  const it = p && shelfLive.filter((x) => x.kind === "link" && x.main && x.project === p).sort((a, b) => b.main - a.main)[0];
  projBar.hidden = !it;
  const key = it ? `${p} ${it.id} ${it.title} ${it.url}` : "";
  if (projBar.dataset.key === key) return;
  projBar.dataset.key = key;
  projBar.replaceChildren(...(it ? [h("span", { class: "projname", title: p }, p.split("/").pop()),
    h("a", { href: it.url, target: "_blank", rel: "noopener noreferrer", title: it.url }, it.title || it.url),
    h("button", { type: "button", "aria-label": "Not the main page any more", title: "Not the main page any more", onclick: () =>
      api("POST", `/api/shelf/${it.id}/main`, { project: "" }).then(loadShelf, (err) => toast(err.message)) }, "×")] : []));
}
// A pinned page's card: make it the open chat's project's main page
const mainAct = (it) => {
  const p = chatProject();
  if (it.kind !== "link" || !p || (it.main && it.project === p)) return [];
  return [h("button", { type: "button", title: p, onclick: () =>
    api("POST", `/api/shelf/${it.id}/main`, { project: p }).then(loadShelf, (err) => toast(err.message)) }, `Main for ${p.split("/").pop()}`)];
};
// A document's card on the shelf: Review opens it beside the chat (PC). A phone has no reader beside the chat, and the
// card shows only the first 3000 characters (user: "I can't open or download text from my shelf"): Open shows the
// whole text over the screen. Download saves it, on a PC too (user: "I need to be able to download the PDFs from the
// shelf")
const downloadBtn = (it) => h("button", { type: "button", onclick: (e) => downloadItem(it, e.target) }, "Download");
const docActs = (it) => it.kind === "link" ? []
  : it.kind === "doc" && wide.matches ? [h("button", { type: "button", onclick: () => { setReview(true); show("term"); openDoc(it.id); } }, "Review"),
     downloadBtn(it)]
  : [...(wide.matches ? [] : [h("button", { type: "button", onclick: () => readFull(it) }, "Open")]), downloadBtn(it)];
const fileName = (it) => (it.file?.name || `${docTitle(it).replace(/[\\/:*?"<>|\n]+/g, " ").trim().slice(0, 80) || "shelf"}.md`);
async function readFull(it) {
  const f = await fullItem(it);
  let pre = h("pre", { class: "doctext" }, f.text); // a document as text; saved chat as the chat shows it
  if (it.kind !== "doc") { pre = h("div", { class: "cprose fullprose" }); pre.innerHTML = chatMarkdown(f.text); }
  const close = h("button", { type: "button", class: "ghost small", onclick: () => box.remove() }, "Close");
  const box = h("div", { class: "fullread" }, h("div", { class: "fullhead" }, h("span", {}, docTitle(it)), close), pre);
  document.body.append(box);
}
async function downloadItem(it, b) {
  const f = await fullItem(it), name = fileName(it);
  // A document's file itself (a PDF, linked or uploaded, from whichever host has it: docFileUrl), else its text
  const textName = it.file?.name && !/\.(md|txt)$/i.test(name) ? `${name}.txt` : name;
  const file = it.file ? await fetch(docFileUrl(it)).then((r) => (r.ok ? r.blob() : null)).catch(() => null) : null;
  try {
    const where = await saveFile(file || new Blob([f.text], { type: "text/plain;charset=utf-8" }), file ? name : textName);
    if (where) { b.textContent = "Saved ✓"; toast(`Saved to ${where}`); }
  } catch (err) { toast(`Can't save (${err.message || err}): opened it instead`); readFull(it); } // still readable, copyable
}
// Save a file on this device; where it went in the app, "" in a browser (its downloads). The app's WebView drops
// <a download>, so the app writes it to Documents/Deck instead (base64: the bytes).
async function saveFile(blob, name) {
  const fs = window.Capacitor?.Plugins?.Filesystem;
  if (fs) {
    const data = await new Promise((ok, bad) => {
      const rd = new FileReader();
      rd.onload = () => ok(String(rd.result).split(",")[1]); rd.onerror = () => bad(rd.error);
      rd.readAsDataURL(blob);
    });
    await fs.writeFile({ path: `Deck/${name}`, data, directory: "DOCUMENTS", recursive: true });
    return `Documents/Deck/${name}`;
  }
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
  return "";
}

// ---- the board: one board for every session and host (POST /api/board, deck/board.py), Deck's first view ----
// A cork board in function (user): cards sit where they were dragged (x, y, z in board pixels: PUT /api/board/{id}); a
// card never moved takes the shortest column below the placed ones. Pinch (two fingers; Ctrl+wheel or a trackpad pinch
// on a PC) or − / + zoom it, kept per device. Polled with the sessions: every poll while the Board is open, else every
// fifth (the tab's count of new cards).
let boardCards = [], boardTick = 0, boardStamp = "", boardDrag = null, boardJustDragged = false, boardPinching = () => false;
const CARD_W = 240, CARD_GAP = 14, ZOOM_MIN = 0.25, ZOOM_MAX = 2.5;
let boardScale = +(localStorage.getItem("deck-board-zoom") || 0) || 1;
const boardSeen = () => +(localStorage.getItem("deck-board-seen") || 0);
async function loadBoard(now = false) {
  if (!now && view !== "board" && boardTick++ % 5) return;
  try { boardCards = await api("GET", "/api/board"); } catch { return; }
  if (view === "board") {
    try { localStorage.setItem("deck-board-seen", String(Date.now() / 1000)); } catch { /* this visit only */ }
  }
  const fresh = boardCards.filter((c) => !c.deleted && c.at > boardSeen()).length;
  $("#boardnew").hidden = !fresh || view === "board";
  $("#boardnew").textContent = fresh > 99 ? "99+" : String(fresh);
  const stamp = JSON.stringify(boardCards.map((c) => [c.id, c.at, c.x, c.y, c.z, c.s, c.deleted]));
  if (view === "board" && !boardDrag && !boardPinching() && (now || stamp !== boardStamp)) renderBoard();
  boardStamp = stamp;
}
const cardFrom = (c) => c.meta?.host && c.session ? `${c.meta.host}/${c.session}` : c.session || "";
function renderBoard() {
  const shown = boardCards.filter((c) => !c.deleted); // one board for every session and host (user: "1 global")
  const binned = boardCards.length - shown.length;
  $("#boardtrashn").hidden = !binned; $("#boardtrashn").textContent = binned > 99 ? "99+" : String(binned);
  $("#boardempty").hidden = shown.length > 0;
  const box = $("#boardcards"), els = shown.map(boardCard);
  box.replaceChildren(...els);
  // Place: moved cards where they are; each other card in the first free spot (the highest, then the leftmost column)
  // that overlaps no card already placed
  const cols = Math.max(1, Math.floor(($("#boardcork").clientWidth - CARD_GAP) / (CARD_W + CARD_GAP))); // the board at 100%, whatever the zoom
  els.forEach((el, j) => sizeCard(el, shown[j].s || 1));
  const taken = shown.flatMap((c, j) => c.x != null ? [[c.x, c.y, cardW(els[j]), cardH(els[j])]] : []);
  const hits = (x, y, w, hh) => taken.find(([a, b, c, d]) => x < a + c + CARD_GAP && a < x + w + CARD_GAP && y < b + d + CARD_GAP && b < y + hh + CARD_GAP);
  shown.forEach((c, j) => {
    let x = c.x, y = c.y;
    const w = cardW(els[j]), hh = cardH(els[j]);
    if (x == null) {
      let best = null;
      for (let i = 0; i < cols; i++) {
        const cx = CARD_GAP + i * (CARD_W + CARD_GAP);
        let cy = CARD_GAP, hit;
        while ((hit = hits(cx, cy, w, hh))) cy = hit[1] + hit[3] + CARD_GAP;
        if (!best || cy < best[1]) best = [cx, cy];
      }
      [x, y] = best;
      taken.push([x, y, w, hh]);
    }
    Object.assign(els[j].style, { left: `${x}px`, top: `${y}px`, zIndex: String(c.z || 0) });
  });
  sizeBoard();
}
// The cards' layer is scaled; the sizer around it takes the scaled size, so the board scrolls to every card
function sizeBoard() {
  const box = $("#boardcards");
  let right = 0, bottom = 0;
  for (const el of box.children) {
    right = Math.max(right, el.offsetLeft + cardW(el)); bottom = Math.max(bottom, el.offsetTop + cardH(el));
  }
  box.style.width = `${right + CARD_GAP}px`; box.style.height = `${bottom + CARD_GAP * 6}px`;
  box.style.transform = `scale(${boardScale})`;
  Object.assign($("#boardsizer").style, { width: `${(right + CARD_GAP) * boardScale}px`, height: `${(bottom + CARD_GAP * 6) * boardScale}px` });
  $("#boardzoom").textContent = `${Math.round(boardScale * 100)}%`;
}
// A card's own size (pinched on the card; 1: normal), drawn as a scale from its top-left corner
const cardSize = (el) => +el.dataset.s || 1;
const cardW = (el) => el.offsetWidth * cardSize(el), cardH = (el) => el.offsetHeight * cardSize(el);
function sizeCard(el, s) { el.dataset.s = s; el.style.transform = s === 1 ? "" : `scale(${s})`; }
// Zoom to s, keeping the board point under (px, py) (relative to the board's box) where it is
function zoomBoard(s, px, py) {
  const cork = $("#boardcork");
  s = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, s));
  if (px == null) { px = cork.clientWidth / 2; py = cork.clientHeight / 2; }
  const bx = (cork.scrollLeft + px) / boardScale, by = (cork.scrollTop + py) / boardScale;
  boardScale = s;
  sizeBoard();
  cork.scrollLeft = bx * s - px; cork.scrollTop = by * s - py;
  try { localStorage.setItem("deck-board-zoom", s.toFixed(3)); } catch { /* this visit only */ }
}
function fitBoard() {
  const cork = $("#boardcork"), box = $("#boardcards");
  const w = box.offsetWidth, hgt = box.offsetHeight;
  if (!w) return;
  zoomBoard(Math.min(1, cork.clientWidth / w, cork.clientHeight / hgt), 0, 0);
  cork.scrollLeft = cork.scrollTop = 0;
}
// The trash can: drop a card on it; tap it for what was dropped, to restore (30 days)
function overTrash(e) {
  const r = $("#boardtrash").getBoundingClientRect(), m = 14; // a little room around it for a finger
  return e.clientX > r.left - m && e.clientX < r.right + m && e.clientY > r.top - m && e.clientY < r.bottom + m;
}
function renderBin() {
  const gone = boardCards.filter((c) => c.deleted).sort((a, b) => b.deleted - a.deleted);
  $("#boardbinlist").replaceChildren(...(gone.length ? gone.map((c) => h("div", { class: "boardbinrow" },
    h("span", {}, c.title || (c.text || c.url || "").split("\n")[0].slice(0, 80) || "Card"),
    h("button", { type: "button", class: "ghost", onclick: async () => {
      await api("POST", `/api/board/${c.id}/restore`).catch((e) => toast(e.message)); await loadBoard(true); renderBin(); } }, "Restore"))) :
    [h("p", { class: "hint" }, "Empty.")]));
}
$("#boardtrash").onclick = () => { if (boardJustDragged) return; renderBin(); $("#boardbin").showModal(); };
// A card's whole text, in a dialog (the square card cuts it)
function boardRead(c) {
  const dlg = $("#boardread");
  $("#boardreadh").textContent = c.title || "";
  $("#boardreadbody").innerHTML = chatMarkdown(c.text || "");
  dlg.showModal();
}
async function resizeCardDone(el) {
  const c = boardCards.find((x) => x.id === el.dataset.id);
  if (!c) return;
  c.s = +cardSize(el).toFixed(3);
  sizeBoard();
  await api("PUT", `/api/board/${c.id}`, { s: c.s }).catch((e) => toast(e.message));
  loadBoard(true);
}
function boardSessionName(f) {
  const s = sessions.find((x) => x.name === f);
  return s ? `${s.title || s.label || realName(f)} · ${hostOf(f)}` : f;
}
// A card shows only what its type needs (user: "reduce fields in card, types instead")
function boardCard(c) {
  const from = cardFrom(c), live = sessions.some((x) => x.name === from);
  const kind = c.kind === "result" ? "note" : c.kind || "note";
  const md = (t) => { const d = h("div", { class: "cprose boardtext" }); d.innerHTML = chatMarkdown(t); return d; };
  let body;
  if (kind === "link") {
    let host = "";
    try { host = new URL(c.url).hostname.replace(/^www\./, ""); } catch { /* shown as is */ }
    body = [h("a", { class: "boardh", href: c.url, target: "_blank", rel: "noopener" }, c.title || host || c.url),
      c.title && host ? h("div", { class: "boardsub" }, host) : null, c.text ? md(c.text) : null];
  } else if (kind === "list") {
    body = [c.title ? h("div", { class: "boardh" }, c.title) : null,
      h("ul", { class: "boardlist" }, ...(c.text || "").split("\n").map((ln, i) => {
        if (!ln.trim()) return null;
        const done = ln.startsWith("[x] ");
        return h("li", { class: done ? "done" : "" }, h("button", { class: "boardtick", "aria-pressed": String(done), "aria-label": done ? "Untick" : "Tick",
          onclick: async () => { await api("PUT", `/api/board/${c.id}`, { tick: i }).catch((e) => toast(e.message)); loadBoard(true); } }),
        h("span", {}, done ? ln.slice(4) : ln));
      }))];
  } else {
    body = [c.title ? h("div", { class: "boardh" }, c.title) : null, c.text ? md(c.text) : null];
  }
  // A square card shows what fits; tap the text to read it all
  if (c.text && kind !== "list" && kind !== "link") body[body.length - 1].onclick = (e) => { if (!boardJustDragged && !e.target.closest("a")) boardRead(c); };
  // Who and when, one quiet line; only on a session's card (yours carry none)
  const meta = from ? h("button", { class: "boardfrom", disabled: live ? null : "", title: `${boardSessionName(from)}${live ? ": open it" : " (ended)"}`,
    onclick: () => { openSession(from); show("term"); } }, `${realName(from)} · ${since(c.at)}`) : null;
  // × removes the card to the trash, and the corner handle resizes it by dragging (user 2026-11-10: "let me resize and
  // click x on board cards"); dragging onto the trash can and pinching still work
  const remove = h("button", { type: "button", class: "boardx", "aria-label": "Remove card", title: "Remove (restorable from the trash for 30 days)",
    onclick: async (e) => {
      e.stopPropagation();
      card.remove(); c.deleted = Date.now() / 1000;
      await api("DELETE", `/api/board/${c.id}`).catch((err) => toast(err.message));
      loadBoard(true);
    } }, "×");
  const grip = h("span", { class: "boardgrip", "aria-hidden": "true", title: "Drag to resize" });
  const card = h("article", { class: `boardcard k-${kind}`, "data-id": c.id, title: "Drag to move; × or the trash can to remove; drag the corner to resize" },
    remove, ...body, meta, grip);
  grip.addEventListener("pointerdown", (e) => {
    if (e.button) return;
    e.stopPropagation(); e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    const start = { x: e.clientX, y: e.clientY, s: cardSize(card), w: card.offsetWidth };
    const move = (ev) => { // the card stays square: its new size follows the larger of the two directions dragged
      const d = Math.max(ev.clientX - start.x, ev.clientY - start.y) / boardScale;
      sizeCard(card, Math.min(3, Math.max(0.3, start.s + d / start.w)));
    };
    const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up);
      grip.removeEventListener("pointercancel", up); resizeCardDone(card); };
    grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up); grip.addEventListener("pointercancel", up);
  });
  // Drag anywhere on the card (user), past a few pixels so a tap still opens a link; a second finger is a pinch instead
  card.addEventListener("pointerdown", (e) => {
    if (e.button || e.target.closest("a, button, .boardgrip")) return;
    if (boardDrag) { boardDrag.cancel(); return; } // second finger: the board's pinch takes over
    boardDrag = { x: e.clientX, y: e.clientY, left: card.offsetLeft, top: card.offsetTop, z: card.style.zIndex, moved: false, id: e.pointerId,
      cancel() { Object.assign(card.style, { left: `${this.left}px`, top: `${this.top}px`, zIndex: this.z }); card.classList.remove("lifted"); boardDrag = null; } };
  });
  card.addEventListener("pointermove", (e) => {
    const d = boardDrag;
    if (!d || e.pointerId !== d.id || !card.isConnected) return;
    if (!d.moved) {
      if (Math.hypot(e.clientX - d.x, e.clientY - d.y) < 6) return;
      d.moved = true;
      card.setPointerCapture(e.pointerId);
      card.classList.add("lifted");
      card.style.zIndex = String(Math.max(0, ...boardCards.map((x) => x.z || 0)) + 1);
      getSelection()?.removeAllRanges();
    }
    e.preventDefault();
    card.style.left = `${Math.max(0, d.left + (e.clientX - d.x) / boardScale)}px`;
    card.style.top = `${Math.max(0, d.top + (e.clientY - d.y) / boardScale)}px`;
    d.bin = overTrash(e);
    $("#boardtrash").classList.toggle("over", d.bin);
    card.classList.toggle("binning", d.bin);
  });
  const drop = async (e) => {
    if (!boardDrag || e.pointerId !== boardDrag.id) return;
    const { moved, bin } = boardDrag;
    boardDrag = null;
    card.classList.remove("lifted", "binning");
    $("#boardtrash").classList.remove("over");
    if (!moved) return;
    if (bin) { // dropped on the trash can
      boardJustDragged = true; setTimeout(() => { boardJustDragged = false; }, 50);
      card.remove(); c.deleted = Date.now() / 1000;
      await api("DELETE", `/api/board/${c.id}`).catch((err) => toast(err.message));
      loadBoard(true);
      return;
    }
    boardJustDragged = true; setTimeout(() => { boardJustDragged = false; }, 50); // the click that ends a drag opens nothing
    Object.assign(c, { x: card.offsetLeft, y: card.offsetTop, z: +card.style.zIndex });
    sizeBoard();
    await api("PUT", `/api/board/${c.id}`, { x: c.x, y: c.y, z: c.z }).catch((err) => toast(err.message));
    loadBoard(true);
  };
  card.addEventListener("pointerup", drop);
  card.addEventListener("pointercancel", drop);
  return card;
}
// Pinch (user: "let me pinch to shrink/zoom"): two fingers zoom about their midpoint; one finger still scrolls
{
  const cork = $("#boardcork");
  let pinch = null;
  boardPinching = () => !!pinch;
  const mid = (t) => { const r = cork.getBoundingClientRect();
    return [(t[0].clientX + t[1].clientX) / 2 - r.left, (t[0].clientY + t[1].clientY) / 2 - r.top]; };
  const gap = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  // Both fingers on one card: that card's size (user: "let me shrink cards by pinch"); else the whole board's zoom
  const cardAt = (t) => { const a = t[0].target.closest?.(".boardcard"); return a && a === t[1].target.closest?.(".boardcard") ? a : null; };
  cork.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 2) return;
    boardDrag?.cancel();
    const card = cardAt(e.touches);
    pinch = { d: gap(e.touches), s: card ? cardSize(card) : boardScale, card };
    e.preventDefault();
  }, { passive: false });
  cork.addEventListener("touchmove", (e) => {
    if (!pinch || e.touches.length !== 2) return;
    e.preventDefault();
    const s = pinch.s * gap(e.touches) / pinch.d;
    if (pinch.card) sizeCard(pinch.card, Math.min(3, Math.max(0.3, s)));
    else zoomBoard(s, ...mid(e.touches));
  }, { passive: false });
  cork.addEventListener("touchend", (e) => {
    if (e.touches.length >= 2 || !pinch) return;
    if (pinch.card) resizeCardDone(pinch.card);
    pinch = null;
  });
  let wheelDone = 0;
  cork.addEventListener("wheel", (e) => { // Ctrl+wheel, and a trackpad's pinch (sent as Ctrl+wheel): over a card its size
    if (!e.ctrlKey) return;
    e.preventDefault();
    const card = e.target.closest(".boardcard");
    if (card) {
      sizeCard(card, Math.min(3, Math.max(0.3, cardSize(card) * Math.exp(-e.deltaY / 200))));
      clearTimeout(wheelDone); wheelDone = setTimeout(() => resizeCardDone(card), 400);
      return;
    }
    const r = cork.getBoundingClientRect();
    zoomBoard(boardScale * Math.exp(-e.deltaY / 200), e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  $("#boardin").onclick = () => zoomBoard(boardScale * 1.25);
  $("#boardout").onclick = () => zoomBoard(boardScale / 1.25);
  $("#boardzoom").onclick = () => zoomBoard(1);
  $("#boardfit").onclick = fitBoard;
}
// The user's own cards (user: "let me add"): + picks a type, which asks only for what that type needs
let boardType = "note";
function setBoardType(k) {
  boardType = k;
  for (const b of $("#boardftype").children) b.classList.toggle("on", b.dataset.k === k);
  $("#boardfurl").hidden = k !== "link";
  $("#boardftext").hidden = k === "link";
  $("#boardftitle").placeholder = k === "list" ? "List name" : "Title (optional)";
  $("#boardftext").placeholder = k === "list" ? "One item per line" : "Note (Markdown)";
}
for (const b of $("#boardftype").children) b.onclick = () => { setBoardType(b.dataset.k); $(b.dataset.k === "link" ? "#boardfurl" : "#boardftext").focus(); };
$("#boardadd").onclick = () => {
  $("#boardform").reset(); $("#boardferr").textContent = ""; setBoardType(boardType);
  $("#boardnew-dlg").showModal(); $(boardType === "link" ? "#boardfurl" : "#boardftext").focus();
};
$("#boardfcancel").onclick = () => $("#boardnew-dlg").close();
$("#boardform").onsubmit = async (e) => {
  e.preventDefault();
  const k = boardType, body = { kind: k, title: $("#boardftitle").value };
  if (k === "link") body.url = $("#boardfurl").value.trim();
  else body.text = k === "list" ? $("#boardftext").value.split("\n").map((l) => l.trim()).filter(Boolean).join("\n") : $("#boardftext").value;
  try {
    await api("POST", "/api/board", body);
    $("#boardnew-dlg").close();
    loadBoard(true);
  } catch (err) { $("#boardferr").textContent = err.message; }
};
addEventListener("resize", () => { if (view === "board" && !boardDrag) renderBoard(); });
