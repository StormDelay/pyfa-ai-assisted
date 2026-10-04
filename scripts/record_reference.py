"""Recompute tests/reference/expected.json from the current engine.

    uv run python scripts/record_reference.py

Run after a Pyfa bump; review the diff of expected.json like code.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
REF = ROOT / "tests" / "reference"

from pyfa_mcp import eosboot, evaluate, stats  # noqa: E402

KEYS = ("validity.valid", "tank.ehp.total", "tank.ehp.shield", "tank.ehp.armor",
        "tank.ehp.hull", "tank.repair_ehp_per_s.total", "offense.dps.total",
        "offense.volley.total", "capacitor.stable", "navigation.max_speed",
        "navigation.align_time_s", "navigation.signature_m",
        "targeting.lock_range_m", "targeting.scan_resolution_mm")


def _resolve(value):
    if isinstance(value, str) and value.startswith("@"):
        return (REF / f"{value[1:]}.eft").read_text()
    if isinstance(value, dict):
        return {k: _resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v) for v in value]
    return value


def compute() -> dict:
    cases = json.loads((REF / "conditions.json").read_text())
    out = {}
    for case, spec in cases.items():
        fit = (REF / f"{spec.get('fit', case)}.eft").read_text()
        result = evaluate.evaluate(fit, _resolve(spec.get("conditions")))
        flat = stats.flatten({k: v for k, v in result.items() if k not in evaluate.META_KEYS})
        out[case] = {k: flat[k] for k in KEYS}
    return out


if __name__ == "__main__":
    eosboot.boot(Path(tempfile.mkdtemp()))
    text = json.dumps(compute(), indent=2, sort_keys=True)
    (REF / "expected.json").write_text(re.sub(r"(\d\.\d{6})\d+", r"\1", text) + "\n")
    print("wrote", REF / "expected.json")
