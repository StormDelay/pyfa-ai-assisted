"""Everything that could change a fit on one hull, and why the rest cannot."""
from __future__ import annotations

import functools
import inspect
import re
from dataclasses import dataclass

from pyfa_mcp import catalog
from pyfa_mcp.bench import SLOT_LABELS, Edit

SOURCES = ("module", "rig", "subsystem", "charge", "implant", "booster",
           "command_burst", "phenomena", "projected", "environment")
DEFAULT_HIDDEN_META = ("Officer", "Deadspace")
_GRADES = ("Low-grade", "Mid-grade", "High-grade")
# Unbonused for every burst, so its deltas are a floor: command ships give more.
_BURST_HULL = "Ferox"
_TITANS = {"Amarr": "Avatar", "Caldari": "Leviathan", "Gallente": "Erebus",
           "Minmatar": "Ragnarok"}
_IGNORED_ATTRS = frozenset({"metaLevelOld", "metaGroupID", "techLevel", "metaLevel"})
_QUOTED = re.compile(r"'(\w+)'")


@dataclass
class Candidate:
    name: str
    source: str
    slot: str
    group: str
    meta: str
    type_id: int
    charge_id: int | None = None
    edits: tuple = ()        # fixed edits: implants, boosters, sets, projected
    extra: dict | None = None  # extra conditions: bursts, phenomena
    cpu: float = 0.0
    pg: float = 0.0
    calibration: float = 0.0
    exclusive_group: str | None = None
    overheat: bool = False
    active: bool = False
    modifies: tuple = ()


@dataclass
class Pool:
    candidates: list
    excluded: list    # [{"name", "group", "reason"}]
    duplicates: dict  # kept name -> twin Candidates, measured once
    scanned: int
    meta_note: str


def parse_sources(sources: list[str] | None) -> set[str]:
    if sources is None:
        return set(SOURCES)
    bad = [s for s in sources if s not in SOURCES]
    if bad:
        raise ValueError(f"sources: unknown '{bad[0]}'; known: {', '.join(SOURCES)}")
    return set(sources)


def meta_filter(meta: list[str] | None):
    if meta is None:
        return (lambda m: m not in DEFAULT_HIDDEN_META,
                "every meta level but Officer and Deadspace (default); pass "
                "meta=[\"all\"], or a list such as [\"Tech II\", \"Faction\", \"Officer\"], "
                "to include them")
    wanted = {m.casefold() for m in meta}
    if "all" in wanted:
        return (lambda m: True), "every meta level"
    return (lambda m: m.casefold() in wanted), "only " + ", ".join(meta)


def why_not(fit, item) -> str | None:
    """Why `item` can never go on this hull, or None if it can."""
    from eos.const import FittingHardpoint, FittingSlot
    from eos.saveddata.citadel import Citadel
    from eos.saveddata.module import Module

    ship = fit.ship
    if not fit.canFit(item):
        return f"cannot be fitted to {ship.item.group.name}"
    try:
        mod = Module(item)
    except ValueError:
        return "not a fittable module"
    if mod.isInvalid or mod.slot not in SLOT_LABELS:
        return "not a fittable module"
    if not fit.getNumSlots(FittingSlot(mod.slot)):
        return f"{ship.item.name} has no {SLOT_LABELS[mod.slot]} slots"
    if (not isinstance(ship, Citadel) and ship.getModifiedItemAttr("isCapitalSize", 0) != 1
            and mod.isCapitalSize):
        return "capital-size module on a sub-capital hull"
    if (mod.slot == FittingSlot.RIG.value
            and mod.getModifiedItemAttr("rigSize") != ship.getModifiedItemAttr("rigSize")):
        return "rig size does not match the hull"
    for kind, attr, word in ((FittingHardpoint.TURRET, "turretSlotsLeft", "turret"),
                             (FittingHardpoint.MISSILE, "launcherSlotsLeft", "launcher")):
        if mod.hardpoint == kind and not ship.getModifiedItemAttr(attr):
            return f"{ship.item.name} has no {word} hardpoints"
    return None


@functools.cache
def _is_attribute(name: str) -> bool:
    import eos.db
    return eos.db.getAttributeInfo(name) is not None


@functools.cache
def _targets(handler) -> tuple:
    try:
        source = inspect.getsource(handler)
    except (OSError, TypeError):
        return ()
    return tuple(dict.fromkeys(n for n in _QUOTED.findall(source) if _is_attribute(n)))


def _modifies(item) -> tuple:
    """Attributes the item's effect handlers name, minus its own (explanatory only)."""
    names: list[str] = []
    for effect in item.effects.values():
        names += _targets(effect.handler)
    return tuple(n for n in dict.fromkeys(names) if n not in item.attributes)


def _exclusive(item) -> str | None:
    for attr in ("maxGroupFitted", "maxGroupActive", "maxGroupOnline"):
        value = item.getAttribute(attr)
        if value:
            return f"{attr} {int(value)} ({item.group.name})"
    return None


def _excluded(item, reason: str) -> dict:
    return {"name": item.name, "group": item.group.name, "reason": reason}


def _local(item, source: str, slot: str, charge=None) -> Candidate:
    return Candidate(
        name=item.name if charge is None else f"{item.name} + {charge.name}",
        source=source, slot=slot, group=item.group.name, meta=catalog._meta(item),
        type_id=item.ID, charge_id=None if charge is None else charge.ID,
        cpu=item.getAttribute("cpu") or 0.0, pg=item.getAttribute("power") or 0.0,
        calibration=item.getAttribute("upgradeCost") or 0.0,
        exclusive_group=_exclusive(item), overheat=item.isType("overheat"),
        active=item.isType("active"), modifies=_modifies(item))


def _charges(item, allowed) -> list:
    return [c for c in catalog.valid_charges(item) if allowed(catalog._meta(c))]


def _pod(item, sources) -> list[Candidate]:
    from eos.saveddata.booster import Booster
    from eos.saveddata.implant import Implant

    kind = "booster" if item.group.name == "Booster" else "implant"
    if kind not in sources:
        return []
    try:
        slot = (Booster if kind == "booster" else Implant)(item).slot
    except Exception:
        return []
    if not slot:
        return []
    return [Candidate(name=item.name, source=kind, slot=f"{kind} {slot}",
                      group=item.group.name, meta=catalog._meta(item), type_id=item.ID,
                      edits=(Edit((kind, slot), item.ID),),
                      exclusive_group=f"{kind} slot {slot}", modifies=_modifies(item))]


def _sets(implants: list[Candidate]) -> list[Candidate]:
    """One composite candidate per implant set and grade: a lone piece understates it."""
    import eos.db

    groups: dict[tuple, list[Candidate]] = {}
    for c in implants:
        first = c.name.split()[0]
        grade = first if first in _GRADES else ""
        for attr in eos.db.getItem(c.type_id).attributes:
            if attr.startswith("ImplantSet"):
                groups.setdefault((attr[len("ImplantSet"):], grade), []).append(c)
    out = []
    for (name, grade), pieces in sorted(groups.items()):
        slots = [p.edits[0].where[1] for p in pieces]
        if len(pieces) < 2 or len(set(slots)) != len(slots):
            continue
        out.append(Candidate(
            name=f"{grade} {name} set".strip(), source="implant",
            slot="implants " + ", ".join(map(str, sorted(slots))), group="Implant sets",
            meta=pieces[0].meta, type_id=pieces[0].type_id,
            edits=tuple(e for p in pieces for e in p.edits),
            exclusive_group="implant slots " + ", ".join(map(str, sorted(slots)))))
    return out


def _bursts(allowed) -> list[Candidate]:
    out = []
    for item in catalog.published_items(groups=("Command Burst",)):
        if not allowed(catalog._meta(item)):
            continue
        for charge in catalog.valid_charges(item):
            booster = f"[{_BURST_HULL}, {item.name}]\n\n\n{item.name}, {charge.name}\n"
            out.append(Candidate(
                name=f"{item.name} + {charge.name}", source="command_burst", slot="external",
                group=charge.name, meta=catalog._meta(item), type_id=item.ID,
                charge_id=charge.ID, extra={"command": [{"fit": booster}]}))
    return out


def _phenomena(allowed, excluded: list) -> list[Candidate]:
    out = []
    for item in catalog.published_items(groups=("Titan Phenomena Generator",)):
        if not allowed(catalog._meta(item)):
            continue
        titan = _TITANS.get(item.name.split()[0])
        if titan is None:
            excluded.append(_excluded(item, "no titan known to carry it"))
            continue
        out.append(Candidate(
            name=item.name, source="phenomena", slot="external", group=item.group.name,
            meta=catalog._meta(item), type_id=item.ID,
            extra={"command": [{"fit": f"[{titan}, {item.name}]\n\n\n{item.name}\n"}]}))
    return out


def _projected(item, source: str, state: str) -> Candidate:
    return Candidate(name=item.name, source=source, slot="external", group=item.group.name,
                     meta=catalog._meta(item), type_id=item.ID,
                     edits=(Edit(("projected", None), item.ID, None, state),))


def _signature(c: Candidate):
    import eos.db

    if c.extra is not None or c.group == "Implant sets":
        return None
    item = eos.db.getItem(c.type_id)
    return (c.source, c.slot, c.charge_id, tuple(sorted(item.effects)),
            tuple(sorted((k, v.value) for k, v in item.attributes.items()
                         if k not in _IGNORED_ATTRS)))


def _dedupe(found: list[Candidate]) -> tuple[list[Candidate], dict]:
    keep, first, twins = [], {}, {}
    for c in found:
        key = _signature(c)
        if key is None or key not in first:
            if key is not None:
                first[key] = c
            keep.append(c)
        else:
            twins.setdefault(first[key].name, []).append(c)
    return keep, twins


def build(fit, sources: set[str], meta: list[str] | None) -> Pool:
    from eos.saveddata.module import Module

    allowed, note = meta_filter(meta)
    found: list[Candidate] = []
    excluded: list[dict] = []
    items = catalog.published_items(categories=("Module", "Subsystem", "Implant"))
    for item in items:
        meta_name = catalog._meta(item)
        if not allowed(meta_name):
            excluded.append(_excluded(item, f"meta {meta_name} not requested"))
            continue
        if item.category.name == "Implant":
            found += _pod(item, sources)
            continue
        if "projected" in sources and item.isType("projected"):
            found.append(_projected(item, "projected", "active"))
        slot = catalog._slot(item)
        if slot is None:
            continue
        source = slot if slot in ("rig", "subsystem") else "module"
        wants_charges = source == "module" and "charge" in sources
        if source not in sources and not wants_charges:
            continue
        reason = why_not(fit, item)
        if reason:
            excluded.append(_excluded(item, reason))
            continue
        if source in sources:
            found.append(_local(item, source, slot))
        if wants_charges:
            found += [_local(item, "charge", slot, c) for c in _charges(item, allowed)]
    scanned = len(items)
    if "command_burst" in sources:
        found += _bursts(allowed)
    if "phenomena" in sources:
        found += _phenomena(allowed, excluded)
    if "environment" in sources:
        beacons = catalog.published_items(groups=Module.SYSTEM_GROUPS)
        scanned += len(beacons)
        found += [_projected(i, "environment", "online") for i in beacons]
    if "implant" in sources:
        found += _sets([c for c in found if c.source == "implant"])
    found, duplicates = _dedupe(found)
    return Pool(found, excluded, duplicates, scanned, note)
