"""A calculated eos fit as plain data, under one spool setting."""
from __future__ import annotations

_LAYERS = {"shield": "shield", "armor": "armor", "hull": ""}
_TYPES = ("em", "thermal", "kinetic", "explosive")

DEFAULT_COMPARE = (
    "validity.valid",
    "tank.ehp.total",
    "tank.repair_ehp_per_s.total",
    "offense.dps.total",
    "offense.volley.total",
    "capacitor.stable",
    "navigation.max_speed",
    "navigation.align_time_s",
    "navigation.signature_m",
    "targeting.lock_range_m",
    "targeting.scan_resolution_mm",
)


def _resonance_attr(layer: str, damage_type: str) -> str:
    prefix = _LAYERS[layer]
    if not prefix:
        return f"{damage_type}DamageResonance"
    return f"{prefix}{damage_type.capitalize()}DamageResonance"


def _spool_options(spool: float):
    from eos.const import SpoolType
    from eos.utils.spoolSupport import SpoolOptions

    return SpoolOptions(SpoolType.SPOOL_SCALE, spool, True)


def _validity(fit) -> dict:
    from eos.const import FittingHardpoint, FittingSlot

    ship = fit.ship
    problems: list[str] = []

    def resource(label, used, total):
        if used > total + 1e-6:
            problems.append(f"{label} over by {used - total:g}")
        return {"used": used, "total": total}

    cpu = resource("CPU", fit.cpuUsed, ship.getModifiedItemAttr("cpuOutput"))
    pg = resource("powergrid", fit.pgUsed, ship.getModifiedItemAttr("powerOutput"))
    cal = resource("calibration", fit.calibrationUsed,
                   ship.getModifiedItemAttr("upgradeCapacity"))

    slots = {}
    for label, slot in (("high", FittingSlot.HIGH), ("mid", FittingSlot.MED),
                        ("low", FittingSlot.LOW), ("rig", FittingSlot.RIG),
                        ("subsystem", FittingSlot.SUBSYSTEM)):
        # Modules store their slot as the enum's int value and getSlotsUsed
        # compares with `is`, so it must be asked with the int too.
        used, total = fit.getSlotsUsed(slot.value), int(fit.getNumSlots(slot))
        if used > total:
            problems.append(f"{used - total} too many {label} slots")
        slots[label] = {"used": used, "total": total}

    hardpoints = {}
    for label, kind, attr in (("turret", FittingHardpoint.TURRET, "turretSlotsLeft"),
                              ("launcher", FittingHardpoint.MISSILE, "launcherSlotsLeft")):
        used = fit.getHardpointsUsed(kind)
        total = int(ship.getModifiedItemAttr(attr) or 0)
        if used > total:
            problems.append(f"{used - total} too many {label}s")
        hardpoints[label] = {"used": used, "total": total}

    for mod in fit.modules:
        if not mod.isEmpty and not mod.fits(fit):
            problems.append(f"{mod.item.name} cannot be fitted to this ship")
    bay = ship.getModifiedItemAttr("droneCapacity") or 0
    resource("drone bay", fit.droneBayUsed, bay)
    bandwidth = ship.getModifiedItemAttr("droneBandwidth") or 0
    resource("drone bandwidth", fit.droneBandwidthUsed, bandwidth)
    launched = sum(d.amountActive for d in fit.drones)
    if launched > fit.extraAttributes["maxActiveDrones"]:
        problems.append(f"{launched} drones launched, skills allow "
                        f"{fit.extraAttributes['maxActiveDrones']}")
    for dropped in getattr(fit, "dropped_modules", ()):
        problems.append(f"{dropped.name} was left out: {dropped.reason}")

    return {"valid": not problems, "problems": problems, "cpu": cpu,
            "powergrid": pg, "calibration": cal, "slots": slots,
            "hardpoints": hardpoints}


def _tank(fit) -> dict:
    ship = fit.ship
    ehp = {layer: fit.ehp[layer] for layer in _LAYERS}
    ehp["total"] = sum(ehp.values())
    hp = {layer: fit.hp[layer] for layer in _LAYERS}
    resists = {
        layer: {t: 1 - ship.getModifiedItemAttr(_resonance_attr(layer, t))
                for t in _TYPES}
        for layer in _LAYERS
    }
    reps = fit.effectiveTank
    repair = {
        "passive_shield": reps["passiveShield"],
        "shield": reps["shieldRepair"],
        "armor": reps["armorRepair"],
        "hull": reps["hullRepair"],
    }
    repair["total"] = sum(repair.values())
    return {"hp": hp, "ehp": ehp, "resists": resists, "repair_ehp_per_s": repair}


def _dmg(dmg) -> dict:
    return {"total": dmg.total, "em": dmg.em, "thermal": dmg.thermal,
            "kinetic": dmg.kinetic, "explosive": dmg.explosive}


def _offense(fit, spool: float) -> dict:
    options = _spool_options(spool)
    return {
        "dps": _dmg(fit.getTotalDps(spoolOptions=options)),
        "volley": _dmg(fit.getTotalVolley(spoolOptions=options)),
        "weapon_dps": fit.getWeaponDps(spoolOptions=options).total,
        "drone_dps": fit.getDroneDps().total,
    }


def _capacitor(fit) -> dict:
    stable = bool(fit.capStable)
    return {
        "capacity": fit.ship.getModifiedItemAttr("capacitorCapacity"),
        "recharge_s": fit.ship.getModifiedItemAttr("rechargeRate") / 1000,
        "stable": stable,
        "stable_at_percent": fit.capState if stable else None,
        "lasts_s": None if stable else fit.capState,
        "delta_per_s": fit.capDelta,
    }


def _navigation(fit) -> dict:
    ship = fit.ship
    return {
        "max_speed": fit.maxSpeed,
        "align_time_s": fit.alignTime,
        "signature_m": ship.getModifiedItemAttr("signatureRadius"),
        "mass_kg": ship.getModifiedItemAttr("mass"),
        "warp_speed_au_s": fit.warpSpeed,
    }


def _targeting(fit) -> dict:
    return {
        "lock_range_m": fit.maxTargetRange,
        "scan_resolution_mm": fit.ship.getModifiedItemAttr("scanResolution"),
        "max_targets": fit.maxTargets,
        "sensor_strength": fit.scanStrength,
    }


def _drones(fit) -> dict:
    ship = fit.ship
    return {
        "bandwidth": {"used": fit.droneBandwidthUsed,
                      "total": ship.getModifiedItemAttr("droneBandwidth") or 0},
        "bay": {"used": fit.droneBayUsed,
                "total": ship.getModifiedItemAttr("droneCapacity") or 0},
        "active": fit.activeDrones,
    }


def fit_stats(fit, spool: float) -> dict:
    return {
        "validity": _validity(fit),
        "tank": _tank(fit),
        "offense": _offense(fit, spool),
        "capacitor": _capacitor(fit),
        "navigation": _navigation(fit),
        "targeting": _targeting(fit),
        "drones": _drones(fit),
    }


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in d.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, path + "."))
        else:
            out[path] = value
    return out
