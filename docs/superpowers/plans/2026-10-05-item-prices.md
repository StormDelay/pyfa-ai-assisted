# Item Prices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every tool output that names items carries an ISK estimate from a locally cached fuzzwork price file, so the agent can weigh cost against gain.

**Architecture:**
- **`pyfa_mcp/prices.py`** (new) owns the cache: one background download of fuzzwork's bulk aggregate (The Forge sell, 5th percentile) at most every 3 days into `<data_dir>/prices.json`; reads come from memory and never touch the network. It also prices fits (`fit_price`) and an item plus a full load of its charge (`cost`).
- **Surfaces** call into it: `evaluate.py` (fit price block, compare column), `catalog.py` (`_row`: item_info / search_items), `search.py` (find_modifiers rows, marginal_swaps `isk_delta`, optimize_fit `price_total`).
- **`server.py`** starts the first download at boot, adds `refresh_prices` and `status().prices`, and gets the instructions bullet.

**Tech Stack:** Python 3.13, `requests` (already pinned for eos), stdlib `csv`/`gzip`/`json`/`threading`, pytest, Pyfa's eos.

**Spec:** `docs/superpowers/specs/2026-10-05-item-prices-design.md`

## Global Constraints

- No new dependencies. Download with the pinned `requests`.
- Source URL: `https://market.fuzzwork.co.uk/aggregatecsv.csv.gz`. Keep rows whose `what` is `10000002|<typeID>|false`; price = `fivepercent` (column 9, index 8). Skip `fivepercent` 0 and unparsable rows.
- Constants in `prices.py`: `STALE_AFTER = 3 * 86400`, `RETRY_AFTER = 3600`, `REFRESH_COOLDOWN = 600`, `CHECK_EVERY = 3600` (seconds).
- Cache file: `<data_dir>/prices.json` = `{"source": "fuzzwork", "fetched_at": <unix float>, "prices": {"<typeID>": <float>}}`, written to `prices.json.tmp` then `os.replace`d.
- No tool call waits on the network except `refresh_prices`.
- **An unknown price is `None`/`null`, never 0.** A fit total sums what is priced and says `partial: true` + `unpriced: [names]` when anything is missing.
- `price` is not a stat: it must not become an optimizer objective or constraint, and `stats.DEFAULT_COMPARE` does not change (test_stats asserts every key there is a computed stat).
- Prices are attached in the main process. Search worker processes never import or call `prices`.
- Log to stderr only (stdout is the JSON-RPC stream).
- No test touches the network: `conftest.py` replaces `prices._download` for the whole session.
- Run tests with `.venv/Scripts/python.exe -m pytest` (Windows; `uv` is not on PATH in the Bash tool).

## Review Focus

- **A corrupt or hand-edited `prices.json`** (truncated JSON, wrong types): treated as no data, a download starts, no tool fails. Test in Task 1.
- **Another server instance (same data dir) writes a newer file:** picked up on the next hourly check via the file's mtime. Test in Task 1.
- **Writing the cache fails** (`os.replace` raises, e.g. a locked file on Windows): the old file stays byte for byte, the failure is recorded and backed off. Test in Task 2.
- **`refresh_prices` while the background download is running:** waits for it instead of starting a second download. Test in Task 2.
- **An implant set row in find_modifiers:** priced as the sum of its pieces, not just its first implant. Test in Task 5.

---

### Task 1: Price cache — parse, load, read

**Files:**
- Create: `pyfa_mcp/prices.py`
- Create: `tests/test_prices.py`
- Modify: `tests/conftest.py` (session-wide offline download; `seed_prices` fixture)

**Interfaces:**
- Produces (used by every later task):
  - `prices.configure(data_dir: Path | None) -> None` — point at a data dir (None: eosboot's booted dir) and forget all loaded state.
  - `prices.parse(gz: bytes) -> dict[int, float]`
  - `prices.price(type_id: int) -> float | None`
  - `prices.price_info() -> dict` with keys `source`, `state` (`ok`/`loading`/`stale`/`unavailable`), `fetched_at` (ISO str or None), `age_days` (float or None), optional `error`.
  - `prices.price_source() -> str`
  - `prices.ensure_fresh() -> None` — starts a background thread running `_fetch(timeout)` (download, parse, atomic write); both are written in this task. Task 2 adds `refresh` and the tests that pin the download side.
  - `prices._download(timeout: float) -> bytes` — the only network call; replaced in tests.
  - conftest fixture `seed_prices(booted)` → `seed(by_name: dict[str, float], age_days: float = 0.0) -> None`.

- [ ] **Step 1: Add the offline guard and seed fixture to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
@pytest.fixture(scope="session", autouse=True)
def _prices_offline():
    """No test downloads prices. Session-wide and never undone: a background
    download thread may outlive the test that started it."""
    from pyfa_mcp import prices

    def no_download(timeout):
        raise OSError("no network in tests")

    prices._download = no_download
    yield
    prices.configure(None)


@pytest.fixture
def seed_prices(booted):
    """seed({"Heat Sink II": 1e6, ...}, age_days=0): write the server's prices.json
    by item name; removed again after the test."""
    import json
    import time

    import eos.db
    from pyfa_mcp import prices

    path = booted / prices.FILE

    def seed(by_name: dict, age_days: float = 0.0) -> None:
        by_id = {str(eos.db.getItem(name).ID): value for name, value in by_name.items()}
        path.write_text(json.dumps({"source": "fuzzwork",
                                    "fetched_at": time.time() - age_days * 86400,
                                    "prices": by_id}), encoding="utf-8")
        prices.configure(booted)

    yield seed
    path.unlink(missing_ok=True)
    prices.configure(None)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_prices.py`:

```python
import gzip
import json
import os
import threading
import time

import pytest

from pyfa_mcp import prices

HEADER = "what,weightedaverage,maxval,minval,stddev,median,volume,numorders,fivepercent,orderSet\n"


def gz(rows) -> bytes:
    """A fuzzwork aggregate file holding rows of (what, fivepercent)."""
    body = "".join(f"{what},1,1,1,1,1,1,1,{p},1\n" for what, p in rows)
    return gzip.compress((HEADER + body).encode())


def write(data_dir, by_id: dict, age_days: float = 0.0):
    path = data_dir / prices.FILE
    path.write_text(json.dumps({"source": "fuzzwork",
                                "fetched_at": time.time() - age_days * 86400,
                                "prices": {str(k): v for k, v in by_id.items()}}),
                    encoding="utf-8")
    return path


def join():
    if prices._worker is not None:
        prices._worker.join(5)


@pytest.fixture(autouse=True)
def data_dir(tmp_path):
    prices.configure(tmp_path)
    yield tmp_path
    join()
    prices.configure(None)


def test_p1_parse_keeps_forge_sell_only():
    raw = gz([("10000002|2048|false", 367992.61),   # kept
              ("10000002|2048|true", 300000.0),     # buy
              ("10000043|2048|false", 1.0),         # another region
              ("10000002|3000|false", 0.0),         # no orders
              ("10000002|x|false", 5.0),            # malformed type id
              ("10000002|4000", 5.0)])              # malformed key
    assert prices.parse(raw) == {2048: 367992.61}


def test_reads_a_fresh_file_without_downloading(data_dir):
    write(data_dir, {2048: 5.0})
    assert prices.price(2048) == 5.0
    assert prices.price(9999) is None
    info = prices.price_info()
    assert info["state"] == "ok" and info["age_days"] == 0.0
    assert info["source"] == "fuzzwork, The Forge sell (5th percentile)"
    assert prices.price_source() == "fuzzwork Forge sell, 0.0 days old"
    join()
    assert "error" not in prices.price_info()  # no download was attempted


def test_review_corrupt_file_is_no_data(data_dir):
    (data_dir / prices.FILE).write_text("{not json", encoding="utf-8")
    assert prices.price(2048) is None
    join()
    info = prices.price_info()
    assert info["state"] == "unavailable" and "no network" in info["error"]
    assert prices.price_source().startswith("prices unavailable:")


def test_review_picks_up_a_file_another_server_wrote(data_dir):
    path = write(data_dir, {2048: 5.0})
    assert prices.price(2048) == 5.0
    write(data_dir, {2048: 7.0})
    later = path.stat().st_mtime_ns + 2_000_000_000
    os.utime(path, ns=(later, later))
    assert prices.price(2048) == 5.0  # not re-checked within the hour
    prices._checked_at = 0.0
    assert prices.price(2048) == 7.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prices.py -v`
Expected: FAIL / ERROR with `ImportError: cannot import name 'prices'`.

- [ ] **Step 4: Write `pyfa_mcp/prices.py`**

```python
"""Item prices: fuzzwork's bulk market aggregate, The Forge sell (5th percentile).

One download every few days, in a background thread, into the data dir
(prices.json). Tool calls read memory only and never wait on the network,
except refresh(), which the user asks for. An unknown price is None, never 0."""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from pyfa_mcp import eosboot

URL = "https://market.fuzzwork.co.uk/aggregatecsv.csv.gz"
FILE = "prices.json"
REGION = "10000002"  # The Forge, where Jita is
SOURCE = "fuzzwork, The Forge sell (5th percentile)"
STALE_AFTER = 3 * 86400
RETRY_AFTER = 3600       # after a failed download
REFRESH_COOLDOWN = 600   # refresh() does nothing on data younger than this
CHECK_EVERY = 3600       # reads look at the file (and staleness) this often

_lock = threading.Lock()
_dir: Path | None = None
_data: dict | None = None  # {"fetched_at": float, "prices": {int: float}}
_mtime: int | None = None
_failed_at: float | None = None
_error: str | None = None
_worker: threading.Thread | None = None
_checked_at = 0.0


def configure(data_dir: Path | None) -> None:
    """Point at a data dir (None: the booted one) and forget everything loaded."""
    global _dir, _data, _mtime, _failed_at, _error, _worker, _checked_at
    with _lock:
        _dir = None if data_dir is None else Path(data_dir)
        _data = _mtime = _failed_at = _error = _worker = None
        _checked_at = 0.0


def _path() -> Path:
    return (_dir or eosboot.booted_dir()) / FILE


def parse(gz: bytes) -> dict[int, float]:
    out: dict[int, float] = {}
    rows = csv.reader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(gz)), encoding="utf-8"))
    next(rows, None)  # header
    for row in rows:
        try:
            region, type_id, is_buy = row[0].split("|")
            value = float(row[8])
            type_id = int(type_id)
        except (ValueError, IndexError):
            continue
        if region == REGION and is_buy == "false" and value > 0:
            out[type_id] = value
    return out


def _loaded() -> dict | None:
    """The cached file, re-read when its mtime changes; None if missing or unreadable.
    Call with _lock held."""
    global _data, _mtime
    try:
        mtime = _path().stat().st_mtime_ns
    except OSError:
        return _data
    if mtime != _mtime:
        try:
            raw = json.loads(_path().read_text(encoding="utf-8"))
            _data = {"fetched_at": float(raw["fetched_at"]),
                     "prices": {int(k): float(v) for k, v in raw["prices"].items()}}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            _data = None
        _mtime = mtime
    return _data


def _stale(data: dict | None, now: float) -> bool:
    return data is None or now - data["fetched_at"] > STALE_AFTER


def _busy() -> bool:
    return _worker is not None and _worker.is_alive()


def _download(timeout: float) -> bytes:
    import requests

    from pyfa_mcp import drift

    resp = requests.get(URL, timeout=timeout,
                        headers={"User-Agent": f"pyfa-mcp/{drift._own_version() or 'dev'}"})
    resp.raise_for_status()
    return resp.content


def _fetch(timeout: float) -> None:
    """Download, parse and store. A failure keeps the old file and is recorded."""
    global _data, _mtime, _failed_at, _error
    try:
        found = parse(_download(timeout))
        if not found:
            raise ValueError("the download held no The Forge sell prices")
        now = time.time()
        path = _path()
        tmp = path.with_name(FILE + ".tmp")
        tmp.write_text(json.dumps({"source": "fuzzwork", "fetched_at": now,
                                   "prices": {str(k): v for k, v in found.items()}}),
                       encoding="utf-8")
        os.replace(tmp, path)
        with _lock:
            _data, _mtime = {"fetched_at": now, "prices": found}, path.stat().st_mtime_ns
            _failed_at = _error = None
    except Exception as exc:
        with _lock:
            _failed_at, _error = time.time(), f"{type(exc).__name__}: {exc}"
        print(f"pyfa-mcp: price download failed: {_error}", file=sys.stderr)


def ensure_fresh() -> None:
    """Start a background download when the cache is missing or stale, unless
    one is running or the last one failed less than RETRY_AFTER ago."""
    global _worker
    now = time.time()
    with _lock:
        data = _loaded()
        backoff = _failed_at is not None and now - _failed_at < RETRY_AFTER
        if _busy() or backoff or not _stale(data, now):
            return
        _worker = threading.Thread(target=_fetch, args=(120,), name="prices", daemon=True)
        _worker.start()


def _current() -> dict | None:
    global _checked_at
    now = time.time()
    if now - _checked_at >= CHECK_EVERY:
        _checked_at = now
        ensure_fresh()
    return _data


def price(type_id: int) -> float | None:
    data = _current()
    return None if data is None else data["prices"].get(type_id)


def price_info() -> dict:
    data = _current()
    with _lock:
        busy, error = _busy(), _error
    now = time.time()
    if data is None:
        state = "loading" if busy else "unavailable"
    else:
        state = "stale" if _stale(data, now) else "ok"
    info = {"source": SOURCE, "state": state, "fetched_at": None, "age_days": None}
    if data is not None:
        info["fetched_at"] = datetime.fromtimestamp(
            data["fetched_at"], timezone.utc).isoformat(timespec="seconds")
        info["age_days"] = round((now - data["fetched_at"]) / 86400, 1)
    if error:
        info["error"] = error
    return info


def price_source() -> str:
    """One line for tool outputs: where the prices come from and how old they are."""
    info = price_info()
    if info["state"] == "loading":
        return "prices loading (first download running); retry shortly"
    if info["state"] == "unavailable":
        return f"prices unavailable: {info.get('error', 'no data yet')}"
    text = f"fuzzwork Forge sell, {info['age_days']} days old"
    if info["state"] == "stale":
        text += (" (stale; last refresh failed: " + info["error"] + ")"
                 if "error" in info else " (stale; refreshing)")
    return text
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prices.py -v`
Expected: 4 PASS.

- [ ] **Step 6: Run the whole suite (conftest changed)**

Run: `.venv/Scripts/python.exe -m pytest -x -q -m "not slow"`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/prices.py tests/test_prices.py tests/conftest.py
git commit -m "Prices: cache of fuzzwork's Forge sell prices, read from memory"
```

---

### Task 2: Background download, backoff and forced refresh

**Files:**
- Modify: `pyfa_mcp/prices.py` (add `refresh`)
- Test: `tests/test_prices.py`

**Interfaces:**
- Consumes: Task 1's `prices` module (`_fetch`, `ensure_fresh`, `_worker`, `_data`, `_lock`, `_busy`, `_current`, `price_info`).
- Produces: `prices.refresh(timeout: float = 30) -> dict` — `{"refreshed": bool, "reason"?: str, "error"?: str, **price_info()}`; never raises.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_prices.py`:

```python
class Downloads:
    """A stand-in for prices._download: counts calls, returns `body` or raises it,
    optionally waits on `gate` first."""

    def __init__(self, body, gate=None):
        self.body, self.gate, self.calls = body, gate, 0

    def __call__(self, timeout):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


@pytest.fixture
def downloads(monkeypatch):
    def install(body, gate=None):
        fake = Downloads(body, gate)
        monkeypatch.setattr(prices, "_download", fake)
        return fake
    return install


def test_p2_fresh_until_three_days(data_dir, downloads):
    fake = downloads(gz([("10000002|2048|false", 9.0)]))
    write(data_dir, {2048: 5.0}, age_days=2.9)
    assert prices.price_info()["state"] == "ok"
    join()
    assert fake.calls == 0

    prices.configure(data_dir)
    write(data_dir, {2048: 5.0}, age_days=3.1)
    assert prices.price_info()["state"] in ("stale", "ok")
    join()
    assert fake.calls == 1
    assert prices.price(2048) == 9.0 and prices.price_info()["state"] == "ok"


def test_p3_failure_keeps_the_old_file_and_backs_off(data_dir, downloads):
    fake = downloads(OSError("boom"))
    path = write(data_dir, {2048: 5.0}, age_days=4)
    before = path.read_bytes()
    prices.price(2048)
    join()
    assert path.read_bytes() == before
    info = prices.price_info()
    assert info["state"] == "stale" and "boom" in info["error"]
    assert "last refresh failed" in prices.price_source()
    prices.ensure_fresh()  # within RETRY_AFTER: no new attempt
    join()
    assert fake.calls == 1


def test_p3_no_file_and_failure_is_unavailable(data_dir, downloads):
    downloads(OSError("boom"))
    assert prices.price(2048) is None
    join()
    assert prices.price_info()["state"] == "unavailable"


def test_p4_loading_while_the_first_download_runs(data_dir, downloads):
    gate = threading.Event()
    downloads(gz([("10000002|2048|false", 9.0)]), gate)
    assert prices.price(2048) is None
    assert prices.price_info()["state"] == "loading"
    assert prices.price_source().startswith("prices loading")
    gate.set()
    join()
    assert prices.price(2048) == 9.0


def test_p5_refresh_cooldown_success_and_failure(data_dir, downloads):
    fake = downloads(gz([("10000002|2048|false", 9.0)]))
    write(data_dir, {2048: 5.0}, age_days=0.001)  # ~1.4 minutes
    out = prices.refresh()
    assert out["refreshed"] is False and "minutes old" in out["reason"]
    assert fake.calls == 0

    prices.configure(data_dir)
    write(data_dir, {2048: 5.0}, age_days=1)
    out = prices.refresh()
    assert out["refreshed"] is True and out["state"] == "ok"
    assert prices.price(2048) == 9.0

    downloads(OSError("boom"))
    prices.configure(data_dir)
    write(data_dir, {2048: 5.0}, age_days=1)
    out = prices.refresh()
    assert out["refreshed"] is False and "boom" in out["error"]
    assert prices.price(2048) == 5.0


def test_review_a_failed_write_keeps_the_old_file(data_dir, downloads, monkeypatch):
    downloads(gz([("10000002|2048|false", 9.0)]))
    path = write(data_dir, {2048: 5.0}, age_days=1)
    before = path.read_bytes()

    def locked(src, dst):
        raise PermissionError("file is locked")

    monkeypatch.setattr(prices.os, "replace", locked)
    out = prices.refresh()
    assert out["refreshed"] is False and "PermissionError" in out["error"]
    assert path.read_bytes() == before and prices.price(2048) == 5.0


def test_review_refresh_waits_for_a_running_download(data_dir, downloads):
    gate = threading.Event()
    fake = downloads(gz([("10000002|2048|false", 9.0)]), gate)
    prices.price(2048)  # starts the background download, which waits on the gate
    threading.Timer(0.2, gate.set).start()
    out = prices.refresh(timeout=5)
    assert out["refreshed"] is True and fake.calls == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prices.py -v`
Expected: the P5 and two review refresh tests FAIL with `AttributeError: module 'pyfa_mcp.prices' has no attribute 'refresh'`; P2–P4 already pass (the download side came with Task 1) — that is fine, they pin it.

- [ ] **Step 3: Add `refresh` to `pyfa_mcp/prices.py`** (after `price_source`)

```python
def refresh(timeout: float = 30) -> dict:
    """Download now (the user asked). Waits on a running background download
    instead of starting a second one. Never raises."""
    before = _current()
    if before is not None and time.time() - before["fetched_at"] < REFRESH_COOLDOWN:
        minutes = int((time.time() - before["fetched_at"]) / 60)
        return {"refreshed": False, "reason": f"prices are {minutes} minutes old",
                **price_info()}
    with _lock:
        running = _worker if _busy() else None
    if running is not None:
        running.join(timeout)
    else:
        _fetch(timeout)
    with _lock:
        after, error = _data, _error
    if after is not None and after is not before:
        return {"refreshed": True, **price_info()}
    return {"refreshed": False, **price_info(),
            "error": error or "the download is still running; prices update when it ends"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prices.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/prices.py tests/test_prices.py
git commit -m "Prices: forced refresh; tests for staleness, backoff and failed writes"
```

---

### Task 3: Fit prices in evaluate_fit and compare_fits

**Files:**
- Modify: `pyfa_mcp/prices.py` (add `load`, `cost`, `fit_price`)
- Modify: `pyfa_mcp/evaluate.py:7-9` (import, `META_KEYS`), `:45-56` (`_evaluate_parsed`), `:70-103` (`compare`)
- Test: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: `prices.price`, `prices.price_source`, conftest `seed_prices`.
- Produces:
  - `prices.load(module_item, charge_item) -> int` — charges that fill the module (Pyfa's `Module.getNumCharges`, unmodified capacity / volume).
  - `prices.cost(type_id: int, charge_id: int | None = None) -> float | None` — the item plus a full load of its charge; None if either is unknown.
  - `prices.fit_price(fit) -> dict` — `{"total", "hull", "modules", "charges", "drones", "implants", "cargo"}` floats, plus `partial: True` and `unpriced: [names]` when anything is unpriced.
  - `evaluate_fit` result keys `price` (that dict) and `price_source` (str); `compare` rows may carry `price_partial`/`unpriced`; `compare` result key `price_source`.
  - `evaluate.META_KEYS` gains `"price_source"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evaluate.py`:

```python
ZEALOT_ITEMS = ["Zealot", "Heat Sink II", "Damage Control II",
                "Multispectrum Energized Membrane II", "Medium Armor Repairer II",
                "50MN Microwarpdrive II", "Warp Disruptor II", "Stasis Webifier II",
                "Heavy Pulse Laser II", "Medium Energy Locus Coordinator II",
                "Medium Energy Metastasis Adjuster II"]


def test_p6_fit_price_buckets(booted, zealot_eft, seed_prices, no_fits_left):
    seed_prices({**{n: 1e6 for n in ZEALOT_ITEMS}, "Scorch M": 100.0})
    result = evaluate.evaluate(zealot_eft, None)
    price = result["price"]
    # 5 low + 3 mid + 5 high + 2 rigs; one Scorch M per laser
    assert price == {"total": 16e6 + 500.0, "hull": 1e6, "modules": 15e6, "charges": 500.0,
                     "drones": 0.0, "implants": 0.0, "cargo": 0.0}
    assert result["price_source"] == "fuzzwork Forge sell, 0.0 days old"


def test_p6_partial_fit_price(booted, zealot_eft, seed_prices, no_fits_left):
    seed_prices({**{n: 1e6 for n in ZEALOT_ITEMS if n != "Stasis Webifier II"},
                 "Scorch M": 100.0})
    price = evaluate.evaluate(zealot_eft, None)["price"]
    assert price["total"] == 15e6 + 500.0 and price["modules"] == 14e6
    assert price["partial"] is True and price["unpriced"] == ["Stasis Webifier II"]


def test_fit_price_counts_drones_and_cargo(booted, seed_prices, no_fits_left):
    seed_prices({"Vexor": 1e7, "Hobgoblin II": 1e5, "Nanite Repair Paste": 1e4})
    # drones and cargo in separate sections, or the importer puts both in cargo
    fit = "[Vexor, d]\n\nHobgoblin II x5\n\nNanite Repair Paste x10\n"
    price = evaluate.evaluate(fit, None)["price"]
    assert price["drones"] == 5e5 and price["cargo"] == 1e5
    assert price["total"] == 1e7 + 5e5 + 1e5 and "partial" not in price


def test_p8_compare_has_a_price_column(booted, zealot_eft, seed_prices, no_fits_left):
    seed_prices({"Zealot": 1e6})
    out = evaluate.compare([zealot_eft], None, None)
    assert "price.total" in out["columns"]
    row = out["rows"][0]
    assert row["price.total"] == 1e6
    assert row["price_partial"] is True and "Heat Sink II" in row["unpriced"]
    assert out["price_source"].startswith("fuzzwork Forge sell")


def test_unseeded_prices_are_null_not_zero(booted, zealot_eft, no_fits_left):
    from pyfa_mcp import prices
    prices.configure(None)
    price = evaluate.evaluate(zealot_eft, None)["price"]
    assert price["total"] == 0.0 and price["partial"] is True
    assert "Zealot" in price["unpriced"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_evaluate.py -v -k "price or p6 or p8"`
Expected: FAIL with `KeyError: 'price'`.

- [ ] **Step 3: Add `load`, `cost`, `fit_price` to `pyfa_mcp/prices.py`** (at the end)

```python
def load(module_item, charge_item) -> int:
    """Charges that fill the module: Pyfa's Module.getNumCharges."""
    capacity = module_item.getAttribute("capacity") or 0
    volume = charge_item.getAttribute("volume") or 0
    return int(capacity / volume + 1e-9) if volume else 0


def cost(type_id: int, charge_id: int | None = None) -> float | None:
    """An item, plus a full load of its charge; None when either price is unknown."""
    base = price(type_id)
    if charge_id is None or base is None:
        return base
    unit = price(charge_id)
    if unit is None:
        return None
    import eos.db

    module, charge = eos.db.getItem(type_id), eos.db.getItem(charge_id)
    if module is None or charge is None:
        return None
    return base + unit * load(module, charge)


def fit_price(fit) -> dict:
    """A calculated eos fit's price by bucket. Unknown items count nothing and are
    named in `unpriced`, with `partial: True`."""
    used = [m for m in fit.modules if not m.isEmpty]
    lines = {
        "hull": [(fit.ship.item, 1)],
        "modules": [(m.item, 1) for m in used],
        "charges": [(m.charge, m.numCharges) for m in used if m.charge is not None],
        "drones": [(d.item, d.amount) for d in fit.drones]
                  + [(f.item, f.amount) for f in fit.fighters],
        "implants": [(i.item, 1) for i in fit.implants] + [(b.item, 1) for b in fit.boosters],
        "cargo": [(c.item, c.amount) for c in fit.cargo],
    }
    buckets, unpriced = {}, []
    for bucket, items in lines.items():
        buckets[bucket] = 0.0
        for item, count in items:
            each = price(item.ID)
            if each is None:
                unpriced.append(item.name)
            else:
                buckets[bucket] += each * count
    out = {"total": sum(buckets.values()), **buckets}
    if unpriced:
        out.update(partial=True, unpriced=list(dict.fromkeys(unpriced)))
    return out
```

- [ ] **Step 4: Wire it into `pyfa_mcp/evaluate.py`**

Import and meta keys (top of file):

```python
from pyfa_mcp import catalog, conditions, drift, eft, eosboot, notes, prices, stats, store

# Keys of an evaluate result that are not stats.
META_KEYS = ("fit", "ship", "applied", "warnings", "notes", "hull_bonuses", "price_source")
```

`_evaluate_parsed`: price the fit inside the scratch block (the fit is gone after it), and add both keys:

```python
        bonuses = catalog.hull_bonuses(fit.ship.item)
        try:
            price = prices.fit_price(fit)
        except Exception as exc:  # a price problem never fails an evaluation
            price = {"total": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"fit": name, "ship": ship, "applied": applied,
            "warnings": warnings_for(result) + effect_warnings + store.pyfa_warnings(ref),
            "notes": fit_notes, "hull_bonuses": bonuses, **result,
            "price": price, "price_source": prices.price_source()}
```

`compare`: the default columns gain `price.total`, partial rows say so, and the result names the source once:

```python
    keys = list(keys) if keys else [*stats.DEFAULT_COMPARE, "price.total"]
```

```python
            partial = ({"price_partial": True, "unpriced": result["price"]["unpriced"]}
                       if result["price"].get("partial") else {})
            rows.append({"fit": result["fit"], "ship": result["ship"], **label,
                         **{k: flat[k] for k in keys}, **partial,
                         "warnings": result["warnings"], "notes": result["notes"]})
    columns = ["fit", *(["variant"] if variants else []), *keys]
    return {"applied": applied, "columns": columns, "rows": rows,
            "price_source": prices.price_source()}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_evaluate.py tests/test_stats.py tests/test_bench.py -v`
Expected: all PASS. If `test_p6_fit_price_buckets` is off by the charge count, print `prices.load(getItem("Heavy Pulse Laser II"), getItem("Scorch M"))`: a crystal is one per laser, so it must be 1.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -x -q -m "not slow"`
Expected: all pass (search tests flatten evaluate results; `price.*` keys appear there but no stat key changes).

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/prices.py pyfa_mcp/evaluate.py tests/test_evaluate.py
git commit -m "Prices: evaluate_fit price block, compare_fits price.total column"
```

---

### Task 4: Prices in item_info and search_items

**Files:**
- Modify: `pyfa_mcp/catalog.py:9` (import), `:101-110` (`_row`), `:260-279` (`item_info`)
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: `prices.price`, `prices.price_source`, conftest `seed_prices`.
- Produces: every `catalog._row` (search_items, list_ships, item_info) has `price: float | None`; `item_info` also has `price_source`. `whats_new` is unchanged (it filters `_row` keys).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalog.py`:

```python
def test_p8_item_rows_carry_a_price(booted, seed_prices):
    seed_prices({"Zealot": 2e8})
    info = catalog.item_info("Zealot")
    assert info["price"] == 2e8
    assert info["price_source"].startswith("fuzzwork Forge sell")
    rows = catalog.search_items("Heat Sink II")
    assert rows and all("price" in r for r in rows)
    assert all(r["price"] is None for r in rows)  # unseeded: unknown, not 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_catalog.py::test_p8_item_rows_carry_a_price -v`
Expected: FAIL with `KeyError: 'price'`.

- [ ] **Step 3: Implement**

In `pyfa_mcp/catalog.py`, after `from pyfa_mcp.eft import suggest`:

```python
from pyfa_mcp import prices
```

In `_row`, add the price to the dict:

```python
        "cpu": item.getAttribute("cpu"), "powergrid": item.getAttribute("power"),
        "price": prices.price(item.ID),
    }
```

In `item_info`, after the `charges` block and before `return info`:

```python
    info["price_source"] = prices.price_source()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_catalog.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/catalog.py tests/test_catalog.py
git commit -m "Prices: item_info and search_items rows carry a price"
```

---

### Task 5: Prices in find_modifiers, marginal_swaps and optimize_fit

**Files:**
- Modify: `pyfa_mcp/search.py` — import (`:17`), `_row` (`:190-209`), `_groups` (`:220-238`), `find_modifiers` return (`:322-340`), `marginal_swaps` (`:418-485`), `_diff` (`:925-933`), `optimize_fit` best entries and return (`:1081-1120`)
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `prices.cost`, `prices.price`, `prices.price_source`; `candidates.Candidate` (`type_id`, `charge_id`, `edits`, `source`); `bench.Edit` (`item_id`); `bench.Bench.occupant` → `(type_id, charge_id, state) | None`; `search.Option` (`type_id`, `charge_id`).
- Produces:
  - `search._candidate_price(c) -> float | None` — a set (several edits) sums its pieces; anything else is `prices.cost(c.type_id, c.charge_id)`.
  - find_modifiers: each candidate row, `best` and `reference` carry `price`; result has `price_source`.
  - marginal_swaps: each swap row carries `isk_delta: float | None`; result has `price_source`.
  - optimize_fit: each full `best` entry carries `price_total` (+ `price_partial`, `unpriced` when partial); compact diffs carry them too; result has `price_source`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_search.py`:

```python
@pytest.fixture
def price_is_type_id(monkeypatch):
    """Every item costs its type ID in ISK: prices become checkable by arithmetic."""
    from pyfa_mcp import prices
    monkeypatch.setattr(prices, "price", lambda type_id: float(type_id))


def _id(name):
    import eos.db
    return eos.db.getItem(name).ID


def test_p9_find_modifiers_rows_carry_prices(booted, no_fits_left, price_is_type_id):
    result = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["module"],
                                   expand=["*"])
    rows = result["candidates"]
    assert rows and all(r["price"] == r["type_id"] for r in rows if r["charge"] is None)
    by_name = {r["name"]: r["price"] for r in rows}
    for g in result["groups"]:
        assert g["best"]["price"] == by_name[g["best"]["name"]]
        if g["reference"] is not None:
            assert g["reference"]["price"] == by_name[g["reference"]["name"]]
    assert "price_source" in result


def test_review_an_implant_set_costs_all_its_pieces(booted, price_is_type_id):
    from pyfa_mcp.bench import Edit
    from pyfa_mcp.candidates import Candidate
    a, b = _id("High-grade Crystal Alpha"), _id("High-grade Crystal Beta")
    the_set = Candidate(name="High-grade Crystal set", source="implant", slot="implant 1",
                        group="Implant sets", meta="Faction", type_id=a,
                        edits=(Edit(("implant", 1), a), Edit(("implant", 2), b)))
    assert search._candidate_price(the_set) == a + b


def test_p7_marginal_swaps_isk_delta(booted, no_fits_left, price_is_type_id):
    result = search.marginal_swaps(LOOSE, "tank.ehp.total", include_empty_slots=False,
                                   top_n=50)
    swaps = result["swaps"]
    assert swaps
    for s in swaps:
        new = 0.0 if s["add"] is None else _id(s["add"])
        old = 0.0 if s["remove"] is None else _id(s["remove"])
        assert s["isk_delta"] == new - old, s
    assert "price_source" in result


def test_p7_unpriced_side_gives_null(booted, no_fits_left, monkeypatch):
    from pyfa_mcp import prices
    extender = _id("Small Core Defense Field Extender I")
    monkeypatch.setattr(prices, "price",
                        lambda type_id: None if type_id == extender else float(type_id))
    swaps = search.marginal_swaps(LOOSE, "tank.ehp.total", include_empty_slots=False,
                                  top_n=50)["swaps"]
    assert swaps and all(s["isk_delta"] is None for s in swaps)  # every swap replaces it


def test_p9_optimize_fit_best_carries_its_price(booted, zealot_eft, no_fits_left,
                                                price_is_type_id):
    out = search.optimize_fit(zealot_eft, "tank.ehp.total", budget={"evaluations": 50},
                              top_k=2)
    best = out["best"][0]
    assert best["price_total"] == evaluate.evaluate(best["eft"], best["conditions"])[
        "price"]["total"]
    assert "price_partial" not in best
    for diff in out["best"][1:]:
        assert "price_total" in diff
    assert "price_source" in out
```

If `test_search.py` does not import `evaluate` yet, add `from pyfa_mcp import evaluate` to its imports. If `test_p7_unpriced_side_gives_null` finds a swap that is not a replacement of the extender (LOOSE has only that one module, so with `include_empty_slots=False` every swap replaces or removes it), keep the assertion: a removal of an unpriced item is also `None`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -v -k "p7 or p9 or implant_set"`
Expected: FAIL with `KeyError: 'price'` / `'isk_delta'` / `'price_total'` and `AttributeError: ... _candidate_price`.

- [ ] **Step 3: Implement in `pyfa_mcp/search.py`**

Add `prices` to the `from pyfa_mcp import (...)` list at line 17.

Add next to `_charge_name`:

```python
def _candidate_price(c) -> float | None:
    """What fitting the candidate costs: a set is all its pieces."""
    if len(c.edits) > 1:
        each = [prices.cost(e.item_id) for e in c.edits]
        return None if None in each else sum(each)
    return prices.cost(c.type_id, c.charge_id)
```

In `_row`, add `"price": _candidate_price(c),` after `"calibration": c.calibration,`.

In `_groups`, carry the price into `best` and `reference`:

```python
            "best": {**{k: top[k] for k in ("name", "meta", "cpu", "pg", "calibration",
                                            "price")},
                     "delta": top["delta"], **({"limits": top["limits"]} if "limits" in top
                                              else {})},
            "reference": None if ref is None or ref is top else
            {"name": ref["name"], "meta": ref["meta"], "price": ref["price"],
             "delta": ref["delta"]},
```

In `find_modifiers`' return dict, add `"price_source": prices.price_source(),` after `"pinned": pinned,`.

In `marginal_swaps`, price each side when the labels are built (main process, before `_run`). Add above `marginal_swaps`:

```python
def _isk_delta(occ, option) -> float | None:
    """What a swap costs: the new item (and its charge load) minus the old one."""
    old = 0.0 if occ is None else prices.cost(occ[0], occ[1])
    new = 0.0 if option is None else prices.cost(option.type_id, option.charge_id)
    return None if old is None or new is None else new - old
```

and change the label tuples and the row:

```python
            if occ is not None:
                trials.append(([Edit(where, None)], None))
                labels.append((place, removed, None, (), _isk_delta(occ, None)))
            for option in options.get(place, []):
                if occ is not None and (option.type_id, option.charge_id) == occ[:2]:
                    continue
                trials.append(([option.edit(where)], None))
                labels.append((place, removed, option.name, option.limits,
                               _isk_delta(occ, option)))
```

```python
    for (place, removed, added, lim, isk), (edits, _), trial in zip(labels, trials, results):
```

```python
        rows.append({"slot": place, "remove": removed, "add": added, "delta": delta,
                     "isk_delta": isk,
                     "new_value": trial.values[key], "valid": True, "_edits": edits,
                     **({"limits": list(lim)} if lim else {})})
```

and in its return dict add `"price_source": prices.price_source(),` after `"warnings": warnings,`.

In `optimize_fit`, where each `entry` is built (after `entry = {"eft": ..., "objective_value": ...}`):

```python
        price = confirmed["result"]["price"]
        entry["price_total"] = price["total"]
        if price.get("partial"):
            entry.update(price_partial=True, unpriced=price["unpriced"])
```

In `_diff`, carry them into the compact rows:

```python
    return {"objective_value": other["objective_value"], "valid": other["valid"],
            **{k: other[k] for k in ("price_total", "price_partial", "unpriced") if k in other},
            "diff": diff}
```

In `optimize_fit`'s final return dict, add `"price_source": prices.price_source(),` after `**out,`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -v -m "not slow"`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/search.py tests/test_search.py
git commit -m "Prices: find_modifiers, marginal_swaps isk_delta, optimize_fit price_total"
```

---

### Task 6: Server — boot download, status, refresh_prices, instructions, guide

**Files:**
- Modify: `pyfa_mcp/server.py` — imports (`:20`), `INSTRUCTIONS` (`:23-77`), `_ensure_booted` (`:90-101`), `status` (`:342-353`), new `refresh_prices` tool after `status`
- Modify: `pyfa_mcp/fitting_guide.yaml:137-139` (mindlink principle)
- Modify: `packaging/mcp_smoke.py:17-20` (`TOOLS`)
- Modify: `tests/test_server.py` (`test_smoke_over_stdio` seeds prices; new tests)

**Interfaces:**
- Consumes: `prices.ensure_fresh`, `prices.price_info`, `prices.refresh`, `prices.FILE`.
- Produces: MCP tool `refresh_prices() -> dict`; `status()["prices"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
def test_status_and_refresh_prices(booted, seed_prices):
    seed_prices({"Zealot": 1e8})
    assert server.status()["prices"]["state"] == "ok"
    out = server.refresh_prices()
    assert out["refreshed"] is False and "minutes old" in out["reason"]


def test_instructions_explain_prices():
    text = server.INSTRUCTIONS
    assert "refresh_prices" in text and "partial" in text and "null" in text


def test_guide_no_longer_says_the_tools_cant_see_price():
    from pyfa_mcp import guide
    assert "can't see price" not in guide.GUIDE.read_text(encoding="utf-8")
```

In `test_smoke_over_stdio`, after the `release-check.json` write, seed a fresh price file so the subprocess server never downloads:

```python
    (data / "prices.json").write_text(
        json.dumps({"source": "fuzzwork", "fetched_at": time.time(), "prices": {}}),
        encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_server.py -v`
Expected: the three new tests FAIL (`KeyError: 'prices'`, `AttributeError: refresh_prices`, instruction/guide asserts); the smoke test FAILS with `tools mismatch` only after Step 3 adds the tool — update `TOOLS` in the same step.

- [ ] **Step 3: Implement**

`pyfa_mcp/server.py` — add `prices` to the `from pyfa_mcp import (...)` list.

`_ensure_booted`: start the first download (a background thread; returns at once):

```python
    try:
        eosboot.boot(_data_dir)
    except Exception as exc:
        _boot_error = f"pyfa-mcp could not start Pyfa: {exc}"
        raise ToolError(_boot_error) from exc
    _booted = True
    prices.ensure_fresh()  # background download if prices.json is missing or stale
```

`status`: add the price state:

```python
            "search_workers": pool.describe(),
            "prices": prices.price_info(),
            **drift.report()}
```

New tool after `status`:

```python
@app.tool()
@_tool
def refresh_prices() -> dict:
    """Download fresh market prices now (fuzzwork, The Forge sell). Call it only
    when the user asks for fresh prices: they otherwise refresh in the
    background every 3 days. Waits up to about 30 seconds."""
    return prices.refresh()
```

`INSTRUCTIONS`: add this bullet after the `optimize_fit's default budget ...` bullet:

```
- Item and fit prices (`price`, `price.total`, marginal_swaps `isk_delta`,
  optimize_fit `price_total`) are estimates: fuzzwork's The Forge sell
  price, roughly Jita sell, as old as `price_source` says. Weigh cost when
  you recommend Faction, Deadspace or Officer items over Tech II, and say
  what the upgrade costs. null means unknown, not free; a `partial` total
  leaves out its `unpriced` items: quote it as "at least X, without ...".
  Call refresh_prices only when the user asks for fresh prices.
```

`pyfa_mcp/fitting_guide.yaml`, the `fleet_command` mindlink principle (lines 137-139), becomes:

```yaml
      - text: "A command mindlink is essentially mandatory: an implant line in the EFT; evaluate_fit's notes flag a missing one. The Tech II mindlink of the booster's main family is the norm. Faction mindlinks give the same bonus to two families, so the search tools always prefer them, but they cost far more (see their price): use one only when the doctrine calls for it."
        why: "The mindlink raises every burst's strength and duration; the search tools rank by strength, not price."
```

`packaging/mcp_smoke.py` — add `"refresh_prices"` to `TOOLS`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_server.py tests/test_guide.py -v`
Expected: all PASS (the smoke test takes a minute or two).

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q -m "not slow"`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pyfa_mcp/server.py pyfa_mcp/fitting_guide.yaml packaging/mcp_smoke.py tests/test_server.py
git commit -m "Prices: download at boot, refresh_prices tool, status, instructions"
```

---

### Task 7: Live check against fuzzwork

**Files:** none (manual check; report the output to the user).

- [ ] **Step 1: One real download into a scratch data dir**

Save to the session scratchpad as `price_check.py` and run it with `.venv/Scripts/python.exe price_check.py`:

```python
import tempfile
import time
from pathlib import Path

if __name__ == "__main__":
    from pyfa_mcp import eosboot, evaluate, prices

    eosboot.boot(Path(tempfile.mkdtemp()))
    started = time.monotonic()
    print(prices.refresh(timeout=60))
    print(f"download: {time.monotonic() - started:.1f}s")
    import eos.db
    print("Damage Control II:", prices.price(eos.db.getItem("Damage Control II").ID))
    print(evaluate.evaluate("[Rifter, x]\nDamage Control II\n", None)["price"])
```

Expected: `refreshed: True`, state `ok`, a download of a few seconds, a positive Damage Control II price (≈ 300k–500k ISK on 2026-10-05), and a Rifter price block with no `partial`.

- [ ] **Step 2: Report** the printed output to the user, and that the branch is ready for review.
