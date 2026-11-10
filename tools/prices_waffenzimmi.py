#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
waffenzimmi.ch (Kurt Zimmermann Waffen AG, Höri ZH) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_waffenzimmi.py                       # -> assets/prices/waffenzimmi.json
    python3 tools/prices_waffenzimmi.py --only mp-pm10,eo-g33 # csak néhány id (stdout)
    python3 tools/prices_waffenzimmi.py --cache /tmp/wzcache  # HTTP-válaszok gyorsítótára

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  1. A bolt WooCommerce-alapú: a nyilvános Store API-ból (/wp-json/wc/store/v1/products,
     100 termék/kérés, ~45 kérés) letölti a teljes katalógust (név, ár, akciós ár, készlet,
     cikkszám, kép-URL). Változatos terméknél a változatokat külön lekéri.
  2. Minden data.js id-hoz a RULES táblában a bolti név PONTOS (fullmatch) mintája áll
     (normalizált, kisbetűs névre). Szabály nélküli új id-nál csak a "márka + név" teljes
     egyezése számít. A színt a névből olvassa ki (data.js palettakulcsokra fordítva).
  3. Ha nincs pontos egyezés: "unmatched" okkal + a legközelebbi bolti termékek (NEM azonos).
     Árat soha nem becsül, nem számol, nem vált át; a 0 / hiányzó ár = nincs kiírt ár.

Udvariasság: egy szálon, kérések között >= 1,2 s, böngésző User-Agent; 403 / captcha /
tartós 429 esetén azonnal leáll ("blocked"), nem próbálja megkerülni.
"""
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

# --------------------------------------------------------------------------- KÖZÖS RÉSZ (core)
# (A boltonkénti szkriptekben szó szerint ugyanez a blokk szerepel, hogy mindegyik önállóan futtatható legyen.)
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2  # másodperc két kérés között (boltonként egyszerre egy kérés)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")

BOT_MARKERS = ("cf-chl", "challenge-platform", "just a moment", "verifying you are human",
               "verifying connection", "g-recaptcha", "h-captcha", "hcaptcha", "captcha-delivery",
               "attention required! | cloudflare")


class Blocked(Exception):
    """Botvédelem / tiltás: a boltot azonnal abba kell hagyni (nem kerüljük meg)."""


class Http:
    def __init__(self, hosts, cache_dir=None):
        self.hosts = set(hosts)
        self.cache_dir = cache_dir
        self.last = 0.0
        self.n = 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def get(self, url, accept=None, ajax=False):
        host = urllib.parse.urlparse(url).netloc
        if host not in self.hosts:
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, "rb") as f:
                    return f.read().decode("utf-8", "replace")
        headers = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5",
                   "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
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
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = r.read().decode("utf-8", "replace")
                head = body[:4000].lower()
                if any(m in head for m in BOT_MARKERS):
                    raise Blocked("botvédelmi / captcha oldal: %s" % url)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise Blocked("HTTP 403 (tiltás / botvédelem): %s" % url)
                if e.code == 429:
                    if attempt >= 2:
                        raise Blocked("tartós HTTP 429 (túl sok kérés): %s" % url)
                    time.sleep(20 * (attempt + 1))
                    continue
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


def clean(s):
    s = htmllib.unescape(s or "").replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", s).strip()


def norm(s):
    """Összehasonlításhoz: kisbetű, egységes idézőjel/kötőjel/szorzójel, egyszeres szóköz."""
    s = htmllib.unescape(s or "")
    for a, b in (("″", '"'), ("′′", '"'), ("“", '"'), ("”", '"'), ("„", '"'), ("′", "'"), ("’", "'"),
                 ("‘", "'"), ("×", "x"), ("–", "-"), ("—", "-"), ("‐", "-"), ("®", ""), ("™", "")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKC", clean(s)).lower()
    for a, b in (("′′", '"'), ("′", "'")):  # NFKC a ″-t két ′-re bontja
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


# ---- data.js
def read_data_js():
    """-> [{id, brand, name, cat, colors(list|None), kind('base'|'item')}] (a 'factory'/'none' elemek nélkül)."""
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    consts = {}
    for m in re.finditer(r"const\s+([A-Z0-9_]+)\s*=\s*(\[[^\]]*\])\s*;", src):
        try:
            consts[m.group(1)] = json.loads(m.group(2).replace("'", '"'))
        except ValueError:
            pass
    out = []
    bm = re.search(r"const BASES = \[(.*?)\n  \];", src, re.S)
    for seg in re.split(r"\n\s*\{\s*\n", bm.group(1))[1:] if bm else []:
        m = re.search(r"id:\s*'([^']+)',\s*brand:\s*'([^']*)',\s*name:\s*'([^']*)'", seg)
        if not m:
            continue
        f = re.search(r"finishes:\s*(\[[^\]]*\])", seg)
        out.append(dict(id=m.group(1), brand=m.group(2), name=m.group(3), cat="base",
                        colors=json.loads(f.group(1).replace("'", '"')) if f else None, kind="base"))
    im = re.search(r"const ITEMS = \[(.*?)\n  \];", src, re.S)
    for line in (im.group(1) if im else "").splitlines():
        if not re.match(r"\s*\{\s*id:\s*'", line):
            continue
        g = lambda k: re.search(k + r":\s*'((?:[^'\\]|\\.)*)'", line)
        pid, cat, brand, name = g("id"), g("cat"), g("brand"), g("name")
        if not (pid and brand and name):
            continue
        cm = re.search(r"colors:\s*(\[[^\]]*\]|[A-Z0-9_]+)", line)
        colors = None
        if cm:
            v = cm.group(1)
            colors = json.loads(v.replace("'", '"')) if v.startswith("[") else consts.get(v)
        out.append(dict(id=pid.group(1), brand=brand.group(1).replace("\\'", "'"),
                        name=name.group(1).replace("\\'", "'"), cat=cat.group(1) if cat else "",
                        colors=colors, kind="item"))
    return out


# ---- színek (a data.js COLORS / FINISHES kulcsai)
ITEM_COLOR_KEYS = ("black", "fde", "odg", "gray", "wolfgray", "foliage", "ranger", "coyote")
BASE_COLOR_KEYS = ("black", "olive", "earth", "bronze", "fde")
COLOR_WORDS = [  # sorrend számít: a konkrétabb előbb; None = ismert, de a palettán nincs
    (r"modified coyote brown|\bmcb\b", None),
    (r"wolf ?gr[ae]y", "wolfgray"),
    (r"foliage", "foliage"),
    (r"ranger green|\brgr\b", "ranger"),
    (r"flat dark earth|dark earth|\bfde\b", "fde"),
    (r"olive drab|o\.? ?d\.? green|\bodg\b|\boliv(e|grün)?\b|\bod\b", "odg"),
    (r"stealth gr[ae]y|\bgr[ae]y\b|\bgrau\b|\bgry\b", "gray"),
    (r"\bcoyote\b|\bmct\b", "coyote"),
    (r"\bbronze\b", "bronze"),
    (r"black|schwarz|\bblk\b|\bbk\b", "black"),
    (r"\btan\b|\bsand\b|\bpink\b|\bred\b|\bblue\b|\bwhite\b|\bweiss\b|\bplum\b|\bzombie\b|\bcamo\b", None),
]


def color_key(text, item):
    """Bolti színnév -> palettakulcs (None: nem ismerhető fel / nincs a palettán)."""
    t = norm(text)
    for pat, key in COLOR_WORDS:
        if re.search(pat, t):
            if key and item["kind"] == "base":  # alapfegyver: data.js FINISHES
                key = {"odg": "olive", "fde": ("fde" if "fde" in (item["colors"] or []) else "earth")}.get(key, key)
                return key if key in BASE_COLOR_KEYS else None
            return key if key in ITEM_COLOR_KEYS else None
    return None


# ---- szabályok
def R(q, pat=None, why=None, near=None, note=None, excl=None, color_from=None):
    """q: keresőkifejezés(ek) (keresős boltnál);
    pat: a NORMALIZÁLT bolti terméknév teljes (fullmatch) mintája; a (?P<c>...) csoport a szín.
         None = a bolt biztosan nem árulja ugyanezt a terméket (csomag, más kivitel, nincs a kínálatban);
    why: indok az unmatched-hez; near: regex (search) a tájékoztató 'legközelebbi' termékekhez;
    note: megjegyzés a felvett tételhez; excl: kizáró regex (search);
    color_from: 'title' (alap) | 'none' (a szín nem a névből jön; nincs colors_prices)."""
    return dict(q=[q] if isinstance(q, str) else list(q or []), pat=pat, why=why, near=near, note=note,
                excl=excl, color_from=color_from or "title")


NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a márkát/terméket)"
NOT_FOUND = "nincs a boltban (a keresés nem adott pontosan azonos terméket)"
FIREARM_NONE = "alapfegyver: ez a modell nincs a bolt kínálatában"
SET_BUIS = "szett (első + hátsó irányzék): a bolt nem árul szettet (az árat nem adjuk össze)"
PKG = "csomag (optika + szerelék/magasító): a bolt ezt az összeállítást nem árulja egy termékként"


# színszavak a mintákhoz (a (?P<c>...) csoport); a color_key() fordítja palettakulcsra
CW = (r"(?P<c>black|schwarz|blk|fde|flat dark earth|dark earth|olive drab|olive|oliv|od green|o\.d\. green|odg|"
      r"stealth gr[ae]y|gr[ae]y|grau|medium coyote tan|modified coyote brown|coyote brown|coyote|tan|bronze|"
      r"ranger green|foliage green|foliage|wolf gr[ae]y|sand|red|pink|zombie green|plum|white)")


def near_text(cands, near_re, limit=4):
    if not near_re:
        return ""
    seen, near = set(), []
    for c in cands:
        if c["url"] in seen or not re.search(near_re, c["n"]):
            continue
        seen.add(c["url"])
        near.append(c)
    if not near:
        return ""
    return "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(
        "%s (%s, %s)" % (c["title"], ("%.2f CHF" % c["price"]) if c.get("price") else "ár nélkül", c["url"])
        for c in near[:limit])


def pick_main(by_color, item):
    if not by_color:
        return None
    if "black" in by_color:
        return "black"
    for k in item["colors"] or []:
        if k in by_color:
            return k
    return next(iter(by_color))


def build(item, rule, hits):
    """hits: [cand] (mind árral) -> item dict a brief szerinti mezőkkel."""
    by_color, alts, other = {}, [], []
    for c in sorted(hits, key=lambda c: (c["price"], c["title"])):
        k = c.get("ckey")
        if k is None and c.get("ctext"):
            other.append(c)  # felismert, de a palettán nem szereplő szín (pl. MCB)
            continue
        k = k or "_"
        if k in by_color:
            alts.append(c)
        else:
            by_color[k] = c
    if not by_color:
        return None
    colored = {k: v for k, v in by_color.items() if k != "_"}
    main_key = pick_main(colored, item) if colored else "_"
    m = by_color[main_key]
    out = dict(title=m["title"], price_chf=m["price"], list_chf=m.get("list"), url=m["url"],
               image=m.get("image"), in_stock=m.get("in_stock"), stock_text=m.get("stock_text"),
               sku=m.get("sku"), mpn=m.get("mpn"), weight_g=m.get("weight_g"), dims=m.get("dims"))
    if colored:
        order = list(item["colors"] or []) + [k for k in ITEM_COLOR_KEYS + BASE_COLOR_KEYS
                                              if k not in (item["colors"] or [])]
        out["color"] = main_key
        out["colors_prices"] = {k: dict(price_chf=colored[k]["price"], list_chf=colored[k].get("list"),
                                        url=colored[k]["url"], in_stock=colored[k].get("in_stock"),
                                        title=colored[k]["title"])
                                for k in sorted(colored, key=order.index)}
    if "_" in by_color and colored:
        alts.append(by_color["_"])
    if alts:
        out["alternatives"] = [dict(title=c["title"], price_chf=c["price"], url=c["url"]) for c in alts]
    if other:
        out["other_colors"] = [dict(title=c["title"], price_chf=c["price"], url=c["url"]) for c in other]
    notes = [rule["note"]] if rule.get("note") else []
    if main_key == "_" and item["colors"] and rule.get("color_from") != "none":
        notes.append("a szín a bolti terméknévben nincs megadva")
    elif colored and main_key != "black":
        notes.append("feketében nem kapható ebben a boltban; a fő ár a(z) '%s' színé" % main_key)
    if notes:
        out["note"] = "; ".join(notes)
    out["match"] = "variant" if any((a.get("ckey") or "_") == main_key for a in alts) else "exact"
    return out


def decide(item, rule, cands):
    """cands: a bolt (keresési / katalógus) termékei normalizált névvel ('n') -> ('ok', item) | ('no', indok)."""
    if rule is None:
        rule = R(None, None, why=NOT_FOUND)
    hits = []
    if rule["pat"]:
        for c in cands:
            m = re.fullmatch(rule["pat"], c["n"])
            if not m or (rule["excl"] and re.search(rule["excl"], c["n"])):
                continue
            c = dict(c)
            ctext = m.groupdict().get("c")
            if rule["color_from"] == "none":
                c["ckey"], c["ctext"] = None, None
            elif ctext:
                c["ckey"], c["ctext"] = color_key(ctext, item), ctext
            elif c.get("color_raw"):
                c["ckey"], c["ctext"] = color_key(c["color_raw"], item), c["color_raw"]
            else:
                c["ckey"], c["ctext"] = None, None
            hits.append(c)
    # azonos URL kétszer (több keresésből) -> egyszer
    uniq = {}
    for h in hits:
        uniq.setdefault(h["url"], h)
    hits = list(uniq.values())
    priced = [h for h in hits if h.get("price")]
    if hits and not priced:
        return "no", "ok: a bolt a terméket ár nélkül listázza (nincs kiírt ár / 'Preis auf Anfrage')" + \
            near_text(hits, r".", 2)
    if priced:
        built = build(item, rule, priced)
        if built:
            return "ok", built
        return "no", "ok: a termék csak a data.js palettáján nem szereplő színben kapható" + near_text(priced, r".", 3)
    return "no", "ok: " + (rule["why"] or NOT_FOUND) + near_text(cands, rule["near"])


def write_output(path, shop, items, unmatched, blocked, extra=None):
    doc = dict(shop=shop, currency="CHF", fetched=datetime.date.today().isoformat())
    doc.update(extra or {})
    doc.update(items=items, unmatched=unmatched, blocked=blocked)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
# --------------------------------------------------------------------------- /KÖZÖS RÉSZ


# --------------------------------------------------------------------------- WooCommerce Store API
def woo_catalog(http, base):
    """A teljes katalógus a nyilvános Store API-ból (100 termék / kérés)."""
    out, page = [], 1
    while True:
        body = http.get("%s/wp-json/wc/store/v1/products?per_page=100&page=%d" % (base, page))
        arr = json.loads(body)
        if not arr:
            break
        out += arr
        if len(arr) < 100:
            break
        page += 1
    return out


def woo_price(p):
    pr = p.get("prices") or {}
    if pr.get("currency_code", "CHF") != "CHF":
        return None, None
    div = 10 ** int(pr.get("currency_minor_unit", 2))
    price = int(pr["price"]) / div if str(pr.get("price") or "").isdigit() else None
    reg = int(pr["regular_price"]) / div if str(pr.get("regular_price") or "").isdigit() else None
    lst = reg if (p.get("on_sale") and reg and price and reg > price) else None
    return (price or None), lst


STOCK_CLASS = {"in-stock": (True, "an Lager"), "available-on-backorder": (False, "Lieferbar auf Bestellung (Rückstand)"),
               "out-of-stock": (False, "nicht an Lager")}


def woo_cand(p, title=None, color_raw=None):
    price, lst = woo_price(p)
    sa = p.get("stock_availability") or {}
    in_stock, default_txt = STOCK_CLASS.get(sa.get("class"), (None, None))
    title = clean(title or p.get("name"))
    img = (p.get("images") or [{}])[0].get("src")
    return dict(title=title, n=norm(title), url=p.get("permalink"), price=price, list=lst,
                in_stock=in_stock, stock_text=clean(sa.get("text")) or default_txt,
                sku=p.get("sku") or None, mpn=None, image=img, weight_g=None, dims=None,
                color_raw=color_raw, pid=p.get("id"), type=p.get("type"))


def woo_expand_variations(http, base, p):
    """Változatos termék -> változatonkénti jelöltek (ár, készlet, szín az attribútumból)."""
    out = []
    for v in p.get("variations") or []:
        body = http.get("%s/wp-json/wc/store/v1/products/%d" % (base, v["id"]))
        vp = json.loads(body)
        attrs = [clean(a.get("value")) for a in v.get("attributes") or []]
        c = woo_cand(vp, title=p.get("name"), color_raw=" ".join(attrs))
        c["title"] = clean("%s – %s" % (clean(p.get("name")), " / ".join(attrs)))
        c["n"] = norm(p.get("name"))  # a mintát a szülő nevére illesztjük
        out.append(c)
    return out


# --------------------------------------------------------------------------- bolt-specifikus rész
SHOP = "waffenzimmi.ch"
BASE = "https://www.waffenzimmi.ch"
HOSTS = ("www.waffenzimmi.ch", "waffenzimmi.ch")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "waffenzimmi.json")
CATALOG = "nincs a boltban (a teljes katalógusban nincs pontosan azonos termék)"
SOLID = "a 'Solid Line' (új, Solid-előagyas) kivitel másik termék, nem vettük fel"

RULES = {
    # ---- alapfegyverek
    "schm-s4f": R(None, r'schmeisser ar15 s4f ' + CW + r' 10\.5" \.223 rem\.', near=r"schmeisser ar15 s4f",
                  note=SOLID),
    "schm-m4fl": R(None, r'schmeisser ar15 m4fl 14\.5" ' + CW + r' - \.223 rem\.', near=r"schmeisser ar15 m4fl",
                   note=SOLID + "; a 12.5\"-es Solid Line sem ez a modell"),
    "schm-m5fl": R(None, r'schmeisser ar15 m5fl 16\.75"', near=r"schmeisser ar15 m5fl",
                   note=SOLID + "; a bolti oldal szerint 16.75\", 3550 g, flip-up irányzékkal"),
    "schm-dyn": R(None, None, why="a Schmeisser AR15 Dynamic nincs a katalógusban", near=r"^schmeisser ar15 (m5fl|duty|dmr)"),
    "dd-v7": R(None, None, why="a DDM4 V7 nincs a katalógusban (csak MK18 SBR változatok)", near=r"^daniel defense (ddm4|mk18)"),
    "bcm-recce16": R(None, None, why=NOT_SOLD),
    # ---- markolatok
    "mp-moe": R(None, None, why="csak MOE AK / AK+ markolat van (AK-platformra, más termék)", near=r"^magpul moe ak\+? grip"),
    "mp-moek2": R(None, None, why=CATALOG),
    "mp-moek2p": R(None, None, why=CATALOG),
    "mp-miad": R(None, None, why=CATALOG),
    "bcm-g0": R(None, None, why=NOT_SOLD),
    "bcm-g3": R(None, None, why=NOT_SOLD),
    "b5-t22": R(None, None, why=CATALOG),
    "b5-t23": R(None, None, why=CATALOG),
    # ---- tusok
    "mp-ctr": R(None, None, why=CATALOG),
    "mp-moesl": R(None, None, why="csak a MOE SL-S tus (más modell) és a MOE SL előagy van", near=r"^magpul moe sl"),
    "mp-moeslk": R(None, None, why="csak a MOE SL-S tus (más modell) van", near=r"^magpul moe sl-s"),
    "mp-ubr2": R(None, None, why=CATALOG),
    "mp-prs3": R(None, None, why=CATALOG),
    "mp-prslite": R(None, None, why=CATALOG),
    "bcm-s0": R(None, None, why=NOT_SOLD),
    "bcm-s1": R(None, None, why=NOT_SOLD),
    "b5-bravo": R(None, None, why=CATALOG),
    "b5-esop": R(None, None, why="csak a B5 'Government Issue SOPMOD' tus van (más modell, ár nélkül)",
                 near=r"^b5 systems"),
    "b5-prec": R(None, None, why=CATALOG),
    # ---- tustalpak
    "mp-pad030": R(None, None, why=CATALOG),
    "mp-pad055": R(None, None, why=CATALOG),
    "mp-pad070": R(None, None, why=CATALOG),
    "mp-padsl": R(None, None, why=CATALOG),
    # ---- red dot / holo
    "ap-t2": R(None, None, why="Aimpoint Micro T-2 nincs a katalógusban"),
    "ap-m5": R(None, None, why="Aimpoint CompM5 nincs a katalógusban"),
    "ap-pro": R(None, None, why="Aimpoint PRO nincs a katalógusban"),
    "ap-acro": R(None, None, why="az ACRO P-2 csak pisztoly-kombóban szerepel, önállóan és Picatinny talppal nem",
                 near=r"acro p-2"),
    "eo-exps3": R(None, r"eotech exps 3-0 nv mit qd picatinny montage(?: (?P<c>tan))?",
                  note="EXPS3-0 (NV-kompatibilis, QD-szerelékkel); a TAN színű külön termék"),
    "eo-exps2": R(None, None, why="csak a zöld szálkeresztes EXPS2-0 'Green Reticle' van; a data.js a (piros) EXPS2-0",
                  near=r"eotech exps 2-0"),
    "eo-xps2": R(None, None, why=CATALOG),
    "hs-510c": R(None, r"holosun hs510c solar red circle dot 2 moa / 65 moa"),
    "hs-aems": R(None, None, why="csak az AEMS EVO (más modell) van", near=r"holosun aems"),
    "hs-403b": R(None, None, why=CATALOG),
    "tj-mro": R(None, None, why=CATALOG),
    "vx-uh1": R(None, None, why=CATALOG),
    "vx-sparc": R(None, r"vortex sparc ar 2 moa red dot",
                  note="a bolti leírás szerint a mellékelt távtartóval két szerelési magasság (Lower 1/3 40 mm / "
                       "abszolút 37 mm) – ez a data.js 'magasító'-ja"),
    "vx-crossfire": R(None, None, why=CATALOG),
    "vx-se16": R(None, None, why="Strike Eagle 1-6x24 nincs (csak nagyobb nagyítású Strike Eagle-ök)",
                 near=r"vortex strike eagle"),
    "vx-se18": R(None, None, why="Strike Eagle 1-8x24 nincs (csak nagyobb nagyítású Strike Eagle-ök)",
                 near=r"vortex strike eagle"),
    "vx-pst16": R(None, r"vortex viper pst gen ii 1-6x24 sfp vmr-2 (moa|mrad)",
                  note="két retikulos kivitel (VMR-2 MOA / MRAD) azonos áron; a data.js nem határozza meg"),
    "vx-razor110": R(None, None, why="Razor HD Gen III 1-10x24 nincs (csak más Razor modellek)", near=r"vortex razor"),
    "tj-acog": R(None, None, why=CATALOG),
    "tj-credo16": R(None, None, why=CATALOG),
    "tj-vcog": R(None, None, why=CATALOG),
    "eo-vudu16": R(None, None, why=CATALOG),
    "eo-vudu110": R(None, None, why=CATALOG),
    # ---- szerelék
    "gs-sp30": R(None, None, why=NOT_SOLD),
    "vx-cant30": R(None, None, why="a Vortex Sport Cantilever nincs a katalógusban"),
    # ---- nagyítók
    "eo-g33": R(None, r"eotech g33 magnifier mit qd-sts-montage picatinny(?: (?P<c>tan))?",
                note="G33 + STS (QD) szerelék; a TAN színű külön termék"),
    "eo-g43": R(None, None, why=CATALOG),
    "ap-3x": R(None, None, why=CATALOG),
    "hs-hm3x": R(None, None, why="csak a HM3XT Elite (más modell) van", near=r"holosun hm3x"),
    "vx-m3x": R(None, r"vortex micro 3x magnifier qd-flip mount"),
    # ---- BUIS
    "mp-mbus3": R(None, None, why=CATALOG),
    "mp-mbuspro": R(None, None, why=CATALOG),
    "mp-mbusoff": R(None, None, why=CATALOG),
    # ---- lámpák
    "sf-m600": R(None, None, why="M600 nincs; csak a 'Dual Fuel Scout Light Pro' (M640DF/M640DFT) van",
                 near=r"surefire dual fuel scout light pro"),
    "sf-m640": R(None, None, why="bizonytalan: a bolti cikkszám M640DF-PRO, de a leírás és a képek az M640DFT 'Turbo' "
                                 "változatot (700 lm) írják – nem azonosítható biztosan az M640DF Scout Light Pro-val",
                 near=r"surefire dual fuel scout light pro"),
    "sf-m300": R(None, None, why="M300 nincs; csak a 'Dual Fuel Scout Light Pro' (M640DF/M640DFT) van",
                 near=r"surefire dual fuel scout light pro"),
    "sf-x300": R(None, None, why="csak X300U-A és X300-A Turbo van; az X300U-B (Picatinny-csavaros rögzítés) nincs",
                 near=r"^surefire x300"),
    "sl-hlx": R(None, None, why="csak ProTac Rail Mount 1 (más modell) van", near=r"streamlight protac rail mount"),
    "sl-tlr1": R(None, r"streamlight tlr-1 hl " + CW),
    # ---- M-LOK
    "mp-rvg": R(None, None, why="csak a Picatinny (1913) sínre való RVG van (a bolti leírás szerint), M-LOK RVG nincs",
                near=r"^magpul (rvg|m-lok mvg)"),
    "mp-hstop": R(None, None, why=CATALOG),
    "bcm-vg3": R(None, None, why=NOT_SOLD),
    "bcm-kag": R(None, None, why=NOT_SOLD),
    "mp-rail5": R(None, r"magpul m-lok rail section aluminium 5 slots " + CW),
    "mp-qd": R(None, r"magpul m-lok qd sling mount"),
    "mp-cover": R(None, r"magpul m-lok rail cover type 2 " + CW),
    "mp-cable": R(None, None, why=CATALOG),
    # ---- bipod
    "mp-bipod": R(None, r"magpul bipod aluminium for m-lok " + CW,
                  note="Magpul Bipod for M-LOK (alumínium)"),
    "hr-sbrm": R(None, None, why="csak BRM (forgó csukló nélküli) Harris M-LOK bipodok vannak; az S-BRM (forgó) + "
                                 "M-LOK adapter csomag nincs", near=r'^harris bipod m-lok .*6"-9"'),
    # ---- torkolat
    "sf-warcomp": R(None, r'surefire warcomp 5\.56mm - 1/2"x28', note="1/2\"x28 menet, a leírás szerint WARCOMP-556-CTN"),
    "sf-3p": R(None, None, why=CATALOG),
    "bcm-comp0": R(None, None, why=NOT_SOLD),
    "sf-rc2": R(None, None, why=CATALOG),
    # ---- elsütők
    "gs-ssae": R(None, None, why=NOT_SOLD),
    "gs-ssa": R(None, None, why=NOT_SOLD),
    "gs-sde": R(None, None, why=NOT_SOLD),
    "gs-s3g": R(None, None, why=NOT_SOLD),
    # ---- felhúzókar
    "rd-raptor": R(None, None, why="a (nem LT) Raptor nincs, csak a Raptor-LT", near=r"radian weapons raptor"),
    "rd-raptorlt": R(None, r"radian weapons raptor-lt amidextrius charging handle " + CW),
    "rd-raptorsd": R(None, None, why="a Raptor-SD nincs, csak a Raptor-LT", near=r"radian weapons raptor"),
    "bcm-ch4": R(None, None, why=NOT_SOLD),
    "bcm-ch3": R(None, None, why=NOT_SOLD),
    "gs-ach": R(None, None, why=NOT_SOLD),
    # ---- kezelőszervek
    "rd-talon": R(None, r"radian weapons ar-15 talon safety selector ambidextrous"),
    "mp-bad": R(None, r"magpul ar-15/m16 b\.a\.d\. lever"),
    "mp-trigguard": R(None, None, why=CATALOG),
    # ---- tárak
    "mp-pm10": R(None, r"magpul pmag 10 gen m3 ar/m4", note="AR/M4 (5.56/.223) kivitel"),
    "mp-pm20": R(None, None, why="PMAG 20 AR/M4 nincs (csak a .308-as LR/SR)", near=r"pmag 20"),
    "mp-pm30": R(None, None, why="bizonytalan: a bolti név 'PMAG 30 Gen M3 AR/M4' (ablak nélkül), a leírás szerint "
                                 "viszont 'mit Ladefenster' (ablakos) – a kivitel nem azonosítható biztosan",
                 near=r"^magazin magpul pmag 30 gen m3 ar/m4"),
    "mp-pm30w": R(None, r"magazin magpul pmag 30 gen m3 ar/m4 window \.223 rem\. / " + CW),
    "mp-pm40": R(None, None, why=CATALOG),
    "mp-d60": R(None, None, why="a D-60 nincs (csak D-50 dobtár .308-hoz)", near=r"pmag d-50"),
    # ---- szíjak
    "mp-ms1": R(None, None, why="csak az MS1 QDM (QDM forgókapcsos, más kivitel) van", near=r"^magpul ms1"),
    "mp-ms4": R(None, r"magpul ms4 dual qd gen 2 sling " + CW,
                note="Gen 2 kivitel; az 'MS4 QDM' külön (más) termék"),
    "bfg-vickers": R(None, r"blue force gear vickers sling",
                     note="a bolti leírás szerint a Vickers Combat Applications Sling"),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás, stdout-ra)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/waffenzimmi.json)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["id"] in wanted]
    http = Http(HOSTS, args.cache)
    items, unmatched, blocked = {}, {}, None
    try:
        raw = woo_catalog(http, BASE)
        sys.stderr.write("katalógus: %d termék\n" % len(raw))
        cands = []
        pats = [r["pat"] for r in RULES.values() if r["pat"]]
        for p in raw:
            c = woo_cand(p)
            if p.get("type") == "variable" and any(re.fullmatch(pt, c["n"]) for pt in pats):
                cands += woo_expand_variations(http, BASE, p)
            else:
                cands.append(c)
    except Blocked as e:
        blocked = str(e)
        cands = []
    for e in entries:
        pid = e["id"]
        if blocked:
            unmatched[pid] = "ok: nem ellenőrizve – a bolt blokkolta a lekérést (%s)" % blocked
            continue
        rule = RULES.get(pid) or R(None, re.escape(norm("%s %s" % (e["brand"], e["name"]))), why=CATALOG)
        kind, res = decide(e, rule, cands)
        if kind == "ok":
            items[pid] = res
            sys.stderr.write("%-14s -> %s  CHF %s\n" % (pid, res["title"], res["price_chf"]))
        else:
            unmatched[pid] = res
    if blocked and not items:
        items = {}
    out = args.out or (None if args.only else OUT_JSON)
    write_output(out, SHOP, items, unmatched, blocked,
                 extra=dict(source=BASE + "/wp-json/wc/store/v1/products (WooCommerce Store API)",
                            price_basis="kiírt fogyasztói ár CHF, ÁFÁ-val; akciónál price_chf = akciós ár, "
                                        "list_chf = áthúzott (regular) ár",
                            stock_basis="in_stock: Store API stock_availability (in-stock=true, "
                                        "backorder/out-of-stock=false)"))
    sys.stderr.write("Kész: %d egyezés, %d unmatched, %d HTTP kérés%s\n" % (
        len(items), len(unmatched), http.n, (", BLOCKED: " + blocked) if blocked else ""))


if __name__ == "__main__":
    main()
