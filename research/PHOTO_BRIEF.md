# Alkatrészfotók a puskára: közös utasítás a fotó-ügynököknek

Cél: a konfigurátorban a kiválasztott kiegészítő **valódi, kalibrált oldalnézeti termékfotója** kerüljön a puskára
(tools/PARTS_GUIDE.md: **olvasd el először**, abban van az `add_part.py` használata, az anchor-táblázat, a méretarány).
A felhasználó személyes használatra engedélyezte a gyártói és bolti fotók letöltését.

## Mi számít jó fotónak
- **Tiszta oldalnézet** (profilból, nem dőlt, nem három-negyedes), vágható (egységes/világos vagy átlátszó háttér), legalább
  ~600 px a hosszabbik oldalán. A bolti 294x294-es bélyegképek NEM jók (kicsik, ferdék, vízjelesek).
- **Ellenőrizhető valós méret** a méretarányhoz (`--width-in`): gyártói adatlap / termékoldal hossz-adata. Találgatni tilos.
- Ha nincs ilyen: az alkatrész **kimarad** (nincs hamis fotó). Ezt írd a jelentésbe okkal. Inkább kevesebb, de pontos.

## Források (a hálózat csak ezeket engedi)
magpul.com, www.surefire.com, www.streamlight.com, www.holosun.com, www.trijicon.com, www.eotechinc.com, www.radianweapons.com,
www.bcmgunfighter.com, b5systems.com / www.b5systems.com, www.primaryarms.com + image.primaryarms.com (nyilvános termék-API,
korábban innen jöttek oldalnézeti képek), cdn11.bigcommerce.com, cdn.shopify.com, res.cloudinary.com, a svájci boltok (tacstore,
thoron, schiessstand, shooting-store, armamat, tellsarmory, gunlex, armatum…). aimpoint.com, vortexoptics.com, geissele.com,
danieldefense.com **tiltott/botvédett**: ezeket nem lehet és nem szabad megkerülni (a Vortex/Aimpoint termékekhez a Primary Arms
képei vagy a svájci boltok nagyobb képei jöhetnek szóba, ha tiszta oldalnézet van rajtuk).
Egy hostra egyszerre egy kérés, ≥1 s szünettel, böngésző User-Agent. Botvédelem (403, captcha, Cloudflare-kihívás, 429)
**megkerülése TILOS**: hagyd abba azt a hostot, jelentsd.

## Menet
1. A feladatlistád minden termékére: js/data.js (id, név, specs, `style`), keress gyártói/PA/bolti oldalt, találd meg a tiszta
   oldalnézetű képet és a hosszméretet.
2. `python3 tools/add_part.py --item <id> --src <URL|fájl> --facing <left|right> --width-in <..> --anchor <mód> --pack <csomag> [...]`
   Csomagnév: a feladatod neve (pl. `visible1`), így nem ütközöl más ügynökkel: **csak a saját csomagfájlodba írj**
   (`assets/photos/packs/<csomag>.json`), és `assets/photos/parts/` alá képeket. Más fájlhoz ne nyúlj (data.js, app.js, manifest…).
3. **Nézd meg az ellenőrző képet** (`assets/photos/_check/*.png`), és tedd a valódi puskára: a szerver fut a 8765-ös porton
   (`python3 -m http.server 8765` a repó gyökeréből, ha nem). Nagyított kép: a `/tmp/…/scratchpad/qa/zoom.js` helyett használd
   a saját Playwright-szkriptedet (a Chromium előre telepítve), az állapotot a `localStorage['ar15-config']` JSON-nal állítsd:
   `{base, finish, sel:{<kat>:<id>}, multi:{mlok:[<id>]}, colors:{}, pos:{}, shop:{}, mode:'cheapest'}` majd újratöltés.
   Ellenőrizd az illesztést (talp a sínen, nem lebeg, nem lóg, arány, irány). Rosszul illeszkedőt javíts (`--rot`, `--dx`, `--dy`)
   vagy hagyd ki.
4. Színváltozatok: ha a gyártó színenként ad képet, vedd fel a palettakulcsokkal (black, fde, odg, gray, wolfgray, foliage, ranger,
   coyote); egy képből színezni nem kell.
5. Tár és markolat: a felvétel után futtasd `python3 tools/fit_parts.py --only <id>` (a gyári tengelyhez illeszti a dőlést/helyet),
   majd `python3 tools/check_fit.py` (a tár/markolat illesztését méri).
6. Végül: `python3 tools/verify.py` (RENDBEN kell) és `node tools/runtime_check.js`.

## Szabályok
- Csak a saját csomagfájlodba és a `parts/` képekhez írj; **ne commitolj**, ne nyúlj más ügynök fájljaihoz.
- Semmit ne találj ki: méret, forrás, szín csak ellenőrzött lehet. A `--source` mezőbe a pontos oldalt írd.
- A végén rövid magyar jelentés: termékenként FEL / KIMARADT + ok + forrás + a méret forrása.
