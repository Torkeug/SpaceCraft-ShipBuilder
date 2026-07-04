"""
extract_ship_stats.py -- Rebuild shipbuilder/ship_editor_data.json's per-part
`stats` dicts from the game's own data.cdb, and dump the ship-stat formula
constants and attribute display names/units the ship builder needs.

Why: ship_editor_data.json's `stats` field was populated by an earlier,
undocumented, one-off process and only ever carries 23 distinct keys across
all 142 parts -- entirely missing real attributes that exist in data.cdb for
batteries (BatteryChargeSpeed/Efficiency/Wastage), heaters/engines
(HeatGeneration/HeatDissipation), shields/weapons (ShieldMaxCharge,
DamageNegation, Damage, ...), decoration (DecoPointsCost), and the ship-points
penalty (SystemMalusForShipPoints). This tool re-derives every part's stats
directly from data.cdb's `item.attributes` list, for every part kind (hull,
inside modules, outside modules/engines/cockpits alike), so nothing needed by
the real in-game ship-stats formulas (see src/logic/ShipStats.hx, decompiled
via hlbc -- tools/heaps_ref/hlbc_src) is missing or guessed.

The formula constants (ShipStatIntegrityFactor, ShipPointsCoeff, etc.) and
attribute display names/units are also real data.cdb content (the `constant`
and `attribute` sheets), dumped here so the ship-builder JS reads real game
text/values instead of hand-typed copies.

Usage:
    python tools/extract_ship_stats.py
    python tools/extract_ship_stats.py --cdb path/to/data.cdb   # skip pak read
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pak_extract import PakReader, PAK_PATH

SHIPBUILDER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shipbuilder')
EDITOR_DATA_PATH = os.path.join(SHIPBUILDER_DIR, 'ship_editor_data.json')
CONSTANTS_OUT_PATH = os.path.join(SHIPBUILDER_DIR, 'ship_constants.json')
ATTRIBUTES_OUT_PATH = os.path.join(SHIPBUILDER_DIR, 'ship_attributes.json')

# Every $Const.* referenced by logic.ShipStats.calcStats()/getPointsValue(),
# per the hlbc decompile + raw opcode disassembly (see the plan/session notes
# in tools/hmd_format_notes.md for how these were found and verified).
SHIP_STAT_CONSTANTS = [
    'ShipStatIntegrityFactor', 'ShipStatMinIntegrity', 'ShipStatMinHullRequirement',
    'ShipStatSysSupportNormalization', 'ShipStatSysSupportScale',
    'ShipStatHeatInterfaceScale', 'ShipStatHeatInterfacePower',
    'ShipPointsCoeff', 'ShipPointsHullCoeff', 'ShipPointsSupportCoeff',
    'ShipDecoSupportCoeff',
    'ShipManeuvrabilityScale', 'ShipManeuvrabilityWeightPower',
    'ShipStatMultiThrustScale', 'ShipStatMultiBoostThrustScale',
    'ShipStatAccelFactorPower', 'ShipStatAccelFactorMult',
    'ShipStatSpeedFactorScale', 'ShipStatSpeedFactorThrustScale', 'ShipStatSpeedFactorForceScale',
    'ShipStatBoostSpeedFactorScale', 'ShipStatBoostSpeedFactorThrustScale',
    'ShipStatSpaceSpeedFactor', 'ShipStatSpaceSpeedPower',
    'ShipStatDefaultSpaceSpeed', 'ShipStatSpaceBoostSpeedPower', 'ShipStatDefaultSpaceBoostSpeed',
    'ShipStatOrbitSpeedFactor', 'ShipStatOrbitSpeedPower',
    'ShipStatDefaultOrbitSpeed', 'ShipStatOrbitBoostSpeedPower', 'ShipStatDefaultOrbitBoostSpeed',
    'ShipStatLiquidSpeedFactor', 'ShipStatLiquidSpeedPower',
    'ShipStatDefaultLiquidSpeed', 'ShipStatLiquidBoostSpeedPower', 'ShipStatDefaultLiquidBoostSpeed',
    'ShipStatCombatSpeedFactor', 'ShipStatCombatSpeedPower',
    'ShipStatDefaultCombatSpeed', 'ShipStatCombatBoostSpeedPower', 'ShipStatDefaultCombatBoostSpeed',
]

# Extra computed/display attribute ids that aren't real data.cdb attribute
# ids on any item (HeatInterfaceShip, SystemEfficiency, CurrentHull_Display
# are all real attribute-sheet entries actually, kept here only as
# documentation of which ones matter to the ship-wide formulas above).
SHIP_FORMULA_ATTRIBUTE_IDS = [
    'Frame', 'ShipWeight', 'Hull', 'CurrentHull_Display', 'Integrity',
    'SystemSupport', 'SystemRequirement', 'SystemMalusForShipPoints', 'SystemEfficiency',
    'HeatCapacity', 'HeatInterfaceMaterial', 'HeatInterfaceParts', 'HeatInterfaceShip',
    'PowerProduction', 'PowerStorage', 'PowerUsage', 'EngineConsumption', 'BoostConsumption',
    'BatteryChargeSpeed', 'BatteryEfficiency', 'BatteryWastage',
    'MaxSpeed', 'MaxBoostSpeed', 'Maneuvrability', 'AccelerationTime',
    'EngineForce', 'EngineThrust', 'BoostThrust', 'SteeringStrength',
    'SolidStorage', 'FluidStorage', 'FTOilStorage', 'FakeFTLOptimalMaxWeight',
    'ShipDecoSupport', 'DecoPointsCost', 'MaxDecoPoints',
]


def load_cdb(cdb_path=None, pak_path=PAK_PATH):
    if cdb_path and os.path.exists(cdb_path):
        with open(cdb_path, encoding='utf-8') as f:
            return json.load(f)
    reader = PakReader(pak_path)
    for path, pos, size, is_d02 in reader.list_files():
        if path == 'data.cdb':
            reader.f.seek(pos)
            data = reader.f.read(size)
            return json.loads(data)
    raise FileNotFoundError('data.cdb not found in pak')


def sheet(cdb, name):
    return next(s for s in cdb['sheets'] if s['name'] == name)['lines']


def build_constants(cdb):
    consts = {c['id']: c['val'] for c in sheet(cdb, 'constant')}
    out = {}
    missing = []
    for cid in SHIP_STAT_CONSTANTS:
        if cid not in consts:
            missing.append(cid)
            continue
        val = consts[cid]
        # constant@val is a typed union ({'float': x} / {'int': x} / ...); every
        # constant we care about here is a plain scalar.
        out[cid] = next(iter(val.values()))
    if missing:
        print(f'  WARN: {len(missing)} constants not found in data.cdb: {missing}')
    return out


def build_attributes(cdb):
    # Dump every real attribute id/name/unit, not just the ship-formula subset
    # (SHIP_FORMULA_ATTRIBUTE_IDS above) -- parts can carry any of the game's
    # attributes (weapon, farming, corpo, ...) and the per-part inspector
    # should be able to label whichever ones actually show up.
    #
    # A handful of attribute ids (e.g. NoNaturalHeatDissipationOnNotColdEnvironement)
    # have no 'name' at all in data.cdb -- they're internal engine flags the
    # real game never surfaces to players either, so they're skipped here
    # rather than falling back to showing the raw id.
    out = {}
    skipped = 0
    for a in sheet(cdb, 'attribute'):
        if 'name' not in a:
            skipped += 1
            continue
        out[a['id']] = {'name': a['name'], 'unit': a.get('unit') or None}
    print(f'  Skipped {skipped} nameless/internal attribute ids (not real player-facing stats).')
    missing = [aid for aid in SHIP_FORMULA_ATTRIBUTE_IDS if aid not in out]
    if missing:
        print(f'  WARN: {len(missing)} ship-formula attributes not found in data.cdb: {missing}')
    return out


def rebuild_part_stats(cdb, editor_data):
    items = {i['id']: i for i in sheet(cdb, 'item')}
    parts = editor_data['parts']
    updated, unmatched, with_skills = 0, [], 0
    for part in parts:
        item = items.get(part['id'])
        if item is None:
            unmatched.append(part['id'])
            continue
        new_stats = {}
        for attr in item.get('attributes', []):
            new_stats[attr['attr']] = attr['value']
        part['stats'] = new_stats

        # Per-action stats (PowerUsage, ActiveHeatGeneration, Damage,
        # MiningPower/Tier, RadarRange, ShieldMaxCharge/DamageNegation, ...)
        # live under item.props.skills[], keyed by skill/mode (e.g. a mining
        # laser has separate "Mine" and "MinerAttack" skills with different
        # power/heat costs) -- NOT in the flat item.attributes list above.
        # Kept as a separate per-skill structure rather than flattened into
        # `stats`, since the same attr id (e.g. PowerUsage) can carry a
        # different value per skill/mode on the same item.
        skills = item.get('props', {}).get('skills') or []
        new_skills = [
            {'skill': sk['skill'], 'stats': {a['attr']: a['value'] for a in sk.get('attributes', [])}}
            for sk in skills if sk.get('attributes')
        ]
        if new_skills:
            part['skills'] = new_skills
            with_skills += 1
        elif 'skills' in part:
            del part['skills']
        updated += 1
    print(f'  {with_skills} parts carry per-skill stats (props.skills).')

    editor_ids = {p['id'] for p in parts}
    ship_types = {
        t['id'] for t in sheet(cdb, 'itemType')
        if t.get('id', '').startswith(('ShipHull_', 'ShipEngine', 'ShipModule', 'ShipTool'))
    }
    uncatalogued = [
        i['id'] for i in sheet(cdb, 'item')
        if i.get('type') in ship_types and i['id'] not in editor_ids
    ]
    return updated, unmatched, uncatalogued


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cdb', default=None, help='Pre-extracted data.cdb path (otherwise reads fresh from the pak)')
    ap.add_argument('--pak', default=PAK_PATH)
    args = ap.parse_args()

    print('Loading data.cdb...')
    cdb = load_cdb(args.cdb, args.pak)

    with open(EDITOR_DATA_PATH, encoding='utf-8') as f:
        editor_data = json.load(f)

    print('Rebuilding per-part stats from item.attributes...')
    updated, unmatched, uncatalogued = rebuild_part_stats(cdb, editor_data)
    print(f'  Updated {updated} parts.')
    if unmatched:
        print(f'  WARN: {len(unmatched)} catalogue parts have no data.cdb item match: {unmatched}')
    if uncatalogued:
        print(f'  NOTE: {len(uncatalogued)} ship-type data.cdb items are not in the catalogue at all: {uncatalogued}')

    with open(EDITOR_DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(editor_data, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f'  Wrote {EDITOR_DATA_PATH}')

    print('Building ship_constants.json...')
    constants = build_constants(cdb)
    with open(CONSTANTS_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(constants, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f'  Wrote {CONSTANTS_OUT_PATH} ({len(constants)} constants)')

    print('Building ship_attributes.json...')
    attributes = build_attributes(cdb)
    with open(ATTRIBUTES_OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(attributes, f, indent=2, ensure_ascii=False)
        f.write('\n')
    print(f'  Wrote {ATTRIBUTES_OUT_PATH} ({len(attributes)} attributes)')


if __name__ == '__main__':
    main()
