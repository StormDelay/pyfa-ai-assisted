import subprocess
import sys
from pathlib import Path

import pytest

from pyfa_mcp import server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _server_uses_test_dir(booted):
    server._data_dir = booted


def test_tools_return_data(booted, zealot_eft, no_fits_left):
    result = server.evaluate_fit(zealot_eft)
    assert result["fit"] == "Test Zealot"
    assert server.status()["pyfa_version"] == "v2.69.0"
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
    proc = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "mcp_smoke.py"),
         sys.executable, "-m", "pyfa_mcp", "--data-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=300, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
