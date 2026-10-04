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


def _can_fit_items(name: str) -> list:
    """The items an item-group name stands for, or the one item named."""
    import eos.db
    from sqlalchemy import func
    from eos.gamedata import Group, Item
    from service.market import Market

    items = (eos.db.gamedata_session.query(Item).join(Group)
             .filter(Item.published == True,  # noqa: E712
                     func.lower(Group.name) == name.strip().casefold())
             .order_by(Item.ID).all())
    if items:
        return items
    try:
        item = Market.getInstance().getItem(name.strip())
    except Exception:
        item = None
    if item is None or not item.published:
        close = suggest(name)
        raise CatalogError(f"can_fit: no item or item group named '{name}'"
                           + (f" (did you mean: {', '.join(close)}?)" if close else ""))
    return [item]


def _bonus_lines(ship, words: list[str]) -> list[dict]:
    """Trait lines holding every word, with the bonus at All V."""
    text = _plain(ship.traits.display) if ship.traits is not None else ""
    per, out = "role", []
    for raw in text.splitlines():
        line = raw.strip().lstrip("•").strip()
        if line.endswith(":"):  # a section header: "... bonuses (per skill level):"
            per = "level" if "per skill level" in line.casefold() else "role"
            continue
        if line and all(w in line.casefold() for w in words):
            number = re.match(r"([\d.]+)%", line)
            value = None if number is None else float(number.group(1)) * (
                5 if per == "level" else 1)
            out.append({"line": line, "per": per, "at_all_v": value})
    return out


def hull_bonuses(ship) -> list[str]:
    """Every trait line of a ship item, per-level ones marked."""
    return [b["line"] + (" (per skill level)" if b["per"] == "level" else "")
            for b in _bonus_lines(ship, [])]


def list_ships(group: str | None = None, race: str | None = None,
               can_fit: str | None = None, bonus: str | None = None) -> list[dict]:
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
    if can_fit:
        from eos.saveddata.fit import Fit
        from eos.saveddata.ship import Ship
        from pyfa_mcp.candidates import why_not

        wanted = _can_fit_items(can_fit)

        def fits(ship) -> bool:
            try:
                fit = Fit(Ship(ship))
            except Exception:
                return False
            return any(why_not(fit, item) is None for item in wanted)
        ships = [s for s in ships if fits(s)]

    def attr(item, name):
        return int(item.getAttribute(name) or 0)

    words = bonus.casefold().split() if bonus else []
    rows = []
    for s in ships:
        row = {
            "name": s.name, "type_id": s.ID, "group": s.group.name, "race": s.race,
            "slots": {"high": attr(s, "hiSlots"), "mid": attr(s, "medSlots"),
                      "low": attr(s, "lowSlots"), "rig": attr(s, "rigSlots")},
            "hardpoints": {"turret": attr(s, "turretSlotsLeft"),
                           "launcher": attr(s, "launcherSlotsLeft")},
            "drones": {"bandwidth": attr(s, "droneBandwidth"), "bay": attr(s, "droneCapacity")},
        }
        if words:
            row["bonuses"] = _bonus_lines(s, words)
            if not row["bonuses"]:
                continue
        rows.append(row)
    if words:
        return sorted(rows, key=lambda r: (-max(b["at_all_v"] or 0.0 for b in r["bonuses"]),
                                           r["name"]))
    return sorted(rows, key=lambda r: r["name"])


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
    info = {
        **{k: v for k, v in _row(item).items()
           if k not in ("cpu", "powergrid") and not (k == "slot" and v is None)},
        "traits": _plain(item.traits.display) if item.traits is not None else "",
        "attributes": {attr_name: attr.value for attr_name, attr in item.attributes.items()},
    }
    if any(a.startswith("chargeGroup") for a in item.attributes):
        info["charges"] = [c.name for c in valid_charges(item)]
    return info


_NEW_CATEGORIES = ("Ship", "Module", "Implant", "Charge", "Drone", "Fighter", "Subsystem")


def whats_new(category: str | None = None, limit: int = 30) -> dict:
    if limit < 1:
        raise CatalogError("limit must be at least 1")
    categories = _NEW_CATEGORIES
    if category:
        categories = tuple(c for c in _NEW_CATEGORIES if c.casefold() == category.casefold())
        if not categories:
            raise CatalogError(f"unknown category '{category}'; one of "
                               + ", ".join(_NEW_CATEGORIES))
    items = published_items(categories=categories)[::-1][:limit]
    keep = ("name", "type_id", "group", "category", "limits")
    return {"ordered_by": "type ID: the game data carries no dates; higher IDs were "
                          "added later",
            "game_client_build": client_build(),
            "items": [{k: v for k, v in _row(i).items() if k in keep} for i in items]}
