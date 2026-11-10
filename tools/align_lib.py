"""
Közös segédfüggvények az illesztés-ellenőrzéshez: a böngészőben használt transzformáció pontos másolata,
hüvelykben mért rácson (100 px/hüvelyk). A fegyver koordinátái: x előre, y lefelé, origó = a felső tok sín hátulja.
"""
import glob, json, os
import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH = os.path.join(ROOT, 'assets', 'photos')
S = 28.0            # SVG px / hüvelyk (mint a photo-render.js-ben)
RES = 100           # rács: px / hüvelyk
X0, X1, Y0, Y1 = -8.0, 28.0, -4.0, 9.0
GW, GH = int((X1 - X0) * RES), int((Y1 - Y0) * RES)


def load_records():
    recs = []
    for f in [os.path.join(PH, 'manifest.json')] + sorted(glob.glob(os.path.join(PH, 'packs', '*.json'))):
        recs += json.load(open(f, encoding='utf-8')).get('photos', [])
    return recs


def find(recs, item, slot, color=None, facing='left'):
    """A getPart() sorrendje: azonos irányú -> main -> ellentétes irányú."""
    other = 'right' if facing == 'left' else 'left'
    for sl in (slot + '@' + facing, slot, slot + '@' + other):
        c = [r for r in recs if r['item'] == item and r['slot'] == sl]
        if color is not None:
            cc = [r for r in c if r.get('color') == color]
            if cc: return cc[0]
        if c: return c[0]
    return None


def to_grid(rec, xin, yin, base_facing='left'):
    """A rekord alfa-maszkja a rácson (bool tömb GH x GW) és a kép RGBA-ja ugyanazon a rácson."""
    D = -1 if base_facing == 'left' else 1
    k = (S / rec['ppi']) * rec.get('scale', 1)
    F = D * (-1 if rec['facing'] == 'left' else 1)
    Xo = D * (xin + (rec.get('dx') or 0)) * S
    Yo = (yin + (rec.get('dy') or 0)) * S
    th = np.deg2rad((rec.get('rot') or 0) * D)
    c, s = np.cos(th), np.sin(th)
    # p_svg = T(Xo,Yo) R(th) Scale(F k, k) T(-ax,-ay) p_px
    A = np.array([[1, 0, Xo], [0, 1, Yo], [0, 0, 1]])
    Rm = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    Sm = np.array([[F * k, 0, 0], [0, k, 0], [0, 0, 1]])
    Tm = np.array([[1, 0, -rec['ax']], [0, 1, -rec['ay']], [0, 0, 1]])
    M_px_to_svg = A @ Rm @ Sm @ Tm
    # rács px -> svg: x_in = svgX/(D S), y_in = svgY/S ; rács: gx = (x_in - X0)*RES, gy = (y_in - Y0)*RES
    G = np.array([[D * S / RES, 0, D * S * X0], [0, S / RES, S * Y0], [0, 0, 1]])  # rács px -> svg
    inv = np.linalg.inv(M_px_to_svg) @ G   # rács px -> kép px
    im = Image.open(os.path.join(PH, rec['src'])).convert('RGBA')
    out = im.transform((GW, GH), Image.AFFINE, (inv[0, 0], inv[0, 1], inv[0, 2], inv[1, 0], inv[1, 1], inv[1, 2]), resample=Image.BILINEAR)
    return out


def alpha(im, thr=40):
    return np.asarray(im)[:, :, 3] > thr


def gx(x): return int(round((x - X0) * RES))
def gy(y): return int(round((y - Y0) * RES))


def edges_at(mask, y_in, xlo, xhi):
    """Egy vízszintes sávban (y_in +-0.02") a maszk bal és jobb szélének x-e hüvelykben, [xlo,xhi] ablakon belül."""
    r0, r1 = gy(y_in - 0.02), gy(y_in + 0.02) + 1
    band = mask[r0:r1, gx(xlo):gx(xhi)].any(axis=0)
    xs = np.nonzero(band)[0]
    if not len(xs): return None
    return (xs[0] / RES + xlo, (xs[-1] + 1) / RES + xlo)


# ---------------------------------------------------------------- gyári tár / markolat referencia
from PIL import ImageDraw  # noqa: E402

MAG_YS = np.arange(2.9, 3.91, 0.1)          # a tárnyílás alatti első hüvelyk
GRIP_SLOPE_YS = np.arange(2.6, 4.41, 0.2)   # a markolat teste
GRIP_TOP_YS = np.arange(2.2, 2.71, 0.1)     # a markolat a tok alatt
MAG_CTR_Y, GRIP_CTR_Y = 3.4, 2.4


def poly_mask(poly):
    im = Image.new('L', (GW, GH), 0)
    ImageDraw.Draw(im).polygon([((x - X0) * RES, (y - Y0) * RES) for x, y in poly], fill=255)
    return np.asarray(im) > 0


def center_fit(mask, ys, xlo, xhi):
    """A maszk középvonala a megadott y-szinteken: (meredekség, tengelymetszet, [[y, közép, bal, jobb], ...])."""
    pts = []
    for y in ys:
        e = edges_at(mask, float(y), xlo, xhi)
        if e: pts.append((y, (e[0] + e[1]) / 2, e[0], e[1]))
    a = np.array(pts)
    if len(a) < 4: return None
    s, c = np.polyfit(a[:, 0], a[:, 1], 1)
    return s, c, a


def factory_params(recs, base_id):
    """A gyári tár és markolat tengelye/helye az alapfotó alfa-csatornájából (lásd fit_parts.py)."""
    geo = [r for r in recs if r['item'] == base_id and r['slot'] == 'geo'][0]
    base = find(recs, base_id, 'base', 'black')
    bm = alpha(to_grid(base, 0, 0, base['facing']))
    mag = center_fit(bm & poly_mask(geo['cuts']['mag']), MAG_YS, 2.0, 10.0)
    grip = center_fit(bm & poly_mask(geo['cuts']['grip']), GRIP_SLOPE_YS, -2, 3)
    top = center_fit(bm & poly_mask(geo['cuts']['grip']), GRIP_TOP_YS, -2, 3)
    return dict(geo=geo, bm=bm, mag=mag, grip=grip, grip_top=top, facing=base['facing'])
