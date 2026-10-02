import pytest

from pyfa_mcp import eft


def _delete(fit):
    from service.fit import Fit
    Fit.deleteFit(fit.ID)


def test_round_trip(booted, zealot_eft, no_fits_left):
    fit = eft.import_fit(zealot_eft)
    try:
        text = eft.export_fit(fit)
        assert text.splitlines()[0] == "[Zealot, Test Zealot]"
        assert text.count("Heavy Pulse Laser II, Scorch M") == 5
        assert fit.getTotalDps().total > 0  # charges loaded, fit calculated
    finally:
        _delete(fit)


def test_name_and_temp_flag(booted, zealot_eft, no_fits_left):
    from pyfa_mcp.eosboot import TEMP_NOTE
    fit = eft.import_fit(zealot_eft, name="Renamed", temp=True)
    try:
        assert fit.name == "Renamed"
        assert fit.notes == TEMP_NOTE
    finally:
        _delete(fit)


@pytest.mark.parametrize("text, bad", [
    ("[Zealout, x]\nHeat Sink II\n", "Zealout"),
    ("[Zealot, x]\nHeat Sinkk II\nDamage Control II\n", "Heat Sinkk II"),
    ("[Zealot, x]\nHeavy Pulse Laser II, Scorch Medium\n", "Scorch Medium"),
    ("[Zealot, x]\n\nHobgoblin III x1\n", "Hobgoblin III"),
    ("[Zealot, x]\n\nStrong Blue Pill Boostr\n", "Strong Blue Pill Boostr"),
])
def test_unknown_names_are_errors_and_leave_nothing(booted, no_fits_left, text, bad):
    with pytest.raises(eft.EftError) as caught:
        eft.import_fit(text)
    assert f"'{bad}'" in str(caught.value)


def test_unknown_name_comes_with_suggestions(booted, no_fits_left):
    with pytest.raises(eft.EftError, match="did you mean: .*Heat Sink II"):
        eft.import_fit("[Zealot, x]\nHeat Sinkk II\n")


def test_every_bad_line_is_reported(booted, no_fits_left):
    with pytest.raises(eft.EftError) as caught:
        eft.import_fit("[Zealot, x]\nHeat Sinkk II\nDamage Contrl II\n")
    assert "Heat Sinkk II" in str(caught.value)
    assert "Damage Contrl II" in str(caught.value)


@pytest.mark.parametrize("text", ["Heat Sink II\n", "", "   \n", "[Zealot]\n"])
def test_missing_header_is_an_error(booted, no_fits_left, text):
    with pytest.raises(eft.EftError, match="header"):
        eft.import_fit(text)


def test_offline_modules_survive(booted, no_fits_left):
    from eos.const import FittingModuleState
    fit = eft.import_fit("[Zealot, x]\nHeat Sink II /OFFLINE\n")
    try:
        assert fit.modules[0].state == FittingModuleState.OFFLINE
    finally:
        _delete(fit)


def test_modules_pyfa_drops_are_recorded(booted, no_fits_left):
    # Pyfa's importer silently skips modules that do not fit the hull.
    fit = eft.import_fit("[Zealot, over]\n" + "Heat Sink II\n" * 9)
    try:
        assert [m.name for m in fit.dropped_modules] == ["Heat Sink II"] * 2
        assert fit.dropped_modules[0].reason == "no free low slot"
    finally:
        _delete(fit)


def test_nothing_dropped_from_a_valid_fit(booted, zealot_eft, no_fits_left):
    fit = eft.import_fit(zealot_eft)
    try:
        assert fit.dropped_modules == []
    finally:
        _delete(fit)


def test_looks_like_eft():
    assert eft.looks_like_eft("\n  [Zealot, x]\n")
    assert not eft.looks_like_eft("My Zealot")
    assert not eft.looks_like_eft("12")
