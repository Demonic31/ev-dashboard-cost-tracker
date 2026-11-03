// Service worker de la PWA : seulement une page « hors connexion » quand le réseau manque.
// Rien d'autre n'est mis en cache, et surtout pas les données (/api/data) ni les pages protégées.
const CACHE = "ve-offline-v1";
const OFFLINE = ["/static/offline.html", "/static/favicon.svg"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(OFFLINE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  if (e.request.mode !== "navigate") return;  // le navigateur s'en occupe normalement
  e.respondWith(fetch(e.request).catch(() => caches.match("/static/offline.html")));
});
