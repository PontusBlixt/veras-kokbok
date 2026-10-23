// Veras Kokbok – service worker.
// Gör att appen går att installera. Hämtar alltid senaste versionen från servern först,
// och visar den senast sparade sidan om servern inte går att nå (t.ex. Tailscale avstängt).
// Recept, skafferi och bilder (/api/, /images/) går alltid direkt till servern och sparas aldrig här.

const CACHE = "kokbok-skal-1";
const SHELL = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;                 // typsnitt m.m. hanteras av webbläsaren
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/images/")) return;

  const key = req.mode === "navigate" ? "/" : url.pathname;
  event.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(key, copy));
        }
        return res;
      })
      .catch(() => caches.match(key).then((hit) => hit || caches.match("/")))
  );
});
