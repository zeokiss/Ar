/*
 * AR-15 Konfigurátor – fotókompozitáló megjelenítő
 *
 * Az alapfegyver valódi (gyári) oldalnézeti fotója + a rá illesztett valódi alkatrészfotók.
 * Koordináták: hüvelyk a fegyver keretében (x előre, y lefelé, origó: a felső tok hátsó éle a
 * cső tengelyén). A képernyőre 1" = S px, az irány az alapfotó irányához igazodik.
 *
 * Rétegek (alulról): hátsó alkatrészek (tus, tár, markolat, torkolat) -> alapfotó
 * (a lecserélt gyári részek maszkkal kivágva) -> felső alkatrészek (optika, lámpa, ...).
 */
window.AR_PHOTO_RENDER = (function () {
  const S = 28;
  const P = () => window.AR_PHOTOS;
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

  // Alapértelmezett pozíciók a sínen / előagyon (hüvelyk), a felhasználó csúszkával eltolhatja
  const RAIL = { lpvo: 4.1, optic: 6.2, buisRear: 1.5 };
  const PH_SIZE = {
    optic: [3.4, 1.9, 'rail'], lpvo: [10, 2.4, 'rail'], magnifier: [3.6, 2.2, 'rail'], mount: [3, 0.8, 'rail'],
    buis: [1.4, 1.0, 'rail'], light: [4, 1.3, 'side'], bipod: [6, 1.1, 'bottom'], mlok: [1.6, 1.6, 'bottom'],
    stock: [5, 4, 'box'], grip: [2, 4, 'box'], mag: [2, 5, 'box'], muzzle: [2.3, 1, 'box'],
  };
  const MLOK_PLACE = { rvg: ['bottom', 0.45], vg3: ['bottom', 0.45], kag: ['bottom', 0.4], hstop: ['bottom', 0.62], rail: ['side', 0.8], qd: ['side', 0.12], cover: ['side', 0.35], cable: ['side', 0.7] };

  function extents(rec) { // a kép kiterjedése az illesztési ponthoz képest, hüvelykben (előre pozitív)
    const k = (rec.scale || 1) / rec.ppi, s = rec.facing === 'left' ? -1 : 1;
    const u0 = rec.bx0 != null ? rec.bx0 : 0, u1 = rec.bx1 != null ? rec.bx1 : rec.w;
    const a = s * (u0 - rec.ax) * k, b = s * (u1 - rec.ax) * k;
    return { back: Math.min(a, b), fwd: Math.max(a, b), up: -rec.ay * k, down: (rec.h - rec.ay) * k };
  }

  function R() {} // (helyfoglaló a modul-szerkezethez)

  function render(spec) {
    const ph = P();
    if (!ph || !spec.baseId) return null;
    const baseRec = ph.get(spec.baseId, 'base', spec.finishId);
    const geo = ph.geo(spec.baseId);
    if (!baseRec || !geo) return null;
    const getPart = (id, slot, color) => ph.get(id, slot + '@' + baseRec.facing, color) || ph.get(id, slot, color);
    const pts = geo.pts, cuts = geo.cuts || {};
    const D = baseRec.facing === 'left' ? -1 : 1;
    const X = (x) => D * x * S, Y = (y) => y * S;
    const pos = spec.pos || {};
    const missing = [], approx = [];
    const behind = [], over = [];
    const spans = [];
    const holes = []; // a gyári alapfotóból kivágandó poligonok
    let uid = 0;

    function img(rec, xin, yin) {
      const k = (S / rec.ppi) * (rec.scale || 1);
      const F = D * (rec.facing === 'left' ? -1 : 1);
      const x = X(xin + (rec.dx || 0)), y = Y(yin + (rec.dy || 0));
      const t = `translate(${x.toFixed(2)},${y.toFixed(2)}) rotate(${((rec.rot || 0) * D).toFixed(2)}) scale(${(F * k).toFixed(5)},${k.toFixed(5)}) translate(${-rec.ax},${-rec.ay})`;
      return `<image href="${esc(rec.src)}" x="0" y="0" width="${rec.w}" height="${rec.h}" transform="${t}" preserveAspectRatio="none"/>`;
    }
    function placeholder(cat, name, xin, yin) {
      const [w, h, kind] = PH_SIZE[cat] || [2, 1.5, 'rail'];
      let x0 = xin - w / 2, y0 = kind === 'rail' ? yin - h : kind === 'bottom' ? yin : kind === 'side' ? yin - h / 2 : yin - h / 2;
      const px = D * x0 * S, wpx = w * S;
      const rx = D === 1 ? px : px - wpx;
      missing.push({ cat, name });
      return `<g class="ph"><title>Nincs fotó: ${esc(name)}</title><rect x="${rx.toFixed(1)}" y="${(y0 * S).toFixed(1)}" width="${wpx.toFixed(1)}" height="${(h * S).toFixed(1)}" rx="6" fill="rgba(232,145,58,.10)" stroke="#e8913a" stroke-width="1.6" stroke-dasharray="7 5"/>` +
        `<text x="${(rx + wpx / 2).toFixed(1)}" y="${(y0 * S + h * S / 2 + 7).toFixed(1)}" text-anchor="middle" font-size="20" font-weight="700" fill="#f5b46a" font-family="Inter,Arial,sans-serif">?</text></g>`;
    }
    const note = (rec, it) => { if (rec && rec.exactColor === false && it && it.colors) approx.push({ name: it.name, have: rec.color }); };

    // ------------------------------------------------ hátsó rétegek (az alapfotó mögött)
    const stockIt = spec.stock && !spec.stock.factory ? spec.stock : null;
    let rearX = null;
    if (stockIt) {
      const rec = getPart(stockIt.id, 'main', stockIt.colorId);
      if (rec) { behind.push(['stock', img(rec, pts.stock[0], pts.stock[1])]); holes.push(cuts.stock); note(rec, stockIt); rearX = pts.stock[0] + extents(rec).back; }
      else behind.push(['stock', placeholder('stock', stockIt.name, pts.stock[0] - 2.8, pts.stock[1])]);
    }
    const magIt = spec.mag && !spec.mag.factory ? spec.mag : null;
    if (magIt) {
      const rec = getPart(magIt.id, 'main', magIt.colorId);
      if (rec) { behind.push(['mag', img(rec, pts.mag[0], pts.mag[1])]); holes.push(cuts.mag); note(rec, magIt); }
      else behind.push(['mag', placeholder('mag', magIt.name, pts.mag[0], pts.mag[1] + 2.6)]);
    }
    const gripIt = spec.grip && !spec.grip.factory ? spec.grip : null;
    if (gripIt) {
      const rec = getPart(gripIt.id, 'main', gripIt.colorId);
      if (rec) { behind.push(['grip', img(rec, pts.grip[0], pts.grip[1])]); holes.push(cuts.grip); note(rec, gripIt); }
      else behind.push(['grip', placeholder('grip', gripIt.name, pts.grip[0], pts.grip[1] + 1.8)]);
    }
    const mzIt = spec.muzzle && !spec.muzzle.factory ? spec.muzzle : null;
    let muzzleLen = null;
    if (mzIt) {
      const rec = getPart(mzIt.id, 'main', mzIt.colorId);
      if (rec) { behind.push(['muzzle', img(rec, pts.muzzle[0], pts.muzzle[1])]); holes.push(cuts.muzzle); note(rec, mzIt); muzzleLen = extents(rec).fwd; }
      else behind.push(['muzzle', placeholder('muzzle', mzIt.name, pts.muzzle[0] + 1.1, pts.muzzle[1])]);
    }
    if (muzzleLen == null) muzzleLen = (pts.tipX != null ? pts.tipX : pts.muzzle[0] + 2.25) - pts.muzzle[0];

    // ------------------------------------------------ gyári flip-up irányzék kezelése
    const factoryBuis = spec.buis && spec.buis.factory;
    const noSightsInPhoto = !(factoryBuis && !spec.optic && !spec.lpvo);
    if (noSightsInPhoto) {
      const sg = pts.sights || [[1.0, 2.6], [pts.hgX1 - 2.4, pts.hgX1 - 0.9]];
      sg.forEach(([a, b]) => holes.push([[a, -4], [b, -4], [b, pts.railY - 0.03], [a, pts.railY - 0.03]]));
    }

    // ------------------------------------------------ felső rétegek
    const railY = pts.railY, rx0 = pts.railX0, rx1 = pts.railX1;
    const mountIt = spec.mountItem;
    const lpvoX = spec.lpvo ? ((spec.lpvo.railX != null ? spec.lpvo.railX : rx0 + RAIL.lpvo) + (pos.lpvo || 0)) : null;

    function railPart(cat, it, slot, xin, yin, label) {
      const rec = getPart(it.id, slot || 'main', it.colorId);
      if (!rec) { over.push([cat, placeholder(cat === 'mount' ? 'mount' : cat, label || it.name, xin, yin)]); return null; }
      note(rec, it);
      over.push([cat, img(rec, xin, yin)]);
      const e = extents(rec);
      spans.push([xin + e.back, xin + e.fwd, label || it.name]);
      return { rec, e };
    }

    if (spec.lpvo) {
      const it = spec.lpvo;
      const axis = it.contact ? 0 : ((mountIt && mountIt.axis) || it.axis || 1.93);
      if (mountIt) railPart('mount', mountIt, 'main', lpvoX, railY);
      railPart('lpvo', it, 'main', lpvoX, railY - axis);
    }
    let optX = null, optW = 3.2;
    if (spec.optic) {
      optX = (spec.optic.railX != null ? spec.optic.railX : (spec.lpvo ? lpvoX + 5.2 : rx0 + RAIL.optic)) + (pos.optic || 0);
      const r = railPart('optic', spec.optic, 'main', optX, railY);
      if (r) optW = r.e.fwd - r.e.back;
    }
    if (spec.magnifier && spec.optic && !spec.lpvo) {
      const it = spec.magnifier;
      const rec = getPart(it.id, 'main', it.colorId);
      const mw = rec ? (extents(rec).fwd - extents(rec).back) : 3.6;
      const mx = optX - optW / 2 - mw / 2 - 0.25 + (pos.magnifier || 0);
      railPart('magnifier', it, 'main', mx, railY);
    }
    if (spec.buis && !spec.buis.factory) {
      const it = spec.buis;
      holes.length && null;
      railPart('buis', it, 'rear', rx0 + RAIL.buisRear + (pos.buis || 0), railY, it.name + ' (hátsó)');
      railPart('buis', it, 'front', pts.hgX1 - 1.5, railY, it.name + ' (első)');
    }

    // fegyverlámpa
    if (spec.light) {
      const it = spec.light;
      const x = pts.hgX1 - 2.4 + (pos.light || 0);
      railPart('light', it, 'main', x, pts.sideY);
    }
    // bipod
    if (spec.bipod) {
      const x = pts.hgX1 - 1.0 + (pos.bipod || 0);
      railPart('bipod', spec.bipod, 'main', x, pts.hgBottomY);
    }
    // M-LOK kiegészítők
    (spec.mlok || []).forEach(it => {
      const [on, f] = MLOK_PLACE[it.style] || ['bottom', 0.5];
      const x = pts.hgX0 + (pts.hgX1 - pts.hgX0) * f;
      const y = on === 'bottom' ? pts.hgBottomY : pts.sideY;
      const rec = getPart(it.id, 'main', it.colorId);
      if (!rec) { over.push(['mlok', placeholder('mlok', it.name, x, y)]); return; }
      note(rec, it); over.push(['mlok', img(rec, x, y)]);
    });

    // ------------------------------------------------ alapfotó (maszkkal)
    const maskId = 'cutmask' + (++uid);
    const polys = holes.filter(Boolean);
    let baseSvg = img(baseRec, 0, 0);
    let defs = '';
    if (polys.length) {
      defs = `<defs><mask id="${maskId}" maskUnits="userSpaceOnUse" x="-4000" y="-1500" width="8000" height="3000"><rect x="-4000" y="-1500" width="8000" height="3000" fill="#fff"/>` +
        polys.map(p => `<polygon points="${p.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(' ')}" fill="#000"/>`).join('') + '</mask></defs>';
      baseSvg = `<g mask="url(#${maskId})">${baseSvg}</g>`;
    }

    // ------------------------------------------------ összeállítás, mérőszámok
    const parts = [];
    behind.forEach(([cat, svg]) => parts.push({ cat, svg }));
    parts.push({ cat: 'base', svg: defs + baseSvg });
    over.forEach(([cat, svg]) => parts.push({ cat, svg }));

    const be = extents(baseRec);
    const back = rearX != null ? rearX : be.back, fwd = Math.max(pts.muzzle[0] + muzzleLen, 0);
    const railClash = [];
    for (let i = 0; i < spans.length; i++) for (let j = i + 1; j < spans.length; j++) {
      const a = spans[i], b = spans[j];
      if (a[0] < b[1] - 0.05 && b[0] < a[1] - 0.05) railClash.push(`${a[2]} ↔ ${b[2]}`);
    }
    spans.forEach(sp => { if (sp[1] > rx1 + 0.1) railClash.push(`${sp[2]} túllóg a sínen`); if (sp[0] < rx0 - 0.1) railClash.push(`${sp[2]} a sín mögé lóg`); });

    return {
      parts,
      metrics: { muzzleEnd: X(fwd), muzzleLen, rearPx: 0, railClash, hgX1: X(pts.hgX1), oalIn: fwd - back, photo: { missing, approx, finishExact: baseRec.exactColor !== false, finishHave: baseRec.color, ppi: baseRec.ppi } },
    };
  }

  return { render, extents, S };
})();
