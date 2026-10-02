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


def test_valid_charges_survives_the_group_id_cache_collision(booted):
    import eos.db
    from eos.db.gamedata import queries
    from eos.saveddata.module import Module

    # Light Missile Launchers take charge group 394, which is also Shield Recharger II's id.
    launcher = eos.db.getItem("Civilian Light Missile Launcher")
    eos.db.getItem(394)  # eos's id-keyed cache now hands 394 to getGroup too
    try:
        with pytest.raises(AttributeError):
            Module(launcher).getValidCharges()  # the upstream bug this pins
        charges = catalog.valid_charges(launcher)
        assert charges and all(c.published for c in charges)
    finally:
        queries.cache.pop((394, None), None)
    sebo = eos.db.getItem("Sensor Booster II")
    assert "Targeting Range Script" in [c.name for c in catalog.valid_charges(sebo)]
