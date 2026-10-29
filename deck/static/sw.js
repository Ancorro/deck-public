// Deck offline copy: network first, cache as fallback. Keeps the app shell and the machine notes bundle so a
// phone can still open Deck and read the notes with no connection. Live data (sessions, terminals) is never cached.
const CACHE = "deck-offline-v2";
// Another site's files never change at their address (pinned CDN versions with SRI hashes, Google Fonts): from the
// cache at once, the network only on a miss (user: "restarting app too slow"; over iroh each was a round trip first)
const FIXED = (url) => url.origin !== location.origin && /^(cdn\.jsdelivr\.net|fonts\.(googleapis|gstatic)\.com|unpkg\.com|cdnjs\.cloudflare\.com)$/.test(url.hostname);
const KEEP = (url) => url.origin !== location.origin || url.pathname === "/" || url.pathname.startsWith("/static/")
  || url.pathname === "/api/notes/bundle";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(Promise.all([self.clients.claim(),
  caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))]))); // older caches go
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || !KEEP(url)) return;
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    if (FIXED(url)) {
      const hit = await cache.match(e.request);
      if (hit) return hit;
    }
    try {
      const res = await fetch(e.request);
      if (res.ok || res.type === "opaque") cache.put(e.request, res.clone());
      return res;
    } catch (err) {
      const hit = await cache.match(e.request, { ignoreSearch: url.pathname === "/api/notes/bundle" });
      if (hit) return hit;
      throw err;
    }
  })());
});
