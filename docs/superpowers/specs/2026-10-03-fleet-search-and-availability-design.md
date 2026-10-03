# pyfa-mcp — fleet search, availability, optimizer fixes

Date: 2026-10-03
Status: approved in brainstorming, pending written-spec review
Origin: `pyfa-mcp-improvements.pdf`, the post-mortem of a hand test of
v0.2.0 (a max-EHP Wyvern with heat, pod, drugs, bursts and phenomena),
checked against the code and the game data. The spec's numbering (#1–#8) is
kept.

## Purpose

Four failures in the hand test, and their causes:

- **Wrong boosters.** Burst and phenomena sources are fixed inputs in
  `conditions`; the agent picked a Vulture from memory. The Nighthawk was
  better and the Simurgh (a Command Carrier, newer than the model's
  training) better still. `find_modifiers` measured bursts from an
  unbonused Ferox, so they looked weak.
- **Unusable items.** The best fit stacked three Capsuleer Defense
  Augmentation Chips and two Serenity 'Hardshell' doses. All are
  Serenity-only; the chips are also limited by character age.
- **A missed swap.** `optimize_fit` returned Halcyon G-5 where B-5 is
  better. Not a local optimum: `_dominated` compares options by their
  deltas on an *empty hull* (`screen(clean)`). There G-5 leads (2.127M vs
  2.082M EHP); on the fitted Wyvern B-5 leads (123.21M vs 122.99M). B-5 was
  pruned before the local search saw it.
- **Friction.** Results carry no module states (EFT cannot hold heat), so
  re-evaluating meant rebuilding them by hand; outputs overflowed the
  context; comparing booster hulls took five calls.

Success: tests T1–T12 (below) pass.

## Decisions taken in brainstorming

- **External effects stay off by default.** `optimize_fit` searches bursts
  and phenomena only with `allow.command` / `allow.phenomena`; the server
  instructions tell the agent to set them for best/max/min requests.
- **Limited items are hidden by default** in `find_modifiers`,
  `marginal_swaps` and `optimize_fit`, like Officer and Deadspace:
  Serenity-only items, items limited by character age, and items with an
  expiry date (even ones not yet expired). `availability="all"` lifts it;
  `applied` says which was used.
- **The polish pass and the dominance fix both ship** (#6): the pass is the
  guarantee, the fix makes it rarely needed.
- **Compact output by default** (#8); `verbose=true` gives today's output.
- **#3 is reduced** to "newest by type ID": Pyfa's data carries no dates.
- **Out of scope:** #7 beyond one fleet/fit alternation (no pilot-vs-external
  attribution); running two availability presets in one call; `since=<date>`
  and "changed items" in `whats_new`; Tengu/Loki as burst sources (their
  bonus comes from a subsystem); sorting in `compare_fits`.

## Game data facts this relies on

- `boosterMaxCharAgeHours`: 49 items (cerebral and skill accelerators, the
  Capsuleer chips at 720–2400 h).
- `boosterLastInjectionDatetime` (unit Datetime, days since 1970-01-01): 49
  items; one expired in 2018, 48 boosters valid until 2026-11-10.
- Serenity-only: 173 published items whose description says "available on
  Serenity", plus names starting "Serenity ". "Festival-only" items are
  Tranquility event items and stay allowed.
- Booster slots: the Capsuleer chips have `boosterness` 501–504, so stacking
  three is legal in Pyfa; only the availability filter removes them.
- Bursts: buff values are the module's `warfareBuff1..4Value`; Pyfa keeps
  the strongest value per buff ID (`Fit.addCommandBonus`), so sources for
  different charges stack and sources for the same charge don't.
  `Command Burst` modules have `maxGroupOnline 1`; command hulls raise it
  by role bonus.
- Shield-burst hulls by bonus per level: Simurgh and Ymir 5%; Nighthawk,
  Sleipnir 4%; Vulture, Claymore, Rorqual 3%; Wyvern, Hel, Bifrost, Stork,
  Skua, Outrider 2%; Chimera, Nidhoggur, Minokawa, Lif, Orca 1%.
- Newest ships by type ID: Ymir, Gaia, Simurgh, Salvation, …

## A. Optimizer correctness and reproducibility (#6)

**Dominance on two screens.** `optimize_fit` screens every useful option on
the seed fit as well as on the empty hull (today only the non-useful ones
get a second screen). An option is pruned as dominated only if it is
dominated on both screens. Pruning reasons are unchanged.

**Polish pass.** After `improve()` (or when the main search runs out of
budget), the search takes the best state and runs single swaps over the
*full, unpruned* option set (everything `_options` produced, before
`useful`/`dominated`), best improvement first, until none improves. No pairs.
The main search gets 85% of each budget (evaluations, seconds); the polish
gets the rest, plus whatever the main search left. Each best fit reports:

```
"polish": {"swaps_applied": [{"slot": "booster 5", "from": "Halcyon G-5 Booster",
                              "to": "Halcyon B-5 Booster", "delta": 218380.0}],
           "converged": true}
```

Only `best[0]` is polished; the others are reported as found.

**Reproduce block.** Each best fit gets `conditions`: the conditions it was
measured under (the caller's, with stored fits resolved to EFT, plus the
fleet from section E) and `module_states` from `bench.module_states()`.
`_confirm` already evaluates with exactly this; it is now returned.
`evaluate_fit(best.eft, best.conditions)` gives `objective_value`.

**Cold value.** When `allow.module_states` includes `overheated`, each best
fit also reports `objective_cold`: the same fit with no module states.

## B. Compact output (#8)

New `verbose: bool = False` on `optimize_fit`, `find_modifiers` and
`marginal_swaps`. Compact output differs only in these fields:

- `optimize_fit.considered` → `{place: count}`.
- `optimize_fit.pruned` → `{"counts": {"no effect": n, "dominated": n},
  "near_winners": [{"name", "reason"}]}`, where `near_winners` lists pruned
  options in the same item group as an item in `best[0]` (the ones that
  explain a choice).
- `excluded` (all three tools) → `{reason: count}`.
- `optimize_fit.best[1:]` → `{"objective_value", "valid", "diff": {"remove":
  [...], "add": [...]}}` against `best[0]`'s EFT lines; `best[0]` stays
  whole.

`verbose=true` returns today's fields unchanged.

## C. Availability (#4)

`catalog.limits(item) -> list[str]`, empty when unlimited:
`"Serenity only"`, `"characters under N days"` (from hours, rounded),
`"expires YYYY-MM-DD"`.

- Rows gain `limits` (omitted when empty) in `search_items`, `item_info`,
  `find_modifiers` groups and variants, `marginal_swaps` swaps, and
  `optimize_fit` pruned/near_winners.
- `search_items` and `item_info` rows gain `slot` for implants and boosters
  (`"implant 10"`, `"booster 5"`), so slot conflicts need no `item_info`.
- `find_modifiers`, `marginal_swaps`, `optimize_fit` take
  `availability: str = "tq"`. `"tq"` hides every item with a limit (excluded
  with the limit as its reason); `"all"` keeps them. `applied.availability`
  says which, and how to lift it. Anything else is a `ValueError` naming
  the two values.
- The filter sits in `candidates.build` beside `meta_filter`, and the
  candidate-pool cache key includes it.

## D. Ships by capability (#2)

`list_ships(group=None, race=None, can_fit=None, bonus=None)`.

- `can_fit`: an item name or an item group name ("Command Burst"). A ship
  matches when `candidates.why_not` passes on an empty fit of it for at
  least one of those items. Hulls that only get slots from subsystems (T3
  cruisers) don't match; the description says so.
- `bonus`: words, case-insensitive. A ship matches when one line of its
  trait text contains every word. Matching rows gain `bonuses`:
  `[{"line", "per": "level"|"role", "at_all_v"}]`. `at_all_v` is the line's
  leading percentage ×5 for per-level sections, as-is for role bonuses,
  null when the line has no number. Rows are sorted by the largest
  `at_all_v`, descending.
- Unknown `can_fit` names fail with close matches, like `group` does today.

## E. Fleet search: bursts and phenomena (#1)

**Strength table** (new module `fleet.py`). For each published Command Burst
module M and each published ship H that can fit it (section D's check):
fit `[H]` with M and M's first valid charge, measure the largest
`|warfareBuffNValue|` on M. Then on the strongest H for M, try each slot-10
Cyber Leadership implant (and none) and keep the strongest. Result per M:
sources ranked by strength, each `(hull, mindlink, strength)`. All V, as
every booster fit today. Strength is assumed uniform across M's charges
(hull and mindlink bonuses scale the module); a test checks one other
charge.

It depends only on the game data. It is computed once and written to the
server's data dir as `burst_sources-<client_build>.json`; later runs read
that file. The plan measures the cold build; if it exceeds 10 s on one
process, it moves onto the worker pool.

**`find_modifiers`.** `_bursts` builds each burst candidate's booster fit
from M's strongest source instead of `_BURST_HULL = "Ferox"`. The note
becomes "measured from <hull> + <mindlink>, All V; the strongest source in
the game data". Candidates and mindlinks pass the meta and availability
filters; hulls don't.

**`optimize_fit`.** `allow` gains `command: bool` and `phenomena: bool`
(default false). If either is true and `conditions.command` is set, the call
fails: the search chooses those. With `command`, after the fit search:

1. For each burst charge C: its candidate source is the strongest (M, H,
   mindlink) among modules that take C. Measure the fit with that one burst
   added against the fit alone. Keep every C that improves the objective.
2. With `phenomena`: measure each generator (current `_phenomena` titans)
   and none, with the kept bursts; keep the best, possibly none.
3. Run `improve()` again from the best fit with that fleet fixed in the
   conditions, then repeat steps 1–2 once. Stop.
4. Group the kept bursts into booster fits by (hull, mindlink), at most the
   hull's burst limit per fit (split until each booster fit is valid in
   Pyfa).

The fleet steps spend the same budget. Output:

```
"fleet": {
  "bursts": [{"charge": "Shield Harmonizing Charge",
              "module": "'Vigilant' Shield Command Burst", "hull": "Simurgh",
              "mindlink": "Caldari Navy Command Mindlink", "delta": ...,
              "runners_up": [{"hull": "Nighthawk", "module": ..., "mindlink": ...,
                              "delta": ...}]}],
  "phenomena": {"chosen": "Caldari Phenomena Generator",
                "deltas": {"Caldari Phenomena Generator": ..., "none": 0.0, ...}},
  "booster_fits": ["[Simurgh, Fleet boosts]\n...", ...]}
```

`runners_up`: the next three distinct hulls from the strength table,
measured on the final fit. `deltas` lists every generator with its sign,
including the ones that lower the objective. Booster fits also go into each
best fit's `conditions` (section A).

## F. Compare across conditions (#5)

`compare_fits(fits, conditions=None, stats=None, variants=None)`.
`variants`: a list of partial conditions; each is merged over `conditions`
(top-level keys replace). Rows are fit × variant, in that order, each with
`variant` (its index) and `variant_label` (its keys and values, cut to 80
characters). No `variants` = today's output.

## G. Newest items (#3)

`whats_new(category=None, limit=30)`: the newest published items by type
ID, optionally in one category (Ship, Module, Implant, Charge, Drone,
Fighter, Subsystem; default all of those). Returns `{"ordered_by": "type ID:
the game data carries no dates; higher IDs were added later",
"game_client_build", "items": [{name, type_id, group, category, limits?}]}`.

Server instructions gain: "The game data may be newer than your training.
Find ships and items with the tools (list_ships can_fit/bonus, find_modifiers,
whats_new), never from memory." They also say: for best/max/min requests,
set `allow.command` and `allow.phenomena` unless the user fixed the fleet;
and tell the user which `availability` was used.

## Tests

- **T1** (A) On the brief's fitted Wyvern with the brief's pod and booster
  slot 5 open, `optimize_fit` with boosters allowed picks Halcyon B-5, not
  G-5.
- **T2** (A) Polish: given a state where a pruned option improves, the pass
  applies it and reports it in `polish.swaps_applied`.
- **T3** (A) For each best fit, `evaluate_fit(eft, conditions)` gives
  `objective_value`; with heat allowed, `objective_cold` is below it.
- **T4** (B) The Wyvern max-EHP call (all racks, implants, boosters, heat)
  returns under 24 KB of JSON compact; `verbose=true` returns the old fields.
- **T5** (C) With the default, no Capsuleer chip, Serenity item or
  expiring booster appears in `find_modifiers` or `optimize_fit` results;
  with `availability="all"` they do. `search_items("Capsuleer Defense")`
  rows carry `limits`; "Agency 'Hardshell' TB3 Dose I" has none.
- **T6** (D) `list_ships(can_fit="Command Burst")` includes all four
  Command Carriers; `list_ships(bonus="Shield Command burst strength")`
  includes Simurgh, Ymir, Nighthawk, Vulture, Chimera and Wyvern, the first
  two at `at_all_v` 25 and first.
- **T7** (E) The strength table ranks a 5%/level hull (Simurgh or Ymir)
  first for Shield Command Burst II; for a second charge of the same module
  the order is the same.
- **T8** (E) `find_modifiers` on the Wyvern measures Shield Harmonizing
  above what a Vulture booster gives, and its note names the source hull.
- **T9** (E) `optimize_fit(allow.command, allow.phenomena)` on the Wyvern
  picks a 5%/level hull for every shield charge it keeps, beats the same fit
  boosted by a hand-built Vulture, lists every phenomena generator in
  `deltas`, and returns booster fits that `evaluate_fit` marks valid.
- **T10** (E) `allow.command` together with `conditions.command` is an error.
- **T11** (F) Five booster-hull variants on one fit return five rows in one
  call.
- **T12** (G) `whats_new(category="Ship", limit=10)` includes the four
  Command Carriers.

The full replay of the hand test (everything allowed, `meta=["all"]`) is a
slow test, skipped in CI. It checks structure, not the brief's 385.7M: this
server measures about 6% lower than the brief.

## Order of work

A, B, C, D, E, F, G. A–C are independent of the rest; E uses D's can-fit
check.
