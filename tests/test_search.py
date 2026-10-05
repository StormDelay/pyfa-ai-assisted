import json

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
                                   meta=["all"], verbose=True)
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
    result = search.marginal_swaps("[Rifter, x]\n", "-navigation.align_time_s", top_n=3)
    assert result["swaps"][0]["delta"] < 0
    assert all(isinstance(n, int) for n in result["coverage"]["excluded"].values())


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
    rigs = [n for n in best["eft"].splitlines() if n == wyvern.RIG]
    assert len(rigs) == 3
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
                                 allow={"slots": ["low", "mid"]}, top_k=2, verbose=True)
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


def test_search_refuses_stats_that_are_not_numbers(booted, zealot_eft):
    with pytest.raises(ValueError, match="capacitor.lasts_s.*capacitor.delta_per_s"):
        search.find_modifiers(zealot_eft, ["capacitor.lasts_s"])
    with pytest.raises(ValueError, match="validity.problems.*validity.valid"):
        search.marginal_swaps(zealot_eft, "validity.problems")
    with pytest.raises(ValueError, match="capacitor.stable_at_percent"):
        search.optimize_fit(zealot_eft, "-capacitor.stable_at_percent")
    with pytest.raises(ValueError, match="capacitor.lasts_s"):
        search.optimize_fit(zealot_eft, "tank.ehp.total",
                            constraints=[{"stat": "capacitor.lasts_s", "gte": 100}])


def test_a_boolean_constraint_still_works(booted, zealot_eft, no_fits_left):
    result = search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"slots": ["rig"]},
                                 constraints=[{"stat": "capacitor.stable", "eq": False}],
                                 top_k=1, budget={"evaluations": 30})
    assert result["best"]


def test_find_modifiers_minimizes_with_a_minus(booted, zealot_eft, no_fits_left):
    key = "navigation.align_time_s"
    result = search.find_modifiers(zealot_eft, ["-" + key], sources=["module"])
    assert set(result["baseline"]) == {key}
    groups = [g["group"] for g in result["groups"]]
    nano = groups.index("Nanofiber Internal Structure")
    assert nano < groups.index("Armor Plate")
    assert result["groups"][nano]["best"]["delta"][key] < 0
    assert not any("drawback" in n for n in result["groups"][nano]["notes"])
    plates = result["groups"][groups.index("Armor Plate")]
    assert f"drawback: raises {key}" in plates["notes"]
    assert result["groups"][0]["delta_range"][1] < 0
    assert 'objective="-navigation.align_time_s"' in result["next"]


def test_marginal_swaps_names_an_invalid_baseline(booted, no_fits_left):
    two_dcs = "[Rifter, x]\nDamage Control II\nDamage Control II\n"
    result = search.marginal_swaps(two_dcs, "tank.ehp.total", include_empty_slots=False)
    assert result["baseline_invalid"] == ["Damage Control II was left out: "
                                          "cannot be fitted to this ship"]
    assert result["reason"].startswith("the fit is invalid as given")


HOME_EM = {"damage_profile": "Home EM"}


def _rigs(cond):
    return search.find_modifiers("Rifter", ["tank.ehp.total"], sources=["rig"],
                                 raw_conditions=cond)["groups"]


def test_workers_read_the_users_own_pyfa_profiles(pyfa_home, no_fits_left, monkeypatch):
    monkeypatch.setattr(pool, "INLINE_LIMIT", 0)
    before = pool.size()
    try:
        pool.configure(0)
        inline = _rigs(HOME_EM)
        search._TRIALS.clear()
        pool.configure(2)
        pooled = _rigs(HOME_EM)
    finally:
        pool.configure(before)
    assert pooled == inline


def test_an_edited_pyfa_profile_is_not_served_from_cache(pyfa_home, no_fits_left,
                                                         monkeypatch):
    import contextlib
    import sqlite3
    monkeypatch.setattr(pool, "_size", 0)
    first = _rigs(HOME_EM)
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        db.execute("UPDATE damagePatterns SET emAmount = 0, explosiveAmount = 1 "
                   "WHERE name = 'Home EM'")
        db.commit()
    second = _rigs(HOME_EM)
    search._TRIALS.clear()
    assert second == _rigs(HOME_EM) != first


def test_optimize_fit_leaves_the_find_modifiers_cache_alone(booted, zealot_eft, no_fits_left,
                                                            monkeypatch):
    from pyfa_mcp import bench
    calls = []

    def counting(ref, raw, keys, trials):
        calls.append(len(trials))
        return bench.run_trials(ref, raw, keys, trials)

    monkeypatch.setattr(pool, "run", counting)
    monkeypatch.setattr(pool, "_size", 2)
    monkeypatch.setattr(pool, "running", lambda: True)  # every batch goes to "the pool"
    search._TRIALS.clear()

    def ask():
        return search.find_modifiers(zealot_eft, ["tank.ehp.total"], sources=["rig"])

    first = ask()
    cached = list(search._TRIALS)
    search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"slots": ["rig"]}, top_k=1,
                        budget={"evaluations": 300})
    assert list(search._TRIALS) == cached
    asked = len(calls)
    assert asked > 1  # the optimizer did use the pool
    assert ask() == first and len(calls) == asked


def test_improve_takes_every_improving_single_before_any_pair():
    s = search._Search.__new__(search._Search)
    events = []

    def singles(state):
        events.append(("singles", state))
        return [state + 1] if state < 3 else [state - 100]

    def pairs(state):
        events.append(("pairs", state))
        return [state - 200]

    s.singles, s.pairs, s.set_moves = singles, pairs, lambda state, sets: []
    s.evaluate = lambda states: [(st, st, None) for st in states]
    s.best = lambda moves: max((m, m) for m in moves)
    s.better = lambda score, state: score > state
    s.canonical = lambda state: state
    s.improve([3, 0], [])
    first_pair = events.index(next(e for e in events if e[0] == "pairs"))
    assert ("singles", 2) in events[:first_pair]


def test_optimize_measures_in_pieces_and_says_why_it_stopped(booted, zealot_eft, no_fits_left,
                                                             monkeypatch):
    monkeypatch.setattr(pool, "_size", 0)
    sizes = []
    real = search._Search._trials

    def recording(self, edits):
        sizes.append(len(edits))
        return real(self, edits)

    monkeypatch.setattr(search._Search, "_trials", recording)
    result = search.optimize_fit(zealot_eft, "tank.ehp.total", top_k=1, budget={"seconds": 2})
    assert max(sizes) <= pool.INLINE_LIMIT
    assert result["search"]["stopped_by"] == "seconds"
    assert "budget.seconds" in result["search"]["note"]
    assert "in-process" in result["search"]["note"]


def test_polish_takes_improving_singles_until_none_is_left():
    s = search._Search.__new__(search._Search)
    s.sign = 1
    s.singles = lambda st: [{"a": st["a"] + 1}] if st["a"] < 3 else [{"a": 0}]
    s.evaluate = lambda states: [((0, st["a"]), st, None) for st in states]
    s.best = lambda moves: ((0, moves[0]["a"]), moves[0])
    s.better = lambda score, state: score[1] > state["a"]
    swaps = []
    assert s.polish({"a": 0}, swaps) == {"a": 3}
    assert swaps == [("a", 0, 1, 1), ("a", 1, 2, 1), ("a", 2, 3, 1)]


def test_t1_a_resist_booster_is_not_pruned_on_an_empty_hull(booted, no_fits_left):
    # On a bare Wyvern G-5 beats B-5; on this fit B-5 wins (166.60M vs 166.29M).
    result = search.optimize_fit(wyvern.BEST_LOWS, "tank.ehp.total",
                                 allow={"slots": ["low", "mid", "rig"], "boosters": True},
                                 locked="\n".join(wyvern.POD), meta=["all"], top_k=1,
                                 budget={"seconds": 600})
    best = result["best"][0]
    assert "Halcyon B-5 Booster" in best["eft"].splitlines()
    assert "Halcyon G-5 Booster" not in best["eft"].splitlines()
    assert best["polish"]["converged"] is True


def test_t3_a_result_reproduces_with_its_conditions(booted, zealot_eft, no_fits_left):
    result = search.optimize_fit(zealot_eft, "tank.ehp.total",
                                 allow={"slots": ["low"],
                                        "module_states": ["active", "overheated"]},
                                 top_k=1, budget={"seconds": 300})
    best = result["best"][0]
    assert any(s["state"] == "overheated" for s in best["conditions"]["module_states"])
    assert _ehp(best["eft"], best["conditions"]) == pytest.approx(best["objective_value"],
                                                                 rel=1e-12)
    assert best["objective_cold"] < best["objective_value"]


def test_t4_compact_output_fits_a_context(booted, no_fits_left):
    allow = {"slots": ["high", "mid", "low", "rig"], "implants": True, "boosters": True,
             "module_states": ["active", "overheated"]}
    compact = search.optimize_fit("Wyvern", "tank.ehp.total", wyvern.CONDITIONS,
                                  allow=allow, budget={"seconds": 10})  # size, not quality
    assert len(json.dumps(compact)) < 24_000
    assert isinstance(compact["considered"]["low"], int)
    assert set(compact["pruned"]) == {"counts", "near_winners"}
    assert len(compact["pruned"]["near_winners"]) <= 20
    assert all(isinstance(n, int) for n in compact["excluded"].values())
    assert all(set(b) - {"price_total", "price_partial"} == {"objective_value", "valid", "diff"}
               for b in compact["best"][1:])  # unpriced names stay on best[0]


def test_verbose_brings_back_every_name(booted, zealot_eft, no_fits_left):
    full = search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"slots": ["low"]},
                               top_k=2, verbose=True, budget={"seconds": 10})
    assert isinstance(full["considered"]["low"], list)
    assert isinstance(full["pruned"], list) and isinstance(full["excluded"], list)
    assert all("eft" in b for b in full["best"])
    rows = search.find_modifiers(zealot_eft, ["tank.ehp.total"], sources=["rig"])
    assert all(isinstance(n, int) for n in rows["excluded"].values())


def test_t5_limited_items_are_hidden_unless_asked(booted, no_fits_left):
    hidden = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["booster"],
                                   expand=["*"])
    assert not [n for n in _names(hidden) if "Capsuleer" in n or n.startswith("Serenity")]
    assert 'availability="all"' in hidden["applied"]["availability"]
    shown = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["booster"],
                                  availability="all", expand=["*"])
    chip = next(c for c in shown["candidates"]
                if c["name"] == "Advanced Capsuleer Defense Augmentation Chip")
    assert chip["limits"] == ["Serenity only", "characters under 100 days"]
    pods = {"slots": [], "boosters": True}
    tq = search.optimize_fit("Rifter", "tank.ehp.total", allow=pods, top_k=1)
    assert "Capsuleer" not in tq["best"][0]["eft"]
    everything = search.optimize_fit("Rifter", "tank.ehp.total", allow=pods, top_k=1,
                                     availability="all")
    assert "Capsuleer" in everything["best"][0]["eft"]


def test_availability_names_its_values(booted, zealot_eft):
    for wrong in ("TQ", "theoretical"):
        with pytest.raises(ValueError, match='availability: use "tq" .* or "all"'):
            search.find_modifiers(zealot_eft, ["tank.ehp.total"], availability=wrong)


def test_t8_bursts_are_measured_from_the_strongest_source(booted, no_fits_left):
    result = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["command_burst"],
                                   expand=["*"])
    row = next(c for c in result["candidates"]
               if c["name"] == "Shield Command Burst II + Shield Harmonizing Charge")
    assert any(n.startswith(("measured from Simurgh", "measured from Ymir"))
               for n in row["notes"])
    vulture = "[Vulture, b]\n\n\nShield Command Burst II, Shield Harmonizing Charge\n"
    by_vulture = (_ehp("[Wyvern, x]\n", {"command": [{"fit": vulture}]})
                  - _ehp("[Wyvern, x]\n", None))
    assert row["delta"]["tank.ehp.total"] > by_vulture


FLEET_ONLY = {"slots": [], "command": True, "phenomena": True}


def test_t9_the_optimizer_picks_the_fleet(booted, no_fits_left):
    result = search.optimize_fit(wyvern.BEST_LOWS, "tank.ehp.total", allow=FLEET_ONLY,
                                 top_k=1)
    chosen = result["fleet"]
    shield = [b for b in chosen["bursts"] if "Shield Command Burst" in b["module"]]
    assert {"Shield Harmonizing Charge", "Shield Extension Charge"} <= {b["charge"]
                                                                       for b in shield}
    assert all(b["hull"] in ("Simurgh", "Ymir") and b["runners_up"] for b in shield)
    deltas = chosen["phenomena"]["deltas"]
    assert {"none", "Amarr Phenomena Generator", "Caldari Phenomena Generator"} <= set(deltas)
    for text in chosen["booster_fits"]:
        assert evaluate.evaluate(text, None)["validity"]["valid"] is True
    best = result["best"][0]
    vulture = ("[Vulture, b]\n\n\nShield Command Burst II, Shield Harmonizing Charge\n"
               "Shield Command Burst II, Shield Extension Charge\n")
    assert best["objective_value"] > _ehp(wyvern.BEST_LOWS, {"command": [{"fit": vulture}]})
    assert _ehp(best["eft"], best["conditions"]) == pytest.approx(best["objective_value"],
                                                                 rel=1e-12)
    assert result["applied"]["command"].startswith("chosen by the search")
    assert chosen["searched_under"] is True


def test_t10_a_searched_fleet_refuses_a_given_one(booted, zealot_eft):
    with pytest.raises(ValueError, match="allow.command"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", wyvern.CONDITIONS,
                            allow={"command": True})
    with pytest.raises(ValueError, match="allow.phenomena must be true or false"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"phenomena": "yes"})


def test_a_fleet_search_without_a_valid_fit_says_why(booted, no_fits_left):
    short = search.optimize_fit(OVER_PG, "tank.ehp.total", allow={"command": True},
                                budget={"evaluations": 1})
    assert short["best"] == [] and short["fleet"] is None
    assert short["reason"].startswith("the budget ran out")


@pytest.mark.slow
def test_replay_the_hand_tested_wyvern(booted, no_fits_left):
    allow = {"slots": ["high", "mid", "low", "rig"], "implants": True, "boosters": True,
             "module_states": ["active", "overheated"], "command": True, "phenomena": True}
    result = search.optimize_fit("Wyvern", "tank.ehp.total", allow=allow, meta=["all"],
                                 top_k=1)  # the default budget
    assert result["search"]["converged"] is True
    best = result["best"][0]
    assert best["polish"]["converged"] is True
    shield = [b for b in result["fleet"]["bursts"] if "Shield Command Burst" in b["module"]]
    assert shield and all(b["hull"] in ("Simurgh", "Ymir") for b in shield)
    assert result["fleet"]["phenomena"]["chosen"] == "Caldari Phenomena Generator"
    assert not [line for line in best["eft"].splitlines()
                if "Capsuleer" in line or line.startswith("Serenity")]
    assert _ehp(best["eft"], best["conditions"]) == pytest.approx(best["objective_value"],
                                                                 rel=1e-12)
    swaps = search.marginal_swaps(best["eft"], "tank.ehp.total", best["conditions"],
                                  meta=["all"])
    assert swaps["no_improvement_found"] is True


def test_a_search_under_a_fleet_measures_its_start_first(monkeypatch):
    from pyfa_mcp.bench import Trial
    where = ("module", 0)
    a = search.Option("low", 1, None, "active", "A", "g")
    b = search.Option("low", 2, None, "active", "B", "g")
    values = {"k": 1.0, **{x: 0.0 for x in search._FITTING_KEYS}}
    monkeypatch.setattr(search._Search, "_trials",
                        lambda self, edits: [Trial(dict(values), []) for _ in edits])
    main = search._Search("ref", {}, "k", 1, [], {"evaluations": 1}, float("inf"),
                          [where], {where: "low"}, {"low": [a, b]})
    under, finished = search._improve_under(main, {"command": []}, {where: a}, [])
    assert finished is False  # one evaluation: the start, then the budget is gone
    assert under.canonical({where: a}) in under.seen


def test_best_is_ordered_by_its_confirmed_value():
    best = [{"objective_value": 1.0, "valid": True}, {"objective_value": 3.0, "valid": True},
            {"objective_value": 9.0, "valid": False}]
    assert [b["objective_value"] for b in search._ordered(best, 1)] == [3.0, 1.0, 9.0]
    assert [b["objective_value"] for b in search._ordered(best, -1)] == [1.0, 3.0, 9.0]


def test_compact_diff_shows_heat_differences():
    eft_text = "[Rifter, x]\nArmor EM Hardener II\n"
    hot = {"eft": eft_text, "objective_value": 2.0, "valid": True,
           "conditions": {"module_states": [{"module": "Armor EM Hardener II",
                                             "state": "overheated", "count": 1}]}}
    cold = {"eft": eft_text, "objective_value": 1.0, "valid": True, "conditions": {}}
    diff = search._diff(hot, cold)["diff"]
    assert diff["remove"] == [] and diff["add"] == []
    assert diff["module_states"] == []
    assert "module_states" not in search._diff(hot, hot)["diff"]


def test_converged_counts_the_polish(booted, zealot_eft, no_fits_left, monkeypatch):
    real = search._polish

    def starved(s, options, budget, started):
        out = real(s, options, budget, started)
        s.stopped_by = "evaluations"
        return {**out, "converged": False}

    monkeypatch.setattr(search, "_polish", starved)
    result = search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"slots": ["low"]},
                                 top_k=1)
    assert result["search"]["converged"] is False
    assert "budget.evaluations" in result["search"]["note"]


def test_fleet_says_whether_the_fit_was_searched_under_it(booted, zealot_eft, no_fits_left):
    short = search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"command": True},
                                top_k=1, budget={"evaluations": 50})
    assert short["fleet"]["searched_under"] is False


@pytest.fixture
def price_is_type_id(monkeypatch):
    """Every item costs its type ID in ISK: prices become checkable by arithmetic."""
    from pyfa_mcp import prices
    monkeypatch.setattr(prices, "price", lambda type_id: float(type_id))


def _id(name):
    import eos.db
    return eos.db.getItem(name).ID


def test_p9_find_modifiers_rows_carry_prices(booted, no_fits_left, price_is_type_id):
    result = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["module"],
                                   expand=["*"])
    rows = result["candidates"]
    assert rows and all(r["price"] == r["type_id"] for r in rows if r["charge"] is None)
    by_name = {r["name"]: r["price"] for r in rows}
    for g in result["groups"]:
        assert g["best"]["price"] == by_name[g["best"]["name"]]
        if g["reference"] is not None:
            assert g["reference"]["price"] == by_name[g["reference"]["name"]]
    assert "price_source" in result


def test_review_an_implant_set_costs_all_its_pieces(booted, price_is_type_id):
    from pyfa_mcp.bench import Edit
    from pyfa_mcp.candidates import Candidate
    a, b = _id("High-grade Crystal Alpha"), _id("High-grade Crystal Beta")
    the_set = Candidate(name="High-grade Crystal set", source="implant", slot="implant 1",
                        group="Implant sets", meta="Faction", type_id=a,
                        edits=(Edit(("implant", 1), a), Edit(("implant", 2), b)))
    assert search._candidate_price(the_set) == a + b


def test_p7_marginal_swaps_isk_delta(booted, no_fits_left, price_is_type_id):
    result = search.marginal_swaps(LOOSE, "tank.ehp.total", include_empty_slots=False,
                                   top_n=50)
    swaps = result["swaps"]
    assert swaps
    for s in swaps:
        new = 0.0 if s["add"] is None else _id(s["add"])
        old = 0.0 if s["remove"] is None else _id(s["remove"])
        assert s["isk_delta"] == new - old, s
    assert "price_source" in result


def test_p7_unpriced_side_gives_null(booted, no_fits_left, monkeypatch):
    from pyfa_mcp import prices
    extender = _id("Small Core Defense Field Extender I")
    monkeypatch.setattr(prices, "price",
                        lambda type_id: None if type_id == extender else float(type_id))
    swaps = search.marginal_swaps(LOOSE, "tank.ehp.total", include_empty_slots=False,
                                  top_n=50)["swaps"]
    assert swaps and all(s["isk_delta"] is None for s in swaps)  # every swap replaces it


def test_p9_optimize_fit_best_carries_its_price(booted, zealot_eft, no_fits_left,
                                                price_is_type_id):
    out = search.optimize_fit(zealot_eft, "tank.ehp.total", budget={"evaluations": 50},
                              top_k=2)
    best = out["best"][0]
    assert best["price_total"] == evaluate.evaluate(best["eft"], best["conditions"])[
        "price"]["total"]
    assert "price_partial" not in best
    for diff in out["best"][1:]:
        assert "price_total" in diff
    assert "price_source" in out
