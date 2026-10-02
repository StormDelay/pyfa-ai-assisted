"""Search fit space: what moves a stat, which single change helps, the best fit.

Candidates are measured on a bench (bench.py) through the worker pool; every
fit a tool returns is re-run through evaluate.evaluate, so the agent never
sees a number the evaluator did not produce.
"""
from __future__ import annotations

import difflib
import json
from collections import OrderedDict

from pyfa_mcp import bench, candidates, drift, eft, evaluate, pool, stats, store
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
    except store.StoreError:
        ship = _ship_named(fit)
        if ship is None:
            raise
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
    key = ("pool", fit.ship.item.ID, json.dumps(meta), tuple(sorted(sources)))
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
        targets = [(empty, None)] if empty is not None else _distinct_occupied(b, places)
        for state in (("active", "overheated") if c.overheat else ("active",)):
            for where, replaced in targets:
                trials.append(([Edit(where, c.type_id, c.charge_id, state)], None))
                owners.append((index, state, replaced))
    return trials, owners


def _row(c, delta, heat, baseline, replaced, problems, duplicates) -> dict:
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
    twins = duplicates.get(c.name)
    if twins:
        notes.append("identical to " + ", ".join(twins[:5]) + (" ..." if len(twins) > 5 else ""))
    return {"name": c.name, "type_id": c.type_id, "source": c.source, "slot": c.slot,
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
                             if not n.startswith(("identical to", "on this fit"))})[:5]})
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
    failed = []
    for (index, state, replaced), trial in zip(owners, results):
        if trial.values is None:
            failed.append({"name": found.candidates[index].name, "error": trial.error})
        elif state == "overheated":
            if index not in heat or trial.values[first] > heat[index].values[first]:
                heat[index] = trial
        elif index not in best or trial.values[first] > best[index][0].values[first]:
            best[index] = (trial, replaced)

    rows = []
    for index, (trial, replaced) in best.items():
        delta = {k: trial.values[k] - baseline[k] for k in stat_keys}
        if all(_zero(delta[k], baseline[k]) for k in stat_keys):
            continue
        rows.append(_row(found.candidates[index], delta, heat.get(index), baseline,
                         replaced, trial.problems, found.duplicates))

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
        "excluded": _excluded_rows(found.excluded),
        "pinned": pinned,
        "coverage": {"items_scanned": found.scanned, "measured": len(trials),
                     "failed": failed,
                     "effects_unresolved": drift.unhandled_for(
                         [c.type_id for c in found.candidates])},
        "next": (f"optimize_fit(fit, objective=\"{first}\") builds the best fit from these; "
                 "expand=[group names] lists every variant of a group. Candidates: "
                 f"{found.meta_note}."),
    }
