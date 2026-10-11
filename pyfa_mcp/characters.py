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
    taken = next((c for c in _stored() if c.savedName.casefold() == name.casefold()), None)
    if taken is not None:
        raise CharacterError(f"a stored character is already named '{taken.savedName}'; "
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


def export_character_to_pyfa(ref: str, name: str | None = None) -> dict:
    s = _named(ref)
    by_id = {skill(n).ID: lvl for n, lvl in levels(s).items() if lvl}
    alpha = _alpha(s["alpha_clone"]).ID if s["alpha_clone"] is not None else None
    wanted = (name or "").strip() or s["label"].removeprefix(PYFA_PREFIX)
    return pyfadata.export_character(by_id, alpha, wanted)
