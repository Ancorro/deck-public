const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// A setting shows the moment it's changed (user: "changing light/dark is laggy"): the page applies it, then tells
// the host; a host a tunnel away must not be waited on, and a theme change must not re-measure every chat reply.
const root = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

test('saveSetting applies the change before the PUT, and undoes it if the host refuses', () => {
  const js = read('deck/static/app/13-settings.js');
  const fn = js.slice(js.indexOf('async function saveSetting('), js.indexOf('\n}\n', js.indexOf('async function saveSetting(')));
  const apply = fn.indexOf('applyPrefs();'), put = fn.indexOf('await api("PUT", "/api/settings", fields)');
  assert.ok(apply > 0 && put > apply, 'applied first, saved after');
  assert.match(fn.slice(put), /Object\.assign\(prefs, before\);\s*applyPrefs\(\);/);
});

test('a theme change does not re-measure the chat; a font or chat-shape change does', () => {
  const js = read('deck/static/app/02-prefs.js');
  const fn = js.slice(js.indexOf('function applyPrefs('), js.indexOf('\n}\n', js.indexOf('function applyPrefs(')));
  assert.match(fn, /const shape = `\$\{font\(\)\}\|\$\{prefs\.chattools\}/);
  assert.match(fn, /if \(chatView && shape !== chatShape\) refreshBars\(\);/);
  assert.doesNotMatch(fn, /if \(chatView\) refreshBars\(\);/);
});

test('the head script sets the theme for System too, so a light OS does not flash dark', () => {
  const html = read('deck/static/index.html');
  const head = html.slice(0, html.indexOf('</head>'));
  assert.match(head, /matchMedia\("\(prefers-color-scheme: dark\)"\)\.matches \? "dark" : "light"/);
});

test('a reload from the host keeps the settings this page is still saving', () => {
  const js = read('deck/static/app/02-prefs.js');
  const vm = require('node:vm');
  const start = js.indexOf('const savingPrefs'), end = js.indexOf('async function loadPrefs(');
  const ctx = { Date, Map, Infinity, Object };
  vm.runInNewContext(`${js.slice(start, end)}; this.holdPrefs = holdPrefs; this.withSaving = withSaving;`, ctx);
  const done = ctx.holdPrefs({ look: 'zine', theme: 'light' });
  // the sync poll's reload lands before the follower has the save: the page keeps its own values
  assert.deepEqual({ ...ctx.withSaving({ look: 'pad', theme: 'dark', font: 14 }) }, { look: 'zine', theme: 'light', font: 14 });
  done(true); // saved: still kept a while, until the host surely has it
  assert.equal(ctx.withSaving({ look: 'pad' }).look, 'zine');
  const refused = ctx.holdPrefs({ font: 20 });
  refused(false); // the host refused it: the host's value wins again
  assert.equal(ctx.withSaving({ font: 14 }).font, 14);
  assert.match(js.slice(end), /prefs = withSaving\(await api\("GET", "\/api\/settings"\)\)/);
  const settings = read('deck/static/app/13-settings.js');
  assert.match(settings, /const done = holdPrefs\(fields\);/);
});

test("the user's own messages sit on the right in every look", () => {
  for (const f of fs.readdirSync(path.join(root, 'deck/static/looks')).filter((x) => x.endsWith('.css'))) {
    const css = read(`deck/static/looks/${f}`);
    for (const m of css.matchAll(/^\.cmsg\.user \{[^}]*\}/gm)) assert.doesNotMatch(m[0], /align-self: stretch/, f);
  }
});
