#!/usr/bin/env python3
"""A manifest.json `packs` listájának szinkronizálása az assets/photos/packs/*.json fájlokkal
(a böngésző ebből tölti be a katalógusokat; így nem kell a kódban felsorolni őket, és nincs 404).
Futtatás: python3 tools/sync_packs.py"""
import glob, json, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, 'assets', 'photos')
mp = os.path.join(PH, 'manifest.json')
m = json.load(open(mp, encoding='utf-8'))
m['packs'] = sorted('packs/' + os.path.basename(f) for f in glob.glob(os.path.join(PH, 'packs', '*.json')))
json.dump(m, open(mp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print('packs:', m['packs'])
