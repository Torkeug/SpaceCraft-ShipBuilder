import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PARTS, BYID, GROUPS, loadData, loadStats, statsFor } from './data.js';
import { loadManifest, getMeshKey, loadGeom, fitGeom, boxGeom, roleMaterial, getCached } from './meshLoader.js';

// ── Scene ─────────────────────────────────────────────────────────────────────

const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 2.2;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0a1420);
scene.fog = new THREE.Fog(0x0a1420, 80, 150);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 500);
camera.position.set(20, 16, 20);
camera.lookAt(0, 0, 0);

const controls = new OrbitControls(camera, renderer.domElement);
controls.mouseButtons = { LEFT: null, MIDDLE: null, RIGHT: THREE.MOUSE.ROTATE };
controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
controls.enableDamping = true;
controls.dampingFactor = 0.08;

scene.add(new THREE.AmbientLight(0x86a0c0, 1.3));
const sun = new THREE.DirectionalLight(0xffffff, 2.0); sun.position.set(20, 40, 15); scene.add(sun);
const fill = new THREE.DirectionalLight(0x88bbff, 0.8); fill.position.set(-20, 10, -20); scene.add(fill);

const _gridMat = new THREE.ShaderMaterial({
  uniforms: {
    uPeriod:       { value: 10.0 },
    uCrossThick:   { value: 0.05 },
    uCrossLen:     { value: 0.22 },
    uDiamondSize:  { value: 0.40 },
    uDiamondThick: { value: 0.09 },
    uMinorAlpha:   { value: 0.45 },
    uMajorAlpha:   { value: 0.80 },
    uFade:         { value: 50.0 },
  },
  vertexShader: `
    varying vec2 vW;
    void main() {
      vW = (modelMatrix * vec4(position, 1.0)).xz;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  fragmentShader: `
    uniform float uPeriod, uCrossThick, uCrossLen;
    uniform float uDiamondSize, uDiamondThick, uMinorAlpha, uMajorAlpha, uFade;
    varying vec2 vW;
    void main() {
      float fade = 1.0 - smoothstep(uFade * 0.5, uFade, length(vW));
      if (fade < 0.001) discard;

      // Nearest 1-unit grid point
      vec2 snap1 = round(vW);
      // Position of that grid point within the 10-unit period
      vec2 pm = mod(snap1 + 1000.0 * uPeriod, uPeriod);
      // Skip: diamond lines (pm == 0) and mid-gap lines (pm == 5)
      float onDiamondLine = max(step(pm.x, 0.1), step(pm.y, 0.1));
      float onMidLine     = max(step(abs(pm.x - uPeriod * 0.5), 0.1),
                                step(abs(pm.y - uPeriod * 0.5), 0.1));
      float canCross = (1.0 - onDiamondLine) * (1.0 - onMidLine);

      // + cross shape around snap1
      vec2 d1 = vW - snap1;
      float cross = max(
        step(abs(d1.x), uCrossThick) * step(abs(d1.y), uCrossLen),
        step(abs(d1.y), uCrossThick) * step(abs(d1.x), uCrossLen)
      ) * canCross;

      // 4 isosceles trapezoid segments at nearest 10-unit grid point.
      // Each is a square side with 45°-beveled ends and a gap at each corner.
      vec2 snap10 = round(vW / uPeriod) * uPeriod;
      vec2 d10 = vW - snap10;
      float R = uDiamondSize;
      float T = uDiamondThick;
      float hLen = R * 0.68;
      vec2 rd = vec2(d10.x + d10.y, d10.x - d10.y) * 0.7071;
      float dyT = R - rd.y;
      float dyB = rd.y + R;
      float dxR = R - rd.x;
      float dxL = rd.x + R;
      float top   = step(0.0,dyT)*step(dyT,T)*step(abs(rd.x)+dyT,hLen);
      float bot   = step(0.0,dyB)*step(dyB,T)*step(abs(rd.x)+dyB,hLen);
      float right = step(0.0,dxR)*step(dxR,T)*step(abs(rd.y)+dxR,hLen);
      float left  = step(0.0,dxL)*step(dxL,T)*step(abs(rd.y)+dxL,hLen);
      float diamond = max(max(top,bot),max(right,left));

      float alpha = max(cross * uMinorAlpha, diamond * uMajorAlpha) * fade;
      if (alpha < 0.001) discard;
      gl_FragColor = vec4(0.28, 0.48, 0.68, alpha);
    }
  `,
  transparent: true,
  depthWrite: false,
  side: THREE.DoubleSide,
});
const _gridMesh = new THREE.Mesh(new THREE.PlaneGeometry(200, 200), _gridMat);
_gridMesh.rotation.x = -Math.PI / 2;
_gridMesh.position.y = -0.001;
scene.add(_gridMesh);

// Forward direction marker
const buildPlane = new THREE.Mesh(
  new THREE.PlaneGeometry(500, 500),
  new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide })
);
buildPlane.rotation.x = -Math.PI / 2;
scene.add(buildPlane);

// ── Ghost ─────────────────────────────────────────────────────────────────────

const ghostMatOk  = new THREE.MeshPhongMaterial({ color: 0x4488cc, transparent: true, opacity: 0.4, depthWrite: false });
const ghostMatBad = new THREE.MeshPhongMaterial({ color: 0xcc3333, transparent: true, opacity: 0.4, depthWrite: false });
const ghost = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), ghostMatOk);
ghost.visible = false;
scene.add(ghost);

// Selection highlight — edges of the inspected part's bounding box.
const selOutline = new THREE.LineSegments(
  new THREE.EdgesGeometry(new THREE.BoxGeometry(1, 1, 1)),
  new THREE.LineBasicMaterial({ color: 0x4a9abb, transparent: true, opacity: 0.55, depthTest: true })
);
selOutline.visible = false;
selOutline.renderOrder = 2;
scene.add(selOutline);

function updateSelOutline() {
  const e = state.inspected;
  if (!e || e.slotOwner) { selOutline.visible = false; return; }
  selOutline.geometry.dispose();
  selOutline.geometry = new THREE.EdgesGeometry(boxGeom(...e.dims));
  selOutline.position.set(e.gx, e.gy, e.gz);
  selOutline.visible = true;
}

// ── State ─────────────────────────────────────────────────────────────────────

const state = {
  placed: [],
  selected: null,   // part from BYID
  inspected: null,  // placed entry
  groupSel: new Set(),
  shapeIdx: 0,
  rotDeg: 0,
  mx: false, my: false, mz: false, rz: false,
  eraseMode: false,
  ghostGx: null, ghostGy: null, ghostGz: null,
};

let partRot = {}, partFlip = {};
try { partRot  = JSON.parse(localStorage.getItem('sc_partRotDeg') || '{}'); } catch (e) {}
try { partFlip = JSON.parse(localStorage.getItem('sc_partFlip')   || '{}'); } catch (e) {}
function flipOf(id) { return partFlip[id] || [false, false, false]; }

// Entries currently being dragged; excluded from AABB collision checks so a
// piece doesn't block its own placement while it's in motion.
const _dragging = new Set();

// ── Helpers ───────────────────────────────────────────────────────────────────

// Dims with rotations applied: Y 90° swaps X↔Z; Z 90° swaps X↔Y.
function effDims(dims, rotDeg, rz) {
  let [w, h, d] = dims;
  const q = Math.round((((rotDeg || 0) % 360) + 360) % 360 / 90) % 2;
  if (q) [w, d] = [d, w];
  if (rz) [h, d] = [d, h];
  return [w, h, d];
}

// Inside modules sit in 1×1×1 hull slots.
function isInsideMod(part) { return part && part.kind === 'module' && part.mount === 'inside'; }

function _mkSlotTex(occupied, hover) {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const x = c.getContext('2d');
  if (hover) {
    x.fillStyle = 'rgba(255,255,255,0.15)'; x.fillRect(0, 0, 64, 64);
    x.strokeStyle = '#ffffff'; x.lineWidth = 5; x.strokeRect(5, 5, 54, 54);
    x.fillStyle = 'rgba(255,255,255,0.45)'; x.fillRect(21, 21, 22, 22);
  } else if (occupied) {
    x.fillStyle = 'rgba(0,212,255,0.18)'; x.fillRect(0, 0, 64, 64);
    x.strokeStyle = '#00d4ff'; x.lineWidth = 4; x.strokeRect(6, 6, 52, 52);
    x.fillStyle = '#00d4ff'; x.fillRect(22, 22, 20, 20);
  } else {
    x.strokeStyle = 'rgba(255,255,255,0.75)'; x.lineWidth = 4;
    x.setLineDash([10, 5]); x.strokeRect(6, 6, 52, 52);
  }
  const t = new THREE.CanvasTexture(c); t.needsUpdate = true; return t;
}
const TEX_SLOT_EMPTY    = _mkSlotTex(false, false);
const TEX_SLOT_OCCUPIED = _mkSlotTex(true,  false);
const TEX_SLOT_HOVER    = _mkSlotTex(false, true);
const TEX_SLOT_HOVER_SWAP = (() => {
  const c = document.createElement('canvas'); c.width = c.height = 64;
  const x = c.getContext('2d');
  x.fillStyle = 'rgba(245,166,35,0.18)'; x.fillRect(0, 0, 64, 64);
  x.strokeStyle = '#f5a623'; x.lineWidth = 5; x.strokeRect(5, 5, 54, 54);
  x.fillStyle = 'rgba(245,166,35,0.5)'; x.fillRect(21, 21, 22, 22);
  const t = new THREE.CanvasTexture(c); t.needsUpdate = true; return t;
})();
const _slotSprites = new Map();
const _slotIconCache = new Map();
const _slotHoverCache = new Map();
let   _hoveredSlot = null;

function getSlotOccupiedTex(part) {
  if (_slotIconCache.has(part.id)) return _slotIconCache.get(part.id);
  const size = 64;
  const c = document.createElement('canvas'); c.width = c.height = size;
  const ctx = c.getContext('2d');
  const tex = new THREE.CanvasTexture(c);
  _slotIconCache.set(part.id, tex);
  function draw(img) {
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = 'rgba(0,0,0,0.55)'; ctx.fillRect(0, 0, size, size);
    if (img) ctx.drawImage(img, 5, 5, 54, 54);
    ctx.strokeStyle = '#00d4ff'; ctx.lineWidth = 3; ctx.strokeRect(2, 2, 60, 60);
    tex.needsUpdate = true;
  }
  draw(null);
  const img = new Image();
  img.onload = () => draw(img);
  img.src = `ship_icons/${part.id}.webp`;
  return tex;
}

function getSlotHoverTex(part, isSwap) {
  const key = `${part.id}_${isSwap ? 1 : 0}`;
  if (_slotHoverCache.has(key)) return _slotHoverCache.get(key);
  const size = 64;
  const c = document.createElement('canvas'); c.width = c.height = size;
  const ctx = c.getContext('2d');
  const tex = new THREE.CanvasTexture(c);
  _slotHoverCache.set(key, tex);
  const border = isSwap ? '#f5a623' : '#ffffff';
  const bg     = isSwap ? 'rgba(245,166,35,0.15)' : 'rgba(255,255,255,0.12)';
  function draw(img) {
    ctx.clearRect(0, 0, size, size);
    ctx.fillStyle = bg; ctx.fillRect(0, 0, size, size);
    if (img) ctx.drawImage(img, 5, 5, 54, 54);
    ctx.strokeStyle = border; ctx.lineWidth = 4; ctx.strokeRect(3, 3, 58, 58);
    tex.needsUpdate = true;
  }
  draw(null);
  const img = new Image();
  img.onload = () => draw(img);
  img.src = `ship_icons/${part.id}.webp`;
  return tex;
}

function partDims(part) {
  if (isInsideMod(part)) return [1, 1, 1];
  if (part._cockpit) {
    const [l, h, w] = part.dims;
    return [w, h, l];   // fitGeom applies 90°Y, which swaps X↔Z extents
  }
  const [l, w, h] = part.dims;
  return [l, h, w];     // game LxWxH → Three.js X,Y,Z
}

function isFree(gx, gy, gz, dims) {
  const [elx, ely, elz] = dims;
  for (const entry of state.placed) {
    if (_dragging.has(entry) || isInsideMod(entry.part)) continue;
    const [ex, ey, ez] = entry.dims;
    if (gx < entry.gx + ex && gx + elx > entry.gx &&
        gy < entry.gy + ey && gy + ely > entry.gy &&
        gz < entry.gz + ez && gz + elz > entry.gz) return false;
  }
  return true;
}

function stackHeight(gx, gz, dims, exclude = null) {
  const [elx, , elz] = dims;
  let top = null;
  for (const entry of state.placed) {
    if (entry === exclude) continue;
    const [exl, eyl, ezl] = entry.dims;
    if (gx < entry.gx + exl && gx + elx > entry.gx && gz < entry.gz + ezl && gz + elz > entry.gz) {
      const t = entry.gy + eyl;
      if (top === null || t > top) top = t;
    }
  }
  return top ?? 0;
}

function stackDepth(gx, gz, dims, exclude = null) {
  const [elx, ely, elz] = dims;
  let bottom = null;
  for (const entry of state.placed) {
    if (entry === exclude) continue;
    const [exl, , ezl] = entry.dims;
    if (gx < entry.gx + exl && gx + elx > entry.gx && gz < entry.gz + ezl && gz + elz > entry.gz) {
      const b = entry.gy - ely;
      if (bottom === null || b < bottom) bottom = b;
    }
  }
  return bottom ?? -ely;
}

// ── Mesh building ─────────────────────────────────────────────────────────────

// Build a mesh synchronously (box fallback if real mesh not cached yet).
// Geometry origin is at min corner [0..w, 0..h, 0..d].
function buildPartMesh(part, dims, meshKey, rotDeg, mx, my, mz, rz) {
  const [w, h, d] = dims;

  if (isInsideMod(part)) {
    // Inside modules are represented by the slot sprite; use an invisible mesh as anchor.
    const g = boxGeom(w, h, d);
    const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ visible: false }));
    return { mesh, edges: null };
  }

  const cached = meshKey ? getCached(meshKey) : null;
  if (cached) {
    const g = fitGeom(cached.geom, dims, rotDeg, part, [mx, my, mz], rz);
    const mats = cached.groups.map(gr => roleMaterial(gr.role, gr.hex, part));
    return { mesh: new THREE.Mesh(g, mats), edges: null, needsSwap: false };
  }

  // Box fallback while mesh loads.
  const g = boxGeom(w, h, d);
  const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: new THREE.Color(part._paintHex || '#5e7ca2'), metalness: 0.5, roughness: 0.5 }));
  const edges = new THREE.LineSegments(new THREE.EdgesGeometry(g, 15), new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.35 }));
  return { mesh, edges, needsSwap: !!meshKey };
}

// Swap a placed entry's box fallback for the real mesh once it loads.
function swapToRealMesh(entry, cached) {
  if (!state.placed.includes(entry)) return;
  scene.remove(entry.mesh);
  if (entry.edges) { scene.remove(entry.edges); entry.edges.geometry.dispose(); entry.edges = null; }
  disposeMesh(entry.mesh);
  const g = fitGeom(cached.geom, entry.dims, entry.rotDeg, entry.part, [entry.mx, entry.my, entry.mz], entry.rz);
  const mats = cached.groups.map(gr => roleMaterial(gr.role, gr.hex, entry.part));
  const mesh = new THREE.Mesh(g, mats);
  mesh.position.set(entry.gx, entry.gy, entry.gz);
  mesh._entry = entry;
  scene.add(mesh);
  entry.mesh = mesh;
}

// Kick off async mesh load + swap for an entry.
function asyncLoad(entry) {
  const mk = entry.meshKey;
  if (!mk || isInsideMod(entry.part)) return;
  loadGeom(mk).then(cached => { if (cached) swapToRealMesh(entry, cached); });
}

function disposeMesh(mesh) {
  mesh.geometry.dispose();
  const m = mesh.material;
  if (Array.isArray(m)) m.forEach(x => x.dispose()); else if (m) m.dispose();
}

// ── Ghost management ──────────────────────────────────────────────────────────

// Update ghost geometry — real mesh if cached, box fallback while loading.
function refreshGhostGeo() {
  if (!state.selected) { ghost.visible = false; return; }
  const part = state.selected;
  const dims = effDims(partDims(part), state.rotDeg, state.rz);
  const mk = getMeshKey(part, state.shapeIdx);
  const cached = mk ? getCached(mk) : null;
  ghost.geometry.dispose();
  if (cached) {
    ghost.geometry = fitGeom(cached.geom, dims, state.rotDeg, part, [state.mx, state.my, state.mz], state.rz);
  } else {
    ghost.geometry = boxGeom(...dims);
    if (mk) loadGeom(mk).then(c => {
      if (c && state.selected === part && getMeshKey(part, state.shapeIdx) === mk) refreshGhostGeo();
    });
  }
}

function clearGhost() { ghost.visible = false; state.ghostGx = null; state.ghostGy = null; state.ghostGz = null; }

function positionGhost(gx, gy, gz) {
  const dims = state.selected ? effDims(partDims(state.selected), state.rotDeg, state.rz) : [1, 1, 1];
  ghost.material = isFree(gx, gy, gz, dims) ? ghostMatOk : ghostMatBad;
  ghost.position.set(gx, gy, gz);
  ghost.visible = true;
  state.ghostGx = gx; state.ghostGy = gy; state.ghostGz = gz;
}

// Update ghost geo to match the entry being dragged.
function refreshGhostForDrag(entry) {
  ghost.geometry.dispose();
  const cached = entry.meshKey ? getCached(entry.meshKey) : null;
  ghost.geometry = cached
    ? fitGeom(cached.geom, entry.dims, entry.rotDeg, entry.part, [entry.mx, entry.my, entry.mz], entry.rz)
    : boxGeom(...entry.dims);
}

// ── Placement ─────────────────────────────────────────────────────────────────

function placePiece(gx, gy, gz) {
  if (!state.selected || state.eraseMode) return;
  const part = state.selected;
  if (isInsideMod(part)) return;
  const dims = effDims(partDims(part), state.rotDeg, state.rz);
  if (!isFree(gx, gy, gz, dims)) return;
  const meshKey = getMeshKey(part, state.shapeIdx);
  const { mesh, edges, needsSwap } = buildPartMesh(part, dims, meshKey, state.rotDeg, state.mx, state.my, state.mz, state.rz);
  mesh.position.set(gx, gy, gz);
  if (edges) { edges.position.set(gx, gy, gz); scene.add(edges); }
  scene.add(mesh);
  const entry = { part, shapeIdx: state.shapeIdx, meshKey, rotDeg: state.rotDeg, dims, mx: state.mx, my: state.my, mz: state.mz, rz: state.rz, gx, gy, gz, mesh, edges };
  mesh._entry = entry;
  entry.hitMesh = makeHitMesh(dims, gx, gy, gz, entry);
  scene.add(entry.hitMesh);
  state.placed.push(entry);
  if (needsSwap) asyncLoad(entry);
  state.selected = null;
  state.inspected = entry;
  clearGhost();
  buildPalette(document.getElementById('search').value);
  updateInspector();
  updateShipStats();
  refreshSlotSprites();
}

// Used by load/undo — place without consuming state.selected.
function placePieceDirect(part, gx, gy, gz, shapeIdx, rotDeg, mx, my, mz, rz) {
  const dims = effDims(partDims(part), rotDeg, rz);
  const meshKey = getMeshKey(part, shapeIdx);
  const { mesh, edges, needsSwap } = buildPartMesh(part, dims, meshKey, rotDeg, mx, my, mz, rz);
  mesh.position.set(gx, gy, gz);
  if (edges) { edges.position.set(gx, gy, gz); scene.add(edges); }
  scene.add(mesh);
  const entry = { part, shapeIdx, meshKey, rotDeg, dims, mx, my, mz, rz: rz || false, gx, gy, gz, mesh, edges };
  mesh._entry = entry;
  entry.hitMesh = makeHitMesh(dims, gx, gy, gz, entry);
  scene.add(entry.hitMesh);
  state.placed.push(entry);
  if (needsSwap) asyncLoad(entry);
  return entry;
}

function rebuildPlacedMesh(entry, shapeIdx, rotDeg, mx, my, mz, rz) {
  scene.remove(entry.mesh);
  if (entry.edges) { scene.remove(entry.edges); entry.edges.geometry.dispose(); entry.edges = null; }
  disposeMesh(entry.mesh);
  if (entry.hitMesh) { scene.remove(entry.hitMesh); entry.hitMesh.geometry.dispose(); }
  const dims = effDims(partDims(entry.part), rotDeg, rz);
  const meshKey = getMeshKey(entry.part, shapeIdx);
  entry.shapeIdx = shapeIdx; entry.rotDeg = rotDeg; entry.dims = dims;
  entry.meshKey = meshKey; entry.mx = mx; entry.my = my; entry.mz = mz; entry.rz = rz || false;
  const { mesh, edges, needsSwap } = buildPartMesh(entry.part, dims, meshKey, rotDeg, mx, my, mz, rz);
  mesh.position.set(entry.gx, entry.gy, entry.gz);
  if (edges) { edges.position.set(entry.gx, entry.gy, entry.gz); scene.add(edges); }
  mesh._entry = entry;
  scene.add(mesh);
  entry.mesh = mesh; entry.edges = edges;
  entry.hitMesh = makeHitMesh(dims, entry.gx, entry.gy, entry.gz, entry);
  scene.add(entry.hitMesh);
  if (needsSwap) asyncLoad(entry);
  syncSlotModule(entry);
  refreshSlotSprites();
}

// ── Removal ───────────────────────────────────────────────────────────────────

function removeEntry(entry) {
  if (entry.part.kind === 'build') {
    const occupant = state.placed.find(e => e.slotOwner === entry);
    if (occupant) removeEntry(occupant);
  }
  const spr = _slotSprites.get(entry);
  if (spr) { scene.remove(spr); spr.material.dispose(); _slotSprites.delete(entry); }
  scene.remove(entry.mesh);
  if (entry.edges) { scene.remove(entry.edges); entry.edges.geometry.dispose(); }
  disposeMesh(entry.mesh);
  if (entry.hitMesh) { scene.remove(entry.hitMesh); entry.hitMesh.geometry.dispose(); }
  if (state.inspected === entry) { state.inspected = null; updateInspector(); }
  const idx = state.placed.indexOf(entry);
  if (idx >= 0) state.placed.splice(idx, 1);
  updateShipStats();
}

function clearAll() {
  state.placed.slice().forEach(removeEntry);
  clearGhost();
  if (state.inspected) { state.inspected = null; updateInspector(); }
}

// ── Module slot sprites ───────────────────────────────────────────────────────

function refreshSlotSprites() {
  for (const [entry, spr] of _slotSprites) {
    if (!state.placed.includes(entry)) {
      scene.remove(spr); spr.material.dispose(); _slotSprites.delete(entry);
    }
  }
  const show = paletteTab === 'module' || slotDrag.active;
  for (const entry of state.placed) {
    if (entry.part.kind !== 'build') continue;
    const occupant = state.placed.find(e => e.slotOwner === entry && e !== slotDrag.entry);
    const [w, h, d] = entry.dims;
    if (!_slotSprites.has(entry)) {
      const mat = new THREE.SpriteMaterial({ map: TEX_SLOT_EMPTY, depthTest: false, transparent: true });
      const spr = new THREE.Sprite(mat);
      spr.renderOrder = 1;
      spr._hullEntry = entry;
      scene.add(spr);
      _slotSprites.set(entry, spr);
    }
    const spr = _slotSprites.get(entry);
    spr.visible = show;
    spr.position.set(entry.gx + w / 2, entry.gy + h + 0.3, entry.gz + d / 2);
    const isHov = entry === _hoveredSlot;
    const selMod = isHov && state.selected && isInsideMod(state.selected) ? state.selected : null;
    spr.material.map = isHov
      ? (selMod ? getSlotHoverTex(selMod, !!occupant) : (occupant ? TEX_SLOT_HOVER_SWAP : TEX_SLOT_HOVER))
      : (occupant ? getSlotOccupiedTex(occupant.part) : TEX_SLOT_EMPTY);
    spr.scale.setScalar(isHov ? 0.75 : 0.65);
  }
}

function setSlotHighlight(hullEntry, on) {
  _hoveredSlot = on ? hullEntry : null;
  const spr = _slotSprites.get(hullEntry);
  if (!spr) return;
  const occupant = state.placed.find(e => e.slotOwner === hullEntry);
  const selMod = on && state.selected && isInsideMod(state.selected) ? state.selected : null;
  spr.material.map = on
    ? (selMod ? getSlotHoverTex(selMod, !!occupant) : (occupant ? TEX_SLOT_HOVER_SWAP : TEX_SLOT_HOVER))
    : (occupant ? getSlotOccupiedTex(occupant.part) : TEX_SLOT_EMPTY);
  spr.scale.setScalar(on ? 0.75 : 0.65);
}

function syncSlotModule(hullEntry) {
  const occupant = state.placed.find(e => e.slotOwner === hullEntry);
  if (!occupant) return;
  const [w, h, d] = hullEntry.dims;
  const gx = Math.round((hullEntry.gx + (w - 1) / 2) * 2) / 2;
  const gy = Math.round((hullEntry.gy + (h - 1) / 2) * 2) / 2;
  const gz = Math.round((hullEntry.gz + (d - 1) / 2) * 2) / 2;
  occupant.gx = gx; occupant.gy = gy; occupant.gz = gz;
  occupant.mesh.position.set(gx, gy, gz);
  if (occupant.hitMesh) occupant.hitMesh.position.set(gx, gy, gz);
}

function placeInSlot(part, hullEntry) {
  if (!hullEntry || hullEntry.part.kind !== 'build') return;
  const existing = state.placed.find(e => e.slotOwner === hullEntry);
  if (existing) removeEntry(existing);
  const [w, h, d] = hullEntry.dims;
  const gx = Math.round((hullEntry.gx + (w - 1) / 2) * 2) / 2;
  const gy = Math.round((hullEntry.gy + (h - 1) / 2) * 2) / 2;
  const gz = Math.round((hullEntry.gz + (d - 1) / 2) * 2) / 2;
  const dims = [1, 1, 1];
  const { mesh } = buildPartMesh(part, dims, null, 0, false, false, false);
  mesh.position.set(gx, gy, gz);
  scene.add(mesh);
  const entry = { part, shapeIdx: 0, meshKey: null, rotDeg: 0, dims, mx: false, my: false, mz: false, gx, gy, gz, mesh, edges: null, slotOwner: hullEntry };
  mesh._entry = entry;
  entry.hitMesh = makeHitMesh(dims, gx, gy, gz, entry);
  scene.add(entry.hitMesh);
  state.placed.push(entry);
  if (_hoveredSlot) _hoveredSlot = null;
  hideModCursor();
  state.selected = null;
  state.inspected = entry;
  clearGhost();
  buildPalette(document.getElementById('search').value);
  updateInspector();
  updateShipStats();
  refreshSlotSprites();
}

// ── Raycaster ─────────────────────────────────────────────────────────────────

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const _midPan = { active: false, lastX: 0, lastY: 0 };

function updatePointer(e) {
  const r = renderer.domElement.getBoundingClientRect();
  pointer.x =  ((e.clientX - r.left) / r.width)  * 2 - 1;
  pointer.y = -((e.clientY - r.top)  / r.height) * 2 + 1;
}

function snapH(v) { return Math.round(v * 1.5) / 1.5; }        // X: 2/3-unit grid (8 units → 13 pts)
function snapV(v) { return Math.round(v * 2)   / 2;   }        // Y: 1/2-unit grid (2 units → 5 pts)
function snapZ(v) { return Math.round(v / 0.75) * 0.75; }      // Z: 3/4-unit grid (3 units → 5 pts)

// Invisible axis-aligned box used for raycasting instead of the visual mesh,
// so face normals are always perfectly axis-aligned regardless of mesh detail.
const _hitMat = new THREE.MeshBasicMaterial({ visible: false });
function makeHitMesh(dims, gx, gy, gz, entry) {
  const hm = new THREE.Mesh(boxGeom(...dims), _hitMat);
  hm.position.set(gx, gy, gz);
  hm._entry = entry;
  return hm;
}

// Return {gx, gy, gz} for the current pointer. baseDims = unrotated part dims.
function getGridPos(baseDims = null, excludeEntry = null, rotDeg = state.rotDeg) {
  baseDims = baseDims ?? partDims(state.selected) ?? [1, 1, 1];
  const rz = excludeEntry ? excludeEntry.rz : state.rz;
  const [elx, ely, elz] = effDims(baseDims, rotDeg, rz);
  raycaster.setFromCamera(pointer, camera);

  const go = (excludeEntry && drag.grabOffset) ? drag.grabOffset : { x: elx / 2, y: ely / 2, z: elz / 2 };
  let gx, gy, gz;
  const hitMeshes = state.placed.filter(p => p !== excludeEntry).map(p => p.hitMesh).filter(Boolean);
  if (hitMeshes.length) {
    const meshHits = raycaster.intersectObjects(hitMeshes);
    if (meshHits.length) {
      const hit = meshHits[0];
      const entry = hit.object._entry ?? state.placed.find(p => p.hitMesh === hit.object);
      if (entry) {
        const n = hit.face.normal.clone().transformDirection(hit.object.matrixWorld);
        n.x = Math.round(n.x); n.y = Math.round(n.y); n.z = Math.round(n.z);
        const p = hit.point;
        const [exl, eyl, ezl] = entry.dims;
        if (n.y < 0) {
          gx = snapH(p.x - go.x); gz = snapZ(p.z - go.z); gy = entry.gy - ely;
        } else if (n.y > 0) {
          gx = snapH(p.x - go.x); gy = entry.gy + eyl; gz = snapZ(p.z - go.z);
        } else {
          gx = n.x !== 0 ? (n.x > 0 ? entry.gx + exl : entry.gx - elx) : snapH(p.x - go.x);
          gy = snapV(p.y - go.y);
          gz = n.z !== 0 ? (n.z > 0 ? entry.gz + ezl : entry.gz - elz) : snapZ(p.z - go.z);
        }
      }
    }
  }

  if (gx == null) {
    const camDir = new THREE.Vector3(); camera.getWorldDirection(camDir);
    const hLen = Math.sqrt(camDir.x * camDir.x + camDir.z * camDir.z);
    if (!excludeEntry && (hLen < 0.5 || state.placed.length === 0)) {
      // New placement, top-down or bottom-up: cast against the ground plane.
      const planeHits = raycaster.intersectObject(buildPlane);
      if (!planeHits.length) return null;
      const p = planeHits[0].point;
      gx = snapH(p.x - go.x); gz = snapZ(p.z - go.z);
      gy = camDir.y < 0
        ? stackHeight(gx, gz, [elx, ely, elz], excludeEntry)
        : stackDepth(gx, gz, [elx, ely, elz], excludeEntry);
    } else {
      // Side view or drag: cast against a plane facing the camera.
      // Top-down drag uses a horizontal plane at the piece's height;
      // side view uses a vertical plane through the reference point.
      let snapPlane;
      if (excludeEntry && hLen < 0.5) {
        snapPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -(excludeEntry.gy + ely / 2));
      } else {
        const hDir = new THREE.Vector3(camDir.x, 0, camDir.z).normalize();
        const ref = excludeEntry
          ? new THREE.Vector3(excludeEntry.gx + elx / 2, excludeEntry.gy + ely / 2, excludeEntry.gz + elz / 2)
          : controls.target;
        snapPlane = new THREE.Plane().setFromNormalAndCoplanarPoint(hDir, ref);
      }
      const t = new THREE.Vector3();
      if (!raycaster.ray.intersectPlane(snapPlane, t)) return null;
      const raw_gx = snapH(t.x - go.x), raw_gz = snapZ(t.z - go.z);
      gx = raw_gx; gz = raw_gz;
      gy = (excludeEntry && hLen < 0.5) ? excludeEntry.gy : snapV(t.y - go.y);
      // Snap X or Z to the nearest overlapping piece edge.
      let best = null, bestMin = Infinity;
      for (const entry of state.placed) {
        if (entry === excludeEntry) continue;
        const [exl, , ezl] = entry.dims;
        const xo = Math.min(raw_gx + elx, entry.gx + exl) - Math.max(raw_gx, entry.gx);
        const zo = Math.min(raw_gz + elz, entry.gz + ezl) - Math.max(raw_gz, entry.gz);
        if (xo < 0 || zo < 0) continue;
        const m = Math.min(xo, zo);
        if (m < bestMin) { bestMin = m; best = { entry, xo, zo }; }
      }
      if (best) {
        const { entry, xo, zo } = best;
        const [exl, , ezl] = entry.dims;
        if (xo <= zo) {
          gx = (raw_gx + elx / 2) < (entry.gx + exl / 2) ? entry.gx - elx : entry.gx + exl;
        } else {
          gz = (raw_gz + elz / 2) < (entry.gz + ezl / 2) ? entry.gz - elz : entry.gz + ezl;
        }
      }
    }
  }

  return { gx, gy, gz };
}

// ── Pointer events ────────────────────────────────────────────────────────────

const drag = { pending: null, active: false, entry: null, gx: null, gy: null, gz: null, grabOffset: null };
const groupDrag = { pending: null, active: false, anchorEntry: null, startPos: new Map(), gx: null, gy: null, gz: null };
const marquee = { active: false, x0: 0, y0: 0 };
const slotDrag = { pending: null, active: false, entry: null, origOwner: null, _prevSelected: null };
let _pDown = null;

// Marquee selection rectangle (DOM overlay)
const marqueeEl = document.createElement('div');
Object.assign(marqueeEl.style, { display: 'none', position: 'fixed', border: '1px solid #95faf3',
  background: 'rgba(149,250,243,0.07)', pointerEvents: 'none', zIndex: '999' });
document.body.appendChild(marqueeEl);
function showMarquee(x0, y0, x1, y1) {
  Object.assign(marqueeEl.style, { display: 'block',
    left: Math.min(x0,x1)+'px', top: Math.min(y0,y1)+'px',
    width: Math.abs(x1-x0)+'px', height: Math.abs(y1-y0)+'px' });
}

const _modCursorEl = document.createElement('div');
Object.assign(_modCursorEl.style, {
  display: 'none', position: 'fixed', pointerEvents: 'none', zIndex: '1000',
  width: '48px', height: '48px', borderRadius: '6px',
  border: '2px solid rgba(0,212,255,0.7)', background: 'rgba(0,0,0,0.55)',
  transform: 'translate(-50%,-50%)', boxShadow: '0 0 12px rgba(0,212,255,0.4)',
});
const _modCursorImg = document.createElement('img');
Object.assign(_modCursorImg.style, { width: '100%', height: '100%', display: 'block', borderRadius: '4px' });
_modCursorEl.appendChild(_modCursorImg);
document.body.appendChild(_modCursorEl);
function showModCursor(part, x, y) {
  _modCursorImg.src = `ship_icons/${part.id}.webp`;
  _modCursorEl.style.left = x + 'px';
  _modCursorEl.style.top  = y + 'px';
  _modCursorEl.style.display = 'block';
}
function hideModCursor() { _modCursorEl.style.display = 'none'; }

// Group selection highlight outlines (one LineSegments per selected entry)
const _groupOutlines = [];
function updateGroupOutlines() {
  _groupOutlines.forEach(o => { o.geometry.dispose(); scene.remove(o); });
  _groupOutlines.length = 0;
  for (const en of state.groupSel) {
    if (en.slotOwner) continue;
    const ol = new THREE.LineSegments(
      new THREE.EdgesGeometry(boxGeom(...en.dims)),
      new THREE.LineBasicMaterial({ color: 0x95faf3, transparent: true, opacity: 0.7, depthTest: true })
    );
    ol.position.set(en.gx, en.gy, en.gz);
    ol.renderOrder = 2;
    scene.add(ol);
    _groupOutlines.push(ol);
  }
}

function isFreeForGroup(dgx, dgy, dgz) {
  for (const [en, s] of groupDrag.startPos)
    if (!isFree(s.gx + dgx, s.gy + dgy, s.gz + dgz, en.dims)) return false;
  return true;
}

// Extra ghost meshes for non-anchor group members during group drag
const _groupGhosts = [];
function buildGroupGhosts() {
  clearGroupGhosts();
  const anchor = groupDrag.anchorEntry;
  for (const en of state.groupSel) {
    if (en.slotOwner || en === anchor) continue;
    const cached = en.meshKey ? getCached(en.meshKey) : null;
    const geom = cached ? fitGeom(cached.geom, en.dims, en.rotDeg, en.part, [en.mx, en.my, en.mz], en.rz) : boxGeom(...en.dims);
    const m = new THREE.Mesh(geom, ghostMatOk);
    m.position.set(en.gx, en.gy, en.gz);
    m._groupEntry = en;
    scene.add(m);
    _groupGhosts.push(m);
  }
}
function updateGroupGhostPositions(dgx, dgy, dgz, mat) {
  for (const m of _groupGhosts) {
    const s = groupDrag.startPos.get(m._groupEntry);
    if (s) m.position.set(s.gx + dgx, s.gy + dgy, s.gz + dgz);
    m.material = mat;
  }
}
function clearGroupGhosts() {
  _groupGhosts.forEach(m => { m.geometry.dispose(); scene.remove(m); });
  _groupGhosts.length = 0;
}

function clearGroupSel() {
  state.groupSel.clear();
  updateGroupOutlines();
}

renderer.domElement.addEventListener('pointerdown', e => {
  if (e.button === 1) {
    e.preventDefault();
    _midPan.active = true; _midPan.lastX = e.clientX; _midPan.lastY = e.clientY;
    return;
  }
  _pDown = { x: e.clientX, y: e.clientY, btn: e.button };
  if (e.button !== 0 || state.eraseMode || state.selected) return;
  updatePointer(e);
  raycaster.setFromCamera(pointer, camera);
  // Sprite-based slot drag initiation (Module tab only, where sprites are visible).
  if (paletteTab === 'module') {
    const visSprites = [..._slotSprites.values()].filter(s => s.visible);
    if (visSprites.length) {
      const sprHits = raycaster.intersectObjects(visSprites);
      if (sprHits.length) {
        const hullEntry = sprHits[0].object._hullEntry;
        const occupant = hullEntry ? state.placed.find(e => e.slotOwner === hullEntry) : null;
        if (occupant) { slotDrag.pending = { entry: occupant, x: e.clientX, y: e.clientY }; return; }
      }
    }
  }
  const hits = raycaster.intersectObjects(state.placed.map(p => p.mesh));
  if (!hits.length) {
    // Start marquee selection on empty canvas space
    marquee.active = true; marquee.x0 = e.clientX; marquee.y0 = e.clientY;
    if (!e.shiftKey) clearGroupSel();
    return;
  }
  const entry = hits[0].object._entry ?? state.placed.find(p => p.mesh === hits[0].object);
  if (!entry || entry.slotOwner) return;
  if (state.groupSel.size > 1 && state.groupSel.has(entry)) {
    // Initiate group drag
    groupDrag.pending = { entry, x: e.clientX, y: e.clientY, hitPoint: hits[0].point.clone() };
  } else {
    if (!e.shiftKey) clearGroupSel();
    drag.pending = { entry, x: e.clientX, y: e.clientY, hitPoint: hits[0].point.clone() };
  }
});

renderer.domElement.addEventListener('pointermove', e => {
  if (_midPan.active) {
    const dx = e.clientX - _midPan.lastX, dy = e.clientY - _midPan.lastY;
    _midPan.lastX = e.clientX; _midPan.lastY = e.clientY;
    if (dx || dy) {
      const dist = camera.position.distanceTo(controls.target);
      const scale = 2 * (2 * dist * Math.tan(camera.fov * Math.PI / 360)) / renderer.domElement.clientHeight;
      const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0); right.y = 0; right.normalize();
      const fwd   = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 2).negate(); fwd.y = 0; fwd.normalize();
      const fwdSign = camera.position.y < controls.target.y ? -1 : 1;
      camera.position.addScaledVector(right, -dx * scale);
      camera.position.addScaledVector(fwd,   dy * scale * fwdSign);
      controls.target.addScaledVector(right, -dx * scale);
      controls.target.addScaledVector(fwd,   dy * scale * fwdSign);
    }
    return;
  }
  if (marquee.active) {
    showMarquee(marquee.x0, marquee.y0, e.clientX, e.clientY);
    return;
  }
  if (groupDrag.pending) {
    if (Math.hypot(e.clientX - groupDrag.pending.x, e.clientY - groupDrag.pending.y) > 5) {
      const pending = groupDrag.pending;
      groupDrag.pending = null; _pDown = null;
      groupDrag.active = true;
      groupDrag.anchorEntry = pending.entry;
      groupDrag.startPos.clear();
      document.body.style.userSelect = 'none';
      for (const en of state.groupSel) {
        if (en.slotOwner) continue;
        groupDrag.startPos.set(en, { gx: en.gx, gy: en.gy, gz: en.gz });
        _dragging.add(en);
        en.mesh.visible = false;
        if (en.edges) en.edges.visible = false;
        if (en.hitMesh) en.hitMesh.visible = false;
      }
      updateGroupOutlines(); // clear outlines while dragging
      drag.grabOffset = { x: pending.hitPoint.x - pending.entry.gx, y: pending.hitPoint.y - pending.entry.gy, z: pending.hitPoint.z - pending.entry.gz };
      refreshGhostForDrag(pending.entry);
      buildGroupGhosts();
    }
  }
  if (groupDrag.active) {
    updatePointer(e);
    const pos = getGridPos(partDims(groupDrag.anchorEntry.part), groupDrag.anchorEntry, groupDrag.anchorEntry.rotDeg);
    if (!pos) return;
    const { gx, gy, gz } = pos;
    if (gx !== groupDrag.gx || gy !== groupDrag.gy || gz !== groupDrag.gz) {
      groupDrag.gx = gx; groupDrag.gy = gy; groupDrag.gz = gz;
      const s = groupDrag.startPos.get(groupDrag.anchorEntry);
      const dgx = gx - s.gx, dgy = gy - s.gy, dgz = gz - s.gz;
      const mat = isFreeForGroup(dgx, dgy, dgz) ? ghostMatOk : ghostMatBad;
      ghost.material = mat;
      ghost.position.set(gx, gy, gz);
      ghost.visible = true;
      updateGroupGhostPositions(dgx, dgy, dgz, mat);
    }
    return;
  }
  if (drag.pending) {
    if (Math.hypot(e.clientX - drag.pending.x, e.clientY - drag.pending.y) > 5) {
      const pending = drag.pending;
      drag.active = true; drag.entry = pending.entry; drag.pending = null; _pDown = null;
      document.body.style.userSelect = 'none';
      const de = drag.entry;
      drag.grabOffset = { x: pending.hitPoint.x - de.gx, y: pending.hitPoint.y - de.gy, z: pending.hitPoint.z - de.gz };
      state.inspected = drag.entry; updateInspector();
      _dragging.add(drag.entry);
      drag.entry.mesh.visible = false;
      if (drag.entry.edges) drag.entry.edges.visible = false;
      refreshGhostForDrag(drag.entry);
    }
  }
  if (drag.active) {
    updatePointer(e);
    const pos = getGridPos(partDims(drag.entry.part), drag.entry, drag.entry.rotDeg);
    if (!pos) return;
    const { gx, gy, gz } = pos;
    if (gx !== drag.gx || gy !== drag.gy || gz !== drag.gz) {
      drag.gx = gx; drag.gy = gy; drag.gz = gz;
      ghost.material = isFree(gx, gy, gz, drag.entry.dims) ? ghostMatOk : ghostMatBad;
      ghost.position.set(gx, gy, gz);
      ghost.visible = true;
      selOutline.position.set(gx, gy, gz);
      // Keep slot sprite glued to the ghost position while dragging the hull piece.
      const _spr = _slotSprites.get(drag.entry);
      if (_spr) { const [_w, _h, _d] = drag.entry.dims; _spr.position.set(gx + _w/2, gy + _h + 0.3, gz + _d/2); }
    }
    return;
  }
  if (slotDrag.pending) {
    if (Math.hypot(e.clientX - slotDrag.pending.x, e.clientY - slotDrag.pending.y) > 5) {
      slotDrag.active = true;
      slotDrag.entry = slotDrag.pending.entry;
      slotDrag.origOwner = slotDrag.pending.entry.slotOwner;
      slotDrag._prevSelected = state.selected;
      state.selected = slotDrag.entry.part;
      slotDrag.pending = null;
      _pDown = null;
      document.body.style.userSelect = 'none';
      refreshSlotSprites();
    }
  }
  if (slotDrag.active) {
    showModCursor(slotDrag.entry.part, e.clientX, e.clientY);
    updatePointer(e);
    raycaster.setFromCamera(pointer, camera);
    const visSprites = [..._slotSprites.values()].filter(s => s.visible);
    const spHits = visSprites.length ? raycaster.intersectObjects(visSprites) : [];
    const newHov = spHits.length ? spHits[0].object._hullEntry : null;
    if (newHov !== _hoveredSlot) {
      if (_hoveredSlot) setSlotHighlight(_hoveredSlot, false);
      setSlotHighlight(newHov, !!newHov);
    }
    return;
  }
  if (!state.selected || state.eraseMode) return;
  updatePointer(e);
  if (isInsideMod(state.selected)) {
    clearGhost();
    raycaster.setFromCamera(pointer, camera);
    const visSprites = [..._slotSprites.values()].filter(s => s.visible);
    const hits = visSprites.length ? raycaster.intersectObjects(visSprites) : [];
    const newHov = hits.length ? hits[0].object._hullEntry : null;
    if (newHov !== _hoveredSlot) {
      if (_hoveredSlot) setSlotHighlight(_hoveredSlot, false);
      setSlotHighlight(newHov, !!newHov);
    }
    return;
  }
  if (_hoveredSlot) setSlotHighlight(_hoveredSlot, false);
  const pos = getGridPos();
  if (pos) positionGhost(pos.gx, pos.gy, pos.gz);
});

renderer.domElement.addEventListener('pointerup', e => {
  if (e.button === 1) { _midPan.active = false; return; }
  if (marquee.active) {
    marquee.active = false;
    marqueeEl.style.display = 'none';
    const x0 = Math.min(marquee.x0, e.clientX), x1 = Math.max(marquee.x0, e.clientX);
    const y0 = Math.min(marquee.y0, e.clientY), y1 = Math.max(marquee.y0, e.clientY);
    if (x1 - x0 > 4 || y1 - y0 > 4) {
      const rect = renderer.domElement.getBoundingClientRect();
      const v = new THREE.Vector3();
      for (const en of state.placed) {
        if (en.slotOwner) continue;
        v.set(en.gx + en.dims[0]/2, en.gy + en.dims[1]/2, en.gz + en.dims[2]/2).project(camera);
        const sx = (v.x * 0.5 + 0.5) * rect.width + rect.left;
        const sy = (-v.y * 0.5 + 0.5) * rect.height + rect.top;
        if (sx >= x0 && sx <= x1 && sy >= y0 && sy <= y1) state.groupSel.add(en);
      }
      updateGroupOutlines();
    }
    _pDown = null;
    return;
  }
  groupDrag.pending = null;
  if (groupDrag.active) {
    groupDrag.active = false;
    document.body.style.userSelect = '';
    clearGhost();
    clearGroupGhosts();
    const anchorStart = groupDrag.startPos.get(groupDrag.anchorEntry);
    const dgx = groupDrag.gx !== null ? groupDrag.gx - anchorStart.gx : 0;
    const dgy = groupDrag.gx !== null ? groupDrag.gy - anchorStart.gy : 0;
    const dgz = groupDrag.gx !== null ? groupDrag.gz - anchorStart.gz : 0;
    const canPlace = groupDrag.gx !== null && isFreeForGroup(dgx, dgy, dgz);
    for (const [en, s] of groupDrag.startPos) {
      en.gx = canPlace ? s.gx + dgx : s.gx;
      en.gy = canPlace ? s.gy + dgy : s.gy;
      en.gz = canPlace ? s.gz + dgz : s.gz;
      en.mesh.visible = true; en.mesh.position.set(en.gx, en.gy, en.gz);
      if (en.edges) { en.edges.visible = true; en.edges.position.set(en.gx, en.gy, en.gz); }
      if (en.hitMesh) { en.hitMesh.visible = true; en.hitMesh.position.set(en.gx, en.gy, en.gz); }
      _dragging.delete(en);
      syncSlotModule(en);
    }
    groupDrag.anchorEntry = null;
    groupDrag.gx = null; groupDrag.gy = null; groupDrag.gz = null;
    groupDrag.startPos.clear();
    updateGroupOutlines();
    refreshSlotSprites();
    return;
  }
  drag.pending = null;
  if (drag.active) {
    drag.active = false;
    document.body.style.userSelect = '';
    _palette.classList.remove('drop-target');
    const entry = drag.entry; drag.entry = null;
    entry.mesh.visible = true;
    if (entry.edges) entry.edges.visible = true;
    clearGhost();
    const palRect = _palette.getBoundingClientRect();
    const onPalette = e.clientX >= palRect.left && e.clientX <= palRect.right
                   && e.clientY >= palRect.top  && e.clientY <= palRect.bottom;
    if (onPalette) {
      drag.gx = null; drag.gy = null; drag.gz = null;
      removeEntry(entry);
      updateSelOutline();
      return;
    }
    if (drag.gx !== null && isFree(drag.gx, drag.gy, drag.gz, entry.dims)) {
      entry.gx = drag.gx; entry.gy = drag.gy; entry.gz = drag.gz;
      entry.mesh.position.set(entry.gx, entry.gy, entry.gz);
      if (entry.edges) entry.edges.position.set(entry.gx, entry.gy, entry.gz);
      if (entry.hitMesh) entry.hitMesh.position.set(entry.gx, entry.gy, entry.gz);
      syncSlotModule(entry);
      refreshSlotSprites();
    }
    updateSelOutline();
    _dragging.delete(entry);
    drag.gx = null; drag.gy = null; drag.gz = null;
    return;
  }
  const _sdPending = slotDrag.pending; slotDrag.pending = null;
  if (slotDrag.active) {
    slotDrag.active = false;
    document.body.style.userSelect = '';
    hideModCursor();
    _palette.classList.remove('drop-target');
    if (_hoveredSlot) setSlotHighlight(_hoveredSlot, false);
    state.selected = slotDrag._prevSelected;
    slotDrag._prevSelected = null;
    const sdEntry = slotDrag.entry; slotDrag.entry = null;
    const sdOrigOwner = slotDrag.origOwner; slotDrag.origOwner = null;
    const palRect2 = _palette.getBoundingClientRect();
    const onPalette2 = e.clientX >= palRect2.left && e.clientX <= palRect2.right && e.clientY >= palRect2.top && e.clientY <= palRect2.bottom;
    if (onPalette2) {
      removeEntry(sdEntry);
    } else {
      updatePointer(e);
      raycaster.setFromCamera(pointer, camera);
      const visSprites = [..._slotSprites.values()].filter(s => s.visible);
      const spHits = visSprites.length ? raycaster.intersectObjects(visSprites) : [];
      if (spHits.length && spHits[0].object._hullEntry !== sdOrigOwner) {
        const part = sdEntry.part;
        removeEntry(sdEntry);
        placeInSlot(part, spHits[0].object._hullEntry);
        return;
      }
    }
    refreshSlotSprites();
    updateInspector();
    updateShipStats();
    return;
  }
  if (!_pDown) {
    // Palette drag released on canvas: place the selected part.
    if (e.button === 0 && state.selected && !state.eraseMode) {
      updatePointer(e);
      if (isInsideMod(state.selected)) {
        raycaster.setFromCamera(pointer, camera);
        const visSprites = [..._slotSprites.values()].filter(s => s.visible);
        const spHits = visSprites.length ? raycaster.intersectObjects(visSprites) : [];
        if (spHits.length) placeInSlot(state.selected, spHits[0].object._hullEntry);
      } else {
        const pos = getGridPos();
        if (pos) placePiece(pos.gx, pos.gy, pos.gz);
      }
    }
    return;
  }
  const moved = Math.hypot(e.clientX - _pDown.x, e.clientY - _pDown.y) > 5;
  const btn = _pDown.btn; _pDown = null;
  if (moved) return;
  // Short click on an occupied slot sprite → inspect the module.
  if (_sdPending && btn === 0 && !state.eraseMode) {
    state.inspected = _sdPending.entry; updateInspector(); return;
  }
  updatePointer(e);
  if (btn === 0) {
    if (state.eraseMode) {
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(state.placed.map(p => p.mesh));
      if (hits.length) { const entry = hits[0].object._entry ?? state.placed.find(p => p.mesh === hits[0].object); if (entry) removeEntry(entry); }
      return;
    }
    if (!state.selected) {
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(state.placed.map(p => p.mesh));
      if (hits.length) {
        const entry = hits[0].object._entry ?? state.placed.find(p => p.mesh === hits[0].object);
        if (entry) { state.inspected = entry; updateInspector(); }
      } else { state.inspected = null; updateInspector(); }
      return;
    }
    if (isInsideMod(state.selected)) {
      raycaster.setFromCamera(pointer, camera);
      const visSprites = [..._slotSprites.values()].filter(s => s.visible);
      const hits = visSprites.length ? raycaster.intersectObjects(visSprites) : [];
      if (hits.length) {
        const hullEntry = hits[0].object._hullEntry;
        if (hullEntry) placeInSlot(state.selected, hullEntry);
      }
    } else {
      const pos = getGridPos();
      if (pos) placePiece(pos.gx, pos.gy, pos.gz);
    }
  }
  if (btn === 2) {
    if (state.selected) { selectPart(null); return; }
    const visSprites = [..._slotSprites.values()].filter(s => s.visible);
    if (visSprites.length) {
      raycaster.setFromCamera(pointer, camera);
      const sprHits = raycaster.intersectObjects(visSprites);
      if (sprHits.length) {
        const occupant = state.placed.find(e => e.slotOwner === sprHits[0].object._hullEntry);
        if (occupant) { removeEntry(occupant); refreshSlotSprites(); }
      }
    }
  }
});

renderer.domElement.addEventListener('contextmenu', e => e.preventDefault());
renderer.domElement.addEventListener('pointerleave', () => {
  if (!drag.active && !slotDrag.active) {
    clearGhost();
    if (_hoveredSlot) setSlotHighlight(_hoveredSlot, false);
  } else if (slotDrag.active && _hoveredSlot) {
    setSlotHighlight(_hoveredSlot, false);
  }
});

const _palette = document.getElementById('palette');

window.addEventListener('pointermove', e => {
  if (drag.active) {
    const overPalette = e.clientX <= _palette.getBoundingClientRect().right;
    _palette.classList.toggle('drop-target', overPalette);
    ghost.visible = !overPalette;
  }
  if (slotDrag.active) {
    const overPalette = e.clientX <= _palette.getBoundingClientRect().right;
    _palette.classList.toggle('drop-target', overPalette);
    showModCursor(slotDrag.entry.part, e.clientX, e.clientY);
  }
  if (state.selected && isInsideMod(state.selected) && !slotDrag.active) {
    showModCursor(state.selected, e.clientX, e.clientY);
  }
});

window.addEventListener('pointerup', () => {
  _midPan.active = false;
  // Fallback: clean up any stuck drag/marquee (e.g. released outside browser window).
  if (marquee.active) { marquee.active = false; marqueeEl.style.display = 'none'; }
  if (groupDrag.active) {
    groupDrag.active = false; document.body.style.userSelect = '';
    clearGhost(); clearGroupGhosts();
    for (const [en, s] of groupDrag.startPos) {
      en.gx = s.gx; en.gy = s.gy; en.gz = s.gz;
      en.mesh.visible = true; en.mesh.position.set(en.gx, en.gy, en.gz);
      if (en.edges) { en.edges.visible = true; en.edges.position.set(en.gx, en.gy, en.gz); }
      if (en.hitMesh) { en.hitMesh.visible = true; en.hitMesh.position.set(en.gx, en.gy, en.gz); }
      _dragging.delete(en);
    }
    groupDrag.startPos.clear(); groupDrag.anchorEntry = null;
    groupDrag.gx = null; groupDrag.gy = null; groupDrag.gz = null;
    updateGroupOutlines(); refreshSlotSprites();
  }
  if (slotDrag.active) {
    slotDrag.active = false; slotDrag.pending = null;
    document.body.style.userSelect = '';
    hideModCursor();
    _palette.classList.remove('drop-target');
    if (_hoveredSlot) setSlotHighlight(_hoveredSlot, false);
    state.selected = slotDrag._prevSelected; slotDrag._prevSelected = null;
    slotDrag.entry = null; slotDrag.origOwner = null;
    refreshSlotSprites(); updateInspector(); updateShipStats();
  }
  if (!drag.active) return;
  _palette.classList.remove('drop-target');
  drag.active = false;
  document.body.style.userSelect = '';
  const entry = drag.entry; drag.entry = null;
  drag.gx = null; drag.gy = null; drag.gz = null;
  entry.mesh.visible = true;
  if (entry.edges) entry.edges.visible = true;
  clearGhost();
  _dragging.delete(entry);
  updateSelOutline();
});

// ── Keyboard + camera ─────────────────────────────────────────────────────────

const keysHeld = new Set();
const _fwd = new THREE.Vector3(), _right = new THREE.Vector3(), _delta = new THREE.Vector3(), _up = new THREE.Vector3(0, 1, 0);

window.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  keysHeld.add(e.code);
  if (e.code === 'Space') e.preventDefault();
  if (e.repeat) return;
  if (e.code === 'KeyE')          toggleErase();
  if (e.code === 'Escape')        { selectPart(null); clearGroupSel(); }
  if (e.code === 'KeyR')          turnSel(90);
  if (e.code === 'BracketLeft')   turnSel(-5);
  if (e.code === 'BracketRight')  turnSel(5);
});
window.addEventListener('keyup', e => keysHeld.delete(e.code));

function moveCamera() {
  const moving = keysHeld.has('KeyW') || keysHeld.has('KeyS') || keysHeld.has('KeyA') || keysHeld.has('KeyD') || keysHeld.has('Space') || keysHeld.has('ControlLeft') || keysHeld.has('ControlRight');
  if (!moving) return;
  const speed = (keysHeld.has('ShiftLeft') || keysHeld.has('ShiftRight')) ? 0.6 : 0.2;
  camera.getWorldDirection(_fwd); _fwd.y = 0; _fwd.normalize();
  _right.crossVectors(_fwd, _up).normalize(); _delta.set(0, 0, 0);
  if (keysHeld.has('KeyW')) _delta.addScaledVector(_fwd,    speed);
  if (keysHeld.has('KeyS')) _delta.addScaledVector(_fwd,   -speed);
  if (keysHeld.has('KeyA')) _delta.addScaledVector(_right, -speed);
  if (keysHeld.has('KeyD')) _delta.addScaledVector(_right,  speed);
  if (keysHeld.has('Space'))       _delta.y += speed;
  if (keysHeld.has('ControlLeft') || keysHeld.has('ControlRight')) _delta.y -= speed;
  camera.position.add(_delta); controls.target.add(_delta);
}

// ── Rotation ──────────────────────────────────────────────────────────────────

function turnSel(delta) {
  state.rotDeg = (((state.rotDeg + delta) % 360) + 360) % 360;
  if (state.selected) {
    partRot[state.selected.id] = state.rotDeg;
    try { localStorage.setItem('sc_partRotDeg', JSON.stringify(partRot)); } catch (e) {}
    // Rebuild all already-placed copies to the new rotation.
    state.placed.filter(en => en.part.id === state.selected.id)
      .forEach(en => rebuildPlacedMesh(en, en.shapeIdx, state.rotDeg, en.mx, en.my, en.mz, en.rz));
  }
  refreshGhostGeo();
  if (state.ghostGx !== null) positionGhost(state.ghostGx, state.ghostGy, state.ghostGz);
  document.getElementById('piece-rot').textContent = state.rotDeg ? `${state.rotDeg}°` : '';
}

function rotateInspected(delta) {
  if (!state.inspected) return;
  const e = state.inspected;
  const newRot = (((e.rotDeg + delta) % 360) + 360) % 360;
  rebuildPlacedMesh(e, e.shapeIdx, newRot, e.mx, e.my, e.mz, e.rz);
  updateInspector();
}
document.getElementById('rot-left') .addEventListener('click', () => rotateInspected(-90));
document.getElementById('rot-right').addEventListener('click', () => rotateInspected(90));

// ── Erase ─────────────────────────────────────────────────────────────────────

function toggleErase() {
  state.eraseMode = !state.eraseMode;
  const btn = document.getElementById('btn-erase');
  btn.classList.toggle('active',  state.eraseMode);
  btn.classList.toggle('danger',  state.eraseMode);
  clearGhost();
}
document.getElementById('btn-erase').addEventListener('click', toggleErase);

// ── Symmetry (flip) buttons ───────────────────────────────────────────────────

document.querySelectorAll('.sym-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const axis = +btn.dataset.axis; // 0=x, 1=y, 2=z
    if (state.inspected) {
      const f = [state.inspected.mx, state.inspected.my, state.inspected.mz];
      f[axis] = !f[axis];
      rebuildPlacedMesh(state.inspected, state.inspected.shapeIdx, state.inspected.rotDeg, f[0], f[1], f[2], state.inspected.rz);
    } else {
      const keys = ['mx', 'my', 'mz']; state[keys[axis]] = !state[keys[axis]];
      if (state.selected) {
        partFlip[state.selected.id] = [state.mx, state.my, state.mz];
        try { localStorage.setItem('sc_partFlip', JSON.stringify(partFlip)); } catch (e) {}
      }
      if (state.ghostGx !== null) positionGhost(state.ghostGx, state.ghostGy, state.ghostGz);
    }
  });
});

// ── Thruster orientation toggle ───────────────────────────────────────────────

document.querySelectorAll('.orient-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const newRz = btn.id === 'orient-v';
    if (state.inspected) {
      rebuildPlacedMesh(state.inspected, state.inspected.shapeIdx, state.inspected.rotDeg,
        state.inspected.mx, state.inspected.my, state.inspected.mz, newRz);
      updateInspector();
    } else {
      state.rz = newRz;
      refreshGhostGeo();
      if (state.ghostGx !== null) positionGhost(state.ghostGx, state.ghostGy, state.ghostGz);
      updateInspector();
    }
  });
});

// ── Modals ───────────────────────────────────────────────────────────────────

const modalOverlay = document.getElementById('modal-overlay');

function openModal(id) {
  modalOverlay.classList.add('open');
  document.getElementById(id).classList.add('open');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('open');
  modalOverlay.classList.remove('open');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && modalOverlay.classList.contains('open')) {
    modalOverlay.querySelectorAll('.modal.open').forEach(m => m.classList.remove('open'));
    modalOverlay.classList.remove('open');
  }
});

// Clear modal
document.getElementById('btn-clear').addEventListener('click', () => openModal('modal-clear'));
document.getElementById('modal-clear-cancel').addEventListener('click', () => closeModal('modal-clear'));
document.getElementById('modal-clear-ok').addEventListener('click', () => { closeModal('modal-clear'); clearAll(); });

// Save modal
document.getElementById('btn-save').addEventListener('click', () => {
  const data = state.placed.map((e, _, arr) => ({ partId: e.part.id, shapeIdx: e.shapeIdx, rotDeg: e.rotDeg, mx: e.mx, my: e.my, mz: e.mz, rz: e.rz || false, gx: e.gx, gy: e.gy, gz: e.gz, ...(e.slotOwner != null ? { slotOwnerIdx: arr.indexOf(e.slotOwner) } : {}) }));
  const json = JSON.stringify(data, null, 2);
  const ta = document.getElementById('modal-save-text');
  const hint = document.getElementById('modal-save-hint');
  ta.value = json;
  hint.textContent = '';
  hint.className = 'modal-hint';
  openModal('modal-save');
  navigator.clipboard.writeText(json).then(() => {
    hint.textContent = 'Copied to clipboard.';
    hint.className = 'modal-hint ok';
  }).catch(() => {});
});

document.getElementById('modal-save-copy').addEventListener('click', () => {
  const hint = document.getElementById('modal-save-hint');
  navigator.clipboard.writeText(document.getElementById('modal-save-text').value).then(() => {
    hint.textContent = 'Copied to clipboard.';
    hint.className = 'modal-hint ok';
  }).catch(() => {
    hint.textContent = 'Copy failed — select all and copy manually.';
    hint.className = 'modal-hint error';
  });
});

document.getElementById('modal-save-close').addEventListener('click', () => closeModal('modal-save'));

// Load modal
document.getElementById('btn-load').addEventListener('click', () => {
  document.getElementById('modal-load-text').value = '';
  const err = document.getElementById('modal-load-error');
  err.style.display = 'none';
  openModal('modal-load');
});

document.getElementById('modal-load-cancel').addEventListener('click', () => closeModal('modal-load'));

document.getElementById('modal-load-ok').addEventListener('click', () => {
  const raw = document.getElementById('modal-load-text').value.trim();
  const errEl = document.getElementById('modal-load-error');
  if (!raw) return;
  try {
    const data = JSON.parse(raw); clearAll();
    const savedEntries = [];
    data.forEach(({ partId, shapeIdx, rotDeg, mx, my, mz, rz, gx, gy, gz, slotOwnerIdx }) => {
      const part = BYID[partId];
      if (!part) { savedEntries.push(null); return; }
      const e = placePieceDirect(part, gx, gy, gz, shapeIdx || 0, rotDeg || 0, mx || false, my || false, mz || false, rz || false);
      e._loadSlotIdx = slotOwnerIdx;
      savedEntries.push(e);
    });
    savedEntries.forEach(e => {
      if (!e || e._loadSlotIdx == null) return;
      const owner = savedEntries[e._loadSlotIdx];
      if (owner) e.slotOwner = owner;
      delete e._loadSlotIdx;
    });
    updateShipStats();
    refreshSlotSprites();
    closeModal('modal-load');
  } catch (err) {
    errEl.textContent = 'Invalid JSON: ' + err.message;
    errEl.style.display = 'block';
  }
});

// ── Palette ───────────────────────────────────────────────────────────────────

let paletteTab = 'build';

function buildPalette(filter = '') {
  const list = document.getElementById('piece-list');
  list.innerHTML = '';
  const lower = filter.toLowerCase();
  GROUPS.forEach(groupName => {
    const items = PARTS.filter(p => p.group === groupName && p.kind === paletteTab && (!lower || p.name.toLowerCase().includes(lower)));
    if (!items.length) return;
    const hdr = document.createElement('div');
    hdr.className = 'category-header';
    hdr.innerHTML = `<span class="cat-dot" style="background:${items[0].color || '#888'}"></span>${groupName}`;
    hdr.addEventListener('click', () => {
      hdr.classList.toggle('collapsed');
      const g = hdr.nextElementSibling;
      if (g) g.style.display = hdr.classList.contains('collapsed') ? 'none' : '';
    });
    list.appendChild(hdr);
    const group = document.createElement('div');
    items.forEach(p => {
      const el = document.createElement('div');
      el.className = 'piece-item' + (state.selected?.id === p.id ? ' selected' : '');
      el.innerHTML = `<img class="piece-icon" src="ship_icons/${p.id}.webp" alt="" loading="lazy"><span class="piece-name">${p.name}</span>`;
      if (p.kind === 'module') {
        el.innerHTML += `<span class="mount-badge ${p.mount === 'inside' ? 'inside' : 'surface'}">${p.mount === 'inside' ? 'in' : 'out'}</span>`;
      }
      el.addEventListener('pointerdown', () => selectPart(state.selected?.id === p.id ? null : p));
      group.appendChild(el);
    });
    list.appendChild(group);
  });
}

document.getElementById('search').addEventListener('input', e => buildPalette(e.target.value));

document.getElementById('palette-tabs').addEventListener('click', e => {
  const btn = e.target.closest('.pal-tab');
  if (!btn) return;
  paletteTab = btn.dataset.kind;
  document.querySelectorAll('.pal-tab').forEach(b => b.classList.toggle('active', b === btn));
  buildPalette(document.getElementById('search').value);
  refreshSlotSprites();
});

// ── Inspector ─────────────────────────────────────────────────────────────────

function selectPart(part) {
  state.selected = part;
  document.body.style.userSelect = part ? 'none' : '';
  state.inspected = null;
  if (part) {
    state.rotDeg  = partRot[part.id] || 0;
    const f = flipOf(part.id); state.mx = f[0]; state.my = f[1]; state.mz = f[2];
    state.rz = false;
    state.shapeIdx = 0;
  }
  if (!part || !isInsideMod(part)) hideModCursor();
  if (_hoveredSlot) setSlotHighlight(_hoveredSlot, false);
  buildPalette(document.getElementById('search').value);
  clearGhost();
  if (part && !isInsideMod(part)) { refreshGhostGeo(); if (state.ghostGx !== null) positionGhost(state.ghostGx, state.ghostGy, state.ghostGz); }
  updateInspector();
}

function isThruster(part) { return !!(part && part.group === 'Engines & thrusters'); }
function isWing(part) { return !!(part && part.type === 'ShipWing'); }
function supportsOrient(part) { return isThruster(part) || isWing(part); }

function updateInspector() {
  updateSelOutline();
  const part = state.inspected?.part ?? state.selected;
  const entry = state.inspected;
  document.getElementById('piece-name').textContent = part ? part.name : 'Select a part';
  const rz = entry ? entry.rz : state.rz;
  const dims = part ? (entry ? entry.dims : effDims(partDims(part), state.rotDeg, state.rz)) : null;
  document.getElementById('piece-dims').textContent = part?.dims ? part.dims.join('×') : '';
  document.getElementById('piece-rot').textContent  = part ? ((entry ? entry.rotDeg : state.rotDeg) ? `${entry ? entry.rotDeg : state.rotDeg}°` : '') : '';
  const showRot = !!entry;
  document.getElementById('rot-left') .style.display = showRot ? '' : 'none';
  document.getElementById('rot-right').style.display = showRot ? '' : 'none';
  const showOrient = supportsOrient(part);
  document.getElementById('orient-section').style.display = showOrient ? '' : 'none';
  if (showOrient) {
    document.getElementById('orient-h').classList.toggle('active', !rz);
    document.getElementById('orient-v').classList.toggle('active',  rz);
  }
  updateShapePicker(part);
  updateFlipButtons([state.mx, state.my, state.mz]);
  renderPartStats(part);
}

function updateShapePicker(part) {
  const el = document.getElementById('shape-picker');
  el.innerHTML = '';
  const shapes = (part && part.shapes) || [];
  if (shapes.length <= 1) return;
  const activeIdx = state.inspected ? state.inspected.shapeIdx : state.shapeIdx;
  shapes.forEach((s, i) => {
    const btn = document.createElement('button');
    btn.className = 'shape-btn' + (i === activeIdx ? ' active' : '');
    if (s.s) {
      const img = document.createElement('img');
      img.src = `ship_shapes/${s.s}.webp`; img.alt = s.s;
      img.onerror = () => { btn.removeChild(img); btn.textContent = s.s; };
      btn.appendChild(img);
    } else { btn.textContent = i + 1; }
    btn.addEventListener('click', () => {
      el.querySelectorAll('.shape-btn').forEach((b, j) => b.classList.toggle('active', j === i));
      if (state.inspected) {
        rebuildPlacedMesh(state.inspected, i, state.inspected.rotDeg, state.inspected.mx, state.inspected.my, state.inspected.mz, state.inspected.rz);
      } else {
        state.shapeIdx = i;
        refreshGhostGeo();
        if (state.ghostGx !== null) positionGhost(state.ghostGx, state.ghostGy, state.ghostGz);
      }
    });
    el.appendChild(btn);
  });
}

function updateFlipButtons() {}

const STAT_LABELS = {
  Frame: 'Frame', Hull: 'Hull', ShipWeight: 'Weight',
  SystemSupport: 'Sys. Support', SystemRequirement: 'Sys. Required',
  EngineForce: 'Engine Force', EngineThrust: 'Engine Thrust',
  PowerProduction: 'Power Gen', PowerUsage: 'Power Use',
  HeatCapacity: 'Heat Cap.',
  HeatInterfaceMaterial: 'Heat conductivity',
  SolidStorage: 'Cargo (su)', FluidStorage: 'Fluid Store',
  FTOilStorage: 'FTL Oil', FakeFTLOptimalMaxWeight: 'FTL Cap.',
};

function renderPartStats(part) {
  const el = document.getElementById('specs');
  el.innerHTML = '';
  if (!part?.stats) return;
  Object.entries(part.stats).forEach(([k, v]) => {
    if (!v || !STAT_LABELS[k]) return;
    const row = document.createElement('div');
    row.className = 'spec-row';
    row.innerHTML = `<span>${STAT_LABELS[k]}</span><span>${typeof v === 'number' ? v.toLocaleString() : v}</span>`;
    el.appendChild(row);
  });
}

// ── Ship viability stats ──────────────────────────────────────────────────────

function fmt(n) { return Math.round(n).toLocaleString(); }

function updateShipStats() {
  const el = document.getElementById('ship-stats');
  if (!state.placed.length) { el.innerHTML = '<div class="ship-empty">Place parts to see stats</div>'; return; }

  const sum  = k => state.placed.reduce((a, e) => a + ((e.part.stats?.[k]) || 0), 0);
  const anyM = k => state.placed.some(e => e.part.kind === 'module' && e.part.stats?.[k]);

  const hasCockpit = state.placed.some(e => e.part.group === 'Cockpits');
  const hasEngine  = state.placed.some(e => e.part.stats?.EngineThrust);
  const weight  = sum('ShipWeight');
  const frames  = sum('Frame');
  const force   = sum('EngineForce');
  const thrust  = sum('EngineThrust');
  const support = sum('SystemSupport');
  const req     = sum('SystemRequirement');
  const powProd = sum('PowerProduction');
  const powUse  = sum('PowerUsage') + sum('EngineConsumption');
  const powStorage = sum('PowerStorage');
  const heat    = sum('HeatCapacity');
  const heatCond = sum('HeatInterfaceParts');
  const steering = sum('SteeringStrength');

  // Fan-data stats (not yet in game data): heat gen, shields, recharge
  const fanSum = field => state.placed.reduce((acc, e) => {
    const s = statsFor(e.part.name); return acc + ((s?.[field]) || 0);
  }, 0);
  const heatGen  = fanSum('heat');
  const shields  = fanSum('shields');
  const recharge = fanSum('recharge');
  const solid   = sum('SolidStorage');
  const fluid   = sum('FluidStorage');
  const ftlOil  = sum('FTOilStorage');
  const ftlCap  = Math.max(0, ...state.placed.map(e => e.part.stats?.FakeFTLOptimalMaxWeight || 0));
  const hasFTL  = anyM('FTOilStorage') || anyM('FakeFTLOptimalMaxWeight');
  const slotsTotal = state.placed.filter(e => e.part.kind === 'build').length;
  const slotsUsed  = state.placed.filter(e => e.slotOwner != null).length;
  const suProvided = state.placed.filter(e => e.part.kind !== 'module').reduce((a, e) => a + (e.part.stats?.StorageUnits || 0), 0);
  const suUsed     = state.placed.filter(e => e.part.kind === 'module').reduce((a, e) => a + (e.part.stats?.StorageUnits || 0), 0);

  // Integrity = 200 − (7 × weight²) / (25 × frames). Warn below 20.
  const integrity     = frames > 0 ? 200 - (7 * weight * weight) / (25 * frames) : null;
  const integrityOk   = integrity !== null && integrity >= 20;
  const maneuver      = weight > 0 && steering > 0 ? 280 * steering / Math.pow(weight, 1.5) : null;
  const powNet        = powProd - powUse;
  const spPct         = support > 0 ? Math.min(100, (req / support) * 100) : 0;

  const flyable = hasCockpit && hasEngine && force >= weight && support >= req && integrityOk;

  // ── Verdict + checks ──
  const checks = [
    ['Cockpit',       hasCockpit,           hasCockpit ? 'yes' : 'missing'],
    ['Engine',        hasEngine,            hasEngine  ? 'yes' : 'missing'],
    ['Thrust / Mass', force >= weight,      `${fmt(force)} / ${fmt(weight)} t`],
    ['Integrity',     integrityOk,          integrity !== null ? `${integrity.toFixed(1)}%` : '—'],
    ['Sys. support',  support >= req,       `${fmt(req)} / ${fmt(support)}`],
    ['Power',         powProd >= powUse,    `${fmt(powUse)} / ${fmt(powProd)}`],
    ...(hasFTL ? [['FTL cap.', ftlCap >= weight, `${fmt(ftlCap)} / ${fmt(weight)} t`]] : []),
    ['Mod. slots',    slotsUsed <= slotsTotal, `${slotsUsed} / ${slotsTotal}`],
  ];

  let h = `<div class="ship-verdict ${flyable ? 'ok' : 'no'}">${flyable ? '✓ Flight-ready' : '✗ Not ready'}</div>`;
  checks.forEach(([n, ok, v]) => {
    h += `<div class="ship-check ${ok ? 'pass' : 'fail'}">
      <span class="chk-icon">${ok ? '◆' : '!'}</span>
      <span class="chk-name">${n}</span>
      <span class="chk-val">${v}</span>
    </div>`;
  });

  // ── SP bar ──
  const spOver = req > support && support > 0;
  h += `<div class="stat-section">
    <div class="stat-section-label">System Support</div>
    <div class="sp-bar-wrap"><div class="sp-bar-fill ${spOver ? 'over' : spPct >= 80 ? 'warn' : ''}" style="width:${spPct}%"></div></div>
    <div class="sp-bar-foot${spOver ? ' over' : ''}"><span>${fmt(req)} used</span><span>${fmt(support)} cap</span></div>
  </div>`;

  // ── Structure ──
  h += `<div class="stat-section"><div class="stat-section-label">Structure</div><div class="stat-grid">`;
  h += statBlock('Weight',    `${weight % 1 ? weight.toFixed(1) : fmt(weight)} t`, 'neutral');
  h += statBlock('Frames',    fmt(frames), 'neutral');
  const intCls = integrity === null ? 'neutral' : integrity < 20 ? 'bad' : integrity < 60 ? 'warn' : 'neutral';
  h += statBlock('Integrity', integrity !== null ? `${integrity.toFixed(1)}%` : '—', intCls);
  h += statBlock('Maneuver',  maneuver !== null ? maneuver.toFixed(2) : '—', 'neutral');
  h += `</div></div>`;

  // ── Propulsion ──
  if (thrust > 0 || force > 0) {
    h += `<div class="stat-section"><div class="stat-section-label">Propulsion</div><div class="stat-grid">`;
    if (thrust > 0) h += statBlock('Thrust', fmt(thrust));
    if (force  > 0) h += statBlock('Force',  fmt(force), force < weight ? 'bad' : '');
    h += `</div></div>`;
  }

  // ── Power ──
  h += `<div class="stat-section"><div class="stat-section-label">Power</div><div class="stat-grid">`;
  h += statBlock('Gen',   fmt(powProd));
  h += statBlock('Usage', fmt(powUse), 'neutral');
  h += statBlock('Net', (powNet >= 0 ? '+' : '') + fmt(powNet), powNet < 0 ? 'bad' : 'good', true);
  h += `</div>`;
  if (powStorage) h += `<div class="stat-row" style="margin-top:3px"><span class="stat-label">Battery cap.</span><span class="stat-val">${fmt(powStorage)}</span></div>`;
  if (recharge)   h += `<div class="stat-row"><span class="stat-label">Recharge</span><span class="stat-val">${fmt(recharge)}/s</span></div>`;
  if (heat)      h += `<div class="stat-row"><span class="stat-label">Heat cap.</span><span class="stat-val">${fmt(heat)}</span></div>`;
  if (heatCond)  h += `<div class="stat-row"><span class="stat-label">Heat conductivity</span><span class="stat-val">${fmt(heatCond)}</span></div>`;
  h += `</div>`;

  // ── Module slots ──
  if (slotsTotal > 0) {
    const slotOver = slotsUsed > slotsTotal;
    const hullEntries = state.placed.filter(e => e.part.kind === 'build');
    const dots = hullEntries.map(e => {
      const mod = state.placed.find(m => m.slotOwner === e);
      return `<div class="slot-dot${mod ? ' used' : ''}"${mod ? ` title="${mod.part.name}"` : ''}></div>`;
    }).join('');
    h += `<div class="stat-section">
      <div class="stat-section-label">Module Slots</div>
      <div class="slot-grid">${dots}</div>
      <div class="slot-grid-foot${slotOver ? ' over' : ''}">${slotsUsed} of ${slotsTotal} slots used</div>
    </div>`;
  }

  // ── Cargo capacity ──
  const hasCargo = solid || fluid || ftlOil || hasFTL;
  if (hasCargo) {
    h += `<div class="stat-section"><div class="stat-section-label">Cargo</div>`;
    if (solid)  h += `<div class="stat-row"><span class="stat-label">Solid cargo</span><span class="stat-val">${fmt(solid)}</span></div>`;
    if (fluid)  h += `<div class="stat-row"><span class="stat-label">Liquid</span><span class="stat-val">${fmt(fluid)}</span></div>`;
    if (ftlOil) h += `<div class="stat-row"><span class="stat-label">Mag fuel</span><span class="stat-val">${fmt(ftlOil)}</span></div>`;
    if (hasFTL) h += `<div class="stat-row${ftlCap < weight ? ' bad' : ''}"><span class="stat-label">FTL cap.</span><span class="stat-val">${fmt(ftlCap)} t</span></div>`;
    h += `</div>`;
  }

  // ── Heat & shields (fan data) ──
  const hasHeatShield = heatGen || shields;
  if (hasHeatShield) {
    h += `<div class="stat-section"><div class="stat-section-label">Combat & Heat</div><div class="stat-grid">`;
    if (shields) h += statBlock('Shields', fmt(shields));
    if (heatGen) h += statBlock('Heat gen.', (heatGen > 0 ? '+' : '') + fmt(heatGen), heatGen > 0 ? 'warn' : 'good');
    h += `</div></div>`;
  }

  el.innerHTML = h;
}

function statBlock(label, value, cls = '', wide = false) {
  return `<div class="stat-block${wide ? ' wide' : ''}">
    <div class="stat-blabel">${label}</div>
    <div class="stat-bval${cls ? ' ' + cls : ''}">${value}</div>
  </div>`;
}

// ── Resize + animation ────────────────────────────────────────────────────────

function resize() {
  const vp = document.getElementById('viewport');
  renderer.setSize(vp.clientWidth, vp.clientHeight, false);
  camera.aspect = vp.clientWidth / vp.clientHeight;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);

function animate() { requestAnimationFrame(animate); moveCamera(); controls.update(); renderer.render(scene, camera); }

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  await loadData();
  await loadStats();
  await loadManifest();
  resize();
  buildPalette();
  updateInspector();
  updateShipStats();
  animate();
}

init();
