"""
Extract real, correctly-tinted item icons from the game's own data, for
shipbuilder/ship_icons/.

The game (per content/data.cdb, a CastleDB JSON export) does not store a
final colored icon per item. Instead each item has:
  - icon: {file, size, x, y} -- a cell in a grayscale sprite sheet
    (typically ui/icons/sprite_sheet_icon_64.png, 64px cells)
  - color: {colors: [4x signed ARGB int32], positions: [4x float 0..1]} --
    a 4-stop gradient used to tint that grayscale cell at render time.

The grayscale cell's R=G=B channel is the gradient lookup key (luminance/255
=> t in [0,1], fed through the color stops); the cell's own alpha channel is
the icon's shape mask and is preserved unchanged in the output.

This was reverse-engineered and confirmed pixel-faithful this session by
reconstructing MiningTool1's icon from data.cdb alone and comparing it
against the pre-existing (externally-sourced) MiningTool1.webp -- see
tools/hmd_format_notes.md finding 17 for the pak-position bug this depended
on (data.cdb and the icon sprite sheet were unreadable before that fix).

Usage:
    python tools/extract_item_icons.py MiningTool0 MiningTool3 MiningTool3_OC PathwayPuncher Radar0
    python tools/extract_item_icons.py --out-dir shipbuilder/ship_icons --cdb path/to/data.cdb <ids...>
"""
import argparse
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pak_extract import PakReader, PAK_PATH

DEFAULT_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'shipbuilder', 'ship_icons')


def load_cdb(cdb_path=None, pak_path=PAK_PATH):
    if cdb_path and os.path.exists(cdb_path):
        with open(cdb_path, encoding='utf-8') as f:
            return json.load(f)
    reader = PakReader(pak_path)
    for path, pos, size, is_d02 in reader.list_files():
        if path == 'data.cdb':
            reader.f.seek(pos)
            data = reader.f.read(size)
            return json.loads(data)
    raise FileNotFoundError('data.cdb not found in pak')


def find_item(cdb, item_id):
    item_sheet = next(s for s in cdb['sheets'] if s['name'] == 'item')
    for line in item_sheet['lines']:
        if line.get('id') == item_id:
            return line
    raise KeyError(f'item {item_id!r} not found in data.cdb')


def argb_int_to_rgba(v):
    v &= 0xFFFFFFFF
    a = (v >> 24) & 0xFF
    r = (v >> 16) & 0xFF
    g = (v >> 8) & 0xFF
    b = v & 0xFF
    return (r, g, b, a)


def build_gradient_stops(color_field):
    colors = color_field['colors']
    positions = color_field['positions']
    return sorted(zip(positions, (argb_int_to_rgba(c) for c in colors)), key=lambda s: s[0])


def gradient_lookup(stops, t):
    if t <= stops[0][0]:
        return stops[0][1]
    if t >= stops[-1][0]:
        return stops[-1][1]
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if p0 <= t <= p1:
            local_t = (t - p0) / (p1 - p0) if p1 > p0 else 0
            return tuple(c0[i] + (c1[i] - c0[i]) * local_t for i in range(4))
    return stops[-1][1]


def render_icon(sheet_img, icon_field, color_field):
    size = icon_field['size']
    x, y = icon_field['x'], icon_field['y']
    cell = sheet_img.crop((x * size, y * size, (x + 1) * size, (y + 1) * size))
    stops = build_gradient_stops(color_field)
    out = Image.new('RGBA', cell.size)
    px_in = cell.load()
    px_out = out.load()
    for yy in range(cell.height):
        for xx in range(cell.width):
            r, g, b, a = px_in[xx, yy]
            t = r / 255.0
            rr, gg, bb, _ = gradient_lookup(stops, t)
            px_out[xx, yy] = (int(round(rr)), int(round(gg)), int(round(bb)), a)
    return out


def main():
    ap = argparse.ArgumentParser(description='Extract real tinted item icons from data.cdb + the game sprite sheets')
    ap.add_argument('item_ids', nargs='+', help='Item ids from data.cdb (e.g. MiningTool0)')
    ap.add_argument('--out-dir', default=DEFAULT_OUT_DIR)
    ap.add_argument('--cdb', default=None, help='Pre-extracted data.cdb path (otherwise reads from the pak)')
    ap.add_argument('--pak', default=PAK_PATH)
    args = ap.parse_args()

    cdb = load_cdb(args.cdb, args.pak)
    reader = PakReader(args.pak)
    files = {path: (pos, size) for path, pos, size, is_d02 in reader.list_files()}

    sheet_cache = {}
    os.makedirs(args.out_dir, exist_ok=True)

    for item_id in args.item_ids:
        line = find_item(cdb, item_id)
        icon_field = line.get('icon')
        color_field = line.get('color')
        if not icon_field:
            print(f'  SKIP {item_id}: no icon field in data.cdb')
            continue
        if not color_field:
            print(f'  SKIP {item_id}: no color field in data.cdb')
            continue
        sheet_path = icon_field['file']
        if sheet_path not in sheet_cache:
            pos, size = files[sheet_path]
            reader.f.seek(pos)
            data = reader.f.read(size)
            from io import BytesIO
            sheet_cache[sheet_path] = Image.open(BytesIO(data)).convert('RGBA')
        icon = render_icon(sheet_cache[sheet_path], icon_field, color_field)
        out_path = os.path.join(args.out_dir, f'{item_id}.webp')
        icon.save(out_path, 'WEBP', lossless=True)
        print(f'  OK {item_id} <- {sheet_path} cell({icon_field["x"]},{icon_field["y"]}) -> {out_path}')


if __name__ == '__main__':
    main()
