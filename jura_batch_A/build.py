#!/usr/bin/env python3
"""
Great Forest of Jura -- BATCH A (trees) -- one re-runnable generator.

Builds every model of batch A procedurally (no manual steps), paints the shared
texture atlas, skins the trees to their wind bones, runs the self-checks,
exports FBX / GLB / .blend, renders the review images, re-imports the exports
to verify them and writes report.json + colliders.json.

Run (either works):
    blender -b -P build.py [-- options]      Blender 4.x / 5.x (needs Pillow in Blender's Python)
    python3 build.py [options]               with `pip install bpy pillow` (bpy 5.2 / Python 3.13)

Options:
    --no-render   skip the PNG renders
    --no-verify   skip re-importing the exported files
    --no-zip      do not (re)create ../jura_batch_A.zip

All outputs are written next to this file (the jura_batch_A/ folder).
The build is deterministic: every model uses its own seeded RNG.
"""
import os
import sys
import math
import json
import random
import hashlib
import zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import bpy
from mathutils import Vector, Matrix

# --------------------------------------------------------------------------------------
# paths / options
# --------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = HERE
REN = os.path.join(OUT, "renders")
BATCH = "jura_batch_A"
ZIP_NAME = "jura_batch_A2.zip"         # revision 2 delivery
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
OPT_RENDER = "--no-render" not in _argv
OPT_VERIFY = "--no-verify" not in _argv
OPT_ZIP = "--no-zip" not in _argv

# --------------------------------------------------------------------------------------
# palette (sRGB 0-255) -- exact values from the shared spec
# --------------------------------------------------------------------------------------
PAL = {
    "leaf_green": (88, 132, 57), "leaf_dark": (50, 88, 52),
    "leaf_yellow_green": (119, 152, 73), "leaf_jade": (64, 138, 108),
    "bark": (92, 65, 43), "bark_light": (109, 77, 47), "bark_dark": (79, 58, 40),
    "rock_grey": (126, 124, 118), "moss": (84, 122, 60), "cream": (226, 216, 190), "straw": (204, 172, 98),
    "charred": (44, 37, 32), "ember": (196, 84, 32),
    "maple_red": (196, 58, 40), "maple_orange": (226, 118, 44), "maple_amber": (232, 170, 64),
    "pine_needle": (46, 84, 60), "vermilion": (200, 52, 38), "limestone": (232, 228, 214),
    "dark_iron": (58, 58, 64), "orc_cloth": (112, 74, 52), "dwarf_stone": (148, 140, 128),
    "gold_trim": (204, 166, 82),
}
GLOW_RGB = (120, 230, 235)          # dryas spirit fruit, own material (not in the atlas)

# 4x4 atlas, 256 px tiles. (col, row) with row 0 = TOP row of the PNG.
TILES = {
    "leaf_green": (0, 0), "leaf_dark": (1, 0), "leaf_yellow_green": (2, 0), "leaf_jade": (3, 0),
    "maple_red": (0, 1), "maple_orange": (1, 1), "maple_amber": (2, 1), "pine_needle": (3, 1),
    "bark": (0, 2), "bark_light": (1, 2), "bark_dark": (2, 2), "charred": (3, 2),
    "far_crown_jura": (0, 3), "far_crown_maple": (1, 3), "far_crown_pine": (2, 3), "far_bark": (3, 3),
}
TILE_KIND = {
    "leaf_green": "leaf", "leaf_dark": "leaf", "leaf_yellow_green": "leaf", "leaf_jade": "leaf",
    "maple_red": "maple leaf", "maple_orange": "maple leaf", "maple_amber": "maple leaf",
    "pine_needle": "needle fan", "bark": "bark", "bark_light": "bark", "bark_dark": "bark",
    "charred": "charred bark + ember cracks", "far_crown_jura": "flat", "far_crown_maple": "flat",
    "far_crown_pine": "flat", "far_bark": "flat",
}
ATLAS_PX = 1024
TILE_PX = 256
INSET = 0.04
CHAR_PLAIN_U = (0.66, 1.0)     # part of the charred tile without ember cracks (used by burnt branches)
CHAR_CRACK_U = (0.0, 0.62)     # part of the charred tile with ember cracks (burnt trunk)

# existing forest trees (sizes given by the batch prompt: [x, height, depth])
EXISTING = {
    "far_jura_broad": ("broad crown", (54, 56, 55)),
    "far_jura_tall": ("tall", (36, 75, 33)),
    "far_jura_young": ("young", (35, 37, 34)),
    "far_jura_ancient": ("ancient", (72, 65, 73)),
}
BUDGET_TREE, BUDGET_BARK, BUDGET_FAR, BUDGET_DRYAS = 9000, 2600, 150, 12000

STATE = {"tile_mean": {}, "models": {}, "checks": {}, "colliders": {}}


# ======================================================================================
# 1. TEXTURE ATLAS (PIL + numpy, painted at 4x and downsampled for clean edges)
# ======================================================================================
SS = 4
NS = TILE_PX * SS


def _grid():
    y, x = np.mgrid[0:NS, 0:NS].astype(np.float32)
    return (x + 0.5) / NS, (y + 0.5) / NS          # 0..1, y grows downwards (PIL rows)


def _c(rgb):
    return np.array(rgb, np.float32)


def _lighten(c, a):
    return c + (255.0 - c) * a


def _finish(img):
    img = np.clip(img, 0, 255).astype(np.uint8)
    return Image.fromarray(img, "RGB").resize((TILE_PX, TILE_PX), Image.LANCZOS)


def _smooth_noise(rng, cells, amp):
    g = rng.uniform(-1, 1, (cells + 1, cells + 1)).astype(np.float32)
    im = Image.fromarray(((g + 1) * 127.5).astype(np.uint8), "L").resize((NS, NS), Image.BICUBIC)
    return (np.asarray(im, np.float32) / 127.5 - 1.0) * amp


def paint_leaf(base, rng):
    """Generic broadleaf: pointed leaf on a darker card, darker toward left/right,
    thin lighter midrib, faint side veins."""
    x, y = _grid()
    b = _c(base)
    edge = np.abs(x - 0.5) * 2.0
    bg = b * (0.74 - 0.10 * edge ** 2)[..., None]
    t = (0.93 - y) / 0.89                      # 0 at stem end (bottom), 1 at tip (top)
    tc = np.clip(t, 0, 1)
    hw = 0.43 * np.clip(np.sin(np.pi * tc ** 0.80), 0, 1) ** 0.85
    dx = x - 0.5
    adx = np.abs(dx)
    inside = (t > 0) & (t < 1) & (adx < hw)
    stem = (y > 0.90) & (y < 0.99) & (adx < 0.011)
    rel = np.where(hw > 1e-4, adx / np.maximum(hw, 1e-4), 1.0)
    shade = 1.0 - 0.30 * rel ** 1.7 + _smooth_noise(rng, 6, 0.03)
    leaf = b * shade[..., None]
    side = np.zeros_like(x, bool)
    for k in range(8):
        t0 = 0.08 + 0.105 * k
        side |= (np.abs(t - (t0 + 0.85 * adx)) < 0.0055) & (rel < 0.86) & (rel > 0.04)
    leaf = np.where(side[..., None], _lighten(leaf, 0.10), leaf)
    mid = (adx < 0.0065) & (t > -0.02) & (t < 0.94)
    leaf = np.where(mid[..., None], _lighten(b, 0.30), leaf)
    img = np.where((inside | stem)[..., None], leaf, bg)
    img = np.where(stem[..., None], b * 0.62, img)
    return _finish(img)


def paint_maple(base, rng):
    """Japanese maple (momiji): 5-point palmate leaf with serrated lobes."""
    x, y = _grid()
    b = _c(base)
    edge = np.abs(x - 0.5) * 2.0
    bg = b * (0.80 - 0.10 * edge ** 2)[..., None]
    cx, cy = 0.5, 0.60
    dx, dy = x - cx, cy - y
    r = np.sqrt(dx * dx + dy * dy)
    phi = np.arctan2(dx, dy)                   # 0 = up, + to the right
    lobes = [(0.0, 0.52, 0.56), (0.98, 0.47, 0.53), (-0.98, 0.47, 0.53),
             (1.95, 0.33, 0.46), (-1.95, 0.33, 0.46)]
    R = np.full_like(x, 0.11)
    near = np.full_like(x, 9.0)
    nearL = np.full_like(x, 0.5)
    for a, L, hw in lobes:
        d = np.angle(np.exp(1j * (phi - a)))
        prof = np.clip(1.0 - np.abs(d) / hw, 0, 1)
        R = np.maximum(R, 0.11 + (L - 0.11) * prof ** 0.62)
        better = np.abs(d) / hw < near
        near = np.where(better, np.abs(d) / hw, near)
        nearL = np.where(better, L, nearL)
    R = R * (1.0 + 0.04 * np.sin(phi * 46.0) * (r > 0.15))
    inside = r < R
    stem = (y > cy) & (y < 0.985) & (np.abs(dx) < 0.010 + 0.004 * (y - cy))
    shade = 1.0 - 0.24 * np.clip(near, 0, 1) ** 1.4 + 0.07 * np.clip(r / nearL, 0, 1) + _smooth_noise(rng, 6, 0.03)
    leaf = b * shade[..., None]
    vein = np.zeros_like(x, bool)
    for a, L, hw in lobes:
        d = np.angle(np.exp(1j * (phi - a)))
        along = r * np.cos(d)
        perp = np.abs(r * np.sin(d))
        vein |= (perp < 0.0055) & (along > 0) & (along < L * 0.9) & (np.abs(d) < 1.2)
    leaf = np.where(vein[..., None], _lighten(b, 0.26), leaf)
    img = np.where(inside[..., None], leaf, bg)
    img = np.where(stem[..., None], b * 0.66, img)
    return _finish(img)


def paint_needles(base, rng):
    """Pine needle fan radiating from the bottom centre; dark at the base (inside)."""
    b = _c(base)
    x, y = _grid()
    bg = b * (0.52 + 0.14 * (1.0 - y))[..., None]
    im = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8), "RGB")
    dr = ImageDraw.Draw(im)
    for i in range(170):
        ang = rng.uniform(-0.72, 0.72)
        ln = NS * rng.uniform(0.62, 0.97) * (1.0 - 0.30 * (abs(ang) / 0.72) ** 2)
        ox = NS * (0.5 + rng.uniform(-0.035, 0.035))
        oy = NS * 0.99
        seg = 7
        for s in range(seg):
            f0, f1 = s / seg, (s + 1) / seg
            c = b * (0.55 + 0.75 * f1)
            c = np.minimum(c, 255)
            p0 = (ox + math.sin(ang) * ln * f0, oy - math.cos(ang) * ln * f0)
            p1 = (ox + math.sin(ang) * ln * f1, oy - math.cos(ang) * ln * f1)
            dr.line([p0, p1], fill=tuple(int(v) for v in c), width=6)
    dr.line([(NS * 0.5, NS * 0.99), (NS * 0.5, NS * 0.45)], fill=tuple(int(v) for v in b * 0.45), width=9)
    return im.resize((TILE_PX, TILE_PX), Image.LANCZOS)


def paint_bark(base, rng, charred=False):
    """Vertical grooves (+ ember cracks on the left part of the charred tile)."""
    x, y = _grid()
    b = _c(base)
    v = np.ones_like(x)
    ng = 9
    for i in range(ng):
        xc = (i + 0.5 + rng.uniform(-0.25, 0.25)) / ng
        amp, f, ph = rng.uniform(0.004, 0.014), rng.uniform(1.0, 3.0), rng.uniform(0, 1)
        w = rng.uniform(0.010, 0.019)
        xcy = xc + amp * np.sin(2 * np.pi * (f * y + ph)) + 0.003 * np.sin(2 * np.pi * (7.3 * y + ph * 3))
        d = (x - xcy) / w
        v *= 1.0 - (0.42 if not charred else 0.50) * np.exp(-d * d)
        v += (0.10 if not charred else 0.22) * np.exp(-((d - 1.9) ** 2) / 0.9)
    col_noise = np.repeat(rng.uniform(-1, 1, (1, 64)).astype(np.float32), NS // 64, axis=1)
    col_noise = np.asarray(Image.fromarray(((col_noise + 1) * 127.5).astype(np.uint8)).resize((NS, NS), Image.BILINEAR), np.float32) / 127.5 - 1
    v += 0.035 * col_noise + _smooth_noise(rng, 8, 0.03)
    img = b * v[..., None]
    if charred:
        e = _c(PAL["ember"])
        mask = Image.new("L", (NS, NS), 0)
        dr = ImageDraw.Draw(mask)
        for k in range(10):
            px = (0.06 + 0.50 * (k + rng.uniform(0.15, 0.85)) / 10) * NS
            py = rng.uniform(0.0, 0.55) * NS
            pts = [(px, py)]
            for s in range(rng.randint(7, 13)):
                px = min(max(px + rng.uniform(-0.022, 0.022) * NS, 0.03 * NS), 0.58 * NS)
                py = py + rng.uniform(0.02, 0.05) * NS
                pts.append((px, py))
                if rng.random() < 0.18:     # side crack
                    qx, qy = px, py
                    sub = [(qx, qy)]
                    for _ in range(3):
                        qx = min(max(qx + rng.uniform(-0.035, 0.035) * NS, 0.03 * NS), 0.58 * NS)
                        qy += rng.uniform(0.01, 0.03) * NS
                        sub.append((qx, qy))
                    dr.line(sub, fill=200, width=10)
            dr.line(pts, fill=255, width=16)
        core = np.asarray(mask, np.float32) / 255.0
        glow = np.asarray(mask.filter(ImageFilter.GaussianBlur(16)), np.float32) / 255.0
        glow = np.clip(glow * 1.8, 0, 1)
        img = img * (1 - 0.55 * glow[..., None]) + e * (0.55 * glow[..., None])
        img = img * (1 - core[..., None]) + _lighten(e, 0.18) * core[..., None]
        # never let the glow reach the plain part used by the branches
        plain = x >= CHAR_PLAIN_U[0] - 0.02
        img = np.where(plain[..., None], b * v[..., None], img)
    return _finish(img)


def paint_flat(rgb, rng):
    n = _smooth_noise(rng, 12, 0.025) + rng.uniform(-0.008, 0.008, (NS, NS)).astype(np.float32)
    img = _c(rgb) * (1.0 + n)[..., None]
    return _finish(img)


def tile_mean(img):
    a = np.asarray(img, np.float32)
    i0, i1 = int(TILE_PX * INSET), int(TILE_PX * (1 - INSET))
    return tuple(float(v) for v in a[i0:i1, i0:i1].reshape(-1, 3).mean(0))


def paint_atlas_base():
    """Paint every tile except the far-card crown tiles (they depend on the trees)."""
    rng = np.random.RandomState(1729)
    prng = random.Random(1729)
    tiles = {}
    for k in ("leaf_green", "leaf_dark", "leaf_yellow_green", "leaf_jade"):
        tiles[k] = paint_leaf(PAL[k], rng)
    for k in ("maple_red", "maple_orange", "maple_amber"):
        tiles[k] = paint_maple(PAL[k], rng)
    tiles["pine_needle"] = paint_needles(PAL["pine_needle"], prng)
    for k in ("bark", "bark_light", "bark_dark"):
        tiles[k] = paint_bark(PAL[k], rng)
    tiles["charred"] = paint_bark(PAL["charred"], rng, charred=True)
    tiles["far_bark"] = paint_flat(PAL["bark"], rng)
    for k, im in tiles.items():
        STATE["tile_mean"][k] = tile_mean(im)
    return tiles, rng


def paint_far_tiles(tiles, rng, far_colours):
    for k, rgb in far_colours.items():
        tiles[k] = paint_flat(rgb, rng)
        STATE["tile_mean"][k] = tile_mean(tiles[k])


def tile_uv(tile, u, v):
    """Tile-local (u, v) in [0,1] (v=0 bottom) -> atlas UV inside the 4 % inset."""
    c, r = TILES[tile]
    u = min(max(u, 0.0), 1.0)
    v = min(max(v, 0.0), 1.0)
    return ((c + INSET + (1 - 2 * INSET) * u) / 4.0,
            (3 - r + INSET + (1 - 2 * INSET) * v) / 4.0)


# --------------------------------------------------------------------------------------
# Revision 2: leaf-shaped card geometry and the tiles painted to fill those outlines
# --------------------------------------------------------------------------------------
# Pointed leaf of the existing Jura trees (5 verts, 4 tris, midrib lifted 10 % of L).
KITE_UV = [(.5, .02), (.05, .46), (.5, .48), (.95, .46), (.5, .98)]
KITE_TRIS = [(0, 1, 2), (1, 4, 2), (4, 3, 2), (3, 0, 2)]
KITE_HW = 0.34                       # half width / length
# Momiji: 5-pointed fan, centre (lifted) + 10 outline verts (base notch, 5 tips, 4 notches).
MOMIJI_C = (0.0, 0.34)
MOMIJI_OUT = [(0.0, 0.08), (0.399, 0.210), (0.246, 0.383), (0.457, 0.697), (0.118, 0.583),
              (0.0, 1.0), (-0.118, 0.583), (-0.457, 0.697), (-0.246, 0.383), (-0.399, 0.210)]
MOMIJI_SCALE = 0.95                  # uv = (0.5 + 0.95 x, 0.03 + 0.95 y)
MOMIJI_TRIS = [(0, (i + 1) % 10 + 1, i + 1) for i in range(10)]
# Pine needle fan: tapered pentagon (s across W, t along L), 3 tris, flat.
NEEDLE_ST = [(-0.2, 0.0), (0.2, 0.0), (0.5, 0.52), (0.0, 1.0), (-0.5, 0.52)]
NEEDLE_TRIS = [(0, 2, 1), (0, 3, 2), (0, 4, 3)]


def momiji_uv(x, y):
    return (0.5 + MOMIJI_SCALE * x, 0.03 + MOMIJI_SCALE * y)


def needle_uv(s, t):
    return (0.5 + 0.94 * s, 0.02 + 0.96 * t)


KITE_POLY = [KITE_UV[0], KITE_UV[3], KITE_UV[4], KITE_UV[1]]
MOMIJI_POLY = [momiji_uv(*p) for p in MOMIJI_OUT]
NEEDLE_POLY = [needle_uv(*p) for p in NEEDLE_ST]


def poly_area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2.0


def _tile_uv_grid(n=NS):
    y, x = np.mgrid[0:n, 0:n].astype(np.float32)
    x, y = (x + 0.5) / n, (y + 0.5) / n
    return (x - INSET) / (1 - 2 * INSET), ((1.0 - y) - INSET) / (1 - 2 * INSET)


def _in_poly(u, v, poly):
    inside = np.zeros(u.shape, bool)
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        inside ^= ((y1 > v) != (y2 > v)) & (u < (x2 - x1) * (v - y1) / (y2 - y1 + 1e-12) + x1)
    return inside


def _seg_dist(u, v, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = np.clip(((u - a[0]) * dx + (v - a[1]) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
    return np.hypot(u - (a[0] + t * dx), v - (a[1] + t * dy))


def _edge_dist(u, v, poly):
    d = np.full(u.shape, 9.0, np.float32)
    for i in range(len(poly)):
        d = np.minimum(d, _seg_dist(u, v, poly[i], poly[(i + 1) % len(poly)]))
    return d


BLEED = 0.035                        # leaf colour painted this far (tile-local) outside each outline


def paint_leaf_v2(base, rng):
    """Pointed leaf filling the kite outline: darker toward left/right, light midrib, side veins."""
    u, v = _tile_uv_grid()
    b = _c(base)
    region = _in_poly(u, v, KITE_POLY) | (_edge_dist(u, v, KITE_POLY) < BLEED)
    hw = np.where(v < 0.46, 0.45 * (v - 0.02) / 0.44, 0.45 * (0.98 - v) / 0.52)
    adx = np.abs(u - 0.5)
    rel = np.clip(adx / np.maximum(hw, 1e-3), 0.0, 1.0)
    leaf = b * (1.0 - 0.28 * rel ** 1.6 + _smooth_noise(rng, 6, 0.03))[..., None]
    side = np.zeros(u.shape, bool)
    for k in range(9):
        side |= (np.abs(v - (0.05 + 0.10 * k + 0.80 * adx)) < 0.0055) & (rel < 0.86) & (rel > 0.05)
    leaf = np.where(side[..., None], _lighten(leaf, 0.10), leaf)
    mid = (adx < 0.0065) & (v > 0.02) & (v < 0.95)
    leaf = np.where(mid[..., None], _lighten(b, 0.30), leaf)
    img = np.where(region[..., None], leaf, (b * 0.82)[None, None, :])
    return _finish(img), KITE_POLY


def paint_maple_v2(base, rng):
    """Momiji filling the 5-pointed outline: veins from the palm to each tip, darker lobe edges."""
    u, v = _tile_uv_grid()
    b = _c(base)
    poly = MOMIJI_POLY
    d_edge = _edge_dist(u, v, poly)
    region = _in_poly(u, v, poly) | (d_edge < BLEED)
    cu, cv = momiji_uv(*MOMIJI_C)
    r = np.hypot(u - cu, v - cv)
    shade = 1.0 - 0.24 * np.exp(-d_edge / 0.035) + 0.07 * np.clip(r / 0.6, 0, 1) + _smooth_noise(rng, 6, 0.03)
    leaf = b * shade[..., None]
    vein = np.zeros(u.shape, bool)
    for i in (1, 3, 5, 7, 9):
        vein |= _seg_dist(u, v, (cu, cv), poly[i]) < 0.0058
    vein |= _seg_dist(u, v, (cu, cv), poly[0]) < 0.0058
    leaf = np.where(vein[..., None], _lighten(b, 0.26), leaf)
    img = np.where(region[..., None], leaf, (b * 0.85)[None, None, :])
    return _finish(img), poly


def paint_needles_v2(base, rng):
    """Needle spray filling the tapered fan outline (and the pointed spire outline):
    needles grow from a central twig, dark at the twig (inside), light at the tips."""
    b = _c(base)
    u, v = _tile_uv_grid()
    region = (_in_poly(u, v, NEEDLE_POLY) | _in_poly(u, v, KITE_POLY) |
              (_edge_dist(u, v, NEEDLE_POLY) < BLEED) | (_edge_dist(u, v, KITE_POLY) < BLEED))
    fill = b * (0.56 + 0.12 * np.clip(v, 0, 1))[..., None]
    im = Image.fromarray(np.clip(fill, 0, 255).astype(np.uint8), "RGB")
    dr = ImageDraw.Draw(im)

    def px(uu, vv):
        return ((INSET + (1 - 2 * INSET) * uu) * NS, (1.0 - (INSET + (1 - 2 * INSET) * vv)) * NS)
    for i in range(230):
        v0 = rng.uniform(0.0, 0.86)
        side = -1.0 if i % 2 else 1.0
        ang = math.radians(rng.uniform(18, 62))
        ln = rng.uniform(0.22, 0.48)
        seg = 6
        for s in range(seg):
            f0, f1 = s / seg, (s + 1) / seg
            c = np.minimum(b * (0.62 + 0.70 * f1), 255)
            p0 = px(0.5 + side * math.sin(ang) * ln * f0, v0 + math.cos(ang) * ln * f0)
            p1 = px(0.5 + side * math.sin(ang) * ln * f1, v0 + math.cos(ang) * ln * f1)
            dr.line([p0, p1], fill=tuple(int(x) for x in c), width=6)
    for i in range(30):                                   # needles crowding the pointed tip
        v0 = rng.uniform(0.70, 0.90)
        dr.line([px(0.5, v0), px(0.5 + rng.uniform(-0.05, 0.05), min(0.99, v0 + rng.uniform(0.06, 0.12)))],
                fill=tuple(int(x) for x in np.minimum(b * 1.25, 255)), width=6)
    dr.line([px(0.5, 0.0), px(0.5, 0.86)], fill=tuple(int(x) for x in b * 0.45), width=10)
    a = np.asarray(im, np.float32)
    img = np.where(region[..., None], a, (b * 0.5)[None, None, :])
    return _finish(img), NEEDLE_POLY


def paint_rev2_tiles():
    """Leaf, maple and needle tiles repainted for the leaf-shaped cards (own RNG streams, so the
    bark / charred / far tiles of batch A keep their exact pixels)."""
    tiles, masks = {}, {}
    for k in ("leaf_green", "leaf_dark", "leaf_yellow_green", "leaf_jade"):
        tiles[k], poly = paint_leaf_v2(PAL[k], np.random.RandomState(hash_seed("rev2:" + k) % 2 ** 31))
        masks[k] = [poly]
    for k in ("maple_red", "maple_orange", "maple_amber"):
        tiles[k], poly = paint_maple_v2(PAL[k], np.random.RandomState(hash_seed("rev2:" + k) % 2 ** 31))
        masks[k] = [poly]
    tiles["pine_needle"], poly = paint_needles_v2(PAL["pine_needle"], random.Random(hash_seed("rev2:pine")))
    masks["pine_needle"] = [poly, KITE_POLY]
    return tiles, masks


def compose_atlas(tiles):
    atlas = Image.new("RGB", (ATLAS_PX, ATLAS_PX), (0, 0, 0))
    for k, (c, r) in TILES.items():
        atlas.paste(tiles[k], (c * TILE_PX, r * TILE_PX))
    return atlas


def outline_mean(img, polys):
    """Mean colour of the tile pixels inside the leaf outline(s) = what the card shows."""
    u, v = _tile_uv_grid(TILE_PX)
    m = np.zeros(u.shape, bool)
    for p in polys:
        m |= _in_poly(u, v, p)
    a = np.asarray(img, np.float32)
    return tuple(float(x) for x in a[m].mean(0))

# ======================================================================================
# 2. GEOMETRY HELPERS
# ======================================================================================
Z = Vector((0.0, 0.0, 1.0))
W_REF = 6.0          # studs of tube circumference shown by one tile width (bark grooves)
H_REF = 9.0          # studs of tube length shown by one tile height


def rand_unit(rng):
    z = rng.uniform(-1.0, 1.0)
    a = rng.uniform(0.0, 2.0 * math.pi)
    s = math.sqrt(max(0.0, 1.0 - z * z))
    return Vector((s * math.cos(a), s * math.sin(a), z))


def any_perp(v):
    a = Vector((1.0, 0.0, 0.0)) if abs(v.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return (a - v * a.dot(v)).normalized()


def wind_bone(x, y):
    """Quadrant by azimuth around the trunk: Wind1 = 0-90 deg (+X+Y), Wind2 = 90-180,
    Wind3 = 180-270, Wind4 = 270-360 (azimuth measured from +X toward +Y)."""
    a = math.degrees(math.atan2(y, x)) % 360.0
    return "Wind%d" % (min(3, int(a // 90.0)) + 1)


def wlerp(a, b, t):
    keys = set(a) | set(b)
    out = {k: (1.0 - t) * a.get(k, 0.0) + t * b.get(k, 0.0) for k in keys}
    return {k: v for k, v in out.items() if v > 1e-5}


def bezier(p0, p1, p2, p3, n):
    pts = []
    for k in range(n + 1):
        t = k / n
        m = 1.0 - t
        pts.append(p0 * (m * m * m) + p1 * (3 * m * m * t) + p2 * (3 * m * t * t) + p3 * (t * t * t))
    return pts


class Geo:
    """Triangle soup with per-corner UVs, per-vertex bone weights and per-face smooth flags."""

    def __init__(self):
        self.co, self.w, self.tris, self.uv, self.smooth, self.tag = [], [], [], [], [], []
        self.cards = []                      # revision-2 leaf cards (see leaf_geom)

    def v(self, co, w):
        self.co.append((co[0], co[1], co[2]))
        self.w.append(w)
        return len(self.co) - 1

    def tri(self, a, b, c, ua, ub, uc, smooth=False, tag=None):
        self.tris.append((a, b, c))
        self.uv.append((ua, ub, uc))
        self.smooth.append(smooth)
        self.tag.append(tag)

    def quad(self, a, b, c, d, ua, ub, uc, ud, smooth=False, tag=None):
        self.tri(a, b, c, ua, ub, uc, smooth, tag)
        self.tri(a, c, d, ua, uc, ud, smooth, tag)

    def ntris(self):
        return len(self.tris)


def tube(geo, pts, radii, sides, tile, w0, w1, *, tip=0.0, cap=False, radius_fn=None,
         phase=0.0, u_sub=(0.0, 1.0), v0=0.0, smooth=True, wfn=None, tag=None, h_ref=H_REF,
         level_rings=0):
    """Tapered tube along a polyline. Faces wind outward. tip>0 closes the end with a cone
    of that length (forks / branch tips); cap=True closes the start with a flat fan."""
    n = len(pts)
    s = [0.0]
    for i in range(1, n):
        s.append(s[-1] + (pts[i] - pts[i - 1]).length)
    ltot = max(s[-1], 1e-6)
    T = []
    for i in range(n):
        d = (pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)])
        T.append(Z.copy() if i < level_rings else d.normalized())     # trunk rings at the ground stay level
    N = [any_perp(T[0])]
    for i in range(1, n):
        nn = N[-1] - T[i] * N[-1].dot(T[i])
        N.append(nn.normalized() if nn.length > 1e-6 else any_perp(T[i]))
    B = [T[i].cross(N[i]) for i in range(n)]
    rings, us, vs = [], [], []
    v, vdir = v0, 1.0
    for i in range(n):
        if i > 0:
            dv = (s[i] - s[i - 1]) / h_ref
            if vdir > 0 and v + dv > 1.0:
                vdir = -1.0
            elif vdir < 0 and v - dv < 0.0:
                vdir = 1.0
            v = min(max(v + vdir * dv, 0.0), 1.0)
        vs.append(v)
        w = wfn(s[i] / ltot) if wfn else wlerp(w0, w1, s[i] / ltot)
        ring = []
        for j in range(sides):
            th = 2.0 * math.pi * j / sides + phase
            r = radius_fn(i, th, radii[i]) if radius_fn else radii[i]
            p = pts[i] + (N[i] * math.cos(th) + B[i] * math.sin(th)) * r
            ring.append(geo.v(p, w))
        rings.append(ring)
        span = min(1.0, 2.0 * math.pi * radii[i] / W_REF)
        us.append(span)
    ua, ub = u_sub

    def U(i, j):
        u = 0.5 - us[i] / 2.0 + us[i] * j / sides
        return tile_uv(tile, ua + (ub - ua) * u, vs[i])

    for i in range(n - 1):
        for j in range(sides):
            a, b = rings[i][j], rings[i][(j + 1) % sides]
            c, d = rings[i + 1][(j + 1) % sides], rings[i + 1][j]
            geo.quad(a, b, c, d, U(i, j), U(i, j + 1), U(i + 1, j + 1), U(i + 1, j), smooth, tag)
    geo.last_v = vs[-1]
    if tip > 0.0:
        w = wfn(1.0) if wfn else w1
        t = geo.v(pts[-1] + T[-1] * tip, w)
        uvt = tile_uv(tile, (ua + ub) / 2.0, vs[-1])
        for j in range(sides):
            geo.tri(rings[-1][j], rings[-1][(j + 1) % sides], t, U(n - 1, j), U(n - 1, j + 1), uvt, smooth, tag)
    if cap:
        w = wfn(0.0) if wfn else w0
        c = geo.v(pts[0], w)
        for j in range(sides):
            th0 = 2 * math.pi * j / sides
            th1 = 2 * math.pi * (j + 1) / sides
            geo.tri(c, rings[0][(j + 1) % sides], rings[0][j],
                    tile_uv(tile, ua + (ub - ua) * 0.5, 0.5),
                    tile_uv(tile, ua + (ub - ua) * (0.5 + 0.45 * math.cos(th1)), 0.5 + 0.45 * math.sin(th1)),
                    tile_uv(tile, ua + (ub - ua) * (0.5 + 0.45 * math.cos(th0)), 0.5 + 0.45 * math.sin(th0)),
                    False, tag)
    return rings


def card(geo, base, tipdir, normal, L, W, tile, w, eps=0.012, tag=None):
    """Two-sided leaf card: two back-to-back quads (4 tris), the tile mapped stem->tip."""
    t = tipdir.normalized()
    n = normal - t * normal.dot(t)
    n = n.normalized() if n.length > 1e-6 else any_perp(t)
    sv = t.cross(n)
    h = sv * (W * 0.5)
    c0, c1 = base - h, base + h
    c2, c3 = base + t * L + h, base + t * L - h
    uv = [tile_uv(tile, 0, 0), tile_uv(tile, 1, 0), tile_uv(tile, 1, 1), tile_uv(tile, 0, 1)]
    f = [geo.v(p + n * eps, w) for p in (c0, c1, c2, c3)]
    geo.quad(f[0], f[1], f[2], f[3], uv[0], uv[1], uv[2], uv[3], False, tag)
    b = [geo.v(p - n * eps, w) for p in (c0, c1, c2, c3)]
    geo.quad(b[0], b[3], b[2], b[1], uv[0], uv[3], uv[2], uv[1], False, tag)


def picker(rng, mix):
    keys = list(mix.keys())
    acc, tot = [], 0.0
    for k in keys:
        tot += mix[k]
        acc.append(tot)

    def pick():
        r = rng.random() * tot
        for k, a in zip(keys, acc):
            if r <= a:
                return k
        return keys[-1]
    return pick


def leaf_cluster(geo, rng, c, rh, rv, n, pick, Lr, aspect, bone, *, flat=0.0, droop=0.0,
                 shell=0.45, up_bias=0.25, out_bias=0.45, card_droop=0.25, usage=None, tag=None):
    """Loose cluster of leaf cards around a branch end (cards biased to the outer shell)."""
    out = Vector((c.x, c.y, 0.0))
    out = out.normalized() if out.length > 1e-3 else Vector((0, 0, 0))
    w = {"Root": 0.08, bone: 0.92}
    for _ in range(n):
        d = rand_unit(rng) + out * out_bias + Z * up_bias
        d.normalize()
        u = shell + (1.0 - shell) * math.sqrt(rng.random())
        off = Vector((d.x * rh * u, d.y * rh * u, d.z * rv * u))
        p = c + off
        if droop > 0.0:
            hor = (off.x * out.x + off.y * out.y) / rh
            if hor > 0:
                p.z -= droop * rv * hor * hor
        L = rng.uniform(*Lr)
        Wd = L * aspect * rng.uniform(0.9, 1.08)
        nrm = (d * (1.0 - flat) + Z * flat).normalized() + rand_unit(rng) * 0.55
        nrm.normalize()
        tip = rand_unit(rng) * 0.7 + d * 0.6 + out * 0.2 - Z * card_droop
        tip = tip - nrm * tip.dot(nrm)
        if tip.length < 1e-3:
            tip = any_perp(nrm)
        tip.normalize()
        tile = pick()
        if usage is not None:
            usage[tile] = usage.get(tile, 0) + 1
        card(geo, p - tip * (L * 0.5), tip, nrm, L, Wd, tile, w, tag=tag)


# --------------------------------------------------------------------------------------
# Revision 2 leaf cards: single-sided, leaf-shaped geometry (the game renders leaves
# DoubleSided). Winding follows the existing Jura trees: faces wind toward -n, the midrib
# is lifted toward +n (n = the side facing out of the cluster).
# --------------------------------------------------------------------------------------
def leaf_geom(geo, base, tipdir, normal, pts, uvs, tris, tile, w, L, lift_i):
    t = tipdir.normalized()
    n = normal - t * normal.dot(t)
    n = n.normalized() if n.length > 1e-6 else any_perp(t)
    x = t.cross(n)                                   # local X; X x Y(=t) = Z(=n)
    t0, v0 = len(geo.tris), len(geo.co)
    idx = [geo.v(base + x * px + t * py + n * pz, w) for px, py, pz in pts]
    for a, b, c in tris:
        geo.tri(idx[a], idx[b], idx[c], tile_uv(tile, *uvs[a]), tile_uv(tile, *uvs[b]),
                tile_uv(tile, *uvs[c]), False, "leaf")
    geo.cards.append(dict(t0=t0, nt=len(tris), v0=v0, nv=len(pts), L=L, n=(n.x, n.y, n.z),
                          lift=lift_i, base=(base.x, base.y, base.z), tile=tile))


def kite_card(geo, base, tipdir, normal, L, hw, tile, w):
    """The existing trees' pointed leaf: 5 verts, 4 tris, midrib lifted 10 % of L."""
    pts = [(0, 0, 0), (-hw, L * .46, .03), (0, L * .48, L * .10), (hw, L * .46, .03), (0, L, 0)]
    leaf_geom(geo, base, tipdir, normal, pts, KITE_UV, KITE_TRIS, tile, w, L, 2)


def momiji_card(geo, base, tipdir, normal, L, tile, w):
    """Japanese maple leaf in geometry: 11 verts, 10 tris (fan from the lifted palm)."""
    pts = [(MOMIJI_C[0] * L, MOMIJI_C[1] * L, 0.10 * L)]
    for i, (x, y) in enumerate(MOMIJI_OUT):
        pts.append((x * L, y * L, 0.04 * L if i in (2, 4, 6, 8) else 0.0))   # notches half-way up
    uvs = [momiji_uv(*MOMIJI_C)] + [momiji_uv(x, y) for x, y in MOMIJI_OUT]
    leaf_geom(geo, base, tipdir, normal, pts, uvs, MOMIJI_TRIS, tile, w, L, 0)


def needle_card(geo, base, tipdir, normal, L, W, tile, w):
    """Pine needle fan: tapered to a point, single-sided (5 verts, 3 tris, flat)."""
    pts = [(s * W, t * L, 0.0) for s, t in NEEDLE_ST]
    leaf_geom(geo, base, tipdir, normal, pts, [needle_uv(s, t) for s, t in NEEDLE_ST], NEEDLE_TRIS,
              tile, w, L, None)


CARD_TRIS = {"kite": 4, "momiji": 10, "needle": 3}
CARD_AREA = {"kite": KITE_HW, "momiji": poly_area(MOMIJI_OUT)}       # x L^2


def ellipsoid_area(a, b, c):
    p = 1.6075
    return 4 * math.pi * (((a * b) ** p + (a * c) ** p + (b * c) ** p) / 3.0) ** (1 / p)


def shell_points(n, z_lo):
    """Golden-spiral directions covering the unit sphere from z = 1 down to z = z_lo."""
    ga = math.pi * (3.0 - math.sqrt(5.0))
    out = []
    for i in range(n):
        zz = 1.0 - (1.0 - z_lo) * (i + 0.5) / n
        rr = math.sqrt(max(0.0, 1.0 - zz * zz))
        out.append(Vector((rr * math.cos(i * ga), rr * math.sin(i * ga), zz)))
    return out


def shell_cluster(geo, rng, c, rh, rv, n, pick, Lr, kind, bone, *, z_lo=-1.0, depth=(0.86, 1.0),
                  tilt=0.30, lift=0.42, twist=38.0, buried=None, usage=None, hw=KITE_HW):
    """Leaf cards spread evenly over the outer shell of an ellipsoid cluster (golden spiral)
    and laid on it like shingles: base up-slope, tip down-slope and lifted off the surface.
    A cluster then reads as one full, soft mass instead of a scatter of cards."""
    w = {"Root": 0.08, bone: 0.92}
    out = Vector((c.x, c.y, 0.0))
    out = out.normalized() if out.length > 1e-3 else Vector((1.0, 0.0, 0.0))
    rot = rng.uniform(0, 2 * math.pi)
    made = 0
    for d0 in shell_points(n, z_lo):
        d = Vector((d0.x * math.cos(rot) - d0.y * math.sin(rot), d0.x * math.sin(rot) + d0.y * math.cos(rot), d0.z))
        d = (d + rand_unit(rng) * 0.10).normalized()
        u = rng.uniform(*depth)
        L = rng.uniform(*Lr)
        tw = math.radians(rng.uniform(-twist, twist))
        nz = rand_unit(rng)
        tile = pick()
        p = c + Vector((d.x * rh * u, d.y * rh * u, d.z * rv * u))
        if buried is not None and buried(p):
            continue                                   # hidden inside a neighbouring cluster
        N = Vector((d.x / rh, d.y / rh, d.z / rv)).normalized()
        nrm = (N + nz * tilt).normalized()
        dh = Vector((d.x, d.y, 0.0))
        dh = dh.normalized() if dh.length > 1e-3 else out
        down = -Z * 0.75 + dh * 0.65 + out * 0.15
        td = down - nrm * down.dot(nrm)
        td = td.normalized() if td.length > 1e-3 else any_perp(nrm)
        td = td * math.cos(tw) + nrm.cross(td) * math.sin(tw)
        td = (td + nrm * lift).normalized()
        base = p - td * (L * 0.45)
        if kind == "kite":
            kite_card(geo, base, td, nrm, L, hw * L, tile, w)
        else:
            momiji_card(geo, base, td, nrm, L, tile, w)
        if usage is not None:
            usage[tile] = usage.get(tile, 0) + 1
        made += 1
    return made


def budget_counts(clusters, n_cards, z_lo, buried_fns=None):
    """Share n_cards among clusters by visible shell area; return per-cluster spiral counts
    (inflated by the share of each shell buried inside neighbours, which is skipped)."""
    vis, surv = [], []
    for k, c in enumerate(clusters):
        f = buried_fns[k] if buried_fns else None
        pts = shell_points(160, z_lo)
        ok = sum(1 for d in pts if f is None or
                 not f(c["c"] + Vector((d.x * c["rh"] * 0.93, d.y * c["rh"] * 0.93, d.z * c["rv"] * 0.93))))
        s = max(ok / len(pts), 0.05)
        surv.append(s)
        vis.append(ellipsoid_area(c["rh"], c["rh"], c["rv"]) * (1.0 - z_lo) / 2.0 * s)
    tot = sum(vis)
    return [max(4, int(round(n_cards * a / tot / s))) for a, s in zip(vis, surv)]


# --------------------------------------------------------------------------------------
# recursive branch grower (pipe-model radii, fork toward groups of leaf clusters)
# --------------------------------------------------------------------------------------
def sides_for(r):
    return 10 if r > 3.0 else 9 if r > 1.2 else 8 if r > 0.45 else 7


def split_groups(targets, E, axis, k):
    if len(targets) <= k:
        return [[t] for t in targets]
    a1 = any_perp(axis)
    a2 = axis.cross(a1)
    ang = []
    for t in targets:
        d = t["c"] - E
        d = d - axis * d.dot(axis)
        ang.append(math.atan2(d.dot(a2), d.dot(a1)))
    order = sorted(range(len(targets)), key=lambda i: ang[i])
    m = len(order)
    sa = [ang[i] for i in order]
    gaps = [((sa[(i + 1) % m] - sa[i]) % (2 * math.pi)) for i in range(m)]
    st = (max(range(m), key=lambda i: gaps[i]) + 1) % m
    seq = order[st:] + order[:st]
    out, i0 = [], 0
    for g in range(k):
        i1 = round((g + 1) * m / k)
        out.append([targets[i] for i in seq[i0:i1]])
        i0 = i1
    return [g for g in out if g]


def grow(P, rng, out, start, sdir, targets, level, w_start, r_parent, s_start, k_force=None):
    """Append branch dicts to `out`. targets: dicts with c (Vector), bone, rv."""
    n = len(targets)
    r0 = min(r_parent * 0.88, P["r_twig"] * n ** (1.0 / 2.4))
    seg_len = P.get("seg_len", 3.0)
    if n == 1:
        tg = targets[0]
        end = tg["c"] - Z * (tg.get("rv", 1.0) * 0.15)
        L = (end - start).length
        edir = ((end - start).normalized() - Z * P.get("twig_droop", 0.0)).normalized()
        segs = max(P.get("min_twig_segs", 2), min(P.get("twig_segs", 3), int(L / seg_len) + 1))
        pts = bezier(start, start + sdir * L * 0.35, end - edir * L * 0.3, end, segs)
        radii = [r0 + (P["r_tip"] - r0) * (k / segs) for k in range(segs + 1)]
        out.append(dict(pts=pts, radii=radii, w0=w_start, w1={"Root": 0.12, tg["bone"]: 0.88},
                        level=level, tip=max(P["r_tip"] * 2.0, 0.15),
                        sides=P.get("twig_sides") or sides_for(r0)))
        return
    C = Vector((0, 0, 0))
    for t in targets:
        C += t["c"]
    C /= n
    D = C - start
    dist = D.length
    f = P["fork_frac"][min(level, len(P["fork_frac"]) - 1)]
    inert = P.get("inertia", 0.3)
    E = start + (sdir * dist * f) * inert + (D * f) * (1.0 - inert)
    L = (E - start).length
    axis = (E - start).normalized()
    segs = max(2, min(P.get("max_segs", 6), int(L / seg_len) + 1))
    p1 = start + sdir * L * 0.4
    p2 = E - axis * L * 0.3
    pts = bezier(start, p1, p2, E, segs)
    # bones of the subtree (majority) and how far along the path to the leaves we are
    cnt = {}
    for t in targets:
        cnt[t["bone"]] = cnt.get(t["bone"], 0) + 1
    bone = max(sorted(cnt), key=lambda b: cnt[b])
    md = sum((t["c"] - E).length for t in targets) / n
    sE = s_start + L
    phi = sE / (sE + md)
    root_E = 1.0 - 0.88 * phi
    rs = w_start.get("Root", 1.0)
    lam = 0.0 if rs <= 0.12 + 1e-6 else min(1.0, max(0.0, (rs - root_E) / (rs - 0.12)))
    w_end = wlerp(w_start, {"Root": 0.12, bone: 0.88}, lam)
    groups = split_groups(targets, E, axis, k_force or (3 if n >= 7 else 2))
    r_end = max(P["r_twig"] * max(len(g) for g in groups) ** (1.0 / 2.4) * 1.05, r0 * 0.72)
    r_end = min(r_end, r0)
    radii = [r0 + (r_end - r0) * (k / segs) for k in range(segs + 1)]
    out.append(dict(pts=pts, radii=radii, w0=w_start, w1=w_end, level=level,
                    tip=r_end * 0.9, sides=min(sides_for(r0), P.get("max_sides", 10))))
    for g in groups:
        Cg = Vector((0, 0, 0))
        for t in g:
            Cg += t["c"]
        Cg /= len(g)
        to = (Cg - E).normalized()
        cdir = (axis * P.get("child_inertia", 0.45) + to * (1.0 - P.get("child_inertia", 0.45))).normalized()
        cst = E - axis * (r_end * 0.6)
        grow(P, rng, out, cst, cdir, g, level + 1, w_end, r_end, sE)


def fork(P, rng, out, start, axis, targets, k, w, r_parent):
    """Split right at the trunk top into k limbs/leaders, one per azimuth sector of clusters."""
    ci = P.get("fork_inertia", 0.35)
    for g in split_groups(targets, start, axis, k):
        cg = Vector((0, 0, 0))
        for t in g:
            cg += t["c"]
        cg /= len(g)
        d = (axis * ci + (cg - start).normalized() * (1.0 - ci)).normalized()
        grow(P, rng, out, start, d, g, 1, w, r_parent, 0.0)


def build_branches(geo, branches, tile, rng, tag="branch"):
    for b in branches:
        tube(geo, b["pts"], b["radii"], b["sides"], tile, b["w0"], b["w1"], tip=b["tip"],
             phase=rng.uniform(0, 1), v0=rng.uniform(0, 1), tag=tag)


def trunk_radius_fn(base_r, top_r, z0, z1, flare=0.9, flare_h=1.6, lobes=5, lobe_amp=0.55,
                    lobe_pow=3.0, lobe_phase=0.0, lobe_dirs=None, zs=None):
    """Radius of a trunk ring: linear taper + flaring buttress lobes near the ground."""
    def fn(i, th, r):
        z = zs[i]
        fl = math.exp(-max(0.0, z - z0) / flare_h)
        if lobe_dirs:
            lob = sum(max(0.0, math.cos(th - a)) ** lobe_pow for a in lobe_dirs)
        else:
            lob = max(0.0, math.cos(lobes * (th - lobe_phase))) ** lobe_pow
        return r * (1.0 + flare * fl * (0.45 + lobe_amp * lob))
    return fn


# ======================================================================================
# 3. BLENDER OBJECTS
# ======================================================================================
def srgb_to_lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def get_atlas_material():
    mat = bpy.data.materials.get("Atlas")
    if mat:
        return mat
    img = bpy.data.images.load(os.path.join(OUT, "atlas.png"), check_existing=True)
    img.name = "atlas.png"            # saved as //atlas.png by relative_remap when the .blend is written
    return atlas_material("Atlas", img)


def atlas_material(name, img):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Linear"
    tex.location = (-400, 200)
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.9
    bsdf.inputs["Metallic"].default_value = 0.0
    nt.nodes.active = tex
    mat.diffuse_color = (0.5, 0.5, 0.5, 1.0)
    return mat


def get_flat_material(name, rgb, emission=0.0):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    lin = tuple(srgb_to_lin(c) for c in rgb) + (1.0,)
    bsdf.inputs["Base Color"].default_value = lin
    bsdf.inputs["Roughness"].default_value = 0.9
    if emission > 0:
        bsdf.inputs["Emission Color"].default_value = lin
        bsdf.inputs["Emission Strength"].default_value = emission
    mat.diffuse_color = lin
    return mat


def new_collection(name, parent=None):
    col = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(col)
    return col


def mesh_from_geo(name, geo, mat, col, mid=None, arm=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata(geo.co, [], geo.tris)
    uvl = me.uv_layers.new(name="UVMap")
    flat = []
    for tri in geo.uv:
        for u in tri:
            flat.extend(u)
    uvl.data.foreach_set("uv", flat)
    me.polygons.foreach_set("use_smooth", geo.smooth)
    me.materials.append(mat)
    me.update()
    ob = bpy.data.objects.new(name, me)
    col.objects.link(ob)
    if arm is not None:
        groups = {}
        for i, w in enumerate(geo.w):
            for b, x in w.items():
                groups.setdefault(b, []).append((i, x))
        for b in ("Root", "Wind1", "Wind2", "Wind3", "Wind4"):
            vg = ob.vertex_groups.new(name="%s_%s" % (mid, b))
            for i, x in groups.get(b, []):
                vg.add([i], x, "REPLACE")
        ob.parent = arm
        mod = ob.modifiers.new("Armature", "ARMATURE")
        mod.object = arm
        mod.use_vertex_groups = True
    return ob


def make_armature(mid, col, H, W):
    data = bpy.data.armatures.new(mid)
    arm = bpy.data.objects.new(mid, data)
    col.objects.link(arm)
    vl = bpy.context.view_layer
    vl.objects.active = arm
    arm.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    root = data.edit_bones.new("%s_Root" % mid)
    root.head = (0.0, 0.0, 0.0)
    root.tail = (0.0, 0.0, max(2.0, 0.25 * H))
    zb = 0.54 * H
    for k in range(4):
        a = math.radians(45 + 90 * k)
        r = 0.08 * W
        b = data.edit_bones.new("%s_Wind%d" % (mid, k + 1))
        b.head = (r * math.cos(a), r * math.sin(a), zb)
        b.tail = (r * math.cos(a), r * math.sin(a), zb + max(2.0, 0.22 * H))
        b.roll = 0.0
        b.parent = root
        b.use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    arm.select_set(False)
    for b in data.bones:
        b.use_deform = True
    return arm


# ======================================================================================
# 4. TREE GENERATORS
# ======================================================================================
def trunk_points(zs, lean=(0.0, 0.0), wob=0.0, rng=None, hf=None):
    hf = hf or zs[-1]
    out = []
    ph1, ph2 = (rng.uniform(0, 6.3), rng.uniform(0, 6.3)) if rng else (0.0, 0.0)
    for z in zs:
        f = max(0.0, z) / hf
        out.append(Vector((lean[0] * f ** 1.6 + wob * math.sin(z * 0.23 + ph1),
                           lean[1] * f ** 1.6 + wob * math.cos(z * 0.19 + ph2), z)))
    return out


MAPLE_TRI_TARGET = 8750               # rev 2: fill each maple to ~8.75k of its 9k budget
MAPLE_PAD = dict(L=(4.4, 5.4), z_lo=-0.75, tilt=0.50, twist=55.0, depth=(0.75, 1.05), lift=0.30, lower=0.85)
MAPLE_PAD_SCALE = {"maple_a": (0.95, 2.3), "maple_b": (0.97, 2.3), "maple_c": (0.97, 2.3)}  # rev 2 (rh, rv)


def gen_maple(mid, seed, H, W, mix, tiers, leaders, rev=2):
    """Momiji: slender dark trunk forking low, wide crown of 2-3 layered leaf-pad tiers.
    rev 1 = batch A (square two-sided cards), rev 2 = leaf-shaped momiji cards in soft pads."""
    rng = random.Random(seed)
    bark, leaves, usage = Geo(), Geo(), {}
    R = W / 2.0 - 5.5
    pads = []
    for ti, (zf, rf, npad, rh, rv) in enumerate(tiers):
        off = rng.uniform(0, 2 * math.pi)
        for j in range(npad):
            a = off + 2 * math.pi * j / npad + rng.uniform(-0.22, 0.22)
            rad = R * rf * rng.uniform(0.66, 1.0)
            z = H * zf + rng.uniform(-0.7, 0.7) - 1.6 * (rad / R) ** 2
            pads.append(dict(c=Vector((rad * math.cos(a), rad * math.sin(a), z)),
                             rh=rh * rng.uniform(0.88, 1.1), rv=rv))
        for j in range(max(1, npad // 3)):
            a = rng.uniform(0, 2 * math.pi)
            rad = R * rf * rng.uniform(0.18, 0.42)
            pads.append(dict(c=Vector((rad * math.cos(a), rad * math.sin(a), H * zf + rng.uniform(0.3, 1.4))),
                             rh=rh * 0.85, rv=rv))
    for p in pads:
        p["bone"] = wind_bone(p["c"].x, p["c"].y)
        if rev == 2:                           # rounder, cloud-like pads (grown downward so the tree keeps
            rv0 = p["rv"]                      # its height); the momiji cards reach a bit further out than
            p["rv"] *= MAPLE_PAD_SCALE[mid][1]   # batch A's, so the radius is trimmed to keep every size
            p["rh"] *= MAPLE_PAD_SCALE[mid][0]   # within 3 % of batch A
            p["c"] = p["c"] - Z * ((p["rv"] - rv0) * MAPLE_PAD["lower"])
    # trunk: slender, forks low
    hf = H * 0.21
    la = rng.uniform(0, 2 * math.pi)
    lean = (math.cos(la) * 0.9, math.sin(la) * 0.9)
    zs = [-0.8, 0.0, 0.5, 1.2, 2.2, 3.6, 5.4, hf - 1.5, hf]
    pts = trunk_points(zs, lean, 0.12, rng, hf)
    rb, rt = 0.031 * H, 0.024 * H
    radii = [rb + (rt - rb) * max(0.0, z) / hf for z in zs]
    fn = trunk_radius_fn(rb, rt, -0.8, hf, flare=1.05, flare_h=1.3, lobes=5, lobe_amp=0.8,
                         lobe_phase=rng.uniform(0, 1), zs=zs)
    tube(bark, pts, radii, 10, "bark_dark", {"Root": 1.0}, {"Root": 1.0}, tip=rt * 1.8, cap=True,
         radius_fn=fn, level_rings=3, v0=rng.uniform(0, 1), tag="trunk")
    P = dict(r_twig=rt / len(pads) ** (1 / 2.4), r_tip=0.05, fork_frac=[0.6, 0.62, 0.55, 0.5, 0.5],
             seg_len=3.2, max_segs=5, twig_segs=3 if rev == 1 else 2, twig_droop=0.35, inertia=0.25,
             child_inertia=0.35)
    br = []
    fork(P, rng, br, pts[-1] - Z * rt * 1.4, (pts[-1] - pts[-3]).normalized(), pads, leaders,
         {"Root": 1.0}, rt * 1.12)
    build_branches(bark, br, "bark_dark", rng)
    pick = picker(rng, mix)
    if rev == 1:
        for p in pads:
            n = int(round(1.3 * p["rh"] ** 2))
            t0 = leaves.ntris()
            leaf_cluster(leaves, rng, p["c"], p["rh"], p["rv"], n, pick, (3.7, 4.8), 0.95, p["bone"],
                         flat=0.62, droop=1.5, shell=0.25, up_bias=0.05, out_bias=0.25, card_droop=0.45,
                         usage=usage)
            p["tris"] = (t0, leaves.ntris())
    else:
        n_cards = (MAPLE_TRI_TARGET - bark.ntris()) // CARD_TRIS["momiji"]
        for p, n in zip(pads, budget_counts(pads, n_cards, MAPLE_PAD["z_lo"])):
            t0 = leaves.ntris()
            shell_cluster(leaves, rng, p["c"], p["rh"], p["rv"], n, pick, MAPLE_PAD["L"], "momiji", p["bone"],
                          z_lo=MAPLE_PAD["z_lo"], usage=usage, tilt=MAPLE_PAD["tilt"], twist=MAPLE_PAD["twist"],
                          depth=MAPLE_PAD["depth"], lift=MAPLE_PAD["lift"])
            p["tris"] = (t0, leaves.ntris())
    col = dict(c=(0.0, 0.0, hf * 0.5), s=(rb * 1.75, rb * 1.75, hf), yaw=0.0)
    return dict(bark=bark, leaves=leaves, usage=usage, colliders=[col], trunk_tile="bark_dark", pads=pads,
                info=dict(fork_height=round(hf, 2), tiers=len(tiers), pads=len(pads)))


def gen_pine(mid, seed, H, Rmax, ntiers, rev=2):
    """Tall conifer: straight trunk, narrow cone of drooping tiers made of needle-card fans."""
    rng = random.Random(seed)
    bark, leaves, usage = Geo(), Geo(), {"pine_needle": 0}
    rb = 0.021 * H
    zt = 0.80 * H
    zs = [-0.8, 0.0, 0.5, 1.2, 2.2, 3.6, 5.5] + [5.5 + (zt - 5.5) * k / 6 for k in range(1, 7)]
    pts = trunk_points(zs, (0.0, 0.0), 0.05, rng, zt)
    radii = [rb * (1.0 - 0.80 * max(0.0, z) / zt) for z in zs]
    fn = trunk_radius_fn(rb, rb * 0.2, -0.8, zt, flare=0.95, flare_h=1.5, lobes=5, lobe_amp=0.7,
                         lobe_phase=rng.uniform(0, 1), zs=zs)
    tube(bark, pts, radii, 9, "bark", {"Root": 1.0}, {"Root": 1.0}, tip=0.8, cap=True,
         radius_fn=fn, level_rings=3, v0=rng.uniform(0, 1), tag="trunk")
    # top leader: a branch (fades Root -> wind bone) that carries the uppermost tiers + spire
    sa = rng.uniform(0, 2 * math.pi)
    sbone = wind_bone(math.cos(sa), math.sin(sa))
    lt = Vector((0.2 * math.cos(sa), 0.2 * math.sin(sa), H - 2.2))
    lpts = [Vector((0, 0, zt - 3.0)), Vector((0, 0, zt + 2.0)), (Vector((0, 0, zt + 2.0)) + lt) / 2, lt]
    tube(bark, lpts, [radii[-1] * 0.9, radii[-1] * 0.7, radii[-1] * 0.45, 0.07], 7, "bark", {"Root": 1.0},
         {"Root": 0.12, sbone: 0.88}, tip=0.3, v0=rng.uniform(0, 1), tag="branch")

    def leader_w(z):
        t = min(1.0, max(0.0, (z - (zt - 3.0)) / (H - 2.2 - (zt - 3.0))))
        return wlerp({"Root": 1.0}, {"Root": 0.12, sbone: 0.88}, t)
    # first tier: high enough that its drooping tips + hanging cards stop at ~21 % of the height,
    # so the straight trunk stays visible over the bottom 20 %
    z0 = 0.21 * H + (0.76 - 0.14) * Rmax * 1.05 + 4.6
    ztop = 0.905 * H
    gold = 2.39996
    off = rng.uniform(0, 2 * math.pi)
    nb_total = 0
    for i in range(ntiers):
        f = i / (ntiers - 1)
        zi = z0 + (ztop - z0) * f ** 0.88 + rng.uniform(-0.4, 0.4)
        Ri = Rmax * (1.0 - 0.86 * f ** 0.95) * rng.uniform(0.93, 1.05) + 0.5
        m = int(round(7 - 2.6 * f))
        sz = 1.0 - 0.20 * f                       # smaller cards on the upper tiers
        for j in range(m):
            nb_total += 1
            a = off + i * gold + 2 * math.pi * j / m + rng.uniform(-0.22, 0.22)
            L = Ri * rng.uniform(0.80, 1.0)
            dh = Vector((math.cos(a), math.sin(a), 0.0))
            sd = Vector((-math.sin(a), math.cos(a), 0.0))
            sg = rng.choice((-1.0, 1.0))
            st = Vector((0.0, 0.0, zi))
            dr = rng.uniform(0.64, 0.76)

            def bp(t, st=st, dh=dh, sd=sd, L=L, sg=sg, dr=dr):
                return st + dh * (L * t) + sd * (sg * 0.06 * L * math.sin(math.pi * t)) + \
                    Z * (L * (0.14 * t - dr * t * t))
            segs = 3 if L > 9.0 else 2
            bpts = [bp(k / segs) for k in range(segs + 1)]
            bone = wind_bone(dh.x, dh.y)
            w0 = {"Root": 1.0} if zi < zt - 3.0 else leader_w(zi)
            r0 = min(rb * 0.42, 0.14 + 0.03 * L)
            tube(bark, bpts, [r0 + (0.05 - r0) * k / segs for k in range(segs + 1)], 7, "bark",
                 w0, {"Root": 0.12, bone: 0.88}, tip=0.12, v0=rng.uniform(0, 1), tag="branch")
            w = {"Root": 0.08, bone: 0.92}
            nst = max(2, int(round(L / 1.8)))
            for k in range(nst):
                t = 0.18 + 0.82 * (k + 0.5) / nst
                q = bp(t)
                tg = (bp(min(1.0, t + 0.02)) - bp(max(0.0, t - 0.02))).normalized()
                sh = tg.cross(Z).normalized()
                upn = sh.cross(tg).normalized()
                if upn.z < 0:
                    upn = -upn
                fans = (-62, -24, 24, 62) if L * (1 - t) > 2.2 or k == nst - 1 else (-45, 45)
                for phi in fans:
                    ph = math.radians(phi + rng.uniform(-10, 10))
                    d = tg * math.cos(ph) + sh * math.sin(ph)
                    dl = math.radians(28 + 40 * t + abs(phi) * 0.35 + rng.uniform(-7, 7))
                    d = (d * math.cos(dl) - upn * math.sin(dl)).normalized()
                    roll = math.radians(rng.uniform(-40, 40))
                    nrm = upn * math.cos(roll) + d.cross(upn) * math.sin(roll)
                    Lc = rng.uniform(3.6, 5.0) * sz
                    usage["pine_needle"] += 1
                    (card if rev == 1 else needle_card)(leaves, q - d * (Lc * 0.15), d, nrm, Lc, Lc * 0.72,
                                                        "pine_needle", w)
                if k >= 1:
                    d = (tg * 0.35 - Z * 0.94 + sh * rng.uniform(-0.3, 0.3)).normalized()
                    Lc = rng.uniform(3.5, 4.5) * sz
                    usage["pine_needle"] += 1
                    (card if rev == 1 else needle_card)(leaves, q - d * 0.4, d, sh, Lc, Lc * 0.72,
                                                        "pine_needle", w)
    # conical spire around the top leader
    w = {"Root": 0.08, sbone: 0.92}
    nsp = 16
    for k in range(nsp):
        f = k / (nsp - 1)
        a = off + k * gold
        zc = ztop - 1.2 + (H - 4.2 - ztop + 1.2) * f
        el = math.radians(18 + 60 * f)
        d = Vector((math.cos(a) * math.cos(el), math.sin(a) * math.cos(el), math.sin(el)))
        nrm = Vector((-math.sin(a), math.cos(a), 0.0))
        Lc = rng.uniform(3.5, 4.2)
        usage["pine_needle"] += 1
        if rev == 1:
            card(leaves, lt * (zc / lt.z) - d * 0.3, d, nrm, Lc, Lc * 0.64, "pine_needle", w)
        else:
            kite_card(leaves, lt * (zc / lt.z) - d * 0.3, d, nrm, Lc, Lc * 0.32, "pine_needle", w)
    for k in range(3):
        a = off + k * 2.1
        d = Vector((math.cos(a) * 0.1, math.sin(a) * 0.1, 1.0)).normalized()
        nrm = Vector((-math.sin(a), math.cos(a), 0.0))
        usage["pine_needle"] += 1
        if rev == 1:
            card(leaves, lt * ((H - 4.4) / lt.z), d, nrm, 4.4, 2.5, "pine_needle", w)
        else:
            kite_card(leaves, lt * ((H - 4.4) / lt.z), d, nrm, 4.4, 1.25, "pine_needle", w)
    lowest = min(p[2] for p in leaves.co)
    col = dict(c=(0.0, 0.0, lowest * 0.5), s=(rb * 1.75, rb * 1.75, lowest), yaw=0.0)
    return dict(bark=bark, leaves=leaves, usage=usage, colliders=[col], trunk_tile="bark",
                info=dict(tiers=ntiers, branches=nb_total, first_tier_height=round(z0, 2),
                          lowest_needles=round(lowest, 2), bare_trunk_fraction=round(lowest / H, 3)))


def gen_burnt(mid, seed, H):
    """Dead charred tree: broken jagged top, 4 bare twisted branches, everything on Root."""
    rng = random.Random(seed)
    bark = Geo()
    w = {"Root": 1.0}
    ztop = H - 5.6
    zs = [-0.8, 0.0, 0.5, 1.2, 2.2, 3.6, 5.5, 8.0, 11.0, 14.0, 17.0, 20.0, 22.4, ztop]
    pts = trunk_points(zs, (0.7, -0.5), 0.35, rng, ztop)
    rb, rt = 2.3, 1.35
    radii = [rb + (rt - rb) * max(0.0, z) / ztop for z in zs]
    fn = trunk_radius_fn(rb, rt, -0.8, ztop, flare=1.35, flare_h=1.6, lobes=4, lobe_amp=0.9,
                         lobe_phase=rng.uniform(0, 1), zs=zs)
    S = 9
    rings = tube(bark, pts, radii, S, "charred", w, w, cap=True, radius_fn=fn, level_rings=3, v0=0.0, tag="trunk",
                 u_sub=CHAR_CRACK_U, h_ref=ztop + 1.5)
    vt = bark.last_v
    # broken, splintered top: a ring of jagged teeth + a charred hollow
    top = rings[-1]
    hs = [rng.uniform(0.3, 1.5) for _ in range(S)]
    k1 = rng.randrange(S)
    hs[k1] = H - ztop
    hs[(k1 + 4) % S] = rng.uniform(2.6, 3.4)
    hs[(k1 + 1) % S] = rng.uniform(1.8, 2.4)
    ctr = pts[-1]
    jag = []
    for j in range(S):
        p = Vector(bark.co[top[j]])
        p = ctr + (p - ctr) * (0.86 - 0.05 * hs[j] / 5.6)
        p.z = pts[-1].z + hs[j] + (Vector(bark.co[top[j]]).z - pts[-1].z)
        jag.append(bark.v(p, w))
    for j in range(S):
        u0, u1 = j / S * CHAR_CRACK_U[1], (j + 1) / S * CHAR_CRACK_U[1]
        a, b, c, d = top[j], top[(j + 1) % S], jag[(j + 1) % S], jag[j]
        bark.quad(a, b, c, d, tile_uv("charred", u0, vt), tile_uv("charred", u1, vt),
                  tile_uv("charred", u1, vt - 0.18 * hs[(j + 1) % S] / 5.6),
                  tile_uv("charred", u0, vt - 0.18 * hs[j] / 5.6), False, "trunk")
    hollow = bark.v(ctr + Z * 0.35, w)
    for j in range(S):
        a, b = jag[j], jag[(j + 1) % S]
        bark.tri(a, b, hollow, tile_uv("charred", 0.70, 0.3), tile_uv("charred", 0.95, 0.3),
                 tile_uv("charred", 0.82, 0.05), False, "trunk")
    # 4 bare, twisted branches (+2 small offshoots)
    base_a = rng.uniform(0, 2 * math.pi)
    tips = []
    for k, (zb, L, elev, blunt) in enumerate([(8.0, 12.0, 22, False), (11.8, 10.0, 36, True),
                                              (15.6, 10.5, 28, False), (19.6, 8.0, 44, False)]):
        a = base_a + k * 1.75 + rng.uniform(-0.25, 0.25)
        i = min(range(len(zs)), key=lambda q: abs(zs[q] - zb))
        st = pts[i]
        d = Vector((math.cos(a) * math.cos(math.radians(elev)), math.sin(a) * math.cos(math.radians(elev)),
                    math.sin(math.radians(elev))))
        side = Vector((-math.sin(a), math.cos(a), 0.0))
        bpts, lat, upw = [], 0.0, 0.0
        n = 6
        for s in range(n + 1):
            t = s / n
            if s > 0:
                lat += rng.uniform(-1.0, 1.0)
                upw += rng.uniform(-0.45, 0.7)
            bpts.append(st + d * (L * t) + side * (lat * t) + Z * (upw * t + 0.9 * math.sin(math.pi * t)))
        r0 = radii[i] * 0.45
        rr = [r0 + ((0.26 if blunt else 0.05) - r0) * (s / n) ** 0.9 for s in range(n + 1)]
        tube(bark, bpts, rr, 7, "charred", w, w, tip=(0.06 if blunt else 0.25),
             u_sub=CHAR_PLAIN_U, v0=rng.uniform(0, 1), tag="branch")
        tips.append((bpts, rr, a))
    for k in (0, 2):
        bpts, rr, a = tips[k]
        st = bpts[3]
        a2 = a + rng.choice((-1, 1)) * rng.uniform(0.6, 0.9)
        d = Vector((math.cos(a2) * 0.7, math.sin(a2) * 0.7, 0.75)).normalized()
        L2 = rng.uniform(3.2, 4.2)
        tp = [st, st + d * (L2 * 0.5) + Vector((rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3), 0.2)), st + d * L2]
        tube(bark, tp, [rr[3] * 0.6, rr[3] * 0.35, 0.05], 7, "charred", w, w, tip=0.15,
             u_sub=CHAR_PLAIN_U, v0=rng.uniform(0, 1), tag="branch")
    col = dict(c=(0.0, 0.0, ztop * 0.5), s=(rb * 1.7, rb * 1.7, ztop), yaw=0.0)
    return dict(bark=bark, leaves=None, usage={}, colliders=[col], trunk_tile="charred",
                info=dict(broken_top_base=round(ztop, 2), branches=4, offshoots=2))


def envelope_points(rng, centre, Rh, Rv, zmin, n, min_d, shell=(0.82, 0.96), zbias=0.0, seeds=()):
    pts, tries = list(seeds), 0
    while len(pts) < n and tries < 40000:
        tries += 1
        d = rand_unit(rng)
        d.z = d.z + zbias
        if d.length < 1e-3:
            continue
        d.normalize()
        u = rng.uniform(*shell)
        p = centre + Vector((d.x * Rh * u, d.y * Rh * u, d.z * Rv * u))
        if p.z < zmin:
            continue
        if all((p - q).length >= min_d for q in pts):
            pts.append(p)
    return pts


DRYAS_TRI_BUDGET = 12000


DRYAS_CROWN = {
    1: dict(Rh=50.0, apex=26.5, Rv=29.0, zmin=49.0, n=33, min_d=16.0, shell=(0.80, 0.97), under_z=(51.0, 55.0),
            rh=(9.2, 11.0), rv=(6.4, 7.6)),
    2: dict(Rh=47.5, apex=24.0, Rv=27.0, zmin=50.0, n=24, min_d=15.0, shell=(0.74, 0.95), under_z=(53.0, 57.0),
            rh=(12.5, 14.0), rv=(8.5, 9.8), L=(5.4, 6.6), bury=0.82, fill=1.0, skin=0.7, hw=0.40, lift=0.2,
            upper=(7, 0.62, 0.56), core=4, tilt=0.45, twist=50.0, depth=(0.80, 1.03)),
}


def gen_dryas(mid, seed, rev=2):
    """Sacred Dryad tree: massive trunk splitting into 3 limbs, huge arching roots,
    wide crown of large clusters, ring of glowing spirit fruit.
    rev 1 = batch A (airy, square cards), rev 2 = full crown of shingled pointed leaves."""
    rng = random.Random(seed)
    bark, leaves, glow, usage = Geo(), Geo(), Geo(), {}
    root_w = {"Root": 1.0}
    # ---------------- roots ----------------
    base = rng.uniform(0, 2 * math.pi)
    azs = [base + k * 2 * math.pi / 7 + rng.uniform(-0.1, 0.1) for k in range(7)]
    thick_prof = [(2.5, 5.2, 3.7), (8.0, 4.4, 3.5), (13.5, 3.4, 3.1), (19.0, 3.8, 2.7), (25.0, 3.0, 2.4),
                  (31.0, 2.0, 2.0), (36.5, 1.1, 1.7), (41.0, 0.5, 1.4), (45.0, 0.05, 1.05)]
    arch_prof = [(3.0, 15.0, 2.9), (9.0, 16.0, 2.75), (15.0, 15.8, 2.6), (20.0, 14.5, 2.45), (24.5, 12.3, 2.3),
                 (28.5, 9.5, 2.15), (32.0, 6.3, 2.0), (35.5, 3.2, 1.8), (39.0, 0.75, 1.6), (43.0, 0.1, 1.1)]
    roots, thick_dirs, arch_dirs = [], [], []
    for k, a in enumerate(azs):
        arch = (k % 2 == 0)
        prof = arch_prof if arch else thick_prof
        sc = rng.uniform(0.94, 1.04)
        A = (1.0 if arch else 1.6) * rng.choice((-1, 1))
        ph = rng.uniform(0, 1)
        ca, sa = math.cos(a), math.sin(a)
        pts, rr = [], []
        for (r, zc, rad) in prof:
            r2 = r * sc
            lat = A * math.sin(math.pi * (r2 / 45.0) * 1.3 + ph) * min(1.0, r2 / 10.0)
            pts.append(Vector((r2 * ca - lat * sa, r2 * sa + lat * ca, zc)))
            rr.append(rad)
        tube(bark, pts, rr, 8, "bark_light", root_w, root_w, tip=1.2, phase=rng.uniform(0, 1),
             v0=rng.uniform(0, 1), tag="root")
        roots.append(dict(arch=arch, pts=pts, radii=rr, az=a))
        (arch_dirs if arch else thick_dirs).append(a)
    # ---------------- trunk ----------------
    hs = 36.0
    zs = [-1.0, 0.0, 0.8, 1.8, 3.2, 5.0, 7.5, 10.5, 14.0, 18.0, 23.0, 28.0, 32.0, hs]
    pts = trunk_points(zs, (1.6, -0.9), 0.0, rng, hs)
    radii = [8.0 - 1.8 * max(0.0, z) / hs for z in zs]

    def fn(i, th, r, zs=zs):
        z = zs[i]
        fl = math.exp(-(z + 1.0) / 3.2)
        lob = sum(max(0.0, math.cos(th - a)) ** 6 for a in thick_dirs)
        return r * (1.0 + 0.55 * fl * (0.35 + 0.95 * lob))
    tube(bark, pts, radii, 10, "bark", root_w, root_w, tip=6.5, cap=True, radius_fn=fn, level_rings=3,
         phase=rng.uniform(0, 1), v0=0.1, tag="trunk")
    # ---------------- crown clusters ----------------
    DP = DRYAS_CROWN[rev]
    cc = Vector((pts[-1].x, pts[-1].y, 74.0))
    Rh = DP["Rh"]
    ring = [cc + Vector((0.0, 0.0, DP["apex"]))]           # apex + an even ring at the crown's widest level
    for k in range(11):
        a = base + 0.3 + k * 2 * math.pi / 11 + rng.uniform(-0.1, 0.1)
        ring.append(cc + Vector((Rh * 0.96 * math.cos(a), Rh * 0.96 * math.sin(a), rng.uniform(-3.0, 2.0))))
    if DP.get("upper"):                                    # rev 2: ring around the apex closes the dome
        nu, fr, fz = DP["upper"]
        for k in range(nu):
            a = base + 0.9 + k * 2 * math.pi / nu
            ring.append(cc + Vector((Rh * fr * math.cos(a), Rh * fr * math.sin(a), DP["Rv"] * fz)))
    cl = envelope_points(rng, cc, Rh, DP["Rv"], DP["zmin"], DP["n"], DP["min_d"], shell=DP["shell"],
                         zbias=0.15, seeds=ring)
    under = []
    for k in range(6):
        a = base + k * 2 * math.pi / 6 + rng.uniform(-0.3, 0.3)
        rad = rng.uniform(18.0, 32.0)
        under.append(cc + Vector((rad * math.cos(a), rad * math.sin(a), -cc.z + rng.uniform(*DP["under_z"]))))
    core = []
    for k in range(DP.get("core", 0)):                     # rev 2: inner clusters fill the crown's core
        a = base + 0.5 + k * 2 * math.pi / DP["core"]
        core.append(cc + Vector((Rh * 0.42 * math.cos(a), Rh * 0.42 * math.sin(a), -0.12 * DP["Rv"])))
    clusters = []
    for p in cl + under + core:
        clusters.append(dict(c=p, rh=rng.uniform(*DP["rh"]), rv=rng.uniform(*DP["rv"]),
                             bone=wind_bone(p.x, p.y), core=p in core))
    rt = radii[-1]
    P = dict(r_twig=rt * 0.98 / len(clusters) ** (1 / 2.4), r_tip=0.35, fork_frac=[0.5, 0.55, 0.52, 0.5, 0.55],
             seg_len=6.0, max_segs=3, twig_segs=2 if rev == 1 else 1, min_twig_segs=2 if rev == 1 else 1,
             twig_droop=0.1, inertia=0.3, child_inertia=0.4, twig_sides=7, max_sides=9)
    br = []
    fork(P, rng, br, pts[-1] - Z * 7.0, Z, clusters, 3, {"Root": 1.0}, rt * 1.02)
    build_branches(bark, br, "bark", rng)
    pick = picker(rng, {"leaf_jade": 0.55, "leaf_green": 0.45})
    if rev == 1:
        for c in clusters:
            n = int(round(0.43 * c["rh"] * c["rh"]))
            leaf_cluster(leaves, rng, c["c"], c["rh"], c["rv"], n, pick, (4.8, 5.9), 0.64, c["bone"],
                         flat=0.0, shell=0.6, up_bias=0.38, out_bias=0.75, card_droop=0.3, usage=usage)
    else:
        def buried_fn(k):
            me = clusters[k]
            nb = [c for j, c in enumerate(clusters) if j != k and
                  (c["c"] - me["c"]).length < c["rh"] + me["rh"]]

            def f(p):
                for c in nb:
                    q = p - c["c"]
                    if (q.x / c["rh"]) ** 2 + (q.y / c["rh"]) ** 2 + (q.z / c["rv"]) ** 2 < DP["bury"] ** 2:
                        return True
                return False
            return f
        # crown envelope through the outermost cluster surfaces: cards deep inside it are hidden
        # behind the outer skin, so they are skipped and the budget goes to the skin instead
        rhm, rvm = DP["rh"][1], DP["rv"][1]
        e_h = Rh * 0.96 + rhm
        e_top = cc.z + DP["apex"] + rvm - cc.z
        e_bot = cc.z - (DP["under_z"][0] - rvm)

        def skin_rho(p):
            q = p - cc
            return math.sqrt((q.x / e_h) ** 2 + (q.y / e_h) ** 2 + (q.z / (e_top if q.z > 0 else e_bot)) ** 2)
        inner = [buried_fn(k) for k in range(len(clusters))]
        bfs = [f if clusters[k]["core"] else (lambda f: (lambda p: skin_rho(p) < DP["skin"] or f(p)))(f)
               for k, f in enumerate(inner)]
        glow_tris = 10 * 48
        n_cards = int((DRYAS_TRI_BUDGET - 60 - bark.ntris() - glow_tris) // CARD_TRIS["kite"] * DP["fill"])
        counts = budget_counts(clusters, n_cards, -1.0, bfs)
        for k, c in enumerate(clusters):
            shell_cluster(leaves, rng, c["c"], c["rh"], c["rv"], counts[k], pick, DP["L"], "kite", c["bone"],
                          z_lo=-1.0, buried=bfs[k], usage=usage, hw=DP["hw"], lift=DP["lift"],
                          tilt=DP.get("tilt", 0.30), twist=DP.get("twist", 38.0), depth=DP.get("depth", (0.86, 1.0)))
    # ---------------- spirit fruit ----------------
    lows = sorted(clusters, key=lambda c: c["c"].z)
    fruit, used_bins = [], set()
    for c in sorted(clusters, key=lambda c: (c["c"].z - 0.15 * Vector((c["c"].x, c["c"].y)).length)):
        az = math.atan2(c["c"].y, c["c"].x) % (2 * math.pi)
        b = int(az / (2 * math.pi / 10))
        if b in used_bins or Vector((c["c"].x, c["c"].y)).length < 14:
            continue
        used_bins.add(b)
        fruit.append(c)
        if len(fruit) == 10:
            break
    for c in fruit:
        out = Vector((c["c"].x, c["c"].y, 0)).normalized()
        if rev == 1:
            p = c["c"] + out * (c["rh"] * 0.35) - Z * (c["rv"] * 0.78)
        else:                                  # hang just below the (now dense) cluster underside
            p = c["c"] + out * (c["rh"] * 0.30) - Z * (c["rv"] * 0.95 + 0.6)
        uv_sphere(glow, p, 1.5, {"Root": 0.08, c["bone"]: 0.92})
    # ---------------- colliders + arch clearance ----------------
    cols = [dict(c=(pts[0].x * 0.5 + pts[-1].x * 0.5, pts[0].y * 0.5 + pts[-1].y * 0.5, hs * 0.5),
                 s=(14.4, 14.4, hs), yaw=0.0)]
    for r in sorted([r for r in roots if not r["arch"]], key=lambda r: -max(r["radii"]))[:3]:
        ps = [p for p in r["pts"] if Vector((p.x, p.y)).length >= 9.0]
        a = math.atan2(ps[-1].y - ps[0].y, ps[-1].x - ps[0].x)
        L = Vector((ps[-1].x - ps[0].x, ps[-1].y - ps[0].y)).length + 2.0
        top = max(p.z + rad for p, rad in zip(r["pts"], r["radii"]) if Vector((p.x, p.y)).length >= 9.0)
        wdt = 2.0 * sum(r["radii"][i] for i, p in enumerate(r["pts"]) if Vector((p.x, p.y)).length >= 9.0) / len(ps)
        mx, my = (ps[0].x + ps[-1].x) / 2, (ps[0].y + ps[-1].y) / 2
        cols.append(dict(c=(mx, my, top / 2.0), s=(L, wdt, top), yaw=math.degrees(a)))
    clearance = []
    for r in roots:
        if not r["arch"]:
            continue
        span, best, cur, maxc = [], 0.0, None, 0.0
        for i in range(len(r["pts"]) - 1):
            for q in range(8):
                t = q / 8
                p = r["pts"][i].lerp(r["pts"][i + 1], t)
                rad = r["radii"][i] + (r["radii"][i + 1] - r["radii"][i]) * t
                d = Vector((p.x, p.y)).length
                under_z = p.z - rad
                maxc = max(maxc, under_z)
                if under_z >= 7.0 and d > 9.0:
                    cur = (cur[0], d) if cur else (d, d)
                elif cur:
                    span.append(cur)
                    cur = None
        if cur:
            span.append(cur)
        best = max((b - a for a, b in span), default=0.0)
        mid_r = (span[0][0] + span[0][1]) / 2 if span else 20.0
        # the root snakes sideways: find its actual xy position at that radius
        best_p, best_r = min(((r["pts"][i].lerp(r["pts"][i + 1], q / 8),
                               r["radii"][i] + (r["radii"][i + 1] - r["radii"][i]) * q / 8)
                              for i in range(len(r["pts"]) - 1) for q in range(8)),
                             key=lambda pr: abs(Vector((pr[0].x, pr[0].y)).length - mid_r))
        lat = Vector((best_p.x, best_p.y, 0.0)) - Vector((math.cos(r["az"]), math.sin(r["az"]), 0.0)) * mid_r
        clearance.append(dict(azimuth_deg=round(math.degrees(r["az"]) % 360, 1),
                              human_radius=round(mid_r, 2), lateral_offset=[round(lat.x, 3), round(lat.y, 3), 0.0],
                              underside_at_human=round(best_p.z - best_r, 2),
                              max_underside=round(maxc, 2),
                              walkable_span_ge7=[round(span[0][0], 1), round(span[0][1], 1)] if span else None,
                              span_len=round(best, 1)))
    return dict(bark=bark, leaves=leaves, glow=glow, usage=usage, colliders=cols, trunk_tile="bark",
                info=dict(split_height=hs, limbs=3, roots=7, arch_roots=4, clusters=len(clusters),
                          fruit=len(fruit), arch_clearance=clearance))


def uv_sphere(geo, c, r, w, seg=8):
    lats = [-math.pi / 4, 0.0, math.pi / 4]
    rings = []
    for la in lats:
        ring = []
        for j in range(seg):
            th = 2 * math.pi * j / seg
            ring.append(geo.v(c + Vector((r * math.cos(la) * math.cos(th), r * math.cos(la) * math.sin(th),
                                          r * math.sin(la))), w))
        rings.append(ring)
    bot = geo.v(c - Z * r, w)
    top = geo.v(c + Z * r, w)
    uv = lambda j, k: ((j / seg), k / 4.0)
    for j in range(seg):
        j1 = (j + 1) % seg
        geo.tri(bot, rings[0][j1], rings[0][j], uv(j, 0), uv(j + 1, 1), uv(j, 1), True)
        for k in range(2):
            geo.quad(rings[k][j], rings[k][j1], rings[k + 1][j1], rings[k + 1][j],
                     uv(j, k + 1), uv(j + 1, k + 1), uv(j + 1, k + 2), uv(j, k + 2), True)
        geo.tri(rings[2][j], rings[2][j1], top, uv(j, 3), uv(j + 1, 3), uv(j, 4), True)


def gen_broadleaf_ref(mid, seed, W, H, D, crown_c, crown_r, split, nclus, rb, limbs):
    """Stand-in for an EXISTING forest tree (not delivered). Only used for the far-card
    comparison renders, because the original meshes were not part of this batch."""
    rng = random.Random(seed)
    bark, leaves, usage = Geo(), Geo(), {}
    hs = H * split
    zs = [-0.8, 0.0, 0.5, 1.2, 2.2, 3.6, 5.5, hs * 0.7, hs]
    pts = trunk_points(zs, (0.4, 0.3), 0.1, rng, hs)
    rt = rb * 0.75
    radii = [rb + (rt - rb) * max(0.0, z) / hs for z in zs]
    fn = trunk_radius_fn(rb, rt, -0.8, hs, flare=1.0, flare_h=1.4, lobes=5, lobe_amp=0.8, zs=zs)
    tube(bark, pts, radii, 10, "bark", {"Root": 1}, {"Root": 1}, tip=rt, cap=True, radius_fn=fn, level_rings=3)
    rh = crown_r[0] * 0.30
    cc = Vector((0, 0, H * crown_c))
    cl = envelope_points(rng, cc, crown_r[0] - rh * 0.8, crown_r[1] - rh * 0.6, hs + 2, nclus, rh * 1.25,
                         shell=(0.7, 0.95), zbias=0.15)
    clusters = [dict(c=p, rh=rh, rv=rh * 0.8, bone=wind_bone(p.x, p.y)) for p in cl]
    P = dict(r_twig=rt / len(clusters) ** (1 / 2.4), r_tip=0.06, fork_frac=[0.5, 0.55, 0.5, 0.5],
             seg_len=3.5, max_segs=5, twig_segs=3, inertia=0.3, child_inertia=0.4)
    br = []
    fork(P, rng, br, pts[-1] - Z * rt, Z, clusters, limbs, {"Root": 1}, rt * 1.05)
    build_branches(bark, br, "bark", rng)
    pick = picker(rng, {"leaf_green": 1, "leaf_dark": 1, "leaf_yellow_green": 1, "leaf_jade": 1})
    for c in clusters:                     # the existing trees' pointed leaf card (revision 2 knowledge)
        n = int(round(1.4 * ellipsoid_area(c["rh"], c["rh"], c["rv"]) / (0.40 * 4.5 ** 2)))
        shell_cluster(leaves, rng, c["c"], c["rh"], c["rv"], n, pick, (3.5, 5.5), "kite", c["bone"],
                      usage=usage, hw=0.40, lift=0.2)
    return dict(bark=bark, leaves=leaves, usage=usage, colliders=[], trunk_tile="bark", info={})


# ======================================================================================
# 5. FAR-CANOPY LOD CARDS
# ======================================================================================
# Closed, faceted crown shell ("low dome" + lobed rings) + 6-sided trunk stub.
# rings: (radius fraction, height fraction inside the crown); z of the bottom-centre point.
FAR_SHAPES = {
    "broad": dict(S=9, crown_bottom=0.30, trunk_r=0.065, star=0.11, lobes=6, jit=0.06, bottom_z=0.10,
                  rings=[(0.60, 0.0), (0.90, 0.12), (1.0, 0.30), (0.91, 0.47), (0.97, 0.62), (0.74, 0.80),
                         (0.50, 0.92)], apex_r=0.0),
    "tall": dict(S=9, crown_bottom=0.24, trunk_r=0.085, star=0.10, lobes=6, jit=0.05, bottom_z=0.08,
                 rings=[(0.56, 0.0), (0.88, 0.10), (1.0, 0.27), (0.90, 0.43), (0.97, 0.58), (0.72, 0.77),
                        (0.45, 0.91)], apex_r=0.0),
    "young": dict(S=9, crown_bottom=0.34, trunk_r=0.06, star=0.10, lobes=6, jit=0.05, bottom_z=0.10,
                  rings=[(0.62, 0.0), (0.92, 0.13), (1.0, 0.33), (0.90, 0.50), (0.94, 0.64), (0.70, 0.81),
                         (0.46, 0.93)], apex_r=0.0),
    "ancient": dict(S=9, crown_bottom=0.30, trunk_r=0.085, star=0.13, lobes=7, jit=0.07, bottom_z=0.10,
                    rings=[(0.68, 0.0), (0.95, 0.11), (1.0, 0.28), (0.89, 0.44), (0.96, 0.58), (0.76, 0.77),
                           (0.50, 0.91)], apex_r=0.0),
    "maple": dict(S=9, crown_bottom=None, trunk_r=0.05, star=0.10, lobes=4, jit=0.08, bottom_z=0.07,
                  rings=[(0.80, 0.0), (1.0, 0.12), (0.76, 0.30), (0.86, 0.43), (0.64, 0.60), (0.62, 0.72),
                         (0.36, 0.91)], apex_r=0.0),
    "pine": dict(S=7, crown_bottom=None, trunk_r=0.12, star=0.05, lobes=3, jit=0.04, bottom_z=0.04,
                 rings=[(1.0, 0.0), (0.42, 0.20), (0.86, 0.165), (0.34, 0.39), (0.70, 0.355),
                        (0.27, 0.58), (0.53, 0.545), (0.20, 0.76), (0.36, 0.73)], apex_r=0.0),
}


def gen_far(fid, shape, bmin, bmax, crown_tile, seed, crown_bottom=None):
    """Crown shell fitted EXACTLY to the target bbox in x/y/z; trunk stub on the origin."""
    rng = random.Random(seed)
    sp = FAR_SHAPES[shape]
    S = sp["S"]
    cb = crown_bottom if crown_bottom is not None else sp["crown_bottom"]
    hx, hy = (bmax[0] - bmin[0]) / 2, (bmax[1] - bmin[1]) / 2
    pts, faces, rings = [], [], []                    # crown in normalised coords (z: 0 = ground, 1 = top)
    for i, (rf, zf) in enumerate(sp["rings"]):
        ph = rng.uniform(0, 2 * math.pi)
        rot = rng.uniform(0, 2 * math.pi / S)
        ring = []
        for j in range(S):
            th = 2 * math.pi * j / S + rot
            m = 1.0 + sp["star"] * math.cos(sp["lobes"] * th + ph) + rng.uniform(-sp["jit"], sp["jit"])
            z = cb + (1.0 - cb) * zf + rng.uniform(-0.012, 0.012)
            pts.append((rf * m * math.cos(th), rf * m * math.sin(th), z))
            ring.append(len(pts) - 1)
        rings.append(ring)
    pts.append((rng.uniform(-0.05, 0.05), rng.uniform(-0.05, 0.05), cb + (1.0 - cb) * sp["bottom_z"]))
    bc = len(pts) - 1
    pts.append((rng.uniform(-0.06, 0.06), rng.uniform(-0.06, 0.06), 1.0))
    ap = len(pts) - 1
    for j in range(S):
        j1 = (j + 1) % S
        faces.append((bc, rings[0][j1], rings[0][j], "crown"))
        for i in range(len(rings) - 1):
            a, b, c, d = rings[i][j], rings[i][j1], rings[i + 1][j1], rings[i + 1][j]
            faces.append((a, b, c, "crown"))
            faces.append((a, c, d, "crown"))
        faces.append((rings[-1][j], rings[-1][j1], ap, "crown"))
    mn = [min(p[k] for p in pts) for k in range(2)]
    mx = [max(p[k] for p in pts) for k in range(2)]
    co = [(bmin[0] + (p[0] - mn[0]) / (mx[0] - mn[0]) * (bmax[0] - bmin[0]),
           bmin[1] + (p[1] - mn[1]) / (mx[1] - mn[1]) * (bmax[1] - bmin[1]),
           bmin[2] + p[2] * (bmax[2] - bmin[2])) for p in pts]
    # trunk stub: 6 sides, slight flare, centred on the origin, ends inside the crown
    tr = sp["trunk_r"]
    ztop = bmin[2] + (cb + (1 - cb) * 0.14) * (bmax[2] - bmin[2])
    tb, tt = [], []
    for j in range(6):
        th = 2 * math.pi * j / 6 + 0.3
        co.append((1.35 * tr * hx * math.cos(th), 1.35 * tr * hy * math.sin(th), bmin[2]))
        tb.append(len(co) - 1)
    for j in range(6):
        th = 2 * math.pi * j / 6 + 0.3
        co.append((0.75 * tr * hx * math.cos(th), 0.75 * tr * hy * math.sin(th), ztop))
        tt.append(len(co) - 1)
    for j in range(6):
        j1 = (j + 1) % 6
        faces.append((tb[j], tb[j1], tt[j1], "trunk"))
        faces.append((tb[j], tt[j1], tt[j], "trunk"))
    geo = Geo()
    for p in co:
        geo.v(p, {})
    cx, cy = (bmax[0] + bmin[0]) / 2, (bmax[1] + bmin[1]) / 2
    for a, b, c, part in faces:
        tile = crown_tile if part == "crown" else "far_bark"
        uvs = []
        for q in (a, b, c):
            p = co[q]
            u = 0.5 + 0.35 * (p[0] - cx) / max(hx, 1e-6) + 0.12 * (p[1] - cy) / max(hy, 1e-6)
            v = (p[2] - bmin[2]) / (bmax[2] - bmin[2])
            uvs.append(tile_uv(tile, u, v))
        geo.tri(a, b, c, uvs[0], uvs[1], uvs[2], False, part)
    return dict(geo=geo, trunk_xy=[0.0, 0.0], crown_bottom=cb)


# ======================================================================================
# 6. MODEL ASSEMBLY + CHECKS
# ======================================================================================
def geo_bbox(*geos):
    mn = [1e9] * 3
    mx = [-1e9] * 3
    for g in geos:
        if g is None:
            continue
        for p in g.co:
            for k in range(3):
                mn[k] = min(mn[k], p[k])
                mx[k] = max(mx[k], p[k])
    return mn, mx


def geo_hash(g):
    h = hashlib.sha1()
    for p in g.co:
        h.update(("%.4f,%.4f,%.4f;" % p).encode())
    for t, uv in zip(g.tris, g.uv):
        h.update(("%d,%d,%d:" % t).encode())
        for u in uv:
            h.update(("%.5f,%.5f;" % u).encode())
    for w in g.w:
        h.update(";".join("%s=%.4f" % (k, w[k]) for k in sorted(w)).encode())
    return h.hexdigest()


def check_uvs(g, name):
    """Every face must sit inside ONE tile, inside the 4 % inset."""
    bad = 0
    lo, hi = INSET - 1e-4, 1 - INSET + 1e-4
    for uv in g.uv:
        cells = set()
        for u, v in uv:
            cu, cv = u * 4.0, v * 4.0
            c, r = int(math.floor(cu - 1e-9 if cu >= 4 else cu)), int(math.floor(cv - 1e-9 if cv >= 4 else cv))
            fu, fv = cu - c, cv - r
            if not (lo <= fu <= hi and lo <= fv <= hi):
                bad += 1
            cells.add((c, r))
        if len(cells) > 1:
            bad += 1
    if bad:
        raise RuntimeError("UV check failed on %s: %d corners/faces outside a tile inset" % (name, bad))
    return True


def silhouette_coverage(leaf_geo, other_geos, azimuth_deg, dist=100.0, res=480, close_studs=8.0, height=None):
    """Side view from `dist` studs (horizontal distance to the trunk axis, camera at crown
    height). Rays are cast through every pixel; the crown silhouette is the leaf mask closed
    over gaps up to `close_studs` and hole-filled. Returns the share of the silhouette whose
    first hit is a leaf (leaves count from both sides, like DoubleSided in game), the share
    showing bark, and a diagnostic image (green leaf, brown bark, red sky inside the crown)."""
    from mathutils.bvhtree import BVHTree
    co, tris, part = [], [], []
    for g, kind in [(leaf_geo, 1)] + [(g, 2) for g in other_geos if g is not None]:
        o = len(co)
        co.extend(Vector(p) for p in g.co)
        tris.extend((a + o, b + o, c + o) for a, b, c in g.tris)
        part.extend([kind] * len(g.tris))
    bvh = BVHTree.FromPolygons(co, tris, all_triangles=True)
    mn, mx = geo_bbox(leaf_geo)
    ctr = Vector(((mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, height if height else (mn[2] + mx[2]) / 2))
    a = math.radians(azimuth_deg)
    cam = Vector((math.cos(a) * dist, math.sin(a) * dist, ctr.z))
    fwd = (Vector((0.0, 0.0, ctr.z)) - cam).normalized()
    right = fwd.cross(Z).normalized()
    up = right.cross(fwd)
    half = 0.0
    for p in leaf_geo.co[::3]:                       # frame the crown itself, not its bbox corners
        d = Vector(p) - cam
        f = d.dot(fwd)
        if f > 1e-3:
            half = max(half, abs(d.dot(right)) / f, abs(d.dot(up)) / f)
    half *= 1.06
    hit = np.zeros((res, res), np.uint8)
    for j in range(res):
        vy = (1.0 - 2.0 * (j + 0.5) / res) * half
        for i in range(res):
            vx = (2.0 * (i + 0.5) / res - 1.0) * half
            loc, nrm, idx, dd = bvh.ray_cast(cam, (fwd + right * vx + up * vy).normalized())
            if loc is not None:
                hit[j, i] = part[idx]
    leaf = hit == 1
    px = 2.0 * dist * half / res                       # studs per pixel at the trunk axis
    rad = max(1, int(round(close_studs / px / 2.0)))   # closing with a disk of diameter close_studs
    offs = [(dy, dx) for dy in range(-rad, rad + 1) for dx in range(-rad, rad + 1) if dx * dx + dy * dy <= rad * rad]

    def dilate(mask):
        p = np.pad(mask, rad)
        out = np.zeros_like(mask)
        for dy, dx in offs:
            out |= p[rad + dy:rad + dy + res, rad + dx:rad + dx + res]
        return out
    closed = ~dilate(~dilate(leaf))
    m = Image.fromarray((closed * 255).astype(np.uint8))
    pad = Image.new("L", (res + 2, res + 2), 0)
    pad.paste(m, (1, 1))
    ImageDraw.floodfill(pad, (0, 0), 77)
    sil = np.asarray(pad, np.uint8)[1:-1, 1:-1] != 77
    n = max(int(sil.sum()), 1)
    cov = float((leaf & sil).sum()) / n
    bark = float(((hit == 2) & sil).sum()) / n
    img = np.full((res, res, 3), 235, np.uint8)
    img[sil] = (205, 60, 50)
    img[(hit == 2) & sil] = (120, 80, 45)
    img[(hit == 2) & ~sil] = (170, 140, 110)
    img[leaf] = (60, 140, 70)
    return dict(azimuth=azimuth_deg, leaf_coverage=round(cov, 4), bark_visible=round(bark, 4),
                sky_gaps=round(1.0 - cov - bark, 4), close_studs=close_studs, px_studs=round(px, 3)), img


def pad_coverage(d, step=0.4):
    """How full each maple pad reads on its own: rays straight down over the pad footprint
    (ellipse 0.85 rh) and horizontally through its side outline (0.85 rh x 0.85 rv) from 4
    sides; share of rays that hit that pad's own leaves. Mean and minimum over all pads."""
    from mathutils.bvhtree import BVHTree
    g = d["leaves"]
    tops, sides = [], []
    for p in d["pads"]:
        t0, t1 = p["tris"]
        vids = sorted({i for t in g.tris[t0:t1] for i in t})
        rm = {v: k for k, v in enumerate(vids)}
        bvh = BVHTree.FromPolygons([Vector(g.co[v]) for v in vids], [tuple(rm[i] for i in t) for t in g.tris[t0:t1]],
                                   all_triangles=True)
        c, rh, rv = p["c"], p["rh"] * 0.85, p["rv"] * 0.85
        hit = tot = 0
        n = int(2 * rh / step)
        for i in range(n + 1):
            for j in range(n + 1):
                x, y = -rh + i * step, -rh + j * step
                if x * x + y * y <= rh * rh:
                    tot += 1
                    hit += bvh.ray_cast(Vector((c.x + x, c.y + y, c.z + 30.0)), -Z)[0] is not None
        tops.append(hit / max(tot, 1))
        hit = tot = 0
        for az in (0.0, 90.0, 180.0, 270.0):
            dv = Vector((math.cos(math.radians(az)), math.sin(math.radians(az)), 0.0))
            sv = Vector((-dv.y, dv.x, 0.0))
            for i in range(n + 1):
                for j in range(int(2 * rv / step) + 1):
                    s_, z_ = -rh + i * step, -rv + j * step
                    if (s_ / rh) ** 2 + (z_ / rv) ** 2 <= 1.0:
                        tot += 1
                        hit += bvh.ray_cast(c + dv * 40.0 + sv * s_ + Z * z_, -dv)[0] is not None
        sides.append(hit / max(tot, 1))
    return dict(top_mean=round(sum(tops) / len(tops), 4), top_min=round(min(tops), 4),
                side_mean=round(sum(sides) / len(sides), 4), side_min=round(min(sides), 4))


def make_tree_model(mid, data, kind, ref=None):
    """ref = the batch A (rev 1) build of the same tree: bone positions and collider boxes are
    taken from it so they stay exactly as approved."""
    col = new_collection(mid)
    mn, mx = geo_bbox(data["bark"], data.get("leaves"), data.get("glow"))
    rmn, rmx = geo_bbox(ref["bark"], ref.get("leaves"), ref.get("glow")) if ref else (mn, mx)
    H = rmx[2]
    W = max(rmx[0] - rmn[0], rmx[1] - rmn[1])
    arm = make_armature(mid, col, H, W)
    mat = get_atlas_material()
    objs = {"Bark": mesh_from_geo(mid + "__Bark", data["bark"], mat, col, mid, arm)}
    check_uvs(data["bark"], mid + "__Bark")
    if data.get("leaves") is not None and data["leaves"].ntris():
        objs["Leaves"] = mesh_from_geo(mid + "__Leaves", data["leaves"], mat, col, mid, arm)
        check_uvs(data["leaves"], mid + "__Leaves")
    if data.get("glow") is not None:
        gm = get_flat_material("Glow", GLOW_RGB, emission=1.5)
        objs["Glow"] = mesh_from_geo(mid + "__Glow", data["glow"], gm, col, mid, arm)
    rec = dict(kind=kind, collection=mid, bbox_min=mn, bbox_max=mx, armature=arm.name,
               bones=[b.name for b in arm.data.bones], objects={k: o.name for k, o in objs.items()},
               triangles={k: (data[k.lower()].ntris()) for k in objs}, info=data.get("info", {}),
               usage=data.get("usage", {}), trunk_tile=data.get("trunk_tile"),
               geometry_sha1={k: geo_hash(data[k.lower()]) for k in objs})
    rec["triangles"]["total"] = sum(v for k, v in rec["triangles"].items())
    STATE["models"][mid] = rec
    STATE["colliders"][mid] = [[[round(c["c"][0], 3), round(c["c"][1], 3), round(c["c"][2], 3)],
                                [round(c["s"][0], 3), round(c["s"][1], 3), round(c["s"][2], 3)],
                                round(c["yaw"], 2)] for c in (ref or data)["colliders"]]
    if ref:
        rec["size_batch_A"] = [rmx[0] - rmn[0], rmx[2] - rmn[2], rmx[1] - rmn[1]]
    return col, arm, objs


def _uv_tile_local(u, v):
    c = min(3, int(u * 4.0))
    rb = min(3, int(v * 4.0))
    name = next(k for k, (cc, rr) in TILES.items() if cc == c and rr == 3 - rb)
    return name, ((u * 4.0 - c) - INSET) / (1 - 2 * INSET), ((v * 4.0 - rb) - INSET) / (1 - 2 * INSET)


def _inside(poly, x, y):
    inside = False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def leaf_card_check(g, outlines):
    """Revision-2 leaf test (replaces the ray test, which assumed two-sided cards):
    every card is a proper leaf - no degenerate triangle, all of its triangles wound to the
    same side (toward -n, the Jura convention), midrib lifted >= 8 % of L toward +n - and
    UV samples inside every leaf triangle fall inside the painted leaf outline of its tile,
    so the tile background (a square card edge) can never show."""
    deg = wind = lift = uv_out = samples = 0
    kinds = {}
    for cd in g.cards:
        n = Vector(cd["n"])
        kinds[cd["nt"]] = kinds.get(cd["nt"], 0) + 1
        for k in range(cd["t0"], cd["t0"] + cd["nt"]):
            a, b, c = (Vector(g.co[i]) for i in g.tris[k])
            cr = (b - a).cross(c - a)
            if cr.length < 1e-5:
                deg += 1
            elif cr.normalized().dot(n) > -0.05:
                wind += 1
        if cd["lift"] is not None:
            top = Vector(g.co[cd["v0"] + cd["lift"]])
            if (top - Vector(cd["base"])).dot(n) < 0.08 * cd["L"]:
                lift += 1
    bary = ((1 / 3, 1 / 3, 1 / 3), (.70, .15, .15), (.15, .70, .15), (.15, .15, .70))
    for tri in g.uv:
        for wa, wb, wc in bary:
            u = wa * tri[0][0] + wb * tri[1][0] + wc * tri[2][0]
            v = wa * tri[0][1] + wb * tri[1][1] + wc * tri[2][1]
            name, lu, lv = _uv_tile_local(u, v)
            samples += 1
            if not any(_inside(p, lu, lv) for p in outlines.get(name, [])):
                uv_out += 1
    return dict(cards=len(g.cards), cards_by_triangle_count=kinds, degenerate_tris=deg,
                inconsistent_winding_tris=wind, cards_without_lifted_midrib=lift,
                uv_samples=samples, uv_samples_outside_leaf_outline=uv_out,
                ok=(deg == 0 and wind == 0 and lift == 0 and uv_out == 0 and len(g.cards) * 1 > 0))


def make_far_model(fid, far, crown_tile, source):
    col = new_collection(fid)
    mat = get_atlas_material()
    ob = mesh_from_geo(fid + "__Card", far["geo"], mat, col)
    check_uvs(far["geo"], fid)
    mn, mx = geo_bbox(far["geo"])
    STATE["models"][fid] = dict(kind="far_card", collection=fid, bbox_min=mn, bbox_max=mx, armature=None,
                                bones=[], objects={"Card": ob.name},
                                triangles={"Card": far["geo"].ntris(), "total": far["geo"].ntris()},
                                info=dict(source=source, crown_tile=crown_tile,
                                          trunk_xy=[round(v, 3) for v in far["trunk_xy"]],
                                          crown_bottom_fraction=round(far["crown_bottom"], 3)),
                                geometry_sha1={"Card": geo_hash(far["geo"])})
    STATE["colliders"][fid] = []
    return col, ob


def wind_test(mid, arm, objs):
    """Rotate each wind bone by 0.05 rad on X and measure how far the meshes move."""
    dg = bpy.context.evaluated_depsgraph_get()
    rest = {}
    for k, ob in objs.items():
        rest[k] = [v.co.copy() for v in ob.evaluated_get(dg).to_mesh().vertices]
        ob.evaluated_get(dg).to_mesh_clear()
    res = {}
    for b in range(1, 5):
        pb = arm.pose.bones["%s_Wind%d" % (mid, b)]
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = (0.05, 0.0, 0.0)
        bpy.context.view_layer.update()
        dg = bpy.context.evaluated_depsgraph_get()
        for k, ob in objs.items():
            ev = ob.evaluated_get(dg)
            me = ev.to_mesh()
            mv = max(((v.co - r).length for v, r in zip(me.vertices, rest[k])), default=0.0)
            ev.to_mesh_clear()
            res.setdefault(k, {})["Wind%d" % b] = round(mv, 4)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()
    return res


def facing_check(geos, card_geo=None, nrays=4000, seed=7):
    """Cast rays from a sphere around the model toward its inside; the first face hit must
    face the ray (back-face culling ON would otherwise show a hole / inverted normal).
    Hits on the thin rim of a leaf card are counted separately: the two quads of a
    two-sided card sit +-0.012 stud apart, so a grazing ray can slip past one quad's edge."""
    from mathutils.bvhtree import BVHTree
    from mathutils.geometry import intersect_point_line
    co, tris, owner = [], [], []
    for g in geos:
        if g is None:
            continue
        o = len(co)
        co.extend(Vector(p) for p in g.co)
        tris.extend((a + o, b + o, c + o) for a, b, c in g.tris)
        owner.extend([(g, o)] * len(g.tris))
    bvh = BVHTree.FromPolygons(co, tris, all_triangles=True)
    mn = [min(p[k] for p in co) for k in range(3)]
    mx = [max(p[k] for p in co) for k in range(3)]
    ctr = Vector([(mn[k] + mx[k]) / 2 for k in range(3)])
    rad = (Vector(mx) - Vector(mn)).length
    rng = random.Random(seed)
    hits = back = sliver = 0
    for _ in range(nrays):
        o = ctr + rand_unit(rng) * rad
        tgt = Vector([rng.uniform(mn[k], mx[k]) for k in range(3)])
        d = (tgt - o).normalized()
        loc, nrm, idx, dist = bvh.ray_cast(o, d)
        if loc is None:
            continue
        hits += 1
        if nrm.dot(d) > 1e-4:
            g, off = owner[idx]
            if g is card_geo:
                c = (tris[idx][0] - off) // 8
                q = [co[off + 8 * c + k] for k in range(4)]
                de = min((intersect_point_line(loc, q[k], q[(k + 1) % 4])[0] - loc).length for k in range(4))
                cth = max(abs(nrm.dot(d)), 0.005)
                if de < 0.02 + 0.024 * math.sqrt(1.0 - cth * cth) / cth:   # rim seen through the 2*eps gap
                    sliver += 1
                    continue
            back += 1
    return dict(rays=nrays, hits=hits, backfacing_first_hits=back, card_edge_slivers=sliver)


# ======================================================================================
# 7. EXPORT
# ======================================================================================
def select_only(objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def make_fbx_deterministic():
    """The stock FBX exporter derives node UIDs from Python's per-process randomised hash()
    and stamps datetime.now() into the header, so two runs never give identical files.
    Patch both at runtime (the installed add-on is not modified)."""
    import importlib
    import datetime
    try:
        fu = importlib.import_module("io_scene_fbx.fbx_utils")
        eb = importlib.import_module("io_scene_fbx.export_fbx_bin")
    except Exception as e:                          # pragma: no cover - other Blender layouts
        return "not patched (%s)" % e

    def stable_uuid(uuids, key):
        if isinstance(key, int) and 0 <= key < 2 ** 63:
            uuid = key
        else:
            uuid = int.from_bytes(hashlib.sha1(repr(key).encode()).digest()[:8], "little") >> 1
        if uuid > int(1e9) and uuid % int(1e9) not in uuids:
            uuid = uuid % int(1e9)
        while uuid in uuids:
            uuid += 1
        return uuid
    fu._key_to_uuid = stable_uuid
    if not getattr(eb.fbx_header_elements, "_jura_fixed", False):
        orig = eb.fbx_header_elements

        def header(root, scene_data, time=None):
            return orig(root, scene_data, time=datetime.datetime(2026, 1, 1, 12, 0, 0))
        header._jura_fixed = True
        eb.fbx_header_elements = header
    return "patched (stable uids, fixed header date)"


def export_fbx(mid):
    col = bpy.data.collections[mid]
    objs = list(col.objects)
    arm = [o for o in objs if o.type == "ARMATURE"]
    select_only(arm + [o for o in objs if o.type == "MESH"])
    path = os.path.join(OUT, mid + ".fbx")
    bpy.ops.export_scene.fbx(
        filepath=path, use_selection=True, object_types={"MESH", "ARMATURE"},
        apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS", global_scale=1.0,
        axis_forward="-Z", axis_up="Y", use_mesh_modifiers=False, mesh_smooth_type="OFF",
        add_leaf_bones=False, use_armature_deform_only=True, bake_anim=False,
        path_mode="COPY", embed_textures=True, use_custom_props=False)
    return path


def export_glb():
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    path = os.path.join(OUT, BATCH + ".glb")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=False,
                              export_apply=False, export_skins=True, export_animations=False,
                              export_yup=True, export_materials="EXPORT")
    return path


# ======================================================================================
# 8. RENDERS (Workbench, studio light, backface culling ON, light grey background)
# ======================================================================================
BG = (214, 214, 212)


def box(geo, c, s, w=None, tile=None):
    w = w or {}
    x, y, z = c
    a, b, h = s[0] / 2, s[1] / 2, s[2] / 2
    v = [geo.v((x + sx * a, y + sy * b, z + sz * h), w) for sx, sy, sz in
         [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]]
    uv = (0.5, 0.5)
    for f in [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]:
        geo.quad(v[f[0]], v[f[1]], v[f[2]], v[f[3]], uv, uv, uv, uv)


def human_geo():
    """5-stud tall human silhouette (scale reference, renders only)."""
    g = Geo()
    box(g, (-0.33, 0, 1.05), (0.55, 0.5, 2.1))
    box(g, (0.33, 0, 1.05), (0.55, 0.5, 2.1))
    box(g, (0, 0, 2.95), (1.5, 0.7, 1.8))
    box(g, (-1.0, 0, 2.9), (0.42, 0.42, 1.75))
    box(g, (1.0, 0, 2.9), (0.42, 0.42, 1.75))
    box(g, (0, 0, 4.42), (0.95, 0.95, 1.16))
    return g


def render_scene_setup():
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x = sc.render.resolution_y = 1024
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = "PNG"
    sc.render.image_settings.color_mode = "RGB"
    sh = sc.display.shading
    sh.light = "STUDIO"
    sh.color_type = "TEXTURE"
    sh.show_backface_culling = True
    sh.show_object_outline = False
    sh.show_cavity = False
    sh.show_shadows = False
    sh.show_specular_highlight = False
    sc.display.render_aa = "16"
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    world = bpy.data.worlds.get("RenderWorld") or bpy.data.worlds.new("RenderWorld")
    world.color = tuple(srgb_to_lin(c) for c in BG)
    sc.world = world
    cam_data = bpy.data.cameras.new("RenderCam")
    cam = bpy.data.objects.new("RenderCam", cam_data)
    hcol = new_collection("_render_helpers")
    hcol.objects.link(cam)
    sc.camera = cam
    hum = mesh_from_geo("_human", human_geo(), get_flat_material("Human", (52, 56, 66)), hcol)
    return cam, hum, hcol


def set_visible(names):
    for col in bpy.context.scene.collection.children:
        col.hide_render = col.name not in names


def render_to(path, size=(1024, 1024)):
    sc = bpy.context.scene
    sc.render.resolution_x, sc.render.resolution_y = size
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    Image.open(path).convert("RGB").save(path)       # drop Blender's date/time PNG metadata


def aim(cam, direction, centre, dist):
    d = Vector(direction).normalized()
    cam.location = Vector(centre) + d * dist
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()


def fit_ortho(cam, direction, pts, margin=1.12, aspect=1.0):
    """Orthographic camera along `direction`, framing the given points (actual vertices)."""
    d = Vector(direction).normalized()
    mn = [min(p[k] for p in pts) for k in range(3)]
    mx = [max(p[k] for p in pts) for k in range(3)]
    centre = (Vector(mn) + Vector(mx)) / 2
    aim(cam, d, centre, 4000.0)
    rot = cam.rotation_euler.to_matrix()
    xa, ya = rot @ Vector((1, 0, 0)), rot @ Vector((0, 1, 0))
    xs, ys = [], []
    for q in pts:
        p = Vector(q) - centre
        xs.append(p.dot(xa))
        ys.append(p.dot(ya))
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    off = xa * ((max(xs) + min(xs)) / 2) + ya * ((max(ys) + min(ys)) / 2)
    aim(cam, d, centre + off, 4000.0)
    cam.data.type = "ORTHO"
    cam.data.sensor_fit = "HORIZONTAL" if aspect != 1.0 else "AUTO"
    cam.data.ortho_scale = max(w, h * aspect) * margin
    cam.data.clip_start = 1.0
    cam.data.clip_end = 10000.0


VIEWS = {"front": (0, -1, 0), "back": (0, 1, 0), "left": (-1, 0, 0), "right": (1, 0, 0),
         "three_quarter": (1, -1, 1.1)}


def render_model_views(mid, cam, hum):
    rec = STATE["models"][mid]
    mn, mx = list(rec["bbox_min"]), list(rec["bbox_max"])
    hum.location = (max(4.0, mx[0] * 0.4) + 1.0, mn[1] - 1.0, 0.0)
    pts = [o.matrix_world @ v.co for o in bpy.data.collections[mid].objects if o.type == "MESH"
           for v in o.data.vertices]
    pts += [hum.location + Vector((sx, sy, sz)) for sx in (-1.3, 1.3) for sy in (-0.6, 0.6) for sz in (0.0, 5.0)]
    set_visible({mid, "_render_helpers"})
    out = []
    for vname, d in VIEWS.items():
        fit_ortho(cam, d, pts, 1.08)
        p = os.path.join(REN, "%s_%s.png" % (mid, vname))
        render_to(p)
        out.append(os.path.relpath(p, OUT))
    return out


def render_arch_walkthrough(cam, hum):
    """Eye-level view along the ground: a 5-stud human standing under each arch root
    (proof of the >= 7 stud clearance), camera tangential to the trunk."""
    info = STATE["models"]["dryas_tree"]["info"]["arch_clearance"]
    set_visible({"dryas_tree", "_render_helpers"})
    gnd = bpy.data.objects.get("_ground")
    if gnd:
        gnd.hide_render = False
    outs = []
    for k, arch in enumerate(info[:1]):
        a = math.radians(arch["azimuth_deg"])
        rm = arch["human_radius"]
        hp = Vector((rm * math.cos(a), rm * math.sin(a), 0.0)) + Vector(arch["lateral_offset"])
        hum.location = hp
        tg = Vector((-math.sin(a), math.cos(a), 0.0))
        rad = Vector((math.cos(a), math.sin(a), 0.0))
        cam.data.type = "PERSP"
        cam.data.lens = 16.0
        cam.data.sensor_fit = "AUTO"
        cam.data.clip_start = 0.3
        cam.data.clip_end = 3000.0
        cam.location = hp + tg * 13.0 + rad * 1.5 + Vector((0, 0, 2.2))
        cam.rotation_euler = (hp + Vector((0, 0, 7.5)) - cam.location).to_track_quat("-Z", "Y").to_euler()
        hum.rotation_euler = (0.0, 0.0, math.atan2(tg.y, tg.x) + math.pi / 2)
        sh = bpy.context.scene.display.shading
        sh.use_world_space_lighting = True
        sh.studiolight_rotate_z = math.radians(-60.0)
        p = os.path.join(REN, "dryas_tree_arch_walkthrough.png")
        render_to(p)
        sh.use_world_space_lighting = False
        sh.studiolight_rotate_z = 0.0
        hum.rotation_euler = (0.0, 0.0, 0.0)
        im = Image.open(p).convert("RGB")
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 1024, 52], fill=BG)
        d.text((14, 8), "dryas_tree: 5-stud human standing under an arch root, %.1f studs from the trunk axis" % rm,
               fill=(25, 25, 25), font=_font(16))
        d.text((14, 29), "root underside above the human: %.1f studs; >= 7 studs over %.1f radial studs on all %d arches"
               % (arch["underside_at_human"], min(x["span_len"] for x in info), len(info)),
               fill=(25, 25, 25), font=_font(16))
        im.save(p)
        outs.append(os.path.relpath(p, OUT))
    cam.data.lens = 50.0
    if gnd:
        gnd.hide_render = True
    return outs


def add_render_backfaces(ids):
    """Renders only (after export + .blend save): the game draws leaf meshes DoubleSided, but
    Workbench culls back faces for every object alike. A flipped copy of each leaf mesh shows
    the leaves from both sides while bark keeps back-face culling ON."""
    made = []
    for mid in ids:
        col = bpy.data.collections[mid]
        for o in list(col.objects):
            if o.type == "MESH" and o.name.endswith("__Leaves"):
                made.append(flipped_copy(o, col))
    return made


def flipped_copy(o, col):
    me = o.data.copy()
    me.flip_normals()
    ob = bpy.data.objects.new("_backfaces_" + o.name, me)
    col.objects.link(ob)
    ob.parent = o.parent
    return ob


def render_before_after(cam, hum, ref, atlas_a):
    """maple_b and the dryas from one 3/4 camera each: batch A (left) vs revision 2 (right)."""
    import tempfile
    tmp = tempfile.mkdtemp()
    p = os.path.join(tmp, "atlas_batch_A.png")
    atlas_a.save(p)
    img = bpy.data.images.load(p)
    img.pack()
    os.remove(p)
    os.rmdir(tmp)
    mat_a = atlas_material("Atlas_batch_A", img)
    glow = get_flat_material("Glow", GLOW_RGB, emission=1.5)
    sheet = Image.new("RGB", (1024, 1024), BG)
    dr = ImageDraw.Draw(sheet)
    dr.text((14, 6), "Leaf cards before / after - same 3/4 camera per tree (leaves rendered double-sided, bark culled)",
            fill=(30, 30, 30), font=_font(16))
    cw, ch = 512, 488
    for row, mid in enumerate(("maple_b", "dryas_tree")):
        bid = "_before_" + mid
        col = new_collection(bid)
        d = ref[mid]
        objs = [mesh_from_geo(bid + "__Bark", d["bark"], mat_a, col), mesh_from_geo(bid + "__Leaves", d["leaves"], mat_a, col)]
        if d.get("glow") is not None:
            objs.append(mesh_from_geo(bid + "__Glow", d["glow"], glow, col))
        rec = STATE["models"][mid]
        hum.location = (max(4.0, rec["bbox_max"][0] * 0.4) + 1.0, rec["bbox_min"][1] - 1.0, 0.0)
        pts = [v.co.copy() for o in objs for v in o.data.vertices]
        pts += [v.co.copy() for o in bpy.data.collections[mid].objects if o.type == "MESH" for v in o.data.vertices]
        pts += [hum.location + Vector((sx, sy, sz)) for sx in (-1.3, 1.3) for sy in (-0.6, 0.6) for sz in (0.0, 5.0)]
        a_tris = sum(len(o.data.polygons) for o in objs)
        b_tris = rec["triangles"]["total"]
        a_cards = d["leaves"].ntris() // 4
        b_cards = len(gens_leaf_cards(mid))
        for colm, (which, label) in enumerate(((bid, "batch A: %d tris, %d square two-sided cards" % (a_tris, a_cards)),
                                                (mid, "revision 2: %d tris, %d leaf-shaped cards" % (b_tris, b_cards)))):
            set_visible({which, "_render_helpers"})
            fit_ortho(cam, VIEWS["three_quarter"], pts, 1.04, aspect=cw / ch)
            tp = os.path.join(REN, "_ba.png")
            render_to(tp, (cw, ch))
            sheet.paste(Image.open(tp).convert("RGB"), (colm * cw, 30 + row * (ch + 5)))
            os.remove(tp)
            dr.text((colm * cw + 10, 34 + row * (ch + 5)), "%s - %s" % (mid, label), fill=(25, 25, 25), font=_font(14))
    cam.data.sensor_fit = "AUTO"
    out = os.path.join(REN, "before_after_leaves.png")
    sheet.save(out)
    return os.path.relpath(out, OUT)


def gens_leaf_cards(mid):
    return STATE["models"][mid].get("_cards", [])


def coverage_figure(cov_imgs, batch_a):
    """2x2 sheet of the dryas side views used for the leaf-coverage measurement."""
    sheet = Image.new("RGB", (1024, 1024), (255, 255, 255))
    dr = ImageDraw.Draw(sheet)
    for k, (r, img) in enumerate(cov_imgs):
        im = Image.fromarray(img).resize((500, 470), Image.NEAREST)
        x, y = (k % 2) * 512 + 6, 62 + (k // 2) * 482
        sheet.paste(im, (x, y))
        dr.text((x + 6, y + 4), "azimuth %d deg: leaves %.1f %%, bark %.1f %%, sky %.1f %%" % (
            r["azimuth"], 100 * r["leaf_coverage"], 100 * r["bark_visible"], 100 * r["sky_gaps"]),
            fill=(20, 20, 20), font=_font(14))
    m = sum(r["leaf_coverage"] for r, _ in cov_imgs) / len(cov_imgs)
    ma = sum(r["leaf_coverage"] for r in batch_a) / len(batch_a)
    dr.text((10, 6), "dryas_tree - leaf coverage of the crown silhouette, side views from 100 studs (crown height)",
            fill=(20, 20, 20), font=_font(16))
    dr.text((10, 28), "green leaf, red sky gap, dark brown limb inside the crown outline (gaps < %g studs closed). "
                      "Mean %.1f %% (batch A: %.1f %%)" % (cov_imgs[0][0]["close_studs"], 100 * m, 100 * ma),
            fill=(20, 20, 20), font=_font(13))
    out = os.path.join(REN, "dryas_tree_leaf_coverage.png")
    sheet.save(out)
    return os.path.relpath(out, OUT)


def place_collection(mid, loc):
    for o in bpy.data.collections[mid].objects:
        if o.parent is None:
            o.location = loc


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_lineup(path, rows, cam, hum, title):
    """Rows of models rendered front-on (orthographic) at ONE common scale, each row with a
    5-stud human silhouette, then stacked into a single 1024x1024 sheet with labels."""
    gap = 12.0
    layouts, maxw, sumh = [], 0.0, 0.0
    for row in rows:
        x = 0.0
        items = [("human", 3.0, 5.0, None)]
        x += 3.0 + gap
        lay = [("human", 1.5, 3.0)]
        for mid in row:
            r = STATE["models"][mid]
            w = r["bbox_max"][0] - r["bbox_min"][0]
            lay.append((mid, x - r["bbox_min"][0], w))
            x += w + gap
        rowh = max(STATE["models"][m]["bbox_max"][2] for m in row)
        layouts.append((lay, x - gap, rowh))
        maxw = max(maxw, x - gap)
        sumh += rowh
    label_px = 32
    scale = min(1000.0 / maxw, (1024 - 56 - len(rows) * (label_px + 14)) / sumh)
    sheet = Image.new("RGB", (1024, 1024), BG)
    dr = ImageDraw.Draw(sheet)
    dr.text((14, 6), title, fill=(40, 40, 40), font=_font(20))
    ycur = 52
    for ri, (lay, roww, rowh) in enumerate(layouts):
        names = [m for m, _, _ in lay if m != "human"]
        set_visible(set(names) | {"_render_helpers"})
        for m, x0, w in lay:
            if m == "human":
                hum.location = (x0, 0.0, 0.0)
            else:
                place_collection(m, (x0, 0.0, 0.0))
        hpx = int(math.ceil((rowh + 2.0) * scale))
        wpx = 1024
        sc = bpy.context.scene
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = wpx / scale
        cam.data.clip_end = 10000.0
        cam.data.sensor_fit = "HORIZONTAL"
        aim(cam, (0, -1, 0), (roww / 2.0, 0.0, (rowh + 2.0) / 2.0 - 1.0), 4000.0)
        tmp = os.path.join(REN, "_row.png")
        render_to(tmp, (wpx, hpx))
        im = Image.open(tmp).convert("RGB")
        sheet.paste(im, (0, ycur))
        os.remove(tmp)
        y_lab = ycur + hpx + 2
        right_edge = [-1e9, -1e9]                  # per text line: where the previous label ended
        for m, x0, w in lay:
            if m == "human":
                continue
            r = STATE["models"][m]
            cxpx = 512 + ((x0 + (r["bbox_min"][0] + r["bbox_max"][0]) / 2) - roww / 2.0) * scale
            sz = r["size"]
            lines = ((m, _font(13)), ("%g x %g x %g" % (sz[0], sz[1], sz[2]), _font(11)))
            spans = []
            for txt, f in lines:
                tw = dr.textlength(txt, font=f)
                spans.append((min(max(cxpx - tw / 2, 4), 1020 - tw), tw))
            drop = 12 if any(x < right_edge[li] + 6 for li, (x, _) in enumerate(spans)) else 0
            for li, ((txt, f), (tx, tw)) in enumerate(zip(lines, spans)):
                dr.text((tx, y_lab + li * 14 + drop), txt, fill=(30, 30, 30), font=f)
                right_edge[li] = tx + tw if not drop else right_edge[li]
        ycur = y_lab + label_px
        for m, x0, w in lay:
            if m != "human":
                place_collection(m, (0.0, 0.0, 0.0))
    dr.text((14, 30), "human silhouette = 5 studs (left of each row); one common scale: %.2f px/stud" % scale,
            fill=(60, 60, 60), font=_font(13))
    sheet.save(path)
    cam.data.sensor_fit = "AUTO"
    return scale


def backdrop(hcol):
    """Hazy mountain ridge + ground for the 1500-stud comparisons (renders only)."""
    g = Geo()
    rng = random.Random(99)
    n = 60
    prev = None
    for i in range(n + 1):
        x = -14000 + 28000 * i / n
        h = 500 + 450 * math.sin(i * 0.45) + rng.uniform(0, 380)
        a = g.v((x, 6500, -50), {})
        b = g.v((x, 6500 + rng.uniform(-200, 200), h), {})
        if prev:
            g.quad(prev[0], a, b, prev[1], (0, 0), (0, 0), (0, 0), (0, 0))
        prev = (a, b)
    m1 = mesh_from_geo("_mountains", g, get_flat_material("Haze", (176, 189, 204)), hcol)
    gg = Geo()
    a, b, c, d = [gg.v(p, {}) for p in [(-15000, -3000, 0), (15000, -3000, 0), (15000, 7000, 0), (-15000, 7000, 0)]]
    gg.quad(a, b, c, d, (0, 0), (0, 0), (0, 0), (0, 0))
    m2 = mesh_from_geo("_ground", gg, get_flat_material("Ground", (150, 162, 140)), hcol)
    return [m1, m2]


def render_far_compare(fid, full_id, cam, hcol, extra):
    """Full tree and its far card side by side, seen from 1500 studs (fov 70) from a
    45-degree azimuth, plus an optically zoomed inset taken from the same camera."""
    rf, rc = STATE["models"][full_id], STATE["models"][fid]
    span = max(rf["bbox_max"][0] - rf["bbox_min"][0], rf["bbox_max"][1] - rf["bbox_min"][1])
    vd = Vector((1, -1, 0)).normalized()               # camera looks from the front-right (45 deg)
    perp = Vector((1, 1, 0)).normalized()
    off = span * 0.62 + 6.0
    place_collection(full_id, tuple(-perp * off))
    place_collection(fid, tuple(perp * off))
    set_visible({full_id, fid, "_render_helpers"})
    for o in extra:
        o.hide_render = False
    H = max(rf["bbox_max"][2], rc["bbox_max"][2])
    tgt = Vector((0, 0, H * 0.5))
    cam.data.type = "PERSP"
    cam.data.sensor_fit = "VERTICAL"
    cam.data.clip_start = 1.0
    cam.data.clip_end = 30000.0
    cam.location = vd * 1500.0 + Vector((0, 0, 6.0))      # player eye level on flat ground
    cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()
    sh = bpy.context.scene.display.shading
    sh.use_world_space_lighting = True                     # sun fixed in the world, like in game
    sh.studiolight_rotate_z = math.radians(-60.0)
    cam.data.angle = math.radians(70.0)
    sc = bpy.context.scene
    old_bg = sc.world.color[:]
    sc.world.color = tuple(srgb_to_lin(c) for c in (205, 215, 226))
    main = os.path.join(REN, "_far_main.png")
    render_to(main)
    width = 2 * off + span
    zoom_angle = 2 * math.atan((max(width, H) * 0.62) / 1500.0)
    cam.data.angle = zoom_angle
    zoom = os.path.join(REN, "_far_zoom.png")
    render_to(zoom)
    sc.world.color = old_bg
    sh.use_world_space_lighting = False
    sh.studiolight_rotate_z = 0.0
    cam.data.angle = math.radians(70.0)
    cam.data.sensor_fit = "AUTO"
    for o in extra:
        o.hide_render = True
    place_collection(full_id, (0, 0, 0))
    place_collection(fid, (0, 0, 0))
    im = Image.open(main).convert("RGB")
    zm = Image.open(zoom).convert("RGB").resize((470, 470), Image.LANCZOS)
    dr = ImageDraw.Draw(im)
    x0, y0 = 14, 1024 - 470 - 40
    dr.rectangle([x0 - 3, y0 - 3, x0 + 470 + 2, y0 + 470 + 2], outline=(40, 40, 40), width=3)
    im.paste(zm, (x0, y0))
    zf = math.radians(70.0) / zoom_angle
    f = _font(15)
    full_lbl = full_id if not full_id.startswith("_ref") else "stand-in of the existing tree"
    dr.text((14, 10), "%s vs %s - camera 1500 studs away, vertical fov 70 deg, 45 deg azimuth" % (full_lbl, fid),
            fill=(25, 25, 25), font=f)
    dr.text((14, 30), "eye level (6 studs), world-space light; full frame = true size on a 1024 px screen; inset = same camera zoomed x%.1f" % (
        math.tan(math.radians(35)) / math.tan(zoom_angle / 2)), fill=(25, 25, 25), font=f)
    dr.text((x0, y0 + 474), "left: full tree    right: far card", fill=(25, 25, 25), font=f)
    out = os.path.join(REN, "far_compare_%s_1500.png" % fid)
    im.save(out)
    os.remove(main)
    os.remove(zoom)
    return os.path.relpath(out, OUT)


# ======================================================================================
# 9. EXPORT VERIFICATION (re-import into an empty scene)
# ======================================================================================
def verify_exports(ids):
    res = {}
    for mid in ids:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.unit_settings.scale_length = 0.01      # same unit setup as the build scene
        path = os.path.join(OUT, mid + ".fbx")
        bpy.ops.import_scene.fbx(filepath=path)
        objs = list(bpy.context.scene.objects)
        meshes = [o for o in objs if o.type == "MESH"]
        arms = [o for o in objs if o.type == "ARMATURE"]
        dg = bpy.context.evaluated_depsgraph_get()
        mn, mx = [1e9] * 3, [-1e9] * 3
        tris, ngons = {}, 0
        for o in meshes:
            me = o.data
            tris[o.name] = sum(len(p.vertices) - 2 for p in me.polygons)
            ngons += sum(1 for p in me.polygons if len(p.vertices) > 4)
            for v in me.vertices:
                p = o.matrix_world @ v.co
                for k in range(3):
                    mn[k] = min(mn[k], p[k])
                    mx[k] = max(mx[k], p[k])
        tris = dict(sorted(tris.items()))
        rec = STATE["models"][mid]
        size = [round(mx[0] - mn[0], 2), round(mx[2] - mn[2], 2), round(mx[1] - mn[1], 2)]
        exp_tris = {rec["objects"][k]: v for k, v in rec["triangles"].items() if k != "total"}
        res[mid] = dict(objects=sorted(o.name for o in objs),
                        bones=sorted(b.name for a in arms for b in a.data.bones),
                        vertex_groups=sorted({g.name for o in meshes for g in o.vertex_groups}),
                        triangles=tris, ngons=ngons, size_studs=size,
                        materials=sorted({s.material.name for o in meshes for s in o.material_slots if s.material}),
                        ok=(tris == exp_tris and ngons == 0 and
                            all(abs(a - b) < 0.05 for a, b in zip(size, rec["size"])) and
                            sorted(b.name for a in arms for b in a.data.bones) == sorted(rec["bones"])))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.join(OUT, BATCH + ".glb"))
    objs = list(bpy.context.scene.objects)
    mn, mx = [1e9] * 3, [-1e9] * 3
    for o in objs:
        if o.type == "MESH" and o.name.startswith("dryas_tree__Bark"):
            for v in o.data.vertices:
                p = o.matrix_world @ v.co
                for k in range(3):
                    mn[k] = min(mn[k], p[k])
                    mx[k] = max(mx[k], p[k])
    # (the glTF importer adds 'Icosphere' bone-shape helpers of its own; they are not in the file)
    res["_glb"] = dict(meshes=sorted(o.name for o in objs if o.type == "MESH" and not o.name.startswith("Icosphere")),
                       armatures=sorted(o.name for o in objs if o.type == "ARMATURE"),
                       dryas_bark_size=[round(mx[k] - mn[k], 2) for k in range(3)])
    return res


# ======================================================================================
# 10. MAIN
# ======================================================================================
BATCH_A_SHA1 = {                       # geometry hashes delivered in batch A (must not change)
    "burnt_tree": {"Bark": "e6fa94e118b5ef94be61f8bb96aadb370f8c6c73"},
    "far_jura_broad": {"Card": "4af8675d3523692119c8d3ace2ecab74a31ca951"},
    "far_jura_tall": {"Card": "e81ee527d6e82345952baa798e5391bea4d85a2e"},
    "far_jura_young": {"Card": "bf38e2ade6dfbbb2fe108b2b9fdfde56f27786ad"},
    "far_jura_ancient": {"Card": "31e268f7588445725808cf42c43e12181ceb246c"},
    "far_maple": {"Card": "87f09c76394a5e726c86ac51a3986074b56b61a2"},
    "far_pine": {"Card": "3350b6af4549d9e9a9ed87256ea9941e17570ed4"},
}
MAPLES = [  # id, seed, H, W, leaf mix, tiers (z frac, radius frac, pads, pad rh, pad rv), leaders
    ("maple_a", 11, 40.0, 47.0, {"maple_red": 0.84, "maple_orange": 0.16},
     [(0.47, 1.0, 8, 5.6, 1.7), (0.66, 0.80, 7, 5.2, 1.6), (0.85, 0.50, 4, 5.0, 1.6)], 3),
    ("maple_b", 23, 44.0, 51.0, {"maple_orange": 0.78, "maple_red": 0.12, "maple_amber": 0.10},
     [(0.46, 1.0, 9, 5.8, 1.7), (0.65, 0.82, 7, 5.4, 1.7), (0.84, 0.52, 5, 5.0, 1.6)], 2),
    ("maple_c", 37, 40.5, 44.0, {"maple_amber": 0.80, "maple_red": 0.20},
     [(0.53, 1.0, 9, 5.6, 1.7), (0.78, 0.66, 6, 5.2, 1.7)], 3),
]
PINES = [("pine_a", 41, 60.0, 10.6, 8), ("pine_b", 53, 80.0, 13.6, 9)]


def finalize_sizes():
    for mid, r in STATE["models"].items():
        mn, mx = r["bbox_min"], r["bbox_max"]
        r["size"] = [round(mx[0] - mn[0], 2), round(mx[2] - mn[2], 2), round(mx[1] - mn[1], 2)]
        r["bbox_min"] = [round(v, 3) for v in mn]
        r["bbox_max"] = [round(v, 3) for v in mx]


def colours_for(mid):
    r = STATE["models"][mid]
    used = {}
    if r["kind"] == "far_card":
        t = r["info"]["crown_tile"]
        used["crown (%s)" % t] = [round(v) for v in STATE["far_rgb"][t]]
        used["trunk (far_bark = bark)"] = list(PAL["bark"])
        return used
    for t in sorted(r.get("usage", {})):
        used["leaf: " + t] = list(PAL[t])
    tt = r.get("trunk_tile")
    used["bark: " + tt] = list(PAL[tt])
    if mid == "dryas_tree":
        used["roots: bark_light"] = list(PAL["bark_light"])
        used["spirit fruit (material Glow, not in atlas)"] = list(GLOW_RGB)
    if mid == "burnt_tree":
        used["ember cracks"] = list(PAL["ember"])
    return used


def main():
    os.makedirs(REN, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.scale_length = 0.01
    tiles, nrng = paint_atlas_base()                   # batch A tiles, same RNG stream as batch A
    tile_mean_a = dict(STATE["tile_mean"])
    # ---- batch A reference builds (rev 1, never exported). They pin what revision 2 must not
    #      change (far-card boxes and colours, collider boxes, bone positions) and feed the
    #      before/after render.
    ref = {}
    for mid, seed, H, W, mix, tiers, leaders in MAPLES:
        ref[mid] = gen_maple(mid, seed, H, W, mix, tiers, leaders, rev=1)
    for mid, seed, H, R, nt in PINES:
        ref[mid] = gen_pine(mid, seed, H, R, nt, rev=1)
    ref["dryas_tree"] = gen_dryas("dryas_tree", 73, rev=1)
    # ---- revision 2 trees ----
    gens = {}
    for mid, seed, H, W, mix, tiers, leaders in MAPLES:
        gens[mid] = ("maple", gen_maple(mid, seed, H, W, mix, tiers, leaders))
    for mid, seed, H, R, nt in PINES:
        gens[mid] = ("pine", gen_pine(mid, seed, H, R, nt))
    gens["burnt_tree"] = ("dead tree", gen_burnt("burnt_tree", 61, 30.0))
    gens["dryas_tree"] = ("landmark tree", gen_dryas("dryas_tree", 73))

    def avg_usage(usage):
        tot = sum(usage.values())
        return tuple(sum(tile_mean_a[t][k] * n for t, n in usage.items()) / tot for k in range(3))
    far_rgb = {                                        # batch A values: the far cards stay unchanged
        "far_crown_jura": avg_usage({"leaf_green": 1, "leaf_dark": 1, "leaf_yellow_green": 1, "leaf_jade": 1}),
        "far_crown_maple": avg_usage(ref["maple_b"]["usage"]),
        "far_crown_pine": avg_usage(ref["pine_b"]["usage"]),
    }
    STATE["far_rgb"] = far_rgb
    paint_far_tiles(tiles, nrng, {k: tuple(int(round(c)) for c in v) for k, v in far_rgb.items()})
    atlas_a = compose_atlas(tiles)                     # batch A atlas: only for the before/after render
    rev2_tiles, outlines = paint_rev2_tiles()
    for k, im in rev2_tiles.items():
        tiles[k] = im
        STATE["tile_mean"][k] = tile_mean(im)
    STATE["leaf_outline_mean"] = {k: outline_mean(im, outlines[k]) for k, im in rev2_tiles.items()}
    compose_atlas(tiles).save(os.path.join(OUT, "atlas.png"), optimize=True)
    built = {}
    for mid, (kind, data) in gens.items():
        col, arm, objs = make_tree_model(mid, data, kind, ref=ref.get(mid))
        built[mid] = (arm, objs)
        STATE["models"][mid]["facing"] = facing_check([data["bark"], data.get("glow")])     # bark (+fruit)
        if data.get("leaves") is not None:
            STATE["models"][mid]["leaf_check"] = leaf_card_check(data["leaves"], outlines)
            STATE["models"][mid]["_cards"] = data["leaves"].cards
    for mid, (arm, objs) in built.items():
        STATE["models"][mid]["wind_test"] = wind_test(mid, arm, objs)
    # ---- foliage density measurements ----
    cov_imgs = []
    for tag, d in (("rev2", gens["dryas_tree"][1]), ("batch_A", ref["dryas_tree"])):
        res = []
        for az in (270, 0, 90, 180):
            r, img = silhouette_coverage(d["leaves"], [d["bark"], d["glow"]], az)
            res.append(r)
            if tag == "rev2":
                cov_imgs.append((r, img))
        STATE["models"]["dryas_tree"].setdefault("leaf_coverage", {})[tag] = res
    for mid in ("maple_a", "maple_b", "maple_c"):
        STATE["models"][mid]["pad_coverage"] = {
            "rev2": pad_coverage(gens[mid][1]), "batch_A": pad_coverage(ref[mid])}
    # ---- far cards (boxes, crown bottoms and colours of batch A -> unchanged geometry) ----
    for fid, (label, (sx, sh, sz)) in EXISTING.items():
        shape = fid.split("_")[-1]
        far = gen_far(fid, shape, (-sx / 2, -sz / 2, 0.0), (sx / 2, sz / 2, sh), "far_crown_jura", hash_seed(fid))
        make_far_model(fid, far, "far_crown_jura", "existing Jura tree '%s' %gx%gx%g" % (label, sx, sh, sz))
    for fid, src, shape, tile in (("far_maple", "maple_b", "maple", "far_crown_maple"),
                                  ("far_pine", "pine_b", "pine", "far_crown_pine")):
        rmn, rmx = geo_bbox(ref[src]["bark"], ref[src]["leaves"])
        lmn, lmx = geo_bbox(ref[src]["leaves"])
        cb = (lmn[2] - rmn[2]) / (rmx[2] - rmn[2])
        far = gen_far(fid, shape, rmn, rmx, tile, hash_seed(fid), crown_bottom=cb)
        make_far_model(fid, far, tile, "%s (exact bbox of the batch A full tree)" % src)
    STATE["checks"]["unchanged_since_batch_A"] = {
        mid: all(STATE["models"][mid]["geometry_sha1"][k] == v for k, v in hs.items())
        for mid, hs in BATCH_A_SHA1.items()}
    finalize_sizes()
    # ---- budgets ----
    problems = []
    for mid, r in STATE["models"].items():
        t = r["triangles"]
        if r["kind"] == "far_card":
            lim = BUDGET_FAR
        elif mid == "dryas_tree":
            lim = BUDGET_DRYAS
        else:
            lim = BUDGET_TREE
            if t["Bark"] > BUDGET_BARK:
                problems.append("%s bark %d > %d" % (mid, t["Bark"], BUDGET_BARK))
        r["budget"] = lim
        if t["total"] > lim:
            problems.append("%s total %d > %d" % (mid, t["total"], lim))
    STATE["checks"]["budget_problems"] = problems
    print("BUDGET PROBLEMS:", problems)
    # ---- structural self-checks ----
    ident = [o.name for o in bpy.data.objects if any(abs(o.matrix_world[i][j] - (1.0 if i == j else 0.0)) > 1e-6
                                                    for i in range(4) for j in range(4))]
    sink = {mid: r["bbox_min"][2] for mid, r in STATE["models"].items()}
    STATE["checks"]["objects_with_unapplied_transforms"] = ident
    STATE["checks"]["lowest_point_per_model"] = sink
    STATE["checks"]["sink_ok_(-1..0)"] = all(-1.0 - 1e-6 <= z <= 0.0 for z in sink.values())
    STATE["checks"]["modifiers_other_than_armature"] = [o.name for o in bpy.data.objects
                                                         for m in o.modifiers if m.type != "ARMATURE"]
    from mathutils.kdtree import KDTree
    lv = gens["dryas_tree"][1]["leaves"]
    kd = KDTree(len(lv.co))
    for i, p in enumerate(lv.co):
        kd.insert(p, i)
    kd.balance()
    gl = gens["dryas_tree"][1]["glow"]
    fruit_gap = []
    for f in range(len(gl.co) // 26):
        c = Vector([sum(gl.co[f * 26 + k][j] for k in range(26)) / 26 for j in range(3)])
        fruit_gap.append(round(kd.find(c)[2], 2))
    STATE["checks"]["dryas_fruit_to_nearest_leaf_vertex"] = fruit_gap
    STATE["checks"]["pine_bare_trunk_fraction"] = {m: STATE["models"][m]["info"]["bare_trunk_fraction"]
                                                   for m in ("pine_a", "pine_b")}
    if ident or not STATE["checks"]["sink_ok_(-1..0)"] or max(fruit_gap) > 4.0 or \
            min(STATE["checks"]["pine_bare_trunk_fraction"].values()) < 0.195:
        raise RuntimeError("structural self-check failed: %s" % STATE["checks"])
    # ---- export ----
    ids = list(STATE["models"].keys())
    STATE["checks"]["fbx_determinism_patch"] = make_fbx_deterministic()
    for mid in ids:
        export_fbx(mid)
    export_glb()
    bpy.context.preferences.filepaths.save_version = 0            # no .blend1 backup next to the deliverable
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, BATCH + ".blend"), relative_remap=True, compress=True)
    # ---- renders ----
    renders = {}
    if OPT_RENDER:
        cam, hum, hcol = render_scene_setup()
        add_render_backfaces(ids)
        renders["dryas_leaf_coverage"] = coverage_figure(cov_imgs, STATE["models"]["dryas_tree"]["leaf_coverage"]["batch_A"])
        for mid in ids:
            renders[mid] = render_model_views(mid, cam, hum)
        extra = backdrop(hcol)
        for o in extra:
            o.hide_render = True
        renders["dryas_arch_walkthrough"] = render_arch_walkthrough(cam, hum)
        render_lineup(os.path.join(REN, "lineup.png"),
                      [["dryas_tree", "pine_b", "pine_a", "burnt_tree"],
                       ["maple_a", "maple_b", "maple_c", "far_maple", "far_pine"],
                       ["far_jura_broad", "far_jura_tall", "far_jura_young", "far_jura_ancient"]],
                      cam, hum, "Batch A (revision 2) lineup - all models (front, orthographic)")
        render_lineup(os.path.join(REN, "forest_lineup.png"),
                      [["far_jura_broad", "far_jura_tall", "far_jura_young", "far_jura_ancient"],
                       ["dryas_tree", "pine_b", "pine_a"],
                       ["maple_a", "maple_b", "maple_c", "burnt_tree"]],
                      cam, hum, "Forest lineup - far cards of the 4 existing trees + the new trees")
        # stand-ins of the existing trees (NOT delivered) for the far-card comparison
        refs = {"far_jura_broad": (0.62, (27, 20), 0.30, 18, 1.7, 4),
                "far_jura_tall": (0.60, (18, 28), 0.26, 16, 1.5, 3),
                "far_jura_young": (0.64, (17.5, 12.5), 0.36, 12, 1.0, 3),
                "far_jura_ancient": (0.62, (36, 22), 0.30, 22, 3.0, 4)}
        for fid, (label, (sx, sh, sz)) in EXISTING.items():
            cc, cr, split, ncl, rb, limbs = refs[fid]
            rid = "_ref_" + fid[4:]
            d = gen_broadleaf_ref(rid, hash_seed(rid), sx, sh, sz, cc, cr, split, ncl, rb, limbs)
            mn, mx = geo_bbox(d["bark"], d["leaves"])
            for g in (d["bark"], d["leaves"]):       # stretch the stand-in to the exact known size
                g.co = [((p[0] - (mn[0] + mx[0]) / 2) * sx / (mx[0] - mn[0]),
                         (p[1] - (mn[1] + mx[1]) / 2) * sz / (mx[1] - mn[1]),
                         p[2] * sh / mx[2]) for p in g.co]
            col = new_collection(rid)
            mesh_from_geo(rid + "__Bark", d["bark"], get_atlas_material(), col)
            flipped_copy(mesh_from_geo(rid + "__Leaves", d["leaves"], get_atlas_material(), col), col)
            STATE["models"][rid] = dict(kind="reference", bbox_min=[-sx / 2, -sz / 2, -0.8],
                                        bbox_max=[sx / 2, sz / 2, sh], size=[sx, sh, sz])
            renders.setdefault("far_compare", []).append(render_far_compare(fid, rid, cam, hcol, extra))
        for fid, src in (("far_maple", "maple_b"), ("far_pine", "pine_b")):
            renders.setdefault("far_compare", []).append(render_far_compare(fid, src, cam, hcol, extra))
        for k in [k for k in STATE["models"] if k.startswith("_ref_")]:
            del STATE["models"][k]
        renders["before_after_leaves"] = render_before_after(cam, hum, ref, atlas_a)
    # ---- verify exports ----
    verify = verify_exports(ids) if OPT_VERIFY else {}
    write_reports(ids, renders, verify)
    if OPT_ZIP:
        make_zip()
    print("DONE")


def hash_seed(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


def write_reports(ids, renders, verify):
    rep = {"batch": "A", "revision": 2, "generator": "build.py", "blender": bpy.app.version_string,
           "units": "1 Blender unit = 1 stud; scene.unit_settings.scale_length = 0.01; Z up; front = -Y",
           "size_convention": "[x, height (Blender Z), depth (Blender Y)] in studs, from the mesh bounding box",
           "atlas": {"file": "atlas.png", "size": [ATLAS_PX, ATLAS_PX], "tile_px": TILE_PX, "inset": INSET,
                     "tiles": {k: {"col": c, "row_from_top": r, "kind": TILE_KIND[k],
                                   "base_rgb": list(PAL[k]) if k in PAL else
                                   ([int(round(v)) for v in STATE["far_rgb"][k]] if k in STATE["far_rgb"]
                                    else list(PAL["bark"])),
                                   "mean_rgb": [round(v, 1) for v in STATE["tile_mean"][k]],
                                   **({"leaf_outline_mean_rgb": [round(v, 1) for v in STATE["leaf_outline_mean"][k]]}
                                      if k in STATE.get("leaf_outline_mean", {}) else {})}
                               for k, (c, r) in TILES.items()}},
           "models": {}, "checks": STATE["checks"], "export_verification": verify, "renders": renders}
    for mid in ids:
        r = STATE["models"][mid]
        m = {"size": r["size"], "bbox_min": r["bbox_min"], "bbox_max": r["bbox_max"], "kind": r["kind"],
             "triangles": r["triangles"], "budget": r["budget"], "objects": r["objects"],
             "armature": r["armature"], "bones": r["bones"], "colours": colours_for(mid),
             "info": r["info"], "geometry_sha1": r["geometry_sha1"]}
        if r.get("usage"):
            m["leaf_cards_per_tile"] = r["usage"]
        for key in ("leaf_check", "leaf_coverage", "pad_coverage"):
            if r.get(key):
                m[key] = r[key]
        if r.get("size_batch_A"):
            m["size_batch_A"] = [round(x, 2) for x in r["size_batch_A"]]
            m["size_change_pct_vs_batch_A"] = [round(100.0 * (a / b - 1.0), 2) for a, b in zip(r["size"], r["size_batch_A"])]
        if r.get("wind_test"):
            m["wind_test_max_move_studs"] = r["wind_test"]
        if r.get("facing"):
            m["facing_check"] = r["facing"]
        rep["models"][mid] = m
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(rep, f, indent=1)
    with open(os.path.join(OUT, "colliders.json"), "w") as f:
        json.dump({k: STATE["colliders"][k] for k in ids}, f, indent=1)


def make_zip():
    zp = os.path.join(os.path.dirname(OUT), ZIP_NAME)
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(OUT):
            for fn in sorted(files):
                if fn.endswith((".blend1", ".pyc")) or "__pycache__" in root:
                    continue
                p = os.path.join(root, fn)
                z.write(p, os.path.join(BATCH, os.path.relpath(p, OUT)))
    return zp


if __name__ == "__main__":
    main()
