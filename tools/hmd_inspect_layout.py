"""
hmd_inspect_layout.py — Read raw bytes at key positions in HMD files to confirm layout.

Usage:
    python hmd_inspect_layout.py <file.fbx>
"""

import struct, sys, os

# Known parameters for cross-checking
KNOWN = {
    '4x3x1_G.fbx': {
        'lod_desc_end': 824,
        'vbuf0_start': 825, 'vc0': 1434, 'ic0': 3606,
        'ibuf0_start': 46710,
        'vbuf1_start': 53925, 'vc1': 966, 'ic1': 1872,
        'ibuf1_start': 84834,
        'vbuf2_start': 88578, 'vc2': 736,
    },
}


def hexdump(data, base_offset, n=64):
    """Print hex + ASCII of n bytes starting at base_offset."""
    chunk = data[base_offset:base_offset+n]
    for row in range(0, len(chunk), 16):
        b = chunk[row:row+16]
        hex_part = ' '.join(f'{x:02x}' for x in b)
        asc_part = ''.join(chr(x) if 32 <= x < 127 else '.' for x in b)
        print(f"  {base_offset+row:6d}: {hex_part:<48s}  {asc_part}")


def read_be_u16(data, off):
    return struct.unpack_from('>H', data, off)[0]


def read_le_f32(data, off):
    return struct.unpack_from('<f', data, off)[0]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    basename = os.path.basename(path)
    known = KNOWN.get(basename, {})

    with open(path, 'rb') as f:
        data = f.read()

    print(f"File: {basename}  ({len(data)} bytes)")

    # 1. File header
    print("\n=== File header (bytes 0..11) ===")
    hexdump(data, 0, 12)
    if len(data) >= 4:
        h23 = struct.unpack_from('<H', data, 2)[0]
        print(f"  header[2-3] = {h23} (= LOD0_vc-1? => vc0 = {h23+1})")

    # 2. Boundary around expected ibuf0 start (ibuf0 = vbuf0_start + vc0*32)
    vbuf0 = known.get('vbuf0_start', 0)
    vc0 = known.get('vc0', 0)
    stride = 32
    ibuf0_computed = vbuf0 + vc0 * stride
    ibuf0_known = known.get('ibuf0_start', ibuf0_computed)

    print(f"\n=== vbuf0/ibuf0 boundary (around byte {ibuf0_computed}) ===")
    region_start = max(0, ibuf0_computed - 16)
    hexdump(data, region_start, 64)

    # Show first 10 BE u16 values from various ibuf start candidates
    for candidate_name, candidate in [
        (f"computed ({ibuf0_computed})", ibuf0_computed),
        (f"known ({ibuf0_known})", ibuf0_known),
        (f"computed-3 ({ibuf0_computed-3})", ibuf0_computed-3),
    ]:
        print(f"\n  First 10 indices if ibuf starts at {candidate_name}:")
        for i in range(10):
            off = candidate + i * 2
            if off + 2 <= len(data):
                v = read_be_u16(data, off)
                print(f"    [{i}] = {v}")

    # 3. Show first 3 and last 3 vertices in LOD0 vbuf
    print(f"\n=== LOD0 vbuf first 3 vertices (at {vbuf0}) ===")
    for i in range(3):
        off = vbuf0 + i * stride
        x, y, z = struct.unpack_from('<3f', data, off)
        print(f"  vert[{i}]: x={x:.4f} y={y:.4f} z={z:.4f}  raw={data[off:off+12].hex()}")

    if vc0 > 0:
        print(f"=== LOD0 vbuf last 3 vertices ===")
        for i in range(vc0-3, vc0):
            off = vbuf0 + i * stride
            x, y, z = struct.unpack_from('<3f', data, off)
            print(f"  vert[{i}]: x={x:.4f} y={y:.4f} z={z:.4f}  raw={data[off:off+12].hex()}")

    # 4. LOD descriptor section — scan for vc0 and ic0
    print(f"\n=== LOD descriptor scan for vc0={vc0}, ic0={known.get('ic0',0)} ===")
    lod_desc_start = 535
    lod_desc_end = known.get('lod_desc_end', 824)
    ic0 = known.get('ic0', 0)
    for v, label in [(vc0, 'vc0'), (ic0, 'ic0'), (vbuf0, 'vbuf0'), (ibuf0_known, 'ibuf0')]:
        for fmt, name in [('<H','>H','LE u16','BE u16'), ('<I','>I','LE u32','BE u32')]:
            pass  # simplified below
        for fmt, fname in [('<H','LE u16'), ('>H','BE u16'), ('<I','LE u32'), ('>I','BE u32')]:
            try:
                packed = struct.pack(fmt, v)
            except struct.error:
                continue
            idx = lod_desc_start
            while True:
                idx = data.find(packed, idx, lod_desc_end+1)
                if idx == -1:
                    break
                print(f"  {label}={v} ({fname}) at byte {idx}")
                idx += 1

    # 5. Show LOD0 descriptor meta bytes
    lod0_meta_start = 568
    print(f"\n=== LOD0 descriptor meta (bytes {lod0_meta_start}..{lod0_meta_start+76}) ===")
    hexdump(data, lod0_meta_start, 80)
    # Interpret as LE u32s
    for i in range(0, 76, 4):
        v = struct.unpack_from('<I', data, lod0_meta_start + i)[0]
        fv = struct.unpack_from('<f', data, lod0_meta_start + i)[0]
        matches = []
        for k, kv in known.items():
            if isinstance(kv, int) and v == kv:
                matches.append(k)
        tag = '  <-- ' + ', '.join(matches) if matches else ''
        print(f"  meta[{i:2d}]: u32={v:10d}  float={fv:12.4f}{tag}")

    # 6. Extra block[0] hypothesis
    print(f"\n=== ic/vc derivation from extra[0] hypothesis ===")
    print("  (Requires attribute blocks parsed separately via hmd_analyze_extras.py)")
    # For G: block0_extra[0]=0, block1_extra[0]=53068, block2_extra[0]=87724
    # vc0 from header: 1434. Stride=32.
    if basename == '4x3x1_G.fbx':
        e0, e1, e2 = 0, 53068, 87724
        vc0_h = 1434  # from header
        vc1_h = 966   # from block0 extra[46-47]
        vc2_h = 736   # from block1 extra[46-47]
        ic0_derived = (e1 - (vc0_h - 1) * 32) // 2
        ic1_derived = (e2 - e1 - vc1_h * 32) // 2
        print(f"  G: vc0={vc0_h}, e0={e0}, e1={e1}, e2={e2}")
        print(f"  ic0 = (e1 - (vc0-1)*32) / 2 = ({e1} - {(vc0_h-1)*32}) / 2 = {ic0_derived}")
        print(f"  ic1 = (e2 - e1 - vc1*32) / 2 = ({e2} - {e1} - {vc1_h*32}) / 2 = {ic1_derived}")
        print(f"  Actual ic0={ic0}, ic1={known.get('ic1',0)}")

        # vbuf offsets
        vbuf0_start = 825  # = lod_desc_end + 1
        ibuf0_start = vbuf0_start + vc0_h * 32
        vbuf1_start = ibuf0_start + ic0_derived * 2
        ibuf1_start = vbuf1_start + vc1_h * 32
        vbuf2_start = ibuf1_start + ic1_derived * 2

        print(f"\n  Computed layout:")
        print(f"  vbuf0: {vbuf0_start}  (actual: {vbuf0})")
        print(f"  ibuf0: {ibuf0_start}  (actual: {ibuf0_known})")
        print(f"  vbuf1: {vbuf1_start}  (actual: {known.get('vbuf1_start')})")
        print(f"  ibuf1: {ibuf1_start}  (actual: {known.get('ibuf1_start')})")
        print(f"  vbuf2: {vbuf2_start}  (actual: {known.get('vbuf2_start')})")


if __name__ == '__main__':
    main()
