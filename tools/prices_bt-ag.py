#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bt-ag.ch (B&T AG, Thun BE) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_bt-ag.py                  # minden termék -> assets/prices/bt-ag.json
    python3 tools/prices_bt-ag.py --only mp-pm30   # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_bt-ag.py --cache /tmp/btcache

Függőség: Python 3.8+ (standard könyvtár).

Működés
  * A bt-ag.ch WooCommerce-katalógusát a nyilvános Store API-n tölti le
    (/wp-json/wc/store/v1/products?per_page=100&page=N; ~145 termék, 2 kérés).
  * Csak a PONTOSAN azonos termék egyezik: a data.js márkája a bolti névben / márkamezőben, és a
    teljes név (írásjel- és kisbetű-függetlenül) azonos. A saját B&T termékek (fegyverek,
    hangtompítók, B&T szerelékek) NEM számítanak egyezésnek.
  * Megjegyzés: a bt-ag.ch termékoldalai nem írnak ki árat (a Store API-ban van ár, de az oldalon
    'Händler finden' gomb van). A polgári webshop külön hoston fut (webshop.bt-ag.ch), azt ez a
    szkript nem kérdezi (a gyűjtéskor a futtatókörnyezet hálózati szabálya nem engedte: proxy 403).

Udvariasság: egy szálon, kérések között >= 1,1 s; 403 / kihívás / tartós 429 -> azonnal leáll ('blocked').
Csak a bt-ag.ch hostra kérdez.
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

BASE = "https://bt-ag.ch"
API = BASE + "/wp-json/wc/store/v1/products"
HOSTS = ("bt-ag.ch", "www.bt-ag.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.1

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "bt-ag.json")

WEBSHOP_NOTE = ("a B&T polgári webshopja (webshop.bt-ag.ch, 'bewilligungsfrei', külső beszállítók termékei) "
                "külön hoston fut: a gyűjtéskor a futtatókörnyezet hálózati szabálya tiltotta (proxy 403), így ott "
                "nincs ellenőrizve; a Flagship Store oldala (bt-shop.ch) nem volt a feladat része")


class Blocked(Exception):
    pass


class Http:
    def __init__(self, cache_dir=None):
        self.cache_dir, self.last, self.n = cache_dir, 0.0, 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def get(self, url):
        if urllib.parse.urlparse(url).netloc not in HOSTS:
            raise ValueError("nem engedélyezett host: " + url)
        ck = os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest()) if self.cache_dir else None
        if ck and os.path.exists(ck):
            with open(ck, "rb") as f:
                return f.read().decode("utf-8", "replace")
        headers = {"User-Agent": UA, "Accept": "application/json", "Accept-Language": "de-CH,de;q=0.9"}
        last_err = None
        for attempt in range(4):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                    body = r.read().decode("utf-8", "replace")
                if re.search(r"cf-chl|challenge-platform/h/|Just a moment\.\.\.|captcha", body[:5000], re.I):
                    raise Blocked("bot-védelmi oldal: " + url)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body.encode("utf-8"))
                return body
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 403:
                    raise Blocked("HTTP 403: " + url)
                if e.code == 429 and attempt >= 2:
                    raise Blocked("tartós HTTP 429: " + url)
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(3 * (attempt + 1))
        raise RuntimeError("letöltés sikertelen: %s (%s)" % (url, last_err))


def norm(s):
    s = unicodedata.normalize("NFKC", html.unescape(s or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "", norm(s).lower())


def read_data_js(path=DATA_JS):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    out = []
    for m in re.finditer(r"\{\s*id:\s*'([^']+)'([^{}]*?)\}", src, re.S):
        body = m.group(2)
        b = re.search(r"\bbrand:\s*'((?:[^'\\]|\\.)*)'", body)
        n = re.search(r"\bname:\s*'((?:[^'\\]|\\.)*)'", body)
        if b and n:
            out.append(dict(id=m.group(1), brand=b.group(1).replace("\\'", "'"), name=n.group(1).replace("\\'", "'")))
    return out


def catalog(http):
    prods = []
    for page in range(1, 30):
        d = json.loads(http.get(API + "?" + urllib.parse.urlencode({"per_page": 100, "page": page})))
        if not d:
            break
        prods.extend(d)
        if len(d) < 100:
            break
    out = []
    for p in prods:
        pr = p.get("prices") or {}
        unit = int(pr.get("currency_minor_unit") or 2)
        brands = [norm(b.get("name")) for b in (p.get("brands") or [])]
        out.append(dict(name=norm(p.get("name")), brands=brands, url=p.get("permalink"),
                        price=(int(pr["price"]) / 10 ** unit) if pr.get("price") else None,
                        currency=pr.get("currency_code"), sku=p.get("sku"),
                        cats=[norm(c.get("name")) for c in p.get("categories") or []]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only")
    ap.add_argument("--out")
    ap.add_argument("--cache")
    args = ap.parse_args()
    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["id"] in wanted]
    http = Http(args.cache)
    items, unmatched, blocked = {}, {}, None
    try:
        cat = catalog(http)
    except Blocked as b:
        cat, blocked = [], str(b)
    own = [c for c in cat if c["name"].startswith("B&T") or any("B&T" in b for b in c["brands"])
           or any(x.startswith("B&T") for x in c["cats"])]
    sys.stderr.write("katalógus: %d termék, ebből saját B&T: %d\n" % (len(cat), len(own)))
    for e in entries if not blocked else []:
        bslug = slug(e["brand"])
        cands = [c for c in cat if bslug in slug(c["name"]) or any(slug(b) == bslug for b in c["brands"])]
        hits = [c for c in cands if slug(c["name"]) in (slug(e["brand"] + " " + e["name"]), slug(e["name"]))]
        if hits:
            c = hits[0]
            items[e["id"]] = dict(title=c["name"], price_chf=c["price"], list_chf=None, url=c["url"], image=None,
                                  in_stock=None, stock_text=None, sku=c["sku"], mpn=None, weight_g=None, dims=None,
                                  match="exact",
                                  note="ár a bolt Store API-jából; a termékoldal nem ír ki árat")
            continue
        why = ("ok: nincs a bt-ag.ch katalógusában – a %d termékes katalógus a gyártó saját B&T termékeit "
               "tartalmazza (fegyverek, hangtompítók, adapterek, szerelékek, markolatok), %s márkájú termék nincs "
               "benne; %s" % (len(cat), e["brand"], WEBSHOP_NOTE))
        if cands:
            why += "; a névben '%s' szerepel, de NEM azonos: %s" % (
                e["brand"], " | ".join("%s (%s CHF, %s)" % (c["name"], c["price"], c["url"]) for c in cands[:3]))
        unmatched[e["id"]] = why
    if blocked:
        unmatched = {e["id"]: "ok: a bolt botvédelmet / tiltást adott" for e in entries}
    doc = dict(shop="bt-ag.ch", currency="CHF", fetched=datetime.date.today().isoformat(),
               items=items, unmatched=unmatched, blocked=blocked,
               note="A bt-ag.ch a gyártó (B&T AG) oldala: a WooCommerce-katalógus csak saját B&T termékeket "
                    "tartalmaz, a termékoldalak nem írnak ki árat ('Händler finden'). " + WEBSHOP_NOTE[0].upper()
                    + WEBSHOP_NOTE[1:] + ".")
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    out = args.out or (None if args.only else OUT_JSON)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("Kész: %s  (%d egyezés, %d kimaradt, %d HTTP kérés)\n" % (out, len(items), len(unmatched), http.n))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
