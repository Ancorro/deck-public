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
// The clipboard API needs a secure page; the app's iroh pages and plain-http hosts aren't: a hidden box's copy instead
async function copyText(text) {
  if (window.isSecureContext && navigator.clipboard) return navigator.clipboard.writeText(text);
  const t = h("textarea", { style: "position:fixed;opacity:0" }); t.value = text;
  document.body.append(t); t.select();
  const ok = document.execCommand("copy"); t.remove();
  if (!ok) throw new Error("copy refused");
}
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

// A shelf item's session, as saved with it (deck/core.py session_meta): at the bottom of the opened item, one small
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

// Just enough Markdown for agent replies: code blocks, inline code, bold, italic, strikethrough, headings, links, quotes,
// tables. Escaped first, so the agent's text can't add HTML of its own; links only to http(s) and mailto. One exception:
// <details><summary>…</summary> … </details> on lines of their own (outside code) is a fold, its body Markdown too.
function chatMarkdown(text) {
  const lines = text.split("\n"), out = [];
  const flush = (a, b) => { if (b > a) out.push(mdBlocks(lines.slice(a, b).join("\n").replace(/^\n|\n$/g, ""))); };
  let from = 0, fence = false;
  for (let i = 0; i < lines.length; i++) {
    if (/^```/.test(lines[i])) { fence = !fence; continue; }
    const head = !fence && detailsHead(lines, i);
    if (!head) continue;
    let depth = 1, inner = false, j = head.next; // its </details>: folds inside it nest, code blocks hide tags
    for (; j < lines.length; j++) {
      if (/^```/.test(lines[j])) inner = !inner;
      else if (!inner && detailsHead(lines, j)) depth++;
      else if (!inner && /^\s*<\/details>\s*$/i.test(lines[j]) && !--depth) break;
    }
    if (j === lines.length) continue; // never closed: stays text
    flush(from, i);
    out.push(`<details class="cfold"><summary>${mdInline(head.summary)}</summary><div>${
      chatMarkdown(lines.slice(head.next, j).join("\n").replace(/^\n+|\n+$/g, ""))}</div></details>`);
    from = j + 1;
    i = j;
  }
  flush(from, lines.length);
  return out.join("");
}
// A fold's opening: "<details><summary>…</summary>" on one line, or "<details>" with the summary on the next (or none)
function detailsHead(lines, i) {
  const m = lines[i].match(/^\s*<details>\s*(?:<summary>(.*?)<\/summary>\s*)?$/i);
  if (!m) return null;
  if (m[1] !== undefined) return { summary: m[1], next: i + 1 };
  const s = lines[i + 1]?.match(/^\s*<summary>(.*?)<\/summary>\s*$/i);
  return s ? { summary: s[1], next: i + 2 } : { summary: "Details", next: i + 1 };
}
const mdEsc = (t) => t.replace(/\u0001/g, "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const mdBlocks = (text) => text.split(/^```[^\n]*\n?/m)
  .map((part, i) => i % 2 ? `<pre>${mdEsc(part.replace(/\n$/, ""))}</pre>` : withMath(part, (t) => mdTables(t, mdInline))).join("");
// Code spans and [label](url) links are set aside first (\u0001n\u0001), so nothing else rewrites inside them.
function mdInline(t) {
  const kept = [], keep = (html) => `\u0001${kept.push(html) - 1}\u0001`;
  const back = (s) => s.replace(/\u0001(\d+)\u0001/g, (_, n) => back(kept[n]));
  return back(mdEsc(t)
    .replace(/`([^`\n]+)`/g, (_, c) => keep(`<code>${c}</code>`))
    .replace(/\[([^\]\n]+)\]\(((?:https?:\/\/|mailto:)[^\s()<]+(?:\([^\s()<]*\)[^\s()<]*)*)\)/g,
      (_, label, url) => keep(`<a href="${url}" target="_blank" rel="noopener">${label}</a>`))
    .replace(/^(\s*)[-*] /gm, "$1• ")
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*\w])\*(\S(?:[^*\n]*\S)?)\*(?![*\w])/g, "$1<i>$2</i>")
    .replace(/~~(?=\S)([^~\n]*?\S)~~/g, "<s>$1</s>")
    .replace(/^#{1,6} (.*)$/gm, "<b>$1</b>")
    // bare links: the text is escaped by now, so a URL's & is &amp; (a query string must not end the link there)
    .replace(/(https?:\/\/(?:[^\s<&\u0001]|&amp;)*(?:[^\s<&.,;:!?)\]\u0001]|&amp;))/g, '<a href="$1" target="_blank" rel="noopener">$1</a>')
    // quotes: "> " lines, a run of them one block (its newline after dropped: the block ends the line)
    .replace(/(?:^&gt; ?.*(?:\n|$))+/gm, (q) => `<blockquote>${q.replace(/\n$/, "").replace(/^&gt; ?/gm, "").replace(/^(\s*)[-*] /gm, "$1• ")}</blockquote>`));
}
// Tables: a row with pipes, then a |---|:--:| row, then body rows. Cells go through the same inline markdown; wide
// tables scroll sideways. The newline either side is dropped (the table is a block; .cprose keeps newlines).
function mdTables(text, inline) {
  const SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
  const row = (l) => l.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
  const lines = text.split("\n"), out = [];
  let para = [];
  const flush = () => { if (para.length) out.push(inline(para.join("\n"))); para = []; };
  for (let i = 0; i < lines.length; i++) {
    const sep = lines[i + 1];
    if (!lines[i].includes("|") || sep === undefined || !sep.includes("-") || !(sep.includes("|") || lines[i].trim().startsWith("|")) || !SEP.test(sep)) {
      para.push(lines[i]);
      continue;
    }
    const align = row(sep).map((c) => (c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : ""));
    const cell = (tag, c, j) => `<${tag}${align[j] ? ` style="text-align:${align[j]}"` : ""}>${inline(c)}</${tag}>`;
    const head = row(lines[i]);
    let body = "";
    for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) {
      const r = row(lines[i]);
      body += `<tr>${head.map((_, j) => cell("td", r[j] ?? "", j)).join("")}</tr>`;
    }
    i--;
    if (para.length && para[para.length - 1] === "") para.pop(); // the blank line before
    flush();
    out.push(`<div class="ctable"><table><thead><tr>${head.map((c, j) => cell("th", c, j)).join("")}</tr></thead><tbody>${body}</tbody></table></div>`);
    if (lines[i + 1] === "") i++; // and after
  }
  flush();
  return out.join("");
}
// LaTeX (KaTeX): $$…$$ and \[…\] as display, \(…\) and $…$ inline. Pulled out before the markdown and put back
// rendered; inline code is left alone, and "$5 or $10" isn't math (no space inside the $s, no digit after the closing one).
function withMath(text, inline) {
  const MATH = /(`[^`\n]+`)|\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?=\S)((?:\\\$|[^$\n`])+?)(?<=\S)\$(?!\d)/g;
  if (!window.katex) return inline(text);
  const out = [];
  const marked = text.replace(MATH, (all, code, dd, sq, par, d) => {
    if (code) return all;
    try { out.push(katex.renderToString(dd ?? sq ?? par ?? d, { displayMode: dd !== undefined || sq !== undefined, throwOnError: false })); }
    catch { return all; }
    return `\u0000${out.length - 1}\u0000`;
  });
  return inline(marked).replace(/\u0000(\d+)\u0000/g, (_, n) => out[n]);
}

function send(d) { if (ws?.readyState !== 1) return false; ws.send(JSON.stringify({ t: "in", d })); return true; }
// A line to submit (message box, slash commands): the host types it into the session, then Enter on its own (Claude
// takes a burst as a paste, and Enter inside a paste is a newline). Inner \n (Ctrl-J) is a newline in agent input.
// via: how it was sent, for the message's stamp (the server adds the device's name). Agents go by it: on a phone they
// keep replies short (core.DECK_CONTEXT). A touch screen narrower than the PC layout counts as a phone.
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
// Harness → Sampling: OpenCode's sampling settings on the open session's host (its Settings, deck/core.py SAMPLING);
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

// A PC has no key row (style.css): the message box gets a tool row, as in Claude's web app (Harness and Resume on the
// left; Model, Effort, the mic and Send on the right). A phone, or a PC with the message box off (Settings), keeps
// Harness first in the keys and the mic and Send beside the box.
function placeHarness() {
  const send = $("#say button[type=submit]");
  if (wide.matches && !$("#say").hidden) {
    $("#say .msgfield").append($("#msgmenu"), $(".msgbar"));
    $("#say .mbl").prepend($("#harnessbtn"));
    $("#say .mbr").append($("#upbtn"), $("#mic"), send);
  } else {
    // a phone or narrow window: the tool row (Resume, Brain, Model, Effort) sits in the key row after Harness, + at
    // its right end as on a PC (user)
    $(".keybar .keys").prepend($("#harnessbtn"), $(".msgbar"));
    $(".keybar .keys").append($("#upbtn"));
    $(".keybar").append($("#msgmenu"));
    $("#say .msgside").append($("#mic"));
    $("#say").append(send);
  }
  // A chat's Working… and Stop: on a phone, at the top bar's right end, beside the view tabs (user: the key row had no room);
  // on a PC, together just above the message box (user)
  // A chat's status line: on a PC at the foot of the chat, under the message box (user), else above the screen as for
  // terminals
  if (wide.matches && $("#term").classList.contains("chat")) $("#term").append($("#agentbar"));
  else $("#view-term .termwrap").before($("#agentbar"));
  if (chatView && $("#term").classList.contains("chat")) {
    if (wide.matches) chatView.box.after(chatView.status);
    else $(".top .brand").append(chatView.status);
  }
  if (!wide.matches || $("#say").hidden) $(".keys .kright").append($("#upbtn"));
  for (const b of document.querySelectorAll("#say .chatonly, .msgbar .chatonly, #upbtn"))
    b.hidden = !$("#term").classList.contains("chat") || (b.classList.contains("agyonly") && sessions.find((s) => s.name === attached)?.type !== "agy");
  closeMenu(); // another session: its menu goes
}
placeHarness();
wide.addEventListener("change", placeHarness);
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
const AGENT_TYPES = ["claude", "codex", "opencode", "pi", "agy", "openclaw"];
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
// Any tap outside the pop-up or its button closes it.
document.addEventListener("pointerdown", (ev) => {
  if (!$("#harness").hidden && !ev.target.closest("#harness, #harnessbtn")) showHarness(false);
  if (menuReq && !ev.target.closest("#msgmenu, .msgbar button[data-cmd], #brainbtn")) closeMenu();
});
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && menuReq) closeMenu(); });
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
