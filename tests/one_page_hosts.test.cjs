const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// One page for every host (user): no host list in Settings, one name for the whole Deck, builds through /peer.
const part = (name) => fs.readFileSync(path.join(__dirname, `../deck/static/app/${name}.js`), 'utf8');
const source = part('13-settings') + part('04-pad'); // Settings, and the key builder's ask sheet

test("Settings lists no hosts, and the name is the whole Deck's", () => {
  const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
  assert.doesNotMatch(html, /id="hostlist"/);
  assert.match(html, /<h3 class="setgroup">Deck<\/h3>[\s\S]{0,200}<label>Name <input id="setalias" name="alias"/);
  const site = fs.readFileSync(path.join(__dirname, '../deck/site.py'), 'utf8');
  assert.doesNotMatch(site.match(/^PER_NODE = .*$/m)[0], /"alias"/);
});

test('Build on another host goes through the peer proxy, never to its site', () => {
  assert.match(source, /api\("POST", `\/peer\/\$\{encodeURIComponent\(at\)\}\/api\/build`/);
  assert.doesNotMatch(source, /location\.href = `\$\{target\.dataset\.url\}/);
});

test('Settings → Deck → Hosts: every host a name and colour wheel, shared; the header LED opens nothing of its own', () => {
  const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
  assert.match(html, /<div class="setname"><label>Name <input id="setalias"/);
  assert.match(html, /<div id="sethosts" class="sethosts"><\/div>/);
  assert.doesNotMatch(html, /id="ledbtn"|id="ledpick"|id="setled"/);
  assert.match(source, /saveSetting\(\{ hostnames: \{ \[name\]: \{ alias, led: colour \} \} \}\)/);
  const chips = part('05-tabs');
  assert.match(chips, /h\("span", \{ class: "chname" \}, x\?\.aka \|\| host\)/); // the tab column shows the alias
});

test('Settings groups: Deck, New sessions, Chat, Appearance, Projects, More, Advanced', () => {
  const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
  const from = html.indexOf('<form id="setform">'), form = html.slice(from, html.indexOf('</form>', from));
  assert.deepEqual([...form.matchAll(/<(?:h3|summary) class="setgroup">([^<]+)</g)].map((m) => m[1]),
    ['Deck', 'New sessions', 'Chat', 'Appearance', 'Projects', 'More', 'Advanced']);
});

test('an opened shelf item has its Copy row at the top, above the text', () => {
  const shelf = part('10-shelf');
  assert.match(shelf, /h\("div", \{ class: "shelfacts" \}, \.\.\.acts\), body, metaBox\(it\)\)/);
});

test("in the phone app, Back on a host's page closes a dialog, else leaves the app; the host list is in Settings", () => {
  // Native, from the URL: WebView history skips entries a page adds without a tap (emulator test 2026-10-22)
  const main = fs.readFileSync(path.join(__dirname, '../app/native/MainActivity.java'), 'utf8');
  assert.match(main, /if \(url == null \|\| url\.startsWith\("https:\/\/localhost"\)\)/);
  assert.match(main, /if \(!"1"\.equals\(closed\)\) moveTaskToBack\(true\);/);
  const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
  assert.match(html, /<button type="button" id="hostsbtn" hidden>Hosts /);
  assert.match(source, /\$\("#hostsbtn"\)\.onclick = \(\) => \{ location\.href = "https:\/\/localhost\/#hosts"; \};/);
  const app = fs.readFileSync(path.join(__dirname, '../app/www/index.html'), 'utf8');
  assert.match(app, /if \(location\.hash === "#hosts"\)/);
  assert.doesNotMatch(html, /id="sitenote"/); // no banner on a follower's page (user: in the way)
});

test('a page shows the saved theme and Deck name before the server answers (no light flash or "…" over iroh)', () => {
  const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
  const head = html.slice(0, html.indexOf('</head>'));
  assert.match(head, /document\.documentElement\.dataset\.theme = /);
  assert.match(source, /localStorage\.getItem\("deck-name"\)/);
});

test("on a PC, a click on the tab column's empty space does not shut it", () => {
  const pad = part('04-pad');
  assert.match(pad, /\$\("#padcol"\)\.addEventListener\("click", \(e\) => \{\n  if \(wide\.matches\) return;/);
});

test('App in the phone app asks for a browser link instead of downloading in the WebView', () => {
  const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
  assert.match(html, /<button type="button" id="appbtn" hidden>App /);
  assert.match(html, /<dialog id="applink">/);
  assert.match(source, /\$\("#appbtn"\)\.hidden = !window\.Capacitor;/);
  assert.match(source, /api\("POST", "\/api\/app\/link"\)/);
  const java = fs.readFileSync(path.join(__dirname, '../app/native/MainActivity.java'), 'utf8');
  assert.match(java, /setDownloadListener/);
  assert.match(java, /CATEGORY_APP_BROWSER/);
});

test("Download on a document gives its file from the host that has it, linked or uploaded, not its text", () => {
  const shelf = part('10-shelf');
  const fn = shelf.slice(shelf.indexOf('async function downloadItem'), shelf.indexOf('\n}\n', shelf.indexOf('async function downloadItem')));
  assert.match(fn, /fetch\(docFileUrl\(it\)\)/); // /peer/<host> when the file is on another host
  assert.doesNotMatch(fn, /!it\.file\.linked/); // a linked PDF downloads as the PDF too
  assert.match(fn, /URL\.createObjectURL\(file\)/);
});
