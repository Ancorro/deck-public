const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// The box under the screen: the picker's choices and where a message goes (screen.js sayTo).
const source = fs.readFileSync(path.join(__dirname, '../deck/static/screen.js'), 'utf8');
const body = source.slice(source.indexOf('const DESK = '), source.indexOf('for (const b of document.querySelectorAll'));

function page({ sessions, kept = null, attached = null }) {
  const els = {};
  const el = (id) => (els[id] ||= { id, hidden: false, value: '', placeholder: '', textContent: '', title: '',
    dataset: {}, children: [], replaceChildren(...c) { this.children = c; } });
  const calls = [], keys = [], toasts = [];  // calls hold the sandbox's objects: compared as JSON
  const ctx = {
    rfb: { sendKey: (k) => keys.push(k) },
    $: (s) => el(s),
    matchMedia: () => ({ matches: false }),
    setInterval() {},
    Option: function (text, value, _d, selected) { Object.assign(this, { text, value, selected }); },
    localStorage: { getItem: () => kept, setItem() {} },
    window: {
      toast: (t) => toasts.push(t),
      deckPage: {
        sessions: () => sessions, attached: () => attached, home: 'home', poll() {},
        fullName: (n) => n.includes('/') ? n : `home/${n}`,
        api: async (method, url, data) => { calls.push(JSON.parse(JSON.stringify({ method, url, data }))); return { session: 'deck-claude-1' }; },
      },
    },
  };
  vm.createContext(ctx);
  vm.runInContext(body.replace('let sayTo = DESK, fresh = "";', 'var sayTo = DESK, fresh = "";') +
    '\nthis.renderTo = renderTo; this.get = () => sayTo;', ctx);
  return { ctx, els, calls, keys, toasts };
}

const S = [
  { name: 'home/a', title: 'Fix bug', running: true, preview: 'Reading the log' },
  { name: 'desk/b', label: 'Claude', host: 'desk', running: true },
  { name: 'home/old', title: 'Gone', running: false },
  { name: 'laptop/c', title: 'Away', host: 'laptop', running: true, offline: true },
];
const submit = async (p, text) => { p.ctx.$('#screentext').value = text; await p.ctx.$('#screensay').onsubmit({ preventDefault() {} }); };

test('the picker lists the desktop, running sessions on any host and a new session; it starts on the open tab', () => {
  const p = page({ sessions: S, attached: 'home/a' });
  p.ctx.renderTo();
  assert.deepEqual(p.els['#screento'].children.map((o) => o.text), ['Desktop', 'Fix bug', 'Claude · desk', '+ New Claude session']);
  assert.equal(p.ctx.get(), 'home/a');
});

test('a kept session that ended falls back to the desktop', () => {
  const p = page({ sessions: S, kept: 'home/old' });
  p.ctx.renderTo();
  assert.equal(p.ctx.get(), '');
  assert.equal(p.els['#screentext'].placeholder, 'Type on the desktop…');
});

test('Desktop types through sendKey; a session gets a message with Enter', async () => {
  const p = page({ sessions: S, kept: '' });
  p.ctx.renderTo();
  await submit(p, 'hi');
  assert.deepEqual(p.keys, [0x68, 0x69]);
  assert.equal(p.calls.length, 0);

  p.ctx.$('#screento').onchange({ target: { value: 'desk/b' } });
  await submit(p, 'look at the screen');
  assert.deepEqual(p.calls, [{ method: 'POST', url: '/api/sessions/desk%2Fb/send',
    data: { text: 'look at the screen', enter: true, via: 'PC browser' } }]);
  assert.equal(p.els['#screentext'].value, '');
});

test('New starts a Claude session with the message, then sends there', async () => {
  const p = page({ sessions: S, kept: '+new' });
  p.ctx.renderTo();
  await submit(p, 'open chrome');
  assert.deepEqual(p.calls[0], { method: 'POST', url: '/api/agent', data: { agent: 'claude', text: 'open chrome' } });
  assert.equal(p.ctx.get(), 'home/deck-claude-1');
});

test('a failed send puts the text back', async () => {
  const p = page({ sessions: S, kept: 'home/a' });
  p.ctx.window.deckPage.api = async () => { throw new Error('offline'); };
  await submit(p, 'hello');
  assert.equal(p.els['#screentext'].value, 'hello');
  assert.match(p.toasts[0], /Not sent: offline/);
});
