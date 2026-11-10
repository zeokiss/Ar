#!/usr/bin/env python3
"""
A konfigurátor egyfájlos, megosztható (Claude Artifact) változatának összeállítása.

A kimenet egy önálló HTML (a CSS és a JS beágyazva), a fotók és adatok relatív fájlként
mellé kerülnek. Az artifact-környezet korlátai miatt kimarad a PNG-mentés és a link-megosztás
(a letöltés és a #kulcs=érték hash ott nem működik), és a „Alaphelyzet" nem kér megerősítést.

Használat:  python3 tools/build_artifact.py <kimeneti-mappa>
Kiírja a mellé publikálandó fájlok listáját (JSON).
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out_dir = sys.argv[1]
os.makedirs(out_dir, exist_ok=True)

html = open(os.path.join(ROOT, 'index.html'), encoding='utf-8').read()
css = open(os.path.join(ROOT, 'css/style.css'), encoding='utf-8').read()
js = '\n'.join(open(os.path.join(ROOT, 'js', f), encoding='utf-8').read()
               for f in ['data.js', 'photos.js', 'render.js', 'photo-render.js', 'app.js'])
assert '</script' not in js.lower()

# a keretben nincs confirm(), a letöltés és a hash-megosztás sem megy
js = js.replace("confirm('Visszaállítod a gyári állapotot?')", "true")
js = js.replace("history.replaceState(null, '', location.pathname + location.search);", "try { history.replaceState(null, '', location.pathname + location.search); } catch (e) { /* keret */ }")

body = re.search(r'<body>(.*)</body>', html, re.S).group(1)
body = re.sub(r'\s*<script src="[^"]+"></script>', '', body)
body = re.sub(r'\s*<button id="btnPng"[^>]*>.*?</button>', '', body)
body = re.sub(r'\s*<button id="btnShare"[^>]*>.*?</button>', '', body)
# az eseménykezelők nem találnák a törölt gombokat
js = js.replace("$('#btnShare').onclick = share;", "").replace("$('#btnPng').onclick = exportPng;", "")

page = f'''<title>AR-15 Konfigurátor</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root {{ color-scheme: dark; }}
{css}
</style>
{body}
<script>
{js}
</script>
'''
open(os.path.join(out_dir, 'index.html'), 'w', encoding='utf-8').write(page)

files = []
for dp, dn, fn in os.walk(os.path.join(ROOT, 'assets')):
    dn[:] = [d for d in dn if not d.startswith('_')]
    for f in fn:
        if f.endswith(('.webp', '.json', '.png', '.jpg')):
            files.append(os.path.relpath(os.path.join(dp, f), ROOT))
print(json.dumps(sorted(files)))
