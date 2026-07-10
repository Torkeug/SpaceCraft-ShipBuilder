"""
Extract crafting-recipe-related sheets from the game's own data.cdb
(shipbuilder/pak_out/data.cdb) into standalone JSON files under
craftmap/game_data_extract/, for manual review/merge into CraftMap's
hand-maintained resources.db. Does NOT touch resources.db.

Usage:
    python tools/extract_craft_data.py
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CDB_PATH = REPO_ROOT / "shipbuilder" / "pak_out" / "data.cdb"
OUT_DIR = REPO_ROOT / "craftmap" / "game_data_extract"


def load_sheets():
    with open(CDB_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {s["name"]: s for s in data["sheets"]}


def main():
    sheets = load_sheets()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Full recipe list, as authored in the game data.
    craft_lines = sheets["craft"]["lines"]
    (OUT_DIR / "craft_recipes.json").write_text(
        json.dumps(craft_lines, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Item id -> display name/type/price/etc, needed to make recipe item ids readable.
    items = {}
    for line in sheets["item"]["lines"]:
        price_attr = next(
            (a["value"] for a in line.get("attributes", []) if a.get("attr") == "Price"),
            None,
        )
        items[line["id"]] = {
            "name": line.get("name"),
            "type": line.get("type"),
            "guid": line.get("guid"),
            "price": price_attr,
            "desc": line.get("desc"),
        }
    (OUT_DIR / "items.json").write_text(
        json.dumps(items, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    # Item type hierarchy (id -> name/parent), for grouping/categorization.
    item_types = {
        line["id"]: {"name": line.get("name"), "parent": line.get("parent")}
        for line in sheets["itemType"]["lines"]
    }
    (OUT_DIR / "item_types.json").write_text(
        json.dumps(item_types, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    # Item tags: craft station display metadata (label, craft time, color, etc).
    item_tags = {
        line["id"]: line.get("props", {}) for line in sheets["itemTag"]["lines"]
    }
    (OUT_DIR / "item_tags.json").write_text(
        json.dumps(item_tags, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )

    # Per-station crafting economy constants (power cost, price ratios, etc).
    craft_values = sheets["craftValues"]["lines"]
    (OUT_DIR / "craft_values.json").write_text(
        json.dumps(craft_values, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Wrote {len(craft_lines)} recipes, {len(items)} items, "
          f"{len(item_types)} item types, {len(item_tags)} item tags, "
          f"{len(craft_values)} craft-value entries to {OUT_DIR}")


if __name__ == "__main__":
    main()
