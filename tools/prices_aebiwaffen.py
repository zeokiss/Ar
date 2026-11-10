#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aebiwaffen.ch (Aebi Waffen GmbH, Hasle b. Burgdorf BE) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_aebiwaffen.py                        # minden termék -> assets/prices/aebiwaffen.json
    python3 tools/prices_aebiwaffen.py --only eo-xps2,tj-acog  # csak néhány id (stdout, ha nincs --out)
    python3 tools/prices_aebiwaffen.py --cache /tmp/aebi       # HTTP-gyorsítótár (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  1. A bolt "blue office E-Shop" (ASP.NET). A saját keresőjét használja: /de/suche?s=<szó>&seite=N
     (50 találat / oldal). A találati kártyák tartalmazzák a nevet, az árat (meta itemprop=price,
     MWST-vel), az áthúzott árat ("statt"), a készletet (schema.org availability + "Verfügbar"/
     "Bestellt" + darabszám), a cikkszámot és a kép URL-jét.
  2. Minden data.js id-hoz a RULES táblában van a bolti név PONTOS (fullmatch) mintája, szükség
     szerint cikkszám-feltétellel (pl. XPS2-0, G33.STS, TA31).
  3. Termékoldalt csak ott tölt le, ahol a szabály a leírás ellenőrzését kéri (pl. HS403B: a
     csomagban van-e a lower 1/3 magasító).
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

SHOP = "aebiwaffen.ch"
BASE = "https://www.aebiwaffen.ch"
HOSTS = ("www.aebiwaffen.ch", "aebiwaffen.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "aebiwaffen.json")

SOURCE = BASE + "/de/suche?s=<keresőszó>&seite=N (blue office E-Shop, HTML-találati lista) + néhány termékoldal"
PRICE_BASIS = ("a bolt kiírt fogyasztói ára ('inkl. 8.1% MwSt.'); akciónál list_chf = a 'statt' (áthúzott) ár. "
               "Készlet: schema.org InStock / LimitedAvailability ('Verfügbar') = true, OutOfStock ('Bestellt') = false")
COLOR_ORDER = ("name", "sku", "attr")
MAX_PAGES = 12

QUERIES = ["magpul", "pmag", "bcm", "gunfighter", "b5 systems", "geissele", "radian", "aimpoint", "eotech",
           "holosun", "trijicon", "vortex", "surefire", "streamlight", "harris", "blue force", "vickers",
           "schmeisser", "daniel defense"]


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
    "schm-s4f": R(r'halbautomat schmeisser ar15 s4f \.223 10\.5"{C}',
                  note="standard kivitel (Slim Line M-LOK előagy, 10.5\")"),
    "schm-m4fl": R(r'halbautomat schmeisser ar15 m4fl \.223 14\.5"{C}',
                   note="standard kivitel (Slim Line M-LOK előagy, 14.5\")"),
    "schm-m5fl": R(None), "schm-dyn": R(None),
    "dd-v7": R(None, why="a fekete alap DDM4 V7 nincs a boltban: csak 'DDM4 V7 FDE', 'DDM4V7, Rattlecan', 'DDM4 V7 Law "
                         "Enforcement Edition' és V7 SLW / V7 K változatok (más kivitel/modell; a data.js csak fekete "
                         "kivitelt ír)"),
    "bcm-recce16": R(None, why=NOT_SOLD),
    # ---- markolatok
    "mp-moe": R(r"magpul moe grip ar15/m4{C}",
                note="a 'FOL' (Foliage) változat nem szerepel a data.js Magpul-palettáján"),
    "mp-moek2": R(None, why="csak az AK-hoz való MOE K2 markolat (más termék)"),
    "mp-moek2p": R(r"magpul moe-k2\+ grip ar15/m4{C}"),
    "mp-miad": R(None),
    "bcm-g0": R(None, why="csak 'BCM Jack Carr Grip Mod 4' (más típus)"),
    "bcm-g3": R(None, why="csak 'BCM Jack Carr Grip Mod 4' (más típus)"),
    "b5-t22": R(None), "b5-t23": R(None),
    # ---- tusok
    "mp-ctr": R(r"magpul ctr carbine stock mil-spec{C}", note="Mil-Spec csöves kivitel"),
    "mp-moesl": R(None), "mp-moeslk": R(None), "mp-ubr2": R(None),
    "mp-prs3": R(r"magpul prs gen3 precision-adjustable stock{C}"),
    "mp-prslite": R(r"magpul prs lite stock{C}"),
    "bcm-s0": R(None), "bcm-s1": R(None), "b5-bravo": R(None), "b5-esop": R(None), "b5-prec": R(None),
    # ---- tustalpak
    "mp-pad030": R(None), "mp-pad055": R(None),
    "mp-pad070": R(r'magpul enhanced rubber butt-pad 0\.70"'),
    "mp-padsl": R(None),
    # ---- red dot / holo
    "ap-t2": R(None, why="a bolt két T-2 csomagot árul: 'Picatinny Montage mit 39mm Spacer' (AP-200451, a leírás "
                         "szerint NAR szerelékkel) és 'Micro T-2 schwarz 2 MOA Picatinny' (AP-200170, integrált "
                         "szerelék); LRP szerelék egyiknél sincs feltüntetve"),
    "ap-m5": R(None),
    "ap-pro": R(r"aimpoint pro{C}", skureq=r"12841",
                note="Aimpoint PRO készlet (AP-12841); a bolti leírás szerint a szerelék és a távtartó a csomag része "
                     "(a szerelék típusát - QRP2 - a bolt nem írja ki)"),
    "ap-acro": R(None, why="csak ACRO O-2 / C-2 (más modellek) és használt ACRO P-2 talp nélkül"),
    "eo-exps3": R(r"eotech hws exps3 0{C}", skureq=r"^EXPS3-0"),
    "eo-exps2": R(r"eotech hws exps2 0{C}", skureq=r"^EXPS2-0", excl=r"green|grn"),
    "eo-xps2": R(r"eotech hws xps2 0{C}", skureq=r"^XPS2-0(TAN)?$",
                 note="a bolt egy másik 'XPS2 - 0' nevű tétele valójában XPS2-1 cikkszámú (790.00 CHF) - kihagyva"),
    "hs-510c": R(r"holosun hs510c red circle dot"),
    "hs-aems": R(r"holosun aems red red 2 moa dot & 65 moa circle",
                 note="az alap AEMS (piros, 2 MOA pont + 65 MOA kör); az AEMS Core / Pro más modell"),
    "hs-403b": R(r"holosun hs403b red 2 moa dot", check=r"lower 1/3 co-witness mount",
                 note="a bolti leírás szerint a csomag tartalmazza a lower 1/3 co-witness magasítót (1.63\") és az "
                      "alacsony szereléket"),
    "tj-mro": R(None, why="csak MRO SD 1x25 és 'MRO HD Patrol + 3x Magnifier' csomag (más kivitel/csomag); MRO HD + "
                          "Lower 1/3 szerelék nincs"),
    "vx-uh1": R(None),
    "vx-sparc": R(None, why="csak 'SPARC Solar' (más modell); SPARC AR + magasító nincs"),
    "vx-crossfire": R(r"vortex optics crossfire red dot sight 2 moa", check=r"1/3 Co-Witness-Montage ist im Set",
                      note="a bolti leírás szerint a gyári alacsony szerelék mellett a lower 1/3 co-witness magasító "
                           "is a csomag része"),
    # ---- LPVO
    "vx-se16": R(None),
    "vx-se18": R(r"vortex strike eagle 1-8x24 ar-bdc2",
                 note="AR-BDC2 szálkeresztes (korábbi generációs) SFP kivitel; a data.js nem határozza meg a "
                      "szálkeresztet"),
    "vx-pst16": R(r"vortex viper pst gen ii 1-6x24 sfp vmr-2 mrad", note="VMR-2 MRAD szálkereszt"),
    "vx-razor110": R(None),
    "tj-acog": R(r"zielgerät trijicon (?:trijicon )?acog 4x32 bac riflescope \.223 5\.56 bdc", skureq=r"TA31",
                 note="TA31 (ACOG 4x32 BAC, .223/5.56 BDC); a bolt több szálkereszt-változatot árul (TA31-C-100370, "
                      "TA31F red chevron, TA31-CH-G green crosshair, TA31F-A amber chevron), a data.js nem határozza "
                      "meg a szálkeresztet; a fő ár a raktáron lévő legolcsóbb. A 300 BLK változat más termék."),
    "tj-credo16": R(None, why="csak 'Credo HX 1-6x24' (külön, olcsóbb termékcsalád)"),
    "tj-vcog": R(None, why="csak VCOG 1-6x24 (a data.js 1-8x28-at ír)"),
    "eo-vudu16": R(None), "eo-vudu110": R(None),
    # ---- optikaszerelék
    "gs-sp30": R(None),
    "vx-cant30": R(None, why="a bolti 'Cantilever Ring Mount 30mm 2-inch offset' a régebbi gyűrűs Cantilever, nem a "
                             "'Sport Cantilever 30mm' (más termék)"),
    # ---- nagyítók
    "eo-g33": R(r"eotech magnifier g33{C}", skureq=r"G33\.STS", note="G33.STS (STS szerelékkel)"),
    "eo-g43": R(None, why=G43_STS),
    "ap-3x": R(None), "hs-hm3x": R(r"holosun hm3x magnifier", note="a HM3X-MICRO és a HM3X-T más termék"),
    "vx-m3x": R(None),
    # ---- BUIS
    "mp-mbus3": R(None, why=SET_BUIS), "mp-mbuspro": R(None, why=SET_BUIS), "mp-mbusoff": R(None, why=SET_BUIS),
    # ---- lámpák
    "sf-m600": R(None),
    "sf-m640": R(r"lampe surefire mod\. m640df dual fuel scout pro{C} max\. lumen 1500"),
    "sf-m300": R(None, why="csak M340C Scout Pro Compact (más típus); M300 nincs"),
    "sf-x300": R(r"lampe surefire tactical x300u-b ultra led{C}"),
    "sl-hlx": R(None),
    "sl-tlr1": R([r"lampe streamlight tlr-1 hl 1000 lm stroboskop", r"lampe streamlight tlr-1 hl tactical led light{C}"],
                 note="a bolt két tételként listázza (eltérő ár/készlet)"),
    # ---- M-LOK
    "mp-rvg": R(None, why=RVG_PIC),
    "mp-hstop": R(r"magpul m-lok hand stop kit{C}"),
    "bcm-vg3": R(r"bcm gunfighter vertical grip m-lok{C} mod 3", note="M-LOK kivitel (a Picatinny külön tétel)"),
    "bcm-kag": R(None),
    "mp-rail5": R(r"magpul m-lok aluminium rail 5 slots"),
    "mp-qd": R(r"magpul m-lok qd sling mount"),
    "mp-cover": R(r"magpul m-lok rail cover type 2{C}", note="a színt a cikkszám adja (MAG603-FDE)"),
    "mp-cable": R(None, why=CABLE),
    # ---- bipod
    "mp-bipod": R(None),
    "hr-sbrm": R(r'harris zweibeinstütze s-brm 6"- ?9" mlok', note="S-BRM M-LOK kivitel (HB-S-BRM-MLOK)"),
    # ---- torkolat
    "sf-warcomp": R(r"feuerscheindämpfer surefire warcomp 556-1/2-28", note="a CTN (zárt ágú) változat más termék"),
    "sf-3p": R(r"feuerscheindämpfer surefire socom 3 prong flash hider", skureq=r"SF3P-556",
               note="SF3P-556-1/2-28"),
    "bcm-comp0": R(None),
    "sf-rc2": R(None, why="csak a SOCOM556-RC2 hangtompító önmagában; a data.js 'RC2 + WarComp' csomagot ír"),
    # ---- elsütők, felhúzókarok
    "gs-ssae": R(None), "gs-ssa": R(None, why="csak 'Super MPX SSA Trigger' (SIG MPX-hez, más termék)"),
    "gs-sde": R(None), "gs-s3g": R(None),
    "rd-raptor": R(r"radian raptor ar-15/m-16"),
    "rd-raptorlt": R(r"radian raptor lt ambidextrous charging handle", note="AR-15, fekete (a SIG MPX-es más termék)"),
    "rd-raptorsd": R(r"radian raptor sd ambidextrous charging handle"),
    "bcm-ch4": R(None), "bcm-ch3": R(None),
    "gs-ach": R(None, why="csak 'Geissele Super Charging Handle' (más termék)"),
    # ---- kezelőszervek
    "rd-talon": R(None),
    "mp-bad": R(r"magpul b\.a\.d\. lever battery assist device ar15/m4"),
    "mp-trigguard": R(None, why="csak 'Magpul MOE Trigger Guard' (nem az 'Enhanced' kivitel, más termék)"),
    # ---- tárak
    "mp-pm10": R(None, why="a bolti 'PMAG Window AR-15 .223 Rem 10 Schuss GEN M3' neve ablakos kivitelt ír, cikkszáma "
                           "(MAG559-BLK) viszont az ablak nélküli PMAG 10 Gen M3-é - ellentmondásos, nem azonosítható "
                           "biztosan"),
    "mp-pm20": R(None),
    "mp-pm30": R(None, why="a bolti 'AR15 PMAG Gen 3 Magazin 30 Schuss Magpul' (cikkszám 109824) nem adja meg "
                           "egyértelműen a Gen M3 / ablak nélküli kivitelt"),
    "mp-pm30w": R(r"magazin magpul pmag window ar-15 \.223 rem 30 schuss gen m3{C}",
                  note="az ODG változat nem szerepel a data.js palettáján (black/fde)"),
    "mp-pm40": R(None, why="a bolti 'Magazin Magpul PMAG 40 AR-15 .223 Rem 40Schuss' neve nem tartalmazza a 'Gen M3' "
                           "jelölést (cikkszám Mag223-BLK) - nem azonosítható biztosan"),
    "mp-d60": R(None),
    # ---- szíjak
    "mp-ms1": R(None),
    "mp-ms4": R(r"magpul ms4 dual qd sling gen2{C}", note="Gen 2 kivitel; a 'Grün' tétel cikkszáma MAG518RGR (Ranger "
                                                          "Green)"),
    "bfg-vickers": R(None, why="a bolt a Blue Force Gear-től csak tártáskát/elsősegély-tasakot árul, Vickers szíjat nem"),
}


# =========================================================================== bolt-specifikus: katalógus
def soup(markup):
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")


AVAIL = {"InStock": True, "LimitedAvailability": True, "OutOfStock": False, "PreOrder": False,
         "BackOrder": False, "Discontinued": False, "SoldOut": False}


def card_from_li(li):
    a = li.select_one(".bezeichnung a")
    if a is None or not a.get("href"):
        return None
    pm = li.select_one("[itemprop=price]")
    st = li.select_one(".statt-preis del")
    av = li.select_one("link[itemprop=availability]")
    lag = li.select_one(".lager")
    cnt = li.select_one(".dyn-bestandtext")
    sku = li.select_one(".art-nr")
    img = li.select_one("meta[itemprop=image]")
    avail = (av.get("href") or "").rsplit("/", 1)[-1] if av is not None else ""
    price = float(pm["content"]) if pm is not None and pm.get("content") else None
    lst = parse_chf(st.get_text(" ")) if st is not None else None
    txt = norm(lag.get("title")) if lag is not None else ""
    if cnt is not None and norm(cnt.get_text(" ")):
        txt = "%s (Bestand: %s)" % (txt, norm(cnt.get_text(" ")))
    return dict(name=norm(a.get_text(" ")), url=a["href"], price=price,
                list=lst if (lst and price and lst > price) else None, in_stock=AVAIL.get(avail),
                stock_text=txt, sku=norm(sku.get_text(" ")) if sku is not None else "",
                image=img.get("content") if img is not None else None, weight_g=None, dims=None, attr_color=None)


def fetch_catalog(http):
    cards, seen = [], set()
    for q in QUERIES:
        page = 1
        while page <= MAX_PAGES:
            url = BASE + "/de/suche?" + urllib.parse.urlencode({"s": q}) + ("&seite=%d" % page if page > 1 else "")
            s = soup(http.get(url))
            for li in s.select("li.artikel"):
                c = card_from_li(li)
                if c and c["url"] not in seen:
                    seen.add(c["url"])
                    cards.append(c)
            if not any(("seite=%d" % (page + 1)) in (x.get("href") or "") for x in s.find_all("a", href=True)):
                break
            page += 1
        sys.stderr.write("  keresés '%s': %d termék eddig\n" % (q, len(cards)))
    return cards


def prepare_hits(http, hits):
    return hits  # a találati kártyák minden szükséges adatot tartalmaznak


def page_text(http, card):
    if "page_text" not in card:
        s = soup(http.get(card["url"]))
        art = s.select_one("article.artikel") or s
        h1 = art.select_one("h1")
        if h1 is None or key(h1.get_text(" ")) != key(card["name"]):
            card["page_text"] = ""
        else:
            ds = art.select_one(".beschreibung")
            card["page_text"] = norm(h1.get_text(" ")) + " || " + (norm(ds.get_text(" ")) if ds else "")
            big = art.select_one("a.hauptbild[href]")
            if big is not None and big["href"].startswith("http"):
                card["image"] = big["href"]
    return card["page_text"]


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
