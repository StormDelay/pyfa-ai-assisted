"""Search fit space: what moves a stat, which single change helps, the best fit.

Candidates are measured on a bench (bench.py) through the worker pool; every
fit a tool returns is re-run through evaluate.evaluate, so the agent never
sees a number the evaluator did not produce.
"""
from __future__ import annotations

import contextlib
import difflib
import json
import re
import time
from collections import Counter, OrderedDict
from typing import NamedTuple

from pyfa_mcp import (bench, candidates, conditions, drift, eft, evaluate, pool, pyfadata,
                      stats, store)
from pyfa_mcp.bench import Edit

# Candidate pools and find_modifiers/marginal_swaps trial results; optimize_fit's
# batches are never asked twice and bypass the cache.
_POOLS: OrderedDict = OrderedDict()
_TRIALS: OrderedDict = OrderedDict()
_CACHE_SIZE = 32


def _cached(cache: OrderedDict, key, compute):
    if key in cache:
        cache.move_to_end(key)
        return cache[key]
    value = cache[key] = compute()
    if len(cache) > _CACHE_SIZE:
        cache.popitem(last=False)
    return value


def _numeric(keys) -> None:
    for key in keys:
        if key in stats.NOT_NUMERIC:
            raise ValueError(f"'{key}' is not always a number, so a search cannot rank or "
                             f"compare by it; use {stats.NOT_NUMERIC[key]} instead")


def _zero(delta, base) -> bool:
    return abs(delta) <= 1e-9 * max(1.0, abs(base))


def _ship_named(name: str):
    from service.market import Market
    try:
        item = Market.getInstance().getItem(name.strip())
    except Exception:
        return None
    return item if item is not None and item.category.name == "Ship" else None


def _baseline_eft(fit: str) -> str:
    """EFT for a fit reference, or an empty hull for a ship name."""
    if eft.looks_like_eft(fit):
        return fit
    try:
        return store.resolve_eft(fit)
    except store.StoreError as exc:
        ship = _ship_named(fit)
        if ship is None:
            from pyfa_mcp import catalog
            close = difflib.get_close_matches(
                fit.strip(), [i.name for i in catalog.published_items(categories=("Ship",))],
                n=3, cutoff=0.6)
            raise store.StoreError(
                f"{exc}; nor is it a ship name"
                + (f" (did you mean: {', '.join(close)}?)" if close else "")) from None
        return f"[{ship.name}, {ship.name}]\n"


def _portable(raw: dict | None) -> dict:
    """Conditions a worker can apply: stored and pyfa: fit references become EFT."""
    raw = dict(raw or {})
    for key in ("command", "projected"):
        if isinstance(raw.get(key), list):
            raw[key] = [{**e, "fit": store.resolve_eft(e["fit"])}
                        if isinstance(e, dict) and isinstance(e.get("fit"), str) else e
                        for e in raw[key]]
    return raw


def _own_profiles(raw: dict) -> list:
    """What the user's own Pyfa profiles named in `raw` hold now: a cache key part,
    so an edited profile is measured again."""
    out = []
    for name, kind, builtins, mine in (
            ("damage_profile", "damage profile", conditions._builtin_damage_profiles,
             pyfadata.damage_profiles),
            ("target", "target profile", conditions._builtin_target_profiles,
             pyfadata.target_profiles)):
        value = raw.get(name)
        if isinstance(value, str) and value != "uniform":
            found = conditions._by_name(value, kind, builtins(), mine)
            out.append(found if isinstance(found, dict) else None)
    return out


def _run(ref: str, raw: dict, keys: list[str], trials: list) -> list:
    key = json.dumps([ref, raw, _own_profiles(raw), list(keys), trials], sort_keys=True)
    return _cached(_TRIALS, key, lambda: pool.run(ref, raw, list(keys), trials))


def _pool(fit, sources: set[str], meta: list[str] | None, availability: str | None = None):
    subs = tuple(sorted(m.item.ID for m in fit.modules if not m.isEmpty and m.slot == 5))
    key = (fit.ship.item.ID, subs, json.dumps(meta), tuple(sorted(sources)), availability)
    return _cached(_POOLS, key, lambda: candidates.build(fit, sources, meta, availability))


def _excluded_rows(excluded: list[dict]) -> list[dict]:
    by: dict[tuple, list[str]] = {}
    for e in excluded:
        by.setdefault((e["group"], e["reason"]), []).append(e["name"])
    return [{"group": g, "reason": r, "variants": len(n), "examples": n[:3]}
            for (g, r), n in sorted(by.items())]


def _excluded_counts(excluded: list[dict]) -> dict:
    return dict(sorted(Counter(e["reason"] for e in excluded).items()))


def _flat(result: dict) -> dict:
    return stats.flatten({k: v for k, v in result.items()
                          if k not in ("fit", "ship", "applied", "warnings", "notes")})


def _value(text: str, raw: dict, key: str) -> float:
    return _flat(evaluate.evaluate(text, raw))[key]


def _confirm(ref: str, raw: dict, edits) -> dict:
    """The evaluator's numbers for the bench fit after `edits`, and the conditions
    that reproduce them (EFT has no heat, so module states travel there)."""
    with bench.Bench(ref, raw) as b:
        b.apply(edits)
        text, states = b.eft(), b.module_states()
    cond = {**raw, "module_states": states} if states else dict(raw)
    result = evaluate.evaluate(text, cond)
    return {"eft": text, "flat": _flat(result), "result": result, "conditions": cond}


# --- find_modifiers ----------------------------------------------------------

def _distinct_occupied(b, places) -> list[tuple]:
    seen, out = set(), []
    for where in places:
        occ = b.occupant(where)
        if occ is not None and occ not in seen:
            seen.add(occ)
            out.append((where, b.name_of(occ[0])))
    return out


def _add_trials(b, cands) -> tuple[list, list]:
    """Trials adding each candidate once (swapping it in where its rack is full).
    owners[i] = (candidate index, "active"|"overheated", replaced name or None)."""
    racks: dict[str, list] = {}
    for where in b.module_places():
        racks.setdefault(b.rack(where), []).append(where)
    trials, owners = [], []
    for index, c in enumerate(cands):
        if c.extra is not None or c.edits:
            trials.append((list(c.edits), c.extra))
            owners.append((index, "active", None))
            continue
        places = racks.get(c.slot, [])
        empty = next((w for w in places if b.occupant(w) is None), None)
        if c.slot == "subsystem":  # a Core only replaces a Core
            same = [t for t in _distinct_occupied(b, places)
                    if b.fit.modules[t[0][1]].item.group.name == c.group]
            targets = same or [(w, None) for w in places if b.occupant(w) is None]
        else:
            targets = [(empty, None)] if empty is not None else _distinct_occupied(b, places)
        for state in (("active", "overheated") if c.overheat else ("active",)):
            for where, replaced in targets:
                trials.append(([Edit(where, c.type_id, c.charge_id, state)], None))
                owners.append((index, state, replaced))
    return trials, owners


def _charge_name(type_id: int) -> str:
    import eos.db
    return eos.db.getItem(type_id).name


def _row(c, delta, heat, baseline, replaced, problems, signs) -> dict:
    notes = []
    if c.active:
        notes.append("active module: measured active")
    if replaced:
        notes.append(f"its slots are full: measured replacing {replaced}")
    notes += [f"drawback: {'lowers' if d < 0 else 'raises'} {k}" for k, d in delta.items()
              if signs[k] * d < 0 and not _zero(d, baseline[k])]
    if problems:
        notes.append("on this fit: " + "; ".join(problems[:2]))
    if c.note:
        notes.append(c.note)
    return {"name": c.name, "type_id": c.type_id, "source": c.source, "slot": c.slot,
            "charge": None if c.charge_id is None else _charge_name(c.charge_id),
            "group": c.group, "meta": c.meta, "cpu": c.cpu, "pg": c.pg,
            "calibration": c.calibration, "delta": delta,
            "delta_overheated": None if heat is None else
            {k: heat.values[k] - baseline[k] for k in delta},
            "exclusive_group": c.exclusive_group, "modifies": list(c.modifies),
            "notes": notes, **({"limits": list(c.limits)} if c.limits else {})}


def _reference(members: list[dict]) -> dict | None:
    for meta in ("Tech II", "Faction", "Tech I"):
        found = [r for r in members if r["meta"] == meta]
        if found:
            return found[0]
    return None


def _groups(rows: list[dict], first: str, sign: int) -> list[dict]:
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        by.setdefault((r["group"], r["source"]), []).append(r)
    out = []
    for (group, source), members in by.items():
        members.sort(key=lambda r: (-sign * r["delta"][first], r["name"]))
        top, ref = members[0], _reference(members)
        out.append({
            "group": group, "source": source, "slot": top["slot"], "variants": len(members),
            "best": {**{k: top[k] for k in ("name", "meta", "cpu", "pg", "calibration")},
                     "delta": top["delta"], **({"limits": top["limits"]} if "limits" in top
                                              else {})},
            "reference": None if ref is None or ref is top else
            {"name": ref["name"], "meta": ref["meta"], "delta": ref["delta"]},
            "delta_range": [members[-1]["delta"][first], top["delta"][first]],
            "notes": sorted({n for r in members for n in r["notes"]
                             if not n.startswith("on this fit")})[:5]})
    out.sort(key=lambda g: (-sign * g["delta_range"][1], g["group"]))
    return out


def _expand(rows: list[dict], groups: list[dict], expand: list[str] | None) -> list[dict]:
    if not expand:
        return []
    if "*" in expand:
        return rows
    known = {g["group"].casefold(): g["group"] for g in groups}
    unknown = [e for e in expand if e.casefold() not in known]
    if unknown:
        close = difflib.get_close_matches(unknown[0], list(known.values()), n=3, cutoff=0.5)
        raise ValueError(f"expand: no group '{unknown[0]}' in this result"
                         + (f" (did you mean: {', '.join(close)}?)" if close else ""))
    wanted = {e.casefold() for e in expand}
    return [r for r in rows if r["group"].casefold() in wanted]


def find_modifiers(fit: str, stat_keys: list[str], sources: list[str] | None = None,
                   meta: list[str] | None = None, raw_conditions: dict | None = None,
                   expand: list[str] | None = None, availability: str | None = None,
                   verbose: bool = False) -> dict:
    candidates.availability_filter(availability)  # a typo fails before any work
    signs: dict[str, int] = {}
    for text in stat_keys or []:
        key, sign = _objective(text)
        signs.setdefault(key, sign)
    stat_keys = list(signs)
    if not stat_keys:
        raise ValueError("stats: name at least one stat key, e.g. tank.ehp.total "
                         "or ship.shieldCapacity")
    _numeric(stat_keys)
    wanted = candidates.parse_sources(sources)
    ref, raw = _baseline_eft(fit), _portable(raw_conditions)
    with bench.Bench(ref, raw) as b:
        b.measure(stat_keys)  # unknown keys fail here, before any search
        caps = {k: c for k in stat_keys
                if signs[k] > 0 and (c := stats.cap(b.fit, k)) is not None}
        keys = stat_keys + [f"ship.{name}" for name, _ in caps.values()
                            if f"ship.{name}" not in stat_keys]
        baseline = b.measure(keys)
        found = _pool(b.fit, wanted, meta, availability)
        trials, owners = _add_trials(b, found.candidates)
        applied = {**b.applied, "meta": found.meta_note,
                   "availability": found.availability_note}
    results = _run(ref, raw, keys, trials)

    first = stat_keys[0]
    sign = signs[first]
    best: dict[int, tuple] = {}
    heat: dict[int, bench.Trial] = {}
    errors: dict[int, str] = {}
    for (index, state, replaced), trial in zip(owners, results):
        if trial.values is None:
            errors.setdefault(index, trial.error)
        elif state == "overheated":
            if index not in heat or sign * (trial.values[first]
                                            - heat[index].values[first]) > 0:
                heat[index] = trial
        elif index not in best or sign * (trial.values[first]
                                          - best[index][0].values[first]) > 0:
            best[index] = (trial, replaced)

    failed = [{"name": found.candidates[i].name, "group": found.candidates[i].group,
               "reason": f"Pyfa failed: {err}"}
              for i, err in errors.items() if i not in best]
    rows = []
    for index, (trial, replaced) in best.items():
        delta = {k: trial.values[k] - baseline[k] for k in stat_keys}
        if all(_zero(delta[k], baseline[k]) for k in stat_keys):
            continue
        c = found.candidates[index]
        for member in (c, *found.duplicates.get(c.name, ())):
            rows.append(_row(member, delta, heat.get(index), baseline, replaced,
                             trial.problems, signs))

    pinned = []
    for key, (cap_name, cap_value) in caps.items():
        if baseline[key] >= cap_value * (1 - 1e-9):
            cap_key = f"ship.{cap_name}"
            raised = sorted({found.candidates[i].name for i, (t, _) in best.items()
                             if t.values[cap_key] > baseline[cap_key]})
            pinned.append({"stat": key, "cap_attribute": cap_name, "cap": cap_value,
                           "raised_by": raised})

    groups = _groups(rows, first, sign)
    return {
        "applied": applied,
        "baseline": {k: baseline[k] for k in stat_keys},
        "groups": groups,
        "candidates": _expand(rows, groups, expand),
        "excluded": (_excluded_rows if verbose else _excluded_counts)(found.excluded + failed),
        "pinned": pinned,
        "coverage": {"items_scanned": found.scanned, "measured": len(trials),
                     "effects_unresolved": drift.unhandled_for(
                         [c.type_id for c in found.candidates])},
        "next": (f"optimize_fit(fit, objective=\"{'-' if sign < 0 else ''}{first}\") "
                 "builds the best fit from these; expand=[group names] lists every variant "
                 "of a group. Candidates: "
                 f"{found.meta_note}."),
    }


# --- marginal_swaps ----------------------------------------------------------

_RACKS = ("high", "mid", "low", "rig")
_LOCAL = {"module", "rig", "charge", "implant", "booster"}


class Option(NamedTuple):
    """One thing a search can put in one kind of place."""
    place: str           # "high"/"mid"/"low"/"rig" or "implant 7"/"booster 1"
    type_id: int
    charge_id: int | None
    state: str | None
    name: str
    group: str
    cpu: float = 0.0
    pg: float = 0.0
    calibration: float = 0.0
    limits: tuple = ()

    def edit(self, where) -> Edit:
        return Edit(where, self.type_id, self.charge_id, self.state)

    def sort_key(self) -> tuple:
        return (self.place, self.type_id, self.charge_id or 0, self.state or "")


def _objective(text: str) -> tuple[str, int]:
    text = text.strip()
    return (text[1:], -1) if text.startswith("-") else (text, 1)


def _options(cands, heat: bool) -> dict[str, list[Option]]:
    out: dict[str, list[Option]] = {}
    for c in cands:
        if c.extra is not None or c.source not in _LOCAL:
            continue
        if c.source in ("implant", "booster"):
            if len(c.edits) == 1:  # sets are moves of their own
                place = f"{c.source} {c.edits[0].where[1]}"
                out.setdefault(place, []).append(
                    Option(place, c.type_id, None, None, c.name, c.group, limits=c.limits))
            continue
        for state in (("active", "overheated") if heat and c.overheat else ("active",)):
            name = c.name + (" (overheated)" if state == "overheated" else "")
            out.setdefault(c.slot, []).append(Option(c.slot, c.type_id, c.charge_id, state,
                                                     name, c.group, c.cpu, c.pg,
                                                     c.calibration, c.limits))
    return out


def _place_key(b, where) -> str:
    return b.rack(where) if where[0] == "module" else f"{where[0]} {where[1]}"


def _pod_places(b, options) -> list[tuple]:
    places = [(kind, int(p.split()[1])) for p in options for kind in ("implant", "booster")
              if p.startswith(kind + " ")]
    places += [("implant", i.slot) for i in b.fit.implants]
    places += [("booster", x.slot) for x in b.fit.boosters]
    return list(dict.fromkeys(places))


def _swap_places(b, include_empty: bool, options) -> list[tuple]:
    """(where, occupant): one place per distinct occupant, one empty per rack."""
    seen, out = set(), []
    places = [w for w in b.module_places() if b.rack(w) in _RACKS] + _pod_places(b, options)
    for where in places:
        occ = b.occupant(where)
        key = (_place_key(b, where), occ)
        if key in seen or (occ is None and not include_empty):
            continue
        seen.add(key)
        out.append((where, occ))
    return out


def marginal_swaps(fit: str, objective: str, raw_conditions: dict | None = None,
                   meta: list[str] | None = None, include_empty_slots: bool = True,
                   top_n: int = 10, availability: str | None = None,
                   verbose: bool = False) -> dict:
    candidates.availability_filter(availability)  # a typo fails before any work
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    key, sign = _objective(objective)
    _numeric([key])
    ref, raw = _baseline_eft(fit), _portable(raw_conditions)
    with bench.Bench(ref, raw) as b:
        base = b.measure([key])[key]
        base_problems = b.problems()
        found = _pool(b.fit, _LOCAL, meta, availability)
        options = _options(found.candidates, heat=False)
        trials, labels = [], []
        for where, occ in _swap_places(b, include_empty_slots, options):
            place = _place_key(b, where)
            removed = None if occ is None else b.name_of(occ[0])
            if occ is not None:
                trials.append(([Edit(where, None)], None))
                labels.append((place, removed, None, ()))
            for option in options.get(place, []):
                if occ is not None and (option.type_id, option.charge_id) == occ[:2]:
                    continue
                trials.append(([option.edit(where)], None))
                labels.append((place, removed, option.name, option.limits))
        applied = {**b.applied, "meta": found.meta_note,
                   "availability": found.availability_note}
    results = _run(ref, raw, [key], trials)

    rows, invalid, failed = [], 0, []
    for (place, removed, added, lim), (edits, _), trial in zip(labels, trials, results):
        if trial.values is None:
            failed.append({"remove": removed, "add": added, "error": trial.error})
            continue
        if trial.problems:
            invalid += 1
            continue
        delta = trial.values[key] - base
        rows.append({"slot": place, "remove": removed, "add": added, "delta": delta,
                     "new_value": trial.values[key], "valid": True, "_edits": edits,
                     **({"limits": list(lim)} if lim else {})})
    rows.sort(key=lambda r: (-sign * r["delta"], r["add"] or "", r["remove"] or ""))
    improving = bool(rows) and sign * rows[0]["delta"] > 0 and not _zero(rows[0]["delta"], base)
    top, warnings = rows[:top_n], []
    if improving:
        confirmed = _confirm(ref, raw, top[0]["_edits"])
        value = confirmed["flat"][key]
        if not _zero(value - top[0]["new_value"], value):
            warnings.append(f"the bench measured {top[0]['new_value']:g} but evaluate_fit "
                            f"gives {value:g}; the evaluator's number is reported")
        top[0].update(new_value=value, delta=value - base, eft=confirmed["eft"])
    for r in rows:
        r.pop("_edits")
    invalid_base = {}
    if base_problems:
        invalid_base = {"baseline_invalid": base_problems}
        if not improving:
            invalid_base["reason"] = (
                "the fit is invalid as given (" + "; ".join(base_problems[:3]) + ") and no "
                "single change makes it both valid and better; fix those problems first, "
                "or let optimize_fit rebuild it")
    return {"applied": applied, "objective": objective, "baseline": base, "swaps": top,
            "no_improvement_found": not improving, **invalid_base, "warnings": warnings,
            "coverage": {"swaps_tried": len(trials), "invalid_dropped": invalid,
                         "failed": failed, "excluded": (_excluded_rows if verbose else _excluded_counts)(
                             found.excluded)}}


# --- optimize_fit ------------------------------------------------------------

_ALLOW_DEFAULT = {"slots": list(_RACKS), "implants": False, "boosters": False,
                  "module_states": ["active"], "command": False, "phenomena": False}
_FITTING_KEYS = ("ship.cpuOutput", "ship.powerOutput", "ship.upgradeCapacity")
_PAIR_OPTIONS = 6
_MAIN_SHARE = 0.85  # of each budget; the polish pass gets the rest
_METHOD = ("greedy seeds, then best-improvement local search over single swaps, "
           "implant-set swaps and pair swaps, then a polish pass of single swaps over "
           "every useful option, dominated ones included")


class _OutOfBudget(Exception):
    pass


def _allow(allow: dict | None) -> dict:
    merged = {**_ALLOW_DEFAULT, **(allow or {})}
    unknown = set(merged) - set(_ALLOW_DEFAULT)
    if unknown:
        raise ValueError(f"allow: unknown key '{sorted(unknown)[0]}'; "
                         f"known: {', '.join(_ALLOW_DEFAULT)}")
    bad = [s for s in merged["slots"] if s not in _RACKS]
    if bad:
        raise ValueError(f"allow.slots: '{bad[0]}' is not one of {', '.join(_RACKS)} "
                         "(subsystems are kept as fitted)")
    if [s for s in merged["module_states"] if s not in ("active", "overheated")]:
        raise ValueError('allow.module_states: use "active" and/or "overheated"')
    if "active" not in merged["module_states"]:
        raise ValueError('allow.module_states must include "active": a module is overheated '
                         'on top of being active, so heat alone is not a choice')
    for name in ("implants", "boosters", "command", "phenomena"):
        if not isinstance(merged[name], bool):
            raise ValueError(f"allow.{name} must be true or false")
    return merged


def _holds(value, op: str, target) -> bool:
    if op == "eq":
        return value == target if isinstance(target, bool) else _zero(value - target, target)
    slack = 1e-9 * max(1.0, abs(target))
    return value <= target + slack if op == "lte" else value >= target - slack


def _constraints(raw) -> list[tuple]:
    out = []
    for c in raw or []:
        ops = [op for op in ("eq", "lte", "gte") if isinstance(c, dict) and op in c]
        if (not isinstance(c, dict) or not isinstance(c.get("stat"), str) or len(ops) != 1
                or set(c) - {"stat", *ops}):
            raise ValueError('constraints: each is {"stat": key, "eq"|"lte"|"gte": value}')
        target = c[ops[0]]
        if not isinstance(target, int | float) or (isinstance(target, bool) and ops[0] != "eq"):
            raise ValueError(f"constraints: {c['stat']} {ops[0]} {target!r}: the target "
                             "must be a number (true/false only with eq)")
        out.append((c["stat"], ops[0], target))
    return out


def _locked_counts(locked: str | None) -> tuple[Counter, dict]:
    """How many of each item name must stay, and the names as written."""
    counts: Counter = Counter()
    written: dict[str, str] = {}
    for line in (locked or "").splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        name = re.sub(r"\s+x\d+$", "", line.split(",")[0].replace("/OFFLINE", "").strip())
        counts[name.casefold()] += 1
        written[name.casefold()] = name
    return counts, written


def _fitted(ref: str) -> tuple[str, list[str]]:
    """The fit as Pyfa imports it: items it refused (a second Damage Control) would
    leave every trial invalid, so the search starts from what was fitted."""
    with evaluate.Scratch() as scratch:
        fit = scratch.add_fit(ref)
        left_out = [f"{d.name}: {d.reason}" for d in getattr(fit, "dropped_modules", ())]
        return (eft.export_fit(fit) if left_out else ref), left_out


class _Search:
    def __init__(self, ref, raw, key, sign, cons, budget, deadline, places, place_key,
                 options):
        self.ref, self.raw, self.key, self.sign, self.cons = ref, raw, key, sign, cons
        self.keys = list(dict.fromkeys([key, *(s for s, _, _ in cons), *_FITTING_KEYS]))
        self.deadline = deadline
        self.left = budget["evaluations"]
        self.places, self.place_key, self.options = places, place_key, options
        self.full = options  # set pieces are found here, whatever pruning dropped
        self.top: dict[str, list[Option]] = {}
        self.seen: dict[tuple, tuple] = {}  # canonical state -> (score, state, values)
        self.evaluations = 0
        self.stopped_by: str | None = None
        self.stack = contextlib.ExitStack()  # closes the in-process bench
        self.bench = None
        self.problems: Counter = Counter()  # why fits were invalid, numbers blanked

    def canonical(self, state) -> tuple:
        return tuple(sorted((self.place_key[w], o.sort_key()) for w, o in state.items()
                            if o is not None))

    def edits(self, state) -> list[Edit]:
        return [o.edit(w) if o is not None else Edit(w, None) for w, o in state.items()]

    def _score(self, trial):
        if trial.values is None or trial.problems:
            return None
        broken = sum(not _holds(trial.values[s], op, x) for s, op, x in self.cons)
        return (-broken, self.sign * trial.values[self.key])

    def evaluate(self, states) -> list:
        todo: dict[tuple, dict] = {}
        for state in states:
            todo.setdefault(self.canonical(state), state)
        batch = [(c, s) for c, s in todo.items() if c not in self.seen]
        over = None
        if len(batch) > self.left:
            over, batch = "evaluations", batch[:self.left]  # spend what is left
        # In pieces, so the seconds budget is checked every second or so of work;
        # the first batch (the start fit) always runs, so there is a fit to return.
        step = max(pool.INLINE_LIMIT, 150 * pool.size())
        for at in range(0, len(batch), step):
            if self.seen and time.monotonic() > self.deadline:
                over = "seconds"
                break
            piece = batch[at:at + step]
            trials = self._trials([self.edits(state) for _, state in piece])
            self.left -= len(piece)
            self.evaluations += len(piece)
            for (canon, state), trial in zip(piece, trials):
                self.seen[canon] = (self._score(trial), state, trial.values)
                self.problems.update(re.sub(r"\d+(\.\d+)?", "#", p)
                                     for p in trial.problems or [trial.error] if p)
        if over:
            self.stopped_by = over
            raise _OutOfBudget
        return [self.seen[self.canonical(s)] for s in states]

    def _trials(self, edits: list) -> list:
        if pool.size() and (len(edits) >= pool.INLINE_LIMIT or pool.running()):
            return pool.run(self.ref, self.raw, self.keys, [(e, None) for e in edits])
        if self.bench is None:  # in-process: one bench for the whole search
            self.bench = self.stack.enter_context(bench.Bench(self.ref, self.raw))
        return [self.bench.trial(e, self.keys) for e in edits]

    def best(self, states):
        scored = [(s, st) for (s, _, _), st in zip(self.evaluate(states), states)
                  if s is not None]
        if not scored:
            return None, None
        return min(scored, key=lambda p: ((-p[0][0], -p[0][1]), self.canonical(p[1])))

    def better(self, score, state) -> bool:
        current = self.evaluate([state])[0][0]
        return score is not None and (current is None or score > current)

    def singles(self, state) -> list:
        seen, out = set(), []
        for where in self.places:
            pk, occ = self.place_key[where], state[where]
            if (pk, occ) in seen:
                continue
            seen.add((pk, occ))
            out += [{**state, where: o} for o in [None, *self.options.get(pk, [])] if o != occ]
        return out

    def pairs(self, state) -> list:
        seen, reps = set(), []
        for where in self.places:
            if (self.place_key[where], state[where]) not in seen:
                seen.add((self.place_key[where], state[where]))
                reps.append(where)
        out = []
        for i, a in enumerate(reps):
            for b in reps[i + 1:]:
                for oa in self.top.get(self.place_key[a], []):
                    for ob in self.top.get(self.place_key[b], []):
                        if oa != state[a] and ob != state[b]:
                            out.append({**state, a: oa, b: ob})
        return out

    def set_moves(self, state, sets) -> list:
        out = []
        for c in sets:
            new = dict(state)
            for e in c.edits:
                pk = f"{e.where[0]} {e.where[1]}"
                option = next((o for o in self.full.get(pk, []) if o.type_id == e.item_id),
                              None)
                if e.where not in new or option is None:
                    break
                new[e.where] = option
            else:
                out.append(new)
        return out

    def greedy(self, start, order) -> dict:
        state = dict(start)
        for pk in order:
            for where in [w for w in self.places if self.place_key[w] == pk]:
                score, best = self.best([{**state, where: o} for o in self.options.get(pk, [])])
                if self.better(score, state):
                    state = best
        return state

    def improve(self, seeds, sets) -> None:
        """Best single swap (or implant set) until none improves, then the best pair
        swap; repeat. Seeds step together, so each round is one large batch; pairs
        (the costly step) wait until no seed has an improving single left."""
        todo = [(state, False) for state in seeds]  # (state, at the pair step)
        while todo:
            stepping = [t for t in todo if not t[1]] or todo
            following = {self.canonical(st): (st, paired) for st, paired in todo
                         if (st, paired) not in stepping}
            moves = [self.pairs(st) if paired else self.singles(st) + self.set_moves(st, sets)
                     for st, paired in stepping]
            self.evaluate([m for ms in moves for m in ms])
            for (state, paired), ms in zip(stepping, moves):
                score, best = self.best(ms)
                if self.better(score, state):
                    following.setdefault(self.canonical(best), (best, False))
                elif not paired:
                    following.setdefault(self.canonical(state), (state, True))
            todo = list(following.values())

    def polish(self, state, swaps: list) -> dict:
        """Best single swap until none improves. Each swap taken is appended to
        `swaps` as (where, old option, new option, objective delta), so a budget
        running out midway still leaves the ones made."""
        while True:
            current = self.evaluate([state])[0][0]
            score, best = self.best(self.singles(state))
            if not self.better(score, state):
                return state
            where = next(w for w in state if best[w] != state[w])
            swaps.append((where, state[where], best[where],
                          None if current is None else self.sign * (score[1] - current[1])))
            state = best

    def screen(self, state) -> dict[Option, dict]:
        """Each option's effect on every searched key, put in an emptied place."""
        moves, owners = [], []
        for pk, opts in self.options.items():
            where = next((w for w in self.places if self.place_key[w] == pk), None)
            if where is None:
                continue
            empty = {**state, where: None}
            moves.append(empty)
            owners.append((None, empty))
            for option in opts:
                moves.append({**empty, where: option})
                owners.append((option, empty))
        results = self.evaluate(moves)
        bases = {self.canonical(e): v for (o, e), (_, _, v) in zip(owners, results) if o is None}
        out = {}
        for (option, empty), (_, _, values) in zip(owners, results):
            base = bases.get(self.canonical(empty))
            if option is not None and values is not None and base is not None:
                out[option] = {k: values[k] - base[k] for k in self.keys}
        return out


def _useful(delta: dict, key: str, sign: int, cons: list) -> bool:
    if sign * delta[key] > 0 and not _zero(delta[key], 1.0):
        return True
    for stat, op, _ in cons:
        d = delta[stat]
        if (op == "eq" and d != 0) or (op == "lte" and d < 0) or (op == "gte" and d > 0):
            return True
    return any(delta[k] > 0 for k in _FITTING_KEYS)


def _dominated(options: dict[str, list[Option]], deltas: dict, key: str, sign: int,
               cons: list) -> dict:
    """Options beaten within their own item group on the objective, every constraint,
    every fitting output and every fitting cost."""
    goods = [(key, sign), *((s, 1 if op == "gte" else -1) for s, op, _ in cons if op != "eq"),
             *((k, 1) for k in _FITTING_KEYS)]
    equal = [s for s, op, _ in cons if op == "eq"]
    out = {}
    for opts in options.values():
        for a in opts:
            for b in opts:
                if a is b or a.group != b.group or a not in deltas or b not in deltas:
                    continue
                da, db = deltas[a], deltas[b]
                no_worse = (all(g * db[k] >= g * da[k] for k, g in goods)
                            and all(db[s] == da[s] for s in equal)
                            and b.cpu <= a.cpu and b.pg <= a.pg
                            and b.calibration <= a.calibration)
                strictly = (any(g * db[k] > g * da[k] for k, g in goods) or b.cpu < a.cpu
                            or b.pg < a.pg or b.calibration < a.calibration)
                if no_worse and (strictly or b.sort_key() < a.sort_key()):
                    out[a] = f"dominated by {b.name}"
                    break
    return out


def _ranked(search) -> list:
    """Measured valid fits, best first; fits breaking more constraints never shown."""
    ranked = sorted(((s, st) for s, st, _ in search.seen.values() if s is not None),
                    key=lambda p: ((-p[0][0], -p[0][1]), search.canonical(p[1])))
    return [p for p in ranked if p[0][0] == ranked[0][0][0]] if ranked else []


def _polish(search, options, budget, started) -> dict:
    """Single swaps from the best fit over every useful option, dominated ones too:
    pruning compared options on nearly empty fits, where an option can lose that
    wins on a full one."""
    search.left += budget["evaluations"] - max(1, int(budget["evaluations"] * _MAIN_SHARE))
    search.deadline = started + budget["seconds"]
    out = {"swaps_applied": [], "converged": True}
    ranked = _ranked(search)
    if not ranked:
        return out
    search.options = options
    swaps: list = []
    try:
        search.polish(ranked[0][1], swaps)
    except _OutOfBudget:
        out["converged"] = False
    out["swaps_applied"] = [
        {"slot": search.place_key[w], "from": "(empty)" if old is None else old.name,
         "to": "(empty)" if new is None else new.name, "delta": delta}
        for w, old, new, delta in swaps]
    return out


_RUNNERS_UP = 3


def _fleet(ref, raw, keys, key, sign, edits, allow, ok) -> tuple[list, dict]:
    """The bursts and phenomena that most help the fit `edits` make. Pyfa keeps the
    strongest value per buff, so each charge comes from its strongest source and is
    kept if it helps on its own; phenomena are then tried on top, and none."""
    from pyfa_mcp import fleet

    def booster(source, charge):
        return {"command": [{"fit": fleet.booster_eft(source["hull"],
                                                      [(source["module"], charge)],
                                                      source["mindlink"])}]}

    ranked = fleet.by_charge(ok) if allow["command"] else {}
    charges = list(ranked)
    results = pool.run(ref, raw, keys, [(edits, None)] + [
        (edits, booster(ranked[c][0], c)) for c in charges])
    if results[0].values is None:
        raise ValueError(f"Pyfa could not compute the fit: {results[0].error}")
    base = results[0].values[key]
    kept = []
    for charge, trial in zip(charges, results[1:]):
        if trial.values is None:
            continue
        delta = trial.values[key] - base
        if sign * delta > 0 and not _zero(delta, base):
            kept.append({**ranked[charge][0], "charge": charge, "delta": delta})
    # ponytail: runners-up and phenomena ignore constraints; the final fit is
    # confirmed with them, so a broken constraint shows as valid: false.
    runners = [(k, s) for k in kept for s in ranked[k["charge"]][1:1 + _RUNNERS_UP]]
    measured = pool.run(ref, raw, keys, [(edits, booster(s, k["charge"]))
                                         for k, s in runners]) if runners else []
    ups: dict[str, list] = {}
    for (k, s), trial in zip(runners, measured):
        if trial.values is not None:
            ups.setdefault(k["charge"], []).append(
                {"hull": s["hull"], "module": s["module"], "mindlink": s["mindlink"],
                 "delta": trial.values[key] - base})
    fits = fleet.booster_fits(kept)
    command = [{"fit": text} for text in fits]
    bursts = [{**{f: k[f] for f in ("charge", "module", "hull", "mindlink", "delta")},
               "runners_up": ups.get(k["charge"], [])} for k in kept]

    phenomena = None
    if allow["phenomena"]:
        generators = candidates._phenomena(ok, [])
        tried = pool.run(ref, raw, keys, [(edits, {"command": command})] + [
            (edits, {"command": command + g.extra["command"]}) for g in generators])
        none = tried[0].values[key]
        deltas = {"none": 0.0, **{g.name: t.values[key] - none
                                  for g, t in zip(generators, tried[1:])
                                  if t.values is not None}}
        chosen = max(deltas, key=lambda n: (sign * deltas[n], n == "none"))
        if chosen != "none" and _zero(deltas[chosen], none):
            chosen = "none"
        if chosen != "none":
            extra = next(g for g in generators if g.name == chosen).extra["command"]
            command += extra
            fits = fits + [e["fit"] for e in extra]
        phenomena = {"chosen": None if chosen == "none" else chosen, "deltas": deltas}
    return command, {"bursts": bursts, "phenomena": phenomena, "booster_fits": fits}


def _under(search, raw) -> "_Search":
    """The same search, measuring under other conditions (a chosen fleet)."""
    other = _Search(search.ref, raw, search.key, search.sign, search.cons,
                    {"evaluations": search.left}, search.deadline, search.places,
                    search.place_key, search.options)
    other.top, other.full = search.top, search.full
    other.evaluations, other.problems = search.evaluations, search.problems
    return other


def _improve_under(search, raw, start, sets) -> tuple:
    """Improve `start` again under other conditions. The start is measured first,
    so a budget running out still leaves it ranked. Returns (search, finished)."""
    other = _under(search, raw)
    try:
        other.evaluate([start])
        other.improve([start], sets)
    except _OutOfBudget:
        return other, False
    return other, True


def _ordered(best: list[dict], sign: int) -> list[dict]:
    """Valid fits first, then by the confirmed objective: fits were ranked on the
    bench, possibly under another fleet than the one they are confirmed with."""
    return sorted(best, key=lambda b: (not b["valid"], -sign * b["objective_value"]))


_NEAR_WINNERS = 20


def _pruned_summary(pruned: dict, info: dict, best_eft: str | None) -> dict:
    """Counts by kind, and the dominated options in an item group the best fit uses:
    the ones that explain a choice."""
    import eos.db

    counts = Counter("dominated" if why.startswith("dominated") else "no effect"
                     for why in pruned.values())
    fitted = set()
    for name in _locked_counts(best_eft)[1].values() if best_eft else ():
        item = eos.db.getItem(name)
        if item is not None:
            fitted.add(item.group.name)
    near = [{"name": n, "reason": why,
             **({"limits": list(info[n].limits)} if info[n].limits else {})}
            for n, why in sorted(pruned.items())
            if why.startswith("dominated") and n in info and info[n].group in fitted]
    return {"counts": dict(sorted(counts.items())), "near_winners": near[:_NEAR_WINNERS]}


def _diff(first: dict, other: dict) -> dict:
    a, b = (Counter(line for line in fit["eft"].splitlines()[1:] if line.strip())
            for fit in (first, other))
    diff = {"remove": sorted((a - b).elements()), "add": sorted((b - a).elements())}
    states = other.get("conditions", {}).get("module_states", [])
    if states != first.get("conditions", {}).get("module_states", []):
        diff["module_states"] = states  # EFT has no heat: states tell such fits apart
    return {"objective_value": other["objective_value"], "valid": other["valid"],
            "diff": diff}


def optimize_fit(fit: str, objective: str, raw_conditions: dict | None = None,
                 allow: dict | None = None, meta: list[str] | None = None,
                 locked: str | None = None, constraints: list | None = None,
                 top_k: int = 5, budget: dict | None = None,
                 availability: str | None = None, verbose: bool = False) -> dict:
    started = time.monotonic()
    key, sign = _objective(objective)
    allow, cons = _allow(allow), _constraints(constraints)
    _numeric([key, *(s for s, _, _ in cons)])
    candidates.availability_filter(availability)  # a typo fails before any work
    raw = _portable(raw_conditions)
    if raw.get("module_states"):
        raise ValueError("optimize_fit chooses the modules: set heat with "
                         "allow.module_states, not conditions.module_states")
    wants_fleet = allow["command"] or allow["phenomena"]
    if wants_fleet and raw.get("command"):
        raise ValueError("allow.command/allow.phenomena: the search chooses the bursts and "
                         "phenomena; leave conditions.command empty, or turn those off")
    ok = candidates.item_filter(meta, availability)
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if set(budget or {}) - {"evaluations", "seconds"}:
        raise ValueError('budget: use {"evaluations": n, "seconds": s}')
    for name, value in (budget or {}).items():
        if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"budget.{name} must be a number above 0")
    budget = {"evaluations": 400_000, "seconds": 300, **(budget or {})}
    ref, left_out = _fitted(_baseline_eft(fit))
    sources = {"module", "rig", "charge"} | ({"implant"} if allow["implants"] else set()) \
        | ({"booster"} if allow["boosters"] else set())

    with bench.Bench(ref, raw) as b:
        b.measure([key, *(s for s, _, _ in cons)])  # unknown keys fail here
        found = _pool(b.fit, sources, meta, availability)
        options = {pk: opts for pk, opts in
                   _options(found.candidates, "overheated" in allow["module_states"]).items()
                   if pk in allow["slots"] or pk.split()[0] in ("implant", "booster")}
        keep, written = _locked_counts(locked)
        places, place_key, start = [], {}, {}
        for where in b.module_places() + _pod_places(b, options):
            occ = b.occupant(where)
            name = b.name_of(occ[0]).casefold() if occ else None
            if name and keep[name] > 0:  # locked: stays wherever it is
                keep[name] -= 1
                continue
            searched = (b.rack(where) in allow["slots"] if where[0] == "module"
                        else allow["implants" if where[0] == "implant" else "boosters"])
            if not searched:
                continue
            places.append(where)
            place_key[where] = _place_key(b, where)
            state = occ and occ[2]
            if where[0] == "module" and occ:  # as an Option's "active" edit would set it
                mod = b.fit.modules[where[1]]
                if mod.state == mod.getMaxState(conditions._state("active")):
                    state = "active"
            start[where] = None if occ is None else Option(
                place_key[where], occ[0], occ[1], state, b.name_of(occ[0]), "")
        missing = [n for n, c in keep.items() if c > 0]
        if missing:
            raise ValueError(f"locked: '{written[missing[0]]}' is not on the fit")
        options = {pk: opts for pk, opts in options.items() if pk in place_key.values()}
        applied = {**b.applied, "meta": found.meta_note,
                   "availability": found.availability_note,
                   "heat": " and ".join(allow["module_states"]), "left_out": left_out}
        if wants_fleet:
            applied["command"] = "chosen by the search (see fleet)"

    sets = [c for c in found.candidates if c.group == "Implant sets"]
    main_budget = {"evaluations": max(1, int(budget["evaluations"] * _MAIN_SHARE))}
    search = _Search(ref, raw, key, sign, cons, main_budget,
                     started + budget["seconds"] * _MAIN_SHARE, places, place_key, options)
    pruned: dict = {}
    considered = polish_options = options  # until pruning has run
    converged = True
    searches = [search]
    fleet_command, fleet_out, searched_under = [], None, False
    try:
        try:
            clean = {w: None for w in places}
            search.evaluate([start, clean])  # first, so even a tiny budget returns a fit
            deltas = search.screen(clean)
            order = [pk for pk in ("low", "mid", "rig", "high") if pk in options] + \
                sorted(pk for pk in options if pk not in _RACKS)
            useful = {o for o, d in deltas.items() if _useful(d, key, sign, cons)}
            search.options = {pk: [o for o in opts if o in useful]
                              for pk, opts in options.items()}
            seed = search.greedy(clean, order)
            search.options = options  # every option again, measured on the seed this time
            seed_deltas = search.screen(seed)
            for option, delta in seed_deltas.items():
                if _useful(delta, key, sign, cons):
                    useful.add(option)
                    deltas.setdefault(option, delta)
            kept = {pk: [o for o in opts if o in useful] for pk, opts in options.items()}
            polish_options = kept
            # Pruned only when beaten on the empty fit and on the seed: on an empty
            # hull a plain HP bonus can beat a resist bonus that wins on a full fit.
            on_seed = _dominated(kept, seed_deltas, key, sign, cons)
            dominated = {o: why for o, why in _dominated(kept, deltas, key, sign, cons).items()
                         if o in on_seed}
            for opts in options.values():
                for o in opts:
                    if o not in useful:
                        pruned[o.name] = ("no effect on the objective, the constraints or "
                                          "fitting resources")
                    elif o in dominated:
                        pruned[o.name] = dominated[o]
            search.options = considered = {pk: [o for o in opts if o not in dominated]
                                           for pk, opts in kept.items()}
            search.top = {pk: sorted(opts, key=lambda o: -sign * deltas[o][key])[:_PAIR_OPTIONS]
                          for pk, opts in search.options.items()}
            seeds = [seed, search.greedy(clean, order[::-1])]
            if any(o is not None for o in start.values()):
                seeds.insert(0, start)
            search.improve(seeds, sets)
        except _OutOfBudget:
            converged = False
        ranked = _ranked(search)
        if wants_fleet and ranked:
            fleet_command, _ = _fleet(ref, raw, search.keys, key, sign,
                                      search.edits(ranked[0][1]), allow, ok)
            if converged:  # improve the fit under that fleet
                under, converged = _improve_under(
                    search, bench.merge_conditions(raw, {"command": fleet_command}),
                    ranked[0][1], sets)
                searches.append(under)
                searched_under = True
                search = under
        polish = _polish(search, polish_options, budget, started)
        converged = converged and polish["converged"]
        ranked = _ranked(search)
        if wants_fleet and ranked:  # the fleet for the fit as polished
            fleet_command, fleet_out = _fleet(ref, raw, search.keys, key, sign,
                                              search.edits(ranked[0][1]), allow, ok)
            fleet_out["searched_under"] = searched_under
    finally:
        for each in searches:
            each.stack.close()
    final = bench.merge_conditions(raw, {"command": fleet_command}) if fleet_command else raw
    best = []
    for score, state in ranked[:top_k]:
        confirmed = _confirm(ref, final, search.edits(state))
        flat = confirmed["flat"]
        shown = list(dict.fromkeys([key, *(s for s, _, _ in cons), *stats.DEFAULT_COMPARE]))
        holds = all(_holds(flat[s], op, x) for s, op, x in cons)
        entry = {"eft": confirmed["eft"], "objective_value": flat[key]}
        if "overheated" in allow["module_states"]:
            entry["objective_cold"] = _value(confirmed["eft"], final, key)
        entry.update(stats={k: flat[k] for k in shown if k in flat},
                     valid=bool(flat["validity.valid"]) and holds,
                     warnings=confirmed["result"]["warnings"],
                     conditions=confirmed["conditions"])
        best.append(entry)
    if best:
        best[0]["polish"] = polish  # the pass ran from the top-ranked fit
        best = _ordered(best, sign)
    out = {"applied": applied,
           "best": best if verbose or not best else best[:1] + [_diff(best[0], b)
                                                              for b in best[1:]]}
    if wants_fleet:
        out["fleet"] = fleet_out
    if not best:
        common = search.problems.most_common(1)
        out["reason"] = (
            f"the budget ran out ({search.stopped_by}) before any valid fit was found"
            if not converged else "every fit tried was invalid"
        ) + (f"; most common problem: {common[0][0]}" if common else "")
    info = {o.name: o for opts in options.values() for o in opts}
    return {
        **out,
        "considered": {pk: (sorted if verbose else len)({o.name for o in opts})
                       for pk, opts in considered.items()},
        "pruned": ([{"name": n, "reason": r} for n, r in sorted(pruned.items())] if verbose
                   else _pruned_summary(pruned, info, best[0]["eft"] if best else None)),
        "excluded": (_excluded_rows if verbose else _excluded_counts)(found.excluded),
        "search": {"method": _METHOD, "evaluations": search.evaluations,
                   "seconds": round(time.monotonic() - started, 1),
                   "converged": converged, "stopped_by": search.stopped_by,
                   **({} if converged else {"note": _stop_note(search.stopped_by)})},
    }


def _stop_note(stopped_by: str) -> str:
    note = (f"stopped by budget.{stopped_by} before the search finished: these are the "
            "best fits found so far, not necessarily the best there are; a larger "
            f"budget.{stopped_by} searches further")
    if stopped_by == "seconds" and pool.size() <= 2:
        ran = (f"on only {pool.size()} search worker(s)" if pool.size() else
               "in-process (no search workers)")
        note += f"; it ran {ran}, and more workers (server option --workers) would too"
    return note
