# Batch A: trees for the Great Forest of Jura

## Revision 2: leaf-shaped cards and denser foliage

Revision 2 changes only what was asked: the leaf cards, foliage density, leaf culling in the renders, the leaf test, and a before/after render. Everything else is pinned to the batch A delivery and checked automatically (`report.json → checks.unchanged_since_batch_A`):
* far cards and burnt_tree: geometry hashes identical to batch A (their FBX files still change at byte level, because every FBX embeds the shared atlas, whose leaf tiles were repainted);
* `colliders.json`: byte-identical to batch A;
* bones: the same names and positions (computed from batch A's bounding boxes);
* atlas: the same tile layout, with the bark, charred and far tiles pixel-identical;
* names, weights and export settings: unchanged.

To make this possible, `build.py` keeps the batch A leaf code as a `rev=1` path, which reproduces batch A's geometry hashes bit for bit. It is never exported: it pins the items above and draws the "before" half of `renders/before_after_leaves.png`.

### New triangle counts

| id | Bark | Leaves | Glow | **total** | batch A total | budget | size vs batch A [x, h, z] |
|---|---|---|---|---|---|---|---|
| maple_a | 1803 | 6920 (692 momiji) | – | **8723** | 5107 | 9000 / bark 2600 | +0.2 %, +0.3 %, +0.4 % |
| maple_b | 2066 | 6700 (670 momiji) | – | **8766** | 6198 | 9000 / 2600 | −1.2 %, −0.4 %, −1.6 % |
| maple_c | 1544 | 7200 (720 momiji) | – | **8744** | 4476 | 9000 / 2600 | −1.4 %, +1.6 %, −0.7 % |
| pine_a | 2019 | 2284 (736 fans + 19 pointed) | – | **4303** | 5039 | 9000 / 2600 | −2.8 %, −0.2 %, −2.7 % |
| pine_b | 2348 | 3103 (1009 fans + 19 pointed) | – | **5451** | 6460 | 9000 / 2600 | −2.4 %, −0.2 %, −2.4 % |
| burnt_tree | 704 | – | – | **704** | 704 | 9000 / 2600 | unchanged |
| dryas_tree | 3522 | 7896 (1974 pointed) | 480 | **11898** | 11878 | 12000 | −1.6 %, −1.6 %, +0.4 % |
| far_* (6 cards) | – | – | – | **138 each** | 138 | 150 | unchanged |

Leaf mixes:
* maple_a: 84.5 % red / 15.5 % orange
* maple_b: 80.7 % orange / 11.5 % red / 7.8 % amber
* maple_c: 80 % amber / 20 % red
* dryas: 55.5 % jade / 44.5 % green

New sizes, bounding boxes and per-part counts are in `report.json` (`size`, `size_batch_A`, `size_change_pct_vs_batch_A`).

### What changed
1. **Leaf cards follow the leaf outline and are single-sided**; no square edge remains.
   * **Dryas, pine spire and pine tip**: the existing trees' pointed leaf, using your coordinates, UVs and triangles verbatim. That is 5 vertices and 4 triangles, with the midrib lifted 10 % of the length.
   * **Maples**: a 5-pointed momiji fan in geometry: 11 vertices (palm centre, base notch, 5 tips, 4 notches) and 10 triangles fanned from the palm. The palm is lifted 10 % of L and the notches 4 %, so every lobe is creased along its vein.
   * **Pine needle fans and hanging cards**: still cards, but now a tapered pentagon (5 vertices, 3 triangles, flat, one side) that narrows to a point at the tip. The back quad is gone.
   * **Atlas**: rows 0–1 were repainted so the painted leaf fills each outline, plus a 0.035 bleed so filtering never samples the background. The needle tile covers both its fan and the pointed outline. Positions, colours and the 4 % inset rule are unchanged.
2. **Saved triangles spent on density.**
   * **Leaf layout**: cards are no longer scattered. They are spread evenly over each cluster's shell (golden spiral) and laid like shingles: base up-slope, tip down-slope and slightly lifted, with random twist and tilt. A cluster now reads as one soft mass.
   * **Dryas**: 34 large clusters (radius 12.5–14):
     * apex, a ring of 7 around it, and an even ring of 11 at the widest level;
     * 5 Poisson-spaced clusters;
     * 6 under the crown and 4 in its core, so gaps show leaves rather than limbs.
     Cards that would sit inside a neighbouring cluster, or deep behind the crown's skin, are skipped, and their budget goes to the visible skin. Twigs inside the now-dense clusters are single segments.
   * **Maples**: the tiered pads are kept, but each pad is 2.3× thicker. Pads grow downward so the tree keeps its height, which makes each pad a soft, rounded cloud. Each maple is filled to about 8.75 k of its 9 k budget.
3. **Renders**: leaves are now drawn from both sides, as with `DoubleSided` in game, and bark keeps back-face culling ON. The **ray test now runs on bark (+ fruit) only**: 0 back-facing hits on every tree. Leaves get the new leaf test below.
4. **`renders/before_after_leaves.png`** shows maple_b and the dryas from the same 3/4 camera, batch A on the left and revision 2 on the right. The batch A trees are rebuilt by the `rev=1` path with the batch A atlas.

### Density results
* **Dryas leaf coverage of the crown silhouette, side views from 100 studs** (`renders/dryas_tree_leaf_coverage.png`):

  | azimuth | leaves | bark visible | sky |
  |---|---|---|---|
  | 270° | 86.5 % | 4.8 % | 8.7 % |
  | 0° | 87.9 % | 3.1 % | 9.0 % |
  | 90° | 86.2 % | 5.8 % | 8.0 % |
  | 180° | 87.4 % | 4.2 % | 8.4 % |

  The mean is **87.0 %**; batch A scores 76.4–81.1 % (mean 79.1 %) by the same method. Limbs show only in small gaps.
  * Method: one ray per pixel (480², about 0.3 studs/px) from a camera 100 studs from the trunk axis, at crown height.
  * Crown silhouette: the leaf mask, closed with an 8-stud disk, with holes filled.
  * Coverage: the share of that silhouette whose first hit is a leaf, from either side.
* **Maple pads**: each pad is tested on its own leaves only, with rays from above and from 4 sides through 85 % of the pad's ellipse.
  * From above: maple_a 95.4 %, maple_b 88.2 %, maple_c 97.6 %.
  * From the side: maple_a 95.7 %, maple_b 90.8 %, maple_c 96.8 %.
  * maple_b is lowest because it spreads the same budget over the most pads (27).

### Leaf test (replaces the ray test for leaves; `report.json → <id>.leaf_check`)
For every card:
* no degenerate triangle;
* all of its triangles wound to the same side;
* the midrib (or palm) lifted at least 8 % of L toward the outside.

For every leaf triangle, 4 UV samples must fall inside the painted leaf outline of its tile, so the tile background, which is the old square card edge, can never show.

Result: **0 failures** on all five leafy trees and the dryas (e.g. dryas: 1974 cards, 31 584 UV samples, 0 outside).

### Decisions taken in revision 2
* **R1. Far-card colours were kept at their batch A values, as instructed.** Note for the art lead: the new tiles show leaf colour edge to edge, so the trees now read brighter than those values. The averages of the new tiles inside the leaf outlines would be:

  | far tile | current | new-tile average | gap |
  |---|---|---|---|
  | Jura crown | (66, 105, 60) | (74, 116, 68) | ~11 % darker now |
  | maple crown | (184, 96, 38) | (214, 113, 48) | ~14 % darker now |
  | pine crown | (35, 65, 46) | (36, 67, 48) | negligible |

  The far maple may visibly pop darker at the LOD swap. Updating is a one-line change in `build.py` (`far_rgb`), if you want it.
* **R2. Dryas leaf size**: 5.4–6.6 studs long, half-width 0.40·L. This is a little above the ~5.5 guideline, but it was needed to reach ~85 % coverage with 12 k triangles on a 120-stud crown. The leaf shape is the existing kite unchanged; only its `width` parameter is set.
* **R3. Momiji uses the maximum 11 vertices / 10 triangles.** A readable 5-point silhouette needs 5 tips, 4 notches and the base notch, and the lifted palm adds the 11th vertex.
* **R4. Pine density was not raised.** The pine shapes were approved as they were, so the needle fans only changed outline. The pines therefore use fewer triangles than before.
* **R5. Leaf winding follows your code**: faces wind away from the lifted midrib side. The meshes are single-sided and rely on `DoubleSided` in game.
* **R6. Leaves shown double-sided in renders.** Workbench ignores per-material back-face culling in final renders. The renders therefore add a flipped copy of each leaf mesh, which is never exported or saved. This shows leaves from both sides while bark stays culled.
* **R7. Sizes within ±3 % of batch A.**
  * Maples: pad radii trimmed per tree (×0.95 / 0.97 / 0.97); all axes are within 1.6 %.
  * Pines: 2.4–2.8 % narrower, because a pointed tip reaches less far out than a square card's corners.
  * The far cards keep batch A's exact boxes. The full trees therefore differ from their far cards by the same small amounts (maple_b 0.4–1.6 %, pine_b 0.2–2.4 %).
* **R8. Spirit fruit** hang just below the now-dense cluster bottoms (0.5–2.0 studs from the nearest leaf vertex), so they stay visible.
* **R9. Stand-ins of the 4 existing trees** in the far-card comparison renders now use the existing pointed leaf card.

### Reproducibility (revision 2)
Two runs from a clean scene give **93 of 94 files byte-identical**: atlas, all FBX, GLB, both JSON files and all 76 renders. The `.blend` differs only by Blender's runtime memory addresses; its content hash is identical (`848dac3f…`). A full run takes about 2 minutes. The delivery is zipped as `jura_batch_A2.zip`. The batch A `jura_batch_A.zip` is left untouched as the previous delivery.

---

*Everything below documents the original batch A delivery. Where it differs from Revision 2 above, Revision 2 is current. The sections affected are: the leaf card description, decision 1 (two-sided cards), the leaf part of the ray test, the "What was built" triangle table, atlas rows 0–1 and the dryas density note.*

## Batch A — original delivery

Everything in this folder is produced by **one script, `build.py`**. It paints the atlas, builds and skins the 13 models, runs the self-checks, exports FBX/GLB/.blend, renders the review images, re-imports the exports to verify them, and writes `report.json` and `colliders.json`. A full run takes about 2 minutes on 4 CPU cores (batch A: 80 s). Two clean runs produce byte-identical files (see *Reproducibility*).

```
blender -b -P build.py              # Blender 4.x/5.x; Pillow must be importable from Blender's Python
python3 build.py                    # or with the bpy module:  pip install bpy==5.2.3 pillow  (Python 3.13)
options: --no-render  --no-verify  --no-zip
```

This batch was built and verified with **bpy 5.2.3 LTS** (the official Blender-as-a-module wheel), because download.blender.org was not reachable from the build machine. Renders use **Workbench**: studio light, back-face culling **ON** for bark (leaves are drawn from both sides since revision 2), light grey background, 16× AA. Eevee needs a GPU; software GL was about 60× slower.

## Deliverables

| file | content |
|---|---|
| `build.py` | the generator (all models, atlas, checks, exports, renders, reports, zip) |
| `atlas.png` | 1024×1024, 4×4 tiles of 256 px (layout below) |
| `jura_batch_A.blend` | all 13 collections, saved right after the build (atlas linked as `//atlas.png`) |
| `<id>.fbx` ×13 | one per model; trees include their armature; atlas embedded |
| `jura_batch_A.glb` | all models, skinned trees included |
| `colliders.json` | boxes per model (format below) |
| `report.json` | per model: size, triangles per part, bones, colours, wind test, checks, export round-trip |
| `renders/` | 5 views per model (65), `lineup.png`, `forest_lineup.png`, 6 × `far_compare_*_1500.png`, `dryas_tree_arch_walkthrough.png`; revision 2 adds `before_after_leaves.png` and `dryas_tree_leaf_coverage.png` |

## What was built (batch A numbers; current counts are in Revision 2)

Sizes come from the mesh bounding box as `[x, height, depth]` in studs. They include the 0.8 stud (dryas: 1.0) sink below z = 0, so the visible height above ground is 0.8 less.

| id | size [x, h, z] | triangles | budget | notes |
|---|---|---|---|---|
| maple_a | 45.24 × 38.57 × 39.90 | Bark 1959 + Leaves 3148 = **5107** | 9000 / bark 2600 | 3 tiers, 3 leaders, 84 % red / 16 % orange cards |
| maple_b | 51.26 × 41.43 × 48.31 | Bark 2250 + Leaves 3948 = **6198** | 9000 / 2600 | 3 tiers, 2 leaders, 79 % orange / 12 % red / 9 % amber |
| maple_c | 43.99 × 35.64 × 41.35 | Bark 1700 + Leaves 2776 = **4476** | 9000 / 2600 | 2 tiers, 3 leaders, 83 % amber / 17 % red |
| pine_a | 23.33 × 60.90 × 22.11 | Bark 2019 + Leaves 3020 = **5039** | 9000 / 2600 | 8 drooping tiers, bare trunk 24 % of height |
| pine_b | 27.44 × 80.90 × 27.08 | Bark 2348 + Leaves 4112 = **6460** | 9000 / 2600 | 9 drooping tiers, bare trunk 23.5 % of height |
| burnt_tree | 14.50 × 30.72 × 17.04 | Bark **704** | 9000 / 2600 | broken splintered top, 4 twisted branches + 2 offshoots, no leaves |
| dryas_tree | 119.97 × 109.79 × 117.95 | Bark 4434 + Leaves 6964 + Glow 480 = **11878** | 12000 | 3 limbs, 7 roots (4 walk-under arches), 39 clusters, 10 spirit fruit |
| far_jura_broad | 54 × 56 × 55 | Card **138** | 150 | exact size of the existing tree |
| far_jura_tall | 36 × 75 × 33 | Card **138** | 150 | 〃 |
| far_jura_young | 35 × 37 × 34 | Card **138** | 150 | 〃 |
| far_jura_ancient | 72 × 65 × 73 | Card **138** | 150 | 〃 |
| far_maple | 51.26 × 41.43 × 48.31 | Card **138** | 150 | exact bbox (min and max) of maple_b |
| far_pine | 27.44 × 80.90 × 27.08 | Card **138** | 150 | exact bbox (min and max) of pine_b |

Every budget holds, with no exceptions.

### Trees (shared construction)
* **Trunks and branches** are tapered tubes with 7–10 sides (trunks 9–10, limbs 8–9, twigs 7), smooth-shaded. Trunks have 4–7 buttress lobes that flare into the ground and sink 0.8 studs (dryas: 1.0) below z = 0. Their bottom is capped and their ground rings are kept level.
* **Branching** follows a pipe-model rule (radius ∝ clusters carried^(1/2.4)). The trunk splits right at its top into leaders or limbs, each growing toward one azimuth sector of leaf clusters and forking again until every cluster has a twig ending inside it. Forks are closed with short cones, so there are no open tube ends.
* **Leaf cards** *(batch A; replaced in revision 2 by single-sided, leaf-shaped cards laid as shingles)* were 3.5–5.5 stud cards (dryas 4.8–5.9) grouped in loose clusters around the twig ends, biased toward each cluster's outer shell. The silhouette came from the tile painting (opaque, card edges visible as allowed). The cards were **two-sided by geometry** (decision 1).

### Model notes
* **Maples (momiji)**: a slender `bark_dark` trunk forks at about 0.21 H (8.4–9.2 studs) into 2–3 visible leaders. The crown is made of 2–3 horizontal tiers of wide, flat leaf pads (cards mostly face up). Pad edges and card tips droop at the outer rim, and gaps between tiers give the layered look.
* **Pines**: straight `bark` trunk; tiers are placed by the golden angle, with 5–7 drooping branches per tier. Each branch carries needle-card fans: 4 cards per station, fanned ±24°/±62° and pitched down more toward the tip, plus a hanging card. The `pine_needle` tile is dark at the fan root, so each tier is darker inside. The first tier sits where its drooping tips stop at about 21 % of the height, so the trunk is bare over the bottom 23–24 %. The top leader is a branch, not trunk (decision 6), carrying a conical spire.
* **burnt_tree**: a gnarled leaning trunk (base Ø 4.6, strong 4-lobe flare). The broken top is a ring of 9 splinters (the tallest reaches 30 studs) around a charred hollow. There are 4 bare twisted branches (one ends blunt, broken) and 2 offshoots. The trunk uses the cracked part of the `charred` tile (long vertical ember cracks); branches use its plain part. All weights are on Root, and the armature still has Wind1–4 (no weights). The wind test moves it by 0.0, as expected.
* **dryas_tree**: trunk Ø 16 at 5 studs height, flaring to Ø 18 at ground level (up to 24 across the lobes of the ground roots), splitting at 36 studs into 3 limbs. The crown is 39 large clusters (r ≈ 9–11) in `leaf_jade` (54 %) and `leaf_green` (46 %), on a dome reaching ±60 studs and 109 studs high. An even ring of 11 clusters at the widest level guarantees the ~120 width.
  * **Roots**: 7 roots, alternating two kinds:
    * **4 arch roots** leave the trunk at 15 studs, arch over and land about 40 studs out. Their underside is ≥ 7 studs from the trunk surface out to r ≈ 27–29.5, a walkable span of 17.8–20.1 studs on all four (`report.json → dryas_tree.info.arch_clearance`). `renders/dryas_tree_arch_walkthrough.png` shows a 5-stud human standing under one, with 12.2 studs above them.
    * **3 thick ground roots** hug and hump along the ground out to 45 studs.
  * **Spirit fruit**: 10 low-poly spheres (48 tris each, r = 1.5), one per 36° azimuth bin, hang under the lowest outer clusters. They form a separate mesh `dryas_tree__Glow` with its own material `Glow`: base and emission (120, 230, 235), emission strength 1.5, not in the atlas. Each sits 0.9–3.3 studs from the nearest leaf vertex, so none floats.
  * **Colliders**: trunk plus the 3 thick roots (decision 8).

### Far-canopy LOD cards
Each card is one mesh (`<id>__Card`) with no armature and 138 triangles:
* **Crown**: a closed, faceted, lobed shell. 7 rings × 9 vertices plus a bottom point and an apex; for the pine, 9 rings × 7 forming stacked overhanging cones.
* **Trunk stub**: 6 sides, open-ended, with its top inside the crown and its bottom at ground level.

The crown shell is fitted to the exact target bbox and the trunk stub stands on the origin. Colours are flat atlas tiles: crown = average of the source tree's leaf tiles, trunk = `bark` (see the atlas table). The ring profiles mimic each species:
* broad/young: round, lumpy
* tall: egg
* ancient: wide and lumpy
* maple: wide bottom tier, waist, upper tier, dome
* pine: 5 zigzag tiers with dark undersides

Why a shell rather than literal crossed quads is explained in decision 3. The `far_compare_*_1500.png` renders put each card next to its full tree, seen from 1500 studs (vertical fov 70, eye level, 45° azimuth, world-space light) against hazy mountains, with a same-camera zoom inset.

## Atlas tile layout (`atlas.png`, 4×4 × 256 px, UV inset 4 % per tile)

Rows are counted from the top of the PNG. A face's UVs always stay inside one tile's 4 % inset; build.py checks every face of every mesh and aborts otherwise.

| row \ col | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| **0** leaf (rev 2: fills the pointed kite outline; darker L/R edges, light midrib, side veins) | leaf green (88,132,57) | leaf dark (50,88,52) | leaf yellow-green (119,152,73) | leaf jade (64,138,108) |
| **1** maple leaf (rev 2: fills the 5-point momiji outline) / needle fan (rev 2: fills the tapered fan + pointed outline) | maple red (196,58,40) | maple orange (226,118,44) | maple amber (232,170,64) | pine needle (46,84,60) fan, dark at the root |
| **2** bark (vertical grooves) | bark (92,65,43) | bark light (109,77,47) | bark dark (79,58,40) | charred (44,37,32) + ember cracks (196,84,32) in u 0–0.62; plain in u 0.66–1 |
| **3** flat (±3 % noise) | far crown Jura (66,105,60) | far crown maple (184,96,38) | far crown pine (35,65,46) | far bark = bark (92,65,43) |

Tile use:
* leaf green / leaf jade → dryas
* maple tiles → maples
* pine needle → pines
* bark → pines, dryas trunk and limbs
* bark light → dryas roots
* bark dark → maples
* charred → burnt tree
* row 3 → far cards

`leaf dark` and `leaf yellow-green` are painted only so the Jura far-card colour can be averaged (decision 2).

Far-card colours are the sRGB mean of the tile pixels inside the inset:
* **Jura**: equal mix of the 4 row-0 tiles.
* **Maple**: maple_b's real card mix (776 orange / 119 red / 92 amber).
* **Pine**: the pine needle tile.

## Conventions used (for the importer)
* **Units and axes**: 1 Blender unit = 1 stud, with `scene.unit_settings.scale_length = 0.01`. Z is up and the front faces −Y. Each origin is the centre of the base at z = 0.
* **Transforms**: all objects have identity transforms, and the only modifier is Armature (both checked).
* **Collections and names**: each model is one collection named by its id.
  * Tree meshes: `<id>__Bark`, `<id>__Leaves`, `<id>__Glow`.
  * Armature object: `<id>`.
  * Bones: `<id>_Root` (head 0,0,0, tail up) and `<id>_Wind1..4`. Wind bone heads sit at 0.54 × height, offset 8 % of the crown width toward their quadrant (45°/135°/225°/315°). They point straight up with roll 0, so bone-local X = world X.
* **Wind quadrants**: azimuth = atan2(y, x) from +X toward +Y. Wind1 = 0–90°, Wind2 = 90–180°, Wind3 = 180–270°, Wind4 = 270–360°.
* **Weights**:
  * trunk (incl. roots): Root 1.0
  * leaf cards and fruit: Root 0.08 + their cluster's wind bone 0.92
  * branch tubes: fade from their start weights to Root 0.12 + wind bone 0.88 at the tip (decision 5)
* **Wind test** (Wind1 rotated 0.05 rad on X, leaf mesh max move): maple_a 0.79, maple_b 1.07, maple_c 0.82, pine_a 1.04, pine_b 1.39, dryas 2.54 (fruit 0.89), burnt 0.0 (no leaves, all on Root). Wind2–4 were tested too and each moves its quadrant (`report.json → wind_test_max_move_studs`).
* **FBX export**: per model with `object_types={'MESH','ARMATURE'}`, `apply_unit_scale=True`, `apply_scale_options='FBX_SCALE_UNITS'`, `axis_forward='-Z'`, `axis_up='Y'`, `add_leaf_bones=False`, `use_armature_deform_only=True` (all 5 bones are deform bones), `bake_anim=False`, atlas embedded. Re-imported into a scene with the same unit scale, every FBX gives back the same sizes in studs, triangle counts, bones and vertex groups, with 0 n-gons (`report.json → export_verification.<id>.ok = true` for all 13).
* **GLB**: all models with skins and an embedded atlas. glTF ignores the unit scale, so 1 m in the GLB = 1 stud.
* **`colliders.json`**: `{"<id>": [[[cx,cy,cz],[sx,sy,sz],yawDeg], ...]}` in Blender coordinates (Z up, studs). Box centre and full size; yaw is the rotation about +Z, counter-clockwise from +X, of the box's local X (length) axis.
  * Trees: one trunk box from the ground to the fork, or for pines to the lowest needles.
  * Dryas: trunk + 3 root boxes.
  * Far cards: `[]` (no collision; they are only shown 900–3000 studs away).

## Decisions taken (no questions possible)
1. **Two-sided leaf cards** *(batch A; superseded in revision 2: the game renders leaves `DoubleSided`, so cards are now single-sided)*. Each card is two back-to-back quads (4 triangles) 0.024 studs apart, so foliage renders with back-face culling ON. That matters both in the review renders and in Roblox when `DoubleSided` is off, including for players looking up from under a crown such as the dryas. The cost is twice the triangles per card, and it is counted in every budget. If the importer turns `DoubleSided` on, the back quads are redundant but harmless (the offset prevents z-fighting).
2. **The existing trees and their atlas were not provided.**
   * Their far cards are built to the stated sizes, with z from exactly 0 to H (no sink, since theirs is unknown).
   * The crown colour is the mean of the four palette leaf colours (green, dark, yellow-green, jade) painted in this atlas. This assumes their "8 green tiles" are variants of these four.
   * For the 1500-stud comparisons I generated **stand-ins** of the 4 existing trees with this batch's own generator, stretched to the stated sizes. The stand-ins exist only in those renders (labelled "stand-in" in the image) and are not exported or saved.
3. **Far cards use a closed faceted shell instead of literal crossed quads.** With 138 triangles a shell has no gaps from any side or elevation. Real normals give it lit tops and dark undersides under the game's sun, so it never reads as flat cardboard at 45°. Its lobed rings, rotated per ring, keep a broken outline. Crossed planes at this budget either show gaps or need two-sided faces that halve the detail.
4. **Far card name `<id>__Card`**: the tree-style double underscore, since these are tree LODs; one mesh holds both crown and trunk through the atlas.
5. **Branch weight fade is hierarchical.** Branches that start on the trunk (pine branches, dryas limbs, maple leaders) fade exactly from Root 1.0 to Root 0.12 / wind 0.88. Sub-branches start with their parent's weights at the attachment point, then fade to their own tip weights. This keeps joints closed while the wind bones move; restarting every sub-branch at Root 1.0 would tear the forks open. Each twig's bone is the bone of the cluster it carries, so leaves and twigs move together.
6. **Pine top leader is a branch, not trunk.** The trunk stays Root 1.0 as specified up to 0.8 H. The thin top leader and the uppermost tiers fade to the spire's wind bone. Otherwise the swaying spire cards would slide off a rigid trunk tip.
7. **Spirit fruit are skinned** like leaves (Root 0.08 + the cluster's wind bone 0.92), so they sway with the cluster they hang from.
8. **Dryas root roles.** The arch roots stay free of colliders so players can walk under them. The colliders go on the 3 thick ground roots, which are also the thickest (base r 3.7 against 2.9 for the arches).
9. **Leaf card sizes**: 3.5–5.5 studs; maples 3.7–4.8 (square maple tile); pines 3.5–5.0 (×0.8 on the upper tiers); dryas 4.8–5.9 for its 110-stud scale.
10. **Wind bone head height** is 0.54 × the bbox top (height above z = 0).
11. **"Left" view** = camera on −X looking toward +X (Blender's Left view); "right" = from +X. The 3/4 view is from the front-right, 38° above.
12. **Burnt tree charred tile.** Ember cracks are painted only in the left 62 % of the tile, which the trunk uses (one tile height spans the whole trunk, so cracks run long and vertical). Branches use the plain right part.

## Self-review: automated checks (all pass, written to `report.json → checks`)
* **Budgets**: all within limits.
* **UVs**: every face sits in one tile, inside the 4 % inset (enforced; the build aborts otherwise).
* **Transforms and modifiers**: no unapplied transforms; no modifier other than Armature.
* **Sink**: lowest point between −1 and 0 for every model (trees −0.8, dryas −1.0, existing far cards 0).
* **Inverted normals / holes ("back-face culling" ray test)** *(batch A version; in revision 2 it runs on bark + fruit only, and leaves get the leaf test)*: 4000 rays per tree from outside; the first face hit must face the ray. Result: **0** back-facing hits on all trees. Hits on the 0.024-stud rim of a two-sided card (17–53 per tree) are counted separately, as they are grazing-angle artefacts of the card offset.
* **Arch clearance**: ≥ 7 studs under all 4 dryas arch roots over 17.8–20.1 radial studs.
* **Pine bare trunk**: 24.1 % and 23.5 % of the height.
* **Fruit attachment**: every fruit is within 3.4 studs of a leaf vertex.
* **Export round-trip**: all 13 FBX files pass (names, bones, vertex groups, triangles, size); the GLB contains all 20 meshes and 7 armatures.

## Problems found during self-review and fixed
* **Budgets.** pine_b bark was 2657/2600: branches use 3 segments only when longer than 9 studs. The dryas total was 13 157/12 000: twigs were capped at 7 sides and 2 segments, internal branches at 9 sides and 3 segments, root profiles thinned, and leaf density trimmed.
* **The maples' low fork was invisible**, and the dryas trunk read as 50 studs tall. The grower first extended one trunk-continuation branch toward all clusters and only then split. A `fork()` step now splits right at the trunk top into 2–3 leaders or 3 limbs.
* **Dryas** was 103 wide × 103 tall at first, then its width drifted with random sampling. An apex cluster and an even equator ring now give 120 × 110 × 118. A swelling term left a visible collar on the trunk and was removed. The trunk tip cone was lengthened and the limbs start lower inside the trunk, to soften the crotch "lip".
* **Pines** looked like stacked pancakes with a column spire. Fixed with stronger droop (tip drop 0.5–0.6 × length), cards pitched 28–90° down (steeper toward the tips and the fan sides), hanging cards, tiers spaced closer toward the top, and a conical spire. Later the lowest needles reached down to 13.5 % of the height, against the 20 % bare-trunk requirement. The first tier is now placed from its expected droop, and this is checked.
* **Burnt tree** was spindly with unreadable ember cracks. The trunk was thickened (Ø 4.6), branches lengthened and twisted more, and the cracks painted 2× wider with a stronger glow. The cracks then repeated in mirrored bands with a UV seam at the splinters: the trunk now spans one tile height and the splinters continue its V.
* **Burnt tree sank 1.1 studs** (limit 1): its tilted bottom ring dipped. Trunk ground rings are now forced level, and the check is automated.
* **maple_c** stood 33.7 studs above ground (range 34–46): raised to 34.8.
* **Maple colours**: amber read khaki because the card background was 70 % of the leaf colour; maple tile backgrounds are now 80 %.
* **Far cards**: the trunk stub was off the origin after the bbox fit, so the shell is now fitted on its own and the stub placed on the origin. The maple card looked like a pagoda and the broadleaf cards like smooth eggs; a 7th ring, alternating radii and stronger lobes fixed both.
* **First ray test** reported about 2 % "back-facing hits". Analysis showed they were grazing rays slipping between the two quads of a card, on bark 0 of 20 000 rays. The test now uses an angle-aware rim tolerance, and the remaining count is 0.
* **Render layout**:
  * The comparison camera first sat above the trees (seen against the ground, not the mountains). It is now at eye level with world-space light, and the zoom inset no longer covers the trees.
  * Lineup labels overlapped or were cut off: the layout was changed and labels clamped.
  * 3/4 frames were loose: they are now fitted to the real vertices.
  * The arch walkthrough camera first looked through a ground root: it was re-placed.

## Reproducibility
`build.py` always starts from `read_factory_settings(use_empty=True)`, and every model uses its own seeded RNG. In batch A I ran it twice from clean and compared every output byte for byte: **all 91 files in the folder except the .blend were identical** — atlas, all FBX, GLB, JSON and all renders.

Two sources of noise had to be removed:
* **FBX exporter**: it derives node UIDs from Python's per-process randomised `hash()` and writes `datetime.now()` into the header. build.py patches both at runtime (stable SHA-1 UIDs, fixed date); the installed add-on is untouched.
* **PNG metadata**: Blender writes date stamps into the PNG files, so build.py re-saves them without metadata.

The `.blend` still differs at byte level, because Blender writes runtime memory addresses into it. Re-opening both files and hashing every collection, object, matrix, vertex, UV, weight, modifier and bone gives identical content (`a2cb0870…`).

## Notes and limitations
* **Lighting**: Workbench's studio light follows the camera, which flatters two-sided cards (the side facing the camera is always lit). The 1500-stud comparisons therefore use world-space light, which is closer to Roblox. Seen that way, the far cards look slightly darker than the full trees, because their undersides are in shadow. This is expected, and I kept the requested "average of the leaf tiles" colour rather than tuning it.
* **Dryas density** *(batch A; fixed in revision 2, see Density results)*: the 12 000-triangle budget with two-sided cards capped the crown at about 1740 cards for a 120-stud crown. It reads airy, with visible limbs, from near and above. That fits "broken, airy outline" and the landmark silhouette from far away, but it is less dense than the smaller trees.
* **Wind-test magnitudes**: 0.05 rad gives up to 2.5 studs of movement at the dryas crown edge, because the crown extends ~55 studs above the bone heads. The game should scale wind amplitude by tree size if needed.
