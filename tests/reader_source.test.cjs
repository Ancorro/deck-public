const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Markdown in the reader: formatted or source (user 2026-11-23: "add a render/source button for the review bar, (md)").
const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
const review = fs.readFileSync(path.join(__dirname, '../deck/static/app/11-review.js'), 'utf8');

test('the reader bar has a Source button, hidden until a Markdown document opens', () => {
  assert.match(html, /<button type="button" id="docsrc" class="ghost small" hidden>Source<\/button>/);
  assert.match(review, /\$\("#docsrc"\)\.hidden = !md;/);
  assert.match(review, /if \(!reviewDoc\) \{[\s\S]{0,200}\$\("#docsrc"\)\.hidden = true/);
});

test('Source shows the raw text, Rendered formats it, remembered per browser, never over an open edit', () => {
  assert.match(review, /md && !docSource\s*\? Object\.assign\(h\("div", \{ class: "cprose" \}\)/);
  assert.match(review, /localStorage\.getItem\("deck-doc-source"\) === "1"/);
  assert.match(review, /\$\("#docsrc"\)\.onclick = \(\) => \{\s*if \(docEditing[\s\S]*?docSource = !docSource;/);
  assert.match(review, /docSource \? "Rendered" : "Source"/);
});
