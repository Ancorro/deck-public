const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../deck/static/app/13-settings.js'), 'utf8');

test('each agent has a default model menu that loads its models and keeps an unavailable saved choice', async () => {
  const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
  for (const k of ['claude', 'codex', 'opencode', 'pi', 'agy', 'openclaw']) assert.match(html, new RegExp(`<select name="model-${k}"`));
  const select = { replaceChildren(...items) { this.items = items; }, append(item) { this.items.push(item); } };
  const asked = [];
  const context = { $: (s) => s === '#setform' ? {elements: {'model-pi': select}} : {}, AGENT_NAME: {}, HOME_HOST: 'a', document: {activeElement: null},
    hostList: [{name: 'a'}, {name: 'b'}, {name: 'c'}], hostApi: (h) => h === 'a' ? '' : `/peer/${h}`,
    prefs: {model: {pi: 'old'}}, api: async (_, url) => { asked.push(url); if (url.startsWith('/peer/c')) throw new Error('down');
      return {data: url.startsWith('/peer/b') ? [{model: 'new'}, {model: 'other'}] : [{model: 'new', displayName: 'New'}]}; },
    h: (tag, attrs, label) => ({tag, ...attrs, label}) };
  const start = source.indexOf('const MODEL_AGENTS');
  const end = source.indexOf('function openSettings()', start);
  vm.runInNewContext(source.slice(start, end) + '; this.loadModelDefaults = loadModelDefaults;', context);
  await context.loadModelDefaults('pi');
  assert.deepEqual(asked, ['/api/models/pi']);
  assert.deepEqual(select.items.map((x) => x.value), ['', 'new', 'old']);
  assert.equal(select.disabled, false);
  assert.equal(select.value, 'old');
});

test('editing two default models keeps the other choices, including clearing one', async () => {
  let handler;
  const calls = [];
  const context = {
    $: () => ({ addEventListener: (_, fn) => { handler = fn; } }),
    prefs: { model: { claude: 'opus', codex: 'old-model' } },
    saveSetting: async (fields) => { calls.push(fields); Object.assign(context.prefs, fields); },
  };
  const start = source.indexOf('$("#setform").addEventListener("change"');
  const end = source.indexOf('$("#setform").onsubmit', start);
  vm.runInNewContext(source.slice(start, end), context);
  await handler({target: {name: 'model-codex', value: '  gpt-test  '}});
  assert.equal(calls[0].model.claude, 'opus');
  assert.equal(calls[0].model.codex, 'gpt-test');
  await handler({target: {name: 'model-agy', value: 'gemini-test'}});
  assert.equal(calls[1].model.codex, 'gpt-test');
  await handler({target: {name: 'model-codex', value: ''}});
  assert.equal(calls[2].model.codex, '');
  assert.equal(calls[2].model.claude, 'opus');
  assert.equal(calls[2].model.agy, 'gemini-test');
});
