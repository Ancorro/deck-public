// Just enough Markdown for agent replies: code blocks, inline code, bold, italic, strikethrough, headings, links, quotes,
// tables. Escaped first, so the agent's text can't add HTML of its own; links only to http(s) and mailto. One exception:
// <details><summary>…</summary> … </details> on lines of their own (outside code) is a fold, its body Markdown too.
function chatMarkdown(text) {
  const lines = text.split("\n"), out = [];
  const flush = (a, b) => { if (b > a) out.push(mdBlocks(lines.slice(a, b).join("\n").replace(/^\n+|\n+$/g, ""))); };
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
function mdCode(langText, code) {
  const lang = langText.trim().split(/\s+/)[0].replace(/[^a-zA-Z0-9_+#.-]/g, "");
  return `<div class="ccode"><div class="ccodebar"><span class="ccodelang">${mdEsc(lang || "text")}</span><button type="button" class="ccopy">Copy</button></div><pre><code>${mdEsc(code.replace(/\n$/, ""))}</code></pre></div>`;
}
function mdBlocks(text) {
  const re = /^```([^\n]*)\n?([\s\S]*?)^```[ \t]*(?:\n|$)/gm, out = [];
  const prose = (s) => { s = s.replace(/^\n+|\n+$/g, ""); return s ? withMath(s, (t) => mdTables(t, mdInline)) : ""; };
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(prose(text.slice(last, m.index)));
    out.push(mdCode(m[1], m[2]));
    last = re.lastIndex;
  }
  const tail = text.slice(last);
  if (tail) {
    const open = tail.match(/^([\s\S]*?)^```([^\n]*)\n?([\s\S]*)$/m);
    if (open) { if (open[1]) out.push(prose(open[1])); out.push(mdCode(open[2], open[3])); }
    else out.push(prose(tail));
  }
  return out.join("");
}
function mdListItem(sp, bullet, txt, num = false) {
  const indent = Math.min(Math.floor(sp.replace(/\t/g, "  ").length / 2), 4);
  return `<span class="cli"${indent ? ` data-indent="${indent}"` : ""}><span class="cbul${num ? " cnum" : ""}">${bullet}</span><span class="clitxt">${txt}</span></span>`;
}
// Code spans and [label](url) links are set aside first (\u0001n\u0001), so nothing else rewrites inside them.
function mdInline(t) {
  const kept = [], keep = (html) => `\u0001${kept.push(html) - 1}\u0001`;
  const back = (s) => s.replace(/\u0001(\d+)\u0001/g, (_, n) => back(kept[n]));
  const lines = (s) => s.replace(
    /^(?:#{1,6} +(.*?)(?:\n{1,2}|$)|(?:---+|\*\*\*+|___+)[ \t]*(?:\n{1,2}|$)|([ \t]*)[-*] +(.*?)(?:\n|$)|([ \t]*)(\d{1,3}\.) +(.*?)(?:\n|$))/gm,
    (all, hd, sp1, li1, sp2, num, li2) => {
      if (hd !== undefined) return `<span class="chd chd-${Math.min(all.indexOf(" "), 4)}">${hd}</span>`;
      if (li1 !== undefined) return mdListItem(sp1, "•", li1);
      if (li2 !== undefined) return mdListItem(sp2, num, li2, true);
      return '<hr class="chr">';
    }
  );
  return back(lines(mdEsc(t)
    .replace(/`([^`\n]+)`/g, (_, c) => keep(`<code>${c}</code>`))
    .replace(/\[([^\]\n]+)\]\(((?:https?:\/\/|mailto:)[^\s()<]+(?:\([^\s()<]*\)[^\s()<]*)*)\)/g,
      (_, label, url) => keep(`<a href="${url}" target="_blank" rel="noopener">${label}</a>`))
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*\w])\*(\S(?:[^*\n]*\S)?)\*(?![*\w])/g, "$1<i>$2</i>")
    .replace(/~~(?=\S)([^~\n]*?\S)~~/g, "<s>$1</s>")
    // bare links: the text is escaped by now, so a URL's & is &amp; (a query string must not end the link there)
    .replace(/(https?:\/\/(?:[^\s<&\u0001]|&amp;)*(?:[^\s<&.,;:!?)\]\u0001]|&amp;))/g, '<a href="$1" target="_blank" rel="noopener">$1</a>')
    // quotes: "> " lines, a run of them one block (its newline after dropped: the block ends the line)
    .replace(/(?:^&gt; ?.*(?:\n|$))+/gm, (q) => `<blockquote>${lines(q.replace(/\n$/, "").replace(/^&gt; ?/gm, ""))}</blockquote>`)));
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
if (typeof document !== "undefined") {
  document.addEventListener("click", (e) => {
    const btn = e.target.closest?.(".ccopy");
    if (!btn) return;
    const code = btn.closest(".ccode")?.querySelector("code")?.textContent ?? "";
    copyText(code).then(() => {
      btn.textContent = "Copied";
      btn.classList.add("copied");
      clearTimeout(btn._copyTimer);
      btn._copyTimer = setTimeout(() => { btn.textContent = "Copy"; btn.classList.remove("copied"); }, 1200);
    }).catch(() => {});
  });
}
