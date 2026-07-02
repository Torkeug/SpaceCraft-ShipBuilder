import * as THREE from 'three';

let MESHMAN = {};
const MESHCACHE = {};

export async function loadManifest() {
  try {
    MESHMAN = await fetch('ship_meshes/_manifest.json?v=' + Date.now()).then(r => r.json());
  } catch (e) { MESHMAN = {}; }
}

// Synchronous cache lookup — returns result if already loaded, null otherwise.
export function getCached(id) {
  return (MESHCACHE[id] && !MESHCACHE[id].then) ? MESHCACHE[id] : null;
}

// Returns the mesh key for a part given a shape index.
export function getMeshKey(part, shapeIdx = 0) {
  if (part.shapes && part.shapes.length > 0) {
    const s = part.shapes[Math.min(shapeIdx, part.shapes.length - 1)];
    return s ? s.m : null;
  }
  return part.m || null;
}

// Fetch + decode a binary mesh. Returns {geom, groups:[{role,hex}]} or null. Cached.
export function loadGeom(id) {
  if (!MESHMAN[id]) return Promise.resolve(null);
  if (MESHCACHE[id] && MESHCACHE[id].then) return MESHCACHE[id];
  if (MESHCACHE[id]) return Promise.resolve(MESHCACHE[id]);

  const p = fetch('ship_meshes/' + id + '.bin?v=' + (MESHMAN[id] ? MESHMAN[id].t : 0))
    .then(r => r.arrayBuffer())
    .then(buf => {
      const dv = new DataView(buf); let o = 0;
      const vc = dv.getUint32(o, true); o += 4;
      const ic = dv.getUint32(o, true); o += 4;
      const gc = dv.getUint8(o); o += 1;
      if (gc < 1 || gc > 64) { MESHCACHE[id] = null; return null; }
      const b = [];
      for (let k = 0; k < 6; k++) { b.push(dv.getFloat32(o, true)); o += 4; }
      const sx = (b[3] - b[0]) || 1, sy = (b[4] - b[1]) || 1, sz = (b[5] - b[2]) || 1;
      const pos = new Float32Array(vc * 3);
      for (let v = 0; v < vc; v++) {
        pos[v * 3]     = b[0] + dv.getUint16(o, true) / 65535 * sx; o += 2;
        pos[v * 3 + 1] = b[1] + dv.getUint16(o, true) / 65535 * sy; o += 2;
        pos[v * 3 + 2] = b[2] + dv.getUint16(o, true) / 65535 * sz; o += 2;
      }
      const groups = [];
      for (let k = 0; k < gc; k++) {
        const role  = dv.getUint8(o); o += 1;
        const r     = dv.getUint8(o); o += 1;
        const gg    = dv.getUint8(o); o += 1;
        const bl    = dv.getUint8(o); o += 1;
        const start = dv.getUint32(o, true); o += 4;
        const count = dv.getUint32(o, true); o += 4;
        groups.push({ role, hex: '#' + [r, gg, bl].map(x => x.toString(16).padStart(2, '0')).join(''), start, count });
      }
      const i32 = MESHMAN[id] && MESHMAN[id].i32;
      const idx = i32 ? new Uint32Array(ic) : new Uint16Array(ic);
      for (let k = 0; k < ic; k++) {
        idx[k] = i32 ? dv.getUint32(o, true) : dv.getUint16(o, true);
        o += i32 ? 4 : 2;
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      g.setIndex(new THREE.BufferAttribute(idx, 1));
      groups.forEach((gr, i) => g.addGroup(gr.start, gr.count, i));
      g.computeVertexNormals();
      g.computeBoundingBox();
      const res = { geom: g, groups: groups.map(gr => ({ role: gr.role, hex: gr.hex })) };
      MESHCACHE[id] = res;
      return res;
    })
    .catch(() => { MESHCACHE[id] = null; return null; });

  MESHCACHE[id] = p;
  return p;
}

function reverseWinding(g) {
  const idx = g.getIndex(); if (!idx) return;
  const a = idx.array;
  for (let i = 0; i + 2 < a.length; i += 3) {
    const t = a[i + 1]; a[i + 1] = a[i + 2]; a[i + 2] = t;
  }
  idx.needsUpdate = true;
}

// Fit a raw geometry into a cell [0..w, 0..h, 0..d] with rotation + flip applied.
// Dimensioned parts (frames) fill the cell exactly; others scale uniformly.
export function fitGeom(base, dims, rotDeg, part, flip, rz) {
  const [w, h, d] = dims;
  const g = base.clone();
  // Game meshes stored in Z-up; rotate to Y-up for all parts except cockpits.
  // Cockpits are already Y-up in source but face +X; rotate 90° Y to face -Z (forward).
  if (part && part._cockpit) g.rotateY(Math.PI / 2);
  else g.rotateX(-Math.PI / 2);
  if (part && part._meshRot) g.rotateY(part._meshRot * Math.PI / 180);
  const [fx, fy, fz] = flip || [false, false, false];
  if (fx || fy || fz) {
    g.scale(fx ? -1 : 1, fy ? -1 : 1, fz ? -1 : 1);
    if (((fx ? 1 : 0) + (fy ? 1 : 0) + (fz ? 1 : 0)) % 2 === 1) reverseWinding(g);
  }
  let deg = rotDeg || 0;
  if (part && part._dimd) deg = Math.round(deg / 90) * 90;
  if (deg) g.rotateY(deg * Math.PI / 180);
  if (rz) g.rotateX(Math.PI / 2);
  g.computeBoundingBox();
  const bb = g.boundingBox;
  const sp = new THREE.Vector3(); bb.getSize(sp);
  g.translate(-bb.min.x, -bb.min.y, -bb.min.z);
  if (part && part._dimd) {
    g.scale(w / (sp.x || 1), h / (sp.y || 1), d / (sp.z || 1));
  } else {
    const s = Math.min(w / (sp.x || 1), h / (sp.y || 1), d / (sp.z || 1));
    g.scale(s, s, s);
    const [mx, my, mz] = (part && part._meshScale) || [1, 1, 1];
    if (mx !== 1 || my !== 1 || mz !== 1) g.scale(mx, my, mz);
    g.translate((w - sp.x * s * mx) / 2, 0, (d - sp.z * s * mz) / 2);
  }
  return g;
}

// Create a box geometry with origin at min corner [0..w, 0..h, 0..d].
export function boxGeom(w, h, d) {
  const g = new THREE.BoxGeometry(w, h, d);
  g.translate(w / 2, h / 2, d / 2);
  return g;
}

// Material per sub-mesh role (0=paint body, 1=metal, 2=dark, 3=light, 4=emissive, 5=glass).
export function roleMaterial(role, hex, part) {
  let m;
  if (role === 0) {
    const def = (part && part._paintHex) || hex;
    m = new THREE.MeshStandardMaterial({ color: new THREE.Color(def), metalness: 0.55, roughness: 0.5 });
    m.userData = { paint: true, def };
  } else if (role === 4) {
    m = new THREE.MeshStandardMaterial({ color: 0x0c0f13, emissive: new THREE.Color(hex), emissiveIntensity: 1.25, metalness: 0.2, roughness: 0.4 });
    m.userData = {};
  } else if (role === 5) {
    m = new THREE.MeshStandardMaterial({ color: new THREE.Color(hex), metalness: 0.1, roughness: 0.08, transparent: true, opacity: 0.42 });
    m.userData = {};
  } else {
    const pr = role === 1 ? { metalness: 0.9, roughness: 0.42 }
             : role === 2 ? { metalness: 0.35, roughness: 0.72 }
             : role === 3 ? { metalness: 0.25, roughness: 0.6 }
                          : { metalness: 0.45, roughness: 0.5 };
    m = new THREE.MeshStandardMaterial(Object.assign({ color: new THREE.Color(hex) }, pr));
    m.userData = {};
  }
  if (part && part.kind === 'module') m.side = THREE.DoubleSide;
  return m;
}
