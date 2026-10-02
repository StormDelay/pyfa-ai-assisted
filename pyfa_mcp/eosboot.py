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
        purge_temp_profiles()

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


def purge_temp_profiles() -> None:
    """Delete custom damage/target profiles made for an evaluation.

    A fit refers to a custom profile through a relationship, so Pyfa saves
    the profile along with the (temporary) fit; they carry TEMP_NOTE as name.
    """
    import eos.db
    from eos.saveddata.damagePattern import DamagePattern
    from eos.saveddata.targetProfile import TargetProfile

    session = eos.db.saveddata_session
    for cls in (DamagePattern, TargetProfile):
        for profile in session.query(cls).filter(cls.rawName == TEMP_NOTE).all():
            session.delete(profile)
    eos.db.commit()
