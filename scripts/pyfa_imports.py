"""What the bundled Pyfa source imports, for packaging/pyfa-mcp.spec and its test.

Pyfa travels in the frozen build as data, so PyInstaller sees none of its
imports; the spec lists them as hidden imports from this scan instead. A
top-level module Pyfa imports that is not installed is an error unless it
is in KNOWN_ABSENT: a Pyfa release that adds a dependency must fail the
gate, not ship a frozen exe that lacks it.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYFA = ROOT / "vendor" / "Pyfa"
PACKAGES = ("eos", "service", "graphs", "gui", "utils")
LOCAL = {*PACKAGES, "config", "wx"}  # carried as data, or stubbed by pyfa_mcp.wxstub
KNOWN_ABSENT = {
    "configforced": "Pyfa's optional local override of config.py",
    "matplotlib": "Pyfa's graph window; graphs.data computes without it",
}


def imports() -> set[str]:
    found = set()
    sources = [PYFA / "config.py",
               *(path for package in PACKAGES for path in (PYFA / package).rglob("*.py"))]
    for source in sources:
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                # `from xml.etree import ElementTree` imports a submodule.
                found |= {node.module} | {f"{node.module}.{alias.name}" for alias in node.names}
    return found


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False  # `from x import function`, or a module guarded for another platform


def hidden_imports() -> list[str]:
    return sorted(name for name in imports()
                  if name.split(".")[0] not in LOCAL and _importable(name))


def missing() -> list[str]:
    tops = {name.split(".")[0] for name in imports()} - LOCAL
    return sorted(top for top in tops if top not in KNOWN_ABSENT and not _importable(top))
