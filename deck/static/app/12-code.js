// ---- Code (PC): the chat on the left, a code editor on the right, and a file tree of the session's folder where the
// shelf was (user 2026-11-14: "a 'code' tab, looks like review but with text editor, and shelf is replaced with a file
// tree"). The tree lists the folder the open session works in (its agent's PUT .../project folder, else where it
// started; deck/code.py), on that session's host. Files open as tabs in a CodeMirror 6 editor (loaded from jsDelivr
// the first time, every file pinned by the import map in index.html); Ctrl+S saves. A save names the version it
// started from, and the host refuses it when an agent changed the file meanwhile (409: Reload or Overwrite). An open
// file an agent changes reloads by itself, unless it has unsaved edits.
// The tree's folder: {host, root} (root as the host's OS names it, with forward slashes); "pinned" when typed in, so
// switching sessions doesn't move it
let codeRoot = null, codePinned = false;
const codeOpen = new Map(); // "host|root" -> Set of folder paths left open in the tree
let codeTabs = []; // open files: {key, host, root, path, name, state, version, eol, bom, saved, dirty, mtime, size, note}
let codeShown = ""; // the shown tab's key
let cmLib = null, cmView = null, cmLangs = new Map();
try {
  const s = JSON.parse(localStorage.getItem("deck-code") || "null");
  if (s?.tabs) codeTabs = s.tabs.map((t) => ({ ...t, state: null, dirty: false })); // texts come from the host again
  if (s?.shown) codeShown = s.shown;
  if (s?.root && s.pinned) { codeRoot = s.root; codePinned = true; }
} catch { /* none */ }
const codeKey = (host, root, path) => `${host}|${root}|${path}`;
const codeBase = (host) => host && host !== HOME_HOST ? `/peer/${encodeURIComponent(host)}` : "";
const codeQ = (root, path) => `root=${encodeURIComponent(root)}&path=${encodeURIComponent(path)}`;
function saveCode() {
  try {
    localStorage.setItem("deck-code", JSON.stringify({ on: codeOn, shown: codeShown, pinned: codePinned, root: codePinned ? codeRoot : null,
      tabs: codeTabs.map(({ key, host, root, path, name }) => ({ key, host, root, path, name })) }));
  } catch { /* this visit only */ }
}
// Unsaved edits: Deck's reload after an update (01-core.js reloadWhenIdle) waits for them, and leaving the page asks
const codeDirty = () => codeTabs.some((t) => t.dirty);
window.addEventListener("beforeunload", (e) => { if (codeDirty()) { e.preventDefault(); e.returnValue = ""; } });

function setCode(on) {
  codeOn = on;
  if (on && webOn) setWeb(false);
  if (on && reviewOn) setReview(false);
  $("#app").classList.toggle("review", on || reviewOn || webOn); // the same split as Review
  $("#app").classList.toggle("coding", on);
  $("#codepane").hidden = !on;
  $("#codetree").hidden = !on;
  saveCode();
  if (on) { codeFollow(); renderCodeTabs(); if (codeShown) showCodeTab(codeShown); }
  fit?.fit();
}

// ---- the tree ----
// The open session's folder on its host, unless a folder was typed in; the tree draws when its folder changed (or never
// drew: a typed-in folder restored at boot)
let codeTreeFor = null; // the folder the tree last drew, "host|root" ("" for none)
function codeFollow(force = false) {
  if (!codeOn) return;
  if (!codePinned || force) {
    const s = attached && sessions.find((x) => x.name === attached);
    if (s?.folder && (force || s.host !== codeRoot?.host || s.folder !== codeRoot?.root)) {
      codeRoot = { host: s.host || HOME_HOST, root: s.folder }; codePinned = false;
      saveCode();
    }
  }
  if (force || codeTreeFor !== (codeRoot ? `${codeRoot.host}|${codeRoot.root}` : "")) renderCodeTree();
}
const codeFolders = new Map(); // "host|root|path" -> {entries, more} as last listed
async function codeList(path) {
  const { host, root } = codeRoot;
  const r = await api("GET", `${codeBase(host)}/api/code/list?${codeQ(root, path)}`);
  codeFolders.set(`${host}|${root}|${path}`, r);
  return r;
}
async function renderCodeTree(fresh = false) {
  const box = $("#codetreelist"), head = $("#codetreepath");
  codeTreeFor = codeRoot ? `${codeRoot.host}|${codeRoot.root}` : "";
  if (!codeRoot) {
    head.value = ""; head.title = "";
    box.replaceChildren(h("p", { class: "codehint" }, "Open a session to see its folder, or type a folder's path above."));
    return;
  }
  const { host, root } = codeRoot, rootKey = `${host}|${root}`;
  head.value = host === HOME_HOST ? root : `${host}: ${root}`;
  head.title = head.value;
  if (!codeOpen.has(rootKey)) codeOpen.set(rootKey, new Set([""]));
  const open = codeOpen.get(rootKey);
  if (fresh) for (const k of [...codeFolders.keys()]) if (k.startsWith(`${rootKey}|`)) codeFolders.delete(k);
  const shownPath = codeTabs.find((t) => t.key === codeShown)?.path;
  // one folder's rows (its open subfolders' rows nested under them); listed once, then kept until ↻
  const rows = async (path, depth) => {
    let r = codeFolders.get(`${rootKey}|${path}`);
    if (!r) try { r = await codeList(path); } catch (e) { return [h("div", { class: "codenone", style: `--depth:${depth}` }, e.message)]; }
    const out = [];
    for (const e of r.entries) {
      const p = path ? `${path}/${e.name}` : e.name;
      if (e.dir) {
        const isOpen = open.has(p);
        out.push(h("button", { type: "button", class: `coderow codedir${e.heavy ? " heavy" : ""}${isOpen ? " open" : ""}`, style: `--depth:${depth}`,
          title: p, onclick: () => { if (open.has(p)) open.delete(p); else open.add(p); renderCodeTree(); } },
          h("span", { class: "codechev", "aria-hidden": "true" }, isOpen ? "▾" : "▸"), h("span", { class: "codename" }, e.name)));
        if (isOpen) out.push(...await rows(p, depth + 1));
      } else {
        out.push(h("button", { type: "button", class: `coderow codefile${p === shownPath ? " on" : ""}`, style: `--depth:${depth}`,
          title: `${p}${e.size != null ? ` (${fmtSize(e.size)})` : ""}`, "data-path": p, onclick: () => openCodeFile(host, root, p) },
          h("span", { class: "codechev", "aria-hidden": "true" }), h("span", { class: "codename" }, e.name)));
      }
    }
    if (r.more) out.push(h("div", { class: "codenone", style: `--depth:${depth}` }, `${r.more} more not shown`));
    if (!r.entries.length && !path) out.push(h("div", { class: "codenone" }, "Empty folder"));
    return out;
  };
  const mine = (renderCodeTree.n = (renderCodeTree.n || 0) + 1);
  const list = await rows("", 0);
  if (mine !== renderCodeTree.n) return; // a newer draw won
  const keep = box.scrollTop;
  box.replaceChildren(...list);
  box.scrollTop = keep;
}
const fmtSize = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
// The path box: type a folder (on the open session's host) and Enter shows it; empty follows the session again
$("#codetreeform").onsubmit = (e) => {
  e.preventDefault();
  const v = $("#codetreepath").value.trim();
  if (!v) { codePinned = false; codeRoot = null; codeFollow(true); saveCode(); return; }
  const m = v.match(/^([\w.-]+):\s+(.+)$/); // "host: path", as the box shows another host's folder
  const host = m && hostList.some((x) => x.name === m[1]) ? m[1] : codeRoot?.host || hostOf(attached || "") || HOME_HOST;
  codeRoot = { host, root: (m && hostList.some((x) => x.name === m[1]) ? m[2] : v).replace(/\\/g, "/") };
  codePinned = true;
  saveCode();
  renderCodeTree(true);
};
$("#codetreereload").onclick = () => renderCodeTree(true);
$("#codetreesession").onclick = () => { codePinned = false; codeFollow(true); };
$("#codetreeup").onclick = () => {
  if (!codeRoot) return;
  const r = codeRoot.root.replace(/\/+$/, ""), up = r.slice(0, r.lastIndexOf("/"));
  if (!up || /^[A-Za-z]:$/.test(r)) return;
  codeRoot = { host: codeRoot.host, root: /^[A-Za-z]:$/.test(up) ? `${up}/` : up };
  codePinned = true;
  saveCode();
  renderCodeTree();
};

// ---- the editor ----
// CodeMirror 6 from jsDelivr: every module (and every module it imports) is pinned in index.html's import map, so a
// changed file doesn't load. The bare names resolve there too, so all of CodeMirror shares one @codemirror/state.
function loadCodeMirror() {
  cmLib ||= Promise.all(["@codemirror/state", "@codemirror/view", "@codemirror/commands", "@codemirror/language",
    "@codemirror/search", "@codemirror/autocomplete", "@lezer/highlight"].map((m) => import(m)))
    .then(([state, view, commands, language, search, autocomplete, highlight]) => ({ state, view, commands, language, search, autocomplete, highlight }))
    .catch((e) => { cmLib = null; throw e; });
  return cmLib;
}
// A file's language, by its name: the module that has it and how to get its extension (loaded when first needed)
const LEGACY = (mode, name) => async (L) => L.language.StreamLanguage.define((await import(`@codemirror/legacy-modes/mode/${mode}`))[name]);
const CODE_LANGS = [
  [/\.(m?js|cjs|jsx)$/i, async () => (await import("@codemirror/lang-javascript")).javascript({ jsx: true })],
  [/\.(ts|mts|cts|tsx)$/i, async () => (await import("@codemirror/lang-javascript")).javascript({ typescript: true, jsx: true })],
  [/\.(py|pyw|pyi)$/i, async () => (await import("@codemirror/lang-python")).python()],
  [/\.(json|jsonc|ipynb|webmanifest)$/i, async () => (await import("@codemirror/lang-json")).json()],
  [/\.(md|markdown|mdx)$/i, async () => (await import("@codemirror/lang-markdown")).markdown()],
  [/\.(html?|xhtml|vue|svelte)$/i, async () => (await import("@codemirror/lang-html")).html()],
  [/\.(css|scss|less)$/i, async () => (await import("@codemirror/lang-css")).css()],
  [/\.(c|h|cc|cpp|cxx|hpp|hh|hxx|ino|cu|cuh)$/i, async () => (await import("@codemirror/lang-cpp")).cpp()],
  [/\.rs$/i, async () => (await import("@codemirror/lang-rust")).rust()],
  [/\.go$/i, async () => (await import("@codemirror/lang-go")).go()],
  [/\.java$/i, async () => (await import("@codemirror/lang-java")).java()],
  [/\.(ya?ml)$/i, async () => (await import("@codemirror/lang-yaml")).yaml()],
  [/\.(xml|svg|xsd|xsl|plist|csproj|urdf|sdf|launch)$/i, async () => (await import("@codemirror/lang-xml")).xml()],
  [/\.sql$/i, async () => (await import("@codemirror/lang-sql")).sql()],
  [/(\.(sh|bash|zsh|ksh)|^\.?(bashrc|bash_profile|profile|zshrc))$/i, LEGACY("shell", "shell")],
  [/\.(toml|lock)$|^(uv|Cargo|poetry)\.lock$/i, LEGACY("toml", "toml")],
  [/\.(tex|sty|cls|ltx|bib)$/i, LEGACY("stex", "stex")],
  [/\.(ps1|psm1|psd1)$/i, LEGACY("powershell", "powerShell")],
  [/(^|\.)dockerfile$|^Dockerfile(\..+)?$|^Containerfile$/i, LEGACY("dockerfile", "dockerFile")],
  [/\.lua$/i, LEGACY("lua", "lua")],
  [/\.(ini|cfg|conf|properties|env|editorconfig|gitconfig)$|^\.(env|gitattributes|gitignore)(\..*)?$/i, LEGACY("properties", "properties")],
  [/\.(diff|patch)$/i, LEGACY("diff", "diff")],
  [/\.jl$/i, LEGACY("julia", "julia")],
  [/\.(r|rmd)$/i, LEGACY("r", "r")],
  [/\.(rb|rake|gemspec)$|^(Gemfile|Rakefile)$/i, LEGACY("ruby", "ruby")],
  [/\.swift$/i, LEGACY("swift", "swift")],
  [/\.(kt|kts)$/i, LEGACY("clike", "kotlin")],
  [/\.cs$/i, LEGACY("clike", "csharp")],
  [/\.scala$/i, LEGACY("clike", "scala")],
  [/\.dart$/i, LEGACY("clike", "dart")],
  [/\.(glsl|vert|frag|hlsl|wgsl)$/i, LEGACY("clike", "shader")],
  [/\.m$/i, LEGACY("octave", "octave")], // MATLAB (WEC-Sim, Simulink scripts)
  [/^CMakeLists\.txt$|\.cmake$/i, LEGACY("cmake", "cmake")],
  [/\.proto$/i, LEGACY("protobuf", "protobuf")],
];
async function langFor(name, L) {
  const hit = CODE_LANGS.find(([re]) => re.test(name));
  if (!hit) return [];
  const key = String(hit[0]);
  if (!cmLangs.has(key)) cmLangs.set(key, hit[1](L).catch(() => []));
  return cmLangs.get(key);
}
// The editor's look follows the page: the pane's colours, the mono font, and a syntax palette for light and dark
function cmTheme(L) {
  const dark = shownTheme() !== "light";
  const t = L.highlight.tags;
  const c = dark
    ? { kw: "#c792ea", str: "#c3e88d", num: "#f78c6c", com: "#7d8b6f", fn: "#82aaff", type: "#ffcb6b", prop: "#89ddff", op: "#89ddff", head: "#ff9e64", link: "#4fd1c5", err: "#ff5370", tag: "#f07178", attr: "#ffcb6b", meta: "#8f8c84" }
    : { kw: "#7b3fb0", str: "#3d7a12", num: "#b4500f", com: "#6a7a5a", fn: "#0b5fb8", type: "#8a6500", prop: "#0b7c72", op: "#0b7c72", head: "#b4530a", link: "#0b7c72", err: "#c0392b", tag: "#c0392b", attr: "#8a6500", meta: "#7f7c74" };
  const style = L.language.HighlightStyle.define([
    { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword, t.definitionKeyword, t.modifier, t.self, t.null, t.bool, t.atom], color: c.kw },
    { tag: [t.string, t.special(t.string), t.regexp, t.character, t.inserted], color: c.str },
    { tag: [t.number, t.integer, t.float, t.unit], color: c.num },
    { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], color: c.com, fontStyle: "italic" },
    { tag: [t.function(t.variableName), t.function(t.propertyName), t.macroName, t.labelName], color: c.fn },
    { tag: [t.typeName, t.className, t.namespace, t.standard(t.typeName)], color: c.type },
    { tag: [t.propertyName, t.definition(t.propertyName)], color: c.prop },
    { tag: [t.operator, t.punctuation, t.separator, t.bracket, t.angleBracket, t.squareBracket, t.paren, t.brace], color: c.op },
    { tag: [t.heading, t.heading1, t.heading2, t.heading3], color: c.head, fontWeight: "700" },
    { tag: [t.link, t.url], color: c.link, textDecoration: "underline" },
    { tag: [t.strong], fontWeight: "700" }, { tag: [t.emphasis], fontStyle: "italic" }, { tag: [t.strikethrough], textDecoration: "line-through" },
    { tag: [t.invalid, t.deleted], color: c.err },
    { tag: [t.tagName], color: c.tag }, { tag: [t.attributeName], color: c.attr },
    { tag: [t.meta, t.processingInstruction, t.annotation, t.docString], color: c.meta },
  ]);
  return [L.view.EditorView.theme({
    "&": { height: "100%", color: "var(--pane-text)", backgroundColor: "var(--pane)", fontSize: "calc(var(--codefont, 13px))" },
    ".cm-content": { fontFamily: "var(--mono, ui-monospace, monospace)", caretColor: "var(--accent)", padding: "6px 0" },
    ".cm-scroller": { fontFamily: "var(--mono, ui-monospace, monospace)", lineHeight: "1.5" },
    ".cm-gutters": { backgroundColor: "var(--pane)", color: "var(--pane-text-3)", border: "none", borderRight: "1px solid var(--pane-line)" },
    ".cm-activeLineGutter": { backgroundColor: "var(--wash)", color: "var(--pane-text-2)" },
    ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--pane-text) 5%, transparent)" },
    "&.cm-focused .cm-cursor": { borderLeftColor: "var(--accent)", borderLeftWidth: "2px" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "color-mix(in srgb, var(--accent) 30%, transparent) !important" },
    ".cm-panels": { backgroundColor: "var(--pane-2)", color: "var(--pane-text)" },
    ".cm-panels.cm-panels-bottom": { borderTop: "1px solid var(--pane-line)" },
    ".cm-panel input, .cm-panel button": { font: "inherit", color: "inherit" },
    ".cm-textfield": { backgroundColor: "var(--wash)", border: "1px solid var(--pane-line)", borderRadius: "4px" },
    ".cm-button": { backgroundImage: "none", backgroundColor: "var(--wash-2)", border: "none", borderRadius: "4px" },
    ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--accent) 22%, transparent)" },
    ".cm-searchMatch-selected": { backgroundColor: "color-mix(in srgb, var(--accent) 45%, transparent)" },
    ".cm-matchingBracket": { backgroundColor: "color-mix(in srgb, var(--accent) 25%, transparent)", outline: "none" },
    ".cm-foldPlaceholder": { backgroundColor: "var(--wash-2)", border: "none", color: "var(--pane-text-2)" },
    ".cm-tooltip": { backgroundColor: "var(--pane-2)", border: "1px solid var(--pane-line)", color: "var(--pane-text)" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "color-mix(in srgb, var(--accent) 35%, transparent)", color: "var(--pane-text)" },
  }, { dark }), L.language.syntaxHighlighting(style)];
}
// One editor state per open file (its undo history kept while switching tabs); one view shows the current one
const cmCompart = { theme: null, lang: null, wrap: null };
let codeWrap = false;
try { codeWrap = localStorage.getItem("deck-code-wrap") === "1"; } catch { /* off */ }
async function makeState(t, text) {
  const L = await loadCodeMirror();
  const { EditorState, Compartment } = L.state, { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter,
    drawSelection, highlightSpecialChars, rectangularSelection, crosshairCursor, dropCursor } = L.view;
  cmCompart.theme ||= new Compartment(); cmCompart.lang ||= new Compartment(); cmCompart.wrap ||= new Compartment();
  return EditorState.create({ doc: text, extensions: [
    lineNumbers(), highlightActiveLineGutter(), highlightSpecialChars(), L.commands.history(), L.language.foldGutter(),
    drawSelection(), dropCursor(), EditorState.allowMultipleSelections.of(true), L.language.indentOnInput(),
    L.language.bracketMatching(), L.autocomplete.closeBrackets(), rectangularSelection(), crosshairCursor(),
    highlightActiveLine(), L.search.highlightSelectionMatches(), L.language.indentUnit.of(guessIndent(text)),
    keymap.of([{ key: "Mod-s", preventDefault: true, run: () => { saveCodeTab(t.key); return true; } },
      ...L.autocomplete.closeBracketsKeymap, ...L.commands.defaultKeymap, ...L.search.searchKeymap, ...L.commands.historyKeymap,
      ...L.language.foldKeymap, L.commands.indentWithTab]),
    cmCompart.theme.of(cmTheme(L)), cmCompart.lang.of(await langFor(t.name, L)), cmCompart.wrap.of(codeWrap ? EditorView.lineWrapping : []),
    EditorView.updateListener.of((u) => { if (u.docChanged) markDirty(t); }),
  ] });
}
// The file's own indent: tabs, else the most common step of leading spaces (2 or 4), else 4
function guessIndent(text) {
  const lines = text.split("\n", 400);
  if (lines.filter((l) => l.startsWith("\t")).length > lines.filter((l) => /^ {2,}\S/.test(l)).length) return "\t";
  const steps = { 2: 0, 4: 0 };
  let prev = 0;
  for (const l of lines) {
    const m = l.match(/^( *)\S/);
    if (!m) continue;
    const d = Math.abs(m[1].length - prev);
    if (d === 2 || d === 4) steps[d]++;
    prev = m[1].length;
  }
  return " ".repeat(steps[2] > steps[4] ? 2 : 4);
}
function markDirty(t) {
  const dirty = t.state && cmView && t.key === codeShown ? cmView.state.doc.toString() !== t.saved : t.dirty;
  if (dirty === t.dirty) return;
  t.dirty = dirty;
  renderCodeTabs();
}

async function openCodeFile(host, root, path, line = 0) {
  const key = codeKey(host, root, path);
  if (!codeTabs.some((t) => t.key === key)) {
    codeTabs.push({ key, host, root, path, name: path.split("/").pop(), state: null, dirty: false });
    saveCode();
  }
  await showCodeTab(key, line);
}
async function readCodeFile(t) {
  return api("GET", `${codeBase(t.host)}/api/code/file?${codeQ(t.root, t.path)}`);
}
async function showCodeTab(key, line = 0) {
  const t = codeTabs.find((x) => x.key === key);
  if (!t) return;
  stashShown();
  codeShown = key;
  saveCode();
  renderCodeTabs();
  for (const el of document.querySelectorAll("#codetreelist .codefile")) el.classList.toggle("on", t.host === codeRoot?.host && t.root === codeRoot?.root && el.dataset.path === t.path);
  const body = $("#codebody"), mine = (showCodeTab.n = (showCodeTab.n || 0) + 1);
  if (!t.state && !t.note) {
    body.replaceChildren(h("p", { class: "codehint" }, "Opening…"));
    try {
      const f = await readCodeFile(t);
      if (mine !== showCodeTab.n) return;
      if (f.big || f.binary) {
        t.note = f.big ? `${t.path} is ${fmtSize(f.size)}: too big to edit here (2 MB at most).` : `${t.path} isn't a text file.`;
      } else {
        Object.assign(t, { version: f.version, eol: f.eol, bom: !!f.bom, saved: f.text, mtime: f.mtime, size: f.size });
        t.state = await makeState(t, f.text);
      }
    } catch (e) {
      if (mine !== showCodeTab.n) return;
      body.replaceChildren(h("p", { class: "codehint" }, `Couldn't open ${t.path}: ${e.message}`));
      return;
    }
  }
  if (mine !== showCodeTab.n) return;
  if (t.note) { body.replaceChildren(h("p", { class: "codehint" }, t.note)); codeStatus(t); return; }
  const L = await loadCodeMirror();
  if (!cmView) cmView = new L.view.EditorView({ state: t.state });
  else cmView.setState(t.state);
  cmView.dispatch({ effects: cmCompart.theme.reconfigure(cmTheme(L)) }); // the page's theme may have changed
  body.replaceChildren(cmView.dom);
  if (line > 0) {
    const ln = cmView.state.doc.line(Math.min(line, cmView.state.doc.lines));
    cmView.dispatch({ selection: { anchor: ln.from }, effects: L.view.EditorView.scrollIntoView(ln.from, { y: "center" }) });
  } else if (t.scroll != null) requestAnimationFrame(() => { if (codeShown === key) cmView.scrollDOM.scrollTop = t.scroll; });
  cmView.focus();
  codeStatus(t);
  watchCode(t);
}
// The shown tab's state back into its record (the view holds the live one)
function stashShown() {
  const t = codeTabs.find((x) => x.key === codeShown);
  if (t && cmView && t.state) { t.state = cmView.state; t.scroll = cmView.scrollDOM.scrollTop; }
}
function codeStatus(t, msg = "") {
  const parts = t ? [t.host !== HOME_HOST ? t.host : "", t.path, t.eol === "crlf" ? "CRLF" : t.eol ? "LF" : ""].filter(Boolean) : [];
  $("#codestatus").textContent = msg || parts.join(" · ");
  $("#codestatus").title = t ? `${t.root}/${t.path}` : "";
  $("#codesave").hidden = !t || !!t.note;
  $("#codesave").disabled = !t?.dirty;
}
function renderCodeTabs() {
  const box = $("#codetabs");
  box.replaceChildren(...codeTabs.map((t) => h("div", { class: `doctab codetab${t.key === codeShown ? " on" : ""}${t.dirty ? " dirty" : ""}`, title: `${t.host !== HOME_HOST ? `${t.host}: ` : ""}${t.root}/${t.path}` },
    h("button", { type: "button", class: "doctabname", onclick: () => showCodeTab(t.key), onauxclick: (e) => { if (e.button === 1) closeCodeTab(t.key); } }, t.name),
    h("button", { type: "button", class: "doctabx", "aria-label": t.dirty ? "Unsaved; close" : "Close", onclick: () => closeCodeTab(t.key) }, t.dirty ? "●" : "×"))));
  const t = codeTabs.find((x) => x.key === codeShown);
  codeStatus(t);
  if (!codeTabs.length) { $("#codebody").replaceChildren(h("p", { class: "codehint" }, "Pick a file in the tree on the right.")); codeStatus(null); }
}
function closeCodeTab(key) {
  const t = codeTabs.find((x) => x.key === key);
  if (!t) return;
  if (t.dirty && !confirmClose(t)) return;
  const i = codeTabs.indexOf(t);
  codeTabs.splice(i, 1);
  if (codeShown === key) {
    codeShown = codeTabs[Math.min(i, codeTabs.length - 1)]?.key || "";
    if (codeShown) { renderCodeTabs(); showCodeTab(codeShown); } else renderCodeTabs();
  } else renderCodeTabs();
  saveCode();
}
// A tab with unsaved edits: the first × arms it (the dot turns to "Discard?"), a second within 3 s closes it
const armed = new Set();
function confirmClose(t) {
  if (armed.has(t.key)) { armed.delete(t.key); return true; }
  armed.add(t.key);
  toast(`${t.name} has unsaved edits: × again to discard them`);
  setTimeout(() => armed.delete(t.key), 3000);
  return false;
}

async function saveCodeTab(key, overwrite = false) {
  const t = codeTabs.find((x) => x.key === key);
  if (!t || t.note || !t.state) return;
  if (t.key === codeShown && cmView) t.state = cmView.state;
  const text = t.state.doc.toString();
  if (!overwrite && text === t.saved) { codeStatus(t, "No changes"); setTimeout(() => codeStatus(t), 1200); return; }
  codeStatus(t, "Saving…");
  try {
    const r = await fetch(`${codeBase(t.host)}/api/code/file`, { method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ root: t.root, path: t.path, text, base: overwrite ? null : t.version, eol: t.eol, bom: t.bom }) });
    const d = await r.json().catch(() => ({}));
    if (r.status === 409 && d.conflict) return codeConflict(t, d.now);
    if (!r.ok) throw new Error(d.error || r.statusText);
    Object.assign(t, { version: d.version, saved: text, mtime: d.mtime, size: d.size, dirty: false });
    renderCodeTabs();
    codeStatus(t, "Saved");
    setTimeout(() => { if (codeShown === t.key) codeStatus(t); }, 1200);
  } catch (e) { codeStatus(t, `Not saved: ${e.message}`); toast(`${t.name}: not saved (${e.message})`); }
}
$("#codesave").onclick = () => saveCodeTab(codeShown);
// The file changed on disk since it was opened (an agent wrote it) and this tab has edits: show both choices
function codeConflict(t, now) {
  codeStatus(t, "Changed on disk");
  const bar = h("div", { class: "codeconflict" },
    h("span", {}, `${t.name} changed on disk since you opened it (an agent's edit?).`),
    h("button", { type: "button", class: "ghost small", onclick: async () => { bar.remove(); await reloadCodeTab(t, now); } }, "Reload theirs"),
    h("button", { type: "button", class: "primary small", onclick: () => { bar.remove(); saveCodeTab(t.key, true); } }, "Overwrite with mine"),
    h("button", { type: "button", class: "ghost small", "aria-label": "Dismiss", onclick: () => bar.remove() }, "×"));
  $("#codepane .codeconflict")?.remove();
  $("#codebody").before(bar);
}
// The file as it is on disk now, replacing the tab's text (its undo history keeps the old text one Ctrl+Z away)
async function reloadCodeTab(t, now = null) {
  const f = now?.text != null ? now : await readCodeFile(t);
  if (f.big || f.binary) { t.note = f.big ? `${t.path} is too big to edit here now.` : `${t.path} isn't a text file now.`; t.state = null; if (codeShown === t.key) showCodeTab(t.key); return; }
  Object.assign(t, { version: f.version, eol: f.eol, bom: !!f.bom, saved: f.text, mtime: f.mtime, size: f.size });
  const live = codeShown === t.key && cmView;
  const st = live ? cmView.state : t.state;
  if (!st) return;
  const tr = st.update({ changes: { from: 0, to: st.doc.length, insert: f.text } });
  if (live) { const sel = cmView.state.selection.main.head; cmView.dispatch(tr); cmView.dispatch({ selection: { anchor: Math.min(sel, f.text.length) } }); t.state = cmView.state; }
  else t.state = tr.state;
  t.dirty = false;
  renderCodeTabs();
}
// The shown file's watch: every 2 s while it's on screen, its size and change time; changed and no edits here: it
// reloads (an agent's edit shows as it lands); changed with edits here: the conflict bar
let codeWatch = 0;
function watchCode(t) {
  clearInterval(codeWatch);
  codeWatch = setInterval(async () => {
    if (!codeOn || codeShown !== t.key || document.hidden || !t.state) return;
    const s = await api("GET", `${codeBase(t.host)}/api/code/stat?${codeQ(t.root, t.path)}`).catch(() => null);
    if (!s || codeShown !== t.key || (s.mtime === t.mtime && s.size === t.size)) return;
    const f = await readCodeFile(t).catch(() => null);
    if (!f || f.version === t.version) { if (f) Object.assign(t, { mtime: f.mtime, size: f.size }); return; }
    if (t.dirty) { if (!$("#codepane .codeconflict")) codeConflict(t, f); return; }
    await reloadCodeTab(t, f);
    codeStatus(t, "Reloaded: changed on disk");
    setTimeout(() => { if (codeShown === t.key) codeStatus(t); }, 2000);
  }, 2000);
}
// Wrap long lines (remembered per browser)
$("#codewrap").onclick = async () => {
  codeWrap = !codeWrap;
  try { localStorage.setItem("deck-code-wrap", codeWrap ? "1" : "0"); } catch { /* this visit only */ }
  $("#codewrap").setAttribute("aria-pressed", codeWrap);
  if (!cmView) return;
  const L = await loadCodeMirror();
  cmView.dispatch({ effects: cmCompart.wrap.reconfigure(codeWrap ? L.view.EditorView.lineWrapping : []) });
  for (const t of codeTabs) if (t.state && t.key !== codeShown) t.state = t.state.update({ effects: cmCompart.wrap.reconfigure(codeWrap ? L.view.EditorView.lineWrapping : []) }).state;
};
$("#codewrap").setAttribute("aria-pressed", codeWrap);
// The page's theme changed: the editor's colours follow
sysDark.addEventListener("change", () => { if (cmView && cmLib) cmLib.then((L) => cmView.dispatch({ effects: cmCompart.theme.reconfigure(cmTheme(L)) })); });
function codeRetheme() { if (cmView && cmLib) cmLib.then((L) => cmView.dispatch({ effects: cmCompart.theme.reconfigure(cmTheme(L)) })); }
// Selected code goes with the next message, as a quote naming the file and lines (like the reader's selections)
$("#codebody").addEventListener("pointerup", () => setTimeout(() => {
  const t = codeTabs.find((x) => x.key === codeShown);
  if (!cmView || !t) return;
  const sel = cmView.state.selection.main;
  if (sel.empty) return;
  const q = cmView.state.sliceDoc(sel.from, sel.to);
  if (q.trim().length < 3) return;
  const a = cmView.state.doc.lineAt(sel.from).number, b = cmView.state.doc.lineAt(sel.to).number;
  const where = `${t.path}, line${a === b ? ` ${a}` : `s ${a}-${b}`}`;
  attachedItems = attachedItems.filter((x) => x.kind !== "quote");
  attachedItems.push({ id: "quote", kind: "quote", open: false, text: q, title: `${where}: “${q.trim().slice(0, 60)}${q.length > 60 ? "…" : ""}”`, from: where });
  renderAttached();
}, 0));
