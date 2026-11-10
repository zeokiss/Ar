#!/usr/bin/env python3
"""
A svájci boltok árainak összefésülése: assets/prices/<bolt>.json -> assets/prices/merged.json

Minden termékhez a legkedvezőbb RAKTÁRON LÉVŐ ajánlat kerül (ha egy sincs raktáron, a legolcsóbb).
A kimenet tartalmazza az összes ajánlatot is, a súlyt és a méretet, ha egy bolt közölte.
Kiírja az eltéréseket is a js/data.js súlyaihoz képest (20% fölött).

Futtatás:  python3 tools/merge_prices.py
"""
import glob, json, os, re, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PR = os.path.join(ROOT, 'assets', 'prices')


def data_weights():
    """A data.js tömegei (g) id szerint (egyszerű kinyerés a forrásból)."""
    src = open(os.path.join(ROOT, 'js', 'data.js'), encoding='utf-8').read()
    out = {}
    for m in re.finditer(r"id: '([^']+)'[^}]*?weight: (\d+)", src):
        out.setdefault(m.group(1), int(m.group(2)))
    return out


COLOR_ALIAS = {'black': 'black', 'schwarz': 'black', 'noir': 'black', 'nero': 'black',
               'fde': 'fde', 'fde / dark earth': 'fde', 'flat dark earth': 'fde',
               'coyote': 'coyote', 'odg': 'odg', 'od green': 'odg', 'olive drab green': 'odg'}


def color_of(it):
    """A bolt főárához tartozó szín, a data.js palettakulcsára normálva; ha nem egyértelmű: None."""
    return COLOR_ALIAS.get(str(it.get('color') or '').strip().lower())


def colors_of(it):
    """Színváltozatonkénti ár: {szín: {chf, url, in_stock, title}}. Régebbi fájlokban az érték csak szám."""
    out = {}
    for k, v in (it.get('colors_prices') or {}).items():
        if k == 'default':
            continue
        if isinstance(v, dict) and v.get('price_chf') is not None:
            out[k] = dict(chf=float(v['price_chf']), url=v.get('url'), in_stock=v.get('in_stock'), title=v.get('title'))
        elif isinstance(v, (int, float)):
            out[k] = dict(chf=float(v), url=None, in_stock=None, title=None)
    return out


def main():
    offers = {}
    shops = []
    for f in sorted(glob.glob(os.path.join(PR, '*.json'))):
        if f.endswith('merged.json'):
            continue
        d = json.load(open(f, encoding='utf-8'))
        shop = d.get('shop') or os.path.basename(f)[:-5]
        shops.append(shop)
        for iid, it in (d.get('items') or {}).items():
            if it.get('price_chf') is None:
                continue
            offers.setdefault(iid, []).append(dict(
                shop=shop, chf=float(it['price_chf']), list_chf=it.get('list_chf'), url=it.get('url'),
                image=it.get('image'), in_stock=it.get('in_stock'), title=it.get('title'), color=color_of(it),
                weight_g=it.get('weight_g'), dims=it.get('dims'), note=it.get('note'),
                colors=colors_of(it)))

    items = {}
    for iid, offs in offers.items():
        stock = [o for o in offs if o['in_stock'] is not False]
        best = min(stock or offs, key=lambda o: o['chf'])
        weights = [o['weight_g'] for o in offs if o.get('weight_g')]
        items[iid] = dict(
            chf=best['chf'], shop=best['shop'], url=best['url'], in_stock=best['in_stock'], title=best['title'],
            weight_g=round(sum(weights) / len(weights)) if weights else None,
            dims=next((o['dims'] for o in offs if o.get('dims')), None),
            offers=sorted(({k: o[k] for k in ('shop', 'chf', 'url', 'in_stock', 'colors', 'title', 'note', 'color') if o.get(k) is not None} for o in offs), key=lambda x: x['chf']))

    out = dict(currency='CHF', shops=shops, built=datetime.date.today().isoformat(), items=items)
    json.dump(out, open(os.path.join(PR, 'merged.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(items)} termékhez van bolti ár, boltok: {", ".join(shops)}')

    dw = data_weights()
    diffs = []
    for iid, it in items.items():
        w, d = it.get('weight_g'), dw.get(iid)
        if w and d and abs(w - d) / d > 0.20:
            diffs.append((iid, d, w))
    if diffs:
        print('\nTömeg-eltérések (> 20%):')
        for iid, d, w in sorted(diffs):
            print(f'  {iid:14s} data.js: {d:5d} g   bolt: {w:5d} g')


if __name__ == '__main__':
    main()
