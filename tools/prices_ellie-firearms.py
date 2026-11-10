#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ellie-firearms.com – ellenőrző szkript (árat NEM gyűjt).

A közös utasítás (research/PRICING_BRIEF.md) szerint csak svájci boltok CHF-árai kerülhetnek be.
Az ellie-firearms.com Impressuma szerint a bolt üzemeltetője az ELLIE FA GmbH, székhelye
FL-9495 Triesen, értékesítési pontja Nendeln – azaz Liechtenstein, nem Svájc. Az árak ugyan
CHF-ben vannak, de a feladat szerint nem svájci boltnál árat nem gyűjtünk.

Ez a szkript újrafuttatható: letölti (vagy fájlból beolvassa) az Impressumot, megállapítja az
ország szerinti címet, és megírja az assets/prices/ellie-firearms.json-t:
  - nem svájci cím  -> items: {}, minden data.js id az 'unmatched'-ben az okkal, 'excluded' mező;
  - svájci cím      -> NEM ír semmit, hanem jelzi, hogy a bolt státusza megváltozott, és a
                       gyűjtőt (PrestaShop-kereső: /suche?s=…) meg kell írni.

Futtatás (a repo gyökeréből):
    python3 tools/prices_ellie-firearms.py
    python3 tools/prices_ellie-firearms.py --impressum-file mentett_impressum.html   # hálózat nélkül

Függőség: Python 3.8+, beautifulsoup4. Egyetlen kérés (az Impressum), böngésző User-Agent.
Botvédelem (403, challenge/captcha, 429) esetén nem próbálkozik tovább.
"""
import argparse
import datetime
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("Hiányzik a beautifulsoup4: pip install beautifulsoup4 lxml")

SHOP = "ellie-firearms.com"
IMPRESSUM = "https://ellie-firearms.com/content/7-impressum"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "ellie-firearms.json")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def data_ids():
    with open(DATA_JS, encoding="utf-8") as f:
        src = f.read()
    starts = list(re.finditer(r"\{\s*id:\s*'([^']+)',", src))
    out = []
    for i, m in enumerate(starts):
        chunk = src[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(src)]
        chunk = chunk[: chunk.find("}") + 1 or len(chunk)]
        if re.search(r"brand:\s*'", chunk) and re.search(r"name:\s*'", chunk):
            out.append(m.group(1))
    return out


def fetch_impressum():
    req = urllib.request.Request(IMPRESSUM, headers={"User-Agent": UA, "Accept": "*/*",
                                                     "Accept-Language": "de-CH,de;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            sys.exit("A bolt %d-et adott (botvédelem / korlátozás) – nem próbálkozunk tovább." % e.code)
        raise
    if re.search(r"cf-chl|challenge-platform|Just a moment\.\.\.|captcha", body[:8000], re.I):
        sys.exit("Botvédelmi oldal – nem próbálkozunk tovább.")
    return body


def analyse(html):
    s = BeautifulSoup(html, "lxml")
    main = s.select_one("#content") or s.select_one("main") or s
    text = norm(main.get_text(" "))
    fl = re.findall(r"\bFL\s*-?\s*(\d{4})\s+([A-ZÄÖÜ][\wäöüÄÖÜ]+)", text)
    ch = re.findall(r"\bCH\s*-?\s*(\d{4})\s+([A-ZÄÖÜ][\wäöüÄÖÜ]+)", text)
    firm = re.search(r"(ELLIE FA GmbH)", text)
    return dict(text=text, fl=fl, ch=ch, firm=firm.group(1) if firm else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--impressum-file", help="mentett Impressum-HTML (hálózat nélküli futtatáshoz)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/ellie-firearms.json)")
    args = ap.parse_args()
    if args.impressum_file:
        with open(args.impressum_file, encoding="utf-8", errors="replace") as f:
            html = f.read()
    else:
        html = fetch_impressum()
    if not norm(html):
        sys.exit("Üres válasz az Impressum-oldalról – nem sikerült ellenőrizni; a JSON változatlan.")
    a = analyse(html)
    if not a["fl"] and not a["ch"]:
        sys.exit("Az Impressumban nem találtam országkódos címet (FL-/CH-) – kézi ellenőrzés kell.")
    if a["ch"] and not a["fl"]:
        sys.exit("Az Impressum svájci címet mutat (%s) – a bolt státusza megváltozott, az árgyűjtőt meg kell "
                 "írni (PrestaShop: /suche?s=...). A JSON változatlan." % a["ch"])
    places = ", ".join("FL-%s %s" % p for p in a["fl"])
    reason = ("ok: nem svájci bolt – az Impressum szerint %s (%s, Liechtenstein); az ár CHF, de a feladat "
              "szerint nem svájci boltnál árat nem gyűjtünk" % (a["firm"] or "a cég", places))
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
               excluded="nem svájci bolt: %s, %s (Liechtenstein) – forrás: %s" % (a["firm"] or "?", places, IMPRESSUM),
               items={}, unmatched={pid: reason for pid in data_ids()}, blocked=None)
    out = args.out or OUT_JSON
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    sys.stderr.write("Kész: %s  (nem svájci bolt: %s; 0 ár, %d id az unmatched-ben)\n" % (
        out, places, len(doc["unmatched"])))


if __name__ == "__main__":
    main()
