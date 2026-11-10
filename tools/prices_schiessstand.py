#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Valós árak és műszaki adatok gyűjtése a schiessstand.ch webshopból (CHF, ÁFÁ-val).

A bolt (shop.schiessstand.ch) WooCommerce/WordPress alapú, a nyilvános Store API-t
(/wp-json/wc/store/v1/products) használja a katalógus lekéréséhez. A teljes katalógus
~3100 termék, 100/oldal -> ~32 kérés (nagyságrenddel kevesebb, mint termékenként keresni),
ezt a szkript lemezre cache-eli. A találatokat a lap alján lévő SZABÁLYOK (RULES) alapján
választja ki: csak a PONTOSAN azonos termék kerül be, a hasonló-de-nem-azonos az
"unmatched" részbe, indoklással.

A termékoldalakat (egyezésenként 1 db) a súly/méret kiolvasásához tölti le.
Udvariasság: legfeljebb 1 kérés/másodperc, szekvenciális letöltés.

Használat (a projekt gyökeréből):
    python3 tools/prices_schiessstand.py                 # cache-t használ, ha van
    python3 tools/prices_schiessstand.py --refresh       # katalógus újratöltése
    python3 tools/prices_schiessstand.py --no-pages      # termékoldalak (súly/méret) nélkül
    python3 tools/prices_schiessstand.py --cache-dir DIR --out FILE --data-js FILE

Kimenet: assets/prices/schiessstand.json (lásd a make_output függvényt).
Csak Python 3 standard könyvtárat használ.
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.request

SHOP = 'https://shop.schiessstand.ch'
API = SHOP + '/wp-json/wc/store/v1/products'
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/120.0 Safari/537.36')
MIN_INTERVAL = 1.0  # másodperc két kérés között

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# ----------------------------------------------------------------------------- HTTP

_last_req = [0.0]


def http_get(url, retries=3):
    """Udvarias, szekvenciális GET (min. MIN_INTERVAL mp a kérések között)."""
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'de-CH,de;q=0.9'})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read(), dict(r.headers)
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            print('  újrapróbálás (%s): %s' % (e, url), file=sys.stderr)
            time.sleep(3 * (attempt + 1))


# ----------------------------------------------------------------------------- data.js

def parse_data_js(path):
    """Az ITEMS és BASES lista kiolvasása a data.js-ből (regex, nem kell hozzá node)."""
    src = open(path, encoding='utf-8').read()
    consts = {}
    for m in re.finditer(r"const\s+([A-Z0-9_]+)\s*=\s*(\[[^\]]*\])\s*;", src):
        try:
            consts[m.group(1)] = json.loads(m.group(2).replace("'", '"'))
        except ValueError:
            pass

    def colors_of(seg):
        m = re.search(r"colors:\s*(\[[^\]]*\]|[A-Z0-9_]+)", seg)
        if not m:
            return None
        v = m.group(1)
        if v.startswith('['):
            return json.loads(v.replace("'", '"'))
        return consts.get(v)

    items = []
    for line in src.splitlines():
        m = re.match(r"\s*\{\s*id:\s*'([^']+)',\s*cat:\s*'([^']+)',\s*brand:\s*'([^']*)',\s*name:\s*'((?:[^'\\]|\\.)*)'", line)
        if not m:
            continue
        w = re.search(r"weight:\s*(\d+)", line)
        items.append({'id': m.group(1), 'cat': m.group(2), 'brand': m.group(3),
                      'name': m.group(4).replace("\\'", "'"),
                      'weight': int(w.group(1)) if w else None,
                      'colors': colors_of(line), 'kind': 'item'})
    bases = []
    bm = re.search(r"const BASES = \[(.*?)\n  \];", src, re.S)
    if bm:
        for seg in re.split(r"\n\s*\{\s*\n", bm.group(1))[1:]:
            m = re.search(r"id:\s*'([^']+)',\s*brand:\s*'([^']*)',\s*name:\s*'([^']*)'", seg)
            if not m:
                continue
            w = re.search(r"weight:\s*(\d+)", seg)
            b = re.search(r"barrel:\s*([\d.]+)", seg)
            f = re.search(r"finishes:\s*(\[[^\]]*\])", seg)
            bases.append({'id': m.group(1), 'cat': 'base', 'brand': m.group(2), 'name': m.group(3),
                          'weight': int(w.group(1)) if w else None,
                          'barrel': float(b.group(1)) if b else None,
                          'colors': json.loads(f.group(1).replace("'", '"')) if f else None,
                          'kind': 'base'})
    return bases + items


# ----------------------------------------------------------------------------- katalógus

def fetch_catalog(cache_dir, refresh):
    os.makedirs(cache_dir, exist_ok=True)
    marker = os.path.join(cache_dir, 'catalog.json')
    if os.path.exists(marker) and not refresh:
        print('Katalógus a cache-ből: %s' % marker)
        return json.load(open(marker, encoding='utf-8'))
    products, page, pages = [], 1, 1
    while page <= pages:
        print('Katalógus %d/%s ...' % (page, pages if pages > 1 else '?'), flush=True)
        body, hdr = http_get('%s?per_page=100&page=%d' % (API, page))
        hdr = {k.lower(): v for k, v in hdr.items()}
        pages = int(hdr.get('x-wp-totalpages', '1'))
        products += json.loads(body.decode('utf-8'))
        page += 1
    json.dump(products, open(marker, 'w', encoding='utf-8'), ensure_ascii=False)
    return products


def norm(s):
    """Összehasonlításhoz: HTML-entitás -> szöveg, kisbetű, írásjelek szóközzé (a . és + marad)."""
    s = html.unescape(s or '').lower()
    s = re.sub(r"[^a-z0-9äöüéèàç+.]+", ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def price_of(p):
    pr = p['prices']
    div = 10 ** int(pr.get('currency_minor_unit', 2))
    price = round(int(pr['price']) / div, 2)
    reg = round(int(pr['regular_price']) / div, 2)
    return price, (reg if p.get('on_sale') and reg > price else None)


def stock_of(p):
    cls = p['stock_availability']['class']
    status = {'in-stock': 'in-stock', 'available-on-backorder': 'backorder'}.get(cls, 'out-of-stock')
    return status, html.unescape(p['stock_availability']['text'])


# ----------------------------------------------------------------------------- szín

# sorrend számít: a konkrétabb szín előbb
_COLOR_PATTERNS = [
    ('ranger', r'\branger\b|\brgr\b'),
    ('fde', r'\bfde\b|\bflat dark earth\b|\bdark earth\b'),
    ('odg', r'\bodg\b|\bod green\b|\bgreen\b|\bod\b|\boliv\w*\b'),
    ('gray', r'\bgr[ae]y\b|\bgrau\b|\bgry\b'),
    ('coyote', r'\bcoyote\b|\bmct\b'),
    ('tan', r'\btan\b'),
    ('bronze', r'\bbronze\b'),
    ('black', r'\bblack\b|\bschwarz\b|\bblk\b|\bbk\b'),
]
_COLOR_LABEL = {'black': 'Black', 'fde': 'FDE / Dark Earth', 'odg': 'OD Green', 'gray': 'Grey',
                'coyote': 'Coyote', 'tan': 'Tan', 'ranger': 'Ranger Green', 'bronze': 'Bronze',
                'olive': 'OD Green', 'earth': 'Dark Earth'}


def detect_color(title_norm, is_base=False):
    for key, pat in _COLOR_PATTERNS:
        if re.search(pat, title_norm):
            if is_base:  # data.js FINISHES kulcsok: black, olive, earth, bronze, fde
                return {'odg': 'olive', 'fde': 'earth'}.get(key, key)  # FDE ~ Dark Earth
            return key
    return None


# ----------------------------------------------------------------------------- szabályok
# Minden data.js id-hoz egy szabály: a márka (a bolti cím elején) + regex a NORMALIZÁLT címre
# (norm() kimenete: kisbetű, "-" és "/" szóköz). `excl` kizáró minták, `req` kötelező további minták.
# `near`: a hiányzó termékhez hasonló bolti termékek keresése (csak tájékoztató, az unmatched
# indoklásába kerül). `why`: kézi indoklás, ha a hasonló termék biztosan NEM azonos.
# `parts`: több bolti termékből álló készlet (az ár a részek összege).

RULES = {}


def R(pid, brand, pat, excl=None, req=(), near=None, why=None, note=None, parts=None):
    RULES[pid] = dict(brand=brand, pat=pat, excl=excl, req=req, near=near, why=why, note=note,
                      parts=parts)


# --- alapfegyverek (Schmeisser: csak félautomata, új, .223, a megadott csőhosszal; a CSS-tusos,
#     Special Edition, Falke-optikás és használt (Occ.) változatok MÁS termékek)
_BASE_EXCL = r'special|css stock|inkl|falke|occ\b|occasion|gebrauchs|9mm|s4fl|duty'
R('schm-s4f', 'schmeisser', r'\bhalbauto\w*\b.*\bs4f\b', _BASE_EXCL, req=(r'\b10\.5\b', r'223'),
  near=r'schmeisser.*s4f')
R('schm-m4fl', 'schmeisser', r'\bhalbauto\w*\b.*\bm4fl\b', _BASE_EXCL, req=(r'\b14\.5\b', r'223'),
  near=r'schmeisser.*m4fl')
R('schm-m5fl', 'schmeisser', r'\bhalbauto\w*\b.*\bm5fl\b', _BASE_EXCL, req=(r'\b16\.75\b', r'223'),
  near=r'schmeisser.*m5fl')
R('schm-dyn', 'schmeisser', r'\bdynamic m5fl\b|\bm5fl dynamic\b|\bdynamic\b.*\bm5fl\b', None,
  near=r'schmeisser.*halbauto.*dynamic',
  why='a bolt "AR15 Dynamic L" néven árul 16.75"-es Dynamic puskát; az "M5FL" jelöléssel való azonosság '
      'a bolti oldalon nincs kimondva -> bizonytalan egyezés')
R('dd-v7', 'daniel defense', r'\bddm4 v7\b|\bv7\b', None, near=r'daniel defense',
  why='nincs DDM4 V7 a boltban (csak más modellek)')
R('bcm-recce16', 'bcm', r'recce 16|\bmcmr\b.*halbauto|halbauto.*recce', None, near=r'^bcm',
  why='nincs BCM RECCE-16 puska a boltban')

# --- markolatok
R('mp-moe', 'magpul', r'\bmoe grip\b', r'k2|mvg|\bak\b|evo', near=r'magpul.*moe\+? grip',
  why='a sima MOE Grip nincs a boltban (csak a MOE+ / MOE-K2 / K2-XL változat, ezek másik termékek)')
R('mp-moek2', 'magpul', r'\bmoe k2 grip\b', r'\bak\b|xl|\+|evo')
R('mp-moek2p', 'magpul', r'\bmoe k2\+ grip\b', r'\bak\b|xl|evo')
R('mp-miad', 'magpul', r'\bmiad\b', near=r'magpul.*(miad|grip)')
R('bcm-g0', 'bcm', r'gunfighter grip.*mod ?0|gunfighter pistol grip.*mod ?0')
R('bcm-g3', 'bcm', r'gunfighter (pistol )?grip.*mod ?3', r'vertical', near=r'^bcm.*grip')
R('b5-t22', 'b5', r'type 22')
R('b5-t23', 'b5', r'type 23')

# --- tusok
R('mp-ctr', 'magpul', r'\bctr (carbine )?stock\b', r'cheek')
R('mp-moesl', 'magpul', r'\bmoe sl (carbine )?stock\b', r'sl k|sl m|sl s', near=r'magpul.*(carbine stock|moe sl)',
  why='MOE SL tus nincs a boltban (csak MOE Carbine Mil-Spec / ACS tus, ezek másik termékek)')
R('mp-moeslk', 'magpul', r'\bmoe sl k\b')
R('mp-ubr2', 'magpul', r'\bubr\b.*(gen ?2|2)\b|\bubr 2\b')
R('mp-prs3', 'magpul', r'\bprs gen ?3\b', r'lite')
R('mp-prslite', 'magpul', r'\bprs lite\b')
R('bcm-s0', 'bcm', r'gunfighter stock.*mod ?0')
R('bcm-s1', 'bcm', r'gunfighter stock.*mod ?1')
R('b5-bravo', 'b5', r'\bbravo stock\b')
R('b5-esop', 'b5', r'enhanced sopmod')
R('b5-prec', 'b5', r'precision stock')

# --- tustalp
R('mp-pad030', 'magpul', r'rubber butt pad 0\.30')
R('mp-pad055', 'magpul', r'extended rubber butt pad 0\.55')
R('mp-pad070', 'magpul', r'(?<!sl )enhanced rubber butt pad 0\.70')
R('mp-padsl', 'magpul', r'moe sl enhanced (rubber )?butt pad')

# --- red dot / holo
_NOBUNDLE = ('a bolt csak a céget/optikát árulja, a data.js "+ szerelék" csomagot (a megadott szerelékkel) '
             'nem; a szerelék nélküli optika ára nem azonos a csomag árával -> nem biztos egyezés')
R('ap-t2', 'aimpoint', r'micro t ?2\b.*\blrp\b', None, near=r'aimpoint.*micro t ?2', why=_NOBUNDLE)
R('ap-m5', 'aimpoint', r'comp ?m5\b', None, near=r'aimpoint.*comp',
  why='CompM5 nincs a boltban (csak Comp M4s, ami másik modell)')
R('ap-pro', 'aimpoint', r'\bpro 2 moa\b', r'patrol|micro',
  note='a bolti PRO kit tartalmazza a szerelést, a linsenschutz-ot és a spacert (QRP2 nincs név szerint említve)')
R('ap-acro', 'aimpoint', r'acro p ?2\b.*picatinny', None, near=r'aimpoint.*acro p ?2', why=_NOBUNDLE)
R('eo-exps3', 'eotech', r'\bexps3 0\b', r'green|dcr')
R('eo-exps2', 'eotech', r'\bexps2 0\b', r'green')
R('eo-xps2', 'eotech', r'\bxps2 0\b', r'green|xps2 300')
R('hs-510c', 'holosun', r'\bhs510c\b')
R('hs-aems', 'holosun', r'\baems rd\b', r'evo|pro|x2',
  note='piros (RD) reticle-változat; a bolt a zöld (GR), EVO és PRO-X2 típusokat külön árulja')
R('hs-403b', 'holosun', r'\bhs403b\b', note='a bolti termék tartalmaz Visierlinienerhöhung-ot (magasító); csak FDE színben kapható')
R('tj-mro', 'trijicon', r'\bmro\b', near=r'trijicon')
R('vx-uh1', 'vortex', r'\buh ?1\b', near=r'vortex.*(holo|uh)')
R('vx-sparc', 'vortex', r'\bsparc\b', near=r'vortex.*(red dot|strike fire|sparc)',
  why='SPARC AR nincs a boltban (csak Strike Fire II, ami másik modell)')
R('vx-crossfire', 'vortex', r'crossfire', near=r'vortex.*(red dot|strike fire)',
  why='Crossfire nincs a boltban (csak Strike Fire II, ami másik modell)')

# --- LPVO
R('vx-se16', 'vortex', r'strike eagle.*\b1 6\b', near=r'vortex.*strike eagle',
  why='csak Strike Eagle 5-25x56 van a boltban, 1-6x24 nincs')
R('vx-se18', 'vortex', r'strike eagle.*\b1 8\b', near=r'vortex.*strike eagle',
  why='csak Strike Eagle 5-25x56 van a boltban, 1-8x24 nincs')
R('vx-pst16', 'vortex', r'viper pst gen ?(ii|2).*\b1 6 24\b.*\bsfp\b', note='SFP, VMR-2 MOA szálkereszt')
R('vx-razor110', 'vortex', r'razor hd gen ?(iii|3).*\b1 10\b')
R('tj-acog', 'trijicon', r'\bta31\b', note='bolti változat: TA31 4x32, .223 BAC szálkereszt, dual illuminated, szereléssel (a szálkereszt-változat nem azonosítható a data.js-ből)')
R('tj-credo16', 'trijicon', r'credo.*\b1 6\b', near=r'trijicon')
R('tj-vcog', 'trijicon', r'vcog', near=r'trijicon')
R('eo-vudu16', 'eotech', r'vudu.*\b1 6 24\b', note='bolti változat: FFP, 7.62 SR2 szálkereszt (a data.js nem ad szálkeresztet)')
R('eo-vudu110', 'eotech', r'vudu.*\b1 10 28\b', near=r'eotech.*vudu')

# --- szerelék / nagyító
R('gs-sp30', 'geissele', r'super precision (scope )?mount.*30 ?mm|\bsp30\b', None,
  near=r'geissele.*(mount|sopmod)',
  why='a boltban csak a Vortex 1-6x-hez készült SOPMOD "Super Precision" szerelés van (Vortex-specifikus), '
      'az általános Super Precision 30 mm nincs -> nem azonos')
R('vx-cant30', 'vortex', r'sport cantilever mount 30 ?mm')
R('eo-g33', 'eotech', r'\bg33\b', note='a bolt csak TAN színben árulja, STS szereléssel')
R('eo-g43', 'eotech', r'\bg43\b')
R('ap-3x', 'aimpoint', r'3x ?mag ?1\b', None, near=r'aimpoint.*(3x|vergr)',
  why='a bolt "3X-P Mag-1" (Flip Mount + TwistMount) nagyítót árul, ami a 3XMag-1 utódja; nem biztosan azonos')
R('hs-hm3x', 'holosun', r'\bhm3x\b', r'micro')
R('vx-m3x', 'vortex', r'micro ?3x', near=r'vortex.*(magnif|3x)')

# --- BUIS
R('mp-mbus3', 'magpul', r'mbus 3\b', near=r'magpul.*mbus',
  why='MBUS 3 nincs a boltban (csak "MBUS Original" és "MBUS Pro Offset" külön első/hátsó irányzék)')
R('mp-mbuspro', 'magpul', r'mbus pro(?! offset)', near=r'magpul.*mbus')
R('mp-mbusoff', 'magpul', None, parts=[r'mbus pro offset sight front', r'mbus pro offset sight rear'],
  note='SZETT = Front + Rear irányzék, két külön bolti termék; az ár a két termék összege')

# --- lámpák
R('sf-m600', 'surefire', r'\bm600', near=r'surefire.*scout')
R('sf-m640', 'surefire', r'\bm640df\b', r'turbo|m640t')
R('sf-m300', 'surefire', r'\bm300\b', near=r'surefire.*(m3\d\d|mini)')
R('sf-x300', 'surefire', r'\bx300u b\b')
R('sl-hlx', 'streamlight', r'protac rail mount hl x(?! pro)',
  note='a bolti cím "ProTac Rail Mount HL-X"; létezik külön "HL-X Pro" is (más termék, nem ez)')
R('sl-tlr1', 'streamlight', r'\btlr 1 hl\b')

# --- M-LOK kiegészítők
R('mp-rvg', 'magpul', r'm lok rvg|rvg m lok (vertical )?grip', None, near=r'magpul.*(rvg|mvg)',
  why='a bolt az RVG-t Picatinny (M1913) változatban árulja + külön "RVG M-LOK Adapter Rail"-t; '
      'az M-LOK RVG kész termék nincs -> nem azonos')
R('mp-hstop', 'magpul', r'm lok hand stop kit')
R('bcm-vg3', 'bcm', r'gunfighter vertical grip', note='bolti SKU: BCM-VG-MCMR-MOD-3 (Mod 3, M-LOK)')
R('bcm-kag', 'bcm', r'gunfighter kag', note='bolti SKU: BCM-KAG-MCMR (M-LOK)')
R('mp-rail5', 'magpul', r'm lok alumin\w* rail section 5 slots')
R('mp-qd', 'magpul', r'm lok qd sling mount')
R('mp-cover', 'magpul', r'm lok rail cover type 2')
R('mp-cable', 'magpul', r'cable management', near=r'magpul.*(cable|clip|paraclip)')

# --- bipod
R('mp-bipod', 'magpul', r'bipod for m lok')
R('hr-sbrm', 'harris', r's ?brm', near=r'harris')

# --- torkolat
R('sf-warcomp', 'surefire', r'warcomp.*(5\.56|556)', None, near=r'surefire.*warcomp',
  why='a boltban csak WarComp 7.62 (5/8-24 menet) van, az 5.56 (1/2x28) nincs -> nem azonos')
R('sf-3p', 'surefire', r'sf3p', near=r'surefire.*(feuer|flash|komp)')
R('bcm-comp0', 'bcm', r'gunfighter compensator')
R('sf-rc2', 'surefire', r'rc2|socom ?556', near=r'surefire.*(socom|schalld)')

# --- elsütők
_TRG_WHY = ('a boltban Geissele elsütőből csak az SD-C (Super Dynamic Combat) van, a megadott típus nincs')
R('gs-ssae', 'geissele', r'\bssa e\b', near=r'geissele.*(sd c|trigger|abzug)', why=_TRG_WHY)
R('gs-ssa', 'geissele', r'\bssa\b(?! e)', near=r'geissele.*(sd c|trigger|abzug)', why=_TRG_WHY)
R('gs-sde', 'geissele', r'\bsd e\b', near=r'geissele.*(sd c|trigger|abzug)', why=_TRG_WHY)
R('gs-s3g', 'geissele', r'super 3 gun|\bs3g\b', near=r'geissele.*(sd c|trigger|abzug)', why=_TRG_WHY)

# --- felhúzókarok
R('rd-raptor', 'radian', r'raptor.*charging handle', r'\blt\b|\bsd\b')
R('rd-raptorlt', 'radian', r'raptor lt')
R('rd-raptorsd', 'radian', r'raptor sd')
R('bcm-ch4', 'bcm', r'gunfighter charging handle.*mod ?4')
R('bcm-ch3', 'bcm', r'gunfighter charging handle.*mod ?3')
R('gs-ach', 'geissele', r'airborne charging handle')

# --- kezelőszervek
R('rd-talon', 'radian', r'\btalon\b')
R('mp-bad', 'magpul', r'b\.a\.d\.? lever')
R('mp-trigguard', 'magpul', r'trigger guard', near=r'magpul.*(trigger|guard)')

# --- tárak
_PMAG_X = r'7\.62|308|\bak\b|lr sr|\bac\b|window|\bm2\b|10 30'
R('mp-pm10', 'magpul', r'\bpmag 10\b.*\bgen m3\b', _PMAG_X)
R('mp-pm20', 'magpul', r'\bpmag 20\b.*\bgen m3\b', _PMAG_X)
R('mp-pm30', 'magpul', r'\bpmag 30 (ar m4 )?gen m3\b|\bar15 pmag gen m3 30schuss\b', r'window|\bak\b|7\.62|10 30|\bm2\b')
R('mp-pm30w', 'magpul', r'pmag gen m3 30schuss window|pmag 30.*gen m3.*window', r'7\.62|308|\bak\b|lr sr',
  note='a bolti színek: black, coyote (MCT); FDE változat nincs')
R('mp-pm40', 'magpul', r'\bpmag 40\b.*\bgen m3\b', _PMAG_X)
R('mp-d60', 'magpul', r'd ?60', near=r'magpul.*(d 50|drum)',
  why='D-60 nincs a boltban (csak D-50 típusok más kaliberhez)')

# --- szíjak
R('mp-ms1', 'magpul', r'\bms1 sling\b', r'padded', note='csak a sima MS1 (a "Padded" egy másik termék)')
R('mp-ms4', 'magpul', r'\bms4 dual qd sling\b', note='bolti kivitel: GEN2')
R('bfg-vickers', 'blue force', r'vickers')


# ----------------------------------------------------------------------------- illesztés

def brand_ok(t, brand):
    return t.startswith(brand) or (' ' + brand + ' ') in (' ' + t[:40] + ' ')


def match_product(t, rule, pat):
    if not brand_ok(t, rule['brand']):
        return False
    if pat is None or not re.search(pat, t):
        return False
    if rule['excl'] and re.search(rule['excl'], t):
        return False
    return all(re.search(r, t) for r in rule['req'])


def pick(cands):
    """Több azonos (szín szerinti) találat esetén: készleten lévő, aztán olcsóbb, aztán kisebb id."""
    def key(c):
        st = {'in-stock': 0, 'backorder': 1, 'out-of-stock': 2}[stock_of(c['p'])[0]]
        return (st, price_of(c['p'])[0], c['p']['id'])
    cands = sorted(cands, key=key)
    return cands[0], cands[1:]


def variant_record(c):
    p = c['p']
    price, lst = price_of(p)
    status, text = stock_of(p)
    return {
        'title': html.unescape(p['name']),
        'price_chf': price,
        'list_chf': lst,
        'url': p['permalink'],
        'image': p['images'][0]['src'] if p.get('images') else None,
        'in_stock': status == 'in-stock',
        'stock_status': status,
        'availability': text,
        'sku': p.get('sku') or None,
    }


def near_text(catalog, near, limit=4):
    if not near:
        return ''
    pat = re.compile(near)
    hits = [p for p in catalog if pat.search(norm(p['name']))][:limit]
    if not hits:
        return ''
    out = '; '.join('%s (CHF %.2f) %s' % (html.unescape(p['name']), price_of(p)[0], p['permalink']) for p in hits)
    return ' | hasonló, de NEM azonos bolti termékek: ' + out


def match_all(entries, catalog):
    for p in catalog:
        p['_n'] = norm(p['name'])
    matched, unmatched, pending = {}, {}, {}
    for e in entries:
        rule = RULES.get(e['id'])
        if rule is None:
            unmatched[e['id']] = 'nincs illesztési szabály (új termék a data.js-ben?)'
            continue
        is_base = e['kind'] == 'base'
        if rule['parts']:
            recs = []
            for part in rule['parts']:
                cs = [{'p': p} for p in catalog if match_product(p['_n'], rule, part)]
                if not cs:
                    recs = None
                    break
                best, _ = pick(cs)
                recs.append(variant_record(best))
            if not recs:
                unmatched[e['id']] = 'nincs a boltban (a készlet egyik tagja hiányzik)' + near_text(catalog, rule['near'])
                continue
            price = round(sum(r['price_chf'] for r in recs), 2)
            lists = [r['list_chf'] if r['list_chf'] is not None else r['price_chf'] for r in recs]
            lst = round(sum(lists), 2)
            main = dict(recs[0])
            main.update({'title': ' + '.join(r['title'] for r in recs), 'price_chf': price,
                         'list_chf': lst if lst > price else None,
                         'in_stock': all(r['in_stock'] for r in recs),
                         'stock_status': 'in-stock' if all(r['in_stock'] for r in recs) else 'mixed',
                         'availability': ' / '.join(r['availability'] for r in recs),
                         'parts': recs, 'color': 'Black'})
            main['_pid'] = None
            matched[e['id']] = {'main': main, 'colors': {}, 'alts': [], 'note': rule['note'], 'entry': e}
            continue
        cands = [{'p': p, 'color': detect_color(p['_n'], is_base)} for p in catalog
                 if match_product(p['_n'], rule, rule['pat'])]
        if not cands:
            why = rule['why'] or 'nincs a boltban (a teljes katalógusban nincs pontosan azonos termék)'
            unmatched[e['id']] = why + near_text(catalog, rule['near'])
            continue
        groups = {}
        for c in cands:
            groups.setdefault(c['color'], []).append(c)
        chosen, alts = {}, []
        for col, cs in groups.items():
            best, rest = pick(cs)
            chosen[col] = best
            alts += [x['p']['permalink'] for x in rest]
        # fő változat: színtelen > black > a data.js szerinti első szín > legolcsóbb
        order = [None, 'black'] + list(e.get('colors') or [])
        main_col = next((c for c in order if c in chosen), 'N/A')
        if main_col == 'N/A':
            main_col = sorted(chosen, key=lambda k: price_of(chosen[k]['p'])[0])[0]
        main = variant_record(chosen[main_col])
        main['_pid'] = chosen[main_col]['p']['id']
        main['color'] = _COLOR_LABEL.get(main_col, main_col) if main_col else None
        cols = {}
        if any(k is not None for k in chosen):
            for k, c in sorted(chosen.items(), key=lambda kv: str(kv[0])):
                v = variant_record(c)
                v.pop('sku', None)
                cols[k if k is not None else 'default'] = v  # 'default' = a címben színt nem jelölő változat
        matched[e['id']] = {'main': main, 'colors': cols, 'alts': alts, 'note': rule['note'], 'entry': e,
                            'desc': chosen[main_col]['p'].get('description') or ''}
    return matched, unmatched


# ----------------------------------------------------------------------------- termékoldal

def to_g(val, unit):
    val = val.strip().replace("'", '')
    unit = unit.lower()
    if unit == 'kg':
        return round(float(val.replace(',', '.')) * 1000)
    if re.fullmatch(r'\d{1,3}\.\d{3}', val):  # 3.250 g = 3250 g (ezres elválasztó)
        return int(val.replace('.', ''))
    return round(float(val.replace(',', '.')))


def _weight(val):
    m = re.match(r"^(?:ca\.?\s*)?([\d.,']+)\s*(kg|g)\b", val.strip(), re.I)
    if not m:
        return None
    try:
        g = to_g(m.group(1), m.group(2))
    except ValueError:
        return None
    return g if 1 <= g <= 15000 else None  # épelmeződő bolti adat (pl. "230 kg" egy hevederre) kiszűrése


def parse_page(page_html):
    """Cím + súly/méret a WooCommerce "Zusätzliche Information" táblából (ha a bolt kitöltötte)."""
    out = {}
    for m in re.finditer(r'<tr class="woocommerce-product-attributes-item woocommerce-product-attributes-item--(\w+)">\s*'
                         r'<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>', page_html, re.S):
        out[m.group(1)] = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', m.group(3)))).strip()
    res = {}
    h = re.search(r'<h1[^>]*class="[^"]*product_title[^"]*"[^>]*>(.*?)</h1>', page_html, re.S)
    res['_title'] = re.sub(r'<[^>]+>', '', h.group(1)).strip() if h else None
    if out.get('weight'):
        w = _weight(out['weight'])
        if w:
            res['weight_g'] = w
    if out.get('dimensions'):
        res['dims'] = out['dimensions']
    return res


def parse_description(desc_html):
    """Tartalék: strukturált sorok a leírásból ("Gewicht:", "Länge:", nagybetűs GEWICHT/LÄNGE blokk,
    "Gewicht von 317g und Abmessungen von 95x58x73mm"). A "verpackt" (csomagolt) értékeket kihagyja."""
    txt = html.unescape(re.sub(r'<(br|/p|/li|/tr|/td|/div|/h\d)[^>]*>', '\n', desc_html or ''))
    txt = re.sub(r'<[^>]+>', ' ', txt)
    lines = [re.sub(r'[ \t\u00a0]+', ' ', ln).strip() for ln in txt.split('\n')]
    lines = [ln for ln in lines if ln]
    kv = {}
    for i, ln in enumerate(lines):
        m = re.match(r'^([A-Za-zÄÖÜäöüß ]{3,30}):\s*(.*)$', ln)
        if m:
            key, val = m.group(1).strip().lower(), m.group(2).strip()
            if not val and i + 1 < len(lines):
                val = lines[i + 1]
        elif ln in ('GEWICHT', 'LÄNGE') and i + 1 < len(lines):
            key, val = ln.lower(), lines[i + 1]
        else:
            continue
        if val and not val.endswith(':'):
            kv.setdefault(key, val)
    res = {}
    w = _weight(kv['gewicht']) if 'gewicht' in kv else None
    if w is None:
        m = re.search(r'Gewicht von (?:ca\.?\s*)?([\d.,]+\s*(?:kg|g)\b)', txt)
        w = _weight(m.group(1)) if m else None
    if w:
        res['weight_g'] = w
    parts = [kv[k] for k in ('länge', 'breite', 'höhe') if k in kv]
    if len(parts) == 3:
        res['dims'] = 'L %s x B %s x H %s' % tuple(parts)
    elif kv.get('abmessungen'):
        res['dims'] = kv['abmessungen'][:100]
    else:
        m = re.search(r'Abmessungen von ([\d.,]+(?:\s*[x×]\s*[\d.,]+)+\s*(?:mm|cm))', txt)
        if m:
            res['dims'] = m.group(1)
        elif 'länge' in kv:
            res['dims'] = 'Länge: ' + kv['länge'][:100]
    return res


def enrich(matched, use_pages, cache_dir):
    pdir = os.path.join(cache_dir, 'pages')
    os.makedirs(pdir, exist_ok=True)
    n = 0
    for pid, m in matched.items():
        main = m['main']
        extra = {}
        if use_pages and main.get('url') and not main.get('parts'):
            fn = os.path.join(pdir, re.sub(r'[^a-z0-9]+', '_', main['url'].lower())[-120:] + '.html')
            if os.path.exists(fn):
                page = open(fn, encoding='utf-8').read()
            else:
                n += 1
                print('Termékoldal %d: %s' % (n, main['url']), flush=True)
                try:
                    body, _ = http_get(main['url'])
                    page = body.decode('utf-8', 'replace')
                    open(fn, 'w', encoding='utf-8').write(page)
                except Exception as e:  # noqa: BLE001
                    print('  hiba: %s' % e, file=sys.stderr)
                    page = ''
            extra = parse_page(page)
            t = extra.pop('_title', None)
            if t and norm(t) != norm(main['title']):  # az oldal nem a várt termék -> nem használjuk
                print('  FIGYELEM: oldalcím eltér (%r != %r), oldaladat kihagyva' % (t, main['title']), file=sys.stderr)
                extra = {}
        if ('weight_g' not in extra or 'dims' not in extra) and m.get('desc'):
            for k, v in parse_description(m['desc']).items():
                extra.setdefault(k, v)
        main['weight_g'] = extra.get('weight_g')
        main['dims'] = extra.get('dims')


# ----------------------------------------------------------------------------- kimenet

def make_output(matched, unmatched, catalog_n, entries):
    items = {}
    for e in entries:
        m = matched.get(e['id'])
        if not m:
            continue
        main = m['main']
        rec = {
            'title': main['title'],
            'price_chf': main['price_chf'],
            'list_chf': main['list_chf'],
            'url': main['url'],
            'image': main['image'],
            'in_stock': main['in_stock'],
            'stock_status': main['stock_status'],
            'weight_g': main.get('weight_g'),
            'dims': main.get('dims'),
            'color': main.get('color'),
            'availability': main['availability'],
            'sku': main.get('sku'),
        }
        if m['colors']:
            rec['colors_prices'] = m['colors']
        if main.get('parts'):
            rec['parts'] = main['parts']
        if m['alts']:
            rec['alt_urls'] = m['alts']
        if m['note']:
            rec['note'] = m['note']
        items[e['id']] = rec
    return {
        'shop': 'schiessstand.ch',
        'currency': 'CHF',
        'fetched': datetime.date.today().isoformat(),
        'source': SHOP + ' (WooCommerce Store API, %d termék a katalógusban)' % catalog_n,
        'price_basis': 'kiírt fogyasztói ár, ÁFÁ-val (valueAddedTaxIncluded)',
        'items': items,
        'unmatched': {k: unmatched[k] for k in sorted(unmatched)},
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data-js', default=os.path.join(ROOT, 'js', 'data.js'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'assets', 'prices', 'schiessstand.json'))
    ap.add_argument('--cache-dir', default=os.path.join(tempfile.gettempdir(), 'prices_schiessstand_cache'))
    ap.add_argument('--refresh', action='store_true', help='a katalógus (és a termékoldalak) újratöltése')
    ap.add_argument('--no-pages', action='store_true', help='ne töltse le a termékoldalakat (súly/méret nélkül)')
    args = ap.parse_args()

    if args.refresh:
        for sub in ('catalog.json',):
            try:
                os.remove(os.path.join(args.cache_dir, sub))
            except OSError:
                pass
        pd = os.path.join(args.cache_dir, 'pages')
        if os.path.isdir(pd):
            for f in os.listdir(pd):
                os.remove(os.path.join(pd, f))

    entries = parse_data_js(args.data_js)
    print('data.js: %d termék/alapfegyver' % len(entries))
    catalog = fetch_catalog(args.cache_dir, False)
    print('Bolti katalógus: %d termék' % len(catalog))
    matched, unmatched = match_all(entries, catalog)
    enrich(matched, not args.no_pages, args.cache_dir)
    out = make_output(matched, unmatched, len(catalog), entries)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print('Kész: %d egyezés, %d unmatched -> %s' % (len(out['items']), len(out['unmatched']), args.out))


if __name__ == '__main__':
    main()
