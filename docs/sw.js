/**
 * Birds Today — service worker.
 *
 * Strategy:
 *  - cache-first for big, immutable assets (model, wasm runtime, labels, vendor
 *    libs) so repeat visits don't re-download ~19 MB — the main free-hosting
 *    bandwidth win and a big speed-up.
 *  - network-first (cache fallback) for everything else (HTML, JS, CSS, the
 *    manifest, and bird images) so updates — including new checkpoint birds —
 *    show up immediately, with offline fallback.
 *
 * Bump VERSION when the app shell changes to retire old caches.
 */
var VERSION = "birds-today-v1";
var IMMUTABLE = [/geomodel_fp16\.onnx$/, /labels\.txt$/, /\/vendor\//];

self.addEventListener("install", function (e) {
  self.skipWaiting();
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== VERSION; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function isImmutable(url) {
  return IMMUTABLE.some(function (re) { return re.test(url); });
}

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;  // let tiles etc. pass through

  if (isImmutable(url.pathname)) {
    e.respondWith(caches.open(VERSION).then(function (c) {
      return c.match(req).then(function (hit) {
        return hit || fetch(req).then(function (res) {
          if (res.ok) c.put(req, res.clone());
          return res;
        });
      });
    }));
  } else {
    e.respondWith(fetch(req).then(function (res) {
      if (res.ok) {
        var copy = res.clone();
        caches.open(VERSION).then(function (c) { c.put(req, copy); });
      }
      return res;
    }).catch(function () { return caches.match(req); }));
  }
});
