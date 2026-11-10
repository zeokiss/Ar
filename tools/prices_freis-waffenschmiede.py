#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
freis-waffenschmiede.ch (Freis Waffenschmiede, Wittenbach SG) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_freis-waffenschmiede.py                  # minden termék -> assets/prices/freis-waffenschmiede.json
    python3 tools/prices_freis-waffenschmiede.py --only vx-uh1    # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_freis-waffenschmiede.py --cache /tmp/fwc # HTTP-válaszok gyorsítótára (fejlesztéshez)
    python3 tools/prices_freis-waffenschmiede.py --catalog katalogus.json
        # a TELJES kínálat (~600 termékoldal, ~13 perc) letöltése: id -> név, ár; a RULES frissítéséhez

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés (JSF/PrimeFaces webshop, nincs keresőfunkció és nincs JSON-végpont)
  1. A bolt /sitemap.xml-jéből (a robots.txt engedi) kiolvassa a termék-azonosítókat
     (/de/shop/products/<id>); a robots.txt csak a /de/shop/products* útvonalakat és a sitemap.xml-t
     engedi, ezért a script mást nem kér le (a /de/sitemap és /de/shop/cart oldalakat nem).
  2. A RULES táblában id-nként: `ids` = a bolti termék azonosítója(i) (a teljes kínálat 2026-11-10-i
     átvizsgálásából), `title` = a termékoldal nevének PONTOS (fullmatch) mintája, opcionálisan
     `desc` (a leírásban kötelező minta), `colors` = {szín-kulcs: bolti id}. A script ellenőrzi,
     hogy az id még létezik, és a név/leírás egyezik-e; csak akkor vesz fel egyezést.
  3. Nincs pontos egyezés -> "unmatched" okkal + a legközelebbi bolti termékek (név, ár, URL).
     Szabály nélküli id: "nincs a boltban" (a 2026-11-10-i teljes kínálat alapján; új termékekhez
     futtasd a --catalog módot, és bővítsd a RULES-t).
  Árat soha nem becsül, nem számol, nem vált át. A bolt nem ír ki készletet -> in_stock = null.

Udvariasság: egy szálon, kérések között >= 1,2 s, böngésző User-Agent; csak a bolt hostjára kérdez.
403 / tartós 429 / Cloudflare-kihívás esetén azonnal leáll ("blocked"), nem próbál megkerülni semmit.
"""
import argparse
import datetime
import hashlib
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

SHOP = "freis-waffenschmiede.ch"
SLUG = "freis-waffenschmiede"
BASE = "https://freis-waffenschmiede.ch"
HOSTS = ("freis-waffenschmiede.ch", "www.freis-waffenschmiede.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
LANG = "de-CH,de;q=0.9,en;q=0.5"
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", SLUG + ".json")


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

    def get(self, url):
        host = urllib.parse.urlparse(url).netloc
        if host not in HOSTS:
            raise ValueError("nem engedélyezett host: " + host)
        ck = None
        if self.cache_dir:
            ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest())
            if os.path.exists(ck):
                with open(ck, "rb") as f:
                    return f.read().decode("utf-8", "replace")
        headers = {"User-Agent": UA, "Accept-Language": LANG,
                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        last_err = None
        for attempt in range(3):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                if re.search(r"cf-chl|challenge-platform|Just a moment\.\.\.|Verifying (you are human|connection)",
                             body[:20000]):
                    raise Blocked("botvédelmi kihívás (%s)" % url)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise Blocked("HTTP 403 (%s)" % url)
                if e.code == 429:
                    if attempt == 0:
                        time.sleep(30)
                        continue
                    raise Blocked("tartós HTTP 429 (%s)" % url)
                if e.code in (404, 410):
                    return None
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


def norm(s):
    s = re.sub(r"[®™©]", "", s or "")
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def prices_in(text):
    return [float(re.sub(r"[’'\s]", "", x)) for x in re.findall(r"\d[\d’' ]*\.\d{2}", norm(text))]


# --------------------------------------------------------------------------- szabályok
def R(ids=None, title=None, why=None, near=None, colors=None, desc=None, note=None):
    """ids: bolti termék-azonosító(k) (jelöltek); title: a termékoldal nevének mintája (fullmatch, kisbetű-
    független) - None: a bolt biztosan nem árulja pontosan ugyanezt; near: tájékoztató 'legközelebbi'
    bolti azonosítók; colors: {szín-kulcs: bolti id}; desc: a leírásban kötelező minta; note: megjegyzés."""
    return dict(ids=ids or [], title=title, why=why, near=near or [], colors=colors or {}, desc=desc, note=note)


# A teljes kínálat (600 termékoldal) átvizsgálása 2026-11-10-én. Vadász/sport + biztonsági bolt;
# AR-15 tartozékból néhány Magpul tár/bipod, Radian Raptor LT, AMG UH-1 Gen II és más márkák vannak.
RULES = {
    # ---- tárak (a bolti nevek nem írják ki a színt; a leírás "GEN M3")
    "mp-pm10": R([405], r"MAGPUL AR 15 Magazin 10 Schuss Kaliber \.223 Rem", desc=r"GEN M3",
                 near=[403],
                 note="a bolti oldal nem nevezi meg a színt; a név nem írja ki a 'PMAG' szót, de a leírás szerint 'GEN M3' Magpul AR15 tár; Bewilligung nélkül vásárolható"),
    "mp-pm20": R([406], r"MAGPUL AR 15 Magazin ohne Fenster 20 Schuss Kaliber \.223 Rem\.", desc=r"GEN M3",
                 why="a 20-as Magpul tár oldalán (id 406) nincs kiírt ár"),
    "mp-pm30": R([404], None, near=[404, 816, 402],
                 why="sima (ablak nélküli) fekete PMAG 30 Gen M3 nincs a boltban; csak 'Sand' színű 30-as (36 CHF), a szín nem azonosítható biztosan FDE-ként"),
    "mp-pm30w": R([402], r"MAGPUL AR 15 Magazin mit Fenster 30 Schuss Kaliber \.223 Rem\.", desc=r"GEN M3",
                  note="a bolti oldal nem nevezi meg a színt; a név nem írja ki a 'PMAG' szót, de a leírás szerint 'GEN M3' ablakos 30-as tár"),
    "mp-pm40": R(None, None, why="a PMAG 40 Gen M3 nincs a boltban"),
    # ---- bipod
    "mp-bipod": R([512], r"Magpul M-Lock BLK", desc=r"M-Lock", colors={"black": 512, "fde": 513},
                  near=[509, 510, 511],
                  note="Magpul M-LOK bipod (6.3-10.3\"); a QD / Picatinny rögzítésű változatok külön termékek"),
    "hr-sbrm": R(None, None, near=[573], why="Harris bipod nincs a boltban (csak 'M-Lock Adapter für Zweibein Harrys Style')"),
    # ---- felhúzókar
    "rd-raptorlt": R([300], r"Radian Raptor LT für AR15"),
    "rd-raptor": R(None, None, near=[301, 300],
                   why="az AR15-ös Raptor nincs a boltban (csak Raptor LT AR15-höz és Raptor AR10-hez)"),
    # ---- optikák
    "vx-uh1": R([379], r"AMG UH-1 Gen II", desc=r"CR123",
                note="bolti név: 'AMG UH-1 Gen II' (a Vortex AMG UH-1 Gen II; a leírás: 1 MOA pont, CR123, 311 g); a márka a bolti oldalon nincs kiírva"),
    "vx-sparc": R(None, None, near=[381],
                  why="csomag ('SPARC AR + magasító'): a bolt a 'SPARC AR GenII'-t árulja, a magasító szerelék-összetétel és a generáció nem azonosítható biztosan"),
    "vx-m3x": R(None, None, near=[382], why="a Vortex Micro3X + Flip nincs a boltban (csak 'VMX-3T', más típusjelzés)"),
    "hs-hm3x": R([693], r"Holosun HM3X", why="a HM3X oldalán (id 693) nincs kiírt ár"),
    "hs-403b": R(None, None, near=[694, 549], why="a HS403B nincs a boltban (csak HS403C és HS407K X2)"),
    "eo-xps2": R(None, None, near=[358], why="az XPS2-0 nincs a boltban (csak XPS3-2)"),
    "eo-exps3": R(None, None, near=[358], why="az EXPS3-0 nincs a boltban"),
    # ---- markolatok / M-LOK
    "mp-moe": R(None, None, near=[539],
                why="a 'Pistolengriff MOE' (id 539) oldalán a márka és a típus nincs kiírva (csak 'Pistolengriff AR-10/15'), nem azonosítható biztosan Magpul MOE Grip-ként"),
    "mp-rvg": R(None, None, near=[538, 14, 765, 766],
                why="az 'M-LOK RVG' nincs a boltban; a 'Vorderschaft Vertikal Griff RVG/MVG' oldalakon a márka nincs kiírva"),
    "mp-qd": R(None, None, near=[299], why="a Magpul M-LOK QD nincs a boltban; a 'QD Sling Adapter' (id 299) márkája nincs kiírva"),
    "dd-v7": R(None, None, near=[715], why="a DDM4 V7 nincs a boltban (csak a 'Daniel Defense H9' pisztoly)"),
}


# --------------------------------------------------------------------------- data.js
def read_data_js():
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    out = []
    for m in re.finditer(r"\{\s*id:\s*'([^']+)'([^{}]*)\}", src):
        body = m.group(2)
        b = re.search(r"\bbrand:\s*'((?:[^'\\]|\\.)*)'", body)
        n = re.search(r"\bname:\s*'((?:[^'\\]|\\.)*)'", body)
        if b and n:
            out.append((m.group(1), b.group(1).replace("\\'", "'"), n.group(1).replace("\\'", "'")))
    return out


# --------------------------------------------------------------------------- oldalak
def product_ids(http):
    xml = http.get(BASE + "/sitemap.xml") or ""
    return sorted(set(int(x) for x in re.findall(r"/de/shop/products/(\d+)", xml)))


def parse_product(html, pid):
    s = soup(html)
    h1 = s.select_one("h1.seo-headline")
    pp = s.select_one(".price-panel")
    nums = prices_in(pp.get_text(" ")) if pp else []
    price = nums[0] if nums else None
    lst = nums[1] if len(nums) > 1 and nums[1] > nums[0] else None
    d = s.select_one(".product-detail-information .shop-element-description") or s.select_one(".shop-element-description")
    im = s.select_one(".product-detail-image")
    m = re.search(r"url\('([^']+)'\)", im.get("style", "")) if im else None
    return dict(id=pid, title=norm(h1.get_text(" ")) if h1 else None, price_chf=price, list_chf=lst,
                desc=norm(d.get_text(" ")) if d else "", image=(BASE + m.group(1)) if m else None,
                url="%s/de/shop/products/%d" % (BASE, pid))


def fmt_near(infos):
    return " | ".join("%s (%s, %s)" % (i["title"], ("%.2f CHF" % i["price_chf"]) if i["price_chf"] is not None else "ár nincs kiírva",
                                       i["url"]) for i in infos[:4])


def decide(http, pid, brand, name, rule, known, pages):
    def page(i):
        if i not in known:
            return None
        if i not in pages:
            h = http.get("%s/de/shop/products/%d" % (BASE, i))
            pages[i] = parse_product(h, i) if h else None
        return pages[i]

    if not rule:
        return "no", "ok: nincs a boltban (a 2026-11-10-i teljes kínálat átvizsgálása nem hozott egyező terméket)"
    near = [p for p in (page(i) for i in rule["near"]) if p]
    why = rule["why"]

    def unmatched(reason):
        txt = "ok: " + reason
        if near:
            txt += "; legközelebbi bolti termék(ek), NEM azonos: " + fmt_near(near)
        return "no", txt

    if not rule["title"] or not rule["ids"]:
        return unmatched(why or "nincs a boltban (nincs pontosan azonos termék)")
    cands = [p for p in (page(i) for i in rule["ids"]) if p]
    hits = [p for p in cands if p["title"] and re.fullmatch(rule["title"], p["title"], re.I)]
    if not hits:
        return unmatched("a bolti termék (id %s) már nem létezik vagy a neve megváltozott" % rule["ids"])
    h = hits[0]
    if rule["desc"] and not re.search(rule["desc"], h["desc"], re.I):
        return unmatched("a termékleírás nem erősíti meg a típust ('%s' hiányzik): %s" % (rule["desc"], h["url"]))
    if h["price_chf"] is None:
        return unmatched(why or "nincs kiírt ár: " + h["url"])
    item = dict(title=h["title"], price_chf=h["price_chf"], list_chf=h["list_chf"], url=h["url"], image=h["image"],
                in_stock=None, stock_text="", sku=str(h["id"]), mpn=None, weight_g=None, dims=None,
                colors_prices={}, match="exact")
    for key, cid in rule["colors"].items():
        c = page(cid)
        if c and c["price_chf"] is not None:
            item["colors_prices"][key] = dict(price_chf=c["price_chf"], list_chf=c["list_chf"], url=c["url"],
                                              in_stock=None, title=c["title"])
    if rule["note"]:
        item["note"] = rule["note"]
    return "ok", item


def build_catalog(http, known, out_path):
    cat = {}
    for n, i in enumerate(sorted(known), 1):
        h = http.get("%s/de/shop/products/%d" % (BASE, i))
        if h:
            p = parse_product(h, i)
            cat[str(i)] = dict(title=p["title"], price_chf=p["price_chf"], list_chf=p["list_chf"])
        if n % 50 == 0:
            sys.stderr.write("  %d/%d\n" % (n, len(known)))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    sys.stderr.write("katalógus: %s (%d termék)\n" % (out_path, len(cat)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/%s.json; --only mellett stdout)" % SLUG)
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    ap.add_argument("--catalog", help="a teljes kínálat letöltése ebbe a JSON-fájlba, majd kilép")
    args = ap.parse_args()
    http = Http(args.cache)
    known = set(product_ids(http))
    sys.stderr.write("sitemap: %d termék\n" % len(known))
    if args.catalog:
        build_catalog(http, known, args.catalog)
        return
    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    items, unmatched, pages, blocked = {}, {}, {}, None
    for i, (pid, brand, name) in enumerate(entries, 1):
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), pid, brand, name))
        if blocked:
            unmatched[pid] = "ok: a bolt letiltotta a lekérdezést (%s), nem ellenőrizhető" % blocked
            continue
        try:
            kind, res = decide(http, pid, brand, name, RULES.get(pid), known, pages)
        except Blocked as e:
            blocked = str(e)
            sys.stderr.write("   BLOKKOLVA: %s - leállás\n" % e)
            unmatched[pid] = "ok: a bolt letiltotta a lekérdezést (%s), nem ellenőrizhető" % blocked
            continue
        except Exception as e:
            unmatched[pid] = "ok: hiba a lekérdezéskor, nem sikerült ellenőrizni (%s)" % e
            sys.stderr.write("   HIBA: %s\n" % e)
            continue
        if kind == "ok":
            items[pid] = res
            sys.stderr.write("   -> %s  CHF %s\n" % (res["title"], res["price_chf"]))
        else:
            unmatched[pid] = res
            sys.stderr.write("   -> unmatched\n")
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
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
