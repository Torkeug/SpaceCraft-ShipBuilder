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

---

## TestPE Format (legacy reference)

TestPE files (`assets/Buildings/Props/TestPE/`) use disc=0x00 and are extractable via pak_extract.py.
Format differs from production: float32 positions in game units (÷100 to get grid units), big-endian uint16 indices.

These files were used for early research but are not the assets shown in-game. The G-style parser (`tools/hmd_parse.py`) handles them. No further work planned on TestPE format.

Key difference from production: **big-endian** uint16 indices (production uses little-endian).
