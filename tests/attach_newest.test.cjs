const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the shipped fallback with the calls it makes stubbed.
const source = fs.readFileSync(path.join(__dirname, '../deck/static/app/06-terminal.js'), 'utf8');
const start = source.indexOf('async function attachNewest(');
const end = source.indexOf('// The terminal', start);
assert.ok(start >= 0 && end > start, 'attachNewest remains available');

function run(sessions) {
  const calls = [];
  const ctx = {
    sessions, attached: null, calls,
    poll: async () => {}, useDraft: () => {}, toast: (m) => calls.push(['toast', m]),
    attach: (n) => calls.push(['attach', n]),
    api: async (...a) => { calls.push(['api', ...a]); return { session: 'deck-shell' }; },
  };
  vm.createContext(ctx);
  vm.runInContext(`${source.slice(start, end)}\nthis.go = attachNewest;`, ctx);
  return ctx.go().then(() => ctx);
}

test('no running session: nothing is started on the host', async () => {
  const ctx = await run([{ name: 'a', running: false, created: 1 }]);
  assert.deepEqual(ctx.calls, []);
  assert.equal(ctx.attached, null);
});

test('opens the newest running session', async () => {
  const ctx = await run([{ name: 'a', running: true, created: 1 }, { name: 'b', running: true, created: 2 }]);
  assert.deepEqual(ctx.calls, [['attach', 'b']]);
});
