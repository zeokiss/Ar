#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schildwaffen.ch (Schild Waffen AG, Wallbach AG) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_schildwaffen.py                         # -> assets/prices/schildwaffen.json
    python3 tools/prices_schildwaffen.py --only sf-x300,sl-tlr1    # részleges futás (stdout-ra ír)
    python3 tools/prices_schildwaffen.py --dump                    # csak a keresési találatok listája

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés (Opacc webshop)
  1. A js/data.js-ből kiolvassa a BASES és ITEMS azonosítókat (márka, név, színpaletta).
  2. A QUERIES keresőkifejezésekkel lekéri a bolt keresőjét (/de/search/Section1.htm?query=…;
     a 'Mehr Produkte laden' lapozás a bolt munkamenet-sütijével működik), és a kártyákból
     közös találati készletet épít. Egyetlen találatnál a bolt egyből a termékoldalra irányít.
  3. A bolt az árakat nem a HTML-ben, hanem JavaScripttel tölti be: a lap minden cikkéhez
     ugyanazt a csak olvasó ár-lekérdezést küldjük, amit a böngésző (POST az oldal
     'article-price-url' címére, artIntNoPrice=<a lap cikk-azonosítói>, ugyanabban a munkamenetben).
     A válaszból: price-cust = fizetendő ár, price-basis = alapár (ha nagyobb, ez az áthúzott ár).
  4. Minden id-hez a RULES táblában van a bolti terméknév PONTOS (fullmatch) mintája; a minta
     'color' nevű csoportja a színt fogja meg (a bolt a színeket külön cikként árulja).
  5. Egyezésnél a termékoldalt is letölti: Art.-szám, 'Modell' (gyártói cikkszám), készlet
     ('an Lager' / 'wenig an Lager' / …), kép-URL, és az oldal saját ár-lekérdezésével az árat.
  Árat soha nem becsül és nem számol. Az ÁSZF az árakat EXW-ként írja le; azt, hogy a megjelenített
  ár MWST-t tartalmaz-e, az oldal nem írja ki (lásd research/shopinfo/schildwaffen.json).

Udvariasság: egyetlen szálon, kérésenként legalább 1,2 s szünettel, böngésző User-Agent.
Botvédelem (403, Cloudflare/captcha oldal, tartós 429) esetén azonnal leáll, és a JSON-ba
'blocked' okot ír. Csak a www.schildwaffen.ch-ra kérdez; képeknek csak az URL-jét rögzíti.
Nincs bejelentkezés, kosár vagy pénztár; gyorsítótár nincs (az ár-lekérdezés munkamenethez kötött).
"""
import argparse
import datetime
import hashlib
import html as htmllib
import http.cookiejar
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

SHOP = "schildwaffen.ch"
BASE = "https://www.schildwaffen.ch"
HOSTS = ("www.schildwaffen.ch", "schildwaffen.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "schildwaffen.json")
PRICE_NOTE = ("A bolt saját ár-lekérdezéséből (price-cust) kiolvasott CHF-ár. Az oldal nem írja ki, hogy az ár "
              "MWST-vel értendő-e (az ÁSZF: 'Preise ... EXW; Mehrwertsteuern ... gehen zu Lasten des Kunden'). "
              "A színváltozatok külön cikkek. in_stock a termékoldal készletjelzéséből ('an Lager' = true).")


class Blocked(Exception):
    pass


# --------------------------------------------------------------------------- HTTP
class Http:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir
        self.last = 0.0
        self.n = 0
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def get(self, url, cache=True):
        host = urllib.parse.urlparse(url).netloc
        if host not in HOSTS:
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir and cache:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, encoding="utf-8") as f:
                    d = json.load(f)
                return d["body"], d["url"]
        hdr = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5", "Accept": "*/*"}
        err, n429 = None, 0
        for attempt in range(5):
            w = MIN_GAP - (time.time() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.time()
            self.n += 1
            try:
                with self.opener.open(urllib.request.Request(url, headers=hdr), timeout=60) as r:
                    body = r.read().decode("utf-8", "replace")
                    final = r.geturl()
                if re.search(r"cf-chl|challenge-platform|Just a moment\.\.\.|Verifying (you are human|connection)"
                             r"|g-recaptcha|hcaptcha", body[:8000], re.I):
                    raise Blocked("botvédelmi (challenge/captcha) oldal: " + url)
                if ck:
                    with open(ck, "w", encoding="utf-8") as f:
                        json.dump({"url": final, "body": body}, f)
                return body, final
            except urllib.error.HTTPError as e:
                err = e
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
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                err = e  # a bolt néha bontja a kapcsolatot (ECONNRESET) – visszalépéses újrapróbálás
                time.sleep(5 * (attempt + 1))
        raise RuntimeError("letöltés sikertelen: %s (%s)" % (url, err))


    def post_form(self, url, fields):
        """Az oldal saját XHR-je (ár-lekérdezés) – ugyanabban a munkamenetben, gyorsítótár nélkül."""
        host = urllib.parse.urlparse(url).netloc
        if host not in HOSTS:
            raise ValueError("nem engedélyezett host: " + host)
        data = urllib.parse.urlencode(fields).encode()
        hdr = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5", "Accept": "*/*",
               "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
               "X-Requested-With": "XMLHttpRequest"}
        err, n429 = None, 0
        for attempt in range(4):
            w = MIN_GAP - (time.time() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.time()
            self.n += 1
            try:
                with self.opener.open(urllib.request.Request(url, data=data, headers=hdr), timeout=60) as r:
                    return r.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                err = e
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
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                err = e
                time.sleep(5 * (attempt + 1))
        raise RuntimeError("ár-lekérdezés sikertelen: %s (%s)" % (url, err))


def soup(markup):
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def parse_chf(text):
    """'CHF 1'042.00' -> 1042.0"""
    m = re.search(r"([0-9][0-9'’ ]*(?:\.[0-9]{1,2})?)", norm(text))
    return float(re.sub(r"['’ ]", "", m.group(1))) if m else None


# --------------------------------------------------------------------------- data.js
PALETTES = {"MAGPUL": ["black", "fde", "odg", "gray"], "BCM": ["black", "fde", "foliage", "wolfgray"],
            "B5": ["black", "fde", "odg", "wolfgray"]}


def read_data_js():
    """-> [(id, brand, name, paletta-lista)]  (a 'factory'/'none' elemek nélkül)"""
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    for k in list(PALETTES):
        m = re.search(r"const %s = \[([^\]]*)\]" % k, src)
        if m:
            PALETTES[k] = re.findall(r"'([a-z]+)'", m.group(1))
    starts = list(re.finditer(r"\{\s*id:\s*'([^']+)',", src))
    out = []
    for i, m in enumerate(starts):
        chunk = src[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(src)]
        chunk = chunk[: chunk.find("}") + 1 or len(chunk)]
        bm = re.search(r"brand:\s*'((?:[^'\\]|\\.)*)'", chunk)
        nm = re.search(r"name:\s*'((?:[^'\\]|\\.)*)'", chunk)
        if not bm or not nm:
            continue
        pal = []
        cm = re.search(r"(?:colors|finishes):\s*(\[[^\]]*\]|[A-Z0-9_]+)", chunk)
        if cm:
            v = cm.group(1)
            pal = re.findall(r"'([a-z]+)'", v) if v.startswith("[") else list(PALETTES.get(v, []))
        out.append((m.group(1), bm.group(1).replace("\\'", "'"), nm.group(1).replace("\\'", "'"), pal))
    return out


# --------------------------------------------------------------------------- színek
COLOR_WORDS = [
    (r"schwarz|black|blk|noir", "black"),
    (r"fde|flat dark earth", "fde"),
    (r"odg|od green|olive drab", "odg"),
    (r"stealth gr[ae]y|gr[ae]y|grau", "gray"),
    (r"wolf gr[ae]y", "wolfgray"),
    (r"foliage( green)?", "foliage"),
    (r"ranger green", "ranger"),
    (r"coyote( brown| tan)?", "coyote"),
    (r"bron[cz]e", "bronze"),
    (r"oliv(e)?|od", "olive"),
    (r"dark earth|earth", "earth"),
]


def color_key(label, palette):
    lab = norm(label).lower()
    for pat, key in COLOR_WORDS:
        if re.fullmatch(pat, lab):
            if key not in palette:
                # alapfegyver-kivitel: 'olive'/'earth' – Magpul-paletta: 'odg'/'fde'
                alt = {"olive": "odg", "odg": "olive", "earth": "fde"}.get(key)
                if alt in palette:
                    return alt
            return key
    return None


# --------------------------------------------------------------------------- szabályok
def R(title=None, near=None, why=None, note=None, mpn=None, default_color=None):
    """title: bolti terméknév (regex, fullmatch, kis/nagybetű-független; a 'color' csoport a színt
    fogja meg) – None: a bolt biztosan nem árulja ugyanezt; near: regex a tájékoztató
    'legközelebbi' termékekhez; why: indok az unmatched-hez; note: megjegyzés a felvett tételhez;
    mpn: a termékoldal 'Bestellnummer' mezőjének regexe (ha meg van adva, egyeznie kell);
    default_color: a szín nélküli bolti név színe (pl. 'black'), ha az oldal ezt igazolja."""
    return dict(title=title, near=near, why=why, note=note, mpn=mpn, default_color=default_color)


NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a márkát/terméket)"
SET_BUIS = "szett (első + hátsó irányzék): a bolt az elsőt és a hátsót külön termékként árulja, szett nincs"
C = r"(?P<color>Schwarz|Black|FDE|ODG|OD Green|Olive Drab|Grau|Grey|Gray|Stealth Grey|Coyote|Ranger Green)"

QUERIES = [
    "Magpul", "MOE", "MIAD", "CTR", "UBR", "PRS", "Butt Pad", "Handstop", "RVG", "M-LOK", "Bipod", "B.A.D.",
    "Trigger Guard", "PMAG", "PMAG 10", "PMAG 20", "PMAG 30", "PMAG 40", "D-60", "MS1", "MS4", "MBUS",
    "BCM", "B5 Systems", "Geissele", "Radian", "Raptor", "Talon", "Charging Handle",
    "Aimpoint", "Micro T-2", "CompM5", "Comp M5", "Aimpoint PRO", "Acro", "3XMag",
    "EOTech", "EXPS", "XPS2", "G33", "G43", "Vudu", "Holosun", "HS510C", "AEMS", "HS403", "HM3X",
    "Trijicon", "MRO", "ACOG", "Vortex", "Strike Eagle", "Cantilever",
    "SureFire", "M600", "M600DF", "M640", "M300", "X300U", "Scout", "WarComp", "SF3P", "SOCOM", "RC2",
    "Streamlight", "TLR-1", "ProTac", "Harris", "Vickers", "Blue Force",
    "Schmeisser", "S4F", "M4FL", "M5FL", "Dynamic", "Daniel Defense", "DDM4",
]

RULES = {
    # ---- alapfegyverek (a bolt fegyvert is árul; a szín a bolti névben: 'Bronce', 'BLK')
    "schm-s4f": R(r"Schmeisser S4F (?:(?P<color>Bronce|Bronze|BLK|Schwarz|Olive|OD Green) )?10\.5\" \.223 ?Rem",
                  near=r"Schmeisser .*S4F.*", default_color="black",
                  note="10.5\" .223 Rem; a szín nélküli bolti név a termékoldal szerint fekete"),
    "schm-m4fl": R(r"Schmeisser (?:AR15 )?M4FL 14\.5\" (?:(?P<color>Bronce|Bronze|BLK|Schwarz|Olive|OD Green) )?"
                   r"\.223 ?rem", near=r"Schmeisser .*M4FL.*", default_color="black",
                   note="14.5\" .223 Rem; a szín nélküli bolti név a termékoldal szerint fekete"),
    "schm-m5fl": R(r"Schmeisser AR15 M5FL (?:(?P<color>BLK|Bronce|Bronze|Olive|OD Green) )?16\.75\" \.223 ?rem",
                   near=r"Schmeisser .*M5FL.*"),
    "schm-dyn": R(r"Schmeisser AR15 Dynamic M5FL.*", near=r"Schmeisser .*Dynamic.*",
                  why="az AR15 Dynamic M5FL (.223) nincs a boltban"),
    "dd-v7": R(r"Daniel Defense DDM4 V7 .*", near=r"Daniel Defense DDM4.*", why="a DDM4 V7 nincs a boltban"),
    "bcm-recce16": R(None, why=NOT_SOLD),
    # ---- markolatok
    "mp-moe": R(r"Magpul MOE Grip " + C, near=r"Magpul MOE(\+| SL| K)? .*Grip.*"),
    "mp-moek2": R(r"Magpul MOE[- ]K2 (Grip )?" + C),
    "mp-moek2p": R(r"Magpul MOE[- ]K2\+ (Grip )?" + C),
    "mp-miad": R(r"Magpul MIAD Gen 1\.1 Grip Kit Type 1 " + C, near=r"Magpul MIAD.*",
                 note="AR-15 (Type 1) kivitel; a Type 2 (AR-10/SR-25) másik termék"),
    "bcm-g0": R(None, why=NOT_SOLD), "bcm-g3": R(None, why=NOT_SOLD),
    "b5-t22": R(None, why=NOT_SOLD), "b5-t23": R(None, why=NOT_SOLD),
    # ---- tusok
    "mp-ctr": R(r"Magpul CTR Carbine Stock Mil-?spec " + C, note="Mil-Spec csöves kivitel"),
    "mp-moesl": R(r"Magpul MOE SL Carbine Stock( Mil-?spec)? " + C, near=r"Magpul MOE SL .*",
                  why="a MOE SL Carbine Stock nincs a boltban (a 'Magpul MOE SL <szín>' cikkek a MOE SL "
                      "markolatok, nem a tus)"),
    "mp-moeslk": R(r"Magpul MOE SL-K Carbine Stock( Mil-?spec)? " + C, why="a MOE SL-K tus nincs a boltban"),
    "mp-ubr2": R(r"Magpul UBR Gen ?2.*", why="az UBR Gen2 tus nincs a boltban"),
    "mp-prs3": R(r"Magpul PRS (GEN3 Precision Adjustable Stock Milspec|Precision Adjustable Stock Gen3) " + C,
                 note="Mil-Spec csöves kivitel"),
    "mp-prslite": R(r"Magpul PRS Lite Stock Mil-?spec " + C),
    "bcm-s0": R(None, why=NOT_SOLD), "bcm-s1": R(None, why=NOT_SOLD), "b5-bravo": R(None, why=NOT_SOLD),
    "b5-esop": R(None, why=NOT_SOLD), "b5-prec": R(None, why=NOT_SOLD),
    # ---- tustalpak
    "mp-pad030": R(r"Magpul Rubber Butt[- ]?Pad,? 0\.30\"( .*)?", near=r"Magpul .*Butt[- ]?Pad.*",
                   why="a 0.30\" Rubber Butt-Pad nincs a boltban"),
    "mp-pad055": R(r"Magpul Extended Rubber Butt[- ]?Pad,? 0\.55\"( .*)?", near=r"Magpul .*Butt[- ]?Pad.*",
                   why="a 0.55\" Extended Rubber Butt-Pad nincs a boltban"),
    "mp-pad070": R(r"Magpul Enhanced Rubber Butt[- ]?Pad,? 0\.70\"", near=r"Magpul .*Butt[- ]?Pad.*"),
    "mp-padsl": R(r"Magpul MOE SL Enhanced Rubber Butt[- ]?Pad.*", near=r"Magpul .*Butt[- ]?Pad.*",
                  why="a MOE SL Enhanced Butt-Pad nincs a boltban"),
    # ---- red dot / holo
    "ap-t2": R(None, near=r"Aimpoint Micro T-2.*",
               why="csomag (T-2 + LRP szerelék): a bolti 'Micro T-2 2 MOA' cikk szerelékét az oldal nem nevezi "
                   "meg LRP-ként – nem azonosítható biztosan"),
    "ap-m5": R(None, near=r"Aimpoint Comp ?M5.*",
               why="csomag (CompM5 + LRP szerelék): ilyen csomag nincs a boltban (csak CompM5B + 3XMag Unity "
                   "csomag)"),
    "ap-pro": R(None, near=r"Aimpoint PRO.*", why="az Aimpoint PRO nincs a boltban"),
    "ap-acro": R(None, near=r"Aimpoint Acro P-2.*",
                 why="csomag (ACRO P-2 + Picatinny talp): a bolt az ACRO P-2-t talp nélkül árulja"),
    "eo-exps3": R(r"EOTech EXPS3-0 " + C, note="a bolt fekete és Tan kivitelt is árul; a fekete ára"),
    "eo-exps2": R(r"EOTech EXPS2-0 " + C),
    "eo-xps2": R(r"EOTech (HWS )?XPS2-0 " + C, near=r"EOTech (HWS )?XPS2.*"),
    "hs-510c": R(r"Holosun HS510C-RD", near=r"Holosun HE?510C.*",
                 note="HS510C (piros kör+pont); a HE510C-GR zöld változat külön cikk"),
    "hs-aems": R(r"Holosun AEMS(-RD)?", near=r"Holosun AEMS.*",
                 why="az eredeti (piros) 'AEMS' nincs a boltban; az 'AEMS-GR' (zöld), 'AEMS Core', 'AEMS-PRO-X2' "
                     "más típusjelzés"),
    "hs-403b": R(None, near=r"Holosun HS403.*", why="a HS403B + magasító csomag nincs a boltban"),
    "tj-mro": R(None, near=r"Trijicon MRO.*",
                why="csomag (MRO HD + Lower 1/3 szerelék): a bolti 'MRO-HD 68 Red Dot 2 MOA' cikk szerelékét az "
                    "oldal nem nevezi meg – nem azonosítható biztosan"),
    "vx-uh1": R(None, near=r"Vortex .*UH-1.*", why="a Vortex UH-1 Gen II nincs a boltban"),
    "vx-sparc": R(None, near=r"Vortex SPARC.*", why="a SPARC AR + magasító csomag nincs a boltban"),
    "vx-crossfire": R(None, near=r"Vortex Crossfire (II )?Red Dot.*",
                      why="a Crossfire Red Dot + magasító csomag nincs a boltban"),
    # ---- LPVO
    "vx-se16": R(None, near=r"Vortex Strike ?Eagle 1-6x24.*", why="a Strike Eagle 1-6x24 nincs a boltban"),
    "vx-se18": R(None, near=r"Vortex Strike ?Eagle 1-8x24.*",
                 why="a data.js SFP kivitele nincs a boltban; a 'StrikeEagle 1-8x24 EBR-8 MOA FFP' FFP kivitel "
                     "(másik típus)"),
    "vx-pst16": R(None, near=r"Vortex Viper PST.*", why="a Viper PST Gen II 1-6x24 nincs a boltban"),
    "vx-razor110": R(None, near=r"Vortex Razor HD.*", why="a Razor HD Gen III 1-10x24 nincs a boltban"),
    "tj-acog": R(None, near=r"Trijicon (ACOG|TA)\b.*", why="a Trijicon ACOG TA31 nincs a boltban"),
    "tj-credo16": R(None, near=r"Trijicon Credo.*", why="a Trijicon Credo 1-6x24 nincs a boltban"),
    "tj-vcog": R(None, near=r"Trijicon VCOG.*", why="a Trijicon VCOG nincs a boltban"),
    "eo-vudu16": R(None, near=r"EOTech Vudu.*", why="az EOTech Vudu 1-6x24 nincs a boltban"),
    "eo-vudu110": R(None, near=r"EOTech Vudu.*", why="az EOTech Vudu 1-10x28 nincs a boltban"),
    # ---- szerelék
    "gs-sp30": R(None, why=NOT_SOLD),
    "vx-cant30": R(r"Vortex (Montage )?Sport Cantilever 30mm 1\.5(\"|'')", near=r"Vortex .*Cantilever.*",
                   note="30 mm, 1.5\" magasság"),
    # ---- nagyítók
    "eo-g33": R(r"EOTech G33.*STS.*", near=r"EOTech .*G33.*",
                why="a G33 + STS szerelék önállóan nincs a boltban (csak HHS II csomagban EXPS2-2 irányzékkal)"),
    "eo-g43": R(None, near=r"EOTech G43.*", why="a G43 + Micro szerelék nincs a boltban"),
    "ap-3x": R(None, near=r"Aimpoint (3XMag|Booster).*",
               why="csomag (3XMag-1 + Flip szerelék): ilyen csomag nincs a boltban"),
    "hs-hm3x": R(r"Holosun HM3X", note="a HM3XT (titán) külön, drágább modell"),
    "vx-m3x": R(None, near=r"Vortex .*(Micro ?3X|V3XM).*", why="a Vortex Micro3X + Flip nincs a boltban"),
    # ---- BUIS
    "mp-mbus3": R(None, near=r"Magpul MBUS 3 (Front|Rear) .*", why=SET_BUIS),
    "mp-mbuspro": R(None, near=r"Magpul MBUS PRO (Front|Rear) .*", why=SET_BUIS),
    "mp-mbusoff": R(None, near=r"Magpul MBUS PRO Offset .*", why=SET_BUIS),
    # ---- lámpák
    "sf-m600": R(r"SureFire M600DF.*", near=r"SureFire M[36]\d\d.*Scout.*", why="az M600DF nincs a boltban"),
    "sf-m640": R(r"SureFire M640DF Scout Light Pro " + C, near=r"SureFire M640.*",
                 why="az M640DF nincs a boltban; az 'M640U Scout Light Pro' másik típus"),
    "sf-m300": R(r"SureFire M300.*", near=r"SureFire M3\d0.*",
                 why="az M300 Mini Scout Light Pro nincs a boltban; az 'M340C Mini Scout Light Pro' másik típus"),
    "sf-x300": R(r"SureFire X300U-B " + C, near=r"SureFire X300.*"),
    "sl-hlx": R(r"Streamlight ProTac Rail Mount HL-X.*", near=r"Streamlight Pro[Tt]ac Rail Mount.*",
                why="a ProTac Rail Mount HL-X nincs a boltban (csak 1 / 2 / 1L-X)"),
    "sl-tlr1": R(r"Streamlight TLR-1 HL " + C, near=r"Streamlight TLR-1.*"),
    # ---- M-LOK
    "mp-rvg": R(None, near=r"Magpul (RVG|MVG) .*",
                why="az 'M-LOK RVG' nincs a boltban: a bolti 'RVG Rail Vertical Grip' Picatinny-sínes, az 'MVG "
                    "M-Lok Vertical Grip' másik termék"),
    "mp-hstop": R(r"Magpul M-LOK Handstop Kit " + C, near=r"Magpul .*Handstop.*",
                  note="az 'XTM Handstop Kit' másik termék"),
    "bcm-vg3": R(None, why=NOT_SOLD), "bcm-kag": R(None, why=NOT_SOLD),
    "mp-rail5": R(r"Magpul M-LOK Aluminum Rail 5 Slots", near=r"Magpul M-LOK .*Rail \d Slots"),
    "mp-qd": R(r"Magpul QD Sling Mount M-LOK"),
    "mp-cover": R(r"Magpul M-LOK Rail Covers Type 2 (6er Pack )?" + C, near=r"Magpul M-LOK Rail Covers .*",
                  mpn=r"MAG60[23].*", note="a bolt 6 darabos csomagként árulja (a Magpul Type 2 kiszerelése)"),
    "mp-cable": R(None, why="a Magpul M-LOK Cable Management Kit nincs a boltban"),
    # ---- bipod
    "mp-bipod": R(r"Magpul Bipod für M-LOK " + C, near=r"Magpul Bipod .*"),
    "hr-sbrm": R(r"Harris S-BRM", near=r"Harris S-BR.*",
                 why="az S-BRM + M-LOK adapter nincs a boltban; az 'S-BR-MLOK', 'S-BRMP', 'S-BRP' más típus"),
    # ---- torkolat
    "sf-warcomp": R(r"SureFire WarComp.*", why="a SureFire WarComp nincs a boltban"),
    "sf-3p": R(r"SureFire SF3P.*", why="a SureFire SF3P nincs a boltban"),
    "bcm-comp0": R(None, why=NOT_SOLD),
    "sf-rc2": R(None, why="a SureFire SOCOM556-RC2 nincs a boltban"),
    # ---- elsütők
    "gs-ssae": R(r"Geissele SSA-E Two-Stage Trigger", near=r"Geissele .*Trigger.*"),
    "gs-ssa": R(r"Geissele SSA Two-Stage Trigger", near=r"Geissele .*Trigger.*", why="az SSA nincs a boltban"),
    "gs-sde": R(r"Geissele SD-E.*", near=r"Geissele .*Trigger.*", why="az SD-E nincs a boltban"),
    "gs-s3g": R(r"Geissele (Super 3 Gun|S3G).*", near=r"Geissele .*Trigger.*", why="az S3G nincs a boltban"),
    # ---- felhúzókar
    "rd-raptor": R(r"Radian Raptor (AR-?15 )?" + C, near=r"Radian Raptor.*",
                   why="a Raptor (AR-15) felhúzókar nincs a boltban"),
    "rd-raptorlt": R(r"Radian Raptor-LT( AR-?15)?( " + C + ")?", near=r"Radian Raptor.*",
                     why="az AR-15-ös Raptor-LT nincs a boltban; a 'Raptor-LT MPX' SIG MPX-hez való"),
    "rd-raptorsd": R(r"Radian Raptor-SD.*", near=r"Radian Raptor.*", why="a Raptor-SD nincs a boltban"),
    "bcm-ch4": R(None, why=NOT_SOLD), "bcm-ch3": R(None, why=NOT_SOLD),
    "gs-ach": R(r"Geissele Airborne.*", why="a Geissele Airborne Charging Handle nincs a boltban"),
    # ---- kezelőszervek
    "rd-talon": R(r"Radian Talon.*", why="a Radian Talon nincs a boltban"),
    "mp-bad": R(r"Magpul B\.?A\.?D\.? Lever.*", why="a Magpul B.A.D. Lever nincs a boltban"),
    "mp-trigguard": R(r"Magpul (MOE )?Enhanced Trigger Guard( Polymer)? " + C, mpn=r"MAG015.*",
                      near=r"Magpul .*Trigger Guard.*",
                      why="a 'MOE Enhanced Trigger Guard' (MAG015) nincs a boltban; a 'Enhanced Trigger Guard "
                          "Polymer' (MAG1186) / 'Alu' más cikkszám"),
    # ---- tárak
    "mp-pm10": R(r"Magpul PMAG 10 AR(15)?/M4( GEN M3)? " + C, mpn=r"MAG559.*", near=r"Magpul PMAG 10 .*",
                 why="a bolti 'PMAG 10 AR15/M4' nevében nincs 'Gen M3', és a cikkszám sem igazolja – nem "
                     "azonosítható biztosan"),
    "mp-pm20": R(r"Magpul PMAG 20 AR/M4 GEN M3 " + C),
    "mp-pm30": R(r"Magpul PMAG 30 AR/M4 GEN M3 " + C),
    "mp-pm30w": R(r"Magpul PMAG 30 AR/M4 GEN M3 (Fenster|Window) " + C),
    "mp-pm40": R(r"Magpul PMAG 40 AR/M4 GEN M3 " + C),
    "mp-d60": R(r"Magpul PMAG D-60.*", near=r"Magpul PMAG D-\d0 .*",
                why="a D-60 dobtár nincs a boltban (csak D-50 típusok, más fegyverhez)"),
    # ---- szíjak
    "mp-ms1": R(r"Magpul MS1 Sling Two Point " + C, near=r"Magpul MS1 .*",
                note="az 'MS1 Lite', 'MS1 Padded' és 'MS1 QDM' más termékek"),
    "mp-ms4": R(r"Magpul MS4 Dual QD Gen ?2 Sling " + C, note="a bolt csak a Gen 2 kivitelt árulja"),
    "bfg-vickers": R(r"Blue Force Gear Vickers.*", why=NOT_SOLD),
}


# --------------------------------------------------------------------------- keresés
def is_product_page(s_el):
    return bool(s_el.select_one(".article-detail, .article-infos") and s_el.select_one("h1")
                and not s_el.select_one("#result-list"))


def parse_price_html(txt):
    """Az ár-XHR 'text' mezője (HTML) -> (fizetendő ár, áthúzott ár, UVP)"""
    s_el = soup(txt)

    def val(sel):
        e = s_el.select_one(sel)
        return parse_chf(e.get_text(" ")) if e and norm(e.get_text(" ")) else None
    cust, basis, uvp = val(".price-cust .price"), val(".price-basis .price"), val(".price-uvp .price")
    price = cust if cust is not None else basis
    lst = basis if (basis is not None and price is not None and basis > price) else None
    return price, lst, uvp


def fetch_prices(http, s_el, page_url):
    """A lap összes '.article-price-wrapper' elemének ára a lap saját ár-lekérdezésével.
    -> {art-internalno: (ár, áthúzott ár, UVP)}"""
    pu = s_el.select_one("#article-price-url[data-op-url]")
    ids = [w["data-op-art-internalno"] for w in s_el.select(".article-price-wrapper[data-op-art-internalno]")
           if w.get("data-op-art-internalno")]
    out = {}
    if not pu or not ids:
        return out
    url = urllib.parse.urljoin(page_url, pu["data-op-url"])
    uniq = list(dict.fromkeys(ids))
    for i in range(0, len(uniq), 20):  # a bolt JS-e is 20-asával kérdez
        body = http.post_form(url, {"artIntNoPrice": ",".join(uniq[i:i + 20])})
        try:
            d = json.loads(body)
        except ValueError:
            raise RuntimeError("az ár-lekérdezés nem JSON-t adott (%s)" % page_url)
        for p in d.get("price") or []:
            out[p.get("artIntNo")] = parse_price_html(htmllib.unescape(p.get("text") or ""))
    return out


def search(http, q, max_pages=20):
    url = BASE + "/de/search/Section1.htm?" + urllib.parse.urlencode({"query": q})
    cards, seen = [], set()
    for _ in range(max_pages):
        body, final = http.get(url, cache=False)
        s = soup(body)
        if is_product_page(s):  # egyetlen találat -> a bolt a termékoldalra irányít
            h1 = s.select_one("h1")
            prices = fetch_prices(http, s, final)
            w = s.select_one(".article-infos .article-price-wrapper[data-op-art-internalno]") or \
                s.select_one(".article-price-wrapper[data-op-art-internalno]")
            pr = prices.get(w["data-op-art-internalno"]) if w else None
            cards.append(dict(name=norm(h1.get_text(" ")), url=final, price=pr[0] if pr else None))
            break
        lst = s.select_one("#result-list") or s
        page_cards = []
        for it in lst.select(".article-list-item"):
            a = it.select_one(".article-list-item-title a[href]")
            if not a:
                continue
            name = norm(a.get_text(" "))
            if "CatItemA" in name or not name:
                continue
            u = urllib.parse.urljoin(BASE, a["href"])
            if u in seen:
                continue
            seen.add(u)
            w = it.select_one(".article-price-wrapper[data-op-art-internalno]")
            page_cards.append(dict(name=name, url=u, price=None, _ino=w["data-op-art-internalno"] if w else None))
        if page_cards:
            prices = fetch_prices(http, lst, final)
            for c in page_cards:
                pr = prices.get(c.pop("_ino"))
                c["price"] = pr[0] if pr else None
            cards += page_cards
        nxt = s.select_one('.opc-segment-navigation[data-op-type="pagination"] [data-op-href]')
        href = nxt["data-op-href"] if nxt else ""
        if not page_cards or "pposCatItem=" not in href or href.lower().startswith("javascript"):
            break
        url = urllib.parse.urljoin(BASE, href)
    return cards


# --------------------------------------------------------------------------- termékoldal
def scrape_product(http, url):
    body, final = http.get(url, cache=False)
    s_el = soup(body)
    h1 = s_el.select_one("h1")
    title = norm(h1.get_text(" ")) if h1 else ""
    info = s_el.select_one(".article-infos") or s_el
    w = info.select_one(".article-price-wrapper[data-op-art-internalno]")
    prices = fetch_prices(http, s_el, final)
    price, list_price, uvp = prices.get(w["data-op-art-internalno"], (None, None, None)) if w else (None, None, None)
    spec = {}
    for li in s_el.select("li.artSpecAttr"):
        lab, val = li.find("label"), li.find("span")
        if lab and val:
            spec[norm(lab.get_text(" "))] = norm(val.get_text(" "))
    art = s_el.select_one(".article-art-nr")
    sku = norm(art.get_text(" ")).replace("Art.", "").strip() if art else None
    img = None
    for im in s_el.select("img"):
        src = im.get("src") or im.get("data-src") or ""
        if "/pictures/" in src and "_L_" in src:
            img = urllib.parse.urljoin(BASE, src)
            break
    st = info.select_one(".article-stock") or s_el.select_one(".article-stock")
    stock_text = norm(st.get("title") or st.get_text(" ")) if st else None
    in_stock = None
    if st:
        cls = " ".join(st.get("class") or [])
        if "on-stock" in cls or "less-stock" in cls or re.search(r"\ban Lager\b", stock_text or "", re.I):
            in_stock = not re.search(r"nicht|kein", stock_text or "", re.I)
        elif re.search(r"no-stock|out-stock|na-stock", cls):
            in_stock = False
    wg = None
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*(kg|g)\b", spec.get("Gewicht", ""), re.I)
    if m:
        v = float(m.group(1).replace(",", "."))
        wg = int(round(v * 1000 if m.group(2).lower() == "kg" else v))
    out = dict(title=title, url=final, price_chf=price, list_chf=list_price, image=img, sku=sku,
               mpn=spec.get("Modell") or spec.get("Bestellnummer") or spec.get("Herstellernummer"),
               weight_g=wg, dims=spec.get("Abmessungen"), in_stock=in_stock, stock_text=stock_text)
    if uvp:
        out["uvp_chf"] = uvp
    return out


def near_text(cards, near_re, limit=4):
    out = [c for c in cards if re.fullmatch(near_re, c["name"], re.I)][:limit]
    return " | ".join("%s (%s CHF, %s)" % (c["name"], ("%.2f" % c["price"]) if c["price"] else "?", c["url"])
                      for c in out)


# --------------------------------------------------------------------------- fő logika
def decide(http, pid, brand, name, palette, rule, cards):
    if rule is None:  # új, szabály nélküli id: csak teljes névegyezés
        key = re.sub(r"[^a-z0-9]+", "", (brand + name).lower())
        hits = [c for c in cards if re.sub(r"[^a-z0-9]+", "", c["name"].lower()) == key]
        rule = R(title=re.escape(brand + " " + name))
    else:
        hits = [] if rule["title"] is None else [c for c in cards if re.fullmatch(rule["title"], c["name"], re.I)]

    def nope(extra=None):
        reason = "ok: " + (extra or rule["why"] or "nincs a boltban (a keresés nem adott pontosan azonos terméket)")
        near = near_text(cards, rule["near"]) if rule["near"] else ""
        if near:
            reason += "; legközelebbi bolti termék(ek), NEM azonos: " + near
        return "no", reason

    if not hits:
        return nope()
    variants = []
    for c in hits:
        m = re.fullmatch(rule["title"], c["name"], re.I)
        word = (m.groupdict().get("color") if m else None) or None
        det = scrape_product(http, c["url"])
        if not re.fullmatch(rule["title"], det["title"], re.I):
            sys.stderr.write("   a termékoldal neve eltér (%s / %s) – kihagyva\n" % (c["name"], det["title"]))
            continue
        if rule["mpn"] and not re.fullmatch(rule["mpn"], det["mpn"] or "", re.I):
            sys.stderr.write("   cikkszám nem egyezik (%s: %s) – kihagyva\n" % (c["name"], det["mpn"]))
            continue
        if det["price_chf"] is None:
            continue
        key = color_key(word, palette) if word else (rule["default_color"] or ("black" if not palette else None))
        det["color_word"], det["color"] = word, key
        variants.append(det)
    if not variants:
        return nope(rule["why"] if rule["mpn"] else "a találat a termékoldal alapján nem azonos")
    if palette:
        variants = [v for v in variants if v["color"] in palette] or variants
    # fő ár: fekete (vagy az első palettaszín); azonos színből a legolcsóbb
    order = (palette or ["black"])
    prim = sorted(variants, key=lambda v: (order.index(v["color"]) if v["color"] in order else 99,
                                           v["price_chf"]))[0]
    item = {k: prim[k] for k in ("title", "price_chf", "list_chf", "url", "image", "in_stock", "stock_text",
                                 "sku", "mpn", "weight_g", "dims")}
    if prim["color_word"]:
        item["color"] = prim["color_word"]
    if palette:
        cp = {}
        for v in sorted(variants, key=lambda v: v["price_chf"]):
            if v["color"] in palette and v["color"] not in cp:
                cp[v["color"]] = dict(price_chf=v["price_chf"], list_chf=v["list_chf"], url=v["url"],
                                      in_stock=v["in_stock"], title=v["title"], sku=v["sku"], mpn=v["mpn"])
        if cp:
            item["colors_prices"] = cp
    others = [v for v in variants if v is not prim and v["color"] == prim["color"]]
    if others:
        item["alternatives"] = [dict(title=v["title"], url=v["url"], price_chf=v["price_chf"]) for v in others]
        item["match"] = "variant"
    else:
        item["match"] = "exact"
    if rule["note"]:
        item["note"] = rule["note"]
    return "ok", item


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/schildwaffen.json; --only mellett stdout)")
    ap.add_argument("--dump", action="store_true", help="csak a keresési találatok kiírása")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    http = Http(None)
    items, unmatched, blocked = {}, {}, None
    try:
        cards, seen = [], set()
        for q in QUERIES:
            got = search(http, q)
            for c in got:
                if c["url"] not in seen:
                    seen.add(c["url"])
                    cards.append(c)
            sys.stderr.write("keresés %-18s %3d kártya\n" % (q, len(got)))
        sys.stderr.write("%d bolti termékkártya a keresésekből\n" % len(cards))
        if args.dump:
            for c in sorted(cards, key=lambda c: c["name"].lower()):
                print("%-80s | %9s | %s" % (c["name"], c["price"], c["url"]))
            return
        for i, (pid, brand, name, palette) in enumerate(entries, 1):
            sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), pid, brand, name))
            try:
                kind, res = decide(http, pid, brand, name, palette, RULES.get(pid), cards)
            except Blocked:
                raise
            except Exception as e:
                unmatched[pid] = "ok: hiba a lekérdezéskor, nem sikerült ellenőrizni (%s)" % e
                sys.stderr.write("   HIBA: %s\n" % e)
                continue
            if kind == "ok":
                items[pid] = res
                sys.stderr.write("   -> %s  CHF %s\n" % (res["title"], res["price_chf"]))
            else:
                unmatched[pid] = res
    except Blocked as e:
        blocked = str(e)
        items = {}
        sys.stderr.write("BOTVÉDELEM / BLOKKOLÁS: %s – a gyűjtés leállt\n" % e)
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(), note=PRICE_NOTE,
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
