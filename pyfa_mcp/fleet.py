"""Who boosts best: the strongest source of every command burst, from the game data.

A burst's strength is hull bonus x module x mindlink x skills (All V), each
scaling the module's warfareBuff values, so they are measured apart: every hull
that has a burst bonus (plus one unbonused hull as the floor) per burst family,
then the mindlinks and each module variant on the family's best hull. Pyfa keeps
only the strongest value per buff (Fit.addCommandBonus): the best source for a
charge is the strongest one, and different charges stack.

The table depends only on the game data. It is built once (a few seconds) and
kept in the data dir per game build.
"""
from __future__ import annotations

import functools
import json
import re

from pyfa_mcp import catalog
from pyfa_mcp.bench import Edit

_BONUS = re.compile(r"burst (effect )?strength", re.IGNORECASE)


def _strength(fit) -> float:
    mod = next(m for m in fit.modules if not m.isEmpty)
    return max(abs(mod.getModifiedItemAttr(f"warfareBuff{i}Value") or 0.0)
               for i in range(1, 5))


def _measure(b, edits) -> float | None:
    from service.fit import Fit as FitService
    try:
        undo = b.apply(edits)
    except ValueError:  # BenchError, or eos refusing the item
        return None
    try:
        FitService.getInstance().recalc(b.fit)
        return _strength(b.fit)
    finally:
        b.revert(undo)


def _bonused(ship) -> bool:
    return ship.traits is not None and bool(_BONUS.search(catalog._plain(ship.traits.display)))


def _bench(hull: str, module: str, charge: str):
    from pyfa_mcp import bench
    return bench.Bench(f"[{hull}, x]\n\n\n{module}, {charge}\n", {})


def _burst_at(b) -> int:
    return next(i for i, m in enumerate(b.fit.modules) if not m.isEmpty)


def _build() -> dict:
    from eos.saveddata.fit import Fit
    from eos.saveddata.ship import Ship
    from pyfa_mcp.candidates import why_not

    families: dict[str, list] = {}  # first module's name -> modules taking its charges
    charges: dict[str, list] = {}   # module name -> its charges (items)
    first_of: dict[tuple, str] = {}
    for item in catalog.published_items(groups=("Command Burst",)):
        valid = catalog.valid_charges(item)
        if not valid:
            continue
        charges[item.name] = valid
        family = first_of.setdefault(tuple(c.ID for c in valid), item.name)
        families.setdefault(family, []).append(item)
    reps = {family: members[0] for family, members in families.items()}

    can: dict[str, list] = {family: [] for family in reps}
    for ship in catalog.published_items(categories=("Ship",)):
        try:
            fit = Fit(Ship(ship))
        except Exception:
            continue
        for family, rep in reps.items():
            if why_not(fit, rep) is None:
                can[family].append(ship)
    per_ship: dict = {}
    for family, ships in can.items():
        floor = [s for s in ships if not _bonused(s)][:1]  # unbonused hulls all tie
        for ship in [s for s in ships if _bonused(s)] + floor:
            per_ship.setdefault(ship, []).append(family)

    hulls: dict[str, list] = {family: [] for family in reps}
    for ship, fams in sorted(per_ship.items(), key=lambda p: p[0].ID):
        rep = reps[fams[0]]
        with _bench(ship.name, rep.name, charges[rep.name][0].name) as b:
            at = _burst_at(b)
            for family in fams:
                r = reps[family]
                value = _measure(b, [Edit(("module", at), r.ID, charges[r.name][0].ID)])
                if value:
                    hulls[family].append((ship.ID, ship.name, value))

    mindlinks: dict[str, list] = {}
    modules: dict[str, dict] = {}
    for family, found in hulls.items():
        if not found:
            continue
        found.sort(key=lambda h: (-h[2], h[0]))  # strongest; ties: the older hull
        _, hull, base = found[0]
        rep = reps[family]
        with _bench(hull, rep.name, charges[rep.name][0].name) as b:
            at = _burst_at(b)
            links = []
            for link in catalog.published_items(groups=("Cyber Leadership",)):
                value = _measure(b, [Edit(("implant", 10), link.ID)])
                if value and value > base * (1 + 1e-9):
                    links.append([link.name, value / base])
            mindlinks[family] = sorted(links, key=lambda l: (-l[1], l[0]))
            for item in families[family]:
                value = _measure(b, [Edit(("module", at), item.ID, charges[item.name][0].ID)])
                if value:
                    modules[item.name] = {"family": family, "factor": value / base,
                                          "charges": [c.name for c in charges[item.name]]}
    return {"hulls": {f: [[name, s] for _, name, s in found] for f, found in hulls.items()
                      if found},
            "mindlinks": mindlinks, "modules": modules}


_SCHEMA = 1  # bump when the table's shape changes: older files are then ignored


def _path():
    from pyfa_mcp import eosboot
    return eosboot.booted_dir() / f"burst_sources-v{_SCHEMA}-{catalog.client_build()}.json"


@functools.cache
def table() -> dict:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if all(isinstance(data.get(k), dict) for k in ("hulls", "mindlinks", "modules")):
            return data
    except (OSError, ValueError, AttributeError):
        pass  # missing, damaged or another shape: built again
    data = _build()
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # kept for this process only
    return data


def sources(module: str, ok) -> list[dict]:
    """Every hull for this burst module, strongest first, with the strongest
    mindlink `ok` allows."""
    import eos.db
    t = table()
    entry = t["modules"].get(module)
    if entry is None:
        return []
    family = entry["family"]
    link, factor = next(((n, f) for n, f in t["mindlinks"].get(family, [])
                         if ok(eos.db.getItem(n))), (None, 1.0))
    return [{"module": module, "hull": hull, "mindlink": link,
             "strength": s * entry["factor"] * factor} for hull, s in t["hulls"][family]]


def by_charge(ok) -> dict[str, list[dict]]:
    """Every burst charge -> its sources over every module `ok` allows, strongest
    first, one per hull."""
    import eos.db
    out: dict[str, list] = {}
    for module, entry in table()["modules"].items():
        if not ok(eos.db.getItem(module)):
            continue
        for charge in entry["charges"]:
            out.setdefault(charge, []).extend(sources(module, ok))
    for charge, found in out.items():
        found.sort(key=lambda s: -s["strength"])  # stable: table order breaks ties
        seen: set = set()
        out[charge] = [s for s in found if not (s["hull"] in seen or seen.add(s["hull"]))]
    return out


def booster_eft(hull: str, lines, mindlink: str | None) -> str:
    text = f"[{hull}, Fleet boosts]\n\n\n" + "".join(f"{m}, {c}\n" for m, c in lines)
    return text + (f"\n\n{mindlink}\n" if mindlink else "")


def _valid(hull: str, lines: list, mindlink: str | None) -> list[str]:
    from pyfa_mcp import evaluate
    text = booster_eft(hull, lines, mindlink)
    if len(lines) == 1 or evaluate.evaluate(text, None)["validity"]["valid"]:
        return [text]
    half = len(lines) // 2
    return _valid(hull, lines[:half], mindlink) + _valid(hull, lines[half:], mindlink)


def booster_fits(chosen: list[dict]) -> list[str]:
    """Booster fits carrying the chosen sources: one per hull and mindlink, split
    until Pyfa calls each valid (a hull runs only so many bursts)."""
    groups: dict[tuple, list] = {}
    for s in chosen:
        groups.setdefault((s["hull"], s["mindlink"]), []).append((s["module"], s["charge"]))
    return [text for (hull, link), lines in groups.items()
            for text in _valid(hull, lines, link)]
