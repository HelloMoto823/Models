# Batch B: Khusha mountains, home of the Tengu

Everything in this folder comes from **one script, `build.py`**. It does all of the following:
* paints the atlas
* builds the 6 props
* runs the self-checks, and stops the build if any of them fails
* exports FBX, GLB and `.blend`
* renders the review images (including the path test scene)
* re-imports every export to verify it
* writes `report.json` and `colliders.json`, and zips the folder

A full run takes about 60 s on 4 CPU cores. Two clean runs produce byte-identical files (see *Reproducibility*).

```
blender -b -P build.py              # Blender 4.x/5.x; Pillow must be importable from Blender's Python
python3 build.py                    # or with the bpy module:  pip install bpy==5.2.3 pillow  (Python 3.13)
options: --no-render  --no-verify  --no-zip
```

Toolchain:
* Built and verified with **bpy 5.2.3 LTS** (the official Blender-as-a-module wheel), the same as batch A.
* Headless rendering needed the Mesa EGL libraries (`apt-get install libegl1 libegl-mesa0 libgl1-mesa-dri`).
* Renders use **Workbench**: studio light, back-face culling **ON**, light grey background, 16× AA.

## Deliverables

| file | content |
|---|---|
| `build.py` | the generator (atlas, models, checks, exports, renders, reports, zip) |
| `atlas.png` | 1024×1024, 4×4 tiles of 256 px (layout below) |
| `jura_batch_B.blend` | the 6 model collections, saved right after the build (atlas linked as `//atlas.png`) |
| `<id>.fbx` ×6 | one per model, atlas embedded |
| `jura_batch_B.glb` | all 6 models (22 meshes) |
| `colliders.json` | boxes per model (format below) |
| `report.json` | per model: size, bbox, triangles per mesh, budget, colours, collider count, info, every check result, geometry hashes; plus the atlas table, export verification and path-scene layout |
| `renders/` | 30 model views (front, back, left, right, three_quarter), `lineup.png`, `path_scene.png`, plus extras `path_scene_climb.png` and `path_scene_side.png` |

## What was built

Sizes are `[x, height, depth]` in studs from the mesh bounding box. They include the part sunk below z = 0 (stated per model).

| id | size | triangles | budget | mesh parts |
|---|---|---|---|---|
| torii | 20.00 × 21.75 × 3.14 (sinks 0.62) | **884** | 1 500 | `torii_Lacquer` 584, `torii_Black` 176, `torii_Stone` 124 |
| toro_lantern | 3.76 × 7.50 × 3.26 (sinks 0.50) | **694** | 900 | `toro_lantern_Stone` 670, `toro_lantern__Glow` 24 |
| stone_stairs | 16.05 × 10.54 × 20.00 (sinks 1.0) | **804** | 1 200 | `stone_stairs_Stone` 804 |
| tengu_gate | 74.87 × 23.00 × 19.07 (sinks 1.0) | **5 848** | 6 000 | `_Lacquer` 964, `_Roof` 1508, `_Stone` 1596, `_Rope` 646, `_Rock` 308, `_DoorL` 249, `_DoorR` 249, `_Plaster` 184, `_Wood` 96, `__Glow` 48 |
| roadside_shrine | 3.54 × 5.52 × 3.28 (sinks 0.45) | **658** | 800 | `_Offering` 264, `_Wood` 200, `_Roof` 120, `_Stone` 74 |
| tengu_banner | 3.51 × 14.80 × 1.12 (sinks 0.80) | **286** | 300 | `tengu_banner_Cloth` 168, `tengu_banner_Pole` 118 |

Every budget holds. Every mesh is triangles only, with UV map `UVMap` and the single material `Atlas`.

### torii
* **Pillars**: 16-sided and smooth-shaded, 12 studs apart (centre to centre at the ground), rising to z = 18.3, where they end inside the lower lintel.
  * They lean slightly inward toward the top (uchikorobi): the centres go from x = ±6.0 to ±5.82.
  * The black band at the foot (nemaki) is `torii_Black`.
  * The collar at the top of each pillar (daiwa) gives the Inari-style look.
* **Top beams**: the shimaki (lower lintel) and the kasagi body are vermilion. A dark-iron black cap sits on the kasagi.
  * All three curve upward toward the ends, with an end lift of 1.45 studs (an exaggerated anime curve).
  * Their slanted end cuts reach exactly x = ±10 (**20 wide**).
* **Lower beams**: a straight nuki, protruding past the pillars, with a centre strut (gakuzuka) and a blank black plaque.
* **Stone bases**: faceted, irregular, 8-sided.
* **Clearances**: 10.0 studs between the pillars, 8.68 between the stone bases, and 14.4 under the nuki. A wagon 6 wide passes with room to spare.

### toro_lantern (7.0 tall)
A hexagonal Kasuga-style stone lantern:
* a two-tier base
* a round 10-sided pillar with a node ring
* a hexagonal middle platform (chudai)
* a hexagonal light box
* a hexagonal roof with concave slopes, lifted corners and 6 curled horn-shaped corners (warabite)
* an onion finial

**Light box**: the faces looking **−Y and +Y** have real window openings with stone reveals.
* The reveals run into **`toro_lantern__Glow`**, a closed hexagonal prism inside the box.
* The windows therefore show the warm glow (255, 190, 110) and never a hole.

Moss grows on random upward faces of the roof and base.

### stone_stairs (16 wide, rises 8 over 20, 8 steps of 1 × 2.5)
**Steps**: each step is 2–3 irregular stones with chamfered top edges, random seams and ±0.05–0.1 stud height and tilt jitter.
* Each stone runs 0.5 studs under the next step, so the treads never show gaps.
* Mossy-stone faces are concentrated near the curbs.

**Curbs**: each side has a low curb of 3 sloped, chamfered stones. Their top is 0.6 above the tread noses, and their bottom is 1 stud below the slope line.

**Fallen leaves**: 6 fallen momiji leaves lie on the treads (see decision 8).

**Tiling**: the next segment goes at this origin + **(0, +20, +8)**.
* The first riser starts at y = −10 and the last tread ends at y = +10.
* The curb end sections at y = ±10 match exactly after that offset. They are never jittered, and `build.py` checks this.
* The stair keeps a 1-stud rise across the joint.
* The terrain under a segment should follow `z = 0.4·(y + 10)`, the back edge of every tread.

**Colliders**: one box per step, the full 16 wide, from the tread top down to 1.8 below it.

### tengu_gate (roofed gate 26 wide × 22 tall; front −Y)
**Gate frame**:
* 2 vermilion main pillars at x = ±8 and 2 rear posts, all on stone footings.
* Tie beams, the door lintel (kamoi), a plaster panel, and the head beam (kashiranuki) with white-painted ends.
* Bracket blocks and purlins with white ends.
* A plaster gable wall under each side of the roof.

**Roof**: a heavy kirizuma kawara roof, 26 × 18.
* **Shape**: concave slopes (eaves at 17.3, ridge at 21.0), with the eave corners turned up by 0.9.
* **Tiles**: modelled as 16 corrugated tile rows per side, with a tile-end skirt.
* **Eaves**: a vermilion fascia under the tile ends; 13 vermilion rafters per side under each eave, with white ends.
* **Ridge**: stacked and turned up at the ends, with a white plaster band.
* **Gable details**: onigawara end tiles (top at exactly z = 22.0), vermilion bargeboards (hafu), and gold gegyo ornaments.

**Doors**: two closed leaves of dark planks with iron bands, gold studs, and a **gold Tengu-feather crest** split across both leaves. On the back there are battens and a kannuki locking bar.

**Shimenawa**: a thick twisted straw rope across the top of the opening, thicker in the middle, with 3 straw tassels and 4 white zigzag **shide**.

**Side walls**: white plaster walls (tsuijibei) with timber posts, a stone footing course and a small tiled coping roof. They run to x = ±22.6 and **end inside mountain rock**: 5 faceted moss-topped rocks per side, up to 20 tall.

**Lamp posts**: two tōrō lamp posts, 8.75 tall, at (±11, −8). Their light boxes are **`tengu_gate__Glow`**.

**Barrier space**: the box **x ±6.75, y −5.10 … −3.09, z 0 … 13.4** is kept empty.
* That is 2 studs in front of the doors' frontmost relief. The iron studs reach y = −3.07; the door planks are at y = −2.75.
* `build.py` checks the box: it contains 0 triangles.
* The shimenawa and shide hang above it: their lowest point is z = 13.66.

**Door leaves**: `tengu_gate_DoorL` (x < 0) and `tengu_gate_DoorR` are separate meshes, so the game can swing them open.
* They hinge about the vertical axes at (∓7.06, −2.475).
* Hide or remove the kannuki bar in `tengu_gate_Wood` first.

### roadside_shrine (hokora, 5.07 tall)
* **Stone base**: two-tier and irregular, with a front ledge.
* **Shrine**: a pale hinoki-wood body (cream) with brown corner posts and doors on a wooden floor.
* **Roof**: a tiny gabled kawara roof with chigi (crossed boards at the gables, their tips setting the 5-stud height) and two katsuogi logs on the ridge.
* **Offering cup**: white, on the front ledge.
* **Folded paper charm**: a zigzag **shide** on a small straw rope across the shrine front.
* **Fallen leaves**: 3 momiji leaves.

### tengu_banner (nobori, 14.0 tall)
* **Pole**: a wooden pole on a stone weight, with a gold finial and a crossbar.
* **Cloth**: cream, 2.5 × 10.55, with a gentle low-poly billow. It is tied to the pole and the bar with 4 cloth loops.
* **Emblem**: a **black Tengu feather** painted as **faces of the cloth mesh**: two notched vanes, a light rachis gap and a quill, with no text.
  * A constrained Delaunay triangulation cuts the cloth around the emblem, so there is no decal and no z-fighting.
  * The cloth is a thin closed slab, so the emblem shows on both sides (mirrored on the back, like a dyed banner).

## Atlas tile layout (`atlas.png`, 4×4 × 256 px, UV inset 4 % per tile)
Rows are counted from the top of the PNG. A face's UVs always stay inside one tile's 4 % inset; `build.py` checks every face and aborts otherwise.

| row \ col | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| **0** | `stone_grey` rock grey (126,124,118), stone blotches | `stone_warm` dwarf stone (148,140,128), stone blotches | `moss` moss (84,122,60) clumps over rock grey | `plaster` limestone (232,228,214), flat |
| **1** | `lacquer` vermilion (200,52,38), flat | `iron` dark iron (58,58,64), flat | `roof_tile` dark iron, kawara courses + round-tile columns | `gold` gold trim (204,166,82), flat |
| **2** | `wood_dark` bark dark (79,58,40), planks + grain | `wood` bark (92,65,43), timber grain | `wood_light` bark light (109,77,47), timber grain | `cut_wood` cream (226,216,190), pale hinoki grain |
| **3** | `rope` straw (204,172,98), twisted 3-strand rope | `cloth` cream (226,216,190), flat | `glow` **lantern glow (255,190,110)**, flat | `maple_red` maple red (196,58,40), flat |

**Painting**:
* Flat tiles carry about ±3 % smooth noise.
* Stone tiles carry soft round blotches, so random UV windows give each facet its own shade.
* Every patterned tile is **periodic in u and v**, so world-anchored UVs continue the pattern across face borders.
* Each tile's measured mean colour is in `report.json → atlas.tiles`. Flat tiles match the palette within 1 %. Patterned tiles are darker on average because of their grooves: rope is 11 % darker, and the mossy-stone tile is a mix of moss and rock grey.

**UV mapping**:
* Faces are mapped planar on their own plane, with **one uniform scale per tile** (no stretching). Faces bigger than a tile are scaled down uniformly.
* Ropes use a tube mapping: u around the rope, v along it.
* The worst UV anisotropy over all faces of each model is 1.01–1.11. The exception is the gate at 1.58, on the tapering rope ends.

**Tile use**:
* stone → bases, lanterns, stairs, wall footings, rocks
* lacquer → torii and gate frame
* iron → torii black, door bands, banner emblem
* roof_tile → roofs
* gold → studs, crest, gegyo, finial
* wood tiles → doors, posts, poles
* cut_wood → painted beam ends, shrine body, chigi
* rope → shimenawa, tassels
* cloth → banner
* glow → `__Glow` meshes
* maple_red → fallen leaves

## Conventions used (for the importer)
* **Units and axes**: 1 Blender unit = 1 stud, with `scene.unit_settings.scale_length = 0.01`. Z is up, the front faces −Y, and each origin is the centre of the base at z = 0.
  * Foundations, footings, pole feet and rock bottoms sink 0.45–1.0 studs. The lowest point per model is between −1 and 0 (checked).
* **Origin choices** (the "centre of the base" for the less obvious models):
  * stairs: centre of the footprint, at the elevation of the bottom riser
  * gate: centre of the roofed footprint (under the ridge), with the door plane at y = −2.5
  * banner: the pole and its stone weight (the cloth hangs toward +X)
* **Collections and names**: each model is one collection named by its id, with meshes `<id>_<Part>`. As in the item list, the glow meshes are `<id>__Glow` with a double underscore. No armatures, no bones.
* **Transforms**: all objects have identity transforms, with no modifiers (both checked).
* **FBX export**: per model with `object_types={'MESH'}`, `apply_unit_scale=True`, `apply_scale_options='FBX_SCALE_UNITS'`, `axis_forward='-Z'`, `axis_up='Y'`, `mesh_smooth_type='OFF'`, atlas embedded.
  * Each FBX is re-imported into a scene with the same unit scale. It gives back the same object names, triangle counts per mesh and size in studs, with 0 n-gons, one UV layer and the material `Atlas` (`report.json → export_verification.<id>.ok = true` for all 6).
* **GLB**: all models in one file. glTF ignores the unit scale, so 1 m in the GLB = 1 stud. The re-imported GLB contains all 22 meshes.
* **`colliders.json`**: `{"<id>": [[[cx,cy,cz],[sx,sy,sz],yawDeg], ...]}` in Blender coordinates (Z up, studs). Each entry is the box centre and full size; yaw is about +Z, and all boxes in this batch have yaw 0. Box counts per model:

  | model | boxes | what |
  |---|---|---|
  | torii | 6 | 2 pillars, 2 bases, kasagi, nuki |
  | toro_lantern | 2 | base, body |
  | stone_stairs | 8 | one per step |
  | tengu_gate | 21 | closed doors, lintel band, 2 pillars + footings, 2 rear posts, 2 walls, 2 lamp posts, 8 rocks, roof |
  | roadside_shrine | 2 | base, shrine |
  | tengu_banner | 2 | pole, weight |

  The banner cloth, the rope, the shide and the small decorations have no colliders. No collider enters the gate's barrier space.

## Decisions taken (no questions possible)
1. **"Pillars 18 studs tall, 12 apart."** I read 12 as the centre-to-centre distance at the ground, and 18 as the pillar length up to the lintel (they end at z = 18.3 inside it).
   * The kasagi stack and its upturned ends bring the overall height to 21.1 above ground.
   * The inward batter (a real torii detail) brings the pillar centres to 11.64 apart at the top. The clear width never drops below 9.96.
2. **"Tengu gate 26 wide, 22 tall."** These are the roofed gate: the roof is 26 wide, and the onigawara top is at 22.0. The plaster walls and mountain rock are added on both sides, so the whole model is 74.9 wide.
   * I picked a closed yakuimon-style gate with vermilion lacquer, because it fits the shrine mood of the path.
   * The wall ends sit inside the rocks, so the model can be dropped into a mountain pass.
3. **Barrier space** is measured from the frontmost door relief (iron studs). It spans the door opening between the pillar footings (±6.75 of the 7.06 half-door) and goes up to the door top.
4. **One material per model.** The glow meshes use the `Atlas` material on the flat `glow` tile instead of a separate emissive material, as the shared spec asks for one material. The game can switch the `__Glow` MeshParts to Neon or add a light at night.
5. **Gate lamp posts** reuse the lantern generator, scaled ×1.25 with fewer sides. Their light boxes form `tengu_gate__Glow`, named like the lantern glow so the same night script can light both.
6. **"Black"** is dark iron (58, 58, 64), as the torii item specifies. It is used for the torii cap and foot, the plaque, the door bands and the banner emblem.
7. **Wood colours** come from the bark palette (bark, bark dark, bark light), with cream for pale hinoki and painted beam ends. No new wood colour was added.
8. **Fallen momiji leaves** (the `maple_red` tile) are added to give these pieces the autumn-maple mood of the path: 6 on each stair segment and 3 on the shrine.
   * They are dropped by ray-casting onto the real surfaces, kept only where the whole leaf lies on one face, and embedded 0.04 studs, so they never float.
   * They sit inside the segment, so the stair tiling is unaffected. They repeat every 20 studs on a long flight. Remove them by deleting the `drop_leaves` lines if that bothers the level design.
9. **The gate doors are separate meshes** (DoorL / DoorR), so a barrier-drop or village-opening event can animate them. Their hinge axes are given in `report.json → tengu_gate.info.doors`.
10. **The nobori emblem is a stylised single feather** (two notched vanes split by a light rachis line, plus a quill), leaning 9°. The same feather, in gold inside a ring, is the crest on the gate doors.
11. **Views**: the "left" view is the camera on −X looking toward +X (Blender's Left view), and "right" is from +X. The 3/4 view is from the front-right, 38° above.
12. **The test scene's terrain and maple trees are render-only stand-ins**: flat palette colours, made by `build.py`, never exported or saved. The maples only provide the autumn mood; the real maples are batch A's.

## Self-review: automated checks
All of these pass. They are written to `report.json → models.<id>.checks`, and the build aborts if any fails.
* **Budgets**: all within limits (table above).
* **UVs**: every face sits in one tile, inside the 4 % inset, on the tile its paint asks for. The worst anisotropy per model is listed in `report.json`.
* **Inverted normals / holes ("back-face culling" ray test)**: 5000 rays per model, from all around and above the model's ground surface. The first face hit must face the ray. Result: **0** back-facing first hits on all 6 models.
* **Floating parts**: every primitive (one "piece" per call: 9 to 165 per model) must touch or intersect the ground, or something that does. The test uses a triangle-overlap test, a 0.03-stud proximity test and a containment test. Result: **0** floating pieces. Every closed piece also has a positive signed volume, so no piece is inside-out.
* **Open edges**: only where a piece is deliberately embedded in another, so the open boundary is never visible. That covers pillar and pole ends, the light-box shell, the door relief, stud bases and leaf undersides. They are counted per mesh in `report.json`. There are no non-manifold edges.
* **Gate barrier space**: 0 triangles inside it.
* **Stair tiling**: the curb ends match exactly after the (0, 20, 8) offset, the first riser is at y = −10 and the last tread ends at y = +10.
* **Transforms, modifiers, sink**: identity transforms, no modifiers, lowest point between −1 and 0 for every model.
* **Export round-trip**: all 6 FBX files and the GLB pass.

## Self-review: problems found in the renders and fixed
* **Lantern**:
  * The corner knobs (octahedra) looked like floating diamonds from the front. They were replaced by horn-shaped curls that grow out of the roof corners, and the corner lift was increased (0.34 → 0.52).
  * The pillar was too thin to read from far away and was thickened.
* **Stairs**: whole moss faces read as green carpet tiles. The moss tile became mossy stone (patches over rock grey), with moss biased toward the curbs.
* **Shrine**:
  * The first chigi were 1.9 studs long and looked like antennas. They are now shorter and chunkier.
  * The rope and shide hung inside the roof overhang, so they were invisible. They now hang lower.
  * The body was re-proportioned so that the walls follow the roof underside, there is no gap under the eaves, and the height is exactly 5.
  * The dark body became pale hinoki.
* **Gate**:
  * The ridge did not read from the front. It got a taller stack with a white band.
  * The eave corners got more upturn (0.5 → 0.9), and a vermilion fascia was added under the tile ends. This came from repainting existing faces, so it cost 0 triangles.
  * The shimenawa was thickened.
  * The shide looked like "+" signs. They became narrow, tall, offset strips that read as zigzag paper.
* **Torii**: the upturn was strengthened (1.15 → 1.45) and the black cap thickened, for a more anime silhouette.
* **Path scene**:
  * The first stand-in maples blocked the view and the camera looked across a huge bank. The trees were moved to the far side and behind, and the camera was raised.
  * Workbench shadows caused streaks (shadow acne) on the near torii, so shadows were disabled.
  * The side elevation hides the banks and maples, so the stair profile is visible.

## Problems caught by the automated checks and fixed
* **Floating parts**: two gate shide floated 0.04 studs in front of the thinner rope ends. They are now placed from the rope radius at each position.
* **Inside-out pieces**: the rope tassels were inside-out, because their lathe profile ran top-down. The volume check caught it.
* **Torii pillar tops** (1 back-facing ray hit): the open pillar ends stuck out of a lintel that was shallower than the pillar. The lintels were deepened (beams 1.8–2.2 deep) and the pillar tops capped.
* **Gate roof corners**: the four eave corners had a T-junction sliver (open edges). The gable-end faces now include the skirt vertex.
* **Overlapping slabs**: coplanar ridge edges between the two coping slabs, and between the two shrine roof slabs, made non-manifold edges. Each slab now runs 4 % past the ridge under the ridge cap.
* **Stretched UVs**: early paints used non-uniform scales (up to 6:1 on the banner pole). All planar paints now use one scale, the crossbar uses planar UVs, and the rope tube repeat length was matched to its circumference.
* **Budgets**: the gate was at 6 104 (fixed with 13 rafters per side instead of 16, lighter lamp curls and a 16-segment rope), and the banner at 310 (fixed with 4 cloth loops instead of 6).
* **The checks themselves**:
  * The first barrier box overlapped the door's own studs and crest; it is now measured from the frontmost relief.
  * The stair tiling check was also comparing step stones; it now compares only the curb pieces.
  * Stair stone jitter could push a tread edge 0.02 past y = +10. Jitter is now clamped at the segment ends, so the segment is exactly 20 long.

## Reproducibility
`build.py` always starts from `read_factory_settings(use_empty=True)`, and every model uses its own seeded RNG. I ran it twice from clean and compared every output byte for byte.
* **All 44 generated files except the .blend are identical**: atlas, all 6 FBX, the GLB, both JSON files and all 34 renders.
* The zip entries carry a fixed timestamp.

Two sources of noise had to be removed, as in batch A:
* **FBX exporter**: it derives node UIDs from Python's per-process randomised `hash()` and writes `datetime.now()` into the header. `build.py` patches both at runtime (stable SHA-1 UIDs, fixed date); the installed add-on is untouched.
* **PNG metadata**: Blender writes date stamps into the PNG files, so `build.py` re-saves them without metadata.

The `.blend` differs at byte level, because Blender writes runtime memory addresses into it. Re-opening it and hashing every collection, object, matrix, vertex, face, smooth flag, UV and material gives the same content on both runs.

## Notes
* **Lighting**: Workbench's studio light follows the camera and darkens vermilion a little in the renders. The atlas colour is exactly the palette's (200, 52, 38).
* **Harmless log noise**: the glTF exporter prints that the optional Draco and meshoptimizer libraries are missing from the bpy wheel. Compression is not used, so the GLB is unaffected. Mesa may print `EGL_BAD_MATCH` at shutdown, also with no effect on the files.
