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
  assert.match(fn, /const shape = `\$\{font\(\)\}\|\$\{uiZoom\(\)\}\|\$\{uiText\(\)\}\|\$\{prefs\.chattools\}/);
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

test('no light theme paints a surface pure white', () => {
  for (const f of fs.readdirSync(path.join(root, 'deck/static/looks')).filter((x) => x.endsWith('.css'))) {
    const css = read(`deck/static/looks/${f}`);
    const block = css.match(/^:root\[data-theme=light\] \{[^}]*\}/m)?.[0] || '';
    for (const m of block.matchAll(/--(bg|frame|frame-2|frame-3|pane|pane-2|pane-3|paper|bubble|sheet|field):\s*(#[0-9a-f]+)/gi))
      assert.ok(!/^#(fff|ffffff)$/i.test(m[2]), `${f} --${m[1]} is pure white`);
  }
});

test('Default agent offers every agent, Gemini included, even where it is not installed', () => {
  const js = read('deck/static/app/13-settings.js');
  const core = read('deck/static/app/01-core.js');
  const vm = require('node:vm');
  const names = core.slice(core.indexOf('const AGENT_NAME'), core.indexOf('\n', core.indexOf('const AGENT_NAME')));
  const fn = js.slice(js.indexOf('const here = agentsOn(prefs)'), js.indexOf('$("#setagentwrap").hidden', js.indexOf('const here = agentsOn(prefs)')));
  const select = { value: '', options: [], replaceChildren(...o) { this.options = o; } };
  const ctx = { prefs: { agent: 'agy', agents: ['claude', 'codex'] }, f: { agent: select },
    $: () => select, h: (_t, a, text) => ({ value: a.value, text }),
    agentsOn: (s) => s.agents };
  vm.runInNewContext(`${names.replace('const ', 'var ')}\n${fn}`, ctx);
  const gemini = select.options.find((o) => o.value === 'agy');
  assert.equal(gemini.text, 'Gemini (not on this host)');
  assert.equal(select.options.find((o) => o.value === 'claude').text, 'Claude');
  assert.equal(select.value, 'agy');
});
