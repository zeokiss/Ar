# Svájci fegyverboltok: ellenőrzés és bővítés (2026-11-10)

Adatfájl: `shopinfo_extra.json`. A kör országos: Richterswil nem viszonyítási pont, `km` mező nincs.

**Hálózat.** Közvetlenül csak a `vogtwaffen.ch` és a `munitionsdepot.ch` oldalt tudtam letölteni (a `gunfactory.ch` is elérhető). Minden más adat WebSearch-találatból származik.

- A `stampfli-waffen.ch` és az `m426.ch` HTTP 403-at ad; ezt nem kerültem meg.
- A `swisswaffen.com` egyetlen kérésre „You are banned” választ adott. Automatikusan ne használjuk.
- Webshop-domainek a hosts_to_allow listában: 127 host, `www.`-vel és anélkül.

## 1) A ch_shops.json bizonytalan boltjai (18 db): 15 megerősítve, 3 nem

| Bolt | Eredmény |
|---|---|
| **Gun Shop Schweiz GmbH** (Samstagern ZH) | Domain: `gunshop-schweiz.ch` és `gunshopschweiz.ch`. Cím: Obere Schwandenstrasse 8. Nyitva: Sze 15–18, Cs–P 15–19, Szo 9–12. A telefonszámok forrásonként eltérnek. Bolt és katalógus is. |
| AATS-Shop (Burgdorf BE) | Domain: `aats-shop.ch`, `aats-group.ch`, `shop.aats-group.ch`. Cím: Kirchbergstrasse 192, 2. emelet. Tel.: 034 413 40 40. Nyitva: Sze–Szo. Az árak CHF-ben vannak. |
| Kessler Shop (Kreuzlingen TG) | Domain: `shop.kesslerauktionen.ch`, Löwenstrasse 20, tel. 071 671 23 23 |
| Brix Waffen AG (Brig VS) | Rhonesandstrasse 10. Weboldalt nem találtam. |
| Waffen Ingold AG (Höchstetten BE) | Domain: `waffen-ingold.ch`, CHF-webshoppal. Cím: Zürich-Bernstr. 46 (a címtárakban 50). Tel.: 034 413 13 34. |
| Lakeside Shooting AG (Au ZH) | Domain: `lakeside-shooting.ch`. Elsősorban lőtér. A bolt külön cég: Shooting & Wilderness Store (új bejegyzés). |
| Vogt Waffen AG (Bülach ZH) | **Javítás:** a cím Marterlochstrasse **21**, nem 218. Tel., nyitvatartás és szállítás az oldalról megerősítve. Shop: `shop.vogtwaffen.ch`. |
| Waffenwerkstatt Ming | **Javítás:** a helye Lungern **OW** (Industriestrasse 42), nem Sempach. |
| Ulrich Waffen AG (Muotathal SZ) | Domain: `ulrich-waffen.ch`, tel. 041 811 64 40 |
| Waffen Glauser AG (Aarberg BE) | Domain: `waffenglauser.ch` |
| Hollenstein Waffen (Bettwiesen TG) | Hauptstrasse 24, tel. 071 622 87 27. Forrás a TG kantoni kereskedőlista. |
| Caputi Waffen (Brittnau AG) | Neumattweg 12. Csak cégjegyzékből; a bolt-jelleg ismeretlen. |
| Stampfli Waffen AG | Grossmatte Ost 22, 6014 Luzern, tel. 041 410 38 33 |
| Munitionsdepot | Üzemeltető: CBR Trading AG. Két bolt: Zwingen **BL** és Mägenwil AG. Teljes nyitvatartás és szállítás megerősítve. |
| M426 Systems GmbH | Schenkstrasse 6, Wangen a.d. Aare. Tel.: 032 426 70 00. |
| **Nem megerősített** | **Boller-Waffen:** valószínűleg Sempach LU, nem Steinen SZ (a Steinen-cím a Thoroné). **Geissbühler (Lungern):** csak egy local.ch-snippet van róla. **MGX GmbH:** semmi nem köti fegyverkereskedéshez, törlését javaslom. |

## 2) Új boltok országosan: 64 (27 megerősítve)

Kantonok szerint: ZH 6, BE 5, TI 5, GE 4, GR 4, SG 4, AG 3, BL 3, TG 3, ZG 3, BS 2, GL 2, FR 1, JU 1, LU 1, NE 1, SO 1, VD 1, VS 1. 13 webshop helye ismeretlen.

A korábban lefedetlen kantonok közül mostantól van bolt ezekben: BS, BL, NE, JU, GR, GL, ZG, FR, GE, TI. **SH, AR, AI, UR** kantonban továbbra sem találtam boltot. Az SH-ban található SIG Sauer gyártó, nem bolt.

**A legrelevánsabb új AR-15-ös online források (CHF-árral):**

| Bolt | Kínálat |
|---|---|
| **Brownells Schweiz** (`brownells.ch`) | A legszélesebb AR-alkatrész-kínálat: Geissele, BCM, Magpul, B5. Egy része USA-raktárból jön. Az üzemeltető ismeretlen. |
| **Dynamik Arms** (GE, `dynamikarms.ch`) | Hivatalos Daniel Defense-kereskedő. DDM4 CHF 2340-től, Magpul. |
| **Trooper** (Baar ZG) | Holosun és Vortex svájci raktárról. Ingyenes szállítás CHF 100 felett. |
| **Taczone** | PMAG CHF 29. Ingyenes szállítás CHF 250 felett. |
| **Wyssen Defence** (BE) | AR15-elsütők, Geissele S3G |
| **Thalmann Gun-Art** (Romanshorn TG) | SureFire puskalámpák |
| **DangoS** (SG?) | Magpul M-LOK előagy, Sig Tango-MSR LPVO |
| **Waffen Grischun** (Chur) | Aimpoint |
| **BP Group Shop** és **DH Network** | Trijicon ACOG, Streamlight |
| **Swiss Safety** | Vortex és Kahles LPVO |
| **Roch Custom** (Genf) | UTG előagy, Aimpoint |
| **Waffen Joray** (Laufen BL), **SwissOrdnance** | Komplett AR-15 CHF 910 és 990 között |

**Fontos új fizikai boltok:**

- W. Glaser Waffen, Bürchler Waffen (Zürich)
- Swiss Gun Center (Genf)
- Armurerie Lagardère (La Chaux-de-Fonds, webshoppal)
- Tactical-Armes (Delémont)
- Angerer Waffen (Davos), Vasella (Chur), Waffen Koller (Celerina)
- Rüegg Waffen (Schwanden GL)
- Frölicher (Solothurn), Waffen Dobler (Gelterkinden)
- Ambrosini, Casarmi (Lugano), Bertarmi (Losone)
- Büchel (Altstätten)
- Wyss/WWB (Burgdorf)

**Figyelmeztetések:**

- A Beierer Waffen GmbH (Basel) a cégjegyzék szerint felszámolás alatt áll.
- A The Gun Room (Sion) csak harmadik feles forrásból ismert.

## Következő lépés

A teljes országos lista ezekből a forrásokból bővíthető tovább, ha az allow-listára kerülnek:

- SBV kiskereskedelmi címlista: `sbv-asa.ch/ad.html` (Stand 10/2026) és a kantonszűrős `wg.html`
- Outdoor Enterprise kereskedőtérkép
- waffenforum.ch „Liste Schweizer Shops”
- sigsauer.swiss és acheron.ch kereskedőlistái

Részletek a JSON `leads_unverified`, `existing_updates` és `excluded` mezőiben.
