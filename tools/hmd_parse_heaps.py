"""
hmd_parse_heaps.py -- Faithful port of Heaps' own hxd.fmt.hmd.Reader (from the
HeapsIO/heaps engine source, hxd/fmt/hmd/Reader.hx and Data.hx, CURRENT_VERSION=6,
matching our files' "HMD\\x06" magic exactly).

This supersedes the earlier from-scratch/heuristic parser (hmd_parse_prod.py) for
one critical reason: the real format has a `models[]` array, separate from
`geometries[]`. Each Model has its own `position` (translation + quaternion
rotation + **scale** sx/sy/sz) and a `geometry` index into the geometries array.
Multiple models can reference the same geometry. Our own from-scratch parser only
ever read the geometries/vertex data directly and never accounted for a model's own
scale -- this is the root cause of the Water_Collector oversize bug (and likely
others): the raw vertex data is correct, but the model-node scale that the real
engine applies on top of it was being silently dropped.

Usage:
    python tools/hmd_parse_heaps.py <file.fbx>
"""

import struct
import sys


class Reader:
    def __init__(self, data, offset=0):
        self.data = data
        self.pos = offset
        self.version = None

    def u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self):
        v = struct.unpack_from('<H', self.data, self.pos)[0]
        self.pos += 2
        return v

    def i32(self):
        v = struct.unpack_from('<i', self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self):
        v = struct.unpack_from('<f', self.data, self.pos)[0]
        self.pos += 4
        return v

    def string(self, n):
        v = self.data[self.pos:self.pos + n].decode('utf-8', errors='replace')
        self.pos += n
        return v

    def read_name(self):
        b = self.u8()
        if b == 0xFF:
            return None
        return self.string(b)

    # readCachedName is byte-identical to readName; caching is a memory optimization only.
    read_cached_name = read_name

    def read_property(self):
        tag = self.u8()
        if tag == 0:
            return ('CameraFOVY', self.f32())
        if tag == 1:
            raise ValueError("Obsolete HasMaterialFlags")
        names = {2: 'HasExtraTextures', 3: 'FourBonesByVertex', 4: 'HasLod',
                 5: 'HasCollider', 6: 'HasColliders', 7: 'HasCustomCollider'}
        if tag in names:
            return (names[tag],)
        raise ValueError(f"Unknown property #{tag}")

    def read_props(self):
        if self.version == 1:
            return None
        n = self.u8()
        if n == 0:
            return None
        return [self.read_property() for _ in range(n)]

    def read_position(self, has_scale=True):
        x, y, z = self.f32(), self.f32(), self.f32()
        qx, qy, qz = self.f32(), self.f32(), self.f32()
        if has_scale:
            sx, sy, sz = self.f32(), self.f32(), self.f32()
        else:
            sx = sy = sz = 1.0
        return dict(x=x, y=y, z=z, qx=qx, qy=qy, qz=qz, sx=sx, sy=sy, sz=sz)

    def read_bounds(self):
        return [self.f32() for _ in range(6)]

    def read_format(self):
        stride = self.u8()
        count = self.u8()
        fields = []
        for _ in range(count):
            name = self.read_cached_name()
            typ = self.u8()
            fields.append((name, typ))
        return stride, fields

    def read_skin(self):
        name = self.read_cached_name()
        if name is None:
            return None
        sprops = self.read_props()
        njoints = self.u16()
        joints = []
        for _ in range(njoints):
            jprops = self.read_props()
            jname = self.read_cached_name()
            pid = self.u16()
            has_scale = (pid & 0x8000) != 0
            if has_scale:
                pid &= 0x7FFF
            parent = pid - 1
            position = self.read_position(has_scale)
            bind = self.u16() - 1
            transpos = self.read_position(has_scale) if bind >= 0 else None
            joints.append(dict(name=jname, parent=parent, position=position,
                                bind=bind, transpos=transpos))
        count = self.u8()
        split = None
        if count > 0:
            split = []
            for _ in range(count):
                mat_index = self.u8()
                njg = self.u8()
                joint_refs = [self.u16() for _ in range(njg)]
                split.append(dict(materialIndex=mat_index, joints=joint_refs))
        return dict(name=name, joints=joints, split=split)

    def has_prop(self, props, name):
        return bool(props) and any(p[0] == name for p in props)


_PRECISION_BYTES = {0: 4, 1: 2, 2: 1, 3: 1}  # F32, F16, U8, S8


def stride_bytes(fields):
    """Compute the real per-vertex byte stride from a geometry's field list.

    The raw `stride` byte stored in the file (Reader.hx's makeFormat/BufferFormat)
    is the total *component count* across fields (e.g. position+normal+tangent
    DVec3 + uv DVec2 = 11), NOT the byte size -- it's only used as a sanity-check
    assertion in the real reader, never as an actual buffer stride. The real byte
    stride must be computed field-by-field from each field's component count and
    precision, with 4-byte alignment padding applied cumulatively after each field
    (matching BufferFormat's constructor in hxd/BufferFormat.hx).
    """
    total = 0
    for _name, typ in fields:
        fmt = typ & 15
        prec = typ >> 4
        components = 1 if fmt == 9 else fmt  # DBytes4 (9) is special-cased to size 1
        total += components * _PRECISION_BYTES[prec]
        if total & 3 != 0:
            total += 4 - (total & 3)
    return total


def parse(data, offset=0):
    r = Reader(data, offset)
    magic = r.string(3)
    if magic != "HMD":
        raise ValueError(f"Invalid HMD header {magic!r}")
    r.version = r.u8()
    data_position = r.i32()
    props = r.read_props()

    geometries = []
    for _ in range(r.i32()):
        gprops = r.read_props()
        vc = r.i32()
        stride, fields = r.read_format()
        vpos = r.i32()
        subcount = r.u8()
        if subcount == 0xFF:
            subcount = r.i32()
        idxcounts = [r.i32() for _ in range(subcount)]
        ipos = r.i32()
        bounds = r.read_bounds()
        geometries.append(dict(props=gprops, vertexCount=vc, stride=stride, fields=fields,
                                vertexPosition=vpos, indexCounts=idxcounts,
                                indexPosition=ipos, bounds=bounds))

    materials = []
    for _ in range(r.i32()):
        mprops = r.read_props()
        name = r.read_name()
        diffuse = r.read_name()
        blend = r.u8()
        r.u8()   # old culling
        r.f32()  # old killalpha
        specular = normalmap = None
        if r.has_prop(mprops, 'HasExtraTextures'):
            specular = r.read_name()
            normalmap = r.read_name()
        materials.append(dict(name=name, diffuse=diffuse, specular=specular, normalmap=normalmap))

    models = []
    for _ in range(r.i32()):
        mprops = r.read_props()
        name = r.read_cached_name()
        parent = r.i32() - 1
        follow = r.read_cached_name()
        position = r.read_position()
        geom = r.i32() - 1
        model = dict(props=mprops, name=name, parent=parent, follow=follow,
                     position=position, geometry=geom)
        models.append(model)
        if geom < 0:
            continue
        matcount = r.u8()
        if matcount == 0xFF:
            matcount = r.i32()
        model['materials'] = [r.i32() for _ in range(matcount)]
        model['skin'] = r.read_skin()
        if r.has_prop(mprops, 'HasLod'):
            lodcount = r.i32()
            model['lods'] = [r.i32() for _ in range(lodcount)]
        if r.has_prop(mprops, 'HasCollider'):
            model['collider'] = r.i32()
        if r.has_prop(mprops, 'HasColliders'):
            model['colliders'] = [r.i32() for _ in range(r.i32())]

    return dict(version=r.version, dataPosition=data_position, props=props,
                geometries=geometries, materials=materials, models=models, end_pos=r.pos)


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    data = open(sys.argv[1], 'rb').read()
    off = data.find(b'HMD\x06')
    if off < 0:
        print("No HMD\\x06 magic found")
        sys.exit(1)
    d = parse(data, off)
    print(f"version={d['version']}  geometries={len(d['geometries'])}  "
          f"materials={len(d['materials'])}  models={len(d['models'])}  "
          f"parsed up to byte {d['end_pos']} (file size {len(data)})")
    for i, g in enumerate(d['geometries']):
        print(f"  geom[{i}] vc={g['vertexCount']} stride={g['stride']} "
              f"idxCounts={g['indexCounts']} bounds={[round(b,3) for b in g['bounds']]}")
    for i, m in enumerate(d['models']):
        p = m['position']
        print(f"  model[{i}] name={m['name']!r} parent={m['parent']} geom={m['geometry']} "
              f"pos=({p['x']:.3f},{p['y']:.3f},{p['z']:.3f}) "
              f"scale=({p['sx']:.4f},{p['sy']:.4f},{p['sz']:.4f}) "
              f"lods={m.get('lods')}")


if __name__ == '__main__':
    main()
