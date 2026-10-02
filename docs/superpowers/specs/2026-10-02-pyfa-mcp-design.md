# pyfa-mcp — design

Date: 2026-10-02
Status: approved in brainstorming, pending written-spec review

## Purpose

An MCP server that gives an LLM client the full fitting and simulation
ability of Pyfa, so a user can ask things like:

- *Targeted questions* — "how many sensor boosters does a carrier need to
  lock a Fortizar 7000 km away?"
- *Exploration* — "using standard modules, which T1 gun battleship has the
  best omni tank?"
- *Import* (stretch goal) — "import the standard Zealot fit the Initiative
  uses."

Users: the author and their corp/alliance mates, running a packaged Windows
build locally with any MCP client.

Success: each of the three example questions is answered with numbers that
match the Pyfa GUI at the pinned version, and every number states the
conditions it was computed under.

## Principles

- The LLM reasons; the server computes. No question-specific tools ("how
  many sebos"); the LLM composes primitive tools.
- Stateless evaluation over EFT text. Every `fit` argument accepts EFT text
  or the name/id of a stored fit. Editing a fit = the LLM rewrites EFT and
  re-evaluates. No stateful "add module to fit #3" tools.
- Nothing silent: every result echoes the conditions it applied (defaults
  included), and unknown names or impossible states are errors with
  close-match suggestions.
- Never write the user's Pyfa data unless explicitly asked.

## Scope

### v1

- Catalog, evaluation, graph, and fit-storage tools (below).
- `conditions` on every evaluation: damage/target profile, module states,
  spool, drug side effects, command bursts, projected effects.
- Character fixed to "All 5" (the `character` field exists, accepts only
  that value).
- Packaged Windows build with multi-client registration.
- Automated Pyfa-tracking release pipeline.

### Later phases (not in v1)

1. Characters: use and edit characters from the user's Pyfa (Pyfa's built-in
   "All 0"/"All 5" plus imported ones). No ESI/SSO of our own.
2. Permutation hints: the server suggests module/rig/charge permutations the
   LLM should try so exploration doesn't miss candidates; later a narrow
   optimizer (e.g. "best mid+low tank for this hull").
3. zKillboard import (stretch): fetch a group's losses of a hull (default:
   last 50 within 90 days, overridable), resolve items via ESI, cluster by
   **unordered module multiset** (slot positions ignored; cargo and charges
   excluded from identity), return the top variants with counts and latest
   loss date.

## Architecture

```
MCP client ──stdio──> pyfa_mcp.server (mcp 2.x)
                         │  every tool body runs on one dedicated eos thread
                         ▼
                      pyfa_mcp.* modules ──> vendored Pyfa (eos + selected service/graphs)
                         │
        ┌────────────────┼──────────────────────┐
     eve.db          server saveddata.db     user's Pyfa saveddata.db
   (bundled, RO)    (~/.pyfa-mcp, RW)       (~/.pyfa, snapshot RO; written
                                              only by export_to_pyfa)
```

### Boot (`eosboot.py`)

Copied from dread-sim and extended. Pyfa is a git submodule at
`vendor/Pyfa`, pinned to a release tag. Boot happens once, on the first tool
call that needs it.

- wx is replaced by a stub module providing `Colour`, `GetTranslation`
  (identity), `CallAfter` (call synchronously), and `CommandProcessor`
  (no-op undo stack). Further attributes are added only as the import check
  demands them.
- `config.defPaths()` must never re-point eos at `~/.pyfa`; boot sets
  `config.savePath`/`saveDB` and eos connection strings explicitly (as
  dread-sim does) and a test asserts the engine URL after importing every
  reused service module.
- **Reuse rule (approach C):** use Pyfa's own `service.port` (EFT import and
  export), `service.market` (search), `service.fit` and the `graphs.data`
  getters wherever they import and run cleanly under the stub. The first
  implementation step is a check that boots, round-trips an EFT fit through
  `Port`, runs a `Market` search and computes one lock-time graph point. Any
  module that fails gets a small replacement in this project, written
  directly against `eos`.

### Data

| DB | Location | Access |
|---|---|---|
| Game data `eve.db` | bundled, generated from the pinned Pyfa commit by `db_update.py` | read-only |
| Server fits | `~/.pyfa-mcp/saveddata.db`, Pyfa schema, created on first run | read-write; eos's main session |
| User's Pyfa | `~/.pyfa/saveddata.db` if present | read via a snapshot copy refreshed when mtime changes, through a second SQLAlchemy session over the same eos mappers; written only by `export_to_pyfa` |

Code and game data always come from the same Pyfa commit. Pyfa need not be
installed; without it, `list_fits(source="pyfa")`, `export_to_pyfa` and the
user's custom damage/target profiles return "no Pyfa install found".

### Drift checks (run at boot, reported by `status()`)

1. **Version:** installed Pyfa `version.yml` (if any) and latest
   non-prerelease Pyfa GitHub release (fetched at most daily, skipped quietly
   offline) vs the pinned version. If the user's Pyfa is behind the latest
   release, the warning tells the LLM to advise updating Pyfa. If a newer
   pyfa-mcp release exists, `status()` says so.
2. **Effect coverage (safety net):** effect IDs attached to published items
   in `eve.db` with no `Effect{ID}` handler in eos, minus the set recorded at
   build time as intentionally unhandled. Any remaining hit lists the
   affected items; evaluations touching them carry a warning.

Server `instructions` tell the LLM to relay `status()` warnings and
per-result warnings to the user.

## MCP tools

### Catalog

- `search_items(query, category?, meta?)` → name, typeID, group, slot,
  CPU/PG, meta group (T1, T2, Faction, Deadspace, Officer, …). `meta` is how
  "standard modules" is expressed.
- `list_ships(group?, race?)` → hulls with slot/hardpoint/drone layout and
  trait bonuses.
- `item_info(name)` → full attributes and traits.

### Evaluation

- `evaluate_fit(fit, conditions?)` → `validity` (CPU/PG/calibration, slot
  overflow, max-group-fitted/restrictions; an invalid fit is still
  evaluated), EHP and resists per layer and omni, DPS/volley, capacitor
  stability, speed, align, signature, lock range, scan resolution, max
  targets, drones; plus `applied` and `warnings`.
- `compare_fits(fits[], conditions?, stats?)` → one compact table across
  many fits; the workhorse for exploration.
- `fit_graph(fit, graph, x, params, conditions?)` → data points for Pyfa's
  graphs: lock time, damage application, mobility, warp time, capacitor,
  shield regen, ewar, remote reps.
- `conditions_format()` → full `conditions` schema, worked examples, and the
  names of available damage/target profiles.
- `status()` → versions, drift-check results, Pyfa install found or not.

### Storage

- `save_fit(fit, name)`, `list_fits(source="server"|"pyfa")`,
  `get_fit(id)` (→ EFT), `delete_fit(id)`. Pyfa source is read-only.
- `export_to_pyfa(fit, name?)` — only on explicit user request. Refuses if a
  `pyfa` process is running; refuses if the user DB's migration version
  differs from the pinned eos's (error says to export EFT instead); backs up
  `saveddata.db` with a timestamp; inserts a new fit only, never overwrites;
  name clash → ` (2)` suffix.

### `conditions`

EFT already carries implants, drugs, charges, drones/fighters with counts,
`/OFFLINE` modules and mutated modules. `conditions` covers the rest:

```json
{
  "character": "All 5",
  "damage_profile": "uniform" | "<profile name>" | {"em":0,"thermal":0,"kinetic":0,"explosive":0},
  "target": "<target profile name>" | {"resists":{...}, "signature":0, "speed":0, "radius":0},
  "module_states": [{"module":"Large Shield Booster II", "state":"online|active|overheated|offline", "count":1}],
  "spool": "min" | "max" | "average" | 0.5,
  "drug_side_effects": [{"drug":"<booster name>", "effect":"<side effect>"}],
  "command": [{"fit":"<EFT or stored name>"}],
  "projected": [{"item":"Stasis Webifier II", "count":2, "state":"active"},
                {"fit":"<EFT or stored name>", "count":1}]
}
```

Defaults: modules active (not overheated), minimum spool, uniform damage,
no target profile, no side effects, no boosts, nothing projected. Every
result's `applied` block lists each condition and marks defaults, e.g.
`"spool": "min (default)"`.

Errors (never silently ignored): unknown item/profile/fit names (with
close matches), a module named in `module_states` that is not on the fit or
cannot take the state, an unknown drug side effect, a malformed profile.

## Error handling

- Tool errors are MCP tool errors carrying the reason (dread-sim's `_tool()`
  pattern); the process never dies on bad input.
- A failed boot is remembered: every later tool returns the same reason.
- An over-limit fit is an answer (`validity`), not an error.

## Packaging and registration

- PyInstaller one-folder build `pyfa-mcp/` with `pyfa-mcp.exe` (stdio
  server), bundled `eve.db` and eos source; Inno Setup installer.
- `pyfa-mcp.exe --register <client>|auto`, `--unregister <client>`,
  `--print-config [json|toml]`. Registration is table-driven (config path,
  key, format); each write is atomic, keeps other entries, and backs up the
  file first. Initial table (paths and keys verified against each client's
  docs during planning):

  | Client | Config | Shape |
  |---|---|---|
  | claude-desktop | `%APPDATA%\Claude\claude_desktop_config.json` | `mcpServers` JSON |
  | claude-code | `~/.claude.json` (user scope) | `mcpServers` JSON |
  | cursor | `~/.cursor/mcp.json` | `mcpServers` JSON |
  | windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` JSON |
  | vscode | user `mcp.json` | `servers` JSON |
  | codex | `~/.codex/config.toml` | `[mcp_servers.pyfa]` TOML |

  `auto` registers every client whose config directory exists; the
  installer exposes it as a checkbox listing detected clients.
  `--print-config` covers clients not in the table.

## Release pipeline

GitHub Actions, scheduled hourly:

1. Read the latest non-prerelease `pyfa-org/Pyfa` tag; exit if it equals the
   pin.
2. Bump the submodule, re-sync Python and dependency versions from the
   pinned Pyfa's `.python-version` and `uv.lock`, regenerate `eve.db`, run
   the suite.
3. Gate: boots, every tool runs, all reference fits evaluate without error,
   effect-coverage check clean. Reference-fit number changes do **not**
   block; they are listed in the release notes.
4. Pass → commit the bump, build, publish `vX.Y.Z+pyfa<version>`.
   Fail → open a PR with the bump and the failure output.
5. Each run calls the workflow-enable API on itself so GitHub's 60-day
   inactivity rule never disables it (last year had a 65-day Pyfa gap).

Background: over 2025-10 → 2026-10, 11 of 14 Pyfa releases added effect
handlers for player items and none changed the eve.db schema, so tracking
Pyfa releases is the normal case and must be automatic.

## Testing

- Reference fits (~10): subcap, capital, T3D modes, triglavian spool, drugs,
  projected webs, command bursts. Expected stats recorded from the Pyfa GUI
  at the pinned version. A mismatch fails CI on ordinary changes; in the
  bump pipeline it is reported, not blocking (see above).
- Unit tests: `conditions` parsing and errors, EFT round-trip, `applied`
  echo, drift checks, registration table writes, `export_to_pyfa` safeguards
  against a temp copy of `saveddata.db`.
- The engine-URL test from Boot (eos never points at `~/.pyfa`).
- MCP smoke test against the built exe in CI (dread-sim's
  `packaging/mcp_smoke.py` pattern).

## Repo

Fresh repo at `D:\claude\pyfa-ai-assisted`, GPL-3.0-or-later (it imports
Pyfa). Initial pin: Pyfa **v2.69.0**, which requires Python ≥3.12 (its
`.python-version` says 3.14), SQLAlchemy 2.0 and numpy 2 — unlike dread-sim's
v2.68 pin (Python 3.11, SQLAlchemy 1.4), so `eosboot.py` is copied but
re-verified, not trusted. The Python version and the versions of eos's
runtime dependencies are taken from the pinned Pyfa's `.python-version` and
`uv.lock`; the bump pipeline re-syncs them on every Pyfa bump, so a Pyfa
runtime change is part of the same gate as a code change. Shared code with dread-sim (`eosboot.py`, packaging) is
copied, not shared; extract a common package only if a third project needs
it or the shared part grows large.
