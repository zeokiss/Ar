/* AR-15 Konfigurátor – alkalmazáslogika */
(function () {
  const D = window.AR_DATA;
  const RN = window.AR_RENDER;
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));

  const ITEM = Object.fromEntries(D.ITEMS.map(i => [i.id, i]));
  const BASE = Object.fromEntries(D.BASES.map(b => [b.id, b]));
  const byCat = (cat) => D.ITEMS.filter(i => i.cat === cat);
  const SINGLE_CATS = D.CATEGORIES.filter(c => c.id !== 'base' && !c.multi).map(c => c.id);
  const MULTI_CATS = D.CATEGORIES.filter(c => c.multi).map(c => c.id);

  // Gyári alkatrészek tömege (g) – a cserénél ennyit vonunk le
  const FACTORY_W = { grip: 75, stock: 250, muzzle: 85, trigger: 70, ch: 45, buis: 90 };
  const RIFLE_KIT = { name: 'Rifle puffercső-készlet (tushoz szükséges)', price: 60, weight: 110 };

  // Árak: KIZÁRÓLAG svájci bolti árak (assets/prices/merged.json); ahol nincs ilyen, ott "nincs ár" látszik.
  // Csak olyan bolt jöhet szóba, amelyik szállít, vagy a 8805 Richterswil közelében van (assets/shops.json).
  let PRICES = {}, SHOPS = [], SHOPINFO = {}, SHOPREF = { place: 'Richterswil', plz: '8805', radius_km: 30 };
  const shopOk = (sh) => {
    if (!Object.keys(SHOPINFO).length) return true;
    const i = SHOPINFO[sh];
    return !!i && (i.ships === true || i.walk === true || (i.km != null && i.km <= SHOPREF.radius_km));
  };
  const shopTag = (sh) => (SHOPINFO[sh] && SHOPINFO[sh].city) ? `${sh} (${SHOPINFO[sh].city})` : sh;
  const shopBadge = (sh) => { const i = SHOPINFO[sh]; return i ? (i.walk ? ' · helyben is elérhető' : (i.ships ? ' · szállít' : '')) : ''; };
  // a színváltozatnak külön lehet ára és termékoldala (pl. a Schmeisser Bronze kivitel drágább a feketénél)
  const variantKey = (it) => BASE[it.id] ? state.finish : (it.colors ? (state.colors[it.id] || it.colors[0]) : null);
  function variantOf(o, it) {
    const k = variantKey(it), v = (o.colors && k) ? o.colors[k] : null;
    return { chf: v ? v.chf : o.chf, url: (v && v.url) || o.url, in_stock: v ? v.in_stock : o.in_stock };
  }
  function offersOf(it) {
    const p = PRICES[it.id];
    if (!p) return [];
    const offs = (p.offers && p.offers.length) ? p.offers : (p.chf != null ? [{ shop: p.shop, chf: p.chf, url: p.url, in_stock: p.in_stock }] : []);
    return offs.filter(o => shopOk(o.shop));
  }
  // Bolt-választás egy tételhez: tételenként felülírható, egyébként az "egy boltból" mód, egyébként a legolcsóbb.
  function offerFor(it) {
    const offs = offersOf(it);
    if (!offs.length) return null;
    const want = state.shop[it.id];
    let o = want && offs.find(x => x.shop === want);
    if (!o && state.mode && state.mode !== 'cheapest') o = offs.find(x => x.shop === state.mode);
    if (o) return o;
    // mindig a legolcsóbb ajánlat; a raktárhiány csak jelölve látszik (nincs raktáron / előrendelés)
    return offs.reduce((a, b) => (variantOf(b, it).chf < variantOf(a, it).chf ? b : a));
  }
  const priceOf = (it) => {
    const o = offerFor(it);
    if (!o) return { chf: null, none: true, p: null };
    const v = variantOf(o, it);
    return { chf: v.chf, none: false, p: { shop: o.shop, url: v.url, in_stock: v.in_stock } };
  };
  const weightOf = (it) => { const p = PRICES[it.id]; return (p && p.weight_g) ? p.weight_g : (it.weight || 0); };
  const fmtCHF = (n) => (n == null ? '—' : new Intl.NumberFormat('hu-HU', { maximumFractionDigits: 0 }).format(Math.round(n)) + ' CHF');
  const priceTxt = (q) => (q.none ? 'nincs ár' : fmtCHF(q.chf));
  const shopName = (p) => (p && p.shop) ? p.shop : '';
  const fmtHUF = n => new Intl.NumberFormat('hu-HU', { maximumFractionDigits: 0 }).format(Math.round(n / 1000) * 1000) + ' Ft';
  const fmtKg = g => (g / 1000).toLocaleString('hu-HU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' kg';

  // ------------------------------------------------------------ állapot
  function defaultState(baseId) {
    const b = BASE[baseId || 'schm-m4fl'];
    const sel = {};
    SINGLE_CATS.forEach(c => {
      const list = byCat(c);
      sel[c] = (list.find(i => i.factory) || list.find(i => i.none) || list[0]).id;
    });
    if (!b.factoryBuis) sel.buis = 'buis-none';
    return { base: b.id, finish: b.finishes[0], sel, multi: { mlok: [], controls: [] }, colors: {}, pos: {}, rate: 430, eurChf: 0.93, shop: {}, mode: 'cheapest' };
  }

  let state = loadState() || defaultState();
  let activeTab = 'base';
  let brandFilter = 'Összes';
  let exploded = false;
  let lastChangedCat = null;

  function loadState() {
    try {
      const h = location.hash.slice(1);
      if (h.startsWith('b=')) return sanitize(JSON.parse(decodeURIComponent(escape(atob(h.slice(2))))));
    } catch (e) { /* hibás link */ }
    try {
      const s = localStorage.getItem('ar15-config');
      if (s) return sanitize(JSON.parse(s));
    } catch (e) { /* nincs tároló */ }
    return null;
  }
  function sanitize(s) {
    const d = defaultState(s && BASE[s.base] ? s.base : undefined);
    if (!s || typeof s !== 'object') return d;
    const out = Object.assign({}, d, { finish: BASE[d.base].finishes.includes(s.finish) ? s.finish : d.finish });
    SINGLE_CATS.forEach(c => { if (s.sel && ITEM[s.sel[c]] && ITEM[s.sel[c]].cat === c) out.sel[c] = s.sel[c]; });
    MULTI_CATS.forEach(c => { out.multi[c] = ((s.multi && s.multi[c]) || []).filter(id => ITEM[id] && ITEM[id].cat === c); });
    out.colors = (s.colors && typeof s.colors === 'object') ? s.colors : {};
    out.pos = (s.pos && typeof s.pos === 'object') ? s.pos : {};
    out.rate = Number(s.rate) > 0 ? Number(s.rate) : d.rate;
    out.eurChf = Number(s.eurChf) > 0 ? Number(s.eurChf) : d.eurChf;
    out.shop = (s.shop && typeof s.shop === 'object') ? s.shop : {};
    out.mode = typeof s.mode === 'string' ? s.mode : 'cheapest';
    return out;
  }
  function save() {
    try { localStorage.setItem('ar15-config', JSON.stringify(state)); } catch (e) { /* ignore */ }
  }
  const shareUrl = () => location.href.split('#')[0] + '#b=' + btoa(unescape(encodeURIComponent(JSON.stringify(state))));

  // ------------------------------------------------------------ származtatott adatok
  const base = () => BASE[state.base];
  const sel = (cat) => ITEM[state.sel[cat]];
  const colorOf = (it) => {
    if (!it) return D.COLORS.black.hex;
    const c = state.colors[it.id] || (it.colors ? it.colors[0] : 'black');
    return (D.COLORS[c] || D.COLORS.black).hex;
  };
  const colorName = (it) => {
    if (!it || !it.colors) return '';
    const c = state.colors[it.id] || it.colors[0];
    return D.COLORS[c] ? D.COLORS[c].name : '';
  };
  const colorIdOf = (it) => (it && it.colors) ? (state.colors[it.id] || it.colors[0]) : undefined;
  const isActive = (it) => it && !it.none;

  function factoryStyle(cat) {
    const b = base();
    if (cat === 'grip') return b.factoryGrip === 'bcm-mod3' ? { style: 'bcm3', angle: 17 } : { style: 'factory', angle: 23 };
    if (cat === 'stock') return b.factoryStock === 'bcm-mod0' ? { style: 'bcm', type: 'carbine', pad: 'own' } : { style: 'm4', type: 'carbine', pad: 'm4' };
    if (cat === 'muzzle') return b.factoryMuzzle === 'comp' ? { style: 'comp', len: 1.85 } : { style: 'a2', len: 1.75 };
    if (cat === 'ch') return { style: b.factoryCh === 'ambi' ? 'ambi' : b.factoryCh === 'bcm' ? 'bcm' : 'mil' };
    if (cat === 'trigger') return { style: b.factoryTrigger };
    return {};
  }

  function resolved(cat) {
    const it = sel(cat);
    if (!it) return null;
    if (it.factory) return Object.assign({}, it, factoryStyle(cat));
    return it;
  }

  function buildSpec() {
    const b = base();
    const st = resolved('stock');
    const pad = sel('buttpad');
    const lop = pad && pad.lop ? pad.lop : 0;
    let stockRear;
    if (st.type === 'rifle') stockRear = (st.style === 'prs' ? 11.0 : st.style === 'b5prec' ? 11.2 : 10.8) + lop;
    else stockRear = (st.style === 'slk' ? 8.4 : 9.2) + lop;
    const pick = (cat, extra) => { const it = resolved(cat); return isActive(it) ? Object.assign({ hex: colorOf(it), colorId: colorIdOf(it) }, it, extra || {}) : null; };
    const tg = state.multi.controls.includes('mp-trigguard') ? ITEM['mp-trigguard'] : null;
    return {
      barrel: b.barrel, hgLen: b.hgLen, hgStyle: b.hgStyle, finish: D.FINISHES[state.finish].hex, ambi: b.ambi,
      baseId: b.id, finishId: state.finish, pos: state.pos,
      grip: Object.assign({ hex: colorOf(resolved('grip')), colorId: colorIdOf(resolved('grip')) }, resolved('grip')),
      stock: Object.assign({ hex: colorOf(st), colorId: colorIdOf(st) }, st), stockRear,
      mountItem: isActive(sel('mount')) ? sel('mount') : null,
      pad: pad && !pad.factory ? pad.style : 'std',
      optic: pick('optic'), lpvo: pick('lpvo'), mount: (sel('mount') && !sel('mount').none) ? sel('mount').style : null,
      magnifier: pick('magnifier'), buis: pick('buis'), light: pick('light'), bipod: pick('bipod'),
      muzzle: Object.assign({ colorId: colorIdOf(resolved('muzzle')) }, resolved('muzzle')), trigger: resolved('trigger').style, ch: Object.assign({ hex: colorOf(resolved('ch')) }, resolved('ch')),
      mag: Object.assign({ hex: colorOf(sel('mag')), colorId: colorIdOf(sel('mag')) }, sel('mag')), sling: pick('sling'),
      mlok: state.multi.mlok.map(id => Object.assign({ hex: colorOf(ITEM[id]), colorId: colorIdOf(ITEM[id]) }, ITEM[id])),
      controls: state.multi.controls.map(id => ITEM[id].style),
      tguardHex: tg ? colorOf(tg) : null,
    };
  }

  function totals(metrics) {
    const b = base();
    const bp = priceOf(b);
    let price = bp.none ? 0 : bp.chf, weight = weightOf(b), missing = bp.none ? 1 : 0;
    const lines = [{ cat: 'base', label: `${b.brand} ${b.name}`, sub: D.FINISHES[state.finish].name, price: bp.chf, none: bp.none, p: bp.p, weight: weightOf(b) }];
    SINGLE_CATS.forEach(cat => {
      const it = sel(cat);
      if (!it || it.none && !(cat === 'buis' && b.factoryBuis)) return;
      if (it.factory) {
        if (cat === 'mag') { weight += 90; }
        return;
      }
      let w = weightOf(it);
      if (it.none) { // gyári BUIS eltávolítva
        weight -= FACTORY_W.buis; return;
      }
      if (FACTORY_W[cat] != null && !(cat === 'buis' && !b.factoryBuis)) w -= FACTORY_W[cat];
      const ip = priceOf(it); if (ip.none) missing++; else price += ip.chf; weight += w;
      lines.push({ cat, label: `${it.brand} ${it.name}`, sub: colorName(it), price: ip.chf, none: ip.none, p: ip.p, weight: weightOf(it) });
    });
    MULTI_CATS.forEach(cat => state.multi[cat].forEach(id => {
      const it = ITEM[id];
      const ip = priceOf(it); if (ip.none) missing++; else price += ip.chf; weight += weightOf(it);
      lines.push({ cat, label: `${it.brand} ${it.name}`, sub: colorName(it), price: ip.chf, none: ip.none, p: ip.p, weight: weightOf(it) });
    }));
    const st = sel('stock');
    if (st && st.type === 'rifle') {
      weight += RIFLE_KIT.weight; missing++;
      lines.push({ cat: 'stock', label: RIFLE_KIT.name, sub: 'nincs svájci bolti ár', price: null, none: true, weight: RIFLE_KIT.weight });
    }
    // hossz
    const spec = buildSpec();
    const fixed = RN.BF / RN.S + b.barrel + metrics.muzzleLen;
    const isRifle = spec.stock.type === 'rifle';
    const minS = isRifle ? spec.stockRear : spec.stockRear - 1.6;
    const maxS = isRifle ? spec.stockRear + (spec.stock.style === 'prs' ? 1.5 : 0) : spec.stockRear + 1.65;
    let oal = [(minS + fixed) * 2.54, (maxS + fixed) * 2.54];
    if (metrics.oalIn) oal = [metrics.oalIn * 2.54, metrics.oalIn * 2.54];
    return { price, weight, lines, oal, missing };
  }

  // ------------------------------------------------------------ kompatibilitás
  function issuesFor(metrics) {
    const b = base();
    const out = [];
    const add = (lvl, msg, cats) => out.push({ lvl, msg, cats: cats || [] });
    const optic = sel('optic'), lpvo = sel('lpvo'), mount = sel('mount'), mag = sel('mag'), buis = sel('buis');
    const lightIt = sel('light'), bip = sel('bipod'), st = sel('stock'), pad = sel('buttpad'), mz = sel('muzzle');

    if (isActive(lpvo) && lpvo.needsMount && (!mount || mount.none)) add('error', `A(z) ${lpvo.name} céltávcsőhöz szerelék kell – válassz az „Optikaszerelék” fülön.`, ['lpvo', 'mount']);
    if (mount && !mount.none && (!isActive(lpvo) || !lpvo.needsMount)) add('warn', 'A kiválasztott szerelék 30 mm-es tubusos céltávcsőhöz való; most nincs mire felszerelni.', ['mount']);
    const mag3 = sel('magnifier');
    if (isActive(mag3) && isActive(lpvo)) add('error', 'Nagyító LPVO-val / prizmás irányzékkal nem használható.', ['magnifier', 'lpvo']);
    else if (isActive(mag3) && !isActive(optic)) add('error', 'A nagyító red dot vagy holografikus irányzék mögé való – válassz red dotot.', ['magnifier', 'optic']);
    if (isActive(optic) && isActive(lpvo)) {
      if (!['micro', 'acro', 'mro'].includes(optic.style)) add('error', `A(z) ${optic.name} túl nagy offset (45°) red dotnak LPVO mellé. Kompakt irányzék (pl. T-2, ACRO, 403B) való erre.`, ['optic', 'lpvo']);
      else add('info', 'Red dot + LPVO: a red dot offset (45°) helyzetbe kerül – offset szerelék szükséges (nincs benne az árban).', ['optic']);
    }
    if (metrics.railClash.length) add('error', 'Nem fér el a felső sínen: ' + [...new Set(metrics.railClash)].join(', ') + '.', ['optic', 'lpvo', 'magnifier', 'buis']);
    if (isActive(buis) && (buis.style === 'mbus' || buis.style === 'mbuspro' || buis.factory) && isActive(lpvo)) add('warn', 'LPVO mellé a felső sínre szerelt BUIS nem használható – az MBUS Pro Offset a jobb választás.', ['buis']);
    if (!isActive(optic) && !isActive(lpvo) && !isActive(buis)) add('warn', 'Nincs irányzék a fegyveren.', ['optic', 'buis']);
    if (isActive(lightIt) && lightIt.needsRail && !state.multi.mlok.includes('mp-rail5')) add('error', `A(z) ${lightIt.name} Picatinny sínt igényel – tedd fel az „M-LOK Picatinny sín” elemet.`, ['light', 'mlok']);
    if (isActive(bip) && state.multi.mlok.some(id => ITEM[id].group === 'fgrip') && b.hgLen < 12) add('warn', `A ${b.hgLen}" előagy alján szűkös a hely bipodnak és előmarkolatnak együtt.`, ['bipod', 'mlok']);
    if (pad && !pad.factory) {
      if (st.factory || !pad.fits.includes(st.pad)) add('error', `A(z) ${pad.name} nem kompatibilis a kiválasztott tussal (${st.factory ? 'gyári tus' : st.name}).`, ['buttpad', 'stock']);
    }
    if (st && st.type === 'rifle') add('info', 'A precíziós tus rifle (A2) puffercsövet, -rugót és -puffert igényel – becsült áron hozzáadva.', ['stock']);
    if (mag && mag.rounds > 10) add('info', '10 töltényesnél nagyobb tárak birtoklása hosszú fegyverhez az uniós és a hazai szabályozás szerint korlátozott lehet – ellenőrizd a jogosultságodat.', ['mag']);
    if (mz && mz.style === 'can') {
      add('info', 'Hangtompító: beszerzése és használata engedélyhez kötött – ellenőrizd a hatályos szabályozást.', ['muzzle']);
      if (b.barrel <= 11) add('warn', 'Rövid cső + hangtompító: erős túlgázosodás várható, állítható gázblokk / nehezebb puffer javasolt.', ['muzzle']);
      if (sel('ch').id !== 'rd-raptorsd') add('info', 'Hangtompítóhoz a Radian Raptor-SD felhúzókar csökkenti az arcba csapó gázt.', ['ch']);
    }
    if (b.ambi && state.multi.controls.includes('rd-talon')) add('info', `A ${b.name} gyárilag ambidex biztosítóval szerelt – a Talon ergonómiai csere.`, ['controls']);
    if (!sel('trigger').factory) add('info', 'Elsütőcserét fegyverműves végezze; ellenőrizd a csapátmérőt (mil-spec 0,154").', ['trigger']);
    if (b.hgEstimated) add('info', `A(z) ${b.name} előagyhossza a megjelenítéshez becsült érték.`, ['base']);
    return out;
  }

  // ------------------------------------------------------------ megjelenítés: SVG
  const svg = $('#rifle');
  let currentVB = null, baseVB = null, zoom = { k: 1, dx: 0, dy: 0 };

  function renderRifle(animateAssembly) {
    const spec = buildSpec();
    const r = RN.render(spec);
    $('#parts').innerHTML = r.svg;
    // bekerülő alkatrész animáció
    if (lastChangedCat) {
      const map = { optic: 'optic', lpvo: 'lpvo', mount: 'lpvo' };
      const target = map[lastChangedCat] || lastChangedCat;
      $$(`#parts .part[data-cat="${target}"] .inner`).forEach(g => { g.classList.remove('enter'); void g.getBoundingClientRect(); g.classList.add('enter'); });
      lastChangedCat = null;
    }
    // címkék + határoló dobozok
    const parts = $$('#parts .part');
    let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9, eMinX = 1e9, eMinY = 1e9, eMaxX = -1e9, eMaxY = -1e9;
    let labels = '';
    parts.forEach(p => {
      const bb = p.getBBox();
      if (!bb.width) return;
      const cs = p.getAttribute('style');
      const ex = parseFloat(/--ex:(-?[\d.]+)/.exec(cs)[1]), ey = parseFloat(/--ey:(-?[\d.]+)/.exec(cs)[1]);
      if (p.dataset.cat !== 'sling') {
        minX = Math.min(minX, bb.x); minY = Math.min(minY, bb.y); maxX = Math.max(maxX, bb.x + bb.width); maxY = Math.max(maxY, bb.y + bb.height);
      }
      if (p.dataset.cat !== 'sling') {
        eMinX = Math.min(eMinX, bb.x + ex); eMinY = Math.min(eMinY, bb.y + ey - 26); eMaxX = Math.max(eMaxX, bb.x + bb.width + ex); eMaxY = Math.max(eMaxY, bb.y + bb.height + ey);
        const lx = bb.x + bb.width / 2 + ex, ly = bb.y + ey - 12;
        labels += `<g class="label" style="--d:${(Math.random() * 0.2).toFixed(2)}s"><text x="${lx}" y="${ly}" text-anchor="middle">${p.dataset.label}</text></g>`;
      }
    });
    $('#labels').innerHTML = labels;
    const pad = 40;
    const vbA = [minX - pad, minY - pad - 10, maxX - minX + 2 * pad, maxY - minY + 2 * pad + 60];
    const vbE = [eMinX - pad, eMinY - pad, eMaxX - eMinX + 2 * pad, eMaxY - eMinY + 2 * pad];
    svg.dataset.vba = vbA.join(',');
    svg.dataset.vbe = vbE.join(',');
    // padló, árnyék, tükröződés
    const floorY = maxY + 26;
    $('#floor').innerHTML = `<ellipse cx="${(minX + maxX) / 2}" cy="${floorY}" rx="${(maxX - minX) / 2}" ry="14" fill="#000" opacity=".55" filter="url(#soft)"/>`;
    $('#reflection').setAttribute('transform', `translate(0,${2 * floorY}) scale(1,-1)`);
    $('#reflMaskRect').setAttribute('y', floorY);
    $('#reflMaskRect').setAttribute('x', minX - 2000);
    $('#reflGrad').setAttribute('y1', floorY); $('#reflGrad').setAttribute('y2', floorY + 180);
    baseVB = exploded ? vbE : vbA;
    if (animateAssembly) assemble(vbA, vbE);
    else animateVB(applyZoom(baseVB));
    return r.metrics;
  }

  function applyZoom(vb) {
    const [x, y, w, h] = vb;
    const k = zoom.k;
    const nw = w / k, nh = h / k;
    return [x + (w - nw) / 2 + zoom.dx, y + (h - nh) / 2 + zoom.dy, nw, nh];
  }

  let vbAnim = null;
  function animateVB(target, dur) {
    dur = dur == null ? 650 : dur;
    if (!currentVB || dur === 0) { currentVB = target.slice(); svg.setAttribute('viewBox', target.join(' ')); return; }
    const from = currentVB.slice(), t0 = performance.now();
    cancelAnimationFrame(vbAnim);
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      currentVB = from.map((v, i) => v + (target[i] - v) * e);
      svg.setAttribute('viewBox', currentVB.join(' '));
      if (p < 1) vbAnim = requestAnimationFrame(step);
    };
    vbAnim = requestAnimationFrame(step);
  }

  const ORDER = ['lower', 'trigger', 'grip', 'tube', 'stock', 'upper', 'bcg', 'ch', 'barrel', 'handguard', 'muzzle', 'mag', 'controls', 'mlok', 'bipod', 'light', 'buis', 'lpvo', 'optic', 'magnifier', 'sling'];
  function assemble(vbA, vbE) {
    const stage = $('#stage');
    stage.classList.add('no-anim', 'exploded');
    animateVB(vbE, 0);
    void stage.offsetWidth;
    stage.classList.remove('no-anim');
    $$('#parts .part').forEach(p => {
      const i = ORDER.indexOf(p.dataset.cat);
      p.style.setProperty('--delay', (0.25 + (i < 0 ? 10 : i) * 0.07).toFixed(2) + 's');
    });
    setTimeout(() => {
      stage.classList.remove('exploded');
      exploded = false; syncExplodeBtn();
      zoom = { k: 1, dx: 0, dy: 0 };
      baseVB = vbA;
      setTimeout(() => animateVB(vbA, 1100), 250);
      setTimeout(() => $$('#parts .part').forEach(p => p.style.removeProperty('--delay')), 2600);
    }, 500);
  }

  function setExploded(v) {
    exploded = v;
    $('#stage').classList.toggle('exploded', v);
    zoom = { k: 1, dx: 0, dy: 0 };
    baseVB = (v ? svg.dataset.vbe : svg.dataset.vba).split(',').map(Number);
    animateVB(baseVB, 800);
    syncExplodeBtn();
  }
  function syncExplodeBtn() { $('#btnExplode').classList.toggle('on', exploded); $('#btnExplode').textContent = exploded ? 'Összeszerelt nézet' : 'Robbantott nézet'; }

  // zoom / pan
  function initZoom() {
    const stage = $('#stage');
    stage.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && !e.metaKey && !stage.classList.contains('zoomable')) return;
      e.preventDefault();
      zoom.k = Math.max(1, Math.min(5, zoom.k * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      if (zoom.k === 1) { zoom.dx = 0; zoom.dy = 0; }
      animateVB(applyZoom(baseVB), 120);
    }, { passive: false });
    let drag = null;
    svg.addEventListener('pointerdown', (e) => { if (zoom.k > 1) { drag = { x: e.clientX, y: e.clientY, dx: zoom.dx, dy: zoom.dy }; svg.setPointerCapture(e.pointerId); } });
    svg.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const scale = currentVB[2] / svg.clientWidth;
      zoom.dx = drag.dx - (e.clientX - drag.x) * scale;
      zoom.dy = drag.dy - (e.clientY - drag.y) * scale;
      animateVB(applyZoom(baseVB), 0);
    });
    svg.addEventListener('pointerup', () => { drag = null; });
    svg.addEventListener('dblclick', () => { zoom = { k: 1, dx: 0, dy: 0 }; animateVB(applyZoom(baseVB)); });
    $('#btnZoomIn').onclick = () => { zoom.k = Math.min(5, zoom.k * 1.4); animateVB(applyZoom(baseVB), 300); };
    $('#btnZoomOut').onclick = () => { zoom.k = Math.max(1, zoom.k / 1.4); if (zoom.k === 1) zoom.dx = zoom.dy = 0; animateVB(applyZoom(baseVB), 300); };
  }

  // kattintás / hover az alkatrészen
  const PART_TO_TAB = { tube: 'stock', lower: 'base', upper: 'base', barrel: 'base', handguard: 'base', bcg: 'base' };
  function initPartInteraction() {
    const tip = $('#tip');
    $('#parts').addEventListener('mousemove', (e) => {
      const p = e.target.closest('.part');
      $$('#parts .part.hl').forEach(x => x !== p && x.classList.remove('hl'));
      if (!p) { tip.hidden = true; return; }
      p.classList.add('hl');
      tip.hidden = false;
      tip.textContent = partTitle(p.dataset.cat);
      const r = $('#stage').getBoundingClientRect();
      tip.style.left = (e.clientX - r.left + 14) + 'px';
      tip.style.top = (e.clientY - r.top + 14) + 'px';
    });
    $('#parts').addEventListener('mouseleave', () => { tip.hidden = true; $$('#parts .part.hl').forEach(x => x.classList.remove('hl')); });
    $('#parts').addEventListener('click', (e) => {
      const p = e.target.closest('.part');
      if (!p) return;
      const tab = PART_TO_TAB[p.dataset.cat] || p.dataset.cat;
      if (D.CATEGORIES.some(c => c.id === tab)) { activeTab = tab; brandFilter = 'Összes'; renderTabs(); renderPanel(); $('#builder').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
    });
  }
  function partTitle(cat) {
    const b = base();
    if (['lower', 'upper', 'barrel', 'handguard', 'bcg', 'base'].includes(cat)) return `${b.brand} ${b.name} – ${RN.LABELS[cat]}`;
    if (cat === 'tube') return 'Puffercső';
    if (MULTI_CATS.includes(cat)) return state.multi[cat].map(id => ITEM[id].name).join(', ');
    const it = sel(cat);
    if (!it) return RN.LABELS[cat] || cat;
    return it.factory ? `${RN.LABELS[cat] || cat}: gyári` : `${it.brand} ${it.name}`;
  }

  // ------------------------------------------------------------ UI: fülek, kártyák
  function renderTabs(issues) {
    issues = issues || currentIssues;
    const errCats = new Set(issues.filter(i => i.lvl === 'error').flatMap(i => i.cats));
    const tabs = D.CATEGORIES.map(c => {
      let sub = '';
      if (c.id === 'base') sub = base().name;
      else if (c.multi) sub = state.multi[c.id].length ? state.multi[c.id].length + ' db' : '—';
      else { const it = sel(c.id); sub = it.factory ? 'gyári' : it.none ? '—' : it.brand; }
      return `<button class="tab${activeTab === c.id ? ' active' : ''}${errCats.has(c.id) ? ' err' : ''}" data-tab="${c.id}"><span>${c.short}</span><small>${sub}</small></button>`;
    }).join('') + `<button class="tab${activeTab === 'brands' ? ' active' : ''}" data-tab="brands"><span>Márkák</span><small>áttekintés</small></button>`;
    $('#tabs').innerHTML = tabs;
    $$('#tabs .tab').forEach(t => t.onclick = () => { activeTab = t.dataset.tab; brandFilter = 'Összes'; renderTabs(); renderPanel(); });
    const at = $('#tabs .tab.active');
    if (at && at.scrollIntoView) at.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }

  function swatches(it, selected) {
    if (!it.colors || it.colors.length < 2) return '';
    const cur = state.colors[it.id] || it.colors[0];
    return `<div class="sw">${it.colors.map(c => `<button class="swatch${c === cur ? ' on' : ''}" data-item="${it.id}" data-color="${c}" title="${D.COLORS[c].name}" style="--c:${D.COLORS[c].hex}"></button>`).join('')}<span class="swname">${D.COLORS[cur].name}</span></div>`;
  }

  function wouldError(cat, id) {
    // gyors szimuláció: hiba lenne-e ezzel a választással (a jelenlegi hibákhoz képest új)
    const saved = JSON.stringify(state);
    if (MULTI_CATS.includes(cat)) { if (!state.multi[cat].includes(id)) toggleMulti(cat, id, true); }
    else state.sel[cat] = id;
    const m = RN.render(buildSpec()).metrics;
    const errs = issuesFor(m).filter(i => i.lvl === 'error').map(i => i.msg);
    state = JSON.parse(saved);
    const now = new Set(currentIssues.filter(i => i.lvl === 'error').map(i => i.msg));
    return errs.filter(e => !now.has(e));
  }

  function renderPanel() {
    const panel = $('#panel');
    if (activeTab === 'base') return renderBasePanel(panel);
    if (activeTab === 'brands') return renderBrandsPanel(panel);
    const cat = D.CATEGORIES.find(c => c.id === activeTab);
    const items = byCat(cat.id);
    const brands = ['Összes', ...new Set(items.filter(i => !i.factory && !i.none).map(i => i.brand))];
    if (!brands.includes(brandFilter)) brandFilter = 'Összes';
    const shown = items.filter(i => brandFilter === 'Összes' || i.brand === brandFilter || i.factory || i.none);
    const selectedIds = cat.multi ? state.multi[cat.id] : [state.sel[cat.id]];
    const hint = cat.multi ? '<p class="hint">Több elem is választható. Az előmarkolat-félék közül egyszerre egy lehet fent.</p>' : '';
    const posHtml = (lastMetrics && lastMetrics.photo && POS_CATS[cat.id]) ? posRow(cat.id) : '';
    panel.innerHTML = `
      <div class="panel-head"><h2>${cat.name}</h2>
        <div class="chips">${brands.map(b => `<button class="chip${b === brandFilter ? ' on' : ''}" data-brand="${b}">${b}</button>`).join('')}</div></div>
      ${hint}${posHtml}
      <div class="grid">${shown.map(it => card(it, selectedIds.includes(it.id), cat)).join('')}</div>`;
    const sl = $('#posSlider', panel);
    if (sl) {
      sl.oninput = () => { state.pos[cat.id] = Number(sl.value); $('#posVal').textContent = fmtPos(sl.value); const m = renderRifle(false); lastMetrics = m; currentIssues = issuesFor(m); renderSummary(totals(m), currentIssues); renderPhotoNote(m); save(); };
      $('#posReset', panel).onclick = () => { state.pos[cat.id] = 0; sl.value = 0; sl.oninput(); };
    }
    $$('.chip', panel).forEach(c => c.onclick = () => { brandFilter = c.dataset.brand; renderPanel(); });
    $$('.card', panel).forEach(c => c.onclick = (e) => {
      if (e.target.closest('.swatch, .shopsel, .src a')) return;
      choose(cat.id, c.dataset.id);
    });
    bindShopSelects(panel);
    $$('.swatch', panel).forEach(s => s.onclick = () => {
      state.colors[s.dataset.item] = s.dataset.color;
      const it = ITEM[s.dataset.item];
      if (cat.multi) { if (!state.multi[cat.id].includes(it.id)) toggleMulti(cat.id, it.id, true); }
      else state.sel[cat.id] = it.id;
      lastChangedCat = cat.id;
      update();
    });
  }

  const POS_CATS = { optic: [-3, 6, 'a sínen'], lpvo: [-2, 4, 'a sínen'], magnifier: [-3, 3, 'a sínen'], buis: [-1, 2, 'a sínen'], light: [-4, 2, 'az előagyon'], bipod: [-4, 2, 'az előagyon'] };
  const fmtPos = v => (Number(v) >= 0 ? '+' : '−') + Math.abs(Number(v)).toFixed(1).replace('.', ',') + '"';
  function posRow(cat) {
    const [lo, hi, where] = POS_CATS[cat], v = state.pos[cat] || 0;
    return `<div class="posrow"><label for="posSlider">Pozíció ${where}</label><input id="posSlider" type="range" min="${lo}" max="${hi}" step="0.1" value="${v}"><span id="posVal">${fmtPos(v)}</span><button id="posReset" class="btn small ghost">Alap</button></div>`;
  }
  function srcHtml(it) {
    const q = (it.price || PRICES[it.id]) ? priceOf(it) : null;
    if (!q) return '';
    if (q.none) return '<div class="src est">nincs svájci bolti ár</div>';
    const offs = offersOf(it);
    const sel = offs.length > 1
      ? `<select class="shopsel" data-item="${it.id}" aria-label="Bolt választása">${offs.map(o => `<option value="${o.shop}"${o.shop === q.p.shop ? ' selected' : ''}>${shopTag(o.shop)} · ${fmtCHF(variantOf(o, it).chf)}${variantOf(o, it).in_stock === false ? ' (nincs raktáron)' : ''}</option>`).join('')}</select>`
      : '';
    return `<div class="src"><a href="${q.p.url}" target="_blank" rel="noopener">${shopTag(q.p.shop)} ↗</a>${shopBadge(q.p.shop)}${q.p.in_stock === false ? ' · nincs raktáron' : ''}${sel}</div>`;
  }
  function bindShopSelects(root) {
    $$('.shopsel', root).forEach(sl => {
      sl.onclick = (e) => e.stopPropagation();
      sl.onchange = () => { state.shop[sl.dataset.item] = sl.value; update(true); };
    });
  }
  function card(it, on, cat) {
    const b = base();
    let name = it.name, brand = it.brand, desc = '';
    if (it.factory) {
      brand = b.brand;
      const fs = { grip: b.factoryGrip === 'bcm-mod3' ? 'BCM Gunfighter Mod 3' : 'Gyári markolat', stock: b.factoryStock === 'bcm-mod0' ? 'BCM Gunfighter Mod 0' : 'Gyári 6-pozíciós tus', muzzle: b.factoryMuzzle === 'comp' ? 'Gyári kompenzátor' : 'Gyári A2 lángrejtő', trigger: b.factoryTrigger === 'flat' ? 'Gyári Dynamic elsütő' : 'Gyári elsütő', ch: b.factoryCh === 'ambi' ? 'Gyári ambidex felhúzókar' : b.factoryCh === 'bcm' ? 'BCM Gunfighter' : 'Mil-spec felhúzókar', buis: 'Gyári flip-up irányzék', mag: 'Gyári 10-es tár', buttpad: 'A tus saját tustalpa' };
      name = fs[cat.id] || it.name;
      desc = 'Az alapfegyverrel szállítva';
    }
    if (it.factory && cat.id === 'buis' && !b.factoryBuis) return '';
    const errs = on ? [] : wouldError(cat.id, it.id);
    const specs = (it.specs || []).map(s => `<span>${s}</span>`).join('');
    return `<div class="card${on ? ' on' : ''}${errs.length ? ' bad' : ''}" data-id="${it.id}" role="button" tabindex="0">
      <div class="card-top"><span class="brand">${brand}</span>${on ? '<span class="check">Kiválasztva</span>' : ''}</div>
      <div class="name">${name}</div>
      ${desc ? `<div class="desc">${desc}</div>` : ''}
      ${specs ? `<div class="specs">${specs}</div>` : ''}
      ${swatches(it, on)}
      <div class="card-bot">${it.factory ? '<span class="price">benne az árban</span>' : (() => { const q = priceOf(it); return `<span class="price${q.none ? ' none' : ''}">${priceTxt(q)}</span>`; })()}${weightOf(it) ? `<span class="w">${weightOf(it)} g</span>` : ''}</div>
      ${srcHtml(it)}
      ${errs.length ? `<div class="warnline">${errs[0]}</div>` : ''}
    </div>`;
  }

  function renderBasePanel(panel) {
    const b = base();
    panel.innerHTML = `
      <div class="panel-head"><h2>Alapfegyver</h2><p class="hint">Válaszd ki az alap puskát, majd a színét. A kiegészítők a többi fülön cserélhetők.</p></div>
      <div class="grid grid-base">${D.BASES.map(x => `
        <div class="card base${x.id === b.id ? ' on' : ''}" data-id="${x.id}" role="button" tabindex="0">
          <div class="card-top"><span class="brand">${x.brand}</span><span class="tagline">${x.tag}</span></div>
          <div class="name big">${x.name}</div>
          <div class="desc">${x.desc}</div>
          <div class="specs"><span>${String(x.barrel).replace('.', ',')}" cső</span><span>${x.cal}</span><span>${x.twist}</span><span>${String(x.hgLen).replace('.', ',')}" előagy${x.hgEstimated ? '*' : ''}</span>${x.ambi ? '<span>Ambidex</span>' : ''}</div>
          <div class="sw">${x.finishes.map(f => `<button class="swatch fin${x.id === b.id && f === state.finish ? ' on' : ''}${(window.AR_PHOTOS.get(x.id, 'base', f) || {}).exactColor ? '' : ' nophoto'}" data-base="${x.id}" data-finish="${f}" title="${D.FINISHES[f].name}${(window.AR_PHOTOS.get(x.id, 'base', f) || {}).exactColor ? ((window.AR_PHOTOS.get(x.id, 'base', f) || {}).derived ? ' (színezett fotó)' : '') : ' (nincs fotó)'}" style="--c:${D.FINISHES[f].hex}"></button>`).join('')}
            <span class="swname">${x.id === b.id ? D.FINISHES[state.finish].name : x.finishes.map(f => D.FINISHES[f].name).join(' / ')}</span></div>
          <div class="card-bot"><span class="price">${priceTxt(priceOf(x))}</span><span class="w">${fmtKg(weightOf(x))}</span></div>
          ${srcHtml(x)}
          ${x.oalNote ? `<div class="desc small">${x.oalNote}</div>` : ''}
        </div>`).join('')}</div>
      <p class="hint small">* Becsült érték a megjelenítéshez. Az árak tájékoztató jellegű európai irányárak; a színváltozatok elérhetősége modellenként és kereskedőnként eltérhet.</p>`;
    $$('.card.base', panel).forEach(c => c.onclick = (e) => {
      if (e.target.closest('.shopsel, .src a')) return;
      const sw = e.target.closest('.swatch');
      setBase(c.dataset.id, sw ? sw.dataset.finish : null);
    });
    bindShopSelects(panel);
  }

  function renderBrandsPanel(panel) {
    const brands = Object.keys(D.BRANDS);
    panel.innerHTML = `<div class="panel-head"><h2>Márkák és kínálatuk kategóriánként</h2>
      <p class="hint">10 vezető AR-15 kiegészítő-márka, kiegészítve a lámpa-, bipod- és szíjkategória specialistáival. Kattints egy termékre a felszereléshez.</p></div>
      <div class="brands">${brands.map(br => {
        const its = D.ITEMS.filter(i => i.brand === br);
        const cats = D.CATEGORIES.filter(c => its.some(i => i.cat === c.id));
        return `<section class="brand-box${D.BRANDS[br].core ? '' : ' spec'}"><h3>${br}${D.BRANDS[br].core ? '' : ' <small>specialista</small>'}</h3><p>${D.BRANDS[br].note}</p>
          ${cats.map(c => `<div class="bcat"><h4>${c.name}</h4><ul>${its.filter(i => i.cat === c.id).map(i => {
            const on = c.multi ? state.multi[c.id].includes(i.id) : state.sel[c.id] === i.id;
            return `<li class="${on ? 'on' : ''}" data-cat="${c.id}" data-id="${i.id}"><span>${i.name}</span><b>${priceTxt(priceOf(i))}</b></li>`;
          }).join('')}</ul></div>`).join('')}</section>`;
      }).join('')}</div>`;
    $$('.brands li', panel).forEach(li => li.onclick = () => choose(li.dataset.cat, li.dataset.id, true));
  }

  function toggleMulti(cat, id, forceOn) {
    const list = state.multi[cat];
    const it = ITEM[id];
    if (list.includes(id) && !forceOn) { state.multi[cat] = list.filter(x => x !== id); return; }
    if (list.includes(id)) return;
    let next = list.slice();
    if (it.group) next = next.filter(x => ITEM[x].group !== it.group);
    next.push(id);
    state.multi[cat] = next;
  }

  function choose(cat, id, keepTab) {
    if (MULTI_CATS.includes(cat)) toggleMulti(cat, id);
    else state.sel[cat] = id;
    // kényelmi automatizmus: LPVO-hoz szerelék, ha nincs
    if (cat === 'lpvo' && ITEM[id].needsMount && sel('mount').none) state.sel.mount = 'vx-cant30';
    if (cat === 'lpvo' && (ITEM[id].none || !ITEM[id].needsMount)) state.sel.mount = 'mount-none';
    lastChangedCat = cat;
    update(keepTab);
  }

  function setBase(id, finish) {
    const prevBase = state.base;
    state.base = id;
    const b = BASE[id];
    state.finish = finish && b.finishes.includes(finish) ? finish : (b.finishes.includes(state.finish) ? state.finish : b.finishes[0]);
    if (!b.factoryBuis && state.sel.buis === 'buis-factory') state.sel.buis = 'buis-none';
    if (b.factoryBuis && prevBase !== id && state.sel.buis === 'buis-none' && !BASE[prevBase].factoryBuis) state.sel.buis = 'buis-factory';
    update(false, prevBase !== id);
  }

  // ------------------------------------------------------------ összesítő
  let currentIssues = [], lastMetrics = null;
  function renderPhotoNote(m) {
    const el = $('#photoNote');
    const b = base();
    if (!m.photo) {
      el.className = 'photo-note warn';
      el.innerHTML = `<b>Vázlatnézet.</b> A(z) ${b.brand} ${b.name} modellhez nincs betöltött hivatalos fotó, ezért rajzolt nézet látszik.`;
      return;
    }
    const p = m.photo, bits = [];
    bits.push(`<b>Fotómód.</b> ${b.brand} ${b.name} – a gyártó hivatalos oldalnézeti fotója, valós méretarányban (${p.ppi} px/hüvelyk).`);
    if (!p.finishExact) bits.push(`A(z) ${D.FINISHES[state.finish].name} színhez nincs hivatalos fotó, a ${D.FINISHES[p.finishHave] ? D.FINISHES[p.finishHave].name : p.finishHave} kivitelt látod.`);
    if (p.finishDerived) bits.push(`<b>Színezett változat:</b> a gyártó ehhez a kivitelhez nem közöl nagyfelbontású fotót, ezért a hivatalos fekete fotó tokját és előagyát a bolti fotókról mért Cerakote-színre igazítottam (a tus, markolat, tár, cső és kezelőszervek feketék maradnak).`);
    if (p.missing.length) bits.push(`<span class="miss">${p.missing.length} kiegészítőhöz még nincs fotó (szaggatott jelölés): ${[...new Set(p.missing.map(x => x.name))].slice(0, 4).join(', ')}${p.missing.length > 4 ? '…' : ''}.</span>`);
    if (p.approx.length) bits.push(`Más színű fotó látszik: ${p.approx.map(x => x.name).join(', ')}.`);
    bits.push('Szíj, elsütő, felhúzókar és kezelőszervek nem jelennek meg külön a képen.');
    el.className = 'photo-note' + (p.missing.length || !p.finishExact ? ' warn' : '');
    el.innerHTML = bits.join(' ');
  }
  function shopInfoHtml(sh) {
    const i = SHOPINFO[sh];
    if (!i) return '';
    const dist = i.visit ? i.visit : (i.km != null ? `kb. ${i.km} km a ${SHOPREF.plz} ${SHOPREF.place} térségétől (légvonal, becslés)` : '');
    return `<div class="ordinfo">${esc(i.name)} · ${esc(i.addr)}${dist ? '<br>' + dist : ''}<br>${esc(i.shipNote || '')}` +
      `${i.tel ? '<br>Tel.: ' + esc(i.tel) : ''}${i.url ? ' · <a href="' + i.url + '" target="_blank" rel="noopener">a bolt oldala ↗</a>' : ''}</div>`;
  }
  function orderText(t) {
    const groups = {}, est = [];
    t.lines.forEach(l => { if (l.p) (groups[l.p.shop] = groups[l.p.shop] || []).push(l); else est.push(l); });
    const b = base();
    let out = `Rendelési lista – ${b.brand} ${b.name}\n`;
    Object.keys(groups).forEach(sh => {
      const ls = groups[sh];
      const si = SHOPINFO[sh];
      out += `\n== ${shopTag(sh)}${hidePrices ? '' : ' – ' + fmtCHF(ls.reduce((a, l) => a + l.price, 0))} ==\n${si ? si.name + ', ' + si.addr + (si.visit ? ' (' + si.visit + ')' : '') + '\n' : ''}`;
      ls.forEach(l => { out += `- ${l.label}${hidePrices ? '' : ' – ' + fmtCHF(l.price)}${l.p.in_stock === false ? ' [nincs raktáron]' : ''}\n  ${l.p.url}\n`; });
    });
    if (est.length) { out += `\n== Még nincs bolti ár ==\n`; est.forEach(l => { out += `- ${l.label}\n`; }); }
    return out;
  }
  function renderOrder(t) {
    const box = $('#orderBox');
    if (!box) return;
    const groups = {}, est = [];
    t.lines.forEach(l => { if (l.p) (groups[l.p.shop] = groups[l.p.shop] || []).push(l); else est.push(l); });
    const shops = Object.keys(groups);
    let html = '';
    if (shops.length) html += `<div class="ordhead"><b>Hol rendeld</b><span>${shops.length} bolt</span><button id="ordCopy" class="btn small ghost" type="button">Lista másolása</button></div>`;
    if (state.mode !== 'cheapest' && shops.length > 1) html += `<p class="ordnote">Nem minden tétel kapható a(z) ${shopTag(state.mode)} boltban, ezért ${shops.length} boltba kell rendelni.</p>`;
    shops.forEach(sh => {
      const ls = groups[sh];
      html += `<div class="ord"><h4><span>${shopTag(sh)}</span><span class="price">${fmtCHF(ls.reduce((a, l) => a + l.price, 0))}</span></h4>${shopInfoHtml(sh)}<ul>` +
        ls.map(l => `<li><a href="${l.p.url}" target="_blank" rel="noopener">${l.label}</a>${l.p.in_stock === false ? '<em> · nincs raktáron</em>' : ''}<span class="price">${fmtCHF(l.price)}</span></li>`).join('') + '</ul></div>';
    });
    if (est.length) html += `<div class="ord est"><h4><span>Még nincs svájci bolti ár</span></h4><ul>${est.map(l => `<li>${l.label}</li>`).join('')}</ul></div>`;
    box.innerHTML = html;
    const cp = $('#ordCopy');
    if (cp) cp.onclick = async () => {
      try { await navigator.clipboard.writeText(orderText(t)); toast('A rendelési lista a vágólapon van.'); }
      catch (e) { toast('A vágólap nem érhető el ebben a nézetben.'); }
    };
  }
  function buildShopMode() {
    const sel = $('#shopMode');
    if (!sel) return;
    sel.innerHTML = `<option value="cheapest">Legolcsóbb tételenként</option>` + SHOPS.filter(shopOk).map(sh => `<option value="${sh}">Egy boltból: ${shopTag(sh)}</option>`).join('');
    if (state.mode !== 'cheapest' && !SHOPS.filter(shopOk).includes(state.mode)) state.mode = 'cheapest';
    sel.value = state.mode;
    sel.onchange = () => { state.mode = sel.value; update(true); };
  }
  function renderSummary(t, issues) {
    const b = base();
    $('#sumTitle').textContent = `${b.brand} ${b.name}`;
    $('#sumFinish').textContent = D.FINISHES[state.finish].name;
    $('#statPrice').textContent = fmtCHF(t.price);
    $('#statHuf').textContent = '≈ ' + fmtHUF(t.price * state.rate) + (t.missing ? ` · ${t.missing} tétel ára nincs` : '');
    $('#statWeight').textContent = fmtKg(t.weight);
    $('#statOal').textContent = Math.abs(t.oal[1] - t.oal[0]) < 0.5 ? `${t.oal[0].toFixed(0)} cm` : `${t.oal[0].toFixed(0)}–${t.oal[1].toFixed(0)} cm`;
    $('#statBarrel').textContent = `${String(b.barrel).replace('.', ',')}" · ${b.cal}`;
    $('#sumList').innerHTML = t.lines.map(l => `<li data-cat="${l.cat}"><div><b>${l.label}</b>${(l.sub || l.p) ? `<small>${[l.sub, l.p ? l.p.shop : ''].filter(Boolean).join(' · ')}</small>` : ''}</div><span>${l.none ? '—' : fmtCHF(l.price)}</span></li>`).join('');
    $$('#sumList li').forEach(li => li.onclick = () => { activeTab = li.dataset.cat; renderTabs(); renderPanel(); });
    $('#sumTotal').textContent = fmtCHF(t.price);
    $('#sumHuf').textContent = '≈ ' + fmtHUF(t.price * state.rate);
    renderOrder(t);
    $('#sumEst').textContent = t.missing ? `${t.missing} tétel ára hiányzik (nincs svájci bolti ár): az összeg csak a bolti áras tételeket tartalmazza.` : 'Minden tétel svájci bolti ár.';
    const order = { error: 0, warn: 1, info: 2 };
    const sorted = issues.slice().sort((a, c) => order[a.lvl] - order[c.lvl]);
    const nErr = issues.filter(i => i.lvl === 'error').length;
    $('#compatState').className = 'compat-state ' + (nErr ? 'bad' : 'ok');
    $('#compatState').textContent = nErr ? `${nErr} kompatibilitási hiba` : 'Kompatibilis összeállítás';
    $('#issues').innerHTML = sorted.map(i => `<li class="${i.lvl}">${i.msg}</li>`).join('');
  }

  // ------------------------------------------------------------ fő frissítés
  function update(keepTab, animate) {
    const metrics = renderRifle(animate);
    currentIssues = issuesFor(metrics);
    const t = totals(metrics);
    renderSummary(t, currentIssues);
    renderPhotoNote(metrics);
    lastMetrics = metrics;
    renderTabs(currentIssues);
    renderPanel();
    $('#stageCaption').textContent = `${base().brand} ${base().name} · ${D.FINISHES[state.finish].name}`;
    save();
    history.replaceState(null, '', location.pathname + location.search);
  }

  // ------------------------------------------------------------ export, megosztás
  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg; el.classList.add('show');
    clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.remove('show'), 2200);
  }
  async function share() {
    const url = shareUrl();
    try { await navigator.clipboard.writeText(url); toast('Link a vágólapon – bárki megnyithatja ezt az összeállítást.'); }
    catch (e) { prompt('Másold ki a linket:', url); }
  }
  async function exportPng() {
    const clone = svg.cloneNode(true);
    const vb = (svg.getAttribute('viewBox') || '').split(' ').map(Number);
    const W = 2400, H = Math.round(W * vb[3] / vb[2]);
    clone.setAttribute('width', W); clone.setAttribute('height', H);
    clone.querySelectorAll('.label').forEach(n => { if (!exploded) n.remove(); else n.setAttribute('opacity', 1); });
    if (exploded) clone.querySelectorAll('.part').forEach(p => {
      const cs = p.getAttribute('style');
      const ex = /--ex:(-?[\d.]+)/.exec(cs)[1], ey = /--ey:(-?[\d.]+)/.exec(cs)[1];
      p.setAttribute('transform', `translate(${ex},${ey})`);
    });
    if (exploded) { const r = clone.querySelector('#reflection'); if (r) r.remove(); }
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    bg.setAttribute('x', vb[0]); bg.setAttribute('y', vb[1]); bg.setAttribute('width', vb[2]); bg.setAttribute('height', vb[3]); bg.setAttribute('fill', '#15171a');
    clone.insertBefore(bg, clone.firstChild.nextSibling);
    clone.querySelectorAll('.label text').forEach(tx => { tx.setAttribute('fill', '#f1c48a'); tx.setAttribute('font-family', 'Arial, sans-serif'); tx.setAttribute('font-size', '15'); });
    await Promise.all($$('image', clone).map(async im => {
      const h = im.getAttribute('href');
      if (h && !h.startsWith('data:')) {
        const blob = await (await fetch(h)).blob();
        im.setAttribute('href', await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result); fr.readAsDataURL(blob); }));
      }
    }));
    const data = new XMLSerializer().serializeToString(clone);
    const img = new Image();
    img.onload = () => {
      const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
      const ctx = cv.getContext('2d');
      ctx.drawImage(img, 0, 0);
      ctx.fillStyle = 'rgba(255,255,255,.55)'; ctx.font = '600 34px Arial'; ctx.fillText(hidePrices ? `${base().brand} ${base().name}` : `${base().brand} ${base().name} · ${fmtCHF(totals(RN.render(buildSpec()).metrics).price)}`, 40, H - 40);
      const a = document.createElement('a');
      a.download = `${base().name.replace(/\s+/g, '_')}_build.png`;
      a.href = cv.toDataURL('image/png');
      a.click();
    };
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(data);
  }

  // ------------------------------------------------------------ előre összeállított buildek
  const PRESETS = {
    'Gyári állapot': (b) => defaultState(b),
    'Taktikai karabély': () => {
      const s = defaultState('schm-m4fl');
      Object.assign(s.sel, { grip: 'bcm-g3', stock: 'bcm-s0', optic: 'eo-exps3', magnifier: 'eo-g33', buis: 'mp-mbuspro', light: 'sf-m600', muzzle: 'sf-warcomp', trigger: 'gs-ssae', ch: 'bcm-ch4', mag: 'mp-pm30', sling: 'bfg-vickers' });
      s.multi.mlok = ['bcm-kag', 'mp-qd', 'mp-cable'];
      return s;
    },
    'LPVO általános': () => {
      const s = defaultState('schm-m5fl'); s.finish = 'earth';
      Object.assign(s.sel, { grip: 'mp-moek2p', stock: 'mp-ctr', buttpad: 'mp-pad070', lpvo: 'vx-razor110', mount: 'gs-sp30', optic: 'ap-acro', buis: 'mp-mbusoff', light: 'sf-m640', muzzle: 'sf-3p', trigger: 'gs-sde', ch: 'rd-raptor', mag: 'mp-pm30w', sling: 'mp-ms4' });
      s.multi.mlok = ['mp-hstop', 'mp-qd', 'mp-cover'];
      s.colors = { 'mp-moek2p': 'fde', 'mp-ctr': 'fde', 'mp-pm30w': 'fde', 'mp-hstop': 'fde', 'mp-cover': 'fde', 'mp-ms4': 'coyote' };
      return s;
    },
    'Precíziós (DMR)': () => {
      const s = defaultState('schm-m5fl'); s.finish = 'olive';
      Object.assign(s.sel, { grip: 'b5-t23', stock: 'mp-prs3', lpvo: 'tj-vcog', buis: 'mp-mbusoff', muzzle: 'sf-warcomp', trigger: 'gs-ssa', ch: 'rd-raptor', mag: 'mp-pm20', bipod: 'hr-sbrm', sling: 'mp-ms1' });
      s.colors = { 'b5-t23': 'odg', 'mp-prs3': 'odg', 'mp-pm20': 'odg', 'mp-ms1': 'ranger' };
      s.multi.mlok = ['mp-qd'];
      return s;
    },
    'Rövid CQB': () => {
      const s = defaultState('schm-s4f'); s.finish = 'bronze';
      Object.assign(s.sel, { grip: 'mp-moek2', stock: 'mp-moeslk', optic: 'ap-t2', magnifier: 'hs-hm3x', buis: 'buis-factory', light: 'sl-hlx', muzzle: 'bcm-comp0', ch: 'rd-raptorlt', mag: 'mp-pm30', sling: 'mp-ms1' });
      s.multi.mlok = ['mp-rvg'];
      s.colors = { 'mp-moek2': 'gray', 'mp-moeslk': 'gray', 'mp-pm30': 'gray', 'mp-rvg': 'gray' };
      return s;
    },
    'IPSC / Dynamic': () => {
      const s = defaultState('schm-dyn');
      Object.assign(s.sel, { grip: 'mp-miad', stock: 'mp-moesl', buttpad: 'mp-padsl', lpvo: 'vx-se18', mount: 'vx-cant30', muzzle: 'bcm-comp0', ch: 'rd-raptor', mag: 'mp-pm30' });
      s.multi.mlok = ['mp-hstop'];
      s.multi.controls = ['mp-bad'];
      return s;
    },
  };
  function initPresets() {
    $('#presets').innerHTML = '<option value="">Előre összeállított buildek…</option>' + Object.keys(PRESETS).map(k => `<option>${k}</option>`).join('');
    $('#presets').onchange = (e) => {
      const k = e.target.value; if (!k) return;
      const rate = state.rate, ec = state.eurChf, mode = state.mode;
      state = sanitize(PRESETS[k](state.base));
      state.rate = rate; state.eurChf = ec; state.mode = mode;
      activeTab = 'base';
      update(false, true);
      e.target.value = '';
      toast(`Betöltve: ${k}`);
    };
  }

  // ------------------------------------------------------------ gyorskeresés
  const norm = (t) => String(t || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9.+\- ]+/g, ' ');
  // kategóriánként magyar és angol kulcsszavak, hogy "ravasz", "scope" vagy "torkolat" is találjon
  const CAT_WORDS = {
    base: 'puska fegyver alapfegyver karabely rifle carbine ar15 ar-15 ar',
    grip: 'markolat pisztolymarkolat pistol grip fogantyu',
    stock: 'tus vallpar stock puffercso buffer',
    buttpad: 'tustalp vallparna butt pad recoil pad tamasz',
    optic: 'red dot reddot holo holografikus reflex optika irnyzek sight pontcel',
    lpvo: 'lpvo celtavcso scope variable zoom prizmas optika acog tavcso',
    mount: 'szerelek mount gyuru ring cantilever szerelekin',
    magnifier: 'nagyito magnifier 3x booster',
    buis: 'vas iranyzek buis backup sight flip-up iron sight',
    light: 'lampa fegyverlampa weaponlight light flashlight vilagito taktikai lampa',
    mlok: 'mlok m-lok kiegeszito elomarkolat vertical grip kezutkozo handstop sin rail qd cover vedo',
    bipod: 'bipod ketlab lab tamasz harris',
    muzzle: 'torkolat langrejto kompenzator hangtompito suppressor muzzle flash hider brake comp',
    trigger: 'elsuto ravasz trigger',
    ch: 'felhuzokar toltokar charging handle',
    controls: 'kezeloszerv biztosito safety selector zarakaszto ravaszvedo trigger guard',
    mag: 'tar magazine pmag lovedek 10 20 30 40 toltenyes',
    sling: 'szij sling hevder',
  };
  let QS = [];
  function qsBuild() {
    const catName = Object.fromEntries(D.CATEGORIES.map(c => [c.id, c.name]));
    const out = [];
    D.BASES.forEach(b => out.push({ kind: 'base', id: b.id, cat: 'base', title: `${b.brand} ${b.name}`, sub: `Alapfegyver · ${String(b.barrel).replace('.', ',')}" · ${b.cal}`,
      hay: norm([b.brand, b.name, b.id, b.tag, b.cal, b.desc, CAT_WORDS.base, 'm4 m5 s4 dynamic ' + b.finishes.map(f => D.FINISHES[f].name).join(' ')].join(' ')) }));
    D.ITEMS.forEach(i => {
      if (i.factory || i.none) return;
      const colors = (i.colors || []).map(c => D.COLORS[c] ? D.COLORS[c].name + ' ' + c : c).join(' ');
      out.push({ kind: 'item', id: i.id, cat: i.cat, title: `${i.brand} ${i.name}`, sub: `${catName[i.cat]}${(i.specs && i.specs[0]) ? ' · ' + i.specs[0] : ''}`,
        hay: norm([i.brand, i.name, i.id, catName[i.cat], CAT_WORDS[i.cat] || '', (i.specs || []).join(' '), colors, i.type || '', i.style || ''].join(' ')) });
    });
    QS = out.map(e => Object.assign(e, { t: norm(e.title), cw: norm((catName[e.cat] || '') + ' ' + (CAT_WORDS[e.cat] || '')) }));
  }
  function qsScore(e, toks, whole) {
    let sc = 0;
    for (const t of toks) {
      const wordStart = new RegExp('(^|[ \\-.])' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
      if (wordStart.test(e.t)) sc += 4;
      else if (e.t.includes(t)) sc += 3;
      else if (wordStart.test(e.hay)) sc += 2;
      else if (e.hay.includes(t)) sc += 1;
      else return 0;
      if (wordStart.test(e.cw)) sc += 3;        // a kategória nevére / szinonimájára találat előbbre kerül
    }
    if (e.t.startsWith(whole)) sc += 3;
    return sc;
  }
  function qsSearch(q) {
    const whole = norm(q).trim();
    if (!whole) return [];
    const toks = whole.split(/\s+/).filter(Boolean);
    return QS.map(e => ({ e, s: qsScore(e, toks, whole) })).filter(x => x.s > 0).map(x => (x.s += (window.AR_PHOTOS.has(x.e.id) ? 0.5 : 0), x))
      .sort((a, b) => b.s - a.s || a.e.title.length - b.e.title.length).slice(0, 12).map(x => x.e);
  }
  const hl = (title, toks) => {
    let out = esc(title);
    toks.filter(t => t.length > 1).forEach(t => {
      // ékezetek nélküli egyezés kiemelése az eredeti szövegen
      const n = norm(title); const at = n.indexOf(t);
      if (at >= 0 && n.length === title.length) out = out.replace(new RegExp(esc(title.slice(at, at + t.length)).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), m => `<mark>${m}</mark>`);
    });
    return out;
  };
  const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  function qsIsOn(e) {
    if (e.kind === 'base') return state.base === e.id;
    return MULTI_CATS.includes(e.cat) ? state.multi[e.cat].includes(e.id) : state.sel[e.cat] === e.id;
  }
  function qsPick(e) {
    if (e.kind === 'base') { activeTab = 'base'; brandFilter = 'Összes'; setBase(e.id, null); }
    else {
      activeTab = e.cat; brandFilter = 'Összes';
      if (!qsIsOn(e)) choose(e.cat, e.id); else update(true);
    }
    const cat = D.CATEGORIES.find(c => c.id === e.cat);
    toast(`${e.title} – ${qsIsOn(e) ? 'kiválasztva' : 'kiválasztás megszűnt'} (${cat ? cat.name : ''})`);
    const card = $(`#panel .card[data-id="${e.id}"]`);
    if (card) { card.classList.add('flash'); }
  }
  function initQuickSearch() {
    const input = $('#qs'), list = $('#qsList');
    if (!input) return;
    qsBuild();
    let res = [], sel = 0;
    const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); };
    const mark = () => $$('#qsList li').forEach((li, i) => { const on = i === sel; li.setAttribute('aria-selected', on ? 'true' : 'false'); if (on) { input.setAttribute('aria-activedescendant', li.id); li.scrollIntoView({ block: 'nearest' }); } });
    const render = () => {
      const q = input.value;
      if (!q.trim()) { close(); return; }
      res = qsSearch(q); sel = 0;
      const toks = norm(q).trim().split(/\s+/);
      if (!res.length) list.innerHTML = '<li class="qs-empty" role="presentation">Nincs találat. Próbálj rövidebb szót vagy márkanevet (pl. „holosun”, „lámpa”, „tus”).</li>';
      else list.innerHTML = res.map((e, i) => {
        const q2 = e.kind === 'base' ? D.BASES.find(b => b.id === e.id) : ITEM[e.id];
        const pr = priceOf(q2);
        const photo = e.kind === 'base' ? !!window.AR_PHOTOS.get(e.id, 'base', 'black') : window.AR_PHOTOS.has(e.id);
        return `<li id="qso${i}" role="option" data-i="${i}" aria-selected="false"><div class="qs-main"><b>${hl(e.title, toks)}</b><small>${esc(e.sub)}</small></div>` +
          `<div class="qs-side">${qsIsOn(e) ? '<span class="qs-tag on">kiválasztva</span>' : ''}${photo ? '<span class="qs-tag ok">fotó</span>' : ''}<span class="price${pr.none ? ' none' : ''}">${priceTxt(pr)}</span></div></li>`;
      }).join('') + `<li class="qs-foot" role="presentation">${res.length} találat · ↑↓ választ · Enter kiválaszt · Esc bezár</li>`;
      list.hidden = false; input.setAttribute('aria-expanded', 'true'); mark();
    };
    input.addEventListener('input', render);
    input.addEventListener('focus', () => { if (input.value.trim()) render(); });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowDown' && res.length) { ev.preventDefault(); sel = (sel + 1) % res.length; mark(); }
      else if (ev.key === 'ArrowUp' && res.length) { ev.preventDefault(); sel = (sel - 1 + res.length) % res.length; mark(); }
      else if (ev.key === 'Enter' && res[sel] && !list.hidden) { ev.preventDefault(); const e = res[sel]; qsPick(e); input.value = ''; close(); input.blur(); }
      else if (ev.key === 'Escape') { if (input.value) input.value = ''; close(); input.blur(); }
    });
    list.addEventListener('mousedown', (ev) => ev.preventDefault());   // a fókusz maradjon a mezőn a kattintásig
    list.addEventListener('click', (ev) => {
      const li = ev.target.closest('li[data-i]'); if (!li) return;
      qsPick(res[Number(li.dataset.i)]); input.value = ''; close();
    });
    list.addEventListener('mousemove', (ev) => { const li = ev.target.closest('li[data-i]'); if (li && Number(li.dataset.i) !== sel) { sel = Number(li.dataset.i); mark(); } });
    document.addEventListener('click', (ev) => { if (!ev.target.closest('.qsearch')) close(); });
    document.addEventListener('keydown', (ev) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '');
      if ((ev.key === '/' && !typing) || ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k')) { ev.preventDefault(); input.focus(); input.select(); }
    });
    window.AR_QS_REFRESH = () => { if (!list.hidden) render(); };
  }

  // ------------------------------------------------------------ árak elrejtése
  let hidePrices = false;
  try { hidePrices = localStorage.getItem('ar15-hideprices') === '1'; } catch (e) { /* nincs tároló */ }
  function applyPriceVisibility() {
    document.body.classList.toggle('noprice', hidePrices);
    const b = $('#btnPrices');
    if (b) b.checked = !hidePrices;
  }

  // ------------------------------------------------------------ indítás
  function init() {
    $('#defs').innerHTML = RN.DEFS + `
      <linearGradient id="reflGrad" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="180">
        <stop offset="0" stop-color="#fff" stop-opacity=".16"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>
      </linearGradient>
      <mask id="reflMask" maskUnits="userSpaceOnUse"><rect id="reflMaskRect" x="-2000" y="0" width="6000" height="400" fill="url(#reflGrad)"/></mask>`;
    $('#rate').value = state.rate;
    $('#rate').oninput = (e) => { const v = Number(e.target.value); if (v > 0) { state.rate = v; update(true); } };
    $('#btnPrices').onchange = () => { hidePrices = !$('#btnPrices').checked; try { localStorage.setItem('ar15-hideprices', hidePrices ? '1' : '0'); } catch (e) { /* ignore */ } applyPriceVisibility(); };
    applyPriceVisibility();
    initQuickSearch();
    $('#btnExplode').onclick = () => setExploded(!exploded);
    $('#btnAssemble').onclick = () => renderRifle(true);
    $('#btnShare').onclick = share;
    $('#btnPng').onclick = exportPng;
    $('#btnReset').onclick = () => { if (confirm('Visszaállítod a gyári állapotot?')) { const r = state.rate, ec = state.eurChf, md = state.mode; state = defaultState(state.base); state.rate = r; state.eurChf = ec; state.mode = md; update(false, true); } };
    initZoom();
    initPartInteraction();
    initPresets();
    let ready = false;
    window.AR_PHOTOS.onChange(() => { if (ready) update(false); });
    const loadShops = fetch('assets/shops.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).then(j => { if (j) { SHOPINFO = j.shops || {}; if (j.reference) SHOPREF = Object.assign(SHOPREF, j.reference); } }).catch(() => {});
    const loadPrices = fetch('assets/prices/merged.json', { cache: 'no-cache' }).then(r => r.ok ? r.json() : null).then(j => { if (j && j.items) { PRICES = j.items; SHOPS = j.shops || []; } }).catch(() => {});
    Promise.all([window.AR_PHOTOS.load().catch(() => {}), loadPrices, loadShops]).then(() => { buildShopMode(); ready = true; update(false, true); });
  }
  document.addEventListener('DOMContentLoaded', init);
})();
