"""
batch_convert_modules_v2.py -- Reconvert outside-mount module meshes using the
transform-aware converter (hmd_convert_v2.py), which applies each real HMD
model node's own position/rotation/scale instead of the old heuristic merge
that assumed every sub-part sat unscaled at the origin.

Falls back to the old converter (hmd_to_bin.convert) for files the new parser
can't yet handle (currently: the 3 Decoratives_Parts items, which hit an
animation/skin section this port doesn't fully cover) so those aren't
regressed.

Updates shipbuilder/ship_meshes/_manifest.json with the new vc/ic/gc stats.

Usage:
    python tools/batch_convert_modules_v2.py
"""

import os
import sys
import json
import struct

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)

PAK_OUT = os.path.join(REPO_ROOT, 'pak_out', 'assets', 'Vehicules', 'Buildings_Parts')
MESH_OUT = os.path.join(REPO_ROOT, 'shipbuilder', 'ship_meshes')
MANIFEST = os.path.join(MESH_OUT, '_manifest.json')

sys.path.insert(0, TOOLS_DIR)

from batch_convert_modules import MODULE_SOURCES
import hmd_convert_v2
import hmd_to_bin

# These fail the new HMD-model-hierarchy parser (animation/skin section not yet
# ported); keep converting them with the old heuristic converter for now.
FALLBACK_TO_V1 = {'Spot_Light_01', 'Spot_Light_Barrel', 'Aerator_Spot_01'}

# Legacy TestPE format (disc=0x00), not production HMD -- needs hmd_to_bin's
# G-style converter, not hmd_parse_heaps/hmd_convert_v2 which only understand
# the production models[] hierarchy.
FALLBACK_TO_V1.add('PathwayPuncher')


def read_bin_stats(path):
    with open(path, 'rb') as f:
        vc, ic, gc = struct.unpack('<IIB', f.read(9))
    return vc, ic, gc


def main():
    manifest = {}
    if os.path.exists(MANIFEST):
        manifest = json.load(open(MANIFEST))

    converted = errors = 0
    for key, rel in MODULE_SOURCES.items():
        src = os.path.join(PAK_OUT, rel)
        out = os.path.join(MESH_OUT, f'{key}.bin')
        if not os.path.exists(src):
            print(f"  MISSING source for {key}: {src}")
            errors += 1
            continue
        try:
            if key in FALLBACK_TO_V1:
                print(f"[{key}] (v1 fallback)")
                hmd_to_bin.convert(src, out)
            else:
                print(f"[{key}]")
                hmd_convert_v2.convert(src, out)
            vc, ic, gc = read_bin_stats(out)
            manifest[key] = {'g': gc, 'i32': ic > 0 and vc > 0x10000, 't': ic // 3, 'v': vc}
            converted += 1
        except Exception as e:
            print(f"  ERROR converting {key}: {e}")
            errors += 1

    with open(MANIFEST, 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    print(f"\nConverted {converted} modules ({errors} errors) -> manifest updated")


if __name__ == '__main__':
    main()
