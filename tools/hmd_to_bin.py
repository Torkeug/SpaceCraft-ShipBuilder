"""
hmd_to_bin.py — Convert SpaceCraft HMD (.fbx) mesh files to .bin for the ship builder.

Usage:
    python hmd_to_bin.py <input.fbx> <output.bin>

The HMD format is Heaps Model Data stored with a .fbx extension inside res.pak.
The .bin format is documented in shipbuilder/js/meshLoader.js.

See tools/hmd_format_notes.md for full format documentation.
"""

import struct
import sys
import os
import re

# Allow importing hmd_parse from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


_MODEL_PROPS_CACHE = {}


def _true_object_names(hmd_path):
    """Ground-truth sub-object names for hmd_path's fbx stem, read from the
    model.props manifest in the same directory. model.props is the game's own
    asset-pipeline LOD config, keyed "<Stem>.fbx/<ObjectName>" — it lists every
    real object a compound fbx contains, independent of our own HMD parsing.
    Returns None if no props file exists or the stem isn't listed there.
    """
    props_path = os.path.join(os.path.dirname(hmd_path), 'model.props')
    if props_path not in _MODEL_PROPS_CACHE:
        table = {}
        if os.path.exists(props_path):
            text = open(props_path, encoding='utf-8', errors='replace').read()
            start = text.find('{')
            if start != -1:
                for fstem, obj in re.findall(r'"([^"]+)\.fbx/([^"]+)"\s*:\s*\{', text[start:]):
                    table.setdefault(fstem, []).append(obj)
        _MODEL_PROPS_CACHE[props_path] = table
    stem = os.path.splitext(os.path.basename(hmd_path))[0]
    return _MODEL_PROPS_CACHE[props_path].get(stem)


# ---------------------------------------------------------------------------
# Known file-specific parameters (discovered by binary analysis).
# These are used when auto-detection is not possible.
# ---------------------------------------------------------------------------

KNOWN_FILES = {
    # 4x3x1_A.fbx — fully confirmed
    '4x3x1_A.fbx': {
        'lod0_vert_start': 1039,
        'lod0_vert_count': 1280,
        'vert_stride': 32,
        'pos_offset': 0,           # float32 × 3 at stride offset 0
        'pos_format': 'float32',
        'index_start': 248884,
        'index_count': 3141,       # use 3141 (not 3143 — last 2 are garbage)
        'index_format': '>H',      # big-endian uint16
        'coord_scale': 1.0 / 100, # game units → grid units
        'coord_z_shift': -0.5,    # center Z: [0,100]→[-0.5,0.5] in grid
        # Material group boundaries — proportional estimate, NOT confirmed from file
        # Actual group data in LOD descriptor at A bytes [693..720]
        'groups': [
            {'role': 0, 'rgb': (94,  124, 162), 'index_count': 270},   # paint
            {'role': 1, 'rgb': (121, 130, 141), 'index_count': 1449},  # metal
            {'role': 2, 'rgb': (34,  38,  44),  'index_count': 1422},  # dark
        ],
    },

    # 4x3x1_G.fbx — fully confirmed
    '4x3x1_G.fbx': {
        'lod0_vert_start': 825,
        'lod0_vert_count': 1434,   # indices [0..3605]; one bad index (1535) clamped to 0
        'vert_stride': 32,
        'pos_offset': 0,           # float32 × 3 at stride offset 0
        'pos_format': 'float32',
        'index_start': 46710,
        'index_count': 3606,       # 1202 triangles (3606 BE uint16)
        'index_format': '>H',      # big-endian uint16
        'coord_scale': 1.0,        # already in grid units — NO scaling needed
        'coord_z_shift': 0.0,      # no Z shift (Z is near 0; piece is a flat panel)
        # 3 material groups (matching pattern of other 4x3x1 shapes, gc=3).
        # LOD meta sentinel value 2 = LOD_count-1 = 3-1 (NOT material group count).
        # Boundaries from vertex-range transitions in the index stream:
        #   paint  verts 0-255  → first 112 triangles (indices 0-335)
        #   metal  verts 0-767  → tris 112-592 (indices 336-1778)
        #   dark   verts 512+   → tris 593-1201 (indices 1779-3605)
        'groups': [
            {'role': 0, 'rgb': (94,  124, 162), 'index_count': 336},   # paint
            {'role': 1, 'rgb': (121, 130, 141), 'index_count': 1443},  # metal
            {'role': 2, 'rgb': (34,  38,  44),  'index_count': 1827},  # dark
        ],
    },
}


# ---------------------------------------------------------------------------
# .bin writer
# ---------------------------------------------------------------------------

def quantize(val, vmin, vmax):
    """Quantize a float to uint16 [0..65535] within [vmin, vmax]."""
    r = vmax - vmin
    if r == 0:
        return 0
    return max(0, min(65535, int(round((val - vmin) / r * 65535))))


def write_bin(out_path, verts, groups, indices, i32=False):
    """
    Write the .bin format:
      uint32 vc, uint32 ic, uint8 gc,
      6×float32 bbox,
      vc×3×uint16 quantized positions,
      gc×(role:1B + r:1B + g:1B + b:1B + start:4B + count:4B),
      ic×uint16 indices (LE)

    verts: list of (x, y, z) floats in grid units
    groups: list of {'role': int, 'rgb': (r,g,b), 'start': int, 'count': int}
    indices: list of ints
    """
    vc = len(verts)
    ic = len(indices)
    gc = len(groups)

    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    bbox = [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]

    idx_fmt = f'<{ic}I' if i32 else f'<{ic}H'

    with open(out_path, 'wb') as f:
        f.write(struct.pack('<IIB', vc, ic, gc))
        f.write(struct.pack('<6f', *bbox))
        for x, y, z in verts:
            f.write(struct.pack('<HHH',
                quantize(x, bbox[0], bbox[3]),
                quantize(y, bbox[1], bbox[4]),
                quantize(z, bbox[2], bbox[5]),
            ))
        for g in groups:
            r, gg, b = g['rgb']
            f.write(struct.pack('<BBBBII', g['role'], r, gg, b, g['start'], g['count']))
        f.write(struct.pack(idx_fmt, *indices))

    print(f"Wrote {vc} verts, {ic} indices, {gc} groups -> {out_path}")
    print(f"  bbox: {[round(v,4) for v in bbox]}")


# ---------------------------------------------------------------------------
# HMD reader — 4x3x1_A.fbx style (float32 positions, BE uint16 indices)
# ---------------------------------------------------------------------------

def read_verts_float32(data, start, count, stride, pos_offset):
    """Read `count` vertex positions from a stride-32 float32 buffer."""
    verts = []
    for i in range(count):
        off = start + i * stride + pos_offset
        x, y, z = struct.unpack_from('<3f', data, off)
        verts.append((x, y, z))
    return verts


def read_indices_be_u16(data, start, count):
    """Read `count` big-endian uint16 triangle indices."""
    return [struct.unpack_from('>H', data, start + i * 2)[0] for i in range(count)]


def convert_a_style(hmd_path, out_path, params):
    """Convert an A-style HMD file (float32 positions, game units, BE uint16 indices)."""
    with open(hmd_path, 'rb') as f:
        data = f.read()

    scale = params['coord_scale']
    z_shift = params['coord_z_shift']

    raw_verts = read_verts_float32(
        data,
        params['lod0_vert_start'],
        params['lod0_vert_count'],
        params['vert_stride'],
        params['pos_offset'],
    )

    # Apply coordinate transform: game units → grid units, center Z
    verts = [(x * scale, y * scale, z * scale + z_shift) for x, y, z in raw_verts]

    raw_indices = read_indices_be_u16(data, params['index_start'], params['index_count'])

    # Clamp any out-of-range indices to 0 (makes degenerate triangles, safe for rendering)
    vc = len(verts)
    bad = [i for i, v in enumerate(raw_indices) if v >= vc]
    if bad:
        print(f"  Warning: {len(bad)} out-of-range indices clamped to 0 (e.g. index {raw_indices[bad[0]]})")
        raw_indices = [v if v < vc else 0 for v in raw_indices]

    # Build groups with cumulative index offsets
    groups = []
    idx_start = 0
    for g in params['groups']:
        groups.append({
            'role': g['role'],
            'rgb': g['rgb'],
            'start': idx_start,
            'count': g['index_count'],
        })
        idx_start += g['index_count']

    assert idx_start == len(raw_indices), \
        f"Group index counts {idx_start} != total indices {len(raw_indices)}"

    write_bin(out_path, verts, groups, raw_indices)


# Role and default colour for each group slot (index = group position).
# When material names are available, role_from_material_name() overrides.
_DEFAULT_ROLES = [
    (0, (94,  124, 162)),  # slot 0 → paint
    (1, (121, 130, 141)),  # slot 1 → metal
    (2, (34,  38,  44)),   # slot 2 → dark
    (3, (220, 220, 200)),  # slot 3 → light
    (4, (255, 200,  50)),  # slot 4 → emissive
    (5, (80,  160, 200)),  # slot 5 → glass
]


def _detect_ring_buffer_hmd(raw):
    """
    Return the byte offset of HMD\x06 magic if raw is a ring-buffer file
    (header at end of file, body + geometry at start), otherwise return None.

    Validates that geom_start read from the header is plausibly before hmd_off
    to reject false-positive magic hits inside geometry data.
    """
    MAGIC = b'HMD\x06'
    off = raw.find(MAGIC)
    if off > 0 and off >= len(raw) // 2 and off + 8 <= len(raw):
        geom_start = struct.unpack_from('<I', raw, off + 4)[0]
        if 0 < geom_start < off:
            return off
    # Split-magic: HMD\x06 straddles the end/start boundary
    for split in range(1, 4):
        if raw[-split:] == MAGIC[:split] and raw[:4 - split] == MAGIC[split:]:
            return len(raw) - split
    return None


def _find_hmd_data(data):
    """
    Locate and return the HMD data slice from raw file bytes.

    Variants handled:
    1. Standard (4x3x1): HMD\x06 at byte 0 — return data as-is.
    2. Text-prefix (4x3x2–8x6x2): FBX ASCII text precedes HMD\x06 in the first
       half of the file — slice from the magic offset. geom_start is relative to
       the HMD header so it remains correct after slicing.
    3. Ring-buffer (12x6x2+): HMD\x06 is in the SECOND half of the file. The body
       (attr blocks + geometry) precedes the header. Rotation is attempted but
       parse_prod_hmd will likely fail because the first attr block is incomplete
       in the trailer (it wraps around byte 0). This case still needs a wrap-aware
       attr-block parser — currently fails cleanly and returns None from convert().
    4. Split-magic (16x6x2): the 4-byte HMD\x06 magic straddles the end/start
       boundary. Rotation attempted — same caveat as case 3.

    Returns the bytes slice to parse, or None if no HMD\x06 could be found.
    """
    MAGIC = b'HMD\x06'

    # Fast path: standard format
    if data[:4] == MAGIC:
        return data

    # Search for magic anywhere in the file
    off = data.find(MAGIC)
    if off > 0:
        # Text-prefix variant (first half of file): strip the prefix
        if off < len(data) // 2:
            return data[off:]
        # Ring-buffer variant (second half of file): rotate so header is first
        return data[off:] + data[:off]

    # Edge case: magic split across end/start boundary (e.g. 16x6x2)
    for split in range(1, 4):
        if data[-split:] == MAGIC[:split] and data[:4 - split] == MAGIC[split:]:
            return data[-split:] + data[:-split]

    return None


def _count_bad_indices(data, ibuf_start, ic, vc):
    """Count how many of the ic uint16 LE indices at ibuf_start are >= vc.
    Returns None if the buffer doesn't fit in data at all."""
    if ibuf_start < 0 or ibuf_start + ic * 2 > len(data):
        return None
    vals = struct.unpack_from(f'<{ic}H', data, ibuf_start)
    return sum(1 for v in vals if v >= vc)


def _vertex_sanity_score(data, vbuf_start, vc, stride, target_bbox=None, sample=200):
    """
    Sample up to `sample` evenly-spaced vertices starting at vbuf_start and score
    how plausible they look as real mesh-position data.

    When `target_bbox` (the file's own stored [minX,minY,minZ,maxX,maxY,maxZ], read
    directly from the LOD0 extra section) is given, the score is how closely the
    sampled min/max per axis matches it — this is far more discriminating than a
    generic "small numbers" heuristic, because misreading the wrong attribute
    column (normal/tangent/uv are all small floats too) can otherwise look just as
    plausible as the real position data.

    Returns (nan_fraction, badness) — lower is better on both.
    """
    if vbuf_start < 0 or vbuf_start + vc * stride > len(data):
        return (1.0, float('inf'))
    step = max(1, vc // sample)
    nan_count = checked = 0
    mins = [float('inf')] * 3
    maxs = [float('-inf')] * 3
    for i in range(0, vc, step):
        off = vbuf_start + i * stride
        if off + 6 > len(data):
            break
        pt = struct.unpack_from('<3e', data, off)
        checked += 1
        bad = False
        for v in pt:
            if v != v or v in (float('inf'), float('-inf')):  # NaN/Inf check
                bad = True
        if bad:
            nan_count += 1
            continue
        for axis, v in enumerate(pt):
            mins[axis] = min(mins[axis], v)
            maxs[axis] = max(maxs[axis], v)
    if checked == 0:
        return (1.0, float('inf'))
    nan_frac = nan_count / checked
    if target_bbox is None:
        max_abs = max((abs(v) for v in mins + maxs if v not in (float('inf'), float('-inf'))), default=float('inf'))
        return (nan_frac, max_abs)
    badness = 0.0
    for axis in range(3):
        if mins[axis] == float('inf'):
            return (nan_frac, float('inf'))
        badness += abs(mins[axis] - target_bbox[axis]) + abs(maxs[axis] - target_bbox[axis + 3])
    return (nan_frac, badness)


def _find_ibuf_start(data, vc, ic, stride=None, bbox=None):
    """
    Scan data for the LOD0 index buffer: ic consecutive uint16 LE values all < vc.
    Returns the absolute byte offset of the index buffer within data, or None.

    Used to correct ring-buffer files where geom_start in the HMD trailer is wrong
    (observed misalignments range from a few bytes up to ~2KB depending on asset
    category), causing vbuf_start / ibuf_start to be misaligned. Checks both byte
    parities — some module/prop assets have been observed with an odd-byte-aligned
    vertex/index buffer start, unlike hull pieces which are always even-aligned.

    When `stride` is given, candidates are additionally ranked by vertex-sanity
    (real mesh positions are small and finite) because a purely index-based match
    can hit a coincidental run of small values that doesn't correspond to the real
    buffer (observed on some Tools/-category files with large vertex counts).
    """
    try:
        import numpy as np
    except ImportError:
        return _find_ibuf_start_slow(data, vc, ic)

    if len(data) < ic * 2:
        return None

    # Gather the lowest-bad-count candidates from both byte parities.
    candidates = []  # (bad_count, byte_offset)
    for parity in (0, 1):
        sub = data[parity:]
        n = len(sub) // 2
        if n < ic:
            continue
        arr = np.frombuffer(sub[:n * 2], dtype='<u2')
        valid = (arr < vc).astype(np.int64)
        csum = np.concatenate(([0], np.cumsum(valid)))
        window_sum = csum[ic:] - csum[:-ic]
        bad_arr = ic - window_sum
        k = min(25, len(bad_arr))
        top_idx = np.argpartition(bad_arr, k - 1)[:k]
        for idx in top_idx:
            candidates.append((int(bad_arr[idx]), int(idx) * 2 + parity))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    min_bad = candidates[0][0]
    if min_bad / ic >= 0.05:
        return None

    if stride is None:
        return candidates[0][1]

    # Among candidates within a small margin of the best index-match, prefer the
    # one whose implied vertex buffer looks like real geometry.
    margin = max(min_bad, 1) * 3
    close = [c for c in candidates if c[0] - min_bad <= margin]
    scored = []
    for bad, byte_off in close:
        vbuf_start = byte_off - vc * stride
        nan_frac, badness = _vertex_sanity_score(data, vbuf_start, vc, stride, target_bbox=bbox)
        scored.append((nan_frac, badness, bad, byte_off))
    scored.sort(key=lambda s: (s[0], s[1]))
    return scored[0][3]


def _find_ibuf_start_slow(data, vc, ic):
    """Pure-Python fallback for _find_ibuf_start when numpy is unavailable."""
    results = []
    for start in range(0, len(data) - ic * 2 + 1, 1):
        ok = True
        for check in range(min(ic, 8)):
            if struct.unpack_from('<H', data, start + check * 2)[0] >= vc:
                ok = False
                break
        if not ok:
            continue
        try:
            if all(v < vc for v in struct.unpack_from(f'<{ic}H', data, start)):
                results.append(start)
                if len(results) > 2:
                    return None
        except Exception:
            pass
    return results[0] if len(results) == 1 else None


def _read_object_geometry(raw, data, lod0, blocks, geom_start, mat_skip=0):
    """Read one object's verts/indices/groups from a single LOD0-style entry.
    Returns (verts, groups, raw_indices, idx_size)."""
    from hmd_parse_prod import (read_verts_f16, read_indices_le_u16, read_indices_le_u32,
                                 parse_material_groups)

    idx_size = lod0.get('idx_size', 2)

    # For ring-buffer files, geom_start in the HMD header can be wrong by 16–62 bytes.
    # Locate the true index buffer by scanning for ic consecutive uint16s all < vc,
    # then back-compute the true vbuf_start.  Skip for uint32 files (all uint16 values
    # pass the vc check when vc > 65535, causing false positives).
    if idx_size == 2:
        # Only search for a corrected offset if the position implied by geom_start
        # is actually broken. Checking this first avoids the search's occasional
        # false-positive matches (a coincidental low-bad-count run elsewhere in the
        # file) overriding an already-correct position.
        orig_bad = _count_bad_indices(data, lod0['ibuf_start'], lod0['ic'], lod0['vc'])
        if orig_bad is None or orig_bad / lod0['ic'] > 0.01:
            ibuf_found = _find_ibuf_start(data, lod0['vc'], lod0['ic'], lod0['stride'], lod0['bbox'])
            if ibuf_found is not None:
                true_vbuf = ibuf_found - lod0['vc'] * lod0['stride']
                if true_vbuf >= 0 and true_vbuf != lod0['vbuf_start']:
                    print(f"  Correcting vbuf_start {lod0['vbuf_start']} -> {true_vbuf} "
                          f"(ibuf at {ibuf_found}, delta={true_vbuf - lod0['vbuf_start']})")
                    lod0 = dict(lod0)
                    lod0['vbuf_start'] = true_vbuf
                    lod0['ibuf_start'] = ibuf_found

    verts = read_verts_f16(data, lod0['vbuf_start'], lod0['vc'], lod0['stride'])
    if idx_size == 4:
        raw_indices = read_indices_le_u32(data, lod0['ibuf_start'], lod0['ic'])
    else:
        raw_indices = read_indices_le_u16(data, lod0['ibuf_start'], lod0['ic'])

    vc = len(verts)
    bad = [i for i, v in enumerate(raw_indices) if v >= vc]
    if bad:
        print(f"  Warning: {len(bad)} out-of-range indices clamped to 0")
        raw_indices = [v if v < vc else 0 for v in raw_indices]

    gc, ic_per_group, mat_roles = parse_material_groups(data, blocks, geom_start, lod0.get('extra_off'), mat_skip)

    if sum(ic_per_group) == len(raw_indices) and gc > 0:
        groups = []
        start = 0
        for count, role in zip(ic_per_group, mat_roles):
            rgb = _DEFAULT_ROLES[role][1] if role < len(_DEFAULT_ROLES) else (128, 128, 128)
            groups.append({'role': role, 'rgb': rgb, 'start': start, 'count': count})
            start += count
    else:
        groups = [{'role': 1, 'rgb': (121, 130, 141), 'start': 0, 'count': len(raw_indices)}]

    return verts, groups, raw_indices, idx_size, gc


def _finish_prod_conversion(raw, data, lod0, blocks, geom_start, out_path, label='Production'):
    """Read one object's geometry and write it directly to .bin."""
    verts, groups, raw_indices, idx_size, _gc = _read_object_geometry(raw, data, lod0, blocks, geom_start)
    i32 = (idx_size == 4)
    print(f"  {label} HMD: vc={len(verts)}, ic={len(raw_indices)}, gc={len(groups)}, "
          f"stride={lod0['stride']}, idx_size={idx_size}")
    write_bin(out_path, verts, groups, raw_indices, i32=i32)


def _finish_prod_conversion_merged(raw, data, object_lods, blocks, geom_start, out_path, label='Production'):
    """Read multiple sub-objects (each a LOD0-style entry) and merge them into one .bin.

    Used for compound multi-object files (see parse_prod_hmd) where the real visual
    is an assembly of several parts, not just the first declared "LOD0"."""
    all_verts = []
    all_groups = []
    all_indices = []
    idx_size = 2
    vertex_offset = 0
    index_offset = 0
    # NOTE: passing an accumulating mat_skip here (so each sub-object claims the next
    # slice of the shared keyword-match list instead of always starting at 0) was tried
    # and made things worse (emissive jumped from 10% to 48% of the mesh) — the shared
    # material section has only as many *unique* keyword hits as distinct materials
    # (18 on MiningTool1_OC), reused across far more group slots (30), so a flat
    # sequential-slice model runs out of real matches partway through and starts
    # assigning tail-of-list (often emissive/signage) keywords to unrelated later
    # sub-objects. The true group-to-material mapping needs an actual index/reference
    # array we haven't located yet — see hmd_format_notes.md "material attribution"
    # notes. Left at 0 (each sub-object restarts from the first keyword) as the
    # least-bad default until that's found.
    for lod0 in object_lods:
        verts, groups, raw_indices, this_idx_size, gc = _read_object_geometry(
            raw, data, lod0, blocks, geom_start, 0)
        idx_size = max(idx_size, this_idx_size)
        all_verts.extend(verts)
        all_indices.extend(v + vertex_offset for v in raw_indices)
        for g in groups:
            all_groups.append({**g, 'start': g['start'] + index_offset})
        vertex_offset += len(verts)
        index_offset += len(raw_indices)

    i32 = (idx_size == 4) or vertex_offset > 65535
    print(f"  {label} HMD (merged {len(object_lods)} sub-objects): "
          f"vc={vertex_offset}, ic={len(all_indices)}, gc={len(all_groups)}")
    write_bin(out_path, all_verts, all_groups, all_indices, i32=i32)


def _detect_prefix_ring_buffer(raw):
    """
    Detect ring-buffer files whose geom_start cannot be read from a binary header —
    either because the header is absent (replaced by JSON) or because the trailer is
    too short for the geom_start bytes to be read without wrapping into the body.

    These files all start with raw[0..2]=<version byte ≤20, 0, 0> and raw[3]=0x0B
    (the continuation of the LOD0 attr block that wraps from the end of the file).
    geom_start is inferred from the known sentinel offset.

    Returns an inferred geom_start value, or None if the pattern doesn't match.
    """
    if len(raw) < 20:
        return None
    if not (1 <= raw[0] <= 20 and raw[1] == 0x00 and raw[2] == 0x00 and raw[3] == 0x0B):
        return None

    sentinel = bytes([0x00, 0x00, 0x00, 0x02, 0x04, 0x05])
    sent_off = raw.find(sentinel)
    if sent_off <= 0:
        return None

    # Try geom_start candidates: sentinel+326 (observed constant for 16x6x2 files),
    # then common fixed values.
    for gs_try in [sent_off + 326, 714, 768]:
        if 0 < gs_try < len(raw) // 2:
            return gs_try
    return None


def _detect_body_start_ring_buffer(raw):
    """
    Detect ring-buffer files where the body (attr defs + geometry) begins at raw[0]
    and the file end contains non-binary content (JSON) rather than a binary HMD header.

    These start with attr-name bytes (e.g. 'n' for "normal"), have the LOD descriptor
    sentinel within the first ~500 bytes, and contain no HMD\x06 magic.

    Returns an inferred geom_start, or None if the pattern doesn't match.
    """
    if len(raw) < 20 or raw.find(b'HMD\x06') >= 0:
        return None
    sentinel = bytes([0x00, 0x00, 0x00, 0x02, 0x04, 0x05])
    sent_off = raw.find(sentinel)
    if not (0 < sent_off < 500):
        return None
    for gs_try in [sent_off + 326, 714, 768]:
        if 0 < gs_try < len(raw) // 2:
            return gs_try
    return None


def convert_prod_style(hmd_path, out_path):
    """Convert a production HMD file (version 0x06). Returns True on success."""
    from hmd_parse_prod import (parse_prod_hmd, parse_ring_buffer_hmd,
                                 _parse_attr_blocks)

    with open(hmd_path, 'rb') as f:
        raw = f.read()

    # --- Ring-buffer path (12x6x2+): HMD header at end of file ---
    hmd_off = _detect_ring_buffer_hmd(raw)
    if hmd_off is not None:
        lods, blocks, geom_start = parse_ring_buffer_hmd(raw, hmd_off)
        if lods is not None:
            _finish_prod_conversion(raw, raw, lods[0], blocks, geom_start,
                                    out_path, label='Ring-buffer HMD')
            return True
        # Fall through if ring-buffer parse fails

    # --- Prefix ring-buffer path (version-byte prefix, no full HMD header) ---
    gs_inferred = _detect_prefix_ring_buffer(raw)
    if gs_inferred is not None:
        lods, blocks, geom_start = parse_ring_buffer_hmd(raw, None, gs_inferred)
        if lods is not None:
            _finish_prod_conversion(raw, raw, lods[0], blocks, geom_start,
                                    out_path, label='Prefix ring-buffer HMD')
            return True

    # --- Body-start ring-buffer path (attr-name bytes at start, no binary header) ---
    gs_inferred = _detect_body_start_ring_buffer(raw)
    if gs_inferred is not None:
        lods, blocks, geom_start = parse_ring_buffer_hmd(raw, None, gs_inferred)
        if lods is not None:
            lod0 = lods[0]
            if lod0['ibuf_start'] + lod0['ic'] * 2 <= len(raw):
                _finish_prod_conversion(raw, raw, lod0, blocks, geom_start,
                                        out_path, label='Body-start ring-buffer HMD')
                return True

    # --- Standard path: HMD header at start (or after text prefix) ---
    data = _find_hmd_data(raw)
    if data is None:
        return False

    lods = parse_prod_hmd(data)
    if lods is None:
        return False

    lod0 = lods[0]
    # Reject if geometry buffer overflows (catches false-positive HMD\x06 hits)
    if lod0['ibuf_start'] + lod0['ic'] * 2 > len(data):
        return False

    blocks = _parse_attr_blocks(data)

    # Compound/prop assets can pack multiple independent sub-objects into the LOD
    # slots (see parse_prod_hmd docstring) — each marked is_object_start. If there's
    # more than one, the real visual is their assembly, not just the first one.
    object_starts = [l for l in lods if l.get('is_object_start') and
                      l['ibuf_start'] + l['ic'] * (l.get('idx_size', 2)) <= len(data)]

    # When no embedded LOD names were found, is_object_start falls back to an
    # unreliable vc-increase heuristic that can false-positive on a single
    # object's own LOD chain. model.props (the game's own asset manifest) lists
    # the true object count per fbx — trust it over the heuristic when they
    # disagree and it says there's only one real object.
    true_names = _true_object_names(hmd_path)
    if true_names is not None and len(true_names) == 1 and len(object_starts) > 1:
        object_starts = object_starts[:1]

    if len(object_starts) > 1:
        _finish_prod_conversion_merged(raw, data, object_starts, blocks, None, out_path)
    else:
        _finish_prod_conversion(raw, data, lod0, blocks, None, out_path)
    return True


def convert_g_style_auto(hmd_path, out_path):
    """Try to parse and convert an HMD file as G-style. Returns True on success."""
    from hmd_parse import parse_hmd_g, read_index_counts, role_from_material_name, refine_ibuf_start

    with open(hmd_path, 'rb') as f:
        data = f.read()

    lods, materials, blocks = parse_hmd_g(data)
    if lods is None:
        return False

    lod0 = lods[0]
    verts = read_verts_float32(data, lod0['vbuf_start'], lod0['vc'], 32, 0)
    # The vertex-to-index-buffer offset formula can be byte-misaligned in a way
    # that still produces all-in-range indices (passes a naive bad-index check)
    # but scrambles triangle connectivity -- refine using local coherence too
    # (confirmed necessary on Pathway_Puncher.fbx).
    ibuf_start = refine_ibuf_start(data, lod0['ibuf_start'], lod0['ic'], lod0['vc'])
    raw_indices = read_indices_be_u16(data, ibuf_start, lod0['ic'])

    vc = len(verts)
    bad = [i for i, v in enumerate(raw_indices) if v >= vc]
    if bad:
        print(f"  Warning: {len(bad)} out-of-range indices clamped to 0")
        raw_indices = [v if v < vc else 0 for v in raw_indices]

    # Read per-group index counts directly from the HMD attribute block extra bytes.
    gc, idx_counts = read_index_counts(blocks)
    if not gc or sum(idx_counts) != len(raw_indices):
        # Fallback: single group covering all indices
        gc, idx_counts = 1, [len(raw_indices)]

    # Assign roles from material names when available, otherwise use defaults by slot.
    roles = []
    for i in range(gc):
        mat_name = materials[i] if i < len(materials) else ''
        r = role_from_material_name(mat_name)
        if r < 0:
            r = _DEFAULT_ROLES[i][0] if i < len(_DEFAULT_ROLES) else 0
        rgb = _DEFAULT_ROLES[i][1] if i < len(_DEFAULT_ROLES) else (128, 128, 128)
        roles.append((r, rgb))

    groups = []
    start = 0
    for i, count in enumerate(idx_counts):
        if count > 0:
            role, rgb = roles[i]
            groups.append({'role': role, 'rgb': rgb, 'start': start, 'count': count})
        start += count

    if materials:
        print(f"  Materials: {', '.join(materials)}")

    write_bin(out_path, verts, groups, raw_indices)
    return True


# ---------------------------------------------------------------------------
# Auto-detect or lookup conversion parameters
# ---------------------------------------------------------------------------

def convert(hmd_path, out_path):
    """Detect file type and convert to .bin format."""
    basename = os.path.basename(hmd_path)

    # Try production format first (version 0x06, disc=0x02 files)
    if convert_prod_style(hmd_path, out_path):
        return

    # Try G-style auto-parser (disc=0x00 TestPE files)
    if convert_g_style_auto(hmd_path, out_path):
        return

    # Fall back to KNOWN_FILES for A-style and other confirmed entries
    params = KNOWN_FILES.get(basename)
    if params is None:
        msg = (f"Auto-detection failed and no known parameters for {basename!r}. "
               "See tools/hmd_format_notes.md for format documentation.")
        if __name__ == '__main__':
            print(f"ERROR: {msg}")
            sys.exit(1)
        raise RuntimeError(msg)

    if 'TODO' in params:
        msg = f"{basename} conversion not yet implemented: {params['TODO']}"
        if __name__ == '__main__':
            print(f"ERROR: {msg}")
            sys.exit(1)
        raise RuntimeError(msg)

    if params.get('pos_format') == 'float32':
        convert_a_style(hmd_path, out_path, params)
    else:
        print(f"ERROR: Unknown pos_format {params.get('pos_format')!r}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(__doc__)
        print("\nKnown files:")
        for name, p in KNOWN_FILES.items():
            status = 'TODO: ' + p['TODO'] if 'TODO' in p else 'OK'
            print(f"  {name}: {status}")
        sys.exit(1)

    hmd_path = sys.argv[1]
    out_path = sys.argv[2]

    if not os.path.exists(hmd_path):
        print(f"ERROR: File not found: {hmd_path}")
        sys.exit(1)

    convert(hmd_path, out_path)
