"""
Compute a ship-builder part's real `dims` (the [l,w,h] grid-footprint stat
shown in the inspector and used by fitGeom's box-fit scaling) directly from
its actual converted mesh, instead of hand-measuring a screenshot and typing
rounded numbers into ship_editor_data.json.

Why this exists: manually reading a bounding box off a test render and
copying rounded numbers into JSON is exactly the kind of error-prone process
that produced this session's repeated dims mistakes (wrong axis order, wrong
storage convention). This script derives dims the same way the browser
renderer actually will: read the real .bin vertex data, apply the exact same
rotation sequence fitGeom() applies for that part kind, compute the resulting
axis-aligned bounding box, and invert shipbuilder/js/main.js's partDims()
formula to get the stored [l,w,h] value.

Usage:
    python tools/compute_part_dims.py Cockpit_TC1 Cockpit_LR1 ...
    python tools/compute_part_dims.py --all-cockpits

Rotation sequences (must be kept in sync with meshLoader.js's fitGeom -- if
that changes, update ROTATION_SEQUENCES here too):
    cockpit:  rotateX(+90), rotateY(180), rotateZ(180)
    default:  rotateX(-90)
"""
import argparse
import json
import math
import os
import struct

MESH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shipbuilder', 'ship_meshes')
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shipbuilder', 'ship_editor_data.json')

ROTATION_SEQUENCES = {
    'cockpit': [('x', 90), ('y', 180), ('z', 180)],
    'default': [('x', -90)],
}


def read_verts(bin_path):
    with open(bin_path, 'rb') as f:
        data = f.read()
    vc, ic, gc = struct.unpack('<IIB', data[:9])
    bbox = struct.unpack('<6f', data[9:33])
    o = 33
    verts = []
    for _ in range(vc):
        ux, uy, uz = struct.unpack_from('<3H', data, o); o += 6
        x = bbox[0] + ux / 65535 * (bbox[3] - bbox[0])
        y = bbox[1] + uy / 65535 * (bbox[4] - bbox[1])
        z = bbox[2] + uz / 65535 * (bbox[5] - bbox[2])
        verts.append((x, y, z))
    return verts


def rotate_axis(verts, axis, deg):
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    out = []
    for x, y, z in verts:
        if axis == 'x':
            y, z = y * c - z * s, y * s + z * c
        elif axis == 'y':
            x, z = x * c + z * s, -x * s + z * c
        elif axis == 'z':
            x, y = x * c - y * s, x * s + y * c
        out.append((x, y, z))
    return out


def apply_rotation_sequence(verts, sequence):
    for axis, deg in sequence:
        verts = rotate_axis(verts, axis, deg)
    return verts


def bbox_size(verts):
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def compute_dims(mesh_key, part_kind, real_scale=1.0):
    """Returns the stored [l, w, h] dims value for a part.

    part_kind: 'cockpit' or 'default' -- selects the rotation sequence AND the
    matching partDims() inverse (both kinds use the SAME formula as of this
    session's fix: stored=[l,w,h] -> three.js target=[l,h,w], i.e.
    l=target_X, h=target_Y, w=target_Z).
    """
    bin_path = os.path.join(MESH_DIR, f'{mesh_key}.bin')
    verts = read_verts(bin_path)
    verts = apply_rotation_sequence(verts, ROTATION_SEQUENCES[part_kind])
    size_x, size_y, size_z = bbox_size(verts)
    size_x, size_y, size_z = size_x * real_scale, size_y * real_scale, size_z * real_scale
    # partDims(): const [l,w,h] = part.dims; return [l,h,w];  =>  target=[l,h,w]=[X,Y,Z]
    # invert: l=target_X, h=target_Y, w=target_Z  =>  stored=[l,w,h]=[X,Z,Y]
    l, w, h = size_x, size_z, size_y
    return [round(l), round(w), round(h)], (size_x, size_y, size_z)


# Real prefab-level uniform scale for each cockpit's mesh (from the real
# .prefab model-node scale -- see tools/hmd_format_notes.md finding 18).
# DA1 is a merged compound mesh (tools/merge_prefab_parts.py) whose real
# per-file scale is already baked into its vertices, so its factor here is 1.0.
COCKPIT_REAL_SCALE = {
    'Cockpit_TC1': 1.0, 'Cockpit_LR1': 1.0, 'Cockpit_MK1': 1.0, 'Cockpit_AE1': 1.08,
    'Cockpit_AE3': 1.0, 'Cockpit_LR3': 1.0, 'Cockpit_LR2': 0.7, 'Cockpit_MK3': 1.0,
    'Cockpit_AE2': 0.8, 'Cockpit_MK2': 0.8, 'Cockpit_DA1': 1.0,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('part_ids', nargs='*', help='Part ids (e.g. Cockpit_TC1); mesh key is looked up from ship_editor_data.json')
    ap.add_argument('--all-cockpits', action='store_true')
    ap.add_argument('--write', action='store_true', help='Write computed dims back into ship_editor_data.json (otherwise just prints)')
    args = ap.parse_args()

    with open(DATA_PATH, encoding='utf-8') as f:
        data = json.load(f)
    parts = data['parts'] if 'parts' in data else data
    by_id = {p['id']: p for p in parts}

    target_ids = list(args.part_ids)
    if args.all_cockpits:
        target_ids = [p['id'] for p in parts if p.get('group') == 'Cockpits']

    for pid in target_ids:
        part = by_id[pid]
        mesh_key = part['shapes'][0]['m'] if part.get('shapes') else part.get('m')
        scale = COCKPIT_REAL_SCALE.get(pid, 1.0)
        dims, raw = compute_dims(mesh_key, 'cockpit', scale)
        cur = part.get('dims')
        print(f'{pid:15s} mesh={mesh_key:14s} raw_size=({raw[0]:.2f},{raw[1]:.2f},{raw[2]:.2f}) current={cur} computed={dims}')
        if args.write:
            part['dims'] = dims

    if args.write:
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
        print(f'\nWrote {len(target_ids)} updated dims to {DATA_PATH}')


if __name__ == '__main__':
    main()
