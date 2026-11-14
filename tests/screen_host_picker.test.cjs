const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../deck/static/screen.js'), 'utf8');
const body = source.slice(source.indexOf('let via ='), source.indexOf('// ---- phone screen'));

function page(screens, kept = null) {
  const els = {};
  const el = (id) => (els[id] ||= { id, hidden: false, disabled: false, children: [], checked: false,
    replaceChildren(...c) { this.children = c; } });
  const fetches = [];
  const ctx = {
    cfg: null, phone: null, wantPhone: false,
    $: (s) => el(s),
    Option: function (text, value, _d, selected) { Object.assign(this, { text, value, selected }); },
    localStorage: { getItem: () => kept, setItem() {} },
    fetch: async (url) => {
      fetches.push(url);
      if (url === '/api/hosts') return { json: async () => ({ hosts: [
        { name: 'home', current: true }, { name: 'desk' }, { name: 'laptop' }, { name: 'down' }] }) };
      if (url === '/api/screen') return { json: async () => screens.home || { enabled: false } };
      const host = decodeURIComponent((url.match(/^\/peer\/([^/]+)\/api\/screen$/) || [])[1] || '');
      if (host === 'down') throw new Error('502');
      if (host) return { json: async () => screens[host] || { enabled: false } };
      throw new Error('unexpected ' + url);
    },
  };
  vm.createContext(ctx);
  vm.runInContext(body.replace('let via = "", screens = [];', 'var via = "", screens = [];') +
    '\nthis.init = init; this.at = at; this.get = () => ({ via, cfg });', ctx);
  return { ctx, els, fetches };
}

const on = (path = '/vnc/websockify') => ({ enabled: true, path, password: 'x' });

test('the picker lists every host with a screen, this one first, and skips a peer that fails', async () => {
  const { ctx, els } = page({ home: on(), desk: on() });
  await ctx.init();
  assert.deepEqual(els['#screenhost'].children.map((o) => [o.text, o.value]), [['home', ''], ['desk', 'desk']]);
  assert.equal(els['#screenhost'].hidden, false);
  assert.equal(els['#screenhost'].disabled, false);
  assert.equal(ctx.get().via, '');
  assert.equal(ctx.at('/vnc/websockify'), '/vnc/websockify');
});

test('a kept choice opens that host, through /peer', async () => {
  const { ctx } = page({ home: on(), desk: on() }, 'desk');
  await ctx.init();
  assert.equal(ctx.get().via, 'desk');
  assert.equal(ctx.at('/vnc/websockify'), '/peer/desk/vnc/websockify');
});

test('with no screen here, the first other host that has one is shown, as before', async () => {
  const { ctx, els } = page({ laptop: on() });
  await ctx.init();
  assert.equal(ctx.get().via, 'laptop');
  assert.equal(els['#screenhost'].hidden, false);
  assert.equal(els['#screenhost'].disabled, true);
});

test('only this host has a screen: no picker', async () => {
  const { ctx, els } = page({ home: on() });
  await ctx.init();
  assert.equal(els['#screenhost'].hidden, true);
  assert.equal(els['#screentab'].hidden, false);
});

test('no screen anywhere: no Screen tab', async () => {
  const { ctx, els } = page({});
  await ctx.init();
  assert.equal(els['#screentab'].hidden, true);
});

test('Stop shows OFF: the closing connection is no longer current, so its own event is ignored', () => {
  const src = source.slice(source.indexOf('function status('), source.indexOf('// Stream start and stop'));
  const els = {};
  const ctx = { $: (s) => (els[s] ||= { textContent: '' }), rfb: null, closed: 0 };
  vm.createContext(ctx);
  vm.runInContext(src.replace('function disconnect()', 'var _d = 0; function disconnect()') +
    '\nthis.disconnect = disconnect; this.setRfb = (r) => { rfb = r; };', ctx);
  els['#screenstatus'] = { textContent: 'LIVE' };
  ctx.setRfb({ disconnect() { ctx.closed++; } });
  ctx.disconnect();
  assert.equal(ctx.closed, 1);
  assert.equal(els['#screenstatus'].textContent, 'OFF');
});
