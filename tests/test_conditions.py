import pytest

from pyfa_mcp import conditions as C
from pyfa_mcp import eft


@pytest.fixture
def temp_fits(booted):
    """add_fit for apply(); deletes everything it created."""
    from service.fit import Fit
    made = []

    def add_fit(ref):
        fit = eft.import_fit(ref, temp=True)
        made.append(fit.ID)
        return fit

    yield add_fit
    import eos.db
    eos.db.commit()  # as Scratch does: flush items added by conditions first
    for fit_id in reversed(made):  # projected/command fits first
        Fit.deleteFit(fit_id)
    from pyfa_mcp import eosboot
    eosboot.purge_temp_profiles()


def test_parse_defaults():
    cond = C.parse(None)
    assert cond.character == "All 5"
    assert cond.spool is None  # Pyfa's default, resolved by spool_of
    assert cond.damage_profile == "uniform"
    assert cond.explicit == frozenset()


def test_default_spool_is_pyfas(booted):
    import eos.config
    assert C.spool_of(C.parse(None)) == eos.config.settings["globalDefaultSpoolupPercentage"]


@pytest.mark.parametrize("raw, message", [
    ({"bogus": 1}, "unknown condition 'bogus'"),
    ({"character": "Bob"}, "All 5"),
    ({"spool": 1.5}, "spool"),
    ({"spool": "average"}, "spool"),
    ({"damage_profile": {"em": 1}}, "damage_profile"),
    ({"damage_profile": {"em": -1, "thermal": 1, "kinetic": 1, "explosive": 1}}, "damage_profile"),
    ({"target": {"resists": {"em": 2}}}, "resists"),
    ({"module_states": [{"module": "X", "state": "angry"}]}, "state"),
    ({"projected": [{"item": "X", "fit": "Y"}]}, "exactly one"),
    ({"projected": [{"count": 2}]}, "exactly one"),
])
def test_parse_rejects(raw, message):
    with pytest.raises(C.ConditionsError, match=message):
        C.parse(raw)


def test_spool_words():
    assert C.parse({"spool": "min"}).spool == 0.0
    assert C.parse({"spool": "max"}).spool == 1.0
    assert C.parse({"spool": 0.25}).spool == 0.25


def test_apply_defaults_echo(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    applied = C.apply(fit, C.parse(None), temp_fits)
    assert applied["character"] == "All 5 (default)"
    assert applied["spool"].endswith("(default)")
    assert applied["damage_profile"].endswith("(default)")
    assert applied["projected"] == "none (default)"


def test_projected_webs_slow_the_fit(temp_fits, zealot_eft):
    base = temp_fits(zealot_eft)
    C.apply(base, C.parse(None), temp_fits)
    speed = base.maxSpeed
    webbed = temp_fits(zealot_eft)
    applied = C.apply(webbed, C.parse(
        {"projected": [{"item": "Stasis Webifier II", "count": 2}]}), temp_fits)
    assert webbed.maxSpeed < speed / 2
    assert "Stasis Webifier II x2" in applied["projected"][0]


def test_projected_fit(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    applied = C.apply(fit, C.parse({"projected": [
        {"fit": "[Scimitar, logi]\nLarge Remote Shield Booster II\n", "count": 1}]}),
        temp_fits)
    assert [f.name for f in fit.projectedFits] == ["logi"]
    assert "logi" in applied["projected"][0]


def test_overheat_raises_repair(temp_fits, zealot_eft):
    cold = temp_fits(zealot_eft)
    C.apply(cold, C.parse(None), temp_fits)
    hot = temp_fits(zealot_eft)
    C.apply(hot, C.parse({"module_states": [
        {"module": "Medium Armor Repairer II", "state": "overheated"}]}), temp_fits)
    assert hot.tank["armorRepair"] > cold.tank["armorRepair"]


@pytest.mark.parametrize("states, message", [
    ([{"module": "Large Shield Booster II", "state": "active"}], "not fitted"),
    ([{"module": "Heat Sink II", "state": "active", "count": 3}], "only 2"),
    ([{"module": "Damage Control II", "state": "overheated"}], "cannot be overheated"),
])
def test_module_state_errors(temp_fits, zealot_eft, states, message):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match=message):
        C.apply(fit, C.parse({"module_states": states}), temp_fits)


def test_unknown_projected_item_suggests(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="Stasis Webifier II"):
        C.apply(fit, C.parse({"projected": [{"item": "Stasis Webifer II"}]}), temp_fits)


def test_damage_profile_changes_ehp(temp_fits, zealot_eft):
    import eos.db
    uniform = temp_fits(zealot_eft)
    C.apply(uniform, C.parse(None), temp_fits)
    em = temp_fits(zealot_eft)
    C.apply(em, C.parse({"damage_profile":
        {"em": 1, "thermal": 0, "kinetic": 0, "explosive": 0}}), temp_fits)
    assert em.ehp["armor"] != pytest.approx(uniform.ehp["armor"])


def test_custom_profiles_are_purged(booted, zealot_eft):
    import eos.db
    from pyfa_mcp import eosboot
    from service.fit import Fit
    fit = eft.import_fit(zealot_eft, temp=True)
    C.apply(fit, C.parse({
        "damage_profile": {"em": 1, "thermal": 0, "kinetic": 0, "explosive": 0},
        "target": {"signature": 100}}), None)
    eos.db.commit()
    Fit.deleteFit(fit.ID)
    eosboot.purge_temp_profiles()

    def count(table):
        with eos.db.saveddata_engine.connect() as connection:
            return connection.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar()

    assert count("damagePatterns") == 0
    assert count("targetResists") == 0


def test_unknown_damage_profile_name(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="damage profile"):
        C.apply(fit, C.parse({"damage_profile": "Nope"}), temp_fits)


def test_drug_side_effect(temp_fits):
    fit = temp_fits("[Zealot, drug]\n\n\nStrong Blue Pill Booster\n")
    booster = fit.boosters[0]
    effect = booster.sideEffects[0]
    description = effect.name.split(" ", 1)[1]  # "-10% Shield Capacity" -> "Shield Capacity"
    applied = C.apply(fit, C.parse({"drug_side_effects": [
        {"drug": "Strong Blue Pill Booster", "effect": description}]}), temp_fits)
    assert effect.active
    assert "Strong Blue Pill Booster" in applied["drug_side_effects"][0]


def test_drug_not_in_fit(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="not in the fit"):
        C.apply(fit, C.parse({"drug_side_effects": [
            {"drug": "Strong Blue Pill Booster", "effect": "shield"}]}), temp_fits)


def test_command_burst_fit(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    booster_eft = ("[Damnation, boosts]\n\n\n"
                   "Armor Command Burst II, Armor Energizing Charge\n")
    applied = C.apply(fit, C.parse({"command": [{"fit": booster_eft}]}), temp_fits)
    assert [f.name for f in fit.commandFits] == ["boosts"]
    assert "boosts" in applied["command"][0]


HECATE = "[Hecate, modes]\n\n1MN Afterburner II\n"


def test_mode_defaults_to_pyfas_first_mode(temp_fits):
    fit = temp_fits(HECATE)
    applied = C.apply(fit, C.parse(None), temp_fits)
    assert applied["mode"] == f"{fit.mode.item.name} (default)"


def test_mode_changes_the_hull(temp_fits):
    defense = temp_fits(HECATE)
    C.apply(defense, C.parse({"mode": "defense"}), temp_fits)
    sniper = temp_fits(HECATE)
    applied = C.apply(sniper, C.parse({"mode": "Sharpshooter"}), temp_fits)
    assert applied["mode"] == "Hecate Sharpshooter Mode"
    assert sniper.maxTargetRange > defense.maxTargetRange


def test_mode_errors(temp_fits, zealot_eft):
    hecate = temp_fits(HECATE)
    with pytest.raises(C.ConditionsError, match="Sharpshooter"):
        C.apply(hecate, C.parse({"mode": "flying"}), temp_fits)
    zealot = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="no modes"):
        C.apply(zealot, C.parse({"mode": "defense"}), temp_fits)


def test_describe_lists_profiles(booted):
    d = C.describe()
    assert "uniform" in d["damage_profiles"]
    assert d["fields"]["spool"]
    assert d["examples"]


def test_eft_drones_are_launched_by_default(temp_fits):
    fit = temp_fits("[Vexor, drones]\n\n\n\nHammerhead II x5\n")
    applied = C.apply(fit, C.parse(None), temp_fits)
    assert fit.drones[0].amountActive == 5
    assert fit.getDroneDps().total > 0
    assert applied["drones"] == ["Hammerhead II: 5 of 5 launched (default)"]


def test_drone_launch_respects_pyfa_limits(temp_fits):
    fit = temp_fits("[Vexor, drones]\n\n\n\nHammerhead II x5\nHobgoblin II x5\n")
    applied = C.apply(fit, C.parse(None), temp_fits)
    import eos.db  # noqa: F401
    active = sum(d.amountActive for d in fit.drones)
    assert active == fit.extraAttributes["maxActiveDrones"]
    assert fit.droneBandwidthUsed <= fit.ship.getModifiedItemAttr("droneBandwidth")
    assert any("of 5 launched" in line for line in applied["drones"])


def test_overlapping_module_states_use_different_modules(temp_fits, zealot_eft):
    from eos.const import FittingModuleState
    fit = temp_fits(zealot_eft)
    C.apply(fit, C.parse({"module_states": [
        {"module": "Heat Sink II", "state": "offline", "count": 1},
        {"module": "Heat Sink II", "state": "online", "count": 1}]}), temp_fits)
    states = sorted(m.state for m in fit.modules if not m.isEmpty and m.item.name == "Heat Sink II")
    assert states == [FittingModuleState.OFFLINE, FittingModuleState.ONLINE]


def test_module_states_run_out(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="already"):
        C.apply(fit, C.parse({"module_states": [
            {"module": "Heat Sink II", "state": "offline"},
            {"module": "Heat Sink II", "state": "online", "count": 1}]}), temp_fits)


def test_projected_drone_state_is_honoured(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    applied = C.apply(fit, C.parse({"projected": [
        {"item": "Berserker TP-900", "count": 2, "state": "offline"}]}), temp_fits)
    assert fit.projectedDrones[0].amountActive == 0
    assert "(offline)" in applied["projected"][0]


def test_unprojectable_drone_is_an_error(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="cannot be projected"):
        C.apply(fit, C.parse({"projected": [{"item": "Hobgoblin II"}]}), temp_fits)
