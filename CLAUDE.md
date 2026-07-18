# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Three.js browser-based ship designer for the game **SpaceCraft**, plus the
reverse-engineering tooling used to extract and convert its assets from the
game's own files. See [NOTICE.md](NOTICE.md) for licensing — this repo mixes
original code (MIT) with reference content extracted from the game itself
(not covered by that license).

## Game installation

The actual game (SpaceCraft) is installed at
`D:\SteamLibrary\steamapps\common\SpaceCraft`. `res.pak` there is the source
that `pak_out`/`pak_out_full` were extracted from. `hlboot.dat` in that same
directory is the compiled **HashLink bytecode** for the game's own Haxe
logic (not just data) — use it (via `hlbc`, see
[`tools/game_logic_notes.md`](tools/game_logic_notes.md)) whenever a question
is about actual game *logic/formulas* (damage, combat, movement math) rather
than static balance data, since `data.cdb` alone only has the latter.

**Use `tools/heaps_ref/hlbc_src/target/release/hlbc.exe`, not
`tools/heaps_ref/hlbc/hlbc.exe`.** The game updated past what the prebuilt
binary's bytecode parser supports — it fails with `Error: Malformed
bytecode (Invalid type kind '23')` against the current `hlboot.dat`. The
`hlbc_src` checkout (its own separate git repo, cloned from upstream
`Gui-Yom/hlbc`) has a local patch (committed there, not pushable — no fork
to push to) adding support for that new type kind (`Guid`) plus a newer
`Catch` opcode, and its `target/release/hlbc.exe` is already built from
that patch. `tools/heaps_ref/` is entirely gitignored in this repo, so
this fix isn't reproduced by a fresh clone — if `hlbc.exe` starts failing
on type kind 23 again after a fresh checkout, this is why; the local
`hlbc_src` clone with its patch commit needs to exist and be rebuilt
(`cargo build --release`) before `hlbc` works again. See
[`tools/game_logic_notes.md`](tools/game_logic_notes.md)'s note on this
for the full patch diff summary.

## Keeping the finding logs current

[`tools/game_logic_notes.md`](tools/game_logic_notes.md) and
[`tools/hmd_format_notes.md`](tools/hmd_format_notes.md) are numbered-finding
investigation logs, not just this file - both already do this well in
several places (e.g. `hmd_format_notes.md`'s finding 15 disproving finding
schemes it had earlier assumed necessary). When a new finding supersedes or
invalidates an earlier one, correct/annotate that earlier finding in place
(or clearly mark it superseded) rather than just appending a new finding
number and leaving the old, now-wrong one sitting there uncorrected - these
logs are read and trusted as current state, not a changelog.

## Ship Builder

Launch with `start.bat` (double-click) or `python -m http.server 8765` then open `http://localhost:8765`.

### Files

| File | Purpose |
|------|---------|
| `shipbuilder/index.html` | App shell — palette, viewport, inspector panels |
| `shipbuilder/style.css` | All styling (Orbitron/Rajdhani fonts, dark theme, stats panel, slot grid) |
| `shipbuilder/js/main.js` | All Three.js logic (~950 lines) |
| `shipbuilder/js/data.js` | Part data loading, fan-stat lookup (`statsFor`), ship stat calc functions |
| `shipbuilder/js/meshLoader.js` | Manifest fetch, geometry loading, cache-busted `.bin` fetches |
| `shipbuilder/ship_editor_data.json` | Complete part catalogue: 77 hull + 63 module parts, all shapes and material variants |
| `shipbuilder/ship_stats_data.json` | Fan-sourced stat values (weight, frame, thrust, shields, etc.) keyed by part name |
| `shipbuilder/ship_meshes/` | `.bin` mesh files + `_manifest.json` |
| `shipbuilder/ship_icons/` | Part icon `.webp` files (one per part ID) |
| `shipbuilder/ship_shapes/` | Shape thumbnail `.webp` files |
| `shipbuilder/start.bat` | Launcher: tries Python then Node.js, opens browser automatically |

### Part model (`ship_editor_data.json`)

Each part has `id`, `name`, `group`, `kind`, `mount`, `dims`, `stats`, `shapes`, `color`/`grad`.

- `kind: 'build'` — hull frames, cockpits, wings, engines (77 parts). Each provides **1 internal module slot**.
- `kind: 'module'` — cargo, FTL, shields, batteries, etc. (59 parts).
  - `mount: 'inside'` — placed into a hull slot via the slot sprite system.
  - `mount: 'outside'` — placed on the grid surface like hull pieces.

**`dims` axis convention is NOT uniform, and NOT even consistent within a single
part kind — this has caused real bugs.** `partDims()` in `main.js` converts each
part's raw `dims` array into Three.js [X, Y, Z] extents:
- Hull frames (the general fallback case): raw `dims` is `[L, W, H]` in a "game"
  convention where W and H are NOT already in Three.js order — `partDims` swaps
  them (`[l, w, h] → [l, h, w]`) to get Three.js X/Y/Z. Confirmed correct for
  hull frames.
- Cockpits: raw `dims` is destructured as `[l, h, w]` (already Three.js-ish
  order) and reordered for the 90°Y rotation `fitGeom` applies to cockpit meshes.
- Outside-mount modules: `partDims` uses raw `dims` **directly, no swap**, and
  `dims` is derived as `[hmdX, hmdZ, hmdY]` from the mesh's own bounding box
  (matching the `rotateX(-90°)` mapping threeX=hmdX, threeY=hmdZ, threeZ=hmdY).
  For compound multi-part tool/module meshes, that bounding box must come from
  applying each part's *real* per-model transform (position/rotation/scale read
  from the HMD file's own `models[]` hierarchy — see finding 8 in
  `tools/hmd_format_notes.md`), not from raw, untransformed geometry. An earlier
  attempt to fix per-item dims by manually guessing an axis swap or eyeballed
  scale (e.g. for Simple Hose Pump, Hi-Pi Laser) was **fully superseded** once
  the real per-model transforms were read correctly — those items' current
  `dims` values come from the mesh's own stored transform data, not a guess.
  If a new outside module looks wrong-shaped or wrong-sized, first re-derive
  its `dims` from `tools/hmd_convert_v2.py`'s output bbox (which applies real
  transforms) rather than guessing an axis swap or scale factor by eye.

### Module slot system (`main.js`)

Inside modules (`isInsideMod(part)`) bypass the grid occupation system entirely and are instead **assigned to a hull piece** via `slotOwner: hullEntry`.

**Key functions:**
- `placeInSlot(part, hullEntry)` — places module at hull center; swaps if slot already occupied.
- `syncSlotModule(hullEntry)` — repositions the slot module after hull drag or rebuild.
- `refreshSlotSprites()` — rebuilds Three.js Sprite billboards over all hull pieces; visible only when Modules tab is active.
- `setSlotHighlight(hullEntry, on)` — highlights the hovered slot sprite.

**Slot sprite textures** (canvas-based, created once at module load):
- `TEX_SLOT_EMPTY` — dashed white border (no module).
- `TEX_SLOT_OCCUPIED` — not used directly; replaced by `getSlotOccupiedTex(part)`.
- `TEX_SLOT_HOVER` / `TEX_SLOT_HOVER_SWAP` — fallback hover states.
- `getSlotOccupiedTex(part)` — canvas texture with part icon + cyan border; async image load updates texture.
- `getSlotHoverTex(part, isSwap)` — hover preview showing selected module's icon; white border = place, amber = swap.

**Interaction:**
- Switch to **Modules tab** → slot sprites appear over all hull pieces.
- Select an inside module → hover over sprites shows the module icon as preview (white = empty, amber = will swap).
- Left-click sprite → place or swap.
- Right-click sprite → remove installed module.
- Dragging a hull piece moves its slot module with it (`syncSlotModule`).
- Removing a hull piece cascades to remove its slot module.

**Save/load:** `slotOwnerIdx` (index into the placed array) persists slot assignments across clipboard save/load.

### Ship stats panel (`updateShipStats` in `main.js`)

Shown in the inspector when parts are placed. Sections:
- **Verdict banner** — flight-ready / not ready.
- **Viability checks** — Cockpit, Engine, Thrust/Mass, Integrity, Sys. support, Power, FTL cap. (if FTL present), Module slots.
- **System Support bar** — SP used vs capacity.
- **Structure** — Weight, Frames, Integrity (fan formula: `200 − 7w²/25f`), Maneuverability (`280×steering/w^1.5`).
- **Propulsion** — Thrust, Force.
- **Power** — Gen, Usage (`PowerUsage + EngineConsumption`), Net, Battery, Recharge, Heat cap.
- **Module Slots** — dot grid (one dot per hull piece, cyan = occupied), foot label.
- **Cargo** — Solid, Liquid, Mag fuel, FTL cap.
- **Combat & Heat** — Shields, Heat gen. (fan data).

Stats sourced from `part.stats` (game data in `ship_editor_data.json`) plus `statsFor(name)` (fan data in `ship_stats_data.json`).

## HMD Mesh Pipeline

The source game (SpaceCraft) runs on **Heaps.io** (a Haxe game engine), or a modified/customized build of it — confirmed by `res.pak`'s directory format, the `HMD` mesh magic, and a `.prefab` object-tree format that matches Heaps' `hxbit` binary serializer conventions (tag bytes 0/1/2/3/4/5/6/7 for null/false/true/int/float/object/string/array — see `tools/prefab_parse.py`). Assume Heaps/Haxe conventions when reverse-engineering any new binary format from `res.pak`.

See [`tools/hmd_format_notes.md`](tools/hmd_format_notes.md) for full format documentation, coordinate transforms, vertex/index buffer layouts, and the .bin output format. **Keep this file up to date** with any new findings discovered during conversion work - see "Keeping the finding logs current" above: correct/supersede a stale finding in place, don't just append a new one on top of it.

**All tools must be saved to `tools/`** — never write a tool only in memory or in a code block. Save every script immediately after writing it, even if incomplete.

**Use only the in-game extracted files from `pak_out` as reference for reverse-engineering.**

#### Current state

- Production HMD format (magic `HMD\x06`, disc=0x02): fully decoded. Three ring-buffer variants documented in `hmd_format_notes.md`.
- **130 of 130 shapes from pak_out — all real, none excluded.** All 11 hull sizes complete, including 8x3x1's shape N, whose pak entry looked like a genuine alternate format for a long time (raw index data at byte 0, no parseable HMD header) until the full `pak_extract.py --all` re-extraction revealed it was just another stale-`pak_out` casualty of the same disc=0x02 position bug (finding 17) — see finding 25 in `hmd_format_notes.md`.
- `shipbuilder/ship_editor_data.json` — complete with all hull sizes and all material variants. No edits needed there for hull data; outside-module `dims` are being recalibrated as real per-part transform data is confirmed (see below).
- `shipbuilder/ship_shapes/` — all 14 shape thumbnails are real crops from the in-game `ui/icons/BlocksShapesIcons.png` sprite sheet, positioned via `data.cdb`'s `icon` sheet (see finding 24 in `hmd_format_notes.md`).
- **22 outside-mount parts total** (up from 18 — see finding 11 in `hmd_format_notes.md`: `MiningTool0`, `MiningTool3`, `MiningTool3_OC`, and `PathwayPuncher` were missing from the catalogue entirely, found by cross-checking every `data.cdb` item of the relevant types rather than assuming the existing list was complete). **All Tools- and Decoratives_Parts-category mesh files convert with real per-part transforms** via `hmd_convert_v2.py`/`hmd_parse_heaps.py` (see finding 8) — this is the current, correct pipeline for any compound tool/module mesh, and now includes `PathwayPuncher` too: it was never a legacy/alternate format at all, just a real production HMD file mis-extracted from the pak with a 13-byte offset error (see finding 15). The 3 Decoratives_Parts items once thought to need an older fallback path (Spot_Light_01, Spot_Light_Barrel, Aerator_Spot_01) never actually needed one — see finding 23.
- **Known open issue:** `RadarMK1`'s source file mapping (`Tools/Radar.fbx`) is an unconfirmed guess (no file matches its actual part id in the pak) — confirmed likely wrong now that real transforms are applied (produces an elongated 0.33×0.4×1.7 shape, contradicting the known in-game ~1×1×1 size). Left at `dims: [1,1,1]` pending a real source file; do not "fix" this by guessing a scale/rotation, the underlying mesh is probably just the wrong asset. `Simple_Mining_Laser` and `Scanner` have the same unconfirmed-mapping caveat but produced plausible-looking dims, so they're lower priority.
- **Two more real bugs found and fixed after finding 8** (see finding 9 in `hmd_format_notes.md`): (1) `hmd_convert_v2.py` was only applying each model's own transform, not composing up its `parent` chain — harmless for files where every part parents to an identity root, but silently misplaced children of a real geometry-bearing parent (fixed on `MiningTool1_OC`, `ColdLaser`, `HiPiLaser`, `HiPi_Overclocked_Laser`, `RadarMK1`, `Simple_Mining_Laser`, `SmartRadar`). (2) `HiPiLaser.fbx`/`HiPi_Overclocked_Laser.fbx` were real, cleanly-converting files that were nonetheless the *wrong* asset for those items — a file existing and converting without errors is not proof it's correct; always cross-check `data.cdb`'s `visual.model` field when a mapping was chosen by name-similarity rather than verified. Both items now correctly source from `MiningTool_Medium.fbx`.
- **Material colors were invented placeholders, now replaced with real extracted colors** (see finding 10 in `hmd_format_notes.md`). The game's `*_basecolor.png` files are actually BC1/DXT1 or BC3/DXT5 compressed textures (reverse-engineered from scratch — no reference implementation existed for this format), decoded and averaged per material name by `tools/extract_material_colors.py` into `tools/material_colors.json`. `hmd_convert_v2.py` uses these when available, falling back to the old keyword-matched placeholder color otherwise (35 of 55 real material names are covered). The keyword-based `role` assignment is unchanged — it still drives PBR shading params, only the flat display color is now real where extractable.

#### Conversion tools

| Tool                          | Purpose                                                            |
|-------------------------------|--------------------------------------------------------------------|
| `tools/hmd_parse_prod.py`     | Legacy heuristic parser for production HMD v0x06 (hull frames/engines): `parse_prod_hmd()`, `parse_material_groups()`, `_parse_attr_blocks()`, `read_verts_f16()`, `read_indices_le_u16()` — does NOT read the real model-node hierarchy (see finding 8 in `hmd_format_notes.md`); superseded by `hmd_parse_heaps.py` for anything with per-part transforms |
| `tools/hmd_parse_heaps.py`    | **Authoritative** HMD reader — faithful port of Heaps' own `hxd/fmt/hmd/Reader.hx`. Reads the real `models[]` scene-node hierarchy (each node's position/quaternion rotation/scale, separate from raw geometry) plus `stride_bytes()` (real per-vertex byte stride — the raw file `stride` byte is a component count, not a byte size) |
| `tools/hmd_to_bin.py`         | CLI converter: auto-detects format and writes .bin; entry point for hull/engine conversions |
| `tools/hmd_convert_v2.py`     | Transform-aware converter for compound multi-part meshes (tools/modules): applies each real model node's scale→rotate→translate before merging, using the file's own material index per group |
| `tools/batch_convert_hulls.py`| Batch converter: converts all Main_Structures sizes from pak_out, updates `_manifest.json` |
| `tools/batch_convert_modules.py` | Batch converter for outside-mount modules using the old heuristic path (superseded by v2 below, kept for its `MODULE_SOURCES` mapping) |
| `tools/batch_convert_modules_v2.py` | Batch converter for outside-mount modules using `hmd_convert_v2.py` — every module converts through this path now (see finding 23 in `hmd_format_notes.md`) |
| `tools/hmd_parse.py`          | Heuristic "G-style" parser once believed necessary for a "legacy TestPE" format. Finding 15 disproved that: `Pathway_Puncher.fbx` (the one file this was built for) is a normal production HMD file that was simply mis-extracted from the pak. Kept for reference/history only — do not reach for this on a new file without first checking whether it's actually just a mis-extracted production HMD file (search the raw pak for a nearby `HMD\x06` magic before assuming a new format). |
| `tools/hbson_parse.py`        | Faithful reader for `.prefab`/HBSON binary format, ported from the real `hxd/fmt/hbson/Reader.hx`/`Writer.hx` (see finding 14). Magic `"HBSON"` + 1 pad byte, then one recursive tag-encoded value; see the file's own docstring for the full tag table. |
| `tools/pak_extract.py`        | Extracts both disc=0x00 and disc=0x02 files from res.pak. disc=0x02 positions use the exact `stored_pos + dir_size` formula (finding 17) — no cumulative-sum math. `--all` extracts every file in the pak (used to build a full local mirror for reverse-engineering); re-run this whenever a file looks structurally wrong before assuming a new format quirk, since `pak_out` is a point-in-time mirror that doesn't auto-refresh when the extractor changes (finding 23/24). One known unresolved exception: one disc=0x00 entry (`Pathway_Puncher.fbx`) is extracted 13 bytes past its real start every time (not root-caused, fixed by hand each time — see finding 15). |

**Running the converter:**
```
python tools/hmd_to_bin.py <input.hmd> <output.bin>            # hull frames / engines
python tools/hmd_convert_v2.py <input.hmd> <output.bin>        # compound tools/modules (real transforms)
python tools/batch_convert_hulls.py         # converts all sizes, updates _manifest.json
python tools/batch_convert_modules_v2.py    # converts all outside modules, updates _manifest.json
```

#### Remaining work

**pak_extract.py** handles disc=0x02 extraction (D02_DATA_START = 2,156,315,392, 16-byte alignment). Re-extract any hull size: `python tools/pak_extract.py --extract "Main_Structures" --out pak_out`.
