#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tacstore.ch (Euschen Trading GmbH, Dietikon ZH) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_tacstore.py                      # -> assets/prices/tacstore.json
    python3 tools/prices_tacstore.py --only mp-moe,eo-g33 # csak néhány id (stdout)
    python3 tools/prices_tacstore.py --cache /tmp/tscache # HTTP-válaszok gyorsítótára

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  1. A bolt (Trueloaded-motor, ~100 000 termék) kereső-listája (/catalog/all-products?keywords=…)
     a találatokat egy beágyazott JSON-ban (var entryData = JSON.parse('…')) is közli: név, URL,
     aktuális / akciós / áthúzott ár, készletjelző, gyártói cikkszám (products_model), kép.
     Termékoldalt nem kell letölteni. A boltban minden szín KÜLÖN termék ("… - Black",
     "… - Dark Earth"), a színt a név végéből (vagy a cikkszám -BLK/-FDE… végződéséből) olvassuk ki.
  2. Minden data.js id-hoz a RULES táblában keresőkifejezés(ek) + a bolti név PONTOS (fullmatch)
     mintája áll (normalizált, kisbetűs névre). Az összes keresés találatait egyesítjük, és minden
     szabályt a teljes halmazon értékelünk ki. Szabály nélküli új id-nál "márka + név" a keresés,
     és csak a teljes névegyezés számít.
  3. Ha nincs pontos egyezés: "unmatched" okkal + a legközelebbi bolti termékek (NEM azonos).
     Árat soha nem becsül, nem számol (szettárat sem ad össze), nem vált át.
  in_stock: true csak az "Umgehend versandbereit" (saját raktár) jelzésnél; a "Versandbereit in X
  Tagen" / "Lieferfrist" / "Auf Anfrage" / "Derzeit nicht lagernd" rendelhető vagy nem, de nincs
  raktáron (false) – a pontos szöveg a stock_text-ben.

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
        out["color"] = main_key
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
SHOP = "tacstore.ch"
BASE = "https://www.tacstore.ch"
HOSTS = ("www.tacstore.ch", "tacstore.ch")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "tacstore.json")
C = r" - " + CW  # a bolti nevek végén: " - <szín>"

MPN_COLOR = {"BLK": "black", "BK": "black", "FDE": "fde", "ODG": "olive drab", "GRY": "grey", "MCT": "coyote",
             "COY": "coyote", "RGR": "ranger green", "FOL": "foliage", "TN": "tan", "TAN": "tan",
             "MCB": "modified coyote brown", "PLM": "plum", "PNK": "pink"}


def unjs(c):
    """JavaScript egyszeres idézőjeles string -> Python str."""
    mp = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}
    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)",
                  lambda m: chr(int(m.group(1)[1:], 16)) if len(m.group(1)) == 5 and m.group(1)[0] == "u"
                  else mp.get(m.group(1), m.group(1)), c, flags=re.S)


def ts_price(x):
    if not x:
        return None
    m = re.search(r'content="([0-9.]+)"', str(x))
    return float(m.group(1)) if m else None


def ts_cand(p):
    title = clean(p.get("products_name"))
    pr = p.get("price") or {}
    special, old, cur = ts_price(pr.get("special")), ts_price(pr.get("old")), ts_price(pr.get("current"))
    price = special or cur
    lst = old if (special and old and old > special) else None
    si = p.get("stock_indicator") or {}
    txt = clean(si.get("stock_indicator_text"))
    in_stock = (si.get("stock_code") == "in-stock" and txt.lower().startswith("umgehend")) if txt else None
    model = clean(p.get("products_model"))
    mpn = model.split("_", 1)[1] if "_" in model else (model or None)
    m = re.search(r"-([A-Z]{2,3})$", mpn or "")
    img = p.get("image") or None
    if img and img.startswith("/"):
        img = BASE + img
    return dict(title=title, n=norm(title), url=p.get("link"), price=price, list=lst, in_stock=in_stock,
                stock_text=txt or None, sku=model or None, mpn=mpn, image=img, weight_g=None, dims=None,
                color_raw=MPN_COLOR.get(m.group(1)) if m else None)


def ts_search(http, q):
    out = []
    for page in range(1, 4):
        url = "%s/catalog/all-products?keywords=%s&max_items=72" % (BASE, urllib.parse.quote_plus(q))
        if page > 1:
            url += "&page=%d" % page
        body = http.get(url)
        m = re.search(r"var entryData = JSON\.parse\('((?:[^'\\]|\\.)*)'\)", body)
        if not m:
            break
        prods = json.loads(unjs(m.group(1))).get("products") or {}
        prods = list(prods.values()) if isinstance(prods, dict) else []
        out += [ts_cand(p) for p in prods]
        if len(prods) < 72:
            break
    return out


SCHM = r"schmeisser ar15 (s4f|m4fl|m5fl|dynamic)"
RULES = {
    # ---- alapfegyverek (csak a .223-as, nem CSS-tusos, nem 'Complete Upper' kivitel)
    "schm-s4f": R("schmeisser ar15", r"schmeisser ar15 s4f 10\.5 inch \.223 rem" + C, near=SCHM,
                  note="a CSS-tusos, a 9 mm-es (AR15-9) és a 'Complete Upper' változat másik termék"),
    "schm-m4fl": R("schmeisser ar15", r"schmeisser ar15 m4fl 14\.5 inch \.223 rem" + C, near=SCHM,
                   note="a CSS-tusos, a 9 mm-es (AR15-9) és a 'Complete Upper' változat másik termék"),
    "schm-m5fl": R("schmeisser ar15", r"schmeisser ar15 m5fl 16\.75 inch \.223 rem" + C, near=SCHM,
                   note="a 9 mm-es (AR15-9) és a 'Complete Upper' változat másik termék"),
    "schm-dyn": R("schmeisser ar15", None,
                  why="bizonytalan: a bolt 'AR15 Dynamic L 16.75 Inch' (Black / Grey) néven árul 16.75\"-es Dynamic "
                      "puskát; az 'M5FL' megjelöléssel való azonosság a bolti oldalon nincs kimondva",
                  near=r"schmeisser ar15 dynamic l 16\.75"),
    "dd-v7": R("daniel defense ddm4 v7", r"daniel defense ddm4 v7 16 inch 5\.56 nato" + C,
               near=r"daniel defense ddm4 v7", note="16\" kivitel; a 'No Mag' (Grey), PRO, S, SLW és K változat más termék"),
    "bcm-recce16": R("bcm recce", r"bcm recce-16 mcmr15 5\.56" + C,
                     note="RECCE-16 MCMR15 (15\" előagy), 5.56; a Precision / Enhanced Lightweight / .300 BLK más termék"),
    # ---- markolatok
    "mp-moe": R("magpul moe grip", r"magpul moe grip ar15" + C),
    "mp-moek2": R("magpul moe k2 grip", r"magpul moe k2 ar15 grip" + C),
    "mp-moek2p": R("magpul moe k2 grip", r"magpul moe k2\+ grip" + C),
    "mp-miad": R("magpul miad", r"magpul miad 5\.56 grip gen 1\.1 type 1" + C,
                 note="AR-15 (5.56, Type 1) kivitel; a 7.62 Type 2 másik termék"),
    "bcm-g0": R("bcm gunfighter grip", r"bcm gunfighter grip mod 0" + C),
    "bcm-g3": R("bcm gunfighter grip", r"bcm gunfighter grip mod 3" + C, note="a 'Mod 3-KD' másik termék"),
    "b5-t22": R(["b5 systems grip", "b5 systems"], None, why="a Type 22 P-Grip nincs (csak Type 23)",
                near=r"b5 systems p-grip"),
    "b5-t23": R(["b5 systems grip", "b5 systems"], r"b5 systems p-grip type 23" + C),
    # ---- tusok
    "mp-ctr": R("magpul ctr", r"magpul ctr carbine mil-spec stock" + C, note="Mil-Spec csöves kivitel"),
    "mp-moesl": R("magpul moe sl stock", r"magpul moe sl carbine stock mil-spec" + C, note="Mil-Spec csöves kivitel"),
    "mp-moeslk": R("magpul sl-k", r"magpul moe sl-k carbine stock mil-spec" + C, note="Mil-Spec csöves kivitel"),
    "mp-ubr2": R("magpul ubr", r"magpul ubr gen 2\.0 collapsible stock" + C),
    "mp-prs3": R("magpul prs", r"magpul prs gen 3 rifle stock" + C),
    "mp-prslite": R("magpul prs", r"magpul prs lite stock" + C),
    "bcm-s0": R("bcm gunfighter stock", r"bcm gunfighter stock mod 0" + C,
                note="a 'Stock Assembly Mod 0 SOPMOD' másik termék"),
    "bcm-s1": R("bcm gunfighter stock", r"bcm gunfighter stock mod 1 sopmod compartment" + C,
                note="tárolórekeszes (Compartment) SOPMOD kivitel"),
    "b5-bravo": R(["b5 systems stock", "b5 systems"], r"b5 systems bravo stock" + C),
    "b5-esop": R(["b5 systems stock", "b5 systems"], None, why="B5 Enhanced SOPMOD tus nincs (csak Bravo és AK tus)",
                 near=r"b5 systems .*stock"),
    "b5-prec": R(["b5 systems stock", "b5 systems"], None, why="B5 Precision tus nincs (csak Bravo és AK tus)",
                 near=r"b5 systems .*stock"),
    # ---- tustalpak
    "mp-pad030": R(["magpul buttpad", "magpul rubber butt"], None,
                   why="a 0.30\"-es (alap) Rubber Butt-Pad nincs; csak Extended (0.55\") és Enhanced (0.70\")",
                   near=r"^magpul (extended|carbine stock enhanced) rubber buttpad"),
    "mp-pad055": R(["magpul buttpad", "magpul rubber butt"], r"magpul extended rubber buttpad" + C,
                   note="Extended Rubber Butt-Pad (0.55\")"),
    "mp-pad070": R(["magpul enhanced butt", "magpul buttpad"], r"magpul carbine stock enhanced rubber buttpad 0\.70 inch",
                   note="a szín a cikkszámból (MAG317-BLK)"),
    "mp-padsl": R(["magpul enhanced butt", "magpul buttpad"], r"magpul moe sl enhanced rubber buttpad 0\.70 inch" + C),
    # ---- red dot / holo
    "ap-t2": R("aimpoint micro t-2", r"aimpoint micro t-2 2 moa & 39mm lrp picatinny mount",
               note="Micro T-2 2 MOA + LRP szerelék 39 mm-es távtartóval (Lower 1/3); a 33 mm-es és a "
                    "standard szerelékes csomag más termék"),
    "ap-m5": R(["aimpoint comp m5", "aimpoint compm5"], r"aimpoint comp m5 2 moa & 39mm lrp mount red dot reflex sight",
               note="CompM5 2 MOA + 39 mm-es LRP szerelék; a 'Standard Mount' és a szerelék nélküli más termék"),
    "ap-pro": R("aimpoint pro", r"aimpoint pro - 2 moa",
                note="a bolti leírás szerint QRP2 Picatinny szerelékkel és AR15/M4 távtartóval (Aimpoint 12841)"),
    "ap-acro": R("aimpoint acro p-2", r"aimpoint acro p-2 sight & b&t asm 23mm mount" + C,
                 note="ACRO P-2 + B&T ASM 23 mm QD Picatinny/NAR szerelék (szett); a pontméret a bolti oldalon "
                      "nincs megadva"),
    "eo-exps3": R(["eotech exps3", "eotech exps3-0"], r"eotech exps3-0 holosight" + C,
                  note="az EXPS3-1/-2/-4/-DCR más retikül/típus"),
    "eo-exps2": R("eotech exps2", r"eotech exps2-0 holosight" + C, note="a zöld (GRN) és az EXPS2-2 más termék"),
    "eo-xps2": R(["eotech xps2", "eotech xps2-0"], r"eotech xps2-0 holosight" + C,
                 note="a zöld (GRN) és az XPS2-2 más termék"),
    "hs-510c": R("holosun 510c", r"holosun hs510c (?:solar red )?circle dot sight(?:" + C + ")?",
                 note="a HE510C-GR (Elite, zöld) és a HM3X-kombó más termék"),
    "hs-aems": R("holosun aems", None, why="az eredeti 'AEMS' nincs, csak AEMS EVO / Core / PRO / Macro / Max (más modellek)",
                 near=r"^holosun aems"),
    "hs-403b": R(["holosun 403b", "holosun hs403b"], None, why="a HS403B nincs, csak a HE403B-GR Elite (zöld, más modell)",
                 near=r"holosun he403b"),
    "tj-mro": R(["trijicon mro", "trijicon mro hd"], None,
                why="'MRO HD + Lower 1/3 szerelék' csomag nincs: az MRO HD szerelék nélkül, a Lower 1/3 szerelékes "
                    "csomagok pedig a nem-HD MRO-val szerepelnek",
                near=r"trijicon mro (hd 1x25 2\.0 moa red dot$|1x25 2\.0 moa red dot lower)"),
    "vx-uh1": R(["vortex uh-1", "vortex uh-1 gen ii"], r"vortex optics amg uh-1 gen ii holographic sight" + C),
    "vx-sparc": R(["vortex sparc", "vortex sparc ar"], r"vortex optics sparc ar red dot led upgrade" + C,
                  note="SPARC AR (SPC-AR2) – a gyári csomagban magasító távtartóval; a bolti leírás ezt nem részletezi"),
    "vx-crossfire": R("vortex crossfire red dot", None,
                      why="bizonytalan: az eredeti 'Crossfire 2 MOA Red Dot' csak Tan színben és magasító említése nélkül "
                          "szerepel; a 'Crossfire II' (Lower 1/3 szerelékkel) újabb, másik modell",
                      near=r"vortex optics crossfire"),
    # ---- LPVO
    "vx-se16": R(["vortex strike eagle 1-6x24", "strike eagle 1-6"], None, why="a Strike Eagle 1-6x24 nincs a boltban",
                 near=r"vortex optics strike eagle 1-8x24"),
    "vx-se18": R("vortex strike eagle 1-8x24", r"vortex optics strike eagle 1-8x24 riflescope ar-bdc3 reticle - moa",
                 note="AR-BDC3 retikulos SFP kivitel; az FFP EBR-8 külön termék"),
    "vx-pst16": R("vortex viper pst 1-6x24", r"vortex optics viper pst gen ii 1-6x24 sfp riflescope vmr-2 (moa|mrad)",
                  note="két retikulos kivitel (VMR-2 MOA / MRAD) azonos áron; a data.js nem határozza meg"),
    "vx-razor110": R("vortex razor 1-10x24", r"vortex optics razor hd gen iii 1-10x24 ffp riflescope ebr-9 (bdc moa|mrad)",
                     note="két retikulos kivitel (EBR-9 MOA / MRAD); a fő ár a (most akciós) MOA-é"),
    "tj-acog": R("trijicon acog ta31",
                 r"trijicon ta31(?:f|h|-ch|-ch-g)? 4x32 acog [a-z ]+ \.223 & ta51 mount(?:" + C + ")?",
                 note="TA31 4x32 .223 + TA51 szerelék; több retikül-változat (a data.js nem határozza meg), a fő ár a "
                      "legolcsóbbé; a RMR-kombók, a 300 BLK és a szerelék nélküli változatok nem kerültek be"),
    "tj-credo16": R("trijicon credo", r"trijicon credo 1-6x24 sfp red bdc segmented circle reticle riflescope",
                    note="SFP kivitel (a data.js szerint SFP); az FFP MRAD külön termék"),
    "tj-vcog": R("trijicon vcog", None, why="VCOG nincs a boltban (csak hozzá való szerelékek)", near=r"vcog"),
    "eo-vudu16": R("eotech vudu 1-6x24", r"eotech vudu 1-6x24 sr[123] ffp [a-z0-9. ]+" + C,
                   note="FFP kivitel, több retikül (SR1/SR2/SR3, piros/zöld) azonos áron; a 'Vudu X 1-6x24 SFP' másik modell"),
    "eo-vudu110": R("eotech vudu 1-10x28", r"eotech vudu 1-10x28 (?:sr4|sr5|le5) ffp (?:moa|mrad)" + C,
                    note="FFP, több retikül (SR4/SR5/LE5); a fő ár a legolcsóbb fekete kiviteé"),
    # ---- szerelék
    "gs-sp30": R("geissele super precision", r"geissele super precision scope mount 30mm ar15 1\.93 inch" + C,
                 note="30 mm, 1.93\" magasság"),
    "vx-cant30": R("vortex cantilever", r"vortex optics sport cantilever mount 30mm 1\.5 inch height"),
    # ---- nagyítók
    "eo-g33": R(["eotech g33", "eotech g33.sts"], r"eotech g33\.sts magnifier" + C, note="G33 + STS szerelék (G33.STS)"),
    "eo-g43": R("eotech g43", None, why="G43 + 'Micro' szerelék csomag nincs: a bolt a G43-at STS szerelékkel (G43.STS) árulja",
                near=r"eotech g43 magnifier"),
    "ap-3x": R("aimpoint 3xmag", r"aimpoint 3xmag-1 magnifier & (?:30|39)mm flipmount & twistmount base",
               note="3XMag-1 + FlipMount (30 vagy 39 mm-es) + TwistMount talp, azonos áron"),
    "hs-hm3x": R("holosun hm3x", r"holosun hm3x (?:3x micro )?magnifier" + C,
                 note="a HM3XT Elite és a kombók más termékek"),
    "vx-m3x": R(["vortex micro 3x", "vortex magnifier"], r"vortex optics micro 3x magnifier & flip mount",
                note="Micro 3X + Flip Mount (V3XM)"),
    # ---- BUIS (szett nincs, az árat nem adjuk össze)
    "mp-mbus3": R("magpul mbus", None, why=SET_BUIS, near=r"^magpul mbus 3 (front|rear)"),
    "mp-mbuspro": R("magpul mbus", None, why=SET_BUIS, near=r"^magpul mbus pro (front|rear) flip up sight"),
    "mp-mbusoff": R("magpul mbus", None, why=SET_BUIS, near=r"^magpul mbus pro offset (front|rear) sight"),
    # ---- lámpák
    "sf-m600": R(["surefire m600", "surefire scout light"], None,
                 why="M600DF nincs a boltban (csak M640 és M340/M140 Scout Light Pro változatok)",
                 near=r"surefire m640df dual fuel"),
    "sf-m640": R(["surefire m640", "surefire scout light"], r"surefire m640df dual fuel scout light pro" + C,
                 note="az M640DFT (Turbo), M640U, M640T és M640V más típusok"),
    "sf-m300": R(["surefire m300", "surefire scout light"], None,
                 why="M300 nincs a boltban (csak M340 / M140 Mini/Micro Scout Light Pro)", near=r"surefire m340c mini"),
    "sf-x300": R("surefire x300", r"surefire x300u-b led light" + C),
    "sl-hlx": R(["streamlight protac rail mount", "streamlight hl-x"], r"streamlight protac rail mount hl-x" + C,
                note="az 'HL-X Pro System' változatok más termékek"),
    "sl-tlr1": R("streamlight tlr-1", r"streamlight tlr-1 hl tactical led light" + C,
                 note="a 'Long Gun Kit' és az 'Earless Screw' változat más termék"),
    # ---- M-LOK
    "mp-rvg": R("magpul rvg", None,
                why="az 'M-LOK RVG' függőleges markolat nincs; a 'MOE RVG Rail Vertical Grip' Picatinny-s, a 'RVG M-LOK "
                    "Adapter Rail' csak sín", near=r"^magpul (moe rvg rail vertical grip - black|rvg m-lok adapter rail)"),
    "mp-hstop": R("magpul hand stop", r"magpul m-lok hand stop kit" + C, note="az 'XTM Hand Stop Kit' más termék"),
    "bcm-vg3": R("bcm vertical grip", r"bcm gunfighter vertical grip mod 3 m-lok" + C,
                 note="M-LOK kivitel (a KeyMod és Picatinny változat más termék)"),
    "bcm-kag": R("bcm kag", r"bcm gunfighter kag m-lok" + C, note="M-LOK kivitel"),
    "mp-rail5": R("magpul m-lok rail", r"magpul m-lok aluminium rail 5 slot", note="a szín a cikkszámból (MAG581-BLK)"),
    "mp-qd": R(["magpul m-lok qd", "magpul m-lok rail"], r"magpul m-lok qd sling mount" + C),
    "mp-cover": R(["magpul rail cover", "magpul m-lok rail"], r"magpul m-lok rail cover type 2" + C,
                  note="a 'Half Slot' és a Type 1 más termék"),
    "mp-cable": R(["magpul cable", "magpul cable management"], None, why="Magpul M-LOK Cable Management Kit nincs a boltban"),
    # ---- bipod
    "mp-bipod": R("magpul bipod", r"magpul bipod m-lok" + C,
                  note="Magpul Bipod for M-LOK (MAG933); a MOE / MOE QD bipod más termék"),
    "hr-sbrm": R(["harris bipod", "harris s-brm"], r"harris s-brm m-lok 6-9 inch rotating bipod",
                 note="S-BRM 6-9\", közvetlen M-LOK rögzítéssel (S-BRM-MLOK)"),
    # ---- torkolat
    "sf-warcomp": R("surefire warcomp", r"surefire warcomp (?:ctn )?556 1/2-28 flash hider",
                    note="1/2-28; a nyitott és a zárt (CTN) ágú változat azonos áron"),
    "sf-3p": R("surefire sf3p", r"surefire sf3p 556 1/2x28 flash hider"),
    "bcm-comp0": R("bcm compensator", r"bcm gunfighter compensator mod0 5\.56 / \.223"),
    "sf-rc2": R("surefire socom", None, why="a SOCOM556-RC2 hangtompító (+ WarComp) nincs a boltban",
                near=r"surefire socom"),
    # ---- elsütők
    "gs-ssae": R("geissele ssa", r"geissele super semi-automatic enhanced ssa-e trigger",
                 note="a 'Large Pin' és az 'SSA-E X' más termék"),
    "gs-ssa": R("geissele ssa", r"geissele super semi-automatic ssa trigger",
                note="a 'Large Pin' és az 'SSA X' más termék"),
    "gs-sde": R("geissele sd-e", r"geissele super dynamic enhanced trigger sd-e"),
    "gs-s3g": R("geissele s3g", r"geissele ar15 super 3 gun s3g trigger", note="a 'Large Pin' más termék"),
    # ---- felhúzókar
    "rd-raptor": R("radian raptor", r"radian raptor ambidextrous charging handle ar15" + C),
    "rd-raptorlt": R("radian raptor", r"radian raptor-lt ambidextrous charging handle ar15" + C),
    "rd-raptorsd": R("radian raptor", r"radian raptor-sd ambidextrous charging handle ar15" + C),
    "bcm-ch4": R("bcm charging handle", None,
                 why="bizonytalan: a bolti 'Gunfighter Charging Handle … Large Latch' neve Mod 4-et (nagy kar), a "
                     "cikkszáma (BCM-GFH-MOD3B-556) viszont Mod 3-at jelez", near=r"bcm gunfighter charging handle"),
    "bcm-ch3": R("bcm charging handle", None,
                 why="bizonytalan: a bolti 'Gunfighter Charging Handle … Large Latch' cikkszáma Mod 3B, a neve viszont "
                     "nagy (Mod 4) kart ír", near=r"bcm gunfighter charging handle"),
    "gs-ach": R("geissele charging handle", r"geissele airborne charging handle ach 5\.56" + C,
                note="a DDC színű és az URGI változat külön termék"),
    # ---- kezelőszervek
    "rd-talon": R("radian talon", None,
                  why="csak 'Kit' kiszerelés van (2-karos R0018 / 4-karos R0013) – a data.js tétel kiszerelése nem "
                      "azonosítható biztosan; a 'Talon-GI' más termék",
                  near=r"radian talon ambidextrous 45/90 safety selector"),
    "mp-bad": R("magpul b.a.d. lever", r"magpul b\.a\.d\. lever"),
    "mp-trigguard": R(["magpul trigger guard", "magpul moe trigger guard"], None,
                      why="a 'MOE Enhanced Trigger Guard' név nem szerepel; a 'Polymer Enhanced Trigger Guard' (MAG1186) "
                          "nem azonosítható biztosan azonosnak", near=r"magpul polymer enhanced trigger guard"),
    # ---- tárak
    "mp-pm10": R("magpul pmag 10", r"magpul pmag 10 ar/m4 gen m3 5\.56 magazine" + C),
    "mp-pm20": R("magpul pmag 20", r"magpul pmag 20 ar/m4 gen m3 5\.56 magazine" + C),
    "mp-pm30": R("magpul pmag 30", r"magpul pmag 30 ar/m4 gen m3 5\.56 magazine" + C),
    "mp-pm30w": R(["magpul pmag 30", "magpul pmag 30 window", "magpul pmag 40"],
                  r"magpul pmag 30 ar/m4 gen m3 window 5\.56 magazine" + C),
    "mp-pm40": R("magpul pmag 40", r"magpul pmag 40 ar/m4 gen m3 5\.56 magazine" + C),
    "mp-d60": R(["magpul d-60", "magpul d60", "magpul drum"], None, why="a PMAG D-60 dobtár nincs a boltban",
                near=r"magpul pmag d-50"),
    # ---- szíjak
    "mp-ms1": R("magpul ms1", r"magpul ms1 sling" + C, note="a Lite / Padded / QDM változat más termék"),
    "mp-ms4": R("magpul ms4", r"magpul ms4 dual qd sling gen 2" + C, note="Gen 2 kivitel; az 'MS4 QDM' más termék"),
    "bfg-vickers": R("blue force gear vickers", r"blue force gear 2 point vickers combat applications sling" + C,
                     note="2-pontos, nem párnázott kivitel; a 'Padded' és a 'Push Button' / '221' változat más termék"),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás, stdout-ra)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/tacstore.json)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["id"] in wanted]
    http = Http(HOSTS, args.cache)
    rules = {e["id"]: RULES.get(e["id"]) or R("%s %s" % (e["brand"], e["name"]),
                                              re.escape(norm("%s %s" % (e["brand"], e["name"]))))
             for e in entries}
    queries = []
    for r in rules.values():
        for q in r["q"]:
            if q not in queries:
                queries.append(q)
    items, unmatched, blocked, cands = {}, {}, None, []
    for i, q in enumerate(queries, 1):
        sys.stderr.write("[%d/%d] keresés: %s\n" % (i, len(queries), q))
        try:
            cands += ts_search(http, q)
        except Blocked as e:
            blocked = str(e)
            break
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("   HIBA: %s\n" % e)
    for e in entries:
        pid = e["id"]
        if blocked:
            unmatched[pid] = "ok: nem ellenőrizve – a bolt blokkolta a lekérést (%s)" % blocked
            continue
        kind, res = decide(e, rules[pid], cands)
        if kind == "ok":
            items[pid] = res
            sys.stderr.write("%-14s -> %s  CHF %s\n" % (pid, res["title"], res["price_chf"]))
        else:
            unmatched[pid] = res
    if blocked:
        items = {}
    out = args.out or (None if args.only else OUT_JSON)
    write_output(out, SHOP, items, unmatched, blocked,
                 extra=dict(source=BASE + "/catalog/all-products?keywords=… (kereső-lista beágyazott JSON-ja)",
                            price_basis="kiírt fogyasztói ár CHF, 8.1% ÁFÁ-val (AGB 1.); akciónál price_chf = akciós "
                                        "ár, list_chf = áthúzott ár",
                            stock_basis="in_stock=true csak 'Umgehend versandbereit' (saját raktár); egyébként "
                                        "false, a pontos szöveg a stock_text-ben"))
    sys.stderr.write("Kész: %d egyezés, %d unmatched, %d HTTP kérés%s\n" % (
        len(items), len(unmatched), http.n, (", BLOCKED: " + blocked) if blocked else ""))


if __name__ == "__main__":
    main()
