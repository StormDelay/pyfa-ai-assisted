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


def test_full_racks_are_measured_by_swapping_and_overheat_is_reported(booted, no_fits_left):
    result = search.find_modifiers(wyvern.BRIEF, ["tank.ehp.total"], sources=["module"],
                                   meta=["all"], raw_conditions=wyvern.CONDITIONS,
                                   expand=["Shield Hardener"])
    rows = result["candidates"]
    assert any(n.startswith("its slots are full: measured replacing")
               for c in rows for n in c["notes"])
    assert any(c["delta_overheated"] and c["delta_overheated"]["tank.ehp.total"]
               > c["delta"]["tank.ehp.total"] for c in rows)


def test_identical_twins_each_get_a_row(booted, no_fits_left):
    from pyfa_mcp import bench
    with bench.Bench(search._baseline_eft("Wyvern"), {}) as b:
        found = search._pool(b.fit, {"module"}, ["all"])
    result = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["module"],
                                   meta=["all"], expand=["*"])
    by = {c["name"]: c for c in result["candidates"]}
    pairs = [(k, t) for k, ts in found.duplicates.items() if k in by for t in ts]
    assert pairs
    for kept, twin in pairs:
        assert by[twin.name]["delta"] == by[kept]["delta"]
        assert by[twin.name]["type_id"] == twin.type_id
    assert not any("identical to" in n for c in by.values() for n in c["notes"])


def test_pool_cache_separates_bare_and_fitted_tengu(booted, no_fits_left):
    fitted = ("[Tengu, t]\n\nTengu Core - Augmented Graviton Reactor\n"
              "Tengu Defensive - Covert Reconfiguration\n"
              "Tengu Offensive - Accelerated Ejection Bay\n"
              "Tengu Propulsion - Chassis Optimization\n")
    from pyfa_mcp import bench
    pools = []
    for ref in ("Tengu", fitted):
        with bench.Bench(search._baseline_eft(ref), {}) as b:
            pools.append(search._pool(b.fit, {"module"}, None))
    bare, full = pools
    assert bare is not full
    assert any(c.slot == "high" for c in full.candidates)


def test_unknown_reference_is_a_store_error_naming_ships(booted, no_fits_left):
    with pytest.raises(store.StoreError, match="no stored fit named 'Wyvrn'.*nor is it a ship"):
        search._baseline_eft("Wyvrn")


def test_t3_the_plate_should_have_been_a_pds(booted, no_fits_left):
    result = search.marginal_swaps(wyvern.BRIEF, "tank.ehp.total", wyvern.HOT, meta=["all"])
    top = result["swaps"][0]
    assert top["remove"] == wyvern.PLATE
    assert "Power Diagnostic System" in top["add"] and "Modified" in top["add"]
    one_pds = wyvern.fit([wyvern.DC, wyvern.PDS, wyvern.PLATE, wyvern.PLATE])
    expected = _ehp(one_pds, wyvern.HOT) - _ehp(wyvern.BRIEF, wyvern.HOT)
    assert top["delta"] == pytest.approx(expected, rel=1e-6)
    assert result["no_improvement_found"] is False
    assert result["warnings"] == []


def test_marginal_swaps_minimizes_with_a_minus(booted, zealot_eft, no_fits_left):
    result = search.marginal_swaps(zealot_eft, "-navigation.align_time_s", top_n=3)
    assert result["swaps"][0]["delta"] < 0


# Found by applying marginal_swaps' own top swap to a Rifter with one Small Core Defense
# Field Extender I (objective tank.ehp.total) until it reported no improvement.
PUMPED = "[Rifter, g]" + "\n" * 6 + "Small Trimark Armor Pump II\n"
LOOSE = "[Rifter, g]" + "\n" * 6 + "Small Core Defense Field Extender I\n"


def test_no_improvement_on_an_optimal_fit(booted, no_fits_left):
    result = search.marginal_swaps(PUMPED, "tank.ehp.total", include_empty_slots=False)
    assert result["coverage"]["swaps_tried"] > 0 and result["swaps"]
    assert result["no_improvement_found"] is True
    assert all(s["delta"] <= 0 for s in result["swaps"])
    assert "eft" not in result["swaps"][0]


def test_a_disagreeing_evaluator_wins_and_is_named(booted, no_fits_left, monkeypatch):
    real = search._confirm

    def lying(ref, raw, edits):
        out = real(ref, raw, edits)
        return {**out, "flat": {**out["flat"], "tank.ehp.total": 12345.0}}

    monkeypatch.setattr(search, "_confirm", lying)
    result = search.marginal_swaps(LOOSE, "tank.ehp.total", include_empty_slots=False, top_n=1)
    top = result["swaps"][0]
    assert top["new_value"] == 12345.0
    assert top["delta"] == pytest.approx(12345.0 - result["baseline"])
    assert len(result["warnings"]) == 1 and "12345" in result["warnings"][0]
    assert "evaluate_fit" in result["warnings"][0]


def test_marginal_swaps_rejects_a_top_n_below_one(booted, no_fits_left):
    with pytest.raises(ValueError, match="top_n must be at least 1"):
        search.marginal_swaps(LOOSE, "tank.ehp.total", top_n=0)


WYVERN_ALLOW = {"slots": ["low", "mid", "rig"], "module_states": ["active", "overheated"]}


def _lows(text):
    return text.split("\n\n")[1].splitlines()  # Pyfa's EFT: header, blank line, lows


def test_t4_t5_the_best_wyvern(booted, no_fits_left):
    hot = search.optimize_fit(wyvern.BRIEF, "tank.ehp.total", wyvern.CONDITIONS,
                              allow=WYVERN_ALLOW, meta=["all"], top_k=1,
                              budget={"seconds": 900})
    best = hot["best"][0]
    assert hot["search"]["converged"] is True
    assert best["valid"] is True
    assert best["objective_value"] >= _ehp(wyvern.BEST_LOWS, wyvern.HOT) * (1 - 1e-9)
    assert best["objective_value"] > _ehp(wyvern.NO_DC, wyvern.HOT)
    lows = _lows(best["eft"])
    assert sum("Power Diagnostic System" in n for n in lows) == 3
    assert sum("Damage Control" in n for n in lows) == 1

    cold = search.optimize_fit(wyvern.BRIEF, "tank.ehp.total", wyvern.CONDITIONS,
                               allow={**WYVERN_ALLOW, "module_states": ["active"]},
                               meta=["all"], top_k=1, budget={"seconds": 900})
    assert cold["search"]["converged"] is True
    assert cold["best"][0]["objective_value"] < best["objective_value"]


def test_optimize_respects_constraints_and_budget(booted, zealot_eft, no_fits_left):
    speed = evaluate.evaluate(zealot_eft, None)["navigation"]["max_speed"]
    capped = search.optimize_fit(zealot_eft, "offense.dps.total",
                                 constraints=[{"stat": "navigation.max_speed", "gte": speed}],
                                 allow={"slots": ["low", "mid"]}, top_k=2)
    assert capped["best"]
    for fit in capped["best"]:
        assert fit["valid"] is True
        assert fit["stats"]["navigation.max_speed"] >= speed * (1 - 1e-9)
    short = search.optimize_fit(zealot_eft, "tank.ehp.total", budget={"evaluations": 50})
    assert short["search"]["converged"] is False
    assert short["search"]["stopped_by"] == "evaluations"
    assert short["best"]


def test_optimize_keeps_subsystems(booted, no_fits_left):
    tengu = ("[Tengu, t]\n\n\n\n\nTengu Core - Augmented Graviton Reactor\n"
             "Tengu Defensive - Covert Reconfiguration\nTengu Offensive - Accelerated Ejection Bay\n"
             "Tengu Propulsion - Chassis Optimization\n")
    result = search.optimize_fit(tengu, "tank.ehp.total", top_k=1, budget={"seconds": 300})
    for line in tengu.splitlines()[5:]:
        assert line in result["best"][0]["eft"]


def test_optimize_recovers_from_an_invalid_start(booted, no_fits_left):
    two_dcs = "[Rifter, x]\nDamage Control II\nDamage Control II\n"
    result = search.optimize_fit(two_dcs, "tank.ehp.total", allow={"slots": ["low"]},
                                 top_k=1, budget={"seconds": 300})
    assert result["best"][0]["valid"] is True


def test_optimize_refuses_module_states_and_bad_allow(booted, zealot_eft):
    with pytest.raises(ValueError, match="allow.module_states"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", {"module_states": [
            {"module": "Damage Control II", "state": "online"}]})
    with pytest.raises(ValueError, match="subsystems are kept"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"slots": ["subsystem"]})
    with pytest.raises(ValueError, match="locked: 'Heat Sink III' is not on the fit"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", locked="Heat Sink III")
    with pytest.raises(ValueError, match="top_k must be at least 1"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", top_k=0)
    with pytest.raises(ValueError, match="budget: use"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", budget={"second": 5})


def test_optimize_validates_budget_constraints_and_heat(booted, zealot_eft):
    for budget in ({"seconds": 0}, {"evaluations": -1}, {"seconds": "60"}):
        with pytest.raises(ValueError, match="budget"):
            search.optimize_fit(zealot_eft, "tank.ehp.total", budget=budget)
    with pytest.raises(ValueError, match="navigation.max_speed gte '100'"):
        search.optimize_fit(zealot_eft, "tank.ehp.total",
                            constraints=[{"stat": "navigation.max_speed", "gte": "100"}])
    with pytest.raises(ValueError, match="validity.valid lte True"):
        search.optimize_fit(zealot_eft, "tank.ehp.total",
                            constraints=[{"stat": "validity.valid", "lte": True}])
    for states in ([], ["overheated"]):
        with pytest.raises(ValueError, match='must include "active"'):
            search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"module_states": states})


OVER_PG = "[Rifter, x]\n\n" + "Large Shield Extender II\n" * 3


def test_an_empty_best_says_why(booted, no_fits_left):
    short = search.optimize_fit(OVER_PG, "tank.ehp.total", budget={"evaluations": 1})
    assert short["best"] == []
    assert short["reason"].startswith("the budget ran out (evaluations) before any valid fit")
    stuck = search.optimize_fit(OVER_PG, "tank.ehp.total", allow={"slots": ["low"]},
                                locked=OVER_PG)
    assert stuck["best"] == [] and stuck["search"]["converged"] is True
    assert stuck["reason"] == ("every fit tried was invalid; "
                               "most common problem: powergrid over by #")
