# Fitting Guide and Fit Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent fleet fitting principles before it builds a fit (a `fitting_guide` tool over a YAML file), flag undersized propmods in `evaluate_fit` / `compare_fits` output (`notes`), and point the server instructions at both.

**Architecture:**
- **Guide:** `pyfa_mcp/guide.py` reads `pyfa_mcp/fitting_guide.yaml`, a hand-written, user-maintained data file. It filters principles by tank, space and fleet size and returns plain dicts. It never boots Pyfa.
- **Notes:** `pyfa_mcp/notes.py` reads a calculated eos fit and returns advisory strings. `evaluate.py` calls it inside the existing scratch-fit block.
- **Server:** `server.py` gets one new tool and new instruction text.

**Tech Stack:** Python 3.13, pyyaml (already pinned for eos), pytest, Pyfa's eos.

**Spec:** `docs/superpowers/specs/2026-10-04-fitting-guide-design.md`

## Global Constraints

- No new dependencies. YAML is read with the pinned `pyyaml`.
- `fitting_guide` must not boot Pyfa. It reads only the YAML.
- `when` keys are exactly `tank`, `space`, `min_pilots`, `max_pilots`. Axis values: tank `armor`/`shield`; space `nullsec`/`lowsec`/`wormhole`.
- Data files ship like `unhandled_effects.json`: `pyproject.toml` package data and `packaging/pyfa-mcp.spec` datas.
- `PROPMOD_RATIO = 0.5`, a module constant in `notes.py`.
- `notes` is separate from `warnings`. `warnings` keeps validity and unhandled-effect messages only.
- Run tests with `.venv/Scripts/python.exe -m pytest` (Windows; `uv` is not on PATH in the Bash tool).
- Scripts that evaluate fits need an `if __name__ == "__main__":` guard (Windows spawn).

## Review Focus

- **Case of role and axis values** ("Fleet_Mainline", "Shield"): accepted case-insensitively. Test in Task 1.
- **Offline or online (not active) propmod:** still judged, with its mass addition counted; a 50MN `/OFFLINE` on a Rokh still gets the note. Test in Task 2.
- **Fit with no propmod, or two identical propmods:** no note, or one note (not two identical lines). Test in Task 2.
- **`compare_fits` with variants:** each row carries its own `notes`. Test in Task 2.
- **Frozen build missing the YAML:** the smoke test calls `fitting_guide`, so the release build fails loudly. Covered in Task 3.

---

### Task 1: The guide (YAML, loader, packaging)

**Files:**
- Create: `pyfa_mcp/fitting_guide.yaml`
- Create: `pyfa_mcp/guide.py`
- Modify: `pyproject.toml` (`[tool.setuptools.package-data]`)
- Modify: `packaging/pyfa-mcp.spec:45`
- Test: `tests/test_guide.py`

**Interfaces:**
- Produces: `guide.guide(role: str | None = None, tank: str | None = None, space: str | None = None, pilots: int | None = None) -> dict`. It raises `ValueError` on bad input. Also `guide.GUIDE: Path` and `guide.AXES_OF_WHEN: dict[str, str]` (when-key → axis name), used by the tests.

- [ ] **Step 1: Write the YAML**

Create `pyfa_mcp/fitting_guide.yaml`:

```yaml
# Fleet fitting principles for the fitting_guide tool. Maintained by hand:
# edit freely, no code change needed. Each principle is a default with its
# reason, not a rule; a fit may break one deliberately and say why.
# when: tank (armor|shield), space (nullsec|lowsec|wormhole, or a list),
# min_pilots / max_pilots (fleet size). A tagged principle shows only when
# every key matches the axes passed to fitting_guide.

axes:
  tank:
    armor: "slower, heavier buffer; mids free for tackle and EWAR; armor logi and links"
    shield: "faster, kites and repositions; lows free for damage; shield logi and links"
  space:
    nullsec: "bubbles, bombers, titans and supers, time dilation in big fights"
    lowsec: "no bubbles, gate guns and aggression timers, capital drops"
    wormhole: "hole mass and ship-size limits, no local, system environment effects, capital escalation"
  pilots: "fleet size; changes how much alpha, bombers, time dilation and per-pilot utility matter"

general:
  - text: "Size propulsion to the hull: a propmod's speed gain falls with hull mass. evaluate_fit's notes flag one giving under half its rating."
    why: "An undersized propmod is legal but nearly useless; the ship can't keep up with the fleet."
  - text: "Tank one layer and commit the slots, rigs and resists to it."
    why: "Split shield and armor modules give neither layer enough to matter, and hull bonuses favour one layer."
  - text: "Know the weakest resist of the tank layer, against the damage the enemy actually deals."
    why: "The enemy picks the damage type you are weakest to; uniform damage hides that."
  - text: "Count only damage that applies at the engagement range: weapons within optimal plus falloff with the ammo you will load, drones within drone control range."
    why: "Paper DPS that can't reach the target isn't DPS. fit_graph's damage graph shows DPS by distance."
  - text: "Know how long the capacitor lasts with everything you will run, under the pressure you expect."
    why: "A fit that runs dry mid-fight loses its guns, tank and propulsion."
  - text: "Evaluate under the fight's conditions, not the defaults: damage_profile for the enemy, target for what you shoot, projected for the neuts and webs you will take."
    why: "The defaults (uniform damage, no target, nothing projected) reward paper DPS and EHP."

roles:
  fleet_doctrine:
    summary: "The whole fleet: mainline, logistics, links and support built to one plan."
    principles:
      - text: "One tank layer for the whole fleet."
        why: "Logi repairs one layer, links boost one layer, and one resist profile keeps the fleet predictable for its own logi."
      - text: "One engagement range band; every mainline weapon and ammo choice serves it."
        why: "The FC can hold one range; ships built for another band do no damage."
      - text: "One mainline hull, or a few with matching speed, range and tank."
        why: "The fleet moves at its slowest ship's speed and can only hold range together."
      - text: "About one logi per 5 to 8 mainline ships."
        why: "Fewer and the fleet bleeds ships to sustained DPS; more and it lacks the damage to win."
      - text: "Links for the doctrine's layer (armor or shield bursts), plus skirmish for speed and sig, and information for lock range; a second booster as a spare."
        why: "Bursts multiply the whole fleet's tank and range; a lone booster is a primary target."
      - text: "Support matched to the plan: tackle and webs for a brawl, EWAR and anti-support for a kite or a snipe."
        why: "Support decides who can leave and who can shoot; the wrong mix wastes pilots."
      - text: "Check the doctrine as a whole: evaluate the mainline with conditions.command set to the link fit and projected set to the enemy pressure (neuts, webs), and compare scenarios with compare_fits variants."
        why: "Mainline ships are built to be boosted and repaired; their solo numbers mislead."
      - text: "Armor fleets are slow: they hold a position and brawl or snipe from it; skirmish links and webs make up for speed."
        why: "Armor buffer and plates cost speed and agility."
        when: {tank: armor}
      - text: "Shield fleets trade buffer for speed: they kite and reposition, and live on range control and skirmish links."
        why: "Shield tanks leave the lows for damage and speed, and the hulls are faster."
        when: {tank: shield}
      - text: "Expect bubbles: you can't count on warping out, so the fleet must win or burn out of the bubble."
        why: "Interdictors and HICs hold whole fleets on grid."
        when: {space: nullsec}
      - text: "Expect capital drops: dreads and FAX can be on grid in minutes; plan to kill them, survive them or leave first."
        why: "In lowsec capitals are cheap to bring and decide subcap fights."
        when: {space: lowsec}
      - text: "Size the hulls to the holes you will fight through; battleships and capitals need large holes and burn their mass."
        why: "A fleet that can't jump the hole can't take the fight, and a collapsed hole strands it."
        when: {space: wormhole}
      - text: "Check every ship in the system's environment (conditions.environment: Wolf-Rayet, Pulsar, Magnetar, Black Hole, Cataclysmic Variable, Red Giant)."
        why: "These effects swing resists, damage, cap and reps by tens of percent and can pick the doctrine."
        when: {space: wormhole}
      - text: "At this size every pilot counts: give mainline ships some utility (a point or a web) and pick fights with speed."
        why: "A small fleet can't brute-force; it wins by choosing engagements."
        when: {max_pilots: 60}
      - text: "Expect alpha: buffer and resists must outlast the enemy's first volleys until logi lands reps."
        why: "At this size called primaries die before reps arrive; sustained reps matter less."
        when: {min_pilots: 100}
      - text: "Expect bomb runs: keep support and logi off the main ball's warp-in and spread on landing."
        why: "Bombs kill clumped low-HP ships; bombers come for big fleets."
        when: {min_pilots: 100, space: [nullsec, lowsec, wormhole]}
      - text: "Expect time dilation: cap, ammo and charges must last a long fight."
        why: "Tidi stretches fights to many times their length; ramp-up and burst matter less than sustained performance."
        when: {min_pilots: 200}

  fleet_mainline:
    summary: "The fleet's DPS ship: shoots what the FC calls, holds the fleet's range, survives being primary until logi lands."
    principles:
      - text: "MWD sized to the hull: 500MN on battleships, 50MN on cruisers and battlecruisers, 5MN on frigates and destroyers."
        why: "The FC repositions the fleet; an undersized MWD leaves the ship behind."
      - text: "Weapons and ammo for the doctrine's range band, judged with fit_graph damage by distance, not paper DPS."
        why: "Damage that doesn't reach the band doesn't count."
      - text: "Fit for the fleet: buffer and resists over local repairers."
        why: "Resists multiply every logi rep; local reps waste slots under remote reps."
      - text: "Plug the weakest resist against the expected enemy damage (set damage_profile)."
        why: "The enemy loads the damage type you are weakest to."
      - text: "Cap booster when cap is short under neuts (evaluate with the suggested neut pressure)."
        why: "Fleets get neuted; charges keep guns, tank and MWD running."
      - text: "Drones are utility: light drones against tackle, EWAR drones; don't count drone DPS at the band."
        why: "Drones die or can't reach at fleet range, and anti-tackle saves ships."
      - text: "Lows to tank (plates, resists, damage control); mids for MWD, cap booster, sensor booster, point or web."
        why: "Armor tanks in the lows, so targeting and utility go in the mids."
        when: {tank: armor}
      - text: "Mids to tank (extenders, hardeners), MWD and cap booster; lows for damage mods and a Signal Amplifier."
        why: "Shield tanks in the mids; a Signal Amplifier adds lock range and targets without cap or a mid slot."
        when: {tank: shield}
      - text: "Restrained MWD when the fleet is big: less cap and sig bloom for a little less speed."
        why: "In a big fleet every ship's MWD is on most of the fight; cap and sig matter more than top speed."
        when: {min_pilots: 100}
      - text: "Volley matters: EHP must outlast the enemy's first volleys."
        why: "At this size primaries die in one or two volleys, before logi locks them."
        when: {min_pilots: 100}
      - text: "Carry your own utility: a point or a web when the fleet has few tacklers."
        why: "Small fleets lack dedicated support."
        when: {max_pilots: 60}
      - text: "Check the fit under the system's environment (conditions.environment)."
        why: "Wormhole effects change resists, damage and cap by tens of percent."
        when: {space: wormhole}
    suggested:
      conditions:
        projected: [{item: "Heavy Energy Neutralizer II", count: 2}]
      why: "Neut pressure: shows how long cap lasts when the enemy neuts you. Compare against no conditions with compare_fits variants."

  fleet_logistics:
    summary: "Repairs the fleet: lands reps on the called primary faster than the enemy kills it."
    principles:
      - text: "Remote repairers for the doctrine's tank layer, and a resist tank of your own."
        why: "Logi repairs one layer, and the enemy primaries logi first."
      - text: "Rep range covers the fleet's spread at the band: check the repairers' optimal and falloff."
        why: "A rep that can't reach the primary repairs nothing."
      - text: "Cap stable with reps running, through a cap chain, cap transfers or a cap booster."
        why: "Logi out of cap turns off the fleet's tank."
      - text: "Lock speed and targets: scan resolution and max targets decide how fast reps land on a new primary."
        why: "A primary that dies before you lock it wasn't repaired."
      - text: "Survive being primaried: buffer, resists, low sig and speed, with other logi repairing you."
        why: "Losing logi collapses the fleet's tank."
      - text: "Armor reps land at the end of the cycle: start repairing the primary before the damage lands."
        why: "Late armor reps arrive after the volley that kills."
        when: {tank: armor}
      - text: "Shield reps land at the start of the cycle: they answer damage fast; keep cycling on the primary."
        why: "Shield logi reacts quickly but has less buffer to work with."
        when: {tank: shield}
      - text: "Bombers and alpha target logi: keep logi spread and off the main ball's warp-in, and pre-lock likely primaries."
        why: "In big fleets logi dies to bombs and alpha before it can react."
        when: {min_pilots: 100}
      - text: "Check reps under the system's environment: some (Cataclysmic Variable) boost remote reps, others cut them."
        why: "Environment effects change rep amounts by tens of percent."
        when: {space: wormhole}
    suggested:
      constraints: [{stat: "capacitor.stable", eq: true}]
      why: "Logi must run its reps indefinitely. Add a cap chain as projected cap transfers if the doctrine uses one."

  fleet_command:
    summary: "Runs the command bursts that boost the whole fleet."
    principles:
      - text: "Bursts for the doctrine: armor or shield bursts for its layer, skirmish for speed and sig, information for lock range and scan resolution."
        why: "Bursts multiply the whole fleet's stats; the wrong family boosts nothing the fleet uses."
      - text: "Pick the hull from the game data: optimize_fit with allow.command, or list_ships(bonus=\"Command Burst strength\")."
        why: "Burst strength varies by hull, module and mindlink; newer hulls may beat the ones you remember."
      - text: "Survive above all: a dead booster drops every bonus at once."
        why: "Boosters are primary targets."
      - text: "Stay within burst range of the fleet."
        why: "Ships outside burst range get nothing."
      - text: "Measure the boost on the mainline: evaluate it with conditions.command set to this fit."
        why: "A booster's value is what it adds to the fleet, not its own stats."
      - text: "A second booster with the same bursts."
        why: "The first one gets primaried; bonuses must survive it."
        when: {min_pilots: 100}

  fleet_support:
    summary: "Tackle, webs, EWAR and anti-support: the ships that decide who can leave and who can shoot."
    principles:
      - text: "Tackle holds targets for the mainline: point and scram ranges must reach where the fleet fights."
        why: "A target that warps off wastes the fleet's damage."
      - text: "Webs make brawl fleets work: they slow targets into the mainline's tracking and hold range; find web-bonused hulls with list_ships(bonus=\"Stasis Webifier\")."
        why: "Webs turn missed shots into hits and stop kiting."
      - text: "EWAR (damps, ECM, painters, tracking and guidance disruptors) changes the fight without DPS; judge it by its effect on the target, since it doesn't show in DPS or EHP."
        why: "Utility value is invisible to evaluate_fit's stats."
      - text: "Anti-support: kill or chase off the enemy's tackle and EWAR with fast hulls and tracking weapons or light drones."
        why: "Enemy tackle and EWAR hold and blind the fleet."
      - text: "Support inside the enemy's range needs its own tank or speed."
        why: "Low-HP support dies first."
      - text: "Interdictors and HICs: bubbles hold the enemy fleet, and yours."
        why: "In nullsec bubbles decide whether anyone can leave."
        when: {space: nullsec}
      - text: "No bubbles: tackle is points and scrams only, and anything unpointed warps off."
        why: "Lowsec bans interdiction bubbles."
        when: {space: lowsec}
      - text: "Fewer, multi-role support ships: a mainline ship with a web or point may replace a dedicated tackler."
        why: "Small fleets can't spare pilots for one job."
        when: {max_pilots: 60}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_guide.py`:

```python
import pytest
import yaml

from pyfa_mcp import guide

ROLES = {"fleet_doctrine", "fleet_mainline", "fleet_logistics", "fleet_command", "fleet_support"}


def _texts(result):
    return {p["text"] for p in result["principles"]}


def _tagged(role):
    data = yaml.safe_load(guide.GUIDE.read_text(encoding="utf-8"))
    return data["roles"][role]["principles"]


def test_t1_no_role_lists_roles_axes_and_general():
    result = guide.guide()
    assert set(result["roles"]) == ROLES
    assert set(result["axes"]) == {"tank", "space", "pilots"}
    assert result["general"] and all({"text", "why"} == set(p) for p in result["general"])


def test_t2_axes_pick_matching_principles():
    result = guide.guide("fleet_mainline", tank="armor", space="wormhole", pilots=250)
    texts = _texts(result)
    for p in _tagged("fleet_mainline"):
        when = p.get("when", {})
        spaces = when.get("space", [])
        spaces = spaces if isinstance(spaces, list) else [spaces]
        expected = (when.get("tank", "armor") == "armor"
                    and (not spaces or "wormhole" in spaces)
                    and when.get("min_pilots", 0) <= 250
                    and when.get("max_pilots", 10**9) >= 250)
        assert (p["text"] in texts) == expected, p["text"]
    assert "unset" not in result
    assert result["general"] and result["suggested"]["conditions"]


def test_t3_unset_axes_hide_tagged_principles():
    result = guide.guide("fleet_mainline")
    untagged = {p["text"] for p in _tagged("fleet_mainline") if "when" not in p}
    assert _texts(result) == untagged
    assert set(result["unset"]) == {"tank", "space", "pilots"}
    assert "wormhole" in result["unset"]["space"]


def test_unset_lists_only_axes_the_role_uses():
    result = guide.guide("fleet_command")  # tagged by pilots only
    assert set(result["unset"]) == {"pilots"}


def test_t4_bad_input_names_the_valid_values():
    with pytest.raises(ValueError, match="fleet_mainline"):
        guide.guide("fleet_dps")
    with pytest.raises(ValueError, match="armor, shield"):
        guide.guide("fleet_mainline", tank="hull")
    with pytest.raises(ValueError, match="wormhole"):
        guide.guide("fleet_mainline", space="highsec")
    with pytest.raises(ValueError, match="pilots"):
        guide.guide("fleet_mainline", pilots=0)


def test_case_is_ignored():
    assert guide.guide("Fleet_Mainline", tank="Shield", space="NULLSEC") == \
        guide.guide("fleet_mainline", tank="shield", space="nullsec")


def test_t5_yaml_shape():
    data = yaml.safe_load(guide.GUIDE.read_text(encoding="utf-8"))
    axes = data["axes"]
    for name, role in data["roles"].items():
        assert role["summary"], name
        for p in role["principles"]:
            assert p["text"] and p["why"], p
            assert set(p) <= {"text", "why", "when"}, p
            for key, value in p.get("when", {}).items():
                assert key in guide.AXES_OF_WHEN, (name, key)
                if key in ("tank", "space"):
                    values = value if isinstance(value, list) else [value]
                    assert set(values) <= set(axes[key]), (name, values)
                else:
                    assert isinstance(value, int) and value > 0, (name, key, value)
        assert set(role.get("suggested", {})) <= {"conditions", "constraints", "why"}, name


def test_t5_suggested_conditions_and_constraints_are_usable(booted, zealot_eft, no_fits_left):
    from pyfa_mcp import conditions, evaluate, search, stats
    data = yaml.safe_load(guide.GUIDE.read_text(encoding="utf-8"))
    keys = set(stats.flatten({k: v for k, v in evaluate.evaluate(zealot_eft, None).items()
                              if k not in ("fit", "ship", "applied", "warnings", "notes")}))
    for name, role in data["roles"].items():
        suggested = role.get("suggested", {})
        if "conditions" in suggested:
            evaluate.evaluate(zealot_eft, suggested["conditions"])  # parses and applies
        for stat, _, _ in search._constraints(suggested.get("constraints")):
            assert stat in keys, (name, stat)
    # the mainline's neut pressure really drains the cap sim
    calm = evaluate.evaluate(zealot_eft, None)["capacitor"]
    neuted = evaluate.evaluate(
        zealot_eft, data["roles"]["fleet_mainline"]["suggested"]["conditions"])["capacitor"]
    assert neuted["delta_per_s"] < calm["delta_per_s"]
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_guide.py -v`
Expected: FAIL / ERROR with `ImportError: cannot import name 'guide'`.

- [ ] **Step 4: Write `pyfa_mcp/guide.py`**

```python
"""Fleet fitting principles from fitting_guide.yaml, filtered by tank layer,
space and fleet size. Plain data: never boots Pyfa."""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

GUIDE = Path(__file__).with_name("fitting_guide.yaml")
# when-key -> the axis it reads
AXES_OF_WHEN = {"tank": "tank", "space": "space", "min_pilots": "pilots", "max_pilots": "pilots"}


@functools.cache
def _data() -> dict:
    return yaml.safe_load(GUIDE.read_text(encoding="utf-8"))


def _choice(value: str | None, options, label: str) -> str | None:
    if value is None:
        return None
    if value.casefold() not in options:
        raise ValueError(f"unknown {label} '{value}'; one of: {', '.join(options)}")
    return value.casefold()


def _matches(when: dict, axes: dict) -> bool:
    for key, want in when.items():
        given = axes[AXES_OF_WHEN[key]]
        if given is None:
            return False
        if key == "min_pilots" and given < want:
            return False
        if key == "max_pilots" and given > want:
            return False
        if key in ("tank", "space") and given not in (want if isinstance(want, list) else [want]):
            return False
    return True


def _line(p: dict) -> dict:
    return {"text": p["text"], "why": p["why"]}


def _describe(axis) -> str:
    return axis if isinstance(axis, str) else "; ".join(f"{k}: {v}" for k, v in axis.items())


def guide(role: str | None = None, tank: str | None = None, space: str | None = None,
          pilots: int | None = None) -> dict:
    data = _data()
    role = _choice(role, data["roles"], "role")
    axes = {"tank": _choice(tank, data["axes"]["tank"], "tank"),
            "space": _choice(space, data["axes"]["space"], "space"),
            "pilots": pilots}
    if pilots is not None and pilots < 1:
        raise ValueError("pilots: the fleet size, 1 or more")
    general = [_line(p) for p in data["general"]]
    if role is None:
        return {"general": general,
                "roles": {name: r["summary"] for name, r in data["roles"].items()},
                "axes": data["axes"]}
    entry = data["roles"][role]
    out = {"role": role, "summary": entry["summary"],
           "principles": [_line(p) for p in entry["principles"]
                          if _matches(p.get("when", {}), axes)],
           "general": general}
    if entry.get("suggested"):
        out["suggested"] = entry["suggested"]
    tagged = {AXES_OF_WHEN[k] for p in entry["principles"] for k in p.get("when", {})}
    unset = {a: _describe(data["axes"][a]) for a in ("tank", "space", "pilots")
             if a in tagged and axes[a] is None}
    if unset:
        out["unset"] = unset
    return out
```

- [ ] **Step 5: Ship the YAML in both builds**

In `pyproject.toml`, change:

```toml
pyfa_mcp = ["unhandled_effects.json"]
```

to:

```toml
pyfa_mcp = ["unhandled_effects.json", "fitting_guide.yaml"]
```

In `packaging/pyfa-mcp.spec`, after line 45 (`datas += [(str(ROOT / "pyfa_mcp" / "unhandled_effects.json"), "pyfa_mcp")]`), add:

```python
datas += [(str(ROOT / "pyfa_mcp" / "fitting_guide.yaml"), "pyfa_mcp")]
```

- [ ] **Step 6: Run the tests to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_guide.py -v`
Expected: all PASS. `test_t5_suggested_conditions_and_constraints_are_usable` reads `"notes"` in an exclusion set only, so it passes before Task 2 exists.

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/fitting_guide.yaml pyfa_mcp/guide.py tests/test_guide.py pyproject.toml packaging/pyfa-mcp.spec
git commit -m "Fitting guide: fleet principles in YAML, filtered by tank, space and fleet size"
```

---

### Task 2: Propmod notes in evaluate_fit and compare_fits

**Files:**
- Create: `pyfa_mcp/notes.py`
- Modify: `pyfa_mcp/evaluate.py` (`_evaluate_parsed`, `compare`)
- Test: `tests/test_notes.py`

**Interfaces:**
- Produces:
  - `notes.PROPMOD_RATIO: float`
  - `notes.propmod_note(name: str, rated: float, thrust: float, mass: float) -> str | None`
  - `notes.for_fit(fit) -> list[str]`
  - `evaluate.evaluate(...)` results gain `"notes": list[str]`, placed after `"warnings"`. `compare(...)` rows gain `"notes"` after `"warnings"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_notes.py`:

```python
from pyfa_mcp import evaluate, notes


def _notes(eft, cond=None):
    return evaluate.evaluate(eft, cond)["notes"]


def test_propmod_note_threshold():
    assert notes.propmod_note("x", 500, 15e6, 110e6)       # gain 68% of 500%: fires
    assert notes.propmod_note("x", 500, 150e6, 155e6) is None
    assert notes.propmod_note("x", 135, 150e6, 63e6) is None  # oversized beats its rating


def test_t6_undersized_mwd_on_a_battleship(booted, no_fits_left):
    (note,) = _notes("[Rokh, small mwd]\n50MN Microwarpdrive II\n")
    assert note.startswith("50MN Microwarpdrive II gives +") and "rated +" in note


def test_t6_right_and_oversized_propmods_are_quiet(booted, no_fits_left):
    assert _notes("[Rokh, big mwd]\n500MN Microwarpdrive II\n") == []
    assert _notes("[Hurricane, big ab]\n100MN Afterburner II\n") == []
    assert _notes("[Rifter, frig mwd]\n5MN Microwarpdrive II\n") == []


def test_offline_propmod_still_judged(booted, no_fits_left):
    assert len(_notes("[Rokh, off]\n50MN Microwarpdrive II /OFFLINE\n")) == 1


def test_no_propmod_and_duplicates(booted, no_fits_left):
    assert _notes("[Rokh, none]\n") == []
    assert len(_notes("[Rokh, two]\n50MN Microwarpdrive II\n50MN Microwarpdrive II\n")) == 1


def test_notes_are_not_warnings(booted, no_fits_left):
    result = evaluate.evaluate("[Rokh, small mwd]\n50MN Microwarpdrive II\n", None)
    assert result["warnings"] == [] and len(result["notes"]) == 1


def test_t7_compare_rows_carry_notes(booted, no_fits_left):
    table = evaluate.compare(["[Rokh, a]\n50MN Microwarpdrive II\n",
                              "[Rokh, b]\n500MN Microwarpdrive II\n"], None, None,
                             variants=[{}, {"damage_profile": {"em": 1, "thermal": 0,
                                                               "kinetic": 0, "explosive": 0}}])
    by_fit = {}
    for row in table["rows"]:
        by_fit.setdefault(row["fit"], []).append(len(row["notes"]))
    assert by_fit == {"a": [1, 1], "b": [0, 0]}
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notes.py -v`
Expected: FAIL with `ImportError: cannot import name 'notes'`.

- [ ] **Step 3: Write `pyfa_mcp/notes.py`**

```python
"""Observations on a calculated fit that the stats alone don't make obvious.
Advisory: a deliberate fit may ignore them (evaluate's `warnings` are for
validity and effects Pyfa can't compute)."""
from __future__ import annotations

# Calibration knob: flag a propmod giving under this share of its rated speed
# gain. Right-sized propmods give 0.8-1.0; a 50MN MWD on a battleship ~0.14.
PROPMOD_RATIO = 0.5


def propmod_note(name: str, rated: float, thrust: float, mass: float) -> str | None:
    """Pyfa's propmod effect: max velocity x (1 + speedFactor x thrust / mass / 100)."""
    gain = rated * thrust / mass
    if gain >= PROPMOD_RATIO * rated:
        return None
    return (f"{name} gives +{gain:.0f}% speed of its rated +{rated:.0f}%: too little "
            "thrust for this hull's mass; a larger propmod gives more")


def for_fit(fit) -> list[str]:
    from eos.const import FittingModuleState

    out = []
    mass = fit.ship.getModifiedItemAttr("mass")
    for mod in fit.modules:
        if mod.isEmpty or mod.item.group.name != "Propulsion Module":
            continue
        rated = mod.getModifiedItemAttr("speedFactor")
        thrust = mod.getModifiedItemAttr("speedBoostFactor")
        if not rated or not thrust:
            continue
        # An active propmod's mass addition is already on the ship.
        own = 0 if mod.state >= FittingModuleState.ACTIVE else (
            mod.getModifiedItemAttr("massAddition") or 0)
        note = propmod_note(mod.item.name, rated, thrust, mass + own)
        if note:
            out.append(note)
    return list(dict.fromkeys(out))
```

- [ ] **Step 4: Wire into `evaluate.py`**

Change the import line:

```python
from pyfa_mcp import conditions, drift, eft, eosboot, notes, stats, store
```

Replace `_evaluate_parsed` with:

```python
def _evaluate_parsed(ref: str, cond) -> dict:
    with Scratch() as scratch:
        fit = scratch.add_fit(ref)
        applied = conditions.apply(fit, cond, scratch.add_fit)
        result = stats.fit_stats(fit, conditions.spool_of(cond))
        name, ship = fit.name, fit.ship.item.name
        effect_warnings = drift.effect_warnings(fit)
        fit_notes = notes.for_fit(fit)
    return {"fit": name, "ship": ship, "applied": applied,
            "warnings": warnings_for(result) + effect_warnings + store.pyfa_warnings(ref),
            "notes": fit_notes, **result}
```

In `compare`, change the flatten exclusion and the row:

```python
            flat = stats.flatten({k: v for k, v in result.items()
                                  if k not in ("fit", "ship", "applied", "warnings", "notes")})
```

```python
            rows.append({"fit": result["fit"], "ship": result["ship"], **label,
                         **{k: flat[k] for k in keys}, "warnings": result["warnings"],
                         "notes": result["notes"]})
```

- [ ] **Step 5: Check nothing else flattens the whole evaluate result**

Run: `grep -rn '"warnings")' pyfa_mcp/ tests/`
Expected: every hit that excludes `"warnings"` from a flatten of an evaluate result also excludes `"notes"`. Fix any that don't the same way as Step 4. If `stats.flatten` is fed `notes` (a list), it would show up as a stat column of type list.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notes.py tests/test_evaluate.py tests/test_guide.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/notes.py pyfa_mcp/evaluate.py tests/test_notes.py
git commit -m "evaluate_fit and compare_fits: notes flag a propmod too small for the hull"
```

---

### Task 3: Server tool, instructions, smoke test

**Files:**
- Modify: `pyfa_mcp/server.py` (imports, `INSTRUCTIONS`, `evaluate_fit` docstring, new tool)
- Modify: `packaging/mcp_smoke.py` (`TOOLS`, `CALLS`)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `guide.guide(role, tank, space, pilots) -> dict` (Task 1), with `ValueError` on bad input; `"notes"` in evaluate results (Task 2).
- Produces: MCP tool `fitting_guide(role, tank, space, pilots)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
def test_fitting_guide_tool_needs_no_boot(monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    def no_boot(*a, **k):
        raise AssertionError("fitting_guide booted Pyfa")

    monkeypatch.setattr(server, "_ensure_booted", no_boot)
    assert "fleet_mainline" in server.fitting_guide()["roles"]
    assert server.fitting_guide("fleet_mainline", tank="shield")["role"] == "fleet_mainline"
    with pytest.raises(ToolError, match="unknown role"):
        server.fitting_guide("fleet_dps")


def test_instructions_point_at_the_guide_and_notes():
    assert "fitting_guide" in server.INSTRUCTIONS
    assert "starting point" in server.INSTRUCTIONS
    assert "`notes`" in server.INSTRUCTIONS
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_server.py -v -k "guide or instructions_point"`
Expected: FAIL with `AttributeError: module 'pyfa_mcp.server' has no attribute 'fitting_guide'`.

- [ ] **Step 3: Add the tool and instructions in `server.py`**

Add `guide` to the import list:

```python
from pyfa_mcp import (catalog, conditions, drift, eft, eosboot, evaluate, graphs, guide, pool,
                      pyfadata, register, search, store)
```

In `INSTRUCTIONS`, replace:

```
- Every result has `applied` (what the numbers assume) and `warnings`.
  Tell the user about warnings, and mention the assumptions that matter.
```

with:

```
- Before building, optimizing or judging a fit, settle what it is for; ask
  the user when that isn't clear. For fleet fits and doctrines call
  fitting_guide with the role and whatever the user said about tank layer,
  space and fleet size, say which of those you assumed, and evaluate and
  optimize under its `suggested` conditions and constraints. A fit that
  maximizes one stat under default conditions is a starting point, not a
  recommendation.
- Every result has `applied` (what the numbers assume) and `warnings`.
  Tell the user about warnings, and mention the assumptions that matter.
  evaluate_fit and compare_fits also give `notes`: likely fitting mistakes
  (e.g. a propmod too small for the hull). Fix them, or tell the user why
  the fit deliberately keeps one.
```

In the `evaluate_fit` docstring, change `plus \`applied\` and \`warnings\`.` to `plus \`applied\`, \`warnings\` and \`notes\` (likely fitting mistakes).`

Add the tool after `conditions_format` (no `@_tool`: it must not boot Pyfa or use the eos thread):

```python
@app.tool()
def fitting_guide(role: str | None = None, tank: str | None = None,
                  space: str | None = None, pilots: int | None = None) -> dict:
    """Fleet fitting principles, each with its reason. Call it before building,
    optimizing or judging a fleet fit or doctrine. No role: the general
    principles, the roles (fleet_doctrine, fleet_mainline, fleet_logistics,
    fleet_command, fleet_support) and the axes. With a role, pass what the user
    said: tank (armor, shield), space (nullsec, lowsec, wormhole), pilots (fleet
    size). Returns the role's principles for that fleet, `suggested` conditions
    and constraints for evaluate_fit / compare_fits / optimize_fit, and `unset`:
    the axes not given that would change the advice (ask the user, or say what
    you assumed). Principles are defaults with reasons: a fit may break one
    deliberately, and should say why."""
    try:
        return guide.guide(role, tank, space, pilots)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
```

- [ ] **Step 4: Add the tool to the smoke test**

In `packaging/mcp_smoke.py`, add `"fitting_guide"` to the `TOOLS` set, and add this to `CALLS` after `("conditions_format", {}),`:

```python
    # reads fitting_guide.yaml: fails if the frozen build left the data file out
    ("fitting_guide", {"role": "fleet_mainline"}),
```

- [ ] **Step 5: Run the server tests (includes the stdio smoke test)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_server.py -v`
Expected: all PASS, including `test_smoke_over_stdio`.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -x -q --durations=10`
Expected: all PASS. The search tests are slow, around 15 minutes.

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/server.py packaging/mcp_smoke.py tests/test_server.py
git commit -m "fitting_guide tool; instructions: settle the fit's purpose first, act on notes"
```

---

### Task 4: Hand check with the original prompt

**Files:** none changed unless the check finds a problem.

- [ ] **Step 1: Check the guide's output reads well**

Run:

```bash
.venv/Scripts/python.exe -c "import json; from pyfa_mcp import guide; print(json.dumps(guide.guide('fleet_mainline', tank='shield', space='nullsec', pilots=150), indent=1))"
```

Expected: the untagged mainline principles plus the shield slot line, the Restrained MWD and volley lines (150 ≥ 100); no armor, wormhole or small-fleet lines; no `unset`; `suggested` neut conditions.

- [ ] **Step 2: Report to the user**

Ask the user to rerun the original fleet Rokh prompt against the server built from this branch. Check that the agent calls `fitting_guide` before building and acts on the propmod note. Also ask the user to review the YAML wording; it is theirs to maintain.
