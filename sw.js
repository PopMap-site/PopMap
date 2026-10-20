// Radar Crises : données toujours fraîches (réseau d'abord), carte et images en cache pour un démarrage rapide.
const C="popmap-v9";
self.addEventListener("install",e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(["./","index.html","icons/icon-192.png"])))});
self.addEventListener("activate",e=>e.waitUntil(self.clients.claim()));
self.addEventListener("fetch",e=>{const u=new URL(e.request.url);if(e.request.method!=="GET"||/\.(mp3|mp4)$/.test(u.pathname)||u.pathname.startsWith("/api/"))return;
  const fresh=u.pathname.endsWith(".json")&&!u.pathname.endsWith("world.json")||u.pathname.endsWith("/")||u.pathname.endsWith("index.html");
  if(fresh){e.respondWith(fetch(e.request).then(r=>{const k=r.clone();caches.open(C).then(c=>c.put(e.request,k));return r}).catch(()=>caches.match(e.request)));return}
  e.respondWith(caches.match(e.request).then(m=>m||fetch(e.request).then(r=>{if(r.ok&&(u.origin===location.origin||u.host.includes("cdnjs")||u.host.includes("gstatic")||u.host.includes("googleapis"))){const k=r.clone();caches.open(C).then(c=>c.put(e.request,k))}return r})))});
