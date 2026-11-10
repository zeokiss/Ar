// Futásidejű ellenőrzés: minden fül, demó-build, keresés, boltváltás, árkapcsoló, robbantás. Előtte: python3 -m http.server 8765
// Futtatás: node tools/runtime_check.js
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 1000 } });
  const errs = [], bad = [];
  p.on('pageerror', e => errs.push('JS: ' + e.message));
  p.on('console', m => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errs.push('konzol: ' + m.text()); });
  p.on('response', r => { if (r.status() >= 400) bad.push(r.status() + ' ' + r.url().replace('http://localhost:8765/', '')); });
  await p.goto('http://localhost:8765/index.html');
  await p.evaluate(() => localStorage.clear()); await p.reload(); await p.waitForTimeout(2500);
  const tabs = await p.$$eval('#tabs .tab', els => els.map(e => e.dataset.tab));
  let thumbsBroken = 0, cards = 0;
  for (const t of tabs) {
    await p.click(`#tabs .tab[data-tab="${t}"]`); await p.waitForTimeout(250);
    // minden kép betöltését kikényszerítjük (lusta betöltés)
    await p.evaluate(() => document.querySelectorAll('#panel img').forEach(i => { i.loading = 'eager'; }));
    await p.waitForTimeout(500);
    const r = await p.evaluate(() => { const imgs = [...document.querySelectorAll('#panel img')]; return { cards: document.querySelectorAll('#panel .card').length, broken: imgs.filter(i => i.complete && i.naturalWidth === 0).map(i => i.getAttribute('src')) }; });
    cards += r.cards; thumbsBroken += r.broken.length; if (r.broken.length) errs.push('törött bélyegkép: ' + r.broken.join(', '));
  }
  const presets = await p.$$eval('#presets option', els => els.map(e => e.value).filter(Boolean));
  for (const pr of presets) { await p.selectOption('#presets', pr); await p.waitForTimeout(1800); }
  for (const q of ['holo', 'lámpa', 'tus', 'm4', 'xyz']) { await p.fill('#qs', q); await p.waitForTimeout(120); }
  await p.fill('#qs', ''); 
  for (const m of await p.$$eval('#shopMode option', els => els.map(e => e.value))) { await p.selectOption('#shopMode', m); await p.waitForTimeout(250); }
  await p.click('.pswitch'); await p.click('.pswitch');
  await p.click('#btnExplode'); await p.waitForTimeout(900); await p.click('#btnExplode');
  console.log(JSON.stringify({ fülek: tabs.length, kártyák: cards, demóbuildek: presets.length, töröttBélyeg: thumbsBroken, JShibák: errs, hiányzóFájlok: [...new Set(bad)] }, null, 1));
  await b.close();
})();
