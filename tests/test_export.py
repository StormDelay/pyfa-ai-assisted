import contextlib
import hashlib
import sqlite3
from pathlib import Path

import pytest

from pyfa_mcp import eft, pyfadata, store


@pytest.fixture(autouse=True)
def _pyfa_not_running(monkeypatch):
    monkeypatch.setattr(pyfadata, "_pyfa_running", lambda: False)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refused(home: Path, match: str, call) -> None:
    """The call fails, Pyfa's file is byte-identical, and no backup was made."""
    db = home / "saveddata.db"
    before = _digest(db)
    with pytest.raises((store.StoreError, pyfadata.PyfaDataError, eft.EftError), match=match):
        call()
    assert _digest(db) == before
    assert not list(home.glob("*backup*"))


def test_export_inserts_a_new_fit_after_a_backup(pyfa_home, zealot_eft, no_fits_left):
    before = _digest(pyfa_home / "saveddata.db")
    result = store.export_to_pyfa(zealot_eft, "Exported Zealot")
    assert result["name"] == "Exported Zealot" and result["ship"] == "Zealot"
    assert _digest(Path(result["backup"])) == before
    assert sorted(e["name"] for e in store.list_fits(source="pyfa")) == \
        ["Exported Zealot", "Home Zealot"]
    assert store.get_fit(result["id"])["eft"].startswith("[Zealot, Exported Zealot]")


def test_export_never_overwrites(pyfa_home, zealot_eft, no_fits_left):
    assert store.export_to_pyfa(zealot_eft, "Home Zealot")["name"] == "Home Zealot (2)"
    assert store.export_to_pyfa(zealot_eft, "home zealot")["name"] == "home zealot (3)"
    assert len(store.list_fits(source="pyfa")) == 3


def test_export_keeps_the_eft_name_by_default(pyfa_home, zealot_eft, no_fits_left):
    assert store.export_to_pyfa(zealot_eft)["name"] == "Test Zealot"


def test_export_a_stored_or_pyfa_fit(pyfa_home, no_fits_left):
    assert store.export_to_pyfa("pyfa:Home Zealot")["name"] == "Home Zealot (2)"


def test_refused_while_pyfa_runs(pyfa_home, zealot_eft, monkeypatch, no_fits_left):
    monkeypatch.setattr(pyfadata, "_pyfa_running", lambda: True)
    _refused(pyfa_home, "Pyfa is running", lambda: store.export_to_pyfa(zealot_eft))


def test_refused_on_another_schema_version(pyfa_home, zealot_eft, no_fits_left):
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        db.execute("PRAGMA user_version = 49")
        db.commit()
    _refused(pyfa_home, "EFT", lambda: store.export_to_pyfa(zealot_eft))


def test_refused_for_an_unknown_item(pyfa_home, no_fits_left):
    _refused(pyfa_home, "Heat Sinkk II",
             lambda: store.export_to_pyfa("[Zealot, typo]\nHeat Sinkk II\n"))


def test_refused_for_a_trimmed_fit(pyfa_home, no_fits_left):
    over = "[Zealot, over]\n\n\n" + "Heavy Pulse Laser II\n" * 7
    _refused(pyfa_home, "left out", lambda: store.export_to_pyfa(over))


def test_refused_without_pyfa(booted, zealot_eft, no_fits_left):
    with pytest.raises(pyfadata.PyfaDataError, match="no Pyfa install"):
        store.export_to_pyfa(zealot_eft)
