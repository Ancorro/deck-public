// Phone layout fixes (user: "mobile UI still buggy"), checked at 360px and 393px in a real browser; these guard the rules.
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const css = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "style.css"), "utf8");

test("the shut tab strip hides each host's name and + (the + stuck out half cut)", () => {
  assert.match(css, /#padcol\.shut \.chiphost :is\(\.chname, em, \.fchev, \.chnew\) \{ display: none; \}/);
});

test("a reply's buttons stay one line each; the row wraps instead", () => {
  assert.match(css, /\.cturnacts \{ display: flex; flex-wrap: wrap;/);
  assert.match(css, /\.cturnacts button \{ white-space: nowrap;/);
});

test("the key row's pinned right end covers the row's padding, in the surface's own colour", () => {
  assert.match(css, /\.keys \.kright \{[^}]*box-shadow: 8px 0 0 var\(--c-bg\)/);
});

test("a phone's open shelf is as wide as the open tab column, so the chat shows beside it; its header wraps", () => {
  const width = (sel) => css.match(new RegExp(`${sel} \\{[^}]*width: (min\\([^)]*\\))`))[1];
  assert.equal(width("#shelf:not\\(\\.shut\\)"), "min(48%, 210px)");
  assert.equal(width("#shelf:not\\(\\.shut\\)"), width("#padcol:not\\(\\.shut\\)"));
  assert.match(css, /#shelfrow \{[^}]*flex-wrap: wrap;/);
});

// The shut tab strip and shut shelf are one width on a phone (style.css: 14px). A look that sets either's width
// outside its PC block (min-width: 1100px) wins over that and makes them differ (user 2026-11-14: "once again looking
// not the same width"). Rack is left out: its tab column is a bar across the top.
test("no look sets a shut strip's width on a phone", () => {
  const outsidePc = (text) => { // the stylesheet without its @media (min-width: 1100px) blocks
    let out = "", i = 0;
    for (const m of text.matchAll(/@media \(min-width: 1100px\)\s*\{/g)) {
      if (m.index < i) continue;
      out += text.slice(i, m.index);
      let depth = 1, j = m.index + m[0].length;
      for (; depth && j < text.length; j++) depth += text[j] === "{" ? 1 : text[j] === "}" ? -1 : 0;
      i = j;
    }
    return out + text.slice(i);
  };
  const looks = path.join(__dirname, "..", "deck", "static", "looks");
  for (const file of fs.readdirSync(looks).filter((f) => f.endsWith(".css"))) {
    const text = outsidePc(fs.readFileSync(path.join(looks, file), "utf8"));
    assert.doesNotMatch(text, /#shelf\.shut \{[^}]*[^-]width:/, `${file}: shut shelf width on a phone`);
    if (file !== "rack.css") assert.doesNotMatch(text, /#padcol(\.shut)? \{[^}]*[^-]width:/, `${file}: shut tab strip width on a phone`);
  }
});

test("the agent picker wraps rather than running off a phone", () => {
  assert.match(css, /\.agentpick \{ display: grid; grid-template-columns: repeat\(auto-fit, minmax\(78px, 1fr\)\);/);
  assert.doesNotMatch(css, /\.agentpick \{[^}]*grid-auto-flow: column/);
});

test("one session trash for the whole Deck: every host's, each row marked with its host, actions sent to it", () => {
  const js = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "app", "05-tabs.js"), "utf8");
  assert.match(js, /api\("GET", `\$\{at\(host\)\}\/api\/session-trash`\)/);
  assert.match(js, /api\("POST", `\$\{at\(it\.host\)\}\/api\/session-trash\/\$\{it\.id\}\/restore`\)/);
  assert.match(js, /api\("DELETE", `\$\{at\(it\.host\)\}\/api\/session-trash\/\$\{it\.id\}`\)/);
  assert.match(js, /class: "trashhost"/);
});

test("text on the host colour turns dark when the colour is light", () => {
  const vm = require("node:vm");
  const js = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "app", "13-settings.js"), "utf8");
  const normalized = js.replace(/\r\n/g, "\n");
  const src = normalized.slice(normalized.indexOf("function setPower"), normalized.indexOf("\n}\n", normalized.indexOf("function setPower")) + 3);
  const props = {};
  const ctx = { document: { documentElement: { style: { setProperty: (k, v) => { props[k] = v; }, removeProperty: (k) => { delete props[k]; } } } } };
  vm.runInNewContext(`${src}; this.setPower = setPower;`, ctx);
  ctx.setPower("#22c7b8"); assert.equal(props["--on-accent"], "#121311"); // teal: dark text
  ctx.setPower("#ff4f12"); assert.equal(props["--on-accent"], "#121311"); // orange: dark text reads better
  ctx.setPower("#3d3dff"); assert.equal(props["--on-accent"], "#fff6f0"); // deep blue: light text
  const css = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "style.css"), "utf8");
  assert.doesNotMatch(css, /background: var\(--accent\);[^}]*color: #fff(6f0)?\b/);
});

test("a shelf card with cut text fetches the item whole before showing it (the list cuts every long text)", () => {
  const js = fs.readFileSync(path.join(__dirname, "..", "deck", "static", "app", "10-shelf.js"), "utf8");
  assert.match(js, /const fill = async \(\) => \{[\s\S]*?const f = await fullItem\(it\);[\s\S]*?chatMarkdown\(f\.text\)/);
});

// "can't change model in mobile": agy lists its models in a few seconds, and any synced change meanwhile (a Board card,
// a shelf pin, a setting on any host) reloads the settings, whose placeHarness closed the open Model menu.
const app = (f) => fs.readFileSync(path.join(__dirname, "..", "deck", "static", "app", f), "utf8");
const fnSrc = (js, head) => js.slice(js.indexOf(head), js.indexOf("\n}\n", js.indexOf(head)) + 3);

test("a settings reload keeps the open Model menu; opening another session closes it", () => {
  const vm = require("node:vm");
  const body = fnSrc(app("02-prefs.js"), "function placeHarness");
  const tail = body.slice(body.lastIndexOf("\n  //", body.indexOf("closeMenu()")));  // the closing rule, on its own
  let closed = 0;
  const ctx = { menuReq: { rid: "m1" }, menuSession: "deck-agy-1", attached: "deck-agy-1", closeMenu: () => { closed++; } };
  vm.runInNewContext(`function rule() {${tail}; this.rule = rule;`, ctx);
  ctx.rule(); assert.equal(closed, 0, "same session: the menu stays open");
  ctx.attached = "deck-claude-2"; ctx.rule(); assert.equal(closed, 1, "another session: its menu goes");
  ctx.menuReq = null; ctx.rule(); assert.equal(closed, 1, "no menu open: nothing to close");
  assert.match(app("09-composer.js"), /menuReq = \{ rid, btn \}; menuSession = attached;/);
  assert.match(app("09-composer.js"), /menuReq = \{ rid: "brain", btn \}; menuSession = attached;/);
});

test("a menu on a button far along a phone's key row is pulled back onto the screen", () => {
  const vm = require("node:vm");
  const src = fnSrc(app("09-composer.js"), "function placeMenu");
  const rect = (left, width) => ({ left, right: left + width, width });
  const box = rect(0, 400), btnRect = rect(260, 50), style = {};
  const pop = { style, parentElement: { getBoundingClientRect: () => box },
    getBoundingClientRect: () => rect(parseFloat(style.left) || 0, 220) };
  const btn = { getBoundingClientRect: () => btnRect, closest: () => null };
  const ctx = { $: () => pop, menuReq: { btn }, wide: { matches: false }, uiZoom: () => 1 };
  vm.runInNewContext(`${src}; this.placeMenu = placeMenu;`, ctx);
  ctx.placeMenu();
  assert.equal(style.left, "180px", "right edge on the box's right edge (260 + 220 would end at 480 of 400)");
  btnRect.left = 20; ctx.placeMenu();
  assert.equal(style.left, "20px", "a button near the start keeps the menu lined up with it");
});
