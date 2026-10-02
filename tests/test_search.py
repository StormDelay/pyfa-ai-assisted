import pytest

from pyfa_mcp import evaluate, pool, search, store
from tests import wyvern


def _names(result):
    return {c["name"] for c in result["candidates"]}


def _ehp(text, cond):
    return evaluate.evaluate(text, cond)["tank"]["ehp"]["total"]


def test_t1_every_source_of_shield_hp(booted, no_fits_left):
    result = search.find_modifiers("Wyvern", ["tank.hp.shield"], meta=["all"], expand=["*"])
    groups = {g["group"] for g in result["groups"]}
    assert {"Power Diagnostic System", "Shield Extender", "Rig Shield", "Implant sets"} <= groups
    names = _names(result)
    assert "Chelm's Modified Power Diagnostic System" in names
    assert "High-grade Nirvana set" in names
    assert "Zainou 'Gnome' Shield Management SM-706" in names
    assert "Caldari Phenomena Generator" in names
    assert any(n.endswith("+ Shield Extension Charge") for n in names)
    assert result["baseline"]["tank.hp.shield"] > 0
    assert "optimize_fit" in result["next"]
    assert result["coverage"]["items_scanned"] > 1000


def test_t2_assault_damage_control_is_excluded_with_a_reason(booted, no_fits_left):
    result = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["module"],
                                   meta=["all"])
    rows = [e for e in result["excluded"] if e["group"] == "Damage Control"
            and e["reason"].startswith("cannot be fitted to")]
    assert rows and any("Assault" in name for name in rows[0]["examples"])


def test_t6_a_second_phenomena_is_a_drawback(booted, no_fits_left):
    result = search.find_modifiers(wyvern.BRIEF, ["tank.ehp.total"], sources=["phenomena"],
                                   raw_conditions=wyvern.CONDITIONS, expand=["*"])
    amarr = next(c for c in result["candidates"] if c["name"] == "Amarr Phenomena Generator")
    assert amarr["delta"]["tank.ehp.total"] < 0
    assert "drawback: lowers tank.ehp.total" in amarr["notes"]


def test_t7_t8_lock_range_pinned_at_its_cap(booted, no_fits_left):
    four = "[Chimera, c]\n\n" + "Sensor Booster II, Targeting Range Script\n" * 4
    five = "[Chimera, c]\n\n" + "Sensor Booster II, Targeting Range Script\n" * 5
    for text in (four, five):
        assert evaluate.evaluate(text, None)["targeting"]["lock_range_m"] == 750000.0
    result = search.find_modifiers(four, ["targeting.lock_range_m"], sources=["module"])
    assert result["pinned"][0]["cap_attribute"] == "maximumRangeCap"
    assert "Integrated Sensor Array" in result["pinned"][0]["raised_by"]
    isa = "[Chimera, c]\nIntegrated Sensor Array\n\nSensor Booster II, Targeting Range Script\n"
    lock = evaluate.evaluate(isa, None)["targeting"]["lock_range_m"]
    assert lock == pytest.approx(7_987_728, rel=1e-3)


def test_officer_items_need_meta(booted, no_fits_left):
    result = search.find_modifiers("Wyvern", ["tank.hp.shield"], sources=["module"],
                                   expand=["*"])
    assert not [c for c in result["candidates"] if c["meta"] in ("Officer", "Deadspace")]
    assert 'meta=["all"]' in result["applied"]["meta"]


def test_expand_names_unknown_groups(booted, no_fits_left):
    with pytest.raises(ValueError, match="did you mean: Power Diagnostic System"):
        search.find_modifiers("Wyvern", ["tank.hp.shield"], sources=["module"],
                              expand=["Power Diagnostic Sytem"])


def test_find_modifiers_resolves_stored_fits_for_workers(booted, no_fits_left, monkeypatch):
    monkeypatch.setattr(pool, "INLINE_LIMIT", 0)
    before = pool.size()
    pool.configure(2)
    store.save_fit(wyvern.PHENOMENA, "test phenomena")
    try:
        result = search.find_modifiers(
            "Wyvern", ["tank.hp.shield"], sources=["rig"],
            raw_conditions={"command": [{"fit": "test phenomena"}]})
        assert result["groups"]
    finally:
        store.delete_fit("test phenomena")
        pool.configure(before)
