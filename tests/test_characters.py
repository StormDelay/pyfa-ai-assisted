import contextlib
import sqlite3

import pytest

from pyfa_mcp import characters, eosboot
from pyfa_mcp.eosboot import TEMP_NOTE


def _item_id(name: str) -> int:
    import eos.db
    return eos.db.getItem(name).ID


@pytest.fixture
def pyfa_alt(pyfa_home):
    """pyfa_home plus a Pyfa character 'Home Alt': Gunnery III, Medium Energy
    Turret II, and a row for an item id no skill has (a skill CCP removed)."""
    rows = [(_item_id("Gunnery"), 3), (_item_id("Medium Energy Turret"), 2), (999999999, 5)]
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        cid = db.execute("INSERT INTO characters (name, defaultLevel) "
                         "VALUES ('Home Alt', NULL)").lastrowid
        db.executemany('INSERT INTO characterSkills (characterID, itemID, "_Skill__level") '
                       "VALUES (?, ?, ?)", [(cid, i, lvl) for i, lvl in rows])
        db.commit()
    return pyfa_home


def _temp_characters() -> list:
    import eos.db
    return [c for c in eos.db.getCharacterList() if c.savedName == TEMP_NOTE]


def test_builtin_sheets(booted):
    assert characters.sheet("all 5") == {"base": "All 5", "skills": {},
                                         "alpha_clone": None, "label": "All 5"}
    assert characters.sheet(" All 0 ")["base"] == "All 0"
    assert characters.is_all5(characters.sheet("All 5"))
    assert not characters.is_all5(characters.sheet("All 0"))


def test_a_sheet_overrides_its_base_and_resolves_again_unchanged(booted):
    got = characters.sheet({"base": "All 0", "skills": {"gunnery": 4}})
    assert got == {"base": "All 0", "skills": {"Gunnery": 4}, "alpha_clone": None,
                   "label": "custom (base All 0, 1 skill set)"}
    assert characters.sheet(got) == got
    assert characters.sheet({"skills": {"Gunnery": 0, "Rapid Firing": 1}})["label"] == \
        "custom (base All 5, 2 skills set)"


def test_unknown_skill_suggests(booted):
    with pytest.raises(characters.CharacterError, match="did you mean: Gunnery"):
        characters.sheet({"skills": {"Gunery": 4}})
    with pytest.raises(characters.CharacterError, match="unknown skill 'Heat Sink II'"):
        characters.skill("Heat Sink II")


def test_alpha_clone_names(booted):
    assert characters.sheet({"alpha_clone": "alpha clone"})["alpha_clone"] == "Alpha Clone"
    with pytest.raises(characters.CharacterError, match="known: Alpha Clone"):
        characters.sheet({"alpha_clone": "Omega"})


def test_levels_cover_every_published_skill(booted):
    all5 = characters.levels(characters.sheet("All 5"))
    assert set(all5.values()) == {5} and len(all5) > 300
    some = characters.levels(characters.sheet({"base": "All 0", "skills": {"Gunnery": 2}}))
    assert some["Gunnery"] == 2 and some["Rapid Firing"] == 0


def test_a_pyfa_character_is_a_sheet(pyfa_alt):
    got = characters.sheet("pyfa:home alt")
    assert got == {"base": "All 0", "skills": {"Gunnery": 3, "Medium Energy Turret": 2},
                   "alpha_clone": None, "label": "pyfa:Home Alt"}  # the removed skill is skipped


def test_unknown_pyfa_character_suggests(pyfa_alt):
    with pytest.raises(characters.CharacterError, match="did you mean: Home Alt"):
        characters.sheet("pyfa:Home Alr")


def test_build_and_purge(booted):
    import eos.db
    assert characters.build(characters.sheet("All 5")) is None
    char = characters.build(characters.sheet({"base": "All 0", "skills": {"Gunnery": 4}}))
    assert char.savedName == TEMP_NOTE
    assert char.getSkill("Gunnery").level == 4
    assert char.getSkill("Rapid Firing").level == 0
    eos.db.save(char)
    assert _temp_characters()
    eosboot.purge_temp_profiles()
    assert not _temp_characters()
