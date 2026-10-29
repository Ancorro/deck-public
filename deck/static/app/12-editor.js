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
