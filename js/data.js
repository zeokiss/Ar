/*
 * AR-15 Konfigurátor – termékadatbázis
 *
 * Az árak TÁJÉKOZTATÓ JELLEGŰ, becsült európai bolti irányárak EUR-ban.
 * A tényleges ár kereskedőnként, országonként és időben eltér – itt szerkeszthetők.
 * A tömegek (gramm) gyártói / kereskedői adatok alapján kerekített értékek.
 *
 * Új termék felvétele: másold le egy meglévő elemet a megfelelő kategóriában,
 * adj neki egyedi `id`-t. A `style` mező határozza meg, hogyan rajzolja ki a
 * megjelenítő (js/render.js).
 */
window.AR_DATA = (function () {
  // ---------------------------------------------------------------- Színek
  const COLORS = {
    black:    { name: 'Fekete',         hex: '#232427' },
    fde:      { name: 'FDE',            hex: '#a68e69' },
    odg:      { name: 'OD Green',       hex: '#585b3c' },
    gray:     { name: 'Stealth Gray',   hex: '#5d6267' },
    wolfgray: { name: 'Wolf Grey',      hex: '#707376' },
    foliage:  { name: 'Foliage Green',  hex: '#6d7058' },
    ranger:   { name: 'Ranger Green',   hex: '#4b5241' },
    coyote:   { name: 'Coyote',         hex: '#8b7656' },
  };

  // Tok-/előagy-felületek (alapfegyver színváltozatai)
  const FINISHES = {
    black:  { name: 'Black',      hex: '#2a2c2f' },
    olive:  { name: 'Olive',      hex: '#5a5d3d' },
    earth:  { name: 'Dark Earth', hex: '#8a7455' },
    bronze: { name: 'Bronze',     hex: '#7b5d3e' },
    fde:    { name: 'FDE',        hex: '#a08865' },
  };

  const MAGPUL = ['black', 'fde', 'odg', 'gray'];
  const BCM = ['black', 'fde', 'foliage', 'wolfgray'];
  const B5 = ['black', 'fde', 'odg', 'wolfgray'];

  // ---------------------------------------------------------------- Alapfegyverek
  // hgLen: előagy hossza (hüvelyk, a felső tok elejétől) – a vizualizációhoz,
  // ahol a gyártó nem közli, becsült érték (hgEstimated: true).
  const BASES = [
    {
      id: 'schm-s4f', brand: 'Schmeisser', name: 'AR15 S4F', tag: 'Rövid karabély',
      price: 2300, weight: 2950, barrel: 10.5, cal: '.223 Rem', twist: '1:9',
      hgLen: 9.5, hgEstimated: true, hgStyle: 'schm', finishes: ['black', 'olive', 'earth', 'bronze'],
      ambi: true, factoryBuis: true, factoryMuzzle: 'a2', factoryStock: 'factory-m4', factoryGrip: 'factory',
      factoryTrigger: 'curved', factoryCh: 'ambi', oalNote: null,
      desc: '10,5" cső, M-LOK előagy, ambidex kezelőszervek, gyári Schmeisser flip-up irányzék.',
    },
    {
      id: 'schm-m4fl', brand: 'Schmeisser', name: 'AR15 M4FL', tag: 'Karabély',
      price: 2400, weight: 3250, barrel: 14.5, cal: '.223 Rem', twist: '1:9',
      hgLen: 13, hgEstimated: true, hgStyle: 'schm', finishes: ['black', 'olive', 'earth', 'bronze'],
      ambi: true, factoryBuis: true, factoryMuzzle: 'a2', factoryStock: 'factory-m4', factoryGrip: 'factory',
      factoryTrigger: 'curved', factoryCh: 'ambi', oalNote: 'Gyári adat: 795–895 mm',
      desc: '14,5" (370 mm) cső, 1:9 csavarulat, 1/2"×28 menet, M-LOK előagy, ambidex kezelőszervek.',
    },
    {
      id: 'schm-m5fl', brand: 'Schmeisser', name: 'AR15 M5FL', tag: 'Puska',
      price: 2500, weight: 3550, barrel: 16.75, cal: '.223 Rem', twist: '1:9',
      hgLen: 15, hgEstimated: true, hgStyle: 'schm', finishes: ['black', 'olive', 'earth', 'bronze'],
      ambi: true, factoryBuis: true, factoryMuzzle: 'a2', factoryStock: 'factory-m4', factoryGrip: 'factory',
      factoryTrigger: 'curved', factoryCh: 'ambi', oalNote: 'Gyári adat: 875–950 mm',
      desc: '16,75" (425 mm) cső, hosszú M-LOK előagy, lángrejtő, ambidex kezelőszervek.',
    },
    {
      id: 'schm-dyn', brand: 'Schmeisser', name: 'AR15 Dynamic M5FL', tag: 'Sport / IPSC',
      price: 2800, weight: 2900, barrel: 16.75, cal: '.223 Rem', twist: '1:9',
      hgLen: 15, hgEstimated: true, hgStyle: 'dyn', finishes: ['black', 'bronze'],
      ambi: true, factoryBuis: false, factoryMuzzle: 'comp', factoryStock: 'factory-m4', factoryGrip: 'factory',
      factoryTrigger: 'flat', factoryCh: 'ambi', oalNote: null,
      desc: 'Könnyített, vázszerkezetes kialakítás, Schmeisser Dynamic elsütő, optikára kész flat-top.',
    },
    {
      id: 'dd-v7', brand: 'Daniel Defense', name: 'DDM4 V7', tag: 'Puska',
      price: 2400, weight: 2810, barrel: 16, cal: '5.56 NATO', twist: '1:7',
      hgLen: 15, hgEstimated: false, hgStyle: 'mfr', finishes: ['black'],
      ambi: false, factoryBuis: false, factoryMuzzle: 'a2', factoryStock: 'factory-m4', factoryGrip: 'factory',
      factoryTrigger: 'curved', factoryCh: 'mil', oalNote: null,
      desc: '16" CHF cső, 15" MFR XL M-LOK előagy, mid-length gázrendszer.',
    },
    {
      id: 'bcm-recce16', brand: 'BCM', name: 'RECCE-16 MCMR', tag: 'Puska',
      price: 2000, weight: 2900, barrel: 16, cal: '5.56 NATO', twist: '1:7',
      hgLen: 15, hgEstimated: false, hgStyle: 'mcmr', finishes: ['black'],
      ambi: false, factoryBuis: false, factoryMuzzle: 'comp', factoryStock: 'bcm-mod0', factoryGrip: 'bcm-mod3',
      factoryTrigger: 'curved', factoryCh: 'bcm', oalNote: null,
      desc: '16" cső, 15" MCMR M-LOK előagy, BCM Gunfighter tus, markolat és felhúzókar.',
    },
  ];

  // ---------------------------------------------------------------- Kategóriák
  // multi: több elem is választható; group: kizáró csoport a multi kategórián belül
  const CATEGORIES = [
    { id: 'base',      name: 'Alapfegyver',          short: 'Alap' },
    { id: 'grip',      name: 'Pisztolymarkolat',     short: 'Markolat' },
    { id: 'stock',     name: 'Tus',                  short: 'Tus' },
    { id: 'buttpad',   name: 'Tustalp / vállpárna',  short: 'Tustalp' },
    { id: 'optic',     name: 'Red dot / holo',       short: 'Red dot' },
    { id: 'lpvo',      name: 'LPVO / prizmás',       short: 'LPVO' },
    { id: 'mount',     name: 'Optikaszerelék',       short: 'Szerelék' },
    { id: 'magnifier', name: 'Nagyító',              short: 'Nagyító' },
    { id: 'buis',      name: 'Vas irányzék (BUIS)',  short: 'BUIS' },
    { id: 'light',     name: 'Fegyverlámpa',         short: 'Lámpa' },
    { id: 'mlok',      name: 'M-LOK kiegészítők',    short: 'M-LOK', multi: true },
    { id: 'bipod',     name: 'Bipod',                short: 'Bipod' },
    { id: 'muzzle',    name: 'Csőtorkolat',          short: 'Torkolat' },
    { id: 'trigger',   name: 'Elsütőszerkezet',      short: 'Elsütő' },
    { id: 'ch',        name: 'Felhúzókar',           short: 'Felhúzó' },
    { id: 'controls',  name: 'Kezelőszervek',        short: 'Kezelő', multi: true },
    { id: 'mag',       name: 'Tár',                  short: 'Tár' },
    { id: 'sling',     name: 'Szíj',                 short: 'Szíj' },
  ];

  // "Gyári" (alapfegyverrel szállított) opció, ahol értelmes
  const FACTORY = (cat, name, extra) => Object.assign({ id: cat + '-factory', cat, brand: 'Gyári', name, price: 0, weight: 0, factory: true }, extra || {});
  const NONE = (cat, name) => ({ id: cat + '-none', cat, brand: '—', name: name || 'Nincs', price: 0, weight: 0, none: true });

  const ITEMS = [
    // ----------------------------------------------------------- Markolatok
    FACTORY('grip', 'Gyári markolat', { style: 'factory', angle: 23, weightRef: 75 }),
    { id: 'mp-moe', cat: 'grip', brand: 'Magpul', name: 'MOE Grip', price: 25, weight: 80, colors: MAGPUL, style: 'moe', angle: 23, specs: ['Klasszikus szög', 'Tárolórekesz-kompatibilis'] },
    { id: 'mp-moek2', cat: 'grip', brand: 'Magpul', name: 'MOE-K2 Grip', price: 30, weight: 80, colors: MAGPUL, style: 'k2', angle: 17, specs: ['Meredekebb szög', 'Kompakt tusokhoz'] },
    { id: 'mp-moek2p', cat: 'grip', brand: 'Magpul', name: 'MOE-K2+ Grip', price: 35, weight: 85, colors: MAGPUL, style: 'k2', angle: 17, tex: true, specs: ['Gumírozott borítás', 'Meredek szög'] },
    { id: 'mp-miad', cat: 'grip', brand: 'Magpul', name: 'MIAD Gen 1.1', price: 45, weight: 110, colors: MAGPUL, style: 'miad', angle: 22, specs: ['Moduláris hát- és előlap', 'Állítható méret'] },
    { id: 'bcm-g0', cat: 'grip', brand: 'BCM', name: 'Gunfighter Grip Mod 0', price: 30, weight: 70, colors: BCM, style: 'bcm', angle: 19, specs: ['Közepes szög', 'Ujjtámasz nélküli'] },
    { id: 'bcm-g3', cat: 'grip', brand: 'BCM', name: 'Gunfighter Grip Mod 3', price: 30, weight: 70, colors: BCM, style: 'bcm3', angle: 17, specs: ['Meredek szög', 'Hátsó nyúlvány'] },
    { id: 'b5-t22', cat: 'grip', brand: 'B5 Systems', name: 'Type 22 P-Grip', price: 30, weight: 75, colors: B5, style: 'b5', angle: 22, tex: true, specs: ['Hagyományos szög', 'Erős textúra'] },
    { id: 'b5-t23', cat: 'grip', brand: 'B5 Systems', name: 'Type 23 P-Grip', price: 30, weight: 75, colors: B5, style: 'b5', angle: 17, tex: true, specs: ['Meredek szög', 'Erős textúra'] },

    // ----------------------------------------------------------- Tusok
    FACTORY('stock', 'Gyári 6-pozíciós tus', { style: 'm4', type: 'carbine', weightRef: 250 }),
    { id: 'mp-ctr', cat: 'stock', brand: 'Magpul', name: 'CTR Carbine Stock', price: 80, weight: 270, colors: MAGPUL, style: 'ctr', type: 'carbine', pad: 'ctr', specs: ['Súrlódó zár', 'QD szíjcsatlakozó'] },
    { id: 'mp-moesl', cat: 'stock', brand: 'Magpul', name: 'MOE SL Carbine Stock', price: 70, weight: 255, colors: MAGPUL, style: 'sl', type: 'carbine', pad: 'sl', specs: ['Karcsú profil', 'Akadásmentes'] },
    { id: 'mp-moeslk', cat: 'stock', brand: 'Magpul', name: 'MOE SL-K Carbine Stock', price: 70, weight: 230, colors: MAGPUL, style: 'slk', type: 'carbine', pad: 'slk', specs: ['Rövidebb kompakt tus', 'Plate carrierhez'] },
    { id: 'mp-ubr2', cat: 'stock', brand: 'Magpul', name: 'UBR Gen2 Collapsible', price: 230, weight: 590, colors: MAGPUL, style: 'ubr', type: 'carbine', pad: 'ctr', specs: ['Zárt szerkezet', 'Stabil arcfekvés'] },
    { id: 'mp-prs3', cat: 'stock', brand: 'Magpul', name: 'PRS Gen3 Precision Stock', price: 290, weight: 620, colors: MAGPUL, style: 'prs', type: 'rifle', pad: 'prs', specs: ['Állítható arcdeszka', 'Állítható tushossz', 'Rifle puffercső'] },
    { id: 'mp-prslite', cat: 'stock', brand: 'Magpul', name: 'PRS Lite Stock', price: 120, weight: 450, colors: MAGPUL, style: 'prslite', type: 'rifle', pad: 'prs', specs: ['Könnyített precíziós tus', 'Rifle puffercső'] },
    { id: 'bcm-s0', cat: 'stock', brand: 'BCM', name: 'Gunfighter Stock Mod 0', price: 65, weight: 220, colors: BCM, style: 'bcm', type: 'carbine', pad: 'own', specs: ['Könnyű', 'QD foglalat'] },
    { id: 'bcm-s1', cat: 'stock', brand: 'BCM', name: 'Gunfighter Stock Mod 1 SOPMOD', price: 85, weight: 300, colors: BCM, style: 'sopmod', type: 'carbine', pad: 'own', specs: ['SOPMOD forma', 'Tárolórekesz'] },
    { id: 'b5-bravo', cat: 'stock', brand: 'B5 Systems', name: 'Bravo Stock', price: 60, weight: 200, colors: B5, style: 'bravo', type: 'carbine', pad: 'own', specs: ['Nagyon könnyű', 'QD + hurok'] },
    { id: 'b5-esop', cat: 'stock', brand: 'B5 Systems', name: 'Enhanced SOPMOD Stock', price: 70, weight: 290, colors: B5, style: 'sopmod', type: 'carbine', pad: 'own', specs: ['SOPMOD forma', 'Tárolórekesz'] },
    { id: 'b5-prec', cat: 'stock', brand: 'B5 Systems', name: 'Precision Stock', price: 110, weight: 440, colors: B5, style: 'b5prec', type: 'rifle', pad: 'own', specs: ['Fix precíziós tus', 'Rifle puffercső'] },

    // ----------------------------------------------------------- Tustalp
    FACTORY('buttpad', 'Gyári / tus saját tustalpa', { style: 'std' }),
    { id: 'mp-pad030', cat: 'buttpad', brand: 'Magpul', name: 'Rubber Butt-Pad 0.30"', price: 20, weight: 40, style: 'pad30', fits: ['ctr'], specs: ['CTR / MOE mil-spec tusokhoz', 'Alap vastagság'] },
    { id: 'mp-pad055', cat: 'buttpad', brand: 'Magpul', name: 'Extended Rubber Butt-Pad 0.55"', price: 25, weight: 55, style: 'pad55', fits: ['ctr'], lop: 0.25, specs: ['CTR / MOE tusokhoz', '+0,25" tushossz'] },
    { id: 'mp-pad070', cat: 'buttpad', brand: 'Magpul', name: 'Enhanced Rubber Butt-Pad 0.70"', price: 25, weight: 70, style: 'pad70', fits: ['ctr'], lop: 0.4, specs: ['CTR / MOE / UBR tusokhoz', 'Szellőzött, hátrarúgás-csillapító'] },
    { id: 'mp-padsl', cat: 'buttpad', brand: 'Magpul', name: 'MOE SL Enhanced Butt-Pad 0.70"', price: 25, weight: 65, style: 'pad70', fits: ['sl'], lop: 0.4, specs: ['Csak teljes méretű MOE SL tushoz', 'SL-K / SL-M nem kompatibilis'] },

    // ----------------------------------------------------------- Red dot / holo (szerelékkel)
    NONE('optic', 'Nincs red dot'),
    { id: 'ap-t2', cat: 'optic', brand: 'Aimpoint', name: 'Micro T-2 + LRP szerelék', price: 1000, weight: 165, style: 'micro', axis: 1.93, specs: ['2 MOA', '50 000 óra elem', 'Lower 1/3'] },
    { id: 'ap-m5', cat: 'optic', brand: 'Aimpoint', name: 'CompM5 + LRP szerelék', price: 980, weight: 210, style: 'micro', axis: 1.93, specs: ['2 MOA', 'AAA elem', 'NV-kompatibilis'] },
    { id: 'ap-pro', cat: 'optic', brand: 'Aimpoint', name: 'PRO + QRP2 szerelék', price: 480, weight: 330, style: 'tube', axis: 1.5, specs: ['2 MOA', 'Abszolút co-witness', 'Robosztus'] },
    { id: 'ap-acro', cat: 'optic', brand: 'Aimpoint', name: 'ACRO P-2 + Picatinny talp', price: 700, weight: 100, style: 'acro', axis: 1.5, specs: ['Zárt emitter', '3,5 MOA', 'Kompakt'] },
    { id: 'eo-exps3', cat: 'optic', brand: 'EOTech', name: 'EXPS3-0 HWS', price: 760, weight: 320, style: 'holo', axis: 1.45, specs: ['68 MOA kör + 1 MOA pont', 'NV mód', 'QD szerelék'] },
    { id: 'eo-exps2', cat: 'optic', brand: 'EOTech', name: 'EXPS2-0 HWS', price: 680, weight: 320, style: 'holo', axis: 1.45, specs: ['68 MOA kör + 1 MOA pont', 'Gyors oldású'] },
    { id: 'eo-xps2', cat: 'optic', brand: 'EOTech', name: 'XPS2-0 HWS', price: 620, weight: 255, style: 'holo', short: true, axis: 1.3, specs: ['Kompakt test', 'CR123 elem'] },
    { id: 'hs-510c', cat: 'optic', brand: 'Holosun', name: 'HS510C', price: 330, weight: 255, style: 'reflex', axis: 1.45, specs: ['Nyitott reflex', 'Napelem + Shake Awake', 'Kör + pont'] },
    { id: 'hs-aems', cat: 'optic', brand: 'Holosun', name: 'AEMS', price: 320, weight: 200, style: 'aems', axis: 1.5, specs: ['Zárt doboz forma', 'Napelem', 'Széles látómező'] },
    { id: 'hs-403b', cat: 'optic', brand: 'Holosun', name: 'HS403B + magasító', price: 190, weight: 150, style: 'micro', axis: 1.93, specs: ['2 MOA', 'Kedvező ár', 'Shake Awake'] },
    { id: 'tj-mro', cat: 'optic', brand: 'Trijicon', name: 'MRO HD + Lower 1/3 szerelék', price: 620, weight: 200, style: 'mro', axis: 1.93, specs: ['Nagy objektív', '68 MOA kör + pont', 'Lower 1/3'] },
    { id: 'vx-uh1', cat: 'optic', brand: 'Vortex', name: 'UH-1 Gen II holografikus', price: 560, weight: 310, style: 'uh1', axis: 1.45, specs: ['Holografikus', 'NV-kompatibilis', 'USB tölthető'] },
    { id: 'vx-sparc', cat: 'optic', brand: 'Vortex', name: 'SPARC AR + magasító', price: 210, weight: 210, style: 'micro', axis: 1.93, specs: ['2 MOA', 'Belépő szint'] },
    { id: 'vx-crossfire', cat: 'optic', brand: 'Vortex', name: 'Crossfire Red Dot + magasító', price: 160, weight: 200, style: 'micro', axis: 1.93, specs: ['2 MOA', 'Belépő szint'] },

    // ----------------------------------------------------------- LPVO / prizmás
    NONE('lpvo', 'Nincs LPVO / prizmás'),
    { id: 'vx-se16', cat: 'lpvo', brand: 'Vortex', tube: 30, name: 'Strike Eagle 1-6x24', price: 380, weight: 520, style: 'lpvo', len: 10.5, obj: 24, needsMount: true, specs: ['1–6x', 'SFP', '30 mm tubus'] },
    { id: 'vx-se18', cat: 'lpvo', brand: 'Vortex', tube: 30, name: 'Strike Eagle 1-8x24', price: 450, weight: 560, style: 'lpvo', len: 10.6, obj: 24, needsMount: true, specs: ['1–8x', 'SFP', '30 mm tubus'] },
    { id: 'vx-pst16', cat: 'lpvo', brand: 'Vortex', tube: 30, name: 'Viper PST Gen II 1-6x24', price: 700, weight: 600, style: 'lpvo', len: 10.4, obj: 24, needsMount: true, specs: ['1–6x', 'SFP', 'Világító szálkereszt'] },
    { id: 'vx-razor110', cat: 'lpvo', brand: 'Vortex', tube: 34, name: 'Razor HD Gen III 1-10x24', price: 2000, weight: 610, style: 'lpvo', len: 10.1, obj: 24, needsMount: true, specs: ['1–10x', 'FFP', 'Prémium üveg'] },
    { id: 'tj-acog', cat: 'lpvo', brand: 'Trijicon', name: 'ACOG TA31 4x32', price: 1400, weight: 430, style: 'acog', contact: true, specs: ['Fix 4x prizmás', 'Elem nélküli megvilágítás', 'Szerelékkel'] },
    { id: 'tj-credo16', cat: 'lpvo', brand: 'Trijicon', tube: 30, name: 'Credo 1-6x24', price: 900, weight: 560, style: 'lpvo', len: 10.3, obj: 24, needsMount: true, specs: ['1–6x', 'SFP', '30 mm tubus'] },
    { id: 'tj-vcog', cat: 'lpvo', brand: 'Trijicon', name: 'VCOG 1-8x28', price: 2100, weight: 650, style: 'lpvo', len: 10.2, obj: 28, integralMount: true, contact: true, specs: ['1–8x', 'FFP', 'Integrált szerelék'] },
    { id: 'eo-vudu16', cat: 'lpvo', brand: 'EOTech', tube: 30, name: 'Vudu 1-6x24', price: 1200, weight: 570, style: 'lpvo', len: 10.2, obj: 24, needsMount: true, specs: ['1–6x', 'FFP / SFP', '30 mm tubus'] },
    { id: 'eo-vudu110', cat: 'lpvo', brand: 'EOTech', tube: 34, name: 'Vudu 1-10x28', price: 1700, weight: 640, style: 'lpvo', len: 10.8, obj: 28, needsMount: true, specs: ['1–10x', 'FFP', '34 mm tubus'] },

    // ----------------------------------------------------------- Szerelék (LPVO-hoz)
    NONE('mount', 'Nincs külön szerelék'),
    { id: 'gs-sp30', cat: 'mount', brand: 'Geissele', tube: 30, name: 'Super Precision 30 mm', price: 420, weight: 230, style: 'geissele', axis: 1.93, specs: ['30 mm', '1,93" magasság', 'Prémium'] },
    { id: 'vx-cant30', cat: 'mount', brand: 'Vortex', tube: 30, name: 'Sport Cantilever 30 mm', price: 80, weight: 260, style: 'cant', axis: 1.5, specs: ['30 mm', '2" előretolás'] },

    // ----------------------------------------------------------- Nagyító
    NONE('magnifier', 'Nincs nagyító'),
    { id: 'eo-g33', cat: 'magnifier', brand: 'EOTech', name: 'G33 + STS szerelék', price: 650, weight: 380, style: 'g33', specs: ['3x', 'Oldalra billenthető'] },
    { id: 'eo-g43', cat: 'magnifier', brand: 'EOTech', name: 'G43 + Micro szerelék', price: 700, weight: 300, style: 'g43', specs: ['3x', 'Kompakt'] },
    { id: 'ap-3x', cat: 'magnifier', brand: 'Aimpoint', name: '3XMag-1 + Flip szerelék', price: 560, weight: 320, style: 'g43', specs: ['3x', 'Billenthető'] },
    { id: 'hs-hm3x', cat: 'magnifier', brand: 'Holosun', name: 'HM3X', price: 210, weight: 310, style: 'g43', specs: ['3x', 'Billenthető szerelék'] },
    { id: 'vx-m3x', cat: 'magnifier', brand: 'Vortex', name: 'Micro3X + Flip szerelék', price: 330, weight: 340, style: 'g43', specs: ['3x', 'Billenthető'] },

    // ----------------------------------------------------------- BUIS
    FACTORY('buis', 'Gyári flip-up irányzék', { style: 'factory' }),
    NONE('buis', 'Nincs vas irányzék'),
    { id: 'mp-mbus3', cat: 'buis', brand: 'Magpul', name: 'MBUS 3 szett', price: 100, weight: 95, colors: ['black', 'fde'], style: 'mbus', specs: ['Polimer', 'Lecsukható'] },
    { id: 'mp-mbuspro', cat: 'buis', brand: 'Magpul', name: 'MBUS Pro szett', price: 210, weight: 85, style: 'mbuspro', specs: ['Acél', 'Lecsukható'] },
    { id: 'mp-mbusoff', cat: 'buis', brand: 'Magpul', name: 'MBUS Pro Offset szett', price: 210, weight: 90, style: 'offset', specs: ['45°-os oldalirányzék', 'LPVO mellé'] },

    // ----------------------------------------------------------- Lámpa
    NONE('light', 'Nincs lámpa'),
    { id: 'sf-m600', cat: 'light', brand: 'SureFire', name: 'M600DF Scout Light Pro', price: 380, weight: 190, style: 'scout', specs: ['1500 lm', 'M-LOK / Pic szerelék', 'Kettős kapcsoló'] },
    { id: 'sf-m640', cat: 'light', brand: 'SureFire', on: 'top', name: 'M640DF Scout Light Pro', price: 450, weight: 215, style: 'scout', big: true, specs: ['1500 lm', 'Nagyobb fej', 'Távolra'] },
    { id: 'sf-m300', cat: 'light', brand: 'SureFire', name: 'M300 Mini Scout Light Pro', price: 330, weight: 120, style: 'scout', mini: true, specs: ['Kompakt', 'CR123'] },
    { id: 'sf-x300', cat: 'light', brand: 'SureFire', on: 'bottom', name: 'X300U-B', price: 330, weight: 115, style: 'x300', needsRail: true, specs: ['1000 lm', 'Picatinny sín kell'] },
    { id: 'sl-hlx', cat: 'light', brand: 'Streamlight', on: 'top', name: 'ProTac Rail Mount HL-X', price: 170, weight: 180, style: 'scout', big: true, specs: ['1000 lm', 'Kedvező ár'] },
    { id: 'sl-tlr1', cat: 'light', brand: 'Streamlight', on: 'bottom', name: 'TLR-1 HL', price: 170, weight: 120, style: 'x300', needsRail: true, specs: ['1000 lm', 'Picatinny sín kell'] },

    // ----------------------------------------------------------- M-LOK kiegészítők (több is választható)
    { id: 'mp-rvg', cat: 'mlok', brand: 'Magpul', name: 'M-LOK RVG függőleges markolat', price: 25, weight: 60, colors: MAGPUL, style: 'rvg', group: 'fgrip', specs: ['Függőleges markolat', 'Polimer'] },
    { id: 'mp-hstop', cat: 'mlok', brand: 'Magpul', name: 'M-LOK Hand Stop Kit', price: 20, weight: 25, colors: MAGPUL, style: 'hstop', group: 'fgrip', specs: ['Kézütköző', 'Alacsony profil'] },
    { id: 'bcm-vg3', cat: 'mlok', brand: 'BCM', name: 'Gunfighter Vertical Grip Mod 3', price: 40, weight: 50, colors: BCM, style: 'vg3', group: 'fgrip', specs: ['Rövid függőleges markolat'] },
    { id: 'bcm-kag', cat: 'mlok', brand: 'BCM', name: 'Gunfighter KAG (Kinesthetic Angled Grip)', price: 35, weight: 45, colors: BCM, style: 'kag', group: 'fgrip', specs: ['Szögletes előmarkolat'] },
    { id: 'mp-rail5', cat: 'mlok', brand: 'Magpul', name: 'M-LOK Picatinny sín, 5 slot (aluminium)', price: 18, weight: 20, style: 'rail', specs: ['Lámpa / lézer felszereléséhez'] },
    { id: 'mp-qd', cat: 'mlok', brand: 'Magpul', name: 'M-LOK QD szíjcsatlakozó', price: 25, weight: 15, style: 'qd', specs: ['QD foglalat', '±90° stop'] },
    { id: 'mp-cover', cat: 'mlok', brand: 'Magpul', name: 'M-LOK Rail Cover Type 2', price: 15, weight: 20, colors: MAGPUL, style: 'cover', specs: ['Fogásvédő burkolat', 'Hőszigetelés'] },
    { id: 'mp-cable', cat: 'mlok', brand: 'Magpul', name: 'M-LOK Cable Management Kit', price: 15, weight: 10, style: 'cable', specs: ['Kapcsolókábel rögzítése'] },

    // ----------------------------------------------------------- Bipod
    NONE('bipod', 'Nincs bipod'),
    { id: 'mp-bipod', cat: 'bipod', brand: 'Magpul', name: 'Bipod M-LOK', price: 110, weight: 330, colors: ['black', 'fde'], style: 'magpul', specs: ['6,3–10,3"', 'Dönthető', 'Közvetlen M-LOK'] },
    { id: 'hr-sbrm', cat: 'bipod', brand: 'Harris', name: 'S-BRM 6–9" + M-LOK adapter', price: 170, weight: 450, style: 'harris', specs: ['6–9"', 'Dönthető', 'Klasszikus'] },

    // ----------------------------------------------------------- Csőtorkolat
    FACTORY('muzzle', 'Gyári torkolati eszköz', { style: 'factory' }),
    { id: 'sf-warcomp', cat: 'muzzle', brand: 'SureFire', name: 'WarComp 5.56', price: 180, weight: 100, style: 'warcomp', len: 2.25, specs: ['Kompenzátor + lángrejtő', 'Hangtompító-adapter', '1/2"×28'] },
    { id: 'sf-3p', cat: 'muzzle', brand: 'SureFire', name: 'SF3P-556 lángrejtő', price: 140, weight: 85, style: '3p', len: 2.0, specs: ['Háromágú lángrejtő', 'Hangtompító-adapter', '1/2"×28'] },
    { id: 'bcm-comp0', cat: 'muzzle', brand: 'BCM', name: 'Gunfighter Compensator Mod 0', price: 90, weight: 70, style: 'bcmcomp', len: 1.75, specs: ['Kompenzátor', 'Kis oldalnyomás', '1/2"×28'] },
    { id: 'sf-rc2', cat: 'muzzle', brand: 'SureFire', name: 'SOCOM556-RC2 hangtompító + WarComp', price: 1700, weight: 600, style: 'can', len: 7.3, dia: 1.5, specs: ['Hangtompító', 'Gyorsrögzítés', 'Engedélyköteles'] },

    // ----------------------------------------------------------- Elsütő
    FACTORY('trigger', 'Gyári elsütő', { style: 'factory' }),
    { id: 'gs-ssae', cat: 'trigger', brand: 'Geissele', name: 'SSA-E kétfokozatú', price: 270, weight: 70, style: 'curved', specs: ['Kétfokozatú', '~1,6 kg (3,5 lb)'] },
    { id: 'gs-ssa', cat: 'trigger', brand: 'Geissele', name: 'SSA kétfokozatú', price: 230, weight: 70, style: 'curved', specs: ['Kétfokozatú', '~2,1 kg (4,6 lb)'] },
    { id: 'gs-sde', cat: 'trigger', brand: 'Geissele', name: 'SD-E (Super Dynamic Enhanced)', price: 260, weight: 70, style: 'curved', specs: ['Kétfokozatú', 'Gyors visszaállás'] },
    { id: 'gs-s3g', cat: 'trigger', brand: 'Geissele', name: 'Super 3 Gun (S3G)', price: 260, weight: 70, style: 'flat', specs: ['Egyfokozatú', 'Lapos elsütőbillentyű', '~1,6 kg'] },

    // ----------------------------------------------------------- Felhúzókar
    FACTORY('ch', 'Gyári felhúzókar', { style: 'factory' }),
    { id: 'rd-raptor', cat: 'ch', brand: 'Radian', name: 'Raptor ambidex', price: 95, weight: 50, colors: ['black', 'fde', 'odg'], style: 'raptor', specs: ['Ambidex', 'Alumínium'] },
    { id: 'rd-raptorlt', cat: 'ch', brand: 'Radian', name: 'Raptor-LT ambidex', price: 85, weight: 35, style: 'raptor', specs: ['Könnyített', 'Ambidex'] },
    { id: 'rd-raptorsd', cat: 'ch', brand: 'Radian', name: 'Raptor-SD (hangtompítós)', price: 115, weight: 55, style: 'raptor', specs: ['Gázterelés hangtompítóhoz', 'Ambidex'] },
    { id: 'bcm-ch4', cat: 'ch', brand: 'BCM', name: 'Gunfighter Charging Handle Mod 4', price: 55, weight: 45, style: 'bcm', specs: ['Nagy bal oldali kar'] },
    { id: 'bcm-ch3', cat: 'ch', brand: 'BCM', name: 'Gunfighter Charging Handle Mod 3', price: 50, weight: 45, style: 'bcm', specs: ['Közepes kar'] },
    { id: 'gs-ach', cat: 'ch', brand: 'Geissele', name: 'Airborne Charging Handle', price: 90, weight: 45, style: 'raptor', specs: ['Ambidex', 'Akadásmentes'] },

    // ----------------------------------------------------------- Kezelőszervek (több is)
    { id: 'rd-talon', cat: 'controls', brand: 'Radian', name: 'Talon ambidex biztosító', price: 80, weight: 15, style: 'talon', specs: ['45° / 90° karok', 'Ambidex'] },
    { id: 'mp-bad', cat: 'controls', brand: 'Magpul', name: 'B.A.D. Lever (zárakasztó-hosszabbító)', price: 30, weight: 10, style: 'bad', specs: ['Zárakasztó elérése jobb kézzel'] },
    { id: 'mp-trigguard', cat: 'controls', brand: 'Magpul', name: 'MOE Enhanced Trigger Guard', price: 12, weight: 10, colors: MAGPUL, style: 'tguard', specs: ['Nagyobb sátor kesztyűhöz'] },

    // ----------------------------------------------------------- Tár
    FACTORY('mag', 'Gyári 10-es tár', { style: 'gi10' }),
    { id: 'mp-pm10', cat: 'mag', brand: 'Magpul', name: 'PMAG 10 AR/M4 Gen M3', price: 20, weight: 95, colors: MAGPUL, style: 'pmag10', rounds: 10, specs: ['10 töltény', 'Polimer'] },
    { id: 'mp-pm20', cat: 'mag', brand: 'Magpul', name: 'PMAG 20 AR/M4 Gen M3', price: 20, weight: 110, colors: MAGPUL, style: 'pmag20', rounds: 20, specs: ['20 töltény', 'Fekvő lövészethez'] },
    { id: 'mp-pm30', cat: 'mag', brand: 'Magpul', name: 'PMAG 30 AR/M4 Gen M3', price: 20, weight: 135, colors: MAGPUL, style: 'pmag30', rounds: 30, specs: ['30 töltény', 'Polimer'] },
    { id: 'mp-pm30w', cat: 'mag', brand: 'Magpul', name: 'PMAG 30 Gen M3 Window', price: 25, weight: 140, colors: ['black', 'fde'], style: 'pmag30w', rounds: 30, specs: ['30 töltény', 'Töltényszint-ablak'] },
    { id: 'mp-pm40', cat: 'mag', brand: 'Magpul', name: 'PMAG 40 AR/M4 Gen M3', price: 25, weight: 165, colors: MAGPUL, style: 'pmag40', rounds: 40, specs: ['40 töltény'] },
    { id: 'mp-d60', cat: 'mag', brand: 'Magpul', name: 'PMAG D-60 dobtár', price: 130, weight: 590, colors: ['black', 'fde'], style: 'drum', rounds: 60, specs: ['60 töltény', 'Dobtár'] },

    // ----------------------------------------------------------- Szíj
    NONE('sling', 'Nincs szíj'),
    { id: 'mp-ms1', cat: 'sling', brand: 'Magpul', name: 'MS1 Sling', price: 50, weight: 150, colors: ['black', 'coyote', 'ranger'], style: 'ms1', specs: ['Két/egypontos', 'Gyors állítás'] },
    { id: 'mp-ms4', cat: 'sling', brand: 'Magpul', name: 'MS4 Dual QD Sling', price: 70, weight: 170, colors: ['black', 'coyote', 'ranger'], style: 'ms1', specs: ['Két/egypontos', 'QD csatlakozók'] },
    { id: 'bfg-vickers', cat: 'sling', brand: 'Blue Force Gear', name: 'Vickers Combat Applications Sling', price: 55, weight: 140, colors: ['black', 'coyote', 'ranger'], style: 'vickers', specs: ['Kétpontos', 'Gyors állítás'] },
  ];

  // Márkák rövid leírása (a "Márkák" nézethez)
  const BRANDS = {
    'Magpul':          { core: true, note: 'Polimer bútorzat, tárak, M-LOK rendszer, irányzékok, bipod, szíjak' },
    'BCM':             { core: true, note: 'Gunfighter markolatok, tusok, felhúzókarok, előmarkolatok' },
    'B5 Systems':      { core: true, note: 'Könnyű tusok és markolatok' },
    'Geissele':        { core: true, note: 'Prémium elsütők, felhúzókar, optikaszerelékek' },
    'Radian':          { core: true, note: 'Raptor felhúzókarok, Talon ambidex biztosító' },
    'Aimpoint':        { core: true, note: 'Red dot irányzékok, nagyítók' },
    'EOTech':          { core: true, note: 'Holografikus irányzékok, nagyítók, Vudu LPVO-k' },
    'Holosun':         { core: true, note: 'Kedvező árú reflex és red dot irányzékok' },
    'Trijicon':        { core: true, note: 'ACOG, VCOG, Credo, MRO' },
    'Vortex':          { core: true, note: 'LPVO-k, red dotok, szerelékek, nagyítók' },
    'SureFire':        { core: false, note: 'Fegyverlámpák, torkolati eszközök, hangtompítók' },
    'Streamlight':     { core: false, note: 'Kedvező árú fegyverlámpák' },
    'Harris':          { core: false, note: 'Klasszikus bipodok' },
    'Blue Force Gear': { core: false, note: 'Vickers szíjak' },
  };

  return { COLORS, FINISHES, BASES, CATEGORIES, ITEMS, BRANDS };
})();
