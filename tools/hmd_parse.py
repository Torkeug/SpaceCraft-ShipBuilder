"""
hmd_parse.py -- Parser for SpaceCraft HMD (.fbx) mesh files (G-style format).

Parses vc, ic, and buffer offsets for each LOD from the binary file structure,
without hardcoded per-file values.

See tools/hmd_format_notes.md for format documentation.

Usage (as module):
    from hmd_parse import parse_hmd_g
    lods, materials = parse_hmd_g(data)

Usage (CLI):
    python hmd_parse.py <file.fbx>
"""

import struct
import sys
import math


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERTEX_STRIDE = 32
ATTR_BLOCK_MARKER = 0x0b
EXTRA_BYTES_LEN = 50

MATERIAL_ROLES = {
    'panel': 0, 'paint': 0,
    'metal': 1,
    'dark': 2, 'pom': 2, 'decal': 2,
    'light': 3,
    'emissive': 4,
    'glass': 5, 'glass_': 5,
}
DEFAULT_ROLE_RGB = {
    0: (94, 124, 162),   # paint
    1: (121, 130, 141),  # metal
    2: (34, 38, 44),     # dark
    3: (220, 220, 200),  # light
    4: (255, 200, 50),   # emissive
    5: (80, 160, 200),   # glass
}


# ---------------------------------------------------------------------------
# Step 1: Parse attribute blocks
# ---------------------------------------------------------------------------

def parse_attr_blocks(data):
    """
    Parse all LOD attribute blocks starting at byte 6.
    Returns list of dicts: {'extra': bytes, 'attrs': [(name, type), ...]}
    """
    blocks = []
    off = 6
    while off < len(data) - 2:
        if data[off] != ATTR_BLOCK_MARKER:
            off += 1
            continue
        start = off
        off += 1
        attr_count = data[off]
        off += 1
        if attr_count == 0 or attr_count > 10:
            off = start + 1
            continue
        attrs = []
        ok = True
        for _ in range(attr_count):
            if off >= len(data):
                ok = False
                break
            name_len = data[off]
            off += 1
            if name_len == 0 or name_len > 32 or off + name_len + 1 > len(data):
                ok = False
                break
            name = data[off:off + name_len].decode('ascii', errors='replace')
            off += name_len
            type_byte = data[off]
            off += 1
            attrs.append((name, type_byte))
        if not ok or off + EXTRA_BYTES_LEN > len(data):
            off = start + 1
            continue
        extra = data[off:off + EXTRA_BYTES_LEN]
        off += EXTRA_BYTES_LEN
        blocks.append({'extra': extra, 'attrs': attrs})
        # Only accept up to 10 blocks, and stop if next byte doesn't look like a block
        if len(blocks) >= 10:
            break

    return blocks


# ---------------------------------------------------------------------------
# Step 2: Extract LOD geometry parameters from attribute blocks
# ---------------------------------------------------------------------------

def extract_lod_params(blocks, file_size):
    """
    From attribute blocks, extract:
      lod_count, vc_list[], ic_list[], extra0_list[]

    Formula:
      lod_count = blocks[0].extra[4]
      vc[0]     = from file header (not here — caller provides)
      vc[N+1]   = LE u16 at blocks[N].extra[46..47]
      ic[0]     = (blocks[1].extra[0] - (vc[0]-1)*stride) / 2
      ic[N]     = (blocks[N+1].extra[0] - blocks[N].extra[0] - vc[N]*stride) / 2  (N>=1)
      ic[last]  = computed from file_size later (needs vbuf offsets)
    Returns dict with lod_count and lists indexed by LOD.
    """
    if not blocks:
        return None

    lod_count = blocks[0]['extra'][4]
    if lod_count < 1 or lod_count > 8:
        return None

    return {'lod_count': lod_count, 'blocks': blocks}


# ---------------------------------------------------------------------------
# Step 3: Find vbuf0 start by scanning for first valid vertex
# ---------------------------------------------------------------------------

def find_vbuf0_start(data, meta_start, vc2, vbuf1_offset, max_scan=300):
    """
    Find the LOD0 vertex buffer start within [meta_start, meta_start+max_scan].

    Strategy 1 (reliable): the dummy vertex has a deliberate binary encoding —
    x=y = LE uint32 1 (bytes 00 00 00 01), z = vc2 as LE uint32.
    Search for this specific 12-byte signature.

    Strategy 2 (fallback): scan for a position where:
      - the 12-byte dummy vertex decodes to all near-zero float32
      - the predicted vbuf1 position (pos+vbuf1_offset) has a real vertex (0.05<max<300)
    """
    # Strategy 1: exact dummy vertex signature
    z_bytes = struct.pack('<I', vc2)  # vc2 as LE uint32
    signature = b'\x00\x00\x00\x01\x00\x00\x00\x01' + z_bytes
    idx = data.find(signature, meta_start, meta_start + max_scan)
    if idx != -1:
        return idx

    # Strategy 2: float scan with cross-validation
    search_end = min(len(data) - 12, meta_start + max_scan)
    for pos in range(meta_start, search_end):
        x0, y0, z0 = struct.unpack_from('<3f', data, pos)
        if abs(x0) > 1e-6 or abs(y0) > 1e-6 or abs(z0) > 1e-6:
            continue
        pos1 = pos + vbuf1_offset
        if pos1 + 12 > len(data):
            break
        x1, y1, z1 = struct.unpack_from('<3f', data, pos1)
        if not (math.isfinite(x1) and math.isfinite(y1) and math.isfinite(z1)):
            continue
        m1 = max(abs(x1), abs(y1), abs(z1))
        if 0.05 < m1 < 300:
            return pos
    return None


# ---------------------------------------------------------------------------
# Step 4: Compute all LOD offsets
# ---------------------------------------------------------------------------

def compute_lod_offsets(vc_list, ic_list, vbuf0_start, lod_count, file_size):
    """
    Compute vbuf_start and ibuf_start for each LOD.

    For non-last LODs:
      ibuf[N]_start = vbuf[N]_start + vc[N]*stride - 3
      vbuf[N+1]_start = vbuf[N]_start + vc[N]*stride + ic[N]*2         (N=0)
      vbuf[N+1]_start = vbuf[N]_start + vc[N]*stride + ic[N]*2 - 3    (N>=1)

    For last LOD:
      ibuf[last]_start = vbuf[last]_start + vc[last]*stride
      ic[last] = (file_size - ibuf[last]_start) / 2
    """
    vbuf_starts = [0] * lod_count
    ibuf_starts = [0] * lod_count

    vbuf_starts[0] = vbuf0_start

    for n in range(lod_count):
        vc = vc_list[n]
        is_last = (n == lod_count - 1)

        if is_last:
            ibuf_starts[n] = vbuf_starts[n] + vc * VERTEX_STRIDE
        else:
            ibuf_starts[n] = vbuf_starts[n] + vc * VERTEX_STRIDE - 3
            ic = ic_list[n]
            if n == 0:
                vbuf_starts[n + 1] = vbuf_starts[n] + vc * VERTEX_STRIDE + ic * 2
            else:
                vbuf_starts[n + 1] = vbuf_starts[n] + vc * VERTEX_STRIDE + ic * 2 - 3

    # ic for last LOD
    last = lod_count - 1
    ic_last_bytes = file_size - ibuf_starts[last]
    if ic_last_bytes > 0 and ic_last_bytes % 2 == 0:
        ic_list[last] = ic_last_bytes // 2
    else:
        ic_list[last] = 0

    return vbuf_starts, ibuf_starts


# ---------------------------------------------------------------------------
# Step 5: Parse material section
# ---------------------------------------------------------------------------

def parse_materials(data, attr_blocks_end):
    """
    Parse the material name list that starts right after the attribute blocks.

    Structure (confirmed from 4x3x1_G.fbx hex analysis):
      - 1 header byte (0x0F)
      - material string (terminated by 0x00 for long paths, or 0xFF+0x00 for short names)
      - 7-byte inter-material separator: 01 00 00 80 3F 00 <NN>
      - ... repeated for each material ...
      - section ends when next byte is a control byte (< 0x20, e.g. 0x03 = LOD count)

    Returns list of material name strings.
    """
    off = attr_blocks_end + 1
    if off >= len(data):
        return []

    # Skip leading nulls, then one section-header control byte (e.g. 0x0F)
    while off < len(data) and data[off] == 0:
        off += 1
    if off < len(data) and data[off] < 0x20:
        off += 1  # skip header byte

    materials = []
    while off < len(data) and len(materials) < 20:
        b = data[off]
        # Control byte = end of material section (LOD count or other marker)
        if b < 0x20:
            break

        # Read until 0x00 or 0xFF (both can terminate a material string)
        end = off
        while end < len(data) and data[end] != 0x00 and data[end] != 0xFF:
            end += 1

        if end == off or end - off > 300:
            break

        name = data[off:end].decode('ascii', errors='replace')
        if all(0x20 <= ord(c) < 0x7f for c in name):
            materials.append(name)

        # Advance past terminator
        if end < len(data) and data[end] == 0xFF:
            off = end + 2  # skip 0xFF + following 0x00
        else:
            off = end + 1  # skip 0x00

        # Skip 7-byte inter-material separator: 01 00 00 80 3F 00 NN
        if off < len(data) and data[off] == 0x01:
            off += 7

    return materials


def role_from_material_name(name):
    """Determine material role (0-5) from the material name."""
    low = name.lower()
    if 'panel' in low or 'paint' in low or 'color' in low:
        return 0
    if 'metal' in low or 'steel' in low or 'alumin' in low:
        return 1
    if 'pom' in low or 'decal' in low or 'dark' in low or 'black' in low:
        return 2
    if 'light' in low or 'lamp' in low:
        return 3
    if 'emit' in low or 'glow' in low or 'signal' in low:
        return 4
    if 'glass' in low or 'trans' in low or 'window' in low:
        return 5
    return -1  # unknown


# ---------------------------------------------------------------------------
# Step 6: Determine material group boundaries from index stream
# ---------------------------------------------------------------------------

def read_index_counts(blocks):
    """
    Read the per-material-group index counts from the first attribute block's extra bytes.

    In the HMD geometry record, after the vertex format, the data is:
      extra[0..3]:              vertexPosition (Int32)
      extra[4]:                 gc — number of material groups
      extra[5 .. 5+gc*4-1]:    indexCounts[gc] (each an Int32)
      extra[5+gc*4 .. +3]:     indexPosition (Int32)

    Returns (gc, index_counts) or (0, []) on failure.
    """
    if not blocks:
        return 0, []
    extra = blocks[0]['extra']
    gc = extra[4]
    if gc < 1 or gc > 10 or 5 + gc * 4 > len(extra):
        return 0, []
    counts = [struct.unpack_from('<I', extra, 5 + i * 4)[0] for i in range(gc)]
    return gc, counts


# ---------------------------------------------------------------------------
# Index buffer offset refinement
# ---------------------------------------------------------------------------

def _index_buffer_coherence(data, ibuf_start, ic, vc):
    """Score a candidate index-buffer start: (bad_count, coherence).

    A naive vertex-to-index-buffer offset formula (see compute_lod_offsets's
    "-3" constant) can land on a position where every index happens to be
    in-range [0, vc) -- passing a naive bad-index check -- while still being
    byte-misaligned and producing scrambled/non-local triangle connectivity
    (confirmed on Pathway_Puncher.fbx: the "-3" formula gives 0 bad indices
    but a garbled mesh; the true offset is 4 bytes later). Real triangle
    lists for hard-surface meshes with little/no vertex sharing are strongly
    *locally coherent* -- consecutive indices are usually close together or
    identical to nearby ones -- so use that as the real correctness signal,
    not just range-validity.
    """
    if ibuf_start < 0 or ibuf_start + ic * 2 > len(data):
        return ic, 0.0
    try:
        idxs = struct.unpack_from(f'>{ic}H', data, ibuf_start)
    except struct.error:
        return ic, 0.0
    bad = sum(1 for v in idxs if v >= vc)
    if bad > ic * 0.05:
        return bad, 0.0
    close = sum(1 for i in range(1, len(idxs)) if abs(idxs[i] - idxs[i - 1]) <= 4)
    return bad, close / max(1, len(idxs) - 1)


def refine_ibuf_start(data, naive_start, ic, vc, search=16):
    """Search a small window of byte offsets around a naive formula's result
    and pick whichever produces the most locally-coherent index stream. Falls
    back to the naive offset if nothing in the search window scores better."""
    best = naive_start
    best_bad, best_score = _index_buffer_coherence(data, naive_start, ic, vc)
    for delta in range(-search, search + 1):
        if delta == 0:
            continue
        candidate = naive_start + delta
        bad, score = _index_buffer_coherence(data, candidate, ic, vc)
        if bad <= ic * 0.05 and score > best_score:
            best_score = score
            best_bad = bad
            best = candidate
    return best


# ---------------------------------------------------------------------------
# Full parse pipeline
# ---------------------------------------------------------------------------

def _parse_one_block(data, off):
    """
    Parse one attribute block starting at off (must be an 0x0b byte).
    Returns (end_offset, block_dict) or (None, None) on failure.
    """
    if data[off] != ATTR_BLOCK_MARKER:
        return None, None
    start = off
    off += 1
    attr_count = data[off]
    off += 1
    if attr_count == 0 or attr_count > 10:
        return None, None
    attrs = []
    for _ in range(attr_count):
        if off >= len(data):
            return None, None
        name_len = data[off]
        off += 1
        if name_len == 0 or name_len > 32 or off + name_len + 1 > len(data):
            return None, None
        name = data[off:off + name_len].decode('ascii', errors='replace')
        off += name_len
        type_byte = data[off]
        off += 1
        attrs.append((name, type_byte))
    if off + EXTRA_BYTES_LEN > len(data):
        return None, None
    extra = data[off:off + EXTRA_BYTES_LEN]
    off += EXTRA_BYTES_LEN
    return off, {'start': start, 'end': off, 'extra': extra, 'attrs': attrs}


_LOD_DESC_PATTERN = bytes([0x00, 0x00, 0x00, 0x02, 0x04, 0x05])


def parse_hmd_g(data):
    """
    Parse an HMD file in G-style format.

    Returns:
        lods: list of {vc, ic, vbuf_start, ibuf_start} per LOD
        materials: list of material name strings
        blocks: parsed attribute blocks (for debugging)

    Returns (None, None, None) on parse failure.
    """
    if len(data) < 10:
        return None, None, None

    # Step 1: vc[0] from header
    vc0 = struct.unpack_from('<H', data, 2)[0] + 1

    # Step 2: Parse ALL attribute blocks (up to 10), allowing a 16-byte gap between them.
    # Do NOT use extra[4] to limit the count — extra[4] is unreliable (see session_resume.md).
    blocks_with_pos = []
    off = 6
    if off >= len(data) or data[off] != ATTR_BLOCK_MARKER:
        return None, None, None

    end, block = _parse_one_block(data, off)
    if block is None:
        return None, None, None
    blocks_with_pos.append(block)
    off = end

    for _ in range(9):  # up to 9 more blocks (10 total)
        found = False
        for gap in range(16):
            if off + gap < len(data) and data[off + gap] == ATTR_BLOCK_MARKER:
                end2, block2 = _parse_one_block(data, off + gap)
                if block2 is not None:
                    blocks_with_pos.append(block2)
                    off = end2
                    found = True
                    break
        if not found:
            break

    attr_blocks_end = blocks_with_pos[-1]['end'] - 1

    # Step 3: Find the true lod_count from the LOD descriptor section.
    # The descriptor starts with [lod_count][00 00 00][02 04 05].
    # The byte immediately before the 6-byte pattern is the true lod_count.
    pat_idx = data.find(_LOD_DESC_PATTERN, attr_blocks_end, attr_blocks_end + 500)
    if pat_idx < 1:
        return None, None, None
    lod_count = data[pat_idx - 1]
    if lod_count < 1 or lod_count > 8:
        return None, None, None

    # Extract vc list from header and extra[46-47] of each block.
    # blocks[n].extra[46-47] gives vc[n+1]; use only as many blocks as needed.
    vc_list = [0] * lod_count
    vc_list[0] = vc0
    for n in range(min(len(blocks_with_pos) - 1, lod_count - 1)):
        vc_next = struct.unpack_from('<H', blocks_with_pos[n]['extra'], 46)[0]
        if vc_next > 0:
            vc_list[n + 1] = vc_next

    # Extract ic list from extra[0] cumulative offsets.
    ic_list = [0] * lod_count
    extra0_list = [struct.unpack_from('<I', b['extra'], 0)[0] for b in blocks_with_pos]

    if lod_count >= 2 and len(blocks_with_pos) >= 2:
        ic_list[0] = (extra0_list[1] - (vc_list[0] - 1) * VERTEX_STRIDE) // 2
        for n in range(1, lod_count - 1):
            if n + 1 < len(extra0_list):
                ic_list[n] = (extra0_list[n + 1] - extra0_list[n] - vc_list[n] * VERTEX_STRIDE) // 2

    # Expected byte offset from vbuf0 to vbuf1 (G-style N=0 formula):
    #   vbuf1 = vbuf0 + vc0*stride + ic0*2
    if ic_list[0] <= 0 or vc_list[0] <= 0:
        return None, None, None
    vbuf1_offset = vc_list[0] * VERTEX_STRIDE + ic_list[0] * 2

    # Narrow the scan to right after the last LOD name's null terminator.
    # The last LOD descriptor name always ends with "LOD{lod_count-1}\x00".
    lod_suffix = f"LOD{lod_count - 1}".encode() + b"\x00"
    scan_from = attr_blocks_end + 1
    lo = scan_from
    while True:
        idx = data.find(lod_suffix, lo, lo + 2000)
        if idx == -1:
            break
        lo = idx + len(lod_suffix)
    # lo now points to the byte after the last LOD name null = start of last LOD meta
    meta_start = lo if lo > scan_from else scan_from

    vc2 = vc_list[lod_count - 1]
    vbuf0_start = find_vbuf0_start(data, meta_start, vc2, vbuf1_offset)
    if vbuf0_start is None:
        return None, None, None

    # Compute all LOD buffer offsets
    vbuf_starts, ibuf_starts = compute_lod_offsets(
        vc_list, ic_list, vbuf0_start, lod_count, len(data)
    )

    lods = []
    for n in range(lod_count):
        lods.append({
            'vc': vc_list[n],
            'ic': ic_list[n],
            'vbuf_start': vbuf_starts[n],
            'ibuf_start': ibuf_starts[n],
        })

    # Parse material names
    materials = parse_materials(data, attr_blocks_end)

    return lods, materials, blocks_with_pos


# ---------------------------------------------------------------------------
# CLI: print parsed info
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    import os
    for path in sys.argv[1:]:
        print(f"\n{'='*60}")
        print(f"File: {os.path.basename(path)}")
        with open(path, 'rb') as f:
            data = f.read()

        lods, materials, blocks = parse_hmd_g(data)
        if lods is None:
            print("  ERROR: Parse failed")
            continue

        print(f"  LOD count: {len(lods)}")
        for n, lod in enumerate(lods):
            print(f"  LOD{n}: vc={lod['vc']:5d}  ic={lod['ic']:5d}  "
                  f"vbuf={lod['vbuf_start']:7d}  ibuf={lod['ibuf_start']:7d}")

        print(f"  Materials ({len(materials)}):")
        for m in materials:
            role = role_from_material_name(m)
            print(f"    {m!r:60s}  role={role}")

        # Cross-check LOD0 by reading first few vertices
        lod0 = lods[0]
        print(f"\n  LOD0 vertex 0: ", end='')
        x, y, z = struct.unpack_from('<3f', data, lod0['vbuf_start'])
        print(f"({x:.4f}, {y:.4f}, {z:.4f})")
        print(f"  LOD0 vertex 1: ", end='')
        x, y, z = struct.unpack_from('<3f', data, lod0['vbuf_start'] + VERTEX_STRIDE)
        print(f"({x:.4f}, {y:.4f}, {z:.4f})")
        print(f"  LOD0 first 5 indices (BE u16): ", end='')
        for i in range(5):
            v = struct.unpack_from('>H', data, lod0['ibuf_start'] + i * 2)[0]
            print(v, end=' ')
        print()


if __name__ == '__main__':
    main()
