const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// The phone app's host list: a host that doesn't answer hands over to the next (user: no main host).
const page = fs.readFileSync(path.join(__dirname, '../app/www/index.html'), 'utf8');
const start = page.indexOf('const WAIT =');
const end = page.indexOf('function render()', start);
assert.ok(start >= 0 && end > start, 'reach and open remain available');

function run(hosts, upUrls, h, pair, setting, deck = null) {
  const msg = { className: '', textContent: '' };
  let stored = JSON.stringify(hosts);
  const ctx = {
    location: { href: '' }, sessionStorage: { setItem() {} }, localStorage: { getItem: () => null }, setTimeout, clearTimeout, AbortController,
    $: () => msg, Tunnel: null, Fs: null,
    load: () => JSON.parse(stored), save: async (x) => { stored = JSON.stringify(x); },
    fetch: async (url) => { if (!upUrls.some((u) => url.startsWith(u))) throw new Error('down'); return {}; },
  };
  vm.createContext(ctx);
  vm.runInContext(`${page.slice(start, end)}\nthis.go = open; this.rows = rows; this.merge = mergeDeck; deck = ${JSON.stringify(deck)}; appPrefs = ${JSON.stringify(setting === 'off' ? { failover: false } : {})};`, ctx); // the setting: app.json, not localStorage
  return ctx.go(h, pair).then(() => ({ href: ctx.location.href, msg, hosts: JSON.parse(stored), rows: ctx.rows, merge: ctx.merge }));
}

// Pairing with no Tailscale (emulator test 2026-10-22: the QR's ts.net page can't load there)
test('a host with an address and an iroh key falls back to the tunnel when the address is down', async () => {
  const h = { name: 'cloud', url: 'https://cloud', key: 'K', used: 3 };
  const ctxHosts = [h];
  const msg = { className: '', textContent: '' };
  const ctx = {
    location: { href: '' }, sessionStorage: { setItem() {} }, localStorage: { getItem: () => null }, setTimeout, clearTimeout, AbortController,
    $: () => msg, Fs: null, Tunnel: { open: async ({ key }) => ({ port: key === 'K' ? 4321 : 0 }) },
    load: () => JSON.parse(JSON.stringify(ctxHosts)), save: async () => {}, fetch: async () => { throw new Error('no tailnet'); },
  };
  vm.createContext(ctx);
  vm.runInContext(`${page.slice(start, end)}\nthis.go = open; deck = null; appPrefs = {};`, ctx);
  await ctx.go(h);
  assert.equal(ctx.location.href, 'http://127.0.0.1:4321/');
});

test('a host first saved by its iroh key merges into the Deck entry, keeping when it was used', async () => {
  const { merge, rows } = await run(hosts, ['https://cloud'], hosts[0]);
  const saved = [{ name: 'desk', url: 'iroh:K1', key: 'K1', used: 9 }];
  const d = { name: 'cloud', hosts: [{ name: 'desk', url: 'https://desk', key: 'K1' }, { name: 'box', url: 'iroh:K2', key: 'K2' }] };
  const out = merge(saved, d);
  assert.equal(out.length, 2);
  assert.deepEqual({ ...out.find((x) => x.url === 'https://desk') }, { name: 'desk', url: 'https://desk', key: 'K1', used: 9 });
  const list = rows(out, d);
  assert.equal(list.length, 1);
  assert.equal(list[0].count, 2);
});

test('the Add box and Scan take a pairing link, iroh key and code included', () => {
  const from = page.indexOf('function pairingLink');
  const ctx = { URL, URLSearchParams, nameOf: (u) => new URL(u).hostname.split('.')[0] };
  vm.createContext(ctx);
  vm.runInContext(`${page.slice(from, page.indexOf('\n}\n', from) + 2)}\nthis.p = pairingLink;`, ctx);
  assert.deepEqual({ ...ctx.p('https://desk.x.ts.net/#pair=C1&iroh=K1&name=desk') },
    { name: 'desk', url: 'iroh:K1', key: 'K1', pair: 'C1' });
  assert.deepEqual({ ...ctx.p('deck://pair?key=K2&name=box&code=C2') }, { name: 'box', url: 'iroh:K2', key: 'K2', pair: 'C2' });
  assert.deepEqual({ ...ctx.p('https://desk.x.ts.net/#pair=C3') }, { name: 'desk', url: 'https://desk.x.ts.net', pair: 'C3' });
  assert.equal(ctx.p('https://desk.x.ts.net/'), null);
  assert.equal(ctx.p('not a link'), null);
});

test('the whole host list script runs to the end on load', async () => {
  const script = page.slice(page.indexOf('<script>') + 8, page.lastIndexOf('</script>'));
  const els = new Map();
  let rendered = false;
  const el = () => ({ hidden: false, value: '', textContent: '', className: '', style: {}, checked: false, replaceChildren() { rendered = true; },
    querySelector: () => el(), showModal() {}, close() {}, append() {} });
  const ctx = {
    document: { getElementById: (id) => els.get(id) || els.set(id, el()).get(id), createElement: el, querySelectorAll: () => [] },
    localStorage: { getItem: () => null, setItem() {} }, sessionStorage: { getItem: () => null, setItem() {} },
    location: { hash: '', pathname: '/', href: '' }, history: { replaceState() {} }, window: { addEventListener() {} },
    navigator: {}, fetch: async () => { throw new Error('none'); }, setTimeout, clearTimeout, URL, URLSearchParams, console,
  };
  ctx.window = Object.assign(ctx.window, ctx);
  vm.createContext(ctx);
  vm.runInContext(script, ctx);
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(rendered, 'the startup code ran to render()');
  assert.ok(!page.includes('id="failover"'), 'the failover switch lives in Settings → Advanced now, not on the start page');
});

test("the app's own QR reader decodes a host's pairing code (no Play Services needed)", () => {
  const jsQR = require('../app/www/vendor/jsQR.js');
  const rows = JSON.parse(fs.readFileSync(path.join(__dirname, 'pair_qr.json'), 'utf8')); // qrcode's modules, 1 = dark
  const k = 6, n = rows.length * k, px = new Uint8ClampedArray(n * n * 4);
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    const v = rows[Math.floor(y / k)][Math.floor(x / k)] === '1' ? 0 : 255;
    px.set([v, v, v, 255], (y * n + x) * 4);
  }
  assert.match(jsQR(px, n, n).data, /#pair=C1&iroh=814f.*&name=desk$/);
});

test('the host list asks in its own dialog, never the system confirm', () => {
  const script = page.slice(page.indexOf('<script>'));
  assert.doesNotMatch(script, /\bconfirm\(/);
  assert.match(page, /<dialog id="ask">/);
});

// The Deck as one entry (user: no separate host sites)
const deck = { name: 'cloud', hosts: [{ name: 'cloud', url: 'https://cloud' }, { name: 'desk', url: 'https://desk' }] };

test('the Deck\'s hosts show as one row named after the Deck; other hosts keep their own', async () => {
  const { rows } = await run(hosts, ['https://cloud'], hosts[0], undefined, undefined, deck);
  const list = rows(hosts, deck);
  assert.equal(list.length, 2);
  assert.equal(list.find((r) => r.deck).name, 'cloud');
  assert.equal(list.find((r) => r.deck).count, 2);
  assert.equal(list.find((r) => !r.deck).name, 'laptop');
});

test('a Deck host falls back only to the Deck\'s other hosts', async () => {
  const r = await run(hosts, ['https://laptop'], hosts[0], undefined, undefined, deck);
  assert.equal(r.href, '');
  assert.match(r.msg.textContent, /No host is answering \(cloud, desk\)/);
});

const hosts = [
  { name: 'cloud', url: 'https://cloud', used: 3 },
  { name: 'desk', url: 'https://desk', used: 2 },
  { name: 'laptop', url: 'https://laptop', used: 1 },
];

test('the chosen host opens when it answers', async () => {
  const r = await run(hosts, ['https://cloud', 'https://desk'], hosts[0]);
  assert.equal(r.href, 'https://cloud/');
});

test('a host that is down hands over to the most recently used one that answers', async () => {
  const r = await run(hosts, ['https://laptop', 'https://desk'], hosts[0]);
  assert.equal(r.href, 'https://desk/');
  assert.ok(r.hosts.find((x) => x.name === 'desk').used > 3, 'the host that opened becomes the last used');
});

test('nothing answering says so and stays on the list', async () => {
  const r = await run(hosts, [], hosts[0]);
  assert.equal(r.href, '');
  assert.match(r.msg.textContent, /No host is answering/);
});

test('pairing never fails over to another host', async () => {
  const r = await run(hosts, ['https://desk'], hosts[0], 'CODE');
  assert.equal(r.href, '');
});

test('with the setting off, a down host just says so', async () => {
  const r = await run(hosts, ['https://desk'], hosts[0], undefined, 'off');
  assert.equal(r.href, '');
  assert.match(r.msg.textContent, /No host is answering \(cloud\)/);
});
