#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
freesport.ch (Freesport Pierre-Alain Dufaux SA, Granges-Paccot FR) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_freesport.py                    # minden termék -> assets/prices/freesport.json
    python3 tools/prices_freesport.py --only hr-sbrm     # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_freesport.py --cache /tmp/fsc   # HTTP-válaszok gyorsítótára (fejlesztéshez)
    python3 tools/prices_freesport.py --dump Harris      # a bolti keresés találatainak listája (szabályíráshoz)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés (Shopware 6 storefront, francia nyelvű oldal)
  1. A js/data.js-ből kiolvassa a BASES és ITEMS azonosítókat.
  2. Id-nként a RULES táblában (ha van): keresőkifejezés + a bolti terméknév PONTOS (fullmatch) mintája.
     Szabály nélküli id: "márka + név" keresés, csak teljes (írásjel- és kisbetű-független) névegyezés
     számít.
  3. Találat -> termékoldal: ár, áthúzott ár, kép, cikkszám, készlet-címke.
  4. Nincs pontos egyezés -> "unmatched" okkal + a legközelebbi bolti termékek (név, ár, URL).
     Árat soha nem becsül, nem számol, nem vált át.

A bolt vadász/sport kínálatú (Blaser, Walther, Wyss, Hawke...), AR-15 tartozékot gyakorlatilag nem árul:
a Magpul, BCM, Geissele, Radian, Schmeisser, Daniel Defense, SureFire, Streamlight, AR15, M-LOK, PMAG
keresések 0 találatot adnak; Aimpoint/EOTech/Holosun/Vortex/Trijicon alatt csak Walther
szerelőlemezek vannak. A scriptnek ezért jelenleg nincs egyező tétele - ellenőrzés: 2026-11-10.

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

# --------------------------------------------------------------------------- bolt-konfiguráció
SHOP = "freesport.ch"
SLUG = "freesport"
BASE = "https://www.freesport.ch"
HOSTS = ('www.freesport.ch', 'freesport.ch')
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
LANG = "fr-CH,fr;q=0.9,de;q=0.6,en;q=0.4"
MIN_GAP = 1.2
MAX_PAGES = 12

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

    def get(self, url, params=None):
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
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
    s = re.sub(r"[®™©]", "", s or "")  # NFKC előtt: a ™ különben "TM"-mé alakulna
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", norm(s).lower())


def parse_chf(text):
    """'1.995,00 CHF*' / 'Ab 33,00 CHF*' / '1 453,00 CHF' -> float"""
    t = norm(text)
    m = re.search(r"([0-9][0-9.'’ ]*,[0-9]{2})", t)
    if m:
        return float(re.sub(r"[.'’ ]", "", m.group(1)).replace(",", "."))
    m = re.search(r"([0-9][0-9'’]*(?:\.[0-9]{1,2})?)", t)
    return float(re.sub(r"['’]", "", m.group(1))) if m else None


def kg_to_g(txt):
    m = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*kg", txt or "")
    return int(round(float(m.group(1).replace(",", ".")) * 1000)) if m else None


def stock_state(txt):
    low = (txt or "").lower()
    if not low:
        return None
    if re.search(r"nicht (mehr )?(lieferbar|verfügbar|an lager)|ausverkauft|auf bestellung|vergriffen|"
                 r"épuisé|sur commande|non disponible", low):
        return False
    if re.search(r"lieferbar|sofort|an lager|verfügbar|en stock|disponible", low):
        return True
    return None


# --------------------------------------------------------------------------- szabályok
def R(q, title=None, colors=None, near=None, why=None, note=None, desc=None):
    """q: keresőkifejezés(ek) a bolt keresőjében;
    title: a fő (fekete / első palettaszín) bolti termék nevének mintája (fullmatch, kisbetű-független,
           ®/™ nélkül) - None: a bolt nem árulja pontosan ugyanezt;
    colors: {paletta-kulcs: névminta} a külön termékként árult színváltozatokhoz;
    near: minta a tájékoztató "legközelebbi" bolti termékekhez; why: indok az unmatched-hez;
    note: megjegyzés a felvett tételhez; desc: a termékleírásban kötelezően szereplő minta."""
    return dict(q=[q] if isinstance(q, str) else q, title=title, colors=colors or {}, near=near,
                why=why, note=note, desc=desc)


RULES = {
    "hr-sbrm": R(["Harris S-BRM", "Harris"], None, near=r".*Harris.*",
                 why="az S-BRM nincs a boltban; a bolt Harris S-BR (14.5-21 cm), S-L (21-31 cm) és S-LM (19.5-30 cm) bipodot árul, ezek más típusok"),
    "mp-bipod": R(["Magpul", "bipied"], None, near=r".*(Magpul|M-?LOK).*",
                  why="Magpul bipod nincs a boltban"),
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


# --------------------------------------------------------------------------- keresés
def search_cards(http, query):
    cards, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        s = soup(http.get(BASE + "/search", {"search": query, "p": page}))
        boxes = s.select(".product-box")
        for b in boxes:
            a = b.select_one("a.product-name") or b.select_one("a.product-image-link")
            if not a or not a.get("href") or a["href"] in seen:
                continue
            seen.add(a["href"])
            pel = b.select_one(".product-price-info") or b.select_one(".product-price")
            lp = b.select_one(".list-price-price")
            ptxt = norm(pel.get_text(" ")) if pel else ""
            if lp:
                ptxt = ptxt.replace(norm(lp.get_text(" ")), " ", 1)
            cards.append(dict(name=norm(a.get("title") or a.get_text(" ")), url=a["href"],
                              price_from=parse_chf(ptxt) if ptxt else None,
                              list_from=parse_chf(lp.get_text(" ")) if lp else None,
                              ab=bool(re.match(r"^(ab|de|dès)\s", ptxt, re.I))))
        pages = [int(i.get("value")) for i in s.select(".pagination input[type=radio]")
                 if (i.get("value") or "").isdigit()]
        if not boxes or page >= (max(pages) if pages else 1):
            break
    return cards


# --------------------------------------------------------------------------- termékoldal
def parse_detail(html, url):
    s = soup(html)
    out = dict(url=url)
    n = s.select_one(".product-detail-name")
    out["title"] = norm(n.get_text(" ")) if n else None
    buy = s.select_one(".product-detail-buy") or s
    mp = buy.select_one('meta[itemprop="price"]')
    price = None
    if mp and mp.get("content"):
        try:
            price = float(mp["content"])
        except ValueError:
            price = None
    pc = buy.select_one(".product-detail-price-container")
    lp = pc.select_one(".list-price-price, .product-detail-list-price") if pc else None
    if price is None and pc:
        txt = norm(pc.get_text(" "))
        if lp:
            txt = txt.replace(norm(lp.get_text(" ")), " ", 1)
        price = parse_chf(txt)
    out["price_chf"] = price
    out["list_chf"] = parse_chf(lp.get_text(" ")) if lp else None
    if out["list_chf"] is not None and price is not None and out["list_chf"] <= price:
        out["list_chf"] = None
    st = buy.select_one(".twt-product-stock-label")
    if st is None:
        st = buy.select_one(".product-detail-delivery-information .delivery-information")
    out["stock_text"] = norm(st.get_text(" ")) if st else ""
    out["in_stock"] = stock_state(out["stock_text"])
    on = buy.select_one(".product-detail-ordernumber")
    if on:
        out["sku"] = norm(on.get_text(" "))
    else:
        oc = buy.select_one(".product-detail-ordernumber-container")
        m = re.search(r"Produktnummer:?\s*(\S+)", norm(oc.get_text(" "))) if oc else None
        out["sku"] = m.group(1) if m else None
    page_txt = norm(s.get_text(" "))
    m = re.search(r"Hersteller(?:artikel)?-?(?:nummer|nr\.?)\s*:?\s*([\w\-./]+)", page_txt, re.I)
    out["mpn"] = m.group(1) if m else None
    w = buy.select_one('meta[itemprop="weight"]')
    out["weight_g"] = kg_to_g(w.get("content")) if w else None
    dims = []
    for prop, lab in (("depth", "L"), ("width", "B"), ("height", "H")):
        e = buy.select_one('meta[itemprop="%s"]' % prop)
        if e and e.get("content"):
            dims.append("%s %s" % (lab, e["content"]))
    out["dims"] = (" x ".join(dims) + " (bolti adat, valószínűleg csomagméret)") if dims else None
    img = s.select_one("img.gallery-slider-image") or s.select_one(".gallery-slider-item img")
    src = (img.get("src") or img.get("data-src")) if img else None
    if not src:
        og = s.select_one('meta[property="og:image"]')
        src = og.get("content") if og else None
    out["image"] = src
    d = s.select_one(".product-detail-description-text") or s.select_one(".product-detail-description")
    out["desc"] = norm(d.get_text(" ")) if d else ""
    out["has_configurator"] = bool(s.select_one(".product-detail-configurator-group"))
    return out


# --------------------------------------------------------------------------- döntés
def fmt_near(cards):
    return " | ".join("%s (%s%s CHF, %s)" % (c["name"], "ab " if c["ab"] else "",
                                             ("%.2f" % c["price_from"]) if c["price_from"] is not None else "?",
                                             c["url"]) for c in cards[:4])


def decide(http, pid, brand, name, rule, qcache, dcache):
    queries = rule["q"] if rule else ["%s %s" % (brand, name)]
    cards, seen = [], set()
    for q in queries:
        if q not in qcache:
            qcache[q] = search_cards(http, q)
        for c in qcache[q]:
            if c["url"] not in seen:
                seen.add(c["url"])
                cards.append(c)

    def fm(pattern, c):
        return pattern and re.fullmatch(pattern, c["name"], re.I)

    if rule:
        hits = [c for c in cards if fm(rule["title"], c)]
    else:
        hits = [c for c in cards if slug(c["name"]) in (slug(name), slug(brand + " " + name))]

    def unmatched(reason):
        near = [c for c in cards if fm((rule or {}).get("near"), c)]
        txt = "ok: " + reason
        if near:
            txt += "; legközelebbi bolti termék(ek), NEM azonos: " + fmt_near(near)
        return "no", txt

    if not hits:
        return unmatched((rule or {}).get("why") or "nincs a boltban (a keresés nem adott pontosan azonos terméket)")
    hits.sort(key=lambda c: (c["price_from"] is None, c["price_from"] or 0))
    main = hits[0]
    if main["ab"]:
        return unmatched("változatfüggő ('ab') ár: a változat ára nem olvasható ki biztosan (%s)" % main["url"])

    def detail(c):
        if c["url"] not in dcache:
            dcache[c["url"]] = parse_detail(http.get(c["url"]), c["url"])
        return dcache[c["url"]]

    d = detail(main)
    if d["title"] and slug(d["title"]) != slug(main["name"]):
        return unmatched("bizonytalan egyezés: a találati név (%s) és a termékoldal neve (%s) eltér"
                         % (main["name"], d["title"]))
    if rule and rule.get("desc") and not re.search(rule["desc"], d["desc"], re.I):
        return unmatched("a termékleírás nem erősíti meg a típust ('%s' hiányzik): %s"
                         % (rule["desc"], main["url"]))
    if d["price_chf"] is None:
        return unmatched("nincs kiírt ár: " + main["url"])
    if d["has_configurator"]:
        return unmatched("a terméknek változatválasztója van, a változat nem azonosítható biztosan: " + main["url"])
    item = dict(title=d["title"] or main["name"], price_chf=d["price_chf"], list_chf=d["list_chf"],
                url=main["url"], image=d["image"], in_stock=d["in_stock"], stock_text=d["stock_text"],
                sku=d["sku"], mpn=d["mpn"], weight_g=d["weight_g"], dims=d["dims"], colors_prices={})
    for key, pat in (rule or {}).get("colors", {}).items():
        cc = [c for c in cards if fm(pat, c) and not c["ab"]]
        if not cc:
            continue
        cc.sort(key=lambda c: (c["price_from"] is None, c["price_from"] or 0))
        dc = detail(cc[0])
        if dc["price_chf"] is None or dc["has_configurator"]:
            continue
        item["colors_prices"][key] = dict(price_chf=dc["price_chf"], list_chf=dc["list_chf"], url=cc[0]["url"],
                                          in_stock=dc["in_stock"], title=dc["title"] or cc[0]["name"])
    if (rule or {}).get("note"):
        item["note"] = rule["note"]
    if len(hits) > 1:
        item["alternatives"] = [dict(title=c["name"], url=c["url"], price_from_chf=c["price_from"])
                                for c in hits[1:]]
        item["match"] = "variant"
    else:
        item["match"] = "exact"
    return "ok", item


def dump(http, queries):
    for q in queries:
        for c in search_cards(http, q):
            print("%-10s %8s  %s  %s" % (q[:10], c["price_from"], c["name"], c["url"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/%s.json; --only mellett stdout)" % SLUG)
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    ap.add_argument("--dump", nargs="+", help="csak kilistázza a keresés találatait")
    args = ap.parse_args()
    http = Http(args.cache)
    if args.dump:
        dump(http, args.dump)
        return
    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    items, unmatched, qcache, dcache, blocked = {}, {}, {}, {}, None
    for i, (pid, brand, name) in enumerate(entries, 1):
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), pid, brand, name))
        if blocked:
            unmatched[pid] = "ok: a bolt letiltotta a lekérdezést (%s), nem ellenőrizhető" % blocked
            continue
        try:
            kind, res = decide(http, pid, brand, name, RULES.get(pid), qcache, dcache)
        except Blocked as e:
            blocked = str(e)
            sys.stderr.write("   BLOKKOLVA: %s – leállás\n" % e)
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
