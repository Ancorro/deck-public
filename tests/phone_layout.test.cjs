// Phone layout fixes (user: "mobile UI still buggy"), checked at 360px and 393px in a real browser; these guard the rules.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const css = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "style.css"), "utf8");

test("the shut 14px tab strip hides each host's + (it stuck out half cut)", () => {
  assert.match(css, /#padcol\.shut #chips \.chiphost \.chnew \{ display: none; \}/);
});

test("a reply's buttons stay one line each; the row wraps instead", () => {
  assert.match(css, /\.cturnacts \{ display: flex; flex-wrap: wrap;/);
  assert.match(css, /\.cturnacts button \{ white-space: nowrap; \}/);
});

test("the key row's pinned right end covers the row's padding", () => {
  assert.match(css, /\.keys \.kright \{[^}]*box-shadow: 8px 0 0 var\(--screen\)/);
});

test("a phone's open shelf is wide enough for titles, its header wraps", () => {
  assert.match(css, /#shelf:not\(\.shut\) \{[^}]*width: min\(66%, 260px\)/);
  assert.match(css, /#shelfrow \{ flex-wrap: wrap;/);
});

test("the agent picker wraps rather than running off a phone", () => {
  assert.match(css, /\.agentpick \{ display: grid; grid-template-columns: repeat\(auto-fit, minmax\(78px, 1fr\)\);/);
  assert.doesNotMatch(css, /\.agentpick \{[^}]*grid-auto-flow: column/);
});

test("one session trash for the whole Deck: every host's, each row marked with its host, actions sent to it", () => {
  const js = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "app", "7-editor.js"), "utf8");
  assert.match(js, /api\("GET", `\$\{at\(host\)\}\/api\/session-trash`\)/);
  assert.match(js, /api\("POST", `\$\{at\(it\.host\)\}\/api\/session-trash\/\$\{it\.id\}\/restore`\)/);
  assert.match(js, /api\("DELETE", `\$\{at\(it\.host\)\}\/api\/session-trash\/\$\{it\.id\}`\)/);
  assert.match(js, /class: "trashhost"/);
});

test("text on the host colour turns dark when the colour is light", () => {
  const vm = require("node:vm");
  const js = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "app", "8-settings.js"), "utf8");
  const src = js.slice(js.indexOf("function setPower"), js.indexOf("\n}\n", js.indexOf("function setPower")) + 3);
  const props = {};
  const ctx = { document: { documentElement: { style: { setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; } } } } };
  vm.runInNewContext(`${src}; this.setPower = setPower;`, ctx);
  ctx.setPower("#22c7b8"); assert.equal(props["--on-accent"], "#121311"); // teal: dark text
  ctx.setPower("#ff4f12"); assert.equal(props["--on-accent"], "#121311"); // orange: dark text reads better
  ctx.setPower("#3d3dff"); assert.equal(props["--on-accent"], "#fff6f0"); // deep blue: light text
  const css = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "style.css"), "utf8");
  assert.doesNotMatch(css, /background: var\(--accent\);[^}]*color: #fff(6f0)?\b/);
});
