/**
 * Service Worker for HomeCave Standalone Mobile PWA (/homecave/)
 * Provides fast shell caching while keeping authenticated API requests strictly live.
 */

const CACHE_NAME = "homecave-pwa-v0.9.1";
const APP_SHELL = [
  "/homecave/index.html",
  "/homecave/styles.css",
  "/homecave/app.js",
  "/homecave/manifest.webmanifest",
  "/homecave/logo-512.png",
  "/homecave/icon-192.png",
  "/homecave/icon-512.png",
  "/homecave/icon-maskable-512.png",
  "/homecave/apple-touch-icon.png",
  "/homecave/icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL).catch(() => {}))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k.startsWith("homecave-pwa-") && k !== CACHE_NAME)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Never cache authenticated API calls or HA auth endpoints
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/auth/") ||
    url.pathname.startsWith("/local/")
  ) {
    return;
  }

  // Network-first with cache fallback for PWA static shell assets
  if (url.pathname.startsWith("/homecave")) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match("/homecave/index.html")))
    );
  }
});
