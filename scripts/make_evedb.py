"""Generate vendor/Pyfa/eve.db from the submodule's staticdata.

    uv run python scripts/make_evedb.py

Runs Pyfa's own db_update.py with the wx stand-ins installed (it imports
Pyfa's config, which imports wx). Takes about 35 seconds.
"""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyfa_mcp import wxstub  # noqa: E402

wxstub.install()
script = ROOT / "vendor" / "Pyfa" / "db_update.py"
sys.argv = [str(script)]
runpy.run_path(str(script), run_name="__main__")
