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
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime
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
            # Pyfa is not running, so a plain copy is exact (as Pyfa's own
            # migration backups are): restoring is a rename.
            shutil.copy2(db, backup)
            session.add(fit)
            session.commit()
            return {"id": f"pyfa:{fit.ID}", "name": fit.name,
                    "ship": fit.ship.item.name, "backup": str(backup)}
    finally:
        engine.dispose()
