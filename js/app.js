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

  const fmtEUR = n => new Intl.NumberFormat('hu-HU', { maximumFractionDigits: 0 }).format(n) + ' €';
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
    return { base: b.id, finish: b.finishes[0], sel, multi: { mlok: [], controls: [] }, colors: {}, rate: 390 };
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
    out.rate = Number(s.rate) > 0 ? Number(s.rate) : d.rate;
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
    const pick = (cat, extra) => { const it = resolved(cat); return isActive(it) ? Object.assign({ hex: colorOf(it) }, it, extra || {}) : null; };
    const tg = state.multi.controls.includes('mp-trigguard') ? ITEM['mp-trigguard'] : null;
    return {
      barrel: b.barrel, hgLen: b.hgLen, hgStyle: b.hgStyle, finish: D.FINISHES[state.finish].hex, ambi: b.ambi,
      grip: Object.assign({ hex: colorOf(resolved('grip')) }, resolved('grip')),
      stock: Object.assign({ hex: colorOf(st) }, st), stockRear,
      pad: pad && !pad.factory ? pad.style : 'std',
      optic: pick('optic'), lpvo: pick('lpvo'), mount: (sel('mount') && !sel('mount').none) ? sel('mount').style : null,
      magnifier: pick('magnifier'), buis: pick('buis'), light: pick('light'), bipod: pick('bipod'),
      muzzle: resolved('muzzle'), trigger: resolved('trigger').style, ch: Object.assign({ hex: colorOf(resolved('ch')) }, resolved('ch')),
      mag: Object.assign({ hex: colorOf(sel('mag')) }, sel('mag')), sling: pick('sling'),
      mlok: state.multi.mlok.map(id => Object.assign({ hex: colorOf(ITEM[id]) }, ITEM[id])),
      controls: state.multi.controls.map(id => ITEM[id].style),
      tguardHex: tg ? colorOf(tg) : null,
    };
  }

  function totals(metrics) {
    const b = base();
    let price = b.price, weight = b.weight;
    const lines = [{ cat: 'base', label: `${b.brand} ${b.name}`, sub: D.FINISHES[state.finish].name, price: b.price, weight: b.weight }];
    SINGLE_CATS.forEach(cat => {
      const it = sel(cat);
      if (!it || it.none && !(cat === 'buis' && b.factoryBuis)) return;
      if (it.factory) {
        if (cat === 'mag') { weight += 90; }
        return;
      }
      let w = it.weight;
      if (it.none) { // gyári BUIS eltávolítva
        weight -= FACTORY_W.buis; return;
      }
      if (FACTORY_W[cat] != null && !(cat === 'buis' && !b.factoryBuis)) w -= FACTORY_W[cat];
      price += it.price; weight += w;
      lines.push({ cat, label: `${it.brand} ${it.name}`, sub: colorName(it), price: it.price, weight: it.weight });
    });
    MULTI_CATS.forEach(cat => state.multi[cat].forEach(id => {
      const it = ITEM[id];
      price += it.price; weight += it.weight;
      lines.push({ cat, label: `${it.brand} ${it.name}`, sub: colorName(it), price: it.price, weight: it.weight });
    }));
    const st = sel('stock');
    if (st && st.type === 'rifle') {
      price += RIFLE_KIT.price; weight += RIFLE_KIT.weight;
      lines.push({ cat: 'stock', label: RIFLE_KIT.name, sub: 'becsült', price: RIFLE_KIT.price, weight: RIFLE_KIT.weight });
    }
    // hossz
    const spec = buildSpec();
    const fixed = RN.BF / RN.S + b.barrel + metrics.muzzleLen;
    const isRifle = spec.stock.type === 'rifle';
    const minS = isRifle ? spec.stockRear : spec.stockRear - 1.6;
    const maxS = isRifle ? spec.stockRear + (spec.stock.style === 'prs' ? 1.5 : 0) : spec.stockRear + 1.65;
    const oal = [(minS + fixed) * 2.54, (maxS + fixed) * 2.54];
    return { price, weight, lines, oal };
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
    if (['lower', 'upper', 'barrel', 'handguard', 'bcg'].includes(cat)) return `${b.brand} ${b.name} – ${RN.LABELS[cat]}`;
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
    panel.innerHTML = `
      <div class="panel-head"><h2>${cat.name}</h2>
        <div class="chips">${brands.map(b => `<button class="chip${b === brandFilter ? ' on' : ''}" data-brand="${b}">${b}</button>`).join('')}</div></div>
      ${hint}
      <div class="grid">${shown.map(it => card(it, selectedIds.includes(it.id), cat)).join('')}</div>`;
    $$('.chip', panel).forEach(c => c.onclick = () => { brandFilter = c.dataset.brand; renderPanel(); });
    $$('.card', panel).forEach(c => c.onclick = (e) => {
      if (e.target.closest('.swatch')) return;
      choose(cat.id, c.dataset.id);
    });
    $$('.swatch', panel).forEach(s => s.onclick = () => {
      state.colors[s.dataset.item] = s.dataset.color;
      const it = ITEM[s.dataset.item];
      if (cat.multi) { if (!state.multi[cat.id].includes(it.id)) toggleMulti(cat.id, it.id, true); }
      else state.sel[cat.id] = it.id;
      lastChangedCat = cat.id;
      update();
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
      <div class="card-bot"><span class="price">${it.price ? fmtEUR(it.price) : (it.factory ? 'benne az árban' : '—')}</span>${it.weight ? `<span class="w">${it.weight} g</span>` : ''}</div>
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
          <div class="sw">${x.finishes.map(f => `<button class="swatch fin${x.id === b.id && f === state.finish ? ' on' : ''}" data-base="${x.id}" data-finish="${f}" title="${D.FINISHES[f].name}" style="--c:${D.FINISHES[f].hex}"></button>`).join('')}
            <span class="swname">${x.id === b.id ? D.FINISHES[state.finish].name : x.finishes.map(f => D.FINISHES[f].name).join(' / ')}</span></div>
          <div class="card-bot"><span class="price">${fmtEUR(x.price)}</span><span class="w">${fmtKg(x.weight)}</span></div>
          ${x.oalNote ? `<div class="desc small">${x.oalNote}</div>` : ''}
        </div>`).join('')}</div>
      <p class="hint small">* Becsült érték a megjelenítéshez. Az árak tájékoztató jellegű európai irányárak; a színváltozatok elérhetősége modellenként és kereskedőnként eltérhet.</p>`;
    $$('.card.base', panel).forEach(c => c.onclick = (e) => {
      const sw = e.target.closest('.swatch');
      setBase(c.dataset.id, sw ? sw.dataset.finish : null);
    });
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
            return `<li class="${on ? 'on' : ''}" data-cat="${c.id}" data-id="${i.id}"><span>${i.name}</span><b>${fmtEUR(i.price)}</b></li>`;
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
  let currentIssues = [];
  function renderSummary(t, issues) {
    const b = base();
    $('#sumTitle').textContent = `${b.brand} ${b.name}`;
    $('#sumFinish').textContent = D.FINISHES[state.finish].name;
    $('#statPrice').textContent = fmtEUR(t.price);
    $('#statHuf').textContent = '≈ ' + fmtHUF(t.price * state.rate);
    $('#statWeight').textContent = fmtKg(t.weight);
    $('#statOal').textContent = Math.abs(t.oal[1] - t.oal[0]) < 0.5 ? `${t.oal[0].toFixed(0)} cm` : `${t.oal[0].toFixed(0)}–${t.oal[1].toFixed(0)} cm`;
    $('#statBarrel').textContent = `${String(b.barrel).replace('.', ',')}" · ${b.cal}`;
    $('#sumList').innerHTML = t.lines.map(l => `<li data-cat="${l.cat}"><div><b>${l.label}</b>${l.sub ? `<small>${l.sub}</small>` : ''}</div><span>${fmtEUR(l.price)}</span></li>`).join('');
    $$('#sumList li').forEach(li => li.onclick = () => { activeTab = li.dataset.cat; renderTabs(); renderPanel(); });
    $('#sumTotal').textContent = fmtEUR(t.price);
    $('#sumHuf').textContent = '≈ ' + fmtHUF(t.price * state.rate);
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
  function exportPng() {
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
    const data = new XMLSerializer().serializeToString(clone);
    const img = new Image();
    img.onload = () => {
      const cv = document.createElement('canvas'); cv.width = W; cv.height = H;
      const ctx = cv.getContext('2d');
      ctx.drawImage(img, 0, 0);
      ctx.fillStyle = 'rgba(255,255,255,.55)'; ctx.font = '600 34px Arial'; ctx.fillText(`${base().brand} ${base().name} · ${fmtEUR(totals(RN.render(buildSpec()).metrics).price)}`, 40, H - 40);
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
      const rate = state.rate;
      state = sanitize(PRESETS[k](state.base));
      state.rate = rate;
      activeTab = 'base';
      update(false, true);
      e.target.value = '';
      toast(`Betöltve: ${k}`);
    };
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
    $('#btnExplode').onclick = () => setExploded(!exploded);
    $('#btnAssemble').onclick = () => renderRifle(true);
    $('#btnShare').onclick = share;
    $('#btnPng').onclick = exportPng;
    $('#btnReset').onclick = () => { if (confirm('Visszaállítod a gyári állapotot?')) { const r = state.rate; state = defaultState(state.base); state.rate = r; update(false, true); } };
    initZoom();
    initPartInteraction();
    initPresets();
    update(false, true);
  }
  document.addEventListener('DOMContentLoaded', init);
})();
