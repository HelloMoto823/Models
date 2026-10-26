# Batch C: the burned Ogre village and the Orc army's trail

Everything in this folder comes from **one script, `build.py`**. It does all of the following:
* paints the atlas
* builds the 8 props
* runs the self-checks, and stops the build if any of them fails
* exports FBX, GLB and `.blend`
* renders the review images (5 views per model, the lineup and the village scene)
* re-imports every export to verify it
* writes `report.json` and `colliders.json`, and zips the folder

A full run takes about 60 s on 4 CPU cores. Two clean runs produce byte-identical files (see *Reproducibility*).

```
blender -b -P build.py              # Blender 4.x/5.x; Pillow must be importable from Blender's Python
python3 build.py                    # or with the bpy module:  pip install bpy==5.2.3 pillow  (Python 3.13)
options: --no-render  --no-verify  --no-zip  (--only=<id> to build a subset while developing)
```

Toolchain:
* Built and verified with **bpy 5.2.3 LTS** (the official Blender-as-a-module wheel), the same as batches A and B.
* Headless rendering needed the Mesa EGL libraries (`apt-get install libegl1 libegl-mesa0 libgl1-mesa-dri`).
* Renders use **Workbench**: studio light, back-face culling **ON**, light grey background, 16× AA.

## Deliverables

| file | content |
|---|---|
| `build.py` | the generator (atlas, models, checks, exports, renders, reports, zip) |
| `atlas.png` | 1024×1024, 4×4 tiles of 256 px (layout below) |
| `jura_batch_C.blend` | the 8 model collections, saved right after the build (atlas linked as `//atlas.png`) |
| `<id>.fbx` ×8 | one per model, atlas embedded |
| `jura_batch_C.glb` | all 8 models (33 meshes) |
| `colliders.json` | boxes per model (format below) |
| `report.json` | per model: size, bbox, triangles per mesh, budget, colours, collider count, info (incl. smoke points), every check result, geometry hashes; plus the atlas table, export verification and the village layout |
| `renders/` | 40 model views (front, back, left, right, three_quarter), `lineup.png`, `village_scene.png`, plus the extra `village_scene_yard.png` (eye level through the torii) |

## What was built

Sizes are `[x, height, depth]` in studs from the mesh bounding box. They include the part sunk below z = 0
(footing stones sink 0.2; the deepest points are listed per model). No model has bones (no trees in this batch).

| id | size | triangles | budget | mesh parts |
|---|---|---|---|---|
| oni_house_a | 24.68 × 15.94 × 21.41 | **3 930** | 4 000 | `_Frame` 752, `_Walls` 1000, `_Floor` 360, `_Roof` 908, `_Stone` 212, `_Debris` 592, `_Embers` 106 |
| oni_house_b | 20.00 × 13.73 × 16.52 | **2 208** | 4 000 | `_Frame` 728, `_Walls` 348, `_Floor` 316, `_Stone` 218, `_Debris` 412, `_Embers` 186 |
| oni_house_c | 28.00 × 15.91 × 18.53 | **3 584** | 4 000 | `_Frame` 764, `_Walls` 932, `_Floor` 304, `_Roof` 912, `_Stone` 228, `_Debris` 328, `_Embers` 116 |
| stake_fence | 24.00 × 7.95 × 4.98 | **826** | 900 | `_Stakes` 618, `_Rails` 208 |
| battle_debris_a | 8.07 × 7.88 × 8.69 | **1 173** | 1 200 | `_Wood` 801, `_Iron` 104, `_Cloth` 268 |
| battle_debris_b | 7.57 × 4.66 × 8.68 | **1 197** | 1 200 | `_Wood` 825, `_Iron` 104, `_Cloth` 268 |
| torii_broken | 24.74 × 19.48 × 24.97 | **1 436** | 1 500 | `_Pillars` 568, `_Fallen` 670, `_Stone` 124, `_Embers` 74 |
| trampled_log | 30.47 × 4.20 × 5.83 | **642** | 900 | `_Bark` 642 |

Every budget holds. Every mesh is triangles only, with UV map `UVMap` and the single material `Atlas`.

### The three houses (shared minka construction)
All three are built by one shared builder, so they read as one village:
* **Raised wooden floor on posts**: square posts (0.8) stand on faceted footing stones; sills under the walls; floor planks with the top at **z = 1.6**. A step stone (top ≈ 0.8) sits in front of each entrance, so every rise is under 1 stud.
* **Walls**: vertical plank walls (0.36 thick) on the post lines, from 1.25 (inside the floor edge) up to the eave beams.
* **Frame**: eave beams (keta) at z 9.0–9.8 and tie beams across the house (z 9.05–9.75). The beams are 0.9 wide, wider than the posts, and every beam end runs 0.1 past the post faces, so no faces are coplanar (no z-fighting).
* **Thatch roofs (kayabuki)**: 1.25-thick slabs. Each slab drops *vertically* to its underside, so slabs meeting at a ridge or a hip close flush with no gap and no overlap. The underside is sooty (`char`).
* **How the burning reads**:
  * Charred wood shows the alligator-cracked `char` tile. The worst spots (broken board ends, hole rims, splinters) use `char_ember`, whose cracks glow ember orange. Unburned wood keeps the bark / plank colours.
  * The charred tiles have their board seams in the same places as the plank tiles. A charred board therefore continues the unburned board below it across the jagged burn line.
  * Broken posts and beams end in charred splinter crowns with glowing cracks inside.
* **Remnants on the floor**:
  * a few straw remnants (thick ragged thatch clumps, partly charred)
  * fallen rafters
  * smouldering spots: a flat grey ash mound with glowing coals (`<id>_Embers`)
* **Smoke points**: the smouldering spots are listed in `report.json → models.<id>.info.smoke_points`, so the game can attach smoke particles there.

| | footprint | height above ground | roof | interior clearance (checked box, empty) |
|---|---|---|---|---|
| **oni_house_a** | 22 × 13 | 15.74 | gable thatch, 32° | x −10.3…−1.4, y −6…6, z 1.7…8.95 (beams at 9.0) |
| **oni_house_b** | 18 × 12 | 13.16 (central pillar) | none left | x −8.5…−3.75, y −5.5…5.5, z 1.7…8.95 |
| **oni_house_c** | 24 × 14 | 15.71 | hipped thatch, 30° | x −11.5…3.4, y −6.5…6.5, z 1.7…8.95 |

**oni_house_a: roof caved in on one side, two walls standing.**
* **Walls**:
  * The **left gable wall** stands, charred from z ≈ 8 up into the gable.
  * The **back wall** stands at full height on its left two thirds. To the right its boards break off lower and lower (broken board ends with ember spots).
  * The front and right walls burned down to low jagged stubs. The front has a 3.2-wide doorway gap between two posts, with the step stone in front.
* **Remaining roof**: the left part of both thatch slopes still stands on the gable wall, the back wall and the posts. It has a jagged, charred, ember-edged break toward the right, a small burned hole, and charred rafters sticking out of the break. The ridge beam is snapped at x ≈ 0.9.
* **The right side caved in**:
  * one thatch chunk slid inside and lies tilted from the floor onto the back-wall stub
  * another slid out over the front stubs into the mud
  * the snapped ridge beam lies diagonally from the floor up onto a broken back post
  * a fallen rafter lies on the floor
  * the floor there is charred and burned through in two places
* **Walkable interior**: the left part stays walkable (7.4 studs from the floor to the beams).

**oni_house_b: only the post frame and part of the floor are left.**
* **Posts**: 6 full charred posts, 6 broken posts with splinter tops, and the central pillar (daikoku-bashira, 1.1 thick, 12.2 tall, broken top).
* **Beams still up**: the left tie beam, pieces of both eave beams, and a tie beam on the central pillar.
* **Floor**: only the left half is left, with a jagged burned edge and one hole. On the right the joists and sills are exposed; one joist and one sill have dropped into the mud.
* **Roof beams fallen inside**:
  * the ridge beam, lying from the ground onto a broken back post
  * a sasu rafter leaning on the central pillar
  * a second rafter lying across from the floor edge to the ground
* **Other wreckage**: the front eave beam leans from the mud onto a broken front post. Low charred board stubs show where the left and back walls stood.

**oni_house_c: mostly intact walls, roof burned through with a hole, sliding door broken.**
* **Walls**: all four stand, with a soot band under the eaves.
  * The doorway (6.2 wide, 7.1 high) and the barred window are scorched, with ember cracks over them.
  * The right wall has a small burned-through hole near the top.
* **Roof**: the hipped thatch roof has a ragged hole burned through the front-right slope. The hole has a glowing rim, a charred ring, and a smoke stain up to the ridge. Charred rafters and purlins cross the hole, and through it the sooty inside of the roof shows.
  * The ridge has a charred ridge cap with crossed karasu-odori poles.
  * Under the hole the floor is burned through, with charred thatch clumps and coals on the floor.
* **The two sliding doors (itado)**:
  * One is knocked out of its track and leans against the front wall left of the doorway, charred at the bottom with a glowing corner.
  * The other is still in its track, cracked and tilted outward, with its top-left corner burned away.
  * A 3.2-wide passage stays free (a 2.9-wide box is checked).

### stake_fence (24 long, 6.95 tall above ground)
* **Stakes**: 24 stake positions 1 stud apart: 21 intact sharpened log stakes plus the 3 damaged ones below. They are 6-sided, Ø 0.84–0.98, and sunk 1 stud into the ground.
  * The hewn tips are charred with glowing cracks (`char_ember`).
  * About 60 % of the stakes also have a charred band below the tip.
* **The 3 damaged stakes**: one stake is **missing**; one is **snapped** to a splintered charred stump, with its broken top lying in the mud in front; one is **leaning 22° outward**, with its lashing gone.
* **Rails**: two rails on the inside (+Y), tied to the stakes with 6 straw-rope lashings.
* **Tiling**: the rails run exactly from x = −12 to +12. Place the next segment at this origin + (24, 0, 0). The rails butt end to end and the 1-stud stake rhythm continues across the joint (checked).

### battle_debris_a / battle_debris_b (~8 × 8 scatter piles)
Both piles contain every item type, in different compositions. Items are modelled once and placed with random rotations, resting on (or stuck into) the ground.

**The items**
* **Spears**: broken spears with timber shafts, splintered snapped ends (cream inside), iron sockets and leaf blades.
* **Shield**: a cracked round plank shield. It has a zigzag crack from the rim toward the iron boss, a chunk broken out of the rim, and a charred corner with ember cracks.
* **Helmet**: a dented crude iron helmet with a short spike. The dent pushes the outer and inner shells in together.
* **Cart wheel**: hub, 8 spokes, and felloes with an iron tyre. A section of the rim is broken away, with splintered ends, and some spokes are snapped or missing.
* **Orc war banner** (orc cloth):
  * ragged tatters at the bottom, a torn drooping top corner, and a charred corner
  * the **boar head emblem made of cloth faces**: ears, slanted eyes, a flat snout with nostrils, and long tusks, in bone colour with dark-iron eyes, nostrils and snout line
  * the emblem is cut into the cloth by a constrained triangulation, like the batch B banner, so it shows on both sides (mirrored on the back, like a dyed banner)

**The two piles**
* **battle_debris_a, "the fallen standard"**:
  * the banner pole, still planted, snapped at the top, with the torn banner tied to it
  * the broken-off top of the pole, with its crossbar, lying in the mud
  * a broken wheel lying flat
  * the cracked shield, face up
  * the helmet on its side
  * one spear stuck head-down in the mud, and two lying
* **battle_debris_b, "the wrecked cart"**:
  * the wheel stuck upright in the mud on its broken axle
  * the shield leaning against the wheel
  * the helmet upside down
  * three broken spears lying crossed, and one stuck in the mud
  * the torn banner draped over the spear shafts on the ground, next to its snapped pole

### torii_broken (12 between the pillars, 18-stud torii scale)
* **Standing pillar (right)**: stands full height. The kasagi seat at its top is charred, with the tenon showing. The snapped nuki is still wedged through it.
* **Snapped pillar (left)**: snapped at half height (z 8.9) into a charred splinter crown with glowing cracks. Its upper half, with its collar and tenon, lies in the mud behind-left.
* **Fallen beam stack**: the kasagi stack (shimaki + kasagi + cap, with the upturned ends of batch B's torii) has **fallen diagonally**.
  * Its upper end rests on the splintered stub. Its lower end lies in the mud at the front-left (pitch 32°).
  * It is mostly vermilion, with charred ends and soot.
* **Debris**: the snapped nuki halves and the fallen plaque lie behind-left, and a smouldering spot sits at the base of the stub.
* **Paint**: the lacquer is sooty vermilion (`lacquer_soot`) with clean vermilion bands, and charred at the foot and at the breaks.
* **Wagon corridor**: a corridor **7 wide × 9 tall** (x −3.5…3.5) along the whole depth contains no triangle and no collider (checked). A 6-wide wagon passes between the pillars. The clear width between the stone bases is 8.68.

### trampled_log (30.5 long including the splinters)
* **Trunk**: a 10-sided tapered trunk (Ø 2.2 at the crown end to Ø 3.3 at the break), slightly bent. It lies in the mud with its bottom sunk 0.3.
* **Break ends**:
  * **Butt end**: the trunk **snapped**, it was not cut. Long splinters stand on the hinge side and short ones on the opposite side, the broken core stands proud, and the inside is cream wood.
  * **Crown end**: also torn, with short splinters.
* **Branches**: 6 broken branch stubs (7-sided, splintered tips) point up and sideways, angled toward the lost crown.
* **Ground debris**: cream bark patches are scraped off on top, and three cream splinter shards lie in the mud at the break.

## Atlas tile layout (`atlas.png`, 4×4 × 256 px, UV inset 4 % per tile)
Rows are counted from the top of the PNG. A face's UVs always stay inside one tile's 4 % inset; `build.py` checks every face and aborts otherwise.

| row \ col | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| **0** | `char_ember` charred (44,37,32) boards + alligator cracks, ~45 % of the cracks glow **ember (196,84,32)** | `char` charred (44,37,32) boards + dark alligator cracks | `planks` bark (92,65,43), 4 boards + grain | `planks_dark` bark dark (79,58,40), 4 boards + grain |
| **1** | `timber` bark light (109,77,47), grain | `bark` bark (92,65,43), vertical grooves | `cut_wood` cream (226,216,190), grain (splinters, cut wood) | `thatch` straw (204,172,98), fine strands in 4 courses |
| **2** | `stone` rock grey (126,124,118), soft facet blotches | `lacquer` vermilion (200,52,38), flat | `lacquer_soot` vermilion + charred (44,37,32) soot patches | `iron` dark iron (58,58,64), flat |
| **3** | `cloth` orc cloth (112,74,52), flat | `bone` cream (226,216,190), flat (boar emblem) | `rope` straw (204,172,98), twisted rope | `ember` ember (196,84,32), flat (glowing coals) |

**Painting**:
* Flat tiles carry about ±3 % smooth noise.
* Every patterned tile is periodic in u and v.
* The four board tiles (`char_ember`, `char`, `planks`, `planks_dark`) have their seams at the same u, so boards continue across tile changes.

Each tile's measured mean colour is in `report.json → atlas.tiles`:
* Flat tiles match the palette within 1 %.
* Patterned tiles shift with their pattern: `char_ember` is warmer (57,41,32) because of the glowing cracks; `lacquer_soot` is about 40 % soot; `thatch` and `rope` are a little darker in their grooves.

**UV mapping**:
* **Planar faces** are mapped on their own plane, with one uniform scale per tile and world-anchored UVs, so the pattern runs on across faces.
* **Large panels** (walls, floors, thatch slabs, door leaves) are triangulated with a constrained Delaunay triangulation (CDT). Their faces are also cut along the tile borders of that world-anchored mapping. Every triangle therefore sits in one tile period, and boards and thatch courses continue seamlessly over a whole wall or roof.
* **Round pillars and the log** use tube UVs (u around, v along) so patterned tiles run on around them.
* **Worst UV anisotropy per model** is 1.00–1.08. The log is 1.37, from its tube mapping along a tapering trunk.

**Tile use**:
* `char_ember` → glowing breaks, board ends, hole rims, stake tips
* `char` → charred posts, beams, boards and thatch, and the sooty roof undersides
* `planks` → walls, floors and the shield
* `planks_dark` → posts, beams and doors
* `timber` → spears, poles and the cart wheel
* `bark` → the log, stakes and rails
* `cut_wood` → splinters, cut ends and scraped bark
* `thatch` → roofs and straw remnants
* `stone` → footings, step stones, torii bases and the ash mounds
* `lacquer` / `lacquer_soot` → the torii
* `iron` → helmets, blades, tyres, bosses and the emblem eyes
* `cloth` + `bone` → the banner
* `rope` → fence lashings
* `ember` → the coals in `_Embers`

## Conventions used (for the importer)
* **Units and axes**: 1 Blender unit = 1 stud, with `scene.unit_settings.scale_length = 0.01`. Z is up, the front faces −Y, and each origin is the centre of the base at z = 0.
  * Footing stones sink 0.2. Stakes, the torii bases, planted poles, spear blades and the splinters of house b's fallen eave beam sink up to 1.0. The lowest point per model is between −1 and 0 (checked, `report.json → checks.lowest_point_per_model`).
* **Origin choices**:
  * houses: centre of the post footprint (the post lines are at x = ±W/2, y = ±D/2)
  * fence: centre of the 24-stud segment
  * debris: centre of the pile
  * torii: centre between the two pillar bases, even though the fallen beam reaches out to the front-left
  * log: centre of its length, on the ground
* **Collections and names**: each model is one collection named by its id, with meshes `<id>_<Part>`. No armatures and no bones.
  * Glowing coals are separate meshes, `<id>_Embers` (flat `ember` tile), so the game can switch them to Neon or add a light.
* **Transforms**: all objects have identity transforms, with no modifiers (both checked).
* **FBX export**: per model with `object_types={'MESH'}`, `apply_unit_scale=True`, `apply_scale_options='FBX_SCALE_UNITS'`, `axis_forward='-Z'`, `axis_up='Y'`, `mesh_smooth_type='OFF'`, atlas embedded.
  * Each FBX is re-imported into a scene with the same unit scale. It gives back the same object names, triangle counts per mesh and size in studs, with 0 n-gons, one UV layer and the material `Atlas` (`report.json → export_verification.<id>.ok = true` for all 8).
* **GLB**: all models in one file. glTF ignores the unit scale, so 1 m in the GLB = 1 stud. The re-imported GLB contains all 33 meshes.
* **`colliders.json`**: `{"<id>": [[[cx,cy,cz],[sx,sy,sz],yawDeg], ...]}` in Blender coordinates (Z up, studs). Each entry is the box centre and full size; yaw is about +Z. Every box overlaps the model's geometry (checked).

  | model | boxes | what |
  |---|---|---|
  | oni_house_a | 35 | 13 posts, wall pieces (≈ 4-stud chunks of every wall, as high as the wall there; doorway left open), floor, step stone, the two caved thatch chunks, the remaining roof |
  | oni_house_b | 17 | 11 posts incl. the central pillar, wall stubs, floor (left part), step stone |
  | oni_house_c | 37 | 13 posts, wall pieces, floor, step stone, both doors, roof |
  | stake_fence | 2 | the palisade (24 × 1.4 × 7) and the leaning stake |
  | battle_debris_a | 2 | banner pole, flat wheel |
  | battle_debris_b | 1 | upright wheel with its axle |
  | torii_broken | 8 | 2 pillars, 2 bases, the fallen beam as 3 stepped boxes, the fallen pillar half |
  | trampled_log | 2 | two yawed boxes along the trunk |

  * **Floor boxes** run from the ground to the floor top (1.6), so players never get under a raised floor.
  * **No collider enters** a house interior box, a doorway box or the torii wagon corridor (checked).
  * **Left without colliders** (walk-through): steeply leaning beams and the small debris (spears, helmets, coals, straw). Yaw-only boxes cannot follow a sloped beam.

## Decisions taken (no questions possible)
1. **"12–16 tall" is measured from the ground.** House a reaches 15.74 and house c 15.71 above ground. The bounding boxes in `report.json` also include the 0.2 sink of the footing stones (15.94 / 15.91), still under 16. House b's top is its 12.2-stud central pillar plus splinters (13.16).
2. **The roof pitches are 30–32°**, flatter than real kayabuki (45°+), so the roofs stay under 16 studs while every interior keeps more than 7 studs from the floor (1.6) to the beams (9.0).
3. **House a: "roof caved in on one side, two walls standing".** I read "one side" as the right end of the house.
   * The left gable wall and the back wall stand. The front and right walls are stubs.
   * The left part of the roof (both slopes) still stands, which keeps a walkable interior under it, and the right part caved in.
4. **Walls are wooden planks (itabari)** rather than mud plaster, as the item asks for "charred wood … original plank colour". Posts and beams use the dark plank tile and walls and floors the bark plank tile, so the frame reads against the walls.
5. **The torii beam rests on the snapped stub, not on the standing pillar.**
   * A 20-stud kasagi cannot reach the ground from an 18-stud pillar top except nearly vertically. If its ground end lay toward the standing pillar, it would also block the wagon path.
   * So the left pillar snapped and the stack fell onto its stub, with the lower end in the mud at the front-left, outside the passage.
   * The standing pillar keeps the snapped nuki, high above the corridor.
6. **The fence's 3 damaged stakes** (one missing, one snapped, one leaning) are not neighbours. Every gap stays about 1 stud wide, too narrow to walk through, so the palisade collider is one continuous box.
   * The damage repeats every 24 studs on a long run. Rotate a segment 180° about Z to vary it; the rails then face outward.
7. **Both debris piles contain all five item types**, in different compositions. That is how I read "different compositions".
8. **The boar emblem is bone-coloured with dark-iron eyes and nostrils.** No new colour was needed: cream reads clearly on orc cloth, and dark iron gives it the crude, painted look.
9. **The coals are separate `<id>_Embers` meshes** on the flat `ember` tile. The game can make them glow; the charred tiles carry the ember cracks for everything else.
10. **Ash is shown with the rock-grey `stone` tile** (flat mounds under the coals), so no off-palette colour was added.
11. **No armature on the log.** It is a felled trunk lying on the ground with no leaves to animate, so it is a prop (`trampled_log_Bark`) with yaw-box colliders.
12. **Mesh parts are split by function**, so the game can hide or swap them:
    * houses: `Frame`, `Walls`, `Floor`, `Roof`, `Stone`, `Debris`, `Embers`
    * torii: `Pillars`, `Fallen`, `Stone`, `Embers`
    * fence: `Stakes`, `Rails`
    * debris: `Wood`, `Iron`, `Cloth`

    The doors of house c are separate pieces inside `oni_house_c_Debris`.
13. **The village scene is render-only.** Its flat ground (mud (78,66,54)) and trampled trail strip (122,104,84) are never exported or saved.
    * It shows the 3 houses, 7 fence segments and the broken torii at the entrance, with the debris piles and the log on the trail outside.
    * `village_scene_yard.png` is an extra eye-level view through the torii.
14. **Views**: as in batch B, "left" is the camera on −X and "right" the camera on +X. The 3/4 view is from the front-right, about 38° above. The 5-stud human stands front-right, or back-right in the 3/4 view, so it never hides a small prop.

## Self-review: automated checks
All of these pass. They are written to `report.json → models.<id>.checks`, and the build aborts if any fails.
* **Budgets**: all within limits (table above).
* **UVs**: every face sits in one tile, inside the 4 % inset, on the tile its paint asks for. The worst anisotropy per model is listed.
* **Inverted normals / holes ("back-face culling" ray test)**: 5000 rays per model, from all around and above the ground. The first face hit must face the ray. Result: **0** back-facing first hits on all 8 models.
* **Floating parts**: every primitive (10 to 89 per model) must touch or intersect the ground, or something that does. Result: **0** floating pieces. Every closed piece also has a positive signed volume, so none is inside-out.
* **Z-fighting (new in this batch)**: triangles of different pieces that lie in the same plane, face the same way and overlap are reported with their overlap area.
  * Anything visible fails the build.
  * What remains, and is allowed, is never visible: the corner squares where two wall ends meet *inside* a corner post (0.03–0.06 stud²), and the flat bottoms of touching coals under the ash.
* **Clearance boxes** (0 triangles, 0 colliders in each):
  * the house interiors (more than 7 studs high)
  * the doorways of houses a and c
  * the torii wagon corridor
* **Fence tiling**: the rail end sections at x = ±12 match, the stake gap across the joint is 1.0, and nothing pokes past the segment ends.
* **Open edges**: only where a piece is deliberately embedded in another, so the open boundary is never visible. That covers post bottoms in footing stones, stake bottoms underground, branch-stub roots inside the log, collars around the torii pillars and the shield boss base. They are counted per mesh in `report.json`.
* **Shared edges at the thatch seams**: the flush thatch seams (ridge and hips) share edges by position. They are separate vertices, and the touching cut faces are back to back inside the roof.
* **Transforms, modifiers, sink**: identity transforms, no modifiers, lowest point between −1 and 0 for every model.
* **Colliders**: every box overlaps the model's geometry.
* **Export round-trip**: all 8 FBX files and the GLB pass.

## Self-review: problems found in the renders and fixed
* **Torii**:
  * The soot pattern banded vertically on the round pillars, because every facet had its own planar UVs. Round pillars now use tube UVs, and the soot runs on around them.
  * The first version was almost black. It now has clean vermilion bands, and the fallen kasagi cap is black lacquer (dark iron) except at its burnt lower end.
* **Roof hole (house c)**: the hole showed straw inside, so it did not read as a hole.
  * The cut faces of every panel now take the paint of the face they border, so burn regions wrap over the cut edges.
  * The roof undersides are sooty, so the hole reads as a dark opening with a glowing rim.
* **Splintered ends** looked like regular stars when seen end-on. Tooth positions, depths and the broken core are now jittered.
  * The log's break is asymmetric (long splinters on the hinge side), so it reads as *snapped*, not cut.
* **Banner emblem**: it first read as a cat. The snout, the nostrils and the tusks are now bigger.
* **Shield**: the crack was invisible. It became a zigzag crack from the rim toward the boss.
* **Scale human**: it hid small props in the 3/4 views. It now stands back-right for that view.
* **Village scene**:
  * The trail strip was wound facing down, so back-face culling hid it. It was rewound.
  * The scene was too dark and too spread out. It now has lighter mud colours, a tighter layout, a closer camera and a larger ground, so no ground edge shows.
* **Log length**: it measured 31.2 with the shards. It is now 30.5.

## Problems caught by the automated checks and fixed
* **Budgets**:
  * **Debris piles** (1580 → 1173 / 1197): the wheel rim uses 4-point sections, the shield boss is lighter, the spears lost their rope binding, and the pole sides were reduced.
  * **House a** (4798 → 3930):
    * the floor texture cells are 6 studs, like the wall boards
    * the burn regions on broken walls use a few points instead of copying every board end
    * fewer rafters
    * 7-point footing rocks
    * 4-point splinters on beam ends
  * **Fence** (926 → 826): 3 rail segments and 6 lashings.
* **Height**: two houses measured just over 16 with the sunk footings. The footings now sink 0.2, and house c's pitch went from 31° to 30°.
* **Helmet**: the dent pushed the outer shell through the inner one (3 back-facing hits). The dent now moves outer and inner ring pairs together, before lofting.
* **Wheel rim floating**: the spokes stopped 0.05 short of the rim. They now run into it.
* **Shield crack outline**: the crack self-intersected because of the point order. The order was fixed.
* **Doorway box (house c)**: the leaning door touched the doorway clearance box. The door was shifted 0.26 left.
* **Z-fighting** (found by the new check), all fixed geometrically:
  * posts and eave beams were the same width, with flush ends and tops
  * crossing beams shared their top and bottom planes
  * door jambs were flush with post faces
  * the threshold and the sill were coplanar
  * the crossed karasu poles shared side planes
  * the ridge pole was flush with the thatch verge

## Reproducibility
`build.py` always starts from `read_factory_settings(use_empty=True)`, and every model uses its own seeded RNG. I ran it twice from clean (all outputs deleted) and compared every output byte for byte.
* **All 57 files in the folder except the .blend are identical**: `build.py`, `REPORT.md`, the atlas, all 8 FBX, the GLB, both JSON files and all 43 renders.
* The zip entries carry a fixed timestamp. The zip still differs between runs, but only because it contains the .blend.

Two sources of noise had to be removed, as in batches A and B:
* **FBX exporter**: it derives node UIDs from Python's per-process randomised `hash()` and writes `datetime.now()` into the header. `build.py` patches both at runtime (stable SHA-1 UIDs, fixed date); the installed add-on is untouched.
* **PNG metadata**: Blender writes date stamps into the PNG files, so `build.py` re-saves them without metadata.

The `.blend` differs at byte level, because Blender writes runtime memory addresses into it. Re-opening both files and hashing every collection, object, matrix, vertex, face, smooth flag, UV and material gives the same content on both runs.

## Notes
* **Lighting**: Workbench's studio light follows the camera and darkens the charred tiles a lot in the renders. The atlas colours are exactly the palette's.
* **Harmless log noise**: the glTF exporter prints that the optional Draco and meshoptimizer libraries are missing from the bpy wheel. Compression is not used, so the GLB is unaffected. Mesa may print `EGL_BAD_MATCH` during rendering, also with no effect on the files.
