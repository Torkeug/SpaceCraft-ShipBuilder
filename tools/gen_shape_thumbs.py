#!/usr/bin/env python3
"""
Generate missing shape thumbnails for shipbuilder/ship_shapes/.
Reads .bin mesh files, renders an isometric wireframe, saves as 32×32 RGBA webp.

Requires: Pillow  (pip install Pillow)
Usage:    python tools/gen_shape_thumbs.py [H I L M ...]
          (no args = generate all four missing thumbnails)
"""
import json, math, os, struct, sys

ROOT     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MESH_DIR = os.path.join(ROOT, 'shipbuilder', 'ship_meshes')
OUT_DIR  = os.path.join(ROOT, 'shipbuilder', 'ship_shapes')
MAN_PATH = os.path.join(MESH_DIR, '_manifest.json')

RENDER_SIZE = 128      # render at 4× then downscale for anti-aliasing
OUT_SIZE    = 32
MARGIN      = 6        # pixels at render resolution
LINE_W      = 4        # ~1 px after 4× downscale
LINE_COLOR  = (149, 250, 243, 230)   # matches existing thumbnails
BG_COLOR    = (0, 0, 0, 0)

# Isometric projection (Y-up world space):
# rotate 45° around Y, then tilt -30° around X.
_CY, _SY = math.cos(math.radians(45)),  math.sin(math.radians(45))
_CX, _SX = math.cos(math.radians(-30)), math.sin(math.radians(-30))

# Camera-to-scene direction in world space (from projection math).
# A face is front-facing if dot(normal, _CAM) < 0.
_CAM = (-_CX * _SY, _SX, _CX * _CY)

def project(x, y, z):
    x2 =  _CY * x + _SY * z
    z2 = -_SY * x + _CY * z
    y3 =  _CX * y - _SX * z2
    return x2, -y3   # flip y for screen space

def read_mesh(mesh_key):
    path = os.path.join(MESH_DIR, mesh_key + '.bin')
    if not os.path.exists(path):
        return None, None
    manifest = json.load(open(MAN_PATH))
    i32 = manifest.get(mesh_key, {}).get('i32', False)

    with open(path, 'rb') as f:
        data = f.read()
    o = 0
    vc = struct.unpack_from('<I', data, o)[0]; o += 4
    ic = struct.unpack_from('<I', data, o)[0]; o += 4
    gc = struct.unpack_from('B',  data, o)[0]; o += 1
    b  = struct.unpack_from('<6f', data, o);   o += 24
    bx, by, bz, bX, bY, bZ = b
    sx = (bX - bx) or 1; sy = (bY - by) or 1; sz = (bZ - bz) or 1

    verts = []
    for _ in range(vc):
        rx = struct.unpack_from('<H', data, o)[0]; o += 2
        ry = struct.unpack_from('<H', data, o)[0]; o += 2
        rz = struct.unpack_from('<H', data, o)[0]; o += 2
        wx = bx + rx / 65535 * sx
        wy = by + ry / 65535 * sy   # game Y (depth)
        wz = bz + rz / 65535 * sz   # game Z (height) — .bin is Z-up
        verts.append((wx, wz, -wy))  # convert to Y-up

    o += gc * 12  # skip groups: role(1)+rgb(3)+start(4)+count(4)

    fmt, isz = ('<I', 4) if i32 else ('<H', 2)
    indices = [struct.unpack_from(fmt, data, o + k * isz)[0] for k in range(ic)]

    edges = hard_edges(verts, indices)
    return verts, edges


def front(n):
    """True if face normal points toward the camera."""
    return n[0]*_CAM[0] + n[1]*_CAM[1] + n[2]*_CAM[2] < 0

def hard_edges(verts, indices, threshold_deg=30.0):
    """Return hard edges (dihedral > threshold) visible from the camera."""
    cos_thresh = math.cos(math.radians(threshold_deg))
    edge_normals = {}
    for i in range(0, len(indices) - 2, 3):
        ia, ib, ic = indices[i], indices[i+1], indices[i+2]
        va, vb, vc = verts[ia], verts[ib], verts[ic]
        ax, ay, az = vb[0]-va[0], vb[1]-va[1], vb[2]-va[2]
        bx, by, bz = vc[0]-va[0], vc[1]-va[1], vc[2]-va[2]
        nx, ny, nz = ay*bz-az*by, az*bx-ax*bz, ax*by-ay*bx
        ln = math.sqrt(nx*nx + ny*ny + nz*nz)
        if ln < 1e-10:
            continue
        n = (nx/ln, ny/ln, nz/ln)
        for a, b in ((min(ia,ib), max(ia,ib)), (min(ib,ic), max(ib,ic)), (min(ia,ic), max(ia,ic))):
            edge_normals.setdefault((a, b), []).append(n)
    result = set()
    for edge, normals in edge_normals.items():
        if len(normals) == 1:
            if front(normals[0]):
                result.add(edge)
        elif len(normals) >= 2:
            n1, n2 = normals[0], normals[1]
            is_hard = n1[0]*n2[0] + n1[1]*n2[1] + n1[2]*n2[2] < cos_thresh
            if is_hard and (front(n1) or front(n2)):
                result.add(edge)
    return result

def render_thumb(mesh_key):
    from PIL import Image, ImageDraw
    verts, edges = read_mesh(mesh_key)
    if verts is None:
        return None

    proj = [project(x, y, z) for x, y, z in verts]
    xs = [p[0] for p in proj]; ys = [p[1] for p in proj]
    w = (max(xs) - min(xs)) or 1
    h = (max(ys) - min(ys)) or 1
    scale = (RENDER_SIZE - 2 * MARGIN) / max(w, h)
    ox = -min(xs) * scale + MARGIN + ((RENDER_SIZE - 2 * MARGIN) - w * scale) / 2
    oy = -min(ys) * scale + MARGIN + ((RENDER_SIZE - 2 * MARGIN) - h * scale) / 2

    def sc(px, py):
        return px * scale + ox, py * scale + oy

    img = Image.new('RGBA', (RENDER_SIZE, RENDER_SIZE), BG_COLOR)
    draw = ImageDraw.Draw(img)
    for a, b2 in edges:
        draw.line([sc(*proj[a]), sc(*proj[b2])], fill=LINE_COLOR, width=LINE_W)

    resample = getattr(Image, 'Resampling', Image).LANCZOS
    return img.resize((OUT_SIZE, OUT_SIZE), resample)

# One representative mesh per shape letter (smallest hull size = clearest silhouette)
SHAPE_MESHES = {'H': '4x3x1_H', 'I': '4x3x1_I', 'L': '4x3x1_L', 'M': '4x3x1_M'}

targets = sys.argv[1:] or list(SHAPE_MESHES.keys())
for shape in targets:
    key = SHAPE_MESHES.get(shape.upper())
    if not key:
        print(f'Unknown shape: {shape}'); continue
    print(f'Rendering {shape} ({key})... ', end='', flush=True)
    img = render_thumb(key)
    if img:
        out = os.path.join(OUT_DIR, f'{shape}.webp')
        img.save(out, 'webp', quality=90)
        print(f'saved -> {os.path.relpath(out, ROOT)}')
    else:
        print('FAILED (missing .bin)')
