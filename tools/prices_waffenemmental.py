#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
waffenemmental.ch (Waffen Emmental GmbH, Langnau i.E. BE) ár-gyűjtő az AR-15 konfigurátorhoz.

Futtatás (a repo gyökeréből):
    python3 tools/prices_waffenemmental.py                    # minden termék -> assets/prices/waffenemmental.json
    python3 tools/prices_waffenemmental.py --only mp-pm30,mp-pm10   # csak néhány id (stdout, ha nincs --out)
    python3 tools/prices_waffenemmental.py --cache /tmp/wecache     # HTTP-gyorsítótár (fejlesztéshez)

Függőség: Python 3.8+, beautifulsoup4 (+ lxml, ha van).

Működés
  * A bolt kereső- és listaoldalait egy külső szolgáltatás (Algolia) tölti a böngészőben, ezért
    a szkript NEM a keresőt használja, hanem a bolt saját, nyilvános termék-sitemapjét
    (/sitemap.xml -> item-sitemap.xml, kb. 800 termék-URL). Csak a waffenemmental.ch hostra kérdez.
  * id-nként a RULES 'slug' mintája (re.search a termék-URL utolsó szakaszán) választja ki a
    jelölteket; ezeknek a termékoldalát tölti le (JSON-LD Product: név, ár CHF, készlet, állapot,
    cikkszám, kép; a „Spezifikationen” táblából a szín / gyártó / modell).
  * Egyezés: a bolti terméknév PONTOS (fullmatch) mintája + új állapot (NewCondition) + elérhető
    ajánlat. Használt / egyedi (sorozatszámos) fegyver, nem elérhető tétel: nem egyezés.
  * A bolt a színváltozatokat külön URL-en tartja (Spezifikationen: Farbe) -> colors_prices.
  * Nincs egyezés -> 'unmatched' okkal (+ legközelebbi bolti termék(ek)). Árat soha nem becsül.

Udvariasság: egy szálon, kérések között >= 1,1 s, böngésző User-Agent; 429/5xx esetén visszalépés.
403 / Cloudflare-kihívás / captcha esetén AZONNAL leáll ('blocked'), nem próbálja megkerülni.
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

BASE = "https://waffenemmental.ch"
HOSTS = ("waffenemmental.ch", "www.waffenemmental.ch")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
MIN_GAP = 1.1

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_JS = os.path.join(ROOT, "js", "data.js")
OUT_JSON = os.path.join(ROOT, "assets", "prices", "waffenemmental.json")


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
        headers = {"User-Agent": UA, "Accept-Language": "de-CH,de;q=0.9,en;q=0.5"}
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
                # (az oldalakon lévő Turnstile-szkript az űrlapokhoz tartozik; kihívásoldal = blokkolás)
                if re.search(r"cf-chl|challenge-platform/h/|Just a moment\.\.\.|Verifying (you are human|connection)",
                             body[:20000], re.I):
                    raise Blocked("bot-védelmi oldal (challenge): " + url)
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
    s = unicodedata.normalize("NFKC", s or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- data.js
def read_data_js(path=DATA_JS):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    consts = {m.group(1): re.findall(r"'([^']+)'", m.group(2))
              for m in re.finditer(r"const\s+(\w+)\s*=\s*\[([^\]]*)\]", src)}
    out = []
    for m in re.finditer(r"\{\s*id:\s*'([^']+)'", src):
        i, j, depth, instr = m.start(), m.start(), 0, False
        while j < len(src):
            c = src[j]
            if instr:
                if c == "\\":
                    j += 1
                elif c == "'":
                    instr = False
            elif c == "'":
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = src[i:j + 1]

        def sfield(k):
            mm = re.search(r"\b%s:\s*'((?:[^'\\]|\\.)*)'" % k, body)
            return mm.group(1).replace("\\'", "'") if mm else None

        brand, name = sfield("brand"), sfield("name")
        if not brand or not name:
            continue
        pal = None
        mm = re.search(r"\b(colors|finishes):\s*(\w+|\[[^\]]*\])", body)
        if mm:
            v = mm.group(2)
            pal = re.findall(r"'([^']+)'", v) if v.startswith("[") else consts.get(v)
        out.append(dict(id=m.group(1), cat=sfield("cat") or "base", brand=brand, name=name,
                        palette=pal, is_base=sfield("cat") is None))
    return out


COLOR_WORDS = [
    (r"schwarz|black", "black"), (r"flat dark earth|fde", "fde"), (r"dark earth", "earth"),
    (r"od ?green|odg|olive drab|oliv(e|grün)?", "odg"), (r"stealth gr[ae]y|gr[ae]y|grau", "gray"),
    (r"wolf gr[ae]y", "wolfgray"), (r"foliage( green)?", "foliage"), (r"ranger green|ranger", "ranger"),
    (r"coyote( brown)?", "coyote"), (r"bronze", "bronze"),
]


def color_key(label, palette, is_base=False):
    if not label or not palette:
        return None
    lab = norm(label).lower()
    for pat, key in COLOR_WORDS:
        if re.fullmatch(pat, lab):
            if is_base:
                key = {"odg": "olive", "fde": "fde" if "fde" in palette else "earth"}.get(key, key)
            return key if key in palette else None
    return None


# --------------------------------------------------------------------------- szabályok
def R(slug=None, title=None, near=None, why=None, note=None):
    """slug: minta a termék-URL-re (re.search) a jelöltekhez; title: a bolti név PONTOS mintája
    (fullmatch) – None: nincs azonos termék; near: slug-minta a tájékoztató 'legközelebbi' tételekhez."""
    return dict(slug=slug, title=title, near=near, why=why, note=note)


NOT_SOLD = "nincs a boltban (a ~800 tételes termék-sitemapben nincs ilyen márkájú/típusú termék)"
NOT_FOUND = "nincs a boltban (a termék-sitemapben nincs pontosan azonos termék)"
FIREARM = "ez a puskamodell nincs a bolt kínálatában"
MAG = r"5,56 × 45 mm NATO / \.223 Remington"

RULES = {
    # ---- alapfegyverek
    "schm-s4f": R(r"schmeisser-ar-?15-s4f", r"Schmeisser, AR15 S4F.*", near=r"schmeisser-ar-?15", why=FIREARM),
    "schm-m4fl": R(r"schmeisser-ar-?15-m4fl", r"Schmeisser, AR15 M4FL.*", near=r"schmeisser-ar-?15",
                   why="nincs elérhető új AR15 M4FL a boltban"),
    "schm-m5fl": R(r"schmeisser-ar-?15-m5fl", r"Schmeisser, AR15 M5FL.*", near=r"schmeisser-ar-?15", why=FIREARM),
    "schm-dyn": R(r"schmeisser-ar-?15-dynamic", r"Schmeisser, AR15 Dynamic.*", near=r"schmeisser-ar-?15", why=FIREARM),
    "dd-v7": R(r"daniel-defense|ddm4", None, why=NOT_SOLD),
    "bcm-recce16": R(r"bcm|recce", None, why=NOT_SOLD),
    # ---- tárak
    "mp-pm10": R(r"magpul-pmag-10-gen-m3-ar-m4", r"Magazin Magpul PMAG 10 Schuss Gen M3 AR/M4 " + MAG),
    "mp-pm20": R(r"magpul-pmag-20-gen-m3-ar-m4", r"Magazin Magpul PMAG 20 Schuss Gen M3 AR/M4 " + MAG,
                 near=r"magpul-pmag-20", why="a PMAG 20 AR/M4 Gen M3 nincs a boltban"),
    "mp-pm30": R(r"magpul-pmag-30-gen-m3-ar-m4", r"Magazin Magpul PMAG 30 Schuss Gen M3 AR/M4 " + MAG),
    "mp-pm30w": R(r"magpul-pmag-30-gen-m3-window-ar-m4", r"Magazin Magpul PMAG 30 Schuss Gen M3 Window AR/M4 " + MAG,
                  note="FIGYELEM: a bolti termékleírás szövege tévesen egy szénszálas (CF) tárról szól; a terméknév "
                       "és a gyártó (Magpul PMAG 30 Gen M3 Window AR/M4) egyértelmű. A bolt 'Coyote' színe nem "
                       "azonos a data.js FDE színével, ezért nincs felvéve."),
    "mp-pm40": R(r"magpul-pmag-40", None, why=NOT_FOUND),
    "mp-d60": R(r"magpul-.*d-?60", None, why=NOT_FOUND),
    # ---- szíj
    "mp-ms4": R(r"magpul.*ms4", r"Gewehrriemen Magpul (MS4 Dual QD|Dual QD.*MS4).*", near=r"magpul.*ms4",
                why="az 'MS4 Dual QD Sling' nincs a boltban; a 'Magpul QDM Sling MS4' (MS4 QDM) a leírás szerint "
                    "QDM forgókarabinerekkel szerelt, más termék"),
    "mp-ms1": R(r"magpul.*ms1", None, why=NOT_FOUND),
    # ---- egyebek, ahol a sitemapben van rokon tétel (tájékoztatásul)
    "mp-rvg": R(r"magpul.*(rvg|vertical-grip)", r".*Magpul (M-LOK )?RVG.*", near=r"magpul.*(rvg|mvg)",
                why="az M-LOK RVG nincs a boltban; a 'Magpul MVG Vertical Grip' más termék"),
    "sl-tlr1": R(r"streamlight.*tlr-1", None, near=r"streamlight", why="a TLR-1 HL nincs a boltban (csak TLR-7 X, TLR-RM 2)"),
    "sl-hlx": R(r"streamlight.*protac", None, near=r"streamlight", why="a ProTac Rail Mount HL-X nincs a boltban"),
    "hs-510c": R(r"holosun.*510", None, near=r"holosun", why=NOT_FOUND),
    "hs-aems": R(r"holosun.*aems", None, near=r"holosun", why=NOT_FOUND),
    "hs-403b": R(r"holosun.*403", None, near=r"holosun", why=NOT_FOUND),
    "hs-hm3x": R(r"holosun.*hm3x", None, near=r"holosun", why=NOT_FOUND),
}

# márka -> slug-minta (ha nincs külön szabály: a márka nincs a kínálatban / nincs azonos termék)
BRAND_SLUG = {
    "Magpul": r"magpul", "BCM": r"\bbcm\b", "B5 Systems": r"b5-systems", "Geissele": r"geissele",
    "Radian": r"radian", "Aimpoint": r"aimpoint", "EOTech": r"eotech", "Holosun": r"holosun",
    "Trijicon": r"trijicon", "Vortex": r"vortex", "SureFire": r"surefire", "Streamlight": r"streamlight",
    "Harris": r"harris", "Blue Force Gear": r"blue-force", "Schmeisser": r"schmeisser",
    "Daniel Defense": r"daniel-defense",
}


# --------------------------------------------------------------------------- katalógus
def sitemap_urls(http):
    urls, todo, seen = [], [BASE + "/sitemap.xml"], set()
    while todo:
        u = todo.pop(0)
        if u in seen:
            continue
        seen.add(u)
        body = http.get(u)
        locs = [htmlunescape(x) for x in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", body)]
        if "<sitemapindex" in body:
            todo.extend(l for l in locs if urllib.parse.urlparse(l).netloc in HOSTS)
        else:
            urls.extend(l for l in locs if "/shop/item/" in l)
    return urls


def htmlunescape(s):
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def url_slug(u):
    return u.rstrip("/").rsplit("/", 1)[-1].lower()


def parse_page(body, url):
    s = soup(body)
    prod = None
    for t in s.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(t.string or "")
        except Exception:
            continue
        if isinstance(d, dict) and d.get("@type") == "Product":
            prod = d
            break
    specs = {}
    for d in s.find_all("div"):
        kids = d.find_all("div", recursive=False)
        if len(kids) == 2 and not kids[0].find("div") and not kids[1].find("div"):
            k, v = norm(kids[0].get_text(" ")), norm(kids[1].get_text(" "))
            if k and v and len(k) < 40:
                specs.setdefault(k, v)
    h1 = s.find("h1")
    title = norm(h1.get_text(" ")) if h1 else None
    for t in s(["script", "style"]):
        t.decompose()
    text = norm(s.get_text(" "))
    out = dict(url=url, title=title, specs=specs, available=False, price_chf=None, in_stock=None,
               condition=None, sku=None, image=None, stock_text=None, brand=specs.get("Hersteller"))
    if "nicht mehr verfügbar" in text:
        out["stock_text"] = "Dieser Artikel ist nicht mehr verfügbar."
    if prod:
        o = prod.get("offers") or {}
        if isinstance(o, list):
            o = o[0] if o else {}
        out["title"] = norm(prod.get("name")) or title
        out["price_chf"] = round(float(o["price"]), 2) if o.get("price") not in (None, "") else None
        av = (o.get("availability") or "").rsplit("/", 1)[-1]
        out["in_stock"] = True if av in ("InStock", "LimitedAvailability") else (False if av else None)
        out["condition"] = (o.get("itemCondition") or "").rsplit("/", 1)[-1] or None
        out["sku"] = prod.get("sku")
        img = prod.get("image")
        out["image"] = img[0] if isinstance(img, list) and img else (img if isinstance(img, str) else None)
        out["available"] = out["price_chf"] is not None and not out["stock_text"]
        # a változat saját cikkszáma a cím után áll (a JSON-LD-ben a szülőé lehet)
        if out["title"]:
            m = re.search(re.escape(out["title"]) + r"\s+([A-Za-z0-9\-]+)\s+(Farbe|Verfügbar|Nicht|Auf|Ausverkauft)", text)
            if m and m.group(1) != out["sku"]:
                out["variant_sku"] = m.group(1)
        m = re.search(r"\b(Verfügbar(?: Solange Vorrat)?|Nicht verfügbar|Ausverkauft|Auf Bestellung[^0-9]{0,40})\b", text)
        if m and not out["stock_text"]:
            out["stock_text"] = m.group(1)
    return out


# --------------------------------------------------------------------------- döntés
def decide(http, urls, entry, rule, pages):
    def fetch(u):
        if u not in pages:
            pages[u] = parse_page(http.get(u), u)
        return pages[u]

    if rule is None:
        bs = BRAND_SLUG.get(entry["brand"])
        hits = [u for u in urls if bs and re.search(bs, url_slug(u))]
        why = NOT_SOLD if not hits else NOT_FOUND
        return "no", "ok: " + why
    cands = [u for u in urls if rule["slug"] and re.search(rule["slug"], url_slug(u))]
    matched, rejected = [], []
    for u in cands:
        p = fetch(u)
        ok_title = bool(rule["title"] and p["title"] and re.fullmatch(rule["title"], p["title"], re.I))
        if ok_title and p["available"] and p["condition"] in (None, "NewCondition"):
            matched.append(p)
        else:
            rejected.append(p)
    if not matched:
        reason = "ok: " + (rule["why"] or NOT_FOUND)
        near_urls = [u for u in urls if rule["near"] and re.search(rule["near"], url_slug(u))]
        near = []
        for u in near_urls[:6]:
            p = fetch(u)
            extra = []
            if p["condition"] and p["condition"] != "NewCondition":
                extra.append({"UsedCondition": "használt"}.get(p["condition"], p["condition"]))
            if not p["available"]:
                extra.append("nem elérhető" + (": " + p["stock_text"] if p["stock_text"] else ""))
            near.append("%s (%s CHF%s, %s)" % (p["title"] or url_slug(u),
                                                ("%.2f" % p["price_chf"]) if p["price_chf"] is not None else "?",
                                                ("; " + ", ".join(extra)) if extra else "", u))
        if near:
            reason += "; legközelebbi bolti termék(ek), NEM azonos: " + " | ".join(near)
        return "no", reason
    pal, is_base = entry["palette"], entry["is_base"]
    for p in matched:
        p["color_label"] = p["specs"].get("Farbe")
        p["color"] = color_key(p["color_label"], pal, is_base)
    prim = None
    for key in (pal or []):
        c = [p for p in matched if p["color"] == key]
        if c:
            prim = min(c, key=lambda p: p["price_chf"])
            break
    if prim is None:
        nocol = [p for p in matched if not p["color_label"]]
        prim = min(nocol or matched, key=lambda p: p["price_chf"])
    item = dict(title=prim["title"], price_chf=prim["price_chf"], list_chf=None, url=prim["url"],
                image=prim["image"], in_stock=prim["in_stock"], stock_text=prim["stock_text"],
                sku=prim.get("variant_sku") or prim["sku"], mpn=None, weight_g=None, dims=None)
    if prim.get("color"):
        item["color"] = prim["color"]
    cp, other = {}, []
    for p in matched:
        if p["color"]:
            if p["color"] not in cp or p["price_chf"] < cp[p["color"]]["price_chf"]:
                cp[p["color"]] = dict(price_chf=p["price_chf"], list_chf=None, url=p["url"], in_stock=p["in_stock"],
                                      title="%s (%s)" % (p["title"], p["color_label"]), stock_text=p["stock_text"],
                                      sku=p.get("variant_sku") or p["sku"])
        elif p["color_label"]:
            other.append("%s (%.2f CHF, %s)" % (p["color_label"], p["price_chf"], p["url"]))
    if cp:
        item["colors_prices"] = {k: cp[k] for k in (pal or []) if k in cp}
    if other:
        item["variants_not_in_palette"] = sorted(set(other))
    item["match"] = "exact"
    if rule.get("note"):
        item["note"] = rule["note"]
    return "ok", item


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="vesszővel elválasztott id-k (részleges futás)")
    ap.add_argument("--out", help="kimeneti JSON (alapértelmezett: assets/prices/waffenemmental.json; --only mellett stdout)")
    ap.add_argument("--cache", help="HTTP-gyorsítótár könyvtár (fejlesztéshez)")
    args = ap.parse_args()

    entries = read_data_js()
    if args.only:
        wanted = set(args.only.split(","))
        entries = [e for e in entries if e["id"] in wanted]
    http = Http(args.cache)
    items, unmatched, blocked, pages = {}, {}, None, {}
    try:
        urls = sitemap_urls(http)
        sys.stderr.write("termék-URL-ek a sitemapben: %d\n" % len(urls))
    except Blocked as b:
        urls, blocked = [], str(b)
    for i, e in enumerate(entries if not blocked else [], 1):
        sys.stderr.write("[%d/%d] %s  (%s %s)\n" % (i, len(entries), e["id"], e["brand"], e["name"]))
        try:
            kind, res = decide(http, urls, e, RULES.get(e["id"]), pages)
        except Blocked as b:
            blocked = str(b)
            sys.stderr.write("   BLOKKOLVA: %s – leállás\n" % b)
            break
        except Exception as ex:
            unmatched[e["id"]] = "ok: hiba a lekérdezéskor, nem sikerült ellenőrizni (%s)" % ex
            sys.stderr.write("   HIBA: %s\n" % ex)
            continue
        if kind == "ok":
            items[e["id"]] = res
            sys.stderr.write("   -> %s  CHF %s  %s\n" % (res["title"], res["price_chf"], sorted(res.get("colors_prices", {}))))
        else:
            unmatched[e["id"]] = res
    if blocked:
        items, unmatched = {}, {e["id"]: "ok: a bolt botvédelmet / tiltást adott, a gyűjtés leállt" for e in entries}
    doc = dict(shop="waffenemmental.ch", currency="CHF", fetched=datetime.date.today().isoformat(),
               items=items, unmatched=unmatched, blocked=blocked)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    out = args.out or (None if args.only else OUT_JSON)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("Kész: %s  (%d egyezés, %d kimaradt, %d HTTP kérés)\n" % (
            out, len(items), len(unmatched), http.n))
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
