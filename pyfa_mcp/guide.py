"""Fleet fitting principles from fitting_guide.yaml, filtered by tank layer,
space and fleet size. Plain data: never boots Pyfa.

The user's own copy in the data dir (~/.pyfa-mcp/fitting_guide.yaml) replaces
the bundled one, and is re-read whenever it changes: no restart, no rebuild."""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

GUIDE = Path(__file__).with_name("fitting_guide.yaml")
# when-key -> the axis it reads
AXES_OF_WHEN = {"tank": "tank", "space": "space", "min_pilots": "pilots", "max_pilots": "pilots"}


def _source(data_dir: Path | None) -> Path:
    mine = Path(data_dir) / GUIDE.name if data_dir else None
    return mine if mine and mine.is_file() else GUIDE


@functools.lru_cache(maxsize=4)
def _load(path: Path, mtime_ns: int) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
          pilots: int | None = None, data_dir: Path | None = None) -> dict:
    source = _source(data_dir)
    try:
        result = _guide(_load(source, source.stat().st_mtime_ns), role, tank, space, pilots)
    except (KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        # the YAML is hand-edited: say where the problem is, not just "error"
        raise ValueError(f"{source} is malformed ({type(exc).__name__}: {exc})") from exc
    result["source"] = str(source)
    if role is None:
        result["customize"] = (f"copy {GUIDE} to {Path(data_dir or '.') / GUIDE.name} and "
                               "edit it; changes apply on the next call")
    return result


def _guide(data, role, tank, space, pilots) -> dict:
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
