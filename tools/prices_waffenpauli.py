#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
waffenpauli.ch (Waffen Pauli AG, Dintikon AG) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_waffenpauli.py                    # minden termék -> assets/prices/waffenpauli.json
    python3 tools/prices_waffenpauli.py --only mp-pm40     # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_waffenpauli.py --cache /tmp/wpc   # HTTP-válaszok gyorsítótára (fejlesztéshez)
    python3 tools/prices_waffenpauli.py --dump Magpul      # a bolti keresés találatainak listája (szabályíráshoz)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés (Shopware 6 storefront)
  1. A js/data.js-ből kiolvassa a BASES és ITEMS azonosítókat.
  2. Id-nként a RULES táblában: keresőkifejezés(ek) (a bolt /search oldala, minden lapozott oldal),
     a bolti terméknév PONTOS (fullmatch) mintája, és opcionálisan színenkénti minták (a bolt a
     színeket külön termékként árulja). Szabály nélküli id: "márka + név" keresés, csak teljes
     (írásjel- és kisbetű-független) névegyezés számít.
  3. Találat -> termékoldal: ár (itemprop=price), áthúzott ár, készlet-címke, cikkszám, kép, súly.
     Ha a szabály 'desc' mintát ad, a termékleírásnak is tartalmaznia kell (pl. "GEN M3").
  4. Nincs pontos egyezés -> "unmatched" okkal + a legközelebbi bolti termékek (név, ár, URL).
     Árat soha nem becsül, nem számol, nem vált át.

Udvariasság: egy szálon, kérések között >= 1,2 s, böngésző User-Agent; csak a bolt hostjára kérdez.
403 / tartós 429 / Cloudflare-kihívás esetén azonnal leáll ("blocked"), nem próbál megkerülni semmit.
"""
import argparse
import datetime
import hashlib
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

# --------------------------------------------------------------------------- bolt-konfiguráció
SHOP = "waffenpauli.ch"
SLUG = "waffenpauli"
BASE = "https://www.waffenpauli.ch"
HOSTS = ("www.waffenpauli.ch", "waffenpauli.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
LANG = "de-CH,de;q=0.9,en;q=0.5"
MIN_GAP = 1.2
MAX_PAGES = 12

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", SLUG + ".json")


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
        if host not in HOSTS:
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, "rb") as f:
                    return f.read().decode("utf-8", "replace")
        headers = {"User-Agent": UA, "Accept-Language": LANG,
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        last_err = None
        for attempt in range(3):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                if re.search(r"cf-chl|challenge-platform|Just a moment\.\.\.|Verifying (you are human|connection)",
                             body[:20000]):
                    raise Blocked("botvédelmi kihívás (%s)" % url)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise Blocked("HTTP 403 (%s)" % url)
                if e.code == 429:
                    if attempt == 0:
                        time.sleep(30)
                        continue
                    raise Blocked("tartós HTTP 429 (%s)" % url)
                if e.code in (500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
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
    s = re.sub(r"[®™©]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", norm(s).lower())


def parse_chf(text):
    """'1.995,00 CHF*' / 'Ab 33,00 CHF*' / '1 453,00 CHF' -> float"""
    t = norm(text)
    m = re.search(r"([0-9][0-9.'’ ]*,[0-9]{2})", t)
    if m:
        return float(re.sub(r"[.'’ ]", "", m.group(1)).replace(",", "."))
    m = re.search(r"([0-9][0-9'’]*(?:\.[0-9]{1,2})?)", t)
    return float(re.sub(r"['’]", "", m.group(1))) if m else None


def kg_to_g(txt):
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*kg", txt or "")
    return int(round(float(m.group(1).replace(",", ".")) * 1000)) if m else None


def stock_state(txt):
    low = (txt or "").lower()
    if not low:
        return None
    if re.search(r"nicht (mehr )?(lieferbar|verfügbar|an lager)|ausverkauft|auf bestellung|vergriffen|"
                 r"épuisé|sur commande|non disponible", low):
        return False
    if re.search(r"lieferbar|sofort|an lager|verfügbar|en stock|disponible", low):
        return True
    return None


# --------------------------------------------------------------------------- szabályok
def R(q, title=None, colors=None, near=None, why=None, note=None, desc=None):
    """q: keresőkifejezés(ek) a bolt keresőjében;
    title: a fő (fekete / első palettaszín) bolti termék nevének mintája (fullmatch, kisbetű-független,
           ®/™ nélkül) - None: a bolt nem árulja pontosan ugyanezt;
    colors: {paletta-kulcs: névminta} a külön termékként árult színváltozatokhoz;
    near: minta a tájékoztató "legközelebbi" bolti termékekhez; why: indok az unmatched-hez;
    note: megjegyzés a felvett tételhez; desc: a termékleírásban kötelezően szereplő minta."""
    return dict(q=[q] if isinstance(q, str) else q, title=title, colors=colors or {}, near=near,
                why=why, note=note, desc=desc)


NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a márkát/terméket)"
SET_BUIS = "szett (első + hátsó irányzék): a bolt csak külön első/hátsó irányzékot árul, szettet nem"

RULES = {
    # ---- alapfegyverek (a bolt árul fegyvert, WES szükséges)
    "schm-s4f": R("Schmeisser",
                  r'Halbautomat Schmeisser AR15 S4F Black 10\.5" \.223 Rem\. Handschutz MLOK System, .*',
                  colors={"black": r'Halbautomat Schmeisser AR15 S4F Black 10\.5" \.223 Rem\. Handschutz MLOK System, .*'},
                  note="Standard S4F 10.5\" fekete kivitel (a bolti név a gyári 10-es tárat is említi); más kivitel nincs a boltban"),
    "schm-m4fl": R("Schmeisser", None, near=r"Halbautomat Schmeisser AR15 .*",
                   why="az M4FL nincs a boltban (csak S4F, M4 A2 Classic Limited és 'Special Edition inkl. Falke' kivitel)"),
    "schm-m5fl": R("Schmeisser", None, near=r"Halbautomat Schmeisser AR15 .*", why="az M5FL nincs a boltban"),
    "schm-dyn": R("Schmeisser", None, near=r"Halbautomat Schmeisser AR15 .*", why="a Dynamic nincs a boltban"),
    "dd-v7": R("Daniel Defense", None, near=r"Halbautomat Daniel Defense .*",
               why="DDM4 V7 nincs a boltban (csak DD5 V4 .308/6.5 CM)"),
    "bcm-recce16": R("BCM", None, why=NOT_SOLD),
    # ---- markolatok
    "mp-moe": R("Magpul", None, near=r".*Magpul.*(Griff|Grip).*", why="a Magpul MOE Grip nincs a boltban"),
    "mp-moek2": R("Magpul", None, near=r".*Magpul.*(Griff|Grip).*", why="a MOE-K2 (sima) markolat nincs a boltban"),
    "mp-moek2p": R("Magpul", r"Magpul MOE K2\+ Pistolengriff",
                   note="a bolti oldal nem nevezi meg a színt (a képen fekete)"),
    "mp-miad": R(["Magpul", "MIAD"], None, near=r".*MIAD.*", why="a MIAD markolat nincs a boltban (csak tárolóbetét)"),
    "bcm-g0": R("BCM", None, why=NOT_SOLD),
    "bcm-g3": R("BCM", None, why=NOT_SOLD),
    "b5-t22": R("B5 Systems", None, why=NOT_SOLD),
    "b5-t23": R("B5 Systems", None, why=NOT_SOLD),
    # ---- tusok
    "mp-ctr": R("Magpul", None, near=r".*Magpul.*Schaft.*", why="a CTR tus nincs a boltban"),
    "mp-moesl": R("Magpul", None, near=r".*Magpul.*Schaft.*", why="a MOE SL tus nincs a boltban"),
    "mp-moeslk": R("Magpul", None, near=r".*Magpul.*Schaft.*", why="a MOE SL-K tus nincs a boltban"),
    "mp-ubr2": R("Magpul", None, near=r".*Magpul.*Schaft.*", why="az UBR Gen2 nincs a boltban"),
    "mp-prs3": R("Magpul", None, near=r".*Magpul.*Schaft.*", why="a PRS Gen3 nincs a boltban"),
    "mp-prslite": R("Magpul", None, near=r".*Magpul.*Schaft.*", why="a PRS Lite nincs a boltban"),
    "bcm-s0": R("BCM", None, why=NOT_SOLD),
    "bcm-s1": R("BCM", None, why=NOT_SOLD),
    "b5-bravo": R("B5 Systems", None, why=NOT_SOLD),
    "b5-esop": R("B5 Systems", None, why=NOT_SOLD),
    "b5-prec": R("B5 Systems", None, why=NOT_SOLD),
    # ---- tustalpak
    "mp-pad030": R("Magpul", None, why="Magpul tustalp nincs a boltban"),
    "mp-pad055": R("Magpul", None, why="Magpul tustalp nincs a boltban"),
    "mp-pad070": R("Magpul", None, why="Magpul tustalp nincs a boltban"),
    "mp-padsl": R("Magpul", None, why="Magpul tustalp nincs a boltban"),
    # ---- red dot / holo
    "ap-t2": R("Aimpoint", None, near=r".*Aimpoint.*", why="a Micro T-2 (+ LRP) nincs a boltban"),
    "ap-m5": R("Aimpoint", None, near=r".*Aimpoint.*", why="a CompM5 (+ LRP) nincs a boltban"),
    "ap-pro": R("Aimpoint", None, why="az Aimpoint PRO nincs a boltban"),
    "ap-acro": R("Aimpoint", None, near=r".*ACRO P-2.*",
                 why="csomag (ACRO P-2 + Picatinny talp): a bolt az ACRO P-2-t talp nélkül árulja"),
    "eo-exps3": R("EOTech", None, near=r".*EXPS.*", why="az EXPS3-0 nincs a boltban"),
    "eo-exps2": R("EOTech", None, near=r".*EXPS.*",
                  why="az EXPS2-0 önállóan nincs a boltban (csak EXPS2-2 + G33 csomag)"),
    "eo-xps2": R("EOTech", None, why="az XPS2-0 nincs a boltban"),
    "hs-510c": R("Holosun", r"Rotpunktvisier Holosun HS510C-RD-Black mit rotem Wechselabsehen: .*"),
    "hs-aems": R("Holosun", None, why="az AEMS nincs a boltban"),
    "hs-403b": R("Holosun", None, why="a HS403B nincs a boltban"),
    "tj-mro": R("Trijicon", None, why=NOT_SOLD),
    "vx-uh1": R("Vortex", None, why="az UH-1 Gen II nincs a boltban"),
    "vx-sparc": R("Vortex", None, why="a SPARC AR nincs a boltban"),
    "vx-crossfire": R("Vortex", None, why="a Crossfire nincs a boltban"),
    # ---- LPVO
    "vx-se16": R("Vortex", None, why="a Strike Eagle nincs a boltban"),
    "vx-se18": R("Vortex", None, why="a Strike Eagle nincs a boltban"),
    "vx-pst16": R("Vortex", None, why="a Viper PST Gen II nincs a boltban"),
    "vx-razor110": R("Vortex", None, why="a Razor HD Gen III nincs a boltban"),
    "tj-acog": R("Trijicon", None, why=NOT_SOLD),
    "tj-credo16": R("Trijicon", None, why=NOT_SOLD),
    "tj-vcog": R("Trijicon", None, why=NOT_SOLD),
    "eo-vudu16": R("EOTech", None, why="a Vudu nincs a boltban"),
    "eo-vudu110": R("EOTech", None, why="a Vudu nincs a boltban"),
    # ---- optikaszerelék
    "gs-sp30": R("Geissele", None, why="a Super Precision szerelék nincs a boltban"),
    "vx-cant30": R("Vortex", None, why="a Vortex Sport Cantilever nincs a boltban"),
    # ---- nagyítók
    "eo-g33": R("EOTech", None, near=r".*G33.*",
                why="a G33 + STS önállóan nincs a boltban (csak EXPS2-2 + G33 csomag)"),
    "eo-g43": R("EOTech", None, why="a G43 nincs a boltban"),
    "ap-3x": R("Aimpoint", None, why="a 3XMag-1 nincs a boltban"),
    "hs-hm3x": R("Holosun", r"Holosun Magnifier HM3X"),
    "vx-m3x": R("Vortex", None, why="a Vortex Micro3X nincs a boltban"),
    # ---- BUIS
    "mp-mbus3": R("Magpul", None, near=r".*Back-Up Sight \(MBUS\).*",
                  why="MBUS 3 nincs a boltban; csak az előző generációs MBUS első és hátsó irányzék külön (nem szett)"),
    "mp-mbuspro": R("Magpul", None, why="az MBUS Pro nincs a boltban"),
    "mp-mbusoff": R("Magpul", None, why="MBUS Pro Offset nincs a boltban"),
    # ---- lámpák
    "sf-m600": R("SureFire", None, near=r".*SureFire.*", why="az M600DF nincs a boltban"),
    "sf-m640": R("SureFire", None, near=r".*M640.*",
                 why="az M640DF nincs a boltban; az M640T (Turbo) másik modell"),
    "sf-m300": R("SureFire", None, near=r".*M340.*",
                 why="az M300 nincs a boltban; az M340T (Turbo) másik modell"),
    "sf-x300": R("SureFire", None, near=r".*X300.*",
                 why="az X300U-B nincs a boltban; az X300U-A (más sínrögzítés) nem azonos"),
    "sl-hlx": R("Streamlight", None, why="a ProTac Rail Mount HL-X nincs a boltban"),
    "sl-tlr1": R("Streamlight", None, near=r".*TLR.*", why="a TLR-1 HL nincs a boltban"),
    # ---- M-LOK
    "mp-rvg": R("Magpul", None, near=r".*(RVG|MVG).*",
                why="az 'M-LOK RVG' nincs a boltban (csak MOE MVG markolat)"),
    "mp-hstop": R("Magpul", None, why="az M-LOK Hand Stop Kit nincs a boltban"),
    "bcm-vg3": R("BCM", None, why=NOT_SOLD),
    "bcm-kag": R("BCM", None, why=NOT_SOLD),
    "mp-rail5": R("Magpul", r"Magpul M-LOK Rail Section Aluminium 5 Slots / 58mm"),
    "mp-qd": R("Magpul", None, near=r".*(QD Sling|Sling Swivel|Bipod Mount).*",
               why="az M-LOK QD Sling Mount nincs a boltban"),
    "mp-cover": R("Magpul", None, why="az M-LOK Rail Cover Type 2 nincs a boltban"),
    "mp-cable": R("Magpul", None, why="az M-LOK Cable Management Kit nincs a boltban"),
    # ---- bipod
    "mp-bipod": R("Magpul", r"MAGPUL Bipod for M-LOK Schwarz",
                  colors={"black": r"MAGPUL Bipod for M-LOK Schwarz"},
                  note="Magpul 'Bipod for M-LOK' (MAG933, alumínium); a 'MOE QD Bipod' / 'MOE Bipod' külön termékek"),
    "hr-sbrm": R(["Harris", "S-BRM"], r"Harris Zweibeinstützen Mod\. S-BRM, neigbar, 15\.2-23cm \(MLOCK\)",
                 note="S-BRM 6-9\" (15,2-23 cm), gyárilag M-LOK rögzítéssel (HAS-BRM-MLOK)"),
    # ---- torkolat
    "sf-warcomp": R("SureFire", None, why=NOT_SOLD),
    "sf-3p": R("SureFire", None, why=NOT_SOLD),
    "bcm-comp0": R("BCM", None, why=NOT_SOLD),
    "sf-rc2": R("SureFire", None, why=NOT_SOLD),
    # ---- elsütők
    "gs-ssae": R("Geissele",
                 r"Abzug Geissele Automatics Super Semi-Automatic Enhanced \(SSA-E\) Trigger 2 Stage AR15/AR10"),
    "gs-ssa": R("Geissele", None, near=r".*Geissele.*", why="az SSA nincs a boltban (csak SSA-E)"),
    "gs-sde": R("Geissele", None, near=r".*Geissele.*", why="az SD-E nincs a boltban"),
    "gs-s3g": R("Geissele", None, near=r".*Geissele.*", why="az S3G nincs a boltban"),
    # ---- felhúzókar
    "rd-raptor": R("Radian", r"Wyssen-RADIAN Raptor Ladehebel AXTS zu AR15/M16",
                   note="Radian (AXTS) Raptor ambidex felhúzókar AR15-höz, a Wyssen Defence forgalmazásában; szín nincs megadva"),
    "rd-raptorlt": R("Radian", None, near=r".*Raptor.*", why="a Raptor-LT nincs a boltban"),
    "rd-raptorsd": R("Radian", r"Wyssen-RADIAN Raptor SD für AR15 und WD9",
                     note="Radian (AXTS) Raptor-SD hangtompítós felhúzókar AR15-höz (Wyssen Defence forgalmazás)"),
    "bcm-ch4": R("BCM", None, why=NOT_SOLD),
    "bcm-ch3": R("BCM", None, why=NOT_SOLD),
    "gs-ach": R("Geissele", None, why="az Airborne Charging Handle nincs a boltban"),
    # ---- kezelőszervek
    "rd-talon": R("Radian", None, why="a Talon nincs a boltban"),
    "mp-bad": R("Magpul", None, why="a B.A.D. Lever nincs a boltban"),
    "mp-trigguard": R("Magpul", None, why="a MOE Enhanced Trigger Guard nincs a boltban"),
    # ---- tárak (a bolt a színeket külön termékként árulja)
    "mp-pm10": R(["Magpul", "PMAG"], r"Magazin Magpul PMAG 10 AR/M4 GEN M3 \.223 Rem\. 10 Patronen, schwarz",
                 colors={"black": r"Magazin Magpul PMAG 10 AR/M4 GEN M3 \.223 Rem\. 10 Patronen, schwarz"},
                 near=r".*(PMAG ?10|10 Patronen).*",
                 note="a bolt egy 'sandfarben' 10-es változatot is árul (WP0229.4) – a színe nem azonosítható biztosan FDE-ként, ezért nincs felvéve"),
    "mp-pm20": R(["Magpul", "PMAG"], r"Magazin Magpul AR15 \.223 Rem\. 20 Patronen schwarz 5\.56x45, \.223 Rem\.",
                 colors={"black": r"Magazin Magpul AR15 \.223 Rem\. 20 Patronen schwarz 5\.56x45, \.223 Rem\."},
                 desc=r"GEN M3 PMAG", near=r".*20 Patronen.*",
                 note="a bolti név nem írja ki, de a leírás szerint 'GEN M3 PMAG' 5,56x45; a 'sandfarben' 20-as (WP0229.5) színe nem azonosítható biztosan FDE-ként, ezért nincs felvéve"),
    "mp-pm30": R(["Magpul", "PMAG"], None, near=r".*PMAG.*30.*|.*TMAG.*",
                 why="sima PMAG 30 AR/M4 Gen M3 (ablak nélkül) nincs a boltban"),
    "mp-pm30w": R(["Magpul", "PMAG"], r"Magazin Magpul PMAG 30 AR/M4 GEN M3 Window",
                  colors={"black": r"Magazin Magpul PMAG 30 AR/M4 GEN M3 Window"},
                  note="a bolt Coyote (MCT) színben is árulja (MAG556-MCT, 26.00 CHF) – ez nem FDE, a data.js palettájában (black/fde) nincs"),
    "mp-pm40": R(["Magpul", "PMAG"], r"Magazin Magpul GEN M3 PMAG, schwarz, 40Patronen \.223 Rem\.",
                 colors={"black": r"Magazin Magpul GEN M3 PMAG, schwarz, 40Patronen \.223 Rem\."}),
    "mp-d60": R(["Magpul", "PMAG"], None, why="a D-60 dobtár nincs a boltban"),
    # ---- szíjak
    "mp-ms1": R(["Magpul", "MS1"], None, near=r".*MS1.*",
                why="az MS1 Sling nincs a boltban; az 'MS1 Lite' másik termék"),
    "mp-ms4": R(["Magpul", "MS4"], None, near=r".*MS4.*",
                why="az MS4 Dual QD nincs a boltban; az 'MS4 Dual QDM' (mini-QD) másik termék"),
    "bfg-vickers": R("Blue Force", None, near=r".*Vickers.*",
                     why="a Vickers Combat Applications Sling nincs a boltban (csak 'Vickers Standard AK Sling')"),
}


# --------------------------------------------------------------------------- data.js
def read_data_js():
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    out = []
    for m in re.finditer(r"\{\s*id:\s*'([^']+)'([^{}]*)\}", src):
        body = m.group(2)
        b = re.search(r"\bbrand:\s*'((?:[^'\\]|\\.)*)'", body)
        n = re.search(r"\bname:\s*'((?:[^'\\]|\\.)*)'", body)
        if b and n:
            out.append((m.group(1), b.group(1).replace("\\'", "'"), n.group(1).replace("\\'", "'")))
    return out


# --------------------------------------------------------------------------- keresés
def search_cards(http, query):
    cards, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        s = soup(http.get(BASE + "/search", {"search": query, "p": page}))
        boxes = s.select(".product-box")
        for b in boxes:
            a = b.select_one("a.product-name") or b.select_one("a.product-image-link")
            if not a or not a.get("href") or a["href"] in seen:
                continue
            seen.add(a["href"])
            pel = b.select_one(".product-price-info") or b.select_one(".product-price")
            lp = b.select_one(".list-price-price")
            ptxt = norm(pel.get_text(" ")) if pel else ""
            if lp:
                ptxt = ptxt.replace(norm(lp.get_text(" ")), " ", 1)
            cards.append(dict(name=norm(a.get("title") or a.get_text(" ")), url=a["href"],
                              price_from=parse_chf(ptxt) if ptxt else None,
                              list_from=parse_chf(lp.get_text(" ")) if lp else None,
                              ab=bool(re.match(r"^(ab|de|dès)\s", ptxt, re.I))))
        pages = [int(i.get("value")) for i in s.select(".pagination input[type=radio]")
                 if (i.get("value") or "").isdigit()]
        if not boxes or page >= (max(pages) if pages else 1):
            break
    return cards


# --------------------------------------------------------------------------- termékoldal
def parse_detail(html, url):
    s = soup(html)
    out = dict(url=url)
    n = s.select_one(".product-detail-name")
    out["title"] = norm(n.get_text(" ")) if n else None
    buy = s.select_one(".product-detail-buy") or s
    mp = buy.select_one('meta[itemprop="price"]')
    price = None
    if mp and mp.get("content"):
        try:
            price = float(mp["content"])
        except ValueError:
            price = None
    pc = buy.select_one(".product-detail-price-container")
    lp = pc.select_one(".list-price-price, .product-detail-list-price") if pc else None
    if price is None and pc:
        txt = norm(pc.get_text(" "))
        if lp:
            txt = txt.replace(norm(lp.get_text(" ")), " ", 1)
        price = parse_chf(txt)
    out["price_chf"] = price
    out["list_chf"] = parse_chf(lp.get_text(" ")) if lp else None
    if out["list_chf"] is not None and price is not None and out["list_chf"] <= price:
        out["list_chf"] = None
    st = buy.select_one(".twt-product-stock-label")
    if st is None:
        st = buy.select_one(".product-detail-delivery-information .delivery-information")
    out["stock_text"] = norm(st.get_text(" ")) if st else ""
    out["in_stock"] = stock_state(out["stock_text"])
    on = buy.select_one(".product-detail-ordernumber")
    if on:
        out["sku"] = norm(on.get_text(" "))
    else:
        oc = buy.select_one(".product-detail-ordernumber-container")
        m = re.search(r"Produktnummer:?\s*(\S+)", norm(oc.get_text(" "))) if oc else None
        out["sku"] = m.group(1) if m else None
    page_txt = norm(s.get_text(" "))
    m = re.search(r"Hersteller(?:artikel)?-?(?:nummer|nr\.?)\s*:?\s*([\w\-./]+)", page_txt, re.I)
    out["mpn"] = m.group(1) if m else None
    w = buy.select_one('meta[itemprop="weight"]')
    out["weight_g"] = kg_to_g(w.get("content")) if w else None
    dims = []
    for prop, lab in (("depth", "L"), ("width", "B"), ("height", "H")):
        e = buy.select_one('meta[itemprop="%s"]' % prop)
        if e and e.get("content"):
            dims.append("%s %s" % (lab, e["content"]))
    out["dims"] = (" x ".join(dims) + " (bolti adat, valószínűleg csomagméret)") if dims else None
    img = s.select_one("img.gallery-slider-image") or s.select_one(".gallery-slider-item img")
    src = (img.get("src") or img.get("data-src")) if img else None
    if not src:
        og = s.select_one('meta[property="og:image"]')
        src = og.get("content") if og else None
    out["image"] = src
    d = s.select_one(".product-detail-description-text") or s.select_one(".product-detail-description")
    out["desc"] = norm(d.get_text(" ")) if d else ""
    out["has_configurator"] = bool(s.select_one(".product-detail-configurator-group"))
    return out


# --------------------------------------------------------------------------- döntés
def fmt_near(cards):
    return " | ".join("%s (%s%s CHF, %s)" % (c["name"], "ab " if c["ab"] else "",
                                             ("%.2f" % c["price_from"]) if c["price_from"] is not None else "?",
                                             c["url"]) for c in cards[:4])


def decide(http, pid, brand, name, rule, qcache, dcache):
    queries = rule["q"] if rule else ["%s %s" % (brand, name)]
    cards, seen = [], set()
    for q in queries:
        if q not in qcache:
            qcache[q] = search_cards(http, q)
        for c in qcache[q]:
            if c["url"] not in seen:
                seen.add(c["url"])
                cards.append(c)

    def fm(pattern, c):
        return pattern and re.fullmatch(pattern, c["name"], re.I)

    if rule:
        hits = [c for c in cards if fm(rule["title"], c)]
    else:
        hits = [c for c in cards if slug(c["name"]) in (slug(name), slug(brand + " " + name))]

    def unmatched(reason):
        near = [c for c in cards if fm((rule or {}).get("near"), c)]
        txt = "ok: " + reason
        if near:
            txt += "; legközelebbi bolti termék(ek), NEM azonos: " + fmt_near(near)
        return "no", txt

    if not hits:
        return unmatched((rule or {}).get("why") or "nincs a boltban (a keresés nem adott pontosan azonos terméket)")
    hits.sort(key=lambda c: (c["price_from"] is None, c["price_from"] or 0))
    main = hits[0]
    if main["ab"]:
        return unmatched("változatfüggő ('ab') ár: a változat ára nem olvasható ki biztosan (%s)" % main["url"])

    def detail(c):
        if c["url"] not in dcache:
            dcache[c["url"]] = parse_detail(http.get(c["url"]), c["url"])
        return dcache[c["url"]]

    d = detail(main)
    if d["title"] and slug(d["title"]) != slug(main["name"]):
        return unmatched("bizonytalan egyezés: a találati név (%s) és a termékoldal neve (%s) eltér"
                         % (main["name"], d["title"]))
    if rule and rule.get("desc") and not re.search(rule["desc"], d["desc"], re.I):
        return unmatched("a termékleírás nem erősíti meg a típust ('%s' hiányzik): %s"
                         % (rule["desc"], main["url"]))
    if d["price_chf"] is None:
        return unmatched("nincs kiírt ár: " + main["url"])
    if d["has_configurator"]:
        return unmatched("a terméknek változatválasztója van, a változat nem azonosítható biztosan: " + main["url"])
    item = dict(title=d["title"] or main["name"], price_chf=d["price_chf"], list_chf=d["list_chf"],
                url=main["url"], image=d["image"], in_stock=d["in_stock"], stock_text=d["stock_text"],
                sku=d["sku"], mpn=d["mpn"], weight_g=d["weight_g"], dims=d["dims"], colors_prices={})
    for key, pat in (rule or {}).get("colors", {}).items():
        cc = [c for c in cards if fm(pat, c) and not c["ab"]]
        if not cc:
            continue
        cc.sort(key=lambda c: (c["price_from"] is None, c["price_from"] or 0))
        dc = detail(cc[0])
        if dc["price_chf"] is None or dc["has_configurator"]:
            continue
        item["colors_prices"][key] = dict(price_chf=dc["price_chf"], list_chf=dc["list_chf"], url=cc[0]["url"],
                                          in_stock=dc["in_stock"], title=dc["title"] or cc[0]["name"])
    if (rule or {}).get("note"):
        item["note"] = rule["note"]
    if len(hits) > 1:
        item["alternatives"] = [dict(title=c["name"], url=c["url"], price_from_chf=c["price_from"])
                                for c in hits[1:]]
        item["match"] = "variant"
    else:
        item["match"] = "exact"
    return "ok", item


def dump(http, queries):
    for q in queries:
        for c in search_cards(http, q):
            print("%-10s %8s  %s  %s" % (q[:10], c["price_from"], c["name"], c["url"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/%s.json; --only mellett stdout)" % SLUG)
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    ap.add_argument("--dump", nargs="+", help="csak kilistázza a keresés találatait")
    args = ap.parse_args()
    http = Http(args.cache)
    if args.dump:
        dump(http, args.dump)
        return
    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    items, unmatched, qcache, dcache, blocked = {}, {}, {}, {}, None
    for i, (pid, brand, name) in enumerate(entries, 1):
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), pid, brand, name))
        if blocked:
            unmatched[pid] = "ok: a bolt letiltotta a lekérdezést (%s), nem ellenőrizhető" % blocked
            continue
        try:
            kind, res = decide(http, pid, brand, name, RULES.get(pid), qcache, dcache)
        except Blocked as e:
            blocked = str(e)
            sys.stderr.write("   BLOKKOLVA: %s – leállás\n" % e)
            unmatched[pid] = "ok: a bolt letiltotta a lekérdezést (%s), nem ellenőrizhető" % blocked
            continue
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
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
               items=items, unmatched=unmatched, blocked=blocked)
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
