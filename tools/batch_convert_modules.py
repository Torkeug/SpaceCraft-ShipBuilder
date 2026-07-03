"""
batch_convert_modules.py — Convert outside-mount module meshes to .bin.
Overwrites any existing .bin files for these keys.
Updates shipbuilder/ship_meshes/_manifest.json with converted file stats.

Usage:
    python tools/batch_convert_modules.py
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

# mesh key ("m" field in ship_editor_data.json) -> source .fbx relative to PAK_OUT
# Simple_Mining_Laser, RadarMK1, and Scanner have no file matching their part id in
# the pak; the prefabs for those ids reference unrelated template data (weapon/turret
# fx, unrelated cockpit meshes), matching the same red-herring pattern seen on
# Spot_Light_01's prefab. Mapped to the plain/base-tier file of the same tool family
# instead (see hmd_format_notes.md).
MODULE_SOURCES = {
    'Spot_Light_01':          'Decoratives_Parts/Spot_Light_01.fbx',
    'Spot_Light_Barrel':      'Decoratives_Parts/Spot_Light_Barrel.fbx',
    'Aerator_Spot_01':        'Decoratives_Parts/Aerator_Spot_01.fbx',
    'Simple_Mining_Laser':    'Tools/MiningTool.fbx',
    'ColdLaser':              'Tools/ColdLaser.fbx',
    'MiningTool1_OC':         'Tools/MiningTool1_OC.fbx',
    'Water_Collector':        'Tools/Water_Collector.fbx',
    # Both confirmed via data.cdb's actual "model" field (MiningTool2 ->
    # MiningTool_Medium.prefab, MiningTool2_OC -> MiningTool_Medium_OC.prefab).
    # No MiningTool_Medium_OC.fbx exists in the pak or model.props -- the OC
    # variant reuses the same mesh with different stats/material. The old
    # 'HiPiLaser.fbx'/'HiPi_Overclocked_Laser.fbx' files are real but wrong:
    # they belong to some other weapon, not this item (name-guessed match,
    # never verified against data.cdb).
    'HiPiLaser':              'Tools/MiningTool_Medium.fbx',
    'HiPi_Overclocked_Laser': 'Tools/MiningTool_Medium.fbx',
    'RadarMK1':               'Tools/Radar.fbx',
    'SmartRadar':             'Tools/SmartRadar.fbx',
    'Sniffer_Radar':          'Tools/Sniffer_radar.fbx',
    'Gravitron':              'Tools/Gravitron.fbx',
    'CrudeSolarPanel_Flat':   'Tools/CrudeSolarPanel_Flat.fbx',
    'SmallSolarPanel_Flat':   'Tools/SmallSolarPanel_Flat.fbx',
    'BigSolarPanel_Flat':     'Tools/BigSolarPanel_Flat.fbx',
    'Scanner':                'Tools/ScanningTool.fbx',
}


def read_bin_stats(bin_path):
    """Return (group_count, triangle_count, vertex_count) from a .bin file."""
    with open(bin_path, 'rb') as f:
        vc, ic, gc = struct.unpack('<IIB', f.read(9))
    return gc, ic // 3, vc


def main():
    from hmd_to_bin import convert

    if not os.path.isdir(PAK_OUT):
        print(f'ERROR: pak_out not found at {PAK_OUT!r}')
        sys.exit(1)

    os.makedirs(MESH_OUT, exist_ok=True)

    try:
        with open(MANIFEST) as f:
            manifest = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}

    converted = errors = 0

    for key, rel_path in MODULE_SOURCES.items():
        src = os.path.join(PAK_OUT, rel_path.replace('/', os.sep))
        out_bin = os.path.join(MESH_OUT, key + '.bin')

        if not os.path.isfile(src):
            print(f'  SKIP (missing source): {key} -> {rel_path}')
            errors += 1
            continue

        try:
            convert(src, out_bin)
            gc, tc, vc = read_bin_stats(out_bin)
            manifest[key] = {'g': gc, 'i32': False, 't': tc, 'v': vc}
            print(f'  OK  {key}: gc={gc} tc={tc} vc={vc}')
            converted += 1
        except Exception as e:
            print(f'  ERR {key}: {e}')
            errors += 1

    with open(MANIFEST, 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)

    print(f'\nDone: {converted} converted, {errors} errors')
    print(f'Manifest updated: {MANIFEST}')


if __name__ == '__main__':
    main()
