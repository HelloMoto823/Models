# Batch D: landmarks at the edges of the forest

Everything in this folder comes from **one script, `build.py`**. It does all of the following:
* paints the atlas
* builds the 8 models
* runs the self-checks, and stops the build if any of them fails
* exports FBX, GLB and `.blend`
* renders the review images (5 views per model, lineup, scale check, collider check)
* re-imports every export to verify it
* writes `report.json` and `colliders.json`, and zips the folder

A full run takes about 80 s on 4 CPU cores. Two clean runs produce byte-identical files, except the `.blend` (see *Reproducibility*).

```
blender -b -P build.py              # Blender 4.x/5.x; Pillow must be importable from Blender's Python
python3 build.py                    # or with the bpy module:  pip install bpy==5.2.3 pillow  (Python 3.13)
options: --no-render  --no-verify  --no-zip      (dev only: --only=<id>  --keep-going)
```

Toolchain:
* Built and verified with **bpy 5.2.3 LTS** (the official Blender-as-a-module wheel), the same as batches A and B.
* Headless rendering needed the Mesa EGL libraries (`apt-get install libegl1 libegl-mesa0 libgl1-mesa-dri`).
* Renders use **Workbench**: studio light, back-face culling **ON**, light grey background, 16× AA, orthographic (the scale check's upper panel is a 35 mm perspective).
* The geometry core, checks, exporter patches and render code are batch B's, extended (see *What is new in the generator*).

## Deliverables

| file | content |
|---|---|
| `build.py` | the generator (atlas, models, checks, exports, renders, reports, zip) |
| `atlas.png` | 1024×1024, 4×4 tiles of 256 px (layout below) |
| `jura_batch_D.blend` | the 8 model collections, saved right after the build (atlas linked as `//atlas.png`) |
| `<id>.fbx` ×8 | one per model, atlas embedded |
| `jura_batch_D.glb` | all 8 models (22 meshes) |
| `colliders.json` | boxes per model (format below) |
| `report.json` | per model: size, bbox, triangles per mesh, budget, colours, collider count, info (door hinges, barrier pivot, sign face, mouth sizes...), every check result, geometry hashes; plus the atlas table and the export verification |
| `renders/` | 40 model views (front, back, left, right, three_quarter), `lineup.png`, `scale_check.png`, plus the extra `colliders_check.png` |

## What was built

Sizes are `[x, height, depth]` in studs from the mesh bounding box. They include the part sunk below z = 0 (the "sink" column).

| id | size | sink | triangles | budget | mesh parts |
|---|---|---|---|---|---|
| dwarf_gate | 85.41 × 58.86 × 33.73 | 1.0 | **4 610** | 6 000 | `_Stone` 1708, `_Statues` 1464, `_Rock` 646, `_Gold` 490, `_DoorL` 151, `_DoorR` 151 |
| blumund_marker | 16.55 × 15.12 × 9.75 | 0.8 | **824** | 2 500 | `_Wood` 360, `_Barrier` 240, `_Stone` 88, `_Roof` 76, `_Sign` 60 |
| milim_marker | 22.14 × 16.80 × 22.95 | 0.8 | **926** | 2 500 | `_Ground` 510, `_Monolith` 356, `_Stones` 60 |
| limestone_cave | 54.18 × 18.74 × 34.89 | 0.8 | **2 840** | 5 000 | `_Rock` 2523, `_Inside` 317 |
| limestone_rock_a | 6.00 × 4.30 × 4.84 | 0.6 | **320** | 600 | `_Rock` 320 |
| limestone_rock_b | 12.00 × 7.32 × 9.52 | 0.8 | **320** | 600 | `_Rock` 320 |
| limestone_rock_c | 20.00 × 10.29 × 13.44 | 0.8 | **320** | 600 | `_Rock` 320 |
| wolf_den | 30.48 × 16.57 × 22.24 | 0.9 | **954** | 2 500 | `_Bones` 528, `_Rock` 293, `_Inside` 133 |

Every budget holds. Every mesh is triangles only, with UV map `UVMap` and the single material `Atlas`.

### dwarf_gate (the gate of the Armed Nation of Dwargon)
The portal is **40 wide (x ±20) and 36 tall** (top of the stepped crown), set into a cliff. Origin: centre of the portal footprint on the ground; the closed doors sit at y = −0.2…0.6.

**Steps and landing**: the road ends at the first riser (y = −15.5). Three risers of 1 stud, treads 2.5, **26 wide**, lead up to a 40-wide landing at z = 3.

**Facade**: squared dwarven architecture, all straight blocks.
* **Walls and steps** use the new `masonry` tile: ashlar courses 2 studs tall, laid on a world grid (every face is cut at multiples of 8 studs from z = 1). The course lines therefore run straight across neighbouring blocks, and land on the step edges and the landing.
* **Stepped arch**: two recessed orders frame the doorway. The outer order (masonry, at y = −2) and the inner order (plain dwarf stone, at y = −0.8) both have squared, corbelled tops (3 steps each). Each order has a gold outline that follows its steps.
* **Pillars**: two thick chamfered pillars in plain dwarf stone, 5 wide, protruding about 3.2 from the wall. Each has a two-tier base and a two-tier capital with a gold ring.
* **Rune bands in gold trim**: each pillar wears two rune collars (front and both sides) between gold rings. A 39-stud **rune frieze** runs across the entablature between two gold trims. Smaller bands sit on the statue plinths. The runes are abstract angular glyphs, not a real script, and spell nothing.
* **Top**: a cornice with a gold line, and a two-step crown carrying a gold three-peak emblem (the Canaat range). A gold anvil and two gems sit in the stepped tympanum above the doors.

**Doors**: closed double door in **dark iron with gold trim**.
* Each leaf has a gold border, two gold bands with studs, and half of a gold lozenge emblem.
* The leaves are separate meshes (`dwarf_gate_DoorL/R`), like the tengu gate. Their hinge axes are in `report.json → dwarf_gate.info.doors`.
* The facade behind them is solid, because the city is off-map.

**Guardian statues** (two, at x = ±27, y = −6), **18 studs tall** from the plinth top to the horn tips. Each stands on a 2.5-stud rune plinth.
* The figure is a stocky dwarf warrior: boots, armoured kilt, broad chest and belt with a gold buckle, pauldrons, a horned helmet, a heavy brow over a dark eye band, a nose, a moustache, and a long wedge beard.
* Both hands rest on the pommel of a great **double axe planted head-down**. The axe has a dark iron bow-tie head and a gold collar.

**Rock shoulders**: 18 boxy faceted cliff blocks (rock grey, moss on up-facing tops).
* A cliff body sits behind the facade, so the back of the model is all rock.
* Shoulders wrap both side walls of the portal, out to x ≈ ±43.
* Crags sit above the crown, kept clear of it, and peaks reach z ≈ 58: **about 20 studs of rock** on both sides and above.
* The statues stand in front of the shoulders.

### blumund_marker (western border post toward the Kingdom of Blumund)
Origin: centre of the ground footprint. The road passes on the −X side; travellers come along −Y and face the sign.
* **Boundary pillar**, **10 tall**: a moss-footed stone plinth, a tapering square shaft with a collar, and a cap with a pyramid top.
* **Signboard arm**: a timber arm with a brace reaches about 4 studs out over the road edge. A **blank** board hangs from it on two iron hangers: a plank frame with a cream face on both sides, ready for the game's text. The board is its own mesh, `blumund_marker_Sign`. Its face rectangle (centre, size, normal) is in `report.json → info.sign_face`.
* **Toll barrier, lifted**: a vermilion/cream striped pole, 13.1 long, mounted on the side of a timber post with an iron axle and washer. It is lifted at 76°, with a stone counterweight on the short arm.
  * It is its own mesh, `blumund_marker_Barrier`, so the game can lower it.
  * Rotating it **−76° about +Y** through the pivot (`info.barrier.pivot`) lays the pole level across the road toward −X.
* **Guard booth, 6 × 6 × 9**: a stone footing, plank walls on corner posts, and top plates.
  * Openings: a counter window toward the incoming road, a small window toward the road side, and a framed door on the far side.
  * Gabled plank ends and a **dark slate roof** with a ridge beam; the ridge top is at z = 9.
  * The interior faces are dark, so both windows read as openings.

### milim_marker (eastern border of the Demon Lord Milim's lands)
* **Monolith, 16 tall**: a rough, chamfered, slightly leaning slab of **dark stone**. It has a broken two-peaked top and a charred foot.
* **Carved dragon-claw emblem**, made as faces with no decal and no text: a palm, three curved talons and a dew claw.
  * It is cut 0.34 deep into the planar front face with a constrained Delaunay triangulation (as the batch B banner emblem).
  * The carved floor is **ember**, so the claw glows from far away.
* **Stones**: three smaller jagged dark shards lean outward around it, at 330°, 212° and 95°, leaving the front clear.
* **Ground disc**: radius about 11, a low rock grey disc with a jagged rim. The rim sinks to −0.3; the centre is 0.2 above the ground.
  * A radial **scorch burst** in the `charred` tile (charred with ember cracks) spreads from the monolith's foot.

### limestone_cave (the Lizardmen's limestone caves at the lake marsh)
* **Mouth**: **22 wide, 14 tall**, with a 1.6-stud brow overhang.
* **Outcrop**: a low white limestone outcrop. The shell alone is **49.4 × 29.4 × 17.4 tall**.
  * Its rounded, water-worn shape comes from smooth noise, lobes and cross-sections that drift sideways.
  * Five large rounded limestone masses merge into it, and two loose boulders lie at its foot. These widen the whole model to 54.2 × 34.9, 17.9 tall above the ground.
* **Stalactites**: six, irregular, hanging from the brow and the first studs of the ceiling.
* **Inside, going dark**: the tunnel walls and floor use the `cave_dark` fade tile.
  * The fade goes from limestone in shadow at the mouth to black **about 6.7 studs in**.
  * A **cave-black back plate** closes the tunnel **15 studs inside** the mouth (y = +2). Players never see a void.
* **Floor**: a strip at z = 0.08, so it never z-fights with the terrain.
* Rock and interior are separate meshes, `limestone_cave_Rock` and `limestone_cave_Inside`. Together they form one closed shell.

### limestone_rock_a / b / c
* Loose white limestone boulders, **6, 12 and 20 studs** across (the largest horizontal size, exact), about 0.6 as tall.
* Each is a 320-triangle icosphere with smooth low-frequency lumps, a flattened top and a flat sunk base: rounded, water-worn, faceted.
* **b and c** have **slightly mossy tops**: a few contiguous moss patches on the flattest top faces.

### wolf_den
* **Rock**: a big mossy rock, **25.5 × 14.8 × 15.7** for the rock alone (asked: about 26 × 14 × 16). It has a contiguous moss cap, two side rocks and a top rock bedded in for a lumpy silhouette.
* **Burrow**: a dark mouth **10 wide, 7 tall**, dug under the rock's front. Inside, a burrow 7 deep fades to black and ends at a back plate.
* **Claw marks**: the `claw` tile sits on a few rock faces left and right of the mouth.
* **Bones** (cream) are scattered in front: three long bones, three ribs and a skull, all resting on the ground.

## Atlas tile layout (`atlas.png`, 4×4 × 256 px, UV inset 4 % per tile)
Rows are counted from the top of the PNG. A face's UVs always stay inside one tile's 4 % inset; `build.py` checks every face and aborts otherwise. The measured mean colour of each tile is in `report.json → atlas.tiles`.

| row \ col | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| **0** | `stone_grey` rock grey (126,124,118), stone blotches | `dwarf_stone` dwarf stone (148,140,128), stone blotches | `masonry` dwarf stone ashlar (4 courses × 2 blocks, running bond, mortar + bevel) | `runes` gold trim (204,166,82) glyphs engraved in darkened dwarf stone, 2 rows × 3 |
| **1** | `gold` gold trim (204,166,82), flat | `dark_stone` dark iron (58,58,64), stone blotches | `charred` charred (44,37,32) + ember (196,84,32) cracks | `ember` ember (196,84,32), flat |
| **2** | `limestone` limestone (232,228,214), soft blotches | `cave_dark` fade: limestone ×0.58 (top) → **cave black (16,15,14)** at v ≤ 0.5 | `moss` moss (84,122,60) clumps | `claw` rock grey stone + 4 parallel claw gouges |
| **3** | `wood` bark (92,65,43), planks + grain | `wood_light` bark light (109,77,47), timber grain | `bone` cream / bone (226,216,190), flat | `vermilion` vermilion lacquer (200,52,38), flat |

**Painting**:
* Flat tiles carry about ±3 % smooth noise.
* Stone tiles carry soft round blotches; random UV windows give each facet its own shade.
* **New in this batch**: patterned tiles are painted **periodic over the inset window**, the part of the tile UVs can actually reach. The batch B tiles were periodic over the whole tile. This makes the masonry courses continue exactly across face and block borders.

**Special mappings**:
* **Runes**: each band is cut into equal segments. Each segment shows one glyph row (3 glyphs) at uniform scale.
* **Cave interiors**: v follows the depth behind the mouth on each face's own plane (one scale for u and v), so the fade is continuous and unstretched.
* **Back plates**: fitted into the black half of the `cave_dark` tile.

**UV quality**: the worst anisotropy per model is 1.00–1.12. The exception is the cave at 1.35, on non-planar tunnel quads.

**Tile use**:
* stone_grey → gate cliff, pillar, disc, den rock
* dwarf_stone / masonry / runes / gold → gate
* dark_stone → Milim's monolith and shards, the gate's iron doors and axes, the booth roof (slate), hangers and axle, the booth interior
* charred / ember → scorch burst, monolith foot, carved claw
* limestone → cave, boulders
* cave_dark → cave and burrow interiors and back plates
* moss → mossy tops
* claw → den
* wood / wood_light → booth, sign, barrier post
* bone → bones, sign face, barrier stripes
* vermilion → barrier stripes

## Conventions used (for the importer)
* **Units and axes**: 1 Blender unit = 1 stud, with `scene.unit_settings.scale_length = 0.01`. Z is up, the front faces −Y, and each origin is the centre of the base at z = 0.
  * Foundations, rock bottoms and plinths sink 0.6–1.0 studs; the lowest point per model is between −1 and 0 (checked).
* **Origins**:
  * gate: centre of the portal footprint (door plane at y ≈ 0)
  * cave: centre of the outcrop footprint, mouth toward −Y
  * den: centre of the rock footprint, burrow toward −Y (downhill)
  * Blumund: centre of the ground footprint (pillar plinth + booth footing)
  * Milim: the monolith
  * rocks: their centre
* **Collections and names**: each model is one collection named by its id, with meshes `<id>_<Part>`. No armatures, no bones (no trees in this batch).
* **Transforms**: identity transforms, no modifiers (both checked).
* **FBX export**: per model with `object_types={'MESH'}`, `apply_unit_scale=True`, `apply_scale_options='FBX_SCALE_UNITS'`, `axis_forward='-Z'`, `axis_up='Y'`, `mesh_smooth_type='OFF'`, atlas embedded.
  * Every FBX is re-imported and gives back the same object names, triangle counts and size in studs, with 0 n-gons, one UV layer and the material `Atlas` (`report.json → export_verification.<id>.ok = true` for all 8).
* **GLB**: all models in one file; 1 m in the GLB = 1 stud. The re-imported GLB contains all 22 meshes.
* **`colliders.json`**: `{"<id>": [[[cx,cy,cz],[sx,sy,sz],yawDeg], ...]}` in Blender coordinates (Z up, studs). Each entry is the box centre and full size; yaw is about +Z, and all boxes in this batch have yaw 0. `renders/colliders_check.png` draws every box as a red cage over its model.

  | model | boxes | what |
  |---|---|---|
  | dwarf_gate | 30 | 2 steps, landing, facade + closed doors, entablature + cornice, crown, 2 pillars, 2 × (statue plinth + figure), 18 rock blocks |
  | blumund_marker | 6 | booth, booth roof, pillar plinth, pillar, barrier post, counterweight |
  | milim_marker | 4 | monolith, 3 stones |
  | limestone_cave | 15 | outcrop boxes (2 per side along the tunnel, lintel over the mouth, ceiling, back mass), **back plate**, 7 rounded masses / boulders |
  | limestone_rock_a/b/c | 1 each | the boulder (footprint 80 %, top at 90 % of the height) |
  | wolf_den | 8 | 2 side masses, rock over the burrow, back mass, burrow back plate, 3 side / top rocks |

  * Not collidable: the lifted barrier pole and the signboard (both above head height), the bones, the Milim ground disc (flat) and the gold trims.
  * The cave and burrow mouths stay open, so players walk in up to the back plate.

## Decisions taken (no questions possible)
1. **Item dimensions are read as width × depth × height**: the booth "6×6×9" is clearly 9 tall. So the den rock "26×14×16" is 26 wide, 14 deep and 16 tall, and the outcrop's "50×30 footprint" is x × y.
2. **Statues "18 studs tall"** is the figure from the plinth top to the horn tips; each stands on a 2.5-stud rune plinth.
   * They hold their axes planted head-down, in a calm guard pose, which keeps the face and beard visible.
   * The axe heads are **dark iron with a gold collar**, so they read against the stone and the cliff.
3. **The portal's 40 × 36** is the architecture: pillars to x = ±20, crown top at z = 36. The rock adds about 20 studs on each side and above; the cliff body behind the facade makes the back all rock, ready to sit against a mountain.
4. **Masonry vs plain stone**: an all-masonry facade read as one flat wall in the first renders. Walls, steps and the outer order are masonry; pillars, the inner order, cornice and crown are plain dwarf stone with gold trims.
5. **"Dark stone"** (Milim) uses the palette's dark iron (58,58,64) painted as stone, instead of a new colour. The same `dark_stone` tile serves the gate's forged-iron doors, the booth's slate roof and its shadowed interior.
6. **The carved claw glows ember** (the palette's ember), to feel powerful and slightly ominous. The ground disc is rock grey, so the charred scorch burst stands out on it.
7. **New colour**: **cave black (16,15,14)** for the "black-coloured" back plates and the deep end of the interior fade.
8. **Claw tile**: batches A/B have no bark-claw tile to copy, so a `claw` tile was painted: 4 parallel tapered gouges with a dark core and a lit rim on rock grey stone.
9. **Blumund layout**: the road passes on the −X side of the model. The barrier would be lowered across it; the sign arm reaches over it and faces travellers coming along −Y.
   * The barrier is **red/white (vermilion/cream)**, the usual toll barrier look.
   * The pivot is side-mounted, so the counterweight clears the post.
   * Blumund's colours are unknown, so no flag or crest was added.
10. **Moss**:
    * none on the cave: not asked, and isolated moss faces read as a checkerboard on white limestone
    * a contiguous cap on the den: a mossy rock
    * small patches on rocks b and c: "slightly mossy"
    * moss on the gate cliff's up-facing tops
11. **The den burrow is level**: it cannot descend, because models may sink at most 1 stud. It goes dark within 4 studs instead. On a slope the rock's back simply goes into the rising terrain.
12. **Cave footprint**: the outcrop shell is 49.4 × 29.4 (about 50 × 30). The merged masses and two loose boulders, added for the water-worn look, bring the model's box to 54.2 × 34.9.
13. **Views**: "left" is the camera on −X looking toward +X, "right" is from +X, and the 3/4 view is from the front-right above (as batch B). The human silhouette stands at the model's front-right, so it never hides the model in any view.

## Self-review: automated checks
All of these pass. They are written to `report.json → models.<id>.checks`, and the build aborts if any fails.
* **Budgets**: all within limits (table above).
* **UVs**: every face sits in one tile, inside the 4 % inset, on the tile its paint asks for.
* **Inverted normals / holes ("back-face culling" ray test)**: 5000 rays per model, from all around and above the ground. The first face hit must face the ray. Result: **0** back-facing first hits on all 8 models (2 030–4 116 hits per model).
* **Floating parts**: every primitive (1 to 208 per model) must touch the ground or something grounded. Result: **0** floating pieces. Every closed piece has a positive signed volume.
  * The inward-facing cave and burrow skins are checked together with their outer skin as one closed shell: the cave outcrop shell encloses +13 550 stud³.
* **Z-fighting (new)**: no two triangles may face the same way, lie in the same plane and overlap above the ground. Result: **0** on all models.
  * The scan buckets triangles by plane and clips them in 2D.
  * Each build runs it on a planted overlapping pair first, and the build fails if the scan does not see it.
* **Mouths clear (new)**: no rock triangle inside the walk-in space of the cave mouth and of the burrow.
  * No rock piece (boulder, mass, crag) crosses the tunnel skin, so nothing pokes through into the dark interior. A planted intruding boulder is caught (tested during development).
* **Open edges**: only where a piece is deliberately embedded (trim backs, skipped hidden faces, bone shaft ends, the rim and feet where the Rock and Inside meshes meet). Counted per mesh in `report.json`.
* **Transforms, modifiers, sink**: identity transforms, no modifiers, lowest point between −1 and 0 for every model.
* **Export round-trip**: all 8 FBX files and the GLB pass.

## Self-review: problems found in the renders and fixed
* **Gate, first version**:
  * The rock was a pile of separate round boulders, with sky showing between them.
  * The portal's side walls were exposed, so it looked like a box stood in front of rocks rather than carved into a cliff.
  * The all-masonry facade flattened the pillars, the stepped orders and the crown.
  * The statues' faces and axes did not read.
  * Fixes:
    * The cliff was rebuilt: a back body, shoulders wrapping the portal sides, top crags placed clear of the crown, and boxier blocks.
    * The pillars, inner order, cornice and crown became plain stone with gold trims.
    * The statues got an iron bow-tie axe head with a gold collar, a dark eye band and a bigger beard.
* **Gate back**: a seam between the two back blocks showed the masonry wall. The blocks now overlap.
* **Cave, first version**: it looked like a regular "loaf" or tunnel hut, with checkerboard moss and evenly spaced, tooth-like stalactites. Fixes:
  * stronger low-frequency noise and lobes, plus sideways drift of the cross-sections
  * five rounded masses merged in
  * no moss
  * six irregular stalactites
* **Den**: the claw tile landed on every front face and turned into a pattern. It is now on a few faces beside the mouth only. The moss became a contiguous cap (noise field instead of per-face chance), and extra side and top rocks broke the boxy silhouette.
* **Booth**: the lit plank interior made the windows invisible. The interior faces are now dark.
* **Rock c**: moss covered almost the whole top. It now forms a few patches.
* **Renders**:
  * Lineup labels overlapped. Slots now take at least their label's width, and the lineup uses 3 rows for a larger common scale (8.2 px/stud).
  * The scale check's plan panel overflowed the sheet.
  * The human silhouette stood in front of models in the side views.

## Problems caught by the automated checks and fixed
* **Z-fighting**:
  * The first z-fighting scan used BVH overlap, which never reports coplanar pairs, so it was blind. Its self-test exposed this, and it was rewritten.
  * The new scan found about 500 coplanar overlaps above the ground (468 on the gate, 34 on the booth). Examples:
    * gold trim pieces overlapping at the stepped-outline corners and door corners
    * five facade blocks sharing the x = ±20 end plane and the y = 9 back plane
    * the crown top coinciding with the back wall top
    * the statue's two hands meeting at x = 0, and the brow flush with the head sides
    * door-frame pieces and the two roof-slab ends on the booth
  * All were separated by 0.01–0.1 stud offsets. The scan is now a permanent check.
* **UV anisotropy over 1000 on the cave interior**: the fade v was measured from a fixed origin, and the brow overhang pushed it past 1, where it was clamped.
  * v is now measured from each face's frontmost vertex, with the fade origin 1 stud in front of the brow.
  * The den's jittered back cap and back plate were non-planar, which stretched their UVs. Both caps are now planar.
* **Ribs**: their tube UVs used a 3-stud repeat for a 0.6-stud circumference. The repeat now matches the circumference.
* **Inside-out pieces**:
  * Open rune slabs and sign panels were flagged as inside-out (negative volume). They are now closed.
  * The tunnel skins are inward-facing by design and are now checked with their outer skin (see above).
* **Floating part**: the skull floated 0.03 above the ground. It now rests on it.
* **Soffits** (caught in code review before the first render): the stepped arch's upper bands skipped their bottom faces, which would have left the soffits open. Only the lowest band skips its bottom now.
* **Boulder triangle count**: Blender counts icosphere subdivisions from 1, so the boulders came out at 80 triangles instead of 320. Fixed.

## Reproducibility
`build.py` always starts from `read_factory_settings(use_empty=True)`, and every model uses its own seeded RNG. I ran it twice from clean and compared every output byte for byte.
* **All 55 generated files except the .blend are identical**: atlas, 8 FBX, the GLB, both JSON files and all 43 renders.
* The zip differs only because it contains the `.blend`; its entries carry a fixed timestamp.

As in batches A and B, two sources of noise had to be removed:
* **FBX exporter**: `build.py` patches the per-process random node UIDs and the header date at runtime; the installed add-on is untouched.
* **PNG metadata**: Blender writes date stamps into the PNG files, so `build.py` re-saves them without metadata.

The `.blend` differs at byte level, because Blender writes runtime memory addresses into it. Re-opening it and hashing every collection, object, matrix, vertex, face, smooth flag, UV and material gives the same content on both runs (22 objects).

## What is new in the generator (compared with batch B)
* `gbox`: boxes cut on the paint's world grid, so periodic tiles (masonry) continue across faces and blocks.
* `rune_slab`: rune bands with uniform-scale glyph segments.
* `crag`: boxy cliff blocks.
* `boulder`: water-worn icosphere boulders.
* `mound`: rock mass with an arched mouth, a blind tunnel, a floor strip, a back plate and a bottom cap, all one closed, consistently wound shell.
* `carve_face`: emblems carved into a planar face via a constrained Delaunay triangulation.
* `fade_uvs`: unstretched depth-fade UVs for interiors.
* Checks: coplanar-overlap (z-fighting), mouth clearance and tunnel-skin crossing.
* Renders: the collider-cage sheet.

## Notes
* **Lighting**: Workbench's studio light is camera-relative and has no ambient occlusion. Interiors are dark through their texture (cave fade, dark booth interior), so they also read dark in Roblox.
* **Harmless log noise**: the glTF exporter prints that the optional Draco and meshoptimizer libraries are missing from the bpy wheel; compression is not used. Blender 5.2 prints a deprecation notice for `Material.use_nodes`; the material is still built the same way as in batches A and B.
