from scripts import pyfa_imports


def test_every_pyfa_import_is_installed_or_known_absent():
    """A Pyfa release adding a dependency must fail here, not ship a frozen exe without it."""
    assert pyfa_imports.missing() == []


def test_a_missing_import_is_reported(monkeypatch):
    monkeypatch.setattr(pyfa_imports, "KNOWN_ABSENT", {})
    assert "matplotlib" in pyfa_imports.missing()


def test_hidden_imports_include_submodules_named_by_from_imports():
    assert "xml.etree.ElementTree" in pyfa_imports.hidden_imports()
    assert not any(name.split(".")[0] in ("eos", "wx", "config") for name in pyfa_imports.hidden_imports())
