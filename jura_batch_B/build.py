#!/usr/bin/env python3
"""
Great Forest of Jura -- BATCH B -- Khusha mountains, home of the Tengu -- one re-runnable generator.

Builds every prop of batch B procedurally (no manual steps): paints the shared texture
atlas, models the six props, runs the self-checks, exports FBX / GLB / .blend, renders
the review images (5 views per model, lineup, path test scene), re-imports the exports
to verify them and writes report.json + colliders.json.

Run (either works):
    blender -b -P build.py [-- options]      Blender 4.x / 5.x (needs Pillow in Blender's Python)
    python3 build.py [options]               with `pip install bpy pillow` (bpy 5.2 / Python 3.13)

Options:
    --no-render   skip the PNG renders
    --no-verify   skip re-importing the exported files
    --no-zip      do not (re)create ../jura_batch_B.zip

All outputs are written next to this file (the jura_batch_B/ folder).
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
from PIL import Image, ImageDraw, ImageFont

import bpy
import bmesh
from mathutils import Vector, Matrix
from mathutils import geometry as mgeo

# --------------------------------------------------------------------------------------
# paths / options
# --------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = HERE
REN = os.path.join(OUT, "renders")
BATCH = "jura_batch_B"
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
OPT_RENDER = "--no-render" not in _argv
OPT_VERIFY = "--no-verify" not in _argv
OPT_ZIP = "--no-zip" not in _argv
OPT_ONLY = [a.split("=", 1)[1] for a in _argv if a.startswith("--only=")]   # dev: build a subset

# --------------------------------------------------------------------------------------
# palette (sRGB 0-255) -- exact values from the shared spec (+ the lantern glow of this batch)
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
    "lantern_glow": (255, 190, 110),     # given by the toro_lantern item
}

# 4x4 atlas, 256 px tiles. (col, row) with row 0 = TOP row of the PNG.
TILES = {
    "stone_grey": (0, 0), "stone_warm": (1, 0), "moss": (2, 0), "plaster": (3, 0),
    "lacquer": (0, 1), "iron": (1, 1), "roof_tile": (2, 1), "gold": (3, 1),
    "wood_dark": (0, 2), "wood": (1, 2), "wood_light": (2, 2), "cut_wood": (3, 2),
    "rope": (0, 3), "cloth": (1, 3), "glow": (2, 3), "maple_red": (3, 3),
}
TILE_PAL = {   # palette entry painted into each tile
    "stone_grey": "rock_grey", "stone_warm": "dwarf_stone", "moss": "moss", "plaster": "limestone",
    "lacquer": "vermilion", "iron": "dark_iron", "roof_tile": "dark_iron", "gold": "gold_trim",
    "wood_dark": "bark_dark", "wood": "bark", "wood_light": "bark_light", "cut_wood": "cream",
    "rope": "straw", "cloth": "cream", "glow": "lantern_glow", "maple_red": "maple_red",
}
TILE_KIND = {
    "stone_grey": "stone (soft round facet blotches)", "stone_warm": "stone (soft round facet blotches)",
    "moss": "mossy stone (moss clumps over rock grey)", "plaster": "flat", "lacquer": "flat", "iron": "flat",
    "roof_tile": "roof tiles (kawara courses + round-tile columns)", "gold": "flat",
    "wood_dark": "wood planks (vertical seams + grain)", "wood": "timber grain", "wood_light": "timber grain",
    "cut_wood": "pale cut wood / hinoki (timber grain)", "rope": "twisted straw rope (3 strands)", "cloth": "flat",
    "glow": "flat", "maple_red": "flat (fallen momiji leaves)",
}
ATLAS_PX = 1024
TILE_PX = 256
INSET = 0.04

BUDGET = {"torii": 1500, "toro_lantern": 900, "stone_stairs": 1200, "tengu_gate": 6000,
          "roadside_shrine": 800, "tengu_banner": 300}
BUDGET_CLASS = {"torii": "item limit (prop)", "toro_lantern": "item limit", "stone_stairs": "item limit",
                "tengu_gate": "item limit (landmark/gate)", "roadside_shrine": "item limit",
                "tengu_banner": "item limit"}
MODEL_IDS = ["torii", "toro_lantern", "stone_stairs", "tengu_gate", "roadside_shrine", "tengu_banner"]

STATE = {"tile_mean": {}, "models": {}, "checks": {}, "colliders": {}}


def hash_seed(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


# ======================================================================================
# 1. TEXTURE ATLAS (PIL + numpy, painted at 4x and downsampled for clean edges)
#    Patterned tiles are periodic in u and v, so faces mapped with world-anchored UVs
#    continue the pattern across face borders.
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


def _pnoise(rng, cells, amp):
    """Smooth periodic noise in [-amp, amp] (tiles seamlessly)."""
    g = rng.uniform(-1, 1, (cells, cells)).astype(np.float32)
    big = np.tile(g, (3, 3))
    im = Image.fromarray(big, "F").resize((3 * NS, 3 * NS), Image.BICUBIC)
    a = np.asarray(im, np.float32)[NS:2 * NS, NS:2 * NS]
    return np.clip(a, -1.0, 1.0) * amp


def _pdist(a, c):
    d = np.abs(a - c)
    return np.minimum(d, 1.0 - d)


def paint_flat(rgb, rng):
    n = _pnoise(rng, 10, 0.022) + rng.uniform(-0.008, 0.008, (NS, NS)).astype(np.float32)
    return _finish(_c(rgb) * (1.0 + n)[..., None])


def paint_stone(base, rng):
    """Soft round blotches of lighter / darker stone (facet colour variation) + fine grit."""
    x, y = _grid()
    b = _c(base)
    v = 1.0 + _pnoise(rng, 5, 0.04)
    tint = np.zeros((NS, NS, 3), np.float32)
    for _ in range(44):
        cx, cy = rng.uniform(0, 1), rng.uniform(0, 1)
        r = rng.uniform(0.07, 0.21)
        a = rng.uniform(-0.12, 0.12)
        d2 = (_pdist(x, cx) ** 2 + _pdist(y, cy) ** 2) / (r * r)
        blob = np.exp(-d2 * 1.8)
        v += a * blob
        tint += (blob * rng.uniform(-0.025, 0.025))[..., None] * np.array([1.0, 0.2, -0.8], np.float32)
    v += _pnoise(rng, 64, 0.025)
    img = b * v[..., None] * (1.0 + tint)
    return _finish(img)


def paint_moss(base, stone, rng):
    """Mossy stone: rock-grey stone partly covered by soft moss clumps (about 60 %)."""
    x, y = _grid()
    st = np.asarray(paint_stone(stone, rng).resize((NS, NS), Image.BICUBIC), np.float32)
    cover = _pnoise(rng, 5, 1.0) * 0.8 + _pnoise(rng, 14, 0.45)
    for _ in range(26):
        cx, cy = rng.uniform(0, 1), rng.uniform(0, 1)
        r = rng.uniform(0.06, 0.16)
        d2 = (_pdist(x, cx) ** 2 + _pdist(y, cy) ** 2) / (r * r)
        cover += 0.5 * np.exp(-d2 * 2.0)
    m = np.clip((cover + 0.05) * 3.0, 0.0, 1.0)[..., None]
    v = 1.0 + _pnoise(rng, 40, 0.07) + _pnoise(rng, 8, 0.05)
    moss = _c(base) * v[..., None]
    edge = np.clip(1.0 - np.abs(m - 0.5) * 2.0, 0, 1)            # darker rim where moss meets stone
    img = st * (1 - m) + moss * m
    img *= (1.0 - 0.12 * edge)
    return _finish(img)


def paint_planks(base, rng, n=4):
    """Vertical planks (dark seams between them) with simple wavy grain; periodic."""
    x, y = _grid()
    b = _c(base)
    f = (x * n + 0.5) % 1.0
    k = np.floor(x * n + 0.5).astype(np.int32) % n
    shade = np.array([rng.uniform(0.92, 1.07) for _ in range(n)], np.float32)[k]
    ph = np.array([rng.uniform(0, 1) for _ in range(n)], np.float32)[k]
    grain = np.sin(2 * np.pi * (5 * n * x + 0.22 * np.sin(2 * np.pi * (2 * y + ph)) + 0.06 * np.sin(2 * np.pi * (5 * y + 2 * ph))))
    v = shade * (1.0 - 0.07 * (grain > 0.55) - 0.035 * (grain < -0.75)) + _pnoise(rng, 8, 0.03)
    seam = np.minimum(f, 1.0 - f) / n
    v = np.where(seam < 0.0045, v * 0.52, np.where(seam < 0.009, v * 0.82, v))
    hi = (np.abs(seam - 0.0115) < 0.0022)                # thin highlight next to each seam
    v = np.where(hi, v * 1.08, v)
    return _finish(b * v[..., None])


def paint_timber(base, rng):
    x, y = _grid()
    b = _c(base)
    g = np.sin(2 * np.pi * (11 * x + 0.12 * np.sin(2 * np.pi * (2 * y + 0.3)) + 0.04 * np.sin(2 * np.pi * (7 * y))))
    g2 = np.sin(2 * np.pi * (27 * x + 0.3 * np.sin(2 * np.pi * (3 * y + 0.7))))
    v = 1.0 - 0.08 * (g > 0.6) - 0.03 * (g2 > 0.8) + _pnoise(rng, 6, 0.04) + _pnoise(rng, 40, 0.015)
    return _finish(b * v[..., None])


def paint_rope(base, rng):
    """Three twisted strands; the pattern is periodic (u around the rope, v along it)."""
    x, y = _grid()
    u, v = x, 1.0 - y
    s = (3 * u + 2 * v) % 1.0
    prof = np.sin(np.pi * s) ** 0.7
    fib = np.sin(2 * np.pi * (24 * u - 10 * v)) * 0.5 + 0.5
    val = 0.58 + 0.48 * prof - 0.06 * fib * prof + _pnoise(rng, 8, 0.03)
    val = np.where(s < 0.045, val * 0.75, val)
    return _finish(_c(base) * val[..., None])


def paint_roof(base, rng):
    """Kawara: 6 courses per tile (v), 4 round-tile columns per tile (u); periodic."""
    x, y = _grid()
    u, v = x, 1.0 - y
    t = (v * 6.0) % 1.0                                 # 0 at the lower lip of a course, 1 at its top
    col = np.sin(np.pi * ((u * 4.0) % 1.0)) ** 0.6       # rounded column (lighter crown)
    val = 0.80 + 0.22 * (1.0 - t) * 0.6 + 0.20 * col
    val = np.where(t > 0.92, val * 0.62, val)            # shadow under the next course's lip
    val = np.where(t < 0.035, val * 1.12, val)           # bright lip edge
    val += _pnoise(rng, 12, 0.03)
    return _finish(_c(base) * val[..., None])


def tile_mean(img):
    a = np.asarray(img, np.float32)
    i0, i1 = int(TILE_PX * INSET), int(TILE_PX * (1 - INSET))
    return tuple(float(v) for v in a[i0:i1, i0:i1].reshape(-1, 3).mean(0))


def paint_atlas():
    rng = np.random.RandomState(4242)
    tiles = {}
    for k in TILES:
        rgb = PAL[TILE_PAL[k]]
        if k in ("stone_grey", "stone_warm"):
            tiles[k] = paint_stone(rgb, rng)
        elif k == "moss":
            tiles[k] = paint_moss(rgb, PAL["rock_grey"], rng)
        elif k == "wood_dark":
            tiles[k] = paint_planks(rgb, rng)
        elif k in ("wood", "wood_light", "cut_wood"):
            tiles[k] = paint_timber(rgb, rng)
        elif k == "rope":
            tiles[k] = paint_rope(rgb, rng)
        elif k == "roof_tile":
            tiles[k] = paint_roof(rgb, rng)
        else:
            tiles[k] = paint_flat(rgb, rng)
        STATE["tile_mean"][k] = tile_mean(tiles[k])
    atlas = Image.new("RGB", (ATLAS_PX, ATLAS_PX), (0, 0, 0))
    for k, (c, r) in TILES.items():
        atlas.paste(tiles[k], (c * TILE_PX, r * TILE_PX))
    path = os.path.join(OUT, "atlas.png")
    atlas.save(path, optimize=True)
    return path


def tile_uv(tile, u, v):
    """Tile-local (u, v) in [0,1] (v=0 bottom) -> atlas UV inside the 4 % inset."""
    c, r = TILES[tile]
    u = min(max(u, 0.0), 1.0)
    v = min(max(v, 0.0), 1.0)
    return ((c + INSET + (1 - 2 * INSET) * u) / 4.0,
            (3 - r + INSET + (1 - 2 * INSET) * v) / 4.0)


# ======================================================================================
# 2. GEOMETRY CORE
#    Geo = triangle soup with per-corner atlas UVs, per-face tile + smooth flag, and
#    "pieces" (one per primitive) used by the floating-part check.
#    Every primitive winds its faces counter-clockwise seen from outside.
# ======================================================================================
Z = Vector((0.0, 0.0, 1.0))


def any_perp(v):
    a = Vector((1.0, 0.0, 0.0)) if abs(v.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return (a - v * a.dot(v)).normalized()


class Geo:
    def __init__(self):
        self.co, self.tris, self.uv, self.smooth, self.tile = [], [], [], [], []
        self.pieces = []

    def mark(self, label):
        self.pieces.append((len(self.tris), label))

    def v(self, p):
        self.co.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.co) - 1

    def add_tri(self, a, b, c, ua, ub, uc, tile, smooth):
        self.tris.append((a, b, c))
        self.uv.append((ua, ub, uc))
        self.tile.append(tile)
        self.smooth.append(smooth)

    def ntris(self):
        return len(self.tris)

    def piece_ranges(self):
        marks = list(self.pieces)
        if not marks or marks[0][0] > 0:
            marks.insert(0, (0, "misc"))
        out = []
        for i, (s, lab) in enumerate(marks):
            e = marks[i + 1][0] if i + 1 < len(marks) else len(self.tris)
            if e > s:
                out.append((s, e, lab))
        return out


class P:
    """How faces are painted: atlas tile, texture scale S (studs per tile, u/v), optional
    grain axis (v follows it), optional rnd (random.Random: random window inside the
    tile -> per-facet colour variation on stone), smooth shading, and fn(normal, centre)
    returning a per-face tile override (moss on top faces, painted ends...)."""

    def __init__(self, tile, S=4.0, grain=None, rnd=None, smooth=False, fn=None):
        self.tile = tile
        self.S = tuple(S) if isinstance(S, (tuple, list)) else (S, S)
        self.grain = Vector(grain) if grain is not None else None
        self.rnd = rnd
        self.smooth = smooth
        self.fn = fn

    def but(self, **kw):
        q = P(self.tile, self.S, self.grain, self.rnd, self.smooth, self.fn)
        for k, v in kw.items():
            if k == "S":
                v = tuple(v) if isinstance(v, (tuple, list)) else (v, v)
            if k == "grain" and v is not None:
                v = Vector(v)
            setattr(q, k, v)
        return q


def poly_normal(pts):
    n = Vector((0.0, 0.0, 0.0))
    m = len(pts)
    for i in range(m):
        a, b = pts[i], pts[(i + 1) % m]
        n.x += (a.y - b.y) * (a.z + b.z)
        n.y += (a.z - b.z) * (a.x + b.x)
        n.z += (a.x - b.x) * (a.y + b.y)
    return n.normalized() if n.length > 1e-12 else Vector((0.0, 0.0, 1.0))


def planar_uv(pts, n, S, grain=None, rnd=None):
    """Project the face on its own plane (v along the grain / world up / world +Y) and fit
    it into one tile: world-anchored (pattern continues across faces) or, with rnd, at a
    random spot. Faces larger than a tile are scaled down uniformly (never stretched)."""
    up = None
    if grain is not None:
        g = grain - n * grain.dot(n)
        if g.length > 0.35:
            up = g.normalized()
    if up is None:
        g = Z - n * n.z
        if g.length > 0.35:
            up = g.normalized()
        else:
            yv = Vector((0.0, 1.0, 0.0))
            up = (yv - n * n.y).normalized()
    right = up.cross(n)
    su, sv = S
    us = [p.dot(right) / su for p in pts]
    vs = [p.dot(up) / sv for p in pts]
    u0, v0 = min(us), min(vs)
    eu, ev = max(us) - u0, max(vs) - v0
    f = 1.0 / max(1.0, eu / 0.999, ev / 0.999)
    eu, ev = eu * f, ev * f
    if rnd is not None:
        ou, ov = rnd.uniform(0.0, 1.0 - eu), rnd.uniform(0.0, 1.0 - ev)
    else:
        ou, ov = min(u0 % 1.0, 1.0 - eu), min(v0 % 1.0, 1.0 - ev)
    return [((u - u0) * f + ou, (v - v0) * f + ov) for u, v in zip(us, vs)]


def _tess(pts, n):
    a = any_perp(n)
    b = n.cross(a)
    tl = mgeo.tessellate_polygon([[(p.dot(a), p.dot(b), 0.0) for p in pts]])
    out = []
    for i, j, k in tl:
        if (pts[j] - pts[i]).cross(pts[k] - pts[i]).dot(n) < 0:
            j, k = k, j
        out.append((i, j, k))
    return out


def face(g, idx, p, uvs=None, tile=None):
    pts = [Vector(g.co[i]) for i in idx]
    n = poly_normal(pts)
    t = tile or p.tile
    if p.fn is not None and tile is None:
        o = p.fn(n, sum(pts, Vector()) / len(pts))
        if o:
            t = o
    if uvs is None:
        uvs = planar_uv(pts, n, p.S, p.grain, p.rnd)
    auv = [tile_uv(t, u, v) for u, v in uvs]
    m = len(idx)
    if m == 3:
        tl = [(0, 1, 2)]
    elif m == 4:
        tl = [(0, 1, 2), (0, 2, 3)] if (pts[0] - pts[2]).length <= (pts[1] - pts[3]).length else [(0, 1, 3), (1, 2, 3)]
    else:
        tl = _tess(pts, n)
    for a, b, c in tl:
        g.add_tri(idx[a], idx[b], idx[c], auv[a], auv[b], auv[c], t, p.smooth)


HEXA_FACES = {"bottom": (0, 3, 2, 1), "top": (4, 5, 6, 7), "front": (0, 1, 5, 4),
              "right": (1, 2, 6, 5), "back": (2, 3, 7, 6), "left": (3, 0, 4, 7)}


def hexa(g, c, p, skip=(), fp=None, label="hexa"):
    """Box from 8 corners: bottom ring (-x-y, +x-y, +x+y, -x+y) then the top ring."""
    g.mark(label)
    idx = [g.v(q) for q in c]
    for name, f in HEXA_FACES.items():
        if name in skip:
            continue
        face(g, [idx[k] for k in f], (fp or {}).get(name, p))
    return idx


def box_corners(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    return [Vector(v) for v in ((x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                                (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))]


def block(g, lo, hi, p, skip=(), fp=None, label="block"):
    return hexa(g, box_corners(lo, hi), p, skip, fp, label)


def chamfer_hexa(g, c, ch, p, skip=("bottom",), jit=None, label="stone"):
    """Stone block with chamfered top edges, from 8 corners (top ring may be sloped).
    jit(point) -> offset applied to the mid and top rings (irregular stones)."""
    g.mark(label)
    b = c[:4]
    t = c[4:]
    m = [q - Z * ch for q in t]
    ti = []
    for k in range(4):
        e1 = (t[(k - 1) % 4] - t[k]).normalized()
        e2 = (t[(k + 1) % 4] - t[k]).normalized()
        ti.append(t[k] + (e1 + e2) * ch)
    if jit:
        m = [q + jit(q) for q in m]
        ti = [q + jit(q) for q in ti]
    B = [g.v(q) for q in b]
    M = [g.v(q) for q in m]
    T = [g.v(q) for q in ti]
    if "bottom" not in skip:
        face(g, [B[0], B[3], B[2], B[1]], p)
    for k in range(4):
        k1 = (k + 1) % 4
        face(g, [B[k], B[k1], M[k1], M[k]], p)
        face(g, [M[k], M[k1], T[k1], T[k]], p)
    face(g, [T[0], T[1], T[2], T[3]], p)
    return B, M, T


def loft(g, rings, p, cap0=True, cap1=True, cap_p=None, label="loft", band_p=None):
    """Connect rings (lists of points, counter-clockwise about the direction of travel;
    a single Vector = apex). band_p(i) may return a different paint for band i."""
    g.mark(label)
    R = []
    for r in rings:
        R.append(g.v(r) if isinstance(r, Vector) else [g.v(q) for q in r])
    for i in range(len(R) - 1):
        a, b = R[i], R[i + 1]
        pp = band_p(i) if band_p else p
        if isinstance(a, int):
            m = len(b)
            for j in range(m):
                face(g, [a, b[(j + 1) % m], b[j]], pp)
        elif isinstance(b, int):
            m = len(a)
            for j in range(m):
                face(g, [a[j], a[(j + 1) % m], b], pp)
        else:
            m = len(a)
            for j in range(m):
                face(g, [a[j], a[(j + 1) % m], b[(j + 1) % m], b[j]], pp)
    cp = (cap_p or p).but(smooth=False)
    if cap0 and not isinstance(R[0], int):
        face(g, list(reversed(R[0])), cp)
    if cap1 and not isinstance(R[-1], int):
        face(g, list(R[-1]), cp)
    return R


def ring_xy(c, r, sides, z, phase=0.0, sx=1.0, sy=1.0, jit=None):
    out = []
    for j in range(sides):
        th = phase + 2.0 * math.pi * j / sides
        q = Vector((c[0] + r * math.cos(th) * sx, c[1] + r * math.sin(th) * sy, z))
        if jit:
            q += jit(q)
        out.append(q)
    return out


def lathe(g, profile, sides, p, c=(0.0, 0.0, 0.0), phase=0.0, cap0=True, cap1=True, jit=None,
          sx=1.0, sy=1.0, label="lathe", band_p=None):
    """Surface of revolution about the vertical axis through c; profile = [(r, z)], r=0 -> apex."""
    rings = []
    for r, z in profile:
        if r <= 1e-9:
            rings.append(Vector((c[0], c[1], c[2] + z)))
        else:
            rings.append(ring_xy(c, r, sides, c[2] + z, phase, sx, sy, jit))
    return loft(g, rings, p, cap0, cap1, label=label, band_p=band_p)


def frame_along(a, b, up):
    T = (b - a).normalized()
    N = up - T * up.dot(T)
    N = N.normalized() if N.length > 1e-6 else any_perp(T)
    return T, N, T.cross(N)


def rect_ring(c, N, B, h, w):
    """Rectangle (height h along N, width w along B) counter-clockwise about T = N x B."""
    return [c + N * (-h / 2) + B * (-w / 2), c + N * (h / 2) + B * (-w / 2),
            c + N * (h / 2) + B * (w / 2), c + N * (-h / 2) + B * (w / 2)]


def beam(g, a, b, w, h, p, up=Z, cap0=True, cap1=True, cap_p=None, label="beam"):
    """Rectangular beam from a to b: width w (horizontal), height h (along `up`)."""
    a, b = Vector(a), Vector(b)
    T, N, B = frame_along(a, b, Vector(up))
    return loft(g, [rect_ring(a, N, B, h, w), rect_ring(b, N, B, h, w)], p, cap0, cap1, cap_p, label)


def sweep(g, pts, sections, p, ups=None, cap0=True, cap1=True, cap_p=None, label="sweep"):
    """Rectangular section (h, w) swept along a polyline; the section stays upright."""
    rings = []
    n = len(pts)
    for i in range(n):
        d = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        T = d.normalized()
        up = ups[i] if ups else Z
        N = (up - T * up.dot(T)).normalized()
        B = T.cross(N)
        h, w = sections[i]
        rings.append(rect_ring(pts[i], N, B, h, w))
    return loft(g, rings, p, cap0, cap1, cap_p, label)


def tube(g, pts, radii, sides, p, h_ref=3.0, cap0=False, cap1=False, phase=0.0, label="tube"):
    """Round tube with tube UVs: u around (one tile = circumference), v along the length
    (one tile = h_ref studs); periodic tiles keep the pattern continuous."""
    g.mark(label)
    n = len(pts)
    s = [0.0]
    for i in range(1, n):
        s.append(s[-1] + (pts[i] - pts[i - 1]).length)
    T = [(pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized() for i in range(n)]
    N = [any_perp(T[0])]
    for i in range(1, n):
        q = N[-1] - T[i] * N[-1].dot(T[i])
        N.append(q.normalized() if q.length > 1e-6 else any_perp(T[i]))
    rings = []
    for i in range(n):
        B = T[i].cross(N[i])
        rings.append([g.v(pts[i] + (N[i] * math.cos(phase + 2 * math.pi * j / sides) +
                                     B * math.sin(phase + 2 * math.pi * j / sides)) * radii[i])
                      for j in range(sides)])
    for i in range(n - 1):
        v0 = (s[i] / h_ref) % 1.0
        dv = (s[i + 1] - s[i]) / h_ref
        if v0 + dv > 1.0:
            v0 = max(0.0, 1.0 - dv)
        v1 = min(1.0, v0 + dv)
        for j in range(sides):
            u0, u1 = j / sides, (j + 1) / sides
            face(g, [rings[i][j], rings[i][(j + 1) % sides], rings[i + 1][(j + 1) % sides], rings[i + 1][j]], p,
                 uvs=[(u0, v0), (u1, v0), (u1, v1), (u0, v1)])
    cp = p.but(smooth=False)
    if cap0:
        face(g, list(reversed(rings[0])), cp)
    if cap1:
        face(g, list(rings[-1]), cp)
    return rings


def ccw2(poly):
    a = 0.0
    for i in range(len(poly)):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % len(poly)]
        a += x0 * y1 - x1 * y0
    return poly if a > 0 else list(reversed(poly))


def extrude_poly(g, poly, o, U, Vv, depth, p, p_back=None, p_side=None, back=True, label="extrude"):
    """2D polygon (u, v) placed at o + u*U + v*V, front face normal U x V, extruded back by depth."""
    g.mark(label)
    poly = ccw2(poly)
    o, U, Vv = Vector(o), Vector(U), Vector(Vv)
    N = U.cross(Vv).normalized()
    F = [g.v(o + U * u + Vv * v) for u, v in poly]
    Bk = [g.v(o + U * u + Vv * v - N * depth) for u, v in poly]
    face(g, F, p)
    if back:
        face(g, list(reversed(Bk)), p_back or p)
    m = len(poly)
    for k in range(m):
        k1 = (k + 1) % m
        face(g, [F[k1], F[k], Bk[k], Bk[k1]], p_side or p)
    return F, Bk


def slab_grid(g, o, U, Vv, nu, nv, depth, p, p_back=None, p_side=None, back=True, label="slab"):
    """Thin box whose front/back faces are nu x nv grids (keeps texel density on big faces
    without T-junctions). Front normal = U x V; extruded back by depth."""
    g.mark(label)
    o, U, Vv = Vector(o), Vector(U), Vector(Vv)
    N = U.cross(Vv).normalized()
    F = [[g.v(o + U * (i / nu) + Vv * (j / nv)) for j in range(nv + 1)] for i in range(nu + 1)]
    Bk = [[g.v(o + U * (i / nu) + Vv * (j / nv) - N * depth) for j in range(nv + 1)] for i in range(nu + 1)]
    for i in range(nu):
        for j in range(nv):
            face(g, [F[i][j], F[i + 1][j], F[i + 1][j + 1], F[i][j + 1]], p)
            if back:
                face(g, [Bk[i][j], Bk[i][j + 1], Bk[i + 1][j + 1], Bk[i + 1][j]], p_back or p)
    ring = [F[i][0] for i in range(nu)] + [F[nu][j] for j in range(nv)] + \
           [F[i][nv] for i in range(nu, 0, -1)] + [F[0][j] for j in range(nv, 0, -1)]
    ringb = [Bk[i][0] for i in range(nu)] + [Bk[nu][j] for j in range(nv)] + \
            [Bk[i][nv] for i in range(nu, 0, -1)] + [Bk[0][j] for j in range(nv, 0, -1)]
    m = len(ring)
    for k in range(m):
        k1 = (k + 1) % m
        face(g, [ring[k1], ring[k], ringb[k], ringb[k1]], p_side or p)
    return F, Bk


def cyl(g, a, b, r0, r1, sides, p, cap0=True, cap1=True, label="cyl"):
    """Straight (tapered) round bar from a to b with planar UVs."""
    a, b = Vector(a), Vector(b)
    T = (b - a).normalized()
    N = any_perp(T)
    B = T.cross(N)

    def ring(c, r):
        return [c + (N * math.cos(2 * math.pi * j / sides) + B * math.sin(2 * math.pi * j / sides)) * r for j in range(sides)]
    return loft(g, [ring(a, r0), ring(b, r1)], p, cap0, cap1, label=label)


def horn(g, pts, radii, sides, p, label="horn"):
    """Curled tapering horn along pts (len(radii) = len(pts) - 1 rings, last point = tip)."""
    n = len(pts)
    T = [(pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized() for i in range(n)]
    N = [any_perp(T[0])]
    for i in range(1, n):
        q = N[-1] - T[i] * N[-1].dot(T[i])
        N.append(q.normalized() if q.length > 1e-6 else any_perp(T[i]))
    rings = []
    for i in range(n - 1):
        B = T[i].cross(N[i])
        rings.append([pts[i] + (N[i] * math.cos(2 * math.pi * j / sides) + B * math.sin(2 * math.pi * j / sides)) * radii[i]
                      for j in range(sides)])
    rings.append(pts[-1])
    return loft(g, rings, p, True, False, label=label)


def octa(g, c, r, p, rz=None, label="knob"):
    g.mark(label)
    c = Vector(c)
    rz = rz or r
    v = [g.v(c + d) for d in (Vector((r, 0, 0)), Vector((-r, 0, 0)), Vector((0, r, 0)), Vector((0, -r, 0)),
                              Vector((0, 0, rz)), Vector((0, 0, -rz)))]
    px, nx, py, ny, pz, nz = v
    for f in ((px, py, pz), (py, nx, pz), (nx, ny, pz), (ny, px, pz),
              (py, px, nz), (nx, py, nz), (ny, nx, nz), (px, ny, nz)):
        face(g, list(f), p)


def stud(g, c, n, r, h, p, sides=4, phase=math.pi / 4, label="stud"):
    """Pyramid stud whose base is embedded in the surface (no base face)."""
    g.mark(label)
    c, n = Vector(c), Vector(n).normalized()
    e1 = any_perp(n)
    e2 = n.cross(e1)
    base = [g.v(c - n * 0.04 + (e1 * math.cos(phase + 2 * math.pi * j / sides) +
                                e2 * math.sin(phase + 2 * math.pi * j / sides)) * r) for j in range(sides)]
    apex = g.v(c + n * h)
    for j in range(sides):
        face(g, [base[j], base[(j + 1) % sides], apex], p)


def rock(g, c, radii, npts, rnd, p, zmin=-1.0, squash_top=0.0, label="rock"):
    """Faceted rock: convex hull of jittered points on an ellipsoid, flat bottom at zmin."""
    g.mark(label)
    bm = bmesh.new()
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(npts):
        zz = 1.0 - 2.0 * (i + 0.5) / npts
        rr = math.sqrt(max(0.0, 1.0 - zz * zz))
        th = golden * i + rnd.uniform(-0.35, 0.35)
        k = rnd.uniform(0.80, 1.0)
        q = Vector((c[0] + radii[0] * rr * math.cos(th) * k, c[1] + radii[1] * rr * math.sin(th) * k,
                    c[2] + radii[2] * zz * k))
        if squash_top and q.z > c[2]:
            q.z = c[2] + (q.z - c[2]) * (1.0 - squash_top)
        q.z = max(q.z, zmin)
        bm.verts.new(q)
    for dx, dy in ((-0.7, -0.7), (0.7, -0.7), (0.7, 0.7), (-0.7, 0.7)):
        bm.verts.new(Vector((c[0] + radii[0] * dx * 0.92, c[1] + radii[1] * dy * 0.92, zmin)))
    res = bmesh.ops.convex_hull(bm, input=bm.verts[:], use_existing_faces=False)
    junk = list({e for e in res["geom_interior"] + res["geom_unused"] if isinstance(e, bmesh.types.BMVert)})
    junk.sort(key=lambda v: v.index)
    if junk:
        bmesh.ops.delete(bm, geom=junk, context="VERTS")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.verts.index_update()
    ids = [g.v(v.co) for v in bm.verts]
    for f in bm.faces:
        face(g, [ids[v.index] for v in f.verts], p)
    bm.free()


def paint_ends(p, end_tile, axis, thresh=0.9):
    """Paint override: faces whose normal is (anti)parallel to `axis` get `end_tile`
    (white-painted beam ends, cut timber...)."""
    axis = Vector(axis).normalized()
    base_fn = p.fn

    def fn(n, c):
        if abs(n.dot(axis)) > thresh:
            return end_tile
        return base_fn(n, c) if base_fn else None
    return p.but(fn=fn)


def moss_fn(rnd, prob=0.45, nz=0.55, other=None, other_prob=0.0, zmin=-1e9):
    """Moss on upward-facing faces (random), optional second stone tile for variation."""
    def fn(n, c):
        if n.z > nz and c.z > zmin and rnd.random() < prob:
            return "moss"
        if other and rnd.random() < other_prob:
            return other
        return None
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
    mat = bpy.data.materials.new("Atlas")
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


def get_flat_material(name, rgb):
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    lin = tuple(srgb_to_lin(c) for c in rgb) + (1.0,)
    bsdf.inputs["Base Color"].default_value = lin
    bsdf.inputs["Roughness"].default_value = 0.9
    mat.diffuse_color = lin
    return mat


def new_collection(name, parent=None):
    col = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(col)
    return col


def mesh_from_geo(name, geo, mat, col):
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
    return ob


# ======================================================================================
# 4. CHECKS (run on the generated geometry before export)
# ======================================================================================
def geo_bbox(*geos):
    mn = [1e9] * 3
    mx = [-1e9] * 3
    for g in geos:
        for p in g.co:
            for k in range(3):
                mn[k] = min(mn[k], p[k])
                mx[k] = max(mx[k], p[k])
    return mn, mx


def geo_hash(g):
    h = hashlib.sha1()
    for p in g.co:
        h.update(("%.4f,%.4f,%.4f;" % p).encode())
    for t, uv, tl in zip(g.tris, g.uv, g.tile):
        h.update(("%d,%d,%d:%s:" % (t + (tl,))).encode())
        for u in uv:
            h.update(("%.5f,%.5f;" % u).encode())
    return h.hexdigest()


def check_uvs(g, name):
    """Every face must sit inside ONE tile, inside the 4 % inset."""
    bad = 0
    lo, hi = INSET - 1e-4, 1 - INSET + 1e-4
    for uv, tl in zip(g.uv, g.tile):
        cells = set()
        for u, v in uv:
            cu, cv = u * 4.0, v * 4.0
            c, r = int(math.floor(cu - 1e-9 if cu >= 4 else cu)), int(math.floor(cv - 1e-9 if cv >= 4 else cv))
            fu, fv = cu - c, cv - r
            if not (lo <= fu <= hi and lo <= fv <= hi):
                bad += 1
            cells.add((c, r))
        if len(cells) > 1 or cells != {(TILES[tl][0], 3 - TILES[tl][1])}:
            bad += 1
    if bad:
        raise RuntimeError("UV check failed on %s: %d corners/faces outside a tile inset" % (name, bad))
    return True


def uv_stretch(g):
    """Max anisotropy of the UV mapping over all faces (1.0 = no stretching)."""
    worst = 1.0
    for (a, b, c), uv in zip(g.tris, g.uv):
        p0, p1, p2 = Vector(g.co[a]), Vector(g.co[b]), Vector(g.co[c])
        e1, e2 = p1 - p0, p2 - p0
        t1 = Vector((uv[1][0] - uv[0][0], uv[1][1] - uv[0][1]))
        t2 = Vector((uv[2][0] - uv[0][0], uv[2][1] - uv[0][1]))
        det = t1.x * t2.y - t1.y * t2.x
        if abs(det) < 1e-12 or e1.cross(e2).length < 1e-6:
            continue
        # Jacobian 3D <- UV; singular values give the stretch ratio
        du = (e1 * t2.y - e2 * t1.y) / det
        dv = (e2 * t1.x - e1 * t2.x) / det
        a11, a12, a22 = du.dot(du), du.dot(dv), dv.dot(dv)
        tr, dt = a11 + a22, a11 * a22 - a12 * a12
        disc = math.sqrt(max(0.0, tr * tr / 4 - dt))
        s1, s2 = math.sqrt(max(1e-18, tr / 2 + disc)), math.sqrt(max(1e-18, tr / 2 - disc))
        worst = max(worst, s1 / s2)
    return worst


def edge_stats(g):
    cnt = {}
    key = [tuple(int(round(c * 2e4)) for c in p) for p in g.co]
    for a, b, c in g.tris:
        for x, y in ((a, b), (b, c), (c, a)):
            k = (min(key[x], key[y]), max(key[x], key[y]))
            cnt[k] = cnt.get(k, 0) + 1
    return dict(open_edges=sum(1 for v in cnt.values() if v == 1),
                nonmanifold_edges=sum(1 for v in cnt.values() if v > 2))


def _bvh(co, tris):
    from mathutils.bvhtree import BVHTree
    return BVHTree.FromPolygons(co, tris, all_triangles=True)


def _rand_unit(rng):
    z = rng.uniform(-1.0, 1.0)
    a = rng.uniform(0.0, 2.0 * math.pi)
    s = math.sqrt(max(0.0, 1.0 - z * z))
    return Vector((s * math.cos(a), s * math.sin(a), z))


def facing_check(geos, ground=None, nrays=5000, seed=7):
    """'Back-face culling ON' test: rays from all around (above the ground) toward the
    model; the first face hit must face the ray, otherwise there is a hole or an inverted
    normal. Hits below the model's ground surface (hidden by terrain) are ignored."""
    co, tris = [], []
    for g in geos:
        o = len(co)
        co.extend(Vector(p) for p in g.co)
        tris.extend((a + o, b + o, c + o) for a, b, c in g.tris)
    bvh = _bvh(co, tris)
    mn = [min(p[k] for p in co) for k in range(3)]
    mx = [max(p[k] for p in co) for k in range(3)]
    ctr = Vector([(mn[k] + mx[k]) / 2 for k in range(3)])
    rad = (Vector(mx) - Vector(mn)).length
    ground = ground or (lambda x, y: 0.0)
    rng = random.Random(seed)
    hits = back = 0
    worst = []
    tries = 0
    while tries < nrays:
        o = ctr + _rand_unit(rng) * rad
        if o.z < ground(o.x, o.y) + 0.5:
            continue
        tries += 1
        tgt = Vector([rng.uniform(mn[k], mx[k]) for k in range(3)])
        d = (tgt - o).normalized()
        loc, nrm, idx, dist = bvh.ray_cast(o, d)
        if loc is None or loc.z < ground(loc.x, loc.y) - 0.02:
            continue
        hits += 1
        if nrm.dot(d) > 1e-4:
            back += 1
            if len(worst) < 5:
                worst.append([round(v, 2) for v in loc])
    return dict(rays=nrays, hits=hits, backfacing_first_hits=back, examples=worst)


def _pieces(parts):
    out = []
    for pname, g in parts.items():
        for s, e, lab in g.piece_ranges():
            vids = sorted({i for t in g.tris[s:e] for i in t})
            remap = {v: k for k, v in enumerate(vids)}
            co = [Vector(g.co[i]) for i in vids]
            tris = [(remap[a], remap[b], remap[c]) for a, b, c in g.tris[s:e]]
            mn = Vector([min(q[k] for q in co) for k in range(3)])
            mx = Vector([max(q[k] for q in co) for k in range(3)])
            vol = sum(co[a].dot(co[b].cross(co[c])) for a, b, c in tris) / 6.0
            out.append(dict(part=pname, label=lab, co=co, tris=tris, mn=mn, mx=mx, vol=vol,
                            bvh=_bvh(co, tris), tri_range=(s, e)))
    return out


def _inside(pc, q):
    """Ray-parity point-in-mesh test (rays along +Z, slightly tilted)."""
    d = Vector((0.0123, 0.0217, 1.0)).normalized()
    o = q.copy()
    n = 0
    for _ in range(64):
        loc, nrm, idx, dist = pc["bvh"].ray_cast(o, d)
        if loc is None:
            break
        n += 1
        o = loc + d * 1e-4
    return n % 2 == 1


def floating_check(parts, ground=None, tol=0.03):
    """Every primitive must touch the ground (or sink into it) or touch / intersect a
    primitive that is (transitively) grounded. Returns the labels of floating pieces."""
    pcs = _pieces(parts)
    ground = ground or (lambda x, y: 0.0)
    n = len(pcs)
    adj = [[] for _ in range(n)]
    for i in range(n):
        a = pcs[i]
        for j in range(i + 1, n):
            b = pcs[j]
            if any(a["mn"][k] > b["mx"][k] + tol or b["mn"][k] > a["mx"][k] + tol for k in range(3)):
                continue
            touch = bool(a["bvh"].overlap(b["bvh"]))
            if not touch:
                touch = any(b["bvh"].find_nearest(q, tol)[0] is not None for q in a["co"]) or \
                    any(a["bvh"].find_nearest(q, tol)[0] is not None for q in b["co"])
            if not touch:
                touch = _inside(b, a["co"][0]) or _inside(a, b["co"][0])
            if touch:
                adj[i].append(j)
                adj[j].append(i)
    seen = set()
    stack = [i for i in range(n) if any(q.z <= ground(q.x, q.y) + 0.02 for q in pcs[i]["co"])]
    seen.update(stack)
    while stack:
        i = stack.pop()
        for j in adj[i]:
            if j not in seen:
                seen.add(j)
                stack.append(j)
    floating = ["%s/%s" % (pcs[i]["part"], pcs[i]["label"]) for i in range(n) if i not in seen]
    neg = ["%s/%s" % (pc["part"], pc["label"]) for pc in pcs if pc["vol"] < -1e-6]
    return dict(pieces=n, floating=floating, inverted_volume_pieces=neg)


def box_clear(parts, lo, hi):
    """Number of triangles that intersect the open box (lo, hi) (barrier space check)."""
    lo, hi = Vector(lo), Vector(hi)
    cnt = 0
    bx = box_corners(lo, hi)
    btris = []
    for f in HEXA_FACES.values():
        btris += [(f[0], f[1], f[2]), (f[0], f[2], f[3])]
    bb = _bvh(bx, btris)
    for g in parts.values():
        co = [Vector(p) for p in g.co]
        bvh = _bvh(co, g.tris)
        hit = set(i for i, _ in bvh.overlap(bb))
        for i, (a, b, c) in enumerate(g.tris):
            if i in hit:
                cnt += 1
                continue
            m = (co[a] + co[b] + co[c]) / 3
            if all(lo[k] < m[k] < hi[k] for k in range(3)):
                cnt += 1
    return cnt


# ======================================================================================
# 5. MODELS
#    Blender +Z up, front = -Y, origin = centre of the base at ground level (z = 0).
# ======================================================================================
MOMIJI = [(-22, 0.62), (5, 0.36), (32, 0.95), (61, 0.42), (90, 1.0), (119, 0.42), (148, 0.95), (175, 0.36),
          (202, 0.62), (240, 0.22), (270, 0.32), (300, 0.22)]       # (angle deg, radius): 5-lobed maple leaf


def drop_leaves(src_geos, out, rnd, pick_xy, n, size=(0.95, 1.3), label="fallen_leaf"):
    """Fallen momiji leaves: ray-cast from above onto the given geometry, keep only spots
    where the whole leaf outline lies on one face plane (no straddling), embed the leaf
    0.04 studs into the surface so it can never float."""
    co, tris = [], []
    for g in src_geos:
        o = len(co)
        co.extend(Vector(p) for p in g.co)
        tris.extend((a + o, b + o, c + o) for a, b, c in g.tris)
    bvh = _bvh(co, tris)
    pl = P("maple_red", 1.5)
    placed = []
    tries = 0
    while len(placed) < n and tries < 400:
        tries += 1
        x, y = pick_xy(rnd)
        p, nrm, _, _ = bvh.ray_cast(Vector((x, y, 200.0)), Vector((0.0, 0.0, -1.0)))
        if p is None or nrm.z < 0.5:
            continue
        s = rnd.uniform(*size) / 2.0
        if any((p - q).length < s + r for q, r in placed):
            continue
        a = rnd.uniform(0.0, 2.0 * math.pi)
        t = any_perp(nrm)
        U = t * math.cos(a) + nrm.cross(t) * math.sin(a)
        Vv = nrm.cross(U)
        poly = [(r * s * math.cos(math.radians(th)), r * s * math.sin(math.radians(th))) for th, r in MOMIJI]
        ok = True
        for u, v in poly:
            q = p + U * u + Vv * v
            h = bvh.ray_cast(q + nrm * 0.3, -nrm, 0.6)
            if h[0] is None or abs((h[0] - q).dot(nrm)) > 0.02:
                ok = False
                break
        if not ok:
            continue
        extrude_poly(out, poly, p + nrm * 0.03, U, Vv, 0.07, pl, back=False, label=label)
        placed.append((p, s))
    return len(placed)


def _jit(rnd, xy, z):
    return lambda q: Vector((rnd.uniform(-xy, xy), rnd.uniform(-xy, xy), rnd.uniform(-z, z)))


def beam_x(g, x0, x1, half_y, z_bot, h, sori, p, slant=0.0, ts=None, cap_p=None, label="beam_x"):
    """Beam along X with upright sections; bottom follows z_bot + sori(x). The end faces
    are slanted: the top edge reaches x0/x1 and the bottom edge stops `slant` short."""
    ts = ts or [-1, -0.92, -0.8, -0.64, -0.44, -0.22, 0, 0.22, 0.44, 0.64, 0.8, 0.92, 1]
    half = (x1 - x0) / 2.0
    xc = (x0 + x1) / 2.0
    rings = []
    for t in ts:
        xt = xc + t * half
        xb = xc + t * (half - slant)
        zb = z_bot + sori(xb)
        zt = z_bot + h + sori(xt)
        rings.append([Vector((xb, half_y, zb)), Vector((xt, half_y, zt)),
                      Vector((xt, -half_y, zt)), Vector((xb, -half_y, zb))])
    return loft(g, rings, p, True, True, cap_p, label)


# --------------------------------------------------------------------------------------
# torii: two pillars 18 tall, 12 apart, kasagi with upturned ends 20 wide, nuki, black cap
# --------------------------------------------------------------------------------------
def gen_torii():
    mid = "torii"
    rnd = random.Random(hash_seed(mid))
    L, K, S = Geo(), Geo(), Geo()
    pl = P("lacquer", 6.0, smooth=True)
    plf = P("lacquer", 6.0)
    pk = P("iron", 6.0, smooth=True)
    pkf = P("iron", 6.0)
    ps = P("stone_grey", 6.0, rnd=rnd, fn=moss_fn(rnd, 0.35, 0.5, "stone_warm", 0.3))
    X0, XT, ZTOP = 6.0, 5.82, 18.3                    # pillar centre x at z=0 / z=18 (inward batter)

    def px(z):
        return X0 + (XT - X0) * z / 18.0

    def pr(z):
        return 0.98 - 0.13 * z / 18.0

    def sori(x):
        return 1.45 * (abs(x) / 10.0) ** 2.3

    Z_SHI = 17.42                                     # shimaki underside at x = 0
    for sx in (-1, 1):
        lathe(S, [(1.62, -0.6), (1.66, 0.35), (1.42, 0.86), (1.12, 1.0), (0.0, 1.0)], 8, ps,
              c=(sx * X0, 0.0, 0.0), phase=math.pi / 8, jit=_jit(rnd, 0.05, 0.03), label="base")

        def pring(z, dr=0.0):
            return ring_xy((sx * px(z), 0.0, 0.0), pr(z) + dr, 16, z)
        loft(K, [pring(0.9), pring(2.9)], pk, cap0=False, cap1=False, label="nemaki")
        loft(L, [pring(2.9), pring(ZTOP)], pl, cap0=False, cap1=True, label="pillar")
        zb = Z_SHI + sori(XT) + 0.04
        r0 = pr(zb)
        lathe(L, [(r0 - 0.03, zb - 0.78), (r0 + 0.17, zb - 0.72), (r0 + 0.17, zb - 0.06), (r0 + 0.1, zb),
                  (r0 - 0.03, zb)], 16, pl, c=(sx * px(zb - 0.4), 0.0, 0.0), cap0=False, cap1=False,
              band_p=lambda i: pl if i == 1 else plf, label="daiwa")
    beam_x(L, -9.3, 9.3, 0.9, Z_SHI, 0.86, sori, plf, slant=0.12, label="shimaki")
    beam_x(L, -9.92, 9.92, 1.0, Z_SHI + 0.84, 0.98, sori, plf, slant=0.25, label="kasagi")
    beam_x(K, -10.0, 10.0, 1.1, Z_SHI + 1.80, 0.46, sori, pkf, slant=0.08, label="kasagi_cap")
    block(L, (-7.7, -0.42, 14.4), (7.7, 0.42, 15.35), plf, label="nuki")
    block(L, (-0.42, -0.36, 15.3), (0.42, 0.36, Z_SHI + 0.06), plf, label="gakuzuka")
    block(L, (-0.86, -0.57, 15.52), (0.86, -0.3, 17.24), plf, label="plaque_frame")
    block(K, (-0.68, -0.65, 15.7), (0.68, -0.5, 17.06), pkf, label="plaque")
    cols = []
    for sx in (-1, 1):
        cols.append([[sx * 5.91, 0.0, 9.15], [2.0, 2.0, 18.3], 0.0])
        cols.append([[sx * 6.0, 0.0, 0.2], [3.3, 3.3, 1.6], 0.0])
    cols.append([[0.0, 0.0, 18.9], [20.0, 2.2, 3.0], 0.0])
    cols.append([[0.0, 0.0, 14.88], [15.4, 0.84, 0.95], 0.0])
    gap_z = [round(2 * (px(z) - pr(z)), 2) for z in (1.0, 5.0, 14.4)]
    info = dict(pillar_centres_x=[-X0, X0], pillar_spacing_at_ground=2 * X0, pillar_height=ZTOP,
                clear_width_between_pillars={"z=1": gap_z[0], "z=5": gap_z[1], "z=14.4 (under nuki)": gap_z[2]},
                clear_width_between_stone_bases=round(2 * (X0 - 1.66), 2),
                clear_height_under_nuki=14.4, kasagi_width=20.0,
                notes="pillars lean inward (uchikorobi) from x=+-6.0 to +-5.82; black nemaki band at the foot")
    return dict(parts={"Lacquer": L, "Black": K, "Stone": S}, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# toro_lantern: hexagonal Kasuga-style stone lantern (also used, scaled, by the gate)
# --------------------------------------------------------------------------------------
def light_box(S, G, ps, pglow, c, sc, R=0.85, z0=3.72, z1=5.14, ww=0.5, wz=(3.98, 4.86), t=0.2):
    """Hexagonal light box; the faces looking -Y and +Y have window openings with reveals
    that run into the glow prism (so the window shows the glow, never a hole)."""
    S.mark("hibukuro")
    cx, cy, cz = c
    vb = [Vector((cx + R * sc * math.cos(math.radians(60 * k)), cy + R * sc * math.sin(math.radians(60 * k)),
                  cz + z0 * sc)) for k in range(6)]
    vt = [q + Z * ((z1 - z0) * sc) for q in vb]
    B = [S.v(q) for q in vb]
    T = [S.v(q) for q in vt]
    for k in range(6):
        k1 = (k + 1) % 6
        if k in (1, 4):                               # face normals at 90 / 270 degrees
            n = Vector((math.cos(math.radians(30 + 60 * k)), math.sin(math.radians(30 + 60 * k)), 0.0))
            e = (vb[k1] - vb[k]).normalized()
            m = (vb[k] + vb[k1]) / 2.0
            za, zb = cz + wz[0] * sc, cz + wz[1] * sc
            I = [m - e * (ww / 2 * sc), m + e * (ww / 2 * sc), m + e * (ww / 2 * sc), m - e * (ww / 2 * sc)]
            I = [Vector((q.x, q.y, z)) for q, z in zip(I, (za, za, zb, zb))]
            J = [q - n * (t * sc) for q in I]
            Ii = [S.v(q) for q in I]
            Ji = [S.v(q) for q in J]
            O = [B[k], B[k1], T[k1], T[k]]
            for a in range(4):
                a1 = (a + 1) % 4
                face(S, [O[a], O[a1], Ii[a1], Ii[a]], ps)
                face(S, [Ii[a], Ii[a1], Ji[a1], Ji[a]], ps)
        else:
            face(S, [B[k], B[k1], T[k1], T[k]], ps)
    G.mark("glow")
    lathe(G, [(0.0, 3.77 * sc), (0.635 * sc, 3.77 * sc), (0.635 * sc, 5.09 * sc), (0.0, 5.09 * sc)], 6, pglow,
          c=c, label="glow")


def lantern(S, G, rnd, c=(0.0, 0.0, 0.0), sc=1.0, psides=10, fsides=8, knobs=True, hsides=5):
    cx, cy, cz = c
    ps = P("stone_grey", 4.0 * sc, rnd=rnd, fn=moss_fn(rnd, 0.0, 1.0, "stone_warm", 0.22))
    pbase = ps.but(fn=moss_fn(rnd, 0.3, 0.6, "stone_warm", 0.22))
    proof = ps.but(fn=moss_fn(rnd, 0.42, 0.45, "stone_warm", 0.15))
    psm = ps.but(smooth=True)
    pglow = P("glow", 2.0)
    jit = _jit(rnd, 0.022 * sc, 0.012 * sc)

    def prof(lst):
        return [(r * sc, z * sc) for r, z in lst]
    lathe(S, prof([(1.5, -0.5), (1.5, 0.3), (1.3, 0.45), (1.04, 0.48), (0.84, 0.78), (0.0, 0.78)]), 6, pbase,
          c=c, jit=jit, label="kiso")
    lathe(S, prof([(0.5, 0.7), (0.48, 1.72), (0.56, 1.78), (0.56, 1.98), (0.47, 2.04), (0.45, 3.1)]), psides, psm,
          c=c, cap0=False, cap1=False, band_p=lambda i: psm if i % 2 == 0 else ps, label="sao")
    lathe(S, prof([(0.45, 3.0), (0.62, 3.08), (1.12, 3.44), (1.12, 3.68), (0.92, 3.72), (0.0, 3.72)]), 6, ps,
          c=c, jit=jit, label="chudai")
    light_box(S, G, ps, pglow, c, sc)
    # kasa: hexagonal roof, corners curled up (warabite), concave slopes
    rc = 1.62

    def ring(scale, zc, lift):
        pts = []
        for m in range(12):
            th = math.radians(30 * m)
            corner = m % 2 == 0
            r = rc * scale * (1.0 if corner else math.cos(math.radians(30)))
            pts.append(Vector((cx + r * math.cos(th) * sc, cy + r * math.sin(th) * sc,
                               cz + (zc + (lift if corner else 0.0)) * sc)))
        return pts
    U1 = ring(1.0, 5.36, 0.52)
    loft(S, [Vector((cx, cy, cz + 5.12 * sc)), ring(1.0, 5.12, 0.3), U1, ring(0.64, 5.66, 0.16),
             ring(0.33, 5.96, 0.0), ring(0.19, 6.06, 0.0), Vector((cx, cy, cz + 6.06 * sc))], proof,
         cap0=False, cap1=False, label="kasa")
    if knobs:
        for m in range(0, 12, 2):
            q = U1[m]
            out = Vector((q.x - cx, q.y - cy, 0.0)).normalized()
            pts = [q - out * (0.12 * sc) - Z * (0.12 * sc), q + Z * (0.1 * sc), q + out * (0.1 * sc) + Z * (0.3 * sc),
                   q + out * (0.26 * sc) + Z * (0.34 * sc)]
            horn(S, pts, [0.13 * sc, 0.12 * sc, 0.09 * sc], hsides, ps, label="warabite")
    lathe(S, prof([(0.3, 6.0), (0.36, 6.1), (0.3, 6.22), (0.19, 6.27), (0.31, 6.42), (0.35, 6.56), (0.27, 6.74),
                   (0.12, 6.89), (0.0, 7.0)]), fsides, ps, c=c, label="hoju")


def gen_lantern():
    mid = "toro_lantern"
    rnd = random.Random(hash_seed(mid))
    S, G = Geo(), Geo()
    lantern(S, G, rnd)
    cols = [[[0.0, 0.0, 0.15], [3.0, 2.6, 1.3], 0.0], [[0.0, 0.0, 4.15], [2.6, 2.3, 5.7], 0.0]]
    info = dict(style="hexagonal Kasuga-style toro", windows="2 openings, facing -Y (front) and +Y (back)",
                glow_mesh="toro_lantern__Glow: closed hexagonal prism inside the light box, visible through the windows")
    return dict(parts={"Stone": S, "_Glow": G}, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# stone_stairs: modular segment 16 wide, rises 8 over 20 (8 steps), low side curbs; tiles
# end to end with an offset of (0, +20, +8).
# --------------------------------------------------------------------------------------
STAIR_RUN, STAIR_RISE, STAIR_STEPS, STAIR_W = 20.0, 8.0, 8, 16.0


def gen_stairs():
    mid = "stone_stairs"
    rnd = random.Random(hash_seed(mid))
    S = Geo()
    tread, rise = STAIR_RUN / STAIR_STEPS, STAIR_RISE / STAIR_STEPS
    slope = STAIR_RISE / STAIR_RUN
    hw = STAIR_W / 2.0
    curb_w = 1.2

    def stone_fn(n, c):
        if n.z > 0.3 and rnd.random() < (0.42 if abs(c.x) > hw - 3.4 else 0.1):
            return "moss"
        if rnd.random() < 0.3:
            return "stone_warm"
        return None
    ps = P("stone_grey", 5.0, rnd=rnd, fn=stone_fn)
    for k in range(STAIR_STEPS):
        y0 = -STAIR_RUN / 2 + tread * k
        zt = rise * (k + 1)
        c1, c2 = rnd.uniform(-3.6, -1.4), rnd.uniform(1.4, 3.6)
        for xa, xb in ((-hw + curb_w - 0.2, c1 + 0.03), (c1 - 0.03, c2 + 0.03), (c2 - 0.03, hw - curb_w + 0.2)):
            yf = y0 + (rnd.uniform(0.0, 0.07) if k == 0 else rnd.uniform(-0.06, 0.06))
            yb = min(STAIR_RUN / 2, y0 + tread + 0.5)
            ztop = zt + rnd.uniform(-0.06, 0.04)
            c = box_corners((xa, yf, rise * k - 0.8), (xb, yb, ztop))
            for q in c[4:]:
                q.z += rnd.uniform(-0.05, 0.05)
            def sjit(q):
                d = Vector((rnd.uniform(-0.02, 0.02), rnd.uniform(-0.02, 0.02), 0.0))
                if abs(q.y + d.y) > STAIR_RUN / 2:
                    d.y = 0.0
                return d
            chamfer_hexa(S, c, 0.13, ps, skip=(), jit=sjit, label="step%d" % k)
    # side curbs, 3 stones per side; their ends at y = +-10 are exact so segments tile
    for side in (-1, 1):
        xlo, xhi = (hw - curb_w, hw) if side > 0 else (-hw, -hw + curb_w)
        s1, s2 = rnd.uniform(-4.0, -2.0), rnd.uniform(2.5, 5.0)
        for ya, yb in ((-STAIR_RUN / 2, s1 + 0.02), (s1 - 0.02, s2 + 0.02), (s2 - 0.02, STAIR_RUN / 2)):
            def zb(y):
                return slope * (y + STAIR_RUN / 2) - 1.0

            def ztp(y):
                return rise + 0.6 + slope * (y + STAIR_RUN / 2)
            c = [Vector((xlo, ya, zb(ya))), Vector((xhi, ya, zb(ya))), Vector((xhi, yb, zb(yb))), Vector((xlo, yb, zb(yb))),
                 Vector((xlo, ya, ztp(ya))), Vector((xhi, ya, ztp(ya))), Vector((xhi, yb, ztp(yb))), Vector((xlo, yb, ztp(yb)))]

            def jit(q):
                if abs(abs(q.y) - STAIR_RUN / 2) < 0.6:
                    return Vector((0.0, 0.0, 0.0))
                return Vector((rnd.uniform(-0.03, 0.03), 0.0, rnd.uniform(-0.05, 0.05)))
            chamfer_hexa(S, c, 0.16, ps, skip=(), jit=jit, label="curb")
    nleaf = drop_leaves([S], S, rnd, lambda r: (r.uniform(-6.2, 6.2), r.uniform(-9.2, 9.2)), 6)
    cols = []
    for k in range(STAIR_STEPS):
        y0 = -STAIR_RUN / 2 + tread * k
        cols.append([[0.0, round(y0 + tread / 2, 3), round((rise * k - 0.8 + rise * (k + 1)) / 2, 3)],
                     [STAIR_W, tread, round(rise + 0.8, 3)], 0.0])
    info = dict(steps=STAIR_STEPS, step_rise=rise, step_run=tread, width=STAIR_W, run=STAIR_RUN, rise=STAIR_RISE,
                tile_offset_blender=[0.0, STAIR_RUN, STAIR_RISE],
                tiling="place the next segment at this origin + (0, +20, +8) (Blender, Z up; climbing toward +Y)",
                ground_line="terrain under the stairs: z = 0.4 * (y + 10) (the back edge of every tread)",
                curb_height_above_nose=0.6, fallen_maple_leaves=nleaf)
    return dict(parts={"Stone": S}, colliders=cols, info=info,
                ground=lambda x, y: slope * (min(max(y, -STAIR_RUN / 2), STAIR_RUN / 2) + STAIR_RUN / 2))


# --------------------------------------------------------------------------------------
# shared emblem: the Tengu feather (two vanes split by a light rachis gap, notched edges)
# --------------------------------------------------------------------------------------
FEATHER_R = [(0.0, 0.06), (0.06, 0.40), (0.2, 0.60), (0.4, 0.66), (0.47, 0.48), (0.53, 0.64), (0.7, 0.56),
             (0.86, 0.36), (0.98, 0.09)]
FEATHER_L = [(0.0, -0.06), (0.04, -0.30), (0.16, -0.44), (0.36, -0.50), (0.55, -0.46), (0.61, -0.30),
             (0.67, -0.42), (0.85, -0.26), (0.97, -0.07)]
FEATHER_SHAFT = [(-0.2, -0.035), (0.03, -0.035), (0.03, 0.035), (-0.2, 0.035)]


def feather(cx, cz, L, W, tilt=0.0, shaft=True):
    """Feather outline polygons in a 2D plane: [right vane, left vane(, shaft)]."""
    d = (math.sin(tilt), math.cos(tilt))
    e = (math.cos(tilt), -math.sin(tilt))

    def tr(poly):
        return [(cx + (a - 0.45) * L * d[0] + w * W * e[0], cz + (a - 0.45) * L * d[1] + w * W * e[1]) for a, w in poly]
    out = [tr(FEATHER_R), tr(FEATHER_L)]
    if shaft:
        out.append(tr(FEATHER_SHAFT))
    return out


def face_dir(g, idx, p, want):
    """Add a face, flipping its winding if needed so that it faces `want`."""
    pts = [Vector(g.co[i]) for i in idx]
    if poly_normal(pts).dot(Vector(want)) < 0:
        idx = list(reversed(idx))
    face(g, idx, p)


# --------------------------------------------------------------------------------------
# tengu_banner: nobori on a pole, 14 tall, cream cloth with a black feather (faces)
# --------------------------------------------------------------------------------------
def gen_banner():
    mid = "tengu_banner"
    rnd = random.Random(hash_seed(mid))
    PO, CL = Geo(), Geo()
    pwood = P("wood", 15.0, grain=Z, smooth=True)
    pcloth = P("cloth", 3.0)
    pink = P("iron", 3.0)
    pstone = P("stone_grey", 3.0, rnd=rnd, fn=moss_fn(rnd, 0.4, 0.6, "stone_warm", 0.3))
    chamfer_hexa(PO, box_corners((-0.55, -0.55, -0.3), (0.55, 0.55, 0.32)), 0.1, pstone, skip=(),
                 jit=_jit(rnd, 0.02, 0.015), label="weight")
    lathe(PO, [(0.2, -0.8), (0.17, 13.72)], 6, pwood, cap1=False, label="pole")
    lathe(PO, [(0.12, 13.66), (0.25, 13.78), (0.0, 14.0)], 6, P("gold", 1.0), cap0=False, label="finial")
    cyl(PO, (0.0, 0.0, 13.28), (2.95, 0.0, 13.28), 0.1, 0.1, 6, pwood.but(S=4.0, grain=(1, 0, 0)), cap0=False,
        label="crossbar")
    # cloth: 2D layout in (x, z), constrained triangulation keeps the feather as its own faces
    x0, x1, z0, z1 = 0.3, 2.8, 2.6, 13.15
    nzs = 6
    bnd = [(x0, z0), ((x0 + x1) / 2, z0), (x1, z0)] + [(x1, z0 + (z1 - z0) * k / nzs) for k in range(1, nzs)] + \
          [(x1, z1), ((x0 + x1) / 2, z1), (x0, z1)] + [(x0, z1 - (z1 - z0) * k / nzs) for k in range(1, nzs)]
    polys = feather((x0 + x1) / 2 + 0.05, 7.6, 6.4, 1.5, tilt=math.radians(9))
    grid = [((x0 + x1) / 2, z0 + (z1 - z0) * k / nzs) for k in range(1, nzs)]
    verts, faces = list(bnd), [list(range(len(bnd)))]
    for poly in polys:
        poly = ccw2(poly)
        faces.append(list(range(len(verts), len(verts) + len(poly))))
        verts += poly
    for q in grid:
        verts.append(q)
    cdt = mgeo.delaunay_2d_cdt([Vector(q) for q in verts], [], faces, 1, 1e-5)
    ov, ofaces, oorig, orig_v = cdt[0], cdt[2], cdt[5], cdt[3]

    def disp(x, z):
        u = (x - x0) / (x1 - x0)
        return -0.16 * math.sin(math.pi * u) + 0.08 * u * math.sin(2 * math.pi * (z - z0) / 3.6)
    th = 0.025
    CL.mark("cloth")
    F = [CL.v((q.x, disp(q.x, q.y) - th, q.y)) for q in ov]
    B = [CL.v((q.x, disp(q.x, q.y) + th, q.y)) for q in ov]
    nemb = 0
    for f, orig in zip(ofaces, oorig):
        emb = any(k >= 1 for k in orig)
        nemb += emb
        a, b, c = f
        pa, pb, pc = ov[a], ov[b], ov[c]
        if (pb.x - pa.x) * (pc.y - pa.y) - (pb.y - pa.y) * (pc.x - pa.x) < 0:
            b, c = c, b
        face(CL, [F[a], F[b], F[c]], pink if emb else pcloth)     # front (faces -Y)
        face(CL, [B[a], B[c], B[b]], pink if emb else pcloth)     # back (faces +Y)
    o2n = {}
    for i, lst in enumerate(orig_v):
        for k in lst:
            o2n[k] = i
    loop = [o2n[k] for k in range(len(bnd))]
    for k in range(len(loop)):
        a, b = loop[k], loop[(k + 1) % len(loop)]
        face(CL, [F[b], F[a], B[a], B[b]], pcloth)
    # loops (chichi) tying the cloth to the pole and the crossbar
    for z in (3.6, 7.6, 11.6):
        block(PO, (-0.25, -0.25, z), (0.37, 0.25, z + 0.26), pcloth, label="chichi")
    block(PO, (1.44, -0.16, 13.04), (1.68, 0.16, 13.42), pcloth, label="chichi")
    cols = [[[0.0, 0.0, 6.7], [0.45, 0.45, 13.4], 0.0], [[0.0, 0.0, 0.01], [1.1, 1.1, 0.62], 0.0]]
    info = dict(cloth=dict(width=x1 - x0, height=round(z1 - z0, 2), x_range=[x0, x1], z_range=[z0, z1]),
                emblem="black feather painted as cloth faces (constrained triangulation), visible on both sides; no text",
                emblem_triangles_per_side=nemb)
    return dict(parts={"Pole": PO, "Cloth": CL}, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# roadside_shrine: hokora on a stone base, tiny gabled roof, offering cup, paper charm
# --------------------------------------------------------------------------------------
def shide(g, x, ytop, ztop, w, h, n, p, depth=0.04, label="shide"):
    """Zigzag folded paper streamer (front faces -Y) hanging from (x, ytop, ztop):
    the outline of n overlapping strips, every other one shifted sideways."""
    o = [0.62 * w * (k % 2) for k in range(n)]
    pts = []
    for k in range(n):
        pts += [(o[k] + w, -k * h), (o[k] + w, -(k + 1) * h)]
    for k in reversed(range(n)):
        pts += [(o[k], -(k + 1) * h), (o[k], -k * h)]
    cx = 0.5 * (w + 0.62 * w * (n > 1))
    poly = [(u - cx, v) for u, v in pts]
    extrude_poly(g, poly, (x, ytop, ztop), (1, 0, 0), (0, 0, 1), depth, p, label=label)


def gen_shrine():
    mid = "roadside_shrine"
    rnd = random.Random(hash_seed(mid))
    S, W, R, O = Geo(), Geo(), Geo(), Geo()
    ps = P("stone_grey", 3.0, rnd=rnd, fn=moss_fn(rnd, 0.45, 0.55, "stone_warm", 0.3))
    pw = P("cut_wood", 1.6, grain=Z)
    pwd = P("wood_light", 1.6, grain=Z)
    proof = P("roof_tile", 1.6)
    chamfer_hexa(S, box_corners((-1.75, -1.6, -0.45), (1.75, 1.5, 0.55)), 0.12, ps, skip=(),
                 jit=_jit(rnd, 0.03, 0.025), label="base1")
    chamfer_hexa(S, box_corners((-1.3, -0.95, 0.5), (1.3, 1.25, 1.45)), 0.1, ps, skip=(),
                 jit=_jit(rnd, 0.025, 0.02), label="base2")
    block(W, (-1.1, -0.82, 1.43), (1.1, 1.1, 1.6), pwd.but(grain=(0, 1, 0)), label="floor")
    # gabled roof (ridge along X), front slope faces -Y
    ye_f, ye_b, yr, ze, zr, t = -1.28, 1.68, 0.2, 3.05, 4.12, 0.14
    sl = (zr - ze) / (yr - ye_f)
    tv = t * math.sqrt(1 + sl * sl)                           # vertical slab thickness

    def under(y):
        return ze + (min(y - ye_f, ye_b - y)) * sl - tv
    body = [(-0.55, 1.6), (0.95, 1.6), (0.95, under(0.95) - 0.01), (yr, under(yr) - 0.01), (-0.55, under(-0.55) - 0.01)]
    extrude_poly(W, body, (0.85, 0.0, 0.0), (0, 1, 0), (0, 0, 1), 1.7, pw, label="body")
    for xa, xb in ((-0.62, -0.025), (0.025, 0.62)):
        block(W, (xa, -0.63, 1.7), (xb, -0.52, 2.95), pwd, label="door")
    block(W, (-0.86, -0.64, 2.95), (0.86, -0.5, 3.08), pwd.but(grain=(1, 0, 0)), label="lintel")
    for x in (-0.85, 0.85):
        for y in (-0.55, 0.95):
            block(W, (x - 0.09, y - 0.09, 1.6), (x + 0.09, y + 0.09, under(y) + 0.02), pwd, label="post")
    for sgn in (-1, 1):
        ye = ye_f if sgn < 0 else ye_b
        if sgn < 0:
            o, U = (-1.34, ye, ze), (2.68, 0.0, 0.0)
        else:
            o, U = (1.34, ye, ze), (-2.68, 0.0, 0.0)
        Vv = (0.0, (yr - ye) * 1.04, (zr - ze) * 1.04)
        slab_grid(R, o, U, Vv, 2, 1, t, proof.but(grain=Vv), p_back=pwd, p_side=pwd, label="roof")
    block(R, (-1.38, yr - 0.18, zr - 0.12), (1.38, yr + 0.18, zr + 0.16), proof.but(grain=(1, 0, 0)), label="ridge")
    for x in (-1.32, 1.32):
        for dy in (1, -1):
            d = Vector((0.0, dy * math.cos(math.radians(52)), math.sin(math.radians(52))))
            c = Vector((x, yr, zr + 0.04))
            beam(W, c - d * 0.75, c + d * 1.05, 0.13, 0.26, pw, label="chigi")
    for x in (-0.45, 0.45):
        tube(W, [Vector((x, yr - 0.38, zr + 0.24)), Vector((x, yr + 0.38, zr + 0.24))], [0.12, 0.12], 6,
             pw, h_ref=0.8, cap0=True, cap1=True, label="katsuogi")
    # offerings: straw rope with a folded paper charm (shide), white cup on the front ledge
    n = 9
    pts = [Vector((-0.94 + 1.88 * k / (n - 1), -0.67, 2.68 - 0.1 * math.sin(math.pi * k / (n - 1)))) for k in range(n)]
    tube(O, pts, [0.075] * n, 6, P("rope", smooth=True), h_ref=0.47, cap0=True, cap1=True, label="rope")
    shide(O, 0.0, -0.75, 2.6, 0.13, 0.17, 4, P("plaster", 1.0), label="shide")
    lathe(O, [(0.0, 0.45), (0.13, 0.45), (0.13, 0.52), (0.27, 0.62), (0.31, 0.72), (0.26, 0.72), (0.0, 0.62)], 10,
          P("plaster", 1.0), c=(-0.75, -1.28, 0.1), label="cup")
    nleaf = drop_leaves([R], R, rnd, lambda r: (r.uniform(-1.0, 1.0), r.uniform(-1.05, -0.3)), 2, size=(0.6, 0.75))
    nleaf += drop_leaves([S, O], S, rnd, lambda r: (r.uniform(0.0, 1.4), r.uniform(-1.45, -1.15)), 1, size=(0.55, 0.65))
    cols = [[[0.0, -0.05, 0.05], [3.5, 3.1, 1.0], 0.0], [[0.0, 0.2, 2.85], [2.7, 3.0, 2.8], 0.0]]
    info = dict(roof="gabled (kirizuma) kawara roof with chigi and katsuogi", offering_cup="white cup on the front ledge",
                paper_charm="zigzag shide folded paper on a small straw rope across the shrine front",
                fallen_maple_leaves=nleaf)
    return dict(parts={"Stone": S, "Wood": W, "Roof": R, "Offering": O}, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# tengu_gate: wooden two-door gate 26 wide x 22 tall, heavy kawara roof, plaster walls that
# end in mountain rock, shimenawa with shide across the top, two toro lamp posts.
# Origin = centre of the roofed gate footprint (ridge line) at ground level.
# --------------------------------------------------------------------------------------
GATE_YF, GATE_YR, GATE_PX = -2.5, 2.5, 8.0           # front pillar / door line, rear posts, pillar x
GATE_DOOR = dict(x=7.06, z0=0.45, z1=13.4, y_front=-2.75, y_back=-2.2)
GATE_BARRIER = ((-6.75, -5.1, 0.0), (6.75, -3.09, 13.4))    # must stay empty (barrier effect): 2 studs in
#                                                              front of the door relief (studs reach y = -3.07)
GATE_ROOF = dict(half_x=13.0, half_y=9.0, z_eave=17.3, z_ridge=21.0, thick=0.9, curve=1.35, upturn=0.9)


def gate_roof_z(x, y):
    r = GATE_ROOF
    t = max(0.0, 1.0 - abs(y) / r["half_y"])
    return r["z_eave"] + (r["z_ridge"] - r["z_eave"]) * t ** r["curve"] + r["upturn"] * (abs(x) / r["half_x"]) ** 3 * (1 - t) ** 2


def gate_roof(R, L, PL, rnd):
    r = GATE_ROOF
    hx, hy, th = r["half_x"], r["half_y"], r["thick"]
    NX = 32
    xs = [-hx + 2 * hx * i / NX for i in range(NX + 1)]
    dz = [0.24 if i % 2 else 0.0 for i in range(NX + 1)]
    rows = [1.0, 0.733, 0.467, 0.222, 0.0]                  # |y| / hy from eave to ridge
    ux = [-hx + 2 * hx * m / 8 for m in range(9)]           # underside columns
    for sgn in (-1, 1):                                     # front slope (-Y), back slope (+Y)
        R.mark("roof_slope")
        up_slope = Vector((0.0, -sgn * 1.0, 0.75)).normalized()
        out = Vector((0.0, sgn * 1.0, 1.0))
        pr = P("roof_tile", (6.5, 6.0), grain=up_slope)
        pu = P("wood_dark", (4.0, 4.0), grain=(1, 0, 0))
        ys = [sgn * hy * f for f in rows]
        T = [[R.v((x, y, gate_roof_z(x, y) + dz[i])) for i, x in enumerate(xs)] for y in ys]
        Uv = [[R.v((x, y, gate_roof_z(x, y) - th)) for x in ux] for y in ys]
        for a in range(len(ys) - 1):
            for i in range(NX):
                face_dir(R, [T[a][i], T[a][i + 1], T[a + 1][i + 1], T[a + 1][i]], pr, out)
            for m in range(8):
                face_dir(R, [Uv[a][m], Uv[a][m + 1], Uv[a + 1][m + 1], Uv[a + 1][m]], pu, -out)
        # eave: tile-end skirt, then a fascia strip down to the underside
        y = ys[0]
        Sk = [R.v((x, y, gate_roof_z(x, y) - 0.3)) for x in xs]
        pe = P("roof_tile", (1.6, 1.5))
        pf = P("lacquer", 6.0)                                  # vermilion fascia board under the tile ends
        for i in range(NX):
            face_dir(R, [Sk[i], Sk[i + 1], T[0][i + 1], T[0][i]], pe, (0, sgn, 0))
        for m in range(8):
            face_dir(R, [Sk[4 * m + k] for k in range(5)] + [Uv[0][m + 1], Uv[0][m]], pf, (0, sgn, 0))
        # gable ends
        for ix, iu, sx in ((0, 0, -1), (NX, 8, 1)):
            face_dir(R, [T[0][ix], T[1][ix], Uv[1][iu], Sk[ix]], pr, (sx, 0, 0))
            face_dir(R, [Sk[ix], Uv[1][iu], Uv[0][iu]], pr, (sx, 0, 0))
            for a in range(1, len(ys) - 1):
                face_dir(R, [T[a][ix], T[a + 1][ix], Uv[a + 1][iu], Uv[a][iu]], pr, (sx, 0, 0))
    # ridge (two stacked layers, ends turned up) + onigawara + gold gegyo
    pr = P("roof_tile", (1.6, 1.5), grain=(1, 0, 0))

    def sori(x):
        return 0.25 * (abs(x) / 12.6) ** 3
    beam_x(R, -12.6, 12.6, 0.88, 20.7, 0.66, sori, pr, label="ridge")
    beam_x(PL, -12.45, 12.45, 0.66, 21.3, 0.22, sori, P("plaster", 4.0), label="ridge_band")
    beam_x(R, -12.3, 12.3, 0.52, 21.47, 0.28, sori, pr, label="ridge_top")
    oni = [(-1.1, 20.45), (1.1, 20.45), (1.1, 21.4), (0.8, 22.0), (0.45, 21.66), (0.0, 21.84), (-0.45, 21.66),
           (-0.8, 22.0), (-1.1, 21.4)]
    gegyo = [(0.0, 19.05), (0.5, 19.35), (0.75, 19.85), (0.45, 20.2), (0.0, 20.0), (-0.45, 20.2), (-0.75, 19.85),
             (-0.5, 19.35)]
    for sx in (-1, 1):
        extrude_poly(R, oni, (sx * 13.15, 0.0, 0.0), (0, sx, 0), (0, 0, 1), 0.6, pr, label="onigawara")
        extrude_poly(L, gegyo, (sx * 13.14, 0.0, 0.0), (0, sx, 0), (0, 0, 1), 0.14, P("gold", 1.5), label="gegyo")
    # hafu (bargeboards) along both gable edges, over the apex
    pl = P("lacquer", 6.0)
    for sx in (-1, 1):
        ys = [-hy, -6.6, -4.2, -2.0, 0.0, 2.0, 4.2, 6.6, hy]
        pts = [Vector((sx * 12.98, y, gate_roof_z(sx * 12.98, y) - th - 0.22)) for y in ys]
        sweep(L, pts, [(0.78, 0.32)] * len(pts), pl, label="hafu")
    # rafters under both eaves, white-painted ends
    pend = paint_ends(pl, "cut_wood", (0, 1, 0), 0.8)
    for sgn in (-1, 1):
        for k in range(13):
            x = -12.0 + 2.0 * k
            pts = [Vector((x, sgn * y, gate_roof_z(x, sgn * y) - th - 0.17)) for y in (2.2, 5.6, hy - 0.12)]
            sweep(L, pts, [(0.42, 0.34)] * 3, pend, label="rafter")


def gen_gate():
    mid = "tengu_gate"
    rnd = random.Random(hash_seed(mid))
    parts = {k: Geo() for k in ("Lacquer", "Wood", "Roof", "Plaster", "Stone", "Rock", "Rope", "DoorL", "DoorR", "_Glow")}
    L, W, R, PL, S, RK, RP, G = (parts[k] for k in ("Lacquer", "Wood", "Roof", "Plaster", "Stone", "Rock", "Rope", "_Glow"))
    YF, YR, PX = GATE_YF, GATE_YR, GATE_PX
    pl = P("lacquer", 6.0)
    pls = pl.but(smooth=True)
    pwhite = paint_ends(pl, "cut_wood", (1, 0, 0), 0.9)
    pwd = P("wood_dark", 4.4, grain=Z)
    pplaster = P("plaster", 8.0)
    ps = P("stone_grey", 5.0, rnd=rnd, fn=moss_fn(rnd, 0.35, 0.55, "stone_warm", 0.3))
    # ---- pillars, footings, beams ----
    for sx in (-1, 1):
        lathe(S, [(1.2, -0.6), (1.2, 0.3), (1.02, 0.5), (0.0, 0.5)], 10, ps, c=(sx * PX, YF, 0.0),
              jit=_jit(rnd, 0.04, 0.02), label="footing")
        lathe(L, [(0.95, 0.4), (0.95, 18.05)], 14, pls, c=(sx * PX, YF, 0.0), cap0=False, cap1=False, label="pillar")
        lathe(S, [(0.95, -0.5), (0.95, 0.2), (0.8, 0.38), (0.0, 0.38)], 10, ps, c=(sx * PX, YR, 0.0),
              jit=_jit(rnd, 0.03, 0.02), label="footing_rear")
        lathe(L, [(0.6, 0.3), (0.6, 18.05)], 10, pls, c=(sx * PX, YR, 0.0), cap0=False, cap1=False, label="rear_post")
        for z0, z1 in ((12.4, 13.1), (15.9, 16.6)):
            block(L, (sx * PX - 0.32, YF, z0), (sx * PX + 0.32, YR, z1), pl, label="tie_beam")
        block(L, (sx * PX - 0.95, YF - 0.95, 17.45), (sx * PX + 0.95, YF + 0.95, 18.06), pl, label="daito")
        block(L, (sx * PX - 0.7, YR - 0.7, 17.45), (sx * PX + 0.7, YR + 0.7, 18.06), pl, label="daito_rear")
    block(W, (-7.1, YF - 0.2, 0.0), (7.1, YF + 0.6, 0.45), pwd.but(grain=(1, 0, 0)), label="threshold")
    block(L, (-7.1, YF - 0.45, 13.4), (7.1, YF + 0.5, 14.2), pl, label="kamoi")
    block(PL, (-7.1, YF - 0.12, 14.2), (7.1, YF + 0.2, 15.8), pplaster, label="panel")
    block(L, (-9.7, YF - 0.5, 15.8), (9.7, YF + 0.5, 16.7), pwhite, label="kashiranuki")
    block(L, (-PX, YR - 0.33, 15.9), (PX, YR + 0.33, 16.6), pl, label="rear_lintel")
    for y in (YF, YR):
        block(L, (-12.4, y - 0.45, 18.05), (12.4, y + 0.45, 18.82), pwhite, label="keta")
    gable = [(YF, 16.55), (YR, 16.55), (YR, 18.8), (1.25, 19.44), (0.0, 20.12), (-1.25, 19.44), (YF, 18.8)]
    for sx in (-1, 1):
        extrude_poly(PL, gable, (sx * (PX + 0.15), 0.0, 0.0), (0, sx, 0), (0, 0, 1), 0.3, pplaster, label="gable_wall")
    gate_roof(R, L, PL, rnd)
    # ---- doors (separate leaves so the game can swing them open) ----
    D = GATE_DOOR
    crest_c, r_in, r_out = 7.0, 2.05, 2.45
    fea = feather(0.0, crest_c, 3.3, 1.45, shaft=False)
    for key, s in (("DoorL", -1), ("DoorR", 1)):
        g = parts[key]
        xa, xb = (0.03, D["x"]) if s > 0 else (-D["x"], -0.03)
        slab_grid(g, (xa, D["y_front"], D["z0"]), (xb - xa, 0, 0), (0, 0, D["z1"] - D["z0"]), 2, 3,
                  D["y_back"] - D["y_front"], pwd, label="leaf")
        for z0, z1 in ((1.3, 1.8), (11.8, 12.3)):
            block(g, (xa + 0.08, D["y_front"] - 0.12, z0), (xb - 0.08, D["y_front"] + 0.05, z1), P("iron", 4.0), label="band")
            for k in range(4):
                x = xa + (xb - xa) * (0.14 + 0.24 * k)
                stud(g, (x, D["y_front"] - 0.12, (z0 + z1) / 2), (0, -1, 0), 0.17, 0.2, P("gold", 1.0))
        for z in (2.6, 10.9):
            block(g, (xa + 0.3, D["y_back"] - 0.05, z - 0.3), (xb - 0.3, D["y_back"] + 0.22, z + 0.3),
                  pwd.but(grain=(1, 0, 0)), label="batten")
        for x in ((-1.2, -5.0) if s < 0 else (1.2, 5.0)):
            block(g, (x - 0.22, D["y_back"] - 0.05, 6.25), (x + 0.22, D["y_back"] + 0.75, 7.35), P("iron", 4.0), label="bar_bracket")
        n = 12
        a0 = -math.pi / 2 if s > 0 else math.pi / 2
        arc = [a0 + math.pi * k / n for k in range(n + 1)]
        ring = [(s * 0.03 + r_out * math.cos(a), crest_c + r_out * math.sin(a)) for a in arc] + \
               [(s * 0.03 + r_in * math.cos(a), crest_c + r_in * math.sin(a)) for a in reversed(arc)]
        extrude_poly(g, ring, (0.0, D["y_front"] - 0.1, 0.0), (1, 0, 0), (0, 0, 1), 0.14, P("gold", 2.0), back=False,
                     label="crest_ring")
        vane = fea[0] if s > 0 else fea[1]
        extrude_poly(g, [(u + s * 0.03, v) for u, v in vane], (0.0, D["y_front"] - 0.1, 0.0), (1, 0, 0), (0, 0, 1),
                     0.14, P("gold", 2.0), back=False, label="crest_feather")
    block(W, (-6.4, D["y_back"] + 0.1, 6.5), (6.4, D["y_back"] + 0.6, 7.1), pwd.but(grain=(1, 0, 0)), label="kannuki")
    # ---- plaster walls (tsuijibei) ending in mountain rock ----
    pr_tile = P("roof_tile", (1.6, 1.5))
    for sx in (-1, 1):
        x0, x1 = 8.85, 22.6

        def X(a, b):
            return (min(sx * a, sx * b), max(sx * a, sx * b))
        x = x0
        while x < x1 - 0.5:
            Ls = min(rnd.uniform(2.2, 3.6), x1 + 0.6 - x)
            xa, xb = X(x, x + Ls + 0.04)
            chamfer_hexa(S, box_corners((xa, YF - 0.92, -0.6), (xb, YF + 0.92, 1.45 + rnd.uniform(-0.12, 0.2))),
                         0.16, ps, skip=(), jit=_jit(rnd, 0.04, 0.04), label="wall_stone")
            x += Ls
        xa, xb = X(x0, x1)
        block(PL, (xa, YF - 0.75, 1.2), (xb, YF + 0.75, 8.75), pplaster, label="wall")
        for xp in (13.4, 18.2):
            a, b = X(xp - 0.3, xp + 0.3)
            block(W, (a, YF - 0.85, 1.2), (b, YF + 0.85, 8.75), pwd, label="wall_post")
        block(W, (xa, YF - 0.86, 8.6), (xb, YF + 0.86, 9.2), pwd.but(grain=(1, 0, 0)), label="wall_beam")
        Lx = xb - xa
        slab_grid(R, (xa, YF - 1.55, 9.15), (Lx, 0, 0), (0, 1.6, 1.39), 10, 1, 0.2,
                  pr_tile.but(grain=(0, 1, 1.0)), p_back=pwd, p_side=pr_tile, label="coping")
        slab_grid(R, (xb, YF + 1.55, 9.15), (-Lx, 0, 0), (0, -1.6, 1.39), 10, 1, 0.2,
                  pr_tile.but(grain=(0, -1, 1.0)), p_back=pwd, p_side=pr_tile, label="coping")
        block(R, (xa, YF - 0.24, 10.3), (xb, YF + 0.24, 10.72), pr_tile.but(grain=(1, 0, 0)), label="coping_ridge")
        # mountain rock
        prk = P("stone_grey", 9.0, rnd=rnd, fn=moss_fn(rnd, 0.6, 0.55, "stone_warm", 0.25))
        for c, rad, npts in (((28.0, -1.0, 7.0), (6.5, 6.0, 8.5), 26), ((24.0, -3.6, 2.4), (2.9, 2.7, 3.6), 18),
                             ((33.0, 3.0, 9.0), (5.0, 6.0, 10.5), 24), ((25.4, 4.2, 1.8), (3.0, 3.0, 2.9), 16),
                             ((21.6, -5.6, 0.7), (1.6, 1.4, 1.7), 12)):
            rock(RK, (sx * c[0], c[1], c[2]), rad, npts, rnd, prk, zmin=-1.0, label="rock")
    # ---- toro lamp posts flanking the approach ----
    for sx in (-1, 1):
        lantern(S, G, rnd, c=(sx * 11.0, -8.0, 0.0), sc=1.25, psides=8, fsides=6, hsides=4)
    # ---- shimenawa rope with shide (above the barrier space) ----
    n = 17
    xr = 7.55

    def rope_z(x):
        return 16.05 - 0.65 * (1 - (x / xr) ** 2)

    def rope_r(x):
        return 0.27 + 0.33 * (1 - (x / xr) ** 2)
    pts = [Vector((-xr + 2 * xr * k / (n - 1), -3.2, rope_z(-xr + 2 * xr * k / (n - 1)))) for k in range(n)]
    tube(RP, pts, [rope_r(p.x) for p in pts], 8, P("rope", smooth=True), h_ref=2.6, cap0=True, cap1=True, label="shimenawa")
    for x in (-3.6, 0.0, 3.6):
        top = rope_z(x) - rope_r(x) + 0.14
        lathe(RP, [(0.0, top - 1.1), (0.16, top - 0.78), (0.24, top - 0.36), (0.15, top - 0.2), (0.22, top)], 6,
              P("rope", 1.2), c=(x, -3.2, 0.0), label="tassel")
    for x in (-5.4, -1.8, 1.8, 5.4):
        r = rope_r(x)
        shide(RP, x, -3.2 - 0.85 * r - 0.04, rope_z(x) - 0.3 * r, 0.3, 0.4, 4, P("plaster", 2.0))
    # ---- colliders ----
    cols = [[[0.0, -2.475, 6.7], [14.2, 0.55, 13.4], 0.0],                 # closed doors (+ threshold)
            [[0.0, YF, 15.05], [19.4, 1.0, 3.3], 0.0]]                     # lintel / panel / head beam
    for sx in (-1, 1):
        cols += [[[sx * PX, YF, 9.0], [1.9, 1.9, 18.0], 0.0], [[sx * PX, YF, 0.0], [2.4, 2.4, 1.0], 0.0],
                 [[sx * PX, YR, 9.0], [1.2, 1.2, 18.0], 0.0],
                 [[sx * 15.72, YF, 5.0], [13.75, 1.84, 10.7], 0.0],
                 [[sx * 11.0, -8.0, 4.35], [2.6, 2.4, 8.7], 0.0]]
    for c, s in (((28.0, -1.0, 6.8), (10.4, 9.6, 15.6)), ((24.0, -3.6, 2.2), (4.6, 4.3, 6.4)),
                 ((33.0, 3.0, 8.7), (8.0, 9.6, 19.4)), ((25.4, 4.2, 1.5), (4.8, 4.8, 5.0))):
        for sx in (-1, 1):
            cols.append([[sx * c[0], c[1], c[2]], list(s), 0.0])
    cols.append([[0.0, 0.0, 19.4], [26.0, 18.0, 3.6], 0.0])                 # roof
    D = GATE_DOOR
    info = dict(gate_width_roof=26.0, height=22.0,
                barrier_clear_space=dict(min=list(GATE_BARRIER[0]), max=list(GATE_BARRIER[1]),
                                         note="kept empty for the barrier effect: 2 studs deep in front of the closed "
                                              "doors' frontmost relief (iron studs at y=-3.07; planks at y=-2.75), "
                                              "between the pillar footings, up to the door top"),
                doors=dict(DoorL=dict(x_range=[-D["x"], -0.03], hinge_axis_xy=[-D["x"], -2.475]),
                           DoorR=dict(x_range=[0.03, D["x"]], hinge_axis_xy=[D["x"], -2.475]),
                           z_range=[D["z0"], D["z1"]], front_face_y=D["y_front"], back_face_y=D["y_back"],
                           note="each leaf is its own mesh; rotate about the vertical hinge axis to open "
                                "(remove tengu_gate_Wood's kannuki bar first, or hide it)"),
                lamp_posts=dict(positions=[[-11.0, -8.0, 0.0], [11.0, -8.0, 0.0]], height=8.75,
                                glow_mesh="tengu_gate__Glow"),
                walls="plaster walls x = +-8.85..22.6 (y = -3.25..-1.75), 10.7 tall, end inside the mountain rock")
    return dict(parts=parts, colliders=cols, info=info)


# ======================================================================================
# 6. MODEL ASSEMBLY
# ======================================================================================
def part_name(mid, key):
    return mid + "_" + key                     # key "_Glow" -> "<id>__Glow"


def make_model(mid, data):
    col = new_collection(mid)
    mat = get_atlas_material()
    objs = {}
    for key, g in data["parts"].items():
        if g.ntris() == 0:
            continue
        name = part_name(mid, key)
        check_uvs(g, name)
        objs[key] = mesh_from_geo(name, g, mat, col)
    geos = [g for g in data["parts"].values() if g.ntris()]
    mn, mx = geo_bbox(*geos)
    tri = {part_name(mid, k): g.ntris() for k, g in data["parts"].items() if g.ntris()}
    tri["total"] = sum(tri.values())
    usage = {}
    for g in geos:
        for t in g.tile:
            usage[t] = usage.get(t, 0) + 1
    rec = dict(collection=mid, bbox_min=mn, bbox_max=mx, objects=[o.name for o in objs.values()],
               triangles=tri, budget=BUDGET[mid], info=data.get("info", {}), tile_usage=usage,
               geometry_sha1={part_name(mid, k): geo_hash(g) for k, g in data["parts"].items() if g.ntris()})
    STATE["models"][mid] = rec
    STATE["colliders"][mid] = [[[round(v, 3) for v in c], [round(v, 3) for v in s], round(yaw, 2)]
                               for c, s, yaw in data["colliders"]]
    # ---- geometry self-checks ----
    ground = data.get("ground")
    rec["checks"] = dict(
        facing=facing_check(geos, ground=ground),
        floating=floating_check({part_name(mid, k): g for k, g in data["parts"].items() if g.ntris()}, ground=ground),
        edges={part_name(mid, k): edge_stats(g) for k, g in data["parts"].items() if g.ntris()},
        max_uv_anisotropy=round(max(uv_stretch(g) for g in geos), 3),
        ngons=0)
    return col, objs


def finalize_sizes():
    for mid, r in STATE["models"].items():
        mn, mx = r["bbox_min"], r["bbox_max"]
        r["size"] = [round(mx[0] - mn[0], 2), round(mx[2] - mn[2], 2), round(mx[1] - mn[1], 2)]
        r["bbox_min"] = [round(v, 3) for v in mn]
        r["bbox_max"] = [round(v, 3) for v in mx]


def colours_for(mid):
    used = {}
    for t, n in sorted(STATE["models"][mid]["tile_usage"].items(), key=lambda kv: -kv[1]):
        used[TILE_PAL[t] + " (tile %s, %d tris)" % (t, n)] = list(PAL[TILE_PAL[t]])
    return used


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
    objs = [o for o in bpy.data.collections[mid].objects if o.type == "MESH"]
    select_only(objs)
    path = os.path.join(OUT, mid + ".fbx")
    bpy.ops.export_scene.fbx(
        filepath=path, use_selection=True, object_types={"MESH"},
        apply_unit_scale=True, apply_scale_options="FBX_SCALE_UNITS", global_scale=1.0,
        axis_forward="-Z", axis_up="Y", use_mesh_modifiers=False, mesh_smooth_type="OFF",
        add_leaf_bones=False, bake_anim=False, path_mode="COPY", embed_textures=True, use_custom_props=False)
    return path


def export_glb(ids):
    objs = [o for mid in ids for o in bpy.data.collections[mid].objects if o.type == "MESH"]
    select_only(objs)
    path = os.path.join(OUT, BATCH + ".glb")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True,
                              export_apply=False, export_animations=False,
                              export_yup=True, export_materials="EXPORT")
    return path


# ======================================================================================
# 8. RENDERS (Workbench, studio light, backface culling ON, light grey background)
# ======================================================================================
BG = (214, 214, 212)


def human_geo():
    """5-stud tall human silhouette (scale reference, renders only)."""
    g = Geo()
    p = P("iron")
    block(g, (-0.6, -0.25, 0.0), (-0.06, 0.25, 2.1), p)
    block(g, (0.06, -0.25, 0.0), (0.6, 0.25, 2.1), p)
    block(g, (-0.75, -0.35, 2.05), (0.75, 0.35, 3.85), p)
    block(g, (-1.21, -0.21, 2.03), (-0.79, 0.21, 3.78), p)
    block(g, (0.79, -0.21, 2.03), (1.21, 0.21, 3.78), p)
    block(g, (-0.475, -0.475, 3.84), (0.475, 0.475, 5.0), p)
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
    hg = human_geo()
    hum = mesh_from_geo("_human", hg, get_flat_material("Human", (52, 56, 66)), hcol)
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


def fit_ortho(cam, direction, pts, margin=1.12):
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
    cam.data.ortho_scale = max(w, h) * margin
    cam.data.clip_start = 1.0
    cam.data.clip_end = 10000.0
    cam.data.sensor_fit = "AUTO"


VIEWS = {"front": (0, -1, 0), "back": (0, 1, 0), "left": (-1, 0, 0), "right": (1, 0, 0),
         "three_quarter": (1, -1, 1.1)}


def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_model_views(mid, cam, hum):
    rec = STATE["models"][mid]
    mn, mx = list(rec["bbox_min"]), list(rec["bbox_max"])
    hum.location = (mx[0] + 2.5, mn[1] + 1.0, 0.0)
    if mid == "stone_stairs":
        hum.location = (mx[0] + 2.5, -8.75, 0.0)
    pts = [o.matrix_world @ v.co for o in bpy.data.collections[mid].objects if o.type == "MESH"
           for v in o.data.vertices]
    pts += [hum.location + Vector((sx, sy, sz)) for sx in (-1.3, 1.3) for sy in (-0.6, 0.6) for sz in (0.0, 5.0)]
    set_visible({mid, "_render_helpers"})
    out = []
    for vname, d in VIEWS.items():
        fit_ortho(cam, d, pts, 1.08)
        p = os.path.join(REN, "%s_%s.png" % (mid, vname))
        render_to(p)
        im = Image.open(p).convert("RGB")
        dr = ImageDraw.Draw(im)
        dr.text((12, 10), "%s - %s   (human = 5 studs)" % (mid, vname.replace("_", " ")), fill=(40, 40, 40),
                font=_font(16))
        im.save(p)
        out.append(os.path.relpath(p, OUT))
    hum.location = (0.0, 0.0, 0.0)
    return out


def place_collection(mid, loc):
    for o in bpy.data.collections[mid].objects:
        o.location = loc


def render_lineup(path, rows, cam, hum, title):
    """Rows of models rendered front-on (orthographic) at ONE common scale, each row with a
    5-stud human silhouette, stacked into a single 1024x1024 sheet with labels."""
    gap = 6.0
    layouts, maxw, sumh = [], 0.0, 0.0
    for row in rows:
        x = 3.0 + gap
        lay = [("human", 1.5, 3.0)]
        for mid in row:
            r = STATE["models"][mid]
            w = r["bbox_max"][0] - r["bbox_min"][0]
            lay.append((mid, x - r["bbox_min"][0], w))
            x += w + gap
        rowh = max(STATE["models"][m]["bbox_max"][2] + 1.0 for m in row)
        layouts.append((lay, x - gap, rowh))
        maxw = max(maxw, x - gap)
        sumh += rowh
    label_px = 32
    scale = min(1000.0 / maxw, (1024 - 60 - len(rows) * (label_px + 14)) / sumh)
    sheet = Image.new("RGB", (1024, 1024), BG)
    dr = ImageDraw.Draw(sheet)
    dr.text((14, 6), title, fill=(40, 40, 40), font=_font(20))
    ycur = 56
    for lay, roww, rowh in layouts:
        names = [m for m, _, _ in lay if m != "human"]
        set_visible(set(names) | {"_render_helpers"})
        for m, x0, w in lay:
            if m == "human":
                hum.location = (x0, -3.0, 0.0)
            else:
                place_collection(m, (x0, 0.0, 0.0))
        hpx = int(math.ceil((rowh + 1.5) * scale))
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = 1024 / scale
        cam.data.clip_end = 10000.0
        cam.data.sensor_fit = "HORIZONTAL"
        aim(cam, (0, -1, 0), (roww / 2.0, 0.0, (rowh + 1.5) / 2.0 - 1.5), 4000.0)
        tmp = os.path.join(REN, "_row.png")
        render_to(tmp, (1024, hpx))
        sheet.paste(Image.open(tmp).convert("RGB"), (0, ycur))
        os.remove(tmp)
        y_lab = ycur + hpx + 2
        for m, x0, w in lay:
            if m == "human":
                continue
            r = STATE["models"][m]
            cxpx = 512 + ((x0 + (r["bbox_min"][0] + r["bbox_max"][0]) / 2) - roww / 2.0) * scale
            sz = r["size"]
            for li, (txt, f) in enumerate(((m, _font(13)), ("%g x %g x %g" % (sz[0], sz[1], sz[2]), _font(11)))):
                tw = dr.textlength(txt, font=f)
                tx = min(max(cxpx - tw / 2, 4), 1020 - tw)
                dr.text((tx, y_lab + li * 14), txt, fill=(30, 30, 30), font=f)
        ycur = y_lab + label_px
        for m, x0, w in lay:
            if m != "human":
                place_collection(m, (0.0, 0.0, 0.0))
    dr.text((14, 32), "human silhouette = 5 studs (left of each row); one common scale: %.2f px/stud; "
                      "size = x * height * depth" % scale, fill=(60, 60, 60), font=_font(13))
    sheet.save(path)
    hum.location = (0.0, 0.0, 0.0)
    cam.data.sensor_fit = "AUTO"
    return scale


# ======================================================================================
# 9. PATH TEST SCENE + EXPORT VERIFICATION
# ======================================================================================
def ground_z(x, y):
    """Test-scene terrain: flat approach, the stair slope (0.4), the gate landing, side banks."""
    if y < -10.0:
        z = 0.0
    elif y < 50.0:
        z = 0.4 * (y + 10.0)
    else:
        z = 24.0
    ax = abs(x)
    bank = max(0.0, ax - 15.0) * 0.32 + max(0.0, ax - 34.0) * 0.5
    if y > 50.0:
        bank = max(0.0, ax - 38.0) * 0.6
    return z + bank


def standin_maple(rng, base, H, W):
    """Render-only stand-in of the batch A maples (flat palette colours, NOT exported):
    a tapered trunk, limbs to 6-8 clusters of two-sided leaf cards."""
    trunk = Geo()
    leaves = {k: Geo() for k in ("maple_red", "maple_orange", "maple_amber")}
    bx, by, bz = base
    top = Vector((bx + rng.uniform(-0.8, 0.8), by + rng.uniform(-0.8, 0.8), bz + 0.42 * H))
    P0 = P("iron")
    loft(trunk, [ring_xy((bx, by), 1.5, 7, bz - 0.8), ring_xy((bx, by), 1.15, 7, bz + 1.0),
                 ring_xy((top.x, top.y), 0.7, 7, top.z)], P0, cap0=False)
    n = rng.randint(6, 8)
    cols = list(leaves)
    w = [0.6, 0.3, 0.1] if rng.random() < 0.6 else [0.25, 0.6, 0.15]
    for k in range(n):
        a = 2 * math.pi * (k + rng.uniform(-0.3, 0.3)) / n
        rr = W * 0.5 * rng.uniform(0.45, 0.85)
        c = Vector((bx + rr * math.cos(a), by + rr * math.sin(a), bz + H * rng.uniform(0.55, 0.85)))
        if k == 0:
            c = Vector((top.x, top.y, bz + 0.92 * H))
        cyl(trunk, top, c, 0.55, 0.2, 6, P0, cap0=False)
        for _ in range(46):
            d = _rand_unit(rng)
            p = c + Vector((d.x * 4.2, d.y * 4.2, d.z * 2.2)) * math.sqrt(rng.random())
            nrm = (Z * 0.7 + _rand_unit(rng) * 0.6).normalized()
            t = any_perp(nrm)
            s = nrm.cross(t)
            L = rng.uniform(2.2, 3.0)
            col = rng.choices(cols, w)[0]
            g = leaves[col]
            q = [g.v(p + (t * x + s * y) * L / 2) for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
            uv = (0.5, 0.5)
            for tri in ((q[0], q[1], q[2]), (q[0], q[2], q[3]), (q[0], q[2], q[1]), (q[0], q[3], q[2])):
                g.add_tri(*tri, uv, uv, uv, col, False)
    return trunk, leaves


def build_path_scene():
    """3 stair segments climbing a slope, a torii every 30 studs, lanterns in pairs, banners,
    the roadside shrine and the gate on the landing at the top (renders only)."""
    col = new_collection("_path_scene")
    rng = random.Random(2024)
    nx, ny = 56, 72
    xs = [-70.0 + 140.0 * i / nx for i in range(nx + 1)]
    ys = sorted(set([round(-70.0 + 180.0 * j / ny, 4) for j in range(ny + 1)] + [-10.0, 50.0]))
    noise = {}
    for x in xs:
        for y in ys:
            noise[(x, y)] = rng.uniform(-0.6, 0.6) * min(1.0, max(0.0, abs(x) - 16.0) / 8.0)
    for name, keep, matname, rgb in (("_terrain_corridor", lambda cx: abs(cx) < 12.5, "_ScnPath", (154, 132, 100)),
                                     ("_terrain_banks", lambda cx: abs(cx) >= 12.5, "_ScnGrass", (118, 142, 86))):
        g = Geo()
        V = {}
        for i in range(len(xs) - 1):
            cx = (xs[i] + xs[i + 1]) / 2
            if not keep(cx):
                continue
            for j in range(len(ys) - 1):
                q = []
                for x, y in ((xs[i], ys[j]), (xs[i + 1], ys[j]), (xs[i + 1], ys[j + 1]), (xs[i], ys[j + 1])):
                    if (x, y) not in V:
                        z = ground_z(x, y) + noise[(x, y)] - (0.05 if abs(x) < 8.0 and -10 < y < 50 else 0.0)
                        V[(x, y)] = g.v((x, y, z))
                    q.append(V[(x, y)])
                uv = (0.5, 0.5)
                g.add_tri(q[0], q[1], q[2], uv, uv, uv, "x", False)
                g.add_tri(q[0], q[2], q[3], uv, uv, uv, "x", False)
        mesh_from_geo(name, g, get_flat_material(matname, rgb), col)
    # stand-in autumn maples along the banks (render only)
    mcol = {"maple_red": PAL["maple_red"], "maple_orange": PAL["maple_orange"], "maple_amber": PAL["maple_amber"]}
    k = 0
    for x, y in ((-26, -50), (-31, -18), (-24, 8), (-30, 34), (-36, 58), (-46, 88), (32, 44), (40, 66),
                 (48, 94), (14, 100), (-14, 104), (30, 20)):
        H = rng.uniform(34.0, 44.0)
        trunk, leaves = standin_maple(rng, (float(x), float(y), ground_z(x, y)), H, H * 1.1)
        mesh_from_geo("_maple%02d_trunk" % k, trunk, get_flat_material("_ScnBark", PAL["bark_dark"]), col)
        for c, g in leaves.items():
            if g.ntris():
                mesh_from_geo("_maple%02d_%s" % (k, c), g, get_flat_material("_Scn_" + c, mcol[c]), col)
        k += 1

    def put(mid, loc, rz=0.0):
        for o in bpy.data.collections[mid].objects:
            ob = bpy.data.objects.new("_scn_" + o.name, o.data)
            ob.matrix_world = Matrix.Translation(Vector(loc)) @ Matrix.Rotation(math.radians(rz), 4, "Z")
            col.objects.link(ob)
    layout = []
    for s in range(3):
        put("stone_stairs", (0.0, 20.0 * s, 8.0 * s))
        layout.append(["stone_stairs", [0.0, 20.0 * s, 8.0 * s]])
    for y, z in ((-35.5, 0.0), (-5.5, 2.0), (24.5, 14.0), (54.5, 24.0)):     # 30 studs apart, on treads
        put("torii", (0.0, y, z))
        layout.append(["torii", [0.0, y, z]])
    for y in range(-45, 61, 15):
        for sx in (-1, 1):
            z = ground_z(sx * 11.0, y) - (0.35 if -10 < y < 50 else 0.0)
            put("toro_lantern", (sx * 11.0, float(y), z))
            layout.append(["toro_lantern", [sx * 11.0, float(y), round(z, 2)]])
    put("tengu_gate", (0.0, 80.0, 24.0))
    layout.append(["tengu_gate", [0.0, 80.0, 24.0]])
    for x, y in ((-15.5, 62.0), (15.5, 62.0), (-13.0, -20.0), (13.0, -20.0)):
        put("tengu_banner", (x, y, ground_z(x, y)))
        layout.append(["tengu_banner", [x, y, ground_z(x, y)]])
    put("roadside_shrine", (-17.0, -27.0, 0.0), rz=90.0)
    layout.append(["roadside_shrine", [-17.0, -27.0, 0.0], "rotated 90 deg (faces the path)"])
    return col, layout


def render_path_scene(cam, hum):
    col, layout = build_path_scene()
    set_visible({"_path_scene"})
    sc = bpy.context.scene
    sh = sc.display.shading
    sh.use_world_space_lighting = True
    sh.studiolight_rotate_z = math.radians(-35.0)
    sh.show_shadows = False
    cam.data.type = "PERSP"
    cam.data.lens = 26.0
    cam.data.sensor_fit = "AUTO"
    cam.data.clip_start = 0.5
    cam.data.clip_end = 5000.0
    outs = []
    for name, loc, tgt in (("path_scene.png", (44.0, -84.0, 66.0), (0.0, 26.0, 12.0)),
                           ("path_scene_climb.png", (9.0, -58.0, 9.5), (0.0, 30.0, 16.0))):
        cam.location = Vector(loc)
        cam.rotation_euler = (Vector(tgt) - cam.location).to_track_quat("-Z", "Y").to_euler()
        p = os.path.join(REN, name)
        render_to(p)
        im = Image.open(p).convert("RGB")
        dr = ImageDraw.Draw(im)
        dr.rectangle([0, 0, 1024, 30], fill=BG)
        dr.text((12, 7), "path test scene: 3 stair segments (rise 8 / run 20 each), torii every 30 studs, "
                         "lanterns in pairs every 15, gate on the landing", fill=(30, 30, 30), font=_font(14))
        im.save(p)
        outs.append(os.path.relpath(p, OUT))
    # side elevation (orthographic) to show the stair tiling and the slope: banks and maples hidden
    sh.show_shadows = False
    for o in col.objects:
        if o.name.startswith(("_terrain_banks", "_maple")):
            o.hide_render = True
    pts = [Vector((0, -50, 0)), Vector((0, 105, 46))]
    fit_ortho(cam, (1, 0, 0), pts, 1.02)
    p = os.path.join(REN, "path_scene_side.png")
    render_to(p)
    im = Image.open(p).convert("RGB")
    ImageDraw.Draw(im).text((12, 10), "path test scene - side elevation (from +X, orthographic)", fill=(30, 30, 30),
                            font=_font(16))
    im.save(p)
    outs.append(os.path.relpath(p, OUT))
    sh.use_world_space_lighting = False
    sh.studiolight_rotate_z = 0.0
    col.hide_render = True
    return outs, layout


def verify_exports(ids):
    res = {}
    for mid in ids:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.unit_settings.scale_length = 0.01      # same unit setup as the build scene
        bpy.ops.import_scene.fbx(filepath=os.path.join(OUT, mid + ".fbx"))
        objs = list(bpy.context.scene.objects)
        meshes = [o for o in objs if o.type == "MESH"]
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
        exp_tris = {k: v for k, v in rec["triangles"].items() if k != "total"}
        uvs = all(len(o.data.uv_layers) == 1 for o in meshes)
        res[mid] = dict(objects=sorted(o.name for o in objs), triangles=tris, ngons=ngons, size_studs=size,
                        materials=sorted({s.material.name for o in meshes for s in o.material_slots if s.material}),
                        one_uv_layer=uvs,
                        ok=(tris == dict(sorted(exp_tris.items())) and ngons == 0 and uvs and
                            all(abs(a - b) < 0.05 for a, b in zip(size, rec["size"])) and
                            sorted(o.name for o in objs) == sorted(rec["objects"])))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.join(OUT, BATCH + ".glb"))
    objs = list(bpy.context.scene.objects)
    res["_glb"] = dict(meshes=sorted(o.name for o in objs if o.type == "MESH"),
                       ok=sorted(o.name for o in objs if o.type == "MESH") ==
                       sorted(n for mid in ids for n in STATE["models"][mid]["objects"]))
    return res


# ======================================================================================
# 10. MAIN
# ======================================================================================
def generators():
    g = {}
    for mid, fn in (("torii", "gen_torii"), ("toro_lantern", "gen_lantern"), ("stone_stairs", "gen_stairs"),
                    ("tengu_gate", "gen_gate"), ("roadside_shrine", "gen_shrine"), ("tengu_banner", "gen_banner")):
        if not OPT_ONLY or mid in OPT_ONLY:
            g[mid] = globals()[fn]
    return g


def stairs_tiling_check(g):
    """The top of one segment must meet the bottom of the next: the curb end sections at
    y = -10 and y = +10 must match after the (0, +20, +8) offset, and the treads must climb
    1 stud per 2.5 studs including across the joint."""
    curb = set()
    for s, e, lab in g.piece_ranges():
        if lab == "curb":
            curb.update(i for t in g.tris[s:e] for i in t)
    co = [g.co[i] for i in sorted(curb)]
    lo = sorted((round(p[0], 4), round(p[2], 4)) for p in co if abs(p[1] + STAIR_RUN / 2) < 1e-6)
    hi = sorted((round(p[0], 4), round(p[2] - STAIR_RISE, 4)) for p in co if abs(p[1] - STAIR_RUN / 2) < 1e-6)
    return dict(curb_end_vertices=len(lo), curb_ends_match_after_offset=(lo == hi and len(lo) > 0),
                first_riser_y=-STAIR_RUN / 2, last_tread_ends_y=STAIR_RUN / 2,
                rise_across_joint=STAIR_RISE / STAIR_STEPS)


def main():
    os.makedirs(REN, exist_ok=True)
    if OPT_RENDER:
        for fn in os.listdir(REN):
            if fn.endswith(".png"):
                os.remove(os.path.join(REN, fn))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    sc.unit_settings.scale_length = 0.01
    paint_atlas()
    data = {}
    for mid, fn in generators().items():
        data[mid] = fn()
        make_model(mid, data[mid])
    finalize_sizes()
    ids = list(STATE["models"].keys())
    # ---- budgets + structural checks ----
    problems = []
    for mid in ids:
        r = STATE["models"][mid]
        if r["triangles"]["total"] > r["budget"]:
            problems.append("%s: %d triangles > %d" % (mid, r["triangles"]["total"], r["budget"]))
        ck = r["checks"]
        if ck["facing"]["backfacing_first_hits"]:
            problems.append("%s: %d back-facing first hits" % (mid, ck["facing"]["backfacing_first_hits"]))
        if ck["floating"]["floating"] or ck["floating"]["inverted_volume_pieces"]:
            problems.append("%s: floating/inverted pieces %s" % (mid, ck["floating"]))
        if not -1.0 - 1e-6 <= r["bbox_min"][2] <= 0.0:
            problems.append("%s: lowest point %.3f outside [-1, 0]" % (mid, r["bbox_min"][2]))
        if ck["max_uv_anisotropy"] > 1.6:
            problems.append("%s: UV anisotropy %.2f" % (mid, ck["max_uv_anisotropy"]))
    if "tengu_gate" in data:
        n = box_clear({k: g for k, g in data["tengu_gate"]["parts"].items() if g.ntris()}, *GATE_BARRIER)
        STATE["models"]["tengu_gate"]["checks"]["barrier_space_triangles"] = n
        if n:
            problems.append("tengu_gate: %d triangles inside the barrier space" % n)
    if "stone_stairs" in data:
        t = stairs_tiling_check(data["stone_stairs"]["parts"]["Stone"])
        STATE["models"]["stone_stairs"]["checks"]["tiling"] = t
        if not t["curb_ends_match_after_offset"]:
            problems.append("stone_stairs: segment ends do not tile")
    ident = [o.name for o in bpy.data.objects if any(abs(o.matrix_world[i][j] - (1.0 if i == j else 0.0)) > 1e-6
                                                    for i in range(4) for j in range(4))]
    mods = [o.name for o in bpy.data.objects if len(o.modifiers)]
    if ident or mods:
        problems.append("unapplied transforms %s / modifiers %s" % (ident, mods))
    STATE["checks"] = dict(problems=problems, objects_with_unapplied_transforms=ident, objects_with_modifiers=mods,
                           lowest_point_per_model={m: STATE["models"][m]["bbox_min"][2] for m in ids})
    for mid in ids:
        r = STATE["models"][mid]
        print("%-16s size %-22s tris %5d / %5d  facing-back %d  floating %d" % (
            mid, r["size"], r["triangles"]["total"], r["budget"], r["checks"]["facing"]["backfacing_first_hits"],
            len(r["checks"]["floating"]["floating"])))
    print("PROBLEMS:", problems)
    if problems:
        raise RuntimeError("self-check failed: %s" % problems)
    # ---- export ----
    STATE["checks"]["fbx_determinism_patch"] = make_fbx_deterministic()
    for mid in ids:
        export_fbx(mid)
    export_glb(ids)
    bpy.context.preferences.filepaths.save_version = 0            # no .blend1 backup next to the deliverable
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, BATCH + ".blend"), relative_remap=True, compress=True)
    # ---- renders ----
    renders = {}
    if OPT_RENDER:
        cam, hum, hcol = render_scene_setup()
        for mid in ids:
            renders[mid] = render_model_views(mid, cam, hum)
        rows = [[m for m in ("tengu_gate",) if m in ids],
                [m for m in ("torii", "stone_stairs", "toro_lantern", "roadside_shrine", "tengu_banner") if m in ids]]
        render_lineup(os.path.join(REN, "lineup.png"), [r for r in rows if r], cam, hum,
                      "Batch B lineup - Khusha mountains (front, orthographic)")
        renders["lineup"] = ["renders/lineup.png"]
        if len(ids) == len(MODEL_IDS):
            renders["path_scene"], STATE["path_layout"] = render_path_scene(cam, hum)
    # ---- verify exports ----
    verify = verify_exports(ids) if OPT_VERIFY else {}
    write_reports(ids, renders, verify)
    if OPT_ZIP:
        make_zip()
    print("DONE")


def write_reports(ids, renders, verify):
    rep = {"batch": "B", "title": "Khusha mountains, home of the Tengu", "generator": "build.py",
           "blender": bpy.app.version_string,
           "units": "1 Blender unit = 1 stud; scene.unit_settings.scale_length = 0.01; Z up; front = -Y",
           "size_convention": "[x, height (Blender Z), depth (Blender Y)] in studs, from the mesh bounding box",
           "atlas": {"file": "atlas.png", "size": [ATLAS_PX, ATLAS_PX], "tile_px": TILE_PX, "inset": INSET,
                     "tiles": {k: {"col": c, "row_from_top": r, "kind": TILE_KIND[k], "palette": TILE_PAL[k],
                                   "base_rgb": list(PAL[TILE_PAL[k]]),
                                   "mean_rgb": [round(v, 1) for v in STATE["tile_mean"][k]]}
                               for k, (c, r) in TILES.items()}},
           "models": {}, "checks": STATE["checks"], "export_verification": verify, "renders": renders}
    if STATE.get("path_layout"):
        rep["path_scene_layout"] = STATE["path_layout"]
    for mid in ids:
        r = STATE["models"][mid]
        rep["models"][mid] = {"size": r["size"], "bbox_min": r["bbox_min"], "bbox_max": r["bbox_max"],
                              "triangles": r["triangles"], "budget": r["budget"], "objects": r["objects"],
                              "bones": [], "material": "Atlas (atlas.png, Base Color only, Roughness 0.9)",
                              "colours": colours_for(mid), "colliders": len(STATE["colliders"][mid]),
                              "info": r["info"], "checks": r["checks"], "geometry_sha1": r["geometry_sha1"]}
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(rep, f, indent=1)
    with open(os.path.join(OUT, "colliders.json"), "w") as f:
        json.dump({k: STATE["colliders"][k] for k in ids}, f, indent=1)


def make_zip():
    zp = os.path.join(os.path.dirname(OUT), BATCH + ".zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(OUT):
            dirs.sort()
            for fn in sorted(files):
                if fn.endswith((".blend1", ".pyc")) or "__pycache__" in root:
                    continue
                p = os.path.join(root, fn)
                zi = zipfile.ZipInfo(os.path.join(BATCH, os.path.relpath(p, OUT)), date_time=(2026, 1, 1, 12, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                with open(p, "rb") as fh:
                    z.writestr(zi, fh.read())
    return zp


if __name__ == "__main__":
    main()
