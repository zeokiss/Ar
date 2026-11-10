#!/usr/bin/env python3
"""
Fekete termékfotók tónusának igazítása az alapfotóhoz.

A Schmeisser hivatalos alapfotó feketéje sötét (medián fényesség ~40). A bolti és gyártói
termékfotók feketéi a fotózástól függően világosabbak (medián 50-90), ezért a kész képen
kilógnának. Ez az eszköz a SEMLEGES (fekete / sötétszürke) alkatrészfotók sötét tónusait a
cél-mediánhoz húzza, a csillanásokat és a fényes részeket meghagyja, a színes termékeket
(pl. FDE, OD, bronz, piros ACOG) nem bántja.

Idempotens: az érintett rekord `toned: true` jelölést kap, és kimarad a következő futásnál.
Futtatás:  python3 tools/tone_match.py [--target 46] [--dry]
"""
import argparse, glob, json, os
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, 'assets', 'photos')


def smooth(x, a, b):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--target', type=float, default=46.0)
    ap.add_argument('--dry', action='store_true')
    args = ap.parse_args()
    changed = 0
    for pf in sorted(glob.glob(os.path.join(PH, 'packs', '*.json'))):
        pack = json.load(open(pf)); dirty = False
        for rec in pack['photos']:
            if rec.get('toned') or rec.get('slot') == 'thumb' or rec.get('color') not in ('black', '*') or not rec.get('src'):   # a kártya-bélyegképek nem kerülnek a puskára
                continue
            path = os.path.join(PH, rec['src'])
            if not os.path.exists(path):
                continue
            a = np.array(Image.open(path).convert('RGBA')).astype(np.float32)
            m = a[:, :, 3] > 200
            if m.sum() < 500:
                continue
            rgb = a[:, :, :3]
            Y = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
            mean = rgb[m].mean(0)
            chroma = float(mean.max() - mean.min())
            med = float(np.median(Y[m]))
            if chroma > 14 or med <= args.target + 6:       # színes vagy már elég sötét
                continue
            k = float(np.clip(args.target / med, 0.58, 1.0))
            gain = (k + (1 - k) * smooth(Y, 95, 205))[..., None]
            a[:, :, :3] = np.clip(rgb * gain, 0, 255)
            print(f"{rec['item']:12s} {rec['slot']:11s} medián {med:5.1f} -> {med * k:5.1f}  (k={k:.2f}, chroma={chroma:.0f})")
            if not args.dry:
                Image.fromarray(a.astype(np.uint8)).save(path, 'WEBP', quality=95, method=6)
                rec['toned'] = True; dirty = True
            changed += 1
        if dirty and not args.dry:
            json.dump(pack, open(pf, 'w'), indent=1)
    print(f'{changed} kép igazítva' + (' (száraz futás)' if args.dry else ''))


if __name__ == '__main__':
    main()
