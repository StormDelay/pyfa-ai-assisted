# Fleet search, availability, optimizer fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `optimize_fit` stop missing swaps, return reproducible and
compact results, hide unusable items, and search command bursts and
phenomena from the game data; add `list_ships` capability filters,
`compare_fits` variants and `whats_new`.

**Architecture:** Changes stay inside the existing modules. `search.py`
(optimizer, find_modifiers, marginal_swaps) gets the polish pass, the
two-screen dominance rule, reproduce blocks, compact output and the fleet
step. `catalog.py` gets item limits, implant/booster slots, capability
filters and `whats_new`. `candidates.py` gets the availability filter. One
new module, `fleet.py`, holds the burst strength table (game data only,
cached on disk per game build). `server.py` exposes the new parameters.

**Tech Stack:** Python 3.14, Pyfa's eos (vendored at `vendor/Pyfa`), the MCP
Python SDK, pytest.

**Spec:** `docs/superpowers/specs/2026-10-03-fleet-search-and-availability-design.md`

## Global Constraints

- Run tests with `.venv/Scripts/python.exe -m pytest` from the repo root (Windows, Git Bash). There is no `uv` on PATH.
- Tests that need eos take the session fixture `booted`; tests that create fits also take `no_fits_left`.
- A throwaway script that runs a search must wrap its body in `if __name__ == "__main__":` (Windows spawns the worker pool's processes by re-importing the main module).
- Defaults: external effects stay off (`allow.command` / `allow.phenomena` false); limited items hidden (`availability="tq"`); compact output (`verbose=false`).
- Values a test asserts come from this server's evaluator, never from the brief: this server measures about 6% lower than the brief.
- Budget split: the main search gets 85% of `budget.evaluations` and `budget.seconds` (`_MAIN_SHARE = 0.85`), the polish pass the rest.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

- A typo in `availability` ("TQ", "theoretical") must fail with a message naming "tq" and "all", not silently fall back. Test in Task 4.
- `allow.command` on a fit for which no valid fit is ever found must return `best: []` with a reason and `fleet: null`, not crash in the fleet step. Test in Task 7.
- A damaged or truncated `burst_sources-<build>.json` must be rebuilt, not crash every burst query. Test in Task 6.
- `compare_fits(variants=[])` or a non-object variant must fail clearly, not return an empty table. Test in Task 8.
- `whats_new` with `limit=0` or an unknown category must fail naming the valid categories. Test in Task 9.

---

## File Structure

- Modify `pyfa_mcp/search.py`: polish pass, two-screen dominance, `_ranked`, reproduce/cold values, compact output, availability plumbing, fleet step.
- Modify `pyfa_mcp/candidates.py`: `availability_filter`, `item_filter`, item predicates in `build`/`_bursts`/`_phenomena`, `limits` and `note` on `Candidate`, bursts from `fleet`.
- Modify `pyfa_mcp/catalog.py`: `limits`, `client_build`, implant/booster `slot`, `list_ships(can_fit, bonus)`, `whats_new`.
- Create `pyfa_mcp/fleet.py`: burst strength table, `sources`, `by_charge`, `booster_eft`, `booster_fits`.
- Modify `pyfa_mcp/evaluate.py`: `compare(..., variants)`.
- Modify `pyfa_mcp/server.py`: new parameters and docstrings, `whats_new` tool, INSTRUCTIONS, `status` via `catalog.client_build`.
- Modify `packaging/mcp_smoke.py`: add `whats_new` to `TOOLS`.
- Tests: `tests/test_search.py`, `tests/test_catalog.py`, `tests/test_candidates.py`, `tests/test_evaluate.py`, `tests/test_server.py`, create `tests/test_fleet.py`.

---

### Task 1: Polish pass and two-screen dominance

**Files:**
- Modify: `pyfa_mcp/search.py` (`_METHOD`, `_Search`, `optimize_fit` search block and ranking)
- Test: `tests/test_search.py`

**Interfaces:**
- Produces: `_MAIN_SHARE = 0.85`; `_Search.polish(state, swaps: list) -> dict`; `_ranked(search) -> list[(score, state)]`; `_polish(search, options, budget, started) -> {"swaps_applied": [...], "converged": bool}`; `optimize_fit` result `best[0]["polish"]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_search.py`:

```python
def test_polish_takes_improving_singles_until_none_is_left():
    s = search._Search.__new__(search._Search)
    s.sign = 1
    s.singles = lambda st: [{"a": st["a"] + 1}] if st["a"] < 3 else [{"a": 0}]
    s.evaluate = lambda states: [((0, st["a"]), st, None) for st in states]
    s.best = lambda moves: ((0, moves[0]["a"]), moves[0])
    s.better = lambda score, state: score[1] > state["a"]
    swaps = []
    assert s.polish({"a": 0}, swaps) == {"a": 3}
    assert swaps == [("a", 0, 1, 1), ("a", 1, 2, 1), ("a", 2, 3, 1)]


def test_t1_a_resist_booster_is_not_pruned_on_an_empty_hull(booted, no_fits_left):
    # On a bare Wyvern G-5 beats B-5; on this fit B-5 wins (166.60M vs 166.29M).
    result = search.optimize_fit(wyvern.BEST_LOWS, "tank.ehp.total",
                                 allow={"slots": ["low", "mid", "rig"], "boosters": True},
                                 locked="\n".join(wyvern.POD), meta=["all"], top_k=1,
                                 budget={"seconds": 600})
    best = result["best"][0]
    assert "Halcyon B-5 Booster" in best["eft"].splitlines()
    assert "Halcyon G-5 Booster" not in best["eft"].splitlines()
    assert best["polish"]["converged"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -k "polish or t1_a_resist" -v`
Expected: both FAIL (`_Search` has no `polish`; the optimizer returns Halcyon G-5).

- [ ] **Step 3: Implement**

In `pyfa_mcp/search.py`, replace `_METHOD`:

```python
_MAIN_SHARE = 0.85  # of each budget; the polish pass gets the rest
_METHOD = ("greedy seeds, then best-improvement local search over single swaps, "
           "implant-set swaps and pair swaps, then a polish pass of single swaps over "
           "every useful option, dominated ones included")
```

Add this method to `_Search`, after `improve`:

```python
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
```

Add these functions after `_dominated`:

```python
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
```

In `optimize_fit`, replace everything from `sets = [c for c in found.candidates if c.group == "Implant sets"]` down to and including the `ranked = [p for p in ranked if p[0][0] == ranked[0][0][0]]` line with:

```python
    sets = [c for c in found.candidates if c.group == "Implant sets"]
    main_budget = {"evaluations": max(1, int(budget["evaluations"] * _MAIN_SHARE))}
    search = _Search(ref, raw, key, sign, cons, main_budget,
                     started + budget["seconds"] * _MAIN_SHARE, places, place_key, options)
    pruned: dict = {}
    considered = polish_options = options  # until pruning has run
    converged = True
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
        polish = _polish(search, polish_options, budget, started)
    finally:
        search.stack.close()

    ranked = _ranked(search)
```

Then, right after the `for score, state in ranked[:top_k]:` loop that builds `best`, add:

```python
    if best:
        best[0]["polish"] = polish
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -k "polish or t1_a_resist" -v`
Expected: PASS.

- [ ] **Step 5: Run the optimizer tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -v`
Expected: all PASS. If `test_an_empty_best_says_why` fails, check that `main_budget` uses `max(1, ...)`, so the start fit can still be measured with `budget={"evaluations": 1}`.

- [ ] **Step 6: Commit**

```bash
git add pyfa_mcp/search.py tests/test_search.py
git commit -m "optimize_fit: prune only what loses on both screens, and polish the best fit with single swaps

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Reproduce block and cold value

**Files:**
- Modify: `pyfa_mcp/search.py` (`_confirm`, new `_flat`/`_value`, `optimize_fit` best entries)
- Modify: `pyfa_mcp/server.py` (`optimize_fit` docstring)
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `optimize_fit` from Task 1.
- Produces: `_confirm(ref, raw, edits) -> {"eft", "flat", "result", "conditions"}`; `_value(text, raw, key) -> float`; best entries gain `conditions` (and `objective_cold` when heat is allowed).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_search.py`:

```python
def test_t3_a_result_reproduces_with_its_conditions(booted, zealot_eft, no_fits_left):
    result = search.optimize_fit(zealot_eft, "tank.ehp.total",
                                 allow={"slots": ["low"],
                                        "module_states": ["active", "overheated"]},
                                 top_k=1, budget={"seconds": 300})
    best = result["best"][0]
    assert any(s["state"] == "overheated" for s in best["conditions"]["module_states"])
    assert _ehp(best["eft"], best["conditions"]) == pytest.approx(best["objective_value"],
                                                                 rel=1e-12)
    assert best["objective_cold"] < best["objective_value"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -k t3_a_result -v`
Expected: FAIL with `KeyError: 'conditions'`.

- [ ] **Step 3: Implement**

In `pyfa_mcp/search.py`, replace `_confirm` with:

```python
def _flat(result: dict) -> dict:
    return stats.flatten({k: v for k, v in result.items()
                          if k not in ("fit", "ship", "applied", "warnings")})


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
```

In `optimize_fit`, replace the body of the `for score, state in ranked[:top_k]:` loop with:

```python
        confirmed = _confirm(ref, raw, search.edits(state))
        flat = confirmed["flat"]
        shown = list(dict.fromkeys([key, *(s for s, _, _ in cons), *stats.DEFAULT_COMPARE]))
        holds = all(_holds(flat[s], op, x) for s, op, x in cons)
        entry = {"eft": confirmed["eft"], "objective_value": flat[key]}
        if "overheated" in allow["module_states"]:
            entry["objective_cold"] = _value(confirmed["eft"], raw, key)
        entry.update(stats={k: flat[k] for k in shown if k in flat},
                     valid=bool(flat["validity.valid"]) and holds,
                     warnings=confirmed["result"]["warnings"],
                     conditions=confirmed["conditions"])
        best.append(entry)
```

In `pyfa_mcp/server.py`, in the `optimize_fit` docstring, replace the last sentence (`Every returned fit is computed by evaluate_fit; ... inside the budget.`) with:

```
    them. Every returned fit is computed by evaluate_fit; its `conditions`
    reproduce it there (module states included: EFT has no heat), and with heat
    allowed `objective_cold` is the same fit unheated. best[0]["polish"] lists
    the single swaps a last pass made. `search.converged` says whether the search
    finished inside the budget."""
```

(Keep the `them.` that precedes it: the sentence before reads "...stay as conditions set them.")

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -k "t3_a_result or marginal" -v`
Expected: PASS. `marginal_swaps` also calls `_confirm`; it only reads `eft` and `flat`.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/search.py pyfa_mcp/server.py tests/test_search.py
git commit -m "optimize_fit: return the conditions that reproduce each fit, and its cold value under heat

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Compact output by default

**Files:**
- Modify: `pyfa_mcp/search.py` (`_excluded_counts`, `_pruned_summary`, `_diff`; `verbose` on `find_modifiers`, `marginal_swaps`, `optimize_fit`)
- Modify: `pyfa_mcp/server.py` (the three tools)
- Test: `tests/test_search.py` (new tests; `test_t2_...` passes `verbose=True`)

**Interfaces:**
- Consumes: `optimize_fit` from Task 2.
- Produces: `verbose: bool = False` as the last parameter of `search.find_modifiers`, `search.marginal_swaps`, `search.optimize_fit`; compact `excluded` is `{reason: count}`; compact `pruned` is `{"counts": {...}, "near_winners": [...]}`; compact `considered` is `{place: count}`; compact `best[1:]` is `{"objective_value", "valid", "diff": {"remove", "add"}}`.

- [ ] **Step 1: Write the failing tests**

Add `import json` to the top of `tests/test_search.py`, then append:

```python
def test_t4_compact_output_fits_a_context(booted, no_fits_left):
    allow = {"slots": ["high", "mid", "low", "rig"], "implants": True, "boosters": True,
             "module_states": ["active", "overheated"]}
    compact = search.optimize_fit("Wyvern", "tank.ehp.total", wyvern.CONDITIONS,
                                  allow=allow, budget={"seconds": 30})
    assert len(json.dumps(compact)) < 24_000
    assert isinstance(compact["considered"]["low"], int)
    assert set(compact["pruned"]) == {"counts", "near_winners"}
    assert len(compact["pruned"]["near_winners"]) <= 20
    assert all(isinstance(n, int) for n in compact["excluded"].values())
    assert all(set(b) == {"objective_value", "valid", "diff"} for b in compact["best"][1:])


def test_verbose_brings_back_every_name(booted, zealot_eft, no_fits_left):
    full = search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"slots": ["low"]},
                               top_k=2, verbose=True)
    assert isinstance(full["considered"]["low"], list)
    assert isinstance(full["pruned"], list) and isinstance(full["excluded"], list)
    assert all("eft" in b for b in full["best"])
    rows = search.find_modifiers(zealot_eft, ["tank.ehp.total"], sources=["rig"])
    assert all(isinstance(n, int) for n in rows["excluded"].values())
    swaps = search.marginal_swaps(zealot_eft, "tank.ehp.total")
    assert all(isinstance(n, int) for n in swaps["coverage"]["excluded"].values())
```

In `test_t2_assault_damage_control_is_excluded_with_a_reason`, add `verbose=True` to the `find_modifiers` call.

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -k "t4_compact or verbose_brings or t2_assault" -v`
Expected: FAIL (`verbose` is an unexpected keyword; `considered["low"]` is a list).

- [ ] **Step 3: Implement**

In `pyfa_mcp/search.py`, add after `_excluded_rows`:

```python
def _excluded_counts(excluded: list[dict]) -> dict:
    return dict(sorted(Counter(e["reason"] for e in excluded).items()))
```

Add after `_polish`:

```python
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
    near = [{"name": n, "reason": why} for n, why in sorted(pruned.items())
            if why.startswith("dominated") and n in info and info[n].group in fitted]
    return {"counts": dict(sorted(counts.items())), "near_winners": near[:_NEAR_WINNERS]}


def _diff(first: dict, other: dict) -> dict:
    a, b = (Counter(line for line in fit["eft"].splitlines()[1:] if line.strip())
            for fit in (first, other))
    return {"objective_value": other["objective_value"], "valid": other["valid"],
            "diff": {"remove": sorted((a - b).elements()), "add": sorted((b - a).elements())}}
```

`find_modifiers`: add `verbose: bool = False` as the last parameter and change its `"excluded"` entry to:

```python
        "excluded": (_excluded_rows if verbose else _excluded_counts)(found.excluded + failed),
```

`marginal_swaps`: add `verbose: bool = False` as the last parameter and change the `coverage` entry `"excluded": _excluded_rows(found.excluded)` to:

```python
                         "excluded": (_excluded_rows if verbose else _excluded_counts)(
                             found.excluded)}}
```

`optimize_fit`: add `verbose: bool = False` as the last parameter. Replace everything from `out = {"applied": applied, "best": best}` to the end of the function's `return {...}` with:

```python
    out = {"applied": applied,
           "best": best if verbose or not best else best[:1] + [_diff(best[0], b)
                                                              for b in best[1:]]}
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
```

In `pyfa_mcp/server.py`, add `verbose: bool = False` as the last parameter of `find_modifiers`, `marginal_swaps` and `optimize_fit`, pass `verbose=verbose` to the `search.` call in each, and append this sentence to each of the three docstrings:

```
    Output is compact (excluded and pruned items as counts by reason);
    verbose=true lists every item.
```

For `optimize_fit` the sentence is instead:

```
    Output is compact: counts for considered, pruned and excluded items, and
    best[1:] as a diff against best[0]; verbose=true lists everything whole.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py tests/test_server.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/search.py pyfa_mcp/server.py tests/test_search.py
git commit -m "Compact search output by default; verbose=true restores every name

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Availability limits and filter

**Files:**
- Modify: `pyfa_mcp/catalog.py` (`limits`, `client_build`, `_serenity_ids`, `_slot`, `_row`, `item_info`)
- Modify: `pyfa_mcp/candidates.py` (`availability_filter`, `item_filter`, `build`, `_charges`, `_local`, `_pod`, `_bursts`, `_phenomena`, `Candidate.limits`, `Pool.availability_note`)
- Modify: `pyfa_mcp/search.py` (`Option.limits`, `_options`, `_pool`, `_row`, `_groups`, three tools' `availability`)
- Modify: `pyfa_mcp/server.py` (`status`, three tools)
- Test: `tests/test_catalog.py`, `tests/test_search.py`

**Interfaces:**
- Produces: `catalog.limits(item) -> list[str]` (`"Serenity only"`, `"characters under N days"`, `"expires YYYY-MM-DD"`); `catalog.client_build() -> str | None`; `candidates.availability_filter(availability) -> (predicate(item) -> bool, note)`; `candidates.item_filter(meta, availability) -> predicate(item) -> bool`; `candidates.build(fit, sources, meta, availability=None)`; `candidates._bursts(ok)` and `candidates._phenomena(ok, excluded)` take an item predicate; `availability: str | None = None` before `verbose` on the three search functions.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_catalog.py`:

```python
def test_limits_and_pod_slots(booted):
    import eos.db
    chips = {r["name"]: r for r in catalog.search_items("Capsuleer Defense")}
    assert chips["Advanced Capsuleer Defense Augmentation Chip"]["limits"] == [
        "Serenity only", "characters under 100 days"]
    dose = catalog.search_items("Agency 'Hardshell' TB3")[0]
    assert "limits" not in dose and dose["slot"].startswith("booster ")
    assert catalog.limits(eos.db.getItem("Imperial Electronics Booster I")) == [
        "expires 2026-11-10"]
    assert catalog.item_info("Halcyon B-5 Booster")["slot"] == "booster 5"
    assert catalog.client_build()
```

Append to `tests/test_search.py`:

```python
def test_t5_limited_items_are_hidden_unless_asked(booted, no_fits_left):
    hidden = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["booster"],
                                   expand=["*"])
    assert not [n for n in _names(hidden) if "Capsuleer" in n or n.startswith("Serenity")]
    assert 'availability="all"' in hidden["applied"]["availability"]
    shown = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["booster"],
                                  availability="all", expand=["*"])
    chip = next(c for c in shown["candidates"]
                if c["name"] == "Advanced Capsuleer Defense Augmentation Chip")
    assert chip["limits"] == ["Serenity only", "characters under 100 days"]
    pods = {"slots": [], "boosters": True}
    tq = search.optimize_fit("Rifter", "tank.ehp.total", allow=pods, top_k=1)
    assert "Capsuleer" not in tq["best"][0]["eft"]
    everything = search.optimize_fit("Rifter", "tank.ehp.total", allow=pods, top_k=1,
                                     availability="all")
    assert "Capsuleer" in everything["best"][0]["eft"]


def test_availability_names_its_values(booted, zealot_eft):
    for wrong in ("TQ", "theoretical"):
        with pytest.raises(ValueError, match='availability: use "tq" .* or "all"'):
            search.find_modifiers(zealot_eft, ["tank.ehp.total"], availability=wrong)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_catalog.py tests/test_search.py -k "limits_and_pod or t5_limited or availability_names" -v`
Expected: FAIL (`limits` key missing; `availability` is an unexpected keyword).

- [ ] **Step 3: Implement catalog**

In `pyfa_mcp/catalog.py`, add `import datetime` to the imports, then add after `_meta`:

```python
@functools.cache
def _serenity_ids() -> frozenset:
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT typeID FROM invtypes "
            "WHERE typeDescription LIKE '%available on Serenity%'").fetchall()
    return frozenset(r[0] for r in rows)


def limits(item) -> list[str]:
    """Why most pilots cannot use `item`; empty when anyone on Tranquility can."""
    out = []
    if item.ID in _serenity_ids() or item.name.startswith("Serenity "):
        out.append("Serenity only")
    hours = item.getAttribute("boosterMaxCharAgeHours")
    if hours:
        out.append(f"characters under {round(hours / 24)} days")
    days = item.getAttribute("boosterLastInjectionDatetime")  # days since 1970-01-01
    if days:
        expires = datetime.date(1970, 1, 1) + datetime.timedelta(days=int(days))
        out.append(f"expires {expires.isoformat()}")
    return out


@functools.cache
def client_build() -> str | None:
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        meta = dict(connection.exec_driver_sql(
            "SELECT field_name, field_value FROM metadata").fetchall())
    return meta.get("client_build")
```

Replace `_slot` and `_row` with:

```python
def _slot(item) -> str | None:
    for effect, slot in _SLOT_EFFECTS.items():
        if effect in item.effects:
            return slot
    if item.category.name == "Implant":
        for attr, kind in (("implantness", "implant"), ("boosterness", "booster")):
            value = item.getAttribute(attr)
            if value:
                return f"{kind} {int(value)}"
    return None


def _row(item) -> dict:
    row = {
        "name": item.name, "type_id": item.ID, "group": item.group.name,
        "category": item.category.name, "meta": _meta(item), "slot": _slot(item),
        "cpu": item.getAttribute("cpu"), "powergrid": item.getAttribute("power"),
    }
    found = limits(item)
    if found:
        row["limits"] = found
    return row
```

In `item_info`, replace the first line of the returned dict with:

```python
        **{k: v for k, v in _row(item).items()
           if k not in ("cpu", "powergrid") and not (k == "slot" and v is None)},
```

`candidates.build` calls `catalog._slot` only for non-implants, so the new implant branch never reaches it.

- [ ] **Step 4: Implement candidates**

In `pyfa_mcp/candidates.py`:

Add to `Candidate` (after `modifies`):

```python
    limits: tuple = ()
    note: str | None = None  # how it was measured, when that needs saying
```

Add to `Pool` (after `meta_note`):

```python
    availability_note: str = ""
```

Add after `meta_filter`:

```python
def availability_filter(availability: str | None):
    if availability in (None, "tq"):
        return (lambda item: not catalog.limits(item),
                "Tranquility items anyone can use (default): Serenity-only, "
                "character-age-limited and expiring items are left out; pass "
                "availability=\"all\" to include them")
    if availability == "all":
        return (lambda item: True), "every item, limited ones included"
    raise ValueError(f'availability: use "tq" (the default) or "all", not {availability!r}')


def item_filter(meta: list[str] | None, availability: str | None):
    """Whether an item passes both the meta and the availability filter."""
    allowed, _ = meta_filter(meta)
    open_, _ = availability_filter(availability)
    return lambda item: allowed(catalog._meta(item)) and open_(item)
```

In `_local`, add `limits=tuple(catalog.limits(item)),` to the `Candidate(...)` arguments. In `_pod`, add the same argument to its `Candidate(...)`.

Replace `_charges`:

```python
def _charges(item, ok) -> list:
    return [c for c in catalog.valid_charges(item) if ok(c)]
```

In `_bursts` and `_phenomena`, rename the parameter `allowed` to `ok` and replace `if not allowed(catalog._meta(item)):` with `if not ok(item):`.

Replace the start of `build` up to `scanned = len(items)` with:

```python
def build(fit, sources: set[str], meta: list[str] | None,
          availability: str | None = None) -> Pool:
    from eos.saveddata.module import Module

    allowed, note = meta_filter(meta)
    open_, availability_note = availability_filter(availability)
    ok = item_filter(meta, availability)
    found: list[Candidate] = []
    excluded: list[dict] = []
    items = catalog.published_items(categories=("Module", "Subsystem", "Implant"))
    for item in items:
        meta_name = catalog._meta(item)
        if not allowed(meta_name):
            excluded.append(_excluded(item, f"meta {meta_name} not requested"))
            continue
        if not open_(item):
            excluded.append(_excluded(item, catalog.limits(item)[0]))
            continue
        if item.category.name == "Implant":
            found += _pod(item, sources)
            continue
        if "projected" in sources and item.isType("projected"):
            found.append(_projected(item, "projected", "active"))
        slot = catalog._slot(item)
        if slot is None:
            continue
        source = slot if slot in ("rig", "subsystem") else "module"
        wants_charges = source == "module" and "charge" in sources
        if source not in sources and not wants_charges:
            continue
        reason = why_not(fit, item)
        if reason:
            excluded.append(_excluded(item, reason))
            continue
        if source in sources:
            found.append(_local(item, source, slot))
        if wants_charges:
            found += [_local(item, "charge", slot, c) for c in _charges(item, ok)]
    scanned = len(items)
```

In the rest of `build`, change `_bursts(allowed)` to `_bursts(ok)`, `_phenomena(allowed, excluded)` to `_phenomena(ok, excluded)`, and the final line to:

```python
    return Pool(found, excluded, duplicates, scanned, note, availability_note)
```

- [ ] **Step 5: Implement search**

In `pyfa_mcp/search.py`:

Add a field to `Option` after `calibration`:

```python
    limits: tuple = ()
```

In `_options`, pass `limits=c.limits` to both `Option(...)` constructions. For the pod one: `Option(place, c.type_id, None, None, c.name, c.group, limits=c.limits)`. For the rack one, add `limits=c.limits` after `c.calibration`.

Replace `_pool`:

```python
def _pool(fit, sources: set[str], meta: list[str] | None, availability: str | None = None):
    subs = tuple(sorted(m.item.ID for m in fit.modules if not m.isEmpty and m.slot == 5))
    key = (fit.ship.item.ID, subs, json.dumps(meta), tuple(sorted(sources)), availability)
    return _cached(_POOLS, key, lambda: candidates.build(fit, sources, meta, availability))
```

In `_row` (find_modifiers rows), change the returned dict's last line to:

```python
            "notes": notes, **({"limits": list(c.limits)} if c.limits else {})}
```

In `_groups`, change the `"best"` entry to:

```python
            "best": {**{k: top[k] for k in ("name", "meta", "cpu", "pg", "calibration")},
                     "delta": top["delta"], **({"limits": top["limits"]} if "limits" in top
                                              else {})},
```

`find_modifiers`: add `availability: str | None = None` before `verbose`. As the first line of its body, add `candidates.availability_filter(availability)  # a typo fails before any work`. Change `found = _pool(b.fit, wanted, meta)` to `found = _pool(b.fit, wanted, meta, availability)` and its `applied` line to:

```python
        applied = {**b.applied, "meta": found.meta_note,
                   "availability": found.availability_note}
```

`marginal_swaps`: the same parameter, first line, `_pool(..., availability)` and `applied` change. Then carry limits into its rows. Change the two `labels.append` lines to:

```python
                labels.append((place, removed, None, ()))
```
```python
                labels.append((place, removed, option.name, option.limits))
```

and the results loop to:

```python
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
```

`optimize_fit`: add `availability: str | None = None` before `verbose`. Add `candidates.availability_filter(availability)` right after `_numeric(...)`. Change `found = _pool(b.fit, sources, meta)` to `found = _pool(b.fit, sources, meta, availability)`. Change the `applied` assignment to:

```python
        applied = {**b.applied, "meta": found.meta_note,
                   "availability": found.availability_note,
                   "heat": " and ".join(allow["module_states"]), "left_out": left_out}
```

In `_pruned_summary`, change the `near` comprehension to carry limits:

```python
    near = [{"name": n, "reason": why,
             **({"limits": list(info[n].limits)} if info[n].limits else {})}
            for n, why in sorted(pruned.items())
            if why.startswith("dominated") and n in info and info[n].group in fitted]
```

- [ ] **Step 6: Implement server**

In `pyfa_mcp/server.py`:
- `status`: replace the `with eos.db.gamedata_engine.connect()...` block and `meta.get("client_build")` with `"game_client_build": catalog.client_build(),`, and drop the now-unused `import eos.db`.
- `find_modifiers`, `marginal_swaps`, `optimize_fit`: add `availability: str | None = None` before `verbose`, pass `availability=availability`, and in each docstring after the Officer/Deadspace sentence add:

```
    Serenity-only, character-age-limited and expiring items are left out unless
    availability="all" (default "tq"); rows show an item's `limits`.
```

- `search_items` docstring: change "Returns name, group, category, meta, slot (high/mid/low/rig/subsystem), cpu, powergrid." to "Returns name, group, category, meta, slot (high/mid/low/rig/subsystem, \"implant 7\", \"booster 5\"), cpu, powergrid, and `limits` when most pilots cannot use it (Serenity only, character age, expiry)."

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_catalog.py tests/test_candidates.py tests/test_search.py tests/test_server.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add pyfa_mcp/catalog.py pyfa_mcp/candidates.py pyfa_mcp/search.py pyfa_mcp/server.py tests/test_catalog.py tests/test_search.py
git commit -m "Hide Serenity-only, age-limited and expiring items by default; show item limits and pod slots

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Ships by capability

**Files:**
- Modify: `pyfa_mcp/catalog.py` (`_can_fit_items`, `_bonus_lines`, `list_ships`)
- Modify: `pyfa_mcp/server.py` (`list_ships`)
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces: `catalog.list_ships(group=None, race=None, can_fit=None, bonus=None) -> list[dict]`; with `bonus`, rows gain `bonuses: [{"line", "per": "level"|"role", "at_all_v": float|None}]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_catalog.py`:

```python
def test_t6_ships_by_capability(booted):
    bursts = {s["name"] for s in catalog.list_ships(can_fit="command burst")}
    assert {"Salvation", "Simurgh", "Gaia", "Ymir", "Nighthawk", "Ferox"} <= bursts
    assert "Rifter" not in bursts
    rows = catalog.list_ships(bonus="Shield Command burst strength")
    names = [r["name"] for r in rows]
    assert names[:2] == ["Simurgh", "Ymir"]
    assert {"Nighthawk", "Vulture", "Chimera", "Wyvern"} <= set(names)
    top = rows[0]["bonuses"][0]
    assert top["at_all_v"] == 25.0 and top["per"] == "level"
    assert "Shield Command" in top["line"]
    with pytest.raises(catalog.CatalogError, match="can_fit"):
        catalog.list_ships(can_fit="Comand Brust")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_catalog.py -k t6_ships -v`
Expected: FAIL (unexpected keyword `can_fit`).

- [ ] **Step 3: Implement**

In `pyfa_mcp/catalog.py`, add before `list_ships`:

```python
def _can_fit_items(name: str) -> list:
    """The items an item-group name stands for, or the one item named."""
    import eos.db
    from sqlalchemy import func
    from eos.gamedata import Group, Item
    from service.market import Market

    items = (eos.db.gamedata_session.query(Item).join(Group)
             .filter(Item.published == True,  # noqa: E712
                     func.lower(Group.name) == name.strip().casefold())
             .order_by(Item.ID).all())
    if items:
        return items
    try:
        item = Market.getInstance().getItem(name.strip())
    except Exception:
        item = None
    if item is None or not item.published:
        close = suggest(name)
        raise CatalogError(f"can_fit: no item or item group named '{name}'"
                           + (f" (did you mean: {', '.join(close)}?)" if close else ""))
    return [item]


def _bonus_lines(ship, words: list[str]) -> list[dict]:
    """Trait lines holding every word, with the bonus at All V."""
    text = _plain(ship.traits.display) if ship.traits is not None else ""
    per, out = "role", []
    for raw in text.splitlines():
        line = raw.strip().lstrip("•").strip()
        if line.endswith(":"):  # a section header: "... bonuses (per skill level):"
            per = "level" if "per skill level" in line.casefold() else "role"
            continue
        if line and all(w in line.casefold() for w in words):
            number = re.match(r"([\d.]+)%", line)
            value = None if number is None else float(number.group(1)) * (
                5 if per == "level" else 1)
            out.append({"line": line, "per": per, "at_all_v": value})
    return out
```

Replace `list_ships` with:

```python
def list_ships(group: str | None = None, race: str | None = None,
               can_fit: str | None = None, bonus: str | None = None) -> list[dict]:
    ships = _ship_items()
    if group:
        groups = sorted({s.group.name for s in ships})
        if group.casefold() not in {g.casefold() for g in groups}:
            close = difflib.get_close_matches(group, groups, n=3, cutoff=0.5)
            raise CatalogError(f"unknown ship group '{group}'"
                               + (f" (did you mean: {', '.join(close)}?)" if close else "")
                               + f"; groups: {', '.join(groups)}")
        ships = [s for s in ships if s.group.name.casefold() == group.casefold()]
    if race:
        ships = [s for s in ships if (s.race or "").casefold() == race.casefold()]
    if can_fit:
        from eos.saveddata.fit import Fit
        from eos.saveddata.ship import Ship
        from pyfa_mcp.candidates import why_not

        wanted = _can_fit_items(can_fit)

        def fits(ship) -> bool:
            try:
                fit = Fit(Ship(ship))
            except Exception:
                return False
            return any(why_not(fit, item) is None for item in wanted)
        ships = [s for s in ships if fits(s)]

    def attr(item, name):
        return int(item.getAttribute(name) or 0)

    words = bonus.casefold().split() if bonus else []
    rows = []
    for s in ships:
        row = {
            "name": s.name, "type_id": s.ID, "group": s.group.name, "race": s.race,
            "slots": {"high": attr(s, "hiSlots"), "mid": attr(s, "medSlots"),
                      "low": attr(s, "lowSlots"), "rig": attr(s, "rigSlots")},
            "hardpoints": {"turret": attr(s, "turretSlotsLeft"),
                           "launcher": attr(s, "launcherSlotsLeft")},
            "drones": {"bandwidth": attr(s, "droneBandwidth"), "bay": attr(s, "droneCapacity")},
        }
        if words:
            row["bonuses"] = _bonus_lines(s, words)
            if not row["bonuses"]:
                continue
        rows.append(row)
    if words:
        return sorted(rows, key=lambda r: (-max(b["at_all_v"] or 0.0 for b in r["bonuses"]),
                                           r["name"]))
    return sorted(rows, key=lambda r: r["name"])
```

In `pyfa_mcp/server.py`, replace the `list_ships` tool with:

```python
@app.tool()
@_tool
def list_ships(group: str | None = None, race: str | None = None,
               can_fit: str | None = None, bonus: str | None = None) -> list:
    """Ships with slot, hardpoint and drone layouts. group: e.g. Battleship,
    Heavy Assault Cruiser, Carrier. race: amarr, caldari, gallente, minmatar, ...
    can_fit: an item or item group name ("Command Burst"): only hulls that can fit
    it (T3 cruisers need subsystems for their slots and don't match).
    bonus: words that must all appear in one trait line ("Shield Command burst
    strength"); rows then carry the matching `bonuses` with their value at All V,
    sorted strongest first. Use these to find hulls rather than recalling them:
    the game data may be newer than your training."""
    return catalog.list_ships(group, race, can_fit, bonus)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_catalog.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/catalog.py pyfa_mcp/server.py tests/test_catalog.py
git commit -m "list_ships: find hulls by what they can fit and by trait bonus

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Burst strength table; find_modifiers measures from the strongest source

**Files:**
- Create: `pyfa_mcp/fleet.py`
- Modify: `pyfa_mcp/candidates.py` (`_bursts`, drop `_BURST_HULL`)
- Modify: `pyfa_mcp/search.py` (`_row` note)
- Test: create `tests/test_fleet.py`; modify `tests/test_candidates.py` (the Ferox assertion) and `tests/test_search.py`

**Interfaces:**
- Consumes: `catalog.client_build()`, `catalog.valid_charges`, `catalog.published_items`, `catalog._plain`, `candidates.why_not`, `bench.Bench`, `bench.Edit`.
- Produces:
  - `fleet.table() -> dict` (cached; `{"hulls": {family: [[hull, strength], ...]}, "mindlinks": {family: [[name, factor], ...]}, "modules": {module: {"family", "factor", "charges"}}}`)
  - `fleet.sources(module: str, ok) -> list[{"module", "hull", "mindlink", "strength"}]`, strongest first
  - `fleet.by_charge(ok) -> dict[charge, list[source]]`, one per hull, strongest first
  - `fleet.booster_eft(hull, lines: list[(module, charge)], mindlink) -> str`
  - `fleet.booster_fits(chosen: list[{"hull", "mindlink", "module", "charge"}]) -> list[str]`
  - `fleet._strength(fit) -> float`
  - `ok` is an item predicate (`candidates.item_filter`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fleet.py`:

```python
import json

from pyfa_mcp import catalog, eosboot, evaluate, fleet


def _measured(hull: str) -> float:
    from service.fit import Fit as FitService
    text = fleet.booster_eft(hull, [("Shield Command Burst II", "Active Shielding Charge")],
                             None)
    with evaluate.Scratch() as scratch:
        fit = scratch.add_fit(text)
        FitService.getInstance().recalc(fit)
        return fleet._strength(fit)


def test_t7_the_strongest_shield_burst_source(booted, no_fits_left):
    rows = fleet.sources("Shield Command Burst II", lambda item: True)
    best = rows[0]
    assert best["hull"] in ("Simurgh", "Ymir")
    assert best["mindlink"] in ("Caldari Navy Command Mindlink", "Shield Command Mindlink")
    hulls = [r["hull"] for r in rows]
    assert hulls.index("Nighthawk") < hulls.index("Vulture")
    # the table measured one charge per module; another charge ranks hulls the same
    assert _measured(best["hull"]) > _measured("Nighthawk") > _measured("Vulture")


def test_by_charge_and_booster_fits(booted, no_fits_left):
    harmonizing = fleet.by_charge(lambda item: True)["Shield Harmonizing Charge"]
    assert harmonizing[0]["hull"] in ("Simurgh", "Ymir")
    assert len({s["hull"] for s in harmonizing}) == len(harmonizing)
    chosen = [{**harmonizing[0], "charge": c} for c in
              ("Shield Harmonizing Charge", "Shield Extension Charge", "Active Shielding Charge")]
    for text in fleet.booster_fits(chosen):
        assert evaluate.evaluate(text, None)["validity"]["valid"] is True


def test_a_damaged_table_file_is_rebuilt(booted, no_fits_left):
    path = eosboot.booted_dir() / f"burst_sources-{catalog.client_build()}.json"
    fleet.table()
    path.write_text("{not json", encoding="utf-8")
    fleet.table.cache_clear()
    assert fleet.table()["modules"]["Shield Command Burst II"]
    assert json.loads(path.read_text(encoding="utf-8"))["modules"]
```

Append to `tests/test_search.py`:

```python
def test_t8_bursts_are_measured_from_the_strongest_source(booted, no_fits_left):
    result = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["command_burst"],
                                   expand=["*"])
    row = next(c for c in result["candidates"]
               if c["name"] == "Shield Command Burst II + Shield Harmonizing Charge")
    assert any(n.startswith(("measured from Simurgh", "measured from Ymir"))
               for n in row["notes"])
    vulture = "[Vulture, b]\n\n\nShield Command Burst II, Shield Harmonizing Charge\n"
    by_vulture = (_ehp("[Wyvern, x]\n", {"command": [{"fit": vulture}]})
                  - _ehp("[Wyvern, x]\n", None))
    assert row["delta"]["tank.ehp.total"] > by_vulture
```

In `tests/test_candidates.py::test_pod_sets_and_external_sources`, change

```python
    assert burst.extra["command"][0]["fit"].startswith("[Ferox,")
```

to

```python
    assert burst.extra["command"][0]["fit"].startswith(("[Simurgh,", "[Ymir,"))
    assert burst.note.startswith("measured from")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fleet.py tests/test_candidates.py -k "t7 or by_charge or damaged or pod_sets" -v`
Expected: FAIL (`ModuleNotFoundError: pyfa_mcp.fleet`; the burst booster is a Ferox).

- [ ] **Step 3: Create `pyfa_mcp/fleet.py`**

```python
"""Who boosts best: the strongest source of every command burst, from the game data.

A burst's strength is hull bonus x module x mindlink x skills (All V), each
scaling the module's warfareBuff values, so they are measured apart: every hull
that has a burst bonus (plus one unbonused hull as the floor) per burst family,
then the mindlinks and each module variant on the family's best hull. Pyfa keeps
only the strongest value per buff (Fit.addCommandBonus): the best source for a
charge is the strongest one, and different charges stack.

The table depends only on the game data. It is built once (a few seconds) and
kept in the data dir per game build.
"""
from __future__ import annotations

import functools
import json
import re

from pyfa_mcp import catalog
from pyfa_mcp.bench import Edit

_BONUS = re.compile(r"burst (effect )?strength", re.IGNORECASE)


def _strength(fit) -> float:
    mod = next(m for m in fit.modules if not m.isEmpty)
    return max(abs(mod.getModifiedItemAttr(f"warfareBuff{i}Value") or 0.0)
               for i in range(1, 5))


def _measure(b, edits) -> float | None:
    from service.fit import Fit as FitService
    try:
        undo = b.apply(edits)
    except ValueError:  # BenchError, or eos refusing the item
        return None
    try:
        FitService.getInstance().recalc(b.fit)
        return _strength(b.fit)
    finally:
        b.revert(undo)


def _bonused(ship) -> bool:
    return ship.traits is not None and bool(_BONUS.search(catalog._plain(ship.traits.display)))


def _bench(hull: str, module: str, charge: str):
    from pyfa_mcp import bench
    return bench.Bench(f"[{hull}, x]\n\n\n{module}, {charge}\n", {})


def _burst_at(b) -> int:
    return next(i for i, m in enumerate(b.fit.modules) if not m.isEmpty)


def _build() -> dict:
    from eos.saveddata.fit import Fit
    from eos.saveddata.ship import Ship
    from pyfa_mcp.candidates import why_not

    families: dict[str, list] = {}  # first module's name -> modules taking its charges
    charges: dict[str, list] = {}   # module name -> its charges (items)
    first_of: dict[tuple, str] = {}
    for item in catalog.published_items(groups=("Command Burst",)):
        valid = catalog.valid_charges(item)
        if not valid:
            continue
        charges[item.name] = valid
        family = first_of.setdefault(tuple(c.ID for c in valid), item.name)
        families.setdefault(family, []).append(item)
    reps = {family: members[0] for family, members in families.items()}

    can: dict[str, list] = {family: [] for family in reps}
    for ship in catalog.published_items(categories=("Ship",)):
        try:
            fit = Fit(Ship(ship))
        except Exception:
            continue
        for family, rep in reps.items():
            if why_not(fit, rep) is None:
                can[family].append(ship)
    per_ship: dict = {}
    for family, ships in can.items():
        floor = [s for s in ships if not _bonused(s)][:1]  # unbonused hulls all tie
        for ship in [s for s in ships if _bonused(s)] + floor:
            per_ship.setdefault(ship, []).append(family)

    hulls: dict[str, list] = {family: [] for family in reps}
    for ship, fams in sorted(per_ship.items(), key=lambda p: p[0].ID):
        rep = reps[fams[0]]
        with _bench(ship.name, rep.name, charges[rep.name][0].name) as b:
            at = _burst_at(b)
            for family in fams:
                r = reps[family]
                value = _measure(b, [Edit(("module", at), r.ID, charges[r.name][0].ID)])
                if value:
                    hulls[family].append((ship.ID, ship.name, value))

    mindlinks: dict[str, list] = {}
    modules: dict[str, dict] = {}
    for family, found in hulls.items():
        if not found:
            continue
        found.sort(key=lambda h: (-h[2], h[0]))  # strongest; ties: the older hull
        _, hull, base = found[0]
        rep = reps[family]
        with _bench(hull, rep.name, charges[rep.name][0].name) as b:
            at = _burst_at(b)
            links = []
            for link in catalog.published_items(groups=("Cyber Leadership",)):
                value = _measure(b, [Edit(("implant", 10), link.ID)])
                if value and value > base * (1 + 1e-9):
                    links.append([link.name, value / base])
            mindlinks[family] = sorted(links, key=lambda l: (-l[1], l[0]))
            for item in families[family]:
                value = _measure(b, [Edit(("module", at), item.ID, charges[item.name][0].ID)])
                if value:
                    modules[item.name] = {"family": family, "factor": value / base,
                                          "charges": [c.name for c in charges[item.name]]}
    return {"hulls": {f: [[name, s] for _, name, s in found] for f, found in hulls.items()
                      if found},
            "mindlinks": mindlinks, "modules": modules}


@functools.cache
def table() -> dict:
    from pyfa_mcp import eosboot
    path = eosboot.booted_dir() / f"burst_sources-{catalog.client_build()}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    data = _build()
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass  # kept for this process only
    return data


def sources(module: str, ok) -> list[dict]:
    """Every hull for this burst module, strongest first, with the strongest
    mindlink `ok` allows."""
    import eos.db
    t = table()
    entry = t["modules"].get(module)
    if entry is None:
        return []
    family = entry["family"]
    link, factor = next(((n, f) for n, f in t["mindlinks"].get(family, [])
                         if ok(eos.db.getItem(n))), (None, 1.0))
    return [{"module": module, "hull": hull, "mindlink": link,
             "strength": s * entry["factor"] * factor} for hull, s in t["hulls"][family]]


def by_charge(ok) -> dict[str, list[dict]]:
    """Every burst charge -> its sources over every module `ok` allows, strongest
    first, one per hull."""
    import eos.db
    out: dict[str, list] = {}
    for module, entry in table()["modules"].items():
        if not ok(eos.db.getItem(module)):
            continue
        for charge in entry["charges"]:
            out.setdefault(charge, []).extend(sources(module, ok))
    for charge, found in out.items():
        found.sort(key=lambda s: -s["strength"])  # stable: table order breaks ties
        seen: set = set()
        out[charge] = [s for s in found if not (s["hull"] in seen or seen.add(s["hull"]))]
    return out


def booster_eft(hull: str, lines, mindlink: str | None) -> str:
    text = f"[{hull}, Fleet boosts]\n\n\n" + "".join(f"{m}, {c}\n" for m, c in lines)
    return text + (f"\n\n{mindlink}\n" if mindlink else "")


def _valid(hull: str, lines: list, mindlink: str | None) -> list[str]:
    from pyfa_mcp import evaluate
    text = booster_eft(hull, lines, mindlink)
    if len(lines) == 1 or evaluate.evaluate(text, None)["validity"]["valid"]:
        return [text]
    half = len(lines) // 2
    return _valid(hull, lines[:half], mindlink) + _valid(hull, lines[half:], mindlink)


def booster_fits(chosen: list[dict]) -> list[str]:
    """Booster fits carrying the chosen sources: one per hull and mindlink, split
    until Pyfa calls each valid (a hull runs only so many bursts)."""
    groups: dict[tuple, list] = {}
    for s in chosen:
        groups.setdefault((s["hull"], s["mindlink"]), []).append((s["module"], s["charge"]))
    return [text for (hull, link), lines in groups.items()
            for text in _valid(hull, lines, link)]
```

- [ ] **Step 4: Use it in `candidates._bursts` and the find_modifiers note**

In `pyfa_mcp/candidates.py`, delete `_BURST_HULL` and its comment, then replace `_bursts` with:

```python
def _bursts(ok) -> list[Candidate]:
    from pyfa_mcp import fleet

    out = []
    for item in catalog.published_items(groups=("Command Burst",)):
        found = fleet.sources(item.name, ok) if ok(item) else []
        if not found:
            continue
        best = found[0]
        note = (f"measured from {best['hull']}"
                + (f" + {best['mindlink']}" if best["mindlink"] else "")
                + ", All V: the strongest source in the game data")
        for charge in catalog.valid_charges(item):
            booster = fleet.booster_eft(best["hull"], [(item.name, charge.name)],
                                        best["mindlink"])
            out.append(Candidate(
                name=f"{item.name} + {charge.name}", source="command_burst", slot="external",
                group=charge.name, meta=catalog._meta(item), type_id=item.ID,
                charge_id=charge.ID, extra={"command": [{"fit": booster}]}, note=note))
    return out
```

In `pyfa_mcp/search.py::_row`, replace

```python
    if c.source == "command_burst":
        notes.append("measured from an unbonused Ferox; a command ship, mindlink or "
                     "booster skills give more")
```

with

```python
    if c.note:
        notes.append(c.note)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fleet.py tests/test_candidates.py tests/test_search.py -k "t7 or by_charge or damaged or pod_sets or t8 or t1_every or t6_a_second" -v`
Expected: all PASS. Note how long the first `fleet.table()` build takes (pytest `--durations=5`). If it is over 10 s, report it in the task summary: the spec says such a build moves onto the worker pool.

- [ ] **Step 6: Commit**

```bash
git add pyfa_mcp/fleet.py pyfa_mcp/candidates.py pyfa_mcp/search.py tests/test_fleet.py tests/test_candidates.py tests/test_search.py
git commit -m "Measure command bursts from their strongest source in the game data, not a Ferox

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: optimize_fit searches the fleet

**Files:**
- Modify: `pyfa_mcp/search.py` (`_ALLOW_DEFAULT`, `_allow`, `_fleet`, `_under`, `optimize_fit`)
- Modify: `pyfa_mcp/server.py` (`optimize_fit` docstring)
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `fleet.by_charge`, `fleet.booster_eft`, `fleet.booster_fits` (Task 6); `candidates.item_filter`, `candidates._phenomena(ok, excluded)` (Task 4); `_polish`, `_ranked` (Task 1); `_confirm`, `_value` (Task 2).
- Produces: `allow.command` and `allow.phenomena` (bool, default false); result `fleet: {"bursts": [...], "phenomena": {"chosen", "deltas"} | None, "booster_fits": [...]} | None` when either is set; `applied.command` reads "chosen by the search (see fleet)".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_search.py`:

```python
FLEET_ONLY = {"slots": [], "command": True, "phenomena": True}


def test_t9_the_optimizer_picks_the_fleet(booted, no_fits_left):
    result = search.optimize_fit(wyvern.BEST_LOWS, "tank.ehp.total", allow=FLEET_ONLY,
                                 top_k=1)
    chosen = result["fleet"]
    shield = [b for b in chosen["bursts"] if "Shield Command Burst" in b["module"]]
    assert {"Shield Harmonizing Charge", "Shield Extension Charge"} <= {b["charge"]
                                                                       for b in shield}
    assert all(b["hull"] in ("Simurgh", "Ymir") and b["runners_up"] for b in shield)
    deltas = chosen["phenomena"]["deltas"]
    assert {"none", "Amarr Phenomena Generator", "Caldari Phenomena Generator"} <= set(deltas)
    for text in chosen["booster_fits"]:
        assert evaluate.evaluate(text, None)["validity"]["valid"] is True
    best = result["best"][0]
    vulture = ("[Vulture, b]\n\n\nShield Command Burst II, Shield Harmonizing Charge\n"
               "Shield Command Burst II, Shield Extension Charge\n")
    assert best["objective_value"] > _ehp(wyvern.BEST_LOWS, {"command": [{"fit": vulture}]})
    assert _ehp(best["eft"], best["conditions"]) == pytest.approx(best["objective_value"],
                                                                 rel=1e-12)
    assert result["applied"]["command"].startswith("chosen by the search")


def test_t10_a_searched_fleet_refuses_a_given_one(booted, zealot_eft):
    with pytest.raises(ValueError, match="allow.command"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", wyvern.CONDITIONS,
                            allow={"command": True})
    with pytest.raises(ValueError, match="allow.phenomena must be true or false"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"phenomena": "yes"})


def test_a_fleet_search_without_a_valid_fit_says_why(booted, no_fits_left):
    short = search.optimize_fit(OVER_PG, "tank.ehp.total", allow={"command": True},
                                budget={"evaluations": 1})
    assert short["best"] == [] and short["fleet"] is None
    assert short["reason"].startswith("the budget ran out")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -k "t9_the or t10_a or fleet_search_without" -v`
Expected: FAIL (`allow: unknown key 'command'`).

- [ ] **Step 3: Implement allow**

In `pyfa_mcp/search.py`:

```python
_ALLOW_DEFAULT = {"slots": list(_RACKS), "implants": False, "boosters": False,
                  "module_states": ["active"], "command": False, "phenomena": False}
```

In `_allow`, before `return merged`, add:

```python
    for name in ("implants", "boosters", "command", "phenomena"):
        if not isinstance(merged[name], bool):
            raise ValueError(f"allow.{name} must be true or false")
```

- [ ] **Step 4: Implement the fleet step**

Add after `_polish` in `pyfa_mcp/search.py`:

```python
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
```

- [ ] **Step 5: Wire it into `optimize_fit`**

At the top of `optimize_fit`, after `raw = _portable(raw_conditions)` and the existing `module_states` check, add:

```python
    wants_fleet = allow["command"] or allow["phenomena"]
    if wants_fleet and raw.get("command"):
        raise ValueError("allow.command/allow.phenomena: the search chooses the bursts and "
                         "phenomena; leave conditions.command empty, or turn those off")
    ok = candidates.item_filter(meta, availability)
```

Move `candidates.availability_filter(availability)` above that `ok` line if it is not there already: the call checks the value before it is used.

After `applied = {...}` inside the `with bench.Bench(...)` block, add:

```python
        if wants_fleet:
            applied["command"] = "chosen by the search (see fleet)"
```

Replace the block from `try:` (the outer one that opens the search) through `ranked = _ranked(search)` with:

```python
    searches = [search]
    fleet_command, fleet_out = [], None
    try:
        try:
            <the main search, unchanged from Task 1: from `clean = {w: None for w in places}`
             through `search.improve(seeds, sets)`>
        except _OutOfBudget:
            converged = False
        ranked = _ranked(search)
        if wants_fleet and ranked:
            fleet_command, _ = _fleet(ref, raw, search.keys, key, sign,
                                      search.edits(ranked[0][1]), allow, ok)
            if converged:  # improve the fit under that fleet
                search = _under(search, bench.merge_conditions(raw, {"command": fleet_command}))
                searches.append(search)
                try:
                    search.improve([ranked[0][1]], sets)
                except _OutOfBudget:
                    converged = False
        polish = _polish(search, polish_options, budget, started)
        ranked = _ranked(search)
        if wants_fleet and ranked:  # the fleet for the fit as polished
            fleet_command, fleet_out = _fleet(ref, raw, search.keys, key, sign,
                                              search.edits(ranked[0][1]), allow, ok)
    finally:
        for each in searches:
            each.stack.close()
    final = bench.merge_conditions(raw, {"command": fleet_command}) if fleet_command else raw
```

(The angle-bracketed line stands for the main-search lines, which stay exactly as Task 1 left them. Do not type it literally.)

In the loop that builds `best`, change `_confirm(ref, raw, ...)` to `_confirm(ref, final, ...)` and `_value(confirmed["eft"], raw, key)` to `_value(confirmed["eft"], final, key)`.

After `if best: best[0]["polish"] = polish`, build `out` as Task 3 left it, then add:

```python
    if wants_fleet:
        out["fleet"] = fleet_out
```

In `pyfa_mcp/server.py`, replace the whole `optimize_fit` docstring (it now holds Tasks 2–4 edits) with this final text:

```python
    """Search for the best fit for a stat. Use it whenever the user asks for the
    best, highest, max, min-max or optimal fit, or before recommending a module
    choice. It builds its candidates from every item that affects the stat, so it
    won't miss modules you didn't think of. fit: hull name or EFT (a start
    point). objective: stat key, "-" prefix to minimize. allow: {slots: [high,
    mid, low, rig], implants: bool, boosters: bool, module_states: [active,
    overheated], command: bool, phenomena: bool} (default: all racks, no
    implants/boosters, no overheat, no fleet search). command/phenomena: the
    search also picks the command bursts (each charge from its strongest hull,
    module and mindlink in the game data) and the phenomena generator, and
    returns them under `fleet` with runner-up hulls and booster fits; set them
    for best/max/min questions unless the user fixed the fleet, and leave
    conditions.command empty then. Otherwise command bursts, phenomena,
    projected and environment stay as conditions set them.
    locked: EFT lines that must stay. constraints: [{"stat", "eq"|"lte"|"gte":
    value}]. budget: {evaluations, seconds} (default 20000, 60). Officer and
    Deadspace items are left out unless meta includes them (meta=["all"]).
    Serenity-only, character-age-limited and expiring items are left out unless
    availability="all" (default "tq"); rows show an item's `limits`.
    Every returned fit is computed by evaluate_fit; its `conditions` reproduce
    it there (module states included: EFT has no heat), and with heat allowed
    `objective_cold` is the same fit unheated. best[0]["polish"] lists the single
    swaps a last pass made. `search.converged` says whether the search finished
    inside the budget. Output is compact: counts for considered, pruned and
    excluded items, and best[1:] as a diff against best[0]; verbose=true lists
    everything whole."""
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py tests/test_server.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/search.py pyfa_mcp/server.py tests/test_search.py
git commit -m "optimize_fit: allow.command/phenomena pick the bursts and phenomena from the game data

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: compare_fits variants

**Files:**
- Modify: `pyfa_mcp/evaluate.py` (`compare`)
- Modify: `pyfa_mcp/server.py` (`compare_fits`)
- Test: `tests/test_evaluate.py`

**Interfaces:**
- Produces: `evaluate.compare(refs, raw_conditions, keys, variants: list[dict] | None = None)`. With variants, rows carry `variant` (index) and `variant_label`, and `columns` includes `"variant"` after `"fit"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evaluate.py` (add `import pytest`, `from pyfa_mcp import evaluate` and `from tests import wyvern` at the top if missing):

```python
def test_t11_one_fit_under_five_fleets(booted, no_fits_left):
    hulls = ["Vulture", "Nighthawk", "Claymore", "Sleipnir", "Simurgh"]
    variants = [{"command": [{"fit": f"[{h}, b]\n\n\n"
                              "Shield Command Burst II, Shield Harmonizing Charge\n"}]}
                for h in hulls]
    result = evaluate.compare([wyvern.BRIEF], None, ["tank.ehp.total"], variants)
    assert [r["variant"] for r in result["rows"]] == [0, 1, 2, 3, 4]
    assert "Simurgh" in result["rows"][4]["variant_label"]
    assert result["rows"][4]["tank.ehp.total"] > result["rows"][0]["tank.ehp.total"]
    assert result["columns"] == ["fit", "variant", "tank.ehp.total"]


def test_variants_must_be_a_list_of_objects(booted):
    for wrong in ([], ["uniform"]):
        with pytest.raises(ValueError, match="variants"):
            evaluate.compare([wyvern.BRIEF], None, None, wrong)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_evaluate.py -k "t11 or variants_must" -v`
Expected: FAIL (`compare()` takes 3 positional arguments).

- [ ] **Step 3: Implement**

In `pyfa_mcp/evaluate.py`, add `import json` at the top, then replace `compare` with:

```python
def compare(refs: list[str], raw_conditions: dict | None,
            keys: list[str] | None, variants: list[dict] | None = None) -> dict:
    if variants is not None and (not variants
                                 or not all(isinstance(v, dict) for v in variants)):
        raise ValueError("variants: a non-empty list of partial conditions objects, each "
                         "merged over conditions")
    keys = list(keys) if keys else list(stats.DEFAULT_COMPARE)
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
                rows.append({"fit": _label(ref), **label, "error": f"{type(exc).__name__}: {exc}"})
                continue
            applied = applied or result["applied"]
            flat = stats.flatten({k: v for k, v in result.items()
                                  if k not in ("fit", "ship", "applied", "warnings")})
            unknown = [k for k in keys if k not in flat]
            if unknown:
                raise ValueError(f"unknown stat '{unknown[0]}'; stat keys look like "
                                 f"{', '.join(stats.DEFAULT_COMPARE[:3])}")
            rows.append({"fit": result["fit"], "ship": result["ship"], **label,
                         **{k: flat[k] for k in keys}, "warnings": result["warnings"]})
    columns = ["fit", *(["variant"] if variants else []), *keys]
    return {"applied": applied, "columns": columns, "rows": rows}
```

In `pyfa_mcp/server.py`, replace `compare_fits` with:

```python
@app.tool()
@_tool
def compare_fits(fits: list[str], conditions: dict | None = None,
                 stats: list[str] | None = None, variants: list[dict] | None = None) -> dict:
    """Evaluate many fits under the same conditions into one table. `stats` picks
    columns by dotted key from evaluate_fit's output (e.g. "tank.ehp.total",
    "offense.dps.total", "targeting.lock_range_m"); omitted = a standard set.
    variants: a list of partial conditions, each merged over `conditions` (top-level
    keys replace), giving one row per fit and variant: e.g. one fit under several
    booster fits, damage profiles or phenomena in a single call.
    A fit that fails gets an `error` in its row; the others still compute.
    To check whether a fit can be improved, use marginal_swaps."""
    return evaluate.compare(fits, conditions, stats, variants)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_evaluate.py tests/test_server.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/evaluate.py pyfa_mcp/server.py tests/test_evaluate.py
git commit -m "compare_fits: variants compare one fit under several sets of conditions

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: whats_new and server instructions

**Files:**
- Modify: `pyfa_mcp/catalog.py` (`whats_new`)
- Modify: `pyfa_mcp/server.py` (`whats_new` tool, `INSTRUCTIONS`)
- Modify: `packaging/mcp_smoke.py` (`TOOLS`)
- Test: `tests/test_catalog.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `catalog.client_build`, `catalog._row`, `catalog.published_items`.
- Produces: `catalog.whats_new(category=None, limit=30) -> {"ordered_by", "game_client_build", "items"}`; MCP tool `whats_new`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_catalog.py`:

```python
def test_t12_whats_new_lists_the_command_carriers(booted):
    result = catalog.whats_new("ship", 10)
    names = [i["name"] for i in result["items"]]
    assert {"Salvation", "Simurgh", "Gaia", "Ymir"} <= set(names)
    assert names[0] == "Ymir"
    assert "type ID" in result["ordered_by"] and result["game_client_build"]
    assert len(catalog.whats_new(limit=5)["items"]) == 5


def test_whats_new_rejects_bad_arguments(booted):
    with pytest.raises(catalog.CatalogError, match="Ship, Module"):
        catalog.whats_new("Spaceship")
    with pytest.raises(catalog.CatalogError, match="limit"):
        catalog.whats_new(limit=0)
```

In `tests/test_server.py::test_search_tools_and_redirects`, append:

```python
    assert "never from memory" in server.INSTRUCTIONS
    assert "allow.command" in server.INSTRUCTIONS and 'availability="all"' in server.INSTRUCTIONS
    assert server.whats_new(category="Ship", limit=3)["items"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_catalog.py tests/test_server.py -k "t12 or whats_new or search_tools" -v`
Expected: FAIL (`catalog` has no `whats_new`).

- [ ] **Step 3: Implement**

Append to `pyfa_mcp/catalog.py`:

```python
_NEW_CATEGORIES = ("Ship", "Module", "Implant", "Charge", "Drone", "Fighter", "Subsystem")


def whats_new(category: str | None = None, limit: int = 30) -> dict:
    if limit < 1:
        raise CatalogError("limit must be at least 1")
    categories = _NEW_CATEGORIES
    if category:
        categories = tuple(c for c in _NEW_CATEGORIES if c.casefold() == category.casefold())
        if not categories:
            raise CatalogError(f"unknown category '{category}'; one of "
                               + ", ".join(_NEW_CATEGORIES))
    items = published_items(categories=categories)[::-1][:limit]
    keep = ("name", "type_id", "group", "category", "limits")
    return {"ordered_by": "type ID: the game data carries no dates; higher IDs were "
                          "added later",
            "game_client_build": client_build(),
            "items": [{k: v for k, v in _row(i).items() if k in keep} for i in items]}
```

In `pyfa_mcp/server.py`, add after `item_info`:

```python
@app.tool()
@_tool
def whats_new(category: str | None = None, limit: int = 30) -> dict:
    """The newest items in the game data, newest first: ships, modules, implants,
    charges, drones, fighters, subsystems (category picks one). The data has no
    dates, so this orders by type ID (higher = added later). Use it when an answer
    depends on what exists: the game data may be newer than your training."""
    return catalog.whats_new(category, limit)
```

In `INSTRUCTIONS`, replace

```
- Those three leave Officer and Deadspace items out unless meta includes
  them (meta=["all"]); tell the user which you used.
```

with

```
- Those three leave Officer and Deadspace items out unless meta includes
  them (meta=["all"]), and Serenity-only, character-age-limited and
  expiring items unless availability="all"; tell the user which you used.
- For a best/max/min fit, set optimize_fit's allow.command and
  allow.phenomena unless the user fixed the fleet: it then picks the bursts
  and phenomena from the game data.
- The game data may be newer than your training. Find ships and items with
  the tools (list_ships can_fit/bonus, find_modifiers, whats_new), never
  from memory.
```

In `packaging/mcp_smoke.py`, add `"whats_new"` to the `TOOLS` set.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_catalog.py tests/test_server.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/catalog.py pyfa_mcp/server.py packaging/mcp_smoke.py tests/test_catalog.py tests/test_server.py
git commit -m "whats_new: the newest items by type ID; instructions point the agent at the data, not its memory

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Hand-test replay and full suite

**Files:**
- Modify: `pyproject.toml` (`slow` marker)
- Modify: `.github/workflows/tests.yml` (skip slow)
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the replay test**

In `pyproject.toml`, under `[tool.pytest.ini_options]`, add:

```toml
markers = ["slow: replays a whole hand-tested search (minutes); skipped in CI"]
```

Read `.github/workflows/tests.yml` and add `-m "not slow"` to its pytest command.

Append to `tests/test_search.py`:

```python
@pytest.mark.slow
def test_replay_the_hand_tested_wyvern(booted, no_fits_left):
    allow = {"slots": ["high", "mid", "low", "rig"], "implants": True, "boosters": True,
             "module_states": ["active", "overheated"], "command": True, "phenomena": True}
    result = search.optimize_fit("Wyvern", "tank.ehp.total", allow=allow, meta=["all"],
                                 top_k=1, budget={"seconds": 900})
    best = result["best"][0]
    shield = [b for b in result["fleet"]["bursts"] if "Shield Command Burst" in b["module"]]
    assert shield and all(b["hull"] in ("Simurgh", "Ymir") for b in shield)
    assert result["fleet"]["phenomena"]["chosen"] == "Caldari Phenomena Generator"
    assert not [line for line in best["eft"].splitlines()
                if "Capsuleer" in line or line.startswith("Serenity")]
    assert _ehp(best["eft"], best["conditions"]) == pytest.approx(best["objective_value"],
                                                                 rel=1e-12)
    swaps = search.marginal_swaps(best["eft"], "tank.ehp.total", best["conditions"],
                                  meta=["all"])
    assert swaps["no_improvement_found"] is True
```

- [ ] **Step 2: Run the replay**

Run: `.venv/Scripts/python.exe -m pytest tests/test_search.py -k replay -v -s`
Expected: PASS. If `marginal_swaps` finds an improving swap, report it with the swap. Expected causes: an implant or booster slot the optimizer's pruning dropped, or heat (marginal_swaps measures without heat choices). Don't loosen the assertion.

- [ ] **Step 3: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -m "not slow" -q`
Expected: all pass (the previous count was 308, plus this plan's tests).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml .github/workflows/tests.yml tests/test_search.py
git commit -m "Replay the hand-tested Wyvern search as a slow test

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
