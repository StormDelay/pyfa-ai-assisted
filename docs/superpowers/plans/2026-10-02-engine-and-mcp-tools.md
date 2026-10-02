# pyfa-mcp Plan 1 — Engine and MCP tools

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stdio MCP server, runnable from a checkout, that lets an LLM search the EVE item catalog, evaluate/compare/graph EFT fits under explicit conditions using Pyfa's own engine, and store fits in the server's own database.

**Architecture:** Pyfa v2.69.0 is a git submodule. A meta-path import hook stands in for wxPython and two GUI-only Pyfa modules so Pyfa's *service layer* (EFT port, fit service, GUI calc commands, graphs) imports headless (verified by a probe on 2026-10-02). Every evaluation imports the EFT into the server's own `saveddata.db` as a temporary fit, applies conditions through Pyfa's own calc commands, reads stats, and deletes the temporary fits. All eos work runs on one dedicated thread.

**Tech Stack:** Python 3.14 (uv-managed), Pyfa v2.69.0 (eos, SQLAlchemy 2.0.51, numpy 2.5.1), `mcp` 2.x (`MCPServer`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md`

This is plan 1 of 4. Plan 2: user's Pyfa data (`list_fits(source="pyfa")`, user profiles, `export_to_pyfa`) and drift checks. Plan 3: packaging and multi-client registration. Plan 4: release pipeline.

## Global Constraints

- Pyfa pinned at tag `v2.69.0`; Python version is Pyfa's `.python-version` (`3.14`); eos runtime dependency versions are exactly Pyfa's `uv.lock` versions: logbook 1.9.2, numpy 2.5.1, python-dateutil 2.9.0.post0, requests 2.34.2, sqlalchemy 2.0.51, cryptography 50.0.0, markdown2 2.5.5, packaging 26.2, roman 5.2, beautifulsoup4 4.15.0, pyyaml 6.0.3, python-jose 3.5.0, requests-cache 1.3.3.
- `mcp>=2.2,<3`.
- License GPL-3.0-or-later.
- Nothing in this project may point eos at `~/.pyfa` or write the user's Pyfa data. Server data lives in `~/.pyfa-mcp/` (overridable for tests).
- Tool bodies never print to stdout (it is the JSON-RPC stream).
- Every `fit` argument accepts EFT text (first non-blank line starts with `[`) or a stored fit's name or numeric id.
- Every evaluation result carries `applied` (every condition, defaults marked `(default)`) and `warnings`.
- Unknown names never pass silently: unknown items, profiles, fits, modules, side effects and impossible states are errors with close-match suggestions.
- Character is `"All 5"` only in this plan.
- Spool default is Pyfa's own default, `eos.config.settings["globalDefaultSpoolupPercentage"]` (1.0, i.e. full spool) — this matches what the Pyfa GUI shows. (Spec said "minimum"; corrected in Task 0 to match GUI parity.)

## Review Focus

1. **EFT with a misspelled module, charge, drone, implant or ship** — Pyfa's importer silently drops these; the user expects an error naming each bad line with suggestions, and no half-imported fit left in the DB. (Task 3)
2. **An exception midway through an evaluation** (e.g. a projected fit's EFT is bad after the main fit was imported) — the user expects the error, and no temporary fits left behind in `saveddata.db`. (Task 6)
3. **`module_states` naming a module the fit lacks, a `count` larger than fitted, or a state the module cannot take** (overheating a Damage Control) — the user expects a specific error, not a silently ignored setting. (Task 5)
4. **`compare_fits` where one of several fits is broken** — the user expects the other rows computed and the broken one carrying its error. (Task 6)
5. **Anything Pyfa logs or prints during a tool call** — the user expects the MCP connection to survive (stdout must stay clean). (Task 9)

---

## File Structure

```
pyproject.toml                 project + pinned deps
.gitignore
.gitmodules                    vendor/Pyfa
vendor/Pyfa/                   submodule @ v2.69.0 (eve.db generated, untracked)
scripts/make_evedb.py          generate vendor/Pyfa/eve.db through the wx stub
pyfa_mcp/__init__.py
pyfa_mcp/wxstub.py             import hook: wx + gui.mainFrame + gui.ssoLogin stand-ins
pyfa_mcp/eosboot.py            boot(): paths, engine wiring, migrations, temp-fit purge
pyfa_mcp/eft.py                EFT in/out with strict name checking
pyfa_mcp/catalog.py            search_items, list_ships, item_info
pyfa_mcp/stats.py              fit -> stats dict (validity, tank, offense, cap, nav, targeting, drones)
pyfa_mcp/conditions.py         parse/validate conditions; apply to a temp fit; applied echo
pyfa_mcp/store.py              stored-fit CRUD and fit-reference resolution
pyfa_mcp/evaluate.py           Scratch (temp-fit lifecycle), evaluate, compare
pyfa_mcp/graphs.py             Pyfa graphs as data points
pyfa_mcp/server.py             MCP tools, eos thread, error wrapper, instructions
pyfa_mcp/__main__.py           `python -m pyfa_mcp` runs the server
tests/conftest.py              session-wide boot into a tmp data dir; fixtures
tests/test_*.py                one per module
tests/reference/*.eft, expected.json   reference fits (Task 10)
scripts/record_reference.py    regenerate expected.json
packaging/mcp_smoke.py         stdio protocol smoke test
```

---

### Task 0: Correct the spec's spool default

**Files:**
- Modify: `docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md`

- [ ] **Step 1: Edit the spec**

In the `conditions` JSON block replace `"spool": "min" | "max" | "average" | 0.5,` with `"spool": "min" | "max" | 0.5,`. In the "Defaults:" sentence replace `minimum spool` with `Pyfa's default spool (full, as the Pyfa GUI shows)`. Under "Server `instructions`" nothing changes. Add one sentence after the conditions defaults paragraph:

```
Evaluation mechanics: each evaluation imports its fits into the server
database as temporary fits (marked in `notes`), applies conditions with
Pyfa's own GUI calc commands, reads stats, and deletes them; boot purges
any left by a crash.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md
git commit -m "Default spool to Pyfa's own default and record how evaluations use temporary fits"
```

---

### Task 1: Project skeleton, toolchain, Pyfa submodule, eve.db

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `pyfa_mcp/__init__.py`, `pyfa_mcp/wxstub.py`, `scripts/make_evedb.py`, `tests/test_wxstub.py`
- Submodule: `vendor/Pyfa`

**Interfaces:**
- Produces: `pyfa_mcp.wxstub.install() -> None` (idempotent; installs the import hook). `STUBBED_GUI: frozenset[str]`.

- [ ] **Step 1: Install uv and Python 3.14**

```bash
py -3.11 -m pip install --user uv
py -3.11 -m uv python install 3.14
py -3.11 -m uv --version
```
Expected: a `uv 0.x` version line. (If `uv` is on PATH afterwards, plain `uv` works in all later steps; otherwise use `py -3.11 -m uv`.)

- [ ] **Step 2: Add the submodule at v2.69.0**

```bash
git submodule add https://github.com/pyfa-org/Pyfa.git vendor/Pyfa
git -C vendor/Pyfa checkout v2.69.0
cat vendor/Pyfa/.python-version
```
Expected: `3.14`.

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[project]
name = "pyfa-mcp"
version = "0.1.0"
description = "MCP server exposing Pyfa's EVE Online fitting engine to LLM clients"
requires-python = ">=3.14,<3.15"
license = "GPL-3.0-or-later"
authors = [{ name = "Antoine Jacquin-Ravot" }]
# eos runtime dependencies: exactly vendor/Pyfa/uv.lock at the pinned tag.
dependencies = [
    "logbook==1.9.2",
    "numpy==2.5.1",
    "python-dateutil==2.9.0.post0",
    "requests==2.34.2",
    "sqlalchemy==2.0.51",
    "cryptography==50.0.0",
    "markdown2==2.5.5",
    "packaging==26.2",
    "roman==5.2",
    "beautifulsoup4==4.15.0",
    "pyyaml==6.0.3",
    "python-jose==3.5.0",
    "requests-cache==1.3.3",
    "mcp>=2.2,<3",
]

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["pyfa_mcp*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
```

- [ ] **Step 4: Write `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
build/
dist/
*.egg-info/
uv.lock
```
(`vendor/Pyfa/eve.db` is untracked inside the submodule; the submodule's own `.gitignore` already ignores it — verify with `git -C vendor/Pyfa check-ignore eve.db` after Step 8; if it prints nothing, add `eve.db` handling by running `git config -f .gitmodules submodule.vendor/Pyfa.ignore untracked`.)

- [ ] **Step 5: Sync the environment**

```bash
uv sync
uv run python --version
```
Expected: `Python 3.14.x`.

- [ ] **Step 6: Write the failing test `tests/test_wxstub.py`**

```python
import importlib
import sys

from pyfa_mcp import wxstub


def test_install_is_idempotent():
    wxstub.install()
    wxstub.install()
    assert sum(isinstance(f, wxstub._Finder) for f in sys.meta_path) == 1


def test_wx_names_are_usable_the_way_pyfa_uses_them():
    wxstub.install()
    import wx
    import wx.lib.newevent

    assert wx.GetTranslation("Lock Time") == "Lock Time"
    seen = []
    wx.CallAfter(seen.append, 1)
    assert seen == [1]
    event, binder = wx.lib.newevent.NewEvent()
    assert event is not binder

    class Command(wx.Command):  # Pyfa's calc commands subclass wx.Command
        def __init__(self, value):
            wx.Command.__init__(self, True, "name")
            self.value = value

    assert Command(5).value == 5
    assert int(wx.ID_ANY) == 0
    assert (wx.ALL | wx.EXPAND) is not None


def test_two_stub_bases_do_not_break_mro():
    wxstub.install()
    import wx

    class Event(wx.PyCommandEvent, wx.Window):
        pass

    assert Event() is not None


def test_gui_only_modules_are_stubbed():
    wxstub.install()
    assert "gui.mainFrame" in wxstub.STUBBED_GUI
    module = importlib.import_module("gui.ssoLogin")
    assert module.SsoLogin is not None
```

- [ ] **Step 7: Run it to see it fail**

Run: `uv run pytest tests/test_wxstub.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pyfa_mcp'` (or `wxstub`).

- [ ] **Step 8: Write `pyfa_mcp/__init__.py` (empty) and `pyfa_mcp/wxstub.py`**

```python
"""Stand-ins for wxPython and two GUI-only Pyfa modules.

Pyfa's service layer -- EFT import/export, the fit service, the GUI's calc
commands, the graphs -- imports wx for translations, CallAfter, event
classes and wx.Command base classes, none of which do anything we need.
An import hook answers every `wx` / `wx.*` import with a module whose every
attribute is a cached, subclassable, callable stand-in.

`gui.mainFrame` and `gui.ssoLogin` are stubbed too: service.esi imports
gui.ssoLogin -> gui.mainFrame -> gui.fitCommands -> service.fit, a cycle the
Pyfa GUI only survives because it imports the main window first. We never
need SSO or the main window.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types

STUBBED_GUI = frozenset({"gui.mainFrame", "gui.ssoLogin"})


class _Meta(type):
    """Class-level behaviour: wx.ID_ANY, wx.ALL | wx.EXPAND, int(wx.X)."""

    def __getattr__(cls, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _stub(name)

    def __or__(cls, other):
        return cls

    __ror__ = __and__ = __or__

    def __int__(cls):
        return 0

    def __index__(cls):
        return 0

    def __iter__(cls):
        return iter(())


class _Base(metaclass=_Meta):
    def __init__(self, *args, **kwargs):
        pass

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _stub(name)

    def __call__(self, *args, **kwargs):
        return _stub("Result")()


# One class per name, so `class X(wx.A, wx.B)` gets two distinct bases and
# the same name always yields the same class.
_classes: dict[str, type] = {}


def _stub(name: str):
    if name == "GetTranslation":
        return lambda text, *args, **kwargs: text
    if name == "CallAfter":
        return lambda fn, *args, **kwargs: fn(*args, **kwargs)
    if name in ("NewEvent", "NewCommandEvent"):
        def new_event():
            index = len(_classes)
            return _stub(f"Event{index}"), _stub(f"Binder{index}")
        return new_event
    if name not in _classes:
        _classes[name] = _Meta(name, (_Base,), {})
    return _classes[name]


class _StubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _stub(name)


class _Finder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        if name == "wx" or name.startswith("wx.") or name in STUBBED_GUI:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        return _StubModule(spec.name)

    def exec_module(self, module):
        module.__path__ = []


def install() -> None:
    if not any(isinstance(f, _Finder) for f in sys.meta_path):
        sys.meta_path.insert(0, _Finder())
```

- [ ] **Step 9: Run the tests**

Run: `uv run pytest tests/test_wxstub.py -v`
Expected: 4 passed.

- [ ] **Step 10: Write `scripts/make_evedb.py`**

```python
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
```

- [ ] **Step 11: Generate eve.db and check it**

```bash
uv run python scripts/make_evedb.py
uv run python -c "import sqlite3; print(sqlite3.connect('vendor/Pyfa/eve.db').execute('select * from metadata').fetchall())"
```
Expected: last line contains `('schema_version', '4')` and a `client_build`.

- [ ] **Step 12: Commit**

```bash
git add pyproject.toml .gitignore .gitmodules vendor/Pyfa pyfa_mcp scripts tests
git commit -m "Pin Pyfa v2.69.0, stand in for wx, and generate eve.db headless"
```

---

### Task 2: Boot eos against the server's own database

**Files:**
- Create: `pyfa_mcp/eosboot.py`, `tests/conftest.py`, `tests/test_eosboot.py`

**Interfaces:**
- Consumes: `wxstub.install()`.
- Produces:
  - `eosboot.TEMP_NOTE: str = "pyfa-mcp:temp"`
  - `eosboot.BootError(RuntimeError)`
  - `eosboot.pyfa_dir() -> Path`, `eosboot.default_data_dir() -> Path`
  - `eosboot.boot(data_dir: Path | None = None) -> Path` — idempotent; returns the data dir in use; a second call with a different dir raises `BootError`.
  - `eosboot.pyfa_version() -> str` (from `version.yml`, e.g. `"v2.69.0"`).
  - Test fixture `booted` (session scope) returning the data dir.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
import pytest

from pyfa_mcp import eosboot

ZEALOT = """[Zealot, Test Zealot]
Heat Sink II
Heat Sink II
Damage Control II
Multispectrum Energized Membrane II
Medium Armor Repairer II

50MN Microwarpdrive II
Warp Disruptor II
Stasis Webifier II

Heavy Pulse Laser II, Scorch M
Heavy Pulse Laser II, Scorch M
Heavy Pulse Laser II, Scorch M
Heavy Pulse Laser II, Scorch M
Heavy Pulse Laser II, Scorch M

Medium Energy Locus Coordinator II
Medium Energy Metastasis Adjuster II
"""


@pytest.fixture(scope="session")
def booted(tmp_path_factory):
    """eos can be booted once per process, so the whole run shares one."""
    return eosboot.boot(tmp_path_factory.mktemp("pyfa-mcp-data"))


@pytest.fixture
def zealot_eft():
    return ZEALOT


@pytest.fixture
def no_fits_left(booted):
    """Fails the test if it left any fit (temporary or not) in the DB."""
    from service.fit import Fit

    before = {f.ID for f in Fit.getAllFits()}
    yield
    after = {f.ID for f in Fit.getAllFits()}
    assert after == before, f"fits left behind: {after - before}"
```

- [ ] **Step 2: Write the failing tests `tests/test_eosboot.py`**

```python
from pathlib import Path

import pytest

from pyfa_mcp import eosboot


def test_boot_points_eos_at_our_database_only(booted):
    import eos.config
    import eos.db

    ours = (booted / "saveddata.db").resolve()
    url = str(eos.db.saveddata_engine.url).removeprefix("sqlite:///")
    assert Path(url).resolve() == ours
    assert Path.home() / ".pyfa" not in Path(url).resolve().parents
    assert eos.config.saveddata_connectionstring.endswith("saveddata.db")
    assert (booted / "saveddata.db").is_file()


def test_reused_pyfa_modules_imported(booted):
    import sys

    for name in ("service.fit", "service.port", "service.port.eft",
                 "graphs.data", "gui.fitCommands.helpers"):
        assert name in sys.modules


def test_boot_is_idempotent(booted):
    assert eosboot.boot(booted) == booted


def test_second_boot_with_other_dir_is_refused(booted, tmp_path):
    with pytest.raises(eosboot.BootError):
        eosboot.boot(tmp_path)


def test_temp_fits_are_purged_on_boot(booted):
    import eos.db
    from service.fit import Fit
    from service.port import Port

    _, fits = Port.importFitFromBuffer("[Rifter, leftover]\n")
    fits[0].notes = eosboot.TEMP_NOTE
    eos.db.commit()
    eosboot._purge_temp_fits()
    assert all(f.notes != eosboot.TEMP_NOTE for f in Fit.getAllFits())


def test_pyfa_version(booted):
    assert eosboot.pyfa_version() == "v2.69.0"
```

- [ ] **Step 3: Run to see them fail**

Run: `uv run pytest tests/test_eosboot.py -v`
Expected: FAIL — `ImportError: cannot import name 'eosboot'`.

- [ ] **Step 4: Write `pyfa_mcp/eosboot.py`**

```python
"""Boot Pyfa's engine headless, pointed at this server's own databases.

eos builds its SQLAlchemy engines once per process from `eos.config`, and
Pyfa's `config.defPaths()` would re-point them at the user's ~/.pyfa. We
never call defPaths: boot sets the connection strings and Pyfa's config
paths itself, then imports `service.prefetch`, which creates or migrates
saveddata.db exactly as Pyfa does at startup.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import yaml

from pyfa_mcp import wxstub

TEMP_NOTE = "pyfa-mcp:temp"

_ROOT = Path(__file__).resolve().parent.parent
_booted_dir: Path | None = None


class BootError(RuntimeError):
    """Pyfa's source, eve.db, or our data dir is missing or miswired."""


def pyfa_dir() -> Path:
    """The Pyfa source tree: the submodule, or its copy in a frozen build."""
    bundled = getattr(sys, "_MEIPASS", None)
    return Path(bundled) / "pyfa" if bundled else _ROOT / "vendor" / "Pyfa"


def default_data_dir() -> Path:
    return Path.home() / ".pyfa-mcp"


def pyfa_version() -> str:
    with open(pyfa_dir() / "version.yml", encoding="utf-8") as f:
        return yaml.safe_load(f)["version"]


def boot(data_dir: Path | None = None) -> Path:
    global _booted_dir
    data_dir = Path(data_dir) if data_dir else default_data_dir()
    if _booted_dir is not None:
        if data_dir.resolve() != _booted_dir.resolve():
            raise BootError(
                f"eos is already booted on {_booted_dir}; it cannot be re-pointed")
        return _booted_dir

    source = pyfa_dir()
    eve_db = source / "eve.db"
    if not (source / "eos" / "__init__.py").is_file():
        raise BootError(f"no Pyfa source at {source}; run: git submodule update --init")
    if not eve_db.is_file():
        raise BootError(f"no eve.db at {eve_db}; run: uv run python scripts/make_evedb.py")
    data_dir.mkdir(parents=True, exist_ok=True)
    save_db = data_dir / "saveddata.db"

    wxstub.install()
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

    # Pyfa prints during import and migration; stdout is our JSON-RPC stream.
    with contextlib.redirect_stdout(sys.stderr):
        import eos.config

        eos.config.gamedata_connectionstring = f"sqlite:///{eve_db}"
        eos.config.saveddata_connectionstring = f"sqlite:///{save_db}"

        import config

        config.pyfaPath = str(source)
        config.savePath = str(data_dir)
        config.saveDB = str(save_db)
        config.gameDB = str(eve_db)

        import eos.db  # noqa: F401 -- builds the engines from eos.config
        import service.prefetch  # noqa: F401 -- create or migrate saveddata.db
        # Imported here so a Pyfa change that breaks them fails at boot.
        import graphs.data  # noqa: F401
        import gui.fitCommands.helpers  # noqa: F401
        import service.fit  # noqa: F401
        import service.port  # noqa: F401

        _check_engine(save_db)
        _purge_temp_fits()

    _booted_dir = data_dir
    return data_dir


def _check_engine(save_db: Path) -> None:
    import eos.db

    url = str(eos.db.saveddata_engine.url).removeprefix("sqlite:///")
    if Path(url).resolve() != save_db.resolve():
        raise BootError(f"eos saveddata engine points at {url}, expected {save_db}")


def _purge_temp_fits() -> None:
    """Delete temporary fits a crashed evaluation left behind."""
    from service.fit import Fit

    for fit in Fit.getAllFits():
        if fit.notes == TEMP_NOTE:
            Fit.deleteFit(fit.ID)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_eosboot.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add pyfa_mcp/eosboot.py tests/conftest.py tests/test_eosboot.py
git commit -m "Boot Pyfa's service layer headless on the server's own saveddata.db"
```

---

### Task 3: EFT in and out, refusing unknown names

**Files:**
- Create: `pyfa_mcp/eft.py`, `tests/test_eft.py`

**Interfaces:**
- Consumes: `eosboot.TEMP_NOTE`, booted eos.
- Produces:
  - `eft.EftError(ValueError)`
  - `eft.import_fit(text: str, *, name: str | None = None, temp: bool = False) -> eos.saveddata.fit.Fit` — saved in the server DB, fully calculated (via `service.fit.Fit.getFit`). On any error no fit is left behind.
  - `eft.export_fit(fit) -> str`
  - `eft.suggest(name: str, n: int = 3) -> list[str]` — close published item names.
  - `eft.looks_like_eft(text: str) -> bool`

- [ ] **Step 1: Write the failing tests `tests/test_eft.py`**

```python
import pytest

from pyfa_mcp import eft


def _delete(fit):
    from service.fit import Fit
    Fit.deleteFit(fit.ID)


def test_round_trip(booted, zealot_eft, no_fits_left):
    fit = eft.import_fit(zealot_eft)
    try:
        text = eft.export_fit(fit)
        assert text.splitlines()[0] == "[Zealot, Test Zealot]"
        assert text.count("Heavy Pulse Laser II, Scorch M") == 5
        assert fit.getTotalDps().total > 0  # charges loaded, fit calculated
    finally:
        _delete(fit)


def test_name_and_temp_flag(booted, zealot_eft, no_fits_left):
    from pyfa_mcp.eosboot import TEMP_NOTE
    fit = eft.import_fit(zealot_eft, name="Renamed", temp=True)
    try:
        assert fit.name == "Renamed"
        assert fit.notes == TEMP_NOTE
    finally:
        _delete(fit)


@pytest.mark.parametrize("text, bad", [
    ("[Zealout, x]\nHeat Sink II\n", "Zealout"),
    ("[Zealot, x]\nHeat Sinkk II\nDamage Control II\n", "Heat Sinkk II"),
    ("[Zealot, x]\nHeavy Pulse Laser II, Scorch Medium\n", "Scorch Medium"),
    ("[Zealot, x]\n\nHobgoblin III x1\n", "Hobgoblin III"),
    ("[Zealot, x]\n\nStrong Blue Pill Boostr\n", "Strong Blue Pill Boostr"),
])
def test_unknown_names_are_errors_and_leave_nothing(booted, no_fits_left, text, bad):
    with pytest.raises(eft.EftError) as caught:
        eft.import_fit(text)
    assert f"'{bad}'" in str(caught.value)


def test_unknown_name_comes_with_suggestions(booted, no_fits_left):
    with pytest.raises(eft.EftError, match="did you mean: .*Heat Sink II"):
        eft.import_fit("[Zealot, x]\nHeat Sinkk II\n")


def test_every_bad_line_is_reported(booted, no_fits_left):
    with pytest.raises(eft.EftError) as caught:
        eft.import_fit("[Zealot, x]\nHeat Sinkk II\nDamage Contrl II\n")
    assert "Heat Sinkk II" in str(caught.value)
    assert "Damage Contrl II" in str(caught.value)


@pytest.mark.parametrize("text", ["Heat Sink II\n", "", "   \n", "[Zealot]\n"])
def test_missing_header_is_an_error(booted, no_fits_left, text):
    with pytest.raises(eft.EftError, match="header"):
        eft.import_fit(text)


def test_offline_modules_survive(booted, no_fits_left):
    from eos.const import FittingModuleState
    fit = eft.import_fit("[Zealot, x]\nHeat Sink II /OFFLINE\n")
    try:
        assert fit.modules[0].state == FittingModuleState.OFFLINE
    finally:
        _delete(fit)


def test_looks_like_eft():
    assert eft.looks_like_eft("\n  [Zealot, x]\n")
    assert not eft.looks_like_eft("My Zealot")
    assert not eft.looks_like_eft("12")
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_eft.py -v`
Expected: FAIL — `ImportError: cannot import name 'eft'`.

- [ ] **Step 3: Write `pyfa_mcp/eft.py`**

```python
"""EFT text in and out of the server's fit database.

Pyfa's EFT importer drops any line whose item it cannot find and imports
the rest. Every name it resolves goes through `service.port.eft.fetchItem`,
so while importing we wrap that function and record each miss; a fit with
misses is deleted and reported instead of returned.
"""
from __future__ import annotations

import contextlib
import difflib
import functools
import re

from pyfa_mcp.eosboot import TEMP_NOTE

_HEADER = re.compile(r"^\[[^,\]]+,[^\]]*\]$")


class EftError(ValueError):
    """EFT text Pyfa cannot import completely."""


def looks_like_eft(text: str) -> bool:
    return text.lstrip().startswith("[")


@functools.cache
def _published_names() -> tuple[str, ...]:
    import eos.db

    with eos.db.gamedata_engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT typeName FROM invtypes WHERE published = 1").fetchall()
    return tuple(name for (name,) in rows)


def suggest(name: str, n: int = 3) -> list[str]:
    return difflib.get_close_matches(name, _published_names(), n=n, cutoff=0.6)


def _describe_miss(name: str) -> str:
    close = suggest(name)
    hint = f" (did you mean: {', '.join(close)}?)" if close else ""
    return f"unknown item '{name}'{hint}"


@contextlib.contextmanager
def _recording_misses():
    import service.port.eft as pyfa_eft

    real = pyfa_eft.fetchItem
    misses: list[str] = []

    def fetch(typeName, eagerCat=False):
        item = real(typeName, eagerCat=eagerCat)
        if item is None:
            misses.append(typeName)
        return item

    pyfa_eft.fetchItem = fetch
    try:
        yield misses
    finally:
        pyfa_eft.fetchItem = real


def import_fit(text: str, *, name: str | None = None, temp: bool = False):
    import eos.db
    from service.fit import Fit as FitService
    from service.port import Port

    text = text.strip()
    first = text.splitlines()[0].strip() if text else ""
    if not _HEADER.match(first):
        raise EftError(
            "EFT text must start with a '[Ship, Fit name]' header line, "
            f"got {first[:60]!r}")

    failure = None
    with _recording_misses() as misses:
        try:
            _, fits = Port.importFitFromBuffer(text)
        except Exception as exc:  # Pyfa raises on e.g. an unknown hull
            fits, failure = [], exc
    fit = next((f for f in fits if f is not None), None)

    if misses or failure is not None or fit is None:
        if fit is not None:
            FitService.deleteFit(fit.ID)
        if misses:
            raise EftError("; ".join(_describe_miss(m) for m in dict.fromkeys(misses)))
        raise EftError(f"Pyfa could not read this EFT text: {failure}")

    if name:
        fit.name = name
    if temp:
        fit.notes = TEMP_NOTE
    eos.db.commit()
    return FitService.getInstance().getFit(fit.ID)


def export_fit(fit) -> str:
    from service.const import PortEftOptions
    from service.port import Port

    options = {option.value: True for option in PortEftOptions}
    return Port.exportEft(fit, options=options)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_eft.py -v`
Expected: all passed. If `test_missing_header_is_an_error[[Zealot]\n]` fails because Pyfa accepts a comma-less header, keep the stricter `_HEADER` (the test is the spec).

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/eft.py tests/test_eft.py
git commit -m "Import EFT through Pyfa and refuse fits with unknown item names"
```

---

### Task 4: Fit statistics

**Files:**
- Create: `pyfa_mcp/stats.py`, `tests/test_stats.py`

**Interfaces:**
- Consumes: a calculated `eos.saveddata.fit.Fit`.
- Produces:
  - `stats.fit_stats(fit, spool: float) -> dict` with keys `validity`, `tank`, `offense`, `capacitor`, `navigation`, `targeting`, `drones` (shapes below).
  - `stats.flatten(d: dict, prefix="") -> dict[str, object]` — dotted keys (`"tank.ehp.total"`).
  - `stats.DEFAULT_COMPARE: tuple[str, ...]` — dotted keys used by `compare_fits` when `stats` is omitted.

- [ ] **Step 1: Write the failing tests `tests/test_stats.py`**

```python
import pytest

from pyfa_mcp import eft, stats


@pytest.fixture
def zealot(booted, zealot_eft):
    from service.fit import Fit
    fit = eft.import_fit(zealot_eft)
    yield fit
    Fit.deleteFit(fit.ID)


def test_shape(zealot):
    s = stats.fit_stats(zealot, spool=1.0)
    assert set(s) == {"validity", "tank", "offense", "capacitor",
                      "navigation", "targeting", "drones"}
    assert s["validity"]["valid"] is True
    assert s["validity"]["problems"] == []
    assert s["validity"]["slots"]["low"] == {"used": 5, "total": 7}
    assert s["validity"]["hardpoints"]["turret"] == {"used": 5, "total": 5}
    assert s["tank"]["ehp"]["total"] == pytest.approx(
        sum(s["tank"]["ehp"][k] for k in ("shield", "armor", "hull")))
    assert 0 < s["tank"]["resists"]["armor"]["em"] < 1
    assert s["offense"]["dps"]["total"] > 300
    assert s["targeting"]["lock_range_m"] > 0
    assert s["navigation"]["max_speed"] > 0


def test_overfit_is_reported_not_raised(booted, no_fits_left):
    from service.fit import Fit
    fit = eft.import_fit("[Zealot, over]\n" + "Heat Sink II\n" * 9)
    try:
        s = stats.fit_stats(fit, spool=1.0)
        assert s["validity"]["valid"] is False
        assert any("low slots" in p for p in s["validity"]["problems"])
    finally:
        Fit.deleteFit(fit.ID)


def test_powergrid_overuse_is_a_problem(booted, no_fits_left):
    from service.fit import Fit
    # Battleship guns on a frigate: far over powergrid, whatever the skills.
    fit = eft.import_fit("[Rifter, pg]\n\n\n" + "Neutron Blaster Cannon II\n" * 3)
    try:
        s = stats.fit_stats(fit, spool=1.0)
        pg = s["validity"]["powergrid"]
        assert pg["used"] > pg["total"]
        assert any("powergrid" in p for p in s["validity"]["problems"])
    finally:
        Fit.deleteFit(fit.ID)


def test_flatten(zealot):
    flat = stats.flatten(stats.fit_stats(zealot, spool=1.0))
    assert "tank.ehp.total" in flat
    for key in stats.DEFAULT_COMPARE:
        assert key in flat, key
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_stats.py -v`
Expected: FAIL — `ImportError: cannot import name 'stats'`.

- [ ] **Step 3: Write `pyfa_mcp/stats.py`**

```python
"""A calculated eos fit as plain data, under one spool setting."""
from __future__ import annotations

_LAYERS = {"shield": "shield", "armor": "armor", "hull": ""}
_TYPES = ("em", "thermal", "kinetic", "explosive")

DEFAULT_COMPARE = (
    "validity.valid",
    "tank.ehp.total",
    "tank.repair_ehp_per_s.total",
    "offense.dps.total",
    "offense.volley.total",
    "capacitor.stable",
    "navigation.max_speed",
    "navigation.align_time_s",
    "navigation.signature_m",
    "targeting.lock_range_m",
    "targeting.scan_resolution_mm",
)


def _resonance_attr(layer: str, damage_type: str) -> str:
    prefix = _LAYERS[layer]
    if not prefix:
        return f"{damage_type}DamageResonance"
    return f"{prefix}{damage_type.capitalize()}DamageResonance"


def _spool_options(spool: float):
    from eos.const import SpoolType
    from eos.utils.spoolSupport import SpoolOptions

    return SpoolOptions(SpoolType.SPOOL_SCALE, spool, True)


def _validity(fit) -> dict:
    from eos.const import FittingHardpoint, FittingSlot

    ship = fit.ship
    problems: list[str] = []

    def resource(label, used, total):
        if used > total + 1e-6:
            problems.append(f"{label} over by {used - total:g}")
        return {"used": used, "total": total}

    cpu = resource("CPU", fit.cpuUsed, ship.getModifiedItemAttr("cpuOutput"))
    pg = resource("powergrid", fit.pgUsed, ship.getModifiedItemAttr("powerOutput"))
    cal = resource("calibration", fit.calibrationUsed,
                   ship.getModifiedItemAttr("upgradeCapacity"))

    slots = {}
    for label, slot in (("high", FittingSlot.HIGH), ("mid", FittingSlot.MED),
                        ("low", FittingSlot.LOW), ("rig", FittingSlot.RIG),
                        ("subsystem", FittingSlot.SUBSYSTEM)):
        used, total = fit.getSlotsUsed(slot), int(fit.getNumSlots(slot))
        if used > total:
            problems.append(f"{used - total} too many {label} slots")
        slots[label] = {"used": used, "total": total}

    hardpoints = {}
    for label, kind, attr in (("turret", FittingHardpoint.TURRET, "turretSlotsLeft"),
                              ("launcher", FittingHardpoint.MISSILE, "launcherSlotsLeft")):
        used = fit.getHardpointsUsed(kind)
        total = int(ship.getModifiedItemAttr(attr) or 0)
        if used > total:
            problems.append(f"{used - total} too many {label}s")
        hardpoints[label] = {"used": used, "total": total}

    for mod in fit.modules:
        if not mod.isEmpty and not mod.fits(fit):
            problems.append(f"{mod.item.name} cannot be fitted to this ship")

    return {"valid": not problems, "problems": problems, "cpu": cpu,
            "powergrid": pg, "calibration": cal, "slots": slots,
            "hardpoints": hardpoints}


def _tank(fit) -> dict:
    ship = fit.ship
    ehp = {layer: fit.ehp[layer] for layer in _LAYERS}
    ehp["total"] = sum(ehp.values())
    hp = {layer: fit.hp[layer] for layer in _LAYERS}
    resists = {
        layer: {t: 1 - ship.getModifiedItemAttr(_resonance_attr(layer, t))
                for t in _TYPES}
        for layer in _LAYERS
    }
    reps = fit.effectiveTank
    repair = {
        "passive_shield": reps["passiveShield"],
        "shield": reps["shieldRepair"],
        "armor": reps["armorRepair"],
        "hull": reps["hullRepair"],
    }
    repair["total"] = sum(repair.values())
    return {"hp": hp, "ehp": ehp, "resists": resists, "repair_ehp_per_s": repair}


def _dmg(dmg) -> dict:
    return {"total": dmg.total, "em": dmg.em, "thermal": dmg.thermal,
            "kinetic": dmg.kinetic, "explosive": dmg.explosive}


def _offense(fit, spool: float) -> dict:
    options = _spool_options(spool)
    return {
        "dps": _dmg(fit.getTotalDps(spoolOptions=options)),
        "volley": _dmg(fit.getTotalVolley(spoolOptions=options)),
        "weapon_dps": fit.getWeaponDps(spoolOptions=options).total,
        "drone_dps": fit.getDroneDps().total,
    }


def _capacitor(fit) -> dict:
    stable = bool(fit.capStable)
    return {
        "capacity": fit.ship.getModifiedItemAttr("capacitorCapacity"),
        "recharge_s": fit.ship.getModifiedItemAttr("rechargeRate") / 1000,
        "stable": stable,
        "stable_at_percent": fit.capState if stable else None,
        "lasts_s": None if stable else fit.capState,
        "delta_per_s": fit.capDelta,
    }


def _navigation(fit) -> dict:
    ship = fit.ship
    return {
        "max_speed": fit.maxSpeed,
        "align_time_s": fit.alignTime,
        "signature_m": ship.getModifiedItemAttr("signatureRadius"),
        "mass_kg": ship.getModifiedItemAttr("mass"),
        "warp_speed_au_s": fit.warpSpeed,
    }


def _targeting(fit) -> dict:
    return {
        "lock_range_m": fit.maxTargetRange,
        "scan_resolution_mm": fit.ship.getModifiedItemAttr("scanResolution"),
        "max_targets": fit.maxTargets,
        "sensor_strength": fit.scanStrength,
    }


def _drones(fit) -> dict:
    ship = fit.ship
    return {
        "bandwidth": {"used": fit.droneBandwidthUsed,
                      "total": ship.getModifiedItemAttr("droneBandwidth") or 0},
        "bay": {"used": fit.droneBayUsed,
                "total": ship.getModifiedItemAttr("droneCapacity") or 0},
        "active": fit.activeDrones,
    }


def fit_stats(fit, spool: float) -> dict:
    return {
        "validity": _validity(fit),
        "tank": _tank(fit),
        "offense": _offense(fit, spool),
        "capacitor": _capacitor(fit),
        "navigation": _navigation(fit),
        "targeting": _targeting(fit),
        "drones": _drones(fit),
    }


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in d.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, path + "."))
        else:
            out[path] = value
    return out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_stats.py -v`
Expected: all passed. If an attribute name differs in eos v2.69 (AttributeError), find the right name with `grep -n "def <name>" vendor/Pyfa/eos/saveddata/fit.py` and fix it; do not drop the field.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/stats.py tests/test_stats.py
git commit -m "Read a calculated fit's validity, tank, offense, cap, navigation and targeting"
```

---

### Task 5: Conditions — parse, validate, apply, echo

**Files:**
- Create: `pyfa_mcp/conditions.py`, `tests/test_conditions.py`

**Interfaces:**
- Consumes: `eft.suggest`, `eft.import_fit`; a temp fit already in the DB.
- Produces:
  - `conditions.ConditionsError(ValueError)`
  - `conditions.Conditions` (frozen dataclass): `character: str`, `damage_profile: str | dict`, `target: str | dict | None`, `module_states: tuple[dict, ...]`, `spool: float`, `drug_side_effects: tuple[dict, ...]`, `command: tuple[dict, ...]`, `projected: tuple[dict, ...]`, `explicit: frozenset[str]` (keys the caller set).
  - `conditions.parse(raw: dict | None) -> Conditions`
  - `conditions.apply(fit, cond: Conditions, add_fit: Callable[[str], Fit]) -> dict` — mutates the temp fit, recalculates, returns the `applied` echo. `add_fit(ref)` must return a new temp fit (Task 6 supplies it).
  - `conditions.describe() -> dict` — the schema, examples, and built-in profile names for `conditions_format()`.
  - `conditions.target_profile(cond) -> TargetProfile | None` (used by graphs).

- [ ] **Step 1: Write the failing tests `tests/test_conditions.py`**

```python
import pytest

from pyfa_mcp import conditions as C
from pyfa_mcp import eft


@pytest.fixture
def temp_fits(booted):
    """add_fit for apply(); deletes everything it created."""
    from service.fit import Fit
    made = []

    def add_fit(ref):
        fit = eft.import_fit(ref, temp=True)
        made.append(fit.ID)
        return fit

    yield add_fit
    for fit_id in made:
        Fit.deleteFit(fit_id)


def test_parse_defaults():
    cond = C.parse(None)
    assert cond.character == "All 5"
    assert cond.spool == 1.0
    assert cond.damage_profile == "uniform"
    assert cond.explicit == frozenset()


@pytest.mark.parametrize("raw, message", [
    ({"bogus": 1}, "unknown condition 'bogus'"),
    ({"character": "Bob"}, "All 5"),
    ({"spool": 1.5}, "spool"),
    ({"spool": "average"}, "spool"),
    ({"damage_profile": {"em": 1}}, "damage_profile"),
    ({"damage_profile": {"em": -1, "thermal": 1, "kinetic": 1, "explosive": 1}}, "damage_profile"),
    ({"target": {"resists": {"em": 2}}}, "resists"),
    ({"module_states": [{"module": "X", "state": "angry"}]}, "state"),
    ({"projected": [{"item": "X", "fit": "Y"}]}, "exactly one"),
    ({"projected": [{"count": 2}]}, "exactly one"),
])
def test_parse_rejects(raw, message):
    with pytest.raises(C.ConditionsError, match=message):
        C.parse(raw)


def test_spool_words():
    assert C.parse({"spool": "min"}).spool == 0.0
    assert C.parse({"spool": "max"}).spool == 1.0
    assert C.parse({"spool": 0.25}).spool == 0.25


def test_apply_defaults_echo(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    applied = C.apply(fit, C.parse(None), temp_fits)
    assert applied["character"] == "All 5 (default)"
    assert applied["spool"].endswith("(default)")
    assert applied["damage_profile"].endswith("(default)")
    assert applied["projected"] == "none (default)"


def test_projected_webs_slow_the_fit(temp_fits, zealot_eft):
    base = temp_fits(zealot_eft)
    C.apply(base, C.parse(None), temp_fits)
    speed = base.maxSpeed
    webbed = temp_fits(zealot_eft)
    applied = C.apply(webbed, C.parse(
        {"projected": [{"item": "Stasis Webifier II", "count": 2}]}), temp_fits)
    assert webbed.maxSpeed < speed / 2
    assert "Stasis Webifier II x2" in applied["projected"][0]


def test_projected_fit(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    applied = C.apply(fit, C.parse({"projected": [
        {"fit": "[Scimitar, logi]\nLarge Remote Shield Booster II\n", "count": 1}]}),
        temp_fits)
    assert [f.name for f in fit.projectedFits] == ["logi"]
    assert "logi" in applied["projected"][0]


def test_overheat_raises_repair(temp_fits, zealot_eft):
    cold = temp_fits(zealot_eft)
    C.apply(cold, C.parse(None), temp_fits)
    hot = temp_fits(zealot_eft)
    C.apply(hot, C.parse({"module_states": [
        {"module": "Medium Armor Repairer II", "state": "overheated"}]}), temp_fits)
    assert hot.tank["armorRepair"] > cold.tank["armorRepair"]


@pytest.mark.parametrize("states, message", [
    ([{"module": "Large Shield Booster II", "state": "active"}], "not fitted"),
    ([{"module": "Heat Sink II", "state": "active", "count": 3}], "only 2"),
    ([{"module": "Damage Control II", "state": "overheated"}], "cannot be overheated"),
])
def test_module_state_errors(temp_fits, zealot_eft, states, message):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match=message):
        C.apply(fit, C.parse({"module_states": states}), temp_fits)


def test_unknown_projected_item_suggests(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="Stasis Webifier II"):
        C.apply(fit, C.parse({"projected": [{"item": "Stasis Webifer II"}]}), temp_fits)


def test_damage_profile_changes_ehp(temp_fits, zealot_eft):
    uniform = temp_fits(zealot_eft)
    C.apply(uniform, C.parse(None), temp_fits)
    em = temp_fits(zealot_eft)
    C.apply(em, C.parse({"damage_profile":
        {"em": 1, "thermal": 0, "kinetic": 0, "explosive": 0}}), temp_fits)
    assert em.ehp["armor"] != pytest.approx(uniform.ehp["armor"])


def test_unknown_damage_profile_name(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="damage profile"):
        C.apply(fit, C.parse({"damage_profile": "Nope"}), temp_fits)


def test_drug_side_effect(temp_fits):
    fit = temp_fits("[Zealot, drug]\n\n\nStrong Blue Pill Booster\n")
    booster = fit.boosters[0]
    effect = booster.sideEffects[0]
    description = effect.name.split(" ", 1)[1]  # "-10% Shield Capacity" -> "Shield Capacity"
    applied = C.apply(fit, C.parse({"drug_side_effects": [
        {"drug": "Strong Blue Pill Booster", "effect": description}]}), temp_fits)
    assert effect.active
    assert "Strong Blue Pill Booster" in applied["drug_side_effects"][0]


def test_drug_not_in_fit(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="not in the fit"):
        C.apply(fit, C.parse({"drug_side_effects": [
            {"drug": "Strong Blue Pill Booster", "effect": "shield"}]}), temp_fits)


def test_command_burst_fit(temp_fits, zealot_eft):
    fit = temp_fits(zealot_eft)
    booster_eft = ("[Damnation, boosts]\n\n\n"
                   "Armor Command Burst II, Armor Energizing Charge\n")
    applied = C.apply(fit, C.parse({"command": [{"fit": booster_eft}]}), temp_fits)
    assert [f.name for f in fit.commandFits] == ["boosts"]
    assert "boosts" in applied["command"][0]


def test_describe_lists_profiles():
    d = C.describe()
    assert "uniform" in d["damage_profiles"]
    assert d["fields"]["spool"]
    assert d["examples"]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_conditions.py -v`
Expected: FAIL — `ImportError: cannot import name 'conditions'`.

- [ ] **Step 3: Write `pyfa_mcp/conditions.py`**

```python
"""Everything about an evaluation that EFT text cannot say.

`parse` validates shape and values without touching eos; `apply` resolves
names against the fit and the game data and changes a *temporary* fit
through Pyfa's own GUI calc commands, so the result is what the Pyfa GUI
would show for the same clicks. Every value `apply` used comes back in the
`applied` echo, defaults marked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from pyfa_mcp.eft import suggest

_DAMAGE_KEYS = ("em", "thermal", "kinetic", "explosive")
_STATES = ("offline", "online", "active", "overheated")
_FIELDS = {
    "character": "Pilot skills. Only \"All 5\" in this version.",
    "damage_profile": "Incoming damage for EHP: \"uniform\", a Pyfa built-in "
                      "profile name, or {em, thermal, kinetic, explosive} weights.",
    "target": "Target for applied damage: a Pyfa built-in target profile name, or "
              "{resists: {em, thermal, kinetic, explosive} as 0..1, signature, speed, radius}.",
    "module_states": "[{module, state: offline|online|active|overheated, count?}] "
                     "for modules on the fit; count defaults to all of that name.",
    "spool": "Triglavian/mutadaptive spool: \"min\", \"max\" or 0..1. "
             "Default: Pyfa's default (full).",
    "drug_side_effects": "[{drug, effect}] side effects to switch on; drug must be "
                         "in the fit's EFT, effect is a case-insensitive part of "
                         "the side effect's name.",
    "command": "[{fit}] fits whose command bursts apply to this fit (EFT or "
               "stored fit name/id).",
    "projected": "[{item, count?, state?}] projected modules or drones, or "
                 "[{fit, count?}] projected fits.",
}
_EXAMPLES = [
    {"module_states": [{"module": "Medium Armor Repairer II", "state": "overheated"}],
     "projected": [{"item": "Stasis Webifier II", "count": 2}]},
    {"damage_profile": {"em": 0, "thermal": 0, "kinetic": 1, "explosive": 1},
     "command": [{"fit": "[Damnation, boosts]\n\n\nArmor Command Burst II, "
                         "Armor Energizing Charge\n"}]},
    {"spool": "min", "target": {"resists": {"em": 0.5, "thermal": 0.5,
                                            "kinetic": 0.5, "explosive": 0.5},
                                "signature": 125, "speed": 300}},
]


class ConditionsError(ValueError):
    """Conditions that are malformed or do not match the fit."""


@dataclass(frozen=True)
class Conditions:
    character: str = "All 5"
    damage_profile: str | dict = "uniform"
    target: str | dict | None = None
    module_states: tuple = ()
    spool: float | None = None  # None: Pyfa's default
    drug_side_effects: tuple = ()
    command: tuple = ()
    projected: tuple = ()
    explicit: frozenset = field(default_factory=frozenset)


def _default_spool() -> float:
    import eos.config
    return float(eos.config.settings["globalDefaultSpoolupPercentage"])


def _weights(value, name: str, lo: float, hi: float) -> dict:
    if not isinstance(value, dict) or set(value) != set(_DAMAGE_KEYS):
        raise ConditionsError(f"{name} needs exactly the keys {', '.join(_DAMAGE_KEYS)}")
    for key, v in value.items():
        if not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ConditionsError(f"{name}.{key} must be a number in [{lo}, {hi}]")
    return dict(value)


def _list_of_dicts(raw, key: str) -> tuple:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ConditionsError(f"{key} must be a list of objects")
    return tuple(value)


def _count(entry: dict, key: str) -> int:
    count = entry.get("count", 1)
    if not isinstance(count, int) or count < 1:
        raise ConditionsError(f"{key}: count must be a positive integer")
    return count


def parse(raw: dict | None) -> Conditions:
    raw = dict(raw or {})
    unknown = set(raw) - set(_FIELDS)
    if unknown:
        name = sorted(unknown)[0]
        raise ConditionsError(
            f"unknown condition '{name}'; known: {', '.join(_FIELDS)}")

    if raw.get("character", "All 5") != "All 5":
        raise ConditionsError("character: only \"All 5\" is supported in this version")

    spool = raw.get("spool")
    if spool == "min":
        spool = 0.0
    elif spool == "max":
        spool = 1.0
    elif spool is not None and (not isinstance(spool, (int, float)) or not 0 <= spool <= 1):
        raise ConditionsError("spool must be \"min\", \"max\" or a number in [0, 1]")

    damage = raw.get("damage_profile", "uniform")
    if isinstance(damage, dict):
        damage = _weights(damage, "damage_profile", 0, float("inf"))
        if sum(damage.values()) <= 0:
            raise ConditionsError("damage_profile weights must not all be zero")
    elif not isinstance(damage, str):
        raise ConditionsError("damage_profile must be a name or an object")

    target = raw.get("target")
    if isinstance(target, dict):
        extra = set(target) - {"resists", "signature", "speed", "radius"}
        if extra:
            raise ConditionsError(f"target: unknown key '{sorted(extra)[0]}'")
        if "resists" in target:
            _weights(target["resists"], "target.resists", 0, 1)
    elif target is not None and not isinstance(target, str):
        raise ConditionsError("target must be a name or an object")

    states = _list_of_dicts(raw, "module_states")
    for entry in states:
        if entry.get("state") not in _STATES:
            raise ConditionsError(f"module_states: state must be one of {', '.join(_STATES)}")
        if not isinstance(entry.get("module"), str):
            raise ConditionsError("module_states: each entry needs a module name")
        if "count" in entry:
            _count(entry, "module_states")

    drugs = _list_of_dicts(raw, "drug_side_effects")
    for entry in drugs:
        if not isinstance(entry.get("drug"), str) or not isinstance(entry.get("effect"), str):
            raise ConditionsError("drug_side_effects: each entry needs drug and effect")

    command = _list_of_dicts(raw, "command")
    for entry in command:
        if not isinstance(entry.get("fit"), str):
            raise ConditionsError("command: each entry needs a fit")

    projected = _list_of_dicts(raw, "projected")
    for entry in projected:
        if ("item" in entry) == ("fit" in entry):
            raise ConditionsError("projected: each entry needs exactly one of item or fit")
        _count(entry, "projected")
        if entry.get("state", "active") not in _STATES:
            raise ConditionsError(f"projected: state must be one of {', '.join(_STATES)}")

    return Conditions(
        damage_profile=damage, target=target, module_states=states,
        spool=None if spool is None else float(spool),
        drug_side_effects=drugs, command=command, projected=projected,
        explicit=frozenset(raw))


# --- resolution against eos -------------------------------------------------

def _state(name: str):
    from eos.const import FittingModuleState
    return FittingModuleState[name.upper()]


def _item(name: str):
    from service.market import Market
    item = Market.getInstance().getItem(name)
    if item is None or not item.published:
        close = suggest(name)
        hint = f" (did you mean: {', '.join(close)}?)" if close else ""
        raise ConditionsError(f"unknown item '{name}'{hint}")
    return item


def _builtin_damage_profiles() -> dict:
    from eos.saveddata.damagePattern import DamagePattern
    return {p.fullName: p for p in DamagePattern.getBuiltinList()}


def _builtin_target_profiles() -> dict:
    from eos.saveddata.targetProfile import TargetProfile
    return {p.fullName: p for p in TargetProfile.getBuiltinList()}


def _by_name(profiles: dict, name: str, kind: str):
    for full, profile in profiles.items():
        if full.casefold() == name.casefold():
            return profile
    import difflib
    close = difflib.get_close_matches(name, list(profiles), n=3, cutoff=0.5)
    hint = f" (did you mean: {', '.join(close)}?)" if close else ""
    raise ConditionsError(f"unknown {kind} '{name}'{hint}; see conditions_format()")


def damage_pattern(cond: Conditions):
    from eos.saveddata.damagePattern import DamagePattern
    value = cond.damage_profile
    if value == "uniform":
        return DamagePattern.getDefaultBuiltin()
    if isinstance(value, str):
        return _by_name(_builtin_damage_profiles(), value, "damage profile")
    pattern = DamagePattern(value["em"], value["thermal"], value["kinetic"], value["explosive"])
    pattern.builtin = True  # never persisted: builtins are not written to saveddata
    return pattern


def target_profile(cond: Conditions):
    from eos.saveddata.targetProfile import TargetProfile
    value = cond.target
    if value is None:
        return None
    if isinstance(value, str):
        return _by_name(_builtin_target_profiles(), value, "target profile")
    resists = value.get("resists", dict.fromkeys(_DAMAGE_KEYS, 0))
    profile = TargetProfile(
        resists["em"], resists["thermal"], resists["kinetic"], resists["explosive"],
        maxVelocity=value.get("speed"), signatureRadius=value.get("signature"),
        radius=value.get("radius"))
    profile.builtin = True
    return profile


def _apply_module_states(fit, states) -> list[str]:
    echo = []
    for entry in states:
        name, state = entry["module"], _state(entry["state"])
        mods = [m for m in fit.modules
                if not m.isEmpty and m.item.name.casefold() == name.casefold()]
        if not mods:
            fitted = sorted({m.item.name for m in fit.modules if not m.isEmpty})
            raise ConditionsError(
                f"module_states: '{name}' is not fitted; fitted: {', '.join(fitted)}")
        count = entry.get("count", len(mods))
        if count > len(mods):
            raise ConditionsError(
                f"module_states: count {count} for '{name}' but only {len(mods)} fitted")
        for mod in mods[:count]:
            if not mod.isValidState(state):
                raise ConditionsError(
                    f"module_states: {mod.item.name} cannot be {entry['state']}")
            mod.state = state
        echo.append(f"{mods[0].item.name} x{count}: {entry['state']}")
    return echo


def _apply_drugs(fit, drugs) -> list[str]:
    echo = []
    for entry in drugs:
        booster = next((b for b in fit.boosters
                        if b.item.name.casefold() == entry["drug"].casefold()), None)
        if booster is None:
            raise ConditionsError(
                f"drug_side_effects: '{entry['drug']}' is not in the fit; add it to the EFT")
        needle = entry["effect"].casefold()
        matches = [se for se in booster.sideEffects if needle in se.name.casefold()]
        names = [se.name for se in booster.sideEffects]
        if len(matches) != 1:
            what = "no" if not matches else "several"
            raise ConditionsError(
                f"drug_side_effects: {what} side effects of {booster.item.name} match "
                f"'{entry['effect']}'; available: {'; '.join(names)}")
        matches[0].active = True
        echo.append(f"{booster.item.name}: {matches[0].name}")
    return echo


def _apply_projected(fit, projected, add_fit) -> list[str]:
    from gui.fitCommands.calc.drone.projectedAdd import CalcAddProjectedDroneCommand
    from gui.fitCommands.calc.module.projectedAdd import CalcAddProjectedModuleCommand
    from gui.fitCommands.calc.projectedFit.add import CalcAddProjectedFitCommand
    from gui.fitCommands.helpers import DroneInfo, ModuleInfo

    echo = []
    for entry in projected:
        count = entry.get("count", 1)
        state = _state(entry.get("state", "active"))
        if "fit" in entry:
            other = add_fit(entry["fit"])
            CalcAddProjectedFitCommand(fit.ID, other.ID, count, state).Do()
            echo.append(f"fit '{other.name}' ({other.ship.item.name}) x{count}")
            continue
        item = _item(entry["item"])
        if item.isDrone:
            CalcAddProjectedDroneCommand(
                fit.ID, DroneInfo(amount=count, amountActive=count, itemID=item.ID)).Do()
        elif item.category.name == "Module":
            for _ in range(count):
                ok = CalcAddProjectedModuleCommand(
                    fit.ID, ModuleInfo(itemID=item.ID, state=state)).Do()
                if not ok:
                    raise ConditionsError(f"projected: Pyfa refused to project {item.name}")
        else:
            raise ConditionsError(
                f"projected: {item.name} is a {item.category.name}; project modules, "
                "drones, or a whole fit")
        echo.append(f"{item.name} x{count} ({entry.get('state', 'active')})")
    return echo


def _apply_command(fit, command, add_fit) -> list[str]:
    from eos.const import FittingModuleState
    from gui.fitCommands.calc.commandFit.add import CalcAddCommandFitCommand

    echo = []
    for entry in command:
        other = add_fit(entry["fit"])
        CalcAddCommandFitCommand(fit.ID, other.ID, FittingModuleState.ACTIVE).Do()
        echo.append(f"fit '{other.name}' ({other.ship.item.name})")
    return echo


def _mark(value: str, key: str, cond: Conditions) -> str:
    return value if key in cond.explicit else f"{value} (default)"


def apply(fit, cond: Conditions, add_fit: Callable) -> dict:
    from service.fit import Fit as FitService

    pattern = damage_pattern(cond)
    fit.damagePattern = pattern
    profile = target_profile(cond)
    fit.targetProfile = profile

    states = _apply_module_states(fit, cond.module_states)
    drugs = _apply_drugs(fit, cond.drug_side_effects)
    command = _apply_command(fit, cond.command, add_fit)
    projected = _apply_projected(fit, cond.projected, add_fit)
    FitService.getInstance().recalc(fit)

    spool = cond.spool if cond.spool is not None else _default_spool()
    damage_label = (cond.damage_profile if isinstance(cond.damage_profile, str)
                    else "custom " + ", ".join(f"{k} {v:g}" for k, v in cond.damage_profile.items()))
    target_label = ("none" if cond.target is None else
                    cond.target if isinstance(cond.target, str) else "custom target")
    return {
        "character": _mark("All 5", "character", cond),
        "damage_profile": _mark(damage_label, "damage_profile", cond),
        "target": _mark(target_label, "target", cond),
        "module_states": states or _mark("as in the EFT", "module_states", cond),
        "spool": _mark(f"{spool:g}", "spool", cond),
        "drug_side_effects": drugs or _mark("none", "drug_side_effects", cond),
        "command": command or _mark("none", "command", cond),
        "projected": projected or _mark("none", "projected", cond),
    }


def spool_of(cond: Conditions) -> float:
    return cond.spool if cond.spool is not None else _default_spool()


def describe() -> dict:
    return {
        "fields": _FIELDS,
        "defaults": {"character": "All 5", "damage_profile": "uniform",
                     "target": None, "spool": "Pyfa default (full)",
                     "module_states": "as in the EFT (modules active, /OFFLINE honoured)"},
        "damage_profiles": ["uniform", *_builtin_damage_profiles()],
        "target_profiles": list(_builtin_target_profiles()),
        "examples": _EXAMPLES,
        "in_eft_instead": "implants, drugs (boosters), charges, drones/fighters with "
                          "counts, /OFFLINE modules and mutated modules go in the EFT text",
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_conditions.py -v`
Expected: all passed. Points to check if something fails, in this order:
  - `DamagePattern.getDefaultBuiltin()` / `fullName` / `TargetProfile(...)` constructor names: confirm in `vendor/Pyfa/eos/saveddata/damagePattern.py` and `targetProfile.py`.
  - If assigning a non-persisted custom `DamagePattern` to `fit.damagePattern` makes SQLAlchemy insert it on the next commit (check `SELECT count(*) FROM damagePatterns` in the test DB after the test), do not set `builtin` — instead `eos.db.saveddata_session.expunge(pattern)` right after `recalc`, and assert the count in `test_damage_profile_changes_ehp`.
  - `Market.getItem` raising instead of returning None for unknown names: wrap the call in `try/except Exception` and treat as not found.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/conditions.py tests/test_conditions.py
git commit -m "Apply damage/target profiles, module states, spool, drugs, boosts and projections via Pyfa's calc commands"
```

---

### Task 6: Stored fits, fit references, evaluate and compare

**Files:**
- Create: `pyfa_mcp/store.py`, `pyfa_mcp/evaluate.py`, `tests/test_store.py`, `tests/test_evaluate.py`

**Interfaces:**
- Consumes: `eft.import_fit/export_fit/looks_like_eft/EftError`, `conditions.parse/apply/spool_of/ConditionsError`, `stats.fit_stats/flatten/DEFAULT_COMPARE`, `eosboot.TEMP_NOTE`.
- Produces:
  - `store.StoreError(LookupError)`
  - `store.resolve_eft(ref: str) -> str` — EFT text as-is, or a stored fit's exported EFT by id or name.
  - `store.save_fit(ref: str, name: str) -> dict` `{id, name, ship}`; duplicate name → `StoreError`.
  - `store.list_fits(ship: str | None = None) -> list[dict]` `{id, name, ship}`, temp fits excluded, sorted by name.
  - `store.get_fit(ref: str) -> dict` `{id, name, ship, eft}`.
  - `store.delete_fit(ref: str) -> dict` `{deleted: {id, name, ship}}`.
  - `evaluate.Scratch` — context manager; `add_fit(ref) -> Fit` imports a temp fit and tracks it; on exit deletes every tracked fit, even on error.
  - `evaluate.evaluate(ref: str, raw_conditions: dict | None) -> dict` `{fit, ship, applied, warnings, **stats}`.
  - `evaluate.compare(refs: list[str], raw_conditions: dict | None, keys: list[str] | None) -> dict` `{applied, columns, rows}`; a broken fit becomes `{"fit": label, "error": message}`.

- [ ] **Step 1: Write the failing tests `tests/test_store.py`**

```python
import pytest

from pyfa_mcp import store


@pytest.fixture
def saved(booted, zealot_eft):
    entry = store.save_fit(zealot_eft, "Store Zealot")
    yield entry
    try:
        store.delete_fit(str(entry["id"]))
    except store.StoreError:
        pass


def test_save_list_get(saved):
    assert saved["ship"] == "Zealot"
    names = [f["name"] for f in store.list_fits()]
    assert "Store Zealot" in names
    got = store.get_fit("store zealot")  # name lookup is case-insensitive
    assert got["id"] == saved["id"]
    assert got["eft"].startswith("[Zealot, Store Zealot]")
    assert store.get_fit(str(saved["id"]))["name"] == "Store Zealot"


def test_list_filters_by_ship(saved):
    assert store.list_fits(ship="Zealot")
    assert not [f for f in store.list_fits(ship="Rifter") if f["name"] == "Store Zealot"]


def test_duplicate_name_refused(saved, zealot_eft):
    with pytest.raises(store.StoreError, match="already"):
        store.save_fit(zealot_eft, "Store Zealot")


def test_resolve(saved, zealot_eft):
    assert store.resolve_eft(zealot_eft) == zealot_eft
    assert store.resolve_eft("Store Zealot").startswith("[Zealot, Store Zealot]")


def test_unknown_fit_suggests(saved):
    with pytest.raises(store.StoreError, match="Store Zealot"):
        store.resolve_eft("Store Zealt")


def test_delete(booted, zealot_eft):
    entry = store.save_fit(zealot_eft, "Doomed")
    assert store.delete_fit("Doomed")["deleted"]["id"] == entry["id"]
    with pytest.raises(store.StoreError):
        store.get_fit("Doomed")


def test_temp_fits_are_invisible(booted, zealot_eft, no_fits_left):
    from pyfa_mcp import eft
    from service.fit import Fit
    fit = eft.import_fit(zealot_eft, name="Hidden temp", temp=True)
    try:
        assert "Hidden temp" not in [f["name"] for f in store.list_fits()]
        with pytest.raises(store.StoreError):
            store.get_fit("Hidden temp")
    finally:
        Fit.deleteFit(fit.ID)
```

- [ ] **Step 2: Write the failing tests `tests/test_evaluate.py`**

```python
import pytest

from pyfa_mcp import conditions, eft, evaluate


def test_evaluate_shape(booted, zealot_eft, no_fits_left):
    result = evaluate.evaluate(zealot_eft, None)
    assert result["fit"] == "Test Zealot"
    assert result["ship"] == "Zealot"
    assert result["applied"]["character"] == "All 5 (default)"
    assert result["warnings"] == []
    assert result["validity"]["valid"] is True
    assert result["offense"]["dps"]["total"] > 0


def test_evaluate_projected_fit_cleans_up(booted, zealot_eft, no_fits_left):
    result = evaluate.evaluate(zealot_eft, {"projected": [
        {"fit": "[Scimitar, logi]\nLarge Remote Shield Booster II\n"}]})
    assert "logi" in result["applied"]["projected"][0]


def test_failure_midway_leaves_nothing(booted, zealot_eft, no_fits_left):
    with pytest.raises(eft.EftError):
        evaluate.evaluate(zealot_eft, {"projected": [{"fit": "[Scimitar, x]\nBad Module\n"}]})


def test_conditions_error_leaves_nothing(booted, zealot_eft, no_fits_left):
    with pytest.raises(conditions.ConditionsError):
        evaluate.evaluate(zealot_eft, {"module_states": [
            {"module": "Damage Control II", "state": "overheated"}]})


def test_invalid_fit_still_evaluated(booted, no_fits_left):
    result = evaluate.evaluate("[Zealot, over]\n" + "Heat Sink II\n" * 9, None)
    assert result["validity"]["valid"] is False
    assert result["warnings"] == ["fit is not valid: " + "; ".join(result["validity"]["problems"])]


def test_compare_keeps_going_past_a_broken_fit(booted, zealot_eft, no_fits_left):
    table = evaluate.compare(
        [zealot_eft, "[Zealot, broken]\nHeat Sinkk II\n", "[Rifter, small]\n"], None, None)
    assert table["columns"][0] == "fit"
    labels = [row["fit"] for row in table["rows"]]
    assert labels[0] == "Test Zealot" and labels[2] == "small"
    assert "Heat Sinkk II" in table["rows"][1]["error"]
    assert table["rows"][0]["tank.ehp.total"] > table["rows"][2]["tank.ehp.total"]


def test_compare_selected_keys(booted, zealot_eft, no_fits_left):
    table = evaluate.compare([zealot_eft], None, ["offense.dps.total"])
    assert table["columns"] == ["fit", "offense.dps.total"]


def test_compare_unknown_key(booted, zealot_eft, no_fits_left):
    with pytest.raises(ValueError, match="unknown stat"):
        evaluate.compare([zealot_eft], None, ["offense.nope"])


def test_compare_bad_conditions_fail_whole_call(booted, zealot_eft, no_fits_left):
    with pytest.raises(conditions.ConditionsError):
        evaluate.compare([zealot_eft], {"bogus": 1}, None)
```

- [ ] **Step 3: Run to see them fail**

Run: `uv run pytest tests/test_store.py tests/test_evaluate.py -v`
Expected: FAIL — import errors.

- [ ] **Step 4: Write `pyfa_mcp/store.py`**

```python
"""Fits the user chose to keep, in the server's saveddata.db.

A fit reference is EFT text, a stored fit's numeric id, or its name
(case-insensitive). Temporary evaluation fits are never visible here.
"""
from __future__ import annotations

import difflib

from pyfa_mcp import eft
from pyfa_mcp.eosboot import TEMP_NOTE


class StoreError(LookupError):
    """No such stored fit, or a name already taken."""

    def __str__(self):  # LookupError would quote the message
        return str(self.args[0]) if self.args else ""


def _stored():
    from service.fit import Fit
    return [f for f in Fit.getAllFits() if f.notes != TEMP_NOTE]


def _entry(fit) -> dict:
    return {"id": fit.ID, "name": fit.name, "ship": fit.ship.item.name}


def _find(ref: str):
    fits = _stored()
    ref = ref.strip()
    if ref.isdigit():
        for fit in fits:
            if fit.ID == int(ref):
                return fit
        raise StoreError(f"no stored fit with id {ref}")
    matches = [f for f in fits if f.name.casefold() == ref.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:  # possible only for fits stored before names were unique
        ids = ", ".join(str(f.ID) for f in matches)
        raise StoreError(f"several stored fits are named '{ref}'; use an id: {ids}")
    close = difflib.get_close_matches(ref, [f.name for f in fits], n=3, cutoff=0.5)
    hint = f" (did you mean: {', '.join(close)}?)" if close else ""
    raise StoreError(f"no stored fit named '{ref}'{hint}; pass EFT text or see list_fits()")


def resolve_eft(ref: str) -> str:
    if eft.looks_like_eft(ref):
        return ref
    from service.fit import Fit
    return eft.export_fit(Fit.getInstance().getFit(_find(ref).ID))


def save_fit(ref: str, name: str) -> dict:
    name = name.strip()
    if not name:
        raise StoreError("a stored fit needs a name")
    if any(f.name.casefold() == name.casefold() for f in _stored()):
        raise StoreError(f"a stored fit is already named '{name}'; delete it or pick another name")
    return _entry(eft.import_fit(resolve_eft(ref), name=name))


def list_fits(ship: str | None = None) -> list[dict]:
    fits = _stored()
    if ship:
        fits = [f for f in fits if f.ship.item.name.casefold() == ship.casefold()]
    return sorted((_entry(f) for f in fits), key=lambda e: e["name"].casefold())


def get_fit(ref: str) -> dict:
    from service.fit import Fit
    fit = _find(ref)
    return {**_entry(fit), "eft": eft.export_fit(Fit.getInstance().getFit(fit.ID))}


def delete_fit(ref: str) -> dict:
    from service.fit import Fit
    fit = _find(ref)
    entry = _entry(fit)
    Fit.deleteFit(fit.ID)
    return {"deleted": entry}
```

- [ ] **Step 5: Write `pyfa_mcp/evaluate.py`**

```python
"""Evaluate fits under conditions, on temporary copies that never outlive a call."""
from __future__ import annotations

from pyfa_mcp import conditions, eft, stats, store


class Scratch:
    """Temporary fits for one evaluation; all deleted on exit, error or not."""

    def __init__(self):
        self._ids: list[int] = []

    def add_fit(self, ref: str):
        fit = eft.import_fit(store.resolve_eft(ref), temp=True)
        self._ids.append(fit.ID)
        return fit

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        from service.fit import Fit
        # Projected and command fits first: they hang off the main fit.
        for fit_id in reversed(self._ids):
            Fit.deleteFit(fit_id)
        self._ids.clear()
        return False


def _warnings(result: dict) -> list[str]:
    problems = result["validity"]["problems"]
    return ["fit is not valid: " + "; ".join(problems)] if problems else []


def _evaluate_parsed(ref: str, cond) -> dict:
    with Scratch() as scratch:
        fit = scratch.add_fit(ref)
        applied = conditions.apply(fit, cond, scratch.add_fit)
        result = stats.fit_stats(fit, conditions.spool_of(cond))
        name, ship = fit.name, fit.ship.item.name
    return {"fit": name, "ship": ship, "applied": applied,
            "warnings": _warnings(result), **result}


def evaluate(ref: str, raw_conditions: dict | None) -> dict:
    return _evaluate_parsed(ref, conditions.parse(raw_conditions))


def _label(ref: str) -> str:
    if eft.looks_like_eft(ref):
        header = ref.strip().splitlines()[0].strip("[] ")
        return header.split(",", 1)[-1].strip() or header
    return ref.strip()


def compare(refs: list[str], raw_conditions: dict | None,
            keys: list[str] | None) -> dict:
    cond = conditions.parse(raw_conditions)  # bad conditions fail the whole call
    keys = list(keys) if keys else list(stats.DEFAULT_COMPARE)
    applied, rows = None, []
    for ref in refs:
        try:
            result = _evaluate_parsed(ref, cond)
        except (eft.EftError, store.StoreError, conditions.ConditionsError) as exc:
            rows.append({"fit": _label(ref), "error": str(exc)})
            continue
        applied = applied or result["applied"]
        flat = stats.flatten({k: v for k, v in result.items()
                              if k not in ("fit", "ship", "applied", "warnings")})
        unknown = [k for k in keys if k not in flat]
        if unknown:
            raise ValueError(f"unknown stat '{unknown[0]}'; stat keys look like "
                             f"{', '.join(stats.DEFAULT_COMPARE[:3])}")
        rows.append({"fit": result["fit"], "ship": result["ship"],
                     **{k: flat[k] for k in keys}, "warnings": result["warnings"]})
    return {"applied": applied, "columns": ["fit", *keys], "rows": rows}
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_store.py tests/test_evaluate.py -v`
Expected: all passed. If `test_failure_midway_leaves_nothing` finds the main fit left behind, `Scratch.__exit__` is not being reached — check that `add_fit` appends the id *before* anything can raise after the import.

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/store.py pyfa_mcp/evaluate.py tests/test_store.py tests/test_evaluate.py
git commit -m "Store fits by name, and evaluate or compare fits on temporary copies"
```

---

### Task 7: Catalog — items, ships, item info

**Files:**
- Create: `pyfa_mcp/catalog.py`, `tests/test_catalog.py`

**Interfaces:**
- Consumes: `eft.suggest`.
- Produces:
  - `catalog.CatalogError(LookupError)`
  - `catalog.search_items(query: str, category: str | None = None, meta: str | None = None, limit: int = 25) -> list[dict]` `{name, type_id, group, category, meta, slot, cpu, powergrid}`.
  - `catalog.list_ships(group: str | None = None, race: str | None = None) -> list[dict]` `{name, type_id, group, race, slots: {high, mid, low, rig}, hardpoints: {turret, launcher}, drones: {bandwidth, bay}}`.
  - `catalog.item_info(name: str) -> dict` `{name, type_id, group, category, meta, traits, attributes}`.

- [ ] **Step 1: Write the failing tests `tests/test_catalog.py`**

```python
import pytest

from pyfa_mcp import catalog


def test_search_items(booted):
    rows = catalog.search_items("sensor booster")
    names = [r["name"] for r in rows]
    assert "Sensor Booster II" in names
    sebo = next(r for r in rows if r["name"] == "Sensor Booster II")
    assert sebo["slot"] == "mid"
    assert sebo["meta"] == "Tech II"
    assert sebo["cpu"] > 0


def test_search_filters(booted):
    t1 = catalog.search_items("sensor booster", meta="Tech I")
    assert t1 and all(r["meta"] == "Tech I" for r in t1)
    mods = catalog.search_items("hobgoblin", category="Drone")
    assert mods and all(r["category"] == "Drone" for r in mods)


def test_search_limit(booted):
    assert len(catalog.search_items("armor", limit=5)) == 5


def test_list_ships_by_group(booted):
    battleships = catalog.list_ships(group="Battleship")
    names = {s["name"] for s in battleships}
    assert {"Apocalypse", "Megathron", "Tempest", "Rokh"} <= names
    mega = next(s for s in battleships if s["name"] == "Megathron")
    assert mega["race"] == "gallente"
    assert mega["hardpoints"]["turret"] > 0
    assert mega["slots"]["high"] > 0


def test_list_ships_unknown_group_suggests(booted):
    with pytest.raises(catalog.CatalogError, match="Battleship"):
        catalog.list_ships(group="Battleshp")


def test_item_info(booted):
    info = catalog.item_info("Zealot")
    assert info["group"] == "Heavy Assault Cruiser"
    assert info["traits"]
    assert info["attributes"]["hiSlots"] == 5


def test_item_info_unknown(booted):
    with pytest.raises(catalog.CatalogError, match="Zealot"):
        catalog.item_info("Zealout")
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Write `pyfa_mcp/catalog.py`**

```python
"""Questions about the game data itself: what items and ships exist."""
from __future__ import annotations

import difflib
import re

from pyfa_mcp.eft import suggest

_SLOT_EFFECTS = {"hiPower": "high", "medPower": "mid", "loPower": "low",
                 "rigSlot": "rig", "subSystem": "subsystem"}


class CatalogError(LookupError):
    def __str__(self):
        return str(self.args[0]) if self.args else ""


def _meta(item) -> str:
    return item.metaGroup.name if item.metaGroup is not None else "Tech I"


def _slot(item) -> str | None:
    for effect, slot in _SLOT_EFFECTS.items():
        if effect in item.effects:
            return slot
    return None


def _row(item) -> dict:
    return {
        "name": item.name, "type_id": item.ID, "group": item.group.name,
        "category": item.category.name, "meta": _meta(item), "slot": _slot(item),
        "cpu": item.getAttribute("cpu"), "powergrid": item.getAttribute("power"),
    }


def search_items(query: str, category: str | None = None, meta: str | None = None,
                 limit: int = 25) -> list[dict]:
    import eos.db

    rows = []
    for item in eos.db.searchItems(query):
        if not item.published:
            continue
        row = _row(item)
        if category and row["category"].casefold() != category.casefold():
            continue
        if meta and row["meta"].casefold() != meta.casefold():
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["name"])
    return rows[:limit]


def _ship_items():
    import eos.db
    from eos.gamedata import Category, Group, Item

    return (eos.db.gamedata_session.query(Item).join(Group).join(Category)
            .filter(Category.name == "Ship", Item.published == True)  # noqa: E712
            .all())


def list_ships(group: str | None = None, race: str | None = None) -> list[dict]:
    ships = _ship_items()
    if group:
        groups = sorted({s.group.name for s in ships})
        if group.casefold() not in {g.casefold() for g in groups}:
            close = difflib.get_close_matches(group, groups, n=3, cutoff=0.5)
            raise CatalogError(f"unknown ship group '{group}'"
                               + (f" (did you mean: {', '.join(close)}?)" if close else "")
                               + f"; groups: {', '.join(groups)}")
        ships = [s for s in ships if s.group.name.casefold() == group.casefold()]
    if race:
        ships = [s for s in ships if (s.race or "").casefold() == race.casefold()]

    def attr(item, name):
        return int(item.getAttribute(name) or 0)

    return sorted(({
        "name": s.name, "type_id": s.ID, "group": s.group.name, "race": s.race,
        "slots": {"high": attr(s, "hiSlots"), "mid": attr(s, "medSlots"),
                  "low": attr(s, "lowSlots"), "rig": attr(s, "rigSlots")},
        "hardpoints": {"turret": attr(s, "turretSlotsLeft"),
                       "launcher": attr(s, "launcherSlotsLeft")},
        "drones": {"bandwidth": attr(s, "droneBandwidth"), "bay": attr(s, "droneCapacity")},
    } for s in ships), key=lambda r: r["name"])


def _plain(html: str | None) -> str:
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    return re.sub(r"<[^>]+>", "", text).strip()


def item_info(name: str) -> dict:
    from service.market import Market

    try:
        item = Market.getInstance().getItem(name)
    except Exception:
        item = None
    if item is None or not item.published:
        close = suggest(name)
        raise CatalogError(f"unknown item '{name}'"
                           + (f" (did you mean: {', '.join(close)}?)" if close else ""))
    return {
        **{k: v for k, v in _row(item).items() if k not in ("cpu", "powergrid", "slot")},
        "traits": _plain(item.traits.display) if item.traits is not None else "",
        "attributes": {attr_name: attr.value for attr_name, attr in item.attributes.items()},
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: all passed. If `eos.db.searchItems` caps results internally below `limit`, accept it (the cap is Pyfa's); if `item.race` is None for a pirate hull, that is fine — the filter just won't match it.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/catalog.py tests/test_catalog.py
git commit -m "Search items, list ships with layouts, and describe any item"
```

---

### Task 8: Graphs as data points

**Files:**
- Create: `pyfa_mcp/graphs.py`, `tests/test_graphs.py`

**Interfaces:**
- Consumes: `evaluate.Scratch`, `conditions.parse/apply/target_profile/ConditionsError`.
- Produces:
  - `graphs.GRAPHS: dict[str, str]` — our name → Pyfa internal name.
  - `graphs.GraphError(ValueError)`
  - `graphs.describe() -> dict` — per graph: `x` options `[{handle, unit, label}]`, `y` options, `inputs` `[{handle, unit, default}]`, `has_target`.
  - `graphs.fit_graph(ref: str, graph: str, x: str, y: str, x_range: list[float], inputs: dict | None, raw_conditions: dict | None, max_points: int = 50) -> dict` `{graph, x: {handle, unit}, y: {handle, unit}, points: [[x, y], ...], applied}`. `x` and `y` are `"handle"` or `"handle:unit"`.

- [ ] **Step 1: Write the failing tests `tests/test_graphs.py`**

```python
import pytest

from pyfa_mcp import graphs


def test_describe_has_lock_time(booted):
    d = graphs.describe()
    assert set(graphs.GRAPHS) <= set(d)
    lock = d["lock_time"]
    assert lock["x"][0]["handle"] == "tgtSigRad"
    assert lock["y"][0]["handle"] == "time"


def test_lock_time_falls_with_signature(booted, zealot_eft, no_fits_left):
    out = graphs.fit_graph(zealot_eft, "lock_time", "tgtSigRad", "time", [25, 500], None, None)
    ys = [p[1] for p in out["points"]]
    assert len(out["points"]) <= 50
    assert ys[0] > ys[-1] > 0
    assert out["x"] == {"handle": "tgtSigRad", "unit": "m"}


def test_damage_vs_distance_with_target(booted, zealot_eft, no_fits_left):
    out = graphs.fit_graph(zealot_eft, "damage", "distance:km", "dps", [0, 60], None,
                           {"target": {"signature": 125, "speed": 0}})
    assert out["points"][0][1] > out["points"][-1][1]


def test_unknown_graph(booted, zealot_eft):
    with pytest.raises(graphs.GraphError, match="lock_time"):
        graphs.fit_graph(zealot_eft, "lock", "tgtSigRad", "time", [25, 500], None, None)


def test_unknown_axis(booted, zealot_eft, no_fits_left):
    with pytest.raises(graphs.GraphError, match="tgtSigRad"):
        graphs.fit_graph(zealot_eft, "lock_time", "speed", "time", [25, 500], None, None)


def test_bad_range(booted, zealot_eft):
    with pytest.raises(graphs.GraphError, match="x_range"):
        graphs.fit_graph(zealot_eft, "lock_time", "tgtSigRad", "time", [500], None, None)
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_graphs.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Write `pyfa_mcp/graphs.py`**

```python
"""Pyfa's graphs, computed headless and returned as points."""
from __future__ import annotations

from collections import namedtuple

from pyfa_mcp import conditions
from pyfa_mcp.evaluate import Scratch

# The GUI's InputData (graphs/gui/ctrlPanel.py), which we cannot import:
# that module is the wx control panel itself.
InputData = namedtuple("InputData", ("handle", "unit", "value"))

GRAPHS = {
    "damage": "dmgStatsGraph",
    "lock_time": "lockTimeGraph",
    "mobility": "mobilityGraph",
    "warp_time": "warpTimeGraph",
    "capacitor": "capacitorGraph",
    "shield_regen": "shieldRegenGraph",
    "ewar": "ewarStatsGraph",
    "remote_reps": "remoteRepsGraph",
}


class GraphError(ValueError):
    pass


def _view(name: str):
    import graphs.data  # noqa: F401 -- registers every graph
    from graphs.data.base import FitGraph

    if name not in GRAPHS:
        raise GraphError(f"unknown graph '{name}'; graphs: {', '.join(GRAPHS)}")
    return FitGraph.viewMap[GRAPHS[name]]()


def describe() -> dict:
    out = {}
    for name in GRAPHS:
        view = _view(name)
        out[name] = {
            "x": [{"handle": d.handle, "unit": d.unit, "label": d.label} for d in view.xDefs],
            "y": [{"handle": d.handle, "unit": d.unit, "label": d.label} for d in view.yDefs],
            "inputs": [{"handle": i.handle, "unit": i.unit, "default": i.defaultValue}
                       for i in view.inputs],
            "has_target": view.hasTargets,
        }
    return out


def _pick(defs, spec: str, axis: str):
    handle, _, unit = spec.partition(":")
    for d in defs:
        if d.handle == handle and (not unit or d.unit == unit):
            return d
    options = ", ".join(f"{d.handle}:{d.unit}" if d.unit else d.handle for d in defs)
    raise GraphError(f"unknown {axis} '{spec}'; options: {options}")


def _downsample(xs, ys, max_points: int) -> list[list[float]]:
    if len(xs) <= max_points:
        return [[x, y] for x, y in zip(xs, ys)]
    step = (len(xs) - 1) / (max_points - 1)
    picks = [round(i * step) for i in range(max_points)]
    return [[xs[i], ys[i]] for i in picks]


def fit_graph(ref: str, graph: str, x: str, y: str, x_range: list[float],
              inputs: dict | None, raw_conditions: dict | None,
              max_points: int = 50) -> dict:
    from graphs.wrapper import SourceWrapper, TargetWrapper

    view = _view(graph)
    if (not isinstance(x_range, (list, tuple)) or len(x_range) != 2
            or not all(isinstance(v, (int, float)) for v in x_range)):
        raise GraphError("x_range must be [low, high]")
    x_def = _pick(view.xDefs, x, "x")
    y_def = _pick(view.yDefs, y, "y")
    cond = conditions.parse(raw_conditions)

    main_handle, main_unit = x_def.mainInput
    main = InputData(main_handle, main_unit, tuple(x_range))
    given = dict(inputs or {})
    misc = []
    for spec in view.inputs:
        if spec.handle == main_handle:
            continue
        value = given.pop(spec.handle, spec.defaultValue)
        if value is None:  # left empty, as the GUI leaves an unset box out
            continue
        misc.append(InputData(spec.handle, spec.unit, value))
    if given:
        raise GraphError(f"unknown input '{sorted(given)[0]}'; inputs: "
                         + ", ".join(i.handle for i in view.inputs))

    with Scratch() as scratch:
        fit = scratch.add_fit(ref)
        applied = conditions.apply(fit, cond, scratch.add_fit)
        target = None
        if view.hasTargets:
            from eos.saveddata.targetProfile import TargetProfile
            profile = conditions.target_profile(cond) or TargetProfile.getIdeal()
            target = TargetWrapper(profile, None, None)
        xs, ys = view.getPlotPoints(main, misc, x_def, y_def,
                                    SourceWrapper(fit, None), target)
    return {
        "graph": graph,
        "x": {"handle": x_def.handle, "unit": x_def.unit},
        "y": {"handle": y_def.handle, "unit": y_def.unit},
        "points": _downsample(list(xs), list(ys), max_points),
        "applied": applied,
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_graphs.py -v`
Expected: all passed. If `getPlotPoints` caches across calls with the same fit ID (Pyfa keys its cache on `src.item.ID`; temp IDs can be reused after deletion), construct a fresh view per call — `_view` already does — and keep it that way.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/graphs.py tests/test_graphs.py
git commit -m "Compute Pyfa's graphs headless and return them as points"
```

---

### Task 9: The MCP server

**Files:**
- Create: `pyfa_mcp/server.py`, `pyfa_mcp/__main__.py`, `packaging/mcp_smoke.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: every module above.
- Produces: tools `search_items, list_ships, item_info, evaluate_fit, compare_fits, fit_graph, graph_options, conditions_format, status, save_fit, list_fits, get_fit, delete_fit`; `server.main()`; `python -m pyfa_mcp`.

- [ ] **Step 1: Write the failing tests `tests/test_server.py`**

```python
import subprocess
import sys
from pathlib import Path

import pytest

from pyfa_mcp import server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _server_uses_test_dir(booted):
    server._data_dir = booted


def test_tools_return_data(booted, zealot_eft, no_fits_left):
    result = server.evaluate_fit(zealot_eft)
    assert result["fit"] == "Test Zealot"
    assert server.status()["pyfa_version"] == "v2.69.0"
    assert "fields" in server.conditions_format()


def test_errors_become_tool_errors(booted):
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="Heat Sinkk II"):
        server.evaluate_fit("[Zealot, x]\nHeat Sinkk II\n")
    with pytest.raises(ToolError, match="unknown condition"):
        server.evaluate_fit("[Zealot, x]\n", conditions={"bogus": 1})


def test_tool_bodies_keep_stdout_clean(booted, zealot_eft, capsys, no_fits_left):
    server.evaluate_fit(zealot_eft)
    server.search_items("sensor booster")
    assert capsys.readouterr().out == ""


def test_smoke_over_stdio(booted, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "mcp_smoke.py"),
         sys.executable, "-m", "pyfa_mcp", "--data-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Write `pyfa_mcp/server.py`**

```python
"""pyfa-mcp: Pyfa's fitting engine over MCP (stdio).

Every tool body runs on one dedicated thread, one call at a time: eos keeps
one SQLAlchemy session per process and Pyfa's services are singletons, none
of them safe to use from two calls at once. Pyfa is booted on the first
tool call and kept for the life of the process.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from pyfa_mcp import catalog, conditions, eft, eosboot, evaluate, graphs, store

INSTRUCTIONS = """\
pyfa-mcp computes EVE Online fits with Pyfa's own engine.

- Fits are EFT text (as Pyfa or the game exports them) or the name/id of a
  fit saved with save_fit. To change a fit, edit the EFT and evaluate again.
- evaluate_fit / compare_fits / fit_graph take `conditions`. Defaults: All 5
  skills, uniform incoming damage, no target profile, modules as in the EFT
  (active, /OFFLINE honoured), Pyfa's default spool (full), no drug side
  effects, no command bursts, nothing projected. Set the conditions that
  matter for the user's question; call conditions_format() for the schema.
- Every result has `applied` (what the numbers assume) and `warnings`.
  Tell the user about warnings, and mention the assumptions that matter.
- Use compare_fits to evaluate many candidate fits in one call.
- Call status() if numbers look wrong; relay any warning it reports.
"""

app = MCPServer("pyfa", instructions=INSTRUCTIONS)

_eos_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eos")
_data_dir: Path | None = None
_boot_error: str | None = None
_booted = False

_USER_ERRORS = (eft.EftError, conditions.ConditionsError, store.StoreError,
                catalog.CatalogError, graphs.GraphError, ValueError)


def _ensure_booted() -> None:
    global _booted, _boot_error
    if _booted:
        return
    if _boot_error is not None:
        raise ToolError(_boot_error)
    try:
        eosboot.boot(_data_dir)
    except Exception as exc:
        _boot_error = f"pyfa-mcp could not start Pyfa: {exc}"
        raise ToolError(_boot_error) from exc
    _booted = True


def _tool(fn):
    """Boot, run on the eos thread, keep stdout clean, explain failures."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        def body():
            with contextlib.redirect_stdout(sys.stderr):
                _ensure_booted()
                try:
                    return fn(*args, **kwargs)
                except ToolError:
                    raise
                except _USER_ERRORS as exc:
                    raise ToolError(str(exc)) from exc
                except Exception as exc:
                    raise ToolError(f"{type(exc).__name__}: {exc}") from exc
        return _eos_thread.submit(body).result()
    return run


# --- catalog -----------------------------------------------------------------

@app.tool()
@_tool
def search_items(query: str, category: str | None = None, meta: str | None = None,
                 limit: int = 25) -> list:
    """Find items by name. category: e.g. Module, Drone, Charge, Ship, Implant.
    meta: Tech I, Tech II, Faction, Deadspace, Officer, Storyline, ... Returns
    name, group, category, meta, slot (high/mid/low/rig/subsystem), cpu, powergrid."""
    return catalog.search_items(query, category, meta, limit)


@app.tool()
@_tool
def list_ships(group: str | None = None, race: str | None = None) -> list:
    """Ships with slot, hardpoint and drone layouts. group: e.g. Battleship,
    Heavy Assault Cruiser, Carrier. race: amarr, caldari, gallente, minmatar, ..."""
    return catalog.list_ships(group, race)


@app.tool()
@_tool
def item_info(name: str) -> dict:
    """All attributes and the trait/bonus text of one item (ship, module, charge...)."""
    return catalog.item_info(name)


# --- evaluation --------------------------------------------------------------

@app.tool()
@_tool
def evaluate_fit(fit: str, conditions: dict | None = None) -> dict:
    """Full stats of one fit (EFT text or stored fit name/id) under conditions:
    validity (cpu/pg/calibration/slots/hardpoints), tank (hp, ehp, resists,
    repair), offense (dps/volley), capacitor, navigation, targeting, drones,
    plus `applied` and `warnings`. An overfit fit is still evaluated."""
    return evaluate.evaluate(fit, conditions)


@app.tool()
@_tool
def compare_fits(fits: list[str], conditions: dict | None = None,
                 stats: list[str] | None = None) -> dict:
    """Evaluate many fits under the same conditions into one table. `stats` picks
    columns by dotted key from evaluate_fit's output (e.g. "tank.ehp.total",
    "offense.dps.total", "targeting.lock_range_m"); omitted = a standard set.
    A fit that fails gets an `error` in its row; the others still compute."""
    return evaluate.compare(fits, conditions, stats)


@app.tool()
@_tool
def fit_graph(fit: str, graph: str, x: str, y: str, x_range: list[float],
              inputs: dict | None = None, conditions: dict | None = None) -> dict:
    """One of Pyfa's graphs as points. graph: damage, lock_time, mobility,
    warp_time, capacitor, shield_regen, ewar, remote_reps. x/y: an axis handle,
    optionally "handle:unit" (see graph_options()). For graphs with a target,
    conditions.target is the target (default: Pyfa's ideal target)."""
    return graphs.fit_graph(fit, graph, x, y, x_range, inputs, conditions)


@app.tool()
@_tool
def graph_options() -> dict:
    """Axes, extra inputs and defaults for every graph fit_graph can draw."""
    return graphs.describe()


@app.tool()
@_tool
def conditions_format() -> dict:
    """The `conditions` schema with examples, and the names of Pyfa's built-in
    damage and target profiles."""
    return conditions.describe()


@app.tool()
@_tool
def status() -> dict:
    """Versions and health of the engine. Relay any warnings to the user."""
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        meta = dict(connection.exec_driver_sql(
            "SELECT field_name, field_value FROM metadata").fetchall())
    return {"pyfa_version": eosboot.pyfa_version(),
            "game_client_build": meta.get("client_build"),
            "data_dir": str(eosboot.boot(_data_dir)),
            "warnings": []}


# --- storage -----------------------------------------------------------------

@app.tool()
@_tool
def save_fit(fit: str, name: str) -> dict:
    """Keep a fit (EFT or stored name/id) under a unique name in the server's database."""
    return store.save_fit(fit, name)


@app.tool()
@_tool
def list_fits(ship: str | None = None) -> list:
    """Fits stored in the server's database, optionally for one ship."""
    return store.list_fits(ship)


@app.tool()
@_tool
def get_fit(fit: str) -> dict:
    """A stored fit (by name or id) as EFT text."""
    return store.get_fit(fit)


@app.tool()
@_tool
def delete_fit(fit: str) -> dict:
    """Delete a stored fit by name or id."""
    return store.delete_fit(fit)


def main(argv: list[str] | None = None) -> None:
    global _data_dir
    parser = argparse.ArgumentParser(prog="pyfa-mcp")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="where the server keeps its saveddata.db (default ~/.pyfa-mcp)")
    args = parser.parse_args(argv)
    _data_dir = args.data_dir
    app.run()  # stdio
```

- [ ] **Step 4: Write `pyfa_mcp/__main__.py`**

```python
from pyfa_mcp.server import main

main()
```

- [ ] **Step 5: Write `packaging/mcp_smoke.py`**

```python
"""Check that a command is a working pyfa-mcp server over stdio.

    python packaging/mcp_smoke.py COMMAND [ARGS...]

initialize, tools/list, then evaluate_fit on a small fit. Exits non-zero on
anything unexpected; a stray print on the server's stdout fails json.loads.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys

TOOLS = {"search_items", "list_ships", "item_info", "evaluate_fit", "compare_fits",
         "fit_graph", "graph_options", "conditions_format", "status", "save_fit",
         "list_fits", "get_fit", "delete_fit"}
FIT = "[Rifter, smoke]\n200mm AutoCannon II, EMP S\n"


def main(command: list[str]) -> int:
    server = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, encoding="utf-8")
    ids = itertools.count(1)

    def send(message: dict) -> None:
        server.stdin.write(json.dumps(message) + "\n")
        server.stdin.flush()

    def call(method: str, params: dict) -> dict:
        send({"jsonrpc": "2.0", "id": next(ids), "method": method, "params": params})
        line = server.stdout.readline()
        if not line:
            raise SystemExit(f"server closed stdout during {method}")
        reply = json.loads(line)
        if "error" in reply:
            raise SystemExit(f"{method}: {reply['error']}")
        return reply["result"]

    try:
        call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "smoke", "version": "0"}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        names = {t["name"] for t in call("tools/list", {})["tools"]}
        if names != TOOLS:
            raise SystemExit(f"tools mismatch: missing {TOOLS - names}, extra {names - TOOLS}")
        result = call("tools/call", {"name": "evaluate_fit", "arguments": {"fit": FIT}})
        if result.get("isError"):
            raise SystemExit(f"evaluate_fit: {result['content']}")
        print("ok")
        return 0
    finally:
        server.stdin.close()
        server.terminate()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_server.py -v`
Expected: all passed. Notes if not:
  - Cross-thread use is fine: with SQLAlchemy 2.0 eos's engines use `QueuePool` and a session created on the main thread works from the eos thread (verified 2026-10-02). `server._ensure_booted()` calls `eosboot.boot(_data_dir)`; in tests `_data_dir` is None, so set `server._data_dir = booted` at the top of `test_tools_return_data` — `boot()` returns the already-booted dir when they match. Add that line if `status()` raises `BootError` about re-pointing.
  - If `MCPServer.tool()` cannot introspect the wrapped signature, swap decorator order is wrong — `@app.tool()` must be outermost and `_tool` must use `functools.wraps` (it does).
  - If the protocol version string is rejected, use the one from `mcp.types.LATEST_PROTOCOL_VERSION`.

- [ ] **Step 7: Try it in Claude Code**

```bash
claude mcp add pyfa -- uv --directory D:/claude/pyfa-ai-assisted run python -m pyfa_mcp
```
Then ask: "Evaluate this fit" with the Zealot EFT. Expected: a stats answer that mentions its assumptions.

- [ ] **Step 8: Commit**

```bash
git add pyfa_mcp/server.py pyfa_mcp/__main__.py packaging/mcp_smoke.py tests/test_server.py tests/conftest.py
git commit -m "Serve catalog, evaluation, graphs and fit storage over MCP"
```

---

### Task 10: Reference fits checked against the Pyfa GUI

**Files:**
- Create: `tests/reference/*.eft` (8 files), `tests/reference/conditions.json`, `tests/reference/expected.json`, `scripts/record_reference.py`, `tests/test_reference.py`

**Interfaces:**
- Consumes: `evaluate.evaluate`, `stats.flatten`.
- Produces: `scripts/record_reference.py` (rewrites `expected.json`); the reference test used as the CI gate in plan 4.

- [ ] **Step 1: Write the reference fits**

`tests/reference/zealot.eft` — the Zealot from `tests/conftest.py`.

`tests/reference/megathron_blaster.eft`:
```
[Megathron, Ref Blaster Mega]
Magnetic Field Stabilizer II
Magnetic Field Stabilizer II
Magnetic Field Stabilizer II
Large Armor Repairer II
Damage Control II
Multispectrum Energized Membrane II
Multispectrum Energized Membrane II

500MN Microwarpdrive II
Warp Scrambler II
Stasis Webifier II
Large Cap Battery II

Neutron Blaster Cannon II, Void L
Neutron Blaster Cannon II, Void L
Neutron Blaster Cannon II, Void L
Neutron Blaster Cannon II, Void L
Neutron Blaster Cannon II, Void L
Neutron Blaster Cannon II, Void L
Neutron Blaster Cannon II, Void L

Large Hybrid Burst Aerator II
Large Hybrid Collision Accelerator II
Large Trimark Armor Pump I


Hammerhead II x5
```

`tests/reference/drekavac_spool.eft`:
```
[Drekavac, Ref Drekavac]
Entropic Radiation Sink II
Entropic Radiation Sink II
Damage Control II
Multispectrum Energized Membrane II
Large Armor Repairer II
Multispectrum Energized Membrane II

50MN Microwarpdrive II
Warp Scrambler II
Stasis Webifier II
Large Cap Battery II

Heavy Entropic Disintegrator II, Baryon Exotic Plasma L
Heavy Entropic Disintegrator II, Baryon Exotic Plasma L

Large Trimark Armor Pump I
Large Trimark Armor Pump I
Large Trimark Armor Pump I
```

`tests/reference/hecate_modes.eft`:
```
[Hecate, Ref Hecate]
Magnetic Field Stabilizer II
Magnetic Field Stabilizer II
Damage Control II

1MN Afterburner II
Warp Disruptor II
Stasis Webifier II

Light Neutron Blaster II, Void S
Light Neutron Blaster II, Void S
Light Neutron Blaster II, Void S
Light Neutron Blaster II, Void S
Light Neutron Blaster II, Void S

Small Hybrid Burst Aerator II
Small Hybrid Collision Accelerator II
Small Transverse Bulkhead II

Hecate Sharpshooter Mode
```

`tests/reference/thanatos_fighters.eft`:
```
[Thanatos, Ref Thanatos]
Capital Armor Repairer II
Damage Control II
Multispectrum Energized Membrane II
Multispectrum Energized Membrane II
Fighter Support Unit II
Fighter Support Unit II

Capital Cap Battery II
Fighter Squadron Optimizer II

Capital Drone Control Range Augmentor I

Templar II x9
```

`tests/reference/rifter_drugs.eft`:
```
[Rifter, Ref Rifter]
Gyrostabilizer II
Damage Control II

1MN Afterburner II
Warp Scrambler II
Stasis Webifier II

200mm AutoCannon II, Republic Fleet EMP S
200mm AutoCannon II, Republic Fleet EMP S
200mm AutoCannon II, Republic Fleet EMP S


Strong Blue Pill Booster
Synth Drop Booster
```

`tests/reference/vargur_boosted.eft`:
```
[Vargur, Ref Vargur]
Gyrostabilizer II
Gyrostabilizer II
Gyrostabilizer II
Tracking Enhancer II

Large Shield Booster II
Shield Boost Amplifier II
Multispectrum Shield Hardener II
Multispectrum Shield Hardener II
Large Cap Battery II

1400mm Howitzer Artillery II, Republic Fleet EMP L
1400mm Howitzer Artillery II, Republic Fleet EMP L
1400mm Howitzer Artillery II, Republic Fleet EMP L
1400mm Howitzer Artillery II, Republic Fleet EMP L
Bastion Module I
```

`tests/reference/damnation_booster.eft` (used only as a command fit):
```
[Damnation, Ref Damnation]
Damage Control II

Armor Command Burst II, Armor Energizing Charge
Armor Command Burst II, Armor Reinforcement Charge
```

`tests/reference/conditions.json`:
```json
{
  "zealot": {},
  "zealot_webbed_hot": {"fit": "zealot",
    "conditions": {"projected": [{"item": "Stasis Webifier II", "count": 2}],
                   "module_states": [{"module": "Medium Armor Repairer II", "state": "overheated"}]}},
  "megathron_blaster": {},
  "megathron_kinetic": {"fit": "megathron_blaster",
    "conditions": {"damage_profile": {"em": 0, "thermal": 0, "kinetic": 1, "explosive": 0}}},
  "drekavac_spool_min": {"fit": "drekavac_spool", "conditions": {"spool": "min"}},
  "drekavac_spool_max": {"fit": "drekavac_spool", "conditions": {"spool": "max"}},
  "hecate_modes": {},
  "thanatos_fighters": {},
  "rifter_drugs": {"fit": "rifter_drugs",
    "conditions": {"drug_side_effects": [{"drug": "Strong Blue Pill Booster", "effect": "capacitor"}]}},
  "vargur_boosted": {"fit": "vargur_boosted",
    "conditions": {"command": [{"fit": "@damnation_booster"}]}}
}
```
(`"@name"` inside a `fit` reference means "the text of `tests/reference/name.eft`".)

- [ ] **Step 2: Load the fits once to catch typos**

```bash
uv run python -c "
from pathlib import Path
from pyfa_mcp import eosboot, eft
from service.fit import Fit
import tempfile
eosboot.boot(Path(tempfile.mkdtemp()))
for p in sorted(Path('tests/reference').glob('*.eft')):
    f = eft.import_fit(p.read_text()); print(p.name, 'ok'); Fit.deleteFit(f.ID)
"
```
Expected: one `ok` per file. If an item name is wrong (game renamed it), the `EftError` names it with suggestions — fix the `.eft` file with the suggested name.

- [ ] **Step 3: Write `scripts/record_reference.py`**

```python
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
        flat = stats.flatten({k: v for k, v in result.items()
                              if k not in ("fit", "ship", "applied", "warnings")})
        out[case] = {k: flat[k] for k in KEYS}
    return out


if __name__ == "__main__":
    eosboot.boot(Path(tempfile.mkdtemp()))
    text = json.dumps(compute(), indent=2, sort_keys=True)
    (REF / "expected.json").write_text(re.sub(r"(\d\.\d{6})\d+", r"\1", text) + "\n")
    print("wrote", REF / "expected.json")
```

- [ ] **Step 4: Record and commit the first expected values**

```bash
uv run python scripts/record_reference.py
```
Expected: `wrote ...expected.json`. If a case raises `ConditionsError` (e.g. the side-effect text `"capacitor"` matches no or several side effects), the error lists the available names — fix `conditions.json` with an unambiguous part of one and re-run.

- [ ] **Step 5: HUMAN CHECKPOINT — compare against the Pyfa GUI**

Ask the user to open the installed Pyfa **v2.69.0**, import each `.eft` from `tests/reference/`, apply the same conditions (projected webs, overheat, damage profile, spool, drug side effect, command fit), and compare EHP, DPS, volley, speed, align, lock range and scan resolution with `expected.json`. Pyfa's GUI rounds; agreement to the GUI's displayed precision is the bar. Any mismatch is a bug in `stats.py` or `conditions.py` — fix it, re-record, re-check. Do not continue until the user confirms.

- [ ] **Step 6: Write `tests/test_reference.py`**

```python
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
```
Also create an empty `scripts/__init__.py` so `scripts.record_reference` imports.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -v`
Expected: everything passes.

- [ ] **Step 8: Commit**

```bash
git add tests/reference scripts/record_reference.py scripts/__init__.py tests/test_reference.py
git commit -m "Pin reference fits to values checked against the Pyfa GUI"
```

---

## Self-review notes

- Spec coverage (plan 1 scope): catalog tools (Task 7), evaluate/compare (Task 6), fit_graph (Task 8), conditions incl. projected/command/states/spool/drugs/profiles (Task 5), conditions_format + status + instructions (Task 9), server-side storage (Task 6), boot + wx stub + engine guard (Tasks 1–2), reference fits (Task 10), MCP smoke (Task 9). Deferred to plans 2–4 by design: `list_fits(source="pyfa")`, user profiles, `export_to_pyfa`, drift checks, packaging/registration, release pipeline.
- `status()` returns `warnings: []` here; plan 2 fills it from the drift checks.
