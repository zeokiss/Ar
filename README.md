# AR-15 konfigurátor

Böngészőben futó konfigurátor: kiválasztod az alapfegyvert (Schmeisser AR15 S4F / M4FL / M5FL /
Dynamic), majd márkás kiegészítőket raksz rá (Magpul, BCM, B5 Systems, Geissele, Radian, Aimpoint,
EOTech, Holosun, Trijicon, Vortex, SureFire, Streamlight, Harris, Blue Force Gear). A fegyver
**a gyártó hivatalos fotójából** áll össze, a kiegészítők valódi termékfotói a valós méretarány
szerint kerülnek rá. Közben számolja az árat, a tömeget és a hosszt, és ellenőrzi a kompatibilitást.

## Indítás

```
python3 -m http.server 8765
```

majd: <http://localhost:8765>. (Fájlból megnyitva a fotók nem töltődnek be, szerver kell.)

## Felépítés

| Hely | Mi |
|---|---|
| `js/data.js` | alapfegyverek, kategóriák, ~150 termék (ár, tömeg, szín, adatok) |
| `js/photo-render.js` | fotókompozitáló megjelenítő (alapfotó + alkatrészfotók, kivágó maszkok) |
| `js/render.js` | tartalék, rajzolt vázlatnézet – csak akkor, ha az alapfegyverhez nincs fotó |
| `js/photos.js` | fotókatalógus betöltése (`assets/photos/…` + böngészőben mentett fotók) |
| `js/app.js` | felület, kompatibilitás, ár/tömeg, megosztás, PNG-mentés |
| `assets/photos/base/` | a Schmeisser hivatalos oldalnézeti fotói |
| `assets/photos/manifest.json` | az alapfotók kalibrációja és a fegyver-geometria |
| `assets/photos/parts/`, `packs/` | alkatrészfotók és kalibrációjuk |
| `tools/build_base_assets.py` | alapfotók letöltése, méretarány és geometria kimérése |
| `tools/add_part.py` | alkatrészfotó felvétele – lásd `tools/PARTS_GUIDE.md` |

## Hogyan működik a méretarány

Az alapfotók méretarányát a felső Picatinny sín osztásából mérem ki (0,394" = 10 mm), ez
független ellenőrzés: az így kapott teljes hossz egyezik a gyári adatlappal (M4FL: 89,5 cm, M5FL:
95 cm kihúzott tusnál). A rögzítési pontok (sín, előagy, torkolat, tus, markolat, tár) hüvelykben
vannak tárolva, így minden szín- és modellváltozat ugyanazt a geometriát használja.

## Illesztés-ellenőrzés (tár, markolat)

A termékfotókon a tár és a markolat a saját függőlegesében áll; a puskán a tárnyílás kb. 12 fokkal előre, a markolat kb.
17 fokkal hátra dől. A `tools/fit_parts.py` a gyári alkatrész éleiből (az alapfotó alfa-csatornájából) kimeri a tengelyt, és
minden tár- és markolatfotóhoz beírja a forgatást (`rot`) és az eltolást (`dx`); a többi alapfegyver (pl. a Dynamic, más
render-szög) eltérése a `manifest.json` geo-rekordjának `adj` mezőjébe kerül. A megjelenítő a csere-alkatrészt a tárnyílás /
markolat fölött levágja (nincs kilógó él), és a keskenyebb alkatrész melletti hézagot a tok színével tölti ki
(`tools/base_fill.py`). Kiadás előtt: `python3 tools/check_fit.py` (112 változat: dőlés, középvonal, kilógás, új rések).

## Ami még korlátozott

- **Színváltozatok**: a Schmeisser a fekete kivitelről közöl hivatalos fotót. Az Olive / Dark Earth /
  Bronze kivitelt a hivatalos fekete fotó átszínezésével állítja elő a `tools/recolor_base.py`
  (csak a tok és az előagy kap színt, a bolti fotókról mért Cerakote-színnel); a felület jelzi,
  hogy ez színezett kép, nem hivatalos fotó.
- **Alkatrészfotók**: csak tiszta oldalnézetű, ellenőrizhető méretű termékfotók kerülnek fel. Ahol
  ilyen nincs, szaggatott „?” jelölés mutatja a helyét, a lista az állapotsorban látható.
- A szíj, az elsütő, a felhúzókar és a kezelőszervek nem jelennek meg külön a képen.
- **Árak: kizárólag svájci frank (CHF), kizárólag svájci online shopokból**, az ország egész területéről
  (`assets/prices/<bolt>.json`, összefésülve: `tools/merge_prices.py` → `assets/prices/merged.json`).
  Alapból minden tételnél a legolcsóbb ajánlat számít; tételenként vagy egy boltra is váltható. Becsült vagy
  átváltott ár nincs: ahol nincs bolti ár, ott „nincs ár" látszik. A boltok adatai (cég, cím, szállítás):
  `assets/shops.json` (`tools/build_shops.py`). A kalkulátor senki lakóhelyéhez nem kötött.
- A tömegek a bolti adatlapokból, ennek hiányában kerekített gyári adatokból származnak.

## Jogi megjegyzés

A fotók a gyártók és kereskedők tulajdonában vannak; a projekt személyes, nem kereskedelmi
használatra készült. Lőfegyverek, lényeges alkatrészek, hangtompító és nagy kapacitású tár
beszerzése és birtoklása engedélyhez kötött lehet – ellenőrizd a hatályos szabályozást.
