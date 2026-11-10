#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
armatum.ch (Armatum GmbH, Fulenbach SO) ár-gyűjtő az AR-15 konfigurátorhoz.
FIGYELEM: ez NEM azonos az armamat.ch-val (TM Trading GmbH, Cham) - külön bolt.

Futtatás (a repo gyökeréből):
    python3 tools/prices_armatum.py                       # minden termék -> assets/prices/armatum.json
    python3 tools/prices_armatum.py --only mp-moe,eo-xps2 # csak néhány id (stdout, ha nincs --out)
    python3 tools/prices_armatum.py --cache /tmp/armatum  # HTTP-gyorsítótár (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  1. A bolt WooCommerce, a nyilvános Store API-t használja:
     /wp-json/wc/store/v1/products?search=<márka>&per_page=100  (márkánként néhány oldal),
     változatos (variable) terméknél /wp-json/wc/store/v1/products/<változat-id>.
  2. Minden data.js id-hoz a RULES táblában van a bolti név PONTOS (fullmatch) mintája
     (normalizált, kisbetűs címre; a {C} helyén opcionális színszó), ill. cikkszám-minta.
     Szabály nélküli / None mintájú id -> unmatched (indok + a legközelebbi bolti termékek).
  3. A szín a cikkszám végéből (MAGxxx-BLK/-FDE/...), a névből vagy a "Farbe" attribútumból jön,
     a data.js palettakulcsaira (black, fde, odg, gray, ...) képezve.
  4. Árat soha nem becsül, nem számol, nem vált át: a Store API "price" (fizetendő) és
     "regular_price" (áthúzott, ha akciós) mezőit veszi át.

Udvariasság: egyetlen szálon, kérések között >= 1,2 s, böngésző User-Agent.
Botvédelem (403, captcha/Cloudflare-kihívás, tartós 429) esetén azonnal leáll ("blocked").
"""
import argparse
import datetime
import hashlib
import html
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
    from bs4 import BeautifulSoup  # noqa: F401  (a többi bolti szkripttel azonos függőség)
except ImportError:  # pragma: no cover
    sys.exit("Hiányzik a beautifulsoup4: pip install beautifulsoup4 lxml")

SHOP = "armatum.ch"
BASE = "https://armatum.ch"
HOSTS = ("armatum.ch", "www.armatum.ch")
API = BASE + "/wp-json/wc/store/v1/products"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "armatum.json")

QUERIES = ["magpul", "eotech", "holosun", "aimpoint", "trijicon", "vortex", "surefire", "streamlight",
           "harris", "radian", "geissele", "bcm", "bravo company", "b5 systems", "blue force", "vickers",
           "schmeisser", "daniel defense"]


# =========================================================================== HTTP
class Blocked(Exception):
    pass


class Http:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir
        self.last = 0.0
        self.n = 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def get(self, url, accept=None):
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
        if accept:
            headers["Accept"] = accept
        last_err = None
        n429 = 0
        for attempt in range(4):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                    body = r.read().decode("utf-8", "replace")
                if re.search(r"<title>\s*(Just a moment|Attention Required|Verifying|Access denied)", body, re.I) \
                        or "cf-chl-" in body or "g-recaptcha" in body:
                    raise Blocked("botvédelmi (Cloudflare/captcha) oldal: " + url)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise Blocked("HTTP 403: " + url)
                if e.code == 429:
                    n429 += 1
                    if n429 >= 3:
                        raise Blocked("tartós HTTP 429: " + url)
                    time.sleep(30 * n429)
                    continue
                if e.code in (500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(10 * (attempt + 1))
        raise RuntimeError("letöltés sikertelen: %s (%s)" % (url, last_err))


# =========================================================================== közös segédek
def norm(s):
    s = unicodedata.normalize("NFKC", html.unescape(s or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def key(s):
    """Összehasonlító kulcs: kisbetű, egységes idézőjel/kötőjel, a ' - ', ', ', ' / ' elválasztók szóközzé."""
    s = norm(s).lower()
    s = re.sub(r"[®™©]", "", s)
    s = s.replace("×", "x").replace("″", '"').replace("“", '"').replace("”", '"').replace("’", "'")
    s = re.sub(r"[–—‐−]", "-", s)
    s = re.sub(r"\s*[,|;]\s*", " ", s)
    s = re.sub(r"\s+[-/]+\s+", " ", s)
    s = re.sub(r"\s+-(?=[a-zäöü])", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -")


# színszavak -> data.js palettakulcs ('?' = nem egyértelmű, a cikkszám/attribútum dönt)
COLOR_WORDS = [
    ("flat dark earth", "fde"), ("fde flat dark earth", "fde"), ("dark earth", "fde"), ("fde", "fde"),
    ("ranger green", "ranger"), ("rgr", "ranger"),
    ("olive drab green", "odg"), ("olive drab", "odg"), ("od green", "odg"), ("odg", "odg"),
    ("olive", "odg"), ("oliv", "odg"),
    ("wolf grey", "wolfgray"), ("wolf gray", "wolfgray"),
    ("stealth grey", "gray"), ("stealth gray", "gray"), ("grey", "gray"), ("gray", "gray"),
    ("grau", "gray"), ("gry", "gray"),
    ("foliage green", "foliage"), ("foliage", "foliage"), ("fol", "foliage"),
    ("coyote brown", "coyote"), ("coyote", "coyote"),
    ("black", "black"), ("schwarz", "black"), ("blk", "black"),
    ("tan", "tan"), ("mct", "mct"), ("mc brown", "mcbrown"), ("mc-brown", "mcbrown"),
    ("medium coyote brown", "mcbrown"), ("plum", "plum"), ("orange", "orange"), ("bronze", "bronze"),
    ("sand", "sand"), ("rattlecan", "rattlecan"), ("green", "?"), ("grün", "?"),
]
_CW = sorted({w for w, _ in COLOR_WORDS}, key=len, reverse=True)
C_RE = r"(?: (?P<c>" + "|".join(re.escape(w) for w in _CW) + r"))?"
SKU_COLOR = [(r"(?:-|\b)BLK$|-BK$|BLK-|-BK-", "black"), (r"-FDE$|FDE-", "fde"), (r"-ODG$", "odg"),
             (r"-GRY$|-GRAY$", "gray"), (r"-?RGR$", "ranger"), (r"-COY$", "coyote"), (r"-?TAN$|-TN(-|$)", "tan"),
             (r"-MCT$", "mct"), (r"-MCB$", "mcbrown"), (r"-PLM$", "plum")]


def color_word(w):
    w = (w or "").lower().strip()
    for word, k in COLOR_WORDS:
        if w == word:
            return None if k == "?" else k
    return None


def color_from_text(t):
    """Szabad szövegből (attribútumérték) színkulcs."""
    t = key(t)
    for w in _CW:
        if re.search(r"(?:^|\s)" + re.escape(w) + r"(?:\s|$)", t):
            return color_word(w)
    return None


def color_from_sku(sku):
    for pat, k in SKU_COLOR:
        if sku and re.search(pat, sku, re.I):
            return k
    return None


def parse_chf(text):
    m = re.search(r"([0-9][0-9'’ ]*(?:\.[0-9]{1,2})?)", norm(text).replace("CHF", ""))
    return float(re.sub(r"['’ ]", "", m.group(1))) if m else None


# =========================================================================== data.js
def read_data_js():
    src = open(DATA_JS, encoding="utf-8").read()
    consts = {}
    for m in re.finditer(r"const\s+([A-Z0-9_]+)\s*=\s*(\[[^\]]*\])\s*;", src):
        try:
            consts[m.group(1)] = json.loads(m.group(2).replace("'", '"'))
        except ValueError:
            pass
    out = []
    bm = re.search(r"const BASES = \[(.*?)\n  \];", src, re.S)
    for seg in re.split(r"\n\s*\{\s*\n", bm.group(1))[1:]:
        m = re.search(r"id:\s*'([^']+)',\s*brand:\s*'([^']*)',\s*name:\s*'([^']*)'", seg)
        f = re.search(r"finishes:\s*(\[[^\]]*\])", seg)
        if m:
            out.append(dict(id=m.group(1), brand=m.group(2), name=m.group(3), kind="base",
                            colors=json.loads(f.group(1).replace("'", '"')) if f else ["black"]))
    for line in src.splitlines():
        m = re.match(r"\s*\{\s*id:\s*'([^']+)',\s*cat:\s*'([^']+)'", line)
        b = re.search(r"\bbrand:\s*'((?:[^'\\]|\\.)*)'", line)
        n = re.search(r"\bname:\s*'((?:[^'\\]|\\.)*)'", line)
        if not (m and b and n):
            continue
        cm = re.search(r"colors:\s*(\[[^\]]*\]|[A-Z0-9_]+)", line)
        colors = None
        if cm:
            v = cm.group(1)
            colors = json.loads(v.replace("'", '"')) if v.startswith("[") else consts.get(v)
        out.append(dict(id=m.group(1), brand=b.group(1).replace("\\'", "'"), name=n.group(1).replace("\\'", "'"),
                        kind="item", colors=colors))
    return out


BRAND_RE = {
    "Magpul": r"magpul|pmag", "BCM": r"\bbcm\b|bravo company|gunfighter", "B5 Systems": r"\bb5\b",
    "Geissele": r"geissele", "Radian": r"radian", "Aimpoint": r"aimpoint", "EOTech": r"eotech",
    "Holosun": r"holosun", "Trijicon": r"trijicon|acog", "Vortex": r"vortex", "SureFire": r"surefire",
    "Streamlight": r"streamlight", "Harris": r"harris", "Blue Force Gear": r"blue force|vickers",
    "Schmeisser": r"schmeisser", "Daniel Defense": r"daniel defense|ddm4",
}

# =========================================================================== közös indokok / "legközelebbi" minták
NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a márkát/terméket)"
NOT_FOUND = "nincs a boltban (a keresés nem adott pontosan azonos terméket)"
SET_BUIS = "szett (első + hátsó irányzék): a bolt az elsőt és a hátsót külön termékként árulja, szett nincs"
ACRO_BASE = "csomag (ACRO P-2 + Picatinny talp): a bolt az ACRO P-2-t talp nélkül árulja"
G43_STS = "G43 + 'Micro' szerelék csomag: a bolt csak a G43.STS (STS szerelékkel) változatot árulja"
RVG_PIC = ("az 'M-LOK RVG' nincs a boltban: a bolti RVG a Picatinny (M1913) változat, ill. külön "
           "'RVG M-LOK Adapter Rail' - nem azonos")
CABLE = "a Magpul M-LOK Cable Management Kit nincs a boltban; a 'WCK - Wire Control Kit' más termék"
DYN = ("a bolt csak 'AR15 Dynamic L' modellt árul; a data.js 'AR15 Dynamic M5FL' megnevezésével való "
       "azonosság a bolti oldalon nincs kimondva -> bizonytalan, nem vettük fel")

NEAR = {
    "schm-s4f": r"schmeisser.*\bs4f\b", "schm-m4fl": r"schmeisser.*m4fl", "schm-m5fl": r"schmeisser.*m5fl",
    "schm-dyn": r"schmeisser.*dynamic", "dd-v7": r"ddm4 ?v7", "bcm-recce16": r"recce",
    "mp-moe": r"\bmoe\b.*grip", "mp-moek2": r"moe[- ]?k2|moe 2k", "mp-moek2p": r"moe[- ]?k2",
    "mp-miad": r"miad", "bcm-g0": r"grip mod", "bcm-g3": r"grip mod", "b5-t22": r"p-grip|type 2[23]",
    "b5-t23": r"p-grip|type 2[23]", "mp-ctr": r"\bctr\b", "mp-moesl": r"moe sl", "mp-moeslk": r"moe sl",
    "mp-ubr2": r"\bubr\b", "mp-prs3": r"\bprs\b", "mp-prslite": r"prs lite", "bcm-s0": r"stock mod",
    "bcm-s1": r"stock mod", "b5-bravo": r"bravo|sopmod", "b5-esop": r"sopmod", "b5-prec": r"precision stock",
    "mp-pad030": r"butt[- ]?pad", "mp-pad055": r"butt[- ]?pad", "mp-pad070": r"butt[- ]?pad",
    "mp-padsl": r"butt[- ]?pad", "ap-t2": r"micro t-?2", "ap-m5": r"comp ?m5", "ap-pro": r"aimpoint pro\b",
    "ap-acro": r"acro p-?2", "eo-exps3": r"exps3", "eo-exps2": r"exps2", "eo-xps2": r"\bxps2",
    "hs-510c": r"510c", "hs-aems": r"\baems\b", "hs-403b": r"\b403", "tj-mro": r"\bmro\b",
    "vx-uh1": r"uh-1", "vx-sparc": r"sparc", "vx-crossfire": r"crossfire.*(red dot|rot)",
    "vx-se16": r"strike eagle 1-6", "vx-se18": r"strike eagle 1-8", "vx-pst16": r"pst.*1-6",
    "vx-razor110": r"razor.*1-10", "tj-acog": r"acog", "tj-credo16": r"credo", "tj-vcog": r"vcog",
    "eo-vudu16": r"vudu.*1-6", "eo-vudu110": r"vudu.*1-10", "gs-sp30": r"geissele.*(precision|mount)",
    "vx-cant30": r"cantilever", "eo-g33": r"\bg33", "eo-g43": r"\bg43", "ap-3x": r"3x-?mag|3x-c|booster",
    "hs-hm3x": r"hm3x", "vx-m3x": r"micro ?3x|v3xm", "mp-mbus3": r"mbus 3", "mp-mbuspro": r"mbus pro sight",
    "mp-mbusoff": r"mbus pro offset", "sf-m600": r"\bm600", "sf-m640": r"\bm640", "sf-m300": r"\bm3[04]0",
    "sf-x300": r"x300", "sl-hlx": r"protac rail mount|hl-x", "sl-tlr1": r"tlr-1", "mp-rvg": r"\brvg\b",
    "mp-hstop": r"hand stop", "bcm-vg3": r"vertical grip", "bcm-kag": r"\bkag\b|angled grip",
    "mp-rail5": r"rail section|aluminium rail|aluminum rail", "mp-qd": r"qd sling mount",
    "mp-cover": r"rail cover", "mp-cable": r"\bwck\b|wire control|cable", "mp-bipod": r"bipod|zweibein",
    "hr-sbrm": r"harris.*(brm|bipod|zweibein)", "sf-warcomp": r"warcomp", "sf-3p": r"3[- ]?prong|sf3p",
    "bcm-comp0": r"bcm.*comp", "sf-rc2": r"rc2", "gs-ssae": r"geissele.*(ssa|trigger|abzug)",
    "gs-ssa": r"geissele.*(ssa|trigger|abzug)", "gs-sde": r"geissele.*(sd-|trigger|abzug)",
    "gs-s3g": r"geissele.*(s3g|trigger|abzug)", "rd-raptor": r"raptor", "rd-raptorlt": r"raptor[- ]lt",
    "rd-raptorsd": r"raptor[- ]sd", "bcm-ch4": r"charging handle", "bcm-ch3": r"charging handle",
    "gs-ach": r"geissele.*charging", "rd-talon": r"talon|safety selector", "mp-bad": r"b\.?a\.?d\.? lever",
    "mp-trigguard": r"trigger guard", "mp-pm10": r"pmag ?10", "mp-pm20": r"pmag ?20", "mp-pm30": r"pmag ?30",
    "mp-pm30w": r"pmag ?30.*(window|fenster)", "mp-pm40": r"pmag ?40", "mp-d60": r"\bd-[56]0\b",
    "mp-ms1": r"\bms1\b", "mp-ms4": r"\bms4\b", "bfg-vickers": r"vickers",
}


# =========================================================================== szabályok
def R(pat=None, sku=None, why=None, note=None, nc=None, excl=None, skureq=None, check=None):
    """pat: regex(ek) a bolti név key()-ére (fullmatch, '{C}' = opcionális színszó);
    sku: regex a bolti cikkszámra (fullmatch) - alternatív egyezési út;
    why: indok, ha nincs (vagy nem lehet) egyezés; note: megjegyzés a felvett tételhez;
    nc: szín, ha a névből/cikkszámból nem derül ki; excl: kizáró regex (search) a key()-re;
    skureq: regex, aminek a cikkszámban szerepelnie kell (search); check: regex a termékoldal szövegére."""
    pats = [] if pat is None else ([pat] if isinstance(pat, str) else list(pat))
    return dict(pats=[re.compile(p.replace("{C}", C_RE)) for p in pats],
                sku=re.compile(sku, re.I) if sku else None, why=why, note=note, nc=nc,
                excl=re.compile(excl) if excl else None, skureq=re.compile(skureq, re.I) if skureq else None,
                check=re.compile(check, re.I) if check else None)


RULES = {
    # ---- alapfegyverek
    "schm-s4f": R(r'schmeisser ar15 s4f{C} 10\.5"',
                  note="standard M-LOK (Slim Line) kivitel; a 'SOLID LINE' más előagyú termék"),
    "schm-m4fl": R([r'schmeisser ar15 m4fl{C} 14\.5"', r'schmeisser ar15 m4fl{C} 14\.5" \.223 rem m-lok slim line'],
                   note="standard M-LOK (Slim Line) kivitel; a 'SOLID LINE' és a 12.5\" más termék"),
    "schm-m5fl": R(None, why="csak az 'AR15 M5FL 16.75\" SOLID LINE' kapható (más előagy, másik cikkszám: "
                             "03-0076S); a standard M5FL nincs a boltban"),
    "schm-dyn": R(None, why=DYN),
    "dd-v7": R(None, why="csak DDM4 V7 PRO 18\" és DDM4 V7 SLW (más modellek); a sima DDM4 V7 nincs"),
    "bcm-recce16": R(None, why=NOT_SOLD),
    # ---- markolatok
    "mp-moe": R(r"magpul moe grip ar15/m4{C}"),
    "mp-moek2": R([r"magpul moe-k2 grip{C}", r"magpul moe-k2 grip ar15/m4{C}"]),
    "mp-moek2p": R(r"magpul moe-k2\+ grip ar15/m4{C}",
                   note="a bolt egyik tételének neve hibásan 'Grey', cikkszáma MAG532-FDE (FDE)"),
    "mp-miad": R(r"magpul miad gen 1\.1 grip kit type 1{C}",
                 note="Type 1 (AR-15) kivitel; a Type 2 (AR-10/SR-25) más termék"),
    "bcm-g0": R(None, why=NOT_SOLD), "bcm-g3": R(None, why=NOT_SOLD),
    "b5-t22": R(None, why=NOT_SOLD), "b5-t23": R(None, why=NOT_SOLD),
    # ---- tusok
    "mp-ctr": R(r"magpul ctr carbine stock mil[- ]spec{C}", note="Mil-Spec csöves kivitel"),
    "mp-moesl": R(r"magpul moe sl carbine stock mil-spec{C}", note="Mil-Spec csöves kivitel"),
    "mp-moeslk": R(r"magpul moe sl-k carbine stock mil-spec{C}", note="Mil-Spec csöves kivitel"),
    "mp-ubr2": R(r"magpul ubr gen2 collapsible stock{C}"),
    "mp-prs3": R(r"magpul prs gen3 precision[- ]adjustable stock{C}"),
    "mp-prslite": R(r"magpul prs lite precision adjustable stock{C}"),
    "bcm-s0": R(None, why=NOT_SOLD), "bcm-s1": R(None, why=NOT_SOLD),
    "b5-bravo": R(None, why=NOT_SOLD), "b5-esop": R(None, why=NOT_SOLD), "b5-prec": R(None, why=NOT_SOLD),
    # ---- tustalpak
    "mp-pad030": R(r"magpul rubber butt-pad 0\.30 zoll"),
    "mp-pad055": R(r"magpul extended rubber butt-pad 0\.55 zoll schaftkappe"),
    "mp-pad070": R(r"magpul enhanced rubber butt-pad 0\.70 zoll schaftkappe"),
    "mp-padsl": R(r"magpul moe sl enhanced rubber butt-pad 0\.70 zoll"),
    # ---- red dot / holo
    "ap-t2": R(None, why="a Micro T-2 nincs a boltban (csak Micro T-1 / H-2, Duty RDS)"),
    "ap-m5": R(None, why="a CompM5 nincs a boltban"),
    "ap-pro": R(None, why="az Aimpoint PRO nincs a boltban"),
    "ap-acro": R(None, why=ACRO_BASE),
    "eo-exps3": R(r"eotech exps3-0{C}", note="EXPS3-0 (68 MOA kör + 1 MOA pont)"),
    "eo-exps2": R(r"eotech hws exps2-0 red dot{C}", note="EXPS2-0 (68 MOA kör + 1 MOA pont)"),
    "eo-xps2": R(r"eotech xps2-0 68 moa ring\+ 1 moa dot"),
    "hs-510c": R(r"holosun hs510c(?:-fde)? red circle dot reflexvisier open solar"),
    "hs-aems": R(r"holosun aems rd red circle dot reflexvisier closed emitter solar",
                 note="az alap AEMS (piros, 2 MOA pont + 65 MOA kör); az AEMS Core / Pro / Max / GR más "
                      "modell/változat"),
    "hs-403b": R(None, why="a HS403B nincs a boltban"),
    "tj-mro": R(None, why="az MRO nincs a boltban"),
    "vx-uh1": R(None), "vx-sparc": R(None), "vx-crossfire": R(None),
    # ---- LPVO
    "vx-se16": R(None), "vx-se18": R(None), "vx-pst16": R(None), "vx-razor110": R(None),
    "tj-acog": R(r"trijicon acog 4x32 (?!.*rmr).*zielfernrohr \.223 bac(?: ta51)?", skureq=r"^TR-TA31-",
                 note="TA31 (ACOG 4x32, .223 BAC, TA51 szereléssel); a bolt két szálkereszt-változatot árul "
                      "azonos áron (TA31-CH: dual illuminated red crosshair, TA31-H: red horseshoe dot); a "
                      "data.js nem határozza meg a szálkeresztet. Az RMR-rel szerelt csomagok más termékek."),
    "tj-credo16": R(None), "tj-vcog": R(None, why="csak VCOG 1-6x24 kapható (a data.js 1-8x28-at ír)"),
    "eo-vudu16": R(r"eotech vudu 1-6x24.*", note="FFP kivitel (EO-VDU1-6FFSR); a bolt SR1 / SR2 / SR3 "
                                                 "szálkeresztet kínál azonos áron"),
    "eo-vudu110": R(r"eotech vudu 1-10x28.*", note="FFP kivitel; a bolt LE-5 / SR-4 / SR-5 szálkeresztet "
                                                   "kínál azonos áron"),
    # ---- optikaszerelék
    "gs-sp30": R(None), "vx-cant30": R(None),
    # ---- nagyítók
    "eo-g33": R(None, why="a G33 önállóan (G33.STS) nincs a boltban, csak HHS csomagokban (EXPS + G33)"),
    "eo-g43": R(None, why=G43_STS),
    "ap-3x": R(None, why="csak az Aimpoint Booster 3X-C (más termék); 3XMag-1 + Flip nincs"),
    "hs-hm3x": R(r"holosun hm3x magnifier 3x .*flip mount"),
    "vx-m3x": R(None),
    # ---- BUIS
    "mp-mbus3": R(None, why=SET_BUIS), "mp-mbuspro": R(None, why=SET_BUIS), "mp-mbusoff": R(None, why=SET_BUIS),
    # ---- lámpák
    "sf-m600": R(None, why="az M600 lámpa nincs a boltban (csak M600 LED-modul alkatrész)"),
    "sf-m640": R(sku=r"SF-M640DF-(BK|TN)-PRO",
                 note="a fekete változat bolti neve 'SureFire M640 Scout Light Pro', cikkszáma SF-M640DF-BK-PRO; "
                      "a Turbo (M640DFT) és az M640U más típus"),
    "sf-m300": R(None, why="csak M340C / M340T Mini Scout Light Pro (más típus); M300 nincs"),
    "sf-x300": R(sku=r"SF-X300U-B(-TN)?"),
    "sl-hlx": R(r"streamlight protac rail mount hl-x"),
    "sl-tlr1": R(None, why="csak TLR-1 HL-X USB (más típus); TLR-1 HL nincs"),
    # ---- M-LOK
    "mp-rvg": R(None, why=RVG_PIC),
    "mp-hstop": R(r"magpul m-lok hand stop kit{C}"),
    "bcm-vg3": R(None, why=NOT_SOLD), "bcm-kag": R(None, why=NOT_SOLD),
    "mp-rail5": R(r"magpul m-lok aluminum rail section 5 slots"),
    "mp-qd": R(r"magpul m-lok qd sling mount"),
    "mp-cover": R(r"magpul m-lok rail cover type 2{C}"),
    "mp-cable": R(None, why=CABLE),
    # ---- bipod
    "mp-bipod": R(r"magpul bipod für m-lok{C}", note="Magpul Bipod for M-LOK (MAG933, alumínium)"),
    "hr-sbrm": R(None, why="a bolti Harris M-LOK bipod a BRM (cikkszám HAS-BRM-MLOK, nem dönthető 'S' / swivel); "
                           "az S-BRM ('Swivel gekerbte Beine 6-9\"') csak M-LOK adapter nélkül kapható"),
    # ---- torkolat
    "sf-warcomp": R(sku=r"SF-WARCOMP-556-1/2-28", note="WARCOMP-556 1/2-28 (a CTN és a 7.62 más termék)"),
    "sf-3p": R(sku=r"SF-SF3P-556-1/2-28", note="SF3P-556 1/2-28"),
    "bcm-comp0": R(None, why=NOT_SOLD), "sf-rc2": R(None, why="a SOCOM556-RC2 hangtompító nincs a boltban"),
    # ---- elsütők, felhúzókarok
    "gs-ssae": R(None, why="Geissele elsütő nincs a boltban (csak Geissele Super Duty puska)"),
    "gs-ssa": R(None, why="Geissele elsütő nincs a boltban (csak Geissele Super Duty puska)"),
    "gs-sde": R(None, why="Geissele elsütő nincs a boltban (csak Geissele Super Duty puska)"),
    "gs-s3g": R(None, why="Geissele elsütő nincs a boltban (csak Geissele Super Duty puska)"),
    "rd-raptor": R(None, why="a Raptor (nem LT) nincs a boltban"),
    "rd-raptorlt": R(r"radian weapons raptor-lt ar15/m16 ladehebel{C}", note="AR-15 kivitel (az AR10 más termék)"),
    "rd-raptorsd": R(None, why="a Raptor-SD nincs a boltban"),
    "bcm-ch4": R(None, why=NOT_SOLD), "bcm-ch3": R(None, why=NOT_SOLD),
    "gs-ach": R(None, why="Geissele felhúzókar nincs a boltban"),
    # ---- kezelőszervek
    "rd-talon": R(None, why="a Radian Talon nincs a boltban"),
    "mp-bad": R(r"magpul b\.a\.d\. lever battery assist device ar15/m4"),
    "mp-trigguard": R(r"magpul moe enhanced trigger guard polymer ar15/m4{C}",
                      note="MAG1186 (polimer); az alumínium 'Enhanced Trigger Guard' (MAG015) más termék"),
    # ---- tárak
    "mp-pm10": R(r"magpul pmag 10 ar/m4 gen m3 5\.56x45{C}"),
    "mp-pm20": R(r"magpul pmag 20 ar/m4 gen m3 5\.56x45{C}"),
    "mp-pm30": R([r"magpul pmag 30 ar/m4 gen m3{C}", r"magpul pmag 30 ar/m4 gen m3 5\.56x45{C}"]),
    "mp-pm30w": R(r"magpul pmag 30 ar/m4 gen m3 mit fenster{C}"),
    "mp-pm40": R(r"magpul pmag 40 ar/m4 gen m3 5\.56x45{C}"),
    "mp-d60": R(r"magpul pmag d-60 ar/m4"),
    # ---- szíjak
    "mp-ms1": R(r"magpul ms1 sling{C}"),
    "mp-ms4": R(r"magpul ms4 dual qd sling gen2{C}", note="Gen 2 kivitel (a bolt csak ezt árulja)"),
    "bfg-vickers": R(None, why=NOT_SOLD),
}


# =========================================================================== bolt-specifikus: katalógus
def money(v, minor):
    return round(int(v) / (10 ** int(minor)), 2) if v not in (None, "") else None


def attrs_of(p):
    return {a.get("name"): [t.get("name") for t in a.get("terms", [])] for a in p.get("attributes", [])}


def card_from_api(p, parent=None):
    pr = p.get("prices") or {}
    minor = pr.get("currency_minor_unit", 2)
    price = money(pr.get("price"), minor)
    reg = money(pr.get("regular_price"), minor)
    sa = p.get("stock_availability") or {}
    cls = sa.get("class", "")
    in_stock = True if cls == "in-stock" else (False if cls in ("out-of-stock", "available-on-backorder") else None)
    stock_text = norm(sa.get("text")) or {"in-stock": "An Lager", "out-of-stock": "Nicht vorrätig",
                                          "available-on-backorder": "Verfügbar bei Nachbestellung"}.get(cls, "")
    attrs = attrs_of(parent or p)
    imgs = [i.get("src") for i in (p.get("images") or (parent or {}).get("images") or []) if i.get("src")]
    w = None
    for k, v in attrs.items():
        if k and k.lower().startswith("gewicht") and v:
            m = re.search(r"(\d+(?:\.\d+)?)", v[0])
            w = int(round(float(m.group(1)))) if m else None
    dims = " x ".join("%s %s mm" % (k.split(" ")[0], v[0]) for k, v in attrs.items()
                      if k and re.match(r"(Länge|Breite|Höhe) \[mm\]", k) and v) or None
    name = norm((parent or p).get("name"))
    var = {}
    if parent:
        for a in p.get("variation") or p.get("attributes") or []:
            if isinstance(a, dict) and a.get("value") is not None:
                var[norm(a.get("name") or a.get("attribute"))] = norm(a.get("value"))
    return dict(name=name, url=p.get("permalink") or (parent or {}).get("permalink"), price=price,
                list=reg if (reg and price is not None and reg > price) else None, in_stock=in_stock,
                stock_text=stock_text, sku=p.get("sku") or "", image=imgs[0] if imgs else None,
                weight_g=w, dims=dims, attr_color=(attrs.get("Farbe") or [None])[0] if not parent else None,
                var=var, type=p.get("type"), id=p.get("id"), variations=p.get("variations") or [],
                raw_attrs=attrs)


def fetch_catalog(http):
    cards, seen = [], set()
    for q in QUERIES:
        page = 1
        while True:
            url = API + "?" + urllib.parse.urlencode({"search": q, "per_page": 100, "page": page})
            data = json.loads(http.get(url, accept="application/json"))
            for p in data:
                if p.get("id") in seen:
                    continue
                seen.add(p.get("id"))
                cards.append(card_from_api(p))
            if len(data) < 100 or page >= 15:
                break
            page += 1
        sys.stderr.write("  katalógus '%s': %d termék eddig\n" % (q, len(cards)))
    return cards


def expand_variations(http, card):
    """Változatos termék -> változatonkénti kártyák (ár, készlet, cikkszám a változat saját API-adatából)."""
    out = []
    terms = {}
    for k, vals in (card.get("raw_attrs") or {}).items():
        terms[k] = vals
    for v in card["variations"]:
        p = json.loads(http.get(API + "/%s" % v["id"], accept="application/json"))
        c = card_from_api(p, parent=dict(name=card["name"], permalink=card["url"], images=[],
                                          attributes=[]))
        c["url"] = p.get("permalink") or card["url"]
        labels = []
        for a in v.get("attributes", []):
            val = a.get("value")
            # slug -> megjelenített érték (a szülő attribútum-termjeiből)
            disp = next((t for t in terms.get(a.get("name"), []) if re.sub(r"[^a-z0-9]", "", t.lower())
                         == re.sub(r"[^a-z0-9]", "", (val or "").lower())), val)
            if (a.get("name") or "").lower() == "farbe":
                c["attr_color"] = disp
            else:
                labels.append(disp)
        if labels:
            c["name"] = card["name"] + " – " + " / ".join(labels)
        c["image"] = c["image"] or card["image"]
        c["weight_g"] = c["weight_g"] or card["weight_g"]
        out.append(c)
    return out


def page_text(http, card):
    return ""  # a Store API minden szükséges adatot ad; termékoldal-ellenőrzés itt nem kell


# =========================================================================== egyeztetés (közös)
def card_color(card, mc, order=("sku", "name", "attr")):
    for src in order:
        if src == "name" and mc:
            k = color_word(mc)
            if k:
                return k
        if src == "sku":
            k = color_from_sku(card.get("sku"))
            if k:
                return k
        if src == "attr" and card.get("attr_color"):
            k = color_from_text(card["attr_color"])
            if k:
                return k
    return None


def match_cards(rule, cards):
    hits = []
    for c in cards:
        k = key(c["name"])
        if rule["excl"] and rule["excl"].search(k):
            continue
        m = None
        for p in rule["pats"]:
            m = p.fullmatch(k)
            if m:
                break
        if not m and not (rule["sku"] and c.get("sku") and rule["sku"].fullmatch(c["sku"])):
            continue
        if rule["skureq"] and not rule["skureq"].search(c.get("sku") or ""):
            continue
        hits.append((c, (m.groupdict().get("c") if m else None)))
    return hits


def palette_key(col, item):
    if col is None:
        return None
    if item["kind"] == "base":
        return {"odg": "olive", "fde": "earth" if "earth" in (item["colors"] or []) else "fde"}.get(col, col)
    return col


def offer(c):
    return dict(price_chf=c["price"], list_chf=c["list"], url=c["url"], in_stock=c["in_stock"],
                title=c["name"])


def best(cs):
    """Azonos szín több bolti tétele közül: a raktáron lévő legolcsóbb, ennek hiányában a legolcsóbb."""
    return sorted(cs, key=lambda c: (c["in_stock"] is not True, c["price"] if c["price"] is not None else 1e9))


def near_text(item, cards, used):
    rx = NEAR.get(item["id"])
    brx = BRAND_RE.get(item["brand"])
    if not rx:
        return [], False
    brand_seen = any(re.search(brx, key(c["name"])) for c in cards) if brx else True
    out = []
    for c in cards:
        k = key(c["name"])
        if c["url"] in used or not re.search(rx, k):
            continue
        if brx and not re.search(brx, k):
            continue
        out.append(c)
    out.sort(key=lambda c: (c["price"] is None, c["price"] or 0))
    return out[:4], brand_seen


def unmatched_reason(item, rule, cards, used, why=None):
    near, brand_seen = near_text(item, cards, used)
    why = why or (rule or {}).get("why") or (NOT_FOUND if brand_seen or near else NOT_SOLD)
    s = "ok: " + why
    if near:
        s += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(
            "%s (%s CHF, %s)" % (c["name"], ("%.2f" % c["price"]) if c["price"] is not None else "?", c["url"])
            for c in near)
    return s


def build_item(item, rule, hits, color_order):
    pal = item["colors"]
    groups = {}
    for c, mc in hits:
        col = palette_key(card_color(c, mc, color_order) or rule["nc"], item)
        c["_color"] = col
        groups.setdefault(col, []).append(c)
    if pal:
        valid = [k for k in pal if k in groups]
        if valid:
            main_key = "black" if "black" in valid else valid[0]
        elif None in groups:
            main_key = None
        else:
            return None, "csak a data.js palettáján kívüli színben kapható (%s)" % ", ".join(
                sorted(k for k in groups if k))
    else:
        valid = []
        main_key = "black" if "black" in groups else (None if None in groups else sorted(groups)[0])
    main = best(groups[main_key])[0]
    it = dict(title=main["name"], price_chf=main["price"], list_chf=main["list"], url=main["url"],
              image=main["image"], in_stock=main["in_stock"], stock_text=main["stock_text"],
              sku=main["sku"] or None, mpn=main.get("mpn"), weight_g=main.get("weight_g"), dims=main.get("dims"))
    if main_key:
        it["color"] = main_key
    if pal:
        it["colors_prices"] = {k: offer(best(groups[k])[0]) for k in valid}
    alts = []
    for k, cs in groups.items():
        for c in best(cs):
            if c is main or (pal and k in valid and c is best(groups[k])[0]):
                continue
            alts.append(dict(title=c["name"], color=k, price_chf=c["price"], list_chf=c["list"],
                             in_stock=c["in_stock"], url=c["url"], sku=c["sku"] or None))
    if alts:
        it["alternatives"] = alts
    notes = []
    if rule["note"]:
        notes.append(rule["note"])
    if main_key is None and pal:
        notes.append("a bolti név/cikkszám nem adja meg a színt")
    elif not pal and main_key not in (None, "black"):
        notes.append("csak %s színben kapható (fekete nincs)" % COLOR_LABELS.get(main_key, main_key))
    if notes:
        it["note"] = "; ".join(notes)
    same_color_dupes = len(best(groups[main_key])) > 1
    it["match"] = "variant" if (same_color_dupes or (rule["note"] and "változat" in rule["note"])) else "exact"
    return it, None


COLOR_LABELS = {"black": "fekete", "fde": "FDE", "odg": "OD Green", "gray": "szürke", "tan": "Tan",
                "coyote": "Coyote", "ranger": "Ranger Green", "bronze": "bronz", "mct": "MCT",
                "mcbrown": "MC Brown", "earth": "Dark Earth", "olive": "OD Green"}


def run(http, entries, color_order=("sku", "name", "attr")):
    cards = fetch_catalog(http)
    items, unmatched = {}, {}
    for i, item in enumerate(entries, 1):
        pid = item["id"]
        rule = RULES.get(pid) or R(None)
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), pid, item["brand"], item["name"]))
        hits = match_cards(rule, cards) if (rule["pats"] or rule["sku"]) else []
        # változatos termékek kibontása (csak az egyezőknél)
        exp = []
        for c, mc in hits:
            if c.get("type") == "variable" and c.get("variations"):
                for v in expand_variations(http, c):
                    exp.append((v, mc))
            else:
                exp.append((c, mc))
        hits = exp
        if hits and rule["check"]:
            txt = page_text(http, best([h[0] for h in hits])[0])
            if not rule["check"].search(txt):
                unmatched[pid] = unmatched_reason(item, rule, cards, set(),
                                                  why=(rule["why"] or "a termékoldal nem igazolja az egyezést"))
                continue
        if not hits:
            unmatched[pid] = unmatched_reason(item, rule, cards, set())
            sys.stderr.write("   -> unmatched\n")
            continue
        it, err = build_item(item, rule, hits, color_order)
        if err:
            unmatched[pid] = unmatched_reason(item, rule, cards, set(), why=err)
            continue
        items[pid] = it
        sys.stderr.write("   -> %s  CHF %s\n" % (it["title"], it["price_chf"]))
    return items, unmatched


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/armatum.json; --only mellett stdout)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár")
    args = ap.parse_args()
    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["id"] in wanted]
    http = Http(args.cache)
    blocked = None
    try:
        items, unmatched = run(http, entries)
    except Blocked as e:
        blocked, items, unmatched = str(e), {}, {}
        sys.stderr.write("BLOKKOLVA: %s\n" % e)
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
               source=API + " (WooCommerce Store API)",
               price_basis="a bolt kiírt fogyasztói ára (az AGB szerint MWST-vel); 'Verfügbar bei Nachbestellung' "
                           "= nincs raktáron (in_stock=false), csak rendelhető",
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
