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
