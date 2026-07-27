# Game logic notes (decompiled via hlbc)

`data.cdb` (see `CLAUDE.md`'s data pipeline) only holds *balance constants* and
static item/attribute definitions — it does not contain the code that combines
them. For actual gameplay formulas (damage, combat, movement math, etc.), the
real source is the compiled game logic itself: `hlboot.dat` in the game install
directory (see `CLAUDE.md` → "Game installation"), which is **HashLink
bytecode** (magic `HLB`) compiled from the game's original Haxe source. It
carries full debug info (source file + line numbers, local variable names),
so it decompiles to near-readable Haxe.

## Tooling

`hlbc` (https://github.com/Gui-Yom/hlbc) is a HashLink bytecode
disassembler/decompiler CLI. Two copies are available:
- Installed via cargo: `hlbc` on PATH (`C:\Users\Admin\.cargo\bin\hlbc.exe`).
- Vendored source + prebuilt exe: `tools/heaps_ref/hlbc_src` /
  `tools/heaps_ref/hlbc/hlbc.exe`.

Either works identically. Invoke non-interactively with `-c "<cmds>; exit"`
(commands are `;`-separated) — **always end with `exit`**, otherwise the
process drops into an interactive REPL waiting on stdin and hangs forever:

```
hlbc -c "sfile Ship; exit" "D:\SteamLibrary\steamapps\common\SpaceCraft\hlboot.dat"
```

**Gotcha:** function-listing commands (`infile`, `sfn`) print two numbers per
function, e.g. `9131: fn serverUpdateAsteroidFieldDamage@9132 (...)`. The
leading number (`9131`) is just the sequential position in the printed table —
**not** a usable index. The number after `@` (`9132`) is the real `findex`;
that's what `fn <findex>`, `fnh <findex>`, and `decomp <findex>` all expect.

Useful commands (see `hlbc -c "help; exit"` for the full list):
- `sfile <substr>` — find debug source files by substring (e.g. `sfile Ship`
  → lists every `src/**/Ship*.hx` compiled into the binary).
- `infile <idx>` — list every function compiled from a given source file.
- `sstr <substr>` — search the string pool (useful for finding `$Const.Foo`
  field names, enum tag names, RPC names referenced by string).
- `refto global@<idx>` / `refto string@<idx>` / `refto fn@<idx>` — find where
  a global/string/function is referenced.
- `decomp <findex>` — pseudocode decompile. Good first pass, but the
  decompiler (a young project) sometimes mangles `min`/`max`/ternary patterns
  into garbled `if` chains with leftover register names (e.g. `reg19`) — don't
  trust an odd-looking line at face value.
- `fn <findex>` — raw per-instruction disassembly with real local names and
  `src/File.hx:line` per opcode. **Use this to verify anything `decomp`'s
  output looks suspicious about** — it's unambiguous once you read the
  register flow (a `JNotLt`/`Mov`/`Mov`/`JAlways` triplet around two values is
  always a `min()` or `max()`; `JNotLt if reg !< reg` = "skip unless reg1 <
  reg2").

## Finding 1: Asteroid/debris-field DOT damage formula

Source: `ent.Ship.serverUpdateAsteroidFieldDamage` (`src/ent/Ship.hx:1935-1970`,
findex 9132), called every server tick with `dt`. Verified against raw opcodes
(the `decomp` pseudocode garbled the multiplier `min()`/`max()` clamps).

```
asteroidDensity = 0
if ship.planet != null:
    asteroidDensity = ship.planet.getAttributeValue("AsteroidDensity")
elif (ship.cinematicSystem ?? ship.currentSystem).hasAttribute("SystemDebris"):
    asteroidDensity = Const.SystemDebrisDensity   # = 3

if asteroidDensity == 0:
    return   # no field here, nothing to do

ship.timeSinceLastAsteroidHit += dt

# Poisson-style "does a hit resolve this tick" gate
generatingChance = 1 - exp(-sqrt(asteroidDensity) * dt / Const.AsteroidDotAverageTimeBetweenHits)
if random() >= generatingChance:
    return   # no hit this tick

# Two more gates before damage actually lands
if ship.timeSinceLastAsteroidHit <= Const.AsteroidDotMinDurationBetweenHits
   or random() >= Const.AsteroidHitChance:      # AsteroidHitChance = 0.2
    rpcIncomingAsteroidHit(null, null)           # client-side no-op ping
    return

damageMultiplier = min(Const.AsteroidDotMaxMultiplicator, -log(random())) \
                   + exp(-Const.AsteroidDotMaxMultiplicator)
# Const.AsteroidDotMaxMultiplicator = 3, so the exp() term is a fixed +0.0498 offset

hitDamage = Const.AsteroidAverageDot * asteroidDensity \
            * ship.timeSinceLastAsteroidHit * damageMultiplier
hitDamage = max(1, hitDamage)   # matches data.cdb's own comment on AsteroidAverageDot:
                                 # "does not take into account the minimum damage of 1 on hit"

ship.timeSinceLastAsteroidHit = 0
ship.takeDamage(hitDamage, DamageSource.Asteroid, immediateFeedback=false, onResult=...)
```

Constants (`data.cdb` `constant` sheet — see `craftmap/game_data_extract` for
the extraction pipeline, or read directly):

| Constant | Value |
|---|---|
| `AsteroidHitChance` | 0.2 |
| `AsteroidDotMinDurationBetweenHits` | 0.5 (s) |
| `AsteroidDotAverageTimeBetweenHits` | 1 (s) |
| `AsteroidAverageDot` | 2 |
| `AsteroidDotMaxMultiplicator` | 3 |
| `SystemDebrisDensity` | 3 |

`AsteroidDensity` itself (when orbiting a planet) is a per-planet attribute
value, not a fixed constant — read live from `planet.getAttributeValue`.
`SystemDebrisDensity` is only the fallback used for interstellar "debris
field" systems (flagged `SystemDebris` in `data.cdb`'s `sector.generation.
systemAttributes`), not for asteroid belts around a planet.

## Finding 2: Hull damage mitigation (shields + impact reduction)

Source: `ent.Unit.takeDamage__impl` (`src/ent/Unit.hx:519-557`, findex 8896) —
the shared damage-application path for *all* damage sources, not just
asteroids. `ent.Ship.takeDamage__impl` (findex 9052) is a thin per-ship
wrapper that just forwards into this one.

```
if unit.hull == 0:
    return amount   # already dead, no-op

damage = amount
if isShieldAbsorbed(source) and unit.shield != null:
    damage = unit.shield.absorbDamage(damage)   # shield's own logic (Max Charge / Damage Negation / Regen Delay)
shieldAbsorbed = amount - damage

if source == DamageSource.Collision or source == DamageSource.Asteroid:
    damage = damage * (1 - unit.getAttribute("HullImpactDamageReduction"))

unit.hull -= damage   # (clamped to 0 by set_hull)
unit.onTakeDamages(damage, shieldAbsorbed, hitOrigin)   # client feedback/VFX
```

So `HullImpactDamageReduction` (the "Hull Impact Damage" stat — armor plating
items carry this) only mitigates **collision/asteroid** damage, not other
damage sources (weapons, FTL, heat, pressure, etc. use their own separate
`*DamageReduction`/`*DamageNegation`/`*DamageAbsorption` attributes — see
`attribute` sheet ids `FTLDamageAbsorption`, `DamageNegation`, `Critical
HitDamageMultiplier`, etc. in `data.cdb`).

`isShieldAbsorbed(source)` (`src/ent/Unit.hx:510-514`, findex 9006) gates
which damage sources shields can even intercept — confirmed via raw opcodes
(a `Switch` over the `enum<ent.DamageSource>` variant index; the decompiler
panics on this function, opcodes only). The enum's construct order (from
`decompt`/`t` on its type, id 1525) is `0:Collision, 1:Asteroid, 2:FTLTravel,
3:SkillAccident, 4:CriticalTemperature, 5:StarTemperature,
6:PressureCrushing, 7:PressureHit, 8:Unit, 9:Resource, 10:Admin`.

| DamageSource | shield-absorbed? |
|---|---|
| Collision | false |
| **Asteroid** | **true** |
| FTLTravel, SkillAccident, CriticalTemperature, StarTemperature, PressureCrushing, PressureHit | false |
| Unit (weapon fire from another ship) | true |
| Resource | true |

So asteroid-field damage is mitigated twice, in order: (1) shields, if any
(`isShieldAbsorbed(Asteroid) == true`), then (2) `HullImpactDamageReduction`
on whatever gets past the shield (Collision and Asteroid are the only two
sources that check this attribute — see Finding 2 above). `hull` itself is
just the HP pool subtracted from at the end, not a mitigation stat.

## Finding 3: Agility (dodge stat)

`data.cdb`'s `attribute` sheet only has a "Display only" placeholder for this
(`CurrentAgility_Display`, desc: "Agility is the ship's ability to steer and
avoid dangerous projectiles coming towards it. It affects the ship's dodge
chance in combat."). The real computation is `ent.Unit.getAgility`
(`src/ent/Unit.hx:412-425`, findex 8890), read from raw opcodes:

```
getAgility(unit, includeStanceBonus = true):
    speedTerm = Const.GainAgilityFromSpeed * (stats.maxCombatSpeed - getCombatMinSpeed(unit))

    engineIgnoredWeight = unit.getAttribute("AgilityIgnoreWeightThroughEngineForce") * stats.force
    maxIgnorableWeight  = Const.MaxWeightPercentIgnoredForAgility * stats.weight
    ignoredWeight = engineIgnoredWeight > 0
        ? min(engineIgnoredWeight, maxIgnorableWeight)
        : engineIgnoredWeight        # <=0 -> nothing ignored

    weightPenalty = Const.LoseAgilityFromWeight * (stats.weight - ignoredWeight)

    if includeStanceBonus and unit.combat?.stance == "Slippery":
        speedTerm += Const.GainAgilityFromSlippery

    return speedTerm - weightPenalty
```

Constants (`data.cdb` `constant` sheet):

| Constant | Value |
|---|---|
| `GainAgilityFromSpeed` | 100 |
| `LoseAgilityFromWeight` | 0.008 |
| `GainAgilityFromSlippery` | 40 |
| `MaxWeightPercentIgnoredForAgility` | 0.4 |

Notes:
- `stats.force`/`stats.weight`/`stats.maxCombatSpeed` are the ship's own
  computed `logic.ShipStats` fields (same struct `extract_ship_stats.py`
  already pulls `$Const.ShipStat*` formula constants for).
- A ship with high `Force` (engine thrust) can have part of its `weight`
  excluded from the agility penalty via the `AgilityIgnoreWeightThroughEngineForce`
  attribute (only present on some engine/part types), capped at 40% of total
  weight (`MaxWeightPercentIgnoredForAgility`).
- The "Slippery" combat stance adds a flat +40 agility while active.
- `getCombatMinSpeed(unit)` = `Const.ShipStatDefaultCombatSpeed / X +
  Const.ShipStatCombatSpeedFactor` for some `X` — decompiled output showed
  `/1` here, which looks like a decompiler artifact (this constant pair is
  otherwise used as a `base / weight^power`-style curve everywhere else in
  `logic.ShipStats`, per `extract_ship_stats.py`'s `ShipStatCombatSpeedPower`).
  Not yet re-verified against raw opcodes — treat the exact minspeed formula
  as unconfirmed, only the two `Const` names are solid.

## Finding 4: Attribute aggregation (`AttribCalc`) and `HullImpactDamageReduction`'s real value

Ship-wide attribute values (`ent.Unit.getAttribute`, used by e.g.
`HullImpactDamageReduction` in Finding 2) mostly funnel down to a shared
generic aggregator, `st.dat.Ship.getAttribute` (findex 10803) → `lib.AttribCalc`
(`get@21034` / `get_value@21031`). A handful of computed ship stats
(`Hull`, `Integrity`, `Maneuvrability`, `MaxSpeed`, `SystemSupport`, etc.) are
special-cased earlier in `logic.ShipStats.getAttribute@10843` and never reach
this path — see the `calcStats` formulas in Finding 2's neighborhood above
instead for those.

For everything else, `st.dat.Ship.getAttribute(attrId)` walks every placed
piece, calling each piece's `calcAttribute(attrId, calc)` to accumulate into
an `AttribCalc { tot, max, count }`, then `get_value()` resolves it against
the attribute's own `data.cdb` flags:

```
if attribute.props.flags & (1 << 12):   # "percent" bit (4096)
    tot /= 100; max /= 100
if attribute.props.flags & (1 << 10):   # "use max, not sum" bit (1024)
    return max
return tot                              # default: plain sum across all pieces
```

So whether an attribute is summed-as-raw-number (e.g. `DamageNegation`,
flags unset — used directly as flat damage, see Finding "What is
DamageNegation" in conversation, no /100), summed-then-percent
(`HullImpactDamageReduction`, `flags: 4104 = 4096|8` → percent bit set, plain
sum otherwise), or max-of-all-pieces instead of summed, depends entirely on
that item's `attribute.props.flags` bitfield in `data.cdb` — **don't assume
a raw item attribute value's scale without checking its `flags`.**

Concretely, for `HullImpactDamageReduction`: raw values from every placed
piece are summed, then divided by 100. Only one item in the current catalogue
carries it — `Cockpit_MK1` ("Beaver" Cockpit) = `40` → `0.40` (40% reduction)
once equipped. Since aggregation here is plain sum (not diminishing-returns),
multiple sources would stack additively (two 40s → 80 → 0.80 → 80%
reduction), not multiplicatively — relevant if more sources get added later.

## Finding 5: Shipwreck spawn/despawn cycle

Source: `st.PlanetResourceManager` (`src/st/PlanetResourceManager.hx`,
findices 8098-8103) — `checkPerformShipWreckCycle`, `performShipWreckCycle`,
`getShipWreckCounts`, `generateShipwreck`, `getResGenConstant`,
`generateResGens`. Verified against raw opcodes (the removal-probability and
spawn-probability math both got mangled/reordered by `decomp`).

**Trigger** — not a background timer. `checkPerformShipWreckCycle` is only
called from `ent.Ship.serverRegularUpdate` (findex 9086), specifically the
branch that fires when a ship enters a new planet's orbit range
(`rawDistanceSq(planet, ship) < getOrbitSize(planet)²`), and only after
`planet.res.generate()` returns `false` (i.e. the planet's resources were
already generated — a freshly-generated planet skips the cycle check that
tick). So in practice: **any ship arriving in orbit of an already-generated
planet triggers a cycle check**, gated to run at most once per
`PlanetResGenWreckCyclePeriodDays` (in-game days, real value `0.5`) via
`lastShipWreckCycleTimeS`.

**Removal pass** (`performShipWreckCycle`) — for every existing
`ShipWreck_Core` root resource on the planet (`getShipWreckCounts` walks
`resources`, counting children per root whose `resourceInfs[...].id ==
"ShipWreck_Core"`):

```
timeSinceLastMinedS = now - wreck.lastMining
maxCount = wreck._maxCountLow | ((wreck._countExtra << 4) & 3840)   # packed bitfield
if maxCount <= 0:
    logError(...); proba = 1   # defensive fallback, shouldn't normally happen
else:
    proba = clamp(
        PlanetResGenWreckCycleRemovalFactor
            * (timeSinceLastMinedS - PlanetResGenWreckMinTimeToRemoveDays)
            * (1 - childCount / maxCount),
        0, 1)
if random() < proba:
    mark this wreck root for removal
```

So a wreck can only be removed once `timeSinceLastMinedS >
PlanetResGenWreckMinTimeToRemoveDays` (else the multiplier is negative →
proba clamps to 0), and the `(1 - childCount/maxCount)` term means a wreck
that's been fully mined out (`childCount` near `maxCount`... actually inverted:
low remaining child count relative to max → higher removal chance) decays
faster than one still full of loot. All marked wrecks are then actually
removed via `_remove`.

**Spawn pass** — after removal, using `currentCount = remaining wreck roots`:

```
planetWreckProba = getResGenConstant("PlanetResGenWreckProba")   # sector override, else $Const (0.6)
timeSinceLastSpawnD = (now - lastSpawnedShipWreckTimeS) / 86400   # seconds -> days
proba = 1 - pow(1 - planetWreckProba, timeSinceLastSpawnD - currentCount)
if random() < proba:
    generateShipwreck()
    lastSpawnedShipWreckTimeS = now
```

Note the exponent is `daysSinceLastSpawn - currentCount`, not just
`daysSinceLastSpawn` — every existing wreck on the planet directly reduces
the spawn exponent (and thus `proba`), so more wrecks present → lower chance
of another one appearing this cycle. If `currentCount` exceeds
`daysSinceLastSpawn`, the exponent goes negative, `pow(...)` exceeds 1, and
`proba` goes negative (never satisfies `random() < proba`) — an implicit soft
cap, though `MaxWreckPerPlanet` (`10`) is **not** referenced anywhere in this runtime
cycle (`checkPerformShipWreckCycle`/`performShipWreckCycle`/
`generateShipwreck`) — it's only used in the separate *initial* system/planet
content generation path (`logic.gen.SystemContent.generatePlanet`, findex
37394, `src/logic/gen/SystemContent.hx:371`, as a loop cap via
`Const.getInt("MaxWreckPerPlanet")` while counting/placing initial wreck
resources). So the ongoing per-cycle spawn/despawn logic documented above has
no hard cap of its own — only the negative-exponent soft cap — while the
one-time initial seeding of a new planet is separately capped at 10.

**Initial generation is fully deterministic and reproducible offline** —
`logic.gen.SystemContent.__constructor__`/`generate` (findex 37383,
`src/logic/gen/SystemContent.hx:42`) seeds its `hxd.Rand` (a seeded
multiply-with-carry PRNG, not `Math.random()`) from
`this.system.inf.genProps.seed` — a **fixed value stored per-system in
`data.cdb`** (sheet `system`, field `genProps.seed`; e.g. `Sys_Start` →
`55577`). Every downstream initial-placement call (`randRange`,
`randomResource`, `generatePlanet`, `generateAsteroid`, initial resource/wreck
seeding via `PlanetResGenMaxInitialWrecks`) draws from that same seeded RNG,
so a system's *initial* content is 100% reproducible from `data.cdb` alone,
with no live server access required — this is presumably how third-party
"sector/planet data" sites/tools work. `logic.gen.WorldLoader.syncSector`
(findex 12710) separately seeds its own `hxd.Rand` via a real CRC-32 over the
save's own `Server.__uid` (plus a few map sizes) for placing dynamic
mission/instance content per-save — that part is NOT reproducible from
`data.cdb` alone since it depends on the specific server/save's private uid.
Contrast this with the **ongoing** shipwreck spawn/despawn cycle above, which
uses real `random()` and wall-clock timestamps, not this seeded generator —
so initial layout is knowable offline, but current live wreck state on a
running server is not.

`getResGenConstant(k)` checks the current sector's
`sector.generation.constants` list for an override of `k` first, falling back
to the global `$Const` value — so `PlanetResGenWreckProba` can vary per
sector even though the fallback constant is fixed.

**Which wreck gets spawned** — `generateShipwreck` reads
`planet.system.sector.inf.generation.wreckResGen` (a per-sector array of
`{resGen: <id>}` rows, `data.cdb` sheet `sector@generation@wreckResGen`,
nested under the `sector` sheet), picks one **uniformly at random by index**
(`wrg[Std.random(wrg.length)]`), and generates it via the generic procedural
resource placer (`logic.gen.PlanetRes.run`, with exclusion zones from already
-placed resources). Since sectors list the same `resGen` id multiple times to
weight outcomes (e.g. `Sec_Terminus`: `ShipWreck_Small_0` ×4,
`ShipWreck_Big_0` ×1 → 80%/20%), tier/size odds are entirely a function of
how many times each id repeats in that sector's list, not a separate weight
field.

Constants (`data.cdb` `constant` sheet):

| Constant | Value | Note |
|---|---|---|
| `PlanetResGenWreckProba` | 0.6 | fallback; sector `generation.constants` can override |
| `PlanetResGenWreckCyclePeriodDays` | 0.5 | min real-time-equivalent gap between cycle checks on a planet, per its own doc comment: "checked on a planet when visited by a player" |
| `PlanetResGenWreckCycleRemovalFactor` | 5 | |
| `PlanetResGenWreckMinTimeToRemoveDays` | 0.05 | min days since last mine/collect before a wreck is even eligible for removal |
| `PlanetResGenMaxInitialWrecks` | 1 | caps wrecks placed during a planet's *initial* generation (separate from this cycle; not traced further) |
| `MaxWreckPerPlanet` | 10 | only enforced during *initial* planet generation (`SystemContent.generatePlanet`), not in the runtime cycle above |
| `RadarWreckFinderDistanceFactor` | 4 | multiplies detection range for radars with the `WreckFinder` attribute |

**Addendum (2026-07-22): exact spawn position is also genuinely randomized each
cycle, traced all the way from RNG seed to placed coordinate — not inferred
from watching outputs differ.** Raised directly by the user, who correctly
rejected an earlier draft of this finding that only observed several
non-repeating wreck positions in a live event log and called that
"confirmed random" — that observation can't distinguish true randomness from
a fixed pool of candidate slots. Settled instead by tracing the actual
mechanism in raw bytecode (findices below are current as of this game
build; the ones in the section above have drifted since this doc was first
written - `checkPerformShipWreckCycle`/`performShipWreckCycle`/
`generateShipwreck`/`generateResGens` are now 8106/8108/8110/8111, not
8098-8103; re-resolve by name via `sfn <name>` if they drift again).

**Call path, confirmed via `hlbc`'s `refto` (each hop below has exactly ONE
caller anywhere in the bytecode - checked directly, not assumed):**
```
ent.Ship.serverRegularUpdate@9094 (per-tick, orbit-arrival branch)
  -> checkPerformShipWreckCycle@8106
  -> performShipWreckCycle@8108        (the "Spawn pass" documented above)
  -> generateShipwreck@8110
  -> generateResGens@8111
  -> logic.gen.PlanetRes.run@11230
  -> logic.gen.PlanetRes.generateGroups@11231
```
This confirms `generateResGens` is reachable ONLY from the ongoing,
wall-clock/ship-arrival-gated cycle - never from initial world/server
creation (`logic.gen.SystemContent.generatePlanet`, the separate function
already noted above for `MaxWreckPerPlanet`, is untouched by this chain).

**The seed fed into placement is freshly minted from real entropy every
single call**, not the deterministic per-system `genProps.seed` used for
initial world-gen (contrast with the "Initial generation is fully
deterministic" paragraph above). Raw opcodes, `PlanetResourceManager.hx:717`
(`generateResGens`):
```
seed = int(random() * 65536 + sys_time())
```
`random()` here resolves to `Std.random` -> HashLink's native `rnd_float`
(`Math.hx:85`, backed by `$Std.rnd`, an `hl_random` object) - real
engine/OS entropy, confirmed distinct from the seeded `hxd.Rand` path.
That seed is passed into `PlanetRes.run` (`PlanetRes.hx:189-191`), which
does `this.rand = new hxd.Rand(seed)` - the same Marsaglia multiply-with-
carry generator already reverse-engineered for asteroid-name RNG in the
sibling `spacecraft-memory-research` repo's `RESEARCH_LOG.md`. This is a
live, advancing PRNG object (`seed`/`seed2` fields both mutate on every
draw), not a static lookup table.

**Position itself is computed continuously off that advancing PRNG, subject
to real validation - not selected from a fixed candidate list.** This
paragraph replaces two earlier, partially-wrong passes at this same
question (one had an inequality backwards, the next mislabeled which
branch/line-range was which) - both caught by the user pushing back and
asking to re-check the code rather than trust the writeup. Final version,
every branch and threshold re-read directly from `PlanetRes.generateGroups`
(findex 11231)'s raw opcodes end to end:

`generateGroups` has THREE placement branches, not two, selected up front
by `get_isAsteroid()` then `this.poi`:
- **Asteroid branch** (`PlanetRes.hx:327-330`, only if `isAsteroid`):
  `lat = acos(draw()/10007)`, `lon = (draw()/10007) * PI` - `draw()` being
  one raw Marsaglia step of `this.rand`. Falls straight through to the
  shared validation below with **no stamp/free-space check at all** -
  asteroids skip that step entirely.
- **General/whole-planet branch** (`PlanetRes.hx:331-334`, non-asteroid,
  `this.poi == null`): the SAME `acos`/`*PI` formula shape as the asteroid
  branch, but wrapped in a real retry loop: `stampVal =
  getStampResAt(planetHeight, lon, lat)`; accept if `stampVal <= 0`,
  otherwise jump back to the top of this branch and redraw a fully fresh
  lat/lon (not a nudge - a brand new pair of `this.rand` draws).
- **POI-anchored branch** (`PlanetRes.hx:336-341`, non-asteroid,
  `this.poi != null`): disc sampling around a POI's center - `r =
  sqrt(draw()/10007) * poi.angle`, `theta = (draw()/10007) * 2*PI`, `lat =
  poi.lat + r*cos(theta)`, `lon = poi.lon + r*sin(theta)` (the `sqrt` on
  the radius draw is what makes AREA, not just radius, uniform). Also a
  retry loop, but with the OPPOSITE accept condition: `stampVal =
  getStampResAt(...)`; accept if `stampVal >= 1`, else redraw. Two
  different branches, two different thresholds - don't conflate them.

**Which branch wrecks actually use - resolved by tracing where `this.poi`
gets set**, not assumed: `PlanetRes.run` (`PlanetRes.hx:226,236,243`) sets
`this.poiIndex` from each resGen entry's own `props.poiIndex`, then `this
.poi = this.poiIndex == null ? null : this.pois[this.poiIndex]`. Checked
live against `data.cdb`'s `resGen` sheet (NOT `resGroup` - different sheet,
easy to conflate, see the field glossary above) for the actual wreck rows
referenced by `sector.generation.wreckResGen`: `ShipWreck_Small_0` ->
`{maxSectorLevel: 3}`, `ShipWreck_Big_0` -> `{minSectorLevel: 2,
maxSectorLevel: 2}`, and so on for all six tiers - none of them set
`poiIndex`. So `this.poi` is always null for wrecks: **wrecks always use
the general/whole-planet branch, never the POI-disc branch** - positions
are drawn from the planet's full surface (`acos`/`*PI` sphere sampling),
not clustered around a landmark.

**Neither retry loop has a counter or cap** - confirmed by reading the full
345-op function; there's no fallback-to-a-fixed-position code path if the
free-space check keeps failing, it just keeps redrawing forever. This
directly rules out one candidate explanation for "wrecks keep showing up
in the same spot" reports: there's no mechanism in this code that reuses
or falls back to a previous/fixed coordinate.

**Beyond the free-space retry, three hard-reject checks follow** (shared
by all three branches, `PlanetRes.hx:345-370`) - these abort the whole
`generateGroups` call (`return false`) rather than retrying:
- *Light-level* (general branch only): if the resGroup's `props.lightMin`/
  `lightMax` are set and the light value at that lat/lon falls outside
  that band, bail.
- *Altitude* (non-asteroids only): `altitude = getZLatLon(lat,lon) -
  planet.getSize()`; if outside `props.altitudeMin`/`altitudeMax`, bail.
- *Underwater*: if `altitude < 0` AND `props.flags` bit 7 is set AND
  `planet.hasWater()`, bail.
Checked against live `data.cdb`: `GShipWreck_{Small,Big}_lvl{0,1,2}`
(`resGroup` sheet this time) only set `{groupDensity, flatTerrain: true,
size}` - no light/altitude bounds, so those two checks are dead code for
wrecks specifically. `flatTerrain: true` is set on both tiers, plausibly
feeding the underwater check's `flags` bit 7, but the exact column-to-bit
mapping wasn't traced further.

**Conclusion**: a wreck's exact spawn position is drawn fresh, every spawn
cycle, from a continuously-advancing PRNG seeded with real per-call entropy
(not the deterministic per-system seed used for initial world-gen),
constrained only by the free-space/overlap retry loop (general branch,
whole-planet, no POI clustering, no retry cap or fallback). It cannot be
predicted or reproduced even with full knowledge of the system's
`genProps.seed`. If players report wrecks reappearing "in the same spot,"
the mechanism here gives no support for that being literal - the more
likely explanations are perceptual (same general area looking similar) or
a still-existing wreck being mistaken for a new one, not this code
reusing a coordinate.

## Finding 6: Loot-level candidate search is a 2-level window, not an exact match

Correction to an assumption made while analyzing Finding 5's `ShipWreck_LootChestRare_lvl{0,1,2}` primary-item rolls (Patch/Blueprint category, per the `loot` sheet's `primaryItemTypes`/`secondaryItemTypes` flag columns — `10:Tool,Module,Patch,Blueprint,ShipDecorative` / `10:Gathering,Material,Manufactured,LuxuryArticle,Scrap`, a bitmask over that column's own local enum, **not** an index into the `itemType` sheet). Verified via raw opcodes, `src/logic/Loot.hx:461-478`:

```
generatePrimaryItemCandidateBasic(level, maxLevel, itemPool, rng, typeKind, itlKind):
    target = min(level, maxLevel)
    startLevelMin = target - 1
    startLevelMax = target
    return generateAttemptDownUp(startLevelMin, startLevelMax, getMaxLootLevel(...), candidateFn)

generateAttemptDownUp(startLevelMin, startLevelMax, levelMax, genFunc):
    res = genFunc(startLevelMin, startLevelMax)   # tries BOTH levels pooled together, one draw
    if res != null: return res
    for level from startLevelMin-1 down to 1: try genFunc(level, level), return on first hit
    for level from startLevelMax+1 up to levelMax: try genFunc(level, level), return on first hit
    return null
```

So a crate targeting (capped) level `L` pools together **every** eligible Patch/Blueprint candidate at `lootLevel ∈ {L-1, L}` and draws one uniformly-weighted pick from that combined set — it does not require an exact match to `L`, and only widens further (level by level, first down then up) if that 2-level window is completely empty (never happens in practice for crate-relevant levels 4-9, since both pools have entries at every level 3-9).

Practical consequence: an item's own `lootLevel` doesn't gate it to only the identically-numbered crate roll — a `lootLevel: 3` recipe (e.g. `BP_IronWire`/`BP_AluminiumWire`, both "Blueprint: Wire") is reachable from any crate whose *capped* target level is 4 (window `[3,4]`), not just from a hypothetical level-3 crate (which doesn't exist — the lowest crate tier only rolls levels 4-7). Confirmed by direct play report. Only `lootLevel: 2` and `lootLevel: 10` items are truly unreachable from any shipwreck crate, since no crate ever has a capped target of 2/3 (window would need to reach down to level 2) or 10/11 (window would need to reach up to level 10) — every level 3-9 item is reachable from *some* sector.

**Important addendum (Finding 15): this window applies to Blueprint candidates too, but `lootLevel` in-window is NOT sufficient for a Blueprint specifically** — it also requires `craft.unlockType == 2` (Random_Blueprint), a separate, unconditional filter in the Blueprint-specific candidate closure. A `unlockType != 2` recipe (e.g. `Unique_Blueprint`) can have a perfectly in-window `lootLevel` and still never be drawable from any crate. See Finding 15 for the full mechanism, the `unlockType` enum legend, and a confirmed real example.

## Finding 7: "Nexus Market" (player-to-player trading) is not accessible offline or via any HTTP API

The in-game player-to-player marketplace UI is labelled `market_title: "::station:: Trade Nexus"`
(`data.cdb` `uiText` sheet) — i.e. "Nexus Market" is a colloquial name for the
`st.Marketplace`/`st.MarketItem`/`st.MarketOrder` system (`src/st/Marketplace.hx`,
findices 9559-9610, see the class dump above).

**Only two stations have it.** Confirmed directly in `data.cdb`'s `station`
sheet (not a guess/heuristic): every station lists a `floors[].instance` array,
and only two entries anywhere in that sheet include a `Marketplace*` floor:

| Station `id` | Display `name` | Marketplace floor instance |
|---|---|---|
| `Station_Terminus` | **Helicon** | `MarketplaceTerminus` |
| `Station_Horizon` | **Ur** | `MarketplaceHorizon` |

(Internal ids `Terminus`/`Horizon` don't match the in-game display names
`Helicon`/`Ur` — don't assume id and display name correspond by string
similarity elsewhere in this sheet either.)

**No external/offline access is possible**, unlike the seeded initial
world-gen content covered in Finding 5. `MarketOrder`/`MarketItem` are
`hxbit`-networked objects (`hxbit/NetworkHost.hx`) — live player-submitted buy/sell
orders only exist as in-memory state on a running `Server` instance
(`src/Server.hx`), streamed to clients that are already inside an
authenticated Steam multiplayer session (`steam/GameServer.hx`:
`logonAnonymous`, `requestInternetServerList`). There is no REST/HTTP
endpoint for it — the only real HTTP domains in the bytecode
(`data.shirogames.com`, `monitoring.shirogames.com`, `mongo.shirogames.com`)
are unrelated telemetry/patch endpoints. Reading live Nexus Market listings
would require writing an actual `hxbit`-speaking game client and joining a
session, not a data-extraction task like the rest of this repo.

## Note: use `tools/heaps_ref/hlbc_src/target/release/hlbc.exe`, not `tools/heaps_ref/hlbc/hlbc.exe`

The game updated at some point after 2026-07-11 and the prebuilt
`tools/heaps_ref/hlbc/hlbc.exe` now fails on the current `hlboot.dat` with
`Error: Malformed bytecode (Invalid type kind '23')` — a new HL type kind
(`Guid`, used by `cdb._Types.$Guid_Impl_`-related code) that binary's parser
predates. `tools/heaps_ref/hlbc_src/crates/hlbc/src/read.rs` and `types.rs`
already have local (uncommitted) patches adding `23 => Ok(Guid)` support, and
`tools/heaps_ref/hlbc_src/target/release/hlbc.exe` is the binary already
built from those patches — that's the one that actually works against the
current game version. Use that path for all `hlbc` invocations going
forward; don't rediscover this by re-hitting the type-kind-23 error.

## Finding 8: Rare loot crate *spawn* odds per wreck (distinct from crate *level* odds, Finding 5)

Finding 5 established which wreck resGen a sector rolls and Finding 6/the
`CHEST_LEVELS`/`CHEST_WEIGHTS` constants in `tools/extract_shipwreck_loot.py`
established, *given* a rare crate (`ShipWreck_LootChestRare_lvl{0,1,2}`)
exists, what level it targets. Neither answers: what's the chance a wreck
actually contains a crate at all, as opposed to only ordinary scrap? Traced
directly via `data.cdb`'s `resGroup` sheet plus a decompile of the actual
placement algorithm (`logic.gen.PlanetRes.generateGroup`/`getResGroupCount`,
`src/logic/gen/PlanetRes.hx`, findices 11222/11224 — the same generic
resource placer Finding 5 already named as `generateShipwreck`'s target).

**The generation-tree semantics, confirmed from decompiled bytecode**:
- A resGroup's `generation.groups` list is an **AND**: every listed entry
  independently contributes a rolled count of placed instances.
  **CORRECTED (Finding 12 resolution, 2026-07-25): the count roll is NOT
  `uniform_int(min, max)`.** `getResGroupCount` (findex 11234 post-2026-07
  update; was 11224), decoded from raw opcodes at `PlanetRes.hx:644-649`:
  the normal branch returns `min + MIN(U1, U2)` where U1/U2 are two
  independent uniform draws over `{0 .. max-min}` — the **minimum of two
  rolls**, biasing every count toward its low end (E[count] for `{0,2}` is
  0.556, not 1.0; for `{1,2}` it's 1.25, not 1.5). If the *enclosing*
  group has `props.explodingChance` set (e.g. `ShipWreck_JunkGroup_lvl0`'s
  `0.1`), that probability instead selects `2*min + MIN(U1, U2)` over
  `{0 .. 3*max-2*min}` — also min-of-two, not the flat
  `uniform_int(2*min, 3*max)` modeled before. Every simulation/expectation
  in Findings 8/9/11 that used a single uniform draw overstates counts.
- A resGroup's `generation.overrides` list is an **OR**: exactly one entry
  is chosen, weighted by its own `weight` — this is the mechanic Finding
  5/`CHEST_LEVELS` already relies on for the crate's own level roll.
- A `{group: X}` reference recurses into `X`'s own full generation tree —
  for a `groups`-list entry this happens once per rolled `count` (i.e.
  counts compound through nesting); for an `overrides` entry, once total
  (whichever branch was picked).

**Where the crate actually sits in the tree**: every
`ShipWreck_{Small,Big}_lvl{0,1,2}` resGroup places a `JunkGroup_lvl{N}`
(`min:1,max:2` for Small, `min:5,max:10` for Big) plus one
`JunkGroup_lvl{N}_BlackBox` (always exactly 1). Both junk groups place a
`RareLoot_lvl{N}` sub-count (`min:0,max:2` inside the regular junk group,
`min:0,max:1` inside the blackbox one) — and `RareLoot_lvl{N}` is an
`overrides` pick between `ShipWreck_BasicLoot_lvl{N}` (weight 40, more
scrap) and `ShipWreck_LootChestRare_lvl{N}` (weight 25, the actual crate).
**That 40:25 weighting is identical across all three tiers** — so, unlike
crate *level*, crate *spawn odds* do not depend on wreck tier at all, only
on whether the wreck is Small or Big (Big wrecks roll the junk-group
generator far more times: 5-10 vs 1-2).

**A single wreck can contain more than one crate** — each `JunkGroup`
invocation independently re-rolls its own `RareLoot` slot(s) (see the tree
above), and a Big wreck invokes `JunkGroup` 5-10 times (vs Small's 1-2), so
"does this wreck have a crate" is really a *count* distribution, not a
single spawn-or-not roll. `count_crates_in_wreck` in
`tools/extract_shipwreck_loot.py` returns the actual number placed per
simulated pass, not just a boolean.

**Simulated (20k-trial Monte Carlo per wreck type, replicating the
algorithm above exactly against live `data.cdb` values)** — **SUPERSEDED
(Finding 12 resolution): these figures assume single-uniform count rolls
and no secondary pass; the corrected debris-field-only figures under the
real min-of-two roll are E[count] ~0.43 (Small) / ~1.8 (Big), and the
correct TOTAL per-wreck numbers live in Finding 12's resolution below**:

| Wreck type | P(≥1 crate) | E[crate count] | Distribution |
|---|---|---|---|
| Small (any tier) | ~58% | ~0.89 | 0:42%, 1:36%, 2:16%, 3:5%, 4+: ~2% |
| Big (any tier) | ~97% | ~3.6 | 0:3%, 1:11%, 2:18%, 3:20%, 4:17%, 5:13%, 6+: ~17% (tail out past 10) |

Per-sector stats (weighted by how many times each `resGen` id repeats in
that sector's own `sector.generation.wreckResGen` list — the same
repetition-as-weight mechanic Finding 5 documents) range from **P(≥1)
~58%, E[count] ~0.89** (`Sec_Threshold`, Small-only) up to **P(≥1) ~73%,
E[count] ~2.0** (`Sec_Idol`/`Sec_Vestige`, the sectors with the highest
Big-wreck share) — see `tools/extract_shipwreck_loot.py`'s `crateSpawn`
output (`atLeastOne`/`expectedCount`/`countDistribution`, per sector) for
the full table. Since tier doesn't move any of these numbers, sector
variation is driven almost entirely by each sector's Small:Big wreck mix,
not by its exploration-level/tier gating.

Not independently verified against a live in-game count (unlike Finding
5's spawn-cycle math, which didn't need it) — treat as a high-confidence
estimate from a verified algorithm, not an exact figure the way
`lootLevelProbability` (crate level, *given* one spawns) is.

**Update: now independently verified live**, via the sibling
`spacecraft-memory-research` repo (which reads a *running* game process's
memory directly, rather than statically simulating `data.cdb` the way this
repo does). Two results from that session, both consistent with the table
above rather than contradicting it:
- A live-memory dump of a real, currently-spawned wreck found exactly
  `ShipWreck_LootChestRare_lvl1: 1` on a Small-tier wreck — one crate,
  unremarkable against Small's own `E[count] ~0.89`.
- The user separately reported real in-game experience at Big wreck sites
  in Vestige/Idol-class sectors: "minimum 3, often up to 10" crates. This
  is NOT a contradiction of `E[count] ~3.6` — it's exactly what the
  `countDistribution` above already predicts once you look past the mean:
  3 is the single most likely exact outcome (20%), P(≥3) ≈ 67%, and the
  6+ bucket (17%) genuinely does tail out past 10. Citing the mean alone
  undersold what a single memorable site looks like; the full distribution
  didn't need correcting, just surfacing.
- Also confirmed live: `PlanetResourceManager.summary` (the exact live
  resource-count map used for ordinary ore/deposits) NEVER tracks wreck
  resources at all, on any planet, generated or not — consistent with
  wrecks being placed once via the initial/ongoing generation algorithm
  documented in Finding 5 but bookkept through a separate path than
  permanently-placed resources. So this static, decompiled-algorithm
  estimate remains the only source for crate counts at all, even
  galaxy-wide with the game running — there's no live "exact" number it's
  an approximation of.

See `tools/extract_shipwreck_loot.py`'s `wreckSiteItemOdds` output
(added alongside this) for why this distinction (mean vs. per-site
distribution) also matters for per-item Patch/Blueprint odds, not just
raw crate counts — Finding continues below.

## Finding 9: Composing crate *count* (Finding 8) with crate *contents* (`itemDropOdds`) into a single per-wreck-site number

`itemDropOdds` (the per-item Patch/Blueprint `pct` values in
`shipwreck_loot.json`) answers "given a rare crate is already open in my
hands, what's the chance it's item X" — a probability *conditional on* a
crate existing at all. It says nothing about how many crates a wreck
actually has, which Finding 8 established varies **~4x** between Small
(`E[count] ~0.89`) and Big (`E[count] ~3.6`) wrecks. Quoting `itemDropOdds`
alone as "my odds of finding this blueprint at a wreck site" silently
assumes exactly one crate-opening per visit, which is wrong for Big
wrecks by roughly that same 4x factor.

**The composition is exact, not an approximation, because of two
properties already established**:
- Crate *count* depends only on wreck **size** (Small/Big) — tier-
  independent (Finding 8).
- Crate *contents* depends only on wreck **tier** (0/1/2) via
  `CHEST_LEVELS`/`CHEST_WEIGHTS` — size-independent (Finding 5/6).
- A wreck resGen id (e.g. `"ShipWreck_Big_1"`) fixes size and tier
  *simultaneously* — a sector's `wreckResGen` list is a flat list of these
  combined ids, repetition-weighted the same way Finding 8 already
  weights tiers alone.

So for one specific (size, tier) variant, `P(item X | one crate opened)`
(from `itemDropOdds`, but recomputed per-tier rather than sector-blended)
and `E[crate count]`/`countDistribution` (from Finding 8, per-size) are
independent quantities that compose by simple expectation algebra:

```
expectedPerWreck = E[crate count | size]  *  P(item X | tier)
atLeastOnePct    = 1 - sum_k  P(count=k | size) * (1 - P(item X | tier))**k
```

`expectedPerWreck` is exact via linearity of expectation regardless of the
AND/OR structure underneath (no simulation needed for this part — it's
already known from Finding 8's Monte Carlo `expectedCount` and the
existing `itemDropOdds` math, just not previously multiplied together).
`atLeastOnePct` reuses Finding 8's actual simulated `countDistribution`
rather than a Poisson approximation of `expectedPerWreck`, so it stays
exact even for items with a non-negligible per-crate probability.

## Finding 10: Agility's *only* gameplay consumer is weapon-hit resolution; asteroid DOT damage has no dodge roll at all

Confirmed via `refto fn@8890` (every caller of `ent.Unit.getAgility`,
findex 8890, in the whole binary) — exactly 3 hits:

| Caller | Role |
|---|---|
| `ui.win.ship.ShipElement.redraw@20590` | UI: renders the stat on the ship panel |
| `<none>@26902` | UI: `CurrentAgility_Display` string-keyed stat resolver (same "Display only" attribute noted in Finding 3) |
| `st.Combat.applyImpact@11025` | **The only gameplay use** — weapon-hit crit/hit/miss resolution |

So agility affects exactly one piece of gameplay math, and it's weapon
combat, not asteroids. `serverUpdateAsteroidFieldDamage` (findex 9132,
Finding 1) is not in this caller list, and its own damage path
(Poisson tick-hit gate → fixed `AsteroidHitChance` roll → multiplier →
`takeDamage`) has no dodge/miss roll anywhere in it — nor does
`Unit.takeDamage__impl` (findex 8896, Finding 2), which also isn't a
`getAgility` caller. Asteroid damage is only ever reduced by shields
and `HullImpactDamageReduction`; there is no way to dodge it by flying
better.

`st.Combat.applyImpact` (`src/st/Combat.hx:123-137`) — raw opcodes,
decompiler mangles this one (garbles the `evalue-1` reuse and drops a
statement, so don't trust `decomp`'s output here):

```
if target.hull <= 0: return

evalue = pow(e, HitChanceExpScale * (source.getAccuracy(skillId) - target.getAgility(null)))

critChance = (evalue - 1) / ((evalue - 1) + 1/BaseCriticalProba)
missChance = (1/evalue) / ((1/evalue - 1) + 1/BaseMissProba)

r = random()
if r < critChance:
    damage *= CriticalHitDamageMultiplier
elif r >= 1 - missChance:
    damage = 0          # dodged
# else: normal hit, damage unchanged

takeDamage(target, damage, DamageSource.Unit(source))
```

So the target's agility raises `missChance` (harder to hit → more
likely dodged) and the attacker's accuracy lowers it, via the shared
`evalue` exponential term — a logistic-style hit-chance curve, not a
flat percentage. `source`/`target`/`skillId`/`weaponIdx` are all
per-shot params, confirming this only fires on weapon-fire impact
resolution, never on the asteroid tick-damage path.

A sector's own number is then the weighted average across whichever
(size, tier) variants its own `wreckResGen` list actually reaches (same
repetition-as-weight mechanic throughout this whole investigation) — not
every sector reaches every tier (e.g. Vestige's list has no tier-0 entry
for either size at all, so tier-0-only items are correctly absent from
its `wreckSiteItemOdds` groups, matching `itemDropOdds`'s existing
reachability for that sector exactly).

**Implemented in `tools/extract_shipwreck_loot.py`** as
`compute_wreck_site_item_odds`, sharing its crate-count Monte Carlo cache
with `compute_crate_spawn_stats` (`crate_count_stats_raw`, factored out of
what was previously a private closure) so the ~9 distinct wreck resGen ids
across all sectors are only simulated once total, not once per consumer.
Output written to `shipwreck_loot.json`'s new `wreckSiteItemOdds.{patches,
blueprints}` key, parallel in shape to the existing `itemDropOdds` (same
`{name, level, groups: [{..., sectors}]}` row shape, grouped sectors with
identical composed odds) but with `expectedPerWreck`/`atLeastOnePct`
instead of a single conditional `pct`.

**Verified no regression in the untouched existing outputs**: `itemDropOdds`
is byte-identical before/after (its code path wasn't touched at all); every
sector's `crateSpawn.expectedCount` matches its pre-change value to within
Monte Carlo noise (<0.02 difference, unseeded 20000-trial randomness, same
as any two independent runs of the *unmodified* code would show). Spot-
checked the composition itself: a Vestige-reachable item's `pct` of 0.83%
(crate-conditional) composes to `expectedPerWreck` 0.0162 (1.62%,
`atLeastOnePct` 1.60%) — a ~1.95x boost, consistent with Vestige's own
40:60 Big:Small mix (a blend, not pure Big, so well under the ~4x pure-Big
factor) and with `atLeastOnePct` sitting fractionally below
`expectedPerWreck`'s implied percentage as it must (P(≥1) ≤ E[count] always,
by Markov's inequality, with equality only when multiple drops in one
visit are impossible).

## Finding 11: Rare loot crates ALSO spawn from dismantling a wreck's own hull piece - a second, independent loot pass Finding 8/9 never covered

Findings 8/9 modeled a wreck's rare-loot-crate count entirely from its
outer `GShipWreck_{Small,Big}_lvl{0,1,2}` resGroup's own `generation.groups`
tree (`JunkGroup`/`JunkGroup_BlackBox` → `RareLoot` → 40:25 BasicLoot-vs-
LootChestRare override) - the debris field scattered around a wreck at
creation. That model, on its own, undershot CraftMap's live-tracked
observed per-wreck crate counts (`Craftmap/tools/audit_wreck_crate_rates.py`)
by roughly 2-4x, and a direct live-memory read of 4 currently-existing
wrecks (`dump_planet_resources.py`'s `read_planet_static_resources`, not a
simulation) confirmed the excess was real and specifically isolated to
crates - junk/scrap item counts from the same wrecks tracked within
~10-25% of the Finding-8 prediction, ruling out a general "everything is
under-counted" explanation.

**Root cause**: `data.cdb`'s `resource` sheet shows exactly ONE hull piece
per wreck carries a `props.resGroupSpawn` field - a SEPARATE trigger that
spawns an entirely independent loot-generation pass. **Correction (see
Finding 12): this fires UNCONDITIONALLY at world-generation**, the instant
the hull piece is first placed (`generateResource@11223`,
src/logic/gen/PlanetRes.hx:559-564 - checks `props.resGroupSpawn` on
every placed resource and immediately recurses into `generateGroup` if
set, no player-action gate anywhere in that path) - NOT triggered by the
player's actual dismantle action, despite the resGroup's own name. The
player's real "dismantle a hull piece" RPC
(`st.PlanetResourceManager.rpcDismantle__impl@8117` → `_dismantle@23642`)
is a wholly separate, unrelated mechanism: it grants a fixed junk bundle
via the resource's own `props.loot` (confirmed: never includes a crate,
checked across the entire `resource` sheet), updates the parent wreck
Core's `lastMining` timestamp (feeding Finding 5's removal-cycle
probability), and `_remove()`s the piece - it never calls
`generateGroup`/`resGroupSpawn` at all. Live-tested directly (Finding 12):
dismantling a hull piece adds zero new resources of any kind. The two
"dismantle"-named things are unrelated; naming this mechanism
`dismantleBonus`/`totalIfDismantled` in the initial implementation (below)
was a mistake that conflated them - renamed to `secondarySpawn`/`total`
throughout the code as of Finding 12.

- Small wreck: its own single `ShipWreck_Lvl{tier}` hull piece →
  `resGroupSpawn: ShipWreck_DismantledJunkGroup_lvl{tier}_Small`, which
  places `{min:0, max:4}` `ShipWreck_LootChestRare_lvl{tier}` DIRECTLY (no
  RareLoot override layer at all - every roll in this range is a crate)
  plus its own `{min:20,max:60}` BasicLoot junk.
- Big wreck: **only** `BigPiece1_lvl{tier}` - never `BigPiece2_lvl{tier}`,
  `SmallPiece1`, or `SmallPiece2` - carries
  `resGroupSpawn: ShipWreck_DismantledJunkGroup_lvl{tier}` (no `_Small`
  suffix), which invokes `RareLoot_lvl{tier}` (the same 40:25 override
  Finding 8 already modeled) `{min:3, max:15}` times - about 6x the
  debris field's own `{min:0,max:2}` RareLoot invocation count - plus its
  own BasicLoot junk.

Both `tools/extract_shipwreck_loot.py`'s Monte Carlo and this repo's own
independent closed-form `resgroup_expected_crate_count`
(`dump_galaxy_resources.py`) missed this entirely, for the same reason:
neither ever looked past a wreck's own outer resGroup tree into an
individually-placed resource's own `props.resGroupSpawn` - a completely
separate spawn trigger, not a nested `gen.group`/`gen.res` reference
reachable by the same tree walk both were already doing.

**Verified**: adding this second pass's own Monte Carlo
(`crate_count_stats_for_group`/`dismantle_group_id`/
`combine_independent_counts` in `tools/extract_shipwreck_loot.py`) and
summing it with Finding 8's original debris-field figure landed within
1.3% of live-tracked observed Big-wreck crate counts (7.09 predicted vs
7.00 observed) and reduced the Small-wreck gap from a ~2x undershoot to a
~1.5x OVERshoot (2.89 predicted vs 1.86 observed) - consistent with
players reliably mining open a Big wreck's main hull chunk to get inside,
but not always bothering to fully dismantle a Small wreck's one simple
hull piece - **superseded by Finding 12**: since this pass is confirmed
unconditional (not player-behavior-dependent), that "players don't always
mine it" story is wrong: the Small gap is real and still unexplained.
`shipwreck_loot.json`'s `sectors[*].crateSpawn.bySize.{Small,Big}` carries
both figures side by side (the bare debris-field number, unchanged from
Finding 8, plus a `secondarySpawn`/`total` sibling - originally named
`dismantleBonus`/`totalIfDismantled`, renamed per the correction above)
rather than picking one blended assumption about player behavior - see
that field's own docstring in `compute_crate_spawn_stats`.

**Propagated to per-item odds too**: `wreckSiteItemOdds` (Finding 9,
`compute_wreck_site_item_odds`) composes each item's per-tier drop
probability against `total_crate_stats` - debris field + this secondary
spawn, convolved via the same `combine_independent_counts` helper
`compute_crate_spawn_stats` itself uses - rather than the debris-field
count/distribution alone. Every `expectedPerWreck`/`atLeastOnePct` value
in `shipwreck_loot.json` now reflects the same total
`crateSpawn.bySize.*.total` uses at the sector level.

## Finding 12: Small wrecks' crate count still doesn't match any tested model - `resGroupSpawn` timing confirmed, but the Small-specific distribution SHAPE is unexplained

Finding 11's `total` figure (debris field + `resGroupSpawn`'s secondary
pass) landed within 1.3% of live-tracked Big-wreck crate counts, but ran
notably *higher* than observed for Small wrecks (2.89 predicted vs 1.86
observed) - originally chalked up to "players don't always dismantle a
Small wreck's one hull piece." That explanation is now confirmed WRONG
(see below), and the real cause remains open. Investigated 2026-07-20
against CraftMap's live-tracked `wreck_events` (`tools/
audit_wreck_crate_rates.py`) plus direct live-memory captures of
currently-existing, verifiably untouched wrecks
(`dump_planet_resources.py`'s `read_planet_static_resources` via a
`wreck_tracker.py`-cached-address fast attach, not a simulation).

**`resGroupSpawn` timing confirmed unconditional, live, not player-
triggered** (closing the loop Finding 11 left open): live before/after
snapshots of an untouched Small wreck (9 crates) through looting the
debris field, then separately dismantling its one hull piece, and of an
untouched Big wreck (15 crates) dismantling first `BigPiece2_lvl2` (no
`resGroupSpawn`, confirmed zero change) then `BigPiece1_lvl2` (the piece
that DOES carry `resGroupSpawn`) - all four steps showed items only ever
being REMOVED (consumed), never added. Collection and dismantling are
both confirmed inert with respect to crate generation: the full crate
count is baked in at world-gen, before the player ever arrives, exactly
as `generateResource`'s bytecode already implied. This rules out any
"depends on what the player does" explanation for the Small gap.

**The Small gap is a shape mismatch, not just a mean mismatch.** Comparing
the full predicted count distribution (not just its mean) against 75
historically-observed Small sites (grew from 72 as passive tracking kept
running between sessions):

| crates | predicted (of 75) | observed |
|---|---|---|
| 0 | 6.2 | 0 |
| 1 | 11.7 | 31 |
| 2 | 14.1 | 26 |
| 3 | 14.7 | 14 |
| 4 | 15.0 | 2 |
| 5 | 8.8 | 1 |
| 6-8 | ~4.5 combined | 0 |
| 9 | 0.06 | 1 |

76% of observed sites show exactly 1 or 2 crates; the model predicts a
much broader, gently-peaked distribution centered on 3-4. More precisely,
the mismatch is a sharp CLIFF between 3 and 4, not a gradual taper: the
model treats 3 and 4 as almost equally likely (19.6% vs 19.9%), but
observed sites are ~7x rarer at 4 than at 3 (2 vs 14) - this looks more
like something capping/truncating the distribution from above than like
a smooth concentration at low counts. Big wrecks, by contrast, show a
full-histogram shape that tracks the model reasonably well end to end
(checked the same way, n=44) - the mean match wasn't a coincidence for
Big, but it may have been one for Small (its mean happens to sit inside a
poorly-shaped predicted distribution).

**Ruled out, with evidence, as explanations for the Small-specific
mismatch:**
- **Sector level / wreck tier**: broken the 72 sites down by tier (0/1/2)
  - all three show the same "concentrated at 1-2, never 0" shape and
    near-identical means (1.67/1.77/1.94). If sector level (gating which
    tier a wreck rolls) drove this, tier 0 (reachable in the lowest-level
    sectors) should look different from tier 2 (highest-level only). It
    doesn't. Also independently confirmed structurally: `RareLoot_lvl0/1/2`
    share the identical 40:25 weight and `DismantledJunkGroup_lvl0/1/2_
    Small` share the identical `{min:0,max:4}` - tier moves loot LEVEL,
    never crate COUNT (consistent with Finding 8).
- **`linkedResource`/`detectMission`/`gainAttribute` fields**: checked
  every wreck-related resource - `linkedResource` is `None` everywhere;
  `ShipWreck_Core`'s `detectMission: ShipWreck` is a quest/scanner trigger
  unrelated to loot.
- **A hidden minimum in `DismantledJunkGroup_lvl{N}_Small`**: re-verified
  directly from `data.cdb` field-by-field - genuinely `min:0`, not
  misread. Big's parallel piece (`DismantledJunkGroup_lvl{N}`, no `_Small`
  suffix) has `min:3` on its RareLoot invocation count (a partial floor -
  P(0 crates from that alone) ≈ 23%, not zero) - Small has no analogous
  floor on its direct placement at all, yet is the one that never shows 0.
- **`props.flags` bit differences**: `ShipWreck_LootChestRare_lvl{0,1,2}`
  shares the identical `flags: 360` with ordinary junk items like
  `SteelHullScraps` - not a crate-specific differentiator.
- **Stale Finding 8 baseline**: re-derived the debris-field distribution
  fresh this session rather than trusting the old cached number - P(0)
  came out to 41.9%, matching the original figure closely. Not a
  reproduction bug.
- **Historical data contaminated by wrecks already-partially-looted
  before tracking started** (would explain observed running LOWER than
  live-fresh samples): checked time-since-session-start for every
  historical Small sighting - crate counts stay flat across the entire
  ~27-hour tracked window (mean fluctuates narrowly in the 1-2 range from
  minute 0 through minute 1600+), no upward drift as tracking continues
  that would indicate early sightings were of already-depleted wrecks.
  Rejected.
- **Terrain-placement / spacing retry failures silently dropping some
  rolled crates before they place** (`generateResource`'s up-to-50-attempt
  placement retry, undocumented before this investigation - see below):
  investigated at length and ultimately rejected for a specific, provable
  reason, not just low confidence. The retry loop's behavior differs by
  what the entry being retried resolves to:
  - A **direct `res` target** (Small's own case: `{min:0,max:4,
    res:LootChestRare}`) has no probabilistic decision inside it at all -
    a failed attempt can only reposition or silently drop an
    already-decided crate. It can never turn a would-be-crate into
    nothing-then-crate-again, and can't explain a HIGHER-than-modeled "at
    least 1" rate.
  - A **`group` target** (Big's case: `{min:3,max:15,group:RareLoot}`)
    genuinely does re-roll on retry - each attempt is a fresh
    `generateGroup(RareLoot_lvl{N}, new_point)` call, and `generateGroup`'s
    own "overrides" branch draws a brand new `random()` every invocation
    (confirmed directly from `generateResource@11223`'s bytecode,
    src/logic/gen/PlanetRes.hx:559-564, and `generateGroup@11222`'s own
    retry-loop structure, findex 11222 ops ~245-338). But this mechanism
    can only matter if first-attempt placement failure is common enough
    for retries to actually fire - and the retry loop's own success
    condition is "placed SOMETHING" (crate OR junk), not "placed a crate
    specifically", so a first attempt that rolls junk and finds a valid
    spot immediately exits successfully with junk, never reaching a
    crate-reroll at all. This mechanism, even where it's real, only
    applies to BIG's already-well-fitting mechanism, and says nothing
    about SMALL's direct-placement mechanism where the actual anomaly
    lives.
- **Whole-group terrain-slope gate silently zeroing an entire roll**
  (decompiled `generateGroup@11222` in full, findex 11222 ops 0-122,
  `src/logic/gen/PlanetRes.hx:378-413`, since this was the concrete
  next-step pointer left by the previous session): confirmed
  `getNearCartesian@11238` + `isValidTerrainSlope@11225` gate the ENTIRE
  group at the top of `generateGroup`, before the loop that rolls any
  member's own count even starts (`getResGroupCount@11224` is called
  inside that loop, ops 153-170, strictly after the gate) - so a single
  failed terrain check zeroes everything the group would have rolled, not
  just one item. This is real and asymmetric-by-construction (Small gets
  only 1-2 independent group rolls total - its 1-2 JunkGroup debris rolls
  plus 1 `resGroupSpawn` recursion from its single hull piece - so one
  failure has an outsized chance of showing up in Small's total, where
  Big's 5-10 independent rolls across 4 hull pieces average it away). But
  it predicts the WRONG direction: it can only ever ADD zero-count sites,
  and the model already predicts P(0)=8.3% (6.2 of 75 expected) while
  observed shows ZERO true-zero Small sites - fewer zeros than the
  baseline model, not more. Also separately re-verified
  `DismantledJunkGroup_lvl{0,1,2}_Small`'s raw `data.cdb` definition
  directly (`{min:0,max:4,res:LootChestRare_lvl{N}}` alongside a sibling
  `{min:20,max:60,group:BasicLoot_lvl{N}}` entry, both under the same
  `props:{groupDensity:1,size:7}`) - matches what was already modeled, no
  transcription error hiding here. Rejected as the primary driver, though
  it may still be a real, minor, wrong-direction contributor.

**RESOLVED (2026-07-25)** - the Small distribution shape is fully
explained by two count-roll behaviors decoded from raw opcodes (findices
are post-2026-07-update; the whole `logic.gen.PlanetRes` block shifted
+10: generateGroup 11222->11232, generateResource 11223->11233,
getResGroupCount 11224->11234), plus the placement geometry, all
cross-verified against 189 tracked Small sites from the sibling repo's
`wreck_events.jsonl` (up from the 75 this Finding originally used):

1. **Every count roll is the MINIMUM of two uniform draws, not one**
   (`getResGroupCount@11234`, see the corrected semantics bullet in
   Finding 8). This alone collapses the model's fat 3-5 shoulder: the
   Small secondary's `{0,4}` direct placement rolls
   {0:36%, 1:28%, 2:20%, 3:12%, 4:4%}, not 20% each, and the debris
   field's expected crates drop from ~0.89 to ~0.43 (observed across the
   189 sites, counting crates ≥3u from the hull: 0.46).
2. **A direct-`res` entry that rolls 0 is bumped to 1 if that resource id
   hasn't been generated yet in the current `PlanetRes` run**
   (`generateGroup@11232` ops 176-199, `PlanetRes.hx:430`: `count==0 &&
   entry.max>0 && (generatedResources[res.id] null or 0) -> count=1`).
   `generatedResources` is per-run and starts empty - `loadExisting` only
   feeds the spatial partition, verified from its closure's opcodes - and
   every cycle-spawned wreck is its own run (`generateShipwreck` ->
   `generateResGens` -> `new PlanetRes`), so **every cycle-spawned Small
   wreck's hull `resGroupSpawn` pass places at least 1 crate,
   unconditionally**. That's the "never zero" observation (189/189 sites
   with >=1 crate). Group entries don't get the bump, which is why Big
   sites CAN roll zero (2 of 103 observed - Big's secondary routes
   through the `RareLoot` group). Note the bump implies initial
   world-gen wrecks (all placed in ONE run at world creation) only
   guarantee a crate for the FIRST Small wreck per planet-run - but every
   wreck standing today came from the ongoing per-0.5-day cycle, so in
   practice the guarantee is universal.
3. **Placement geometry pins the guaranteed crate to the hull**: the
   `resGroupSpawn` recursion anchors at the hull piece's exact lat/lon
   (`generateResource@11233`, `PlanetRes.hx:559-564`), and
   `generateGroup`'s first-instance scatter radius works out to
   ~2 x resSize x groupDensity ~= 1.5u for the 0.75-size crate - observed:
   the nearest crate sits 0.125-1.56u from the hull in 189/189 sites.
   Later instances drift outward as the anchor re-centers on the
   size-weighted centroid of what's been placed (`PlanetRes.hx:486-497`),
   giving the observed 2-4u second/third crates.

**Corrected Small model vs the 189 tracked sites** (Monte Carlo faithful
to the decoded rolls; hull part = min-of-two{0..4} with 0->1 bump, debris
= min-of-two rolls through `JunkGroup`/`RareLoot`/blackbox):

| crates | predicted (of 189) | observed |
|---|---|---|
| 0 | 0.0 | 0 |
| 1 | 80.1 | 85 |
| 2 | 57.2 | 55 |
| 3 | 32.1 | 36 |
| 4 | 14.6 | 8 |
| 5 | 4.0 | 4 |
| 6+ | ~0.9 | 0 (+1 nine-crate outlier, a merged double site) |

Predicted mean 1.99 vs observed 1.93. The hull-crate sub-distribution
alone (crates <3u of hull) matches {64%, 20%, 12%, 4%} predicted vs
{67%, 21%, 11%, 2%} observed. The old model's unexplained "cliff between
3 and 4" is just the min-of-two roll (3s and 4s are NOT near-equally
likely: 12% vs 4% before debris smearing); the k=4 row's remaining modest
overprediction (14.6 vs 8) is the one residual, plausibly placement-
failure losses of a 3rd/4th crate in the crowded hull disc - minor, and
in the opposite-and-smaller direction of the original 2.89-vs-1.86
anomaly.

**Big wrecks under the corrected model - RESOLVED TOO (2026-07-25,
follow-up session)**: min-of-two deflates Big's static total to ~4.5
expected, but observed is 6.47 (n=103, isolation-checked: identical
numbers clustering at 150/250/500u) - so Finding 11's celebrated "7.09
predicted vs 7.00 observed" agreement was substantially coincidental (a
single-uniform overshoot compensating a genuinely missing mechanism).
The missing ~2.5 crates: **`BigPiece2` fires the same
`DismantledJunkGroup_lvl{N}` secondary pass as `BigPiece1` on the live
servers, even though the SHIPPED client `data.cdb` carries
`props.resGroupSpawn` only on `BigPiece1`.** Evidence chain, in order of
discovery (raised by the user's own in-game impression that P2 behaves
like P1):

- Tracked sightings show a crate within 4u of BigPiece2 at the same rate
  and with the SAME count distribution as BigPiece1 (P1
  {0:20%, 1:22%, 2:34%, 3:17%, 4:6%} vs P2 {0:17%, 1:28%, 2:34%, 3:14%,
  4:7%}, n=103 each) - one shared mechanism, not coincidence.
- Under the decoded collision rule (sum-of-radii: a placement fails
  within `node.size + mySize` of any partitioned node - confirmed
  empirically by the observed P1<->P2 spacing floor: min 45.1u over 103
  wrecks vs the 25+20 radius sum) it is geometrically IMPOSSIBLE to
  place a crate 1-3u from an already-placed P2 or to place P2 next to an
  existing crate. The only window that allows a glued crate is the
  piece's own `resGroupSpawn` recursion (a resource enters the collision
  partition only AFTER that recursion - `generateResource@11233` op
  order: resources.push -> resGroupSpawn -> partition.add).
- Crate top-up over time is ruled out (10 stray later-appearing crates
  near 206 tracked pieces across days - leave/return artifacts; and
  `performShipWreckCycle@8108`/`flush@8112` contain no generation call
  besides `generateShipwreck@8110` for NEW wrecks).
- The client `data.cdb` is NOT stale: re-extracted from the current
  `res.pak` (post-2026-07-22 patch) and md5-identical to the copy read.
  So this is a real client-data-vs-server-behavior divergence - the
  authoritative server (which runs all generation; the client only
  receives results) evidently has `resGroupSpawn` on both BigPiece1 AND
  BigPiece2 (or equivalent server-side data/code). First documented case
  in this project of the shipped `data.cdb` not matching live-server
  generation behavior; worth remembering for any future static-data
  conclusion.
- **Follow-up (same day): the divergence mechanism itself was then found
  and live-verified - the client does not even RUN on `res.pak`'s
  `data.cdb`.** Two independent proofs: (1) `hlboot.dat`'s string table
  contains `".networkData/data.cdb"` and `"cdbTmpFilename"` - the client
  DOWNLOADS a server-distributed `data.cdb` at connect and loads that
  (the cache file itself wasn't found on disk - possibly transient/
  deleted after load - but the code path is unambiguous). (2) Reading
  the RUNNING online client's parsed CDB rows directly out of process
  memory (via the sibling repo's `hl_process.py`: `$Data` global ->
  `resource.all` ArrayDyn -> per-row vdynobj field lookups, using
  hl_hash_gen's exact semantics incl. C signed-int32 overflow before the
  single final modulo - short names hash fine either way, longer ones
  like "props" NEED the overflow emulation): every wreck piece's
  `props` is EMPTY in live memory - `resGroupSpawn` is null/absent even
  on `BigPiece1` and the Small hulls, where `res.pak`'s copy plainly
  sets it. (Reads a few minutes apart show those fields flickering
  between absent and present-but-null - that's HL's dynobj-to-virtual
  cast machinery lazily materializing MISSING virtual fields as null
  lookup entries as the game touches props; the values are never
  non-null.) So the server distributes a SANITIZED data file to clients
  - generation/loot-relevant props stripped, presumably anti-datamining
  - and there are really THREE data.cdb variants: the server's
  authoritative full copy (never client-visible; the only one where
  BigPiece2's `resGroupSpawn` actually lives), the sanitized
  server-distributed copy the live client runs on, and `res.pak`'s
  shipped full-but-stale snapshot (the only full copy we can read -
  correct for BigPiece1/Small hull, missing the BigPiece2 change).
  Consequence: for server-side generation questions, the shipped
  `data.cdb` is a *lower bound* on what the server's data contains, and
  live-observed behavior (tracked events) outranks it - exactly how the
  BigPiece2 secondary was established.
- **Open, cheap discriminating test (solo mode)**: the game has a solo
  mode where the client must run generation locally - with whichever
  data copy it uses there. Prediction: if solo uses `res.pak` data, solo
  Big wrecks show glued crates at BigPiece1 but NOT BigPiece2 (directly
  demonstrating the pak-vs-server divergence in-game); if solo uses the
  sanitized network copy, solo wrecks spawn with no secondary crates at
  all (and Small hulls lose their guaranteed crate). Either outcome is
  informative; not yet tested.
- Verified by simulation: `tools/simulate_wreck_placement.py` (added
  with this finding) implements the decoded placement algorithm
  faithfully - min-of-two counts, the 0->1 bump, sum-of-radii
  collisions, the partition-blind window, per-attempt override
  re-rolls, 50-attempt instance retries, size-weighted recentering, and
  `run@11230`'s reroll-the-whole-wreck-on-failure conditioning
  (PlanetRes.hx:261-298: a failed attempt is fully rolled back via
  `cancelResources` and regenerated elsewhere - which is also why live
  wrecks ALWAYS have their black box and full junk complement). With P2's
  secondary OFF (client data as-is): Big total 4.12, P2 glued 0% -
  contradicted. With it ON: **Big total 6.61 vs 6.47 observed**, P1/P2
  glued distributions match bin-for-bin, black box 100% at ~61u (observed
  100% at ~53u). Small-wreck regression under the same simulator: total
  1.84 vs 1.93 observed, glued {1:67%, 2:19%, 3:10%, 4:3%} vs
  {63/21/15/2} - intact.
- The re-roll-on-retry crate-share inflation suspected in the previous
  paragraph IS real as a mechanism (the Overrides arm re-picks 40:25 on
  every parent retry - confirmed from `generateGroup@11232` ops 427-524)
  but is inherently included in the simulation and turns out NOT to be a
  significant contributor; the P2 secondary accounts for the gap.

Corrected per-wreck crate expectations, both now empirically validated:
**Small ~1.9** (guaranteed hull crate + min-of-two extras + debris),
**Big ~6.6** (two per-piece secondary passes + debris). A black box is
guaranteed 1 per wreck (the same 0->1 bump on its `{0,1}` direct-res
entry - user-confirmed as known in-game behavior, and 133/133 tracked
sites once black-box tracking was enabled; earlier lower rates in the
event log were a tracking-coverage artifact, not depletion).

## Finding 13: Farming — "Rockwood Nut" (Xenic Farm) variant outcomes, exact gate conditions, and enrichment bonuses

Source: `data.cdb` sheet `farm` (rows `RockwoodSeed` and its 5 grown-variant
rows + their `_Gather`/`_Dead` children), cross-checked against
`ent.b.PlotZone.pickVariant`/`hasMinRequirement` (`hlboot.dat`,
`src/ent/b/Farm.hx:440-485`, findex 22005/22006). Internal row IDs are noted
in parens the first time each is named; everything else below uses the
in-game display name. Building: **Xenic Farm** (`B_Farm`).

**Mechanism.** Planting a Rockwood Nut starts germination (4.5-5h, needs
only Water), then rolls into exactly one of 5 grown variants. The Xenic
Farm has **one shared Temperature dial** (set to exactly one of Cold /
Temperate / Warm / Hot) and **one shared Light dial** (set to exactly one
of UV / Natural / Dark) — a single farm is never in two temperature or
light states at once. Each variant's gate is a bitmask *set of acceptable
dial positions*; the check (`hasMinRequirement`) tests whether the farm's
one current dial position falls inside that variant's allowed set — it is
OR-across-allowed-states, not several states holding simultaneously.
**All variants whose gates currently pass become candidates, and the game
picks uniformly at random among them** (not priority order); if none pass,
the plant dies instead of stalling.

**`requires.supplements` is AND, `requires.noSupplements` is OR — verified
against raw disassembly, not just decompiler pseudocode** (a plot/slot can
hold up to 3 supplements at once, per the user, so a multi-item AND
requirement is a real, reachable state, not a design impossibility).
`hasMinRequirement`'s `supplements` loop (`Farm.hx:437-444`, ops 84-126)
only advances to the next list entry when the current one *is* present;
the instant one is missing it does `missingCount++` and breaks out of the
whole block — so the block only avoids a miss if **every** listed item is
present (short-circuit AND). `noSupplements` (`Farm.hx:446-452`, ops
127-168) is the mirror case: it breaks the instant it finds **any**
forbidden item present (short-circuit OR-to-fail), which is the correct
reading for a deny-list. An earlier pass through this data mistakenly
described `supplements` as an OR/"any one satisfies" match — that was
wrong and is corrected here; see Rockwood Bitter below for the one variant
where this actually changes the answer (its 2-item `supplements` list).

**Grown variants and their exact gates:**

| Variant | Fruit | Byproduct | Fertilizer required | Fertilizer forbidden | Temperature dial (any ONE of) | Light dial (any ONE of) | Neighbor restriction |
|---|---|---|---|---|---|---|---|
| Rockwood Green (`Rockwood`) | Rockwood Nut | Lime | Neutral Fertilizer | Metallic Fertilizer | any | any | no Reclusive-tagged neighbor plant |
| Rockwood White (`Whitewood`) | Rockwood Nut | Kaolinite | Metallic Fertilizer | — | any | any | no Reclusive-tagged neighbor plant |
| Rockwood Dream (`Dreamwood`) | Dreamwood Fruit | Elmerium Nugget | Elmerium Dust | — | Cold, OR Temperate, OR Warm (raw bitmask `7`; excludes Hot) | Dark only (raw `4`) | — |
| Rockwood Glow (`Glowwood`) | Glowwood Fruit | Rockwood Bark | — (corrected in Finding 18: an earlier reading of this cell as "no fertilizer of ANY kind may be present" was wrong — no such gate type exists in the game; Glow's data simply has neither a supplements nor a noSupplements list) | — | **impossible under normal play — see correction below**, not "any" | **impossible under normal play — see correction below**, not "any" | no Reclusive-tagged neighbor plant |
| Rockwood Bitter (`Sulfwood`) | Rockwood Nut | Pyrite | Acidic Fertilizer AND Metallic Fertilizer (both required simultaneously — see AND/OR note above) | Carbonic Fertilizer | Warm, OR Hot (raw bitmask `12`) | any | — |

**Correction, confirmed both by re-tracing raw disassembly and by a real
in-game test (2026-07-22: a Rockwood Nut planted with no supplement at
all came up all-dead stems, matching this reading, contradicting the
"Glow is the no-requirement catch-all" claim previously made here).**
Glowwood's `requires.temperature` and `requires.light` are **present with
literal integer `0`** (confirmed directly against `data.cdb`'s raw JSON —
`"light": 0, "temperature": 0`, not `null`/absent). `hasMinRequirement`'s
temperature/light checks (`Farm.hx:454-459`, raw disassembly) only skip
the check when the field is **`null`** (`JNull ... jump to 184/205`); a
present field, even `0`, runs the full bitwise-AND-against-the-farm's-
current-dial-bit test. Since `0 & (1 << anything)` is always `0`, this
check can **never** pass for Glowwood — not "any dial position is fine,"
but "no dial position is ever fine." This is the opposite of Spacekorn's
encoding for the same intent (Finding 14 below): Spacekorn variants leave
the key **absent** (`null`) to mean unconstrained, which correctly skips
the check; Glowwood's literal `0` instead guarantees a `missingCount`
hit on *both* temperature and light, every single tick, with no
farm-dial configuration able to satisfy it.

Since default `allowMissing` is `0`, this makes Glowwood's own gate fail
unconditionally in ordinary play — meaning a bare Rockwood Nut planting
with no supplement applied has **all five variants failing
simultaneously** (Green/White/Dream/Bitter for their unmet fertilizer
requirement, Glow for this always-fails bug), which is exactly why the
seed died outright instead of becoming Glow.

**No loophole exists via `allowMissing` either — checked and ruled out.**
An earlier version of this note speculated that two adjacent already-grown
Rockwood Dream neighbors could stack `FarmPlantIgnoreMinRequirement` to
`allowMissing = 2` (enough to tolerate both the temperature and light
misses). That's wrong: `updatePlots`' neighbor-adjacency loop checks
`hasUniqueEffect(adj.attr, row, col)` **before** calling `pushUniqueEffect`
and `continue`s (skips the push) if an entry with that same `kind` already
exists at that position this tick (confirmed via raw decompile of both
`hasUniqueEffect`@22010 and `pushUniqueEffect`@22009) — so the first
Dreamwood neighbor's push blocks every subsequent neighbor's push of the
same attr, and at most one `value: 1` entry can ever exist per position
per tick regardless of neighbor count. `allowMissing` therefore caps at
**1**, never `2`, no matter how many Dreamwood neighbors surround the
plot. Since Glowwood's gate needs *two* simultaneous misses tolerated
(temperature and light both always fail) and the cap is one, **Rockwood
Glow cannot be reached through `pickVariant` under any farm/neighbor
configuration this repo has found** — it isn't rare or hard, it's
unreachable through the normal grow path as currently coded. The only
other way this repo has found to place it on a plot at all is
`ent.b.Farm.adminSetPlant__impl` (findex 21909) — a debug/admin-only
function, not something normal play can invoke. Whether this is a
genuine content bug (a `0` that should have been `null`, matching every
other "unconstrained" encoding in this sheet) or deliberately unreachable
placeholder content is not something data/bytecode alone can answer.

Each variant also carries its own bio-tag (relevant to *other* variants'
"no neighbor" checks and to a couple of enrichments below): Rockwood
Green = Reclusive, Rockwood White = Reclusive, Rockwood Dream =
Invasive, Rockwood Glow = Reclusive, Rockwood Bitter = Putrescent.

**Growth-phase durations and production cycle (hours):**

| Variant | Growth duration | Fruit cycle | Byproduct cycle |
|---|---|---|---|
| Rockwood Green | 57.6-64 | 20-28 | 0.14-0.18 |
| Rockwood White | 57.6-64 | 60-80 | 1.3-1.7 |
| Rockwood Dream | 81-89 | 16-24 | 3-4.6 |
| Rockwood Glow | 50-60 | 0.01-0.02 | 0.01-0.02 |
| Rockwood Bitter | 57.6-64 | 28-34 | 1.1-1.35 |

`_Gather`/`_Dead` stages carry no timer (0/0 — static states, not ticking
phases). No yield-quantity, weight, or plot-slot/power field exists on
this sheet; output rate is governed entirely by the production-speed
attributes below, and plot-slot/power requirements (if any) belong to the
Xenic Farm building itself, not to individual plants.

**Enrichment bonuses** (conditions checked against the SAME current single
temperature/light dial position, plus supplement presence / neighbor tag;
`ARatio` = additive %, e.g. `1.0` = +100%; `MRatio` = multiplicative, e.g.
`0.6` = ×0.6 i.e. -40%. **"Fruit"/"Byproduct" entries below are Quantity,
not Speed — see Finding 17**, which corrects this list in place: an
earlier pass wrote these as "Fruit speed"/"Byproduct speed" by
pattern-matching the `FarmPlantFruitProductionSpeed`/
`FarmPlantExtraProductionSpeed` attr ids' literal "...Speed" suffix,
without checking that the `attribute` sheet's own display name for both
is "Germ Quantity"/"Byproduct Quantity" and that the code only ever reads
them into a one-time end-of-growth harvest quantity, never into any
duration. `FarmPlantAllSpeed`/`FarmPlantGrowthSpeed(Mult)` entries
("Growth speed"/"Growth+Production speed") are unaffected — those
generic ARatio/MRatio conventions above still.):

- **Rockwood Green**: Carbonic Fertilizer present → Fruit quantity +50%,
  Byproduct quantity +50%.
- **Rockwood White**: Neutral Fertilizer present → Byproduct quantity +75%.
  Carbonic Fertilizer present → Fruit quantity +50%, Byproduct quantity +50%.
  Neighbor tagged Putrescent → Growth speed / Liquid consumption /
  Supplement consumption all ×0.6 (-40% each) — and because harvest yield
  divides by this metabolic-speed multiplier (Finding 18's formula), the
  ×0.6 is simultaneously a ~×1.67 fruit-AND-byproduct yield boost per
  harvest: a slower-but-richer tradeoff, not a pure malus.
- **Rockwood Dream**: Temperature dial = Cold, OR Temperate (raw `3`) →
  Growth+Production speed +100%. Carbonic Fertilizer present → Fruit
  quantity +350%. Acidic Fertilizer present → Byproduct quantity +120%.
- **Rockwood Glow**: Light dial = Dark (raw `4`) → Fruit quantity +30%.
  Carbonic Fertilizer present → Fruit quantity +20%, Byproduct quantity +20%.
  Neighbor tagged Putrescent → Growth+Production speed +25% (this part is
  `FarmPlantAllSpeed`, genuine speed) AND, separately, Byproduct quantity
  +25% (this part is `FarmPlantExtraProductionSpeed` — a second, distinct
  effect gated by the same condition, not a second speed number).
- **Rockwood Bitter**: Light dial = Dark (raw `4`) → Growth speed /
  Liquid consumption / Supplement consumption all ×0.8 (-20% each) — same
  Finding 18 tradeoff as White's ×0.6 above: the slower metabolic speed
  also means ~×1.25 fruit-and-byproduct yield per harvest.
  Light dial = UV (raw `1`) → Growth+Production speed +30%. Temperature
  dial = Hot (raw `8`) → Byproduct quantity +40% (corrected 2026-07-25,
  originally per live in-game testing and now confirmed against
  `data.cdb`/disassembly in Finding 17 — this whole list's Fruit/Byproduct
  entries were mislabeled "speed" the same way, see that finding).

**Adjacency effects** (what each variant does to *neighboring* plots,
each applies once, doesn't stack):

| Variant | Effect on neighbor |
|---|---|
| Rockwood Green | Neighbor seed germination speed +100%; neighbor seed death speed ×0.5 |
| Rockwood White | Neighbor supplement consumption -50% |
| Rockwood Dream | Neighbor tolerates 1 unmet requirement instead of dying |
| Rockwood Glow | Neighbor treated as if lit by UV, regardless of the farm's actual light dial |
| Rockwood Bitter | Neighbor's decay speed if its own requirements go unmet ×0.05 (dies far slower while starved) |

## Finding 14: Farming — "Spacekorn" variant outcomes, exact gate conditions, and enrichment bonuses

Source: `data.cdb` sheet `farm`, seed row `EinkornSeed` (display name
"Spacekorn"; seed item display name "Spacekorn Seed", item id
`SpaceWheat_Seed`) and its 3 grown-variant rows + `_Gather`/`_Dead`
children. Same sheet/column schema as Finding 13 (Rockwood Nut) — read
that finding first for the schema reference and the temperature/light
single-dial OR-semantics (a farm has exactly one current Temperature
position and one current Light position; a bitmask gate is the *set of
positions that satisfy it*, not several positions active at once).

**Structurally different from Rockwood Nut**: only **3 grown variants**
(not 5), germination is faster (2.7-3h vs 4.5-5h), and one variant
(Woolly Spacekorn) fruits into an intermediate shell item (Wooly Korn —
"contains a few spacekorn seed, a grinder is required to extract them")
rather than directly back into Spacekorn Seed.

**Grown variants and their exact gates:**

| Variant | Fruit | Byproduct | Fertilizer required | Fertilizer forbidden | Temperature dial (any ONE of) | Light dial | Neighbor restriction | bioTag |
|---|---|---|---|---|---|---|---|---|
| Spacekorn Plain (`Plainkorn`) | Spacekorn Seed | Plain Pulp | — | Metallic Fertilizer | Temperate, OR Warm (raw `6`) | any (unconstrained — key absent) | — | Invasive |
| Spacekorn Sour (`SourEinkorn`) | Spacekorn Seed | Sour Pulp | Carbonic Fertilizer | Acidic Fertilizer | Warm, OR Hot (raw `12`) | any | — | Putrescent |
| Woolly Spacekorn (`ChillyEinkorn`) | Wooly Korn (shell item) | Frost Pulp | — | — | Cold only (raw `1`) | any | no Putrescent-tagged neighbor plant | none (only variant across both crops with no bioTag at all) |

This crop's "unconstrained" dials are correctly encoded by the
`temperature`/`light` key being **absent** (`null`) from `requires`, which
`hasMinRequirement` skips entirely — genuinely no gate. This is *not* the
same as Rockwood Glow's encoding (Finding 13, corrected): Glow's
`temperature`/`light` are present with **literal `0`**, which the same
function does *not* skip, and a `0` bitmask can never satisfy the
farm-dial-bit check — so Glow's fields don't mean "unconstrained" at all,
they mean "always fails." Spacekorn has no equivalent bug in any of its
three variants; every "any" in the table above is a genuine absent-key
skip, confirmed the same way.

**Growth-phase durations and production cycle (hours):**

| Variant | Growth duration | Fruit cycle | Byproduct cycle |
|---|---|---|---|
| Spacekorn Plain | 36-40 | 9-12 | 4-6 |
| Spacekorn Sour | 36-40 | 20-27 | 3.2-4.4 |
| Woolly Spacekorn | 36-40 | 20-27 | 4-6 |

Germination (`EinkornSeed`, needs Water only): 2.7-3h. `_Gather`/`_Dead`
stages carry no timer (0/0), same as Rockwood.

**Enrichment bonuses** (all three variants share the same first three —
light/supplement — entries; ARatio = additive %, MRatio = multiplicative):

- **All three variants**: Light dial = Natural (raw `2`) → Growth+Production
  speed +100%. Light dial = UV (raw `1`) → Growth+Production speed +150%,
  but Byproduct quantity ×0.8 (-20%) — **this malus is dead code as of the
  current build, see Finding 17**: it's read from
  `FarmPlantExtraProductionSpeedMult`, which `updatePlots` computes but
  never actually applies, so in practice UV only delivers the +150% speed
  with no byproduct tradeoff. Neutral Fertilizer present → Byproduct
  quantity +100%.
- **Spacekorn Plain** additionally: Temperature dial = Temperate (raw `2`)
  → Growth+Production speed +100%. Neighbor tagged Putrescent → Germ
  quantity +40%, Byproduct quantity +40%.
- **Spacekorn Sour**: no additional entries beyond the shared three.
- **Woolly Spacekorn** additionally: Carbonic Fertilizer present →
  Byproduct quantity +100% (stacks with the shared Neutral-Fertilizer
  entry above if somehow both present).

**Adjacency effects** (what each variant does to neighboring plots):

| Variant | value | attr | target | once | Meaning |
|---|---|---|---|---|---|
| Spacekorn Plain | 0.15 | `FarmPlantAllSpeed` | **Plainkorn only** | false (repeatable) | Buffs neighboring Spacekorn Plain plants specifically (not any neighbor) +15% Growth/Production speed; not consumed on use |
| Spacekorn Sour | 0 | `FarmPlantDissolveDead` | any | true | Boolean toggle, not a speed ratio (`FarmPlantDissolveDead` has no ARatio/MRatio in the `attribute` sheet) — neighbor's dead plants auto-dissolve instead of lingering; `value: 0` is a placeholder, not a percentage |
| Woolly Spacekorn | 0.2 | `FarmPlantExtraProductionSpeed` | any | false (repeatable) | +20% Byproduct quantity for any neighbor |

Two schema features not seen in Finding 13's Rockwood data: an
`adjacency` entry can carry a `target` field restricting the buff to a
specific named variant (Spacekorn Plain's entry), and not every
`adjacency`/enrichment `attr` is a speed ratio — some (`FarmPlantDissolveDead`)
are plain boolean toggles, identifiable by having no ARatio/MRatio `note`
on the `attribute` sheet.

## Finding 15: Blueprint crate-loot eligibility requires `craft.unlockType==2` (Random_Blueprint), not just `craft.lootLevel` — and the Patch-vs-Blueprint category split from Finding 5/6's era was a wrong guess, not an unresolved trace

Two corrections to how `tools/extract_shipwreck_loot.py` (and this file's
own Finding 6) modeled the rare-crate primary-item generator, both found
while investigating whether one specific item — "Blueprint: Module Patch:
System III" (recipe `Patch_SystemIntegration3`, `lootLevel: 9`) — is really
obtainable from shipwreck crates. It is **not**, and the reason why exposes
a real gap in the tooling that had nothing to do with `lootLevel`.

**Root cause of the initial wrong answer**: `generatePrimaryItemCandidate`
(`src/logic/Loot.hx`, findex 22154) dispatches on item-type category via
four `isTypeMatching`/`isTypeToolOrModule` branches. The type-matching
globals for these branches are `global@7276` (Patch), `global@4808`
(**ShipDecorative**, confirmed via `refto string@1396` →
`Loot_Primary_ItemTypeLevel_ShipDecorative` → `constant@1016` → `global@1050`,
which is exactly the itl value that branch reads), and `global@7374`
(**Blueprint** — confirmed the same way, its own inline
`generateAttemptDownUp` closure at findex 24562 does
`itl = Const.Loot_Primary_ItemTypeLevel_Blueprint` in the clear, at
`Loot.hx:444`). An earlier pass through this same disassembly swapped the
last two — assuming category order matched the `10:Tool,Module,Patch,
Blueprint,ShipDecorative` bitmask column order from Finding 5/6 — and
concluded the Blueprint branch couldn't ever produce a candidate at all
(chasing a dead end where the only `item` sheet row of `type=="Blueprint"`
has `lootLevel: null`). **Lesson: never infer which decompiled branch is
which category from column/bitmask ordering — confirm via `refto` on the
category's own named `itl` constant string, since that's the one thing
guaranteed to appear literally in the branch that owns it.**

**The real Blueprint-candidate closure** (findex 24562, `Loot.hx:426-445`,
raw opcodes, not `decomp` — it mangled this one):

```
for craft in Data.craft.all:
    if craft.lootLevel is null: continue
    if craft.lootLevel not in {level-1, level}: continue    # same 2-level window as Finding 6
    if craft.unlockType != 2: continue                       # <-- the missing filter
    candidates.push(craft)
pick one uniformly at random -> new ItemBlueprint(craft.id)
result.itl = Const.Loot_Primary_ItemTypeLevel_Blueprint      # = 7
result.lootLevel = craft.lootLevel
```

So Blueprint candidates are drawn from the **craft/recipe sheet directly**
(not the `item` sheet the Patch/Tool/Module branches use), and **must have
`unlockType == 2`** — confirmed as a real, in-code, unconditional filter,
not a heuristic. `craft.unlockType`'s enum legend comes straight from
`data.cdb`'s own column definition (`craft` sheet, column `unlockType`,
`typeStr: "5:Permit,Unique_Blueprint,Random_Blueprint,Cannot_Unlock,Study,
Dismantle,Custo"`):

| value | name | meaning |
|---|---|---|
| 0 | Permit | always known (308 of 479 recipes) |
| 1 | Unique_Blueprint | unlocked via a fixed, non-random source (quest/vendor/location) — **not** this crate system, even if `lootLevel` is set (16 recipes) |
| 2 | Random_Blueprint | the only value this crate system ever draws from (68 recipes) |
| 3 | Cannot_Unlock | 26 recipes |
| 4 | Study | 22 recipes |
| 5 | Dismantle | 32 recipes |
| 6 | Custo(m) | 7 recipes |

`Patch_SystemIntegration3` is `unlockType: 1`, so despite `lootLevel: 9`
being set, it is **excluded** from the crate's Blueprint candidate pool —
confirmed live against `data.cdb` re-extracted from the currently-installed
game (2026-07-21). Its own dev comment (`"Placé en Random Blueprint when the
craft is right"`) reads as a TODO to eventually flip it to `unlockType: 2` —
as of that build, this had not happened. The same is true for 15 other
`unlockType==1` recipes (`BP_BarrierShield{1,2,3}`, `BP_HeavyShield{2,3}`,
`Patch_BatteryMultiplicator3`, `Patch_HeatAbsorber3`,
`Patch_FuelEfficiency2/3`, `Patch_PowerEfficiency3`, `Patch_MiningTier2PH`
[`BP_Beam1_Platinium`'s own note reads `"Would be Blueprint"`, a step
earlier still], `BP_SteelPlatings`/`BP_KineticShield3` — the latter two
excluded anyway since their level 2 falls outside any crate's reachable
window per Finding 6, but for the wrong reason under the old model, which
never checked `unlockType` at all).

**The Patch-vs-Blueprint category weight (Finding 5/6-era "50/50
approximation") is now fully traced, not just re-approximated.** Raw
opcodes, `generatePrimaryItem` (findex 22152, `Loot.hx:289-320`):

```
for each enabled category (ToolModule/Patch/Blueprint/ShipDecorative):
    candidate = generatePrimaryItemCandidate(category, level, ...)   # null if that category's pool is empty in-window
    if candidate != null:
        weight = max(0, 10 - |level - candidate.itl| - 2*(level - candidate.lootLevel))
        candidates.push({item: candidate.item, weight})
if every pushed weight == 0: treat all weights as 1 (uniform fallback)
pick one candidate, weighted-random by `weight`
```

`itl` is a per-category constant, read from `data.cdb`'s `constant` sheet
(`Loot_Primary_ItemTypeLevel_*` rows) — **not** a per-candidate mystery
value as the old caveat assumed:

| category | itl | how it's read in code |
|---|---|---|
| ToolModule | 3 | direct `Const` field access |
| Patch | 5 | `Const.resolve("Loot_Primary_ItemTypeLevel_Patch")` |
| Blueprint | 7 | `Const.resolve("Loot_Primary_ItemTypeLevel_Blueprint")` |
| ShipDecorative | 3 | direct `Const` field access |

(`Const.resolve(name)`, findex 501, is a generic named-constant lookup —
`src/Const.hx:69-73` — confirmed via raw disasm, not a guess.)

`tools/extract_shipwreck_loot.py` now computes the real pairwise Patch-vs-
Blueprint weighted split (`category_weight`/`opposing_category_win_share`)
using these constants and each candidate's own resolved `lootLevel`,
instead of a flat 50/50.

**Correction (caught via a direct play-experience challenge — reported drops
from these crates are always Patch/Blueprint/materials, never a bare Tool or
Module): this pairwise split is not an approximation of a wider 4-category
draw, it IS the complete model for `ShipWreck_LootChestRare_lvl{0,1,2}`.**
An earlier version of this finding assumed ToolModule (and in principle
ShipDecorative) also compete for the primary-item slot, reasoning from the
bitmask *column legend* order (`10:Tool,Module,Patch,Blueprint,
ShipDecorative`) without checking what integer value the actual crate rows
use — the same category of mistake as this finding's own branch-mislabeling
correction above. Checked directly against `data.cdb`'s `loot` sheet: the
rows these crates reference (`ShipWreck_Loot_4` through `ShipWreck_Loot_9` —
found via `resource.items[].loot` on `ShipWreck_LootChestRare_lvl{0,1,2}`,
matching the already-documented `CHEST_LEVELS`/`CHEST_WEIGHTS` 40/30/20/10
banding) all have `primaryItemTypes: 12`. With bit order `Tool=1, Module=2,
Patch=4, Blueprint=8, ShipDecorative=16`, **12 = 4+8 = Patch|Blueprint
only** — the Tool and Module bits are off. So `generatePrimaryItem`'s
ToolModule/ShipDecorative branches are real code, reachable from other loot
tables in the `loot` sheet (39 rows total, several with different
`primaryItemTypes` values), but never invoked for this specific crate
resource — confirming the 23 `ShipTool`-chain + 20 `ShipModule`-chain items
with a `lootLevel` set never actually compete for a
`ShipWreck_LootChestRare` primary-item slot, and
`Craftmap/game_data_extract/shipwreck_loot.json`'s Patch/Blueprint `pct`
figures already reflect the true, complete draw, not an under-estimate.

**Net effect on `Craftmap/game_data_extract/shipwreck_loot.json`**: blueprint
rows dropped from 84 to 68 (exactly the `unlockType==2` count); 9 of the 16
removed rows had previously been shown as `"obtainable": true` with nonzero
odds (a real false positive players could have been misled by), the other 7
were already `"obtainable": false` for the unrelated level-2/10-unreachable
reason (Finding 6) but for the wrong stated reason under the old model.
Surviving Patch/Blueprint `bestPct` values shifted modestly (Patch odds up
slightly, low-level Blueprint odds down slightly) now that the split uses
real weights instead of an even 50/50.

## Finding 16: Farming — the `requires` gate is checked every tick during growth (not just once), a 5-hour unmet-conditions death timer, and an Invasive-tag spread-on-maturity mechanic

Source: `ent.b.PlotZone.updatePlots` (findex 22002, `Farm.hx:~280-378`,
decompiled cleanly, no mangling) and `ent.b.PlotZone.checkProgress`
(findex 22003, `Farm.hx:378-397`, decompiled + raw disassembly
cross-checked to resolve a decompiler scope-mangle at the top). Extends
Finding 13/14's per-variant `requires` tables — this finding is about
*when* those requirements are enforced, which those findings didn't cover.

**The gate is continuous, not a one-time snapshot.** Every tick,
`updatePlots` iterates every non-`NoUpdate`-flagged plot (i.e. germinating
seeds and in-progress grown variants — `_Gather`/`_Dead` rows are
`NoUpdate`-flagged and are skipped entirely, so fertilizer/temperature/
light stop mattering the instant a plant reaches Gather or Dead) and calls
`hasMinRequirement` against **the current stage's own `requires`** (the
seed's `{liquid: Water}` while germinating, or the already-chosen grown
variant's fertilizer/temperature/light/neighbor gate while growing):
- **Gate satisfied this tick**: growth/fruit/byproduct progress advances
  (using the enrichment-modified speed ratios from Finding 13/14), and
  `progressDeath` (a per-plot decay counter) is forced to *decrease*.
- **Gate NOT satisfied this tick**: growth/fruit/byproduct progress does
  not advance at all (stalls, doesn't reverse), and `progressDeath`
  *increases* instead, at a rate of 1×`dt`/tick by default — modifiable by
  a neighbor's `FarmPlantDeathSpeed` adjacency effect, confirmed (raw
  disassembly, `Farm.hx:332`, op 222-225: `progressDeathMRatio *= adj.value`)
  to be the only adjacency attr this loop routes into the death-rate
  accumulator. Rockwood Bitter is the only variant across Finding 13/14
  that emits this attr (×0.05 on its neighbor). A lower multiplier means
  *slower* accumulation toward the death threshold below, i.e. a *longer*
  grace period, not shorter — the direction only goes one way, there's no
  variant that speeds up a neighbor's death-rate accumulation via this
  mechanism.
  - **Correction**: an earlier draft of this finding also claimed
    Rockwood White's Putrescent-neighbor ×0.6 modifier shortened this same
    grace period. That was wrong on two counts, not just the arithmetic
    direction (which alone would already have been backwards — a <1
    multiplier always lengthens the grace period, same direction as
    Bitter, never shortens it): that ×0.6 figure is White's own
    *enrichment* (a self-effect triggered by a Putrescent neighbor,
    applying to White's own Growth/Liquid-consumption/Supplement-
    consumption multipliers — a different code path, `getEnrichmentValue`,
    entirely separate from this adjacency loop) — not an adjacency effect
    on neighbors' death rate at all. White's actual adjacency effect
    (`FarmPlantSupplementConsumption`, −50%, Finding 13) isn't even one of
    the attr names this loop recognizes (`FarmPlantAllSpeed`/`DeathSpeed`/
    `DissolveDead`/`Extra…`/`Fruit…`/`Growth…`/`SeedDeathSpeed`/
    `SeedGerminateSpeed` — confirmed exhaustive from the raw disassembly's
    string-compare chain, `Farm.hx:316-345`); an unrecognized attr name
    just falls through as a no-op in this specific loop.

**Death threshold, from `checkProgress`'s raw disassembly**: if
`progressDeath` exceeds `FarmPlantDeathTime` (`data.cdb` sheet `constant`,
raw value **5**, comment "In hours") × 3600, the plot's plant is replaced
by its `deadVariant`. This is fully recoverable up to that point — restore
the gate before 5 accumulated unmet-hours and the plant resumes growing
instead of dying (with Bitter's neighbor effect stretching that grace
period to ~100h, per the confirmed mechanism above).

**Variant selection is separate and one-time**, confirmed by
`checkProgress`'s raw disassembly: only once `plot.progress > plot.duration`
(the current stage's own growth timer has fully elapsed) does it call
`pickVariant`/`applyPlant` to move to the next stage — for a germinating
seed, that's the once-only Green/White/Dream/Glow/Bitter (or
Plain/Sour/Woolly for Spacekorn) branch decision Finding 13/14 already
covers; for an already-grown variant, that's the transition into its own
`_Gather` row. It is never re-rolled mid-growth — only the *already-chosen*
variant's own gate keeps mattering for the rest of that growth phase.

**Bonus finding — Invasive bioTag spread-on-maturity** (not previously
documented; relevant since Rockwood Dream and Spacekorn Plain are both
`Invasive`-tagged per Finding 13/14): immediately after an
`Invasive`-tagged plant completes its stage transition (same
`progress > duration` moment above), `checkProgress` rolls a spread chance
against every neighboring plot:
- Neighbor is an **empty plot**: `FarmPlantInvasiveSpreadEmptyChance`
  (`data.cdb` sheet `constant`, raw value **0.5** — 50%) chance to place a
  copy of the same plant there.
- Neighbor is a **germinating seed or a dead plant**: overwrites it with a
  copy of the same plant at `FarmPlantInvasiveSpreadOccupiedChance` (raw
  value **0.25** — 25%) chance instead.
- Neighbor is a **live, already-grown plant** (not a seed, not dead): no
  spread roll happens at all — Invasive spread cannot displace an
  established plant, only claim empty/seed/dead ones.

## Finding 17: Farming — `Fruit`/`Extra` "ProductionSpeed" attrs are Quantity, not Speed (corrects Finding 13/14), and the `*Mult` siblings are dead code

Source: `ent.b.PlotZone.updatePlots` (findex 22002, raw disassembly
`Farm.hx:293-373` — `decomp`'s pseudocode output for this function garbles
the operator grouping into a misleading `+ 1 + x * 1 + y * dt` shape, so
this finding is built off `fn 22002`'s raw opcodes, not `decomp`) and
`ent.SpaceBase.gatherPlant__impl` (findex 21908, decompiles cleanly) plus
`data.cdb` sheet `attribute`'s own `name`/`desc`/`note` fields for every
`FarmPlant*` id. Prompted by a player report that Rockwood Bitter's
Temperature=Hot bonus (Finding 13) read "Byproduct speed +40%" in the
CraftMap app but behaved as a quantity bonus in actual play — tracing that
down turned up a mislabeling that runs through most of Finding 13/14's
enrichment lists, not just that one line.

**The `attribute` sheet's own display name/desc, not the raw attr id
string, is the ground truth for what a `FarmPlant*` attr does.** Finding
13/14's original "Fruit speed"/"Byproduct speed" prose was built directly
off the `FarmPlantFruitProductionSpeed`/`FarmPlantExtraProductionSpeed` id
strings' literal "...Speed" suffix, without cross-checking the `attribute`
sheet row itself — which disagrees with the id on both of these:

| attr id | `attribute` sheet `name` | `attribute` sheet `desc` |
|---|---|---|
| `FarmPlantFruitProductionSpeed` (+ `Mult`) | **Germ Quantity** | "affects how many germ products will be gathered at the end of a plant growth" |
| `FarmPlantExtraProductionSpeed` (+ `Mult`) | **Byproduct Quantity** | "affects how many non-germ products will be gathered at the end of a plant growth" |
| `FarmPlantAllSpeed` | Growth and Production Speed | "Growth speed affects how long a plant will take to reach its gatherable state" |
| `FarmPlantGrowthSpeed` (+ `Mult`) | Productive Metabolic Speed | "affects the duration of a plant growth without changing the hourly amount of products generated" |

Both quantity rows' own `desc` explicitly disclaims changing a duration,
and the code confirms it (below). Every "Fruit speed +X%"/"Byproduct speed
+X%" line in Finding 13/14 that traces back to one of these two attrs is
corrected in place to "Fruit quantity +X%"/"Byproduct quantity +X%" in
both findings; `FarmPlantAllSpeed`/`FarmPlantGrowthSpeed(Mult)` lines are
unaffected — their own `desc` is genuinely about duration, and the code
below confirms that distinction too.

**Code confirms the quantity framing.** `updatePlots` accumulates
`plot.progressFruit`/`plot.progressExtra` every tick the plant's
`requires` gate passes (Finding 16), scaled by
`(1 + FruitProductionSpeed_ARatio) * (1 + AllSpeed_ARatio)` and
`(1 + ExtraProductionSpeed_ARatio) * (1 + AllSpeed_ARatio)` respectively
(raw disassembly, `Farm.hx:368-369`, ops 396-431). These two counters are
**never read again until the plant is actually harvested** —
`gatherPlant__impl` (findex 21908) computes
`fruitQty = Math.ceil(plot.progressFruit / plot.durationFruit)` and
`extraQty = Math.ceil(plot.progressExtra / plot.durationExtra)`, grants
that many items in one lump sum, then resets the plot to `EmptyPlot`. A
higher `FruitProductionSpeed`/`ExtraProductionSpeed` ARatio doesn't make
anything happen *sooner* — it makes the progress counter climb faster per
unit time, which nets a **larger single harvest** for the same elapsed
time, exactly the "how many products... gathered" framing in the
attribute's own `desc`. (Separately, `checkProgress`'s own `NoUpdate`
flag check means this accumulation only happens while the plant is still
in its growing stage — the `_Gather` row stops ticking entirely, per
Finding 16 — so both counters are effectively frozen at whatever they
reached by the time growth finished, and harvesting converts that frozen
total into items in one shot rather than a repeating drip. Whether
CraftMap's own "Fruit cycle"/"Byproduct cycle" Timing-box framing — which
currently presents these as ongoing repeating intervals rather than a
single end-of-growth lump sum — should be reworked to match is a separate,
larger question from the quantity-vs-speed mislabeling this finding fixes.
That rework has since been done, with the exact yield formula derived and
the dial/power mechanics verified alongside it: see Finding 18.)

**`FarmPlantFruitProductionSpeedMult`/`FarmPlantExtraProductionSpeedMult`
are dead code as of the current build** — confirmed at the raw-opcode
level, not just decompiler suspicion. `updatePlots` does compute
`progressFruitMRatio`/`progressExtraMRatio` from these two attrs
(`Farm.hx:357`/`359`, via `getEnrichmentValue(..., true)`), but each
`Mul` result lands only in the scratch register (`reg45`), and the very
next opcode overwrites `reg45` with the *next* `getEnrichmentValue` call's
result before it's ever copied back into `progressFruitMRatio`/
`progressExtraMRatio` — no `Mov` back into the accumulator exists for
either. Contrast with `FarmPlantGrowthSpeedMult`: its own `Mul` at
`Farm.hx:355` (op 347) is immediately followed by `Mov reg26 = reg45` (op
348), and the growth-progress formula on `Farm.hx:366` genuinely reads
`reg26` afterward. `progressFruitMRatio`/`progressExtraMRatio` are never
referenced again anywhere else in the function after their dead `Mul`,
and `gatherPlant__impl`'s own harvest-quantity formula above doesn't
reference them either. **Practical effect**: Spacekorn's shared "Light
dial = UV" enrichment (Finding 14 — `FarmPlantAllSpeed +1.5` plus
`FarmPlantExtraProductionSpeedMult ×0.8`, the latter meant as a -20%
byproduct-quantity tradeoff for the speed gain) currently only delivers
the speed component in actual play; the byproduct malus is present in
`data.cdb` but has no effect in the running game.

## Finding 18: Farming — exact per-harvest yield formula, dial/power mechanics, and three corrections (metabolic-slowdown = yield boost; no "no-fertilizer" gate type; EmptyPlot's +5% adjacency)

Source: raw disassembly (`fn`, not `decomp` — several of these functions
decompile mangled) of `ent.b.PlotZone.updatePlots` (22002),
`applyPlant` (22004), `hasMinRequirement` (22005),
`getEnrichmentValue` (22007), `isEnrichmentConditionValid` (22008),
`ent.b.Farm.calcDuration` (21976), `serverRegularUpdate` (21891),
`setDeployInfo` (21884), `get_tempConfig`/`setLight__impl`
(21898/21902), `getPowerUsage` (21888), `PlotZone.getLight` (21993), and
`ent.SpaceBase.gatherPlant__impl` (21908); plus `data.cdb`'s `attribute`
sheet `props.farmValues` tables and the `constant` sheet's `Farm*` rows.
The attr-name globals in `updatePlots` (`global@10054-10060`) were
resolved to their string-pool entries (`g <idx>` + `s <idx>`) rather than
trusting the decompiler's labeling. Extends Findings 16/17; corrects
Findings 13/14 in three places (patched in place there, marked with
"Finding 18").

### The per-harvest yield formula (fruit and byproduct are one lump sum)

Per growth tick (every `FarmZoneGrowthUpdateRate` = 30s of game time),
for a non-seed plant whose `requires` gate passes (`Farm.hx:366-369`,
ops 375-431 — each increment is int-cast per tick, so sub-second
remainders truncate; negligible over real growth spans):

```
progress      += (1 + GrowthSpeedA) * GrowthSpeedM * (1 + AllSpeedA) * dt
progressFruit += (1 + FruitProdA)                  * (1 + AllSpeedA) * dt
progressExtra += (1 + ExtraProdA)                  * (1 + AllSpeedA) * dt
```

(A = summed additive enrichment values, M = multiplied multiplicative
ones — `getEnrichmentValue` (22007, raw-verified if/else) sums every
condition-passing effect for an additive attr from 0, multiplies for a
`*Mult` attr from 1. The Fruit/Extra `*Mult` products are computed then
discarded — Finding 17's dead code, re-confirmed.)

These counters are read in exactly ONE place: `gatherPlant__impl`
(`Farm.hx:1015-1016`) at manual harvest —
`fruitQty = ceil(progressFruit / durationFruit)`,
`extraQty = ceil(progressExtra / durationExtra)` — granted all at once,
then the plot is reset to EmptyPlot. Between maturity and harvest
nothing accrues (the `_Gather` row is `NoUpdate`-flagged, Finding 16),
and `applyPlant` (22004) only zeroes `progressFruit`/`progressExtra`
when the incoming stage LACKS the Gatherable flag — so the grown→Gather
transition preserves them, and seed→grown / harvest→EmptyPlot reset
them. Every `duration*` is `calcDuration` = `round((min +
random()*(max-min)) * 3600)` seconds (21976, raw — the decomp mangles
the precedence), rolled once per stage application.

Substituting growth completion (`progress` reaching `duration`) into the
fruit counter gives the closed form:

```
yield = ceil( (D_growth / D_item) * (1 + QuantityA) / ((1 + GrowthSpeedA) * GrowthSpeedM) )
```

with `D_growth`/`D_item` the independently-rolled stage durations.
Consequences, each now load-bearing for CraftMap's Farming tab:

- **`FarmPlantAllSpeed` cancels out of yield entirely** — it scales
  growth and product accrual identically, so it means "same harvest,
  sooner", never "more".
- **`FarmPlantGrowthSpeed(Mult)` sits in the yield DENOMINATOR** — the
  game's "Productive Metabolic Speed", whose own `desc` ("longer growth
  time but higher product yield") is literally this formula. So Rockwood
  White's Putrescent-neighbor ×0.6 and Rockwood Bitter's Dark-light ×0.8
  (Finding 13) are not pure maluses: they're ~×1.67 and ~×1.25 yield
  boosts (both products) paid for in growth time. Finding 13's bullets
  corrected in place.
- **Base yield per harvest ≈ growth_hours / cycle_hours** (e.g.
  Spacekorn Plain: 36-40h growth ÷ 4-6h per Plain Pulp ≈ 6-10 Pulp per
  harvest) — the fruit/extra `duration` fields are "hours of satisfied
  growth per item", not a repeating post-maturity cycle. Stalled
  (gate-unmet) time adds nothing to either counter.

### Dial and power mechanics (the Temperature/Light dials verified)

- **The 4 Temperature positions are canonical, planet-independent
  targets**: `attribute.props.farmValues[i].temp` is a DELTA from planet
  ambient landing every planet on the same four targets — index 0 =
  −50°C (Cold), 1 = 0°C (Temperate), 2 = +20°C (Warm), 3 = +50°C (Hot).
  Only the energy column varies: `FarmEnergyScale` (30) power per step
  of distance from ambient, 0 at the nearest position (Warm is free on a
  Hot planet, Cold is free on a Frozen one; on Inferno/Dead-Frozen even
  the nearest costs 1 step). UV light costs flat `FarmLightEnergy` (20);
  Natural/Dark are free (`getPowerUsage`, 21888).
- **Dial changes are INSTANT** (`serverRegularUpdate`, 21891 raw): while
  powered, `tempReal`/`lightReal` snap to the setting on the next server
  tick — no ramp, no transition time (`lastTempUpdateTime` is just
  bookkeeping). Unpowered, they snap to `defaultTempSetting`/
  `defaultLightSetting` instead.
- **Deploy-site defaults** (`setDeployInfo`, 21884 raw):
  `defaultTempSetting = findTempConfigWithMinEnergy()` (the
  ambient-nearest position); `defaultLightSetting = deployInfo.light >= 0
  ? Natural : Dark`. And `setLight__impl` (21902) coerces a Natural
  request to Dark whenever `defaultLightSetting == Dark` — **a farm
  deployed somewhere without natural light has no Natural position at
  all**; only UV or Dark.
- **An unpowered or disabled farm fails EVERY plant's gate**
  (`hasMinRequirement` head, 22005): before any per-plant check,
  `!enabled || !isPowered()` → false. Combined with Finding 16, a power
  outage stalls every plant and starts every death timer at once (while
  also snapping the dials to ambient).
- **Gate structure** (22005 raw): liquid (water) is a hard fail —
  `return false` directly, never waivable. Supplements / noSupplements /
  temperature / light / noNeighborBioTag each increment a
  `missingCount`, and the plant passes iff `missingCount <=
  allowMissing` — Rockwood Dream's `FarmPlantIgnoreMinRequirement`
  neighbor tolerance (Finding 13) waives exactly one of THOSE five, and
  only for non-seed stages (the `allowMissing` lookup is gated on
  `!Seed`). `$Main.PREFS.farmPlantReq` is a debug bypass that passes
  everything.
- **The Glow-neighbor UV effect REPLACES the plot's light for all light
  checks** (22005 ops 189-199 and 22008 case 0, both raw: `usedLight =
  hasUniqueEffect("FarmPlantSetLightToUV") ? UV : lightReal`) —
  requirements included, not just bonuses. A Dark-requiring plant
  (Dreamwood is the only one) adjacent to a Glowwood would fail its own
  light gate. Academic in normal play (Glowwood is unplaceable, Finding
  13), but it's the true semantic of "treated as UV-lit".

### Corrections

1. **Metabolic-slowdown enrichments are yield boosts** — covered above;
   Finding 13's White/Bitter bullets corrected in place.
2. **There is no "no fertilizer of any kind" gate type.** CraftMap's
   farming.json had marked Rockwood Glow `fertilizer_forbidden_any` from
   a live-play impression (explicitly flagged for disassembly follow-up;
   Finding 13's table wrote the cell as "none"). The follow-up: `requires`
   supports only specific-item `supplements`/`noSupplements` lists, and
   Glow's data has neither. Flag removed; Finding 13's table cell
   corrected in place.
3. **EmptyPlot has its own adjacency effect nothing had documented**: the
   `farm` sheet's `EmptyPlot` row carries `adjacency: [{value: 0.05,
   attr: FarmPlantAllSpeed, once: false}]` — every adjacent EMPTY plot
   gives a plant +5% Growth & Production speed (yield-neutral, per the
   formula above). A fully-packed grid therefore grows slightly slower
   per-plant than one with gaps — mildly counterintuitive and worth
   knowing next to Plainkorn's same-species +15%.

CraftMap's Farming tab has been reworked against all of this
(farming.json's `effects` model + per-harvest yield calculator replacing
the old three-parallel-cycle-durations Timing box — see that repo's
game_data_extract/farming.json `_meta.harvest_mechanism`/`_meta.effects`/
`_meta.dial_mechanics`).

## Finding 19: Farming — a neighbor's adjacency effects start the instant it's assigned its grown variant, and keep applying through full maturity, not just while it's still growing

Source: raw disassembly (`fn`, not `decomp`) of `ent.b.PlotZone.updatePlots`
(22002, `Farm.hx:288-334`). Prompted by a direct question (does a
companion plant need to still be growing, or even fully grown, for its
adjacency bonus to reach its neighbor?) that neither Finding 16 nor
Finding 18 answered — both cover *when a plot's own requires-gate and
progress-accrual run*, not *when a neighbor's adjacency list is read*.
Those turn out to be two different questions with two different answers.

**The two checks are not the same check.** `updatePlots` loops over every
plot `P` in `this.plots`. For each `P` it first reads `P`'s own plant's
`props.flags` bit 0 (the NoUpdate flag — set on `_Gather`/`_Dead` rows
per Finding 16) and, if set, skips `P` entirely for this tick
(`Farm.hx:289`, ops 25-39: `flags & 1 != 0` → jump past the rest of the
per-plot block, `continue`-equivalent). This is the *only* place this
function checks a NoUpdate flag. It then calls `getNeighbors` for `P`
(`Farm.hx:305`, op 60) and, for each neighbor `N`, resolves `N.plant` to
its data row and reads `.adjacency` straight off it (`Farm.hx:306-307`,
ops 74-80) — **no flags/NoUpdate check on `N` anywhere in this loop**,
confirmed by tracing every register touched between the neighbor fetch
(op 73) and the attr-dispatch switch (ops 106-334): `reg36` (`N`'s
resolved row) is read for `.adjacency` (op 77) and never once for
`.props.flags`. The dedup/target/once-flag handling that follows
(`Farm.hx:310-316`, target-match at ops 98-105, `hasUniqueEffect` at ops
106-111, `once`-flag immediate-consume at ops 112-116, then the
attr-name string-compare chain from Finding 16) all operates on `N`'s
adjacency *entries*, never on `N`'s own growth state.

**What actually gates a neighbor's contribution, then, is which row
currently occupies its plot — not that row's own progress:**
- **Germinating seed** (`pickVariant` hasn't fired yet): `N.plant` still
  points at the seed row (e.g. `RockwoodSeed`), and no seed row in
  Findings 13/14's data carries an `adjacency` array of its own — every
  documented `adjacency` entry belongs to a *grown variant* row. A
  germinating neighbor therefore gives nothing yet (the `.length == 0`
  short-circuit at `Farm.hx:307-308`, ops 80-82, skips it with no attr
  ever read) — not because of a stage check, but because there's simply
  no data to read.
- **Grown variant, still growing** (`pickVariant` has resolved, `progress
  < duration`): `N.plant` now points at e.g. `Rockwood`/Green, and
  *that* row's `adjacency` array is what gets read — active from this
  moment, not deferred until growth finishes.
- **Grown variant, fully mature, sitting at `_Gather`** (`progress >
  duration`, not yet harvested): still the exact same row, still the
  exact same `adjacency` array. Nothing distinguishes "still growing"
  from "done growing, awaiting harvest" in this read path — a mature,
  unharvested companion keeps buffing its neighbors indefinitely.
- **Dead** (`progressDeath` exceeded the 5h threshold, Finding 16):
  `applyPlant` swaps `N.plant` to that variant's own `deadVariant` row
  (e.g. a `*_Dead` id), a genuinely different row id — adjacency stops
  here not via a special case, but because that row is presumably its
  own (empty) data, same mechanism as the seed case above.

**Practical upshot**: a Reclusive/Putrescent/blanket-adjacency pairing
(Rockwood Green + Woolly Spacekorn, White + Bitter, Plain + Sour, etc.)
doesn't need its two crops harvested in lockstep to keep working — the
companion buffs its neighbor for its *entire* post-germination lifetime,
including while sitting ready-to-harvest, right up until it's actually
picked (which empties the plot) or dies. CraftMap's Farming tab Layouts
view (goal_presets referencing these neighbor pairings) relies on this —
see that repo's `game_data_extract/farming.json` `_meta`
(`growth_death_mechanism`) for the player-facing version of this note.

## Finding 20: Farming — Rockwood Dream's neighbor tolerance makes a gate *pass*, not just survive; it's the only `allowMissing` source in the dataset, and it stacks with Finding 19 to let a neighbor grow on a dial its own gate would normally reject

Source: rereading Finding 13's own already-disassembly-verified
`hasMinRequirement` description (`Farm.hx:437-459`, findex 22005)
alongside Finding 19 above, prompted by a direct question about whether
Dream's tolerance could let Woolly Spacekorn grow while parked next to
something on the "wrong" dial. No new disassembly was pulled for this
finding — it's a rereading of two already-verified mechanics together,
not an independent bytecode check, and the numeric consequences below
have not been tested in actual play.

**The tolerance isn't death-insurance, it's gate-passing.** Finding 13's
own text already establishes the mechanism precisely:
`hasMinRequirement` increments `missingCount` for each of five
independent checks (`supplements`/`noSupplements`/`temperature`/`light`/
`noNeighborBioTag`, `liquid` excluded — that one's a hard, never-waivable
fail), and **the gate passes iff `missingCount <= allowMissing`**.
Rockwood Dream's adjacency effect (`FarmPlantIgnoreMinRequirement`) is
the only thing in this dataset that raises `allowMissing` above its
default of 0 — checked across every variant's `enrichments`/`adjacency`
in Findings 13/14, nothing else grants it. Raising `allowMissing` to 1
doesn't just slow a *failing* plant's death countdown (that's Bitter's
separate `FarmPlantDeathSpeed` effect, a different accumulator entirely,
Finding 16) — it means a plant with exactly one of those five checks
wrong **passes the gate as if nothing were wrong**, and keeps growing
normally. `allowMissing` still caps at 1 regardless of neighbor count
(Finding 13's own loophole-ruled-out note, re-confirmed by the
`hasUniqueEffect` dedup Finding 19 already traced) — a variant needing
two simultaneous waivers (only Rockwood Glow, needing both its
permanently-`0` temperature and light gates covered) still can't be
reached this way.

**Combined with Finding 19 (a live, un-parked Dream already emits this —
no maturity or parking required), two concrete consequences:**

1. **Fertilizer-skipping, zero yield cost.** A "supplements" (fertilizer-
   required) miss is one `missingCount` hit regardless of how many listed
   items are actually missing (Finding 13's own AND-with-short-circuit
   description) — so a Dream-adjacent plant can skip its ENTIRE required
   list at once. Checked which required items carry no separate
   enrichment of their own (so skipping them costs nothing beyond the
   gate check): Rockwood Green's Neutral Fertilizer, Rockwood White's
   Metallic Fertilizer, and Rockwood Bitter's Acidic+Metallic pair
   (both at once, still one miss) all qualify — free savings, no output
   change. Caveat: the tolerance is gated on non-seed stages only (per
   Finding 13's own text) — the real fertilizer still has to be present
   at the exact tick a seed's `pickVariant` roll resolves into that
   variant, since the still-undecided seed doesn't get the waiver. It
   can be pulled immediately after that moment, not before it.

2. **Growing off a variant's own dial, at a real cost.** A Dream neighbor
   can waive a `temperature` (or `light`) miss the same way, letting
   Bitter/Plain/Sour grow on whichever of Dream's own compatible
   positions (`Cold`/`Temperate`/`Warm` + `Dark`) doesn't match their own
   gate — but each forfeits its own dial-triggered enrichment doing so
   (Bitter's Hot-only Byproduct+40%, Plain's Temperate-only speed), so
   per-plant output is always worse than growing it on its own dedicated
   setup. The one variant this genuinely enables rather than just
   permits is Woolly Spacekorn: its `Cold`-only gate has zero overlap
   with anything else in either crop, so under any non-Cold dial it
   can't grow *at all* without a Dream neighbor's waiver — turning a
   live, fully-sustainable Dream+Woolly weave into a real (if
   non-optimal-per-plant) way to get both products off one farm.

**A sharper case specifically under `Cold`** (Woolly's own native dial,
needing no tolerance for temperature at all): Woolly's one tolerance
slot, unused by temperature here, is free to cover a *different* miss —
its own `noNeighborBioTag` restriction (`Putrescent`) — letting it sit
directly next to Sour or Bitter and hand over its `+20%` Byproduct
quantity live, no parking needed (parking, per Finding 19, is still the
right approach under a non-Cold dial specifically, where the tolerance
is already spent on temperature and can't also cover a Putrescent
neighbor). Verified against the actual yield formula (Finding 18), not
just the gate mechanic: a White plot can take both Bitter's
Putrescent-mult trade AND a Woolly neighbor's quantity bonus at once
(neither is Reclusive/Putrescent to the other, so both fit on one plot
without conflicting) — harvest Kaolinite 152 → 166 (+9%); a Sour plot
gains a Woolly neighbor the same way — harvest Sour Pulp 20 → 22 (+10%).

**Neither number changes what CraftMap actually recommends**, and this
is the more important half of the finding. CraftMap's own farm-total
audit (`game_data_extract/farming.json` `_meta.per_slot_vs_per_farm`)
found that Spacekorn Plain's per-plot-superior Sour-neighbor stripe
*loses* to a plain monoculture once weighted by how many plots each
layout actually fits (15 vs 9) — a large per-plot gain on a much smaller
plot count routinely fails to clear that bar in a 15-plot grid. The two
numbers above are real at the single-plot level but have not been
re-verified at the full-grid level (fitting enough Dream+Woolly coverage
to enhance most or all of a variant's plots, while keeping every
Reclusive/Putrescent restriction intact across 15 cells, is a real
constraint-satisfaction problem that was judged too easy to get subtly
wrong by hand and was not attempted) — treat them as verified-per-plot,
not verified-per-farm, and not in-game tested at all.
