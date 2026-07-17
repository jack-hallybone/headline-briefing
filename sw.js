// Caches the static app shell only. The live feed (data/data.json) is
// deliberately left alone here -- app.js manages that cache itself with a
// network-timeout-then-cache-fallback strategy, since it needs finer control
// (content-type validation, background revalidation) than a blanket SW route
// would give it.
//
// Bump the version suffix whenever a shell file changes, so activate() clears
// the old cache instead of serving stale HTML/JS forever.
const SHELL_CACHE_PREFIX = 'headline-briefing-shell-';
const SHELL_CACHE = SHELL_CACHE_PREFIX + 'v1';
const SHELL_PATHS = ['./', './index.html', './app.js', './favicon.svg', './manifest.json'];

function shellUrls() {
  return SHELL_PATHS.map((path) => new URL(path, self.registration.scope).href);
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_PATHS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      // Only prune our own previous shell-cache versions -- the data cache
      // that app.js owns (and any other cache) is not this worker's to touch.
      .then((names) => Promise.all(
        names
          .filter((name) => name.startsWith(SHELL_CACHE_PREFIX) && name !== SHELL_CACHE)
          .map((name) => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

// Cache-first, network-fallback -- for the known shell files only, and only
// same-origin GET requests. Anything else (including data/data.json and any
// cross-origin request) is left untouched and goes straight to the network,
// so this worker can never become a proxy for content it wasn't meant to
// cache.
self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  if (!shellUrls().includes(request.url)) return;

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      });
    })
  );
});
