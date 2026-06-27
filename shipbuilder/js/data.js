export let PARTS = [], BYID = {}, GROUPS = [], PALETTE = [];

// Ship stats data — keyed by normalised part name for fast lookup.
// Source: ship_stats_data.json (fan data; replace entries with pak_out values when available).
let STATS_BY_NAME = {};

export async function loadStats() {
  try {
    const data = await fetch('ship_stats_data.json').then(r => r.json());
    STATS_BY_NAME = {};
    for (const p of (data.parts || [])) {
      STATS_BY_NAME[normName(p.name)] = p;
    }
  } catch (e) {
    console.warn('[stats] ship_stats_data.json not loaded:', e);
  }
}

/** Return the stats record for a placed part (matched by name), or null. */
export function statsFor(partName) {
  return STATS_BY_NAME[normName(partName)] || null;
}

function normName(s) {
  return (s || '').trim().toUpperCase().replace(/\s+/g, ' ');
}

// ---------------------------------------------------------------------------
// Ship stat calculations
// Each function accepts a shipItems array: [{ partName, qty }]
// All formulas are fan-sourced — replace when official values are confirmed.
// ---------------------------------------------------------------------------

function sumStat(shipItems, field, sourceFilter) {
  return shipItems.reduce((acc, { partName, qty }) => {
    const s = statsFor(partName);
    if (!s) return acc;
    if (sourceFilter && s.source !== sourceFilter) return acc;
    return acc + ((s[field] || 0) * qty);
  }, 0);
}

/** Total weight in tons (HULL parts only). */
export function calcWeight(shipItems)    { return sumStat(shipItems, 'weight', 'HULL'); }

/** Total frame strength (HULL parts only). */
export function calcFrames(shipItems)    { return sumStat(shipItems, 'frame', 'HULL'); }

/** Total SP capacity (HULL parts only). */
export function calcSPCapacity(shipItems){ return sumStat(shipItems, 'sp', 'HULL'); }

/** Total SP consumed (sp_usage for HULL, sp_used for PARTS). */
export function calcSPUsed(shipItems) {
  return shipItems.reduce((acc, { partName, qty }) => {
    const s = statsFor(partName);
    if (!s) return acc;
    const usage = s.source === 'HULL' ? (s.sp_usage || 0) : (s.sp_used || 0);
    return acc + usage * qty;
  }, 0);
}

/** Engine force (must exceed weight for the ship to fly). */
export function calcForce(shipItems)     { return sumStat(shipItems, 'force'); }

/** Total thrust. */
export function calcThrust(shipItems)    { return sumStat(shipItems, 'thrust'); }

/** Total steering strength. */
export function calcSteering(shipItems)  { return sumStat(shipItems, 'steering'); }

/** Total power usage (normal mode). */
export function calcPowerUsage(shipItems){ return sumStat(shipItems, 'power_usage'); }

/** Total power generation. */
export function calcPowerGen(shipItems)  { return sumStat(shipItems, 'power_gen'); }

/** Total battery storage. */
export function calcBattery(shipItems)   { return sumStat(shipItems, 'battery'); }

/** Total recharge speed. */
export function calcRecharge(shipItems)  { return sumStat(shipItems, 'recharge'); }

/** Total heat generation (negative = cooling). */
export function calcHeat(shipItems)      { return sumStat(shipItems, 'heat'); }

/** Total cargo space. */
export function calcCargo(shipItems)     { return sumStat(shipItems, 'cargo'); }

/**
 * Ship integrity = 200 − (7 × weight²) / (25 × frames).
 * Returns null when no frames are present.
 * Warning threshold: < 20.
 * Fan-sourced formula — verify against game when possible.
 */
export function calcIntegrity(shipItems) {
  const w = calcWeight(shipItems);
  const f = calcFrames(shipItems);
  if (f <= 0) return null;
  return 200 - (7 * w * w) / (25 * f);
}

/**
 * Ship maneuverability = 280 × steering / weight^1.5.
 * Returns null when weight is 0.
 * Fan-sourced formula — verify against game when possible.
 */
export function calcManeuverability(shipItems) {
  const w = calcWeight(shipItems);
  if (w <= 0) return null;
  return 280 * calcSteering(shipItems) / Math.pow(w, 1.5);
}

/**
 * SP efficiency: 100% when under capacity; degrades as (2 − used/cap) × 100% when over.
 * Returns null when SP capacity is 0.
 */
export function calcEfficiency(shipItems) {
  const cap  = calcSPCapacity(shipItems);
  const used = calcSPUsed(shipItems);
  if (cap <= 0) return null;
  if (used <= cap) return 100;
  return Math.max(0, Math.round((2 - used / cap) * 100));
}

export async function loadData() {
  const data = await fetch('ship_editor_data.json').then(r => r.json());
  PARTS = data.parts;
  PARTS.forEach(p => {
    BYID[p.id] = p;
    p._paintHex = (/frames|plating/i.test(p.group || '') ? p.color : (gradMainHex(p.grad) || p.color)) || '#5e7ca2';
    p._dimd = /\d+x\d+x\d+/.test(p.name || '');
    p._cockpit = p.type === 'ShipCockpit';
  });
  GROUPS = data.groupOrder || [];
  PALETTE = data.palette || [];
}

function gradMainHex(g) {
  if (!g || !g.c || !g.c.length) return null;
  const n = g.c.length, pos = g.p || [];
  let i = 0;
  while (i < n - 1 && (pos[i + 1] != null ? pos[i + 1] : (i + 1) / (n - 1)) < 0.6) i++;
  return g.c[i];
}
