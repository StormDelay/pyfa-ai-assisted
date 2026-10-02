"""Is the user's Pyfa the one we compute with, is either out of date, and
does eos handle every effect the game data uses?

status() reports all of it; evaluations warn about items whose effects eos
does not compute. Network calls (GitHub's latest-release API) happen only
from status(), at most daily per repo, and fail quietly.
"""
from __future__ import annotations

import functools
import itertools
import json
import re
import time
from pathlib import Path

import yaml

from pyfa_mcp import eosboot, pyfadata

PYFA_REPO = "pyfa-org/Pyfa"
OWN_REPO = "StormDelay/pyfa-ai-assisted"
BASELINE = Path(__file__).with_name("unhandled_effects.json")
_DAY = 24 * 3600
_GAMEDATA_LINE = re.compile(r"Gamedata connection: sqlite:///(.+?)[\\/]eve\.db")


# --- versions ----------------------------------------------------------------

def installed_pyfa_version() -> str | None:
    """From the install named in Pyfa's newest log: Pyfa logs its eve.db path at startup."""
    # ponytail: a portable Pyfa (saveInRoot) logs next to itself, not in ~/.pyfa: unknown.
    logs = sorted(pyfadata.pyfa_dir().glob("pyfa*.log"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for log in logs[:3]:
        with open(log, encoding="utf-8", errors="replace") as f:
            for line in itertools.islice(f, 50):
                match = _GAMEDATA_LINE.search(line)
                if match:
                    try:
                        text = (Path(match.group(1)) / "version.yml").read_text(encoding="utf-8")
                        return yaml.safe_load(text)["version"]
                    except (OSError, KeyError, TypeError, yaml.YAMLError):
                        return None
    return None


def _fetch_tag(repo: str) -> str | None:
    """The latest non-prerelease tag; None if the repo has no release (404)."""
    import requests
    reply = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                         headers={"Accept": "application/vnd.github+json"}, timeout=5)
    return reply.json().get("tag_name") if reply.ok else None


def latest_release(repo: str) -> str | None:
    import config
    cache_file = Path(config.savePath) / "release-check.json"
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    entry = cache.get(repo)
    if entry and time.time() - entry["checked"] < _DAY:
        return entry["tag"]
    try:
        tag = _fetch_tag(repo)
    except Exception:  # offline, DNS, TLS, bad JSON: say nothing, keep the last answer
        return entry["tag"] if entry else None
    cache[repo] = {"tag": tag, "checked": time.time()}
    cache_file.write_text(json.dumps(cache), encoding="utf-8")
    return tag


def _own_version() -> str | None:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version("pyfa-mcp")
    except PackageNotFoundError:
        return None


def _newer(a: str | None, b: str | None) -> bool:
    from packaging.version import InvalidVersion, Version
    try:
        return bool(a and b) and Version(a) > Version(b)
    except InvalidVersion:
        return False


# --- effect coverage ---------------------------------------------------------

def _handled() -> set[int]:
    import eos.effects
    return {int(name[6:]) for name in dir(eos.effects)
            if name.startswith("Effect") and name[6:].isdigit()}


@functools.cache
def _published_effects() -> tuple:
    """(typeID, typeName, effectID, effectName) for every effect of a published item."""
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        return tuple(connection.exec_driver_sql(
            "SELECT t.typeID, t.typeName, e.effectID, e.effectName FROM dgmtypeeffects te "
            "JOIN invtypes t ON t.typeID = te.typeID "
            "JOIN dgmeffects e ON e.effectID = te.effectID "
            "WHERE t.published = 1").fetchall())


def unhandled_ids() -> list[int]:
    handled = _handled()
    return sorted({row[2] for row in _published_effects() if row[2] not in handled})


@functools.cache
def _new_rows() -> tuple:
    known = _handled() | set(json.loads(BASELINE.read_text(encoding="utf-8")))
    return tuple(row for row in _published_effects() if row[2] not in known)


def new_unhandled_effects() -> list[dict]:
    effects: dict[int, dict] = {}
    for _, type_name, effect_id, effect_name in _new_rows():
        entry = effects.setdefault(effect_id, {"id": effect_id, "name": effect_name, "items": []})
        if len(entry["items"]) < 10:
            entry["items"].append(type_name)
    return sorted(effects.values(), key=lambda e: e["id"])


def _unhandled_by_type() -> dict[int, list[str]]:
    by_type: dict[int, list[str]] = {}
    for type_id, _, _, effect_name in _new_rows():
        by_type.setdefault(type_id, []).append(effect_name)
    return by_type


def effect_warnings(fit) -> list[str]:
    by_type = _unhandled_by_type()
    if not by_type:
        return []
    items = [fit.ship.item]
    for module in fit.modules:
        if not module.isEmpty:
            items += [module.item, module.charge]
    items += [x.item for x in (*fit.drones, *fit.fighters, *fit.implants, *fit.boosters)]
    return [f"{item.name}: Pyfa {eosboot.pyfa_version()} does not compute its effect "
            f"{', '.join(by_type[item.ID])}; these numbers leave it out"
            for item in dict.fromkeys(i for i in items if i is not None)
            if item.ID in by_type]


# --- the report --------------------------------------------------------------

def report() -> dict:
    pinned = eosboot.pyfa_version()
    found = pyfadata.installed()
    yours = installed_pyfa_version() if found else None
    latest = latest_release(PYFA_REPO)
    ours, ours_latest = _own_version(), latest_release(OWN_REPO)
    effects = new_unhandled_effects()

    warnings = []
    if yours and yours != pinned:
        warnings.append(f"The user's Pyfa is {yours}; pyfa-mcp computes with Pyfa {pinned}, "
                        "so numbers can differ from what their Pyfa shows.")
    if _newer(latest, yours):
        warnings.append(f"Pyfa {latest} is out and the user runs {yours}: "
                        "advise them to update Pyfa.")
    if _newer(ours_latest, ours):
        warnings.append(f"pyfa-mcp {ours_latest} is out (this is {ours}): "
                        "advise the user to update pyfa-mcp.")
    for effect in effects:
        warnings.append(f"Pyfa {pinned} does not compute effect {effect['name']} "
                        f"({effect['id']}), used by {', '.join(effect['items'])}; "
                        "stats of fits with these items leave it out.")
    return {"pyfa_install": {"found": found, "data_dir": str(pyfadata.pyfa_dir()),
                             "version": yours},
            "latest_pyfa_release": latest,
            "pyfa_mcp_version": ours, "latest_pyfa_mcp_release": ours_latest,
            "unhandled_effects": effects,
            "warnings": warnings}
