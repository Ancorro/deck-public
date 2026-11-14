const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../deck/static/app/05-tabs.js'), 'utf8');
const start = source.indexOf('let newHost =');
const end = source.indexOf('$("#newclose")', start);
const core = fs.readFileSync(path.join(__dirname, '../deck/static/app/01-core.js'), 'utf8');
const helpers = core.slice(core.indexOf('const AGENT_NAME'), core.indexOf('\n', core.indexOf('const AGENT_NAME'))) + '\n' +
  core.slice(core.indexOf('function agentsOn'), core.indexOf('function icon'));

function picker(remote, prefs = { pi: true, agy: false, openclaw: false }) {
  const calls = [], list = { children: [], replaceChildren(...children) { this.children = children; } };
  const heading = {};
  const dialog = { open: false, shown: 0, querySelector: () => heading,
    showModal() { this.open = true; this.shown++; }, close() { this.open = false; } };
  const ctx = {
    HOME_HOST: 'cloud', prefs,
    $: (selector) => selector === '#newsess' ? dialog : list,
    h: (tag, attrs, ...children) => ({ tag, ...attrs, children }), icon: (type) => type,
    api: async (method, url, body) => {
      calls.push({ method, url, body });
      return method === 'GET' ? await remote() : { session: 'deck-agy-test' };
    },
    poll: async () => {}, openSession: (name) => calls.push({ opened: name }),
    newShell: () => {}, toast: (message) => calls.push({ error: message }),
    opened: [],
  };
  vm.createContext(ctx);
  vm.runInContext(helpers + source.slice(start, end) + '\nthis.openNew = openNew;', ctx);
  return { ctx, calls, list, dialog };
}

test('cloud page offers agy installed on the selected PC and starts it there', async () => {
  const { ctx, calls, list } = picker(async () => ({ pi: false, agy: true, openclaw: false }));
  await ctx.openNew('desk');
  assert.equal(calls[0].url, '/peer/desk/api/settings');
  const agy = list.children.find((row) => row.children[1].children[0] === 'agy');
  assert.ok(agy);
  assert.ok(!list.children.some((row) => row.children[1].children[0] === 'Pi'));
  await agy.onclick();
  assert.equal(calls[1].url, '/peer/desk/api/agent');
  assert.equal(calls[1].body.agent, 'agy');
  assert.equal(calls[2].opened, 'desk/deck-agy-test');
});

test('home picker uses home capabilities without a peer request', async () => {
  const { ctx, calls, list } = picker(() => assert.fail('unexpected peer request'));
  await ctx.openNew();
  assert.equal(calls.length, 0);
  assert.ok(!list.children.some((row) => row.children[1].children[0] === 'agy'));
});

test('late peer response cannot overwrite a newer host picker', async () => {
  let release;
  const { ctx, list } = picker(() => new Promise((resolve) => { release = resolve; }));
  const pending = ctx.openNew('desk');
  await ctx.openNew('cloud');
  release({ agy: true });
  await pending;
  assert.ok(!list.children.some((row) => row.children[1].children[0] === 'agy'));
});

test('unreachable selected host shows its error without offering local agents', async () => {
  const { ctx, list } = picker(async () => { throw Error('desk is offline'); });
  await ctx.openNew('desk');
  assert.equal(list.children[0].children[0], 'desk is offline');
});

const labels = (list) => list.children.map((row) => row.children?.[1]?.children[0]).filter(Boolean);

test('picker offers only the agents set up on the host', async () => {
  const { ctx, list } = picker(null, { agents: ['claude', 'pi'] });
  await ctx.openNew();
  assert.deepEqual(labels(list), ['Terminal', 'Claude', 'Pi']);
});

test('choose-each-time off starts the default agent without the picker', async () => {
  const { ctx, calls, dialog } = picker(null, { agents: ['claude', 'codex'], agentpick: false, agent: 'codex' });
  await ctx.openNew();
  assert.equal(dialog.open, false);
  assert.equal(calls[0].url, '/api/agent');
  assert.equal(calls[0].body.agent, 'codex');
});

test('choose-each-time off falls back to the picker when the default agent is not set up', async () => {
  const { ctx, calls, dialog, list } = picker(null, { agents: ['claude'], agentpick: false, agent: 'codex' });
  await ctx.openNew();
  assert.equal(dialog.open, true);
  assert.equal(calls.length, 0);
  assert.deepEqual(labels(list), ['Terminal', 'Claude']);
});

test('choose-each-time off starts the default agent on another host when it is set up there', async () => {
  const { ctx, calls, dialog } = picker(async () => ({ agents: ['codex'] }), { agents: ['claude', 'codex'], agentpick: false, agent: 'codex' });
  await ctx.openNew('desk');
  assert.equal(dialog.open, false);
  assert.equal(calls[1].url, '/peer/desk/api/agent');
  assert.equal(calls[1].body.agent, 'codex');
});

test('holding + opens the picker even with choose-each-time off', async () => {
  const { ctx, calls, dialog, list } = picker(null, { agents: ['claude', 'codex'], agentpick: false, agent: 'codex' });
  await ctx.openNew('', true);
  assert.equal(dialog.open, true);
  assert.equal(calls.length, 0);
  assert.deepEqual(labels(list), ['Terminal', 'Claude', 'Codex']);
});

test('choose-each-time off never flashes the picker while another host answers (user: "annoying dialogue")', async () => {
  const { ctx, calls, dialog } = picker(async () => ({ agents: ['codex'] }), { agents: ['claude', 'codex'], agentpick: false, agent: 'codex' });
  await ctx.openNew('desk');
  assert.equal(dialog.shown, 0);
  assert.equal(calls[1].url, '/peer/desk/api/agent');
});

test('choose-each-time off opens the picker on another host only when the default agent is not set up there', async () => {
  const { ctx, dialog, list } = picker(async () => ({ agents: ['claude'] }), { agents: ['claude', 'codex'], agentpick: false, agent: 'codex' });
  await ctx.openNew('desk');
  assert.equal(dialog.shown, 1);
  assert.deepEqual(labels(list), ['Terminal', 'Claude']);
});

test('choose-each-time off reports an unreachable host as a toast, not a dialog', async () => {
  const { ctx, calls, dialog } = picker(async () => { throw Error('desk is offline'); }, { agents: ['claude'], agentpick: false, agent: 'claude' });
  await ctx.openNew('desk');
  assert.equal(dialog.shown, 0);
  assert.deepEqual(calls.at(-1), { error: 'desk is offline' });
});
