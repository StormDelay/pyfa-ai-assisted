# pyfa-mcp — fitting guide and fit notes

Date: 2026-10-04
Status: approved in brainstorming, pending written-spec review
Origin: `pyfa_tooling_upgrades.pdf`, written after a hand test in which the
agent built a fleet Rokh. Its section 2 (guide and hints) was taken as a set of
suggestions and reworked against the current tools; section 3 is out of scope.

## Purpose

The Rokh was valid and strong on paper but not a fleet ship. It had a 50MN MWD
(+87% speed), a Sensor Booster instead of a Signal Amplifier, no cap booster, an
open EM hole, and Hammerheads at sniper range. Pyfa's numbers were right. The
causes:

- **The instructions lead to paper stats.** `INSTRUCTIONS` sends "best fit"
  straight to `find_modifiers` → `optimize_fit` on one stat under the default
  conditions. Nothing asks what the ship is for.
- **No fitting knowledge at the moment of decision.** The levers a fleet role
  needs already exist: `conditions` (damage profile, target, projected neuts or
  logi fits, command links, environment) and `optimize_fit` `constraints`
  (max targets, lock range, cap). Nothing tells the agent to use them, or how.
- **Warning signs pass unnoticed.** The output showed 207 m/s with the MWD on,
  but nothing drew attention to it.

Success: tests T1–T8 (below) pass, and a rerun of the fleet Rokh prompt makes
the agent call `fitting_guide` first and act on the notes.

## Decisions taken in brainstorming

- **No `role` argument on `evaluate_fit` / `optimize_fit` / `marginal_swaps`.**
  Role needs become `conditions` and `constraints` those tools already take. A
  "no role set" warning on every call would be noise.
- **Fleet only for now.** The guide covers fleet doctrines and the ship roles
  in them. Solo, PvE, abyssal and other roles come later, by adding YAML.
- **Variation is tagged, not multiplied.** Doctrines vary by tank layer
  (armor, shield), space (nullsec, lowsec, wormhole) and scale (about 50, 100
  or 250 pilots). Principles carry a `when` tag over these three axes. There
  are no per-combination entries.
- **Role-free notes are mechanical and quiet.** Only two checks survived
  review:
  - **Propmod efficiency** stays.
  - **Uniform-damage hole** stays.
  - **Hull-size lint by name** was dropped. Modules have no size attribute,
    and names mislead: a Large Shield Extender is a cruiser module.
  - **Mixed tank detection** was dropped as too fragile; the EHP split per
    layer is already in the output.
  - **Drone reach vs weapon reach** was dropped. It would fire on most sniper
    and kiting fits, and drones are usually utility.
  - **Cap without a booster** was dropped. `lasts_s` is already shown, and
    neut pressure belongs in conditions.
- **The PDF's section 3 is out of scope:**
  - Scenarios already exist as `conditions`.
  - Role constraints already exist as `constraints`.
  - Permutation testing is close to what `compare_fits` already does.
  - Range-aware DPS exists as the `fit_graph` damage graph.

## Design

### 1. `fitting_guide` tool

```
fitting_guide(role: str | None = None, tank: str | None = None,
              space: str | None = None, pilots: int | None = None) -> dict
```

- **No role:** returns `general` (principles for every fit), `roles` (name →
  one-line summary) and `axes` (each axis with its values).
- **With a role**, returns:
  - `role` and `summary`;
  - `principles`: `[{text, why}]`. These are the role's untagged principles
    plus the tagged ones whose `when` matches the given axes.
  - `general`: the general principles;
  - `suggested`: `{conditions, constraints}` to pass to `evaluate_fit`,
    `compare_fits` and `optimize_fit`. Either can be absent.
  - `unset`: one entry per axis not given, with the text that axis's values
    would add, e.g. `{"space": "wormhole: mass limits, environment effects;
    lowsec: capital drops, gate guns"}`. Absent when every axis is set.
- **Bad input:** an unknown role, axis value or a non-positive `pilots`
  raises `ValueError` and lists the valid values.
- **No boot:** the tool doesn't boot Pyfa. It reads only the YAML, so it is
  fast as the first call of a session. It still runs through the server's
  error mapping, so `ValueError` becomes a `ToolError`.

### 2. `pyfa_mcp/fitting_guide.yaml`

The data file is plain YAML, read with the `pyyaml` already pinned, and shipped
like `unhandled_effects.json` (`pyproject.toml` package data and
`packaging/pyfa-mcp.spec` datas).

```yaml
axes:
  tank: {armor: "...", shield: "..."}          # value -> what it changes, used in `unset`
  space: {nullsec: "...", lowsec: "...", wormhole: "..."}
  pilots: "fleet size; principles apply from/up to a size (min_pilots, max_pilots)"
general:
  - {text: ..., why: ...}
roles:
  fleet_doctrine:
    summary: ...
    principles:
      - {text: ..., why: ...}
      - {text: ..., why: ..., when: {space: [wormhole]}}
      - {text: ..., why: ..., when: {min_pilots: 100}}
    suggested:
      conditions: {...}
      constraints: [...]
```

`when` keys are `tank`, `space` (a value or a list of values), `min_pilots`
and `max_pilots`. A principle matches when every key in its `when` holds. A key
on an unset axis never matches, and that axis then appears in `unset`.

Roles and their substance (wording drafted in the plan, reviewed by the user
in the YAML):

- **general:**
  - Propulsion matches the hull: actual gain vs rated, see the notes.
  - Tank one layer.
  - Know the weakest resist.
  - Count only damage that applies at the engagement range.
  - Know how long cap lasts under pressure.
- **fleet_doctrine:** the whole fleet.
  - One tank layer across the fleet, so logi, links and resists match.
  - One engagement range band, with weapons and ammo for it.
  - Rough composition shares: mainline, logi, links, tackle/webs,
    anti-support.
  - How to check a doctrine with the tools: evaluate the mainline with
    `command` = the link fit, `projected` = N logi fits, and the expected
    enemy pressure (`projected` neuts, `damage_profile`).
  - Tagged variants:
    - wormhole: mass limits, `environment` effects;
    - lowsec: capital drops, no bubbles;
    - scale: alpha, tidi and bombers at 100+ pilots.
- **fleet_mainline:** the DPS ship.
  - Hull-sized MWD, Restrained in large fleets.
  - Weapons and ammo for the band.
  - Cap booster.
  - Signal Amplifier over Sensor Booster.
  - Plug the weakest resist.
  - Drones as utility.
  - Tagged: armor vs shield slot use; volley EHP at scale.
- **fleet_logistics:**
  - Rep range covers the fleet's spread.
  - Cap stable under neuts (cap chains).
  - Matches the doctrine's tank layer.
  - Survives being primaried.
- **fleet_command:**
  - Bursts match the tank layer.
  - Survivability over burst count.
  - Stays in burst range.
  - Find the hull with `optimize_fit` `allow.command` or `list_ships(bonus=...)`.
- **fleet_support:**
  - Tackle, webs, EWAR and anti-tackle: what each does in a fleet.
  - Range vs the mainline's band.
  - Utility value does not show in DPS or EHP.

`suggested` is untagged in this version.

### 3. `notes` in `evaluate_fit` and `compare_fits`

`evaluate_fit` results get `notes: [str]` next to `warnings`. `warnings` stays
for validity and effects Pyfa doesn't compute. Notes are observations; a
deliberate fit may ignore them. `compare_fits` rows carry `notes` as they carry
`warnings`. A new `pyfa_mcp/notes.py` computes them from the calculated fit:

- **Propmod efficiency.**
  - **Formula:** for each fitted propmod (the propulsion module groups), the
    actual gain is `speedFactor × speedBoostFactor / mass`, using modified
    attributes. `mass` includes the module's `massAddition` whether or not
    the module is active. The rated gain is `speedFactor`.
  - **When it fires:** when actual / rated < `PROPMOD_RATIO` (0.5), e.g.
    "50MN Microwarpdrive II gives +87% speed of its rated +638%: too little
    thrust for this hull's mass; a larger propmod gives more".
  - **Oversized is quiet.** An oversized propmod (100MN AB on a cruiser)
    beats its rating and never fires.
- **Uniform-damage hole.**
  - **When it applies:** only when the damage profile is the default
    uniform.
  - **Formula:** for each damage type, compute the EHP against that type
    alone: Σ over layers of `hp / (1 − resist)`, from the existing `tank`
    numbers.
  - **When it fires:** when the worst type's EHP < `HOLE_RATIO` (0.75) × the
    uniform EHP, e.g. "uniform damage hides a hole: EHP vs pure EM is 61k
    (uniform 84k); set damage_profile for the expected enemy".

Both thresholds are module constants, the calibration knobs.

### 4. `INSTRUCTIONS`

- **New first fitting bullet:** before building, optimizing or judging a fit,
  settle what it is for, and ask the user when that isn't clear. For fleet
  fits, call `fitting_guide` with the role and the axes the user gave, and say
  which axes were assumed. Pass its `suggested` conditions and constraints.
- **Best-fit bullet:** `optimize_fit` runs under the role's conditions and
  constraints. A one-stat optimum is a starting point, not a recommendation.
- **Results bullet:** relay `notes` like `warnings`, or say why the fit
  deliberately ignores one.

## Tests

- **T1** `fitting_guide()` lists `general`, the five fleet roles and the axes.
- **T2** `fitting_guide("fleet_mainline", tank="armor", space="wormhole",
  pilots=250)` includes principles tagged armor, wormhole and min_pilots ≤ 250.
  It excludes principles tagged shield, lowsec and max_pilots < 250, and has no
  `unset`.
- **T3** `fitting_guide("fleet_mainline")` excludes every tagged principle and
  lists tank, space and pilots under `unset`.
- **T4** An unknown role, an unknown axis value or `pilots=0` raises
  `ValueError` with the valid values.
- **T5** The YAML is well formed:
  - every principle has `text` and `why`;
  - `when` keys and values are known axes and values;
  - every `suggested.conditions` parses with `conditions.parse`;
  - every `suggested.constraints` passes `search._constraints`.
- **T6** A Rokh with a 50MN MWD gets the propmod note. The same Rokh with a
  500MN MWD, and a cruiser with a 100MN AB, do not.
- **T7** A fit with an open resist hole under uniform damage gets the hole note.
  The same fit with a `damage_profile` set does not, and a fit with balanced
  resists does not.
- **T8** `compare_fits` rows carry `notes`; `test_server` sees the
  `fitting_guide` tool and the new instructions text.

## Out of scope

- Roles outside fleets.
- A user-side override of the YAML. The user edits it in the repo; add a
  data-dir override when the installed app has users who need it.
- Tagged `suggested` conditions.
- Notes in `optimize_fit` / `marginal_swaps` output. Their fits are
  reproducible with `evaluate_fit`, which shows the notes.
- The PDF's section 3: Pareto audit, parameter sweep, permutation testing and
  utility metrics.
