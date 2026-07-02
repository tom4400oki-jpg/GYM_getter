// GYM Getter Service Worker
// 方針: アプリシェルはネットワーク優先＋オフライン時キャッシュ。
//       data.json は鮮度最優先のため SW を素通り（ブラウザの no-store に委ねる）。
const CACHE = "gym-getter-v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  // data 系 JSON（data.json / data-*.json / facilities.json）は
  // 鮮度最優先で常にネットワーク（キャッシュしない）
  if (/\/(data(-[\w-]+)?|facilities)\.json$/.test(url.pathname)) return;
  // 別オリジン（ふれあいネット等）は介入しない
  if (url.origin !== self.location.origin) return;

  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((c) => c || caches.match("/")))
  );
});
