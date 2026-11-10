# Bolti árgyűjtés – közös utasítás az ár-ügynököknek

Cél: a konfigurátor minden termékéhez (js/data.js: `BASES` és `ITEMS`) a bolt **saját oldalán kiírt, pontosan
egyező** termék CHF-árának összegyűjtése. A felhasználó Svájcban él, csak CHF-et használ.

## Kötelező szabályok

1. **Csak pontos egyezés.** Ugyanaz a márka, modell, típusjelzés és kiszerelés (pl. PMAG 30 *Gen M3* ≠ Gen M2;
   ACOG *TA31* 4x32 ≠ TA33; „Micro T-2 + LRP szerelék” csomag ≠ T-2 szerelék nélkül; MBUS „szett” = első+hátsó
   együtt). A data.js `name` és `specs` mezője dönt. Ha nem biztos: **nem egyezés**; az `unmatched`-be kerül
   okkal és a legközelebbi bolti termék(ek) nevével, árával, URL-jével („NEM azonos”).
2. **Csak a bolt kiírt ára.** Akciós ár esetén `price_chf` = a ténylegesen fizetendő ár, `list_chf` = áthúzott
   ár. „ab …” (változattól függő) ár esetén a konkrét változat árát kell kiolvasni. Árat **soha nem becsülsz,
   nem számolsz, nem váltasz át**. EUR-ár nem kerülhet be.
3. **Színváltozatok**: a data.js palettáinak kulcsaival (`black, fde, odg, gray, wolfgray, foliage, ranger,
   coyote`; az alapfegyvereknél a kivitel: `black, olive, earth, bronze, fde`). A `colors_prices` minden értéke
   objektum: `{"price_chf": .., "list_chf": .., "url": .., "in_stock": .., "title": ..}`. A fő `price_chf` a fekete
   (vagy az első palettaszín) ára. Csak azt a színt vedd fel, amelyik ténylegesen kapható a boltban.
   Magpul színek: Black=black, FDE/Flat Dark Earth=fde, ODG/OD Green/Olive Drab=odg, Stealth Gray/Gray=gray.
   BCM: Black, FDE, Foliage Green=foliage, Wolf Gray=wolfgray. Schmeisser: Black, OD Green/Olive=olive,
   Dark Earth/FDE→ a data.js `FINISHES` szerint (earth = Dark Earth), Bronze=bronze.
4. **Udvariasság**: boltonként egyszerre egy kérés, kérések között ≥1 s szünet; böngésző User-Agent.
   Nincs bejelentkezés, kosár, pénztár, űrlapküldés.
5. **Botvédelem megkerülése TILOS.** Ha a bolt Cloudflare/„Verifying connection”/captcha oldalt, 403-at vagy
   tartós 429-et ad, **azonnal hagyd abba azt a boltot**, ne próbálkozz headless böngészővel, más
   User-Agenttel, sütikkel vagy más trükkel. Írd le az okot (`blocked`).
6. Csak a megadott fájlokba írj (lásd lent). Ne commitolj, ne nyúlj más fájlhoz.

## Kimenet boltonként (slug = a domain TLD nélkül, pl. `tellsarmory`, `ellie-firearms`, `indoorswiss`)

1. `assets/prices/<slug>.json`:
   ```json
   {"shop": "<domain, pl. tellsarmory.ch>", "currency": "CHF", "fetched": "YYYY-MM-DD",
    "items": {"<data.js id>": {"title": "<bolti terméknév>", "price_chf": 0.0, "list_chf": null,
              "url": "https://…", "image": "https://…", "in_stock": true|false|null,
              "stock_text": "…", "sku": "…", "mpn": "…", "weight_g": null, "dims": null,
              "colors_prices": {"fde": {"price_chf": 0.0, "list_chf": null, "url": "…", "in_stock": true, "title": "…"}}}},
    "unmatched": {"<id>": "ok: <miért nincs egyezés> [; legközelebbi bolti termék(ek), NEM azonos: név (ár CHF, URL) | …]"},
    "blocked": null}
   ```
   Minden data.js id szerepeljen vagy az `items`-ben, vagy az `unmatched`-ben. Ha a bolt teljesen elérhetetlen /
   botvédett: `"items": {}`, `"blocked": "<ok>"`.
2. `tools/prices_<slug>.py`: újrafuttatható gyűjtő (a `tools/prices_armamat.py` a minta: keresőkifejezés +
   pontos név-minta id-nként; Python 3 standard könyvtár + beautifulsoup4). Ha a bolt motorjának van nyilvános
   JSON-végpontja (WooCommerce Store API `/wp-json/wc/store/v1/products?search=`, Shopify `/search/suggest.json`,
   `/products/<handle>.js`, Shopware, Magento kereső), azt használd.
3. `research/shopinfo/<slug>.json` – a bolt adatai **a bolt saját oldaláról** (Impressum / Kontakt / Versand /
   AGB), mindegyikhez forrás-URL:
   ```json
   {"domain": "…", "url": "https://…", "name": "<cégnév>", "addr": "<utca, PLZ hely>", "city": "<hely + kanton-rövidítés>",
    "canton": "ZH", "tel": "+41 …", "kind": "online|store|both", "ships": true|false|null,
    "shipNote": "<magyarul, 1–2 mondat, csak amit az oldal ténylegesen leír: szállítási díj, ingyenes határ, idő>",
    "km": <közúti km 8805 Richterswil-től, egész szám>, "km_est": true,
    "online_prices": true|false, "sources": ["https://…"], "notes": "…"}
   ```
   A `ships` csak akkor `true`, ha a bolt oldala leírja, hogy Svájcon belül postáz/szállít (fegyvernél és
   „wesentliche Bestandteile”-nél a szállítás engedélyhez kötött – ezt írd a shipNote-ba, ha az oldal említi).
   A `km` becslés (a PLZ alapján, közúton), ezért `km_est: true`.

## Végén

Rövid jelentés magyarul: boltonként hány pontos egyezés, hány unmatched, blokkolt-e, és a gyanús esetek.
