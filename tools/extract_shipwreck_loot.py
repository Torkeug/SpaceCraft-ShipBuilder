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


def build_pools(sheets):
    """Returns (patch_by_level, blueprint_by_level): {lootLevel: [{id, name}]}."""
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
        out = l["outputs"][0]["item"] if l.get("outputs") else None
        item_name = items.get(out, {}).get("name", out)
        bp_by_level[l["lootLevel"]].append(
            {"id": l["id"], "name": f"Blueprint: {item_name}", "output_item": out}
        )
    return patch_by_level, bp_by_level


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


def compute_item_drop_odds(pool_by_level, sector_level_prob):
    """For every item in pool_by_level, compute its drop probability per
    sector, using the CORRECTED 2-level search window (Finding 6):
    a crate targeting (capped) level L pools every candidate with
    lootLevel in {L-1, L} and draws one uniformly - confirmed via raw
    opcodes at src/logic/Loot.hx:461-478. So an item with lootLevel Lx is
    reachable from a crate whose target level L is either Lx or Lx+1.

    The Patch/Blueprint 50/50 split (when both categories have an eligible
    candidate at a level) approximates a small per-candidate weighting
    formula (src/logic/Loot.hx:295-317) not fully traced - see
    game_logic_notes.md Finding 5.
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
                    total += p_level * primary_drop_probability(target_level) * 0.5 / n
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
    0.1) - a uniform int in the wider [2*min, 3*max] range instead."""
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


def compute_crate_spawn_stats(sheets, sectors, cache):
    """{sector_name: {atLeastOne, expectedCount, countDistribution}} for a
    randomly-rolled wreck in that sector, Monte Carlo simulated from the
    actual generation tree - see game_logic_notes.md Finding 8.
    Tier-independent (the crate-vs-junk override weight is identical at
    every tier - only Small vs Big wreck size moves these numbers), unlike
    lootLevelProbability. A single wreck can contain more than one crate
    (each JunkGroup invocation independently re-rolls its own RareLoot
    slot(s), and Big wrecks invoke JunkGroup many times) - countDistribution
    captures that instead of just collapsing to a single spawn-or-not %."""
    resgen = {l["id"]: l for l in sheets["resGen"]["lines"]}
    resgroup = {l["id"]: l for l in sheets["resGroup"]["lines"]}

    result = {}
    for l in sheets["sector"]["lines"]:
        wr = l.get("generation", {}).get("wreckResGen")
        if not wr:
            continue
        entries = [crate_count_stats_raw(resgen, resgroup, entry["resGen"], cache) for entry in wr]
        n = len(entries)
        merged_dist = defaultdict(float)
        for e in entries:
            for k, v in e["countDistribution"].items():
                merged_dist[k] += v / n
        result[sectors[l["id"]]["name"]] = {
            "atLeastOne": round(sum(e["atLeastOne"] for e in entries) / n, 4),
            "expectedCount": round(sum(e["expectedCount"] for e in entries) / n, 3),
            "countDistribution": {
                str(k): round(v, 4) for k, v in sorted(merged_dist.items(), key=lambda kv: kv[0])
            },
        }
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


def item_prob_given_level_dist(entry_level, pool_by_level, level_prob):
    """P(an item with this lootLevel drops | a single crate is opened and
    its target level is drawn from level_prob) - the same 2-level-window
    formula compute_item_drop_odds uses inline for its own sector-blended
    level_prob, factored out so compute_wreck_site_item_odds can reuse it
    against an UNBLENDED, single-(size,tier)-variant level_prob instead."""
    total = 0.0
    for target_level in (entry_level, entry_level + 1):
        p_level = level_prob.get(target_level)
        if not p_level:
            continue
        n = pool_size(pool_by_level, target_level)
        if n == 0:
            continue
        total += p_level * primary_drop_probability(target_level) * 0.5 / n
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


def compute_wreck_site_item_odds(sheets, sectors, patch_by_level, bp_by_level, cache):
    """Composes crateSpawn (how many crates a wreck has, driven by SIZE
    only) with itemDropOdds (which item a crate contains, driven by TIER
    only) into a single per-item, per-sector answer to "how many of this
    item do I expect to find at ONE wreck site" - previously these were
    two separate, uncomposed metrics; itemDropOdds alone answers "given a
    crate is already open in my hands", which understates a Big wreck
    site (avg ~3.6 crates) by roughly that same factor.

    A sector's wreckResGen list is a flat list of resGen ids (e.g.
    "ShipWreck_Small_1"), each ALREADY fixing both size and tier at once -
    repetition in the list is the weight (same mechanic
    build_sector_profiles uses for tier alone, generalized here to the
    full (size, tier) pair via the id itself). For each such variant:

        expectedPerWreck = E[crate count | that variant's SIZE]
                            * P(item | that variant's TIER)

    exact via linearity of expectation - no need to enumerate the count
    distribution for this one. A sector's OWN expectedPerWreck is then the
    weight-average across its reachable variants (valid under the "this
    sector's next wreck is variant X with probability weight[X]" mixture
    interpretation).

        atLeastOnePct = 1 - sum_k P(count=k | variant's SIZE) * (1-p)**k

    uses the full count distribution (already computed for crateSpawn) -
    exact, not a Poisson approximation of expectedPerWreck."""
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

    def compute_for_pool(pool_by_level):
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
                        p = item_prob_given_level_dist(lx, pool_by_level, level_prob)
                        if p <= 0:
                            continue
                        cstats = crate_count_stats_raw(resgen, resgroup, resgen_id, cache)
                        expected_total += w * cstats["expectedCount"] * p
                        p_at_least_one_variant = 1 - sum(
                            frac * ((1 - p) ** k) for k, frac in cstats["countDistribution"].items()
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

    return compute_for_pool(patch_by_level), compute_for_pool(bp_by_level)


def main():
    sheets = load_sheets()
    patch_by_level, bp_by_level = build_pools(sheets)
    sectors = build_sector_profiles(sheets)

    sector_level_prob = {
        s["name"]: {int(k): v for k, v in s["lootLevelProbability"].items()} for s in sectors.values()
    }
    patch_rows = compute_item_drop_odds(patch_by_level, sector_level_prob)
    bp_rows = compute_item_drop_odds(bp_by_level, sector_level_prob)

    # Shared across both consumers below (crateSpawn's own sector rollup AND
    # the per-item wreck-site composition) so each of the ~9 distinct wreck
    # resGen ids only gets Monte Carlo simulated once, not twice.
    crate_stats_cache = {}
    crate_spawn_stats = compute_crate_spawn_stats(sheets, sectors, crate_stats_cache)
    for sector_id, s in sectors.items():
        s["crateSpawn"] = crate_spawn_stats.get(s["name"])

    wreck_site_patch_rows, wreck_site_bp_rows = compute_wreck_site_item_odds(
        sheets, sectors, patch_by_level, bp_by_level, crate_stats_cache
    )

    out = {
        "_meta": {
            "source": f"shipbuilder/pak_out/data.cdb (SpaceCraft), via {Path(__file__).name}",
            "description": (
                "Per-sector shipwreck rare-loot-crate analysis: reachable loot "
                "levels, and per-item (Patch/Blueprint) drop odds by sector, "
                "derived from sector.generation.wreckResGen + "
                "sector.props.maxLootLevel/lootMaterial + craft.lootLevel + "
                "item.lootLevel, cross-referenced against decompiled "
                "src/logic/Loot.hx via hlbc."
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
                "Category choice (Patch vs Blueprint) when a primary item "
                "drops is a weighted pick per src/logic/Loot.hx:295-317 "
                "depending on a per-candidate 'itl' reference value not fully "
                "traced; this dataset approximates it as 50/50 when both "
                "categories have eligible candidates.",
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
                "itemDropOdds[*].groups[*].pct is CONDITIONAL ON a crate "
                "already being open (P(item | one crate opened)) - it does "
                "NOT account for how many crates a wreck actually has, which "
                "varies 4x between Small and Big (see crateSpawn above). "
                "wreckSiteItemOdds composes the two: expectedPerWreck = "
                "E[crate count | wreck's SIZE] * P(item | wreck's TIER), "
                "exact via linearity of expectation (a wreck's resGen id "
                "fixes both size and tier at once, so this is computed once "
                "per (size,tier) VARIANT actually reachable in a sector's "
                "own wreckResGen list, then weight-averaged the same way "
                "crateSpawn already weights tiers). atLeastOnePct is the "
                "exact P(this item drops at least once across the WHOLE "
                "wreck site), using the real crate-count distribution rather "
                "than a Poisson approximation of expectedPerWreck.",
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
