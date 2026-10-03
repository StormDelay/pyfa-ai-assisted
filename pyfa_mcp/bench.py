"""Many small changes to one fit, measured in memory.

A Bench imports a fit once (conditions applied) into a temporary fit, then
edits that live eos fit in place: replace a module, swap an implant,
project a module. A measurement is one recalc, ~10 ms on a linked capital
against ~75 ms for an import/evaluate/delete round trip. Callers re-run
every fit they return through evaluate.evaluate; bench numbers only rank.
"""
from __future__ import annotations

import contextlib
import json
from typing import NamedTuple

from pyfa_mcp import conditions, eft, evaluate, stats

SLOT_LABELS = {1: "low", 2: "mid", 3: "high", 4: "rig", 5: "subsystem"}  # FittingSlot values


class BenchError(ValueError):
    """A change Pyfa will not make."""


class Edit(NamedTuple):
    """One change. where: ("module", position) | ("implant", slot) |
    ("booster", slot) | ("projected", None). item_id None empties the place."""
    where: tuple
    item_id: int | None
    charge_id: int | None = None
    state: str | None = None  # module state; None: the highest valid, up to active


class Trial(NamedTuple):
    values: dict | None  # stat key -> value; None when Pyfa failed
    problems: list       # validity problems after the change; [] = valid
    error: str | None = None


def _item(type_id: int):
    import eos.db
    return eos.db.getItem(type_id)


class Bench:
    def __init__(self, ref: str, raw_conditions: dict | None = None):
        self._ref = ref
        self._cond = conditions.parse(raw_conditions)

    def __enter__(self):
        import eos.db
        from eos.const import ImplantLocation
        self._stack = contextlib.ExitStack()
        try:
            scratch = self._stack.enter_context(evaluate.Scratch())
            self.fit = scratch.add_fit(self._ref)
            # An EFT without implant lines leaves them on the character, where
            # edits to fit.implants would not count; the EFT is the whole truth.
            self.fit.implantLocation = ImplantLocation.FIT
            self.applied = conditions.apply(self.fit, self._cond, scratch.add_fit)
            # No flush while edits are live: a module taken out and put back
            # must never have been deleted from the session in between.
            self._stack.enter_context(eos.db.saveddata_session.no_autoflush)
            self.fit.fill()
        except BaseException:
            self._stack.close()
            raise
        self.spool = conditions.spool_of(self._cond)
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    # --- places ------------------------------------------------------------

    def module_places(self) -> list[tuple]:
        return [("module", p) for p in range(len(self.fit.modules))]

    def rack(self, where: tuple) -> str:
        return SLOT_LABELS.get(self.fit.modules[where[1]].slot, "other")

    def _holders(self, kind: str):
        return self.fit.implants if kind == "implant" else self.fit.boosters

    def occupant(self, where: tuple) -> tuple | None:
        kind, at = where
        if kind == "module":
            mod = self.fit.modules[at]
            if mod.isEmpty:
                return None
            return (mod.item.ID, mod.charge.ID if mod.charge else None, mod.state.name.lower())
        holder = next((x for x in self._holders(kind) if x.slot == at), None)
        return None if holder is None else (holder.item.ID, None, None)

    def name_of(self, type_id: int) -> str:
        return _item(type_id).name

    # --- edits -------------------------------------------------------------

    def apply(self, edits) -> list:
        undo: list = []
        try:
            for edit in edits:
                undo.append(self._apply_one(edit))
        except BaseException:
            self.revert(undo)
            raise
        return undo

    def revert(self, undo: list) -> None:
        for step in reversed(undo):
            step()

    def _apply_one(self, edit: Edit):
        kind, at = edit.where
        if kind == "module":
            return self._set_module(at, edit)
        if kind in ("implant", "booster"):
            return self._set_holder(kind, at, edit.item_id)
        if kind == "projected":
            return self._project(edit)
        raise BenchError(f"unknown place '{kind}'")

    def _set_module(self, pos: int, edit: Edit):
        from eos.saveddata.module import Module

        modules = self.fit.modules
        old = modules[pos]

        def free():
            modules.free(pos)
            modules[pos].owner = self.fit  # the dummy needs an owner like any module

        def back():
            if old.isEmpty:
                free()
            else:
                modules.replace(pos, old)

        if edit.item_id is None:
            free()
            return back
        mod = Module(_item(edit.item_id))
        if mod.slot != old.slot:
            raise BenchError(f"{mod.item.name} does not go in a {self.rack(('module', pos))} slot")
        mod.owner = self.fit
        if edit.charge_id is not None:
            mod.charge = _item(edit.charge_id)
        mod.state = mod.getMaxState(conditions._state(edit.state or "active"))
        modules.replace(pos, mod)
        if modules[pos] is not mod:
            raise BenchError(f"Pyfa refused {mod.item.name} in that slot")
        return back

    def _set_holder(self, kind: str, slot: int, item_id: int | None):
        from eos.saveddata.booster import Booster
        from eos.saveddata.implant import Implant

        holders = self._holders(kind)
        old = next((x for x in holders if x.slot == slot), None)
        at = holders.index(old) if old is not None else None
        if old is not None:
            holders.remove(old)
        new = None
        if item_id is not None:
            new = (Implant if kind == "implant" else Booster)(_item(item_id))
            if new.slot != slot:
                if old is not None:
                    holders.insert(at, old)
                raise BenchError(f"{new.item.name} goes in {kind} slot {new.slot}, not {slot}")
            holders.append(new)

        def back():
            if new is not None and new in holders:
                holders.remove(new)
            if old is not None:
                holders.insert(at, old)
        return back

    def _project(self, edit: Edit):
        from gui.fitCommands.calc.module.projectedAdd import CalcAddProjectedModuleCommand
        from gui.fitCommands.helpers import ModuleInfo

        info = ModuleInfo(itemID=edit.item_id, state=conditions._state(edit.state or "active"))
        command = CalcAddProjectedModuleCommand(self.fit.ID, info, recalc=False)
        if not command.Do():
            raise BenchError(f"Pyfa refused to project {self.name_of(edit.item_id)}")
        return command.Undo

    # --- measuring ---------------------------------------------------------

    def measure(self, keys) -> dict:
        from service.fit import Fit as FitService
        FitService.getInstance().recalc(self.fit)
        return stats.read(self.fit, list(keys), self.spool)

    def problems(self) -> list[str]:
        """Validity problems of the fit as last measured, plus activation limits."""
        from eos.const import FittingModuleState

        found = list(stats._validity(self.fit)["problems"])
        for mod in self.fit.modules:
            if (not mod.isEmpty and mod.state >= FittingModuleState.ACTIVE
                    and mod.canHaveState(mod.state) is not True):
                found.append(f"{mod.item.name} cannot be {mod.state.name.lower()} "
                             "alongside the others (group activation limit)")
        return found

    def trial(self, edits, keys) -> Trial:
        try:
            undo = self.apply(edits)
        except ValueError as exc:  # BenchError, or eos refusing an unsuitable item
            return Trial(None, [str(exc)], str(exc))
        try:
            return Trial(self.measure(keys), self.problems())
        except Exception as exc:  # one candidate Pyfa chokes on must not sink a search
            return Trial(None, [], f"{type(exc).__name__}: {exc}")
        finally:
            self.revert(undo)

    # --- export ------------------------------------------------------------

    def eft(self) -> str:
        return eft.export_fit(self.fit)

    def module_states(self) -> list[dict]:
        """module_states conditions that give this bench's states to its EFT."""
        from eos.const import FittingModuleState

        counts: dict[tuple, int] = {}
        for mod in self.fit.modules:
            if mod.isEmpty or mod.state == FittingModuleState.OFFLINE:
                continue  # /OFFLINE travels in the EFT
            if mod.state != mod.getMaxState(FittingModuleState.ACTIVE):
                key = (mod.item.name, mod.state.name.lower())
                counts[key] = counts.get(key, 0) + 1
        return [{"module": n, "state": s, "count": c} for (n, s), c in counts.items()]


def merge_conditions(raw: dict | None, extra: dict | None) -> dict:
    merged = dict(raw or {})
    for key, value in (extra or {}).items():
        merged[key] = [*merged.get(key, []), *value]
    return merged


def run_trials(ref: str, raw_conditions: dict | None, keys: list[str], trials: list) -> list:
    """Measure each (edits, extra conditions) trial; one bench per distinct extra."""
    results: list = [None] * len(trials)
    groups: dict[str, list[int]] = {}
    for index, (_, extra) in enumerate(trials):
        groups.setdefault(json.dumps(extra, sort_keys=True), []).append(index)
    for key, indexes in groups.items():
        extra = json.loads(key)
        try:
            with Bench(ref, merge_conditions(raw_conditions, extra)) as b:
                for index in indexes:
                    results[index] = b.trial(trials[index][0], keys)
        except Exception as exc:
            if extra is None:
                raise  # the fit itself is wrong: the caller's error
            for index in indexes:
                if results[index] is None:
                    results[index] = Trial(None, [], f"{type(exc).__name__}: {exc}")
    return results
