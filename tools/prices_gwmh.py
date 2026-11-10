#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gwmh-shop.ch (GWM+H AG – Glarner Waffen Manufaktur + Handelshaus AG, Busswil TG) ár-gyűjtő
az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_gwmh.py                        # -> assets/prices/gwmh.json
    python3 tools/prices_gwmh.py --only mp-pm10,hs-hm3x # csak néhány id (stdout)
    python3 tools/prices_gwmh.py --cache /tmp/gwcache   # HTTP-válaszok gyorsítótára

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  1. A bolt ePages 6 alapú (www.gwmh-shop.ch/epages/64344916.sf). Nyilvános JSON-végpont nincs
     (a /rs/ REST API-t a robots.txt tiltja), ezért a bolt saját termékkeresőjét használja
     (ViewAction=FacetedSearchProducts, 50 találat/oldal) márka- és típuskifejezésekkel (~20
     keresés). A kereső "fuzzy", sok idegen találatot is ad – ezért a döntés a pontos névmintán múlik.
     Megjegyzés: a robots.txt a keresőoldalak tömeges indexelését tiltja; ez a szkript csak néhány
     tucat, emberi tempójú (>= 1,2 s) keresést futtat személyes ár-összevetéshez.
  2. Minden data.js id-hoz a RULES táblában a bolti név PONTOS (fullmatch) mintája áll (normalizált,
     kisbetűs névre). A bolti nevek hosszú leíró nevek ("Magazin, Magpul AR15 10RD PMAG GEN M3, …").
  3. Egyezésnél a termékoldalt is letölti: készlet ("Auf Lager …"), cikkszám, márka, kanonikus URL,
     és ha a leírás megadja, a szín ("Farbe: Schwarz").
  Ha nincs pontos egyezés: "unmatched" okkal + a legközelebbi bolti termékek (NEM azonos).
  Árat soha nem becsül, nem számol, nem vált át.

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
    (r"\btan\b|\bsand\b|\bpink\b|\bred\b|\bblue\b|\bwhite\b|\bweiss\b|\bplum\b|\bzombie\b|\bcamo\b|"
     r"\bddc\b|multicam|rattlecan|ral 8000|yellow", None),
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
CW = (r"(?P<c>black|schwarz|blk|fde|flat dark earth|dark earth brown|dark earth|olive drab|olive green|olive|oliv|"
      r"od green|o\.d\. green|odg|stealth gr[ae]y|sniper gr[ae]y|gr[ae]y|grau|medium coyote tan|modified coyote brown|"
      r"coyote brown|coyote tan|coyote|tan|bronze|ranger green|foliage green|foliage|wolf gr[ae]y|sand|red|pink|"
      r"zombie green|plum|white|ddc|multicam black|multicam|rattlecan|ral 8000|yellow)")


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
    notes = [rule["note"]] if rule.get("note") else []
    if not by_color and other and not item["colors"]:
        # színpaletta nélküli tétel (pl. optika): a csak más színben kapható azonos modell is elfogadható
        by_color["_"], alts, other = other[0], other[1:], []
        notes.append("csak '%s' színben kapható ebben a boltban" % by_color["_"].get("ctext"))
    if not by_color:
        return None
    colored = {k: v for k, v in by_color.items() if k != "_"}
    if "black" in colored:
        main_key = "black"
    elif "_" in by_color:
        main_key = "_"
    else:
        main_key = pick_main(colored, item)
    m = by_color[main_key]
    out = dict(title=m["title"], price_chf=m["price"], list_chf=m.get("list"), url=m["url"],
               image=m.get("image"), in_stock=m.get("in_stock"), stock_text=m.get("stock_text"),
               sku=m.get("sku"), mpn=m.get("mpn"), weight_g=m.get("weight_g"), dims=m.get("dims"))
    if colored:
        order = list(item["colors"] or []) + [k for k in ITEM_COLOR_KEYS + BASE_COLOR_KEYS
                                              if k not in (item["colors"] or [])]
        out["color"] = main_key if main_key != "_" else None
        out["colors_prices"] = {k: dict(price_chf=colored[k]["price"], list_chf=colored[k].get("list"),
                                        url=colored[k]["url"], in_stock=colored[k].get("in_stock"),
                                        title=colored[k]["title"])
                                for k in sorted(colored, key=order.index)}
    if main_key != "_" and "_" in by_color:
        alts.append(by_color["_"])
    if alts:
        out["alternatives"] = [dict(title=c["title"], price_chf=c["price"], url=c["url"]) for c in alts]
    if other:
        out["other_colors"] = [dict(title=c["title"], price_chf=c["price"], url=c["url"]) for c in other]
    if main_key == "_" and item["colors"] and rule.get("color_from") != "none":
        notes.append("a szín a bolti terméknévben nincs megadva")
    elif colored and main_key not in ("black", "_"):
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


# --------------------------------------------------------------------------- bolt-specifikus rész
SHOP = "gwmh-shop.ch"
SITE = "https://www.gwmh-shop.ch"
BASE = SITE + "/epages/64344916.sf/de_CH/"
HOSTS = ("www.gwmh-shop.ch", "gwmh-shop.ch")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "gwmh.json")
SEARCH_TERMS = ["magpul", "pmag", "MOE", "MBUS", "M-LOK", "bcm", "bravo company", "b5", "geissele", "radian",
                "aimpoint", "eotech", "holosun", "trijicon", "vortex", "surefire", "streamlight", "harris",
                "blue force", "vickers", "schmeisser", "daniel defense"]
NOT_IN = "nincs a boltban (a márka- és típuskeresések nem adtak ilyen terméket)"


def gw_search(http, q):
    out = []
    for page in range(1, 4):  # max. 150 találat / kifejezés (a kereső fuzzy, a releváns találatok elöl vannak)
        url = BASE + "?ViewAction=FacetedSearchProducts&ObjectPath=/Shops/64344916&SearchString=%s&PageSize=50&Page=%d" % (
            urllib.parse.quote_plus(q), page)
        s_el = soup(http.get(url))
        boxes = s_el.select(".HotDeal")
        for d in boxes:
            a = d.select_one("a.ProductName")
            if not a or not a.get("href"):
                continue
            pr = d.select_one("[itemprop=price]")
            img = d.select_one("img")
            title = clean(a.get("title") or a.get_text(" "))
            href = a["href"]
            out.append(dict(title=title, n=norm(title), url=urllib.parse.urljoin(BASE, href),
                            price=float(pr["content"]) if pr and pr.get("content") else None, list=None,
                            in_stock=None, stock_text=None, sku=None, mpn=None,
                            image=urllib.parse.urljoin(SITE, img["src"]) if img and img.get("src") else None,
                            weight_g=None, dims=None))
        info = s_el.select_one(".PagerInfoLeft")
        m = re.search(r"von (\d+)", info.get_text(" ") if info else "")
        if not m or page * 50 >= int(m.group(1)):
            break
    return out


def gw_enrich(http, item, entry):
    """Termékoldal: készlet, cikkszám, márka, kanonikus URL, (leírásban megadott) szín, áthúzott ár."""
    body = http.get(item["url"])
    s_el = soup(body)
    can = s_el.find("link", rel="canonical")
    if can and can.get("href"):  # a kanonikus (beszédes) URL; az ObjectPath-os URL marad product_url-ként
        item["product_url"], item["url"] = item["url"], can["href"]
    sku = s_el.select_one("[itemprop=sku]")
    if sku:
        item["sku"] = clean(sku.get("content") or sku.get_text(" "))
    av = s_el.select_one("[itemprop=availability]")
    avail = (av.get("href") or av.get("content") or "") if av else ""
    txt = clean(s_el.get_text(" "))
    m = re.search(r"Artikel-Nr\.:\s*\S+\s+(?:Produkt jetzt als Erster bewerten\s+)?(.+?)\s+CHF", txt)
    if m:
        item["stock_text"] = m.group(1)[:120]
    item["in_stock"] = True if avail.endswith("InStock") else (False if avail else None)
    old = s_el.select_one(".ProductDetails .LineThrough, .PriceArea .LineThrough")
    if old:
        lp = re.search(r"([0-9][0-9'’]*\.[0-9]{2})", old.get_text(" "))
        if lp:
            v = float(re.sub(r"['’]", "", lp.group(1)))
            if v > (item["price_chf"] or 0):
                item["list_chf"] = v
    desc = s_el.select_one("[itemprop=description]") or s_el.select_one(".Description")
    dt = clean(desc.get_text(" ")) if desc else ""
    cm = re.search(r"Farbe\s*:\s*([A-Za-zÄÖÜäöü\- ]+?)(?:\s{1,}[A-ZÄÖÜ][a-zäöü]+\s*:|\s*Achtung|$)", dt)
    if cm and not item.get("colors_prices") and entry["colors"]:
        key = color_key(cm.group(1), entry)
        if key:
            item["color"] = key
            item["colors_prices"] = {key: dict(price_chf=item["price_chf"], list_chf=item.get("list_chf"),
                                               url=item["url"], in_stock=item["in_stock"], title=item["title"])}
            if item.get("note"):
                item["note"] = "; ".join(x for x in item["note"].split("; ")
                                         if x != "a szín a bolti terméknévben nincs megadva") or None
                if not item["note"]:
                    del item["note"]
            item["color_source"] = "termékleírás: 'Farbe: %s'" % cm.group(1).strip()


RULES = {
    # ---- alapfegyverek
    "schm-s4f": R(None, None, why="Schmeisser AR15 puska nincs a kínálatban (csak Schmeisser AR15 tár)",
                  near=r"schmeisser ar15"),
    "schm-m4fl": R(None, None, why="Schmeisser AR15 puska nincs a kínálatban (csak Schmeisser AR15 tár)",
                   near=r"schmeisser ar15"),
    "schm-m5fl": R(None, None, why="Schmeisser AR15 puska nincs a kínálatban (csak Schmeisser AR15 tár)",
                   near=r"schmeisser ar15"),
    "schm-dyn": R(None, None, why="Schmeisser AR15 puska nincs a kínálatban (csak Schmeisser AR15 tár)",
                  near=r"schmeisser ar15"),
    "dd-v7": R(None, None, why=NOT_IN),
    "bcm-recce16": R(None, None, why=NOT_IN),
    # ---- markolatok / tusok
    "mp-moe": R(None, None, why="csak a MOE SL Grip (más modell) van", near=r"magpul ar-15 moe sl grip"),
    "mp-ctr": R(None, None, why="CTR tus nincs (csak MOE Fixed / MOE Fixed Rifle tus)", near=r"magpul ar-15 moe fixed"),
    # ---- optikák
    "hs-510c": R(None, r"- rotpunkt- / reflexvisier holosun hs510c dot sight elite",
                 note="a bolti név 'ELITE' jelzőt tartalmaz, de a leírás szerint HS510C (piros 2 MOA pont / 65 MOA kör, "
                      "alumínium ház titán burkolattal); a bolti cikkszám (10678406000) a HS510C forgalmazói cikkszáma "
                      "(az armamat.ch-n is ez)"),
    "hs-hm3x": R(None, r"vergrösserungsoptik; magnifier, holosun magnifier hm3x"),
    "hs-aems": R(None, None, why=NOT_IN),
    # ---- BUIS
    "mp-mbus3": R(None, None, why="csak a régi MBUS Gen 2 első irányzék van (MBUS 3 / szett nincs)",
                  near=r"mbus gen 2"),
    "mp-mbuspro": R(None, None, why="MBUS Pro nincs (csak MBUS Gen 2 első irányzék)", near=r"mbus gen 2"),
    "mp-mbusoff": R(None, None, why="MBUS Pro Offset nincs (csak MBUS Gen 2 első irányzék)", near=r"mbus gen 2"),
    # ---- M-LOK / markolat
    "mp-rvg": R(None, None, why="csak MOE MVG függőleges markolat van (más modell)", near=r"magpul moe mvg"),
    "bcm-vg3": R(None, None, why="csak a KeyMod-os BCMGUNFIGHTER Short Vertical Grip van (nem M-LOK Mod 3)",
                 near=r"bcmgunfighter short vertical grip"),
    "mp-rail5": R(None, None, why="csak a GWMH saját M-LOK sínje van (nem Magpul)",
                  near=r"gwmh m-lok rail picantinnyschiene 5 slots"),
    # ---- bipod
    "hr-sbrm": R(None, None,
                 why="a bolti 'HARRIS Zweibein … BRM Series S' (S-BRM) riemenbügelös rögzítésű, M-LOK adapter nincs "
                     "a csomagban; a data.js tétel S-BRM + M-LOK adapter", near=r"harris zweibein"),
    # ---- tárak
    "mp-pm10": R(None, r"magazin, magpul ar15 10rd pmag gen m3, für ar15-systeme, kal\. \.223rem / 5,56x45, 10 schuss",
                 note="AR15 (.223/5.56) kivitel"),
    "mp-pm30": R(None, None, why="csak az ablakos PMAG 30 GEN M3 WINDOW van", near=r"30rd pmag gen m3 window"),
    "mp-pm30w": R(None, r"magazin, ar-15 30rd pmag gen m3 window magazine, für ar15-systeme, kal\. \.223rem/5,56x45, "
                        r"30 patronen", note="a bolti leírás szerint Magpul, AR15, .223/5.56"),
    "mp-d60": R(None, None, why="a D-60 nincs (csak D-50 dobtárak)", near=r"pmag d-50"),
    # ---- szíjak
    "mp-ms1": R(None, None, why="csak 'Magpul 1 Point Sling' (más termék) van", near=r"magpul 1 point sling"),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás, stdout-ra)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/gwmh.json)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["id"] in wanted]
    http = Http(HOSTS, args.cache)
    terms = list(SEARCH_TERMS)
    for e in entries:  # szabály nélküli (új) id: "márka + név" keresés is
        if e["id"] not in RULES and e["brand"].lower() not in [t.lower() for t in terms]:
            terms.append("%s %s" % (e["brand"], e["name"]))
    items, unmatched, blocked, cands = {}, {}, None, []
    for i, q in enumerate(terms, 1):
        sys.stderr.write("[%d/%d] keresés: %s\n" % (i, len(terms), q))
        try:
            cands += gw_search(http, q)
        except Blocked as e:
            blocked = str(e)
            break
    for e in entries:
        pid = e["id"]
        if blocked:
            unmatched[pid] = "ok: nem ellenőrizve – a bolt blokkolta a lekérést (%s)" % blocked
            continue
        rule = RULES.get(pid) or R(None, re.escape(norm("%s %s" % (e["brand"], e["name"]))), why=NOT_IN)
        kind, res = decide(e, rule, cands)
        if kind == "ok":
            try:
                gw_enrich(http, res, e)
            except Blocked as ex:
                blocked = str(ex)
                break
            items[pid] = res
            sys.stderr.write("%-14s -> %s  CHF %s\n" % (pid, res["title"], res["price_chf"]))
        else:
            unmatched[pid] = res
    if blocked:
        items = {}
        for e in entries:
            unmatched.setdefault(e["id"], "ok: nem ellenőrizve – a bolt blokkolta a lekérést (%s)" % blocked)
    out = args.out or (None if args.only else OUT_JSON)
    write_output(out, SHOP, items, unmatched, blocked,
                 extra=dict(source=BASE + "?ViewAction=FacetedSearchProducts (a bolt termékkeresője) + termékoldal",
                            price_basis="kiírt ár CHF, 'Preis inkl. MwSt., zzgl. Versand'"))
    sys.stderr.write("Kész: %d egyezés, %d unmatched, %d HTTP kérés%s\n" % (
        len(items), len(unmatched), http.n, (", BLOCKED: " + blocked) if blocked else ""))


if __name__ == "__main__":
    main()
