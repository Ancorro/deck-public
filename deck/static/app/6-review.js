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
// The project's main page (deck/shelf.py main): the newest shelf link marked main for the open chat's project
// (deck/core.py project_of: its git repo), in a slim bar atop the chat; every session in the project shows the same one
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
  // the app's WebView drops <a download>: write the text to Documents/Deck instead (an uploaded PDF's text, not the PDF)
  const textName = it.file?.name && !/\.(md|txt)$/i.test(name) ? `${name}.txt` : name;
  if (fs) {
    try {
      await fs.writeFile({ path: `Deck/${textName}`, data: f.text, directory: "DOCUMENTS", encoding: "utf8", recursive: true });
      b.textContent = "Saved ✓"; toast(`Saved to Documents/Deck/${textName}`);
    } catch (err) { toast(`Can't save (${err.message || err}): opened it instead`); readFull(it); } // still readable, copyable
    return;
  }
  // a browser: the original file when there is one on this host, else the text
  const a = it.file && !it.file.linked ? h("a", { href: `/api/shelf/${it.id}/file`, download: name })
    : h("a", { href: URL.createObjectURL(new Blob([f.text], { type: "text/plain;charset=utf-8" })), download: textName });
  document.body.append(a); a.click(); a.remove();
}

// ---- Web (PC): a web page beside the chat, in the reader's place; while it's open, links clicked in Deck (the chat,
// the shelf, the project bar) open in it rather than in a browser tab. Pages that refuse to be framed (their
// X-Frame-Options or CSP frame-ancestors, read by the host: /api/web/check; claude.ai artifacts, GitHub) open in the
// docked window instead: one browser window, reused for each such page, laid over the Web pane (openDocked).
let webOn = false, webUrl = "", webLoad = 0, dockWin = null;
const frameOk = new Map(); // origin -> whether its pages may be framed (/api/web/check)
function openDocked(url) {
  // Over the Web pane, in screen pixels (the window's frame and the browser's bars above the page, estimated from the
  // outer and inner sizes); with no pane on screen, the right half of the screen
  const r = $("#webpane").getBoundingClientRect(), chrome = window.outerHeight - window.innerHeight;
  const side = Math.max(0, (window.outerWidth - window.innerWidth) / 2);
  const box = r.width > 300
    ? { left: window.screenX + side + r.left, top: window.screenY + chrome + r.top, width: r.width, height: r.height }
    : { left: (screen.availLeft ?? 0) + screen.availWidth / 2, top: screen.availTop ?? 0, width: screen.availWidth / 2, height: screen.availHeight };
  // The page it shows can't reach Deck (no opener), which leaves Deck no right to send it elsewhere: the next page
  // gets a new window in the same place, closing the last one where it can (sites with a Cross-Origin-Opener-Policy,
  // GitHub and claude.ai among them, cut the window off from Deck, so theirs stay open for the user to close)
  if (dockWin && !dockWin.closed) dockWin.close();
  dockWin = window.open("about:blank", "",`popup,${Object.entries(box).map(([k, v]) => `${k}=${Math.round(v)}`).join(",")}`);
  if (!dockWin) return false; // the browser blocked the window
  dockWin.opener = null; // the page can't reach back into Deck
  dockWin.location.href = url;
  return true;
}
// In the Deck window (desktop/window_windows.go) the page goes to a real browser laid over the pane instead: any site
// loads, logins and all. window.deckHost tells it the page and the pane's box in window pixels; it hides while the Web
// view is shut, another view shows, or a dialog covers the pane (a native window would sit on top of the dialog).
const deckHost = typeof window.deckHost === "function" ? window.deckHost : null;
function syncHost(url = "") {
  if (!deckHost) return;
  const r = $("#webbody").getBoundingClientRect(), d = devicePixelRatio;
  const covered = !webOn || r.width < 40 || r.height < 40 || document.querySelector("dialog[open]");
  deckHost(JSON.stringify(covered && !url ? { t: "hide" }
    : { t: url ? "web" : "bounds", url, x: r.left * d, y: r.top * d, w: r.width * d, h: r.height * d })).catch(() => {});
}
if (deckHost) { // at most once a frame
  let queued = 0;
  const later = () => { if (!queued) queued = requestAnimationFrame(() => { queued = 0; syncHost(); }); };
  new ResizeObserver(later).observe($("#webbody"));
  window.addEventListener("resize", later);
  new MutationObserver(later).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["open", "hidden"] });
}
function setWeb(on) {
  webOn = on;
  if (on && reviewOn) setReview(false);
  $("#app").classList.toggle("review", on || reviewOn); // the same split as Review
  $("#webpane").hidden = !on;
  try { localStorage.setItem("deck-web", on ? webUrl || "1" : ""); } catch { /* this visit only */ }
  fit?.fit();
  syncHost();
}
async function openWeb(url, restore = false) { // restore: Deck reopening its last page, which opens no window
  if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
  webUrl = url;
  $("#webaddr").value = url;
  $("#webout").href = url;
  if (!webOn) { setWeb(true); show("term"); } else setWeb(true); // remembers the page
  const mine = ++webLoad, body = $("#webbody");
  if (deckHost) { body.replaceChildren(); requestAnimationFrame(() => syncHost(url)); return; } // laid out first
  body.replaceChildren(h("p", { class: "hint" }, "Loading…"));
  // a site's answer is kept for the visit: the docked window may open only within a few seconds of the click
  const origin = new URL(url).origin;
  const frameable = frameOk.has(origin) ? frameOk.get(origin)
    : (await api("GET", `/api/web/check?url=${encodeURIComponent(url)}`).catch(() => ({ frameable: null }))).frameable;
  if (frameable !== null) frameOk.set(origin, frameable);
  if (mine !== webLoad) return;
  if (frameable === false) {
    const docked = !restore && openDocked(url);
    body.replaceChildren(h("p", { class: "hint" }, docked
      ? "This site can't be shown inside Deck, so it's in its own window over this pane (your browser, your logins). "
      : restore ? "This site can't be shown inside Deck: it opens in its own window. "
      : "This site can't be shown inside Deck, and the browser blocked its window (allow pop-ups for Deck). ",
      h("button", { type: "button", class: "ghost small", onclick: () => openDocked(url) }, docked ? "Open it again" : "Open it beside Deck"),
      " ", h("a", { href: url, target: "_blank", rel: "noopener noreferrer", "data-tab": "" }, "or a browser tab ↗")));
    return;
  }
  body.replaceChildren(h("iframe", { src: url, title: url, referrerpolicy: "no-referrer",
    allow: "clipboard-read; clipboard-write; fullscreen" }));
}
$("#webform").onsubmit = (e) => { e.preventDefault(); if ($("#webaddr").value.trim()) openWeb($("#webaddr").value.trim()); };
$("#webreload").onclick = () => { if (webUrl) openWeb(webUrl); };
// A link anywhere in Deck while the Web view is open goes there (not the Web view's own, nor one marked for a tab;
// Ctrl/Cmd/Shift-click still opens a browser tab)
document.addEventListener("click", (e) => {
  const a = webOn && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0 && e.target.closest?.("a[href]");
  if (!a || a.closest("#webpane") || a.hasAttribute("data-tab") || !/^https?:/i.test(a.href)) return;
  if (new URL(a.href).origin === location.origin) return; // Deck's own pages and downloads
  e.preventDefault(); e.stopPropagation();
  openWeb(a.href);
}, true);
const openLink = (url) => webOn ? openWeb(url) : window.open(url, "_blank", "noopener");

// ---- Review (PC): the chat on the left half of the screen, the document on the right ----
// Open items are tabs over the reader (any shelf item: a document, or a saved turn); drag one in from the shelf, or
// drop files from the computer. Tabs and the open one are remembered per browser.
let reviewDoc = "", docRender = 0, docTabs = [];
try { docTabs = JSON.parse(localStorage.getItem("deck-review-tabs") || "[]"); } catch { /* none */ }
const docTitle = (it) => it.title || it.file?.name || it.text.split("\n")[0].slice(0, 60) || "untitled";
function saveReview() {
  try {
    localStorage.setItem("deck-review", reviewOn ? reviewDoc || "1" : "");
    localStorage.setItem("deck-review-tabs", JSON.stringify(docTabs));
  } catch { /* this visit only */ }
}
function setReview(on) {
  reviewOn = on;
  if (on && webOn) setWeb(false);
  $("#app").classList.toggle("review", on || webOn);
  $("#docpane").hidden = !on;
  saveReview();
  if (on) docTabsRender();
  fit?.fit();
}
// A machine notes file (the infra repo, deck/notes.py) as a reader tab "note:<path>": a text document like a shelf
// one, whose saves commit it and push it to the other hosts (saveDocText)
const noteDocs = new Map();
async function noteDoc(id, fresh = false) {
  if (!fresh && noteDocs.has(id)) return noteDocs.get(id);
  const path = id.slice(5), { text } = await api("GET", `/api/notes/file?path=${encodeURIComponent(path)}`);
  const it = Object.assign(noteDocs.get(id) || {}, { id, kind: "doc", note: path, title: path, text,
    file: { name: path, type: /\.(md|markdown)$/i.test(path) ? "text/markdown" : "text/plain", host: HOME_HOST } });
  noteDocs.set(id, it);
  return it;
}
const saveDocText = (it, text) => it.note
  ? api("PUT", "/api/notes/file", { path: it.note, text }).then(() => { it.text = text; })
  : api("PUT", `${it.file && it.file.host !== HOME_HOST ? `/peer/${encodeURIComponent(it.file.host)}` : ""}/api/shelf/${it.id}/text`, { text });
async function docTabsRender(fresh = true) {
  const items = (fresh || !shelfLive.length ? await api("GET", "/api/shelf").catch(() => []) : [...shelfLive])
    .filter((x) => !x.deleted && x.kind !== "folder");
  for (const id of docTabs) if (id.startsWith("note:")) items.push(await noteDoc(id).catch(() => null));
  docTabs = docTabs.filter((id) => items.some((x) => x?.id === id)); // ones deleted from the shelf close
  if (!docTabs.includes(reviewDoc)) reviewDoc = docTabs.at(-1) || "";
  saveReview();
  $("#doctabs").replaceChildren(...docTabs.map((id) => {
    const it = items.find((x) => x?.id === id);
    return h("div", { class: `doctab${id === reviewDoc ? " on" : ""}`, title: docTitle(it) },
      h("button", { type: "button", class: "doctabname", onclick: () => openDoc(id) }, docTitle(it)),
      h("button", { type: "button", class: "doctabx", "aria-label": "Close", onclick: () => closeDoc(id) }, "×"));
  }));
  if (!reviewDoc) {
    docRender++;
    $("#docbody").replaceChildren(h("p", { class: "hint" }, "Drag something from the shelf here, or drop a PDF or text file."));
  }
  return items;
}
function closeDoc(id) {
  docTabs = docTabs.filter((x) => x !== id);
  if (reviewDoc === id) { reviewDoc = docTabs.at(-1) || ""; if (reviewDoc) return openDoc(reviewDoc); }
  docTabsRender();
}
// Files dropped on the reader from the computer: onto the shelf, then open
$("#docpane").addEventListener("dragover", (e) => { if (e.dataTransfer?.types.includes("Files")) { e.preventDefault(); $("#docpane").classList.add("dropchat"); } });
$("#docpane").addEventListener("dragleave", () => $("#docpane").classList.remove("dropchat"));
$("#docpane").addEventListener("drop", async (e) => {
  e.preventDefault();
  $("#docpane").classList.remove("dropchat");
  for (const f of e.dataTransfer?.files || []) {
    try { toast(`Reading ${f.name}…`); const it = await addDoc(f); await openDoc(it.id); }
    catch (err) { toast(err.message || `${f.name}: couldn't read it`); }
  }
  if (shelfOpen) loadShelf();
});
async function openDoc(id) {
  await closeEditor(); // an open editor saves first
  reviewDoc = id;
  if (id && !docTabs.includes(id)) docTabs.push(id);
  if (id.startsWith("note:")) await noteDoc(id, true).catch((e) => toast(e.message)); // as it is now (others edit it)
  const items = await docTabsRender(false);
  const body = $("#docbody"), mine = ++docRender;
  const found = items.find((d) => d?.id === id); // a PDF draws from its file: its full text isn't needed
  const it = found?.file?.type === "application/pdf" ? found
    : found?.file?.linked ? await api("GET", `${docHost(found)}/api/shelf/${id}`).catch(() => found) : await fullItem(found);
  if (!it || mine !== docRender) return;
  watchLinked(it);
  // A text document as is (Markdown formatted); a saved turn as the chat shows it
  const text = () => body.replaceChildren(it.kind !== "doc" || /\.(md|markdown)$/i.test(it.title || "")
    ? Object.assign(h("div", { class: "cprose" }), { innerHTML: chatMarkdown(it.text) })
    : h("pre", { class: "doctext" }, syntaxOf(it) ? highlight(it.text, syntaxOf(it)) : it.text));
  body.style.fontSize = `${Math.round(14 * docZoom)}px`; // text documents zoom by their font size
  $("#doczoomval").textContent = `${Math.round(docZoom * 100)}%`;
  // Text documents (txt, md, code...) can be edited here; PDFs and Word files are read-only
  $("#docedit").hidden = !(it.kind === "doc" && (!it.file || it.file.type.startsWith("text/")));
  $("#docedit").onclick = () => editDoc(it);
  if (it.file?.type === DOCX) { // Word: as HTML (mammoth), formatting and all
    body.replaceChildren(h("p", { class: "hint" }, "Loading…"));
    try {
      const r = await fetch(docFileUrl(it));
      if (!r.ok) throw new Error("file not reachable");
      const { value } = await (await loadMammoth()).convertToHtml({ arrayBuffer: await r.arrayBuffer() });
      if (mine === docRender) body.replaceChildren(h("div", { class: "cprose docword" }, ...safeHtml(value)));
    } catch { if (mine === docRender) text(); }
    return;
  }
  if (it.file?.type !== "application/pdf") return text();
  const keep = body.scrollHeight ? body.scrollTop / body.scrollHeight : 0; // a zoom keeps the place
  try {
    if (docPdf?.id !== id) {
      if (!pdfCache.has(id)) {
        body.replaceChildren(h("p", { class: "hint" }, "Loading…"));
        pdfCache.set(id, (async () => {
          const r = await fetch(docFileUrl(it));
          if (!r.ok) throw new Error("file not reachable");
          return (await loadPdfjs()).getDocument({ data: new Uint8Array(await r.arrayBuffer()) }).promise;
        })());
      }
      try { docPdf = { id, pdf: await pdfCache.get(id) }; } catch (e) { pdfCache.delete(id); throw e; }
    }
    const { pdf } = docPdf;
    if (mine !== docRender) return;
    // A placeholder per page at its size (the pane's width times the zoom); each draws when it scrolls near: the page,
    // and pdf.js's text layer over it, so its text can be selected (and quoted in a message)
    const first = (await pdf.getPage(1)).getViewport({ scale: 1 });
    const pages = Array.from({ length: pdf.numPages }, (_, i) => h("div", { class: "docpage", "data-n": i + 1,
      "data-bw": first.width, "data-bh": first.height })); // its size at scale 1 (each page's own once drawn)
    body.replaceChildren(...pages);
    pages.forEach(sizePage);
    body.scrollTop = keep * body.scrollHeight;
    const seen = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting && !e.target.dataset.drawn) drawPage(e.target, true);
    }), { root: body, rootMargin: "800px 0px" });
    pages.forEach((p) => seen.observe(p));
  } catch { if (mine === docRender) text(); } // the file is gone or its host is offline: its text
}
// Syntax colours for source documents (user: "IDE-like"): LaTeX and BibTeX, by a small tokenizer here, not a CDN
// library. The read view shows the coloured text; the editor draws it behind a transparent textarea (same font,
// padding and wrapping), so typing stays a plain textarea's.
const SYNTAX = { tex: /\.(tex|sty|cls|ltx)$/i, bib: /\.bib$/i };
const syntaxOf = (it) => Object.keys(SYNTAX).find((k) => SYNTAX[k].test(it.file?.name || it.title || "")) || "";
const TEX_RULES = [
  ["cm", /%.*/y],
  ["mt", /\$\$[\s\S]*?\$\$|\$(?:\\.|[^$\\])*\$|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)/y],
  ["sc", /\\(?:part|chapter|(?:sub){0,2}section|paragraph|title|caption)\*?(?![a-zA-Z@])/y],
  ["env", /\\(?:begin|end)(?![a-zA-Z@])\s*\{[^}]*\}/y],
  ["ref", /\\(?:cite[tp]?|citep|citet|ref|eqref|label|autoref|cref|Cref|url|href|input|include|includegraphics|bibliography|bibliographystyle)\*?(?![a-zA-Z@])/y],
  ["cmd", /\\(?:[a-zA-Z@]+\*?|.)/y],
  ["br", /[{}[\]]/y],
  ["op", /[&~^_#]/y],
];
const BIB_RULES = [
  ["cm", /%.*/y],
  ["env", /@[a-zA-Z]+/y],
  ["key", /\b[a-zA-Z_-]+(?=\s*=)/y],
  ["str", /"(?:\\.|[^"\\])*"/y],
  ["cmd", /\\(?:[a-zA-Z@]+|.)/y],
  ["num", /(?<![\w/.-])\d+(?![\w/.-])/y], // not a URL's or a key's digits
  ["br", /[{}]/y],
  ["op", /[=,]/y],
];
function highlight(text, kind) {
  const rules = kind === "bib" ? BIB_RULES : TEX_RULES, frag = document.createDocumentFragment();
  let i = 0, plain = "";
  const flush = () => { if (plain) frag.append(plain); plain = ""; };
  while (i < text.length) {
    let hit = null;
    for (const [cls, re] of rules) {
      re.lastIndex = i;
      const m = re.exec(text);
      if (m && m[0]) { hit = [cls, m[0]]; break; }
    }
    if (!hit) { plain += text[i++]; continue; }
    flush();
    frag.append(h("span", { class: `sx-${hit[0]}` }, hit[1]));
    i += hit[1].length;
  }
  flush();
  return frag;
}
// A document's file lives on the host it was added on: another host's through the /peer proxy
const docHost = (it) => it.file.host === HOME_HOST ? "" : `/peer/${encodeURIComponent(it.file.host)}`;
const docFileUrl = (it) => `${docHost(it)}/api/shelf/${it.id}/file`;
// A linked document (a file on disk, POST /api/shelf/link) redraws when its file changes (a build rewrites the PDF,
// git pulls the source, an agent edits it): its change time is checked every 2 s while it is the one shown and isn't
// being edited
let linkWatch = 0;
function watchLinked(it) {
  clearInterval(linkWatch);
  if (!it.file?.linked) return;
  let seen = null;
  linkWatch = setInterval(async () => {
    if (reviewDoc !== it.id || !reviewOn) return clearInterval(linkWatch);
    if (docEditing || $("#docbody .docedit") || document.hidden) return;
    const m = await api("GET", `${docHost(it)}/api/shelf/${it.id}/mtime`).catch(() => null);
    if (!m) return;
    const key = `${m.mtime}:${m.size}`;
    if (seen !== null && key !== seen) {
      pdfCache.delete(it.id);
      if (docPdf?.id === it.id) docPdf = null;
      openDoc(it.id);
    }
    seen = key;
  }, 2000);
}
