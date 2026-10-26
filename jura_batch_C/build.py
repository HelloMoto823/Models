#!/usr/bin/env python3
"""
Great Forest of Jura -- BATCH C -- the burned Ogre village and the Orc army's trail.

One re-runnable generator: paints the shared texture atlas, models every prop of the
batch procedurally (no manual steps), runs the self-checks, exports FBX / GLB / .blend,
renders the review images (5 views per model, lineup, village scene), re-imports the
exports to verify them and writes report.json + colliders.json (+ the zip).

Run (either works):
    blender -b -P build.py [-- options]      Blender 4.x / 5.x (needs Pillow in Blender's Python)
    python3 build.py [options]               with `pip install bpy pillow` (bpy 5.2 / Python 3.13)

Options:
    --no-render   skip the PNG renders
    --no-verify   skip re-importing the exported files
    --no-zip      do not (re)create ../jura_batch_C.zip
    --only=<id>   dev: build a subset (repeatable)

All outputs are written next to this file (the jura_batch_C/ folder).
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
BATCH = "jura_batch_C"
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
OPT_RENDER = "--no-render" not in _argv
OPT_VERIFY = "--no-verify" not in _argv
OPT_ZIP = "--no-zip" not in _argv
OPT_ONLY = [a.split("=", 1)[1] for a in _argv if a.startswith("--only=")]   # dev: build a subset

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

# 4x4 atlas, 256 px tiles. (col, row) with row 0 = TOP row of the PNG.
TILES = {
    "char_ember": (0, 0), "char": (1, 0), "planks": (2, 0), "planks_dark": (3, 0),
    "timber": (0, 1), "bark": (1, 1), "cut_wood": (2, 1), "thatch": (3, 1),
    "stone": (0, 2), "lacquer": (1, 2), "lacquer_soot": (2, 2), "iron": (3, 2),
    "cloth": (0, 3), "bone": (1, 3), "rope": (2, 3), "ember": (3, 3),
}
TILE_PAL = {   # palette entry painted into each tile (the base colour)
    "char_ember": "charred", "char": "charred", "planks": "bark", "planks_dark": "bark_dark",
    "timber": "bark_light", "bark": "bark", "cut_wood": "cream", "thatch": "straw",
    "stone": "rock_grey", "lacquer": "vermilion", "lacquer_soot": "vermilion", "iron": "dark_iron",
    "cloth": "orc_cloth", "bone": "cream", "rope": "straw", "ember": "ember",
}
TILE_EXTRA = {"char_ember": ["ember"], "lacquer_soot": ["charred"]}     # second palette colour painted in
TILE_KIND = {
    "char_ember": "charred boards: 4 board seams + alligator blocks, ~45 % of the cracks glow ember orange",
    "char": "charred boards: 4 board seams + dark alligator cracks (no ember)",
    "planks": "wood planks (4 board seams + grain)", "planks_dark": "wood planks (4 board seams + grain)",
    "timber": "timber grain", "bark": "bark (vertical grooves)", "cut_wood": "cut wood / splinters (grain)",
    "thatch": "thatch (fine straw strands + 4 courses)", "stone": "stone (soft round facet blotches)",
    "lacquer": "flat", "lacquer_soot": "vermilion lacquer with charred soot patches (~40 %)", "iron": "flat",
    "cloth": "flat", "bone": "flat", "rope": "twisted straw rope (3 strands)",
    "ember": "flat (glowing coals)",
}
ATLAS_PX = 1024
TILE_PX = 256
INSET = 0.04

BUDGET = {"oni_house_a": 4000, "oni_house_b": 4000, "oni_house_c": 4000, "stake_fence": 900,
          "battle_debris_a": 1200, "battle_debris_b": 1200, "torii_broken": 1500, "trampled_log": 900}
BUDGET_CLASS = {"oni_house_a": "house", "oni_house_b": "house", "oni_house_c": "house",
                "stake_fence": "item limit", "battle_debris_a": "item limit", "battle_debris_b": "item limit",
                "torii_broken": "item limit (prop)", "trampled_log": "item limit"}
MODEL_IDS = ["oni_house_a", "oni_house_b", "oni_house_c", "stake_fence", "battle_debris_a", "battle_debris_b",
             "torii_broken", "trampled_log"]

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


def _wrap(d):
    return d - np.round(d)


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


def paint_planks(base, rng, n=4):
    """Vertical planks (dark seams between them) with simple wavy grain; periodic.
    Seams sit at u = (k + 0.5) / 4, the same as on the charred tiles."""
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


def paint_bark(base, rng):
    """Vertical bark grooves (same look as the batch A trunks); periodic."""
    x, y = _grid()
    b = _c(base)
    v = np.ones_like(x)
    ng = 9
    for i in range(ng):
        xc = (i + 0.5 + rng.uniform(-0.25, 0.25)) / ng
        amp, f, ph = rng.uniform(0.004, 0.014), rng.randint(1, 4), rng.uniform(0, 1)
        w = rng.uniform(0.010, 0.019)
        xcy = xc + amp * np.sin(2 * np.pi * (f * y + ph)) + 0.003 * np.sin(2 * np.pi * (7 * y + ph * 3))
        d = _wrap(x - xcy) / w
        v *= 1.0 - 0.42 * np.exp(-d * d)
        v += 0.10 * np.exp(-((d - 1.9) ** 2) / 0.9)
    v += _pnoise(rng, 48, 0.035) + _pnoise(rng, 8, 0.03)
    return _finish(b * v[..., None])


def paint_char(rng, ember):
    """Charred boards. The 4 board seams sit where the plank tiles have theirs, so a board
    keeps its seams across a burn line. Every board is split into alligator blocks by
    wavy cross cracks (blocks lighter at their upper edge, like raised char). On the ember
    tile about 45 % of the cracks glow ember orange with a soft halo."""
    x, y = _grid()
    v_up = 1.0 - y
    n = 4
    b = _c(PAL["charred"])
    e = _c(PAL["ember"])
    f = (x * n + 0.5) % 1.0
    k = np.floor(x * n + 0.5).astype(np.int32) % n
    xl = f - 0.5                                          # -0.5 .. 0.5 across the board
    seam = np.minimum(f, 1.0 - f) / n
    val = np.ones((NS, NS), np.float32)
    dmin = np.full((NS, NS), 9.0, np.float32)             # distance to the nearest crack (tile units)
    gmin = np.full((NS, NS), 9.0, np.float32)             # distance to the nearest glowing crack
    for col in range(n):
        m = k == col
        nc = rng.randint(7, 11)
        vcs = sorted(rng.uniform(0, 1) for _ in range(nc))
        cnt = np.zeros((NS, NS), np.int32)
        above = np.full((NS, NS), 9.0, np.float32)
        for vc in vcs:
            sl, a, fr, ph = rng.uniform(-0.05, 0.05), rng.uniform(0.003, 0.009), rng.randint(1, 3), rng.uniform(0, 1)
            line = vc + sl * xl + a * np.sin(2 * np.pi * (fr * xl + ph))
            sd = _wrap(v_up - line)
            d = np.abs(sd)
            dmin = np.where(m, np.minimum(dmin, d), dmin)
            if ember and rng.rand() < 0.45:
                gmin = np.where(m, np.minimum(gmin, d), gmin)
            cnt += (v_up - line > 0).astype(np.int32)
            up = np.where(sd < 0, -sd, 9.0)
            above = np.minimum(above, up)
        shades = np.array([rng.uniform(0.86, 1.16) for _ in range(nc)], np.float32)
        blk = shades[cnt % nc]
        val = np.where(m, blk * (1.0 + 0.22 * np.exp(-above / 0.018)), val)
    val += _pnoise(rng, 24, 0.05)
    val = np.where(dmin < 0.0055, val * 0.42, val)
    val = np.where(seam < 0.005, val * 0.40, np.where(seam < 0.009, val * 0.75, val))
    img = b * val[..., None]
    if ember:
        halo = np.exp(-(gmin / 0.016) ** 2)
        img = img * (1 - 0.6 * halo[..., None]) + e * (0.6 * halo[..., None])
        core = np.clip((0.0075 - gmin) / 0.002, 0.0, 1.0)
        img = img * (1 - core[..., None]) + _lighten(e, 0.12) * core[..., None]
    return _finish(img)


def paint_thatch(base, rng):
    """Thatch: fine straw strands (v along the slope) laid in 4 courses per tile, each
    course with a ragged shadow line under the strand ends of the course above."""
    x, y = _grid()
    v = 1.0 - y
    b = _c(base)
    ns = 72
    xs = x + 0.004 * np.sin(2 * np.pi * (3 * v + 0.2)) + 0.002 * np.sin(2 * np.pi * (11 * v))
    sidx = np.floor(xs * ns).astype(np.int32) % ns
    fs = (xs * ns) % 1.0
    br = np.array([rng.uniform(-1, 1) for _ in range(ns)], np.float32)
    val = 1.0 + 0.10 * br[sidx]
    val *= 1.0 - 0.13 * (np.abs(fs - 0.5) > 0.36)
    rag = np.array([rng.uniform(0.0, 0.05) for _ in range(ns)], np.float32)[sidx]
    t = (v * 4.0 + rag * 4.0) % 1.0
    val *= 0.70 + 0.30 * np.clip(t / 0.16, 0.0, 1.0)
    val *= np.where((t > 0.16) & (t < 0.26), 1.06, 1.0)
    val += _pnoise(rng, 10, 0.035)
    return _finish(b * val[..., None])


def paint_soot(base, rng):
    """Vermilion lacquer partly covered by charred soot patches (darker rim at the edge)."""
    x, y = _grid()
    lac = np.asarray(paint_flat(base, rng).resize((NS, NS), Image.BICUBIC), np.float32)
    cover = _pnoise(rng, 4, 1.0) * 0.8 + _pnoise(rng, 12, 0.45)
    for _ in range(18):
        cx, cy = rng.uniform(0, 1), rng.uniform(0, 1)
        r = rng.uniform(0.05, 0.14)
        d2 = (_pdist(x, cx) ** 2 + _pdist(y, cy) ** 2) / (r * r)
        cover += 0.45 * np.exp(-d2 * 2.0)
    m = np.clip((cover - 0.02) * 3.2, 0.0, 1.0)[..., None]
    soot = _c(PAL["charred"]) * (1.0 + _pnoise(rng, 30, 0.10) + _pnoise(rng, 6, 0.08))[..., None]
    edge = np.clip(1.0 - np.abs(m - 0.5) * 2.0, 0, 1)
    img = (lac * (1 - m) + soot * m) * (1.0 - 0.18 * edge)
    return _finish(img)


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


def tile_mean(img):
    a = np.asarray(img, np.float32)
    i0, i1 = int(TILE_PX * INSET), int(TILE_PX * (1 - INSET))
    return tuple(float(v) for v in a[i0:i1, i0:i1].reshape(-1, 3).mean(0))


def paint_atlas():
    rng = np.random.RandomState(5151)
    tiles = {}
    for k in TILES:
        rgb = PAL[TILE_PAL[k]]
        if k == "char_ember":
            tiles[k] = paint_char(rng, True)
        elif k == "char":
            tiles[k] = paint_char(rng, False)
        elif k in ("planks", "planks_dark"):
            tiles[k] = paint_planks(rgb, rng)
        elif k in ("timber", "cut_wood"):
            tiles[k] = paint_timber(rgb, rng)
        elif k == "bark":
            tiles[k] = paint_bark(rgb, rng)
        elif k == "thatch":
            tiles[k] = paint_thatch(rgb, rng)
        elif k == "stone":
            tiles[k] = paint_stone(rgb, rng)
        elif k == "lacquer_soot":
            tiles[k] = paint_soot(rgb, rng)
        elif k == "rope":
            tiles[k] = paint_rope(rgb, rng)
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
# 2. GEOMETRY CORE (shared with batch B, plus CDT panels, splintered ends, transforms)
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


def merge(dst, src, M=None):
    """Append src into dst, transformed by the 4x4 matrix M (rotation + translation)."""
    M = M or Matrix.Identity(4)
    flip = M.to_3x3().determinant() < 0
    off = len(dst.co)
    t0 = len(dst.tris)
    for p in src.co:
        q = M @ Vector(p)
        dst.co.append((q.x, q.y, q.z))
    for s, lab in src.pieces:
        dst.pieces.append((t0 + s, lab))
    for (a, b, c), uv, tl, sm in zip(src.tris, src.uv, src.tile, src.smooth):
        if flip:
            dst.add_tri(a + off, c + off, b + off, uv[0], uv[2], uv[1], tl, sm)
        else:
            dst.add_tri(a + off, b + off, c + off, uv[0], uv[1], uv[2], tl, sm)


def geo_minz(g, M=None):
    M = M or Matrix.Identity(4)
    return min((M @ Vector(p)).z for p in g.co)


def rest_on_ground(src, M, sink=0.06):
    """Matrix M shifted vertically so that the lowest vertex of src sits `sink` below z=0."""
    return Matrix.Translation((0.0, 0.0, -sink - geo_minz(src, M))) @ M


def place(loc, rz=0.0, rx=0.0, ry=0.0):
    """Rotation about X, then Y, then Z (degrees), then translation."""
    return (Matrix.Translation(Vector(loc)) @ Matrix.Rotation(math.radians(rz), 4, "Z") @
            Matrix.Rotation(math.radians(ry), 4, "Y") @ Matrix.Rotation(math.radians(rx), 4, "X"))


class P:
    """How faces are painted: atlas tile, texture scale S (studs per tile, u/v), optional
    grain axis (v follows it), optional rnd (random.Random: random window inside the
    tile -> per-facet colour variation on stone), smooth shading, and fn(normal, centre)
    returning a per-face tile override."""

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


def uv_basis(n, grain=None):
    """(right, up) of the planar mapping of a face with normal n: v follows the grain
    (projected), else world up, else world +Y."""
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
    return up.cross(n), up


def planar_uv(pts, n, S, grain=None, rnd=None):
    """Project the face on its own plane and fit it into one tile: world-anchored (the
    pattern continues across faces; the tile period is the one holding the face centre)
    or, with rnd, at a random spot. Faces larger than a tile are scaled down uniformly
    (never stretched)."""
    right, up = uv_basis(n, grain)
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
        uc, vc = sum(us) / len(us), sum(vs) / len(vs)
        ou = min(max(u0 - math.floor(uc), 0.0), 1.0 - eu)
        ov = min(max(v0 - math.floor(vc), 0.0), 1.0 - ev)
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


def face_dir(g, idx, p, want):
    """Add a face, flipping its winding if needed so that it faces `want`."""
    pts = [Vector(g.co[i]) for i in idx]
    if poly_normal(pts).dot(Vector(want)) < 0:
        idx = list(reversed(idx))
    face(g, idx, p)


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
    """Block with chamfered top edges, from 8 corners (top ring may be sloped)."""
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


def loft(g, rings, p, cap0=True, cap1=True, cap_p=None, label="loft", band_p=None, mark=True):
    """Connect rings (lists of points, counter-clockwise about the direction of travel;
    a single Vector = apex). band_p(i) may return a different paint for band i."""
    if mark:
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


def rect_ring8(c, N, B, h, w):
    """Same rectangle with its edge midpoints (8 points) -- for splintered beam ends."""
    q = rect_ring(c, N, B, h, w)
    out = []
    for k in range(4):
        out += [q[k], (q[k] + q[(k + 1) % 4]) / 2]
    return out


def beam(g, a, b, w, h, p, up=Z, cap0=True, cap1=True, cap_p=None, label="beam"):
    """Rectangular beam from a to b: width w (horizontal), height h (along `up`)."""
    a, b = Vector(a), Vector(b)
    T, N, B = frame_along(a, b, Vector(up))
    return loft(g, [rect_ring(a, N, B, h, w), rect_ring(b, N, B, h, w)], p, cap0, cap1, cap_p, label)


def sweep(g, pts, sections, p, ups=None, cap0=True, cap1=True, cap_p=None, label="sweep", ring8=False):
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
        rings.append((rect_ring8 if ring8 else rect_ring)(pts[i], N, B, h, w))
    return loft(g, rings, p, cap0, cap1, cap_p, label)


def tube(g, pts, radii, sides, p, h_ref=3.0, cap0=False, cap1=False, phase=0.0, label="tube", up0=None, band_p=None):
    """Round tube with tube UVs: u around (one tile = circumference), v along the length
    (one tile = h_ref studs); periodic tiles keep the pattern continuous."""
    g.mark(label)
    n = len(pts)
    s = [0.0]
    for i in range(1, n):
        s.append(s[-1] + (pts[i] - pts[i - 1]).length)
    T = [(pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized() for i in range(n)]
    if up0 is not None:
        q = Vector(up0) - T[0] * Vector(up0).dot(T[0])
        N = [q.normalized()]
    else:
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
        pp = band_p(i) if band_p else p
        for j in range(sides):
            u0, u1 = j / sides, (j + 1) / sides
            face(g, [rings[i][j], rings[i][(j + 1) % sides], rings[i + 1][(j + 1) % sides], rings[i + 1][j]], pp,
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


def cyl(g, a, b, r0, r1, sides, p, cap0=True, cap1=True, label="cyl", phase=0.0):
    """Straight (tapered) round bar from a to b with planar UVs."""
    a, b = Vector(a), Vector(b)
    T = (b - a).normalized()
    N = any_perp(T)
    B = T.cross(N)

    def ring(c, r):
        return [c + (N * math.cos(phase + 2 * math.pi * j / sides) + B * math.sin(phase + 2 * math.pi * j / sides)) * r
                for j in range(sides)]
    return loft(g, [ring(a, r0), ring(b, r1)], p, cap0, cap1, label=label)


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
    """Paint override: faces whose normal is (anti)parallel to `axis` get `end_tile`."""
    axis = Vector(axis).normalized()
    base_fn = p.fn

    def fn(n, c):
        if abs(n.dot(axis)) > thresh:
            return end_tile
        return base_fn(n, c) if base_fn else None
    return p.but(fn=fn)


def _jit(rnd, xy, z):
    return lambda q: Vector((rnd.uniform(-xy, xy), rnd.uniform(-xy, xy), rnd.uniform(-z, z)))


# --------------------------------------------------------------------------------------
# new in batch C: splintered ends, CDT panels (outline + holes + burn regions), jagged lines
# --------------------------------------------------------------------------------------
def splinter_end(g, ring, axis, rnd, h=(0.3, 1.2), p_out=None, p_in=None, inward=0.25, core=0.1, tip_h=None,
                 wobble=1.0):
    """Close the open end ring (vertex indices, counter-clockwise about `axis`, the
    direction pointing out of the end) with a crown of splinter teeth around a broken
    core: per rim edge one tooth (outer face p_out, two inner faces p_in). Tooth bases,
    depths and the core are jittered (wobble) so the break never reads as a regular star.
    Closed."""
    axis = Vector(axis).normalized()
    pts = [Vector(g.co[i]) for i in ring]
    m = len(pts)
    c = sum(pts, Vector()) / m
    rad = sum((q - c).length for q in pts) / m
    e1 = any_perp(axis)
    e2 = axis.cross(e1)
    a = rnd.uniform(0.0, 2.0 * math.pi)
    off = (e1 * math.cos(a) + e2 * math.sin(a)) * (rad * rnd.uniform(0.05, 0.3) * wobble)
    C = g.v(c + off + axis * (core * rad * (1.0 + rnd.uniform(-0.4, 0.8) * wobble)))
    T = []
    for j in range(m):
        q0, q1 = pts[j], pts[(j + 1) % m]
        mid = q0.lerp(q1, 0.5 + rnd.uniform(-0.28, 0.28) * wobble)
        hh = tip_h[j] if tip_h else rnd.uniform(*h)
        inw = inward * (1.0 + rnd.uniform(-0.6, 0.9) * wobble)
        T.append(g.v(mid + (c + off - mid) * inw + axis * (hh * rad)))
    for j in range(m):
        j1 = (j + 1) % m
        face(g, [ring[j], ring[j1], T[j]], p_out)
        face(g, [T[j], ring[j1], C], p_in)
        face(g, [ring[j], T[j], C], p_in)
    return T


def pip(pt, poly):
    """Point in polygon (2D, even-odd)."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def subdiv(poly, maxlen, closed=True):
    out = []
    n = len(poly)
    for i in range(n if closed else n - 1):
        a, b = poly[i], poly[(i + 1) % n]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(math.ceil(L / maxlen)))
        for j in range(k):
            out.append((a[0] + (b[0] - a[0]) * j / k, a[1] + (b[1] - a[1]) * j / k))
    if not closed:
        out.append(poly[-1])
    return out


def jagged(a, b, n, amp, rnd, ends=True):
    """Polyline from a to b with n inner points offset sideways by +-amp (a ragged edge)."""
    ax, ay = a
    bx, by = b
    L = math.hypot(bx - ax, by - ay)
    nx, ny = -(by - ay) / L, (bx - ax) / L
    pts = [a] if ends else []
    for k in range(1, n + 1):
        t = (k + rnd.uniform(-0.3, 0.3)) / (n + 1)
        o = rnd.uniform(-amp, amp)
        pts.append((ax + (bx - ax) * t + nx * o, ay + (by - ay) * t + ny * o))
    if ends:
        pts.append(b)
    return pts


def panel(g, outline, o, U, V, depth, p, holes=(), regions=(), side=None, back=None, pos=None, cells=True,
          label="panel", cut_extra=(), ext=None):
    """Thick slab from a 2D outline (s along U, t along V), front normal U x V, extruded
    back by depth. Holes are cut out; regions = [(poly, paint)] repaint the triangles
    inside them (first match wins, on both sides). The front/back faces are also cut
    along the atlas tile borders of their world-anchored planar UVs, so every triangle
    maps inside one tile period and the texture runs on seamlessly over the whole slab.
    side: paint (or fn(s, t) -> paint) of the rim faces (default: the paint of the face each
    rim face borders, so burned regions wrap over the cut edges); back: paint of the back face
    (default: same as the front); pos(s, t) -> 3D point bends the slab (sheets, cloth);
    ext (a vector) extrudes the back by that offset instead of depth along the normal
    (thatch slabs use a vertical drop, so hips and ridges meet flush)."""
    o, U, V = Vector(o), Vector(U), Vector(V)
    N0 = U.cross(V).normalized()
    bent = pos is not None
    if pos is None:
        def pos(s, t):
            return o + U * s + V * t
    outline = ccw2([tuple(q) for q in outline])
    holes = [ccw2([tuple(q) for q in h]) for h in holes]
    verts, faces, edges = [], [], []

    def add_poly(poly):
        faces.append(list(range(len(verts), len(verts) + len(poly))))
        verts.extend(poly)
    add_poly(outline)
    for h in holes:
        add_poly(h)
    for poly, _ in regions:
        add_poly(ccw2([tuple(q) for q in poly]))
    smin = min(q[0] for q in outline) - 1.0
    smax = max(q[0] for q in outline) + 1.0
    tmin = min(q[1] for q in outline) - 1.0
    tmax = max(q[1] for q in outline) + 1.0
    if cells:
        right, up = uv_basis(N0, p.grain)
        su, sv = p.S
        for axis, sc in ((right, su), (up, sv)):
            a0 = o.dot(axis) / sc
            a1 = U.dot(axis) / sc
            a2 = V.dot(axis) / sc
            vals = [a0 + a1 * s + a2 * t for s in (smin, smax) for t in (tmin, tmax)]
            for kk in range(int(math.floor(min(vals))), int(math.ceil(max(vals))) + 1):
                if abs(a2) >= abs(a1):
                    seg = [(s, (kk - a0 - a1 * s) / a2) for s in (smin, smax)]
                else:
                    seg = [((kk - a0 - a2 * t) / a1, t) for t in (tmin, tmax)]
                edges.append((len(verts), len(verts) + 1))
                verts.extend(seg)
    for seg in cut_extra:
        edges.append((len(verts), len(verts) + 1))
        verts.extend(seg)
    res = mgeo.delaunay_2d_cdt([Vector(q) for q in verts], edges, faces, 0, 1e-5)
    ov, ofaces = res[0], res[2]
    kept = []
    for f in ofaces:
        a, b, c = f
        pa, pb, pc = ov[a], ov[b], ov[c]
        cr = (pb.x - pa.x) * (pc.y - pa.y) - (pb.y - pa.y) * (pc.x - pa.x)
        if abs(cr) < 1e-9:
            continue
        if cr < 0:
            b, c = c, b
        cen = ((pa.x + pb.x + pc.x) / 3, (pa.y + pb.y + pc.y) / 3)
        if not pip(cen, outline) or any(pip(cen, h) for h in holes):
            continue
        paint = p
        for poly, rp in regions:
            if pip(cen, poly):
                paint = rp
                break
        kept.append(((a, b, c), paint))
    used = sorted({i for (t, _) in kept for i in t})
    g.mark(label)
    F, Bk = {}, {}
    hstep = 0.05
    for i in used:
        s, t = ov[i].x, ov[i].y
        q = pos(s, t)
        if bent:
            du = pos(s + hstep, t) - pos(s - hstep, t)
            dv = pos(s, t + hstep) - pos(s, t - hstep)
            Nl = du.cross(dv)
            Nl = Nl.normalized() if Nl.length > 1e-9 else N0
        else:
            Nl = N0
        F[i] = g.v(q)
        Bk[i] = g.v(q - Vector(ext) if ext is not None else q - Nl * depth)
    for (a, b, c), paint in kept:
        face(g, [F[a], F[b], F[c]], paint)
        bp = paint if back is None else (back(paint) if callable(back) else back)
        face(g, [Bk[a], Bk[c], Bk[b]], bp)
    cnt = {}
    for (a, b, c), _ in kept:
        for x, y in ((a, b), (b, c), (c, a)):
            k = (min(x, y), max(x, y))
            cnt[k] = cnt.get(k, 0) + 1
    rim = []
    for (a, b, c), tp in kept:
        for x, y in ((a, b), (b, c), (c, a)):
            if cnt[(min(x, y), max(x, y))] != 1:
                continue
            sm, tm = (ov[x].x + ov[y].x) / 2, (ov[x].y + ov[y].y) / 2
            ds, dt = ov[y].x - ov[x].x, ov[y].y - ov[x].y
            want = U * dt - V * ds
            sp = side(sm, tm) if callable(side) else (side or tp)     # default: the paint of the face it borders
            face_dir(g, [F[x], F[y], Bk[y], Bk[x]], sp, want)
            rim.append(((ov[x].x, ov[x].y), (ov[y].x, ov[y].y)))
    return dict(front=F, back=Bk, coords={i: (ov[i].x, ov[i].y) for i in used}, ntri=len(kept), rim=rim)
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


def _clip_area(t1, t2):
    """Area of the intersection of two 2D triangles (convex clipping)."""
    def ccw(t):
        t = list(t)
        if (t[1][0] - t[0][0]) * (t[2][1] - t[0][1]) - (t[1][1] - t[0][1]) * (t[2][0] - t[0][0]) < 0:
            t = [t[0], t[2], t[1]]
        return t

    def inter(p, q, a, b):
        x1, y1, x2, y2, x3, y3, x4, y4 = p[0], p[1], q[0], q[1], a[0], a[1], b[0], b[1]
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(den) < 1e-12:
            return q
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
        return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    poly, clip = ccw(t1), ccw(t2)
    for i in range(3):
        a, b = clip[i], clip[(i + 1) % 3]

        def inside(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= -1e-9
        out = []
        for k in range(len(poly)):
            p, q = poly[k], poly[(k + 1) % len(poly)]
            if inside(q):
                if not inside(p):
                    out.append(inter(p, q, a, b))
                out.append(q)
            elif inside(p):
                out.append(inter(p, q, a, b))
        poly = out
        if len(poly) < 3:
            return 0.0
    return abs(sum(poly[k][0] * poly[(k + 1) % len(poly)][1] - poly[(k + 1) % len(poly)][0] * poly[k][1]
                   for k in range(len(poly)))) / 2


def coplanar_overlaps(parts, min_area=1e-3):
    """Z-fighting test: triangles of different pieces lying in the same plane, facing the same
    way and overlapping. Returns {"pieceA / pieceB": overlap area}."""
    buckets = {}
    for pname, g in parts.items():
        lab = {}
        for pi, (s0, e0, l0) in enumerate(g.piece_ranges()):
            for t in range(s0, e0):
                lab[t] = (pname, pi, l0)
        for ti, (a, b, c) in enumerate(g.tris):
            A, B, C = Vector(g.co[a]), Vector(g.co[b]), Vector(g.co[c])
            n = (B - A).cross(C - A)
            if n.length < 1e-9:
                continue
            n.normalize()
            key = (round(n.x, 3), round(n.y, 3), round(n.z, 3), round(n.dot(A), 2))
            buckets.setdefault(key, []).append((n, (A, B, C), lab[ti]))
    hits = {}
    for lst in buckets.values():
        if len(lst) < 2:
            continue
        n = lst[0][0]
        e1 = any_perp(n)
        e2 = n.cross(e1)
        pr = [([(q.dot(e1), q.dot(e2)) for q in t], l0) for _, t, l0 in lst]
        for i in range(len(pr)):
            for j in range(i + 1, len(pr)):
                if pr[i][1][:2] == pr[j][1][:2]:
                    continue
                ar = _clip_area(pr[i][0], pr[j][0])
                if ar > min_area:
                    k = " / ".join(sorted(["%s:%s" % (pr[i][1][0], pr[i][1][2]), "%s:%s" % (pr[j][1][0], pr[j][1][2])]))
                    hits[k] = round(hits.get(k, 0.0) + ar, 3)
    return dict(sorted(hits.items()))


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
def rbeam(g, a, b, w, h, p, rnd, up=Z, broken=(False, False), pin=None, label="beam", hh=(0.25, 1.1), seg=None,
          band_p=None, ring8=True):
    """Rectangular beam a -> b (width w, height h along `up`), split every `seg` studs so the
    planar UVs keep one texel density; broken ends become splinter crowns (inner faces pin)."""
    a, b = Vector(a), Vector(b)
    T, N, B = frame_along(a, b, Vector(up))
    rg = rect_ring8 if any(broken) and ring8 else rect_ring
    p = p if p.grain is not None else p.but(grain=T)
    L = (b - a).length
    n = max(1, int(math.ceil(L / seg))) if seg else 1
    rings = [rg(a + (b - a) * (k / n), N, B, h, w) for k in range(n + 1)]
    R = loft(g, rings, p, cap0=not broken[0], cap1=not broken[1], label=label, band_p=band_p)
    pin = pin or p
    if broken[1]:
        splinter_end(g, R[-1], T, rnd, h=hh, p_out=p, p_in=pin)
    if broken[0]:
        splinter_end(g, list(reversed(R[0])), -T, rnd, h=hh, p_out=p, p_in=pin)
    return R


def pole(g, a, b, r0, r1, sides, p, rnd, broken=(False, False), pin=None, label="pole", hh=(0.3, 1.2), seg=None,
         phase=0.0, band_p=None):
    """Round pole a -> b with planar UVs (v along the pole); broken ends splintered."""
    a, b = Vector(a), Vector(b)
    T = (b - a).normalized()
    N = any_perp(T)
    B = T.cross(N)
    p = p.but(grain=T)
    L = (b - a).length
    n = max(1, int(math.ceil(L / seg))) if seg else 1

    def ring(c, r):
        return [c + (N * math.cos(phase + 2 * math.pi * j / sides) + B * math.sin(phase + 2 * math.pi * j / sides)) * r
                for j in range(sides)]
    rings = [ring(a + (b - a) * (k / n), r0 + (r1 - r0) * k / n) for k in range(n + 1)]
    R = loft(g, rings, p, cap0=not broken[0], cap1=not broken[1], label=label, band_p=band_p)
    pin = pin or p
    if broken[1]:
        splinter_end(g, R[-1], T, rnd, h=hh, p_out=p, p_in=pin)
    if broken[0]:
        splinter_end(g, list(reversed(R[0])), -T, rnd, h=hh, p_out=p, p_in=pin)
    return R


def coals(g, gd, rnd, c, n=5, spread=0.8, ash=True, label="coal"):
    """A smouldering spot: a flat ash mound (stone tile, grey) in gd and n glowing coals
    (ember tile) in g, all resting on the surface at height c.z."""
    c = Vector(c)
    if ash:
        rock(gd, (c.x, c.y, c.z - 0.05), (spread * 1.3, spread * 1.1, 0.32), 10, rnd, P("stone", 2.0, rnd=rnd),
             zmin=c.z - 0.12, squash_top=0.55, label="ash")
    for k in range(n):
        a = rnd.uniform(0, 2 * math.pi)
        rr = spread * math.sqrt(rnd.uniform(0.0, 1.0))
        q = (c.x + rr * math.cos(a), c.y + rr * math.sin(a), c.z + 0.02)
        s = rnd.uniform(0.18, 0.32)
        rock(g, q, (s, s * rnd.uniform(0.7, 1.0), s * 0.7), 7, rnd, P("ember", 1.0, rnd=rnd), zmin=c.z - 0.08,
             label=label)


# --------------------------------------------------------------------------------------
# trampled_log: a 30-stud trunk felled (snapped, not cut) by the army
# --------------------------------------------------------------------------------------
def gen_log():
    mid = "trampled_log"
    rnd = random.Random(hash_seed(mid))
    g = Geo()

    def scuff(n, c):                         # bark scraped off by feet and carts: cream patches
        if n.z > 0.3 and rnd.random() < 0.09:
            return "cut_wood"
        return None
    pbark = P("bark", 4.0, fn=scuff)
    pb = P("bark", 3.0)
    pcut = P("cut_wood", 1.6)
    n = 12
    x0, x1 = -14.7, 12.35
    pts, radii = [], []
    for i in range(n + 1):
        t = i / n
        x = x0 + (x1 - x0) * t
        r = 1.08 + 0.48 * t ** 1.3 + (0.1 if i == n else 0.0)
        y = 0.45 * math.sin(math.pi * (t * 1.15 - 0.05)) - 0.25
        z = r - 0.3 + 0.07 * math.sin(2.6 * math.pi * t)
        pts.append(Vector((x, y, z)))
        radii.append(r)
    rings = tube(g, pts, radii, 10, pbark.but(smooth=True), h_ref=8.6, phase=0.3, up0=Z, label="trunk")
    # snapped butt end (+X): a few long splinters, the rest short; torn crown end (-X): short
    # snapped, not cut: the trunk tore across with long splinters on the hinge side (top-left)
    # and short ones opposite, the broken core standing proud
    def snap(hinge, lo, hi, n=10):
        return [lo + hi * max(0.0, math.cos(2 * math.pi * j / n - hinge)) ** 2 + rnd.uniform(-0.08, 0.22)
                for j in range(n)]
    T1 = (pts[-1] - pts[-2]).normalized()
    splinter_end(g, rings[-1], T1, rnd, p_out=pb, p_in=pcut, inward=0.18, core=0.55, tip_h=snap(1.2, 0.12, 1.45))
    T0 = (pts[0] - pts[1]).normalized()
    splinter_end(g, list(reversed(rings[0])), T0, rnd, p_out=pb, p_in=pcut, inward=0.2, core=0.3,
                 tip_h=snap(4.0, 0.1, 0.7))
    # broken branch stubs (pointing up / sideways, angled toward the lost crown at -X)
    stubs = []
    for t, ph, ln, rr in ((0.16, -48, 1.6, 0.42), (0.27, 34, 2.4, 0.5), (0.41, -8, 1.3, 0.46), (0.55, 62, 1.9, 0.5),
                          (0.69, -66, 1.5, 0.48), (0.8, 18, 1.0, 0.55)):
        fi = t * n
        i = int(fi)
        c = pts[i].lerp(pts[i + 1], fi - i)
        r = radii[i] + (radii[i + 1] - radii[i]) * (fi - i)
        radial = Vector((0.0, math.sin(math.radians(ph)), math.cos(math.radians(ph))))
        d = (radial * 0.88 + Vector((-0.48, 0.0, 0.0))).normalized()
        a = c + radial * (r * 0.45)
        m = c + radial * (r * 0.92) + d * (ln * 0.45)
        e = c + radial * (r * 0.92) + d * ln
        sr = tube(g, [a, m, e], [rr, rr * 0.9, rr * 0.75], 7, P("bark", 1.0), h_ref=2 * math.pi * rr * 0.88,
                  label="stub")
        splinter_end(g, sr[-1], (e - m).normalized(), rnd, h=(0.15, 0.9), p_out=pb, p_in=pcut, inward=0.3)
        stubs.append([round(v, 2) for v in e])
    # splinters and bark shards broken off at the snap, lying in the mud
    for (sx, sy, ang, L, W) in ((12.6, -2.6, 20, 2.0, 0.5), (12.1, 2.3, -35, 1.6, 0.42), (13.2, 1.7, 75, 1.2, 0.36)):
        sh = Geo()
        extrude_poly(sh, [(0.0, -W / 2), (L * 0.55, -W * 0.6), (L, 0.0), (L * 0.5, W / 2), (0.1, W * 0.35)],
                     (0.0, 0.0, 0.16), (1, 0, 0), (0, 1, 0), 0.16, pcut, p_back=pcut, p_side=pcut, label="shard")
        merge(g, sh, rest_on_ground(sh, place((sx, sy, 0.0), rz=ang), sink=0.05))
    cols = []
    for i0, i1 in ((0, 6), (6, 12)):
        a, b = pts[i0], pts[i1]
        r = (radii[i0] + radii[i1]) / 2
        c = (a + b) / 2
        top = c.z + r
        cols.append([[c.x, c.y, (top - 0.3) / 2], [(b - a).length, 2 * r, top + 0.3],
                     math.degrees(math.atan2(b.y - a.y, b.x - a.x))])
    info = dict(length_trunk=round(x1 - x0, 2), diameter_butt=round(2 * radii[-1], 2), diameter_top=round(2 * radii[0], 2),
                butt_end="+X: snapped trunk, long splinters (bark outside, cream wood inside)",
                top_end="-X: torn-off crown, short splinters", branch_stubs=stubs,
                note="lies on the ground, bottom sunk 0.3 studs; felled by the army (snapped / pushed over), not cut")
    return dict(parts={"Bark": g}, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# stake_fence: broken palisade, 24 long x 7 tall, tiles end to end along X
# --------------------------------------------------------------------------------------
FENCE_LEN, FENCE_N = 24.0, 24


def gen_fence():
    mid = "stake_fence"
    rnd = random.Random(hash_seed(mid))
    S, R = Geo(), Geo()
    pbark = P("bark", 6.5, grain=Z)
    pchar = P("char", 6.5, grain=Z)
    pemb = P("char_ember", 1.5)
    MISSING, STUMP, LEAN = 6, 15, 19
    xs = [-FENCE_LEN / 2 + (k + 0.5) * FENCE_LEN / FENCE_N for k in range(FENCE_N)]

    def stake_rings(x, y, r, zs, lx, ly, ph):
        return [ring_xy((x + z * lx, y + z * ly), r * (1.0 - 0.04 * max(z, 0.0) / 6.0), 6, z, ph) for z in zs]
    for k, x in enumerate(xs):
        if k == MISSING:
            continue
        r = rnd.uniform(0.42, 0.49)
        ph = rnd.uniform(0, math.pi / 3)
        lx, ly = rnd.uniform(-0.03, 0.03), rnd.uniform(-0.035, 0.02)
        if abs(x) > 11.0:
            lx = 0.0
        if k == LEAN:
            lx, ly = 0.04, -math.tan(math.radians(22.0))
        if k == STUMP:
            R0 = stake_rings(x, 0.0, r, [-1.0, 1.0], lx, ly, ph)
            Rr = loft(S, R0, pchar, cap0=False, cap1=False, label="stump",
                      band_p=lambda i: pbark if i == 0 else pchar)
            splinter_end(S, Rr[-1], Vector((lx, ly, 1.0)), rnd, h=(0.3, 1.5), p_out=pchar, p_in=pemb, inward=0.3)
            # its snapped-off top lies in the mud in front of the palisade
            piece = Geo()
            Rp = loft(piece, [ring_xy((0, 0), r, 6, z, ph) for z in (0.0, 3.2)] + [Vector((0.05, -0.03, 4.35))],
                      pbark, cap0=False, cap1=False, label="fallen_top",
                      band_p=lambda i: pbark if i == 0 else pemb)
            splinter_end(piece, list(reversed(Rp[0])), Vector((0, 0, -1)), rnd, h=(0.2, 1.1), p_out=pchar, p_in=pemb)
            merge(S, piece, rest_on_ground(piece, place((x - 1.3, -2.2, 0.0), rz=-24.0, ry=86.0), sink=0.12))
            continue
        h_top = rnd.uniform(5.35, 5.75) if k != LEAN else 5.6
        h_tip = h_top + rnd.uniform(1.0, 1.22)
        if k == LEAN:
            h_tip = h_top + 1.15
        burn = h_top - rnd.uniform(0.5, 2.2) if rnd.random() < 0.6 else None
        zs = [-1.0] + ([burn] if burn else []) + [h_top]
        rings = stake_rings(x, 0.0, r, zs, lx, ly, ph)
        apex = Vector((x + h_tip * lx + rnd.uniform(-0.07, 0.07), h_tip * ly + rnd.uniform(-0.07, 0.07), h_tip))
        bands = [pbark, pchar] if burn else [pbark]
        loft(S, rings + [apex], pbark, cap0=False, cap1=False, label="stake",
             band_p=lambda i, bands=bands: bands[i] if i < len(bands) else pemb)
    # two rails behind the stakes (exactly x = -12 .. 12, so segments butt end to end)
    prail = P("bark", 8.0, grain=(1, 0, 0))
    rail_y, rail_r = 0.66, 0.24
    rails_z = (1.9, 4.6)
    for z in rails_z:
        pole(R, (-FENCE_LEN / 2, rail_y, z), (FENCE_LEN / 2, rail_y, z), rail_r, rail_r, 6, prail, rnd, seg=8.0,
             phase=math.pi / 6, label="rail")
    # rope lashings tying stakes to the rails (the leaning stake's lashing has snapped)
    prope = P("rope", 1.0)
    for z in rails_z:
        for k in (3, 11, 21):
            x = xs[k]
            lathe(R, [(0.56, z - 0.17), (0.56, z + 0.17)], 6, prope, c=(x, 0.28, 0.0), sy=1.45, label="lashing")
    cols = [[[0.0, 0.25, 3.5], [FENCE_LEN, 1.4, 7.0], 0.0],
            [[xs[LEAN], -1.25, 3.0], [1.0, 2.3, 6.0], 0.0]]
    info = dict(length=FENCE_LEN, stakes=FENCE_N - 1, stake_spacing=FENCE_LEN / FENCE_N,
                stake_x=[round(x, 2) for x in xs],
                missing=dict(gone=xs[MISSING], snapped_stump=xs[STUMP], leaning_22deg_forward=xs[LEAN]),
                rails=dict(y=rail_y, z=list(rails_z), x_range=[-FENCE_LEN / 2, FENCE_LEN / 2], radius=rail_r),
                tiling="place the next segment at this origin + (24, 0, 0) (Blender X); rails butt end to end and the "
                       "stake rhythm (1 stud) continues across the joint",
                front="-Y (outside of the palisade, sharpened faces); rails and lashings on the +Y (inside) side")

    def tiling():
        ends = {}
        for side, xe in (("lo", -FENCE_LEN / 2), ("hi", FENCE_LEN / 2)):
            ends[side] = sorted((round(p[1], 4), round(p[2], 4)) for p in R.co if abs(p[0] - xe) < 1e-6)
        mn, mx = geo_bbox(S, R)
        gap = (xs[0] + FENCE_LEN) - xs[-1]
        return dict(rail_end_vertices=len(ends["lo"]), rail_ends_match=ends["lo"] == ends["hi"] and len(ends["lo"]) > 0,
                    stake_gap_across_joint=round(gap, 4), x_extent=[round(mn[0], 3), round(mx[0], 3)],
                    ok=ends["lo"] == ends["hi"] and len(ends["lo"]) > 0 and abs(gap - FENCE_LEN / FENCE_N) < 1e-6 and
                    mn[0] >= -FENCE_LEN / 2 - 0.15 and mx[0] <= FENCE_LEN / 2 + 0.15)
    return dict(parts={"Stakes": S, "Rails": R}, colliders=cols, info=info, extra_checks={"tiling": tiling()})


# --------------------------------------------------------------------------------------
# torii_broken: the burned village torii -- right pillar standing, left pillar snapped at
# half height, the kasagi stack fallen diagonally from the stub to the ground (front-left),
# keeping a wagon corridor free between the pillars.
# --------------------------------------------------------------------------------------
TORII_CORRIDOR = ((-3.5, -16.0, 0.05), (3.5, 16.0, 9.0))     # 7 wide x 9 tall, must stay empty


def beam_xs(g, x0, x1, half_y, z_bot, h, sori, p, slant=0.0, ts=None, band_p=None, label="beam_x"):
    """Beam along X with upright sections; bottom follows z_bot + sori(x); slanted end
    faces (top edge reaches x0/x1). band_p(i) paints band i (sections ts)."""
    ts = ts or [-1, -0.8, -0.55, -0.25, 0, 0.25, 0.55, 0.8, 1]
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
    return loft(g, rings, p, True, True, None, label, band_p=band_p)


def torii_pillar(g, rnd, xc_fn, r_fn, zs, paints, sides=16, top=None, pin=None, pout=None):
    """Lacquered round pillar along z with bands of paint (tube UVs, so patterned tiles run
    on around the pillar); top = 'cap' (closed) or 'broken' (splinter crown)."""
    h_ref = 2 * math.pi * r_fn((zs[0] + zs[-1]) / 2)
    zz, bi = [zs[0]], []
    for i in range(len(zs) - 1):
        n = max(1, int(math.ceil((zs[i + 1] - zs[i]) / (h_ref * 0.98))))
        for k in range(1, n + 1):
            zz.append(zs[i] + (zs[i + 1] - zs[i]) * k / n)
            bi.append(i)
    pts = [Vector((xc_fn(z), 0.0, z)) for z in zz]
    R = tube(g, pts, [r_fn(z) for z in zz], sides, paints[0], h_ref=h_ref, cap1=(top == "cap"), up0=(1, 0, 0),
             band_p=lambda i: paints[bi[i]], label="pillar")
    if top == "broken":
        splinter_end(g, R[-1], Z, rnd, h=(0.2, 1.5), p_out=pout, p_in=pin, inward=0.3, core=0.12)
    return R


def gen_torii_broken():
    mid = "torii_broken"
    rnd = random.Random(hash_seed(mid))
    PIL, FAL, ST, EM = Geo(), Geo(), Geo(), Geo()
    pl = P("lacquer_soot", 6.0, smooth=True)
    plf = P("lacquer_soot", 6.0)
    pc = P("char", 3.0, smooth=True)
    pcf = P("char", 3.0)
    pe = P("char_ember", 1.5)
    ps = P("stone", 6.0, rnd=rnd)
    X0, XT, ZTOP = 6.0, 5.82, 18.3

    def px(z):
        return X0 + (XT - X0) * z / 18.0

    def pr(z):
        return 0.98 - 0.13 * z / 18.0

    def sori(x):
        return 1.45 * (abs(x) / 10.0) ** 2.3
    for sx in (-1, 1):
        lathe(ST, [(1.62, -0.6), (1.66, 0.35), (1.42, 0.86), (1.12, 1.0), (0.0, 1.0)], 8, ps,
              c=(sx * X0, 0.0, 0.0), phase=math.pi / 8, jit=_jit(rnd, 0.05, 0.03), label="base")
    # right pillar: standing to its full height, top charred where the kasagi sat
    zs = [0.9, 2.9, 6.2, 12.0, 16.55, ZTOP]
    pcl = P("lacquer", 6.0, smooth=True)
    torii_pillar(PIL, rnd, lambda z: px(z), pr, zs, [pc, pl, pcl, pl, pc], top="cap")
    zd = 16.55
    lathe(PIL, [(pr(zd) - 0.03, zd), (pr(zd) + 0.17, zd + 0.06), (pr(zd) + 0.17, zd + 0.72), (pr(zd) + 0.1, zd + 0.78),
                (pr(zd) - 0.03, zd + 0.78)], 16, pc, c=(px(zd + 0.4), 0.0, 0.0), cap0=False, cap1=False,
          band_p=lambda i: pc if i == 1 else pcf, label="daiwa")
    block(PIL, (px(ZTOP) - 0.32, -0.32, ZTOP - 0.1), (px(ZTOP) + 0.32, 0.32, ZTOP + 0.55), pcf, label="tenon")
    # the nuki stub still wedged through the right pillar (broken inside the passage, high up)
    rbeam(PIL, (3.7, 0.0, 14.875), (7.7, 0.0, 14.875), 0.84, 0.95, plf, rnd, broken=(True, False), pin=pe,
          hh=(0.3, 1.3), label="nuki_stub")
    # left pillar: snapped at half height
    zb = 8.9
    torii_pillar(PIL, rnd, lambda z: -px(z), pr, [0.9, 2.9, 4.8, 7.6, zb], [pc, pcl, pl, pc], top="broken", pin=pe,
                 pout=pcf)
    # its upper half, lying in the mud behind-left (break end toward the stub)
    up = Geo()
    L2 = ZTOP - 9.4
    zs2 = [0.0, 1.2, 4.6, 7.15, L2]
    R = torii_pillar(up, rnd, lambda z: 0.0, lambda z: pr(z + 9.4), zs2, [pc, pl, pcl, pc], top="cap")
    splinter_end(up, list(reversed(R[0])), Vector((0, 0, -1)), rnd, h=(0.2, 1.3), p_out=pcf, p_in=pe, inward=0.3)
    zd2 = 7.15
    r2 = pr(zd2 + 9.4)
    lathe(up, [(r2 - 0.03, zd2), (r2 + 0.17, zd2 + 0.06), (r2 + 0.17, zd2 + 0.72), (r2 + 0.1, zd2 + 0.78),
               (r2 - 0.03, zd2 + 0.78)], 16, pc, cap0=False, cap1=False, band_p=lambda i: pc if i == 1 else pcf,
          label="daiwa")
    block(up, (-0.32, -0.32, L2 - 0.1), (0.32, 0.32, L2 + 0.55), pcf, label="tenon")
    M_up = rest_on_ground(up, place((-7.9, 2.1, 0.0), rz=128.0, ry=88.5), sink=0.1)
    merge(FAL, up, M_up)
    # the kasagi stack (shimaki + kasagi + charred cap) fallen: upper end on the stub, lower
    # end in the mud toward the front-left, outside the wagon corridor
    kb = Geo()

    plc = P("lacquer", 6.0)
    pk = P("iron", 6.0)
    beam_xs(kb, -9.3, 9.3, 0.9, 0.0, 0.86, sori, plf, slant=0.12,
            band_p=lambda i: pcf if i == 0 else (plf if i in (1, 2, 7) else plc), label="shimaki")
    beam_xs(kb, -9.92, 9.92, 1.0, 0.84, 0.98, sori, plf, slant=0.25,
            band_p=lambda i: pcf if i == 0 else (plf if i in (1, 6, 7) else plc), label="kasagi")
    beam_xs(kb, -10.0, 10.0, 1.1, 1.80, 0.46, sori, pcf, slant=0.08,
            band_p=lambda i: pcf if i in (0, 1, 2, 7) else pk, label="kasagi_cap")
    c_loc = Vector((8.3, 0.0, sori(8.3) + 0.02))
    stub_top = Vector((-px(zb), 0.0, zb + 0.18))
    yaw, roll = 51.5, 9.0

    def M_of(beta):
        return (Matrix.Translation(stub_top) @ Matrix.Rotation(math.radians(yaw), 4, "Z") @
                Matrix.Rotation(-beta, 4, "Y") @ Matrix.Rotation(math.radians(roll), 4, "X") @
                Matrix.Translation(-c_loc))
    lo, hi = 0.05, 1.2
    for _ in range(50):
        mid_b = (lo + hi) / 2
        if geo_minz(kb, M_of(mid_b)) > -0.12:
            lo = mid_b
        else:
            hi = mid_b
    beta = (lo + hi) / 2
    M_k = M_of(beta)
    merge(FAL, kb, M_k)
    # nuki remains (snapped in two) and the plaque, in the mud behind-left
    for a, b, br in (((-5.2, 8.6, 0.0), (-10.8, 10.4, 0.0), (True, False)),
                     ((-11.6, 4.2, 0.0), (-13.4, -0.6, 0.0), (False, True))):
        nk = Geo()
        L = (Vector(b) - Vector(a)).length
        rbeam(nk, (0, 0, 0), (L, 0, 0), 0.84, 0.95, plf, rnd, broken=br, pin=pe, hh=(0.3, 1.2), label="nuki_piece")
        d = Vector(b) - Vector(a)
        merge(FAL, nk, rest_on_ground(nk, place(a, rz=math.degrees(math.atan2(d.y, d.x)), rx=rnd.uniform(-8, 8)),
                                      sink=0.08))
    pq = Geo()
    block(pq, (-0.86, -0.135, 0.0), (0.86, 0.135, 1.72), plf, label="plaque_frame")
    block(pq, (-0.68, -0.2, 0.18), (0.68, -0.13, 1.54), pcf, label="plaque")
    merge(FAL, pq, rest_on_ground(pq, place((-6.4, 4.6, 0.0), rz=24.0, rx=-84.0), sink=0.05))
    coals(EM, FAL, rnd, (-7.3, -2.6, 0.0), n=5, spread=0.9)
    # ---- colliders ----
    cols = [[[5.91, 0.0, 9.15], [2.0, 2.0, 18.3], 0.0], [[6.0, 0.0, 0.2], [3.3, 3.3, 1.6], 0.0],
            [[-6.0, 0.0, 4.6], [2.0, 2.0, 9.2], 0.0], [[-6.0, 0.0, 0.2], [3.3, 3.3, 1.6], 0.0]]
    for k in range(3):
        a = M_k @ Vector((-9.8 + k * 6.0, 0.0, 0.0))
        b = M_k @ Vector((-9.8 + (k + 1) * 6.0, 0.0, 0.0))
        top = max(a.z, b.z) + 2.2
        c = (a + b) / 2
        cols.append([[c.x, c.y, top / 2], [math.hypot(b.x - a.x, b.y - a.y), 2.3, top], yaw])
    lo_up = M_up @ Vector((0, 0, 0))
    hi_up = M_up @ Vector((0, 0, L2))
    c = (lo_up + hi_up) / 2
    cols.append([[c.x, c.y, 0.95], [L2, 2.1, 1.9], math.degrees(math.atan2(hi_up.y - lo_up.y, hi_up.x - lo_up.x))])
    info = dict(pillar_centres_x=[-X0, X0], pillar_spacing=2 * X0,
                standing_pillar=dict(x=X0, height=ZTOP, note="the kasagi seat at the top is charred; tenon visible"),
                snapped_pillar=dict(x=-X0, break_height=zb, note="upper half lies behind-left"),
                fallen_kasagi=dict(length=20.0, pitch_deg=round(math.degrees(beta), 1), yaw_deg=yaw,
                                   upper_end="resting on the splintered left stub (z ~ 9.1)",
                                   lower_end="in the mud, front-left of the gate"),
                wagon_corridor=dict(min=list(TORII_CORRIDOR[0]), max=list(TORII_CORRIDOR[1]),
                                    note="7 studs wide x 9 tall along the full depth: no geometry, no collider"),
                clear_width_between_bases=round(2 * (X0 - 1.66), 2),
                smoke_points=[[-7.3, -2.6, 0.3], [round(-px(zb), 2), 0.0, round(zb + 0.6, 2)]])
    return dict(parts={"Pillars": PIL, "Fallen": FAL, "Stone": ST, "Embers": EM}, colliders=cols, info=info,
                clear_boxes={"wagon_corridor": TORII_CORRIDOR})


# --------------------------------------------------------------------------------------
# battle debris items (built at the origin, then placed): broken spears, cracked shield,
# dented helmet, broken cart wheel, torn orc war banner with a crude boar-head emblem
# --------------------------------------------------------------------------------------
BOAR_R = [(0.0, -1.05), (0.5, -1.02), (0.6, -0.82), (0.55, -0.58), (0.66, -0.62), (1.04, -0.08), (0.66, -0.42),
          (0.7, -0.18), (0.8, 0.18), (0.66, 0.48), (1.06, 1.02), (0.4, 0.66), (0.2, 0.62), (0.12, 0.88)]
BOAR_TOP = (0.0, 0.7)
BOAR_EYE = [(0.12, 0.2), (0.2, 0.04), (0.5, 0.14), (0.55, 0.33)]
BOAR_NOSTRIL = [(0.08, -0.94), (0.3, -0.92), (0.33, -0.7), (0.1, -0.72)]
BOAR_SNOUT = [(-0.55, -0.55), (0.55, -0.55), (0.53, -0.62), (-0.53, -0.62)]


def boar_head(cx, cy, sc):
    """Crude boar head (ears, tusks, snout) as 2D polygons: head, then the dark details
    (eyes, nostrils, snout line) that go first in the region list."""
    def tr(poly, mirror=False):
        return [(cx + (-x if mirror else x) * sc, cy + y * sc) for x, y in poly]
    head = tr(BOAR_R) + [(cx + BOAR_TOP[0] * sc, cy + BOAR_TOP[1] * sc)] + list(reversed(tr(BOAR_R[1:], True)))
    dark = [tr(BOAR_EYE), tr(BOAR_EYE, True), tr(BOAR_NOSTRIL), tr(BOAR_NOSTRIL, True), tr(BOAR_SNOUT)]
    return head, dark


def item_spear(rnd, L, head=True, broken_tail=True, broken_head=False, r=0.13):
    """Spear along +X from x=0 (tail) to x=L (+ head): shaft, iron socket and leaf blade,
    rope binding; snapped ends are splintered (cream inside)."""
    g = Geo()
    pw = P("timber", 2.5)
    pcut = P("cut_wood", 1.0)
    pole(g, (0, 0, 0), (L, 0, 0), r, r * 0.92, 6, pw, rnd, broken=(broken_tail, broken_head and not head),
         pin=pcut, hh=(0.3, 1.6), seg=2.5, label="shaft")
    if head:
        pi = P("iron", 1.5)
        cyl(g, (L - 0.3, 0, 0), (L + 0.32, 0, 0), r * 1.35, r * 1.15, 6, pi, label="socket")
        blade = [(0.0, 0.0), (0.3, 0.2), (1.25, 0.11), (1.65, 0.0), (1.25, -0.11), (0.3, -0.2)]
        extrude_poly(g, blade, (L + 0.22, 0.0, 0.05), (1, 0, 0), (0, 1, 0), 0.1, pi, label="blade")
    return g


def item_shield(rnd, R=1.6, t=0.22):
    """Round wooden shield (face +Z), cracked from the rim inward, a charred patch,
    iron boss in front and a grip bar behind."""
    g = Geo()
    n = 14
    pts = []
    crack_at = 3
    for k in range(n):
        a = 2 * math.pi * k / n
        pts.append((R * math.cos(a), R * math.sin(a)))
        if k == crack_at:                                  # a zigzag crack from the rim toward the boss
            a1 = 2 * math.pi * (k + 0.5) / n
            rad = Vector((math.cos(a1), math.sin(a1), 0.0))
            tan = Vector((-math.sin(a1), math.cos(a1), 0.0))
            path = [(R * 0.97, 0.0, 0.16), (1.2, 0.1, 0.12), (0.85, -0.06, 0.09), (0.55, 0.05, 0.05), (0.32, 0.0, 0.0)]
            left = [rad * r + tan * (o - w / 2) for r, o, w in path]
            right = [rad * r + tan * (o + w / 2) for r, o, w in path]
            pts += [(q.x, q.y) for q in left[:-1]] + [(left[-1].x, left[-1].y)] + \
                   [(q.x, q.y) for q in reversed(right[:-1])]
        if k == 9:                                         # a chunk broken out of the rim
            a1 = 2 * math.pi * (k + 0.5) / n
            pts += [(1.15 * math.cos(a1 - 0.06), 1.15 * math.sin(a1 - 0.06)),
                    (1.25 * math.cos(a1 + 0.1), 1.25 * math.sin(a1 + 0.1))]
    char = [(-1.9, -1.9), (-0.2, -1.9), (-0.5, -0.9), (-1.1, -0.4), (-1.9, -0.6)]
    emb = [(-1.9, -1.9), (-0.9, -1.9), (-1.0, -1.35), (-1.9, -1.1)]
    panel(g, pts, (0, 0, t), (1, 0, 0), (0, 1, 0), t, P("planks", 2.0),
          regions=[(emb, P("char_ember", 2.0)), (char, P("char", 2.0))], side=P("planks_dark", 2.0), cells=False,
          label="shield")
    lathe(g, [(0.5, t - 0.02), (0.42, t + 0.2), (0.0, t + 0.36)], 7, P("iron", 1.0, smooth=True),
          cap0=False, label="boss")
    block(g, (-0.7, -0.12, -0.18), (0.7, 0.12, 0.02), P("planks_dark", 1.5, grain=(1, 0, 0)), label="grip")
    return g


def item_helmet(rnd, dent=True):
    """Crude orc iron helmet: bowl with thickness, rim band, short spike; dented."""
    g = Geo()
    pi = P("iron", 1.5, smooth=True)
    prof = [(0.0, 1.05), (0.55, 0.92), (0.8, 0.45), (0.84, 0.0),
            (0.98, 0.0), (0.95, 0.47), (0.64, 1.02), (0.0, 1.2)]
    rings = [Vector((0.0, 0.0, z)) if r == 0 else ring_xy((0, 0), r, 8, z) for r, z in prof]
    if dent:                                   # outer / inner ring pairs pushed in together
        for (ro, ri), k, d in (((6, 1), 2, 0.2), ((6, 1), 3, 0.16), ((5, 2), 2, 0.12)):
            for idx in (ro, ri):
                q = rings[idx][k]
                rad = Vector((q.x, q.y, 0.0)).normalized()
                rings[idx][k] = q - rad * d - Z * (0.04 if idx in (6, 1) else 0.0)
    loft(g, rings, pi, label="helmet")
    lathe(g, [(0.15, 1.12), (0.0, 1.62)], 5, P("iron", 1.0), label="spike")
    return g


def item_wheel(rnd, R=1.85, gap=(0.0, 0.0), broken_spokes=(), lost_spokes=()):
    """Cart wheel (axis +Z, centre at the origin): hub, 8 spokes, rim of felloes with an iron
    tyre; the rim arc `gap` (radians) is broken away, its ends splintered."""
    g = Geo()
    pw = P("timber", 2.0)
    pcut = P("cut_wood", 1.0)

    def tyre(n, c):
        q = Vector((c.x, c.y, 0.0))
        if q.length > 1e-6 and n.dot(q.normalized()) > 0.7:
            return "iron"
        return None
    prim = pw.but(fn=tyre)
    pole(g, (0, 0, -0.48), (0, 0, 0.48), 0.45, 0.45, 8, pw, rnd, label="hub")
    for k in range(8):
        if k in lost_spokes:
            continue
        a = 2 * math.pi * (k + 0.5) / 8
        d = Vector((math.cos(a), math.sin(a), 0.0))
        L = R + 0.02 if k not in broken_spokes else rnd.uniform(0.7, 1.1)
        rbeam(g, d * 0.3, d * L, 0.2, 0.16, pw, rnd, broken=(False, k in broken_spokes), pin=pcut, label="spoke")
    a0, a1 = gap
    span = 2 * math.pi - (a1 - a0) if a1 > a0 else 2 * math.pi
    n = 11 if a1 > a0 else 14
    closed = not a1 > a0
    rr = R + 0.12
    pts = [Vector((rr * math.cos(a1 + span * k / n), rr * math.sin(a1 + span * k / n), 0.0))
           for k in range(n if closed else n + 1)]
    if closed:
        rings = []
        for k in range(n):
            T = (pts[(k + 1) % n] - pts[k - 1]).normalized()
            Nn = Z
            B = T.cross(Nn)
            rings.append(rect_ring(pts[k], Nn, B, 0.4, 0.34))
        loft(g, rings + [rings[0]], prim, cap0=False, cap1=False, label="rim")
    else:
        R = sweep(g, pts, [(0.4, 0.34)] * len(pts), prim, cap0=False, cap1=False, label="rim")
        splinter_end(g, R[-1], (pts[-1] - pts[-2]).normalized(), rnd, h=(0.3, 1.3), p_out=pw, p_in=pcut)
        splinter_end(g, list(reversed(R[0])), (pts[0] - pts[1]).normalized(), rnd, h=(0.3, 1.3), p_out=pw, p_in=pcut)
    return g


def item_banner_cloth(rnd, emblem=True, w=2.7, h=4.6, z0=1.45, drape=None, depth=0.05):
    """Torn orc war banner cloth in the (x, z) plane, front -Y, attached along x = 0.25:
    ragged bottom tatters, drooping torn top corner, a burned corner, boar-head emblem
    (bone faces, iron eyes / nostrils) cut into the cloth by the constrained triangulation."""
    g = Geo()
    x0, x1 = 0.25, 0.25 + w
    zt = z0 + h
    out = [(x0, z0 + 0.4), (x0, zt), (x0 + 0.9, zt - 0.08), (x0 + 1.6, zt - 0.3), (x0 + 2.05, zt - 0.75),
           (x1, zt - 1.35), (x1 - 0.06, zt - 2.1), (x1 - 0.35, zt - 2.3), (x1 - 0.05, zt - 2.45), (x1, z0 + 0.7)]
    # ragged bottom: tatters of different lengths
    bx = [x1 - 0.25, x1 - 0.6, x1 - 0.95, x1 - 1.3, x1 - 1.65, x1 - 2.0, x1 - 2.35]
    for k, x in enumerate(bx):
        out.append((x, z0 + (0.05 if k % 2 == 0 else 0.6) + rnd.uniform(-0.1, 0.15) - (0.3 if k == 2 else 0.0)))
    regions = []
    if emblem:
        head, dark = boar_head(x0 + w * 0.5, z0 + h * 0.5, 0.98)
        for d in dark:
            regions.append((d, P("iron", 2.0)))
        regions.append((head, P("bone", 2.0)))
    regions.append(([(x1 - 0.9, z0 - 1), (x1 + 1, z0 - 1), (x1 + 1, z0 + 1.25), (x1 - 0.2, z0 + 1.05)], P("char", 2.0)))
    if drape is None:
        def pos(s, t):
            u = (s - x0) / w
            return Vector((s, -0.2 * math.sin(math.pi * u) - 0.07 * u * math.sin(2 * math.pi * (t - z0) / 2.3), t))
    else:
        pos = drape
    res = panel(g, out, (0, 0, 0), (1, 0, 0), (0, 0, 1), depth, P("cloth", 2.0), regions=regions, pos=pos,
                cells=False, label="cloth")
    return g, res


def put(dst, src, xy, rz=0.0, rx=0.0, ry=0.0, sink=0.05):
    """Rotate src (X, then Y, then Z), centre its footprint on xy and rest it on the ground."""
    M = place((0.0, 0.0, 0.0), rz=rz, rx=rx, ry=ry)
    pts = [M @ Vector(p) for p in src.co]
    cx = (min(p.x for p in pts) + max(p.x for p in pts)) / 2
    cy = (min(p.y for p in pts) + max(p.y for p in pts)) / 2
    M = rest_on_ground(src, Matrix.Translation((xy[0] - cx, xy[1] - cy, 0.0)) @ M, sink=sink)
    merge(dst, src, M)
    return M


def gen_debris_a():
    """The fallen standard: snapped banner pole still planted with its torn cloth, the
    broken-off top in the mud, a wheel lying flat, a cracked shield, a helmet on its side,
    one spear stuck in the ground and two lying."""
    mid = "battle_debris_a"
    rnd = random.Random(hash_seed(mid))
    W, I, C = Geo(), Geo(), Geo()
    pw = P("timber", 2.5)
    pcut = P("cut_wood", 1.0)
    bx, by = -2.5, 1.8
    lean = place((bx, by, 0.0), rz=8.0, rx=6.0, ry=-5.0)
    pg = Geo()
    pole(pg, (0, 0, -0.9), (0, 0, 6.75), 0.2, 0.17, 6, pw, rnd, broken=(False, True), pin=pcut, hh=(0.3, 1.8),
         seg=2.6, label="banner_pole")
    for z in (2.6, 5.3):
        block(pg, (-0.24, -0.24, z), (0.34, 0.24, z + 0.24), P("cloth", 1.0), label="tie")
    merge(W, pg, lean)
    cg, _ = item_banner_cloth(rnd)
    merge(C, cg, lean)
    top = Geo()
    pole(top, (0, 0, 0), (3.3, 0, 0), 0.17, 0.15, 6, pw, rnd, broken=(True, False), pin=pcut, hh=(0.3, 1.5), seg=2.5,
         label="pole_top")
    pole(top, (2.9, -1.3, 0), (2.9, 1.2, 0), 0.1, 0.1, 5, pw, rnd, label="crossbar")
    put(W, top, (-2.3, 3.5), rz=-14.0, rx=12.0)
    wh = item_wheel(rnd, gap=(math.radians(200), math.radians(290)), broken_spokes=(1, 6), lost_spokes=(5,))
    put(W, wh, (1.9, 1.7), rz=20.0, rx=4.0, sink=0.08)
    sh = item_shield(rnd)
    put(W, sh, (2.1, -2.3), rz=35.0, rx=-6.0)
    hm = item_helmet(rnd)
    put(I, hm, (-0.8, -2.2), rz=-30.0, rx=78.0, sink=0.08)
    s1 = item_spear(rnd, 3.4, head=True)
    put(W, s1, (-3.1, -1.2), rz=-60.0, ry=118.0, sink=0.9)
    s2 = item_spear(rnd, 3.6, head=True)
    put(W, s2, (-0.6, -3.5), rz=8.0, sink=0.04)
    s3 = item_spear(rnd, 2.2, head=False, broken_head=True)
    put(W, s3, (-0.5, 0.3), rz=-75.0, sink=0.04)
    cols = [[[bx, by, 3.3], [0.6, 0.6, 6.6], 0.0], [[1.9, 1.7, 0.35], [3.9, 3.9, 0.7], 20.0]]
    info = dict(footprint="about 8 x 8", items=["banner pole planted + torn banner (boar emblem)", "snapped pole top",
                                                 "broken cart wheel (flat)", "cracked round shield", "dented helmet",
                                                 "3 broken spears (one stuck head-down in the mud)"],
                banner_emblem="crude boar head (ears, tusks, snout) in bone colour with dark-iron eyes, nostrils and "
                              "snout line, made of cloth faces (constrained triangulation), visible on both sides")
    return dict(parts={"Wood": W, "Iron": I, "Cloth": C}, colliders=cols, info=info)


def gen_debris_b():
    """The wrecked cart: a wheel stuck upright in the mud on its broken axle, a shield
    leaning on it, a helmet upside down, crossed broken spears with the torn banner draped
    over them, and one spear stuck in the ground."""
    mid = "battle_debris_b"
    rnd = random.Random(hash_seed(mid))
    W, I, C = Geo(), Geo(), Geo()
    pw = P("timber", 2.5)
    pcut = P("cut_wood", 1.0)
    wh = item_wheel(rnd, gap=(math.radians(-60), math.radians(10)), broken_spokes=(7,))
    rbeam(wh, (0, 0, 0.3), (0, 0, -2.7), 0.42, 0.42, pw, rnd, broken=(False, True), pin=pcut, label="axle")
    put(W, wh, (-1.5, 2.0), rz=-20.0, rx=60.0, sink=0.5)
    sh = item_shield(rnd)
    put(W, sh, (-1.6, 0.25), rz=-20.0, rx=58.0, sink=0.3)
    hm = item_helmet(rnd)
    put(I, hm, (2.7, -0.5), rz=40.0, rx=172.0, sink=0.06)
    for L, loc, rz, head in ((4.4, (0.6, -2.0), 8.0, True), (3.2, (1.4, -2.5), 58.0, True),
                             (2.6, (2.0, 1.4), -40.0, False)):
        s = item_spear(rnd, L, head=head, broken_head=not head)
        put(W, s, loc, rz=rz, sink=0.04)
    st = item_spear(rnd, 3.0, head=True)
    put(W, st, (3.1, 2.7), rz=150.0, ry=112.0, sink=0.95)

    def drape(s, t):
        return Vector((s, t, 0.03 + 0.3 * math.exp(-((s - 1.45) / 0.5) ** 2) + 0.05 * math.sin(2.1 * t)))
    cg, _ = item_banner_cloth(rnd, drape=drape)
    put(C, cg, (0.7, -2.1), rz=-82.0, sink=0.02)
    pp = Geo()
    pole(pp, (0, 0, 0), (2.9, 0, 0), 0.17, 0.16, 6, pw, rnd, broken=(False, True), pin=pcut, hh=(0.3, 1.5),
         seg=2.5, label="banner_pole")
    put(W, pp, (-2.6, -2.4), rz=104.0)
    cols = [[[-1.5, 2.0, 1.9], [1.2, 4.0, 3.8], -20.0]]
    info = dict(footprint="about 8 x 8", items=["broken cart wheel stuck upright on its axle", "shield leaning on it",
                                                 "helmet upside down", "3 broken spears lying + 1 stuck head-down",
                                                 "torn banner draped over the spears (boar emblem) + its snapped pole"])
    return dict(parts={"Wood": W, "Iron": I, "Cloth": C}, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# burned minka houses: raised wooden floor on posts (footing stones), plank walls, thick
# thatch roof. Shared builder.
# --------------------------------------------------------------------------------------
FLOOR_Z, SILL_Z, KETA0, KETA1 = 1.6, 1.25, 9.0, 9.8     # floor top, wall bottom, eave beam bottom / top
POST_W, WALL_T, THATCH_T, BEAM_W = 0.8, 0.36, 1.25, 0.9  # beams are wider than posts: no coplanar faces
POST_TOP = KETA1 - 0.06                                  # full posts end just inside the eave beams
S_WALL, S_POST, S_FLOOR, S_THATCH = 6.0, 6.5, 6.0, 5.0  # studs per atlas tile


def above(line, top=80.0):
    """Region polygon above a polyline (left -> right)."""
    return list(line) + [(line[-1][0], top), (line[0][0], top)]


def scaled(poly, k, c=None):
    if c is None:
        c = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
    return [(c[0] + (x - c[0]) * k, c[1] + (y - c[1]) * k) for x, y in poly]


def blob(c, rx, ry, n, rnd, jag=0.25, rot=0.0):
    """Ragged closed polygon (burn hole / scorch patch)."""
    out = []
    for k in range(n):
        a = rot + 2 * math.pi * (k + rnd.uniform(-0.25, 0.25)) / n
        f = 1.0 + rnd.uniform(-jag, jag) * (1.6 if k % 2 else 1.0)
        out.append((c[0] + rx * f * math.cos(a), c[1] + ry * f * math.sin(a)))
    return out


def line_through(keys, rnd, amp=0.35, step=1.4):
    """Jagged polyline through key points (s, z) (left -> right)."""
    out = [keys[0]]
    for a, b in zip(keys[:-1], keys[1:]):
        n = max(0, int(math.hypot(b[0] - a[0], b[1] - a[1]) / step))
        out += jagged(a, b, n, amp, rnd, ends=False) if n else []
        out.append(b)
    return out


class Minka:
    def __init__(self, mid, W, D):
        self.mid, self.W, self.D = mid, W, D
        self.rnd = random.Random(hash_seed(mid))
        self.g = {k: Geo() for k in ("Frame", "Walls", "Floor", "Roof", "Stone", "Debris", "Embers")}
        self.cols = []
        self.smoke = []
        self.pw = P("planks", S_WALL)
        self.pwc = P("char", S_WALL)
        self.pwe = P("char_ember", S_WALL)
        self.ps = P("stone", 3.0, rnd=self.rnd)

    # ---- paints -------------------------------------------------------------------
    def wood(self, axis, kind="wood"):
        return P({"wood": "planks_dark", "char": "char", "ember": "char_ember"}[kind], S_POST, grain=axis)

    # ---- frame --------------------------------------------------------------------
    def footing(self, x, y, r=0.62):
        rock(self.g["Stone"], (x, y, 0.0), (r * 1.55, r * 1.55, 0.55), 7, self.rnd, self.ps, zmin=-0.2,
             squash_top=0.5, label="footing")

    def post(self, x, y, top, burn=None, broken=False, w=POST_W, stone=True, col=True, top_kind="char", base=0.12):
        """Square post on a footing stone, wood below `burn`, charred above; broken posts end
        in a charred splinter crown with glowing cracks inside."""
        g = self.g["Frame"]
        if stone:
            self.footing(x, y)
        zs, kinds = [base], []
        if burn is None or burn >= top - 0.1:
            kinds = ["wood"]
        elif burn <= base + 0.05:
            kinds = [top_kind]
        else:
            zs.append(burn)
            kinds = ["wood", top_kind]
        zs.append(top)
        zz, bk = [zs[0]], []
        for i in range(len(zs) - 1):
            n = max(1, int(math.ceil((zs[i + 1] - zs[i]) / S_POST)))
            for k in range(1, n + 1):
                zz.append(zs[i] + (zs[i + 1] - zs[i]) * k / n)
                bk.append(kinds[i])
        rg = rect_ring8 if broken else rect_ring
        rings = [rg(Vector((x, y, z)), Vector((1, 0, 0)), Vector((0, 1, 0)), w, w) for z in zz]
        R = loft(g, rings, self.wood(Z), cap0=False, cap1=not broken, band_p=lambda i: self.wood(Z, bk[i]),
                 label="post")
        if broken:
            splinter_end(g, R[-1], Z, self.rnd, h=(0.25, 1.6), p_out=self.wood(Z, "char"), p_in=P("char_ember", 1.5),
                         inward=0.3)
        if col:
            self.cols.append([[x, y, top / 2], [w, w, top], 0.0])

    def beam(self, a, b, w=BEAM_W, h=0.8, kind="char", broken=(False, False), part="Frame", up=Z, label="beam"):
        a, b = Vector(a), Vector(b)
        T = (b - a).normalized()
        return rbeam(self.g[part], a, b, w, h, self.wood(T, kind), self.rnd, up=up, broken=broken,
                     pin=P("char_ember", 1.5), seg=S_POST, label=label, ring8=False, hh=(0.4, 1.6))

    def pole(self, a, b, r=0.28, kind="char", broken=(False, False), part="Frame", sides=6, label="rafter"):
        a, b = Vector(a), Vector(b)
        return pole(self.g[part], a, b, r, r * 0.9, sides, self.wood(b - a, kind), self.rnd, broken=broken,
                    pin=P("char_ember", 1.5), seg=S_POST, label=label)

    # ---- walls --------------------------------------------------------------------
    def wall_frame(self, P0, P1):
        P0 = Vector((P0[0], P0[1], 0.0))
        P1 = Vector((P1[0], P1[1], 0.0))
        L = (P1 - P0).length
        U = (P1 - P0) / L
        N = U.cross(Z)
        return P0, U, N, L, P0 + N * (WALL_T / 2)

    def seams(self, P0, P1, s0, s1):
        """s positions (along the wall) of the plank seams, so broken board ends line up
        with the boards of the texture."""
        _, U, N, L, o = self.wall_frame(P0, P1)
        right, _ = uv_basis(N, None)
        a0, a1 = o.dot(right) / S_WALL, U.dot(right) / S_WALL
        out = []
        for k in range(-200, 200):
            s = ((k + 0.5) / 4.0 - a0) / a1
            if s0 < s < s1:
                out.append(s)
        return sorted(out)

    def board_tops(self, P0, P1, s_a, s_b, hfn, amp=0.3, notch=0.3):
        """Broken board ends from s_a to s_b (left -> right): every board between two seams
        has its own height hfn(mid) +- amp and a slanted or pointed charred end."""
        r = self.rnd
        cuts = [s_a] + [s for s in self.seams(P0, P1, s_a, s_b) if s_a + 0.2 < s < s_b - 0.2] + [s_b]
        pts = []
        prev = None
        for a, b in zip(cuts[:-1], cuts[1:]):
            h = hfn((a + b) / 2)
            hl, hr = h + r.uniform(-amp, amp), h + r.uniform(-amp, amp)
            if prev is not None and abs(prev - hl) < 0.12:
                hl = prev + (0.25 if hl >= prev else -0.25)
            pts.append((a, hl))
            if b - a > 0.6 and r.random() < notch:
                pts.append((a + (b - a) * r.uniform(0.3, 0.7), h + r.uniform(-amp, amp) * 1.8 + 0.15))
            pts.append((b, hr))
            prev = hr
        return pts

    def wall(self, P0, P1, top_pts, doors=(), holes=(), regions=(), z0=SILL_Z, label="wall", col=True,
             col_step=4.0, s_range=None):
        """Plank wall on the post line P0 -> P1 (outside = U x Z), bottom at z0 (inside the
        floor edge). top_pts: (s, z) from s = L back to s = 0. doors: (s0, s1, z_top) notches.
        regions: [(polygon in (s, z), paint)] (burns), first match wins."""
        P0v, U, N, L, o = self.wall_frame(P0, P1)
        sa0, sb0 = s_range or (0.0, L)
        bottom = [(sa0, z0)]
        for d0, d1, dz in sorted(doors):
            bottom += [(d0, z0), (d0, dz), (d1, dz), (d1, z0)]
        outline = bottom + [(sb0, z0)] + list(top_pts)
        panel(self.g["Walls"], outline, o, U, Z, WALL_T, self.pw, holes=holes, regions=regions, label=label)
        if not col:
            return
        tops = sorted(top_pts)

        def top_at(s):
            best = None
            for (sa, za), (sb, zb) in zip(tops[:-1], tops[1:]):
                if sa - 1e-6 <= s <= sb + 1e-6 and sb > sa:
                    z = za + (zb - za) * (s - sa) / (sb - sa)
                    best = z if best is None else min(best, z)
            return best if best is not None else tops[0][1]
        spans = []
        cur = sa0
        for d0, d1, dz in sorted(doors):
            spans.append((cur, d0))
            cur = d1
        spans.append((cur, sb0))
        yaw = math.degrees(math.atan2(U.y, U.x))
        for a, b in spans:
            n = max(1, int(round((b - a) / col_step)))
            for k in range(n):
                sa, sb = a + (b - a) * k / n, a + (b - a) * (k + 1) / n
                h = sum(top_at(sa + (sb - sa) * f) for f in (0.1, 0.3, 0.5, 0.7, 0.9)) / 5
                if h < 0.6:
                    continue
                c = P0v + U * ((sa + sb) / 2)
                self.cols.append([[c.x, c.y, h / 2], [sb - sa, WALL_T, h], yaw])

    # ---- floor --------------------------------------------------------------------
    def stub_burn(self, top_pts, sa, sb, char_depth=1.4, embers=3, step=2.6):
        """Burn regions for a broken wall top (top_pts right -> left): char above a ragged line
        about char_depth under the top, and a few glowing ember spots on the broken ends."""
        r = self.rnd
        pts = sorted(top_pts)

        def top_at(x):
            best = None
            for (s0, z0), (s1, z1) in zip(pts[:-1], pts[1:]):
                if s0 <= x <= s1 and s1 > s0:
                    z = z0 + (z1 - z0) * (x - s0) / (s1 - s0)
                    best = z if best is None else min(best, z)
            return best if best is not None else pts[0][1]
        n = max(1, int((sb - sa) / step))
        keys = [(sa - 1.0, top_at(sa) - char_depth)]
        for k in range(1, n):
            x = sa + (sb - sa) * k / n
            keys.append((x, top_at(x) - char_depth + r.uniform(-0.35, 0.35)))
        keys.append((sb + 1.0, top_at(sb) - char_depth))
        regs = []
        for k in range(embers):
            x = sa + (sb - sa) * (k + r.uniform(0.25, 0.75)) / embers
            regs.append((blob((x, top_at(x) - 0.15), r.uniform(0.5, 0.8), 0.32, 7, r), self.pwe))
        regs.append((above(keys), self.pwc))
        return regs

    def under_top(self, top_pts, depth, jit=0.3, ends=None):
        """Region above the top edge shifted down by depth (+- jit): the scorch / ember band
        that follows a broken wall top. top_pts run right -> left (as given to wall())."""
        r = self.rnd
        line = [(s, z - depth + r.uniform(-jit, jit)) for s, z in reversed(top_pts)]
        if ends:
            line = [(ends[0], line[0][1])] + line + [(ends[1], line[-1][1])]
        return above(line)

    def floor(self, outline, holes=(), regions=(), z=FLOOR_Z, t=0.3):
        pf = P("planks", S_FLOOR, grain=(1, 0, 0))
        e = 0.03                                         # inset from the post lines
        outline = [(min(max(x, -self.W / 2 + e), self.W / 2 - e), min(max(y, -self.D / 2 + e), self.D / 2 - e))
                   for x, y in outline]
        panel(self.g["Floor"], outline, (0, 0, z), (1, 0, 0), (0, 1, 0), t, pf, holes=holes, regions=regions,
              label="floor")

    def floor_paints(self):
        return (P("char", S_FLOOR, grain=(1, 0, 0)), P("char_ember", S_FLOOR, grain=(1, 0, 0)))

    # ---- thatch -------------------------------------------------------------------
    def thatch(self, o, U, V, outline, holes=(), regions=(), theta=0.55, part="Roof", label="thatch"):
        """Thatch slab: the outline lies on the top surface; the slab drops vertically by
        THATCH_T / cos(theta), so slabs meeting at a hip or ridge close flush."""
        return panel(self.g[part], outline, o, U, V, THATCH_T, P("thatch", S_THATCH), holes=holes, regions=regions,
                     ext=(0.0, 0.0, THATCH_T / math.cos(theta)), back=P("char", S_THATCH), label=label)

    def coals(self, c, n=5, spread=0.8):
        coals(self.g["Embers"], self.g["Debris"], self.rnd, c, n=n, spread=spread)
        self.smoke.append([round(c[0], 2), round(c[1], 2), round(c[2] + 0.4, 2)])

    def straw(self, c, rz, size=(1.6, 1.1), h=0.5, burnt=0.5, part="Debris", rx=0.0):
        """A fallen thatch remnant: a thick ragged straw clump, partly charred."""
        r = self.rnd
        sg = Geo()
        poly = blob((0, 0), size[0], size[1], 9, r, jag=0.22)
        cut = (burnt * 2 - 1) * size[0]
        reg = [([(-9, -9), (cut, -9), (cut + 0.5, 9), (-9, 9)], P("char", 2.5))] if burnt > 0 else []
        panel(sg, poly, (0, 0, h), (1, 0, 0), (0, 1, 0), h, P("thatch", 2.5), regions=reg, cells=False,
              label="straw")
        put(self.g[part], sg, (c[0], c[1]), rz=rz, rx=rx, sink=0.05 - c[2])

    def result(self, info, clear=None, extra=None):
        info = dict(info)
        info["smoke_points"] = self.smoke
        parts = {k: g for k, g in self.g.items()}
        return dict(parts=parts, colliders=self.cols, info=info, clear_boxes=clear or {}, extra_checks=extra or {})


# --------------------------------------------------------------------------------------
# oni_house_c: mostly intact walls, hipped thatch roof burned through, sliding door broken
# --------------------------------------------------------------------------------------
def gen_house_c():
    H = Minka("oni_house_c", 24.0, 14.0)
    r = H.rnd
    W2, D2 = 12.0, 7.0
    pwc, pwe = H.pwc, H.pwe
    # ---- posts, sills, beams ----
    posts = {}
    for x in (-12, -10, -3, 3, 8, 12):
        posts[(x, -D2)] = 6.4 if -11 < x < -2 else 7.3
    for x in (-12, -6, 0, 6, 12):
        posts[(x, D2)] = 7.6
    posts[(-W2, 0.0)] = 7.8
    posts[(W2, 0.0)] = 6.6
    for (x, y), b in posts.items():
        H.post(x, y, POST_TOP, burn=b)
    for y in (-D2, D2):
        H.beam((-W2 - 0.5, y, 1.1), (W2 + 0.5, y, 1.1), w=0.6, h=0.4, kind="wood", label="sill")
        H.beam((-W2 - 0.9, y, 9.4), (W2 + 0.9, y, 9.4), kind="char", label="keta")
    for x in (-W2, W2):
        H.beam((x, -D2 + 0.3, 1.1), (x, D2 - 0.3, 1.1), w=0.6, h=0.4, kind="wood", label="sill")
        H.beam((x, -D2 - 0.5, 9.4), (x, D2 + 0.5, 9.4), h=0.7, kind="char", label="side_beam")
    for x in (-6.0, 0.0, 6.0):
        H.beam((x, -D2, 9.4), (x, D2, 9.4), w=0.7, h=0.7, kind="char", label="tie_beam")
    # ---- walls (all four standing; soot band under the eaves, scorched door and window) ----
    door = (2.0, 9.0, 8.7)          # s range on the front wall: post centre to post centre (clear x -9.6 .. -3.4)
    win = [(16.0, 4.6), (19.5, 4.6), (19.5, 7.2), (16.0, 7.2)]
    fP0, fP1 = (-W2, -D2), (W2, -D2)
    burn_f = line_through([(-1, 6.6), (1.2, 6.3), (1.9, 2.4), (9.2, 2.0), (10.2, 6.0), (15.0, 5.9), (15.6, 3.9),
                           (20.0, 3.6), (20.6, 6.2), (25.0, 6.8)], r, amp=0.3)
    emb_f = blob((5.5, 8.6), 4.2, 0.75, 12, r, jag=0.3)
    emb_w = blob((17.75, 7.5), 2.4, 0.55, 9, r, jag=0.3)
    H.wall(fP0, fP1, [(24.0, KETA0), (0.0, KETA0)], doors=[door], holes=[win],
           regions=[(emb_f, pwe), (emb_w, pwe), (above(burn_f), pwc)], label="wall_front")
    rP0, rP1 = (W2, -D2), (W2, D2)
    hole_r = blob((6.0, 7.5), 2.0, 1.0, 10, r, jag=0.2)
    H.wall(rP0, rP1, [(14.0, KETA0), (0.0, KETA0)], holes=[hole_r],
           regions=[(scaled(hole_r, 1.35), pwe), (scaled(hole_r, 2.3, (6.0, 8.2)), pwc),
                    (above(line_through([(-1, 6.4), (15, 6.9)], r, amp=0.3)), pwc)], label="wall_right")
    bP0, bP1 = (W2, D2), (-W2, D2)
    H.wall(bP0, bP1, [(24.0, KETA0), (0.0, KETA0)],
           regions=[(blob((7.0, 8.7), 3.0, 0.5, 9, r), pwe),
                    (above(line_through([(-1, 6.9), (25, 7.2)], r, amp=0.35)), pwc)], label="wall_back")
    lP0, lP1 = (-W2, D2), (-W2, -D2)
    H.wall(lP0, lP1, [(14.0, KETA0), (0.0, KETA0)],
           regions=[(above(line_through([(-1, 7.4), (15, 7.0)], r, amp=0.35)), pwc)], label="wall_left")
    # window bars, door frame
    for x in (4.7, 5.4, 6.1, 6.8):
        block(H.g["Frame"], (x - 0.11, -D2 - 0.11, 4.55), (x + 0.11, -D2 + 0.11, 7.25), H.wood(Z, "char"), label="bar")
    block(H.g["Frame"], (-9.7, -D2 - 0.26, 8.65), (-3.3, -D2 + 0.26, 9.02), H.wood((1, 0, 0), "char"), label="kamoi")
    block(H.g["Frame"], (-9.6, -D2 - 0.33, 1.25), (-3.4, -D2 + 0.33, 1.72), H.wood((1, 0, 0), "wood"), label="shikii")
    # ---- floor: burned through under the roof hole ----
    pfc, pfe = H.floor_paints()
    fh = blob((6.2, -3.9), 1.5, 1.0, 10, r, jag=0.25)
    H.floor([(-W2, -D2), (W2, -D2), (W2, D2), (-W2, D2)], holes=[fh],
            regions=[(scaled(fh, 1.35), pfe), (scaled(fh, 2.4), pfc), (blob((-6.5, -5.6), 3.0, 1.2, 9, r), pfc)])
    H.beam((6.2, -6.6, 1.03), (6.2, 6.6, 1.03), w=0.5, h=0.5, kind="char", label="joist")
    H.post(6.2, -0.6, 0.86, w=0.44, col=False)
    H.coals((4.6, -4.7, FLOOR_Z), n=5, spread=0.6)
    H.coals((6.3, -3.8, 0.0), n=3, spread=0.5)
    # ---- hipped thatch roof, burned through on the front-right ----
    th = math.radians(30.0)
    ct, st = math.cos(th), math.sin(th)
    OH = 2.0
    z_te = KETA1 - OH * math.tan(th) + THATCH_T / ct
    tr = (D2 + OH) / ct
    ae, ar = W2 + OH, W2 - D2
    ptc, pte = P("char", S_THATCH), P("char_ember", S_THATCH)
    hole = blob((5.6, 5.0), 2.6, 1.9, 13, r, jag=0.16, rot=0.3)
    plume = [(3.6, 6.0), (7.8, 6.0), (6.6, tr + 1), (4.6, tr + 1)]
    front_regs = [(scaled(hole, 1.28), pte), (scaled(hole, 1.9), ptc), (plume, ptc),
                  (blob((-9.5, 0.9), 2.6, 1.0, 9, r), ptc)]
    slopes = (("front", (0, -(D2 + OH), z_te), (1, 0, 0), (0, ct, st), [(-ae, 0), (ae, 0), (ar, tr), (-ar, tr)]),
              ("back", (0, D2 + OH, z_te), (-1, 0, 0), (0, -ct, st), [(-ae, 0), (ae, 0), (ar, tr), (-ar, tr)]),
              ("left", (-(W2 + OH), 0, z_te), (0, -1, 0), (ct, 0, st), [(-(D2 + OH), 0), (D2 + OH, 0), (0, tr)]),
              ("right", (W2 + OH, 0, z_te), (0, 1, 0), (-ct, 0, st), [(-(D2 + OH), 0), (D2 + OH, 0), (0, tr)]))
    for name, o, U, V, out in slopes:
        if name == "front":
            H.thatch(o, U, V, out, holes=[hole], regions=front_regs, theta=th, label="thatch_front")
        elif name == "back":
            H.thatch(o, U, V, out, regions=[(blob((3.0, 1.2), 3.5, 1.1, 9, r), ptc)], theta=th, label="thatch_back")
        else:
            H.thatch(o, U, V, out, regions=[(blob((0.0, tr), 2.5, 2.4, 8, r), ptc)], theta=th, label="thatch_" + name)
    z_ridge = z_te + tr * st
    block(H.g["Roof"], (-ar - 0.7, -0.62, z_ridge - 0.42), (ar + 0.7, 0.62, z_ridge + 0.3),
          H.wood((1, 0, 0), "char"), label="ridge")
    for x in (-4.1, -1.4, 1.4, 4.1):
        for sy in (-1, 1):
            H.beam((x + sy * 0.08, sy * 1.05, z_ridge - 0.3), (x + sy * 0.08, -sy * 0.7, z_ridge + 0.3), w=0.26, h=0.26,
                   kind="char",
                   part="Roof", up=(1, 0, 0), label="karasu")

    def zu(y):                                            # underside of the front slope
        return KETA1 + (y + D2) * math.tan(th)
    for x in (4.2, 7.2):
        H.pole((x, -D2 - 0.4, zu(-D2 - 0.4) - 0.18), (x, -0.3, zu(-0.3) - 0.18), r=0.26, kind="char", part="Roof",
               label="sasu")
    for y in (-5.6, -3.6):
        H.pole((2.0, y, zu(y) - 0.12), (9.5, y, zu(y) - 0.12), r=0.16, kind="char", part="Roof", sides=6,
               label="yanaka")
    # ---- doors: one fallen against the wall, one cracked and tilted in its track ----
    def door_panel(top_poly, holes=(), regs=()):
        dg = Geo()
        panel(dg, top_poly, (0, -0.1, 0), (1, 0, 0), (0, 0, 1), 0.2, P("planks_dark", 3.0), holes=holes, regions=regs,
              label="door")
        for z in (1.0, 3.6, 6.1):
            block(dg, (0.12, -0.24, z), (2.78, -0.08, z + 0.3), H.wood((1, 0, 0), "wood"), label="batten")
        return dg
    dA = door_panel([(0, 0.6), (0.5, 0.2), (1.2, 0.0), (2.9, 0.0), (2.9, 6.9), (0, 6.9)],
                    regs=[(blob((0.4, 0.4), 1.5, 1.2, 8, r), P("char_ember", 3.0)),
                          (above([(-1, 2.4), (1.5, 1.6), (4, 0.9)], -9), P("char", 3.0))])
    lean = math.radians(17.5)
    MA = place((0.0, 0.0, 0.0), rx=-math.degrees(lean))
    MA = rest_on_ground(dA, Matrix.Translation((-12.56, -D2 - 0.18 - 0.12 - 6.9 * math.sin(lean), 0.0)) @ MA,
                        sink=0.08)
    merge(H.g["Debris"], dA, MA)
    dB = door_panel([(0, 0), (2.9, 0), (2.9, 6.85), (1.9, 6.6), (1.4, 5.6), (0.8, 5.9), (0.0, 4.9)],
                    regs=[(above([(-1, 4.3), (1.2, 4.6), (4, 5.6)]), P("char", 3.0)),
                          (blob((0.9, 5.4), 0.9, 0.6, 7, r), P("char_ember", 3.0))])
    merge(H.g["Debris"], dB, Matrix.Translation((-6.35, -D2 + 0.02, 1.72)) @ place((0, 0, 0), rx=4.0))
    H.cols.append([[-4.85, -D2 - 0.25, 5.2], [2.9, 0.6, 7.0], 0.0])
    H.cols.append([[-11.05, -D2 - 1.3, 3.4], [2.9, 2.2, 6.8], 0.0])
    # ---- step stone, fallen thatch under the hole ----
    rock(H.g["Stone"], (-6.6, -D2 - 1.75, 0.25), (1.5, 0.95, 0.6), 10, r, H.ps, zmin=-0.2, squash_top=0.45,
         label="step_stone")
    H.cols.append([[-6.6, -D2 - 1.75, 0.4], [2.8, 1.8, 0.8], 0.0])
    H.straw((8.0, -2.0, FLOOR_Z), 30.0, size=(1.5, 1.0), h=0.55, burnt=0.6)
    H.straw((5.9, -1.5, FLOOR_Z), -20.0, size=(1.0, 0.8), h=0.45, burnt=0.3)
    H.cols.append([[0.0, 0.0, 0.8], [2 * W2 + 0.4, 2 * D2 + 0.4, 1.6], 0.0])
    H.cols.append([[0.0, 0.0, 11.0], [2 * W2 + 2.0, 2 * D2 + 2.0, 2.4], 0.0])
    info = dict(footprint=[2 * W2, 2 * D2], floor_top=FLOOR_Z, roof="hipped thatch (30 deg), burned through on the "
                "front-right (charred rafters and purlins visible in the hole)", eave_beam_top=KETA1,
                ridge_top=round(z_ridge + 0.3, 2), doorway=dict(x=[-9.6, -3.4], clear_height=round(8.7 - FLOOR_Z, 2)),
                doors="itado sliding doors: one knocked out and leaning on the front wall, one cracked and tilted in its "
                      "track (each its own piece in oni_house_c_Debris)",
                interior_clearance="x -11.5..3.4, y -6.5..6.5 from the floor (1.6) to the tie beams (9.0): 7.4 studs")
    clear = {"interior": ((-11.5, -6.5, 1.7), (3.4, 6.5, 8.95)), "doorway": ((-9.4, -7.6, 1.75), (-6.5, -6.4, 8.6))}
    return H.result(info, clear)


def right_of(B, off, ext=12.0):
    """Region to the right (larger s) of a polyline B running toward +t, shifted left by off."""
    pts = [(s - off, t) for s, t in B]
    return [(pts[0][0], B[0][1] - 2.0)] + pts + [(pts[-1][0], B[-1][1] + 2.0), (pts[-1][0] + ext, B[-1][1] + 2.0),
                                                  (pts[0][0] + ext, B[0][1] - 2.0)]


def left_of(B, off, ext=12.0):
    """Region to the left (smaller s) of a polyline B running toward +t, shifted right by off."""
    pts = [(s + off, t) for s, t in B]
    return [(pts[0][0], B[0][1] - 2.0)] + pts + [(pts[-1][0], B[-1][1] + 2.0), (pts[-1][0] - ext, B[-1][1] + 2.0),
                                                  (pts[0][0] - ext, B[0][1] - 2.0)]


def rubble_slab(H, B0, U, V, thick, outline, regions, part="Debris", label="caved_thatch"):
    """A broken chunk of the thatch roof lying tilted: the bottom surface passes through B0
    (U along it, V up its slope); the top surface faces U x V."""
    B0, U, V = Vector(B0), Vector(U).normalized(), Vector(V).normalized()
    N = U.cross(V)
    panel(H.g[part], outline, B0 + N * thick, U, V, thick, P("thatch", S_THATCH), regions=regions,
          back=P("char", S_THATCH), cells=True, label=label)


# --------------------------------------------------------------------------------------
# oni_house_a: gable thatch roof caved in on the right side; the left gable wall and the
# back wall still stand, the front and right walls have burned down to stubs.
# --------------------------------------------------------------------------------------
def gen_house_a():
    H = Minka("oni_house_a", 22.0, 13.0)
    r = H.rnd
    W2, D2 = 11.0, 6.5
    pwc, pwe = H.pwc, H.pwe
    th = math.radians(32.0)
    ct, st, tt = math.cos(th), math.sin(th), math.tan(th)
    OH, OHX = 2.0, 1.6

    def zu(y):
        return KETA1 + (D2 - abs(y)) * tt
    # ---- posts (front: doorway between x = -8.6 and -4.6) ----
    for x, top, burn, br in ((-11, POST_TOP, 7.0, False), (-8.6, POST_TOP, 6.2, False), (-4.6, POST_TOP, 5.0, False),
                             (0, POST_TOP, 3.2, False), (5.5, 4.2, 2.4, True), (11, 6.0, 2.8, True)):
        H.post(x, -D2, top, burn=burn, broken=br)
    for x, top, burn, br in ((-11, POST_TOP, 7.4, False), (-5.5, POST_TOP, 7.0, False), (0, POST_TOP, 5.6, False),
                             (5.5, 7.2, 3.6, True), (11, 3.0, 1.6, True)):
        H.post(x, D2, top, burn=burn, broken=br)
    H.post(-W2, 0.0, zu(0) - 0.4, burn=8.2)                 # gable centre post up to the ridge beam
    H.post(W2, 0.0, 2.2, burn=0.9, broken=True)
    # ---- sills, eave beams, tie beams, ridge struts, ridge beam ----
    H.beam((-W2 - 0.5, -D2, 1.1), (2.0, -D2, 1.1), w=0.6, h=0.4, kind="wood", label="sill")
    H.beam((2.0, -D2, 1.1), (W2 + 0.5, -D2, 1.1), w=0.6, h=0.4, kind="char", label="sill")
    H.beam((-W2 - 0.5, D2, 1.1), (W2 + 0.5, D2, 1.1), w=0.6, h=0.4, kind="wood", label="sill")
    H.beam((-W2, -D2 + 0.3, 1.1), (-W2, D2 - 0.3, 1.1), w=0.6, h=0.4, kind="wood", label="sill")
    H.beam((W2, -D2 + 0.3, 1.1), (W2, D2 - 0.3, 1.1), w=0.6, h=0.4, kind="char", label="sill")
    H.beam((-W2 - 0.9, -D2, 9.4), (1.6, -D2, 9.4), kind="char", broken=(False, True), label="keta")
    H.beam((-W2 - 0.9, D2, 9.4), (3.4, D2, 9.4), kind="char", broken=(False, True), label="keta")
    H.beam((-W2, -D2 - 0.5, 9.4), (-W2, D2 + 0.5, 9.4), h=0.7, kind="char", label="gable_beam")
    for x in (-5.5, 0.0):
        H.beam((x, -D2, 9.4), (x, D2, 9.4), w=0.7, h=0.7, kind="char", label="tie_beam")
        H.post(x, 0.0, zu(0) - 0.4, burn=9.7, w=0.55, stone=False, col=False, base=9.7)
    H.pole((-W2 - OHX + 0.2, 0.0, zu(0) - 0.36), (0.9, 0.0, zu(0) - 0.36), r=0.42, kind="char", broken=(False, True),
           part="Roof", label="ridge_beam")
    for sy in (-1, 1):
        for x, y1, br in ((-8.0, 0.3, False), (-3.4, 0.3, False), ((1.3 if sy < 0 else 2.0), (3.0 if sy < 0 else 2.6), True)):
            y0 = D2 + 0.6
            H.pole((x, sy * y0, zu(y0) - 0.2), (x, sy * y1, zu(y1) - 0.2), r=0.26, kind="char", part="Roof",
                   broken=(False, br), label="sasu")
    # ---- thatch: the left part of both slopes, broken edge toward the cave-in ----
    z_te = KETA1 - OH * tt + THATCH_T / ct
    tr = (D2 + OH) / ct
    ptc, pte = P("char", S_THATCH), P("char_ember", S_THATCH)
    Bf = [(-1.5, 0.0)] + jagged((-1.5, 0.0), (0.7, tr), 7, 0.75, r, ends=False) + [(0.7, tr)]
    H.thatch((0, -(D2 + OH), z_te), (1, 0, 0), (0, ct, st), [(-W2 - OHX, 0.0)] + Bf + [(-W2 - OHX, tr)],
             holes=[blob((-3.6, 6.2), 0.9, 0.7, 8, r)],
             regions=[(right_of(Bf, 0.9), pte), (right_of(Bf, 3.4), ptc), (blob((-3.6, 6.2), 1.7, 1.4, 8, r), ptc),
                      (blob((-10.0, 1.0), 2.4, 1.0, 8, r), ptc)], theta=th, label="thatch_front")
    Bb = [(-2.4, 0.0)] + jagged((-2.4, 0.0), (-1.0, tr), 7, 0.75, r, ends=False) + [(-1.0, tr)]
    H.thatch((0, D2 + OH, z_te), (-1, 0, 0), (0, -ct, st), Bb + [(W2 + OHX, tr), (W2 + OHX, 0.0)],
             regions=[(left_of(Bb, 0.9), pte), (left_of(Bb, 3.0), ptc), (blob((6.0, 2.0), 2.8, 1.2, 9, r), ptc)],
             theta=th, label="thatch_back")
    z_ridge = z_te + tr * st
    rbeam(H.g["Roof"], (-W2 - OHX - 0.2, 0.0, z_ridge + 0.05), (0.2, 0.0, z_ridge + 0.05), 1.2, 0.7,
          H.wood((1, 0, 0), "char"), r, broken=(False, True), pin=P("char_ember", 1.5), seg=S_POST, label="ridge")
    # ---- walls: gable wall and back wall standing, front and right burned to stubs ----
    gP0, gP1 = (-W2, D2), (-W2, -D2)
    gtop = [(2 * D2, KETA1 - 0.05), (D2, zu(0) - 0.05), (0.0, KETA1 - 0.05)]
    H.wall(gP0, gP1, gtop, regions=[(blob((D2, 12.2), 2.2, 1.0, 9, r), pwe),
                                    (above(line_through([(-1, 8.1), (14, 7.6)], r, amp=0.4)), pwc)],
           label="wall_gable")
    bP0, bP1 = (W2, D2), (-W2, D2)

    def hb(s):
        return 2.8 if s < 1.4 else (4.4 if s < 8.0 else 4.4 + (s - 8.0) * 0.94)
    bt = H.board_tops(bP0, bP1, 0.0, 11.4, hb, amp=0.28)
    btop = [(2 * W2, KETA0), (11.4, KETA0)] + list(reversed(bt))
    H.wall(bP0, bP1, btop, regions=H.stub_burn(btop, 0.0, 2 * W2, char_depth=2.2, embers=4), label="wall_back")
    fP0, fP1 = (-W2, -D2), (W2, -D2)
    for sa, sb, hf in ((0.0, 2.4, lambda s: 4.0), (6.4, 22.0, lambda s: 2.4 if s < 13.5 else 3.5)):
        bt = H.board_tops(fP0, fP1, sa, sb, hf, amp=0.35)
        top = list(reversed(bt))
        H.wall(fP0, fP1, top, s_range=(sa, sb), regions=H.stub_burn(top, sa, sb, 1.4, embers=max(1, int((sb - sa) / 4))),
               label="wall_front_stub")
    rP0, rP1 = (W2, -D2), (W2, D2)
    bt = H.board_tops(rP0, rP1, 0.0, 2 * D2, lambda s: 2.4 + 0.5 * math.sin(s * 0.7), amp=0.35)
    top = list(reversed(bt))
    H.wall(rP0, rP1, top, regions=H.stub_burn(top, 0.0, 2 * D2, 1.3, embers=3), label="wall_right_stub")
    # ---- floor: charred on the right, burned through in two places ----
    pfc, pfe = H.floor_paints()
    hA = blob((5.6, -2.4), 1.7, 1.2, 10, r, jag=0.25)
    hB = blob((8.7, 3.2), 1.1, 0.85, 8, r, jag=0.25)
    Bfl = [(1.0, -8.0)] + jagged((1.0, -8.0), (2.8, 8.0), 9, 0.6, r, ends=False) + [(2.8, 8.0)]
    H.floor([(-W2, -D2), (W2, -D2), (W2, D2), (-W2, D2)], holes=[hA, hB],
            regions=[(scaled(hA, 1.3), pfe), (scaled(hB, 1.35), pfe), (right_of(Bfl, 0.0), pfc),
                     (blob((-6.6, -5.6), 1.8, 0.7, 8, r), pfc)])
    # ---- the caved-in right part: thatch chunks, fallen ridge beam and rafters ----
    phi = math.atan2(4.35 - FLOOR_Z, 6.6 - 1.0)
    out1 = [(3.0, -0.2), (5.2, 0.15), (7.4, -0.25), (9.4, 0.1), (9.7, 2.4), (9.2, 4.6), (9.6, 6.5), (7.4, 6.9),
            (5.6, 6.5), (3.4, 6.95), (3.1, 4.6), (2.6, 2.4)]
    rubble_slab(H, (0.0, 1.0, FLOOR_Z - 0.05), (1, 0, 0), (0, math.cos(phi), math.sin(phi)), 0.9, out1,
                [(blob((6.5, 3.2), 1.3, 1.0, 8, r), pte), (blob((5.0, 2.6), 3.0, 2.4, 9, r), ptc),
                 ([(2, 5.4), (11, 6.0), (11, 9), (2, 9)], ptc)])
    phi2 = math.atan2(3.5, 4.1)
    out2 = [(3.7, -0.15), (6.2, 0.2), (9.9, -0.1), (9.6, 2.6), (10.1, 5.6), (8.0, 5.9), (6.4, 5.4), (4.2, 5.9),
            (3.9, 2.9)]
    rubble_slab(H, (0.0, -10.5, -0.08), (1, 0, 0), (0, math.cos(phi2), math.sin(phi2)), 0.9, out2,
                [(blob((7.6, 3.6), 1.4, 1.1, 8, r), pte), (blob((7.0, 3.0), 3.4, 2.8, 9, r), ptc),
                 ([(3, 4.6), (11, 4.9), (11, 8), (3, 8)], ptc)])
    H.pole((1.6, -0.8, FLOOR_Z + 0.38), (5.4, 6.25, 7.55), r=0.42, kind="char", broken=(True, True), part="Debris",
           label="fallen_ridge")
    H.pole((1.8, -5.4, FLOOR_Z + 0.25), (7.6, -0.6, FLOOR_Z + 0.25), r=0.26, kind="char", broken=(True, True),
           part="Debris", label="fallen_rafter")
    H.straw((1.9, 4.6, FLOOR_Z), 25.0, size=(1.2, 0.8), h=0.45, burnt=0.5)
    H.straw((10.2, -12.0, 0.0), -30.0, size=(1.4, 0.9), h=0.5, burnt=0.7)
    H.coals((3.4, -3.8, FLOOR_Z), n=4, spread=0.7)
    H.coals((8.0, -12.0, 0.0), n=3, spread=0.6)
    rock(H.g["Stone"], (-6.6, -D2 - 1.6, 0.25), (1.5, 0.95, 0.6), 10, r, H.ps, zmin=-0.2, squash_top=0.45,
         label="step_stone")
    H.cols += [[[-6.6, -D2 - 1.6, 0.4], [2.8, 1.8, 0.8], 0.0], [[0.0, 0.0, 0.8], [2 * W2 + 0.4, 2 * D2 + 0.4, 1.6], 0.0],
               [[6.2, 3.8, 3.0], [6.6, 5.6, 2.8], 0.0], [[6.8, -8.5, 1.75], [6.2, 4.0, 3.5], 0.0],
               [[-5.8, 0.0, 11.7], [13.6, 2 * (D2 + OH), 3.8], 0.0]]
    info = dict(footprint=[2 * W2, 2 * D2], floor_top=FLOOR_Z,
                roof="gable thatch (32 deg); the left part still stands on the gable wall, the back wall and the posts; "
                     "the right part caved in (a chunk slid inside onto the floor and the back wall stub, another "
                     "slid out over the front stubs), with the snapped ridge beam fallen into the house",
                standing_walls=["left gable wall (x = -11)", "back wall (y = +6.5, full height on the left 2/3)"],
                doorway=dict(x=[-8.2, -5.0], note="gap in the burned front wall, step stone in front"),
                interior_clearance="x -10.3..-1.4, y -6.0..6.0 from the floor (1.6) to the tie beams (9.0): 7.4 studs")
    clear = {"interior": ((-10.3, -6.0, 1.7), (-1.4, 6.0, 8.95)), "doorway": ((-8.1, -7.0, 1.7), (-5.1, -6.0, 8.95))}
    return H.result(info, clear)


# --------------------------------------------------------------------------------------
# oni_house_b: only the post frame and part of the floor are left; the roof beams have
# fallen inside.
# --------------------------------------------------------------------------------------
def gen_house_b():
    H = Minka("oni_house_b", 18.0, 12.0)
    r = H.rnd
    W2, D2 = 9.0, 6.0
    for x, top, burn, br in ((-9, POST_TOP, 6.4, False), (-3, 5.5, 2.8, True), (3, 3.0, 1.4, True), (9, POST_TOP, 4.6, False)):
        H.post(x, -D2, top, burn=burn, broken=br)
    for x, top, burn, br in ((-9, POST_TOP, 7.0, False), (-3, POST_TOP, 6.0, False), (3, 7.0, 3.4, True), (9, 4.5, 2.0, True)):
        H.post(x, D2, top, burn=burn, broken=br)
    H.post(-W2, 0.0, POST_TOP, burn=7.4)
    H.post(W2, 0.0, 1.3, burn=0.5, broken=True)
    H.post(-3.0, 0.0, 12.2, burn=4.6, broken=True, w=1.1)          # daikoku-bashira, the central pillar
    # ---- what is left of the beams ----
    H.beam((-W2, -D2 - 0.5, 9.4), (-W2, D2 + 0.5, 9.4), h=0.7, kind="char", label="tie_beam")
    H.beam((-W2 - 1.2, D2, 9.4), (-0.6, D2, 9.4), kind="char", broken=(False, True), label="keta")
    H.beam((-3.0, D2 + 0.3, 9.4), (-3.0, -2.6, 9.4), w=0.7, h=0.7, kind="char", broken=(False, True), label="tie_beam")
    H.beam((-W2 - 1.2, -D2, 9.4), (-6.8, -D2, 9.4), kind="char", broken=(False, True), label="keta")
    H.beam((-0.6, -9.2, 0.35), (-3.1, -6.2, 5.85), kind="char", broken=(True, True), part="Debris",
           label="fallen_keta")
    # ---- sills and joists ----
    H.beam((-W2, -D2 + 0.3, 1.1), (-W2, D2 - 0.3, 1.1), w=0.6, h=0.4, kind="wood", label="sill")
    H.beam((-W2 - 0.5, D2, 1.1), (3.8, D2, 1.1), w=0.6, h=0.4, kind="char", broken=(False, True), label="sill")
    H.beam((-W2 - 0.5, -D2, 1.1), (0.6, -D2, 1.1), w=0.6, h=0.4, kind="char", broken=(False, True), label="sill")
    H.beam((-W2 + 0.2, 2.0, 1.03), (2.0, 2.0, 1.03), w=0.5, h=0.5, kind="char", broken=(False, True), label="joist")
    H.beam((-W2 + 0.2, -2.0, 1.03), (3.4, -2.0, 1.03), w=0.5, h=0.5, kind="char", broken=(False, True), label="joist")
    H.post(3.0, -2.0, 0.86, w=0.44, col=False)
    H.post(-6.0, 2.0, 0.86, w=0.44, col=False)
    H.beam((2.6, 1.5, 0.25), (6.8, 2.7, 0.25), w=0.5, h=0.5, kind="char", broken=(True, True), part="Debris",
           label="fallen_joist")
    H.beam((5.0, -D2 - 0.6, 0.2), (8.4, -D2 + 0.4, 0.2), w=0.6, h=0.4, kind="char", broken=(True, False),
           part="Debris", label="fallen_sill")
    # ---- the floor that is left (left part), burned edge toward the right ----
    pfc, pfe = H.floor_paints()
    Bfl = [(-1.2, -D2)] + jagged((-1.2, -D2), (0.6, D2), 9, 0.65, r, ends=False) + [(0.6, D2)]
    hole = blob((-5.6, 3.2), 1.0, 0.8, 8, r)
    H.floor([(-W2, -D2)] + Bfl + [(-W2, D2)], holes=[hole],
            regions=[(scaled(hole, 1.35), pfe), (right_of(Bfl, 0.7), pfe), (scaled(hole, 2.0), pfc),
                     (right_of(Bfl, 2.8), pfc)])
    # ---- low board stubs of the walls that burned away ----
    lP0, lP1 = (-W2, D2), (-W2, -D2)
    bt = H.board_tops(lP0, lP1, 0.0, 2 * D2, lambda s: 2.5 + 0.35 * math.sin(s * 0.9), amp=0.35)
    top = list(reversed(bt))
    H.wall(lP0, lP1, top, regions=H.stub_burn(top, 0.0, 2 * D2, 0.9, embers=3), label="wall_stub")
    bP0, bP1 = (W2, D2), (-W2, D2)
    bt = H.board_tops(bP0, bP1, 12.4, 2 * W2, lambda s: 2.9, amp=0.4)
    top = list(reversed(bt))
    H.wall(bP0, bP1, top, s_range=(12.4, 2 * W2),
           regions=H.stub_burn(top, 12.4, 2 * W2, 1.0, embers=2), label="wall_stub")
    # ---- roof beams fallen inside ----
    H.pole((7.6, -3.4, 0.38), (3.15, 5.75, 7.32), r=0.42, kind="char", broken=(True, True), part="Debris",
           label="fallen_ridge")
    H.pole((2.4, -4.4, 0.3), (-2.42, -0.62, 9.0), r=0.3, kind="char", broken=(True, True), part="Debris",
           label="fallen_sasu")
    H.pole((-0.3, 4.6, FLOOR_Z + 0.28), (7.0, -1.0, 0.28), r=0.28, kind="char", broken=(True, True), part="Debris",
           label="fallen_sasu")
    H.straw((5.8, 3.9, 0.0), 20.0, size=(1.7, 1.1), h=0.55, burnt=0.6)
    H.straw((-4.6, -8.2, 0.0), -15.0, size=(1.0, 0.7), h=0.4, burnt=0.4)
    H.coals((5.0, 0.4, 0.0), n=5, spread=0.8)
    H.coals((-1.6, 3.4, FLOOR_Z), n=4, spread=0.55)
    H.coals((1.4, -7.6, 0.0), n=3, spread=0.5)
    rock(H.g["Stone"], (-6.0, -D2 - 1.4, 0.25), (1.4, 0.9, 0.6), 10, r, H.ps, zmin=-0.2, squash_top=0.45,
         label="step_stone")
    H.cols += [[[-6.0, -D2 - 1.4, 0.4], [2.6, 1.7, 0.8], 0.0], [[-4.6, 0.0, 0.8], [9.2, 2 * D2 + 0.4, 1.6], 0.0]]
    info = dict(footprint=[2 * W2, 2 * D2], floor_top=FLOOR_Z,
                standing="post frame: 6 full posts, 6 broken posts, the 12.2-stud central pillar (daikoku-bashira), "
                         "the left tie beam and pieces of the eave beams",
                floor="left part (x -9 .. about 0) with a burned edge and one hole; joists and sills exposed on the right",
                fallen="ridge beam (on the broken back post), two sasu rafters (one leaning on the central pillar), "
                       "the front eave beam (leaning on a broken post), a joist and a sill",
                interior_clearance="x -8.5..-3.75, y -5.5..5.5 from the floor (1.6) up to 8.95 (beams at 9.0)")
    clear = {"interior": ((-8.5, -5.5, 1.7), (-3.75, 5.5, 8.95))}
    return H.result(info, clear)


# ======================================================================================
# 6. MODEL ASSEMBLY
# ======================================================================================
def part_name(mid, key):
    return mid + "_" + key


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
    parts = {part_name(mid, k): g for k, g in data["parts"].items() if g.ntris()}
    rec["checks"] = dict(
        facing=facing_check(geos),
        floating=floating_check(parts),
        edges={k: edge_stats(g) for k, g in parts.items()},
        max_uv_anisotropy=round(max(uv_stretch(g) for g in geos), 3),
        coplanar_overlaps=coplanar_overlaps(parts),
        ngons=0)
    clear = {}
    for name, (lo, hi) in data.get("clear_boxes", {}).items():
        clear[name] = dict(min=list(lo), max=list(hi), triangles=box_clear(parts, lo, hi),
                           colliders=collider_hits(STATE["colliders"][mid], lo, hi))
    rec["checks"]["clear_boxes"] = clear
    return col, objs


def collider_hits(cols, lo, hi):
    """Number of collider boxes (yaw-rotated) overlapping the open box (lo, hi) (conservative:
    uses the rotated box's axis-aligned bounds)."""
    n = 0
    for c, s, yaw in cols:
        a = math.radians(yaw)
        ex = abs(math.cos(a)) * s[0] / 2 + abs(math.sin(a)) * s[1] / 2
        ey = abs(math.sin(a)) * s[0] / 2 + abs(math.cos(a)) * s[1] / 2
        bmin = (c[0] - ex, c[1] - ey, c[2] - s[2] / 2)
        bmax = (c[0] + ex, c[1] + ey, c[2] + s[2] / 2)
        if all(bmin[k] < hi[k] - 1e-6 and bmax[k] > lo[k] + 1e-6 for k in range(3)):
            n += 1
    return n


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
        for extra in TILE_EXTRA.get(t, []):
            used[extra + " (painted into tile %s)" % t] = list(PAL[extra])
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
    mpts = [o.matrix_world @ v.co for o in bpy.data.collections[mid].objects if o.type == "MESH"
            for v in o.data.vertices]
    set_visible({mid, "_render_helpers"})
    out = []
    for vname, d in VIEWS.items():
        # the 5-stud human stands front-right; in the 3/4 view back-right, so it hides nothing
        hum.location = (mx[0] + 2.5, (mx[1] - 1.0) if vname == "three_quarter" else (mn[1] + 1.0), 0.0)
        pts = mpts + [hum.location + Vector((sx, sy, sz)) for sx in (-1.3, 1.3) for sy in (-0.6, 0.6)
                      for sz in (0.0, 5.0)]
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
    gap = 5.0
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
# 9. VILLAGE SCENE + EXPORT VERIFICATION
# ======================================================================================
VILLAGE = [   # (model, (x, y), yaw deg) -- the burned village in its muddy clearing (render only)
    ("torii_broken", (0.0, -34.0), 0.0),
    ("oni_house_c", (-25.0, 0.0), 90.0),
    ("oni_house_a", (25.0, 2.0), -90.0),
    ("oni_house_b", (-1.0, 26.0), 0.0),
    ("stake_fence", (-20.0, -35.0), 0.0), ("stake_fence", (-43.3, -33.0), 10.0),
    ("stake_fence", (20.0, -35.0), 0.0), ("stake_fence", (43.3, -33.0), -10.0),
    ("stake_fence", (-56.5, -15.5), 76.0), ("stake_fence", (56.5, -15.5), -76.0),
    ("stake_fence", (-60.0, 8.0), 90.0),
    ("battle_debris_a", (7.0, -50.0), 20.0), ("battle_debris_b", (-9.0, -66.0), -35.0),
    ("trampled_log", (16.0, -74.0), 64.0),
]


def build_village_scene():
    """Dark muddy clearing (flat), the trampled trail coming in from the forest (-Y) through
    the broken torii, the three burned houses around a yard, the palisade, the battle
    debris and a felled log on the trail (render only, never exported)."""
    col = new_collection("_village_scene")
    rng = random.Random(77)
    g = Geo()
    n = 40
    V = {}
    for i in range(n + 1):
        for j in range(n + 1):
            x, y = -1000.0 + 2000.0 * i / n, -1000.0 + 2000.0 * j / n
            V[(i, j)] = g.v((x, y, -0.06 + rng.uniform(-0.01, 0.01)))
    for i in range(n):
        for j in range(n):
            q = [V[(i, j)], V[(i + 1, j)], V[(i + 1, j + 1)], V[(i, j + 1)]]
            uv = (0.5, 0.5)
            g.add_tri(q[0], q[1], q[2], uv, uv, uv, "x", False)
            g.add_tri(q[0], q[2], q[3], uv, uv, uv, "x", False)
    mesh_from_geo("_ground", g, get_flat_material("_ScnMud", (78, 66, 54)), col)
    # trampled trail: a lighter strip of churned mud from the forest to the village yard
    t = Geo()
    pts = [(-40.0, -1000.0), (-8.0, -120.0), (2.0, -80.0), (1.0, -54.0), (0.0, -34.0), (0.0, -14.0), (0.0, 8.0)]
    hw = [14.0, 13.0, 12.0, 9.0, 6.0, 12.0, 14.0]
    L, R = [], []
    for k, (x, y) in enumerate(pts):
        a = pts[min(k + 1, len(pts) - 1)]
        b = pts[max(k - 1, 0)]
        d = Vector((a[0] - b[0], a[1] - b[1], 0.0)).normalized()
        nrm = Vector((-d.y, d.x, 0.0))
        L.append(t.v((x - nrm.x * hw[k], y - nrm.y * hw[k], -0.015)))
        R.append(t.v((x + nrm.x * hw[k], y + nrm.y * hw[k], -0.015)))
    for k in range(len(pts) - 1):
        uv = (0.5, 0.5)
        t.add_tri(L[k], R[k + 1], R[k], uv, uv, uv, "x", False)          # wound to face up
        t.add_tri(L[k], L[k + 1], R[k + 1], uv, uv, uv, "x", False)
    mesh_from_geo("_trail", t, get_flat_material("_ScnTrail", (122, 104, 84)), col)
    layout = []
    for mid, (x, y), rz in VILLAGE:
        if mid not in bpy.data.collections:
            continue
        for o in bpy.data.collections[mid].objects:
            ob = bpy.data.objects.new("_scn_" + o.name, o.data)
            ob.matrix_world = Matrix.Translation(Vector((x, y, 0.0))) @ Matrix.Rotation(math.radians(rz), 4, "Z")
            col.objects.link(ob)
        layout.append([mid, [x, y, 0.0], rz])
    return col, layout


def render_village_scene(cam):
    col, layout = build_village_scene()
    set_visible({"_village_scene"})
    cam.data.type = "PERSP"
    cam.data.lens = 30.0
    cam.data.sensor_fit = "AUTO"
    cam.data.clip_start = 0.5
    cam.data.clip_end = 5000.0
    outs = []
    for name, loc, tgt, caption in (
            ("village_scene.png", (40.0, -86.0, 64.0), (0.0, -10.0, 0.0),
             "village scene: the burned Ogre village (3 houses, palisade, broken torii) and the Orc army's trail"),
            ("village_scene_yard.png", (-3.0, -66.0, 7.5), (2.0, 0.0, 6.0),
             "village scene: through the broken torii into the yard (eye level, 30 mm)")):
        cam.location = Vector(loc)
        cam.rotation_euler = (Vector(tgt) - cam.location).to_track_quat("-Z", "Y").to_euler()
        p = os.path.join(REN, name)
        render_to(p)
        im = Image.open(p).convert("RGB")
        dr = ImageDraw.Draw(im)
        dr.rectangle([0, 0, 1024, 30], fill=BG)
        dr.text((12, 7), caption, fill=(30, 30, 30), font=_font(14))
        im.save(p)
        outs.append(os.path.relpath(p, OUT))
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
GENERATORS = (("oni_house_a", "gen_house_a"), ("oni_house_b", "gen_house_b"), ("oni_house_c", "gen_house_c"),
              ("stake_fence", "gen_fence"), ("battle_debris_a", "gen_debris_a"), ("battle_debris_b", "gen_debris_b"),
              ("torii_broken", "gen_torii_broken"), ("trampled_log", "gen_log"))


def generators():
    g = {}
    for mid, fn in GENERATORS:
        if (not OPT_ONLY or mid in OPT_ONLY) and fn in globals():
            g[mid] = globals()[fn]
    return g


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
            problems.append("%s: %d back-facing first hits %s" % (mid, ck["facing"]["backfacing_first_hits"],
                                                                 ck["facing"]["examples"]))
        if ck["floating"]["floating"] or ck["floating"]["inverted_volume_pieces"]:
            problems.append("%s: floating/inverted pieces %s" % (mid, ck["floating"]))
        if not -1.0 - 1e-6 <= r["bbox_min"][2] <= 0.0:
            problems.append("%s: lowest point %.3f outside [-1, 0]" % (mid, r["bbox_min"][2]))
        if ck["max_uv_anisotropy"] > 1.6:
            problems.append("%s: UV anisotropy %.2f" % (mid, ck["max_uv_anisotropy"]))
        for k, ar in ck["coplanar_overlaps"].items():
            labs = [x.split(":")[1] for x in k.split(" / ")]
            # allowed (never visible): wall ends meeting inside a corner post, coal bottoms in the ash
            if not (all(lb.startswith("wall") for lb in labs) or all(lb == "coal" for lb in labs)):
                problems.append("%s: coplanar overlap (z-fighting) %s = %.3f" % (mid, k, ar))
        for name, cb in ck["clear_boxes"].items():
            if cb["triangles"] or cb["colliders"]:
                problems.append("%s: clear box %s holds %d triangles / %d colliders" % (
                    mid, name, cb["triangles"], cb["colliders"]))
        for name, res in data[mid].get("extra_checks", {}).items():
            ck[name] = res
            if not res.get("ok", True):
                problems.append("%s: %s failed %s" % (mid, name, res))
    ident = [o.name for o in bpy.data.objects if any(abs(o.matrix_world[i][j] - (1.0 if i == j else 0.0)) > 1e-6
                                                    for i in range(4) for j in range(4))]
    mods = [o.name for o in bpy.data.objects if len(o.modifiers)]
    if ident or mods:
        problems.append("unapplied transforms %s / modifiers %s" % (ident, mods))
    STATE["checks"] = dict(problems=problems, objects_with_unapplied_transforms=ident, objects_with_modifiers=mods,
                           lowest_point_per_model={m: STATE["models"][m]["bbox_min"][2] for m in ids})
    for mid in ids:
        r = STATE["models"][mid]
        print("%-16s size %-22s tris %5d / %5d  facing-back %d  floating %d  aniso %.2f" % (
            mid, r["size"], r["triangles"]["total"], r["budget"], r["checks"]["facing"]["backfacing_first_hits"],
            len(r["checks"]["floating"]["floating"]), r["checks"]["max_uv_anisotropy"]))
    print("PROBLEMS:", problems)
    if problems and "--force" not in _argv:
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
        rows = [[m for m in ("oni_house_a", "oni_house_b", "oni_house_c") if m in ids],
                [m for m in ("torii_broken", "stake_fence", "trampled_log") if m in ids],
                [m for m in ("battle_debris_a", "battle_debris_b") if m in ids]]
        render_lineup(os.path.join(REN, "lineup.png"), [r for r in rows if r], cam, hum,
                      "Batch C lineup - the burned Ogre village & the Orc trail (front, orthographic)")
        renders["lineup"] = ["renders/lineup.png"]
        if len(ids) == len(MODEL_IDS):
            renders["village_scene"], STATE["village_layout"] = render_village_scene(cam)
    # ---- verify exports ----
    verify = verify_exports(ids) if OPT_VERIFY else {}
    write_reports(ids, renders, verify)
    if OPT_ZIP:
        make_zip()
    print("DONE")


def write_reports(ids, renders, verify):
    rep = {"batch": "C", "title": "The burned Ogre village and the Orc army's trail", "generator": "build.py",
           "blender": bpy.app.version_string,
           "units": "1 Blender unit = 1 stud; scene.unit_settings.scale_length = 0.01; Z up; front = -Y",
           "size_convention": "[x, height (Blender Z), depth (Blender Y)] in studs, from the mesh bounding box",
           "atlas": {"file": "atlas.png", "size": [ATLAS_PX, ATLAS_PX], "tile_px": TILE_PX, "inset": INSET,
                     "tiles": {k: {"col": c, "row_from_top": r, "kind": TILE_KIND[k], "palette": TILE_PAL[k],
                                   "base_rgb": list(PAL[TILE_PAL[k]]),
                                   "extra_palette": {e: list(PAL[e]) for e in TILE_EXTRA.get(k, [])},
                                   "mean_rgb": [round(v, 1) for v in STATE["tile_mean"][k]]}
                               for k, (c, r) in TILES.items()}},
           "models": {}, "checks": STATE["checks"], "export_verification": verify, "renders": renders}
    if STATE.get("village_layout"):
        rep["village_scene_layout"] = STATE["village_layout"]
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
