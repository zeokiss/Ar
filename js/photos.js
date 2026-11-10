/*
 * AR-15 Konfigurátor – termékfotó-nyilvántartás
 *
 * Minden fotó egy kivágott (átlátszó hátterű), jobb oldali nézetű termékkép
 * kalibrációs adatokkal:
 *   src  – kép (URL vagy dataURL)
 *   w, h – a kép mérete pixelben
 *   ppi  – pixel / hüvelyk a képen (méretarány)
 *   ax, ay – az illesztési pont pixelkoordinátája a képen (lásd ANCHORS)
 *   rot  – elforgatás fokban az illesztési pont körül (opcionális)
 *   with – mely gyári részeket tartalmazza még a kép (pl. ['muzzle','ch'])
 *
 * Források (prioritás szerint):
 *   1. a böngészőben, a Fotóillesztő eszközzel felvett képek (IndexedDB)
 *   2. a repóban lévő assets/photos/manifest.json
 */
window.AR_PHOTOS = (function () {
  const DB_NAME = 'ar15-photos', STORE = 'photos';
  const reg = new Map();      // kulcs: itemId|slot|color
  const listeners = [];
  const key = (item, slot, color) => `${item}|${slot}|${color || '*'}`;

  function openDb() {
    return new Promise((res, rej) => {
      if (!window.indexedDB) return rej(new Error('Nincs IndexedDB'));
      const r = indexedDB.open(DB_NAME, 1);
      r.onupgradeneeded = () => r.result.createObjectStore(STORE);
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }
  async function dbAll() {
    try {
      const db = await openDb();
      return await new Promise((res) => {
        const out = [];
        const tx = db.transaction(STORE, 'readonly');
        const cur = tx.objectStore(STORE).openCursor();
        cur.onsuccess = () => { const c = cur.result; if (c) { out.push(c.value); c.continue(); } else res(out); };
        cur.onerror = () => res(out);
      });
    } catch (e) { return []; }
  }
  async function dbPut(rec) {
    const db = await openDb();
    return new Promise((res, rej) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put(rec, key(rec.item, rec.slot, rec.color));
      tx.oncomplete = res; tx.onerror = () => rej(tx.error);
    });
  }
  async function dbDel(k) {
    const db = await openDb();
    return new Promise((res) => { const tx = db.transaction(STORE, 'readwrite'); tx.objectStore(STORE).delete(k); tx.oncomplete = res; tx.onerror = res; });
  }

  async function load() {
    // repó-manifest (http-n keresztül; file:// esetén csendben kimarad)
    try {
      const r = await fetch('assets/photos/manifest.json', { cache: 'no-cache' });
      if (r.ok) {
        const m = await r.json();
        (m.photos || []).forEach(p => {
          if (p.src && !/^data:|^https?:|^\//.test(p.src)) p.src = 'assets/photos/' + p.src;
          p.source = 'repo';
          reg.set(key(p.item, p.slot, p.color), p);
        });
      }
    } catch (e) { /* nincs manifest */ }
    (await dbAll()).forEach(p => { p.source = 'local'; reg.set(key(p.item, p.slot, p.color), p); });
    listeners.forEach(f => f());
  }

  // Fotó keresése: pontos szín -> általános -> bármely szín
  function get(item, slot, color) {
    if (!item) return null;
    let r = reg.get(key(item, slot, color)) || reg.get(key(item, slot, '*'));
    if (r) return Object.assign({ exactColor: true }, r);
    for (const [k, v] of reg) if (k.startsWith(item + '|' + slot + '|')) return Object.assign({ exactColor: false }, v);
    return null;
  }
  function has(item) { for (const k of reg.keys()) if (k.startsWith(item + '|')) return true; return false; }
  function all() { return Array.from(reg.values()); }

  async function save(rec) {
    rec = Object.assign({}, rec, { updated: Date.now() });
    await dbPut(rec);
    rec.source = 'local';
    reg.set(key(rec.item, rec.slot, rec.color), rec);
    listeners.forEach(f => f());
  }
  async function remove(item, slot, color) {
    const k = key(item, slot, color);
    await dbDel(k);
    reg.delete(k);
    listeners.forEach(f => f());
  }

  // Export: manifest + képek (dataURL-ként, egyetlen JSON fájlban)
  function exportPack() {
    const photos = all().filter(p => p.source === 'local').map(p => { const o = Object.assign({}, p); delete o.source; delete o.exactColor; return o; });
    return JSON.stringify({ version: 1, photos }, null, 1);
  }
  async function importPack(text) {
    const m = JSON.parse(text);
    for (const p of (m.photos || [])) await save(p);
    return (m.photos || []).length;
  }

  return { load, get, has, all, save, remove, exportPack, importPack, onChange: f => listeners.push(f) };
})();
