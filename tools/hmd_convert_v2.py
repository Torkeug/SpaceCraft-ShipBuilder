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

import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hmd_parse_heaps import parse, stride_bytes
from hmd_parse_prod import read_verts_f16, read_indices_le_u16, read_indices_le_u32, _MAT_KEYWORDS
from hmd_to_bin import write_bin, _DEFAULT_ROLES


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


def match_material_role(name):
    if name:
        for kw, role in _MAT_KEYWORDS:
            if kw.decode() in name:
                return role
    return 2  # dark, matching the old fallback default


def convert(hmd_path, out_path, verbose=True):
    raw = open(hmd_path, 'rb').read()
    off = raw.find(b'HMD\x06')
    if off < 0:
        raise ValueError(f"No HMD\\x06 magic in {hmd_path}")
    d = parse(raw, off)

    targets = [m for m in d['models'] if m['geometry'] >= 0 and m['name'] and m['name'].endswith('LOD0')]
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

        verts_local = read_verts_f16(raw, vbuf_start, vc, stride)
        verts_world = [transform_vert_chain(v, m, d['models']) for v in verts_local]

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
            rgb = _DEFAULT_ROLES[role][1] if role < len(_DEFAULT_ROLES) else (128, 128, 128)
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
