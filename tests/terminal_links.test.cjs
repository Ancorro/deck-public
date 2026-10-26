const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Links in the terminal are clickable (user 2026-10-24: "make links in terminal clickable").
const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
const term = fs.readFileSync(path.join(__dirname, '../deck/static/app/3-terminal.js'), 'utf8');

test('the web-links addon loads after xterm.js, pinned and with an integrity hash', () => {
  const xterm = html.indexOf('@xterm/xterm@5.5.0/lib/xterm.js');
  const links = html.search(/<script src="https:\/\/cdn\.jsdelivr\.net\/npm\/@xterm\/addon-web-links@0\.11\.0\/lib\/addon-web-links\.js" integrity="sha384-[A-Za-z0-9+/=]+" crossorigin="anonymous"><\/script>/);
  assert.ok(xterm > 0 && links > xterm);
});

test('a terminal link opens like a chat link: the Web pane when open, else a tab', () => {
  assert.match(term, /term\.loadAddon\(new WebLinksAddon\.WebLinksAddon\(\(ev, url\) => \{ ev\.preventDefault\(\); openLink\(url\); \}\)\)/);
  assert.match(term, /if \(window\.WebLinksAddon\)/); // a blocked CDN leaves the terminal working
});
