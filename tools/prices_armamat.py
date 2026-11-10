#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
armamat.ch ár- és műszaki adat-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_armamat.py                 # minden termék -> assets/prices/armamat.json
    python3 tools/prices_armamat.py --only mp-moe,mp-ctr   # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_armamat.py --cache /tmp/arcache   # HTTP-válaszok gyorsítótára (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van). Más csomag nem kell.

Működés
  1. A js/data.js-ből kiolvassa a BASES és ITEMS azonosítókat (a 'factory' és 'none'
     elemek nincsenek benne, mert azok nem literál objektumok).
  2. Minden azonosítóhoz a RULES táblában van keresőkifejezés + a bolti név PONTOS
     (fullmatch) mintája. Szabály nélküli új id-nál "márka + név" a keresés, és csak az
     egyezik, amelynek a neve (írásjel- és kisbetű-függetlenül) teljesen azonos.
  3. Találat -> termékoldal; a színváltozatokat a bolt saját /detail/<id>/switch
     végpontján keresztül kérdezi le (változatonként ár, készlet, kép, URL).
  4. Ha nincs pontos egyezés, az id az "unmatched"-be kerül okkal (+ a legközelebbi
     bolti termék neve/ára tájékoztatásul). Árat soha nem becsül és nem számol.

Udvariasság: egyetlen szálon, kérésenként legalább 1,1 s szünettel (<1 kérés/s),
böngésző User-Agent, 429/5xx esetén visszalépéses újrapróbálás.
Csak www.armamat.ch-ra kérdez (a képeknél, res.cloudinary.com, csak az URL-t rögzíti).
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

BASE = "https://www.armamat.ch"
LANG = "/de-ch"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.1  # másodperc két kérés között

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "armamat.json")


# --------------------------------------------------------------------------- HTTP
class Http:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir
        self.last = 0.0
        self.n = 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _sleep(self):
        wait = MIN_GAP - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)

    def get(self, url, params=None, ajax=False):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        host = urllib.parse.urlparse(url).netloc
        if host not in ("www.armamat.ch", "armamat.ch"):
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, "rb") as f:
                    return f.read().decode("utf-8", "replace"), url
        headers = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5"}
        if ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"
        last_err = None
        for attempt in range(4):
            self._sleep()
            self.last = time.time()
            self.n += 1
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                    final = r.geturl()
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body, final
            except urllib.error.HTTPError as e:
                last_err = e
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
    """'CHF 1’281.90' -> 1281.9"""
    m = re.search(r"([0-9][0-9’'’ ]*(?:\.[0-9]{1,2})?)", norm(text))
    if not m:
        return None
    return float(re.sub(r"[’'’ ]", "", m.group(1)))


def parse_price_block(el):
    """Egy ár-elem (kártya vagy oldal) -> (price, list_price)."""
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
    if m:
        return int(round(float(m.group(1).replace(",", ".")) * 1000))
    return None


# --------------------------------------------------------------------------- szabályok
def R(q, brand=None, title=None, near=None, why=None, note=None):
    """q: keresőkifejezés(ek); brand: bolti márka (regex, fullmatch);
    title: bolti terméknév (regex, fullmatch) – None: a bolt biztosan nem árulja
    ugyanezt a terméket (csomag, más kivitel, nincs a kínálatban);
    near: regex a tájékoztató 'legközelebbi' termékekhez; why: indok az unmatched-hez;
    note: megjegyzés a felvett tételhez."""
    return dict(q=[q] if isinstance(q, str) else q, brand=brand, title=title,
                near=near, why=why, note=note)


MP, BCM, B5, GS, RD = "Magpul", "BCM", "B5 Systems", "Geissele Automatics", "Radian Weapons"
VX, EO, AP, HS, TJ, SF, SL = "Vortex Optics", "EoTech", "Aimpoint", "Holosun", "Trijicon", "SureFire", "Streamlight"

NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a márkát/terméket)"
FIREARM = "alapfegyver (lőfegyver): a webshop nem árul fegyvert"
SET_BUIS = "szett (első + hátsó irányzék): a bolt az elsőt és a hátsót külön termékként árulja, szett nincs"

RULES = {
    # ---- alapfegyverek
    "schm-s4f": R("Schmeisser AR15 S4F", "Schmeisser", None, why=FIREARM),
    "schm-m4fl": R("Schmeisser AR15 M4FL", "Schmeisser", None, why=FIREARM),
    "schm-m5fl": R("Schmeisser AR15 M5FL", "Schmeisser", None, why=FIREARM),
    "schm-dyn": R("Schmeisser AR15 Dynamic M5FL", "Schmeisser", None, why=FIREARM),
    "dd-v7": R("Daniel Defense DDM4 V7", "Daniel Defense", None, why=FIREARM),
    "bcm-recce16": R("BCM RECCE-16", BCM, None, why=FIREARM),
    # ---- markolatok
    "mp-moe": R("Magpul MOE Grip", MP, r"MOE Grip"),
    "mp-moek2": R("Magpul MOE K2 Grip", MP, r"MOE K2 Grip"),
    "mp-moek2p": R("Magpul MOE K2+ Grip", MP, r"MOE K2\+ Grip"),
    "mp-miad": R("Magpul MIAD Grip", MP, r"MIAD 5\.56 Grip Gen 1\.1 Type 1",
                 near=r"MIAD .*Grip",
                 note="AR-15 (5.56, Type 1) kivitel; a MIAD 7.62 Type 2 másik termék"),
    "bcm-g0": R(["BCM Grip Mod 0", "BCM Gunfighter Grip"], BCM, r"(Gunfighter )?Grip Mod 0",
                near=r"Grip Mod \d", why="a Mod 0 markolat nincs a boltban"),
    "bcm-g3": R(["BCM Grip Mod 3", "BCM Gunfighter Grip"], BCM, r"(Gunfighter )?Grip Mod 3"),
    "b5-t22": R("B5 Systems Type 22 P-Grip", B5, r"(Type 22|P-Grip).*", why=NOT_SOLD),
    "b5-t23": R("B5 Systems Type 23 P-Grip", B5, r"(Type 23|P-Grip).*", why=NOT_SOLD),
    # ---- tusok
    "mp-ctr": R("Magpul CTR Carbine Stock", MP, r"CTR Carbine Stock( Mil[- ]Spec)?",
                note="Mil-Spec csöves kivitel (a bolt csak ezt árulja)"),
    "mp-moesl": R("Magpul MOE SL Carbine Stock", MP, r"MOE SL Carbine Stock( Mil[- ]Spec)?",
                  note="Mil-Spec csöves kivitel (a bolt csak ezt árulja)"),
    "mp-moeslk": R("Magpul MOE SL-K Carbine Stock", MP, r"MOE SL-K Carbine Stock( Mil[- ]Spec)?",
                   note="Mil-Spec csöves kivitel (a bolt csak ezt árulja)"),
    "mp-ubr2": R("Magpul UBR Gen 2", MP, r"UBR Gen 2(\.0)? Collapsible Stock"),
    "mp-prs3": R("Magpul PRS Gen 3", MP, r"PRS Gen ?3 Rifle Stock( Mil[- ]Spec)?",
                 near=r"PRS .*", note="Mil-Spec csöves kivitel"),
    "mp-prslite": R("Magpul PRS Lite", MP, r"PRS Lite( Stock)?"),
    "bcm-s0": R("BCM Gunfighter Stock Mod 0", BCM, r"(Gunfighter )?Stock Mod 0", why=NOT_SOLD),
    "bcm-s1": R("BCM Gunfighter Stock Mod 1 SOPMOD", BCM, r"(Gunfighter )?Stock Mod 1.*", why=NOT_SOLD),
    "b5-bravo": R("B5 Systems Bravo Stock", B5, r"Bravo Stock", why=NOT_SOLD),
    "b5-esop": R("B5 Systems Enhanced SOPMOD Stock", B5, r".*SOPMOD.*", why=NOT_SOLD),
    "b5-prec": R("B5 Systems Precision Stock", B5, r"Precision Stock", why=NOT_SOLD),
    # ---- tustalpak
    "mp-pad030": R("Magpul Rubber Butt-Pad", MP, r"Rubber Butt-?Pad", near=r".*Butt-?pad.*"),
    "mp-pad055": R("Magpul Extended Rubber Butt-Pad", MP, r"Extended Rubber Butt-?pad",
                   near=r".*Butt-?pad.*"),
    "mp-pad070": R("Magpul Enhanced Rubber Butt-Pad", MP, r"Enhanced Rubber Butt-?Pad",
                   near=r".*Butt-?pad.*"),
    "mp-padsl": R("Magpul MOE SL Enhanced Rubber Butt-Pad", MP,
                  r"MOE SL Enhanced Rubber Butt-Pad 0\.70 Inch(es)?"),
    # ---- red dot / holo
    "ap-t2": R("Aimpoint Micro T-2", AP, None, near=r"Micro T-2 .*",
               why="csomag (T-2 + LRP szerelék): a bolt a T-2-t külön és két különböző (30/39 mm távtartós) LRP-csomagban árulja, a konkrét kombináció nem azonosítható biztosan"),
    "ap-m5": R("Aimpoint Comp M5", AP, None, near=r"Comp M5 .*|LRP Mount.*",
               why="csomag (CompM5 + LRP szerelék): a bolt 'with Mount' (gyári szerelék) és 'without Mount' változatot árul, az LRP külön termék"),
    "ap-pro": R(["Aimpoint PRO", "Aimpoint Patrol Rifle Optic"], AP, r"PRO( .*)?",
                near=r"PRO\b.*", why="az Aimpoint PRO nincs a boltban"),
    "ap-acro": R("Aimpoint ACRO P-2", AP, None, near=r"ACRO P-2.*",
                 why="csomag (ACRO P-2 + Picatinny talp): a bolt az ACRO P-2-t talp nélkül árulja"),
    "eo-exps3": R("EOTech EXPS3", EO, r"EXPS3-0"),
    "eo-exps2": R("EOTech EXPS2", EO, r"EXPS2-0"),
    "eo-xps2": R("EOTech XPS2", EO, r"XPS2-0"),
    "hs-510c": R("Holosun HS510C", HS, r"HS510C Solar Red Circle Dot Sight"),
    "hs-aems": R("Holosun AEMS", HS, None, near=r"AEMS .*",
                 why="az eredeti 'AEMS' nincs a boltban, csak AEMS Core/Macro/Micro/EVO modellek (más termékek)"),
    "hs-403b": R(["Holosun HS403B", "Holosun 403B"], HS, None, near=r"HS403.*",
                 why="a HS403B + magasító csomag nincs a boltban"),
    "tj-mro": R("Trijicon MRO", TJ, r"MRO.*", why=NOT_SOLD),
    "vx-uh1": R("Vortex UH-1 Gen II", VX, r"AMG UH-1 Gen II Holographic Sight"),
    "vx-sparc": R("Vortex SPARC AR", VX, None, near=r"SPARC .*",
                  why="'SPARC AR + magasító' csomag nincs a boltban, csak a 'SPARC Solar' (más modell)"),
    "vx-crossfire": R("Vortex Crossfire Red Dot", VX, None,
                      near=r"Crossfire (II )?(2 MOA )?(Solar )?Red Dot Sight( 2 MOA)?",
                      why="'Crossfire Red Dot + magasító' csomag: a bolt a vörös pontot magasító nélkül árulja, a csomag összetétele nem azonos"),
    # ---- LPVO
    "vx-se16": R("Vortex Strike Eagle 1-6x24", VX, r"Strike Eagle 1-6x24 AR-BDC3",
                 note="AR-BDC3 retikulos kivitel (a bolt csak ezt árulja)"),
    "vx-se18": R("Vortex Strike Eagle 1-8x24", VX, r"Strike Eagle 1-8x24 AR-BDC3",
                 note="AR-BDC3 retikulos SFP kivitel; az FFP EBR-8 külön termék"),
    "vx-pst16": R("Vortex Viper PST Gen II 1-6x24", VX, r"Viper PST Gen II 1-6x24 SFP VMR-2 (MOA|MRAD)",
                  note="a bolt két retikulos (MOA/MRAD) kivitelt árul; a data.js nem határozza meg"),
    "vx-razor110": R("Vortex Razor Gen III 1-10x24", VX, r"Razor (HD )?Gen III 1-10x24 .*"),
    "tj-acog": R("Trijicon ACOG TA31", TJ, r"(ACOG )?TA31.*", why=NOT_SOLD),
    "tj-credo16": R("Trijicon Credo", TJ, r"Credo.*", why=NOT_SOLD),
    "tj-vcog": R("Trijicon VCOG", TJ, r"VCOG.*", why=NOT_SOLD),
    "eo-vudu16": R("EOTech Vudu 1-6x24", EO, r"Vudu 1-6x24 FFP - .*", near=r"Vudu .*1-6x24.*",
                   note="FFP kivitel ('Vudu X 1-6x24 SFP' külön modell)"),
    "eo-vudu110": R("EOTech Vudu 1-10x28", EO, r"Vudu 1-10x28 FFP - .*",
                    note="a bolt két retikulos (SR5/LE5) kivitelt árul; a data.js nem határozza meg"),
    # ---- optikaszerelék
    "gs-sp30": R(["Geissele Super Precision", "Geissele Scope Mount"], GS, r"Super Precision.*",
                 why=NOT_SOLD),
    "vx-cant30": R("Vortex Sport Cantilever 30mm", VX, r"Sport Cantilever Mount 30mm 1\.5 Inch Height"),
    # ---- nagyítók
    "eo-g33": R("EOTech G33", EO, r"G33\.STS Magnifier"),
    "eo-g43": R("EOTech G43", EO, None, near=r"G43.*",
                why="G43 + 'Micro' szerelék csomag: a bolt csak a G43.STS (STS szerelékkel) változatot árulja"),
    "ap-3x": R("Aimpoint 3XMag", AP, None, near=r"3XMag-1|Flip Mount 30mm|3x Magnifier Twist Mount",
               why="csomag (3XMag-1 + Flip szerelék): a bolt a nagyítót és a szerelékeket külön árulja"),
    "hs-hm3x": R("Holosun HM3X", HS, r"HM3X Magnifier"),
    "vx-m3x": R(["Vortex Micro 3X Magnifier", "Vortex Magnifier"], VX, None, near=r"V3XM .*",
                why="a 'Micro3X + Flip' nincs a boltban; a legközelebbi a V3XM (másik típusjelzés, nem azonosítható biztosan azonosnak)"),
    # ---- BUIS
    "mp-mbus3": R("Magpul MBUS 3", MP, None, near=r"MBUS 3 (Front|Rear) .*", why=SET_BUIS),
    "mp-mbuspro": R("Magpul MBUS Pro", MP, None, near=r"MBUS Pro Sight (Front|Rear)", why=SET_BUIS),
    "mp-mbusoff": R("Magpul MBUS Pro Offset", MP, None, near=r"MBUS Pro Offset Sight (Front|Rear)",
                    why=SET_BUIS),
    # ---- lámpák
    "sf-m600": R("SureFire M600", SF, r"M600.*", why=NOT_SOLD),
    "sf-m640": R("SureFire M640", SF, r"M640.*", why=NOT_SOLD),
    "sf-m300": R("SureFire M300", SF, r"M300.*", why=NOT_SOLD),
    "sf-x300": R("SureFire X300", SF, r"X300.*", why=NOT_SOLD),
    "sl-hlx": R("Streamlight ProTac Rail Mount HL-X", SL, None, near=r"ProTac Rail Mount HL X .*",
                why="a bolt csak 'HL X Pro' típusú változatokat listáz, a data.js 'HL-X' típusa nem azonosítható biztosan"),
    "sl-tlr1": R("Streamlight TLR-1 HL", SL, r"TLR-1 HL"),
    # ---- M-LOK
    "mp-rvg": R("Magpul RVG", MP, None, near=r"(MOE )?RVG .*",
                why="az 'M-LOK RVG' függőleges markolat nincs a boltban; a talált 'MOE RVG Grip' / 'RVG M-LOK Adapter Rail' nem azonos"),
    "mp-hstop": R("Magpul M-LOK Hand Stop", MP, r"M-LOK Hand Stop Kit"),
    "bcm-vg3": R("BCM Vertical Grip", BCM, None, near=r"Vertical Grip.*",
                 why="a Gunfighter Vertical Grip Mod 3 nem azonosítható a bolti 'Vertical Grip - Short' termékkel"),
    "bcm-kag": R("BCM KAG", BCM, None, near=r"Vertical Grip.*", why="a KAG nincs a boltban"),
    "mp-rail5": R("Magpul M-LOK Rail Section", MP, r"M-LOK Rail Section Alumini?um 5 Slots"),
    "mp-qd": R("Magpul M-LOK QD", MP, r"M-LOK QD Sling Mount"),
    "mp-cover": R("Magpul M-LOK Rail Cover", MP, r"M-LOK Rail Cover Type 2"),
    "mp-cable": R("Magpul M-LOK Cable", MP, r"M-LOK Cable.*",
                  why="a Magpul M-LOK Cable Management Kit nincs a boltban"),
    # ---- bipod
    "mp-bipod": R("Magpul Bipod", MP, None,
                  near=r"(Aluminum M-LOK 6\.8-10\.3 Inch Bipod|MOE QD Polymer Bipod for M-LOK)",
                  why="több Magpul M-LOK bipod van a boltban (alumínium / MOE QD polimer), a 'Bipod M-LOK' egyértelműen nem azonosítható"),
    "hr-sbrm": R("Harris S-BRM", "Harris.*", r".*BRM.*", why=NOT_SOLD),
    # ---- torkolat
    "sf-warcomp": R("SureFire WarComp", SF, r".*WarComp.*", why=NOT_SOLD),
    "sf-3p": R("SureFire SF3P", SF, r".*SF3P.*", why=NOT_SOLD),
    "bcm-comp0": R("BCM Compensator", BCM, r".*Comp.*", why=NOT_SOLD),
    "sf-rc2": R("SureFire SOCOM556-RC2", SF, r".*RC2.*", why=NOT_SOLD),
    # ---- elsütők
    "gs-ssae": R("Geissele SSA-E", GS, r".*SSA-E.*", why="az SSA-E nincs a boltban"),
    "gs-ssa": R("Geissele SSA", GS, r".*\bSSA\b.*", why="az SSA nincs a boltban"),
    "gs-sde": R("Geissele SD-E", GS, r".*SD-E.*", why="az SD-E nincs a boltban (csak SD-C, SD-3G)"),
    "gs-s3g": R("Geissele S3G", GS, r"AR15 Super 3 Gun S3G Trigger"),
    # ---- felhúzókar
    "rd-raptor": R("Radian Raptor", RD, r"Raptor Ambidextrous Charging Handle AR15"),
    "rd-raptorlt": R("Radian Raptor-LT", RD, r"Raptor-LT Ambidextrous Charging Handle for AR15"),
    "rd-raptorsd": R("Radian Raptor-SD", RD, r"Raptor-SD.*", why="a Raptor-SD nincs a boltban"),
    "bcm-ch4": R("BCM Charging Handle Mod 4", BCM, r".*Charging Handle.*", why=NOT_SOLD),
    "bcm-ch3": R("BCM Charging Handle Mod 3", BCM, r".*Charging Handle.*", why=NOT_SOLD),
    "gs-ach": R("Geissele Airborne Charging Handle", GS, r".*Airborne.*", near=r".*Charging Handle.*",
                why="az 'Airborne' nincs a boltban; az 'AR15 Super Charging Handle' másik termék"),
    # ---- kezelőszervek
    "rd-talon": R("Radian Talon", RD, r"Talon Ambidextrous 45/90 Safety Selector",
                  near=r"Talon .*", note="a bolt külön 'Kit' változatot is árul (az nem ez)"),
    "mp-bad": R("Magpul B.A.D. Lever", MP, r"B\.A\.D\. Lever"),
    "mp-trigguard": R("Magpul Enhanced Trigger Guard", MP, None, near=r".*Enhanced Trigger Guard",
                      why="a 'MOE Enhanced Trigger Guard' nem szerepel a boltban; a 'Polymer Enhanced Trigger Guard' (MAG1186) más cikkszám/név, nem azonosítható biztosan azonosnak"),
    # ---- tárak
    "mp-pm10": R("Magpul PMAG 10 Gen M3", MP, r"PMAG 10 Gen M3"),
    "mp-pm20": R("Magpul PMAG 20 Gen M3", MP, r"PMAG 20 Gen M3"),
    "mp-pm30": R("Magpul PMAG 30 Gen M3", MP, r"PMAG 30 Gen M3"),
    "mp-pm30w": R("Magpul PMAG 30 Gen M3 Window", MP, r"PMAG 30 Gen M3 Window"),
    "mp-pm40": R("Magpul PMAG 40 Gen M3", MP, r"PMAG 40 Gen M3"),
    "mp-d60": R("Magpul D-60", MP, r"(PMAG )?D-60.*",
                why="a D-60 dobtár nincs a boltban (csak D-50 típusok, más lőszerhez)"),
    # ---- szíjak
    "mp-ms1": R("Magpul MS1 Sling", MP, r"MS1 Sling", near=r"MS1 .*Sling"),
    "mp-ms4": R("Magpul MS4 Dual QD", MP, r"MS4 Dual QD( Gen 2)? Sling",
                note="a bolt csak a Gen 2 kivitelt árulja"),
    "bfg-vickers": R("Blue Force Gear Vickers", "Blue Force Gear", r"Vickers Combat Applications? Sling",
                     near=r"Vickers Combat Application Sling Padded"),
}


# --------------------------------------------------------------------------- data.js
def read_data_js():
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    pat = re.compile(r"\{\s*id:\s*'([^']+)',\s*(?:cat:\s*'[^']+',\s*)?brand:\s*'((?:[^'\\]|\\.)*)',"
                     r"\s*name:\s*'((?:[^'\\]|\\.)*)'")
    return [(m.group(1), m.group(2).replace("\\'", "'"), m.group(3).replace("\\'", "'"))
            for m in pat.finditer(src)]


# --------------------------------------------------------------------------- keresés
def search_cards(http, query):
    body, _ = http.get(BASE + LANG + "/search", {"search": query})
    s = soup(body)
    cards = []
    for b in s.select(".product-box-container"):
        a = b.select_one("a.product-name")
        if not a or not a.get("href"):
            continue
        mf = b.select_one(".manufacturer-name")
        pel = b.select_one(".product-price")
        price, list_price = parse_price_block(pel)
        cards.append(dict(
            brand=norm(mf.get_text(" ")) if mf else "",
            name=norm(a.get("title") or a.get_text(" ")),
            url=a["href"],
            price_from=price,
            list_from=list_price,
            ab=bool(pel and norm(pel.get_text(" ")).startswith("Ab ")),
        ))
    return cards


# --------------------------------------------------------------------------- oldal-feldolgozás
def parse_stock(s_el):
    """Készlet: (in_stock, szöveg, db). A bolt 'Lagernd' / szállítási idő szövegeit használja."""
    de = s_el.select_one(".product-detail-delivery-information")
    txt = norm(de.get_text(" ")) if de else ""
    qty = None
    m = re.search(r"Lagerstatus:\s*(\d+)\s*St", norm(s_el.get_text(" ")), re.I)
    if m:
        qty = int(m.group(1))
    low = txt.lower()
    if not txt:
        return None, "", qty
    if low.startswith("lagernd") or "ab lager" in low:
        return True, txt, qty
    return False, txt, qty


def parse_detail(s_el):
    """Egy termék-/változatoldal (vagy a switch-fragmens) -> dict."""
    box = s_el.select_one(".product-detail-price-container") or s_el.select_one(".product-detail-price")
    price, list_price = parse_price_block(box)
    in_stock, stock_text, qty = parse_stock(s_el)
    text = norm(s_el.get_text(" "))
    out = dict(price_chf=price, list_chf=list_price, in_stock=in_stock, stock_text=stock_text,
               stock_qty=qty)
    m = re.search(r"Artikelnummer:\s*([\w.\-]+)", text)
    out["sku"] = m.group(1) if m else None
    m = re.search(r"Herstellernummer:\s*(.+?)\s+(?:Farbe:|CHF|Lagernd)", text)
    out["mpn"] = m.group(1) if m else None
    names = {"L": "Länge", "B": "Breite", "H": "Höhe"}
    dims, packed = {}, {}
    for key, de in names.items():
        mm = re.search(de + r":\s*([0-9.,]+)\s*cm", text)
        if mm:
            dims[key] = mm.group(1)
        mm = re.search(de + r" verpackt:\s*([0-9.,]+)\s*cm", text)
        if mm:
            packed[key] = mm.group(1)
    out["dims"] = " x ".join("%s %s cm" % (names[k], dims[k]) for k in "LBH" if k in dims) or None
    out["dims_packed"] = " x ".join("%s %s cm" % (names[k], packed[k]) for k in "LBH" if k in packed) or None
    mm = re.search(r"Gewicht:\s*([0-9.,]+\s*kg)", text)
    out["weight_g"] = kg_to_g(mm.group(1)) if mm else None
    mm = re.search(r"Gewicht verpackt:\s*([0-9.,]+\s*kg)", text)
    out["weight_packed_g"] = kg_to_g(mm.group(1)) if mm else None
    imgs = []
    for a in s_el.select("a.gallery-slider-item[href]"):
        h = a["href"]
        if h.startswith("http") and h not in imgs:
            imgs.append(h)
    out["images"] = imgs
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
    """-> (switch_url, [(group_id, group_label, [(group_id, option_id, title, checked)])])"""
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
            title = norm((lbl.get("title") if lbl else "") or (lbl.get_text(" ") if lbl else "")
                         or inp.get("value"))
            options.append((inp.get("name"), inp.get("value"), title, inp.has_attr("checked")))
        if options:
            groups.append((options[0][0], label, options))
    return url, groups


def fetch_variant(http, switch_url, chosen):
    """chosen: {group_id: option_id} -> (variáns URL, detail dict)"""
    last_gid = list(chosen)[-1]
    body, _ = http.get(BASE + switch_url, {"options": json.dumps(chosen), "switched": last_gid}, ajax=True)
    d = json.loads(body)
    return d.get("url"), parse_detail(soup(d.get("content", "")))


# --------------------------------------------------------------------------- egy termék
def pick_primary(variants):
    for pref in ("schwarz", "black"):
        for v in variants:
            if v["color"] and v["color"].lower() == pref:
                return v
    return variants[0]


def scrape_product(http, card):
    body, _ = http.get(card["url"])
    s_el = soup(body)
    ld = ldjson(s_el)
    base = parse_detail(s_el)
    switch_url, groups = configurator(s_el)
    variants = []
    if switch_url and groups:
        combos = list(itertools.product(*[g[2] for g in groups]))[:24]
        for combo in combos:
            chosen = {opt[0]: opt[1] for opt in combo}
            label = {g[1]: opt[2] for g, opt in zip(groups, combo)}
            try:
                url, det = fetch_variant(http, switch_url, chosen)
            except Exception as e:  # nem kombinálható / hiba -> kihagyjuk
                sys.stderr.write("   változat kihagyva (%s): %s\n" % (label, e))
                continue
            det["url"] = url
            det["color"] = next((v for k, v in label.items() if k.lower() in ("farbe", "color")), None)
            det["other_options"] = {k: v for k, v in label.items() if k.lower() not in ("farbe", "color")}
            variants.append(det)
    if not variants:
        base["url"] = card["url"]
        base["color"] = base["selected"].get("Farbe")
        base["other_options"] = {}
        variants = [base]
    return ld, base, variants


def build_item(card, ld, base, variants, note=None):
    prim = pick_primary(variants)
    ld_img = (ld.get("image") or [None])[0] if ld else None
    item = dict(
        title=norm("%s %s" % (card["brand"], card["name"])),
        price_chf=prim["price_chf"],
        list_chf=prim["list_chf"],
        url=prim["url"] or card["url"],  # az elsődleges (fekete / első) változat oldala
        image=prim["image"] or base["image"] or ld_img,
        in_stock=prim["in_stock"],
        weight_g=prim["weight_g"] if prim["weight_g"] is not None else base["weight_g"],
        dims=prim["dims"] or base["dims"],
        color=prim["color"],
    )
    # extrák
    item["stock_text"] = prim["stock_text"]
    item["sku"] = prim["sku"]
    item["mpn"] = prim["mpn"]
    item["weight_packed_g"] = (prim["weight_packed_g"] if prim["weight_packed_g"] is not None
                               else base["weight_packed_g"])
    item["dims_packed"] = prim["dims_packed"] or base["dims_packed"]
    item["product_url"] = card["url"]  # a termék általános (szülő) oldala
    item["images"] = prim["images"] or base["images"] or ([ld_img] if ld_img else [])
    if len(variants) > 1 or prim["color"] or prim["other_options"]:
        cp = {}
        for v in variants:
            key = v["color"] or " / ".join(v["other_options"].values()) or (v["sku"] or "default")
            if v["color"] and v["other_options"]:
                key += " / " + " / ".join(v["other_options"].values())
            cp[key] = dict(price_chf=v["price_chf"], list_chf=v["list_chf"], in_stock=v["in_stock"],
                           url=v["url"], image=v["image"], sku=v["sku"], mpn=v["mpn"])
        item["colors_prices"] = cp
    if note:
        item["note"] = note
    return item


# --------------------------------------------------------------------------- fő logika
def decide(http, pid, brand, name, rule, cache):
    """-> ('ok', item) vagy ('no', indok)"""
    queries = rule["q"] if rule else ["%s %s" % (brand, name)]
    cards, seen = [], set()
    for q in queries:
        if q not in cache:
            cache[q] = search_cards(http, q)
        for c in cache[q]:
            if c["url"] not in seen:
                seen.add(c["url"])
                cards.append(c)
    if rule:
        bre, tre = rule["brand"], rule["title"]
        hits = [] if tre is None else [
            c for c in cards
            if (bre is None or re.fullmatch(bre, c["brand"], re.I)) and re.fullmatch(tre, c["name"], re.I)]
    else:
        # nincs szabály: csak teljes névegyezés (írásjel/kisbetű nélkül), a márka egyezésével
        hits = [c for c in cards if slug(c["brand"]) == slug(brand) and slug(c["name"]) == slug(name)]
    if not hits:
        why = (rule or {}).get("why")
        near_re = (rule or {}).get("near")
        near = []
        if near_re:
            near = [c for c in cards if re.fullmatch(near_re, c["name"], re.I)
                    and (rule["brand"] is None or re.fullmatch(rule["brand"], c["brand"], re.I))]
        reason = "ok: " + (why or "nincs a boltban (a keresés nem adott pontosan azonos terméket)")
        if near:
            reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(
                "%s %s (%s%s CHF, %s)" % (c["brand"], c["name"],
                                           ("ab " if c["ab"] else ""),
                                           ("%.2f" % c["price_from"]) if c["price_from"] else "?", c["url"])
                for c in near[:4])
        return "no", reason
    # több találat: ugyanaz a modell, eltérő retikulum/kivitel -> a legolcsóbbat vesszük, a többit felsoroljuk
    hits.sort(key=lambda c: (c["price_from"] is None, c["price_from"] or 0))
    main, alts = hits[0], hits[1:]
    ld, base, variants = scrape_product(http, main)
    if ld and ld.get("name") and slug(ld["name"]) != slug(main["name"]):
        return "no", "ok: bizonytalan egyezés: a találati kártya neve (%s) és az oldal neve (%s) eltér" % (
            main["name"], ld["name"])
    item = build_item(main, ld, base, variants, (rule or {}).get("note"))
    if alts:
        item["alternatives"] = [dict(title=norm("%s %s" % (c["brand"], c["name"])), url=c["url"],
                                     price_from_chf=c["price_from"]) for c in alts]
        item["match"] = "variant"  # azonos modell, de a bolt több kivitelt árul (a data.js nem dönt)
    else:
        item["match"] = "exact"
    return "ok", item


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/armamat.json; --only mellett stdout)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    http = Http(args.cache)
    items, unmatched, qcache = {}, {}, {}
    for i, (pid, brand, name) in enumerate(entries, 1):
        rule = RULES.get(pid)
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), pid, brand, name))
        try:
            kind, res = decide(http, pid, brand, name, rule, qcache)
        except Exception as e:
            unmatched[pid] = "ok: hiba a lekérdezéskor, nem sikerült ellenőrizni (%s)" % e
            sys.stderr.write("   HIBA: %s\n" % e)
            continue
        if kind == "ok":
            items[pid] = res
            sys.stderr.write("   -> %s  CHF %s\n" % (res["title"], res["price_chf"]))
        else:
            unmatched[pid] = res
            sys.stderr.write("   -> unmatched\n")
    doc = dict(shop="armamat.ch", currency="CHF", fetched=datetime.date.today().isoformat(),
               items=items, unmatched=unmatched)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    out = args.out or (None if args.only else OUT_JSON)
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("Kész: %s  (%d egyezés, %d kimaradt, %d HTTP kérés)\n" % (
            out, len(items), len(unmatched), http.n))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
