/*
 * AR-15 Konfigurátor – SVG megjelenítő
 * Jobb oldali nézet, a cső jobbra mutat. Koordináták pixelben, 1" = S px.
 * Origó: a felső tok hátsó éle (x=0), a cső tengelye (y=0), y lefelé nő.
 */
window.AR_RENDER = (function () {
  const S = 28;               // px / hüvelyk
  const UF = 199;             // felső tok eleje
  const BF = 171;             // zártükör (csőhossz innen mérve)
  const RAIL_TOP = -40;

  // ------------------------------------------------------------ színsegédek
  function hexToRgb(h) {
    h = h.replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    const n = parseInt(h, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgbToHex(r, g, b) {
    return '#' + [r, g, b].map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
  }
  function shade(h, a) {
    const [r, g, b] = hexToRgb(h);
    if (a < 0) { const f = 1 + a; return rgbToHex(r * f, g * f, b * f); }
    return rgbToHex(r + (255 - r) * a, g + (255 - g) * a, b + (255 - b) * a);
  }

  // ------------------------------------------------------------ primitívek
  function P(d, fill, o) {
    o = o || {};
    const stroke = o.stroke || shade(fill, -0.6);
    const fr = o.evenodd ? ' fill-rule="evenodd"' : '';
    const sw = o.sw != null ? o.sw : 1;
    let s = `<path d="${d}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" stroke-linejoin="round"${fr}${o.op != null ? ` opacity="${o.op}"` : ''}/>`;
    const sheen = o.sheen === undefined ? 'v' : o.sheen;
    if (sheen) s += `<path d="${d}" fill="url(#sh-${sheen})"${fr} pointer-events="none"/>`;
    if (o.tex) s += `<path d="${d}" fill="url(#tex-dots)"${fr} pointer-events="none" opacity=".55"/>`;
    return s;
  }
  function rr(x, y, w, h, r) {
    r = Math.min(r || 0, w / 2, h / 2);
    if (!r) return `M${x},${y}h${w}v${h}h${-w}Z`;
    return `M${x + r},${y}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 ${-r},${r}h${-(w - 2 * r)}a${r},${r} 0 0 1 ${-r},${-r}v${-(h - 2 * r)}a${r},${r} 0 0 1 ${r},${-r}Z`;
  }
  function R(x, y, w, h, fill, o) { return P(rr(x, y, w, h, (o && o.r) || 0), fill, o); }
  function cyl(x, y, w, h, fill, o) { return P(rr(x, y, w, h, (o && o.r) || 0), fill, Object.assign({ sheen: 'c' }, o)); }
  function circ(cx, cy, r, fill, o) {
    o = o || {};
    let s = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${fill}" stroke="${o.stroke || shade(fill, -0.6)}" stroke-width="${o.sw != null ? o.sw : 1}"/>`;
    if (o.sheen !== false) s += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="url(#sh-r)" pointer-events="none"/>`;
    return s;
  }
  function line(x1, y1, x2, y2, c, w, op) {
    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c}" stroke-width="${w || 1}" stroke-linecap="round"${op != null ? ` opacity="${op}"` : ''}/>`;
  }

  // Picatinny sín oldalnézetben (felül fogazat)
  function picRail(x0, x1, y, color) {
    let s = P(`M${x0},${y + 1.5} L${x0 + 1.5},${y} L${x1 - 1.5},${y} L${x1},${y + 1.5} L${x1},${y + 10} L${x0},${y + 10} Z`, color, { sheen: 'v' });
    const dark = shade(color, -0.65);
    for (let x = x0 + 5; x + 5 < x1 - 2; x += 11) s += `<rect x="${x.toFixed(1)}" y="${y}" width="5.2" height="5.5" fill="${dark}"/>`;
    s += line(x0 + 1, y + 6.5, x1 - 1, y + 6.5, shade(color, 0.25), 0.6, 0.6);
    return s;
  }
  // Oldalsó (felénk néző) Picatinny sín
  function sideRail(x0, x1, yc, color) {
    let s = R(x0, yc - 8, x1 - x0, 16, color, { r: 1.5 });
    const dark = shade(color, -0.65);
    for (let x = x0 + 4; x + 5 < x1 - 2; x += 11) s += `<rect x="${x.toFixed(1)}" y="${yc - 7}" width="5" height="14" fill="${dark}" opacity=".85"/>`;
    return s;
  }

  // ------------------------------------------------------------ alkatrészek
  function upper(c, ambi) {
    const body = `M-2,-29 L4,-30 L${UF},-30 L${UF},18 L8,18 L1,16 L-2,10 Z M94,-15 L150,-15 Q153,-15 153,-12 L153,3 Q153,6 150,6 L94,6 Q91,6 91,3 L91,-12 Q91,-15 94,-15 Z`;
    let s = P(body, c, { evenodd: true });
    // előrész finom vonalai
    s += line(158, -27, 158, 16, shade(c, -0.45), 1, 0.7);
    s += line(6, 12, 186, 12, shade(c, 0.18), 0.8, 0.35);
    // forward assist ház + gomb
    s += P('M18,-25 L72,-21 L74,-4 L22,-6 Q12,-8 12,-16 Q12,-25 18,-25 Z', shade(c, 0.05));
    s += circ(17, -15, 9, shade(c, -0.15));
    for (let i = -6; i <= 6; i += 3) s += line(10, -15 + i, 24, -15 + i, shade(c, -0.55), 0.8, 0.8);
    // hüvelyterelő
    s += P('M76,-28 L90,-28 L90,-10 Q84,-6 78,-12 Z', shade(c, 0.06));
    // porvédő (nyitott, lefelé lóg)
    s += R(92, 7, 60, 6, shade(c, -0.1), { r: 1 });
    s += line(93, 9.5, 151, 9.5, shade(c, -0.6), 0.7);
    // felső sín
    s += picRail(4, UF, RAIL_TOP, c);
    return s;
  }

  function bcg(color) {
    // zárszerkezet – a felső tok mögött, a kidobónyíláson látszik
    let s = cyl(20, -15, 140, 24, color, { r: 3 });
    for (let x = 96; x < 148; x += 4) s += line(x, -13, x, 7, shade(color, -0.55), 1.3, 0.9);
    s += R(34, -24, 50, 10, color, { r: 2 });
    s += cyl(160, -9, 22, 16, shade(color, 0.08), { r: 2 });
    s += circ(130, -18, 4, shade(color, 0.1));
    return s;
  }

  function lower(c, ambi, controls) {
    const d = 'M-10,-20 L-4,-25 L8,-25 L8,18 L192,18 L192,24 L186,31 L173,33 L173,80 L177,90 L106,90 L108,80 L108,50 L62,50 L54,52 L40,52 L24,50 L6,46 L-4,40 L-10,28 Z';
    let s = P(d, c);
    // tárakna kerete, apró részletek
    s += line(110, 82, 172, 82, shade(c, 0.25), 0.8, 0.45);
    s += P('M112,26 L112,52 Q112,58 106,58', 'none', { stroke: shade(c, -0.5), sheen: false });
    // tárkioldó gomb + védőkeret
    s += P('M102,32 Q114,26 124,34 L124,46 Q114,52 102,46 Z', shade(c, 0.05));
    s += circ(113, 39, 5.5, shade(c, -0.2));
    // csapok
    s += circ(16, 25, 4.2, shade(c, -0.15));
    s += circ(184, 25, 4.2, shade(c, -0.15));
    s += circ(64, 33, 2.6, shade(c, -0.25));
    s += circ(86, 33, 2.6, shade(c, -0.25));
    // biztosító (ambidex esetén jobb oldalon is)
    const talon = controls && controls.includes('talon');
    if (ambi || talon) {
      const lc = talon ? '#3b3d40' : shade(c, -0.2);
      s += `<g transform="translate(38,32) rotate(${talon ? -8 : 0})">` + P(talon ? 'M-5,-5 L26,-3 Q30,0 26,3 L-5,5 Q-9,0 -5,-5 Z' : 'M-5,-4 L20,-2.5 Q23,0 20,2.5 L-5,4 Q-8,0 -5,-4 Z', lc) + circ(0, 0, 5, shade(lc, 0.1)) + '</g>';
    } else {
      s += circ(38, 32, 4.5, shade(c, -0.2));
    }
    // ambidex zárakasztó / tárkioldó jelzés
    if (ambi) s += R(160, 40, 9, 22, shade(c, -0.08), { r: 3 });
    // puffercső-torony gyűrűje
    s += line(-10, -20, -10, 28, shade(c, -0.5), 1.4);
    return s;
  }

  function triggerGuard(c, enhanced, encolor) {
    if (enhanced) {
      return `<path d="M107,70 C106,92 96,96 84,96 L68,96 C58,96 54,86 55,52" fill="none" stroke="${encolor}" stroke-width="7" stroke-linecap="round"/>` +
        `<path d="M107,70 C106,92 96,96 84,96 L68,96 C58,96 54,86 55,52" fill="none" stroke="url(#sh-v)" stroke-width="7" stroke-linecap="round"/>`;
    }
    return `<path d="M107,72 C106,85 98,88 86,88 L68,88 C58,88 55,80 55,52" fill="none" stroke="${c}" stroke-width="6" stroke-linecap="round"/>` +
      `<path d="M107,72 C106,85 98,88 86,88 L68,88 C58,88 55,80 55,52" fill="none" stroke="${shade(c, 0.25)}" stroke-width="1" stroke-linecap="round" opacity=".35" transform="translate(0,-2)"/>`;
  }

  function trigger(style) {
    const col = '#2e3034';
    if (style === 'flat') return P('M76,48 L82,48 L84,76 L80,79 L77,78 Z', col, { sheen: 'v' });
    return P('M76,48 L82,48 Q83,64 90,76 L86,79 Q75,66 76,48 Z', col, { sheen: 'v' });
  }

  function grip(g) {
    const shapes = {
      factory: 'M2,0 L4,34 Q0,42 4,50 Q8,56 6,64 L8,108 Q8,120 -6,121 L-28,120 Q-38,118 -39,106 L-38,6 Q-37,-6 -28,-8 L-6,-6 Z',
      moe:     'M2,0 L6,40 L8,106 Q8,121 -6,122 L-30,121 Q-40,119 -40,108 L-40,10 Q-42,-4 -53,-13 L-46,-17 Q-30,-10 -16,-6 L-2,-4 Z',
      k2:      'M2,0 L6,44 L7,108 Q7,121 -7,122 L-31,121 Q-41,119 -41,107 L-41,14 Q-44,-4 -58,-13 L-50,-18 Q-30,-10 -14,-6 Z',
      miad:    'M2,0 Q10,18 4,30 L6,104 Q8,122 -8,123 L-30,122 Q-42,119 -42,106 L-40,12 Q-44,-4 -56,-12 L-48,-17 Q-30,-10 -14,-6 Z',
      bcm:     'M2,0 L5,40 L6,106 Q6,120 -8,121 L-30,120 Q-39,118 -40,106 L-39,10 Q-40,-4 -50,-12 L-42,-15 Q-26,-8 -10,-5 Z',
      bcm3:    'M2,0 L5,40 L6,106 Q6,120 -8,121 L-30,120 Q-39,118 -40,106 L-40,12 Q-44,-3 -60,-10 L-52,-16 Q-30,-9 -10,-5 Z',
      b5:      'M2,0 Q7,20 3,34 Q9,46 5,58 L7,106 Q7,121 -7,122 L-30,121 Q-40,118 -40,106 L-40,10 Q-42,-4 -52,-12 L-44,-16 Q-28,-9 -12,-5 Z',
    };
    const d = shapes[g.style] || shapes.factory;
    let s = `<g transform="translate(52,49) rotate(${g.angle || 22})">`;
    s += P(d, g.hex, { sheen: 'p', tex: g.tex || g.style === 'miad' || g.style === 'bcm' || g.style === 'bcm3' });
    if (g.style === 'miad') s += line(-36, 30, -36, 100, shade(g.hex, -0.5), 1, 0.7);
    s += '</g>';
    return s;
  }

  function bufferTube(type) {
    const len = type === 'rifle' ? 280 : 205;
    let s = cyl(-12 - len, -20, len, 32, '#2b2c2f', { r: 3 });
    s += P('M-26,-24 L-12,-24 L-12,16 L-26,16 Q-29,-4 -26,-24 Z', '#303236');
    for (let y = -20; y <= 12; y += 8) s += line(-25, y, -14, y, '#151618', 1.2, 0.8);
    s += R(-12, -26, 4, 44, '#3a3c40', { r: 1 });
    return s;
  }

  // Tusok – helyi koordináta: origó a tustalp hátulja, x előre
  function stock(st, rear, pad) {
    const c = st.hex;
    const shapes = {
      m4:     'M12,-24 L176,-22 L178,-14 L178,16 L150,18 L70,42 L30,70 L12,86 Z',
      ctr:    'M12,-26 L170,-24 L176,-16 L176,16 L150,20 L40,62 Q28,70 24,84 L12,88 Z',
      sl:     'M12,-24 Q90,-28 172,-22 L176,16 Q120,24 60,52 Q30,70 12,86 Z',
      slk:    'M12,-24 Q80,-28 150,-22 L154,16 Q110,24 56,50 Q30,66 12,84 Z',
      ubr:    'M10,-30 L190,-26 L196,-16 L196,18 L170,22 Q120,30 70,54 Q30,74 10,90 Z',
      bcm:    'M12,-28 L168,-24 L176,-14 L176,18 L130,24 L50,58 L12,88 Z',
      sopmod: 'M12,-30 L172,-26 L178,-16 L178,20 L150,24 Q100,24 70,42 L40,64 L12,90 Z',
      bravo:  'M12,-26 L172,-24 L176,16 L156,20 L56,50 Q26,64 12,88 Z',
    };
    let s = '';
    if (st.style === 'prs' || st.style === 'prslite' || st.style === 'b5prec') {
      const L = rear - 12;
      if (st.style === 'prs') {
        s += P(`M18,-44 L150,-46 Q160,-46 168,-38 L${L - 80},-24 L${L},-22 L${L},16 L${L - 70},18 L170,30 L128,38 L120,60 L92,60 L86,44 L46,62 Q22,76 18,92 Z`, c, { sheen: 'p' });
        s += circ(L - 100, -14, 9, '#2a2b2e');
        s += circ(48, 16, 9, '#2a2b2e');
        s += line(24, -36, 150, -38, shade(c, 0.2), 1, 0.5);
      } else if (st.style === 'prslite') {
        s += P(`M18,-36 L150,-38 Q170,-36 ${L - 70},-24 L${L},-22 L${L},16 L${L - 70},18 Q130,30 90,48 L46,64 Q22,76 18,92 Z M60,0 L130,-4 Q140,4 120,18 L70,30 Q56,20 60,0 Z`, c, { sheen: 'p', evenodd: true });
      } else {
        s += P(`M18,-38 L${L - 60},-30 L${L},-22 L${L},16 L${L - 80},20 Q120,30 96,44 L60,66 Q30,80 18,94 Z M70,2 L150,-2 L150,12 L80,26 Q66,18 70,2 Z`, c, { sheen: 'p', evenodd: true });
        s += circ(36, -14, 7, '#2a2b2e');
      }
      s += pad18(c, pad, 96, -46);
      return `<g transform="translate(${-rear},0)">${s}</g>`;
    }
    const d = shapes[st.style] || shapes.m4;
    s += P(d, c, { sheen: 'p' });
    // részletek: kioldókar, szíjnyílás
    s += R(st.style === 'ubr' ? 166 : 146, 16, 24, 8, shade(c, -0.25), { r: 2 });
    if (st.style === 'ctr') s += R(120, 22, 28, 10, shade(c, -0.15), { r: 3 });
    if (st.style === 'm4') s += R(26, 38, 20, 9, shade(c, -0.55), { r: 2, sheen: false });
    if (st.style === 'bravo' || st.style === 'bcm') s += circ(150, -4, 5, shade(c, -0.4));
    if (st.style === 'sopmod') s += R(40, -24, 110, 14, shade(c, -0.08), { r: 6 });
    s += line(20, -18, 160, -17, shade(c, 0.25), 1, 0.35);
    s += pad18(c, pad, 88, -26);
    return `<g transform="translate(${-rear},0)">${s}</g>`;
  }
  function pad18(c, pad, bottom, top) {
    const t = pad === 'pad70' ? 16 : pad === 'pad55' ? 13 : 11;
    const col = pad && pad !== 'std' ? '#26272a' : shade(c, -0.25);
    let s = P(`M${12 - t},${top + 2} Q${12 - t},${top} ${12 - t + 3},${top} L13,${top} L13,${bottom} L${12 - t + 3},${bottom} Q${12 - t},${bottom} ${12 - t},${bottom - 3} Z`, col, { sheen: 'p' });
    if (pad === 'pad70') for (let y = top + 10; y < bottom - 6; y += 12) s += R(12 - t + 4, y, t - 7, 6, '#121314', { r: 2, sheen: false });
    else for (let y = top + 8; y < bottom; y += 6) s += line(12 - t + 2, y, 11, y, shade(col, -0.5), 0.8, 0.7);
    return s;
  }

  function barrel(len, stainless) {
    const mx = BF + len * S;
    const col = stainless ? '#a9abae' : '#1f2022';
    let s = cyl(UF - 6, -9, mx - UF + 6, 18, col, { r: 1 });
    s += cyl(mx - 10, -7, 10, 14, shade(col, 0.1), { r: 1 });
    return { s, mx };
  }

  function handguard(len, style, c) {
    const x0 = UF, x1 = UF + len * S;
    let s = '';
    if (style === 'quad') {
      s += P(`M${x0},-30 L${x1 - 6},-30 L${x1},-24 L${x1},24 L${x1 - 6},30 L${x0 + 6},30 L${x0},24 Z`, c);
      s += sideRail(x0 + 10, x1 - 8, 0, c);
      s += picRail(x0, x1, RAIL_TOP, c);
      return { s, x0, x1 };
    }
    const bodyTop = -30, bodyBot = style === 'dyn' ? 22 : 26;
    s += P(`M${x0},${bodyTop} L${x1 - 3},${bodyTop} L${x1},${bodyTop + 5} L${x1},${bodyBot - 6} L${x1 - 7},${bodyBot} L${x0 + 10},${bodyBot} L${x0},${bodyBot - 6} Z`, c);
    // alsó él finom árnyék, felső él fény
    s += line(x0 + 10, bodyBot - 1, x1 - 8, bodyBot - 1, shade(c, -0.5), 1.4, 0.6);
    s += line(x0 + 4, 9, x1 - 4, 9, shade(c, 0.22), 0.8, 0.35);
    const dark = shade(c, -0.72);
    const slotRow = (y, h, len2, step, start) => {
      let o = '';
      for (let x = x0 + start; x + len2 <= x1 - 12; x += step) o += `<rect x="${x.toFixed(1)}" y="${y}" width="${len2}" height="${h}" rx="${h / 2}" fill="${dark}"/>`;
      return o;
    };
    if (style === 'dyn') {
      // vázszerkezetes, kivágott előagy
      for (let x = x0 + 30; x + 46 <= x1 - 10; x += 56) {
        s += `<path d="M${x},-20 L${x + 40},-20 L${x + 30},12 L${x - 10},12 Z" fill="${dark}"/>`;
      }
    } else if (style === 'mfr') {
      s += slotRow(-8, 9, 35, 44, 36);
      s += slotRow(14, 6, 35, 44, 36);
      for (let x = x0 + 26; x < x1 - 14; x += 22) s += `<circle cx="${x}" cy="-21" r="2.6" fill="${dark}"/>`;
    } else if (style === 'mcmr') {
      s += slotRow(-8, 9, 35, 44, 30);
      s += slotRow(14, 6, 35, 44, 52);
      s += `<circle cx="${x1 - 16}" cy="16" r="5" fill="${dark}"/>`;
    } else { // schm (gyári M-LOK)
      s += slotRow(-8, 9, 35, 44, 34);
      s += slotRow(14, 6, 35, 44, 34);
      for (let x = x0 + 56; x < x1 - 30; x += 44) s += `<rect x="${x}" y="-23" width="12" height="4" rx="2" fill="${dark}"/>`;
    }
    // hátsó rögzítő gyűrű
    s += R(x0, -30, 10, 56 - (26 - bodyBot), shade(c, -0.06), { r: 1 });
    s += picRail(x0, x1, RAIL_TOP, c);
    return { s, x0, x1 };
  }

  function muzzle(m, mx) {
    const x0 = mx - 6;
    const col = '#222326';
    let s = '';
    switch (m.style) {
      case 'a2': {
        s += cyl(x0, -10, 49, 20, col, { r: 2 });
        s += R(x0 + 14, -7, 30, 3.4, '#09090a', { r: 1.5, sheen: false });
        s += R(x0 + 14, 0, 30, 3.4, '#09090a', { r: 1.5, sheen: false });
        s += line(x0 + 8, -10, x0 + 8, 10, '#111', 1);
        return { s, end: x0 + 49 };
      }
      case 'comp': {
        s += cyl(x0, -11, 52, 22, col, { r: 3 });
        for (let i = 0; i < 3; i++) s += R(x0 + 16 + i * 11, -10, 6, 9, '#09090a', { r: 2, sheen: false });
        return { s, end: x0 + 52 };
      }
      case 'bcmcomp': {
        s += cyl(x0, -11, 49, 22, col, { r: 3 });
        s += R(x0 + 18, -10, 22, 8, '#09090a', { r: 3, sheen: false });
        s += R(x0 + 18, 3, 22, 5, '#09090a', { r: 2, sheen: false });
        return { s, end: x0 + 49 };
      }
      case '3p': {
        s += cyl(x0, -11, 22, 22, col, { r: 2 });
        s += P(`M${x0 + 22},-11 L${x0 + 56},-10 L${x0 + 56},-5 L${x0 + 22},-4 Z`, col, { sheen: 'c' });
        s += P(`M${x0 + 22},3 L${x0 + 56},4 L${x0 + 56},10 L${x0 + 22},11 Z`, col, { sheen: 'c' });
        return { s, end: x0 + 56 };
      }
      case 'warcomp': {
        s += cyl(x0, -11.5, 63, 23, col, { r: 3 });
        s += R(x0 + 6, -11.5, 14, 23, shade(col, 0.1), { r: 1 });
        for (let i = 0; i < 3; i++) s += P(`M${x0 + 34 + i * 9},-11 L${x0 + 40 + i * 9},-11 L${x0 + 36 + i * 9},-2 L${x0 + 30 + i * 9},-2 Z`, '#09090a', { sheen: false });
        s += R(x0 + 36, 3, 24, 5, '#09090a', { r: 2, sheen: false });
        return { s, end: x0 + 63 };
      }
      case 'can': {
        const L = m.len * S, r = (m.dia * S) / 2;
        s += cyl(x0 - 4, -13, 30, 26, '#2a2b2e', { r: 2 });
        s += cyl(x0 + 18, -r, L - 18, 2 * r, '#2d2e31', { r: 6 });
        s += line(x0 + 40, -r + 1, x0 + 40, r - 1, '#111', 1.2, 0.8);
        s += line(x0 + L - 24, -r + 1, x0 + L - 24, r - 1, '#111', 1.2, 0.8);
        s += cyl(x0 + L - 6, -r + 3, 8, 2 * r - 6, '#25262a', { r: 3 });
        return { s, end: x0 + L + 2 };
      }
    }
    return muzzle({ style: 'a2' }, mx);
  }

  // ------------------------------------------------------------ optikák
  const glass = (x, y, w, h) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.min(w, h) / 2}" fill="url(#glass)"/>`;

  function redDot(o, x, scale) {
    // x: a test hátulja; visszaad {s, x0, x1, axis}
    const k = scale || 1;
    const c = '#26282b';
    const axisH = (o.axis || 1.5) * S;
    const ay = RAIL_TOP - axisH;
    let s = '';
    let w = 70;
    switch (o.style) {
      case 'micro': {
        w = 62;
        s += P(`M${x + 6},${RAIL_TOP} L${x + 56},${RAIL_TOP} L${x + 54},${RAIL_TOP - 10} L${x + 46},${ay + 10} L${x + 16},${ay + 10} L${x + 8},${RAIL_TOP - 10} Z`, '#2b2d30');
        s += cyl(x, ay - 16, w, 32, c, { r: 6 });
        s += cyl(x - 3, ay - 17, 8, 34, shade(c, 0.08), { r: 3 });
        s += cyl(x + w - 5, ay - 17, 8, 34, shade(c, 0.08), { r: 3 });
        s += circ(x + 34, ay, 8, shade(c, 0.1));
        s += R(x + 26, ay - 26, 16, 10, shade(c, 0.05), { r: 2 });
        break;
      }
      case 'tube': {
        w = 90;
        s += P(`M${x + 16},${RAIL_TOP} L${x + 74},${RAIL_TOP} L${x + 70},${ay + 14} L${x + 20},${ay + 14} Z`, '#2b2d30');
        s += cyl(x, ay - 20, w, 40, c, { r: 6 });
        s += R(x - 6, ay - 22, 10, 44, '#202124', { r: 3 });
        s += R(x + w - 4, ay - 22, 10, 44, '#202124', { r: 3 });
        s += circ(x + 60, ay, 10, shade(c, 0.1));
        s += R(x + 22, ay + 10, 40, 16, '#2b2d30', { r: 3 });
        break;
      }
      case 'acro': {
        w = 44;
        s += R(x - 2, RAIL_TOP - 12, 50, 12, '#2b2d30', { r: 2 });
        s += P(`M${x},${RAIL_TOP - 12} L${x + 44},${RAIL_TOP - 12} L${x + 44},${ay - 10} Q${x + 44},${ay - 16} ${x + 38},${ay - 16} L${x + 8},${ay - 16} Q${x},${ay - 16} ${x},${ay - 8} Z`, c);
        s += circ(x + 22, ay + 2, 5, shade(c, 0.1));
        break;
      }
      case 'holo': {
        w = o.short ? 82 : 100;
        const top = ay - 26;
        s += R(x - 2, RAIL_TOP - 12, w + 4, 12, '#2b2d30', { r: 2 });
        s += P(`M${x},${RAIL_TOP - 12} L${x + w},${RAIL_TOP - 12} L${x + w},${top + 6} Q${x + w},${top} ${x + w - 6},${top} L${x + 10},${top} Q${x},${top} ${x},${top + 10} Z`, c);
        s += P(`M${x + 14},${top + 8} L${x + w - 8},${top + 8} L${x + w - 8},${RAIL_TOP - 20} L${x + 14},${RAIL_TOP - 20} Z`, shade(c, -0.25), { sheen: false });
        s += glass(x + w - 8, top + 6, 5, RAIL_TOP - 20 - top - 4);
        s += R(x - 6, RAIL_TOP - 26, 8, 8, '#1a1b1d', { r: 2 });
        s += R(x - 6, RAIL_TOP - 38, 8, 8, '#1a1b1d', { r: 2 });
        if (!o.short) s += R(x + 30, RAIL_TOP - 8, 24, 10, '#36383c', { r: 3 });
        break;
      }
      case 'uh1': {
        w = 96;
        const top = ay - 28;
        s += R(x - 2, RAIL_TOP - 12, w + 4, 12, '#2b2d30', { r: 2 });
        s += P(`M${x},${RAIL_TOP - 12} L${x + w},${RAIL_TOP - 12} L${x + w - 6},${top} L${x + 16},${top} Q${x + 4},${top} ${x},${top + 14} Z`, c);
        s += P(`M${x + 20},${top + 8} L${x + w - 14},${top + 8} L${x + w - 16},${RAIL_TOP - 22} L${x + 20},${RAIL_TOP - 22} Z`, shade(c, -0.25), { sheen: false });
        break;
      }
      case 'reflex': {
        w = 82;
        const top = ay - 30;
        s += R(x - 2, RAIL_TOP - 12, w + 4, 12, '#2b2d30', { r: 2 });
        s += P(`M${x},${RAIL_TOP - 12} L${x + w},${RAIL_TOP - 12} L${x + w},${top + 8} Q${x + w},${top} ${x + w - 8},${top} L${x + 8},${top} Q${x},${top} ${x},${top + 8} Z M${x + 12},${top + 9} L${x + w - 12},${top + 9} L${x + w - 12},${RAIL_TOP - 20} L${x + 12},${RAIL_TOP - 20} Z`, c, { evenodd: true });
        s += R(x + 18, top - 3, 46, 5, '#1b2a44', { r: 1.5, sheen: 'v' });
        s += glass(x + w - 14, top + 9, 4, RAIL_TOP - 20 - top - 9);
        break;
      }
      case 'aems': {
        w = 70;
        const top = ay - 28;
        s += R(x - 2, RAIL_TOP - 12, w + 4, 12, '#2b2d30', { r: 2 });
        s += P(`M${x},${RAIL_TOP - 12} L${x + w},${RAIL_TOP - 12} L${x + w},${top + 6} Q${x + w},${top} ${x + w - 6},${top} L${x + 6},${top} Q${x},${top} ${x},${top + 6} Z`, c);
        s += R(x + 14, top - 3, 42, 5, '#1b2a44', { r: 1.5 });
        s += P(`M${x + 10},${top + 10} L${x + w - 10},${top + 10} L${x + w - 10},${RAIL_TOP - 22} L${x + 10},${RAIL_TOP - 22} Z`, shade(c, -0.2), { sheen: false });
        break;
      }
      case 'mro': {
        w = 66;
        s += P(`M${x + 8},${RAIL_TOP} L${x + 58},${RAIL_TOP} L${x + 52},${ay + 12} L${x + 14},${ay + 12} Z`, '#2b2d30');
        s += P(`M${x},${ay - 14} L${x + w},${ay - 22} L${x + w},${ay + 22} L${x},${ay + 14} Z`, c, { sheen: 'c' });
        s += circ(x + 32, ay - 2, 9, shade(c, 0.08));
        s += R(x + 24, ay - 30, 16, 10, shade(c, 0.05), { r: 2 });
        break;
      }
    }
    if (k !== 1) s = `<g transform="translate(${x + w / 2},${RAIL_TOP}) scale(${k}) translate(${-(x + w / 2)},${-RAIL_TOP})">${s}</g>`;
    return { s, x0: x - 6, x1: x + w + 6, axis: ay };
  }

  function scope(o, mountStyle) {
    const c = '#25272a';
    const L = o.len * S;
    const objR = o.obj >= 28 ? 21 : 19;
    const ay = RAIL_TOP - 1.93 * S; // 1,93" tengelymagasság
    const er = 22;                   // okulár hátulja
    const xf = er + L;
    let s = '';
    let m0, m1;
    if (o.style === 'acog') {
      const x = 62, ayA = RAIL_TOP - 1.45 * S;
      s += R(x + 10, RAIL_TOP - 12, 120, 12, '#2b2d30', { r: 2 });
      s += R(x + 112, RAIL_TOP - 24, 18, 14, '#36383c', { r: 4 });
      s += P(`M${x},${ayA - 17} L${x + 34},${ayA - 15} L${x + 54},${ayA - 18} L${x + 150},${ayA - 18} L${x + 158},${ayA - 22} L${x + 158},${ayA + 22} L${x + 150},${ayA + 18} L${x + 40},${ayA + 18} L${x + 34},${ayA + 15} L${x},${ayA + 17} Z`, c, { sheen: 'c' });
      s += P(`M${x + 70},${ayA - 18} L${x + 100},${ayA - 46} L${x + 132},${ayA - 46} L${x + 146},${ayA - 18} Z`, c);
      s += R(x + 100, ayA - 46, 32, 6, '#a37b2f', { r: 2, sheen: 'v' });
      s += circ(x + 100, ayA, 9, shade(c, 0.1));
      s += glass(x + 156, ayA - 18, 4, 36);
      return { s, x0: x + 6, x1: x + 136, axis: ayA, front: x + 160, rear: x };
    }
    // tubus profil
    const tr = 16;
    s += P(`M${er},${ay - 21} L${er + 60},${ay - 21} L${er + 84},${ay - tr} L${xf - 78},${ay - tr} L${xf - 46},${ay - objR} L${xf},${ay - objR} L${xf},${ay + objR} L${xf - 46},${ay + objR} L${xf - 78},${ay + tr} L${er + 84},${ay + tr} L${er + 60},${ay + 21} L${er},${ay + 21} Z`, c, { sheen: 'c' });
    // nagyítás-állító gyűrű + kar
    s += R(er + 32, ay - 22, 26, 44, shade(c, 0.04), { r: 2, sheen: 'c' });
    for (let x = er + 35; x < er + 56; x += 4) s += line(x, ay - 21, x, ay + 21, '#111', 1, 0.6);
    s += P(`M${er + 40},${ay - 22} L${er + 50},${ay - 22} L${er + 48},${ay - 34} L${er + 42},${ay - 34} Z`, c);
    // tornyok
    const tc = er + L * 0.52;
    s += cyl(tc - 15, ay - tr - 22, 30, 22, shade(c, 0.05), { r: 3 });
    s += circ(tc, ay, 15, shade(c, 0.06));
    s += circ(tc, ay, 9, shade(c, -0.1));
    s += glass(xf - 3, ay - objR + 3, 4, objR * 2 - 6);
    s += glass(er - 2, ay - 18, 3, 36);
    // szerelék
    if (o.integralMount || mountStyle) {
      const st = o.integralMount ? 'cant' : mountStyle;
      const r1 = tc - 70, r2 = tc + 62;
      m0 = r1 - 26; m1 = r2 + 30;
      s = R(m0, RAIL_TOP - 12, m1 - m0, 12, st === 'geissele' ? '#3a3a35' : '#2b2d30', { r: 2 }) + s;
      for (const rx of [r1, r2]) {
        s += P(`M${rx - 12},${RAIL_TOP - 10} L${rx + 12},${RAIL_TOP - 10} L${rx + 10},${ay + tr} L${rx - 10},${ay + tr} Z`, st === 'geissele' ? '#3c3c37' : '#2b2d30');
        s += R(rx - 11, ay - tr - 3, 22, 2 * tr + 6, st === 'geissele' ? '#3c3c37' : '#2e3033', { r: 3, sheen: 'c' });
        s += circ(rx, ay + tr - 3, 2.5, '#111');
        s += circ(rx, ay - tr + 3, 2.5, '#111');
      }
    }
    return { s, x0: m0 != null ? m0 : er + 60, x1: m1 != null ? m1 : xf - 80, axis: ay, front: xf, rear: er, mounted: !!(o.integralMount || mountStyle) };
  }

  function magnifier(m, xFront, axis) {
    const c = '#26282b';
    const w = m.style === 'g33' ? 112 : 96;
    const x = xFront - w;
    let s = R(x + 18, RAIL_TOP - 12, w - 30, 12, '#2b2d30', { r: 2 });
    s += P(`M${x + 26},${RAIL_TOP - 10} L${x + w - 26},${RAIL_TOP - 10} L${x + w - 34},${axis + 12} L${x + 34},${axis + 12} Z`, '#2b2d30');
    s += P(`M${x},${axis - 18} L${x + 28},${axis - 18} L${x + 36},${axis - 15} L${x + w - 10},${axis - 15} L${x + w},${axis - 19} L${x + w},${axis + 19} L${x + w - 10},${axis + 15} L${x + 36},${axis + 15} L${x + 28},${axis + 18} L${x},${axis + 18} Z`, c, { sheen: 'c' });
    s += circ(x + w / 2 + 6, axis + 14, 6, '#323438');
    s += glass(x - 2, axis - 15, 3, 30);
    return { s, x0: x + 16, x1: x + w - 10 };
  }

  function buis(b, hx1, deployed) {
    const c = b.hex || '#26282b';
    const rear = (x) => deployed
      ? P(`M${x},${RAIL_TOP} L${x + 30},${RAIL_TOP} L${x + 28},${RAIL_TOP - 10} L${x + 22},${RAIL_TOP - 40} Q${x + 15},${RAIL_TOP - 50} ${x + 8},${RAIL_TOP - 40} L${x + 2},${RAIL_TOP - 10} Z`, c) + circ(x + 15, RAIL_TOP - 38, 4.5, '#0e0e0f', { sheen: false })
      : P(`M${x},${RAIL_TOP} L${x + 30},${RAIL_TOP} L${x + 30},${RAIL_TOP - 9} L${x + 6},${RAIL_TOP - 13} L${x},${RAIL_TOP - 9} Z`, c);
    const front = (x) => deployed
      ? P(`M${x},${RAIL_TOP} L${x + 28},${RAIL_TOP} L${x + 26},${RAIL_TOP - 10} L${x + 20},${RAIL_TOP - 34} L${x + 16},${RAIL_TOP - 50} L${x + 12},${RAIL_TOP - 50} L${x + 8},${RAIL_TOP - 34} L${x + 2},${RAIL_TOP - 10} Z`, c)
      : P(`M${x},${RAIL_TOP} L${x + 28},${RAIL_TOP} L${x + 28},${RAIL_TOP - 9} L${x + 24},${RAIL_TOP - 13} L${x},${RAIL_TOP - 9} Z`, c);
    let s;
    if (b.style === 'offset') {
      // 45°-ban elforgatott, oldalról kisebbnek látszó irányzék
      s = `<g opacity=".95">` + P(`M8,${RAIL_TOP + 2} L36,${RAIL_TOP + 2} L32,${RAIL_TOP - 24} Q22,${RAIL_TOP - 32} 12,${RAIL_TOP - 24} Z`, c) + circ(22, RAIL_TOP - 20, 3.5, '#0e0e0f', { sheen: false }) +
        P(`M${hx1 - 36},${RAIL_TOP + 2} L${hx1 - 8},${RAIL_TOP + 2} L${hx1 - 16},${RAIL_TOP - 32} L${hx1 - 26},${RAIL_TOP - 32} Z`, c) + '</g>';
      return { s, spans: [[6, 38], [hx1 - 38, hx1 - 6]] };
    }
    s = rear(6) + front(hx1 - 36);
    return { s, spans: [[6, 38], [hx1 - 38, hx1 - 6]] };
  }

  function chandle(style, c) {
    c = c || '#2a2c2f';
    if (style === 'raptor' || style === 'ambi') return P('M-26,-34 L4,-32 L4,-20 L-18,-20 Q-28,-22 -30,-28 Z', c) + line(-24, -27, 0, -26, shade(c, 0.3), 0.8, 0.5);
    if (style === 'bcm') return P('M-14,-32 L4,-32 L4,-21 L-14,-21 Z', c) + P('M-14,-31 L-24,-29 Q-28,-24 -22,-20 L-14,-21 Z', shade(c, -0.15));
    return P('M-12,-32 L4,-32 L4,-21 L-12,-21 Z', c);
  }

  function mag(m, c) {
    const curve30 = 'M110,30 L172,30 L172,90 Q178,168 214,226 L205,236 L158,252 Q132,172 110,92 Z';
    const curve40 = 'M110,30 L172,30 L172,90 Q180,190 232,276 L222,286 L172,302 Q136,200 110,92 Z';
    const s20 = 'M110,30 L172,30 L172,90 Q175,130 186,164 L182,172 L132,182 Q120,140 110,92 Z';
    const s10 = 'M110,30 L172,30 L172,90 L174,120 L170,128 L112,128 L110,92 Z';
    let s = '';
    const floor = (pts) => P(pts, shade(c, -0.15), { sheen: 'p' });
    switch (m.style) {
      case 'gi10':
        s += P(s10, '#3a3c3f', { sheen: 'p' });
        s += R(108, 122, 68, 10, '#1d1e20', { r: 2 });
        break;
      case 'pmag10':
        s += P(s10, c, { sheen: 'p' });
        s += R(108, 120, 68, 12, shade(c, -0.12), { r: 3 });
        break;
      case 'pmag20':
        s += P(s20, c, { sheen: 'p' });
        s += floor('M128,178 L186,166 L190,176 L132,190 Z');
        for (let i = 0; i < 4; i++) s += line(124 + i * 2, 120 + i * 12, 174 + i * 3, 116 + i * 12, shade(c, -0.4), 1.4, 0.7);
        break;
      case 'pmag30': case 'pmag30w':
        s += P(curve30, c, { sheen: 'p' });
        s += floor('M154,250 L210,230 L216,240 L160,262 Z');
        for (let i = 0; i < 5; i++) s += line(130 + i * 4, 150 + i * 14, 182 + i * 6, 140 + i * 14, shade(c, -0.4), 1.4, 0.7);
        if (m.style === 'pmag30w') s += `<path d="M122,100 L136,100 Q144,150 160,196 L148,200 Q132,152 122,100 Z" fill="url(#glass)" opacity=".9"/>` +
          [0, 1, 2, 3, 4, 5].map(i => `<rect x="${126 + i * 4}" y="${104 + i * 15}" width="10" height="9" fill="#b8913c" opacity=".85"/>`).join('');
        break;
      case 'pmag40':
        s += P(curve40, c, { sheen: 'p' });
        s += floor('M168,300 L226,280 L232,290 L174,312 Z');
        for (let i = 0; i < 6; i++) s += line(130 + i * 5, 160 + i * 16, 186 + i * 8, 150 + i * 16, shade(c, -0.4), 1.4, 0.7);
        break;
      case 'drum':
        s += P('M110,30 L172,30 L172,96 L176,110 L106,110 L110,92 Z', c, { sheen: 'p' });
        s += circ(146, 182, 78, c, { sheen: true });
        s += circ(146, 182, 30, shade(c, -0.15));
        s += R(138, 160, 16, 44, shade(c, 0.1), { r: 4 });
        break;
    }
    return s;
  }

  function sling(sl, hx1, rear, c) {
    const x1 = hx1 - 24, xr = -rear + 30;
    const sag = 150;
    const d = `M${x1},22 C${x1 - 120},${sag + 110} ${xr + 180},${sag + 120} ${xr},76`;
    let s = `<path d="${d}" fill="none" stroke="${shade(c, -0.25)}" stroke-width="${sl.style === 'vickers' ? 9 : 8}" stroke-linecap="round"/>`;
    s += `<path d="${d}" fill="none" stroke="${shade(c, 0.15)}" stroke-width="1.2" stroke-dasharray="6 5" opacity=".5"/>`;
    // csatok
    s += circ(x1, 22, 6, '#3a3c40');
    s += circ(xr, 76, 6, '#3a3c40');
    const mx = (x1 + xr) / 2 - 40;
    s += R(mx, sag + 60, 26, 12, '#3a3c40', { r: 3 });
    return s;
  }

  function light(l, hx1, onRail) {
    const c = '#24262a';
    if (l.style === 'x300') {
      const x = hx1 - 118, y = onRail ? 0 : 0;
      let s = R(x + 16, y - 10, 70, 10, '#2b2d30', { r: 2 });
      s += P(`M${x},${y + 2} L${x + 80},${y + 2} L${x + 96},${y - 2} L${x + 96},${y + 30} L${x + 80},${y + 26} L${x},${y + 26} Q${x - 6},${y + 14} ${x},${y + 2} Z`, c, { sheen: 'c' });
      s += glass(x + 94, y - 1, 4, 30);
      return { s, x0: x, x1: x + 96 };
    }
    const len = l.mini ? 104 : 138;
    const hr = l.big ? 19 : l.mini ? 12 : 15;
    const br = l.mini ? 10 : 12;
    const ay = -22;
    const x1 = hx1 - 8, x0 = x1 - len;
    let s = R(x0 + 34, ay + br - 4, 44, 14, '#2b2d30', { r: 3 });
    s += P(`M${x0},${ay - br} L${x1 - 40},${ay - br} L${x1 - 24},${ay - hr} L${x1},${ay - hr} L${x1},${ay + hr} L${x1 - 24},${ay + hr} L${x1 - 40},${ay + br} L${x0},${ay + br} Z`, c, { sheen: 'c' });
    for (let x = x0 + 12; x < x0 + 40; x += 5) s += line(x, ay - br + 1, x, ay + br - 1, '#111', 1, 0.7);
    s += R(x0 - 8, ay - br + 2, 10, 2 * br - 4, '#1b1c1e', { r: 3 });
    s += glass(x1 - 2, ay - hr + 2, 4, 2 * hr - 4);
    return { s, x0, x1 };
  }

  function mlokItem(it, hx0, hx1, hasBipod) {
    const c = it.hex || '#26282b';
    const L = hx1 - hx0;
    switch (it.style) {
      case 'rvg': {
        const x = hx0 + Math.min(L * (hasBipod ? 0.35 : 0.45), L - 120);
        return P(`M${x},26 L${x + 34},26 L${x + 32},38 L${x + 30},112 Q${x + 28},120 ${x + 18},120 L${x + 8},120 Q${x - 2},120 ${x - 1},110 L${x + 2},38 Z`, c, { sheen: 'p', tex: true });
      }
      case 'vg3': {
        const x = hx0 + Math.min(L * (hasBipod ? 0.35 : 0.45), L - 120);
        return P(`M${x},26 L${x + 36},26 L${x + 33},74 Q${x + 31},82 ${x + 22},82 L${x + 10},82 Q${x + 2},82 ${x + 2},74 Z`, c, { sheen: 'p' });
      }
      case 'kag': {
        const x = hx0 + Math.min(L * (hasBipod ? 0.3 : 0.48), L - 130);
        return P(`M${x},26 L${x + 92},26 L${x + 90},44 Q${x + 84},54 ${x + 72},50 L${x + 4},32 Z`, c, { sheen: 'p' });
      }
      case 'hstop': {
        const x = hasBipod ? hx0 + L * 0.55 : hx1 - 70;
        return P(`M${x},26 L${x + 44},26 L${x + 42},34 L${x + 30},46 Q${x + 26},49 ${x + 22},46 L${x + 4},32 Z`, c, { sheen: 'p' });
      }
      case 'rail': return sideRail(hx1 - 128, hx1 - 74, 4, '#2c2e31');
      case 'qd': return circ(hx0 + 44, 16, 7, '#2e3033') + circ(hx0 + 44, 16, 3.5, '#111', { sheen: false });
      case 'cover': return R(hx0 + 30, -12, Math.min(150, L - 170), 18, c, { r: 4, sheen: 'p', tex: true });
      case 'cable': return R(hx1 - 170, -16, 10, 12, '#2e3033', { r: 2 }) + `<path d="M${hx1 - 160},-10 Q${hx1 - 150},-4 ${hx1 - 140},-14" fill="none" stroke="#111" stroke-width="2"/>`;
    }
    return '';
  }

  function bipod(b, hx1) {
    const c = b.style === 'harris' ? '#1f2022' : (b.hex || '#26282b');
    const x = hx1 - 46;
    let s = R(x - 4, 26, 40, 14, '#2b2d30', { r: 3 });
    s += P(`M${x},34 L${x + 34},34 L${x + 38},46 L${x - 2},46 Z`, c);
    // összecsukott lábak előre
    const legLen = b.style === 'harris' ? 210 : 200;
    s += P(`M${x + 30},40 L${x + 30 + legLen},44 L${x + 30 + legLen},52 L${x + 30},50 Z`, c, { sheen: 'c' });
    s += P(`M${x + 30 + legLen - 4},42 L${x + 30 + legLen + 14},44 Q${x + 30 + legLen + 18},48 ${x + 30 + legLen + 14},53 L${x + 30 + legLen - 4},54 Z`, '#151516');
    if (b.style === 'harris') for (let i = 0; i < 6; i++) s += line(x + 40 + i * 5, 40, x + 42 + i * 5, 52, '#5a5c5f', 1.2);
    return s;
  }

  // ------------------------------------------------------------ összeállítás
  const EXPLODE = {
    sling: [0, 0], tube: [-110, 0], stock: [-200, 20], lower: [0, 140], trigger: [0, 200], grip: [-40, 250],
    mag: [10, 320], bcg: [-30, -170], upper: [0, 0], ch: [-190, -110], barrel: [130, 0], handguard: [130, -110],
    muzzle: [300, 0], optic: [-10, -230], lpvo: [-10, -260], magnifier: [-150, -230], buis: [0, -300],
    light: [190, -190], mlok: [150, 150], bipod: [220, 240], controls: [-80, 180], base: [0, 0],
  };

  const LABELS = {
    sling: 'Szíj', tube: 'Puffercső', stock: 'Tus', lower: 'Alsó tok', trigger: 'Elsütő', grip: 'Markolat', mag: 'Tár',
    bcg: 'Zárszerkezet', upper: 'Felső tok', ch: 'Felhúzókar', barrel: 'Cső', handguard: 'Előagy', muzzle: 'Csőtorkolat',
    optic: 'Red dot', lpvo: 'LPVO', magnifier: 'Nagyító', buis: 'Vas irányzék', light: 'Lámpa', mlok: 'M-LOK', bipod: 'Bipod', controls: 'Kezelőszervek', base: 'Alapfegyver', mount: 'Optikaszerelék',
  };

  // Fotós módban a robbantás irányai a fegyver irányához igazodnak (dir: +1 = a cső jobbra, -1 = balra néz).
  const EXPLODE_PHOTO = {
    stock: [-190, 30], mag: [30, 170], grip: [-20, 150], muzzle: [150, 0], optic: [-10, -130], lpvo: [10, -160], magnifier: [-120, -130],
    mount: [10, -70], light: [100, -120], bipod: [120, 130], mlok: [70, 110], buis: [0, -150], base: [0, 0],
  };
  function group(cat, content, labelOverride, dir) {
    if (!content) return '';
    let [ex, ey] = dir ? (EXPLODE_PHOTO[cat] || [0, 0]) : (EXPLODE[cat] || [0, 0]);
    if (dir) ex *= dir;
    return `<g class="part" data-cat="${cat}" data-label="${labelOverride || LABELS[cat] || cat}" style="--ex:${ex}px;--ey:${ey}px"><g class="inner">${content}</g></g>`;
  }

  function renderPhoto(spec) {
    const pr = window.AR_PHOTO_RENDER && window.AR_PHOTO_RENDER.render(spec);
    if (!pr) return null;
    return { svg: pr.parts.map(p => group(p.cat, p.svg, null, pr.metrics.dir)).join(''), metrics: pr.metrics, photo: true };
  }

  function render(spec) {
    const ph = renderPhoto(spec);
    if (ph) return ph;
    const fin = spec.finish;
    const out = [];
    const spans = []; // felső sín foglaltság: [x0,x1,név]
    const rear = spec.stockRear * S;

    const hg = handguard(spec.hgLen, spec.hgStyle, fin);
    const br = barrel(spec.barrel, false);
    const mz = muzzle(spec.muzzle, br.mx);

    if (spec.sling) out.push(group('sling', sling(spec.sling, hg.x1, rear, spec.sling.hex)));
    out.push(group('tube', bufferTube(spec.stock.type)));
    out.push(group('stock', stock(spec.stock, rear, spec.pad)));
    out.push(group('mag', mag(spec.mag, spec.mag.hex)));
    out.push(group('grip', grip(spec.grip)));
    out.push(group('lower', lower(fin, spec.ambi, spec.controls) + triggerGuard(fin, spec.controls.includes('tguard'), spec.tguardHex)));
    out.push(group('trigger', trigger(spec.trigger)));
    out.push(group('bcg', bcg('#2c2d2f')));
    out.push(group('upper', upper(fin, spec.ambi)));
    out.push(group('ch', chandle(spec.ch.style, spec.ch.hex)));
    out.push(group('barrel', br.s));
    out.push(group('handguard', hg.s));
    out.push(group('muzzle', mz.s));

    const hasRail = spec.mlok.some(m => m.style === 'rail');
    if (spec.mlok.length) out.push(group('mlok', spec.mlok.map(m => mlokItem(m, hg.x0, hg.x1, !!spec.bipod)).join('')));
    if (spec.bipod) out.push(group('bipod', bipod(spec.bipod, hg.x1)));
    let lightInfo = null;
    if (spec.light) { lightInfo = light(spec.light, hg.x1, hasRail); out.push(group('light', lightInfo.s)); }

    // optikák
    let lp = null, rd = null, mg = null;
    if (spec.lpvo) {
      lp = scope(spec.lpvo, spec.mount);
      out.push(group('lpvo', lp.s));
      spans.push([lp.x0, lp.x1, 'LPVO/szerelék']);
    }
    if (spec.optic) {
      if (lp) {
        // offset red dot az LPVO elé, kisebb méretben
        rd = redDot(spec.optic, Math.max(lp.front + 14, lp.x1 + 10), 0.72);
      } else {
        const w = { micro: 62, tube: 90, acro: 44, holo: spec.optic.short ? 82 : 100, uh1: 96, reflex: 82, aems: 70, mro: 66 }[spec.optic.style] || 70;
        rd = redDot(spec.optic, UF - w + (spec.optic.style === 'holo' || spec.optic.style === 'uh1' ? 14 : -6));
      }
      out.push(group('optic', rd.s));
      spans.push([rd.x0, rd.x1, 'red dot']);
    }
    if (spec.magnifier && rd && !lp) {
      mg = magnifier(spec.magnifier, rd.x0 - 4, rd.axis);
      out.push(group('magnifier', mg.s));
      spans.push([mg.x0, mg.x1, 'nagyító']);
    }
    if (spec.buis) {
      const deployed = !spec.optic && !spec.lpvo;
      const b = buis(spec.buis, hg.x1, deployed);
      out.push(group('buis', b.s));
      b.spans.forEach(sp => spans.push([sp[0], sp[1], 'vas irányzék']));
    }

    // sín-ütközések
    const railClash = [];
    for (let i = 0; i < spans.length; i++) for (let j = i + 1; j < spans.length; j++) {
      const a = spans[i], b = spans[j];
      if (a[0] < b[1] - 2 && b[0] < a[1] - 2) railClash.push(`${a[2]} ↔ ${b[2]}`);
    }
    const railEnd = hg.x1;
    spans.forEach(sp => { if (sp[1] > railEnd + 4) railClash.push(`${sp[2]} túllóg a sínen`); });

    return {
      svg: out.join(''),
      metrics: { muzzleEnd: mz.end, muzzleLen: (mz.end - br.mx) / S, rearPx: rear, railClash, hgX1: hg.x1 },
    };
  }

  const DEFS = `
    <linearGradient id="sh-v" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#fff" stop-opacity=".26"/><stop offset=".28" stop-color="#fff" stop-opacity=".05"/>
      <stop offset=".62" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".34"/>
    </linearGradient>
    <linearGradient id="sh-p" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#fff" stop-opacity=".16"/><stop offset=".45" stop-color="#fff" stop-opacity="0"/>
      <stop offset="1" stop-color="#000" stop-opacity=".32"/>
    </linearGradient>
    <linearGradient id="sh-c" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#000" stop-opacity=".35"/><stop offset=".2" stop-color="#fff" stop-opacity=".32"/>
      <stop offset=".38" stop-color="#fff" stop-opacity=".06"/><stop offset=".72" stop-color="#000" stop-opacity=".12"/>
      <stop offset="1" stop-color="#000" stop-opacity=".5"/>
    </linearGradient>
    <radialGradient id="sh-r" cx=".35" cy=".3" r=".8">
      <stop offset="0" stop-color="#fff" stop-opacity=".3"/><stop offset=".6" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".35"/>
    </radialGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#9fd3ff" stop-opacity=".9"/><stop offset=".45" stop-color="#3a6f9e" stop-opacity=".9"/>
      <stop offset=".55" stop-color="#7a4fa0" stop-opacity=".85"/><stop offset="1" stop-color="#1b2740" stop-opacity=".95"/>
    </linearGradient>
    <pattern id="tex-dots" width="4" height="4" patternUnits="userSpaceOnUse">
      <circle cx="1" cy="1" r=".7" fill="#000" opacity=".55"/><circle cx="3" cy="3" r=".7" fill="#fff" opacity=".12"/>
    </pattern>
    <filter id="soft" x="-20%" y="-50%" width="140%" height="200%"><feGaussianBlur stdDeviation="14"/></filter>
    <filter id="glow" x="-10%" y="-10%" width="120%" height="120%">
      <feDropShadow dx="0" dy="0" stdDeviation="3" flood-color="#ff9a3c" flood-opacity=".95"/>
    </filter>
    <linearGradient id="reflFade" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#fff" stop-opacity=".22"/><stop offset=".5" stop-color="#fff" stop-opacity="0"/>
    </linearGradient>`;

  return { render, DEFS, S, BF, LABELS, shade };
})();
