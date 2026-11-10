#!/usr/bin/env python3
"""
A konfigurátor adatainak ellenőrzése (kiadás előtt futtasd).

Ellenőrzi:
  - minden fotó-rekord képe létezik, a horgony a képen belül van, az irány és a méretarány érvényes,
  - a rekord termék-azonosítója és színe létezik a js/data.js-ben,
  - az ár-összefésülés termékei léteznek, az árak pozitívak, a bolt szerepel a shops.json-ban,
  - a termék-azonosítók egyediek.
Kilépési kód: 0 = rendben, 1 = hiba van.
"""
import glob, json, os, re, sys
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, 'assets', 'photos')
errors, warns = [], []

src = open(os.path.join(ROOT, 'js', 'data.js'), encoding='utf-8').read()
ids = re.findall(r"\{ id: '([^']+)', cat: '", src)
bases = re.findall(r"\{\s*id: '([^']+)', brand: '[^']+', name: '[^']+', tag:", src)
if len(ids) != len(set(ids)):
    errors.append('data.js: ismétlődő termék-azonosító')
colors = {}
for m in re.finditer(r"\{ id: '([^']+)', cat: '[^']+'.*?\n", src):
    line = m.group(0)
    cm = re.search(r"colors: (\w+)", line)
    if cm: colors[m.group(1)] = cm.group(1)
PALETTES = {'MAGPUL': {'black', 'fde', 'odg', 'gray'}, 'BCM': {'black', 'fde', 'foliage', 'wolfgray'}, 'B5': {'black', 'fde', 'odg', 'wolfgray'}}
known = set(ids) | set(bases)

recs = []
for f in [os.path.join(PH, 'manifest.json')] + sorted(glob.glob(os.path.join(PH, 'packs', '*.json'))):
    for p in json.load(open(f, encoding='utf-8')).get('photos', []):
        recs.append((os.path.relpath(f, ROOT), p))

n_img = 0
for f, p in recs:
    tag = f"{f}: {p.get('item')}/{p.get('slot')}/{p.get('color')}"
    if p.get('item') not in known:
        errors.append(f'{tag}: ismeretlen termék-azonosító'); continue
    if p.get('slot') == 'geo':
        if not p.get('pts'): errors.append(f'{tag}: hiányzik a pts')
        continue
    path = os.path.join(PH, p.get('src', ''))
    if not p.get('src') or not os.path.exists(path):
        errors.append(f'{tag}: hiányzik a képfájl ({p.get("src")})'); continue
    n_img += 1
    im = Image.open(path)
    if im.size != (p['w'], p['h']): errors.append(f'{tag}: a rekord mérete ({p["w"]}x{p["h"]}) eltér a képtől {im.size}')
    if not (0 <= p['ax'] <= p['w'] and 0 <= p['ay'] <= p['h']): errors.append(f'{tag}: a horgony a képen kívül van')
    if not p.get('ppi') or p['ppi'] <= 0: errors.append(f'{tag}: érvénytelen ppi')
    if p.get('facing') not in ('left', 'right'): errors.append(f'{tag}: érvénytelen irány')
    if p.get('slot') != 'base':
        c = p.get('color')
        if c not in ('*', None) and p['item'] in colors and c not in PALETTES.get(colors[p['item']], {c}):
            warns.append(f'{tag}: a szín nincs az elem palettájában')
    if im.mode != 'RGBA': warns.append(f'{tag}: a kép nem átlátszó hátterű (RGBA)')

shops = json.load(open(os.path.join(ROOT, 'assets', 'shops.json'), encoding='utf-8'))['shops']
mp = os.path.join(ROOT, 'assets', 'prices', 'merged.json')
if os.path.exists(mp):
    m = json.load(open(mp, encoding='utf-8'))
    for iid, it in m['items'].items():
        if iid not in known: errors.append(f'merged.json: ismeretlen termék: {iid}')
        for o in it['offers']:
            if o['shop'] not in shops: errors.append(f'merged.json: {iid}: a bolt nincs a shops.json-ban: {o["shop"]}')
            if not (o['chf'] and o['chf'] > 0): errors.append(f'merged.json: {iid}: érvénytelen ár ({o["shop"]})')
            if not str(o.get('url', '')).startswith('https://'): errors.append(f'merged.json: {iid}: hiányzó/hibás URL ({o["shop"]})')

print(f'{len(ids)} termék, {len(bases)} alapfegyver, {len(recs)} fotó-rekord ({n_img} kép), {len(m["items"]) if os.path.exists(mp) else 0} termék bolti árral')
for w in warns: print('FIGYELEM:', w)
for e in errors: print('HIBA:', e)
print('RENDBEN' if not errors else f'{len(errors)} HIBA')
sys.exit(1 if errors else 0)
