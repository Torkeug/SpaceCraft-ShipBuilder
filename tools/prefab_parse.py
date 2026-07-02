"""
prefab_parse.py — Exploratory tokenizer/parser for SpaceCraft's .prefab binary format.

The format ("HBSON" per an embedded magic string seen in some files) is a custom
tagged binary serialization, structurally similar to a length-prefixed JSON:
dict-like objects with @-named string keys, arrays, strings, floats (doubles),
and plain 32-bit ints. This is NOT haxe.Serializer (ASCII-tagged) — it's fully
binary with 1-byte type tags per value/token.

Observed so far (from Spot_Light_01.prefab / RadarMK1.prefab, still being verified):
  String token:  3-byte LE length + 1-byte subtype + `length` bytes of UTF-8/ASCII
                 subtype 0x40 = short/interned string (names, keys)
                 subtype 0x80 = long string (paths)
  0x0A  — precedes a string token (roughly: "next value is a string")
  0x08  — object marker, followed by a 4-byte LE field count, then that many
          (key-string-token, value) pairs
  0x0C  — array marker, followed by a 4-byte LE element count
  0x04  — null / empty marker (no payload) OR precedes a nested object (unclear)
  0x03  — float64 marker, followed by 8 bytes (double, little-endian)

This script is a debugging aid: run it on a .prefab file to print an annotated,
best-effort token stream so the format can be refined iteratively. It is NOT a
finished/trustworthy parser yet — treat its output as a hypothesis to check
against known-good facts (e.g. an item's real in-game mesh), not as ground truth.

Usage:
    python tools/prefab_parse.py <file.prefab>
"""

import struct
import sys


def _read_string_token(data, off):
    """Try to read [3-byte len][1-byte subtype][len bytes] at off.
    Returns (value, subtype, next_off) or None if it doesn't look valid."""
    if off + 4 > len(data):
        return None
    length = data[off] | (data[off + 1] << 8) | (data[off + 2] << 16)
    subtype = data[off + 3]
    if length <= 0 or length > 4096:
        return None
    if off + 4 + length > len(data):
        return None
    payload = data[off + 4:off + 4 + length]
    # Require mostly-printable payload for this to count as a string.
    printable = sum(1 for b in payload if 0x20 <= b < 0x7F)
    if printable < length * 0.8:
        return None
    try:
        s = payload.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return s, subtype, off + 4 + length


def tokenize(data, start=0, end=None, indent=0, max_tokens=500):
    """Best-effort linear scan printing an annotated token stream."""
    if end is None:
        end = len(data)
    off = start
    count = 0
    pad = '  ' * indent
    while off < end and count < max_tokens:
        count += 1
        b = data[off]
        tok = _read_string_token(data, off)
        if tok is not None:
            s, subtype, next_off = tok
            print(f"{pad}[{off:5d}] STR   subtype=0x{subtype:02x} len={len(s):3d}  {s!r}")
            off = next_off
            continue
        if b == 0x03 and off + 9 <= end:
            val = struct.unpack_from('<d', data, off + 1)[0]
            print(f"{pad}[{off:5d}] F64   {val!r}")
            off += 9
            continue
        if b in (0x0A, 0x08, 0x0C, 0x04, 0x0B, 0x02, 0x05, 0x06, 0x07, 0x01):
            # Emit as a bare tag; if followed by a plausible 4-byte count, show it.
            if off + 5 <= end:
                maybe_count = struct.unpack_from('<i', data, off + 1)[0]
                print(f"{pad}[{off:5d}] TAG   0x{b:02x}  (next_i32={maybe_count})")
            else:
                print(f"{pad}[{off:5d}] TAG   0x{b:02x}")
            off += 1
            continue
        print(f"{pad}[{off:5d}] BYTE  0x{b:02x}")
        off += 1


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    data = open(sys.argv[1], 'rb').read()
    print(f"{sys.argv[1]}: {len(data)} bytes")
    tokenize(data)


if __name__ == '__main__':
    main()
