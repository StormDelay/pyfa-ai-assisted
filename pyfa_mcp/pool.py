"""Worker processes for searches.

eos is single-threaded with one session per process, so a search fans its
trials out to processes that each boot their own eos (~1 s, ~150 MB each).
The pool starts on the first search big enough to need it, stops after
IDLE_SECONDS without one, and each worker exits by itself if the server
process dies.
"""
from __future__ import annotations

import contextlib
import json
import multiprocessing
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from pyfa_mcp import bench, eosboot, pyfadata

INLINE_LIMIT = 300
IDLE_SECONDS = 60.0

_size = max(1, min((os.cpu_count() or 1) - 2, 12))
_executor: ProcessPoolExecutor | None = None
_idle: threading.Timer | None = None
_busy = 0
_lock = threading.Lock()


class PoolError(RuntimeError):
    """A worker died mid-search."""


def configure(workers: int | None) -> None:
    global _size
    if workers is not None:
        if workers < 0:
            raise ValueError("--workers must be 0 or more")
        _size = workers
    shutdown()


def size() -> int:
    return _size


def run(ref: str, raw_conditions: dict | None, keys: list[str], trials: list) -> list:
    # A small job never starts the pool, but uses it when it is already running.
    executor = _start(len(trials) >= INLINE_LIMIT) if _size else None
    if executor is None:
        return bench.run_trials(ref, raw_conditions, keys, trials)
    try:
        step = -(-len(trials) // (_size * 2))
        futures = [executor.submit(_work, ref, raw_conditions, keys,
                                   trials[i:i + step])
                   for i in range(0, len(trials), step)]
        return [t for future in futures for t in future.result()]
    except BrokenProcessPool as exc:
        shutdown()
        raise PoolError("a search worker died; the next call starts fresh ones (start the "
                        "server with --workers 0 to search in-process)") from exc
    finally:
        _release()


_kept: tuple | None = None  # in a worker: (key, open Bench) reused by the next job


def _work(ref: str, raw_conditions: dict | None, keys: list[str], trials: list) -> list:
    """A worker's job. A search sends many jobs on one fit: the bench stays open
    between them (an import with conditions costs ~20 plain trials)."""
    global _kept
    key = json.dumps([ref, raw_conditions], sort_keys=True)
    extra = any(e is not None for _, e in trials)
    if _kept is not None and (_kept[0] != key or extra):
        _drop_kept()
    if extra:
        return bench.run_trials(ref, raw_conditions, keys, trials)
    if _kept is None:
        opened = bench.Bench(ref, raw_conditions)
        _kept = (key, opened.__enter__())
    try:
        return [_kept[1].trial(edits, keys) for edits, _ in trials]
    except BaseException:  # the fit may be left half edited: never measure on it again
        _drop_kept()
        raise


def _drop_kept() -> None:
    global _kept
    if _kept is not None:
        kept, _kept = _kept, None
        kept[1].__exit__(None, None, None)


def running() -> bool:
    return _executor is not None


def _start(new: bool) -> ProcessPoolExecutor | None:
    """The running pool, started if `new`; None when it is not running and not `new`."""
    global _executor, _busy
    with _lock:
        if _executor is None and not new:
            return None
        _busy += 1
        if _idle is not None:
            _idle.cancel()
        if _executor is None:
            _sweep()
            _executor = ProcessPoolExecutor(
                max_workers=_size, mp_context=multiprocessing.get_context("spawn"),
                initializer=_init_worker,
                initargs=(os.getpid(), str(_own_dir()), str(pyfadata.pyfa_dir())))
        return _executor


def _own_dir() -> Path:
    """workers/<server pid>/<worker pid> holds each worker's database: servers
    sharing a data dir never touch each other's live workers."""
    return eosboot.booted_dir() / "workers" / str(os.getpid())


def _sweep() -> None:
    """Remove what servers that are gone left behind (their workers died with them)."""
    base = eosboot.booted_dir() / "workers"
    for server in base.iterdir() if base.is_dir() else ():
        pid = int(server.name) if server.name.isdigit() else None
        if pid is not None and pid != os.getpid() and not _alive(pid):
            shutil.rmtree(server, ignore_errors=True)


def _stop(executor: ProcessPoolExecutor) -> None:
    """Stop the workers, then remove their databases."""
    pids = list(executor._processes)
    executor.shutdown(wait=True, cancel_futures=True)
    own = _own_dir()
    for pid in pids:
        shutil.rmtree(own / str(pid), ignore_errors=True)
    with contextlib.suppress(OSError):
        own.rmdir()  # stays while a newer pool's workers use it


def _release() -> None:
    global _busy, _idle
    with _lock:
        _busy -= 1
        if _idle is not None:
            _idle.cancel()
        _idle = threading.Timer(IDLE_SECONDS, _idle_shutdown)
        _idle.daemon = True
        _idle.start()


def _idle_shutdown() -> None:
    global _executor, _idle
    with _lock:
        if _busy:
            return
        _idle = None
        executor, _executor = _executor, None
    if executor is not None:
        _stop(executor)


def shutdown() -> None:
    global _executor, _idle
    with _lock:
        if _idle is not None:
            _idle.cancel()
            _idle = None
        executor, _executor = _executor, None
    if executor is not None:
        _stop(executor)


def _init_worker(parent_pid: int, base: str, pyfa_dir: str) -> None:
    # The server's stdout is the MCP stream; Pyfa prints while it boots.
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
    except OSError:
        pass  # no stdout handle at all (Windows spawn): nothing to protect
    sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    threading.Thread(target=_exit_with, args=(parent_pid,), daemon=True).start()
    pyfadata.set_dir(Path(pyfa_dir))  # the user's own profiles resolve as in the server
    eosboot.boot(Path(base) / str(os.getpid()))


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102  # TIMEOUT
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, someone else's
    return True


def _exit_with(parent_pid: int) -> None:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
        if handle:
            kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0xFFFFFFFF)
    else:
        while os.getppid() == parent_pid:
            time.sleep(1)
    os._exit(0)


def _memory_mb(pid: int) -> float:
    try:
        if sys.platform == "win32":
            return _windows_private_mb(pid)
        with open(f"/proc/{pid}/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def _windows_private_mb(pid: int) -> float:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *((name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                        "PrivateUsage"))]

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0.0
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p,
                                                     ctypes.POINTER(Counters), wintypes.DWORD]
        if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0.0
        return counters.PrivateUsage / 2**20
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def describe() -> dict:
    with _lock:
        pids = sorted(_executor._processes) if _executor is not None else []
    return {"workers": _size, "running": len(pids), "pids": pids,
            "memory_mb": round(sum(_memory_mb(p) for p in pids)),
            "idle_shutdown_s": IDLE_SECONDS, "inline_below": INLINE_LIMIT}
