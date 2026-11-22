const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Looks (Settings → Appearance → Look): style.css is the structure, with every colour, font and radius a token; each
// look in deck/static/looks/ sets the tokens for light and dark and restyles what it wants.
const root = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const html = read('deck/static/index.html');
const base = read('deck/static/style.css');
const LOOKS = ['pad', 'rack', 'blueprint', 'zine', 'plain', 'message'];
// the tokens a look must set (the page's two colour contexts, the accent's companions, type)
const TOKENS = ['--bg', '--frame', '--frame-2', '--frame-3', '--frame-text', '--frame-text-2', '--frame-text-3', '--frame-line',
  '--pane', '--pane-2', '--pane-3', '--pane-text', '--pane-text-2', '--pane-text-3', '--pane-line', '--ui', '--mono', '--label'];

test('the head script, the Settings picker and the server agree on the looks', () => {
  const head = html.slice(0, html.indexOf('</head>'));
  const map = head.slice(head.indexOf('const LOOKS = {'), head.indexOf('};', head.indexOf('const LOOKS = {')));
  assert.deepEqual([...map.matchAll(/^\s+(\w+): /gm)].map((m) => m[1]), LOOKS);
  assert.deepEqual([...html.matchAll(/<input type="radio" name="look" value="(\w+)">/g)].map((m) => m[1]), LOOKS);
  const core = read('deck/settings.py');
  assert.match(core, new RegExp(`"look": \\("${LOOKS.join('", "')}"\\)`));
  assert.match(core, /"look": "pad"/); // the default: the one nearest the original Deck
});

test('each look is a stylesheet that sets every token for dark and light', () => {
  for (const look of LOOKS) {
    const css = read(`deck/static/looks/${look}.css`);
    const dark = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {')));
    const light = css.slice(css.indexOf(':root[data-theme=light] {'), css.indexOf('}', css.indexOf(':root[data-theme=light] {')));
    for (const t of TOKENS) assert.match(dark, new RegExp(`${t}:`), `${look}: ${t} in dark`);
    for (const t of TOKENS.filter((x) => !x.startsWith('--ui') && !x.startsWith('--mono') && x !== '--label'))
      assert.match(light, new RegExp(`${t}:`), `${look}: ${t} in light`);
    // the terminal reads the pane's colours as plain hex (06-terminal.js termTheme)
    assert.match(dark, /--pane: #[0-9a-f]{6};/); assert.match(light, /--pane: #[0-9a-f]{6};/);
  }
});

test('the page loads the saved look before the first paint, and applies a change in place', () => {
  const head = html.slice(0, html.indexOf('</head>'));
  assert.match(head, /document\.documentElement\.dataset\.look = look;/);
  assert.match(head, /sheet\.href = `\/static\/looks\/\$\{look\}\.css/);
  assert.match(head, /prefs\.look in LOOKS \? prefs\.look : "pad"/);
  const core = read('deck/static/app/02-prefs.js');
  assert.match(core, /function applyLook\(\)/);
  assert.match(core, /\$\("#lookfonts"\)\?\.remove\(\);/); // the old look's fonts go
  const settings = read('deck/static/app/13-settings.js');
  assert.match(settings, /f\.look\.value = /);
  const server = read('deck/api/pages.py');
  assert.match(server, /LOOKS = STATIC \/ "looks"/);
  assert.match(server, /\*LOOKS\.iterdir\(\)/); // a changed look sheet changes the assets version
});

test('the base sheet keeps colour out of its rules: tokens and washes only, no theme override block', () => {
  const rules = base.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').filter((l) => !/^:root|^\s*--|^\.lookcard\.l-/.test(l)); // the Look picker's swatches are pictures
  const hex = rules.join('\n').match(/#[0-9a-f]{6}\b(?![0-9a-f])/gi) || [];
  // the few that stay: the agents' key colours, diff colours, a Word page's paper
  assert.ok(hex.length <= 14, `hard-coded colours in style.css: ${hex.join(' ')}`);
  assert.doesNotMatch(base, /:root\[data-theme=light\] \{\n  &/);
  assert.doesNotMatch(base, /--screen\b/); // the old names
});

test('no look caps the chat text at a character count: text spreads as wide as the chat in every look', () => {
  for (const look of LOOKS) {
    const css = read(`deck/static/looks/${look}.css`);
    assert.doesNotMatch(css, /max-width:[^;}]*\d+ch\b/, `${look} caps a width in ch`);
  }
});

test('UI size zooms the page; viewport lengths divide by the zoom, the terminal is drawn unzoomed', () => {
  assert.match(base, /html \{ zoom: var\(--uizoom, 1\); \}/);
  assert.match(base, /\.termhost \{ zoom: calc\(1 \/ var\(--uizoom, 1\)\); \}/);
  for (const look of ['', ...LOOKS]) {
    const css = look ? read(`deck/static/looks/${look}.css`) : base;
    assert.doesNotMatch(css.replace(/100(dvh|vw) \/ var\(--uizoom, 1\)/g, ''), /100(dvh|vh|vw)\b/, `${look || 'style.css'}: a viewport length not divided by the zoom`);
  }
  const head = html.slice(0, html.indexOf('</head>'));
  assert.match(head, /"pcui" : "ui"/); // applied before the first paint
  const prefs = read('deck/static/app/02-prefs.js');
  assert.match(prefs, /sizeRow\("UI size"/); assert.match(prefs, /sizeRow\("UI text"/); assert.match(prefs, /sizeRow\("Text size"/);
});

test('the message box and the user\'s messages follow Text size, not UI text, in every look', () => {
  // bug report 2026-11-15: "user text in chat box should be text, not UI text size"
  for (const look of ['', ...LOOKS]) {
    const css = look ? read(`deck/static/looks/${look}.css`) : base;
    for (const m of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
      const sel = m[1].split(',').filter((x) => /#msg\b|\.cmsg\.user|\.csent/.test(x) && !/\.cts\b|button|\.gframe/.test(x));
      if (sel.length) assert.doesNotMatch(m[2], /--uitext/, `${look || 'style.css'}: ${sel.join(',').trim()}`);
    }
  }
});

test('UI text scales every fixed font size in every look, and applies before the first paint', () => {
  for (const look of ['', ...LOOKS]) {
    const css = look ? read(`deck/static/looks/${look}.css`) : base;
    for (const m of css.matchAll(/(?<![-\w])(font(?:-size)?|--label)\s*:\s*([^;}]*)/g)) if (!m[2].includes('--chatfont'))
      assert.doesNotMatch(m[2].replace(/calc\([^()]*var\(--uitext, 1\)\)/g, '').replace(/var\(--chatfont, \d+px\)/g, ''), /\d px\b|\dpx\b/, `${look || 'style.css'}: ${m[0]}`);
  }
  assert.match(html.slice(0, html.indexOf('</head>')), /"pcuitext" : "uitext"/);
});
