const CACHE_NAME = "jarvis-web-v13";
const ASSETS = ["/manifest.webmanifest", "/favicon.svg?v=5"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)).catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }
  const url = new URL(event.request.url);
  // Ne jamais mettre en cache les reponses cross-origin (opaques) ni les routes dynamiques.
  if (
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/ws") ||
    url.pathname.startsWith("/actions/")
  ) {
    return;
  }

  // Navigation HTML : NetworkFirst avec repli sur /index.html mis en cache lors des visites reussies.
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((res) => {
          if (res.ok && res.type === "basic") {
            const clone = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put("/index.html", clone)).catch(() => undefined);
          }
          return res;
        })
        .catch(() => caches.match("/index.html"))
    );
    return;
  }

  // Ressources immuables construites par Vite (/assets/*) : CacheFirst.
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((res) => {
          if (res.ok && res.type === "basic") {
            const clone = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone)).catch(() => undefined);
          }
          return res;
        });
      })
    );
    return;
  }

  // Autres ressources meme-origine : NetworkFirst avec cache de secours.
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok && res.type === "basic") {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone)).catch(() => undefined);
        }
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});
