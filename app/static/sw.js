// Kazuo : service worker. Il rend l'appli installable sur l'écran d'accueil et garde
// l'essentiel en cache pour qu'elle s'ouvre même sans réseau.
// Règle d'or : les DONNÉES (offres, décisions) ne sont jamais servies depuis le cache.
// À chaque modification des fichiers listés ci-dessous, change VERSION : l'ancien cache sera effacé.
const VERSION = "kazuo-v1";
const ESSENTIELS = [
  "/hors-ligne",
  "/static/kazuo.css?v=4",
  "/static/fonts/dela-gothic-one.woff2?v=2",
  "/static/img/embleme.webp",
  "/static/img/icone-192.png",
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(ESSENTIELS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(cles => Promise.all(cles.filter(k => k !== VERSION).map(k => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  const url = new URL(req.url);
  // Décisions (POST), autres sites, API : toujours le réseau, jamais le cache
  if (req.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/static/")) {
    // Fichiers statiques (versionnés par ?v=…) : le cache d'abord, le réseau sinon
    e.respondWith(caches.match(req).then(trouve => trouve || fetch(req).then(rep => {
      if (rep.ok) {
        const copie = rep.clone();
        caches.open(VERSION).then(c => c.put(req, copie));
      }
      return rep;
    })));
  } else if (req.mode === "navigate") {
    // Pages : toujours la version à jour du réseau ; la page « hors ligne » si le réseau ne répond pas
    e.respondWith(fetch(req).catch(() => caches.match("/hors-ligne")));
  }
});
