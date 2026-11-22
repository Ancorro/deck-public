// Edit on a waiting message and "Back to this" put text in the message box to edit (user: "edit message not work on
// mobile"): on a phone the box keeps the keyboard shut (inputmode none) till the box itself is tapped, so these go
// through editInBox, which lifts that and focuses afresh.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const app = (f) => fs.readFileSync(path.join(__dirname, "..", "deck", "static", "app", f), "utf8");

test("editInBox opens the keyboard on a phone", () => {
  const js = app("09-composer.js");
  const body = js.slice(js.indexOf("function editInBox("), js.indexOf("\n}", js.indexOf("function editInBox(")));
  assert.match(body, /removeAttribute\("inputmode"\)/);
  assert.ok(body.indexOf("blur()") < body.indexOf("removeAttribute") && body.indexOf("removeAttribute") < body.indexOf("focus()"));
});

test("Edit and Back to this both use it, and the queue's actions follow the agent's keys", () => {
  const js = app("07-chat.js");
  assert.equal((js.match(/editInBox\(/g) || []).length, 2);
  assert.doesNotMatch(js, /msg\.focus\(\)/);
  assert.match(js, /keys\.includes\("edit"\)/);
  assert.match(js, /keys\.includes\("sendnow"\)/);
});

// Collapse on a sent message folds it to two lines, as Hide reply folds a reply (user 2026-11-21: "let me collapse my
// messages in chat as well"); a tap on a collapsed message opens it again.
test("a sent message offers Collapse, and a tap shows a collapsed one again", () => {
  const js = app("07-chat.js");
  const body = js.slice(js.indexOf("function userBubble("), js.indexOf("\nfunction foldable("));
  assert.match(body, /"Collapse"/);
  assert.match(body, /classList\.add\("shut"\)/);
  assert.match(body, /if \(el\.classList\.contains\("shut"\)\) \{ el\.classList\.remove\("shut"\)/);
  assert.match(body, /fold\.hidden = !foldable\(body\)/);
  const css = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "style.css"), "utf8");
  assert.match(css, /\.cmsg\.user\.shut > span \{[^}]*-webkit-line-clamp: 2/);
});

// A chat shows a thin scroll bar at its right edge (user 2026-11-21: "there should be a scroll bar on the right of chat")
test("a chat shows its scroll bar", () => {
  const css = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "style.css"), "utf8");
  assert.match(css, /\.chatlog \{ scrollbar-width: thin;/);
  assert.doesNotMatch(css, /\.chatlog::-webkit-scrollbar \{ display: none; \}/);
});

// Ctrl+Enter sends the waiting messages now, as Send now does (user 2026-11-22: "let me ctrl enter to send the waiting
// msges"); a message typed with it gets its Send now once it shows as waiting.
test("Ctrl+Enter in a chat sends the waiting messages now", () => {
  const js = app("09-composer.js");
  assert.match(js, /chatView && \(ev\.ctrlKey \|\| ev\.metaKey\)/);
  assert.match(js, /sendNowNext = Date\.now\(\); submitMsg\(\); \} else sendWaitingNow\(\)/);
  const chat = app("07-chat.js");
  const body = chat.slice(chat.indexOf("function sendWaitingNow("), chat.indexOf("\n}", chat.indexOf("function sendWaitingNow(")));
  assert.match(body, /\.qnow:not\(\[disabled\]\)/);
  assert.match(body, /t: "sendnow"/);
  assert.match(chat, /Date\.now\(\) - sendNowNext < 10e3 && !chatBatch\) \{ \/\/ after Ctrl\+Enter\n\s+sendNowNext = 0;\n\s+sendWaitingNow\(\);/);
});
