// App-shell cache so the driver app opens without signal. API calls always go to the network;
// telemetry that cannot be sent waits in the app's own queue (app.js), not here.
const CACHE = "rr-driver-v1";
const SHELL = ["/driver/", "/driver/app.js", "/driver/manifest.webmanifest", "/driver/icon.svg"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL))));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith("/api/")) return; // never cache live data
  e.respondWith(fetch(e.request).then((res) => {
    const copy = res.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match(e.request)));
});
