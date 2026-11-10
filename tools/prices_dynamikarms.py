#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dynamikarms.ch ár- és termék-gyűjtő az AR-15 konfigurátorhoz (research/PRICING_BRIEF.md szerint).

Futtatás (a repo gyökeréből):
    python3 tools/prices_dynamikarms.py                          # minden termék -> assets/prices/dynamikarms.json
    python3 tools/prices_dynamikarms.py --only mp-pm30,mp-moe    # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_dynamikarms.py --cache /tmp/arcache     # HTTP-válaszok gyorsítótára (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van). Más csomag nem kell.

Motor: PrestaShop 1.6 (HTML kereső: /de/suche?controller=search&search_query=…; termékoldal JS 'combinations' = színváltozatok)

Működés
  1. A js/data.js-ből kiolvassa a BASES és ITEMS azonosítókat (név, márka, színpaletta).
  2. Az id-hoz tartozó szabály (RULES: keresőkifejezések + a bolti terméknévre illesztett
     "kell"/"nem lehet" minták, normalizált alakon) választja ki a pontosan azonos terméket.
  3. Találat -> a termék- / változat-adatok (ár, készlet, kép-URL, cikkszám) a bolt saját oldaláról;
     a színváltozatok a data.js palettakulcsaira képződnek le (colors_prices).
  4. Ha nincs pontos egyezés (más típus, csomag, külön szerelék, nincs a kínálatban), az id az
     "unmatched"-be kerül okkal és a legközelebbi bolti termékekkel. Árat soha nem becsül,
     nem számol át; csak a bolt kiírt CHF-árát rögzíti (EUR/USD-árat nem).
  5. Botvédelem (Cloudflare-kihívás, captcha, 403, tartós 429) esetén azonnal leáll és 'blocked'-ot ír;
     nem kerüli meg.

Udvariasság: egyetlen szálon, kérésenként legalább 1,2 s szünettel, böngésző User-Agent.
Képeket nem tölt le, csak az URL-jüket rögzíti.
"""

SHOP = "dynamikarms.ch"
SLUG = "dynamikarms"
BASE = "https://www.dynamikarms.ch"
ALLOWED_HOSTS = ("www.dynamikarms.ch", "dynamikarms.ch")

# ============================================================================ közös mag
import argparse
import datetime
import hashlib
import html as htmllib
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

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2  # másodperc két kérés között (hostonként egy szál, <1 kérés/s)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", SLUG + ".json")


class Blocked(Exception):
    """Botvédelem / tiltás: a bolttal azonnal le kell állni (nem kerüljük meg)."""


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

    def get(self, url, params=None, headers=None, allow_status=()):
        """-> (body, final_url). 403/429-es tartós választ Blocked-ként dob (nem próbálkozik tovább)."""
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        host = urllib.parse.urlparse(url).netloc
        if host not in ALLOWED_HOSTS:
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, "rb") as f:
                    return f.read().decode("utf-8", "replace"), url
        h = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5"}
        h.update(headers or {})
        last_err = None
        for attempt in range(3):
            self._sleep()
            self.last = time.time()
            self.n += 1
            try:
                req = urllib.request.Request(url, headers=h)
                with urllib.request.urlopen(req, timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                    final = r.geturl()
                low = body[:3000].lower()
                if "just a moment" in low or "cf-chl" in low or "verifying connection" in low or "captcha" in low[:1500]:
                    raise Blocked("botvédelmi kihívás oldal: " + url)
                if urllib.parse.urlparse(final).netloc not in ALLOWED_HOSTS:
                    raise ValueError("átirányítás nem engedélyezett hostra: " + final)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body, final
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise Blocked("HTTP 403: " + url)
                if e.code == 429:
                    if attempt >= 1:
                        raise Blocked("tartós HTTP 429: " + url)
                    time.sleep(20)
                    continue
                if e.code in allow_status:
                    return "", url
                if e.code in (500, 502, 503, 504) and attempt < 1:
                    time.sleep(5)
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
    s = (s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def nt(s):
    """Összehasonlításhoz normalizált cím: kisbetű, ®™© nélkül, '+' -> ' plus ', pont csak számok közt."""
    s = htmllib.unescape(s or "")
    s = re.sub(r"[®™©]", "", s)
    s = unicodedata.normalize("NFKC", s).lower().replace("\xa0", " ")
    s = s.replace("+", " plus ").replace("×", "x").replace("’", "").replace("'", "")
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", "", s)
    s = re.sub(r"[^a-z0-9äöüéèàçß.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "", nt(s))


def parse_price(text):
    """'CHF 1’281.90' / '1\'281.90 CHF' / '338,00 CHF' -> 1281.9"""
    t = norm(text)
    m = re.search(r"(\d[\d’'’ ,.]*)", t)
    if not m:
        return None
    raw = m.group(1).strip().rstrip(".,")
    raw = raw.replace("’", "").replace("'", "").replace(" ", "")
    if re.search(r",\d{1,2}$", raw) and "." not in raw:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


# --------------------------------------------------------------------------- data.js
def read_data_js():
    """-> [(id, brand, name, colors|None)] a BASES és ITEMS minden literál elemére."""
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    pal = {"MAGPUL": ["black", "fde", "odg", "gray"], "BCM": ["black", "fde", "foliage", "wolfgray"],
           "B5": ["black", "fde", "odg", "wolfgray"]}
    out = []
    for m in re.finditer(r"\{\s*id:\s*'([^']+)'([^{}]*)\}", src):
        body = m.group(2)
        mb = re.search(r"brand:\s*'((?:[^'\\]|\\.)*)'", body)
        mn = re.search(r"name:\s*'((?:[^'\\]|\\.)*)'", body)
        if not mb or not mn:
            continue
        cm = re.search(r"colors:\s*(MAGPUL|BCM|B5|\[[^\]]*\])", body)
        cols = None
        if cm:
            cols = pal.get(cm.group(1)) or re.findall(r"'([a-z]+)'", cm.group(1))
        out.append((m.group(1), mb.group(1).replace("\\'", "'"), mn.group(1).replace("\\'", "'"), cols))
    return out


# --------------------------------------------------------------------------- színek
COLOR_ORDER = [
    ("wolfgray", r"\bwolf ?gr[ae]y\b"),
    ("foliage", r"\bfoliage\b"),
    ("ranger", r"\branger\b"),
    ("coyote", r"\b(coyote|tan)\b"),
    ("odg", r"\b(odg|od green|olive drab|olive|oliv|olivgrün|olivgrun)\b"),
    ("fde", r"\b(fde|flat dark earth|dark earth|flache dunkle erde|dunkle erde)\b"),
    ("gray", r"\b(stealth gr[ae]y|gr[ae]y|grau|gris)\b"),
    ("black", r"\b(black|schwarz|blk|noir|nero|negro)\b"),
]


def color_of(text):
    t = nt(text)
    for key, rx in COLOR_ORDER:
        if re.search(rx, t):
            return key
    return None


# --------------------------------------------------------------------------- szabályok
def S(q, req=(), bad=(), brand=None, vreq=(), desc=(), multi=False, why=None, near=None, kit=None, note=None):
    """q: keresőkifejezések; brand: a márka (regex, a bolti márka+cím normalizált szövegében);
    req/bad: a bolti TERMÉKCÍM (normalizált) mintái – mind kell / egy sem lehet;
    vreq: a címben, változat-címben, cikkszámban VAGY leírásban kötelezően szereplő minták;
    desc: csomag-tartalom igazolása (cím vagy leírás); multi: azonos modell több kivitele -> a legolcsóbb,
    a többi 'alternatives'; why: az unmatched indoka; near: tájékoztató 'legközelebbi' minta."""
    if isinstance(q, str):
        q = [q]
    return dict(q=list(q), req=list(req), bad=list(bad), brand=brand, vreq=list(vreq), desc=list(desc),
                multi=multi, why=why, near=near, kit=kit, note=note)


FIREARM = "alapfegyver (lőfegyver)"
SET_BUIS = "szett (első + hátsó irányzék): csak külön első/hátsó tétel van, 'szett' nincs"

# ============================================================================ termék-szabályok
# A mintákat a bolti terméknév nt() szerinti normalizált alakjára írjuk (kisbetű, írásjel -> szóköz,
# '+' -> ' plus ', pont csak számjegyek között marad: "k2+" -> "k2 plus", "B.A.D." -> "bad").
MP, BCM_, B5_ = r"magpul", r"bcm|bravo company", r"b5"
PM_BAD = r"\blr\b|\bsr\b|lrsr|7\.62|308|gl9|\bak\b|akm|ar ?10|swiss|\bd 50|\bd 60|300 b|300 blk|blackout|6\.8|6\.5|grendel|x39|9mm|g36|stgw|10 30"
RULES = {
    # ---- alapfegyverek
    "schm-s4f": S(["Schmeisser AR15 S4F"], [r"schmeisser", r"s4f"], brand="schmeisser"),
    "schm-m4fl": S(["Schmeisser AR15 M4FL"], [r"schmeisser", r"m4fl"], brand="schmeisser"),
    "schm-m5fl": S(["Schmeisser AR15 M5FL"], [r"schmeisser", r"m5fl"], [r"dynamic"], brand="schmeisser"),
    "schm-dyn": S(["Schmeisser AR15 Dynamic"], [r"schmeisser", r"dynamic"], brand="schmeisser"),
    "dd-v7": S(["Daniel Defense DDM4 V7", "Daniel Defense"], [r"ddm4 v7 16\b"], [r"slw|\bs\b|sbr|\bpro\b|\blw\b|mk18"],
               brand="daniel defense",
               note="16\" cső, 5.56 NATO, fekete kivitel (a bolti címben: 'DDM4 V7 16\" 5.56 NATO Black')"),
    "bcm-recce16": S(["BCM RECCE-16", "BCM RECCE"], [r"recce 16"], brand=BCM_),
    # ---- markolatok
    "mp-moe": S(["Magpul MOE Grip", "MOE Grip"], [MP, r"\bmoe\b", r"\bgrip\b"],
                [r"\bk2|miad|\bak\b|akm|ak47|ak74|\bsl\b|plus|vertical|guard|mvg|afg|rvg|\bstop|bipod|sg9|\bxl\b|enhanced|angled|hand ?guard|\bsr\b"],
                brand=MP),
    "mp-moek2": S(["Magpul MOE K2 Grip", "MOE K2 Grip"], [MP, r"\bmoe k2\b", r"\bgrip\b"], [r"plus|\bxl\b|\bak"], brand=MP),
    "mp-moek2p": S(["Magpul MOE K2 Grip", "MOE K2 plus"], [MP, r"\bk2 plus\b", r"\bgrip\b"], brand=MP),
    "mp-miad": S(["Magpul MIAD Grip", "MIAD"], [MP, r"\bmiad\b", r"gen ?1.1"], [r"type 2|7.62|308|ar ?10|\blr\b|\bsr\b"], brand=MP,
                 note="AR-15 (5.56, Type 1) kivitel"),
    "bcm-g0": S(["BCM Gunfighter Grip Mod 0", "BCM Grip Mod 0"], [BCM_, r"\bgrip\b", r"\bmod 0\b"], [r"vertical|kag"], brand=BCM_),
    "bcm-g3": S(["BCM Gunfighter Grip Mod 3", "BCM Grip Mod 3"], [BCM_, r"\bgrip\b", r"\bmod 3\b"], [r"vertical|kag"], brand=BCM_),
    "b5-t22": S(["B5 Systems Type 22 P-Grip", "B5 Systems P-Grip"], [B5_, r"type 22"], brand=B5_),
    "b5-t23": S(["B5 Systems Type 23 P-Grip", "B5 Systems P-Grip"], [B5_, r"type 23"], brand=B5_),
    # ---- tusok
    "mp-ctr": S(["Magpul CTR Carbine Stock", "Magpul CTR"], [MP, r"\bctr\b", r"stock"], [r"ubr|prs|\bmoe\b"], multi=True, brand=MP,
                note="a bolt Mil-Spec/Commercial-Spec csöves kivitelt is árulhat; a data.js nem dönt"),
    "mp-moesl": S(["Magpul MOE SL Carbine Stock", "Magpul MOE SL"], [MP, r"moe sl\b", r"stock"],
                  [r"sl (k|m|s|r|a)\b|hk94|mp5|pistol|brace|\bak\b"], multi=True, brand=MP,
                  note="a bolt Mil-Spec/Commercial-Spec csöves kivitelt is árulhat; a data.js nem dönt"),
    "mp-moeslk": S(["Magpul MOE SL-K Carbine Stock", "Magpul MOE SL-K"], [MP, r"moe sl k\b", r"stock"], multi=True, brand=MP,
                   note="a bolt Mil-Spec/Commercial-Spec csöves kivitelt is árulhat; a data.js nem dönt"),
    "mp-ubr2": S(["Magpul UBR Gen 2", "Magpul UBR"], [MP, r"\bubr\b", r"gen ?(2|ii)\b"], brand=MP),
    "mp-prs3": S(["Magpul PRS Gen 3", "Magpul PRS"], [MP, r"\bprs\b", r"gen ?(3|iii)\b"], multi=True, brand=MP),
    "mp-prslite": S(["Magpul PRS Lite", "Magpul PRS"], [MP, r"prs lite"], brand=MP),
    "bcm-s0": S(["BCM Gunfighter Stock Mod 0", "BCM Stock"], [BCM_, r"stock", r"\bmod 0\b"], brand=BCM_),
    "bcm-s1": S(["BCM Gunfighter Stock Mod 1", "BCM Stock"], [BCM_, r"stock", r"\bmod 1\b"], brand=BCM_),
    "b5-bravo": S(["B5 Systems Bravo Stock", "B5 Systems Bravo"], [B5_, r"bravo"], brand=B5_),
    "b5-esop": S(["B5 Systems Enhanced SOPMOD", "B5 Systems SOPMOD"], [B5_, r"sopmod"], [r"\bbasic\b"], brand=B5_),
    "b5-prec": S(["B5 Systems Precision Stock", "B5 Systems Precision"], [B5_, r"precision"], brand=B5_),
    # ---- tustalpak
    "mp-pad030": S(["Magpul Rubber Butt-Pad", "Magpul Butt-Pad"], [MP, r"rubber butt ?pad", r"0.30|\.30"], [r"moe sl|extended|enhanced"], brand=MP),
    "mp-pad055": S(["Magpul Extended Rubber Butt-Pad", "Magpul Butt-Pad"], [MP, r"rubber butt ?pad", r"0.55"], [r"moe sl"], brand=MP),
    "mp-pad070": S(["Magpul Enhanced Rubber Butt-Pad", "Magpul Butt-Pad"], [MP, r"rubber butt ?pad", r"0.70", r"enhanced"], [r"moe sl"], brand=MP),
    "mp-padsl": S(["Magpul MOE SL Enhanced Butt-Pad", "Magpul Butt-Pad"], [MP, r"moe sl", r"butt ?pad", r"0.70"], brand=MP),
    # ---- red dot / holo (szerelékkel)
    "ap-t2": S(["Aimpoint Micro T-2", "Aimpoint T-2"], [r"aimpoint", r"micro t ?2\b", r"\blrp\b", r"\b39"], [r"without|ohne"], brand="aimpoint", kit=True,
               why="csomag (T-2 + LRP szerelék): nincs olyan bolti tétel, amely a T-2-t LRP szerelékkel és 39 mm távtartóval együtt, egy cikkszámon adná"),
    "ap-m5": S(["Aimpoint Comp M5", "Aimpoint CompM5"], [r"aimpoint", r"\bcomp ?m5\b", r"\blrp\b"], [r"without|ohne"], brand="aimpoint", kit=True,
               why="csomag (CompM5 + LRP szerelék): nincs ilyen bolti tétel"),
    "ap-pro": S(["Aimpoint Patrol Rifle Optic", "Aimpoint PRO"], [r"aimpoint", r"\bpro\b|patrol rifle optic"], [r"magnif|mount only"], brand="aimpoint", kit=True,
                desc=[r"qrp ?2"], why="csomag (PRO + QRP2 szerelék): a bolti termék nem igazolja a QRP2 szerelék tartozékként való szállítását"),
    "ap-acro": S(["Aimpoint ACRO P-2", "Aimpoint ACRO"], [r"aimpoint", r"ac?r?c?ro p ?2|arcro p ?2|acro p ?2"], [r"montage|mount|spacer"], brand="aimpoint", kit=True,
                 desc=[r"picatinny"], near=r"ac?r?c?ro",
                 why="csomag (ACRO P-2 + Picatinny talp): a bolti termék nem igazolja a Picatinny talp tartozékként való szállítását"),
    "eo-exps3": S(["EOTech EXPS3", "EOTech HWS EXPS3"], [r"exps3"], [r"dcr|exps3 ?[1-9]"], vreq=[r"exps3 ?0\b"], brand="eotech"),
    "eo-exps2": S(["EOTech EXPS2", "EOTech HWS EXPS2"], [r"exps2"], [r"exps2 ?[1-9]"], vreq=[r"exps2 ?0\b"], brand="eotech"),
    "eo-xps2": S(["EOTech XPS2", "EOTech HWS XPS2"], [r"\bxps2"], [r"exps|xps2 ?[1-9]"], vreq=[r"xps2 ?0\b"], brand="eotech"),
    "hs-510c": S(["Holosun HS510C", "Holosun 510C"], [r"holosun", r"\bhs510c\b"], [r"\bhe510|elite"], brand="holosun"),
    "hs-aems": S(["Holosun AEMS"], [r"holosun", r"\baems\b"], [r"core|macro|micro|evo|\bpro\b|\bmax\b|\bx2\b|aems pro"], brand="holosun", near=r"aems",
                 why="az eredeti 'AEMS' típus nincs a boltban (csak más AEMS-modellek: Core/Macro/Pro/Micro)"),
    "hs-403b": S(["Holosun HS403B", "Holosun 403B"], [r"holosun", r"\bhs403b\b", r"riser|mount|montage|spacer|erhöh|magas"], brand="holosun", kit=True, near=r"403",
                 why="a HS403B + magasító csomag nincs a boltban"),
    "tj-mro": S(["Trijicon MRO"], [r"trijicon", r"\bmro\b", r"\bhd\b|red dot|sight|visier|leuchtpunkt", r"lower 1 3"], [r"cover|quick release|screw|schraube"], brand="trijicon", kit=True, near=r"\bmro\b",
                why="csomag (MRO HD + Lower 1/3 szerelék): a bolt a szerelékeket külön tételként árulja, csomag nincs"),
    "vx-uh1": S(["Vortex UH-1 Gen II", "Vortex UH-1"], [r"vortex|amg", r"uh 1", r"gen ?(ii|2)\b"], brand="vortex"),
    "vx-sparc": S(["Vortex SPARC AR", "Vortex SPARC"], [r"vortex", r"sparc", r"\bar\b", r"riser|magas|erhöh|spacer"], [r"solar"], brand="vortex", kit=True, near=r"sparc",
                  why="'SPARC AR + magasító' csomag nincs a boltban"),
    "vx-crossfire": S(["Vortex Crossfire Red Dot", "Vortex Crossfire"], [r"vortex", r"crossfire", r"red dot|rotpunkt|leuchtpunkt"], [r"solar|hd|ii\b"], brand="vortex", kit=True,
                      desc=[r"riser|magasít|erhöh|spacer"], near=r"crossfire",
                      why="'Crossfire Red Dot + magasító' csomag: a bolti termék nem igazolja a magasító (riser) mellékelését"),
    # ---- LPVO
    "vx-se16": S(["Vortex Strike Eagle 1-6x24", "Vortex Strike Eagle"], [r"vortex", r"strike eagle", r"1 6x24"], [r"ffp|ebr"], multi=True, brand="vortex",
                 note="a data.js nem nevezi meg a retikulumot; a bolti kivitel a címben szerepel"),
    "vx-se18": S(["Vortex Strike Eagle 1-8x24", "Vortex Strike Eagle"], [r"vortex", r"strike eagle", r"1 8x24"], [r"ffp|ebr"], multi=True, brand="vortex",
                 note="a data.js nem nevezi meg a retikulumot; a bolti kivitel a címben szerepel"),
    "vx-pst16": S(["Vortex Viper PST Gen II 1-6x24", "Vortex Viper PST"], [r"vortex", r"viper pst", r"gen ?(ii|2)\b", r"1 6x24"], multi=True, brand="vortex"),
    "vx-razor110": S(["Vortex Razor Gen III 1-10x24", "Vortex Razor"], [r"vortex", r"razor", r"gen ?(iii|3)\b", r"1 10x24"], multi=True, brand="vortex"),
    "tj-acog": S(["Trijicon ACOG TA31", "Trijicon ACOG"], [r"trijicon|acog", r"\bta31"], brand="trijicon|acog", near=r"acog",
                 why="a TA31 típusjelzésű ACOG nincs a boltban"),
    "tj-credo16": S(["Trijicon Credo"], [r"trijicon", r"credo", r"1 6x24"], brand="trijicon"),
    "tj-vcog": S(["Trijicon VCOG"], [r"trijicon", r"vcog", r"1 8x28"], brand="trijicon"),
    "eo-vudu16": S(["EOTech Vudu 1-6x24", "EOTech Vudu"], [r"eotech", r"vudu", r"1 6x24"], multi=True, brand="eotech"),
    "eo-vudu110": S(["EOTech Vudu 1-10x28", "EOTech Vudu"], [r"eotech", r"vudu", r"1 10x28"], multi=True, brand="eotech"),
    # ---- optikaszerelék
    "gs-sp30": S(["Geissele Super Precision", "Geissele Scope Mount"], [r"geissele", r"super precision", r"30"], [r"34"], brand="geissele"),
    "vx-cant30": S(["Vortex Sport Cantilever", "Vortex Cantilever"], [r"vortex", r"sport cantilever", r"30 ?mm"], brand="vortex"),
    # ---- nagyítók
    "eo-g33": S(["EOTech G33", "EOTech Magnifier"], [r"eotech", r"\bg33\b"], vreq=[r"sts"], brand="eotech", kit=True, near=r"g33",
                why="csomag (G33 + STS szerelék): a bolti tétel nem igazolja az STS szerelék mellékelését"),
    "eo-g43": S(["EOTech G43", "EOTech Magnifier"], [r"eotech", r"\bg43\b"], vreq=[r"micro"], brand="eotech", kit=True, near=r"g43",
                why="csomag (G43 + Micro szerelék) nincs a boltban"),
    "ap-3x": S(["Aimpoint 3XMag-1", "Aimpoint 3X Magnifier"], [r"aimpoint", r"3x ?mag ?1", r"flip"], brand="aimpoint", kit=True, near=r"3x ?mag",
               why="csomag (3XMag-1 + Flip szerelék): a bolt a nagyítót és a szerelékeket külön árulja / nincs ilyen tétel"),
    "hs-hm3x": S(["Holosun HM3X", "Holosun Magnifier"], [r"holosun", r"\bhm3x\b"], brand="holosun", near=r"hm3x"),
    "vx-m3x": S(["Vortex Micro 3X Magnifier", "Vortex Magnifier"], [r"vortex", r"micro ?3x"], vreq=[r"flip"], brand="vortex", kit=True, near=r"magnifier|3x",
                why="a 'Micro3X + Flip' nincs a boltban"),
    # ---- BUIS
    "mp-mbus3": S(["Magpul MBUS 3", "Magpul MBUS"], [MP, r"mbus 3", r"set|front (and|&) rear|front rear|vorder"], [r"\bpro\b"], brand=MP, kit=True, near=r"mbus 3", why=SET_BUIS),
    "mp-mbuspro": S(["Magpul MBUS Pro", "Magpul MBUS"], [MP, r"mbus pro", r"set|front (and|&) rear|front rear|vorder"], [r"offset"], brand=MP, kit=True, near=r"mbus pro", why=SET_BUIS),
    "mp-mbusoff": S(["Magpul MBUS Pro Offset", "Magpul MBUS"], [MP, r"mbus pro offset", r"set|front (and|&) rear|front rear|vorder"], brand=MP, kit=True, near=r"mbus pro offset", why=SET_BUIS),
    # ---- lámpák
    "sf-m600": S(["SureFire M600", "SureFire Scout Light"], [r"surefire", r"m600 ?df"], brand="surefire", near=r"m600"),
    "sf-m640": S(["SureFire M640", "SureFire Scout Light"], [r"surefire", r"m640 ?df"], [r"m640 ?dft|turbo"], brand="surefire", near=r"m640"),
    "sf-m300": S(["SureFire M300", "SureFire Scout Light"], [r"surefire", r"\bm300"], [r"m300 ?t|turbo|m340"], multi=True, brand="surefire", near=r"m3[04]0"),
    "sf-x300": S(["SureFire X300", "SureFire X300U"], [r"surefire", r"x300 ?u ?b\b"], brand="surefire", near=r"x300"),
    "sl-hlx": S(["Streamlight ProTac Rail Mount", "Streamlight HL-X"], [r"streamlight", r"pro ?tac rail mount", r"\bhl x\b"], [r"hl x pro"], brand="streamlight", near=r"pro ?tac"),
    "sl-tlr1": S(["Streamlight TLR-1 HL", "Streamlight TLR-1"], [r"streamlight", r"tlr 1 hl\b"], [r"tlr 1 hl (g|x|pro)\b|hl x|flash"], brand="streamlight", near=r"tlr 1"),
    # ---- M-LOK
    "mp-rvg": S(["Magpul RVG", "Magpul M-LOK RVG"], [MP, r"\brvg\b", r"\bgrip\b", r"m ?lok"], [r"adapter"], brand=MP, near=r"\brvg\b",
                why="az 'M-LOK RVG' függőleges markolat nincs a boltban"),
    "mp-hstop": S(["Magpul Hand Stop", "Magpul M-LOK Hand Stop"], [MP, r"hand stop", r"m ?lok"], [r"skinny|ar ?15 ?stop"], brand=MP, kit=True),
    "bcm-vg3": S(["BCM Vertical Grip", "BCM Gunfighter Vertical Grip"], [BCM_, r"vertical grip", r"\bmod 3\b"], brand=BCM_, near=r"vertical grip"),
    "bcm-kag": S(["BCM KAG", "BCM Kinesthetic Angled Grip"], [BCM_, r"\bkag\b|kinesthetic"], brand=BCM_),
    "mp-rail5": S(["Magpul M-LOK Rail Section", "Magpul Rail Section"], [MP, r"m ?lok", r"rail section", r"\b5 slots?\b", r"alumin"], brand=MP, near=r"rail section"),
    "mp-qd": S(["Magpul M-LOK QD", "Magpul QD Sling Mount"], [MP, r"m ?lok", r"\bqd\b", r"sling mount"], [r"rsa|rail"], brand=MP),
    "mp-cover": S(["Magpul M-LOK Rail Cover", "Magpul Rail Cover"], [MP, r"m ?lok", r"rail cover", r"type 2"], brand=MP),
    "mp-cable": S(["Magpul M-LOK Cable", "Magpul Cable Management"], [MP, r"m ?lok", r"cable"], brand=MP,
                  why="a Magpul M-LOK Cable Management Kit nincs a boltban"),
    # ---- bipod
    "mp-bipod": S(["Magpul Bipod", "Magpul Bipod M-LOK"], [MP, r"bipod", r"m ?lok"], [r"moe|alumin|arms|picatinny|1913"], brand=MP, near=r"bipod|zweibein"),
    "hr-sbrm": S(["Harris S-BRM", "Harris Bipod"], [r"harris", r"s ?brm", r"m ?lok"], brand="harris", kit=True, near=r"harris"),
    # ---- torkolat
    "sf-warcomp": S(["SureFire WarComp", "SureFire Muzzle"], [r"surefire", r"warcomp", r"556|5.56|223"], [r"rc2|socom"], brand="surefire", near=r"warcomp"),
    "sf-3p": S(["SureFire SF3P", "SureFire Muzzle"], [r"surefire", r"sf3p", r"556|5.56|223"], brand="surefire", near=r"sf3p"),
    "bcm-comp0": S(["BCM Compensator Mod 0", "BCM Compensator"], [BCM_, r"comp", r"\bmod 0\b"], brand=BCM_, near=r"comp"),
    "sf-rc2": S(["SureFire SOCOM556-RC2", "SureFire RC2"], [r"surefire", r"rc2"], [r"adapter ?only"], brand="surefire", near=r"rc2|socom"),
    # ---- elsütők
    "gs-ssae": S(["Geissele SSA-E", "Geissele SSA"], [r"geissele", r"\bssa e\b"], brand="geissele"),
    "gs-ssa": S(["Geissele SSA", "Geissele Trigger"], [r"geissele", r"\bssa\b"], [r"ssa e\b", r"mcx|mpx|scar|hk416"], brand="geissele", near=r"\bssa\b"),
    "gs-sde": S(["Geissele SD-E", "Geissele Super Dynamic"], [r"geissele", r"\bsd e\b|super dynamic enhanced"], brand="geissele", near=r"\bsd"),
    "gs-s3g": S(["Geissele S3G", "Geissele Super 3 Gun"], [r"geissele", r"\bs3g\b|super 3 gun"], brand="geissele"),
    # ---- felhúzókar
    "rd-raptor": S(["Radian Raptor"], [r"radian|raptor", r"\braptor\b"], [r"raptor (lt|sd|sl)|\blt\b|\bsd\b"], brand="radian|raptor", near=r"raptor"),
    "rd-raptorlt": S(["Radian Raptor-LT", "Radian Raptor"], [r"radian|raptor", r"raptor lt"], brand="radian|raptor", near=r"raptor"),
    "rd-raptorsd": S(["Radian Raptor-SD", "Radian Raptor"], [r"radian|raptor", r"raptor sd"], brand="radian|raptor", near=r"raptor"),
    "bcm-ch4": S(["BCM Charging Handle Mod 4", "BCM Charging Handle"], [BCM_, r"charging handle", r"\bmod 4\b"], brand=BCM_, near=r"charging handle"),
    "bcm-ch3": S(["BCM Charging Handle Mod 3", "BCM Charging Handle"], [BCM_, r"charging handle", r"\bmod 3\b"], brand=BCM_, near=r"charging handle"),
    "gs-ach": S(["Geissele Airborne", "Geissele Charging Handle"], [r"geissele", r"airborne"], brand="geissele", near=r"charging handle"),
    # ---- kezelőszervek
    "rd-talon": S(["Radian Talon", "Radian Safety Selector"], [r"radian|talon", r"\btalon\b", r"safety|selector|sicherung"], [r"\bkit\b"], brand="radian|talon", near=r"talon"),
    "mp-bad": S(["Magpul B.A.D. Lever", "Magpul BAD Lever"], [MP, r"\bbad lever\b"], brand=MP),
    "mp-trigguard": S(["Magpul MOE Enhanced Trigger Guard", "Magpul Trigger Guard"], [MP, r"moe enhanced trigger guard"], brand=MP, near=r"trigger guard",
                      why="a 'MOE Enhanced Trigger Guard' típus nincs a boltban"),
    # ---- tárak
    "mp-pm10": S(["Magpul PMAG 10", "PMAG 10"], [MP + r"|pmag", r"\bpmag 10\b", r"gen m3"], [PM_BAD], brand=MP + r"|pmag", near=r"pmag"),
    "mp-pm20": S(["Magpul PMAG 20", "PMAG 20"], [MP + r"|pmag", r"\bpmag 20\b", r"gen m3"], [PM_BAD], brand=MP + r"|pmag", near=r"pmag"),
    "mp-pm30": S(["Magpul PMAG 30", "PMAG 30"], [MP + r"|pmag", r"\bpmag 30\b", r"gen m3"], [PM_BAD, r"window|maglevel|\bm2\b|moe"], brand=MP + r"|pmag", near=r"pmag"),
    "mp-pm30w": S(["Magpul PMAG 30 Window", "PMAG 30"], [MP + r"|pmag", r"\bpmag 30\b", r"window", r"gen m3"], [PM_BAD, r"\bm2\b|moe"], brand=MP + r"|pmag", near=r"pmag"),
    "mp-pm40": S(["Magpul PMAG 40", "PMAG 40"], [MP + r"|pmag", r"\bpmag 40\b", r"gen m3"], [PM_BAD], brand=MP + r"|pmag", near=r"pmag"),
    "mp-d60": S(["Magpul D-60", "PMAG D-60"], [MP + r"|pmag", r"\bd 60\b"], brand=MP + r"|pmag", near=r"\bd 50\b|\bd 60\b",
                why="a D-60 dobtár nincs a boltban"),
    # ---- szíjak
    "mp-ms1": S(["Magpul MS1 Sling", "Magpul MS1"], [MP, r"\bms1\b", r"sling"], [r"adapter|qdm|ms3|ms4|padded|mount"], brand=MP, near=r"\bms[134]\b"),
    "mp-ms4": S(["Magpul MS4 Dual QD", "Magpul MS4"], [MP, r"\bms4\b", r"dual qd", r"sling"], [r"adapter|qdm|ms1|ms3"], brand=MP, near=r"\bms[134]\b",
                note="a bolt a Gen 2 kivitelt árulhatja"),
    "bfg-vickers": S(["Blue Force Gear Vickers", "Vickers Combat Application Sling"], [r"blue force|bfg|vickers", r"vickers", r"combat applications? sling"], [r"padded|221"], brand="blue force|bfg|vickers", near=r"vickers"),
}

# ============================================================================ PrestaShop 1.6 adapter (HTML)
LANG = "/de"


def _canon(url):
    return url.split("?")[0].split("#")[0]


def _js_json(text, name):
    m = re.search(r"var\s+%s\s*=\s*" % re.escape(name), text)
    if not m:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[m.end():])
        return obj
    except ValueError:
        return None


def _parse_cards(s):
    out = []
    for li in s.select("ul.product_list > li"):
        a = li.select_one("a.product-name")
        if not a or not a.get("href"):
            continue
        pr = li.select_one(".right-block .content_price .price") or li.select_one(".content_price .price")
        old = li.select_one(".right-block .old-price")
        av = li.select_one(".right-block .availability")
        out.append(dict(title=norm(htmllib.unescape(a.get("title") or a.get_text(" "))), brand="",
                        url=_canon(a["href"]), price_from=parse_price(pr.get_text(" ")) if pr and "CHF" in pr.get_text() else None,
                        list_from=parse_price(old.get_text(" ")) if old else None, ab=False,
                        stock_text=norm(av.get_text(" ")) if av else ""))
    return out


def SEARCH(http, q):
    out, seen = [], set()
    for page in (1, 2, 3, 4):
        prm = {"controller": "search", "search_query": q, "orderby": "position", "orderway": "desc", "n": 60}
        if page > 1:
            prm["p"] = page
        try:
            body, _ = http.get(BASE + LANG + "/suche", prm)
        except urllib.error.HTTPError:
            break  # a bolt kereső egyes kifejezésekre 500-at ad -> nincs találat
        s = soup(body)
        cards = _parse_cards(s)
        new = [c for c in cards if c["url"] not in seen]
        for c in new:
            seen.add(c["url"])
        out += new
        cnt = s.select_one(".heading-counter")
        m = re.search(r"(\d+)", cnt.get_text()) if cnt else None
        if not new or not m or len(out) >= int(m.group(1)):
            break
    return out


def EXPAND(http, prod):
    body, final = http.get(prod["url"])
    s = soup(body)
    desc = " ".join(norm(e.get_text(" ")) for e in s.select("#short_description_content, #idTab1") if e)
    img = s.select_one("#bigpic")
    img = img.get("src") if img else None
    det = dict(desc=desc[:4000], image=img, weight_g=None, dims=None, variants=[])
    pe = s.select_one("#our_price_display")
    price = parse_price(pe.get_text(" ")) if pe and "CHF" in pe.get_text() else None
    po = s.select_one("#old_price_display .price")
    old = parse_price(po.get_text(" ")) if po and po.get_text(strip=True) else None
    sku_el = s.select_one("[itemprop=sku]")
    sku = (sku_el.get("content") or sku_el.get_text(strip=True)) if sku_el else None
    av = s.select_one("[itemprop=availability]")
    in_stock = None
    if av is not None:
        href = av.get("href") or ""
        in_stock = True if "InStock" in href else (False if "OutOfStock" in href else None)
    combos = _js_json(body, "combinations") or {}
    groups = {g.get("id_attribute_group"): g.get("group") for g in (_js_json(body, "attributesCombinations") or [])}
    sel = {int(o.get("value")) for o in s.select("#attributes option[selected], #attributes input:checked") if (o.get("value") or "").isdigit()}
    default_impact = None
    for c in combos.values():
        if set(c.get("attributes") or []) == sel and sel:
            default_impact = c.get("price")
    if default_impact is None and combos:
        default_impact = next(iter(combos.values())).get("price")
    if combos:
        for cid, c in combos.items():
            labels = c.get("attributes_values") or {}
            same_price = (c.get("price") == default_impact) and not c.get("specific_price")
            qty = c.get("quantity")
            det["variants"].append(dict(
                title=" / ".join(str(v) for v in labels.values()), options={groups.get(k, k): v for k, v in labels.items()},
                sku=c.get("reference") or sku, price=price if same_price else None, list=old if same_price else None,
                in_stock=(qty > 0) if isinstance(qty, int) else in_stock,
                stock_text=("Lagerbestand: %s" % qty) if isinstance(qty, int) else "",
                url=_canon(final), image=img))
    if not det["variants"]:
        det["variants"].append(dict(title="", options={}, sku=sku, price=price, list=old, in_stock=in_stock,
                                    stock_text=prod.get("stock_text") or "", url=_canon(final), image=img))
    return det

# ============================================================================ egyeztetés és kimenet
def fmt_chf(p):
    return ("%.2f" % p) if isinstance(p, (int, float)) else "?"


def hay_prod(p):
    return nt("%s %s" % (p.get("brand") or "", p.get("title") or ""))


def title_match(rule, p):
    h = hay_prod(p)
    if rule["brand"] and not re.search(rule["brand"], h):
        return False
    if not all(re.search(r, h) for r in rule["req"]):
        return False
    return not any(re.search(r, h) for r in rule["bad"])


def near_list(rule, cands, hits):
    pat = rule.get("near") or (rule["req"][-1] if rule["req"] else None)
    out = []
    hu = {c["url"] for c in hits}
    for c in cands:
        if c["url"] in hu:
            continue
        h = hay_prod(c)
        if rule["brand"] and not re.search(rule["brand"], h):
            continue
        if pat and re.search(pat, h):
            out.append(c)
    return out[:4]


def near_text(near):
    return " | ".join("%s (%s%s CHF, %s)" % (norm(c["title"]), "ab " if c.get("ab") else "",
                                             fmt_chf(c.get("price_from")), c["url"]) for c in near)


def stock_of(v):
    return v.get("in_stock"), v.get("stock_text") or ""


def v_hay(prod, det, v):
    return nt(" ".join([prod.get("brand") or "", prod["title"], v.get("title") or "", v.get("sku") or "",
                        v.get("mpn") or "", " ".join((v.get("options") or {}).values()), det.get("desc") or ""]))


def build_item(prod, det, variants, palette, rule, alts):
    """Változatokból a data.js-hez illő tétel. -> ('ok', item) | ('no', indok)"""
    one = len(variants) == 1
    for v in variants:
        txt = " ".join([v.get("title") or ""] + list((v.get("options") or {}).values()))
        v["color"] = v.get("color_force") or color_of(txt) or (color_of(prod["title"]) if one else None)
    note = []
    if palette is not None:
        colored = {}
        for v in sorted(variants, key=lambda x: x["price"] if x.get("price") is not None else 1e9):
            if v["color"] in palette and v["color"] not in colored and v.get("price") is not None:
                colored[v["color"]] = v
        others = sorted({v["color"] for v in variants if v["color"] and v["color"] not in palette})
        nocolor = [v for v in variants if not v["color"] and v.get("price") is not None]
        if colored:
            main_key = "black" if "black" in colored else [k for k in palette if k in colored][0]
            main = colored[main_key]
            if others:
                note.append("a bolt további színei, amelyek nincsenek a palettában, kimaradtak: " + ", ".join(others))
        elif len(nocolor) == 1:
            main, colored = nocolor[0], {}
            note.append("a bolt a színt nem nevezi meg (a cím/változat nem tartalmaz színt)" +
                        ("; más színek: " + ", ".join(others) if others else ""))
        elif len(nocolor) > 1:
            return "no", "ok: több, színnel nem azonosítható változat (%s) – nem dönthető el; változatok: %s" % (
                "bizonytalan", " | ".join("%s (%s CHF)" % (v.get("title"), fmt_chf(v.get("price"))) for v in nocolor[:5]))
        else:
            return "no", "ok: a bolt csak a paletta-n kívüli színben (%s) árulja" % (", ".join(others) or "ismeretlen")
    else:
        priced = [v for v in variants if v.get("price") is not None]
        if len(priced) == 1:
            main, colored = priced[0], {}
        else:
            blk = [v for v in priced if v["color"] == "black"]
            if len(blk) == 1:
                main, colored = blk[0], {}
                note.append("több színváltozat van, a fekete ára szerepel; a többi: " +
                            ", ".join("%s %s CHF" % (v.get("title"), fmt_chf(v.get("price"))) for v in priced if v is not blk[0]))
            else:
                return "no", "ok: több változat, amelyek közül a data.js alapján nem választható (%s)" % (
                    " | ".join("%s (%s CHF)" % (v.get("title"), fmt_chf(v.get("price"))) for v in priced[:6]))
    pt = norm(prod["title"])
    vt = norm(main.get("title") or "")
    title = pt if (not vt or slugify(vt) in slugify(pt) or one) else "%s – %s" % (pt, vt)
    item = dict(title=title, price_chf=main["price"],
                list_chf=main["list"] if main.get("list") and main["list"] > main["price"] else None,
                url=main.get("url") or prod["url"], image=main.get("image") or det.get("image") or prod.get("image"),
                in_stock=main.get("in_stock"), stock_text=main.get("stock_text") or "",
                sku=main.get("sku"), mpn=main.get("mpn"), weight_g=det.get("weight_g"), dims=det.get("dims"))
    if colored:
        item["colors_prices"] = {k: dict(price_chf=v["price"],
                                         list_chf=v["list"] if v.get("list") and v["list"] > v["price"] else None,
                                         url=v.get("url") or prod["url"], in_stock=v.get("in_stock"),
                                         title=norm(v.get("title") or prod["title"]) if not one else pt)
                                 for k, v in colored.items()}
    if rule.get("note"):
        note.insert(0, rule["note"])
    if note:
        item["note"] = "; ".join(note)
    if alts:
        item["alternatives"] = [dict(title=norm(c["title"]), url=c["url"], price_from_chf=c.get("price_from")) for c in alts]
        item["match"] = "variant"
    else:
        item["match"] = "exact"
    return "ok", item


def decide(http, pid, brand, name, palette, rule, qcache):
    queries = rule["q"] if rule else ["%s %s" % (brand, name)]
    cands, seen = [], set()
    for q in queries:
        if q not in qcache:
            qcache[q] = SEARCH(http, q)
        for c in qcache[q]:
            if c["url"] not in seen:
                seen.add(c["url"])
                cands.append(c)
    if rule is None:
        return "no", "ok: nincs szabály ehhez az azonosítóhoz"
    hits = [c for c in cands if title_match(rule, c)]
    if not hits:
        near = near_list(rule, cands, [])
        reason = "ok: " + (rule["why"] or "nincs a boltban (a keresés nem adott pontosan azonos terméket)")
        if near:
            reason += "; legközelebbi bolti termék(ek), NEM azonos: " + near_text(near)
        return "no", reason
    hits.sort(key=lambda c: (c.get("price_from") is None, c.get("price_from") or 0))
    cols = [color_of(c["title"]) for c in hits]
    nn = [x for x in cols if x]
    color_mode = len(hits) > 1 and nn and len(set(nn)) == len(nn) and cols.count(None) <= 1
    if len(hits) > 1 and not rule["multi"] and not color_mode:
        return "no", "ok: több, a data.js alapján nem megkülönböztethető bolti termék illik rá (nem egyértelmű): " + near_text(hits[:5])
    if color_mode:
        # a bolt a színeket külön termékként árulja: a színmentes (alap) tétel = fekete
        main = next((c for c, col in zip(hits, cols) if col in (None, "black")), hits[0])
        alts, variants, det = [], [], None
        for c, col in zip(hits, cols):
            d = EXPAND(http, c)
            if c is main:
                det = d
            for v in d.get("variants") or []:
                v = dict(v)
                v["title"] = c["title"] if not v.get("title") else "%s / %s" % (c["title"], v["title"])
                v["color_force"] = col or "black"
                variants.append(v)
    else:
        main, alts = hits[0], hits[1:]
        det = EXPAND(http, main)
        variants = det.get("variants") or []
    if not variants:
        return "no", "ok: a termékoldalról nem olvasható ki ár (%s)" % main["url"]
    if rule["vreq"]:
        variants = [v for v in variants if all(re.search(r, v_hay(main, det, v)) for r in rule["vreq"])]
        if not variants:
            near = near_list(rule, cands, [])
            return "no", "ok: " + (rule["why"] or "a kért kivitel/jelölés nincs a boltban") + \
                   "; legközelebbi bolti termék(ek), NEM azonos: " + near_text([main] + [c for c in near if c["url"] != main["url"]][:3])
    if rule["desc"]:
        h = nt(main["title"] + " " + (det.get("desc") or ""))
        if not all(re.search(r, h) for r in rule["desc"]):
            return "no", "ok: " + (rule["why"] or "a csomag tartalma nem igazolható") + \
                   "; legközelebbi bolti termék(ek), NEM azonos: " + near_text([main])
    kind, res = build_item(main, det, variants, palette, rule, alts)
    if kind == "no" and "paletta" in res:
        res += "; bolti termék: " + near_text([main])
    if kind == "ok" and color_mode and cols[hits.index(main)] is None:
        res["note"] = "; ".join(x for x in [res.get("note"), "a színjelölés nélküli alaptermék feketének véve (a többi szín külön termékként szerepel a boltban)"] if x)
    return kind, res


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás, stdout)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/%s.json)" % SLUG)
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    args = ap.parse_args(argv)
    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    http = Http(args.cache)
    items, unmatched, qcache, blocked = {}, {}, {}, None
    for i, (pid, brand, name, palette) in enumerate(entries, 1):
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), pid, brand, name))
        if blocked:
            unmatched[pid] = "ok: a bolt blokkolt, nem ellenőrizhető (%s)" % blocked
            continue
        try:
            kind, res = decide(http, pid, brand, name, palette, RULES.get(pid), qcache)
        except Blocked as e:
            blocked = "%s – a bolttal azonnal leálltunk, a védelmet nem kerültük meg" % e
            unmatched[pid] = "ok: a bolt blokkolt, nem ellenőrizhető (%s)" % blocked
            sys.stderr.write("   BLOKKOLT: %s\n" % e)
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
    run()
