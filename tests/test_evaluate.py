import pytest

from pyfa_mcp import conditions, eft, evaluate
from tests import wyvern


def test_evaluate_shape(booted, zealot_eft, no_fits_left):
    result = evaluate.evaluate(zealot_eft, None)
    assert result["fit"] == "Test Zealot"
    assert result["ship"] == "Zealot"
    assert result["applied"]["character"] == "All 5 (default)"
    assert result["warnings"] == []
    assert result["validity"]["valid"] is True
    assert result["offense"]["dps"]["total"] > 0


def test_evaluate_projected_fit_cleans_up(booted, zealot_eft, no_fits_left):
    result = evaluate.evaluate(zealot_eft, {"projected": [
        {"fit": "[Scimitar, logi]\nLarge Remote Shield Booster II\n"}]})
    assert "logi" in result["applied"]["projected"][0]


def test_failure_midway_leaves_nothing(booted, zealot_eft, no_fits_left):
    with pytest.raises(eft.EftError):
        evaluate.evaluate(zealot_eft, {"projected": [{"fit": "[Scimitar, x]\nBad Module\n"}]})


def test_conditions_error_leaves_nothing(booted, zealot_eft, no_fits_left):
    with pytest.raises(conditions.ConditionsError):
        evaluate.evaluate(zealot_eft, {"module_states": [
            {"module": "Damage Control II", "state": "overheated"}]})


def test_custom_profile_leaves_nothing(booted, zealot_eft, no_fits_left):
    import eos.db
    evaluate.evaluate(zealot_eft, {"damage_profile":
                                   {"em": 1, "thermal": 0, "kinetic": 0, "explosive": 0}})
    with eos.db.saveddata_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM damagePatterns").scalar() == 0


def test_invalid_fit_still_evaluated(booted, no_fits_left):
    result = evaluate.evaluate("[Zealot, over]\n" + "Heat Sink II\n" * 9, None)
    assert result["validity"]["valid"] is False
    assert result["warnings"] == ["fit is not valid: " + "; ".join(result["validity"]["problems"])]


def test_compare_keeps_going_past_a_broken_fit(booted, zealot_eft, no_fits_left):
    table = evaluate.compare(
        [zealot_eft, "[Zealot, broken]\nHeat Sinkk II\n", "[Rifter, small]\n"], None, None)
    assert table["columns"][0] == "fit"
    labels = [row["fit"] for row in table["rows"]]
    assert labels[0] == "Test Zealot" and labels[2] == "small"
    assert "Heat Sinkk II" in table["rows"][1]["error"]
    assert table["rows"][0]["tank.ehp.total"] > table["rows"][2]["tank.ehp.total"]


def test_compare_selected_keys(booted, zealot_eft, no_fits_left):
    table = evaluate.compare([zealot_eft], None, ["offense.dps.total"])
    assert table["columns"] == ["fit", "offense.dps.total"]


def test_compare_unknown_key(booted, zealot_eft, no_fits_left):
    with pytest.raises(ValueError, match="unknown stat"):
        evaluate.compare([zealot_eft], None, ["offense.nope"])


def test_compare_bad_conditions_fail_whole_call(booted, zealot_eft, no_fits_left):
    with pytest.raises(conditions.ConditionsError):
        evaluate.compare([zealot_eft], {"bogus": 1}, None)


def test_compare_isolates_any_failure(booted, zealot_eft, no_fits_left, monkeypatch):
    real = evaluate._evaluate_parsed

    def flaky(ref, cond):
        if "boom" in ref:
            raise RuntimeError("Pyfa fell over")
        return real(ref, cond)

    monkeypatch.setattr(evaluate, "_evaluate_parsed", flaky)
    table = evaluate.compare([zealot_eft, "[Rifter, boom]\n"], None, None)
    assert table["rows"][1]["error"] == "RuntimeError: Pyfa fell over"
    assert "tank.ehp.total" in table["rows"][0]


def test_projected_drones_leave_nothing(booted, zealot_eft, no_fits_left):
    import eos.db
    evaluate.evaluate(zealot_eft, {"projected": [{"item": "Berserker TP-900", "count": 2}]})
    with eos.db.saveddata_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM drones").scalar() == 0


def test_t11_one_fit_under_five_fleets(booted, no_fits_left):
    hulls = ["Vulture", "Nighthawk", "Claymore", "Sleipnir", "Simurgh"]
    variants = [{"command": [{"fit": f"[{h}, b]\n\n\n"
                              "Shield Command Burst II, Shield Harmonizing Charge\n"}]}
                for h in hulls]
    result = evaluate.compare([wyvern.BRIEF], None, ["tank.ehp.total"], variants)
    assert [r["variant"] for r in result["rows"]] == [0, 1, 2, 3, 4]
    assert "Simurgh" in result["rows"][4]["variant_label"]
    assert result["rows"][4]["tank.ehp.total"] > result["rows"][0]["tank.ehp.total"]
    assert result["columns"] == ["fit", "variant", "tank.ehp.total"]


def test_variants_must_be_a_list_of_objects(booted):
    for wrong in ([], ["uniform"]):
        with pytest.raises(ValueError, match="variants"):
            evaluate.compare([wyvern.BRIEF], None, None, wrong)


ZEALOT_ITEMS = ["Zealot", "Heat Sink II", "Damage Control II",
                "Multispectrum Energized Membrane II", "Medium Armor Repairer II",
                "50MN Microwarpdrive II", "Warp Disruptor II", "Stasis Webifier II",
                "Heavy Pulse Laser II", "Medium Energy Locus Coordinator II",
                "Medium Energy Metastasis Adjuster II"]


def test_p6_fit_price_buckets(booted, zealot_eft, seed_prices, no_fits_left):
    seed_prices({**{n: 1e6 for n in ZEALOT_ITEMS}, "Scorch M": 100.0})
    result = evaluate.evaluate(zealot_eft, None)
    price = result["price"]
    # 5 low + 3 mid + 5 high + 2 rigs; one Scorch M per laser
    assert price == {"total": 16e6 + 500.0, "hull": 1e6, "modules": 15e6, "charges": 500.0,
                     "drones": 0.0, "implants": 0.0, "cargo": 0.0}
    assert result["price_source"] == "fuzzwork Forge sell, 0.0 days old"


def test_p6_partial_fit_price(booted, zealot_eft, seed_prices, no_fits_left):
    seed_prices({**{n: 1e6 for n in ZEALOT_ITEMS if n != "Stasis Webifier II"},
                 "Scorch M": 100.0})
    price = evaluate.evaluate(zealot_eft, None)["price"]
    assert price["total"] == 15e6 + 500.0 and price["modules"] == 14e6
    assert price["partial"] is True and price["unpriced"] == ["Stasis Webifier II"]


def test_fit_price_counts_drones_and_cargo(booted, seed_prices, no_fits_left):
    seed_prices({"Vexor": 1e7, "Hobgoblin II": 1e5, "Nanite Repair Paste": 1e4})
    # drones and cargo in separate sections, or the importer puts both in cargo
    fit = "[Vexor, d]\n\nHobgoblin II x5\n\nNanite Repair Paste x10\n"
    price = evaluate.evaluate(fit, None)["price"]
    assert price["drones"] == 5e5 and price["cargo"] == 1e5
    assert price["total"] == 1e7 + 5e5 + 1e5 and "partial" not in price


def test_p8_compare_has_a_price_column(booted, zealot_eft, seed_prices, no_fits_left):
    seed_prices({"Zealot": 1e6})
    out = evaluate.compare([zealot_eft], None, None)
    assert "price.total" in out["columns"]
    row = out["rows"][0]
    assert row["price.total"] == 1e6
    assert row["price_partial"] is True and "Heat Sink II" in row["unpriced"]
    assert out["price_source"].startswith("fuzzwork Forge sell")


def test_unseeded_prices_are_null_not_zero(booted, zealot_eft, no_fits_left):
    from pyfa_mcp import prices
    prices.configure(None)
    price = evaluate.evaluate(zealot_eft, None)["price"]
    assert price["total"] == 0.0 and price["partial"] is True
    assert "Zealot" in price["unpriced"]
