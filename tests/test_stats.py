import pytest

from pyfa_mcp import eft, stats


@pytest.fixture
def zealot(booted, zealot_eft):
    from service.fit import Fit
    fit = eft.import_fit(zealot_eft)
    yield fit
    Fit.deleteFit(fit.ID)


def test_shape(zealot):
    s = stats.fit_stats(zealot, spool=1.0)
    assert set(s) == {"validity", "tank", "offense", "capacitor",
                      "navigation", "targeting", "drones"}
    assert s["validity"]["valid"] is True
    assert s["validity"]["problems"] == []
    assert s["validity"]["slots"]["low"] == {"used": 5, "total": 7}
    assert s["validity"]["hardpoints"]["turret"] == {"used": 5, "total": 5}
    assert s["tank"]["ehp"]["total"] == pytest.approx(
        sum(s["tank"]["ehp"][k] for k in ("shield", "armor", "hull")))
    assert 0 < s["tank"]["resists"]["armor"]["em"] < 1
    assert s["offense"]["dps"]["total"] > 300
    assert s["targeting"]["lock_range_m"] > 0
    assert s["navigation"]["max_speed"] > 0


def test_overfit_is_reported_not_raised(booted, no_fits_left):
    from service.fit import Fit
    fit = eft.import_fit("[Zealot, over]\n" + "Heat Sink II\n" * 9)
    try:
        s = stats.fit_stats(fit, spool=1.0)
        assert s["validity"]["valid"] is False
        assert s["validity"]["problems"] == [
            "Heat Sink II was left out: no free low slot"] * 2
    finally:
        Fit.deleteFit(fit.ID)


def test_powergrid_overuse_is_a_problem(booted, no_fits_left):
    from service.fit import Fit
    # Battleship guns on a frigate: far over powergrid, whatever the skills.
    fit = eft.import_fit("[Rifter, pg]\n\n\n" + "Neutron Blaster Cannon II\n" * 3)
    try:
        s = stats.fit_stats(fit, spool=1.0)
        pg = s["validity"]["powergrid"]
        assert pg["used"] > pg["total"]
        assert any("powergrid" in p for p in s["validity"]["problems"])
    finally:
        Fit.deleteFit(fit.ID)


def test_flatten(zealot):
    flat = stats.flatten(stats.fit_stats(zealot, spool=1.0))
    assert "tank.ehp.total" in flat
    for key in stats.DEFAULT_COMPARE:
        assert key in flat, key
