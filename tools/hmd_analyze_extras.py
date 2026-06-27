"""
hmd_analyze_extras.py — Search for known offset/count values within HMD extra bytes.

For each known LOD geometry parameter (vc, ic, vbuf_start, ibuf_start), search the
entire file as LE/BE uint16/uint32, then cross-check against the 50-byte extra sections
in each LOD attribute block.

Usage:
    python hmd_analyze_extras.py <file.fbx> [<file.fbx> ...]
"""

import struct
import sys
import os

KNOWN = {
    '4x3x1_G.fbx': {
        'lod0_vbuf_start': 825, 'lod0_vc': 1434, 'lod0_ibuf_start': 46710, 'lod0_ic': 3606,
        'lod1_vbuf_start': 53925, 'lod1_vc': 966, 'lod1_ibuf_start': 84834, 'lod1_ic': 1872,
    },
    '4x3x1_A.fbx': {
        'lod0_vbuf_start': 1039, 'lod0_vc': 1280, 'lod0_ibuf_start': 248884, 'lod0_ic': 3141,
        'lod1_vbuf_start': 41999, 'lod1_vc': 1024, 'lod1_ibuf_start': 293288, 'lod1_ic': 2565,
    },
}


def find_value(data, value, label):
    results = []
    for fmt, size, name in [('<H',2,'LE u16'), ('>H',2,'BE u16'), ('<I',4,'LE u32'), ('>I',4,'BE u32')]:
        try:
            packed = struct.pack(fmt, value)
        except struct.error:
            continue
        pos = 0
        while True:
            idx = data.find(packed, pos)
            if idx == -1:
                break
            results.append((idx, name))
            pos = idx + 1
    if results:
        shown = ', '.join(f"{r[0]} ({r[1]})" for r in results[:6])
        print(f"  {label:30s} = {value:8d} (0x{value:05X}):  {shown}")
    else:
        print(f"  {label:30s} = {value:8d} (0x{value:05X}):  NOT FOUND")
    return results


def parse_attr_block(data, start):
    """
    Parse LOD attribute block at `start`. Returns (end, extra_bytes, attr_list).
    """
    off = start
    if data[off] != 0x0b:
        return None, None, None
    off += 1
    attr_count = data[off]
    off += 1
    attrs = []
    for _ in range(attr_count):
        name_len = data[off]
        off += 1
        name = data[off:off+name_len].decode('ascii', errors='replace')
        off += name_len
        type_byte = data[off]
        off += 1
        attrs.append((name, type_byte))
    extra_start = off
    extra = data[extra_start:extra_start + 50]
    end = extra_start + 50
    return end, extra, attrs


def analyze(path):
    basename = os.path.basename(path)
    known = KNOWN.get(basename, {})

    with open(path, 'rb') as f:
        data = f.read()

    print(f"\n{'='*60}")
    print(f"File: {basename}  ({len(data)} bytes)")

    # Print file header
    print(f"\nFile header (first 8 bytes): {data[:8].hex()}")

    # Scan attribute blocks (first 400 bytes)
    print(f"\n--- Attribute blocks ---")
    blocks = []
    off = 0
    while off < 400:
        if data[off] == 0x0b:
            end, extra, attrs = parse_attr_block(data, off)
            if extra is not None and len(extra) == 50:
                attr_desc = ', '.join(f"{n}({t:02x})" for n,t in attrs)
                print(f"  Block {len(blocks)} @ {off}..{end-1}: attrs=[{attr_desc}]  extra_start={end-50}")
                blocks.append((off, end, extra, attrs))
                off = end
                continue
        off += 1

    print(f"  Total blocks: {len(blocks)}")

    # Print extra bytes per block with full analysis
    print(f"\n--- Extra bytes analysis ---")
    for bi, (start, end, extra, attrs) in enumerate(blocks):
        extra_start = end - 50
        print(f"\n  Block {bi} (extra at file bytes {extra_start}..{end-1}):")
        print(f"    hex: {extra.hex()}")
        # Per-byte
        print(f"    bytes: {' '.join(f'{b:02x}' for b in extra)}")
        # u32 fields
        for i in range(0, 48, 4):
            v32 = struct.unpack_from('<I', extra, i)[0]
            # u16 pairs
            v16a = struct.unpack_from('<H', extra, i)[0]
            v16b = struct.unpack_from('<H', extra, i+2)[0]
            tags = []
            for k, kv in known.items():
                if v32 == kv: tags.append(f"={k}")
                if v16a == kv: tags.append(f"[{i}]u16={k}")
                if v16b == kv: tags.append(f"[{i+2}]u16={k}")
            print(f"    [{i:2d}]: u32={v32:10d}  u16=[{v16a:5d},{v16b:5d}]  {'  '.join(tags)}")
        # Last 2 bytes
        v16 = struct.unpack_from('<H', extra, 48)[0]
        print(f"    [48]: u16={v16}")

    # Search for known values
    if known:
        print(f"\n--- Known value search ---")
        for label, value in sorted(known.items()):
            find_value(data, value, label)

    # Check offset-0 hypothesis:
    # block[N][0] = sum of (vc_i - 1) * stride + ic_i * 2 for i in 0..N-1
    print(f"\n--- Offset[0] hypothesis test ---")
    if len(blocks) >= 2 and known:
        vc0 = known.get('lod0_vc', 0)
        ic0 = known.get('lod0_ic', 0)
        stride = 32
        # For G: formula (vc0-1)*stride + ic0*2
        h1 = (vc0 - 1) * stride + ic0 * 2
        # For G: formula vc0*stride + ic0*2
        h2 = vc0 * stride + ic0 * 2
        b1_v = struct.unpack_from('<I', blocks[1][2], 0)[0]
        print(f"  Block 1 [0] = {b1_v}")
        print(f"  (vc0-1)*stride + ic0*2 = {h1}  {'✓' if h1==b1_v else '✗'}")
        print(f"  vc0*stride + ic0*2     = {h2}  {'✓' if h2==b1_v else '✗'}")

        if len(blocks) >= 3:
            vc1 = known.get('lod1_vc', 0)
            ic1 = known.get('lod1_ic', 0)
            vs = known.get('lod1_vbuf_start', 0)
            vbuf0_start = known.get('lod0_vbuf_start', 0)
            gap = vs - (vbuf0_start + h1 + vc0) if vs else 0
            b2_v = struct.unpack_from('<I', blocks[2][2], 0)[0]
            h3 = h1 + vc1 * stride + ic1 * 2
            h4 = h1 + (vc1 - 1) * stride + ic1 * 2
            h5 = h2 + vc1 * stride + ic1 * 2
            print(f"  Block 2 [0] = {b2_v}")
            print(f"  h1 + vc1*stride + ic1*2     = {h3}  {'✓' if h3==b2_v else '✗'}")
            print(f"  h1 + (vc1-1)*stride + ic1*2 = {h4}  {'✓' if h4==b2_v else '✗'}")
            print(f"  h2 + vc1*stride + ic1*2     = {h5}  {'✓' if h5==b2_v else '✗'}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for path in sys.argv[1:]:
        analyze(path)
