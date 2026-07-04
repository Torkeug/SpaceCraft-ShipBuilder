export let PARTS = [], BYID = {}, GROUPS = [], PALETTE = [];

// Ship-stat formula constants and attribute display names/units — both real
// data.cdb content, dumped by tools/extract_ship_stats.py. See that script's
// docstring and the plan notes for how each formula below was verified
// against the game's own compiled code (logic.ShipStats.hx).
export let CONST = {}, ATTRS = {}, ENVIRONMENTS = [];

export async function loadConstants() {
  const [consts, attrs, environments] = await Promise.all([
    fetch('ship_constants.json').then(r => r.json()),
    fetch('ship_attributes.json').then(r => r.json()),
    fetch('ship_environments.json').then(r => r.json()),
  ]);
  CONST = consts;
  ATTRS = attrs;
  // Real planet temperature bands (data.cdb attribute.props.tempRange, verified
  // against ent.Planet.calcBaseTemperature), plus the real in-system space
  // baseline (st.ShipSystems.getSystemTemperature / ent.System.calcTemperature):
  // a normal system sits at 0 (internal) -- this is what ships actually
  // experience flying around in space day-to-day, confirmed via raw
  // opcodes/decompile. The game also defines a "SystemHot" system flag
  // (randomized within SystemHotTemperature, 55..75 internal) and a much
  // colder OuterSpaceTemperature (-164) for interstellar transit between
  // systems, but neither is used here: no system in the current game data
  // actually carries the "SystemHot" attribute (confirmed by checking every
  // system), and interstellar transit is a rare edge case, not normal
  // flight -- a flat -164 "Space" would make every ship look like it's
  // freezing, which doesn't match real behavior (ships don't freeze in
  // normal space, only in Frozen planet environments). All in the internal
  // heat-ratio domain -- use calcVisibleTemperature() to get real degC.
  ENVIRONMENTS = [
    { id: 'Space', name: 'Space', min: 0, max: 0, kind: 'space' },
    ...environments,
  ];
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

// ---------------------------------------------------------------------------
// Heat / temperature — ported from st.ShipSystems (get_temperature,
// getOverheatProgress) and the shared convertTemperature() helper (decompiled
// via hlbc). Verified: calcVisibleTemperature(CONST.OverheatTemperature) =
// 190.48, matching the game's own in-editor "~190°C" overheat readout exactly,
// and the real planet environment tempRanges (e.g. Very Hot's 55..75 internal)
// convert to plausible real degC (224..388) once run through this same curve.
// ---------------------------------------------------------------------------

/**
 * Convert an internal heat-ratio value (heat/heatCapacity, or a raw
 * data.cdb tempRange endpoint — both live in this same domain) to the real
 * degC shown to players. Piecewise quadratic through three known points
 * ((-100,-110), (0,17.3), (100,660)) with a fixed slope at 0.
 */
export function calcVisibleTemperature(t) {
  const slope = CONST.VisibleTempSlopeAt0, t0 = CONST.VisibleTempAtTrueTemp0;
  const coeff = t < 0
    ? (CONST.VisibleTempAtTrueTempNeg100 + slope * 100 - t0) / 10000
    : (CONST.VisibleTempAtTrueTempPos100 - slope * 100 - t0) / 10000;
  return coeff * t * t + slope * t + t0;
}

/** Ship's current internal temperature ratio (get_temperature). */
export function calcInternalTemperature(heat, heatCapacity) {
  return heatCapacity > 0 ? CONST.HeatToTemperatureRatio * heat / heatCapacity : 0;
}

// The real engine's frame-rate default (hxd/Timer.hx in the actual Heaps.io
// source, cloned locally at tools/heaps_ref/heaps: `public static var
// wantedFPS = 60.`). st.ShipSystems.updateHeat's natural-heat-exchange term
// is expressed per-frame at this rate, so it's needed to get a real per-
// second rate constant, not a guess.
const WANTED_FPS = 60;

/**
 * Scale a "while active" heat source (BoosterHeatGeneration, tool
 * ActiveHeatGeneration, ...) by system efficiency and environment, per the
 * real byproductHeatProd formula in st.ShipSystems.updateHeat — verified via
 * raw opcodes (this branch's decompiled pseudocode had garbled control
 * flow, so the raw bytecode was checked directly rather than trusted as-is).
 * A cold-planet environment MULTIPLIES by efficiency (so an inefficient
 * ship produces LESS heat there); any other planet DIVIDES by efficiency
 * (with ByproductHeatDiffHotPlanetScale applied); deep space DIVIDES by
 * efficiency with no scale constant. Baseline heater/radiator heat
 * (HeatGeneration/HeatDissipation) is NOT scaled this way in the real game —
 * it goes through a separate power-availability path instead, so don't pass
 * those through this function.
 */
export function calcActiveHeatWithEfficiency(rawActiveHeat, efficiency, envKind) {
  const eff = efficiency > 0 ? efficiency : 0.01; // matches SystemEfficiency's own real clamp floor
  if (envKind === 'cold') return CONST.ByproductHeatProdColdPlanetScale * rawActiveHeat * eff;
  if (envKind === 'planet') return CONST.ByproductHeatDiffHotPlanetScale * rawActiveHeat / eff;
  return rawActiveHeat / eff; // deep space
}

/**
 * Real time-to-overheat, from a cold start (heat=0, a freshly-assembled/idle
 * ship), solving the actual heat ODE from st.ShipSystems.updateHeat:
 *   dHeat/dt = rate×(targetHeat − heat) + activeNetHeat
 * where targetHeat is the heat level in equilibrium with the external
 * environment, and rate = 1 − (1 − k)^wantedFPS is the real per-second
 * natural-exchange rate (k = adjustScale × HeatToTemperatureRatio ×
 * heatInterface / heatCapacity). This is a linear ODE with equilibrium
 * heq = targetHeat + activeNetHeat/rate; solved in closed form below — this
 * is the actual mechanic (a poorly-insulated/cooled ship can overheat from
 * ambient environment heat alone, thrusters off entirely, if heq exceeds the
 * overheat threshold), not an approximation of it.
 *
 * `activeNetHeat`: constant situational heat sources (HeatGeneration −
 * HeatDissipation, optionally + BoosterHeatGeneration − engine cooling while
 * boosting) — NOT the natural exchange, which this function computes itself.
 * `envTempInternal`: external temperature in the same internal heat-ratio
 * domain as OverheatTemperature (an ENVIRONMENTS entry's min/max midpoint,
 * NOT calcVisibleTemperature's real-degC output). `isPlanet`: false for deep
 * space (NaturalHeatSpaceAdjustScale), true for a planet environment
 * (NaturalHeatPlanetAdjustScale) — matches the real branch in updateHeat.
 */
export function calcTimeToOverheat(activeNetHeat, heatCapacity, heatInterface, envTempInternal, isPlanet) {
  if (heatCapacity <= 0) return null;
  const overheatHeat = heatCapacity * CONST.OverheatTemperature / CONST.HeatToTemperatureRatio;
  const adjustScale = isPlanet ? CONST.NaturalHeatPlanetAdjustScale : CONST.NaturalHeatSpaceAdjustScale;
  const k = adjustScale * CONST.HeatToTemperatureRatio * heatInterface / heatCapacity;
  const rate = 1 - Math.pow(1 - k, WANTED_FPS);

  if (rate <= 0) {
    // No natural exchange with the environment at all (e.g. heatInterface=0) —
    // heat grows purely linearly from the active sources.
    return activeNetHeat > 0 ? overheatHeat / activeNetHeat : null;
  }
  const targetHeat = heatCapacity * envTempInternal / CONST.HeatToTemperatureRatio;
  const heq = targetHeat + activeNetHeat / rate;
  if (heq <= overheatHeat) return null; // settles at/below the threshold — never gets there
  return -Math.log(1 - overheatHeat / heq) / rate;
}

/**
 * Real engine-cooling-while-thrusting formula (st.ShipSystems.updateHeat):
 * Σ(EngineHeatDissipation × BoostThrust/EngineThrust) × ship.totalThrust.
 * Lower confidence than most formulas here — this segment of the decompile
 * had control-flow reconstruction issues (see calcBatteryAggregate's similar
 * caveat), so the expression itself is faithfully ported but not opcode-
 * verified. `thrusters`: [{engineHeatDissipation, boostThrust, engineThrust}].
 */
export function calcEngineCooling(thrusters, totalThrust) {
  const totDissip = thrusters.reduce((a, t) => {
    if (!t.engineThrust || !t.engineHeatDissipation) return a;
    return a + t.engineHeatDissipation * (t.boostThrust || 0) / t.engineThrust;
  }, 0);
  return totDissip * totalThrust;
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
