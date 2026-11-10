#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
armurerie-richert.ch ár-gyűjtő az AR-15 konfigurátorhoz (CHF, a bolt kiírt ára).

Futtatás (a repo gyökeréből):
    python3 tools/prices_armurerie_richert.py                  # -> assets/prices/armurerie-richert.json
    python3 tools/prices_armurerie_richert.py --only eo-exps3,rd-raptor   # részleges futás (stdout)
    python3 tools/prices_armurerie_richert.py --cache /tmp/richert_cache  # HTTP-gyorsítótár

Függőség: Python 3.8+ (csak standard könyvtár; a bs4 itt nem kell, mert JSON-t olvas).

Működés
  * A bolt WooCommerce, a nyilvános Store API-ból (/wp-json/wc/store/v1/products,
    100 termék/oldal, ~5 kérés) a TELJES katalógust letölti (név, ár, akciós ár,
    készlet, kép, leírás).
  * A js/data.js minden azonosítójához a RULES táblában van a bolti terméknév PONTOS
    (fullmatch, kis/nagybetű-független) mintája; színváltozatnál színenként külön minta
    (a bolt a színeket külön termékként listázza, pl. "... Fde.").
  * 'need': a leírásban kötelezően szereplő szöveg (pl. a típusjelzés), ha a bolti cím
    önmagában nem elég egyértelmű.
  * Ha nincs pontos egyezés, az id az "unmatched"-be kerül okkal + a 'near' mintára
    illeszkedő bolti termékek nevével/árával/URL-jével ("NEM azonos").
  * Árat soha nem becsül, nem számol, nem vált át.

Udvariasság: egyetlen szál, kérések között >= 1,2 s, böngésző User-Agent; csak
armurerie-richert.ch-ra kérdez (a képeknek csak az URL-jét rögzíti).
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

BASE = "https://armurerie-richert.ch"
API = BASE + "/wp-json/wc/store/v1/products"
SHOP = "armurerie-richert.ch"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "armurerie-richert.json")


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
                if re.search(r"cf-challenge|Verifying (you are human|connection)|captcha", body[:5000], re.I):
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


# --------------------------------------------------------------------------- segédek
def norm(s):
    s = htmllib.unescape(s or "")
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ").replace("″", '"')
    return re.sub(r"\s+", " ", s).strip()


def text_of(markup):
    return norm(re.sub(r"<[^>]+>", " ", markup or ""))


def read_data_js():
    """(id, márka, név) a BASES és ITEMS literál objektumaiból (a kategóriáknak nincs márkája,
    a FACTORY()/NONE() elemek nem literálok, így kimaradnak)."""
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
    """colors: {data.js színkulcs: bolti név regex}; main: bolti név regex (szín nélkül;
    több találatnál a legolcsóbb a fő, a többi 'alternatives'); why: indok, ha nincs
    egyezés; near: regex a tájékoztató 'legközelebbi' termékekhez; need: regex, aminek a
    leírásban szerepelnie kell; note: megjegyzés a felvett tételhez."""
    return dict(colors=colors or {}, main=main, why=why, near=near, need=need, note=note)


NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a terméket)"
FIREARM_NONE = "nincs a boltban (ez az alapfegyver nem szerepel a bolt kínálatában)"

RULES = {
    # ---- alapfegyverek
    "schm-s4f": R(why=FIREARM_NONE),
    "schm-m4fl": R(why=FIREARM_NONE),
    "schm-m5fl": R(why=FIREARM_NONE),
    "schm-dyn": R(why=FIREARM_NONE),
    "dd-v7": R(why="nincs DDM4 V7 a boltban (csak más Daniel Defense modellek)",
               near=r"AR-?15 +DANIEL DEFENSE.*|AR15 +Daniel Defense.*"),
    "bcm-recce16": R(why="a bolt a BCM RECCE-14 MCMR-t árulja (14,5\" cső), nem a RECCE-16-ot",
                     near=r"AR15 BCM Reece.*"),
    # ---- markolat / tus / tustalp (Magpul, BCM, B5 bútorzat nincs a boltban)
    **{k: R(why=NOT_SOLD) for k in (
        "mp-moe", "mp-moek2", "mp-moek2p", "mp-miad", "bcm-g0", "bcm-g3", "b5-t22", "b5-t23",
        "mp-ctr", "mp-moesl", "mp-moeslk", "mp-ubr2", "mp-prs3", "mp-prslite", "bcm-s0", "bcm-s1",
        "b5-bravo", "b5-esop", "b5-prec", "mp-pad030", "mp-pad055", "mp-pad070", "mp-padsl")},
    # ---- red dot / holo
    "ap-t2": R(why="nincs Micro T-2 (+ LRP) a boltban", near=r"AIMPOINT H2 .*"),
    "ap-m5": R(why=NOT_SOLD),
    "ap-pro": R(why=NOT_SOLD),
    "ap-acro": R(why="csak ACRO C-2 van a boltban (más modell, talp nélkül), ACRO P-2 + Picatinny talp nincs",
                 near=r"AIMPOINT ACRO C2 .*"),
    "eo-exps3": R(main=r"EOTECH EXPS3-0"),
    "eo-exps2": R(main=r"EOTECH EXPS2-0"),
    "eo-xps2": R(why=NOT_SOLD),
    "hs-510c": R(main=r"HOLOSUN HS510C Noir ou FDE", need=r"HS510C",
                 note="a bolt egy termékként árulja fekete vagy FDE színben (azonos ár)"),
    "hs-aems": R(why="a bolti 'HOLOSUN AEMS Red dot' leírása szerint AEMS CORE (csak 2 MOA pont), "
                     "ami más modell, mint az eredeti AEMS (kör + pont)",
                 near=r"HOLOSUN AEMS (Red dot|Pro)"),
    "hs-403b": R(main=r"HOLOSUN HS403B Red dot", need=r"1/3 Co-Witness",
                 note="a bolti leírás szerint a csomag tartalmazza az alacsony és az 1/3 co-witness (magasított) talpat"),
    "tj-mro": R(why="a bolti 'TRIJICON MRO 2moa' nem HD változat, és a Lower 1/3 szerelékes csomag nincs megadva",
                near=r"TRIJICON MRO.*"),
    "vx-uh1": R(main=r"VORTEX Razor AMG UH-1 GenII noir ou tan",
                note="a bolt egy termékként árulja fekete vagy tan színben (azonos ár)"),
    "vx-sparc": R(why=NOT_SOLD),
    "vx-crossfire": R(main=r"VORTEX Crossfire red dot", need=r"lower 1/3 co-witness mount",
                      note="a bolti leírás szerint a csomagban van a lower 1/3 co-witness (magasított) talp"),
    # ---- LPVO
    "vx-se16": R(why="csak Strike Eagle 5-25x56 van a boltban", near=r"VORTEX Strike Eagle.*"),
    "vx-se18": R(why="csak Strike Eagle 5-25x56 van a boltban", near=r"VORTEX Strike Eagle.*"),
    "vx-pst16": R(why=NOT_SOLD),
    "vx-razor110": R(why="a bolti 'VORTEX AMG 1-10x 1-10x24 FFP EBR-9 MRAD' neve és leírása nem 'Razor HD Gen III' – "
                         "nem azonosítható biztosan azonosnak",
                     near=r"VORTEX AMG 1-10x.*"),
    "tj-acog": R(why=NOT_SOLD),
    "tj-credo16": R(why=NOT_SOLD),
    "tj-vcog": R(why=NOT_SOLD),
    "eo-vudu16": R(main=r"EOTECH Vudu 1-6x SR1 MRAD", need=r"Vudu 1-6×24",
                   note="FFP, SR1 (MRAD) retikül; a 'VuduX 1-6x24 SFP' más modell"),
    "eo-vudu110": R(main=r"EO[Tt]ech Vudu 1-10×28 FFP .*",
                    note="a bolt két retikülös kivitelt árul (LE5 MRAD / SR5 Tan); a data.js nem határozza meg, a fő ár a legolcsóbb"),
    # ---- szerelék / nagyító
    "gs-sp30": R(why=NOT_SOLD),
    "vx-cant30": R(why=NOT_SOLD),
    "eo-g33": R(why="G33 csak EXPS3 + G33 + Unity szerelék szettben (más csomag)",
                near=r"EOTECH EXPS3 / G33.*"),
    "eo-g43": R(why="a 'G43 Micro 3x' leírása szerint Switch-to-Side (STS) szerelékkel szállítják; a 'G43 + Micro szerelék' "
                    "csomag nem azonosítható biztosan",
                near=r"EOTECH G43 .*"),
    "ap-3x": R(why=NOT_SOLD),
    "hs-hm3x": R(why=NOT_SOLD),
    "vx-m3x": R(why=NOT_SOLD),
    # ---- BUIS
    "mp-mbus3": R(main=r"MAGPUL Mbus Gen3 \(set\)",
                  note="szett (első + hátsó); a bolt nem jelöli a színt"),
    "mp-mbuspro": R(main=r"MAGPUL Mbus Pro métaliques \(set\)", note="szett (első + hátsó)"),
    "mp-mbusoff": R(why=NOT_SOLD),
    # ---- lámpák
    "sf-m600": R(why="nincs M600DF a boltban", near=r"SUREFIRE Scout.*"),
    "sf-m640": R(why="a bolt M640U-t árul, a data.js M640DF (Dual Fuel) – más típus", near=r"SUREFIRE Scoutlight Pro.*"),
    "sf-m300": R(why="a bolti 'SUREFIRE M300 Fde' a leírás szerint M300 Mini Scout (500 lm), nem 'Mini Scout Light Pro'",
                 near=r"SUREFIRE M300.*"),
    "sf-x300": R(why="csak X300 Turbo van a boltban, X300U-B nincs", near=r"SUREFIRE X300.*"),
    "sl-hlx": R(main=r"STREAMLIGHT Protac HL avec switch", need=r"ProTac ?® ?Rail Mount HL-X",
                note="bolti cím: 'Protac HL avec switch'; a leírás szerint ProTac Rail Mount HL-X (1000 lm), távkapcsolóval és nyomógombos végkapcsolóval"),
    "sl-tlr1": R(main=r"STREAMLIGHT TLR-1 HL"),
    # ---- M-LOK / bipod / torkolat
    **{k: R(why=NOT_SOLD) for k in (
        "mp-rvg", "mp-hstop", "bcm-vg3", "bcm-kag", "mp-rail5", "mp-qd", "mp-cover", "mp-cable",
        "mp-bipod", "hr-sbrm", "sf-3p", "bcm-comp0", "sf-rc2")},
    "sf-warcomp": R(main=r"Compensateur SUREFIRE Warcomp 5\.56mm", need=r"WARCOMP-556-1/2-28",
                    note="WARCOMP-556-1/2-28 (nyitott ágú); a 'Closed tine' külön termék"),
    # ---- elsütők
    "gs-ssae": R(why="SSA-E nincs a boltban (csak SSA, SD-E, SD-3G, SSP, Super T)", near=r"Détente GEISSELE S(SA|D-E) .*"),
    "gs-ssa": R(main=r"Détente GEISSELE SSA pour AR15"),
    "gs-sde": R(main=r"Détente GEISSELE SD-E pour AR15"),
    "gs-s3g": R(why="S3G nincs a boltban", near=r"Détente GEISSELE .*"),
    # ---- felhúzókar
    "rd-raptor": R(colors={"black": r"Levier d’armement RADIAN Raptor pour AR15",
                           "fde": r"Levier d’armement RADIAN Raptor pour AR15 Fde\."}),
    "rd-raptorlt": R(main=r"Levier d’armement RADIAN Raptor LT pour AR15",
                     note="fekete; a bolt FDE színben is árulja azonos áron (a data.js-ben nincs színpaletta)"),
    "rd-raptorsd": R(why=NOT_SOLD),
    "bcm-ch4": R(why=NOT_SOLD),
    "bcm-ch3": R(why=NOT_SOLD),
    "gs-ach": R(why="Airborne nincs a boltban; a 'GEISSELE SCH' (Super Charging Handle) más termék",
                near=r"Levier d’arme?ment GEISSELE.*"),
    # ---- kezelőszervek
    "rd-talon": R(main=r"Sélecteur ambidextre RADIAN Talon pour AR15", need=r"45/90",
                  note="Talon 45/90, rövid + hosszú karral"),
    "mp-bad": R(why=NOT_SOLD),
    "mp-trigguard": R(why=NOT_SOLD),
    # ---- tárak / szíjak
    **{k: R(why=NOT_SOLD) for k in (
        "mp-pm10", "mp-pm20", "mp-pm30", "mp-pm30w", "mp-pm40", "mp-d60", "mp-ms1", "mp-ms4", "bfg-vickers")},
}


# --------------------------------------------------------------------------- katalógus
def fetch_catalog(http):
    products, page, pages = [], 1, 1
    while page <= pages:
        body = http.get("%s?per_page=100&page=%d" % (API, page))
        batch = json.loads(body)
        products += batch
        if len(batch) == 100:
            pages = page + 1  # a cache-ből nem látszik az X-WP-TotalPages fejléc
        page += 1
        if page > 40:
            break
    return products


def price_of(p):
    pr = p["prices"]
    div = 10 ** int(pr.get("currency_minor_unit", 2))
    price = round(int(pr["price"]) / div, 2)
    reg = round(int(pr["regular_price"]) / div, 2)
    return price, (reg if p.get("on_sale") and reg > price else None)


def record(p, note=None):
    price, lst = price_of(p)
    st = p.get("stock_availability") or {}
    return dict(
        title=norm(p["name"]), price_chf=price, list_chf=lst, url=p["permalink"],
        image=(p.get("images") or [{}])[0].get("src"),
        in_stock=bool(p.get("is_in_stock")) if p.get("is_in_stock") is not None else None,
        stock_text=norm(st.get("text")) or st.get("class") or None,
        sku=p.get("sku") or None, mpn=None,
        weight_g=None, dims=None,
    )


def decide(pid, rule, catalog):
    def find(rx):
        return [p for p in catalog if re.fullmatch(rx, norm(p["name"]), re.I)
                and (not rule["need"] or re.search(rule["need"], text_of(p.get("description")), re.I))]

    item = None
    if rule["colors"]:
        cp = {}
        for col, rx in rule["colors"].items():
            hits = sorted(find(rx), key=lambda p: price_of(p)[0])
            if hits:
                r = record(hits[0])
                cp[col] = dict(price_chf=r["price_chf"], list_chf=r["list_chf"], url=r["url"],
                               in_stock=r["in_stock"], title=r["title"])
        if cp:
            first = next(c for c in rule["colors"] if c in cp)
            main = [p for p in catalog if p["permalink"] == cp[first]["url"]][0]
            item = record(main)
            item["color"] = first
            item["colors_prices"] = cp
            item["match"] = "exact"
    elif rule["main"]:
        hits = sorted(find(rule["main"]), key=lambda p: price_of(p)[0])
        if hits:
            item = record(hits[0])
            item["match"] = "exact" if len(hits) == 1 else "variant"
            if len(hits) > 1:
                item["alternatives"] = [dict(title=norm(p["name"]), url=p["permalink"],
                                             price_chf=price_of(p)[0]) for p in hits[1:]]
    if item:
        if rule["note"]:
            item["note"] = rule["note"]
        return "ok", item
    reason = "ok: " + (rule["why"] or "nincs a boltban (a katalógusban nincs pontosan azonos termék)")
    if rule["near"]:
        near = [p for p in catalog if re.fullmatch(rule["near"], norm(p["name"]), re.I)]
        if near:
            reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(
                "%s (%.2f CHF, %s)" % (norm(p["name"]), price_of(p)[0], p["permalink"]) for p in near[:4])
    return "no", reason


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás, stdout)")
    ap.add_argument("--out", help="kimeneti JSON (alap: assets/prices/armurerie-richert.json)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    http = Http(args.cache)
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
               source=API + " (WooCommerce Store API)",
               price_basis="a bolt kiírt ára CHF-ben (akciónál price_chf = akciós ár, list_chf = eredeti ár)",
               items={}, unmatched={}, blocked=None)
    try:
        catalog = fetch_catalog(http)
    except Blocked as e:
        doc["blocked"] = str(e)
        catalog = None
    if catalog is not None:
        sys.stderr.write("Katalógus: %d termék\n" % len(catalog))
        for pid, brand, name in entries:
            rule = RULES.get(pid) or R(main=re.escape("%s %s" % (brand, name)))
            kind, res = decide(pid, rule, catalog)
            (doc["items"] if kind == "ok" else doc["unmatched"])[pid] = res
            sys.stderr.write("%-14s %s\n" % (pid, ("CHF %s  %s" % (res["price_chf"], res["title"])) if kind == "ok" else "-"))
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
