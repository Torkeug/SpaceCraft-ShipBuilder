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
  independently contributes `count = uniform_int(min, max)` placed
  instances (confirmed matching the already-documented `depositGroupSizes`
  behavior in the sibling `spacecraft-memory-research` repo). If the
  *enclosing* group has its own `props.explodingChance` set (e.g.
  `ShipWreck_JunkGroup_lvl0`'s `0.1`), that count is instead drawn from a
  wider `uniform_int(2*min, 3*max)` range with that probability — the exact
  bit-level trigger check was mangled by the decompiler (same class of
  issue as Finding 6's opcode soup), so this is modeled as a literal
  probability roll, the obvious reading of a field named `explodingChance`.
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
algorithm above exactly against live `data.cdb` values)**:

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
