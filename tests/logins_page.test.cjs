const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

// Settings → General → Logins and the setup's login step (user 2026-11-08: "part of a set up sequence, not a script", "also in settings").
const html = fs.readFileSync(path.join(__dirname, '../deck/static/index.html'), 'utf8');
const settings = fs.readFileSync(path.join(__dirname, '../deck/static/app/13-settings.js'), 'utf8');
const start = fs.readFileSync(path.join(__dirname, '../deck/static/app/14-start.js'), 'utf8');

test('Settings has a Logins section and opening Settings fills it', () => {
  assert.match(html, /<section class="setsec" data-sec="logins"[^>]*>\s*<h3 class="setgroup">Logins<\/h3>[\s\S]*?<div id="setlogins"/);
  assert.match(settings, /renderLogins\(\$\("#setlogins"\)\);[^}]*?\$\("#settings"\)\.showModal\(\);/);
});

test('the setup asks for logins after choosing to host sessions, not for a client-only PC', () => {
  assert.match(html, /<dialog id="loginask">[\s\S]*?<div id="loginasklist"/);
  assert.match(start, /if \(!hosting\) \{[^}]*return; \}[\s\S]*?\$\("#loginask"\)\.showModal\(\);[\s\S]*?renderLogins\(\$\("#loginasklist"\), \{ fresh: true, only: HOME_HOST \}\)/);
});

test('Log in goes to the host that runs the tool and opens its tab once the column lists it', () => {
  assert.match(settings, /api\("POST", `\$\{hostApi\(host\)\}\/api\/logins\/\$\{t\.tool\}`\)/);
  assert.match(settings, /const hostApi = \(host\) => host === HOME_HOST \? "" : `\/peer\/\$\{encodeURIComponent\(host\)\}`;/);
  assert.match(settings, /!sessions\.some\(\(s\) => s\.name === fullName\(name\)\)[\s\S]*?openSession\(name\);/);
});

test('Settings tabs: Appearance first and opened by default; each tab shows only its own sections (user 2026-11-08)', () => {
  const css = fs.readFileSync(path.join(__dirname, '../deck/static/style.css'), 'utf8');
  assert.match(html, /<div id="settabs" class="settabs" role="tablist">\s*<button[^>]*data-tab="look"[^>]*>Appearance<\/button>\s*<button[^>]*data-tab="general"[^>]*>General<\/button>/);
  assert.match(css, /#setform\[data-tab=general\] \.setsec:is\(\[data-sec=look\], \[data-sec=hosts\]\), #setform\[data-tab=look\] \.setsec:not\(\[data-sec=look\]\)/);
  assert.match(settings, /function setTab\(tab\) \{[\s\S]*?localStorage\.setItem\("deck-settab", tab\)/);
  assert.match(settings, /\$\("#setform details\.setsec"\)\.open = wide\.matches;\s*let tab = "look";[\s\S]*?setTab\(tab\);/);
});

test('Hosts holds only multi-host settings; with one host it is greyed out and nothing is lost (user 2026-11-08)', () => {
  assert.match(html, /data-tab="hosts"[^>]*>Hosts<\/button>/);
  const from = html.indexOf('data-sec="hosts"'), hosts = html.slice(from, html.indexOf('</section>', from));
  for (const id of ['id="sethosts"', 'id="setorder"', 'name="appfailover"', 'id="linkbtn"', 'id="sethostsone"'])
    assert.ok(hosts.includes(id), id);
  // what a one-host Deck uses lives outside the Hosts tab
  for (const id of ['name="hosting"', 'name="sessionagents"', 'id="devbtn"']) assert.ok(!hosts.includes(id), id);
  const look = html.slice(html.indexOf('data-sec="look"'), html.indexOf('</section>', html.indexOf('data-sec="look"')));
  assert.ok(look.includes('id="setaccent"'));
  const more = html.slice(html.indexOf('data-sec="more"'), html.indexOf('</section>', html.indexOf('data-sec="more"')));
  assert.ok(more.includes('id="devbtn"') && more.includes('id="linkbtnone"'));
  assert.match(settings, /const oneHost = \(\) => hostList\.length < 2;/);
  assert.match(settings, /if \(one && tab === "hosts"\) tab = "look";/);
  assert.match(settings, /if \(b\.dataset\.tab === "hosts" && oneHost\(\)\) return toast\(/);
  // one host: the page's colour comes from `led` (settings.hosts lists no hosts), so the accent saves it too
  assert.match(settings, /saveSetting\(\{ led: c, hostnames: \{ \[me\.name\]: \{ alias: me\.aka \|\| "", led: c \} \} \}\)/);
});
