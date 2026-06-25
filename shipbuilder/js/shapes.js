import * as THREE from 'three';

function buf(floats, idx) {
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(floats, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return g;
}

// All geo(lx, ly, lz): lx=length(X), ly=height(Y), lz=depth(Z), centered at origin.
export const SHAPES = {
  block: {
    label: 'Block',
    geo(lx, ly, lz) { return new THREE.BoxGeometry(lx, ly, lz); },
  },

  // Ramp: full height at back (-Z), zero height at front (+Z)
  wedge: {
    label: 'Wedge',
    geo(lx, ly, lz) {
      const [hx, hy, hz] = [lx / 2, ly / 2, lz / 2];
      return buf(
        // 0=left-bot-back  1=right-bot-back  2=right-bot-front  3=left-bot-front
        // 4=left-top-back  5=right-top-back
        [-hx,-hy,-hz,  hx,-hy,-hz,  hx,-hy,hz,  -hx,-hy,hz,  -hx,hy,-hz,  hx,hy,-hz],
        [0,1,2, 0,2,3,   // bottom (-Y)
         0,4,5, 0,5,1,   // back   (-Z)
         0,3,4,           // left   (-X)
         1,5,2,           // right  (+X)
         4,3,2, 4,2,5]   // slope  (+Y+Z)
      );
    },
  },

  // Corner: full height at left-back corner only (apex), slopes to all front/right edges
  corner: {
    label: 'Corner',
    geo(lx, ly, lz) {
      const [hx, hy, hz] = [lx / 2, ly / 2, lz / 2];
      return buf(
        // 0=left-bot-back  1=right-bot-back  2=right-bot-front  3=left-bot-front
        // 4=left-top-back (apex)
        [-hx,-hy,-hz,  hx,-hy,-hz,  hx,-hy,hz,  -hx,-hy,hz,  -hx,hy,-hz],
        [0,1,2, 0,2,3,   // bottom (-Y)
         0,3,4,           // left   (-X)
         0,4,1,           // back   (-Z)
         1,4,2,           // right slope
         3,2,4]           // front slope
      );
    },
  },

  // Inv-corner: apex at right-front (mirror of corner)
  inv_corner: {
    label: 'Inv.Corner',
    geo(lx, ly, lz) {
      const [hx, hy, hz] = [lx / 2, ly / 2, lz / 2];
      return buf(
        // 0=left-bot-back  1=right-bot-back  2=right-bot-front  3=left-bot-front
        // 4=right-top-front (apex)
        [-hx,-hy,-hz,  hx,-hy,-hz,  hx,-hy,hz,  -hx,-hy,hz,  hx,hy,hz],
        [0,1,2, 0,2,3,   // bottom (-Y)
         1,4,2,           // right  (+X)
         2,4,3,           // front  (+Z)
         0,1,4, 0,4,3]   // two slopes (back-left)
      );
    },
  },

  // Ridge: full height along center line (X-axis), slopes down to both Z edges
  ridge: {
    label: 'Ridge',
    geo(lx, ly, lz) {
      const [hx, hy, hz] = [lx / 2, ly / 2, lz / 2];
      return buf(
        // 0=left-bot-back  1=right-bot-back  2=right-bot-front  3=left-bot-front
        // 4=left-top-center  5=right-top-center
        [-hx,-hy,-hz,  hx,-hy,-hz,  hx,-hy,hz,  -hx,-hy,hz,  -hx,hy,0,  hx,hy,0],
        [0,1,2, 0,2,3,     // bottom
         0,4,5, 0,5,1,     // back slope
         3,2,5, 3,5,4,     // front slope
         0,3,4,             // left tri
         1,5,2]             // right tri
      );
    },
  },
};

export const SHAPE_IDS = Object.keys(SHAPES);
