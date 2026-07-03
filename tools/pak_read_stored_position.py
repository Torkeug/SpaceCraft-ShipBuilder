"""
Diagnostic: read the disc=0x02 payload's first 8 bytes as a little-endian
double and compare against known-correct absolute file positions, to test
the hypothesis (surfaced via hlbc decompilation of the real compiled game
class hxd.fmt.pak.Reader.readFile) that each entry stores its OWN absolute
data position directly, rather than positions needing to be computed via
a cumulative running sum from a fixed base (as pak_extract.py currently
assumes for disc=0x02 entries).

Run: python tools/pak_read_stored_position.py
"""
import struct
import sys
sys.path.insert(0, r'd:\Documents\Spacecraft\tools')
from pak_extract import PakReader, D02_DATA_START, PAK_PATH


def walk_collect(r, f, count, path_parts, targets, found):
    for _ in range(count):
        name_len = r._u8()
        name = f.read(name_len).decode('utf-8', errors='replace')
        d = r._u8()
        cp = path_parts + [name]
        path = '/'.join(cp)
        if d == 0x01:
            c = r._u32()
            walk_collect(r, f, c, cp, targets, found)
        elif d == 0x00:
            f.read(12)
        elif d == 0x02:
            raw = f.read(16)
            if path in targets:
                found[path] = raw


def main():
    r = PakReader(PAK_PATH)
    f = r.f
    f.seek(13)
    disc = r._u8()
    root_count = r._u32()

    # Known-good absolute positions, confirmed earlier this investigation by
    # direct byte inspection (HMD/HBSON magic found at the expected in-file
    # offset for that content type).
    known_good = {
        'assets/Vehicules/Buildings_Parts/Tools/Gravitron.fbx': 14393177552,
        'assets/Vehicules/Buildings_Parts/Main_Structures/12x6x2/12x6x2_A.fbx': 14218792576,
        'assets/Vehicules/Buildings_Parts/Main_Structures/16x6x2/16x6x2_A.fbx': 14221346528,
    }

    found = {}
    walk_collect(r, f, root_count, [], set(known_good), found)

    print(f'{"path":75s} {"stored_double":>18s} {"known_real_pos":>15s} {"delta":>12s}')
    for path, real_pos in known_good.items():
        raw = found.get(path)
        if raw is None:
            print(f'{path:75s}  NOT FOUND')
            continue
        stored = struct.unpack('<d', raw[:8])[0]
        print(f'{path:75s} {stored:>18.1f} {real_pos:>15d} {real_pos - stored:>12.1f}')


if __name__ == '__main__':
    main()
