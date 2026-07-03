# HMD Format Notes (SpaceCraft / Heaps.io)

**Keep this file up to date** as new findings are made. Referenced from CLAUDE.md.

---

## Overview

SpaceCraft game assets use Heaps Model Data (HMD) stored with `.fbx` extensions inside `res.pak`.
Two format variants have been encountered:

| Variant    | Magic       | disc | Where                                      | Status     |
|------------|-------------|------|--------------------------------------------|------------|
| Production | `HMD\x06`  | 0x02 | `assets/Vehicules/Buildings_Parts/`        | **FULLY DECODED** |
| TestPE     | `HMD\x??`  | 0x00 | `assets/Buildings/Props/TestPE/`           | Legacy, partially documented |

All current conversion work uses the **production format**. TestPE files may differ and are no longer the focus.

---

## File Locations

Production HMD files live in `assets/Vehicules/Buildings_Parts/` inside res.pak (disc=0x02 entries).
Extract with:
```
python tools/pak_extract.py --extract "Main_Structures/4x3x1" --out pak_out
python tools/pak_extract.py --extract "Main_Structures/4x3x2" --out pak_out
# etc.
```

Output path matches PAK directory structure under `pak_out/`.

> **Use only the in-game extracted files from `pak_out` as reference.**

---

## PAK disc=0x02 Extraction

**Corrected by finding 17 -- see below.** disc=0x02 directory entries store their OWN absolute position directly, as a little-endian double, in the first 8 bytes of the 16-byte payload (`stored_pos(double, 8) + size(int32, 4) + hash(int32, 4)`). The real absolute byte offset is:

```python
abs_pos = int(stored_pos) + dir_size   # dir_size = the pak header's own headerSize field
```

`dir_size` is the same field already read at pak header byte 4-7 (`PakReader.dir_size` / `self.dir_size`) -- no extra empirically-fitted constant needed.

pak_extract.py implements this automatically. Both disc types are extracted the same way: just pass a pattern to `--extract`. `tools/pak_verify_positions.py` confirms 0 failures across every `.fbx`/`.prefab`/`ui/icons/*.png` entry in the pak (3,030 validatable entries checked).

---

## Production HMD Format (version 0x06, disc=0x02) — CONFIRMED

Fully decoded and implemented. All 14 4x3x1 hull shapes converted successfully.

### Magic and Header (19 bytes)

| Byte(s) | Content                                                          |
|---------|------------------------------------------------------------------|
| 0–2     | `48 4D 44` = "HMD"                                               |
| 3       | `0x06` = version (production format)                             |
| 4–7     | `geom_start` (uint32 LE) = absolute byte offset of geometry      |
| 8–14    | 7 bytes, purpose unclear (`01 07 03 00 00 00 00` observed)       |
| 15–18   | `vc_lod0` (uint32 LE) = LOD0 vertex count                        |
| 19      | `0x0B` = first LOD attribute block marker                        |

### LOD Attribute Blocks (starting at byte 19)

One block per LOD. Format:
- Byte 0: `0x0B` marker
- Byte 1: attribute count (4 for all tested files)
- Per attribute: `name_len(1) + name(name_len bytes) + type(1)`
- Then: extra section (variable size, see below)

**Attribute types (version 0x06):**
| Type | Size/vertex | Meaning              |
|------|-------------|----------------------|
| 0x13 | 8 bytes     | float16×3 + padding  |
| 0x12 | 4 bytes     | float16×2 (UV)       |

Observed attribute order: `position(0x13)`, `normal(0x13)`, `tangent(0x13)`, `uv(0x12)`

**Vertex stride:** 3×8 + 4 = **28 bytes/vertex** (for all 14 4x3x1 files)

**Finding next block:** scan for the next `0x0B` byte after the attr entries. Next `0x0B` within 120 bytes = next block start; otherwise use sentinel to close the last block.

**Spurious 0x0B:** for some files (e.g. 4x3x1_K), a byte inside the bbox float equals 0x0B, causing the parser to create a fake block past `geom_start`. Fix: filter all blocks to only those with `extra_off < geom_start`.

### Extra Section Layout

Each LOD attribute block is followed by an extra section. Size = `38 + gc×4` bytes.

| Extra offset        | Content                                                             |
|---------------------|---------------------------------------------------------------------|
| [0..3]              | `vp` (uint32 LE) = LOD's vertex buffer start offset within geom section |
| [4]                 | `gc` = material group count (5 or 6 for 4x3x1 files)               |
| [5..5+gc×4−1]       | `gc × uint32 LE` = per-group index counts for this LOD              |
| [5+gc×4..5+gc×4+3]  | `vbuf_size` = vc × stride (uint32 LE)                              |
| [5+gc×4+4..5+gc×4+27] | bbox: 6×float32 LE `[minX,minY,minZ,maxX,maxY,maxZ]`             |
| last 5 bytes (non-last LODs) | 5 bytes, purpose unclear                                 |

**Extra size formula:**
- gc=5 → 38+20 = **58 bytes** (standard for A,F,G,H,I,J,K,L,M,N)
- gc=6 → 38+24 = **62 bytes** (B, C, D, E)

**For the last LOD only:** the extra section is extended with the embedded material section immediately after the standard 58/62 bytes. This section holds material name strings and runs until the LOD descriptor sentinel.

### Material Groups

**Group count (`gc`) and per-group index counts:** read from LOD0's extra[4] and extra[5..5+gc×4−1].

**Material roles** are determined by keyword-scanning the material name section embedded in the last LOD's extra (starting `std_extra_len − 5` bytes into that block, ending at the sentinel). First occurrence of each known keyword in byte order = that group's name.

**Known material name → role mapping:**
| Name             | Role | Meaning  | Default RGB  |
|------------------|------|----------|--------------|
| Panel_Principal  | 0    | paint    | (94, 124, 162) |
| Metal_Brushed    | 1    | metal    | (121, 130, 141) |
| POM_Decals_01    | 2    | dark     | (34, 38, 44) |
| POM_Decals_02    | 2    | dark     | (34, 38, 44) |
| POM_Decals_03    | 2    | dark     | (34, 38, 44) |
| Signaletique_01  | 4    | emissive | (255, 200, 50) |
| Signaletic_01    | 4    | emissive (alt spelling) | (255, 200, 50) |

If a group's role cannot be determined, it is padded with role 2 (dark).

**Verified group data for all 14 4x3x1 files (LOD0):**
| File | gc | Total ic | Role order (by group index 0→gc−1)              |
|------|----|----------|-------------------------------------------------|
| A    | 5  | 2370     | dark, paint, metal, dark, dark                  |
| B    | 6  | 5718     | dark, metal, paint, dark, emissive, dark        |
| C    | 6  | ~5000+   | (varies — keyword scan used)                    |
| D    | 6  | ~5000+   | (varies)                                        |
| E    | 6  | ~5000+   | (varies)                                        |
| F    | 5  | ~2000+   | (varies)                                        |
| G    | 5  | 1680     | (varies)                                        |
| H    | 5  | ~2000+   | (varies)                                        |
| I    | 5  | ~2000+   | (varies)                                        |
| J    | 5  | ~2000+   | (varies)                                        |
| K    | 5  | ~3000+   | dark, dark, metal, paint, dark (spurious 0x0B in bbox) |
| L    | 5  | ~2000+   | (varies)                                        |
| M    | 5  | ~2000+   | (varies)                                        |
| N    | 5  | ~2000+   | (varies)                                        |

**Parsing strategy:** use `parse_material_groups()` in `tools/hmd_parse_prod.py` (keyword scan — robust against format variations including files that store full texture paths).

### LOD Descriptor Section

Follows all attr blocks. Detection: find `00 00 00 02 04 05` — the byte immediately before this 6-byte sentinel is `lod_count`.

Format after sentinel:
```
[for each of lod_count LODs:]
  name_len (1 byte)
  name     (name_len bytes, e.g. "4x3x1_G_LOD0")
  null     (1 byte)
  meta     (84 bytes, starts with 00 00 00 FF)
```

For LOD1 and LOD2 in production files, a `01 04` prefix appears before `name_len` (purpose unclear).

**IMPORTANT:** `extra[4]` (gc) is NOT the lod_count. For A, G, I they happen to coincide; for others (D, E, H) they differ. Always read lod_count from the sentinel.

### Geometry Section

Starts at `geom_start` (from header bytes 4–7).

Each LOD section starts at `geom_start + vp` (vp from that LOD's extra[0..3]).

**Within each LOD section:**
1. **Vertex buffer:** `vc × 28 bytes` (vc from `header[15..18]` for LOD0; varies per LOD)
2. **Index buffer:** uint16 LE, runs to start of next LOD's section (or EOF)

**Index buffer format:** little-endian uint16 when `vc ≤ 65535`; little-endian **uint32** when `vc > 65535` (e.g. Engine_Explorer_MK2 with vc=67,614). Detection: `idx_size = 4 if vc_lod0 > 65535 else 2`; `ic = ic_bytes // idx_size`. The .bin format and `_manifest.json` must reflect `i32=True` for these files.

**Vertex position layout (stride=28):**
| Offset | Content         |
|--------|-----------------|
| 0–5    | pos_x, pos_y, pos_z as float16 LE |
| 6–11   | normal (float16×3)   |
| 12–17  | tangent (float16×3)  |
| 18–21  | uv (float16×2)       |
| 22–27  | bitangent or padding |

**Coordinate ranges (all in grid units, no scaling needed):**
- Files A, B, C, F, M, N: Z ∈ [~0, ~1] (piece is along one face)
- Files D, E, G, H, I, J, K, L: Z ∈ [~−0.5, ~0.5] (piece is centered on Z)

No coordinate scaling or shift is required for these hull-frame vertex buffers —
positions are already in grid units. **This does NOT generalize to compound
tool/module meshes** — see finding 8 below: those files carry a *separate*
per-part scale/rotation/translation in the HMD `models[]` node hierarchy (not
present/needed for simple single-object hull frames), which must be read and
applied on top of the raw vertex data, or the result is wrong by whatever
factor that part's model node specifies (confirmed 3x+ wrong on Water_Collector).

---

## .bin Format (shipbuilder output)

Documented in [shipbuilder/js/meshLoader.js](../shipbuilder/js/meshLoader.js).

```
uint32   vertex_count  (LE)
uint32   index_count   (LE)
uint8    group_count
6×float32  bbox [minX,minY,minZ,maxX,maxY,maxZ]  (LE)
vc×3×uint16  quantized positions (LE)
  x = bbox_minX + uint16/65535 * (bbox_maxX − bbox_minX)
gc×(role:1B + r:1B + g:1B + b:1B + start:4B + count:4B)  groups
ic×uint16  indices (LE)
```

**Material roles:** 0=paint, 1=metal, 2=dark, 3=light, 4=emissive, 5=glass

**All current 4x3x1 production bins:** gc=5 (most) or gc=6 (B,C,D,E)

---

## Coordinate System

Production HMD positions are already in grid units (1 grid unit = 1 ship builder cell).

The ship builder's Three.js loader applies `rotateX(-Math.PI/2)`:
- new_X = old_X (width unchanged)
- new_Y = old_Z (HMD depth/Z → Three.js height)
- new_Z = −old_Y (HMD height/Y → Three.js depth, negated)

No scaling or shift is needed before writing positions to .bin **for simple
single-object hull-frame/engine files**. Compound tool/module meshes need their
per-part model-node transform applied first — see finding 8 below.

**Ring-buffer geom_start misalignment (fixed):** For most ring-buffer files, the `geom_start` value in the HMD trailer is wrong by 16–62 bytes relative to the true start of the vertex buffer. The fix: scan the file for `ic` consecutive uint16 values all < `vc` to locate the true index buffer, then back-compute `vbuf_start = ibuf_found − vc×stride`. Implemented in `_find_ibuf_start()` in `hmd_to_bin.py`, called from `_finish_prod_conversion` for all conversion paths. All 32 ring-buffer bins (12x6x2 A–N, 12x6x4 A–B, 16x6x2 A–N, 16x6x4 A–B) now have correct grid-unit dimensions.

---

## Variable-length Text Prefix (4x3x2 through 8x6x2)

Files for hull sizes larger than 4x3x1 have an FBX ASCII text block **before** the HMD\x06 magic bytes. The prefix is material names and path references followed by closing braces. Prefix length observed:

| Size range | Prefix length |
|------------|---------------|
| 4x3x2      | ~16–32 bytes  |
| 6x3x1      | ~48–80 bytes  |
| 8x3x1      | ~128–144 bytes |
| 8x6x2      | ~176 bytes    |

**Fix for converter:** search for `b'HMD\x06'` anywhere in the file data and slice from that offset before parsing. Do NOT assume the magic is at byte 0.

```python
hmd_off = data.find(b'HMD\x06')
if hmd_off > 0:
    data = data[hmd_off:]
```

---

## Ring-Buffer Layout (12x6x2, 12x6x4, 16x6x2, 16x6x4)

For the largest hull sizes, the file is a **circular buffer**: the HMD header is a short trailer near the **end** of the file, and the body (LOD attribute blocks + geometry) occupies the **start**. The last LOD0 attribute block wraps from the trailer back to byte 0 of the file.

### Geometry layout

- `raw[0 .. geom_start-1]` — body: attr-block definitions (LOD0 wraps, LOD1+ complete)
- `raw[geom_start .. hmd_off-1]` — geometry (vertex + index buffers)
- `raw[hmd_off .. EOF]` — HMD header trailer (10–30 bytes; contains geom_start)

**geom_start** is always read from trailer bytes [4..7] (LE uint32). For all observed 12x6x2 and 16x6x2 files: `geom_start = 714`.

### Parsing algorithm

Rotation (`data[hmd_off:] + data[:hmd_off]`) was tried but fails because the LOD0 attr block straddles the boundary. The working approach:

1. **Find LOD1's attr block:** scan `body = raw[:geom_start]` for the first complete `0x0B` block (`_find_first_body_block`). Minimum `name_len = 1` (the `uv` attribute has `name_len=2`, not 4 as initially assumed).
2. **Backtrack to LOD0's extra section:** `extra_start = LOD1_off − (38 + gc×4)`. Validate with `body[extra_start + 4] == gc` and `body[extra_start][0..3] == 0` (LOD0 has `vp=0`).
3. **Read LOD0 geometry:** `vbuf_start = geom_start`, `vc = vbuf_size / stride`, `ibuf_start = geom_start + vc × stride`.

LOD0's attr definitions (position, normal, tangent, uv) are spread across the trailer and body[0..~20] — the parser never needs to reconstruct them because stride and gc come from the complete LOD1 block or the backtracked extra.

### Trailer size variability

The trailer length (= `file_size − hmd_off`) varies from 10 to 30 bytes across files. The standard header content is:

```
HMD\x06 (4) + geom_start (4) + N header bytes + 0x0B + attr_count + [attr defs start]
```

With trailer_len=30: all of "position" name wraps into the trailer, LOD0 marker at ring 19.
With trailer_len=14: marker at ring 17; more attr defs are in the body.
With trailer_len=10: marker at ring 13.

The LOD0 extra section position in the body is determined by backtracking from LOD1, so trailer length doesn't need to be known precisely.

### Three ring-buffer variants

**Variant 1 — standard ring-buffer** (12x6x2_A through most 12x6x2, 16x6x2_F, etc.):
- `HMD\x06` found in second half of file, `geom_start` readable from trailer bytes 4–7.
- Detection: `_detect_ring_buffer_hmd(raw)` — finds magic, validates `geom_start < hmd_off`.

**Variant 2 — prefix ring-buffer** (16x6x2_C, D, F, I, K, L, M; 16x6x2_J, N; 16x6x4_A, B):
- File starts with `XX 00 00 0B` where `1 ≤ XX ≤ 20` (version byte + start of LOD0 block).
- Either: no full HMD header present (replaced by JSON), or trailer too short to read geom_start without wrapping into the body prefix bytes (which are NOT zeros, corrupting the reading).
- Detection: `_detect_prefix_ring_buffer(raw)` — checks byte prefix, finds sentinel, infers `geom_start = sent_off + 326` (observed constant for 16x6x2 files) or falls back to 714/768.

**Variant 3 — body-start ring-buffer** (12x6x2_N, 12x6x4_B):
- File starts with attr-name bytes (`6e 6f 72 6d...` = "norm..." continuation of LOD0 attrs wrapping from end).
- No HMD\x06 magic anywhere — file ends in JSON `"materials": {...}` text rather than a binary header.
- The body structure (LOD1 at body[78], LOD0 extra at body[20]) is identical to working files of the same size; only the header is missing.
- Detection: `_detect_body_start_ring_buffer(raw)` — requires no HMD magic, sentinel at 0–500, infers geom_start.

### False-positive rejection

Some files have `HMD\x06` bytes inside index data near the end:
- 16x6x2_J: magic at offset 176736 (6 bytes from EOF). Only 2 bytes of geom_start are in the trailer; the wrapped reading gives 0x000A02CA ≫ file_size → rejected.
- 16x6x4_A: magic at offset 160160 (6 bytes from EOF). Same pattern.

Both files are then correctly handled by `_detect_prefix_ring_buffer` (prefix byte ≤ 20, sentinel found).

Bounds check in `_detect_ring_buffer_hmd`: `off + 8 <= len(raw)` prevents buffer overflow when reading geom_start from near-EOF hits.

### 8x3x1_N — resolved (was a stale pak_out copy, not a real anomalous format)

For a long time this pak entry appeared to start with raw big-endian uint16 index data at byte 0 (sequential values 0x0295, 0x0296...), with `HMD\x06` appearing at byte 144 as a false positive within the data -- matching none of the ring-buffer variants (raw[1] = 0x95 ≠ 0x00), so no usable mesh could be derived from it. This turned out to be the exact same root cause as findings 17/23/24: a stale `pak_out` copy predating the disc=0x02 position-formula fix, not a real alternate format. After the full `pak_extract.py --all` re-extraction, `8x3x1_N.fbx` starts with a clean `HMD\x06` header at byte 0 like every other hull shape, converts through the normal `hmd_to_bin.py` production-HMD path with no special-casing, and produces a correctly-proportioned ~9x3x1 mesh. All 130 of 130 hull shapes are now real. Lesson: don't trust an "anomalous format" diagnosis made before the pak position bug was found and fixed -- re-extract and re-check first.

---

## Tools

| Tool                        | Purpose                                                             |
|-----------------------------|---------------------------------------------------------------------|
| `tools/hmd_parse_prod.py`   | Legacy heuristic parser for production HMD (v0x06), used for hull frames/engines: `parse_prod_hmd()`, `parse_material_groups()`, `_parse_attr_blocks()`, `read_verts_f16()`, `read_indices_le_u16()`. Does NOT read the real model-node transform hierarchy — see finding 8. |
| `tools/hmd_parse_heaps.py`  | **Authoritative** HMD reader — faithful port of Heaps' own `hxd/fmt/hmd/Reader.hx`. Reads the real `models[]` scene-node hierarchy (position/quaternion rotation/scale per named part, separate from raw geometry) and `stride_bytes()` (the real per-vertex byte stride — the raw file `stride` byte is a component count, not bytes). Use this for any new format investigation. |
| `tools/hmd_to_bin.py`       | Converter: `convert_prod_style()` calls hmd_parse_prod and writes .bin; `convert_g_style_auto()` handles TestPE G-style; `write_bin()` writes the .bin format. Used for hull frames/engines. |
| `tools/hmd_convert_v2.py`   | Transform-aware converter for compound multi-part meshes (tools/modules): selects each `*LOD0` model, applies its real scale→rotate→translate, merges using the file's own material index per group. Use this for any Tools-category asset. |
| `tools/hmd_parse.py`        | Legacy parser for TestPE G-style (disc=0x00) files                  |
| `tools/pak_extract.py`      | Extracts both disc=0x00 and disc=0x02 files from res.pak using cumulative offset calculation. `--all` extracts every file in the pak (used to build a full local mirror for format reverse-engineering, output to `pak_out_full/`, gitignored). |
| `tools/batch_convert_hulls.py` | Batch converter for all Main_Structures hull sizes; updates `_manifest.json` |
| `tools/batch_convert_modules.py` | Batch converter for outside-mount modules using the old heuristic path; kept for its `MODULE_SOURCES` mapping table |
| `tools/batch_convert_modules_v2.py` | Batch converter for outside-mount modules using `hmd_convert_v2.py`; falls back to `hmd_to_bin.py` for the 3 Decoratives_Parts files whose animation/skin section isn't ported yet |
| `tools/prefab_parse.py`     | Exploratory `.prefab` binary tokenizer — debugging aid only, not a finished/trustworthy parser (see finding 5) |

**All tools must be saved to `tools/` immediately after writing, even if incomplete.**

**Reference source:** `hmd_parse_heaps.py` was written by cloning `HeapsIO/heaps`
(and, for other format work, `HeapsIO/hxbit`, `HeapsIO/hide`,
`HaxeFoundation/hashlink`, `Gui-Yom/hlbc`) into `tools/heaps_ref/` and reading
the actual engine source rather than guessing. The full clones aren't kept in
the repo (large, mostly-unrelated code + build artifacts, gitignored), but the
3 specific files actually used as reference — `Reader.hx`, `Data.hx`,
`BufferFormat.hx` — are vendored unmodified (MIT licensed) in
`tools/heaps_ref_excerpts/`. Recreate the full clone if deeper investigation
is needed:
```bash
git clone --depth 1 https://github.com/HeapsIO/heaps.git tools/heaps_ref/heaps
```
The authoritative HMD reader lives at `hxd/fmt/hmd/Reader.hx` and `Data.hx`
inside that clone; the buffer stride/format logic is in `hxd/BufferFormat.hx`.

**Running the converter:**
```bash
python tools/hmd_to_bin.py <input.hmd> <output.bin>          # hull frames / engines
python tools/hmd_convert_v2.py <input.hmd> <output.bin>       # compound tools/modules
```
`hmd_to_bin.py` auto-detects format: tries production (v0x06) first, then G-style, then KNOWN_FILES fallback.

**Running the batch converter (after fixing prefix/inverted support):**
```bash
python tools/batch_convert_hulls.py
```
Converts all sizes from pak_out and updates `_manifest.json`.

---

## Conversion Status

### 4x3x1 through 8x6x2 — COMPLETE (7 sizes × 14 shapes = 98)

All available shapes converted from pak_out to .bin with correct material groups.
Output: `shipbuilder/ship_meshes/{size}_{shape}.bin`
Manifest: `shipbuilder/ship_meshes/_manifest.json` (all entries from pak_out)
Parts: `shipbuilder/ship_editor_data.json` — already complete for all hull sizes and variants.

### 12x6x2, 12x6x4, 16x6x2, 16x6x4 — COMPLETE (ring-buffer parser implemented)

All shapes for these sizes are now converted from pak_out. See "Ring-Buffer Layout" section for implementation details. All 130 shapes that exist across all hull sizes are pak_out-sourced and in the catalogue (see 8x3x1_N above for the last one to be resolved).

### Hull size conversion table

| Size     | Shapes   | Status                  | Notes                                         |
|----------|----------|-------------------------|-----------------------------------------------|
| 4x3x1    | A–N (14) | ✓ DONE (pak_out)        |                                               |
| 4x3x2    | A–N (14) | ✓ DONE (pak_out)        | text prefix ~16–32 B                          |
| 6x3x1    | A–N (14) | ✓ DONE (pak_out)        | text prefix ~48–80 B                          |
| 6x3x2    | A–N (14) | ✓ DONE (pak_out)        | text prefix                                   |
| 8x3x1    | A–N (14) | ✓ DONE (pak_out)        | text prefix ~128 B                            |
| 8x3x2    | A–N (14) | ✓ DONE (pak_out)        | text prefix                                   |
| 8x6x2    | A–N (14) | ✓ DONE (pak_out)        | text prefix ~176 B                            |
| 12x6x2   | A–N (14) | ✓ DONE (pak_out)        | ring-buffer variant 1 (A–M) and 3 (N, JSON end); geom_start corrected via ibuf scan |
| 12x6x4   | A–B (2)  | ✓ DONE (pak_out)        | ring-buffer variant 1 (A) and 3 (B, JSON end); same fix applied |
| 16x6x2   | A–N (14) | ✓ DONE (pak_out)        | variants 1, 2 (prefix/JSON); geom_start corrected via ibuf scan |
| 16x6x4   | A–B (2)  | ✓ DONE (pak_out)        | ring-buffer variant 2; same fix applied |
| MK1      | various  | not started             | Rounded_MK1_* connector pieces                |
| MK2      | various  | not started             | Rounded_MK2_* connector pieces                |

### Outside-mount modules (Tools/, Decoratives_Parts/) — 15 of 18 parts (14 of 17 unique mesh files) on the correct transform-aware pipeline (2026-07-03)

The 18 `mount: 'outside'` module parts (lights, mining lasers, radars, solar panels,
scanners) previously used mesh data that did not match a proper pak_out extraction.
Re-extracted and converted from `assets/Vehicules/Buildings_Parts/{Tools,Decoratives_Parts}/`.

Conversion tool: `tools/batch_convert_modules_v2.py` (transform-aware, see finding 8 —
this is now the correct/current path for the 15 Tools-category items). The 3
Decoratives_Parts items still go through the older `tools/batch_convert_modules.py` /
`hmd_to_bin.py` path (source table + manifest update, same pattern as
`batch_convert_hulls.py`) because the new reader doesn't yet handle their
animation/skin section.

**Format differences found in this asset category vs. hull pieces:**

1. **Wider material group counts.** Hull pieces only ever use gc=5 or 6, so the
   ring-buffer parser (`parse_ring_buffer_hmd` in `hmd_parse_prod.py`) only tried
   those two values when backtracking from a found LOD1+ block to LOD0's extra
   section. Prop/tool assets use anywhere from 2 to 12 material groups. Fixed by
   widening the backtrack search to `range(1, 17)`, with a stricter validator
   (`_valid_extra`): vp must be 0, vbuf_size must divide evenly by stride, and the
   implied index buffer must fit within the file — this prevents false-positive gc
   matches now that the search range is much wider.

2. **geom_start misalignment can be large and odd-byte-aligned.** The existing
   `_find_ibuf_start` correction (scan for `ic` consecutive uint16s all < vc) was
   hardcoded to even byte offsets only (hull files never needed odd alignment).
   Some module files have a true vertex/index buffer start at an ODD byte offset
   (confirmed on `Spot_Light_Barrel.fbx`: true vbuf_start=951, one byte off from
   even). Also, the misalignment magnitude is not always small (16–62 bytes as
   documented for hulls) — one file needed a correction of ~2KB. Fixed by
   rewriting `_find_ibuf_start` (in `hmd_to_bin.py`) with a numpy-based scan that
   checks both parities and is fast enough to search the whole file.

3. **A pure index-match isn't a reliable-enough signal on its own.** With large
   vertex counts (10k–19k), a run of indices that are merely "all < vc" can match
   by coincidence at a position that is NOT the true buffer (confirmed: an
   incorrect match 2.26MB away from the true position on `ColdLaser.fbx`, with
   0 bad indices). Fixed with two additional safeguards:
   - Only search for a corrected offset if the *original* geom_start-implied
     position is actually broken (`_count_bad_indices` checked first) — most
     module files' original geom_start was already correct, and blindly
     re-searching could override a correct position with a coincidental one.
   - When multiple candidate offsets tie on index-match quality, break ties using
     `_vertex_sanity_score`: compare the sampled vertex bounding box against the
     file's own stored bbox (from the LOD0 extra section) rather than a generic
     "small numbers" heuristic — reading the wrong attribute column (normal/
     tangent/uv are also small bounded floats) can otherwise look just as
     plausible as real position data.

4. **Compound multi-object files — the big one.** Tools/Decoratives_Parts `.fbx`
   files can pack *multiple independent sub-objects* into what the file header
   calls "lod_count" slots — NOT one object's decreasing-detail LODs. First
   confirmed on `ColdLaser.fbx`: header declares 12 "LODs" across (as it turns
   out) 4 real sub-objects named `Base`(2 LODs), `Rotary`(3), `Mining_Arm`(3),
   `Reciever`(4) — naively taking `lods[0]` (as the converter always did before
   this fix) gets only the small flat base plate, which is why converted "tool"
   meshes looked like flat rectangular slabs instead of assembled devices.
   - **The vc-derivation bug this exposed:** LOD1+'s vertex count is NOT reliable
     from the field at the expected "vbuf_size" position in its own extra section
     (that only holds a valid value for true LOD0/vp=0; for later entries it's
     stale/unrelated data that doesn't divide evenly by stride). The correct vc
     comes from the **gap to the next LOD's vp**: `vc[i] = (vp[i+1] - vp[i] -
     ic[i]*idx_size) / stride`. Verified exact on every boundary except the very
     last LOD of the file (trailing embedded material-name bytes throw off the
     final total_geom-based boundary — harmless, that LOD isn't needed).
   - **Sub-object boundary detection — use the embedded names, not a vc heuristic.**
     The LOD descriptor section (right after the `00 00 00 02 04 05` sentinel)
     embeds each LOD's own name, e.g. `BaseLOD0`, `RotaryLOD0`, `Mining_ArmLOD0` —
     ground truth for sub-object identity. An earlier version of this fix inferred
     boundaries from "vc increases relative to the previous entry", which is
     WRONG whenever a new sub-object's LOD0 has *fewer* vertices than the
     previous object's LOD0 — confirmed this silently swallowed a whole
     `Mining_Arm` sub-object into the previous `Rotary` object's LOD chain on
     `MiningTool1_OC.fbx`, and (initially unnoticed, since the merged result
     still looked "mostly right") merged `Rotary`+`Mining_Arm` together on
     `ColdLaser.fbx` too. Names are extracted via regex (`[A-Za-z_][A-Za-z0-9_]*
     LOD[0-9]+`) from the descriptor section rather than a fixed-stride walk —
     the per-entry record has a variable-length prefix that isn't fully decoded,
     but the name+trailing LOD number is always contiguous printable ASCII+digits
     regardless. Stripping the trailing `LOD\d+` and comparing against the
     previous sorted LOD's name gives `is_object_start`; falls back to the old
     vc-heuristic only if any LOD in the file is missing a parseable name.
   - **Fix:** `_finish_prod_conversion_merged` in `hmd_to_bin.py` reads every
     `is_object_start` entry's own geometry+groups (via the shared
     `_read_object_geometry` helper) and concatenates them into one combined
     mesh (vertex/index offsets adjusted, groups' `start` offset by cumulative
     index count) — this is the actual assembled visual. `convert_prod_style`
     picks the merged path automatically whenever more than one object-start is
     detected. Applies to the standard (non-ring-buffer) path only so far; the
     ring-buffer path (`parse_ring_buffer_hmd`) still only returns a single LOD0
     and hasn't been checked for the same issue (light/decorative parts looked
     fine on inspection, but haven't been stress-tested for compound files).
   - **`parse_material_groups`** gained an `extra_off` parameter so groups/colors
     can be read for a *specific* sub-object's LOD0 rather than always
     `blocks[0]`; the embedded material-name text section is still scanned
     file-wide (shared across sub-objects), so color/role assignment across
     merged parts is a best-effort, not fully independent per sub-object.

5. **Prefab `@model` references can be misleading red herrings, and prefab files
   can bundle multiple unrelated sibling item records.** Several prefabs (e.g.
   `Spot_Light_01.prefab`, `ColdLaser.prefab`) embed an `@model`/`@box` reference
   to a *completely unrelated* mesh (a cockpit, a cargo crate used as a collision
   proxy) — these are shared template/collision data, not the part's actual
   visual. Worse: confirmed byte-for-byte that `Batterie.prefab`'s entire content
   is embedded inside `ColdLaser.prefab` at a fixed offset (other unrelated tool
   records — `BESS_Battery`, `BigReactiveShield` — also appear as trailing bytes).
   The pak's per-file size for these small tool prefabs does not correspond to a
   clean, independent logical record. The `.prefab` binary format itself was
   partially reverse-engineered (tag bytes matching Heaps' `hxbit` Dynamic-value
   convention: 0=null,1=false,2=true,3=int,4=float,5=object,6=string,7=array,
   8=bytes — see `tools/prefab_parse.py`), but does not reliably yield a mesh
   reference for every item — for at least 3 items (see below) the reachable
   prefab data is exclusively weapon/FX/collider boilerplate.

6. **Three part ids have no file matching their prefab name, and no resolvable
   reference in their prefab data** (`Simple_Mining_Laser`, `RadarMK1`, `Scanner`).
   Currently mapped to a same-family fallback file as an unconfirmed guess:
   `Simple_Mining_Laser` → `Tools/MiningTool.fbx`, `RadarMK1` → `Tools/Radar.fbx`,
   `Scanner` → `Tools/ScanningTool.fbx`. Flagged as wrong by visual inspection —
   left as-is pending better information (no reliable way found yet to resolve
   these from pak data alone).

7. **`lod_count` byte can read as 0 even when the file is fine — fixed.**
   `Water_Collector.fbx`, `CrudeSolarPanel_Flat.fbx`, and `SmallSolarPanel_Flat.fbx`
   all failed at the standard (non-ring-buffer) parse path with zero LODs found.
   Root cause: these files have a *separate* LOD descriptor sentinel per sub-object
   (each with its own small local name list — e.g. `Water_Collector_LOD0`,
   `Water_Collector_Pannel_L_LOD0`, `Water_Collector_Pannel_R_LOD0`,
   `Water_Collector_Piston_LOD0`, 4 LODs each = 16 total) rather than one shared
   descriptor section for the whole file. `data.find(sentinel)` only locates the
   *first* one, and the byte immediately before it doesn't hold a valid global
   count (reads 0) — even though the LOD name data right after it is well-formed.
   Confirmed the real count by both counting actual attribute blocks found (16)
   and by regex-scanning all LOD names in the file (16, ignoring end-of-file
   name-scan overrun duplicates) — they agree. Fix: if the sentinel-derived
   `lod_count` is 0, fall back to `len(blocks)` (the number of attribute blocks
   `_parse_attr_blocks` actually found). All 3 files now convert correctly.
   **Correction (see finding 8 below): the "matches [3,14,4]" sanity check
   originally written here was wrong** — `[3,14,4]` was itself an unverified
   guess in `ship_editor_data.json`, and the real in-game size (confirmed by
   the user against a screenshot) is ~4.5x1.2x0.9. The bbox match was
   coincidental, not confirmation of correctness.

**All 18 of 18 outside modules now convert successfully.** 3 part ids
(`Simple_Mining_Laser`, `RadarMK1`, `Scanner`) still use unconfirmed fallback
source files per finding 6 above — visually flagged as likely wrong, but no
better candidate found in the pak. `MiningTool.fbx` (Simple Mining Laser's
fallback) in particular shows a crowded/disconnected-looking sub-object
arrangement with no evidence of a parser bug (no vbuf corrections triggered,
each sub-object's own geometry reads as internally consistent) — most likely
further evidence it's simply the wrong source file, not a parsing issue.

8. **Root cause of Water_Collector's (and others') wrong absolute size/orientation
   found and fixed: the entire from-scratch heuristic parser above (`hmd_parse_prod.py`)
   only ever reads raw geometry buffers and never reads the file's actual
   `models[]` scene-node hierarchy.** The real HMD format (confirmed by cloning
   HeapsIO/heaps and porting its actual `hxd/fmt/hmd/Reader.hx` byte-for-byte —
   see `tools/hmd_parse_heaps.py`) stores geometry (`geometries[]`, raw vertex/
   index buffers) *separately* from a `models[]` array of named scene nodes.
   Each model has its own `position` (translation + quaternion rotation +
   **scale**, 9 floats: x,y,z,qx,qy,qz,sx,sy,sz) and a `geometry` index. Multiple
   models reference the same or different geometries; a compound part like
   `Water_Collector` is 4 real named models (`Water_Collector` body,
   `_Pannel_L`, `_Pannel_R`, `_Piston`, each with LOD0-3 variants), **not** one
   object mis-split by a heuristic. The old parser/converter dropped this
   entire transform layer, merging every sub-part's raw vertices as if they
   all sat unscaled at the shared origin. For Water_Collector this meant: the
   body+piston's real scale (0.33, found directly in the model node, not
   guessed) was never applied, and every part's real rotation (present on
   nearly every model in nearly every file checked) was dropped, which is what
   actually caused the "wrong orientation" symptom investigated at length
   earlier in this file — not a hull-vs-tool axis-convention mismatch.

   Confirmed by direct calibration: the real model-hierarchy bbox for
   Water_Collector is [4.54, 0.88, 1.22] (HMD X,Y,Z), matching the user's own
   screenshot measurement (~4×1×1, shape/orientation visually confirmed
   against the real in-game mesh) almost exactly — with **zero guessed
   values**, purely from applying the file's own stored transforms.

   **Second bug found while implementing this:** the raw `stride` byte stored
   per-geometry in the file is NOT the byte size — it's the total *component
   count* across vertex fields (matches `BufferFormat.stride`, e.g. position+
   normal+tangent DVec3 + uv DVec2 = 11), used only as an internal assertion
   in the real reader. The actual per-vertex byte stride must be computed
   field-by-field from each field's component count × precision byte-size,
   with 4-byte alignment padding applied cumulatively after each field
   (`tools/hmd_parse_heaps.py`'s `stride_bytes()`, ported from
   `BufferFormat`'s constructor). Using the raw `stride` byte directly as a
   byte count (an easy mistake — it's genuinely misleading) misaligns every
   vertex read and produces garbage/NaN positions.

   **New tools:** `tools/hmd_parse_heaps.py` (faithful reader port — use this
   for any new format investigation, not the old heuristic parser),
   `tools/hmd_convert_v2.py` (converter: selects each `*LOD0` model, applies
   its real scale→rotate→translate transform, merges parts using the file's
   own material index per group instead of keyword-guessing sub-object
   boundaries), `tools/batch_convert_modules_v2.py` (batch runner; falls back
   to the old `hmd_to_bin.py` path for the 3 Decoratives_Parts files that hit
   an animation/skin section not yet ported).

   **Still open:** `RadarMK1`'s new real-transform bbox (0.33×0.4×1.7) is
   *more* obviously wrong than before (contradicts the user's confirmed ~1×1×1
   in-game knowledge) — strong further evidence `Radar.fbx` (finding 6's
   unconfirmed fallback) is simply the wrong source file, now that its
   geometry is being read correctly. Not re-guessed; left as `dims: [1,1,1]`
   pending a real source file. `Simple_Mining_Laser`'s and `Scanner`'s
   fallback files, by contrast, produced dims very close to their prior
   (already-reasonable-looking) values even under the new parser, so those
   guesses may be closer to correct despite being unconfirmed.

9. **Two more real bugs found after finding 8, both confirmed by the user
   spotting wrong-looking results and asking for another look (not found by
   static analysis alone):**

   - **Parent-chain transforms were still being ignored.** Finding 8 fixed
     reading each model's own position/rotation/scale, but every model also
     has a `parent` index, and that transform is relative to the *parent's*
     space, not world space — `hmd_convert_v2.py` was applying only each
     part's own transform, never composing up through its ancestors. This
     happened to be harmless on `Water_Collector` (every part parents to an
     identity scene root, a no-op), which is exactly why it went unnoticed
     there. It was NOT harmless on `MiningTool1_OC`: `Mining_Arm`, `Receiver`,
     and `Plane` are all parented to `Base`, which carries a genuine 180°
     rotation about Z — without composing that in, the children landed at
     the wrong world position/orientation relative to Base, visibly
     detached from the assembly ("the laser is not properly attached to the
     arm"). Fixed with `transform_vert_chain()`: walk from the part up
     through `parent` indices to the root (`parent == -1`), applying each
     ancestor's own transform in turn. Regenerated all 7 affected meshes
     (ColdLaser, HiPiLaser, HiPi_Overclocked_Laser, MiningTool1_OC, RadarMK1,
     Simple_Mining_Laser, SmartRadar) — bbox spans were unchanged in every
     case (rotating a part and its sibling together preserves their
     relative gap/overlap), only internal arrangement corrected, so no
     `dims` changes were needed.

   - **`HiPiLaser.fbx` / `HiPi_Overclocked_Laser.fbx` are real files, but the
     wrong ones for those items — pure name-guess, never checked against
     data.cdb.** Both files exist in the pak and convert cleanly (which is
     why this wasn't caught by finding 6's "file doesn't exist" check), but
     `data.cdb`'s actual `"model"` field for `MiningTool2`/`MiningTool2_OC`
     is `MiningTool_Medium.prefab`/`MiningTool_Medium_OC.prefab` — a
     completely different asset. There is no `MiningTool_Medium_OC.fbx` in
     the pak or `model.props`; the "OC" item variant reuses the plain
     `MiningTool_Medium.fbx` with different stats/materials, not a separate
     mesh. **Lesson: a file existing and converting without errors is not
     confirmation it's the *correct* file — cross-check `data.cdb`'s
     `visual.model` field by item id whenever a mapping was chosen by name
     similarity rather than verified this way.** `ColdLaser.fbx` and
     `MiningTool1_OC.fbx` were re-checked against `data.cdb` and are
     confirmed correct (their filenames exactly match the prefab name in
     `visual.model`).

10. **Material colors were pure invented placeholders (per-role flat RGB),
    never verified against anything real -- the actual texture format has
    now been reverse-engineered from scratch (no reference implementation
    existed for this one, unlike the HMD reader) and real colors are wired
    in.** The `_MAT_KEYWORDS` -> `_DEFAULT_ROLES` system only ever assigned
    one of 6 fixed placeholder colors (paint/metal/dark/light/emissive/glass)
    by keyword-matching a material's *name* -- it never had any connection to
    what that material actually looks like.

    The game's `*_basecolor.png` files (referenced from
    `Tools/materials.props` and the shared `Materials_Library/*` prefabs) are
    NOT real PNGs despite the extension -- confirmed via `hxd/res/Image.hx`
    (Heaps' own format-signature checks for PNG/JPG/GIF/DDS all fail to
    match). Reverse-engineered by algebraic file-size matching plus visual
    verification: **BC1/DXT1 compressed, square power-of-two resolution,
    full mipmap chain, 128-byte header** (confirmed exact: mip-chain byte
    total + 128 equals the real file size for every plain material checked).
    Alpha/decal materials use **BC3/DXT5** (148-byte header) instead, and 2
    files (`Signaletique_01`/`02`) are **uncompressed RGBA, single level, no
    mips** (also 128-byte header). Verified by decoding real images: 
    `Metal_Brushed` produces a recognizable brushed-steel-with-scratches
    texture; `Yellow_Plastic` a plausible flat mustard-yellow. BC3 decoding
    has a remaining bug (the block/plane layout isn't quite standard
    interleaved BC3 -- decoded output shows the right spatial pattern, e.g.
    `Grid_Hex`'s hexagon grid is clearly visible, but with color-channel
    noise on top) -- low priority since it only affects a handful of
    decal/glass materials, and per-block color averaging still gives a
    roughly plausible mean even with the noise.

    `tools/extract_material_colors.py`: driven by the actual material names
    read from each Tools mesh's own `materials[]` list (not a blind crawl of
    the texture library), searches the whole pak for an exact
    `<name>_basecolor*.png`, with two fallbacks: case-insensitive match (the
    game's own data has inconsistent casing for the same material, e.g.
    `Metal_RedPaint` vs `Metal_Redpaint` across different files), then
    progressively stripping trailing `_word` segments to find the base
    texture behind a tint/paint variant (`Metal_Standard_Copper` has no
    texture of its own -- it's `Metal_Standard` tinted elsewhere). Covers 35
    of 55 real material names used across the Tools category; the rest
    (`PufferCloth`, `Logo_Aegir`, `Water_Bubble`, decal-atlas names like
    `POM_Decals_01/02/03`, etc.) have no matching texture anywhere in the pak
    -- either genuinely unique specialty textures not found via this search,
    or (for the decal names) shared trim-sheet atlas materials accessed via
    per-decal UV offsets rather than individual files, which this
    average-color approach can't represent anyway. Those fall back to the
    old placeholder role color, same as before.

    `hmd_convert_v2.py`'s `match_material_color()` looks up the real name in
    `tools/material_colors.json` first, falling back to the role default
    only when no real color was extracted. The `role` assignment (still
    keyword-based) is kept as-is because it also drives PBR shading params
    (metalness/roughness) and special emissive/glass rendering in
    `meshLoader.js` -- only the flat display color changed, not the shading
    model. **Note:** real colors can render more saturated under the ship
    builder's PBR lighting/environment reflections than their raw flat value
    suggests (confirmed on `Blue_Basic`, (97,108,114) — fairly muted in
    isolation) -- this is expected PBR behavior, not a decoding error, but
    means the overall look shifted in ways that need visual sign-off rather
    than being assumed correct just because the source data is real now.

11. **4 real items were missing from `ship_editor_data.json` entirely --
    never added, not a conversion bug.** Found by systematically cross-checking
    every `data.cdb` item with `type` in
    (`MiningTool`, `ShipRadars`, `ShipPowerTools`, `ShipToolSpecial`) against
    what already existed in the catalogue, rather than assuming the existing
    18 outside-mount parts were the complete set. Missing: `MiningTool0`
    (Crude Mining Laser), `MiningTool3` (Giant Laser), `MiningTool3_OC`
    (Overclocked Giant Laser), `PathwayPuncher` (Spacetime Puncher).

    - **`MiningTool3`/`MiningTool3_OC` resolved the `HiPiLaser.fbx`/
      `HiPi_Overclocked_Laser.fbx` mystery from finding 9.** Those files were
      wrong for `MiningTool2`/`MiningTool2_OC` (Hi-Pi Laser), but they aren't
      orphaned/wrong assets -- `data.cdb` shows `MiningTool3` ->
      `HIPI_MiningLaser.prefab` and `MiningTool3_OC` ->
      `HIPI_Overclocked_MiningLaser.prefab`. "HIPI" here is apparently a
      weapon-tier/brand prefix in the prefab naming for the "Giant Laser"
      line, unrelated to the "Hi-Pi Laser" item's display name despite the
      near-identical string -- a naming coincidence that caused the original
      wrong assignment. Both convert cleanly via the normal `hmd_convert_v2.py`
      pipeline with no dims/assembly issues.
    - **`MiningTool0`'s prefab (`DefaultLaser.prefab`) has no recoverable mesh
      reference** -- same prefab-boundary-corruption pattern as finding 6.
      Reuses the same unconfirmed placeholder as `MiningTool1`
      (`Tools/MiningTool.fbx`), documented as such, not presented as verified.
    - **`PathwayPuncher` is a genuine, currently-shipped TestPE-format asset**
      (`assets/Buildings/Props/TestPE/Pathway_Puncher.fbx`, disc=0x00) --
      confirmed via `data.cdb`'s own `visual.model` pointing to
      `Pathway_Puncher.prefab`, which really does reference this file. This
      **contradicts the older blanket assumption** in this file's "TestPE
      Format" section below ("these files were used for early research but
      are not the assets shown in-game") -- that's true for most TestPE
      files checked so far, but not a safe assumption for every one. Converts
      fine with the existing legacy `hmd_to_bin.py` G-style path; no changes
      needed to that converter itself.

    All 4 added to `ship_editor_data.json` with dims computed from their real
    (transform-corrected) bboxes, following the same `[hmdX, hmdZ, hmdY]`
    convention as every other outside module. `cost` (crafting recipe text)
    could not be verified from any data source found so far -- data.cdb has
    no separate recipe table keyed by these item ids, and the existing `cost`
    strings elsewhere in the file appear to be manually curated, not
    programmatically extracted. Left as `"Unknown"` for `MiningTool0` and
    `PathwayPuncher` (both have non-zero `price`, so they're presumably
    craftable) rather than fabricate plausible-sounding ingredients;
    `MiningTool3`/`MiningTool3_OC` have `price: 0` (loot-only per data.cdb)
    and use `""` matching the established convention for other price-0 items.

12. **A 5th missing item found (`Radar0`, Crude Resource Detector) --
    resolved the RadarMK1 mystery from finding 8/9: `Radar.fbx`/`Radar.prefab`
    was never RadarMK1's asset at all, it's Radar0's** (confirmed via
    data.cdb's `visual.model`: `Radar0` -> `Radar.prefab`, `RadarMK1` ->
    the *different* `RadarMK1.prefab`). This is exactly why RadarMK1 looked
    "more obviously wrong" once real transforms were applied in finding 8 --
    it was rendering an entirely different item's mesh, not a scale/rotation
    bug in the shared code. Missed in finding 11's "exhaustive" data.cdb scan
    because that scan assumed `"type"` always appears after `"id"` in the
    JSON -- for some entries (including `Radar0`) it appears before, in a
    different field order. Re-verify with a scan that checks both directions
    if doing this kind of audit again.

    `RadarMK1.prefab` itself has no recoverable mesh reference (same
    prefab-corruption pattern as several other items). `Radar_Upgrade.fbx`
    (otherwise completely unclaimed -- the other 3 radar-family items are
    all confirmed elsewhere) was assigned to it on the user's suggestion: its
    name plausibly means "the upgrade from Radar0", matching RadarMK1's tier
    position (Simple = one step up from Crude). Not confirmed via data.cdb
    like most other entries -- a reasoned name-based match, visually
    confirmed correct by the user afterward alongside Radar0.

13. **(Superseded by finding 14 -- do not trust this entry's conclusion.)**
    An earlier pass concluded `PathwayPuncher` was very likely NOT
    `Pathway_Puncher.fbx`, based on the rendered mesh looking like a jagged
    faceted shard rather than the in-game ring/portal device, plus the
    file's only embedded object name being the generic-sounding
    `Cube_001_slice_004`. Finding 14 disproves this via ground-truth prefab
    content: the mesh assignment is correct. The rendering bug is real and
    still unresolved, but it's a parser/geometry bug in our TestPE reader,
    not a wrong-asset problem. A real, confirmed parser bug was found along
    the way and is still valid: `compute_lod_offsets`'s vertex-to-index-buffer
    offset formula (`vbuf_start + vc*stride - 3` for non-last LODs) can land
    on a byte-misaligned position that still keeps every index in-range
    (passing the old bad-index check) while reading scrambled connectivity.
    `hmd_parse.py`'s `refine_ibuf_start()` (local-coherence scoring across a
    search window) fixes this class of bug and is wired into
    `hmd_to_bin.py`'s `convert_g_style_auto`; worth checking on any other
    TestPE file that looks broken.

14. **Cracked the real `.prefab`/HBSON binary format from the actual Heaps
    engine source (`hxd/fmt/hbson/Reader.hx` and `Writer.hx`, fetched from
    github.com/HeapsIO/heaps via `gh api`), the same way finding 8 cracked
    HMD from `hxd/fmt/hmd/Reader.hx`.** This directly disproves finding 13's
    "wrong asset" conclusion for `PathwayPuncher` and confirms our
    prefab-parsing tools were simply too naive to trust, not that the
    underlying pak data was random research-junk cross-references.

    - **Real format** (see `tools/hbson_parse.py`, a faithful port): magic
      `"HBSON"` + 1 pad byte (6-byte header), then one recursively-encoded
      value. Tag byte meanings: `0`=int 0, `1`=int (next byte), `2`=int (next
      i32), `3`=float (next f64), `4`/`5`=bool true/false, `6`=null,
      `7`=empty object, `8`/`9`=object (byte/i32 field count) with
      `(readString(), read())` pairs, `10`=string, `11`=empty array,
      `12`/`13`=array (byte/i32 element count). All ints/floats are
      **little-endian** (Haxe `Input.bigEndian` defaults false). Strings use
      a per-file backreference table: a leading i32 with bit `0x40000000` set
      means "fresh short (<=16 char) ASCII string, length in low 30 bits,
      pushed to the table"; bit `0x80000000` set means "fresh long/non-ASCII
      string, not added to the table"; neither bit set means "plain index
      into the table". Critically: **a real `.prefab` file's byte 0 must
      literally be `'H'`** -- this is the exact check the game's own loader
      (`hrt/prefab/Resource.hx`'s `loadData()`) uses to decide BSON vs. JSON.
    - **This exposed a real, confirmed bug in `tools/pak_extract.py`:** every
      `.prefab` file we'd extracted under `prefabs/` (disc=0x02) failed this
      "starts with `H`" sanity check -- e.g. our extracted
      `ColdLaser.prefab` starts mid-object and its declared byte range
      actually spans four *other*, unrelated, but individually-valid HBSON
      records (`CargoCrate_Mid`, `Batterie`, `BESS_Battery`,
      `BigReactiveShield`). This is a genuine cumulative-offset drift bug
      specific to (at least) the `prefabs/` subtree of the disc=0x02 stream,
      not the "prefab content is legitimately corrupted/unparseable" theory
      assumed since finding 5. (A tempting-but-wrong lead along the way:
      `data.cdb` also doesn't start with printable JSON at its computed
      disc=0x02 offset -- but that's a false alarm, since `data.cdb` is
      *itself* HBSON-serialized, per the same `Resource.hx` loader, so
      binary-looking leading bytes there are expected, not evidence of
      drift. Don't reuse that check as a validity signal.)
    - **Root cause bypassed, not fixed:** rather than debug the cumulative
      disc=0x02 math across ~8,000 entries, the real `Pathway_Puncher.prefab`
      record was located directly with a drift-independent signature search
      of the raw 16GB pak: since `"Pathway_Puncher"` is short ASCII (15
      chars), the writer format tells us exactly what precedes it as a
      string literal -- 4 bytes `struct.pack('<I', 15 | 0x40000000)`
      immediately followed by the literal bytes. Exactly one hit in the
      whole pak. Walking forward from the nearest preceding real `HBSON`
      marker (~6.1 KB before our -- wrong -- computed position for this
      entry) gave the complete, self-consistent, genuine record:
      ```
      @type prefab @children [...] @object @name @Barrel
      @constraint @BarrelConstraint @target TurretWeapon.Rotary.Receiver
      @WeaponDef @reference @Impact assets/fx/Ship/Weapons/MissileExplosion.fx
      @Eject assets/fx/Ship/Weapons/LaserMissile_Muzzle.fx
      @Projectile assets/fx/Ship/Weapons/Laser/LaserMissile.fx
      @PreviewPivot
      @model @Pathway_Puncher
      @source assets/Buildings/Props/TestPE/Pathway_Puncher.fbx
      ```
      **This confirms `Pathway_Puncher.fbx` (TestPE) genuinely is the correct,
      current, shipped mesh for this item** -- not a coincidental filename
      collision with a slicing-tool test asset as finding 13 concluded. It
      also explains the item's turret-like rig (`Barrel`, rotary constraint
      targeting a `TurretWeapon.Rotary.Receiver` socket, a `WeaponDef` with
      Impact/Eject/Projectile FX): the tool visually "fires" a projectile
      effect to punch its pathway, reusing the generic turret-weapon rig
      rather than having a bespoke animation system.
    - **Net effect:** the outstanding "crumpled shard" render is a genuine,
      still-unsolved bug in our TestPE G-style geometry parser for this
      specific file (see finding 13's still-valid index-offset fix, plus
      ongoing work on LOD-chain math -- LOD1's data was traced and found to
      degrade into synthetic ramp/counter values after ~31k vertices,
      meaning the LOD1-3 offset chain is still wrong even though LOD0 alone
      parses to sane, bounded, valid-normal geometry). It is *not* a wrong
      asset. Do not revert to "unresolved gap, wrong asset" framing again.
    - Fixing `pak_extract.py`'s disc=0x02 drift bug for the whole `prefabs/`
      subtree (not just this one item) is unstarted follow-up work -- it
      would let every other `.prefab` we've had to guess-map by filename
      instead be confirmed directly, the way this one now is.

15. **The actual, final resolution: `PathwayPuncher.fbx` was never a "TestPE
    legacy format" file at all -- it's a completely standard production
    HMD\x06 file, identical in kind to every other tool mesh in this project.
    Everything in findings 11-14 about a mysterious undocumented "G-style"
    format was chasing two compounding, unrelated bugs, not a real format.**

    - **Bug 1, in `tools/pak_extract.py`:** the stored disc=0x00 `pos` for
      this one pak entry pointed 13 bytes *past* the file's real start. The
      real `"HMD\x06"` magic sits at absolute pak offset 1,950,078,304; our
      extraction started at 1,950,078,317 (exactly `HEADER_SIZE` bytes late)
      and so began reading from partway through the real header/props
      section instead of from the magic. Found by brute-force searching a
      wide byte window around the assumed position for a literal `b'HMD'`
      match -- the same technique that cracked the disc=0x02 prefab drift in
      finding 14. Root cause not audited pak-wide (unclear if other disc=0x00
      entries share it); fixed for this one file by re-extracting the
      correct byte range by hand and overwriting the file in `pak_out/`.
    - **This explains the entire "TestPE format" saga in retrospect:** our
      `tools/hmd_parse.py` G-style heuristic parser was never reading a real
      alternate format -- it was reading random offset data from partway
      through a real production HMD file's own props/attribute-block
      section, which happens to *also* contain literal `position`/`normal`/
      `tangent`/`uv` attribute-name strings (both formats use the same
      attribute-block encoding), enough to make the heuristic parser produce
      plausible-looking but fundamentally wrong structure. Every fix layered
      on top of it in findings 11-14 (index-offset refinement, axis
      permutation, degenerate-triangle filtering) was really just
      coincidentally-successful pattern-matching against garbage, not a
      correct decode -- which is exactly why proportions and detail kept
      coming out subtly wrong no matter how much the heuristics were tuned.
    - **Confirmed via the actual game engine, not just inference:** built a
      real HashLink bytecode decompiler (`hlbc`, github.com/Gui-Yom/hlbc,
      installed via cargo) against the shipped `hlboot.dat`. It initially
      failed to parse (`Invalid type kind '23'`, `Unknown opcode 101`) --
      confirming this game runs a customized Heaps/HashLink fork, consistent
      with CLAUDE.md's existing note -- fixed by patching hlbc to stub the
      unknown type kind and add the missing `Catch` opcode (`OP(OCatch,J,X,X)`
      per the real, current `hashlink/src/opcodes.h`, index 101, the one
      opcode newer than hlbc's own bundled table). With that patch, `hlbc`
      loads the game's real bytecode and decompiles it. Traced the actual
      model-loading call chain end to end: `hrt.prefab.Model.makeObject` ->
      `ContextShared.loadModel` -> `ModelCache.loadModel` ->
      `ModelCache.loadLibraryData` -> `hxd.res.Model.toHmd` ->
      `hxd.fmt.hmd.Reader.readHeader`, and confirmed `readHeader`'s real,
      decompiled first check is a hard `if (h != "HMD") throw "FBX was not
      converted to HMD"` with no legacy-format branch anywhere in the chain.
      This is what proved the "legacy TestPE format" theory couldn't be
      right (this call chain cannot load a file lacking real HMD magic) and
      motivated re-checking the extraction itself rather than the format.
      **To reproduce this setup in a future session** (cargo/rustc are
      installed on this machine at `~/.cargo/bin`, just not always on PATH --
      check there before assuming they're missing): `cargo install hlbc-cli`
      installs a working but too-old `hlbc.exe` that fails on this game's
      bytecode; instead `git clone --recursive https://github.com/Gui-Yom/hlbc`,
      remove `"crates/gui"` from the root `Cargo.toml`'s workspace members
      (its `egui_ui_refresh` path dependency isn't fetched by the clone and
      isn't needed for CLI use), then in `crates/hlbc/src/read.rs`'s
      `Type::read` add a `23 => Ok(Void)` arm (a stand-in for a modified-fork
      type kind not in any public spec) before the catch-all error arm, and
      in `crates/hlbc/src/opcodes.rs` add a final `Catch { offset: JumpOffset
      }` variant to the `Opcode` enum after `Asm` (matching pattern of the
      `Trap`/`JAlways` variants for how a `JumpOffset` field is declared).
      Then `cargo install --path crates/cli --force`. Useful commands once
      loaded: `sfn`/`fnamed` need an *exact* function name (both are
      unimplemented substring search, despite `sstr` for strings being a real
      substring search) -- more reliably, dump broad ranges (`string ..`,
      `fnh ..`) to a file and `grep` locally; `refto string@<idx>` finds
      references to a string constant; `decomp <idx>` decompiles a function
      to pseudo-Haxe.
    - **Bug 2, in `tools/hmd_parse_prod.py`/`hmd_convert_v2.py`:** once
      re-extracted correctly and parsed as real production HMD via
      `hmd_parse_heaps.parse`, conversion still crashed (`NaN` during
      `write_bin`'s quantization). Cause: `read_verts_f16` hardcodes
      float16-precision vertex positions, but this file's actual `position`
      field type code decodes (per `stride_bytes`' own field-type table) to
      **float32**, not float16 -- reading it as f16 reinterpreted real
      float32 bit patterns as two garbage float16 values, producing NaN for
      about 6% of vertices. Fixed generically with a new
      `hmd_convert_v2.read_verts_generic()` that checks the real declared
      precision of the position field (via the same `typ >> 4` precision
      bits `stride_bytes` already decodes) and only falls back to
      `read_verts_f16` when the field really is float16. This is a real,
      previously-latent bug in the shared v2 pipeline -- worth keeping an
      eye out for on any future file whose position field isn't float16.
    - **Net result:** `PathwayPuncher` now converts through the exact same
      `hmd_convert_v2.py`/`hmd_parse_heaps.py` pipeline as every other tool
      mesh (removed from `FALLBACK_TO_V1` in
      `batch_convert_modules_v2.py`), using the file's own real per-model
      transform (position, a real 90 degree Z rotation, and a genuine
      non-uniform scale `(0.9237, 0.9237, 0.4499)` -- the actual source of
      the elongated, flattened proportions the in-game reference shows, not
      a guessed/eyeballed `dims` value). Visually confirmed correct by the
      user afterward. Also recovered `PathwayPuncher`'s real crafting recipe
      from `data.cdb` while investigating (`Module Kitx1, Quantic
      Graphenoidx3, Hyper Lensx1, Diffraction Gratingx20`), replacing the
      `"Unknown"` placeholder.
    - **Everything in findings 11-14 describing a "TestPE format," "G-style
      parser," or `Pathway_Puncher` needing special-case legacy handling is
      now historical only** -- kept for the record of how the investigation
      unfolded, but do not treat it as current guidance. The file is a
      normal production HMD file; use the normal pipeline.

16. **Sub-part mounting bug affecting multiple turret-rig tool items (Simple/
    Crude/Overclocked Mining Laser, Cooling Laser, Scanner) fixed with real
    ground-truth data instead of a geometric guess.** Some of these files'
    raw HMD `parent` index for a sub-part (e.g. "Mining_Arm", "Receiver") is
    simply wrong -- it resolves to some arbitrary `Base` LOD slot instead of
    the real mount point, confirmed by comparing bounding boxes (e.g.
    MiningTool1_OC's Receiver ends up with a world position that doesn't
    overlap "Mining_Arm" on any axis). Severity varies a lot per file (from
    "barely noticeable" to "visibly floating"), which is why it went
    unnoticed on some items and not others.

    A from-scratch bounding-box-overlap heuristic (test every sibling as a
    candidate parent, pick whichever maximizes overlap) was tried first and
    discarded: tuning it to fix one confirmed-broken file's joint kept
    silently breaking another confirmed-fine file's joint, because "the two
    parts' bounding boxes happen to overlap" is not the same thing as "this
    part is actually mounted here" -- a bbox is a crude, non-manifold-aware
    proxy, and the user correctly pushed back on trusting it blindly across
    the whole catalog.

    **The real, unambiguous answer already exists in the game's own data:**
    every one of these items' `.prefab` defines a `constraint` object (e.g.
    "BarrelConstraint") whose `target` field is a dotted socket path --
    e.g. `"MiningTool1_OC.Rotary.Mining_Arm.Receiver"` -- the literal,
    authoritative mount chain. `tools/find_socket_chain.py` locates a mesh's
    own prefab in the raw pak (keyed on the mesh's `.fbx` source path, not
    the prefab's file name or the item id -- confirmed these can all
    disagree: `Simple_Mining_Laser.prefab`'s internal model node is actually
    named "MiningTool") and parses its constraint target(s) using
    `hbson_parse.py`. A single prefab can have multiple constraints
    referencing the same socket at different, inconsistent depths (one
    targets `"MiningTool1_OC.Rotary.Receiver"`, another targets
    `"MiningTool_Upgrade.Rotary.Mining_Arm.Receiver"` -- a stale root name
    apparently copy-pasted from a template item, but a more complete path)
    -- `find_socket_chain` collects all of them and keeps the longest
    validated chain. `hmd_convert_v2.py`'s `apply_socket_chain()` then
    overrides any part whose declared parent disagrees with this confirmed
    chain, for every LOD variant.

    Where a prefab has no constraint at all (`Radar.fbx` / Crude Resource
    Detector's `Camera`/`Arms` relationship -- visually looks like it might
    have the same kind of issue, ~4% bbox overlap vs ~98% if reparented, but
    unconfirmed), this is a deliberate no-op: the file's own declared parent
    is left completely untouched rather than guessed at. That item's joint
    may still need attention if it turns out to look wrong in practice, but
    fixing it would require either finding some other ground-truth source or
    accepting a heuristic guess -- not done here.

17. **The disc=0x02 "cumulative sum from a fitted base constant" position
    formula (D02_BASE/D02_DRIFT, documented above under "PAK disc=0x02
    Extraction" in earlier revisions of this file) was never correct -- it
    was an approximation that happened to track the truth closely for files
    stored in the same order as the directory tree, and diverged badly
    (anywhere from tens of bytes to tens of thousands of bytes, non-
    monotonically) wherever real on-disk storage order differed from
    directory-traversal order. This was discovered while investigating why
    `ui/icons/sprite_sheet_icon_64.png` (needed to extract real icons for the
    5 items added to the catalogue this session) could not be decoded as any
    known image format at its cumulative-sum-computed position.

    A from-scratch self-correcting scanner (scratchpad-only, not promoted to
    `tools/` since it's now superseded) tried to patch this by searching
    forward for the next magic-byte occurrence whenever validation failed at
    the computed position, carrying the correction forward as the new
    cumulative base. That produced 215 "corrections", but the very first one
    (index 0, an 8.2MB file) was a false positive -- `HMD` occurred at byte
    8,203,328 as a coincidental substring in unrelated binary payload, not a
    real header -- and because corrections were carried forward, that one
    false positive silently corrupted every position computed after it,
    including entries later proven fine at their pure cumulative-formula
    position (e.g. `Gravitron.fbx`, `ui/icons/sector.png`).

    Rerunning validation without cascading corrections (always computing the
    pure formula position fresh per entry, `tools/pak_verify_positions.py`)
    showed the truth was more specific: `Buildings/SpaceStation/*` and
    `prefabs/*` entries had *genuine* non-trivial drift (confirmed via
    `HBSON`/`HMD` magic search near, but not at, the computed position), while
    `Main_Structures` hull frames had only a tiny, perfectly constant offset
    per size class (exactly -32 bytes for 12x6x2/12x6x4, exactly -16 bytes for
    16x6x2/16x6x4) and `Vehicules/Buildings_Parts/Tools/*` (everything this
    session's mesh conversion pipeline actually depends on) validated with
    zero error. None of this fit a single clean model -- which was the signal
    to stop guessing at byte layouts entirely.

    **Root cause, found via the compiled game itself** (per the user's
    explicit suggestion to use the game engine as ground truth, exactly as
    was done earlier for the HMD/HBSON formats): the patched `hlbc` HashLink
    decompiler (see finding 8 setup) was pointed at the real
    `hxd.fmt.pak.Reader.readFile` function in `hlboot.dat`
    (`hlbc hlboot.dat -c "decomp <findex>"`, findex found via `sfn`/grep over
    a dumped function list). The decompiled pseudocode revealed that
    non-directory pak entries read a conditional field (double vs int32
    depending on a flags bit) followed by `dataPosition`, `dataSize`,
    `checksum` -- i.e. **the real, authoritative position is a value stored
    directly in the directory entry, not something to be derived from
    cumulative file ordering at all.** The disc=0x02 payload previously
    labeled `bsphere_x(float,4) + bsphere_r(float,4) + size(4) + hash(4)` is
    actually `stored_pos(double, 8 bytes) + size(4) + hash(4)` -- same total
    byte count (16), different interpretation of the first 8 bytes.

    Empirically confirmed exact (zero byte error) by reading that double for
    three independent, already-verified anchors and comparing against their
    known-correct real position:

    | File | stored double | + dir_size (346,608) | known-correct real position |
    |---|---|---|---|
    | `Tools/Gravitron.fbx` | 14,392,831,360.0 | 14,393,177,968 | 14,393,177,968 (exact) |
    | `Main_Structures/12x6x2/12x6x2_A.fbx` | 14,218,445,968.0 | 14,218,792,576 | 14,218,792,576 (exact) |
    | `Main_Structures/16x6x2/16x6x2_A.fbx` | 14,220,999,920.0 | 14,221,346,528 | 14,221,346,528 (exact) |

    (`dir_size` is the same `headerSize` field the pak's own header already
    stores at byte 4-7 -- no separate empirically-fitted constant needed.)

    Applying `abs_pos = int(stored_pos) + dir_size` universally and
    re-running `tools/pak_verify_positions.py` across the whole pak: **0
    failures out of 3,030 validatable entries** (`.fbx` via `HMD` magic,
    `.prefab` via `HBSON` magic, `ui/icons/*.png` via the standard PNG magic
    `89 50 4E 47 0D 0A 1A 0A`) -- including every previously-broken
    `Buildings/SpaceStation` and `prefabs/` entry, and the very file this
    investigation started over. `ui/icons/sprite_sheet_icon_64.png` decodes
    at its corrected position as a genuine, valid 1280x640 RGBA **standard
    PNG** -- there is no custom BC1/BC3 format involved for this file at all;
    the entire earlier "which compressed texture format is this" investigation
    was chasing a symptom of the position bug, not a real format mystery.
    (Material-library `*_basecolor.png` textures are still genuinely BC1/BC3
    compressed -- that finding, documented separately, is unaffected.)

    `tools/pak_extract.py` has been updated to use this formula directly (the
    old `D02_BASE`/`D02_DRIFT`/cumulative-sum code path and constants have
    been removed entirely, not kept as a fallback) -- do not reintroduce
    cumulative-sum position math for disc=0x02 entries. `tools/
    pak_read_stored_position.py` and `tools/pak_verify_positions.py` are kept
    as the derivation proof and the regression guard, respectively.

18. **`_meshScale` fudge factors on non-hull "build" parts (engines,
    cockpits) were fixing the wrong thing -- the real bug is always in
    `dims`, never a missing multiplier.** `fitGeom()` (shipbuilder/js/
    meshLoader.js) fits a part's *raw, unscaled* mesh bbox into its `dims`
    grid box via a single uniform `s = min(dims/bbox)`, then applies
    `_meshScale` as an *extra* multiplier on top. `dims` for non-hull parts
    is NOT sourced from real game data (confirmed: no footprint/size field
    exists in `data.cdb` for engines or cockpits, unlike hull frames whose
    `WxHxD` comes directly from real pak folder/shape names) -- it was
    invented/eyeballed by an earlier session for this fan tool's own grid
    placement system. When that eyeballing was done against old, badly
    wrong/incomplete meshes (see below), the resulting `dims` no longer
    matches the real mesh's true proportions, and someone then reached for
    `_meshScale` to paper over the mismatch instead of fixing `dims` itself.

    This was tried and failed twice before landing on the right fix:
    - First attempt: computed `_meshScale` from `real bbox × real prefab
      scale ÷ dims`, i.e. tried to make the mesh's true absolute size (real
      prefab `scale` field, read directly from the .prefab -- see finding
      16 for the discovery that this is applied as a literal, un-transformed
      multiply, not any "2 - scale" formula) fill the *existing* `dims` box.
      Backfired immediately: Quiet Breeze Thruster visibly overflowed its
      grid-cell wireframe (any `_meshScale` > 1 necessarily does, since
      `s=1` already means "just touching the box on the tightest axis"),
      and Silent Thruster/Long Haul Booster -- already flagged *before* any
      of this as "too big" with no correction applied at all -- got a
      `_meshScale` > 1, making a known-oversized part even bigger. Reverted.
    - Root cause of the failure: **the shipbuilder's own render pipeline
      does not apply a part's real prefab `scale` field to the mesh at
      all** -- `hmd_convert_v2.py` only ever reads the `.fbx`, never the
      `.prefab`, so the "raw bbox" fed into `fitGeom` is unscaled native
      mesh geometry, and comparing `bbox × real_scale` against the
      *existing* `dims` conflates two different reference frames.
    - **The actual fix**: recompute `dims` itself as `raw_bbox ×
      real_prefab_scale` (in the same axis convention the stored `dims`
      field already uses -- see `partDims()` in main.js for the swap
      per part kind), round to whole numbers, and delete `_meshScale`
      entirely. This makes the box match the mesh's true proportions
      directly, so `fitGeom`'s existing uniform-fit-to-box logic does the
      right thing on its own with no extra multiplier needed. Confirmed
      visually correct (mesh fills its box with no overflow and no
      floating) for all 6 engines and all 11 cockpits.

    Concretely, for the 6 engines: 4 of 6 (Cart Pusher, Voidseeker, Silent
    Thruster, Long Haul Booster) already had `dims` matching their real
    `bbox × scale` almost exactly once computed properly -- meaning their
    old `_meshScale` values (Voidseeker's `0.68`, in particular) were pure
    accidental compensation for finding 16's Voidseeker/Quiet-Breeze
    real-scale attribution swap, not a real, independent finding. Only
    Quiet Breeze (`[5,2,3]` -> `[7,3,4]`) and Grasshopper (`[5,3,3]` ->
    `[5,3,2]`) needed an actual `dims` correction.

    For the 11 cockpits, the old `dims` were wrong on every single one, in
    a consistent direction (height underestimated, width overestimated) --
    strong evidence the original values were eyeballed against `.bin` mesh
    files that were themselves badly incomplete: swapping in the real,
    fully re-converted meshes (via `hmd_convert_v2.py`) increased vertex
    counts by roughly 5-9x and material group counts by roughly 3-8x
    across the board (e.g. Cockpit_TC1: 15,467 -> 138,370 vertices, 4 -> 32
    groups) -- the previously-shipped cockpit meshes were, in effect, a
    much cruder placeholder than the real available data, not just
    slightly-off proportions.

    One cockpit, Cockpit_DA1 ("Cocoon"), is a genuine two-file compound
    mesh: its real prefab (`prefabs/ships/parts/cockpit/Cockpit_DA1.prefab`)
    places `Cockpit_DA1_INT.fbx` and `Cockpit_DA1_EXT.fbx` as siblings
    under a shared node with its own scale, each with its own additional
    position/scale. `tools/merge_prefab_parts.py` is a new, reusable tool
    that composes each source file's own internal model hierarchy (via the
    same `hmd_convert_v2` machinery) and then applies the additional
    cross-file scale-then-translate transform read from the real prefab,
    before merging vertex/index/group buffers into one `.bin`. Use this
    for any future item found to be a genuine multi-file compound (check a
    part's real `.prefab` for more than one sibling `model` node under a
    shared parent, the same way Cockpit_DA1 was found here) rather than
    guessing at a merge.

19. **Cockpits needed a completely different rotation than the old
    single `rotateY(90°)`, AND `partDims()` had an inverted, cockpit-only
    storage convention bug -- both had to be found before dims made sense
    at all.** The old comment ("cockpits are already Y-up in source but
    face +X") was simply wrong for the real, fully-converted meshes (it may
    have been true for the old placeholder meshes finding 18 replaced).
    Guessing single-axis corrections one at a time (`rotateX`, extra
    `rotateY`, sign flips) each produced a different wrong result (sitting
    on the wrong side, facing backward, facing upward) -- the only thing
    that actually worked was brute-forcing it: render **all 64**
    combinations of `rotateX/Y/Z` at 0/90/180/270 for one cockpit and
    visually pick the correct one against the real game. Confirmed answer,
    applied in that exact order: `rotateX(90°) -> rotateY(180°) ->
    rotateZ(180°)`.

    Separately, `partDims()`'s cockpit branch destructured `part.dims` as
    `[l, h, w]` -- note the **h and w swapped** from every other part kind's
    `[l, w, h]` -- a leftover compensation for the old (wrong) rotation.
    With the rotation fixed, this became actively harmful: the inspector
    correctly shows `dims.join('×')` for every part, but a `[l,h,w]`-stored
    array reads back as `L×H×W`, not `L×W×H` (confirmed concretely: Cocoon
    Cockpit displayed "4×4×8" while its real proportions read as "8×4×4").
    Fix: deleted the special-case branch entirely so cockpits use the exact
    same `[l,w,h]` formula as hull frames, and rederived every cockpit's
    `dims` from scratch to match (see `tools/compute_part_dims.py` below).

    New tool **`tools/compute_part_dims.py`** derives a part's `dims`
    directly from its real `.bin` mesh (applies the same rotation sequence
    `fitGeom()` uses, computes the resulting bounding box, inverts
    `partDims()`'s formula) instead of hand-reading a screenshot and typing
    rounded numbers into JSON -- the manual version of this process is what
    produced most of this session's dims mistakes (wrong axis order, wrong
    storage convention, arithmetic slips). Prefer this tool over hand
    calculation for any future part whose dims need rederiving.

20. **Outside-mount modules had a real display-text bug (not a rendering
    bug): `partDims()` intentionally passes their `dims` straight through
    as raw `[X,Y,Z]` mesh-space values (X=length,Y=height,Z=width per this
    session's now-confirmed axis convention), to avoid re-deriving their
    real per-part dims (see the existing comment about Large Solar Panel
    at the top of `partDims()`).** That's fine for rendering, but the
    inspector's dims text (`part.dims.join('×')`, main.js) assumes every
    part's stored array already reads as `L×W×H` -- true for hull/cockpit
    (`[l,w,h]`) but NOT for outside modules, whose stored order is
    `[L,H,W]`. Concretely, Large Solar Panel stored `[5.38, 0.61, 3.53]`
    displayed as "5.38×0.61×3.53", misreadable as 3.53 tall (its real
    height is 0.61 -- the flat panel's thickness -- and 3.53 is its real
    width). Fixed with a display-only `displayDims()` helper that reorders
    `[X,Y,Z] -> [X,Z,Y]` for outside-mount modules before joining; it does
    not touch `part.dims` itself, so rendering and grid placement (which
    correctly rely on the raw `[X,Y,Z]` order) are untouched.

    Separately (same session, prompted by "modules box dimensions might
    need to be rounded"): outside-module `dims` were stored as raw,
    unrounded mesh-space floats (e.g. `[5.38, 0.61, 3.53]`), which is ugly
    to display and awkward for grid placement. Simply rounding those
    numbers up is unsafe on its own -- `fitGeom()` scales the mesh
    uniformly to fill whatever box `dims` specifies, so growing the box
    (rounding up) grows the rendered mesh unless compensated. New tool
    **`tools/round_module_dims.py`** rounds every outside-module's `dims`
    up to the nearest integer per axis, then computes the exact single
    uniform `_meshScale = 1 / min(rounded_dims_i / raw_size_i)` that cancels
    the resulting box growth back out -- confirmed algebraically that this
    factor is axis-independent (final size on every axis reduces to
    `raw_size_i` again, unchanged), and confirmed visually on Large Solar
    Panel and Giant Laser that the rendered mesh size didn't change.
    Applied to all 23 outside-mount modules.

    **Open architecture concern, not addressed this session:** `dims`
    is forced to serve two different roles at once -- the grid-placement
    footprint (`isFree`/`stackHeight` in main.js) and the `fitGeom` render-
    scale target -- and every dims bug this session (thrusters, cockpits,
    modules) came from those two roles drifting apart. A more robust design
    would derive render scale from the mesh's own bounding box directly
    (always self-consistent) and let `dims` be a pure placement stat that
    never feeds `fitGeom`'s scale-to-fit math at all. Flagged to the user;
    not attempted since it would touch `fitGeom`, `partDims`, and every
    part's data.

21. **Follow-up to finding 20's architecture concern: `dims` and render
    scale are now decoupled for every non-hull part kind, via a new
    `part._renderSize` field.** Confirmed with the user first that this
    should NOT touch hull frames (`_dimd` parts) at all -- those genuinely
    are grid-tiled in the real game (real `WxHxD` sourced from pak folder
    names, meshes modeled to nearly exactly fill their box), so the
    "stretch mesh to fill dims exactly" behavior is correct and desired
    there. For everything else, `data.cdb` has no footprint/size field at
    all (checked directly) -- `dims` for engines/cockpits/outside-modules
    was always a fan-tool invention for grid placement, never a real game
    value, so tying render scale to it was never correct in the first
    place.

    `fitGeom()` (meshLoader.js) now checks, in order: `_dimd` (hull,
    unchanged) -> `_renderSize` (new: scale directly to this real,
    independent size, then center the result in the `dims` box on X/Z,
    floor on Y) -> old dims-fit + `_meshScale` fallback (only reached for
    parts not yet migrated, e.g. wings, which weren't touched this
    session since their sizing hasn't been reviewed at all).

    New tool **`tools/compute_render_size.py`** derives `_renderSize` per
    part kind:
    - cockpit / outside-module: the real, *unrounded* mesh size (same
      computation as `compute_part_dims.py`/`round_module_dims.py`, just
      without the integer rounding those use for the separate, now fully
      independent `dims` display/placement value).
    - thruster: deliberately NOT re-derived from scratch. Back-solved from
      each thruster's *current* rendered appearance (existing dims-fit
      scale x existing `_meshScale`, both already in the data) so this
      refactor is a pure mechanism change with zero visual difference --
      thrusters' real size is still an open, unresolved question (see
      finding 18) the user wants to check against real in-game references
      before touching again, and this refactor deliberately does not
      reopen that question.

    `_meshScale` is now dead everywhere it was set (Voidseeker, Grasshopper,
    every outside-module) and was deleted from those parts' data rather
    than left as stale, unused residue.

    Confirmed visually identical (byte-for-byte same render) for cockpits,
    outside-modules, and thrusters after migration, and confirmed hull
    frames and the `fitGeom` fallback path are completely untouched.

    This work was done on a separate branch/worktree
    (`decouple-dims-render`, with a `backup-pre-decouple` branch/worktree
    at `../Spacecraft-backup` holding the exact pre-refactor state) at the
    user's request, specifically so there was a known-working fallback
    available during a change broad enough to touch every non-hull part's
    data.

22. **Wings migrated to `_renderSize` too; two real bugs found in the
    process, both from the same root cause -- axis-swapping rotations
    applied around `fitGeom`'s bounding-box measurement without every
    downstream consumer knowing about the swap.**

    First bug: `_meshRot` (a per-part extra `rotateY`, `-90°` on the
    `MK1_Little_*` wing shapes and `+90°` on `MK1_Midd_*`) was omitted
    from the initial `_renderSize` computation for wings, since it isn't
    applied anywhere else in the codebase. Checked whether `_meshRot`
    itself is a real game value or another fan-tool invention like
    `_meshScale`: it's not present in any real game data structure (only
    `fitGeom`/`ship_editor_data.json` reference it), but unlike
    `_meshScale` it isn't a droppable fudge factor either -- it was added
    when the previously-used wing meshes were replaced with real
    PAK-extracted ones, because the `Little` and `Midd` mesh families are
    genuinely authored 90° apart in their own raw vertex data. Confirmed by
    comparing each wing's X/Z bounding-box order with and without
    `_meshRot` against that wing's original (long-validated) `dims`: only
    the with-`_meshRot` computation matches the expected orientation on
    all 4 wings. So `_meshRot` is kept, and `render_size_wing()` /
    `dims_wing()` in `compute_render_size.py` now include it before
    measuring the bounding box, same as `fitGeom` already did. Re-running
    `--all-wings --write` produced `_renderSize` matching the mesh's real
    proportions and only minor `dims` corrections for Wing_03 (w: 3->2)
    and Wing_04/Condor (l: 9->8) -- consistent with the user's own
    prediction that Condor "might be wrong as well as right" since its
    mismatch was smaller and harder to spot by eye than the other three
    wings' clearly-stretched meshes.

    Second bug, found right after: the `Flip Y` button mirrored a
    different world-space axis depending on whether the thruster/wing
    orientation toggle (`rz`) was on. Root cause is the same shape as the
    first bug and as finding 21's `rz`/`_renderSize` fix -- `fitGeom`
    applies the user's flip (`g.scale(fx?-1:1, fy?-1:1, fz?-1:1)`) in
    local mesh space *before* the extra `rotateX(±90°)` that `rz` adds
    later, and that rotation swaps the local Y/Z axes. A flip baked in
    beforehand therefore ends up mirroring whatever axis Y maps to after
    the swap, not always the same world axis. Fixed by swapping `fy`/`fz`
    right before the flip is applied whenever `rz` is set, mirroring the
    exact pattern already used for `_renderSize`'s `rh`/`rd` swap and the
    pre-existing `effDims()` `[h,d]` swap in `main.js`. Confirmed visually
    correct on both a thruster and Condor wing, flipped and toggled
    between horizontal/vertical orientation in all four combinations.

23. **The 3 WIP "Decorative" catalogue items (Big Intake Vent/`Aerator_Thin_01`,
    Intake Vent/`Aerator_Thin_02`, Round Hatch/`Aerator_Circle_01`) were never
    actually blocked by an animation/skin section -- their `pak_out` copies
    were just stale, extracted before finding 17's disc=0x02 position-formula
    fix.** Investigating why `hmd_convert_v2.py` choked on
    `Aerator_Thin_01.fbx` (`IndexError` deep inside `read_format()`) found the
    file's only `HMD\x06` occurrence sitting 46 bytes from EOF instead of near
    the start (compare `Water_Collector.fbx`: magic at byte 448 of 2.9MB, as
    expected) -- i.e. the on-disk copy was reading mostly wrong/unrelated
    bytes. Re-extracting fresh from `res.pak` with the current `pak_extract.py`
    (`stored_pos + dir_size`, no cumulative-sum math) put the magic at byte 0
    for every Decoratives_Parts `.fbx`, prefab, and even the 3 files previously
    listed in `batch_convert_modules_v2.py`'s `FALLBACK_TO_V1` set
    (`Spot_Light_01`, `Spot_Light_Barrel`, `Aerator_Spot_01`) specifically
    because of a supposed "animation/skin section the new reader doesn't
    handle" -- **that reason was never real; all 6 files convert cleanly
    through `hmd_convert_v2.py` once freshly extracted, real per-model scale
    and all** (e.g. `Aerator_Circle_01` scale 0.7, `Aerator_Thin_01` scale 1.5).

    Root cause: `pak_out` is a point-in-time local mirror (per CLAUDE.md,
    built via `--all` for reverse-engineering), not something re-synced
    automatically when `pak_extract.py`'s position formula changes. Any
    subtree extracted before finding 17 (or before any later extractor fix)
    stays stale until someone re-runs extraction against it -- there is no
    version check tying a `pak_out` file to the formula that produced it.
    Confirmed via the user's own suspicion ("this is not the first file that
    seems to have issues") that this wasn't an isolated case: a scoped rescan
    of `Vehicules/Buildings_Parts/Tools` (the *other* subtree the modern
    pipeline depends on) came back clean (every file's magic within the first
    5,000 bytes), so the staleness was specific to Decoratives_Parts having
    never been touched since finding 17, not a second live formula bug.

    **Fix applied: re-ran `pak_extract.py --all --out pak_out`, refreshing
    the entire local mirror (8,231 entries, 0 errors) in one pass** rather
    than patching subtrees one at a time as they happen to get noticed --
    this is now the recommended move any time a file looks structurally
    wrong before assuming a new format quirk or a live extractor bug. `git
    diff --stat` afterward is a fast way to see exactly which subtrees were
    actually stale (only files whose extraction differed from before will
    show as changed).

    `FALLBACK_TO_V1` in `batch_convert_modules_v2.py` and the old
    `hmd_to_bin.py`-path fallback code in `batch_convert_modules.py` are now
    dead weight for these 3 files specifically and should be removed once
    Spot_Light_01/Spot_Light_Barrel/Aerator_Spot_01 are reconverted through
    v2 and re-verified.

24. **Auditing which repo assets had actually been verified through this
    project's own pak-extraction pipeline versus never revisited since
    they were first added** (done in response to the repo going public)
    found and fixed several real gaps, and one dead end worth recording
    so it isn't retried:

    - 16 more hull-frame `.bin` files were stale for the same reason as
      finding 23 (pre-dating the position-formula fix) -- re-running
      `batch_convert_hulls.py` against the freshly-refreshed `pak_out`
      fixed them; vertex/index/group counts were unchanged, confirming a
      positional correction, not a structural break.
    - 28 mesh keys used only by `mount: 'inside'` modules (batteries, FTL
      engines/tanks, cargo/liquid storage, some shields) turned out to have
      **no `.bin` file ever committed at all** -- just stale manifest
      entries pointing at nothing. Confirmed via `buildPartMesh` or
      `isInsideMod`: inside-mount modules never load their mesh at all
      (rendered via 2D icon sprite only), so this was always inert;
      removed the dead manifest entries.
    - `Booster.bin` (the "LR Speedster" booster, `kind: 'build'` so it
      *is* rendered when placed) had never been wired into any conversion
      tool. Reconverted from
      its real source (`Tools/Booster.fbx`, real prefab scale 0.5) and
      derived real `dims`/`_renderSize` -- the old box (2x1x1) was well
      undersized against the real ~3.7x1.5x1.5 mesh.
    - All 142 catalogue icons were regenerated via `extract_item_icons.py`
      in one pass (every catalogue id matches a `data.cdb` item id
      directly). 132 of 142 actually changed content, confirming they
      weren't real extracted icons before.
    - **Dead end, don't retry:** for the 14 hull shape-picker thumbnails
      (`ship_shapes/A.webp`..`N.webp`), rendering fresh thumbnails
      ourselves from the real (now-verified) mesh geometry was tried and
      rejected -- not reliable, since matching the real in-game icon's
      exact framing/style from raw geometry alone is guesswork. **The
      correct fix was finding the real in-game icons instead.** They're
      not in the `item` sheet (which only has per-item icons) but in a
      separate, dedicated **`icon` sheet** (`data.cdb['sheets'][0]`,
      name `"icon"`) -- each `PieceShapeA`..`N` id there has its own exact
      `{file: "ui/icons/BlocksShapesIcons.png", size: 32, x, y}` entry.
      `ui/icons/BlocksShapesIcons.png` itself is a real sprite sheet
      extracted from `res.pak` (previously only known to us via a hand-
      traced approximation from a reference screenshot, `ship_shapes/
      icons screenshot.png` -- removed, along with the equally-unused
      `rotation.png`, neither ever referenced by any code). All 14 real
      crops matched the existing hand-traced shape *assignment/order*
      almost exactly, just with hand-drawing imprecision -- confirms the
      original author correctly identified the game's real shape-picker
      order by eye, they just didn't have pixel-accurate source crops.
      When a `data.cdb` id doesn't resolve in the sheet you expect, check
      for a same-named id in a different, more specific sheet before
      assuming a decompilation-only path is necessary.

25. **8x3x1_N's "anomalous format" was never real -- it was the same stale
    `pak_out` bug as findings 17/23/24, just diagnosed before that bug was
    known.** The original diagnosis (raw big-endian uint16 index data at
    byte 0, `HMD\x06` only appearing at byte 144 as a false positive within
    the data) was accurate *for that specific extracted copy* -- but the
    copy itself was stale, predating the disc=0x02 position-formula fix.
    After the pak-wide `pak_extract.py --all` re-extraction (already done
    for finding 24's audit, but not yet re-checked against this specific
    file), `8x3x1_N.fbx` starts with a clean `HMD\x06` header at byte 0,
    identical in shape to every other hull shape file, and converts through
    the completely standard `tools/hmd_to_bin.py` production-HMD path with
    zero special-casing -- no ring-buffer variant, no anomaly handling,
    nothing. Produces a correctly-proportioned ~9x3x1 mesh (bbox
    `[-4.50,-1.50,-0.01, 4.50,1.50,1.00]`). Re-ran `batch_convert_hulls.py`
    to regenerate it through the normal pipeline and added the shape back
    to all 6 material variants of the 8x3x1 catalogue entry (13 → 14
    shapes each) that had deliberately excluded it. Confirmed visually
    correct via the shape picker. **All 130 of 130 hull shapes are now
    real, pak-verified, and in the catalogue -- zero remaining exceptions.**
    General lesson: any "anomalous format" or "can't be parsed" diagnosis
    made before finding 17 (the disc=0x02 position-formula fix) should be
    treated as unconfirmed and re-checked against a fresh extraction before
    being treated as a real, permanent format limitation.

---

## TestPE Format (legacy reference)

TestPE files (`assets/Buildings/Props/TestPE/`) use disc=0x00 and are extractable via pak_extract.py.
Format differs from production: float32 positions in game units (÷100 to get grid units), big-endian uint16 indices.

Most of these files were used for early research and are not the assets shown
in-game. **Correction (finding 11): this is not true for every TestPE file** --
`Pathway_Puncher.fbx` is confirmed via `data.cdb`'s own `visual.model` field
to be the real, currently-shipped mesh for `PathwayPuncher` (Spacetime
Puncher). Don't assume a TestPE file is a research leftover without checking
whether any current `data.cdb` item actually references its prefab. The
G-style parser (`tools/hmd_parse.py` / `hmd_to_bin.py`'s G-style path) handles
them; no further format work is planned, but individual TestPE-sourced items
may still need adding if more are found missing.

Key difference from production: **big-endian** uint16 indices (production uses little-endian).
