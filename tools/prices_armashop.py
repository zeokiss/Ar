#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
armashop.ch (Armurerie Nouvelle Lausanne SA) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_armashop.py                  # -> assets/prices/armashop.json
    python3 tools/prices_armashop.py --only mp-prs3,eo-exps3   # részleges futás (stdout)
    python3 tools/prices_armashop.py --cache /tmp/armashop_cache

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  * A bolt WooCommerce, de a Store API termék-végpontja ki van kapcsolva, a listázó oldalak
    pedig csak az első 12 terméket adják (a többit POST-os AJAX tölti be – ezt NEM használjuk).
    Ezért a nyilvános Yoast termék-sitemapekből (product-sitemap*.xml, ~10 GET) olvassa ki a
    francia termék-URL-eket (/produit/<slug>/).
  * A js/data.js minden azonosítójához a RULES táblában van a bolti termék slugja (színenként,
    ha a bolt a színeket külön termékként árulja) + a termékoldal címére illesztett PONTOS
    minta (fullmatch). Egyezésnél a termékoldalt letölti: JSON-LD (ár, készlet, kép, SKU),
    akciós ár ("Original price was"), "Couleur" és "Neuf" (új/használt) mező.
  * Használt ("Neuf: Non" / "Occasion") termék nem egyezés.
  * Ha nincs pontos egyezés: "unmatched" okkal + a 'near' slugok termékoldaláról vett
    név/ár/URL ("NEM azonos").
  * Árat soha nem becsül, nem számol, nem vált át.

Udvariasság: egyetlen szál, kérések között >= 1,2 s, böngésző User-Agent, csak armashop.ch-ra
kérdez (GET); nincs bejelentkezés, kosár, POST. 403 / captcha / tartós 429 esetén leáll.
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

BASE = "https://armashop.ch"
SITEMAPS = [BASE + "/product-sitemap.xml"] + [BASE + "/product-sitemap%d.xml" % i for i in range(2, 11)]
SHOP = "armashop.ch"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "armashop.json")


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

    def get(self, url, missing_ok=False):
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
                if e.code == 404 and missing_ok:
                    return None
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


def chf(txt):
    m = re.search(r"([0-9][0-9’',]*(?:\.[0-9]{1,2})?)", txt or "")
    return float(re.sub(r"[’',]", "", m.group(1))) if m else None


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
def R(colors=None, main=None, title=None, why=None, near=(), need=None, note=None, alts=()):
    """colors: {data.js színkulcs: slug}; main: slug (szín nélküli fő termék);
    title: a termékoldal címére (h1) illesztett regex (fullmatch) – ellenőrzés;
    why: indok, ha nincs egyezés; near: slugok a 'legközelebbi' termékekhez;
    need: regex, aminek a termékoldal szövegében szerepelnie kell; alts: ugyanazon modell
    más kivitelei (tájékoztató 'alternatives'); note: megjegyzés."""
    return dict(colors=colors or {}, main=main, title=title, why=why, near=list(near), need=need,
                note=note, alts=list(alts))


NOT_SOLD = "nincs a boltban (a bolt nem forgalmazza ezt a terméket)"
SET_BUIS = "szett (első + hátsó irányzék): a bolt az elsőt és a hátsót külön termékként árulja, szett nincs"
RADIAN_CH = ("a bolti 'Radian Weapons levier d’armement ambidextre' terméknél a típus (Raptor / Raptor-LT / "
             "Raptor-SD) nincs megadva – nem azonosítható biztosan")
VORTEX_UPG = ("a bolti 'Upgrade' megnevezésű Vortex red dot leírás nélkül szerepel; a csomag tartalma "
              "(magasító) és a pontos modell nem azonosítható biztosan")

RULES = {k: R(why=NOT_SOLD) for k in (
    "schm-m4fl", "schm-m5fl", "schm-dyn", "mp-moek2p", "mp-miad", "b5-t22", "b5-t23", "mp-ubr2",
    "bcm-s0", "bcm-s1", "b5-bravo", "b5-esop", "b5-prec", "mp-pad030", "mp-pad055", "mp-pad070", "mp-padsl",
    "ap-m5", "ap-pro", "eo-exps2", "hs-510c", "hs-403b", "tj-mro", "vx-pst16", "vx-razor110", "tj-credo16",
    "tj-vcog", "eo-vudu16", "eo-vudu110", "eo-g33", "eo-g43", "ap-3x", "vx-m3x", "sf-x300", "sl-hlx",
    "mp-qd", "mp-cable", "sf-3p", "bcm-comp0", "sf-rc2", "gs-ssae", "gs-ssa", "gs-sde", "gs-s3g",
    "bcm-ch4", "bcm-ch3", "gs-ach", "rd-talon", "mp-bad", "mp-trigguard", "mp-pm10", "mp-pm20",
    "mp-ms4", "bfg-vickers")}

RULES.update({
    # ---- alapfegyverek
    "schm-s4f": R(why="csak 'Ar15-9 S4F' (9 mm-es változat) van, az AR15 S4F (.223) nincs",
                  near=["ar15-9-s4f-bronze-10-5"]),
    "dd-v7": R(why="nincs DDM4 V7 (alapkivitel) a boltban, csak V7 P / V7 S / V7 K változatok",
               near=["ddm4-v7p", "ddm4-v7s-chocolate-chip-cerakote-finish", "ddm4-v7-k-10-3-le-exclusive"]),
    "bcm-recce16": R(why="nincs BCM RECCE-16 a boltban (csak BCM4 / MK2 puskák)", near=["ar-15-bcm4"]),
    # ---- markolatok
    "mp-moe": R(colors={"black": "moe-pistol-grip-ar15-blk"}, title=r"Magpul MOE Pistol Grip AR15 BLK",
                note="a bolt 'Coyote' színű MOE markolatot is árul (a 'Couleur' mezője FDE-t ír) – a Coyote/FDE "
                     "ellentmondás miatt nem soroltuk a palettához"),
    "mp-moek2": R(colors={"fde": "moe-k2-ar-pistol-grip-fde"}, title=r"Magpul MOE-K2 AR Pistol Grip FDE",
                  note="csak FDE színben kapható"),
    "bcm-g0": R(why="csak BCM Grip Mod 1 van, Mod 0 nincs", near=["grip-mod-1-blk"]),
    "bcm-g3": R(why="csak BCM Grip Mod 1 van, Mod 3 nincs", near=["grip-mod-1-blk"]),
    # ---- tusok
    "mp-ctr": R(colors={"fde": "stock-ctr-fde"}, title=r"Magpul Stock CTR FDE",
                note="csak FDE színben; a bolt nem jelöli a Mil-Spec/Commercial kivitelt"),
    "mp-moesl": R(why="teljes méretű MOE SL tus nincs, csak MOE Carbine és MOE SL-K",
                  near=["moe-carabine-stock-fde", "moe-sl-k-carabine-stock-blk"]),
    "mp-moeslk": R(colors={"black": "moe-sl-k-carabine-stock-blk", "fde": "moe-sl-k-carabine-stock-fde"},
                   title=r"Magpul MOE SL-K Carabine Stock (BLK|FDE)"),
    "mp-prs3": R(colors={"black": "prs-gen3-stock-black", "fde": "prs-gen3-stock-fde"},
                 title=r"Magpul PRS GEN3 Stock (Black|FDE)"),
    "mp-prslite": R(colors={"black": "prs-lite-stock-bk", "fde": "prs-lite-stock-fde"},
                    title=r"Magpul PRS Lite Stock (BK|FDE)"),
    # ---- red dot / holo
    "ap-t2": R(why="nincs Micro T-2 (+ LRP) a boltban, csak Micro H-1 / H-2 változatok",
               near=["micro-h-2-without-ir-level-2moa-with-picatinny-mount"]),
    "ap-acro": R(why="csak ACRO P-1 + QD Picatinny és ACRO S-2 van, ACRO P-2 nincs",
                 near=["red-dot-acro-p-1-qd-picatiny"]),
    "eo-exps3": R(main="exps3-0", title=r"EoTech EXPS3-0", alts=["exps3-0tan"],
                  note="fekete; a bolt TAN színben is árulja (EXPS3-0TAN)"),
    "eo-xps2": R(why="csak XPS2-2 (két pontos retikül) van, XPS2-0 nincs", near=["xps2-2"]),
    "hs-aems": R(main="aems-advanced-encoldes-micro-red-dot-sight-2moa-65-moa-circle-dot",
                 title=r"HOLOSUN AEMS Advanced Encoldes Micro Red Dot Sight 2MOA 65 MOA Circle Dot",
                 note="eredeti AEMS (2 MOA pont + 65 MOA kör)"),
    "vx-uh1": R(main="red-dot-amg-uh-1-gen-2", title=r"Vortex Red Dot AMG UH-1 Gen 2"),
    "vx-sparc": R(why=VORTEX_UPG, near=["red-dot-sparc-ar-upgrade-2-moa"]),
    "vx-crossfire": R(why=VORTEX_UPG, near=["red-dot-crossfire-upgrade-2moa", "red-dot-crossfire-upgrade-2moa-tan"]),
    # ---- LPVO
    "vx-se16": R(main="shortdot-strike-eagle-1-6x24-ar-bdc3-moa",
                 title=r"Vortex ShortDot Strike Eagle 1-6×24 AR-BDC3 MOA",
                 note="bolti név: 'ShortDot Strike Eagle 1-6x24 AR-BDC3 MOA'; a bolt 'Strike Eagle 1-6x24 Gen 2' "
                      "terméket is árul (470 CHF), annak azonossága leírás hiányában nem igazolt"),
    "vx-se18": R(why="csak 'Strike Eagle 1-8x24 Gen 2' van; leírás nélkül (fókuszsík, retikül) nem azonosítható biztosan",
                 near=["strike-eagle-1-8x24-gen-2"]),
    "tj-acog": R(why="csak LED-es (elemes) ACOG van (4x32 TA51 szerelékkel / 3.5x35), az elem nélküli TA31 nincs",
                 near=["acog-4x32-red-led-223-crosshair-ta51-mount"]),
    # ---- szerelék / nagyító
    "gs-sp30": R(main="super-precision-scope-mount-30mm-black", title=r"Geissele Super Precision Scope Mount -30MM Black",
                 note="alap 'Super Precision 30mm' (az Extended / HyperExtended külön termék); a magasságot a bolti oldal nem írja"),
    "vx-cant30": R(why="a bolti 'Vortex Cantilever 30mm mount' leírás nélkül szerepel (Sport sorozat, magasság, "
                       "előretolás nincs megadva) – nem azonosítható biztosan",
                   near=["cantilever-30-mm-mount"]),
    "hs-hm3x": R(main="magnifier-3x-hm3x", title=r"HOLOSUN Magnifier 3x, HM3X"),
    # ---- BUIS
    "mp-mbus3": R(why=SET_BUIS, near=["sight-mbus-3-front-blk", "sight-mbus-3-rear-blk"]),
    "mp-mbuspro": R(why="MBUS Pro nincs a boltban, csak az eredeti MBUS (külön első/hátsó)", near=["mbus-sight-front"]),
    "mp-mbusoff": R(why=SET_BUIS, near=["mbus-pro-offset-sight-front-black", "mbus-pro-offset-sight-rear-black"]),
    # ---- lámpák
    "sf-m600": R(why="a bolti 'Scout Light Pro' típusjelzése (M600DF / M600U ...) nincs megadva – nem azonosítható biztosan",
                 near=["lampescout-light-pro-blk-arrive-prochainement"]),
    "sf-m640": R(why="a bolt M640U-t árul, a data.js M640DF (Dual Fuel) – más típus", near=["m640u-scout-pro-black"]),
    "sf-m300": R(why="csak M340C Mini Scout van, M300 Mini Scout Light Pro nincs",
                 near=["lampe-m340c-mini-scout-light-blk-arrive-prochainement"]),
    "sl-tlr1": R(why="csak használt (Occasion) TLR-1 van", near=["tlr-1-occasion-bk"]),
    # ---- M-LOK
    "mp-rvg": R(why="csak a Picatinny-s 'RVG Rail Vertical Grip' van, az M-LOK RVG nincs", near=["rvg-rail-vertical-grip-black"]),
    "mp-hstop": R(colors={"black": "hand-stop-kit-m-lok-2"}, title=r"Magpul Hand Stop Kit M-LOK",
                  alts=["hand-stop-kit-m-lok"],
                  note="a bolt egy második, szín nélküli 'Hand Stop Kit M-LOK' terméket is listáz azonos áron"),
    "bcm-vg3": R(why="a 'BCM Vertical Grip Mod 3' rögzítési módja (M-LOK / Picatinny / KeyMod) nincs megadva",
                 near=["vertical-grip-mod-3-blk", "vertical-grip-mod-3-fde"]),
    "bcm-kag": R(colors={"black": "kag-m-lock-blk", "fde": "kag-m-lock-fde"}, title=r"BCM KAG M-LOCk (BLK|FDE)"),
    "mp-rail5": R(why="a 'Magpul Aluminium Rail M-LOK' slotszáma nincs megadva; 5 slotos alumínium sín nem azonosítható",
                  near=["aluminium-rail-m-lok", "m-lok-aluminum-rail-7-slots"]),
    "mp-cover": R(colors={"fde": "m-lok-rail-cover-type-2-fde"}, title=r"Magpul M-LOK Rail cover type 2 FDE",
                  note="csak FDE színben (a fekete/OD csak Type 1)"),
    # ---- bipod
    "mp-bipod": R(main="m-lok-bipod", colors={"fde": "magpul-m-lok-bipod"}, title=r"Magpul (Magpul )?M-LOK Bipod",
                  note="a fő tétel ('M-LOK Bipod') színe nincs megadva; a 'Magpul M-LOK Bipod' TAN (=FDE) színű"),
    "hr-sbrm": R(why="Harris bipod nincs, csak Tac Shield Bipod 6-9\" (más gyártó)", near=["bipod-6-9"]),
    # ---- torkolat
    "sf-warcomp": R(main="39964", title=r"Surefire WARCOMP Flash Hider", need=r"1/2-28",
                    note="1/2-28 UNEF, .223/5.56"),
    # ---- felhúzókar
    "rd-raptor": R(why=RADIAN_CH, near=["levier-d-armement-ambidextre-blk", "levier-d-armement-ambidextre-fde"]),
    "rd-raptorlt": R(why=RADIAN_CH, near=["levier-d-armement-ambidextre-blk"]),
    "rd-raptorsd": R(why=RADIAN_CH, near=["levier-d-armement-ambidextre-blk"]),
    # ---- tárak / szíj
    "mp-pm30": R(why="5.56 Gen M3 30-as PMAG csak 'Coyote' színben van (a bolti 'Couleur' mező FDE-t ír – "
                     "ellentmondásos), ez nem azonosítható a data.js palettájával; a fekete 'PMAG ... 300 Gen M3' "
                     ".300 BLK-hez készült, más termék",
                 near=["pmag-ar-m4-30-coups-556-gen3-coyote", "magasin-pmag-ar-m4-30-coups-300-gen-m3-black"]),
    "mp-pm30w": R(why="csak 'Window Coyote' van (a bolti 'Couleur' mező Noir-t ír – ellentmondásos)",
                  near=["pmag-ar-m4-30-coups-556-gen3-window-coyote"]),
    "mp-pm40": R(colors={"black": "pmag-ar-m4-40-coups-556-gen3-blk"}, title=r"Magpul PMAG AR/M4 40 coups 556 Gen3 BLK",
                 note="bolti 'Gen3' = Gen M3"),
    "mp-d60": R(colors={"black": "ar-15-pmag-d-60"}, title=r"Magpul AR-15 PMAG D-60"),
    "mp-ms1": R(colors={"coyote": "sling-ms1-coyote"}, title=r"Magpul Sling MS1 Coyote",
                note="csak Coyote színben; a bolt nem jelzi, hogy 'Padded' lenne (ára a más boltokban látott sima MS1-árnál jóval magasabb)"),
})


# --------------------------------------------------------------------------- oldal-feldolgozás
def product_urls(http):
    urls = set()
    for sm in SITEMAPS:
        body = http.get(sm, missing_ok=True)
        if not body:
            continue
        urls.update(u for u in re.findall(r"<loc>(https://armashop\.ch/produit/[^<]+)</loc>", body))
    return {u.rstrip("/").rsplit("/", 1)[-1]: u for u in urls}


def parse_page(body, url):
    s_el = soup(body)
    ld = None
    for t in s_el.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except Exception:
            continue
        for g in (d.get("@graph") if isinstance(d, dict) and d.get("@graph") else [d]):
            if isinstance(g, dict) and g.get("@type") == "Product":
                ld = g
    h1 = s_el.select_one("h1")
    title = norm(h1.get_text(" ")) if h1 else None
    txt = norm((s_el.select_one("div.product") or s_el).get_text(" "))
    off = ((ld or {}).get("offers") or [{}])
    off = off[0] if isinstance(off, list) and off else (off if isinstance(off, dict) else {})
    price = float(off["price"]) if off.get("price") else None
    m = re.search(r"Original price was: CHF ([0-9’',.]+)", txt)
    lst = chf(m.group(1)) if m else None
    if lst is not None and price is not None and lst <= price:
        lst = None
    av = (off.get("availability") or "").rsplit("/", 1)[-1] or None
    col = re.search(r"Couleur (\S+)", txt)
    neuf = re.search(r"\bNeuf (Oui|Non)\b", txt)
    return dict(title=title, ld_name=norm((ld or {}).get("name")), price_chf=price, list_chf=lst, url=url,
                image=(ld or {}).get("image"), in_stock=(av == "InStock") if av else None,
                stock_text=("Non disponible" if "Non disponible" in txt[:600] else av),
                sku=str((ld or {}).get("sku")) if (ld or {}).get("sku") not in (None, "") else None,
                color_shop=(col.group(1) if col and col.group(1) != "/" else None),
                new=(neuf.group(1) == "Oui") if neuf else None, text=txt)


def fetch_product(http, urls, slug):
    u = urls.get(slug)
    if not u:
        return None
    body = http.get(u, missing_ok=True)
    return parse_page(body, u) if body else None


def ok_page(pg, rule):
    if not pg or pg["price_chf"] is None:
        return False
    if pg["new"] is False or re.search(r"\bOccasion\b", pg["title"] or ""):
        return False
    if rule["title"] and not re.fullmatch(rule["title"], pg["title"] or "", re.I):
        return False
    if rule["need"] and not re.search(rule["need"], pg["text"], re.I):
        return False
    return True


def rec(pg, note=None):
    return dict(title=pg["title"], price_chf=pg["price_chf"], list_chf=pg["list_chf"], url=pg["url"],
                image=pg["image"], in_stock=pg["in_stock"], stock_text=pg["stock_text"], sku=pg["sku"],
                mpn=None, weight_g=None, dims=None, color_shop=pg["color_shop"])


def decide(http, urls, rule):
    item, cp = None, {}
    if rule["main"]:
        pg = fetch_product(http, urls, rule["main"])
        if ok_page(pg, rule):
            item = rec(pg)
    for col, slug in rule["colors"].items():
        pg = fetch_product(http, urls, slug)
        if ok_page(pg, rule):
            r = rec(pg)
            cp[col] = dict(price_chf=r["price_chf"], list_chf=r["list_chf"], url=r["url"],
                           in_stock=r["in_stock"], title=r["title"])
            if item is None:
                item = dict(r, color=col)
    if item:
        if cp:
            item["colors_prices"] = cp
        alts = []
        for slug in rule["alts"]:
            pg = fetch_product(http, urls, slug)
            if pg and pg["price_chf"] is not None:
                alts.append(dict(title=pg["title"], url=pg["url"], price_chf=pg["price_chf"]))
        if alts:
            item["alternatives"] = alts
        item["match"] = "exact"
        if rule["note"]:
            item["note"] = rule["note"]
        return "ok", item
    reason = "ok: " + (rule["why"] or "nincs a boltban (a termék-sitemapben nincs pontosan azonos termék)")
    near = []
    for slug in rule["near"]:
        pg = fetch_product(http, urls, slug)
        if pg and pg["price_chf"] is not None:
            near.append("%s (%.2f CHF%s, %s)" % (pg["title"], pg["price_chf"],
                                               "" if pg["in_stock"] else ", nincs készleten", pg["url"]))
    if near:
        reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(near)
    return "no", reason


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás, stdout)")
    ap.add_argument("--out", help="kimeneti JSON (alap: assets/prices/armashop.json)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    http = Http(args.cache)
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
               source=BASE + "/product-sitemap*.xml + termékoldalak (JSON-LD)",
               price_basis="a bolt kiírt ára CHF-ben (akciónál price_chf = akciós ár, list_chf = eredeti ár); "
                           "a webshop 'kosara' e-mailes rendelést küld",
               items={}, unmatched={}, blocked=None)
    try:
        urls = product_urls(http)
        sys.stderr.write("Termék-URL-ek (fr): %d\n" % len(urls))
        for pid, brand, name in entries:
            rule = RULES.get(pid) or R(why="nincs illesztési szabály (új termék a data.js-ben?)")
            kind, res = decide(http, urls, rule)
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
