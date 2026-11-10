#!/usr/bin/env python3
"""
A kitöltő szín (fill) kimérése minden alapfotóhoz: a tok alsó része a markolat és a tárnyílás fölött.
Ezzel a színnel tölti ki a megjelenítő a gyári tár/markolat helyének tetejét, ha a csere-alkatrész keskenyebb
(lásd photo-render.js fadeBand). Minden kivitelhez (fekete, olive, earth, bronze…) külön szín kell.
Futtatás:  python3 tools/base_fill.py
"""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from align_lib import *

mpath = os.path.join(PH, 'manifest.json')
man = json.load(open(mpath, encoding='utf-8'))
for p in man['photos']:
    if p['slot'] != 'base': continue
    im = to_grid(p, 0, 0, p['facing'])
    a = np.asarray(im).astype(float)
    r0, r1, c0, c1 = gy(1.25), gy(1.75), gx(0.0), gx(2.0)
    reg = a[r0:r1, c0:c1]
    m = reg[:, :, 3] > 200
    rgb = reg[:, :, :3][m].mean(axis=0) * 0.95           # árnyékban: alig sötétebb
    p['fill'] = '#%02x%02x%02x' % tuple(int(round(v)) for v in rgb)
    print(p['item'], p.get('color'), p['fill'])

# a kitöltő-sáv alakja: a gyári tár / markolat tényleges sziluettje a vágási poligon tetején
# (a poligon maga szélesebb a tárnál, ott a kitöltés kilógna a tok mellett)
recs = man['photos']
for g in recs:
    if g['slot'] != 'geo': continue
    brec = [r for r in recs if r['item'] == g['item'] and r['slot'] == 'base'][0]
    bm = alpha(to_grid(brec, 0, 0, brec['facing']))
    g['fillpoly'] = {}
    for kind, depth, xlo, xhi in (('mag', 0.5, 2.0, 10.0), ('grip', 1.0, -3.0, 4.0)):
        m = bm & poly_mask(g['cuts'][kind])
        top = min(p[1] for p in g['cuts'][kind])
        left, right = [], []
        for y in np.arange(top + 0.02, top + depth, 0.05):
            e = edges_at(m, float(y), xlo, xhi)
            if e: left.append([round(float(e[0]), 2), round(float(y), 2)]); right.append([round(float(e[1]), 2), round(float(y), 2)])
        g['fillpoly'][kind] = [[left[0][0], round(top, 2)]] + left + right[::-1] + [[right[0][0], round(top, 2)]] if left else None
        print(g['item'], kind, 'kitöltő-sáv:', len(left), 'sor')
json.dump(man, open(mpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
