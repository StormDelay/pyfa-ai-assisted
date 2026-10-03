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
