# Fit Search Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent three tools (`find_modifiers`, `marginal_swaps`, `optimize_fit`) that measure every item that can move a stat, so it stops missing modules it didn't think to name. Also add a checklist of everything outside the hull and an `environment` condition.

**Architecture:** A `Bench` imports a fit once (with conditions) into a temporary eos fit and edits it in place: replace a module, swap an implant, project a module. Each step is one recalc, ~10 ms. `candidates.build` walks the game data once per hull and returns every legal candidate plus every exclusion with its reason. `pool.run` fans trials out to worker processes, each with its own booted eos; it starts lazily and stops after 60 s idle. `search.py` builds the three tools on top. Every fit a tool returns goes through `evaluate.evaluate` before any number is reported.

**Tech Stack:** Python 3.14, Pyfa's eos (vendored), `mcp` (MCPServer), pytest, stdlib `concurrent.futures` / `multiprocessing` (spawn). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-02-fit-search-design.md`

## Global Constraints

- Python `>=3.14,<3.15`; no new runtime or dev dependencies.
- Every number a tool reports for a returned fit comes from `evaluate.evaluate`; bench numbers are only used to rank.
- Nothing may write to the server process's stdout except the MCP stream (tool bodies already redirect; workers must too).
- Officer and Deadspace items are excluded unless `meta` includes them; `meta=["all"]` includes everything; every result's `applied.meta` says which and how to change it.
- `optimize_fit` and `marginal_swaps` never change command bursts, phenomena, projected or environment effects, drones, fighters or subsystems.
- Worker pool: `min(cores − 2, 12)` workers by default, `--workers N` overrides, `--workers 0` disables; stops after 60 s idle; jobs under 300 trials run in-process; workers exit when the server dies.
- Errors the user can fix are `ValueError` / `*Error` types listed in `server._USER_ERRORS`, with close-match suggestions where a name is involved.
- Run tests with `uv run pytest -q` (or `.venv/Scripts/python.exe -m pytest -q` on the dev box). Commits end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **Stored-fit or `pyfa:` references inside `conditions.command`/`projected` when workers run.** A worker has its own empty database, so `search._portable` must replace them with EFT before trials ship. Pinned by `test_find_modifiers_resolves_stored_fits_for_workers` (Task 7).
2. **A T3 cruiser.** Its subsystems must stay put, and its slot layout must not shift under the search. Pinned by `test_optimize_keeps_subsystems` (Task 9).
3. **A starting fit that is already invalid** (over CPU, two DCs). The search must still return a valid fit, not crash on a `None` score. Pinned by `test_optimize_recovers_from_an_invalid_start` (Task 9).
4. **Typos.** Unknown stat keys, `expand` groups, `sources`, `allow` keys or constraint shapes must give a clear `ToolError` with suggestions, not a traceback. Pinned by `test_search_input_errors` (Task 10).
5. **Worker output reaching the MCP stream.** Pyfa prints during import, and a spawned worker inherits the server's stdout. Pinned by the stdio smoke test running `find_modifiers` through a real pool with `--workers 2` (Task 10).

---

### Task 1: Refuse EFT lines that name unfittable items

Pyfa's importer silently drops a module-section line naming e.g. a component ("Capital Armor Plates"), and `dropped_modules` does not report it.

**Files:**
- Modify: `pyfa_mcp/eft.py` (`_line_errors`, new `_FITTABLE`)
- Test: `tests/test_eft.py`

**Interfaces:**
- Produces: `eft.import_fit` raises `EftError` naming the item for such lines; cargo lines (`Name xN`) still import.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_eft.py`)

```python
def test_unfittable_item_line_is_an_error(booted, no_fits_left):
    with pytest.raises(eft.EftError, match="'Capital Armor Plates' is a commodity"):
        eft.import_fit("[Wyvern, x]\nCapital Armor Plates\n", temp=True)


def test_unfittable_item_with_a_count_is_cargo(booted, no_fits_left):
    from service.fit import Fit
    fit = eft.import_fit("[Rifter, x]\n\n\n\n\nCapital Armor Plates x3\n", temp=True)
    try:
        assert [c.item.name for c in fit.cargo] == ["Capital Armor Plates"]
    finally:
        Fit.deleteFit(fit.ID)
```

(If `tests/test_eft.py` lacks `import pytest` / `from pyfa_mcp import eft`, add them.)

- [ ] **Step 2: Run them to verify the first fails**

Run: `uv run pytest tests/test_eft.py -q -k "unfittable"`
Expected: `test_unfittable_item_line_is_an_error` FAILS (no error raised); the cargo test passes.

- [ ] **Step 3: Implement**

In `pyfa_mcp/eft.py`, below `_SLOT_LABELS`:

```python
# What a line without a count may name; anything else is cargo and needs "xN".
_FITTABLE = ("Module", "Subsystem", "Implant", "Structure Module", "Charge")
```

In `_line_errors`, inside the loop after the drone/fighter check:

```python
        if count is None and item.category.name not in (*_FITTABLE, "Drone", "Fighter"):
            errors.append(f"'{item.name}' is a {item.category.name.lower()}, not something "
                          f"that can be fitted; a cargo line needs a count, e.g. "
                          f"'{item.name} x1'")
```

- [ ] **Step 4: Run the whole suite** (reference EFTs must still import)

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/eft.py tests/test_eft.py
git commit -m "Refuse EFT lines naming items that cannot be fitted instead of dropping them silently"
```

---

### Task 2: Read only the stats asked for, raw ship attributes, and caps

**Files:**
- Modify: `pyfa_mcp/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Produces:
  - `stats.read(fit, keys: list[str], spool: float) -> dict[str, value]`. It computes only the sections the keys name, and `ship.<attributeName>` reads the ship's modified attribute. Raises `ValueError("unknown stat ...")` / `ValueError("unknown ship attribute ...")`.
  - `stats.cap(fit, key: str) -> tuple[str, float] | None` gives the capping attribute's name and current value, for keys backed by one ship attribute (`targeting.lock_range_m`, `targeting.scan_resolution_mm`, `navigation.signature_m`, `ship.*`).
  - `stats.fit_stats` is unchanged for callers.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_stats.py`)

```python
import pytest

from pyfa_mcp import evaluate, stats


def _fit(scratch, text):
    from service.fit import Fit
    fit = scratch.add_fit(text)
    Fit.getInstance().recalc(fit)
    return fit


def test_read_returns_only_the_keys_asked(booted, zealot_eft, no_fits_left):
    with evaluate.Scratch() as scratch:
        fit = _fit(scratch, zealot_eft)
        got = stats.read(fit, ["tank.ehp.total", "ship.shieldCapacity"], 1.0)
        full = stats.flatten(stats.fit_stats(fit, 1.0))
    assert set(got) == {"tank.ehp.total", "ship.shieldCapacity"}
    assert got["tank.ehp.total"] == full["tank.ehp.total"]
    assert got["ship.shieldCapacity"] > 0


def test_read_does_not_run_the_cap_sim_for_tank(booted, zealot_eft, no_fits_left, monkeypatch):
    with evaluate.Scratch() as scratch:
        fit = _fit(scratch, zealot_eft)
        monkeypatch.setattr(stats, "_capacitor", lambda fit: pytest.fail("cap sim ran"))
        stats.read(fit, ["tank.ehp.total"], 1.0)


def test_read_rejects_unknown_keys(booted, zealot_eft, no_fits_left):
    with evaluate.Scratch() as scratch:
        fit = _fit(scratch, zealot_eft)
        with pytest.raises(ValueError, match="unknown stat 'tank.ehp.totl'"):
            stats.read(fit, ["tank.ehp.totl"], 1.0)
        with pytest.raises(ValueError, match="unknown ship attribute 'shieldCapacty'"):
            stats.read(fit, ["ship.shieldCapacty"], 1.0)


def test_cap_names_the_capping_attribute(booted, no_fits_left):
    sebos = "[Chimera, c]\n\n" + "Sensor Booster II, Targeting Range Script\n" * 4
    with evaluate.Scratch() as scratch:
        fit = _fit(scratch, sebos)
        assert stats.cap(fit, "targeting.lock_range_m") == ("maximumRangeCap", 750000.0)
        assert stats.read(fit, ["targeting.lock_range_m"], 1.0)["targeting.lock_range_m"] == 750000.0
        assert stats.cap(fit, "tank.ehp.total") is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_stats.py -q`
Expected: FAIL with `AttributeError: module 'pyfa_mcp.stats' has no attribute 'read'`.

- [ ] **Step 3: Implement** in `pyfa_mcp/stats.py`

Replace `fit_stats` with a section table, and add `read` / `cap` below `flatten`:

```python
_SECTIONS = {
    "validity": lambda fit, spool: _validity(fit),
    "tank": lambda fit, spool: _tank(fit),
    "offense": _offense,
    "capacitor": lambda fit, spool: _capacitor(fit),
    "navigation": lambda fit, spool: _navigation(fit),
    "targeting": lambda fit, spool: _targeting(fit),
    "drones": lambda fit, spool: _drones(fit),
}
# Stats that are one ship attribute, so a dogma cap on it caps the stat.
_STAT_ATTRS = {"targeting.lock_range_m": "maxTargetRange",
               "targeting.scan_resolution_mm": "scanResolution",
               "navigation.signature_m": "signatureRadius"}


def fit_stats(fit, spool: float) -> dict:
    return {name: section(fit, spool) for name, section in _SECTIONS.items()}
```

```python
def _attribute(name):
    import eos.db
    return eos.db.getAttributeInfo(name)


def read(fit, keys: list[str], spool: float) -> dict:
    """The asked stats only: a section is computed if a key names it (so the
    cap sim runs only for capacitor.* keys). `ship.<attribute>` reads the
    ship's modified dogma attribute."""
    flat: dict = {}
    for section in dict.fromkeys(k.split(".", 1)[0] for k in keys if not k.startswith("ship.")):
        if section in _SECTIONS:
            flat.update(flatten({section: _SECTIONS[section](fit, spool)}))
    out = {}
    for key in keys:
        if key.startswith("ship."):
            name = key[len("ship."):]
            if _attribute(name) is None:
                raise ValueError(f"unknown ship attribute '{name}' in '{key}'")
            out[key] = fit.ship.getModifiedItemAttr(name)
        elif key in flat:
            out[key] = flat[key]
        else:
            raise ValueError(f"unknown stat '{key}'; stat keys look like "
                             f"{', '.join(DEFAULT_COMPARE[:3])}, or ship.<attribute>")
    return out


def cap(fit, key: str) -> tuple[str, float] | None:
    """(capping attribute, its current value) when `key` is one ship attribute
    that dogma caps (lock range is capped by maximumRangeCap), else None."""
    name = key[len("ship."):] if key.startswith("ship.") else _STAT_ATTRS.get(key)
    info = _attribute(name) if name else None
    if info is None or not info.maxAttributeID:
        return None
    capping = _attribute(info.maxAttributeID)
    return capping.name, fit.ship.getModifiedItemAttr(capping.name)
```

- [ ] **Step 4: Run the tests and the reference suite**

Run: `uv run pytest tests/test_stats.py tests/test_reference.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/stats.py tests/test_stats.py
git commit -m "Read only the stats asked for, ship attributes, and the attribute that caps a stat"
```

---

### Task 3: Environment effects as a condition, and the beyond-the-fit checklist

**Files:**
- Modify: `pyfa_mcp/catalog.py` (new `published_items`)
- Modify: `pyfa_mcp/conditions.py`
- Test: `tests/test_conditions.py`

**Interfaces:**
- Produces:
  - `catalog.published_items(categories=(), groups=()) -> list[Item]`: published items, ordered by type id.
  - `conditions.Conditions.environment: str | None`.
  - `conditions.apply(...)["environment"]`.
  - `conditions.beyond_the_fit() -> dict` (cached). Its keys are `pod, drugs, links, phenomena, projected, environment, heat, mode_spool, skills_damage_target, drones_fighters`, each `{"how": str, "options": ...}`.
  - `conditions.describe()["beyond_the_fit"]` replaces the `in_eft_instead` key.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_conditions.py`)

```python
from pyfa_mcp import conditions, evaluate

WYVERN = "[Wyvern, env]\n"


def test_environment_changes_the_numbers_and_is_echoed(booted, no_fits_left):
    plain = evaluate.evaluate(WYVERN, None)
    pulsar = evaluate.evaluate(WYVERN, {"environment": "Class 6 Pulsar Effects"})
    assert pulsar["tank"]["hp"]["shield"] > plain["tank"]["hp"]["shield"]
    assert pulsar["applied"]["environment"] == "Class 6 Pulsar Effects"
    assert plain["applied"]["environment"] == "none (default)"


def test_environment_errors(booted, no_fits_left):
    with pytest.raises(conditions.ConditionsError, match="did you mean"):
        evaluate.evaluate(WYVERN, {"environment": "Class 6 Pulsar Efects"})
    with pytest.raises(conditions.ConditionsError, match="not a system effect"):
        evaluate.evaluate(WYVERN, {"environment": "Damage Control II"})
    with pytest.raises(conditions.ConditionsError, match="set it with environment"):
        evaluate.evaluate(WYVERN, {"projected": [{"item": "Class 6 Pulsar Effects"}]})


def test_beyond_the_fit_lists_every_category(booted):
    beyond = conditions.describe()["beyond_the_fit"]
    assert set(beyond) == {"pod", "drugs", "links", "phenomena", "projected", "environment",
                           "heat", "mode_spool", "skills_damage_target", "drones_fighters"}
    assert all(set(entry) == {"how", "options"} for entry in beyond.values())
    assert "Nirvana" in beyond["pod"]["options"]["sets"]
    assert "Shield Extension Charge" in beyond["links"]["options"]["Shield Command Burst II"]
    assert "Caldari Phenomena Generator" in beyond["phenomena"]["options"]
    assert "Stasis Web" in beyond["projected"]["options"]
    assert any("Class 6 Pulsar Effects" in names
               for names in beyond["environment"]["options"].values())
    assert beyond["drugs"]["options"]
    assert "in_eft_instead" not in conditions.describe()
```

(Add `import pytest` at the top if absent.)

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_conditions.py -q -k "environment or beyond"`
Expected: FAIL (`unknown condition 'environment'`, `KeyError: 'beyond_the_fit'`).

- [ ] **Step 3: Implement**

`pyfa_mcp/catalog.py`, after `_row`:

```python
def published_items(categories=(), groups=()) -> list:
    import eos.db
    from eos.gamedata import Category, Group, Item

    query = (eos.db.gamedata_session.query(Item).join(Group).join(Category)
             .filter(Item.published == True))  # noqa: E712
    if categories:
        query = query.filter(Category.name.in_(categories))
    if groups:
        query = query.filter(Group.name.in_(groups))
    return query.order_by(Item.ID).all()
```

`pyfa_mcp/conditions.py`:

1. Add `import functools` to the imports.
2. In `_FIELDS`, after `"mode"`:

```python
    "environment": "One system effect by name: a wormhole, abyssal weather, incursion, "
                   "faction warfare or sov hub effect (see beyond_the_fit.environment). "
                   "Default: none.",
```

3. In `Conditions`, after `mode`: `environment: str | None = None`.
4. In `parse`, before the `return`:

```python
    environment = raw.get("environment")
    if environment is not None and not isinstance(environment, str):
        raise ConditionsError("environment must be the name of a system effect, "
                              "e.g. \"Class 6 Pulsar Effects\"")
```

   and pass `environment=environment` to `Conditions(...)`.
5. In `_apply_projected`, right after `item = _item(entry["item"])`:

```python
        from eos.saveddata.module import Module
        if item.group.name in Module.SYSTEM_GROUPS:
            raise ConditionsError(f"projected: {item.name} is a system effect; "
                                  "set it with environment")
```

   The existing `if item.isDrone:` branch follows unchanged.
6. Add after `_apply_command`:

```python
def _apply_environment(fit, name: str | None) -> str | None:
    if name is None:
        return None
    from eos.saveddata.module import Module
    from gui.fitCommands.calc.module.projectedAdd import CalcAddProjectedModuleCommand
    from gui.fitCommands.helpers import ModuleInfo

    item = _item(name)
    if item.group.name not in Module.SYSTEM_GROUPS:
        raise ConditionsError(f"environment: {item.name} is not a system effect; see "
                              "conditions_format()['beyond_the_fit']['environment']")
    info = ModuleInfo(itemID=item.ID, state=_state("online"))
    if not CalcAddProjectedModuleCommand(fit.ID, info).Do():
        raise ConditionsError(f"environment: Pyfa refused to apply {item.name}")
    return item.name
```

7. In `apply`, after `projected = ...`: `environment = _apply_environment(fit, cond.environment)`. In the returned dict, after `"projected"`: `"environment": environment or _mark("none", "environment", cond),`.
8. Add the checklist above `describe`:

```python
_BOOSTER_GRADES = ("Synth", "Standard", "Improved", "Strong")


@functools.cache
def beyond_the_fit() -> dict:
    """Everything that changes a fit's numbers without being a module on the hull."""
    from eos.saveddata.module import Module
    from pyfa_mcp.catalog import published_items

    implants = published_items(categories=("Implant",))
    pods = [i for i in implants if i.group.name != "Booster"]
    boosters = [i.name for i in implants if i.group.name == "Booster"]
    sets = sorted({a[len("ImplantSet"):] for i in pods for a in i.attributes
                   if a.startswith("ImplantSet")})
    drugs = sorted({" ".join(n.split()[1:]) if n.split()[0] in _BOOSTER_GRADES else n
                    for n in boosters})
    bursts = {i.name: sorted(c.name for c in Module(i).getValidCharges() if c.published)
              for i in published_items(groups=("Command Burst",))}
    environment: dict[str, list[str]] = {}
    for item in published_items(groups=Module.SYSTEM_GROUPS):
        environment.setdefault(item.group.name, []).append(item.name)
    return {
        "pod": {"how": "EFT: implant lines after the modules, one per slot 1-10",
                "options": {"sets": sets, "slots": "1-10; search_items(category=\"Implant\")"}},
        "drugs": {"how": "EFT: booster lines; side effects via drug_side_effects",
                  "options": drugs},
        "links": {"how": "command: a booster fit carrying command bursts and their charges",
                  "options": bursts},
        "phenomena": {"how": "command: a titan fit carrying its racial phenomena generator",
                      "options": sorted(i.name for i in published_items(
                          groups=("Titan Phenomena Generator",)))},
        "projected": {"how": "projected: [{item, count?, state?}] or [{fit, count?}]",
                      "options": sorted({i.group.name for i in published_items(
                          categories=("Module",)) if i.isType("projected")})},
        "environment": {"how": "environment: one system effect by name",
                        "options": {g: sorted(n) for g, n in sorted(environment.items())}},
        "heat": {"how": "module_states: [{module, state: \"overheated\"}]", "options": []},
        "mode_spool": {"how": "mode (tactical destroyers), spool (Triglavian weapons)",
                       "options": []},
        "skills_damage_target": {"how": "character, damage_profile, target",
                                 "options": "see damage_profiles and target_profiles"},
        "drones_fighters": {"how": "EFT: 'Name xN' lines, launched up to bandwidth and skills",
                            "options": []},
    }
```

9. In `describe()`, replace the `"in_eft_instead": ...` entry with `"beyond_the_fit": beyond_the_fit(),`.

- [ ] **Step 4: Run the conditions, evaluate and server tests**

Run: `uv run pytest tests/test_conditions.py tests/test_evaluate.py tests/test_server.py -q`
Expected: all pass. If an existing test asserted `in_eft_instead`, change it to assert `beyond_the_fit`.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/catalog.py pyfa_mcp/conditions.py tests/test_conditions.py
git commit -m "Apply environment effects as a condition and list everything beyond the fit"
```

---

### Task 4: The bench, with the equality gate

The gate for everything after it: a fit changed on the bench must measure exactly what `evaluate_fit` gives for the same EFT.

**Files:**
- Create: `pyfa_mcp/bench.py`
- Create: `tests/wyvern.py` (shared fixtures for Tasks 4, 7–9)
- Test: `tests/test_bench.py`

**Interfaces:**
- Consumes: `stats.read`, `stats._validity`, `conditions.parse/apply/spool_of/_state`, `evaluate.Scratch`, `eft.export_fit`.
- Produces:
  - `bench.Edit(where: tuple, item_id: int | None, charge_id: int | None = None, state: str | None = None)`. `where` is `("module", position)`, `("implant", slot)`, `("booster", slot)` or `("projected", None)`.
  - `bench.Trial(values: dict | None, problems: list[str], error: str | None = None)`.
  - `bench.BenchError(ValueError)`.
  - `bench.Bench(ref: str, raw_conditions: dict | None)` is a context manager with:
    - attributes `.fit`, `.applied`, `.spool`
    - `measure(keys) -> dict`, `problems() -> list[str]`, `trial(edits, keys) -> Trial`
    - `apply(edits) -> undo`, `revert(undo)`, `eft() -> str`, `module_states() -> list[dict]`
    - `module_places() -> list[where]`, `rack(where) -> str` (`high|mid|low|rig|subsystem|other`)
    - `occupant(where) -> (item_id, charge_id, state) | None`, `name_of(type_id) -> str`
  - `bench.run_trials(ref, raw_conditions, keys, trials: list[(edits, extra_conditions | None)]) -> list[Trial]`. It opens one bench per distinct `extra`, and is the unit that workers run.
  - `bench.merge_conditions(raw, extra) -> dict`.
  - `bench.SLOT_LABELS`.

- [ ] **Step 1: Write the shared Wyvern fixtures** `tests/wyvern.py`

```python
"""The Wyvern from the fit search design brief, with real item names.

The brief's absolute EHP differs from these (~6% lower here, ratios equal),
so tests compare against evaluate_fit on these fits, not the brief's numbers.
"""
EXTENDER = "Dread Guristas Capital Shield Extender"
HARDENER = "Estamel's Modified Multispectrum Shield Hardener"
DC = "Cormack's Modified Damage Control"
PLATE = "CONCORD 25000mm Steel Plates"
PDS = "Chelm's Modified Power Diagnostic System"
RIG = "Capital Core Defense Field Extender II"
POD = ["High-grade Nirvana Alpha", "High-grade Nirvana Beta", "High-grade Nirvana Gamma",
       "High-grade Nirvana Delta", "High-grade Nirvana Epsilon", "High-grade Nirvana Omega",
       "Zainou 'Gnome' Shield Management SM-706",
       "Inherent Implants 'Noble' Mechanic MC-806",
       "Inherent Implants 'Noble' Hull Upgrades HG-1008"]
BOOST = ("[Nighthawk, Shield links]\n\n\n"
         "Shield Command Burst II, Shield Harmonizing Charge\n"
         "Shield Command Burst II, Active Shielding Charge\n"
         "Shield Command Burst II, Shield Extension Charge\n\n\n"
         "Caldari Navy Command Mindlink\n")
PHENOMENA = "[Leviathan, Phenomena]\n\n\nCaldari Phenomena Generator\n"
CONDITIONS = {"command": [{"fit": BOOST}, {"fit": PHENOMENA}]}
HOT = {**CONDITIONS, "module_states": [{"module": HARDENER, "state": "overheated"}]}


def fit(lows: list[str]) -> str:
    return ("[Wyvern, Brief Wyvern]\n" + "\n".join(lows) + "\n\n"
            + f"{EXTENDER}\n" * 5 + f"{HARDENER}\n" * 3 + "\n\n"
            + f"{RIG}\n" * 3 + "\n" + "\n".join(POD) + "\n")


BRIEF = fit([DC] + [PLATE] * 3)          # the agent's first answer
BEST_LOWS = fit([DC] + [PDS] * 3)        # what the agent should have found
NO_DC = fit([PDS] * 4)                   # must lose to BEST_LOWS
```

- [ ] **Step 2: Write the failing tests** `tests/test_bench.py`

```python
import json

import pytest

from pyfa_mcp import bench, evaluate, stats
from scripts.record_reference import KEYS, REF, _resolve
from tests import wyvern

CASES = json.loads((REF / "conditions.json").read_text())


def _case(name):
    spec = CASES[name]
    return (REF / f"{spec.get('fit', name)}.eft").read_text(), _resolve(spec.get("conditions"))


def _flat(result):
    return stats.flatten({k: v for k, v in result.items()
                          if k not in ("fit", "ship", "applied", "warnings")})


def _same(got, want):
    for key in KEYS:
        if isinstance(want[key], float):
            assert got[key] == pytest.approx(want[key], rel=1e-9), key
        else:
            assert got[key] == want[key], key


def _id(name):
    from service.market import Market
    return Market.getInstance().getItem(name).ID


@pytest.mark.parametrize("case", sorted(CASES))
def test_bench_matches_evaluator_after_a_change(booted, case, no_fits_left):
    fit, cond = _case(case)
    named = {e["module"].casefold() for e in (cond or {}).get("module_states", [])}
    with bench.Bench(fit, cond) as b:
        _same(b.measure(KEYS), _flat(evaluate.evaluate(fit, cond)))
        where = [w for w in b.module_places() if b.occupant(w) is not None
                 and b.name_of(b.occupant(w)[0]).casefold() not in named][-1]
        edits = [bench.Edit(where, None)]
        trial = b.trial(edits, KEYS)
        undo = b.apply(edits)
        changed, states = b.eft(), b.module_states()
        b.revert(undo)
        _same(b.measure(KEYS), _flat(evaluate.evaluate(fit, cond)))
    assert trial.error is None
    _same(trial.values, _flat(evaluate.evaluate(changed, {**(cond or {}),
                                                          "module_states": states})))


def test_bench_matches_evaluator_for_every_kind_of_edit(booted, no_fits_left):
    with bench.Bench(wyvern.BRIEF, wyvern.HOT) as b:
        plate = next(w for w in b.module_places() if b.occupant(w)
                     and b.name_of(b.occupant(w)[0]) == wyvern.PLATE)
        edits = [bench.Edit(plate, _id(wyvern.PDS)),
                 bench.Edit(("implant", 7), None),
                 bench.Edit(("projected", None), _id("Class 6 Pulsar Effects"), None, "online")]
        trial = b.trial(edits, KEYS)
        undo = b.apply(edits[:2])
        changed, states = b.eft(), b.module_states()
        b.revert(undo)
    assert "SM-706" not in changed and changed.count(wyvern.PLATE) == 2
    want = evaluate.evaluate(changed, {**wyvern.CONDITIONS, "module_states": states,
                                       "environment": "Class 6 Pulsar Effects"})
    _same(trial.values, _flat(want))


def test_trial_reports_an_invalid_change(booted, no_fits_left):
    with bench.Bench(wyvern.BRIEF, wyvern.CONDITIONS) as b:
        plate = next(w for w in b.module_places() if b.occupant(w)
                     and b.name_of(b.occupant(w)[0]) == wyvern.PLATE)
        trial = b.trial([bench.Edit(plate, _id("Damage Control II"))], ["tank.ehp.total"])
        assert any("Damage Control" in p for p in trial.problems)
        assert b.problems() == []  # reverted


def test_run_trials_groups_extra_conditions(booted, no_fits_left):
    trials = [([], None), ([], {"command": [{"fit": wyvern.PHENOMENA}]})]
    plain, boosted = bench.run_trials("[Wyvern, x]\n", None, ["tank.hp.shield"], trials)
    assert boosted.values["tank.hp.shield"] > plain.values["tank.hp.shield"]
```

Also create an empty `tests/__init__.py` if the repo has none (so `from tests import wyvern` works with `pythonpath = ["."]`).

- [ ] **Step 3: Run them to verify they fail**

Run: `uv run pytest tests/test_bench.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyfa_mcp.bench'`.

- [ ] **Step 4: Implement** `pyfa_mcp/bench.py`

```python
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
        self._stack = contextlib.ExitStack()
        try:
            scratch = self._stack.enter_context(evaluate.Scratch())
            self.fit = scratch.add_fit(self._ref)
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

        def back():
            if old.isEmpty:
                modules.free(pos)
            else:
                modules.replace(pos, old)

        if edit.item_id is None:
            modules.free(pos)
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
        if old is not None:
            holders.remove(old)
        new = None
        if item_id is not None:
            new = (Implant if kind == "implant" else Booster)(_item(item_id))
            if new.slot != slot:
                if old is not None:
                    holders.append(old)
                raise BenchError(f"{new.item.name} goes in {kind} slot {new.slot}, not {slot}")
            holders.append(new)

        def back():
            if new is not None and new in holders:
                holders.remove(new)
            if old is not None:
                holders.append(old)
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
        except BenchError as exc:
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
```

- [ ] **Step 5: Run the bench tests**

Run: `uv run pytest tests/test_bench.py -q`
Expected: all pass. If a reference case fails equality, **stop and investigate**; do not loosen `rel=1e-9`. A mismatch means bench recalculation differs from a fresh import (e.g. a command fit not recalculated), and every later task depends on it. Check `FitService.recalc` vs `fit.calculateModifiedAttributes` for command/projected fits first.

- [ ] **Step 6: Commit**

```bash
git add pyfa_mcp/bench.py tests/wyvern.py tests/test_bench.py tests/__init__.py
git commit -m "Measure changes to a fit in memory, checked equal to evaluate_fit"
```

---

### Task 5: The candidate pool

**Files:**
- Create: `pyfa_mcp/candidates.py`
- Test: `tests/test_candidates.py`

**Interfaces:**
- Consumes: `catalog.published_items`, `catalog._meta`, `catalog._slot`, `bench.Edit`, `bench.SLOT_LABELS`.
- Produces:
  - `candidates.SOURCES` (the 10 source names), `candidates.DEFAULT_HIDDEN_META = ("Officer", "Deadspace")`.
  - `candidates.parse_sources(sources: list[str] | None) -> set[str]`, which raises `ValueError` on an unknown source.
  - `candidates.meta_filter(meta: list[str] | None) -> (predicate(str) -> bool, note: str)`.
  - `candidates.why_not(fit, item) -> str | None`.
  - `candidates.Candidate` (dataclass) with fields:
    - `name, source, slot, group, meta, type_id`
    - `charge_id=None`, `edits=()`, `extra=None`
    - `cpu=0.0, pg=0.0, calibration=0.0`
    - `exclusive_group=None`, `overheat=False`, `active=False`, `modifies=()`

    `slot` is the rack for modules/rigs/subsystems/charges; `"implant N"` / `"booster N"`; `"implants 1, 2, …"` for sets; `"external"` otherwise.
  - `candidates.Pool(candidates, excluded, duplicates, scanned, meta_note)`.
  - `candidates.build(fit, sources: set[str], meta: list[str] | None) -> Pool`.

- [ ] **Step 1: Write the failing tests** `tests/test_candidates.py`

```python
import pytest

from pyfa_mcp import candidates, evaluate


def _pool(text, sources, meta=None):
    with evaluate.Scratch() as scratch:
        fit = scratch.add_fit(text)
        return candidates.build(fit, set(sources), meta)


def test_pool_has_legal_modules_and_explains_the_rest(booted, no_fits_left):
    pool = _pool("[Wyvern, x]\n", ["module"], ["all"])
    names = {c.name for c in pool.candidates}
    assert "Chelm's Modified Power Diagnostic System" in names
    assert "Capital Shield Extender II" in names
    adc = [e for e in pool.excluded if e["name"] == "Assault Damage Control II"]
    assert adc and adc[0]["reason"].startswith("cannot be fitted to")
    small = [e for e in pool.excluded if e["name"] == "Large Shield Extender II"]
    assert not small  # sub-capital modules fit capitals
    assert all(c.source == "module" for c in pool.candidates)


def test_default_meta_hides_officer_and_deadspace(booted, no_fits_left):
    pool = _pool("[Wyvern, x]\n", ["module"])
    assert not [c for c in pool.candidates if c.meta in ("Officer", "Deadspace")]
    assert "meta=[\"all\"]" in pool.meta_note
    assert any(e["reason"] == "meta Officer not requested" for e in pool.excluded)


def test_rigs_charges_and_identical_items(booted, no_fits_left):
    pool = _pool("[Chimera, x]\n", ["rig", "charge"], ["all"])
    rigs = [c for c in pool.candidates if c.source == "rig"]
    assert rigs and all(c.slot == "rig" for c in rigs)
    assert all("Capital" in c.name for c in rigs)
    assert any(e["reason"] == "rig size does not match the hull" for e in pool.excluded)
    pairs = {c.name for c in pool.candidates if c.source == "charge"}
    assert "Sensor Booster II + Targeting Range Script" in pairs


def test_pod_sets_and_external_sources(booted, no_fits_left):
    pool = _pool("[Wyvern, x]\n", ["implant", "booster", "command_burst", "phenomena",
                                   "projected", "environment"], ["all"])
    by_name = {c.name: c for c in pool.candidates}
    nirvana = by_name["High-grade Nirvana set"]
    assert nirvana.group == "Implant sets" and len(nirvana.edits) == 6
    assert by_name["Zainou 'Gnome' Shield Management SM-706"].slot == "implant 7"
    burst = by_name["Shield Command Burst II + Shield Extension Charge"]
    assert burst.extra["command"][0]["fit"].startswith("[Ferox,")
    assert "Leviathan" in by_name["Caldari Phenomena Generator"].extra["command"][0]["fit"]
    assert by_name["Class 6 Pulsar Effects"].edits[0].state == "online"
    assert by_name["Stasis Webifier II"].source == "projected"


def test_unknown_source():
    with pytest.raises(ValueError, match="unknown 'modules'"):
        candidates.parse_sources(["modules"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_candidates.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyfa_mcp.candidates'`.

- [ ] **Step 3: Implement** `pyfa_mcp/candidates.py`

```python
"""Everything that could change a fit on one hull, and why the rest cannot."""
from __future__ import annotations

import functools
import inspect
import re
from dataclasses import dataclass

from pyfa_mcp import catalog
from pyfa_mcp.bench import SLOT_LABELS, Edit

SOURCES = ("module", "rig", "subsystem", "charge", "implant", "booster",
           "command_burst", "phenomena", "projected", "environment")
DEFAULT_HIDDEN_META = ("Officer", "Deadspace")
_GRADES = ("Low-grade", "Mid-grade", "High-grade")
# Unbonused for every burst, so its deltas are a floor: command ships give more.
_BURST_HULL = "Ferox"
_TITANS = {"Amarr": "Avatar", "Caldari": "Leviathan", "Gallente": "Erebus",
           "Minmatar": "Ragnarok"}
_IGNORED_ATTRS = frozenset({"metaLevelOld", "metaGroupID", "techLevel", "metaLevel"})
_QUOTED = re.compile(r"'(\w+)'")


@dataclass
class Candidate:
    name: str
    source: str
    slot: str
    group: str
    meta: str
    type_id: int
    charge_id: int | None = None
    edits: tuple = ()        # fixed edits: implants, boosters, sets, projected
    extra: dict | None = None  # extra conditions: bursts, phenomena
    cpu: float = 0.0
    pg: float = 0.0
    calibration: float = 0.0
    exclusive_group: str | None = None
    overheat: bool = False
    active: bool = False
    modifies: tuple = ()


@dataclass
class Pool:
    candidates: list
    excluded: list    # [{"name", "group", "reason"}]
    duplicates: dict  # kept name -> names measured identically
    scanned: int
    meta_note: str


def parse_sources(sources: list[str] | None) -> set[str]:
    if sources is None:
        return set(SOURCES)
    bad = [s for s in sources if s not in SOURCES]
    if bad:
        raise ValueError(f"sources: unknown '{bad[0]}'; known: {', '.join(SOURCES)}")
    return set(sources)


def meta_filter(meta: list[str] | None):
    if meta is None:
        return (lambda m: m not in DEFAULT_HIDDEN_META,
                "every meta level but Officer and Deadspace (default); pass "
                "meta=[\"all\"], or a list such as [\"Tech II\", \"Faction\", \"Officer\"], "
                "to include them")
    wanted = {m.casefold() for m in meta}
    if "all" in wanted:
        return (lambda m: True), "every meta level"
    return (lambda m: m.casefold() in wanted), "only " + ", ".join(meta)


def why_not(fit, item) -> str | None:
    """Why `item` can never go on this hull, or None if it can."""
    from eos.const import FittingHardpoint, FittingSlot
    from eos.saveddata.citadel import Citadel
    from eos.saveddata.module import Module

    ship = fit.ship
    if not fit.canFit(item):
        return f"cannot be fitted to {ship.item.group.name}"
    try:
        mod = Module(item)
    except ValueError:
        return "not a fittable module"
    if mod.isInvalid or mod.slot not in SLOT_LABELS:
        return "not a fittable module"
    if not fit.getNumSlots(FittingSlot(mod.slot)):
        return f"{ship.item.name} has no {SLOT_LABELS[mod.slot]} slots"
    if (not isinstance(ship, Citadel) and ship.getModifiedItemAttr("isCapitalSize", 0) != 1
            and mod.isCapitalSize):
        return "capital-size module on a sub-capital hull"
    if (mod.slot == FittingSlot.RIG.value
            and mod.getModifiedItemAttr("rigSize") != ship.getModifiedItemAttr("rigSize")):
        return "rig size does not match the hull"
    for kind, attr, word in ((FittingHardpoint.TURRET, "turretSlotsLeft", "turret"),
                             (FittingHardpoint.MISSILE, "launcherSlotsLeft", "launcher")):
        if mod.hardpoint == kind and not ship.getModifiedItemAttr(attr):
            return f"{ship.item.name} has no {word} hardpoints"
    return None


@functools.cache
def _is_attribute(name: str) -> bool:
    import eos.db
    return eos.db.getAttributeInfo(name) is not None


@functools.cache
def _targets(handler) -> tuple:
    try:
        source = inspect.getsource(handler)
    except (OSError, TypeError):
        return ()
    return tuple(dict.fromkeys(n for n in _QUOTED.findall(source) if _is_attribute(n)))


def _modifies(item) -> tuple:
    """Attributes the item's effect handlers name, minus its own (explanatory only)."""
    names: list[str] = []
    for effect in item.effects.values():
        names += _targets(effect.handler)
    return tuple(n for n in dict.fromkeys(names) if n not in item.attributes)


def _exclusive(item) -> str | None:
    for attr in ("maxGroupFitted", "maxGroupActive", "maxGroupOnline"):
        value = item.getAttribute(attr)
        if value:
            return f"{attr} {int(value)} ({item.group.name})"
    return None


def _excluded(item, reason: str) -> dict:
    return {"name": item.name, "group": item.group.name, "reason": reason}


def _local(item, source: str, slot: str, charge=None) -> Candidate:
    return Candidate(
        name=item.name if charge is None else f"{item.name} + {charge.name}",
        source=source, slot=slot, group=item.group.name, meta=catalog._meta(item),
        type_id=item.ID, charge_id=None if charge is None else charge.ID,
        cpu=item.getAttribute("cpu") or 0.0, pg=item.getAttribute("power") or 0.0,
        calibration=item.getAttribute("upgradeCost") or 0.0,
        exclusive_group=_exclusive(item), overheat=item.isType("overheat"),
        active=item.isType("active"), modifies=_modifies(item))


def _charges(item, allowed) -> list:
    from eos.saveddata.module import Module
    return sorted((c for c in Module(item).getValidCharges()
                   if c.published and allowed(catalog._meta(c))), key=lambda c: c.ID)


def _pod(item, sources) -> list[Candidate]:
    from eos.saveddata.booster import Booster
    from eos.saveddata.implant import Implant

    kind = "booster" if item.group.name == "Booster" else "implant"
    if kind not in sources:
        return []
    try:
        slot = (Booster if kind == "booster" else Implant)(item).slot
    except Exception:
        return []
    if not slot:
        return []
    return [Candidate(name=item.name, source=kind, slot=f"{kind} {slot}",
                      group=item.group.name, meta=catalog._meta(item), type_id=item.ID,
                      edits=(Edit((kind, slot), item.ID),),
                      exclusive_group=f"{kind} slot {slot}", modifies=_modifies(item))]


def _sets(implants: list[Candidate]) -> list[Candidate]:
    """One composite candidate per implant set and grade: a lone piece understates it."""
    import eos.db

    groups: dict[tuple, list[Candidate]] = {}
    for c in implants:
        first = c.name.split()[0]
        grade = first if first in _GRADES else ""
        for attr in eos.db.getItem(c.type_id).attributes:
            if attr.startswith("ImplantSet"):
                groups.setdefault((attr[len("ImplantSet"):], grade), []).append(c)
    out = []
    for (name, grade), pieces in sorted(groups.items()):
        slots = [p.edits[0].where[1] for p in pieces]
        if len(pieces) < 2 or len(set(slots)) != len(slots):
            continue
        out.append(Candidate(
            name=f"{grade} {name} set".strip(), source="implant",
            slot="implants " + ", ".join(map(str, sorted(slots))), group="Implant sets",
            meta=pieces[0].meta, type_id=pieces[0].type_id,
            edits=tuple(e for p in pieces for e in p.edits),
            exclusive_group="implant slots " + ", ".join(map(str, sorted(slots)))))
    return out


def _bursts(allowed) -> list[Candidate]:
    from eos.saveddata.module import Module

    out = []
    for item in catalog.published_items(groups=("Command Burst",)):
        if not allowed(catalog._meta(item)):
            continue
        for charge in sorted(Module(item).getValidCharges(), key=lambda c: c.ID):
            if not charge.published:
                continue
            booster = f"[{_BURST_HULL}, {item.name}]\n\n\n{item.name}, {charge.name}\n"
            out.append(Candidate(
                name=f"{item.name} + {charge.name}", source="command_burst", slot="external",
                group=charge.name, meta=catalog._meta(item), type_id=item.ID,
                charge_id=charge.ID, extra={"command": [{"fit": booster}]}))
    return out


def _phenomena(allowed, excluded: list) -> list[Candidate]:
    out = []
    for item in catalog.published_items(groups=("Titan Phenomena Generator",)):
        if not allowed(catalog._meta(item)):
            continue
        titan = _TITANS.get(item.name.split()[0])
        if titan is None:
            excluded.append(_excluded(item, "no titan known to carry it"))
            continue
        out.append(Candidate(
            name=item.name, source="phenomena", slot="external", group=item.group.name,
            meta=catalog._meta(item), type_id=item.ID,
            extra={"command": [{"fit": f"[{titan}, {item.name}]\n\n\n{item.name}\n"}]}))
    return out


def _projected(item, source: str, state: str) -> Candidate:
    return Candidate(name=item.name, source=source, slot="external", group=item.group.name,
                     meta=catalog._meta(item), type_id=item.ID,
                     edits=(Edit(("projected", None), item.ID, None, state),))


def _signature(c: Candidate):
    import eos.db

    if c.extra is not None or c.group == "Implant sets":
        return None
    item = eos.db.getItem(c.type_id)
    return (c.source, c.slot, c.charge_id, tuple(sorted(item.effects)),
            tuple(sorted((k, v.value) for k, v in item.attributes.items()
                         if k not in _IGNORED_ATTRS)))


def _dedupe(found: list[Candidate]) -> tuple[list[Candidate], dict]:
    keep, first, twins = [], {}, {}
    for c in found:
        key = _signature(c)
        if key is None or key not in first:
            if key is not None:
                first[key] = c
            keep.append(c)
        else:
            twins.setdefault(first[key].name, []).append(c.name)
    return keep, twins


def build(fit, sources: set[str], meta: list[str] | None) -> Pool:
    from eos.saveddata.module import Module

    allowed, note = meta_filter(meta)
    found: list[Candidate] = []
    excluded: list[dict] = []
    items = catalog.published_items(categories=("Module", "Subsystem", "Implant"))
    for item in items:
        meta_name = catalog._meta(item)
        if not allowed(meta_name):
            excluded.append(_excluded(item, f"meta {meta_name} not requested"))
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
            found += [_local(item, "charge", slot, c) for c in _charges(item, allowed)]
    scanned = len(items)
    if "command_burst" in sources:
        found += _bursts(allowed)
    if "phenomena" in sources:
        found += _phenomena(allowed, excluded)
    if "environment" in sources:
        beacons = catalog.published_items(groups=Module.SYSTEM_GROUPS)
        scanned += len(beacons)
        found += [_projected(i, "environment", "online") for i in beacons]
    if "implant" in sources:
        found += _sets([c for c in found if c.source == "implant"])
    found, duplicates = _dedupe(found)
    return Pool(found, excluded, duplicates, scanned, note)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_candidates.py -q`
Expected: all pass. If `eos.saveddata.citadel` does not exist, find `Citadel`'s module with `grep -rn "class Citadel" vendor/Pyfa/eos` and fix the import.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/candidates.py tests/test_candidates.py
git commit -m "Enumerate every candidate a hull can take, and why the rest cannot go on it"
```

---

### Task 6: The worker pool

**Files:**
- Create: `pyfa_mcp/pool.py`
- Modify: `pyfa_mcp/eosboot.py` (new `booted_dir`)
- Modify: `pyfa_mcp/__main__.py` (guard + `freeze_support`)
- Test: `tests/test_pool.py`

**Interfaces:**
- Consumes: `bench.run_trials`.
- Produces:
  - `eosboot.booted_dir() -> Path` (raises `BootError` if not booted).
  - `pool.INLINE_LIMIT = 300`, `pool.IDLE_SECONDS = 60.0`.
  - `pool.configure(workers: int | None)`, `pool.size() -> int`.
  - `pool.run(ref, raw_conditions, keys, trials) -> list[Trial]`. Results come back in input order, and are identical whatever the worker count.
  - `pool.shutdown()`.
  - `pool.describe() -> {"workers", "running", "pids", "memory_mb", "idle_shutdown_s", "inline_below"}`.
  - `pool.PoolError(RuntimeError)`.

- [ ] **Step 1: Write the failing tests** `tests/test_pool.py`

```python
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pyfa_mcp import bench, pool

ROOT = Path(__file__).resolve().parent.parent
KEYS = ["tank.ehp.total", "offense.dps.total"]


@pytest.fixture
def small_pool(booted, monkeypatch):
    before = pool.size()
    monkeypatch.setattr(pool, "INLINE_LIMIT", 0)
    pool.configure(2)
    yield
    pool.configure(before)


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_pool_matches_inline(small_pool, zealot_eft, no_fits_left):
    trials = [([bench.Edit(("module", p), None)], None) for p in range(8)]
    pooled = pool.run(zealot_eft, None, KEYS, trials)
    assert pool.describe()["running"] == 2
    assert pooled == bench.run_trials(zealot_eft, None, KEYS, trials)


def test_small_jobs_stay_in_process(booted, zealot_eft, no_fits_left):
    pool.shutdown()
    pool.run(zealot_eft, None, KEYS, [([], None)])
    assert pool.describe()["running"] == 0


def test_idle_pool_shuts_down(small_pool, monkeypatch, zealot_eft):
    monkeypatch.setattr(pool, "IDLE_SECONDS", 0.5)
    pool.run(zealot_eft, None, KEYS, [([], None)])
    deadline = time.monotonic() + 20
    while pool.describe()["running"] and time.monotonic() < deadline:
        time.sleep(0.2)
    assert pool.describe()["running"] == 0


def test_workers_exit_with_their_parent(booted, tmp_path):
    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from pyfa_mcp import eosboot, pool\n"
        "eosboot.boot(Path(sys.argv[1]))\n"
        "pool.INLINE_LIMIT = 0\n"
        "pool.configure(1)\n"
        "pool.run('[Rifter, x]\\n', None, ['tank.ehp.total'], [([], None)])\n"
        "print(*pool.describe()['pids'], flush=True)\n"
        "time.sleep(600)\n")
    parent = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT,
                              stdout=subprocess.PIPE, text=True)
    pids = [int(p) for p in parent.stdout.readline().split()]
    assert pids and all(_alive(p) for p in pids)
    parent.kill()
    parent.wait()
    deadline = time.monotonic() + 30
    while any(_alive(p) for p in pids) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert not any(_alive(p) for p in pids)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_pool.py -q`
Expected: FAIL with `ImportError: cannot import name 'pool'`.

- [ ] **Step 3: Implement**

`pyfa_mcp/eosboot.py`, after `boot`:

```python
def booted_dir() -> Path:
    if _booted_dir is None:
        raise BootError("eos is not booted")
    return _booted_dir
```

`pyfa_mcp/__main__.py` (spawned workers import this module, and a frozen build re-runs it):

```python
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from pyfa_mcp.server import main
    main()
```

`pyfa_mcp/pool.py`:

```python
"""Worker processes for searches.

eos is single-threaded with one session per process, so a search fans its
trials out to processes that each boot their own eos (~1 s, ~150 MB each).
The pool starts on the first search big enough to need it, stops after
IDLE_SECONDS without one, and each worker exits by itself if the server
process dies.
"""
from __future__ import annotations

import multiprocessing
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from pyfa_mcp import bench, eosboot

INLINE_LIMIT = 300
IDLE_SECONDS = 60.0

_size = max(1, min((os.cpu_count() or 1) - 2, 12))
_executor: ProcessPoolExecutor | None = None
_idle: threading.Timer | None = None
_busy = 0
_lock = threading.Lock()


class PoolError(RuntimeError):
    """A worker died mid-search."""


def configure(workers: int | None) -> None:
    global _size
    if workers is not None:
        if workers < 0:
            raise ValueError("--workers must be 0 or more")
        _size = workers
    shutdown()


def size() -> int:
    return _size


def run(ref: str, raw_conditions: dict | None, keys: list[str], trials: list) -> list:
    if _size == 0 or len(trials) < INLINE_LIMIT:
        return bench.run_trials(ref, raw_conditions, keys, trials)
    executor = _start()
    try:
        step = -(-len(trials) // (_size * 2))
        futures = [executor.submit(bench.run_trials, ref, raw_conditions, keys,
                                   trials[i:i + step])
                   for i in range(0, len(trials), step)]
        return [t for future in futures for t in future.result()]
    except BrokenProcessPool as exc:
        shutdown()
        raise PoolError("a search worker died; the next call starts fresh ones") from exc
    finally:
        _release()


def _start() -> ProcessPoolExecutor:
    global _executor, _busy
    with _lock:
        _busy += 1
        if _idle is not None:
            _idle.cancel()
        if _executor is None:
            base = eosboot.booted_dir() / "workers"
            shutil.rmtree(base, ignore_errors=True)  # left by killed workers
            _executor = ProcessPoolExecutor(
                max_workers=_size, mp_context=multiprocessing.get_context("spawn"),
                initializer=_init_worker, initargs=(os.getpid(), str(base)))
        return _executor


def _release() -> None:
    global _busy, _idle
    with _lock:
        _busy -= 1
        if _idle is not None:
            _idle.cancel()
        _idle = threading.Timer(IDLE_SECONDS, _idle_shutdown)
        _idle.daemon = True
        _idle.start()


def _idle_shutdown() -> None:
    with _lock:
        if _busy:
            return
    shutdown()


def shutdown() -> None:
    global _executor, _idle
    with _lock:
        if _idle is not None:
            _idle.cancel()
            _idle = None
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


def _init_worker(parent_pid: int, base: str) -> None:
    # The server's stdout is the MCP stream; Pyfa prints while it boots.
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
    except OSError:
        pass  # no stdout handle at all (Windows spawn): nothing to protect
    sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    threading.Thread(target=_exit_with, args=(parent_pid,), daemon=True).start()
    eosboot.boot(Path(base) / str(os.getpid()))


def _exit_with(parent_pid: int) -> None:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
        if handle:
            kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0xFFFFFFFF)
    else:
        while os.getppid() == parent_pid:
            time.sleep(1)
    os._exit(0)


def _memory_mb(pid: int) -> float:
    try:
        if sys.platform == "win32":
            return _windows_private_mb(pid)
        with open(f"/proc/{pid}/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def _windows_private_mb(pid: int) -> float:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    *((name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage",
                        "PrivateUsage"))]

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return 0.0
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p,
                                                     ctypes.POINTER(Counters), wintypes.DWORD]
        if not kernel32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return 0.0
        return counters.PrivateUsage / 2**20
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def describe() -> dict:
    with _lock:
        pids = sorted(_executor._processes) if _executor is not None else []
    return {"workers": _size, "running": len(pids), "pids": pids,
            "memory_mb": round(sum(_memory_mb(p) for p in pids)),
            "idle_shutdown_s": IDLE_SECONDS, "inline_below": INLINE_LIMIT}
```

- [ ] **Step 4: Run the pool tests**

Run: `uv run pytest tests/test_pool.py -q`
Expected: all pass. Note that `ProcessPoolExecutor` starts workers lazily, on submit. If `running` is 1 right after the first `run` with 8 trials, the second worker had no chunk: keep 8 trials and two chunks per worker so both start.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/pool.py pyfa_mcp/eosboot.py pyfa_mcp/__main__.py tests/test_pool.py
git commit -m "Run search trials on worker processes that stop when idle and die with the server"
```

---

### Task 7: find_modifiers

**Files:**
- Create: `pyfa_mcp/search.py`
- Modify: `pyfa_mcp/drift.py` (new `unhandled_for`)
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: `bench.Bench/Edit/Trial`, `candidates.build/parse_sources`, `pool.run`, `stats.cap`, `store.resolve_eft`, `eft.looks_like_eft`, `evaluate.evaluate`.
- Produces:
  - `drift.unhandled_for(type_ids) -> list[str]`.
  - `search.find_modifiers(fit, stat_keys, sources=None, meta=None, raw_conditions=None, expand=None) -> dict` with keys `applied, baseline, groups, candidates, excluded, pinned, coverage, next`.
  - Shared helpers that Tasks 8–9 use:
    - `search._baseline_eft(fit) -> str`
    - `search._portable(raw) -> dict`
    - `search._run(ref, raw, keys, trials) -> list[Trial]` (cached)
    - `search._pool(fit, sources, meta) -> Pool` (cached per hull)
    - `search._excluded_rows(excluded) -> list[dict]`
    - `search._confirm(ref, raw, edits) -> {"eft", "flat", "result"}`
    - `search._zero(delta, base) -> bool`

- [ ] **Step 1: Write the failing tests** `tests/test_search.py`

```python
import pytest

from pyfa_mcp import evaluate, pool, search, store
from tests import wyvern


def _names(result):
    return {c["name"] for c in result["candidates"]}


def _ehp(text, cond):
    return evaluate.evaluate(text, cond)["tank"]["ehp"]["total"]


def test_t1_every_source_of_shield_hp(booted, no_fits_left):
    result = search.find_modifiers("Wyvern", ["tank.hp.shield"], meta=["all"], expand=["*"])
    groups = {g["group"] for g in result["groups"]}
    assert {"Power Diagnostic System", "Shield Extender", "Rig Shield", "Implant sets"} <= groups
    names = _names(result)
    assert "Chelm's Modified Power Diagnostic System" in names
    assert "High-grade Nirvana set" in names
    assert "Zainou 'Gnome' Shield Management SM-706" in names
    assert "Caldari Phenomena Generator" in names
    assert any(n.endswith("+ Shield Extension Charge") for n in names)
    assert result["baseline"]["tank.hp.shield"] > 0
    assert "optimize_fit" in result["next"]
    assert result["coverage"]["items_scanned"] > 1000


def test_t2_assault_damage_control_is_excluded_with_a_reason(booted, no_fits_left):
    result = search.find_modifiers("Wyvern", ["tank.ehp.total"], sources=["module"],
                                   meta=["all"])
    rows = [e for e in result["excluded"] if e["group"] == "Damage Control"
            and e["reason"].startswith("cannot be fitted to")]
    assert rows and any("Assault" in name for name in rows[0]["examples"])


def test_t6_a_second_phenomena_is_a_drawback(booted, no_fits_left):
    result = search.find_modifiers(wyvern.BRIEF, ["tank.ehp.total"], sources=["phenomena"],
                                   raw_conditions=wyvern.CONDITIONS, expand=["*"])
    amarr = next(c for c in result["candidates"] if c["name"] == "Amarr Phenomena Generator")
    assert amarr["delta"]["tank.ehp.total"] < 0
    assert "drawback: lowers tank.ehp.total" in amarr["notes"]


def test_t7_t8_lock_range_pinned_at_its_cap(booted, no_fits_left):
    four = "[Chimera, c]\n\n" + "Sensor Booster II, Targeting Range Script\n" * 4
    five = "[Chimera, c]\n\n" + "Sensor Booster II, Targeting Range Script\n" * 5
    for text in (four, five):
        assert evaluate.evaluate(text, None)["targeting"]["lock_range_m"] == 750000.0
    result = search.find_modifiers(four, ["targeting.lock_range_m"], sources=["module"])
    assert result["pinned"][0]["cap_attribute"] == "maximumRangeCap"
    assert "Integrated Sensor Array" in result["pinned"][0]["raised_by"]
    isa = "[Chimera, c]\nIntegrated Sensor Array\n\nSensor Booster II, Targeting Range Script\n"
    lock = evaluate.evaluate(isa, None)["targeting"]["lock_range_m"]
    assert lock == pytest.approx(7_987_728, rel=1e-3)


def test_officer_items_need_meta(booted, no_fits_left):
    result = search.find_modifiers("Wyvern", ["tank.hp.shield"], sources=["module"],
                                   expand=["*"])
    assert not [c for c in result["candidates"] if c["meta"] in ("Officer", "Deadspace")]
    assert 'meta=["all"]' in result["applied"]["meta"]


def test_expand_names_unknown_groups(booted, no_fits_left):
    with pytest.raises(ValueError, match="did you mean: Power Diagnostic System"):
        search.find_modifiers("Wyvern", ["tank.hp.shield"], sources=["module"],
                              expand=["Power Diagnostic Sytem"])


def test_find_modifiers_resolves_stored_fits_for_workers(booted, no_fits_left, monkeypatch):
    monkeypatch.setattr(pool, "INLINE_LIMIT", 0)
    before = pool.size()
    pool.configure(2)
    store.save_fit(wyvern.PHENOMENA, "test phenomena")
    try:
        result = search.find_modifiers(
            "Wyvern", ["tank.hp.shield"], sources=["rig"],
            raw_conditions={"command": [{"fit": "test phenomena"}]})
        assert result["groups"]
    finally:
        store.delete_fit("test phenomena")
        pool.configure(before)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_search.py -q`
Expected: FAIL with `ImportError: cannot import name 'search'`.

- [ ] **Step 3: Implement**

`pyfa_mcp/drift.py`, after `_unhandled_by_type`:

```python
def unhandled_for(type_ids) -> list[str]:
    """'Item: effect, effect' for each of these items Pyfa does not fully compute."""
    by_type = _unhandled_by_type()
    names = {row[0]: row[1] for row in _new_rows()}
    return [f"{names[t]}: {', '.join(by_type[t])}"
            for t in dict.fromkeys(type_ids) if t in by_type]
```

`pyfa_mcp/search.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_search.py -q`
Expected: all pass. `test_t1` is the slowest (every source on a capital); it should take a few seconds with workers.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/search.py pyfa_mcp/drift.py tests/test_search.py
git commit -m "Add find_modifiers: every item, implant, burst and effect that moves a stat"
```

---

### Task 8: marginal_swaps

**Files:**
- Modify: `pyfa_mcp/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: Task 7 helpers.
- Produces:
  - `search.marginal_swaps(fit, objective, raw_conditions=None, meta=None, include_empty_slots=True, top_n=10) -> {"applied", "objective", "baseline", "swaps", "no_improvement_found", "warnings", "coverage"}`. Each swap is `{slot, remove, add, delta, new_value, valid}`, and the top one also carries `eft`.
  - Shared with Task 9:
    - `search.Option` (NamedTuple: `place, type_id, charge_id, state, name, group, cpu, pg, calibration`; `.edit(where)`)
    - `search._options(cands, heat: bool) -> dict[place, list[Option]]`
    - `search._place_key(b, where) -> str`
    - `search._objective(text) -> (key, sign)`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_search.py`)

```python
def test_t3_the_plate_should_have_been_a_pds(booted, no_fits_left):
    result = search.marginal_swaps(wyvern.BRIEF, "tank.ehp.total", wyvern.HOT, meta=["all"])
    top = result["swaps"][0]
    assert top["remove"] == wyvern.PLATE
    assert "Power Diagnostic System" in top["add"] and "Modified" in top["add"]
    one_pds = wyvern.fit([wyvern.DC, wyvern.PDS, wyvern.PLATE, wyvern.PLATE])
    expected = _ehp(one_pds, wyvern.HOT) - _ehp(wyvern.BRIEF, wyvern.HOT)
    assert top["delta"] == pytest.approx(expected, rel=1e-6)
    assert result["no_improvement_found"] is False
    assert result["warnings"] == []


def test_marginal_swaps_minimizes_with_a_minus(booted, zealot_eft, no_fits_left):
    result = search.marginal_swaps(zealot_eft, "-navigation.align_time_s", top_n=3)
    assert result["swaps"][0]["delta"] < 0


def test_no_improvement_on_an_optimal_fit(booted, no_fits_left):
    result = search.marginal_swaps("[Rifter, empty]\n", "-navigation.signature_m",
                                   include_empty_slots=False)
    assert result["no_improvement_found"] is True
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_search.py -q -k "t3 or marginal or no_improvement"`
Expected: FAIL with `AttributeError: module 'pyfa_mcp.search' has no attribute 'marginal_swaps'`.

- [ ] **Step 3: Implement** (append to `pyfa_mcp/search.py`; add `from typing import NamedTuple` to the imports)

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_search.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/search.py tests/test_search.py
git commit -m "Add marginal_swaps: every single change that would improve a fit"
```

---

### Task 9: optimize_fit

**Files:**
- Modify: `pyfa_mcp/search.py`
- Test: `tests/test_search.py`

**Interfaces:**
- Consumes: Tasks 7–8 helpers (`Option`, `_options`, `_place_key`, `_pod_places`, `_objective`, `_run`, `_pool`, `_confirm`, `_excluded_rows`).
- Produces: `search.optimize_fit(fit, objective, raw_conditions=None, allow=None, meta=None, locked=None, constraints=None, top_k=5, budget=None) -> {"applied", "best", "considered", "pruned", "excluded", "search"}`. Each `best[i]` is `{eft, objective_value, stats, valid, warnings}`, and `search` is `{method, evaluations, seconds, converged, stopped_by}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_search.py`)

```python
WYVERN_ALLOW = {"slots": ["low", "mid", "rig"], "module_states": ["active", "overheated"]}


def _lows(text):
    lows = text.split("\n\n")[0].splitlines()[1:]
    return lows


def test_t4_t5_the_best_wyvern(booted, no_fits_left):
    hot = search.optimize_fit(wyvern.BRIEF, "tank.ehp.total", wyvern.CONDITIONS,
                              allow=WYVERN_ALLOW, meta=["all"], top_k=1,
                              budget={"seconds": 900})
    best = hot["best"][0]
    assert hot["search"]["converged"] is True
    assert best["valid"] is True
    assert best["objective_value"] >= _ehp(wyvern.BEST_LOWS, wyvern.HOT) * (1 - 1e-9)
    assert best["objective_value"] > _ehp(wyvern.NO_DC, wyvern.HOT)
    lows = _lows(best["eft"])
    assert sum("Power Diagnostic System" in n for n in lows) == 3
    assert sum("Damage Control" in n for n in lows) == 1

    cold = search.optimize_fit(wyvern.BRIEF, "tank.ehp.total", wyvern.CONDITIONS,
                               allow={**WYVERN_ALLOW, "module_states": ["active"]},
                               meta=["all"], top_k=1, budget={"seconds": 900})
    assert cold["search"]["converged"] is True
    assert cold["best"][0]["objective_value"] < best["objective_value"]


def test_optimize_respects_constraints_and_budget(booted, zealot_eft, no_fits_left):
    speed = evaluate.evaluate(zealot_eft, None)["navigation"]["max_speed"]
    capped = search.optimize_fit(zealot_eft, "offense.dps.total",
                                 constraints=[{"stat": "navigation.max_speed", "gte": speed}],
                                 allow={"slots": ["low", "mid"]}, top_k=2)
    assert capped["best"]
    for fit in capped["best"]:
        assert fit["valid"] is True
        assert fit["stats"]["navigation.max_speed"] >= speed * (1 - 1e-9)
    short = search.optimize_fit(zealot_eft, "tank.ehp.total", budget={"evaluations": 50})
    assert short["search"]["converged"] is False
    assert short["search"]["stopped_by"] == "evaluations"
    assert short["best"]


def test_optimize_keeps_subsystems(booted, no_fits_left):
    tengu = ("[Tengu, t]\n\n\n\n\nTengu Core - Augmented Graviton Reactor\n"
             "Tengu Defensive - Covert Reconfiguration\nTengu Offensive - Accelerated Ejection Bay\n"
             "Tengu Propulsion - Chassis Optimization\n")
    result = search.optimize_fit(tengu, "tank.ehp.total", top_k=1, budget={"seconds": 300})
    for line in tengu.splitlines()[5:]:
        assert line in result["best"][0]["eft"]


def test_optimize_recovers_from_an_invalid_start(booted, no_fits_left):
    two_dcs = "[Rifter, x]\nDamage Control II\nDamage Control II\n"
    result = search.optimize_fit(two_dcs, "tank.ehp.total", allow={"slots": ["low"]},
                                 top_k=1, budget={"seconds": 300})
    assert result["best"][0]["valid"] is True


def test_optimize_refuses_module_states_and_bad_allow(booted, zealot_eft):
    with pytest.raises(ValueError, match="allow.module_states"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", {"module_states": [
            {"module": "Damage Control II", "state": "online"}]})
    with pytest.raises(ValueError, match="subsystems are kept"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", allow={"slots": ["subsystem"]})
    with pytest.raises(ValueError, match="locked: 'Heat Sink III' is not on the fit"):
        search.optimize_fit(zealot_eft, "tank.ehp.total", locked="Heat Sink III")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_search.py -q -k "optimize or t4"`
Expected: FAIL with `AttributeError: ... no attribute 'optimize_fit'`.

- [ ] **Step 3: Implement** (append to `pyfa_mcp/search.py`; add `import re`, `import time` and `from collections import Counter` to the imports)

```python
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


class _Search:
    def __init__(self, ref, raw, key, sign, cons, budget, places, place_key, options):
        self.ref, self.raw, self.key, self.sign, self.cons = ref, raw, key, sign, cons
        self.keys = list(dict.fromkeys([key, *(s for s, _, _ in cons), *_FITTING_KEYS]))
        self.deadline = time.monotonic() + budget["seconds"]
        self.left = budget["evaluations"]
        self.places, self.place_key, self.options = places, place_key, options
        self.top: dict[str, list[Option]] = {}
        self.seen: dict[tuple, tuple] = {}  # canonical state -> (score, state, values)
        self.evaluations = 0
        self.stopped_by: str | None = None

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
            trials = _run(self.ref, self.raw, self.keys,
                          [(self.edits(state), None) for _, state in batch])
            self.left -= len(batch)
            self.evaluations += len(batch)
            for (canon, state), trial in zip(batch, trials):
                self.seen[canon] = (self._score(trial), state, trial.values)
        if over:
            self.stopped_by = over
            raise _OutOfBudget
        return [self.seen[self.canonical(s)] for s in states]

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
                option = next((o for o in self.options.get(pk, []) if o.type_id == e.item_id),
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

    def improve(self, state, sets) -> dict:
        while True:
            score, best = self.best(self.singles(state) + self.set_moves(state, sets))
            if self.better(score, state):
                state = best
                continue
            score, best = self.best(self.pairs(state))
            if self.better(score, state):
                state = best
                continue
            return state

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


def _dominated(options: dict[str, list[Option]], deltas: dict, key: str, sign: int) -> dict:
    """Options beaten within their own item group on the objective and every cost."""
    out = {}
    for opts in options.values():
        for a in opts:
            for b in opts:
                if a is b or a.group != b.group or a.state != b.state or a not in deltas \
                        or b not in deltas:
                    continue
                da, db = sign * deltas[a][key], sign * deltas[b][key]
                cheaper = (b.cpu <= a.cpu and b.pg <= a.pg and b.calibration <= a.calibration)
                strictly = (db > da or b.cpu < a.cpu or b.pg < a.pg
                            or b.calibration < a.calibration)
                if db >= da and cheaper and (strictly or b.type_id < a.type_id):
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
    budget = {"evaluations": 20000, "seconds": 60, **(budget or {})}
    ref = _baseline_eft(fit)
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
        candidates_places = [w for w in b.module_places() if b.rack(w) in allow["slots"]]
        candidates_places += [w for w in _pod_places(b, options)
                              if allow["implants" if w[0] == "implant" else "boosters"]]
        for where in candidates_places:
            occ = b.occupant(where)
            name = b.name_of(occ[0]).casefold() if occ else None
            if name and keep[name] > 0:
                keep[name] -= 1
                continue
            places.append(where)
            place_key[where] = _place_key(b, where)
            start[where] = None if occ is None else Option(
                place_key[where], occ[0], occ[1], occ[2], b.name_of(occ[0]), "")
        missing = [n for n, c in keep.items() if c > 0]
        if missing:
            raise ValueError(f"locked: '{written[missing[0]]}' is not on the fit")
        applied = {**b.applied, "meta": found.meta_note,
                   "heat": " and ".join(allow["module_states"])}

    sets = [c for c in found.candidates if c.group == "Implant sets"]
    search = _Search(ref, raw, key, sign, cons, budget, places, place_key, options)
    pruned: dict = {}
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
        search.options = options
        for option, delta in search.screen(seed).items():
            if _useful(delta, key, sign, cons):
                useful.add(option)
                deltas.setdefault(option, delta)
        kept = {pk: [o for o in opts if o in useful] for pk, opts in options.items()}
        dominated = _dominated(kept, deltas, key, sign)
        for opts in options.values():
            for o in opts:
                if o not in useful:
                    pruned[o.name] = ("no effect on the objective, the constraints or "
                                      "fitting resources")
                elif o in dominated:
                    pruned[o.name] = dominated[o]
        search.options = {pk: [o for o in opts if o not in dominated]
                          for pk, opts in kept.items()}
        search.top = {pk: sorted(opts, key=lambda o: -sign * deltas[o][key])[:_PAIR_OPTIONS]
                      for pk, opts in search.options.items()}
        seeds = [seed, search.greedy(clean, order[::-1])]
        if any(o is not None for o in start.values()):
            seeds.insert(0, start)
        for state in seeds:
            search.improve(state, sets)
    except _OutOfBudget:
        converged = False

    ranked = sorted(((s, st) for s, st, _ in search.seen.values() if s is not None),
                    key=lambda p: ((-p[0][0], -p[0][1]), search.canonical(p[1])))
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
                       for pk, opts in search.options.items()},
        "pruned": [{"name": n, "reason": r} for n, r in sorted(pruned.items())],
        "excluded": _excluded_rows(found.excluded),
        "search": {"method": _METHOD, "evaluations": search.evaluations,
                   "seconds": round(time.monotonic() - started, 1),
                   "converged": converged, "stopped_by": search.stopped_by},
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_search.py -q`
Expected: all pass. `test_t4_t5` is the long one. If it does not converge within its 900 s budget on the dev box, profile before raising the budget. The first suspect is `screen` measuring every option twice: count `search.evaluations` per phase.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/search.py tests/test_search.py
git commit -m "Add optimize_fit: greedy seeds and local search over every useful candidate"
```

---

### Task 10: Server tools, descriptions, `--workers`, status and smoke

**Files:**
- Modify: `pyfa_mcp/server.py`
- Modify: `packaging/mcp_smoke.py`
- Modify: `tests/test_server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `search.find_modifiers/marginal_swaps/optimize_fit`, `pool.configure/describe/PoolError`.
- Produces:
  - MCP tools `find_modifiers`, `marginal_swaps` and `optimize_fit`.
  - The CLI flag `--workers N`.
  - `status()["search_workers"]`.
  - Redirect lines in the `search_items`, `evaluate_fit` and `compare_fits` descriptions.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_server.py`)

```python
def test_search_tools_and_redirects(booted, no_fits_left):
    result = server.find_modifiers("Rifter", ["tank.ehp.total"], sources=["rig"])
    assert result["groups"]
    assert "use find_modifiers" in server.search_items.__doc__
    assert "use marginal_swaps" in server.evaluate_fit.__doc__
    assert "use marginal_swaps" in server.compare_fits.__doc__
    for tool in (server.find_modifiers, server.optimize_fit, server.marginal_swaps):
        assert "best" in tool.__doc__ and "Officer" in tool.__doc__
    assert "find_modifiers" in server.INSTRUCTIONS and "beyond_the_fit" in server.INSTRUCTIONS
    assert server.status()["search_workers"]["workers"] >= 0


def test_search_input_errors(booted):
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="unknown stat 'tank.ehp.totl'"):
        server.find_modifiers("Rifter", ["tank.ehp.totl"])
    with pytest.raises(ToolError, match="sources: unknown 'modules'"):
        server.find_modifiers("Rifter", ["tank.ehp.total"], sources=["modules"])
    with pytest.raises(ToolError, match="allow: unknown key 'slot'"):
        server.optimize_fit("Rifter", "tank.ehp.total", allow={"slot": ["low"]})
    with pytest.raises(ToolError, match="constraints: each is"):
        server.optimize_fit("Rifter", "tank.ehp.total", constraints=[{"stat": "x"}])
    with pytest.raises(ToolError, match="no stored fit named 'Rifterr'"):
        server.marginal_swaps("Rifterr", "tank.ehp.total")
```

In `test_smoke_over_stdio`, add `"--workers", "2"` to the server command list (after `"--pyfa-dir", ...`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_server.py -q`
Expected: FAIL with `AttributeError: module 'pyfa_mcp.server' has no attribute 'find_modifiers'`, and the smoke test fails on the unknown `--workers`.

- [ ] **Step 3: Implement**

`pyfa_mcp/server.py`:

1. Imports: add `pool` and `search` to the `from pyfa_mcp import (...)` list.
2. Append to `INSTRUCTIONS` (before the closing `"""`):

```
- For a best / max / min / optimal fit, or "what affects X": call
  find_modifiers (everything that can move the stat: every slot, implant,
  booster, burst, phenomena and environment), then optimize_fit (searches
  whole fits); audit a hand-built fit with marginal_swaps. Never choose
  candidates from memory or search_items alone.
- Those three leave Officer and Deadspace items out unless meta includes
  them (meta=["all"]); tell the user which you used.
- Walk conditions_format()["beyond_the_fit"] (pod, drugs, links, phenomena,
  projected, environment, heat, mode) and tell the user which of those you
  assumed, set, or left out.
```

3. `search_items` docstring, append: `Matches names only. To find items by what they do (e.g. everything that adds shield HP), use find_modifiers.`
4. `evaluate_fit` and `compare_fits` docstrings, append: `To check whether a fit can be improved, use marginal_swaps.`
5. Add a `# --- search ---` section after `compare_fits`:

```python
@app.tool()
@_tool
def find_modifiers(fit: str, stats: list[str], sources: list[str] | None = None,
                   meta: list[str] | None = None, conditions: dict | None = None,
                   expand: list[str] | None = None) -> dict:
    """What can change a stat on this hull. Use it before saying what is best or
    max/min/optimal, and whenever the user asks what affects or what else could
    raise or lower a stat (EHP, DPS, lock range, align...). Measures every legal
    module, rig, subsystem, charge/script, implant and implant set, booster,
    command burst, phenomena generator, projected module and environment effect
    on `fit` (hull name, EFT or stored fit) under `conditions`, so it finds what
    you would not think to search for. One row per item group: best variant, a
    Tech II/Faction reference, delta range; expand=["Group"] or ["*"] lists every
    variant. Officer and Deadspace items are left out unless meta includes them
    (meta=["all"]). stats: evaluate_fit keys (tank.ehp.total) or ship.<attribute>.
    sources: module, rig, subsystem, charge, implant, booster, command_burst,
    phenomena, projected, environment (default all). Then call optimize_fit."""
    return search.find_modifiers(fit, stats, sources, meta, conditions, expand)


@app.tool()
@_tool
def marginal_swaps(fit: str, objective: str, conditions: dict | None = None,
                   meta: list[str] | None = None, include_empty_slots: bool = True,
                   top_n: int = 10) -> dict:
    """Is there any single change that makes this fit better? Use it to audit a
    hand-built fit before recommending it as the best or max for a stat. Tries
    every module, rig, charge, implant and booster that fits each slot, every
    empty slot and every removal; returns the valid ones sorted by gain
    (objective: a stat key; prefix "-" to minimize, e.g. "-navigation.align_time_s").
    The top swap is confirmed with evaluate_fit. Officer and Deadspace items are
    left out unless meta includes them (meta=["all"])."""
    return search.marginal_swaps(fit, objective, conditions, meta, include_empty_slots,
                                 top_n)


@app.tool()
@_tool
def optimize_fit(fit: str, objective: str, conditions: dict | None = None,
                 allow: dict | None = None, meta: list[str] | None = None,
                 locked: str | None = None, constraints: list[dict] | None = None,
                 top_k: int = 5, budget: dict | None = None) -> dict:
    """Search for the best fit for a stat. Use it whenever the user asks for the
    best, highest, max, min-max or optimal fit, or before recommending a module
    choice. It builds its candidates from every item that affects the stat, so it
    won't miss modules you didn't think of. fit: hull name or EFT (a start
    point). objective: stat key, "-" prefix to minimize. allow: {slots: [high,
    mid, low, rig], implants: bool, boosters: bool, module_states: [active,
    overheated]} (default: all racks, no implants/boosters, no overheat).
    locked: EFT lines that must stay. constraints: [{"stat", "eq"|"lte"|"gte":
    value}]. budget: {evaluations, seconds} (default 20000, 60). Officer and
    Deadspace items are left out unless meta includes them (meta=["all"]).
    Command bursts, phenomena, projected and environment stay as conditions set
    them. Every returned fit is computed by evaluate_fit; `search.converged`
    says whether the search finished inside the budget."""
    return search.optimize_fit(fit, objective, conditions, allow, meta, locked,
                               constraints, top_k, budget)
```

6. `_USER_ERRORS`: add `pool.PoolError`.
7. `status()`: add `"search_workers": pool.describe(),` to the returned dict.
8. `main()`: add the argument, and configure the pool before `app.run()`:

```python
    parser.add_argument("--workers", type=int, default=None,
                        help="processes for find_modifiers/optimize_fit/marginal_swaps "
                             "(default: cores - 2, at most 12; 0 = none)")
```

```python
    pool.configure(args.workers)
```

`packaging/mcp_smoke.py`:
- Add `"find_modifiers", "marginal_swaps", "optimize_fit"` to `TOOLS`.
- Append to `CALLS` (it has more than 300 trials, so it runs through workers whenever the server has them):

```python
    ("find_modifiers", {"fit": "Rifter", "stats": ["tank.ehp.total"], "sources": ["module"]}),
```

- Update the module docstring's list of calls to mention `find_modifiers` (worker processes).

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass, including `test_smoke_over_stdio` with `--workers 2`. A stray byte from a worker on stdout would fail its JSON parse.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/server.py packaging/mcp_smoke.py tests/test_server.py
git commit -m "Expose find_modifiers, marginal_swaps and optimize_fit, and point the agent at them"
```

- [ ] **Step 6: Speed check on the dev box** (not a test; record the numbers in the PR)

Run:
```bash
uv run python -c "
import time, tempfile; from pathlib import Path
from pyfa_mcp import eosboot, search; from tests import wyvern
eosboot.boot(Path(tempfile.mkdtemp()))
t=time.time(); search.find_modifiers('Wyvern', ['tank.ehp.total'], meta=['all']); print('find cold', time.time()-t)
t=time.time(); search.find_modifiers('Wyvern', ['tank.ehp.total'], meta=['all']); print('find cached', time.time()-t)
t=time.time(); r=search.optimize_fit(wyvern.BRIEF, 'tank.ehp.total', wyvern.CONDITIONS, allow={'slots':['low','mid','rig'],'module_states':['active','overheated']}, meta=['all'], top_k=1); print('optimize', time.time()-t, r['search'])
"
```
Expected, per the spec: the cached find takes under 1 s, and the optimize converges in under 60 s on a 16-core machine. If it doesn't, report the numbers and the per-phase evaluation counts. Do not tune silently.
