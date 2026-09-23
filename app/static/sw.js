/* 설치 조건 충족용 최소 서비스워커. 정적 파일만 캐시, /api 와 페이지는 항상 네트워크. */
const CACHE = 'jdr-static-v23';
const ASSETS = ['/static/style.css', '/static/app.js', '/static/icons/icon-192.png', '/static/icons/icon-512.png'];
self.addEventListener('install', (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting())); });
self.addEventListener('activate', (e) => { e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || !url.pathname.startsWith('/static/')) return; // 네트워크 그대로
  e.respondWith(fetch(e.request).then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; }).catch(() => caches.match(e.request)));
});
