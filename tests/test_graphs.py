import pytest

from pyfa_mcp import graphs


def test_describe_has_lock_time(booted):
    d = graphs.describe()
    assert set(graphs.GRAPHS) <= set(d)
    lock = d["lock_time"]
    assert lock["x"][0]["handle"] == "tgtSigRad"
    assert lock["y"][0]["handle"] == "time"


def test_lock_time_falls_with_signature(booted, zealot_eft, no_fits_left):
    out = graphs.fit_graph(zealot_eft, "lock_time", "tgtSigRad", "time", [25, 500], None, None)
    ys = [p[1] for p in out["points"]]
    assert len(out["points"]) <= 50
    assert ys[0] > ys[-1] > 0
    assert out["x"] == {"handle": "tgtSigRad", "unit": "m"}


def test_damage_vs_distance_with_target(booted, zealot_eft, no_fits_left):
    out = graphs.fit_graph(zealot_eft, "damage", "distance:km", "dps", [0, 60], None,
                           {"target": {"signature": 125, "speed": 0}})
    assert out["points"][0][1] > out["points"][-1][1]


def test_describe_lists_vectors_and_checkboxes(booted):
    d = graphs.describe()
    assert {"atkSpeed", "atkAngle", "tgtSpeed", "tgtAngle"} <= {
        v["handle"] for v in d["damage"]["inputs"]}
    assert "useCapsim" in {c["handle"] for c in d["capacitor"]["inputs"]}


def test_target_speed_input_changes_application(booted, zealot_eft, no_fits_left):
    still = graphs.fit_graph(zealot_eft, "damage", "distance:km", "dps", [5, 6], {"tgtSpeed": 0},
                             {"target": {"signature": 125, "speed": 2000}})
    moving = graphs.fit_graph(zealot_eft, "damage", "distance:km", "dps", [5, 6], {"tgtSpeed": 100},
                              {"target": {"signature": 125, "speed": 2000}})
    assert still["points"][0][1] > moving["points"][0][1]


def test_shield_regen_graph_runs(booted, no_fits_left):
    out = graphs.fit_graph("[Drake, regen]\n", "shield_regen", "time", "shieldAmount",
                           [0, 300], None, None)
    assert out["points"][-1][1] > out["points"][0][1]


def test_unknown_input(booted, zealot_eft):
    with pytest.raises(graphs.GraphError, match="unknown input 'bogus'"):
        graphs.fit_graph(zealot_eft, "lock_time", "tgtSigRad", "time", [25, 500],
                         {"bogus": 1}, None)


def test_unknown_graph(booted, zealot_eft):
    with pytest.raises(graphs.GraphError, match="lock_time"):
        graphs.fit_graph(zealot_eft, "lock", "tgtSigRad", "time", [25, 500], None, None)


def test_unknown_axis(booted, zealot_eft, no_fits_left):
    with pytest.raises(graphs.GraphError, match="tgtSigRad"):
        graphs.fit_graph(zealot_eft, "lock_time", "speed", "time", [25, 500], None, None)


def test_bad_range(booted, zealot_eft):
    with pytest.raises(graphs.GraphError, match="x_range"):
        graphs.fit_graph(zealot_eft, "lock_time", "tgtSigRad", "time", [500], None, None)


def test_graph_carries_fit_warnings(booted, no_fits_left):
    out = graphs.fit_graph("[Zealot, over]\n\n\n" + "Heavy Pulse Laser II\n" * 7,
                           "lock_time", "tgtSigRad", "time", [25, 500], None, None)
    assert out["warnings"] and "left out" in out["warnings"][0]
