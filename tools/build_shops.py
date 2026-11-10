#!/usr/bin/env python3
"""
A boltkatalógus (assets/shops.json) összeállítása: Svájc fegyverboltjai, online és fizikai egyaránt.

Források, ebben a sorrendben (a korábbi felülírja a későbbit):
  1. a meglévő assets/shops.json kézzel ellenőrzött bejegyzései (thoron, schiessstand, shooting-store, armamat),
  2. research/shopinfo/<slug>.json  – a bolt saját oldaláról (Impressum, Versand) gyűjtött adatok,
  3. research/shopinfo_extra.json   – a fizikai / bizonytalan boltok ellenőrzése, további boltok,
  4. research/ch_shops.json         – az első felderítés (keresési találatok; nem ellenőrzött).

A katalógus senki lakóhelyéhez nem kötött: nincs távolság, nincs környék szerinti szűrés.
Típus (kind): online = csak webshop, store = csak üzlet, both = webshop + üzlet, null = nem derült ki.
A bolt kulcsa a domain (www. nélkül); webcím nélküli boltnál a név.
Ár csak az assets/prices/<slug>.json-ból jön (merge_prices.py), ez a script árat nem kezel.

Futtatás:  python3 tools/build_shops.py
"""
import glob, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, 'research')
OUT = os.path.join(ROOT, 'assets', 'shops.json')

KEEP = {'thoron.ch', 'schiessstand.ch', 'shooting-store.ch', 'armamat.ch'}   # kézzel ellenőrzött, nem írjuk felül
BLOCKED = 'A bolt oldala botvédelmet használ, automatikusan nem kérdezhető le; az árakat a bolt oldalán kell megnézni.'
KINDS = {'online', 'store', 'both'}
CANTONS = {'AG', 'AI', 'AR', 'BE', 'BL', 'BS', 'FR', 'GE', 'GL', 'GR', 'JU', 'LU', 'NE', 'NW', 'OW', 'SG', 'SH',
           'SO', 'SZ', 'TG', 'TI', 'UR', 'VD', 'VS', 'ZG', 'ZH'}


def unk(v):
    return v is None or (isinstance(v, str) and (v.strip() == '' or v.strip().lower().startswith('unknown')))


def clean(v):
    if unk(v):
        return None
    if isinstance(v, str):
        v = re.sub(r'\s*\([^)]*\)', '', v).strip()   # kutatói megjegyzések ("(cégjegyzék)", "(nem megerősített)") ki
        return v or None
    return v


def key_of(domain, name):
    d = clean(domain)
    if d:
        d = re.sub(r'^https?://', '', d.lower().strip()).split()[0].split('/')[0]
        d = re.sub(r'^www\.', '', d)
        if '.' in d and ' ' not in d:
            return d
    return name


def tri(v):
    return v if v is True or v is False else None


def canton_of(v):
    v = clean(v)
    return v.upper() if v and v.upper() in CANTONS else None


def kind_of(v):
    v = clean(v)
    return v if v in KINDS else None


def https(v):
    v = clean(v)
    return v if v and v.startswith('https://') else None


def rec_of(s, verified_default=True):
    out = dict(
        name=clean(s.get('company')) or clean(s.get('name')), display=clean(s.get('name')),
        addr=clean(s.get('addr') or s.get('address')), city=clean(s.get('city')), canton=canton_of(s.get('canton')),
        tel=clean(s.get('tel')), hours=clean(s.get('hours')), url=https(s.get('url')), kind=kind_of(s.get('kind')),
        ships=tri(s.get('ships')), shipNote=clean(s.get('shipNote')), online_prices=tri(s.get('online_prices')),
        brands=[b for b in (s.get('brands') or []) if clean(b)][:12],
        verified=s.get('verified', verified_default), sources=[u for u in (s.get('sources') or []) if clean(u)])
    if out['display'] == out['name']:
        out['display'] = None
    if s.get('blocked'):
        out['noPriceReason'] = BLOCKED
    return out


def main():
    cur = json.load(open(OUT, encoding='utf-8'))
    shops = {k: v for k, v in cur['shops'].items() if k in KEEP}
    for v in shops.values():                       # "Steinen SZ" -> város + kanton
        m = re.match(r'^(.*\S)\s+([A-Z]{2})$', v.get('city', ''))
        if m and m.group(2) in CANTONS:
            v['city'], v['canton'] = m.group(1), m.group(2)
    shops['schiessstand.ch'].setdefault('canton', 'GL')

    # 2. a bolt saját oldaláról
    for f in sorted(glob.glob(os.path.join(RES, 'shopinfo', '*.json'))):
        s = json.load(open(f, encoding='utf-8'))
        k = key_of(s.get('domain') or s.get('url'), s.get('name'))
        if k in shops:
            continue
        rec = rec_of(s)
        pf = os.path.join(ROOT, 'assets', 'prices', os.path.basename(f))
        if os.path.exists(pf) and json.load(open(pf, encoding='utf-8')).get('blocked'):
            rec['noPriceReason'] = BLOCKED
        shops[k] = rec

    # 3. ellenőrzött / további boltok
    ex = os.path.join(RES, 'shopinfo_extra.json')
    if os.path.exists(ex):
        for s in json.load(open(ex, encoding='utf-8')).get('shops', []):
            k = key_of(s.get('domain') or s.get('url'), s.get('name') or s.get('company'))
            if k not in shops:
                shops[k] = rec_of(s, verified_default=False)

    # 4. első felderítés (ami még nincs benne)
    for s in json.load(open(os.path.join(RES, 'ch_shops.json'), encoding='utf-8'))['shops']:
        k = key_of(s.get('domain'), s.get('name'))
        if k in shops:
            continue
        r = rec_of(s, verified_default=False)
        if r['kind'] is None:
            r['kind'] = ('both' if s.get('in_store') is True else 'online') if s.get('online_prices') is True else \
                        ('store' if s.get('in_store') is True else None)
        r['verified'] = False
        r['sources'] = [s['source_url']] if clean(s.get('source_url')) else []
        shops[k] = r

    # botvédett oldalak (a próba szerint): nincs automatikus ár
    for k in ('m426.ch', 'stampfli-waffen.ch', 'munitionsdepot.ch', 'vogtwaffen.ch'):
        if k in shops and not shops[k].get('noPriceReason'):
            shops[k]['noPriceReason'] = BLOCKED

    for v in shops.values():
        for f in [f for f, x in v.items() if x is None or x == []]:
            del v[f]
    cur.pop('reference', None)
    cur['shops'] = dict(sorted(shops.items(), key=lambda kv: kv[0].lower()))
    json.dump(cur, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    kinds = {}
    for v in shops.values():
        kinds[v.get('kind', '?')] = kinds.get(v.get('kind', '?'), 0) + 1
    print(f'{len(shops)} bolt a katalógusban; típus szerint: {kinds}')


if __name__ == '__main__':
    main()
