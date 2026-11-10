#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wyn.swiss (Armurerie WYN Sàrl, Ecublens VD) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_wyn.py                   # -> assets/prices/wyn.json
    python3 tools/prices_wyn.py --only hs-510c,mp-d60   # részleges futás (stdout)
    python3 tools/prices_wyn.py --cache /tmp/wyn_cache

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  * A bolt oldala WordPress "portfolio" katalógus (nincs webshop/kosár). A kategóriaoldalak
    (/portfolio-categories/<kat>/page/N/) minden tételnél kiírják:
    "<típus> | Prix de vente: 458.- Chf [au lieu de ...] | Disponibilité: En stock".
    A szkript ezeket a kategóriákat lapozza végig (accessoires, points-rouge, lunettes,
    armes-a-epauler), és tételenként kiolvassa a címet, árat, eredeti árat, elérhetőséget,
    leírást, képet és a tétel URL-jét.
  * Egyes tételek gyűjtőlisták ("ACCESSOIRES AR-15", "BIPIEDS"): ezekben soronként
    "<terméknév> PV: 49.- Chf" szerepel – ezeket külön (al)tételként kezeli, URL-jük a
    gyűjtőtétel oldala.
  * A js/data.js minden azonosítójához a RULES táblában van a bolti tételcím vagy listasor
    PONTOS mintája (fullmatch), szükség esetén színenként és a leírásban kötelező szöveggel.
  * "Sur demande" / "De ... à ..." / "selon le modèle" ár NEM konkrét ár -> nem egyezés.
  * Árat soha nem becsül, nem számol, nem vált át.

Udvariasság: egyetlen szál, kérések között >= 1,3 s, böngésző User-Agent, csak wyn.swiss-ra
kérdez; a WordPress REST API-t (amelynek felfedező végpontja tiltott) NEM használja.
403 / captcha / tartós 429 esetén leáll ("blocked").
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

BASE = "https://wyn.swiss"
CATEGORIES = ["accessoires", "points-rouge", "lunettes", "armes-a-epauler"]
SHOP = "wyn.swiss"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "wyn.json")


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
        if not url.startswith(BASE + "/") or "/wp-json" in url:
            raise ValueError("nem engedélyezett URL: " + url)
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


def num(s):
    return float(re.sub(r"[’'\s]", "", s))


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


# --------------------------------------------------------------------------- katalógus
PRICE_RE = re.compile(r"Prix de vente\s*:?\s*([0-9’'\s]*[0-9](?:\.[0-9]{1,2})?)\s*\.?-?\s*Chf"
                      r"(?:\s*au lieu de\s*([0-9’'\s]*[0-9](?:\.[0-9]{1,2})?)\s*\.?-?\s*Chf)?", re.I)
AVAIL_RE = re.compile(r"Disponibilit[ée]\s*:?\s*(En stock|Sur commande|[A-Za-zéèàç ]{3,30}?)(?=\s+[A-Z]|$)", re.I)
LINE_RE = re.compile(r"\s*(.+?)\s+PV\s*:\s*([0-9’']+(?:\.[0-9]{1,2})?)\s*\.?-?\s*Chf", re.I)


def parse_items(body):
    s_el = soup(body)
    out = []
    for it in s_el.select("div.archive-item"):
        a = it.select_one(".item-details h2 a")
        if not a or not a.get("href"):
            continue
        q = it.select_one("div[id^=quick-]")
        txt = norm(q.get_text(" ")) if q else ""
        m = PRICE_RE.search(txt)
        av = AVAIL_RE.search(txt)
        img = it.select_one(".archive-image img")
        rec = dict(title=norm(a.get_text(" ")), url=a["href"].split("?")[0], text=txt,
                   price_chf=num(m.group(1)) if m else None,
                   list_chf=num(m.group(2)) if m and m.group(2) else None,
                   price_raw=(re.search(r"Prix de vente\s*:?\s*([^|]+)", txt) or [None, None])[1],
                   stock_text=norm(av.group(1)) if av else None,
                   image=img.get("src") if img else None, lines=[])
        if rec["list_chf"] is not None and rec["price_chf"] is not None and rec["list_chf"] <= rec["price_chf"]:
            rec["list_chf"] = None
        tail = txt.split("Disponibilité", 1)[-1]
        tail = re.sub(r"^\s*:?\s*(En stock|Sur commande)\s*", "", tail)
        for lm in LINE_RE.finditer(tail):
            rec["lines"].append(dict(title=norm(lm.group(1)), price_chf=num(lm.group(2))))
        out.append(rec)
    return out


def fetch_catalog(http):
    items = {}
    for cat in CATEGORIES:
        page = 1
        while page <= 80:
            url = BASE + "/portfolio-categories/%s/" % cat + ("" if page == 1 else "page/%d/" % page)
            body = http.get(url, missing_ok=True)
            if not body:
                break
            for rec in parse_items(body):
                items.setdefault(rec["url"], rec)
            if "/portfolio-categories/%s/page/%d/" % (cat, page + 1) not in body:
                break
            page += 1
    return list(items.values())


# --------------------------------------------------------------------------- szabályok
def R(colors=None, main=None, line=None, why=None, near=(), near_lines=(), need=None, note=None, alts=None):
    """colors: {data.js színkulcs: tételcím regex}; main: tételcím regex; line: gyűjtőlista-sor
    regex; why: indok, ha nincs egyezés; near / near_lines: tételcím / listasor regexek a
    'legközelebbi' termékekhez; need: regex a tétel leírásában; alts: tételcím regex
    ugyanazon modell más kiviteleihez (tájékoztató); note: megjegyzés."""
    return dict(colors=colors or {}, main=main, line=line, why=why, near=list(near),
                near_lines=list(near_lines), need=need, note=note, alts=alts)


NOT_SOLD = "nincs a bolt katalógusában (a bolt oldala nem listázza ezt a terméket)"
NO_PRICE = "a bolt oldala nem ír konkrét árat ehhez a változathoz"
LIST_NOTE = "a bolt 'ACCESSOIRES AR-15' gyűjtőlistájáról (PV = prix de vente); szín nincs megadva"

RULES = {k: R(why=NOT_SOLD) for k in (
    "schm-m5fl", "bcm-recce16", "mp-moek2", "mp-moek2p", "mp-miad", "bcm-g3", "b5-t22", "b5-t23",
    "mp-ctr", "mp-moeslk", "mp-ubr2", "mp-prs3", "mp-prslite", "b5-bravo", "b5-esop", "b5-prec",
    "mp-pad030", "mp-pad055", "mp-pad070", "mp-padsl", "ap-m5", "eo-exps2", "eo-xps2", "hs-aems", "hs-403b",
    "vx-uh1", "vx-sparc", "vx-crossfire", "vx-se16", "vx-se18", "vx-pst16", "vx-razor110", "tj-credo16",
    "tj-vcog", "eo-vudu16", "eo-vudu110", "gs-sp30", "vx-cant30", "eo-g43", "ap-3x", "hs-hm3x", "vx-m3x",
    "mp-mbus3", "mp-mbuspro", "mp-mbusoff", "sf-m640", "sf-m300", "sl-hlx", "mp-hstop", "bcm-vg3",
    "mp-qd", "mp-cover", "mp-cable", "sf-warcomp", "sf-3p", "bcm-comp0", "sf-rc2", "rd-raptorlt",
    "bcm-ch4", "bcm-ch3", "gs-ach", "rd-talon", "mp-trigguard", "mp-pm40", "mp-ms1", "mp-ms4", "bfg-vickers")}

GEISSELE_WHY = ("a Geissele elsütőket a bolt csak gyűjtőtételként listázza ('De 340.- Chf à 460.- Chf'), "
                "típusonkénti ár nélkül")
RULES.update({
    # ---- alapfegyverek
    "schm-s4f": R(colors={"olive": r"SCHMEISSER AR-15 S4F 10\.5′ OD GREEN \| OPTIQUE EN OPTION",
                          "bronze": r"SCHMEISSER AR-15 S4F 10\.5′ BRONZE \| OPTIQUE EN OPTION"},
                  need=r"Cal\. 5\.56.*Garde-Mains M-LOK",
                  alts=r"SCHMEISSER AR-15 S4F 10\.5′ \| OPTIQUE EN OPTION",
                  note="M-LOK előagyas S4F (OD Green és Bronze); a bolt fekete 'S4F 10.5′' tétele KeyMod előagyas "
                       "(régebbi kivitel), ezért nem a data.js S4F-je – csak tájékoztatásul az 'alternatives'-ben"),
    "schm-m4fl": R(why="a bolti 'SCHMEISSER AR-15 M4FL 14.5′' tétel színe nincs megadva (az URL-je 'bronze'-ot "
                       "tartalmaz), elérhetősége 'Sur commande'; a többi M4FL változat (BLK-LBL bipodos előagy, "
                       "Limited Edition optikával) más csomag",
                   near=[r"SCHMEISSER AR-15 M4FL 14\.5′.*"]),
    "schm-dyn": R(why="csak 'Dynamic' 10.5\"/14.5\" és 'Dynamic L 16.75′ SILVER' van; az ezüst kivitel nincs a data.js "
                      "palettáján, a 'Dynamic L' és az 'M5FL' azonossága nincs kimondva",
                  near=[r"SCHMEISSER AR-15 DYNAMIC L 16\.75′.*"]),
    "dd-v7": R(why=NOT_SOLD),
    # ---- markolat / tus (gyűjtőlista)
    "mp-moe": R(line=r"MAGPUL Poignée pistolet AR-15 MOE", note=LIST_NOTE),
    "bcm-g0": R(line=r"BCM Poignée pistolet GUNFIGHTER Mod 0", note=LIST_NOTE),
    "mp-moesl": R(line=r"MAGPUL MOE SL Collapsible Stock MIL-SPEC", note=LIST_NOTE + "; Mil-Spec kivitel"),
    "bcm-s0": R(why="a gyűjtőlistán szereplő 'BCM Gunfighter Collapsible Stock MIL-SPEC' Mod-száma nincs megadva",
                near_lines=[r"BRAVO COMPANY AR-15 BCM Gunfighter Collapsible Stock MIL-SPEC"]),
    "bcm-s1": R(why="a gyűjtőlistán szereplő 'BCM Gunfighter Collapsible Stock MIL-SPEC' Mod-száma nincs megadva",
                near_lines=[r"BRAVO COMPANY AR-15 BCM Gunfighter Collapsible Stock MIL-SPEC"]),
    # ---- red dot / holo
    "ap-t2": R(why=NO_PRICE + " ('Sur demande et selon modèle souhaité'), és az LRP szerelékes csomag sincs megadva",
               near=[r"AIMPOINT H2 & T2 2\.0 MOA"]),
    "ap-pro": R(why=NO_PRICE + " ('765.- Chf selon le modèle'), a QRP2 szerelékes kivitel nincs megnevezve",
                near=[r"AIMPOINT PRO"]),
    "ap-acro": R(why="ACRO P-2 csak magában szerepel, Picatinny talpas csomag nincs megadva", near=[r"AIMPOINT ACRO P-2 .*"]),
    "eo-exps3": R(main=r"EOTECH EXPS3-0 NOIR OU TAN", need=r"EXPS3-0",
                  note="a bolt egy tételként listázza fekete vagy tan színben"),
    "hs-510c": R(main=r"HOLOSUN HS510C", need=r"HS510C"),
    "tj-mro": R(why=NO_PRICE + " ('De 778.- Chf à 895.- Chf selon le modèle'); MRO HD + Lower 1/3 nincs megnevezve",
                near=[r"TRIJICON 1×25 MRO.*"]),
    # ---- LPVO
    "tj-acog": R(main=r"TRIJICON ACOG 4×32 BAC RED CROSSHAIR 223/556 BDC",
                 alts=r"TRIJICON ACOG 4×32 BAC RED (CROSSHAIR|CHEVRON) 223/556 BDC FDE",
                 note="4x32 BAC (elem nélküli, kettős megvilágítású) vörös szálkereszt, .223 BDC = TA31 család; a bolti "
                      "oldal a TA31 típusjelet és a szereléket nem írja ki; FDE és chevron változat azonos áron"),
    # ---- nagyító
    "eo-g33": R(why="a bolti 'MAGNIFIER EOTECH G33' leírása nem említi az STS szereléket", near=[r"MAGNIFIER EOTECH G33.*"]),
    # ---- lámpák
    "sf-m600": R(why=NO_PRICE + " ('LAMPES DIVERSES POUR ARMES – Prix de vente: Sur demande'); a lista M600 Ultra "
                      "Scout Light-ot említ, M600DF Scout Light Pro-t nem",
                 near=[r"LAMPES DIVERSES POUR ARMES.*"]),
    "sf-x300": R(why="a bolti 'SUREFIRE X300 ULTRA LED' tételnél az -A/-B (rögzítés) változat nincs megadva",
                 near=[r"LAMPE POUR ARMES SUREFIRE X300 ULTRA LED"]),
    "sl-tlr1": R(why=NO_PRICE + " (a TLR-1 HL csak a 'LAMPES DIVERSES POUR ARMES' listán szerepel, 'Sur demande')",
                 near=[r"LAMPES DIVERSES POUR ARMES.*"]),
    # ---- M-LOK / bipod
    "mp-rvg": R(why="csak a Picatinny RVG és az 'M-LOK RVG Adapter Rail' szerepel (más termékek)",
                near_lines=[r"MAGPUL Picatinny RVG Railed Vertical Grip Black", r"MAGPUL M-LOK RVG Adapter Rail"]),
    "bcm-kag": R(why="csak a KeyMod-os BCM KAG szerepel, az M-LOK-os nem",
                 near_lines=[r"BCM GUN FIGHTER Keymod KAG Angled Grip"]),
    "mp-rail5": R(line=r"MAGPUL M-LOK Rail 5 Slot Picatinny Aluminum", note=LIST_NOTE),
    "mp-bipod": R(why="csak 'Bipied MAGPUL 1913' (Picatinny) szerepel, az M-LOK-os nem",
                  near_lines=[r"Bipied MAGPUL 1913"]),
    "hr-sbrm": R(why="a Harris S-BRM csak magában szerepel ('BIPIEDS' lista), M-LOK adapteres csomag nincs",
                 near_lines=[r"Bipied HARRIS S-BRM.*"]),
    # ---- elsütők
    "gs-ssae": R(why=GEISSELE_WHY, near=[r"KITS DÉTENTES GEISSELE AR-15"]),
    "gs-ssa": R(why=GEISSELE_WHY, near=[r"KITS DÉTENTES GEISSELE AR-15"]),
    "gs-sde": R(why=GEISSELE_WHY, near=[r"KITS DÉTENTES GEISSELE AR-15"]),
    "gs-s3g": R(why=GEISSELE_WHY, near=[r"KITS DÉTENTES GEISSELE AR-15"]),
    # ---- felhúzókar / kezelőszervek
    "rd-raptor": R(line=r"RADIAN WEAPONS Levier de charge RAPTOR ambidextre AR-15", note=LIST_NOTE),
    "rd-raptorsd": R(main=r"LEVIER DE CHARGEMENT AR-15 RADIAN RAPTOR SD",
                     note="a gyűjtőlistán 'RAPTOR ambidextre AR-15 SD Grey' is szerepel, azonos áron (196 CHF)"),
    "mp-bad": R(line=r"MAGPUL BAD Lever", note=LIST_NOTE),
    # ---- tárak
    "mp-pm10": R(line=r"Chargeur MAGPUL AR-15 PMAG GEN M3, Cal\. 5\.56, 10cps", note=LIST_NOTE),
    "mp-pm20": R(line=r"Chargeur MAGPUL AR-15 PMAG GEN M3, Cal\. 5\.56, 20cps", note=LIST_NOTE),
    "mp-pm30": R(line=r"Chargeur MAGPUL AR-15 PMAG GEN M3, Cal\. 5\.56, 30cps", note=LIST_NOTE),
    "mp-pm30w": R(line=r"Chargeur MAGPUL AR-15 PMAG GEN M3, Window, Cal\. 5\.56, 30cps", note=LIST_NOTE),
    "mp-d60": R(main=r"MAGPUL PMAG D-60 AR/M4", need=r"D-60 AR/M4",
                note="külön termékoldal (209 CHF); az 'ACCESSOIRES AR-15' gyűjtőlista ugyanerre 189 CHF-et ír – "
                     "a külön (frissebb) tétel árát vettük"),
})


# --------------------------------------------------------------------------- egyeztetés
def avail(rec):
    st = (rec.get("stock_text") or "").lower()
    return True if st == "en stock" else (False if st else None)


def item_from(rec, title=None, price=None):
    """rec: katalógustétel; title/price: gyűjtőlista-sor esetén a sor neve és ára."""
    return dict(title=title or rec["title"], price_chf=price if price is not None else rec["price_chf"],
                list_chf=rec["list_chf"] if title is None else None, url=rec["url"], image=rec["image"],
                in_stock=avail(rec), stock_text=rec.get("stock_text"), sku=None, mpn=None,
                weight_g=None, dims=None)


def decide(rule, catalog):
    def find(rx):
        return [r for r in catalog if re.fullmatch(rx, r["title"], re.I) and r["price_chf"] is not None
                and (not rule["need"] or re.search(rule["need"], r["text"], re.I))]

    def find_line(rx):
        out = []
        for r in catalog:
            for ln in r["lines"]:
                if re.fullmatch(rx, ln["title"], re.I):
                    out.append((r, ln))
        return out

    item = None
    if rule["colors"]:
        cp = {}
        for col, rx in rule["colors"].items():
            hits = find(rx)
            if hits:
                h = hits[0]
                cp[col] = dict(price_chf=h["price_chf"], list_chf=h["list_chf"], url=h["url"],
                               in_stock=avail(h), title=h["title"])
                if item is None:
                    item = dict(item_from(h), color=col)
        if item:
            item["colors_prices"] = cp
    elif rule["main"]:
        hits = find(rule["main"])
        if hits:
            item = item_from(hits[0])
    elif rule["line"]:
        hits = find_line(rule["line"])
        if hits:
            r, ln = hits[0]
            item = item_from(r, title=ln["title"], price=ln["price_chf"])
            item["list_title"] = r["title"]
    if item:
        if rule["alts"]:
            alts = [dict(title=r["title"], url=r["url"], price_chf=r["price_chf"]) for r in catalog
                    if re.fullmatch(rule["alts"], r["title"], re.I) and r["url"] != item["url"]
                    and r["price_chf"] is not None]
            if alts:
                item["alternatives"] = alts
        item["match"] = "exact"
        if rule["note"]:
            item["note"] = rule["note"]
        return "ok", item
    reason = "ok: " + (rule["why"] or NOT_SOLD)
    near = []
    for rx in rule["near"]:
        for r in catalog:
            if re.fullmatch(rx, r["title"], re.I):
                near.append("%s (%s, %s)" % (r["title"], ("%.2f CHF" % r["price_chf"]) if r["price_chf"] is not None
                                             else "ár: " + norm(r["price_raw"] or "?"), r["url"]))
    for rx in rule["near_lines"]:
        for r, ln in find_line(rx):
            near.append("%s (%.2f CHF, lista: %s, %s)" % (ln["title"], ln["price_chf"], r["title"], r["url"]))
    if near:
        reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(near[:4])
    return "no", reason


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás, stdout)")
    ap.add_argument("--out", help="kimeneti JSON (alap: assets/prices/wyn.json)")
    ap.add_argument("--cache", help="HTTP gyorsítótár könyvtár")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e[0] in wanted]
    http = Http(args.cache)
    doc = dict(shop=SHOP, currency="CHF", fetched=datetime.date.today().isoformat(),
               source=BASE + "/portfolio-categories/{%s}/ (katalógus-oldalak, 'Prix de vente')" % ",".join(CATEGORIES),
               price_basis="a bolt oldalán kiírt 'Prix de vente' (CHF); 'au lieu de' = list_chf. Nincs webshop/kosár: "
                           "rendelés e-mailben vagy személyesen (csak előzetes egyeztetéssel).",
               items={}, unmatched={}, blocked=None)
    try:
        catalog = fetch_catalog(http)
        sys.stderr.write("Katalógus: %d tétel, %d listasor\n" % (len(catalog), sum(len(r["lines"]) for r in catalog)))
        for pid, brand, name in entries:
            rule = RULES.get(pid) or R(why="nincs illesztési szabály (új termék a data.js-ben?)")
            kind, res = decide(rule, catalog)
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
