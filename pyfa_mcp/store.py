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
    fit = eft.import_fit(resolve_eft(ref), name=name)
    if fit.dropped_modules:
        from service.fit import Fit
        Fit.deleteFit(fit.ID)
        left_out = "; ".join(f"{d.name} ({d.reason})" for d in fit.dropped_modules)
        raise StoreError(f"not saved: Pyfa left out {left_out}; fix the fit first")
    return _entry(fit)


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
