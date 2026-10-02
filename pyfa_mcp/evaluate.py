"""Evaluate fits under conditions, on temporary copies that never outlive a call."""
from __future__ import annotations

from pyfa_mcp import conditions, eft, eosboot, stats, store


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
        eosboot.purge_temp_profiles()
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
