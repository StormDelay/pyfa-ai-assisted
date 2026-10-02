from pathlib import Path

import pytest

from pyfa_mcp import eosboot


def test_boot_points_eos_at_our_database_only(booted):
    import eos.config
    import eos.db

    ours = (booted / "saveddata.db").resolve()
    url = str(eos.db.saveddata_engine.url).removeprefix("sqlite:///")
    assert Path(url).resolve() == ours
    assert Path.home() / ".pyfa" not in Path(url).resolve().parents
    assert eos.config.saveddata_connectionstring.endswith("saveddata.db")
    assert (booted / "saveddata.db").is_file()


def test_reused_pyfa_modules_imported(booted):
    import sys

    for name in ("service.fit", "service.port", "service.port.eft",
                 "graphs.data", "gui.fitCommands.helpers"):
        assert name in sys.modules


def test_boot_is_idempotent(booted):
    assert eosboot.boot(booted) == booted


def test_second_boot_with_other_dir_is_refused(booted, tmp_path):
    with pytest.raises(eosboot.BootError):
        eosboot.boot(tmp_path)


def test_temp_fits_are_purged_on_boot(booted):
    import eos.db
    from service.fit import Fit
    from service.port import Port

    _, fits = Port.importFitFromBuffer("[Rifter, leftover]\n")
    fits[0].notes = eosboot.TEMP_NOTE
    eos.db.commit()
    eosboot._purge_temp_fits()
    assert all(f.notes != eosboot.TEMP_NOTE for f in Fit.getAllFits())


def test_pyfa_version(booted):
    assert eosboot.pyfa_version() == "v2.69.0"
