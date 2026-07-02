"""
hmd_parse_prod.py — Parser for production HMD files (version 0x06, disc=0x02).

These are the actual in-game assets from assets/Vehicules/Buildings_Parts/.
Format documented in tools/hmd_format_notes.md under "Production HMD Format".

Usage:
    from hmd_parse_prod import parse_prod_hmd
    lods = parse_prod_hmd(data)
    # lods[0] = {'vbuf_start': int, 'vc': int, 'stride': int,
    #            'ibuf_start': int, 'ic': int, 'bbox': [6 floats]}
"""

import struct


# Attribute byte size by type (production HMD version 0x06)
_ATTR_SIZE = {
    0x13: 8,   # float16×3 padded to 8 bytes
    0x12: 4,   # float16×2 (uv)
}


def _u32(data, off):
    return struct.unpack_from('<I', data, off)[0]


def _find_first_body_block(body):
    """
    Scan body bytes for the first valid, complete attr block (0x0B marker).
    Used by the ring-buffer parser to locate LOD1's block so LOD0's extra
    section can be found by backtracking.

    Returns (offset, stride) or (None, 0).
    """
    for off in range(len(body) - 5):
        if body[off] != 0x0B:
            continue
        attr_count = body[off + 1]
        if attr_count < 2 or attr_count > 6:
            continue
        pos = off + 2
        stride = 0
        valid = True
        for _ in range(attr_count):
            if pos >= len(body) - 1:
                valid = False; break
            name_len = body[pos]; pos += 1
            if name_len < 1 or name_len > 64 or pos + name_len >= len(body):
                valid = False; break
            pos += name_len
            atype = body[pos]; pos += 1
            sz = _ATTR_SIZE.get(atype, 0)
            if sz == 0:
                valid = False; break
            stride += sz
        if valid and stride in (24, 28):
            return off, stride
    return None, 0


def parse_ring_buffer_hmd(raw, hmd_off, geom_start_override=None):
    """
    Parse a ring-buffer HMD file where the HMD\x06 header sits near the END
    of the file and the body (attr blocks + geometry) precedes it.

    raw:                complete raw file bytes
    hmd_off:            byte offset of the HMD\x06 magic within raw, or None
                        when geom_start_override is supplied (headerless variant)
    geom_start_override: if provided, use this value instead of reading geom_start
                        from the header (for files with no parseable HMD header)

    Returns (lods, blocks, geom_start) with the same structure as
    parse_prod_hmd / _parse_attr_blocks, or (None, None, None) on failure.
    """
    file_size = len(raw)

    if geom_start_override is not None:
        geom_start = geom_start_override
        trailer_len = 0  # body starts at raw[0]; LOD0 block is directly there
    else:
        trailer_len = file_size - hmd_off

        def ring_u32(header_off):
            b = bytes(raw[(hmd_off + header_off + j) % file_size] for j in range(4))
            return struct.unpack('<I', b)[0]

        geom_start = ring_u32(4)

    if geom_start <= 0 or geom_start >= file_size:
        return None, None, None

    body = raw[:geom_start]

    lod0_extra_start = None
    lod0_gc = None
    stride = 0

    # Approach A: find first complete attr block in body, then decide if it's
    # LOD0 directly (small trailer) or LOD1+ (large trailer, backtrack for LOD0).
    first_block_off, found_stride = _find_first_body_block(body)
    if first_block_off is not None:
        stride = found_stride
        first_ring_pos = first_block_off + trailer_len
        if first_ring_pos >= 30:
            # Large trailer: first block is LOD1. Backtrack to find LOD0 extra.
            for gc_candidate in (5, 6):
                extra_len = 38 + gc_candidate * 4
                es = first_block_off - extra_len
                if es < 0:
                    continue
                if body[es + 4] != gc_candidate:
                    continue
                # LOD0 always has vp=0 (starts at beginning of the geometry section)
                if struct.unpack_from('<I', body, es)[0] == 0:
                    lod0_extra_start = es
                    lod0_gc = gc_candidate
                    break
        else:
            # Small trailer: first block IS LOD0. Parse its attrs to find extra.
            attr_count = body[first_block_off + 1]
            pos = first_block_off + 2
            ok = True
            for _ in range(attr_count):
                if pos >= len(body): ok = False; break
                name_len = body[pos]; pos += 1
                if pos + name_len >= len(body): ok = False; break
                pos += name_len
                if pos >= len(body): ok = False; break
                pos += 1  # type byte
            if ok:
                attrs_end = pos
                for gc_candidate in (5, 6):
                    if attrs_end + 4 < len(body) and body[attrs_end + 4] == gc_candidate:
                        lod0_extra_start = attrs_end
                        lod0_gc = gc_candidate
                        break

    if lod0_extra_start is None or stride == 0:
        return None, None, None

    gc = lod0_gc
    vp = struct.unpack_from('<I', raw, lod0_extra_start)[0]
    ic_per_group = list(struct.unpack_from(f'<{gc}I', raw, lod0_extra_start + 5))
    vbuf_size_val = struct.unpack_from('<I', raw, lod0_extra_start + 5 + gc * 4)[0]
    bbox = list(struct.unpack_from('<6f', raw, lod0_extra_start + 5 + gc * 4 + 4))

    vc = vbuf_size_val // stride
    if vc == 0:
        return None, None, None

    vbuf_start = geom_start + vp
    ic = sum(ic_per_group)
    ibuf_start = vbuf_start + vc * stride

    lod = {
        'vbuf_start': vbuf_start,
        'vc': vc,
        'stride': stride,
        'ibuf_start': ibuf_start,
        'ic': ic,
        'bbox': bbox,
    }

    # Build blocks list for parse_material_groups.
    # LOD0 block (located by backtracking above).
    lod0_extra_len = 38 + gc * 4
    all_blocks = [{'extra_off': lod0_extra_start, 'extra_len': lod0_extra_len, 'stride': stride}]

    # Walk remaining complete LOD attr blocks in body (LOD1, LOD2, ...).
    pos = lod0_extra_start + lod0_extra_len
    while pos < len(body) and body[pos] == 0x0B:
        attr_count = body[pos + 1]
        p = pos + 2
        blk_stride = 0; ok = True
        for _ in range(attr_count):
            if p >= len(body): ok = False; break
            name_len = body[p]; p += 1
            if p + name_len >= len(body): ok = False; break
            p += name_len
            atype = body[p]; p += 1
            blk_stride += _ATTR_SIZE.get(atype, 0)
        if not ok or blk_stride == 0:
            break
        attrs_end = p
        next_ob = body.find(bytes([0x0B]), attrs_end + 1)
        if next_ob < 0 or next_ob - attrs_end > 120:
            all_blocks.append({'extra_off': attrs_end,
                                'extra_len': len(body) - attrs_end,
                                'stride': blk_stride})
            break
        all_blocks.append({'extra_off': attrs_end,
                            'extra_len': next_ob - attrs_end,
                            'stride': blk_stride})
        pos = next_ob

    return [lod], all_blocks, geom_start


def _parse_attr_blocks(data):
    """
    Parse all LOD attribute blocks starting at byte 19.
    Each block: 0x0B marker + count + N×(name_len + name + type) + extra_section.
    Extra section ends just before the next 0x0B marker (or at material section start).

    Returns list of dicts: {'stride': int, 'extra_off': int, 'extra_len': int}
    """
    blocks = []
    off = 19
    while off < len(data) - 5:
        if data[off] != 0x0B:
            break
        block_start = off
        attr_count = data[off + 1]
        off += 2
        stride = 0
        for _ in range(attr_count):
            name_len = data[off]; off += 1
            off += name_len
            atype = data[off]; off += 1
            stride += _ATTR_SIZE.get(atype, 0)
        attrs_end = off

        # Find next 0x0B to determine extra section length.
        # Skip past the known minimum extra (based on gc at extra[4]) to avoid
        # spurious 0x0B bytes inside ic_per_group data (e.g. 2856 = 28 0B 00 00).
        gc_peek = data[attrs_end + 4] if attrs_end + 5 < len(data) else 0
        min_skip = (38 + gc_peek * 4) if 1 <= gc_peek <= 16 else 1
        next_ob = data.find(bytes([0x0B]), attrs_end + min_skip)
        # Sanity: next block should be within 200 bytes (engines have gc=10-12, extra up to ~90 bytes)
        if next_ob < 0 or next_ob - attrs_end > 200:
            # Last block — extra runs to end of attr section (find sentinel instead)
            sentinel = bytes([0x00, 0x00, 0x00, 0x02, 0x04, 0x05])
            sent_idx = data.find(sentinel, attrs_end)
            extra_len = max(0, sent_idx - attrs_end) if sent_idx > attrs_end else 0
            blocks.append({'stride': stride, 'extra_off': attrs_end, 'extra_len': extra_len})
            break
        extra_len = next_ob - attrs_end
        blocks.append({'stride': stride, 'extra_off': attrs_end, 'extra_len': extra_len})
        off = next_ob

    return blocks


def parse_prod_hmd(data):
    """
    Parse a production HMD file (version 0x06).

    Returns list of LOD dicts:
        {
          'vbuf_start': absolute byte offset of vertex buffer,
          'vc':         vertex count,
          'stride':     bytes per vertex,
          'ibuf_start': absolute byte offset of index buffer,
          'ic':         index count (uint16 LE indices),
          'bbox':       [minX, minY, minZ, maxX, maxY, maxZ],
        }
    Returns None if the file is not a valid production HMD.
    """
    if len(data) < 20:
        return None
    if data[0:3] != b'HMD':
        return None
    if data[3] != 0x06:
        return None

    geom_start = _u32(data, 4)
    vc_lod0    = _u32(data, 15)

    if data[19] != 0x0B:
        return None

    # Parse all LOD attribute blocks (variable extra section size)
    blocks = _parse_attr_blocks(data)
    if not blocks:
        return None

    stride = blocks[0]['stride']
    if stride == 0:
        return None

    # Find lod_count from LOD descriptor sentinel
    sentinel = bytes([0x00, 0x00, 0x00, 0x02, 0x04, 0x05])
    idx = data.find(sentinel)
    if idx < 1:
        return None
    lod_count = data[idx - 1]

    # Collect vp and bbox for each LOD from parsed block extras
    lod_extras = []
    for lod in range(min(lod_count, len(blocks))):
        blk = blocks[lod]
        extra_off = blk['extra_off']
        if extra_off + 29 > len(data):
            break
        vp   = _u32(data, extra_off)
        # bbox at extra[5 + gc*4 + 4 .. +24] (6 float32); offset 29 only correct for gc=5
        gc_here = data[extra_off + 4] if extra_off + 4 < len(data) else 5
        bbox_off = extra_off + 5 + gc_here * 4 + 4
        if bbox_off + 24 > len(data):
            bbox_off = extra_off + 29  # fallback
        bbox = list(struct.unpack_from('<6f', data, bbox_off))
        lod_extras.append({'vp': vp, 'bbox': bbox})

    if not lod_extras:
        return None

    # Sort LODs by vp to get section boundaries
    lod_extras_sorted = sorted(lod_extras, key=lambda e: e['vp'])
    vps_sorted = [e['vp'] for e in lod_extras_sorted]
    # End sentinel: total geometry bytes
    total_geom = len(data) - geom_start
    vps_sorted.append(total_geom)

    # Meshes with vc > 65535 cannot use uint16 indices; they use uint32 (4 bytes/index).
    idx_size = 4 if vc_lod0 > 65535 else 2

    lods = []
    for i, ex in enumerate(lod_extras_sorted):
        vp         = ex['vp']
        vc         = vc_lod0
        vbuf_start = geom_start + vp
        ibuf_start = vbuf_start + vc * stride
        next_start = geom_start + vps_sorted[i + 1]
        ic_bytes   = next_start - ibuf_start
        if ic_bytes < 0 or ibuf_start >= len(data):
            continue
        ic = ic_bytes // idx_size
        lods.append({
            'vbuf_start': vbuf_start,
            'vc':         vc,
            'stride':     stride,
            'ibuf_start': ibuf_start,
            'ic':         ic,
            'bbox':       ex['bbox'],
            'idx_size':   idx_size,
        })

    return lods if lods else None


_SENTINEL = bytes([0x00, 0x00, 0x00, 0x02, 0x04, 0x05])

_MAT_KEYWORDS = [
    (b'Panel_Principal',       0),    # paint
    (b'Metal_Brushed_Dark',    2),    # dark metal variant -> dark bucket
    (b'Metal_Brushed',         1),    # metal
    (b'Metal_Standard_Zinc',   1),    # metal variant
    (b'Metal_Standard',        1),    # metal
    (b'Metal_RedPaint',        0),    # painted panel
    (b'Metal_Painted_Yellow',  4),    # yellow accent -> closest bucket (emissive-yellow default)
    (b'MetallicPaint_Color2',  0),    # painted panel variant
    (b'Irridescent_Metal',     1),    # metal
    (b'Yellow_Plastic',        4),    # yellow accent -> closest bucket (emissive-yellow default)
    (b'White_Basic_Color1',    3),    # light/white
    (b'Black_Basic',           2),    # dark
    (b'Grid_Hex',              2),    # dark grille/vent pattern
    (b'Grille_Square',         2),    # dark grille/vent pattern
    (b'POM_Decals_02_Zinc',    2),    # dark decal variant
    (b'POM_Decals_01',         2),    # dark
    (b'POM_Decals_02',         2),    # dark
    (b'POM_Decals_03',         2),    # dark
    (b'POM2',                  2),    # dark decal
    (b'Signaletique_01_Black', 2),    # dark signage backing
    (b'Signaletique_01_Yellow', 4),   # emissive signage stripe
    (b'Signaletique_02_White', 3),    # light/white signage
    (b'Signaletique_02_Black', 2),    # dark signage backing
    (b'Signaletique_01',       4),    # emissive
    (b'Signaletique_02',       4),    # emissive
    (b'Signaletic_02',         4),    # emissive (alt spelling)
    (b'Signaletic_01',         4),    # emissive (alt spelling)
    (b'Emissiv_Generic_01',    4),    # emissive
    (b'Marques_colored',       0),    # painted logo/decal
]


def parse_material_groups(data, blocks, geom_start=None):
    """Parse material groups from LOD0 extra and the embedded material section.

    Returns (gc, ic_per_group, mat_roles) where:
      gc           = number of material groups
      ic_per_group = list of index counts per group (for LOD0)
      mat_roles    = list of role ints in group order (paint=0, metal=1, dark=2, emissive=4)

    geom_start: absolute byte offset of geometry section. When None, read from
                data[4:8] (correct for standard HMD where header is at byte 0).
                Pass explicitly for ring-buffer files where data=raw.
    """
    blk0 = blocks[0]
    gc = data[blk0['extra_off'] + 4]
    std_extra_len = 38 + gc * 4        # 58 for gc=5, 62 for gc=6
    ic_per_group = list(struct.unpack_from('<%dI' % gc, data, blk0['extra_off'] + 5))
    if geom_start is None:
        geom_start = _u32(data, 4)

    # The material section is embedded in the last attr block's extra, normally starting
    # 5 bytes before the end of the standard-length extra. Find the last attr block whose
    # extra_off is before geom_start (skips fake blocks that can arise when a 0x0B byte
    # appears in bbox data and fools the parser).
    valid_blocks = [b for b in blocks if b['extra_off'] < geom_start]
    if not valid_blocks:
        return gc, ic_per_group, []
    last_valid = valid_blocks[-1]

    # Scan from the start of the last block's extra section rather than the fixed
    # std_extra_len offset: some files (e.g. those with per-LOD embedded texture paths)
    # have a larger-than-standard extra section, which throws off the fixed offset and
    # starts the scan mid-string. Scanning the whole extra region is safe because matches
    # below require an exact length-prefix byte, so stray bytes can't produce a false hit.
    mat_off = last_valid['extra_off']

    # It ends at the LOD descriptor sentinel.
    sent_idx = data.find(_SENTINEL, mat_off)
    mat_end = sent_idx if sent_idx > mat_off else geom_start
    mat_bytes = data[mat_off:mat_end]

    # Scan for known material names. Require the byte immediately before the name to
    # equal len(name) — this is the format's length-prefix byte — so that a short name
    # (e.g. "Signaletique_01") cannot spuriously match inside a longer one that shares
    # it as a prefix (e.g. "Signaletique_01_Black").
    found = []
    for kw, role in _MAT_KEYWORDS:
        idx = mat_bytes.find(bytes([len(kw)]) + kw)
        if idx >= 0:
            found.append((idx, role))
    found.sort()

    # Take first gc entries in byte-position order (position of each name's length-prefix
    # byte matches the group's order of appearance in the file).
    mat_roles = []
    for idx, role in found:
        if len(mat_roles) < gc:
            mat_roles.append(role)

    # Pad with dark (2) if we couldn't identify all groups.
    while len(mat_roles) < gc:
        mat_roles.append(2)

    return gc, ic_per_group, mat_roles


def read_verts_f16(data, vbuf_start, vc, stride):
    """Read vc vertex positions (float16×3) from a production HMD vertex buffer.
    Positions are at stride offset 0 (first 3 float16 = x, y, z).
    """
    verts = []
    for vi in range(vc):
        off = vbuf_start + vi * stride
        x = struct.unpack_from('<e', data, off)[0]
        y = struct.unpack_from('<e', data, off + 2)[0]
        z = struct.unpack_from('<e', data, off + 4)[0]
        verts.append((x, y, z))
    return verts


def read_indices_le_u16(data, ibuf_start, ic):
    """Read ic little-endian uint16 triangle indices."""
    return list(struct.unpack_from('<%dH' % ic, data, ibuf_start))


def read_indices_le_u32(data, ibuf_start, ic):
    """Read ic little-endian uint32 triangle indices (for meshes with vc > 65535)."""
    return list(struct.unpack_from('<%dI' % ic, data, ibuf_start))
