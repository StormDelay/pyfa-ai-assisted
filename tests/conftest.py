import pytest

from pyfa_mcp import eosboot

ZEALOT = """[Zealot, Test Zealot]
Heat Sink II
Heat Sink II
Damage Control II
Multispectrum Energized Membrane II
Medium Armor Repairer II

50MN Microwarpdrive II
Warp Disruptor II
Stasis Webifier II

Heavy Pulse Laser II, Scorch M
Heavy Pulse Laser II, Scorch M
Heavy Pulse Laser II, Scorch M
Heavy Pulse Laser II, Scorch M
Heavy Pulse Laser II, Scorch M

Medium Energy Locus Coordinator II
Medium Energy Metastasis Adjuster II
"""


@pytest.fixture(scope="session")
def booted(tmp_path_factory):
    """eos can be booted once per process, so the whole run shares one."""
    return eosboot.boot(tmp_path_factory.mktemp("pyfa-mcp-data"))


@pytest.fixture
def zealot_eft():
    return ZEALOT


@pytest.fixture
def no_fits_left(booted):
    """Fails the test if it left any fit (temporary or not) in the DB."""
    from service.fit import Fit

    before = {f.ID for f in Fit.getAllFits()}
    yield
    after = {f.ID for f in Fit.getAllFits()}
    assert after == before, f"fits left behind: {after - before}"


@pytest.fixture(scope="session", autouse=True)
def _no_real_pyfa(tmp_path_factory):
    """No test may read or write the real ~/.pyfa: point pyfadata at an empty dir."""
    from pyfa_mcp import pyfadata
    pyfadata.set_dir(tmp_path_factory.mktemp("no-pyfa"))


@pytest.fixture
def pyfa_home(booted, tmp_path, zealot_eft):
    """A Pyfa data dir whose saveddata.db is a copy of ours holding one fit,
    'Home Zealot', a damage profile 'Home EM' and a target profile 'Home Target'."""
    import contextlib
    import sqlite3

    from eos.saveddata.character import Character
    from pyfa_mcp import pyfadata, store

    Character.getAll5()  # a fresh server DB has no characters; a real Pyfa DB always has
    entry = store.save_fit(zealot_eft, "Home Zealot")
    home = tmp_path / "pyfa"
    home.mkdir()
    try:
        with contextlib.closing(sqlite3.connect(booted / "saveddata.db")) as src, \
                contextlib.closing(sqlite3.connect(home / "saveddata.db")) as dst:
            src.backup(dst)
            dst.execute("INSERT INTO damagePatterns (name, emAmount, thermalAmount, "
                        "kineticAmount, explosiveAmount) VALUES ('Home EM', 1, 0, 0, 0)")
            dst.execute("INSERT INTO targetResists (name, emAmount, thermalAmount, "
                        "kineticAmount, explosiveAmount, signatureRadius) "
                        "VALUES ('Home Target', 0.5, 0.5, 0.5, 0.5, 40)")
            dst.commit()
    finally:
        store.delete_fit(str(entry["id"]))
    previous = pyfadata.pyfa_dir()
    pyfadata.set_dir(home)
    yield home
    pyfadata.close()
    pyfadata.set_dir(previous)
