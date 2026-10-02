import contextlib
import sqlite3
from pathlib import Path

import pytest

from pyfa_mcp import evaluate, pyfadata, store


def _sql(home: Path, *statements: str) -> None:
    with contextlib.closing(sqlite3.connect(home / "saveddata.db")) as db:
        for statement in statements:
            db.execute(statement)
        db.commit()


def test_tests_never_see_the_real_pyfa(booted):
    assert pyfadata.pyfa_dir() != Path.home() / ".pyfa"


def test_no_pyfa_install(booted):
    assert not pyfadata.installed()
    with pytest.raises(pyfadata.PyfaDataError, match="no Pyfa install"):
        store.list_fits(source="pyfa")
    with pytest.raises(pyfadata.PyfaDataError, match="no Pyfa install"):
        store.resolve_eft("pyfa:Home Zealot")


def test_lists_and_reads_pyfa_fits(pyfa_home):
    entries = store.list_fits(source="pyfa")
    assert [e["name"] for e in entries] == ["Home Zealot"]
    ref = entries[0]["id"]
    assert ref.startswith("pyfa:") and ref[5:].isdigit()
    got = store.get_fit(ref)
    assert got["eft"].startswith("[Zealot, Home Zealot]")
    assert store.get_fit("pyfa:home zealot")["id"] == ref  # names are case-insensitive
    assert store.get_fit("PYFA:Home Zealot")["id"] == ref  # so is the prefix
    assert store.list_fits(source="pyfa", ship="Rifter") == []
    assert "Home Zealot" not in [e["name"] for e in store.list_fits()]  # server list is separate


def test_unknown_source_refused(booted):
    with pytest.raises(store.StoreError, match="source"):
        store.list_fits(source="eve")


def test_pyfa_fit_evaluates(pyfa_home, no_fits_left):
    assert evaluate.evaluate("pyfa:Home Zealot", None)["ship"] == "Zealot"


def test_pyfa_fit_unknown_suggests(pyfa_home):
    with pytest.raises(store.StoreError, match="Home Zealot"):
        store.resolve_eft("pyfa:Home Zealott")


def test_same_named_pyfa_fits_ask_for_an_id(pyfa_home):
    _sql(pyfa_home,
         "CREATE TEMP TABLE twin AS SELECT * FROM fits",
         "UPDATE twin SET ID = ID + 1000",
         "INSERT INTO fits SELECT * FROM twin")
    with pytest.raises(store.StoreError, match=r"use an id: pyfa:\d+, pyfa:\d+"):
        store.resolve_eft("pyfa:Home Zealot")


def test_pyfa_fits_are_read_only(pyfa_home):
    with pytest.raises(store.StoreError, match="read-only"):
        store.delete_fit("pyfa:Home Zealot")


def test_snapshot_follows_pyfa_changes(pyfa_home):
    assert [e["name"] for e in store.list_fits(source="pyfa")] == ["Home Zealot"]
    _sql(pyfa_home, "UPDATE fits SET name = 'Renamed Zealot'")
    assert [e["name"] for e in store.list_fits(source="pyfa")] == ["Renamed Zealot"]


def test_pyfa_file_is_never_written_by_reads(pyfa_home):
    before = (pyfa_home / "saveddata.db").read_bytes()
    store.list_fits(source="pyfa")
    store.get_fit("pyfa:Home Zealot")
    evaluate.evaluate("pyfa:Home Zealot", None)
    assert (pyfa_home / "saveddata.db").read_bytes() == before


def test_older_pyfa_database_is_migrated_on_the_copy(pyfa_home):
    # One schema behind whatever eos is pinned to; Pyfa's upgrades are idempotent.
    from eos.db import migration
    older = migration.getAppVersion() - 1
    _sql(pyfa_home, f"PRAGMA user_version = {older}")
    assert [e["name"] for e in store.list_fits(source="pyfa")] == ["Home Zealot"]
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == older  # the user's file is untouched


def test_newer_pyfa_database_is_refused(pyfa_home):
    _sql(pyfa_home, "PRAGMA user_version = 999")
    with pytest.raises(pyfadata.PyfaDataError, match="update pyfa-mcp"):
        store.list_fits(source="pyfa")


def test_pyfa_fit_with_brackets_in_its_name_evaluates(pyfa_home, no_fits_left):
    _sql(pyfa_home, "UPDATE fits SET name = '[CORP] Home Zealot'")
    assert evaluate.evaluate("pyfa:[CORP] Home Zealot", None)["fit"] == "[CORP] Home Zealot"


def test_what_eft_cannot_carry_is_a_warning(pyfa_home, no_fits_left):
    plain = evaluate.evaluate("pyfa:Home Zealot", None)["warnings"]
    assert not [w for w in plain if "pyfa:" in w]  # uniform, no target, nothing projected
    _sql(pyfa_home,
         "CREATE TEMP TABLE twin AS SELECT * FROM fits",
         "UPDATE twin SET ID = ID + 1000, name = 'Helper'",
         "INSERT INTO fits SELECT * FROM twin",
         "UPDATE fits SET damagePatternID = (SELECT ID FROM damagePatterns WHERE name = 'Home EM'),"
         " targetResistsID = (SELECT ID FROM targetResists WHERE name = 'Home Target')"
         " WHERE name = 'Home Zealot'",
         "INSERT INTO projectedFits (sourceID, victimID, amount, active) SELECT h.ID, z.ID, 1, 1"
         " FROM fits h, fits z WHERE h.name = 'Helper' AND z.name = 'Home Zealot'")
    warnings = " ".join(evaluate.evaluate("pyfa:Home Zealot", None)["warnings"])
    for lost in ("Home EM", "Home Target", "Helper"):
        assert lost in warnings
    assert "Home EM" in " ".join(store.get_fit("pyfa:Home Zealot")["warnings"])


def test_a_corrupt_pyfa_database_is_a_pyfa_data_error(pyfa_home):
    (pyfa_home / "saveddata.db").write_bytes(b"not a database" * 100)
    with pytest.raises(pyfadata.PyfaDataError, match="could not be read"):
        store.list_fits(source="pyfa")


def test_each_process_has_its_own_snapshot(pyfa_home, booted):
    import os
    store.list_fits(source="pyfa")
    snap = booted / f"pyfa-snapshot-{os.getpid()}.db"
    assert snap.exists()
    pyfadata.close()
    assert not snap.exists()


@pytest.mark.parametrize("out, running", [
    # German Windows says "ausgeführt", ü being 0x81 in the console's code page.
    ("INFORMATION: Es werden keine Tasks mit den angegebenen Kriterien ausgef\x81hrt.", False),
    ("pyfa.exe                      1234 Console                    1    250.000 K", True),
])
def test_pyfa_running_reads_tasklist_as_bytes(monkeypatch, out, running):
    import subprocess
    seen = {}

    def fake(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, out.encode("latin-1"), b"")

    monkeypatch.setattr(pyfadata.subprocess, "run", fake)
    monkeypatch.setattr(pyfadata.os, "name", "nt")
    assert pyfadata._pyfa_running() is running
    assert seen["stdin"] == subprocess.DEVNULL and not seen.get("text")
