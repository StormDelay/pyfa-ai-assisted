"""Item prices: fuzzwork's bulk market aggregate, The Forge sell (5th percentile).

One download every few days, in a background thread, into the data dir
(prices.json). Tool calls read memory only and never wait on the network,
except refresh(), which the user asks for. An unknown price is None, never 0."""
from __future__ import annotations

import csv
import gzip
import io
import json
import math
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
            fetched_at = float(raw["fetched_at"])
            # hand-edited or clock-skewed: NaN, 1e20 or a future date never goes stale
            if not math.isfinite(fetched_at) or fetched_at > time.time() + 86400:
                raise ValueError(f"impossible fetched_at {fetched_at}")
            _data = {"fetched_at": fetched_at,
                     "prices": {int(k): float(v) for k, v in raw["prices"].items()
                                if math.isfinite(float(v)) and float(v) > 0}}
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
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
