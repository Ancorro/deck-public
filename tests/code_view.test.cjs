const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// The Code view (user 2026-11-14: "a 'code' tab, looks like review but with text editor, and shelf is replaced with a
// file tree"): a view tab, an editor pane, a file tree in the shelf's column, and CodeMirror pinned module by module.
const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const html = read('deck/static/index.html');
const code = read('deck/static/app/12-code.js');
const prefs = read('deck/static/app/02-prefs.js');
const core = read('deck/static/app/01-core.js');
const css = read('deck/static/style.css');
const sri = read('deploy/cdn-sri');

function importMap() {
  const m = html.match(/<script type="importmap">\n([\s\S]*?)<\/script>/);
  assert.ok(m, 'index.html has an import map');
  return JSON.parse(m[1]);
}

test('Code is a view tab between Review and Web, with an editor pane and a tree in the shelf column', () => {
  assert.match(html, /data-view="review"[\s\S]*?<button data-view="code" id="codetab">[\s\S]*?data-view="web"/);
  assert.match(html, /<section id="codepane" hidden>[\s\S]*?<div id="codetabs"><\/div>[\s\S]*?<div id="codebody">/);
  assert.match(html, /<aside id="shelf"[\s\S]*?<\/aside>\s*<!-- Code view: the session's folder as a file tree[\s\S]*?<aside id="codetree" hidden>/);
  // the shelf makes way while Code is on; the tree stands in its column
  assert.match(css, /#app\.coding #shelf \{ display: none; \}/);
  assert.match(css, /#app\.coding #codetree:not\(\[hidden\]\) \{ display: flex; order: 3; \}/);
});

test('every view switch knows Code: the tab, show(), the Screen, Review and Web clear it', () => {
  assert.match(prefs, /\["term", "review", "code", "web", "screen"\]\.includes\(v\)/);
  assert.match(prefs, /setCode\(v === "code"\)/);
  assert.match(prefs, /codeOn \? "code" : reviewOn \? "review"/);
  assert.match(prefs, /if \(on && codeOn\) setCode\(false\);/); // the Screen
  const review = read('deck/static/app/11-review.js');
  assert.equal((review.match(/if \(on && codeOn\) setCode\(false\);/g) || []).length, 2); // setWeb, setReview
  assert.match(code, /if \(on && webOn\) setWeb\(false\);\s*if \(on && reviewOn\) setReview\(false\);/);
});

test('an update never reloads the page over unsaved code', () => {
  assert.match(core, /reloadTimer = setInterval\(\(\) => \{[\s\S]*?!\(typeof codeDirty === "function" && codeDirty\(\)\)\) location\.reload\(\);/);
  assert.match(code, /const codeDirty = \(\) => codeTabs\.some\(\(t\) => t\.dirty\);/);
});

test('CodeMirror loads by bare names that the import map pins, every file with a hash', () => {
  const map = importMap();
  const names = new Set();
  for (const m of code.matchAll(/import\("(@[\w-]+\/[\w-]+)"\)/g)) names.add(m[1]);
  for (const m of code.matchAll(/"(@codemirror\/[\w-]+|@lezer\/[\w-]+)"/g)) names.add(m[1]);
  for (const m of code.matchAll(/LEGACY\("([\w-]+)"/g)) names.add(`@codemirror/legacy-modes/mode/${m[1]}`);
  assert.ok(names.size > 20);
  for (const n of names) {
    assert.ok(map.imports[n], `${n} is in the import map`);
    assert.ok(map.integrity[map.imports[n]], `${n}'s file has a hash`);
  }
  // every mapped file and every hashed file is a pinned jsDelivr URL; every mapped file has a hash
  for (const [n, u] of Object.entries(map.imports)) {
    assert.match(u, /^https:\/\/cdn\.jsdelivr\.net\/npm\/(@[\w-]+\/)?[\w.-]+@\d+\.\d+\.\d+\//, n);
    assert.ok(map.integrity[u], `${u} has a hash`);
  }
  for (const [u, h] of Object.entries(map.integrity)) assert.match(h, /^sha384-[A-Za-z0-9+/]{64}$/, u);
  // one version per package: two copies of @codemirror/state break the editor
  const versions = {};
  for (const u of Object.keys(map.integrity)) {
    const m = u.match(/^https:\/\/cdn\.jsdelivr\.net\/npm\/((?:@[\w-]+\/)?[\w.-]+)@([^/]+)\//);
    (versions[m[1]] ||= new Set()).add(m[2]);
  }
  for (const [p, v] of Object.entries(versions)) assert.equal(v.size, 1, `${p}: ${[...v]}`);
});

test('the language modes the editor names exist in cdn-sri, which re-pins them', () => {
  const modes = sri.match(/LEGACY_MODES = \[([\s\S]*?)\]/)[1].match(/"([\w-]+)"/g).map((s) => s.slice(1, -1));
  for (const m of code.matchAll(/LEGACY\("([\w-]+)"/g)) assert.ok(modes.includes(m[1]), `${m[1]} in LEGACY_MODES`);
});

test('saves name the version they started from and show a conflict instead of overwriting', () => {
  assert.match(code, /body: JSON\.stringify\(\{ root: t\.root, path: t\.path, text, base: overwrite \? null : t\.version/);
  assert.match(code, /if \(r\.status === 409 && d\.conflict\) return codeConflict\(t, d\.now\);/);
  // an open file an agent changed reloads by itself, unless it has unsaved edits (then: the conflict bar)
  assert.match(code, /if \(t\.dirty\) \{ if \(!\$\("#codepane \.codeconflict"\)\) codeConflict\(t, f\); return; \}\s*await reloadCodeTab\(t, f\);/);
});
