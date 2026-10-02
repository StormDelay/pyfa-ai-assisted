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
