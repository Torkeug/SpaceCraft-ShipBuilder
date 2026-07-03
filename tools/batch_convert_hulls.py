"""
batch_convert_hulls.py — Convert all extracted Main_Structures HMD files to .bin.
Overwrites any existing .bin files.
Updates shipbuilder/ship_meshes/_manifest.json with converted file stats.

Usage:
    python tools/batch_convert_hulls.py
"""

import os
import sys
import json
import struct
import glob

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)

PAK_OUT = os.path.join(REPO_ROOT, 'pak_out', 'assets', 'Vehicules',
                       'Buildings_Parts', 'Main_Structures')
MESH_OUT = os.path.join(REPO_ROOT, 'shipbuilder', 'ship_meshes')
MANIFEST = os.path.join(MESH_OUT, '_manifest.json')

sys.path.insert(0, TOOLS_DIR)

HULL_SIZES = [
    '4x3x1', '4x3x2',
    '6x3x1', '6x3x2',
    '8x3x1', '8x3x2',
    '8x6x2',
    '12x6x2', '12x6x4',
    '16x6x2', '16x6x4',
]


def read_bin_stats(bin_path):
    """Return (group_count, triangle_count, vertex_count) from a .bin file."""
    with open(bin_path, 'rb') as f:
        vc, ic, gc = struct.unpack('<IIB', f.read(9))
    return gc, ic // 3, vc


def main():
    from hmd_to_bin import convert

    if not os.path.isdir(PAK_OUT):
        print(f'ERROR: pak_out not found at {PAK_OUT!r}')
        print('Run: python tools/pak_extract.py --extract "Main_Structures" --out pak_out')
        sys.exit(1)

    os.makedirs(MESH_OUT, exist_ok=True)

    try:
        with open(MANIFEST) as f:
            manifest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}

    converted = skipped = errors = 0

    for size in HULL_SIZES:
        size_dir = os.path.join(PAK_OUT, size)
        if not os.path.isdir(size_dir):
            print(f'  SKIP (no dir): {size}')
            skipped += 1
            continue

        fbx_files = sorted(glob.glob(os.path.join(size_dir, f'{size}_*.fbx')))
        if not fbx_files:
            print(f'  SKIP (no files): {size}')
            skipped += 1
            continue

        print(f'\n{size}:')
        for fbx in fbx_files:
            basename = os.path.splitext(os.path.basename(fbx))[0]  # e.g. "4x3x2_A"
            out_bin = os.path.join(MESH_OUT, basename + '.bin')

            try:
                convert(fbx, out_bin)
                gc, tc, vc = read_bin_stats(out_bin)
                manifest[basename] = {'g': gc, 'i32': False, 't': tc, 'v': vc}
                print(f'  OK  {basename}: gc={gc} tc={tc} vc={vc}')
                converted += 1
            except Exception as e:
                print(f'  ERR {basename}: {e}')
                errors += 1

    with open(MANIFEST, 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    print(f'\nDone: {converted} converted, {skipped} skipped, {errors} errors')
    print(f'Manifest updated: {MANIFEST}')


if __name__ == '__main__':
    main()
