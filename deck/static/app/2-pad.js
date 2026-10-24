// ---- Deck: pad ----
async function loadButtons() {
  buttons = await api("GET", "/api/buttons");
  renderPad();
  applyScriptShut(); // the Scripts bar shows once one exists
}

// Newest live (else newest) run for a key.
function runFor(b) {
  const mine = sessions.filter((s) => s.button === b.id);
  return mine.find((s) => s.running) || null;
}

// Pad order (ids; saved per Deck in settings). Built-ins: settings, add.
let padOrder = [], padBusy = false;

// The Pad's keys: a column left of the screen (#padcol), on every screen,
// which opens over the screen's edge rather than squeezing it (a terminal resized mid-session redraws badly).
$("#padslot").append($("#pad"));
let padOpen = false;
try { padOpen = localStorage.getItem("deck-padcol") === "open"; } catch { /* default: shut */ }
$("#padcol").classList.toggle("shut", !padOpen);
$("#padhead").onclick = () => {
  padOpen = !padOpen;
  try { localStorage.setItem("deck-padcol", padOpen ? "open" : "shut"); } catch { /* this visit only */ }
  $("#padcol").classList.toggle("shut", !padOpen);
};
// Phones: a tap on the column's empty space (not a tab or key) opens or shuts it. On a PC a stray click there does
// nothing (user), like the shelf: its header still opens and shuts it.
$("#padcol").addEventListener("click", (e) => {
  if (wide.matches) return;
  if ([$("#padcol"), $("#padslot"), $("#pad"), $("#chips")].includes(e.target)) $("#padhead").click();
});

// Shut, the tab trash is just its can: a tap opens the column with the trash open
$("#tabtrash > summary").addEventListener("click", (e) => {
  if (!$("#padcol").classList.contains("shut")) return;
  e.preventDefault();
  $("#padhead").click();
  $("#tabtrash").open = true;
});
$("#padadd").onclick = () => openEdit(null);
// The scripts half folds away (user), giving the tabs the column; shared, like the tab folders
function applyScriptShut() {
  $("#padcol").classList.toggle("scriptshut", !!prefs.scriptshut);
  $("#padfold").setAttribute("aria-expanded", String(!prefs.scriptshut));
  // the bar and its scripts show only when switched on (Settings → Advanced) or a script exists (user)
  $("#padcol").classList.toggle("noscripts", !prefs.scripts && !buttons.length);
}
$("#padfold").onclick = () => { saveState({ scriptshut: !prefs.scriptshut }); applyScriptShut(); };
function renderPad() {
  if (padBusy) return; // a key is picked up: don't rebuild under the finger
  // Saved keys (Add script: the column's bottom; Settings: the host name at the top). A saved order wins; keys it
  // doesn't know yet (new ones) go after their default predecessor.
  const cells = buttons.map(key);
  const byId = new Map(cells.map((c) => [c.dataset.id, c]));
  const out = padOrder.filter((id) => byId.has(id)).map((id) => byId.get(id));
  cells.forEach((c, i) => {
    if (out.includes(c)) return;
    const prev = cells.slice(0, i).reverse().find((p) => out.includes(p));
    out.splice(prev ? out.indexOf(prev) + 1 : 0, 0, c);
  });
  $("#pad").replaceChildren(...out);
}

// Pad key gestures: tap; hold (450 ms) picks the key up: release in place = onHold (edit / sheet),
// drag = move it on the Pad (order saved for this Deck).
function holdable(cell, onTap, onHold) {
  const el = cell.querySelector(".key");
  let timer, x0, y0, lifted = false, dragged = false, cancelled = false;
  const drop = () => { clearTimeout(timer); cell.classList.remove("lifted"); padBusy = false; };
  el.addEventListener("pointerdown", (e) => {
    x0 = e.clientX; y0 = e.clientY; lifted = dragged = cancelled = false;
    timer = setTimeout(() => {
      lifted = true; padBusy = true; navigator.vibrate?.(25);
      cell.classList.add("lifted");
      try { el.setPointerCapture(e.pointerId); } catch { /* touch already captures */ }
    }, 450);
  });
  el.addEventListener("pointermove", (e) => {
    const far = Math.hypot(e.clientX - x0, e.clientY - y0) > 10;
    if (!lifted) { if (far) { clearTimeout(timer); cancelled = true; } return; } // a scroll, not a press
    if (!dragged && !far) return;
    dragged = true;
    const over = document.elementFromPoint(e.clientX, e.clientY)?.closest("#pad .cell");
    if (!over || over === cell) return;
    const kids = [...$("#pad").children];
    $("#pad").insertBefore(cell, kids.indexOf(over) < kids.indexOf(cell) ? over : over.nextSibling);
  });
  // Once a key is picked up, the finger moves it instead of scrolling the page.
  el.addEventListener("touchmove", (e) => { if (lifted) e.preventDefault(); }, { passive: false });
  el.addEventListener("pointerup", () => {
    const was = { lifted, dragged };
    drop();
    if (was.dragged) savePadOrder();
    else if (was.lifted) onHold();
  });
  el.addEventListener("pointercancel", () => { drop(); if (dragged) savePadOrder(); cancelled = true; });
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  el.addEventListener("click", () => { if (!lifted && !cancelled) onTap(); });
}

async function savePadOrder() {
  padOrder = [...$("#pad").children].map((c) => c.dataset.id);
  try { await api("PUT", "/api/settings", { pad: padOrder }); }
  catch (e) { toast(e.message); }
}

function key(b) {
  const run = runFor(b);
  const st = run?.state;
  // A row like a session tab (user): its icon, name and run clock, all one button.
  const el = h("button", { class: `key chip t-${b.type}${run ? " lit" : ""}${st === "needs_you" ? " needs" : ""}`, "aria-label": b.label },
    h("span", { class: "kicon" }, icon(b.type)),
    h("span", { class: "klabel" }, b.label),
    h("span", { class: `kstate${run ? "" : " off"}`, "data-clock": run && st !== "needs_you" ? run.created : null },
      run ? (st === "needs_you" ? "REQ" : clock(run.created)) : "00:00"));
  const cell = h("div", { class: "cell", "data-id": b.id }, el);
  holdable(cell, () => press(b, el), () => openEdit(b));
  cell.addEventListener("contextmenu", (e) => { // PC: right-click menu with Delete (user)
    if (window.matchMedia("(pointer: coarse)").matches) return;
    e.preventDefault();
    scriptMenu(b, e.clientX, e.clientY);
  });
  return cell;
}

async function press(b, el) {
  navigator.vibrate?.(12);
  el.classList.add("pressed");
  setTimeout(() => el.classList.remove("pressed"), 350);
  const run = runFor(b);
  if (run) return b.type === "shell" ? showRun(run.name, b.label) : openSession(run.name);
  // Agent keys without a saved prompt ask what to do first; so do script keys whose command takes "$1".
  if ((AGENT_NAME[b.type] && !b.text) || asksArg(b)) return openAsk(b);
  await launch(b);
}

async function launch(b, overrides) {
  try {
    // A key set to run on another host goes there whole, through that host's /peer proxy (its own pad may lack it)
    const away = b.host && b.host !== HOME_HOST;
    const body = { ...b, ...Object.fromEntries(Object.entries(overrides || {}).filter(([k, v]) => k === "yolo" || v)) };
    const { session } = away ? await api("POST", `/peer/${encodeURIComponent(b.host)}/api/run`, body)
      .then((r) => ({ session: `${b.host}/${r.session}` })) : await api("POST", `/api/run/${b.id}`, overrides);
    await poll();
    // Agents want a conversation; a script shows its output in a window over the page (user)
    if (b.type !== "shell") openSession(session);
    else showRun(fullName(session), b.label);
  } catch (e) { toast(e.message); }
}

// A script's output, live: its pane's text, then how it ended. Stop ends it; Open in terminal shows it as a tab.
let runPoll = 0, runName = "";
function showRun(name, label) {
  runName = name;
  $("#runtitle").textContent = label || realName(name);
  $("#runstate").textContent = "running";
  $("#runtext").textContent = "";
  $("#runstop").hidden = false;
  if (!$("#runout").open) $("#runout").showModal();
  clearInterval(runPoll);
  const tick = async () => {
    if (!$("#runout").open || runName !== name) return clearInterval(runPoll);
    const box = $("#runtext"), atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
    let { text } = await api("GET", `/api/sessions/${encodeURIComponent(name)}/text`).catch(() => ({ text: null }));
    // the screen's empty rows, and tmux's own "Pane is dead" line (the state above says how it ended)
    if (text !== null) text = text.replace(/\n*Pane is dead[^\n]*\s*$/, "").replace(/\n{3,}/g, "\n\n").trim();
    if (text !== null && text !== box.textContent) { box.textContent = text; if (atEnd) box.scrollTop = box.scrollHeight; }
    await poll().catch(() => {});
    const s = sessions.find((x) => x.name === name);
    if (!s || !s.running) { // done (or closed): its exit, and nothing more to follow
      $("#runstate").textContent = !s ? "closed" : s.exit === "0" || s.exit === 0 ? "done" : `exited ${s.exit ?? ""}`.trim();
      $("#runstop").hidden = true;
      clearInterval(runPoll);
    }
  };
  tick();
  runPoll = setInterval(tick, 1000);
}
$("#runclose").onclick = () => $("#runout").close();
$("#runopen").onclick = () => { $("#runout").close(); openSession(runName); };
$("#runstop").onclick = async () => {
  await api("DELETE", `/api/sessions/${encodeURIComponent(runName)}`).catch((e) => toast(e.message));
  $("#runstate").textContent = "stopped"; $("#runstop").hidden = true; clearInterval(runPoll); poll();
};
