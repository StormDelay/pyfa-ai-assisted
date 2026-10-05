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

from pyfa_mcp import (catalog, conditions, drift, eft, eosboot, evaluate, graphs, guide, pool,
                      prices, pyfadata, register, search, store)

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
- Before building, optimizing or judging a fit, settle what it is for; ask
  the user when that isn't clear. For fleet fits and doctrines call
  fitting_guide with the role and whatever the user said about tank layer,
  space and fleet size, say which of those you assumed, and evaluate and
  optimize under its `suggested` conditions and constraints. A fit that
  maximizes one stat under default conditions is a starting point, not a
  recommendation.
- Every result has `applied` (what the numbers assume) and `warnings`.
  Tell the user about warnings, and mention the assumptions that matter.
  evaluate_fit and compare_fits also give `notes`: likely fitting mistakes
  (e.g. a propmod too small for the hull, a burst the hull doesn't bonus).
  Fix them, or tell the user why the fit deliberately keeps one.
  evaluate_fit's `hull_bonuses` lists what the hull is built for: fit to it.
- Use compare_fits to evaluate many candidate fits in one call.
- Call status() if numbers look wrong; relay any warning it reports.
- export_to_pyfa writes into the user's own Pyfa. Call it only when the
  user explicitly asks for that; otherwise give them the EFT text.
- For a best / max / min / optimal fit, or "what affects X": call
  find_modifiers (everything that can move the stat: every slot, implant,
  booster, burst, phenomena and environment), then optimize_fit (searches
  whole fits); audit a hand-built fit with marginal_swaps. Never choose
  candidates from memory or search_items alone.
- Those three leave Officer and Deadspace items out unless meta includes
  them (meta=["all"]), and Serenity-only, character-age-limited and
  expiring items unless availability="all"; tell the user which you used.
- For a best/max/min fit, set optimize_fit's allow.command and
  allow.phenomena unless the user fixed the fleet: it then picks the bursts
  and phenomena from the game data.
- optimize_fit's default budget lets even a whole fit (implants, boosters,
  heat, fleet) finish, which can take a few minutes. When an approximate
  answer is enough (a quick look, a first pass, comparing ideas), pass a
  smaller budget such as {"seconds": 30}: the result is then the best found
  so far. Whenever search.converged is false, tell the user the answer may
  not be the best and offer to search longer.
- Item and fit prices (`price`, `price.total`, marginal_swaps `isk_delta`,
  optimize_fit `price_total`) are estimates: fuzzwork's The Forge sell
  price, roughly Jita sell, as old as `price_source` says. Weigh cost when
  you recommend Faction, Deadspace or Officer items over Tech II, and say
  what the upgrade costs. null means unknown, not free; a `partial` total
  leaves out its `unpriced` items: quote it as "at least X, without ...".
  Call refresh_prices only when the user asks for fresh prices.
- The game data may be newer than your training. Find ships and items
  with the tools (list_ships can_fit/bonus, find_modifiers, whats_new),
  never from memory. The same goes for what fits with what: which charges
  a module takes is in item_info charges, and evaluate_fit's validity says
  whether a fit works.
- Walk conditions_format()["beyond_the_fit"] (pod, drugs, links, phenomena,
  projected, environment, heat, mode) and tell the user which of those you
  assumed, set, or left out.
"""

app = MCPServer("pyfa", instructions=INSTRUCTIONS)

_eos_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eos")
_data_dir: Path | None = None
_boot_error: str | None = None
_booted = False

_USER_ERRORS = (eft.EftError, conditions.ConditionsError, store.StoreError,
                catalog.CatalogError, graphs.GraphError, pyfadata.PyfaDataError, pool.PoolError, ValueError)


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
    prices.ensure_fresh()  # background download if prices.json is missing or stale


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
    name, group, category, meta, slot (high/mid/low/rig/subsystem, "implant 7",
    "booster 5"), cpu, powergrid, and `limits` when most pilots cannot use it
    (Serenity only, character age, expiry).
    Matches names only. To find items by what they do (e.g. everything that adds
    shield HP), use find_modifiers."""
    return catalog.search_items(query, category, meta, limit)


@app.tool()
@_tool
def list_ships(group: str | None = None, race: str | None = None,
               can_fit: str | None = None, bonus: str | None = None) -> list:
    """Ships with slot, hardpoint and drone layouts. group: e.g. Battleship,
    Heavy Assault Cruiser, Carrier. race: amarr, caldari, gallente, minmatar, ...
    can_fit: an item or item group name ("Command Burst"): only hulls that can fit
    it (T3 cruisers need subsystems for their slots and don't match).
    bonus: words that must all appear in one trait line ("Shield Command burst
    strength"); rows then carry the matching `bonuses` with their value at All V,
    sorted strongest first. Use these to find hulls rather than recalling them:
    the game data may be newer than your training."""
    return catalog.list_ships(group, race, can_fit, bonus)


@app.tool()
@_tool
def item_info(name: str) -> dict:
    """All attributes and the trait/bonus text of one item (ship, module, charge...),
    and for a module that takes charges, every charge it accepts (`charges`)."""
    return catalog.item_info(name)


@app.tool()
@_tool
def whats_new(category: str | None = None, limit: int = 30) -> dict:
    """The newest items in the game data, newest first: ships, modules, implants,
    charges, drones, fighters, subsystems (category picks one). The data has no
    dates, so this orders by type ID (higher = added later). Use it when an answer
    depends on what exists: the game data may be newer than your training."""
    return catalog.whats_new(category, limit)


# --- evaluation --------------------------------------------------------------

@app.tool()
@_tool
def evaluate_fit(fit: str, conditions: dict | None = None) -> dict:
    """Full stats of one fit (EFT text or stored fit name/id) under conditions:
    validity (cpu/pg/calibration/slots/hardpoints), tank (hp, ehp, resists,
    repair), offense (dps/volley), capacitor, navigation, targeting, drones,
    plus `applied`, `warnings`, `notes` (likely fitting mistakes) and
    `hull_bonuses` (the hull's trait lines: fit to them). Modules that do not
    fit are left out and listed as validity problems.
    To check whether a fit can be improved, use marginal_swaps."""
    return evaluate.evaluate(fit, conditions)


@app.tool()
@_tool
def compare_fits(fits: list[str], conditions: dict | None = None,
                 stats: list[str] | None = None, variants: list[dict] | None = None) -> dict:
    """Evaluate many fits under the same conditions into one table. `stats` picks
    columns by dotted key from evaluate_fit's output (e.g. "tank.ehp.total",
    "offense.dps.total", "targeting.lock_range_m"); omitted = a standard set.
    variants: a list of partial conditions, each merged over `conditions` (top-level
    keys replace), giving one row per fit and variant: e.g. one fit under several
    booster fits, damage profiles or phenomena in a single call.
    A fit that fails gets an `error` in its row; the others still compute.
    To check whether a fit can be improved, use marginal_swaps."""
    return evaluate.compare(fits, conditions, stats, variants)


# --- search ------------------------------------------------------------------

@app.tool()
@_tool
def find_modifiers(fit: str, stats: list[str], sources: list[str] | None = None,
                   meta: list[str] | None = None, conditions: dict | None = None,
                   expand: list[str] | None = None, availability: str | None = None,
                   verbose: bool = False) -> dict:
    """What can change a stat on this hull. Use it before saying what is best or
    max/min/optimal, and whenever the user asks what affects or what else could
    raise or lower a stat (EHP, DPS, lock range, align...). Measures every legal
    module, rig, subsystem, charge/script, implant and implant set, booster,
    command burst, phenomena generator, projected module and environment effect
    on `fit` (hull name, EFT or stored fit) under `conditions`, so it finds what
    you would not think to search for. One row per item group: best variant, a
    Tech II/Faction reference, delta range; expand=["Group"] or ["*"] lists every
    variant. Officer and Deadspace items are left out unless meta includes them
    (meta=["all"]).
    Serenity-only, character-age-limited and expiring items are left out unless
    availability="all" (default "tq"); rows show an item's `limits`.
    stats: evaluate_fit keys (tank.ehp.total) or ship.<attribute>;
    prefix "-" when lower is better ("-navigation.align_time_s"): it orders the
    rows and decides what counts as a drawback (the first stat ranks).
    sources: module, rig, subsystem, charge, implant, booster, command_burst,
    phenomena, projected, environment (default all). Then call optimize_fit.
    Output is compact (excluded and pruned items as counts by reason);
    verbose=true lists every item."""
    return search.find_modifiers(fit, stats, sources, meta, conditions, expand,
                                 availability=availability, verbose=verbose)


@app.tool()
@_tool
def marginal_swaps(fit: str, objective: str, conditions: dict | None = None,
                   meta: list[str] | None = None, include_empty_slots: bool = True,
                   top_n: int = 10, availability: str | None = None,
                   verbose: bool = False) -> dict:
    """Is there any single change that makes this fit better? Use it to audit a
    hand-built fit before recommending it as the best or max for a stat. Tries
    every module, rig, charge, implant and booster that fits each slot, every
    empty slot and every removal; returns the valid ones sorted by gain
    (objective: a stat key; prefix "-" to minimize, e.g. "-navigation.align_time_s").
    The top swap is confirmed with evaluate_fit. Officer and Deadspace items are
    left out unless meta includes them (meta=["all"]).
    Serenity-only, character-age-limited and expiring items are left out unless
    availability="all" (default "tq"); rows show an item's `limits`.
    Output is compact (excluded and pruned items as counts by reason);
    verbose=true lists every item."""
    return search.marginal_swaps(fit, objective, conditions, meta, include_empty_slots,
                                 top_n, availability=availability, verbose=verbose)


@app.tool()
@_tool
def optimize_fit(fit: str, objective: str, conditions: dict | None = None,
                 allow: dict | None = None, meta: list[str] | None = None,
                 locked: str | None = None, constraints: list[dict] | None = None,
                 top_k: int = 5, budget: dict | None = None,
                 availability: str | None = None, verbose: bool = False) -> dict:
    """Search for the best fit for a stat. Use it whenever the user asks for the
    best, highest, max, min-max or optimal fit, or before recommending a module
    choice. It builds its candidates from every item that affects the stat, so it
    won't miss modules you didn't think of. fit: hull name or EFT (a start
    point). objective: stat key, "-" prefix to minimize. allow: {slots: [high,
    mid, low, rig], implants: bool, boosters: bool, module_states: [active,
    overheated], command: bool, phenomena: bool} (default: all racks, no
    implants/boosters, no overheat, no fleet search). command/phenomena: the
    search also picks the command bursts (each charge from its strongest hull,
    module and mindlink in the game data) and the phenomena generator, and
    returns them under `fleet` with runner-up hulls and booster fits; set them
    for best/max/min questions unless the user fixed the fleet, and leave
    conditions.command empty then. Otherwise command bursts, phenomena,
    projected and environment stay as conditions set them.
    locked: EFT lines that must stay. constraints: [{"stat", "eq"|"lte"|"gte":
    value}]. budget: {evaluations, seconds} (default 400000, 300: enough for a
    whole fit to finish, which can take minutes; pass less, e.g. {"seconds":
    30}, when an approximate answer is enough). Officer and
    Deadspace items are left out unless meta includes them (meta=["all"]).
    Serenity-only, character-age-limited and expiring items are left out unless
    availability="all" (default "tq"); rows show an item's `limits`.
    Every returned fit is computed by evaluate_fit; its `conditions`
    reproduce it there (module states included: EFT has no heat), and with heat
    allowed `objective_cold` is the same fit unheated. best[0]["polish"] lists
    the single swaps a last pass made. `search.converged` says whether the search
    finished inside the budget. Output is compact: counts for considered, pruned
    and excluded items, and best[1:] as a diff against best[0]; verbose=true
    lists everything whole."""
    return search.optimize_fit(fit, objective, conditions, allow, meta, locked,
                               constraints, top_k, budget, availability=availability,
                               verbose=verbose)


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
def fitting_guide(role: str | None = None, tank: str | None = None,
                  space: str | None = None, pilots: int | None = None) -> dict:
    """Fleet fitting principles, each with its reason. Call it before building,
    optimizing or judging a fleet fit or doctrine. No role: the general
    principles, the roles (fleet_doctrine, fleet_mainline, fleet_logistics,
    fleet_command, fleet_support) and the axes. With a role, pass what the user
    said: tank (armor, shield), space (nullsec, lowsec, wormhole), pilots (fleet
    size). Returns the role's principles for that fleet, `suggested` conditions
    and constraints for evaluate_fit / compare_fits / optimize_fit, and `unset`:
    the axes not given that would change the advice (ask the user, or say what
    you assumed). Principles are defaults with reasons: a fit may break one
    deliberately, and should say why. The user can edit their own copy (see
    `customize` in the no-role listing)."""
    try:
        return guide.guide(role, tank, space, pilots,
                           _data_dir or eosboot.default_data_dir())
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


@app.tool()
@_tool
def status() -> dict:
    """Versions (pyfa-mcp's Pyfa, the user's Pyfa, latest releases), whether the user's
    Pyfa data was found, and effects Pyfa does not compute. Relay every warning."""
    return {"pyfa_version": eosboot.pyfa_version(),
            "game_client_build": catalog.client_build(),
            "data_dir": str(eosboot.boot(_data_dir)),
            "search_workers": pool.describe(),
            "prices": prices.price_info(),
            **drift.report()}


@app.tool()
@_tool
def refresh_prices() -> dict:
    """Download fresh market prices now (fuzzwork, The Forge sell). Call it only
    when the user asks for fresh prices: they otherwise refresh in the
    background every 3 days. Waits up to about 30 seconds."""
    return prices.refresh()


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
    parser.add_argument("--workers", type=int, default=None,
                        help="processes for find_modifiers/optimize_fit/marginal_swaps "
                             "(default: cores - 2, at most 12; 0 = none)")
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--register", metavar="CLIENT",
                       help="add pyfa-mcp to an MCP client's config: "
                            + ", ".join(register.clients()) + ", or auto (every one installed)")
    setup.add_argument("--unregister", metavar="CLIENT",
                       help="remove pyfa-mcp from a client's config, or all")
    setup.add_argument("--print-config", nargs="?", const="json", choices=("json", "toml"),
                       help="print the config entry for a client not in the list")
    args = parser.parse_args(argv)
    if args.register or args.unregister or args.print_config:
        sys.exit(register.run(args.register, args.unregister, args.print_config))
    _data_dir = args.data_dir
    pyfadata.set_dir(args.pyfa_dir)
    pool.configure(args.workers)
    app.run()  # stdio
