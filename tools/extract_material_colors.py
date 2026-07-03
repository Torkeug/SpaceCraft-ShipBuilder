"""
extract_material_colors.py -- Decode real basecolor textures from the game's
Materials_Library and compute an average RGB per material name, replacing the
old invented per-role placeholder colors (_DEFAULT_ROLES in hmd_to_bin.py)
with actual extracted data.

Format discovered this session (no reference implementation existed for this
one, unlike the HMD reader -- reverse-engineered from scratch): despite the
".png" extension, these files are NOT PNG. They are BC1/DXT1-compressed
textures with a full mipmap chain and a 128-byte header before the pixel
data. Confirmed by: (1) no known image-format magic (PNG/JPG/GIF/DDS) appears
at the start of the file, matching none of hxd.res.Image.hx's real format
signatures: the file size algebraically matches "128-byte header + BC1 mip
chain for a power-of-two square texture" almost exactly (128 bytes short of
a clean total for every file size checked), and (2) decoding under that
hypothesis produces coherent, recognizable material textures (verified
visually on Metal_Brushed -- real brushed steel with scratches/rust spots --
and Yellow_Plastic -- a plausible flat mustard-yellow plastic color).

Driven by the actual material names read from each Tools-category mesh
(hmd_parse_heaps' materials[] list), not a blind crawl of the material
library -- search the whole pak for a "<name>_basecolor*.png" file matching
each real name exactly, rather than fuzzy-matching between two different
naming schemes.

Usage:
    python tools/extract_material_colors.py
Writes tools/material_colors.json: {material_name: [r,g,b]}
"""

import json
import os
import struct
import sys

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS_DIR)
PAK_FULL = os.path.join(REPO_ROOT, 'pak_out_full')
PAK_OUT = os.path.join(REPO_ROOT, 'pak_out', 'assets', 'Vehicules', 'Buildings_Parts')

sys.path.insert(0, TOOLS_DIR)


def real_material_names():
    """Collect every material name actually used across the Tools-category
    mesh source files (batch_convert_modules.MODULE_SOURCES), read straight
    from each file's own materials[] list."""
    from hmd_parse_heaps import parse
    from batch_convert_modules import MODULE_SOURCES

    names = set()
    seen_paths = set()
    for rel in MODULE_SOURCES.values():
        path = os.path.join(PAK_OUT, rel)
        if path in seen_paths or not os.path.exists(path):
            continue
        seen_paths.add(path)
        data = open(path, 'rb').read()
        off = data.find(b'HMD\x06')
        if off < 0:
            continue
        try:
            d = parse(data, off)
        except Exception:
            continue
        for m in d['materials']:
            if m['name']:
                names.add(m['name'])
    return names


def find_basecolor_index():
    """Map every basecolor texture's own filename-derived material name to its
    path, across the whole pak (not just one hardcoded folder subset)."""
    index = {}
    for root, _dirs, fnames in os.walk(PAK_FULL):
        for fn in fnames:
            lf = fn.lower()
            if '_basecolor' in lf and lf.endswith('.png'):
                key = fn[:lf.index('_basecolor')]
                index.setdefault(key, os.path.join(root, fn))
    return index


def mip_chain_bytes(w, h, bytes_per_block=8):
    total = 0
    while True:
        bx = max(1, (w + 3) // 4)
        by = max(1, (h + 3) // 4)
        total += bx * by * bytes_per_block
        if w == 1 and h == 1:
            break
        w = max(1, w // 2)
        h = max(1, h // 2)
    return total


# Header size differs slightly between BC1 (128B, no alpha) and BC3 (148B,
# has an alpha block) -- confirmed empirically against real file sizes for
# both formats (Metal_Brushed etc. for BC1; Glass/Grid_Hex/POM_decals_03 etc.
# for BC3, all named with "_Alpha" or being genuinely alpha-blended decals).
HEADER_BC1 = 128
HEADER_BC3 = 148


def guess_format(file_size):
    """Return (tag, header_size, w, h) for whichever format matches this
    file's size: BC1/BC3 mip chain, or a single-level uncompressed RGBA (seen
    on Signaletique_01/02 -- decal atlases that skip mipmapping)."""
    for bpp, header in ((8, HEADER_BC1), (16, HEADER_BC3)):
        payload = file_size - header
        for size in (2048, 1024, 512, 256, 128, 64):
            if mip_chain_bytes(size, size, bpp) == payload:
                tag = 'bc3' if bpp == 16 else 'bc1'
                return tag, header, size, size
    for header in (HEADER_BC1,):
        payload = file_size - header
        for size in (2048, 1024, 512, 256, 128, 64):
            if size * size * 4 == payload:
                return 'rgba', header, size, size
    return None


def decode_rgba_average(data, off, w, h):
    r_sum = g_sum = b_sum = n = 0
    step = max(1, (w * h) // 1024)  # sample ~1024 pixels regardless of resolution
    total_px = w * h
    for i in range(0, total_px, step):
        p = off + i * 4
        if p + 3 > len(data):
            continue
        r, g, b = data[p], data[p + 1], data[p + 2]
        r_sum += r
        g_sum += g
        b_sum += b
        n += 1
    if n == 0:
        return None
    return (round(r_sum / n), round(g_sum / n), round(b_sum / n))


def decode_bc1_average(data, off, w, h, bytes_per_block=8):
    """Decode just enough blocks to get a representative average color -- sample
    a grid of blocks rather than the whole top mip for speed. For BC3
    (bytes_per_block=16), each block is [8 bytes alpha][8 bytes BC1-style
    color] -- skip the alpha half and read color the same way as BC1."""
    bx, by = (w + 3) // 4, (h + 3) // 4
    r_sum = g_sum = b_sum = n = 0
    step = max(1, bx // 32)  # ~32x32 block sample grid regardless of resolution
    color_skip = bytes_per_block - 8
    for by_i in range(0, by, step):
        for bx_i in range(0, bx, step):
            block_off = off + (by_i * bx + bx_i) * bytes_per_block + color_skip
            if block_off + 8 > len(data):
                continue
            c0, c1 = struct.unpack_from('<HH', data, block_off)

            def unpack565(c):
                r = (c >> 11) & 0x1F
                g = (c >> 5) & 0x3F
                b = c & 0x1F
                return (r << 3 | r >> 2, g << 2 | g >> 4, b << 3 | b >> 2)

            r0, g0, b0 = unpack565(c0)
            r1, g1, b1 = unpack565(c1)
            # Average the two endpoint colors as a fast approximation of the
            # block's overall color (skip full 2-bit index decoding per pixel).
            r_sum += (r0 + r1) / 2
            g_sum += (g0 + g1) / 2
            b_sum += (b0 + b1) / 2
            n += 1
    if n == 0:
        return None
    return (round(r_sum / n), round(g_sum / n), round(b_sum / n))


def find_base_match(name, index, index_lower):
    """Many mesh material names are tint/paint variants of a shared base
    texture with no dedicated file of their own (e.g. Metal_Standard_Copper
    is Metal_Standard with a color tint applied elsewhere, not its own
    texture). Progressively strip trailing '_word' segments to find the base
    texture actually backing a variant name. Also checks case-insensitively --
    the game's own data has inconsistent casing for the same material
    (e.g. Metal_RedPaint vs Metal_Redpaint across different files)."""
    parts = name.split('_')
    while parts:
        candidate = '_'.join(parts)
        if candidate in index:
            return candidate
        if candidate.lower() in index_lower:
            return index_lower[candidate.lower()]
        parts.pop()
    return None


def decode_one(path):
    data = open(path, 'rb').read()
    fmt = guess_format(len(data))
    if fmt is None:
        return None, len(data)
    tag, header, w, h = fmt
    if tag == 'rgba':
        avg = decode_rgba_average(data, header, w, h)
    else:
        avg = decode_bc1_average(data, header, w, h, bytes_per_block=16 if tag == 'bc3' else 8)
    if avg is None:
        return None, len(data)
    return (tag, w, h, avg), None


def main():
    names = real_material_names()
    print(f"{len(names)} distinct material names used across Tools meshes")
    index = find_basecolor_index()
    index_lower = {k.lower(): k for k in index}
    print(f"{len(index)} basecolor textures found in pak_out_full")

    colors = {}
    missing = []
    errors = []
    for name in sorted(names):
        path = index.get(name)
        matched_via = name
        if path is None:
            base = find_base_match(name, index, index_lower)
            if base is not None:
                path = index[base]
                matched_via = base
        if path is None:
            missing.append(name)
            continue
        result, err_size = decode_one(path)
        if result is None:
            errors.append((name, err_size))
            continue
        tag, w, h, avg = result
        colors[name] = list(avg)
        via = '' if matched_via == name else f" (via {matched_via})"
        print(f"  {name:30s} {w}x{h} {tag}{via}  avg={avg}")

    if missing:
        print(f"\n{len(missing)} material names have no matching basecolor texture anywhere in the pak:")
        for name in missing:
            print(f"  {name}")
    if errors:
        print(f"\n{len(errors)} files didn't match the expected size formula:")
        for name, size in errors:
            print(f"  {name} ({size} bytes)")

    out_path = os.path.join(TOOLS_DIR, 'material_colors.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(colors, f, indent=2, sort_keys=True)
    print(f"\nWrote {len(colors)} colors -> {out_path}")


if __name__ == '__main__':
    main()
