#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shop.indoorswiss.ch (Indoor Swiss Shooting AG, Gossau SG) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_indoorswiss.py                      # minden termék -> assets/prices/indoorswiss.json
    python3 tools/prices_indoorswiss.py --only mp-pm10,bcm-kag   # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_indoorswiss.py --cache /tmp/iscache     # HTTP-gyorsítótár (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  1. A js/data.js-ből kiolvassa a BASES + ITEMS termékeket (id, márka, név, színpaletta).
  2. A bolt (PrestaShop 1.7) gyártói oldalait (/de/hersteller -> /de/<id>_<márka>?resultsPerPage=100)
     és a RULES-ban megadott keresőkifejezéseket (/de/suche?s=...) tölti le; a találati kártyákból
     név, alcím, ár, „ab” jelző, URL.
  3. Egyezés: a RULES táblában id-nként a bolti név PONTOS (fullmatch) mintája (+ opcionális
     alcím-minta). Szabály nélkül csak a teljesen azonos név (márka + név) egyezik.
  4. Találat -> termékoldal: a `#product-details[data-product]` JSON adja az árat (akciónál a
     ténylegesen fizetendőt + az áthúzott árat), készletet, cikkszámot, MPN-t (isbn mező), képet.
     A színváltozatokat a PrestaShop saját GET-paraméterével (`?group[<csoport>]=<érték>`) kéri le,
     változatonként ár/készlet/URL. Ha egy szín külön termék (pl. BCM ... Black / FDE), azokat is
     összevonja a colors_prices-ba.
  5. Ha nincs pontos egyezés: 'unmatched' okkal (+ a legközelebbi bolti termék(ek) nevével, árával).
     Árat soha nem becsül, nem számol, nem vált át.

Udvariasság: egy szálon, kérések között >= 1,1 s, böngésző User-Agent; 429/5xx esetén visszalépés.
403 / Cloudflare-kihívás / captcha esetén AZONNAL leáll ('blocked'), nem próbálja megkerülni.
Csak a shop.indoorswiss.ch hostra kérdez (a képeknek csak az URL-jét rögzíti).
"""
import argparse
import datetime
import hashlib
import itertools
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("Hiányzik a beautifulsoup4: pip install beautifulsoup4 lxml")

SHOP = "shop.indoorswiss.ch"
BASE = "https://shop.indoorswiss.ch"
LANG = "/de"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.1

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "indoorswiss.json")


class Blocked(Exception):
    pass


# --------------------------------------------------------------------------- HTTP
class Http:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir
        self.last = 0.0
        self.n = 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def get(self, url, params=None):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        host = urllib.parse.urlparse(url).netloc
        if host != SHOP:
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, "rb") as f:
                    return f.read().decode("utf-8", "replace")
        headers = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5"}
        last_err = None
        for attempt in range(4):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                if re.search(r"cf-chl|challenge-platform/h/|Just a moment\.\.\.|Verifying (you are human|connection)"
                             r"|g-recaptcha|hcaptcha", body[:20000], re.I):
                    raise Blocked("bot-védelmi oldal (challenge/captcha): " + url)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise Blocked("HTTP 403: " + url)
                if e.code == 429 and attempt >= 2:
                    raise Blocked("tartós HTTP 429: " + url)
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1) * (3 if e.code == 429 else 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError("letöltés sikertelen: %s (%s)" % (url, last_err))


def soup(markup):
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")


# --------------------------------------------------------------------------- segédek
def norm(s):
    s = unicodedata.normalize("NFKC", s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", norm(s).lower())


def parse_chf(text):
    """'CHF 1'281.90' -> 1281.9"""
    m = re.search(r"([0-9][0-9’'’ ]*(?:\.[0-9]{1,2})?)", norm(text))
    return float(re.sub(r"[’'’ ]", "", m.group(1))) if m else None


def money(x):
    return None if x is None else round(float(x), 2)


# --------------------------------------------------------------------------- data.js
def read_data_js(path=DATA_JS):
    """-> [dict(id, cat, brand, name, palette)] a BASES + ITEMS literál objektumaiból."""
    with open(path, encoding="utf-8") as f:
        src = f.read()
    consts = {m.group(1): re.findall(r"'([^']+)'", m.group(2))
              for m in re.finditer(r"const\s+(\w+)\s*=\s*\[([^\]]*)\]", src)}
    out = []
    for m in re.finditer(r"\{\s*id:\s*'([^']+)'", src):
        i, j, depth, instr = m.start(), m.start(), 0, False
        while j < len(src):
            c = src[j]
            if instr:
                if c == "\\":
                    j += 1
                elif c == "'":
                    instr = False
            elif c == "'":
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = src[i:j + 1]

        def sfield(k):
            mm = re.search(r"\b%s:\s*'((?:[^'\\]|\\.)*)'" % k, body)
            return mm.group(1).replace("\\'", "'") if mm else None

        brand, name = sfield("brand"), sfield("name")
        if not brand or not name:
            continue  # kategória-objektum
        pal = None
        mm = re.search(r"\b(colors|finishes):\s*(\w+|\[[^\]]*\])", body)
        if mm:
            v = mm.group(2)
            pal = re.findall(r"'([^']+)'", v) if v.startswith("[") else consts.get(v)
        out.append(dict(id=m.group(1), cat=sfield("cat") or "base", brand=brand, name=name,
                        palette=pal, is_base=sfield("cat") is None))
    return out


# --------------------------------------------------------------------------- színek
COLOR_WORDS = [
    (r"schwarz|black|blk", "black"),
    (r"flat dark earth|fde", "fde"),
    (r"dark earth", "earth"),
    (r"od ?green|odg|olive drab|oliv(e|grün)?", "odg"),
    (r"stealth gr[ae]y|gr[ae]y|grau", "gray"),
    (r"wolf gr[ae]y", "wolfgray"),
    (r"foliage( green)?", "foliage"),
    (r"ranger green|ranger", "ranger"),
    (r"coyote( brown)?", "coyote"),
    (r"bronze", "bronze"),
]


def color_key(label, palette, is_base=False):
    """Bolti színnév -> data.js palettakulcs (csak ha a paletta tartalmazza)."""
    if not label or not palette:
        return None
    lab = norm(label).lower()
    for pat, key in COLOR_WORDS:
        if re.fullmatch(pat, lab):
            if is_base:
                key = {"odg": "olive", "fde": "fde" if "fde" in palette else "earth"}.get(key, key)
            return key if key in palette else None
    return None


def color_from_text(text, palette, is_base=False):
    """Színszó a névben/alcímben (pl. 'BCMGunfighter KAG - Black') -> palettakulcs."""
    t = " " + norm(text).lower() + " "
    for pat, key in sorted(COLOR_WORDS, key=lambda x: -len(x[0])):
        if re.search(r"[\s\-/(](%s)[\s\-/)]" % pat, t):
            return color_key(re.search(r"[\s\-/(](%s)[\s\-/)]" % pat, t).group(1), palette, is_base)
    return None


# --------------------------------------------------------------------------- szabályok
def R(q=None, title=None, sub=None, near=None, why=None, note=None):
    """q: keresőkifejezés(ek) (a márka gyártói oldala mellett);
    title: a bolti terméknév PONTOS mintája (re.fullmatch, kisbetű-független) – None: nincs azonos termék;
    sub: a kártya alcímének mintája (re.search), pl. 'M-LOK Version';
    near: minta a tájékoztató 'legközelebbi' termékekhez; why: indok az unmatched-hez; note: megjegyzés."""
    if isinstance(q, str):
        q = [q]
    return dict(q=q or [], title=title, sub=sub, near=near, why=why, note=note)


NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a márkát/terméket)"
NOT_FOUND = "nincs a boltban (a márka kínálatában nincs ilyen termék)"
FIREARM_NA = "ez a puskamodell nincs a bolt kínálatában"
SET_BUIS = ("szett (első + hátsó irányzék): a bolt az MBUS első és hátsó irányzékot külön termékként árulja "
            "(generáció megjelölése nélkül), szett nincs")

RULES = {
    # ---- alapfegyverek
    "schm-s4f": R(None, None, near=r"Schmeisser AR15.*", why=FIREARM_NA),
    "schm-m4fl": R(None, None, near=r"Schmeisser AR15.*",
                   why=FIREARM_NA + " (csak az AR15-9 M4FL 9x19 mm PCC, ami más fegyver)"),
    "schm-m5fl": R(None, None, near=r"Schmeisser AR15.*", why=FIREARM_NA),
    "schm-dyn": R(None, None, near=r"Schmeisser AR15.*", why=FIREARM_NA),
    "dd-v7": R(None, None, near=r"Daniel Defense (M4|DD4).*",
               why="a DDM4 V7 nincs a boltban (csak M4 MK18, DD4 M4A1 RIII 14.5\", PCC)"),
    "bcm-recce16": R(None, None, why=NOT_SOLD),
    # ---- markolatok
    "mp-moe": R(None, r"Magpul MOE Grip( AR15/M4)?", near=r"Magpul MOE.*Grip.*", why=NOT_FOUND),
    "mp-moek2": R(None, r"Magpul MOE-?K2 Grip( AR15/M4)?"),
    "mp-moek2p": R(None, r"Magpul MOE-?K2\+ Grip( AR15/M4)?", why=NOT_FOUND),
    "mp-miad": R(None, r"Magpul MIAD.*", why=NOT_FOUND),
    "bcm-g0": R(None, r"BCM ?Gunfighter Grip Mod ?0.*", why=NOT_FOUND),
    "bcm-g3": R(None, r"BCM ?Gunfighter Grip Mod ?3.*", why=NOT_FOUND),
    "b5-t22": R(None, None, why=NOT_SOLD),
    "b5-t23": R(None, None, why=NOT_SOLD),
    # ---- tusok
    "mp-ctr": R(None, r"Magpul CTR.*Stock.*", why=NOT_FOUND),
    "mp-moesl": R(None, r"Magpul MOE SL .*Stock.*", why=NOT_FOUND),
    "mp-moeslk": R(None, r"Magpul MOE SL-K.*Stock.*", why=NOT_FOUND),
    "mp-ubr2": R(None, r"Magpul UBR.*", why=NOT_FOUND),
    "mp-prs3": R(None, r"Magpul PRS Gen ?3.*", why=NOT_FOUND),
    "mp-prslite": R(None, r"Magpul PRS Lite.*", why=NOT_FOUND),
    "bcm-s0": R(None, r"BCM ?Gunfighter Stock Mod ?0.*", why=NOT_FOUND),
    "bcm-s1": R(None, r"BCM ?Gunfighter Stock Mod ?1.*", why=NOT_FOUND),
    "b5-bravo": R(None, None, why=NOT_SOLD),
    "b5-esop": R(None, None, why=NOT_SOLD),
    "b5-prec": R(None, None, why=NOT_SOLD),
    # ---- tustalpak
    "mp-pad030": R(None, r"Magpul Rubber Butt-?Pad.*", why=NOT_FOUND),
    "mp-pad055": R(None, r"Magpul Extended Rubber Butt-?Pad.*", why=NOT_FOUND),
    "mp-pad070": R(None, r"Magpul Enhanced Rubber Butt-?Pad.*", why=NOT_FOUND),
    "mp-padsl": R(None, r"Magpul MOE SL Enhanced Rubber Butt-?Pad.*", why=NOT_FOUND),
    # ---- red dot / holo
    "ap-t2": R(None, None, why=NOT_FOUND + " (az Aimpoint-kínálat csak ACRO P-2)"),
    "ap-m5": R(None, None, why=NOT_FOUND + " (az Aimpoint-kínálat csak ACRO P-2)"),
    "ap-pro": R(None, None, why=NOT_FOUND + " (az Aimpoint-kínálat csak ACRO P-2)"),
    "ap-acro": R(None, None, near=r"Aimpoint ACRO P-2.*",
                 why="csomag (ACRO P-2 + Picatinny talp): a bolt az ACRO P-2-t talp nélkül árulja (MPN AP-200691)"),
    "eo-exps3": R(None, r"Eotech (HWS )?EXPS ?3-0.*", near=r"Eotech HWS .*",
                  why="az EXPS3-0 nincs a boltban (csak XPS3-0 és 552.A65 – más modellek)"),
    "eo-exps2": R(None, r"Eotech (HWS )?EXPS ?2-0.*", near=r"Eotech HWS .*",
                  why="az EXPS2-0 nincs a boltban (csak XPS3-0 és 552.A65 – más modellek)"),
    "eo-xps2": R(None, r"Eotech (HWS )?XPS ?2-0.*", near=r"Eotech HWS .*",
                 why="az XPS2-0 nincs a boltban; a 'HWS XPS3-0' másik típusjelzés (XPS3 ≠ XPS2)"),
    "hs-510c": R(None, r"Holosun (HS)?510C.*", why=NOT_FOUND),
    "hs-aems": R(None, r"Holosun AEMS.*", why=NOT_FOUND),
    "hs-403b": R(None, r"Holosun (HS)?403B.*", why=NOT_FOUND),
    "tj-mro": R(None, None, why=NOT_SOLD),
    "vx-uh1": R(None, r"Vortex.*UH-1.*", why=NOT_FOUND + " (a Vortex-kínálat 1 termék)"),
    "vx-sparc": R(None, r"Vortex.*SPARC.*", why=NOT_FOUND + " (a Vortex-kínálat 1 termék)"),
    "vx-crossfire": R(None, r"Vortex.*Crossfire.*", why=NOT_FOUND + " (a Vortex-kínálat 1 termék)"),
    # ---- LPVO
    "vx-se16": R(None, r"Vortex.*Strike Eagle 1-6x24.*", why=NOT_FOUND + " (a Vortex-kínálat 1 termék)"),
    "vx-se18": R(None, r"Vortex.*Strike Eagle 1-8x24.*", why=NOT_FOUND + " (a Vortex-kínálat 1 termék)"),
    "vx-pst16": R(None, r"Vortex.*Viper PST.*1-6x24.*", why=NOT_FOUND + " (a Vortex-kínálat 1 termék)"),
    "vx-razor110": R(None, r"Vortex.*Razor.*1-10x24.*", why=NOT_FOUND + " (a Vortex-kínálat 1 termék)"),
    "tj-acog": R(None, None, why=NOT_SOLD),
    "tj-credo16": R(None, None, why=NOT_SOLD),
    "tj-vcog": R(None, None, why=NOT_SOLD),
    "eo-vudu16": R(None, r"Eotech Vudu 1-6x24.*", why=NOT_FOUND),
    "eo-vudu110": R(None, r"Eotech Vudu 1-10x28.*", why=NOT_FOUND),
    # ---- optikaszerelék
    "gs-sp30": R(None, r"Geissele.*Super Precision.*", why=NOT_FOUND),
    "vx-cant30": R(None, r"Vortex Sport Cantilever.*30 ?mm.*", near=r"Vortex .*Cantilever.*",
                   why="a 'Sport Cantilever 30 mm' nincs a boltban; a 'Precision QR Cantilever Mount 30 mm 2\"' másik termék"),
    # ---- nagyítók
    "eo-g33": R(None, r"Eotech G33.*", why=NOT_FOUND),
    "eo-g43": R(None, r"Eotech G43.*", why=NOT_FOUND),
    "ap-3x": R(None, None, why=NOT_FOUND + " (az Aimpoint-kínálat csak ACRO P-2)"),
    "hs-hm3x": R(None, r"Holosun HM3X( Magnifier)?",
                 note="a termékleírás szerint gyorsoldó (QD) szerelékkel és magasítóval"),
    "vx-m3x": R(None, r"Vortex.*Micro ?3X.*", why=NOT_FOUND + " (a Vortex-kínálat 1 termék)"),
    # ---- BUIS
    "mp-mbus3": R(None, None, near=r"Magpul MBUS Sight (Front|Rear)", why=SET_BUIS),
    "mp-mbuspro": R(None, None, near=r"Magpul MBUS .*", why="az MBUS Pro nincs a boltban"),
    "mp-mbusoff": R(None, None, near=r"Magpul MBUS .*", why="az MBUS Pro Offset nincs a boltban"),
    # ---- lámpák
    "sf-m600": R("surefire", None, why=NOT_SOLD),
    "sf-m640": R("surefire", None, why=NOT_SOLD),
    "sf-m300": R("surefire", None, why=NOT_SOLD),
    "sf-x300": R("surefire", None, why=NOT_SOLD),
    "sl-hlx": R("streamlight", None, why=NOT_SOLD),
    "sl-tlr1": R("streamlight", None, why=NOT_SOLD),
    # ---- M-LOK
    "mp-rvg": R(None, r"Magpul (M-LOK )?(MOE )?RVG.*", why=NOT_FOUND),
    "mp-hstop": R(None, r"Magpul M-LOK Hand Stop.*", why=NOT_FOUND),
    "bcm-vg3": R(None, r"BCM ?Gunfighter (Front|Vertical) Grip Mod ?3( - | )?(Black|FDE)?", sub=r"M-LOK",
                 note="a bolt 'BCMGunfighter Front Grip Mod 3 … M-LOK Version' néven árulja; a termékleírás "
                      "szerint 'BCMGunfighter Vertical Grip Mod 3 M-LOK'. A Picatinny-változat (34.50) más kivitel."),
    "bcm-kag": R(None, r"BCM ?Gunfighter KAG( - | )?(Black|FDE)?", sub=r"M-LOK",
                 note="M-LOK kivitel (a bolt külön Picatinny-változatot is árul, az nem ez); csak fekete van"),
    "mp-rail5": R(None, r"Magpul M-LOK (Aluminum|Aluminium) Rail (Section )?5 Slots?.*",
                  near=r"Magpul M-LOK (Aluminum|Aluminium) Rail.*",
                  why="az 5 slotos alumínium M-LOK sín nincs a boltban, csak a 7 slotos"),
    "mp-qd": R(None, r"Magpul M-LOK QD Sling Mount"),
    "mp-cover": R(None, r"Magpul M-LOK Rail Cover.*", why=NOT_FOUND),
    "mp-cable": R(None, r"Magpul M-LOK Cable.*", why=NOT_FOUND),
    # ---- bipod
    "mp-bipod": R(None, r"Magpul Bipod (für|for) M-LOK",
                  note="Magpul Bipod M-LOK (MPN MAG933, a data.js 6,3–10,3\" modellje); a bolt egyetlen Magpul M-LOK bipodja"),
    "hr-sbrm": R("harris", None, why=NOT_SOLD),
    # ---- torkolat
    "sf-warcomp": R(None, None, why=NOT_SOLD),
    "sf-3p": R(None, None, why=NOT_SOLD),
    "bcm-comp0": R(None, r"BCM.*Comp.*", why=NOT_FOUND),
    "sf-rc2": R(None, None, why=NOT_SOLD),
    # ---- elsütők
    "gs-ssae": R(None, r"Geissele.*SSA-E.*", near=r"Geissele .*Trigger.*",
                 why="az SSA-E nincs a boltban; a '2-Stage Match Trigger' a G2S (más modell)"),
    "gs-ssa": R(None, r"Geissele.*\bSSA\b(?!-).*", near=r"Geissele .*Trigger.*",
                why="az SSA nincs a boltban; a '2-Stage Match Trigger' a leírás szerint a G2S (az SSA olcsóbb, nem állítható változata, más modell)"),
    "gs-sde": R(None, r"Geissele.*SD-E.*", near=r"Geissele .*Trigger.*",
                why="az SD-E nincs a boltban (csak G2S)"),
    "gs-s3g": R(None, r"Geissele.*(S3G|Super 3 Gun).*", near=r"Geissele .*Trigger.*",
                why="az S3G nincs a boltban (csak G2S)"),
    # ---- felhúzókar
    "rd-raptor": R("radian", None, why=NOT_SOLD),
    "rd-raptorlt": R("radian", None, why=NOT_SOLD),
    "rd-raptorsd": R("radian", None, why=NOT_SOLD),
    "bcm-ch4": R(None, r"BCM.*Charging Handle.*", why=NOT_FOUND),
    "bcm-ch3": R(None, r"BCM.*Charging Handle.*", why=NOT_FOUND),
    "gs-ach": R(None, r"Geissele.*Airborne.*", why=NOT_FOUND),
    # ---- kezelőszervek
    "rd-talon": R("radian", None, why=NOT_SOLD),
    "mp-bad": R(None, r"Magpul B\.?A\.?D\.? Lever.*", why=NOT_FOUND),
    "mp-trigguard": R(None, r"Magpul (MOE )?Enhanced Trigger Guard.*", why=NOT_FOUND),
    # ---- tárak
    "mp-pm10": R(None, r"Magpul PMAG 10 AR/M4 GEN M3"),
    "mp-pm20": R(None, r"Magpul PMAG 20 AR/M4 GEN M3"),
    "mp-pm30": R(None, r"Magpul PMAG 30 AR/M4 GEN M3", near=r"Magpul PMAG 30 AR/M4 .*",
                 why="a PMAG 30 AR/M4 Gen M3 (ablak nélküli) nincs a boltban; a 'GEN M2 MOE' és a 'GEN M3 Window' más termék"),
    "mp-pm30w": R(None, r"Magpul PMAG 30 AR/M4 GEN M3 Window"),
    "mp-pm40": R(None, r"Magpul PMAG 40 .*", why=NOT_FOUND),
    "mp-d60": R(None, r"Magpul (PMAG )?D-60.*", why=NOT_FOUND),
    # ---- szíjak
    "mp-ms1": R(None, r"Magpul MS1 Sling.*", why=NOT_FOUND),
    "mp-ms4": R(None, r"Magpul MS4 Dual QD Sling( Gen ?2)?",
                note="a bolt a Gen2 kivitelt árulja (MPN MAG518)"),
    "bfg-vickers": R("vickers", None, why=NOT_SOLD),
}

# data.js márka -> a bolt gyártói oldalának neve (a /de/hersteller listán)
BRAND_PAGES = {
    "Magpul": "Magpul", "BCM": "BCM", "Aimpoint": "Aimpoint", "EOTech": "Eotech",
    "Geissele": "Geissele Automatics", "Holosun": "Holosun", "Vortex": "Vortex",
    "Schmeisser": "Schmeisser", "Daniel Defense": "Daniel Defense",
}


# --------------------------------------------------------------------------- katalógus
def parse_cards(body):
    s = soup(body)
    root = s.select_one("#js-product-list") or s
    cards = []
    for a in root.select("article.product-miniature"):
        n = a.select_one(".product-name a")
        if not n or not n.get("href"):
            continue
        pr = a.select_one(".product-price-and-shipping")
        price = lst = None
        ab = False
        if pr:
            p = pr.select_one("[itemprop=price]")
            price = parse_chf(p.get_text(" ")) if p else None
            rp = pr.select_one(".regular-price")
            lst = parse_chf(rp.get_text(" ")) if rp else None
            ab = bool(pr.select_one(".prange_price_from"))
        sub = a.select_one(".product-text2")
        url = n["href"].split("#")[0]
        cards.append(dict(name=norm(n.get("title") or n.get_text(" ")),
                          sub=norm(sub.get_text(" ")) if sub else "",
                          url=url, price=price, list=lst, ab=ab,
                          id_product=a.get("data-id-product"),
                          ipa=a.get("data-id-product-attribute")))
    pag = s.select_one(".pagination")
    more = False
    if pag:
        m = re.search(r"(\d+)\s*-\s*(\d+)\s*von\s*(\d+)", norm(pag.get_text(" ")))
        more = bool(m and int(m.group(2)) < int(m.group(3)))
    return cards, more


class Catalog:
    def __init__(self, http):
        self.http = http
        self.brand_urls = None
        self.cache = {}

    def _paged(self, url, params):
        out, seen = [], set()
        for page in range(1, 20):
            p = dict(params)
            p["resultsPerPage"] = 100
            if page > 1:
                p["page"] = page
            cards, more = parse_cards(self.http.get(url, p))
            for c in cards:
                if c["url"] not in seen:
                    seen.add(c["url"])
                    out.append(c)
            if not more:
                break
        return out

    def brand(self, brand):
        label = BRAND_PAGES.get(brand)
        if not label:
            return []
        if self.brand_urls is None:
            s = soup(self.http.get(BASE + LANG + "/hersteller"))
            self.brand_urls = {}
            for a in s.select("a[href]"):
                h = a["href"]
                if re.search(r"/de/\d+_[^/]+$", h):
                    self.brand_urls.setdefault(norm(a.get_text(" ")).lower(), h)
        url = self.brand_urls.get(label.lower())
        if not url:
            return []
        key = "brand:" + url
        if key not in self.cache:
            self.cache[key] = self._paged(url, {})
        return self.cache[key]

    def search(self, q):
        key = "q:" + q
        if key not in self.cache:
            self.cache[key] = self._paged(BASE + LANG + "/suche", {"controller": "search", "s": q})
        return self.cache[key]


# --------------------------------------------------------------------------- termékoldal
def product_json(body):
    s = soup(body)
    el = s.select_one("#product-details[data-product]")
    if not el:
        return s, None
    try:
        return s, json.loads(el["data-product"])
    except Exception:
        return s, None


def variant_groups(s):
    """-> [(group_id, [(value, label)])] a változatválasztóból."""
    groups = []
    for sel in s.select(".product-variants select[name^='group[']"):
        gid = re.search(r"group\[(\d+)\]", sel["name"]).group(1)
        opts = [(o.get("value"), norm(o.get("title") or o.get_text(" "))) for o in sel.select("option")]
        groups.append((gid, opts))
    if not groups:
        seen = {}
        for inp in s.select(".product-variants input[name^='group[']"):
            gid = re.search(r"group\[(\d+)\]", inp["name"]).group(1)
            lab = inp.get("title") or ""
            if not lab:
                sp = inp.find_next("span")
                lab = sp.get_text(" ") if sp else inp.get("value")
            seen.setdefault(gid, []).append((inp.get("value"), norm(lab)))
        groups = list(seen.items())
    return groups


def variant_url(d):
    link = d.get("link") or ""
    ipa = int(d.get("id_product_attribute") or 0)
    if ipa:
        return re.sub(r"/(%s)-" % d.get("id_product"), r"/\g<1>-%d-" % ipa, link, count=1)
    return link


def detail(d):
    price = money(d.get("price_amount"))
    lst = money(d.get("price_without_reduction")) if d.get("has_discount") else None
    if lst is not None and price is not None and abs(lst - price) < 0.005:
        lst = None
    avail = d.get("availability")
    qty = d.get("quantity")
    # PrestaShop: 'available' akkor is, ha 0 db, de rendelhető ("Wird bestellt") -> a darabszám dönt
    if qty is not None:
        in_stock = int(qty) > 0
    elif avail in ("available", "last_remaining_items"):
        in_stock = True
    elif avail == "unavailable":
        in_stock = False
    else:
        in_stock = None
    attrs = d.get("attributes") or {}
    mpn = next((a.get("isbn") for a in attrs.values() if a.get("isbn")), None)
    ean = next((a.get("ean13") for a in attrs.values() if a.get("ean13")), None)
    img = None
    cov = d.get("cover") or {}
    if cov.get("bySize", {}).get("large_default"):
        img = cov["bySize"]["large_default"]["url"]
    elif d.get("images"):
        im = d["images"][0]
        img = (im.get("bySize", {}).get("large_default") or {}).get("url") or (im.get("large") or {}).get("url")
    weight = None
    for f in d.get("features") or []:
        if re.search(r"gewicht", f.get("name") or "", re.I):
            m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*(kg|g)\b", f.get("value") or "")
            if m:
                v = float(m.group(1).replace(",", "."))
                weight = int(round(v * 1000)) if m.group(2) == "kg" else int(round(v))
    return dict(price_chf=price, list_chf=lst, in_stock=in_stock,
                stock_text=norm(d.get("availability_message") or ""), stock_qty=qty,
                sku=d.get("reference") or None, mpn=mpn or None, ean=ean, image=img, weight_g=weight,
                url=variant_url(d), title=norm(d.get("name") or ""),
                attrs={a.get("group", ""): a.get("name") for a in attrs.values()})


def scrape_product(http, url):
    """-> [variáns dict] (a színválasztó minden értékével, max. 24 kombináció)."""
    s, d = product_json(http.get(url))
    if not d:
        raise RuntimeError("nincs data-product JSON: " + url)
    groups = variant_groups(s)
    base_url = d.get("link") or url
    if not groups:
        v = detail(d)
        v["options"] = {}
        return [v]
    out = []
    labels = {g: dict(opts) for g, opts in groups}
    for combo in list(itertools.product(*[[(g, v) for v, _ in opts] for g, opts in groups]))[:24]:
        params = {"group[%s]" % g: v for g, v in combo}
        _, dv = product_json(http.get(base_url, params))
        if not dv:
            continue
        got = {str(a.get("id_attribute_group")): str(a.get("id_attribute"))
               for a in (dv.get("attributes") or {}).values()}
        if any(got.get(g) != v for g, v in combo):
            continue  # nem létező kombináció -> a bolt másik változatot adott vissza
        v = detail(dv)
        v["options"] = {g: labels[g].get(val) for g, val in combo}
        out.append(v)
    return out


# --------------------------------------------------------------------------- döntés
def candidates(cat, entry, rule):
    cards, seen = [], set()
    lists = [cat.brand(entry["brand"])] + [cat.search(q) for q in rule["q"]]
    for lst in lists:
        for c in lst:
            if c["url"] not in seen:
                seen.add(c["url"])
                cards.append(c)
    return cards


def fmt_near(c):
    return "%s%s (%s%s CHF, %s)" % (c["name"], (" – " + c["sub"]) if c["sub"] else "", "ab " if c["ab"] else "",
                                    ("%.2f" % c["price"]) if c["price"] is not None else "?", c["url"])


def decide(http, cat, entry, rule):
    cards = candidates(cat, entry, rule)
    if rule["title"]:
        hits = [c for c in cards if re.fullmatch(rule["title"], c["name"], re.I)
                and (not rule["sub"] or re.search(rule["sub"], c["sub"], re.I))]
    elif rule["title"] is None and rule["why"] is None and not rule["near"]:
        hits = [c for c in cards if slug(c["name"]) == slug(entry["brand"] + " " + entry["name"])]
    else:
        hits = []
    if not hits:
        reason = "ok: " + (rule["why"] or "nincs a boltban (nincs pontosan azonos termék)")
        near = [c for c in cards if rule["near"] and re.fullmatch(rule["near"], c["name"], re.I)]
        if near:
            reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(fmt_near(c) for c in near[:4])
        return "no", reason
    pal, is_base = entry["palette"], entry["is_base"]
    variants = []
    for c in hits:
        for v in scrape_product(http, c["url"]):
            col = None
            for g, lab in v["options"].items():
                col = col or color_key(lab, pal, is_base)
            for lab in v["attrs"].values():
                col = col or color_key(lab, pal, is_base)
            col = col or color_from_text(c["name"] + " " + c["sub"], pal, is_base)
            v["color"] = col
            v["card"] = c
            variants.append(v)
    if not variants:
        return "no", "ok: a termékoldal nem volt feldolgozható"
    # elsődleges: fekete (vagy az első palettaszín), különben a legolcsóbb
    prim = None
    for key in (pal or []):
        cand = [v for v in variants if v["color"] == key and v["price_chf"] is not None]
        if cand:
            prim = min(cand, key=lambda v: v["price_chf"])
            break
    if prim is None:
        prim = min(variants, key=lambda v: (v["price_chf"] is None, v["price_chf"] or 0))
    item = dict(title=prim["title"] + ((" – " + prim["card"]["sub"]) if prim["card"]["sub"] else ""),
                price_chf=prim["price_chf"], list_chf=prim["list_chf"], url=prim["url"],
                image=prim["image"], in_stock=prim["in_stock"], stock_text=prim["stock_text"],
                sku=prim["sku"], mpn=prim["mpn"], weight_g=prim["weight_g"], dims=None)
    if prim["stock_qty"] is not None:
        item["stock_qty"] = prim["stock_qty"]
    if prim["ean"]:
        item["ean"] = prim["ean"]
    if prim["color"]:
        item["color"] = prim["color"]
    cp, skipped = {}, []
    for v in variants:
        if not v["color"]:
            lab = " / ".join(x for x in v["options"].values() if x)
            if lab:
                skipped.append("%s (%.2f CHF)" % (lab, v["price_chf"] or 0))
            continue
        old = cp.get(v["color"])
        if old and old["price_chf"] is not None and v["price_chf"] is not None and old["price_chf"] <= v["price_chf"]:
            continue
        cp[v["color"]] = dict(price_chf=v["price_chf"], list_chf=v["list_chf"], url=v["url"],
                              in_stock=v["in_stock"], title=v["title"] + ((" – " + v["card"]["sub"]) if v["card"]["sub"] else ""),
                              stock_text=v["stock_text"], sku=v["sku"], mpn=v["mpn"], image=v["image"])
    if cp:
        item["colors_prices"] = {k: cp[k] for k in (pal or []) if k in cp}
    if skipped:
        item["colors_not_in_palette"] = sorted(set(skipped))
    titles = sorted(set(v["title"] for v in variants))
    item["match"] = "exact"
    if rule["note"]:
        item["note"] = rule["note"]
    if len(titles) > 1:
        item["shop_products"] = [dict(title=c["name"], sub=c["sub"], url=c["url"], price_chf=c["price"]) for c in hits]
    return "ok", item


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/indoorswiss.json; --only mellett stdout)")
    ap.add_argument("--cache", help="HTTP-gyorsítótár könyvtár (fejlesztéshez)")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["id"] in wanted]
    http = Http(args.cache)
    cat = Catalog(http)
    items, unmatched, blocked = {}, {}, None
    for i, e in enumerate(entries, 1):
        rule = RULES.get(e["id"]) or R(None, None)
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), e["id"], e["brand"], e["name"]))
        try:
            kind, res = decide(http, cat, e, rule)
        except Blocked as b:
            blocked = str(b)
            sys.stderr.write("   BLOKKOLVA: %s – leállás\n" % b)
            break
        except Exception as ex:
            unmatched[e["id"]] = "ok: hiba a lekérdezéskor, nem sikerült ellenőrizni (%s)" % ex
            sys.stderr.write("   HIBA: %s\n" % ex)
            continue
        if kind == "ok":
            items[e["id"]] = res
            sys.stderr.write("   -> %s  CHF %s  %s\n" % (res["title"], res["price_chf"], sorted(res.get("colors_prices", {}))))
        else:
            unmatched[e["id"]] = res
            sys.stderr.write("   -> unmatched\n")
    if blocked:
        items, unmatched = {}, {e["id"]: "ok: a bolt botvédelmet / tiltást adott, a gyűjtés leállt" for e in entries}
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
               items=items, unmatched=unmatched, blocked=blocked)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    out = args.out or (None if args.only else OUT_JSON)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("Kész: %s  (%d egyezés, %d kimaradt, %d HTTP kérés)\n" % (
            out, len(items), len(unmatched), http.n))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
