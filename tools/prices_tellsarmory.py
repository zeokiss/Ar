#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tellsarmory.ch (TELLS ARMORY AG) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_tellsarmory.py                       # minden termék -> assets/prices/tellsarmory.json
    python3 tools/prices_tellsarmory.py --only bcm-kag,mp-ctr  # csak néhány id (stdout, ha nincs --out)
    python3 tools/prices_tellsarmory.py --cache /tmp/tells     # HTTP-gyorsítótár (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  1. A bolt Odoo-webshop. A katalógust a "Waffenzubehör" és a "Sturmgewehre" kategória
     lapozásával (/shop/category/<kat>/page/N) és márkakereséssel (/shop?search=<márka>) gyűjti.
     (Az Odoo JSON-RPC végpontjai POST-kérést igényelnének - ezeket szándékosan nem használja.)
  2. Minden data.js id-hoz a RULES táblában van a bolti név PONTOS (fullmatch) mintája.
  3. Az egyező termékek oldalát letölti: kép, leírás, Odoo-változatok (pl. Farbe: Black/FDE,
     Absehen: 3-0/...); az oldalon kiírt ár az alapértelmezett (bejelölt) kombinációé. Más színt
     csak akkor vesz fel ugyanazzal az árral, ha a bolt az adott értékhez nem jelez felárat.
  4. Készletinformációt a bolt statikusan nem közöl -> in_stock = null.

FIGYELEM - ár-alap: a bolt az árakat a mi (nem svájci) kapcsolatunkról láthatóan MWST nélkül mutatja
(pl. CHF 23.08, 55.41, 1850.05; ezek x1.081 = 24.95, 59.90, 1999.90), miközben az AGB szerint "Für Kunden
in der Schweiz gelten alle Preise inklusive Mehrwertsteuer". Az Odoo a látogató országa szerint
(GeoIP -> fiskális pozíció) számolhat. A szkript a kiírt árat rögzíti, NEM számol át; svájci
kapcsolatról ellenőrizni kell.

Udvariasság: egyetlen szálon, kérések között >= 1,2 s, böngésző User-Agent; nincs bejelentkezés/kosár.
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

SHOP = "tellsarmory.ch"
BASE = "https://www.tellsarmory.ch"
HOSTS = ("www.tellsarmory.ch", "tellsarmory.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "tellsarmory.json")

SOURCE = (BASE + "/shop/category/waffenzubehor-7, /shop/category/waffen-sturmgewehre-3, /shop?search=<márka> "
          "(Odoo HTML) + termékoldalak")
PRICE_BASIS = ("FIGYELEM: a bolt kiírt ára a lekérdezéskor (nem svájci IP-ről) láthatóan MWST NÉLKÜLI volt: minden ár "
               "x1.081 kerek .90/.95 végű CHF-összeg (pl. 23.08 -> 24.95, 55.41 -> 59.90, 1850.05 -> 1999.90), az AGB "
               "szerint viszont svájci vevőnek minden ár MWST-vel értendő. A price_chf a ténylegesen kiírt ár, "
               "átszámítás nélkül; svájci kapcsolatról a fizetendő ár valószínűleg 8.1%-kal magasabb - "
               "összehasonlítás előtt ellenőrizni kell. Készletadatot a bolt statikusan nem közöl (in_stock = null).")
ITEM_NOTE = "a kiírt ár MWST nélkülinek tűnik (lásd price_basis), svájci kapcsolatról ellenőrizendő"
COLOR_ORDER = ("name", "attr", "sku")

CATEGORIES = ["waffenzubehor-7", "waffen-sturmgewehre-3"]
QUERIES = ["magpul", "bcm", "b5 systems", "radian", "geissele", "aimpoint", "eotech", "holosun", "trijicon",
           "vortex", "surefire", "streamlight", "harris", "blue force", "schmeisser", "daniel defense"]


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
    # ---- alapfegyverek
    "schm-s4f": R(None, why=NOT_SOLD), "schm-m4fl": R(None, why=NOT_SOLD), "schm-m5fl": R(None, why=NOT_SOLD),
    "schm-dyn": R(None, why=NOT_SOLD),
    "dd-v7": R(None, why="csak DD4 MK18 RIII / DD4 RIII S / DD5 / PCC9 (más modellek); DDM4 V7 nincs"),
    "bcm-recce16": R(None, why="BCM fegyver nincs a boltban (csak BCM előmarkolatok)"),
    # ---- markolatok
    "mp-moe": R(None),
    "mp-moek2": R(r"magpul moe 2k grip{C}", skureq=r"MAG522",
                  note="a bolt 'MOE 2K Grip' néven listázza; a cikkszám (MAG522) és a leírás szerint MOE-K2"),
    "mp-moek2p": R(r"magpul moe k2\+ grip ar15/m4{C}"),
    "mp-miad": R(None),
    "bcm-g0": R(None, why="BCM pisztolymarkolat nincs a boltban (csak előmarkolatok)"),
    "bcm-g3": R(None, why="BCM pisztolymarkolat nincs a boltban (csak előmarkolatok)"),
    "b5-t22": R(None), "b5-t23": R(None),
    # ---- tusok
    "mp-ctr": R(r"magpul ctr carbine stock{C} mil spec", note="Mil-Spec csöves kivitel"),
    "mp-moesl": R(None, why="csak MOE SL-K és SL-M tus (más termékek)"),
    "mp-moeslk": R(r"magpul moe sl-k carbine stock mil spec{C}", note="Mil-Spec csöves kivitel"),
    "mp-ubr2": R(None), "mp-prs3": R(None), "mp-prslite": R(None),
    "bcm-s0": R(None), "bcm-s1": R(None),
    "b5-bravo": R(r"b5 systems sopmod bravo stock mil spec", check=r"B5 Bravo-System|Bravo-Stütze",
                  note="a bolti név 'Sopmod Bravo Stock'; a leírás szerint a B5 Bravo tus (a SOPMOD-ra épülő, "
                       "tárolórekesz nélküli kivitel), Mil-Spec; az oldalon kiírt ár az alapértelmezett (Black) "
                       "változaté, más színhez a bolt nem jelez felárat"),
    "b5-esop": R(None, why="csak a B5 Bravo tus (a leírás szerint a SOPMOD-ra épül, de nem az Enhanced SOPMOD)"),
    "b5-prec": R(None),
    # ---- tustalpak
    "mp-pad030": R(None), "mp-pad055": R(None), "mp-pad070": R(None), "mp-padsl": R(None),
    # ---- red dot / holo
    "ap-t2": R(None), "ap-m5": R(None), "ap-pro": R(None), "ap-acro": R(None, why=ACRO_BASE),
    "eo-exps3": R(r"eotech exps3 holographic sight", check=r"DEFAULT:.*Absehen: 3-0\b",
                  note="a bolt egy termékként árulja szín- (Black/FDE) és szálkereszt-változatokkal (3-0/3-1/3-2/"
                       "3-4/DCR); a kiírt ár az alapértelmezett Black + 3-0 (= EXPS3-0) kombinációé, felárat a "
                       "bolt nem jelez"),
    "eo-exps2": R(None), "eo-xps2": R(None),
    "hs-510c": R(None), "hs-aems": R(None), "hs-403b": R(None), "tj-mro": R(None),
    "vx-uh1": R(r"vortex optics amg uh-1 gen ii{C}"),
    "vx-sparc": R(None), "vx-crossfire": R(None),
    # ---- LPVO
    "vx-se16": R(r"vortex optics strike eagle 1-6 x 24 sfp ar-bdc3{C}", note="AR-BDC3 szálkereszt, SFP"),
    "vx-se18": R(None, why="csak 'Strike Eagle 1-8x24 FFP EBR-8' (első fókuszsíkos; a data.js SFP kivitelt ír)"),
    "vx-pst16": R(None),
    "vx-razor110": R(None, why="csak 'AMG 1-10x24 FFP EBR-9' (más termékcsalád), Razor HD Gen III 1-10x24 nincs"),
    "tj-acog": R(None), "tj-credo16": R(None), "tj-vcog": R(None),
    "eo-vudu16": R(None),
    "eo-vudu110": R(r"eotech vudu 1-10 sr5", check=r"1-10x28",
                    note="Vudu 1-10x28 FFP, SR5 (MRAD) szálkereszt, 34 mm; a kiírt ár az alapértelmezett (Black) "
                         "változaté"),
    # ---- optikaszerelék
    "gs-sp30": R(None), "vx-cant30": R(None),
    # ---- nagyítók
    "eo-g33": R(None),
    "eo-g43": R(None, why="G43 + 'Micro' szerelék csomag: a bolti G43 (Tan) a leírás szerint 'Flip-to-Side' (STS) "
                          "szerelékkel érkezik"),
    "ap-3x": R(None), "hs-hm3x": R(None), "vx-m3x": R(None),
    # ---- BUIS
    "mp-mbus3": R(None), "mp-mbuspro": R(None), "mp-mbusoff": R(None),
    # ---- lámpák
    "sf-m600": R(None),
    "sf-m640": R(r"surefire m640df dual fuel scout light pro{C}",
                 note="a nem-Turbo M640DF csak Tan színben kapható; a fekete csak 'M640DF Dual Fuel Turbo' "
                      "(M640DFT, más típus)"),
    "sf-m300": R(None, why="csak M340C / M340V Mini Scout Light Pro (más típus); M300 nincs"),
    "sf-x300": R(r"surefire x300u-b{C}"),
    "sl-hlx": R(None, why="csak TLR-1 HL-X USB / TLR-7 (más típus); ProTac Rail Mount HL-X nincs"),
    "sl-tlr1": R(r"streamlight tlr-1 hl{C}"),
    # ---- M-LOK
    "mp-rvg": R(None, why="csak 'MOE RVG Grip' (Picatinny) és 'M-LOK MOE MVG Grip' - az M-LOK RVG nincs, nem azonos"),
    "mp-hstop": R(None),
    "bcm-vg3": R(r"bcm gunfighter vertical grip mod3 m-lok{C}", note="M-LOK kivitel (a Picatinny változat külön tétel)"),
    "bcm-kag": R(r"bcm kag grip m-lok{C}", note="KAG (Kinesthetic Angled Grip), M-LOK"),
    "mp-rail5": R(None, why="csak 7 slotos alumínium M-LOK sín (MAG582) kapható"),
    "mp-qd": R(r"magpul m-lok qd sling mount"),
    "mp-cover": R(None, why="csak Wilson Combat M-LOK takaró (más márka)"),
    "mp-cable": R(None, why=CABLE),
    # ---- bipod
    "mp-bipod": R(r"magpul bipod m-lok{C}", note="Magpul Bipod M-LOK (alumínium)"),
    "hr-sbrm": R(None),
    # ---- torkolat
    "sf-warcomp": R(None), "sf-3p": R(None), "bcm-comp0": R(None), "sf-rc2": R(None),
    # ---- elsütők, felhúzókarok
    "gs-ssae": R(None), "gs-ssa": R(None), "gs-sde": R(None), "gs-s3g": R(None),
    "rd-raptor": R(r"radian weapons raptor charging handle for ar15/m16{C}",
                   note="csak OD Green színben kapható"),
    "rd-raptorlt": R(r"radian weapons raptor-lt charging handle for ar15/m16{C}",
                     note="AR-15 kivitel (az MCX-es más termék)"),
    "rd-raptorsd": R(None), "bcm-ch4": R(None), "bcm-ch3": R(None), "gs-ach": R(None),
    # ---- kezelőszervek
    "rd-talon": R(None),
    "mp-bad": R(r"magpul b\.a\.d\. lever bad lever battery assist"),
    "mp-trigguard": R(None),
    # ---- tárak
    "mp-pm10": R(None), "mp-pm20": R(None),
    "mp-pm30": R(None, why="csak a Window (ablakos) PMAG 30 Gen M3 kapható"),
    "mp-pm30w": R(r"magpul pmag 30 ar/m4 gen m3 window{C}",
                  note="a 'TAN' változat nem szerepel a data.js palettáján (black/fde)"),
    "mp-pm40": R(None), "mp-d60": R(None),
    # ---- szíjak
    "mp-ms1": R(None), "mp-ms4": R(None), "bfg-vickers": R(None),
}


# =========================================================================== bolt-specifikus: katalógus
def soup(markup):
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")


def absu(u):
    return urllib.parse.urljoin(BASE + "/", u) if u else None


def parse_listing(s, cards, seen):
    prods = s.select(".oe_product")
    for p in prods:
        a = p.select_one("a[itemprop=name]")
        if not a or not a.get("href"):
            continue
        url = absu(a["href"].split("?")[0])
        if url in seen:
            continue
        seen.add(url)
        pr = p.select_one("[itemprop=price]")
        lp = p.select_one(".oe_default_price")
        lst = None
        if lp is not None and "d-none" not in (lp.get("class") or []):
            lst = parse_chf(lp.get_text(" "))
        img = p.select_one("img")
        alt = norm(img.get("alt")) if img else ""
        m = re.match(r"\[([^\]]+)\]", alt)
        price = parse_chf(pr.get_text(" ")) if pr else None
        cards.append(dict(name=norm(a.get_text(" ")), url=url, price=price,
                          list=lst if (lst and price and lst > price) else None, in_stock=None, stock_text="",
                          sku=m.group(1) if m else "", image=absu(img.get("src")) if img else None,
                          weight_g=None, dims=None, attr_color=None))
    return prods


def max_page(s):
    nums = [int(x) for x in re.findall(r"/page/(\d+)", " ".join(a.get("href") or ""
                                                               for a in s.select("ul.pagination a")))]
    return max(nums) if nums else 1


def fetch_catalog(http):
    cards, seen = [], set()
    for cat in CATEGORIES:
        page, last = 1, 1
        while page <= last and page <= 30:
            s = soup(http.get(BASE + "/shop/category/%s%s" % (cat, "/page/%d" % page if page > 1 else "")))
            if not parse_listing(s, cards, seen):
                break
            last = max(last, max_page(s))
            page += 1
        sys.stderr.write("  kategória '%s': %d termék eddig\n" % (cat, len(cards)))
    for q in QUERIES:
        page, last = 1, 1
        while page <= last and page <= 10:
            s = soup(http.get(BASE + "/shop%s?%s" % ("/page/%d" % page if page > 1 else "",
                                                     urllib.parse.urlencode({"search": q}))))
            if not parse_listing(s, cards, seen):
                break
            last = max(last, max_page(s))
            page += 1
        sys.stderr.write("  keresés '%s': %d termék eddig\n" % (q, len(cards)))
    return cards


def enrich(http, card):
    """Termékoldal: kép, leírás, Odoo-változatok (alapértelmezett kombináció; felár nélküli színek)."""
    s = soup(http.get(card["url"]))
    d = s.select_one("#product_details") or s
    h1 = d.select_one("h1")
    if h1 is None or key(h1.get_text(" ")) != key(card["name"]):
        card["page_text"] = ""
        card["_note"] = "a termékoldal címe eltér a listától (%s)" % (norm(h1.get_text(" ")) if h1 else "?")
        return [card]
    pr = d.select_one("[itemprop=price]")
    if pr is not None and parse_chf(pr.get_text(" ")) is not None:
        card["price"] = parse_chf(pr.get_text(" "))
    im = d.select_one("span[itemprop=image]")
    if im is not None and im.get_text(strip=True).startswith("http"):
        card["image"] = im.get_text(strip=True)
    defaults, colors, extras = {}, [], False
    for li in d.select("li.variant_attribute"):
        aname = norm(li.get("data-attribute_name") or "")
        for inp in li.select("input.js_variant_change, option"):
            val = norm(inp.get("data-value_name") or inp.get_text(" "))
            if inp.has_attr("checked") or inp.has_attr("selected"):
                defaults[aname] = val
            if aname.lower() == "farbe":
                colors.append(val)
        if li.select(".variant_price_extra, .badge"):
            extras = True
    desc = d.select_one(".oe_structure") or d
    card["page_text"] = norm(desc.get_text(" ")) + " || DEFAULT: " + "; ".join(
        "%s: %s" % kv for kv in defaults.items())
    if not defaults:
        return [card]
    other = [v for k, v in defaults.items() if k.lower() != "farbe"]
    base_name = card["name"] + (" – " + " / ".join(other) if other else "")
    dcol = defaults.get("Farbe")
    out = []
    main = dict(card, name=base_name + (" – " + dcol if dcol else ""), attr_color=dcol)
    out.append(main)
    if colors and not extras:  # felár nélküli színváltozatok: a bolt ugyanazt az árat mutatja
        for col in colors:
            if col != dcol:
                c = dict(card, name=base_name + " – " + col, attr_color=col,
                         _note="az oldalon kiírt ár az alapértelmezett (%s) színé; ehhez a színhez a bolt nem jelez "
                               "felárat" % dcol)
                out.append(c)
    return out


def prepare_hits(http, hits):
    out = []
    for c, mc in hits:
        out.extend((v, mc if v.get("attr_color") is None else None) for v in enrich(http, c))
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
