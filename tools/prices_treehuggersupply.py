#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
treehuggersupply.ch (Schärer Treehuggersupply, Rümlang ZH) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_treehuggersupply.py                    # -> assets/prices/treehuggersupply.json
    python3 tools/prices_treehuggersupply.py --only dd-v7,eo-g33  # részleges futás (stdout-ra ír)
    python3 tools/prices_treehuggersupply.py --cache /tmp/thcache # GET-válaszok gyorsítótára (fejlesztéshez)
    python3 tools/prices_treehuggersupply.py --dump               # csak a keresési találatok listája

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés (BigCommerce Stencil webshop)
  1. A js/data.js-ből kiolvassa a BASES és ITEMS azonosítókat (márka, név, színpaletta).
  2. A QUERIES keresőkifejezésekkel lekéri a bolt keresőjét (/search.php?section=product,
     lapozással), és a kártyákból (név, márka, URL) egy közös találati készletet épít.
  3. Minden id-hez a RULES táblában van a bolti terméknév PONTOS (fullmatch) mintája. Ha nincs
     egyező kártya, az id az 'unmatched'-be kerül okkal és a legközelebbi bolti termék(ek)kel.
  4. Egyezésnél a termékoldalt tölti le (JSON-LD + BCData). Ha a terméknek választható opciója
     van (pl. szín), a változat árát a termékoldal saját, csak olvasó változat-lekérdezésével kéri
     le (POST /remote/v1/product-attributes/<id> – ugyanaz, amit a böngésző az opció
     kiválasztásakor küld; kosárba nem tesz semmit). Árat soha nem becsül és nem számol.
  A bolt a termékoldalon a BigCommerce 'without_tax' ármezőt jeleníti meg (külön ÁFA-felirat
  nélkül); a JSON a kiírt árat rögzíti.

Udvariasság: egyetlen szálon, kérésenként legalább 1,2 s szünettel, böngésző User-Agent.
Botvédelem (403, Cloudflare/captcha oldal, tartós 429) esetén azonnal leáll, és a JSON-ba
'blocked' okot ír. Csak a treehuggersupply.ch-ra kérdez; a képeknek csak az URL-jét rögzíti.
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

SHOP = "treehuggersupply.ch"
BASE = "https://treehuggersupply.ch"
HOSTS = ("treehuggersupply.ch", "www.treehuggersupply.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "treehuggersupply.json")
PRICE_NOTE = ("A bolt termékoldalán megjelenített ár (BigCommerce 'without_tax' mező, külön ÁFA-felirat "
              "nélkül). in_stock csak akkor true/false, ha a bolt készletszámot közöl; a 'Lieferzeit' "
              "szöveg a stock_text-ben.")


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

    def _wait(self):
        w = MIN_GAP - (time.time() - self.last)
        if w > 0:
            time.sleep(w)

    def _req(self, url, data=None, headers=None):
        host = urllib.parse.urlparse(url).netloc
        if host not in HOSTS:
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir:
            key = url + ("\n" + data.decode() if data else "")
            ck = os.path.join(self.cache_dir, hashlib.sha1(key.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, encoding="utf-8") as f:
                    d = json.load(f)
                return d["body"], d["url"]
        hdr = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5", "Accept": "*/*"}
        hdr.update(headers or {})
        err = None
        n429 = 0
        for attempt in range(5):
            self._wait()
            self.last = time.time()
            self.n += 1
            try:
                req = urllib.request.Request(url, data=data, headers=hdr)
                with urllib.request.urlopen(req, timeout=60) as r:
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
                err = e
                time.sleep(4 * (attempt + 1))
        raise RuntimeError("letöltés sikertelen: %s (%s)" % (url, err))

    def get(self, url, params=None):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        return self._req(url)

    def post_form(self, url, fields):
        data = urllib.parse.urlencode(fields).encode()
        body, _ = self._req(url, data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest"})
        return body


def soup(markup):
    try:
        return BeautifulSoup(markup, "lxml")
    except Exception:
        return BeautifulSoup(markup, "html.parser")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


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
    starts = [m for m in re.finditer(r"\{\s*id:\s*'([^']+)',", src)]
    out = []
    for i, m in enumerate(starts):
        chunk = src[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(src)]
        chunk = chunk[: chunk.find("}") + 1 or len(chunk)]  # csak maga az objektum-literál
        bm =re.search(r"brand:\s*'((?:[^'\\]|\\.)*)'", chunk)
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


# --------------------------------------------------------------------------- szabályok
def R(title=None, brand=None, near=None, why=None, note=None, opts=None):
    """title: bolti terméknév (regex, fullmatch, kis/nagybetű-független) – None: a bolt biztosan
    nem árulja ugyanezt; brand: bolti márka (regex); near: regex a tájékoztató 'legközelebbi'
    termékekhez; why: indok az unmatched-hez; note: megjegyzés; opts: {opciócsoport-regex:
    érték-regex} – csak az ezeknek megfelelő változatok számítanak (pl. retikulum)."""
    return dict(title=title, brand=brand, near=near, why=why, note=note, opts=opts or {})


NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a márkát/terméket)"
SET_BUIS = "szett (első + hátsó irányzék): a bolt nem árul ilyen szettet"

QUERIES = ["Daniel Defense", "DDM4", "Schmeisser", "BCM", "Magpul", "PMAG", "B5 Systems", "Geissele", "Radian",
           "Aimpoint", "Micro T-2", "Comp M5", "Aimpoint PRO", "Acro", "3XMag", "EOTech", "EXPS", "XPS2", "Vudu",
           "G33", "G43", "Holosun", "Trijicon", "ACOG", "MRO", "Credo", "VCOG", "Vortex", "Surefire", "M600",
           "M640", "M300", "X300", "WarComp", "SOCOM", "Streamlight", "TLR", "ProTac", "Harris", "Bipod",
           "Blue Force", "Vickers", "Sling", "Grip", "Stock", "Charging Handle", "Trigger"]

RULES = {
    # ---- alapfegyverek
    "schm-s4f": R(None, why=NOT_SOLD), "schm-m4fl": R(None, why=NOT_SOLD), "schm-m5fl": R(None, why=NOT_SOLD),
    "schm-dyn": R(None, why=NOT_SOLD),
    "dd-v7": R(r"DDM4 V7", r"Daniel Defense", note="16\" 5.56 NATO (a termékleírás szerint); a bolt a színt "
               "opcióként árulja (Schwarz/Braun/FDE), a fekete változat ára"),
    "bcm-recce16": R(None, why=NOT_SOLD),
    # ---- markolat, tus, tustalp (a bolt nem árul Magpul/BCM/B5 bútorzatot)
    **{k: R(None, why=NOT_SOLD) for k in (
        "mp-moe", "mp-moek2", "mp-moek2p", "mp-miad", "bcm-g0", "bcm-g3", "b5-t22", "b5-t23",
        "mp-ctr", "mp-moesl", "mp-moeslk", "mp-ubr2", "mp-prs3", "mp-prslite", "bcm-s0", "bcm-s1", "b5-bravo",
        "b5-esop", "b5-prec", "mp-pad030", "mp-pad055", "mp-pad070", "mp-padsl")},
    # ---- red dot / holo
    "ap-t2": R(None, near=r"Aimpoint Micro T-2.*",
               why="csomag (T-2 + LRP szerelék): a bolt 'Aimpoint Micro T-2' terméke 'integrierte Montage'-t "
                   "említ, az LRP szerelék nincs megnevezve – nem azonosítható biztosan"),
    "ap-m5": R(None, near=r"Aimpoint Comp ?M5.*",
               why="csomag (CompM5 + LRP szerelék): a bolti 'Aimpoint Comp M5' leírása nem nevezi meg az LRP "
                   "szereléket – nem azonosítható biztosan"),
    "ap-pro": R(None, near=r"Aimpoint PRO.*",
                why="csomag (PRO + QRP2 szerelék): a bolti termék leírása nem nevezi meg a QRP2 szereléket – "
                    "nem azonosítható biztosan"),
    "ap-acro": R(None, near=r"Aimpoint.? Acro.? P-2",
                 why="csomag (ACRO P-2 + Picatinny talp): a bolt az ACRO P-2-t talp nélkül árulja "
                     "('Gewicht 61 g nur Visier')"),
    "eo-exps3": R(r"EOTECH HWS EXPS3-0", r"Eotech"),
    "eo-exps2": R(r"EOTECH HWS EXPS2-0", r"Eotech"),
    "eo-xps2": R(r"EOTECH HWS XPS2-0", r"Eotech"),
    "hs-510c": R(None, why=NOT_SOLD), "hs-aems": R(None, why=NOT_SOLD), "hs-403b": R(None, why=NOT_SOLD),
    "tj-mro": R(None, near=r"Trijicon (RMR|SRO|RCR).*", why="a Trijicon MRO nincs a boltban"),
    "vx-uh1": R(None, why=NOT_SOLD), "vx-sparc": R(None, why=NOT_SOLD), "vx-crossfire": R(None, why=NOT_SOLD),
    # ---- LPVO
    "vx-se16": R(None, why=NOT_SOLD), "vx-se18": R(None, why=NOT_SOLD), "vx-pst16": R(None, why=NOT_SOLD),
    "vx-razor110": R(None, why=NOT_SOLD),
    "tj-acog": R(None, near=r"Trijicon ACOG.*",
                 why="a TA31 (4x32) nincs a boltban; a 'ACOG 3.5×35' (TA11) másik típus"),
    "tj-credo16": R(None, why="a Trijicon Credo nincs a boltban"),
    "tj-vcog": R(None, why="a Trijicon VCOG nincs a boltban"),
    "eo-vudu16": R(r"EOTECH Vudu 1-6x24 FFP", r"Eotech", note="FFP kivitel"),
    "eo-vudu110": R(r"EOTECH Vudu 1-10x28 FFP", r"Eotech"),
    # ---- szerelék
    "gs-sp30": R(None, why=NOT_SOLD), "vx-cant30": R(None, why=NOT_SOLD),
    # ---- nagyító
    "eo-g33": R(r"EOTECH G33\.STS", r"Eotech", note="G33 nagyító STS szerelékkel"),
    "eo-g43": R(None, near=r"EOTECH G43.*",
                why="G43 + 'Micro' szerelék csomag: a bolt csak a G43.STS (STS szerelékkel) változatot árulja"),
    "ap-3x": R(None, why="az Aimpoint 3XMag-1 + Flip szerelék nincs a boltban"),
    "hs-hm3x": R(None, why=NOT_SOLD), "vx-m3x": R(None, why=NOT_SOLD),
    # ---- BUIS
    "mp-mbus3": R(None, why=SET_BUIS), "mp-mbuspro": R(None, why=SET_BUIS), "mp-mbusoff": R(None, why=SET_BUIS),
    # ---- lámpák
    "sf-m600": R(None, why="a SureFire M600DF nincs a boltban"),
    "sf-m640": R(None, near=r"Surefire M640.*",
                 why="az M640DF nincs a boltban; a 'M640 Turbo Dual Fuel' (M640DFT) másik típus"),
    "sf-m300": R(None, why="a SureFire M300 nincs a boltban"),
    "sf-x300": R(None, near=r"Surefire X300.*", why="az X300U-B nincs a boltban; az 'X300T' (Turbo) másik típus"),
    "sl-hlx": R(None, why="a Streamlight ProTac Rail Mount HL-X nincs a boltban"),
    "sl-tlr1": R(None, near=r".*Streamlight TLR.*", why="a TLR-1 HL nincs a boltban (csak TLR-2 / TL-Racker)"),
    # ---- M-LOK, bipod, torkolat, elsütő, felhúzókar, kezelőszervek
    **{k: R(None, why=NOT_SOLD) for k in (
        "mp-rvg", "mp-hstop", "bcm-vg3", "bcm-kag", "mp-rail5", "mp-qd", "mp-cover", "mp-cable",
        "mp-bipod", "hr-sbrm", "bcm-comp0", "gs-ssae", "gs-ssa", "gs-sde", "gs-s3g",
        "rd-raptor", "rd-raptorlt", "rd-raptorsd", "bcm-ch4", "bcm-ch3", "gs-ach",
        "rd-talon", "mp-bad", "mp-trigguard")},
    "sf-warcomp": R(None, why="a SureFire WarComp nincs a boltban"),
    "sf-3p": R(None, why="a SureFire SF3P nincs a boltban"),
    "sf-rc2": R(None, why="a SureFire SOCOM556-RC2 nincs a boltban"),
    # ---- tárak
    "mp-pm10": R(None, why="a PMAG 10 AR/M4 Gen M3 nincs a boltban"),
    "mp-pm20": R(None, near=r"PMAG 20 .*",
                 why="a PMAG 20 AR/M4 Gen M3 nincs a boltban (csak 'PMAG 20 LR/SR' 3-as csomag, más tár)"),
    "mp-pm30": R(None, near=r"PMAG 30 .*", why="a sima PMAG 30 AR/M4 Gen M3 nincs a boltban"),
    "mp-pm30w": R(None, near=r"PMAG 30 AR/M4 Gen M3 Window.*",
                  why="kiszerelés: a bolt csak 3 darabos csomagot árul ('3er Set'), egy darabot nem"),
    "mp-pm40": R(None, why="a PMAG 40 nincs a boltban"),
    "mp-d60": R(None, why="a PMAG D-60 nincs a boltban"),
    # ---- szíj
    "mp-ms1": R(None, why=NOT_SOLD), "mp-ms4": R(None, why=NOT_SOLD), "bfg-vickers": R(None, why=NOT_SOLD),
}

# bolti színnév -> data.js palettakulcs
COLOR_WORDS = [
    (r"schwarz|black|blk|noir", "black"),
    (r"fde|flat dark earth|dark earth", "fde"),
    (r"odg|od green|olive drab|oliv(e)?( ?gr(ü|ue)n)?|od", "odg"),
    (r"stealth gr[ae]y|gr[ae]y|grau", "gray"),
    (r"wolf gr[ae]y", "wolfgray"),
    (r"foliage( green)?", "foliage"),
    (r"ranger green", "ranger"),
    (r"coyote( brown| tan)?", "coyote"),
]


def color_key(label):
    lab = norm(label).lower()
    for pat, key in COLOR_WORDS:
        if re.fullmatch(pat, lab):
            return key
    return None


# --------------------------------------------------------------------------- keresés
def search(http, q, max_pages=8):
    cards, seen, url = [], set(), BASE + "/search.php?" + urllib.parse.urlencode(
        {"section": "product", "search_query": q})
    for _ in range(max_pages):
        body, _ = http.get(url)
        s = soup(body)
        new = 0
        for c in s.select("article.card"):
            a = c.select_one(".card-title a")
            if not a or not a.get("href") or a["href"] in seen:
                continue
            seen.add(a["href"])
            new += 1
            br = c.select_one(".card-text--brand, [data-test-info-type=brandName]")
            cards.append(dict(name=norm(a.get_text(" ")), brand=norm(br.get_text(" ")) if br else "",
                              url=urllib.parse.urljoin(BASE, a["href"])))
        nxt = s.select_one(".pagination-item--next a[href]")
        if not new or not nxt:
            break
        url = urllib.parse.urljoin(BASE, nxt["href"])
    return cards


# --------------------------------------------------------------------------- termékoldal
def bcdata(body):
    m = re.search(r"var BCData = (\{.*?\});\s*</script>", body, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1)).get("product_attributes") or {}
    except Exception:
        return {}


def ld_product(s_el):
    for t in s_el.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except Exception:
            continue
        for x in (d if isinstance(d, list) else [d]):
            if isinstance(x, dict) and x.get("@type") == "Product":
                return x
    return None


def price_of(attrs):
    """BCData/variant 'price' -> (ár, áthúzott ár) a bolt által kiírt (with_tax, ha van, különben
    without_tax) mezőből."""
    p = attrs.get("price") or {}
    for kind in ("with_tax", "without_tax"):
        if isinstance(p.get(kind), dict) and p[kind].get("value") is not None:
            lst = None
            for lk in ("non_sale_price_" + kind, "rrp_" + kind):
                if isinstance(p.get(lk), dict) and p[lk].get("value"):
                    lst = float(p[lk]["value"])
                    break
            val = float(p[kind]["value"])
            if lst is not None and lst <= val:
                lst = None
            return val, lst
    return None, None


def stock_of(attrs):
    st = attrs.get("stock")
    if isinstance(st, int):
        return st > 0
    if attrs.get("instock") is False or attrs.get("purchasable") is False:
        return False
    return None


def kg_g(w):
    if isinstance(w, dict) and w.get("value"):
        unit = (w.get("formatted") or "").lower()
        v = float(w["value"])
        if "kg" in unit:
            return int(round(v * 1000))
        if "g" in unit:
            return int(round(v))
    return None


def options(s_el):
    """-> (product_id, [(mező-név, csoport-címke, [(érték, címke)])])"""
    form = s_el.select_one("form[data-cart-item-add]")
    if not form:
        return None, []
    pid = (form.select_one("input[name=product_id]") or {}).get("value")
    groups = []
    for g in form.select("[data-product-attribute]"):
        lab = g.select_one("label.form-label, .form-label")
        glabel = norm(lab.get_text(" ")).replace("(Erforderlich)", "").replace("(Required)", "").strip(" :") \
            if lab else ""
        name, vals = None, []
        sel = g.select_one("select[name^=attribute]")
        if sel:
            name = sel["name"]
            vals = [(o["value"], norm(o.get_text(" "))) for o in sel.select("option") if o.get("value")]
        else:
            for inp in g.select("input[name^=attribute]"):
                name = inp["name"]
                lb = g.find("label", attrs={"for": inp.get("id")})
                vals.append((inp.get("value"), norm((lb.get("title") or lb.get_text(" ")) if lb else "")))
        if name and vals:
            groups.append((name, glabel, vals))
    return pid, groups


def availability_text(s_el):
    out = []
    for dt in s_el.select(".productView-info-name"):
        if re.search(r"Lieferzeit|Verfügbarkeit|Availability|Disponibilit", dt.get_text()):
            dd = dt.find_next_sibling(class_="productView-info-value")
            if dd and norm(dd.get_text(" ")):
                out.append(norm(dd.get_text(" ")))
    return " ".join(out)


def scrape_product(http, url, opt_filter):
    body, final = http.get(url)
    s_el = soup(body)
    ld = ld_product(s_el) or {}
    attrs = bcdata(body)
    h1 = s_el.select_one("h1.productView-title, h1")
    title = norm(h1.get_text(" ")) if h1 else norm(ld.get("name"))
    img = ld.get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    og = s_el.find("meta", property="og:image")
    img = img or (og.get("content") if og else None)
    stock_text = availability_text(s_el)
    av = ((ld.get("offers") or {}) if isinstance(ld.get("offers"), dict) else {}).get("availability", "")
    base = dict(title=title, url=final, image=img, sku=attrs.get("sku") or ld.get("sku"),
                mpn=attrs.get("mpn") or ld.get("mpn"), weight_g=kg_g(attrs.get("weight")),
                stock_text=stock_text, availability=av.rsplit("/", 1)[-1] if av else None)
    pid, groups = options(s_el)
    variants = []
    if groups and pid:
        # opciócsoport-szűrő (pl. retikulum) a szabályból
        fgroups = []
        for name, glabel, vals in groups:
            for gre, vre in opt_filter.items():
                if re.search(gre, glabel, re.I):
                    vals = [v for v in vals if re.fullmatch(vre, v[1], re.I)]
            fgroups.append((name, glabel, vals))
        for combo in list(itertools.product(*[g[2] for g in fgroups]))[:16]:
            fields = {"action": "add", "product_id": pid, "qty[]": "1"}
            labels = {}
            for (name, glabel, _), (val, vlab) in zip(fgroups, combo):
                fields[name] = val
                labels[glabel] = vlab
            try:
                d = json.loads(http.post_form(BASE + "/remote/v1/product-attributes/%s" % pid, fields)).get("data") or {}
            except Blocked:
                raise
            except Exception as e:
                sys.stderr.write("   változat kihagyva (%s): %s\n" % (labels, e))
                continue
            pr, lp = price_of(d)
            if pr is None or d.get("purchasable") is False and d.get("instock") is False and pr == 0:
                continue
            color = next((v for k, v in labels.items() if re.search(r"farbe|colou?r|couleur", k, re.I)), None)
            variants.append(dict(price_chf=pr, list_chf=lp, in_stock=stock_of(d), sku=d.get("sku"),
                                 color=color, other={k: v for k, v in labels.items() if v != color},
                                 image=(d.get("image") or {}).get("data", "").replace("{:size}", "1280x1280")
                                 if isinstance(d.get("image"), dict) else None))
    if not variants:
        pr, lp = price_of(attrs)
        if pr is None and isinstance(ld.get("offers"), dict) and ld["offers"].get("price"):
            pr = float(ld["offers"]["price"])
        variants = [dict(price_chf=pr, list_chf=lp, in_stock=stock_of(attrs), sku=base["sku"], color=None,
                         other={}, image=None)]
    return base, variants, groups


def build_item(card, base, variants, palette, note):
    def is_black(v):
        return v["color"] and color_key(v["color"]) == "black"
    pool = [v for v in variants if is_black(v)] or [
        v for v in variants if v["color"] and color_key(v["color"]) in palette] or variants
    pool = [v for v in pool if v["price_chf"] is not None] or pool
    prim = min(pool, key=lambda v: v["price_chf"] if v["price_chf"] is not None else 1e12)
    item = dict(title=base["title"], price_chf=prim["price_chf"], list_chf=prim["list_chf"], url=base["url"],
                image=prim["image"] or base["image"], in_stock=prim["in_stock"], stock_text=base["stock_text"],
                sku=prim["sku"], mpn=base["mpn"], weight_g=base["weight_g"], dims=None)
    if base.get("availability"):
        item["availability"] = base["availability"]
    if prim["color"]:
        item["color"] = prim["color"]
    if prim["other"]:
        item["variant"] = prim["other"]
    if palette:
        cp = {}
        for v in sorted(variants, key=lambda v: (v["price_chf"] is None, v["price_chf"] or 0)):
            k = color_key(v["color"]) if v["color"] else None
            if k in palette and k not in cp and v["price_chf"] is not None:
                cp[k] = dict(price_chf=v["price_chf"], list_chf=v["list_chf"], url=base["url"],
                             in_stock=v["in_stock"], title="%s – %s" % (base["title"], v["color"]))
        if cp:
            item["colors_prices"] = cp
    others = sorted({(v["price_chf"], " / ".join(v["other"].values())) for v in variants
                     if v["other"] and v is not prim and v["price_chf"] is not None and
                     (not v["color"] or v["color"] == prim["color"])})
    if others:
        item["alternatives"] = [dict(variant=o, price_chf=p) for p, o in others]
        item["match"] = "variant"
    else:
        item["match"] = "exact"
    if note:
        item["note"] = note
    return item


def near_info(http, cards, near_re, brand_re, limit=4):
    out = []
    for c in cards:
        if len(out) >= limit:
            break
        if not re.fullmatch(near_re, c["name"], re.I):
            continue
        if brand_re and not re.fullmatch(brand_re, c["brand"], re.I):
            continue
        try:
            body, _ = http.get(c["url"])
            attrs = bcdata(body)
            pr, _ = price_of(attrs)
            _, groups = options(soup(body))
            ptxt = ("ab " if groups else "") + ("%.2f" % pr if pr is not None else "?")
        except Blocked:
            raise
        except Exception:
            ptxt = "?"
        out.append("%s (%s CHF, %s)" % (c["name"], ptxt, c["url"]))
    return out


# --------------------------------------------------------------------------- fő logika
def decide(http, pid, brand, name, palette, rule, cards):
    if rule is None:  # új, szabály nélküli id: csak teljes névegyezés
        key = re.sub(r"[^a-z0-9]+", "", (brand + name).lower())
        hits = [c for c in cards if re.sub(r"[^a-z0-9]+", "", (c["brand"] + c["name"]).lower()) == key]
        rule = R()
    else:
        hits = [] if rule["title"] is None else [
            c for c in cards if re.fullmatch(rule["title"], c["name"], re.I)
            and (rule["brand"] is None or re.fullmatch(rule["brand"], c["brand"], re.I))]
    if not hits:
        reason = "ok: " + (rule["why"] or "nincs a boltban (a keresés nem adott pontosan azonos terméket)")
        if rule["near"]:
            near = near_info(http, cards, rule["near"], None)
            if near:
                reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(near)
        return "no", reason
    if len(hits) > 1:
        sys.stderr.write("   több találat: %s\n" % [h["name"] for h in hits])
    card = hits[0]
    base, variants, _ = scrape_product(http, card["url"], rule["opts"])
    if not re.fullmatch(rule["title"] or re.escape(card["name"]), base["title"], re.I):
        return "no", "ok: bizonytalan egyezés: a találati kártya (%s) és a termékoldal neve (%s) eltér" % (
            card["name"], base["title"])
    if all(v["price_chf"] is None for v in variants):
        return "no", "ok: a termékoldalon nincs kiírt ár (%s)" % card["url"]
    return "ok", build_item(card, base, variants, palette, rule["note"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/treehuggersupply.json; --only mellett stdout)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    ap.add_argument("--dump", action="store_true", help="csak a keresési találatok kiírása")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    http = Http(args.cache)
    items, unmatched, blocked = {}, {}, None
    try:
        cards, seen = [], set()
        for q in QUERIES:
            for c in search(http, q):
                if c["url"] not in seen:
                    seen.add(c["url"])
                    cards.append(c)
        sys.stderr.write("%d bolti termékkártya a keresésekből\n" % len(cards))
        if args.dump:
            for c in cards:
                print("%-20s | %s | %s" % (c["brand"], c["name"], c["url"]))
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
