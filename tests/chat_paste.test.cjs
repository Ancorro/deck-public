const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Pasting images and files into a chat uploads them like the + button (user 2026-11-15: "let me past images and files
// into deck chat")
const src = fs.readFileSync(path.join(__dirname, '../deck/static/app/09-composer.js'), 'utf8');
const start = src.indexOf('function pastedFiles('), end = src.indexOf('document.addEventListener("paste"', start);
assert.ok(start >= 0 && end > start, 'pastedFiles and the paste handler remain');

class FakeFile { constructor(parts, name, o = {}) { this.parts = parts; this.name = name; this.type = o.type || ''; } }
const ctx = { File: FakeFile, Date };
vm.createContext(ctx);
vm.runInContext(`${src.slice(start, end)}\nthis.pastedFiles = pastedFiles;`, ctx);

test('a pasted screenshot gets a timestamped name; a copied file keeps its own', () => {
  const out = ctx.pastedFiles({ files: [new FakeFile([], 'image.png', { type: 'image/png' }), new FakeFile([], 'notes.pdf', { type: 'application/pdf' })] });
  assert.match(out[0].name, /^pasted-\d{4}-\d\d-\d\d-\d\d-\d\d-\d\d-1\.png$/);
  assert.equal(out[1].name, 'notes.pdf');
  assert.match(ctx.pastedFiles({ files: [new FakeFile([], 'image.jpeg', { type: 'image/jpeg' })] })[0].name, /^pasted-[\d-]+\.jpg$/);
});

test('plain text pastes are left to the browser; files in a chat upload', () => {
  assert.equal(ctx.pastedFiles({ files: [] }).length, 0);
  const handler = src.slice(end, src.indexOf('});', end));
  assert.match(handler, /!files\.length \|\| !chatView/);
  assert.match(handler, /e\.preventDefault\(\);\s*uploadToChat\(files\)/);
});
