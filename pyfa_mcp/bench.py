"""Many small changes to one fit, measured in memory.

A Bench imports a fit once (conditions applied) into a temporary fit, then
edits that live eos fit in place: replace a module, swap an implant,
project a module. A measurement is one recalc, ~2-3 ms on a linked capital
once the skill table knows its items, against ~75 ms for an import/evaluate/delete
round trip. Callers re-run
every fit they return through evaluate.evaluate; bench numbers only rank.
"""
from __future__ import annotations

import contextlib
import functools
import json
import os
from pathlib import Path
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


# --- the skill table ---------------------------------------------------------
# Item key ("<type id>", "<type id>/<charge id>", "p..." when projected) -> type IDs
# of the skills whose effects touch it. A recalc with every skill fills it in from
# what eos records as each attribute's modifiers; skills bonus by group as well as
# by required skill, so only a recalc can say. No skill effect checks its own level
# and eos records a modifier that changes nothing, so an entry holds for every
# character. The table is kept in the server's data dir, per Pyfa version and client build.

_SCHEMA = 1
table_dir: Path | None = None  # pool workers: the server's data dir
_touches: dict | None = None
_new: dict = {}  # learned since the last save


@functools.cache
def _table_path() -> Path:
    from pyfa_mcp import catalog, eosboot
    base = table_dir or eosboot.booted_dir()
    return base / (f"skill_touches-v{_SCHEMA}-{eosboot.pyfa_version()}-"
                   f"{catalog.client_build()}.json")


def _read_table() -> dict:
    try:
        data = json.loads(_table_path().read_text(encoding="utf-8"))
        return {k: frozenset(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}  # missing or damaged: learned again


def _table() -> dict:
    global _touches
    if _touches is None:
        _touches = _read_table()
    return _touches


def save_table() -> None:
    """Merge what this process learned into the file. Processes racing may drop
    each other's entries; those are only learned again."""
    if not _new:
        return
    merged = {**_read_table(), **_new}
    path = _table_path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({k: sorted(v) for k, v in merged.items()}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        return  # kept for this process only, tried again at the next save
    _new.clear()
    _table().update(merged)


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
            self._filter_skills()
        except BaseException:
            self._stack.close()
            raise
        self.spool = conditions.spool_of(self._cond)
        self._pending: list = []  # (edit, undo) the last trial left on the fit
        return self

    def __exit__(self, *exc):
        self._settle()
        return self._stack.__exit__(*exc)

    # --- places ------------------------------------------------------------

    def module_places(self) -> list[tuple]:
        return [("module", p) for p in range(len(self.fit.modules))]

    def rack(self, where: tuple) -> str:
        return SLOT_LABELS.get(self.fit.modules[where[1]].slot, "other")

    def _holders(self, kind: str):
        return self.fit.implants if kind == "implant" else self.fit.boosters

    def occupant(self, where: tuple) -> tuple | None:
        self._settle()
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
        self._settle()
        return self._apply(edits)

    def _apply(self, edits) -> list:
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

    # --- skills ------------------------------------------------------------

    def _filter_skills(self) -> None:
        """Pyfa runs all ~500 skills' effects on every recalc (~80% of one), yet a few
        dozen touch any one fit. The skill table (below) says which skills touch each
        item; a recalc runs only those for what is on the fit."""
        char = self.fit.character
        self._touching: list | None = None  # None: run every skill

        def calc(inner, fit, runTime, forceProjected=False):
            if forceProjected or fit is not self.fit or self._touching is None:
                return inner(fit, runTime, forceProjected)
            for skill in self._touching:
                fit.register(skill)
                skill.calculateModifiedAttributes(fit, runTime)

        def clear(inner):
            # Skill.clear only undoes Skill.suppress(), which Pyfa never calls; the
            # skills about to run are cleared all the same.
            if self._touching is None:
                return inner()
            for stuff in (*self._touching, *char.implants):
                stuff.clear()

        self._override(char, "calculateModifiedAttributes", calc)
        self._override(char, "clear", clear)
        self._stack.callback(save_table)

    def _override(self, obj, name: str, method) -> None:
        """obj.name = method(previous obj.name, ...) while the bench is open. The object
        may be shared (Pyfa's All 5 character) with other fits and benches: method
        hands what is not its own to the previous one."""
        before = obj.__dict__.get(name)
        inner = before or getattr(type(obj), name).__get__(obj)
        setattr(obj, name, functools.partial(method, inner))

        def restore():
            if before is None:
                obj.__dict__.pop(name, None)
            else:
                setattr(obj, name, before)
        self._stack.callback(restore)

    def _calculated(self):
        """(table key, item) for everything on the fit that skills can modify."""
        fit = self.fit
        local = [[fit.ship], [fit.mode] if fit.mode is not None else [],
                 [m for m in fit.modules if not m.isEmpty], fit.drones, fit.fighters,
                 fit.appliedImplants, fit.boosters]
        projected = [[m for m in fit.projectedModules if not m.isEmpty],
                     fit.projectedDrones, fit.projectedFighters]
        for prefix, groups in (("", local), ("p", projected)):
            for group in groups:
                for h in group:
                    charge = getattr(h, "charge", None)
                    yield (f"{prefix}{h.item.ID}" + (f"/{charge.ID}" if charge else ""), h)

    def _calc(self) -> None:
        """FitService.recalc, but with one clear: it clears the fit, then
        calculateModifiedAttributes clears it again."""
        from service.fit import Fit as FitService
        self.fit.factorReload = FitService.getInstance().serviceFittingOptions[
            "useGlobalForceReload"]
        self.fit.calculated = False
        self.fit.calculateModifiedAttributes()

    def _recalc(self) -> None:
        from eos.saveddata.character import Skill

        table = _table()
        on_fit = list(self._calculated())
        if all(key in table for key, _ in on_fit):
            needed = set().union(*(table[key] for key, _ in on_fit))
            self._touching = [s for s in self.fit.character.skills if s.itemID in needed]
            self._calc()
            return
        self._touching = None
        self._calc()
        for key, h in on_fit:
            touched = set()
            for attrs in (getattr(h, "itemModifiedAttributes", None),
                          getattr(h, "chargeModifiedAttributes", None)):
                for name in attrs.iterAfflictions() if attrs is not None else ():
                    for afflictions in attrs.getAfflictions(name).values():
                        touched.update(a[0].itemID for a in afflictions
                                       if isinstance(a[0], Skill))
            table[key] = _new[key] = frozenset(touched)

    # --- measuring ---------------------------------------------------------

    def measure(self, keys) -> dict:
        self._settle()
        self._recalc()
        return stats.read(self.fit, list(keys), self.spool)

    def problems(self) -> list[str]:
        """Validity problems of the fit as last measured, plus activation limits."""
        self._settle()
        return self._problems()

    def _problems(self) -> list[str]:
        from eos.const import FittingModuleState

        found = list(stats._validity(self.fit)["problems"])
        for mod in self.fit.modules:
            if (not mod.isEmpty and mod.state >= FittingModuleState.ACTIVE
                    and mod.canHaveState(mod.state) is not True):
                found.append(f"{mod.item.name} cannot be {mod.state.name.lower()} "
                             "alongside the others (group activation limit)")
        return found

    def trial(self, edits, keys) -> Trial:
        """The fit with `edits` measured. The edits stay on the fit: the next trial
        changes only the places it wants otherwise (a search moves one or two at a
        time, of ~15), and every other method puts the fit back first."""
        try:
            self._move_to(list(edits))
        except ValueError as exc:  # BenchError, or eos refusing an unsuitable item
            return Trial(None, [str(exc)], str(exc))
        try:
            self._recalc()
            return Trial(stats.read(self.fit, list(keys), self.spool), self._problems())
        except Exception as exc:  # one candidate Pyfa chokes on must not sink a search
            self._settle()
            return Trial(None, [], f"{type(exc).__name__}: {exc}")

    def _settle(self) -> None:
        """Back to the fit as opened."""
        pending, self._pending = self._pending, []
        self.revert([undo for _, undo in pending])

    def _move_to(self, edits: list) -> None:
        # Module places undo independently; implants and boosters undo by list
        # position and projected modules by command, so those start from the fit as opened.
        places = {e.where for e in edits}
        if (len(places) < len(edits)
                or any(e.where[0] != "module" for e in [*edits, *(e for e, _ in self._pending)])):
            self._settle()
        wanted = {e.where: e for e in edits}
        keep = [(e, u) for e, u in self._pending if wanted.get(e.where) == e]
        self.revert([u for e, u in self._pending if wanted.get(e.where) != e])
        self._pending = keep
        kept = {e.where for e, _ in keep}
        todo = [e for e in edits if e.where not in kept]
        try:
            undo = self._apply(todo)
        except BaseException:
            self._settle()
            raise
        self._pending += list(zip(todo, undo))

    # --- export ------------------------------------------------------------

    def eft(self) -> str:
        self._settle()
        return eft.export_fit(self.fit)

    def module_states(self) -> list[dict]:
        """module_states conditions that give this bench's states to its EFT."""
        from eos.const import FittingModuleState

        self._settle()
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
