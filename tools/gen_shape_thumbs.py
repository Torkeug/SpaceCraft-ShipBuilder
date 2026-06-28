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

def read_mesh_raw(mesh_key):
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
        wy = by + ry / 65535 * sy
        wz = bz + rz / 65535 * sz
        verts.append((wx, wz, -wy))
    o += gc * 12
    fmt, isz = ('<I', 4) if i32 else ('<H', 2)
    indices = [struct.unpack_from(fmt, data, o + k * isz)[0] for k in range(ic)]
    return verts, indices

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

    edges = structural_edges(verts, indices)
    return verts, edges


def front(n):
    """True if face normal points toward the camera."""
    return n[0]*_CAM[0] + n[1]*_CAM[1] + n[2]*_CAM[2] < 0

def structural_edges(verts, indices, tol_deg=8.0):
    """Cluster nearly-coplanar triangles into planar faces, then return edges between
    different face groups that are visible from the camera. This eliminates tessellation
    artifacts so only true structural edges (like box corners) are drawn."""
    cos_tol = math.cos(math.radians(tol_deg))

    # Compute per-triangle normal
    tri_normals = []
    for i in range(0, len(indices) - 2, 3):
        ia, ib, ic = indices[i], indices[i+1], indices[i+2]
        va, vb, vc = verts[ia], verts[ib], verts[ic]
        ax, ay, az = vb[0]-va[0], vb[1]-va[1], vb[2]-va[2]
        bx, by, bz = vc[0]-va[0], vc[1]-va[1], vc[2]-va[2]
        nx, ny, nz = ay*bz-az*by, az*bx-ax*bz, ax*by-ay*bx
        ln = math.sqrt(nx*nx + ny*ny + nz*nz)
        tri_normals.append((nx/ln, ny/ln, nz/ln) if ln > 1e-10 else None)

    # Greedy cluster: merge triangles whose normals agree within tol_deg
    cluster_normals = []
    tri_cluster = {}
    for fi, n in enumerate(tri_normals):
        if n is None:
            continue
        for ci, cn in enumerate(cluster_normals):
            if cn[0]*n[0] + cn[1]*n[1] + cn[2]*n[2] > cos_tol:
                tri_cluster[fi] = ci
                break
        else:
            tri_cluster[fi] = len(cluster_normals)
            cluster_normals.append(n)

    # Build edge -> list of cluster ids (one per adjacent triangle, may repeat)
    edge_clusters = {}
    for fi, n in enumerate(tri_normals):
        if n is None or fi not in tri_cluster:
            continue
        ci = tri_cluster[fi]
        i = fi * 3
        ia, ib, ic = indices[i], indices[i+1], indices[i+2]
        for a, b in ((min(ia,ib), max(ia,ib)), (min(ib,ic), max(ib,ic)), (min(ia,ic), max(ia,ic))):
            edge_clusters.setdefault((a, b), []).append(ci)

    cluster_front = [front(n) for n in cluster_normals]

    result = set()
    for edge, adj in edge_clusters.items():
        if len(adj) == 1:
            # True boundary edge (open mesh) — include if front-facing
            if cluster_front[adj[0]]:
                result.add(edge)
        else:
            c0, c1 = adj[0], adj[1]
            if c0 == c1:
                continue  # interior edge within same flat face — skip
            # Edge between two different face groups — include if any is visible
            if cluster_front[c0] or cluster_front[c1]:
                result.add(edge)
    return result

def render_thumb(mesh_key):
    from PIL import Image, ImageDraw, ImageFilter
    verts, indices = read_mesh_raw(mesh_key)
    if verts is None:
        return None

    proj = [project(x, y, z) for x, y, z in verts]
    xs = [p[0] for p in proj]; ys = [p[1] for p in proj]
    w = (max(xs) - min(xs)) or 1
    h = (max(ys) - min(ys)) or 1
    SZ = RENDER_SIZE * 4   # render at 4× for clean edges
    margin = MARGIN * 4
    scale = (SZ - 2 * margin) / max(w, h)
    ox = -min(xs) * scale + margin + ((SZ - 2 * margin) - w * scale) / 2
    oy = -min(ys) * scale + margin + ((SZ - 2 * margin) - h * scale) / 2

    def sc(px, py):
        return px * scale + ox, py * scale + oy

    # Cluster triangles by normal so we can assign each face a distinct shade.
    # Edges between differently-shaded face groups give both the outer silhouette
    # AND interior visible creases (e.g. the top-front-right edges of a box).
    cos_tol = math.cos(math.radians(8.0))
    cluster_normals2 = []
    tri_shade = {}   # face_index -> gray value (10..240, distinct per cluster)
    for fi in range(len(indices) // 3):
        i = fi * 3
        ia2, ib2, ic2 = indices[i], indices[i+1], indices[i+2]
        va, vb, vc = verts[ia2], verts[ib2], verts[ic2]
        ax, ay, az = vb[0]-va[0], vb[1]-va[1], vb[2]-va[2]
        bx, by, bz = vc[0]-va[0], vc[1]-va[1], vc[2]-va[2]
        nx, ny, nz = ay*bz-az*by, az*bx-ax*bz, ax*by-ay*bx
        ln = math.sqrt(nx*nx + ny*ny + nz*nz)
        if ln < 1e-10:
            continue
        n = (nx/ln, ny/ln, nz/ln)
        if not front(n):
            continue
        for ci, cn in enumerate(cluster_normals2):
            if cn[0]*n[0] + cn[1]*n[1] + cn[2]*n[2] > cos_tol:
                tri_shade[fi] = 10 + (ci * 37) % 230
                break
        else:
            ci = len(cluster_normals2)
            cluster_normals2.append(n)
            tri_shade[fi] = 10 + (ci * 37) % 230

    # Render each visible face cluster as a distinct gray value
    face_img = Image.new('L', (SZ, SZ), 0)
    draw = ImageDraw.Draw(face_img)
    for fi, shade in tri_shade.items():
        i = fi * 3
        ia2, ib2, ic2 = indices[i], indices[i+1], indices[i+2]
        pts = [sc(*proj[ia2]), sc(*proj[ib2]), sc(*proj[ic2])]
        draw.polygon(pts, fill=shade)

    # Edge detect: find pixels where neighbours differ (= face boundaries)
    import struct as _struct
    px = face_img.load()
    outline_img = Image.new('L', (SZ, SZ), 0)
    opx = outline_img.load()
    for y in range(1, SZ - 1):
        for x in range(1, SZ - 1):
            v = px[x, y]
            if v == 0:
                continue
            for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                n2 = px[x+dx, y+dy]
                if n2 != v:
                    opx[x, y] = 255
                    break

    # Dilate so the 1px edge survives the 16:1 downscale
    thick = outline_img.filter(ImageFilter.MaxFilter(15))

    # Compose cyan on transparent, then downscale
    color_fill = Image.new('RGBA', (SZ, SZ), (*LINE_COLOR[:3], 255))
    result = Image.new('RGBA', (SZ, SZ), (0, 0, 0, 0))
    result.paste(color_fill, mask=thick)

    resample = getattr(Image, 'Resampling', Image).LANCZOS
    small = result.resize((OUT_SIZE, OUT_SIZE), resample)
    return small

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
