import subprocess
import sys
from pathlib import Path

import pytest

from pyfa_mcp import eosboot, server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _server_uses_test_dir(booted):
    server._data_dir = booted


def test_tools_return_data(booted, zealot_eft, no_fits_left):
    result = server.evaluate_fit(zealot_eft)
    assert result["fit"] == "Test Zealot"
    assert server.status()["pyfa_version"] == eosboot.pyfa_version()
    assert server.status()["pyfa_install"]["found"] is False
    assert "fields" in server.conditions_format()


def test_errors_become_tool_errors(booted):
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="Heat Sinkk II"):
        server.evaluate_fit("[Zealot, x]\nHeat Sinkk II\n")
    with pytest.raises(ToolError, match="unknown condition"):
        server.evaluate_fit("[Zealot, x]\n", conditions={"bogus": 1})


def test_tool_bodies_keep_stdout_clean(booted, zealot_eft, capsys, no_fits_left):
    server.evaluate_fit(zealot_eft)
    server.search_items("sensor booster")
    assert capsys.readouterr().out == ""


def test_smoke_over_stdio(booted, tmp_path):
    import json
    import time
    from pyfa_mcp import drift
    data = tmp_path / "data"
    data.mkdir()
    fresh = {"tag": None, "checked": time.time()}  # status() then never asks GitHub
    (data / "release-check.json").write_text(
        json.dumps({drift.PYFA_REPO: fresh, drift.OWN_REPO: fresh}), encoding="utf-8")
    (data / "prices.json").write_text(  # fresh: the server never downloads prices
        json.dumps({"source": "fuzzwork", "fetched_at": time.time(), "prices": {}}),
        encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "mcp_smoke.py"),
         sys.executable, "-m", "pyfa_mcp", "--data-dir", str(data),
         "--pyfa-dir", str(tmp_path / "no-pyfa"), "--workers", "2"],
        capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_pyfa_fits_without_pyfa(booted):
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="no Pyfa install"):
        server.list_fits(source="pyfa")


def test_pyfa_fits_over_the_server(pyfa_home):
    assert [f["name"] for f in server.list_fits(source="pyfa")] == ["Home Zealot"]


def test_main_print_config_exits_without_serving(capsys):
    import json
    with pytest.raises(SystemExit) as exit_info:
        server.main(["--print-config"])
    assert exit_info.value.code == 0
    assert "pyfa" in json.loads(capsys.readouterr().out)["mcpServers"]


def test_search_tools_and_redirects(booted, no_fits_left):
    result = server.find_modifiers("Rifter", ["tank.ehp.total"], sources=["rig"])
    assert result["groups"]
    assert "use find_modifiers" in server.search_items.__doc__
    assert "use marginal_swaps" in server.evaluate_fit.__doc__
    assert "use marginal_swaps" in server.compare_fits.__doc__
    for tool in (server.find_modifiers, server.optimize_fit, server.marginal_swaps):
        assert "best" in tool.__doc__ and "Officer" in tool.__doc__
    assert "find_modifiers" in server.INSTRUCTIONS and "beyond_the_fit" in server.INSTRUCTIONS
    assert server.status()["search_workers"]["workers"] >= 0
    assert "never from memory" in server.INSTRUCTIONS
    assert "allow.command" in server.INSTRUCTIONS and 'availability="all"' in server.INSTRUCTIONS
    assert server.whats_new(category="Ship", limit=3)["items"]
    assert "approximate" in server.INSTRUCTIONS and '{"seconds": 30}' in server.INSTRUCTIONS
    assert "approximate" in server.optimize_fit.__doc__


def test_search_input_errors(booted):
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match="unknown stat 'tank.ehp.totl'"):
        server.find_modifiers("Rifter", ["tank.ehp.totl"])
    with pytest.raises(ToolError, match="sources: unknown 'modules'"):
        server.find_modifiers("Rifter", ["tank.ehp.total"], sources=["modules"])
    with pytest.raises(ToolError, match="allow: unknown key 'slot'"):
        server.optimize_fit("Rifter", "tank.ehp.total", allow={"slot": ["low"]})
    with pytest.raises(ToolError, match="constraints: each is"):
        server.optimize_fit("Rifter", "tank.ehp.total", constraints=[{"stat": "x"}])
    with pytest.raises(ToolError, match="no stored fit named 'Rifterr'"):
        server.marginal_swaps("Rifterr", "tank.ehp.total")


def test_fitting_guide_tool_needs_no_boot(monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    def no_boot(*a, **k):
        raise AssertionError("fitting_guide booted Pyfa")

    monkeypatch.setattr(server, "_ensure_booted", no_boot)
    assert "fleet_mainline" in server.fitting_guide()["roles"]
    assert server.fitting_guide("fleet_mainline", tank="shield")["role"] == "fleet_mainline"
    with pytest.raises(ToolError, match="unknown role"):
        server.fitting_guide("fleet_dps")


def test_instructions_point_at_the_guide_and_notes():
    assert "fitting_guide" in server.INSTRUCTIONS
    assert "starting point" in server.INSTRUCTIONS
    assert "`notes`" in server.INSTRUCTIONS


def test_fitting_guide_reads_the_servers_data_dir(tmp_path, monkeypatch):
    (tmp_path / "fitting_guide.yaml").write_text(
        "axes: {tank: {armor: a}, space: {nullsec: n}, pilots: p}\ngeneral: []\n"
        "roles: {fleet_mine: {summary: mine, principles: []}}\n", encoding="utf-8")
    monkeypatch.setattr(server, "_data_dir", tmp_path)
    assert set(server.fitting_guide()["roles"]) == {"fleet_mine"}


def test_instructions_say_compatibility_comes_from_the_tools():
    assert "item_info charges" in server.INSTRUCTIONS


def test_status_and_refresh_prices(booted, seed_prices):
    seed_prices({"Zealot": 1e8})
    assert server.status()["prices"]["state"] == "ok"
    out = server.refresh_prices()
    assert out["refreshed"] is False and "minutes old" in out["reason"]


def test_instructions_explain_prices():
    text = server.INSTRUCTIONS
    assert "refresh_prices" in text and "partial" in text and "null" in text


def test_guide_no_longer_says_the_tools_cant_see_price():
    from pyfa_mcp import guide
    assert "can't see price" not in guide.GUIDE.read_text(encoding="utf-8")
