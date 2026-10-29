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

// ---- PDF pages: drawn as they scroll into view, at the reader's zoom ----
const pageScale = (el) => ($("#docbody").clientWidth - 24) / +el.dataset.bw * docZoom;
function sizePage(el) { // its box at the zoom now; the canvas stretches and the text layer follows --scale-factor
  const s = pageScale(el);
  Object.assign(el.style, { width: `${+el.dataset.bw * s}px`, height: `${+el.dataset.bh * s}px` });
  el.style.setProperty("--scale-factor", s);
}
// Draw a page at the zoom now: into a new canvas, swapped in when done, so a redraw never blanks it
async function drawPage(el, first) {
  const { pdf } = docPdf || {};
  if (!pdf) return;
  el.dataset.drawn = "1";
  const page = await pdf.getPage(+el.dataset.n), base = page.getViewport({ scale: 1 });
  Object.assign(el.dataset, { bw: base.width, bh: base.height });
  const s = pageScale(el), out = page.getViewport({ scale: s * devicePixelRatio });
  if (first) sizePage(el);
  const canvas = h("canvas", { width: out.width, height: out.height });
  await page.render({ canvasContext: canvas.getContext("2d"), viewport: out }).promise;
  if (!el.isConnected || pageScale(el) !== s) return; // zoomed again meanwhile: that redraw wins
  const old = el.querySelector("canvas");
  if (old) old.replaceWith(canvas);
  else el.prepend(canvas);
}
// A page's selectable text (pdf.js's text layer: a transparent span per run of text) is built when the pointer comes
// onto the page, and only the two pages last pointed at keep one: a dense PDF has over a thousand spans a page, and
// four pages of them made the whole page repaint slowly (7 fps), dividers and all
const textPages = [];
async function textLayerFor(el) {
  if (!docPdf || el.querySelector(".textLayer") || el.dataset.texting) return;
  el.dataset.texting = "1";
  try {
    const lib = await loadPdfjs(), page = await docPdf.pdf.getPage(+el.dataset.n);
    const layer = h("div", { class: "textLayer" });
    el.append(layer);
    await new lib.TextLayer({ textContentSource: page.streamTextContent(), container: layer,
      viewport: page.getViewport({ scale: pageScale(el) }) }).render();
    textPages.push(el);
    const sel = getSelection();
    while (textPages.length > 2) { // the oldest goes, unless it holds the selection being made
      const i = textPages.findIndex((p) => p !== el && !(sel?.rangeCount && p.contains(sel.anchorNode)));
      if (i < 0) break;
      textPages.splice(i, 1)[0].querySelector(".textLayer")?.remove();
    }
  } finally { delete el.dataset.texting; }
}
$("#docbody").addEventListener("pointerover", (e) => { const pg = e.target.closest?.(".docpage"); if (pg) textLayerFor(pg); });
// + and − at the top of the reader: the pages (or text) bigger or smaller, from half to three times the pane's width.
// The pages resize at once (stretched), keeping the place; the ones drawn are redrawn sharp behind the scenes. The
// same when the pane's width changes (the window, the columns).
let docZoom = 1, docPdf = null, docWidth = 0, docRedraw = 0;
// Parsed PDFs by document, so switching reader tabs doesn't fetch (seconds over the tunnel) and parse them again
const pdfCache = new Map();
function refitDoc() {
  const body = $("#docbody"), pages = [...body.querySelectorAll(".docpage")];
  if (!pages.length) return;
  const keep = body.scrollTop / body.scrollHeight;
  pages.forEach(sizePage);
  body.scrollTop = keep * body.scrollHeight;
  clearTimeout(docRedraw); // sharp again once the resizing stops
  docRedraw = setTimeout(() => { for (const el of pages) if (el.dataset.drawn && el.isConnected) drawPage(el); }, 150);
}
for (const [btn, by] of [["#doczoomout", -1], ["#doczoomin", 1]]) $(btn).onclick = () => {
  docZoom = Math.min(3, Math.max(0.5, Math.round((docZoom + by * 0.1) * 100) / 100));
  $("#doczoomval").textContent = `${Math.round(docZoom * 100)}%`;
  $("#docbody").style.fontSize = `${Math.round(14 * docZoom)}px`; // text documents: their font size
  refitDoc();
};
new ResizeObserver(() => {
  const w = $("#docbody").clientWidth;
  if (w && w !== docWidth) { docWidth = w; refitDoc(); }
}).observe($("#docbody"));
// Text selected in the reader goes with the next message, as a quote (one at a time; × above the box drops it)
$("#docbody").addEventListener("pointerup", () => setTimeout(() => {
  const sel = getSelection(), q = sel?.toString().trim();
  if (!q || q.length < 3 || !$("#docbody").contains(sel.anchorNode)) return; // a stray click's letter or two isn't a quote
  const page = sel.anchorNode.parentElement?.closest(".docpage")?.dataset.n;
  const doc = $("#doctabs .doctab.on")?.title || "the document";
  attachedItems = attachedItems.filter((a) => a.kind !== "quote");
  attachedItems.push({ id: "quote", kind: "quote", open: false, text: q,
    title: `${doc}${page ? `, page ${page}` : ""}: “${q.slice(0, 60)}${q.length > 60 ? "…" : ""}”`, from: `${doc}${page ? `, page ${page}` : ""}` });
  renderAttached();
}, 0));
