import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pyfa_mcp import bench, pool

ROOT = Path(__file__).resolve().parent.parent
KEYS = ["tank.ehp.total", "offense.dps.total"]


@pytest.fixture
def small_pool(booted, monkeypatch):
    before = pool.size()
    monkeypatch.setattr(pool, "INLINE_LIMIT", 0)
    pool.configure(2)
    yield
    pool.configure(before)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_pool_matches_inline(small_pool, zealot_eft, no_fits_left):
    trials = [([bench.Edit(("module", p), None)], None) for p in range(8)]
    pooled = pool.run(zealot_eft, None, KEYS, trials)
    assert pool.describe()["running"] == 2
    assert pooled == bench.run_trials(zealot_eft, None, KEYS, trials)


def test_small_jobs_stay_in_process(booted, zealot_eft, no_fits_left):
    pool.shutdown()
    pool.run(zealot_eft, None, KEYS, [([], None)])
    assert pool.describe()["running"] == 0


def test_idle_pool_shuts_down(small_pool, monkeypatch, zealot_eft):
    monkeypatch.setattr(pool, "IDLE_SECONDS", 0.5)
    pool.run(zealot_eft, None, KEYS, [([], None)])
    deadline = time.monotonic() + 20
    while pool.describe()["running"] and time.monotonic() < deadline:
        time.sleep(0.2)
    assert pool.describe()["running"] == 0


def test_workers_exit_with_their_parent(booted, tmp_path):
    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from pyfa_mcp import eosboot, pool\n"
        "eosboot.boot(Path(sys.argv[1]))\n"
        "pool.INLINE_LIMIT = 0\n"
        "pool.configure(1)\n"
        "pool.run('[Rifter, x]\\n', None, ['tank.ehp.total'], [([], None)])\n"
        "print(*pool.describe()['pids'], flush=True)\n"
        "time.sleep(600)\n")
    parent = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT,
                              stdout=subprocess.PIPE, text=True)
    pids = [int(p) for p in parent.stdout.readline().split()]
    assert pids and all(_alive(p) for p in pids)
    parent.kill()
    parent.wait()
    deadline = time.monotonic() + 30
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert not any(_alive(p) for p in pids)
