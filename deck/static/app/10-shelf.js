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
// The shelf's list cuts a long document's text ("cut": its full length); this fetches it whole
async function fullItem(it) {
  if (!it?.cut) return it;
  try { return await api("GET", `/api/shelf/${it.id}`); } catch { return it; }
}
async function loadShelf() {
  const shelf = $("#shelf");
  shelf.classList.toggle("shut", !shelfOpen);
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
    const fill = () => {
      if (!el.open || body.innerHTML) return;
      if (it.kind === "link") {
        body.innerHTML = chatMarkdown(it.text);
        body.prepend(h("a", { class: "shelfurl", href: it.url, target: "_blank", rel: "noopener noreferrer" }, it.url));
      } else if (it.kind === "doc") body.append(h("pre", { class: "doctext" }, it.text.slice(0, 3000) + (it.text.length > 3000 ? "\n…" : "")));
      else body.innerHTML = chatMarkdown(it.text);
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
  $("#shelflist").replaceChildren(
    ...live.filter((it) => !it.folder || !folders.has(it.folder)).map((it) => it.kind === "folder" ? folder(it) : card(it, copy(it), ...docActs(it), ...mainAct(it))),
    ...(can ? [can] : []));
  renderProjBar();
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
    d.ghost.style.transform = `translate(${e.clientX + 12}px, ${e.clientY - 14}px)`;
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
// whole text over the screen, Download saves it
const docActs = (it) => it.kind === "link" ? []
  : it.kind === "doc" && wide.matches ? [h("button", { type: "button", onclick: () => { setReview(true); show("term"); openDoc(it.id); } }, "Review")]
  : [...(wide.matches ? [] : [h("button", { type: "button", onclick: () => readFull(it) }, "Open")]),
     h("button", { type: "button", onclick: (e) => downloadItem(it, e.target) }, "Download")];
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
  const fs = window.Capacitor?.Plugins?.Filesystem;
  // the app's WebView drops <a download>: write to Documents/Deck instead. A document's file itself (a PDF, linked or
  // uploaded, from whichever host has it: docFileUrl), else its text
  const textName = it.file?.name && !/\.(md|txt)$/i.test(name) ? `${name}.txt` : name;
  const file = it.file ? await fetch(docFileUrl(it)).then((r) => (r.ok ? r.blob() : null)).catch(() => null) : null;
  if (fs) {
    try {
      if (file) {
        const data = await new Promise((ok, bad) => {
          const rd = new FileReader();
          rd.onload = () => ok(String(rd.result).split(",")[1]); rd.onerror = () => bad(rd.error);
          rd.readAsDataURL(file);
        });
        await fs.writeFile({ path: `Deck/${name}`, data, directory: "DOCUMENTS", recursive: true }); // base64: the bytes
      } else await fs.writeFile({ path: `Deck/${textName}`, data: f.text, directory: "DOCUMENTS", encoding: "utf8", recursive: true });
      b.textContent = "Saved ✓"; toast(`Saved to Documents/Deck/${file ? name : textName}`);
    } catch (err) { toast(`Can't save (${err.message || err}): opened it instead`); readFull(it); } // still readable, copyable
    return;
  }
  // a browser: the file itself when there is one (on this host or another), else the text
  const a = file ? h("a", { href: URL.createObjectURL(file), download: name })
    : h("a", { href: URL.createObjectURL(new Blob([f.text], { type: "text/plain;charset=utf-8" })), download: textName });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 60000);
}
