// ---- the tab column: every host's sessions as tabs (folders, drag to arrange, rename, menus), + for a new
// session, the trash of closed chats ----
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

async function killCurrent() {
  if (!attached) return;
  const name = attached;
  // Then the tab below it opens (the one above, for the last); with no tabs left, the terminal (user)
  const tabs = [...document.querySelectorAll("#chips .chip[data-name]")].map((c) => c.dataset.name);
  const i = tabs.indexOf(name), next = tabs[i + 1] ?? tabs[i - 1];
  detach(); attached = null; saveState({ [sessionKey()]: "" });
  await api("DELETE", `/api/sessions/${encodeURIComponent(name)}`);
  await poll();
  if (next && sessions.some((s) => s.name === next && s.running)) openSession(next);
  else attachNewest();
  loadTabTrash();
}

// Closed agent chats wait in the left column's trash for a month (deck/session_trash.py): Restore opens one again,
// as a new tab on its conversation; Delete forever drops it. One trash for the whole Deck (user): every host's, newest
// first, each marked with its host's name and colour; Restore and Delete go to the host it was on.
async function loadTabTrash() {
  const at = (host) => host === HOME_HOST ? "" : `/peer/${encodeURIComponent(host)}`;
  const hosts = [HOME_HOST, ...hostList.filter((x) => !x.current && x.name !== HOME_HOST).map((x) => x.name)];
  const lists = await Promise.all(hosts.map((host) => api("GET", `${at(host)}/api/session-trash`)
    .then((l) => l.map((it) => ({ ...it, host })), () => null))); // offline: its trash waits
  if (lists[0] === null) return;
  const items = lists.flat().filter(Boolean).sort((a, b) => b.deleted - a.deleted);
  const box = $("#tabtrash");
  box.hidden = !items.length;
  box.querySelector("summary").replaceChildren(trashIcon(), h("span", { class: "ttlabel" }, ` Trash (${items.length})`));
  const act = (label, run) => h("button", { type: "button", onclick: async (e) => {
    e.target.disabled = true;
    try { await run(); } catch (err) { toast(err.message); }
    loadTabTrash();
  } }, label);
  const ago = (t) => { const d = (Date.now() / 1000 - t) / 86400; return d < 1 ? "today" : `${Math.floor(d)}d ago`; };
  // the same rows as the shelf's trash: one line each, opened for where it ran and its buttons (user)
  $("#tabtrashlist").replaceChildren(...items.map((it) => h("details", { class: "shelfitem" },
    h("summary", {}, h("span", { class: "cicon" }, icon(it.type)), " ", it.title || it.label),
    h("div", { class: "shelfmeta" }, h("span", { class: "trashhost", style: `--hostled: ${hostList.find((x) => x.name === it.host)?.led || "var(--screen-dim)"}` },
      hostLabel(it.host)), ` · ${shortPath(it.folder)} · ${ago(it.deleted)}`),
    h("div", { class: "shelfacts" },
      act("Restore", async () => {
        const { session } = await api("POST", `${at(it.host)}/api/session-trash/${it.id}/restore`);
        await poll(); openSession(it.host === HOME_HOST ? session : `${it.host}/${session}`);
      }),
      act("Delete forever", () => api("DELETE", `${at(it.host)}/api/session-trash/${it.id}`))))));
}
// (first loaded by loadHosts, once the host list is known: it lists every host's trash)
