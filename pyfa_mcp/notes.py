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
    propmods = [m for m in fit.modules
                if not m.isEmpty and m.item.group.name == "Propulsion Module"
                and m.getModifiedItemAttr("speedFactor") and m.getModifiedItemAttr("speedBoostFactor")]
    # Each propmod is judged as the one running: the hull without any active
    # propmod's mass addition, plus its own.
    hull = fit.ship.getModifiedItemAttr("mass") - sum(
        m.getModifiedItemAttr("massAddition") or 0 for m in propmods
        if m.state >= FittingModuleState.ACTIVE)
    for mod in propmods:
        rated = mod.getModifiedItemAttr("speedFactor")
        thrust = mod.getModifiedItemAttr("speedBoostFactor")
        mass = hull + (mod.getModifiedItemAttr("massAddition") or 0)
        note = propmod_note(mod.item.name, rated, thrust, mass)
        if note:
            out.append(note)
    return list(dict.fromkeys(out))
