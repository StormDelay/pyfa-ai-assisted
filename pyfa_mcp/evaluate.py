"""Evaluate fits under conditions, on temporary copies that never outlive a call."""
from __future__ import annotations

import json

from pyfa_mcp import catalog, conditions, drift, eft, eosboot, notes, prices, stats, store

# Keys of an evaluate result that are not stats.
META_KEYS = ("fit", "ship", "applied", "warnings", "notes", "hull_bonuses", "price_source")


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
        import eos.db
        from service.fit import Fit
        # Flush first: an item added by a condition (a projected drone) is
        # still pending, and deleting its fit would insert it fit-less.
        eos.db.commit()
        # Projected and command fits first: they hang off the main fit.
        for fit_id in reversed(self._ids):
            Fit.deleteFit(fit_id)
        self._ids.clear()
        eosboot.purge_temp_profiles()
        return False


def warnings_for(result: dict) -> list[str]:
    problems = result["validity"]["problems"]
    return ["fit is not valid: " + "; ".join(problems)] if problems else []


def _evaluate_parsed(ref: str, cond) -> dict:
    with Scratch() as scratch:
        fit = scratch.add_fit(ref)
        applied = conditions.apply(fit, cond, scratch.add_fit)
        result = stats.fit_stats(fit, conditions.spool_of(cond))
        name, ship = fit.name, fit.ship.item.name
        effect_warnings = drift.effect_warnings(fit)
        fit_notes = notes.for_fit(fit)
        bonuses = catalog.hull_bonuses(fit.ship.item)
        try:
            price = prices.fit_price(fit)
        except Exception as exc:  # a price problem never fails an evaluation
            price = {"total": None, "error": f"{type(exc).__name__}: {exc}"}
    return {"fit": name, "ship": ship, "applied": applied,
            "warnings": warnings_for(result) + effect_warnings + store.pyfa_warnings(ref),
            "notes": fit_notes, "hull_bonuses": bonuses, **result,
            "price": price, "price_source": prices.price_source()}


def evaluate(ref: str, raw_conditions: dict | None) -> dict:
    return _evaluate_parsed(ref, conditions.parse(raw_conditions))


def _label(ref: str) -> str:
    if eft.looks_like_eft(ref):
        header = ref.strip().splitlines()[0].strip("[] ")
        return header.split(",", 1)[-1].strip() or header
    return ref.strip()


def compare(refs: list[str], raw_conditions: dict | None,
            keys: list[str] | None, variants: list[dict] | None = None) -> dict:
    if variants is not None and (not variants
                                 or not all(isinstance(v, dict) for v in variants)):
        raise ValueError("variants: a non-empty list of partial conditions objects, each "
                         "merged over conditions")
    keys = list(keys) if keys else [*stats.DEFAULT_COMPARE, "price.total"]
    applied, rows = None, []
    for index, variant in enumerate(variants or [None]):
        # bad conditions fail the whole call
        cond = conditions.parse({**(raw_conditions or {}), **(variant or {})})
        label = {} if variant is None else {
            "variant": index, "variant_label": json.dumps(variant, sort_keys=True)[:80]}
        for ref in refs:
            try:
                result = _evaluate_parsed(ref, cond)
            except (eft.EftError, store.StoreError, conditions.ConditionsError) as exc:
                rows.append({"fit": _label(ref), **label, "error": str(exc)})
                continue
            except Exception as exc:  # one fit Pyfa chokes on must not sink the table
                rows.append({"fit": _label(ref), **label,
                             "error": f"{type(exc).__name__}: {exc}"})
                continue
            applied = applied or result["applied"]
            flat = stats.flatten({k: v for k, v in result.items() if k not in META_KEYS})
            unknown = [k for k in keys if k not in flat]
            if unknown:
                raise ValueError(f"unknown stat '{unknown[0]}'; stat keys look like "
                                 f"{', '.join(stats.DEFAULT_COMPARE[:3])}")
            partial = ({"price_partial": True, "unpriced": result["price"]["unpriced"]}
                       if result["price"].get("partial") else {})
            rows.append({"fit": result["fit"], "ship": result["ship"], **label,
                         **{k: flat[k] for k in keys}, **partial,
                         "warnings": result["warnings"], "notes": result["notes"]})
    columns = ["fit", *(["variant"] if variants else []), *keys]
    return {"applied": applied, "columns": columns, "rows": rows,
            "price_source": prices.price_source()}
