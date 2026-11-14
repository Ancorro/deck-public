const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

// Settings reads as one kind of thing (user 2026-11-10: "make settings menu consistant in deck"): every control is a
// .setrow, every hint a small.sethint, and every "Settings → ..." in the code and docs names a place that exists.
const root = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const html = read('deck/static/index.html');
const css = read('deck/static/style.css');
const dialog = (id) => html.slice(html.indexOf(`<dialog id="${id}">`), html.indexOf('</dialog>', html.indexOf(`<dialog id="${id}">`)));
const form = html.slice(html.indexOf('<form id="setform">'), html.indexOf('</form>', html.indexOf('<form id="setform">')));
const text = (s) => s.replace(/<small[\s\S]*?<\/small>|<datalist[\s\S]*?<\/datalist>|<option[^>]*>[^<]*/g, '')
  .replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();

test('every control in Settings and Screen settings sits in a .setrow, and no row uses the old classes', () => {
  for (const [id, body] of [['settings', form], ['screensettings', dialog('screensettings')]]) {
    assert.doesNotMatch(body, /class="[^"]*\b(yolo|effort|setname|setnamehint)\b/, `${id}: an old row class`);
    // a switch, a text field or a select is the direct control of a row (the Look, Theme and Shelf choices are inside one)
    for (const m of body.matchAll(/<(input|select)\b[^>]*>/g)) {
      if (/type="(radio|color)"/.test(m[0])) continue;
      const before = body.slice(0, m.index), open = before.lastIndexOf('<label'), close = before.lastIndexOf('</label>');
      assert.ok(open > close && /^<label class="setrow\b/.test(before.slice(open)), `${id}: ${m[0]} is not inside a .setrow label`);
    }
    // a hint is a small.sethint (the one note under Screen settings' rows, and the one-host note, are paragraphs)
    for (const m of body.matchAll(/class="([^"]*\bsethint\b[^"]*)"/g)) assert.equal(m[1], 'sethint', `${id}: hint class ${m[1]}`);
    // a bare label is only a choice card inside a row's set of choices (Look, Theme, Shelf)
    assert.ok(!/<label>(?!<input type="radio")/.test(body), `${id}: a bare label (a heading or a field outside the row kind)`);
  }
  assert.match(html, /<label class="setrow" id="setfailover" hidden><input type="checkbox" name="appfailover">/); // 14-start.js
  assert.match(form, /<label class="setrow stack">Text size [\s\S]*?<input type="range" name="font"[^>]*><\/label>/); // the slider's row reads touches
});

test('the row rules apply to both dialogs, switches included, and every look restyles rows the same way', () => {
  assert.match(css, /:is\(#settings, #screensettings\) \.setrow \{ display: grid;/);
  assert.match(css, /:is\(#settings, #screensettings\) \.setrow input\[type=checkbox\] \{ appearance: none;/);
  assert.match(css, /#settings \.setrow\.deskonly \{ display: none; \}/); // Resizable session pane: PC only
  for (const look of ['pad', 'rack', 'blueprint', 'zine', 'plain', 'message']) {
    const sheet = read(`deck/static/looks/${look}.css`);
    assert.doesNotMatch(sheet, /label\.yolo|#setlinks|#sethostlinks/, `${look}: an old Settings selector`);
    for (const m of sheet.matchAll(/^[^{\n]*\.setrow[^{]*\{/gm)) assert.match(m[0], /:is\(#settings, #screensettings\) \.setrow|\.setrow\.link/, `${look}: ${m[0]}`);
  }
});

// The places Settings has, from the page itself: tabs, their sections, and each section's rows and sub-headings.
// A tab with one section (Appearance, Hosts) does not repeat it: Settings → Appearance → Look.
function places() {
  const out = new Set(['Settings']);
  const add = (...parts) => { for (let i = 1; i <= parts.length; i++) out.add(['Settings', ...parts.slice(0, i)].join(' → ')); };
  for (const m of form.matchAll(/<(section|details) class="setsec" data-sec="(\w+)"[\s\S]*?<\/\1>/g)) {
    const sec = m[0], key = m[2], name = text(sec.match(/class="setgroup">([^<]+)</)[1]);
    const tab = key === 'look' ? 'Appearance' : key === 'hosts' ? 'Hosts' : 'General';
    const at = tab === name ? [tab] : [tab, name];
    add(...at);
    // a row's name: its text up to its hint or wide control (a switch's input comes first and is dropped)
    for (const r of sec.matchAll(/<(?:label|div|button|a|h4)\b[^>]*class="(?:setrow|setsub)[^"]*"[^>]*>([\s\S]*?)(?=<(?:small|select|div|span|output|nav)\b|<\/(?:label|div|button|a|h4)>)/g))
      add(...at, text(r[1]));
  }
  // what the rows open: Devices → Pair a device; PCs → Link a PC, Link to a PC, and each linked PC's Remove and Leave
  const under = (row, names) => { for (const base of [...out].filter((p) => p.endsWith(` → ${row}`))) for (const n of names) out.add(`${base} → ${n}`); };
  under('Devices', [...dialog('devices').matchAll(/<button[^>]*>([^<]+)<\/button>/g)].map((b) => b[1]));
  under('PCs', [...dialog('links').matchAll(/<button[^>]*>([^<]+)<\/button>/g)].map((b) => b[1]).concat(['Remove', 'Leave']));
  return out;
}
// tracked and new files alike (an untracked file can't hide a stale path); not the changelog, which records the past,
// nor deck/win (Windows has a Settings app of its own), nor this file, whose patterns spell the paths it looks for
const tracked = () => execFileSync('git', ['ls-files', '-co', '--exclude-standard'], { cwd: root, encoding: 'utf8' }).split('\n')
  .filter((f) => /\.(md|html|js|cjs|css|py|java|sh|ps1|go)$/.test(f) && f !== 'CHANGELOG.md' && !f.startsWith('deck/win/')
    && f !== 'tests/settings_consistency.test.cjs');

test('every "Settings → ..." in the code and docs names a place that exists, spelled with →', () => {
  const known = places();
  const kids = (cur) => [...known].filter((p) => p.startsWith(`${cur} → `) && !p.slice(cur.length + 3).includes(' → ')).map((p) => p.slice(cur.length + 3));
  const bad = [];
  for (const f of tracked()) {
    const lines = read(f).replace(/\*\*/g, '').split('\n');
    lines.forEach((line, i) => {
      const both = `${line} ${(lines[i + 1] || '').replace(/^\s*(\/\/|#|\*|<!--)?\s*/, '')}`; // a mention may wrap
      for (const m of line.matchAll(/\bSettings ?(→|->|>) /g)) {
        if (m[1] !== '→') { bad.push(`${f}:${i + 1}: "${both.slice(m.index, m.index + 40)}" spelled with ${m[1]}`); continue; }
        // walk it: each step must be one of the place's own names (the longest that fits), else it names nowhere
        let cur = 'Settings', rest = both.slice(m.index + 'Settings'.length), arrow;
        while ((arrow = rest.match(/^ ?→ ?/))) {
          rest = rest.slice(arrow[0].length);
          const step = kids(cur).filter((k) => rest.startsWith(k)).sort((x, y) => y.length - x.length)[0];
          if (!step) { bad.push(`${f}:${i + 1}: ${cur} → "${rest.slice(0, 30)}"`); break; }
          cur += ` → ${step}`; rest = rest.slice(step.length);
        }
      }
    });
  }
  assert.deepEqual(bad, [], `Settings paths that don't exist:\n${bad.join('\n')}`);
});

test('a path names Settings first: "General → More → PCs" alone is not used', () => {
  const bad = [];
  for (const f of tracked())
    for (const m of read(f).matchAll(/(?<!Settings → |→ )\b(General|Appearance) → /g)) bad.push(`${f}: ${read(f).slice(m.index, m.index + 40)}`);
  assert.deepEqual(bad, []);
});
