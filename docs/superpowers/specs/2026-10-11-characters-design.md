# pyfa-mcp — characters and skill editing

Date: 2026-10-11
Status: approved in brainstorming, pending written-spec review
Origin: "Later phases" item 1 of `2026-10-02-pyfa-mcp-design.md`: use and
edit characters from the user's Pyfa. No ESI/SSO of our own. Requested in
issue #12 (cyno force-recon hunting with ~2.5M SP alts: at All 5 the
suggested fit was valid and the fight won; with the real skills the fit is
over CPU, needs an untrained skill, and the fight is lost). The issue's
input shape (`base` + `skills`) and its compare-variants use are adopted.

## Purpose

Every evaluation runs at All 5 (`conditions.character` accepts nothing
else). The user wants both:

- **Real pilot numbers:** evaluate and search fits with their own, an alt's
  or a corpmate's skills, as kept in their Pyfa.
- **What-if training:** "what does this fit gain if I train X to V",
  without editing and reverting a saved character.

Success: tests T1–T11 (below) pass.

## Decisions taken in brainstorming

- **Edits land on a server copy.** Characters are imported into the
  server's own saveddata.db and edited there; the user's Pyfa is written
  only by an explicit export (the same split as stored fits vs `pyfa:` fits).
- **Export never overwrites.** It inserts a new character under a free name,
  like `export_to_pyfa` for fits.
- **Inline skill sheets** (per call) cover what-if questions; nothing is saved.
- **Only the main fit** gets the character; command and projected fits stay
  at All 5.

## Characters and how they are named

| Reference | What | Editable |
|---|---|---|
| `All 0`, `All 5` | Pyfa's built-ins | no |
| `Main` | a character stored in the server's saveddata.db | yes |
| `pyfa:Main` | a character in the user's Pyfa, read from the snapshot (`pyfadata._session`) | no |

Names are case-insensitive. A numeric id works as for fits (`3`,
`pyfa:3`). Unknown names get "did you mean" suggestions. Server character
names are unique; `All 0` and `All 5` are reserved.

## The portable form

Every reference resolves to plain data before an evaluation:

```python
{"name": "Main", "skills": {"Gunnery": 5, ...}, "alpha_clone": None | "Amarr Alpha"}
```

`skills` holds every skill with a level (unlearned skills are absent, i.e.
0). An evaluation builds a temporary Pyfa `Character` from it in the
server's saveddata.db, named with `TEMP_NOTE`, sets it as the temporary
fit's character, and deletes it on exit (`evaluate.Scratch`); boot purges
leftovers as it does temporary fits and profiles. Search workers receive the
portable form in their conditions, so `marginal_swaps`, `optimize_fit`,
`find_modifiers` and fleet search use the character with no other wiring.
The search trial cache key includes the resolved skills (as
`_own_profiles` does for profiles), so an edited character is measured
again.

**Unusable candidates.** With a character other than All 5, the candidate
pool of `find_modifiers`, `marginal_swaps` and `optimize_fit` leaves out
items (and charges) whose required skills the character lacks, with the
excluded reason `"character lacks skills"`, like the availability filter;
`availability="all"` lifts it. Without this the optimizer would suggest
exactly the unflyable fit the issue reports.

## `conditions.character`

- A name: `"character": "pyfa:Main"`.
- An inline skill sheet: `"character": {"base": "pyfa:My Alt", "skills":
  {"Surgical Strike": 4}}`. `base` defaults to `All 5`; skills left out take
  the base's level. Skill levels may come from anywhere (another tool's ESI
  data, the user). The sheet's levels replace
  the base's for those skills only, exactly as given: no prerequisite
  cascade (a what-if may be one that EVE would not allow). Nothing is saved.
- Default: `All 5`, unchanged.

Because `character` is an ordinary condition, `compare_fits` `variants` can
vary it: `[{"character": {"base": "pyfa:My Alt"}}, {"character": {"base":
"pyfa:My Alt", "skills": {"Surgical Strike": 4}}}]` answers "what does
training X buy me" in one call.

`applied.character` echoes the name, or for a sheet `"custom (base pyfa:My
Alt, 2 skills set)"`, and `"command and projected fits: All 5"` when there are any.

**Missing skills.** With a character other than All 5, the result's
`warnings` list what the fit needs and the character lacks, from Pyfa's
`Character.checkRequirements` (rigs excluded, as Pyfa does), one line per
item: `"Large Autocannon II needs Large Projectile Turret V (has III)"`.
Prerequisites of a missing skill are listed under it only one level deep.

## Tools

- `list_characters(source="server")` — `server` lists All 0, All 5 and the
  stored characters; `pyfa` lists the user's Pyfa characters as `pyfa:`
  references. Each row: id, name, number of skills at V, total skill points.
- `get_character(character, group=None)` — without `group`: trained skills
  grouped by skill group, plus the alpha clone. With `group` (a skill group
  name such as "Gunnery"): every skill in that group, untrained ones at 0.
  Unknown group names list the groups.
- `copy_character(character, name)` — store a copy under `name`. One tool
  for importing (`pyfa:Main`), starting fresh (`All 0`, `All 5`) and
  copying a stored character. Skills and alpha clone are copied; implants
  and the SSO link are not.
- `set_skills(character, skills)` — stored characters only. `skills` is
  `{skill name: level 0..5}`. Raising a skill also raises its missing
  prerequisites (Pyfa's `_trainSkillReqs`); lowering one lowers the skills
  that need it (Pyfa's strict skill levels). Returns every level that
  changed, the requested ones and the cascaded ones, as
  `[{skill, from, to, why: "asked" | "prerequisite of X" | "needs X"}]`.
- `delete_character(character)` — stored characters only.
- `export_character_to_pyfa(character, name=None)` — insert the character
  into the user's Pyfa as a new row under a free name (`Main (2)`), with its
  skills and alpha clone. Same guards as `pyfadata.export_fit`: Pyfa must be
  closed, the database version must match, a backup is made first and
  removed if nothing changed. Never changes an existing row.

`conditions_format()` documents the new `character` forms and lists
`your_characters` (the user's Pyfa characters) next to `your_damage_profiles`.
The server instructions say: pass `character` for "with my skills"
questions, use a sheet (or sheet variants) for "if I train" questions, and copy before
editing.

## Errors

- Unknown skill name: `"unknown skill 'Gunery' (did you mean: Gunnery?)"`.
  Non-skill items are rejected the same way.
- Level not an integer in 0..5: rejected, nothing changed (the whole
  `set_skills` call is checked before any change).
- Editing or deleting All 0, All 5 or a `pyfa:` character: error pointing to
  `copy_character`.
- Name taken in `copy_character`: error naming the existing character.
- The user's Pyfa unreadable or absent: the existing `PyfaDataError`
  messages.

## Out of scope

Character implants (the EFT stays the whole pod), security status, a
default-character setting, ESI skill import, and "which skill helps this
fit most". The catalog's "at All V" bonus values and fleet burst strengths
describe items, not pilots, and stay as they are.

## Files

- `pyfa_mcp/characters.py` (new): resolve references to the portable form,
  build/delete temporary characters, the stored-character tools.
- `pyfa_mcp/pyfadata.py`: read characters from the snapshot;
  `export_character` beside `export_fit`, sharing its guards.
- `pyfa_mcp/conditions.py`: parse the new `character` forms; `apply` sets
  the temporary character; echo; missing-skill warnings.
- `pyfa_mcp/candidates.py`: the skill filter on the pool.
- `pyfa_mcp/search.py`: resolve `character` to the portable form in
  `_portable`; include it in the cache key.
- `pyfa_mcp/eosboot.py`: purge temporary characters at boot.
- `pyfa_mcp/server.py`: the six tools, instructions.

## Tests

- **T1** One fit's DPS under All 0 < a stored character with some gunnery
  skills < All 5.
- **T2** A sheet changes the numbers and leaves the stored character
  unchanged; `base` defaults to All 5.
- **T3** `set_skills` raising a skill raises missing prerequisites;
  lowering one lowers dependants; the result lists both with `why`.
- **T4** `set_skills` with one bad name or level changes nothing.
- **T5** `copy_character("pyfa:X", ...)` from a Pyfa database fixture copies
  skills and alpha clone; `pyfa:X` is usable directly in conditions.
- **T6** `export_character_to_pyfa` inserts a new row under a free name,
  makes a backup, leaves existing rows alone; refuses while Pyfa runs.
- **T7** `optimize_fit` / `marginal_swaps` with a character give the same
  confirmed numbers as `evaluate_fit` with that character; editing the
  character invalidates the trial cache.
- **T8** Missing-skill warnings appear for a character lacking a fitted
  module's skill and not for All 5.
- **T9** `compare_fits` with `variants` differing only in `character`
  gives one row per variant with different numbers.
- **T10** With a character lacking Rockets, `marginal_swaps` offers no
  rocket launcher and lists it under excluded `"character lacks skills"`;
  with `availability="all"` it is offered.
- **T11** No temporary character survives an evaluation, an error, or a
  restart.
