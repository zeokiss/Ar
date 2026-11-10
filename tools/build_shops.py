#!/usr/bin/env python3
"""
A boltlista (assets/shops.json) összeállítása.

Források, ebben a sorrendben (a korábbi felülírja a későbbit):
  1. a meglévő assets/shops.json kézzel ellenőrzött bejegyzései (thoron, schiessstand, shooting-store, armamat),
  2. research/shopinfo/<slug>.json  – a bolt saját oldaláról (Impressum, Versand) gyűjtött adatok,
  3. research/shopinfo_extra.json   – a fizikai / bizonytalan boltok ellenőrzése,
  4. research/ch_shops.json         – az első felderítés (keresési találatok; nem ellenőrzött).

A bolt kulcsa a domain (www. nélkül); domain nélküli boltnál a név.
Ár csak az assets/prices/<slug>.json-ból jön (merge_prices.py), ez a script árat nem kezel.

Futtatás:  python3 tools/build_shops.py
"""
import glob, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, 'research')
OUT = os.path.join(ROOT, 'assets', 'shops.json')

KEEP = {'thoron.ch', 'schiessstand.ch', 'shooting-store.ch', 'armamat.ch'}   # kézzel ellenőrzött, nem írjuk felül
SKIP_NAMES = {'Action Sport (indoor lőtér)', 'Armurerie Gilles', "MAS Dayer Sàrl / 'Dayer Armes'"}  # nem bolt / nem megbízható forrás
BLOCKED = 'A bolt oldala botvédelmet használ, automatikusan nem kérdezhető le; az árakat a bolt oldalán kell megnézni.'


def unk(v):
    return v is None or (isinstance(v, str) and (v.strip() == '' or v.strip().lower().startswith('unknown')))


def clean(v):
    return None if unk(v) else v


def key_of(domain, name):
    d = clean(domain)
    if d:
        d = d.lower().strip()
        d = re.sub(r'^https?://', '', d).split('/')[0]
        d = re.sub(r'^www\.', '', d)
        return d
    return name


def tri(v):
    if v is True or v is False:
        return v
    return None


def from_shopinfo(s):
    out = dict(
        name=clean(s.get('name')), addr=clean(s.get('addr')), city=clean(s.get('city')), canton=clean(s.get('canton')),
        tel=clean(s.get('tel')), url=clean(s.get('url')), kind=clean(s.get('kind')), ships=tri(s.get('ships')),
        shipNote=clean(s.get('shipNote')), km=s.get('km') if isinstance(s.get('km'), (int, float)) else None,
        km_est=bool(s.get('km_est', True)), online_prices=tri(s.get('online_prices')),
        hours=clean(s.get('hours')), verified=s.get('verified', True), sources=s.get('sources') or [])
    if s.get('blocked'):
        out['noPriceReason'] = BLOCKED
    return out


def main():
    cur = json.load(open(OUT, encoding='utf-8'))
    shops = {k: v for k, v in cur['shops'].items() if k in KEEP}

    # 2. a bolt saját oldaláról
    for f in sorted(glob.glob(os.path.join(RES, 'shopinfo', '*.json'))):
        s = json.load(open(f, encoding='utf-8'))
        k = key_of(s.get('domain') or s.get('url'), s.get('name'))
        if k in shops:
            continue
        rec = from_shopinfo(s)
        pf = os.path.join(ROOT, 'assets', 'prices', os.path.basename(f))
        if os.path.exists(pf):
            p = json.load(open(pf, encoding='utf-8'))
            if p.get('blocked'):
                rec['noPriceReason'] = BLOCKED
        shops[k] = rec

    # 3. fizikai / bizonytalan boltok ellenőrzése
    ex = os.path.join(RES, 'shopinfo_extra.json')
    if os.path.exists(ex):
        for s in json.load(open(ex, encoding='utf-8')).get('shops', []):
            k = key_of(s.get('domain') or s.get('url'), s.get('name') or s.get('company'))
            if k in shops:
                continue
            rec = from_shopinfo(s)
            rec['name'] = clean(s.get('company')) or clean(s.get('name'))
            rec['display'] = clean(s.get('name'))
            shops[k] = rec

    # 4. első felderítés (csak ami még nincs benne)
    for s in json.load(open(os.path.join(RES, 'ch_shops.json'), encoding='utf-8'))['shops']:
        if s.get('name') in SKIP_NAMES:
            continue
        k = key_of(s.get('domain'), s.get('name'))
        if k in shops:
            continue
        shops[k] = dict(
            name=clean(s.get('company')) or s.get('name'), display=s.get('name'), addr=clean(s.get('address')),
            city=clean(s.get('city')), canton=clean(s.get('canton')), url=clean(s.get('url')),
            kind='store' if s.get('in_store') is True and s.get('online_prices') is False else None,
            ships=tri(s.get('ships')), online_prices=tri(s.get('online_prices')), km=None, km_est=True,
            verified=False, sources=[s.get('source_url')] if s.get('source_url') else [])

    # botvédett oldalak (a próba szerint): nincs automatikus ár
    for k in ('m426.ch', 'stampfli-waffen.ch', 'munitionsdepot.ch', 'vogtwaffen.ch'):
        if k in shops and not shops[k].get('noPriceReason'):
            shops[k]['noPriceReason'] = BLOCKED

    for v in shops.values():
        for f in [f for f, x in v.items() if x is None or x == []]:
            del v[f]
    cur['shops'] = shops
    json.dump(cur, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(shops)} bolt a listán')


if __name__ == '__main__':
    main()
