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

### Hosting it online

The app is fully static — no backend, no build step. It's just the
`shipbuilder/shipbuilder/` folder (`index.html`, `js/`, `style.css`, JSON
data, `ship_meshes/*.bin`, `ship_icons/*.webp`) served as-is by any static
file host. To host it:

1. Point a static host at the `shipbuilder/shipbuilder/` folder specifically
   (not the repo root — the app's root files live one level down).
2. Pick a host:
   - **GitHub Pages** — enable Pages in the repo's Settings, either via a
     GitHub Actions workflow that publishes `shipbuilder/shipbuilder/`, or by
     switching to a `gh-pages` branch/`docs/` folder containing its contents.
     Free, and redeploys automatically on push.
   - **Netlify / Vercel / Cloudflare Pages** — connect the repo and set the
     "base directory" / "publish directory" to `shipbuilder/shipbuilder`. Free
     tier, custom domains, and preview deploys per branch/PR.
   - Any other static host (S3 + CloudFront, nginx, etc.) works the same way:
     just serve that folder's contents at the site root.
3. No server-side code or database is involved, so there's nothing else to
   configure.

Before making it public, read [NOTICE.md](NOTICE.md): the meshes, icons, and
part stats are extracted from the game itself and scoped there for personal,
non-commercial, educational use — hosting publicly means serving those files
to anyone who visits, which is worth deciding on deliberately rather than by
default.

## Reverse-engineering tools

`tools/` contains the scripts used to extract and convert game assets from
the game's `res.pak` (mesh format decoding, pak archive parsing, material
color extraction, etc.), along with detailed format notes in
`tools/hmd_format_notes.md`.
