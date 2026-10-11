import json

import pytest

from pyfa_mcp import bench, evaluate, stats
from scripts.record_reference import KEYS, REF, _resolve
from tests import wyvern

CASES = json.loads((REF / "conditions.json").read_text())


def _case(name):
    spec = CASES[name]
    return (REF / f"{spec.get('fit', name)}.eft").read_text(), _resolve(spec.get("conditions"))


def _flat(result):
    return stats.flatten({k: v for k, v in result.items() if k not in evaluate.META_KEYS})


def _same(got, want):
    for key in KEYS:
        if isinstance(want[key], float):
            assert got[key] == pytest.approx(want[key], rel=1e-9), key
        else:
            assert got[key] == want[key], key


def _item(type_id):
    import eos.db
    return eos.db.getItem(type_id)


def _id(name):
    from service.market import Market
    return Market.getInstance().getItem(name).ID


@pytest.mark.parametrize("case", sorted(CASES))
def test_bench_matches_evaluator_after_a_change(booted, case, no_fits_left):
    fit, cond = _case(case)
    named = {e["module"].casefold() for e in (cond or {}).get("module_states", [])}
    with bench.Bench(fit, cond) as b:
        _same(b.measure(KEYS), _flat(evaluate.evaluate(fit, cond)))
        where = [w for w in b.module_places() if b.occupant(w) is not None
                 and b.name_of(b.occupant(w)[0]).casefold() not in named][-1]
        edits = [bench.Edit(where, None)]
        trial = b.trial(edits, KEYS)
        undo = b.apply(edits)
        changed, states = b.eft(), b.module_states()
        b.revert(undo)
        _same(b.measure(KEYS), _flat(evaluate.evaluate(fit, cond)))
    assert trial.error is None
    _same(trial.values, _flat(evaluate.evaluate(changed, {**(cond or {}),
                                                          "module_states": states})))


def test_bench_matches_evaluator_for_every_kind_of_edit(booted, no_fits_left):
    with bench.Bench(wyvern.BRIEF, wyvern.HOT) as b:
        plate = next(w for w in b.module_places() if b.occupant(w)
                     and b.name_of(b.occupant(w)[0]) == wyvern.PLATE)
        from eos.saveddata.booster import Booster
        pill = _id("Standard Blue Pill Booster")
        edits = [bench.Edit(plate, _id(wyvern.PDS)),
                 bench.Edit(("booster", Booster(_item(pill)).slot), pill),
                 bench.Edit(("implant", 7), None),
                 bench.Edit(("projected", None), _id("Class 6 Pulsar Effects"), None, "online")]
        trial = b.trial(edits, KEYS)
        undo = b.apply(edits[:3])
        changed, states = b.eft(), b.module_states()
        b.revert(undo)
        _same(b.measure(KEYS), _flat(evaluate.evaluate(wyvern.BRIEF, wyvern.HOT)))
    assert "SM-706" not in changed and changed.count(wyvern.PLATE) == 2
    want = evaluate.evaluate(changed, {**wyvern.CONDITIONS, "module_states": states,
                                       "environment": "Class 6 Pulsar Effects"})
    _same(trial.values, _flat(want))


def test_trial_reports_an_invalid_change(booted, no_fits_left):
    with bench.Bench(wyvern.BRIEF, wyvern.CONDITIONS) as b:
        plate = next(w for w in b.module_places() if b.occupant(w)
                     and b.name_of(b.occupant(w)[0]) == wyvern.PLATE)
        trial = b.trial([bench.Edit(plate, _id("Damage Control II"))], ["tank.ehp.total"])
        assert any("Damage Control" in p for p in trial.problems)
        b.measure(["tank.ehp.total"])
        assert b.problems() == []  # reverted


def test_extra_command_fits_measure_as_their_merged_conditions(booted, no_fits_left):
    """Extra command fits join the open bench for their trials, then leave it."""
    edits = [bench.Edit(("module", 0), _id(wyvern.PDS))]
    phenomena = {"command": [{"fit": wyvern.PHENOMENA}]}
    base = {"command": [{"fit": wyvern.BOOST}]}
    trials = [(edits, None), (edits, phenomena), ([], phenomena), (edits, None),
              ([], {"command": [{"fit": "[Nonexistent Hull, x]"}]})]
    got = bench.run_trials(wyvern.BRIEF, base, KEYS, trials)
    for (e, extra), trial in zip(trials[:4], got):
        with bench.Bench(wyvern.BRIEF, bench.merge_conditions(base, extra)) as fresh:
            want = fresh.trial(e, KEYS)
        assert (trial.values, trial.problems) == (want.values, want.problems)
    assert got[1].values != got[0].values and got[3] == got[0]
    assert got[4].values is None and got[4].error


def test_trial_turns_an_unfittable_item_into_an_error(booted, no_fits_left):
    with bench.Bench(wyvern.BRIEF, wyvern.CONDITIONS) as b:
        before = b.measure(KEYS)
        low = next(w for w in b.module_places() if b.rack(w) == "low")
        trial = b.trial([bench.Edit(low, _id("Capital Armor Plates"))], KEYS)
        assert trial.values is None and trial.error and trial.problems
        assert b.measure(KEYS) == before


def test_implant_edit_on_a_bare_hull_is_measured(booted, no_fits_left):
    with bench.Bench("[Wyvern, x]\n", {}) as b:
        base = b.measure(KEYS)
        b.apply([bench.Edit(("implant", 7), _id("Zainou 'Gnome' Shield Management SM-706"))])
        got, text = b.measure(KEYS), b.eft()
    assert got != base
    _same(got, _flat(evaluate.evaluate(text, None)))


@pytest.fixture
def empty_table(monkeypatch, tmp_path):
    path = tmp_path / "skill_touches.json"
    monkeypatch.setattr(bench, "_touches", None)
    monkeypatch.setattr(bench, "_new", {})
    monkeypatch.setattr(bench, "_table_path", lambda: path)
    return path


@pytest.mark.parametrize("character", [
    {"base": "All 5", "alpha_clone": "Alpha Clone"},
    {"base": "All 0", "skills": {"Shield Upgrades": 3, "Thermal Shield Compensation": 2,
                                 "Acceleration Control": 4, "Afterburner": 3,
                                 "Shield Rigging": 2}}])
def test_bench_runs_the_skills_a_new_item_needs(booted, zealot_eft, character, no_fits_left,
                                               empty_table):
    """Skills that bonus an item by group, not by required skill, still apply to it
    the first time it is measured and after (the bench skips untouched skills)."""
    cond = {"character": character}
    with bench.Bench(zealot_eft, cond) as b:
        b.measure(KEYS)
        mid = next(w for w in b.module_places() if b.rack(w) == "mid")
        rig = next(w for w in b.module_places() if b.rack(w) == "rig")
        for where, name in ((mid, "Thermal Shield Amplifier II"),
                            (mid, "1MN Monopropellant Enduring Afterburner"),
                            (rig, "Medium Core Defense Capacitor Safeguard II")):
            edits = [bench.Edit(where, _id(name))]
            first, again = b.trial(edits, KEYS), b.trial(edits, KEYS)
            undo = b.apply(edits)
            text = b.eft()
            b.revert(undo)
            want = _flat(evaluate.evaluate(text, cond))
            _same(first.values, want)
            _same(again.values, want)


def test_skill_table_is_saved_and_read_back(booted, zealot_eft, empty_table, no_fits_left):
    with bench.Bench(zealot_eft) as b:
        first = b.measure(KEYS)
        assert b._touching is None  # learned with every skill
        assert b.measure(KEYS) == first
        assert 0 < len(b._touching) < 100
    saved = json.loads(empty_table.read_text())
    ship = str(_id("Zealot"))
    assert saved[ship] and str(_id("Heat Sink II")) in saved
    bench._touches = None  # a new process
    with bench.Bench(zealot_eft) as b:
        assert b.measure(KEYS) == first and b._touching is not None


def test_damaged_skill_table_is_learned_again(booted, zealot_eft, empty_table, no_fits_left):
    empty_table.write_text("{not json")
    with bench.Bench(zealot_eft) as b:
        b.measure(KEYS)
    assert str(_id("Zealot")) in json.loads(empty_table.read_text())


def test_trials_in_a_row_measure_as_from_the_fit_as_opened(booted, zealot_eft, no_fits_left):
    """A trial leaves its edits on and the next changes only what differs; each must
    still measure what a fresh bench measures, and the fit comes back as opened."""
    with bench.Bench(zealot_eft) as b:
        low = [w for w in b.module_places() if b.rack(w) == "low"]
        mid = [w for w in b.module_places() if b.rack(w) == "mid"]
        dc, web = _id("Damage Control II"), _id("Stasis Webifier II")
        snake = _id("High-grade Snake Alpha")
        steps = [[bench.Edit(low[0], dc)],
                 [bench.Edit(low[0], dc), bench.Edit(mid[0], web)],
                 [bench.Edit(mid[0], web)],
                 [bench.Edit(low[0], None), bench.Edit(mid[0], web)],
                 [bench.Edit(mid[0], web), bench.Edit(("implant", 1), snake)],
                 [bench.Edit(low[1], _id("Capital Armor Plates"))],  # refused
                 [bench.Edit(mid[0], web), bench.Edit(low[2], dc)],
                 []]
        got = [b.trial(edits, KEYS) for edits in steps]
        text = b.eft()
    for edits, trial in zip(steps, got):
        with bench.Bench(zealot_eft) as fresh:
            want = fresh.trial(edits, KEYS)
        assert (trial.values, trial.problems, trial.error) == \
            (want.values, want.problems, want.error), edits
    with bench.Bench(zealot_eft) as fresh:
        assert text == fresh.eft()
