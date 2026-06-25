import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { SHAPES, SHAPE_IDS } from './shapes.js';
import { CATALOG, CATEGORY_COLORS } from './catalog.js';

// ── Scene setup ──────────────────────────────────────────────────────────────

const canvas = document.getElementById('c');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = false;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d1117);
scene.fog = new THREE.Fog(0x0d1117, 80, 150);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 500);
camera.position.set(20, 16, 20);
camera.lookAt(0, 0, 0);

const controls = new OrbitControls(camera, renderer.domElement);
controls.mouseButtons = { LEFT: null, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.ROTATE };
controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
controls.enableDamping = true;
controls.dampingFactor = 0.08;

// Lighting
scene.add(new THREE.AmbientLight(0xffffff, 1.2));
const sun = new THREE.DirectionalLight(0xffffff, 1.2);
sun.position.set(15, 30, 10);
scene.add(sun);
const fill = new THREE.DirectionalLight(0xaabbcc, 0.6);
fill.position.set(-10, 5, -10);
scene.add(fill);

// Grid
const grid = new THREE.GridHelper(100, 200, 0x1a2030, 0x151c28);
scene.add(grid);

// Build plane (invisible, used for raycasting mouse position)
const buildPlane = new THREE.Mesh(
  new THREE.PlaneGeometry(500, 500),
  new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide })
);
buildPlane.rotation.x = -Math.PI / 2;
scene.add(buildPlane);


// ── State ────────────────────────────────────────────────────────────────────

const state = {
  placed: [],
  selected: null,
  inspected: null,
  shapeIdx: 0,
  mx: false, my: false, mz: false,
  eraseMode: false,
  ghostGx: null,
  ghostGy: null,
  ghostGz: null,
};

// Materials
const ghostMatOk  = new THREE.MeshPhongMaterial({ color: 0x4488cc, transparent: true, opacity: 0.45, depthWrite: false });
const ghostMatBad = new THREE.MeshPhongMaterial({ color: 0xcc3333, transparent: true, opacity: 0.45, depthWrite: false });
let ghostMesh = null;
let ghostEdges = null;

// Occupation map: "gx,gy,gz" → placed entry index
const occupation = new Map();

// ── Helpers ──────────────────────────────────────────────────────────────────

function effDims(dims) {
  return dims;
}

function occupyCells(entry, set) {
  const [elx, ely, elz] = effDims(entry.dims);
  for (let dx = 0; dx < elx; dx += 0.5)
  for (let dy = 0; dy < ely; dy += 0.5)
  for (let dz = 0; dz < elz; dz += 0.5) {
    const key = `${entry.gx+dx},${entry.gy+dy},${entry.gz+dz}`;
    if (set) occupation.set(key, entry);
    else occupation.delete(key);
  }
}

function isFree(gx, gy, gz, dims) {
  const [elx, ely, elz] = effDims(dims);
  for (let dx = 0; dx < elx; dx += 0.5)
  for (let dy = 0; dy < ely; dy += 0.5)
  for (let dz = 0; dz < elz; dz += 0.5)
    if (occupation.has(`${gx+dx},${gy+dy},${gz+dz}`)) return false;
  return true;
}

function applyMirror(obj, mx, my, mz) {
  obj.scale.set(mx ? -1 : 1, my ? -1 : 1, mz ? -1 : 1);
}

function buildMesh(shapeId, dims, color, mx, my, mz) {
  const [lx, ly, lz] = dims;
  const geo = SHAPES[shapeId].geo(lx, ly, lz);
  const mat = new THREE.MeshPhongMaterial({ color });
  const mesh = new THREE.Mesh(geo, mat);
  const edgesGeo = new THREE.EdgesGeometry(geo, 15);
  const edges = new THREE.LineSegments(edgesGeo, new THREE.LineBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.4 }));
  applyMirror(mesh, mx, my, mz);
  applyMirror(edges, mx, my, mz);
  return { mesh, edges };
}

function meshCenter(gx, gy, gz, dims) {
  const [elx, ely, elz] = effDims(dims);
  return new THREE.Vector3(gx + elx / 2, gy + ely / 2, gz + elz / 2);
}

function rebuildPlacedMesh(entry, newShapeId, mx, my, mz) {
  scene.remove(entry.mesh);
  scene.remove(entry.edges);
  entry.mesh.geometry.dispose();
  entry.mesh.material.dispose();
  entry.edges.geometry.dispose();
  const color = CATEGORY_COLORS[entry.piece.category] ?? 0x556677;
  const { mesh, edges } = buildMesh(newShapeId, entry.dims, color, mx, my, mz);
  const center = meshCenter(entry.gx, entry.gy, entry.gz, entry.dims);
  mesh.position.copy(center);
  edges.position.copy(center);
  scene.add(mesh);
  scene.add(edges);
  entry.shapeId = newShapeId;
  entry.mx = mx; entry.my = my; entry.mz = mz;
  entry.mesh = mesh;
  entry.edges = edges;
}

// ── Ghost piece ───────────────────────────────────────────────────────────────

function clearGhost() {
  if (ghostMesh) { scene.remove(ghostMesh); ghostMesh.geometry.dispose(); ghostMesh.material.dispose(); ghostMesh = null; }
  if (ghostEdges) { scene.remove(ghostEdges); ghostEdges.geometry.dispose(); ghostEdges = null; }
}

function showGhost(gx, gy, gz, shapeId, dims, mx, my, mz) {
  const free = isFree(gx, gy, gz, dims);
  clearGhost();
  const [lx, ly, lz] = dims;
  const geo = SHAPES[shapeId].geo(lx, ly, lz);
  ghostMesh = new THREE.Mesh(geo, free ? ghostMatOk : ghostMatBad);
  const center = meshCenter(gx, gy, gz, dims);
  ghostMesh.position.copy(center);
  applyMirror(ghostMesh, mx, my, mz);
  scene.add(ghostMesh);
  const edgesGeo = new THREE.EdgesGeometry(geo, 15);
  ghostEdges = new THREE.LineSegments(edgesGeo, new THREE.LineBasicMaterial({ color: free ? 0x88bbff : 0xff6666 }));
  ghostEdges.position.copy(center);
  applyMirror(ghostEdges, mx, my, mz);
  scene.add(ghostEdges);
}

function updateGhost(gx, gy, gz) {
  if (!state.selected) { clearGhost(); return; }
  state.ghostGx = gx;
  state.ghostGy = gy;
  state.ghostGz = gz;
  showGhost(gx, gy, gz, state.selected.shapes[state.shapeIdx], state.selected.dims, state.mx, state.my, state.mz);
}

// ── Place / erase ─────────────────────────────────────────────────────────────

function placePiece(gx, gy, gz) {
  if (!state.selected || state.eraseMode) return;
  const { dims, shapes } = state.selected;
  const shapeId = shapes[state.shapeIdx];
  const color = CATEGORY_COLORS[state.selected.category] ?? 0x556677;
  const { mx, my, mz } = state;
  const { mesh, edges } = buildMesh(shapeId, dims, color, mx, my, mz);
  const center = meshCenter(gx, gy, gz, dims);
  mesh.position.copy(center);
  edges.position.copy(center);
  scene.add(mesh);
  scene.add(edges);

  const entry = { piece: state.selected, shapeId, dims, mx, my, mz, gx, gy, gz, mesh, edges };
  state.placed.push(entry);
  occupyCells(entry, true);
  state.selected = null;
  state.inspected = entry;
  clearGhost();
  buildPalette(document.getElementById('search').value);
  updateInspector();
}

function erasePiece(event) {
  updatePointer(event);
  raycaster.setFromCamera(pointer, camera);
  const meshes = state.placed.map(e => e.mesh);
  const hits = raycaster.intersectObjects(meshes);
  if (!hits.length) return;
  const hitMesh = hits[0].object;
  const idx = state.placed.findIndex(e => e.mesh === hitMesh);
  if (idx < 0) return;
  const entry = state.placed[idx];
  occupyCells(entry, false);
  scene.remove(entry.mesh);
  scene.remove(entry.edges);
  entry.mesh.geometry.dispose();
  entry.mesh.material.dispose();
  entry.edges.geometry.dispose();
  state.placed.splice(idx, 1);
}

// ── Raycaster / pointer ───────────────────────────────────────────────────────

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

function updatePointer(e) {
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x =  ((e.clientX - rect.left) / rect.width)  * 2 - 1;
  pointer.y = -((e.clientY - rect.top)  / rect.height) * 2 + 1;
}

function snap(v) { return Math.round(v * 2) / 2; }

function stackHeight(gx, gz, dims, exclude = null) {
  const [elx, , elz] = effDims(dims);
  let top = null;
  for (const entry of state.placed) {
    if (entry === exclude) continue;
    const [exl, eyl, ezl] = effDims(entry.dims);
    if (gx < entry.gx + exl && gx + elx > entry.gx &&
        gz < entry.gz + ezl && gz + elz > entry.gz) {
      const t = entry.gy + eyl;
      if (top === null || t > top) top = t;
    }
  }
  return top ?? 0; // 0 = build plane when nothing overlaps
}

function stackDepth(gx, gz, dims, exclude = null) {
  const [elx, ely, elz] = effDims(dims);
  let bottom = null;
  for (const entry of state.placed) {
    if (entry === exclude) continue;
    const [exl, , ezl] = effDims(entry.dims);
    if (gx < entry.gx + exl && gx + elx > entry.gx &&
        gz < entry.gz + ezl && gz + elz > entry.gz) {
      const b = entry.gy - ely;
      if (bottom === null || b < bottom) bottom = b;
    }
  }
  return bottom ?? -ely; // ghost just below build plane when nothing overlaps
}

function getGridPos(dims = null, excludeEntry = null) {
  dims = dims ?? state.selected?.dims ?? [1, 1, 1];
  const [elx, ely, elz] = effDims(dims);
  raycaster.setFromCamera(pointer, camera);

  let gx, gy, gz;

  // Raycast placed meshes — face normal locks one axis; cursor (snapped) drives the other two
  const meshes = state.placed.filter(p => p !== excludeEntry).map(p => p.mesh);
  if (meshes.length) {
    const meshHits = raycaster.intersectObjects(meshes);
    if (meshHits.length) {
      const hit = meshHits[0];
      const entry = state.placed.find(p => p.mesh === hit.object);
      const n = hit.face.normal.clone().transformDirection(hit.object.matrixWorld);
      n.x = Math.round(n.x); n.y = Math.round(n.y); n.z = Math.round(n.z);
      const p = hit.point;
      const rayGoingUp = raycaster.ray.direction.y > 0;
      if (n.y < 0) {
        // Bottom face: place ghost directly below the piece.
        gx = snap(p.x - elx / 2);
        gz = snap(p.z - elz / 2);
        gy = entry.gy - ely;
      } else if (n.y > 0 || !rayGoingUp) {
        gx = n.x !== 0
          ? (n.x > 0 ? entry.gx + entry.dims[0] : entry.gx - elx)
          : snap(p.x - elx / 2);
        if (n.y > 0) {
          gy = entry.gy + entry.dims[1]; // top face
        } else {
          // Side face: cursor Y drives climb, XZ penetration provides a floor.
          const cursor_gy = snap(p.y - ely / 2);
          const [exl, eyl, ezl] = effDims(entry.dims);
          const fgx = snap(p.x - elx / 2);
          const fgz = snap(p.z - elz / 2);
          const xo = Math.min(fgx + elx, entry.gx + exl) - Math.max(fgx, entry.gx);
          const zo = Math.min(fgz + elz, entry.gz + ezl) - Math.max(fgz, entry.gz);
          const floor_gy = (xo > 0 && zo > 0) ? entry.gy + snap(xo <= zo ? xo : zo) : 0;
          gy = Math.max(cursor_gy, floor_gy);
        }
        gz = n.z !== 0
          ? (n.z > 0 ? entry.gz + entry.dims[2] : entry.gz - elz)
          : snap(p.z - elz / 2);
      }
      // n.y === 0 && rayGoingUp: side face from below → fall through to ground fallback
    }
  }

  if (gx == null) {
    const planeHits = raycaster.intersectObject(buildPlane);
    if (!planeHits.length) return null;
    const p = planeHits[0].point;
    const raw_gx = snap(p.x - elx / 2);
    const raw_gz = snap(p.z - elz / 2);
    gx = raw_gx; gz = raw_gz; gy = 0;

    // Camera looking mostly downward → top-down intent, sit on the stack directly.
    // Camera from the side → side-approach intent, XZ-clamp to nearest face and climb.
    const camDir = new THREE.Vector3();
    camera.getWorldDirection(camDir);
    if (camDir.y < -0.5) {
      gy = stackHeight(gx, gz, dims, excludeEntry);
    } else if (camDir.y > 0.5) {
      gy = stackDepth(gx, gz, dims, excludeEntry);
    } else {
      let best = null, bestMin = Infinity;
      for (const entry of state.placed) {
        if (entry === excludeEntry) continue;
        const [exl, , ezl] = effDims(entry.dims);
        const xo = Math.min(raw_gx + elx, entry.gx + exl) - Math.max(raw_gx, entry.gx);
        const zo = Math.min(raw_gz + elz, entry.gz + ezl) - Math.max(raw_gz, entry.gz);
        if (xo <= 0 || zo <= 0) continue;
        const m = Math.min(xo, zo);
        if (m < bestMin) { bestMin = m; best = { entry, xo, zo }; }
      }

      if (best) {
        const { entry, xo, zo } = best;
        const [exl, eyl, ezl] = effDims(entry.dims);
        let climb;
        if (xo <= zo) {
          gx = (raw_gx + elx / 2) < (entry.gx + exl / 2) ? entry.gx - elx : entry.gx + exl;
          climb = entry.gy + snap(xo);
        } else {
          gz = (raw_gz + elz / 2) < (entry.gz + ezl / 2) ? entry.gz - elz : entry.gz + ezl;
          climb = entry.gy + snap(zo);
        }
        if (climb >= entry.gy + eyl) {
          gx = raw_gx; gz = raw_gz;
          gy = stackHeight(raw_gx, raw_gz, dims, excludeEntry);
        } else {
          gy = climb;
        }
      }
    }
  }

  return { gx, gy, gz };
}


// ── Pointer: click, drag-to-move, place ──────────────────────────────────────

const drag = {
  pending: null,  // { entry, x, y } — mousedown on a piece, not yet moved
  active: false,
  entry: null,
  ghostGx: null,
  ghostGy: null,
  ghostGz: null,
};

let _pDown = null;

renderer.domElement.addEventListener('pointerdown', e => {
  _pDown = { x: e.clientX, y: e.clientY, btn: e.button };
  // Begin potential drag only on left-click with no piece selected and no erase
  if (e.button !== 0 || state.eraseMode || state.selected) return;
  updatePointer(e);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(state.placed.map(p => p.mesh));
  if (!hits.length) return;
  const entry = state.placed.find(p => p.mesh === hits[0].object);
  if (entry) drag.pending = { entry, x: e.clientX, y: e.clientY };
});

renderer.domElement.addEventListener('pointermove', e => {
  // Promote pending drag to active once pointer moves > 5px
  if (drag.pending) {
    if (Math.hypot(e.clientX - drag.pending.x, e.clientY - drag.pending.y) > 5) {
      drag.active = true;
      drag.entry  = drag.pending.entry;
      drag.pending = null;
      _pDown = null; // prevent pointerup treating this as a click
      occupyCells(drag.entry, false);
      drag.entry.mesh.visible  = false;
      drag.entry.edges.visible = false;
    }
  }

  if (drag.active) {
    updatePointer(e);
    const pos = getGridPos(drag.entry.dims, drag.entry);
    if (!pos) return;
    const { gx, gy, gz } = pos;
    if (gx !== drag.ghostGx || gy !== drag.ghostGy || gz !== drag.ghostGz) {
      drag.ghostGx = gx;
      drag.ghostGy = gy;
      drag.ghostGz = gz;
      showGhost(gx, gy, gz, drag.entry.shapeId, drag.entry.dims, drag.entry.mx, drag.entry.my, drag.entry.mz);
    }
    return;
  }

  if (!state.selected || state.eraseMode) return;
  updatePointer(e);
  const pos = getGridPos();
  if (pos && (pos.gx !== state.ghostGx || pos.gy !== state.ghostGy || pos.gz !== state.ghostGz)) {
    updateGhost(pos.gx, pos.gy, pos.gz);
  }
});

renderer.domElement.addEventListener('pointerup', e => {
  drag.pending = null;

  if (drag.active) {
    drag.active = false;
    const entry = drag.entry;
    drag.entry = null;
    entry.mesh.visible  = true;
    entry.edges.visible = true;
    clearGhost();
    // Commit to new position if free, otherwise snap back
    if (drag.ghostGx !== null && isFree(drag.ghostGx, drag.ghostGy, drag.ghostGz, entry.dims)) {
      entry.gx = drag.ghostGx;
      entry.gy = drag.ghostGy;
      entry.gz = drag.ghostGz;
      const center = meshCenter(entry.gx, entry.gy, entry.gz, entry.dims);
      entry.mesh.position.copy(center);
      entry.edges.position.copy(center);
    }
    occupyCells(entry, true);
    drag.ghostGx = null;
    drag.ghostGy = null;
    drag.ghostGz = null;
    return;
  }

  if (!_pDown) return;
  const moved = Math.hypot(e.clientX - _pDown.x, e.clientY - _pDown.y) > 5;
  const btn = _pDown.btn;
  _pDown = null;
  if (moved) return;
  updatePointer(e);
  if (btn === 0) {
    if (state.eraseMode) { erasePiece(e); return; }
    if (!state.selected) {
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(state.placed.map(p => p.mesh));
      if (hits.length) {
        const entry = state.placed.find(p => p.mesh === hits[0].object);
        if (entry) {
          if (e.shiftKey) {
            selectPiece(entry.piece); // shift+click → enter placement mode
          } else {
            state.inspected = entry;
            state.shapeIdx = entry.piece.shapes.indexOf(entry.shapeId);
            updateInspector();
          }
        }
      } else {
        state.inspected = null;
        updateInspector();
      }
      return;
    }
    const pos = getGridPos();
    if (pos && isFree(pos.gx, pos.gy, pos.gz, state.selected.dims)) placePiece(pos.gx, pos.gy, pos.gz);
  }
  if (btn === 2) {
    if (state.eraseMode) erasePiece(e);
    else if (state.selected) selectPiece(null);
  }
});

renderer.domElement.addEventListener('contextmenu', e => e.preventDefault());
renderer.domElement.addEventListener('pointerleave', () => { if (!drag.active) clearGhost(); });

// ── Keyboard shortcuts + camera movement ─────────────────────────────────────

const keysHeld = new Set();

window.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  keysHeld.add(e.code);
  if (e.code === 'Space') e.preventDefault();
  if (e.repeat) return;
  if (e.code === 'KeyE') toggleErase();
  if (e.code === 'Escape') selectPiece(null);
});

window.addEventListener('keyup', e => keysHeld.delete(e.code));

const _fwd   = new THREE.Vector3();
const _right = new THREE.Vector3();
const _delta = new THREE.Vector3();
const _up    = new THREE.Vector3(0, 1, 0);

function moveCamera() {
  const moving = keysHeld.has('KeyW') || keysHeld.has('KeyS') ||
                 keysHeld.has('KeyA') || keysHeld.has('KeyD') ||
                 keysHeld.has('Space') || keysHeld.has('ControlLeft') || keysHeld.has('ControlRight');
  if (!moving) return;
  const shift = keysHeld.has('ShiftLeft') || keysHeld.has('ShiftRight');
  const speed = shift ? 0.6 : 0.2;
  camera.getWorldDirection(_fwd);
  _fwd.y = 0; _fwd.normalize();
  _right.crossVectors(_fwd, _up).normalize();
  _delta.set(0, 0, 0);
  if (keysHeld.has('KeyW')) _delta.addScaledVector(_fwd,    speed);
  if (keysHeld.has('KeyS')) _delta.addScaledVector(_fwd,   -speed);
  if (keysHeld.has('KeyA')) _delta.addScaledVector(_right, -speed);
  if (keysHeld.has('KeyD')) _delta.addScaledVector(_right,  speed);
  if (keysHeld.has('Space'))                                    _delta.y += speed;
  if (keysHeld.has('ControlLeft') || keysHeld.has('ControlRight')) _delta.y -= speed;
  camera.position.add(_delta);
  controls.target.add(_delta);
}


// ── Erase mode ───────────────────────────────────────────────────────────────

function toggleErase() {
  state.eraseMode = !state.eraseMode;
  const btn = document.getElementById('btn-erase');
  btn.classList.toggle('active', state.eraseMode);
  btn.classList.toggle('danger', state.eraseMode);
  clearGhost();
}
document.getElementById('btn-erase').addEventListener('click', toggleErase);

// ── Symmetry buttons ─────────────────────────────────────────────────────────

document.querySelectorAll('.sym-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const key = btn.dataset.axis === 'x' ? 'mx' : btn.dataset.axis === 'y' ? 'my' : 'mz';
    if (state.inspected) {
      state.inspected[key] = !state.inspected[key];
      rebuildPlacedMesh(state.inspected, state.inspected.shapeId, state.inspected.mx, state.inspected.my, state.inspected.mz);
    } else {
      state[key] = !state[key];
      if (state.ghostGx !== null) updateGhost(state.ghostGx, state.ghostGy, state.ghostGz);
    }
  });
});

// ── Save / Load / Clear ───────────────────────────────────────────────────────

document.getElementById('btn-save').addEventListener('click', () => {
  const data = state.placed.map(e => ({
    pieceId: e.piece.id, shapeId: e.shapeId, mx: e.mx, my: e.my, mz: e.mz, gx: e.gx, gy: e.gy, gz: e.gz,
  }));
  const json = JSON.stringify(data, null, 2);
  navigator.clipboard.writeText(json).then(() => alert('Design copied to clipboard as JSON.'));
});

document.getElementById('btn-load').addEventListener('click', () => {
  const raw = prompt('Paste design JSON:');
  if (!raw) return;
  try {
    const data = JSON.parse(raw);
    clearAll();
    data.forEach(({ pieceId, shapeId, mx, my, mz, gx, gy, gz }) => {
      const piece = CATALOG.find(p => p.id === pieceId);
      if (!piece) return;
      state.selected = piece;
      state.shapeIdx = piece.shapes.indexOf(shapeId);
      state.mx = mx ?? false; state.my = my ?? false; state.mz = mz ?? false;
      setLayer(gy);
      placePiece(gx, gz);
    });
    state.selected = null;
    updateInspector();
  } catch (err) {
    alert('Invalid JSON: ' + err.message);
  }
});

function clearAll() {
  state.placed.forEach(e => {
    scene.remove(e.mesh); scene.remove(e.edges);
    e.mesh.geometry.dispose(); e.mesh.material.dispose(); e.edges.geometry.dispose();
  });
  state.placed.length = 0;
  occupation.clear();
  clearGhost();
}
document.getElementById('btn-clear').addEventListener('click', () => {
  if (confirm('Clear all pieces?')) clearAll();
});

// ── Palette UI ────────────────────────────────────────────────────────────────

function buildPalette(filter = '') {
  const list = document.getElementById('piece-list');
  list.innerHTML = '';
  const lower = filter.toLowerCase();
  const grouped = {};
  CATALOG.forEach(p => {
    if (lower && !p.name.toLowerCase().includes(lower)) return;
    (grouped[p.category] ??= []).push(p);
  });

  Object.entries(grouped).forEach(([cat, pieces]) => {
    const hdr = document.createElement('div');
    hdr.className = 'category-header';
    hdr.textContent = cat;
    hdr.addEventListener('click', () => {
      hdr.classList.toggle('collapsed');
      hdr.nextSibling?.querySelectorAll?.('.piece-item').forEach(el => {
        el.style.display = hdr.classList.contains('collapsed') ? 'none' : '';
      });
    });
    list.appendChild(hdr);
    const group = document.createElement('div');
    pieces.forEach(p => {
      const item = document.createElement('div');
      item.className = 'piece-item' + (state.selected?.id === p.id ? ' selected' : '');
      item.textContent = p.name;
      item.title = p.name;
      item.addEventListener('click', () => selectPiece(state.selected?.id === p.id ? null : p));
      group.appendChild(item);
    });
    list.appendChild(group);
  });
}

document.getElementById('search').addEventListener('input', e => buildPalette(e.target.value));

// ── Inspector UI ──────────────────────────────────────────────────────────────

function updateInspector() {
  const p = state.inspected?.piece ?? state.selected;
  document.getElementById('piece-name').textContent = p ? p.name : 'Select a piece';
  document.getElementById('piece-dims').textContent = p ? `${p.dims[0]}×${p.dims[2]}×${p.dims[1]}` : '';

  const shapePicker = document.getElementById('shape-picker');
  shapePicker.innerHTML = '';
  if (p) {
    const activeIdx = state.inspected
      ? p.shapes.indexOf(state.inspected.shapeId)
      : state.shapeIdx;
    p.shapes.forEach((sid, i) => {
      const btn = document.createElement('button');
      btn.className = 'shape-btn' + (i === activeIdx ? ' active' : '');
      btn.textContent = SHAPES[sid]?.label ?? sid;
      btn.addEventListener('click', () => {
        shapePicker.querySelectorAll('.shape-btn').forEach((b, j) => b.classList.toggle('active', j === i));
        if (state.inspected) {
          rebuildPlacedMesh(state.inspected, p.shapes[i], state.inspected.mx, state.inspected.my, state.inspected.mz);
        } else {
          state.shapeIdx = i;
          if (state.ghostGx !== null) updateGhost(state.ghostGx, state.ghostGy, state.ghostGz);
        }
      });
      shapePicker.appendChild(btn);
    });
  }

  const specs = document.getElementById('specs');
  specs.innerHTML = '';
  if (p) {
    Object.entries(p.specs).forEach(([k, v]) => {
      const row = document.createElement('div');
      row.className = 'spec-row';
      row.innerHTML = `<span>${k}</span><span>${v}</span>`;
      specs.appendChild(row);
    });
  }
}

function selectPiece(p) {
  state.selected = p;
  state.inspected = null;
  state.shapeIdx = 0;
  buildPalette(document.getElementById('search').value);
  updateInspector();
  clearGhost();
}

// ── Resize ────────────────────────────────────────────────────────────────────

function resize() {
  const vp = document.getElementById('viewport');
  const w = vp.clientWidth, h = vp.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
resize();

// ── Animation loop ────────────────────────────────────────────────────────────

function animate() {
  requestAnimationFrame(animate);
  moveCamera();
  controls.update();
  renderer.render(scene, camera);
}

// ── Init ──────────────────────────────────────────────────────────────────────

buildPalette();
updateInspector();
animate();
