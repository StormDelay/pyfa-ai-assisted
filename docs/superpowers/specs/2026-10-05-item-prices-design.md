# pyfa-mcp — item prices

Date: 2026-10-05
Status: approved in brainstorming, pending written-spec review

## Purpose

The agent cannot weigh cost against gain: it sees that a faction module gives
+3% but not that it costs 80M against 5M for Tech II. It guesses prices from
memory or ignores them, and the search tools always prefer the stronger item
(the fitting guide already works around this for faction mindlinks: "the tools
can't see price").

Goal: every tool output that names items carries an ISK estimate, so the agent
can make the cost-effectiveness call itself. The tools only show prices; they
do not search or rank by them.

Success: tests P1–P9 (below) pass; a tool call never waits on the network
(except `refresh_prices`); the server makes at most one price download per
3 days in normal use.

## Decisions taken in brainstorming

- **Show prices only.** No `max_isk` constraint, no ISK objective, no
  gain-per-ISK ranking. The agent does the arbitrage.
- **Surfaces:** evaluate_fit / compare_fits, marginal_swaps, find_modifiers /
  optimize_fit, item_info / search_items.
- **No synchronous lookups, no request spam.** Pyfa's own model (on-demand
  `Price.fetchPrices` per batch of items, through a worker thread) was
  rejected: it waits on the network on every cache miss and sends a request
  per batch. Instead: **one bulk download in the background, tools read a
  local file**.
- **Source: fuzzwork's bulk aggregate**, The Forge sell 5th percentile (the
  same kind of number Pyfa shows by default: fuzzwork, Jita sell). Chosen over
  ESI `/markets/prices/` (CCP universe average). Checked 2026-10-05: 17,343
  items priced; within ~3% of ESI on the samples checked.
- **Expiry: 3 days**, not Pyfa's 24h.
- **The agent can force a refresh** when the user asks (`refresh_prices`).

## Source

`GET https://market.fuzzwork.co.uk/aggregatecsv.csv.gz` (~6.4 MB gzip,
regenerated every ~30 min). CSV header:

```
what,weightedaverage,maxval,minval,stddev,median,volume,numorders,fivepercent,orderSet
10000002|2048|false,...,<fivepercent>,...
```

`what` is `<regionID>|<typeID>|<isBuy>`; the file has regions only (no
stations). Keep rows with region `10000002` (The Forge) and `isBuy` `false`;
the price is `fivepercent`. A `fivepercent` of 0 or a row that does not parse
is skipped (no price). The download is parsed in memory and discarded.

## Component: `pyfa_mcp/prices.py`

The only new module. No Pyfa imports; `requests` is already a dependency.

**Cache file:** `<data_dir>/prices.json`:

```json
{"source": "fuzzwork", "fetched_at": 1791190336.0, "prices": {"2048": 367992.61}}
```

(~400 KB.) Written to `prices.json.tmp` then `os.replace`d, so a crash never
leaves a half-written file.

**State, in memory:** the loaded prices, `fetched_at`, the time of the last
failed attempt, the last error, whether a download is running. The file is
loaded once and reloaded when its mtime changes.

**Freshness:**

- `STALE_AFTER = 3 days` from `fetched_at`.
- `RETRY_AFTER = 1 hour` after a failed attempt.
- `REFRESH_COOLDOWN = 10 minutes`: `refresh_prices` does not download when
  the data is younger than this.

**Background refresh (`ensure_fresh()`):** called at boot and from each price
read, at most once an hour (a cheap time check). If the data is missing or
stale, and no download is running, and the last failure is older than
`RETRY_AFTER`, start a daemon thread that downloads, parses and writes the
file. A lock makes sure only one download runs at a time. Failure keeps the
old file, records the error and time, and logs to stderr (stdout is the
JSON-RPC stream).

**Reads (no network):**

- `price(type_id) -> float | None`
- `price_info() -> dict`:
  `{"source": "fuzzwork, The Forge sell (5th percentile)", "fetched_at": <iso>
  | None, "age_days": float | None, "state": "ok" | "loading" | "stale" |
  "unavailable", "error": str?}`.
  `loading`: no data yet, download running. `stale`: data older than 3 days
  (refresh failed or pending). `unavailable`: no data and no download
  running (the last one failed).
- `price_source() -> str`: one line for tool outputs, e.g.
  `"fuzzwork Forge sell, 1.2 days old"`, `"prices loading, retry shortly"`,
  `"prices unavailable: <error>"`, `"fuzzwork Forge sell, 4.5 days old
  (stale; refresh failed)"`.

**Forced refresh (`refresh(timeout=30) -> dict`):** the only synchronous
download. If the data is younger than `REFRESH_COOLDOWN`, return
`{"refreshed": false, "reason": "prices are N minutes old", **price_info()}`
without downloading. Otherwise download in the calling thread (waiting on a
running background download instead of starting a second one), then return
`{"refreshed": true, **price_info()}` or `{"refreshed": false, "error": ...,
**price_info()}`. It never raises.

**Network:** `requests.get(URL, timeout=..., headers={"User-Agent":
"pyfa-mcp/<version>"})`. Pyfa's `Network` and `Price` services are not used.

## Surfaces

All prices are bare ISK floats. **An unknown price is `null`, never 0.** Each
tool response that contains prices also gets one top-level
`price_source` string (`prices.price_source()`). Prices are attached in the
main process on the rows being returned; search workers never see them.

- **`evaluate_fit`:** a `price` block:

  ```json
  {"total": 412000000.0, "hull": ..., "modules": ..., "charges": ...,
   "drones": ..., "implants": ..., "cargo": ..., "unpriced": ["..."]}
  ```

  `modules` covers high/mid/low slots, rigs and subsystems; `charges` is one
  full load per module that holds one (the module's charge count × the charge
  price); `drones` covers drones and fighters, times their count; `implants`
  covers implants and boosters; `cargo` is cargo times count. A bucket is the
  sum of its priced items; an empty bucket is 0; a bucket whose items are all
  unpriced is `null`. `total` is the sum of every priced item (`null` if
  nothing is priced); `unpriced` names every item without a price
  (deduplicated), so a partial sum is never mistaken for the full cost. Modules that do not fit and are
  left out of the fit are not priced.
- **`compare_fits`:** `price.total` joins `stats.DEFAULT_COMPARE`, and any
  `price.*` key can be picked in `stats`. `price` is not a stat: it is not an
  objective or constraint for the search tools.
- **`marginal_swaps`:** each row gets `isk_delta` = price(add) −
  price(remove). An empty side counts 0; an unknown side makes it `null`. For
  a charge swap the prices are one full load. Rows stay sorted by gain.
- **`find_modifiers`:** each row's `best` and `reference` get `price`; with
  `expand`/`verbose`, every listed variant gets `price`.
- **`optimize_fit`:** each fit in `best` gets `price_total` (the
  `evaluate_fit` `price.total` of that fit); in compact mode `best[1:]`'s diff
  includes it.
- **`item_info`, `search_items`:** `price` per item.
- **`status`:** a `prices` field with `price_info()`.
- **New tool `refresh_prices()`:** docstring: "Download fresh market prices
  now. Call it only when the user asks for fresh prices; prices otherwise
  refresh in the background every 3 days." Returns `prices.refresh()`.

## Text changes

- `INSTRUCTIONS`, one new bullet: prices are estimates (fuzzwork, The Forge
  sell, i.e. roughly Jita sell); weigh cost when recommending faction,
  deadspace or officer items over Tech II and say what the upgrade costs;
  quote prices with their age from `price_source`; `null` means unknown, not
  free; call `refresh_prices` only when the user asks.
- `fitting_guide.yaml`, the `fleet_command` mindlink principle: its `why`
  drops "the tools can't see price"; the text says the faction mindlink's cost
  shows in `price`.

## Errors

Prices never fail a tool call.

- Download or parse failure: keep the old file and data, record the error,
  retry no sooner than `RETRY_AFTER`.
- Corrupt or unreadable `prices.json`: treated as missing (no data), and a
  background download starts.
- Any exception while attaching prices to an output: the price fields are
  `null` and `price_source` carries the error; the rest of the output is
  unchanged.

## Testing

No network in tests: `conftest.py` monkeypatches the download function to
raise (as it does for `drift._fetch_tag`), and tests that need data either
write a known `prices.json` into the test data dir or patch the download to
return a gzip CSV built in the test.

- **P1** Parsing: a small gzip CSV keeps Forge sell rows; drops buy rows,
  other regions, `fivepercent` 0 and malformed rows.
- **P2** Freshness: data with `fetched_at` 2.9 days ago is `ok` and starts no
  download; 3.1 days ago is `stale` and starts one.
- **P3** Failure: a failing download leaves the old `prices.json` byte for
  byte, sets `state` `stale` (or `unavailable` with no file), and no new
  attempt starts within `RETRY_AFTER`.
- **P4** `loading`: with no file and a download blocked on an event,
  `price()` returns `None` and `price_info()["state"]` is `loading`.
- **P5** `refresh`: within `REFRESH_COOLDOWN` it does not download; after it,
  it downloads and returns `refreshed: true`; a failing download returns
  `refreshed: false` with `error` and does not raise.
- **P6** `evaluate_fit.price`: on a known fit with a seeded `prices.json`,
  bucket totals and `total` match the hand sum; an item missing from the
  file is in `unpriced` and makes nothing 0; `price_source` is present once.
- **P7** `marginal_swaps`: a known row's `isk_delta` equals price(add) −
  price(remove); an unpriced side gives `null`.
- **P8** `item_info` and `search_items` carry `price`; `compare_fits` has a
  `price.total` column.
- **P9** `find_modifiers` `best`/`reference` and `optimize_fit` `best` carry
  `price` / `price_total`.

Manual check (in the plan, not in CI): one live `refresh_prices()` against
fuzzwork; Damage Control II gets a positive price.

## Out of scope

- Searching or ranking by price (`max_isk`, ISK objective, gain per ISK).
- A choice of source, region or hub; buy prices; ESI.
- Pyfa's `prices` table in `saveddata.db` and its `Price` service.
- Prices for abyssal modules, contract-only or non-market items (they stay
  `null`).
