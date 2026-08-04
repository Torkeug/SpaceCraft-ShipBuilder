"""General-purpose base-building optimizer.

Picks how many of each base building (including which Command buildings)
to construct to maximize or minimize a chosen attribute, subject to
budget/min/max constraints on any other attribute -- e.g. "maximize
NetPower" or "minimize FP spent while keeping NetPower >= 0 and
OutputStorage >= 500".

Solves an integer linear program (unbounded knapsack: any number of copies
of a non-unique building may be built) via scipy.optimize.milp. Requires
scipy (`pip install scipy`).

Game-rule constraints baked in (see Finding 22 in tools/game_logic_notes.md
for the decompiled source of each):
  - FP is a hard cap, not a soft one: total BuildPointsCost across every
    built building can never exceed total MaxBuildPoints across every
    built building. Same for DecoPointsCost <= MaxDecoPoints. Both are
    added automatically as constraints on every run -- you don't pick a
    single command center's budget, the optimizer picks which Command
    building(s) to build (each capped at 1, see below) as part of the
    solution, same as any other building.
  - MaxBuildPoints/MaxDecoPoints STACK additively across every Command
    building you build on the same base (e.g. base + tower at once = both
    caps summed) -- this is real game behavior, not a modeling choice.
  - Buildings tagged "unique" in base_buildings_data.json (all 4 Command
    buildings, plus the Laboratory) are capped at 1 by default, since the
    game only lets you build one of each per base.
  - NOT modeled: placement (ControlBaseRadius -- only the MainBaseBuilding-
    tagged center, B_ControlBase, actually sets this) and power grid
    topology (SpaceBaseNetworks groups buildings into separate networks;
    this tool's NetPower is a base-wide upper bound, not a guarantee power
    actually reaches a given building).
  - data.cdb ships several buildings that were never actually released
    (confirmed in-game for "Command Relay"; the same 'PH' id-suffix and
    '[NOT IMPL]'/'[DEPRECATED]' desc markers flag 13 of the 47 entries,
    including both "Advanced Fuel Power Plant" and "Fusion Plant" -- the
    two best power sources by the numbers, which is why they matter).
    These are excluded by default (`implemented: false` in the data);
    pass --include-unimplemented to consider them anyway.
  - Corporation Command Center (`B_ControlBase_Corpo1`) is player-confirmed
    corp-base-only and capped at 1 per *corporation* (not per base, unlike
    every other Command building) -- a scope this single-base tool can't
    track across your other bases. Left available by default (flagged `[C]`
    in --list) since it's a real, usable building; exclude it yourself with
    `--exclude B_ControlBase_Corpo1` for a personal-base-only run, or if
    your corporation's one allowance is already spent elsewhere.
  - A corp permit (Industrial Centers) grants a flat +50 FP CorpoBaseFootprint
    bonus on top of built Command buildings -- pass --corp-fp/-dp for this
    rather than modeling it as a building (it isn't one). See Finding 23.
  - NetEthanol (derived, like NetPower): Ethanol produced/sec minus consumed/sec
    -- B_Chemical (Chemical Factory, Sour Pulp -> Ethanol) vs. B_Generator
    (Fuel Power Plant burning it). Hardcoded from decompiled craft-time/
    fuel-burn formulas, not derivable from base_buildings_data.json's attrs
    alone (recipes are a separate data.cdb layer) -- see Finding 23. NOT
    modeled: whether your farms' Sour Pulp *output* rate can keep up with
    the Chemical Factories' *input* draw, and Factory-category buildings'
    dynamic per-craft power draw (data.cdb's craftValues.PowerBaseCost,
    e.g. 5 for Workshop_Chemical) isn't in any EnergyDemand attribute, so
    NetPower undercounts base power draw once crafting buildings are added.

Data comes from base_buildings_data.json (see tools/extract_base_buildings.py
in the repo root, which regenerates it from pak_out/data.cdb).

Examples:
    # List everything available, with units
    python optimize.py --list

    # Maximize net power (EnergyOffer - EnergyDemand); optimizer decides
    # which Command building(s) to build for FP/DP budget too
    python optimize.py --maximize NetPower

    # Same, but assume you've already committed to just the starter base
    # (no Command Tower/Relay) -- pins B_ControlBase, forbids the others
    python optimize.py --maximize NetPower --count B_ControlBase=1 \\
        --max-count B_ControlBase2=0 --max-count B_ControlBase3PH=0 \\
        --max-count B_ControlBase_Corpo1=0

    # Minimize FP spent while covering >=500 OutputStorage and staying
    # power-neutral, restricted to Crafting/Storage buildings
    python optimize.py --minimize BuildPointsCost \\
        --min OutputStorage=500 --min NetPower=0 --category Crafting --category Storage

    # Force at least 1 shipyard, cap solar panels at 10, exclude deco fluff
    python optimize.py --maximize NetPower \\
        --min-count B_Shipyard=1 --max-count B_SolarPanel=10 --exclude-category Deco

    # Fixed farms/cisterns/warehouses/transmitters, +50 FP corp bonus, rest is
    # ONLY fuel generators plus exactly enough Chemical Factories to keep them
    # fed (--only whitelists the free choices; --count-pinned ids are always
    # included on top of --only, so they don't need to be listed twice)
    python optimize.py --maximize NetPower --corp-fp 50 \\
        --only B_ControlBase --only B_ControlBase2 --only B_Generator --only B_Chemical \\
        --count B_Farm=4 --count B_Cistern=2 --count B_Warehouse=4 --count B_PowerTransmitter=4 \\
        --min NetEthanol=0
"""
import argparse
import json
import sys
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "base_buildings_data.json"
DEFAULT_UPPER_BOUND = 999
# Finding 22: real game rule, not a user choice -- consumed <= supplied, where
# "supplied" is summed across whichever Command buildings get built.
CAPACITY_PAIRS = [("BuildPointsCost", "MaxBuildPoints"), ("DecoPointsCost", "MaxDecoPoints")]

# Finding 23 (game_logic_notes.md): Ethanol/sec balance, per building, derived from
# ent.b.Generator.getProduceTime (FuelConversion/FuelConsumption) and
# ent.b.Factory.getProduceTime (ProduceTimeFactor * itemTag.autoCraftTime), cross-checked
# against the Chemical_Ethanol craft row and Ethanol's compatibleBuildings FuelConversion.
# Not derivable generically from base_buildings_data.json's attrs (recipes/fuel are a
# separate data.cdb layer, 'craft'/'itemTag'/item.props.compatibleBuildings) -- hardcoded
# here rather than building a full recipe-throughput engine for one fuel chain.
ETHANOL_RATE_PER_SEC = {
    "B_Generator": -1 / 200,   # burns 1 Ethanol per 200s cycle (FuelConversion 15000 / FuelConsumption 75)
    "B_Chemical": 6 / 180,     # Chemical_Ethanol: 1 SourPulp -> 6 Ethanol per 180s craft (Workshop_Chemical autoCraftTime)
}


def load_data():
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def derive_attrs(building_id, attrs):
    """Add composite attributes on top of the raw data.cdb ones."""
    out = dict(attrs)
    out["NetPower"] = attrs.get("EnergyOffer", 0) - attrs.get("EnergyDemand", 0)
    out["NetEthanol"] = ETHANOL_RATE_PER_SEC.get(building_id, 0)
    return out


def parse_kv(s, cast=float):
    if "=" not in s:
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {s!r}")
    k, v = s.split("=", 1)
    return k, cast(v)


def print_glossary(data):
    print("Attribute glossary (+ NetPower = EnergyOffer - EnergyDemand, derived):")
    for k, v in sorted(data["attribute_glossary"].items()):
        label = f"{v['name']} ({v['unit']})" if v and v.get("unit") else (v["name"] if v else k)
        print(f"  {k:24s} {label}")


def print_listing(data):
    all_buildings = data["command_centers"] + data["buildings"]
    print("Buildings ([U]=1-per-base cap, [!]=unimplemented -- excluded by default, "
          "[C]=corp-only & 1-per-corporation, not 1-per-base -- included by default, filter manually if needed):")
    for b in sorted(all_buildings, key=lambda b: (b["category"], b["id"])):
        attrs = derive_attrs(b["id"], b["attrs"])
        flag = ("U" if b["unique"] else " ") + ("!" if not b["implemented"] else " ") + ("C" if b["corp_only"] else " ")
        print(f"  [{flag}] [{b['category']:10s}] {b['id']:24s} {b['name']:28s} {attrs}")


def build_and_solve(args, data):
    from scipy.optimize import LinearConstraint, milp, Bounds

    all_buildings = data["command_centers"] + data["buildings"]
    by_id = {b["id"]: b for b in all_buildings}

    candidates = all_buildings
    if not args.include_unimplemented:
        candidates = [b for b in candidates if b["implemented"]]
    if args.category:
        candidates = [b for b in candidates if b["category"] in args.category]
    if args.exclude_category:
        candidates = [b for b in candidates if b["category"] not in args.exclude_category]
    if args.only:
        candidates = [b for b in candidates if b["id"] in args.only]
    if args.exclude:
        candidates = [b for b in candidates if b["id"] not in args.exclude]

    # An id explicitly pinned via --count/--min-count/--max-count is included
    # regardless of the filters above -- naming it is a stronger, unambiguous
    # instruction than a category/only/exclude filter that wasn't written with
    # that specific id in mind.
    pinned_ids = {bid for bid, _ in (args.count or []) + (args.min_count or []) + (args.max_count or [])}
    have_ids = {b["id"] for b in candidates}
    for bid in pinned_ids - have_ids:
        if bid not in by_id:
            raise SystemExit(f"Unknown building id {bid!r} in --count/--min-count/--max-count.")
        candidates.append(by_id[bid])

    if not candidates:
        raise SystemExit("No buildings left to consider after filtering.")

    for b in candidates:
        b["_attrs"] = derive_attrs(b["id"], b["attrs"])

    n = len(candidates)
    ids = [b["id"] for b in candidates]
    if len(set(ids)) != n:
        raise SystemExit("Duplicate building id in candidate set (bug in data or filters).")

    # Objective
    obj_attr = args.maximize or args.minimize
    sense = 1 if args.maximize else -1  # milp minimizes; negate to maximize
    c = [-sense * b["_attrs"].get(obj_attr, 0) for b in candidates]

    constraints = []
    summary_attrs = {obj_attr}

    def add_constraint(attr, lb, ub, row=None):
        row = row if row is not None else [b["_attrs"].get(attr, 0) for b in candidates]
        if all(v == 0 for v in row):
            return False  # attribute doesn't apply to any candidate; skip
        lo = -float("inf") if lb is None else lb
        hi = float("inf") if ub is None else ub
        constraints.append(LinearConstraint([row], lo, hi))
        summary_attrs.add(attr)
        return True

    # Finding 22: FP/DP are a hard "consumed <= supplied" cap, both sides
    # summed across whichever buildings get built -- not an external budget.
    # Finding 23: a corp permit (Industrial Centers) can add a flat, building-independent
    # CorpoBaseFootprint bonus to the FP side -- --corp-fp/-dp fold that in as a constant.
    extra_supply = {"BuildPointsCost": args.corp_fp or 0, "DecoPointsCost": args.corp_dp or 0}
    capacity_constrained = set()
    for cost_attr, cap_attr in CAPACITY_PAIRS:
        row = [b["_attrs"].get(cost_attr, 0) - b["_attrs"].get(cap_attr, 0) for b in candidates]
        if add_constraint(f"{cost_attr}<={cap_attr}", None, extra_supply[cost_attr], row=row):
            capacity_constrained.add(cost_attr)
            capacity_constrained.add(cap_attr)
            summary_attrs.discard(f"{cost_attr}<={cap_attr}")
            summary_attrs.update([cost_attr, cap_attr])

    for attr, val in (args.budget or []):
        add_constraint(attr, None, val)
    for attr, val in (args.min or []):
        add_constraint(attr, val, None)
    for attr, val in (args.max or []):
        add_constraint(attr, None, val)

    lb = [0] * n
    # Finding 22: "unique" buildings (all 4 Command buildings + the Laboratory)
    # cap at 1 per base -- enforced client-side but the effective limit either way.
    ub = [1 if b["unique"] else DEFAULT_UPPER_BOUND for b in candidates]
    for bid, count in (args.min_count or []):
        lb[ids.index(bid)] = int(count)
    for bid, count in (args.max_count or []):
        ub[ids.index(bid)] = int(count)
    for bid, count in (args.count or []):
        i = ids.index(bid)
        lb[i] = ub[i] = int(count)

    res = milp(
        c=c,
        integrality=[1] * n,
        bounds=Bounds(lb, ub),
        constraints=constraints,
    )

    if not res.success:
        raise SystemExit(f"No feasible solution found ({res.message}).")

    counts = [round(x) for x in res.x]
    selected = [(b, cnt) for b, cnt in zip(candidates, counts) if cnt > 0]

    print(f"Objective: {'maximize' if args.maximize else 'minimize'} {obj_attr}")
    print()
    if not selected:
        print("(nothing built -- empty selection is optimal under these constraints)")
    for b, cnt in sorted(selected, key=lambda t: -t[1]):
        print(f"  x{cnt:<4d} {b['name']} ({b['id']})")

    cap_to_cost = {cap: cost for cost, cap in CAPACITY_PAIRS}
    print()
    print("Totals:")
    for attr in sorted(summary_attrs):
        total = sum(cnt * b["_attrs"].get(attr, 0) for b, cnt in selected)
        note = ""
        if attr in capacity_constrained:
            bonus = extra_supply.get(cap_to_cost.get(attr, ""), 0)
            if bonus:
                total += bonus
                note = f" (incl. +{bonus:g} corp bonus)"
            else:
                note = " (capacity-constrained, see FP/DP rule above)"
        print(f"  {attr:20s} {total:g}{note}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--list", action="store_true", help="print all buildings and exit")
    p.add_argument("--list-attrs", action="store_true", help="print the attribute glossary and exit")
    p.add_argument("--include-unimplemented", action="store_true",
                    help="also consider buildings never actually shipped in-game (see --list's [!] flag)")
    p.add_argument("--corp-fp", type=float, default=0, metavar="N",
                    help="flat FP bonus added on top of built Command buildings (e.g. 50 from the "
                         "'Industrial Centers' corp permit's CorpoBaseFootprint) -- see Finding 23")
    p.add_argument("--corp-dp", type=float, default=0, metavar="N", help="same as --corp-fp for DP")
    obj = p.add_mutually_exclusive_group()
    obj.add_argument("--maximize", metavar="ATTR")
    obj.add_argument("--minimize", metavar="ATTR")
    p.add_argument("--budget", action="append", type=lambda s: parse_kv(s), metavar="ATTR=VALUE",
                    help="cap total ATTR at VALUE (shorthand for --max)")
    p.add_argument("--min", action="append", type=lambda s: parse_kv(s), metavar="ATTR=VALUE",
                    help="require total ATTR >= VALUE")
    p.add_argument("--max", action="append", type=lambda s: parse_kv(s), metavar="ATTR=VALUE",
                    help="require total ATTR <= VALUE")
    p.add_argument("--category", action="append", metavar="CATEGORY", help="restrict to this category (repeatable)")
    p.add_argument("--exclude-category", action="append", metavar="CATEGORY")
    p.add_argument("--exclude", action="append", metavar="ID", help="exclude a specific building id")
    p.add_argument("--only", action="append", metavar="ID",
                    help="restrict to exactly these building ids (repeatable) -- e.g. to say 'nothing else "
                         "is a free choice' alongside --count-pinned ids, which are always included regardless")
    p.add_argument("--count", action="append", type=lambda s: parse_kv(s, int), metavar="ID=N", help="force exact count")
    p.add_argument("--min-count", action="append", type=lambda s: parse_kv(s, int), metavar="ID=N")
    p.add_argument("--max-count", action="append", type=lambda s: parse_kv(s, int), metavar="ID=N")
    args = p.parse_args()

    data = load_data()

    if args.list:
        print_listing(data)
        return
    if args.list_attrs:
        print_glossary(data)
        return
    if not args.maximize and not args.minimize:
        p.error("pass --maximize ATTR or --minimize ATTR (or use --list / --list-attrs)")

    try:
        import scipy  # noqa: F401
    except ImportError:
        sys.exit("This optimizer needs scipy (`pip install scipy`) for its MILP solver.")

    build_and_solve(args, data)


if __name__ == "__main__":
    main()
