# pyfa-mcp Plan 2 — The user's Pyfa data, and drift checks

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the LLM read the user's own Pyfa fits and damage/target profiles, write a fit into their Pyfa on explicit request (with the spec's safeguards), and have `status()` report version drift and effects eos does not compute.

**Architecture:** A new `pyfadata.py` owns everything about `~/.pyfa`: it copies Pyfa's `saveddata.db` into our data dir with SQLite's backup API (source opened read-only), re-copies when the file's mtime/size changes, migrates an older copy with Pyfa's own migrations, and reads it through a second SQLAlchemy `Session` over eos's mappers. Exports open Pyfa's real file through a separate short-lived engine. A new `drift.py` compares versions (pinned / installed Pyfa / latest GitHub releases, cached daily) and diffs eve.db's effects against eos's handlers and a reviewed baseline file. `store.py` learns `pyfa:<id|name>` fit references, so every tool that takes a fit takes a Pyfa fit too.

**Tech Stack:** as Plan 1. Python 3.14, Pyfa v2.69.0 eos (SQLAlchemy 2.0.51), `requests` and `packaging` (both already eos dependencies), stdlib `sqlite3`.

**Spec:** `docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md`. Plan 1: `docs/superpowers/plans/2026-10-02-engine-and-mcp-tools.md` (done).

This is plan 2 of 4. Plan 3: packaging and multi-client registration (must bundle `pyfa_mcp/unhandled_effects.json` and the `pyfa-mcp` dist metadata so `drift._own_version()` works frozen). Plan 4: release pipeline (its gate runs `tests/test_drift.py::test_effect_coverage_is_clean`).

Verified by prototypes on 2026-10-02 against the author's real `~/.pyfa` (a copy):
- 547 fits load through a second `Session(bind=create_engine(snapshot))` in 0.6 s, export to EFT with `eft.export_fit`, and none is `isInvalid`.
- A fit built by `Port.importAuto(eft)[2][0]` is not attached to eos's session; after setting `character` (the DB's "All 5"), `damagePattern = DamagePattern.getDefaultBuiltin()` and `implantLocation` (NOT NULL), `session.add(fit); session.commit()` inserts it and it reloads identically.
- User DB `PRAGMA user_version` 50 = `eos.db.migration.getAppVersion()`; journal mode `delete` (so mtime tracks every write). `upgrade50` is idempotent.
- The installed Pyfa is found through its log: `~/.pyfa/pyfa-YYYY-MM-DD.log` line `Gamedata connection: sqlite:///C:\Program Files\pyfa\app\eve.db?...`; `C:\Program Files\pyfa\app\version.yml` says `version: v2.69.0`. Not in the registry uninstall keys.
- 615 effect IDs on published items have no `eos.effects.Effect{ID}` class (structural ones like `online`, `hiPower`, `turretFitted`): that is the baseline.
- A fresh server `saveddata.db` has no characters until `Character.getAll5()` runs.

## Global Constraints

- Everything in Plan 1's Global Constraints still holds (pin v2.69.0, Python 3.14, exact eos dependency versions, `mcp>=2.2,<3`, GPL-3.0-or-later, stdout is the JSON-RPC stream, unknown names are errors with suggestions, `applied`/`warnings` on every evaluation, character "All 5" only).
- Never write the user's Pyfa data unless explicitly asked: only `export_to_pyfa` opens Pyfa's `saveddata.db` for writing; every read goes through the snapshot copy.
- Pyfa data dir: `~/.pyfa`, overridable with `--pyfa-dir`. Snapshot: `<data dir>/pyfa-snapshot.db`. Release cache: `<data dir>/release-check.json`.
- Without a Pyfa install, `list_fits(source="pyfa")`, `pyfa:` references and `export_to_pyfa` fail with "no Pyfa install found"; the user's profiles are simply absent.
- `export_to_pyfa`: refuses if a `pyfa` process is running; refuses if the user DB's migration version differs from the pinned eos's (error says to export EFT instead); backs up `saveddata.db` with a timestamp; inserts a new fit only, never overwrites; name clash → ` (2)` suffix.
- Latest Pyfa release fetched at most daily, skipped quietly offline. Repos: `pyfa-org/Pyfa`, `StormDelay/pyfa-ai-assisted`.
- Tests never read or write the real `~/.pyfa` and never call GitHub.

## Review Focus

1. **A test or dev run touching the real `~/.pyfa`.** Expected: impossible, because a session-wide autouse fixture points `pyfadata` at an empty temp dir and a test asserts it. (Task 1)
2. **The user saves or renames a fit in Pyfa while the server runs.** Expected: the next `list_fits(source="pyfa")` shows the change, with no restart. (Task 1, `test_snapshot_follows_pyfa_changes`)
3. **A refused export (Pyfa running, schema mismatch, unknown item, trimmed fit, no install).** Expected: Pyfa's database is byte-identical afterwards and no backup file was made. (Task 3, `_refused`)
4. **A user profile with the same name as a built-in one, or a misspelled profile name.** Expected: the built-in wins, and a misspelling suggests names from both lists. (Task 2)
5. **`status()` offline or rate-limited by GitHub.** Expected: no exception and no invented warning; the last cached answer is kept. (Task 4)

---

## File Structure

```
pyfa_mcp/pyfadata.py           NEW  ~/.pyfa: locate, snapshot + migrate, fits, profiles, export
pyfa_mcp/drift.py              NEW  versions + release check, effect coverage, per-fit warnings
pyfa_mcp/unhandled_effects.json NEW reviewed baseline of effect IDs eos has no handler for
scripts/record_unhandled_effects.py NEW  rewrite the baseline
pyfa_mcp/store.py              pyfa: references, list_fits(source), export_to_pyfa validation
pyfa_mcp/conditions.py         user's damage/target profiles by name
pyfa_mcp/evaluate.py           effect warnings in results
pyfa_mcp/server.py             --pyfa-dir, list_fits(source), export_to_pyfa, status
packaging/mcp_smoke.py         export_to_pyfa in the tool list
pyproject.toml                 ship the baseline json as package data
tests/conftest.py              no-real-Pyfa + offline autouse fixtures, pyfa_home fixture
tests/test_pyfadata.py         NEW
tests/test_export.py           NEW
tests/test_drift.py            NEW
tests/test_conditions.py, tests/test_server.py   additions
docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md   pyfa: references
```

---

### Task 1: Read the user's Pyfa fits through a snapshot

**Files:**
- Create: `pyfa_mcp/pyfadata.py`, `tests/test_pyfadata.py`
- Modify: `tests/conftest.py`, `pyfa_mcp/store.py`, `pyfa_mcp/server.py`, `tests/test_server.py`, `docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md`

**Interfaces:**
- Consumes: `eosboot.pyfa_version()`, `eft.export_fit(fit)`, eos `migration.getVersion/getAppVersion`, `eos.db.migrations.updates`.
- Produces:
  - `pyfadata.PyfaDataError(LookupError)`
  - `pyfadata.set_dir(path: Path | None) -> None`, `pyfadata.pyfa_dir() -> Path`, `pyfadata.installed() -> bool`
  - `pyfadata.fits() -> list[eos Fit]` (snapshot objects, never invalid ones)
  - `pyfadata.close() -> None`
  - private, used by Task 3: `pyfadata._user_db() -> Path`, `pyfadata._copy(src: Path, dst: Path)`
  - `store.PYFA_PREFIX = "pyfa:"`; `store.list_fits(ship=None, source="server")`; Pyfa entries are `{"id": "pyfa:<ID>", "name", "ship"}`; `store.resolve_eft` / `get_fit` accept `pyfa:<id|name>`; `store.delete_fit("pyfa:...")` raises `StoreError` ("read-only")
  - conftest fixture `pyfa_home` -> `Path` of a fake Pyfa data dir whose `saveddata.db` holds one fit "Home Zealot" (Task 2 adds profiles to it)

- [ ] **Step 1: Add the fixtures to `tests/conftest.py`**

Append to `tests/conftest.py`:

```python
@pytest.fixture(scope="session", autouse=True)
def _no_real_pyfa(tmp_path_factory):
    """No test may read or write the real ~/.pyfa: point pyfadata at an empty dir."""
    from pyfa_mcp import pyfadata
    pyfadata.set_dir(tmp_path_factory.mktemp("no-pyfa"))


@pytest.fixture
def pyfa_home(booted, tmp_path, zealot_eft):
    """A Pyfa data dir whose saveddata.db is a copy of ours holding one fit, 'Home Zealot'."""
    import contextlib
    import sqlite3

    from eos.saveddata.character import Character
    from pyfa_mcp import pyfadata, store

    Character.getAll5()  # a fresh server DB has no characters; a real Pyfa DB always has
    entry = store.save_fit(zealot_eft, "Home Zealot")
    home = tmp_path / "pyfa"
    home.mkdir()
    try:
        with contextlib.closing(sqlite3.connect(booted / "saveddata.db")) as src, \
                contextlib.closing(sqlite3.connect(home / "saveddata.db")) as dst:
            src.backup(dst)
    finally:
        store.delete_fit(str(entry["id"]))
    previous = pyfadata.pyfa_dir()
    pyfadata.set_dir(home)
    yield home
    pyfadata.close()
    pyfadata.set_dir(previous)
```

- [ ] **Step 2: Write the failing tests `tests/test_pyfadata.py`**

```python
import contextlib
import sqlite3
from pathlib import Path

import pytest

from pyfa_mcp import evaluate, pyfadata, store


def _sql(home: Path, *statements: str) -> None:
    with contextlib.closing(sqlite3.connect(home / "saveddata.db")) as db:
        for statement in statements:
            db.execute(statement)
        db.commit()


def test_tests_never_see_the_real_pyfa(booted):
    assert pyfadata.pyfa_dir() != Path.home() / ".pyfa"


def test_no_pyfa_install(booted):
    assert not pyfadata.installed()
    with pytest.raises(pyfadata.PyfaDataError, match="no Pyfa install"):
        store.list_fits(source="pyfa")
    with pytest.raises(pyfadata.PyfaDataError, match="no Pyfa install"):
        store.resolve_eft("pyfa:Home Zealot")


def test_lists_and_reads_pyfa_fits(pyfa_home):
    entries = store.list_fits(source="pyfa")
    assert [e["name"] for e in entries] == ["Home Zealot"]
    ref = entries[0]["id"]
    assert ref.startswith("pyfa:") and ref[5:].isdigit()
    got = store.get_fit(ref)
    assert got["eft"].startswith("[Zealot, Home Zealot]")
    assert store.get_fit("pyfa:home zealot")["id"] == ref  # names are case-insensitive
    assert store.get_fit("PYFA:Home Zealot")["id"] == ref  # so is the prefix
    assert store.list_fits(source="pyfa", ship="Rifter") == []
    assert "Home Zealot" not in [e["name"] for e in store.list_fits()]  # server list is separate


def test_unknown_source_refused(booted):
    with pytest.raises(store.StoreError, match="source"):
        store.list_fits(source="eve")


def test_pyfa_fit_evaluates(pyfa_home, no_fits_left):
    assert evaluate.evaluate("pyfa:Home Zealot", None)["ship"] == "Zealot"


def test_pyfa_fit_unknown_suggests(pyfa_home):
    with pytest.raises(store.StoreError, match="Home Zealot"):
        store.resolve_eft("pyfa:Home Zealott")


def test_same_named_pyfa_fits_ask_for_an_id(pyfa_home):
    _sql(pyfa_home,
         "CREATE TEMP TABLE twin AS SELECT * FROM fits",
         "UPDATE twin SET ID = ID + 1000",
         "INSERT INTO fits SELECT * FROM twin")
    with pytest.raises(store.StoreError, match=r"use an id: pyfa:\d+, pyfa:\d+"):
        store.resolve_eft("pyfa:Home Zealot")


def test_pyfa_fits_are_read_only(pyfa_home):
    with pytest.raises(store.StoreError, match="read-only"):
        store.delete_fit("pyfa:Home Zealot")


def test_snapshot_follows_pyfa_changes(pyfa_home):
    assert [e["name"] for e in store.list_fits(source="pyfa")] == ["Home Zealot"]
    _sql(pyfa_home, "UPDATE fits SET name = 'Renamed Zealot'")
    assert [e["name"] for e in store.list_fits(source="pyfa")] == ["Renamed Zealot"]


def test_pyfa_file_is_never_written_by_reads(pyfa_home):
    before = (pyfa_home / "saveddata.db").read_bytes()
    store.list_fits(source="pyfa")
    store.get_fit("pyfa:Home Zealot")
    evaluate.evaluate("pyfa:Home Zealot", None)
    assert (pyfa_home / "saveddata.db").read_bytes() == before


def test_older_pyfa_database_is_migrated_on_the_copy(pyfa_home):
    # Pinned schema is v50; upgrade50 is what adds commandLinks. Revisit on a schema bump.
    _sql(pyfa_home, "DROP TABLE commandLinks", "PRAGMA user_version = 49")
    assert [e["name"] for e in store.list_fits(source="pyfa")] == ["Home Zealot"]
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 49  # the user's file is untouched


def test_newer_pyfa_database_is_refused(pyfa_home):
    _sql(pyfa_home, "PRAGMA user_version = 999")
    with pytest.raises(pyfadata.PyfaDataError, match="update pyfa-mcp"):
        store.list_fits(source="pyfa")
```

- [ ] **Step 3: Run to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_pyfadata.py -q`
Expected: collection error, `ImportError: cannot import name 'pyfadata'`.

- [ ] **Step 4: Write `pyfa_mcp/pyfadata.py`**

```python
"""The user's own Pyfa data: read from a snapshot, written only by export_fit.

Pyfa keeps fits and profiles in <pyfa dir>/saveddata.db (~/.pyfa unless the
server is started with --pyfa-dir). Reads go through a copy in our data dir,
re-made whenever Pyfa's file changes, so Pyfa's file is never opened for
writing and an older schema can be migrated on the copy. Both use eos's own
mappers through their own SQLAlchemy sessions; game items still come from
our eve.db.
"""
from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

_dir: Path | None = None
_snapshot: dict = {}


class PyfaDataError(LookupError):
    """No Pyfa data found, or the user's Pyfa data cannot be used as asked."""

    def __str__(self):  # LookupError would quote the message
        return str(self.args[0]) if self.args else ""


def set_dir(path: Path | None) -> None:
    global _dir
    _dir = Path(path) if path else None


def pyfa_dir() -> Path:
    return _dir or Path.home() / ".pyfa"


def installed() -> bool:
    return (pyfa_dir() / "saveddata.db").is_file()


def _user_db() -> Path:
    db = pyfa_dir() / "saveddata.db"
    if not db.is_file():
        raise PyfaDataError(f"no Pyfa install found (no {db}); if Pyfa keeps its data "
                            "elsewhere, start pyfa-mcp with --pyfa-dir")
    return db


def _copy(src: Path, dst: Path) -> None:
    """A consistent copy even while Pyfa has the file open; src is opened read-only."""
    with contextlib.closing(sqlite3.connect(f"{src.as_uri()}?mode=ro", uri=True)) as source, \
            contextlib.closing(sqlite3.connect(dst)) as target:
        source.backup(target)


def _migrate(engine) -> None:
    from eos.db import migration, migrations
    from eos.db.migration import _MigrationConnection

    from pyfa_mcp import eosboot

    have, want = migration.getVersion(engine), migration.getAppVersion()
    if have > want:
        raise PyfaDataError(
            f"your Pyfa's database is version {have}, newer than Pyfa "
            f"{eosboot.pyfa_version()} inside pyfa-mcp reads ({want}); update pyfa-mcp")
    if have == want:
        return
    # Pyfa's migration.update() would also back up *our* saveddata.db; the
    # snapshot is a throwaway copy, so run the same upgrades on it directly.
    with engine.connect() as connection:
        wrapped = _MigrationConnection(connection)
        for version in range(have, want):
            upgrade = migrations.updates[version + 1]
            if upgrade:
                upgrade(wrapped)
        wrapped.execute(f"PRAGMA user_version = {want}")


def _session():
    """The snapshot's session, re-copied whenever Pyfa's file has changed."""
    import config
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    src = _user_db()
    stat = src.stat()
    key = (str(src), stat.st_mtime_ns, stat.st_size)
    if _snapshot.get("key") != key:
        close()
        snap = Path(config.savePath) / "pyfa-snapshot.db"
        _copy(src, snap)
        engine = create_engine(f"sqlite:///{snap}")
        try:
            _migrate(engine)
        except BaseException:
            engine.dispose()
            raise
        _snapshot.update(key=key, engine=engine,
                         session=Session(bind=engine, autoflush=False, expire_on_commit=False))
    return _snapshot["session"]


def close() -> None:
    if _snapshot:
        _snapshot["session"].close()
        _snapshot["engine"].dispose()
        _snapshot.clear()


def fits() -> list:
    from eos.saveddata.fit import Fit
    return [fit for fit in _session().query(Fit).all() if not fit.isInvalid]
```

- [ ] **Step 5: Teach `pyfa_mcp/store.py` about `pyfa:` references**

Replace the module docstring, imports and `_find` (everything from the top of the file through the end of `_find`) with:

```python
"""Fits the user chose to keep, in the server's saveddata.db, and fits in
the user's own Pyfa (read-only).

A fit reference is EFT text, a stored fit's numeric id or name
(case-insensitive), or `pyfa:` followed by a Pyfa fit's id or name.
Temporary evaluation fits are never visible here.
"""
from __future__ import annotations

import difflib

from pyfa_mcp import eft, pyfadata
from pyfa_mcp.eosboot import TEMP_NOTE

PYFA_PREFIX = "pyfa:"


class StoreError(LookupError):
    """No such stored fit, or a name already taken."""

    def __str__(self):  # LookupError would quote the message
        return str(self.args[0]) if self.args else ""


def _stored():
    from service.fit import Fit
    return [f for f in Fit.getAllFits() if f.notes != TEMP_NOTE]


def _entry(fit) -> dict:
    return {"id": fit.ID, "name": fit.name, "ship": fit.ship.item.name}


def _pyfa_entry(fit) -> dict:
    return {"id": f"{PYFA_PREFIX}{fit.ID}", "name": fit.name, "ship": fit.ship.item.name}


def _pick(fits, ref: str, kind: str, listing: str, id_prefix: str = ""):
    ref = ref.strip()
    if ref.isdigit():
        for fit in fits:
            if fit.ID == int(ref):
                return fit
        raise StoreError(f"no {kind} with id {ref}")
    matches = [f for f in fits if f.name.casefold() == ref.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:  # Pyfa allows it; stored fits only from before names were unique
        ids = ", ".join(f"{id_prefix}{f.ID}" for f in matches)
        raise StoreError(f"several {kind}s are named '{ref}'; use an id: {ids}")
    close = difflib.get_close_matches(ref, [f.name for f in fits], n=3, cutoff=0.5)
    hint = f" (did you mean: {', '.join(close)}?)" if close else ""
    raise StoreError(f"no {kind} named '{ref}'{hint}; pass EFT text or see {listing}")


def _find(ref: str):
    return _pick(_stored(), ref, "stored fit", "list_fits()")


def _pyfa_ref(ref: str) -> str | None:
    """The part after `pyfa:`, or None for any other reference."""
    ref = ref.strip()
    return ref[len(PYFA_PREFIX):] if ref.casefold().startswith(PYFA_PREFIX) else None


def _find_pyfa(rest: str):
    return _pick(pyfadata.fits(), rest, "Pyfa fit", 'list_fits(source="pyfa")', PYFA_PREFIX)
```

Replace `resolve_eft`:

```python
def resolve_eft(ref: str) -> str:
    if eft.looks_like_eft(ref):
        return ref
    rest = _pyfa_ref(ref)
    if rest is not None:
        return eft.export_fit(_find_pyfa(rest))
    from service.fit import Fit
    return eft.export_fit(Fit.getInstance().getFit(_find(ref).ID))
```

Replace `list_fits`, `get_fit` and `delete_fit`:

```python
def list_fits(ship: str | None = None, source: str = "server") -> list[dict]:
    if source == "server":
        fits, entry = _stored(), _entry
    elif source == "pyfa":
        fits, entry = pyfadata.fits(), _pyfa_entry
    else:
        raise StoreError('source must be "server" or "pyfa"')
    if ship:
        fits = [f for f in fits if f.ship.item.name.casefold() == ship.casefold()]
    return sorted((entry(f) for f in fits), key=lambda e: e["name"].casefold())


def get_fit(ref: str) -> dict:
    rest = _pyfa_ref(ref)
    if rest is not None:
        fit = _find_pyfa(rest)
        return {**_pyfa_entry(fit), "eft": eft.export_fit(fit)}
    from service.fit import Fit
    fit = _find(ref)
    return {**_entry(fit), "eft": eft.export_fit(Fit.getInstance().getFit(fit.ID))}


def delete_fit(ref: str) -> dict:
    if _pyfa_ref(ref) is not None:
        raise StoreError("fits in the user's Pyfa are read-only here; delete them in Pyfa")
    from service.fit import Fit
    fit = _find(ref)
    entry = _entry(fit)
    Fit.deleteFit(fit.ID)
    return {"deleted": entry}
```

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_pyfadata.py tests/test_store.py -q`
Expected: all pass.

- [ ] **Step 7: Wire the server**

In `pyfa_mcp/server.py`:

Change the import line to:
```python
from pyfa_mcp import catalog, conditions, eft, eosboot, evaluate, graphs, pyfadata, store
```

Add `pyfadata.PyfaDataError` to `_USER_ERRORS`:
```python
_USER_ERRORS = (eft.EftError, conditions.ConditionsError, store.StoreError,
                catalog.CatalogError, graphs.GraphError, pyfadata.PyfaDataError, ValueError)
```

In `INSTRUCTIONS`, replace the first bullet with:
```
- Fits are EFT text (as Pyfa or the game exports them), the name/id of a
  fit saved with save_fit, or "pyfa:<id or name>" for a fit in the user's
  own Pyfa (list_fits(source="pyfa"); read-only). To change a fit, edit the
  EFT and evaluate again.
```

Replace the `list_fits`, `get_fit` and `delete_fit` tools:
```python
@app.tool()
@_tool
def list_fits(ship: str | None = None, source: str = "server") -> list:
    """Fits stored in the server's database (source="server") or in the user's
    own Pyfa (source="pyfa", read-only, ids like "pyfa:12"), optionally for one ship."""
    return store.list_fits(ship, source)


@app.tool()
@_tool
def get_fit(fit: str) -> dict:
    """A stored fit (name or id) or a Pyfa fit ("pyfa:<id or name>") as EFT text."""
    return store.get_fit(fit)


@app.tool()
@_tool
def delete_fit(fit: str) -> dict:
    """Delete a fit stored in the server's database, by name or id. Pyfa fits are read-only."""
    return store.delete_fit(fit)
```

In `main`, add the flag and apply it:
```python
    parser.add_argument("--pyfa-dir", type=Path, default=None,
                        help="the user's Pyfa data dir (default ~/.pyfa); read, and "
                             "written only by export_to_pyfa")
    args = parser.parse_args(argv)
    _data_dir = args.data_dir
    pyfadata.set_dir(args.pyfa_dir)
    app.run()  # stdio
```

Append to `tests/test_server.py`:
```python
def test_pyfa_fits_without_pyfa(booted):
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="no Pyfa install"):
        server.list_fits(source="pyfa")


def test_pyfa_fits_over_the_server(pyfa_home):
    assert [f["name"] for f in server.list_fits(source="pyfa")] == ["Home Zealot"]
```

- [ ] **Step 8: Note `pyfa:` references in the spec**

In `docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md`, replace
```
- Stateless evaluation over EFT text. Every `fit` argument accepts EFT text
  or the name/id of a stored fit.
```
with
```
- Stateless evaluation over EFT text. Every `fit` argument accepts EFT text,
  the name/id of a stored fit, or `pyfa:<id or name>` for a fit in the
  user's Pyfa.
```

- [ ] **Step 9: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 10: Commit**

```bash
git add pyfa_mcp/pyfadata.py pyfa_mcp/store.py pyfa_mcp/server.py tests/conftest.py tests/test_pyfadata.py tests/test_server.py docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md
git commit -m "Read the user's Pyfa fits from a snapshot, and take pyfa: references wherever a fit goes"
```

---

### Task 2: The user's own damage and target profiles

**Files:**
- Modify: `pyfa_mcp/pyfadata.py`, `pyfa_mcp/conditions.py`, `tests/conftest.py`, `tests/test_conditions.py`

**Interfaces:**
- Consumes: `pyfadata._session()`, `pyfadata.installed()`, `pyfa_home` fixture.
- Produces:
  - `pyfadata.damage_profiles() -> dict[str, dict]` name -> `{"em","thermal","kinetic","explosive"}`; `{}` without Pyfa
  - `pyfadata.target_profiles() -> dict[str, dict]` name -> `{"resists": {...}, "speed", "signature", "radius"}` (the custom `target` condition's shape; `None` = not set); `{}` without Pyfa
  - `conditions.describe()` gains `your_damage_profiles` and `your_target_profiles` (sorted name lists), or `your_profiles_error` (str) when the user's DB cannot be read
  - `pyfa_home`'s DB now also holds damage profile "Home EM" (1/0/0/0) and target profile "Home Target" (resists 0.5 each, signature 40)

- [ ] **Step 1: Seed profiles in the `pyfa_home` fixture**

In `tests/conftest.py`, inside `pyfa_home`, after `src.backup(dst)` (still inside the `with` block), add:
```python
            dst.execute("INSERT INTO damagePatterns (name, emAmount, thermalAmount, "
                        "kineticAmount, explosiveAmount) VALUES ('Home EM', 1, 0, 0, 0)")
            dst.execute("INSERT INTO targetResists (name, emAmount, thermalAmount, "
                        "kineticAmount, explosiveAmount, signatureRadius) "
                        "VALUES ('Home Target', 0.5, 0.5, 0.5, 0.5, 40)")
            dst.commit()
```
and change its docstring to `"""A Pyfa data dir whose saveddata.db is a copy of ours holding one fit, 'Home Zealot', a damage profile 'Home EM' and a target profile 'Home Target'."""`.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_conditions.py`:
```python
def test_user_damage_profile_by_name(temp_fits, zealot_eft, pyfa_home):
    mine = temp_fits(zealot_eft)
    applied = C.apply(mine, C.parse({"damage_profile": "home em"}), temp_fits)
    same = temp_fits(zealot_eft)
    C.apply(same, C.parse({"damage_profile":
        {"em": 1, "thermal": 0, "kinetic": 0, "explosive": 0}}), temp_fits)
    assert mine.ehp["armor"] == pytest.approx(same.ehp["armor"])
    assert applied["damage_profile"] == "home em"


def test_user_target_profile_by_name(temp_fits, zealot_eft, pyfa_home):
    fit = temp_fits(zealot_eft)
    C.apply(fit, C.parse({"target": "Home Target"}), temp_fits)
    assert fit.targetProfile.signatureRadius == 40
    assert fit.targetProfile.emAmount == 0.5


def test_misspelled_profile_suggests_user_profiles(temp_fits, zealot_eft, pyfa_home):
    fit = temp_fits(zealot_eft)
    with pytest.raises(C.ConditionsError, match="Home EM"):
        C.apply(fit, C.parse({"damage_profile": "Home EMM"}), temp_fits)


def test_builtin_profile_wins_over_a_user_profile_of_the_same_name(pyfa_home):
    import contextlib
    import sqlite3
    from pyfa_mcp.eosboot import TEMP_NOTE
    name = next(iter(C._builtin_damage_profiles()))
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        db.execute("INSERT INTO damagePatterns (name, emAmount, thermalAmount, kineticAmount, "
                   "explosiveAmount) VALUES (?, 1, 0, 0, 0)", (name,))
        db.commit()
    pattern = C.damage_pattern(C.parse({"damage_profile": name}))
    assert pattern.rawName != TEMP_NOTE  # a built-in object, not a copy of the user's row


def test_describe_lists_user_profiles(pyfa_home):
    described = C.describe()
    assert described["your_damage_profiles"] == ["Home EM"]
    assert described["your_target_profiles"] == ["Home Target"]


def test_describe_without_pyfa(booted):
    described = C.describe()
    assert described["your_damage_profiles"] == []
    assert described["your_target_profiles"] == []
```

- [ ] **Step 3: Run to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_conditions.py -q -k "user or describe"`
Expected: FAIL (`unknown damage profile 'home em'`, `KeyError: 'your_damage_profiles'`).

- [ ] **Step 4: Read profiles in `pyfa_mcp/pyfadata.py`**

Append:
```python
def damage_profiles() -> dict[str, dict]:
    """The user's own damage profiles as {em, thermal, kinetic, explosive} weights."""
    if not installed():
        return {}
    from eos.saveddata.damagePattern import DamagePattern
    return {p.rawName: {"em": p.emAmount, "thermal": p.thermalAmount,
                        "kinetic": p.kineticAmount, "explosive": p.explosiveAmount}
            for p in _session().query(DamagePattern).all() if p.rawName}


def target_profiles() -> dict[str, dict]:
    """The user's own target profiles, shaped like a custom `target` condition."""
    if not installed():
        return {}
    from eos.saveddata.targetProfile import TargetProfile
    # The underscored attributes keep None ("not set"); the properties turn it into 0.
    return {p.rawName: {"resists": {"em": p.emAmount, "thermal": p.thermalAmount,
                                    "kinetic": p.kineticAmount, "explosive": p.explosiveAmount},
                        "speed": p._maxVelocity, "signature": p._signatureRadius,
                        "radius": p._radius}
            for p in _session().query(TargetProfile).all() if p.rawName}
```

- [ ] **Step 5: Resolve them in `pyfa_mcp/conditions.py`**

Add to the imports:
```python
from pyfa_mcp import pyfadata
```

Change the two `_FIELDS` texts:
```python
    "damage_profile": "Incoming damage for EHP: \"uniform\", a Pyfa built-in profile "
                      "name, one of the user's own Pyfa profiles (your_damage_profiles), "
                      "or {em, thermal, kinetic, explosive} weights.",
    "target": "Target for applied damage: a Pyfa built-in target profile name, one of "
              "the user's own (your_target_profiles), or {resists: {em, thermal, "
              "kinetic, explosive} as 0..1, signature, speed, radius}.",
```

Replace `_by_name`, `damage_pattern` and `target_profile` with:
```python
def _match(profiles: dict, name: str):
    for full, profile in profiles.items():
        if full.casefold() == name.casefold():
            return profile
    return None


def _by_name(name: str, kind: str, builtins: dict, mine: Callable[[], dict]):
    """A built-in profile, else one of the user's own (a dict, as in a condition).

    Built-ins first, so the user's Pyfa is read only when the name needs it.
    """
    found = _match(builtins, name)
    if found is not None:
        return found
    own = mine()
    found = _match(own, name)
    if found is not None:
        return found
    close = difflib.get_close_matches(name, [*builtins, *own], n=3, cutoff=0.5)
    hint = f" (did you mean: {', '.join(close)}?)" if close else ""
    raise ConditionsError(f"unknown {kind} '{name}'{hint}; see conditions_format()")


def damage_pattern(cond: Conditions):
    from eos.saveddata.damagePattern import DamagePattern
    value = cond.damage_profile
    if value == "uniform":
        return DamagePattern.getDefaultBuiltin()
    if isinstance(value, str):
        value = _by_name(value, "damage profile", _builtin_damage_profiles(),
                         pyfadata.damage_profiles)
        if not isinstance(value, dict):
            return value
    pattern = DamagePattern(value["em"], value["thermal"], value["kinetic"], value["explosive"])
    # A user pattern: it is saved with the temporary fit and purged with it.
    pattern.rawName = TEMP_NOTE
    return pattern


def target_profile(cond: Conditions):
    from eos.saveddata.targetProfile import TargetProfile
    value = cond.target
    if value is None:
        return None
    if isinstance(value, str):
        value = _by_name(value, "target profile", _builtin_target_profiles(),
                         pyfadata.target_profiles)
        if not isinstance(value, dict):
            return value
    resists = value.get("resists", dict.fromkeys(_DAMAGE_KEYS, 0))
    profile = TargetProfile(
        resists["em"], resists["thermal"], resists["kinetic"], resists["explosive"],
        maxVelocity=value.get("speed"), signatureRadius=value.get("signature"),
        radius=value.get("radius"))
    profile.rawName = TEMP_NOTE
    return profile
```

In `describe()`, compute the user's lists first and merge them into the returned dict:
```python
def describe() -> dict:
    try:
        mine = {"your_damage_profiles": sorted(pyfadata.damage_profiles()),
                "your_target_profiles": sorted(pyfadata.target_profiles())}
    except pyfadata.PyfaDataError as exc:
        mine = {"your_profiles_error": str(exc)}
    return {
        "fields": _FIELDS,
        "defaults": {"character": "All 5", "damage_profile": "uniform",
                     "target": None, "spool": "Pyfa default (full)",
                     "module_states": "as in the EFT (modules active, /OFFLINE honoured)"},
        "damage_profiles": ["uniform", *_builtin_damage_profiles()],
        "target_profiles": list(_builtin_target_profiles()),
        **mine,
        "examples": _EXAMPLES,
        "in_eft_instead": "implants, drugs (boosters), charges, drones/fighters with "
                          "counts, /OFFLINE modules and mutated modules go in the EFT text",
    }
```

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/pyfadata.py pyfa_mcp/conditions.py tests/conftest.py tests/test_conditions.py
git commit -m "Accept the user's own Pyfa damage and target profiles by name"
```

---

### Task 3: `export_to_pyfa`, with its safeguards

**Files:**
- Create: `tests/test_export.py`
- Modify: `pyfa_mcp/pyfadata.py`, `pyfa_mcp/store.py`, `pyfa_mcp/server.py`, `packaging/mcp_smoke.py`

**Interfaces:**
- Consumes: `pyfadata._user_db()`, `pyfadata._copy()`, `store.resolve_eft()`, `eft.import_fit(text, temp=True)` (sets `.dropped_modules`), `eosboot.pyfa_version()`.
- Produces:
  - `pyfadata.export_fit(eft_text: str, name: str | None = None) -> dict` `{"id": "pyfa:<ID>", "name", "ship", "backup": str}`; raises `PyfaDataError`
  - `pyfadata._pyfa_running() -> bool` (tests monkeypatch it)
  - `store.export_to_pyfa(ref: str, name: str | None = None) -> dict` (same dict)
  - MCP tool `export_to_pyfa(fit, name=None)`

- [ ] **Step 1: Write the failing tests `tests/test_export.py`**

```python
import contextlib
import hashlib
import sqlite3
from pathlib import Path

import pytest

from pyfa_mcp import eft, pyfadata, store


@pytest.fixture(autouse=True)
def _pyfa_not_running(monkeypatch):
    monkeypatch.setattr(pyfadata, "_pyfa_running", lambda: False)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refused(home: Path, match: str, call) -> None:
    """The call fails, Pyfa's file is byte-identical, and no backup was made."""
    db = home / "saveddata.db"
    before = _digest(db)
    with pytest.raises((store.StoreError, pyfadata.PyfaDataError, eft.EftError), match=match):
        call()
    assert _digest(db) == before
    assert not list(home.glob("*backup*"))


def test_export_inserts_a_new_fit_after_a_backup(pyfa_home, zealot_eft, no_fits_left):
    before = _digest(pyfa_home / "saveddata.db")
    result = store.export_to_pyfa(zealot_eft, "Exported Zealot")
    assert result["name"] == "Exported Zealot" and result["ship"] == "Zealot"
    assert _digest(Path(result["backup"])) == before
    assert sorted(e["name"] for e in store.list_fits(source="pyfa")) == \
        ["Exported Zealot", "Home Zealot"]
    assert store.get_fit(result["id"])["eft"].startswith("[Zealot, Exported Zealot]")


def test_export_never_overwrites(pyfa_home, zealot_eft, no_fits_left):
    assert store.export_to_pyfa(zealot_eft, "Home Zealot")["name"] == "Home Zealot (2)"
    assert store.export_to_pyfa(zealot_eft, "home zealot")["name"] == "home zealot (3)"
    assert len(store.list_fits(source="pyfa")) == 3


def test_export_keeps_the_eft_name_by_default(pyfa_home, zealot_eft, no_fits_left):
    assert store.export_to_pyfa(zealot_eft)["name"] == "Test Zealot"


def test_export_a_stored_or_pyfa_fit(pyfa_home, no_fits_left):
    assert store.export_to_pyfa("pyfa:Home Zealot")["name"] == "Home Zealot (2)"


def test_refused_while_pyfa_runs(pyfa_home, zealot_eft, monkeypatch, no_fits_left):
    monkeypatch.setattr(pyfadata, "_pyfa_running", lambda: True)
    _refused(pyfa_home, "Pyfa is running", lambda: store.export_to_pyfa(zealot_eft))


def test_refused_on_another_schema_version(pyfa_home, zealot_eft, no_fits_left):
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        db.execute("PRAGMA user_version = 49")
        db.commit()
    _refused(pyfa_home, "EFT", lambda: store.export_to_pyfa(zealot_eft))


def test_refused_for_an_unknown_item(pyfa_home, no_fits_left):
    _refused(pyfa_home, "Heat Sinkk II",
             lambda: store.export_to_pyfa("[Zealot, typo]\nHeat Sinkk II\n"))


def test_refused_for_a_trimmed_fit(pyfa_home, no_fits_left):
    over = "[Zealot, over]\n\n\n" + "Heavy Pulse Laser II\n" * 7
    _refused(pyfa_home, "left out", lambda: store.export_to_pyfa(over))


def test_refused_without_pyfa(booted, zealot_eft, no_fits_left):
    with pytest.raises(pyfadata.PyfaDataError, match="no Pyfa install"):
        store.export_to_pyfa(zealot_eft)
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_export.py -q`
Expected: FAIL, `AttributeError: module 'pyfa_mcp.store' has no attribute 'export_to_pyfa'` (and the autouse fixture fails on `_pyfa_running`).

- [ ] **Step 3: Write the export in `pyfa_mcp/pyfadata.py`**

Add to the imports:
```python
import os
import subprocess
from datetime import datetime
```

Append:
```python
def _pyfa_running() -> bool:
    # ponytail: sees the packaged pyfa.exe only, not Pyfa run from source
    # (python pyfa.py); add a cmdline scan if someone runs it that way.
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pyfa.exe", "/NH"],
                             capture_output=True, text=True,
                             creationflags=subprocess.CREATE_NO_WINDOW).stdout
        return "pyfa.exe" in out.lower()
    return subprocess.run(["pgrep", "-ix", "pyfa"], capture_output=True).returncode == 0


def _free_name(session, wanted: str) -> str:
    from eos.saveddata.fit import Fit
    taken = {name.casefold() for (name,) in session.query(Fit.name)}
    name, n = wanted, 2
    while name.casefold() in taken:
        name, n = f"{wanted} ({n})", n + 1
    return name


def export_fit(eft_text: str, name: str | None = None) -> dict:
    """Insert eft_text as a new fit in the user's Pyfa. Never changes an existing row.

    The caller has already checked the EFT strictly (store.export_to_pyfa).
    """
    from eos.const import ImplantLocation
    from eos.db import migration
    from eos.saveddata.character import Character
    from eos.saveddata.damagePattern import DamagePattern
    from service.port import Port
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from pyfa_mcp import eosboot

    db = _user_db()
    if _pyfa_running():
        raise PyfaDataError("Pyfa is running: ask the user to close it, then export again")
    engine = create_engine(f"sqlite:///{db}")
    try:
        have, want = migration.getVersion(engine), migration.getAppVersion()
        if have != want:
            raise PyfaDataError(
                f"not exported: the user's Pyfa database is version {have}, but Pyfa "
                f"{eosboot.pyfa_version()} inside pyfa-mcp writes version {want}. Give the "
                "user the EFT text (get_fit) to import in Pyfa instead")
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            all5 = session.query(Character).filter(Character.savedName == "All 5").first()
            if all5 is None:
                raise PyfaDataError("the user's Pyfa database has no 'All 5' character; "
                                    "start Pyfa once, close it, and export again")
            fit = Port.importAuto(eft_text)[2][0]
            fit.name = _free_name(session, name or fit.name)
            # What Port.importFitFromBuffer sets on every fit it imports.
            fit.character = all5
            fit.damagePattern = DamagePattern.getDefaultBuiltin()
            fit.implantLocation = ImplantLocation.FIT
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup = db.with_name(f"saveddata_pyfa-mcp-backup_{stamp}.db")
            _copy(db, backup)
            session.add(fit)
            session.commit()
            return {"id": f"pyfa:{fit.ID}", "name": fit.name,
                    "ship": fit.ship.item.name, "backup": str(backup)}
    finally:
        engine.dispose()
```

- [ ] **Step 4: Validate and delegate in `pyfa_mcp/store.py`**

Add a shared refusal helper above `save_fit`, and use it in `save_fit`:
```python
def _refuse_dropped(fit, what: str) -> None:
    """A fit Pyfa trimmed is not what the user wrote: delete it and say what went."""
    if fit.dropped_modules:
        from service.fit import Fit
        Fit.deleteFit(fit.ID)
        left_out = "; ".join(f"{d.name} ({d.reason})" for d in fit.dropped_modules)
        raise StoreError(f"{what}: Pyfa left out {left_out}; fix the fit first")


def save_fit(ref: str, name: str) -> dict:
    name = name.strip()
    if not name:
        raise StoreError("a stored fit needs a name")
    if any(f.name.casefold() == name.casefold() for f in _stored()):
        raise StoreError(f"a stored fit is already named '{name}'; delete it or pick another name")
    fit = eft.import_fit(resolve_eft(ref), name=name)
    _refuse_dropped(fit, "not saved")
    return _entry(fit)
```

Append:
```python
def export_to_pyfa(ref: str, name: str | None = None) -> dict:
    """Write a fit into the user's Pyfa as a new fit, after the same strict checks as save_fit."""
    from service.fit import Fit
    text = resolve_eft(ref)
    fit = eft.import_fit(text, temp=True)  # unknown names raise here
    _refuse_dropped(fit, "not exported")
    Fit.deleteFit(fit.ID)
    return pyfadata.export_fit(text, name.strip() if name and name.strip() else None)
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_export.py tests/test_store.py -q`
Expected: all pass.

- [ ] **Step 6: Add the tool**

In `pyfa_mcp/server.py`, append a bullet to `INSTRUCTIONS`:
```
- export_to_pyfa writes into the user's own Pyfa. Call it only when the
  user explicitly asks for that; otherwise give them the EFT text.
```

After the `delete_fit` tool add:
```python
@app.tool()
@_tool
def export_to_pyfa(fit: str, name: str | None = None) -> dict:
    """Write a fit into the user's own Pyfa as a NEW fit. Only when the user explicitly
    asks. Refuses while Pyfa runs; backs up Pyfa's database first; never overwrites
    (a taken name gets " (2)"). Returns the new fit's pyfa: id and the backup path."""
    return store.export_to_pyfa(fit, name)
```

In `packaging/mcp_smoke.py`, add `"export_to_pyfa"` to `TOOLS`:
```python
TOOLS = {"search_items", "list_ships", "item_info", "evaluate_fit", "compare_fits",
         "fit_graph", "graph_options", "conditions_format", "status", "save_fit",
         "list_fits", "get_fit", "delete_fit", "export_to_pyfa"}
```

- [ ] **Step 7: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass (including `test_smoke_over_stdio`).

- [ ] **Step 8: Commit**

```bash
git add pyfa_mcp/pyfadata.py pyfa_mcp/store.py pyfa_mcp/server.py packaging/mcp_smoke.py tests/test_export.py
git commit -m "Export a fit into the user's Pyfa on request: refuse while it runs or on schema drift, back up, never overwrite"
```

---

### Task 4: Drift checks in `status()`

**Files:**
- Create: `pyfa_mcp/drift.py`, `pyfa_mcp/unhandled_effects.json` (generated), `scripts/record_unhandled_effects.py`, `tests/test_drift.py`
- Modify: `pyfa_mcp/evaluate.py`, `pyfa_mcp/server.py`, `tests/conftest.py`, `tests/test_server.py`, `pyproject.toml`

**Interfaces:**
- Consumes: `pyfadata.pyfa_dir()`, `pyfadata.installed()`, `eosboot.pyfa_version()`, `config.savePath` (set by boot).
- Produces:
  - `drift.PYFA_REPO = "pyfa-org/Pyfa"`, `drift.OWN_REPO = "StormDelay/pyfa-ai-assisted"`, `drift.BASELINE: Path`
  - `drift.installed_pyfa_version() -> str | None`
  - `drift.latest_release(repo: str) -> str | None` (cached a day in `<data dir>/release-check.json`; `_fetch_tag(repo)` is the only network call)
  - `drift.unhandled_ids() -> list[int]` (for the baseline script), `drift.new_unhandled_effects() -> list[dict]` `{"id", "name", "items": [<=10 names]}`
  - `drift.effect_warnings(fit) -> list[str]`
  - `drift.report() -> dict` with `pyfa_install {found, data_dir, version}`, `latest_pyfa_release`, `pyfa_mcp_version`, `latest_pyfa_mcp_release`, `unhandled_effects`, `warnings`
  - `status()` = `{pyfa_version, game_client_build, data_dir, **drift.report()}`

- [ ] **Step 1: Keep tests offline**

Append to `tests/conftest.py`:
```python
@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Tests never call GitHub."""
    from pyfa_mcp import drift

    def no_network(repo):
        raise OSError("no network in tests")

    monkeypatch.setattr(drift, "_fetch_tag", no_network)
```

- [ ] **Step 2: Write the failing tests `tests/test_drift.py`**

```python
import json

import pytest

from pyfa_mcp import drift, eosboot, evaluate


@pytest.fixture
def fresh_cache(booted):
    cache = booted / "release-check.json"
    cache.unlink(missing_ok=True)
    yield cache
    cache.unlink(missing_ok=True)


def _install(home, tmp_path, version):
    """A Pyfa install at tmp_path, named in a Pyfa log in home, as Pyfa logs it."""
    app = tmp_path / "Program Files" / "pyfa" / "app"
    app.mkdir(parents=True)
    (app / "version.yml").write_text(f"version: {version}\n", encoding="utf-8")
    (home / "pyfa-2026-10-02.log").write_text(
        "[2026-10-02 12:46:10.387761] INFO: __main__: Starting Pyfa\n"
        "[2026-10-02 12:46:10.402578] INFO: eos.db: Gamedata connection: "
        f"sqlite:///{app / 'eve.db'}?check_same_thread=False\n", encoding="utf-8")


def test_latest_release_is_cached_for_a_day(fresh_cache, monkeypatch):
    calls = []
    monkeypatch.setattr(drift, "_fetch_tag", lambda repo: calls.append(repo) or "v9.9.9")
    assert drift.latest_release(drift.PYFA_REPO) == "v9.9.9"
    assert drift.latest_release(drift.PYFA_REPO) == "v9.9.9"
    assert calls == [drift.PYFA_REPO]


def test_offline_is_quiet(fresh_cache):
    assert drift.latest_release(drift.PYFA_REPO) is None


def test_offline_keeps_the_last_answer(fresh_cache):
    fresh_cache.write_text(json.dumps({drift.PYFA_REPO: {"tag": "v2.70.0", "checked": 0}}))
    assert drift.latest_release(drift.PYFA_REPO) == "v2.70.0"


def test_installed_pyfa_version_from_its_log(pyfa_home, tmp_path):
    assert drift.installed_pyfa_version() is None
    _install(pyfa_home, tmp_path, "v2.68.0")
    assert drift.installed_pyfa_version() == "v2.68.0"


def test_report_warns_about_an_old_user_pyfa(pyfa_home, tmp_path, monkeypatch):
    _install(pyfa_home, tmp_path, "v2.68.0")
    monkeypatch.setattr(drift, "latest_release",
                        lambda repo: "v2.70.0" if repo == drift.PYFA_REPO else None)
    report = drift.report()
    assert report["pyfa_install"] == {"found": True, "data_dir": str(pyfa_home),
                                      "version": "v2.68.0"}
    text = " ".join(report["warnings"])
    assert "v2.68.0" in text and "update Pyfa" in text


def test_report_is_quiet_when_everything_matches(pyfa_home, tmp_path, monkeypatch):
    _install(pyfa_home, tmp_path, eosboot.pyfa_version())
    monkeypatch.setattr(drift, "latest_release", lambda repo: None)
    assert drift.report()["warnings"] == []


def test_report_without_pyfa(booted, monkeypatch):
    monkeypatch.setattr(drift, "latest_release", lambda repo: None)
    report = drift.report()
    assert report["pyfa_install"]["found"] is False
    assert report["warnings"] == []


def test_report_offline_is_quiet(booted, fresh_cache):
    assert drift.report()["warnings"] == []


def test_newer_pyfa_mcp_release(booted, monkeypatch):
    monkeypatch.setattr(drift, "_own_version", lambda: "0.1.0")
    monkeypatch.setattr(drift, "latest_release",
                        lambda repo: "v0.2.0+pyfa2.69.0" if repo == drift.OWN_REPO else None)
    assert any("pyfa-mcp v0.2.0+pyfa2.69.0" in w for w in drift.report()["warnings"])


def test_effect_coverage_is_clean(booted):
    """The Pyfa-bump gate: every effect eos lacks is in the reviewed baseline."""
    assert drift.new_unhandled_effects() == []


def test_evaluation_warns_about_unhandled_effects(booted, zealot_eft, monkeypatch, no_fits_left):
    from service.market import Market
    zealot = Market.getInstance().getItem("Zealot")
    monkeypatch.setattr(drift, "_unhandled_by_type", lambda: {zealot.ID: ["someEffect"]})
    warnings = evaluate.evaluate(zealot_eft, None)["warnings"]
    assert any("Zealot" in w and "someEffect" in w for w in warnings)
```

- [ ] **Step 3: Run to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_drift.py -q`
Expected: collection error, `ImportError: cannot import name 'drift'` (every test errors in the `_offline` fixture too).

- [ ] **Step 4: Write `pyfa_mcp/drift.py`**

```python
"""Is the user's Pyfa the one we compute with, is either out of date, and
does eos handle every effect the game data uses?

status() reports all of it; evaluations warn about items whose effects eos
does not compute. Network calls (GitHub's latest-release API) happen only
from status(), at most daily per repo, and fail quietly.
"""
from __future__ import annotations

import functools
import itertools
import json
import re
import time
from pathlib import Path

import yaml

from pyfa_mcp import eosboot, pyfadata

PYFA_REPO = "pyfa-org/Pyfa"
OWN_REPO = "StormDelay/pyfa-ai-assisted"
BASELINE = Path(__file__).with_name("unhandled_effects.json")
_DAY = 24 * 3600
_GAMEDATA_LINE = re.compile(r"Gamedata connection: sqlite:///(.+?)[\\/]eve\.db")


# --- versions ----------------------------------------------------------------

def installed_pyfa_version() -> str | None:
    """From the install named in Pyfa's newest log: Pyfa logs its eve.db path at startup."""
    # ponytail: a portable Pyfa (saveInRoot) logs next to itself, not in ~/.pyfa: unknown.
    logs = sorted(pyfadata.pyfa_dir().glob("pyfa*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for log in logs[:3]:
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in itertools.islice(f, 50):
                match = _GAMEDATA_LINE.search(line)
                if match:
                    try:
                        text = (Path(match.group(1)) / "version.yml").read_text(encoding="utf-8")
                        return yaml.safe_load(text)["version"]
                    except (OSError, KeyError, TypeError, yaml.YAMLError):
                        return None
    return None


def _fetch_tag(repo: str) -> str | None:
    """The latest non-prerelease tag; None if the repo has no release (404)."""
    import requests
    reply = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                         headers={"Accept": "application/vnd.github+json"}, timeout=5)
    return reply.json().get("tag_name") if reply.ok else None


def latest_release(repo: str) -> str | None:
    import config
    cache_file = Path(config.savePath) / "release-check.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    entry = cache.get(repo)
    if entry and time.time() - entry["checked"] < _DAY:
        return entry["tag"]
    try:
        tag = _fetch_tag(repo)
    except Exception:  # offline, DNS, TLS, bad JSON: say nothing, keep the last answer
        return entry["tag"] if entry else None
    cache[repo] = {"tag": tag, "checked": time.time()}
    cache_file.write_text(json.dumps(cache), encoding="utf-8")
    return tag


def _own_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("pyfa-mcp")
    except PackageNotFoundError:
        return None


def _newer(a: str | None, b: str | None) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        return bool(a and b) and Version(a) > Version(b)
    except InvalidVersion:
        return False


# --- effect coverage ---------------------------------------------------------

def _handled() -> set[int]:
    import eos.effects
    return {int(name[6:]) for name in dir(eos.effects)
            if name.startswith("Effect") and name[6:].isdigit()}


@functools.cache
def _published_effects() -> tuple:
    """(typeID, typeName, effectID, effectName) for every effect of a published item."""
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        return tuple(connection.exec_driver_sql(
            "SELECT t.typeID, t.typeName, e.effectID, e.effectName FROM dgmtypeeffects te "
            "JOIN invtypes t ON t.typeID = te.typeID "
            "JOIN dgmeffects e ON e.effectID = te.effectID "
            "WHERE t.published = 1").fetchall())


def unhandled_ids() -> list[int]:
    handled = _handled()
    return sorted({row[2] for row in _published_effects() if row[2] not in handled})


@functools.cache
def _new_rows() -> tuple:
    known = _handled() | set(json.loads(BASELINE.read_text(encoding="utf-8")))
    return tuple(row for row in _published_effects() if row[2] not in known)


def new_unhandled_effects() -> list[dict]:
    effects: dict[int, dict] = {}
    for _, type_name, effect_id, effect_name in _new_rows():
        entry = effects.setdefault(effect_id, {"id": effect_id, "name": effect_name, "items": []})
        if len(entry["items"]) < 10:
            entry["items"].append(type_name)
    return sorted(effects.values(), key=lambda e: e["id"])


def _unhandled_by_type() -> dict[int, list[str]]:
    by_type: dict[int, list[str]] = {}
    for type_id, _, _, effect_name in _new_rows():
        by_type.setdefault(type_id, []).append(effect_name)
    return by_type


def effect_warnings(fit) -> list[str]:
    by_type = _unhandled_by_type()
    if not by_type:
        return []
    items = [fit.ship.item]
    for module in fit.modules:
        if not module.isEmpty:
            items += [module.item, module.charge]
    items += [x.item for x in (*fit.drones, *fit.fighters, *fit.implants, *fit.boosters)]
    return [f"{item.name}: Pyfa {eosboot.pyfa_version()} does not compute its effect "
            f"{', '.join(by_type[item.ID])}; these numbers leave it out"
            for item in dict.fromkeys(i for i in items if i is not None)
            if item.ID in by_type]


# --- the report --------------------------------------------------------------

def report() -> dict:
    pinned = eosboot.pyfa_version()
    found = pyfadata.installed()
    yours = installed_pyfa_version() if found else None
    latest = latest_release(PYFA_REPO)
    ours, ours_latest = _own_version(), latest_release(OWN_REPO)
    effects = new_unhandled_effects()

    warnings = []
    if yours and yours != pinned:
        warnings.append(f"The user's Pyfa is {yours}; pyfa-mcp computes with Pyfa {pinned}, "
                        "so numbers can differ from what their Pyfa shows.")
    if _newer(latest, yours):
        warnings.append(f"Pyfa {latest} is out and the user runs {yours}: "
                        "advise them to update Pyfa.")
    if _newer(ours_latest, ours):
        warnings.append(f"pyfa-mcp {ours_latest} is out (this is {ours}): "
                        "advise the user to update pyfa-mcp.")
    for effect in effects:
        warnings.append(f"Pyfa {pinned} does not compute effect {effect['name']} "
                        f"({effect['id']}), used by {', '.join(effect['items'])}; "
                        "stats of fits with these items leave it out.")
    return {"pyfa_install": {"found": found, "data_dir": str(pyfadata.pyfa_dir()),
                             "version": yours},
            "latest_pyfa_release": latest,
            "pyfa_mcp_version": ours, "latest_pyfa_mcp_release": ours_latest,
            "unhandled_effects": effects,
            "warnings": warnings}
```

- [ ] **Step 5: Write `scripts/record_unhandled_effects.py` and generate the baseline**

```python
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
```

Run: `.venv/Scripts/python scripts/record_unhandled_effects.py`
Expected: `615 unhandled effects recorded in ...unhandled_effects.json` (615 at v2.69.0; a different count means eve.db or the pin changed: stop and ask).

In `pyproject.toml`, after the `[tool.setuptools.packages.find]` section, add:
```toml
[tool.setuptools.package-data]
pyfa_mcp = ["unhandled_effects.json"]
```

- [ ] **Step 6: Warn in evaluations**

In `pyfa_mcp/evaluate.py`, change the import to:
```python
from pyfa_mcp import conditions, drift, eft, eosboot, stats, store
```
and replace `_evaluate_parsed`:
```python
def _evaluate_parsed(ref: str, cond) -> dict:
    with Scratch() as scratch:
        fit = scratch.add_fit(ref)
        applied = conditions.apply(fit, cond, scratch.add_fit)
        result = stats.fit_stats(fit, conditions.spool_of(cond))
        name, ship = fit.name, fit.ship.item.name
        effect_warnings = drift.effect_warnings(fit)
    return {"fit": name, "ship": ship, "applied": applied,
            "warnings": warnings_for(result) + effect_warnings, **result}
```

- [ ] **Step 7: Report in `status()`**

In `pyfa_mcp/server.py`, add `drift` to the import line:
```python
from pyfa_mcp import (catalog, conditions, drift, eft, eosboot, evaluate, graphs, pyfadata,
                      store)
```
and replace the `status` tool:
```python
@app.tool()
@_tool
def status() -> dict:
    """Versions (pyfa-mcp's Pyfa, the user's Pyfa, latest releases), whether the user's
    Pyfa data was found, and effects Pyfa does not compute. Relay every warning."""
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        meta = dict(connection.exec_driver_sql(
            "SELECT field_name, field_value FROM metadata").fetchall())
    return {"pyfa_version": eosboot.pyfa_version(),
            "game_client_build": meta.get("client_build"),
            "data_dir": str(eosboot.boot(_data_dir)),
            **drift.report()}
```

In `tests/test_server.py`, in `test_tools_return_data`, after the `status()` assertion add:
```python
    assert server.status()["pyfa_install"]["found"] is False
```

- [ ] **Step 8: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add pyfa_mcp/drift.py pyfa_mcp/unhandled_effects.json scripts/record_unhandled_effects.py pyfa_mcp/evaluate.py pyfa_mcp/server.py pyproject.toml tests/conftest.py tests/test_drift.py tests/test_server.py
git commit -m "Report Pyfa version drift and effects eos does not compute"
```

- [ ] **Step 10: HUMAN CHECKPOINT — against the real Pyfa**

With the server registered in Claude Code from this checkout, ask the user to try, in a session: `status()` (expect `pyfa_install.found: true`, `version: v2.69.0`, no warnings); `list_fits(source="pyfa", ship="<a hull they fly>")`; `evaluate_fit("pyfa:<an id from that list>")` and compare its EHP/DPS with the same fit in the Pyfa GUI; `conditions_format()` lists their own profiles (the two damage profiles in their Pyfa). `export_to_pyfa` writes their real database: only if they want to, with Pyfa closed, and then check the new fit appears in Pyfa and the `saveddata_pyfa-mcp-backup_*.db` file exists.

---

## Self-review notes

- Spec coverage: snapshot read through a second session, refreshed on mtime → Task 1; `list_fits(source="pyfa")` read-only → Task 1; the user's custom profiles, "no Pyfa install found" without one → Tasks 1–2; `export_to_pyfa` (running check, migration-version refusal saying to export EFT, timestamped backup, insert only, ` (2)`) → Task 3; drift check 1 (installed vs latest vs pinned, daily fetch, quiet offline, newer pyfa-mcp) and check 2 (effect coverage minus a recorded baseline, warnings on evaluations touching them) → Task 4; `status()` → Task 4.
- Deliberate cuts: an older user schema is migrated on the snapshot (the spec only says "snapshot"; without it a Pyfa that is one schema behind could not be read); per-evaluation effect warnings only appear when the baseline is behind eos (never in a released build, whose gate requires it clean), so they cost one cached query; the Pyfa-running check only sees `pyfa.exe` (marked `ponytail:`).
- Types: `pyfa:` ids are strings everywhere (`list_fits`, `get_fit`, `export_to_pyfa`); server fit ids stay ints.
