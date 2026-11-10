/*
 * AR-15 Konfigurátor – termékfotó-nyilvántartás
 *
 * Rekord (kulcs: item | slot | color):
 *   item  – termék id (data.js) vagy alapfegyver id
 *   slot  – 'main' (alap), 'base' (alapfegyver fotó, color = finish id),
 *           'geo' (alapfegyver geometria, kép nélkül), 'rear' / 'front' (BUIS)
 *   color – szín id (data.js COLORS / FINISHES) vagy '*'
 *   src, w, h  – a kivágott (átlátszó hátterű) oldalnézeti kép és mérete (px)
 *   ppi        – pixel / hüvelyk a képen
 *   ax, ay     – az illesztési pont a képen (px)
 *   rot, dx, dy, scale – finomhangolás (fok, hüvelyk, szorzó)
 *
 * Geometria rekord (slot 'geo'): a fegyver koordinátarendszerében, hüvelykben
 * (origó: a felső tok hátsó éle a cső tengelyén, x előre, y lefelé):
 *   pts: { stock, grip, mag, muzzle, railStart, railEnd, hgRear, hgFront, hgBottom }
 *   cuts: { stock|grip|mag|muzzle: [[x,y], ...] } – a gyári alkatrész kivágó poligonja
 *
 * Források: 1. a böngésző IndexedDB-je (Fotóillesztő eszköz)
 *           2. assets/photos/pack.json (ugyanaz a formátum, a repóba commitolható)
 *           3. assets/photos/manifest.json (fájlnevekkel hivatkozott képek)
 */
window.AR_PHOTOS = (function () {
  const DB_NAME = 'ar15-photos', STORE = 'photos';
  const reg = new Map();
  const drafts = new Map();
  const listeners = [];
  const key = (item, slot, color) => `${item}|${slot}|${color || '*'}`;
  const notify = () => listeners.forEach(f => { try { f(); } catch (e) { console.error(e); } });

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
        const cur = db.transaction(STORE, 'readonly').objectStore(STORE).openCursor();
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

  async function fetchJson(url) {
    try { const r = await fetch(url, { cache: 'no-cache' }); return r.ok ? await r.json() : null; } catch (e) { return null; }
  }
  const resolveSrc = (p) => { if (p.src && !/^data:|^https?:|^\//.test(p.src)) p.src = 'assets/photos/' + p.src; return p; };

  async function load() {
    for (const url of ['assets/photos/manifest.json', 'assets/photos/pack.json']) {
      const m = await fetchJson(url);
      if (m && m.photos) m.photos.forEach(p => { resolveSrc(p); p.source = 'repo'; reg.set(key(p.item, p.slot, p.color), p); });
    }
    (await dbAll()).forEach(p => { p.source = 'local'; reg.set(key(p.item, p.slot, p.color), p); });
    notify();
  }

  function pick(map, item, slot, color) {
    return map.get(key(item, slot, color)) || map.get(key(item, slot, '*'));
  }
  // Fotó keresése: piszkozat -> pontos szín -> általános -> bármely szín (exactColor: false)
  function get(item, slot, color) {
    if (!item) return null;
    slot = slot || 'main';
    const d = pick(drafts, item, slot, color);
    if (d && d.src) return Object.assign({ exactColor: true, draft: true }, d);
    const r = pick(reg, item, slot, color);
    if (r && r.src) return Object.assign({ exactColor: true }, r);
    for (const [k, v] of reg) if (v.src && v.item === item && v.slot === slot) return Object.assign({ exactColor: false }, v);
    return null;
  }
  function geo(baseId) {
    const d = drafts.get(key(baseId, 'geo', '*'));
    return d || reg.get(key(baseId, 'geo', '*')) || null;
  }
  const has = (item) => { for (const v of reg.values()) if (v.item === item && v.src) return true; return false; };
  const all = () => Array.from(reg.values());

  async function save(rec) {
    rec = Object.assign({}, rec, { updated: Date.now() });
    delete rec.source; delete rec.exactColor; delete rec.draft;
    await dbPut(rec);
    rec.source = 'local';
    reg.set(key(rec.item, rec.slot, rec.color), rec);
    drafts.delete(key(rec.item, rec.slot, rec.color));
    notify();
  }
  async function remove(item, slot, color) {
    const k = key(item, slot, color);
    await dbDel(k); reg.delete(k); notify();
  }
  function setDraft(rec) { drafts.set(key(rec.item, rec.slot, rec.color), rec); notify(); }
  function clearDrafts() { if (drafts.size) { drafts.clear(); notify(); } }

  function exportPack() {
    const photos = all().filter(p => p.source === 'local').map(p => { const o = Object.assign({}, p); delete o.source; return o; });
    return JSON.stringify({ version: 1, photos });
  }
  async function importPack(text) {
    const m = JSON.parse(text);
    let n = 0;
    for (const p of (m.photos || [])) { await save(p); n++; }
    return n;
  }

  return { load, get, geo, has, all, save, remove, setDraft, clearDrafts, exportPack, importPack, onChange: f => listeners.push(f), key };
})();
