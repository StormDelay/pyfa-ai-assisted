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
