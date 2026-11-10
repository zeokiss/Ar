#!/usr/bin/env python3
"""
Alkatrészfotó felvétele a konfigurátorba.

Egy termék oldalnézeti fotóját kivágja (átlátszó háttér), méretarányt rendel hozzá egy
ismert valós méretből, kiszámolja az illesztési pontot, és beírja a fotókatalógusba.

Példa:
  python3 tools/add_part.py --item hs-510c --src https://.../hs510c.jpg \\
      --facing right --width-in 3.9 --anchor contact --pack optics

Paraméterek:
  --item      a termék id-ja a js/data.js-ből (pl. hs-510c), alapfegyvernél nem ezt használd
  --slot      main (alap) | rear | front (BUIS) – alap: main
  --color     szín id (black, fde, odg, gray, ...) vagy * (minden szín) – alap: *
  --src       kép URL-je vagy helyi fájl
  --facing    merre néz a cső / az elöl lévő rész a képen: left | right
  --width-in  a kivágott termék valós szélessége (hosszúsága) hüvelykben  -> méretarány
  --height-in (alternatíva) a valós magasság hüvelykben
  --anchor    contact  : a sínre fekvő alap alja, középen (red dot, holo, ACOG, szerelék)
              axis     : a test hossztengelye a közepén (céltávcső szerelék nélkül)
              side     : a test közepe (fegyverlámpa, M-LOK elem oldalra)
              top      : a felső szél közepe (markolat teteje, tár teteje)
              front    : a legelső pont a tengelyen (tus eleje, torkolatfék hátulja a hátsó pont: 'rear')
              rear     : a leghátsó pont a tengelyen (torkolati eszköz hátsó síkja)
              X,Y      : kézi pixel-koordináta a kivágott képen
  --tol       háttér-eltávolítás tűrése (alap 22)
  --pack      a katalógus-fájl neve (assets/photos/packs/<pack>.json)
  --dry       csak előnézet-kép készítése, nem ír a katalógusba
"""
import argparse, io, json, os, sys, urllib.request
import numpy as np
from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, 'assets', 'photos')
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36'}


def load(src):
    if src.startswith('http'):
        req = urllib.request.Request(src, headers=UA)
        data = urllib.request.urlopen(req, timeout=60).read()
        return Image.open(io.BytesIO(data))
    return Image.open(src)


def cutout(im, tol):
    """Egységes (világos) háttér eltávolítása a széltől induló feltöltéssel."""
    im = im.convert('RGBA')
    a = np.array(im)
    if (a[:, :, 3] < 250).mean() > 0.05:      # már átlátszó hátterű
        return Image.fromarray(a)
    rgb = a[:, :, :3].astype(int)
    H, W = rgb.shape[:2]
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    dist = np.abs(rgb - bg).max(axis=2)
    cand = dist <= tol
    seen = np.zeros((H, W), bool)
    stack = [(y, x) for y in (0, H - 1) for x in range(W) if cand[y, x]] + [(y, x) for x in (0, W - 1) for y in range(H) if cand[y, x]]
    for y, x in stack: seen[y, x] = True
    while stack:
        y, x = stack.pop()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and not seen[ny, nx] and cand[ny, nx]:
                seen[ny, nx] = True; stack.append((ny, nx))
    alpha = np.where(seen, 0, 255).astype(np.uint8)
    # a szél finom simítása (1 px)
    al = Image.fromarray(alpha).filter(ImageFilter.GaussianBlur(0.8))
    a[:, :, 3] = np.array(al)
    return Image.fromarray(a)


def trim(im, pad=2):
    a = np.array(im)[:, :, 3]
    ys, xs = np.where(a > 24)
    box = (max(xs.min() - pad, 0), max(ys.min() - pad, 0), min(xs.max() + pad + 1, im.width), min(ys.max() + pad + 1, im.height))
    return im.crop(box)


def anchor_for(im, mode, facing):
    a = np.array(im)[:, :, 3] > 40
    H, W = a.shape
    ys, xs = np.where(a)
    if mode == 'contact':
        band = a[int(H * 0.93):, :]; bx = np.where(band.any(0))[0]
        return float((bx.min() + bx.max()) / 2), float(ys.max())
    if mode == 'top':
        band = a[:int(H * 0.06) + 1, :]; bx = np.where(band.any(0))[0]
        return float((bx.min() + bx.max()) / 2), float(ys.min())
    if mode == 'side':
        return float((xs.min() + xs.max()) / 2), float((ys.min() + ys.max()) / 2)
    if mode == 'axis':
        mid = a[:, int(W * 0.3):int(W * 0.7)]; yy = np.where(mid.any(1))[0]
        return float(W / 2), float(np.median([np.where(a[:, x])[0].mean() for x in range(int(W * 0.3), int(W * 0.7)) if a[:, x].any()]))
    if mode in ('front', 'rear'):
        want_right = (facing == 'right') == (mode == 'front')
        x = xs.max() if want_right else xs.min()
        col = np.where(a[:, max(0, x - 4):x + 1].any(1))[0]
        return float(x), float((col.min() + col.max()) / 2)
    x, y = mode.split(',')
    return float(x), float(y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--item', required=True); ap.add_argument('--slot', default='main'); ap.add_argument('--color', default='*')
    ap.add_argument('--src', required=True); ap.add_argument('--facing', choices=['left', 'right'], required=True)
    ap.add_argument('--width-in', type=float); ap.add_argument('--height-in', type=float)
    ap.add_argument('--anchor', default='contact'); ap.add_argument('--tol', type=int, default=22)
    ap.add_argument('--pack', default='parts'); ap.add_argument('--dry', action='store_true')
    ap.add_argument('--source', default=''); ap.add_argument('--max-px', type=int, default=1600)
    args = ap.parse_args()
    if not (args.width_in or args.height_in):
        sys.exit('Adj meg --width-in vagy --height-in értéket (a valós méret a méretarányhoz).')

    im = trim(cutout(load(args.src), args.tol))
    if max(im.size) > args.max_px:
        r = args.max_px / max(im.size); im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    ppi = im.width / args.width_in if args.width_in else im.height / args.height_in
    ax, ay = anchor_for(im, args.anchor, args.facing)
    name = f"{args.item}-{args.slot}-{'any' if args.color == '*' else args.color}.webp"
    os.makedirs(os.path.join(PH, 'parts'), exist_ok=True)
    im.save(os.path.join(PH, 'parts', name), 'WEBP', quality=92, method=6)

    # ellenőrző kép: a kivágott termék szürke alapon, az illesztési ponttal
    chk = Image.new('RGBA', im.size, (110, 116, 124, 255)); chk.alpha_composite(im)
    from PIL import ImageDraw
    d = ImageDraw.Draw(chk); d.ellipse([ax - 6, ay - 6, ax + 6, ay + 6], outline=(255, 60, 60, 255), width=2)
    d.line([ax - 14, ay, ax + 14, ay], fill=(255, 60, 60, 255), width=1); d.line([ax, ay - 14, ax, ay + 14], fill=(255, 60, 60, 255), width=1)
    os.makedirs(os.path.join(PH, '_check'), exist_ok=True)
    chk_path = os.path.join(PH, '_check', name.replace('.webp', '.png'))
    chk.convert('RGB').save(chk_path)
    print(f'kivágva: {im.size[0]}x{im.size[1]}px  ppi={ppi:.1f}  horgony=({ax:.0f},{ay:.0f})  ellenőrző kép: {chk_path}')
    if args.dry:
        os.remove(os.path.join(PH, 'parts', name)); return

    rec = dict(item=args.item, slot=args.slot, color=args.color, src='parts/' + name, w=im.width, h=im.height,
               ppi=round(ppi, 2), ax=round(ax, 1), ay=round(ay, 1), facing=args.facing)
    if args.source: rec['source'] = args.source
    pdir = os.path.join(PH, 'packs'); os.makedirs(pdir, exist_ok=True)
    pf = os.path.join(pdir, args.pack + '.json')
    pack = json.load(open(pf)) if os.path.exists(pf) else {'version': 1, 'photos': []}
    pack['photos'] = [p for p in pack['photos'] if not (p['item'] == rec['item'] and p['slot'] == rec['slot'] and p['color'] == rec['color'])] + [rec]
    json.dump(pack, open(pf, 'w'), indent=1)
    # a manifest hivatkozik a csomagra
    mf = os.path.join(PH, 'manifest.json'); m = json.load(open(mf))
    rel = 'packs/' + args.pack + '.json'
    if rel not in m.setdefault('packs', []): m['packs'].append(rel); json.dump(m, open(mf, 'w'), indent=1)
    print('felvéve:', rec['item'], rec['slot'], rec['color'])


if __name__ == '__main__':
    main()
