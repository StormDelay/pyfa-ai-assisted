from pyfa_mcp import evaluate, notes


def _notes(eft, cond=None):
    return evaluate.evaluate(eft, cond)["notes"]


def test_propmod_note_threshold():
    assert notes.propmod_note("x", 500, 15e6, 110e6)       # gain 68% of 500%: fires
    assert notes.propmod_note("x", 500, 150e6, 155e6) is None
    assert notes.propmod_note("x", 135, 150e6, 63e6) is None  # oversized beats its rating


def test_t6_undersized_mwd_on_a_battleship(booted, no_fits_left):
    (note,) = _notes("[Rokh, small mwd]\n50MN Microwarpdrive II\n")
    assert note.startswith("50MN Microwarpdrive II gives +") and "rated +" in note


def test_t6_right_and_oversized_propmods_are_quiet(booted, no_fits_left):
    assert _notes("[Rokh, big mwd]\n500MN Microwarpdrive II\n") == []
    assert _notes("[Hurricane, big ab]\n100MN Afterburner II\n") == []
    assert _notes("[Rifter, frig mwd]\n5MN Microwarpdrive II\n") == []


def test_offline_propmod_still_judged(booted, no_fits_left):
    assert len(_notes("[Rokh, off]\n50MN Microwarpdrive II /OFFLINE\n")) == 1


def test_no_propmod_and_duplicates(booted, no_fits_left):
    assert _notes("[Rokh, none]\n") == []
    assert len(_notes("[Rokh, two]\n50MN Microwarpdrive II\n50MN Microwarpdrive II\n")) == 1


def test_notes_are_not_warnings(booted, no_fits_left):
    result = evaluate.evaluate("[Rokh, small mwd]\n50MN Microwarpdrive II\n", None)
    assert result["warnings"] == [] and len(result["notes"]) == 1


def test_t7_compare_rows_carry_notes(booted, no_fits_left):
    table = evaluate.compare(["[Rokh, a]\n50MN Microwarpdrive II\n",
                              "[Rokh, b]\n500MN Microwarpdrive II\n"], None, None,
                             variants=[{}, {"damage_profile": {"em": 1, "thermal": 0,
                                                               "kinetic": 0, "explosive": 0}}])
    by_fit = {}
    for row in table["rows"]:
        by_fit.setdefault(row["fit"], []).append(len(row["notes"]))
    assert by_fit == {"a": [1, 1], "b": [0, 0]}
