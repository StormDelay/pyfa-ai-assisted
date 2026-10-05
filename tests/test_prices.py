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
