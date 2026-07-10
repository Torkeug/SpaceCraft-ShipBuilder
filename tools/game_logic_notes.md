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
