# Characters and Skill Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate and search fits with real pilot skills (Pyfa's All 0/All 5, characters stored in the server, the user's Pyfa characters, or inline skill sheets), and edit stored characters' skills, with export back to Pyfa.

**Architecture:** A new `pyfa_mcp/characters.py` resolves every character reference to a *sheet*: plain data (`{"base": "All 0"|"All 5", "skills": {...}, "alpha_clone", "label"}`) that is itself a valid `conditions.character`. `conditions.apply` builds a temporary Pyfa `Character` from the sheet (named `TEMP_NOTE`, purged with temporary profiles). Search resolves the sheet once in the server process, so worker processes never read the user's Pyfa. The candidate pool leaves out items the character can't use.

**Tech Stack:** Python 3.11, Pyfa's eos (SQLAlchemy mappers for `Character`/`Skill`), pytest. Run everything with `.venv/Scripts/python.exe` (no `uv` on PATH).

**Spec:** `docs/superpowers/specs/2026-10-11-characters-design.md`

## Global Constraints

- No ESI/SSO of our own. The user's Pyfa file is written only by `export_character_to_pyfa` / `export_to_pyfa`.
- Default character stays `All 5`; the default echo stays `"All 5 (default)"`.
- Levels are integers 0..5 (`bool` is not a level).
- Reference forms: `All 0`, `All 5` (built-in, case-insensitive), a stored name or numeric id, `pyfa:<name or id>`.
- Only the main fit gets the character; command and projected fits stay at All 5.
- Export never changes an existing row; a taken name gets ` (2)`, ` (3)`, ...
- Error text style: lower-case start, "did you mean: X?" suggestions, point to the tool that fixes it.
- Tests never touch the real `~/.pyfa` (the `_no_real_pyfa` and `pyfa_home` fixtures).

## Review Focus

1. **Drone count follows skills** — Drones III launches 3 of 5 drones, All 0 launches none (the character must be set and recalculated *before* drones are launched). Test in Task 2.
2. **A Pyfa character holding a skill no longer in the game data or unpublished** — skipped silently, never a crash. Test in Task 1.
3. **An alpha-clone Pyfa character** — the alpha clone travels with the sheet and caps levels in the evaluation. Test in Task 2.
4. **A stored character name that would shadow another form** (`All 5`, `pyfa:x`, `12`, empty) — refused by `copy_character`. Test in Task 3.
5. **One bad character in `compare_fits` variants** — that row gets an `error`, the others still compute. Test in Task 2.

---

### Task 1: Character sheets, temporary characters, purge

**Files:**
- Create: `pyfa_mcp/characters.py`
- Modify: `pyfa_mcp/pyfadata.py` (add `characters()` after `target_profiles()`)
- Modify: `pyfa_mcp/eosboot.py` (`purge_temp_profiles`)
- Test: `tests/test_characters.py` (new)

**Interfaces:**
- Produces:
  - `characters.CharacterError(LookupError)`
  - `characters.BUILTINS = ("All 0", "All 5")`, `characters.PYFA_PREFIX = "pyfa:"`, `characters.SHEET_KEYS = ("base", "skills", "alpha_clone", "label")`
  - `characters.skill(name: str) -> Item` (published skill, case-insensitive; raises `CharacterError` with suggestions)
  - `characters.sheet(value: str | dict) -> dict` (sheet on a built-in base; idempotent on sheets)
  - `characters.levels(sheet: dict) -> dict[str, int]` (every published skill, 0 = untrained)
  - `characters.is_all5(sheet: dict) -> bool`
  - `characters.build(sheet: dict) -> Character | None` (None for plain All 5)
  - Private helpers later tasks use: `_named(ref) -> dict`, `_row_sheet(char, label) -> dict`, `_stored() -> list`, `_find_stored(ref)`, `_builtin(ref) -> str | None`, `_pyfa_rest(ref) -> str | None`, `_alpha(name) -> AlphaClone`, `_set(char, skills: dict)`, `ROMAN`
  - `pyfadata.characters() -> list[Character]` (from the snapshot session)
  - `eosboot.purge_temp_profiles()` now also deletes `Character` rows whose `savedName == TEMP_NOTE`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_characters.py`:

```python
import contextlib
import sqlite3

import pytest

from pyfa_mcp import characters, eosboot
from pyfa_mcp.eosboot import TEMP_NOTE


def _item_id(name: str) -> int:
    import eos.db
    return eos.db.getItem(name).ID


@pytest.fixture
def pyfa_alt(pyfa_home):
    """pyfa_home plus a Pyfa character 'Home Alt': Gunnery III, Medium Energy
    Turret II, and a row for an item id no skill has (a skill CCP removed)."""
    rows = [(_item_id("Gunnery"), 3), (_item_id("Medium Energy Turret"), 2), (999999999, 5)]
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        cid = db.execute("INSERT INTO characters (name, defaultLevel) "
                         "VALUES ('Home Alt', NULL)").lastrowid
        db.executemany('INSERT INTO characterSkills (characterID, itemID, "_Skill__level") '
                       "VALUES (?, ?, ?)", [(cid, i, lvl) for i, lvl in rows])
        db.commit()
    return pyfa_home


def _temp_characters() -> list:
    import eos.db
    return [c for c in eos.db.getCharacterList() if c.savedName == TEMP_NOTE]


def test_builtin_sheets(booted):
    assert characters.sheet("all 5") == {"base": "All 5", "skills": {},
                                         "alpha_clone": None, "label": "All 5"}
    assert characters.sheet(" All 0 ")["base"] == "All 0"
    assert characters.is_all5(characters.sheet("All 5"))
    assert not characters.is_all5(characters.sheet("All 0"))


def test_a_sheet_overrides_its_base_and_resolves_again_unchanged(booted):
    got = characters.sheet({"base": "All 0", "skills": {"gunnery": 4}})
    assert got == {"base": "All 0", "skills": {"Gunnery": 4}, "alpha_clone": None,
                   "label": "custom (base All 0, 1 skill set)"}
    assert characters.sheet(got) == got
    assert characters.sheet({"skills": {"Gunnery": 0, "Rapid Firing": 1}})["label"] == \
        "custom (base All 5, 2 skills set)"


def test_unknown_skill_suggests(booted):
    with pytest.raises(characters.CharacterError, match="did you mean: Gunnery"):
        characters.sheet({"skills": {"Gunery": 4}})
    with pytest.raises(characters.CharacterError, match="unknown skill 'Heat Sink II'"):
        characters.skill("Heat Sink II")


def test_alpha_clone_names(booted):
    assert characters.sheet({"alpha_clone": "alpha clone"})["alpha_clone"] == "Alpha Clone"
    with pytest.raises(characters.CharacterError, match="known: Alpha Clone"):
        characters.sheet({"alpha_clone": "Omega"})


def test_levels_cover_every_published_skill(booted):
    all5 = characters.levels(characters.sheet("All 5"))
    assert set(all5.values()) == {5} and len(all5) > 300
    some = characters.levels(characters.sheet({"base": "All 0", "skills": {"Gunnery": 2}}))
    assert some["Gunnery"] == 2 and some["Rapid Firing"] == 0


def test_a_pyfa_character_is_a_sheet(pyfa_alt):
    got = characters.sheet("pyfa:home alt")
    assert got == {"base": "All 0", "skills": {"Gunnery": 3, "Medium Energy Turret": 2},
                   "alpha_clone": None, "label": "pyfa:Home Alt"}  # the removed skill is skipped


def test_unknown_pyfa_character_suggests(pyfa_alt):
    with pytest.raises(characters.CharacterError, match="did you mean: Home Alt"):
        characters.sheet("pyfa:Home Alr")


def test_build_and_purge(booted):
    import eos.db
    assert characters.build(characters.sheet("All 5")) is None
    char = characters.build(characters.sheet({"base": "All 0", "skills": {"Gunnery": 4}}))
    assert char.savedName == TEMP_NOTE
    assert char.getSkill("Gunnery").level == 4
    assert char.getSkill("Rapid Firing").level == 0
    eos.db.save(char)
    assert _temp_characters()
    eosboot.purge_temp_profiles()
    assert not _temp_characters()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_characters.py -q`
Expected: FAIL with `ImportError: cannot import name 'characters'`

- [ ] **Step 3: Add `pyfadata.characters()`**

In `pyfa_mcp/pyfadata.py`, after `target_profiles()`:

```python
def characters() -> list:
    """The user's own Pyfa characters, built-ins included, from the snapshot."""
    from eos.saveddata.character import Character
    return _session().query(Character).all()
```

- [ ] **Step 4: Purge temporary characters**

In `pyfa_mcp/eosboot.py`, replace `purge_temp_profiles` with:

```python
def purge_temp_profiles() -> None:
    """Delete custom damage/target profiles and characters made for an evaluation.

    A fit refers to them through relationships, so Pyfa saves them along with
    the (temporary) fit; they carry TEMP_NOTE as name.
    """
    import eos.db
    from eos.saveddata.character import Character
    from eos.saveddata.damagePattern import DamagePattern
    from eos.saveddata.targetProfile import TargetProfile

    session = eos.db.saveddata_session
    for column in (DamagePattern.rawName, TargetProfile.rawName, Character.savedName):
        for row in session.query(column.class_).filter(column == TEMP_NOTE).all():
            session.delete(row)
    eos.db.commit()
```

- [ ] **Step 5: Write `pyfa_mcp/characters.py`**

```python
"""Pilot skills: Pyfa's built-in All 0 and All 5, characters stored in the
server's saveddata.db, and characters in the user's own Pyfa ("pyfa:<id or
name>", read-only).

Every reference resolves to a *sheet*, plain data a search worker can apply
without reading the user's Pyfa, and itself a valid `conditions.character`:

    {"base": "All 0" | "All 5", "skills": {skill name: level},
     "alpha_clone": name | None, "label": what `applied` calls it}

An evaluation builds a temporary Pyfa Character from a sheet; it is named
TEMP_NOTE, saved with its temporary fit and purged with it.
"""
from __future__ import annotations

import difflib
import functools

from pyfa_mcp import pyfadata
from pyfa_mcp.eosboot import TEMP_NOTE

BUILTINS = ("All 0", "All 5")
PYFA_PREFIX = "pyfa:"
SHEET_KEYS = ("base", "skills", "alpha_clone", "label")
ROMAN = ("0", "I", "II", "III", "IV", "V")


class CharacterError(LookupError):
    """No such character or skill, or a character that cannot be changed."""

    def __str__(self):  # LookupError would quote the message
        return str(self.args[0]) if self.args else ""


@functools.cache
def _skills() -> dict:
    """Published skills by case-folded name."""
    from eos.saveddata.character import Character
    return {i.name.casefold(): i for i in Character.getSkillList() if i.published}


def skill(name: str):
    item = _skills().get(name.strip().casefold()) if isinstance(name, str) else None
    if item is None:
        close = difflib.get_close_matches(str(name), [i.name for i in _skills().values()],
                                          n=3, cutoff=0.6)
        hint = f" (did you mean: {', '.join(close)}?)" if close else ""
        raise CharacterError(f"unknown skill '{name}'{hint}")
    return item


def _alpha(name: str):
    import eos.db
    clones = eos.db.getAlphaCloneList()
    for clone in clones:
        if clone.alphaCloneName.casefold() == name.strip().casefold():
            return clone
    known = ", ".join(c.alphaCloneName for c in clones)
    raise CharacterError(f"unknown alpha clone '{name}'; known: {known}")


def _row_sheet(char, label: str) -> dict:
    """A Pyfa Character row (stored here or in the user's Pyfa) as a sheet."""
    skills = {}
    for s in list(char.skills):  # Skill.item drops skills CCP removed from the list
        item = s.item
        if s.activeLevel and item is not None and item.published:
            skills[item.name] = s.activeLevel
    alpha = char.alphaClone.alphaCloneName if char.alphaClone is not None else None
    return {"base": "All 0", "skills": dict(sorted(skills.items())),
            "alpha_clone": alpha, "label": label}


def _stored() -> list:
    import eos.db
    return [c for c in eos.db.getCharacterList()
            if c.savedName not in BUILTINS and c.savedName != TEMP_NOTE]


def _pick(chars, ref: str, kind: str, listing: str, prefix: str = ""):
    ref = ref.strip()
    if ref.isdigit():
        for char in chars:
            if char.ID == int(ref):
                return char
        raise CharacterError(f"no {kind} with id {ref}")
    found = [c for c in chars if c.savedName.casefold() == ref.casefold()]
    if len(found) == 1:
        return found[0]
    if found:  # Pyfa allows it
        ids = ", ".join(f"{prefix}{c.ID}" for c in found)
        raise CharacterError(f"several {kind}s are named '{ref}'; use an id: {ids}")
    close = difflib.get_close_matches(ref, [c.savedName for c in chars], n=3, cutoff=0.5)
    hint = f" (did you mean: {', '.join(close)}?)" if close else ""
    raise CharacterError(f"no {kind} named '{ref}'{hint}; see {listing}")


def _builtin(ref: str) -> str | None:
    return next((b for b in BUILTINS if b.casefold() == ref.strip().casefold()), None)


def _pyfa_rest(ref: str) -> str | None:
    """The part after `pyfa:`, or None for any other reference."""
    ref = ref.strip()
    return ref[len(PYFA_PREFIX):] if ref.casefold().startswith(PYFA_PREFIX) else None


def _find_stored(ref: str):
    return _pick(_stored(), ref, "stored character", "list_characters()")


def _named(ref: str) -> dict:
    builtin = _builtin(ref)
    if builtin is not None:
        return {"base": builtin, "skills": {}, "alpha_clone": None, "label": builtin}
    rest = _pyfa_rest(ref)
    if rest is not None:
        char = _pick(pyfadata.characters(), rest, "Pyfa character",
                     'list_characters(source="pyfa")', PYFA_PREFIX)
        return _row_sheet(char, f"{PYFA_PREFIX}{char.savedName}")
    char = _find_stored(ref)
    return _row_sheet(char, char.savedName)


def sheet(value) -> dict:
    """A character condition, a name or {base?, skills?, alpha_clone?, label?}, as a
    sheet on a built-in base. A sheet resolves to itself."""
    if isinstance(value, str):
        return _named(value)
    base = _named(value.get("base", "All 5"))
    asked = value.get("skills", {})
    skills = dict(base["skills"])
    for name, level in asked.items():
        skills[skill(name).name] = level
    alpha = value.get("alpha_clone", base["alpha_clone"])
    if alpha is not None:
        alpha = _alpha(alpha).alphaCloneName
    label = value.get("label") or (
        f"custom (base {base['label']}, {len(asked)} skill{'' if len(asked) == 1 else 's'} set)")
    return {"base": base["base"], "skills": dict(sorted(skills.items())),
            "alpha_clone": alpha, "label": label}


def is_all5(s: dict) -> bool:
    return s["base"] == "All 5" and not s["skills"] and s["alpha_clone"] is None


def levels(s: dict) -> dict:
    """Every published skill's level under a sheet (0: untrained), by name."""
    default = 5 if s["base"] == "All 5" else 0
    out = {i.name: default for i in _skills().values()}
    out.update(s["skills"])
    return out


def _set(char, skills: dict) -> None:
    """Exact levels, no prerequisite cascade: a what-if may break EVE's rules."""
    for name, level in skills.items():
        char.getSkill(skill(name)).setLevel(level, persist=True, ignoreRestrict=True)


def build(s: dict):
    """A temporary Character for a sheet; None for plain All 5, where the fit keeps
    Pyfa's own All 5."""
    from eos.saveddata.character import Character
    if is_all5(s):
        return None
    char = Character(TEMP_NOTE, 5 if s["base"] == "All 5" else None)
    _set(char, s["skills"])
    if s["alpha_clone"] is not None:
        char.alphaCloneID = _alpha(s["alpha_clone"]).ID
    return char
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_characters.py tests/test_pyfadata.py tests/test_conditions.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/characters.py pyfa_mcp/pyfadata.py pyfa_mcp/eosboot.py tests/test_characters.py
git commit -m "Characters: sheets from built-in, stored and Pyfa characters; temporary characters"
```

---

### Task 2: `conditions.character` in evaluations

**Files:**
- Modify: `pyfa_mcp/conditions.py` (`_FIELDS`, `_EXAMPLES`, `Conditions.character` type, `parse`, `apply`, `beyond_the_fit`, `describe`)
- Modify: `pyfa_mcp/evaluate.py` (`_evaluate_parsed` warnings)
- Modify: `pyfa_mcp/characters.py` (add `missing`)
- Modify: `tests/test_conditions.py:42` (the `"Bob"` case)
- Test: `tests/test_characters.py`

**Interfaces:**
- Consumes: `characters.sheet`, `characters.build`, `characters.SHEET_KEYS`, `characters.CharacterError`, `characters.ROMAN`, `characters.BUILTINS`, `pyfadata.characters`
- Produces:
  - `conditions.parse` accepts `character` as `str` or a dict with keys in `SHEET_KEYS`
  - `conditions.apply(...)["character"]` is the sheet's label (`_mark`ed), plus `"; command and projected fits: All 5"` when either is set
  - `characters.missing(fit) -> list[str]`: lines like `"Heavy Pulse Laser II needs Medium Energy Turret V (has 0)"`
  - `conditions.describe()["your_characters"]`: the user's Pyfa character names (`[]` without Pyfa)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_characters.py`:

```python
from pyfa_mcp import conditions, evaluate  # noqa: E402

GUNS = {"base": "All 0", "skills": {"Gunnery": 5, "Medium Energy Turret": 5}}
DRONES = "[Vexor, drones]\n\n\n\nHammerhead II x5\n"


def _dps(eft: str, character) -> float:
    return evaluate.evaluate(eft, {"character": character})["offense"]["dps"]["total"]


def test_skills_change_the_numbers(booted, zealot_eft, no_fits_left):
    assert _dps(zealot_eft, "All 0") < _dps(zealot_eft, GUNS) < _dps(zealot_eft, "All 5")
    assert not _temp_characters()


def test_applied_names_the_character(booted, zealot_eft, no_fits_left):
    assert evaluate.evaluate(zealot_eft, {"character": GUNS})["applied"]["character"] == \
        "custom (base All 0, 2 skills set)"
    assert evaluate.evaluate(zealot_eft, {"character": "all 0"})["applied"]["character"] == \
        "All 0"
    boosted = evaluate.evaluate(zealot_eft, {"character": "All 0", "command": [
        {"fit": "[Damnation, boosts]\n\n\nArmor Command Burst II, Armor Energizing Charge\n"}]})
    assert boosted["applied"]["character"] == "All 0; command and projected fits: All 5"


def test_a_sheet_defaults_to_all_5_and_saves_nothing(pyfa_alt, zealot_eft, no_fits_left):
    assert _dps(zealot_eft, {"skills": {"Medium Energy Turret": 0}}) < _dps(zealot_eft, "All 5")
    trained = {"base": "pyfa:Home Alt", "skills": {"Medium Energy Turret": 5}}
    assert _dps(zealot_eft, trained) > _dps(zealot_eft, "pyfa:Home Alt")
    assert characters.sheet("pyfa:Home Alt")["skills"]["Medium Energy Turret"] == 2


def test_missing_skills_are_warnings(booted, zealot_eft, no_fits_left):
    warnings = evaluate.evaluate(zealot_eft, {"character": "All 0"})["warnings"]
    lasers = [w for w in warnings if w.startswith("Heavy Pulse Laser II needs ")]
    assert lasers and "Medium Energy Turret V (has 0)" in lasers[0]
    assert not [w for w in evaluate.evaluate(zealot_eft, None)["warnings"] if " needs " in w]


def test_drones_launched_follow_the_drones_skill(booted, no_fits_left):
    def launched(character):
        return evaluate.evaluate(DRONES, {"character": character})["applied"]["drones"]
    assert launched({"skills": {"Drones": 3}}) == ["Hammerhead II: 3 of 5 launched (default)"]
    assert launched("All 0") == ["Hammerhead II: 0 of 5 launched (default)"]


def test_an_alpha_clone_caps_skills(booted, zealot_eft, no_fits_left):
    assert _dps(zealot_eft, {"alpha_clone": "Alpha Clone"}) < _dps(zealot_eft, "All 5")


def test_compare_variants_vary_the_character(booted, zealot_eft, no_fits_left):
    table = evaluate.compare([zealot_eft], None, ["offense.dps.total"], variants=[
        {"character": "All 0"}, {"character": GUNS}, {"character": "Nobody"}])
    rows = table["rows"]
    assert rows[0]["offense.dps.total"] < rows[1]["offense.dps.total"]
    assert "no stored character named 'Nobody'" in rows[2]["error"]


def test_a_failed_evaluation_leaves_no_character(booted, zealot_eft, no_fits_left):
    with pytest.raises(conditions.ConditionsError):
        evaluate.evaluate(zealot_eft, {"character": GUNS, "module_states": [
            {"module": "Not Fitted", "state": "active"}]})
    assert not _temp_characters()


@pytest.mark.parametrize("raw, message", [
    ({"character": 5}, "character must be a name"),
    ({"character": {"bogus": 1}}, "unknown key 'bogus'"),
    ({"character": {"skills": {"Gunnery": 6}}}, "level 0-5"),
    ({"character": {"skills": {"Gunnery": True}}}, "level 0-5"),
    ({"character": {"base": 3}}, "character.base"),
])
def test_parse_rejects_bad_characters(raw, message):
    with pytest.raises(conditions.ConditionsError, match=message):
        conditions.parse(raw)


def test_conditions_format_lists_pyfa_characters(pyfa_alt):
    assert conditions.describe()["your_characters"] == ["Home Alt"]
```

In `tests/test_conditions.py`, the parametrized case `({"character": "Bob"}, "All 5"),` no longer fails at parse time (names resolve at apply). Replace that line with:

```python
    ({"character": ["All 5"]}, "character must be a name"),
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_characters.py tests/test_conditions.py -q`
Expected: FAIL (`character: only "All 5" is supported`, `missing` undefined, `your_characters` missing)

- [ ] **Step 3: Add `characters.missing`**

Append to `pyfa_mcp/characters.py`:

```python
def missing(fit) -> list[str]:
    """What the fit needs that its character lacks, one line per item (Pyfa's own
    check, which skips rigs)."""
    from service.character import Character as CharacterService

    char, out = fit.character, []
    if char is None:
        return out
    for thing, needs in CharacterService.getInstance().checkRequirements(fit).items():
        parts = []
        for name, (level, skill_id, subs) in needs.items():
            part = f"{name} {ROMAN[level]} (has {ROMAN[char.getSkill(skill_id).level]})"
            if subs:
                part += ", itself needing " + ", ".join(
                    f"{n} {ROMAN[lvl]}" for n, (lvl, _, _) in subs.items())
            parts.append(part)
        out.append(f"{thing.name} needs " + "; ".join(parts))
    return out
```

- [ ] **Step 4: Parse the new forms**

In `pyfa_mcp/conditions.py`:

Add `characters` to the imports: `from pyfa_mcp import characters, pyfadata`.

Replace the `"character"` entry of `_FIELDS`:

```python
    "character": "Pilot skills: \"All 5\" (default), \"All 0\", a character stored here, "
                 "or \"pyfa:<name>\" (list_characters); or a sheet {base: one of those "
                 "(default \"All 5\"), skills: {skill: level 0-5}, alpha_clone?: "
                 "\"Alpha Clone\", label?: its name in applied}. Only this fit uses it: "
                 "command and projected fits fly at All 5.",
```

Append to `_EXAMPLES`:

```python
    {"character": {"base": "pyfa:My Alt", "skills": {"Surgical Strike": 4}}},
```

Change the dataclass field: `character: str | dict = "All 5"`.

In `parse`, replace the two `character` lines with:

```python
    character = raw.get("character", "All 5")
    if isinstance(character, dict):
        extra = set(character) - set(characters.SHEET_KEYS)
        if extra:
            raise ConditionsError(f"character: unknown key '{sorted(extra)[0]}'; known: "
                                  + ", ".join(characters.SHEET_KEYS))
        if not isinstance(character.get("base", "All 5"), str):
            raise ConditionsError("character.base must be a character name")
        skills = character.get("skills", {})
        if not isinstance(skills, dict) or not all(
                isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
                and 0 <= v <= 5 for k, v in skills.items()):
            raise ConditionsError("character.skills must be {skill name: level 0-5}")
        for key in ("alpha_clone", "label"):
            if character.get(key) is not None and not isinstance(character[key], str):
                raise ConditionsError(f"character.{key} must be a string")
    elif not isinstance(character, str):
        raise ConditionsError("character must be a name or a {base, skills} sheet")
```

and pass `character=character,` in the `Conditions(...)` call.

- [ ] **Step 5: Apply the character first, then echo it**

In `pyfa_mcp/conditions.py`, add before `_mark`:

```python
def _apply_character(fit, value) -> str:
    """Give the fit the character; recalculated now, since the drones launched next
    depend on skills."""
    from service.fit import Fit as FitService
    try:
        sheet = characters.sheet(value)
        char = characters.build(sheet)
    except characters.CharacterError as exc:
        raise ConditionsError(f"character: {exc}") from exc
    if char is not None:
        fit.character = char
        FitService.getInstance().recalc(fit)
    return sheet["label"]
```

In `apply`, make it the first step:

```python
    character = _apply_character(fit, cond.character)
    fit.damagePattern = damage_pattern(cond)
```

and replace the `"character"` echo line with:

```python
        "character": _mark(character, "character", cond)
                     + ("; command and projected fits: All 5"
                        if cond.command or cond.projected else ""),
```

In `beyond_the_fit`, change the `skills_damage_target` entry to:

```python
        "skills_damage_target": {"how": "character (a name or a skill sheet), "
                                        "damage_profile, target",
                                 "options": "list_characters(); see damage_profiles "
                                            "and target_profiles"},
```

In `describe`, inside the `try`, add the user's characters:

```python
        mine = {"your_damage_profiles": sorted(pyfadata.damage_profiles()),
                "your_target_profiles": sorted(pyfadata.target_profiles()),
                "your_characters": sorted(
                    c.savedName for c in pyfadata.characters()
                    if c.savedName not in characters.BUILTINS) if pyfadata.installed() else []}
```

- [ ] **Step 6: Missing skills in evaluate warnings**

In `pyfa_mcp/evaluate.py`, import `characters` (`from pyfa_mcp import catalog, characters, conditions, ...`). In `_evaluate_parsed`, inside the `with` block after `effect_warnings = ...`:

```python
        skill_warnings = characters.missing(fit)
```

and in the returned dict:

```python
            "warnings": warnings_for(result) + skill_warnings + effect_warnings
                        + store.pyfa_warnings(ref),
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_characters.py tests/test_conditions.py tests/test_evaluate.py -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add pyfa_mcp/conditions.py pyfa_mcp/evaluate.py pyfa_mcp/characters.py tests/test_characters.py tests/test_conditions.py
git commit -m "Characters: conditions.character takes names and skill sheets; missing-skill warnings"
```

---

### Task 3: Stored characters: list, get, copy, set_skills, delete

**Files:**
- Modify: `pyfa_mcp/characters.py` (append the tool functions)
- Test: `tests/test_characters.py`

**Interfaces:**
- Consumes: Task 1 helpers (`_named`, `_row_sheet`, `_stored`, `_find_stored`, `_builtin`, `_pyfa_rest`, `_alpha`, `_set`, `levels`, `skill`)
- Produces:
  - `list_characters(source: str = "server") -> list[dict]`, rows `{"id", "name", "skills_at_v", "skill_points", "alpha_clone"}`; built-in ids are their names, Pyfa ids `"pyfa:<n>"`
  - `get_character(ref: str, group: str | None = None) -> dict`
  - `copy_character(ref: str, name: str) -> dict` (a list row)
  - `set_skills(ref: str, skills: dict) -> dict`, `{"character": name, "changed": [{"skill", "from", "to", "why"}]}`
  - `delete_character(ref: str) -> dict`, `{"deleted": {"id", "name"}}`
  - `_editable(ref) -> Character` (stored only)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_characters.py`:

```python
@pytest.fixture
def stored(booted):
    """Deletes the stored characters a test made."""
    yield
    for row in characters.list_characters():
        if row["name"] not in characters.BUILTINS:
            characters.delete_character(str(row["id"]))


def _level(ref: str, name: str) -> int:
    return characters.levels(characters.sheet(ref))[name]


def test_server_list_starts_with_the_builtins(stored):
    rows = characters.list_characters()
    assert [r["name"] for r in rows] == ["All 0", "All 5"]
    assert rows[0]["skill_points"] == 0 and rows[1]["skills_at_v"] > 300
    with pytest.raises(characters.CharacterError, match="source"):
        characters.list_characters("eve")


def test_copy_from_pyfa_then_use_it(pyfa_alt, stored, zealot_eft, no_fits_left):
    assert [r["name"] for r in characters.list_characters("pyfa")] == ["pyfa:Home Alt"]
    row = characters.copy_character("pyfa:Home Alt", "Main")
    assert row["name"] == "Main" and row["skill_points"] > 0
    assert characters.sheet("main")["skills"] == {"Gunnery": 3, "Medium Energy Turret": 2}
    assert _dps(zealot_eft, "Main") == _dps(zealot_eft, "pyfa:Home Alt")
    assert characters.sheet(str(row["id"]))["label"] == "Main"  # by id too


@pytest.mark.parametrize("name", ["", "  ", "All 5", "all 0", "pyfa:x", "12", TEMP_NOTE])
def test_copy_refuses_names_that_shadow_other_forms(stored, name):
    with pytest.raises(characters.CharacterError):
        characters.copy_character("All 0", name)


def test_copy_refuses_a_taken_name(stored):
    characters.copy_character("All 0", "Alt")
    with pytest.raises(characters.CharacterError, match="already named 'Alt'"):
        characters.copy_character("All 5", "alt")


def test_raising_a_skill_trains_its_prerequisites(stored):
    characters.copy_character("All 0", "Fresh")
    result = characters.set_skills("Fresh", {"Medium Energy Turret": 3})
    by_skill = {c["skill"]: c for c in result["changed"]}
    assert by_skill["Medium Energy Turret"] == {"skill": "Medium Energy Turret", "from": 0,
                                                "to": 3, "why": "asked"}
    assert by_skill["Gunnery"]["why"] == "prerequisite" and by_skill["Gunnery"]["to"] >= 1
    assert _level("Fresh", "Medium Energy Turret") == 3


def test_lowering_a_skill_untrains_what_needs_it(stored):
    characters.copy_character("All 5", "Vet")
    result = characters.set_skills("Vet", {"Gunnery": 1})
    untrained = [c for c in result["changed"] if c["why"] != "asked"]
    assert untrained and all(c["why"] == "untrained: needs a lowered skill" for c in untrained)
    assert _level("Vet", "Medium Energy Turret") == 0


@pytest.mark.parametrize("skills, message", [
    ({"Gunnery": 4, "Gunery": 4}, "did you mean: Gunnery"),
    ({"Gunnery": 4, "Rapid Firing": 6}, "integer 0-5"),
    ({"Gunnery": 4, "Rapid Firing": True}, "integer 0-5"),
    ({}, "non-empty"),
])
def test_a_bad_set_skills_changes_nothing(stored, skills, message):
    characters.copy_character("All 0", "Careful")
    with pytest.raises(characters.CharacterError, match=message):
        characters.set_skills("Careful", skills)
    assert _level("Careful", "Gunnery") == 0


@pytest.mark.parametrize("ref", ["All 5", "pyfa:Home Alt"])
def test_builtin_and_pyfa_characters_are_read_only(pyfa_alt, stored, ref):
    for call in (lambda: characters.set_skills(ref, {"Gunnery": 1}),
                 lambda: characters.delete_character(ref)):
        with pytest.raises(characters.CharacterError, match="copy_character"):
            call()


def test_get_character(stored):
    characters.copy_character("All 0", "Shown")
    characters.set_skills("Shown", {"Gunnery": 2})
    got = characters.get_character("Shown")
    assert got["skills"] == {"Gunnery": {"Gunnery": 2}} and got["alpha_clone"] is None
    group = characters.get_character("Shown", group="gunnery")
    assert group["group"] == "Gunnery" and group["skills"]["Rapid Firing"] == 0
    with pytest.raises(characters.CharacterError, match="groups: "):
        characters.get_character("Shown", group="Gunnry")


def test_delete(stored):
    characters.copy_character("All 0", "Gone")
    assert characters.delete_character("gone")["deleted"]["name"] == "Gone"
    with pytest.raises(characters.CharacterError, match="no stored character named 'Gone'"):
        characters.sheet("Gone")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_characters.py -q`
Expected: FAIL with `AttributeError: module 'pyfa_mcp.characters' has no attribute 'list_characters'`

- [ ] **Step 3: Implement the tools**

Append to `pyfa_mcp/characters.py`:

```python
_SP = (0, 250, 1415, 8000, 45255, 256000)  # skill points per rank at each level


def _skill_points(lv: dict) -> int:
    return sum(round(_SP[level] * (skill(name).getAttribute("skillTimeConstant") or 1))
               for name, level in lv.items() if level)


def _entry(ref_id, s: dict) -> dict:
    lv = levels(s)
    return {"id": ref_id, "name": s["label"],
            "skills_at_v": sum(1 for v in lv.values() if v == 5),
            "skill_points": _skill_points(lv), "alpha_clone": s["alpha_clone"]}


def list_characters(source: str = "server") -> list[dict]:
    if source == "server":
        stored = [_entry(c.ID, _row_sheet(c, c.savedName)) for c in _stored()]
        return ([_entry(b, _named(b)) for b in BUILTINS]
                + sorted(stored, key=lambda e: e["name"].casefold()))
    if source == "pyfa":
        rows = [_entry(f"{PYFA_PREFIX}{c.ID}", _row_sheet(c, f"{PYFA_PREFIX}{c.savedName}"))
                for c in pyfadata.characters() if c.savedName not in BUILTINS]
        return sorted(rows, key=lambda e: e["name"].casefold())
    raise CharacterError('source must be "server" or "pyfa"')


def get_character(ref: str, group: str | None = None) -> dict:
    s = _named(ref)
    lv = levels(s)
    by_group: dict[str, dict] = {}
    for name, level in sorted(lv.items()):
        by_group.setdefault(skill(name).group.name, {})[name] = level
    head = {"name": s["label"], "alpha_clone": s["alpha_clone"],
            "skill_points": _skill_points(lv)}
    if group is None:
        trained = {g: {n: lvl for n, lvl in skills.items() if lvl}
                   for g, skills in sorted(by_group.items())}
        return {**head, "skills": {g: v for g, v in trained.items() if v}}
    found = next((g for g in by_group if g.casefold() == group.strip().casefold()), None)
    if found is None:
        raise CharacterError(f"no skill group '{group}'; groups: {', '.join(sorted(by_group))}")
    return {**head, "group": found, "skills": by_group[found]}


def _new_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise CharacterError("a character needs a name")
    if _builtin(name) or _pyfa_rest(name) is not None or name.isdigit() or name == TEMP_NOTE:
        raise CharacterError(f"'{name}' cannot name a stored character: it reads as "
                             "another kind of reference")
    if any(c.savedName.casefold() == name.casefold() for c in _stored()):
        raise CharacterError(f"a stored character is already named '{name}'; "
                             "delete it or pick another name")
    return name


def copy_character(ref: str, name: str) -> dict:
    import eos.db
    from eos.saveddata.character import Character
    s = _named(ref)
    char = Character(_new_name(name), None)
    _set(char, {n: lvl for n, lvl in levels(s).items() if lvl})
    if s["alpha_clone"] is not None:
        char.alphaCloneID = _alpha(s["alpha_clone"]).ID
    eos.db.save(char)
    return _entry(char.ID, _row_sheet(char, char.savedName))


def _editable(ref: str):
    if _builtin(ref) or _pyfa_rest(ref) is not None:
        raise CharacterError(f"'{ref.strip()}' is read-only; copy_character it to a new "
                             "name and edit the copy")
    return _find_stored(ref)


def set_skills(ref: str, skills: dict) -> dict:
    """Pyfa's own editing: raising trains missing prerequisites; lowering untrains
    the skills that need it (Pyfa's strict skill levels)."""
    import eos.db
    from service.character import Character as CharacterService

    char = _editable(ref)
    if not isinstance(skills, dict) or not skills:
        raise CharacterError("skills: a non-empty {skill name: level 0-5} object")
    asked = {}
    for name, level in skills.items():  # check everything before changing anything
        item = skill(name)
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 5:
            raise CharacterError(f"skills: {item.name} needs an integer 0-5, not {level!r}")
        asked[item.ID] = (item, level)
    before = {s.itemID: s.level for s in char.skills}
    for item, level in asked.values():
        target = char.getSkill(item)
        if level > target.level:
            CharacterService._trainSkillReqs(char, target, True)
        target.setLevel(level, persist=True)
    eos.db.commit()
    changed = []
    for s in char.skills:
        old, new = before.get(s.itemID, 0), s.level
        if old != new and s.item is not None:
            why = ("asked" if s.itemID in asked else
                   "prerequisite" if new > old else "untrained: needs a lowered skill")
            changed.append({"skill": s.item.name, "from": old, "to": new, "why": why})
    return {"character": char.savedName,
            "changed": sorted(changed, key=lambda c: c["skill"])}


def delete_character(ref: str) -> dict:
    import eos.db
    char = _editable(ref)
    entry = {"id": char.ID, "name": char.savedName}
    eos.db.saveddata_session.delete(char)
    eos.db.commit()
    return {"deleted": entry}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_characters.py -q`
Expected: PASS. If `test_lowering_a_skill_untrains_what_needs_it` fails because nothing was untrained, check `eos.config.settings["strictSkillLevels"]` is `True` in the booted server (Pyfa's default). Don't work around it in the test.

- [ ] **Step 5: Commit**

```bash
git add pyfa_mcp/characters.py tests/test_characters.py
git commit -m "Characters: list, get, copy, set_skills, delete stored characters"
```

---

### Task 4: Export a character to Pyfa

**Files:**
- Modify: `pyfa_mcp/pyfadata.py` (`_free_name`, new `_export`, `export_fit` on top of it, new `export_character`)
- Modify: `pyfa_mcp/characters.py` (append `export_character_to_pyfa`)
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `characters._named`, `characters.levels`, `characters.skill`, `characters._alpha`
- Produces:
  - `pyfadata.export_character(levels: dict[int, int], alpha_clone_id: int | None, name: str) -> dict`, `{"id": "pyfa:<n>", "name", "backup"}`
  - `characters.export_character_to_pyfa(ref: str, name: str | None = None) -> dict`
  - `pyfadata.export_fit` keeps its signature, behaviour and messages

- [ ] **Step 1: Write the failing tests**

Change the file's import line to `from pyfa_mcp import characters, eft, pyfadata, store`, then append to `tests/test_export.py`:

```python
def test_export_character_inserts_a_new_one_after_a_backup(pyfa_home):
    before = _digest(pyfa_home / "saveddata.db")
    result = characters.export_character_to_pyfa("All 0", "Fresh Alt")
    assert result["name"] == "Fresh Alt" and result["id"].startswith("pyfa:")
    assert _digest(Path(result["backup"])) == before
    assert characters.sheet(result["id"])["skills"] == {}


def test_export_character_carries_skills_and_never_overwrites(pyfa_home):
    characters.copy_character("All 0", "Trained")
    try:
        characters.set_skills("Trained", {"Gunnery": 4})
        first = characters.export_character_to_pyfa("Trained")
        second = characters.export_character_to_pyfa("Trained")
    finally:
        characters.delete_character("Trained")
    assert (first["name"], second["name"]) == ("Trained", "Trained (2)")
    assert characters.sheet(second["id"])["skills"]["Gunnery"] == 4


def test_export_character_refused_while_pyfa_runs(pyfa_home, monkeypatch):
    monkeypatch.setattr(pyfadata, "_pyfa_running", lambda: True)
    _refused(pyfa_home, "Pyfa is running",
             lambda: characters.export_character_to_pyfa("All 5", "X"))


def test_export_character_refused_on_another_schema_version(pyfa_home):
    with contextlib.closing(sqlite3.connect(pyfa_home / "saveddata.db")) as db:
        from eos.db import migration
        db.execute(f"PRAGMA user_version = {migration.getAppVersion() - 1}")
        db.commit()
    _refused(pyfa_home, "character editor",
             lambda: characters.export_character_to_pyfa("All 5", "X"))
```

Also extend `_refused`'s `pytest.raises` tuple with `characters.CharacterError`:

```python
    with pytest.raises((store.StoreError, pyfadata.PyfaDataError, eft.EftError,
                        characters.CharacterError), match=match):
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_export.py -q`
Expected: the four new tests FAIL with `AttributeError: ... no attribute 'export_character_to_pyfa'`; the fit export tests still PASS

- [ ] **Step 3: Share export_fit's guards**

In `pyfa_mcp/pyfadata.py`, replace `_free_name` and `export_fit` with:

```python
def _free_name(taken, wanted: str) -> str:
    taken = {name.casefold() for name in taken}
    name, n = wanted, 2
    while name.casefold() in taken:
        name, n = f"{wanted} ({n})", n + 1
    return name


def _export(make, fallback: str) -> dict:
    """Insert one new row into the user's Pyfa; never changes an existing one.

    Refuses while Pyfa runs or on another schema version (`fallback` says what the
    user can do instead), backs Pyfa's file up first, and removes the backup when
    the write failed. make(session) -> (row, entry(row) -> dict).
    """
    from eos.db import migration
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from pyfa_mcp import eosboot

    db = _user_db()
    if _pyfa_running():
        raise PyfaDataError("Pyfa is running: ask the user to close it, then export again")
    engine = create_engine(f"sqlite:///{db}")
    try:
        have, want = migration.getVersion(engine), migration.getAppVersion()
        if have != want:
            raise PyfaDataError(
                f"not exported: the user's Pyfa database is version {have}, but Pyfa "
                f"{eosboot.pyfa_version()} inside pyfa-mcp writes version {want}. {fallback}")
        with Session(bind=engine, autoflush=False, expire_on_commit=False) as session:
            row, entry = make(session)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup = db.with_name(f"saveddata_pyfa-mcp-backup_{stamp}.db")
            # Pyfa is not running, so a plain copy is exact (as Pyfa's own
            # migration backups are): restoring is a rename.
            shutil.copy2(db, backup)
            try:
                _insert(session, row)
            except Exception as exc:
                session.rollback()
                backup.unlink(missing_ok=True)  # nothing changed: nothing to restore
                raise PyfaDataError(f"not exported, nothing was changed: {exc}") from exc
            return {**entry(row), "backup": str(backup)}
    finally:
        engine.dispose()


def export_fit(eft_text: str, name: str | None = None) -> dict:
    """Insert eft_text as a new fit in the user's Pyfa. Never changes an existing row.

    The caller has already checked the EFT strictly (store.export_to_pyfa).
    """
    from eos.const import ImplantLocation
    from eos.saveddata.character import Character
    from eos.saveddata.damagePattern import DamagePattern
    from eos.saveddata.fit import Fit
    from service.port import Port

    def make(session):
        all5 = session.query(Character).filter(Character.savedName == "All 5").first()
        if all5 is None:
            raise PyfaDataError("the user's Pyfa database has no 'All 5' character; "
                                "start Pyfa once, close it, and export again")
        fit = Port.importAuto(eft_text)[2][0]
        fit.name = _free_name((n for (n,) in session.query(Fit.name)), name or fit.name)
        # Fields Port.importFitFromBuffer sets on an import; here fixed to
        # All 5, uniform damage, no target and the EFT's own implants,
        # rather than the user's Pyfa defaults.
        fit.character = all5
        fit.damagePattern = DamagePattern.getDefaultBuiltin()
        fit.implantLocation = ImplantLocation.FIT
        return fit, lambda f: {"id": f"pyfa:{f.ID}", "name": f.name, "ship": f.ship.item.name}

    return _export(make, "Give the user the EFT text (get_fit) to import in Pyfa instead")


def export_character(levels: dict[int, int], alpha_clone_id: int | None, name: str) -> dict:
    """Insert a new character in the user's Pyfa: skills by type id, under a free name."""
    from eos.saveddata.character import Character

    def make(session):
        taken = (n for (n,) in session.query(Character.savedName))
        char = Character(_free_name(taken, name), None)
        for skill_id, level in levels.items():
            char.getSkill(skill_id).setLevel(level, persist=True, ignoreRestrict=True)
        char.alphaCloneID = alpha_clone_id
        return char, lambda c: {"id": f"pyfa:{c.ID}", "name": c.savedName}

    return _export(make, "Set the skills in Pyfa's character editor instead")
```

- [ ] **Step 4: Add the character export**

Append to `pyfa_mcp/characters.py`:

```python
def export_character_to_pyfa(ref: str, name: str | None = None) -> dict:
    s = _named(ref)
    by_id = {skill(n).ID: lvl for n, lvl in levels(s).items() if lvl}
    alpha = _alpha(s["alpha_clone"]).ID if s["alpha_clone"] is not None else None
    wanted = (name or "").strip() or s["label"].removeprefix(PYFA_PREFIX)
    return pyfadata.export_character(by_id, alpha, wanted)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_export.py tests/test_pyfadata.py tests/test_characters.py -q`
Expected: PASS (the fit export tests are unchanged and still pass)

- [ ] **Step 6: Commit**

```bash
git add pyfa_mcp/pyfadata.py pyfa_mcp/characters.py tests/test_export.py
git commit -m "Characters: export a character to the user's Pyfa as a new one"
```

---

### Task 5: Characters in fit search

**Files:**
- Modify: `pyfa_mcp/characters.py` (append `usable`, `fingerprint`)
- Modify: `pyfa_mcp/search.py` (`_portable`, `_pool`)
- Modify: `pyfa_mcp/candidates.py` (`build`)
- Test: `tests/test_characters.py`

**Interfaces:**
- Consumes: `characters.sheet`, `conditions.parse`
- Produces:
  - `characters.usable(char) -> Callable[[Item], bool] | None` (None for All 5 or no character)
  - `characters.fingerprint(char) -> tuple | None`
  - `search._portable(raw)["character"]` is a sheet
  - pool exclusion reason `"character lacks skills"`; `Pool.availability_note` says so when it applies

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_characters.py`:

```python
from pyfa_mcp import bench, candidates, search  # noqa: E402

NO_MET_V = {"skills": {"Medium Energy Turret": 4}}


def test_search_sends_workers_a_sheet(pyfa_alt):
    assert search._portable({"character": "pyfa:Home Alt"})["character"] == \
        characters.sheet("pyfa:Home Alt")
    with pytest.raises(conditions.ConditionsError, match="character must be a name"):
        search._portable({"character": 7})


def _pool(zealot_eft, character, availability=None):
    with bench.Bench(zealot_eft, {"character": character}) as b:
        return candidates.build(b.fit, {"module"}, None, availability)


def test_the_pool_leaves_out_what_the_character_cannot_use(booted, zealot_eft, no_fits_left):
    pool = _pool(zealot_eft, NO_MET_V)
    names = {c.name for c in pool.candidates}
    assert "Heavy Pulse Laser II" not in names and "Heavy Pulse Laser I" in names
    assert {"name": "Heavy Pulse Laser II", "group": "Energy Weapon",
            "reason": "character lacks skills"} in pool.excluded
    assert "lacks skills" in pool.availability_note
    assert "Heavy Pulse Laser II" in {c.name for c in _pool(zealot_eft, NO_MET_V, "all").candidates}
    assert "Heavy Pulse Laser II" in {c.name for c in _pool(zealot_eft, "All 5").candidates}


def test_marginal_swaps_measure_with_the_character(booted, zealot_eft, no_fits_left):
    result = search.marginal_swaps(zealot_eft, "offense.dps.total", {"character": GUNS},
                                   top_n=3)
    assert result["baseline"] == pytest.approx(_dps(zealot_eft, GUNS))
    assert result["applied"]["character"] == "custom (base All 0, 2 skills set)"
    # GUNS lacks Weapon Upgrades, Hull Upgrades, ...: those modules are never offered
    assert result["coverage"]["excluded"]["character lacks skills"] > 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_characters.py -q -k "search or pool or marginal"`
Expected: FAIL (the sheet is not resolved; nothing is excluded for lacking skills)

- [ ] **Step 3: Add `usable` and `fingerprint`**

Append to `pyfa_mcp/characters.py`:

```python
def _plain(char) -> bool:
    return char is None or char.savedName == "All 5"


def usable(char):
    """Whether `char` has an item's own required skills; None when every item is usable."""
    if _plain(char):
        return None
    return lambda item: all(char.getSkill(req).level >= level
                            for req, level in item.requiredSkills.items())


def fingerprint(char) -> tuple | None:
    """The character's levels, for cache keys; None for All 5."""
    return None if _plain(char) else tuple(sorted((s.itemID, s.level) for s in char.skills))
```

- [ ] **Step 4: Resolve the character once in search**

In `pyfa_mcp/search.py`, import `characters` in the `from pyfa_mcp import (...)` list. At the end of `_portable`, before `return raw`:

```python
    if "character" in raw:
        conditions.parse({"character": raw["character"]})  # shape errors first
        try:
            # A sheet: workers apply it without reading the user's Pyfa, and an
            # edited character changes the trial cache key.
            raw["character"] = characters.sheet(raw["character"])
        except characters.CharacterError as exc:
            raise conditions.ConditionsError(f"character: {exc}") from exc
```

In `_pool`, add the character to the cache key:

```python
    key = (fit.ship.item.ID, subs, json.dumps(meta), tuple(sorted(sources)), availability,
           characters.fingerprint(fit.character))
```

- [ ] **Step 5: Filter the pool**

In `pyfa_mcp/candidates.py`, add `characters` to the imports (`from pyfa_mcp import catalog, characters`). In `build`, after `ok = item_filter(meta, availability)`:

```python
    usable = characters.usable(fit.character) if availability != "all" else None
    charge_ok = ok if usable is None else (lambda c: ok(c) and usable(c))
    if usable is not None:
        availability_note += ("; items the character lacks skills for are left out too "
                              "(availability=\"all\" includes them)")
```

In the item loop, right after the `if not open_(item):` block:

```python
        if usable is not None and not usable(item):
            excluded.append(_excluded(item, "character lacks skills"))
            continue
```

and change the charges line to use `charge_ok`:

```python
            found += [_local(item, "charge", slot, c) for c in _charges(item, charge_ok)]
```

Bursts and phenomena keep `ok`: booster and titan fits fly at All 5.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_characters.py tests/test_candidates.py tests/test_search.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyfa_mcp/characters.py pyfa_mcp/search.py pyfa_mcp/candidates.py tests/test_characters.py
git commit -m "Characters: fit search measures with the character and leaves out what it cannot use"
```

---

### Task 6: MCP tools, instructions, version

**Files:**
- Modify: `pyfa_mcp/server.py` (imports, `INSTRUCTIONS`, `_USER_ERRORS`, six tools)
- Modify: `pyproject.toml` (`version = "0.6.0"`), `uv.lock` (the `pyfa-mcp` package version line)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: Task 3/4 functions in `characters`
- Produces: MCP tools `list_characters`, `get_character`, `copy_character`, `set_skills`, `delete_character`, `export_character_to_pyfa`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
def test_character_tools(booted, zealot_eft, no_fits_left):
    from pyfa_mcp import server
    assert [r["name"] for r in server.list_characters()][:2] == ["All 0", "All 5"]
    server.copy_character("All 0", "Tool Alt")
    try:
        changed = server.set_skills("Tool Alt", {"Gunnery": 2})["changed"]
        assert changed[0]["skill"] == "Gunnery"
        assert server.get_character("Tool Alt")["skills"] == {"Gunnery": {"Gunnery": 2}}
        result = server.evaluate_fit(zealot_eft, {"character": "Tool Alt"})
        assert result["applied"]["character"] == "Tool Alt"
    finally:
        server.delete_character("Tool Alt")
    with pytest.raises(ToolError, match="copy_character"):
        server.set_skills("All 5", {"Gunnery": 1})


def test_instructions_cover_characters():
    from pyfa_mcp import server
    text = server.INSTRUCTIONS
    assert "list_characters" in text and "copy_character" in text and "sheet" in text
```

If `tests/test_server.py` does not already import `pytest` and `ToolError` at the top, add `import pytest` and `from mcp.server.mcpserver.exceptions import ToolError`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_server.py -q`
Expected: FAIL with `AttributeError: module 'pyfa_mcp.server' has no attribute 'list_characters'`

- [ ] **Step 3: Add the tools**

In `pyfa_mcp/server.py`, add `characters` to the `from pyfa_mcp import (...)` list and `characters.CharacterError` to `_USER_ERRORS`.

In `INSTRUCTIONS`, after the `evaluate_fit / compare_fits / fit_graph take conditions` bullet, add:

```
- Skills: conditions.character defaults to All 5. For "my / my alt's
  skills" pass the character: "pyfa:<name>" (list_characters(source="pyfa"))
  or one stored here. For "what if I train X" pass a sheet {"base": ...,
  "skills": {...}}; compare_fits variants compare several sheets in one
  call. Say which character the numbers assume. A "needs ... (has ...)"
  warning means that pilot cannot use the item. To keep skill changes,
  copy_character, then set_skills; export_character_to_pyfa only when the
  user explicitly asks.
```

Add a section before `# --- storage ---`:

```python
# --- characters --------------------------------------------------------------

@app.tool()
@_tool
def list_characters(source: str = "server") -> list:
    """Characters for conditions.character. source="server": All 0, All 5 and the
    characters stored here (editable); source="pyfa": the user's own Pyfa characters
    ("pyfa:<id>", read-only). Rows carry skills at V, skill points and alpha clone."""
    return characters.list_characters(source)


@app.tool()
@_tool
def get_character(character: str, group: str | None = None) -> dict:
    """A character's trained skills by skill group; with `group` (e.g. "Gunnery"),
    every skill in that group, untrained ones at 0."""
    return characters.get_character(character, group)


@app.tool()
@_tool
def copy_character(character: str, name: str) -> dict:
    """Store a copy of a character under a new name, to edit with set_skills: import
    one from the user's Pyfa ("pyfa:Main"), start from "All 0" or "All 5", or copy a
    stored one. Copies skills and alpha clone, not implants."""
    return characters.copy_character(character, name)


@app.tool()
@_tool
def set_skills(character: str, skills: dict) -> dict:
    """Set skill levels (0-5) on a stored character, e.g. {"Surgical Strike": 4}.
    Raising a skill trains its missing prerequisites; lowering one untrains the
    skills that need it, as in EVE. Returns every level that changed and why. For a
    one-off "what if I train X", pass a sheet in conditions.character instead."""
    return characters.set_skills(character, skills)


@app.tool()
@_tool
def delete_character(character: str) -> dict:
    """Delete a character stored here, by name or id. Built-in and Pyfa characters
    are read-only."""
    return characters.delete_character(character)


@app.tool()
@_tool
def export_character_to_pyfa(character: str, name: str | None = None) -> dict:
    """Write a character into the user's own Pyfa as a NEW character. Only when the
    user explicitly asks. Refuses while Pyfa runs; backs up Pyfa's database first;
    never overwrites (a taken name gets " (2)"). Returns its pyfa: id and the backup."""
    return characters.export_character_to_pyfa(character, name)
```

In `evaluate_fit`'s docstring, change "under conditions:" to "under conditions (skills: conditions.character):".

- [ ] **Step 4: Bump the version**

In `pyproject.toml`, `version = "0.5.0"` → `version = "0.6.0"`. In `uv.lock`, change the `version = "0.5.0"` line that directly follows `name = "pyfa-mcp"` to `version = "0.6.0"` (the same two-line change as commit `c34800b`).

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, no failures. Any failure in a file this plan didn't touch means a shared behaviour changed (most likely the `applied["character"]` echo or `_free_name`'s new signature): fix the cause, don't loosen the test.

- [ ] **Step 6: Commit**

```bash
git add pyfa_mcp/server.py tests/test_server.py pyproject.toml uv.lock
git commit -m "Version 0.6.0: characters and skill editing"
```
