// ---- the rich editor (user): Markdown documents edit like a Google Doc, on a white page with a toolbar, saved as they
// change. TipTap (ProseMirror) draws and edits; marked reads the Markdown in, turndown writes it back out, so the shelf
// and agents keep plain Markdown. All from a CDN, the first time a document is edited: TipTap from esm.sh, which gives
// its packages one shared ProseMirror (jsdelivr's builds load two, and Enter then fails).
let tiptapLib = null;
function loadTiptap() {
  tiptapLib ||= Promise.all(["@tiptap/core@2.11.5", "@tiptap/starter-kit@2.11.5", "@tiptap/extension-link@2.11.5",
    "@tiptap/extension-placeholder@2.11.5", "marked@15.0.7", "turndown@7.2.0"].map((p) => import(`https://esm.sh/${p}`)))
    .then(([core, kit, link, ph, marked, td]) => {
      const turndown = new td.default({ headingStyle: "atx", codeBlockStyle: "fenced", bulletListMarker: "-", emDelimiter: "*" });
      turndown.addRule("strike", { filter: ["s", "del", "strike"], replacement: (c) => `~~${c}~~` });
      // tight lists ("- item", "1. item"): an editor's list item wraps its text in a paragraph, which turndown spaces out
      turndown.addRule("listItem", { filter: "li", replacement: (content, node, o) => {
        const list = node.parentNode, i = [...list.children].indexOf(node);
        const mark = list.nodeName === "OL" ? `${(+list.getAttribute("start") || 1) + i}. ` : `${o.bulletListMarker} `;
        const body = content.replace(/^\n+/, "").replace(/\n+$/, "").replace(/\n\n+/g, "\n").replace(/\n/g, `\n${" ".repeat(mark.length)}`);
        return `${mark}${body}\n`;
      } });
      return { Editor: core.Editor, StarterKit: kit.default, Link: link.default, Placeholder: ph.default,
        toHtml: (md) => marked.marked.parse(md || ""), toMd: (html) => turndown.turndown(html).trim() + "\n" };
    });
  return tiptapLib;
}
const isMarkdown = (it) => /\.(md|markdown)$/i.test(it.title || "") || it.file?.type === "text/markdown";
// Markdown the rich editor can't hold (tables, checkbox lists, raw HTML, footnotes): edited as plain text, so nothing is lost
const richSafe = (md) => !/^\s*\|.*\|\s*$/m.test(md) && !/^\s*[-*+] \[[ xX]\]/m.test(md) && !/<[a-zA-Z][^>]*>/.test(md) && !/\[\^[^\]]+\]/.test(md);
let docEditing = null; // the open editor: {flush, destroy}
async function closeEditor() {
  const ed = docEditing;
  docEditing = null;
  if (ed) { await ed.flush(); ed.destroy(); }
}
async function richEditor(it) {
  const body = $("#docbody"), mine = ++docRender; // a redraw of the document now would throw the edit away
  $("#docedit").hidden = true;
  body.replaceChildren(h("p", { class: "hint" }, "Opening the editor…"));
  let lib;
  try { lib = await loadTiptap(); } catch { toast("Couldn't load the editor"); return editDoc(it, true); }
  if (mine !== docRender) return;
  const title = h("input", { class: "richtitle", value: (it.title || "Untitled.md").replace(/\.(md|markdown)$/i, ""), "aria-label": "Title",
    ...(it.note ? { readonly: "" } : {}) }); // a notes file keeps its path
  const status = h("span", { class: "richstatus" }, "Saved");
  const page = h("div", { class: "richpage" });
  let saved = it.text, timer = 0, edited = false; // only an edit saves: the round trip through HTML isn't byte-exact
  const editor = new lib.Editor({
    element: page,
    extensions: [lib.StarterKit, lib.Link.configure({ openOnClick: false }), lib.Placeholder.configure({ placeholder: "Start writing…" })],
    content: lib.toHtml(it.text),
    // a notes file saves on Done or Ctrl+S, not as it changes: each save is a commit pushed to every host
    onUpdate: () => { edited = true; status.textContent = it.note ? "Not saved (Ctrl+S)" : "Editing…"; clearTimeout(timer); if (!it.note) timer = setTimeout(save, 900); },
    onTransaction: () => paint(),
  });
  async function save() {
    clearTimeout(timer);
    if (!edited) return;
    const md = lib.toMd(editor.getHTML());
    if (md === saved) { status.textContent = "Saved"; return; }
    status.textContent = "Saving…";
    try { await saveDocText(it, md); saved = md; status.textContent = it.note ? "Saved, pushing" : "Saved"; }
    catch (e) { status.textContent = "Not saved"; toast(e.message); }
  }
  const saveTitle = async () => {
    const t = `${title.value.trim() || "Untitled"}.md`;
    if (t === it.title || it.note) return;
    it.title = t;
    await api("POST", `/api/shelf/${it.id}/rename`, { title: t }).catch((e) => toast(e.message));
    docTabsRender(); if (shelfOpen) loadShelf();
  };
  title.addEventListener("change", saveTitle);
  title.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); editor.commands.focus("start"); } });
  // the toolbar: each button runs a command and shows whether it's on where the cursor is
  const c = () => editor.chain().focus();
  const tools = [
    ["↶", "Undo (Ctrl+Z)", () => c().undo().run()], ["↷", "Redo (Ctrl+Y)", () => c().redo().run()], "|",
    ["Text", "Normal text", () => c().setParagraph().run(), () => editor.isActive("paragraph")],
    ["H1", "Title", () => c().toggleHeading({ level: 1 }).run(), () => editor.isActive("heading", { level: 1 })],
    ["H2", "Heading", () => c().toggleHeading({ level: 2 }).run(), () => editor.isActive("heading", { level: 2 })],
    ["H3", "Subheading", () => c().toggleHeading({ level: 3 }).run(), () => editor.isActive("heading", { level: 3 })], "|",
    ["B", "Bold (Ctrl+B)", () => c().toggleBold().run(), () => editor.isActive("bold"), "b"],
    ["I", "Italic (Ctrl+I)", () => c().toggleItalic().run(), () => editor.isActive("italic"), "i"],
    ["S", "Strikethrough", () => c().toggleStrike().run(), () => editor.isActive("strike"), "s"],
    ["</>", "Code", () => c().toggleCode().run(), () => editor.isActive("code")],
    ["🔗", "Link", () => {
      const was = editor.getAttributes("link").href || "", url = prompt("Link to", was || "https://");
      if (url === null) return;
      if (url.trim()) c().extendMarkRange("link").setLink({ href: url.trim() }).run(); else c().extendMarkRange("link").unsetLink().run();
    }, () => editor.isActive("link")], "|",
    ["•", "Bulleted list", () => c().toggleBulletList().run(), () => editor.isActive("bulletList")],
    ["1.", "Numbered list", () => c().toggleOrderedList().run(), () => editor.isActive("orderedList")],
    ["❝", "Quote", () => c().toggleBlockquote().run(), () => editor.isActive("blockquote")],
    ["{ }", "Code block", () => c().toggleCodeBlock().run(), () => editor.isActive("codeBlock")],
    ["―", "Divider", () => c().setHorizontalRule().run()], "|",
    // Markdown keeps no font sizes (headings size text): these size the page on screen, like the reader's − and +
    ["A−", "Smaller text on screen", () => $("#doczoomout").click()], ["A+", "Bigger text on screen", () => $("#doczoomin").click()],
  ];
  const buttons = tools.map((t) => t === "|" ? h("span", { class: "richsep" }) : h("button", {
    type: "button", class: `richbtn${t[4] ? ` rb-${t[4]}` : ""}`, title: t[1], onmousedown: (e) => e.preventDefault(), onclick: t[2] }, t[0]));
  function paint() { tools.forEach((t, i) => { if (t !== "|" && t[3]) buttons[i].classList.toggle("on", t[3]()); }); }
  const done = h("button", { type: "button", class: "primary small", onclick: async () => { await closeEditor(); openDoc(it.id); } }, "Done");
  const raw = h("button", { type: "button", class: "ghost small", title: "Edit the Markdown itself", onclick: async () => {
    await closeEditor();
    const fresh = it.note ? it : await fullItem({ ...it, cut: 1 });
    editDoc(fresh, true);
  } }, "Markdown");
  body.replaceChildren(h("div", { class: "richbar" }, title, status, raw, done), h("div", { class: "richtools" }, ...buttons),
    h("div", { class: "richscroll" }, page));
  page.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); } });
  docEditing = { flush: async () => { await saveTitle(); await save(); }, destroy: () => editor.destroy() };
  paint();
  editor.commands.focus("end");
}
// + New in the reader: a new Markdown document on the shelf, open in the editor
$("#docnew").onclick = async () => {
  try {
    const text = "# Untitled\n";
    const it = await api("POST", "/api/shelf", { kind: "doc", text, title: "Untitled.md" });
    const r = await fetch(`/api/shelf/${it.id}/file?name=Untitled.md&type=text%2Fmarkdown`, { method: "PUT", body: text });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "couldn't make it");
    await openDoc(it.id);
    if (shelfOpen) loadShelf();
    richEditor({ ...(await r.json()) });
  } catch (e) { toast(e.message); }
};
// Edit a text document in the reader: Save writes it back (its file too, on its host), and agents read the new text
function editDoc(it, plain = false) {
  // A notes file edits as plain text: the rich editor's round trip re-wraps Markdown lines, so one small edit would
  // rewrite most of the file in its git history
  if (!plain && !it.note && isMarkdown(it) && richSafe(it.text)) return richEditor(it);
  const kind = syntaxOf(it), body = $("#docbody");
  // LaTeX/BibTeX: the coloured text itself is the editor (one element, so nothing can drift off the text); else a textarea
  const area = kind ? h("pre", { class: "docedit sxlive", contenteditable: "plaintext-only", spellcheck: "false" })
    : h("textarea", { class: "docedit", spellcheck: "false" });
  const value = () => kind ? area.textContent : area.value;
  if (!kind) area.value = it.text;
  const save = h("button", { type: "button", class: "primary small", onclick: async () => {
    try {
      await saveDocText(it, value());
      toast(it.note ? "Saved; pushing to the other hosts" : "Saved"); if (shelfOpen) loadShelf(); openDoc(it.id);
    } catch (e) { toast(e.message); }
  } }, "Save");
  const cancel = h("button", { type: "button", class: "ghost small", onclick: () => openDoc(it.id) }, "Cancel");
  docRender++; // a redraw of the document now would throw the edit away
  $("#docedit").hidden = true;
  if (kind) { // recoloured a moment after typing stops, the caret put back at the same character
    const caret = () => {
      const sel = getSelection();
      if (!sel.rangeCount || !area.contains(sel.anchorNode)) return -1;
      const r = sel.getRangeAt(0).cloneRange();
      r.selectNodeContents(area);
      r.setEnd(sel.anchorNode, sel.anchorOffset);
      return r.toString().length;
    };
    const place = (n) => {
      const walk = document.createTreeWalker(area, NodeFilter.SHOW_TEXT);
      for (let t = walk.nextNode(); t; t = walk.nextNode()) {
        if (n <= t.length) { getSelection().collapse(t, n); return; }
        n -= t.length;
      }
    };
    let timer = 0, composing = false;
    const paint = () => {
      if (composing) return;
      const at = caret(), top = area.scrollTop;
      area.replaceChildren(highlight(value(), kind));
      if (at >= 0) place(at);
      area.scrollTop = top;
    };
    area.replaceChildren(highlight(it.text, kind));
    area.addEventListener("compositionstart", () => { composing = true; });
    area.addEventListener("compositionend", () => { composing = false; });
    area.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(paint, 250); });
    // Enter, Tab and paste put plain characters in the text node at the caret (a contenteditable would add <div>/<br>,
    // move focus, or paste formatting; Chrome's insertText drops a lone "\n" here)
    const insert = (s) => {
      const sel = getSelection();
      if (!sel.rangeCount) return;
      const r = sel.getRangeAt(0);
      r.deleteContents();
      r.insertNode(document.createTextNode(s));
      sel.collapse(r.endContainer, r.endOffset);
      const at = caret();
      area.normalize();
      place(at);
      area.dispatchEvent(new Event("input"));
    };
    area.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === "Tab") && !e.ctrlKey && !e.metaKey && !e.altKey && !e.isComposing) {
        e.preventDefault();
        insert(e.key === "Enter" ? "\n" : "\t");
      }
    });
    area.addEventListener("paste", (e) => {
      e.preventDefault();
      insert((e.clipboardData?.getData("text/plain") || "").replace(/\r\n/g, "\n"));
    });
  }
  body.replaceChildren(h("div", { class: "doceditbar" }, h("span", {}, `Editing ${docTitle(it)}`), cancel, save), area);
  area.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save.click(); } });
  area.focus();
}
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
try { // back where it was: Review open, and its document; or the Web view and its page
  const was = localStorage.getItem("deck-review"), page = localStorage.getItem("deck-web");
  if (was && wide.matches) { setReview(true); if (was !== "1") openDoc(was); }
  else if (page && wide.matches) { setWeb(true); if (page !== "1") openWeb(page, true); show(view); }
} catch { /* private mode */ }

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
  const show = (partial) => {
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
  Dictation.addListener("partial", ({ text }) => { if (listening) show(text); });
  Dictation.addListener("segment", ({ text }) => {
    if (!listening || !text) return;
    done = [done, text].filter(Boolean).join(" ");
    show("");
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
// The message box grows with its lines: up to 5 on a phone; on a PC up to 12, and never under 3 (style.css)
// The message box grows with its text, wrapped lines included, up to 12 lines on a PC and 8 on a phone (user); past
// that it scrolls
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
async function killCurrent() {
  if (!attached) return;
  const name = attached;
  // Then the tab below it opens (the one above, for the last); with no tabs left, the terminal (user)
  const tabs = [...document.querySelectorAll("#chips .chip[data-name]")].map((c) => c.dataset.name);
  const i = tabs.indexOf(name), next = tabs[i + 1] ?? tabs[i - 1];
  detach(); attached = null; saveState({ [sessionKey()]: "" });
  await api("DELETE", `/api/sessions/${encodeURIComponent(name)}`);
  await poll();
  if (next && sessions.some((s) => s.name === next && s.running)) openSession(next);
  else attachNewest();
  loadTabTrash();
}

// Closed agent chats wait in the left column's trash for a month (deck/session_trash.py): Restore opens one again,
// as a new tab on its conversation; Delete forever drops it. One trash for the whole Deck (user): every host's, newest
// first, each marked with its host's name and colour; Restore and Delete go to the host it was on.
async function loadTabTrash() {
  const at = (host) => host === HOME_HOST ? "" : `/peer/${encodeURIComponent(host)}`;
  const hosts = [HOME_HOST, ...hostList.filter((x) => !x.current && x.name !== HOME_HOST).map((x) => x.name)];
  const lists = await Promise.all(hosts.map((host) => api("GET", `${at(host)}/api/session-trash`)
    .then((l) => l.map((it) => ({ ...it, host })), () => null))); // offline: its trash waits
  if (lists[0] === null) return;
  const items = lists.flat().filter(Boolean).sort((a, b) => b.deleted - a.deleted);
  const box = $("#tabtrash");
  box.hidden = !items.length;
  box.querySelector("summary").replaceChildren(trashIcon(), h("span", { class: "ttlabel" }, ` Trash (${items.length})`));
  const act = (label, run) => h("button", { type: "button", onclick: async (e) => {
    e.target.disabled = true;
    try { await run(); } catch (err) { toast(err.message); }
    loadTabTrash();
  } }, label);
  const ago = (t) => { const d = (Date.now() / 1000 - t) / 86400; return d < 1 ? "today" : `${Math.floor(d)}d ago`; };
  // the same rows as the shelf's trash: one line each, opened for where it ran and its buttons (user)
  $("#tabtrashlist").replaceChildren(...items.map((it) => h("details", { class: "shelfitem" },
    h("summary", {}, h("span", { class: "cicon" }, icon(it.type)), " ", it.title || it.label),
    h("div", { class: "shelfmeta" }, h("span", { class: "trashhost", style: `--hostled: ${hostList.find((x) => x.name === it.host)?.led || "var(--screen-dim)"}` },
      hostLabel(it.host)), ` · ${shortPath(it.folder)} · ${ago(it.deleted)}`),
    h("div", { class: "shelfacts" },
      act("Restore", async () => {
        const { session } = await api("POST", `${at(it.host)}/api/session-trash/${it.id}/restore`);
        await poll(); openSession(it.host === HOME_HOST ? session : `${it.host}/${session}`);
      }),
      act("Delete forever", () => api("DELETE", `${at(it.host)}/api/session-trash/${it.id}`))))));
}
loadTabTrash();
