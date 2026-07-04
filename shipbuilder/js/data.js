export let PARTS = [], BYID = {}, GROUPS = [], PALETTE = [];

// Ship-stat formula constants and attribute display names/units — both real
// data.cdb content, dumped by tools/extract_ship_stats.py. See that script's
// docstring and the plan notes for how each formula below was verified
// against the game's own compiled code (logic.ShipStats.hx).
export let CONST = {}, ATTRS = {};

export async function loadConstants() {
  const [consts, attrs] = await Promise.all([
    fetch('ship_constants.json').then(r => r.json()),
    fetch('ship_attributes.json').then(r => r.json()),
  ]);
  CONST = consts;
  ATTRS = attrs;
}

/** Real in-game display name for an attribute id, falling back to the id itself. */
export function attrLabel(id) { return ATTRS[id]?.name || id; }

/** False for internal engine flags the real game never shows to players (no data.cdb display name). */
export function isDisplayable(id) { return id in ATTRS; }

/** Real in-game unit for an attribute id ('' if unitless/unknown). */
export function attrUnit(id) { return ATTRS[id]?.unit || ''; }

// ---------------------------------------------------------------------------
// Ship stat formulas — ported from logic.ShipStats.calcStats/getPointsValue
// (decompiled via hlbc against the shipped hlboot.dat) and numerically
// verified against a real single-part example (Alloy X 8x6x2: Integrity
// 197.2%, Hull 39/20, Heat Interface 2811.2, Ship Points 46, System
// Efficiency 100%, Speed 5 (5.00), Maneuverability 0).
// ---------------------------------------------------------------------------

/** Ship integrity ratio (1.0 = 100%). Null when there's no frame yet. */
export function calcIntegrity(weight, frame) {
  if (frame <= 0) return null;
  return 2 - (CONST.ShipStatIntegrityFactor * weight * weight) / frame;
}

/** Displayed current Hull = floor(rawHull × integrity). */
export function calcHullDisplay(rawHull, integrity) {
  return Math.floor(rawHull * (integrity ?? 0));
}

/** System support capacity (ShipStatSysSupportScale is 1 in live data, so this is currently a passthrough). */
export function calcSystemSupport(rawSupport) {
  const n = CONST.ShipStatSysSupportNormalization;
  return n * Math.pow(rawSupport / n, CONST.ShipStatSysSupportScale);
}

/** System efficiency ratio (1.0 = 100%), clamped to [0.01, 1]. Null when there's no support capacity yet. */
export function calcSystemEfficiency(sysReq, sysSupport) {
  if (sysSupport <= 0) return null;
  return Math.min(1, Math.max(0.01, 2 - sysReq / sysSupport));
}

/** Heat Interface (MW/K) from the ship's total Parts Heat Interface stat. */
export function calcHeatInterface(heatInterfaceParts) {
  if (!heatInterfaceParts) return 0;
  return CONST.ShipStatHeatInterfaceScale * Math.pow(heatInterfaceParts, CONST.ShipStatHeatInterfacePower);
}

/** Ship Points (logic.ShipStats.getPointsValue) — a bespoke value score, not part of any stat category. */
export function calcShipPoints(hullDisplay, sysReq, sysMalus, sysSupport) {
  const cappedReq = Math.min(sysSupport, sysReq - sysMalus);
  const ptf = CONST.ShipPointsHullCoeff * hullDisplay + CONST.ShipPointsSupportCoeff * cappedReq;
  return Math.floor(CONST.ShipPointsCoeff * ptf);
}

/** Maneuverability. Null when there's no weight yet. */
export function calcManeuverability(steering, weight) {
  if (weight <= 0) return null;
  return CONST.ShipManeuvrabilityScale * (steering || 0) / Math.pow(weight, CONST.ShipManeuvrabilityWeightPower);
}

/** Decoration capacity = ceil(totalShipDecoSupport × ShipDecoSupportCoeff). */
export function calcMaxDecoPoints(shipDecoSupport) {
  return Math.ceil((shipDecoSupport || 0) * CONST.ShipDecoSupportCoeff);
}

/**
 * Space-context Max Speed / Max Boost Speed. `efficiency` is the ratio from
 * calcSystemEfficiency (1.0 = 100%). Negative speedFactor (underpowered ship)
 * clamps its pow() term to 0 rather than producing NaN — verified against
 * the raw opcodes for the engine-less single-part example (5 / 5.00).
 */
export function calcSpeed(thrust, force, weight, efficiency, boostThrust) {
  if (weight <= 0) return { speed: null, boostSpeed: null };
  const speedFactorScale = CONST.ShipStatSpeedFactorThrustScale * thrust
    + CONST.ShipStatSpeedFactorForceScale * (force - weight);
  const speedFactor = (CONST.ShipStatSpeedFactorScale * speedFactorScale / weight) * (efficiency ?? 1);
  const powTerm = speedFactor < 0 ? 0 : Math.pow(speedFactor, CONST.ShipStatSpaceSpeedPower);
  const sf = CONST.ShipStatSpaceSpeedFactor;
  const speed = (CONST.ShipStatDefaultSpaceSpeed * (1 + sf * powTerm)) / (1 + sf);

  const boostSpeedFactorScale = CONST.ShipStatBoostSpeedFactorThrustScale * (boostThrust || 0);
  const boostSpeedFactor = Math.max(0, CONST.ShipStatBoostSpeedFactorScale * boostSpeedFactorScale / weight);
  const boostSpeed = speed + CONST.ShipStatDefaultSpaceBoostSpeed * Math.pow(boostSpeedFactor, CONST.ShipStatSpaceBoostSpeedPower);
  return { speed, boostSpeed };
}

/**
 * Battery aggregate (Max Charge Speed / Theoretical Efficiency / Self-Discharge).
 * Ported from the per-battery weighted-average loop in calcStats; unlike the
 * other formulas here, this one has NOT been numerically verified against a
 * real example (the confirmed test ship had no batteries) — the decompiler's
 * iterator control-flow was mangled the same way it was for the thrust-bonus
 * loop, so double-check against a real multi-battery ship if precision here matters.
 * `batteries`: [{chargeSpeed, efficiency, wastage, powerStorage}], one per placed battery part.
 */
export function calcBatteryAggregate(batteries, totalPowerStorage) {
  if (!batteries.length) return { chargeSpeed: 0, efficiency: 0, wastage: 0 };
  const maxCS = Math.max(...batteries.map(b => b.chargeSpeed));
  let chargeSpeed = 0, efficiency = 0, wastage = 0, effFactor = 0;
  for (const b of batteries) {
    const { chargeSpeed: cs, efficiency: eff, wastage: waste, powerStorage: pow } = b;
    const exp = totalPowerStorage > 0 ? 1 - pow / totalPowerStorage : 1;
    chargeSpeed += maxCS > 0 ? cs * Math.pow(cs / maxCS, exp) : 0;
    efficiency += eff * cs * pow;
    effFactor += cs * pow;
    wastage += waste * pow;
  }
  return {
    chargeSpeed,
    efficiency: effFactor !== 0 ? efficiency / effFactor : 0,
    wastage: totalPowerStorage !== 0 ? wastage / totalPowerStorage : 0,
  };
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
