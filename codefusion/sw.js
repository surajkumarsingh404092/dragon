// DRAGON offline app shell: the page (with its CSS and JavaScript inside) is cached,
// so the editor opens with no network. Network first, cached copy when offline.
// Hostel Wi-Fi is often "connected" but dead, so a request that hangs for 4 seconds
// also falls back to the cached copy instead of leaving a blank page.
const CACHE = 'dragon-shell-v6';
const NETWORK_TIMEOUT_MS = 4000;

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.add('./')).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || req.mode !== 'navigate') return;
  if (new URL(req.url).origin !== location.origin) return;
  const cached = () => caches.match('./', { ignoreSearch: true });
  e.respondWith(new Promise(resolve => {
    let done = false;
    const finish = res => { if (!done && res) { done = true; clearTimeout(timer); resolve(res); } };
    const timer = setTimeout(() => cached().then(finish), NETWORK_TIMEOUT_MS);
    fetch(req).then(res => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put('./', copy));
        finish(res);
      } else {
        cached().then(hit => finish(hit || res));
      }
    }).catch(() => cached().then(hit => finish(hit || Response.error())));
  }));
});
