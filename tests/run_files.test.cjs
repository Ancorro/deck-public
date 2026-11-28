const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// A script key hands files to the device that pressed it (user 2026-11-23: "it should try to go to the client machine,
// (drive if cant)"): the key's window saves each offered file once, then tells Deck, which the script waits for.
const pad = fs.readFileSync(path.join(__dirname, '../deck/static/app/04-pad.js'), 'utf8');
const shelf = fs.readFileSync(path.join(__dirname, '../deck/static/app/10-shelf.js'), 'utf8');

test("the key's window downloads once and keeps a local save button after acknowledgement", async () => {
  assert.match(pad, /let \{ text, files \} = await api\("GET", `\/api\/sessions\/\$\{encodeURIComponent\(name\)\}\/text`\)/);
  assert.match(pad, /if \(files\) saveRunFiles\(name, files\);/);
  const name = 'other-pc/deck-test';
  const files = [{ file: 'clip 1.mp4' }];
  const requests = [], downloads = [], rows = [], written = [];
  const blob = new Blob(['video']);
  const source = pad.slice(pad.indexOf('async function saveRunFiles'), pad.indexOf('$("#runclose").onclick'));
  const saveRunFiles = vm.runInNewContext(source + '\nsaveRunFiles', {
    runName: name,
    runSaving: new Set(),
    $: (id) => id === '#runout' ? { open: true } : { append: (row) => rows.push(row) },
    h: (tag, attrs, ...children) => ({ tag, ...attrs, children, isConnected: true }),
    route: (url) => url,
    fetch: async (url) => {
      requests.push(['GET', url]);
      return { ok: true, blob: async () => blob };
    },
    saveFile: async (data, filename) => {
      downloads.push([data, filename]);
      return '';
    },
    api: async (...args) => requests.push(args),
    window: {
      showSaveFilePicker: async ({ suggestedName }) => {
        assert.equal(suggestedName, files[0].file);
        return {
          name: 'chosen.mp4',
          createWritable: async () => new WritableStream({ write: (chunk) => { written.push(Buffer.from(chunk)); } }),
        };
      },
    },
  });
  await saveRunFiles(name, files);
  await saveRunFiles(name, files);
  const url = '/api/sessions/other-pc%2Fdeck-test/files/clip%201.mp4';
  assert.deepEqual(requests, [['GET', url], ['POST', url + '/saved']]);
  assert.deepEqual(downloads, [[blob, 'clip 1.mp4']]);
  assert.equal(rows.length, 1);
  const [button, status] = rows[0].children;
  assert.equal(button.children[0], 'Save on this device');
  assert.equal(status.textContent, 'Check browser downloads for clip 1.mp4');
  await button.onclick();
  assert.equal(Buffer.concat(written).toString(), 'video');
  assert.equal(status.textContent, 'Saved chosen.mp4');
  assert.equal(requests.length, 2);
});

test('saving a file: Documents/Deck in the app, a download in a browser; the shelf uses the same', () => {
  assert.match(shelf, /async function saveFile\(blob, name\) \{\s*const fs = window\.Capacitor\?\.Plugins\?\.Filesystem;/);
  assert.match(shelf, /await fs\.writeFile\(\{ path: `Deck\/\$\{name\}`, data, directory: "DOCUMENTS", recursive: true \}\);/);
  assert.match(shelf, /const a = h\("a", \{ href: URL\.createObjectURL\(blob\), download: name \}\);/);
  assert.match(shelf, /await saveFile\(file \|\| new Blob\(\[f\.text\]/);
});

test("a run just started keeps its window polling until it is listed (a file it offers later still saves)", () => {
  assert.match(pad, /let seen = false, unseen = 0;/);
  assert.match(pad, /seen \|\|= !!s;\s*if \(s \? !s\.running : seen \|\| \+\+unseen > 10\)/);
});
