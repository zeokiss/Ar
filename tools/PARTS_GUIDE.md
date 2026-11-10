# Alkatrészfotók felvétele

A konfigurátor valódi termékfotókat illeszt az alapfegyver hivatalos fotójára. Egy alkatrész
akkor kerül fel, ha **tiszta oldalnézetű** (profilból fotózott, nem dőlt, nem három-negyedes)
képe van, és a méretarányához **ellenőrizhető valós méret** áll rendelkezésre. Ha ezek közül
bármelyik hiányzik, az alkatrész NEM kerül fel (a konfigurátor szaggatott „?” jelölést mutat a
helyén). Találgatni, vagy nem oldalnézetű képet erőltetni tilos: eltolódik és elrontja a képet.

## Az eszköz

```
python3 tools/add_part.py --item <data.js id> --src <URL|fájl> --facing left|right \
    --width-in <valós hossz hüvelykben> --anchor <mód> --pack <csomagnév> [--slot ...] [--color ...]
```

A kép a `assets/photos/parts/` mappába, a rekord az `assets/photos/packs/<pack>.json`-ba kerül,
a `manifest.json` automatikusan hivatkozik rá. Az `assets/photos/_check/*.png` az ellenőrző kép
(szürke alapon, a piros kereszt az illesztési pont) – **mindig nézd meg**, mielőtt továbbmész.

## Irány (`--facing`) és oldal-változatok

A `--facing` azt mondja meg, **merre néz a termék eleje a képen** (a cső/torkolat iránya, ha a
fegyveren lenne): `left` vagy `right`. Az alapfegyver fotók: Schmeisser M4FL / M5FL / S4F balra,
Dynamic jobbra néznek. A felesleges tükrözés elkerülésére, ha mindkét irányú kép megvan, vedd fel
mindkettőt: `--slot main@left` és `--slot main@right`. A motor az alapfotó irányával egyezőt
választja, egyébként a sima `main` slotot (tükrözve).

## Slotok

- `main` – alap; `main@left`, `main@right` – irányfüggő változat
- `rear`, `front` – vas irányzék (BUIS) hátsó/első eleme (külön kép mindkettő)
- `--color` – szín id (`black`, `fde`, `odg`, `gray`, `wolfgray`, `foliage`, `ranger`, `coyote`),
  vagy `*`, ha egyetlen kép minden színre érvényes. Színenként külön fotó a legjobb.

## Illesztési pont (`--anchor`) kategóriánként

| Kategória | anchor | Jelentése a fotón |
|---|---|---|
| red dot / holo / ACOG / szereléksín (szereléssel együtt fotózva) | `contact` | a sínre fekvő talp alja, középen |
| céltávcső szerelék nélkül | `axis` | a tubus tengelye a közepén (a szerelék külön tétel) |
| optikaszerelék | `contact` | a talp alja, középen |
| nagyító | `contact` | a talp alja, középen |
| BUIS hátsó/első elem | `contact` | a talp alja, középen |
| fegyverlámpa | `contact` | a rögzítőláb alja |
| markolat | `top` | a markolat tetejének közepe (ahol a tokhoz csatlakozik) |
| tus | `front` | a tus eleje a tengely magasságában (ahol a tokhoz csatlakozik) |
| tár | `top` | a tár tetejének közepe |
| torkolati eszköz | `rear` | a hátsó sík (ahol a csőre menetezett) a tengelyen |
| bipod, előmarkolat, kézütköző, QD, sín (M-LOK) | `top` | a rögzítő felület közepe |
| kézi pont | `X,Y` | pixel-koordináta a kivágott képen |

## Méretarány (`--width-in`)

A kivágott termék **teljes szélessége** (a kép x-irányú kiterjedése) hüvelykben. Csak megbízható
forrásból: gyártói műszaki adatlap, termékoldal „Length/Overall length” sora, vagy olyan
leírás, ami a fotón látható méretre vonatkozik. A szerelék/talp külön ne szerepeljen a hosszban,
ha a hossz csak a testre vonatkozik – ilyenkor `--height-in`-t vagy más ismert méretet használj.
Ha nincs ellenőrizhető méret, hagyd ki az alkatrészt.

## Egyéb

- Háttér: az eszköz a világos, egységes hátteret automatikusan kivágja (`--tol`, alap 22).
  Átlátszó hátterű képet (PNG/WebP alfával) változtatás nélkül használ.
- Forrás: add meg a `--source` mezőben az oldalt (pl. `eotechinc.com`).
- Új alapfegyver: `tools/build_base_assets.py`.
- A megjelenítő a katalógust a böngészőben tölti be; a fejlesztői szervert a repó gyökeréből
  indítsd (`python3 -m http.server 8765`).

## Igazítás (forgatás, finomhangolás)

A motor az illesztési pont körül forgat. Ha a termékfotó dőlése nem egyezik a fegyveren állóval
(pl. a markolat függőlegesen áll a fotón), nem kell a képet előre elforgatni:

- `--rot FOK` – forgatás az illesztési pont körül; pozitív érték a termék elejét lefelé billenti
  (a fegyver keretében, irányfüggetlenül), negatív felfelé. A markolatnál pl. a gyári M4FL markolat
  dőlése a mérce.
- `--dx`, `--dy` – finomhangolás hüvelykben (előre / lefelé).

Mindig előnézettel ellenőrizd a valódi puskán (lásd fent), és ha kell, igazíts rajta.
