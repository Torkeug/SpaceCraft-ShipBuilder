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

> **Do not use .har files as reference — use only the in-game extracted files from pak_out.**

---

## PAK disc=0x02 Extraction

disc=0x02 directory entries do NOT store a pos field. Instead, files are stored sequentially in directory-traversal order starting at a fixed absolute offset in the PAK file:

```
D02_BASE       = 2_156_306_928   # where disc=0x02 block starts in res.pak
D02_DRIFT      = 8_464           # gap from D02_BASE to first actual file byte
D02_DATA_START = D02_BASE + D02_DRIFT = 2_156_315_392
```

Each disc=0x02 file's absolute byte offset:
```python
cumulative = 0
for path, size in d02_files_in_directory_order:
    abs_pos = D02_DATA_START + cumulative
    cumulative += (size + 15) & ~15   # 16-byte alignment
```

The directory entry for disc=0x02 is 16 bytes: `bsphere_x(4) + bsphere_r(4) + size(4) + hash(4)`. The `pos` field used by disc=0x00 files is absent; position is derived from cumulative ordering.

**How D02_DRIFT was determined:** confirmed by finding HMD magic (`48 4D 44 06`) at `D02_DATA_START + cumulative_off` for Blasting_Missile.fbx and all 14 Main_Structures/4x3x1 files. The files are uncompressed despite the "compressed" name sometimes used for disc=0x02.

pak_extract.py implements this automatically. Both disc types are extracted the same way: just pass a pattern to `--extract`.

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

No coordinate scaling or shift is required — positions are already in grid units.

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

No scaling or shift is needed before writing positions to .bin.

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

### 8x3x1_N — separate anomalous format

8x3x1_N starts with raw big-endian uint16 index data at byte 0 (sequential values 0x0295, 0x0296...), with `HMD\x06` appearing at byte 144 as a false positive within the data. It matches none of the ring-buffer variants (raw[1] = 0x95 ≠ 0x00). The standard text-prefix path would slice from the false HMD hit and produce a garbage parse. The bounds check on the resulting `ibuf_start + ic*2 > len(data)` catches this and returns failure cleanly. The HAR-sourced bin is preserved for 8x3x1_N.

---

## Tools

| Tool                        | Purpose                                                             |
|-----------------------------|---------------------------------------------------------------------|
| `tools/hmd_parse_prod.py`   | Parser for production HMD (v0x06): `parse_prod_hmd()`, `parse_material_groups()`, `_parse_attr_blocks()`, `read_verts_f16()`, `read_indices_le_u16()` |
| `tools/hmd_to_bin.py`       | Converter: `convert_prod_style()` calls hmd_parse_prod and writes .bin; `convert_g_style_auto()` handles TestPE G-style; `write_bin()` writes the .bin format |
| `tools/hmd_parse.py`        | Legacy parser for TestPE G-style (disc=0x00) files                  |
| `tools/pak_extract.py`      | Extracts both disc=0x00 and disc=0x02 files from res.pak using cumulative offset calculation |
| `tools/batch_convert_hulls.py` | Batch converter for all Main_Structures hull sizes; updates `_manifest.json` |

**All tools must be saved to `tools/` immediately after writing, even if incomplete.**

**Running the converter:**
```bash
python tools/hmd_to_bin.py <input.hmd> <output.bin>
```
Auto-detects format: tries production (v0x06) first, then G-style, then KNOWN_FILES fallback.

**Running the batch converter (after fixing prefix/inverted support):**
```bash
python tools/batch_convert_hulls.py
```
Converts all sizes from pak_out, overwrites HAR-sourced bins, updates `_manifest.json`.

---

## Conversion Status

### 4x3x1 through 8x6x2 — COMPLETE (7 sizes × 14 shapes = 98 files)

All 98 shapes converted from pak_out to .bin with correct material groups.
Output: `shipbuilder/ship_meshes/{size}_{shape}.bin`
Manifest: `shipbuilder/ship_meshes/_manifest.json` (all 98 entries from pak_out)
Parts: `shipbuilder/ship_editor_data.json` — already complete for all hull sizes and variants.

### 12x6x2, 12x6x4, 16x6x2, 16x6x4 — COMPLETE (ring-buffer parser implemented)

All shapes for these sizes are now converted from pak_out. See "Ring-Buffer Layout" section for implementation details. 129 of 130 total shapes are pak_out-sourced; only 8x3x1_N remains HAR-sourced.

### Missing shape thumbnails (ship_shapes/)

H.webp, I.webp, L.webp, M.webp are absent from `shipbuilder/ship_shapes/`.
Cannot be sourced from HAR — must come from in-game assets.

### Hull size conversion table

| Size     | Shapes   | Status                  | Notes                                         |
|----------|----------|-------------------------|-----------------------------------------------|
| 4x3x1    | A–N (14) | ✓ DONE (pak_out)        |                                               |
| 4x3x2    | A–N (14) | ✓ DONE (pak_out)        | text prefix ~16–32 B                          |
| 6x3x1    | A–N (14) | ✓ DONE (pak_out)        | text prefix ~48–80 B                          |
| 6x3x2    | A–N (14) | ✓ DONE (pak_out)        | text prefix                                   |
| 8x3x1    | A–N (14) | ✓ DONE (pak_out, except N) | text prefix ~128 B; N has anomalous format (HAR) |
| 8x3x2    | A–N (14) | ✓ DONE (pak_out)        | text prefix                                   |
| 8x6x2    | A–N (14) | ✓ DONE (pak_out)        | text prefix ~176 B                            |
| 12x6x2   | A–N (14) | ✓ DONE (pak_out)        | ring-buffer variant 1 (A–M) and 3 (N, JSON end); geom_start corrected via ibuf scan |
| 12x6x4   | A–B (2)  | ✓ DONE (pak_out)        | ring-buffer variant 1 (A) and 3 (B, JSON end); same fix applied |
| 16x6x2   | A–N (14) | ✓ DONE (pak_out)        | variants 1, 2 (prefix/JSON); geom_start corrected via ibuf scan |
| 16x6x4   | A–B (2)  | ✓ DONE (pak_out)        | ring-buffer variant 2; same fix applied |
| MK1      | various  | not started             | Rounded_MK1_* connector pieces                |
| MK2      | various  | not started             | Rounded_MK2_* connector pieces                |

### Outside-mount modules (Tools/, Decoratives_Parts/) — 14 of 18 DONE (2026-07-02)

The 18 `mount: 'outside'` module parts (lights, mining lasers, radars, solar panels,
scanners) previously used mesh data that did not match a proper pak_out extraction
(likely HAR/website-sourced, per the same caveat as hull pieces). Re-extracted and
converted from `assets/Vehicules/Buildings_Parts/{Tools,Decoratives_Parts}/`.

Conversion tool: `tools/batch_convert_modules.py` (source path table + manifest update,
same pattern as `batch_convert_hulls.py`).

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

---

## TestPE Format (legacy reference)

TestPE files (`assets/Buildings/Props/TestPE/`) use disc=0x00 and are extractable via pak_extract.py.
Format differs from production: float32 positions in game units (÷100 to get grid units), big-endian uint16 indices.

These files were used for early research but are not the assets shown in-game. The G-style parser (`tools/hmd_parse.py`) handles them. No further work planned on TestPE format.

Key difference from production: **big-endian** uint16 indices (production uses little-endian).
