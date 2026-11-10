#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
armurerie-lesdix.ch (Armurerie Les Dix SA, Sion) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_armurerie_lesdix.py                  # -> assets/prices/armurerie-lesdix.json
    python3 tools/prices_armurerie_lesdix.py --only mp-moek2p,mp-qd   # részleges futás (stdout)
    python3 tools/prices_armurerie_lesdix.py --cache /tmp/lesdix_cache

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  * A bolt PrestaShop 1.7. A kategória-listázás AJAX-válasza (GET, ?ajax=1) JSON-ban adja a
    termékeket (név, gyártó, ár, eredeti ár, kép, URL). A gyökérkategória
    (/fr/2-accueil, 100 termék/oldal, ~7 kérés) a teljes katalógust tartalmazza.
  * A js/data.js minden azonosítójához a RULES táblában van a bolti terméknév PONTOS
    (fullmatch, kis/nagybetű-független) mintája; színváltozatnál színenként külön minta
    (a bolt a színeket külön termékként listázza: "... - Noir", "... - Gris").
  * Egyezés esetén a termékoldalt is letölti (data-product JSON: készlet-állapot és -szöveg).
  * Ha nincs pontos egyezés: "unmatched" okkal + a 'near' mintára illeszkedő bolti
    termékek (név, ár, URL – "NEM azonos").
  * Árat soha nem becsül, nem számol, nem vált át.

Udvariasság: egyetlen szál, kérések között >= 1,2 s, böngésző User-Agent, csak
armurerie-lesdix.ch-ra kérdez; nincs bejelentkezés/kosár. 403 / captcha / tartós 429
esetén leáll ("blocked").
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
import urllib.request

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("Hiányzik a beautifulsoup4: pip install beautifulsoup4 lxml")

BASE = "https://armurerie-lesdix.ch"
LIST_URL = BASE + "/fr/2-accueil?ajax=1&resultsPerPage=100&page=%d"
SHOP = "armurerie-lesdix.ch"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "armurerie-lesdix.json")


# --------------------------------------------------------------------------- HTTP
class Blocked(Exception):
    pass


class Http:
    def __init__(self, cache_dir=None):
        self.cache_dir = cache_dir
        self.last = 0.0
        self.n = 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def get(self, url):
        if not url.startswith(BASE + "/"):
            raise ValueError("nem engedélyezett host: " + url)
        ck = None
        if self.cache_dir:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, "rb") as f:
                    return f.read().decode("utf-8", "replace")
        last_err = None
        for attempt in range(3):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": UA, "Accept-Language": "fr-CH,fr;q=0.9,en;q=0.5"})
                with urllib.request.urlopen(req, timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                if re.search(r"cf-challenge|Verifying (you are human|connection)|g-recaptcha|hcaptcha", body[:8000], re.I):
                    raise Blocked("botvédelmi oldal: " + url)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise Blocked("HTTP 403: " + url)
                if e.code == 429:
                    if attempt == 2:
                        raise Blocked("tartós HTTP 429: " + url)
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


# --------------------------------------------------------------------------- segédek
def norm(s):
    s = htmllib.unescape(s or "")
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def text_of(markup):
    return norm(re.sub(r"<[^>]+>", " ", markup or ""))


def read_data_js():
    """(id, márka, név) a BASES és ITEMS literál objektumaiból."""
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    out = []
    for m in re.finditer(r"\{\s*id:\s*'([^']+)'", src):
        seg = src[m.start():src.find("}", m.start())]
        b = re.search(r"\bbrand:\s*'((?:[^'\\]|\\.)*)'", seg)
        n = re.search(r"\bname:\s*'((?:[^'\\]|\\.)*)'", seg)
        if b and n:
            out.append((m.group(1), b.group(1).replace("\\'", "'"), n.group(1).replace("\\'", "'")))
    return out


# --------------------------------------------------------------------------- szabályok
def R(colors=None, main=None, why=None, near=None, need=None, note=None):
    """colors: {data.js színkulcs: bolti név regex}; main: bolti név regex (szín nélkül);
    why: indok, ha nincs egyezés; near: regex a 'legközelebbi' bolti termékekhez;
    need: regex, aminek a rövid leírásban szerepelnie kell; note: megjegyzés."""
    return dict(colors=colors or {}, main=main, why=why, near=near, need=need, note=note)


NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a terméket)"

RULES = {k: R(why=NOT_SOLD) for k in (
    "schm-s4f", "schm-m4fl", "schm-m5fl", "schm-dyn", "bcm-recce16",
    "mp-miad", "bcm-g0", "bcm-g3", "b5-t22", "b5-t23",
    "mp-ctr", "mp-moesl", "mp-moeslk", "mp-ubr2", "mp-prs3", "mp-prslite", "bcm-s0", "bcm-s1",
    "b5-bravo", "b5-esop", "b5-prec", "mp-pad030", "mp-pad055", "mp-pad070", "mp-padsl",
    "ap-t2", "ap-m5", "ap-pro", "ap-acro", "eo-exps3", "eo-exps2", "eo-xps2", "hs-510c", "hs-aems",
    "hs-403b", "tj-mro", "vx-uh1", "vx-sparc", "vx-crossfire",
    "vx-se16", "vx-se18", "vx-pst16", "vx-razor110", "tj-acog", "tj-credo16", "tj-vcog",
    "eo-vudu16", "eo-vudu110", "gs-sp30", "vx-cant30", "eo-g33", "eo-g43", "ap-3x", "hs-hm3x", "vx-m3x",
    "mp-mbus3", "mp-mbuspro", "mp-mbusoff", "sf-m600", "sf-m640", "sf-m300", "sf-x300", "sl-hlx", "sl-tlr1",
    "mp-rvg", "mp-hstop", "bcm-vg3", "mp-rail5", "mp-cover", "mp-cable", "hr-sbrm",
    "sf-warcomp", "sf-3p", "bcm-comp0", "sf-rc2", "gs-ssae", "gs-ssa", "gs-sde", "gs-s3g",
    "rd-raptor", "rd-raptorsd", "bcm-ch4", "bcm-ch3", "gs-ach", "rd-talon", "mp-bad", "mp-trigguard",
    "mp-pm10", "mp-pm20", "mp-pm30w", "mp-pm40", "mp-d60", "mp-ms1", "mp-ms4", "bfg-vickers")}

RULES.update({
    "dd-v7": R(why="csak 'DDM4 V7 ... BROWN' és 'DDM4 V7 16\" Multi-Cam OD Green/Black' kivitel van; "
                   "a data.js-ben a DDM4 V7 csak fekete (black) kivitelben szerepel",
               near=r".*DDM4 V7.*"),
    "mp-moe": R(why="a bolti 'MAGPUL MOE+ GRIP' címe MOE+ (gumírozott, más termék), a leírása viszont a sima MOE "
                    "markolatot írja – ellentmondásos, nem azonosítható biztosan",
                near=r"MAGPUL MOE\+? GRIP.*"),
    "mp-moek2": R(why="csak MOE K2+ van a boltban (más termék)", near=r"MAGPUL MOE K2\+ GRIP.*"),
    "mp-moek2p": R(colors={"black": r"MAGPUL MOE K2\+ GRIP – AR15/M4 - Noir",
                           "gray": r"MAGPUL MOE K2\+ GRIP – AR15/M4 - Gris"}),
    "mp-qd": R(main=r"MAGPUL M-LOK QD SLING MOUNT"),
    "bcm-kag": R(main=r"BCM Angle Grip M-Lock", need=r"Kinesthetic Angled Grip",
                 note="a leírás szerint BCM KAG (Kinesthetic Angled Grip), M-LOK; a bolt a színt nem jelöli"),
    "mp-bipod": R(why="csak 'MAGPUL M-LOK BIPOD MOUNT' (adapter Harris-típusú bipodhoz) van, maga a Magpul bipod nincs",
                  near=r"MAGPUL M-LOK BIPOD MOUNT"),
    "mp-pm30": R(why="csak PMAG 30 GEN 2 MOE (Gen M2) van, Gen M3 nincs", near=r"MAGPUL PMAG.*"),
    "rd-raptorlt": R(why="csak a Raptor-LT 7.62 AR10/SR25-ös változata van, az AR15-ös nincs",
                     near=r"RADIAN RAPTOR-LT.*"),
})


# --------------------------------------------------------------------------- katalógus
def fetch_catalog(http):
    products, page, pages = {}, 1, 1
    while page <= pages:
        d = json.loads(http.get(LIST_URL % page))
        pages = int((d.get("pagination") or {}).get("pages_count") or 1)
        for p in d.get("products") or []:
            products[p["id_product"]] = p
        page += 1
        if page > 50:
            break
    return list(products.values())


def list_price(p):
    price = float(p["price_amount"])
    reg = p.get("regular_price_amount")
    lst = float(reg) if p.get("has_discount") and reg and float(reg) > price else None
    return price, lst


def cover(p):
    c = p.get("cover") or {}
    by = c.get("bySize") or {}
    return (by.get("large_default") or {}).get("url") or c.get("large", {}).get("url")


def product_page(http, url):
    s_el = soup(http.get(url))
    det = s_el.select_one("#product-details[data-product]") or s_el.select_one("[data-product]")
    dp = {}
    if det:
        try:
            dp = json.loads(det["data-product"])
        except Exception:
            dp = {}
    h1 = s_el.select_one("h1")
    av = dp.get("availability")
    in_stock = True if av in ("available", "in_stock", "last_remaining_items") else (False if av else None)
    return dict(h1=norm(h1.get_text(" ")) if h1 else None, in_stock=in_stock,
                stock_text=norm(dp.get("availability_message")) or av or None,
                price=dp.get("price_amount"), reference=dp.get("reference") or None,
                mpn=dp.get("mpn") or None, weight=dp.get("weight"))


def record(http, p):
    price, lst = list_price(p)
    pg = product_page(http, p["url"])
    if pg["price"] is not None and abs(float(pg["price"]) - price) > 0.001:
        price = float(pg["price"])  # a termékoldal aktuális ára az irányadó
    w = None
    try:
        w = int(round(float(pg["weight"]) * 1000)) if pg["weight"] and float(pg["weight"]) > 0 else None
    except (TypeError, ValueError):
        w = None
    return dict(title=norm(p["name"]), price_chf=price, list_chf=lst, url=p["url"], image=cover(p),
                in_stock=pg["in_stock"], stock_text=pg["stock_text"], sku=pg["reference"],
                mpn=pg["mpn"], weight_g=w, dims=None, brand_shop=p.get("manufacturer_name") or None)


def decide(http, rule, catalog):
    def find(rx):
        return [p for p in catalog if re.fullmatch(rx, norm(p["name"]), re.I)
                and (not rule["need"] or re.search(rule["need"], text_of(p.get("description_short")), re.I))]

    item = None
    if rule["colors"]:
        cp = {}
        for col, rx in rule["colors"].items():
            hits = sorted(find(rx), key=lambda p: list_price(p)[0])
            if hits:
                r = record(http, hits[0])
                cp[col] = dict(price_chf=r["price_chf"], list_chf=r["list_chf"], url=r["url"],
                               in_stock=r["in_stock"], title=r["title"])
                if item is None:
                    item = dict(r, color=col)
        if item:
            item["colors_prices"] = cp
            item["match"] = "exact"
    elif rule["main"]:
        hits = sorted(find(rule["main"]), key=lambda p: list_price(p)[0])
        if hits:
            item = record(http, hits[0])
            item["match"] = "exact" if len(hits) == 1 else "variant"
            if len(hits) > 1:
                item["alternatives"] = [dict(title=norm(p["name"]), url=p["url"], price_chf=list_price(p)[0])
                                        for p in hits[1:]]
    if item:
        if rule["note"]:
            item["note"] = rule["note"]
        return "ok", item
    reason = "ok: " + (rule["why"] or "nincs a boltban (a katalógusban nincs pontosan azonos termék)")
    if rule["near"]:
        near = [p for p in catalog if re.fullmatch(rule["near"], norm(p["name"]), re.I)]
        if near:
            reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(
                "%s (%.2f CHF, %s)" % (norm(p["name"]), list_price(p)[0], p["url"]) for p in near[:4])
    return "no", reason


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás, stdout)")
    ap.add_argument("--out", help="kimeneti JSON (alap: assets/prices/armurerie-lesdix.json)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    http = Http(args.cache)
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
               source=BASE + "/fr/2-accueil?ajax=1 (PrestaShop listázás, teljes katalógus) + termékoldalak",
               price_basis="a bolt kiírt ára CHF-ben, ÁFÁ-val (akciónál list_chf = eredeti ár)",
               items={}, unmatched={}, blocked=None)
    try:
        catalog = fetch_catalog(http)
        sys.stderr.write("Katalógus: %d termék\n" % len(catalog))
        for pid, brand, name in entries:
            rule = RULES.get(pid) or R(main=re.escape("%s %s" % (brand, name)))
            kind, res = decide(http, rule, catalog)
            (doc["items"] if kind == "ok" else doc["unmatched"])[pid] = res
            sys.stderr.write("%-14s %s\n" % (pid, ("CHF %s  %s" % (res["price_chf"], res["title"])) if kind == "ok" else "-"))
    except Blocked as e:
        doc["blocked"] = str(e)
        doc["items"], doc["unmatched"] = {}, {}
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    out = args.out or (None if args.only else OUT_JSON)
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("Kész: %s (%d egyezés, %d unmatched, %d HTTP kérés)\n" % (
            out, len(doc["items"]), len(doc["unmatched"]), http.n))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
