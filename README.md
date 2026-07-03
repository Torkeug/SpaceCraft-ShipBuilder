# SpaceCraft Ship Builder

A Three.js browser-based ship designer for the game SpaceCraft, built against
the game's real part catalogue (dimensions, meshes, and stats derived from
the game's own files).

See [NOTICE.md](NOTICE.md) for important information on licensing — this
repository mixes original code (MIT-licensed) with reference content
extracted from the game itself (not covered by that license).

A companion Windows desktop overlay for tracking resource deposits and
crafting recipes lives in a separate repository:
[CraftMap](https://github.com/Torkeug/CraftMap).

## Ship Builder

**Launch:**
```
cd shipbuilder
python -m http.server 8765
```
then open `http://localhost:8765`, or just double-click `shipbuilder/start.bat`.

Full part catalogue (hull frames, cockpits, engines, wings, modules) with
real dimensions and mesh sizes derived from the game's own files, a
module-slot system for internal components, and a live ship-stats panel
(structure, propulsion, power, cargo, combat).

## Reverse-engineering tools

`tools/` contains the scripts used to extract and convert game assets from
the game's `res.pak` (mesh format decoding, pak archive parsing, material
color extraction, etc.), along with detailed format notes in
`tools/hmd_format_notes.md`.
