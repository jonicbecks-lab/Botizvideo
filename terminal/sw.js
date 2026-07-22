const CACHE_NAME = 'galka-production-shell-v11';
const APP_SHELL = [
  './pro.html',
  './pro.css?v=7',
  './pro.js?v=9',
  './manifest.webmanifest',
  './icons/galka-mark.svg',
  './icons/galka-192.png',
  './icons/galka-512.png',
  './vendor/lightweight-charts.standalone.production.js',
  './modules/store.js',
  './modules/paper-engine.js',
  './modules/radar-engine.js',
  './modules/backup.js',
  './modules/galka-stats.js',
  './modules/shadow-engine.js',
];

const MARKET_HOSTS = new Set(['fapi.binance.com', 'fstream.binance.com']);

async function fetchAndCache(request) {
  const response = await fetch(request, { cache: 'no-store' });
  if (response.ok) {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  // Market data is never served from an application cache.
  if (MARKET_HOSTS.has(url.hostname)) return;
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        return await fetchAndCache(request);
      } catch (error) {
        const cached = await caches.match(request) || await caches.match('./pro.html');
        if (cached) return cached;
        throw error;
      }
    })());
    return;
  }

  if (['script', 'style', 'worker'].includes(request.destination)) {
    event.respondWith(
      fetchAndCache(request).catch(() => caches.match(request)),
    );
    return;
  }

  event.respondWith((async () => {
    const cached = await caches.match(request);
    const refresh = fetchAndCache(request).catch(() => null);
    return cached || await refresh || new Response('Offline', { status: 503 });
  })());
});
