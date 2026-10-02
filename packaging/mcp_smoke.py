"""Check that a command is a working pyfa-mcp server over stdio.

    python packaging/mcp_smoke.py COMMAND [ARGS...]

initialize, tools/list, then evaluate_fit, status, fit_graph and
conditions_format. Exits non-zero on anything unexpected; a stray print on
the server's stdout fails json.loads. Pass --data-dir / --pyfa-dir through to
keep it off the user's own data.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import sys

TOOLS = {"search_items", "list_ships", "item_info", "evaluate_fit", "compare_fits",
         "fit_graph", "graph_options", "conditions_format", "status", "save_fit",
         "list_fits", "get_fit", "delete_fit", "export_to_pyfa"}
FIT = "[Rifter, smoke]\n200mm AutoCannon II, EMP S\n"
# One call per subsystem a frozen build could miss an import for: eos (evaluate),
# drift (status), graphs, conditions.
CALLS = (
    ("evaluate_fit", {"fit": FIT}),
    ("status", {}),
    ("fit_graph", {"fit": FIT, "graph": "lock_time", "x": "tgtSigRad", "y": "time",
                   "x_range": [10, 1000]}),
    ("conditions_format", {}),
)


def main(command: list[str]) -> int:
    server = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              text=True, encoding="utf-8")
    ids = itertools.count(1)

    def send(message: dict) -> None:
        server.stdin.write(json.dumps(message) + "\n")
        server.stdin.flush()

    def call(method: str, params: dict) -> dict:
        send({"jsonrpc": "2.0", "id": next(ids), "method": method, "params": params})
        line = server.stdout.readline()
        if not line:
            raise SystemExit(f"server closed stdout during {method}")
        reply = json.loads(line)
        if "error" in reply:
            raise SystemExit(f"{method}: {reply['error']}")
        return reply["result"]

    try:
        call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                            "clientInfo": {"name": "smoke", "version": "0"}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        names = {t["name"] for t in call("tools/list", {})["tools"]}
        if names != TOOLS:
            raise SystemExit(f"tools mismatch: missing {TOOLS - names}, extra {names - TOOLS}")
        for name, arguments in CALLS:
            result = call("tools/call", {"name": name, "arguments": arguments})
            if result.get("isError"):
                raise SystemExit(f"{name}: {result['content']}")
        print("ok")
        return 0
    finally:
        server.stdin.close()
        server.terminate()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
