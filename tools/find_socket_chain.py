"""
find_socket_chain.py -- Find the REAL, ground-truth sub-part mount hierarchy
for a tool/module item, straight from its own .prefab's constraint data,
instead of guessing from bounding-box overlap.

Root cause this replaces: some source HMD files have a `parent` index for a
sub-part (e.g. "Receiver") that doesn't match what it's actually mounted to
(e.g. resolves to a "Base" LOD variant, skipping right past "Rotary" and
"Mining_Arm" entirely) -- confirmed on MiningTool1_OC.fbx by comparing
bounding boxes. A geometric bbox-overlap heuristic can catch the most
egregious cases but is a crude proxy and can't be trusted not to mis-fire on
a case that merely looks superficially similar.

The real answer already exists in the game's own data: every one of these
turret-rig items' .prefab defines a "constraint" object (e.g.
"BarrelConstraint") whose "target" field is a dotted socket path like
"MiningTool1_OC.Rotary.Mining_Arm.Receiver" -- the exact, authoritative
mount chain, straight from the source. Confirmed on MiningTool1_OC
("Rotary.Mining_Arm.Receiver"), ColdLaser ("Rotary.Mining_Arm.Reciever") and
MiningTool/Simple_Mining_Laser ("Rotary.Receiver", no Mining_Arm -- correctly
matching that this simpler rig has no separate Mining_Arm piece at all).

Lookup is keyed on the mesh's own .fbx *source path*, not the .prefab's file
name or the item id -- confirmed these can all disagree (Simple_Mining_Laser
the ITEM has prefab file "Simple_Mining_Laser.prefab", whose internal model
node is named "MiningTool", not "Simple_Mining_Laser"). The source fbx path
is unambiguous and always a >16-char string, so it reliably round-trips
through this project's `MODULE_SOURCES` mapping (tools/batch_convert_modules.py).

Usage (as module):
    from find_socket_chain import find_socket_chain
    chain = find_socket_chain('assets/Vehicules/Buildings_Parts/Tools/MiningTool1_OC.fbx',
                               known_names={'Base','Rotary','Mining_Arm','Receiver'})
    # -> ['Rotary', 'Mining_Arm', 'Receiver']  (or None if no constraint found,
    #    e.g. Radar.fbx's prefab has no constraints at all)

Usage (CLI):
    python tools/find_socket_chain.py <assets/relative/path.fbx>
"""

import os
import struct
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOLS_DIR)

from hbson_parse import HBSONReader

PAK_PATH = r'D:\SteamLibrary\steamapps\common\SpaceCraft\res.pak'
_CHUNK = 200 * 1024 * 1024


def _find_string_offsets(pak_path, name):
    """Find every place `name` appears as a fresh HBSON string literal (see
    hbson_parse.py's docstring for the tag format) -- short-ASCII strings
    (<=16 chars) are prefixed with len|0x40000000, longer ones with
    len|0x80000000. Scans the whole pak in bounded chunks so this works
    without loading 16GB at once."""
    name_b = name.encode('utf-8')
    flag = 0x40000000 if len(name_b) <= 16 and name.isascii() else 0x80000000
    sig = struct.pack('<I', len(name_b) | flag) + name_b
    found = []
    overlap = len(sig)
    with open(pak_path, 'rb') as f:
        pos = 0
        prev_tail = b''
        while True:
            chunk = f.read(_CHUNK)
            if not chunk:
                break
            buf = prev_tail + chunk
            base = pos - len(prev_tail)
            start = 0
            while True:
                idx = buf.find(sig, start)
                if idx == -1:
                    break
                found.append(base + idx)
                start = idx + 1
            pos += len(chunk)
            prev_tail = chunk[-overlap:]
    return found


def _nearest_hbson_start(pak_path, near_offset, window=3000):
    with open(pak_path, 'rb') as f:
        f.seek(max(0, near_offset - window))
        data = f.read(window * 2)
    idx = data.rfind(b'HBSON', 0, window)
    if idx < 0:
        return None
    return max(0, near_offset - window) + idx


def _parse_prefab_at(pak_path, start, size=4000):
    with open(pak_path, 'rb') as f:
        f.seek(start)
        data = f.read(size)
    return HBSONReader(data).read()


def _all_constraint_targets(node, acc):
    """Depth-first collect every {"type": "constraint", "target": ...}
    node's raw dotted target path (unstripped -- the leading segment isn't
    reliably the item's own name; see module docstring)."""
    if isinstance(node, dict):
        if node.get('type') == 'constraint' and node.get('target'):
            acc.append(node['target'].split('.'))
        for child in node.get('children', []) or []:
            _all_constraint_targets(child, acc)


def find_socket_chain(mesh_source_path, known_names=None, pak_path=PAK_PATH):
    """Return the real mount-point chain (list of part-name segments, root
    first) for a tool item, read from its own .prefab's constraint target(s)
    -- or None if the prefab has no constraint (e.g. Radar.fbx), or the mesh
    source path couldn't be located in any prefab in the pak.

    `mesh_source_path` is the mesh's own path exactly as it appears in the
    prefab's "source" field, e.g.
    "assets/Vehicules/Buildings_Parts/Tools/MiningTool1_OC.fbx".

    A prefab can have *multiple* constraints referencing the same socket at
    different, inconsistent depths (confirmed on MiningTool1_OC.prefab:
    "BarrelConstraint" targets "MiningTool1_OC.Rotary.Receiver" while
    "FlareConstraint" targets "MiningTool_Upgrade.Rotary.Mining_Arm.Receiver"
    -- a stale root name apparently copy-pasted from a template item, but a
    more complete/specific path). Pass `known_names` (the mesh's own real
    part-name prefixes, e.g. {'Base','Rotary','Mining_Arm','Receiver'}) to
    correctly strip whatever leading junk each target has (which varies) and
    pick the longest validated chain across every constraint found, rather
    than trusting only the first one encountered.
    """
    offsets = _find_string_offsets(pak_path, mesh_source_path)
    best = None
    for off in offsets:
        hbson_start = _nearest_hbson_start(pak_path, off)
        if hbson_start is None:
            continue
        try:
            obj = _parse_prefab_at(pak_path, hbson_start)
        except Exception:
            continue
        if obj.get('type') != 'prefab':
            continue
        model_child = next((c for c in obj.get('children', [])
                             if c.get('type') == 'model' and c.get('source') == mesh_source_path), None)
        if model_child is None:
            continue
        raw_targets = []
        _all_constraint_targets(obj, raw_targets)
        for parts in raw_targets:
            if known_names:
                # Strip leading segments until the first one is a real,
                # known part-name prefix (handles both the item's own name
                # and a stale copy-pasted different root -- see docstring).
                while parts and parts[0] not in known_names:
                    parts = parts[1:]
            elif parts and model_child.get('name') and parts[0] == model_child['name']:
                parts = parts[1:]
            if parts and (best is None or len(parts) > len(best)):
                best = parts
    return best


def find_prefab_path_for_item(item_id, cdb_path=None):
    """Look up an item's visual.model prefab path from data.cdb (the plain
    HBSON-wrapped-but-mostly-readable-JSON-text file already used elsewhere
    in this project -- see hmd_format_notes.md). Not needed by
    find_socket_chain itself (which keys on mesh source path instead) but
    kept as a convenience for other tools/callers."""
    if cdb_path is None:
        repo_root = os.path.dirname(TOOLS_DIR)
        cdb_path = os.path.join(repo_root, 'pak_out', 'data.cdb')
    with open(cdb_path, encoding='utf-8', errors='replace') as f:
        text = f.read()
    idx = text.find(f'"id": "{item_id}"')
    if idx < 0:
        return None
    chunk = text[idx:idx + 3000]
    mi = chunk.find('"model"')
    if mi < 0:
        return None
    start = chunk.find('"', mi + len('"model"') + 1) + 1
    end = chunk.find('"', start)
    return chunk[start:end]


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    chain = find_socket_chain(sys.argv[1])
    print(chain)


if __name__ == '__main__':
    main()
