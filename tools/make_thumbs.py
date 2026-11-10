#!/usr/bin/env python3
"""
Kártya-bélyegképek készítése azokhoz a termékekhez, amelyekhez nincs kalibrált oldalnézeti fotó.

A bolti termékfotók (assets/prices/<bolt>.json `image`) legtöbbször ferde nézetűek és kicsik, a puskára rakáshoz
nem használhatók (ahhoz kalibrált, tiszta oldalnézet kell, lásd tools/PARTS_GUIDE.md), a kártyán viszont jól mutatnak.
Ezért 'thumb' típusú fotó-rekord kerül a katalógusba (slot = 'thumb'): csak a kártya használja, a megjelenítő nem.

Használat:  python3 tools/make_thumbs.py <képmappa> [--only id1,id2]
  A képmappában a fájlnév:  <termék-id>__<bolt>__<hash>.<kiterjesztés>  (a letöltő script adja).
A háttér eltávolítása: a széli pixelek színéből kiindulva (flood fill), a kép szélére vágva; sötét hátterű képet kihagy.
"""
import glob, json, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, 'assets', 'photos')
OUT_DIR = os.path.join(PH, 'thumbs')
PACK = os.path.join(PH, 'packs', 'thumbs.json')
MAXW = 460
args = [a for a in sys.argv[1:] if not a.startswith('--')]
only = None
if '--only' in sys.argv:
    only = set(sys.argv[sys.argv.index('--only') + 1].split(','))
    args = [a for a in args if a != sys.argv[sys.argv.index('--only') + 1]]
src_dir = args[0]


def cutout(im):
    """Fehér/világos háttér eltávolítása a széltől befelé; None, ha a háttér sötét vagy nem egységes."""
    im = im.convert('RGB')
    a = np.asarray(im).astype(int)
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    if border.mean() < 150 or border.std(axis=0).max() > 40: return None
    marker = (255, 0, 255)
    work = im.copy()
    w, h = work.size
    for xy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2)]:
        px = work.getpixel(xy)
        if px != marker and abs(px[0] - border.mean(axis=0)[0]) < 40:
            ImageDraw.floodfill(work, xy, marker, thresh=34)
    mask = (np.asarray(work) == np.array(marker)).all(axis=2)
    alpha = Image.fromarray(((~mask) * 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    # a hátteret a kiinduló szín szerinti peremmel ellensúlyozzuk: a szél kicsit beszűkítve
    out = im.convert('RGBA'); out.putalpha(alpha)
    bbox = alpha.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not bbox: return None
    pad = 6
    bbox = (max(bbox[0] - pad, 0), max(bbox[1] - pad, 0), min(bbox[2] + pad, w), min(bbox[3] + pad, h))
    return out.crop(bbox)


def score(path):
    base = os.path.basename(path)
    shop = base.split('__')[1]
    try: im = Image.open(path)
    except Exception: return -1
    s = im.size[0] * im.size[1]
    if 'shooting-store' in shop or 'heldef' in shop: s *= 0.15      # vízjeles (Shooting-Store felirat, Helvetia Defense embléma)
    return s


cands = {}
for f in sorted(glob.glob(os.path.join(src_dir, '*'))):
    iid = os.path.basename(f).split('__')[0]
    if only and iid not in only: continue
    cands.setdefault(iid, []).append(f)

os.makedirs(OUT_DIR, exist_ok=True)
pack = json.load(open(PACK, encoding='utf-8')) if os.path.exists(PACK) else {'version': 1, 'photos': []}
have = {p['item']: p for p in pack['photos']}
done, skipped = [], []
for iid, files in sorted(cands.items()):
    for f in sorted(files, key=score, reverse=True):
        try: im = cutout(Image.open(f))
        except Exception as e: im = None
        if im is None: continue
        if im.width > MAXW: im = im.resize((MAXW, int(im.height * MAXW / im.width)), Image.LANCZOS)
        fn = f'thumbs/{iid}.webp'
        im.save(os.path.join(PH, fn), 'WEBP', quality=90, method=6)
        shop = os.path.basename(f).split('__')[1].replace('-ch', '.ch').replace('-com', '.com')
        have[iid] = {'item': iid, 'slot': 'thumb', 'color': '*', 'src': fn, 'w': im.width, 'h': im.height, 'ppi': 1,
                     'ax': 0, 'ay': 0, 'facing': 'left', 'source': shop, 'credit': shop, 'note': 'Bolti termékfotó (kártya-bélyegkép); nem kalibrált oldalnézet.'}
        done.append(iid); break
    else:
        skipped.append(iid)
pack['photos'] = [have[k] for k in sorted(have)]
json.dump(pack, open(PACK, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f'{len(done)} bélyegkép kész; nem sikerült: {skipped}')
