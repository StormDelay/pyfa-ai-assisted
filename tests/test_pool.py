import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pyfa_mcp import bench, pool
from tests import wyvern

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
    try:
        pids = [int(p) for p in parent.stdout.readline().split()]
        assert pids and all(_alive(p) for p in pids)
        parent.kill()
        parent.wait()
        deadline = time.monotonic() + 30
        while any(_alive(p) for p in pids) and time.monotonic() < deadline:
            time.sleep(0.5)
        assert not any(_alive(p) for p in pids)
    finally:
        parent.kill()
        parent.wait()
        parent.stdout.close()


def test_idle_shutdown_spares_a_running_search(small_pool, zealot_eft):
    pool.run(zealot_eft, None, KEYS, [([], None)] * 8)
    with pool._lock:
        pool._busy += 1
    try:
        pool._idle_shutdown()
        assert pool.describe()["running"] == 2
    finally:
        with pool._lock:
            pool._busy -= 1


@pytest.fixture
def no_kept_bench(booted):
    yield
    pool._drop_kept()


def _empty(*positions):
    return [([bench.Edit(("module", p), None)], None) for p in positions]


def test_a_worker_reuses_its_bench_across_jobs(zealot_eft, no_fits_left, no_kept_bench):
    first, second = _empty(0, 1, 2), _empty(3, 4, 5)
    fresh = bench.run_trials(zealot_eft, None, KEYS, first + second)
    assert pool._work(zealot_eft, None, KEYS, first) == fresh[:3]
    opened = pool._kept[1]
    assert pool._work(zealot_eft, None, KEYS, second) == fresh[3:]
    assert pool._kept[1] is opened


def test_a_new_fit_or_extra_conditions_drop_the_kept_bench(zealot_eft, no_fits_left,
                                                           no_kept_bench):
    rifter = "[Rifter, x]\nDamage Control II\n"
    extra = [([], None), ([], {"command": [{"fit": wyvern.PHENOMENA}]})]
    fresh_rifter = bench.run_trials(rifter, None, KEYS, _empty(0))
    fresh_extra = bench.run_trials(zealot_eft, None, KEYS, extra)
    pool._work(zealot_eft, None, KEYS, _empty(0))
    opened = pool._kept[1]
    assert pool._work(rifter, None, KEYS, _empty(0)) == fresh_rifter
    assert pool._kept[1] is not opened and pool._kept[1]._ref == rifter
    assert pool._work(zealot_eft, None, KEYS, extra) == fresh_extra
    assert pool._kept is None


def test_a_failed_job_never_leaves_its_bench_behind(zealot_eft, no_fits_left, no_kept_bench,
                                                    monkeypatch):
    jobs = _empty(0, 1)
    fresh = bench.run_trials(zealot_eft, None, KEYS, jobs)
    real = bench.Bench._apply_one
    calls = []

    def flaky(self, edit):
        calls.append(edit)
        if len(calls) == 2:
            raise RuntimeError("eos broke mid-trial")
        return real(self, edit)

    monkeypatch.setattr(bench.Bench, "_apply_one", flaky)
    with pytest.raises(RuntimeError, match="mid-trial"):
        pool._work(zealot_eft, None, KEYS, jobs)
    assert pool._kept is None
    monkeypatch.setattr(bench.Bench, "_apply_one", real)
    assert pool._work(zealot_eft, None, KEYS, jobs) == fresh


def test_small_jobs_use_a_running_pool(small_pool, monkeypatch, zealot_eft, no_fits_left):
    pool.run(zealot_eft, None, KEYS, _empty(0))  # INLINE_LIMIT 0: starts the pool
    monkeypatch.setattr(pool, "INLINE_LIMIT", 300)
    sent = []
    monkeypatch.setattr(bench, "run_trials", lambda *a: sent.append(a) or [])
    assert len(pool.run(zealot_eft, None, KEYS, _empty(1, 2))) == 2
    assert not sent  # went to the workers, not in-process
