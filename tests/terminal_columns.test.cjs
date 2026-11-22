const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// PC two-column Terminal (user 2026-11-15: "two column terminal drop down when hover terminal tab"; "it should only drop
// down after a second")
const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const js = read('deck/static/app/06-terminal.js'), html = read('deck/static/index.html');

test('resting on the Terminal tab a second drops down One column / Two columns', () => {
  assert.match(js, /pointerenter[^\n]*setTimeout\(open, 1000\)/);
  assert.match(js, /pointerleave[^\n]*clearTimeout\(hover\)/);
  assert.match(js, /item\("One column"[^\n]*item\("Two columns"/);
});

test('the second column has its own session picker and is saved as pcsession2', () => {
  assert.match(html, /id="term2col" hidden/);
  assert.match(html, /id="term2pick"/);
  assert.match(js, /saveState\(\{ pcsession2: /);
  assert.match(read('deck/settings.py'), /"pcsession2" in raw/);
});
