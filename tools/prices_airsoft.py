#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
airsoft.ch (TM Trading GmbH, Cham ZG; Shopware 6) ár-gyűjtő az AR-15 konfigurátorhoz.

FIGYELEM: az airsoft.ch túlnyomórészt airsoft-bolt. Csak a VALÓDI lőfegyver-tartozék / eredeti
márkás optika számít; airsoft-replika (más márkanév, pl. 'PTS', 'Aim-O', 'Element', vagy a névben
AEG/GBB/BB/Airsoft) SOHA nem egyezés. A márkát a bolt saját márkamezője (manufacturer) dönti el.

Futtatás (a repo gyökeréből):
    python3 tools/prices_airsoft.py                       # minden termék -> assets/prices/airsoft.json
    python3 tools/prices_airsoft.py --only vx-crossfire,hs-aems   # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_airsoft.py --cache /tmp/ascache   # HTTP-gyorsítótár (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés (a tools/prices_armamat.py mintájára – a két bolt ugyanazt a Shopware-sablont használja)
  1. A js/data.js-ből kiolvassa a BASES + ITEMS termékeket (id, márka, név, színpaletta).
  2. id-nként a RULES keresőkifejezéseivel a bolt AJAX-listázóját kérdezi:
     /de-ch/widgets/search?search=<q>&order=score&p=1 (40 találat, relevancia szerint).
  3. Egyezés: a bolti márka (fullmatch) + a bolti terméknév PONTOS (fullmatch) mintája.
  4. Találat -> termékoldal (JSON-LD, ár-doboz, 'Lagerstatus', Artikelnummer / Herstellernummer,
     méret / súly); a színváltozatokat a bolt saját /de-ch/detail/<id>/switch végpontja adja
     (változatonként ár, készlet, kép, URL). A színnevek a data.js palettakulcsaira képződnek le.
  5. Nincs pontos egyezés -> 'unmatched' okkal (+ a legközelebbi bolti termék(ek) nevével, árával).
     Árat soha nem becsül, nem számol, nem vált át.

Udvariasság: egy szálon, kérések között >= 1,1 s, böngésző User-Agent; 429/5xx esetén visszalépés.
403 / Cloudflare-kihívás / captcha esetén AZONNAL leáll ('blocked'), nem próbálja megkerülni.
Csak a www.airsoft.ch hostra kérdez (a képeknél – res.cloudinary.com – csak az URL-t rögzíti).
"""
import argparse
import datetime
import hashlib
import html as htmllib
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

BASE = "https://www.airsoft.ch"
LANG = "/de-ch"
HOSTS = ("www.airsoft.ch", "airsoft.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.1

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "airsoft.json")

# airsoft-replika / nem lőfegyver-tartozék jelei a terméknévben
AIRSOFT_RE = re.compile(r"\b(airsoft|AEG|GBBR?|HPA|BBs?|6 ?mm|PTS|replica|replika|dummy|mock[- ]?up|"
                        r"spring|gas blow ?back|co2)\b", re.I)


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

    def get(self, url, params=None, ajax=False):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        host = urllib.parse.urlparse(url).netloc
        if host not in HOSTS:
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, "rb") as f:
                    return f.read().decode("utf-8", "replace")
        headers = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5"}
        if ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"
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
    m = re.search(r"([0-9][0-9’'’ ]*(?:\.[0-9]{1,2})?)", norm(text))
    return float(re.sub(r"[’'’ ]", "", m.group(1))) if m else None


def parse_price_block(el):
    if el is None:
        return None, None
    lp = el.select_one(".list-price-price")
    list_price = parse_chf(lp.get_text(" ")) if lp else None
    txt = norm(el.get_text(" "))
    if lp:
        txt = txt.replace(norm(lp.get_text(" ")), " ", 1)
    return parse_chf(txt), list_price


def kg_to_g(txt):
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*kg", txt)
    return int(round(float(m.group(1).replace(",", ".")) * 1000)) if m else None


# --------------------------------------------------------------------------- data.js
def read_data_js(path=DATA_JS):
    """-> [dict(id, cat, brand, name, palette, is_base)] a BASES + ITEMS literál objektumaiból."""
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
            continue
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
    (r"dark earth", "fde"),      # a bolt a Magpul FDE-t 'Dark Earth' néven listázza
    (r"od|od ?green|odg|olive drab|oliv(e|grün)?", "odg"),
    (r"stealth gr[ae]y|gr[ae]y|grau", "gray"),
    (r"wolf gr[ae]y", "wolfgray"),
    (r"foliage( green)?", "foliage"),
    (r"ranger green|ranger", "ranger"),
    (r"coyote( brown)?", "coyote"),
    (r"bronze", "bronze"),
]


def color_key(label, palette, is_base=False):
    if not label or not palette:
        return None
    lab = norm(label).lower()
    for pat, key in COLOR_WORDS:
        if re.fullmatch(pat, lab):
            if is_base:
                key = {"odg": "olive", "fde": "fde" if "fde" in palette else "earth"}.get(key, key)
            return key if key in palette else None
    return None


# --------------------------------------------------------------------------- szabályok
def R(q, brand=None, title=None, near=None, why=None, note=None, mpn=None):
    """q: keresőkifejezés(ek); brand: bolti márka (regex, fullmatch);
    title: bolti terméknév (regex, fullmatch) – None: a bolt biztosan nem árulja ugyanezt;
    near: regex a tájékoztató 'legközelebbi' termékekhez; why: indok; note: megjegyzés;
    mpn: ha meg van adva, a termékoldal gyártói cikkszámának is erre kell illeszkednie (re.match)."""
    return dict(q=[q] if isinstance(q, str) else q, brand=brand, title=title, near=near, why=why, note=note,
                mpn=mpn)


MP, BCM, B5, GS, RD = "Magpul", "BCM", "B5 Systems", "Geissele( Automatics)?", "Radian( Weapons)?"
VX, EO, AP, HS, TJ, SF, SL = "Vortex( Optics)?", "EoTech", "Aimpoint", "Holosun", "Trijicon", "SureFire", "Streamlight"

NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a márkát/terméket)"
NOT_FOUND = "nincs a boltban (a keresés nem adott pontosan azonos terméket)"
FIREARM = "alapfegyver (lőfegyver): az airsoft.ch nem árul lőfegyvert"
SET_BUIS = "szett (első + hátsó irányzék): a bolt az elsőt és a hátsót külön termékként árulja, szett nincs"

RULES = {
    # ---- alapfegyverek
    "schm-s4f": R("Schmeisser AR15 S4F", "Schmeisser", None, why=FIREARM),
    "schm-m4fl": R("Schmeisser AR15 M4FL", "Schmeisser", None, why=FIREARM),
    "schm-m5fl": R("Schmeisser AR15 M5FL", "Schmeisser", None, why=FIREARM),
    "schm-dyn": R("Schmeisser AR15 Dynamic", "Schmeisser", None, why=FIREARM),
    "dd-v7": R("Daniel Defense DDM4 V7", "Daniel Defense", None, why=FIREARM),
    "bcm-recce16": R("BCM RECCE-16", BCM, None, why=FIREARM),
    # ---- markolatok
    "mp-moe": R("Magpul MOE Grip", MP, r"MOE Grip", near=r"MOE .*Grip", why=NOT_FOUND),
    "mp-moek2": R("Magpul MOE K2 Grip", MP, r"MOE[ -]K2 Grip", why=NOT_FOUND),
    "mp-moek2p": R("Magpul MOE K2+ Grip", MP, r"MOE[ -]K2\+ Grip", why=NOT_FOUND),
    "mp-miad": R("Magpul MIAD Grip", MP, r"MIAD 5\.56 Grip Gen 1\.1 Type 1", why=NOT_FOUND),
    "bcm-g0": R("BCM Grip Mod 0", BCM, r"(Gunfighter )?Grip Mod 0", near=r".*Grip.*", why=NOT_FOUND),
    "bcm-g3": R("BCM Grip Mod 3", BCM, r"(Gunfighter )?Grip Mod 3", near=r".*Grip.*", why=NOT_FOUND),
    "b5-t22": R("B5 Systems Type 22 P-Grip", B5, r"(Type 22 )?P-Grip.*", why=NOT_SOLD),
    "b5-t23": R("B5 Systems Type 23 P-Grip", B5, r"(Type 23 )?P-Grip.*", why=NOT_SOLD),
    # ---- tusok
    "mp-ctr": R("Magpul CTR Carbine Stock", MP, r"CTR Carbine Stock( Mil[- ]Spec)?",
                note="Mil-Spec csöves kivitel (a bolt csak ezt árulja)"),
    "mp-moesl": R("Magpul MOE SL Carbine Stock", MP, r"MOE SL Carbine Stock( Mil[- ]Spec)?",
                  note="Mil-Spec csöves kivitel (a bolt csak ezt árulja)"),
    "mp-moeslk": R("Magpul MOE SL-K Carbine Stock", MP, r"MOE SL-K Carbine Stock( Mil[- ]Spec)?",
                   note="Mil-Spec csöves kivitel (a bolt csak ezt árulja)"),
    "mp-ubr2": R("Magpul UBR Gen 2", MP, r"UBR Gen 2(\.0)? Collapsible Stock", why=NOT_FOUND),
    "mp-prs3": R("Magpul PRS Gen 3", MP, r"PRS Gen ?3 Rifle Stock( Mil[- ]Spec)?", near=r"PRS .*",
                 why=NOT_FOUND),
    "mp-prslite": R("Magpul PRS Lite", MP, r"PRS Lite( Stock)?", why=NOT_FOUND),
    "bcm-s0": R("BCM Gunfighter Stock Mod 0", BCM, r"(Gunfighter )?Stock Mod 0", why=NOT_FOUND),
    "bcm-s1": R("BCM Gunfighter Stock Mod 1 SOPMOD", BCM, r"(Gunfighter )?Stock Mod 1.*", why=NOT_FOUND),
    "b5-bravo": R("B5 Systems Bravo Stock", B5, r"Bravo Stock", why=NOT_SOLD),
    "b5-esop": R("B5 Systems Enhanced SOPMOD Stock", B5, r".*SOPMOD.*", why=NOT_SOLD),
    "b5-prec": R("B5 Systems Precision Stock", B5, r"Precision Stock", why=NOT_SOLD),
    # ---- tustalpak
    "mp-pad030": R("Magpul Rubber Butt-Pad", MP, r"Rubber Butt-?Pad", near=r".*Butt-?pad.*"),
    "mp-pad055": R("Magpul Extended Rubber Butt-Pad", MP, r"Extended Rubber Butt-?pad", near=r".*Butt-?pad.*"),
    "mp-pad070": R("Magpul Enhanced Rubber Butt-Pad", MP, r"Enhanced Rubber Butt-?Pad", near=r".*Butt-?pad.*"),
    "mp-padsl": R("Magpul MOE SL Enhanced Rubber Butt-Pad", MP,
                  r"MOE SL Enhanced Rubber Butt-Pad 0\.70 Inch(es)?"),
    # ---- red dot / holo
    "ap-t2": R("Aimpoint Micro T-2", AP, None, near=r"Micro T-2 .*",
               why="csomag (T-2 + LRP szerelék): a bolt a T-2-t külön és két különböző (30 / 39 mm távtartós) "
                   "LRP-csomagban árulja; a data.js 'Lower 1/3' specje alapján valószínűleg a 39 mm-es csomag "
                   "felel meg, de a bolti oldal ezt nem írja le, ezért nem azonosítható biztosan"),
    "ap-m5": R("Aimpoint Comp M5", AP, None, near=r"Comp M5 .*|LRP Mount.*",
               why="csomag (CompM5 + LRP szerelék): a bolt 'with Mount' (a szerelék típusát nem nevezi meg) és "
                   "'without Mount' változatot árul, az LRP külön termék"),
    "ap-pro": R(["Aimpoint PRO", "Aimpoint Patrol Rifle Optic"], AP, None, near=r"PRO\b.*",
                why="csomag (PRO + QRP2 szerelék): a bolti 'PRO 2 MOA' oldala nem írja le a mellékelt szereléket"),
    "ap-acro": R("Aimpoint ACRO P-2", AP, None, near=r"ACRO P-2.*|ACRO Mount.*",
                 why="csomag (ACRO P-2 + Picatinny talp): a bolt az ACRO P-2-t és az ACRO Mount talpakat külön árulja"),
    "eo-exps3": R("EOTech EXPS3", EO, r"EXPS3-0", near=r"EXPS.*", why=NOT_FOUND),
    "eo-exps2": R("EOTech EXPS2", EO, r"EXPS2-0"),
    "eo-xps2": R("EOTech XPS2", EO, r"XPS2-0", note="piros retikulos alapváltozat (a 'XPS2-0 - Green Reticle' külön termék)"),
    "hs-510c": R("Holosun HS510C", HS, r"HS510C Solar Red Circle Dot Sight",
                 note="önálló irányzék (a 'Combo with HM3X' külön termék)"),
    "hs-aems": R("Holosun AEMS", HS, r"AEMS Red Circle Dot Sight", near=r"AEMS .*",
                 note="az eredeti AEMS (AEMS-RD, MPN 211301); a leírás szerint napelemes, Shake Awake, "
                      "a csomagban 1/3 co-witness szerelékkel"),
    "hs-403b": R(["Holosun HS403B", "Holosun 403B"], HS, None, near=r"H[SE]403.*",
                 why="a HS403B nincs a boltban (csak HE403B-GR Elite zöld pontos és HS403C Solar – más modellek)"),
    "tj-mro": R("Trijicon MRO", TJ, r"MRO.*", why=NOT_SOLD),
    "vx-uh1": R("Vortex UH-1 Gen II", VX, r"AMG UH-1 Gen II Holographic Sight", why=NOT_FOUND),
    "vx-sparc": R("Vortex SPARC AR", VX, None, near=r"SPARC .*",
                  why="a 'SPARC AR' nincs a boltban, csak a 'SPARC Solar' (más modell)"),
    "vx-crossfire": R("Vortex Crossfire Red Dot", VX, r"Crossfire Red Dot Sight 2 MOA",
                      near=r"Crossfire (II )?(2 MOA )?(Solar )?(Red|Green) Dot Sight( 2 MOA)?",
                      note="a bolti leírás szerint gyárilag alacsony Picatinny-talppal, a csomagban a 1/3 "
                           "co-witness magasítóval (= 'Crossfire Red Dot + magasító')"),
    # ---- LPVO
    "vx-se16": R("Vortex Strike Eagle 1-6x24", VX, r"Strike Eagle 1-6x24 AR-BDC3",
                 note="AR-BDC3 retikulos kivitel (a bolt csak ezt árulja)"),
    "vx-se18": R("Vortex Strike Eagle 1-8x24", VX, r"Strike Eagle 1-8x24 AR-BDC3",
                 note="AR-BDC3 retikulos SFP kivitel; az FFP EBR-8 külön termék"),
    "vx-pst16": R("Vortex Viper PST Gen II 1-6x24", VX, r"Viper PST Gen II 1-6x24 SFP VMR-2 (MOA|MRAD)",
                  why=NOT_FOUND, note="a bolt két retikulos (MOA/MRAD) kivitelt árulhat; a data.js nem határozza meg"),
    "vx-razor110": R("Vortex Razor Gen III 1-10x24", VX, r"Razor (HD )?Gen III 1-10x24 .*", why=NOT_FOUND),
    "tj-acog": R("Trijicon ACOG TA31", TJ, r"(ACOG )?TA31.*", why=NOT_SOLD),
    "tj-credo16": R("Trijicon Credo", TJ, r"Credo.*", why=NOT_SOLD),
    "tj-vcog": R("Trijicon VCOG", TJ, r"VCOG.*", why=NOT_SOLD),
    "eo-vudu16": R("EOTech Vudu 1-6x24", EO, r"Vudu 1-6x24 FFP - .*", near=r"Vudu .*1-6x24.*",
                   why="a 'Vudu 1-6x24 FFP' nincs a boltban; a 'Vudu X 1-6x24 SFP' másik modell",
                   note="FFP kivitel"),
    "eo-vudu110": R("EOTech Vudu 1-10x28", EO, r"Vudu 1-10x28 FFP - .*", why=NOT_FOUND),
    # ---- optikaszerelék
    "gs-sp30": R(["Geissele Super Precision", "Geissele Scope Mount"], GS, r"Super Precision.*", why=NOT_SOLD),
    "vx-cant30": R("Vortex Sport Cantilever 30mm", VX, r"Sport Cantilever Mount 30mm 1\.5 Inch Height"),
    # ---- nagyítók
    "eo-g33": R("EOTech G33", EO, r"G33\.STS Magnifier"),
    "eo-g43": R("EOTech G43", EO, None, near=r"G43.*",
                why="G43 + 'Micro' szerelék csomag: a bolt csak a G43.STS (STS szerelékkel) változatot árulja"),
    "ap-3x": R("Aimpoint 3XMag", AP, None, near=r"3XMag-1|Flip Mount 30mm|Flip Mount 39mm.*",
               why="csomag (3XMag-1 + Flip szerelék): a bolt a nagyítót és a Flip szerelékeket külön árulja"),
    "hs-hm3x": R("Holosun HM3X", HS, r"HM3X Magnifier",
                 note="a 'HM3X Micro' és a 'HM3XT Elite' más modellek"),
    "vx-m3x": R(["Vortex Micro 3X Magnifier", "Vortex Magnifier"], VX, None, near=r"(V3XM|Micro 3X).*",
                why="a 'Micro3X + Flip' nincs a boltban"),
    # ---- BUIS
    "mp-mbus3": R("Magpul MBUS 3", MP, None, near=r"MBUS 3 (Front|Rear) .*", why=SET_BUIS),
    "mp-mbuspro": R("Magpul MBUS Pro", MP, None, near=r"MBUS Pro Sight (Front|Rear)", why=SET_BUIS),
    "mp-mbusoff": R("Magpul MBUS Pro Offset", MP, None, near=r"MBUS Pro Offset Sight (Front|Rear)", why=SET_BUIS),
    # ---- lámpák
    "sf-m600": R("SureFire M600", SF, r"M600.*", why=NOT_SOLD),
    "sf-m640": R("SureFire M640", SF, r"M640.*", why=NOT_SOLD),
    "sf-m300": R("SureFire M300", SF, r"M300.*", why=NOT_SOLD),
    "sf-x300": R("SureFire X300", SF, r"X300.*", why=NOT_SOLD),
    "sl-hlx": R("Streamlight ProTac Rail Mount HL-X", SL, None, near=r"ProTac Rail Mount HL[ -]X.*",
                why="a 'ProTac Rail Mount HL-X' lámpa nincs a boltban (csak a 'HL X Pro Jack-Cap' tartozék)"),
    "sl-tlr1": R("Streamlight TLR-1 HL", SL, r"TLR-1 HL", near=r"TLR-1.*", why=NOT_FOUND),
    # ---- M-LOK
    "mp-rvg": R("Magpul RVG", MP, None, near=r"(MOE )?RVG .*",
                why="az 'M-LOK RVG' függőleges markolat nincs a boltban; a 'MOE RVG Grip' (MOE-sínes) és az "
                    "'RVG M-LOK Adapter Rail' nem azonos"),
    "mp-hstop": R("Magpul M-LOK Hand Stop", MP, r"M-LOK Hand Stop Kit"),
    "bcm-vg3": R("BCM Vertical Grip", BCM, None, near=r"Vertical Grip.*",
                 why="a Gunfighter Vertical Grip Mod 3 nem azonosítható a bolti 'Vertical Grip - Short' termékkel"),
    "bcm-kag": R("BCM KAG", BCM, None, near=r".*KAG.*", why="a KAG nincs a boltban"),
    "mp-rail5": R("Magpul M-LOK Rail Section", MP, r"M-LOK Rail Section Alumini?um 5 Slots"),
    "mp-qd": R("Magpul M-LOK QD", MP, r"M-LOK QD Sling Mount"),
    "mp-cover": R("Magpul M-LOK Rail Cover", MP, r"M-LOK Rail Cover Type 2"),
    "mp-cable": R("Magpul M-LOK Cable", MP, r"M-LOK Cable.*", why=NOT_FOUND),
    # ---- bipod
    "mp-bipod": R(["Magpul Bipod", "Magpul Aluminum M-LOK Bipod"], MP, r"Aluminum M-LOK 6\.8-10\.3 Inch Bipod",
                  near=r"(Aluminum .*Bipod|MOE QD Polymer Bipod for M-LOK|MOE 7\.0-10\.0 Inch Bipod)",
                  why="a Magpul Bipod M-LOK (MAG933) nincs a boltban", mpn=r"MAG933",
                  note="gyártói cikkszám MAG933 = Magpul 'Bipod for M-LOK' (a data.js modellje); a bolt a lábhosszt "
                       "6,8–10,3\"-ként (173–262 mm) adja meg. A 'MOE QD Polymer' és a 'MOE 7–10\"' bipod más termék."),
    "hr-sbrm": R("Harris S-BRM", "Harris.*", r".*BRM.*", why=NOT_SOLD),
    # ---- torkolat
    "sf-warcomp": R("SureFire WarComp", SF, r".*WarComp.*", why=NOT_SOLD),
    "sf-3p": R("SureFire SF3P", SF, r".*SF3P.*", why=NOT_SOLD),
    "bcm-comp0": R("BCM Compensator", BCM, r".*Comp.*", why=NOT_SOLD),
    "sf-rc2": R("SureFire SOCOM556-RC2", SF, r".*RC2.*", why=NOT_SOLD),
    # ---- elsütők
    "gs-ssae": R("Geissele SSA-E", GS, r".*SSA-E.*", why=NOT_SOLD),
    "gs-ssa": R("Geissele SSA", GS, r".*\bSSA\b.*", why=NOT_SOLD),
    "gs-sde": R("Geissele SD-E", GS, r".*SD-E.*", why=NOT_SOLD),
    "gs-s3g": R("Geissele S3G", GS, r".*(S3G|Super 3 Gun).*", why=NOT_SOLD),
    # ---- felhúzókar
    "rd-raptor": R("Radian Raptor", RD, r"Raptor Ambidextrous Charging Handle AR15", why=NOT_SOLD),
    "rd-raptorlt": R("Radian Raptor-LT", RD, r"Raptor-LT Ambidextrous Charging Handle for AR15", why=NOT_SOLD),
    "rd-raptorsd": R("Radian Raptor-SD", RD, r"Raptor-SD.*", why=NOT_SOLD),
    "bcm-ch4": R("BCM Charging Handle Mod 4", BCM, r".*Charging Handle.*", why=NOT_SOLD),
    "bcm-ch3": R("BCM Charging Handle Mod 3", BCM, r".*Charging Handle.*", why=NOT_SOLD),
    "gs-ach": R("Geissele Airborne Charging Handle", GS, r".*Airborne.*", why=NOT_SOLD),
    # ---- kezelőszervek
    "rd-talon": R("Radian Talon", RD, r"Talon Ambidextrous 45/90 Safety Selector", why=NOT_SOLD),
    "mp-bad": R("Magpul B.A.D. Lever", MP, r"B\.A\.D\. Lever", why=NOT_FOUND),
    "mp-trigguard": R("Magpul Enhanced Trigger Guard", MP, r"MOE Enhanced Trigger Guard", near=r".*Trigger Guard.*",
                      why=NOT_FOUND),
    # ---- tárak (valódi PMAG; az airsoft-tárak más márkájúak)
    "mp-pm10": R("Magpul PMAG 10 Gen M3", MP, r"PMAG 10 Gen M3", why=NOT_FOUND),
    "mp-pm20": R("Magpul PMAG 20 Gen M3", MP, r"PMAG 20 Gen M3", why=NOT_FOUND),
    "mp-pm30": R("Magpul PMAG 30 Gen M3", MP, r"PMAG 30 Gen M3", why=NOT_FOUND),
    "mp-pm30w": R("Magpul PMAG 30 Gen M3 Window", MP, r"PMAG 30 Gen M3 Window", why=NOT_FOUND),
    "mp-pm40": R("Magpul PMAG 40 Gen M3", MP, r"PMAG 40 Gen M3", why=NOT_FOUND),
    "mp-d60": R("Magpul D-60", MP, r"(PMAG )?D-60.*", why=NOT_FOUND),
    # ---- szíjak
    "mp-ms1": R("Magpul MS1 Sling", MP, r"MS1 Sling", near=r"MS1 .*Sling"),
    "mp-ms4": R("Magpul MS4 Dual QD", MP, r"MS4 Dual QD( Gen 2)? Sling", note="a bolt csak a Gen 2 kivitelt árulja"),
    "bfg-vickers": R("Blue Force Gear Vickers", "Blue Force Gear", r"Vickers Combat Applications? Sling",
                     near=r"Vickers .*|.*Sling.*", why=NOT_FOUND),
}


# --------------------------------------------------------------------------- keresés
def search_cards(http, query):
    body = http.get(BASE + LANG + "/widgets/search", {"search": query, "order": "score", "p": 1}, ajax=True)
    s = soup(body)
    cards = []
    for b in s.select(".product-box"):
        a = b.select_one("a.product-name")
        if not a or not a.get("href"):
            continue
        mf = b.select_one(".manufacturer-name")
        pel = b.select_one(".product-price")
        price, list_price = parse_price_block(pel)
        cards.append(dict(brand=norm(mf.get_text(" ")) if mf else "",
                          name=norm(a.get("title") or a.get_text(" ")),
                          url=a["href"], price_from=price, list_from=list_price,
                          ab=bool(pel and norm(pel.get_text(" ")).lower().startswith("ab "))))
    return cards


# --------------------------------------------------------------------------- termékoldal
def parse_stock(s_el):
    de = s_el.select_one(".product-detail-delivery-information")
    txt = norm(de.get_text(" ")) if de else ""
    if not txt:
        # nem rendelhető cikk: 'Verfügbarkeit: Ausverkauft / Mitte November wieder lieferbar – Dieser Artikel
        # ist im Moment nicht lieferbar.' (nincs szállítási-idő doboz)
        buy = s_el.select_one(".product-detail-buy") or s_el
        m = re.search(r"Verfügbarkeit:\s*(.+?)\s*(?:Dieser Artikel ist im Moment nicht lieferbar|Ja, mich|Farbe:|CHF)",
                      norm(buy.get_text(" ")))
        if m:
            t = m.group(1)
            if "nicht lieferbar" in norm(buy.get_text(" ")):
                t += " (Dieser Artikel ist im Moment nicht lieferbar.)"
            return False, t, None
    qty = None
    m = re.search(r"Lagerstatus:\s*(\d+)\s*St", txt, re.I)
    if m:
        qty = int(m.group(1))
    if not txt:
        return None, "", qty
    low = txt.lower()
    if qty and qty > 0:
        return True, txt, qty
    if "lagernd" in low or "ab lager" in low:
        return True, txt, qty
    return False, txt, qty


def parse_detail(s_el):
    box = s_el.select_one(".product-detail-price-container") or s_el.select_one(".product-detail-price")
    price, list_price = parse_price_block(box)
    in_stock, stock_text, qty = parse_stock(s_el)
    text = norm(s_el.get_text(" "))
    out = dict(price_chf=price, list_chf=list_price, in_stock=in_stock, stock_text=stock_text, stock_qty=qty)
    m = re.search(r"Artikelnummer:\s*([\w.\-]+)", text)
    out["sku"] = m.group(1) if m else None
    m = re.search(r"Herstellernummer:\s*(.+?)\s+(?:Verfügbarkeit:|Farbe:|CHF|Lagerstatus|Lagernd|Artikelnummer)", text)
    out["mpn"] = m.group(1).strip() if m else None
    names = {"L": "Länge", "B": "Breite", "H": "Höhe"}
    dims = {}
    for key, de in names.items():
        mm = re.search(de + r":\s*([0-9.,]+)\s*cm", text)
        if mm:
            dims[key] = mm.group(1)
    out["dims"] = " x ".join("%s %s cm" % (names[k], dims[k]) for k in "LBH" if k in dims) or None
    mm = re.search(r"Gewicht:\s*([0-9.,]+\s*kg)", text)
    out["weight_g"] = kg_to_g(mm.group(1)) if mm else None
    imgs = []
    for a in s_el.select("a.gallery-slider-item[href]"):
        h = a["href"]
        if h.startswith("http") and h not in imgs:
            imgs.append(h)
    out["image"] = imgs[0] if imgs else None
    sel = {}
    for t in s_el.select(".product-detail-configurator-group-title"):
        tx = norm(t.get_text(" "))
        if ":" in tx:
            k, v = tx.split(":", 1)
            sel[norm(k)] = norm(v)
    out["selected"] = sel
    return out


def ldjson(s_el):
    for t in s_el.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except Exception:
            continue
        d = d[0] if isinstance(d, list) and d else d
        if isinstance(d, dict) and d.get("@type") == "Product":
            return d
    return None


def configurator(s_el):
    form = s_el.select_one("form[data-variant-switch]")
    if not form:
        return None, []
    try:
        url = json.loads(htmllib.unescape(form.get("data-variant-switch-options") or "{}")).get("url")
    except Exception:
        url = None
    groups = []
    for g in form.select(".product-detail-configurator-group"):
        lab = g.select_one(".product-detail-configurator-group-title strong")
        label = norm(lab.get_text(" ")).rstrip(":") if lab else ""
        options = []
        for inp in g.select("input.product-detail-configurator-option-input"):
            lbl = g.find("label", attrs={"for": inp.get("id")})
            title = norm((lbl.get("title") if lbl else "") or (lbl.get_text(" ") if lbl else "") or inp.get("value"))
            options.append((inp.get("name"), inp.get("value"), title, inp.has_attr("checked")))
        if options:
            groups.append((options[0][0], label, options))
    return url, groups


def scrape_product(http, card):
    s_el = soup(http.get(card["url"]))
    ld = ldjson(s_el)
    base = parse_detail(s_el)
    switch_url, groups = configurator(s_el)
    variants = []
    if switch_url and groups:
        for combo in list(itertools.product(*[g[2] for g in groups]))[:24]:
            chosen = {opt[0]: opt[1] for opt in combo}
            label = {g[1]: opt[2] for g, opt in zip(groups, combo)}
            try:
                body = http.get(BASE + switch_url, {"options": json.dumps(chosen), "switched": list(chosen)[-1]},
                                ajax=True)
                d = json.loads(body)
            except Blocked:
                raise
            except Exception as e:
                sys.stderr.write("   változat kihagyva (%s): %s\n" % (label, e))
                continue
            det = parse_detail(soup(d.get("content", "")))
            det["url"] = d.get("url")
            det["color_label"] = next((v for k, v in label.items() if k.lower() in ("farbe", "color")), None)
            det["other_options"] = {k: v for k, v in label.items() if k.lower() not in ("farbe", "color")}
            variants.append(det)
    if not variants:
        base["url"] = card["url"]
        base["color_label"] = base["selected"].get("Farbe")
        base["other_options"] = {}
        variants = [base]
    return ld, base, variants


# --------------------------------------------------------------------------- döntés
def fmt_near(c):
    return "%s %s (%s%s CHF, %s)" % (c["brand"], c["name"], "ab " if c["ab"] else "",
                                      ("%.2f" % c["price_from"]) if c["price_from"] else "?", c["url"])


def decide(http, entry, rule, qcache):
    queries = rule["q"] if rule else ["%s %s" % (entry["brand"], entry["name"])]
    cards, seen = [], set()
    for q in queries:
        if q not in qcache:
            qcache[q] = search_cards(http, q)
        for c in qcache[q]:
            if c["url"] not in seen:
                seen.add(c["url"])
                cards.append(c)
    # airsoft-replikák kiszűrése (márka + névjelek)
    cards = [c for c in cards if not AIRSOFT_RE.search(c["name"])]
    if rule:
        bre, tre = rule["brand"], rule["title"]
        hits = [] if tre is None else [
            c for c in cards if (bre is None or re.fullmatch(bre, c["brand"], re.I)) and re.fullmatch(tre, c["name"], re.I)]
    else:
        hits = [c for c in cards if slug(c["brand"]) == slug(entry["brand"]) and slug(c["name"]) == slug(entry["name"])]
    if not hits:
        why = (rule or {}).get("why")
        near_re = (rule or {}).get("near")
        near = [c for c in cards if near_re and re.fullmatch(near_re, c["name"], re.I)
                and (rule["brand"] is None or re.fullmatch(rule["brand"], c["brand"], re.I))] if near_re else []
        reason = "ok: " + (why or NOT_FOUND)
        if near:
            reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(fmt_near(c) for c in near[:4])
        return "no", reason
    hits.sort(key=lambda c: (c["price_from"] is None, c["price_from"] or 0))
    main, alts = hits[0], hits[1:]
    ld, base, variants = scrape_product(http, main)
    if ld and ld.get("name") and slug(ld["name"]) != slug(main["name"]):
        return "no", "ok: bizonytalan egyezés: a találati kártya neve (%s) és az oldal neve (%s) eltér" % (
            main["name"], ld["name"])
    if ld and ld.get("name") and AIRSOFT_RE.search(ld.get("name", "")):
        return "no", "ok: airsoft-termék (nem lőfegyver-tartozék): %s" % ld["name"]
    if rule and rule.get("mpn"):
        mpns = [v["mpn"] for v in variants if v.get("mpn")] or [base.get("mpn")]
        if not any(m and re.match(rule["mpn"], m, re.I) for m in mpns):
            return "no", "ok: bizonytalan egyezés: a gyártói cikkszám (%s) nem a várt (%s)" % (
                ", ".join(m for m in mpns if m) or "nincs", rule["mpn"])
    pal, is_base = entry["palette"], entry["is_base"]
    for v in variants:
        v["color"] = color_key(v["color_label"], pal, is_base)
    prim = None
    for key in (pal or []):
        cand = [v for v in variants if v["color"] == key and v["price_chf"] is not None and not v["other_options"]]
        cand = cand or [v for v in variants if v["color"] == key and v["price_chf"] is not None]
        if cand:
            prim = min(cand, key=lambda v: v["price_chf"])
            break
    if prim is None:
        blk = [v for v in variants if (v["color_label"] or "").lower() in ("schwarz", "black")]
        prim = (blk or variants)[0]
    ld_img = None
    if ld:
        im = ld.get("image")
        ld_img = im[0] if isinstance(im, list) and im else (im if isinstance(im, str) else None)
    title = norm("%s %s" % (main["brand"], main["name"]))
    item = dict(title=title, price_chf=prim["price_chf"], list_chf=prim["list_chf"],
                url=prim["url"] or main["url"], image=prim["image"] or base["image"] or ld_img,
                in_stock=prim["in_stock"], stock_text=prim["stock_text"], sku=prim["sku"], mpn=prim["mpn"],
                weight_g=prim["weight_g"] if prim["weight_g"] is not None else base["weight_g"],
                dims=prim["dims"] or base["dims"])
    if prim["stock_qty"] is not None:
        item["stock_qty"] = prim["stock_qty"]
    if prim.get("color"):
        item["color"] = prim["color"]
    item["product_url"] = main["url"]
    cp, other = {}, []
    for v in variants:
        if v["color"] and not v["other_options"]:
            old = cp.get(v["color"])
            if old and old["price_chf"] is not None and v["price_chf"] is not None and old["price_chf"] <= v["price_chf"]:
                continue
            cp[v["color"]] = dict(price_chf=v["price_chf"], list_chf=v["list_chf"], url=v["url"],
                                  in_stock=v["in_stock"], title="%s (%s)" % (title, v["color_label"]),
                                  stock_text=v["stock_text"], sku=v["sku"], mpn=v["mpn"], image=v["image"])
        elif (v["color_label"] or v["other_options"]) and v is not prim:
            lab = " / ".join([x for x in [v["color_label"]] + list(v["other_options"].values()) if x])
            other.append("%s (%s CHF)" % (lab, ("%.2f" % v["price_chf"]) if v["price_chf"] is not None else "?"))
    if cp:
        item["colors_prices"] = {k: cp[k] for k in (pal or []) if k in cp}
    if other:
        item["variants_not_in_palette"] = sorted(set(other))
    if alts:
        item["alternatives"] = [dict(title=norm("%s %s" % (c["brand"], c["name"])), url=c["url"],
                                     price_from_chf=c["price_from"]) for c in alts]
        item["match"] = "variant"
    else:
        item["match"] = "exact"
    if rule and rule.get("note"):
        item["note"] = rule["note"]
    return "ok", item


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/airsoft.json; --only mellett stdout)")
    ap.add_argument("--cache", help="HTTP-gyorsítótár könyvtár (fejlesztéshez)")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["id"] in wanted]
    http = Http(args.cache)
    items, unmatched, qcache, blocked = {}, {}, {}, None
    for i, e in enumerate(entries, 1):
        rule = RULES.get(e["id"])
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), e["id"], e["brand"], e["name"]))
        try:
            kind, res = decide(http, e, rule, qcache)
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
    doc = dict(shop="airsoft.ch", currency="CHF", fetched=datetime.date.today().isoformat(),
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
