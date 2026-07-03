"""
Merge multiple HMD source files into a single .bin, using each file's real
cross-file placement (position + scale) as declared in a real .prefab
(HBSON), composed up that prefab's own parent chain.

Why this exists: hmd_convert_v2.py composes transforms *within* a single HMD
file's own models[] hierarchy (e.g. a tool's arm/receiver/plane sub-parts),
but some items are genuinely built from two or more *separate* .fbx files
referenced side-by-side in one prefab -- e.g. Cockpit_DA1 ("Cocoon" Cockpit),
whose real prefab (prefabs/ships/parts/cockpit/Cockpit_DA1.prefab) places
Cockpit_DA1_INT.fbx and Cockpit_DA1_EXT.fbx as siblings under a shared
"INTEXT" node, itself under a "part" node with its own scale:

    part (scale=1.5)
      INTEXT (x=0.085, z=-0.655)
        Cockpit_DA1_INT (x=0.185, z=-0.035, scale=1.0)
        Cockpit_DA1_EXT (x=-0.185, z=0.035, scale=0.97)

Each source file's own internal model hierarchy is first composed exactly as
hmd_convert_v2.convert() does (so a source file that is itself multi-part,
e.g. has its own sub-components, still works correctly), and only THEN is
the additional cross-file prefab transform (scale, then translate, per the
same convention as transform_vert -- see hmd_convert_v2.py) applied on top.

Usage: define a PARTS list below (or extend to read one from a prefab
programmatically) and run this file directly, or import merge_parts().
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hmd_parse_heaps import parse, stride_bytes
from hmd_parse_prod import read_verts_f16 as _unused_import  # noqa: F401 (parity with hmd_convert_v2 imports)
from hmd_to_bin import write_bin
from hmd_convert_v2 import (
    read_verts_generic, transform_vert_chain, apply_socket_chain,
    match_material_role, match_material_color, _pak_relative_path, _base_name,
)
from find_socket_chain import find_socket_chain


def _read_indices(raw, ibuf_start, ic, is_small):
    import struct
    if is_small:
        return list(struct.unpack_from(f'<{ic}H', raw, ibuf_start))
    return list(struct.unpack_from(f'<{ic}I', raw, ibuf_start))


def _apply_cross_file_transform(v, scale, translate):
    x, y, z = v[0] * scale, v[1] * scale, v[2] * scale
    return (x + translate[0], y + translate[1], z + translate[2])


def convert_one_file_verts(hmd_path, verbose=True):
    """Runs the same per-file model composition as hmd_convert_v2.convert(),
    but returns (verts_world, indices, groups) instead of writing a .bin, so
    the caller can apply an additional cross-file transform before merging."""
    raw = open(hmd_path, 'rb').read()
    off = raw.find(b'HMD\x06')
    if off < 0:
        raise ValueError(f"No HMD\\x06 magic in {hmd_path}")
    d = parse(raw, off)

    mesh_source_path = _pak_relative_path(hmd_path)
    chain = None
    if mesh_source_path:
        known_names = {_base_name(mm['name'])[0] for mm in d['models']}
        try:
            chain = find_socket_chain(mesh_source_path, known_names=known_names)
        except OSError:
            chain = None
    models = apply_socket_chain(d['models'], chain)

    targets = [m for m in models if m['geometry'] >= 0 and m['name'] and m['name'].endswith('LOD0')]
    if not targets:
        raise ValueError(f"No *LOD0 models found in {hmd_path}")

    verts = []
    indices = []
    groups = []
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
        idx = _read_indices(raw, ibuf_start, ic, is_small)
        idx = [v if v < vc else 0 for v in idx]

        mat_indices = m.get('materials', [])
        group_start = len(indices)
        for gi, count in enumerate(geom['indexCounts']):
            mat_idx = mat_indices[gi] if gi < len(mat_indices) else None
            mat_name = (d['materials'][mat_idx]['name']
                        if mat_idx is not None and 0 <= mat_idx < len(d['materials']) else None)
            role = match_material_role(mat_name)
            rgb = match_material_color(mat_name, role)
            groups.append({'role': role, 'rgb': rgb, 'start': group_start, 'count': count})
            group_start += count

        verts.extend(verts_world)
        indices.extend(v + vbase for v in idx)
        vbase += vc

        if verbose:
            print(f"    part {m['name']!r}: vc={vc} ic={ic}")

    return verts, indices, groups


def merge_parts(parts, out_path, verbose=True):
    """parts: list of dicts {path, scale (float, uniform), translate (x,y,z)}
    giving each source file's real cross-file transform (already composed up
    its own prefab parent chain -- see module docstring for how to derive
    this from a real .prefab)."""
    all_verts = []
    all_indices = []
    all_groups = []
    vbase = 0
    for part in parts:
        if verbose:
            print(f"  {part['path']}  scale={part['scale']} translate={part['translate']}")
        verts, indices, groups = convert_one_file_verts(part['path'], verbose=verbose)
        verts = [_apply_cross_file_transform(v, part['scale'], part['translate']) for v in verts]
        for g in groups:
            g['start'] += len(all_indices)
        all_verts.extend(verts)
        all_indices.extend(v + vbase for v in indices)
        all_groups.extend(groups)
        vbase += len(verts)

    i32 = len(all_verts) > 0x10000
    write_bin(out_path, all_verts, all_groups, all_indices, i32=i32)
    print(f"Wrote {len(all_verts)} verts, {len(all_indices)} indices, {len(all_groups)} groups -> {out_path}")


if __name__ == '__main__':
    # Cockpit_DA1: part(scale=1.5) -> INTEXT(x=0.085,z=-0.655) -> {INT(x=0.185,z=-0.035,scale=1.0), EXT(x=-0.185,z=0.035,scale=0.97)}
    # Composed (scale then translate, matching transform_vert's convention):
    #   INT: scale = 1.0 * 1.5 = 1.5   translate = 1.5 * ((0.185,0,-0.035) + (0.085,0,-0.655)) = (0.405, 0, -1.035)
    #   EXT: scale = 0.97 * 1.5 = 1.455  translate = 1.5 * ((-0.185,0,0.035) + (0.085,0,-0.655)) = (-0.150, 0, -0.930)
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('int_fbx')
    ap.add_argument('ext_fbx')
    ap.add_argument('out_bin')
    args = ap.parse_args()

    parts = [
        {'path': args.int_fbx, 'scale': 1.5, 'translate': (0.405, 0.0, -1.035)},
        {'path': args.ext_fbx, 'scale': 1.455, 'translate': (-0.150, 0.0, -0.930)},
    ]
    merge_parts(parts, args.out_bin)
