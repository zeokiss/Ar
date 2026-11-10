#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gunfactory.ch (Gun Factory Handels AG, Rudolfstetten AG) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_gunfactory.py                      # minden termék -> assets/prices/gunfactory.json
    python3 tools/prices_gunfactory.py --only mp-pm10       # csak néhány id (stdout-ra ír, ha nincs --out)
    python3 tools/prices_gunfactory.py --cache /tmp/gfc     # HTTP-válaszok gyorsítótára (fejlesztéshez)
    python3 tools/prices_gunfactory.py --dump               # az összes beolvasott ajánlat listája (szabályíráshoz)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  A bolt egy 1997-es stílusú, statikus HTML oldal (keretekkel); a főoldal (/) csak a frame-halmaz.
  A termékek kategória-oldalakon vannak, minden vásárolható tételhez egy "Bestellen" gomb tartozik, amelynek
  `bestellen(db, 'kosárnév', 'cikkszám', ár)` hívása hordozza a bolt saját cikkszámát és árát.
    1. A PAGES listában szereplő oldalakat (kb. 20 db, ~30 s) letölti, és kinyeri az ajánlatokat:
       szakasz-cím (a termék leírása), felirat (ár + változat/szín), cikkszám, kosárnév, kiírt ár,
       készletikon (stock_full = "ab Lager lieferbar", stock_alert = "Bestellt / nur noch kleine
       Lagermenge verfügbar", stock_empty = "zur Zeit nicht an Lager"), képfájl.
    2. A RULES táblában id-nként: az oldal, a szakasz-cím mintája (`head`), a fő tétel cikkszáma (`art`),
       a színváltozatok cikkszámai (`colors`). Csak akkor egyezés, ha a cikkszám létezik, a szakasz-cím
       mintája illik, és a kiírt ár megegyezik a kosárba kerülő árral.
    3. Nincs pontos egyezés -> "unmatched" okkal + a legközelebbi bolti tételek (név, ár, oldal).
  Árat soha nem becsül, nem számol, nem vált át. A gunfactory nem ad ki árlistát az alapfegyverekhez
  (azok "Anfrage"-alapúak vagy más márkák); a Schmeisser / DDM4 V7 / RECCE-16 nincs a kínálatban.

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
    from bs4 import BeautifulSoup, NavigableString, Tag
except ImportError:  # pragma: no cover
    sys.exit("Hiányzik a beautifulsoup4: pip install beautifulsoup4 lxml")

SHOP = "gunfactory.ch"
SLUG = "gunfactory"
BASE = "https://www.gunfactory.ch"
HOSTS = ("www.gunfactory.ch", "gunfactory.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", SLUG + ".json")

PAGES = [
    "langwaf/colt_mags.htm", "langwaf/magazin_betamag.htm", "langwaf/colt_pistolgrip.htm",
    "langwaf/colt_rearstock.htm", "langwaf/colt_trigger.htm", "langwaf/accgewehr_sights.htm",
    "langwaf/accgewehr_riemen.htm", "langwaf/accgewehr_frontgrip.htm", "langwaf/accgewehr_keymod.html",
    "langwaf/sniperzweibein.htm", "langwaf/colt_flashhider.htm", "langwaf/colt_handguard.htm",
    "langwaf/colt_mounts.htm", "langwaf/semiautos_denieldefense.htm",
    "optic/booster.htm", "optic/dot_aimpoint.htm", "optic/eotech.htm", "optic/dot_holosun.html",
    "optic/dot_vortex.htm", "optic/trijicon.htm", "faustfw/glock_tacticallight.htm",
]
STOCK = {"stock_full": (True, "ab Lager lieferbar"),
         "stock_alert": (None, "Bestellt / nur noch kleine Lagermenge verfügbar"),
         "stock_empty": (False, "zur Zeit nicht an Lager")}


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
                    return f.read()
        headers = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5"}
        last_err = None
        for attempt in range(3):
            wait = MIN_GAP - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.n += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=40) as r:
                    body = r.read()
                if re.search(rb"cf-chl|challenge-platform|Just a moment\.\.\.|Verifying (you are human|connection)",
                             body[:20000]):
                    raise Blocked("botvédelmi kihívás (%s)" % url)
                if ck:
                    with open(ck, "wb") as f:
                        f.write(body)
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


def decode(raw):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def norm(s):
    s = re.sub(r"[®™©]", "", s or "")
    s = unicodedata.normalize("NFKC", s).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def flat(s):
    """szakasz-cím: a ' | ' tördelések kisimítása"""
    return norm(re.sub(r"\|", " ", s or ""))


# --------------------------------------------------------------------------- ajánlat-kinyerés
BEST = re.compile(r"bestellen\(\s*[^,]+,\s*'((?:[^'\\]|\\.)*)'\s*,\s*'([^']*)'\s*,\s*([0-9.]+)\s*\)")
SHOWN = re.compile(r"CHF\s*([0-9][0-9']*(?:\.[0-9]{1,2})?)\s*\.?\s*-?")
NOT_PRODUCT_IMG = re.compile(r"stock_|logo|/(?:223|9x19|300ba|308|762x39)\.jpg$|new5|bullet", re.I)


def parse_offers(html, path):
    s = BeautifulSoup(html, "html.parser")
    for t in s(["script", "style"]):
        t.decompose()
    page_url = "%s/%s" % (BASE, path)
    tds = []
    for inp in s.select("input[onclick*=bestellen]"):
        td = inp.find_parent("td")
        if td is not None and td not in tds:
            tds.append(td)
    out = []
    for td in tds:
        toks = []
        for el in td.descendants:
            if isinstance(el, NavigableString):
                toks.append(("t", str(el)))
            elif isinstance(el, Tag):
                if el.name == "input" and "bestellen" in (el.get("onclick") or ""):
                    toks.append(("o", el.get("onclick")))
                elif el.name == "img":
                    toks.append(("i", el.get("src") or ""))
                elif el.name in ("br", "p", "hr"):
                    toks.append(("t", " | "))
        head = []
        k = 0
        while k < len(toks) and toks[k][0] != "o":
            if toks[k][0] == "t":
                head.append(toks[k][1])
            k += 1
        head = norm(" ".join(head))
        row = td.find_parent("tr")
        image = None
        if row is not None:
            for im in row.select("img"):
                src = im.get("src") or ""
                if src and not NOT_PRODUCT_IMG.search(src):
                    image = urllib.parse.urljoin(page_url, src)
                    break
        idx = [i for i, t in enumerate(toks) if t[0] == "o"]
        for n, i in enumerate(idx):
            end = idx[n + 1] if n + 1 < len(idx) else len(toks)
            m = BEST.search(toks[i][1])
            seg = toks[i + 1:end]
            label = norm(" ".join(t[1] for t in seg if t[0] == "t"))
            icon = next((re.search(r"(stock_\w+)\.gif", t[1]).group(1) for t in seg
                         if t[0] == "i" and re.search(r"(stock_\w+)\.gif", t[1])), None)
            sm = SHOWN.search(label)
            shown = float(sm.group(1).replace("'", "")) if sm else None
            if not m:
                continue
            out.append(dict(page=path, url=page_url, head=head, label=label, oname=m.group(1), art=m.group(2),
                            cart_price=float(m.group(3)), shown_price=shown, icon=icon, image=image,
                            n_in_block=len(idx)))
    return out


# --------------------------------------------------------------------------- szabályok
def R(art=None, head=None, colors=None, why=None, near=None, note=None):
    """art: a fő tétel bolti cikkszáma; head: regex a szakasz-címre (re.search, kisbetű-független, a '|'
    tördelés nélkül); colors: {szín-kulcs: cikkszám}; near: tájékoztató cikkszámok (unmatched-hez);
    why: indok az unmatched-hez (art=None esetén kötelező); note: megjegyzés a felvett tételhez."""
    return dict(art=art, head=head, colors=colors or {}, why=why, near=near or [], note=note)


RULES = {
    # ---- alapfegyverek: a bolt Schmeisser / BCM RECCE fegyvert nem árul (Daniel Defense igen, de más modellt)
    "schm-s4f": R(why="Schmeisser fegyver nincs a boltban (csak Schmeisser alkatrészek: tár, torkolat, szerelék)"),
    "schm-m4fl": R(why="Schmeisser fegyver nincs a boltban"),
    "schm-m5fl": R(why="Schmeisser fegyver nincs a boltban"),
    "schm-dyn": R(why="Schmeisser fegyver nincs a boltban"),
    "dd-v7": R(why="a DDM4 V7 nincs a boltban (csak DDM4 PDW 7\" .300 BLK és M4A1 RIS III 14.5\")", near=["40955", "41494"]),
    "bcm-recce16": R(why="a BCM RECCE-16 nincs a boltban"),
    # ---- markolatok
    "mp-moek2": R("40859", r"Magpul MOE K2 Grip", colors={"black": "40859", "odg": "42402"},
                  note="a bolt TAN/Desert színben is árulja (29 CHF, 40358) - a 'TAN' nem azonosítható biztosan FDE-ként, ezért nincs felvéve"),
    "mp-miad": R("32742", r"Magpul MIAD .*Full Grip Set Gen 1\.1",
                 note="az AR15-ös MIAD markolat (a bolti oldal: 'Pistolengriffe für alle Halbautomaten Typ AR15/M16/M4'); típus (Type 1/2) nincs külön kiírva"),
    # ---- tusok
    "mp-ubr2": R("32678", r"UBR \(Utility Battle Rifle\) Gen\.2 Einschubschaft Typ AR15 M4",
                 note="a bolti oldal nem nevezi meg a színt"),
    "mp-prs3": R("32305", r"PRS \(Precision Rifle Sniper\) Gen\.3 Festschaft",
                 note="a bolti oldal nem nevezi meg a színt; csak AR15 A1/A2 (rifle) puffercsőre szerelhető"),
    "mp-moesl": R(why="a MOE SL tus nincs a boltban (csak a sima 'MOE Einschubschaft', más modell)", near=["32740"]),
    # ---- red dot / holo / nagyító
    "ap-t2": R(why="csomag (Micro T-2 + LRP szerelék): a bolt a Micro T-2-t szerelék nélkül árulja", near=["8103"]),
    "ap-acro": R(why="csomag (ACRO P-2 + Picatinny talp): a bolt az ACRO P-2-t talp nélkül árulja", near=["42641"]),
    "vx-crossfire": R(why="csomag ('Crossfire Red Dot + magasító'): a bolt a Crossfire-t a saját alacsony Picatinny talpával árulja, a magasító nincs külön feltüntetve", near=["42869"]),
    "eo-exps3": R(why="az EXPS3-0 nincs a boltban (csak EXPS3-DCR és EXPS3-2/3 NV)", near=["42941", "41185"]),
    "eo-xps2": R("29370", r"XPS3\s+XPS2",
                 note="XPS2-0 (nem éjjellátó-kompatibilis kivitel); ugyanabban a szakaszban az XPS3-0 NV is szerepel (32727, nem azonos)"),
    "hs-hm3x": R("25035", r"Holosun HM3X 3x Vergr"),
    "eo-g33": R("31731", r"Eo-Tech 3\S{1,4}x Vergr.*Flip to side",
                note="'EoTech 3¼x Booster (G33) inkl. Flip to side Montage' - a G33.STS-nek megfelelő felszereléssel; a bolti oldal a 'G33.STS' jelölést nem írja ki"),
    "ap-3x": R(why="csomag (3XMag-1 + Flip szerelék): a bolt a 3XMag-1-et szerelék nélkül árulja, a billenő szerelékek külön tételek", near=["42613", "33851"]),
    # ---- BUIS
    "mp-mbus3": R(why="az 'MBUS' szett generációja a bolti oldalon nincs kiírva (nem 'MBUS 3'), nem azonosítható biztosan", near=["32650"]),
    "mp-mbusoff": R(why="ellentmondó ár: a bolti oldalon CHF 299.- szerepel, a kosárba kerülő ár 280.-", near=["24928"]),
    # ---- lámpák
    "sl-tlr1": R("30528", r"Streamlight TLR-1 HL, 1'000",
                 note="a bolt FDE kivitelben is árulja (320 CHF, 33448); a TLR-1 HL oldal a pisztoly-lámpák között szerepel (Picatinny/Glock sínre)"),
    # ---- M-LOK
    "mp-rvg": R(why="az 'M-LOK RVG' nincs a boltban; az RVG Picatinnyre, az MVG M-LOK-ra való, külön termék", near=["34042", "40706"]),
    "mp-hstop": R(why="a Magpul M-LOK Hand Stop Kit nincs a boltban (csak 'UTG AFG M-LOK Handstopp' és 'Magpul XTM')", near=["23932", "33576"]),
    "bcm-vg3": R(why="a 'MOD 3 Vertical Short Grip' (BCM logóval) neve nem azonos a 'Gunfighter Vertical Grip Mod 3'-mal, nem azonosítható biztosan", near=["43820"]),
    "mp-rail5": R(why="a 5 slotos alumínium M-LOK sín oldalán a Magpul márka nincs kiírva (a 'Magpul Picatinny-Schiene M-LOK' polimer, 16 CHF), nem azonosítható biztosan", near=["30242", "28776"]),
    "mp-trigguard": R(why="a 'Magpul Abzugsbügel' (polimer, 12 CHF) neve nem azonos a 'MOE Enhanced Trigger Guard'-dal, nem azonosítható biztosan", near=["30171", "30229"]),
    # ---- bipod
    "hr-sbrm": R("42894", r"Harris Zweibein, das Original! Neue Versionen mit MLOK",
                 note="S-BRM 15-23 cm (6-9\"), M-LOK direktmontázzsal (BRM-MLOK); a 192 CHF-es sima S-BRM (3992) nyakpántcsavarra szerelhető, M-LOK adapter 25 CHF külön - összeget nem számolunk"),
    # ---- tárak
    "mp-pm10": R("33834", r"Magpul PMAG 10 Gen\.3 \.223 Magazin",
                 note="a bolti oldal nem nevezi meg a színt"),
    "mp-pm20": R(why="ellentmondás: a bolti oldal 'PMAG 20 Gen.3'-at ír, de a kosárba kerülő termék neve 'Magpul PMAG Gen.2 Magazin 20rds black' - a generáció nem azonosítható biztosan", near=["32741", "34978"]),
    "mp-pm30": R("8021", r"Magpul PMAG 30 Gen\.3 \.223 Magazin ?, ?30rds.*Mutter", colors={"black": "8021"},
                 note="a sima (ablak nélküli) Gen.3 fekete; a bolt egy Gen.2 fekete tárat is árul (34532, nem azonos)"),
    "mp-pm30w": R("22303", r"Magpul PMAG 30 Gen\.3 \.223 Magazin ?, ?30rds.*Sichtfenster", colors={"black": "22303"},
                  note="a bolt Coyote színben is árulja (29 CHF, 34441) - ez nem szerepel a data.js palettájában (black/fde)"),
    "mp-pm40": R("34305", r"Magpul PMAG 40 Gen\.3 \.223 Magazin",
                 note="a bolti oldal nem nevezi meg a színt"),
    "mp-d60": R("31232", r"PMAG D-60 Kaliber \.223",
                note="a bolti oldal nem nevezi meg a színt"),
    # ---- szíjak
    "mp-ms4": R("5153", r"MS4 Dual QD Multi Mission Sling", colors={"black": "5153"},
                note="a bolti oldal a generációt nem nevezi meg; csak fekete kivitel"),
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


# --------------------------------------------------------------------------- döntés
def short(o):
    return "%s (%s CHF, %s)" % (o["oname"], ("%.2f" % o["shown_price"]) if o["shown_price"] is not None else "?", o["url"])


def fmt_item(o, extra_title=True):
    segs = [x.strip() for x in o["head"].split("|") if x.strip()]
    first = segs[0] if segs else flat(o["head"])[:100]
    if len(first) < 14 and len(segs) > 1:
        first += " " + segs[1]
    first = first.replace("1\u20444", "\u00bc")[:100]
    variant = re.sub(r"^CHF\s*[0-9'.]+\s*\.?\s*-?\s*", "", o["label"]).strip(" |")
    return first + ((" / " + variant[:60]) if (extra_title and variant) else "")


def offer_dict(o):
    in_stock, stock_text = STOCK.get(o["icon"], (None, ""))
    return dict(title=fmt_item(o), price_chf=o["shown_price"], list_chf=None, url=o["url"],
                image=o["image"] if o.get("n_in_block", 1) == 1 else None,
                in_stock=in_stock, stock_text=stock_text, sku=o["art"], mpn=None, weight_g=None, dims=None,
                cart_name=o["oname"])


def decide(pid, brand, name, rule, by_art, offers):
    if not rule:
        toks = [t for t in re.findall(r"[a-z0-9]{3,}", name.lower()) if t not in ("pro", "grip", "stock", "sight")]
        b = brand.lower().replace(" ", "")
        near = [o for o in offers if b in (flat(o["head"]) + o["oname"]).lower().replace(" ", "")
                and any(t in (flat(o["head"]) + o["oname"]).lower() for t in toks)]
        txt = "ok: nincs a boltban (a beolvasott kategória-oldalakon nincs egyező márka+típus tétel)"
        if near:
            seen, shown = set(), []
            for o in near:
                if o["art"] not in seen:
                    seen.add(o["art"])
                    shown.append(o)
            txt += "; legközelebbi bolti tétel(ek), NEM azonos: " + " | ".join(short(o) for o in shown[:4])
        return "no", txt
    if not rule["art"]:
        txt = "ok: " + (rule["why"] or "nincs a boltban")
        near = [by_art[a][0] for a in rule["near"] if a in by_art]
        if near:
            txt += "; legközelebbi bolti tétel(ek), NEM azonos: " + " | ".join(short(o) for o in near[:4])
        return "no", txt
    cand = [o for o in by_art.get(rule["art"], []) if re.search(rule["head"], flat(o["head"]), re.I)]
    if not cand:
        return "no", "ok: a bolti tétel (cikkszám %s) nem található vagy a szakasz-címe megváltozott (%s)" % (
            rule["art"], rule["head"])
    o = cand[0]
    if o["shown_price"] is None or abs(o["shown_price"] - o["cart_price"]) > 0.005:
        return "no", "ok: ellentmondó ár a bolti oldalon (kiírt: %s, kosárba kerülő: %s): %s" % (
            o["shown_price"], o["cart_price"], o["url"])
    item = offer_dict(o)
    item["colors_prices"] = {}
    for key, art in rule["colors"].items():
        cc = [c for c in by_art.get(art, []) if re.search(rule["head"], flat(c["head"]), re.I)]
        if cc and cc[0]["shown_price"] is not None and abs(cc[0]["shown_price"] - cc[0]["cart_price"]) < 0.005:
            d = offer_dict(cc[0])
            item["colors_prices"][key] = dict(price_chf=d["price_chf"], list_chf=None, url=d["url"],
                                              in_stock=d["in_stock"], title=d["title"])
    item["match"] = "exact"
    if rule["note"]:
        item["note"] = rule["note"]
    return "ok", item


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/%s.json; --only mellett stdout)" % SLUG)
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár (fejlesztéshez)")
    ap.add_argument("--dump", action="store_true", help="kilistázza az összes ajánlatot, majd kilép")
    args = ap.parse_args()
    http = Http(args.cache)
    offers, blocked = [], None
    for path in PAGES:
        try:
            raw = http.get("%s/%s" % (BASE, path))
        except Blocked as e:
            blocked = str(e)
            sys.stderr.write("BLOKKOLVA: %s - leállás\n" % e)
            break
        except Exception as e:
            sys.stderr.write("HIBA %s: %s\n" % (path, e))
            continue
        if raw:
            offers += parse_offers(decode(raw), path)
        sys.stderr.write("%s: %d ajánlat összesen\n" % (path, len(offers)))
    if args.dump:
        for o in offers:
            print("%-34s %-7s %8s  %-12s %s | %s" % (o["page"], o["art"], o["shown_price"], o["icon"], flat(o["head"])[:70], o["label"][:50]))
        return
    by_art = {}
    for o in offers:
        by_art.setdefault(o["art"], []).append(o)
    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    items, unmatched = {}, {}
    for pid, brand, name in entries:
        if blocked:
            unmatched[pid] = "ok: a bolt letiltotta a lekérdezést (%s), nem ellenőrizhető" % blocked
            continue
        kind, res = decide(pid, brand, name, RULES.get(pid), by_art, offers)
        if kind == "ok":
            items[pid] = res
            sys.stderr.write("%s -> %s  CHF %s\n" % (pid, res["title"], res["price_chf"]))
        else:
            unmatched[pid] = res
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
