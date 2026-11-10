#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gunlex.ch (Gunlex GmbH, Beckenried NW) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_gunlex.py                        # minden termék -> assets/prices/gunlex.json
    python3 tools/prices_gunlex.py --only schm-s4f,hs-510c # csak néhány id (stdout, ha nincs --out)
    python3 tools/prices_gunlex.py --cache /tmp/gunlex     # HTTP-gyorsítótár (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  1. A bolt WooCommerce, de a nyilvános Store API (/wp-json/wc/store/v1/products) a próbakor
     HTTP 500-at adott ("kritischer Fehler"), ezért a bolt saját HTML-keresőjét használja:
     /?s=<márka>&post_type=product (16 találat / oldal, /page/N/ lapozás).
  2. Minden data.js id-hoz a RULES táblában van a bolti név PONTOS (fullmatch) mintája
     (normalizált, kisbetűs címre; a {C} helyén opcionális színszó). Szabály nélküli / None
     mintájú id -> unmatched (indok + a legközelebbi bolti termékek).
  3. Az egyező termékek oldalát is letölti: ár (akciós ár és áthúzott ár), készlet
     ("Vorrätig" / "Nicht vorrätig"), cikkszám (JSON-LD), kép URL; változatos terméknél a
     data-product_variations JSON-ból változatonként.
  4. Árat soha nem becsül, nem számol, nem vált át.

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
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("Hiányzik a beautifulsoup4: pip install beautifulsoup4 lxml")

SHOP = "gunlex.ch"
BASE = "https://gunlex.ch"
HOSTS = ("gunlex.ch", "www.gunlex.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "gunlex.json")

SOURCE = BASE + "/?s=<keresőszó>&post_type=product (WooCommerce HTML-kereső; a Store API HTTP 500-at ad) + termékoldalak"
PRICE_BASIS = ("a bolt kiírt fogyasztói ára (az AGB szerint MWST-vel); akciónál price_chf = 'Aktueller Preis', "
               "list_chf = 'Ursprünglicher Preis'")
COLOR_ORDER = ("name", "sku", "attr")

QUERIES = ["magpul", "pmag", "eotech", "holosun", "aimpoint", "trijicon", "vortex", "surefire", "streamlight",
           "harris", "radian", "geissele", "bcm", "b5 systems", "blue force", "schmeisser", "daniel defense"]


# =========================================================================== HTTP
class Blocked(Exception):
    pass


class Http:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir
        self.last = 0.0
        self.n = 0
        self.final = None
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
                self.final = url
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
                    self.final = r.geturl()
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
    s = unicodedata.normalize("NFC", html.unescape(s or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def key(s):
    """Összehasonlító kulcs: kisbetű, egységes idézőjel/kötőjel, a ' - ', ', ', ' / ' elválasztók szóközzé."""
    s = unicodedata.normalize("NFKC", norm(s)).lower()
    s = re.sub(r"[®™©]", "", s)
    s = s.replace("×", "x").replace("′′", '"').replace("″", '"').replace("“", '"').replace("”", '"')
    s = s.replace("′", "'").replace("’", "'")
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
    "schm-dyn": r"schmeisser ar15 dynamic(?!.*(trigger|abzug|falke))", "dd-v7": r"ddm4 ?v7", "bcm-recce16": r"recce",
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
    "bcm-comp0": r"bcm.*comp", "sf-rc2": r"rc2", "gs-ssae": r"geissele(?!.*guard).*(ssa|trigger|abzug)",
    "gs-ssa": r"geissele(?!.*guard).*(ssa|trigger|abzug)", "gs-sde": r"geissele(?!.*guard).*(sd-|trigger|abzug)",
    "gs-s3g": r"geissele(?!.*guard).*(s3g|trigger|abzug)", "rd-raptor": r"raptor", "rd-raptorlt": r"raptor[- ]lt",
    "rd-raptorsd": r"raptor[- ]sd", "bcm-ch4": r"charging handle", "bcm-ch3": r"charging handle",
    "gs-ach": r"geissele.*charging", "rd-talon": r"talon|safety selector", "mp-bad": r"b\.?a\.?d\.? lever",
    "mp-trigguard": r"trigger guard", "mp-pm10": r"pmag ?10", "mp-pm20": r"pmag ?20", "mp-pm30": r"pmag ?30",
    "mp-pm30w": r"pmag ?30.*(window|fenster)", "mp-pm40": r"pmag ?40", "mp-d60": r"\bd-[56]0\b",
    "mp-ms1": r"\bms1\b", "mp-ms4": r"\bms4\b", "bfg-vickers": r"vickers.*(sling|riemen|gurt)|(sling|riemen|gurt).*vickers",
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
    # ---- alapfegyverek (a bolti név nem írja ki a színt: a standard kivitel fekete, a Bronze külön tétel)
    "schm-s4f": R(r"schmeisser ar15 s4f{C}", nc="black",
                  note="standard kivitel (10.5\"); a SOLID LINE, CSS, Special Edition és a Falke LE optikás "
                       "csomagok más termékek; a bolti név a feketénél nem írja ki a színt"),
    "schm-m4fl": R(r"schmeisser ar15 m4fl{C}", nc="black",
                   note="standard kivitel (14.5\"); a SOLID LINE, Special Edition és a Falke LE optikás csomagok "
                        "más termékek; a bolti név a feketénél nem írja ki a színt"),
    "schm-m5fl": R(r"schmeisser ar15 m5fl{C}", nc="black",
                   note="standard kivitel (16.75\"); a Falke LE optikás csomag más termék"),
    "schm-dyn": R(None, why="a bolt 'AR15 Dynamic' (10.5\") és 'AR15 Dynamic L' (14.5\" / 16.75\") modelleket árul; "
                            "a data.js 'AR15 Dynamic M5FL' megnevezésével való azonosság a bolti oldalon nincs "
                            "kimondva -> bizonytalan, nem vettük fel"),
    "dd-v7": R(None, why="csak DDM4 V7 PRO / V7 SLW / V7K / V7S (más modellek); a sima DDM4 V7 nincs"),
    "bcm-recce16": R(None, why=NOT_SOLD),
    # ---- markolatok
    "mp-moe": R(None), "mp-moek2": R(None, why="csak az AK-hoz való MOE-K2 AK markolat (más termék)"),
    "mp-moek2p": R(r"magpul moe k2\+ ?grip{C}"),
    "mp-miad": R(None), "bcm-g0": R(None, why=NOT_SOLD), "bcm-g3": R(None, why=NOT_SOLD),
    "b5-t22": R(None, why=NOT_SOLD), "b5-t23": R(None, why=NOT_SOLD),
    # ---- tusok
    "mp-ctr": R(r"magpul ctr carbine stock mil spec{C}", note="Mil-Spec csöves kivitel"),
    "mp-moesl": R(None), "mp-moeslk": R(None), "mp-ubr2": R(None), "mp-prs3": R(None), "mp-prslite": R(None),
    "bcm-s0": R(None, why=NOT_SOLD), "bcm-s1": R(None, why=NOT_SOLD),
    "b5-bravo": R(None, why=NOT_SOLD), "b5-esop": R(None, why=NOT_SOLD), "b5-prec": R(None, why=NOT_SOLD),
    # ---- tustalpak
    "mp-pad030": R(None, why="a bolti 'Magpul Rubber Butt Pad' vastagsága/típusa (0.30\" / 0.55\" / 0.70\") nincs "
                             "megadva, nem azonosítható biztosan"),
    "mp-pad055": R(None), "mp-pad070": R(None), "mp-padsl": R(None),
    # ---- red dot / holo
    "ap-t2": R(None, why="a bolti 'Micro T-2 2 MOA LRP Montage' (Aimpoint 200212) LRP szerelékkel, de 39 mm-es "
                         "távtartó nélkül érkezik (optikai tengely 20 mm a szerelék felett) - nem a data.js 'Lower "
                         "1/3' kivitele"),
    "ap-m5": R(None), "ap-pro": R(None), "ap-acro": R(None, why=ACRO_BASE),
    "eo-exps3": R(None, why="csak EXPS3-DCR (más szálkereszt, külön modell); EXPS3-0 nincs"),
    "eo-exps2": R(None), "eo-xps2": R(None),
    "hs-510c": R([r"holosun hs510c", r"holosun hs510c solar red circle dot sight{C}"],
                 note="a FDE változat külön tétel (más ár)"),
    "hs-aems": R(None), "hs-403b": R(None), "tj-mro": R(None),
    "vx-uh1": R(None), "vx-sparc": R(None), "vx-crossfire": R(None),
    # ---- LPVO
    "vx-se16": R(None), "vx-se18": R(None), "vx-pst16": R(None), "vx-razor110": R(None),
    "tj-acog": R(r"trijicon ta31", note="TA31 (4x32 ACOG, .223 Rem) - a bolti oldal a szálkereszt-változatot nem "
                                        "adja meg; a data.js sem határozza meg"),
    "tj-credo16": R(None), "tj-vcog": R(None),
    "eo-vudu16": R(None), "eo-vudu110": R(r"eotech vudu 1-10x28 ffp", note="FFP kivitel"),
    # ---- optikaszerelék
    "gs-sp30": R(None), "vx-cant30": R(None),
    # ---- nagyítók
    "eo-g33": R(None), "eo-g43": R(None),
    "ap-3x": R(None, why="csak az Aimpoint 3X-C nagyító (más termék); 3XMag-1 + Flip nincs"),
    "hs-hm3x": R(r"holosun hm3x"), "vx-m3x": R(None),
    # ---- BUIS
    "mp-mbus3": R(None, why=SET_BUIS), "mp-mbuspro": R(None), "mp-mbusoff": R(None),
    # ---- lámpák
    "sf-m600": R(None), "sf-m640": R(None), "sf-m300": R(None), "sf-x300": R(None),
    "sl-hlx": R(None), "sl-tlr1": R(None),
    # ---- M-LOK
    "mp-rvg": R(None, why=RVG_PIC),
    "mp-hstop": R(r"magpul m-lok hand stop kit{C}", nc="black",
                  note="a szín nélküli bolti tételt feketének vettük (az FDE külön tétel)"),
    "bcm-vg3": R(None, why=NOT_SOLD), "bcm-kag": R(None, why=NOT_SOLD),
    "mp-rail5": R(None, why="a bolti 'Magpul M-Lok Rail Section' slot-száma és anyaga nincs megadva, nem "
                            "azonosítható biztosan az 5 slotos alumínium sínnel"),
    "mp-qd": R(r"magpul m-lok qd sling mount"),
    "mp-cover": R(None), "mp-cable": R(None),
    # ---- bipod
    "mp-bipod": R(r"magpul zweibeinstütze m-lok{C}", note="a bolti leírás szerint 'Magpul Bipod for M-LOK'"),
    "hr-sbrm": R(None),
    # ---- torkolat
    "sf-warcomp": R(r"surefire warcomp 556 1/2-28"),
    "sf-3p": R(r"surefire flash hider 3 prong 556", check=r"SF3P",
               note="a bolti leírás szerint SF3P (5.56)"),
    "bcm-comp0": R(None, why=NOT_SOLD), "sf-rc2": R(None),
    # ---- elsütők, felhúzókarok, kezelőszervek
    "gs-ssae": R(None), "gs-ssa": R(None), "gs-sde": R(None), "gs-s3g": R(None),
    "rd-raptor": R(None), "rd-raptorlt": R(None), "rd-raptorsd": R(None),
    "bcm-ch4": R(None, why=NOT_SOLD), "bcm-ch3": R(None, why=NOT_SOLD), "gs-ach": R(None),
    "rd-talon": R(None), "mp-bad": R(None), "mp-trigguard": R(None),
    # ---- tárak (a szín nélküli bolti tétel a fekete; FDE külön tétel)
    "mp-pm10": R(r"magpul pmag10 ar/m4 gen m3", nc="black"),
    "mp-pm20": R(None),
    "mp-pm30": R(r"magpul pmag 30 ar m4 gen m3", nc="black"),
    "mp-pm30w": R([r"magpul pmag 30 ar/m4 gen m3 \.223 rem window", r"magpul pmag 30 gen 3 window \.223 rem{C}"],
                  nc="black", note="az FDE tétel bolti neve 'PMAG 30 Gen 3 Window'"),
    "mp-pm40": R(r"magpul pmag 40 ar/m4 gen m3", nc="black"),
    "mp-d60": R(None),
    # ---- szíjak
    "mp-ms1": R([r"magpul ms1 sling{C}", r"sling magpul ms1{C}"], check=r"MS1 Sling",
                excl=r"padded"),
    "mp-ms4": R([r"magpul ms4 dual qd gen2{C}", r"magpul sling ms4 dual qd{C}"],
                note="az FDE tétel nem szerepel a data.js palettáján (black/coyote/ranger)"),
    "bfg-vickers": R(None, why=NOT_SOLD),
}


# =========================================================================== bolt-specifikus: katalógus
def soup(markup):
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")


def woo_price(el):
    """WooCommerce ár-elem -> (fizetendő, áthúzott). Ártartomány ('ab') esetén (None, None)."""
    if el is None:
        return None, None
    d, i = el.select_one("del"), el.select_one("ins")
    if d is not None and i is not None:
        return parse_chf(i.get_text(" ")), parse_chf(d.get_text(" "))
    amts = el.select(".woocommerce-Price-amount")
    if len(amts) == 1:
        return parse_chf(amts[0].get_text(" ")), None
    return None, None


def img_of(el):
    if el is None:
        return None
    for a in ("data-src", "src"):
        v = el.get(a)
        if v and v.startswith("http"):
            return v
    return None


def fetch_catalog(http):
    cards, seen = [], set()
    for q in QUERIES:
        page = 1
        while True:
            url = BASE + "/" + ("page/%d/" % page if page > 1 else "") + "?" + urllib.parse.urlencode(
                {"s": q, "post_type": "product"})
            s = soup(http.get(url))
            prods = s.select("li.product")
            for li in prods:
                a = li.select_one("a.woocommerce-LoopProduct-link")
                h = li.select_one(".woocommerce-loop-product__title")
                if not a or not h or a["href"] in seen:
                    continue
                seen.add(a["href"])
                cls = li.get("class", [])
                price, lst = woo_price(li.select_one(".price"))
                cards.append(dict(name=norm(h.get_text(" ")), url=a["href"], price=price, list=lst,
                                  in_stock=(False if ("outofstock" in cls or "onbackorder" in cls)
                                            else (True if "instock" in cls else None)),
                                  stock_text="", sku="", image=img_of(li.select_one("img")), weight_g=None,
                                  dims=None, attr_color=None,
                                  type="variable" if "product-type-variable" in cls else "simple"))
            if not prods and "/shop/" in (http.final or "") and s.select_one("h1"):
                break  # (egyetlen találatnál a WooCommerce a termékoldalra irányíthat - itt nem fordult elő)
            if not s.select_one("a.next.page-numbers") or page >= 12:
                break
            page += 1
        sys.stderr.write("  katalógus '%s': %d termék eddig\n" % (q, len(cards)))
    return cards


_PAGES = {}


def product_page(http, url):
    if url not in _PAGES:
        _PAGES[url] = soup(http.get(url))
    return _PAGES[url]


def ld_product(s):
    out = {}
    for sc in s.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(sc.string or "")
        except Exception:
            continue
        graph = d.get("@graph", [d]) if isinstance(d, dict) else (d if isinstance(d, list) else [])
        for x in graph:
            if isinstance(x, dict) and x.get("@type") == "Product":
                for k, v in x.items():
                    out.setdefault(k, v)
    return out


def enrich(http, card):
    """Termékoldal: ár (akció), készlet, cikkszám, kép, tömeg; változatok."""
    s = product_page(http, card["url"])
    h1 = s.select_one("h1.product_title") or s.select_one("h1")
    if h1 is None or key(h1.get_text(" ")) != key(card["name"]):
        # a találati lista linkje más termékoldalra irányít (pl. WordPress slug-tippelés) -> a lista adatai maradnak
        card["page_text"] = ""
        card["_note"] = ("a bolti találati lista linkje egy másik termékoldalra (%s) irányít; az ár és a készlet "
                         "a találati listából" % (norm(h1.get_text(" ")) if h1 else "?"))
        return [card]
    ld = ld_product(s)
    pel = s.select_one("p.price") or s.select_one(".summary .price")
    price, lst = woo_price(pel)
    off = ld.get("offers") if isinstance(ld.get("offers"), dict) else {}
    if price is None and off.get("price"):
        price = float(off["price"])
    if price is not None:
        card["price"], card["list"] = price, lst
    st = s.select_one("p.stock")
    if st is not None:
        card["stock_text"] = norm(st.get_text(" "))
        cls = st.get("class", [])
        card["in_stock"] = True if "in-stock" in cls else (False if ("out-of-stock" in cls or
                                                                      "available-on-backorder" in cls) else None)
    elif off.get("availability"):
        card["in_stock"] = off["availability"].endswith("InStock")
        card["stock_text"] = off["availability"].rsplit("/", 1)[-1]
    card["sku"] = str(ld.get("sku") or "")
    imgs = ld.get("image")
    if isinstance(imgs, list) and imgs:
        card["image"] = imgs[0].get("url") if isinstance(imgs[0], dict) else imgs[0]
    og = s.find("meta", property="og:image")
    if og and og.get("content") and not card.get("image"):
        card["image"] = og["content"]
    for pv in ld.get("additionalProperty") or []:
        if isinstance(pv, dict) and re.match(r"Gewicht( Visier)?$", pv.get("name") or ""):
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*g\b", pv.get("value") or "")
            if m:
                card["weight_g"] = int(round(float(m.group(1).replace(",", "."))))
    main = s.select_one("main") or s
    txt = norm(main.get_text(" "))
    card["page_text"] = re.split(r"auch interessant|auch erhältlich|Ähnliche Produkte|Quick View", txt)[0]
    form = s.select_one("form.variations_form")
    variants = []
    if form is not None and form.get("data-product_variations"):
        try:
            vs = json.loads(html.unescape(form["data-product_variations"]))
        except Exception:
            vs = []
        for v in vs if isinstance(vs, list) else []:
            attrs = v.get("attributes") or {}
            label = " / ".join(norm(str(x)) for x in attrs.values() if x)
            c = dict(card)
            c.update(name=card["name"] + (" – " + label if label else ""),
                     price=v.get("display_price"), list=(v.get("display_regular_price")
                                                         if (v.get("display_regular_price") or 0) >
                                                         (v.get("display_price") or 0) else None),
                     in_stock=bool(v.get("is_in_stock")), sku=v.get("sku") or card["sku"],
                     stock_text=norm(BeautifulSoup(v.get("availability_html") or "", "html.parser").get_text(" ")),
                     image=(v.get("image") or {}).get("url") or card["image"],
                     attr_color=next((norm(str(x)) for k2, x in attrs.items() if "farbe" in k2.lower()), None))
            variants.append(c)
    return variants or [card]


def prepare_hits(http, hits):
    out = []
    for c, mc in hits:
        out.extend((v, mc) for v in enrich(http, c))
    return out


def page_text(http, card):
    if "page_text" not in card:
        enrich(http, card)
    return card.get("page_text", "")


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
        if None in groups and "black" in groups:  # paletta nélküli tételnél a szín nélküli = alapkivitel
            groups["black"] = groups["black"] + groups.pop(None)
        main_key = "black" if "black" in groups else (None if None in groups else sorted(groups)[0])
    main = best(groups[main_key])[0]
    it = dict(title=main["name"], price_chf=main["price"], list_chf=main["list"], url=main["url"],
              image=main["image"], in_stock=main["in_stock"], stock_text=main["stock_text"],
              sku=main["sku"] or None, mpn=main.get("mpn"), weight_g=main.get("weight_g"), dims=main.get("dims"))
    if main_key:
        it["color"] = main_key
    it["colors_prices"] = {k: offer(best(groups[k])[0]) for k in valid} if pal else {}
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
    if globals().get("ITEM_NOTE"):
        notes.append(globals()["ITEM_NOTE"])
    for k, cs in groups.items():
        for c in cs:
            if c.get("_note") and (c is main or (pal and k in valid)):
                notes.append(c["_note"] if c is main else "%s: %s" % (COLOR_LABELS.get(k, k), c["_note"]))
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


def run(http, entries, color_order=COLOR_ORDER):
    cards = fetch_catalog(http)
    items, unmatched = {}, {}
    for i, item in enumerate(entries, 1):
        pid = item["id"]
        rule = RULES.get(pid) or R(None)
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), pid, item["brand"], item["name"]))
        hits = match_cards(rule, cards) if (rule["pats"] or rule["sku"]) else []
        hits = prepare_hits(http, hits)  # bolt-specifikus: változatok kibontása / termékoldal-adatok
        if hits and rule["check"]:  # a termékoldal szövegének igazolnia kell az egyezést
            hits = [h for h in hits if rule["check"].search(page_text(http, h[0]))]
            if not hits:
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
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/<bolt>.json; --only mellett stdout)")
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
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(), source=SOURCE,
               price_basis=PRICE_BASIS, items=items, unmatched=unmatched, blocked=blocked)
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
