#!/usr/bin/env python3
"""
A tárak és markolatok dőlésének/helyének illesztése a gyári alkatrész tengelyéhez.

Miért kell: a termékfotókon a tár és a markolat a saját függőlegesében áll, a puskán viszont a tárnyílás (kb. 12 fokkal
előre dől) és a markolat (kb. 17 fokkal hátra dől) tengelyébe kell állni. Ha a fotót nem forgatjuk el, az élek nem
párhuzamosak a tárnyílás falaival és a ravaszvédővel, vékony rések és kilógó élek keletkeznek.

Lépések:
  1. a gyári tár / markolat tengelye az alapfotó alfa-csatornájából (align_lib.factory_params),
  2. minden tár- és markolatfotónál: rot (dőlés) és dx (eltolás), hogy a felső szakasz középvonala a gyáriéval egybeessen,
     beírva a packs/*.json rekordokba,
  3. a többi alapfegyvernél (más render-szög, pl. Dynamic) a különbség a manifest.json geo-rekordjának `adj` mezőjébe kerül
     (adj.mag / adj.grip: {rot, dx}); a megjelenítő ezt adja a fotó saját értékéhez.
Futtatás:  python3 tools/fit_parts.py [--dry]
"""
import glob, json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from align_lib import *

DRY = '--dry' in sys.argv
ADJ_ONLY = '--adj-only' in sys.argv   # csak az alapfegyver-korrekciók újraszámolása
BASE_ID = 'schm-m4fl'
GRIPS = ('bcm-g3', 'bcm-g0', 'b5-t22', 'b5-t23', 'mp-moek2', 'mp-moek2p', 'mp-miad')
REF = {'mag': ('mp-pm30', 'black'), 'grip': ('bcm-g3', 'black')}

recs = load_records()
FAC = {b: factory_params(recs, b) for b in [r['item'] for r in recs if r['slot'] == 'geo']}


def measure(kind, rec, geo, rot, dx, facing):
    pt = geo['pts'][kind]
    m = alpha(to_grid(dict(rec, rot=rot, dx=dx), pt[0], pt[1], facing))
    if kind == 'mag':
        c = center_fit(m, MAG_YS, 2.0, 10.0)
        return None if c is None else (c[0], c[0] * MAG_CTR_Y + c[1])
    c, t = center_fit(m, GRIP_SLOPE_YS, -3, 4), center_fit(m, GRIP_TOP_YS, -3, 4)
    return None if (c is None or t is None) else (c[0], t[0] * GRIP_CTR_Y + t[1])


def fit(kind, rec, fac, dx0):
    """(rot, dx) úgy, hogy a tengely dőlése és a helye a gyáriéval egyezzen."""
    f = fac[kind]
    want_slope = f[0]
    want_ctr = (f[0] * MAG_CTR_Y + f[1]) if kind == 'mag' else (fac['grip_top'][0] * GRIP_CTR_Y + fac['grip_top'][1])
    best = None
    for rot in np.arange(-30, 30.1, 2.0):
        r = measure(kind, rec, fac['geo'], float(rot), dx0, fac['facing'])
        if r and (best is None or abs(r[0] - want_slope) < best[0]): best = (abs(r[0] - want_slope), float(rot))
    for rot in np.arange(best[1] - 2, best[1] + 2.01, 0.25):
        r = measure(kind, rec, fac['geo'], float(rot), dx0, fac['facing'])
        if r and abs(r[0] - want_slope) < best[0]: best = (abs(r[0] - want_slope), float(rot))
    rot = best[1]
    r = measure(kind, rec, fac['geo'], rot, dx0, fac['facing'])
    return round(rot, 2), round(dx0 + (want_ctr - r[1]), 3)


# ---- 1. alapérték: az M4FL gyári tengelyéhez (a rekordok eddigi dx-e nélkül, tisztán)
ref_vals = {}
for f in ([] if ADJ_ONLY else sorted(glob.glob(os.path.join(PH, 'packs', '*.json')))):
    d = json.load(open(f, encoding='utf-8'))
    dirty = False
    for p in d['photos']:
        kind = 'mag' if p['item'].startswith('mp-pm') and p['item'] != 'mp-pm' else ('grip' if p['item'] in GRIPS else None)
        if not kind or not p['slot'].startswith('main'): continue
        rot, dx = fit(kind, p, FAC[BASE_ID], 0.0)
        if (p['item'], p.get('color')) == REF[kind] and p['slot'].startswith('main'): ref_vals[kind] = (rot, dx)
        print(f"  {kind:4s} {p['item']:10s} {p['slot']:11s} {str(p.get('color')):8s} rot {p.get('rot')}→{rot:6.2f}  dx {p.get('dx')}→{dx:6.3f}")
        p['rot'], p['dx'] = rot, dx
        dirty = True
    if dirty and not DRY:
        json.dump(d, open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

# ---- 2. a többi alapfegyver korrekciója a referencia-alkatrészen mérve
if ADJ_ONLY:
    for kind, (item, color) in REF.items():
        r = find(recs, item, 'main', color)
        ref_vals[kind] = (r['rot'], r['dx'])
mpath = os.path.join(PH, 'manifest.json')
man = json.load(open(mpath, encoding='utf-8'))
for kind, (item, color) in REF.items():
    rec = find(recs, item, 'main', color)
    base_rot, base_dx = ref_vals[kind]
    for g in man['photos']:
        if g['slot'] != 'geo': continue
        rot, dx = fit(kind, rec, FAC[g['item']], 0.0)
        adj = g.setdefault('adj', {})
        adj[kind] = {'rot': round(rot - base_rot, 2), 'dx': round(dx - base_dx, 3)}
        print(f"  korrekció {g['item']:10s} {kind:4s} {adj[kind]}")
if not DRY:
    json.dump(man, open(mpath, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('kész' if not DRY else '(dry)')
