"""Everything about an evaluation that EFT text cannot say.

`parse` validates shape and values without touching eos; `apply` resolves
names against the fit and the game data and changes a *temporary* fit
through Pyfa's own GUI calc commands, so the result is what the Pyfa GUI
would show for the same clicks. Every value `apply` used comes back in the
`applied` echo, defaults marked.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Callable

from pyfa_mcp.eft import suggest
from pyfa_mcp.eosboot import TEMP_NOTE

_DAMAGE_KEYS = ("em", "thermal", "kinetic", "explosive")
_STATES = ("offline", "online", "active", "overheated")
_FIELDS = {
    "character": "Pilot skills. Only \"All 5\" in this version.",
    "damage_profile": "Incoming damage for EHP: \"uniform\", a Pyfa built-in "
                      "profile name, or {em, thermal, kinetic, explosive} weights.",
    "target": "Target for applied damage: a Pyfa built-in target profile name, or "
              "{resists: {em, thermal, kinetic, explosive} as 0..1, signature, speed, radius}.",
    "module_states": "[{module, state: offline|online|active|overheated, count?}] "
                     "for modules on the fit; count defaults to all of that name.",
    "spool": "Triglavian/mutadaptive spool: \"min\", \"max\" or 0..1. "
             "Default: Pyfa's default (full).",
    "drug_side_effects": "[{drug, effect}] side effects to switch on; drug must be "
                         "in the fit's EFT, effect is a case-insensitive part of "
                         "the side effect's name.",
    "command": "[{fit}] fits whose command bursts apply to this fit (EFT or "
               "stored fit name/id).",
    "projected": "[{item, count?, state?}] projected modules or drones, or "
                 "[{fit, count?}] projected fits.",
    "mode": "Tactical destroyer mode, e.g. \"sharpshooter\" (EFT cannot carry it). "
            "Default: Pyfa's first mode for the hull.",
}
_EXAMPLES = [
    {"module_states": [{"module": "Medium Armor Repairer II", "state": "overheated"}],
     "projected": [{"item": "Stasis Webifier II", "count": 2}]},
    {"damage_profile": {"em": 0, "thermal": 0, "kinetic": 1, "explosive": 1},
     "command": [{"fit": "[Damnation, boosts]\n\n\nArmor Command Burst II, "
                         "Armor Energizing Charge\n"}]},
    {"spool": "min", "target": {"resists": {"em": 0.5, "thermal": 0.5,
                                            "kinetic": 0.5, "explosive": 0.5},
                                "signature": 125, "speed": 300}},
]


class ConditionsError(ValueError):
    """Conditions that are malformed or do not match the fit."""


@dataclass(frozen=True)
class Conditions:
    character: str = "All 5"
    damage_profile: str | dict = "uniform"
    target: str | dict | None = None
    module_states: tuple = ()
    spool: float | None = None  # None: Pyfa's default
    drug_side_effects: tuple = ()
    command: tuple = ()
    projected: tuple = ()
    mode: str | None = None
    explicit: frozenset = field(default_factory=frozenset)


def _default_spool() -> float:
    import eos.config
    return float(eos.config.settings["globalDefaultSpoolupPercentage"])


def spool_of(cond: Conditions) -> float:
    return cond.spool if cond.spool is not None else _default_spool()


def _weights(value, name: str, lo: float, hi: float) -> dict:
    if not isinstance(value, dict) or set(value) != set(_DAMAGE_KEYS):
        raise ConditionsError(f"{name} needs exactly the keys {', '.join(_DAMAGE_KEYS)}")
    for key, v in value.items():
        if not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ConditionsError(f"{name}.{key} must be a number in [{lo}, {hi}]")
    return dict(value)


def _list_of_dicts(raw, key: str) -> tuple:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise ConditionsError(f"{key} must be a list of objects")
    return tuple(value)


def _count(entry: dict, key: str) -> int:
    count = entry.get("count", 1)
    if not isinstance(count, int) or count < 1:
        raise ConditionsError(f"{key}: count must be a positive integer")
    return count


def parse(raw: dict | None) -> Conditions:
    raw = dict(raw or {})
    unknown = set(raw) - set(_FIELDS)
    if unknown:
        name = sorted(unknown)[0]
        raise ConditionsError(
            f"unknown condition '{name}'; known: {', '.join(_FIELDS)}")

    if raw.get("character", "All 5") != "All 5":
        raise ConditionsError("character: only \"All 5\" is supported in this version")

    spool = raw.get("spool")
    if spool == "min":
        spool = 0.0
    elif spool == "max":
        spool = 1.0
    elif spool is not None and (isinstance(spool, bool) or not isinstance(spool, (int, float))
                                or not 0 <= spool <= 1):
        raise ConditionsError("spool must be \"min\", \"max\" or a number in [0, 1]")

    damage = raw.get("damage_profile", "uniform")
    if isinstance(damage, dict):
        damage = _weights(damage, "damage_profile", 0, float("inf"))
        if sum(damage.values()) <= 0:
            raise ConditionsError("damage_profile weights must not all be zero")
    elif not isinstance(damage, str):
        raise ConditionsError("damage_profile must be a name or an object")

    target = raw.get("target")
    if isinstance(target, dict):
        extra = set(target) - {"resists", "signature", "speed", "radius"}
        if extra:
            raise ConditionsError(f"target: unknown key '{sorted(extra)[0]}'")
        if "resists" in target:
            _weights(target["resists"], "target.resists", 0, 1)
    elif target is not None and not isinstance(target, str):
        raise ConditionsError("target must be a name or an object")

    states = _list_of_dicts(raw, "module_states")
    for entry in states:
        if entry.get("state") not in _STATES:
            raise ConditionsError(f"module_states: state must be one of {', '.join(_STATES)}")
        if not isinstance(entry.get("module"), str):
            raise ConditionsError("module_states: each entry needs a module name")
        if "count" in entry:
            _count(entry, "module_states")

    drugs = _list_of_dicts(raw, "drug_side_effects")
    for entry in drugs:
        if not isinstance(entry.get("drug"), str) or not isinstance(entry.get("effect"), str):
            raise ConditionsError("drug_side_effects: each entry needs drug and effect")

    command = _list_of_dicts(raw, "command")
    for entry in command:
        if not isinstance(entry.get("fit"), str):
            raise ConditionsError("command: each entry needs a fit")

    projected = _list_of_dicts(raw, "projected")
    for entry in projected:
        if ("item" in entry) == ("fit" in entry):
            raise ConditionsError("projected: each entry needs exactly one of item or fit")
        _count(entry, "projected")
        if entry.get("state", "active") not in _STATES:
            raise ConditionsError(f"projected: state must be one of {', '.join(_STATES)}")

    mode = raw.get("mode")
    if mode is not None and not isinstance(mode, str):
        raise ConditionsError("mode must be a mode name such as \"sharpshooter\"")

    return Conditions(
        mode=mode, damage_profile=damage, target=target, module_states=states,
        spool=None if spool is None else float(spool),
        drug_side_effects=drugs, command=command, projected=projected,
        explicit=frozenset(raw))


# --- resolution against eos -------------------------------------------------

def _state(name: str):
    from eos.const import FittingModuleState
    return FittingModuleState[name.upper()]


def _item(name: str):
    from service.market import Market
    try:
        item = Market.getInstance().getItem(name)
    except Exception:
        item = None
    if item is None or not item.published:
        close = suggest(name)
        hint = f" (did you mean: {', '.join(close)}?)" if close else ""
        raise ConditionsError(f"unknown item '{name}'{hint}")
    return item


def _builtin_damage_profiles() -> dict:
    from eos.saveddata.damagePattern import DamagePattern
    return {p.fullName: p for p in DamagePattern.getBuiltinList()}


def _builtin_target_profiles() -> dict:
    from eos.saveddata.targetProfile import TargetProfile
    return {p.fullName: p for p in TargetProfile.getBuiltinList()}


def _by_name(profiles: dict, name: str, kind: str):
    for full, profile in profiles.items():
        if full.casefold() == name.casefold():
            return profile
    close = difflib.get_close_matches(name, list(profiles), n=3, cutoff=0.5)
    hint = f" (did you mean: {', '.join(close)}?)" if close else ""
    raise ConditionsError(f"unknown {kind} '{name}'{hint}; see conditions_format()")


def damage_pattern(cond: Conditions):
    from eos.saveddata.damagePattern import DamagePattern
    value = cond.damage_profile
    if value == "uniform":
        return DamagePattern.getDefaultBuiltin()
    if isinstance(value, str):
        return _by_name(_builtin_damage_profiles(), value, "damage profile")
    pattern = DamagePattern(value["em"], value["thermal"], value["kinetic"], value["explosive"])
    # A user pattern: it is saved with the temporary fit and purged with it.
    pattern.rawName = TEMP_NOTE
    return pattern


def target_profile(cond: Conditions):
    from eos.saveddata.targetProfile import TargetProfile
    value = cond.target
    if value is None:
        return None
    if isinstance(value, str):
        return _by_name(_builtin_target_profiles(), value, "target profile")
    resists = value.get("resists", dict.fromkeys(_DAMAGE_KEYS, 0))
    profile = TargetProfile(
        resists["em"], resists["thermal"], resists["kinetic"], resists["explosive"],
        maxVelocity=value.get("speed"), signatureRadius=value.get("signature"),
        radius=value.get("radius"))
    profile.rawName = TEMP_NOTE
    return profile


def _apply_module_states(fit, states) -> list[str]:
    echo = []
    claimed: set[int] = set()  # each module takes the state of one entry only
    for entry in states:
        name, state = entry["module"], _state(entry["state"])
        mods = [m for m in fit.modules
                if not m.isEmpty and m.item.name.casefold() == name.casefold()]
        if not mods:
            fitted = sorted({m.item.name for m in fit.modules if not m.isEmpty})
            raise ConditionsError(
                f"module_states: '{name}' is not fitted; fitted: {', '.join(fitted)}")
        free = [m for m in mods if id(m) not in claimed]
        count = entry.get("count", len(free))
        if count > len(mods):
            raise ConditionsError(
                f"module_states: count {count} for '{name}' but only {len(mods)} fitted")
        if not free or count > len(free):
            raise ConditionsError(
                f"module_states: {len(mods) - len(free)} of the {len(mods)} '{name}' "
                "are already set by an earlier entry")
        for mod in free[:count]:
            claimed.add(id(mod))
            if not mod.isValidState(state):
                raise ConditionsError(
                    f"module_states: {mod.item.name} cannot be {entry['state']}")
            mod.state = state
        echo.append(f"{mods[0].item.name} x{count}: {entry['state']}")
    return echo


def _launch_drones(fit) -> list[str]:
    """Launch the EFT's drones, as many as bandwidth and skills allow.

    Pyfa imports drones docked (the GUI user clicks them out); a fit is
    asked about with its drones out, so launch them in EFT order.
    """
    bandwidth = fit.ship.getModifiedItemAttr("droneBandwidth") or 0
    limit = fit.extraAttributes["maxActiveDrones"]
    used, active, echo = 0.0, 0, []
    for drone in fit.drones:
        each = drone.getModifiedItemAttr("droneBandwidthUsed") or 0
        count = drone.amount
        if each:
            count = min(count, int((bandwidth - used + 1e-6) // each))
        count = max(0, min(count, limit - active))
        drone.amountActive = count
        used += count * each
        active += count
        echo.append(f"{drone.item.name}: {count} of {drone.amount} launched (default)")
    return echo


def _apply_drugs(fit, drugs) -> list[str]:
    echo = []
    for entry in drugs:
        booster = next((b for b in fit.boosters
                        if b.item.name.casefold() == entry["drug"].casefold()), None)
        if booster is None:
            raise ConditionsError(
                f"drug_side_effects: '{entry['drug']}' is not in the fit; add it to the EFT")
        needle = entry["effect"].casefold()
        matches = [se for se in booster.sideEffects if needle in se.name.casefold()]
        names = [se.name for se in booster.sideEffects]
        if len(matches) != 1:
            what = "no" if not matches else "several"
            raise ConditionsError(
                f"drug_side_effects: {what} side effects of {booster.item.name} match "
                f"'{entry['effect']}'; available: {'; '.join(names)}")
        matches[0].active = True
        echo.append(f"{booster.item.name}: {matches[0].name}")
    return echo


def _apply_projected(fit, projected, add_fit) -> list[str]:
    from gui.fitCommands.calc.drone.projectedAdd import CalcAddProjectedDroneCommand
    from gui.fitCommands.calc.module.projectedAdd import CalcAddProjectedModuleCommand
    from gui.fitCommands.calc.projectedFit.add import CalcAddProjectedFitCommand
    from gui.fitCommands.helpers import DroneInfo, ModuleInfo

    echo = []
    for entry in projected:
        count = entry.get("count", 1)
        state = _state(entry.get("state", "active"))
        if "fit" in entry:
            other = add_fit(entry["fit"])
            CalcAddProjectedFitCommand(fit.ID, other.ID, count, state).Do()
            echo.append(f"fit '{other.name}' ({other.ship.item.name}) x{count}")
            continue
        item = _item(entry["item"])
        if item.isDrone:
            launched = count if state.value >= _state("active").value else 0
            ok = CalcAddProjectedDroneCommand(
                fit.ID, DroneInfo(amount=count, amountActive=launched, itemID=item.ID)).Do()
            if not ok:
                raise ConditionsError(f"projected: {item.name} cannot be projected "
                                      "(Pyfa only projects e-war and logistics drones)")
        elif item.category.name == "Module":
            for _ in range(count):
                ok = CalcAddProjectedModuleCommand(
                    fit.ID, ModuleInfo(itemID=item.ID, state=state)).Do()
                if not ok:
                    raise ConditionsError(f"projected: Pyfa refused to project {item.name}")
        else:
            raise ConditionsError(
                f"projected: {item.name} is a {item.category.name}; project modules, "
                "drones, or a whole fit")
        echo.append(f"{item.name} x{count} ({entry.get('state', 'active')})")
    return echo


def _apply_command(fit, command, add_fit) -> list[str]:
    from eos.const import FittingModuleState
    from gui.fitCommands.calc.commandFit.add import CalcAddCommandCommand

    echo = []
    for entry in command:
        other = add_fit(entry["fit"])
        CalcAddCommandCommand(fit.ID, other.ID, FittingModuleState.ACTIVE).Do()
        echo.append(f"fit '{other.name}' ({other.ship.item.name})")
    return echo


def _apply_mode(fit, mode: str | None) -> str | None:
    from gui.fitCommands.calc.shipModeChange import CalcChangeShipModeCommand

    modes = fit.ship.modes
    if mode is None:
        return fit.mode.item.name if fit.mode is not None else None
    if not modes:
        raise ConditionsError(f"mode: {fit.ship.item.name} has no modes")
    names = [m.item.name for m in modes]
    matches = [m for m in modes if mode.casefold() in m.item.name.casefold()]
    if len(matches) != 1:
        raise ConditionsError(f"mode: '{mode}' must match one of {', '.join(names)}")
    CalcChangeShipModeCommand(fit.ID, matches[0].item.ID).Do()
    return matches[0].item.name


def _mark(value: str, key: str, cond: Conditions) -> str:
    return value if key in cond.explicit else f"{value} (default)"


def apply(fit, cond: Conditions, add_fit: Callable) -> dict:
    from service.fit import Fit as FitService

    fit.damagePattern = damage_pattern(cond)
    fit.targetProfile = target_profile(cond)

    mode = _apply_mode(fit, cond.mode)
    drones = _launch_drones(fit)
    states = _apply_module_states(fit, cond.module_states)
    drugs = _apply_drugs(fit, cond.drug_side_effects)
    command = _apply_command(fit, cond.command, add_fit)
    projected = _apply_projected(fit, cond.projected, add_fit)
    FitService.getInstance().recalc(fit)

    damage_label = (cond.damage_profile if isinstance(cond.damage_profile, str)
                    else "custom " + ", ".join(f"{k} {v:g}"
                                               for k, v in cond.damage_profile.items()))
    target_label = ("none" if cond.target is None else
                    cond.target if isinstance(cond.target, str) else "custom target")
    return {
        "character": _mark("All 5", "character", cond),
        "damage_profile": _mark(damage_label, "damage_profile", cond),
        "target": _mark(target_label, "target", cond),
        "module_states": states or _mark("as in the EFT", "module_states", cond),
        "spool": _mark(f"{spool_of(cond):g}", "spool", cond),
        "drug_side_effects": drugs or _mark("none", "drug_side_effects", cond),
        "command": command or _mark("none", "command", cond),
        "projected": projected or _mark("none", "projected", cond),
        "drones": drones or "none in the EFT",
        **({"mode": _mark(mode, "mode", cond)} if mode else {}),
    }


def describe() -> dict:
    return {
        "fields": _FIELDS,
        "defaults": {"character": "All 5", "damage_profile": "uniform",
                     "target": None, "spool": "Pyfa default (full)",
                     "module_states": "as in the EFT (modules active, /OFFLINE honoured)"},
        "damage_profiles": ["uniform", *_builtin_damage_profiles()],
        "target_profiles": list(_builtin_target_profiles()),
        "examples": _EXAMPLES,
        "in_eft_instead": "implants, drugs (boosters), charges, drones/fighters with "
                          "counts, /OFFLINE modules and mutated modules go in the EFT text",
    }
