"""Extract base-building data (BaseBuilding_* items) from data.cdb.

The 'FP' (Footprint) budget system asked about in the shipbuilder is a
base-building-only mechanic -- it has nothing to do with ship parts (see
CLAUDE.md's Base Builder section). This pulls every base-building item's
id/name/category/price/description and flattened attribute dict (BuildPointsCost,
EnergyOffer/EnergyDemand, storage caps, etc.) out of data.cdb into
basebuilder/base_buildings_data.json for basebuilder/optimize.py to consume.

Command centers (type BaseBuilding_Command) are split into their own
"command_centers" list since they set the FP/DP budget rather than spending
it, while everything else lands in "buildings".

Usage: python tools/extract_base_buildings.py
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CDB_PATH = REPO_ROOT / "pak_out" / "data.cdb"
ATTRS_PATH = REPO_ROOT / "shipbuilder" / "ship_attributes.json"
OUT_PATH = REPO_ROOT / "basebuilder" / "base_buildings_data.json"


def flatten_attrs(row):
    return {a["attr"]: a["value"] for a in row.get("attributes", [])}


def tags_of(row):
    props = row.get("props", {})
    tags = {t.get("tag") for t in props.get("tags", [])}
    if props.get("tag"):
        tags.add(props["tag"])
    return tags


# Player-confirmed, not derivable from data.cdb itself: Corporation Command
# Center only works on a corp-owned base and is capped at 1 per *corporation*
# (not per base) -- stricter than the generic 'unique' (1-per-base) tag, and
# a constraint a single-base optimizer has no way to track across bases.
CORP_ONLY_IDS = {"B_ControlBase_Corpo1"}


def is_implemented(row):
    # Finding 22 (game_logic_notes.md): confirmed in-game (a player reported
    # "Command Relay doesn't exist") that a 'PH' id suffix marks a data.cdb
    # entry that was never actually shipped. '[NOT IMPL]'/'[DEPRECATED]' in
    # desc are the same signal from the dev's own descriptions.
    desc = row.get("desc") or ""
    if row["id"].endswith("PH"):
        return False
    if "[NOT IMPL]" in desc or "[DEPRECATED]" in desc:
        return False
    return True


def building_entry(row):
    tags = tags_of(row)
    return {
        "id": row["id"],
        "name": row.get("name", row["id"]),
        "category": row["type"][len("BaseBuilding_"):] if row["type"] != "BaseBuilding" else "Misc",
        "price": row.get("price"),
        "desc": row.get("desc"),
        "attrs": flatten_attrs(row),
        # Finding 22 (game_logic_notes.md): 'UniqueBuilding' (max 1 per base, enforced
        # by ui.win.b.SelectDeployBuilding hiding the option) and 'MainBaseBuilding'
        # (the sole ControlBaseRadius/placement anchor, ent.SpaceBase.get_mainBuilding)
        # are real per-building tags in data.cdb, not just a command-center thing.
        "unique": "UniqueBuilding" in tags,
        "main": "MainBaseBuilding" in tags,
        "implemented": is_implemented(row),
        "corp_only": row["id"] in CORP_ONLY_IDS,
    }


def main():
    cdb = json.loads(CDB_PATH.read_text(encoding="utf-8"))
    item_sheet = next(s for s in cdb["sheets"] if s["name"] == "item")
    items = item_sheet["lines"]
    attr_glossary_full = json.loads(ATTRS_PATH.read_text(encoding="utf-8"))

    bb_items = [r for r in items if str(r.get("type", "")).startswith("BaseBuilding")]

    command_centers = [building_entry(r) for r in bb_items if r["type"] == "BaseBuilding_Command"]
    buildings = [building_entry(r) for r in bb_items if r["type"] != "BaseBuilding_Command"]

    used_attrs = set()
    for entry in command_centers + buildings:
        used_attrs.update(entry["attrs"].keys())
    attribute_glossary = {k: attr_glossary_full.get(k) for k in sorted(used_attrs)}

    out = {
        "_source": "pak_out/data.cdb 'item' sheet, type startswith BaseBuilding_ or == BaseBuilding",
        "attribute_glossary": attribute_glossary,
        "command_centers": command_centers,
        "buildings": buildings,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {len(command_centers)} command centers, {len(buildings)} buildings -> {OUT_PATH}")


if __name__ == "__main__":
    main()
