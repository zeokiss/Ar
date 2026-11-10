#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Árak és műszaki adatok gyűjtése a https://www.shooting-store.ch webshopból
az AR-15 konfigurátor (js/data.js) termékeihez.

Futtatás (a repó gyökeréből, csak Python 3 standard könyvtár kell):

    python3 tools/prices_shooting_store.py                 # teljes futás -> assets/prices/shooting-store.json
    python3 tools/prices_shooting_store.py --only mp-ctr,bcm-g0   # csak egyes id-k (kiírja, nem írja felül a JSON-t, ha --out nincs megadva)
    python3 tools/prices_shooting_store.py --cache /tmp/ss_cache  # letöltések gyorsítótárazása (újrafuttatáskor nem kér újra)
    python3 tools/prices_shooting_store.py --list-specs    # a kézi egyezési szabályok kilistázása

Működés:
  1. a data.js-ből kiolvassa az ITEMS/BASES elemeket (id, brand, name); a 'factory'/'none'
     elemeket kihagyja,
  2. mindegyikre a bolt keresőjén át keres
     (GET /shop/USER_ARTIKEL_HANDLING_AUFRUF.php?Suchstring=...&darstellen=1&lowlimit=..&highlimit=..),
  3. a találatokat a SPECS táblázat kézzel írt, szigorú szabályai (ok / no reguláris kifejezés
     a bolti terméknévre) szűrik: csak a PONTOSAN azonos terméket fogadja el, minden más
     (más modell, hossz, kivitel, csak tartozék) az "unmatched"-be kerül okkal,
  4. a termékoldalról (JSON-LD + a subartikel_content blokkok) kiolvassa az árat (CHF, ÁFÁ-val),
     akciós árat és listaárat, készletet, képet, színváltozatokat, súlyt, méretet.

Udvarias a bolttal: max. ~1 kérés/másodperc, szekvenciális letöltés, böngésző User-Agent.
Csak a www.shooting-store.ch hosttal kommunikál.
"""
import argparse
import base64
import datetime
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = 'https://www.shooting-store.ch'
SEARCH_URL = BASE + '/shop/USER_ARTIKEL_HANDLING_AUFRUF.php'
UA = ('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/124.0 Safari/537.36')
MIN_INTERVAL = 1.0          # másodperc két kérés között
PAGE = 60                   # találat / oldal (a keresőűrlap highlimit értéke)
MAX_PAGES = 3

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DATA_JS = os.path.join(ROOT, 'js', 'data.js')
OUT_JSON = os.path.join(ROOT, 'assets', 'prices', 'shooting-store.json')

# ---------------------------------------------------------------------------
# Kézi egyezési szabályok. Kulcs: data.js id.
#   q     : keresőszavak (több is lehet)
#   ok    : reguláris kif. a normalizált bolti terméknévre (kisbetű, csak a-z0-9 és szóköz,
#           a '+' jel "plus"-ra cserélve) -- a névnek meg kell felelnie
#   no    : kizáró reguláris kif. (más kivitel / modell)
#   colors: True -> több bolti termékoldal is lehet ugyanannak a terméknek a SZÍNVÁLTOZATA
#   multi : True -> több bolti oldal ugyanazon termék egyéb (nem szín) változata (pl. reticle);
#           az első (ár szerint a legolcsóbb nem, hanem a listában első) az elsődleges
#   set   : [ {ok, no, q}, ...] -> a bolt a készletet alkatrészenként (pl. első+hátsó) árulja,
#           az ár az alkatrészek összege
#   note  : megjegyzés a kimenetbe
#   skip  : 'ok' -> nem keresünk, biztosan nincs a boltban (magyarázat), de a keresés ettől még
#           lefuthat a --recheck kapcsolóval
# ---------------------------------------------------------------------------
SPECS = {}


def S(id_, q, ok=None, no=None, **kw):
    d = {'q': q if isinstance(q, list) else [q], 'ok': ok, 'no': no}
    d.update(kw)
    SPECS[id_] = d


# --- Alapfegyverek ---------------------------------------------------------
S('schm-s4f', ['Schmeisser S4F'], r'^selbstladebuchse schmeisser ar15 s4f (black|od green|bronze) 10 5 223 rem$',
  r'css|solid|special|\b9\b|9mm|duty', colors=True,
  note='Standard (Selbstladebüchse, M-LOK, 10.5", .223 Rem); a Solid Line / Special Edition / CSS-tusos kivitelek más termékek')
S('schm-m4fl', ['Schmeisser M4FL'], r'^selbstladebuchse schmeisser ar15 m4fl (black|fde|od green|bronze) 14 5 223 rem$',
  r'css|solid|special|\b9\b|9mm|duty', colors=True,
  note='Standard (14.5", .223 Rem)')
S('schm-m5fl', ['Schmeisser M5FL'], r'^selbstladebuchse schmeisser ar15 m5fl black 16 75 223 rem$',
  r'css|solid|special|\b9\b|9mm|duty|straight|sp15', colors=True,
  note='Standard (16.75", .223 Rem); a boltban csak fekete')
S('schm-dyn', ['Schmeisser Dynamic'], r'^selbstladebuchse schmeisser ar15 dynamic l (black|grey) 16 75 ',
  r'9mm|pcc', colors=True,
  note='A boltban "AR15 Dynamic L" néven fut (16.75"); a "Dynamic M5FL" névvel való azonosság a bolti oldal alapján valószínű, nem gyári megerősítésű')
S('dd-v7', ['Daniel Defense DDM4 V7'], r'^daniel defense ddm4 v7 16 5 56 nato brown milspec$',
  note='A boltban csak a Brown (Mil-Spec) 16" változat; a data.js fekete')
S('bcm-recce16', ['RECCE-16 MCMR'], r'^recce 16 mcmr15$', r'precision|enhanced|light|300')

# --- Markolatok ------------------------------------------------------------
S('mp-moe', ['Magpul MOE Grip'], r'^magpul moe grip$')
S('mp-moek2', ['Magpul MOE K2'], r'^magpul moe k2 grip$')
S('mp-moek2p', ['Magpul MOE K2'], r'^magpul moe k2 plus grip$')
S('mp-miad', ['Magpul MIAD'], r'^magpul miad 5 56 grip gen 1 1 type 1$',
  note='AR-15 (5.56) változat; a 7.62 külön termék')
S('bcm-g0', ['BCM Grip Mod 0'], r'^bcm grip mod 0 (black|flat dark earth)$', colors=True,
  note='A boltban "BCM Grip Mod 0" (a BCM Gunfighter sorozat)')
S('bcm-g3', ['BCM Grip Mod 3'], r'^bcm grip mod 3 (black|flat dark earth)$', colors=True,
  note='A boltban "BCM Grip Mod 3" (a BCM Gunfighter sorozat)')

# --- Tusok -----------------------------------------------------------------
S('mp-ctr', ['Magpul CTR Carbine Stock'], r'^magpul ctr carbine stock mil spec$', note='Mil-Spec tuskivitel')
S('mp-moesl', ['Magpul MOE SL Carbine Stock'], r'^magpul moe sl carbine stock mil spec$', note='Mil-Spec tuskivitel')
S('mp-moeslk', ['Magpul MOE SL-K Carbine Stock'], r'^magpul moe sl k carbine stock mil spec$', note='Mil-Spec tuskivitel')
S('mp-ubr2', ['Magpul UBR Gen 2'], r'^magpul ubr gen 2 0 collapsible stock$')
S('mp-prs3', ['Magpul PRS Gen 3'], r'^magpul prs gen 3 rifle stock$')
S('mp-prslite', ['Magpul PRS Lite'], r'^magpul prs lite$')
S('bcm-s0', ['BCM Stock'], r'^bcm stock mod 0 (black|flat dark earth)$', r'sopmod|kit', colors=True)
S('bcm-s1', ['BCM Stock'], r'^bcm stock mod 1 sopmod compartment (black|flat dark earth)$', colors=True)
S('b5-bravo', ['B5 Systems Bravo'], r'^b5 systems ar 15 bravo stock collapsible mil spec', note='Mil-Spec kivitel')

# --- Tustalpak -------------------------------------------------------------
S('mp-pad030', ['Magpul Buttpad'], r'^magpul rubber buttpad black$')
S('mp-pad055', ['Magpul Buttpad'], r'^magpul extended rubber buttpad black$')
S('mp-pad070', ['Magpul Buttpad'], r'^magpul enhanced rubber buttpad black$')
S('mp-padsl', ['Magpul Buttpad'], r'^magpul moe sl enhanced rubber buttpad 0 70 inches black$')

# --- Red dot / holo --------------------------------------------------------
S('eo-exps3', ['EoTech EXPS3'], r'^eotech exps3 0 (black|tan)$', colors=True)
S('eo-exps2', ['EoTech EXPS2'], r'^eotech exps2 0 black$', r'green|schwarz',
  note='Piros reticle (a "Green Reticle" változat külön termék)')
S('eo-xps2', ['EoTech XPS2'], r'^eotech xps2 0 black$', r'green')
S('hs-510c', ['Holosun HS510C'], r'^holosun hs510c (solar red circle dot sight black|fde)', r'set|ge|he510', colors=True)
S('vx-uh1', ['Vortex UH-1'], r'^vortex optics amg uh 1 gen ii holographic sight$')
S('vx-crossfire', ['Vortex Crossfire Red Dot'], r'^vortex optics crossfire red dot led upgrade black$',
  note='A boltban "Crossfire Red Dot LED Upgrade"; a magasító szerelék nincs külön feltüntetve')

# --- LPVO ------------------------------------------------------------------
S('vx-se16', ['Vortex Strike Eagle 1-6x24'], r'^vortex optics strike eagle 1 6x24 ar bdc3$')
S('vx-se18', ['Vortex Strike Eagle 1-8x24'], r'^vortex optics strike eagle 1 8x24 ar bdc3$', r'ffp|ebr',
  note='SFP, AR-BDC3 reticle')
S('vx-pst16', ['Vortex Viper PST Gen II 1-6x24'], r'^vortex optics viper pst gen ii 1 6x24 sfp vmr 2 (moa|mrad)$', multi=True,
  note='Két reticle-változat (MOA / MRAD), a fizikai termék azonos; price_chf = az első (MOA) változat')
S('vx-razor110', ['Vortex Razor Gen III'], r'^vortex optics razor gen iii 1 10x24 ebr 9 mrad$')
S('eo-vudu16', ['EoTech Vudu'], r'^eotech vudu 1 6x24 ffp sr1 reticle mrad black$', note='FFP, SR1 MRAD reticle (az egyetlen 1-6x24 a boltban)')
S('eo-vudu110', ['EoTech Vudu'], r'^eotech vudu 1 10x28 ffp (le5|sr5) reticle mrad black$', multi=True,
  note='Két reticle-változat (LE5 / SR5), azonos ár')

# --- Szerelék / nagyító ----------------------------------------------------
S('gs-sp30', ['Super Precision 30mm'], r'^super precision 30mm 1 93 (black|ddc)$', colors=True,
  note='Geissele Super Precision 30 mm, 1.93" magasság')
S('eo-g33', ['EoTech G33'], r'^eotech g33 sts magnifier (black|tan)$', colors=True)
S('eo-g43', ['EoTech G43'], r'^eotech vergrosserungsaufsatz g43 sts 3x magnifier', 
  note='G43.STS (STS szerelékkel); a data.js "Micro szerelék" megjelölése eltér')
S('hs-hm3x', ['Holosun HM3X'], r'^holosun magnifier hm3x magnifier$')
S('vx-m3x', ['Vortex Magnifier'], r'^vortex optics v3xm magnifier with flip mount black$',
  note='V3XM = Vortex Micro 3X magnifier (flip mount-tal)')

# --- BUIS (a bolt első/hátsó irányzékot külön árul -> összeg) --------------
S('mp-mbus3', ['Magpul MBUS'], None, set=[
    {'ok': r'^magpul mbus 3 front back up sight$'}, {'ok': r'^magpul mbus 3 rear back up sight$'}],
  note='A bolt az első és a hátsó irányzékot külön árulja; az ár az alkatrészek összege')
S('mp-mbuspro', ['Magpul MBUS Pro'], None, set=[
    {'ok': r'^magpul mbus pro sight front black$'}, {'ok': r'^magpul mbus pro sight rear black$'}],
  note='A bolt az első és a hátsó irányzékot külön árulja; az ár az alkatrészek összege')
S('mp-mbusoff', ['Magpul MBUS Pro Offset'], None, set=[
    {'ok': r'^magpul mbus pro offset sight front black$'}, {'ok': r'^magpul mbus pro offset sight rear black$'}],
  note='A bolt az első és a hátsó irányzékot külön árulja; az ár az alkatrészek összege')

# --- Lámpák ----------------------------------------------------------------
S('sl-hlx', ['Streamlight ProTac Rail Mount'], r'^streamlight protac rail mount hl x black$')
S('sl-tlr1', ['Streamlight TLR-1'], r'^streamlight tlr 1 hl black$')

# --- M-LOK -----------------------------------------------------------------
S('bcm-vg3', ['BCM Vertical Grip'], r'^bcm vertical grip m lok compatible mod 3', note='M-LOK kompatibilis Mod 3 (BCM Gunfighter)')
S('bcm-kag', ['BCM Kinesthetic Angled Grip'], r'^bcm kinesthetic angled grip m lok compatible$', note='M-LOK kompatibilis KAG (BCM Gunfighter)')
S('mp-hstop', ['Magpul Hand Stop Kit'], r'^magpul m lok hand stop kit$')
S('mp-rail5', ['Magpul M-LOK Rail Section'], r'^magpul m lok rail section aluminium 5 slots black$')
S('mp-qd', ['Magpul M-LOK QD Sling Mount'], r'^magpul m lok qd sling mount black$')
S('mp-cover', ['Magpul M-LOK Rail Cover'], r'^magpul m lok rail cover type 2$')

# --- Bipod, torkolat, elsütő, felhúzókar, kezelőszervek -------------------
S('mp-bipod', ['Magpul Bipod'], r'^magpul bipod for m lok$')
S('bcm-comp0', ['BCM Compensator'], r'^bcm compensator mod 0 5 56$')
S('gs-ssae', ['Geissele SSA-E', 'Super Semi-Automatic'], r'^super semi automatic enhanced ssa e$', r'large pin')
S('gs-ssa', ['Geissele SSA', 'Super Semi-Automatic'], r'^super semi automatic ssa$', r'large pin')
S('gs-sde', ['Geissele SD-E', 'Super Dynamic Enhanced'], r'^super dynamic enhanced sd e$', r'large pin')
S('gs-s3g', ['Geissele Super 3 Gun'], r'^geissele automatics ar15 super 3 gun s3g trigger$')
S('rd-raptor', ['Radian Raptor'], r'^(radian weapons raptor ambidextrous charging handle ar15 bl|radian raptor ambidextrous charging handle ar15 dark earth|raptor charging handle ar15 radian (grey|od green))', r'sd|lt|sl|combo|competition|ar10',
  colors=True)
S('rd-raptorlt', ['Radian Raptor-LT'], r'^radian raptor lt ambidextrous charging handle ar15', r'ar10|combo', colors=True)
S('rd-raptorsd', ['Radian Raptor-SD'], r'^(radian weapons ar 15 raptor sd ambidextrous charging handl|radian raptor sd ambidextrous charging handle ar15|raptor sd charging handle ar15)', r'ar10|combo', colors=True)
S('rd-talon', ['Radian Talon'], r'^radian weapons talon ambidextrous 45 90 safety selector', r'kit|gi\b', colors=True)
S('mp-bad', ['Magpul B.A.D.'], r'^magpul b a d lever black$')
S('mp-trigguard', ['Magpul MOE Enhanced Trigger Guard'], r'^magpul moe enhanced trigger guard polymer ar15 m4$')

# --- Tárak -----------------------------------------------------------------
S('mp-pm10', ['Magpul PMAG 10 Gen M3'], r'^magpul pmag 10 gen m3 (black|odg)$', colors=True)
S('mp-pm20', ['Magpul PMAG 20 Gen M3'], r'^magpul pmag 20 gen m3 (black|odg)$', colors=True)
S('mp-pm30', ['Magpul PMAG 30 Gen M3'], r'^magpul pmag 30 gen m3$')
S('mp-pm40', ['Magpul PMAG 40'], r'^magpul ar 15 40rd pmag gen m3 magazine 223 5 56$')

# --- Szíjak ----------------------------------------------------------------
S('mp-ms1', ['Magpul MS1 Sling'], r'^magpul ms1 sling$')
S('mp-ms4', ['Magpul MS4 Dual QD'], r'^magpul ms4 dual qd gen 2 sling$', note='A boltban Gen 2 kivitel')
S('bfg-vickers', ['Blue Force Gear Vickers'], r'^blue force gear vickers combat applications? sling$')

# Biztosan nincs a boltban (a keresés ettől még lefut, hogy a futás bizonyíthassa):
NOT_FOUND_NOTES = {
    'b5-t22': 'nincs a boltban (B5 Systems P-Grip nincs a kínálatban)',
    'b5-t23': 'nincs a boltban (B5 Systems P-Grip nincs a kínálatban)',
    'b5-esop': 'nincs a boltban (B5 Systems Enhanced SOPMOD Stock)',
    'b5-prec': 'nincs a boltban (B5 Systems Precision Stock)',
    'ap-t2': 'nincs a boltban (Aimpoint optikát nem árul, csak szereléket)',
    'ap-m5': 'nincs a boltban (Aimpoint optikát nem árul, csak szereléket)',
    'ap-pro': 'nincs a boltban (Aimpoint optikát nem árul, csak szereléket)',
    'ap-acro': 'nincs a boltban (Aimpoint optikát nem árul, csak szereléket)',
    'ap-3x': 'nincs a boltban (Aimpoint 3XMag)',
}
