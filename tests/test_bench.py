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


def test_run_trials_groups_extra_conditions(booted, no_fits_left):
    trials = [([], None), ([], {"command": [{"fit": wyvern.PHENOMENA}]})]
    plain, boosted = bench.run_trials("[Wyvern, x]\n", None, ["tank.hp.shield"], trials)
    assert boosted.values["tank.hp.shield"] > plain.values["tank.hp.shield"]


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
