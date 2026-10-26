// ---- Terminal tab ----
// Each tab's last screen (plain text), shown at once when switching back while its live view loads (phones: the
// tunnel's round trips). Text mode only: its first frame redraws the whole screen anyway.
const lastScreens = new Map();
function snapshotScreen() {
  const b = term?.buffer.active;
  if (!b || !attached) return;
  const rows = [];
  for (let i = 0; i < term.rows; i++) rows.push(b.getLine(b.viewportY + i)?.translateToString(true) ?? "");
  lastScreens.set(attached, rows);
}
function openSession(name) {
  name = fullName(name); // a new session's name from its host's API is bare
  if (attached !== name) {
    if (term && textTerm() && !chatNames.has(name)) {
      // Keep the terminal (rebuilding it is slow on a phone); swap the connection and show the cached screen.
      snapshotScreen();
      clearTimeout(reconnectTimer);
      if (ws) { ws.onclose = null; ws.close(); ws = null; }
      term.reset();
      const rows = lastScreens.get(name);
      if (rows) term.write("\x1b[90m" + rows.join("\r\n").trimEnd() + "\x1b[0m");
    } else if (!(chatView && chatNames.has(name))) detach();
    // (chat to chat: attachChat parks the one on screen, and its stack stays in the page; emptying it here made the
    // browser lay out a long chat all over again on the way back, ~250 ms)
  }
  useDraft(name);
  attached = name;
  show("term");
  attach(name);
}

// Fallback when nothing is selected: the newest running session. With none the screen stays empty; it no longer opens
// a shell on the host (user): + opens one.
async function attachNewest() {
  try {
    await poll();
    const newest = sessions.filter((s) => s.running).sort((a, b) => b.created - a.created)[0];
    if (!newest) return;
    useDraft(newest.name); attached = newest.name; attach(newest.name); poll();
  }
  catch (e) { toast(e.message); }
}

let tabOrder = prefs.tabs || [], tabDrag = false;
function renderChips() {
  // Oldest first, so new sessions open at the bottom; + comes last.
  // Tabs the user dragged keep that order (shared with this Deck's other browsers); others follow, oldest first.
  if (tabDrag || renaming) return; // a poll mid-drag would put the dragged tab back; mid-rename, drop the typing
  const ranks = tabOrder.map(fullName);
  const rank = (s) => { const i = ranks.indexOf(s.name); return i < 0 ? Infinity : i; };
  const order = sessions.filter((s) => s.running || s.name === attached)
    .sort((a, b) => a.created - b.created).sort((a, b) => rank(a) - rank(b) || 0);
  // Folders (user): a header, then its tabs under it; a folder sits where its first tab would. Tabs gone leave it; an
  // empty folder goes.
  const folders = (prefs.tabfolders || []).map((f) => ({ ...f, tabs: order.filter((s) => f.tabs.includes(s.name)).map((s) => s.name) }))
    .filter((f) => f.tabs.length);
  const folderOf = new Map(folders.flatMap((f) => f.tabs.map((n) => [n, f])));
  // armed = same content (name and size unchanged), just recoloured red
  const cls = (s) => `chip c-${s.state || "idle"}${s.name === attached ? " on" : ""}${s.name === armedKill ? " armed" : ""}` +
    `${s.host !== HOME_HOST ? " remote" : ""}${s.offline ? " off" : ""}${folderOf.has(s.name) ? " infolder" : ""}`;
  // a folder's header: open or shut, and (shut) whether a tab in it is the open one or needs you
  const fcls = (f) => {
    const kids = order.filter((s) => f.tabs.includes(s.name));
    return `chip tabfolder${f.open ? " open" : ""}${kids.some((s) => s.name === attached) ? " hason" : ""}` +
      `${kids.some((s) => s.state === "needs_you") ? " c-needs_you" : ""}`;
  };
  const title = (s) => s.name === armedKill ? "Tap again to kill"
    : `${s.title || s.label}${s.host !== HOME_HOST ? ` · on ${s.host}${s.offline ? " (offline)" : ""}` : ""}`;
  const hostLed = (s) => hostList.find((x) => x.name === s.host)?.led || "var(--screen-dim)";
  // Same chips as before: restyle in place. Rebuilding every 2s poll swallowed taps that
  // landed mid-rebuild (touch started on the old button, ended on the new one).
  const key = order.map((s) => `${s.name}\t${s.label}\t${s.title || ""}\t${tint(s.name)}`).join("\n") +
    `\n${JSON.stringify(folders.map((f) => [f.id, f.title, f.open, f.tabs]))}\n${hostsSeen.map((x) => `${x.name}:${x.online}`).join(",")}|${hostList.map((x) => x.name).join(",")}|${prefs.hosting}`;
  const box = $("#chips");
  if (box.dataset.key === key) {
    for (const s of order) {
      const b = box.querySelector(`.chip[data-name="${CSS.escape(s.name)}"]`);
      if (b) { b.className = cls(s); b.title = title(s); }
    }
    for (const f of folders) { const b = box.querySelector(`.tabfolder[data-folder="${f.id}"]`); if (b) b.className = fcls(f); }
    return;
  }
  const added = box.dataset.key !== undefined && order.length > box.dataset.key.split("\n").filter(Boolean).length;
  box.dataset.key = key;
  const chip = (s) => h("button", {
    class: cls(s), title: title(s), "data-name": s.name, onclick: () => tapChip(s.name),
    style: `--tint: ${tint(s.name)}; --hostled: ${hostLed(s)}`,
  }, h("span", { class: "cicon tinted", title: s.label }, s.type === "terminal" ? "$" : icon(s.type)), h("span", { class: "cidle" }),
    // The logo says which agent and its colour which session (tint); the text is the title the program set (Claude names
    // its task), else its name.
    h("span", { class: "ctext" }, h("span", { class: "csub" }, h("span", {}, s.title || s.label))));
  const header = (f) => h("button", { class: fcls(f), "data-folder": f.id, title: `${f.title} (${f.tabs.length})`,
    onclick: () => saveFolders((prefs.tabfolders || []).map((x) => x.id === f.id ? { ...x, open: !x.open } : x)) },
    h("span", { class: "cicon fchev" }, "›"),
    h("span", { class: "ctext" }, h("span", { class: "fname" }, f.title), h("span", { class: "fcount" }, f.tabs.length)));
  // Units in tab order: a tab, or a folder with its tabs (a folder goes with the host of its first tab)
  const units = [], done = new Set();
  for (const s of order) {
    if (done.has(s.name)) continue;
    const f = folderOf.get(s.name);
    if (!f) { units.push({ host: s.host, els: [chip(s)] }); continue; }
    const els = [header(f)];
    for (const n of f.tabs) {
      done.add(n);
      const c = chip(order.find((x) => x.name === n));
      c.dataset.folder = f.id;
      c.hidden = !f.open; // shut: still here (the tab order counts it), unseen
      els.push(c);
    }
    units.push({ host: s.host, els });
  }
  // Divided by host (user), when Deck knows more than one: this host first, then the others as Settings lists them, each
  // under its name (and "offline" when it can't be reached) with its own + to start a session there (user); the tab
  // order holds within each
  const hostsHere = [...new Set([...(hostList.length > 1 ? hostList.map((x) => x.name) : []), ...units.map((u) => u.host)])];
  const items = [];
  if (hostsHere.length < 2) items.push(...units.flatMap((u) => u.els));
  else {
    const rank = (n) => n === HOME_HOST ? -1 : (i => i < 0 ? 999 : i)(hostList.findIndex((x) => x.name === n));
    for (const host of hostsHere.sort((a, b) => rank(a) - rank(b))) {
      const off = hostsSeen.find((x) => x.name === host)?.online === false;
      const x = hostList.find((y) => y.name === host);
      const shut = (prefs.hostshut || []).includes(host), mine = units.filter((u) => u.host === host);
      items.push(h("div", { class: `chiphost${host === HOME_HOST ? " here" : ""}${shut ? " shut" : ""}`, style: `--hostled: ${x?.led || "var(--screen-dim)"}` },
        h("button", { type: "button", class: "chfold", title: shut ? `Show ${host}'s sessions` : `Hide ${host}'s sessions`,
          "aria-expanded": String(!shut), onclick: () => toggleHost(host) },
          h("span", { class: "chled" }), h("span", { class: "chname" }, x?.aka || host),
          shut ? h("em", {}, String(mine.reduce((k, u) => k + u.els.filter((e) => e.dataset.name).length, 0))) : null,
          h("span", { class: "fchev" }, "›")),
        off ? h("em", {}, "offline") : null,
        off || (host === HOME_HOST && prefs.hosting === false) ? null : h("button", { class: "chnew", title: `New session on ${host}`,
          "aria-label": `New session on ${host}`, ...newPress(host) }, "+")));
      if (!shut) items.push(...mine.flatMap((u) => u.els)); // shut (user): only its header, with how many it has
    }
  }
  const arranging = box.classList.contains("arranging");
  box.replaceChildren(h("button", { class: `chip newchip${hostsHere.length > 1 ? " multi" : ""}`, "aria-label": arranging ? "Done arranging" : "New session",
    ...newPress("", () => box.classList.contains("arranging")) }, arranging ? "Done" : "+"), ...items);
  if (added) box.scrollTop = box.scrollHeight; // keep the new tab in view (+ sits at the top)
  // A title wider than the tab scrolls past like a ticker on touch screens (two copies loop seamlessly); a PC's mouse
  // finds it in the tooltip, so it just ends in "…" there.
  if (!window.matchMedia("(pointer: coarse)").matches) return;
  for (const sub of box.querySelectorAll(".csub")) {
    const t = sub.firstChild;
    if (t.scrollWidth <= sub.clientWidth) continue;
    t.append("\u2003\u2003" + t.textContent + "\u2003\u2003");
    t.style.animationDuration = `${t.scrollWidth / 2 / 12}s`; // ~12 px/s: slow enough to read
    sub.classList.add("tick");
  }
}

// A host's sessions in the tab column, hidden under its header or shown (shared, like the tab folders)
function toggleHost(host) {
  const shut = new Set(prefs.hostshut || []);
  if (!shut.delete(host)) shut.add(host);
  saveState({ hostshut: [...shut] });
  $("#chips").dataset.key = ""; // redraw
  renderChips();
}

// Folders of tabs (shared, like the tab order): the empty ones go, and tabs of sessions that are gone
function saveFolders(fs) {
  const live = new Set(sessions.map((s) => s.name));
  saveState({ tabfolders: fs.map((f) => ({ ...f, tabs: f.tabs.filter((n) => live.has(n)) })).filter((f) => f.tabs.length) });
  $("#chips").dataset.key = ""; // redraw
  renderChips();
}
// A folder's name: right-click its header (PC) or long-press it (touch) to rename it in place
// The box takes the whole header's place: inside the header's button it could not take focus, so no typing (user)
function renameFolder(head) {
  if (renaming) return;
  const f = (prefs.tabfolders || []).find((x) => x.id === head.dataset.folder);
  if (!f) return;
  renaming = true;
  const input = h("input", { class: "tabname foldername", value: f.title, "aria-label": "Folder name", maxlength: "40" });
  head.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true; renaming = false;
    saveFolders((prefs.tabfolders || []).map((x) => x.id === f.id && save ? { ...x, title: input.value.trim() || "Folder" } : x));
  };
  input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); });
  input.addEventListener("blur", () => finish(true));
  for (const ev of ["click", "pointerdown"]) input.addEventListener(ev, (e) => e.stopPropagation());
}
$("#chips").addEventListener("contextmenu", (e) => { // a folder's header: a menu with Rename, like a tab's
  const head = e.target.closest(".tabfolder");
  if (!head) return;
  e.preventDefault();
  if (window.matchMedia("(pointer: coarse)").matches) return renameFolder(head);
  popMenu(e.clientX, e.clientY, (item) => [
    item("Rename", () => renameFolder(head)),
    item("Ungroup", () => saveFolders((prefs.tabfolders || []).filter((x) => x.id !== head.dataset.folder)))]);
});

// Arrange mode (touch): a long press on a tab starts it; the tabs wiggle, any drag moves one at once (no hold), taps
// don't open sessions, and the + reads Done, which ends it. One gesture of hold-then-drag needs no mode, where the
// device delivers it.
function arrangeTabs(on) {
  const box = $("#chips");
  box.classList.toggle("arranging", on);
  const done = box.querySelector(".newchip");
  if (done) { done.textContent = on ? "Done" : "+"; done.setAttribute("aria-label", on ? "Done arranging" : "New session"); }
}

// Drag a tab to move it: mouse drags straight away; touch holds 400 ms first (a plain swipe scrolls the column), or, in
// arrange mode, drags at once.
(() => {
  const box = $("#chips");
  let st = null, eat = false;
  const end = () => {
    if (!st) return;
    clearTimeout(st.timer);
    st.c.classList.remove("lifted");
    if (st.live && st.pointerType !== "mouse") eat = true; // long press: no tap afterwards
    if (st.held && !st.moved) arrangeTabs(true); // held and let go without moving: arrange mode
    if (st.moved) {
      eat = true;
      box.querySelector(".dropinto")?.classList.remove("dropinto");
      const name = st.c.dataset.name, into = st.into;
      let fs = (prefs.tabfolders || []).map((f) => ({ ...f, tabs: f.tabs.filter((n) => n !== name) }));
      if (into?.classList.contains("tabfolder")) { // onto a folder's header: into it, at its end
        box.insertBefore(st.c, [...box.querySelectorAll(`.chip[data-folder="${into.dataset.folder}"]`)].pop()?.nextSibling || into.nextSibling);
        fs = fs.map((f) => f.id === into.dataset.folder ? { ...f, tabs: [...f.tabs, name] } : f);
      } else if (into) { // onto a tab: into its folder, or a new folder of the two
        box.insertBefore(st.c, into.nextSibling);
        const fid = into.dataset.folder;
        fs = fid ? fs.map((f) => f.id === fid ? { ...f, tabs: [...f.tabs, name] } : f)
          : [...fs, { id: `f${Date.now().toString(36)}`, title: "Folder", open: true, tabs: [into.dataset.name, name] }];
      } else { // between tabs: it joins the folder of what's just above it (an open folder's header or one of its tabs)
        let prev = st.c.previousElementSibling;
        while (prev?.hidden) prev = prev.previousElementSibling;
        const fid = prev?.classList.contains("tabfolder") ? (prev.classList.contains("open") ? prev.dataset.folder : "") : prev?.dataset.folder;
        if (fid) fs = fs.map((f) => f.id === fid ? { ...f, tabs: [...f.tabs, name] } : f);
      }
      tabOrder = [...box.querySelectorAll(".chip[data-name]")].map((c) => c.dataset.name);
      saveState({ tabs: tabOrder });
      saveFolders(fs);
    }
    st = null; tabDrag = false; renderChips();
  };
  box.addEventListener("pointerdown", (e) => {
    eat = false; // a new press: its own tap
    const c = e.target.closest(".chip[data-name]");
    if (!c || e.button > 0) return;
    const arranging = box.classList.contains("arranging");
    st = { c, x: e.clientX, y: e.clientY, pointerType: e.pointerType, live: e.pointerType === "mouse" || arranging, moved: false };
    const s = st;
    if (arranging) eat = true; // arranging: a tap on a tab doesn't open it
    if (!st.live) st.timer = setTimeout(() => {
      s.held = true;
      st.live = true; tabDrag = true; c.classList.add("lifted"); navigator.vibrate?.(25);
      try { c.setPointerCapture(e.pointerId); } catch { /* touch already captures */ }
    }, 400);
  });
  box.addEventListener("pointermove", (e) => {
    if (!st) return;
    const far = Math.hypot(e.clientX - st.x, e.clientY - st.y) > 6;
    if (!st.live) { if (far) { clearTimeout(st.timer); st = null; } return; } // a scroll, not a press
    if (!st.moved) {
      if (!far) return;
      st.moved = tabDrag = true; st.c.classList.add("lifted");
      try { st.c.setPointerCapture(e.pointerId); } catch { /* fine */ }
    }
    const r = st.c.getBoundingClientRect();
    const over = document.elementFromPoint(r.left + r.width / 2, e.clientY)?.closest("#chips .chip[data-name], #chips .tabfolder");
    box.querySelector(".dropinto")?.classList.remove("dropinto");
    st.into = null;
    if (over && over !== st.c) {
      const o = over.getBoundingClientRect();
      // the middle of a tab: into a folder with it (a folder's header: into that folder); its ends: move it there
      if (e.clientY > o.top + o.height * .3 && e.clientY < o.bottom - o.height * .3) { st.into = over; over.classList.add("dropinto"); }
      else {
        box.insertBefore(st.c, e.clientY < o.top + o.height / 2 ? over : over.nextSibling);
        try { st.c.setPointerCapture(e.pointerId); } catch { /* moving it in the page let go of the pointer: take it back */ }
      }
    }
    const bx = box.getBoundingClientRect(); // near an end: scroll the column along
    if (e.clientY < bx.top + 24) box.scrollTop -= 12; else if (e.clientY > bx.bottom - 24) box.scrollTop += 12;
  });
  box.addEventListener("touchmove", (e) => { if (st?.live) e.preventDefault(); }, { passive: false });
  // the release anywhere: a tab moved in the page can lose the pointer, and the release then lands outside the list
  addEventListener("pointerup", end);
  addEventListener("pointercancel", end);
  box.addEventListener("contextmenu", (e) => {
    const chip = e.target.closest(".chip[data-name]");
    if (!chip) return;
    e.preventDefault();
    if (!window.matchMedia("(pointer: coarse)").matches) tabMenu(chip, e.clientX, e.clientY); // PC: right-click menu (user)
  });
  box.addEventListener("click", (e) => { if (eat) { eat = false; e.stopPropagation(); e.preventDefault(); } }, true);
})();

// Tapping the active chip arms it ("Kill?"); a second tap within 3s kills that session.
// Armed state lives here, not on the button, because polls re-render the chips.
let armedKill = null, armTimer = 0;
function tapChip(name) {
  const off = sessions.find((s) => s.name === name && s.offline);
  if (off) return off.copy ? showOffline(off) : toast(`${off.host} is offline, and there's no copy of this here`);
  if (name !== attached) {
    armedKill = null;
    if (padOpen && !wide.matches) $("#padhead").click(); // a phone: the column lies over the chat
    openSession(name);
    return;
  }
  clearTimeout(armTimer);
  if (armedKill === name) { armedKill = null; killCurrent(); return; }
  armedKill = name;
  armTimer = setTimeout(() => { armedKill = null; renderChips(); }, 3000);
  renderChips();
}

// An offline node's chat, read only from this node's copy of the site's state (deck/site.py); Transfer here carries it on here
async function showOffline(s) {
  $("#offtitle").textContent = s.title || s.label;
  $("#offstate").textContent = `${s.host} offline · read only`;
  $("#offtext").textContent = "Loading…";
  $("#offmove").onclick = () => { $("#offview").close(); askMoveHere(s); };
  $("#offview").showModal();
  try {
    const r = await api("GET", `/api/offline/${encodeURIComponent(s.host)}/${encodeURIComponent(realName(s.name))}/text`);
    $("#offtext").textContent = r.text || "(nothing said yet)";
    $("#offtext").scrollTop = $("#offtext").scrollHeight;
  } catch (e) { $("#offtext").textContent = e.message; }
}
$("#offclose").onclick = () => $("#offview").close();

// An offline node's chat: carry it on on this node from the state copy (deck/site.py move_here). Its node closes its own tab when back.
function askMoveHere(s) {
  $("#movehereq").textContent = `${s.host} is offline. Continue "${s.title || s.label}" on ${HOME_HOST} from its copy made ` +
    `${new Date(s.copy * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}? Anything ` +
    `said after that stays on ${s.host}, which closes its tab when it's back.`;
  $("#moveherego").onclick = async () => {
    $("#movehere").close();
    try {
      const r = await api("POST", `/api/offline/${encodeURIComponent(s.host)}/${encodeURIComponent(realName(s.name))}/move-here`);
      await poll(); openSession(r.session);
      toast(`Moved here from ${s.host}${r.warning ? `. ${r.warning}` : ""}`);
    } catch (e) { toast(e.message); }
  };
  $("#movehere").showModal();
}
$("#moveherex").onclick = () => $("#movehere").close();

// A tab's right-click menu (PC): Rename, Move host (agent chats), Close
function tabMenu(chip, x, y) {
  const s = sessions.find((v) => v.name === chip.dataset.name);
  if (!s) return;
  popMenu(x, y, (item) => [
    item("Rename", () => renameTab(chip)),
    s.offline ? null : item("Description", () => showDescription(s)),
    s.chat && AGENT_TYPES.includes(s.type) ? item("Move host", () => {
      if (s.name !== attached) openSession(s.name);
      $("#movesess").click();
    }) : null,
    item("Close", async () => {
      if (s.name === attached) return killCurrent();
      await api("DELETE", `/api/sessions/${encodeURIComponent(s.name)}`).catch((e) => toast(e.message));
      await poll(); loadTabTrash();
    })]);
}

// A script key's right-click menu (PC; touch: hold it for the editor, which has Delete too)
function scriptMenu(b, x, y) {
  popMenu(x, y, (item) => [
    item("Run", () => press(b, $(`#pad .cell[data-id="${b.id}"] .key`))),
    item("Edit", () => openEdit(b)),
    item("Delete", async (btn) => {
      if (!confirmTwice(btn)) return false; // stays open: click again to delete
      await api("DELETE", `/api/buttons/${b.id}`).catch((e) => toast(e.message));
      loadButtons();
    })]);
}

// A small menu at x, y: items(item) gives its buttons; a run returning false keeps the menu open
function popMenu(x, y, items) {
  $("#tabmenu")?.remove();
  const item = (text, run) => h("button", { type: "button", onclick: async (e) => {
    const btn = e.currentTarget;
    if (run.length && await run(btn) === false) return;
    menu.remove();
    if (!run.length) run();
  } }, text);
  const menu = h("div", { id: "tabmenu", class: "hpop tabmenu", role: "menu" }, ...items(item));
  document.body.append(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = `${Math.min(x, innerWidth - r.width - 8)}px`;
  menu.style.top = `${Math.min(y, innerHeight - r.height - 8)}px`;
  const away = (e) => {
    if (e.type === "keydown" ? e.key !== "Escape" : menu.contains(e.target)) return;
    menu.remove();
    for (const ev of ["pointerdown", "keydown"]) removeEventListener(ev, away, true);
  };
  for (const ev of ["pointerdown", "keydown"]) addEventListener(ev, away, true);
}

// The tab's description (deck/describe.py): Haiku rewrites it after each of the agent's turns
async function showDescription(s) {
  $("#desctitle").textContent = s.title || s.label;
  $("#desctext").textContent = "Loading…";
  $("#descbox").showModal();
  try {
    const d = await api("GET", `/api/sessions/${encodeURIComponent(s.name)}/describe`);
    $("#desctext").textContent = d.text ? `${d.text} (${new Date(d.at * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })})`
      : prefs.describe ? "No description yet: one is written after the agent's next turn." : "Session descriptions are off (Settings).";
  } catch (e) { $("#desctext").textContent = e.message; }
}
$("#descx").onclick = () => $("#descbox").close();

// Rename a tab in place: Enter or leaving the box saves, Escape keeps the old name, empty goes back to the title its
// program sets
let renaming = false;
function renameTab(chip) {
  if (renaming) return;
  const s = sessions.find((x) => x.name === chip.dataset.name);
  const sub = chip.querySelector(".csub");
  if (!s || !sub) return;
  renaming = true;
  const input = h("input", { class: "tabname", value: s.title || s.label, "aria-label": "Tab name", maxlength: "80" });
  sub.replaceChildren(input);
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    if (save && input.value.trim() !== (s.title || s.label)) {
      await api("POST", `/api/sessions/${encodeURIComponent(s.name)}/rename`, { title: input.value }).catch((e) => toast(e.message));
    }
    renaming = false;
    $("#chips").dataset.key = ""; // redraw with the new name
    await poll();
  };
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  for (const ev of ["click", "pointerdown"]) input.addEventListener(ev, (e) => e.stopPropagation());
}

// "+" chip: pick a blank terminal or a blank agent session; a host header's + starts it on that host. Only agents set up
// on that host are offered (its settings' `agents`). Settings → New sessions → Choose agent each time off (`agentpick`)
// skips the picker and starts Default agent (`agent`) there, falling back to the picker where it isn't set up (user).
// `pick`: holding + always opens the picker.
let newHost = "", newSessionRequest = 0;
async function startOn(path, body) {
  if (!newHost || newHost === HOME_HOST) return (await api("POST", path, body)).session;
  return `${newHost}/${(await api("POST", `/peer/${encodeURIComponent(newHost)}${path}`, body)).session}`;
}
async function startAgent(type) {
  try { const session = await startOn("/api/agent", { agent: type }); await poll(); openSession(session); }
  catch (e) { toast(e.message); }
}
// A + button's handlers: a tap starts New session (or Default agent, see openNew); holding it, or right-clicking on a
// PC, always opens the picker (user). `arranging` (the column's +): a tap there ends arrange mode instead.
function newPress(host, arranging = () => false) {
  let timer = 0, held = false;
  const cancel = () => clearTimeout(timer);
  return {
    onpointerdown: (e) => {
      held = false; cancel();
      if (e.button > 0 || arranging()) return;
      const x = e.clientX, y = e.clientY;
      timer = setTimeout(() => { held = true; navigator.vibrate?.(25); openNew(host, true); }, 450);
      e.currentTarget.onpointermove = (m) => { if (Math.hypot(m.clientX - x, m.clientY - y) > 8) cancel(); };
    },
    onpointerup: cancel, onpointercancel: cancel, onpointerleave: cancel,
    oncontextmenu: (e) => { e.preventDefault(); if (!held && !arranging()) openNew(host, true); held = true; },
    onclick: () => { if (held) { held = false; return; } if (arranging()) arrangeTabs(false); else openNew(host); },
  };
}
async function openNew(host = "", pick = false) {
  newHost = host;
  const request = ++newSessionRequest;
  const dlg = $("#newsess");
  const remote = host && host !== HOME_HOST;
  const straight = !pick && prefs.agentpick === false; // Choose agent each time, off: + starts the Default agent
  if (straight && !remote && agentsOn(prefs).includes(prefs.agent)) return startAgent(prefs.agent);
  dlg.querySelector("h2").textContent = host ? `New session on ${host}` : "New session";
  $("#newlist").replaceChildren(h("p", {}, "Loading agents…"));
  if (!straight) dlg.showModal(); // straight: no dialog flashes while the other host answers (user); only if it must
  let available = prefs;
  if (remote) {
    try { available = await api("GET", `/peer/${encodeURIComponent(host)}/api/settings`); }
    catch (e) {
      if (request !== newSessionRequest) return;
      if (straight) return toast(e.message);
      if (dlg.open) $("#newlist").replaceChildren(h("p", {}, e.message));
      return;
    }
  }
  if (request !== newSessionRequest || (!straight && !dlg.open)) return;
  const agents = agentsOn(available);
  if (straight && agents.includes(prefs.agent)) { if (dlg.open) dlg.close(); return startAgent(prefs.agent); }
  if (!dlg.open) dlg.showModal(); // the Default agent isn't set up there: choose
  const row = (glyph, label, run) => h("button", { class: "hostrow", onclick: () => { dlg.close(); return run(); } },
    h("span", { class: "cicon" }, glyph), h("b", {}, label));
  $("#newlist").replaceChildren(row("$", "Terminal", newShell),
    ...agents.map((a) => row(icon(a), AGENT_NAME[a], () => startAgent(a))),
    ...(agents.length ? [] : [h("p", {}, "No agents are set up on this host yet. Install Claude Code, Codex, OpenCode or Pi, then reopen.")]));
}
$("#newclose").onclick = () => $("#newsess").close();

async function newShell() {
  try {
    const session = await startOn("/api/shell", { new: true });
    await poll();
    openSession(session);
  } catch (e) { toast(e.message); }
}

function attach(name) {
  name = fullName(name); // saved before names carried their host
  if (prefs[sessionKey()] !== name) saveState({ [sessionKey()]: name });
  if (chatNames.has(name)) { attachChat(name); renderChips(); return; }
  if (chatView) detach();
  if (term) { connectTerminal(name); return; }
  term = new Terminal({
    fontSize: font(), fontFamily: "'IBM Plex Mono', 'Noto Sans Symbols 2', ui-monospace, monospace", cursorBlink: true, scrollback: 5000,
    theme: termTheme(),
  });
  fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  // URLs in the output are links: a click opens them like a chat link (the Web pane when it's open, else a tab)
  if (window.WebLinksAddon) term.loadAddon(new WebLinksAddon.WebLinksAddon((ev, url) => { ev.preventDefault(); openLink(url); }));
  const host = h("div", { class: "termhost" });
  $("#term").replaceChildren(host);
  term.open(host);
  // Phone keyboards replace whole words/composition buffers. A live terminal
  // cannot apply those edits reliably; compose in a normal textarea instead.
  setTermTyping();
  phoneTyping(host);
  fit.fit();
  connectTerminal(name);
  // Agents (Claude) turn on mouse reporting; on a touch screen every tap and swipe would reach them as clicks at
  // that spot (and cancel the scroll). Phones scroll with swipes (below) and type in the message box, so drop them.
  const coarse = window.matchMedia("(pointer: coarse)").matches;
  const MOUSE = /^(\x1b\[<\d+;\d+;\d+[Mm]|\x1b\[M[\s\S]{3})+$/;
  term.onData((d) => { if (!(coarse && MOUSE.test(d))) send(d); });
  // Text mode: the page never sees the program turn on mouse reporting, and xterm.js would turn the wheel into arrow
  // keys on its alternate screen; scroll on the host instead (tmux history, or wheel events for full-screen agents).
  term.attachCustomWheelEventHandler((ev) => {
    if (!textTerm()) return true;
    scrollBy(ev.deltaMode === 1 ? ev.deltaY : ev.deltaY / lineHeight());
    return false;
  });
  term.onWriteParsed(scheduleCrop);
  term.onResize(scheduleCrop);
  renderChips();
}

function setTermTyping() {
  const box = window.matchMedia("(pointer: coarse)").matches && prefs.composer !== false;
  term.textarea.readOnly = box;
  if (box) term.textarea.setAttribute("inputmode", "none"); else term.textarea.removeAttribute("inputmode");
}

// Message box off, phone keyboard typing straight into the terminal: autocorrect and suggestions rewrite words already
// typed (a composition), which xterm.js turns into doubled or lost text. Instead the page keeps the keyboard's own
// text and sends only the difference from what was sent before: backspaces for what changed, then the new letters.
// Enter, and keys the keyboard sends as plain key events (Backspace on an empty box), still go through xterm.js.
let typingReset = () => {}; // phoneTyping: the box's text no longer matches the line (quick keys, paste)
function phoneTyping(host) {
  const box = term.textarea;
  let sent = "";
  const on = () => window.matchMedia("(pointer: coarse)").matches && prefs.composer === false;
  const sync = () => {
    const now = box.value;
    let p = 0;
    while (p < sent.length && p < now.length && sent[p] === now[p]) p++;
    const d = "\x7f".repeat([...sent.slice(p)].length) + now.slice(p);
    if (d) send(d);
    sent = now;
  };
  const reset = () => { box.value = ""; sent = ""; };
  for (const type of ["compositionstart", "compositionupdate", "compositionend", "input"]) {
    host.addEventListener(type, (ev) => {
      if (!on() || ev.target !== box) return;
      ev.stopImmediatePropagation();
      // Some keyboards type Enter as a line break in the box: that's Enter for the terminal (a bare \n is a newline
      // inside an agent's input, not Submit).
      if (box.value.includes("\n")) { box.value = box.value.replace(/\n/g, ""); sync(); send("\r"); reset(); return; }
      if (type === "input" || type === "compositionend") sync();
    }, true);
  }
  host.addEventListener("keydown", (ev) => {
    if (!on() || ev.target !== box) return;
    if (ev.isComposing || ev.keyCode === 229 || (ev.key === "Backspace" && box.value)) { ev.stopImmediatePropagation(); return; }
    if (ev.key === "Enter") setTimeout(reset); // the line is done: start the next one from an empty box
  }, true);
  // xterm.js empties the box itself on blur, paste, Enter and ^C; a quick key changes the line behind its back.
  typingReset = () => { if (on()) reset(); };
  box.addEventListener("blur", typingReset);
  box.addEventListener("paste", () => setTimeout(typingReset));
}

// ---- agent footer: crop the agent's own input box + status lines, show them as a card instead ----
// Typing goes through the message box, so the agent's prompt box only takes space, and its status line is cut at the
// terminal's width. Covered: everything below the spinner/token line (or below the conversation when idle).
// Claude: from the input box's top rule (a ─── line right above "❯"; on Windows ">", and the rule may carry text). Codex: from its "›" prompt, when its footer
// (Context … / ? for shortcuts) follows. Menus (permission questions) never match, so they stay visible.
const SPINNER = /…\s*\(\d+[hms]|\(\d+[hms][^)]*esc to interrupt/; // Claude "Wibbling… (18s · …)", Codex "Working (5s • esc…"
function footerCrop(lines, kind) {
  const n = lines.length;
  let start = -1;
  for (let i = n - 1; i >= Math.max(1, n - 16) && start < 0; i--) {
    if (kind === "claude" && /^\s*[❯>]/.test(lines[i]) && /^\s*─{2,}/.test(lines[i - 1])) start = i - 1;
    if (kind === "codex" && /^\s*›/.test(lines[i])
      && lines.slice(i + 1, i + 5).some((l) => /Context \d+%|for shortcuts|context left/i.test(l))) start = i;
  }
  if (start < 0) return null;
  for (let k = start - 1; k >= Math.max(0, start - 6); k--) {
    if (SPINNER.test(lines[k])) { start = k + 1; break; }
  }
  const top = start;
  while (start > 0 && !lines[start - 1].trim()) start--; // blank rows above go too
  const gap = top - start; // a big gap = short conversation (Codex keeps its prompt at the bottom)
  // Card text: footer lines after the prompt box (status line, mode), minus rules, hints and the prompt itself.
  const tail = lines.slice(start).map((l) => l.trim())
    .filter((l) => l && !/^[─━]{2,}|^[❯›>]|for shortcuts|^⎿|Tip:/.test(l) && !SPINNER.test(l));
  return { start, gap, tail };
}

// Status chips kept short enough for one row on a phone: Claude and Codex wording, limits as % used.
function shortPart(p) {
  let m;
  if (/^Goal[^:]*: /.test(p)) return "Goal"; // the /goal in force: its words only on a PC
  if ((m = p.match(/^(?:ctx|Context) (\d+)% (used)?/i))) return `ctx ${m[1]}%`;
  if ((m = p.match(/^Context (\d+)% left/i))) return `ctx ${100 - m[1]}%`;
  if ((m = p.match(/^\$(\d+)(\.\d*)?$/))) return +m[1] >= 10 || !m[2] || m[2].length < 3 ? `$${m[1]}` : p; // "$42." = cut
  if ((m = p.match(/^(5h|five-hour|weekly|wk)\D*(\d+)% (left|used)?/i)))
    return `${/^(5h|five)/i.test(m[1]) ? "5h" : "wk"} ${m[3] === "left" ? 100 - m[2] : m[2]}%`;
  return p.replace(/\b(default)\b/, "").replace(/\bmedium\b/, "med").replace(/\s*\((\d+[KM]) context\)/i, " $1")
    .replace(/\s+/g, " ").trim();
}
function shortMode(m) {
  m = m.replace(/^[^\w]+/, "").replace(/\s*\(.*$/, "").replace(/\s+on$/, "");
  return { "bypass permissions": "bypass", "accept edits": "edits", "plan mode": "plan" }[m] || m;
}

let cropRaf = 0, extraRows = 0;
function scheduleCrop() { if (!cropRaf) cropRaf = requestAnimationFrame(() => { cropRaf = 0; applyCrop(); }); }
function applyCrop() {
  const hostEl = $("#term .termhost");
  if (!term || !hostEl) return;
  const s = sessions.find((x) => x.name === attached);
  const kind = s?.type;
  const buf = term.buffer.active;
  const lines = [];
  for (let i = 0; i < term.rows; i++) lines.push(buf.getLine(buf.viewportY + i)?.translateToString(true) ?? "");
  // tmux copy mode (scrolling a non-full-screen agent): its position "[12/1989]" sits top right. Keep the frame where
  // it was so the history slides under it instead of jumping as the footer scrolls out of view.
  if (/\[\d+\/\d+\]\s*$/.test(lines[0])) return;
  const crop = extraRows && buf.viewportY === buf.baseY ? footerCrop(lines, kind) : null;
  const xt = hostEl.querySelector(".xterm");
  const rowH = hostEl.querySelector(".xterm-screen").clientHeight / term.rows;
  // No crop (a menu, a shell, history): line the terminal's bottom up with the frame's; extra rows go off the top.
  // Crop: the last line that matters sits on the frame's bottom edge and the agent's footer falls below it
  // (the tab's extra rows fill the space it took, see sendSize).
  xt.style.transform = crop ? `translateY(${hostEl.clientHeight - crop.start * rowH}px)`
    : extraRows ? `translateY(${-extraRows * rowH}px)` : "";
  if (!crop) return; // the bar keeps its last chips while a menu is up
  // Uncut status parts from the agent's status line script when we have them; else the terminal's line.
  const mode = crop.tail.find((l) => /permissions|accept edits|plan mode|auto mode|full access/i.test(l));
  const statusLine = crop.tail.find((l) => l !== mode && l.includes("·"));
  const parts = (s.status?.length ? s.status : (statusLine || "").split(/\s+·\s+/))
    .map((p) => p.replace(/\s*…$/, "").trim()).filter(Boolean); // the terminal cuts its line with "…"
  const chips = [...parts.map(shortPart), mode && shortMode(mode)].filter(Boolean);
  const bar = $("#agentbar"), key = attached + JSON.stringify(chips);
  if (bar.dataset.key === key) return;
  bar.dataset.key = key;
  bar.replaceChildren(...chips.map((c, i) =>
    h("span", { class: `fpart${i === 0 ? " model" : ""}${mode && i === chips.length - 1 ? " mode" : ""}` }, c)));
}

function connectTerminal(name) {
  if (ws && ws.readyState < 2) return;
  clearTimeout(reconnectTimer);
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${proto}://${location.host}${wsPath(name)}${textTerm() ? "?mode=text" : ""}`);
  ws = socket;
  socket.binaryType = "arraybuffer";
  // Text mode's first frame clears the screen itself, so a cached screen stays up until the live one replaces it.
  socket.onopen = () => { if (ws === socket) { if (!textTerm()) term.reset(); sendSize(); } };
  socket.onmessage = (e) => { if (ws === socket) term.write(new Uint8Array(e.data)); };
  socket.onclose = () => {
    if (ws !== socket) return;
    ws = null;
    term?.write("\r\n\x1b[90m[reconnecting…]\x1b[0m\r\n");
    if (view === "term" && attached === name && document.visibilityState === "visible") {
      reconnectTimer = setTimeout(() => restoreTerminal(), 1500);
    }
  };
}

async function restoreTerminal() {
  if (view !== "term" || !attached) return;
  const name = attached;
  try {
    const live = await listSessions();
    noteChats(live);
    if (view !== "term" || attached !== name) return;
    if (!live.some((s) => s.name === name)) {
      detach(); attached = null; saveState({ [sessionKey()]: "" });
      toast("That session has ended");
      if (view === "term") await attachNewest();
      return;
    }
    attach(name);
  } catch {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(restoreTerminal, 3000);
  }
}

// Text terminal (experimental, shared setting): the host sends the session's text (polled) instead of a tmux client's
// output, so nothing re-attaches on resize. See terminal_text in deck/server.py.
function textTerm() { return prefs.textterm === true; }

function detach() {
  clearTimeout(reconnectTimer);
  if (chatView) parkChat(); // a chat stays loaded and connected (chatCache); its socket isn't closed here
  else if (ws) { ws.onclose = null; ws.close(); }
  ws = null;
  if (term) { term.dispose(); term = null; }
  fit = null; // its terminal is gone: a late refit (layout, Review, font) would read the disposed one
  if (chatShell) {
    chatShell = false;
    document.querySelector(":is(.keybar, #say, .brand) .chatstatus")?.remove(); // Working… / Stop stay with the chat
    $("#view-term .termwrap").after($(".keybar")); // the keys back under the screen
    $(".keybar").after($("#say")); // the message box back below the keys, before the chat goes
    $("#attach").hidden = true; // attached items wait for the next chat
    $("#view-term .termwrap").before($("#agentbar")); // the status line back above the screen
    $("#term").replaceChildren();
    $("#term").classList.remove("chat");
    projBar.hidden = true;
    $("#say").hidden = prefs.composer === false;
    placeHarness();
    $("#agentbar").hidden = true;
    delete $("#agentbar").dataset.key;
    $("#shelf").hidden = true;
  }
}
