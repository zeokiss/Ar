#!/usr/bin/env python3
"""
Schmeisser színváltozatok előállítása a hivatalos FEKETE fotóból.

A gyártó csak a fekete kivitelről közöl nagyfelbontású oldalnézeti fotót. A valódi színes
kivitelek (bolti fotók alapján) úgy néznek ki, hogy CSAK a felső és az alsó tok, valamint az
előagy kap Cerakote-színt; a tus, a puffercső, a markolat, a tár, a cső, a torkolati eszköz,
az irányzékok és a kezelőszervek feketék maradnak. Ez a szkript pontosan ezt teszi: a hivatalos
fekete kép tok- és előagy-tartományát átszínezi úgy, hogy a megvilágítás (árnyékok, fények,
gravírozás) megmarad.

A cél-színek a svájci bolti fotókról mért, a tokra jellemző mediánok (ugyanabból a gyári
renderből): olive #4a4331, earth (Cerakote FDE) #5c492f, bronze #4b3d29.

Az eredmény NEM hivatalos fotó, ezért a rekord `derived: true` jelölést kap, és a felület jelzi.

Futtatás:  python3 tools/recolor_base.py   (előtte: tools/build_base_assets.py)
"""
import json, os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, 'assets', 'photos')

FINISHES = {            # id: (név, cél-szín: a tok mediánja a bolti fotón)
    'olive':  ('Olive',      (0x4a, 0x43, 0x31)),
    'earth':  ('Dark Earth', (0x5c, 0x49, 0x2f)),
    'bronze': ('Bronze',     (0x4b, 0x3d, 0x29)),
}
WHICH = {                # melyik alapfegyverhez melyik színek (data.js BASES.finishes)
    'schm-m4fl': ['olive', 'earth', 'bronze'],
    'schm-m5fl': ['olive', 'earth', 'bronze'],
    'schm-s4f':  ['olive', 'earth', 'bronze'],
    'schm-dyn':  ['bronze'],
}


def smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def regions(geo):
    p = geo['pts']
    rail, hg1, hgb = p['railY'], p['hgX1'], p['hgBottomY']
    upper = [(-0.30, rail - 0.03), (hg1, rail - 0.03), (hg1, hgb + 0.04), (-0.30, hgb + 0.04)]
    body = [(-0.30, 0.70), (7.55, 0.70), (7.55, 2.72), (1.70, 2.72), (1.70, 1.95), (-0.30, 1.95)]
    ring = [(1.70, 2.60), (4.15, 2.60), (4.15, 3.40), (1.70, 3.40)]     # a ravaszvédő gyűrűje (a nyílás átlátszó marad)
    mag = [(x, 2.72 if y < 3 else y) for x, y in geo['cuts']['mag']]
    return [upper, body, ring], [geo['cuts']['grip'], mag]


def to_px(poly, rec):
    F = -1 if rec['facing'] == 'left' else 1
    return [(rec['ax'] + F * x * rec['ppi'], rec['ay'] + y * rec['ppi']) for x, y in poly]


def recolor(rec, geo, color):
    im = Image.open(os.path.join(PH, rec['src'])).convert('RGBA')
    a = np.array(im).astype(np.float32)
    regs, holes = regions(geo)
    m = Image.new('L', im.size, 0); d = ImageDraw.Draw(m)
    for r in regs: d.polygon(to_px(r, rec), fill=255)
    for h in holes: d.polygon(to_px(h, rec), fill=0)
    m = np.array(m.filter(ImageFilter.GaussianBlur(1.4))).astype(np.float32) / 255.0
    rgb = a[:, :, :3]
    Y = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    sel = (m > 0.9) & (Y > 18) & (Y < 80) & (a[:, :, 3] > 250)
    Yref = float(np.median(Y[sel]))
    Ys = np.array(Image.fromarray(np.clip(Y, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.3))).astype(np.float32)
    s = np.clip(Ys / Yref, 0, 3)[..., None]
    tgt = np.array(color, np.float32)[None, None, :]
    # a valódi festett tok lapos megvilágítású: a fekete render erős kontrasztját visszavesszük
    k = np.where(s >= 1, 0.28, 0.60)
    f = np.clip(1 + k * (s - 1), 0.25, 2.0)
    out = tgt * f
    # sötét kis részek (kar, csap, rés belseje) maradjanak feketék; gravírozás / csillanás marad az eredeti
    keep_dark = smooth(Y / Yref, 0.50, 0.85)[..., None]
    keep_light = smooth(Y, 100, 150)[..., None]
    out = out * (1 - keep_light) + rgb * keep_light
    w = (m[..., None]) * keep_dark
    res = rgb * (1 - w) + out * w
    a[:, :, :3] = np.clip(res, 0, 255)
    return Image.fromarray(a.astype(np.uint8)), Yref


def main():
    mf = os.path.join(PH, 'manifest.json'); man = json.load(open(mf))
    recs = {(p['item'], p['slot'], p.get('color')): p for p in man['photos']}
    man['photos'] = [p for p in man['photos'] if not p.get('derived')]
    for item, fins in WHICH.items():
        base = recs[(item, 'base', 'black')]; geo = recs[(item, 'geo', '*')]
        for f in fins:
            name, col = FINISHES[f]
            img, yref = recolor(base, geo, col)
            out = f'base/{item}-{f}.webp'
            img.save(os.path.join(PH, out), 'WEBP', quality=92, method=6)
            r = dict(base); r.update(color=f, src=out, derived=True,
                                     note=f'A hivatalos fekete fotó átszínezése ({name}); a szín a bolti fotókról mért Cerakote-tokszín.')
            man['photos'].append(r)
            print(f'{item:10s} {f:7s} Yref={yref:5.1f} -> {out}')
    json.dump(man, open(mf, 'w'), indent=1)


if __name__ == '__main__':
    main()
