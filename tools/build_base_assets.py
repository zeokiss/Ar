#!/usr/bin/env python3
"""
Schmeisser alapfegyver-fotók letöltése és kalibrálása.

A képek a Schmeisser GmbH hivatalos oldaláról (schmeisser-germany.com) származnak,
személyes használatra. A szkript:
  1. letölti az átlátszó hátterű oldalnézeti képeket,
  2. a felső Picatinny sín osztásából (0,394" = 10 mm) kimeri a méretarányt (px/inch),
  3. kimeri a rögzítési pontokat (sín eleje/vége, előagy, cső tengelye, torkolat),
  4. kiírja az assets/photos/base/*.webp képeket és az assets/photos/manifest.json-t.

Futtatás:  python3 tools/build_base_assets.py
"""
import json, os, sys, urllib.request
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'assets', 'photos')
UP = 'https://schmeisser-germany.com/wp-content/uploads/2025/12/'
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36'}

# id, forrásfájl, irány (a cső merre néz a képen), kimeneti név
BASES = [
    ('schm-m4fl', '03-0074M_l2-scaled.webp', 'left',  'schm-m4fl-black.webp'),
    ('schm-m5fl', '03-0076M_l2-scaled.webp', 'left',  'schm-m5fl-black.webp'),
    ('schm-s4f',  '03-0099M_l-scaled.webp',  'left',  'schm-s4f-black.webp'),
    ('schm-dyn',  'AR15-223_Dynamic-1675.png', 'right', 'schm-dyn-black.webp'),
]
PITCH = 10 / 25.4  # Picatinny osztás, hüvelyk

# A közös alsó tok / tus / markolat / tár geometriája hüvelykben (M4FL képből mérve;
# az M4FL, M5FL, S4F és Dynamic ugyanazt az alsó részt használja).
# Koordináta: x előre, y lefelé, origó = a felső tok hátsó éle a cső tengelyén.
LOWER = {
    'stock': [-4.44, 0.0],
    'grip':  [0.70, 2.24],
    'mag':   [5.83, 0.98],
}
CUTS = {
    'mag':   [[4.19, 2.55], [7.69, 2.55], [7.69, 6.10], [4.19, 6.10]],
    'grip':  [[1.94, 3.18], [1.75, 1.93], [-0.30, 1.81], [-0.53, 2.81], [-1.23, 5.84], [-0.94, 6.40], [1.55, 6.40], [1.95, 5.97]],
    'stock': [[-4.37, -3.0], [-13.0, -3.0], [-13.0, 7.0], [-4.37, 7.0]],
}

def fetch(name):
    dst = os.path.join(OUT, '_src', name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst):
        req = urllib.request.Request(UP + name, headers=UA)
        with urllib.request.urlopen(req, timeout=60) as r, open(dst, 'wb') as f:
            f.write(r.read())
    return dst

def measure_ppi(top, F, x_lo, x_hi):
    seg = top[x_lo:x_hi].astype(float)
    best = []
    for x0 in range(0, len(seg) - 420, 60):
        s = seg[x0:x0 + 420]
        lo, hi = np.percentile(s, [5, 60]); s = np.clip(s, lo, hi); s = s - s.mean()
        if s.std() < 1: continue
        ac = np.correlate(s, s, 'full')[len(s) - 1:]; ac /= ac[0]
        for lag in range(10, 70):
            if ac[lag] > ac[lag - 1] and ac[lag] >= ac[lag + 1] and ac[lag] > 0.45:
                k = int(round(300 / lag)); c = range(int(lag * k * .96), int(lag * k * 1.04) + 1)
                best.append(max(c, key=lambda l: ac[l]) / k); break
    return float(np.median(best)) / PITCH

def detect(path, facing):
    im = Image.open(path).convert('RGBA'); a = np.array(im)[:, :, 3]; H, W = a.shape; m = a > 128
    cols = m.any(0); xs = np.where(cols)[0]; x0, x1 = int(xs.min()), int(xs.max())
    top = np.where(cols, m.argmax(0), -1); bot = np.where(cols, H - 1 - m[::-1].argmax(0), -1)
    F = -1 if facing == 'left' else 1
    front_end, rear_end = (x0, x1) if F == -1 else (x1, x0)
    ppi = measure_ppi(top, F, x0 + 100, x1 - 100)
    seq = [(i, front_end - F * i, bot[front_end - F * i] - top[front_end - F * i]) for i in range(600)]
    d = [s[2] for s in seq if 60 <= s[0] <= 600 and 35 < s[2] < 70]
    bd = int(np.bincount(d).argmax())
    barrel = [s for s in seq if bd - 1 <= s[2] <= bd + 1 and s[0] > 40]
    yAxis = float(np.median([(top[s[1]] + bot[s[1]]) / 2 for s in barrel[:60]]))
    muzzle_x = barrel[0][1]
    nose = next(front_end - F * i for i in range(1200) if bot[front_end - F * i] - top[front_end - F * i] > 100)
    L = int(np.bincount(np.array([top[nose - F * i] for i in range(120, 700)], dtype=int)).argmax())
    hgBottom = int(np.median([bot[nose - F * i] for i in range(150, 500)]))
    run = 0; rear_rail = None
    for i in range(1500):
        x = rear_end + F * i
        run = run + 1 if top[x] <= L + 8 else 0
        if run >= 12: rear_rail = x - F * 11; break
    run = 0; rail_front = None
    for i in range(400):
        x = nose - F * i
        run = run + 1 if top[x] <= L + 8 else 0
        if run >= 6: rail_front = x + F * 5; break
    return dict(bx0=x0, bx1=x1, W=W, H=H, F=F, ppi=ppi, yAxis=yAxis, muzzle=muzzle_x, nose=nose, railTop=L, hgBottom=hgBottom,
                rear=rear_rail, railFront=rail_front, tip=front_end)

def main():
    os.makedirs(os.path.join(OUT, 'base'), exist_ok=True)
    photos = []
    for item, src, facing, outname in BASES:
        p = fetch(src); r = detect(p, facing)
        im = Image.open(p).convert('RGBA')
        im.save(os.path.join(OUT, 'base', outname), 'WEBP', quality=92, method=6)
        F, O, pp = r['F'], r['rear'], r['ppi']
        inch = lambda x: round(F * (x - O) / pp, 3)
        photos.append(dict(item=item, slot='base', color='black', src='base/' + outname, w=r['W'], h=r['H'],
                           ppi=round(pp, 2), ax=O, ay=round(r['yAxis'], 1), facing=facing, bx0=r['bx0'], bx1=r['bx1'],
                           source='schmeisser-germany.com'))
        railY = round((r['railTop'] - r['yAxis']) / pp, 3)
        tip = inch(r['tip'])
        cuts = dict(CUTS)
        cuts['muzzle'] = [[inch(r['muzzle']) - 0.03, -1.0], [tip + 0.2, -1.0], [tip + 0.2, 1.0], [inch(r['muzzle']) - 0.03, 1.0]]
        photos.append(dict(item=item, slot='geo', color='*', pts=dict(
            railX0=0.0, railX1=inch(r['railFront']), railY=railY,
            hgX0=7.3, hgX1=inch(r['nose']), hgBottomY=round((r['hgBottom'] - r['yAxis']) / pp, 3), sideY=0.0,
            muzzle=[inch(r['muzzle']), 0.0], tipX=tip, **LOWER), cuts=cuts))
        print(item, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()})
    with open(os.path.join(OUT, 'manifest.json'), 'w') as f:
        json.dump({'version': 1, 'photos': photos}, f, indent=1)
    print('kész:', len(photos), 'rekord')

if __name__ == '__main__':
    sys.exit(main())
