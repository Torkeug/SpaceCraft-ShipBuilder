"""
Heaps.io res.pak extractor for SpaceCraft game assets.

Usage:
    python pak_extract.py                          # list all files
    python pak_extract.py --extract Ships          # extract files matching path pattern
    python pak_extract.py --extract .fbx           # extract all .fbx primary files
    python pak_extract.py --out D:/out --extract Ships/Hulls
    python pak_extract.py --extract Vehicules      # extract disc=0x02 production HMDs

PAK format (ground truth, confirmed via hlbc decompilation of the compiled
game's own hxd.fmt.pak.Reader.readHeader/readFile -- see finding 17 in
tools/hmd_format_notes.md for the full derivation):

  Header (13 bytes):
    magic(4)="PAK\0" + dir_size(int32 LE) + hash(int32) + pad(1)

  Directory tree (starts at byte 13, length=dir_size):
    Root node:  discriminator(1)=0x01 + count(int32) + count children
    Each child: name_len(1) + name(name_len bytes) + discriminator(1) + payload
      discriminator=0x01 → directory:  count(int32) + count children
      discriminator=0x00 → uncompressed file: pos(int32) + size(int32) + hash(int32)
      discriminator=0x02 → production file:   16 bytes = stored_pos(double, 8 bytes)
                            + size(int32) + hash(int32)

  Data section layout:
    disc=0x00 files: addressed by pos relative to data_offset (byte 13+dir_size)
    disc=0x02 files: each entry stores its OWN absolute data position directly
                     (as a double), NOT a cumulative/sequential offset. The real
                     absolute byte position in the PAK is:

                         real_pos = stored_pos + dir_size

                     (dir_size is the same header field already parsed at byte
                     4-7 -- the compiled reader calls it `headerSize`). This was
                     confirmed exactly (zero byte error) against multiple
                     independently-verified anchors spanning Main_Structures,
                     Tools, and ui/icons.

  An earlier version of this tool computed disc=0x02 positions by accumulating
  16-byte-aligned sizes from an empirically-fitted base constant
  (D02_BASE/D02_DRIFT). That formula was only ever an approximation -- it
  happened to track the true position closely wherever files were physically
  stored in the same order as the directory tree, but diverged badly (by
  anywhere from dozens to tens of thousands of bytes, non-monotonically)
  wherever real on-disk storage order differed from directory order --
  notably in Buildings/SpaceStation, prefabs/, and ui/icons/. That approach
  has been fully replaced by the exact stored-position formula above; do not
  reintroduce cumulative-sum position math for disc=0x02 entries.

  All extracted positions stored in results are ABSOLUTE byte offsets in the PAK file.
"""

import struct
import os
import argparse

PAK_PATH = r'D:\SteamLibrary\steamapps\common\SpaceCraft\res.pak'
HEADER_SIZE = 13  # magic(4) + dir_size(4) + hash(4) + pad(1)


class PakReader:
    def __init__(self, path):
        self.f = open(path, 'rb')
        magic = self.f.read(4)
        assert magic == b'PAK\x00', f'Bad magic: {magic!r}'
        self.dir_size = struct.unpack('<I', self.f.read(4))[0]
        self.f.read(5)  # hash(4) + pad(1)
        self.data_offset = HEADER_SIZE + self.dir_size

    def _u8(self):
        return struct.unpack('B', self.f.read(1))[0]

    def _u32(self):
        return struct.unpack('<I', self.f.read(4))[0]

    def _parse_children(self, count, path_parts, d00_results, d02_results):
        """Parse `count` named child nodes into d00_results and d02_results."""
        for _ in range(count):
            name_len = self._u8()
            name = self.f.read(name_len).decode('utf-8', errors='replace')
            disc = self._u8()
            child_path = path_parts + [name]

            if disc == 0x01:
                # Directory: recurse
                child_count = self._u32()
                self._parse_children(child_count, child_path, d00_results, d02_results)
            elif disc == 0x00:
                # Uncompressed file: pos relative to data_offset
                pos  = self._u32()
                size = self._u32()
                self.f.read(4)  # hash
                abs_pos = self.data_offset + pos
                d00_results.append(('/'.join(child_path), abs_pos, size))
            elif disc == 0x02:
                # Production file: stored_pos(double, 8 bytes) + size(4) + hash(4).
                # stored_pos is the file's own absolute position, NOT a cumulative
                # offset -- see the module docstring / finding 17 for how this was
                # confirmed against the compiled game's own pak reader.
                stored_pos = struct.unpack('<d', self.f.read(8))[0]
                size = self._u32()
                self.f.read(4)   # hash
                abs_pos = int(stored_pos) + self.dir_size
                d02_results.append(('/'.join(child_path), abs_pos, size))
            else:
                raise ValueError(
                    f'Unknown discriminator 0x{disc:02X} at offset '
                    f'{self.f.tell()-1} for {"/".join(child_path)!r}'
                )

    def list_files(self):
        """Parse the entire directory tree.

        Returns list of (path, abs_pos, size, is_d02) tuples.
        abs_pos is an absolute byte offset in the PAK file (valid for both disc types).
        is_d02 is True for disc=0x02 (production) files.
        """
        self.f.seek(HEADER_SIZE)
        disc = self._u8()
        assert disc == 0x01, f'Root must be directory, got 0x{disc:02X}'
        root_count = self._u32()

        d00_results = []   # (path, abs_pos, size)
        d02_results = []   # (path, abs_pos, size)

        self._parse_children(root_count, [], d00_results, d02_results)

        results = [(p, pos, sz, False) for p, pos, sz in d00_results]
        results.extend((p, pos, sz, True) for p, pos, sz in d02_results)
        return results

    def extract_file(self, abs_pos, size, out_path):
        """Read `size` bytes from absolute PAK position `abs_pos`; write to `out_path`."""
        self.f.seek(abs_pos)
        data = self.f.read(size)
        os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
        with open(out_path, 'wb') as fout:
            fout.write(data)
        return len(data)

    def close(self):
        self.f.close()


def main():
    ap = argparse.ArgumentParser(description='SpaceCraft res.pak extractor')
    ap.add_argument('--pak', default=PAK_PATH)
    ap.add_argument('--out', default='pak_out', help='Output root directory')
    ap.add_argument('--extract', metavar='PATTERN',
                    help='Extract files whose path contains PATTERN (case-insensitive)')
    ap.add_argument('--all', action='store_true', help='Extract every file in the pak')
    ap.add_argument('--list', action='store_true', help='List all files (no extraction)')
    ap.add_argument('--d02-only', action='store_true',
                    help='When listing, show only disc=0x02 (production) files')
    args = ap.parse_args()

    reader = PakReader(args.pak)
    print(f'PAK data section starts at byte {reader.data_offset:,}  '
          f'(dir tree: {reader.dir_size:,} bytes)')

    print('Parsing directory tree...')
    files = reader.list_files()
    d00_count = sum(1 for f in files if not f[3])
    d02_count = sum(1 for f in files if f[3])
    print(f'Found {len(files):,} file entries  ({d00_count:,} disc=0x00,  {d02_count:,} disc=0x02)')

    if args.list or not (args.extract or args.all):
        for path, abs_pos, size, is_d02 in sorted(files, key=lambda t: t[0]):
            if args.d02_only and not is_d02:
                continue
            flag = ' [D02]' if is_d02 else ''
            line = f'  {size:>12,}{flag}  {path}'
            print(line.encode('ascii', 'replace').decode())
        reader.close()
        return

    if args.all:
        matched = files
        print(f'Extracting all {len(matched):,} files')
    else:
        pattern = args.extract.lower()
        matched = [(p, pos, size, d02) for p, pos, size, d02 in files if pattern in p.lower()]
        print(f'Matched {len(matched):,} files for pattern {args.extract!r}')

    if not matched:
        reader.close()
        return

    extracted = errors = 0
    for path, abs_pos, size, is_d02 in matched:
        rel = path.replace('/', os.sep).lstrip(os.sep)
        out_path = os.path.join(args.out, rel)
        tag = '[D02]' if is_d02 else '[D00]'
        try:
            n = reader.extract_file(abs_pos, size, out_path)
            print(f'  OK {tag}  {n:>10,}  {path}')
            extracted += 1
        except OSError as e:
            print(f'  ERR {str(e)[:60]}  {path}')
            errors += 1

    print(f'\nExtracted {extracted} files ({errors} errors) -> {args.out!r}')
    reader.close()


if __name__ == '__main__':
    main()
