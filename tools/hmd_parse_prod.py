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

import re
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
        def _valid_extra(es, gc_candidate):
            """Validate a candidate LOD0 extra section: vp=0, vbuf_size divides evenly
            by stride, and the resulting index buffer fits within the file."""
            if body[es + 4] != gc_candidate:
                return False
            if struct.unpack_from('<I', body, es)[0] != 0:
                return False
            vbuf_size_val = struct.unpack_from('<I', body, es + 5 + gc_candidate * 4)[0]
            if vbuf_size_val == 0 or vbuf_size_val % stride != 0:
                return False
            ic_candidate = sum(struct.unpack_from(f'<{gc_candidate}I', body, es + 5))
            vc_candidate = vbuf_size_val // stride
            ibuf_end = geom_start + vc_candidate * stride + ic_candidate * 2
            return 0 < ibuf_end <= len(raw)

        if first_ring_pos >= 30:
            # Large trailer: first block is LOD1. Backtrack to find LOD0 extra.
            # gc (material group count) varies widely across asset categories — hull
            # pieces use 5-6, but small props/tools can use anywhere from 1 to ~32
            # (confirmed: a "Receiver" sub-object with gc=17 was silently dropped
            # before this range was widened — see compound multi-object notes below).
            for gc_candidate in range(1, 33):
                extra_len = 38 + gc_candidate * 4
                es = first_block_off - extra_len
                if es < 0:
                    continue
                if _valid_extra(es, gc_candidate):
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
                for gc_candidate in range(1, 33):
                    if attrs_end + 4 < len(body) and _valid_extra(attrs_end, gc_candidate):
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


def _valid_attr_block(data, off):
    """
    Check whether `off` looks like a genuine attr block (0x0B marker + count +
    N×(name_len + name + type)) as opposed to an incidental 0x0B byte inside
    embedded material name/path strings (common in files with many texture
    references, where a stray 0x0B can otherwise be mistaken for a new block).

    Returns (attrs_end, stride) or (None, 0).
    """
    if off >= len(data) or data[off] != 0x0B:
        return None, 0
    if off + 1 >= len(data):
        return None, 0
    attr_count = data[off + 1]
    if not (1 <= attr_count <= 6):
        return None, 0
    pos = off + 2
    stride = 0
    for _ in range(attr_count):
        if pos >= len(data):
            return None, 0
        name_len = data[pos]; pos += 1
        if not (1 <= name_len <= 20) or pos + name_len >= len(data):
            return None, 0
        name = data[pos:pos + name_len]
        if not all(0x20 <= b < 0x7F for b in name):
            return None, 0
        pos += name_len
        atype = data[pos]; pos += 1
        if atype not in _ATTR_SIZE:
            return None, 0
        stride += _ATTR_SIZE[atype]
    return pos, stride


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
        next_ob = attrs_end + min_skip - 1
        while True:
            next_ob = data.find(bytes([0x0B]), next_ob + 1)
            if next_ob < 0:
                break
            # Reject incidental 0x0B hits inside material name/path strings (common
            # in files with many texture references) that don't parse as a real block.
            if _valid_attr_block(data, next_ob)[0] is not None:
                break
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
    if lod_count == 0:
        # Some compound files (e.g. Water_Collector.fbx) have a separate LOD
        # descriptor sentinel PER sub-object (each with its own small local name
        # list) rather than one shared section for the whole file — data.find()
        # only locates the first one, and the byte before it doesn't hold a valid
        # global count. Fall back to trusting however many real attribute blocks
        # were actually found (confirmed exact on Water_Collector.fbx: 16 blocks
        # found == 16 LOD names across its 4 named sub-objects: base/Pannel_L/
        # Pannel_R/Piston, each with 4 LODs).
        lod_count = len(blocks)

    # The LOD descriptor section (right after the sentinel) embeds each LOD's own
    # name, e.g. "BaseLOD0", "RotaryLOD0" — these are ground truth for sub-object
    # identity in compound multi-object files, far more reliable than inferring
    # object boundaries from vertex-count patterns (confirmed exact match, in file
    # order, against all declared LODs on MiningTool1_OC.fbx: Base/Rotary/
    # Mining_Arm/Receiver/Plane). The per-entry record layout has a variable-length
    # prefix we haven't fully cracked, so this uses a regex scan rather than a
    # fixed-stride walk — good enough since names+trailing LOD number are always
    # printable ASCII immediately followed by digits.
    lod_names = [m.decode() for m in
                 re.findall(rb'[A-Za-z_][A-Za-z0-9_]*LOD[0-9]+', data[idx + 6:idx + 6 + 4000])]
    lod_base_names = [re.sub(r'LOD\d+$', '', n) for n in lod_names]

    # Collect vp, ic (from ic_per_group — reliable), and bbox for each LOD.
    # IMPORTANT: vc is NOT stored directly for LOD1+ in a form we can trust. A field
    # at the expected "vbuf_size" position exists but only holds a valid value for
    # true LOD0 (vp=0); for later entries it's some other/stale value that doesn't
    # divide evenly by stride. The reliable way to get vc for LOD i is to derive it
    # from the gap to the NEXT LOD's vp: vc[i] = (vp[i+1] - vp[i] - ic[i]*idx_size) / stride
    # (confirmed exact on every LOD boundary in ColdLaser.fbx except the very last,
    # which has trailing material-name bytes after its index buffer).
    #
    # This also reveals that compound/prop assets (Tools/, Decoratives_Parts/) can
    # pack *multiple independent sub-objects* into what the header calls "lod_count"
    # slots, each with its own decreasing-detail LOD chain — not one object's LODs.
    # Confirmed on ColdLaser.fbx: 12 declared "LODs" are actually 3 sub-objects (5+3+4
    # LODs), identifiable because vc jumps back UP at each new sub-object's LOD0
    # after decreasing within the previous one. The first sub-object is a small flat
    # base plate; by far the largest (most detailed) sub-object is a later one — i.e.
    # naively taking `lods[0]` gets an auxiliary part, not the main visual.
    idx_size = 4 if vc_lod0 > 65535 else 2

    lod_extras = []
    for lod in range(min(lod_count, len(blocks))):
        blk = blocks[lod]
        extra_off = blk['extra_off']
        if extra_off + 29 > len(data):
            break
        vp   = _u32(data, extra_off)
        gc_here = data[extra_off + 4] if extra_off + 4 < len(data) else 5
        if not (1 <= gc_here <= 32) or extra_off + 5 + gc_here * 4 + 4 > len(data):
            continue
        ic_per_group = struct.unpack_from(f'<{gc_here}I', data, extra_off + 5)
        ic_here = sum(ic_per_group)
        bbox_off = extra_off + 5 + gc_here * 4 + 4
        if bbox_off + 24 > len(data):
            bbox_off = extra_off + 29  # fallback
        bbox = list(struct.unpack_from('<6f', data, bbox_off))
        name = lod_base_names[lod] if lod < len(lod_base_names) else None
        lod_extras.append({'vp': vp, 'bbox': bbox, 'ic': ic_here, 'extra_off': extra_off, 'name': name})

    if not lod_extras:
        return None

    # Sort LODs by vp to get section boundaries
    lod_extras_sorted = sorted(lod_extras, key=lambda e: e['vp'])
    vps_sorted = [e['vp'] for e in lod_extras_sorted]
    # End sentinel: total geometry bytes
    total_geom = len(data) - geom_start
    vps_sorted.append(total_geom)

    # Prefer name-based object grouping (ground truth) over the vc-increase heuristic
    # when every sorted LOD has a name — a name change from the previous entry marks
    # a new sub-object, regardless of whether its LOD0 vc happens to be smaller than
    # the previous object's (the case the vc-heuristic gets wrong, e.g. "Mining_Arm"
    # having fewer vertices than "Rotary" before it).
    names_sorted = [e.get('name') for e in lod_extras_sorted]
    use_names = all(n is not None for n in names_sorted)

    prev_vc = None
    prev_name = None
    lods = []
    for i, ex in enumerate(lod_extras_sorted):
        vp = ex['vp']
        ic = ex['ic']
        gap = vps_sorted[i + 1] - vp - ic * idx_size
        if gap <= 0 or gap % stride != 0:
            continue
        vc = gap // stride
        vbuf_start = geom_start + vp
        ibuf_start = vbuf_start + vc * stride
        name = ex.get('name')
        if use_names:
            is_object_start = prev_name is None or name != prev_name
        else:
            is_object_start = prev_vc is None or vc > prev_vc
        prev_vc = vc
        prev_name = name
        lods.append({
            'vbuf_start': vbuf_start,
            'vc':         vc,
            'stride':     stride,
            'ibuf_start': ibuf_start,
            'ic':         ic,
            'bbox':       ex['bbox'],
            'idx_size':   idx_size,
            'is_object_start': is_object_start,
            'extra_off':  ex['extra_off'],
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
    (b'Metal_Painted_Yellow',  1),    # yellow-painted metal (caution marking) -> metal/gray, not glowing
    (b'MetallicPaint_Color2',  0),    # painted panel variant
    (b'Irridescent_Metal',     1),    # metal
    (b'Yellow_Plastic',        1),    # yellow accent panel -> metal/gray, not glowing
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

    # Tools/props category materials (discovered on MiningTool1_OC.fbx — these were
    # all absent from the original hull-piece-derived list above, causing most
    # groups on compound tool meshes to fall back to the "dark" default and look
    # like missing/invisible geometry against the dark viewport background).
    (b'Metal_Standard_Copper', 1),    # metal
    (b'Metal_Standard_Zinc2',  1),    # metal variant
    (b'Metal_Coil',            1),    # metal
    (b'Metal_Redpaint',        0),    # painted panel (alt case of Metal_RedPaint)
    (b'Gray_Plastic_Color1',   2),    # dark/neutral plastic housing
    (b'Plastic_Shiny_LightOrange', 4), # orange accent -> emissive bucket
    (b'Plastic_Shiny_Red',     4),    # red accent -> emissive bucket
    (b'Plastic_Cable_Red',     2),    # cable/wiring -> dark bucket
    (b'Yellow_Basic',          1),    # yellow accent panel -> metal/gray, not glowing
    (b'Orange_Basic',          4),    # orange accent -> emissive bucket
    (b'Emissiv_LampOrange',    4),    # emissive lamp (explicit "Emissiv" name — genuinely a lit lamp)
]


def parse_material_groups(data, blocks, geom_start=None, extra_off=None, mat_skip=0):
    """Parse material groups from a LOD's extra section and the embedded material section.

    Returns (gc, ic_per_group, mat_roles) where:
      gc           = number of material groups
      ic_per_group = list of index counts per group (for the target LOD)
      mat_roles    = list of role ints in group order (paint=0, metal=1, dark=2, emissive=4)

    geom_start: absolute byte offset of geometry section. When None, read from
                data[4:8] (correct for standard HMD where header is at byte 0).
                Pass explicitly for ring-buffer files where data=raw.
    extra_off:  byte offset of the target LOD's own extra section (its gc/ic_per_group).
                Defaults to blocks[0] (the file's first LOD) for backward compatibility;
                pass explicitly for a specific sub-object's LOD0 in a compound/multi-object
                file (see "compound multi-object files" note in parse_prod_hmd).
    mat_skip:   number of leading keyword matches to skip before assigning roles to this
                object's groups. Compound multi-object files embed ONE shared material
                name section covering ALL sub-objects' groups in sequence (materials are
                reused across sub-objects, so there are fewer unique names than total
                groups) — each sub-object's groups claim the next slice of that shared,
                file-wide ordered list, not always the first `gc` entries. Callers merging
                multiple sub-objects must pass the cumulative gc of objects already
                processed so each one claims the correct slice.
    """
    if extra_off is None:
        extra_off = blocks[0]['extra_off']
    gc = data[extra_off + 4]
    std_extra_len = 38 + gc * 4        # 58 for gc=5, 62 for gc=6
    ic_per_group = list(struct.unpack_from('<%dI' % gc, data, extra_off + 5))
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

    # Take the next gc entries starting after mat_skip (see mat_skip docstring above) —
    # in byte-position order (position of each name's length-prefix byte matches the
    # group's order of appearance in the file).
    window = found[mat_skip:mat_skip + gc]
    mat_roles = [role for _, role in window]

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
