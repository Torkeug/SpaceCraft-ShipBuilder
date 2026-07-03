"""
hbson_parse.py -- Parser for Heaps' HBSON binary format (.prefab files).

Faithful port of the real engine source:
  hxd/fmt/hbson/Reader.hx / Writer.hx (HeapsIO/heaps on GitHub)

Format:
    Header: "HBSON" (5 bytes) + 1 byte (0x00) = 6 bytes total, then a single
    recursively-encoded value follows (see `read_value`).

    Tag byte meanings (from Writer.hx's writeRec):
        0  -> int 0
        1  -> int, next byte is the value (0..255)
        2  -> int, next 4 bytes (BE? see note) is a full Int32
        3  -> float, next 8 bytes is a Double
        4  -> bool true
        5  -> bool false
        6  -> null
        7  -> empty object {}
        8  -> object, next byte is field count, then (name,value) pairs
        9  -> object, next 4 bytes is field count (for large objects)
        10 -> string (see read_string)
        11 -> empty array []
        12 -> array, next byte is element count
        13 -> array, next 4 bytes is element count (for large arrays)

    Strings use a per-file backreference table (NOT a global constant pool):
    read_string() reads an Int32 index; if the top 2 bits are set, this is a
    fresh string definition (bits 0x3FFFFFFF = byte length of the ASCII/UTF8
    string that follows); if bit 0x40000000 specifically is set the string is
    also pushed onto the local table for later back-reference by plain index
    (0x80000000-flagged "long" strings are NOT added to the table). If neither
    top bit is set, the whole Int32 is a plain index into that local table.

Haxe's haxe.io.Input defaults to *little-endian* (bigEndian=false), and the
game's own Bytes are written the same way (Writer.hx never sets bigEndian),
so integers/doubles below are read as little-endian.
"""

import struct


class HBSONReader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.pos = offset + 6  # skip "HBSON" + 1 pad byte
        self.string_tbl = []

    def _u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def _i32(self):
        v = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def _u32(self):
        v = struct.unpack_from('<I', self.data, self.pos)[0]
        self.pos += 4
        return v

    def _double(self):
        v = struct.unpack_from('<d', self.data, self.pos)[0]
        self.pos += 8
        return v

    def read_string(self):
        index = self._u32()
        if index & 0xC0000000:
            strlen = index & 0x3FFFFFFF
            s = self.data[self.pos:self.pos + strlen].decode('utf-8', errors='replace')
            self.pos += strlen
            if index & 0x40000000:
                self.string_tbl.append(s)
            return s
        else:
            return self.string_tbl[index]

    def read(self):
        code = self._u8()
        if code == 0:
            return 0
        if code == 1:
            return self._u8()
        if code == 2:
            return self._i32()
        if code == 3:
            return self._double()
        if code == 4:
            return True
        if code == 5:
            return False
        if code == 6:
            return None
        if code == 7:
            return {}
        if code == 8 or code == 9:
            n = self._u8() if code == 8 else self._u32()
            obj = {}
            for _ in range(n):
                name = self.read_string()
                obj[name] = self.read()
            return obj
        if code == 10:
            return self.read_string()
        if code == 11:
            return []
        if code == 12 or code == 13:
            n = self._u8() if code == 12 else self._u32()
            return [self.read() for _ in range(n)]
        raise ValueError(f'Unknown HBSON tag {code} at pos {self.pos - 1}')


def parse_prefab(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:5] != b'HBSON':
        raise ValueError(f'{path}: not an HBSON file (magic={data[:5]!r})')
    return HBSONReader(data).read()


def main():
    import sys
    import json
    for path in sys.argv[1:]:
        print(f"=== {path} ===")
        try:
            obj = parse_prefab(path)
            print(json.dumps(obj, indent=2, default=str)[:4000])
        except Exception as e:
            print(f'  ERROR: {e}')


if __name__ == '__main__':
    main()
