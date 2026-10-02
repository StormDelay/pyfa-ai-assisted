"""pyfa-mcp: Pyfa's fitting engine over MCP (stdio).

Every tool body runs on one dedicated thread, one call at a time: eos keeps
one SQLAlchemy session per process and Pyfa's services are singletons, none
of them safe to use from two calls at once. Pyfa is booted on the first
tool call and kept for the life of the process.
"""
from __future__ import annotations

import argparse
import contextlib
import functools
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from pyfa_mcp import catalog, conditions, eft, eosboot, evaluate, graphs, pyfadata, store

INSTRUCTIONS = """\
pyfa-mcp computes EVE Online fits with Pyfa's own engine.

- Fits are EFT text (as Pyfa or the game exports them), the name/id of a
  fit saved with save_fit, or "pyfa:<id or name>" for a fit in the user's
  own Pyfa (list_fits(source="pyfa"); read-only). To change a fit, edit the
  EFT and evaluate again.
- evaluate_fit / compare_fits / fit_graph take `conditions`. Defaults: All 5
  skills, uniform incoming damage, no target profile, modules as in the EFT
  (active, /OFFLINE honoured), Pyfa's default spool (full), no drug side
  effects, no command bursts, nothing projected. Set the conditions that
  matter for the user's question; call conditions_format() for the schema.
- Every result has `applied` (what the numbers assume) and `warnings`.
  Tell the user about warnings, and mention the assumptions that matter.
- Use compare_fits to evaluate many candidate fits in one call.
- Call status() if numbers look wrong; relay any warning it reports.
- export_to_pyfa writes into the user's own Pyfa. Call it only when the
  user explicitly asks for that; otherwise give them the EFT text.
"""

app = MCPServer("pyfa", instructions=INSTRUCTIONS)

_eos_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eos")
_data_dir: Path | None = None
_boot_error: str | None = None
_booted = False

_USER_ERRORS = (eft.EftError, conditions.ConditionsError, store.StoreError,
                catalog.CatalogError, graphs.GraphError, pyfadata.PyfaDataError, ValueError)


def _ensure_booted() -> None:
    global _booted, _boot_error
    if _booted:
        return
    if _boot_error is not None:
        raise ToolError(_boot_error)
    try:
        eosboot.boot(_data_dir)
    except Exception as exc:
        _boot_error = f"pyfa-mcp could not start Pyfa: {exc}"
        raise ToolError(_boot_error) from exc
    _booted = True


def _tool(fn):
    """Boot, run on the eos thread, keep stdout clean, explain failures."""
    @functools.wraps(fn)
    def run(*args, **kwargs):
        def body():
            with contextlib.redirect_stdout(sys.stderr):
                _ensure_booted()
                try:
                    return fn(*args, **kwargs)
                except ToolError:
                    raise
                except _USER_ERRORS as exc:
                    raise ToolError(str(exc)) from exc
                except Exception as exc:
                    raise ToolError(f"{type(exc).__name__}: {exc}") from exc
        return _eos_thread.submit(body).result()
    return run


# --- catalog -----------------------------------------------------------------

@app.tool()
@_tool
def search_items(query: str, category: str | None = None, meta: str | None = None,
                 limit: int = 25) -> list:
    """Find items by name. category: e.g. Module, Drone, Charge, Ship, Implant.
    meta: Tech I, Tech II, Faction, Deadspace, Officer, Storyline, ... Returns
    name, group, category, meta, slot (high/mid/low/rig/subsystem), cpu, powergrid."""
    return catalog.search_items(query, category, meta, limit)


@app.tool()
@_tool
def list_ships(group: str | None = None, race: str | None = None) -> list:
    """Ships with slot, hardpoint and drone layouts. group: e.g. Battleship,
    Heavy Assault Cruiser, Carrier. race: amarr, caldari, gallente, minmatar, ..."""
    return catalog.list_ships(group, race)


@app.tool()
@_tool
def item_info(name: str) -> dict:
    """All attributes and the trait/bonus text of one item (ship, module, charge...)."""
    return catalog.item_info(name)


# --- evaluation --------------------------------------------------------------

@app.tool()
@_tool
def evaluate_fit(fit: str, conditions: dict | None = None) -> dict:
    """Full stats of one fit (EFT text or stored fit name/id) under conditions:
    validity (cpu/pg/calibration/slots/hardpoints), tank (hp, ehp, resists,
    repair), offense (dps/volley), capacitor, navigation, targeting, drones,
    plus `applied` and `warnings`. Modules that do not fit are left out and
    listed as validity problems."""
    return evaluate.evaluate(fit, conditions)


@app.tool()
@_tool
def compare_fits(fits: list[str], conditions: dict | None = None,
                 stats: list[str] | None = None) -> dict:
    """Evaluate many fits under the same conditions into one table. `stats` picks
    columns by dotted key from evaluate_fit's output (e.g. "tank.ehp.total",
    "offense.dps.total", "targeting.lock_range_m"); omitted = a standard set.
    A fit that fails gets an `error` in its row; the others still compute."""
    return evaluate.compare(fits, conditions, stats)


@app.tool()
@_tool
def fit_graph(fit: str, graph: str, x: str, y: str, x_range: list[float],
              inputs: dict | None = None, conditions: dict | None = None) -> dict:
    """One of Pyfa's graphs as points. graph: damage, lock_time, mobility,
    warp_time, capacitor, shield_regen, ewar, remote_reps. x/y: an axis handle,
    optionally "handle:unit"; `inputs` overrides the other inputs (see
    graph_options()). For graphs with a target, conditions.target is the
    target (default: Pyfa's ideal target)."""
    return graphs.fit_graph(fit, graph, x, y, x_range, inputs, conditions)


@app.tool()
@_tool
def graph_options() -> dict:
    """Axes, extra inputs and their defaults for every graph fit_graph can draw."""
    return graphs.describe()


@app.tool()
@_tool
def conditions_format() -> dict:
    """The `conditions` schema with examples, and the names of Pyfa's built-in
    damage and target profiles."""
    return conditions.describe()


@app.tool()
@_tool
def status() -> dict:
    """Versions and health of the engine. Relay any warnings to the user."""
    import eos.db
    with eos.db.gamedata_engine.connect() as connection:
        meta = dict(connection.exec_driver_sql(
            "SELECT field_name, field_value FROM metadata").fetchall())
    return {"pyfa_version": eosboot.pyfa_version(),
            "game_client_build": meta.get("client_build"),
            "data_dir": str(eosboot.boot(_data_dir)),
            "warnings": []}


# --- storage -----------------------------------------------------------------

@app.tool()
@_tool
def save_fit(fit: str, name: str) -> dict:
    """Keep a fit (EFT or stored name/id) under a unique name in the server's database."""
    return store.save_fit(fit, name)


@app.tool()
@_tool
def list_fits(ship: str | None = None, source: str = "server") -> list:
    """Fits stored in the server's database (source="server") or in the user's
    own Pyfa (source="pyfa", read-only, ids like "pyfa:12"), optionally for one ship."""
    return store.list_fits(ship, source)


@app.tool()
@_tool
def get_fit(fit: str) -> dict:
    """A stored fit (name or id) or a Pyfa fit ("pyfa:<id or name>") as EFT text."""
    return store.get_fit(fit)


@app.tool()
@_tool
def delete_fit(fit: str) -> dict:
    """Delete a fit stored in the server's database, by name or id. Pyfa fits are read-only."""
    return store.delete_fit(fit)


@app.tool()
@_tool
def export_to_pyfa(fit: str, name: str | None = None) -> dict:
    """Write a fit into the user's own Pyfa as a NEW fit. Only when the user explicitly
    asks. Refuses while Pyfa runs; backs up Pyfa's database first; never overwrites
    (a taken name gets " (2)"). Returns the new fit's pyfa: id and the backup path."""
    return store.export_to_pyfa(fit, name)


def main(argv: list[str] | None = None) -> None:
    global _data_dir
    parser = argparse.ArgumentParser(prog="pyfa-mcp")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="where the server keeps its saveddata.db (default ~/.pyfa-mcp)")
    parser.add_argument("--pyfa-dir", type=Path, default=None,
                        help="the user's Pyfa data dir (default ~/.pyfa); read, and "
                             "written only by export_to_pyfa")
    args = parser.parse_args(argv)
    _data_dir = args.data_dir
    pyfadata.set_dir(args.pyfa_dir)
    app.run()  # stdio
