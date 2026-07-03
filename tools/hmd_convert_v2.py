"""
hmd_convert_v2.py -- Correct compound-mesh converter using the real Heaps HMD
model hierarchy (hmd_parse_heaps.parse), instead of the old heuristic/guessed
object-boundary detection in hmd_parse_prod.py.

Root cause this fixes: the old converter only ever read raw geometry buffers and
merged them assuming every sub-object sat at the origin with no scale. The real
format stores a `models[]` array where each named part (e.g. "Water_Collector",
"Water_Collector_Piston", "Water_Collector_Pannel_L") has its OWN position
(translation), quaternion rotation, AND scale -- applied on top of the raw
geometry. Ignoring this caused wrong absolute sizes (Water_Collector's body is
authored at scale 0.33, not 1.0) and wrong relative placement of sub-parts
(e.g. HiPi_Overclocked_Laser's "Receiver" sits offset from "Base"/"Rotary", not
at the same origin).

Usage:
    python tools/hmd_convert_v2.py <input.fbx> <output.bin>
"""

import json
import os
import re
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import struct

from hmd_parse_heaps import parse, stride_bytes
from hmd_parse_prod import read_verts_f16, read_indices_le_u16, read_indices_le_u32, _MAT_KEYWORDS
from hmd_to_bin import write_bin, _DEFAULT_ROLES
from find_socket_chain import find_socket_chain


def read_verts_generic(data, vbuf_start, vc, stride, fields):
    """Read vc vertex positions using the file's own declared position precision.

    read_verts_f16 hardcodes float16 positions, which is wrong for files whose
    position field is actually float32 (type code 3 -> fmt=3,prec=0=F32 per
    stride_bytes' encoding) -- confirmed on Pathway_Puncher.fbx, where blindly
    reading as f16 produced NaN for ~6% of vertices (raw float32 bit patterns
    reinterpreted as two garbage float16 values).
    """
    pos_type = fields[0][1]
    prec = pos_type >> 4
    if prec == 0:  # F32
        verts = []
        for vi in range(vc):
            off = vbuf_start + vi * stride
            verts.append(struct.unpack_from('<3f', data, off))
        return verts
    return read_verts_f16(data, vbuf_start, vc, stride)

_COLORS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'material_colors.json')
try:
    with open(_COLORS_PATH, encoding='utf-8') as _f:
        _REAL_COLORS = json.load(_f)
except FileNotFoundError:
    _REAL_COLORS = {}


def quat_rotate(qx, qy, qz, v):
    """Rotate vector v by the quaternion (qx,qy,qz,qw), where qw is reconstructed
    as sqrt(1 - (qx^2+qy^2+qz^2)) matching Data.hx's Position.get_qw() -- the file
    format only stores the vector part and assumes qw >= 0."""
    qw_sq = 1.0 - (qx * qx + qy * qy + qz * qz)
    qw = math.sqrt(qw_sq) if qw_sq > 0 else 0.0
    vx, vy, vz = v
    # cross(u, v)
    cx = qy * vz - qz * vy
    cy = qz * vx - qx * vz
    cz = qx * vy - qy * vx
    # cross(u, cross(u, v))
    ccx = qy * cz - qz * cy
    ccy = qz * cx - qx * cz
    ccz = qx * cy - qy * cx
    return (
        vx + 2 * qw * cx + 2 * ccx,
        vy + 2 * qw * cy + 2 * ccy,
        vz + 2 * qw * cz + 2 * ccz,
    )


def transform_vert(v, pos):
    """Apply a Model's own Position (scale -> rotate -> translate), matching
    the non-postScale branch of Data.hx's Position.toMatrix()."""
    x, y, z = v[0] * pos['sx'], v[1] * pos['sy'], v[2] * pos['sz']
    x, y, z = quat_rotate(pos['qx'], pos['qy'], pos['qz'], (x, y, z))
    return (x + pos['x'], y + pos['y'], z + pos['z'])


def transform_vert_chain(v, model, models):
    """Apply a model's own transform, then walk up its `parent` chain applying
    each ancestor's transform in turn, until reaching a node whose parent is
    -1 (root). Models are NOT always direct children of the scene root --
    e.g. MiningTool1_OC's Mining_Arm/Receiver/Plane are parented to Base
    (which has a real 180-degree Z rotation baked in), so skipping the chain
    leaves them at the wrong world position/orientation relative to Base even
    though each part's own local transform is correct in isolation. This was
    silently fine for files where every part parents directly to an identity
    scene root (e.g. Water_Collector), which is why it went unnoticed there."""
    node = model
    while True:
        v = transform_vert(v, node['position'])
        parent_idx = node['parent']
        if parent_idx < 0:
            break
        node = models[parent_idx]
    return v


def _base_name(name):
    m = re.match(r'^(.*?)(LOD\d+)$', name or '')
    return (m.group(1), m.group(2)) if m else (name, '')


def _pak_relative_path(hmd_path):
    """Turn a filesystem path to a source .fbx into the "assets/..." form
    used both in prefab "source" fields and as find_socket_chain's lookup
    key, e.g. ".../pak_out/assets/Vehicules/.../MiningTool1_OC.fbx" ->
    "assets/Vehicules/.../MiningTool1_OC.fbx"."""
    norm = hmd_path.replace('\\', '/')
    idx = norm.find('assets/')
    return norm[idx:] if idx >= 0 else None


def apply_socket_chain(models, chain):
    """Override each named part's `parent` to match the REAL, prefab-
    confirmed mount chain (e.g. ['Rotary', 'Mining_Arm', 'Receiver'], from
    find_socket_chain.py) wherever it disagrees with the file's own declared
    parent, for every LOD variant of an affected part (not just LOD0, so all
    LODs stay internally consistent). Returns a new list; models not
    mentioned in the chain, or already agreeing with it, keep their original
    dict object.

    This replaces an earlier from-scratch bounding-box-overlap heuristic
    that kept producing conflicting results across known cases (fixing one
    file's Receiver/Mining_Arm joint broke another's) -- ground truth read
    directly from the game's own prefab constraint data doesn't have that
    problem where it's available. Where it isn't (chain is None, e.g.
    Radar.fbx's prefab has no constraints at all), this is a no-op: the
    file's own declared parents are left completely alone rather than
    guessed at.
    """
    if not chain:
        return models
    name_to_idx = {mm['name']: i for i, mm in enumerate(models)}
    out = list(models)
    for i, mm in enumerate(models):
        prefix, lod = _base_name(mm['name'])
        pos = next((j for j, seg in enumerate(chain) if seg == prefix), None)
        if pos is None or pos == 0:
            continue  # not part of this chain, or is the chain's own root
        parent_idx = name_to_idx.get(chain[pos - 1] + lod)
        if parent_idx is not None and parent_idx != mm['parent']:
            out[i] = dict(mm, parent=parent_idx)
    return out


def match_material_role(name):
    if name:
        for kw, role in _MAT_KEYWORDS:
            if kw.decode() in name:
                if role == 4:
                    # Emissive keyword match, but if the real extracted texture
                    # color is dark, this is very likely a non-glowing signage
                    # backing variant rather than a genuine bright indicator
                    # light -- rendering a dark color as emissive (additive
                    # glow at 1.25 intensity, see meshLoader.js's roleMaterial)
                    # produces a jarring, wrongly-lit patch. Confirmed on the
                    # bare "Signaletique_01"/"Signaletique_02" name (used by
                    # ColdLaser, HiPiLaser, HiPi_Overclocked_Laser,
                    # MiningTool1_OC, Sniffer_radar, Gravitron), whose real
                    # average color is dark navy/purple (~20-80 range), not a
                    # bright indicator color.
                    real = _REAL_COLORS.get(name)
                    if real and max(real) < 110:
                        return 2
                return role
    return 2  # dark, matching the old fallback default


# Specific, manually-confirmed correspondences between a material name used
# by a mesh and a *differently-named* real basecolor texture that's actually
# the same material family -- extract_material_colors.py's exact/fuzzy name
# matching doesn't catch these because the naming diverges too much (word
# order, inserted words). Confirmed on ColdLaser (Cooling Laser), whose real
# in-game colors are mostly white/light gray, not the near-black default it
# was falling back to for every material below.
_COLOR_ALIASES = {
    'Metal_Painted_Color1': 'MetallicPaint_white_color1',  # light gray, not dark
    'POM_Decals_01': 'POM_Decals_03',  # same decal family, only _03 has a real texture
    'POM_Decals_02': 'POM_Decals_03',
}


def match_material_color(name, role):
    """Real average color extracted from the material's own basecolor texture
    (tools/extract_material_colors.py), falling back to the old invented
    per-role placeholder when no real texture was found for this name."""
    if name and name in _REAL_COLORS:
        return tuple(_REAL_COLORS[name])
    alias = _COLOR_ALIASES.get(name) if name else None
    if alias and alias in _REAL_COLORS:
        return tuple(_REAL_COLORS[alias])
    return _DEFAULT_ROLES[role][1] if role < len(_DEFAULT_ROLES) else (128, 128, 128)


def convert(hmd_path, out_path, verbose=True):
    raw = open(hmd_path, 'rb').read()
    off = raw.find(b'HMD\x06')
    if off < 0:
        raise ValueError(f"No HMD\\x06 magic in {hmd_path}")
    d = parse(raw, off)

    # Correct any sub-part's declared `parent` against the REAL mount chain
    # read from this mesh's own .prefab constraint data (see
    # find_socket_chain.py) -- a no-op when no prefab constraint data exists
    # (e.g. Radar.fbx), leaving the file's own declared parents untouched
    # rather than guessing.
    mesh_source_path = _pak_relative_path(hmd_path)
    chain = None
    if mesh_source_path:
        known_names = {_base_name(mm['name'])[0] for mm in d['models']}
        try:
            chain = find_socket_chain(mesh_source_path, known_names=known_names)
        except OSError:
            chain = None  # pak not available (e.g. running off already-extracted files only)
    models = apply_socket_chain(d['models'], chain)
    if verbose and chain:
        print(f"  socket chain (from prefab): {' -> '.join(chain)}")
        for old, new in zip(d['models'], models):
            if old is not new:
                old_parent = d['models'][old['parent']]['name'] if old['parent'] >= 0 else None
                print(f"  note: {new['name']!r} parent corrected to "
                      f"{models[new['parent']]['name']!r} (was {old_parent!r})")

    targets = [m for m in models if m['geometry'] >= 0 and m['name'] and m['name'].endswith('LOD0')]
    if not targets:
        raise ValueError(f"No *LOD0 models found in {hmd_path}")

    all_verts = []
    all_groups = []
    all_indices = []
    vbase = 0

    for m in targets:
        geom = d['geometries'][m['geometry']]
        stride = stride_bytes(geom['fields'])
        vc = geom['vertexCount']
        vbuf_start = off + d['dataPosition'] + geom['vertexPosition']
        ibuf_start = off + d['dataPosition'] + geom['indexPosition']
        ic = sum(geom['indexCounts'])

        verts_local = read_verts_generic(raw, vbuf_start, vc, stride, geom['fields'])
        verts_world = [transform_vert_chain(v, m, models) for v in verts_local]

        is_small = vc <= 0x10000
        indices = read_indices_le_u16(raw, ibuf_start, ic) if is_small else read_indices_le_u32(raw, ibuf_start, ic)

        bad = [i for i, v in enumerate(indices) if v >= vc]
        if bad:
            indices = [v if v < vc else 0 for v in indices]

        mat_indices = m.get('materials', [])
        group_start = len(all_indices)
        for gi, count in enumerate(geom['indexCounts']):
            mat_idx = mat_indices[gi] if gi < len(mat_indices) else None
            mat_name = (d['materials'][mat_idx]['name']
                        if mat_idx is not None and 0 <= mat_idx < len(d['materials']) else None)
            role = match_material_role(mat_name)
            rgb = match_material_color(mat_name, role)
            all_groups.append({'role': role, 'rgb': rgb, 'start': group_start, 'count': count})
            group_start += count

        all_verts.extend(verts_world)
        all_indices.extend(v + vbase for v in indices)
        vbase += vc

        if verbose:
            print(f"  part {m['name']!r}: vc={vc} ic={ic} pos=({m['position']['x']:.3f},"
                  f"{m['position']['y']:.3f},{m['position']['z']:.3f}) "
                  f"scale=({m['position']['sx']:.4f},{m['position']['sy']:.4f},{m['position']['sz']:.4f})")

    i32 = len(all_verts) > 0x10000
    write_bin(out_path, all_verts, all_groups, all_indices, i32=i32)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])


if __name__ == '__main__':
    main()
