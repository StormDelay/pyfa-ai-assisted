# pyfa-mcp Plan 4 — Release pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GitHub Actions that test every change, build and publish the Windows installer on a tag, and track Pyfa releases on their own: hourly, bump the pinned Pyfa, re-sync Python and eos's dependencies from it, gate, then publish `vX.Y.Z+pyfa<version>` or open a PR with the failure.

**Architecture:** `scripts/track_pyfa.py` (stdlib only) owns the version logic: the release tag of a tree, the patch bump, re-pinning `pyproject.toml` from the pinned Pyfa's `.python-version` / `uv.lock` / `pyproject.toml`, and release notes from a reference-fit diff. `packaging/installer_check.ps1` installs a built setup into a throwaway user profile (Codex only), checks registration, serves MCP and uninstalls. Three workflows:
- `tests.yml` runs pytest on Ubuntu for pushes to master and PRs.
- `release.yml` runs on a `v*` tag push, as a reusable workflow, and as a PR check when packaging changes. It builds the exe, smokes it, builds and checks the installer, and publishes, except on PRs.
- `pyfa-bump.yml` runs hourly plus on manual dispatch.
  - A cheap Ubuntu `check` job keeps the schedule alive and compares the latest Pyfa release's commit with the submodule's.
  - On a difference, a `bump` job moves the submodule and runs the whole gate. On success it commits, tags and pushes, then calls `release.yml`. On failure it pushes a `pyfa-bump/<tag>` branch and opens a PR carrying the gate output.

**Tech Stack:** GitHub Actions (`actions/checkout@v7`, `astral-sh/setup-uv@v10`, both current majors as of 2026-10-02), uv, PyInstaller, Inno Setup 6 (preinstalled on `windows-latest`; choco fallback), `gh` CLI.

**Spec:** `docs/superpowers/specs/2026-10-02-pyfa-mcp-design.md` (section "Release pipeline"). Plans 1–3 done.

This is plan 4 of 4.

Verified on 2026-10-02:
- **The repo:** `StormDelay/pyfa-ai-assisted` is **private**, default branch `master`.
  - No branch protection: not available on this plan, so a workflow can push to master.
  - Actions setting `default_workflow_permissions: read`, so workflows declare their own permissions.
  - `can_approve_pull_request_reviews: false`: a workflow cannot open PRs until the user enables "Allow GitHub Actions to create and approve pull requests" (Task 4).
- **Pyfa releases and the pin:**
  - `gh api repos/pyfa-org/Pyfa/releases/latest` gives `v2.69.0`.
  - `gh api repos/pyfa-org/Pyfa/commits/v2.69.0 -q .sha` gives `052c597b…`, the same SHA as `git ls-tree HEAD vendor/Pyfa`. The check job needs no submodule checkout.
- **Pyfa's dependency files:**
  - Its `uv.lock` has `[[package]]` entries with `name`/`version`. Our 13 pins equal its locked versions.
  - Its `pyproject.toml` `dependencies` are our 13 plus `matplotlib`. wx lives in dependency groups, and both are GUI-only.
  - Its `.python-version` is `3.14`.
- **No `.github/` yet.** `uv.lock` is gitignored, so CI resolves from `pyproject.toml`'s exact pins.
- **A drift bug the release tags would trigger:** `drift._newer("v0.1.1+pyfa2.70.0", "0.1.1")` is True (a PEP 440 local version sorts after its public one), so every user would be told to update to the version they run. Task 1 fixes it.

## Global Constraints

- Everything in Plans 1–3's Global Constraints still holds.
- Release pipeline (spec), GitHub Actions, scheduled hourly:
  1. Read the latest non-prerelease `pyfa-org/Pyfa` tag; exit if it equals the pin.
  2. Bump the submodule, re-sync Python and dependency versions from the pinned Pyfa's `.python-version` and `uv.lock`, regenerate `eve.db`, run the suite.
  3. Gate: boots, every tool runs, all reference fits evaluate without error, effect-coverage check clean. Reference-fit number changes do **not** block; they are listed in the release notes.
  4. Pass → commit the bump, build, publish `vX.Y.Z+pyfa<version>`. Fail → open a PR with the bump and the failure output.
  5. Each run calls the workflow-enable API on itself so GitHub's 60-day inactivity rule never disables it.
- Release tag of a tree: `v<pyproject version>+pyfa<pinned Pyfa version without the v>`, e.g. `v0.1.0+pyfa2.69.0`. An automatic bump raises the patch version.
- Installer file: `pyfa-mcp-v<version>-pyfa<pyfa>-setup.exe` (no `+` in file names). Inno `AppVersion` is the plain `X.Y.Z`.
- The installer check in CI never touches a real profile: it runs against a throwaway `USERPROFILE`/`APPDATA`/`LOCALAPPDATA` with only the Codex task on.
- Workflows declare least permissions per workflow or job.

## Review Focus

1. **A Pyfa release that adds a runtime dependency.** Expected: the bump adds it to `pyproject.toml` with the locked version, so the build carries it (a missing one would be silently left out of the frozen exe). (Task 1)
2. **The same version released twice under a new Pyfa (`v0.1.1+pyfa2.70.0`) while the user runs 0.1.1.** Expected: no "update pyfa-mcp" warning. (Task 1)
3. **A gate failure.** Expected: a PR with the bump and the failure output, no tag, no release; the next hourly run does not open a second PR for the same Pyfa tag. (Task 3)
4. **A tag pushed that does not match the tree** (pyproject says 0.2.0, tag says v0.1.9). Expected: the release refuses before building. (Task 3)
5. **Reference-fit numbers move in a Pyfa release.** Expected: the release still publishes, and its notes list each changed value. (Task 1 notes, Task 3 wiring)

---

## File Structure

```
scripts/track_pyfa.py              NEW  tag / version / pyfa / bump / notes
tests/test_track_pyfa.py           NEW
pyfa_mcp/drift.py                  compare release versions without their +pyfa local part
tests/test_drift.py                that comparison
packaging/pyfa-mcp.iss             PyfaVersion in the file name
packaging/installer_check.ps1      NEW  silent install into a throwaway profile, register, smoke, uninstall
.github/workflows/tests.yml        NEW
.github/workflows/release.yml      NEW
.github/workflows/pyfa-bump.yml    NEW
```

---

### Task 1: Release versions and Pyfa tracking, as a script

**Files:**
- Create: `scripts/track_pyfa.py`, `tests/test_track_pyfa.py`
- Modify: `pyfa_mcp/drift.py`, `tests/test_drift.py`, `packaging/pyfa-mcp.iss`

**Interfaces:**
- Produces (`scripts/track_pyfa.py`, importable as `scripts.track_pyfa`):
  - `pyfa_version(pyfa_dir: Path = PYFA) -> str` (`"2.69.0"`)
  - `project_version(pyproject_text: str) -> str`
  - `release_tag(version: str, pyfa: str) -> str`
  - `bump_patch(pyproject_text: str) -> str`
  - `sync_pins(pyproject_text, lock_text, python_version, pyfa_pyproject_text) -> tuple[str, list[str]]`
  - `notes(old: dict, new: dict) -> str` (Markdown)
  - CLI: `tag`, `version`, `pyfa`, `bump`, `notes OLD NEW`
- `drift._newer(a, b)` compares public versions.
- `packaging/pyfa-mcp.iss` requires `/DPyfaVersion=`.

- [ ] **Step 1: Write the failing tests**

`tests/test_track_pyfa.py`:
```python
import tomllib

from pyfa_mcp import eosboot
from scripts import track_pyfa as T

OURS = '''[project]
name = "pyfa-mcp"
version = "0.1.9"
requires-python = ">=3.14,<3.15"
dependencies = [
    "logbook==1.9.2",
    "numpy==2.5.1",
    "mcp>=2.2,<3",
]

[dependency-groups]
dev = ["pytest>=8", "pyinstaller==6.22.3"]
'''
LOCK = '''version = 1
[[package]]
name = "logbook"
version = "1.9.3"
[[package]]
name = "numpy"
version = "2.5.1"
[[package]]
name = "new-dep"
version = "1.0"
[[package]]
name = "matplotlib"
version = "3.11.1"
'''
PYFA_PROJECT = '''[project]
name = "pyfa"
dependencies = ["logbook==1.9.3", "numpy==2.5.1", "matplotlib==3.11.1", "new_dep==1.0"]
'''


def test_tag_of_this_tree():
    text = T.PYPROJECT.read_text(encoding="utf-8")
    assert T.pyfa_version() == eosboot.pyfa_version().removeprefix("v")
    assert T.release_tag(T.project_version(text), T.pyfa_version()) == \
        f"v{T.project_version(text)}+pyfa{T.pyfa_version()}"
    assert T.release_tag("0.1.0", "2.69.0") == "v0.1.0+pyfa2.69.0"


def test_bump_patch():
    assert T.project_version(T.bump_patch(OURS)) == "0.1.10"


def test_sync_pins_follows_pyfa():
    text, changes = T.sync_pins(OURS, LOCK, "3.15\n", PYFA_PROJECT)
    deps = tomllib.loads(text)["project"]["dependencies"]
    assert deps == ["logbook==1.9.3", "numpy==2.5.1", "new-dep==1.0", "mcp>=2.2,<3"]
    assert tomllib.loads(text)["project"]["requires-python"] == ">=3.15,<3.16"
    assert tomllib.loads(text)["dependency-groups"]["dev"] == ["pytest>=8", "pyinstaller==6.22.3"]
    assert "logbook 1.9.2 -> 1.9.3" in changes
    assert "new-dep 1.0 (new in Pyfa)" in changes
    assert not any("matplotlib" in c for c in changes)  # Pyfa's window only


def test_sync_pins_on_this_tree_changes_nothing():
    pyfa = T.PYFA
    before = T.PYPROJECT.read_text(encoding="utf-8")
    after, changes = T.sync_pins(before, (pyfa / "uv.lock").read_text(encoding="utf-8"),
                                 (pyfa / ".python-version").read_text(encoding="utf-8"),
                                 (pyfa / "pyproject.toml").read_text(encoding="utf-8"))
    assert changes == []
    assert after == before


def test_notes_list_every_changed_value():
    old = {"zealot": {"tank.ehp.total": 1000.0, "capacitor.stable": True},
           "gone": {"tank.ehp.total": 1.0}}
    new = {"zealot": {"tank.ehp.total": 1012.5, "capacitor.stable": True},
           "fresh": {"tank.ehp.total": 2.0}}
    text = T.notes(old, new)
    assert "zealot `tank.ehp.total`: 1000 → 1012.5" in text
    assert "capacitor.stable" not in text
    assert "gone: no longer computed" in text and "fresh: new reference fit" in text


def test_notes_when_nothing_moved():
    same = {"zealot": {"tank.ehp.total": 1000.0}}
    assert "No reference-fit value changed." in T.notes(same, same)
```

Append to `tests/test_drift.py`:
```python
def test_a_release_of_the_version_we_run_is_not_newer():
    assert not drift._newer("v0.1.1+pyfa2.70.0", "0.1.1")
    assert drift._newer("v0.1.2+pyfa2.70.0", "0.1.1")
    assert drift._newer("v2.70.0", "v2.69.0")
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/Scripts/python -m pytest tests/test_track_pyfa.py tests/test_drift.py -q`
Expected: collection error for `test_track_pyfa.py` (`cannot import name 'track_pyfa'`), and `test_a_release_of_the_version_we_run_is_not_newer` FAILS.

- [ ] **Step 3: Write `scripts/track_pyfa.py`**

```python
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
```

- [ ] **Step 4: Compare public versions in `pyfa_mcp/drift.py`**

Replace `_newer`:
```python
def _newer(a: str | None, b: str | None) -> bool:
    """a > b, ignoring a local part: v0.1.1+pyfa2.70.0 is release 0.1.1 built on Pyfa 2.70."""
    from packaging.version import InvalidVersion, Version
    try:
        return bool(a and b) and Version(Version(a).public) > Version(Version(b).public)
    except InvalidVersion:
        return False
```

- [ ] **Step 5: Name the installer after both versions**

In `packaging/pyfa-mcp.iss`, replace the header comment's build line and the `AppVersion` guard:
```
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=0.1.0 /DPyfaVersion=2.69.0 packaging\pyfa-mcp.iss
;
; after building dist\pyfa-mcp with packaging\pyfa-mcp.spec. Both versions
; come from scripts/track_pyfa.py (version, pyfa) and have no default, so a
; build can never quietly carry a stale one.

#ifndef AppVersion
  #error Pass /DAppVersion=x.y.z -- scripts/track_pyfa.py version
#endif
#ifndef PyfaVersion
  #error Pass /DPyfaVersion=x.y.z -- scripts/track_pyfa.py pyfa
#endif
```
and set:
```
AppVerName={#AppName} {#AppVersion} (Pyfa {#PyfaVersion})
OutputBaseFilename={#AppName}-v{#AppVersion}-pyfa{#PyfaVersion}-setup
```
(`AppVerName` goes right after `AppVersion=`; `OutputBaseFilename` replaces the existing line.)

Run (PowerShell): `& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /Q /DAppVersion=0.1.0 /DPyfaVersion=2.69.0 packaging\pyfa-mcp.iss; $LASTEXITCODE; Test-Path dist\pyfa-mcp-v0.1.0-pyfa2.69.0-setup.exe`
Expected: `0`, `True`.

- [ ] **Step 6: Run the tests**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all pass. Also: `.venv/Scripts/python scripts/track_pyfa.py tag` prints `v0.1.0+pyfa2.69.0`.

- [ ] **Step 7: Commit**

```bash
git add scripts/track_pyfa.py tests/test_track_pyfa.py pyfa_mcp/drift.py tests/test_drift.py packaging/pyfa-mcp.iss
git commit -m "Name releases v<version>+pyfa<pyfa>, re-pin from a Pyfa release, and list reference-fit changes as notes"
```

---

### Task 2: The installer check, as a script CI and humans both run

**Files:**
- Create: `packaging/installer_check.ps1`

**Interfaces:**
- Consumes: a built setup (`dist/pyfa-mcp-v*-pyfa*-setup.exe`), `packaging/mcp_smoke.py`, the installer's per-client tasks.
- Produces: `powershell -File packaging/installer_check.ps1 -Setup <exe> [-Python <python>]`, exit 0 on success.

- [ ] **Step 1: Write `packaging/installer_check.ps1`**

```powershell
<# Install a built pyfa-mcp setup silently into a throwaway user profile, and
   check it registers, serves MCP and uninstalls cleanly.

     powershell -File packaging\installer_check.ps1 -Setup dist\pyfa-mcp-v0.1.0-pyfa2.69.0-setup.exe

   Only the Codex task is on, and USERPROFILE / HOME / APPDATA / LOCALAPPDATA
   point into the throwaway profile: the installer's client checks and
   pyfa-mcp.exe --register read those, so neither can reach the real user's
   configs. #>
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"

$T = Join-Path ([IO.Path]::GetTempPath()) ("pyfa-mcp-installer-" + [guid]::NewGuid())
$H = Join-Path $T "home"
New-Item -ItemType Directory "$H\.codex", "$H\AppData\Roaming", "$H\AppData\Local" -Force | Out-Null
$env:USERPROFILE = $H
$env:HOME = $H
$env:APPDATA = "$H\AppData\Roaming"
$env:LOCALAPPDATA = "$H\AppData\Local"
$codex = "$H\.codex\config.toml"

$tasks = "codex,!claude_desktop,!claude_code,!cursor,!windsurf,!devin,!vscode"
$p = Start-Process (Resolve-Path $Setup) -Wait -PassThru -ArgumentList `
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$T\app", "/MERGETASKS=$tasks", "/LOG=$T\install.log"
if ($p.ExitCode -ne 0) { Write-Host "install failed: exit $($p.ExitCode)"; exit 1 }
if (Select-String -Path "$T\install.log" -Pattern "Registration failures" -Quiet) {
    Get-Content "$T\install.log" | Select-Object -Last 20 | ForEach-Object { Write-Host $_ }
    Write-Host "registration failed"; exit 1
}
if (-not ((Test-Path $codex) -and (Select-String -Path $codex -Pattern "pyfa-mcp.exe" -SimpleMatch -Quiet))) {
    Write-Host "the Codex task did not register pyfa-mcp"; exit 1
}

& $Python packaging\mcp_smoke.py "$T\app\pyfa-mcp.exe" --data-dir "$T\data" --pyfa-dir "$T\no-pyfa"
if ($LASTEXITCODE -ne 0) { Write-Host "the installed exe failed the smoke"; exit 1 }

Start-Process "$T\app\unins000.exe" -Wait -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" | Out-Null
# Inno's uninstaller relaunches itself from a temp copy: wait for the files, not the process.
$deadline = (Get-Date).AddSeconds(120)
while ((Test-Path "$T\app\pyfa-mcp.exe") -and ((Get-Date) -lt $deadline)) { Start-Sleep -Seconds 2 }
if (Test-Path "$T\app\pyfa-mcp.exe") { Write-Host "not uninstalled"; exit 1 }
if (Select-String -Path $codex -Pattern "mcp_servers.pyfa" -SimpleMatch -Quiet) {
    Write-Host "uninstall left the Codex entry behind"; exit 1
}
Write-Host "installer ok"
# Said out loud: a native command's last exit code would otherwise become ours.
exit 0
```

- [ ] **Step 2: Run it against the local build**

The bundle must match the tree first. Run from the repo root:
1. `.venv/Scripts/python -m PyInstaller --noconfirm --log-level WARN packaging/pyfa-mcp.spec`
2. The Task 1 Step 5 ISCC command.
3. In PowerShell: `powershell -NoProfile -File packaging\installer_check.ps1 -Setup dist\pyfa-mcp-v0.1.0-pyfa2.69.0-setup.exe -Python .venv\Scripts\python.exe; $LASTEXITCODE`

Expected: `ok` (from the smoke), `installer ok`, `0`.

Then break it on purpose to see the check fail: rerun with `-Setup dist\does-not-exist.exe`.
Expected: an error and a non-zero exit code.

- [ ] **Step 3: Commit**

```bash
git add packaging/installer_check.ps1
git commit -m "Check a built installer end to end in a throwaway user profile"
```

---

### Task 3: The workflows

**Files:**
- Create: `.github/workflows/tests.yml`, `.github/workflows/release.yml`, `.github/workflows/pyfa-bump.yml`

**Interfaces:**
- Consumes: `scripts/track_pyfa.py` (`tag`, `version`, `pyfa`, `bump`, `notes`), `scripts/make_evedb.py`, `scripts/record_reference.py`, `packaging/pyfa-mcp.spec`, `packaging/pyfa-mcp.iss` (`/DAppVersion`, `/DPyfaVersion`), `packaging/mcp_smoke.py`, `packaging/installer_check.ps1`.
- Produces:
  - `release.yml` reusable with inputs `tag` (string, required) and `notes` (string, optional)
  - `pyfa-bump.yml` `workflow_dispatch` inputs `pyfa_tag` (force a target) and `dry_run` (boolean)

- [ ] **Step 1: Write `.github/workflows/tests.yml`**

```yaml
name: tests

on:
  push:
    branches: [master]
  pull_request:

permissions:
  contents: read

jobs:
  pytest:
    # Linux: the engine, the store and every test are platform-neutral, and
    # Linux minutes are a tenth of Windows' on a private repo. The Windows
    # build is checked by release.yml.
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          submodules: true
          fetch-depth: 1   # 279 MB of Pyfa history is not needed for its tree
      - uses: astral-sh/setup-uv@v10
      - run: uv sync
      - run: uv run python scripts/make_evedb.py
      - run: uv run pytest -q
```

- [ ] **Step 2: Write `.github/workflows/release.yml`**

```yaml
name: release

# A tag push publishes; pyfa-bump.yml calls this after pushing its own tag
# (a tag pushed with GITHUB_TOKEN starts no workflow by itself); a PR that
# touches packaging gets the same build and checks, without publishing.
on:
  push:
    tags: ["v*"]
  workflow_call:
    inputs:
      tag:
        type: string
        required: true
      notes:
        type: string
        required: false
        default: ""
  pull_request:
    paths:
      - "packaging/**"
      - ".github/workflows/release.yml"
      - "pyproject.toml"
      - "vendor/Pyfa"

permissions:
  contents: write   # gh release create

jobs:
  windows:
    runs-on: windows-latest
    env:
      TAG: ${{ inputs.tag || github.ref_name }}
      PUBLISH: ${{ github.event_name != 'pull_request' }}
    steps:
      - uses: actions/checkout@v7
        with:
          ref: ${{ inputs.tag || github.ref }}
          submodules: true
          fetch-depth: 1
      - uses: astral-sh/setup-uv@v10
      - run: uv sync

      - name: The tag has to name this tree's version and Pyfa
        if: env.PUBLISH == 'true'
        shell: bash
        run: |
          want=$(uv run python scripts/track_pyfa.py tag)
          if [ "$want" != "$TAG" ]; then
            echo "tag $TAG, but pyproject.toml and vendor/Pyfa make this $want" >&2
            exit 1
          fi

      - run: uv run python scripts/make_evedb.py
      - run: uv run pytest -q
      - run: uv run pyinstaller --noconfirm packaging/pyfa-mcp.spec

      - name: The build has to serve MCP
        shell: bash
        run: |
          uv run python packaging/mcp_smoke.py "$(cygpath -w "$PWD/dist/pyfa-mcp/pyfa-mcp.exe")" \
            --data-dir "$RUNNER_TEMP/data" --pyfa-dir "$RUNNER_TEMP/no-pyfa"

      - name: Build the installer
        shell: pwsh
        run: |
          $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
          if (-not (Test-Path $iscc)) { choco install innosetup -y --no-progress }
          $version = uv run python scripts/track_pyfa.py version
          $pyfa = uv run python scripts/track_pyfa.py pyfa
          & $iscc /Q "/DAppVersion=$version" "/DPyfaVersion=$pyfa" packaging\pyfa-mcp.iss
          exit $LASTEXITCODE

      - name: The installer has to register, serve and uninstall
        shell: pwsh
        run: |
          $setup = (Get-Item dist\pyfa-mcp-v*-setup.exe).FullName
          uv run powershell -NoProfile -File packaging\installer_check.ps1 -Setup $setup
          exit $LASTEXITCODE

      - name: Publish
        if: env.PUBLISH == 'true'
        shell: bash
        env:
          GH_TOKEN: ${{ github.token }}
          NOTES: ${{ inputs.notes }}
        run: |
          if [ -n "$NOTES" ]; then
            gh release create "$TAG" dist/pyfa-mcp-v*-setup.exe --title "$TAG" --notes "$NOTES"
          else
            gh release create "$TAG" dist/pyfa-mcp-v*-setup.exe --title "$TAG" --generate-notes
          fi
```

- [ ] **Step 3: Write `.github/workflows/pyfa-bump.yml`**

```yaml
name: pyfa-bump

on:
  schedule:
    - cron: "23 * * * *"   # hourly
  workflow_dispatch:
    inputs:
      pyfa_tag:
        description: "Pyfa tag to move to (default: the latest release)"
        type: string
        required: false
      dry_run:
        description: "Run the gate, publish nothing"
        type: boolean
        default: false

concurrency:
  group: pyfa-bump
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: write   # the enable call below
    outputs:
      target: ${{ steps.look.outputs.target }}
    steps:
      - name: Keep this schedule alive
        # GitHub disables a scheduled workflow after 60 days without repo
        # activity; Pyfa once went 65 days between releases.
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh api -X PUT "repos/$GITHUB_REPOSITORY/actions/workflows/pyfa-bump.yml/enable"
      - uses: actions/checkout@v7   # no submodules: the pin is the gitlink's commit
      - id: look
        env:
          GH_TOKEN: ${{ github.token }}
          FORCED: ${{ inputs.pyfa_tag }}
        run: |
          latest=${FORCED:-$(gh api repos/pyfa-org/Pyfa/releases/latest -q .tag_name)}
          want=$(gh api "repos/pyfa-org/Pyfa/commits/$latest" -q .sha)
          pinned=$(git ls-tree HEAD vendor/Pyfa | awk '{print $3}')
          echo "pinned $pinned; $latest is $want"
          if [ -n "$FORCED" ] || [ "$want" != "$pinned" ]; then
            if [ -z "$FORCED" ] && [ -n "$(gh pr list --head "pyfa-bump/$latest" --state open -q '.[].number' --json number)" ]; then
              echo "a PR for $latest is already open; waiting for a human"
            else
              echo "target=$latest" >> "$GITHUB_OUTPUT"
            fi
          fi

  bump:
    needs: check
    if: needs.check.outputs.target != ''
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    env:
      TARGET: ${{ needs.check.outputs.target }}
      DRY_RUN: ${{ inputs.dry_run || false }}
    outputs:
      tag: ${{ steps.publish.outputs.tag }}
      notes: ${{ steps.publish.outputs.notes }}
    steps:
      - uses: actions/checkout@v7
        with:
          submodules: true
          fetch-depth: 1
      - name: Move vendor/Pyfa to the release
        run: |
          git -C vendor/Pyfa fetch --depth 1 origin "refs/tags/$TARGET:refs/tags/$TARGET"
          git -C vendor/Pyfa checkout --quiet "$TARGET"
      - uses: astral-sh/setup-uv@v10

      - name: Gate
        id: gate
        continue-on-error: true
        run: |
          set -o pipefail
          {
            python3 scripts/track_pyfa.py bump
            uv sync
            uv run python scripts/make_evedb.py
            cp tests/reference/expected.json "$RUNNER_TEMP/expected-before.json"
            # Every reference fit has to evaluate; a changed number does not block.
            uv run python scripts/record_reference.py
            uv run python scripts/track_pyfa.py notes "$RUNNER_TEMP/expected-before.json" \
              tests/reference/expected.json > "$RUNNER_TEMP/notes.md"
            # Boots, every tool over stdio, effect coverage, the reference fits as recorded.
            uv run pytest -q
          } 2>&1 | tee "$RUNNER_TEMP/gate.log"

      - name: Publish the bump
        id: publish
        if: steps.gate.outcome == 'success' && env.DRY_RUN != 'true'
        run: |
          tag=$(python3 scripts/track_pyfa.py tag)
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git commit -am "Track Pyfa $TARGET"
          git tag "$tag"
          git push origin "HEAD:${{ github.event.repository.default_branch }}" "$tag"
          echo "tag=$tag" >> "$GITHUB_OUTPUT"
          { echo "notes<<PYFA_BUMP_NOTES"; echo "Tracks Pyfa $TARGET."; echo
            cat "$RUNNER_TEMP/notes.md"; echo "PYFA_BUMP_NOTES"; } >> "$GITHUB_OUTPUT"

      - name: Open a PR with the failure
        if: steps.gate.outcome != 'success' && env.DRY_RUN != 'true'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          branch="pyfa-bump/$TARGET"
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git switch -c "$branch"
          git commit -am "Track Pyfa $TARGET (gate failed)"
          git push --force origin "$branch"
          { echo "The automatic move to Pyfa $TARGET failed its gate."; echo
            echo '```'; tail -n 150 "$RUNNER_TEMP/gate.log"; echo '```'; echo
            cat "$RUNNER_TEMP/notes.md" 2>/dev/null || true; } > "$RUNNER_TEMP/body.md"
          gh pr create --base "${{ github.event.repository.default_branch }}" --head "$branch" \
            --title "Track Pyfa $TARGET (gate failed)" --body-file "$RUNNER_TEMP/body.md"

      - name: Say how the gate went
        run: |
          cat "$RUNNER_TEMP/notes.md" >> "$GITHUB_STEP_SUMMARY" 2>/dev/null || true
          test "${{ steps.gate.outcome }}" = success

  release:
    needs: bump
    if: needs.bump.outputs.tag != ''
    permissions:
      contents: write
    uses: ./.github/workflows/release.yml
    with:
      tag: ${{ needs.bump.outputs.tag }}
      notes: ${{ needs.bump.outputs.notes }}
```

- [ ] **Step 4: Check the files parse**

Run: `.venv/Scripts/python -c "import yaml, pathlib; [print(p, sorted(yaml.safe_load(p.read_text())['jobs'])) for p in pathlib.Path('.github/workflows').glob('*.yml')]"`
Expected: `pyfa-bump.yml ['bump', 'check', 'release']`, `release.yml ['windows']`, `tests.yml ['pytest']`. (PyYAML reads the `on:` key as `True`; that is fine.)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows
git commit -m "Test every change, release on a tag, and follow Pyfa releases hourly"
```

- [ ] **Step 6: Run them on GitHub**

Pushing the branch and opening a PR is outward-facing; the user approves this plan with that step in it.
1. Push `feat/packaging` and open a PR against `feat/engine-mcp-tools`. That base keeps it in the chain under PR #1.
2. On that PR: `tests` must pass, and `release` must run its `windows` job (it touches `packaging/**`), green, with its tag-check and publish steps skipped. Watch with `gh pr checks <n> --watch`.

If `tests` fails on Linux only, fix the cause with a regression test. The plan's premise is platform-neutral tests, so a Linux-only failure is a finding.

Expected: both checks green.

---

### Task 4: Live on master — HUMAN CHECKPOINT

These steps change shared state (master, repository settings, a public release), so each is the user's call, made after the PRs are reviewed.

- [ ] **Step 1:** The user merges the PR chain into master: this branch's PR into `feat/engine-mcp-tools`, then PR #1 into `master`.
- [ ] **Step 2:** The user enables Settings → Actions → General → Workflow permissions → **"Allow GitHub Actions to create and approve pull requests"**. Without it the failure path cannot open its PR.
- [ ] **Step 3:** Dry-run the bump against the current pin, from the Actions tab or with `gh workflow run pyfa-bump.yml -f pyfa_tag=v2.69.0 -f dry_run=true`.
  Expected: `check` green (with the "keep alive" call), `bump` green with the gate passing and "No reference-fit value changed." in the summary, `release` skipped, nothing pushed.
- [ ] **Step 4:** The first release. The user tags `master` with `v0.1.0+pyfa2.69.0` and pushes it (`git tag v0.1.0+pyfa2.69.0 && git push origin v0.1.0+pyfa2.69.0`).
  Expected: `release` builds, checks and publishes `pyfa-mcp-v0.1.0-pyfa2.69.0-setup.exe` under that tag. This is the installer the user then tests by hand.

---

## Self-review notes

- **Spec coverage:**
  - Step 1: hourly check on the latest non-prerelease tag, compared by commit with the gitlink, no checkout.
  - Step 2: submodule bump, re-sync of Python and dependencies from `.python-version` and `uv.lock`, `eve.db` regenerated, suite run.
  - Step 3: the gate covers boot (pytest's session boot), every tool (`test_smoke_over_stdio`), all reference fits evaluating (`record_reference.py`) and effect coverage (`test_effect_coverage_is_clean`). Reference numbers don't block, since `expected.json` is re-recorded before the suite, and they go into the notes.
  - Step 4: pass means commit, tag and the release workflow; fail means a PR with the failure.
  - Step 5: the enable API runs every run.
  - Also: the "MCP smoke test against the built exe in CI" from the spec's Testing section.
- **Deliberate calls:**
  - **Tests on Linux** to save minutes; the Windows build runs on tags and packaging PRs.
  - **An automatic bump raises the patch version.**
  - **The installer file name has no `+`.**
  - **`dry_run` and `pyfa_tag` dispatch inputs**, so the bump path can be exercised without a real Pyfa release.
  - **One PR per Pyfa tag**: the check job skips while one is open.
  - **Pyfa's new runtime dependencies are added automatically** (GUI-only ones excluded). This closes Plan 3's deferred minor about a missing import being silently left out of the build.
- **Cost note for the user:** the repo is private. An hourly Ubuntu check is ~720 billed minutes a month even when idle (each run bills at least a minute). The spec says hourly, so the plan keeps it; changing the cron to `23 */6 * * *` cuts it six-fold.
- **Types:** tags are strings everywhere. `TARGET` is a Pyfa tag (`v2.70.0`), and `tag` is ours (`v0.1.1+pyfa2.70.0`).
