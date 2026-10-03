import pytest

from pyfa_mcp import candidates, evaluate


def _pool(text, sources, meta=None):
    with evaluate.Scratch() as scratch:
        fit = scratch.add_fit(text)
        return candidates.build(fit, set(sources), meta)


def test_pool_has_legal_modules_and_explains_the_rest(booted, no_fits_left):
    pool = _pool("[Wyvern, x]\n", ["module"], ["all"])
    names = {c.name for c in pool.candidates}
    assert "Chelm's Modified Power Diagnostic System" in names
    assert "Capital Shield Extender II" in names
    adc = [e for e in pool.excluded if e["name"] == "Assault Damage Control II"]
    assert adc and adc[0]["reason"].startswith("cannot be fitted to")
    small = [e for e in pool.excluded if e["name"] == "Large Shield Extender II"]
    assert not small  # sub-capital modules fit capitals
    assert all(c.source == "module" for c in pool.candidates)


def test_default_meta_hides_officer_and_deadspace(booted, no_fits_left):
    pool = _pool("[Wyvern, x]\n", ["module"])
    assert not [c for c in pool.candidates if c.meta in ("Officer", "Deadspace")]
    assert "meta=[\"all\"]" in pool.meta_note
    assert any(e["reason"] == "meta Officer not requested" for e in pool.excluded)


def test_rigs_charges_and_identical_items(booted, no_fits_left):
    pool = _pool("[Chimera, x]\n", ["rig", "charge"], ["all"])
    rigs = [c for c in pool.candidates if c.source == "rig"]
    assert rigs and all(c.slot == "rig" for c in rigs)
    assert all("Capital" in c.name for c in rigs)
    assert any(e["reason"] == "rig size does not match the hull" for e in pool.excluded)
    pairs = {c.name for c in pool.candidates if c.source == "charge"}
    assert "Sensor Booster II + Targeting Range Script" in pairs


def test_pod_sets_and_external_sources(booted, no_fits_left):
    pool = _pool("[Wyvern, x]\n", ["implant", "booster", "command_burst", "phenomena",
                                   "projected", "environment"], ["all"])
    by_name = {c.name: c for c in pool.candidates}
    nirvana = by_name["High-grade Nirvana set"]
    assert nirvana.group == "Implant sets" and len(nirvana.edits) == 6
    assert by_name["Zainou 'Gnome' Shield Management SM-706"].slot == "implant 7"
    burst = by_name["Shield Command Burst II + Shield Extension Charge"]
    assert burst.extra["command"][0]["fit"].startswith(("[Simurgh,", "[Ymir,"))
    assert burst.note.startswith("measured from")
    assert "Leviathan" in by_name["Caldari Phenomena Generator"].extra["command"][0]["fit"]
    assert by_name["Class 6 Pulsar Effects"].edits[0].state == "online"
    assert by_name["Stasis Webifier II"].source == "projected"


def test_unknown_source():
    with pytest.raises(ValueError, match="unknown 'modules'"):
        candidates.parse_sources(["modules"])
