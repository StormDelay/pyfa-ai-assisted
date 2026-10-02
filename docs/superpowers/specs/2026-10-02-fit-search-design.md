# pyfa-mcp — fit search tools

Date: 2026-10-02
Status: approved in brainstorming, pending written-spec review
Origin: design brief `pyfa-mcp_fit-search-tooling_design.pdf` (a test run's
post-mortem), refined in brainstorming.

## Purpose

The agent evaluates fits accurately but only compares items it already
thought to name: `search_items` matches names. In the test run it built a
"max EHP" Wyvern with three CONCORD plates (255.2M EHP) and never considered
the Power Diagnostic System (308.4M, +21%). Two causes, both removed here:

- **Discovery by name.** Nothing answers "what can change shield HP on this
  hull?". New: `find_modifiers`.
- **Comparison by hand.** The agent picked which axes to vary and varied them
  one at a time. New: `optimize_fit` (search) and `marginal_swaps` (audit).

Success: tests T1–T8 (below) pass; every result shows what was considered
and what was excluded; an agent asked for a best/max/optimal fit reaches for
these tools.

## Decisions taken in brainstorming

- **Officer and Deadspace items are excluded by default.** Every result's
  `applied` says so and says how to lift it (`meta=[...]`); the tool
  descriptions and server instructions say the same.
- **The optimizer changes only what the pilot controls**: high/mid/low/rig/
  subsystem slots, charges and scripts, and implants and boosters when
  allowed. Command bursts, phenomena, projected and environment effects stay as
  `conditions` sets them; `find_modifiers` still reports them so the agent
  can change `conditions` itself.
- **Overheat is opt-in** (`allow.module_states`); otherwise active modules
  are measured active.
- **Drones and fighters are kept as given**, not searched (v1).
- **Subsystems are measured, not swapped** (v1): `find_modifiers` reports
  them; `marginal_swaps` and `optimize_fit` keep a T3 cruiser's subsystems,
  since swapping one changes the slot layout under the search.
- **`meta`**: omitted means every meta level but Officer and Deadspace;
  `["all"]` means everything; a list means exactly those levels.
- **Measured, not static.** Pyfa's game DB has no dogma `modifierInfo`; all
  ~2,400 effects are Python handlers. Membership in a result comes only from
  a measured delta. Attribute names scraped from handler source fill the
  explanatory `modifies` field and nothing else.
- **`find_modifiers` output is grouped by item group** by default, with
  `expand` to list variants. Measurement always covers every variant.
- **Everything outside the hull is listed in one place**
  (`conditions_format().beyond_the_fit`), and environment effects become a
  condition. See "Beyond the fit".
- **Related fix:** an EFT line naming an item that cannot be fitted (e.g.
  the component "Capital Armor Plates") is an error today it is silently
  dropped by Pyfa's importer and not reported.
- Out of scope: pricing, mutated (abyssal) modules, optimizing a worst case
  over several damage profiles, drone/fighter search, system and pilot
  security status.

## Beyond the fit

### `conditions_format().beyond_the_fit`

A checklist of every category that changes a fit's numbers without being a
module on the hull. One entry per category: `how` (EFT or which conditions
key) and `options` (names when short; item group names when long, whose
members `search_items` lists). Options are read from the game data at call
time, not hard-coded.

| category | how | options |
|---|---|---|
| pod | EFT (implants, slots 1–10) | slot numbers; implant set names (items sharing a set bonus attribute) |
| drugs | EFT (boosters, slots 1–3) + `drug_side_effects` | booster groups |
| links | `command` (a booster fit) | command burst modules and their charges |
| phenomena | `command` (a titan fit) | the phenomena generators |
| projected | `projected` | projectable module groups (remote repair, webs, paints, e-war…) |
| environment | `environment` (new) | beacon names grouped by kind (wormhole class/type, abyssal weather, incursion, faction warfare, sov hub…) |
| heat | `module_states` | — |
| mode / spool | `mode` / `spool` | — |
| skills, damage, target | `character` / `damage_profile` / `target` | profile names (already listed) |
| drones / fighters | EFT | — |

It replaces the existing one-line `in_eft_instead` note.

### `conditions.environment`

`environment: "<beacon name>"` applies one system effect through Pyfa's
projected-module path (Pyfa treats system effects as exclusive, so one at a
time). Accepted: published items in the groups Pyfa marks as system effects
(`Module`'s system-effect group list: Effect Beacon, MassiveEnvironments,
Abyssal Hazards, …). Unknown names get close-match suggestions; `applied`
echoes it (`"environment": "none"` by default). `projected` keeps rejecting
these items and its error points to `environment`.

## Architecture

New module `pyfa_mcp/search.py` (the engine and the three tools) and
`pyfa_mcp/pool.py` (worker processes). Reuses `eft`, `conditions`,
`evaluate.Scratch`, `stats`, `catalog._slot/_meta`, `drift`.

### Bench: in-memory measurement

`Bench(fit_ref, conditions)` is a context manager around an
`evaluate.Scratch` fit with conditions applied once. It offers:

- `measure(keys)` — recalc (`fit.clear(); fit.calculateModifiedAttributes()`)
  then return only the requested flattened stat keys.
- `try_change(remove, add)` — apply a change (modules get `owner` set,
  charges loaded, state set), measure, revert. Returns the stat values and
  the change's validity (CPU/PG/calibration/slots/hardpoints/group limits,
  using the same checks as `stats._validity`).
- `eft()` — the current fit as EFT text.

Per step ≈ 10 ms on a linked capital, against ≈ 75 ms for an EFT
import/evaluate/delete round trip.

**Lazy stats.** `stats.fit_stats` is split so a caller can ask for sections
by key prefix: `stats.fit_stats(fit, spool, sections={"tank"})`. The cap sim
(~7 ms) runs only when a `capacitor.*` key is asked for. `evaluate_fit` keeps
asking for every section.

**Nothing unverified reaches the agent.** Every fit a tool returns (top
swap, top_k optimizer fits) is re-run through `evaluate.evaluate` and the
reported numbers are that call's. If the bench and the evaluator disagree
by more than 1e-6 relative, the result carries a warning naming both values.

### Candidate pool

`pool_for(bench, sources, meta)` walks published items once per hull
(cached per hull + meta) and returns candidates and exclusions:

| source | items | legality |
|---|---|---|
| module | category Module, slot high/mid/low | `Module.fits(fit)`, hull restriction, hardpoint type |
| rig | slot rig | rig size, calibration |
| subsystem | category Subsystem | ship (T3 only) |
| charge | (module, charge) pairs for each module candidate that takes charges | `isValidCharge` |
| implant | category Implant, implantness 1–10 | — |
| booster | boosterness slots | — |
| command_burst | burst + charge on an unbonused Ferox booster fit (a floor: command ships and mindlinks give more) | find_modifiers only |
| phenomena | phenomena generator on its racial titan, as a command fit | find_modifiers only |
| projected | projected modules from conditions schema | find_modifiers only |
| environment | system effect beacons, applied as `conditions.environment` | find_modifiers only |

- `meta` filter: default all except Officer and Deadspace.
- **Dedupe:** items with identical effects and attribute values are measured
  once; the result is copied to each.
- Every rejected item goes to `excluded` with its reason ("cannot be fitted
  to Supercarrier", "rig size mismatch", "meta Officer not requested").

### Worker pool

Measuring is embarrassingly parallel and eos is single-threaded per
process, so searches fan out to worker processes, each with its own booted
eos.

- Size: `min(cores − 2, 12)`; `--workers N` overrides, `--workers 0`
  disables (everything runs in the server process).
- Started on the first search call that needs it (≈ 1.1 s boot each, in
  parallel); **shut down after 60 s without a search call**, returning
  memory (~150 MB private per worker, measured).
- Jobs estimated under ~300 evaluations run in the server process; no pool
  start.
- A worker receives the baseline EFT, the raw conditions dict and a slice
  of candidate type ids; it builds its own Bench and returns stat values.
  Results are merged in candidate order, so output does not depend on
  worker count.
- Workers poll the server's PID and exit if it is gone (no orphans when a
  client is killed).
- `status()` reports pool size, state (idle/running/stopped) and workers'
  memory.

Later spike, not in v1: skipping recalcs of unchanged command/projected
fits and pruning skills that touch nothing (≈ 85% of recalc time is skill
application). Both patch eos internals; accept only with exact equality on
every reference fit.

### Cache

Measured deltas are cached in the server process per (hull, baseline EFT,
canonical conditions JSON, stat keys, meta) for the life of the process,
LRU-bounded to 32 entries.

## Tools

### find_modifiers

```
find_modifiers(fit, stats, sources=None, meta=None, conditions=None,
               expand=None) -> {
  applied, baseline: {stat: value},
  groups: [{group, source, slot, variants, best: {name, delta, cpu, pg,
            calibration, meta}, reference: {name, delta} | None,
            delta_range: [min, max], notes: [...]}],
  candidates: [...]            # only for groups named in `expand` ("*" = all)
  excluded: [{group, variants, reason}],
  pinned: [{stat, cap_attribute, raised_by: [names]}],
  coverage: {items_scanned, measured, effects_unresolved: [...]},
  next: "optimize_fit ..."
}
```

- `fit`: hull name (empty hull is the baseline), EFT, or stored id.
  `stats`: `evaluate_fit` keys (`tank.ehp.total`, `targeting.lock_range_m`).
- Each candidate is added to the baseline. If its slot is full it is
  swapped against each distinct item in that slot and the best result kept;
  `delta` is that net change. Active modules are measured active; modules
  that can overheat are also measured overheated
  (`delta_overheated`).
- A candidate is listed if any requested stat's delta is non-zero, negative
  included (`notes: ["drawback: lowers X"]`).
- Candidate fields: name, type_id, source, slot, meta, cpu, pg, calibration,
  charge, delta, delta_overheated, exclusive_group (maxGroupFitted /
  maxGroupActive / implant slot / booster slot), modifies, notes.
- Groups are Pyfa item groups, sorted by best delta descending (drawback-
  only groups last). `reference` is the Tech II variant, else the best
  Faction, else Tech I.
- **Implant sets** (items sharing a set bonus attribute): one composite
  candidate per set and grade, measured with the full set fitted.
- **Caps:** when a requested stat equals the ship's cap attribute
  (`maximumRangeCap` for lock range), it is listed in `pinned`, with the
  candidates that raise the cap.
- `coverage.effects_unresolved`: the unhandled effects (from `drift`) on
  scanned items. Empty means every scanned item's effects are computed.

### marginal_swaps

```
marginal_swaps(fit, objective, conditions=None, meta=None,
               include_empty_slots=True, top_n=10) -> {
  applied, baseline,
  swaps: [{slot, remove, add, delta, new_value, valid}],
  no_improvement_found,
  coverage: {...}
}
```

Tries every single change: each fitted item in a slot swapped for each
pool candidate of that slot type, each empty slot filled, each item removed.
Invalid results dropped; sorted by delta (objective's sign respected). The
top swap is confirmed through `evaluate_fit`.

### optimize_fit

```
optimize_fit(fit, objective, conditions=None,
             allow={slots: [high, mid, low, rig, subsystem],
                    implants: false, boosters: false,
                    module_states: ["active"]},
             meta=None, locked=None, constraints=[], top_k=5,
             budget={evaluations: 20000, seconds: 60}) -> {
  applied,
  best: [{eft, objective_value, stats, valid}],
  considered: {slot: [names]},
  pruned: [{name, reason}],
  search: {method, evaluations, seconds, converged, stopped_by}
}
```

- `fit`: hull or EFT. Items in slots outside `allow.slots` stay; `locked` is
  EFT lines that must stay anywhere. `objective` prefixed with `-` is
  minimized. `constraints`: `{"stat", "eq"|"lte"|"gte": value}`.
- **Prune** per slot type: keep candidates with a non-zero effect on the
  objective or any constraint; collapse identical items; drop dominated
  items (no better on objective, every constraint, and every fitting
  resource). Every prune recorded.
- **Seed:** greedy fill, place by place, best measured result first, within
  CPU/PG/calibration and group limits; a second greedy seed fills the racks
  in reverse order. A different slot-type split (mids 5/3 vs 4/4) is one
  single swap away, so the improve step covers it.
- **Improve:** from each seed, apply the best single swap until none
  improves; then pair swaps among pruned candidates; repeat until neither
  improves. Invalid or constraint-breaking fits never count.
- **Validate:** top_k distinct fits run through `evaluate_fit`; only those
  numbers are reported.
- Deterministic: no randomness, ties broken by type id.
- Heat comes from `allow.module_states`; `conditions.module_states` is
  refused (it names modules the optimizer may remove). Fits are validated
  with `module_states` derived from the states the search chose.
- Budget exhausted → best so far, `converged: false`, `stopped_by`.

## Agent guidance

- Descriptions of the three tools carry trigger words (max, maximize,
  highest, best, optimal, optimize, min-max, "what affects", "what else
  could", EHP, DPS, lock range, align) and say when to use them, e.g.
  optimize_fit: "Use whenever the user asks for the best, highest, max or
  optimal fit for a stat, or before recommending a module choice. Builds its
  candidates from every item that affects the stat, so it won't miss modules
  the caller didn't think of."
- `search_items` gains: "Matches names only. To find items by what they do
  (e.g. everything that adds shield HP), use find_modifiers."
- `evaluate_fit` / `compare_fits` gain: "To check whether a fit can be
  improved, use marginal_swaps."
- Server instructions gain a paragraph: for best/max/optimal questions use
  find_modifiers → optimize_fit, audit hand-built fits with marginal_swaps;
  Officer/Deadspace are excluded unless `meta` includes them; walk
  `conditions_format().beyond_the_fit` and tell the user which of those
  categories were assumed, set, or left out.
- `find_modifiers` returns a `next` hint naming optimize_fit; grouped rows
  name `expand`.

## Errors

Same as existing tools: bad fit, conditions, unknown stat keys or unknown
groups in `expand` are `ToolError`s with close-match suggestions. A worker
crash fails the call with the worker's error; the pool is restarted on the
next call. A candidate Pyfa raises on is moved to `excluded` with the
exception text, not fatal.

## Testing

All with All 5 skills, uniform damage, the pinned game data. Choices are
exact; values are compared with `evaluate_fit` on hand-built reference fits
rather than the brief's absolute numbers (the brief's Wyvern differed in
some detail: its numbers are ~6% higher, its ratios identical).

- **Engine equality (first task, gates the rest):** for each reference fit
  and a linked/projected Wyvern, mutate a bench into the target fit and
  check every stat equals `evaluate_fit` on the same EFT.
- **Pool:** same results with `--workers 0` and with a pool; idle shutdown;
  worker exits when its parent PID dies.
- **T1** find_modifiers(Wyvern, shieldCapacity, meta incl. Officer) groups
  include Power Diagnostic System, Shield Extender, Rig Shield (Core Defense
  Field Extender), Nirvana set, Zainou 'Gnome' SM-706, Shield Extension
  burst, Caldari phenomena.
- **T2** Assault Damage Control appears in `excluded` with a hull
  restriction reason.
- **T3** marginal_swaps on the 255.2M Wyvern (brief §8): top swap is a plate
  to an officer PDS, about +16.5M.
- **T4** optimize_fit(Wyvern, tank.ehp.total, overheat allowed, Officer
  allowed): lows officer DC + 3 officer PDS; mids 5 extenders / 3 Estamel's;
  rigs 3 Field Extender II; ≥ ~308.4M; not 4 PDS without DC (260.3M).
- **T5** same without overheat: lower value, mid split searched again.
- **T6** Amarr phenomena on top of Caldari: negative delta, drawback note.
- **T7** Chimera lock range without ISA, 4 vs 7 Sensor Booster II + range
  script: both 750 km (1 gives 700 km), `pinned` names maximumRangeCap.
- **T8** Chimera with Integrated Sensor Array + 1 SeBo II script: ≈ 7,988
  km; ISA in `pinned.raised_by`.
- **Defaults:** without `meta`, no Officer/Deadspace candidate appears and
  `applied` says how to include them.
- **Beyond the fit:** `conditions_format()` lists every category in the
  table with non-empty options where the table gives some; implant sets
  include Nirvana; environment options include a Class 6 wormhole effect.
- **Environment:** a Wyvern under "Class 6 Pulsar Effects" has more shield
  HP than without; `applied.environment` echoes it; an unknown name gets
  suggestions; the beacon in `projected` errors and points to `environment`.
- **Descriptions:** redirect strings present on search_items, evaluate_fit,
  compare_fits.
- **Speed:** capital optimize_fit with default budget under 60 s;
  find_modifiers on a cached baseline under 1 s.
