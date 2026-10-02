"""Pyfa's graphs, computed headless and returned as points.

The GUI's graph control panel decides which inputs a graph gets; this
module reproduces it (graphs/gui/ctrlPanel.py): attacker and target
vectors first, at the panel's reset values; then every input and checkbox
whose conditions match the chosen x/y axes, at its default -- `None` for an
empty box, exactly as the panel passes it.
"""
from __future__ import annotations

from collections import namedtuple

from pyfa_mcp import conditions
from pyfa_mcp.evaluate import Scratch

# The GUI's InputData (graphs/gui/ctrlPanel.py), which we cannot import:
# that module is the wx control panel itself.
InputData = namedtuple("InputData", ("handle", "unit", "value"))

GRAPHS = {
    "damage": "dmgStatsGraph",
    "lock_time": "lockTimeGraph",
    "mobility": "mobilityGraph",
    "warp_time": "warpTimeGraph",
    "capacitor": "capacitorGraph",
    "shield_regen": "shieldRegenGraph",
    "ewar": "ewarStatsGraph",
    "remote_reps": "remoteRepsGraph",
}

# The control panel's reset: attacker still, target at full speed, both at 90 degrees.
_SRC_VECTOR = (0, 90)
_TGT_VECTOR = (100, 90)


class GraphError(ValueError):
    pass


def _view(name: str):
    import graphs.data  # noqa: F401 -- Pyfa's package; registers every graph
    from graphs.data.base import FitGraph

    if name not in GRAPHS:
        raise GraphError(f"unknown graph '{name}'; graphs: {', '.join(GRAPHS)}")
    view = FitGraph.viewMap[GRAPHS[name]]()
    if hasattr(view, "isEffective"):
        # Read from the GUI's resistances view, which defaults to effective HP.
        view.isEffective = True
    return view


def _vectors(view):
    for vector, (length, angle) in ((view.srcVectorDef, _SRC_VECTOR),
                                    (view.tgtVectorDef, _TGT_VECTOR)):
        if vector is not None:
            yield vector, length, angle


def describe() -> dict:
    out = {}
    for name in GRAPHS:
        view = _view(name)
        inputs = []
        for vector, length, angle in _vectors(view):
            inputs.append({"handle": vector.lengthHandle, "unit": vector.lengthUnit,
                           "default": length, "label": f"{vector.label} speed"})
            inputs.append({"handle": vector.angleHandle, "unit": vector.angleUnit,
                           "default": angle, "label": f"{vector.label} angle"})
        inputs += [{"handle": i.handle, "unit": i.unit, "default": i.defaultValue,
                    "label": i.label} for i in view.inputs]
        inputs += [{"handle": c.handle, "unit": None, "default": c.defaultValue,
                    "label": c.label} for c in view.checkboxes]
        out[name] = {
            "x": [{"handle": d.handle, "unit": d.unit, "label": d.label} for d in view.xDefs],
            "y": [{"handle": d.handle, "unit": d.unit, "label": d.label} for d in view.yDefs],
            "inputs": inputs,
            "has_target": view.hasTargets,
        }
    return out


def _pick(defs, spec: str, axis: str):
    handle, _, unit = spec.partition(":")
    for d in defs:
        if d.handle == handle and (not unit or d.unit == unit):
            return d
    options = ", ".join(f"{d.handle}:{d.unit}" if d.unit else d.handle for d in defs)
    raise GraphError(f"unknown {axis} '{spec}'; options: {options}")


def _shown(definition, x_def, y_def) -> bool:
    if not definition.conditions:
        return True
    for x_cond, y_cond in definition.conditions:
        if ((x_cond is None or tuple(x_cond) == (x_def.handle, x_def.unit))
                and (y_cond is None or tuple(y_cond) == (y_def.handle, y_def.unit))):
            return True
    return False


def _misc_inputs(view, x_def, y_def, given: dict) -> list:
    main_handle = x_def.mainInput[0]
    known = {main_handle}
    misc = []
    for vector, length, angle in _vectors(view):
        known |= {vector.lengthHandle, vector.angleHandle}
        if vector.lengthHandle != main_handle:  # the panel shows direction only
            misc.append(InputData(vector.lengthHandle, vector.lengthUnit,
                                  given.pop(vector.lengthHandle, length)))
        misc.append(InputData(vector.angleHandle, vector.angleUnit,
                              given.pop(vector.angleHandle, angle)))
    for spec in view.inputs:
        if spec.handle in known:
            continue
        known.add(spec.handle)
        if _shown(spec, x_def, y_def):
            misc.append(InputData(spec.handle, spec.unit,
                                  given.pop(spec.handle, spec.defaultValue)))
    for box in view.checkboxes:
        if box.handle in known:
            continue
        known.add(box.handle)
        if _shown(box, x_def, y_def):
            misc.append(InputData(box.handle, None, given.pop(box.handle, box.defaultValue)))
    if given:
        raise GraphError(f"unknown input '{sorted(given)[0]}' for this x/y; inputs: "
                         + ", ".join(i.handle for i in misc))
    return misc


def _downsample(xs, ys, max_points: int) -> list[list[float]]:
    if len(xs) <= max_points:
        return [[x, y] for x, y in zip(xs, ys)]
    step = (len(xs) - 1) / (max_points - 1)
    picks = [round(i * step) for i in range(max_points)]
    return [[xs[i], ys[i]] for i in picks]


def fit_graph(ref: str, graph: str, x: str, y: str, x_range: list[float],
              inputs: dict | None, raw_conditions: dict | None,
              max_points: int = 50) -> dict:
    from graphs.wrapper import SourceWrapper, TargetWrapper

    view = _view(graph)
    if (not isinstance(x_range, (list, tuple)) or len(x_range) != 2
            or not all(isinstance(v, (int, float)) for v in x_range)):
        raise GraphError("x_range must be [low, high]")
    x_def = _pick(view.xDefs, x, "x")
    y_def = _pick(view.yDefs, y, "y")
    cond = conditions.parse(raw_conditions)
    main = InputData(*x_def.mainInput, tuple(x_range))
    misc = _misc_inputs(view, x_def, y_def, dict(inputs or {}))

    with Scratch() as scratch:
        fit = scratch.add_fit(ref)
        applied = conditions.apply(fit, cond, scratch.add_fit)
        target = None
        if view.hasTargets:
            from eos.saveddata.targetProfile import TargetProfile
            profile = conditions.target_profile(cond) or TargetProfile.getIdeal()
            target = TargetWrapper(profile, None, None)
        xs, ys = view.getPlotPoints(main, misc, x_def, y_def,
                                    SourceWrapper(fit, None), target)
    return {
        "graph": graph,
        "x": {"handle": x_def.handle, "unit": x_def.unit},
        "y": {"handle": y_def.handle, "unit": y_def.unit},
        "points": _downsample(list(xs), list(ys), max_points),
        "applied": applied,
    }
