"""Accept the effects eos has no handler for as reviewed.

    uv run python scripts/record_unhandled_effects.py

drift.new_unhandled_effects() (status() and test_effect_coverage_is_clean)
reports every effect outside pyfa_mcp/unhandled_effects.json. Run this only
after checking each new entry needs no handler (a fitting-slot marker, a
visual, ...): the diff of the json is the review. The Pyfa-bump pipeline
never runs it.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyfa_mcp import drift, eosboot  # noqa: E402

eosboot.boot(Path(tempfile.mkdtemp()))
ids = drift.unhandled_ids()
drift.BASELINE.write_text(json.dumps(ids, indent=0) + "\n", encoding="utf-8")
print(f"{len(ids)} unhandled effects recorded in {drift.BASELINE}", file=sys.stderr)
