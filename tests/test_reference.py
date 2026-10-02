import json
from pathlib import Path

import pytest

from scripts.record_reference import compute

REF = Path(__file__).resolve().parent / "reference"


def test_reference_fits_match_recorded_values(booted):
    expected = json.loads((REF / "expected.json").read_text())
    actual = compute()
    assert actual.keys() == expected.keys()
    for case, values in expected.items():
        for key, value in values.items():
            got = actual[case][key]
            if isinstance(value, float):
                assert got == pytest.approx(value, rel=1e-5), f"{case} {key}"
            else:
                assert got == value, f"{case} {key}"
