#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
swiss-sale.ch (Swiss-Sale.ch AG, Menzingen ZG; JTL-Shop) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_swiss-sale.py                    # minden termék -> assets/prices/swiss-sale.json
    python3 tools/prices_swiss-sale.py --only sl-tlr1     # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_swiss-sale.py --cache /tmp/sscache

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  * A bolt keresője: GET https://swiss-sale.ch/?qs=<kifejezés>. A találati oldal JSON-LD
    'ItemList'-je adja a termékeket (név, márka, ár CHF, készlet, URL); 'nichts gefunden' = 0 találat.
  * Először márkánként keres (a data.js márkái); ha a márkára van a bolti márkamezővel egyező
    találat, id-nként „márka + név” keresés is fut, és csak a márka + teljes név (írásjel- és
    kisbetű-független) egyezése, vagy a RULES pontos mintája számít egyezésnek.
  * Egyezésnél a termékoldal JSON-LD Product-ját olvassa (ár, akció, készlet, cikkszám, GTIN, kép).
  * Nincs egyezés -> 'unmatched' okkal. Árat soha nem becsül, nem számol, nem vált át.

Udvariasság: egy szálon, kérések között >= 1,1 s, böngésző User-Agent; 429/5xx esetén visszalépés.
403 / Cloudflare-kihívás / captcha esetén AZONNAL leáll ('blocked'), nem próbálja megkerülni.
(Az oldal betölt egy Cloudflare-szkriptet, de normál tartalmat ad; kihívásoldalnál a szkript leáll.)
Csak a swiss-sale.ch hostra kérdez.
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

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("Hiányzik a beautifulsoup4: pip install beautifulsoup4 lxml")

BASE = "https://swiss-sale.ch"
HOSTS = ("swiss-sale.ch", "www.swiss-sale.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.1

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "swiss-sale.json")


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
        headers = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5"}
        last_err = None
        for attempt in range(4):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as r:
                    body = r.read().decode("utf-8", "replace")
                # a normál oldalakon is van egy háttér-JSD szkript (/cdn-cgi/challenge-platform/scripts/jsd/),
                # az nem kihívás; kihívás: 'Just a moment' / cf-chl / captcha
                if re.search(r"cf-chl|challenge-platform/h/|Just a moment\.\.\.|Verifying (you are human|connection)"
                             r"|g-recaptcha|hcaptcha|cf-turnstile", body[:30000], re.I):
                    raise Blocked("bot-védelmi oldal (challenge/captcha): " + url)
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
                    time.sleep(5 * (attempt + 1) * (3 if e.code == 429 else 1))
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
    s = unicodedata.normalize("NFKC", html.unescape(html.unescape(s or ""))).replace("\xa0", " ")
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


# pontos bolti név-minták (ha a márka egyszer felbukkanna a boltban); üres: csak teljes névegyezés
RULES = {}

# data.js márka -> a boltban várható márkanév(ek) (regex, fullmatch a JSON-LD brand mezőre)
BRAND_RE = {
    "Magpul": r"Magpul", "BCM": r"BCM|Bravo Company.*", "B5 Systems": r"B5 Systems", "Geissele": r"Geissele.*",
    "Radian": r"Radian.*", "Aimpoint": r"Aimpoint", "EOTech": r"EO ?Tech", "Holosun": r"Holosun",
    "Trijicon": r"Trijicon", "Vortex": r"Vortex.*", "SureFire": r"Sure ?Fire", "Streamlight": r"Streamlight",
    "Harris": r"Harris.*", "Blue Force Gear": r"Blue Force Gear", "Schmeisser": r"Schmeisser",
    "Daniel Defense": r"Daniel Defense",
}


def search(http, q):
    body = http.get(BASE + "/?" + urllib.parse.urlencode({"qs": q}))
    s = soup(body)
    text = norm(s.get_text(" "))
    out = []
    for t in s.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except Exception:
            continue
        if isinstance(d, dict) and d.get("@type") == "ItemList":
            for e in d.get("itemListElement") or []:
                it = e.get("item") or {}
                o = it.get("offers") or {}
                br = it.get("brand")
                br = br.get("name") if isinstance(br, dict) else br
                out.append(dict(name=norm(it.get("name")), brand=norm(br or ""),
                                price=float(o["price"]) if o.get("price") not in (None, "") else None,
                                avail=(o.get("availability") or "").rsplit("/", 1)[-1],
                                url=it.get("url") or o.get("url")))
    return out, ("nichts gefunden" in text)


def product(http, url):
    s = soup(http.get(url))
    for t in s.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except Exception:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            return d
    return None


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
    brand_hits = {}
    try:
        for b in sorted(set(e["brand"] for e in entries)):
            res, nf = search(http, b)
            bre = BRAND_RE.get(b, re.escape(b))
            own = [r for r in res if re.fullmatch(bre, r["brand"], re.I)]
            brand_hits[b] = (own, res, nf)
            sys.stderr.write("márka %-16s: %d találat, ebből a márkáé: %d\n" % (b, len(res), len(own)))
        for e in entries:
            own, res, nf = brand_hits[e["brand"]]
            if not own:
                if nf or not res:
                    why = "ok: a bolt nem forgalmazza a márkát (a '%s' keresésre nincs találat)" % e["brand"]
                else:
                    why = ("ok: a bolt nem forgalmazza a márkát (a '%s' keresés csak más márkájú termékeket ad: %s)"
                           % (e["brand"], ", ".join(sorted(set(r["brand"] for r in res if r["brand"])))[:200]))
                unmatched[e["id"]] = why
                continue
            res2, _ = search(http, "%s %s" % (e["brand"], e["name"]))
            cands = {r["url"]: r for r in own + res2 if re.fullmatch(BRAND_RE.get(e["brand"], re.escape(e["brand"])), r["brand"], re.I)}
            pat = RULES.get(e["id"])
            hits = [r for r in cands.values()
                    if (re.fullmatch(pat, r["name"], re.I) if pat else
                        slug(r["name"]) in (slug(e["name"]), slug(e["brand"] + " " + e["name"])))]
            if not hits:
                unmatched[e["id"]] = "ok: nincs pontosan azonos termék a boltban; a márka termékei, NEM azonos: " + \
                    " | ".join("%s (%s CHF, %s)" % (r["name"], r["price"], r["url"]) for r in list(cands.values())[:4])
                continue
            h = hits[0]
            p = product(http, h["url"]) or {}
            o = p.get("offers") or {}
            img = p.get("image")
            items[e["id"]] = dict(title=norm(p.get("name") or h["name"]),
                                  price_chf=float(o["price"]) if o.get("price") else h["price"], list_chf=None,
                                  url=h["url"], image=img[0] if isinstance(img, list) and img else img,
                                  in_stock=(o.get("availability") or "").endswith("InStock"),
                                  stock_text=(o.get("availability") or "").rsplit("/", 1)[-1],
                                  sku=p.get("sku"), mpn=p.get("mpn"), weight_g=None, dims=None, match="exact")
    except Blocked as b:
        blocked = str(b)
        items, unmatched = {}, {e["id"]: "ok: a bolt botvédelmet / tiltást adott, a gyűjtés leállt" for e in entries}
    doc = dict(shop="swiss-sale.ch", currency="CHF", fetched=datetime.date.today().isoformat(),
               items=items, unmatched=unmatched, blocked=blocked,
               note="Outdoor-/survival-bolt (a gyűjtéskor: fegyverlámpák csak Fenix, vörös pontos irányzékok "
                    "Bushnell / Sightmark / Alpen Optics). A data.js márkái közül a boltban talált: %s." % (
                        ", ".join(b for b, v in sorted(brand_hits.items()) if v[0]) or "egyik sem"))
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
