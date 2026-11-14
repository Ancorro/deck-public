const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

test('OpenRouter routing covers helper agents, reasoning models and refreshes between requests', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'deck-routing-'));
  const previous = process.env.DECK_SAMPLING;
  process.env.DECK_SAMPLING = path.join(dir, 'settings.json');
  try {
    const source = fs.readFileSync(path.join(__dirname, '../deploy/agents/opencode-sampling.js'), 'utf8');
    const { DeckSampling } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
    const hook = (await DeckSampling())['chat.params'];
    const routing = { sort: 'price', data_collection: 'deny', zdr: true,
      preferred_min_throughput: { p50: 50 }, preferred_max_latency: { p50: 3 } };
    fs.writeFileSync(process.env.DECK_SAMPLING, JSON.stringify({ openrouter: routing, sampling: { build: { temperature: 0.4 } } }));
    for (const agent of ['build', 'plan', 'title', 'summary']) {
      const output = { options: { reasoning: { effort: 'high' }, provider: { allow_fallbacks: false, sort: 'latency' } } };
      await hook({ agent, model: { providerID: 'openrouter', id: 'test', capabilities: { temperature: false } } }, output);
      assert.deepEqual(output.options.provider, { allow_fallbacks: false, ...routing });
      assert.deepEqual(output.options.reasoning, { effort: 'high' });
      assert.equal(output.temperature, undefined);
    }
    const other = { options: {} };
    await hook({ agent: 'build', model: { providerID: 'anthropic', capabilities: { temperature: true } } }, other);
    assert.deepEqual(other, { options: {}, temperature: 0.4 });
    fs.writeFileSync(process.env.DECK_SAMPLING, JSON.stringify({ openrouter: { sort: 'throughput' } }));
    const updated = { options: {} };
    await hook({ agent: 'build', model: { providerID: 'openrouter' } }, updated);
    assert.deepEqual(updated.options.provider, { sort: 'throughput' });
    fs.writeFileSync(process.env.DECK_SAMPLING, '{}');
    const cleared = { options: {} };
    await hook({ agent: 'build', model: { providerID: 'openrouter' } }, cleared);
    assert.deepEqual(cleared.options, {});
  } finally {
    if (previous === undefined) delete process.env.DECK_SAMPLING;
    else process.env.DECK_SAMPLING = previous;
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
