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
