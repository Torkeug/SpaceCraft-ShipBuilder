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
import re
from collections import defaultdict
from math import floor, log10
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CDB_PATH = REPO_ROOT / "shipbuilder" / "pak_out" / "data.cdb"
OUT_PATH = REPO_ROOT / "Craftmap" / "game_data_extract" / "shipwreck_loot.json"

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


def main():
    sheets = load_sheets()
    patch_by_level, bp_by_level = build_pools(sheets)
    sectors = build_sector_profiles(sheets)

    sector_level_prob = {
        s["name"]: {int(k): v for k, v in s["lootLevelProbability"].items()} for s in sectors.values()
    }
    patch_rows = compute_item_drop_odds(patch_by_level, sector_level_prob)
    bp_rows = compute_item_drop_odds(bp_by_level, sector_level_prob)

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
            ],
        },
        "patchPoolByLevel": {str(k): v for k, v in sorted(patch_by_level.items())},
        "blueprintPoolByLevel": {str(k): v for k, v in sorted(bp_by_level.items())},
        "sectors": sectors,
        "itemDropOdds": {"patches": patch_rows, "blueprints": bp_rows},
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(sectors)} sectors, {len(patch_rows)} patch rows, "
          f"{len(bp_rows)} blueprint rows -> {OUT_PATH}")


if __name__ == "__main__":
    main()
