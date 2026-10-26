#!/usr/bin/env python3
"""
Great Forest of Jura -- BATCH D -- Landmarks at the edges of the forest -- one re-runnable generator.

Builds every model of batch D procedurally (no manual steps): paints the shared texture
atlas, models the eight landmarks / props, runs the self-checks, exports FBX / GLB / .blend,
renders the review images (5 views per model, lineup, scale check), re-imports the exports
to verify them and writes report.json + colliders.json.

Run (either works):
    blender -b -P build.py [-- options]      Blender 4.x / 5.x (needs Pillow in Blender's Python)
    python3 build.py [options]               with `pip install bpy pillow` (bpy 5.2 / Python 3.13)

Options:
    --no-render   skip the PNG renders
    --no-verify   skip re-importing the exported files
    --no-zip      do not (re)create ../jura_batch_D.zip
    --only=<id>   (development) build only the given model(s); repeatable
    --keep-going  (development) export and render even if a self-check fails

All outputs are written next to this file (the jura_batch_D/ folder).
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
from PIL import Image, ImageDraw, ImageFont, ImageFilter

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
BATCH = "jura_batch_D"
_argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
OPT_RENDER = "--no-render" not in _argv
OPT_VERIFY = "--no-verify" not in _argv
OPT_ZIP = "--no-zip" not in _argv
OPT_ONLY = [a.split("=", 1)[1] for a in _argv if a.startswith("--only=")]   # dev: build a subset

# --------------------------------------------------------------------------------------
# palette (sRGB 0-255) -- exact values from the shared spec (+ the cave black of this batch)
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
    "cave_black": (16, 15, 14),          # "black-coloured back plate" asked by limestone_cave
}

# 4x4 atlas, 256 px tiles. (col, row) with row 0 = TOP row of the PNG.
TILES = {
    "stone_grey": (0, 0), "dwarf_stone": (1, 0), "masonry": (2, 0), "runes": (3, 0),
    "gold": (0, 1), "dark_stone": (1, 1), "charred": (2, 1), "ember": (3, 1),
    "limestone": (0, 2), "cave_dark": (1, 2), "moss": (2, 2), "claw": (3, 2),
    "wood": (0, 3), "wood_light": (1, 3), "bone": (2, 3), "vermilion": (3, 3),
}
TILE_PAL = {   # palette entries painted into each tile (first = base colour)
    "stone_grey": ["rock_grey"], "dwarf_stone": ["dwarf_stone"], "masonry": ["dwarf_stone"],
    "runes": ["gold_trim", "dwarf_stone"], "gold": ["gold_trim"], "dark_stone": ["dark_iron"],
    "charred": ["charred", "ember"], "ember": ["ember"], "limestone": ["limestone"],
    "cave_dark": ["limestone", "cave_black"], "moss": ["moss"], "claw": ["rock_grey"],
    "wood": ["bark"], "wood_light": ["bark_light"], "bone": ["cream"], "vermilion": ["vermilion"],
}
TILE_KIND = {
    "stone_grey": "stone (soft round facet blotches)", "dwarf_stone": "stone (soft round facet blotches)",
    "masonry": "dwarven ashlar: 4 courses x 2 blocks, running bond, mortar + bevel (periodic)",
    "runes": "gold rune glyphs (2 rows x 3 abstract glyphs, no text) engraved in darkened dwarf stone",
    "gold": "flat", "dark_stone": "stone (soft round blotches) in dark iron: Milim's dark stone, forged-iron doors, slate",
    "charred": "charred ground (soft blotches) + branching ember cracks", "ember": "flat (glow)",
    "limestone": "stone (soft round facet blotches)",
    "cave_dark": "vertical fade: limestone shadow (top, v=1) to cave black (v <= 0.5)",
    "moss": "moss clumps (periodic)", "claw": "rock grey stone + 4 parallel claw gouges",
    "wood": "wood planks (vertical seams + grain)", "wood_light": "timber grain", "bone": "flat", "vermilion": "flat",
}
ATLAS_PX = 1024
TILE_PX = 256
INSET = 0.04

BUDGET = {"dwarf_gate": 6000, "blumund_marker": 2500, "milim_marker": 2500, "limestone_cave": 5000,
          "limestone_rock_a": 600, "limestone_rock_b": 600, "limestone_rock_c": 600, "wolf_den": 2500}
MODEL_IDS = ["dwarf_gate", "blumund_marker", "milim_marker", "limestone_cave",
             "limestone_rock_a", "limestone_rock_b", "limestone_rock_c", "wolf_den"]

STATE = {"tile_mean": {}, "models": {}, "checks": {}, "colliders": {}}


def hash_seed(s):
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16)


# ======================================================================================
# 1. TEXTURE ATLAS (PIL + numpy, painted at 4x and downsampled for clean edges)
#    Coordinates are TILE-LOCAL: (0, 0) .. (1, 1) is the part of the tile inside the 4 %
#    inset, i.e. exactly the window the UVs can reach (tile_uv). Patterned tiles are
#    periodic in that window, so faces mapped with world-anchored UVs continue the
#    pattern seamlessly across face borders.
# ======================================================================================
SS = 4
NS = TILE_PX * SS


def _grid():
    y, x = np.mgrid[0:NS, 0:NS].astype(np.float32)
    px, py = (x + 0.5) / NS, (y + 0.5) / NS                 # 0..1 over the whole tile, y down
    lx = (px - INSET) / (1.0 - 2 * INSET)
    ly = (py - INSET) / (1.0 - 2 * INSET)
    return lx, ly                                           # tile-local (inset window = 0..1)


def _to_px(u, v):
    """tile-local (u, v up) -> supersampled pixel coordinates (x, y down)."""
    return ((INSET + (1 - 2 * INSET) * u) * NS, (INSET + (1 - 2 * INSET) * (1.0 - v)) * NS)


def _c(rgb):
    return np.array(rgb, np.float32)


def _lighten(c, a):
    return c + (255.0 - c) * a


def _finish(img):
    img = np.clip(img, 0, 255).astype(np.uint8)
    return Image.fromarray(img, "RGB").resize((TILE_PX, TILE_PX), Image.LANCZOS)


def _pnoise(rng, cells, amp):
    """Smooth noise in [-amp, amp], exactly periodic over the tile-local window
    (sum of plane waves with integer frequencies, amplitude ~ 1/frequency)."""
    x, y = _grid()
    n = np.zeros_like(x)
    tot = 0.0
    fmax = max(1, cells // 2)
    for _ in range(3 * cells):
        fx, fy = rng.randint(-fmax, fmax + 1), rng.randint(-fmax, fmax + 1)
        if fx == 0 and fy == 0:
            fx = 1
        a = 1.0 / math.hypot(fx, fy)
        n += a * np.sin(2 * np.pi * (fx * x + fy * y) + rng.uniform(0, 2 * np.pi))
        tot += a
    n /= max(1e-6, np.abs(n).max())
    return n * amp


def _pdist(a, c):
    d = np.abs(a - c) % 1.0
    return np.minimum(d, 1.0 - d)


def _blobs(rng, n, rmin, rmax, amin, amax):
    x, y = _grid()
    v = np.zeros_like(x)
    for _ in range(n):
        cx, cy = rng.uniform(0, 1), rng.uniform(0, 1)
        r = rng.uniform(rmin, rmax)
        a = rng.uniform(amin, amax)
        d2 = (_pdist(x, cx) ** 2 + _pdist(y, cy) ** 2) / (r * r)
        v += a * np.exp(-d2 * 1.8)
    return v


def stone_field(rng, amp=1.0):
    """Multiplier field of soft round blotches (facet colour variation) + fine grit; periodic."""
    v = 1.0 + _pnoise(rng, 5, 0.04 * amp) + _blobs(rng, 44, 0.07, 0.21, -0.12 * amp, 0.12 * amp)
    v += _pnoise(rng, 48, 0.022 * amp)
    return v


def paint_flat(rgb, rng):
    n = _pnoise(rng, 10, 0.022) + rng.uniform(-0.008, 0.008, (NS, NS)).astype(np.float32)
    return _finish(_c(rgb) * (1.0 + n)[..., None])


def paint_stone(base, rng, amp=1.0):
    return _finish(_c(base) * stone_field(rng, amp)[..., None])


def paint_masonry(base, rng):
    """Dwarven ashlar: 4 courses per tile (v), 2 blocks per course (u), running bond.
    Darker mortar joints, a light bevel along each block top and a shadow under it."""
    x, y = _grid()
    u, v = x % 1.0, (1.0 - y) % 1.0
    nc = 4
    cv = v * nc
    ci = np.floor(cv).astype(np.int32) % nc
    fv = cv - np.floor(cv)
    off = np.where(ci % 2 == 1, 0.25, 0.0)
    bu = (u + off) * 2.0
    bi = np.floor(bu).astype(np.int32) % 2
    fu = bu - np.floor(bu)
    shade = rng.uniform(0.9, 1.08, (nc, 2)).astype(np.float32)[ci, bi]
    val = shade * stone_field(rng, 0.8)
    du = np.minimum(fu, 1.0 - fu) * 0.5
    dv = np.minimum(fv, 1.0 - fv) / nc
    d = np.minimum(du, dv)
    val = np.where(d < 0.006, val * 0.52, np.where(d < 0.011, val * 0.82, val))
    top = (fv > 1.0 - 0.06) & (d >= 0.006)
    bot = (fv < 0.07) & (d >= 0.006)
    val = np.where(top, val * 1.09, np.where(bot, val * 0.92, val))
    return _finish(_c(base) * val[..., None])


RUNE_BRANCHES = ["diag_ur", "diag_ul", "diag_dr", "diag_dl", "bar", "chev_r", "chev_l", "hook_r", "hook_l",
                 "cross", "fork_top", "foot"]


def paint_runes(gold, stone, rng):
    """Two rows of 3 abstract angular glyphs (no letters, no text): row 0 = local v 0..0.5,
    row 1 = local v 0.5..1. A band face maps to exactly one row. Gold strokes with a bevel,
    engraved into darkened dwarf stone, thin dark border lines framing each row."""
    x, y = _grid()
    bg = _c(stone) * 0.6 * stone_field(rng, 0.7)[..., None]
    mask = Image.new("L", (NS, NS), 0)
    dr = ImageDraw.Draw(mask)
    prng = random.Random(77)
    for row in (0, 1):
        v0, v1 = (0.0, 0.5) if row == 0 else (0.5, 1.0)
        for c in range(3):
            u0, u1 = c / 3.0, (c + 1) / 3.0
            cx = (u0 + u1) / 2
            top, bot = v0 + (v1 - v0) * 0.83, v0 + (v1 - v0) * 0.17
            mid = (top + bot) / 2
            h = top - bot
            hw = (u1 - u0) * 0.27
            strokes = [[(cx, bot), (cx, top)]]
            for b in prng.sample(RUNE_BRANCHES, prng.choice((1, 2, 2, 3))):
                if b == "diag_ur":
                    strokes.append([(cx, top), (cx + hw, top - 0.32 * h)])
                elif b == "diag_ul":
                    strokes.append([(cx, top), (cx - hw, top - 0.32 * h)])
                elif b == "diag_dr":
                    strokes.append([(cx, mid), (cx + hw, bot + 0.12 * h)])
                elif b == "diag_dl":
                    strokes.append([(cx, mid), (cx - hw, bot + 0.12 * h)])
                elif b == "bar":
                    strokes.append([(cx - hw, mid), (cx + hw, mid)])
                elif b == "chev_r":
                    strokes.append([(cx, top - 0.18 * h), (cx + hw, mid), (cx, bot + 0.18 * h)])
                elif b == "chev_l":
                    strokes.append([(cx, top - 0.18 * h), (cx - hw, mid), (cx, bot + 0.18 * h)])
                elif b == "hook_r":
                    strokes.append([(cx, top), (cx + hw, top), (cx + hw, mid)])
                elif b == "hook_l":
                    strokes.append([(cx, bot), (cx - hw, bot), (cx - hw, mid)])
                elif b == "cross":
                    strokes.append([(cx - hw, top - 0.2 * h), (cx + hw, bot + 0.2 * h)])
                elif b == "fork_top":
                    strokes.append([(cx - hw, top), (cx, mid + 0.1 * h), (cx + hw, top)])
                elif b == "foot":
                    strokes.append([(cx - hw * 0.8, bot), (cx + hw * 0.8, bot)])
            w = int(0.075 * h * NS)
            for s in strokes:
                pts = [_to_px(a, b) for a, b in s]
                dr.line(pts, fill=255, width=w, joint="curve")
                for p in (pts[0], pts[-1]):
                    dr.rectangle([p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2], fill=255)
    m = np.asarray(mask, np.float32) / 255.0
    sh = int(0.006 * NS)
    hi = np.roll(np.roll(m, -sh, 0), -sh, 1)               # up-left neighbour -> lit edge
    lo = np.roll(np.roll(m, sh, 0), sh, 1)
    groove = np.asarray(mask.filter(ImageFilter.GaussianBlur(sh * 2)), np.float32) / 255.0
    bgv = bg * (1.0 - 0.45 * np.clip(groove * 1.6 - m, 0, 1))[..., None]
    g = _c(gold) * (1.0 + 0.16 * (m - lo) - 0.12 * (m - hi))[..., None] * (1.0 + _pnoise(rng, 8, 0.03))[..., None]
    img = bgv * (1 - m[..., None]) + g * m[..., None]
    v = 1.0 - y
    for edge in (0.025, 0.475, 0.525, 0.975):
        img = np.where((np.abs(v - edge) < 0.008)[..., None], img * 0.55, img)
    return _finish(img)


def paint_charred(rng):
    """Scorched ground: charred (soft blotches) with branching ember cracks (glowing)."""
    b = _c(PAL["charred"])
    img = b * stone_field(rng, 1.1)[..., None]
    e = _c(PAL["ember"])
    mask = Image.new("L", (NS, NS), 0)
    dr = ImageDraw.Draw(mask)
    prng = random.Random(4711)
    for k in range(9):
        px, py = prng.uniform(0.05, 0.95) * NS, prng.uniform(0.05, 0.95) * NS
        a = prng.uniform(0, 2 * math.pi)
        pts = [(px, py)]
        for s in range(prng.randint(6, 11)):
            a += prng.uniform(-0.6, 0.6)
            px = min(max(px + math.cos(a) * 0.045 * NS, 0.02 * NS), 0.98 * NS)
            py = min(max(py + math.sin(a) * 0.045 * NS, 0.02 * NS), 0.98 * NS)
            pts.append((px, py))
            if prng.random() < 0.25:
                qx, qy, qa = px, py, a + prng.choice((-1, 1)) * prng.uniform(0.7, 1.3)
                sub = [(qx, qy)]
                for _ in range(3):
                    qx = min(max(qx + math.cos(qa) * 0.03 * NS, 0.02 * NS), 0.98 * NS)
                    qy = min(max(qy + math.sin(qa) * 0.03 * NS, 0.02 * NS), 0.98 * NS)
                    qa += prng.uniform(-0.5, 0.5)
                    sub.append((qx, qy))
                dr.line(sub, fill=190, width=9)
        dr.line(pts, fill=255, width=15, joint="curve")
    core = np.asarray(mask, np.float32) / 255.0
    glow = np.asarray(mask.filter(ImageFilter.GaussianBlur(18)), np.float32) / 255.0
    glow = np.clip(glow * 1.7, 0, 1)
    img = img * (1 - 0.5 * glow[..., None]) + e * (0.5 * glow[..., None])
    img = img * (1 - core[..., None]) + _lighten(e, 0.2) * core[..., None]
    return _finish(img)


def paint_cave(rng):
    """Vertical fade for cave / burrow interiors: limestone in shadow (x0.58) at the top
    (local v = 1, the mouth) to cave black at v <= 0.5 (deep inside, back plate)."""
    x, y = _grid()
    v = 1.0 - y
    s = np.clip((v - 0.5) / 0.5, 0.0, 1.0)
    w = s ** 1.5
    n = 1.0 + _pnoise(rng, 8, 0.05)
    lime = _c(PAL["limestone"]) * 0.58
    blk = _c(PAL["cave_black"])
    img = blk[None, None, :] * (1 - w)[..., None] + lime[None, None, :] * (w * n)[..., None]
    return _finish(img)


def paint_moss(rng):
    v = 1.0 + _pnoise(rng, 6, 0.07) + _blobs(rng, 40, 0.04, 0.12, -0.16, 0.14) + _pnoise(rng, 40, 0.06)
    return _finish(_c(PAL["moss"]) * v[..., None])


def paint_claw(rng):
    """Rock grey stone with 4 parallel, slightly curved claw gouges (dark core, lit rim)."""
    img = _c(PAL["rock_grey"]) * stone_field(rng)[..., None]
    mask = Image.new("L", (NS, NS), 0)
    dr = ImageDraw.Draw(mask)
    for k in range(4):
        a = (0.16 + 0.16 * k, 0.88)
        b = (0.40 + 0.16 * k, 0.10)
        n = 14
        pts = []
        for i in range(n + 1):
            t = i / n
            u = a[0] + (b[0] - a[0]) * t + 0.05 * math.sin(math.pi * t)
            vv = a[1] + (b[1] - a[1]) * t
            pts.append(_to_px(u, vv))
        for i in range(n):
            t = (i + 0.5) / n
            w = int((0.006 + 0.028 * math.sin(math.pi * min(1.0, t * 1.15)) ** 0.8) * NS)
            dr.line([pts[i], pts[i + 1]], fill=255, width=max(2, w))
    m = np.asarray(mask.filter(ImageFilter.GaussianBlur(3)), np.float32) / 255.0
    sh = int(0.008 * NS)
    rim = np.clip(np.roll(m, sh, 1) - m, 0, 1)
    img = img * (1 - 0.6 * m)[..., None] + _c((30, 28, 26)) * (0.25 * m)[..., None]
    img = img * (1 + 0.28 * rim)[..., None]
    return _finish(img)


def paint_planks(base, rng, n=4):
    """Vertical planks (dark seams between them) with simple wavy grain; periodic."""
    x, y = _grid()
    x = x % 1.0
    y = y % 1.0
    b = _c(base)
    f = (x * n + 0.5) % 1.0
    k = np.floor(x * n + 0.5).astype(np.int32) % n
    shade = np.array([rng.uniform(0.92, 1.07) for _ in range(n)], np.float32)[k]
    ph = np.array([rng.uniform(0, 1) for _ in range(n)], np.float32)[k]
    grain = np.sin(2 * np.pi * (5 * n * x + 0.22 * np.sin(2 * np.pi * (2 * y + ph)) + 0.06 * np.sin(2 * np.pi * (5 * y + 2 * ph))))
    v = shade * (1.0 - 0.07 * (grain > 0.55) - 0.035 * (grain < -0.75)) + _pnoise(rng, 8, 0.03)
    seam = np.minimum(f, 1.0 - f) / n
    v = np.where(seam < 0.0045, v * 0.52, np.where(seam < 0.009, v * 0.82, v))
    hi = (np.abs(seam - 0.0115) < 0.0022)
    v = np.where(hi, v * 1.08, v)
    return _finish(b * v[..., None])


def paint_timber(base, rng):
    x, y = _grid()
    b = _c(base)
    g = np.sin(2 * np.pi * (11 * x + 0.12 * np.sin(2 * np.pi * (2 * y + 0.3)) + 0.04 * np.sin(2 * np.pi * (7 * y))))
    g2 = np.sin(2 * np.pi * (27 * x + 0.3 * np.sin(2 * np.pi * (3 * y + 0.7))))
    v = 1.0 - 0.08 * (g > 0.6) - 0.03 * (g2 > 0.8) + _pnoise(rng, 6, 0.04) + _pnoise(rng, 40, 0.015)
    return _finish(b * v[..., None])


def tile_mean(img):
    a = np.asarray(img, np.float32)
    i0, i1 = int(TILE_PX * INSET), int(TILE_PX * (1 - INSET))
    return tuple(float(v) for v in a[i0:i1, i0:i1].reshape(-1, 3).mean(0))


def paint_atlas():
    rng = np.random.RandomState(4343)
    tiles = {}
    for k in TILES:
        rgb = PAL[TILE_PAL[k][0]]
        if k in ("stone_grey", "dwarf_stone", "dark_stone"):
            tiles[k] = paint_stone(rgb, rng, 1.25 if k == "dark_stone" else 1.0)
        elif k == "limestone":
            tiles[k] = paint_stone(rgb, rng, 0.75)
        elif k == "masonry":
            tiles[k] = paint_masonry(rgb, rng)
        elif k == "runes":
            tiles[k] = paint_runes(PAL["gold_trim"], PAL["dwarf_stone"], rng)
        elif k == "charred":
            tiles[k] = paint_charred(rng)
        elif k == "cave_dark":
            tiles[k] = paint_cave(rng)
        elif k == "moss":
            tiles[k] = paint_moss(rng)
        elif k == "claw":
            tiles[k] = paint_claw(rng)
        elif k == "wood":
            tiles[k] = paint_planks(rgb, rng)
        elif k == "wood_light":
            tiles[k] = paint_timber(rgb, rng)
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
    tile -> per-facet colour variation on stone), smooth shading, fn(normal, centre)
    returning a per-face tile override (moss on top faces, claw marks...), and an anchor
    (world point where the periodic pattern starts: masonry courses line up with it)."""

    def __init__(self, tile, S=4.0, grain=None, rnd=None, smooth=False, fn=None, anchor=None):
        self.tile = tile
        self.S = tuple(S) if isinstance(S, (tuple, list)) else (S, S)
        self.grain = Vector(grain) if grain is not None else None
        self.rnd = rnd
        self.smooth = smooth
        self.fn = fn
        self.anchor = Vector(anchor) if anchor is not None else Vector((0.0, 0.0, 0.0))

    def but(self, **kw):
        q = P(self.tile, self.S, self.grain, self.rnd, self.smooth, self.fn, self.anchor)
        for k, v in kw.items():
            if k == "S":
                v = tuple(v) if isinstance(v, (tuple, list)) else (v, v)
            if k in ("grain", "anchor") and v is not None:
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


def _frac(u0, extent):
    """Fractional start of a face in its tile, robust to float noise at period borders."""
    f = u0 - math.floor(u0 + 1e-6)
    return min(max(f, 0.0), max(0.0, 1.0 - extent))


def planar_uv(pts, n, S, grain=None, rnd=None, anchor=None):
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
    a = anchor if anchor is not None else Vector((0.0, 0.0, 0.0))
    us = [(p - a).dot(right) / su for p in pts]
    vs = [(p - a).dot(up) / sv for p in pts]
    u0, v0 = min(us), min(vs)
    eu, ev = max(us) - u0, max(vs) - v0
    f = 1.0 / max(1.0, eu / 0.999, ev / 0.999)
    eu, ev = eu * f, ev * f
    if rnd is not None:
        ou, ov = rnd.uniform(0.0, 1.0 - eu), rnd.uniform(0.0, 1.0 - ev)
    else:
        ou, ov = _frac(u0, eu), _frac(v0, ev)
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
        uvs = planar_uv(pts, n, p.S, p.grain, p.rnd, p.anchor)
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
    # A "<x>_tunnel" piece is the inward-facing inner skin of the solid "<x>" (cave / burrow):
    # on its own its volume is negative by design, so it is checked together with its outer skin.
    neg, shells = [], {}
    for pc in pcs:
        if pc["label"].endswith("_tunnel"):
            continue
        if pc["vol"] < -1e-6:
            neg.append("%s/%s" % (pc["part"], pc["label"]))
    for pc in pcs:
        if pc["label"].endswith("_tunnel"):
            outer = [q for q in pcs if q["label"] == pc["label"][:-len("_tunnel")]]
            tot = pc["vol"] + sum(q["vol"] for q in outer)
            shells[pc["label"][:-len("_tunnel")]] = round(tot, 2)
            if not outer or tot < -1e-6:
                neg.append("%s/%s (with its outer skin)" % (pc["part"], pc["label"]))
    return dict(pieces=n, floating=floating, inverted_volume_pieces=neg, closed_shell_volumes=shells)


def _clip2(poly, a, b):
    out = []
    for i in range(len(poly)):
        p, q = poly[i], poly[(i + 1) % len(poly)]
        sp = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        sq = (b.x - a.x) * (q.y - a.y) - (b.y - a.y) * (q.x - a.x)
        if sp >= 0:
            out.append(p)
        if (sp >= 0) != (sq >= 0):
            out.append(p + (q - p) * (sp / (sp - sq)))
    return out


def coplanar_overlaps(parts, min_area=1e-3, zmin=0.01):
    """Z-fighting check: pairs of triangles (any parts) that face the same way, lie in the
    same plane (within 0.002 studs) and overlap by more than min_area stud^2. Faces
    entirely below zmin (under the terrain) are ignored. Opposite-facing coincident faces
    (hidden interior contacts) are not z-fighting with back-face culling and are ignored."""
    items = []
    for key, g in parts.items():
        for a, b, c in g.tris:
            A = [Vector(g.co[a]), Vector(g.co[b]), Vector(g.co[c])]
            n = (A[1] - A[0]).cross(A[2] - A[0])
            if n.length < 1e-9 or max(p.z for p in A) < zmin:
                continue
            n.normalize()
            items.append((key, A, n, n.dot(A[0])))
    groups = {}
    for it in items:
        groups.setdefault(tuple(round(x, 2) for x in it[2]), []).append(it)
    bad = []
    for lst in groups.values():
        lst.sort(key=lambda t: t[3])
        for i in range(len(lst)):
            ki, A, na, da = lst[i]
            u = any_perp(na)
            w = na.cross(u)
            pa = [Vector((p.dot(u), p.dot(w))) for p in A]
            if (pa[1] - pa[0]).cross(pa[2] - pa[0]) < 0:
                pa.reverse()
            for j in range(i + 1, len(lst)):
                kj, Bt, nb, db = lst[j]
                if db - da > 2e-3:
                    break
                if na.dot(nb) < 0.9995:
                    continue
                pb = [Vector((p.dot(u), p.dot(w))) for p in Bt]
                if max(p.x for p in pa) < min(p.x for p in pb) or max(p.x for p in pb) < min(p.x for p in pa) or \
                        max(p.y for p in pa) < min(p.y for p in pb) or max(p.y for p in pb) < min(p.y for p in pa):
                    continue
                if (pb[1] - pb[0]).cross(pb[2] - pb[0]) < 0:
                    pb.reverse()
                poly = pa
                for k in range(3):
                    poly = _clip2(poly, pb[k], pb[(k + 1) % 3])
                    if not poly:
                        break
                if poly:
                    ar = 0.5 * abs(sum(poly[m].x * poly[(m + 1) % len(poly)].y - poly[(m + 1) % len(poly)].x * poly[m].y
                                       for m in range(len(poly))))
                    if ar > min_area:
                        bad.append(["%s/%s" % (ki, kj), round(ar, 4), [round(x, 2) for x in sum(A, Vector()) / 3]])
    return bad


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



def _jit(rnd, xy, z):
    return lambda q: Vector((rnd.uniform(-xy, xy), rnd.uniform(-xy, xy), rnd.uniform(-z, z)))

def face_dir(g, idx, p, want):
    """Add a face, flipping its winding if needed so that it faces `want`."""
    pts = [Vector(g.co[i]) for i in idx]
    if poly_normal(pts).dot(Vector(want)) < 0:
        idx = list(reversed(idx))
    face(g, idx, p)



# ======================================================================================
# 4b. BATCH D GEOMETRY HELPERS
# ======================================================================================
GB_FACES = {"top": (2, 1, 0, 1), "bottom": (2, -1, 1, 0), "right": (0, 1, 1, 2), "left": (0, -1, 2, 1),
            "back": (1, 1, 2, 0), "front": (1, -1, 0, 2)}     # (axis, sign, in-plane a, in-plane b), a x b = normal


def _cuts(a0, a1, S, off):
    out = [a0]
    k = math.floor((a0 - off) / S + 1e-6) + 1
    while k * S + off < a1 - 1e-4:
        c = k * S + off
        if c > a0 + 1e-4:
            out.append(c)
        k += 1
    out.append(a1)
    return out


def gbox(g, lo, hi, p, skip=(), fp=None, label="gbox"):
    """Axis-aligned box whose faces are cut on the world grid of the paint (multiples of
    S from its anchor). Every cell then fits one tile period, so a periodic tile (masonry)
    continues seamlessly across cells, faces and neighbouring boxes. Vertices are shared."""
    g.mark(label)
    S = p.S[0]
    cuts = [_cuts(lo[a], hi[a], S, p.anchor[a]) for a in range(3)]
    cache = {}

    def V(c):
        key = tuple(round(t, 5) for t in c)
        if key not in cache:
            cache[key] = g.v(c)
        return cache[key]
    for name, (ax, sgn, aa, bb) in GB_FACES.items():
        if name in skip:
            continue
        pp = (fp or {}).get(name, p)
        fixed = hi[ax] if sgn > 0 else lo[ax]
        A, B = cuts[aa], cuts[bb]
        for i in range(len(A) - 1):
            for j in range(len(B) - 1):
                q = []
                for x_, y_ in ((A[i], B[j]), (A[i + 1], B[j]), (A[i + 1], B[j + 1]), (A[i], B[j + 1])):
                    c = [0.0, 0.0, 0.0]
                    c[ax], c[aa], c[bb] = fixed, x_, y_
                    q.append(V(c))
                face(g, q, pp)


def rune_slab(g, o, U, N, L, H, depth, ps, row=0, label="rune_band"):
    """Thin closed slab whose outer face (normal N, horizontal) carries one row of rune
    glyphs. o = lower-left corner of the outer face, U = unit along the band (U x Z = N).
    The band is cut into equal segments that each show 3 glyphs at uniform UV scale;
    top/bottom/back faces follow the same cuts (no T-junctions)."""
    g.mark(label)
    o, U, N = Vector(o), Vector(U).normalized(), Vector(N).normalized()
    k = 0.5 / H
    n = max(1, int(math.ceil(L * k - 1e-6)))
    seg = L / n
    du = min(1.0, k * seg)
    u0 = 0.5 - du / 2
    v0 = 0.0 if row == 0 else 0.5
    FB = [g.v(o + U * (seg * i)) for i in range(n + 1)]
    FT = [g.v(o + U * (seg * i) + Z * H) for i in range(n + 1)]
    BB = [g.v(o + U * (seg * i) - N * depth) for i in range(n + 1)]
    BT = [g.v(o + U * (seg * i) - N * depth + Z * H) for i in range(n + 1)]
    pr = P("runes")
    for i in range(n):
        face(g, [FB[i], FB[i + 1], FT[i + 1], FT[i]], pr,
             uvs=[(u0, v0), (u0 + du, v0), (u0 + du, v0 + 0.5), (u0, v0 + 0.5)])
        face_dir(g, [FT[i], FT[i + 1], BT[i + 1], BT[i]], ps, Z)
        face_dir(g, [FB[i], FB[i + 1], BB[i + 1], BB[i]], ps, -Z)
        face_dir(g, [BB[i], BB[i + 1], BT[i + 1], BT[i]], ps, -N)          # embedded back (keeps the slab closed)
    face_dir(g, [FB[0], FT[0], BT[0], BB[0]], ps, -U)
    face_dir(g, [FB[n], FT[n], BT[n], BB[n]], ps, U)


def merge(dst, src, M=None):
    """Append geometry (with its pieces) transformed by M; mirrored transforms flip the winding."""
    o = len(dst.co)
    t0 = len(dst.tris)
    M = M if M is not None else Matrix.Identity(4)
    flip = M.to_3x3().determinant() < 0
    for p in src.co:
        dst.co.append(tuple(M @ Vector(p)))
    for (a, b, c), uv, tl, sm in zip(src.tris, src.uv, src.tile, src.smooth):
        if flip:
            dst.add_tri(c + o, b + o, a + o, uv[2], uv[1], uv[0], tl, sm)
        else:
            dst.add_tri(a + o, b + o, c + o, uv[0], uv[1], uv[2], tl, sm)
    for s, lab in src.pieces:
        dst.pieces.append((s + t0, lab))


def _hull_into(g, pts, p, label):
    g.mark(label)
    bm = bmesh.new()
    for q in pts:
        bm.verts.new(q)
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
    mn = Vector([min(v.co[k] for v in bm.verts) for k in range(3)])
    mx = Vector([max(v.co[k] for v in bm.verts) for k in range(3)])
    for f in bm.faces:
        face(g, [ids[v.index] for v in f.verts], p)
    bm.free()
    return mn, mx


def crag(g, c, half, npts, rnd, p, zmin=-1.0, boxy=0.6, jit=0.12, label="crag"):
    """Faceted cliff block: convex hull of jittered points on a sphere-to-box blend
    (boxy = 0 round, 1 cube) scaled by `half`; flat bottom at zmin."""
    pts = []
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(npts):
        zz = 1.0 - 2.0 * (i + 0.5) / npts
        rr = math.sqrt(max(0.0, 1.0 - zz * zz))
        th = golden * i + rnd.uniform(-0.3, 0.3)
        d = Vector((rr * math.cos(th), rr * math.sin(th), zz))
        m = max(abs(d.x), abs(d.y), abs(d.z))
        q = d * (1.0 - boxy) + (d / m) * boxy
        k = rnd.uniform(1.0 - jit, 1.0)
        pt = Vector((c[0] + half[0] * q.x * k, c[1] + half[1] * q.y * k, c[2] + half[2] * q.z * k))
        pt.z = max(pt.z, zmin)
        pts.append(pt)
    if zmin > -1e8 and c[2] - half[2] < zmin + 0.01:
        for dx, dy in ((-0.75, -0.75), (0.75, -0.75), (0.75, 0.75), (-0.75, 0.75)):
            pts.append(Vector((c[0] + half[0] * dx, c[1] + half[1] * dy, zmin)))
    return _hull_into(g, pts, p, label)


class Noise3:
    """Smooth deterministic 3D noise in about [-1, 1]: sum of random plane waves."""

    def __init__(self, rnd, n=7, scale=10.0):
        self.w = []
        for _ in range(n):
            d = Vector((rnd.uniform(-1, 1), rnd.uniform(-1, 1), rnd.uniform(-1, 1)))
            d = d.normalized() if d.length > 1e-6 else Vector((1.0, 0.0, 0.0))
            f = rnd.uniform(0.6, 1.5) / scale
            self.w.append((d * (2.0 * math.pi * f), rnd.uniform(0, 2 * math.pi), rnd.uniform(0.5, 1.0)))
        self.tot = sum(a for _, _, a in self.w) * 0.62

    def __call__(self, p):
        p = Vector(p)
        return max(-1.0, min(1.0, sum(a * math.sin(d.dot(p) + ph) for d, ph, a in self.w) / self.tot))


def boulder(g, size, rnd, p, c=(0.0, 0.0), zsink=-0.6, flat_top=0.25, lump=0.15, subdiv=2, moss=None,
            label="boulder"):
    """Rounded, water-worn boulder: icosphere with smooth low-frequency lumps, flattened
    top, bottom cut flat at zsink; rescaled so its largest horizontal size = size[0].
    moss = (min normal z, min height as a fraction of the top): mossy top faces."""
    g.mark(label)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdiv, radius=1.0)
    nz = Noise3(rnd, 7, 1.3)
    rx, ry, rz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    cz = rz * 0.62
    pts = []
    for v in bm.verts:
        d = v.co.normalized()
        s = 1.0 + lump * nz(d)
        z = d.z * rz * s
        if z > 0:
            z *= 1.0 - flat_top * (d.z ** 2)
        pts.append(Vector((d.x * rx * s, d.y * ry * s, z + cz)))
    span = max(max(q.x for q in pts) - min(q.x for q in pts), max(q.y for q in pts) - min(q.y for q in pts))
    f = size[0] / span
    out = []
    for q in pts:
        q = q * f
        q.z = max(q.z, zsink)
        out.append(Vector((q.x + c[0], q.y + c[1], q.z)))
    ids = [g.v(q) for q in out]
    if moss:
        ztop = max(q.z for q in out)
        nzm = Noise3(rnd, 5, size[0] / 3.0)

        def mfn(n, cc):
            return "moss" if n.z > moss[0] and cc.z > moss[1] * ztop and nzm(cc) > 0.15 else None
        p = p.but(fn=mfn)
    for fc in bm.faces:
        face(g, [ids[v.index] for v in fc.verts], p)
    bm.free()
    mn = Vector([min(q[k] for q in out) for k in range(3)])
    mx = Vector([max(q[k] for q in out) for k in range(3)])
    return mn, mx


def superarc(th, e):
    c, s = math.cos(th), math.sin(th)
    return math.copysign(abs(c) ** e, c), abs(s) ** e


def fade_uvs(pts, n, k, y0):
    """UVs for cave interiors on the cave_dark tile: v = 1 - k * depth -> dark with depth
    (depth = distance behind y0, the fade origin in front of the brow); u around (the fade
    is constant in u). The face is projected on its own plane with ONE scale k on both
    axes (no stretching); its v starts at the depth of its frontmost vertex."""
    yv = Vector((0.0, 1.0, 0.0))
    up = yv - n * n.y
    up = up.normalized() if up.length > 0.2 else (Z - n * n.z).normalized()
    right = up.cross(n)
    p0 = min(pts, key=lambda q: q.y)
    us = [(p - p0).dot(right) * k for p in pts]
    vs = [1.0 - k * (p0.y - y0) - (p - p0).dot(up) * k for p in pts]
    du = max(us) - min(us)
    u0 = _frac(min(us), du)
    return [(u - min(us) + u0, min(1.0, max(0.0, v))) for u, v in zip(us, vs)]


def fit_uvs(pts, n, rect):
    """Planar UVs fitted uniformly into a sub-rectangle (u0, v0, u1, v1) of the tile."""
    up = Z - n * n.z
    up = up.normalized() if up.length > 0.35 else (Vector((0.0, 1.0, 0.0)) - n * n.y).normalized()
    right = up.cross(n)
    us = [p.dot(right) for p in pts]
    vs = [p.dot(up) for p in pts]
    w, h = max(us) - min(us), max(vs) - min(vs)
    s = min((rect[2] - rect[0]) / max(w, 1e-6), (rect[3] - rect[1]) / max(h, 1e-6))
    return [(rect[0] + (u - min(us)) * s, rect[1] + (v - min(vs)) * s) for u, v in zip(us, vs)]


def mound(OG, IG, prm, rnd, p_out, label="mound"):
    """Rock mass with an arched mouth and a blind tunnel (limestone cave, wolf den).

    Every cross-section is an arch from the right foot (x+, ground) over the top to the
    left foot, plus two foot points sunk to zs. Outer surface: mouth rim -> front-face
    rings (bulging) -> body rings -> back cap. Tunnel (in IG): rim -> shrinking rings ->
    back plate (cave black). A floor strip at z = 0.08 and a bottom cap close everything,
    so the whole thing is one closed, consistently wound shell."""
    n, zs = prm["n"], prm["zs"]
    ths = [math.pi * i / n for i in range(n + 1)]
    am, bm_, em = prm["a_m"], prm["b_m"], prm["e_m"]
    ym, brow = prm["y_m"], prm["brow"]
    amax, bmax, eo = prm["a_max"], prm["b_max"], prm["e_o"]
    nz = Noise3(rnd, 9, prm["noise_scale"])
    nz2 = Noise3(rnd, 7, prm["noise_scale"] * 0.55)
    jit = prm.get("jitter", 0.0)

    def displace(q, w, front=0.0):
        r = Vector((q.x, 0.0, max(q.z, 0.0)))
        d = r.normalized() if r.length > 1e-6 else Vector((0.0, 0.0, 1.0))
        amt = prm["amp"] * (0.55 + 0.45 * nz(q))
        for lc, lr, lh in prm.get("lobes", []):
            amt += lh * math.exp(-((q - Vector(lc)).length / lr) ** 2)
        q2 = q + d * (amt * w)
        q2.y -= front * w * prm.get("amp_front", 0.0) * (0.5 + 0.5 * nz2(q))
        if jit:
            q2 += Vector((rnd.uniform(-jit, jit), rnd.uniform(-jit, jit), rnd.uniform(-jit, jit) if q.z > 0.01 else 0.0)) * w
        return q2

    def arch(a, b, e, yfn, w=0.0, front=0.0, rim_noise=0.0):
        out = []
        for th in ths:
            c, s = superarc(th, e)
            q = Vector((a * c, yfn(s), b * s))
            if w > 0:
                q = displace(q, w, front)
            elif rim_noise:
                q += Vector((q.x, 0.0, q.z)).normalized() * (rim_noise * nz2(q)) if q.z > 0.01 else Vector()
            out.append(q)
        return out

    rim = arch(am, bm_, em, lambda s: ym - brow * s * s, rim_noise=prm.get("rim_noise", 0.0))
    y0, sa0, sb0, sl0 = prm["body"][0]
    body0 = arch(amax * sa0, bmax * sb0, eo, lambda s: y0 + sl0 * s, w=1.0, front=1.0)
    outer = [rim]
    for f, bulge in prm["front"]:
        ring = []
        for th, a, b in zip(ths, rim, body0):
            s = superarc(th, eo)[1]
            q = a.lerp(b, f)
            q.y -= bulge * math.sin(math.pi * f) * (0.4 + 0.6 * s)
            ring.append(displace(q, f * 0.8, front=1.0))
        outer.append(ring)
    outer.append(body0)
    for ring in prm["body"][1:]:
        y, sa, sb, sl = ring[:4]
        dx = ring[4] if len(ring) > 4 else 0.0
        outer.append([q + Vector((dx, 0.0, 0.0)) for q in
                      arch(amax * sa, bmax * sb, eo, lambda s, y=y, sl=sl: y + sl * s, w=1.0)])
    yb_ = prm["body"][-1][0]                                          # planar back cap
    outer[-1] = [Vector((q.x, yb_, q.z)) for q in outer[-1]]
    tun = [rim]
    for dpt, sa, sb in prm["tunnel"]:
        tj = prm.get("tjit", 0.0)
        ring = arch(am * sa, bm_ * sb, em, lambda s, dpt=dpt: ym + dpt)
        if tj:
            ring = [q + Vector((rnd.uniform(-tj, tj), rnd.uniform(-tj, tj) * 0.5, rnd.uniform(-tj, tj) if q.z > 0.01 else 0.0))
                    for q in ring]
        tun.append(ring)
    tun[-1] = [Vector((q.x, ym + prm["tunnel"][-1][0], q.z)) for q in tun[-1]]   # planar back plate

    def full(g, ring):
        ids = [g.v(q) for q in ring]
        fr = g.v((ring[0].x, ring[0].y, zs))
        fl = g.v((ring[-1].x, ring[-1].y, zs))
        return [fr] + ids + [fl]

    OG.mark(label)
    O = [full(OG, r) for r in outer]
    for A, B in zip(O[:-1], O[1:]):
        for i in range(len(A) - 1):
            face(OG, [A[i], B[i], B[i + 1], A[i + 1]], p_out)
    face_dir(OG, O[-1], p_out, (0, 1, 0))
    # tunnel (inside part): its own copy of the rim vertices so the parts stay separate meshes
    IG.mark(label + "_tunnel")
    T = [full(IG, r) for r in tun]
    k = prm["k_fade"]
    yf0 = ym - brow - 1.0                       # fade origin 1 stud in front of the brow: depths > 0
    pin = P("cave_dark")
    for A, B in zip(T[:-1], T[1:]):
        for i in range(len(A) - 1):
            idx = [A[i], A[i + 1], B[i + 1], B[i]]
            pts = [Vector(IG.co[j]) for j in idx]
            nrm = poly_normal(pts)
            face(IG, idx, pin, uvs=fade_uvs(pts, nrm, k, yf0))
    plate = list(reversed(T[-1]))
    pts = [Vector(IG.co[j]) for j in plate]
    nrm = poly_normal(pts)
    if nrm.y > 0:
        plate = list(reversed(plate))
        pts.reverse()
        nrm = -nrm
    face(IG, plate, pin, uvs=fit_uvs(pts, nrm, (0.02, 0.02, 0.98, 0.42)))
    # floor strip
    IG.mark(label + "_floor")
    zf = 0.08
    F = []
    for i, ring in enumerate(tun):
        hw = ring[0].x - 0.06 if i == 0 else ring[0].x + 0.35
        hw2 = -ring[-1].x - 0.06 if i == 0 else -ring[-1].x + 0.35
        y = ym if i == 0 else ring[0].y
        F.append((IG.v((hw, y, zf)), IG.v((-hw2, y, zf))))
    for (a, b), (c, d) in zip(F[:-1], F[1:]):
        idx = [a, c, d, b]
        pts = [Vector(IG.co[j]) for j in idx]
        nrm = poly_normal(pts)
        if nrm.z < 0:
            idx.reverse()
            pts.reverse()
            nrm = -nrm
        face(IG, idx, pin, uvs=fade_uvs(pts, nrm, k, yf0))
    # bottom cap (outer part): footprint loop = outer right feet -> back -> outer left feet ->
    # tunnel left feet -> tunnel right feet (the tunnel is a notch in the footprint)
    loop = [r[0] for r in O] + [r[-1] for r in reversed(O)]
    tl = [OG.v((r[-1].x, r[-1].y, zs)) for r in tun[1:]]
    tr = [OG.v((r[0].x, r[0].y, zs)) for r in tun[1:]]
    loop += tl + list(reversed(tr))
    face_dir(OG, loop, p_out.but(fn=None), (0, 0, -1))
    # the tunnel's own bottom edge: close the tunnel shell's feet with thin strips on the cap plane
    return dict(rim=rim, tunnel=tun, outer=outer)


def moss_patches(rnd, nz_min=0.8, thresh=0.3, scale=6.0, zmin=-1e9, other=None):
    """Paint override: moss on up-facing faces where a smooth noise field is high, so moss
    grows in contiguous patches (no checkerboard of single faces)."""
    nzf = Noise3(rnd, 6, scale)

    def fn(n, c):
        if n.z > nz_min and c.z > zmin and nzf(c) > thresh:
            return "moss"
        return other(n, c) if other else None
    return fn


def cone_down(g, x, y, ztop, r, L, sides, p, rnd, label="stalactite"):
    """Stalactite: lathe from the tip (bottom) up to a base ring embedded 0.6 above ztop."""
    prof = [(0.0, ztop - L), (r * 0.42, ztop - L * 0.55), (r * 0.85, ztop - L * 0.12), (r, ztop + 0.6)]
    lathe(g, prof, sides, p, c=(x, y, 0.0), phase=rnd.uniform(0, 1), cap1=True, label=label)


def ray_z(geos, x, y, z0, up=True):
    co, tris = [], []
    for g in geos:
        o = len(co)
        co.extend(Vector(p) for p in g.co)
        tris.extend((a + o, b + o, c + o) for a, b, c in g.tris)
    bvh = _bvh(co, tris)
    loc, nrm, idx, dist = bvh.ray_cast(Vector((x, y, z0)), Vector((0.0, 0.0, 1.0 if up else -1.0)))
    return None if loc is None else loc.z


def polys_disjoint(polys):
    """True when no two outline polygons cross or touch (2D, edge-edge test)."""
    for i in range(len(polys)):
        for j in range(i + 1, len(polys)):
            A, B = polys[i], polys[j]
            for a in range(len(A)):
                for b in range(len(B)):
                    if mgeo.intersect_line_line_2d(Vector(A[a]), Vector(A[(a + 1) % len(A)]),
                                                   Vector(B[b]), Vector(B[(b + 1) % len(B)])):
                        return False
    return True


def carve_face(g, outer2d, holes2d, to3d, N, depth, p_face, p_wall, p_floor, label="carved"):
    """Planar face (outer loop in 2D) with carved emblem polygons: constrained Delaunay
    triangulation keeps the emblem outlines as edges; triangles inside an emblem become
    the floor (pushed `depth` into the surface along -N), walls join the outlines."""
    g.mark(label)
    verts, faces = list(outer2d), [list(range(len(outer2d)))]
    for poly in holes2d:
        poly = ccw2(poly)
        faces.append(list(range(len(verts), len(verts) + len(poly))))
        verts += poly
    cdt = mgeo.delaunay_2d_cdt([Vector(q) for q in verts], [], faces, 1, 1e-6)
    ov, ofaces, orig_v, oorig = cdt[0], cdt[2], cdt[3], cdt[5]
    if len(ov) != len(verts):
        raise RuntimeError("carve_face: CDT added %d vertices" % (len(ov) - len(verts)))
    N = Vector(N).normalized()
    S_ = [g.v(to3d(q.x, q.y)) for q in ov]
    D_ = [g.v(to3d(q.x, q.y) - N * depth) for q in ov]
    n_floor = 0
    for f, orig in zip(ofaces, oorig):
        a, b, c = f
        pa, pb, pc = ov[a], ov[b], ov[c]
        if (pb.x - pa.x) * (pc.y - pa.y) - (pb.y - pa.y) * (pc.x - pa.x) < 0:
            b, c = c, b
        if any(k >= 1 for k in orig):
            face_dir(g, [D_[a], D_[b], D_[c]], p_floor, N)
            n_floor += 1
        else:
            face_dir(g, [S_[a], S_[b], S_[c]], p_face, N)
    o2n = {}
    for i, lst in enumerate(orig_v):
        for kk in lst:
            o2n[kk] = i
    for fi in faces[1:]:
        m = len(fi)
        for e in range(m):
            ia, ib = o2n[fi[e]], o2n[fi[(e + 1) % m]]
            qa, qb = ov[ia], ov[ib]
            inward = to3d(qa.x - (qb.y - qa.y), qa.y + (qb.x - qa.x)) - to3d(qa.x, qa.y)
            face_dir(g, [S_[ia], S_[ib], D_[ib], D_[ia]], p_wall, inward)
    return n_floor


def talon(bx, bz, ang, L, w0, bend, n=6):
    """Curved, tapering claw outline (2D) from its base centre along a bending centreline."""
    x, z, a = bx, bz, ang
    ds = L / n
    cl = []
    for i in range(n + 1):
        cl.append((x, z, a))
        x += math.cos(a) * ds
        z += math.sin(a) * ds
        a += bend / n
    left, right = [], []
    for i, (x, z, a) in enumerate(cl):
        w = w0 * (1.0 - i / n) ** 0.8
        nx, nz_ = -math.sin(a), math.cos(a)
        left.append((x + nx * w, z + nz_ * w))
        right.append((x - nx * w, z - nz_ * w))
    return ccw2(right[:-1] + [right[-1]] + list(reversed(left[:-1])))


def ellipse2(cx, cz, rx, rz, n=8, phase=0.0):
    return [(cx + rx * math.cos(phase + 2 * math.pi * i / n), cz + rz * math.sin(phase + 2 * math.pi * i / n))
            for i in range(n)]


def box_collider(mn, mx, shrink=1.0, yaw=0.0, hfrac=0.9):
    """Box for a rounded rock from its bounding box: footprint shrunk by `shrink`, top at
    `hfrac` of the height (the crest is rounded), bottom at the rock's lowest point."""
    h = (mx[2] - mn[2]) * hfrac
    c = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, mn[2] + h / 2]
    s = [(mx[0] - mn[0]) * shrink, (mx[1] - mn[1]) * shrink, h]
    return [c, s, yaw]


def piece_bbox(g, label_prefix):
    """Bounding box of every piece of g whose label starts with label_prefix."""
    out = []
    for s, e, lab in g.piece_ranges():
        if lab.startswith(label_prefix):
            vids = {i for t in g.tris[s:e] for i in t}
            pts = [g.co[i] for i in vids]
            out.append(([min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)]))
    return out


# ======================================================================================
# 5. MODELS
#    Blender +Z up, front = -Y, origin = centre of the base at ground level (z = 0).
# ======================================================================================

# --------------------------------------------------------------------------------------
# dwarf_gate: the gate of the Armed Nation of Dwargon, carved into the Canaat range
# --------------------------------------------------------------------------------------
GATE = dict(
    y_o0=-2.0, y_o1=-0.8, y_door=0.6,                          # order-0 face, order-1 face, door plane
    o0=[(3.0, 19.0, 11.0), (19.0, 21.0, 9.0), (21.0, 23.0, 7.0), (23.0, 25.0, 5.0)],     # (z0, z1, half width)
    o1=[(3.0, 18.0, 9.5), (18.0, 20.0, 7.5), (20.0, 22.0, 5.5), (22.0, 24.0, 3.5)],
    door=dict(x=9.5, z0=3.25, z1=18.0, y_front=-0.2, y_back=0.6),
    statues_x=27.0, statues_y=-6.0, plinth_top=2.5,
)
MASONRY = dict(S=8.0, anchor=(0.0, 0.0, 1.0))                  # courses every 2 studs at odd z


def stepped_frame(g, bands, top, x_out, z_out, y0, y1, p, label):
    """Frame of one recessed order around a stepped (squared, corbelled) opening."""
    for z0, z1, hw in bands:
        sk = ("bottom", "back") if z0 <= bands[0][0] + 1e-6 else ("back",)     # upper steps keep their soffit
        gbox(g, (-x_out, y0, z0), (-hw, y1, z1), p, skip=sk, label=label)
        gbox(g, (hw, y0, z0), (x_out, y1, z1), p, skip=sk, label=label)
    gbox(g, (-x_out, y0, top), (x_out, y1, z_out), p, skip=("back",), label=label)


def outline_trim(g, bands, top, y, pg, w=0.35, t=0.18, label="gold_outline"):
    """Gold trim following the stepped opening outline on the frame's front face. Where the
    horizontal strips overlap the vertical ones they are 0.02-0.03 larger on every side, so
    no two faces are ever coplanar (no z-fighting)."""
    yb = y + 0.05
    th = t + 0.03
    e = 0.02
    for i, (z0, z1, hw) in enumerate(bands):
        zt = z1 + w if i + 1 < len(bands) else top + w
        for s in (-1, 1):
            a, b = sorted((s * (hw - 0.01), s * (hw + w)))
            block(g, (a, y - t, z0), (b, yb, zt), pg, skip=("back", "bottom"), label=label)
        if i + 1 < len(bands):
            hn = bands[i + 1][2]
            for s in (-1, 1):
                a, b = sorted((s * (hn - 0.03), s * (hw + w + e)))
                block(g, (a, y - th, z1 - e), (b, yb, z1 + w + e), pg, skip=("back",), label=label)
    hw = bands[-1][2]
    block(g, (-hw - w - e, y - th, top - e), (hw + w + e, yb, top + w + e), pg, skip=("back",), label=label)


def prism(g, poly, z0, z1, p, cap0=True, cap1=True, label="prism"):
    """Vertical prism from a 2D outline (x, y), counter-clockwise seen from above."""
    g.mark(label)
    poly = ccw2(poly)
    B = [g.v((x, y, z0)) for x, y in poly]
    T = [g.v((x, y, z1)) for x, y in poly]
    m = len(poly)
    for k in range(m):
        face(g, [B[k], B[(k + 1) % m], T[(k + 1) % m], T[k]], p)
    if cap0:
        face(g, list(reversed(B)), p)
    if cap1:
        face(g, T, p)


def statue_geo(rnd):
    """Dwarven guardian, local frame: plinth top at z = 0, faces -Y, 18 studs tall
    (horn tips), hands resting on the pommel of a great iron double axe planted head-down."""
    g = Geo()
    ps = P("dwarf_stone", 3.0, rnd=rnd)
    piron = P("dark_stone", 3.0)
    pg = P("gold", 1.5)
    chamfer_hexa(g, box_corners((-3.6, -3.6, -3.3), (3.6, 3.6, -0.95)), 0.22, ps, skip=(), label="plinth")
    chamfer_hexa(g, box_corners((-3.2, -3.2, -1.0), (3.2, 3.2, 0.0)), 0.18, ps, skip=(), label="plinth_top")
    rune_slab(g, (-3.0, -3.78, -2.75), (1, 0, 0), (0, -1, 0), 6.0, 1.3, 0.3, ps, row=1, label="plinth_runes")
    for s in (-1, 1):
        a, b = sorted((s * 0.25, s * 2.5))
        chamfer_hexa(g, box_corners((a, -2.3, -0.05), (b, 1.0, 1.35)), 0.25, ps, skip=(), label="boot")
        a, b = sorted((s * 0.45, s * 2.2))
        a2, b2 = sorted((s * 0.3, s * 2.4))
        hexa(g, [Vector(q) for q in ((a, -1.0, 1.2), (b, -1.0, 1.2), (b, 0.9, 1.2), (a, 0.9, 1.2),
                                     (a2, -1.2, 4.0), (b2, -1.2, 4.0), (b2, 1.0, 4.0), (a2, 1.0, 4.0))], ps, label="leg")
    lathe(g, [(3.25, 3.3), (3.05, 5.0), (2.8, 7.3)], 8, ps, c=(0.0, -0.05, 0.0), phase=math.pi / 8, sy=0.7,
          cap1=False, label="kilt")
    tc = [Vector(q) for q in ((-2.8, -1.8, 7.0), (2.8, -1.8, 7.0), (2.8, 1.6, 7.0), (-2.8, 1.6, 7.0),
                              (-3.4, -2.0, 11.8), (3.4, -2.0, 11.8), (3.4, 1.8, 11.8), (-3.4, 1.8, 11.8))]
    chamfer_hexa(g, tc, 0.35, ps, skip=(), label="torso")
    block(g, (-2.98, -1.97, 6.85), (2.98, 1.77, 7.85), ps, label="belt")
    block(g, (-0.7, -2.22, 6.92), (0.7, -1.9, 7.78), pg, label="buckle")
    for s in (-1, 1):
        a, b = sorted((s * 2.45, s * 4.65))
        a2, b2 = sorted((s * 2.35, s * 4.1))
        chamfer_hexa(g, [Vector(q) for q in ((a, -2.15, 10.5), (b, -2.15, 10.5), (b, 1.9, 10.5), (a, 1.9, 10.5),
                                              (a2, -2.0, 12.6), (b2, -2.0, 12.6), (b2, 1.75, 12.6), (a2, 1.75, 12.6))],
                     0.3, ps, skip=(), label="pauldron")
        sweep(g, [Vector((s * 3.85, -0.1, 11.2)), Vector((s * 3.75, -1.2, 8.3))], [(1.5, 1.5)] * 2, ps, label="upper_arm")
        sweep(g, [Vector((s * 3.95, -1.0, 8.5)), Vector((s * 1.0, -3.3, 9.3))], [(1.25, 1.3)] * 2, ps, label="forearm")
        a, b = sorted((s * -0.05, s * 1.3))
        d_ = 0.0 if s < 0 else 0.06                     # the two hands overlap at x = 0: keep their faces apart
        chamfer_hexa(g, box_corners((a, -4.15 - d_, 8.55 - d_), (b, -2.45 - d_, 9.95 + d_)), 0.2, ps, skip=(),
                     label="hand")
    # head, face (dark eye band under a heavy brow), big beard, horned helmet
    chamfer_hexa(g, box_corners((-1.5, -1.6, 12.0), (1.5, 1.3, 14.9)), 0.35, ps, skip=(), label="head")
    block(g, (-1.18, -1.7, 13.42), (1.18, -1.5, 13.92), piron, label="eyes")
    block(g, (-1.56, -2.05, 13.9), (1.56, -1.4, 14.45), ps, label="brow")
    hexa(g, [Vector(q) for q in ((-0.36, -2.25, 13.0), (0.36, -2.25, 13.0), (0.36, -1.45, 13.0), (-0.36, -1.45, 13.0),
                                 (-0.22, -1.85, 14.0), (0.22, -1.85, 14.0), (0.22, -1.45, 14.0), (-0.22, -1.45, 14.0))],
         ps, label="nose")
    block(g, (-1.45, -2.4, 12.7), (1.45, -1.8, 13.2), ps, label="moustache")
    rings = []
    for z, w, y0, y1 in ((13.15, 3.4, -2.1, -1.0), (11.8, 3.95, -2.8, -1.3), (10.2, 3.3, -2.95, -1.75),
                         (8.9, 2.1, -2.8, -1.9), (7.9, 0.8, -2.5, -2.05)):
        rings.append([Vector((w / 2, y0, z)), Vector((-w / 2, y0, z)), Vector((-w / 2, y1, z)), Vector((w / 2, y1, z))])
    loft(g, rings, ps, cap0=True, cap1=True, label="beard")
    lathe(g, [(2.05, 14.3), (2.05, 14.78), (1.78, 14.78), (1.7, 15.6), (1.2, 16.35), (0.0, 16.7)], 8, ps,
          c=(0.0, -0.15, 0.0), phase=math.pi / 8, label="helmet")
    for s in (-1, 1):
        horn(g, [Vector((s * 1.45, -0.15, 15.3)), Vector((s * 2.5, -0.25, 15.75)), Vector((s * 3.2, -0.35, 16.6)),
                 Vector((s * 3.45, -0.4, 17.4)), Vector((s * 3.35, -0.4, 18.0))], [0.58, 0.46, 0.33, 0.2], 6, ps,
             label="horn")
    # great iron double axe planted head-down on the plinth (bow-tie blades, gold collar)
    cyl(g, (0.0, -3.3, 1.0), (0.0, -3.3, 9.6), 0.3, 0.3, 6, piron, label="haft")
    half_head = [(0.45, 1.25), (1.3, 0.85), (2.2, 0.15), (2.6, 0.0), (2.95, 0.9), (3.1, 1.75), (2.95, 2.6),
                 (2.6, 3.5), (2.2, 3.35), (1.3, 2.65), (0.45, 2.25)]
    head = half_head + [(-u, v) for u, v in reversed(half_head)]
    extrude_poly(g, head, (0.0, -3.55, 0.0), (1, 0, 0), (0, 0, 1), 0.5, piron, label="axe_head")
    block(g, (-0.62, -3.7, 1.05), (0.62, -2.9, 2.45), pg, label="axe_collar")
    return g


# cliff blocks (centre, half size, points); x > 0 entries are mirrored with jitter
GATE_ROCK_SIDE = [((31.5, 3.0, 12.5), (11.5, 6.0, 13.5), 24), ((29.5, 2.0, 32.5), (10.0, 6.5, 9.0), 22),
                  ((38.5, -3.0, 9.5), (4.5, 5.0, 10.5), 18), ((36.0, -9.5, 2.0), (3.5, 3.0, 3.0), 14),
                  ((21.5, 1.5, 40.0), (5.9, 6.0, 6.5), 18)]
GATE_ROCK_MID = [((-18.5, 12.0, 20.0), (23.5, 6.5, 21.0), 26), ((18.5, 12.0, 21.0), (23.5, 6.5, 22.0), 26),
                 ((0.0, 11.0, 45.5), (25.0, 7.5, 11.5), 26), ((0.0, 1.0, 42.0), (11.5, 5.5, 5.5), 20),
                 ((-27.0, 7.0, 49.0), (7.5, 6.5, 8.5), 18), ((26.5, 8.0, 50.0), (7.0, 6.0, 8.0), 18),
                 ((6.0, 9.0, 53.5), (8.5, 6.0, 4.5), 18), ((-10.0, 8.5, 51.5), (7.0, 6.0, 4.5), 16)]


def gen_dwarf_gate():
    mid = "dwarf_gate"
    rnd = random.Random(hash_seed(mid))
    parts = {k: Geo() for k in ("Stone", "Gold", "Rock", "Statues", "DoorL", "DoorR")}
    ST, GD, RK, SU = parts["Stone"], parts["Gold"], parts["Rock"], parts["Statues"]
    G = GATE
    pm = P("masonry", MASONRY["S"], anchor=MASONRY["anchor"])
    pd = P("dwarf_stone", 4.0, rnd=rnd)
    pdv = P("dwarf_stone", 6.0, grain=Z, rnd=rnd)
    pg = P("gold", 2.0)
    Y0, Y1, YD = G["y_o0"], G["y_o1"], G["y_door"]
    # ---- podium (landing) and the steps the road ends at ----
    gbox(ST, (-20.0, -10.5, -0.8), (20.0, 9.0, 3.0), pm, skip=("bottom",), label="podium")
    gbox(ST, (-12.98, -13.0, -0.8), (12.98, -10.4, 2.0), pm, skip=("bottom", "back"), label="step")
    gbox(ST, (-13.0, -15.5, -0.8), (13.0, -12.9, 1.0), pm, skip=("bottom", "back"), label="step")
    block(ST, (-9.6, -0.95, 3.0), (9.6, YD + 0.05, 3.25), pd, skip=("bottom",), label="threshold")
    # ---- facade: back wall (door plane), masonry outer order, plain inner order, pillars ----
    gbox(ST, (-19.9, YD, 3.0), (19.9, 8.9, 35.8), pm, skip=("bottom",), label="back_wall")
    stepped_frame(ST, G["o0"], 25.0, 19.95, 27.0, Y0, YD, pm, "order0")
    stepped_frame(ST, G["o1"], 24.0, 11.6, 25.6, Y1, YD, pd, "order1")
    outline_trim(GD, G["o0"], 25.0, Y0, pg)
    outline_trim(GD, G["o1"], 24.0, Y1, pg, w=0.22, t=0.12, label="gold_outline_inner")
    for s in (-1, 1):
        def X(a, b):
            return tuple(sorted((s * a, s * b)))
        xa, xb = X(13.9, 20.0)
        chamfer_hexa(ST, box_corners((xa, -6.0, 3.0), (xb, -1.9, 4.4)), 0.22, pd, skip=(), label="pillar_base")
        xa, xb = X(14.2, 19.8)
        chamfer_hexa(ST, box_corners((xa, -5.6, 4.35), (xb, -1.9, 5.5)), 0.15, pd, skip=(), label="pillar_base")
        xa, xb = X(14.5, 19.5)
        c = 0.55
        prism(ST, [(xa + c, -5.2), (xb - c, -5.2), (xb, -5.2 + c), (xb, -1.93), (xa, -1.93), (xa, -5.2 + c)],
              5.45, 24.05, pdv, cap0=False, cap1=False, label="pillar")
        for z0 in (10.5, 17.5):
            ox = 14.3 if s > 0 else -19.7
            rune_slab(ST, (ox, -5.4, z0), (1, 0, 0), (0, -1, 0), 5.4, 2.0, 0.4, pd, row=0, label="collar")
            for xs, U, Nn in ((19.7, (0, 1, 0), (1, 0, 0)), (14.3, (0, -1, 0), (-1, 0, 0))):
                xo = s * xs
                Uo = Vector(U) if s > 0 else Vector((0, -U[1], 0))
                No = Vector(Nn) if s > 0 else Vector((-Nn[0], 0, 0))
                y_start = -5.0 if Uo.y > 0 else -1.95
                rune_slab(ST, (xo, y_start, z0), Uo, No, 3.05, 2.0, 0.4, pd, row=1, label="collar")
            xa, xb = X(14.15, 19.85)
            block(GD, (xa, -5.55, z0 - 0.25), (xb, -1.95, z0 + 0.05), pg, skip=("back",), label="collar_trim")
            block(GD, (xa, -5.55, z0 + 1.95), (xb, -1.95, z0 + 2.25), pg, skip=("back",), label="collar_trim")
        xa, xb = X(14.2, 19.8)
        chamfer_hexa(ST, box_corners((xa, -5.6, 24.0), (xb, -1.9, 25.2)), 0.15, pd, skip=(), label="capital")
        xa, xb = X(14.05, 19.97)
        block(GD, (xa, -5.75, 25.1), (xb, -1.95, 25.4), pg, skip=("back",), label="capital_trim")
        xa, xb = X(13.9, 20.0)
        chamfer_hexa(ST, box_corners((xa, -6.0, 25.3), (xb, -1.9, 27.0)), 0.2, pd, skip=(), label="capital")
    # ---- entablature with the great rune band, cornice, stepped crown ----
    gbox(ST, (-20.0, -6.2, 27.0), (20.0, 8.8, 31.0), pm, label="entablature")
    rune_slab(ST, (-19.6, -6.45, 28.0), (1, 0, 0), (0, -1, 0), 39.2, 2.0, 0.3, pd, row=0, label="frieze")
    block(GD, (-20.02, -6.62, 27.7), (20.02, -6.15, 28.02), pg, skip=("back",), label="frieze_trim")
    block(GD, (-20.02, -6.62, 29.98), (20.02, -6.15, 30.3), pg, skip=("back",), label="frieze_trim")
    chamfer_hexa(ST, box_corners((-20.0, -6.9, 31.0), (20.0, 8.7, 32.0)), 0.25, pd, skip=("bottom",), label="cornice")
    block(GD, (-20.02, -7.0, 31.15), (20.02, -6.8, 31.45), pg, skip=("back",), label="cornice_trim")
    chamfer_hexa(ST, box_corners((-15.0, -5.9, 31.95), (15.0, 8.0, 34.0)), 0.2, pd, skip=("bottom",), label="crown")
    block(GD, (-15.05, -6.02, 33.4), (15.05, -5.85, 33.7), pg, skip=("back",), label="crown_trim")
    chamfer_hexa(ST, box_corners((-8.5, -4.9, 33.95), (8.5, 7.0, 36.0)), 0.2, pd, skip=("bottom",), label="crown")
    peaks = [(-3.4, 34.2), (3.4, 34.2), (3.4, 34.55), (2.3, 35.4), (1.35, 34.8), (0.0, 35.85), (-1.35, 34.8),
             (-2.3, 35.4), (-3.4, 34.55)]
    extrude_poly(GD, peaks, (0.0, -5.15, 0.0), (1, 0, 0), (0, 0, 1), 0.3, pg, back=False, label="crown_emblem")
    # tympanum anvil over the doors
    anvil = [(-1.5, 19.0), (1.5, 19.0), (1.5, 19.3), (0.75, 19.55), (0.6, 20.4), (1.7, 20.6), (1.75, 21.6),
             (-1.3, 21.6), (-2.6, 21.25), (-1.3, 20.6), (-0.6, 20.4), (-0.75, 19.55), (-1.5, 19.3)]
    extrude_poly(GD, anvil, (0.0, YD - 0.3, 0.0), (1, 0, 0), (0, 0, 1), 0.35, pg, back=False, label="anvil")
    for s in (-1, 1):
        dia = [(s * 4.6, 19.3), (s * 5.3, 20.0), (s * 4.6, 20.7), (s * 3.9, 20.0)]
        extrude_poly(GD, dia, (0.0, YD - 0.2, 0.0), (1, 0, 0), (0, 0, 1), 0.25, pg, back=False, label="gem")
    # ---- closed iron double door (each leaf its own mesh) ----
    D = G["door"]
    R = Geo()
    piron = P("dark_stone", 9.0)
    xa, xb = 0.03, D["x"]
    slab_grid(R, (xa, D["y_front"], D["z0"]), (xb - xa, 0, 0), (0, 0, D["z1"] - D["z0"]), 2, 3,
              D["y_back"] - D["y_front"], piron, label="leaf")
    yf = D["y_front"]
    for lo, hi in (((xa + 0.15, yf - 0.18, D["z0"] + 0.3), (xa + 0.55, yf + 0.05, D["z1"] - 0.3)),
                   ((xb - 0.55, yf - 0.18, D["z0"] + 0.3), (xb - 0.15, yf + 0.05, D["z1"] - 0.3)),
                   ((xa + 0.13, yf - 0.21, D["z0"] + 0.23), (xb - 0.13, yf + 0.05, D["z0"] + 0.65)),
                   ((xa + 0.13, yf - 0.21, D["z1"] - 0.7), (xb - 0.13, yf + 0.05, D["z1"] - 0.28))):
        block(R, lo, hi, pg, skip=("back",), label="door_trim")
    zc = 10.6
    for z0 in (7.6, 13.1):
        block(R, (1.05, yf - 0.16, z0), (xb - 0.55, yf + 0.05, z0 + 0.5), pg, skip=("back",), label="door_band")
        for x in (2.6, 5.0, 7.4):
            stud(R, (x, yf - 0.16, z0 + 0.25), (0, -1, 0), 0.19, 0.24, pg)
    chev = [(0.045, zc + 3.6), (3.0, zc), (0.045, zc - 3.6), (0.045, zc - 2.45), (1.95, zc), (0.045, zc + 2.45)]
    extrude_poly(R, chev, (0.0, yf - 0.24, 0.0), (1, 0, 0), (0, 0, 1), 0.3, pg, back=False, label="door_emblem")
    extrude_poly(R, [(0.045, zc + 1.25), (0.9, zc), (0.045, zc - 1.25)], (0.0, yf - 0.24, 0.0), (1, 0, 0), (0, 0, 1),
                 0.3, pg, back=False, label="door_emblem")
    merge(parts["DoorR"], R)
    merge(parts["DoorL"], R, Matrix.Scale(-1.0, 4, (1.0, 0.0, 0.0)))
    # ---- guardian statues ----
    sg = statue_geo(rnd)
    for s in (-1, 1):
        merge(SU, sg, Matrix.Translation((s * G["statues_x"], G["statues_y"], G["plinth_top"])))
    # ---- the cliff: back body behind the facade, shoulders wrapping its sides, crags above ----
    prk = P("stone_grey", 9.0, rnd=rnd, fn=moss_fn(rnd, 0.7, 0.62))
    rocks = []

    def add_crag(c, h, npt):
        zmin = -1.0 if c[2] - h[2] < 0.5 else -1e9
        rocks.append(crag(RK, c, h, npt, rnd, prk, zmin=zmin, boxy=0.78, jit=0.1, label="rock"))
    for c, h, npt in GATE_ROCK_MID:
        add_crag(c, h, npt)
    for s in (-1, 1):
        for c, h, npt in GATE_ROCK_SIDE:
            j = (rnd.uniform(-0.6, 0.6), rnd.uniform(-0.3, 0.3), rnd.uniform(-0.6, 0.6))
            add_crag((s * c[0] + j[0] * (abs(c[0]) > 25), c[1] + j[1], c[2] + j[2]),
                     (h[0], h[1], h[2] * rnd.uniform(0.94, 1.06)), npt)
    # ---- colliders ----
    cols = [[[0.0, -14.25, 0.1], [26.0, 2.5, 1.8], 0.0], [[0.0, -11.75, 0.6], [26.0, 2.5, 2.8], 0.0],
            [[0.0, -0.75, 1.1], [40.0, 19.5, 3.8], 0.0],
            [[0.0, 3.5, 19.5], [40.0, 11.0, 33.0], 0.0],                      # facade + closed doors
            [[0.0, 1.05, 29.5], [40.0, 15.9, 5.0], 0.0],                      # entablature + cornice
            [[0.0, 1.05, 34.0], [30.0, 13.9, 4.0], 0.0]]                      # crown
    for s in (-1, 1):
        cols.append([[s * 16.95, -3.95, 15.0], [6.1, 4.1, 24.0], 0.0])        # pillars
        cols.append([[s * G["statues_x"], G["statues_y"], 0.85], [7.2, 7.2, 3.3], 0.0])
        cols.append([[s * G["statues_x"], G["statues_y"] - 0.3, 11.5], [7.6, 5.4, 18.0], 0.0])
    for mn, mx in rocks:
        cols.append(box_collider(mn, mx, 0.82))
    info = dict(portal=dict(width=40.0, height=36.0, front_face_y=Y0, door_plane_y=YD),
                opening=dict(order0=G["o0"], order1=G["o1"], note="(z0, z1, half width) bands of the stepped arch"),
                steps=dict(width=26.0, risers=3, rise=1.0, tread=2.5, front_y=-15.5,
                           note="the 22-stud road ends at the first riser (y = -15.5); landing at z = 3"),
                doors=dict(DoorL=dict(x_range=[-D["x"], -0.03], hinge_axis_xy=[-D["x"], round(D["y_back"] - 0.4, 3)]),
                           DoorR=dict(x_range=[0.03, D["x"]], hinge_axis_xy=[D["x"], round(D["y_back"] - 0.4, 3)]),
                           z_range=[D["z0"], D["z1"]], front_face_y=D["y_front"],
                           note="closed; the facade behind them is solid (the city is off-map)"),
                statues=dict(positions=[[-G["statues_x"], G["statues_y"], 0.0], [G["statues_x"], G["statues_y"], 0.0]],
                             plinth_top=G["plinth_top"], figure_height=18.0),
                rock_shoulders="%d faceted cliff blocks: x to about +-43, top at z ~57, back body behind the facade"
                               % len(rocks),
                origin="centre of the portal footprint at ground level; the door plane is at y = 0.6")
    return dict(parts=parts, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# blumund_marker: border post toward the Kingdom of Blumund
# --------------------------------------------------------------------------------------
BLU_SHIFT = Vector((3.2, 1.5, 0.0))            # moves the footprint centre to the origin


def gen_blumund():
    mid = "blumund_marker"
    rnd = random.Random(hash_seed(mid))
    parts = {k: Geo() for k in ("Stone", "Wood", "Roof", "Sign", "Barrier")}
    ST, WD, RF, SG, BR = (parts[k] for k in ("Stone", "Wood", "Roof", "Sign", "Barrier"))
    ps = P("stone_grey", 3.0, rnd=rnd, fn=moss_fn(rnd, 0.35, 0.6))
    pw = P("wood", 4.0, grain=Z)
    pwh = P("wood", 4.0, grain=(1, 0, 0))
    pwy = P("wood", 4.0, grain=(0, 1, 0))
    pt = P("wood_light", 4.0, grain=Z)
    piron = P("dark_stone", 2.0)
    # ---- guard booth 6 x 6 x 9 (centre 0, 0) ----
    chamfer_hexa(ST, box_corners((-3.1, -3.1, -0.6), (3.1, 3.1, 0.45)), 0.12, ps, skip=(), label="footing")
    block(WD, (-2.85, -2.85, 0.4), (2.85, 2.85, 0.6), pwy, fp={"top": P("dark_stone", 4.0)}, label="floor")
    for sx in (-1, 1):
        for sy in (-1, 1):
            a, b = sorted((sx * 2.4, sx * 2.95))
            c, d = sorted((sy * 2.4, sy * 2.95))
            block(WD, (a, c, 0.45), (b, d, 6.1), pt, label="post")
    t0, t1 = 2.58, 2.82
    pin = P("dark_stone", 4.0)              # interior in shadow: reads as a dark room through the windows
    # front wall (-Y) with an open counter window
    for lo, hi in (((-2.45, -t1, 0.6), (2.45, -t0, 3.0)), ((-2.45, -t1, 3.0), (-1.55, -t0, 5.0)),
                   ((1.55, -t1, 3.0), (2.45, -t0, 5.0)), ((-2.45, -t1, 5.0), (2.45, -t0, 6.0))):
        block(WD, lo, hi, pw, fp={"back": pin}, label="wall")
    # road-side wall (-X) with a small window
    for lo, hi in (((-t1, -2.45, 0.6), (-t0, 2.45, 3.2)), ((-t1, -2.45, 3.2), (-t0, -1.1, 5.0)),
                   ((-t1, 1.1, 3.2), (-t0, 2.45, 5.0)), ((-t1, -2.45, 5.0), (-t0, 2.45, 6.0))):
        block(WD, lo, hi, pw, fp={"right": pin}, label="wall")
    block(WD, (t0, -2.45, 0.6), (t1, 2.45, 6.0), pw, fp={"left": pin}, label="wall")
    block(WD, (-2.45, t0, 0.6), (2.45, t1, 6.0), pw, fp={"front": pin}, label="wall")
    # door on the +X side
    block(WD, (t1 - 0.05, -0.9, 0.47), (t1 + 0.12, 0.9, 4.6), pw, skip=("left",), label="door")
    for lo, hi in (((t1 - 0.05, -1.15, 0.47), (t1 + 0.2, -0.9, 4.85)), ((t1 - 0.05, 0.9, 0.47), (t1 + 0.2, 1.15, 4.85)),
                   ((t1 - 0.05, -1.17, 4.58), (t1 + 0.22, 1.17, 4.87))):
        block(WD, lo, hi, pt, skip=("left",), label="door_frame")
    block(WD, (t1 + 0.1, 0.45, 2.45), (t1 + 0.24, 0.65, 2.75), piron, skip=("left",), label="door_handle")
    # counter + sill, top plates
    block(WD, (-1.75, -3.2, 2.85), (1.75, -2.5, 3.02), pt.but(grain=(1, 0, 0)), label="counter")
    block(WD, (-3.2, -1.25, 3.05), (-2.5, 1.25, 3.22), pt.but(grain=(0, 1, 0)), label="sill")
    for lo, hi in (((-3.0, -3.0, 5.95), (3.0, -2.45, 6.3)), ((-3.0, 2.45, 5.95), (3.0, 3.0, 6.3)),
                   ((-3.0, -2.4, 5.95), (-2.45, 2.4, 6.3)), ((2.45, -2.4, 5.95), (3.0, 2.4, 6.3))):
        block(WD, lo, hi, pt.but(grain=(1, 0, 0)), label="top_plate")
    # gables (planks) and the slate roof (ridge along Y, top of the ridge cap at z = 9)
    gab = [(-2.95, 6.25), (2.95, 6.25), (0.0, 8.42)]
    extrude_poly(WD, gab, (0.0, -t1, 0.0), (1, 0, 0), (0, 0, 1), t1 - t0, pwh, p_back=pin, label="gable")
    extrude_poly(WD, gab, (0.0, t1, 0.0), (-1, 0, 0), (0, 0, 1), t1 - t0, pwh, p_back=pin, label="gable")
    slope = 2.8 / 3.65
    for s in (-1, 1):
        x0 = -s * 0.15
        o = (x0, -3.75 if s > 0 else 3.53, 8.7 + 0.15 * slope)          # slabs differ by 0.02 at the
        U = (s * 3.8, 0.0, -3.8 * slope)                                   # gable ends: no coplanar overlap
        V = (0.0, 7.3 if s > 0 else -7.26, 0.0)
        slab_grid(RF, o, U, V, 2, 2, 0.25, P("dark_stone", 3.0, grain=(0, 0, 1)), p_back=pwy,
                  p_side=pt, label="roof")
    block(RF, (-0.3, -3.85, 8.55), (0.3, 3.65, 9.0), pt.but(grain=(0, 1, 0)), label="ridge")
    # ---- barrier: post with a side-mounted pivot, striped pole lifted at 76 degrees ----
    bx, by, pz = -4.4, -2.0, 3.4
    block(WD, (bx - 0.35, by - 0.35, -0.8), (bx + 0.35, by + 0.35, 3.95), pt, label="barrier_post")
    lathe(WD, [(0.5, 3.9), (0.0, 4.3)], 4, pt, c=(bx, by, 0.0), phase=math.pi / 4, label="post_cap")
    cyl(BR, (bx, by - 1.25, pz), (bx, by + 0.15, pz), 0.13, 0.13, 6, piron, label="axle")
    block(BR, (bx - 0.28, by - 0.67, pz - 0.28), (bx + 0.28, by - 0.3, pz + 0.28), piron, label="washer")
    yp = by - 0.85
    al = math.radians(76.0)
    d = Vector((-math.cos(al), 0.0, math.sin(al)))
    piv = Vector((bx, yp, pz))
    T, N, B = frame_along(piv, piv + d, Vector((0.0, 1.0, 0.0)))
    ss = [-1.9 + 1.25 * k for k in range(11)] + [11.2]
    rings = [[piv + d * s + (N * math.cos(2 * math.pi * j / 8) + B * math.sin(2 * math.pi * j / 8)) * 0.22
              for j in range(8)] for s in ss]
    pv, pc = P("vermilion", 2.0), P("bone", 2.0)
    loft(BR, rings, pv, cap0=True, cap1=True, label="pole", band_p=lambda i: pv if i % 2 == 0 else pc)
    cw = piv - d * 1.75
    chamfer_hexa(BR, box_corners((cw.x - 0.42, cw.y - 0.38, cw.z - 0.42), (cw.x + 0.42, cw.y + 0.38, cw.z + 0.42)),
                 0.08, ps.but(fn=None), skip=(), label="counterweight")
    # ---- boundary pillar (10 tall) with the signboard arm ----
    qx, qy = -8.2, -4.8
    chamfer_hexa(ST, box_corners((qx - 1.3, qy - 1.3, -0.6), (qx + 1.3, qy + 1.3, 0.9)), 0.15, ps, skip=(), label="plinth")
    lathe(ST, [(1.22, 0.85), (1.05, 8.6)], 4, ps.but(fn=None), c=(qx, qy, 0.0), phase=math.pi / 4, cap0=False,
          cap1=False, label="shaft")
    lathe(ST, [(1.2, 6.9), (1.2, 7.3), (0.0, 7.3)], 4, ps.but(fn=None), c=(qx, qy, 0.0), phase=math.pi / 4,
          label="collar")
    chamfer_hexa(ST, box_corners((qx - 1.15, qy - 1.15, 8.55), (qx + 1.15, qy + 1.15, 9.1)), 0.1, ps.but(fn=None),
                 skip=(), label="cap")
    lathe(ST, [(1.45, 9.05), (0.0, 10.0)], 4, ps.but(fn=None), c=(qx, qy, 0.0), phase=math.pi / 4, label="cap_top")
    beam(WD, (qx + 0.2, qy, 8.0), (qx - 4.7, qy, 8.0), 0.42, 0.42, pt.but(grain=(1, 0, 0)), label="sign_arm")
    beam(WD, (qx - 0.6, qy, 6.2), (qx - 2.25, qy, 7.85), 0.3, 0.3, pt, label="brace")
    sx0, sx1, sz0, sz1 = qx - 4.35, qx - 0.95, 5.15, 7.25
    block(SG, (sx0, qy - 0.12, sz0), (sx1, qy + 0.12, sz1), pwh, label="board")
    block(SG, (sx0 + 0.25, qy - 0.17, sz0 + 0.25), (sx1 - 0.25, qy - 0.08, sz1 - 0.25), P("bone", 3.0),
          label="board_face")
    block(SG, (sx0 + 0.25, qy + 0.08, sz0 + 0.25), (sx1 - 0.25, qy + 0.17, sz1 - 0.25), P("bone", 3.0),
          label="board_face")
    for hx in (sx0 + 0.45, sx1 - 0.45):
        block(SG, (hx - 0.07, qy - 0.07, sz1 - 0.1), (hx + 0.07, qy + 0.07, 7.84), piron, label="hanger")
    for g in parts.values():
        g.co = [tuple(Vector(p) + BLU_SHIFT) for p in g.co]
    sh = BLU_SHIFT
    cols = [[[sh.x, sh.y, 2.85], [6.2, 6.2, 6.9], 0.0], [[sh.x, sh.y, 7.6], [7.3, 7.6, 2.8], 0.0],
            [[qx + sh.x, qy + sh.y, 0.15], [2.6, 2.6, 1.5], 0.0], [[qx + sh.x, qy + sh.y, 5.0], [2.2, 2.2, 10.0], 0.0],
            [[bx + sh.x, by + sh.y, 1.6], [0.7, 0.7, 4.8], 0.0],
            [[cw.x + sh.x, cw.y + sh.y, cw.z], [0.9, 0.8, 0.9], 0.0]]
    info = dict(sign_face=dict(centre=[round((sx0 + sx1) / 2 + sh.x, 3), round(qy - 0.17 + sh.y, 3), round((sz0 + sz1) / 2, 3)],
                               size=[round(sx1 - sx0 - 0.5, 3), round(sz1 - sz0 - 0.5, 3)], normal=[0, -1, 0],
                               mesh="blumund_marker_Sign", note="blank cream face for the game's text (both sides)"),
                barrier=dict(mesh="blumund_marker_Barrier",
                             pivot=[round(piv.x + sh.x, 3), round(piv.y + sh.y, 3), round(piv.z, 3)],
                             axis=[0, 1, 0], lifted_angle_deg=76.0,
                             note="rotate by -76 deg about +Y through the pivot (right-handed, Blender axes) to "
                                  "lower the pole level across the road toward -X (11.2 studs from the pivot to "
                                  "the tip)"),
                booth=dict(size=[6.2, 6.2, 9.0], centre=[round(sh.x, 3), round(sh.y, 3)]),
                pillar=dict(height=10.0, position=[round(qx + sh.x, 3), round(qy + sh.y, 3)]),
                road="the road passes on the -X side; the sign faces travellers coming along -Y")
    return dict(parts=parts, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# milim_marker: the eastern border of the Demon Lord Milim's lands
# --------------------------------------------------------------------------------------
def gen_milim():
    mid = "milim_marker"
    rnd = random.Random(hash_seed(mid))
    parts = {k: Geo() for k in ("Monolith", "Stones", "Ground")}
    MO, SN, GR = parts["Monolith"], parts["Stones"], parts["Ground"]
    pd = P("dark_stone", 4.0, rnd=rnd)
    pch = P("charred", 5.0, rnd=rnd)
    # ---- monolith: chamfered slab, planar leaning front face carrying the carved claw ----
    zs_ = [-0.8, 0.6, 3.5, 7.0, 10.5, 13.3]
    hws = [3.6, 3.5, 3.3, 3.05, 2.8, 2.5]
    hds = [1.85, 1.8, 1.7, 1.6, 1.48, 1.32]
    c = 0.5
    lean = 0.06

    def yfront(z):
        return -1.75 + lean * z
    MO.mark("monolith")
    rings = []
    for i, (z, hw, hd) in enumerate(zip(zs_, hws, hds)):
        yf = yfront(z)
        yb = hd + rnd.uniform(-0.15, 0.15)
        j = 0.0 if i == 0 else 0.22
        pts = [(-(hw - c), yf), (hw - c, yf), (hw + rnd.uniform(-j, j), yf + c), (hw + rnd.uniform(-j, j), yb - c),
               (hw - c + rnd.uniform(-j, j), yb), (-(hw - c) + rnd.uniform(-j, j), yb), (-hw + rnd.uniform(-j, j), yb - c),
               (-hw + rnd.uniform(-j, j), yf + c)]
        rings.append([MO.v((x, y, z)) for x, y in pts])
    # broken, jagged top: a smaller twisted ring then two peaks
    top = [(-1.7, -0.75), (1.2, -0.85), (2.0, -0.3), (1.9, 0.6), (1.0, 1.0), (-1.4, 1.0), (-2.2, 0.5), (-2.1, -0.4)]
    rings.append([MO.v((x - 0.3, y, 14.7 + rnd.uniform(-0.3, 0.3))) for x, y in top])
    for i in range(len(rings) - 1):
        A, B_ = rings[i], rings[i + 1]
        for k in range(8):
            if k == 0 and 1 <= i <= 4:
                continue
            face(MO, [A[k], A[(k + 1) % 8], B_[(k + 1) % 8], B_[k]], pch if i == 0 else pd)
    pa = MO.v((-1.1, 0.15, 16.0))
    pb = MO.v((1.15, -0.2, 15.2))
    R6 = rings[-1]
    ctr = Vector((0.0, 0.0, 12.0))
    tris = [(R6[k], R6[k + 1], pb) for k in range(0, 3)] + [(R6[k], R6[(k + 1) % 8], pa) for k in range(4, 8)]
    tris += [(R6[3], R6[4], pb), (R6[4], pa, pb), (R6[0], pb, pa)]
    for t in tris:
        pts = [Vector(MO.co[q]) for q in t]
        face_dir(MO, list(t), pd, sum(pts, Vector()) / 3 - ctr)
    face_dir(MO, list(rings[0]), pd, (0, 0, -1))
    # carved dragon-claw emblem (faces), glowing ember floor
    nrm = Vector((0.0, -1.0, lean)).normalized()
    cv = 8.4
    palm = ellipse2(0.0, cv - 2.05, 1.2, 0.85, 9, phase=math.pi / 2)
    claws = [talon(0.0, cv - 0.95, math.radians(90), 3.55, 0.4, 0.3),
             talon(-0.95, cv - 1.2, math.radians(121), 3.05, 0.38, -0.62),
             talon(0.95, cv - 1.2, math.radians(59), 3.05, 0.38, 0.62),
             talon(-1.35, cv - 2.35, math.radians(170), 1.35, 0.27, -0.55)]
    emblem = [palm] + claws
    if not polys_disjoint(emblem):
        raise RuntimeError("milim emblem polygons overlap")
    outer2d = [(-(hws[i] - c), zs_[i]) for i in (1,)] + [((hws[i] - c), zs_[i]) for i in (1, 2, 3, 4, 5)] + \
              [(-(hws[i] - c), zs_[i]) for i in (5, 4, 3, 2)]

    def to3d(u, v):
        return Vector((u, yfront(v), v))
    nfl = carve_face(MO, outer2d, emblem, to3d, nrm, 0.34, pd, pd, P("ember", 2.0), label="emblem")
    # ---- three smaller jagged stones ----
    shards = []
    for ang, rad, h, r0, tilt in ((330.0, 6.6, 5.4, 1.35, 16.0), (212.0, 6.2, 4.2, 1.15, 20.0), (95.0, 6.9, 6.6, 1.5, 12.0)):
        a = math.radians(ang + rnd.uniform(-6, 6))
        cx, cy = rad * math.cos(a), rad * math.sin(a)
        out = Vector((math.cos(a), math.sin(a), 0.0))
        t = math.radians(tilt)
        pts = []
        for k in range(6):
            th = 2 * math.pi * k / 6 + rnd.uniform(-0.3, 0.3)
            rr = r0 * rnd.uniform(0.75, 1.1)
            pts.append(Vector((cx + rr * math.cos(th), cy + rr * math.sin(th), -0.7)))
        for k in range(4):
            th = 2 * math.pi * k / 4 + rnd.uniform(-0.4, 0.4)
            rr = r0 * rnd.uniform(0.45, 0.7)
            zz = h * rnd.uniform(0.4, 0.6)
            pts.append(Vector((cx + rr * math.cos(th), cy + rr * math.sin(th), zz)) + out * (math.tan(t) * zz))
        pts.append(Vector((cx + rnd.uniform(-0.25, 0.25), cy + rnd.uniform(-0.25, 0.25), h)) + out * (math.tan(t) * h))
        pts.append(Vector((cx, cy, h * 0.82)) + out * (math.tan(t) * h * 0.82 + 0.35))
        shards.append(_hull_into(SN, pts, pd, "shard"))
    # ---- scorched ground disc ----
    GR.mark("disc")
    nsec, rr_ = 32, [1.3, 3.0, 4.6, 6.2, 7.8, 9.4]
    nzd = Noise3(rnd, 6, 3.0)
    rays = [(rnd.uniform(0, 2 * math.pi), rnd.uniform(2.5, 6.0), rnd.uniform(0.12, 0.28)) for _ in range(8)]

    def scorch_r(th):
        r = 3.6
        for t0, L, w in rays:
            d = (th - t0 + math.pi) % (2 * math.pi) - math.pi
            r += L * math.exp(-(d / w) ** 2)
        return r

    def ztop(r):
        return 0.2 - 0.015 * r
    ctr_v = GR.v((0.0, 0.0, ztop(0.0)))
    grid = []
    for i in range(nsec):
        th = 2 * math.pi * i / nsec
        col = []
        for k, r in enumerate(rr_):
            rj = r + rnd.uniform(-0.25, 0.25)
            tj = th + rnd.uniform(-0.05, 0.05)
            col.append(Vector((rj * math.cos(tj), rj * math.sin(tj), ztop(rj))))
        Ro = 10.7 + 0.8 * nzd(Vector((math.cos(th) * 3, math.sin(th) * 3, 0.0)))
        col.append(Vector((Ro * math.cos(th), Ro * math.sin(th), 0.04)))
        col.append(Vector(((Ro + 0.35) * math.cos(th), (Ro + 0.35) * math.sin(th), -0.3)))
        grid.append([GR.v(q) for q in col])
    prk = P("stone_grey", 5.0, rnd=rnd)
    for i in range(nsec):
        j = (i + 1) % nsec
        A, B_ = grid[i], grid[j]

        def paint(q):
            r = q.length
            th = math.atan2(q.y, q.x) % (2 * math.pi)
            return pch if r < scorch_r(th) and rnd.random() > 0.08 else prk
        q0 = (Vector(GR.co[A[0]]) + Vector(GR.co[B_[0]])) / 3
        face_dir(GR, [ctr_v, A[0], B_[0]], paint(q0), Z)
        for k in range(len(A) - 1):
            idx = [A[k], B_[k], B_[k + 1], A[k + 1]]
            q = sum((Vector(GR.co[v]) for v in idx), Vector()) / 4
            face_dir(GR, idx, paint(q) if k < len(A) - 2 else prk, Z if k < len(A) - 2 else q.normalized())
    face_dir(GR, [grid[i][-1] for i in range(nsec)], prk, (0, 0, -1))
    cols = [[[0.0, 0.05, 7.6], [6.8, 3.4, 16.0], 0.0]]
    for mn, mx in shards:
        cols.append(box_collider(mn, mx, 0.75))
    info = dict(monolith_height=16.0, emblem=dict(kind="carved dragon claw (palm + 3 talons + dew claw), no text",
                                                  carve_depth=0.34, floor_tile="ember", floor_triangles=nfl,
                                                  face_normal=[round(v, 3) for v in nrm]),
                ground_disc="radius ~10.7-11.5, top 0.04-0.2 above ground, edge sunk to -0.3; charred scorch burst",
                stones="3 jagged dark shards leaning outward at 330, 212 and 95 degrees")
    return dict(parts=parts, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# limestone_cave: the Lizardmen's limestone caves at the lake marsh
# --------------------------------------------------------------------------------------
CAVE = dict(n=26, zs=-0.8, a_m=11.0, b_m=14.0, e_m=0.88, y_m=-13.0, brow=1.6, rim_noise=0.35,
            front=[(0.33, 1.2), (0.66, 1.0)],
            body=[(-10.8, 0.84, 0.98, 2.6), (-6.0, 0.97, 1.02, 1.0, 0.6), (0.0, 1.0, 0.97, 0.0, -0.8),
                  (6.0, 0.95, 0.86, 0.0, 0.9), (10.5, 0.82, 0.66, 0.0, -0.5), (13.4, 0.58, 0.4, 0.0, 0.3),
                  (14.8, 0.28, 0.12, 0.0, 0.0)],
            a_max=22.0, b_max=15.0, e_o=0.75,
            tunnel=[(1.3, 0.97, 0.96), (3.6, 0.91, 0.9), (6.6, 0.84, 0.83), (10.2, 0.78, 0.77), (15.0, 0.72, 0.7)],
            amp=2.2, amp_front=1.0, noise_scale=10.0, k_fade=0.054, jitter=0.3,
            lobes=[((-15.0, 0.0, 14.0), 8.0, 2.2), ((13.0, 4.0, 15.0), 9.0, 2.4), ((20.0, -7.0, 8.0), 6.0, 2.0),
                   ((-21.0, 6.0, 8.0), 7.0, 2.2), ((1.0, 9.0, 13.0), 8.0, 2.0), ((-5.0, -9.5, 16.0), 5.0, 1.0)])
CAVE_MASSES = [((-17.5, 6.0), (15.0, 14.0, 22.0), 3), ((15.0, 7.0), (15.0, 14.0, 24.0), 3),
               ((1.0, 11.5), (20.0, 13.0, 26.0), 3), ((-18.5, -9.5), (11.0, 10.0, 16.0), 3),
               ((18.5, -10.0), (10.0, 9.0, 14.0), 3), ((-25.0, -14.0), (5.0, 4.2, 4.6), 2),
               ((24.0, -14.5), (5.6, 4.6, 5.0), 2)]


def gen_cave():
    mid = "limestone_cave"
    rnd = random.Random(hash_seed(mid))
    parts = {k: Geo() for k in ("Rock", "Inside")}
    RK, IN = parts["Rock"], parts["Inside"]
    plim = P("limestone", 7.0, rnd=rnd)
    mound(RK, IN, CAVE, rnd, plim, label="outcrop")
    omn, omx = geo_bbox(RK)
    # stalactites at the mouth (hang from the brow / first studs of the ceiling)
    stal = []
    for x, dy, L, r in ((-6.9, 0.9, 1.1, 0.5), (-5.7, 1.6, 2.5, 0.82), (-1.6, 0.7, 3.2, 1.0), (-0.5, 2.2, 1.3, 0.55),
                        (4.1, 1.2, 1.9, 0.72), (6.4, 2.4, 0.9, 0.45)):
        y = CAVE["y_m"] + dy
        zc = ray_z([IN, RK], x, y, 1.0)
        if zc is None:
            continue
        cone_down(RK, x, y, zc, r, L, 6, P("limestone", 3.0, rnd=rnd), rnd)
        stal.append([x, round(y, 2), round(zc - L, 2)])
    # big rounded, water-worn limestone masses merged into the outcrop + two loose boulders
    bl = []
    for c, sz, sub in CAVE_MASSES:
        pm_ = P("limestone", 7.0 if sz[0] > 9 else 4.0, rnd=rnd)
        bl.append(boulder(RK, sz, rnd, pm_, c=c, zsink=-0.7, subdiv=sub, lump=0.14, flat_top=0.3, label="mass"))
    # colliders: outcrop boxes (two per side along the tunnel, lintel, back mass) + back plate
    am, ym = CAVE["a_m"], CAVE["y_m"]
    tun = CAVE["tunnel"]
    cols = []
    mn, mx = geo_bbox(RK)
    for s in (-1, 1):
        for (d0, sa0, _), (d1, sa1, _) in (((0.0, 1.0, 1.0), tun[2]), (tun[2], tun[4])):
            w = am * min(sa0, sa1)
            xo = 23.5
            cols.append([[s * (w + xo) / 2, ym + (d0 + d1) / 2, 7.5], [xo - w, d1 - d0, 15.0], 0.0])
    cols.append([[0.0, ym + 3.3, 15.0], [2 * am, 6.6, 4.0], 0.0])           # lintel over the mouth
    cols.append([[0.0, ym + 10.8, 13.5], [2 * am * 0.84, 8.4, 6.0], 0.0])
    cols.append([[0.0, (ym + 15.0 + mx[1]) / 2, 7.5], [46.0, mx[1] - (ym + 15.0), 15.0], 0.0])   # back mass
    cols.append([[0.0, ym + 15.0, 5.0], [2 * am * tun[4][1], 0.6, 10.0], 0.0])                  # back plate
    for b_ in bl:
        cols.append(box_collider(b_[0], b_[1], 0.8))
    info = dict(mouth=dict(width=2 * am, height=CAVE["b_m"], rim_y_at_ground=ym, brow_overhang=CAVE["brow"]),
                tunnel=dict(depth_to_back_plate=15.0, back_plate_y=ym + 15.0,
                            fade="cave_dark tile: limestone shadow at the mouth -> black from ~%.1f studs in"
                                 % (0.5 / CAVE["k_fade"] - CAVE["brow"] - 1.0), floor_z=0.08),
                outcrop_shell_size=[round(omx[0] - omn[0], 2), round(omx[2], 2), round(omx[1] - omn[1], 2)],
                outcrop_note="outcrop shell alone [x, height above ground, depth]; the merged rounded masses and the "
                             "two loose boulders widen the model footprint",
                stalactites=stal, rounded_masses=len(bl),
                origin="centre of the outcrop footprint; the mouth opens toward -Y")
    return dict(parts=parts, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# limestone_rock_a / b / c: loose water-worn limestone boulders (6, 12, 20 studs)
# --------------------------------------------------------------------------------------
ROCKS = {"limestone_rock_a": dict(size=(6.0, 5.0, 5.4), moss=False, subdiv=3, flat=0.2),
         "limestone_rock_b": dict(size=(12.0, 9.2, 9.6), moss=True, subdiv=3, flat=0.3),
         "limestone_rock_c": dict(size=(20.0, 14.5, 15.0), moss=True, subdiv=3, flat=0.32)}


def gen_rock(mid):
    rnd = random.Random(hash_seed(mid))
    r = ROCKS[mid]
    parts = {"Rock": Geo()}
    g = parts["Rock"]
    p = P("limestone", 4.0 if r["size"][0] < 8 else 6.0, rnd=rnd)
    mn, mx = boulder(g, r["size"], rnd, p, zsink=-0.6 if r["size"][0] < 8 else -0.8, flat_top=r["flat"], lump=0.16,
                     subdiv=r["subdiv"], moss=(0.84, 0.62) if r["moss"] else None, label="boulder")
    cols = [box_collider(mn, mx, 0.8)]
    info = dict(nominal_size=r["size"][0], mossy_top=r["moss"])
    return dict(parts=parts, colliders=cols, info=info)


# --------------------------------------------------------------------------------------
# wolf_den: a burrow dug under a big mossy rock on a slope
# --------------------------------------------------------------------------------------
DEN = dict(n=12, zs=-0.9, a_m=5.0, b_m=7.0, e_m=0.62, y_m=-7.0, brow=0.9, rim_noise=0.2,
           front=[(0.5, 0.7)],
           body=[(-5.6, 0.82, 0.86, 1.6), (-2.0, 0.98, 1.0, 0.0), (2.6, 1.0, 0.97, 0.0), (5.6, 0.82, 0.7, 0.0),
                 (6.9, 0.45, 0.3, 0.0)],
           a_max=12.0, b_max=14.4, e_o=0.66,
           tunnel=[(1.2, 0.95, 0.92), (3.4, 0.86, 0.8), (5.6, 0.78, 0.7), (7.0, 0.74, 0.64)],
           amp=0.9, amp_front=0.5, noise_scale=5.0, k_fade=0.095, jitter=0.55, tjit=0.15,
           lobes=[((-6.0, 1.0, 13.0), 5.0, 1.2), ((6.5, -1.0, 12.0), 5.0, 1.0)])


def gen_den():
    mid = "wolf_den"
    rnd = random.Random(hash_seed(mid))
    parts = {k: Geo() for k in ("Rock", "Inside", "Bones")}
    RK, IN, BN = parts["Rock"], parts["Inside"], parts["Bones"]

    nzm = Noise3(rnd, 6, 5.0)

    def paint(n, c):
        if n.z > 0.5 and c.z > 8.5 and nzm(c) > -0.55:
            return "moss"
        if n.y < -0.5 and 1.5 < c.z < 7.5 and 5.2 < abs(c.x) < 9.0 and c.y < -4.0 and rnd.random() < 0.55:
            return "claw"
        return None
    prock = P("stone_grey", 4.5, rnd=rnd, fn=paint)
    mound(RK, IN, DEN, rnd, prock, label="rock")
    rmn, rmx = geo_bbox(RK)
    # two extra rocks bedded into the slope for a lumpier silhouette
    p2 = P("stone_grey", 6.0, rnd=rnd, fn=moss_patches(rnd, 0.5, -0.2, 4.0))
    extra = [crag(RK, (-11.8, 2.5, 4.0), (4.6, 5.0, 5.0), 18, rnd, p2, zmin=-0.9, boxy=0.3, label="side_rock"),
             crag(RK, (11.2, 4.0, 3.2), (3.9, 4.2, 4.2), 16, rnd, p2, zmin=-0.9, boxy=0.3, label="side_rock"),
             crag(RK, (1.5, 1.5, 12.6), (8.0, 5.0, 3.4), 18, rnd, p2, zmin=-1e9, boxy=0.35, label="top_rock")]
    # scattered bones in front of the burrow
    pb = P("bone", 1.5)
    psb = pb.but(smooth=True)

    def long_bone(c, ang, L, r):
        d = Vector((math.cos(ang), math.sin(ang), 0.0))
        a, b = Vector(c) - d * (L / 2), Vector(c) + d * (L / 2)
        cyl(BN, a, b, r, r * 0.85, 6, psb, cap0=False, cap1=False, label="bone")
        for e in (a, b):
            for side in (-1, 1):
                q = e + Vector((-d.y, d.x, 0.0)) * (side * r * 0.6)
                lathe(BN, [(0.0, -r * 1.5), (r * 1.35, -r * 0.45), (r * 1.35, r * 0.45), (0.0, r * 1.5)], 6, psb,
                      c=(q.x, q.y, q.z), label="bone")

    def rib(c, ang, L, r, bend):
        d = Vector((math.cos(ang), math.sin(ang), 0.0))
        nrm = Vector((-d.y, d.x, 0.0))
        pts = []
        for i in range(6):
            t = i / 5 - 0.5
            pts.append(Vector(c) + d * (t * L) + nrm * (bend * (0.25 - t * t)) + Vector((0, 0, 0.6 * r * (1 - 4 * t * t))))
        tube(BN, pts, [r] * 6, 4, psb, h_ref=4 * math.sqrt(2) * r, cap0=True, cap1=True, label="rib")

    zr = 0.13
    long_bone((-2.6, -9.6, zr), 0.4, 2.3, zr + 0.03)
    long_bone((2.9, -11.3, zr), 2.2, 1.9, zr)
    long_bone((5.6, -8.9, zr), -0.3, 1.6, zr - 0.02)
    rib((0.6, -9.2, 0.1), 1.2, 2.2, 0.11, 0.5)
    rib((-0.9, -12.2, 0.1), -0.6, 2.0, 0.11, -0.45)
    rib((-5.2, -10.6, 0.1), 0.9, 1.8, 0.1, 0.4)
    # skull (wolf / deer): rounded cranium + tapering snout + eye ridges, lying on the ground
    sk = Geo()      # built along local +Z (cranium -> snout), then laid down with the snout toward -Y
    lathe(sk, [(0.0, -0.55), (0.45, -0.38), (0.56, 0.0), (0.46, 0.38), (0.32, 0.62), (0.24, 1.2), (0.15, 1.55),
               (0.0, 1.62)], 6, psb, sy=0.8, label="skull")
    M = Matrix.Translation((3.6, -13.0, 0.37)) @ Matrix.Rotation(math.radians(-25.0), 4, "Z") @ \
        Matrix.Rotation(math.radians(90.0), 4, "X")
    merge(BN, sk, M)
    cols = []
    mn, mx = geo_bbox(RK)
    am, ym = DEN["a_m"], DEN["y_m"]
    for s in (-1, 1):
        cols.append([[s * (am + 13.0) / 2, 0.0, 7.0], [13.0 - am, 14.0, 14.0], 0.0])
    cols.append([[0.0, ym + 3.5, 11.5], [2 * am, 7.0, 7.0], 0.0])           # rock above the burrow
    cols.append([[0.0, (ym + 7.0 + mx[1]) / 2, 7.0], [2 * am, mx[1] - (ym + 7.0), 14.0], 0.0])
    cols.append([[0.0, ym + 7.0, 3.0], [2 * am * DEN["tunnel"][-1][1], 0.5, 6.0], 0.0])   # back plate
    for b in extra:
        cols.append(box_collider(b[0], b[1], 0.8))
    info = dict(burrow_mouth=dict(width=2 * am, height=DEN["b_m"], rim_y_at_ground=ym),
                burrow_depth_to_back_plate=7.0, rock_nominal=[26, 14, 16],
                rock_size=[round(rmx[0] - rmn[0], 2), round(rmx[1] - rmn[1], 2), round(rmx[2], 2)],
                rock_size_note="main rock alone [x, depth, height above ground]; two side rocks and the bones widen "
                               "the model footprint",
                bones="3 long bones, 3 ribs, 1 skull (cream), resting on / sunk 0.02-0.05 into the ground",
                claw_marks="claw tile on front faces left and right of the mouth",
                origin="centre of the rock footprint; the burrow opens toward -Y (downhill)")
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
    zf = coplanar_overlaps({part_name(mid, k): g for k, g in data["parts"].items() if g.ntris()})
    rec["checks"] = dict(
        coplanar_overlaps=dict(count=len(zf), examples=zf[:5]),
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
        for k, key in enumerate(TILE_PAL[t]):
            used[key + " (tile %s%s, %d tris)" % (t, "" if k == 0 else " detail", n)] = list(PAL[key])
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
    p = P("dark_stone")
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
    hum.location = (mx[0] + 2.5, mn[1] - 2.0, 0.0)          # front-right: never in front of the model in any view
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
    label_px = 32
    scale = 8.0
    for _ in range(4):        # slots are at least as wide as their label at the current scale
        layouts, maxw, sumh = [], 0.0, 0.0
        for row in rows:
            x = 3.0 + gap
            lay = [("human", 1.5, 3.0)]
            for mid in row:
                r = STATE["models"][mid]
                w = r["bbox_max"][0] - r["bbox_min"][0]
                slot = max(w, (7.2 * len(mid) + 8) / scale)
                lay.append((mid, x + (slot - w) / 2 - r["bbox_min"][0], w))
                x += slot + gap
            rowh = max(STATE["models"][m]["bbox_max"][2] + 1.0 for m in row)
            layouts.append((lay, x - gap, rowh))
            maxw = max(maxw, x - gap)
            sumh += rowh
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




def render_collider_sheet(ids, cam, hum):
    """Every model (3/4 view) with its colliders.json boxes drawn as red cages (edges of each
    box, yaw applied), 2 x 4 panels on one sheet: a visual check of the collision boxes."""
    col = new_collection("_collider_cages")
    mat = get_flat_material("_Cage", (220, 30, 30))
    cages = {}
    for mid in ids:
        g = Geo()
        for (cx, cy, cz), (sx, sy, sz), yaw in STATE["colliders"][mid]:
            R_ = Matrix.Rotation(math.radians(yaw), 3, "Z")
            hx, hy, hz = sx / 2, sy / 2, sz / 2
            cn = [Vector((cx, cy, cz)) + R_ @ Vector((ax * hx, ay * hy, az * hz))
                  for az in (-1, 1) for ay in (-1, 1) for ax in (-1, 1)]
            t = max(0.06, 0.004 * max(sx, sy, sz))
            for i in range(8):
                for j in range(i + 1, 8):
                    if bin(i ^ j).count("1") == 1:
                        a, b = cn[i], cn[j]
                        d = (b - a).normalized()
                        beam(g, a - d * t, b + d * t, 2 * t, 2 * t, P("vermilion"),
                             up=(Z if abs(d.z) < 0.9 else Vector((1.0, 0.0, 0.0))))
        ob = mesh_from_geo("_cage_" + mid, g, mat, col)
        cages[mid] = ob
    sheet = Image.new("RGB", (1024, 1024), BG)
    dr = ImageDraw.Draw(sheet)
    dr.text((12, 6), "colliders.json check: red cages = collision boxes (3/4 view from the front-right)",
            fill=(30, 30, 30), font=_font(15))
    tmp = os.path.join(REN, "_cage.png")
    for k, mid in enumerate(ids):
        for m2, ob in cages.items():
            ob.hide_render = m2 != mid
        set_visible({mid, "_collider_cages"})
        pts = [o.matrix_world @ v.co for o in bpy.data.collections[mid].objects if o.type == "MESH"
               for v in o.data.vertices] + [v.co.copy() for v in cages[mid].data.vertices]
        fit_ortho(cam, VIEWS["three_quarter"], pts, 1.05)
        render_to(tmp, (512, 488))
        x0, y0 = (k % 2) * 512, 30 + (k // 2) * 248
        im = Image.open(tmp).convert("RGB").resize((256, 244), Image.LANCZOS)
        pane = Image.new("RGB", (512, 248), BG)
        pane.paste(im, (128, 2))
        sheet.paste(pane, (x0, y0))
        dr.text((x0 + 8, y0 + 6), "%s (%d boxes)" % (mid, len(STATE["colliders"][mid])), fill=(30, 30, 30),
                font=_font(13))
    os.remove(tmp)
    p = os.path.join(REN, "colliders_check.png")
    sheet.save(p)
    col.hide_render = True
    return [os.path.relpath(p, OUT)]


# ======================================================================================
# 9. SCALE CHECK SCENE + EXPORT VERIFICATION
# ======================================================================================
SCALE_LAYOUT = dict(gate=(0.0, 0.0), cave=(84.0, 0.0), road_w=22.0, road_end_gate=-15.5, road_end_cave=CAVE["y_m"],
                    road_far=-95.0)


def _flat_rect(g, x0, x1, y0, y1, z):
    a, b, c, d = g.v((x0, y0, z)), g.v((x1, y0, z)), g.v((x1, y1, z)), g.v((x0, y1, z))
    uv = (0.5, 0.5)
    g.add_tri(a, b, c, uv, uv, uv, "x", False)
    g.add_tri(a, c, d, uv, uv, uv, "x", False)


def build_scale_scene(hum):
    """Ground, two 22-stud road strips ending at the gate steps and at the cave mouth,
    the two models and human silhouettes (renders only, never exported)."""
    col = new_collection("_scale_scene")
    L = SCALE_LAYOUT
    gx, cx = L["gate"][0], L["cave"][0]
    ground = Geo()
    _flat_rect(ground, -400.0, 500.0, -500.0, 400.0, -0.02)
    mesh_from_geo("_ground", ground, get_flat_material("_ScnGrass", (118, 142, 86)), col)
    road = Geo()
    hw = L["road_w"] / 2
    _flat_rect(road, gx - hw, gx + hw, L["road_far"], L["road_end_gate"], 0.03)
    _flat_rect(road, cx - hw, cx + hw, L["road_far"], L["road_end_cave"], 0.03)
    mesh_from_geo("_road", road, get_flat_material("_ScnPath", (154, 132, 100)), col)
    edge = Geo()
    for x0 in (gx - hw, gx + hw, cx - hw, cx + hw):
        yend = L["road_end_gate"] if abs(x0 - gx) < 20 else L["road_end_cave"]
        _flat_rect(edge, x0 - 0.25, x0 + 0.25, L["road_far"], yend, 0.05)
    mesh_from_geo("_road_edges", edge, get_flat_material("_ScnEdge", (96, 80, 58)), col)

    def put(mid, loc):
        for o in bpy.data.collections[mid].objects:
            ob = bpy.data.objects.new("_scl_" + o.name, o.data)
            ob.matrix_world = Matrix.Translation(Vector(loc))
            col.objects.link(ob)
    put("dwarf_gate", (gx, 0.0, 0.0))
    put("limestone_cave", (cx, 0.0, 0.0))
    for i, loc in enumerate(((gx - 4.0, -19.0, 0.0), (gx + 6.0, -46.0, 0.0), (gx + 2.0, -6.5, 3.0),
                             (cx - 3.0, -17.0, 0.0), (cx + 2.5, -7.0, 0.08), (cx + 7.0, -52.0, 0.0))):
        ob = bpy.data.objects.new("_scl_human%d" % i, hum.data)
        ob.location = loc
        col.objects.link(ob)
    return col


def render_scale_check(cam, hum):
    col = build_scale_scene(hum)
    set_visible({"_scale_scene"})
    sc = bpy.context.scene
    sh = sc.display.shading
    sh.use_world_space_lighting = True
    sh.studiolight_rotate_z = math.radians(-35.0)
    L = SCALE_LAYOUT
    mid_x = (L["gate"][0] + L["cave"][0]) / 2
    # perspective, 35 mm, from the front-right above the roads
    cam.data.type = "PERSP"
    cam.data.lens = 35.0
    cam.data.sensor_fit = "HORIZONTAL"
    cam.data.sensor_width = 36.0
    cam.data.clip_start = 0.5
    cam.data.clip_end = 5000.0
    cam.location = Vector((mid_x + 38.0, -205.0, 92.0))
    cam.rotation_euler = (Vector((mid_x + 4.0, -14.0, 13.0)) - cam.location).to_track_quat("-Z", "Y").to_euler()
    top = os.path.join(REN, "_scale_top.png")
    render_to(top, (1024, 536))
    # plan view (orthographic, from above): road widths against the steps and the mouth
    x0, x1, y0, y1 = -46.0, 114.0, -48.0, 18.0
    cam.data.type = "ORTHO"
    cam.data.sensor_fit = "HORIZONTAL"
    cam.data.ortho_scale = x1 - x0
    cam.location = Vector(((x0 + x1) / 2, (y0 + y1) / 2, 500.0))
    cam.rotation_euler = (0.0, 0.0, 0.0)
    cam.data.clip_end = 2000.0
    hpx = int(round(1024 * (y1 - y0) / (x1 - x0)))
    bot = os.path.join(REN, "_scale_plan.png")
    render_to(bot, (1024, hpx))
    sheet = Image.new("RGB", (1024, 1024), BG)
    dr = ImageDraw.Draw(sheet)
    dr.text((12, 6), "scale check: dwarf_gate + limestone_cave, 22-stud road strips, human silhouettes = 5 studs",
            fill=(30, 30, 30), font=_font(16))
    sheet.paste(Image.open(top).convert("RGB"), (0, 28))
    dr.text((12, 28 + 536 + 6), "plan view (orthographic, from above, %.1f px/stud): road 22 wide vs gate steps 26 wide "
                                "and cave mouth 22 wide" % (1024 / (x1 - x0)), fill=(30, 30, 30), font=_font(14))
    pv = Image.open(bot).convert("RGB")
    yp = 28 + 536 + 28
    sheet.paste(pv, (0, yp))
    pxs = 1024 / (x1 - x0)

    def to_px(x, y):
        return ((x - x0) * pxs, yp + (y1 - y) * pxs)
    for cxr, yend, lab in ((L["gate"][0], L["road_end_gate"], "road 22"), (L["cave"][0], L["road_end_cave"], "road 22")):
        a = to_px(cxr - 11.0, y0 + 6.0)
        b = to_px(cxr + 11.0, y0 + 6.0)
        dr.line([a, b], fill=(200, 30, 30), width=2)
        for q in (a, b):
            dr.line([(q[0], q[1] - 6), (q[0], q[1] + 6)], fill=(200, 30, 30), width=2)
        dr.text(((a[0] + b[0]) / 2 - 22, a[1] - 20), lab, fill=(200, 30, 30), font=_font(13))
    os.remove(top)
    os.remove(bot)
    p = os.path.join(REN, "scale_check.png")
    sheet.save(p)
    sh.use_world_space_lighting = False
    sh.studiolight_rotate_z = 0.0
    cam.data.sensor_fit = "AUTO"
    col.hide_render = True
    return [os.path.relpath(p, OUT)]


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
    table = (("dwarf_gate", gen_dwarf_gate), ("blumund_marker", gen_blumund), ("milim_marker", gen_milim),
             ("limestone_cave", gen_cave), ("limestone_rock_a", lambda: gen_rock("limestone_rock_a")),
             ("limestone_rock_b", lambda: gen_rock("limestone_rock_b")),
             ("limestone_rock_c", lambda: gen_rock("limestone_rock_c")), ("wolf_den", gen_den))
    return {mid: fn for mid, fn in table if not OPT_ONLY or mid in OPT_ONLY}


MOUTH_CLEAR = {   # space inside the openings that the rock mesh must leave empty (players walk in)
    "limestone_cave": ((-7.5, CAVE["y_m"] + 0.6, 0.3), (7.5, CAVE["y_m"] + 14.0, 9.0)),
    "wolf_den": ((-3.0, DEN["y_m"] + 0.6, 0.3), (3.0, DEN["y_m"] + 6.4, 4.5)),
}


def tunnel_crossings(parts, skip_labels):
    """Triangles of the rock pieces that intersect the inside (tunnel) skin: a boulder or a
    crag poking through the cave / burrow walls would show inside. The mound's own outer
    skin (shares the rim with the tunnel) and the stalactites (hang through the ceiling on
    purpose) are skipped."""
    ins = parts["Inside"]
    bi = _bvh([Vector(p) for p in ins.co], ins.tris)
    hits = {}
    for key, g in parts.items():
        if key == "Inside" or not g.ntris():
            continue
        for s0, e0, lab in g.piece_ranges():
            if lab in skip_labels:
                continue
            vids = sorted({i for t in g.tris[s0:e0] for i in t})
            rm = {v: k for k, v in enumerate(vids)}
            b = _bvh([Vector(g.co[i]) for i in vids], [(rm[a], rm[b_], rm[c]) for a, b_, c in g.tris[s0:e0]])
            n = len(b.overlap(bi))
            if n:
                hits["%s/%s" % (key, lab)] = hits.get("%s/%s" % (key, lab), 0) + n
    return hits


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
    t = Geo()                                       # self-test: the z-fighting scan must see a planted overlap
    block(t, (0, 0, 0), (2, 1, 1), P("gold"))
    block(t, (1, 0, 0.5), (3, 1, 2), P("gold"))
    self_test = len(coplanar_overlaps({"t": t}))
    if not self_test:
        problems.append("coplanar overlap scan self-test failed")
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
        if ck["coplanar_overlaps"]["count"]:
            problems.append("%s: %d coplanar overlapping faces (z-fighting) %s" % (
                mid, ck["coplanar_overlaps"]["count"], ck["coplanar_overlaps"]["examples"]))
        if ck["max_uv_anisotropy"] > 1.6:
            problems.append("%s: UV anisotropy %.2f" % (mid, ck["max_uv_anisotropy"]))
    for mid, box in MOUTH_CLEAR.items():
        if mid in data:
            n = box_clear({k: g for k, g in data[mid]["parts"].items() if k != "Inside" and g.ntris()}, *box)
            STATE["models"][mid]["checks"]["mouth_clear_triangles"] = n
            STATE["models"][mid]["checks"]["mouth_clear_box"] = [list(box[0]), list(box[1])]
            if n:
                problems.append("%s: %d triangles inside the walk-in space of the mouth" % (mid, n))
    for mid, skip in (("limestone_cave", ("outcrop", "stalactite")), ("wolf_den", ("rock",))):
        if mid in data:
            h = tunnel_crossings(data[mid]["parts"], skip)
            STATE["models"][mid]["checks"]["rock_through_tunnel_skin"] = h
            if h:
                problems.append("%s: rock pieces cross the tunnel skin %s" % (mid, h))
    ident = [o.name for o in bpy.data.objects if any(abs(o.matrix_world[i][j] - (1.0 if i == j else 0.0)) > 1e-6
                                                    for i in range(4) for j in range(4))]
    mods = [o.name for o in bpy.data.objects if len(o.modifiers)]
    if ident or mods:
        problems.append("unapplied transforms %s / modifiers %s" % (ident, mods))
    STATE["checks"] = dict(problems=problems, coplanar_scan_self_test_pairs=self_test,
                           objects_with_unapplied_transforms=ident, objects_with_modifiers=mods,
                           lowest_point_per_model={m: STATE["models"][m]["bbox_min"][2] for m in ids})
    for mid in ids:
        r = STATE["models"][mid]
        print("%-18s size %-24s tris %5d / %5d  facing-back %d  floating %d  aniso %.2f" % (
            mid, r["size"], r["triangles"]["total"], r["budget"], r["checks"]["facing"]["backfacing_first_hits"],
            len(r["checks"]["floating"]["floating"]), r["checks"]["max_uv_anisotropy"]))
    print("PROBLEMS:", problems)
    if problems and "--keep-going" not in _argv:
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
        rows = [[m for m in ("dwarf_gate",) if m in ids], [m for m in ("limestone_cave", "wolf_den") if m in ids],
                [m for m in ("blumund_marker", "milim_marker", "limestone_rock_a", "limestone_rock_b",
                             "limestone_rock_c") if m in ids]]
        render_lineup(os.path.join(REN, "lineup.png"), [r for r in rows if r], cam, hum,
                      "Batch D lineup - landmarks at the edges of the forest (front, orthographic)")
        renders["lineup"] = ["renders/lineup.png"]
        if "dwarf_gate" in ids and "limestone_cave" in ids:
            renders["scale_check"] = render_scale_check(cam, hum)
        renders["colliders_check"] = render_collider_sheet(ids, cam, hum)
    # ---- verify exports ----
    verify = verify_exports(ids) if OPT_VERIFY else {}
    write_reports(ids, renders, verify)
    if OPT_ZIP and not OPT_ONLY:
        make_zip()
    print("DONE")


def write_reports(ids, renders, verify):
    rep = {"batch": "D", "title": "Landmarks at the edges of the forest", "generator": "build.py",
           "blender": bpy.app.version_string,
           "units": "1 Blender unit = 1 stud; scene.unit_settings.scale_length = 0.01; Z up; front = -Y",
           "size_convention": "[x, height (Blender Z), depth (Blender Y)] in studs, from the mesh bounding box",
           "atlas": {"file": "atlas.png", "size": [ATLAS_PX, ATLAS_PX], "tile_px": TILE_PX, "inset": INSET,
                     "tiles": {k: {"col": c, "row_from_top": r, "kind": TILE_KIND[k], "palette": TILE_PAL[k],
                                   "base_rgb": [list(PAL[q]) for q in TILE_PAL[k]],
                                   "mean_rgb": [round(v, 1) for v in STATE["tile_mean"][k]]}
                               for k, (c, r) in TILES.items()}},
           "models": {}, "checks": STATE["checks"], "export_verification": verify, "renders": renders}
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
