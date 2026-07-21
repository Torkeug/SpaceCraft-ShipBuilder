"""
Derive a per-sector, per-item shipwreck rare-loot-crate analysis from the
game's own data.cdb (shipbuilder/pak_out/data.cdb) and write it to
craftmap/game_data_extract/shipwreck_loot.json, for review before deciding
how (or whether) to surface it in CraftMap's UI.

This is NOT a raw sheet dump like extract_craft_data.py's other outputs -
it's a computed simulation of the in-game drop-generation algorithm
(src/logic/Loot.hx, decompiled via hlbc from hlboot.dat - see
tools/game_logic_notes.md Findings 5 and 6), combined with the static
sector/craft/item tables from data.cdb. Re-run this whenever data.cdb is
refreshed (see tools/pak_extract.py) or if game_logic_notes.md's Loot.hx
findings are revised.

Usage:
    python tools/extract_shipwreck_loot.py
"""
import json
import random
import re
from collections import defaultdict
from math import floor, log10
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CDB_PATH = REPO_ROOT / "shipbuilder" / "pak_out" / "data.cdb"
OUT_PATH = REPO_ROOT / "Craftmap" / "game_data_extract" / "shipwreck_loot.json"

CRATE_SPAWN_TRIALS = 20000

# A rare crate (ShipWreck_LootChestRare_lvl{0,1,2}) rolls one of 4 levels
# weighted 40/30/20/10, banded by the wreck's own tier (0/1/2, read off the
# "_N" suffix of its resGen id in sector.generation.wreckResGen).
CHEST_LEVELS = {0: [4, 5, 6, 7], 1: [5, 6, 7, 8], 2: [6, 7, 8, 9]}
CHEST_WEIGHTS = [40, 30, 20, 10]


def load_sheets():
    with open(CDB_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {s["name"]: s for s in data["sheets"]}


def item_type_chain(item_types, type_id):
    chain = []
    while type_id:
        chain.append(type_id)
        type_id = item_types.get(type_id, {}).get("parent")
    return chain


def primary_drop_probability(level):
    """P(a Patch-or-Blueprint primary item drops at all) at a given crate
    target level. Confirmed against raw HashLink opcodes,
    src/logic/Loot.hx:158-159: clamp((level-2)/(7-2), 0, 1)."""
    return max(0.0, min(1.0, (level - 2) / 5))


def sigfig(x, n=2):
    if x == 0:
        return 0.0
    digits = n - 1 - floor(log10(abs(x)))
    return round(x, digits)


def tier_of_resgen(resgen_id):
    m = re.search(r"_(\d)$", resgen_id)
    return int(m.group(1)) if m else None


UNLOCK_TYPE_RANDOM_BLUEPRINT = 2
"""craft.unlockType's own CastleDB enum column (`data.cdb`'s craft sheet,
column typeStr: "5:Permit,Unique_Blueprint,Random_Blueprint,Cannot_Unlock,
Study,Dismantle,Custo") - 0=Permit (always known), 1=Unique_Blueprint (a
FIXED, non-random source - quest/vendor/location, NOT this crate system),
2=Random_Blueprint, 3=Cannot_Unlock, 4=Study, 5=Dismantle, 6=Custom. Only
value 2 is ever produced by the crate primary-item generator below."""


def build_pools(sheets):
    """Returns (patch_by_level, blueprint_by_level): {lootLevel: [{id, name}]}.

    Blueprint eligibility requires BOTH craft.lootLevel set AND
    craft.unlockType == Random_Blueprint (2) - confirmed via raw opcodes,
    the dedicated Blueprint-candidate closure at src/logic/Loot.hx:426-445
    (embedded in generatePrimaryItemCandidate, called only for the item-type
    category matching global@7374/"Blueprint" - see game_logic_notes.md
    Finding 15). That closure iterates Data.craft.all directly (NOT the
    `item` sheet used for Patch/Tool/Module) and explicitly skips any craft
    row whose unlockType != 2, before ever looking at lootLevel-window
    membership. A recipe with unlockType==1 (Unique_Blueprint, e.g. a fixed
    quest/vendor/location source) has its own lootLevel populated in the
    sheet but is UNREACHABLE from this crate system - confirmed live: as of
    the 2026-07-21 game build, Patch_SystemIntegration3 ("Blueprint: Module
    Patch: System III", lootLevel 9) is unlockType==1 and therefore excluded
    here, despite carrying a dev note ("Placé en Random Blueprint when the
    craft is right") suggesting it was intended to move to unlockType==2
    eventually - it had not, as of that build. Earlier versions of this
    function only checked lootLevel, which wrongly marked 16 Unique_Blueprint
    recipes (of 84 total lootLevel-tagged craft rows) as crate-obtainable."""
    items = {l["id"]: l for l in sheets["item"]["lines"]}
    item_types = {l["id"]: l for l in sheets["itemType"]["lines"]}
    craft = sheets["craft"]["lines"]

    patch_by_level = defaultdict(list)
    for l in items.values():
        if "Patch" in item_type_chain(item_types, l.get("type", "")) and l.get("lootLevel") is not None:
            patch_by_level[l["lootLevel"]].append({"id": l["id"], "name": l["name"]})

    bp_by_level = defaultdict(list)
    for l in craft:
        if l.get("lootLevel") is None:
            continue
        if l.get("unlockType") != UNLOCK_TYPE_RANDOM_BLUEPRINT:
            continue
        out = l["outputs"][0]["item"] if l.get("outputs") else None
        item_name = items.get(out, {}).get("name", out)
        bp_by_level[l["lootLevel"]].append(
            {"id": l["id"], "name": f"Blueprint: {item_name}", "output_item": out}
        )
    return patch_by_level, bp_by_level


def load_category_itl(sheets):
    """The 'itl' (Item Type Level) baseline each primary-item category is
    weighted around - read live from data.cdb's `constant` sheet (not
    hardcoded, since these are real balance values, unlike the CHEST_LEVELS/
    CHEST_WEIGHTS code constants above) so this stays correct across game
    patches. Confirmed via raw opcodes (generatePrimaryItemCandidate,
    findex 22154, and its embedded Blueprint-candidate closure at
    Loot.hx:426-445 - see game_logic_notes.md Finding 15): each category's
    itl is looked up either as a direct Const field (ToolModule, ShipDecorative)
    or via Const.resolve(<name>) (Patch, Blueprint), all four ultimately
    reading the same named rows in this sheet."""
    constants = {
        l["id"]: l["val"]["float"]
        for l in sheets["constant"]["lines"]
        if "float" in l.get("val", {})
    }
    return {
        "toolmodule": constants["Loot_Primary_ItemTypeLevel_ToolModule"],
        "patch": constants["Loot_Primary_ItemTypeLevel_Patch"],
        "blueprint": constants["Loot_Primary_ItemTypeLevel_Blueprint"],
        "shipdecorative": constants["Loot_Primary_ItemTypeLevel_ShipDecorative"],
    }


def category_weight(itl, target_level, item_level):
    """A primary-item category's own selection weight once its in-window
    representative item has been picked (item_level is that representative's
    OWN lootLevel, either target_level or target_level-1) - exact formula
    from raw opcodes, generatePrimaryItem@22152 (Loot.hx:295): weight =
    max(0, 10 - |target_level - itl| - 2*(target_level - item_level)). See
    game_logic_notes.md Finding 15."""
    return max(0.0, 10 - abs(target_level - itl) - 2 * (target_level - item_level))


def opposing_category_win_share(own_weight, opp_itl, opp_pool_by_level, target_level):
    """P(own category's already-fixed-weight candidate wins the weighted
    cross-category draw) against ONE opposing category, exact (not
    simulated) - the opposing category's own in-window representative can
    only land on target_level or target_level-1, so this is a 2-outcome
    expectation weighted by the opposing pool's own split across those two
    levels. Returns 1.0 if the opposing category has no eligible candidate
    at all (no competition). Patch vs Blueprint is the ONLY pairwise
    competition that actually matters for ShipWreck_LootChestRare_lvl{0,1,2}:
    confirmed directly from data.cdb's `loot` sheet - the rows those crates
    reference (ShipWreck_Loot_4..9) have primaryItemTypes==12 (bit order
    Tool=1,Module=2,Patch=4,Blueprint=8,ShipDecorative=16 per that column's
    own typeStr), i.e. Patch|Blueprint only - Tool/Module/ShipDecorative bits
    are OFF, so those categories' branches in generatePrimaryItem never even
    get a chance to compete for THIS crate type's primary-item slot, despite
    existing as real code paths. This was confirmed after a direct play-
    experience challenge (drops observed are always Patch/Blueprint/
    materials, never a bare Tool or Module) - see game_logic_notes.md
    Finding 15's own correction note."""
    n_at_l = len(opp_pool_by_level.get(target_level, []))
    n_at_lm1 = len(opp_pool_by_level.get(target_level - 1, []))
    total = n_at_l + n_at_lm1
    if total == 0:
        return 1.0
    share = 0.0
    for n, opp_level in ((n_at_l, target_level), (n_at_lm1, target_level - 1)):
        if n <= 0:
            continue
        p = n / total
        opp_weight = category_weight(opp_itl, target_level, opp_level)
        denom = own_weight + opp_weight
        share += p * (own_weight / denom if denom > 0 else 0.5)
    return share


def build_sector_profiles(sheets):
    """Returns {sector_name: {level: capped_probability}} plus per-sector metadata."""
    sectors = {}
    for l in sheets["sector"]["lines"]:
        gen = l.get("generation", {})
        wreck_resgen = gen.get("wreckResGen")
        if not wreck_resgen:
            continue
        props = l.get("props", {})
        requirements = props.get("requirements", [])
        explo_level = next(
            (r["level"] for r in requirements if r.get("attribute") == "Exploration"), None
        )
        max_loot_level = props.get("maxLootLevel", 999)
        loot_material = [i.get("item") for i in props.get("lootMaterial", [])]

        tiers = [tier_of_resgen(r["resGen"]) for r in wreck_resgen]
        tier_count = defaultdict(int)
        for t in tiers:
            tier_count[t] += 1
        total = len(tiers)

        # Weighted level distribution across whichever wreck tiers this
        # sector's wreckResGen list can produce, then capped by maxLootLevel
        # (a crate's raw rolled level clamps to the sector's ceiling before
        # any item-pool lookup happens).
        level_prob = defaultdict(float)
        for tier, count in tier_count.items():
            tier_weight = count / total
            for lvl, w in zip(CHEST_LEVELS[tier], CHEST_WEIGHTS):
                level_prob[lvl] += tier_weight * (w / 100)
        capped_prob = defaultdict(float)
        for lvl, p in level_prob.items():
            capped_prob[min(lvl, max_loot_level)] += p

        sectors[l["id"]] = {
            "name": l.get("name"),
            "exploLevel": explo_level,
            "maxLootLevel": max_loot_level,
            "wreckTierCounts": dict(tier_count),
            "lootLevelProbability": {str(k): round(v, 4) for k, v in sorted(capped_prob.items())},
            "secondaryMaterialPool": loot_material,
        }
    return sectors


def compute_item_drop_odds(pool_by_level, own_itl, opp_itl, opp_pool_by_level, sector_level_prob):
    """For every item in pool_by_level, compute its drop probability per
    sector, using the CORRECTED 2-level search window (Finding 6):
    a crate targeting (capped) level L pools every candidate with
    lootLevel in {L-1, L} and draws one uniformly - confirmed via raw
    opcodes at src/logic/Loot.hx:461-478. So an item with lootLevel Lx is
    reachable from a crate whose target level L is either Lx or Lx+1.

    The Patch-vs-Blueprint category split uses the REAL per-candidate
    weighting formula (category_weight/opposing_category_win_share - see
    game_logic_notes.md Finding 15), not a flat 50/50 guess as earlier
    versions of this function assumed. This is the COMPLETE model for
    ShipWreck_LootChestRare_lvl{0,1,2} specifically - confirmed from
    data.cdb's own `loot` sheet that those crates' primaryItemTypes==12
    (Patch|Blueprint only; Tool/Module/ShipDecorative bits are off), so no
    third category ever competes for this crate type's primary-item slot,
    despite Tool/Module/ShipDecorative all being real, separate branches in
    the underlying code (see opposing_category_win_share's own docstring).
    """

    def pool_size(target_level):
        return len(pool_by_level.get(target_level - 1, [])) + len(pool_by_level.get(target_level, []))

    rows = []
    for lx, entries in pool_by_level.items():
        for entry in entries:
            per_sector = {}
            for sector_name, level_prob in sector_level_prob.items():
                total = 0.0
                for target_level in (lx, lx + 1):
                    p_level = level_prob.get(target_level)
                    if not p_level:
                        continue
                    n = pool_size(target_level)
                    if n == 0:
                        continue
                    own_weight = category_weight(own_itl, target_level, lx)
                    share = opposing_category_win_share(own_weight, opp_itl, opp_pool_by_level, target_level)
                    total += p_level * primary_drop_probability(target_level) * share / n
                if total > 0:
                    per_sector[sector_name] = total

            groups = defaultdict(list)
            for sector_name, p in per_sector.items():
                pct = sigfig(p * 100, 2)
                if pct > 0:
                    groups[pct].append(sector_name)
            grouped = [
                {"pct": pct, "sectors": sorted(v)} for pct, v in sorted(groups.items(), reverse=True)
            ]
            rows.append(
                {
                    "name": entry["name"],
                    "level": lx,
                    "bestPct": grouped[0]["pct"] if grouped else 0,
                    "groups": grouped,
                    "obtainable": bool(grouped),
                }
            )
    rows.sort(key=lambda r: (r["level"], r["name"]))
    return rows


def res_group_count(min_, max_, exploding_chance):
    """One draw of a `{min,max,gen}` group entry's instance count, per the
    decompiled logic.gen.PlanetRes.getResGroupCount (findex 11224): a plain
    uniform int in [min,max], or - with probability exploding_chance, when
    the *enclosing* group has that prop set (e.g. ShipWreck_JunkGroup_lvl0's
    0.1) - a uniform int in the wider [2*min, 3*max] range instead.

    NOTE (2026-07-20): raw opcodes (src/logic/gen/PlanetRes.hx:644-649) show
    both branches actually draw TWO independent uniforms over the same span
    and take the MINIMUM, not a single draw - min(A,B) sits lower on average
    than one draw (E[min of 2 uniforms over span S] = S/3, not S/2), so this
    plain-randint version overstates expected crate counts relative to the
    real algorithm. NOT switched to the corrected version yet: doing so
    widens the gap against CraftMap's live-tracked observed crate counts
    (tools/audit_wreck_crate_rates.py in the sibling Craftmap repo) from
    ~2x to ~4x rather than closing it, so there is still at least one other
    unidentified factor at play and this file's shipped numbers are left
    alone pending that - see the investigation thread referenced there
    before changing this function."""
    if exploding_chance and random.random() < exploding_chance:
        lo, hi = 2 * min_, 3 * max_
    else:
        lo, hi = min_, max_
    return random.randint(lo, max(hi, lo))


def count_crates_in_wreck(resgroup, group_id, depth=0):
    """One simulated placement pass through a resGroup's generation tree
    (per decompiled logic.gen.PlanetRes.generateGroup, findex 11222):
    `groups` entries all fire independently (AND, each its own random
    count, and each instance can itself recurse into another full
    sub-tree - so multiple crates from one wreck are possible and were
    confirmed common for Big wrecks); `overrides` entries pick exactly one
    weighted branch (OR). Returns the number of ShipWreck_LootChestRare_*
    resources placed in this pass (0 if none)."""
    if depth > 20:
        return 0
    g = resgroup[group_id]
    gen = g.get("generation", {})

    if "groups" in gen:
        exploding_chance = g.get("props", {}).get("explodingChance")
        total = 0
        for entry in gen["groups"]:
            count = res_group_count(entry["min"], entry["max"], exploding_chance)
            sub = entry["gen"]
            for _ in range(count):
                if "group" in sub:
                    total += count_crates_in_wreck(resgroup, sub["group"], depth + 1)
                elif sub["res"].startswith("ShipWreck_LootChestRare"):
                    total += 1
        return total

    if "overrides" in gen:
        total_w = sum(o["weight"] for o in gen["overrides"])
        k = random.random() * total_w
        for o in gen["overrides"]:
            k -= o["weight"]
            if k <= 0:
                sub = o["gen"]
                if "group" in sub:
                    return count_crates_in_wreck(resgroup, sub["group"], depth + 1)
                return 1 if sub["res"].startswith("ShipWreck_LootChestRare") else 0
        return 0

    return 0


def crate_count_stats_raw(resgen, resgroup, resgen_id, cache, trials=CRATE_SPAWN_TRIALS):
    """Unrounded Monte Carlo crate-count distribution for ONE specific wreck
    variant (a single resGen id, e.g. "ShipWreck_Big_1" - which pins down
    BOTH size and tier at once, since a sector's wreckResGen list picks a
    whole resGen id, not size/tier independently - see game_logic_notes.md
    Finding 8). This is the atomic building block both
    compute_crate_spawn_stats (sector-level rollup, rounds its own output)
    and compute_wreck_site_item_odds (per-item composition, needs full
    precision for the (1-p)**k sum) are built from - factored out and
    cache-shared between them (passed in from main()) so the ~9 distinct
    wreck resGen ids across all sectors only get simulated once each, not
    once per consumer."""
    if resgen_id in cache:
        return cache[resgen_id]
    root = resgen[resgen_id]["resources"][0]["res"]
    counts = [count_crates_in_wreck(resgroup, root) for _ in range(trials)]
    dist = defaultdict(int)
    for c in counts:
        dist[c] += 1
    stats = {
        "atLeastOne": sum(c > 0 for c in counts) / trials,
        "expectedCount": sum(counts) / trials,
        "countDistribution": {k: v / trials for k, v in dist.items()},
    }
    cache[resgen_id] = stats
    return stats


def wreck_size_of_resgen(resgen_id):
    """"Small" or "Big" from a resGen id like "ShipWreck_Big_1" - a sector's
    wreckResGen list is a flat mix of both, and Small vs Big is the ONE
    thing that moves crate count 4x (Finding 8) - tier does not. Confirmed
    directly from data.cdb's resGroup sheet: GShipWreck_Small_lvlN places a
    single ShipWreck_LvlN hull piece and rolls its JunkGroup 1-2 times;
    GShipWreck_Big_lvlN places four hull debris pieces (BigPiece1/2_lvlN +
    SmallPiece1/2 - "SmallPiece" here means small DEBRIS chunk of a Big
    wreck's hull, unrelated to this Small/Big wreck-SIZE axis) and rolls
    JunkGroup 5-10 times."""
    if "_Big_" in resgen_id:
        return "Big"
    if "_Small_" in resgen_id:
        return "Small"
    return None


def secondary_spawn_group_id(size, tier):
    """The resGroupSpawn target fired for a wreck's own hull PIECE (not
    the surrounding debris field crate_count_stats_raw/count_crates_in_wreck
    already cover) - a SECOND, independent loot-generation pass, confirmed
    (src/logic/gen/PlanetRes.hx:559-564, generateResource@11223) to fire
    UNCONDITIONALLY at world-generation, the instant the hull piece is
    first placed - NOT a player-triggered action. `ShipWreck_
    DismantledJunkGroup_lvl{tier}[_Small]`'s own name is misleading here:
    despite the name, it has NO connection to the player's actual "dismantle
    a hull piece" RPC action (st.PlanetResourceManager.rpcDismantle__impl@
    8117 -> _dismantle@23642) - that action grants a separate, fixed junk
    bundle via the resource's own props.loot (confirmed: never includes a
    crate, checked across the entire `resource` sheet), updates the parent
    wreck Core's lastMining timestamp (feeding Finding 5's removal-cycle
    probability), and _remove()s the dismantled piece - it never calls
    generateGroup/resGroupSpawn at all. The two "dismantle"-named things
    are unrelated; this function is named for the FIRST one only (the
    resGroupSpawn target), not the player action.

    Confirmed directly from data.cdb's `resource` sheet: exactly one hull
    piece per wreck ever carries a props.resGroupSpawn at all - a Small
    wreck's own single ShipWreck_Lvl{tier} piece (-> ShipWreck_
    DismantledJunkGroup_lvl{tier}_Small, a direct {min:0,max:4}
    ShipWreck_LootChestRare_lvl{tier} placement - no RareLoot override
    layer), or for a Big wreck, ONLY BigPiece1_lvl{tier} (-> ShipWreck_
    DismantledJunkGroup_lvl{tier}, a {min:3,max:15} RareLoot_lvl{tier}
    invocation count, ~6x the debris field's own {min:0,max:2}) - never
    BigPiece2/SmallPiece1/SmallPiece2.

    This was missing entirely from both this file's original Monte Carlo
    AND the sibling spacecraft-memory-research repo's independent
    closed-form resgroup_expected_crate_count - neither ever looked past
    a wreck's own outer GShipWreck_{size}_lvl{tier} resGroup tree into an
    individually-placed resource's own props.resGroupSpawn, a completely
    separate trigger mechanism from the generation tree both were already
    walking. Confirmed live against CraftMap's wreck_events telemetry
    (2026-07-20, see tools/audit_wreck_crate_rates.py in the sibling
    Craftmap repo, and a direct live-memory read of 4 currently-existing
    wrecks): debris-field-only expectedCount (3.65 Big / 0.89 Small)
    undershot real observed per-wreck crate counts (~7.0 Big / ~1.86
    Small) by 2-4x; debris + this secondary spawn (7.09 Big / 2.89 Small)
    landed within 1.3% of observed for Big, but overshoots Small - and
    since this pass is unconditional (not player-behavior-dependent, per
    the trigger confirmation above), that Small gap is a genuinely
    unresolved discrepancy, not a "players don't always mine it" story.
    One further live-data anomaly still unexplained: 0 of 71 historically
    observed Small wreck sites ever show 0 crates, though this model
    predicts roughly an 8% chance of that outcome - some guarantee of at
    least one crate on Small wrecks likely exists that neither this pass
    nor the debris field accounts for."""
    if size == "Small":
        return f"ShipWreck_DismantledJunkGroup_lvl{tier}_Small"
    return f"ShipWreck_DismantledJunkGroup_lvl{tier}"


def crate_count_stats_for_group(resgroup, group_id, cache, trials=CRATE_SPAWN_TRIALS):
    """Same Monte Carlo as crate_count_stats_raw, but for a resGroup id
    reached directly via a resource's own props.resGroupSpawn (not wrapped
    in a resGen entry the way a wreck's own initial debris field is) - see
    secondary_spawn_group_id. Shares crate_count_stats_raw's cache (keyed
    by id string, so a secondary-spawn group id never collides with a
    resGen id)."""
    if group_id in cache:
        return cache[group_id]
    counts = [count_crates_in_wreck(resgroup, group_id) for _ in range(trials)]
    dist = defaultdict(int)
    for c in counts:
        dist[c] += 1
    stats = {
        "atLeastOne": sum(c > 0 for c in counts) / trials,
        "expectedCount": sum(counts) / trials,
        "countDistribution": {k: v / trials for k, v in dist.items()},
    }
    cache[group_id] = stats
    return stats


def combine_independent_counts(stats_a, stats_b):
    """Count distribution of stats_a's crates PLUS stats_b's crates from
    one wreck (e.g. debris field + the secondary resGroupSpawn pass) - a
    convolution of the two independent distributions, not just their means
    added (though the means DO simply add, by linearity of expectation -
    it's countDistribution's own shape that needs the full convolution)."""
    dist = defaultdict(float)
    for ka, va in stats_a["countDistribution"].items():
        for kb, vb in stats_b["countDistribution"].items():
            dist[int(ka) + int(kb)] += va * vb
    return {
        "atLeastOne": round(1 - dist.get(0, 0.0), 4),
        "expectedCount": round(stats_a["expectedCount"] + stats_b["expectedCount"], 3),
        "countDistribution": {str(k): round(v, 4) for k, v in sorted(dist.items())},
    }


def compute_crate_spawn_stats(sheets, sectors, cache):
    """{sector_name: {atLeastOne, expectedCount, countDistribution, bySize}}
    for a randomly-rolled wreck in that sector, Monte Carlo simulated from
    the actual generation tree - see game_logic_notes.md Finding 8.
    Tier-independent (the crate-vs-junk override weight is identical at
    every tier - only Small vs Big wreck size moves these numbers), unlike
    lootLevelProbability. A single wreck can contain more than one crate
    (each JunkGroup invocation independently re-rolls its own RareLoot
    slot(s), and Big wrecks invoke JunkGroup many times) - countDistribution
    captures that instead of just collapsing to a single spawn-or-not %.

    Every stat here comes in TWO flavors: the bare debris-field figures
    (top level, and bySize.{Small,Big} - what a wreck's scattered debris
    alone contains) and ITS OWN sibling (bySize.{Small,Big}.secondarySpawn/
    total - the resGroupSpawn-triggered pass a wreck's hull piece ALWAYS
    generates in addition, confirmed unconditional/not player-behavior-
    dependent - see secondary_spawn_group_id - plus debris+secondarySpawn
    combined). "total" is not an alternate scenario for a player who
    happens to dismantle the hull - it fires regardless, so it's simply
    the real total crate count for that wreck; debris-field-only is
    exposed alongside it just to show where the total comes from.

    The blended atLeastOne/expectedCount/countDistribution average across
    EVERY wreck-size variant in the sector's own wreckResGen list (weighted
    by repetition) - useful as an overall sector figure, but NOT what you
    should expect from any one wreck you actually walk up to, since a
    sector's Big wrecks alone already run ~4x a Small wreck's own expected
    count (confirmed against live-tracked CraftMap wreck_events data - see
    tools/audit_wreck_crate_rates.py in the sibling Craftmap repo). bySize
    splits the identical Monte Carlo stats out per wreck SIZE (missing a
    key if that sector's wreckResGen list has no variant of that size at
    all) so a caller who already knows which size wreck it's looking at
    (from the hull it can see - BigPiece/SmallPiece debris vs a single
    plain hull piece) can quote the number that actually applies, instead
    of the diluted sector-wide blend."""
    resgen = {l["id"]: l for l in sheets["resGen"]["lines"]}
    resgroup = {l["id"]: l for l in sheets["resGroup"]["lines"]}

    def rollup(entries):
        n = len(entries)
        merged_dist = defaultdict(float)
        for e in entries:
            for k, v in e["countDistribution"].items():
                merged_dist[k] += v / n
        return {
            "atLeastOne": round(sum(e["atLeastOne"] for e in entries) / n, 4),
            "expectedCount": round(sum(e["expectedCount"] for e in entries) / n, 3),
            "countDistribution": {
                str(k): round(v, 4) for k, v in sorted(merged_dist.items(), key=lambda kv: kv[0])
            },
        }

    result = {}
    for l in sheets["sector"]["lines"]:
        wr = l.get("generation", {}).get("wreckResGen")
        if not wr:
            continue
        entries = [crate_count_stats_raw(resgen, resgroup, entry["resGen"], cache) for entry in wr]
        sizes = [wreck_size_of_resgen(entry["resGen"]) for entry in wr]
        tiers = [tier_of_resgen(entry["resGen"]) for entry in wr]
        secondary_entries = [
            crate_count_stats_for_group(resgroup, secondary_spawn_group_id(size, tier), cache)
            for size, tier in zip(sizes, tiers)
        ]
        combined_entries = [
            combine_independent_counts(base, bonus) for base, bonus in zip(entries, secondary_entries)
        ]
        stats = rollup(entries)
        stats["bySize"] = {
            size: {
                **rollup([e for e, s in zip(entries, sizes) if s == size]),
                "secondarySpawn": rollup([e for e, s in zip(secondary_entries, sizes) if s == size]),
                "total": rollup([e for e, s in zip(combined_entries, sizes) if s == size]),
            }
            for size in ("Small", "Big")
            if any(s == size for s in sizes)
        }
        result[sectors[l["id"]]["name"]] = stats
    return result


def pool_size(pool_by_level, target_level):
    return len(pool_by_level.get(target_level - 1, [])) + len(pool_by_level.get(target_level, []))


def level_prob_for_tier(tier, max_loot_level):
    """The SAME per-level weighting (CHEST_LEVELS/CHEST_WEIGHTS) and
    maxLootLevel cap build_sector_profiles applies, but for exactly ONE
    tier rather than blended across a sector's whole wreckResGen mix -
    needed because compute_wreck_site_item_odds conditions on a SPECIFIC
    (size, tier) wreck variant having already been picked (it does its own
    weighting across variants afterward), not the sector's average tier."""
    capped = defaultdict(float)
    for lvl, w in zip(CHEST_LEVELS[tier], CHEST_WEIGHTS):
        capped[min(lvl, max_loot_level)] += w / 100
    return capped


def item_prob_given_level_dist(entry_level, pool_by_level, own_itl, opp_itl, opp_pool_by_level, level_prob):
    """P(an item with this lootLevel drops | a single crate is opened and
    its target level is drawn from level_prob) - the same 2-level-window
    formula compute_item_drop_odds uses inline for its own sector-blended
    level_prob, factored out so compute_wreck_site_item_odds can reuse it
    against an UNBLENDED, single-(size,tier)-variant level_prob instead. Uses
    the same real category_weight/opposing_category_win_share split as
    compute_item_drop_odds - the complete model for this crate type, not an
    approximation - see that function's and Finding 15's own notes."""
    total = 0.0
    for target_level in (entry_level, entry_level + 1):
        p_level = level_prob.get(target_level)
        if not p_level:
            continue
        n = pool_size(pool_by_level, target_level)
        if n == 0:
            continue
        own_weight = category_weight(own_itl, target_level, entry_level)
        share = opposing_category_win_share(own_weight, opp_itl, opp_pool_by_level, target_level)
        total += p_level * primary_drop_probability(target_level) * share / n
    return total


def size_of_resgen(resgen_id):
    """"ShipWreck_Small_1" -> "Small", "ShipWreck_Big_2" -> "Big" - the
    OTHER half of a wreck resGen id besides its tier (tier_of_resgen).
    Confirmed only two size rows exist in data.cdb's resGroup sheet
    (GShipWreck_{Small,Big}_lvl{0,1,2}, no third size)."""
    if "Small" in resgen_id:
        return "Small"
    if "Big" in resgen_id:
        return "Big"
    return None


def compute_wreck_site_item_odds(sheets, sectors, patch_by_level, bp_by_level, itl, cache):
    """Composes crateSpawn (how many crates a wreck has, driven by SIZE
    only) with itemDropOdds (which item a crate contains, driven by TIER
    only) into a single per-item, per-sector answer to "how many of this
    item do I expect to find at ONE wreck site" - previously these were
    two separate, uncomposed metrics; itemDropOdds alone answers "given a
    crate is already open in my hands", which understates a Big wreck
    site (avg ~3.6 crates) by roughly that same factor.

    "How many crates a wreck has" is the FULL total from
    compute_crate_spawn_stats/secondary_spawn_group_id: the debris field
    scattered at creation PLUS the second, independent generation pass a
    wreck's marked hull piece ALWAYS triggers via its own resGroupSpawn -
    confirmed unconditional, not tied to the player's actual dismantle
    action (see Finding 11, game_logic_notes.md) - composed here via
    combine_independent_counts, the same convolution compute_crate_spawn_
    stats' own "total" figure already uses, so a wreck's per-item odds
    match its own overall crate-count total rather than only the
    debris-field slice of it.

    A sector's wreckResGen list is a flat list of resGen ids (e.g.
    "ShipWreck_Small_1"), each ALREADY fixing both size and tier at once -
    repetition in the list is the weight (same mechanic
    build_sector_profiles uses for tier alone, generalized here to the
    full (size, tier) pair via the id itself). For each such variant:

        expectedPerWreck = E[TOTAL crate count | that variant's (size, tier)]
                            * P(item | that variant's TIER)

    exact via linearity of expectation - no need to enumerate the count
    distribution for this one. A sector's OWN expectedPerWreck is then the
    weight-average across its reachable variants (valid under the "this
    sector's next wreck is variant X with probability weight[X]" mixture
    interpretation).

        atLeastOnePct = 1 - sum_k P(TOTAL count=k | variant's (size,tier)) * (1-p)**k

    uses the full (debris + dismantle-bonus) count distribution - exact,
    not a Poisson approximation of expectedPerWreck."""
    resgen = {l["id"]: l for l in sheets["resGen"]["lines"]}
    resgroup = {l["id"]: l for l in sheets["resGroup"]["lines"]}

    sector_variant_weights = {}
    for l in sheets["sector"]["lines"]:
        wr = l.get("generation", {}).get("wreckResGen")
        if not wr:
            continue
        weights = defaultdict(float)
        for entry in wr:
            weights[entry["resGen"]] += 1 / len(wr)
        sector_variant_weights[l["id"]] = dict(weights)

    def total_crate_stats(resgen_id):
        """Debris field + hull-dismantle bonus, convolved - see this
        function's own docstring and Finding 11."""
        key = f"total|{resgen_id}"
        if key in cache:
            return cache[key]
        debris = crate_count_stats_raw(resgen, resgroup, resgen_id, cache)
        size = wreck_size_of_resgen(resgen_id)
        tier = tier_of_resgen(resgen_id)
        bonus = crate_count_stats_for_group(resgroup, secondary_spawn_group_id(size, tier), cache)
        total = combine_independent_counts(debris, bonus)
        cache[key] = total
        return total

    def compute_for_pool(pool_by_level, own_itl, opp_itl, opp_pool_by_level):
        rows = []
        for lx, entries in pool_by_level.items():
            for item_entry in entries:
                per_sector = {}
                for sector_id, variant_weights in sector_variant_weights.items():
                    max_loot_level = sectors[sector_id]["maxLootLevel"]
                    expected_total = 0.0
                    atleast_one_total = 0.0
                    for resgen_id, w in variant_weights.items():
                        tier = tier_of_resgen(resgen_id)
                        level_prob = level_prob_for_tier(tier, max_loot_level)
                        p = item_prob_given_level_dist(
                            lx, pool_by_level, own_itl, opp_itl, opp_pool_by_level, level_prob
                        )
                        if p <= 0:
                            continue
                        cstats = total_crate_stats(resgen_id)
                        expected_total += w * cstats["expectedCount"] * p
                        p_at_least_one_variant = 1 - sum(
                            frac * ((1 - p) ** int(k)) for k, frac in cstats["countDistribution"].items()
                        )
                        atleast_one_total += w * p_at_least_one_variant
                    if expected_total > 0:
                        per_sector[sectors[sector_id]["name"]] = (
                            sigfig(expected_total, 3),
                            sigfig(atleast_one_total * 100, 3),
                        )
                if not per_sector:
                    continue
                groups = defaultdict(list)
                for sector_name, key in per_sector.items():
                    groups[key].append(sector_name)
                grouped = [
                    {"expectedPerWreck": k[0], "atLeastOnePct": k[1], "sectors": sorted(v)}
                    for k, v in sorted(groups.items(), reverse=True)
                ]
                rows.append({
                    "name": item_entry["name"],
                    "level": lx,
                    "bestExpectedPerWreck": grouped[0]["expectedPerWreck"],
                    "groups": grouped,
                })
        rows.sort(key=lambda r: (r["level"], r["name"]))
        return rows

    return (
        compute_for_pool(patch_by_level, itl["patch"], itl["blueprint"], bp_by_level),
        compute_for_pool(bp_by_level, itl["blueprint"], itl["patch"], patch_by_level),
    )


def main():
    sheets = load_sheets()
    patch_by_level, bp_by_level = build_pools(sheets)
    sectors = build_sector_profiles(sheets)
    itl = load_category_itl(sheets)

    sector_level_prob = {
        s["name"]: {int(k): v for k, v in s["lootLevelProbability"].items()} for s in sectors.values()
    }
    patch_rows = compute_item_drop_odds(
        patch_by_level, itl["patch"], itl["blueprint"], bp_by_level, sector_level_prob
    )
    bp_rows = compute_item_drop_odds(
        bp_by_level, itl["blueprint"], itl["patch"], patch_by_level, sector_level_prob
    )

    # Shared across both consumers below (crateSpawn's own sector rollup AND
    # the per-item wreck-site composition) so each of the ~9 distinct wreck
    # resGen ids only gets Monte Carlo simulated once, not twice.
    crate_stats_cache = {}
    crate_spawn_stats = compute_crate_spawn_stats(sheets, sectors, crate_stats_cache)
    for sector_id, s in sectors.items():
        s["crateSpawn"] = crate_spawn_stats.get(s["name"])

    wreck_site_patch_rows, wreck_site_bp_rows = compute_wreck_site_item_odds(
        sheets, sectors, patch_by_level, bp_by_level, itl, crate_stats_cache
    )

    out = {
        "_meta": {
            "source": f"shipbuilder/pak_out/data.cdb (SpaceCraft), via {Path(__file__).name}",
            "description": (
                "Per-sector shipwreck rare-loot-crate analysis: reachable loot "
                "levels, and per-item (Patch/Blueprint) drop odds by sector, "
                "derived from sector.generation.wreckResGen + "
                "sector.props.maxLootLevel/lootMaterial + craft.lootLevel + "
                "craft.unlockType + item.lootLevel, cross-referenced against "
                "decompiled src/logic/Loot.hx via hlbc."
            ),
            "mechanism_notes": [
                "A rare loot crate (ShipWreck_LootChestRare_lvl{0,1,2}) rolls one "
                "of 4 levels weighted 40/30/20/10, banded by wreck tier: "
                "lvl0=[4,5,6,7], lvl1=[5,6,7,8], lvl2=[6,7,8,9], then capped by "
                "sector.props.maxLootLevel (target = min(rolled, cap)).",
                "Wreck tier (0/1/2) per crate spawn is picked uniformly at "
                "random from the sector's own generation.wreckResGen list "
                "(repetition in that list = weight).",
                "P(a primary Patch-or-Blueprint item drops at all) = "
                "clamp((target-2)/5, 0, 1), confirmed via raw HashLink opcodes "
                "at src/logic/Loot.hx:158-159.",
                "The eligible item pool for a crate targeting level L is NOT "
                "items with lootLevel==L exactly. src/logic/Loot.hx:461-478 "
                "(generatePrimaryItemCandidateBasic + generateAttemptDownUp) "
                "opens a 2-level window {L-1, L} and draws one uniform pick "
                "from the combined pool of both levels; only widens further "
                "(down then up, one level at a time) if that window is fully "
                "empty, which never happens for crate-relevant levels 4-9. So "
                "a lootLevel:3 recipe (e.g. Blueprint: Wire) IS reachable from "
                "a level-4 crate. Only lootLevel 2 and 10 items are truly "
                "unreachable from any wreck crate.",
                "Blueprint eligibility is gated on craft.unlockType==2 "
                "(Random_Blueprint), NOT just craft.lootLevel being set - "
                "confirmed via the dedicated Blueprint-candidate closure at "
                "src/logic/Loot.hx:426-445, which iterates Data.craft.all "
                "directly and explicitly skips unlockType!=2 rows. "
                "unlockType==1 (Unique_Blueprint) recipes have a real "
                "lootLevel in the sheet but are NOT reachable from this crate "
                "system at all - they come from a fixed, non-random source "
                "instead (quest/vendor/location). See game_logic_notes.md "
                "Finding 15 for the full unlockType enum legend and a "
                "confirmed example (Patch_SystemIntegration3, 'Blueprint: "
                "Module Patch: System III') that is excluded by this gate "
                "despite carrying a dev note suggesting eventual Random_"
                "Blueprint status.",
                "Category choice (Patch vs Blueprint) when a primary item "
                "drops is a weighted pick per src/logic/Loot.hx:295-317 "
                "(generatePrimaryItem) - weight = max(0, 10 - |target_level - "
                "itl| - 2*(target_level - item's own lootLevel)), where itl is "
                "a per-category constant from data.cdb's `constant` sheet: "
                "ToolModule=3, Patch=5, Blueprint=7, ShipDecorative=3 (see "
                "Finding 15). This dataset now computes the REAL Patch-vs-"
                "Blueprint weighted split (category_weight/opposing_category_"
                "win_share) instead of a flat 50/50. This IS the complete "
                "model for ShipWreck_LootChestRare_lvl{0,1,2} - confirmed "
                "directly from data.cdb's `loot` sheet, the rows those crates "
                "reference (ShipWreck_Loot_4..9) have primaryItemTypes==12 "
                "(Patch|Blueprint bits only; Tool/Module/ShipDecorative bits "
                "are off), so no third category ever competes for this crate "
                "type's primary-item slot even though Tool/Module/"
                "ShipDecorative are real, separate branches in the underlying "
                "code - matches direct play experience (drops observed are "
                "always Patch/Blueprint/materials, never a bare Tool or "
                "Module).",
                "In-game blueprint display name convention: "
                '"Blueprint: <output item display name>".',
                "sectors[*].crateSpawn = stats for a randomly-rolled wreck in "
                "this sector containing rare loot crates at all, as opposed to "
                "only ordinary scrap - a DIFFERENT question from "
                "lootLevelProbability (which level a crate is, GIVEN one "
                "exists). A single wreck can contain MORE THAN ONE crate - "
                "each JunkGroup invocation independently re-rolls its own "
                "RareLoot slot(s), and Big wrecks invoke JunkGroup 5-10 times "
                "vs Small's 1-2 - so this is a full count distribution, not "
                "just a spawn-or-not %: atLeastOne (P(>=1 crate)), "
                "expectedCount (mean crates per wreck), and countDistribution "
                "(P(exactly k crates) for k=0,1,2,...). Monte Carlo simulated "
                "from the actual resGroup generation tree "
                "(ShipWreck_{Small,Big}_lvl{0,1,2} -> JunkGroup[_BlackBox] -> "
                "RareLoot_lvl{0,1,2}'s 40:25 BasicLoot-vs-LootChestRare "
                "override weight, identical at every tier), per the "
                "decompiled logic.gen.PlanetRes.generateGroup/getResGroupCount "
                "algorithm - see game_logic_notes.md Finding 8. "
                "Tier-independent; driven almost entirely by each sector's "
                "Small:Big wreck-type mix (Small wrecks average <1 crate, Big "
                "wrecks average ~3-4 and can have several).",
                "sectors[*].crateSpawn.bySize.{Small,Big} = the same "
                "atLeastOne/expectedCount/countDistribution stats, split by "
                "wreck SIZE instead of blended across the sector's whole "
                "wreckResGen mix (a key is absent if the sector has no "
                "variant of that size at all). The blended top-level figures "
                "answer 'this sector's wrecks, on average' - not 'the wreck "
                "I'm looking at right now', since Big alone already runs ~4x "
                "Small's own expected count. Added after CraftMap's live "
                "wreck-tracking data (tools/audit_wreck_crate_rates.py in "
                "the sibling Craftmap repo) showed real per-wreck crate "
                "counts running well above the blended figure once split by "
                "the hull actually observed (BigPiece1/2+SmallPiece1/2 debris "
                "vs a single plain hull piece) - the size blend explained "
                "SOME of that gap, but not all of it; see secondarySpawn "
                "below and game_logic_notes.md Findings 11-12 for the rest.",
                "sectors[*].crateSpawn.bySize.{Small,Big}.secondarySpawn/"
                "total = a SECOND, independent loot-generation pass a "
                "wreck's marked hull piece (a Small wreck's single hull "
                "piece, or only BigPiece1 - never BigPiece2/SmallPiece1/"
                "SmallPiece2 - for a Big wreck) ALWAYS triggers via that "
                "resource's own props.resGroupSpawn in data.cdb - "
                "confirmed (Finding 12, including a live before/after "
                "test) to fire unconditionally at world-generation, NOT "
                "when the player mines/dismantles that piece, despite the "
                "underlying resGroup's own 'DismantledJunkGroup' name (a "
                "wholly separate, unrelated mechanism actually triggers on "
                "the player's real dismantle action - see Finding 12) - "
                "completely separate from the debris-field generation tree "
                "the bare bySize figures above cover. total is debris + "
                "secondarySpawn (a convolution of the two independent "
                "count distributions) - the real total for that wreck, not "
                "an alternate scenario. See game_logic_notes.md Finding 11 "
                "for the full derivation and live-data verification (within "
                "~1-3% of observed for Big wrecks; Small's own total lands "
                "close on the mean but its full count-distribution shape "
                "does not match this or any tested model - Finding 12, "
                "still open).",
                "itemDropOdds[*].groups[*].pct is CONDITIONAL ON a crate "
                "already being open (P(item | one crate opened)) - it does "
                "NOT account for how many crates a wreck actually has, which "
                "varies 4x between Small and Big (see crateSpawn above). "
                "wreckSiteItemOdds composes the two: expectedPerWreck = "
                "E[TOTAL crate count | wreck's SIZE] * P(item | wreck's "
                "TIER), exact via linearity of expectation (a wreck's resGen "
                "id fixes both size and tier at once, so this is computed "
                "once per (size,tier) VARIANT actually reachable in a "
                "sector's own wreckResGen list, then weight-averaged the "
                "same way crateSpawn already weights tiers). 'TOTAL crate "
                "count' is debris field + the hull piece's own secondary "
                "generation pass (see game_logic_notes.md Findings 11-12 "
                "and crateSpawn.bySize.*.total above) - a wreck's per-item "
                "odds match the same total the sector-level crate stats "
                "do, not just its debris field's own slice of it. "
                "atLeastOnePct is the exact P(this item drops at least once "
                "across the WHOLE wreck site), using the real (convolved) "
                "crate-count distribution rather than a Poisson "
                "approximation of expectedPerWreck.",
            ],
        },
        "patchPoolByLevel": {str(k): v for k, v in sorted(patch_by_level.items())},
        "blueprintPoolByLevel": {str(k): v for k, v in sorted(bp_by_level.items())},
        "sectors": sectors,
        "itemDropOdds": {"patches": patch_rows, "blueprints": bp_rows},
        "wreckSiteItemOdds": {"patches": wreck_site_patch_rows, "blueprints": wreck_site_bp_rows},
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(sectors)} sectors, {len(patch_rows)} patch rows, "
          f"{len(bp_rows)} blueprint rows -> {OUT_PATH}")


if __name__ == "__main__":
    main()
