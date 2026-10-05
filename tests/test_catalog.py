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


def test_limits_and_pod_slots(booted):
    import eos.db
    chips = {r["name"]: r for r in catalog.search_items("Capsuleer Defense")}
    assert chips["Advanced Capsuleer Defense Augmentation Chip"]["limits"] == [
        "Serenity only", "characters under 100 days"]
    dose = catalog.search_items("Agency 'Hardshell' TB3")[0]
    assert "limits" not in dose and dose["slot"].startswith("booster ")
    assert catalog.limits(eos.db.getItem("Imperial Electronics Booster I")) == [
        "expires 2026-11-10"]
    assert catalog.item_info("Halcyon B-5 Booster")["slot"] == "booster 5"
    assert catalog.client_build()


def test_t6_ships_by_capability(booted):
    bursts = {s["name"] for s in catalog.list_ships(can_fit="command burst")}
    assert {"Salvation", "Simurgh", "Gaia", "Ymir", "Nighthawk", "Ferox"} <= bursts
    assert "Rifter" not in bursts
    rows = catalog.list_ships(bonus="Shield Command burst strength")
    names = [r["name"] for r in rows]
    assert names[:2] == ["Simurgh", "Ymir"]
    assert {"Nighthawk", "Vulture", "Chimera", "Wyvern"} <= set(names)
    top = rows[0]["bonuses"][0]
    assert top["at_all_v"] == 25.0 and top["per"] == "level"
    assert "Shield Command" in top["line"]
    with pytest.raises(catalog.CatalogError, match="can_fit"):
        catalog.list_ships(can_fit="Comand Brust")


def test_t12_whats_new_lists_the_command_carriers(booted):
    result = catalog.whats_new("ship", 10)
    names = [i["name"] for i in result["items"]]
    assert {"Salvation", "Simurgh", "Gaia", "Ymir"} <= set(names)
    assert names[0] == "Ymir"
    assert "type ID" in result["ordered_by"] and result["game_client_build"]
    assert len(catalog.whats_new(limit=5)["items"]) == 5


def test_whats_new_rejects_bad_arguments(booted):
    with pytest.raises(catalog.CatalogError, match="Ship, Module"):
        catalog.whats_new("Spaceship")
    with pytest.raises(catalog.CatalogError, match="limit"):
        catalog.whats_new(limit=0)


def test_item_info_lists_the_charges_a_module_takes(booted):
    assert "Scorch M" in catalog.item_info("Imperial Navy Heavy Pulse Laser")["charges"]
    assert "Scorch M" not in catalog.item_info("Heavy Pulse Laser I")["charges"]
    assert "charges" not in catalog.item_info("Zealot")


def test_p8_item_rows_carry_a_price(booted, seed_prices):
    seed_prices({"Zealot": 2e8})
    info = catalog.item_info("Zealot")
    assert info["price"] == 2e8
    assert info["price_source"].startswith("fuzzwork Forge sell")
    rows = catalog.search_items("Heat Sink II")
    assert rows and all("price" in r for r in rows)
    assert all(r["price"] is None for r in rows)  # unseeded: unknown, not 0
