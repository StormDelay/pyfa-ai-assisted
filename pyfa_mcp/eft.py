"""EFT text in and out of the server's fit database.

Pyfa's EFT importer silently skips two kinds of line:

- an item it cannot find. Every name goes through
  `service.port.eft.fetchItem`, so we wrap it and record each miss; a fit
  with misses is deleted and reported instead of returned.
- a module that does not fit the hull (no free slot or hardpoint, a hull
  restriction). Placement asks `Module.fits`, so we wrap it and record each
  refusal; the fit is kept, and `fit.dropped_modules` says what was left out.
"""
from __future__ import annotations

import contextlib
import difflib
import functools
import re
from typing import NamedTuple

from pyfa_mcp.eosboot import TEMP_NOTE

_HEADER = re.compile(r"^\[[^,\]]+,[^\]]*\]$")
_SLOT_LABELS = {1: "low", 2: "mid", 3: "high", 4: "rig", 5: "subsystem"}


class DroppedModule(NamedTuple):
    name: str
    reason: str


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


def _drop_reason(module, fit) -> str:
    from eos.const import FittingHardpoint

    label = _SLOT_LABELS.get(module.slot)
    if label and fit.getSlotsFree(module.slot) <= 0:
        return f"no free {label} slot"
    if module.hardpoint in (FittingHardpoint.TURRET, FittingHardpoint.MISSILE) \
            and fit.getHardpointsFree(module.hardpoint) <= 0:
        kind = "turret" if module.hardpoint == FittingHardpoint.TURRET else "launcher"
        return f"no free {kind} hardpoint"
    return "cannot be fitted to this ship"


@contextlib.contextmanager
def _recording_drops():
    from eos.saveddata.module import Module

    real = Module.fits
    dropped: dict[int, DroppedModule] = {}

    def fits(self, fit, *args, **kwargs):
        ok = real(self, fit, *args, **kwargs)
        if not ok and not self.isEmpty and id(self) not in dropped:
            dropped[id(self)] = DroppedModule(self.item.name, _drop_reason(self, fit))
        return ok

    Module.fits = fits
    try:
        yield dropped
    finally:
        Module.fits = real


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
    with _recording_misses() as misses, _recording_drops() as dropped:
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
    fit = FitService.getInstance().getFit(fit.ID)
    fit.dropped_modules = list(dropped.values())  # not persisted; lives with the object
    return fit


def export_fit(fit) -> str:
    from service.const import PortEftOptions
    from service.port import Port

    options = {option.value: True for option in PortEftOptions}
    return Port.exportEft(fit, options=options)
