const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// The reader refreshes agents' edits (user 2026-11-08: "add a refresh button ... or auto refresh, even better").
const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
const review = fs.readFileSync(path.join(__dirname, '../deck/static/app/11-review.js'), 'utf8');

test('the reader has a refresh button that reloads the open document fresh, never over an open edit', () => {
  assert.match(html, /<button type="button" id="docreload"[^>]*>↻<\/button>/);
  assert.match(review, /\$\("#docreload"\)\.onclick = \(\) => \{[\s\S]*?docEditing[\s\S]*?openDoc\(reviewDoc, true\);/);
  assert.match(review, /async function openDoc\(id, fresh = false\)/);
});

test('a shelf text document and a notes file are watched, not only linked files', () => {
  assert.match(review, /const docVersion = \(it\) => it\.file\?\.linked/);
  assert.match(review, /it\.note \? api\("GET", `\/api\/\$\{it\.noteSrc\}\/file/);
  assert.match(review, /\/api\/shelf\/\$\{it\.id\}`\)\.then\(\(r\) => `\$\{r\.at\}:\$\{r\.text\}`\)/);
  assert.match(review, /if \(docEditing \|\| \$\("#docbody \.docedit"\) \|\| document\.hidden\) return;/);
});
