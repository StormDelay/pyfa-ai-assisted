"""Keep pyfa-mcp in step with the Pyfa release it is pinned to.

    python scripts/track_pyfa.py tag       release tag of this tree: v<version>+pyfa<pyfa>
    python scripts/track_pyfa.py version   pyfa-mcp's version (pyproject.toml)
    python scripts/track_pyfa.py pyfa      the pinned Pyfa's version (vendor/Pyfa/version.yml)
    python scripts/track_pyfa.py bump      after vendor/Pyfa moved to a new release: take
                                           Python and eos's dependency versions from it,
                                           and raise our patch version
    python scripts/track_pyfa.py notes OLD NEW   reference-fit changes, as Markdown

Standard library only: the bump job runs it before any environment exists.
"""
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYFA = ROOT / "vendor" / "Pyfa"
PYPROJECT = ROOT / "pyproject.toml"
GUI_ONLY = {"matplotlib", "wxpython"}  # Pyfa's own window; eos never imports them
# One pinned dependency per line, as in [project] dependencies.
_PIN = re.compile(r'^([ \t]*")([A-Za-z0-9_.\-]+)==([^"]+)(",?)[ \t]*$', re.M)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pyfa_version(pyfa_dir: Path = PYFA) -> str:
    text = (pyfa_dir / "version.yml").read_text(encoding="utf-8")
    return re.search(r"^version:\s*v?(\S+)", text, re.M).group(1)


def project_version(text: str) -> str:
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)


def release_tag(version: str, pyfa: str) -> str:
    return f"v{version}+pyfa{pyfa}"


def bump_patch(text: str) -> str:
    major, minor, patch = project_version(text).split(".")
    return re.sub(r'^version = "[^"]+"', f'version = "{major}.{minor}.{int(patch) + 1}"',
                  text, count=1, flags=re.M)


def sync_pins(text: str, lock: str, python: str, pyfa_project: str) -> tuple[str, list[str]]:
    """Our pyproject with eos's pins and the Python version taken from Pyfa's."""
    locked = {_norm(p["name"]): p["version"] for p in tomllib.loads(lock)["package"]}
    wanted = {_norm(re.match(r"[A-Za-z0-9_.\-]+", dep).group(0))
              for dep in tomllib.loads(pyfa_project)["project"]["dependencies"]} - GUI_ONLY
    changes: list[str] = []
    ours: set[str] = set()

    def repin(match: re.Match) -> str:
        name, old = match.group(2), match.group(3)
        ours.add(_norm(name))
        new = locked.get(_norm(name), old)
        if new != old:
            changes.append(f"{name} {old} -> {new}")
        return f"{match.group(1)}{name}=={new}{match.group(4)}"

    text = _PIN.sub(repin, text)
    # A dependency Pyfa gained: without it the frozen build would silently lack it.
    for name in sorted(wanted - ours):
        last = list(_PIN.finditer(text))[-1]
        text = f'{text[:last.end()]}\n    "{name}=={locked[name]}",{text[last.end():]}'
        changes.append(f"{name} {locked[name]} (new in Pyfa)")
    major, minor = python.strip().split(".")[:2]
    text = re.sub(r'^requires-python = "[^"]+"',
                  f'requires-python = ">={major}.{minor},<{major}.{int(minor) + 1}"',
                  text, count=1, flags=re.M)
    return text, changes


def _fmt(value) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def notes(old: dict, new: dict) -> str:
    lines = []
    for case in sorted(old.keys() | new.keys()):
        if case not in new:
            lines.append(f"- {case}: no longer computed")
        elif case not in old:
            lines.append(f"- {case}: new reference fit")
        else:
            for key in sorted(old[case].keys() | new[case].keys()):
                before, after = old[case].get(key), new[case].get(key)
                if before != after:
                    lines.append(f"- {case} `{key}`: {_fmt(before)} → {_fmt(after)}")
    body = "\n".join(lines) if lines else "No reference-fit value changed."
    return f"### Reference fits\n\n{body}\n"


def bump() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    text, changes = sync_pins(text, (PYFA / "uv.lock").read_text(encoding="utf-8"),
                              (PYFA / ".python-version").read_text(encoding="utf-8"),
                              (PYFA / "pyproject.toml").read_text(encoding="utf-8"))
    text = bump_patch(text)
    PYPROJECT.write_text(text, encoding="utf-8")
    for change in changes:
        print(change)
    print(release_tag(project_version(text), pyfa_version()))


if __name__ == "__main__":
    command, *args = sys.argv[1:] or [""]
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    if command == "tag":
        print(release_tag(project_version(pyproject), pyfa_version()))
    elif command == "version":
        print(project_version(pyproject))
    elif command == "pyfa":
        print(pyfa_version())
    elif command == "bump":
        bump()
    elif command == "notes" and len(args) == 2:
        old, new = (json.loads(Path(a).read_text(encoding="utf-8")) for a in args)
        print(notes(old, new), end="")
    else:
        sys.exit(__doc__)
