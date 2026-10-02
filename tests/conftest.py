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
