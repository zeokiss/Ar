#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Valós CHF árak és műszaki adatok gyűjtése a thoron.ch webshopból az AR-15
konfigurátor termékeihez (js/data.js: BASES + ITEMS, a 'factory' és 'none'
elemek nélkül).

Használat (a repó gyökeréből):

    python3 tools/prices_thoron.py                    # minden termék
    python3 tools/prices_thoron.py --only mp-moe,gs-ssa   # csak néhány id
    python3 tools/prices_thoron.py --cache-dir /tmp/thoron_cache   # lekérések gyorsítótára

Kimenet: assets/prices/thoron.json

Működés:
  * a bolt keresője: GET https://www.thoron.ch/de/suche?search=<kifejezés>
    (60 találat / oldal, találatonként: márka, terméknév, alcím, ár, készlet);
  * minden termékhez a SPECS táblában van keresőkifejezés + a bolti terméknévre
    illesztett reguláris kifejezés (teljes egyezés kell, nem "hasonló"), opcionális
    alcím-szűrő és márka-ellenőrzés; ami nem azonos termék, az 'unmatched'-be kerül;
  * az egyezés után a termékoldalt is letölti (JSON-LD + "Daten" fül + leírás):
    pontos ár (ÁFÁ-s, akciónál price_chf + list_chf), kép, készlet, súly, méretek;
  * színváltozatok a boltban külön termékoldalak (azonos névvel, eltérő színnel);
    ezek árait a 'variants' / 'colors_prices' mezők adják.

A bolt felé udvarias: kérések között legalább --delay másodperc (alap 1.1 s),
egyszálú letöltés, hibánál újrapróbálkozás.
Az árakat KIZÁRÓLAG a bolt oldaláról olvassa ki, nem becsül.
"""
import argparse
import datetime
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = 'https://www.thoron.ch'
SEARCH = BASE + '/de/suche?search='
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0 Safari/537.36')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA = os.path.join(ROOT, 'js', 'data.js')
DEFAULT_OUT = os.path.join(ROOT, 'assets', 'prices', 'thoron.json')

# ----------------------------------------------------------------------------
# Egyezési szabályok termékenként
#   q     keresőkifejezések (sorban, az eredmények összegyűlnek)
#   t     a bolti terméknév (h2) TELJES egyezéséhez tartozó regex (kis/nagybetű mindegy)
#   sub   (opc.) regex, aminek az alcímben (pl. "AR15/M4 | Schwarz") szerepelnie kell
#   b     (opc.) regex a bolti márkanévre (alap: a data.js márkájából)
#   prefer (opc.) ha több változatcsoport van (pl. célkereszt), ezt választja
#   need  (opc.) regex, aminek a termékoldal leírásában/adataiban szerepelnie kell
#   note  (opc.) megjegyzés, ami a találathoz kerül
#   why   az 'unmatched' indoklása, ha nincs (biztos) egyezés
#   cand  (opc.) regex a találati címekre: "közeli, de nem azonos" jelöltek listázása
# ----------------------------------------------------------------------------
SPECS = {
    # ---- Alapfegyverek
    'schm-s4f': dict(q=['Schmeisser AR15 S4F', 'S4F M-Lock'], t=r'AR15 S4F M-Lock \| 10\.5"',
                     note='Bolti név: AR15 S4F M-Lock 10.5" (.223 Rem.)', why='nincs a boltban'),
    'schm-m4fl': dict(q=['Schmeisser AR15 M4FL', 'Schmeisser AR15'], t=r'AR15 M4FL.*',
                      why='ok: nincs a boltban (a Schmeisser AR15 családból csak az S4F 10.5" kapható)'),
    'schm-m5fl': dict(q=['Schmeisser AR15 M5FL', 'Schmeisser AR15'], t=r'AR15 M5FL.*',
                      why='ok: nincs a boltban (a Schmeisser AR15 családból csak az S4F 10.5" kapható)'),
    'schm-dyn': dict(q=['Schmeisser AR15 Dynamic', 'Schmeisser AR15'], t=r'AR15 Dynamic.*',
                     why='ok: nincs a boltban (a Schmeisser AR15 családból csak az S4F 10.5" kapható)'),
    'dd-v7': dict(q=['Daniel Defense DDM4 V7', 'DDM4 V7 16'], t=r'DDM4 V7 \| 16"', b=r'daniel',
                  why='nincs a boltban'),
    'bcm-recce16': dict(q=['BCM RECCE-16', 'RECCE MCMR'], t=r'RECCE-16.*', cand=r'RECCE',
                        why='ok: nincs a boltban (csak a RECCE-14 MCMR 14.5" kapható, az nem azonos)'),

    # ---- Markolatok
    'mp-moe': dict(q=['Magpul MOE Grip'], t=r'MOE Grip', sub=r'AR15/M4', why='nincs a boltban'),
    'mp-moek2': dict(q=['Magpul MOE-K2 Grip'], t=r'MOE-K2 Grip', sub=r'AR15/M4', why='nincs a boltban'),
    'mp-moek2p': dict(q=['Magpul MOE-K2+ Grip', 'MOE-K2 Grip'], t=r'MOE-K2\+ Grip', sub=r'AR15/M4',
                      why='nincs a boltban'),
    'mp-miad': dict(q=['Magpul MIAD GEN 1.1'], t=r'MIAD GEN 1\.1 Grip Kit', prefer=r'TYPE 1',
                    note='A boltban Type 1 és Type 2 kit is van (azonos ár).', why='nincs a boltban'),
    'bcm-g0': dict(q=['BCM Gunfighter Grip', 'Gunfighter Grip Mod 0'], t=r'Gunfighter (Pistol )?Grip.*Mod ?0.*',
                   why='ok: nincs a boltban (BCM pisztolymarkolat nincs, csak előmarkolatok)'),
    'bcm-g3': dict(q=['BCM Gunfighter Grip', 'Gunfighter Grip Mod 3'], t=r'Gunfighter (Pistol )?Grip.*Mod ?3.*',
                   why='ok: nincs a boltban (BCM pisztolymarkolat nincs, csak előmarkolatok)'),
    'b5-t22': dict(q=['B5 Systems Type 22', 'B5 Type 22 P-Grip'], t=r'.*Type 22.*', b=r'b5',
                   why='ok: nincs a boltban (B5 Systems nincs a kínálatban)'),
    'b5-t23': dict(q=['B5 Systems Type 23', 'B5 Type 23 P-Grip'], t=r'.*Type 23.*', b=r'b5',
                   why='ok: nincs a boltban (B5 Systems nincs a kínálatban)'),

    # ---- Tusok
    'mp-ctr': dict(q=['Magpul CTR Carbine Stock'], t=r'CTR Carbine Stock', sub=r'Mil.?Spec', why='nincs a boltban'),
    'mp-moesl': dict(q=['Magpul MOE SL Carbine Stock'], t=r'MOE SL Carbine Stock', sub=r'Mil.?Spec',
                     why='nincs a boltban'),
    'mp-moeslk': dict(q=['Magpul MOE SL-K Carbine Stock'], t=r'MOE SL-K Carbine Stock', sub=r'Mil.?Spec',
                      why='nincs a boltban'),
    'mp-ubr2': dict(q=['Magpul UBR GEN2 Collapsible Stock', 'UBR GEN2'], t=r'UBR GEN2 Collapsible Stock',
                    why='nincs a boltban'),
    'mp-prs3': dict(q=['Magpul PRS GEN3 Precision-Adjustable Stock', 'PRS GEN3'],
                    t=r'PRS GEN3 Precision(-Adjustable)? Stock', note='Bolti név: PRS GEN3 Precision-Adjustable Stock',
                    why='nincs a boltban'),
    'mp-prslite': dict(q=['Magpul PRS Lite Stock'], t=r'PRS Lite Stock', why='nincs a boltban'),
    'bcm-s0': dict(q=['BCM Gunfighter Stock', 'Gunfighter Stock Mod 0'], t=r'Gunfighter Stock.*Mod ?0.*',
                   why='ok: nincs a boltban (BCM tus nincs a kínálatban)'),
    'bcm-s1': dict(q=['BCM Gunfighter Stock', 'Gunfighter Stock Mod 1 SOPMOD'], t=r'Gunfighter Stock.*Mod ?1.*',
                   why='ok: nincs a boltban (BCM tus nincs a kínálatban)'),
    'b5-bravo': dict(q=['B5 Systems Bravo Stock', 'Bravo Stock'], t=r'Bravo Stock', b=r'b5',
                     why='ok: nincs a boltban (B5 Systems nincs a kínálatban)'),
    'b5-esop': dict(q=['B5 Systems Enhanced SOPMOD Stock', 'Enhanced SOPMOD'], t=r'Enhanced SOPMOD Stock', b=r'b5',
                    why='ok: nincs a boltban (B5 Systems nincs a kínálatban)'),
    'b5-prec': dict(q=['B5 Systems Precision Stock'], t=r'Precision Stock', b=r'b5',
                    why='ok: nincs a boltban (B5 Systems nincs a kínálatban)'),

    # ---- Tustalpak
    'mp-pad030': dict(q=['Magpul Rubber Butt-Pad 0.30'], t=r'Rubber Butt-Pad \| 0\.30"', why='nincs a boltban'),
    'mp-pad055': dict(q=['Magpul Extended Rubber Butt-Pad 0.55'], t=r'Extended Rubber Butt-Pad \| 0\.55"',
                      why='nincs a boltban'),
    'mp-pad070': dict(q=['Magpul Enhanced Rubber Butt-Pad 0.70'], t=r'Enhanced Rubber Butt-Pad \| 0\.70"',
                      why='nincs a boltban'),
    'mp-padsl': dict(q=['Magpul MOE SL Enhanced Rubber Butt-Pad'], t=r'MOE SL Enhanced Rubber Butt-Pad \| 0\.70"',
                     why='nincs a boltban'),

    # ---- Red dot / holo
    'ap-t2': dict(q=['Aimpoint Micro T-2'], t=r'Micro T-2 Schwarz \| LRP Montage \| 39 mm',
                  note='Bolti kivitel: Micro T-2 LRP-quick-release montázzsal és 39 mm távtartóval.',
                  why='nincs a boltban'),
    'ap-m5': dict(q=['Aimpoint CompM5', 'Comp M5'], t=r'Comp M5', need=r'LRP',
                  note='A bolti leírás szerint a CompM5 LRP-montázzsal és 39 mm távtartóval kerül eladásra.',
                  why='nincs a boltban'),
    'ap-pro': dict(q=['Aimpoint PRO', 'Aimpoint PRO QRP2'], t=r'(Aimpoint )?PRO.*', b=r'aimpoint',
                   why='ok: nincs a boltban (Aimpoint PRO nincs a kínálatban)'),
    'ap-acro': dict(q=['Aimpoint Acro P-2'], t=r'Acro P-2', sub=r'3\.5 MOA', cand=r'^Acro P-2$',
                    why='bizonytalan: a boltban az Acro P-2 önmagában kapható (3.5 MOA), a Picatinny talp nincs '
                        'a termékhez írva -> a csomag nem azonosítható biztosan'),
    'eo-exps3': dict(q=['EOTech EXPS3-0', 'HWS EXPS3-0'], t=r'HWS EXPS3-0', sub=r'Red Circle Dot', prefer=r'Schwarz',
                     why='nincs a boltban'),
    'eo-exps2': dict(q=['EOTech EXPS2-0', 'HWS EXPS2-0'], t=r'HWS EXPS2-0', sub=r'Red Circle Dot',
                     note='Piros célkereszt; a zöld változat a boltban külön, drágább termék.', why='nincs a boltban'),
    'eo-xps2': dict(q=['EOTech XPS2-0', 'HWS XPS2-0'], t=r'HWS XPS2-0', sub=r'Circle Dot Red',
                    note='Piros célkereszt; a zöld változat a boltban külön, drágább termék.', why='nincs a boltban'),
    'hs-510c': dict(q=['Holosun HS510C'], t=r'Dot Sight HS510C', why='nincs a boltban'),
    'hs-aems': dict(q=['Holosun AEMS'], t=r'AEMS', cand=r'AEMS',
                    why='bizonytalan: a boltban csak AEMS-EVO / AEMS PRO / Ronin AEMS MAX van, az "AEMS" '
                        'alapmodell nincs -> nem azonos'),
    'hs-403b': dict(q=['Holosun HS403B', 'HS403B'], t=r'.*403B.*', b=r'holosun', why='ok: nincs a boltban'),
    'tj-mro': dict(q=['Trijicon MRO HD', 'MRO Q-Loc 1/3'], t=r'MRO HD 1x25 Red Dot Sight \| .*Lower 1/3.*',
                   cand=r'^MRO (HD 1x25 Red Dot Sight$|Schnellmontage Q-Loc 1/3)',
                   why='bizonytalan: a "MRO HD + Lower 1/3 szerelék" csomag a boltban nem egy termék '
                       '(az MRO HD Low Mount külön, az 1/3 Co-Witness 39 mm Q-Loc montázs külön) -> '
                       'jelöltek a "candidates"-ben'),

    # ---- Vortex (nem szerepel a kínálatban)
    'vx-uh1': dict(q=['Vortex UH-1', 'UH-1 Gen II'], t=r'.*UH-1.*', b=r'vortex', why='ok: nincs a boltban (Vortex UH-1 nincs)'),
    'vx-sparc': dict(q=['Vortex SPARC AR', 'Vortex SPARC'], t=r'.*SPARC.*', b=r'vortex',
                     why='ok: nincs a boltban (Vortex SPARC nincs)'),
    'vx-crossfire': dict(q=['Vortex Crossfire'], t=r'.*Crossfire.*', b=r'vortex',
                         why='ok: nincs a boltban (Vortex Crossfire red dot nincs)'),
    'vx-se16': dict(q=['Vortex Strike Eagle 1-6x24', 'Strike Eagle 1-6'], t=r'Strike Eagle 1-6x24.*', b=r'vortex',
                    cand=r'Strike Eagle',
                    why='ok: nincs a boltban (csak Strike Eagle EBR-7C 5-25x56 van, az nem azonos)'),
    'vx-se18': dict(q=['Vortex Strike Eagle 1-8x24', 'Strike Eagle 1-8'], t=r'Strike Eagle 1-8x24.*', b=r'vortex',
                    cand=r'Strike Eagle',
                    why='ok: nincs a boltban (csak Strike Eagle EBR-7C 5-25x56 van, az nem azonos)'),
    'vx-pst16': dict(q=['Vortex Viper PST Gen II 1-6x24', 'Viper PST'], t=r'.*PST.*1-6x24.*', b=r'vortex',
                     why='ok: nincs a boltban (Viper PST 1-6x24 nincs)'),
    'vx-razor110': dict(q=['Vortex Razor HD Gen III', 'Razor HD'], t=r'.*Razor.*', b=r'vortex',
                        why='ok: nincs a boltban (Vortex Razor nincs)'),
    'tj-acog': dict(q=['Trijicon ACOG 4x32', 'ACOG 4x32 BAC'], t=r'ACOG 4x32 BAC Riflescope', need=r'TA31',
                    note='Bolti kivitel: TA31F (.223/5.56 BDC); a data.js csak "TA31"-et ír.',
                    why='nincs a boltban'),
    'tj-credo16': dict(q=['Trijicon Credo', 'Credo 1-6x24'], t=r'.*Credo.*', b=r'trijicon',
                       why='ok: nincs a boltban (Trijicon Credo nincs)'),
    'tj-vcog': dict(q=['Trijicon VCOG 1-8x28', 'VCOG'], t=r'VCOG.* 1-8x28 Riflescope - SCO', b=r'trijicon',
                    cand=r'VCOG',
                    why='bizonytalan: a boltban csak a "VCOG 1-8x28 SCO" LaRue LT799 szereléssel kapható, '
                        'más kivitel -> jelölt a "candidates"-ben'),
    'eo-vudu16': dict(q=['EOTech Vudu 1-6x24'], t=r'Vudu 1-6x24 FFP', prefer=r'5\.56',
                      note='A boltban csak FFP, többféle célkereszttel (azonos ár); a megjelenített a 5.56 BDC.',
                      why='nincs a boltban'),
    'eo-vudu110': dict(q=['EOTech Vudu 1-10x28'], t=r'Vudu 1-10x28 FFP', sub=r'Schwarz', why='nincs a boltban'),
    'gs-sp30': dict(q=['Geissele Super Precision Scope Mount 1.93', 'Geissele Super Precision'],
                    t=r'Super Precision Scope Mount \| 1\.93"', sub=r'for AR 15', why='nincs a boltban'),
    'vx-cant30': dict(q=['Vortex Sport Cantilever', 'Vortex Cantilever 30'], t=r'.*Cantilever.*', b=r'vortex',
                      why='ok: nincs a boltban (Vortex Sport Cantilever nincs)'),

    # ---- Nagyítók
    'eo-g33': dict(q=['EOTech G33 Magnifier', 'G33 Magnifier'], t=r'G33 Magnifier', sub=r'Schwarz',
                   note='Bolti SKU: G33.STS (Flip-to-Side montázzsal).', why='nincs a boltban'),
    'eo-g43': dict(q=['EOTech G43 Magnifier', 'G43 Magnifier'], t=r'G43 Magnifier', sub=r'Schwarz',
                   note='Bolti SKU: G43.STS (Flip-to-Side montázzsal) - a data.js "Micro szerelék" megjelölése nem ellenőrizhető.',
                   why='nincs a boltban'),
    'ap-3x': dict(q=['Aimpoint 3XMag-1', '3XMag'], t=r'.*3XMag.*', b=r'aimpoint',
                  why='ok: nincs a boltban (Aimpoint 3XMag-1 nincs)'),
    'hs-hm3x': dict(q=['Holosun HM3X'], t=r'HM3X Magnifier', why='nincs a boltban'),
    'vx-m3x': dict(q=['Vortex Micro 3X', 'Vortex Micro3X Magnifier'], t=r'.*Micro ?3X.*', b=r'vortex',
                   why='ok: nincs a boltban (Vortex Micro3X nincs)'),

    # ---- BUIS (a boltban elő- és hátsó irányzék külön termék)
    'mp-mbus3': dict(q=['Magpul MBUS 3 Sight'], t=r'MBUS 3 Set', cand=r'^MBUS 3 Sight$',
                     why='bizonytalan: a boltban az elő- és a hátsó MBUS 3 irányzék külön termék (nincs "szett"), '
                         'jelöltek a "candidates"-ben'),
    'mp-mbuspro': dict(q=['Magpul MBUS Pro Sight'], t=r'MBUS Pro Set', cand=r'^MBUS Pro Sight$',
                       why='bizonytalan: a boltban az elő- és a hátsó MBUS Pro irányzék külön termék (nincs "szett"), '
                           'jelöltek a "candidates"-ben'),
    'mp-mbusoff': dict(q=['Magpul MBUS Pro Offset Sight'], t=r'MBUS Pro Offset Set', cand=r'^MBUS Pro Offset Sight$',
                       why='bizonytalan: a boltban az elő- és a hátsó MBUS Pro Offset irányzék külön termék '
                           '(nincs "szett"), jelöltek a "candidates"-ben'),

    # ---- Lámpák
    'sf-m600': dict(q=['SureFire M600DF Scout Light Pro', 'Scout Light Pro M600'], t=r'Scout Light Pro M600DF.*',
                    b=r'surefire', cand=r'^Scout Light Pro M640DF',
                    why='ok: nincs a boltban (M600DF nincs; csak az M640DF/M640DFT/M640U kapható)'),
    'sf-m640': dict(q=['SureFire Scout Light Pro M640DF', 'M640DF'], t=r'Scout Light Pro M640DF', b=r'surefire',
                    why='nincs a boltban'),
    'sf-m300': dict(q=['SureFire M300 Mini Scout Light Pro', 'Mini Scout Light Pro'],
                    t=r'(Mini Scout Light Pro )?M300.*', b=r'surefire', cand=r'Mini Scout',
                    why='ok: nincs a boltban (M300 nincs; a Mini Scout Light Pro M340C más modell)'),
    'sf-x300': dict(q=['SureFire X300U-B', 'X300U'], t=r'X300U-B', sub=r'Schwarz', b=r'surefire', why='nincs a boltban'),
    'sl-hlx': dict(q=['Streamlight ProTac Rail Mount HL-X'], t=r'ProTac Rail Mount HL-X', why='nincs a boltban'),
    'sl-tlr1': dict(q=['Streamlight TLR-1 HL'], t=r'TLR-1 HL', why='nincs a boltban'),

    # ---- M-LOK kiegészítők
    'mp-rvg': dict(q=['Magpul RVG Rail Vertical Grip', 'Magpul RVG'], t=r'RVG M-LOK.*', cand=r'^RVG ',
                   why='bizonytalan: a boltban az RVG Rail Vertical Grip (Picatinny) és az RVG Adapter Rail (M-LOK) '
                       'külön termék; az M-LOK-os RVG nincs egyben -> jelöltek a "candidates"-ben'),
    'mp-hstop': dict(q=['Magpul M-LOK Hand Stop Kit'], t=r'M-LOK Hand Stop Kit', why='nincs a boltban'),
    'bcm-vg3': dict(q=['BCM Gunfighter Vertical Grip'], t=r'Gunfighter Vertical Grip', sub=r'M-Lok', b=r'bcm',
                    note='Bolti SKU: BCM-VG-MCMR-MOD-3 (M-LOK, Mod 3).', why='nincs a boltban'),
    'bcm-kag': dict(q=['BCM Gunfighter KAG Angled Grip', 'KAG Angled Grip'], t=r'Gunfighter KAG Angled Grip', b=r'bcm',
                    why='nincs a boltban'),
    'mp-rail5': dict(q=['Magpul Aluminum Picatinny Rail M-LOK'], t=r'Aluminum Picatinny Rail M-LOK', sub=r'5 Slots',
                     why='nincs a boltban'),
    'mp-qd': dict(q=['Magpul M-LOK QD Sling Mount'], t=r'M-LOK QD Sling Mount', why='nincs a boltban'),
    'mp-cover': dict(q=['Magpul M-LOK Rail Covers Type 2'], t=r'M-LOK Rail Covers \| Type 2', why='nincs a boltban'),
    'mp-cable': dict(q=['Magpul M-LOK Cable Management Kit', 'Magpul Cable Management'], t=r'.*Cable Management.*',
                     b=r'magpul', cand=r'Wire Control',
                     why='ok: nincs a boltban (Cable Management Kit nincs; a "WCK - Wire Control Kit" más termék)'),

    # ---- Bipod
    'mp-bipod': dict(q=['Magpul Bipod for M-LOK'], t=r'Bipod for M-LOK', why='nincs a boltban'),
    'hr-sbrm': dict(q=['Harris Bipod S-BRM', 'Harris S-BRM'], t=r'.*S-?BRM.*', b=r'harris',
                    why='ok: nincs a boltban (Harris bipod nincs)'),

    # ---- Torkolati eszközök
    'sf-warcomp': dict(q=['SureFire WARCOMP Flash Hider'], t=r'WARCOMP Flash Hider', sub=r'5\.56x45mm', b=r'sure',
                       why='nincs a boltban'),
    'sf-3p': dict(q=['SureFire SF3P', 'SF3P-556'], t=r'.*SF3P.*', b=r'sure',
                  why='ok: nincs a boltban (SF3P nincs; WARCOMP lángrejtők vannak)'),
    'bcm-comp0': dict(q=['BCM Gunfighter Compensator'], t=r'Gunfighter Compensator.*', b=r'bcm',
                      why='ok: nincs a boltban (BCM kompenzátor nincs)'),
    'sf-rc2': dict(q=['SureFire SOCOM556-RC2', 'SureFire SOCOM'], t=r'.*(SOCOM|RC2).*', b=r'sure',
                   why='ok: nincs a boltban (SureFire hangtompító nincs)'),

    # ---- Elsütők
    'gs-ssae': dict(q=['Geissele SSA-E Trigger'], t=r'Super Semi-Automatic Enhanced \(SSA-E\) Trigger',
                    why='nincs a boltban'),
    'gs-ssa': dict(q=['Geissele SSA Trigger'], t=r'Super Semi-Automatic \(SSA\) Trigger', why='nincs a boltban'),
    'gs-sde': dict(q=['Geissele SD-E Trigger'], t=r'Super Dynamic Enhanced \(SD-E\) Trigger', why='nincs a boltban'),
    'gs-s3g': dict(q=['Geissele Super 3 Gun S3G Trigger'], t=r'Super 3 Gun \(S3G\) Trigger', why='nincs a boltban'),

    # ---- Felhúzókarok
    'rd-raptor': dict(q=['Radian Raptor Charging Handle AR15', 'Radian Raptor'], t=r'Raptor', sub=r'AR15/M16', b=r'radian',
                      why='nincs a boltban'),
    'rd-raptorlt': dict(q=['Radian Raptor-LT'], t=r'Raptor-LT', sub=r'AR15/M16', b=r'radian', why='nincs a boltban'),
    'rd-raptorsd': dict(q=['Radian Raptor-SD'], t=r'Raptor-SD', sub=r'AR15/M16', b=r'radian', why='nincs a boltban'),
    'bcm-ch4': dict(q=['BCM Gunfighter Charging Handle Mod 4'], t=r'Gunfighter Charging Handle.*', b=r'bcm',
                    why='ok: nincs a boltban (BCM felhúzókar nincs)'),
    'bcm-ch3': dict(q=['BCM Gunfighter Charging Handle Mod 3'], t=r'Gunfighter Charging Handle.*', b=r'bcm',
                    why='ok: nincs a boltban (BCM felhúzókar nincs)'),
    'gs-ach': dict(q=['Geissele Airborne Charging Handle'], t=r'Airborne Charging Handle', sub=r'AR-15', b=r'geissele',
                   why='nincs a boltban'),

    # ---- Kezelőszervek
    'rd-talon': dict(q=['Radian Talon 45/90'], t=r'Sicherung Talon 45/90', cand=r'Sicherung Talon 45/90',
                     t_never=True,
                     why='bizonytalan: a boltban a Talon 45/90 több kitként kapható (2 LEVER KIT / 4 LEVER KIT / '
                         'Competition), a data.js "ambidex" kit melyik megfelelője, nem dönthető el -> jelöltek a '
                         '"candidates"-ben'),
    'mp-bad': dict(q=['Magpul B.A.D. Lever'], t=r'B\.A\.D\. Lever - Battery Assist Device', sub=r'AR15/M4',
                   why='nincs a boltban'),
    'mp-trigguard': dict(q=['Magpul MOE Enhanced Trigger Guard'], t=r'MOE Enhanced Trigger Guard, Polymer',
                         sub=r'AR15/M4', why='nincs a boltban'),

    # ---- Tárak
    'mp-pm10': dict(q=['Magpul PMAG 10 AR/M4 GEN M3'], t=r'PMAG 10 AR/M4 GEN M3', why='nincs a boltban'),
    'mp-pm20': dict(q=['Magpul PMAG 20 AR/M4 GEN M3'], t=r'PMAG 20 AR/M4 GEN M3', why='nincs a boltban'),
    'mp-pm30': dict(q=['Magpul PMAG 30 AR/M4 GEN M3'], t=r'PMAG 30 AR/M4 GEN M3', why='nincs a boltban'),
    'mp-pm30w': dict(q=['Magpul PMAG 30 AR/M4 GEN M3 Window'], t=r'PMAG 30 AR/M4 GEN M3 Window', why='nincs a boltban'),
    'mp-pm40': dict(q=['Magpul PMAG 40 AR/M4 GEN M3'], t=r'PMAG 40 AR/M4 GEN M3', why='nincs a boltban'),
    'mp-d60': dict(q=['Magpul PMAG D-60 AR/M4 GEN M3'], t=r'PMAG D-60 AR/M4 GEN M3', why='nincs a boltban'),

    # ---- Szíjak
    'mp-ms1': dict(q=['Magpul MS1 Sling'], t=r'MS1 Sling', why='nincs a boltban'),
    'mp-ms4': dict(q=['Magpul MS4 Dual QD Sling'], t=r'MS4 Dual QD Sling', why='nincs a boltban'),
    'bfg-vickers': dict(q=['Blue Force Gear Vickers Combat Application Sling', 'Vickers Combat Application Sling'],
                        t=r'Vickers Combat Applications? Sling', b=r'blue ?force|bfg', why='nincs a boltban'),
}

# Bolti színnevek -> a data.js színkulcsai (ahol van); egyéb: a bolti név kisbetűsen
COLOR_MAP = {
    'schwarz': 'black', 'black': 'black',
    'grau': 'gray', 'grey': 'gray', 'gray': 'gray',
    'fde': 'fde', 'fde brown': 'fde',
    'od-green': 'odg', 'od green': 'odg', 'olive': 'odg',
    'mc-brown': 'mc-brown', 'mcb': 'mc-brown',
    'coyote': 'coyote', 'ranger green': 'ranger',
    'tan': 'tan', 'ddc': 'ddc', 'plum': 'plum', 'gold': 'gold',
    'grün': 'green', 'rot': 'red', 'sniper grey': 'sniper-grey',
    'deep woods': 'deep-woods', 'deep woods green': 'deep-woods', 'tornado': 'tornado',
    'cobalt': 'cobalt', 'wolf grey': 'wolfgray', 'foliage green': 'foliage',
}

BRAND_RX = {
    'Magpul': r'magpul', 'BCM': r'bcm|bravo', 'Aimpoint': r'aimpoint', 'EOTech': r'eotech',
    'Holosun': r'holosun', 'Trijicon': r'trijicon', 'Vortex': r'vortex', 'SureFire': r'sure ?fire',
    'Streamlight': r'streamlight', 'Geissele': r'geissele', 'Radian': r'radian', 'Harris': r'harris',
    'Blue Force Gear': r'blue ?force|bfg', 'Daniel Defense': r'daniel', 'Schmeisser': r'schmeisser',
    'B5 Systems': r'b5',
}


# ----------------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------------
class Shop:
    def __init__(self, delay=1.1, cache_dir=None, verbose=True):
        self.delay = delay
        self.cache_dir = cache_dir
        self.verbose = verbose
        self._last = 0.0
        self.requests = 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, url):
        import hashlib
        return os.path.join(self.cache_dir, hashlib.md5(url.encode()).hexdigest() + '.html')

    def get(self, url):
        if self.cache_dir:
            p = self._cache_path(url)
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    return f.read()
        body = None
        for attempt in range(4):
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.requests += 1
            try:
                req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'de-CH,de;q=0.9',
                                                           'Accept': 'text/html,application/xhtml+xml'})
                with urllib.request.urlopen(req, timeout=40) as r:
                    body = r.read().decode('utf-8', 'replace')
                break
            except (urllib.error.URLError, OSError, ValueError) as e:
                if self.verbose:
                    print('   (újrapróbálás: %s)' % e, file=sys.stderr)
                time.sleep(2 + 3 * attempt)
        if body is not None and self.cache_dir:
            with open(self._cache_path(url), 'w', encoding='utf-8') as f:
                f.write(body)
        return body


# ----------------------------------------------------------------------------
# Elemzés
# ----------------------------------------------------------------------------
def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(s or '').replace('\xa0', ' ')).strip()


def parse_chf(s):
    """"CHF 2'325.00" -> 2325.0 ; üres -> None"""
    if not s:
        return None
    m = re.search(r'([\d][\d\'’.,]*)', s)
    if not m:
        return None
    v = m.group(1).replace("'", '').replace('’', '')
    try:
        return round(float(v), 2)
    except ValueError:
        return None


def parse_search(h):
    """A találati oldal termékkártyái."""
    out = []
    chunks = re.split(r'<div class="article__item">', h)[1:]
    for b in chunks:
        href = re.search(r'class="article__main" href="([^"]+)"', b)
        head = re.search(r'<span>([^<]*)</span>\s*<h2 class="article__title">([^<]*)</h2>\s*'
                         r'<span class="article__subtitle">([^<]*)</span>', b)
        if not (href and head):
            continue
        price = re.search(r'data-price-price>([^<]*)<', b)
        orig = re.search(r'data-price-original\s*class="[^"]*">([^<]*)<', b)
        status = re.search(r'article__lieferstatus article__lieferstatus--(\w+)">\s*([^<]*)<', b)
        img = re.search(r'<img class="article__image"\s+src="([^"]+)"', b)
        out.append(dict(
            brand=clean(head.group(1)), title=clean(head.group(2)), sub=clean(head.group(3)),
            url=BASE + href.group(1), price=parse_chf(price.group(1) if price else None),
            list=parse_chf(orig.group(1) if orig else None),
            stock_class=status.group(1) if status else None, stock_text=clean(status.group(2)) if status else None,
            thumb=(BASE + img.group(1)) if img else None))
    return out


def split_sub(sub):
    """Az alcím szegmensei és a felismert szín: 'AR15/M4 - OD-Green' -> (['AR15/M4'], 'odg')"""
    segs = [s.strip(' -|') for s in re.split(r'\s+[-|]\s+|\s*\|\s*', sub or '') if s.strip(' -|')]
    color = None
    rest = list(segs)
    for i in range(len(segs) - 1, -1, -1):
        key = segs[i].lower()
        if key in COLOR_MAP:
            color = COLOR_MAP[key]
            rest = segs[:i] + segs[i + 1:]
            break
    return rest, color


GRAM = {'g': 1.0, 'gr': 1.0, 'gramm': 1.0, 'kg': 1000.0, 'oz': 28.3495, 'lb': 453.592, 'lbs': 453.592}


def desc_text(h):
    m = re.search(r'<span>Beschreibung</span>.*?<div class="articleDetail__accordionContent[^"]*">(.*?)</div>\s*</div>\s*</details>',
                  h, flags=re.S)
    if not m:
        return ''
    s = re.sub(r'</p>|<br\s*/?>|</li>|</div>', '\n', m.group(1))
    s = re.sub(r'<[^>]+>', '', s)
    s = html.unescape(s).replace('\xa0', ' ')
    return '\n'.join(re.sub(r'\s+', ' ', l).strip() for l in s.split('\n') if l.strip())


def parse_detail(h):
    d = {}
    m = re.search(r'<script type="application/ld\+json">\s*(\{[^<]*?"@type": "Product".*?)</script>', h, flags=re.S)
    ld = None
    if m:
        try:
            ld = json.loads(m.group(1))
        except ValueError:
            ld = None
    d['ld'] = ld
    d['entries'] = [(clean(re.sub(r'<[^>]+>', '', a)), clean(re.sub(r'<[^>]+>', '', b))) for a, b in
                    re.findall(r'articleDetail__dataEntryLabel">(.*?)</div>\s*<div class="articleDetail__dataEntryValue">(.*?)</div>',
                               h, flags=re.S)]
    d['desc'] = desc_text(h)
    pm = re.search(r'class="articleDetail__pricevalue" data-price-price>([^<]*)<', h)
    om = re.search(r'<s data-price-original class="[^"]*">([^<]*)</s>', h)
    d['price'] = parse_chf(pm.group(1)) if pm else None
    d['list'] = parse_chf(om.group(1)) if om else None
    lm = re.search(r'<div class="articleDetail__lieferzeit"\s*data-status="(\w*)"\s*>\s*<span>([^<]*)</span>', h)
    d['stock_status'] = lm.group(1) if lm else None
    d['stock_text'] = clean(lm.group(2)) if lm else None
    return d


def extract_weight(entries, desc):
    texts = []
    for label, val in entries:
        if re.search(r'gewicht|weight', label, re.I):
            texts.append(val)
    for line in desc.split('\n'):
        if re.search(r'gewicht|weight', line, re.I):
            texts.append(line)
    rx = re.compile(r'([\d][\d\'.,]*)\s*(kg|gramm|grams?|gr|g|oz|lbs?)\b', re.I)
    for t in texts:
        # "Gewicht: 5.6 oz (160 g)" -> a gramm érték előnyben
        found = rx.findall(t)
        for num, unit in found:
            u = unit.lower().rstrip('s') if unit.lower() in ('grams', 'lbs') else unit.lower()
            if u in ('g', 'gr', 'gram', 'gramm'):
                v = _num(num)
                if v is not None:
                    return round(v, 1)
        for num, unit in found:
            u = unit.lower()
            u = 'lb' if u in ('lb', 'lbs') else u
            if u in GRAM:
                v = _num(num)
                if v is not None:
                    return round(v * GRAM[u], 1)
    return None


def _num(s):
    s = s.strip().replace("'", '')
    if re.fullmatch(r'\d+,\d{1,2}', s):
        s = s.replace(',', '.')
    else:
        s = s.replace(',', '')
    try:
        return float(s)
    except ValueError:
        return None


DIM_LABEL = re.compile(r'länge|laenge|höhe|hoehe|breite|abmessung|masse\b|grösse|größe|durchmesser|length|height|width|dimension|lauflänge', re.I)
DIM_LINE = re.compile(r'(länge|laenge|höhe|breite|abmessung|gesamtlänge|dimension|length|height|width)[^\n]{0,40}?\d', re.I)


def extract_dims(entries, desc):
    parts = []
    for label, val in entries:
        if DIM_LABEL.search(label):
            parts.append('%s: %s' % (label, val))
    for line in desc.split('\n'):
        if DIM_LINE.search(line) and len(line) <= 160:
            parts.append(line.strip(' ·•-'))
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return '; '.join(out)[:400] if out else None


# ----------------------------------------------------------------------------
# data.js betöltése (node)
# ----------------------------------------------------------------------------
NODE_DUMP = r"""
global.window = {}; require(process.argv[1]);
const D = window.AR_DATA, out = [];
for (const b of D.BASES) out.push({id: b.id, brand: b.brand, name: b.name, kind: 'base', colors: b.finishes || null});
for (const i of D.ITEMS) { if (i.factory || i.none) continue;
  out.push({id: i.id, brand: i.brand, name: i.name, kind: 'item', cat: i.cat, colors: i.colors || null}); }
console.log(JSON.stringify(out));
"""


def load_items(path):
    r = subprocess.run(['node', '-e', NODE_DUMP, os.path.abspath(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit('A data.js betöltése nem sikerült (node szükséges): ' + r.stderr)
    return json.loads(r.stdout)


# ----------------------------------------------------------------------------
# Egyeztetés
# ----------------------------------------------------------------------------
def candidates_for(spec, item, results):
    """A spec szabályainak megfelelő bolti találatok."""
    t_rx = re.compile(spec['t'], re.I)
    b_rx = re.compile(spec.get('b') or BRAND_RX.get(item['brand'], re.escape(item['brand'])), re.I)
    s_rx = re.compile(spec['sub'], re.I) if spec.get('sub') else None
    out = []
    for r in results:
        if spec.get('t_never'):
            break
        if not t_rx.fullmatch(r['title']):
            continue
        if not b_rx.search(r['brand']):
            continue
        if s_rx and not s_rx.search(r['sub']):
            continue
        rest, color = split_sub(r['sub'])
        r = dict(r, color=color, group=(r['title'], ' | '.join(rest).lower()))
        out.append(r)
    return out


def pick_primary(variants, item):
    pref = []
    for c in (item.get('colors') or []):
        pref.append(c)
    pref += ['black']
    for c in pref:
        for v in variants:
            if v['color'] == c:
                return v
    return variants[0]


def process_item(shop, item, spec, now_note):
    """-> (item_dict | None, unmatched_reason | None, candidates | None)"""
    results, seen_urls = [], set()
    fetch_failed = False
    for q in spec['q']:
        h = shop.get(SEARCH + urllib.parse.quote_plus(q))
        if h is None:
            fetch_failed = True
            continue
        for r in parse_search(h):
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                results.append(r)
        if candidates_for(spec, item, results) and not spec.get('cand'):
            # van egyezés az első kifejezésre - a további keresés felesleges
            break

    cands = candidates_for(spec, item, results)
    cand_list = None
    if spec.get('cand'):
        c_rx = re.compile(spec['cand'], re.I)
        b_rx = re.compile(spec.get('b') or BRAND_RX.get(item['brand'], re.escape(item['brand'])), re.I)
        cand_list = []
        for r in results:
            if c_rx.search(r['title']) and b_rx.search(r['brand']):
                cand_list.append({'title': r['title'], 'sub': r['sub'], 'price_chf': r['price'], 'url': r['url']})
        cand_list = cand_list[:8] or None

    if not cands:
        reason = spec['why']
        if fetch_failed and not results:
            reason = 'hiba: a bolti keresés nem volt elérhető (újrafuttatandó)'
        return None, reason, cand_list

    # változatcsoportok (azonos név + azonos nem-szín alcím)
    groups = {}
    for r in cands:
        groups.setdefault(r['group'], []).append(r)
    chosen = list(groups.items())
    if spec.get('prefer'):
        p_rx = re.compile(spec['prefer'], re.I)
        pc = [(k, v) for k, v in chosen if p_rx.search(k[1]) or any(p_rx.search(x['sub']) for x in v)]
        if pc:
            chosen = pc
    note = spec.get('note')
    if len(chosen) > 1:
        prices = set()
        for k, vs in chosen:
            prim = pick_primary(vs, item)
            prices.add((prim['price'], prim['list']))
        if len(prices) > 1:
            return None, ('bizonytalan: több bolti változat eltérő áron (%s) - nem dönthető el, melyik azonos'
                          % ', '.join(sorted({'%s %s' % (k[0], k[1]) for k, _ in chosen}))[:200]), cand_list
        extra = 'A boltban %d változat (%s) azonos áron.' % (len(chosen), ', '.join(k[1] or '-' for k, _ in chosen))
        note = (note + ' ' + extra) if note else extra
    group_key, variants = chosen[0]
    variants.sort(key=lambda v: v['url'])
    prim = pick_primary(variants, item)

    # termékoldal (elsődleges változat)
    ph = shop.get(prim['url'])
    det = parse_detail(ph) if ph else None
    if not det or not det['ld']:
        return None, 'hiba: a termékoldal nem volt elérhető/értelmezhető (újrafuttatandó): ' + prim['url'], cand_list
    ld = det['ld']
    if spec.get('need'):
        hay = det['desc'] + ' ' + ' '.join(v for _, v in det['entries']) + ' ' + (ld.get('sku') or '')
        if not re.search(spec['need'], hay, re.I):
            return None, 'bizonytalan: a termékoldal nem erősíti meg a várt kivitelt (%s)' % spec['need'], cand_list

    offer = ld.get('offers') or {}
    price = det['price'] if det['price'] is not None else parse_chf(str(offer.get('price')))
    list_chf = det['list']
    if list_chf is not None and price is not None and list_chf <= price:
        list_chf = None
    imgs = ld.get('image') or []
    if isinstance(imgs, str):
        imgs = [imgs]
    color_val = None
    for label, val in det['entries']:
        if re.fullmatch(r'farbe|color|colour', label, re.I):
            color_val = val
    in_stock = 'InStock' in (offer.get('availability') or '')

    out = {
        'title': clean(ld.get('name')) + ((' | ' + ' | '.join(split_sub(prim['sub'])[0])) if split_sub(prim['sub'])[0] else ''),
        'price_chf': price, 'list_chf': list_chf,
        'url': prim['url'], 'image': imgs[0] if imgs else prim['thumb'],
        'in_stock': in_stock, 'stock_text': det['stock_text'],
        'weight_g': extract_weight(det['entries'], det['desc']),
        'dims': extract_dims(det['entries'], det['desc']),
        'color': color_val or prim['color'],
        'brand': prim['brand'], 'sku': ld.get('sku'),
    }
    if note:
        out['note'] = note

    # színváltozatok
    if len(variants) > 1:
        out['variants'] = [{'color': v['color'], 'sub': v['sub'], 'price_chf': v['price'], 'list_chf': v['list'],
                            'in_stock': (v['stock_class'] in ('full', 'low')) if v['stock_class'] else None,
                            'url': v['url']} for v in variants]
        cp = {}
        for v in variants:
            if v['color'] and v['price'] is not None:
                cp.setdefault(v['color'], v['price'])
        if len(set(cp.values())) > 1:
            out['colors_prices'] = cp
    return out, None, cand_list


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default=DEFAULT_DATA, help='js/data.js útvonala')
    ap.add_argument('--out', default=DEFAULT_OUT, help='kimeneti JSON')
    ap.add_argument('--only', default='', help='vesszővel elválasztott id-k (alap: mind)')
    ap.add_argument('--delay', type=float, default=1.1, help='kérések közti szünet másodpercben (alap 1.1)')
    ap.add_argument('--cache-dir', default=None, help='lekérések gyorsítótára (fejlesztéshez; alapból nincs)')
    args = ap.parse_args()

    items = load_items(args.data)
    only = {x.strip() for x in args.only.split(',') if x.strip()}
    shop = Shop(delay=args.delay, cache_dir=args.cache_dir)

    prev = None
    if only and os.path.exists(args.out):
        try:
            with open(args.out, encoding='utf-8') as f:
                prev = json.load(f)
        except ValueError:
            prev = None

    res_items, unmatched, cand_all = {}, {}, {}
    if prev:
        res_items = dict(prev.get('items', {}))
        unmatched = dict(prev.get('unmatched', {}))
        cand_all = dict(prev.get('candidates', {}))

    todo = [it for it in items if not only or it['id'] in only]
    for n, it in enumerate(todo, 1):
        spec = SPECS.get(it['id'])
        if spec is None:
            unmatched[it['id']] = 'nincs keresési szabály a SPECS táblában (új termék?)'
            print('[%d/%d] %-12s  nincs spec' % (n, len(todo), it['id']))
            continue
        res_items.pop(it['id'], None)
        unmatched.pop(it['id'], None)
        cand_all.pop(it['id'], None)
        try:
            out, why, cands = process_item(shop, it, spec, None)
        except Exception as e:  # egy termék hibája ne állítsa le az egészet
            out, why, cands = None, 'hiba: %s: %s' % (type(e).__name__, e), None
        if out:
            res_items[it['id']] = out
            msg = 'CHF %s%s %s' % (out['price_chf'], (' (lista %s)' % out['list_chf']) if out['list_chf'] else '',
                                   '' if out['in_stock'] else '[nincs raktáron]')
        else:
            unmatched[it['id']] = why
            msg = 'NINCS: ' + why[:90]
        if cands:
            cand_all[it['id']] = cands
        print('[%d/%d] %-12s %s' % (n, len(todo), it['id'], msg))
        sys.stdout.flush()

    order = [i['id'] for i in items]
    doc = {
        'shop': 'thoron.ch', 'currency': 'CHF', 'fetched': datetime.date.today().isoformat(),
        'note': 'Fogyasztói árak, ÁFÁ-val (inkl. MWST), a bolt termékoldaláról kiolvasva. '
                'in_stock=true csak a "Verfügbar" (InStock) státusznál; a "Beim Lieferanten bestellbar" '
                'rendelhető, de nincs raktáron.',
        'items': {k: res_items[k] for k in order if k in res_items},
        'unmatched': {k: unmatched[k] for k in order if k in unmatched},
        'candidates': {k: cand_all[k] for k in order if k in cand_all},
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print('\nKész: %d találat, %d nincs/bizonytalan; hálózati kérések: %d -> %s'
          % (len(doc['items']), len(doc['unmatched']), shop.requests, args.out))


if __name__ == '__main__':
    main()
