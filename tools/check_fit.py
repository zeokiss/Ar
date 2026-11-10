#!/usr/bin/env python3
"""
Az alkatrészek illesztésének ellenőrzése (tár és markolat) – kiadás előtt futtasd.

A böngészőben használt transzformációt pontosan lemásolva (align_lib.py) megméri minden alapfegyver × tár/markolat
kombinációnál, hogy a csere-alkatrész illeszkedik-e a gyári helyére:

  tár:      a tengely dőlése egyezik a tárnyílás tengelyével (+-3 fok), a középvonal egybeesik (+-0.12"),
            a tárnyílás alatti első szakaszon a tár nem lóg ki a gyári tár éleihez képest (elöl +-0.20", hátul +-0.30")
  markolat: a középvonal dőlése egyezik a gyáriéval (+-6 fok), a tok alatti sávban a középvonal egybeesik (+-0.18")
  mindkettő: a kész kép (alap + csere-alkatrészek) nem tartalmaz a gyárihoz képest új, keskeny rést (zárással mért
            többlet-felület <= 0.12 négyzethüvelyk), ami szétnyíló vagy kilógó élt jelentene.

Kilépési kód: 0 = rendben, 1 = hiba van. Használat:  python3 tools/check_fit.py [-v]
"""
import os, re, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from align_lib import *

VERBOSE = '-v' in sys.argv
recs = load_records()
src = open(os.path.join(ROOT, 'js', 'data.js'), encoding='utf-8').read()
cat_of = dict(re.findall(r"\{ id: '([^']+)', cat: '([^']+)'", src))
BASES = [r['item'] for r in recs if r['slot'] == 'geo']
errors, warns, rows = [], [], 0
GAP_MAX = 0.12   # négyzethüvelyk: ennél nagyobb új rés már szembetűnő (a ravaszvédő és a markolat között kis hézag természetes)


def with_adj(rec, a):
    """Az alapfegyver-korrekció (geo.adj) hozzáadása a rekord forgatásához/eltolásához, mint a photo-render.js."""
    a = a or {}
    return dict(rec, rot=(rec.get('rot') or 0) + (a.get('rot') or 0), dx=(rec.get('dx') or 0) + (a.get('dx') or 0),
                dy=(rec.get('dy') or 0) + (a.get('dy') or 0))


def fill_band(poly, depth):
    """A kitöltő-színátmenet ott számít, ahol az átlátszatlansága legalább 50%: a sáv felső fele."""
    top = min(p[1] for p in poly)
    m = poly_mask(poly).copy()   # a gyári alkatrész sziluettje a sáv tetején (geo.fillpoly)
    m[gy(top + depth / 2):, :] = False
    return m


def below(mask, poly):
    """A csere-alkatrész a vágási poligon teteje fölött nem látszik (mint a photo-render.js clipBelow)."""
    top = gy(min(p[1] for p in poly))
    out = mask.copy(); out[:top, :] = False
    return out


def narrow_gap_area(total, roi):
    """A zárással (dilatáció+erózió 0.24"-es ablakkal) kitöltődő keskeny rések területe a ROI-ban (négyzethüvelyk)."""
    x0, x1, y0, y1 = roi
    sub = Image.fromarray((total[gy(y0):gy(y1), gx(x0):gx(x1)] * 255).astype(np.uint8))
    k = int(0.24 * RES) | 1
    closed = sub.filter(ImageFilter.MaxFilter(k)).filter(ImageFilter.MinFilter(k))
    diff = (np.asarray(closed) > 0) & ~(np.asarray(sub) > 0)
    return diff.sum() / RES / RES


for base_id in BASES:
    geo = [r for r in recs if r['item'] == base_id and r['slot'] == 'geo'][0]
    pts, cuts, adj = geo['pts'], geo['cuts'], geo.get('adj') or {}
    brec = find(recs, base_id, 'base', 'black') or [r for r in recs if r['item'] == base_id and r['slot'] == 'base'][0]
    bm = alpha(to_grid(brec, 0, 0, brec['facing']))
    magp, gripp = poly_mask(cuts['mag']), poly_mask(cuts['grip'])
    fac_mag = center_fit(bm & magp, np.arange(2.9, 3.9, 0.1), 2.0, 10.0)
    fac_grip = center_fit(bm & gripp, np.arange(2.6, 4.4, 0.2), -2, 3)
    fac_grip_top = center_fit(bm & gripp, np.arange(2.2, 2.7, 0.1), -2, 3)
    ROI = (-1.6, 8.2, 1.7, 4.4)
    base_gap = narrow_gap_area(bm, ROI)
    items = sorted({r['item'] for r in recs if cat_of.get(r['item']) in ('mag', 'grip')})
    for item in items:
        cat = cat_of[item]
        colors = sorted({r.get('color') for r in recs if r['item'] == item and r['slot'].startswith('main')}, key=str)
        for color in colors:
            rec = find(recs, item, 'main', color, brec['facing'])
            if not rec: continue
            rows += 1
            tag = f'{base_id}: {item}/{color}'
            if cat == 'mag':
                m = below(alpha(to_grid(with_adj(rec, adj.get('mag')), pts['mag'][0], pts['mag'][1], brec['facing'])), cuts['mag'])
                new = center_fit(m, np.arange(2.9, 3.9, 0.1), 2.0, 10.0)
                if not new or not fac_mag: errors.append(f'{tag}: a tár nem mérhető'); continue
                d_ang = np.degrees(np.arctan(new[0]) - np.arctan(fac_mag[0]))
                d_ctr = (new[0] * 3.4 + new[1]) - (fac_mag[0] * 3.4 + fac_mag[1])
                f_front = new[2][:, 3].mean() - fac_mag[2][:, 3].mean()
                f_rear = new[2][:, 2].mean() - fac_mag[2][:, 2].mean()
                total = (bm & ~magp) | m | fill_band(geo['fillpoly']['mag'], 0.5)
                msg = f'tengely {d_ang:+.1f} fok, középvonal {d_ctr:+.2f}", elöl {f_front:+.2f}", hátul {f_rear:+.2f}"'
                if abs(d_ang) > 3: errors.append(f'{tag}: a tár dőlése eltér a tárnyílás tengelyétől ({msg})')
                if abs(d_ctr) > 0.12: errors.append(f'{tag}: a tár nincs a tárnyílás közepén ({msg})')
                if f_front > 0.20: errors.append(f'{tag}: a tár elöl kilóg a tárnyílásból ({msg})')
                if f_rear < -0.30: errors.append(f'{tag}: a tár hátul kilóg a tárnyílásból ({msg})')
            else:
                m = below(alpha(to_grid(with_adj(rec, adj.get('grip')), pts['grip'][0], pts['grip'][1], brec['facing'])), cuts['grip'])
                new = center_fit(m, np.arange(2.6, 4.4, 0.2), -3, 4)
                top = center_fit(m, np.arange(2.2, 2.7, 0.1), -3, 4)
                if not new or not top or not fac_grip: errors.append(f'{tag}: a markolat nem mérhető'); continue
                d_ang = np.degrees(np.arctan(new[0]) - np.arctan(fac_grip[0]))
                d_top = (top[0] * 2.4 + top[1]) - (fac_grip_top[0] * 2.4 + fac_grip_top[1])
                total = (bm & ~gripp) | m | fill_band(geo['fillpoly']['grip'], 1.0)
                msg = f'dőlés {d_ang:+.1f} fok, középvonal a tok alatt {d_top:+.2f}"'
                if abs(d_ang) > 6: errors.append(f'{tag}: a markolat dőlése eltér a gyáritól ({msg})')
                if abs(d_top) > 0.18: errors.append(f'{tag}: a markolat nincs a gyári helyén a tok alatt ({msg})')
            gap = narrow_gap_area(total, ROI) - base_gap
            if gap > GAP_MAX: errors.append(f'{tag}: új keskeny rés a kész képen ({gap:.3f} négyzethüvelyk)')
            if VERBOSE: print(f'  {tag:34s} {msg}; rés +{gap:.3f}')

print(f'{rows} alkatrész-változat ellenőrizve ({len(BASES)} alapfegyver)')
for w in warns: print('FIGYELEM:', w)
for e in errors: print('HIBA:', e)
print('RENDBEN' if not errors else f'{len(errors)} HIBA')
sys.exit(1 if errors else 0)
