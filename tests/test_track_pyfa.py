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
    assert "zealot `tank.ehp.total`: 1000.0 → 1012.5" in text
    assert "capacitor.stable" not in text
    assert "gone: no longer computed" in text and "fresh: new reference fit" in text


def test_notes_when_nothing_moved():
    same = {"zealot": {"tank.ehp.total": 1000.0}}
    assert "No reference-fit value changed." in T.notes(same, same)


def test_notes_keep_large_values_readable():
    text = T.notes({"thanatos": {"tank.ehp.armor": 257931.5, "big": 1234567.0}},
                   {"thanatos": {"tank.ehp.armor": 257931.9, "big": 1234568.0}})
    assert "257931.5 → 257931.9" in text
    assert "1234567.0 → 1234568.0" in text


def test_notes_list_dependency_changes():
    same = {"zealot": {"tank.ehp.total": 1.0}}
    text = T.notes(same, same, ["logbook 1.9.2 -> 1.9.3"])
    assert "### Dependencies" in text and "- logbook 1.9.2 -> 1.9.3" in text


def test_new_dependency_keeps_its_platform_marker():
    pyfa = PYFA_PROJECT.replace('"new_dep==1.0"', '"new_dep==1.0 ; sys_platform == \'win32\'"')
    text, _ = T.sync_pins(OURS, LOCK, "3.14", pyfa)
    assert "new-dep==1.0; sys_platform == 'win32'" in tomllib.loads(text)["project"]["dependencies"]


def test_repin_keeps_an_existing_marker():
    ours = OURS.replace('"logbook==1.9.2",', '"logbook==1.9.2; sys_platform != \'emscripten\'",')
    text, _ = T.sync_pins(ours, LOCK, "3.14", PYFA_PROJECT)
    assert "logbook==1.9.3; sys_platform != 'emscripten'" in \
        tomllib.loads(text)["project"]["dependencies"]


def test_only_project_dependencies_are_repinned():
    ours = OURS.replace('dev = ["pytest>=8", "pyinstaller==6.22.3"]',
                        'dev = [\n    "pytest>=8",\n    "pyinstaller==6.22.3",\n]')
    lock = LOCK + '[[package]]\nname = "pyinstaller"\nversion = "6.21.0"\n'
    text, changes = T.sync_pins(ours, lock, "3.14", PYFA_PROJECT)
    data = tomllib.loads(text)
    assert data["dependency-groups"]["dev"] == ["pytest>=8", "pyinstaller==6.22.3"]
    assert "new-dep==1.0" in data["project"]["dependencies"]
    assert not any("pyinstaller" in c for c in changes)
