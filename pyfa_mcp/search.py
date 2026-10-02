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

from pyfa_mcp import bench, candidates, conditions, drift, eft, evaluate, pool, stats, store
from pyfa_mcp.bench import Edit

_CACHE: OrderedDict = OrderedDict()
_CACHE_SIZE = 32


def _cached(key, compute):
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    value = _CACHE[key] = compute()
    if len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


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


def _run(ref: str, raw: dict, keys: list[str], trials: list) -> list:
    key = ("trials", json.dumps([ref, raw, list(keys), trials], sort_keys=True))
    return _cached(key, lambda: pool.run(ref, raw, list(keys), trials))


def _pool(fit, sources: set[str], meta: list[str] | None):
    subs = tuple(sorted(m.item.ID for m in fit.modules if not m.isEmpty and m.slot == 5))
    key = ("pool", fit.ship.item.ID, subs, json.dumps(meta), tuple(sorted(sources)))
    return _cached(key, lambda: candidates.build(fit, sources, meta))


def _excluded_rows(excluded: list[dict]) -> list[dict]:
    by: dict[tuple, list[str]] = {}
    for e in excluded:
        by.setdefault((e["group"], e["reason"]), []).append(e["name"])
    return [{"group": g, "reason": r, "variants": len(n), "examples": n[:3]}
            for (g, r), n in sorted(by.items())]


def _confirm(ref: str, raw: dict, edits) -> dict:
    """The evaluator's numbers for the bench fit after `edits`."""
    with bench.Bench(ref, raw) as b:
        b.apply(edits)
        text, states = b.eft(), b.module_states()
    result = evaluate.evaluate(text, {**raw, "module_states": states})
    flat = stats.flatten({k: v for k, v in result.items()
                          if k not in ("fit", "ship", "applied", "warnings")})
    return {"eft": text, "flat": flat, "result": result}


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


def _row(c, delta, heat, baseline, replaced, problems) -> dict:
    notes = []
    if c.active:
        notes.append("active module: measured active")
    if replaced:
        notes.append(f"its slots are full: measured replacing {replaced}")
    notes += [f"drawback: lowers {k}" for k, d in delta.items()
              if d < 0 and not _zero(d, baseline[k])]
    if problems:
        notes.append("on this fit: " + "; ".join(problems[:2]))
    if c.source == "command_burst":
        notes.append("measured from an unbonused Ferox; a command ship, mindlink or "
                     "booster skills give more")
    return {"name": c.name, "type_id": c.type_id, "source": c.source, "slot": c.slot,
            "charge": None if c.charge_id is None else _charge_name(c.charge_id),
            "group": c.group, "meta": c.meta, "cpu": c.cpu, "pg": c.pg,
            "calibration": c.calibration, "delta": delta,
            "delta_overheated": None if heat is None else
            {k: heat.values[k] - baseline[k] for k in delta},
            "exclusive_group": c.exclusive_group, "modifies": list(c.modifies),
            "notes": notes}


def _reference(members: list[dict]) -> dict | None:
    for meta in ("Tech II", "Faction", "Tech I"):
        found = [r for r in members if r["meta"] == meta]
        if found:
            return found[0]
    return None


def _groups(rows: list[dict], first: str) -> list[dict]:
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        by.setdefault((r["group"], r["source"]), []).append(r)
    out = []
    for (group, source), members in by.items():
        members.sort(key=lambda r: (-r["delta"][first], r["name"]))
        top, ref = members[0], _reference(members)
        out.append({
            "group": group, "source": source, "slot": top["slot"], "variants": len(members),
            "best": {**{k: top[k] for k in ("name", "meta", "cpu", "pg", "calibration")},
                     "delta": top["delta"]},
            "reference": None if ref is None or ref is top else
            {"name": ref["name"], "meta": ref["meta"], "delta": ref["delta"]},
            "delta_range": [members[-1]["delta"][first], top["delta"][first]],
            "notes": sorted({n for r in members for n in r["notes"]
                             if not n.startswith("on this fit")})[:5]})
    out.sort(key=lambda g: (-g["delta_range"][1], g["group"]))
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
                   expand: list[str] | None = None) -> dict:
    stat_keys = list(dict.fromkeys(stat_keys or []))
    if not stat_keys:
        raise ValueError("stats: name at least one stat key, e.g. tank.ehp.total "
                         "or ship.shieldCapacity")
    wanted = candidates.parse_sources(sources)
    ref, raw = _baseline_eft(fit), _portable(raw_conditions)
    with bench.Bench(ref, raw) as b:
        b.measure(stat_keys)  # unknown keys fail here, before any search
        caps = {k: c for k in stat_keys if (c := stats.cap(b.fit, k)) is not None}
        keys = stat_keys + [f"ship.{name}" for name, _ in caps.values()
                            if f"ship.{name}" not in stat_keys]
        baseline = b.measure(keys)
        found = _pool(b.fit, wanted, meta)
        trials, owners = _add_trials(b, found.candidates)
        applied = {**b.applied, "meta": found.meta_note}
    results = _run(ref, raw, keys, trials)

    first = stat_keys[0]
    best: dict[int, tuple] = {}
    heat: dict[int, bench.Trial] = {}
    errors: dict[int, str] = {}
    for (index, state, replaced), trial in zip(owners, results):
        if trial.values is None:
            errors.setdefault(index, trial.error)
        elif state == "overheated":
            if index not in heat or trial.values[first] > heat[index].values[first]:
                heat[index] = trial
        elif index not in best or trial.values[first] > best[index][0].values[first]:
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
                             trial.problems))

    pinned = []
    for key, (cap_name, cap_value) in caps.items():
        if baseline[key] >= cap_value * (1 - 1e-9):
            cap_key = f"ship.{cap_name}"
            raised = sorted({found.candidates[i].name for i, (t, _) in best.items()
                             if t.values[cap_key] > baseline[cap_key]})
            pinned.append({"stat": key, "cap_attribute": cap_name, "cap": cap_value,
                           "raised_by": raised})

    groups = _groups(rows, first)
    return {
        "applied": applied,
        "baseline": {k: baseline[k] for k in stat_keys},
        "groups": groups,
        "candidates": _expand(rows, groups, expand),
        "excluded": _excluded_rows(found.excluded + failed),
        "pinned": pinned,
        "coverage": {"items_scanned": found.scanned, "measured": len(trials),
                     "effects_unresolved": drift.unhandled_for(
                         [c.type_id for c in found.candidates])},
        "next": (f"optimize_fit(fit, objective=\"{first}\") builds the best fit from these; "
                 "expand=[group names] lists every variant of a group. Candidates: "
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
                    Option(place, c.type_id, None, None, c.name, c.group))
            continue
        for state in (("active", "overheated") if heat and c.overheat else ("active",)):
            name = c.name + (" (overheated)" if state == "overheated" else "")
            out.setdefault(c.slot, []).append(Option(c.slot, c.type_id, c.charge_id, state,
                                                     name, c.group, c.cpu, c.pg,
                                                     c.calibration))
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
                   top_n: int = 10) -> dict:
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    key, sign = _objective(objective)
    ref, raw = _baseline_eft(fit), _portable(raw_conditions)
    with bench.Bench(ref, raw) as b:
        base = b.measure([key])[key]
        found = _pool(b.fit, _LOCAL, meta)
        options = _options(found.candidates, heat=False)
        trials, labels = [], []
        for where, occ in _swap_places(b, include_empty_slots, options):
            place = _place_key(b, where)
            removed = None if occ is None else b.name_of(occ[0])
            if occ is not None:
                trials.append(([Edit(where, None)], None))
                labels.append((place, removed, None))
            for option in options.get(place, []):
                if occ is not None and (option.type_id, option.charge_id) == occ[:2]:
                    continue
                trials.append(([option.edit(where)], None))
                labels.append((place, removed, option.name))
        applied = {**b.applied, "meta": found.meta_note}
    results = _run(ref, raw, [key], trials)

    rows, invalid, failed = [], 0, []
    for (place, removed, added), (edits, _), trial in zip(labels, trials, results):
        if trial.values is None:
            failed.append({"remove": removed, "add": added, "error": trial.error})
            continue
        if trial.problems:
            invalid += 1
            continue
        delta = trial.values[key] - base
        rows.append({"slot": place, "remove": removed, "add": added, "delta": delta,
                     "new_value": trial.values[key], "valid": True, "_edits": edits})
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
    return {"applied": applied, "objective": objective, "baseline": base, "swaps": top,
            "no_improvement_found": not improving, "warnings": warnings,
            "coverage": {"swaps_tried": len(trials), "invalid_dropped": invalid,
                         "failed": failed, "excluded": _excluded_rows(found.excluded)}}


# --- optimize_fit ------------------------------------------------------------

_ALLOW_DEFAULT = {"slots": list(_RACKS), "implants": False, "boosters": False,
                  "module_states": ["active"]}
_FITTING_KEYS = ("ship.cpuOutput", "ship.powerOutput", "ship.upgradeCapacity")
_PAIR_OPTIONS = 6
_METHOD = ("greedy seeds, then best-improvement local search over single swaps, "
           "implant-set swaps and pair swaps")


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
        out.append((c["stat"], ops[0], c[ops[0]]))
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
    def __init__(self, ref, raw, key, sign, cons, budget, places, place_key, options):
        self.ref, self.raw, self.key, self.sign, self.cons = ref, raw, key, sign, cons
        self.keys = list(dict.fromkeys([key, *(s for s, _, _ in cons), *_FITTING_KEYS]))
        self.deadline = time.monotonic() + budget["seconds"]
        self.left = budget["evaluations"]
        self.places, self.place_key, self.options = places, place_key, options
        self.full = options  # set pieces are found here, whatever pruning dropped
        self.top: dict[str, list[Option]] = {}
        self.seen: dict[tuple, tuple] = {}  # canonical state -> (score, state, values)
        self.evaluations = 0
        self.stopped_by: str | None = None
        self.stack = contextlib.ExitStack()  # closes the in-process bench
        self.bench = None

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
        if batch and time.monotonic() > self.deadline:
            over, batch = "seconds", []
        elif len(batch) > self.left:
            over, batch = "evaluations", batch[:self.left]  # spend what is left
        if batch:
            trials = self._trials([self.edits(state) for _, state in batch])
            self.left -= len(batch)
            self.evaluations += len(batch)
            for (canon, state), trial in zip(batch, trials):
                self.seen[canon] = (self._score(trial), state, trial.values)
        if over:
            self.stopped_by = over
            raise _OutOfBudget
        return [self.seen[self.canonical(s)] for s in states]

    def _trials(self, edits: list) -> list:
        if pool.size() and len(edits) >= pool.INLINE_LIMIT:
            return _run(self.ref, self.raw, self.keys, [(e, None) for e in edits])
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
        swap; repeat. All seeds step together, so each round is one large batch."""
        todo = [(state, False) for state in seeds]  # (state, at the pair step)
        while todo:
            moves = [self.pairs(st) if paired else self.singles(st) + self.set_moves(st, sets)
                     for st, paired in todo]
            self.evaluate([m for ms in moves for m in ms])
            following = {}
            for (state, paired), ms in zip(todo, moves):
                score, best = self.best(ms)
                if self.better(score, state):
                    following.setdefault(self.canonical(best), (best, False))
                elif not paired:
                    following.setdefault(self.canonical(state), (state, True))
            todo = list(following.values())

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


def optimize_fit(fit: str, objective: str, raw_conditions: dict | None = None,
                 allow: dict | None = None, meta: list[str] | None = None,
                 locked: str | None = None, constraints: list | None = None,
                 top_k: int = 5, budget: dict | None = None) -> dict:
    started = time.monotonic()
    key, sign = _objective(objective)
    allow, cons = _allow(allow), _constraints(constraints)
    raw = _portable(raw_conditions)
    if raw.get("module_states"):
        raise ValueError("optimize_fit chooses the modules: set heat with "
                         "allow.module_states, not conditions.module_states")
    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if set(budget or {}) - {"evaluations", "seconds"}:
        raise ValueError('budget: use {"evaluations": n, "seconds": s}')
    budget = {"evaluations": 20000, "seconds": 60, **(budget or {})}
    ref, left_out = _fitted(_baseline_eft(fit))
    sources = {"module", "rig", "charge"} | ({"implant"} if allow["implants"] else set()) \
        | ({"booster"} if allow["boosters"] else set())

    with bench.Bench(ref, raw) as b:
        b.measure([key, *(s for s, _, _ in cons)])  # unknown keys fail here
        found = _pool(b.fit, sources, meta)
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
                   "heat": " and ".join(allow["module_states"]), "left_out": left_out}

    sets = [c for c in found.candidates if c.group == "Implant sets"]
    search = _Search(ref, raw, key, sign, cons, budget, places, place_key, options)
    pruned: dict = {}
    considered = options  # until pruning has run
    converged = True
    try:
        clean = {w: None for w in places}
        search.evaluate([start, clean])  # first, so even a tiny budget returns a fit
        deltas = search.screen(clean)
        order = [pk for pk in ("low", "mid", "rig", "high") if pk in options] + \
            sorted(pk for pk in options if pk not in _RACKS)
        useful = {o for o, d in deltas.items() if _useful(d, key, sign, cons)}
        search.options = {pk: [o for o in opts if o in useful] for pk, opts in options.items()}
        seed = search.greedy(clean, order)
        search.options = {pk: [o for o in opts if o not in useful]
                          for pk, opts in options.items()}
        for option, delta in search.screen(seed).items():
            if _useful(delta, key, sign, cons):
                useful.add(option)
                deltas.setdefault(option, delta)
        kept = {pk: [o for o in opts if o in useful] for pk, opts in options.items()}
        dominated = _dominated(kept, deltas, key, sign, cons)
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
    finally:
        search.stack.close()

    ranked = sorted(((s, st) for s, st, _ in search.seen.values() if s is not None),
                    key=lambda p: ((-p[0][0], -p[0][1]), search.canonical(p[1])))
    # fits that break fewer constraints always rank first; none that break more is shown
    ranked = [p for p in ranked if p[0][0] == ranked[0][0][0]]
    best = []
    for score, state in ranked[:top_k]:
        confirmed = _confirm(ref, raw, search.edits(state))
        flat = confirmed["flat"]
        shown = list(dict.fromkeys([key, *(s for s, _, _ in cons), *stats.DEFAULT_COMPARE]))
        holds = all(_holds(flat[s], op, x) for s, op, x in cons)
        best.append({"eft": confirmed["eft"], "objective_value": flat[key],
                     "stats": {k: flat[k] for k in shown if k in flat},
                     "valid": bool(flat["validity.valid"]) and holds,
                     "warnings": confirmed["result"]["warnings"]})
    return {
        "applied": applied,
        "best": best,
        "considered": {pk: sorted({o.name for o in opts})
                       for pk, opts in considered.items()},
        "pruned": [{"name": n, "reason": r} for n, r in sorted(pruned.items())],
        "excluded": _excluded_rows(found.excluded),
        "search": {"method": _METHOD, "evaluations": search.evaluations,
                   "seconds": round(time.monotonic() - started, 1),
                   "converged": converged, "stopped_by": search.stopped_by},
    }
