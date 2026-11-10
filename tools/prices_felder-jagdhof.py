#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
felder-jagdhof.ch (Felder Jagdhof AG, Entlebuch LU) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_felder-jagdhof.py                     # minden termék -> assets/prices/felder-jagdhof.json
    python3 tools/prices_felder-jagdhof.py --only schm-s4f     # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_felder-jagdhof.py --cache /tmp/fjc    # HTTP-válaszok gyorsítótára (fejlesztéshez)
    python3 tools/prices_felder-jagdhof.py --grep schmeisser   # a bolti termék-sitemap szűrése (szabályíráshoz)

Függőség: Python 3.8+, beautifulsoup4 nem kell (csak a standard könyvtár).

Működés
  A bolt Angular (SSR) alkalmazás; a keresés külső (Typesense) szolgáltatáson fut, amelyhez nem nyúlunk.
  A script a bolt saját, nyilvános termék-sitemapjét használja (robots.txt engedi):
    1. /sitemap.xml -> az "item-sitemap.xml" -> /shop/item/<slug> URL-ek (~11 600 tétel);
    2. id-nként a RULES-ban: `cand` (regex a slugra) a jelöltek szűkítésére, `title` (regex, fullmatch)
       a bolti terméknévre ("designation"), amit a termékoldal beágyazott `ng-state` JSON-jából olvasunk
       (ár, áthúzott ár, készlet, cikkszám, kép, engedélykötelezettség);
    3. csak a jelöltek oldalát töltjük le (id-nként legfeljebb MAX_CAND db), egyenként >= 1,2 s szünettel.
  Szabály nélküli id: a script csak a márka+típus alapján kiszűrt slugokat sorolja fel "legközelebbi,
  NEM azonos" tételként, egyezést nem állít.
  Nincs pontos egyezés -> "unmatched" okkal. Árat soha nem becsül, nem számol, nem vált át.
  A `canBeSold: false` (nem rendelhető) tételek nem kerülnek be az items-be.

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

SHOP = "felder-jagdhof.ch"
SLUG = "felder-jagdhof"
BASE = "https://felder-jagdhof.ch"
HOSTS = ("felder-jagdhof.ch", "www.felder-jagdhof.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
LANG = "de-CH,de;q=0.9,en;q=0.5"
MIN_GAP = 1.2
MAX_CAND = 6

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
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
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
                if e.code in (500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError("letöltés sikertelen: %s (%s)" % (url, last_err))


# --------------------------------------------------------------------------- segédek
def norm(s):
    s = re.sub(r"[®™©]", "", s or "")
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", norm(s).lower()).strip("-")


def R(cand, title=None, why=None, note=None):
    """cand: regex a bolti slugra (re.search) - a jelöltek szűkítése;
    title: regex (fullmatch, kisbetű-független) a bolti terméknévre - None: a bolt biztosan nem árulja
    pontosan ugyanezt (csomag, más kivitel, nincs a kínálatban); why: indok az unmatched-hez;
    note: megjegyzés a felvett tételhez."""
    return dict(cand=cand, title=title, why=why, note=note)


# --------------------------------------------------------------------------- szabályok
# A kínálat 2026-11-10-i átvizsgálása (11 641 sitemap-tétel): a bolt vadász/sport kínálatú; AR-15 tartozékból
# csak Schmeisser fegyverek (több "Falke"-reflexvizieres csomagban), Aimpoint és Holosun optikák vannak.
RULES = {
    "schm-s4f": R(r"schmeisser-ar15-s4f", r'Sturmgewehr Schmeisser AR15 S4F Black 10\.5" \.223 Rem\.?',
                  note="Standard S4F 10.5\" fekete; a 'Komplettset' / 'mit Falke' / 'Komplettpaket' tételek csomagok (nem azonosak). Akciós ár (áthúzott: gyári ajánlott ár)"),
    "schm-m4fl": R(r"schmeisser-ar15-m4fl", None,
                   why="a sima M4FL (fekete, 14.5\") nincs a boltban; csak 'Falke' reflexvizieres csomagok / Special Edition kivitelek"),
    "schm-m5fl": R(r"schmeisser-ar15-m5fl", None, why="az M5FL nincs a boltban"),
    "schm-dyn": R(r"schmeisser-ar15-dynamic", None,
                  why="csak a 'Dynamic inkl. Falke' (reflexvizieres csomag) van a boltban, a sima Dynamic M5FL nincs"),
    "hs-510c": R(r"holosun-hs510c", r"Leuchtpunktvisier Holosun HS510C"),
    "hs-403b": R(r"holosun-hs403b", None,
                 why="a HS403B + magasító csomag nem azonosítható biztosan, és a bolti adat szerint a HS403B nem megvásárolható (canBeSold=false, készlet 0; kiírt akciós ár 235 CHF)"),
    "ap-t2": R(r"aimpoint-micro-t2", None,
               why="csomag (Micro T-2 + LRP szerelék): a bolt a Micro T-2-t szerelék nélkül árulja"),
    "ap-acro": R(r"aimpoint-acro", None,
                 why="az ACRO P-2 (+ Picatinny talp) nincs a boltban (csak ACRO C-2 és S-2)"),
    "ap-m5": R(r"aimpoint-comp-?m5", None, why="a CompM5 nincs a boltban"),
    "ap-pro": R(r"aimpoint-pro", None, why="az Aimpoint PRO nincs a boltban"),
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


# --------------------------------------------------------------------------- sitemap + oldal
def load_slugs(http):
    idx = http.get(BASE + "/sitemap.xml")
    m = re.search(r"<loc>([^<]*item-sitemap\.xml)</loc>", idx)
    if not m:
        raise RuntimeError("nem találom az item-sitemap.xml-t")
    xml = http.get(m.group(1))
    return re.findall(r"<loc>https://felder-jagdhof\.ch/shop/item/([^<]+)</loc>", xml)


def parse_item(html, slug):
    m = re.search(r'<script id="ng-state" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return None
    st = json.loads(m.group(1))
    key = [k for k in st if k.startswith("item-detail:")]
    if not key:
        return None
    v = st[key[0]]
    mi = v["mainItem"]
    sm = v.get("selectedMeta") or v.get("mainMeta") or mi.get("_meta") or {}
    des = ((mi.get("designation") or {}).get("de-CH") or {}).get("f-") or ""
    pr = sm.get("price") or {}

    def amt(x):
        return (x or {}).get("amount")

    price, full = amt(pr.get("priceInclVat")), amt(pr.get("priceWithoutDiscountInclVat"))
    msrp = amt(pr.get("manufacturerSuggestedRetailPrice"))
    stock = sm.get("stock") or {}
    permits = []
    for p in sm.get("requiredPermits") or []:
        t = (((p.get("text") or {}).get("object") or {}).get("de-CH") or {}).get("f-")
        if t:
            permits.append(t)
    imgs = [i["file"]["url"] for i in mi.get("images", []) if i.get("file", {}).get("url")]
    return dict(title=norm(des), price_chf=price,
                list_chf=full if (full is not None and price is not None and full > price) else None,
                msrp=msrp, sku=mi.get("itemNumber"), model=mi.get("model"),
                brand=(mi.get("manufacturer") or {}).get("_title"),
                image=imgs[0] if imgs else None, in_stock=bool(stock.get("isAvailable")),
                stock_n=stock.get("stock"), on_order=bool(stock.get("onlyOnOrder")),
                can_be_sold=bool(sm.get("canBeSold")), on_request=bool((pr or {}).get("priceOnRequest")),
                variants=len(v.get("variants") or []), permits=permits,
                url=BASE + "/shop/item/" + slug)


def decide(http, pid, brand, name, rule, slugs, pages):
    def page(slug):
        if slug not in pages:
            pages[slug] = parse_item(http.get(BASE + "/shop/item/" + slug), slug)
        return pages[slug]

    if rule:
        cands = [s for s in slugs if re.search(rule["cand"], s)]
    else:
        b = slugify(brand)
        toks = [t for t in re.findall(r"[a-z0-9]{3,}", slugify(name)) if t not in ("pro", "grip", "stock", "sight")]
        cands = [s for s in slugs if b in s and any(t in s for t in toks)]

    def near_txt(shown):
        return " | ".join("%s (%s, %s)" % (c["title"], ("%.2f CHF" % c["price_chf"]) if c["price_chf"] is not None else "ár?",
                                           c["url"]) for c in shown[:4])

    if not rule:
        txt = "ok: nincs a boltban (a termék-sitemapben nincs egyező márka+típus tétel)"
        if cands:
            txt = "ok: nincs a boltban pontosan azonos tétel; a sitemapben hasonló slug(ok), NEM azonos: " + \
                  " | ".join("%s (%s/shop/item/%s)" % (s.replace("-", " "), BASE, s) for s in cands[:4])
        return "no", txt
    infos = [page(s) for s in cands[:MAX_CAND]]
    infos = [i for i in infos if i]
    hits = [i for i in infos if rule["title"] and re.fullmatch(rule["title"], i["title"], re.I)]
    if not hits:
        txt = "ok: " + (rule["why"] or "nincs a boltban (nincs pontosan azonos tétel)")
        if infos:
            txt += "; legközelebbi bolti termék(ek), NEM azonos: " + near_txt(infos)
        elif cands:
            txt += "; hasonló slug(ok): " + ", ".join(cands[:4])
        return "no", txt
    hits.sort(key=lambda i: (i["price_chf"] is None, i["price_chf"] or 0))
    h = hits[0]
    if not h["can_be_sold"]:
        return "no", "ok: a bolt nem értékesíti online (canBeSold=false), kiírt ár: %s CHF (%s)" % (h["price_chf"], h["url"])
    if h["price_chf"] is None or h["on_request"]:
        return "no", "ok: nincs kiírt ár (árajánlatkérés): " + h["url"]
    if h["variants"]:
        return "no", "ok: a terméknek változatai vannak, a változat nem azonosítható biztosan: " + h["url"]
    stock_text = "Lager: %s Stk." % h["stock_n"] if h["stock_n"] is not None else ("lieferbar" if h["in_stock"] else "nicht an Lager")
    item = dict(title=h["title"], price_chf=h["price_chf"], list_chf=h["list_chf"], url=h["url"], image=h["image"],
                in_stock=h["in_stock"], stock_text=stock_text, sku=h["sku"], mpn=None, weight_g=None, dims=None,
                colors_prices={}, match="exact" if len(hits) == 1 else "variant")
    if h["permits"]:
        item["permit"] = ", ".join(h["permits"])
    if rule.get("note"):
        item["note"] = rule["note"]
    if len(hits) > 1:
        item["alternatives"] = [dict(title=i["title"], url=i["url"], price_chf=i["price_chf"]) for i in hits[1:]]
    return "ok", item


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/%s.json; --only mellett stdout)" % SLUG)
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    ap.add_argument("--grep", help="a termék-sitemap slugjainak szűrése erre a regexre, majd kilép")
    args = ap.parse_args()
    http = Http(args.cache)
    slugs = load_slugs(http)
    sys.stderr.write("sitemap: %d tétel\n" % len(slugs))
    if args.grep:
        for s in slugs:
            if re.search(args.grep, s, re.I):
                print(BASE + "/shop/item/" + s)
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
            kind, res = decide(http, pid, brand, name, RULES.get(pid), slugs, pages)
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
