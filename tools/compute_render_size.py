"""
Compute `_renderSize` -- a part's real, independent render-scale target (in
Three.js [X,Y,Z], post-rotation) -- decoupled from `dims` (which is now
purely a grid-placement/display stat, not a real game value for non-hull
parts; see hmd_format_notes.md finding 21 for the full rationale).

fitGeom() (shipbuilder/js/meshLoader.js) scales a part's mesh directly to
`part._renderSize` when present, completely independent of `dims` -- no more
coupling the two, no more `_meshScale` fudge factors.

Three categories, three ways of deriving the correct value:

  cockpit / outside-module: the REAL, unrounded mesh size (raw bbox, in the
    cockpit rotation sequence for cockpits, real prefab scale applied where
    known) -- these were already carefully derived from real data earlier
    this session, `_renderSize` is just that same computation without the
    integer rounding `dims` now uses for display/placement.

  thruster: NOT re-derived from scratch -- back-solved from today's *current*
    on-screen appearance (dims-fit scale x existing _meshScale, both already
    in ship_editor_data.json), so migrating to _renderSize is a pure
    mechanism change with zero visual difference. Thruster true size is
    still an open question (see finding 18) the user wants to check
    in-game before touching again.

Usage:
    python tools/compute_render_size.py --all-cockpits --write
    python tools/compute_render_size.py --all-modules --write
    python tools/compute_render_size.py --all-thrusters --write
"""
import argparse
import json
import os

from compute_part_dims import (
    read_verts, apply_rotation_sequence, bbox_size, ROTATION_SEQUENCES,
    DATA_PATH, MESH_DIR, COCKPIT_REAL_SCALE,
)


def raw_size(mesh_key, rotation_kind, real_scale=1.0):
    bin_path = os.path.join(MESH_DIR, f'{mesh_key}.bin')
    verts = read_verts(bin_path)
    verts = apply_rotation_sequence(verts, ROTATION_SEQUENCES[rotation_kind])
    sx, sy, sz = bbox_size(verts)
    return (sx * real_scale, sy * real_scale, sz * real_scale)


def render_size_cockpit(part):
    mesh_key = part['shapes'][0]['m']
    scale = COCKPIT_REAL_SCALE.get(part['id'], 1.0)
    return raw_size(mesh_key, 'cockpit', scale)


def render_size_module(part):
    mesh_key = part['shapes'][0]['m'] if part.get('shapes') else part.get('m')
    return raw_size(mesh_key, 'default', 1.0)


def render_size_thruster(part):
    """Back-solve the CURRENT rendered size from today's dims-fit + _meshScale,
    so switching to _renderSize doesn't change how thrusters look at all."""
    mesh_key = part['shapes'][0]['m']
    sx, sy, sz = raw_size(mesh_key, 'default', 1.0)
    # partDims() for non-cockpit/non-outside-module: stored [l,w,h] -> target [l,h,w] = [X,Y,Z]
    l, w, h = part['dims']
    target = (l, h, w)
    s = min(target[0] / sx, target[1] / sy, target[2] / sz)
    mx, my, mz = part.get('_meshScale', [1, 1, 1])
    return (sx * s * mx, sy * s * my, sz * s * mz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('part_ids', nargs='*')
    ap.add_argument('--all-cockpits', action='store_true')
    ap.add_argument('--all-modules', action='store_true')
    ap.add_argument('--all-thrusters', action='store_true')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()

    with open(DATA_PATH, encoding='utf-8') as f:
        data = json.load(f)
    parts = data['parts'] if 'parts' in data else data
    by_id = {p['id']: p for p in parts}

    jobs = []  # (part_id, compute_fn)
    for pid in args.part_ids:
        jobs.append((pid, render_size_cockpit))
    if args.all_cockpits:
        jobs += [(p['id'], render_size_cockpit) for p in parts if p.get('group') == 'Cockpits']
    if args.all_modules:
        jobs += [(p['id'], render_size_module) for p in parts if p.get('kind') == 'module' and p.get('mount') == 'outside']
    if args.all_thrusters:
        jobs += [(p['id'], render_size_thruster) for p in parts if p.get('group') == 'Engines & thrusters']

    for pid, fn in jobs:
        part = by_id[pid]
        size = fn(part)
        print(f'{pid:18s} _renderSize=({size[0]:.4f}, {size[1]:.4f}, {size[2]:.4f})  (current dims={part.get("dims")}, _meshScale={part.get("_meshScale")})')
        if args.write:
            part['_renderSize'] = [round(v, 4) for v in size]
            # _meshScale is now dead once _renderSize takes over in fitGeom
            # (the fallback branch that reads it is only reached when
            # _renderSize is absent) -- drop it so there's no stale,
            # unused-but-still-there field to confuse later.
            if '_meshScale' in part:
                del part['_meshScale']

    if args.write:
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
        print(f'\nWrote {len(jobs)} _renderSize values to {DATA_PATH}')


if __name__ == '__main__':
    main()
