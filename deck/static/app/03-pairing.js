// ---- pairing: this device must be paired before it may use the Deck ----
let pairCode = null;
function showPairGate(mode) {
  if (mode === "help" && pairCode) return;  // holding a code: the page's other requests failing mustn't hide its form
  const g = $("#pairgate");
  $("#pairclaim").hidden = mode !== "claim";
  $("#pairwait").hidden = mode !== "wait";
  $("#pairhelp").hidden = mode !== "help";
  if (!g.open) g.showModal();
  if (mode === "wait") waitForApproval();
}
$("#pairgate").addEventListener("cancel", (e) => e.preventDefault()); // can't be dismissed, but the app can leave
$("#pairleave").hidden = !window.Capacitor;
$("#pairleavebtn").onclick = () => { location.href = "https://localhost/#hosts"; };  // the app's host list (app/www)
let approvalTimer = 0;
function waitForApproval() {
  clearInterval(approvalTimer);
  approvalTimer = setInterval(async () => {
    const me = await fetch("/api/me").then((r) => r.json()).catch(() => null);
    if (me?.device && !me.pending) { clearInterval(approvalTimer); location.replace("/"); }
    if (me && !me.device) { clearInterval(approvalTimer); pairCode = null; showPairGate("help"); $("#pairerr").textContent = "Pairing was refused."; }
  }, 2000);
}
$("#pairform").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("POST", "/api/pair/claim", { code: pairCode, name: $("#pairname").value });
    pairCode = null;
    history.replaceState(null, "", "/");
    showPairGate("wait");
  } catch (err) { $("#pairerr").textContent = err.message; }
};
function pairFromHash() {
  if (!location.hash.startsWith("#pair=")) return false;
  const q = new URLSearchParams(location.hash.slice(1));
  pairCode = q.get("pair");
  // Scanned with the phone camera, the link lands in the browser: offer to pass it to the installed Deck app, over
  // iroh when the host runs it (deck://pair: the app saves the host by its key), else this same address.
  $("#pairapp").hidden = !!window.Capacitor || !/Android/i.test(navigator.userAgent);
  const iroh = q.get("iroh"), enc = encodeURIComponent;
  $("#pairapplink").href = iroh
    ? `intent://pair?key=${iroh}&name=${enc(q.get("name") || location.hostname.split(".")[0])}&code=${enc(pairCode)}#Intent;scheme=deck;package=dev.deck.app;end`
    : `intent://${location.host}/?pair=${enc(pairCode)}#Intent;scheme=https;package=dev.deck.app;end`;
  $("#pairname").value = /iPhone|iPad/.test(navigator.userAgent) ? "iPhone" : /Android/.test(navigator.userAgent) ? "Android phone" : "Browser";
  for (const d of document.querySelectorAll("dialog[open]")) if (d.id !== "pairgate") d.close();
  showPairGate("claim");
  return true;
}
// A pairing link opened inside an already-loaded Deck (e.g. tapped in Settings → Devices on the phone app itself).
window.addEventListener("hashchange", pairFromHash);
(async () => {
  if (pairFromHash()) return;
  const me = await fetch("/api/me").then((r) => r.json()).catch(() => null);
  if (me?.pending) showPairGate("wait");
  else if (me?.enforced && !me.device) showPairGate("help");
})();

// Settings → Devices: list, remove, and pair a new device (QR + approve).
async function renderDevices() {
  const list = await api("GET", "/api/devices").catch(() => []);
  const ago = (t) => (t ? `${since(t)} ago` : "never");
  $("#devlist").replaceChildren(...(list.length ? list.map((d) => h("div", { class: "hostrow" },
    h("b", {}, d.name), h("span", {}, d.pending ? "waiting" : `used ${ago(d.last_seen)}`),
    d.me ? h("em", {}, "this one") : h("button", { type: "button", class: "ghost small", onclick: async () => {
      if (!confirm(`Remove ${d.name}? It will need pairing again.`)) return;
      await api("DELETE", `/api/devices/${d.id}`).catch((e) => toast(e.message));
      renderDevices();
    } }, "Remove"))) : [h("p", { class: "hint" }, "No devices paired yet.")]));
}
let pairPoll = 0, shownCode = null;
$("#devbtn").onclick = () => { $("#pairbox").hidden = true; renderDevices(); $("#devices").showModal(); };
$("#devclose").onclick = () => $("#devices").close();  // a shown code keeps waiting: the Allow dialog still pops up
$("#pairnew").onclick = async () => {
  try {
    const { code, iroh, name, url } = await api("POST", "/api/pair/start");
    shownCode = code;
    // The QR opens on another device: 127.0.0.1 (this PC's own browser) means nothing there, so use the host's address.
    const origin = url && /^(127\.|localhost$|\[::1\]$)/.test(location.hostname) ? url.replace(/\/$/, "") : location.origin;
    // Opens in the phone's browser; with deck-iroh running it also carries the host's iroh key, so "Pair the Deck
    // app instead" there pairs the app over iroh (works on any network afterwards, no Tailscale).
    const link = `${origin}/#pair=${code}${iroh ? `&iroh=${iroh}&name=${encodeURIComponent(name)}` : ""}`;
    $("#pairqr").src = `/api/pair/qr.svg?url=${encodeURIComponent(link)}`;
    $("#pairlink").href = link; $("#pairlink").textContent = link;
    // Tapped on this same device (e.g. the phone app pairing itself): stay in this page, never hand off to a browser.
    $("#pairlink").onclick = (e) => { e.preventDefault(); location.hash = link.split("#")[1]; };
    $("#pairask").textContent = "Waiting for the new device to scan…";
    $("#pairbox").hidden = false;
    clearInterval(pairPoll);
    const until = Date.now() + 5 * 60 * 1000;  // the code's lifetime
    pairPoll = setInterval(async () => {
      if (Date.now() > until) return clearInterval(pairPoll);
      const { device } = await api("GET", `/api/pair/pending?code=${encodeURIComponent(code)}`).catch(() => ({}));
      if (!device) return;
      clearInterval(pairPoll);
      $("#pairask").textContent = `"${device.name}" is asking to join.`;
      $("#pairapprovetext").textContent = `Allow "${device.name}" to use this Deck?`;
      $("#pairapprove").showModal();
    }, 2000);
  } catch (e) { toast(e.message); }
};
// The PC app's tray "Pair a phone…" opens Deck at #devices-pair: straight to a new pairing code.
function devicesPairFromHash() {
  if (location.hash !== "#devices-pair") return;
  history.replaceState(null, "", "/");
  $("#devbtn").onclick();
  $("#pairnew").onclick();
}
window.addEventListener("hashchange", devicesPairFromHash);
devicesPairFromHash();
for (const [btn, allow] of [["#pairallow", true], ["#pairdeny", false]]) $(btn).onclick = async () => {
  $("#pairapprove").close();
  try {
    await api("POST", "/api/pair/decide", { code: shownCode, allow });
    toast(allow ? "Device allowed" : "Device denied");
  } catch (e) { toast(e.message); }
  $("#pairbox").hidden = true;
  renderDevices();
};
$("#pairapprove").addEventListener("cancel", (e) => e.preventDefault()); // answer it: Allow or Deny

// Settings → PCs: linked PCs (deck/links.py). This PC makes a code and approves it, or pastes another PC's code.
let linkPoll = 0, joinPoll = 0, linkSecret = "";
async function renderLinks() {
  const { me, members = [] } = await api("GET", "/api/links").catch(() => ({}));
  const act = (label, run) => h("button", { type: "button", class: "ghost small", onclick: async () => {
    try { await run(); } catch (e) { toast(e.message); }
    renderLinks(); loadHosts();
  } }, label);
  $("#linklist").replaceChildren(...(members.length ? members.map((m) => h("div", { class: "hostrow" },
    h("b", {}, m.name), m.hosting === false ? h("span", {}, "client only") : null, m.name === me ? h("em", {}, "this one") : null,
    m.name === me ? act("Leave", async () => {
      if (!confirm("Take this PC out of the group? It keeps its own sessions; link it again to rejoin.")) return;
      await api("POST", "/api/link/leave");
    }) : act("Remove", async () => {
      if (!confirm(`Remove ${m.name}? The other PCs get a new key, so it can't reach them any more.`)) return;
      const { missed, relink } = await api("DELETE", `/api/links/${encodeURIComponent(m.name)}`);
      const out = [...missed, ...relink];
      toast(out.length ? `Removed. Not reached, so link again (Leave first): ${out.join(", ")}` : `Removed ${m.name}`);
    })))
    : [h("p", { class: "hint" }, "Not linked with other PCs yet.")]));
}
$("#linkbtn").onclick = () => { $("#linkbox").hidden = $("#joinbox").hidden = true; renderLinks(); $("#links").showModal(); };
$("#linkclose").onclick = () => $("#links").close(); // a shown code keeps waiting: the Allow dialog still pops up
$("#linknew").onclick = async () => {
  try {
    const { code } = await api("POST", "/api/link/start");
    linkSecret = code.split("-").pop();
    $("#linkcode").value = code;
    $("#linkask").textContent = "Waiting for the other PC…";
    $("#joinbox").hidden = true; $("#linkbox").hidden = false;
    $("#linkcode").select();
    clearInterval(linkPoll);
    const until = Date.now() + 5 * 60 * 1000;
    linkPoll = setInterval(async () => {
      if (Date.now() > until) { $("#linkask").textContent = "Code expired."; return clearInterval(linkPoll); }
      const { pc } = await api("GET", `/api/link/pending?secret=${linkSecret}`).catch(() => ({}));
      if (!pc) return;
      clearInterval(linkPoll);
      $("#linkask").textContent = `"${pc.name}" is asking to link.`;
      $("#linkapprovetext").textContent = `Link "${pc.name}" with this PC? It will be able to run anything here, and on every PC linked with this one.`;
      $("#linkapprove").showModal();
    }, 2000);
  } catch (e) { toast(e.message); }
};
for (const [btn, allow] of [["#linkallow", true], ["#linkdeny", false]]) $(btn).onclick = async () => {
  $("#linkapprove").close();
  try {
    await api("POST", "/api/link/decide", { secret: linkSecret, allow });
    toast(allow ? "PC linked" : "Link refused");
  } catch (e) { toast(e.message); }
  $("#linkbox").hidden = true;
  renderLinks(); loadHosts();
};
$("#linkapprove").addEventListener("cancel", (e) => e.preventDefault());
$("#linkjoin").onclick = async () => {
  $("#linkbox").hidden = true; $("#joinbox").hidden = false;
  const { why } = await api("GET", "/api/link/join").catch(() => ({}));
  $("#joinstate").textContent = why || "";
  $("#joincode").focus();
};
$("#joingo").onclick = async () => {
  try {
    $("#joinstate").textContent = "Reaching the other PC…";
    const j = await api("POST", "/api/link/join", { code: $("#joincode").value });
    $("#joinstate").textContent = `Waiting for ${j.host || "the other PC"} to allow it…`;
    clearInterval(joinPoll);
    joinPoll = setInterval(async () => {
      const s = await api("GET", "/api/link/join").catch(() => ({}));
      if (s.state === "pending") return;
      clearInterval(joinPoll);
      $("#joinstate").textContent = s.state === "ok" ? `Linked with ${s.host}.` : s.state === "refused" ? "The other PC refused." : s.error || "Linking failed.";
      if (s.state === "ok") { $("#joincode").value = ""; $("#joinbox").hidden = true; toast(`Linked with ${s.host}`); renderLinks(); loadHosts(); }
    }, 2000);
  } catch (e) { $("#joinstate").textContent = e.message; }
};
