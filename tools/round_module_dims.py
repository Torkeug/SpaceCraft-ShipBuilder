"""
Round outside-mount modules' `dims` up to clean integers for display/grid
purposes, without changing their rendered size.

Why: outside-mount module dims are stored as raw mesh-space [X,Y,Z] floats
(e.g. Large Solar Panel = [5.38, 0.61, 3.53]), derived directly from the real
mesh bounding box in an earlier session. That makes for an ugly, overly-
precise inspector display. Simply rounding those numbers is unsafe: fitGeom()
scales the mesh uniformly to fill whatever box `dims` specifies (see
tools/hmd_format_notes.md's fitGeom architecture note), so growing the box
(rounding up) would make the rendered mesh grow to match it -- a visible
regression, not just a display change.

The fix: round dims up (ceiling) per axis, then compute the exact uniform
`_meshScale` needed to cancel out the resulting box growth, so the final
rendered size is unchanged. Derivation:

    fitGeom computes s = min(dims_i / raw_size_i) over the three axes, then
    scales the mesh by s (uniformly), then by _meshScale (also uniformly, for
    parts outside the hull `_dimd` fast path). Final size on axis i is
    raw_size_i * s * meshScale. We want that to equal raw_size_i (unchanged),
    which only depends on s, not the axis -- so meshScale = 1/s is the exact,
    single, uniform value that restores the original size regardless of which
    axis is the limiting one.

Usage:
    python tools/round_module_dims.py --all
    python tools/round_module_dims.py SolarPanel3 Radar0
    (add --write to actually save; otherwise dry-run only)
"""
import argparse
import json
import math
import os

from compute_part_dims import read_verts, apply_rotation_sequence, bbox_size, ROTATION_SEQUENCES, DATA_PATH, MESH_DIR


def compute_rounded(mesh_key):
    bin_path = os.path.join(MESH_DIR, f'{mesh_key}.bin')
    verts = read_verts(bin_path)
    verts = apply_rotation_sequence(verts, ROTATION_SEQUENCES['default'])
    raw = bbox_size(verts)
    rounded = [math.ceil(v) for v in raw]
    s = min(rounded[i] / raw[i] for i in range(3))
    mesh_scale = 1.0 / s
    return raw, rounded, mesh_scale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('part_ids', nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    with open(DATA_PATH, encoding='utf-8') as f:
        data = json.load(f)
    parts = data['parts'] if 'parts' in data else data
    by_id = {p['id']: p for p in parts}

    target_ids = list(args.part_ids)
    if args.all:
        target_ids = [p['id'] for p in parts if p.get('kind') == 'module' and p.get('mount') == 'outside']

    for pid in target_ids:
        part = by_id[pid]
        mesh_key = part['shapes'][0]['m'] if part.get('shapes') else part.get('m')
        raw, rounded, mesh_scale = compute_rounded(mesh_key)
        cur = part.get('dims')
        print(f'{pid:18s} mesh={mesh_key:14s} raw=({raw[0]:.3f},{raw[1]:.3f},{raw[2]:.3f}) '
              f'current={cur} -> dims={rounded} _meshScale={mesh_scale:.4f}')
        if args.write:
            part['dims'] = rounded
            if abs(mesh_scale - 1.0) > 1e-6:
                part['_meshScale'] = [round(mesh_scale, 4)] * 3
            elif '_meshScale' in part:
                del part['_meshScale']

    if args.write:
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
        print(f'\nWrote {len(target_ids)} updated dims to {DATA_PATH}')


if __name__ == '__main__':
    main()
