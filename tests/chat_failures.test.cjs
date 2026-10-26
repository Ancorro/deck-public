const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Run the shipped functions with only the DOM operations they use stubbed.
const source = fs.readFileSync(path.join(__dirname, '../deck/static/app/4-chat.js'), 'utf8');
const start = source.indexOf('function clearChatFailure(');
const end = source.indexOf('function chatEvent(', start);
assert.ok(start >= 0 && end > start, 'chat failure functions remain available');

class Element {
  constructor(tag, attrs = {}, children = []) {
    this.tag = tag;
    Object.assign(this, attrs);
    this.children = [];
    this.parent = null;
    this.append(...children);
  }
  get isConnected() { return this.root || !!this.parent?.isConnected; }
  get textContent() { return this.text ?? this.children.map(c => typeof c === 'string' ? c : c.textContent).join(''); }
  set textContent(value) { this.text = value; this.children = []; }
  append(...children) {
    for (const child of children) {
      this.children.push(child);
      if (typeof child !== 'string') child.parent = this;
    }
  }
  querySelector(tag) {
    for (const child of this.children) {
      if (typeof child === 'string') continue;
      if (child.tag === tag) return child;
      const found = child.querySelector(tag);
      if (found) return found;
    }
    return null;
  }
  replaceWith(next) {
    const parent = this.parent;
    assert.ok(parent, 'replacement target has a parent');
    parent.children[parent.children.indexOf(this)] = next;
    next.parent = parent;
    this.parent = null;
  }
  click() { if (!this.disabled) this.onclick?.(); }
}

function setup() {
  const sent = [], reconnected = [], toasts = [];
  const context = vm.createContext({
    h: (tag, attrs, ...children) => new Element(tag, attrs, children),
    toast: text => toasts.push(text),
    connectChat: name => reconnected.push(name),
    VIA: 'test browser',
    ws: { send() { assert.fail('retry used the global session socket'); } },
  });
  vm.runInContext(source.slice(start, end), context);
  const box = new Element('div');
  box.root = true;
  const view = { name: 'failed-session', box, working: false,
    socket: { readyState: 1, send: data => sent.push(JSON.parse(data)) } };
  return { context, view, sent, reconnected, toasts };
}

test('failure displays its text and retries through its own session socket once', () => {
  const { context, view, sent } = setup();
  context.chatFailure(view, '429 <busy>');
  assert.equal(view.failure.role, 'alert');
  assert.match(view.failure.textContent, /429 <busy>/);
  const retry = view.failure.querySelector('button');
  retry.click();
  retry.click();
  retry.onclick();
  context.syncChatFailure(view); // Repeated idle notifications cannot enable a sent retry.
  assert.deepEqual(sent, [{ t: 'say', d: 'Please continue from where you stopped.', via: 'test browser' }]);
  assert.equal(retry.disabled, true);
  assert.equal(retry.textContent, 'Retry requested…');
});

test('disconnected retry reconnects its session without sending or disabling retry', () => {
  const { context, view, sent, reconnected, toasts } = setup();
  view.socket.readyState = 3;
  context.chatFailure(view, 'connection failed');
  const retry = view.failure.querySelector('button');
  retry.click();
  assert.deepEqual(sent, []);
  assert.deepEqual(reconnected, ['failed-session']);
  assert.deepEqual(toasts, ['Chat disconnected. Reconnecting…']);
  assert.ok(!retry.disabled);
  view.socket.readyState = 1;
  retry.click();
  assert.equal(sent.length, 1);
});

test('retry while the agent is working sends nothing', () => {
  const { context, view, sent } = setup();
  view.working = true;
  context.chatFailure(view, 'busy');
  const retry = view.failure.querySelector('button');
  assert.equal(retry.disabled, true);
  assert.equal(retry.textContent, 'Agent still working…');
  assert.match(view.failure.querySelector('span').textContent, /Stop the agent/);
  retry.click();
  retry.onclick();
  assert.deepEqual(sent, []);
  view.working = false;
  context.syncChatFailure(view);
  assert.equal(retry.disabled, false);
  assert.equal(retry.textContent, 'Try again');
  retry.click();
  assert.equal(sent.length, 1);
});

test('clearing a failure preserves history and makes its retry inactive', () => {
  const { context, view, sent } = setup();
  context.chatFailure(view, 'earlier failure');
  const old = view.failure;
  context.clearChatFailure(view);
  context.clearChatFailure(view);
  assert.equal(view.failure, null);
  assert.deepEqual(view.box.children, [old]);
  const retry = old.querySelector('button');
  assert.equal(retry.textContent, 'Earlier error');
  retry.click();
  retry.onclick();
  assert.deepEqual(sent, []);
});

test('duplicate failure replaces its card and invalidates the previous retry handler', () => {
  const { context, view, sent } = setup();
  context.chatFailure(view, 'hook failure');
  const old = view.failure;
  context.chatFailure(view, 'transcript failure');
  assert.equal(view.box.children.length, 1);
  assert.equal(view.box.children[0], view.failure);
  assert.match(view.failure.textContent, /transcript failure/);
  assert.equal(old.isConnected, false);
  old.querySelector('button').onclick();
  assert.deepEqual(sent, []);
  view.failure.querySelector('button').click();
  assert.equal(sent.length, 1);
});
