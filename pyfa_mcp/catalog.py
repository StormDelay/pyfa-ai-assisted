"""Questions about the game data itself: what items and ships exist."""
from __future__ import annotations

import datetime
import difflib
import functools
import re

from pyfa_mcp.eft import suggest

_SLOT_EFFECTS = {"hiPower": "high", "medPower": "mid", "loPower": "low",
                 "rigSlot": "rig", "subSystem": "subsystem"}


class CatalogError(LookupError):
    def __str__(self):
        return str(self.args[0]) if self.args else ""


@functools.cache
def _group_items(gid: int) -> tuple:
    """A group's items with their attributes loaded in two queries, not one per charge."""
    from sqlalchemy.orm import selectinload
    from eos.db import get_gamedata_session
    from eos.gamedata import Group, Item

    group = (get_gamedata_session().query(Group).options(
        selectinload(Group.items).selectinload(Item._Item__attributes)).filter(Group.ID == gid).first())
    return tuple(group.items) if group else ()


def valid_charges(item) -> list:
    """Published charges `item` takes, sorted by ID.

    Module.getValidCharges breaks here: eos.db.getGroup shares a cache keyed by id alone
    (vendor/Pyfa/eos/db/gamedata/queries.py cachedQuery), so a group id equal to an
    already-fetched item id returns that Item, which has no `.items`.
    """
    from eos.saveddata.module import Module

    mod = Module(item)
    out = {}
    for i in range(5):
        gid = mod.getModifiedItemAttr(f"chargeGroup{i}", None)
        for c in _group_items(int(gid)) if gid else ():
            if c.published and mod.isValidCharge(c):
                out[c.ID] = c
    return sorted(out.values(), key=lambda c: c.ID)


def _meta(item) -> str:
    return item.metaGroup.name if item.metaGroup is not None else "Tech I"


@functools.cache
def _serenity_ids() -> frozenset:
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT typeID FROM invtypes "
            "WHERE typeDescription LIKE '%available on Serenity%'").fetchall()
    return frozenset(r[0] for r in rows)


def limits(item) -> list[str]:
    """Why most pilots cannot use `item`; empty when anyone on Tranquility can."""
    out = []
    if item.ID in _serenity_ids() or item.name.startswith("Serenity "):
        out.append("Serenity only")
    hours = item.getAttribute("boosterMaxCharAgeHours")
    if hours:
        out.append(f"characters under {round(hours / 24)} days")
    days = item.getAttribute("boosterLastInjectionDatetime")  # days since 1970-01-01
    if days:
        expires = datetime.date(1970, 1, 1) + datetime.timedelta(days=int(days))
        out.append(f"expires {expires.isoformat()}")
    return out


@functools.cache
def client_build() -> str | None:
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        meta = dict(connection.exec_driver_sql(
            "SELECT field_name, field_value FROM metadata").fetchall())
    return meta.get("client_build")


def _slot(item) -> str | None:
    for effect, slot in _SLOT_EFFECTS.items():
        if effect in item.effects:
            return slot
    if item.category.name == "Implant":
        for attr, kind in (("implantness", "implant"), ("boosterness", "booster")):
            value = item.getAttribute(attr)
            if value:
                return f"{kind} {int(value)}"
    return None


def _row(item) -> dict:
    row = {
        "name": item.name, "type_id": item.ID, "group": item.group.name,
        "category": item.category.name, "meta": _meta(item), "slot": _slot(item),
        "cpu": item.getAttribute("cpu"), "powergrid": item.getAttribute("power"),
    }
    found = limits(item)
    if found:
        row["limits"] = found
    return row


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
        **{k: v for k, v in _row(item).items()
           if k not in ("cpu", "powergrid") and not (k == "slot" and v is None)},
        "traits": _plain(item.traits.display) if item.traits is not None else "",
        "attributes": {attr_name: attr.value for attr_name, attr in item.attributes.items()},
    }
