// Guarda só os arquivos do app. Dados do TSE nunca ficam em cache aqui.
const VERSAO = 'apuracao-v19';
const ARQUIVOS = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './apple-touch-icon.png'];
self.addEventListener('install', e => { e.waitUntil(caches.open(VERSAO).then(c => c.addAll(ARQUIVOS))); self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSAO).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (u.origin !== location.origin) return;            // TSE e fontes: direto da rede
  e.respondWith(fetch(e.request).then(r => {           // rede primeiro: pega atualizações do app
    const copia = r.clone(); caches.open(VERSAO).then(c => c.put(e.request, copia)); return r;
  }).catch(() => caches.match(e.request)));
});
