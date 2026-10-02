import pytest

from pyfa_mcp import catalog


def test_search_items(booted):
    rows = catalog.search_items("sensor booster")
    names = [r["name"] for r in rows]
    assert "Sensor Booster II" in names
    sebo = next(r for r in rows if r["name"] == "Sensor Booster II")
    assert sebo["slot"] == "mid"
    assert sebo["meta"] == "Tech II"
    assert sebo["cpu"] > 0


def test_search_filters(booted):
    t1 = catalog.search_items("sensor booster", meta="Tech I")
    assert t1 and all(r["meta"] == "Tech I" for r in t1)
    mods = catalog.search_items("hobgoblin", category="Drone")
    assert mods and all(r["category"] == "Drone" for r in mods)


def test_search_limit(booted):
    assert len(catalog.search_items("armor", limit=5)) == 5


def test_list_ships_by_group(booted):
    battleships = catalog.list_ships(group="Battleship")
    names = {s["name"] for s in battleships}
    assert {"Apocalypse", "Megathron", "Tempest", "Rokh"} <= names
    mega = next(s for s in battleships if s["name"] == "Megathron")
    assert mega["race"] == "gallente"
    assert mega["hardpoints"]["turret"] > 0
    assert mega["slots"]["high"] > 0


def test_list_ships_unknown_group_suggests(booted):
    with pytest.raises(catalog.CatalogError, match="Battleship"):
        catalog.list_ships(group="Battleshp")


def test_item_info(booted):
    info = catalog.item_info("Zealot")
    assert info["group"] == "Heavy Assault Cruiser"
    assert info["traits"]
    assert info["attributes"]["hiSlots"] == 5


def test_item_info_unknown(booted):
    with pytest.raises(catalog.CatalogError, match="Zealot"):
        catalog.item_info("Zealout")
