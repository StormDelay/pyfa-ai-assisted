"""Questions about the game data itself: what items and ships exist."""
from __future__ import annotations

import difflib
import re

from pyfa_mcp.eft import suggest

_SLOT_EFFECTS = {"hiPower": "high", "medPower": "mid", "loPower": "low",
                 "rigSlot": "rig", "subSystem": "subsystem"}


class CatalogError(LookupError):
    def __str__(self):
        return str(self.args[0]) if self.args else ""


def valid_charges(item) -> list:
    """Published charges `item` takes, sorted by ID.

    Module.getValidCharges breaks here: eos.db.getGroup shares a cache keyed by id alone
    (vendor/Pyfa/eos/db/gamedata/queries.py cachedQuery), so a group id equal to an
    already-fetched item id returns that Item, which has no `.items`.
    """
    from eos.db import get_gamedata_session
    from eos.gamedata import Group
    from eos.saveddata.module import Module

    mod = Module(item)
    out = {}
    for i in range(5):
        gid = mod.getModifiedItemAttr(f"chargeGroup{i}", None)
        group = get_gamedata_session().get(Group, int(gid)) if gid else None
        for c in group.items if group else ():
            if c.published and mod.isValidCharge(c):
                out[c.ID] = c
    return sorted(out.values(), key=lambda c: c.ID)


def _meta(item) -> str:
    return item.metaGroup.name if item.metaGroup is not None else "Tech I"


def _slot(item) -> str | None:
    for effect, slot in _SLOT_EFFECTS.items():
        if effect in item.effects:
            return slot
    return None


def _row(item) -> dict:
    return {
        "name": item.name, "type_id": item.ID, "group": item.group.name,
        "category": item.category.name, "meta": _meta(item), "slot": _slot(item),
        "cpu": item.getAttribute("cpu"), "powergrid": item.getAttribute("power"),
    }


def published_items(categories=(), groups=()) -> list:
    import eos.db
    from eos.gamedata import Category, Group, Item

    query = (eos.db.gamedata_session.query(Item).join(Group).join(Category)
             .filter(Item.published == True))  # noqa: E712
    if categories:
        query = query.filter(Category.name.in_(categories))
    if groups:
        query = query.filter(Group.name.in_(groups))
    return query.order_by(Item.ID).all()


def search_items(query: str, category: str | None = None, meta: str | None = None,
                 limit: int = 25) -> list[dict]:
    import eos.db

    rows = []
    for item in eos.db.searchItems(query):
        if not item.published:
            continue
        row = _row(item)
        if category and row["category"].casefold() != category.casefold():
            continue
        if meta and row["meta"].casefold() != meta.casefold():
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    return rows[:limit]


def _ship_items():
    import eos.db
    from eos.gamedata import Category, Group, Item

    return (eos.db.gamedata_session.query(Item).join(Group).join(Category)
            .filter(Category.name == "Ship", Item.published == True)  # noqa: E712
            .all())


def list_ships(group: str | None = None, race: str | None = None) -> list[dict]:
    ships = _ship_items()
    if group:
        groups = sorted({s.group.name for s in ships})
        if group.casefold() not in {g.casefold() for g in groups}:
            close = difflib.get_close_matches(group, groups, n=3, cutoff=0.5)
            raise CatalogError(f"unknown ship group '{group}'"
                               + (f" (did you mean: {', '.join(close)}?)" if close else "")
                               + f"; groups: {', '.join(groups)}")
        ships = [s for s in ships if s.group.name.casefold() == group.casefold()]
    if race:
        ships = [s for s in ships if (s.race or "").casefold() == race.casefold()]

    def attr(item, name):
        return int(item.getAttribute(name) or 0)

    return sorted(({
        "name": s.name, "type_id": s.ID, "group": s.group.name, "race": s.race,
        "slots": {"high": attr(s, "hiSlots"), "mid": attr(s, "medSlots"),
                  "low": attr(s, "lowSlots"), "rig": attr(s, "rigSlots")},
        "hardpoints": {"turret": attr(s, "turretSlotsLeft"),
                       "launcher": attr(s, "launcherSlotsLeft")},
        "drones": {"bandwidth": attr(s, "droneBandwidth"), "bay": attr(s, "droneCapacity")},
    } for s in ships), key=lambda r: r["name"])


def _plain(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    return re.sub(r"<[^>]+>", "", text).strip()


def item_info(name: str) -> dict:
    from service.market import Market

    try:
        item = Market.getInstance().getItem(name)
    except Exception:
        item = None
    if item is None or not item.published:
        close = suggest(name)
        raise CatalogError(f"unknown item '{name}'"
                           + (f" (did you mean: {', '.join(close)}?)" if close else ""))
    return {
        **{k: v for k, v in _row(item).items() if k not in ("cpu", "powergrid", "slot")},
        "traits": _plain(item.traits.display) if item.traits is not None else "",
        "attributes": {attr_name: attr.value for attr_name, attr in item.attributes.items()},
    }
