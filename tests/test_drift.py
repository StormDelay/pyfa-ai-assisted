import json

import pytest

from pyfa_mcp import drift, eosboot, evaluate


@pytest.fixture
def fresh_cache(booted):
    cache = booted / "release-check.json"
    cache.unlink(missing_ok=True)
    yield cache
    cache.unlink(missing_ok=True)


def _install(home, tmp_path, version):
    """A Pyfa install at tmp_path, named in a Pyfa log in home, as Pyfa logs it."""
    app = tmp_path / "Program Files" / "pyfa" / "app"
    app.mkdir(parents=True)
    (app / "version.yml").write_text(f"version: {version}\n", encoding="utf-8")
    (home / "pyfa-2026-10-02.log").write_text(
        "[2026-10-02 12:46:10.387761] INFO: __main__: Starting Pyfa\n"
        "[2026-10-02 12:46:10.402578] INFO: eos.db: Gamedata connection: "
        f"sqlite:///{app / 'eve.db'}?check_same_thread=False\n", encoding="utf-8")


def test_latest_release_is_cached_for_a_day(fresh_cache, monkeypatch):
    calls = []
    monkeypatch.setattr(drift, "_fetch_tag", lambda repo: calls.append(repo) or "v9.9.9")
    assert drift.latest_release(drift.PYFA_REPO) == "v9.9.9"
    assert drift.latest_release(drift.PYFA_REPO) == "v9.9.9"
    assert calls == [drift.PYFA_REPO]


def test_offline_is_quiet(fresh_cache):
    assert drift.latest_release(drift.PYFA_REPO) is None


def test_offline_keeps_the_last_answer(fresh_cache):
    fresh_cache.write_text(json.dumps({drift.PYFA_REPO: {"tag": "v2.70.0", "checked": 0}}))
    assert drift.latest_release(drift.PYFA_REPO) == "v2.70.0"


def test_installed_pyfa_version_from_its_log(pyfa_home, tmp_path):
    assert drift.installed_pyfa_version() is None
    _install(pyfa_home, tmp_path, "v2.68.0")
    assert drift.installed_pyfa_version() == "v2.68.0"


def test_report_warns_about_an_old_user_pyfa(pyfa_home, tmp_path, monkeypatch):
    _install(pyfa_home, tmp_path, "v2.68.0")
    monkeypatch.setattr(drift, "latest_release",
                        lambda repo: "v2.70.0" if repo == drift.PYFA_REPO else None)
    report = drift.report()
    assert report["pyfa_install"] == {"found": True, "data_dir": str(pyfa_home),
                                      "version": "v2.68.0"}
    text = " ".join(report["warnings"])
    assert "v2.68.0" in text and "update Pyfa" in text


def test_report_is_quiet_when_everything_matches(pyfa_home, tmp_path, monkeypatch):
    _install(pyfa_home, tmp_path, eosboot.pyfa_version())
    monkeypatch.setattr(drift, "latest_release", lambda repo: None)
    assert drift.report()["warnings"] == []


def test_report_without_pyfa(booted, monkeypatch):
    monkeypatch.setattr(drift, "latest_release", lambda repo: None)
    report = drift.report()
    assert report["pyfa_install"]["found"] is False
    assert report["warnings"] == []


def test_report_offline_is_quiet(booted, fresh_cache):
    assert drift.report()["warnings"] == []


def test_newer_pyfa_mcp_release(booted, monkeypatch):
    monkeypatch.setattr(drift, "_own_version", lambda: "0.1.0")
    monkeypatch.setattr(drift, "latest_release",
                        lambda repo: "v0.2.0+pyfa2.69.0" if repo == drift.OWN_REPO else None)
    assert any("pyfa-mcp v0.2.0+pyfa2.69.0" in w for w in drift.report()["warnings"])


def test_effect_coverage_is_clean(booted):
    """The Pyfa-bump gate: every effect eos lacks is in the reviewed baseline."""
    assert drift.new_unhandled_effects() == []


def test_evaluation_warns_about_unhandled_effects(booted, zealot_eft, monkeypatch, no_fits_left):
    from service.market import Market
    zealot = Market.getInstance().getItem("Zealot")
    monkeypatch.setattr(drift, "_unhandled_by_type", lambda: {zealot.ID: ["someEffect"]})
    warnings = evaluate.evaluate(zealot_eft, None)["warnings"]
    assert any("Zealot" in w and "someEffect" in w for w in warnings)


def test_an_error_reply_keeps_the_last_answer(fresh_cache, monkeypatch):
    class Reply:
        ok, status_code = False, 403

        def json(self):
            return {"message": "API rate limit exceeded"}

        def raise_for_status(self):
            raise OSError("403 rate limited")

    import requests
    monkeypatch.setattr(drift, "_fetch_tag", _real_fetch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: Reply())
    fresh_cache.write_text(json.dumps({drift.PYFA_REPO: {"tag": "v2.70.0", "checked": 0}}))
    assert drift.latest_release(drift.PYFA_REPO) == "v2.70.0"
    assert json.loads(fresh_cache.read_text())[drift.PYFA_REPO]["tag"] == "v2.70.0"


def test_a_repo_without_releases_is_none(monkeypatch):
    class Reply:
        ok, status_code = False, 404

        def raise_for_status(self):
            raise AssertionError("a 404 is an answer: the repo has no release")

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: Reply())
    assert _real_fetch(drift.OWN_REPO) is None


_real_fetch = drift._fetch_tag  # captured at import, before the autouse offline patch


def test_a_release_of_the_version_we_run_is_not_newer():
    assert not drift._newer("v0.1.1+pyfa2.70.0", "0.1.1")
    assert drift._newer("v0.1.2+pyfa2.70.0", "0.1.1")
    assert drift._newer("v2.70.0", "v2.69.0")
