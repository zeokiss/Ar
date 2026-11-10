#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Árak és műszaki adatok gyűjtése a https://www.shooting-store.ch webshopból
az AR-15 konfigurátor (js/data.js) termékeihez.

Futtatás (a repó gyökeréből, csak Python 3 standard könyvtár kell):

    python3 tools/prices_shooting_store.py                 # teljes futás -> assets/prices/shooting-store.json
    python3 tools/prices_shooting_store.py --only mp-ctr,bcm-g0   # csak egyes id-k (kiírja, nem írja felül a JSON-t, ha --out nincs megadva)
    python3 tools/prices_shooting_store.py --cache /tmp/ss_cache  # letöltések gyorsítótárazása (újrafuttatáskor nem kér újra)
    python3 tools/prices_shooting_store.py --list-specs    # a kézi egyezési szabályok kilistázása

Működés:
  1. a data.js-ből kiolvassa az ITEMS/BASES elemeket (id, brand, name); a 'factory'/'none'
     elemeket kihagyja,
  2. mindegyikre a bolt keresőjén át keres
     (GET /shop/USER_ARTIKEL_HANDLING_AUFRUF.php?Suchstring=...&darstellen=1&lowlimit=..&highlimit=..),
  3. a találatokat a SPECS táblázat kézzel írt, szigorú szabályai (ok / no reguláris kifejezés
     a bolti terméknévre) szűrik: csak a PONTOSAN azonos terméket fogadja el, minden más
     (más modell, hossz, kivitel, csak tartozék) az "unmatched"-be kerül okkal,
  4. a termékoldalról (JSON-LD + a subartikel_content blokkok) kiolvassa az árat (CHF, ÁFÁ-val),
     akciós árat és listaárat, készletet, képet, színváltozatokat, súlyt, méretet.

Udvarias a bolttal: max. ~1 kérés/másodperc, szekvenciális letöltés, böngésző User-Agent.
Csak a www.shooting-store.ch hosttal kommunikál.
"""
import argparse
import base64
import datetime
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = 'https://www.shooting-store.ch'
SEARCH_URL = BASE + '/shop/USER_ARTIKEL_HANDLING_AUFRUF.php'
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0 Safari/537.36')
MIN_INTERVAL = 1.0          # másodperc két kérés között
PAGE = 60                   # találat / oldal (a keresőűrlap highlimit értéke)
MAX_PAGES = 3

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DATA_JS = os.path.join(ROOT, 'js', 'data.js')
OUT_JSON = os.path.join(ROOT, 'assets', 'prices', 'shooting-store.json')

# ---------------------------------------------------------------------------
# Kézi egyezési szabályok. Kulcs: data.js id.
#   q     : keresőszavak (több is lehet)
#   ok    : reguláris kif. a normalizált bolti terméknévre (kisbetű, csak a-z0-9 és szóköz,
#           a '+' jel "plus"-ra cserélve) -- a névnek meg kell felelnie
#   no    : kizáró reguláris kif. (más kivitel / modell)
#   colors: True -> több bolti termékoldal is lehet ugyanannak a terméknek a SZÍNVÁLTOZATA
#   multi : True -> több bolti oldal ugyanazon termék egyéb (nem szín) változata (pl. reticle);
#           az első (ár szerint a legolcsóbb nem, hanem a listában első) az elsődleges
#   set   : [ {ok, no, q}, ...] -> a bolt a készletet alkatrészenként (pl. első+hátsó) árulja,
#           az ár az alkatrészek összege
#   note  : megjegyzés a kimenetbe
#   skip  : 'ok' -> nem keresünk, biztosan nincs a boltban (magyarázat), de a keresés ettől még
#           lefuthat a --recheck kapcsolóval
# ---------------------------------------------------------------------------
SPECS = {}


def S(id_, q, ok=None, no=None, **kw):
    d = {'q': q if isinstance(q, list) else [q], 'ok': ok, 'no': no}
    d.update(kw)
    SPECS[id_] = d


# --- Alapfegyverek ---------------------------------------------------------
S('schm-s4f', ['Schmeisser S4F'], r'^selbstladebuchse schmeisser ar15 s4f (black|od green|bronze) 10 5 223 rem$',
  r'css|solid|special|\b9\b|9mm|duty', colors=True,
  note='Standard (Selbstladebüchse, M-LOK, 10.5", .223 Rem); a Solid Line / Special Edition / CSS-tusos kivitelek más termékek')
S('schm-m4fl', ['Schmeisser M4FL'], r'^selbstladebuchse schmeisser ar15 m4fl (black|fde|od green|bronze) 14 5 223 rem$',
  r'css|solid|special|\b9\b|9mm|duty', colors=True,
  note='Standard (14.5", .223 Rem)')
S('schm-m5fl', ['Schmeisser M5FL'], r'^selbstladebuchse schmeisser ar15 m5fl black 16 75 223 rem$',
  r'css|solid|special|\b9\b|9mm|duty|straight|sp15', colors=True,
  note='Standard (16.75", .223 Rem); a boltban csak fekete')
S('schm-dyn', ['Schmeisser Dynamic'], r'^selbstladebuchse schmeisser ar15 dynamic l (black|grey) 16 75 ',
  r'9mm|pcc', colors=True,
  note='A boltban "AR15 Dynamic L" néven fut (16.75"); a "Dynamic M5FL" névvel való azonosság a bolti oldal alapján valószínű, nem gyári megerősítésű')
S('dd-v7', ['Daniel Defense DDM4 V7'], r'^daniel defense ddm4 v7 16 5 56 nato brown milspec$',
  note='A boltban csak a Brown (Mil-Spec) 16" változat; a data.js fekete')
S('bcm-recce16', ['RECCE-16 MCMR'], r'^recce 16 mcmr15$', r'precision|enhanced|light|300')

# --- Markolatok ------------------------------------------------------------
S('mp-moe', ['Magpul MOE Grip'], r'^magpul moe grip$')
S('mp-moek2', ['Magpul MOE K2'], r'^magpul moe k2 grip$')
S('mp-moek2p', ['Magpul MOE K2'], r'^magpul moe k2 plus grip$')
S('mp-miad', ['Magpul MIAD'], r'^magpul miad 5 56 grip gen 1 1 type 1$',
  note='AR-15 (5.56) változat; a 7.62 külön termék')
S('bcm-g0', ['BCM Grip Mod 0'], r'^bcm grip mod 0 (black|flat dark earth)$', colors=True,
  note='A boltban "BCM Grip Mod 0" (a BCM Gunfighter sorozat)')
S('bcm-g3', ['BCM Grip Mod 3'], r'^bcm grip mod 3 (black|flat dark earth)$', colors=True,
  note='A boltban "BCM Grip Mod 3" (a BCM Gunfighter sorozat)')

# --- Tusok -----------------------------------------------------------------
S('mp-ctr', ['Magpul CTR Carbine Stock'], r'^magpul ctr carbine stock mil spec$', note='Mil-Spec tuskivitel')
S('mp-moesl', ['Magpul MOE SL Carbine Stock'], r'^magpul moe sl carbine stock mil spec$', note='Mil-Spec tuskivitel')
S('mp-moeslk', ['Magpul MOE SL-K Carbine Stock'], r'^magpul moe sl k carbine stock mil spec$', note='Mil-Spec tuskivitel')
S('mp-ubr2', ['Magpul UBR Gen 2'], r'^magpul ubr gen 2 0 collapsible stock$')
S('mp-prs3', ['Magpul PRS Gen 3'], r'^magpul prs gen 3 rifle stock$')
S('mp-prslite', ['Magpul PRS Lite'], r'^magpul prs lite$')
S('bcm-s0', ['BCM Stock'], r'^bcm stock mod 0 (black|flat dark earth)$', r'sopmod|kit', colors=True)
S('bcm-s1', ['BCM Stock'], r'^bcm stock mod 1 sopmod compartment (black|flat dark earth)$', colors=True)
S('b5-bravo', ['B5 Systems Bravo'], r'^b5 systems ar 15 bravo stock collapsible mil spec', note='Mil-Spec kivitel')

# --- Tustalpak -------------------------------------------------------------
S('mp-pad030', ['Magpul Buttpad'], r'^magpul rubber buttpad black$')
S('mp-pad055', ['Magpul Buttpad'], r'^magpul extended rubber buttpad black$')
S('mp-pad070', ['Magpul Buttpad'], r'^magpul enhanced rubber buttpad black$')
S('mp-padsl', ['Magpul Buttpad'], r'^magpul moe sl enhanced rubber buttpad 0 70 inches black$')

# --- Red dot / holo --------------------------------------------------------
S('eo-exps3', ['EoTech EXPS3'], r'^eotech exps3 0 (black|tan)$', colors=True)
S('eo-exps2', ['EoTech EXPS2'], r'^eotech exps2 0 black$', r'green|schwarz',
  note='Piros reticle (a "Green Reticle" változat külön termék)')
S('eo-xps2', ['EoTech XPS2'], r'^eotech xps2 0 black$', r'green')
S('hs-510c', ['Holosun HS510C'], r'^holosun hs510c (solar red circle dot sight black|fde)', r'set|ge|he510', colors=True)
S('vx-uh1', ['Vortex UH-1'], r'^vortex optics amg uh 1 gen ii holographic sight$')
S('vx-crossfire', ['Vortex Crossfire Red Dot'], r'^vortex optics crossfire red dot led upgrade black$',
  note='A boltban "Crossfire Red Dot LED Upgrade"; a magasító szerelék nincs külön feltüntetve')

# --- LPVO ------------------------------------------------------------------
S('vx-se16', ['Vortex Strike Eagle 1-6x24'], r'^vortex optics strike eagle 1 6x24 ar bdc3$')
S('vx-se18', ['Vortex Strike Eagle 1-8x24'], r'^vortex optics strike eagle 1 8x24 ar bdc3$', r'ffp|ebr',
  note='SFP, AR-BDC3 reticle')
S('vx-pst16', ['Vortex Viper PST Gen II 1-6x24'], r'^vortex optics viper pst gen ii 1 6x24 sfp vmr 2 (moa|mrad)$', multi=True,
  note='Két reticle-változat (MOA / MRAD), a fizikai termék azonos; price_chf = az első (MOA) változat')
S('vx-razor110', ['Vortex Razor Gen III'], r'^vortex optics razor gen iii 1 10x24 ebr 9 mrad$')
S('eo-vudu16', ['EoTech Vudu'], r'^eotech vudu 1 6x24 ffp sr1 reticle mrad black$', note='FFP, SR1 MRAD reticle (az egyetlen 1-6x24 a boltban)')
S('eo-vudu110', ['EoTech Vudu'], r'^eotech vudu 1 10x28 ffp (le5|sr5) reticle mrad black$', multi=True,
  note='Két reticle-változat (LE5 / SR5), azonos ár')

# --- Szerelék / nagyító ----------------------------------------------------
S('gs-sp30', ['Super Precision 30mm'], r'^super precision 30mm 1 93 (black|ddc)$', colors=True,
  note='Geissele Super Precision 30 mm, 1.93" magasság')
S('eo-g33', ['EoTech G33'], r'^eotech g33 sts magnifier (black|tan)$', colors=True)
S('eo-g43', ['EoTech G43'], r'^eotech vergrosserungsaufsatz g43 sts 3x magnifier', 
  note='G43.STS (STS szerelékkel); a data.js "Micro szerelék" megjelölése eltér')
S('hs-hm3x', ['Holosun HM3X'], r'^holosun magnifier hm3x magnifier$')
S('vx-m3x', ['Vortex Magnifier'], r'^vortex optics v3xm magnifier with flip mount black$',
  note='V3XM = Vortex Micro 3X magnifier (flip mount-tal)')

# --- BUIS (a bolt első/hátsó irányzékot külön árul -> összeg) --------------
S('mp-mbus3', ['Magpul MBUS'], None, set=[
    {'ok': r'^magpul mbus 3 front back up sight$'}, {'ok': r'^magpul mbus 3 rear back up sight$'}],
  note='A bolt az első és a hátsó irányzékot külön árulja; az ár az alkatrészek összege')
S('mp-mbuspro', ['Magpul MBUS Pro'], None, set=[
    {'ok': r'^magpul mbus pro sight front black$'}, {'ok': r'^magpul mbus pro sight rear black$'}],
  note='A bolt az első és a hátsó irányzékot külön árulja; az ár az alkatrészek összege')
S('mp-mbusoff', ['Magpul MBUS Pro Offset'], None, set=[
    {'ok': r'^magpul mbus pro offset sight front black$'}, {'ok': r'^magpul mbus pro offset sight rear black$'}],
  note='A bolt az első és a hátsó irányzékot külön árulja; az ár az alkatrészek összege')

# --- Lámpák ----------------------------------------------------------------
S('sl-hlx', ['Streamlight ProTac Rail Mount'], r'^streamlight protac rail mount hl x black$')
S('sl-tlr1', ['Streamlight TLR-1'], r'^streamlight tlr 1 hl black$')

# --- M-LOK -----------------------------------------------------------------
S('bcm-vg3', ['BCM Vertical Grip'], r'^bcm vertical grip m lok compatible mod 3', note='M-LOK kompatibilis Mod 3 (BCM Gunfighter)')
S('bcm-kag', ['BCM Kinesthetic Angled Grip'], r'^bcm kinesthetic angled grip m lok compatible$', note='M-LOK kompatibilis KAG (BCM Gunfighter)')
S('mp-hstop', ['Magpul Hand Stop Kit'], r'^magpul m lok hand stop kit$')
S('mp-rail5', ['Magpul M-LOK Rail Section'], r'^magpul m lok rail section aluminium 5 slots black$')
S('mp-qd', ['Magpul M-LOK QD Sling Mount'], r'^magpul m lok qd sling mount black$')
S('mp-cover', ['Magpul M-LOK Rail Cover'], r'^magpul m lok rail cover type 2$')

# --- Bipod, torkolat, elsütő, felhúzókar, kezelőszervek -------------------
S('mp-bipod', ['Magpul Bipod'], r'^magpul bipod for m lok$')
S('bcm-comp0', ['BCM Compensator'], r'^bcm compensator mod 0 5 56$')
S('gs-ssae', ['Geissele SSA-E', 'Super Semi-Automatic'], r'^super semi automatic enhanced ssa e$', r'large pin')
S('gs-ssa', ['Geissele SSA', 'Super Semi-Automatic'], r'^super semi automatic ssa$', r'large pin')
S('gs-sde', ['Geissele SD-E', 'Super Dynamic Enhanced'], r'^super dynamic enhanced sd e$', r'large pin')
S('gs-s3g', ['Geissele Super 3 Gun'], r'^geissele automatics ar15 super 3 gun s3g trigger$')
S('rd-raptor', ['Radian Raptor'], r'^(radian weapons raptor ambidextrous charging handle ar15 bl|radian raptor ambidextrous charging handle ar15 dark earth|raptor charging handle ar15 radian (grey|od green))', r'sd|lt|sl|combo|competition|ar10',
  colors=True)
S('rd-raptorlt', ['Radian Raptor-LT'], r'^radian raptor lt ambidextrous charging handle ar15', r'ar10|combo', colors=True)
S('rd-raptorsd', ['Radian Raptor-SD'], r'^(radian weapons ar 15 raptor sd ambidextrous charging handl|radian raptor sd ambidextrous charging handle ar15|raptor sd charging handle ar15)', r'ar10|combo', colors=True)
S('rd-talon', ['Radian Talon'], r'^radian weapons talon ambidextrous 45 90 safety selector', r'kit|gi\b', colors=True)
S('mp-bad', ['Magpul B.A.D.'], r'^magpul b a d lever black$')
S('mp-trigguard', ['Magpul MOE Enhanced Trigger Guard'], r'^magpul moe enhanced trigger guard polymer ar15 m4$')

# --- Tárak -----------------------------------------------------------------
S('mp-pm10', ['Magpul PMAG 10 Gen M3'], r'^magpul pmag 10 gen m3 (black|odg)$', colors=True)
S('mp-pm20', ['Magpul PMAG 20 Gen M3'], r'^magpul pmag 20 gen m3 (black|odg)$', colors=True)
S('mp-pm30', ['Magpul PMAG 30 Gen M3'], r'^magpul pmag 30 gen m3$')
S('mp-pm40', ['Magpul PMAG 40'], r'^magpul ar 15 40rd pmag gen m3 magazine 223 5 56$')

# --- Szíjak ----------------------------------------------------------------
S('mp-ms1', ['Magpul MS1 Sling'], r'^magpul ms1 sling$')
S('mp-ms4', ['Magpul MS4 Dual QD'], r'^magpul ms4 dual qd gen 2 sling$', note='A boltban Gen 2 kivitel')
S('bfg-vickers', ['Blue Force Gear Vickers'], r'^blue force gear vickers combat applications? sling$')

# Ismert eltérések / magyarázatok a nem egyező termékekhez. A keresés ezekre is lefut
# (q), és ha mégis lenne pontos találat, azt elfogadja; különben az "unmatched" indoklásához
# fűzi a megjegyzést.
INFO = {
    'b5-t22': ('B5 Systems P-Grip', 'a boltban nincs B5 Systems markolat'),
    'b5-t23': ('B5 Systems P-Grip', 'a boltban nincs B5 Systems markolat'),
    'b5-esop': ('B5 Systems SOPMOD', 'a boltban csak a B5 Bravo Stock van'),
    'b5-prec': ('B5 Systems Precision', 'a boltban csak a B5 Bravo Stock van'),
    'ap-t2': ('Aimpoint Micro T-2', 'Aimpoint optikát nem árul, csak szereléket hozzájuk'),
    'ap-m5': ('Aimpoint CompM5', 'Aimpoint optikát nem árul, csak szereléket hozzájuk'),
    'ap-pro': ('Aimpoint PRO', 'Aimpoint optikát nem árul, csak szereléket hozzájuk'),
    'ap-acro': ('Aimpoint ACRO', 'Aimpoint optikát nem árul, csak szereléket hozzájuk'),
    'ap-3x': ('Aimpoint 3XMag', 'Aimpoint 3XMag nincs a kínálatban'),
    'hs-403b': ('Holosun 403', 'a HS403B nincs a kínálatban'),
    'hs-aems': ('Holosun AEMS', 'csak AEMS-CORE/-EVO/-MACRO/-MICRO/-PRO változatok vannak, az "AEMS" alapmodell nem'),
    'tj-mro': ('Trijicon MRO', 'Trijicon optikát nem árul, csak szereléket (Super Precision, BCM, Midwest)'),
    'tj-acog': ('Trijicon ACOG', 'Trijicon optikát nem árul, csak szereléket'),
    'tj-credo16': ('Trijicon Credo', 'a Credo nincs a kínálatban'),
    'tj-vcog': ('Trijicon VCOG', 'a VCOG nincs a kínálatban (csak QD szerelék)'),
    'vx-sparc': ('Vortex SPARC', 'csak a SPARC Solar Red Dot (CHF 337.90) van, a SPARC AR nem azonos'),
    'vx-cant30': ('Vortex Cantilever', 'csak Vortex Precision Cantilever 30mm van, a Sport Cantilever nem'),
    'sf-m600': ('SureFire M600', 'csak M340C/M340DFT/Infrared Scout Light Pro van, M600DF nincs'),
    'sf-m640': ('SureFire M640', 'csak M340C/M340DFT/Infrared Scout Light Pro van, M640DF nincs'),
    'sf-m300': ('SureFire M300', 'az M300 Mini Scout Light Pro nincs a kínálatban'),
    'sf-x300': ('SureFire X300', 'csak az X300U-A van (CHF 403.20), az X300U-B nem'),
    'sf-warcomp': ('SureFire WarComp', 'SureFire torkolati eszköz nincs a kínálatban'),
    'sf-3p': ('SureFire SF3P', 'SureFire torkolati eszköz nincs a kínálatban'),
    'sf-rc2': ('SureFire SOCOM', 'SureFire hangtompító nincs a kínálatban'),
    'mp-rvg': ('Magpul RVG', 'csak MOE RVG Grip (MOE-slotos) és külön RVG M-LOK Adapter Rail van, a kész M-LOK RVG nem azonos komplett termék'),
    'mp-cable': ('Magpul Cable Management', 'a Cable Management Kit nincs a kínálatban'),
    'hr-sbrm': ('Harris S-BRM', 'Harris bipod nincs a kínálatban (csak adapter/szerelék)'),
    'bcm-ch4': ('BCM Charging Handle', 'csak az utód BCM Charging Handle MK2 (Large/Medium Latch) van; nem azonos a Gunfighter Mod 4-gyel'),
    'bcm-ch3': ('BCM Charging Handle', 'csak az utód BCM Charging Handle MK2 (Large/Medium Latch) van; nem azonos a Gunfighter Mod 3-mal'),
    'gs-ach': ('Geissele Charging Handle', 'csak a Geissele "Super Charging Handle" van, az Airborne nem'),
    'mp-pm30w': ('Magpul PMAG 30 Window', 'Gen M3 Window nincs AR-15-höz (csak Gen M2 MOE Window és Steyr AUG Window)'),
    'mp-d60': ('Magpul D-60', 'a D-60 nincs a kínálatban (csak D-50)'),
}

# ---------------------------------------------------------------------------
# Segédfüggvények
# ---------------------------------------------------------------------------
_last_request = [0.0]
CACHE_DIR = None


def http_get(url):
    """Udvarias GET: legfeljebb ~1 kérés/mp, opcionális lemez-gyorsítótár."""
    if CACHE_DIR:
        fn = os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest())
        if os.path.exists(fn):
            with open(fn, encoding='utf-8') as f:
                return f.read()
    wait = MIN_INTERVAL - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    last_err = None
    for attempt in range(3):
        _last_request[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Accept-Language': 'de-CH,de;q=0.9,en;q=0.5',
                'Accept': 'text/html,application/xhtml+xml'})
            with urllib.request.urlopen(req, timeout=45) as r:
                text = r.read().decode('utf-8', 'replace')
            if CACHE_DIR:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(fn, 'w', encoding='utf-8') as f:
                    f.write(text)
            return text
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError('letöltési hiba: %s (%s)' % (url, last_err))


def norm(s):
    s = html.unescape(s or '').lower().replace('+', ' plus ')
    for a, b in (('ä', 'a'), ('ö', 'o'), ('ü', 'u'), ('ß', 'ss'), ('é', 'e'), ('è', 'e')):
        s = s.replace(a, b)
    return ' '.join(re.findall(r'[a-z0-9]+', s))


def strip_tags(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def money(s):
    """'CHF 1\'385.00' / '81.20' -> float"""
    if s is None or s == '':
        return None
    m = re.search(r'(\d[\d\'\u2019,]*\.?\d*)', str(s))
    if not m:
        return None
    t = m.group(1).replace("'", '').replace('\u2019', '')
    if ',' in t and '.' not in t:
        t = t.replace(',', '.')
    else:
        t = t.replace(',', '')
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def search(q):
    """Kereső: [(artikel_id, listanév, listaár-szöveg, slug)] -- lapozva."""
    out, seen = [], set()
    for p in range(MAX_PAGES):
        params = {'Suchstring': q, 'darstellen': 1, 'lowlimit': p * PAGE, 'highlimit': (p + 1) * PAGE}
        h = http_get(SEARCH_URL + '?' + urllib.parse.urlencode(params))
        boxes = re.findall(
            r'<div class="artikel_box " id="Ziel(\d+)".*?href="(/de/produkt/[^"?]+).*?'
            r'class="content artikel_box_name">(.*?)</a>.*?class="artikel_preis[^"]*"[^>]*>(.*?)</span>\s*(?:<span|</span|<br)', h, re.S)
        if not boxes:   # tartalék: ár nélküli dobozok is
            boxes = [(a, b, c, '') for a, b, c in re.findall(
                r'<div class="artikel_box " id="Ziel(\d+)".*?href="(/de/produkt/[^"?]+).*?class="content artikel_box_name">(.*?)</a>', h, re.S)]
        new = [b for b in boxes if b[0] not in seen]
        for b in new:
            seen.add(b[0])
            out.append({'id': b[0], 'name': html.unescape(strip_tags(b[2])).strip(),
                        'price_txt': strip_tags(b[3]), 'slug': b[1].rsplit('/', 1)[1]})
        if len(boxes) < PAGE or not new:
            break
    return out


COLOR_WORDS = [
    (r'wolf ?gr[ae]y', 'wolfgray'), (r'flat dark earth|dark earth|\bfde\b|\bearth\b', 'fde'),
    (r'\bblack\b|schwarz', 'black'), (r'od green|olive|oliv|\bodg\b|\bod\b', 'odg'),
    (r'\bbronze\b', 'bronze'), (r'foliage', 'foliage'), (r'ranger', 'ranger'),
    (r'medium coyote|\bmcb\b|coyote', 'coyote'), (r'\btan\b|sand', 'tan'),
    (r'\bgr[ae]y\b|\bgrau\b|\bgrey\b', 'gray'), (r'\bbrown\b|braun', 'brown'),
    (r'\bred\b|\brot\b', 'red'), (r'\bblue\b|blau', 'blue'), (r'\bddc\b', 'ddc'),
]


def color_key(*texts):
    for t in texts:
        t = (t or '').lower()
        if not t.strip():
            continue
        for rx, key in COLOR_WORDS:
            if re.search(rx, t):
                return key
    return None


def parse_attrs(block_html):
    """artikelmerkmale (base64 HTML) -> {kulcs: érték}"""
    m = re.search(r'data-name="artikelmerkmale" data-base64="([^"]*)"', block_html)
    attrs = {}
    if m and m.group(1):
        try:
            t = base64.b64decode(m.group(1)).decode('utf-8', 'replace')
        except Exception:  # noqa: BLE001
            t = ''
        for k, v in re.findall(r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>', t, re.S):
            attrs[strip_tags(k)] = strip_tags(v)
    return attrs


def b64field(block_html, name, unq=False):
    m = re.search(r'data-name="%s" data-base64="([^"]*)"' % name, block_html)
    if not m or not m.group(1):
        return ''
    try:
        t = base64.b64decode(m.group(1)).decode('utf-8', 'replace')
    except Exception:  # noqa: BLE001
        return ''
    return urllib.parse.unquote(t) if unq else t


def stock_state(txt):
    """-> (in_stock, qty)"""
    t = txt or ''
    if re.search(r'nicht\s+an\s+lager|ausverkauft|nicht\s+lieferbar', t, re.I):
        return False, None
    m = re.search(r'(\d+)\s*Artikel\s+an\s+Lager', t, re.I)
    if m:
        return True, int(m.group(1))
    if re.search(r'an\s+lager|auf lager|lieferbar|sofort', t, re.I):
        return True, None
    return None, None


def to_grams(num, unit):
    v = money(num.replace(',', '.')) if num else None
    if v is None:
        return None
    u = unit.lower()
    if u in ('kg',):
        return round(v * 1000)
    if u in ('g', 'gr', 'gramm', 'grams'):
        return round(v)
    if u in ('oz', 'ounce', 'ounces'):
        return round(v * 28.3495)
    if u in ('lb', 'lbs', 'pound', 'pounds'):
        return round(v * 453.592)
    return None


WEIGHT_RX = re.compile(r'(?:gewicht|weight|masse)\s*(?:\([^)]*\))?\s*[:=]?\s*(?:ca\.?|approx\.?|~|circa)?\s*'
                       r'(\d[\d.,]*)\s*(kg|gramm|grams|gr|g|oz|ounces?|lbs?|pounds?)\b', re.I)
DIM_KEYS = re.compile(r'm(?:a|ä)sse|ma(?:ss|ß)e|l(?:ä|ae)nge|breite|h(?:ö|oe)he|abmessung|dimension|gr(?:ö|oe)(?:ß|ss)e|'
                      r'length|width|height|durchmesser|diameter|laufl|barrel|tiefe|depth', re.I)


def weight_from(attrs, desc):
    for k, v in attrs.items():
        if re.search(r'gewicht|weight', k, re.I):
            m = re.search(r'(\d[\d.,]*)\s*(kg|gramm|grams|gr|g|oz|ounces?|lbs?|pounds?)\b', v, re.I)
            if m:
                g = to_grams(m.group(1), m.group(2))
                if g:
                    return g, 'attr'
    m = WEIGHT_RX.search(desc or '')
    if m:
        g = to_grams(m.group(1), m.group(2))
        if g:
            return g, 'desc'
    return None, None


def dims_from(attrs, desc):
    parts = ['%s: %s' % (k, v) for k, v in attrs.items() if DIM_KEYS.search(k) and v]
    if parts:
        return '; '.join(parts)
    m = re.search(r'(?:dimensions?|abmessungen?|ma(?:ss|ß)e|l(?:ä|ae)nge|length|overall length)\s*[:=]\s*([^\n.;]{3,90})', desc or '', re.I)
    if m:
        return m.group(0).strip()
    return None


def parse_product(h, url):
    """Termékoldal -> {title, brand, mpn, variants:[...]}"""
    ld = {}
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', h, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get('@type') == 'Product':
            ld = d
            break
    title = html.unescape(ld.get('name') or '').strip()
    if not title:
        m = re.search(r'<h1[^>]*>(.*?)</h1>', h, re.S)
        title = strip_tags(m.group(1)) if m else ''
    brand = (ld.get('brand') or {}).get('name') if isinstance(ld.get('brand'), dict) else None
    url = ld.get('url') or url
    variants = []
    blocks = h.split('<div class="subartikel_content"')[1:]
    for b in blocks:
        b = b.split('<!-- Wird bei Multivarianten', 1)[0] if False else b
        def val(name, attr='value'):
            m = re.search(r'data-name="%s"[^>]*?%s="([^"]*)"' % (name, attr), b)
            return html.unescape(m.group(1)) if m else None
        sku = val('Artikel_Nr')
        price = money(val('preis', 'data-preis'))
        act = money(val('aktions_preis', 'data-preis'))
        alt = money(val('preis_alt'))
        if act is not None:
            price, list_ = act, (alt if alt is not None else price)
        else:
            list_ = None
        stock_txt = strip_tags(val('lagerinfo_anzeige', 'data-lagerinfo') or '')
        in_stock, qty = stock_state(stock_txt)
        attrs = parse_attrs(b)
        desc = strip_tags(b64field(b, 'beschreibung', unq=True))
        img = None
        m = re.search(r'data-name="image_link" data-content="([^"]*)"', b)
        if m:
            img = m.group(1)
        label = val('name')
        mpn = strip_tags(b64field(b, 'hersteller_artikel_nr'))
        w, wsrc = weight_from(attrs, desc)
        variants.append({
            'sku': sku, 'label': label, 'mpn': mpn or None, 'price_chf': price, 'list_chf': list_,
            'image': img, 'stock_text': stock_txt, 'in_stock': in_stock, 'stock_qty': qty,
            'attrs': attrs, 'weight_g': w, 'weight_src': wsrc, 'dims': dims_from(attrs, desc),
            'color_attr': attrs.get('Farbe') or attrs.get('Color') or attrs.get('Colour'),
        })
    if not variants:    # egyváltozatos termékoldal: a főoldal adatai
        m = re.search(r'id="artikel_preis"[^>]*>.*?data-update="preis\.preis"[^>]*>([^<]+)<', h, re.S)
        price = money(m.group(1)) if m else None
        off = ld.get('offers') or {}
        if isinstance(off, list):
            off = off[0] if off else {}
        if price is None:
            price = money(off.get('price'))
        list_ = None
        m = re.search(r'<span id="artikel_preis_alt"([^>]*)>.*?data-update="preis_alt"[^>]*>([^<]+)<', h, re.S)
        if m and 'display: none' not in m.group(1) and 'display:none' not in m.group(1):
            alt = money(m.group(2))
            if alt is not None and price is not None and alt != price:
                list_ = alt
        m = re.search(r'data-update="lagerinfo_anzeige\.lagerinfo">(.*?)</span>\s*</div>', h, re.S)
        stock_txt = strip_tags(m.group(1)) if m else ''
        in_stock, qty = stock_state(stock_txt)
        if in_stock is None:
            av = str(off.get('availability', ''))
            in_stock = True if av.endswith('InStock') else (False if av else None)
            stock_txt = stock_txt or av.rsplit('/', 1)[-1]
        attrs = {}
        for dl in re.findall(r'<dl class="dl-horizontal artikel_eigenschaften">(.*?)</dl>', h, re.S):
            for k, v in re.findall(r'<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>', dl, re.S):
                attrs[strip_tags(k)] = strip_tags(v)
        desc = strip_tags(ld.get('description') or '')
        w, wsrc = weight_from(attrs, desc)
        variants.append({
            'sku': ld.get('sku'), 'label': None, 'mpn': ld.get('mpn'), 'price_chf': price, 'list_chf': list_,
            'image': (ld.get('image') or [None])[0], 'stock_text': stock_txt, 'in_stock': in_stock, 'stock_qty': qty,
            'attrs': attrs, 'weight_g': w, 'weight_src': wsrc, 'dims': dims_from(attrs, desc),
            'color_attr': attrs.get('Farbe') or attrs.get('Color')})
    return {'title': title, 'brand': brand, 'url': url, 'variants': variants}


# ---------------------------------------------------------------------------
# data.js beolvasása
# ---------------------------------------------------------------------------
def load_items():
    with open(DATA_JS, encoding='utf-8') as f:
        src = f.read()
    items = []
    for m in re.finditer(r"\{\s*id:\s*'([^']+)'(?:,\s*cat:\s*'[^']*')?,\s*brand:\s*'([^']*)',\s*name:\s*'((?:[^'\\]|\\.)*)'", src):
        id_, brand, name = m.group(1), m.group(2), m.group(3).replace("\\'", "'")
        if id_.endswith('-factory') or id_.endswith('-none') or brand in ('Gyári', '—'):
            continue
        items.append({'id': id_, 'brand': brand, 'name': name})
    return items


def literal_words(rx):
    return set(re.findall(r'[a-z0-9]+', re.sub(r'\\[a-z]', ' ', rx or '')))


def make_matcher(item, spec):
    """-> (queries, ok_fn(title_norm)->bool, no_fn, words)"""
    if spec and spec.get('ok'):
        ok, no = re.compile(spec['ok']), re.compile(spec['no']) if spec.get('no') else None
        words = literal_words(spec['ok'])
        return spec['q'], (lambda t: bool(ok.search(t)) and not (no and no.search(t))), \
            (lambda t: bool(no and no.search(t))), words
    # automatikus: a név (a '+' utáni magyar kiegészítés nélkül) minden szava szerepeljen a bolti névben
    base = item['name'].split('+')[0]
    toks = [w for w in norm(base).split() if w not in ('szett', 'gen')] or norm(base).split()
    q = (spec or {}).get('q') or [item['brand'] + ' ' + base.strip()]
    brand_t = norm(item['brand']).split()
    return q, (lambda t: all(w in t.split() for w in toks) and all(w in t.split() for w in brand_t[:1])), \
        (lambda t: False), set(toks)


# ---------------------------------------------------------------------------
# Egy termék feldolgozása
# ---------------------------------------------------------------------------
def find_candidates(item, spec):
    queries, ok_fn, no_fn, words = make_matcher(item, spec)
    seen, pool = {}, []
    for q in queries:
        for r in search(q):
            if r['id'] in seen:
                continue
            seen[r['id']] = r
            pool.append(r)
    exact, maybe = [], []
    for r in pool:
        t = norm(r['name'])
        if r['name'].endswith('..'):
            toks = t.split()
            if toks and not no_fn(t) and sum(1 for w in toks if w in words) >= 0.8 * len(toks):
                maybe.append(r)
        elif ok_fn(t):
            exact.append(r)
    return pool, exact, maybe[:8], ok_fn


def fetch_product(r):
    url = BASE + '/de/produkt/' + r['slug']
    h = http_get(url)
    p = parse_product(h, url)
    p['list_name'] = r['name']
    p['id'] = r['id']
    return p


def variant_color(prod, v, single):
    return color_key(v.get('color_attr'), v.get('label') if not single else None, prod['title'] if single else None,
                     v.get('label'))


def variant_record(prod, v, ckey):
    return {'label': v.get('color_attr') or v.get('label'), 'color': ckey, 'title': prod['title'], 'url': prod['url'],
            'sku': v.get('sku'), 'price_chf': v['price_chf'], 'list_chf': v['list_chf'], 'image': v['image'],
            'in_stock': v['in_stock'], 'stock_qty': v['stock_qty'], 'stock_text': v['stock_text'],
            'weight_g': v['weight_g'], 'weight_src': v['weight_src'], 'dims': v['dims']}


def pick_primary(recs, multi=False):
    if multi:
        return sorted(recs, key=lambda r: r['title'])[0]
    blacks = [r for r in recs if r['color'] == 'black' and r['price_chf'] is not None]
    pool = blacks or [r for r in recs if r['price_chf'] is not None]
    return min(pool, key=lambda r: (r['in_stock'] is not True, r['price_chf'])) if pool else None


def build_item(prods, spec):
    recs = []
    for pr in prods:
        single = len(pr['variants']) == 1
        for v in pr['variants']:
            recs.append(variant_record(pr, v, variant_color(pr, v, single)))
    recs = [r for r in recs if r['price_chf'] is not None]
    if not recs:
        return None, 'a termékoldalon nincs ár'
    prim = pick_primary(recs, bool(spec and spec.get('multi')))
    out = {
        'title': prim['title'], 'price_chf': prim['price_chf'], 'list_chf': prim['list_chf'], 'url': prim['url'],
        'image': prim['image'], 'in_stock': prim['in_stock'], 'weight_g': prim['weight_g'], 'dims': prim['dims'],
        'color': prim['label'] or prim['color'], 'sku': prim['sku'], 'stock_text': prim['stock_text'], 'stock_qty': prim['stock_qty'],
    }
    if prim['weight_g'] is not None:
        out['weight_src'] = prim['weight_src']
    # a primer változat hiányzó súly/méret adatát más változatból NEM pótoljuk (szín szerint eltérhet)
    by_color = {}
    for r in recs:
        k = r['color'] or norm(r['label'] or '') or 'default'
        if k in by_color and by_color[k]['price_chf'] <= r['price_chf']:
            continue
        by_color[k] = r
    if len(by_color) >= 2 and not (spec and spec.get('multi')):
        out['colors_prices'] = {k: {'label': r['label'], 'price_chf': r['price_chf'], 'list_chf': r['list_chf'],
                                    'in_stock': r['in_stock'], 'url': r['url'], 'image': r['image'], 'sku': r['sku']}
                                for k, r in by_color.items()}
    if spec and spec.get('multi'):
        out['variants'] = [{'title': r['title'], 'price_chf': r['price_chf'], 'url': r['url'], 'in_stock': r['in_stock']}
                           for r in sorted(recs, key=lambda r: r['title'])]
    if spec and spec.get('note'):
        out['note'] = spec['note']
    return out, None


def similar(item, pool, n=3):
    base = set(norm(item['brand'] + ' ' + item['name'].split('+')[0]).split())
    sc = []
    for r in pool:
        t = set(norm(r['name']).split())
        sc.append((len(base & t), r))
    sc.sort(key=lambda x: -x[0])
    return ['%s (%s)' % (r['name'], r['price_txt']) for s, r in sc[:n] if s >= 2]


def process(item):
    spec = SPECS.get(item['id'])
    info = INFO.get(item['id'])
    if info and not spec:
        spec = {'q': [info[0]], 'ok': None, 'no': None}
    # --- készlet-összeállítás (első + hátsó stb.)
    if spec and spec.get('set'):
        comps, pool_all = [], []
        for c in spec['set']:
            cs = {'q': spec['q'], 'ok': c['ok'], 'no': c.get('no')}
            pool, exact, maybe, _ = find_candidates(item, cs)
            pool_all += pool
            exact = exact + [m for m in maybe if re.search(c['ok'], norm(fetch_product(m)['title']))] if maybe else exact
            if len(exact) != 1:
                return None, 'alkatrész nem egyértelmű (%d találat) a keresésre: %s' % (len(exact), c['ok'])
            comps.append(fetch_product(exact[0]))
        parts = []
        for pr in comps:
            recs = [variant_record(pr, v, variant_color(pr, v, len(pr['variants']) == 1)) for v in pr['variants']]
            recs = [r for r in recs if r['price_chf'] is not None]
            prim = pick_primary(recs)
            if not prim:
                return None, 'alkatrész ára nem olvasható: ' + pr['title']
            parts.append(prim)
        price = round(sum(p['price_chf'] for p in parts), 2)
        lst = None
        if any(p['list_chf'] for p in parts):
            lst = round(sum((p['list_chf'] or p['price_chf']) for p in parts), 2)
        out = {'title': ' + '.join(p['title'] for p in parts), 'price_chf': price, 'list_chf': lst, 'url': parts[0]['url'],
               'image': parts[0]['image'], 'in_stock': all(p['in_stock'] is True for p in parts) if all(p['in_stock'] is not None for p in parts) else None,
               'weight_g': None, 'dims': None, 'color': parts[0]['label'],
               'components': [{'title': p['title'], 'price_chf': p['price_chf'], 'list_chf': p['list_chf'], 'url': p['url'],
                               'in_stock': p['in_stock'], 'image': p['image'], 'weight_g': p['weight_g']} for p in parts],
               'note': spec.get('note')}
        wg = [p['weight_g'] for p in parts]
        if all(w is not None for w in wg):
            out['weight_g'] = sum(wg)
        return out, None

    pool, exact, maybe, ok_fn = find_candidates(item, spec)
    prods = []
    for r in exact:
        prods.append(fetch_product(r))
    for r in maybe:      # csonkolt listanév: a teljes névvel ellenőrizzük
        pr = fetch_product(r)
        if ok_fn(norm(pr['title'])):
            prods.append(pr)
    # a termékoldal teljes nevével is ellenőrzünk
    prods = [p for p in prods if ok_fn(norm(p['title'])) or ok_fn(norm(p['list_name']))]
    uniq, seen = [], set()
    for p in prods:
        if p['id'] not in seen:
            seen.add(p['id'])
            uniq.append(p)
    prods = uniq
    if not prods:
        sim = similar(item, pool)
        reason = 'nincs a boltban: a keresés (%s) nem adott pontosan azonos terméket' % ', '.join(spec['q'] if spec else [item['brand'] + ' ' + item['name']])
        if sim:
            reason += '; hasonló, de nem azonos: ' + ' | '.join(sim)
        if info and info[1]:
            reason += '. Megjegyzés: ' + info[1]
        return None, reason
    allow_multi = bool(spec and (spec.get('colors') or spec.get('multi')))
    if len(prods) > 1 and not allow_multi:
        return None, 'bizonytalan egyezés: %d azonos szabályú találat: %s' % (len(prods), ' | '.join(p['title'] for p in prods))
    if len(prods) > 1 and spec.get('colors'):
        # csak akkor szín-család, ha minden oldalon különböző szín azonosítható
        keys = [color_key(*[v.get('color_attr') for v in p['variants']], p['title']) for p in prods]
        if len(set(keys)) != len(keys) and len(prods) != 1:
            # több oldalon is azonos szín -> nem egyértelmű
            pass
    return build_item(prods, spec)


def main():
    global CACHE_DIR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--only', help='vesszővel elválasztott id-k (pl. mp-ctr,bcm-g0)')
    ap.add_argument('--out', default=None, help='kimeneti JSON (alap: assets/prices/shooting-store.json; --only esetén nincs írás, ha nincs megadva)')
    ap.add_argument('--cache', default=None, help='gyorsítótár-könyvtár a letöltésekhez')
    ap.add_argument('--list-specs', action='store_true')
    args = ap.parse_args()
    CACHE_DIR = args.cache
    items = load_items()
    if args.list_specs:
        for i in items:
            print(i['id'], '->', (SPECS.get(i['id']) or {}).get('ok') or 'auto')
        return
    if args.only:
        want = set(args.only.split(','))
        items = [i for i in items if i['id'] in want]
    res, unmatched = {}, {}
    for n, it in enumerate(items, 1):
        try:
            out, reason = process(it)
        except Exception as e:  # noqa: BLE001
            out, reason = None, 'hiba a feldolgozás közben: %s' % e
        if out:
            res[it['id']] = out
            print('[%d/%d] OK   %-12s CHF %-8s %s' % (n, len(items), it['id'], out['price_chf'], out['title'][:60]), file=sys.stderr)
        else:
            unmatched[it['id']] = reason
            print('[%d/%d] --   %-12s %s' % (n, len(items), it['id'], reason[:110]), file=sys.stderr)
    doc = {'shop': 'shooting-store.ch', 'currency': 'CHF', 'fetched': datetime.date.today().isoformat(),
           'items': res, 'unmatched': unmatched}
    out_path = args.out or (None if args.only else OUT_JSON)
    if out_path:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write('\n')
        print('kiírva: %s (%d egyező, %d kimaradt)' % (out_path, len(res), len(unmatched)), file=sys.stderr)
    else:
        print(json.dumps(doc, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
