"""
Verification pass for pak_extract.py's disc=0x02 position formula.

Confirms (via magic-byte signature checks) that every disc=0x02 entry whose
type we can validate -- .fbx (HMD magic), .prefab (HBSON magic) -- resolves
to a genuinely valid header at the position PakReader.list_files() computes.

This exists to guard against ever regressing back to the old cumulative-sum
approximation (see finding 17 in tools/hmd_format_notes.md): that formula
looked correct for many files (small, ~0-30 byte error) but was badly wrong
for others (SpaceStation, prefabs, ui/icons -- errors from hundreds to tens
of thousands of bytes). The current formula (stored_pos + dir_size, read
directly from each entry) has zero known failures.

Run: python tools/pak_verify_positions.py
"""
import sys
sys.path.insert(0, r'd:\Documents\Spacecraft\tools')
from pak_extract import PakReader, PAK_PATH


def validate(f, path, pos):
    lower = path.lower()
    if lower.endswith('.prefab'):
        f.seek(pos)
        return f.read(5) == b'HBSON'
    if lower.endswith('.fbx'):
        f.seek(pos)
        return f.read(3) == b'HMD'
    if lower.endswith('.png') and lower.startswith('ui/icons/'):
        # Only ui/icons/*.png are confirmed real standard PNGs; most other
        # .png-named entries in this pak (assets/fx, materials, etc.) are a
        # custom compressed GPU texture format and legitimately don't start
        # with the PNG magic -- that's a format fact, not a position bug.
        f.seek(pos)
        return f.read(8) == b'\x89PNG\r\n\x1a\n'
    return None


def main():
    r = PakReader(PAK_PATH)
    f = r.f
    files = r.list_files()
    d02 = [(p, pos, sz) for p, pos, sz, is_d02 in files if is_d02]

    checked = 0
    failures = []
    for path, pos, size in d02:
        ok = validate(f, path, pos)
        if ok is not None:
            checked += 1
            if not ok:
                failures.append((path, pos, size))

    print(f'disc=0x02 entries: {len(d02)}')
    print(f'checked {checked} validatable entries (.fbx/.prefab/.png)')
    print(f'failures: {len(failures)}')
    for path, pos, size in failures[:50]:
        print(f'  pos={pos} size={size}  {path}')
    return failures


if __name__ == '__main__':
    main()
