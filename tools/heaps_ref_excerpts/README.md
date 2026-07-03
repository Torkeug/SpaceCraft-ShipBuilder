# Heaps engine reference excerpt

These 3 files are vendored, unmodified, from [HeapsIO/heaps](https://github.com/HeapsIO/heaps)
(MIT licensed — see `LICENSE`), because they are the ground-truth reference
`tools/hmd_parse_heaps.py` is a faithful port of.

- `hxd/fmt/hmd/Reader.hx` — the real HMD file reader (header, geometries,
  materials, `models[]` scene-node hierarchy with per-node position/rotation/
  scale). This is what proved our earlier heuristic parser (`hmd_parse_prod.py`)
  was silently dropping every part's real transform — see finding 8 in
  `tools/hmd_format_notes.md`.
- `hxd/fmt/hmd/Data.hx` — the `Position`, `Model`, `Geometry` etc. type
  definitions the reader populates.
- `hxd/BufferFormat.hx` — vertex format/stride computation. Confirms the raw
  `stride` byte stored per-geometry is a *component count*, not a byte size;
  `stride_bytes()` in `hmd_parse_heaps.py` is a Python port of this file's
  stride-computation loop.

Kept small and excerpted (not the full clone, which also carries ~270MB of
unrelated engine code, other vendored repos, and Rust build artifacts from an
`hlbc` decompiler patch that turned out not to be the path that led to the
actual fix) so the specific reference material survives independent of
GitHub, without bloating the repo. If deeper investigation of the HMD/Heaps
format is needed again, re-clone the full engine:

```bash
git clone --depth 1 https://github.com/HeapsIO/heaps.git tools/heaps_ref/heaps
```
