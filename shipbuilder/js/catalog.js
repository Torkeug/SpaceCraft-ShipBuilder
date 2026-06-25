// dims: [lx, ly, lz] = [length(X), height(Y), depth(Z)]
// Game notation "LxWxH" maps to: lx=L, lz=W, ly=H
// shapes: which shape variants are available for this piece
// specs: arbitrary key/value pairs shown in inspector

export const CATALOG = [
  // ── COCKPIT ──────────────────────────────────────────
  {
    id: 'cocoon_cockpit',
    name: '"Cocoon" Cockpit',
    category: 'COCKPIT',
    dims: [3, 3, 3],
    shapes: ['block'],
    specs: { 'System Support': '480 sp', 'Weight': '120 t' },
  },

  // ── THRUSTER ─────────────────────────────────────────
  {
    id: 'grasshopper',
    name: '"Grasshopper" Thruster',
    category: 'THRUSTER',
    dims: [3, 3, 3],
    shapes: ['block'],
    specs: { 'Engine Force': '300 t', 'Weight': '80 t' },
  },

  // ── WING ─────────────────────────────────────────────
  {
    id: 'warden_spoiler',
    name: 'Thermal "Warden" Spoiler',
    category: 'WING',
    dims: [4, 1, 3],
    shapes: ['block', 'wedge'],
    specs: { 'Weight': '20 t' },
  },

  // ── DECORATIVE ───────────────────────────────────────
  {
    id: 'big_intake_vent',
    name: 'Big Intake Vent',
    category: 'DECORATIVE',
    dims: [2, 2, 2],
    shapes: ['block'],
    specs: {},
  },
  {
    id: 'intake_vent',
    name: 'Intake Vent',
    category: 'DECORATIVE',
    dims: [1, 1, 2],
    shapes: ['block'],
    specs: {},
  },
  {
    id: 'round_hatch',
    name: 'Round Hatch',
    category: 'DECORATIVE',
    dims: [2, 1, 2],
    shapes: ['block'],
    specs: {},
  },

  // ── ALU-K-PLATED STEEL HULL ──────────────────────────
  {
    id: 'thermal_4x3x1',
    name: 'Thermal Steel 4×3×1',
    category: 'HULL',
    dims: [4, 1, 3],
    shapes: ['block', 'wedge', 'corner', 'inv_corner', 'ridge'],
    specs: { 'Frame': '18', 'Hull': '15', 'Weight': '30 t', 'Heat Cap.': '30 MJ/k' },
  },
  {
    id: 'thermal_4x3x2',
    name: 'Thermal Steel 4×3×2',
    category: 'HULL',
    dims: [4, 2, 3],
    shapes: ['block', 'wedge', 'corner', 'inv_corner', 'ridge'],
    specs: { 'Frame': '36', 'Hull': '30', 'Weight': '60 t', 'Heat Cap.': '60 MJ/k' },
  },
  {
    id: 'thermal_8x3x2',
    name: 'Thermal Steel 8×3×2',
    category: 'HULL',
    dims: [8, 2, 3],
    shapes: ['block', 'wedge', 'corner', 'inv_corner', 'ridge'],
    specs: { 'Frame': '72', 'Hull': '30', 'Weight': '60 t', 'Heat Cap.': '60 MJ/k' },
  },
];

export const CATEGORY_COLORS = {
  COCKPIT:    0x2266aa,
  THRUSTER:   0xaa3322,
  WING:       0x226633,
  DECORATIVE: 0x886622,
  HULL:       0x556677,
};
