# PBI Test Utility — Specification

## 1. Overview

A unified test data utility for the Genesis Global TrOWE Primary Bond Issuance (PBI) platform. It combines previously separate scripts into a single application with a React UI and a FastAPI backend. The utility supports:

- **Bond data insertion** — generates and posts `EVENT_NEW_ISSUANCE_DATA` events
- **Loan data insertion** — generates and posts `EVENT_CREATE_NEW_LOAN_ISSUANCE` events
- **Interest capture** — generates and posts `EVENT_INTEREST_CAPTURE` events (common to both bonds and loans)
- **TIG orders** — generates and posts `EVENT_TIG_CREATE_ORDER` events (a different client's order-creation flow; see §16)
- **CSV / Excel upload** — reads an uploaded CSV or Excel file and posts each row as an `EVENT_NEW_ISSUANCE_DATA` payload, with fixed-delay or simulation (time-replay) timing

---

## 2. Source Scripts

| Tool | Source | Event type |
|---|---|---|
| Bonds | `C:\python\LATEST_PBI_JSON\deal_poster_url_auth_v3.py` | `EVENT_NEW_ISSUANCE_DATA` |
| Loans | `C:\python\PBI_LOAN_JSON_PUBLISHER_V1\` (multi-module) | `EVENT_CREATE_NEW_LOAN_ISSUANCE` |
| Interest | `C:\python\Capture_Interest\capture_interest.py` | `EVENT_INTEREST_CAPTURE` |
| CSV Upload | `C:\python\pbi_csv_to_json_publisher_v2\csv_to_json_publisher_V4.py` | `EVENT_NEW_ISSUANCE_DATA` |

---

## 3. Authentication

All tools authenticate via the same Genesis Global auth service using `TXN_LOGIN_AUTH`.

### 3.1 Credential sets

Three separate credential sets are required:

| Credential set | Used by | Fields |
|---|---|---|
| `bonds_loans` | Bonds tab, Loans tab, CSV Upload tab | `username`, `password` |
| `interest` | Interest Capture tab | `username`, `password` |
| `tig_orders` | TIG Orders tab | `username`, `password` |

All sets are stored per environment in `environments.json` and are editable in the Settings tab.

### 3.2 Login request

```
POST https://{host_name}/sm/event-login-auth

Body:
{
  "MESSAGE_TYPE": "TXN_LOGIN_AUTH",
  "SERVICE_NAME": "AUTH_MANAGER",
  "DETAILS": {
    "USER_NAME": "<username>",
    "PASSWORD": "<password>"
  }
}
```

### 3.3 Login response

- Bonds / Loans: extract `SESSION_AUTH_TOKEN` from response body
- Interest: extract both `SESSION_AUTH_TOKEN` and `REFRESH_AUTH_TOKEN` (both required in event POST headers)

Token paths checked (in order):
1. `DETAILS.SESSION_AUTH_TOKEN`
2. `SESSION_AUTH_TOKEN` (top-level)
3. `sessionAuthToken`

---

## 4. Environment Management

Environments are stored in `backend/environments.json`. There is no `.env` file — all configuration is managed through the UI.

### 4.1 Environment profile fields

| Field | Type | Description |
|---|---|---|
| `host_name` | string | Host, **optionally with a scheme and port**. `qa-trowe-pbi2.cddev.genesis.global` (https assumed) or `http://localhost:8080`. See 4.1.1 |
| `verify_ssl` | bool | Whether to verify SSL certificates |
| `credentials.bonds_loans.username` | string | Username for bonds and loans auth |
| `credentials.bonds_loans.password` | string | Password for bonds and loans auth |
| `credentials.interest.username` | string | Username for interest capture auth |
| `credentials.interest.password` | string | Password for interest capture auth |
| `credentials.tig_orders.username` | string | Username for TIG order-creation auth |
| `credentials.tig_orders.password` | string | Password for TIG order-creation auth |

#### 4.1.1 `host_name` carries the scheme (changed 2026-09-24)

**https is the default, not a rule.** Give `host_name` an explicit scheme and that scheme is
used, port included:

| `host_name` | URL the engines call |
|---|---|
| `qa-trowe-pbi2.cddev.genesis.global` | `https://qa-trowe-pbi2.cddev.genesis.global/…` |
| `localhost:8080` | `https://localhost:8080/…` — no scheme given ⇒ https |
| `http://localhost:8080` | `http://localhost:8080/…` |
| `https://localhost:8443` | `https://localhost:8443/…` |

Until 2026-09-24 only Interest Capture honoured this; the other six engines hardcoded
`f"https://{host}"` in **13 places**, so pointing the app at a local dev server meant editing
Python and remembering to put it back. The rule now lives in one place —
`engines/url_utils.py` (`normalize_host` / `join_url`) — and all seven engines use it.

Two properties to preserve when touching that module:

- **An empty or unparseable `host_name` raises `ValueError`**, so a typo in Settings fails with
  a clear message instead of a confusing connection error. Because of that, engines build their
  publish URL **only on the non-dry-run path** — a dry run must work with no host configured at
  all, which is what `tests/test_munis.py` exercises.
- **`join_url` passes the path through as written.** Several engines post to `/gwf//EVENT_X`
  with a doubled slash, which the server accepts; Bonds and CSV Upload collapse it in their own
  `_normalize_url`. That difference is deliberate, not an inconsistency to unify.

### 4.2 Reference directories

Stored at the top level of `environments.json` (not per-environment — they are filesystem paths):

| Key | Default | Description |
|---|---|---|
| `reference_dirs.bonds` | `{BACKEND_DIR}/reference/bonds` | Bonds CSV reference data |
| `reference_dirs.loans` | `{BACKEND_DIR}/reference/loans` | Loans CSV reference data |
| `reference_dirs.securitized` | `{BACKEND_DIR}/reference/securitized` | ABS CSV reference data (10 files, §20.5) |
| `reference_dirs.munis` | `{BACKEND_DIR}/reference/munis` | Munis CSV reference data (7 files, §21.5) |

`{BACKEND_DIR}` is expanded by `config_manager._resolve`. These were absolute
`C:\python\...` paths until 2026-09-24; the placeholder makes one file correct on Windows, on
macOS, in the container, and in a checkout at any path.

### 4.2.1 `environments.json` is not in git (changed 2026-09-24)

The file holds credentials *and* is the one file every developer must edit to run anything, so a
tracked copy guarantees both secret churn and permanent merge conflicts on it. What is tracked is
**`backend/environments.example.json`** — same shape, every password blank, plus a ready-made
`Local` entry (`http://localhost:8080`, `verify_ssl: false`) which is the `active` one.

First start seeds the real file from the first of these that exists:

1. `$PBI_STATE_DIR/environments.json` — already set up, nothing to do
2. `backend/environments.json` — an existing local copy, so upgrading breaks nobody
3. `backend/environments.example.json` — the tracked template
4. `config_manager._defaults()` — last resort

A fresh clone therefore starts pointed at `http://localhost:8080` over plain http with **no file
edits at all**. Adding a real environment means filling in passwords locally; the file is
git-ignored, so they never leave the machine — and the container no longer bakes them into an
image layer, because `.dockerignore` excludes it and the image seeds from the example.

> The credentials committed before this change remain in git history. Rotating them is a separate
> job that this change does not do.

### 4.3 Switching environments

The active environment is selected from the header dropdown. The selection is persisted to `environments.json` as `active` — switching optimistically updates the UI and immediately writes back via `PUT /api/config`, so the choice survives a browser reload. Changes take effect on the next Run.

---

## 5. Bonds Tool

### 5.1 Endpoint

```
POST https://{host_name}/gwf//event_new_issuance_data
```

### 5.2 Run parameters (UI)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `single` | int | 0 | Number of single-tranche deals to post |
| `multi` | int | 1 | Number of multi-tranche deals to post |
| `tranches_per_multi` | list[int] | [3, 8] | Tranche count per multi-deal (cycles through list) |
| `sleep_ms` | int | 100 | Milliseconds to sleep between requests |
| `currency` | string | — | Force every generated tranche into this currency (optional; blank = random per tranche) |
| `coupon_type` | string | — | `Fixed` \| `Float` \| `Prelim`; blank = the engine's own draw (see §5.2.1) |
| `dry_run` | bool | false | Generate payloads but do not authenticate or POST |

When `currency` is supplied, both `CURRENCY_CODE` and `TRANCHE_CURRENCY` on every tranche are set to that value instead of being drawn from `ref.currencies`.

### 5.2.1 Coupon type

Blank — the default — leaves the historical behaviour untouched: `_build_tranche` draws
`Fixed`/`Float` **per tranche** for single deals, and `_build_multi` draws **once per deal** so a
multi-tranche deal is uniform. Choosing a value pins every tranche in the run, including every
tranche of every multi-deal.

`COUPON_TYPE` drives `IPTS`, which is derived and never drawn independently:

| `coupon_type` | `COUPON_TYPE` | `IPTS` |
|---|---|---|
| blank | `Fixed` or `Float` | as below, per the draw |
| `Fixed` | `Fixed` | rate range from `ipts.csv`, e.g. `3.25%-3.50%` |
| `Float` | `Float` | `{benchmark} + {60..180}bps`, benchmark by currency |
| `Prelim` | `Prelim` | **empty string** — Prelim carries no pricing |

**`Prelim` leaves the issuance unassigned in the app.** That is the point of the option — it is how
the unassigned-issuance path is exercised — but it means a Prelim run produces deals the app cannot
classify, so it is never the default; the UI shows an amber warning while it is selected.
`COUPON_TYPE` is the only field that changes, and nothing else in the payload is conditioned on it.

Unrecognised values log a `warn` and fall back to the random draw rather than failing the run.
Emails print `Prelim` verbatim in all five bond formats, and expectation capture (§18) stores
`coupon_type = 'Prelim'` with `ipts`, `benchmark_for_pricing` and `coupon_index` all NULL —
`_r_benchmark` parses the now-empty IPTS and yields nothing.

### 5.3 Payload format

`MESSAGE_TYPE`: `EVENT_NEW_ISSUANCE_DATA`  
`SERVICE_NAME`: `ISSUANCE_EVENT_HANDLER`

Key `DETAILS` fields: `ISSUER_NAME`, `ISSUER_TICKER`, `PRELIMINARY_SECURITY_TENOR`, `CURRENCY_CODE`, `COUPON_TYPE`, `IPTS`, `BOND_CLASS`, `REGISTRATION_TYPE`, `TRANCHE_STATUS`, SEC identifiers (ISIN/CUSIP/FIGI).

### 5.4 Reference data

Loaded from `reference_dirs.bonds`. All files are optional; built-in defaults are used if missing.

| File | Contents |
|---|---|
| `issuers.csv` | `NAME, TICKER` |
| `currencies.csv` | `CODE` |
| `countries.csv` | `ISO2, ISSUER_COUNTRY, COUNTRY_OF_ISSUE, RISK_COUNTRY, CURRENCY_CODE` |
| `sectors.csv` | `NAME` |
| `amounts.csv` | `AMOUNT` (integer) |
| `ipts.csv` | `LOW, HIGH` (percent) |
| `float_benchmarks.csv` | `CODE, BENCHMARK` |

### 5.5 Multi-tranche behaviour

All tranches in a multi-deal share the same issuer, country, sector, and rating. Each tranche gets a unique tenor (drawn without replacement from a pool). Coupon type is consistent within a deal.

---

## 6. Loans Tool

### 6.1 Endpoints

```
POST https://{host_name}/gwf//EVENT_CREATE_NEW_LOAN_ISSUANCE   ← publish
POST https://{host_name}/gwf/ALL_LOAN_DEAL                     ← multi-tranche linkage query
```

### 6.2 Run parameters (UI)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `single` | int | 3 | Single-tranche deals |
| `multi` | int | 0 | Multi-tranche deals |
| `tranches_per_multi` | list[int] | [2, 3] | Tranche counts (cycles) |
| `delay` | float | 15 | Seconds between POSTs |
| `deal_query_wait` | float | 6 | Seconds to wait before ALL_LOAN_DEAL query |
| `currency` | string | — | Force all issuances into this currency (optional) |
| `dry_run` | bool | false | Generate payloads but do not POST |

### 6.3 Payload format

`MESSAGE_TYPE`: `EVENT_CREATE_NEW_LOAN_ISSUANCE`  
`SERVICE_NAME`: `ISSUANCE_EVENT_HANDLER`

Constant fields: `SUB_ASSET_CLASS = "TLB"`, `TRADE_DESK = "Lev Loans"`.

Deal-level fields (shared across tranches): `ISSUER_NAME`, `ISSUER_TICKER`, `ISSUER_COUNTRY`, ratings, dates `ANNOUNCEMENT_DT`→`PRICE_DATE`, `SPONSOR`, `BND_BANK`, etc.

Tranche-level fields (regenerated per tranche): `ISSUE_SIZE`, `FINAL_SIZE`, `COUPON_TYPE`, `SPREAD`, `TENOR`, `MATURITY_DATE`, `SECURITIES[].CUSIP`, etc.

**String-typed numeric fields.** The server schema validates several numeric-looking fields as JSON strings (a `oneOf: [string, null]`), so the engine sends them as strings, not numbers. Sending a raw number produces a NACK such as `$.DETAILS.FINAL_OID: must be valid to one and only one schema, but 0 are valid`. These fields are stringified in the payload:

| Field | Sent as |
|---|---|
| `SPREAD`, `FINAL_SPREAD` | string (e.g. `"90"`) |
| `ORIGINAL_ISSUE_DISCOUNT` (OID), `FINAL_OID` | string (e.g. `"3"`) — changed from int after the DB columns became string |
| `YIELD`, `FINAL_YIELD` | string (e.g. `"5.23"`) — changed from float after the DB columns became string |

`ISSUE_SIZE`/`FINAL_SIZE` remain JSON numbers (their schema still accepts numbers). When a future column flips to string, the multi-error NACK log (see §9.1) names the exact field to convert.

### 6.4 Multi-tranche linkage

1. Tranche 1 is posted and returns `ACK`
2. Wait `deal_query_wait` seconds (lets prior SOURCE_REF subscription expire)
3. POST to `ALL_LOAN_DEAL` with `CRITERIA_MATCH: "ISSUER_TICKER=='<ticker>'"` 
4. Select the row with the latest `CREATED_ON` — extract `MASTER_LOAN_ID`
5. Tranches 2..N include `DETAILS.MASTER_LOAN_ID`

If tranche 1 returns `NACK` or `HTTP_ERROR`, the remaining tranches of that deal are skipped.

### 6.5 Reference data

| File | Contents |
|---|---|
| `issuers.csv` | `issuer_name, ticker` |
| `currencies.csv` | `country_name, country_code, currency, float_benchmark` |

Falls back to 10 hardcoded countries if `currencies.csv` is missing.

---

## 7. Interest Capture Tool

### 7.1 Endpoints

```
POST https://{host_name}/sm/event-login-auth       ← auth
POST https://{host_name}/gwf//event-interest-capture ← events
```

### 7.2 Run parameters (UI)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tranche_name` | string (UUID) | — | Identifies the bond issuance being captured |
| `is_modelled_true` | int | 9 | Number of IS_MODELLED=True events |
| `is_modelled_false` | int | 5 | Number of IS_MODELLED=False events |
| `pair_closeness_pct` | float | 2.0 | Max % deviation of True qty from paired False qty |
| `total_mismatch_pct` | float | 0.0 | Max % difference between True total and False total |
| `delay_seconds` | float | 4 | Seconds between event POSTs |
| `strategy` | bool | false | Enable strategy mode (parent/child account rows) |
| `dry_run` | bool | false | Generate events but do not authenticate or POST |
| `sizing.min_piece` | int | 10000 | Minimum valid quantity |
| `sizing.increment_size` | int | 100000 | Quantity must be a multiple of this |
| `quantity_ranges.true_min/max` | int | 100k / 1.2M | Range for IS_MODELLED=True quantities |
| `quantity_ranges.false_min/max` | int | 600k / 1.6M | Range for IS_MODELLED=False quantities |

### 7.3 Quantity alignment rules

Valid quantities must satisfy:  
`Q = N × increment_size` where `N × increment_size ≥ min_piece`

Example: `min_piece=10000`, `increment_size=100000` → valid: 100k, 200k, 300k, …

### 7.4 Event ordering (standard mode)

Events are interleaved: each IS_MODELLED=False event is followed by its paired IS_MODELLED=True events. Unmatched True events are appended at the end.

### 7.5 Strategy mode

Each strategy group has:
- One parent row: `IS_MODELLED=False`, `ACCOUNT_CODE=null`, `STRATEGY=<4-6 uppercase letters>`
- N child rows: `IS_MODELLED=True`, `ACCOUNT_CODE=<4-digit numeric>`, same `STRATEGY` code

Child quantities sum to approximately the parent quantity (within `pair_closeness_pct`).

### 7.6 Account codes

| IS_MODELLED | Mode | ACCOUNT_CODE format |
|---|---|---|
| False | Standard | 3–4 random uppercase letters |
| True | Standard | 4 random digits |
| False | Strategy | `null` |
| True | Strategy | 4 random digits |

---

## 8. CSV Upload Tool

### 8.1 Purpose

Reads an uploaded CSV or Excel file and publishes each row as an `EVENT_NEW_ISSUANCE_DATA` event, reproducing the behaviour of `csv_to_json_publisher_V4.py` inside the utility. Useful for replaying real captured data rather than generating synthetic payloads.

### 8.2 Endpoint

```
POST https://{host_name}/gwf//event_new_issuance_data
```

Same endpoint as the Bonds tool. Auth uses the `bonds_loans` credential set.

### 8.3 Run parameters (UI)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `file` | File (upload) | — | CSV (`.csv`) or Excel (`.xlsx`/`.xls`) file |
| `rows` | string | — (all) | Row range to process, 1-based inclusive. e.g. `"2-5"` or `"3"`. Blank = all rows. |
| `delay_seconds` | float | 2 | Fixed pause between each row POST (used in non-simulation mode, or as fallback). |
| `simulation` | bool | false | If enabled, replays timing from `INSERT_TIME` column (see §8.5). |
| `time_scale` | float | 1.0 | Speed multiplier for simulation. `2.0` = 2× faster; `0.5` = half speed. Shown only when simulation is on. |
| `dry_run` | bool | false | Generate payloads but do not authenticate or POST. |

### 8.4 Column header normalisation

When the file is loaded, **all column headers are automatically uppercased**. A file with `issuer_name`, `Issuer_Name`, or `ISSUER_NAME` in its header row all resolve to `ISSUER_NAME` in the payload.

### 8.5 Simulation mode

When `simulation = true` the engine reads timing from a column named `INSERT_TIME` (the exact column name is fixed in the engine). For each row after the first:

```
sleep_seconds = (current_INSERT_TIME_ms − prev_INSERT_TIME_ms) / 1000 / time_scale
```

`INSERT_TIME` values are accepted as epoch milliseconds, epoch seconds, or ISO datetime strings. If a value is unparseable the engine falls back to `delay_seconds` for that row.

### 8.6 Value normalisation

The engine applies the same normalisation rules as `csv_to_json_publisher_V4.py`:

| Input | Output |
|---|---|
| `None` / `NaN` | field omitted |
| `00-01-1900` (sentinel) | field omitted |
| `int` / `float` (finite) | string representation with no scientific notation |
| Scientific notation string (e.g. `7.5E8`) | plain integer string (`750000000`) |
| `dd-MM-YYYY` date | epoch milliseconds (string) |
| ISO datetime string | epoch milliseconds (string) |
| Other string | kept as-is |
| `bool` | kept as bool |

### 8.7 Duplicate columns (`.1` / `.2` suffixes)

If the spreadsheet has duplicate column names (pandas renames them `FIELD`, `FIELD.1`, `FIELD.2`), the engine picks the **first non-null value** and maps it to the base name `FIELD`.

### 8.8 Payload format

```json
{
  "MESSAGE_TYPE": "EVENT_NEW_ISSUANCE_DATA",
  "SERVICE_NAME": "ISSUANCE_EVENT_HANDLER",
  "DETAILS": { ...all non-null normalised column values... }
}
```

### 8.9 File upload flow

The browser sends the file + JSON params as `multipart/form-data` to `POST /api/csv_upload/run`. The backend reads the file bytes in the async route handler and passes them in the params dict to the engine thread — no file is written to disk.

---

## 9. Run Lifecycle

1. User clicks **Run** → frontend POSTs params to `/api/{tool}/run` (JSON for Bonds/Loans/Interest; `multipart/form-data` for CSV Upload)
2. Backend creates a `run_id`, starts engine in a background thread, returns `run_id`
3. Frontend opens an SSE connection to `/api/{tool}/stream/{run_id}`
4. Engine emits log messages via a `queue.Queue`; SSE endpoint drains the queue to the client
5. When the engine finishes, it puts `None` (sentinel) in the queue; backend sends `{"type":"done"}`; frontend closes EventSource and re-enables Run button
6. User may click **Stop** at any time → POSTs to `/api/{tool}/stop/{run_id}` → sets a `threading.Event` that the engine checks before each HTTP request and between delay sleeps

Only one run per tool is permitted at a time. Attempting to start a second run while one is active returns HTTP 409.

### 9.1 Dry-run mode (all tools)

Every tab (Bonds, Loans, Interest, TIG Orders, CSV Upload) has a **Dry run** toggle. When enabled the engine:

1. **Skips authentication** — no `TXN_LOGIN_AUTH` call, so a dry run works without valid credentials or network access.
2. **Skips every POST** — nothing is sent to the server.
3. **Logs the full generated payload** for each item as pretty-printed JSON (`json.dumps(payload, indent=2)`), so the exact `DETAILS` that *would* be sent is visible in the Live Output panel.
4. Counts each generated item as a "success" in the summary and skips inter-request delays so the preview is near-instant.

Caveat — Loans multi-tranche: in dry run, tranches 2..N do **not** carry `MASTER_LOAN_ID`, because that value is normally fetched from the live `ALL_LOAN_DEAL` query, which a dry run skips. The tranche-1 payload is fully accurate.

---

## 10. Log Message Format

Each SSE message carries a JSON object:

| `type` | Fields | Description |
|---|---|---|
| `log` | `level`, `msg` | A single log line. `level` ∈ `info`, `success`, `warn`, `error` |
| `summary` | `success`, `failed`, `total` | Final counts after run completes |
| `done` | — | Signals the run is finished; frontend closes SSE |
| `ping` | — | Keep-alive; frontend ignores |

On `done` (or a user **Stop**), the frontend computes the run duration and final status and records a completed-run summary to history (see §12).

### 10.1 NACK error logging (Loans)

When the loans server returns an `EVENT_NACK`, the engine logs **every** entry in the response `ERROR` array, not just the first. The first line shows the error count and the first message; subsequent failing fields are logged on `also —` lines. This makes schema/type mismatches (e.g. several columns at once needing string values) visible in a single run instead of one-per-rerun. Example:

```
[D1/T1/1] NACK (2 error(s)) — $.DETAILS.FINAL_OID: must be valid to one and only one schema, but 0 are valid
[D1/T1/1]      also — $.DETAILS.YIELD: number found, string expected
```

---

## 11. Settings UI

The Settings tab allows:
- Viewing, editing, adding, and deleting environment profiles
- Editing `host_name`, `verify_ssl`, all three credential sets (`bonds_loans`, `interest`, `tig_orders`) per environment
- Editing `reference_dirs.bonds` and `reference_dirs.loans` paths
- Saving all changes back to `environments.json` via `PUT /api/config`

Passwords are stored in plain text in `environments.json` (test environments only — not for production use).

---

## 12. Launch & Setup

The app is launched from the project root (`C:\python\PBI_Test_Utility\`) — no terminal required.

| Script | Purpose |
|---|---|
| `Setup.bat` | One-time setup. Creates `backend\.venv`, installs `requirements.txt`, then runs `npm install` in `frontend`. Must be run once before first launch (and after pulling new dependencies). |
| `Launch.vbs` | Double-click to start the app. Runs `Start-App.ps1` completely hidden (no console windows). |
| `Start-App.ps1` | Starts the backend (`uvicorn main:app` on `127.0.0.1:8000`) and frontend (Vite on `5173`) hidden, waits for both ports concurrently, then opens the browser at `http://localhost:5173`. Typically ready in ~1.5–2s. |
| `Stop-App.vbs` | Double-click to stop. Kills `uvicorn.exe` and the Vite `node.exe` process bound to port 5173. |

### 12.1 Launch robustness (Start-App.ps1)

`Start-App.ps1` includes guards learned from real launch failures:

- **PATH refresh** — rebuilds `$env:Path` from the Machine + User registry values at startup. Without this, if Node.js was installed *after* the current Windows session began, the inherited PATH is stale and `npm` is not found, so the frontend silently fails to start.
- **npm resolution** — resolves `npm.cmd` via `Get-Command`, falling back to known install locations (`%ProgramFiles%\nodejs`, `%APPDATA%\npm`), and invokes it by full path. A clear message box is shown if Node/npm is genuinely absent.
- **IPv6-aware readiness check** — the port probe tries both `127.0.0.1` and `::1`. Vite binds to `localhost` which resolves to IPv6 `[::1]`, while uvicorn binds IPv4 `127.0.0.1`; checking both prevents a false 45-second timeout waiting for the frontend.
- **`System.Windows.Forms` loaded lazily** — the assembly is loaded only inside the error-dialog helper (`Show-Error`), so a successful launch doesn't pay the WinForms load cost.

### 12.2 Launch speed optimizations

The launcher is tuned to reach a usable browser in ~1.5–2s (both servers individually bind in ~1s):

- **Direct Vite, no npm wrapper** — the frontend is started as `node node_modules\vite\bin\vite.js` instead of `cmd /c npm run dev`. This skips the `cmd → npm.cmd → node npm-cli → node vite` chain (~1s of process startup). Falls back to `npm` if `node`/`vite.js` can't be located.
- **Concurrent port wait** — `Wait-ForPorts` polls 8000 and 5173 together (previously it waited for the backend fully, then the frontend) so total wait ≈ the slower server, not the sum.
- **Fine poll granularity** — readiness is polled every 150ms (was every 1s), so a server that binds in ~0.3s is detected almost immediately instead of at the next whole second.
- **Lazy WinForms load** — see §12.1.

### 12.3 macOS launchers

macOS gets the same double-click experience as Windows via `.command` files, which Finder runs
in Terminal:

| Script | Windows equivalent |
|---|---|
| `Launch.command` | `Setup.bat` + `Launch.vbs` + `Start-App.ps1`, folded into one |
| `Stop.command` | `Stop-App.vbs` |

There is deliberately **no `Setup.command`**. The Windows split exists because `Setup.bat` is
slow and `Launch.vbs` is silent; a `.command` file runs in a visible Terminal, so `Launch.command`
can create the venv, `pip install` and `npm install` on first run with the progress on screen,
and launch straight after. Subsequent launches skip it — the test is `backend/.venv/bin/uvicorn`
being executable and `frontend/node_modules` existing.

Otherwise it is a direct port of §12.1–12.2 and carries the same guards:

- **PATH** — a Finder-launched script does not read your shell profile, so `/opt/homebrew/bin`
  and `/usr/local/bin` are prepended. Same failure mode as the stale-registry PATH on Windows.
- **Readiness** — polls `http://localhost:{8000,5173}` with `curl` every 150ms, both together,
  45s budget. Using `localhost` rather than an explicit address covers the IPv4/IPv6 split
  (§12.1) for free, since Vite binds `::1` and uvicorn `127.0.0.1`.
- **Direct Vite** — `node node_modules/vite/bin/vite.js`, falling back to `npm run dev`.
- **Errors** — `osascript -e 'display dialog …'` in place of the WinForms `MessageBox`.

Stopping uses the PIDs written to `.logs/{backend,frontend}.pid`, then falls back to
`lsof -ti tcp:PORT` so a server started by hand is cleaned up too. Server output goes to
`.logs/*.log` (git-ignored) — that is where to look when launch times out.

**Line endings are enforced by `.gitattributes`** (`*.command text eol=lf`). This repo is
developed on Windows with `core.autocrlf=true`; a CRLF checkout fails on macOS with
`bad interpreter: /bin/bash^M`. The executable bit is likewise in the index (mode `100755`,
set with `git update-index --chmod=+x`) because `core.filemode` is `false` on Windows — without
it Finder opens the file in a text editor instead of running it.

### 12.4 Docker (macOS / Linux / Windows)

The `.vbs`/`.bat`/`.ps1` launchers are Windows-only. For any other OS the app ships a
container, which is the *same* app under a different arrangement:

| | Windows launcher | Docker |
|---|---|---|
| Processes | uvicorn `:8000` + Vite `:5173` | uvicorn `:8000` only |
| Frontend | Vite dev server, hot reload | built once at image build, served by FastAPI |
| `/api` reaches the backend via | Vite's dev proxy | same origin — no proxy |
| Browser | `http://localhost:5173` | `http://localhost:8000` |

```bash
docker compose up --build     # then open http://localhost:8000
```

**Single-port mode.** `main.py` mounts `frontend/dist` at `/` with `StaticFiles(html=True)` —
*after* the routers, so every `/api/...` route still wins. The mount is conditional on the
directory existing, so the Windows dev flow (where `dist` may be absent or stale) is unaffected
and still uses Vite. SSE is served directly by FastAPI, with no proxy in the path.

**State directory (`PBI_STATE_DIR`).** Everything the app *writes* — `environments.json`,
`history.json`, and the `expected/` SQLite store — resolves through `backend/paths.py`.
It defaults to the backend folder, i.e. exactly where those files have always lived, so
Windows behaviour is byte-identical. The container sets it to `/app/data`, bind-mounted to
`./data`, so settings and captures survive `docker compose up --build`. `environments.json`
is **seeded** from the copy baked into the image on first start; to change the seed, edit
`backend/environments.json` and rebuild.

**`reference_dirs` are path-independent.** They use the existing `{BACKEND_DIR}` placeholder
(`config_manager._resolve`) rather than absolute `C:\python\...` paths, so the same
`environments.json` resolves correctly on Windows, in the container, and in a checkout at any
path.

**What does not work in the container.** Outlook email *sending*
(`engines/outlook_sender.py`) is Windows COM and requires a locally-installed Outlook Classic
profile. In the container `send_via_outlook` raises `OutlookUnavailable`, which the engines
already handle as a `warn` log line (`✉ Email skipped`) — the run continues and, per §18,
**expectation capture still fires**, because it is deliberately independent of send success.
So email *generation*, capture, upload (`.msg` parsing via `extract-msg`, which is pure Python)
and the whole Email Compare surface work; only the "send it to my inbox" step does not.

**Credentials travel with the image.** `COPY backend/ /app/backend/` bakes
`environments.json` — plain-text test-env passwords included (§4) — into the image layer. That
is no worse than the repo, which checks the same file in, but it means the image must be treated
like the repo: local builds and internal registries only, never a public one.

**Timezone.** A few engines date-stamp deals off the local date (`date.today()`). Containers
default to UTC, so `compose.yml` passes a `TZ` variable — set it to your own zone if the UTC
day could differ from yours.

---

### 12.5 Hot reload — the developer loop (added 2026-09-24)

Neither server needs restarting to pick up a code change.

| Layer | Mechanism | Status |
|---|---|---|
| Frontend | Vite HMR | **always on** — it is what `npm run dev` does, and has been since day one |
| Backend | `uvicorn --reload` | on by default in `Start-App.ps1` and `Launch.command` |

`--reload` costs no new dependency: `watchfiles` already ships with the `uvicorn[standard]`
in `requirements.txt`.

**Only `*.py` triggers a backend reload.** That is uvicorn's `FileFilter` default
(`default_includes = ["*.py"]`, `uvicorn/supervisors/watchfilesreload.py`) and it matters here
more than usual — the backend writes `environments.json` on every Settings save, `history.json`
on every completed run, and `expected/pbi_util.db` on every capture. If any of those triggered a
restart the app would bounce itself mid-run. Verified against the installed uvicorn:

```
includes: ['*.py']
  main.py                     -> reload: True
  engines/bonds_engine.py     -> reload: True
  environments.json           -> reload: False
  history.json                -> reload: False
  expected/pbi_util.db        -> reload: False
```

Set **`PBI_NO_RELOAD=1`** to turn it off — worth doing if the checkout lives on a network drive
or OneDrive, where file watching can be pathological.

In Docker, the base image serves a *static* frontend build, so neither side reloads.
`docker-compose.dev.yml` mounts `backend/` and adds `--reload` for the backend. The frontend is
deliberately not hot-reloaded there: the runtime image is `python:3.12-slim` and has no node, so
there is no Vite to run — for frontend work use `npm run dev` on the host, which is faster and is
what the launchers already do.

---

## 13. Run History

The **History** tab (between CSV Upload and Settings) lists recent runs across all tools and lets the user re-run any of them.

### 13.1 Storage

Run records are persisted **server-side** in `backend/history.json` (managed by `history_manager.py`), so history is shared across the QA team and survives browser refreshes and cache clears. Retention: newest **500** records, dropping anything older than **30 days**.

### 13.2 Endpoints

```
GET  /api/history    → list of records, newest first
POST /api/history    → append one completed-run record (returns it with assigned id)
```

### 13.3 Record shape

| Field | Type | Description |
|---|---|---|
| `id` | string | 8-char hex, assigned server-side |
| `ts` | string | ISO-8601 timestamp, assigned if absent |
| `tool` | string | `bonds` \| `loans` \| `interest` \| `tig_orders` \| `csv_upload` |
| `env` | string | Active environment **name** (e.g. `QA`) at run time |
| `params_summary` | string | Human-readable summary (e.g. `2 single · 1 multi · 3, 8`) |
| `params_raw` | object | Full params object used for the run — drives re-run prefill |
| `ok` / `fail` / `total` | int | Final counts (coerced to int) |
| `status` | string | `done` \| `failed` \| `stopped` (validated; defaults to `done`) |
| `dur_seconds` | number | Wall-clock run duration in seconds |

### 13.4 Recording

History is written from the **frontend** when a run ends, because the engine/SSE layer does not know the environment *name* or a params summary. `useRunState(tool, onFinish)` tracks the run's start time and last `summary`, then fires `onFinish` exactly once on `done` / `stop` (guarded against the `done`-after-`stop` double-fire). Status is derived: `stopped` if the user clicked Stop, else `failed` if any item failed, else `done`. Each tab composes the record and POSTs it via `addHistory`. A failed POST is swallowed — history is best-effort and never blocks a run.

### 13.5 Re-run (↻)

The re-run action is **prefill-only**: it switches to that tool's tab and populates the parameter form from `params_raw`, but does **not** auto-start the run. This is deliberate — re-running posts real test data to the active environment, so the user reviews the prefilled params and clicks Run themselves. (The original prototype auto-fired; this was changed to prefill for safety.)

**CSV Upload re-run caveat:** `params_raw` stores the file name and all run params, but not the file bytes (they are too large and cannot meaningfully be stored in history). Re-running a CSV Upload history entry prefills the params (rows, delay, simulation, time_scale) but the file field is empty — the user must re-select the file before clicking Run.

---

## 14. Form State Persistence

All run-parameter forms (Bonds, Loans, Interest Capture, CSV Upload) persist their field values to `localStorage` whenever any value changes. On the next page load the form starts with the last values the user entered, rather than reverting to the `environments.json` defaults.

**Priority chain for initial field values:**

```
history re-run prefill  ??  localStorage  ??  environments.json tool_defaults  ??  hardcoded fallback
```

The file selection on the CSV Upload tab is not persisted (a `File` object cannot be serialised to localStorage) — only the numeric/text params are saved.

---

## 15. UI Design System

The interface uses the **Operator** design system: a dark, mono-forward "terminal-as-product" aesthetic with a sky-blue accent, suited to a tool whose job is firing requests and streaming live output. Tokens live in `frontend/src/index.css` `:root`.

Tab order: **Bonds · Loans · Interest Capture · TIG Orders · CSV Upload · History · Settings**

| Group | Values |
|---|---|
| Surfaces | `--bg-deep #08090d` · `--bg-base #0b0d13` · `--bg-surface #10141d` · `--bg-raised #161c28` · log `--log-bg #0a0c10` |
| Borders | `--border-subtle #1a2130` · `--border-default #232b3d` (1px hairlines carry separation, not shadows) |
| Accent | `--sky #7dd3fc` · `--sky-dim #38bdf8` + sky glows |
| Semantic | `--emerald` (success) · `--rose` (error) · `--attention` (warn/retry) · `--indigo` (tool/group) · `--slate` (stopped) |
| Text ramp | `--text-primary #e7e9ee` → `--text-secondary` → `--text-muted` → `--text-faint #3a4258` |
| Type | DM Sans (display) + JetBrains Mono (mono), via Google Fonts `@import` |

**Log level → colour** (maps the SSE `level` field): `info` → text-secondary · `success` → emerald · `warn`/retry → attention · `error` → rose. (The terminal also reserves sky for requests, indigo for tool/group lines, and muted for system/init lines.)

Shadows are reserved for *glow* only (status dots, toast, logo mark). Animation is a status signal, not a flourish: the running/status dot pulses (`pulse-dot`, 2s), toggles slide (`.15s`), the toast slides up (`.25s`). No bounces or staggered entrances.

**CSV Upload drop zone** — a dashed-border panel (`.drop-zone`) in the CSV Upload tab. Accepts drag-and-drop or click-to-browse for `.csv`, `.xlsx`, `.xls` files. Turns sky-tinted on drag-over; shows file name + clear button (✕) once a file is selected. Implemented with a visually hidden `<input type="file">` overlaid on the zone so native file-dialog behaviour is preserved.

---

## 16. TIG Orders Tool

A separate order-creation flow used by a different client. It posts `EVENT_TIG_CREATE_ORDER` events — conceptually distinct from Interest Capture (which records rate data), so it is its own tab rather than a toggle inside Interest Capture.

### 16.1 Endpoints

```
POST https://{host_name}/sm/event-login-auth         ← auth
POST https://{host_name}/gwf/EVENT_TIG_CREATE_ORDER   ← orders
```

> Note: the order path uses a **single** `/gwf/` (unlike Bonds/Loans which use `/gwf//`).

### 16.2 Authentication

Uses the dedicated **`tig_orders`** credential set and the **session-token-only** flow (like Bonds/Loans): extract `SESSION_AUTH_TOKEN` from the auth response and send it in the order POST headers. No refresh token.

### 16.3 Run parameters (UI)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `issuance_key` | string | — | `ISSUANCE_KEY`, entered manually. Same value on every order in the run. |
| `market_type` | string | `Market` | Dropdown: `Market` or `Limit`. (Limit is a stub — extra fields such as limit type / spread will be added later.) |
| `registration_type` | string | `144a` | Free-text `REGISTRATION_TYPE`. |
| `trade_desk` | string | `GLOBALFI` | Free-text `TRADE_DESK`. |
| `portfolio_manager` | string | `John Doe` | Optional `PORTFOLIO_MANAGER`; editable. Sent as-is (the engine does not omit it when blank). |
| `count` | int | 5 | Number of orders to post per run. |
| `delay_seconds` | float | 1 | Seconds between order POSTs. |
| `quantity_ranges.min` / `.max` | int | 100k / 1M | Bounds for each order's `QUANTITY`. |
| `sizing.min_piece` | int | 50000 | Minimum valid quantity. |
| `sizing.increment` | int | 50000 | Quantity must be a multiple of this. |
| `dry_run` | bool | false | Generate payloads but do not authenticate or POST. |

### 16.4 Generation rules

- **`QUANTITY`** — a uniform-random multiple of `sizing.increment` that is ≥ `sizing.min_piece` and within `[min, max]`. (If no aligned value fits the window, the smallest qualifying multiple is used.) Each of the `count` orders draws an independent quantity.
- **`ACCOUNT_CODE`** — auto-generated per order: 6 characters from `A–Z` + `0–9`, uppercase, **guaranteed to contain at least one letter and at least one digit**. Not user-editable.

### 16.5 Payload format

`MESSAGE_TYPE` is implicit in the endpoint (`EVENT_TIG_CREATE_ORDER`); the body is a bare `DETAILS` object:

```json
{
  "DETAILS": {
    "ACCOUNT_CODE": "<auto, 6-char>",
    "ISSUANCE_KEY": "<manual>",
    "MARKET_TYPE": "Market",
    "ORDER_STATUS_FIELD": "",
    "PORTFOLIO_MANAGER": "John Doe",
    "QUANTITY": 850000,
    "SECURITY_ID": null,
    "REGISTRATION_TYPE": "144a",
    "REGULATION_SUBCATEGORY": null,
    "TRADE_DESK": "GLOBALFI"
  }
}
```

**Constant fields** (hardcoded on every order, not exposed in the UI): `ORDER_STATUS_FIELD` = `""`, `SECURITY_ID` = `null`, `REGULATION_SUBCATEGORY` = `null`.

### 16.6 Dry run

Same contract as the other tabs (§9.1): skips auth and POST, logs the full generated payload as pretty JSON, counts each generated order as a success.

---

## 17. Email Generation (Bonds & Loans)

Bonds and Loans can additionally render each generated deal as a **broker-style email** and send it to a recipient configured in Settings. This feeds the Genesis email-parsing pipeline: the same deal that would be POSTed as an API payload is also expressed as the natural-language / labelled-block emails that banks send, so the parsed-from-email issuance can be compared against the API-payload issuance (and, later, merged).

Reference formats were catalogued from the `Genesis_Email_Parsing_POC` sample `.msg` files.

### 17.1 Modes

A per-run choice, exposed on the Bonds and Loans tabs as an **Email** slider (mirroring Dry run) plus an **Email only** sub-toggle:

| UI state | `email_mode` | Behaviour |
|---|---|---|
| Email off | `off` | API POST only — unchanged legacy behaviour. |
| Email on | `both` | Normal auth + POST **and** an email per deal. |
| Email on + Email only | `email_only` | No auth, no POST — build & send emails only (fast, credential-free, like dry run but it sends mail). |

`dry_run` + `email` (`both`): payloads are logged (not POSTed) and emails are still sent.

### 17.2 One email per deal

An email represents **one deal**: a single-tranche deal → one email; a multi-tranche deal (one issuer, several tranches) → one email carrying all tranches (mirrors the SMBC/Oracle multi-tranche samples). Multi-*deal* digest formats (bank calendars bundling several borrowers) are **not** generated yet — a possible later phase.

### 17.3 Formats

Selectable per run (`email_format`). Renderers live in `engines/email_builder.py`:

Bond formats are **structural** layouts catalogued from the POC samples (Format 1–10) — each faithfully renders whatever the generator produced (1..N tranches); they differ in layout, not deal type:

| Tool | Format id | Structure / sample |
|---|---|---|
| Bonds | `bond_stacked` (default) | Deal-level labelled header + a per-tranche table (label │ T1 │ T2 …). Format 1/2/7/9 (Oracle, US Bank, SpaceX, ING) |
| Bonds | `bond_single` | Single labelled block; renders each tranche as its own block. Format 3/6 (Synchrony, PSNH) |
| Bonds | `bond_colon` | Compact `Label: value` header (`Issuer:/Syndicate:/Pricing:/Settlement:`) + a repeated per-tenor block (`Tenor:/Size:/Maturity:/Spread to Curve:`). Format 4 (Amazon CAD). The `Issuer:` line was added 2026-08-06 — without a ticker no row can be matched (§18.7) |
| Bonds | `bond_inline` | `*** IPTs: … ***` header, per-tranche one-liners, then `Label value value …` rows on a single line each. Format 8 (SMBC) |
| Bonds | `bond_narrative` | Investor-call / mandate narrative (rated…, has mandated…, offering across tenors…) followed by the stacked table. Format 7 (SpaceX) |
| Loans | `loan_jpm` (default) | JPM Loan Launch — `COMPANY:/BORROWER:/BUSINESS:/UOP:/CALL PROTECTION:/EXPECTED RATINGS:` |
| Loans | `loan_barclays` | Barclays Lead Left / Calendar — Borrower/Facility/UOP + `*** COMMITMENTS DUE … ***` + SyndTrak footer |
| Loans | `loan_citi` | Citi Loan — `Borrower:/Facility:`, Priced/Launched |
| Loans | `loan_rbc` | RBC Debut TLB — narrative intro + Business/Sponsor/Ratings/UoP/Maturity |

### 17.4 Field mapping and gap-filling

The generators already produce most fields (issuer, ratings, spread/margin, tenor, maturity, sponsor, business, use of proceeds; loans' `COMMIT_DATE` → "Commitments due"). Fields the payload has no source for are filled two ways:

- **Boilerplate** — a fixed canned string, identical on every email (denominations `2,000 x 1,000`, timing `Today's Business`, listing venue, disclaimer). See `_BOILERPLATE`.
- **Random pool** — one value picked from a small realistic list each run: Optional Redemption, Ranking, Use of Proceeds, Sale-into-Canada, settlement `T+n`, plus the loan pools (Call Protection, floor, OID). See `_POOLS`.

Each generated bond email opens with a `TD — <note>` desk lead line and ends with a **generic placeholder signature** (`PBI Test Desk` — not a real person) followed by the disclaimer. `ISSUER_RATING` (a combined `Baa2/BBB/BBB-` string) is split into per-agency `Moody's/S&P/Fitch` lines.

### 17.5 Delivery

Sent through the locally-installed **Outlook Classic** profile via COM (`pywin32`), so no SMTP relay is required — the mail lands in the recipient's inbox, from where the user saves it as `.msg` and uploads it to the app. When a dedicated parsing mailbox exists, only the Settings recipient changes.

- COM is initialised per-thread (`pythoncom.CoInitialize`) because engines run on a background thread.
- Optional `save_copy_dir` also writes each sent email to disk as `.msg` (`olMSG`).
- If Outlook / `pywin32` is unavailable the email is **skipped with a warning** (the run does not fail); a missing recipient is logged as a failure.
- A hardened Outlook may show a one-time "a program is trying to send mail on your behalf" prompt — the user clicks Allow.

### 17.6 Configuration

Top-level `email` block in `environments.json` (like `reference_dirs`), edited in Settings → Email:

| Key | Description |
|---|---|
| `email.recipient` | Address generated emails are sent to |
| `email.save_copy_dir` | Optional folder for a local `.msg` copy (blank = don't save) |

Injected into the engine `params` server-side in `routers/_shared.py`; the frontend only sends the per-run `email_mode` / `email_format`. No router changes were needed (params flow-through, per the engine contract).

### 17.7 Counts

In `email_only`, the summary counts **emails per deal** (success/failed). In `both`, counts remain POST-based and email issues are logged but not counted.

---

## 18. Email Comparison (Bonds)

> Status: **built, then simplified.** Steps 1–14 delivered a measurement framework that proved
> too elaborate to use (decision **D9**, §18.14). Step 15 cuts it back to the feature that was
> actually asked for: capture what the utility generated, compare it against what ended up in the
> application database, show one accuracy number and a field-level diff. Progress is tracked in
> the §18.16 status table.
>
> **Removed in step 15** — the three-lane baseline comparison and its four extra verdicts
> (old §18.8.1), the calibration queue / override / known-differences workflow (old §18.8.2),
> LLM run metadata and the cost leaderboard (old §18.12), the relational assertions (old §18.9),
> and seven of the eight scores. Section numbers of surviving material are unchanged so the
> `implementation.md` changelog still resolves; the removed sections are gone rather than
> renumbered. Git history holds the full earlier design if any of it is ever wanted back.

### 18.1 Purpose

The utility generates a synthetic bond deal, renders it as a broker email (§17) and sends it. The
Genesis email-parsing agent reads that email and writes issuance rows into the PBI application
database. Nothing recorded *what those rows should have contained*, so agent accuracy could not be
measured.

This feature makes the utility the source of ground truth. When an email is generated, the utility
also writes the **expected** database state for it into its own mirror tables. Later the rows the
agent actually produced are pulled from the app DB (or imported as a CSV export) and diffed
field-by-field against that expectation, producing one accuracy figure and a per-field diff.

Two lanes only:

| Lane | `util_source` | Produced by |
|---|---|---|
| **Expected** | `expected` | The utility, at email-generation time |
| **Actual** | `actual` | The agent |

> The `baseline` value remains legal in the store's `util_source` column so old rows still load,
> but nothing writes or reads it. There is **no LLM connectivity anywhere in this utility** and
> never was — it neither calls a model nor records anything about one.

### 18.2 The four application tables

Ground truth is expressed in the shape of the four tables the ingestion pipeline writes. Column
lists and lengths come from the `information_schema` exports (`issuance_*_fields_size.csv`).

| App table | Grain | Role |
|---|---|---|
| `t_issuance_deal` | one row per **deal** | Deal-level rollup (issuer, registration type, tranche count, currencies). |
| `t_issuance_data` | one row per **update, per tranche** | Append-only audit. Every BBG/DRB/email update lands here. |
| `t_issuance` | one row per **tranche** | Current state — the row the UI reads. Last-write-wins projection of that tranche's audit rows. |
| `t_issuance_security` | one row per **identifier set, per issuance** | ISIN / CUSIP / FIGI + registration type + status. |

For a first-announcement email (all the utility sends today) each tranche produces exactly one
audit row, one current-state row, and one security row per non-empty identifier set — so a
`144A/Reg S` tranche expects two (D2). A deal produces one `t_issuance_deal` row.

`t_issuance` is a **projection, not a superset**: seven scored `t_issuance_data` columns do not
exist on it (`issuer_name`, `datasource_id`, `bookrunners_as_supplied`, `bnd_bank_as_supplied`,
`rating_outlook`, `issuance_bond_type`, `security_status`), so the issuer is identified there by
`issuer_ticker`. 133 columns are shared.

### 18.3 Mirror tables

```
util_issuance_deal      util_issuance_data      util_issuance      util_issuance_security
```

A `util_` **prefix**, and the app's column names kept byte-identical, so `util_issuance_data` and
`t_issuance_data` diff column-for-column with no aliasing. Utility metadata rides in
`util_`-prefixed extra columns that cannot clash with a future app column: `util_row_id`,
`util_run_id`, `util_email_id`, `util_source`, `util_asset_class`, `util_deal_seq`,
`util_tranche_seq`, `util_match_key`, `util_created_at`.

Supporting tables: `util_run` (one per capture run), `util_email` (one per generated email),
`util_comparison` and `util_comparison_field` (one compare pass and its findings).

**Storage: SQLite**, one file at `backend/expected/pbi_util.db`, created on first write. Zero
setup, no credentials, no network — so capture works in `dry_run` / `email_only`, and the utility
can never write to the environment under test. The file is gitignored: it is runtime data, and
`init_store()` rebuilds it. **Reads degrade to empty when it does not exist** — a machine that has
never captured anything answers "no runs", not an error.

### 18.4 Schema reference data

```
backend/reference/db_schema/
  issuance_deal_fields_size.csv        53 columns
  issuance_data_fields_size.csv       180 columns
  issuance_fields_size.csv            146 columns
  issuance_security_fields_size.csv    12 columns
  field_map.csv                       391 rows — every column, exactly once
  vocab_map.csv                        16 generated-value → DB-value translations
```

These drive three things: **DDL generation** (a refreshed export picks up a new app column with no
code change), **length validation** of every expected value against `character_maximum_length`
(over-length values are warned, never truncated, never fatal), and the **comparison surface** — the
comparator only walks columns present in the map.

### 18.5 Field map: payload → columns

`field_map.csv` is the authority and is hand-editable. One row per `(table, column)`:
`asset_class, table, column, source_kind, source, tier, normalizer, notes`.

- `source_kind` ∈ `payload` · `derived` · `const` · `pool` · `email_meta` · `system` · `null`.
  Only the first five are projected; `system` and `null` stay NULL and are not compared.
- `tier` ∈ `1` · `2` · `3` · `x`. Tiers 1–3 are **compared**; `x` is not. The tiers no longer carry
  scoring weights (removed with the eight-score model) — they survive as a severity hint shown in
  the diff, and as the compare / don't-compare switch.
- `normalizer` — see §18.8.

Coverage: **133 compared `(table, column)` rows** of 391 — 44 tier 1, 62 tier 2, 27 tier 3; the
other 258 are `x`. (Recounted from the CSV 2026-08-10; the earlier figures — 145 / 46 / 70 / 29 —
were wrong.) Those 133 rows are only **69 distinct column names**: 52 of them appear in more than
one table, because `t_issuance` is a projection of `t_issuance_data` (§18.2) and both carry the deal
rollup's fields. **69 concepts, not 133 fields** — which is what §19.6 builds the labelling form
from. If the agent populates an `x` column that is reported as `extra` in the diff and does not
affect the score — usually informative, occasionally a hallucination, always a human judgement.

Adding a straight payload copy needs only a CSV row. Anything derived needs a resolver in
`expected_writer._RESOLVERS`, and a missing one is reported as a warning rather than silently
dropped.

### 18.6 Score only what the email actually said

A field the utility generated but the chosen template never printed **cannot** be extracted by the
agent. Counting it would understate accuracy badly — a perfect extraction scores about 40%.

Each renderer in `email_builder.py` declares the set of columns it rendered; `build_email` returns
it as `meta["rendered_fields"]` alongside the subject and body, and it is stored on
`util_email.rendered_fields`. **Accuracy is measured over `field_map ∩ rendered_fields` only**, and
the UI states how many fields were excluded because the email did not mention them.

The spread between templates is real and worth watching — `bond_stacked` renders 26 mapped
columns, `bond_colon` 14 — and it is a template limitation, not a model failure.

**Every email format must render the ticker or a security identifier.** All row-matching keys are
either identifier-based or ticker-rooted (§18.7), so a template printing neither cannot be matched
at all: every row comes back unmatched and the run scores 0% regardless of agent quality.
`bond_colon` was found in exactly that state and given an `Issuer: <name> ( <ticker> )` line.

#### 18.6.1 The expectation holds the *normalized* value

Where the email states prose and the app stores a code, **the translation is the agent's job**, so
the expectation holds the post-translation value and the template is not simplified to make
matching easier:

| Rendered in the email | Expected in the DB | What the agent must do |
|---|---|---|
| `"The net proceeds … general corporate purposes …"` (~150 chars) | `use_of_proceeds` = `GENERAL CORPORATE PURPOSES` (`varchar(64)`) | classify prose → controlled vocabulary |
| `Moody's (Exp): Baa2/Stable` | `issuer_rating` = `Baa2/BBB/BBB-`, `rating_outlook` = `Stable` | split rating from outlook |

`vocab_map.csv` is the authority for generated-text → expected-DB-value, and `expected_writer`
stores the mapped value, never the raw prose. Only six columns use the `vocab` normalizer
(`registration_type`, `tranche_reg_type`, `coupon_frequency`, `day_count`, `use_of_proceeds`,
`rating_classification`); columns the app stores as supplied use `ci_text` and need no map entry.
The one hard rule left: never generate a value longer than a column the app copies **verbatim**
(`issuer_name` 100, `ipts` 120, `tranche_name` 120, `bond_seniority` 300).

**When our expectation turns out to be wrong, the fix is editing `field_map.csv` or
`vocab_map.csv` by hand.** There is deliberately no UI for it — the earlier calibration workflow
cost more to understand than the edit it replaced.

### 18.7 Row matching

The agent's rows carry app-assigned `deal_id` / `tranche_id` / `issuance_key`, so matching uses
business keys. Candidates are filtered by `issuer_ticker` (the primary filter, and why minted
tickers matter) and by an ingest time window, then matched in priority order:

| # | Key | Confidence |
|---|---|---|
| 1 | `isin` or `cusip` exact | **exact** — unique per tranche |
| 2 | `issuer_ticker` + `currency_code` + `preliminary_security_tenor` (all three required) | **high** |
| 3 | `issuer_ticker` + `maturity_date` (±3 days) + `total_issued_amount` | **high** |
| 4 | `issuer_ticker` + single remaining candidate | **low** — flagged |

Key 2 requires all three parts: with the tenor missing it degenerates to ticker + currency, which
every tranche of a single-currency deal shares, and would pair rows arbitrarily. `t_issuance_deal`
has no tranche key, so deal rows match on ticker alone. A key matching several candidates takes the
first and flags the pair `ambiguous`.

**Unique ticker per deal** (D8) is the `unique_ticker` engine param, on by default whenever capture
is on. Each deal's issuer keeps **its own ticker from `reference/bonds/issuers.csv`** — 4005
issuers, 4005 distinct tickers, and that file is the source of truth. The minter's only job is to
guarantee no two deals share one: it takes the reference ticker when it is free, and falls back to
deriving one from the issuer's name when there is none to take (an issuer with an empty `TICKER`
column, or a name invented by `_gen_company` when the CSV is missing) or when the reference ticker
is already spoken for because an earlier run used the same issuer.

The derived fallback reads like a real ticker rather than a random string: the first word gives its
leading letter plus its next consonants, each later word its initial, capped at 5 characters and
topped up from the name's letters if a short name (`3M Co` → `MCO`) leaves fewer than 4 —
`BREOTASOLUTIONS INDUSTRIES` → `BRTSI`, `FINACOVENTURES RESOURCES` → `FNCVR`. A clash re-spells the
stem (`FNCVR` → `FNCI` → `FNCN`). Uniqueness is enforced within the run and against every ticker
earlier runs used (`expected_store.known_tickers()`).

The run is therefore scoped by its **set** of tickers, one per deal, stored comma-separated on
`util_run.ticker` (written when the run ends, since the tickers do not exist until their deals are
built) and individually on `util_email.ticker`. `db_reader.fetch_rows` accepts the set and binds it
as an `IN` list — still an exact, index-usable match on `issuer_ticker`, falling back to `=` for a
single ticker. `/runs/{id}/fetch` prefers `util_run.ticker` and falls back to the emails' own
tickers, which covers a run stopped before it wrote the set back, and runs captured before this
change.

This *replaced* the original D8, which minted one random 6-character ticker per run and forced it
onto every deal, overriding the reference ticker. That was wrong twice over: two different issuers
appeared under one ticker in the emails, and because `t_issuance_deal` has no tranche key, deal rows
match on ticker alone (key 4) — so with several deals under one ticker only the first could ever be
matched at deal level and the rest were reported as unmatched. Issuer *names* varied per deal all
along; only the ticker was overridden.

Unmatched rows are reported both ways: **`unmatched_expected`** (the agent missed a tranche) and
**`unmatched_actual`** (it invented or duplicated one). "0 rows matched" is surfaced as a
matching/template problem, never as 0% accuracy.

### 18.8 Comparison

**Four verdicts:** `match` · `mismatch` · `missing` (expected non-null, actual null) · `extra`
(expected null, actual non-null — reported, never scored). A field equal only after normalization
is a `match`, shown with a marker.

**Normalizers** (`normalizer` column of `field_map.csv`). Every one copes with both storage shapes,
because the store holds TEXT (`'true'`, epoch-ms) while a Postgres fetch returns `'True'` and
`'1000.00000'`:

| Name | Rule |
|---|---|
| `text` / `ci_text` | trim + collapse whitespace; `ci_text` also case-insensitive |
| `numeric` | Decimal compare, epsilon `1e-6` — `1000` == `1000.00000` |
| `amount` | `numeric` plus email shorthand (`750mm` → `750000000`, `1.5bn`) |
| `date_day` | epoch-ms / epoch-s / ISO / `dd-MM-yyyy` → UTC date, compared at day granularity (`maturity_date` ±3) |
| `bool` | `true/TRUE/1/Y/Yes` ≡ true |
| `list_set` | split on `[,/;]`, trim, uppercase, compare as a **set** |
| `ratings` | `Baa2/BBB/BBB-` → multiset of agency ratings, order and `(Exp)` ignored |
| `tenor` | `5Y` ≡ `5 Y` ≡ `5 Year` ≡ `5` |
| `vocab` | translate through `vocab_map.csv`, then `ci_text`; an unmapped pair is a `mismatch` that names the pair |

**One score:**

```
accuracy = matched / (matched + mismatch + missing)     over field_map ∩ rendered_fields
```

reported beside the counts that explain it: fields compared, fields excluded because the email did
not mention them, rows matched, rows unmatched each way. Per-column results are persisted so a
worst-columns list can answer *which field does the agent get wrong most often* — the
prompt-improvement backlog.

### 18.10 Configuration

Per environment, `environments.<name>.db` — read-only credentials for pulling actual rows:
`enabled`, `host`, `port`, `database`, `schema`, `user`, `password`, `sslmode`, plus optional
`table_prefix`, `row_cap` and `statement_timeout_ms` rails.

Top-level `compare`: `store_path` (blank = default), `ingest_window_minutes` (60),
`expected_datasource` (`LLM` — the value the app stamps on an email-ingested row, D1),
`date_tolerance_days` (`{default: 0, maturity_date: 3}`), `auto_capture` (true).

`psycopg[binary]` is imported lazily, so the app starts without it and the CSV import route keeps
working.

### 18.11 Backend surface

| File | Responsibility |
|---|---|
| `backend/expected_store.py` | SQLite store: DDL from the schema CSVs, thread-locked writes, insert/query/export. Reads degrade to empty when the store is absent. |
| `backend/engines/expected_writer.py` | **Pure** projection: `(payloads, email_meta) → expected rows`. No I/O. |
| `backend/comparators.py` | **Pure**: normalizers, row matching, verdicts, accuracy. |
| `backend/db_reader.py` | Read-only Postgres fetch of the four tables by ticker + window. `SELECT` only, bound parameters, whitelisted identifiers, `READ ONLY` transaction, statement timeout, row cap, no write path. |
| `backend/email_ingest.py` | **Uploaded email sets (§19.4/§19.5).** `.msg`/`.eml`/`.html`/`.txt` → subject, sent date, sender, HTML body, text body; plus ISIN/CUSIP/FIGI extraction validated by check digit. Pure apart from reading the named file. |
| `backend/routers/compare_router.py` | The endpoints below. |

```
GET    /api/compare/runs?asset_class=bonds        → capture runs + latest accuracy
GET    /api/compare/runs/{id}                     → expected rows, emails, latest pass
POST   /api/compare/runs/{id}/fetch               → pull actual rows from the app DB
POST   /api/compare/runs/{id}/import              → CSV fallback (1–4 exports, lane=actual)
POST   /api/compare/runs/{id}/compare             → run a pass; persists and returns the score
GET    /api/compare/runs/{id}/diff?table=…        → field-level findings
GET    /api/compare/export/{id}?format=&lane=&table=
GET    /api/compare/db  ·  POST /api/compare/db/test
DELETE /api/compare/runs/{id}
```

**Engine integration.** Bonds carries `capture_expected` (default: on whenever `email_mode != off`)
and `unique_ticker`. In `emit_email`, immediately after `build_email` succeeds, the engine projects
and stores the expectation and logs one line per deal. Expectations are written whether or not the
send succeeded (`util_email.send_status` records which), it works in `dry_run` and `email_only`, and
**capture never fails a run** — any exception is caught and logged as a `warn`.

**CSV import** accepts the shape of the `Sample_issuance*.csv` exports: a quoted header of app
column names, `NULL` for nulls, headers matched case-insensitively, table inferred from the header
row. Unknown columns are warned and ignored. `export_csv` emits that same shape, so export→import
round-trips.

### 18.13 Deferred

Both need `expected_writer` rules only — mirror tables, matching and scoring already accommodate
them.

- **Email → API association / merge.** Sending an email and *then* an API POST for the same deal, to
  test whether the app associates rather than duplicates them (`deal_closest_match`). Needs
  sequenced emission with a wait, per-column merge precedence, and assertions that one `deal_id`
  covers both ingests and the audit keeps both rows. Backlog (D7).
- **Multi-update sequences.** Announce → Guidance → Launch → Priced, which is why
  `t_issuance_data` is append-only and `t_issuance` is one row. v1 emails a single announcement, so
  the lanes are 1:1.

### 18.14 Decisions

| # | Decision |
|---|---|
| D1 | ~~An email-ingested row is stamped `datasource_id` = **`LLM`**~~ — **contradicted by the live database (2026-08-07).** No row in QA2 carries `datasource_id = 'LLM'`; the real values are `BBG`, `DB`, `MAN`, `DRBK`, `DRB`, and `pbi.t_parsed_email_issuance_data` is empty — the parsing agent has not written into that environment. `compare.expected_datasource` is therefore `""` (no source filter); left as `LLM` the fetch matched the ticker and window and then discarded every row. **Read the value the agent actually stamps from its first real run and set it then.** |
| D2 | A `144A/Reg S` tranche expects **two** `t_issuance_security` rows, one per identifier set. Reported, not scored, until a live run confirms it. |
| D3 | Templates keep their prose; the **expectation** holds the post-normalization value (§18.6.1). |
| D4 | Actual rows come from a **read-only DB query**, with CSV import as the offline route. |
| D6 | An API POST lands as `datasource_id` = `BBG`, unaltered by the app. |
| D7 | Association / merge testing is **backlog**, not v1. |
| D8 | The email and any API post use the **same ticker** — a ticker identifies the issuer. **One ticker per deal, taken from the issuer's row in `issuers.csv`**, derived from the name only as a fallback (revised 2026-08-10; was one random ticker per run, overriding reference data, which put two issuers under one ticker and broke deal-level matching). A run is scoped by its *set* of tickers. |
| D9 | **The measurement framework is cut back to expected-vs-actual (2026-08-07).** Steps 1–14 built three lanes, ten verdicts, eight scores, a calibration workflow and a cost leaderboard. Each addition was individually defensible and individually agreed, but together they produced a tab that could not be used without a manual — the user's verdict on seeing it was "a complete mess … I am not sure how to use it". Removed: the baseline lane, three-way verdicts, the calibration/override/known-differences loop, LLM run metadata, the cost leaderboard, the relational assertions, and seven of eight scores. The lesson worth keeping: an accuracy harness earns its complexity only when someone can read its output without being taught it. |
| D5 | *(withdrawn by D9 — the baseline lane.)* |

### 18.15 UI — the Email Compare tab

One tab, between **CSV Upload** and **History**, with an asset-class control (Bonds active; Loans ·
ABS · Munis visible and disabled). Three panels, in the order you use them:

**1 · Runs** — one row per capture run: time · email format · deals/tranches · whether actual rows
are loaded · accuracy. Selecting a run opens it below. Row actions: delete, export.

**2 · Load actual rows** — a **Fetch from DB** button (window in minutes; disabled with its reason
when `db.enabled` is false or `psycopg` is missing) and a drop zone for 1–4 CSV exports. Reports
what it loaded.

**3 · Result** — the accuracy figure, then one line of counts that explains it (fields compared ·
fields the email did not mention · rows matched · rows unmatched), then the diff:
`field · tier · expected · actual · status`, problems first, with a toggle to show everything and an
`Export CSV` button.

Four status colours only, from the existing Operator tokens (§15): `match` emerald, `mismatch`
rose, `missing` attention, `extra` faint. No badges on the tab label. Nothing auto-runs: fetching,
comparing and deleting are all explicit clicks.

### 18.16 Build order and status

| # | Session | Deliverable | Status |
|---|---|---|---|
| 1–2 | S1 | Schema reference data + `field_map.csv` + `vocab_map.csv`; `expected_store.py` | ☑ Done (2026-08-06) |
| 3–4 | S2 | `expected_writer.py`; engine wiring, `rendered_fields`, unique tickers | ☑ Done (2026-08-06) |
| 5 | S3 | `comparators.py` | ☑ Done (2026-08-06) |
| 6–7 | S4 | `db_reader.py`; `compare_router.py` | ☑ Done (2026-08-06) — **live fetch verified end-to-end 2026-08-07** against TRP - QA2 (ticker `HTSC`: 130 data rows matched, 1 in window, 1+1+1 rows stored). Was "unexercised (no credentials)" — that note was stale |
| 8 | S5 | `EmailCompareTab.jsx` panels 1–4; Settings DB section; Bonds-tab toggles | ☑ Done (2026-08-06) |
| 9 | S6 | Calibration queue and override write-back | ☑ Done (2026-08-06) — **removed by step 15** |
| 10–11 | S7 | LLM runs, leaderboard, email artifact, export | ☑ Done (2026-08-06) — LLM runs and leaderboard **removed by step 15** |
| 12–14 | S8 | Visual pass; known differences + un-keep; delete LLM run | ☑ Done (2026-08-07) — 13 and 14 **removed by step 15** |
| **15** | — | **Simplification (D9).** Rebuild the tab as the three panels of §18.15; delete the baseline lane, the calibration / override / decision surface, LLM runs and the leaderboard from both frontend and backend; collapse the verdict set to four and the scores to one accuracy figure | **☑ Done (2026-08-07)** — 17 endpoints → 10, seven panels deleted, `comparators` 1,249 → 790 lines, `expected_store` 1,104 → 777, CSS 1,251 → 775, JS bundle 275 → 204 kB. Also fixed: `ci_text` now treats hyphens, underscores, case and spacing around a slash as formatting noise, so `Senior Unsecured` vs `Senior-Unsecured` is a match |

Status values: `☐ Not started` · `◐ In progress` · `☑ Done (YYYY-MM-DD)` · `⚠ Done with deviations`.

**The one open design question**, unchanged and blocked on a credentialed environment rather than
on work: does the app associate an email-ingested deal with a feed-ingested one, and is a repeated
ISIN/CUSIP deduped? One `both`-mode run against a filled-in `db` block answers it.

### 18.17 Build protocol — multi-session hand-off

The feature is built across the sessions in §18.16, each in a **fresh chat** to keep context cost down. State lives in the **repo**, never in chat history: the spec's status table, `implementation.md`'s changelog, and the code itself are the only things a new session needs.

**The loop:**

```
[review chat]  issues the hand-off prompt for session N
      ↓  (user pastes it into a fresh chat)
[build chat N] reads spec §18 → builds → verifies → updates §18.16 status
               + implementation.md → emits a COMPLETION REPORT
                 (ending with the hand-off prompt for session N+1)
      ↓  (user pastes the report back)
[review chat]  reviews against the spec → approves, or issues corrections
      ↓
[build chat N+1] …
```

#### 18.17.1 Rules for a build chat

1. **Read first, in this order:** `CLAUDE.md`, `master/spec.md` §18 (all of it), the §18.16 status table, then the files the session touches. Do not re-read the whole spec's other sections unless the step needs them.
2. **Build only the steps named in the prompt.** Do not "helpfully" start the next step — that breaks the review loop and duplicates work in the next chat.
3. **Verify before reporting.** Run the verification named in §18.16. Never report a step done on the strength of "the code looks right".
4. **Deviations are recorded, not hidden.** If the spec is wrong or impossible as written, implement what is right, update §18 in the same session, and list the deviation prominently in the report. A silent deviation invalidates every later session that trusted the spec.
5. **Leave the repo working.** `python -c "import ..."` for touched backend modules; `npm run build` for touched frontend. Both must pass at the end of the session.
6. **Update the two records:** flip the §18.16 status rows, and add a dated entry to `master/implementation.md` (files touched, decisions, caveats) per `CLAUDE.md`.
7. **Do not commit or push** unless the user explicitly asks.
8. **Do not touch** the existing tools' behaviour (Bonds POST path, Loans, Interest, TIG, CSV Upload) beyond the specific wiring the step calls for.

#### 18.17.2 Completion report format

The build chat ends its final message with exactly this, and nothing after it. It is written to be pasted into the review chat, so it stays compact — evidence, not narration.

```markdown
## Session <N> complete — steps <x–y>

**Built**
- <file> (new, ~<n> lines) — <one line on what it does>
- <file> (modified) — <what changed>

**Verified**
- <command or test> → <result>
- <command or test> → <result>

**Deviations from spec** (or "None")
- §<ref>: <what the spec said> → <what was built> → <why> → <spec updated: yes/no>

**Records updated**
- spec §18.16: steps <x–y> → ☑ Done (<date>)
- implementation.md: entry added under "Changes — <date>"

**Open questions for review**
- <anything needing a human decision, or "None">

**Not done / deferred**
- <anything in scope that was left out, and why, or "None">

---

### Hand-off prompt for session <N+1>

```
<the next session's prompt, per §18.17.3>
```
```

#### 18.17.3 Hand-off prompt template

Self-contained — it assumes the next chat knows nothing except that it is in this repo.

```markdown
Working in c:\python\PBI_Test_Utility (Windows, PowerShell). Read CLAUDE.md, then
master/spec.md §18 in full — that is the authoritative spec for this feature — and
the §18.16 build-order/status table.

Build **session <N+1>: steps <x–y>** only:
<one line per step, quoting the Deliverable column>

Context you need that isn't obvious from the code:
- <carry-forward facts: shapes chosen, names used, gotchas hit in session N>

Definition of done and verification are in §18.16 for each step. Follow the build
rules in §18.17.1 — in particular: build only these steps, verify before reporting,
record any deviation in both the code and §18, leave backend imports and
`npm run build` passing, update the §18.16 status rows and add an
implementation.md changelog entry, and do not commit.

Finish with the completion report in the §18.17.2 format, ending with the hand-off
prompt for session <N+2>.
```

#### 18.17.4 Review chat

Reviews the pasted report against the spec and answers one question: *is this step actually done, and did it change anything the next step depends on?* It checks the deviation list hardest — a deviation that went unrecorded in §18 will silently break a later session. It then either issues corrections, or approves and re-issues the next hand-off prompt (adding any carry-forward facts the report surfaced).

## 19. Uploaded Email Sets (Bonds)

> Status: **designed, not built** (agreed 2026-08-10). §18 measures the agent against emails the
> utility *generated*. This section measures it against a **static sample of real client emails the
> user already holds** — 20–50 `.msg` files, mixed broker formats, most carrying an ISIN or CUSIP.
> Progress in the §19.14 status table.

### 19.1 Why this is a different feature, not a flag on §18

In §18 the email is **downstream of the truth**: the utility invents a deal, `expected_writer`
projects the payload into expected rows, and the renderer prints a subset of them. Ground truth is
free because the utility authored the deal.

An uploaded email inverts that arrow. All the utility has is prose. Nothing in the codebase can turn
a real broker email into 145 typed column values, and three of the four ways to try are wrong:

| Route | Verdict |
|---|---|
| Deterministic parser per format | **Rejected.** The sample is mixed real broker mail — there is no single format to target, and a parser bug is indistinguishable from an agent error in the output. |
| LLM extraction | **Rejected.** Ground truth would be exactly as fallible as the subject under test — a parser marking a parser. There is no LLM connectivity in this utility (§18.1) and this does not introduce any. |
| Sidecar truth file supplied with the emails | **Unavailable.** The user has no such file; producing one is the same labour as labelling, without the assistance. |
| **Human labelling inside the utility** | **Chosen (D10).** The user reads the email and states what the app should have stored. The utility's job is to make that cheap, validate it, and make the effort durable. |

Everything downstream of the expectation is **reused unchanged**: `comparators.compare_run`, the
four verdicts, the single accuracy figure, the diff, `db_reader`, the export. Neither the comparator
nor the reader knows or cares where a lane came from.

### 19.2 The two phases

**Phase A — upload and inspect (no score).** Upload the `.msg` set; the utility parses, stores and
displays each email, extracts its identifiers, pulls the rows the agent produced for those
identifiers out of the app DB, and shows them beside the email. Nothing is labelled and no accuracy
is reported.

Its purpose is to prove the *pairing* works before any labelling time is spent. The live fetch itself
is verified (§18.16), but only against a **minted** ticker — one deal, one unique fake ticker, an
empty history. Real tickers carry years of deals in the same table (`t_issuance_data` holds 179,185
rows), so ticker + window is not a usable filter here; §19.8 replaces it with identifier matching,
and that is the part phase A tests. Phase A is also useful on its own as an inspection view.

**Phase B — labelling and scoring.** Add the labelling form, write the `expected` lane from it, and
the existing accuracy figure and diff light up in the panel that already renders them. Phase B is
purely additive: phase A ships nothing that phase B discards.

### 19.3 Prerequisite — the agent must have ingested the sample

**This feature can produce nothing until the 20–50 sample emails have been fed through the Genesis
email-parsing agent into the environment being read.** As of 2026-08-07,
`pbi.t_parsed_email_issuance_data` in TRP - QA2 is **empty** and no row in the four issuance tables
carries an email-ish `datasource_id` (D1). Phase A against today's QA2 would upload, parse and
display correctly and then fetch **zero actual rows** — which is a true answer, not a bug.

**The cause is known and is a deployment timeline, not a data question: the email-parsing code is
not yet deployed to QA2** (confirmed by the user, 2026-08-10). So the empty table is expected and
needs no investigation. What it means for sequencing is that the environment the utility reads must
be **the one the parser is deployed to** — if that turns out not to be QA2, the `db` block for that
environment needs filling in (only TRP - QA2 and TRP - QA - Automation have credentials today;
TRP - QA1 and HSBC - QA1 have `enabled: false` and blank hosts). Phases A and B can be built and
unit-verified against an empty fetch in the meantime; only the end-to-end score waits on the deploy.

Two things to settle on the first real agent run, both of them one-line config edits:

- **What `datasource_id` does the agent stamp?** Set `compare.expected_datasource` to it. Left wrong
  the fetch matches the identifier and then silently discards every row.
- **Does the agent's output land in the four `t_issuance*` tables, or in
  `pbi.t_parsed_email_issuance_data`?** The utility reads only the former. If the agent writes to a
  staging table first, `db_reader` needs one more table in its whitelist — small, but unknown until
  observed.

### 19.4 Ingesting `.msg`

`extract-msg` (new dependency), **not** Outlook COM. COM works — pywin32 is already in
`requirements.txt` and `outlook_sender.py` proves Outlook is present — but it is slow, needs Outlook
running, and fails in any headless context. `extract-msg` is offline and yields headers, subject and
HTML body directly. `.eml` (stdlib `email`), `.html` and `.txt` are accepted on the same route at
no extra cost.

Per file the utility takes: subject, sent timestamp, sender, and the HTML body (falling back to
text wrapped in `<pre>`). The original file is copied to
`backend/expected/uploads/<util_run_id>/<n>.msg` and referenced by `util_email.msg_path`, so the
store remains reproducible from its own directory. Real broker mail is table-heavy HTML and is
rendered in a **sandboxed iframe** (`srcDoc`, no `allow-scripts`) — never
`dangerouslySetInnerHTML`.

Attachments, embedded images and `.pst` archives are out of scope (§19.15).

### 19.5 The upload run

An upload creates the same three things a capture run creates, so panels 1–3 need no new concepts:

- **`util_run`** with `source = 'upload'`, `email_format = 'uploaded'`, `asset_class = 'bonds'`, a
  user-supplied label, and `ticker` = the set of tickers extracted across the set.
- **One `util_email` per file** — `subject`, `body_html`, `msg_path`, `ts` = the email's *own* sent
  date (not now), `ticker`, and `rendered_fields` empty until labelled. `send_status` = `'uploaded'`.
- **No expected rows** until phase B. A run with none is shown as *not labelled*, and `/compare`
  refuses it with a clear reason rather than reporting 0%.

`util_run.source` is the only structural addition; a missing value reads as `'capture'` so existing
rows are unaffected.

**Identifier extraction** is deterministic and runs at upload time: ISIN (`[A-Z]{2}[A-Z0-9]{9}\d`
**with its check digit validated**, which is what keeps ordinary prose out), CUSIP (9 alphanumerics,
check digit validated), and ticker candidates. Validation matters more than recall here — a false
ISIN pairs the email with someone else's deal, which is worse than no pairing at all. Extracted
identifiers are shown per email and are **editable**, because an email carrying none must still be
pairable by hand.

### 19.6 Labelling — authoring the expectation (phase B)

One email at a time, email on the left, form on the right. The form is generated from
`field_map.csv`, and **one field per distinct column name, not per `(table, column)` row** — the 133
compared rows are only **69 concepts** (§18.5), since `issuer_ticker` on `t_issuance_data` and on
`t_issuance` is one thing the email said once. Saving fans the concept out to every table that
carries it. Tier 1 first, grouped by grain (deal / tranche / identifier set).

Two inclusion rules, and the second is not obvious:

- Never show the 258 `x` columns... **except** the ones matching depends on.
  `preliminary_security_tenor` is tier `x` — not scored — but §19.8 key 3 matches on it. A form built
  from "compared columns only" would omit it and matching would degrade **silently**. The form's
  column set is therefore `compared ∪ matching keys`, and a matching-key field is marked as
  *needed for pairing, not scored* so its purpose is visible.
- Default to the columns real broker mail actually uses (§19.17) — about 25 of the 69 — with the
  rest behind *show all*.

Four rules make this tolerable at 20–50 emails:

1. **Blank means the email did not say it.** Not "missing", not zero — *excluded from scoring*
   (§19.7). Effort is therefore proportional to what the email actually contains, and a partially
   labelled email yields a valid narrower score rather than a wrong one. Ten tier-1 fields on day one
   is a legitimate starting point.
2. **Prefill from the email text**, marked as a suggestion, never persisted until saved (§19.6.1).
3. **Dropdowns for the six `vocab` columns** (`registration_type`, `tranche_reg_type`,
   `coupon_frequency`, `day_count`, `use_of_proceeds`, `rating_classification`), options from
   `vocab_map.csv`, so the user picks the **DB-side** value and normalization is never in play.
   Every other column uses `ci_text`/`numeric`/`date_day` and takes free text.
4. **Label once per tranche, not once per table.** One tranche form fans out through
   `expected_writer`'s existing rules: one `util_issuance_data` row, one `util_issuance` row (minus
   the seven columns `t_issuance` does not have, §18.2), one `util_issuance_security` row per
   non-empty identifier set — two for `144A/Reg S` (D2) — and one `util_issuance_deal` row per deal.

Length validation against `character_maximum_length` already exists and applies here: over-length
values warn, never truncate, never block (§18.4). `copy from previous email` seeds the form from the
last labelled email in the run, since the second email of a similar shape is mostly the same again.

`expected_writer` gains a second **pure** entry point — `from_label(label, email_meta) -> rows` —
which takes stated values directly and skips the payload resolvers. The existing payload path is
untouched.

#### 19.6.1 Prefill comes from the email, never from the fetched rows (D11)

Two kinds of assistance look similar and are not:

- **From the email text** — regex for the machine-regular fields (identifiers, currency, `750mm`,
  coupon, maturity/settlement dates, tenor, ratings). **Safe.** The email is the source of truth, so
  reading it faster cannot corrupt the answer.
- **From the actual rows the agent produced** — prefilling the form with the agent's own output and
  asking the user to confirm. **Rejected.** It is marking an exam by copying the candidate's answers
  into the answer sheet: a skimmed confirmation of 40 fields scores near 100% because the user agreed,
  not because the agent was right. At 20–50 emails the saving does not buy enough to give up the
  meaning of the number. Loosening this later is easy; tightening it means re-labelling.

A suggestion is visually distinct from a confirmed value, and saving is what commits it.

#### 19.6.2 Assisted labelling is a chat-side workflow, not a product feature

The user may label a **seed set of 3–5 emails with an assistant's help in a chat** — the assistant
reads the email and proposes column/value pairs with reasoning, the user corrects and commits. This
does **not** contradict D10 and must **not** be built into the utility:

| | Chat-assisted seed labelling | An LLM inside the utility |
|---|---|---|
| Who commits the value | The user, per field | The model, automatically |
| In the scoring path | No — output is a hand-checked label | Yes, every run |
| Auditable | Yes, the reasoning is visible and argued with | No |
| Scales | Deliberately not — a seed set only | Yes, which is the problem |

**The honest caveat:** an assistant reading a broker email is itself a language model reading a
broker email, so where it and the parsing agent share a blind spot the label is not independent
ground truth. It is strictly better than confirming the agent's own output (§19.6.1) because the
source is the email rather than the answer, but the user is the arbiter on domain judgement —
broker shorthand that is conventional rather than literal, and cases where the correct DB
representation is a business convention the email does not state.

Where assistance is genuinely reliable: **which columns the email speaks to at all** (the
`rendered_fields` decision, §19.7, and the tedious part), vocab normalization through
`vocab_map.csv`, grain (deal vs tranche vs identifier set), tranche and identifier-set counts, and
**flagging genuine ambiguity** — a field the email states ambiguously is one the agent cannot fairly
be scored on and should probably be left blank.

**The seed set is also the requirements input for B2.** The form is currently specified over all 145
compared columns because `field_map.csv` is all there is to go on. If real broker mail only ever
speaks to ~30–40 of them, the form should default to those and put the rest behind *show all* — and
which ones those are cannot be known before reading real samples. **Do the seed set before building
B2**, and let it set the form's default column list and ordering.

### 19.7 `rendered_fields` = what the user labelled

Accuracy is scored over `field_map ∩ rendered_fields` (§18.6) — a field the email never mentioned
cannot be extracted and counting it would drop a perfect extraction to ~40%. A generated email gets
that set from its renderer. **An uploaded email gets it from the label**: the columns the user filled
in are, by definition, the ones the email stated. Written to `util_email.rendered_fields` on save,
exactly as before, so the comparator and the "fields the email did not mention" count need no change.

### 19.8 Row matching for real tickers (D13)

§18.7's filter is `issuer_ticker` + ingest window. Neither part survives here: the ticker is real and
has history, and the window is when *the agent* ingested the mail, which the utility cannot know
(the email's own sent date may predate ingestion by weeks). So an upload run matches differently:

| # | Key | Confidence |
|---|---|---|
| 1 | `isin` or `cusip` exact, via `t_issuance_security` -> `issuance_key` | **exact** |
| 2 | Manually pinned identifier (user-entered on the email, §19.5) | **exact** |
| 3 | `issuer_ticker` + `currency_code` + `preliminary_security_tenor`, window optional and user-set | **high** |

**Deal rows are reached through their matched tranches' `deal_id`, not by ticker.** `t_issuance_deal`
has no tranche key, so §18.7 matches deal rows on ticker alone — which is exactly what a real ticker
with years of deals breaks. Coming down from the matched tranches is both correct and cheaper.

`db_reader` therefore gains an identifier-first fetch mode: bind the ISIN/CUSIP set as an `IN` list
against `t_issuance_security`, take the surviving `issuance_key`s, and reach data/current/deal rows
through them. **Every existing read-only rail is unchanged** — `SELECT` only, bound parameters,
whitelisted identifiers, `SET TRANSACTION READ ONLY`, statement timeout, row cap, lazy `psycopg`
import. No new write path exists or is possible.

The securities-fan-out fix of 2026-08-07 stands: securities are reached only through parents that
survived filtering, never from unfiltered keys.

### 19.9 The label is durable (D14)

Each label is stored as JSON on a new support table `util_email_label`
(`util_email_id`, `util_run_id`, `label_json`, `label_status`, `updated_at`), so it can be reopened
and edited without reverse-engineering it out of the mirror rows. Re-saving **replaces** that
email's expected rows.

It is also exportable: the labelled set exports as the same CSV shape `/import` accepts
(§18.11), which means the sidecar file the user does not have today comes **out** of this feature.
Version it, re-import it into a fresh store, and those emails are never labelled twice. That, and
not the per-email form, is what makes 20–50 emails a one-time cost.

### 19.10 Backend surface

```
POST   /api/compare/uploads                          -> multipart: N files + label; creates the run
GET    /api/compare/uploads/{id}/emails               -> parsed emails + extracted identifiers
PUT    /api/compare/uploads/{id}/emails/{eid}/ids     -> correct/pin identifiers by hand
GET    /api/compare/uploads/{id}/emails/{eid}/label    -> form model + prefill suggestions  (phase B)
PUT    /api/compare/uploads/{id}/emails/{eid}/label    -> save; writes expected rows         (phase B)
GET    /api/compare/uploads/{id}/label/export          -> labelled set as importable CSV     (phase B)
```

Existing endpoints absorb upload runs with no signature change: `GET /runs` lists them (`source`
distinguishes), `POST /runs/{id}/fetch` gains an identifier mode, `/import`, `/compare`, `/diff`,
`/export` and `DELETE /runs/{id}` are unchanged. `/compare` refuses an unlabelled run with a reason.

### 19.11 UI

The Email Compare tab keeps its three panels and gains an **Emails** panel between 1 and 2, visible
only for a run whose `source` is `upload`:

- **Panel 1 · Runs** — upload runs listed alongside capture runs, marked, with a *labelled n/N*
  column. A drop zone for `.msg` files creates one.
- **Panel 1b · Emails** — one row per uploaded email: subject · sent date · identifiers · labelled?
  Expanding shows the email in the sandboxed iframe, and (phase B) opens the labelling form.
- **Panel 2 · Load actual rows** — unchanged, except the fetch reports it matched by identifier and
  says how many emails yielded none.
- **Panel 3 · Result** — unchanged. For an unlabelled run it states why there is no score instead of
  showing 0%.

Operator tokens and the four status colours only (§18.15). Nothing auto-runs: uploading, fetching,
labelling, comparing and deleting are all explicit clicks.

### 19.12 Configuration

No new `db` settings. `compare` gains `upload_dir` (blank = `backend/expected/uploads`) and
`identifier_match_only` (default true — do not fall back to ticker + window for upload runs unless
asked). `db.mirror_enabled` is read by `db_reader` and **used by nothing**; it is inert today and
this feature does not change that.

### 19.13 Decisions

| # | Decision |
|---|---|
| D10 | Ground truth for an uploaded email is **authored by a human in the utility**. No format parser (the sample is mixed real broker mail), no LLM (it would make the ground truth as fallible as the subject). The utility's job is to make labelling cheap, validated and durable — not to guess. |
| D11 | Prefill the label form **from the email text only, never from the fetched actual rows** (§19.6.1). Confirming the agent's own output as ground truth scores agreement, not accuracy. |
| D12 | A blank field means *the email did not state it* and is **excluded from scoring**, not counted as missing. Partial labelling therefore yields a valid narrower score, which is what makes incremental labelling safe. |
| D13 | Upload runs match by **identifier (ISIN/CUSIP)**, not ticker + window, and deal rows are reached through their matched tranches' `deal_id`. Real tickers have years of history in the same tables, and the agent's ingest time is unknowable to the utility. |
| D14 | The label is stored as JSON and **exports as an importable CSV**, so 20–50 emails are labelled once and never again. |
| D15 | `.msg` is read with `extract-msg`, not Outlook COM — offline, headless-safe, no Outlook process. |
| D16 | **Where the app's LLM prompt specifies a mapping, that prompt is the authority for `vocab_map.csv`** (§19.18). A guessed entry is a rule disagreement that reads as an agent failure. The limit this accepts: the score then measures *compliance with the specified mapping*, not whether the mapping is correct business logic. |
| D17 | **Accuracy is reported split by provenance — `extracted` · `derived` · `stamped` — beside one headline figure**, and every diff row carries its provenance label (§19.20.1, agreed 2026-08-10). Roughly 27 of the 72 compared concepts are not agent extractions, so a single blended number moves for reasons no prompt change can address and sends people to edit prompts that were never involved. The split is a group-by on `field_map.source_kind`, which the comparator already loads. |

### 19.14 Build order and status

| # | Deliverable | Verification | Status |
|---|---|---|---|
| A1 | `.msg`/`.eml` parsing + identifier extraction with check-digit validation | Parse the real sample offline; identifiers correct on every email that has one, none invented on emails that do not | **☑ Done (2026-08-10)** — `backend/email_ingest.py` + `backend/tests/test_email_ingest.py`. **148 assertions pass** against the 10-file corpus (63 with a clean skip when the gitignored samples are absent). 37 ISINs / 37 CUSIPs / **0 false positives**; 9 of 10 emails matchable, the tenth correctly flagged for a manual pin. Findings in §19.21 |
| A2 | `POST /uploads` + upload run/email storage | Upload the set, reopen the app, run still lists with its emails | **☑ Done (2026-08-10)** — **one endpoint, zero DDL**: `util_run.tool='upload'` is the marker and `add_run`/`add_email` already existed. `util_run.source` and the `util_email_label` table were **cut** (§19.22) |
| A3 | Identifier-first fetch in `db_reader`; deal rows via matched tranches | Live fetch against the sample's identifiers; read-only rails unchanged | **☑ Done (2026-08-10)** — `fetch_rows_by_identifier`, verified against the live database with identifiers taken from it; join path corrected by probing rather than assumed (§19.22.1) |
| A4 | Panels 1 + 1b: run list, email list, sandboxed body | `npm run build`; real sample renders | **☑ Done (2026-08-10)** — drop zone, upload rows marked in panel 1, Emails panel with a sandboxed iframe. `npm run build` clean; **not visually verified** (no browser available in the build session) — the data contract the component reads is asserted instead. Editing identifiers deferred (§19.22) |
| B1 | `expected_writer.from_label` (pure) + fan-out to the four mirror tables | Dry unit check: one tranche label -> 1 deal + 1 data + 1 issuance + n security rows | **☑ Done (2026-08-10)** — verified on the real multi-issuer USB label: 2 deals, 2 tranches, 3 security rows, vocab applied, `t_issuance` correctly omitting the seven columns it lacks |
| B2 | Label form model, vocab dropdowns, length validation, text prefill | Label one real email end to end; suggestions never persist unconfirmed | **☑ Done (2026-08-11)** — `label_form.py`, `LabelForm.jsx`, `GET`/`PUT`/`DELETE …/label`, `util_email_label`. Default form is **33 fields**, derived by counting the ten samples' own row labels (§19.23) |
| B3 | `rendered_fields` from the label; `/compare` enabled for upload runs | Accuracy over labelled columns only; unlabelled run refused with a reason | **☑ Done (2026-08-11)** — fell out of B2 and is verified by the perfect-agent round trip: **100%, 22/22 rows paired**. An unlabelled run is refused with a reason, and a labelled run with no application rows is refused with a *different* one |
| B4 | Label export/import round-trip | Export, wipe the store, re-import, score is identical | **☑ Done (2026-08-11)** — `GET/POST …/labels`. Verified the hard way: export, **delete the run**, re-upload the emails, restore, re-score — identical accuracy over identical field counts. Entries match on **identifiers**, not row ids, so an export restores into a fresh store |
| B5 | **Provenance split (D17)** — group findings by `source_kind` into `extracted` / `derived` / `stamped`, report the three beside the headline figure, label each diff row | A run whose only mismatches are `derived` shows 100% extracted; the diff names which is which | **☑ Done (2026-08-11)** — computed on read from `field_map.source_kind`, so **no migration** and a map correction fixes old passes too. Only non-extracted rows are tagged in the diff; tagging everything would be noise |
| — | *Reference-data edits (§19.19.3)* | 17 assertions through `compare_field`; imports clean | ☑ Done (2026-08-10) |

**Blocked on §19.3, not on work:** until the sample emails have been through the parsing agent into
a readable environment, A3 and everything in phase B can be built but not *validated* — the fetch
will correctly return nothing.

### 19.15 Out of scope

- `.pst` archives, attachments, embedded images.
- Multi-update sequences — **deferred to phase C (§19.16), not refused permanently.**
- Loans, ABS, Munis. Bonds only, as with §18.
- Any write path to the application database. There is none and there will not be one.

### 19.16 Phase C — update sequences (deferred, with a plan)

The user expects announce → guidance → launch → priced sequences from the client. **This is
supportable and the schema already carries what it needs** — it is deferred because building it
before the samples exist means guessing at their shape, not because it is blocked.

What the app does with a sequence (§18.2): every update appends a row to `t_issuance_data`, while
`t_issuance` stays **one** row per tranche holding the last-write-wins projection. So a 4-email
sequence expects **4 audit rows and 1 current-state row**, where §19.8 assumes 1 and 1.

`t_issuance_data` has the discriminators to tell those four rows apart, which is what makes this
tractable: `insert_time` (ordering), `tranche_state` / `tranche_status` / `book_status`, and the
stage-specific columns that only a later update populates — `guidance`, `launch_price`,
`re_offer_price`, `size_final`, `final_terms`, `final_price`, `final_spread`, `final_oid`,
`final_yield`, `tranche_pricing_date`.

Four changes, in order of size:

1. **Labelling** gains two fields per email: the tranche it updates, and its stage
   (`announcement` · `guidance` · `launch` · `priced`). Emails then group into an ordered sequence.
2. **Matching** becomes one email → one audit row *within* an already-matched tranche: order the
   tranche's audit rows by `insert_time` and pair positionally, cross-checked against the stage
   markers above. Tranche identity is still established by identifier (§19.8) — this is a second
   level below it, not a replacement.
3. **`t_issuance` is compared against the merged sequence**, last non-null wins per column. That is
   the "per-column merge precedence" §18.13 names, and it is the only genuinely new logic.
4. **The accuracy denominator** becomes the union of the sequence's `rendered_fields` for the
   current-state row, and each audit row keeps its own email's set.

Roughly phase B in size, almost all of it in `comparators.py` and the labelling form.

**Until it exists, an upload set containing a sequence must be detected and refused, not scored.**
Two emails labelled onto the same tranche collapse onto one match key under the 1:1 assumption and
produce a wrong-but-plausible number. Refusing with *"3 emails share a tranche — update sequences
are phase C"* is the correct behaviour: absent beats wrong. Detection is cheap — it is the same
identifier grouping matching already does.

### 19.17 What the real samples showed (7 emails, 2026-08-10)

The user supplied seven real announcement emails as screenshots: **USB** (multi-entity, 3 tranches),
**SYF** (perpetual preferred), **Amazon CAD** (free-form, 5 tranches), **NextEra/NEE** (3 series,
guaranteed), **SpaceX/SPCX** (5 tranches, 144A/RegS), **SUMIBK** (deliberately messy formatting,
5 tranches), **ING/INTNED** (green, 2 tranches). They are the requirements input §19.6.2 asked for.

**Four things they confirm.**

- **D2 is real, not theoretical.** Three of seven print dual identifier sets per tranche —
  `144A: US86561SQA13 / REGS: USU8531HAA87` — and `field_map` already has
  `sec_144a_isin/cusip/ticker/figi` and `sec_regs_*` as **tier 1**. SpaceX alone expects 5 tranches
  × 2 sets = **10 `util_issuance_security` rows**.
- **D10 (no parser) is settled by one line.** SUMIBK prints
  `Coupon Type Fixed to Float Floating Fixed to Float Fixed to Float Fixed to Float` — five tranche
  values, space-delimited, where the values themselves contain spaces. No deterministic segmentation
  of that string exists. Its `Tenor 6-NC5 6-NC5 8-NC7 11-NC10 21-NC20` line is splittable; the coupon
  line is not, and both are in the same email.
- **D12 (blank = not stated) does the heavy lifting.** Size is `USD Benchmark` / `C$ Benchmark` /
  `$Benchmark` in five of seven — a placeholder, not a number — and NextEra and SYF print `[●]%` for
  the rate and `[●] T+3` for settlement. These are **pre-pricing announcements**, so coupon, price,
  spread, and every `final_*` column are genuinely absent and must be excluded, not scored as missed.
- **Manual identifier pinning is mandatory, not a nicety.** The Amazon CAD email carries **no ISIN,
  no CUSIP, and no ticker** — "AMAZON" appears only in the forwarded subject line.

**Five things they break.**

1. **One email can be more than one deal.** The USB email prints two different legal issuers —
   `US Bank NA/Cincinnati OH` and `US Bancorp` — under **one ticker (USB)**, with different formats
   (`3a2` vs `SEC Registered`) and rankings (`Senior Bank Note` vs `Subordinated Note`). §19.5's "one
   `util_issuance_deal` row per email" is wrong, and the ticker cannot discriminate. NextEra is the
   converse: issuer **plus a guarantor** entity, one deal. **How many deals an email describes is a
   labelling input, not something derivable** — the form needs an explicit deal-grouping control.
2. **The form must include matching-key columns even at tier `x`** — fixed in §19.6.
3. **Matching key 3 is effectively dead here.** It needs `total_issued_amount`, which five of seven
   do not state. Real pairing rests on key 1 (identifier) and key 2 (manual pin), so **identifier
   extraction quality is the whole ballgame** — and Amazon has neither.
4. **CUSIP extraction must strip intra-token whitespace.** SUMIBK prints `86562M EN6`, `86562M EP1`,
   `86562M EQ9` — broken by formatting. Check-digit validation on the raw token **fails and yields no
   CUSIP silently**. Candidates must be whitespace-normalized before validation. (Its ISINs are clean,
   so that email would still pair — by luck, not design.)
5. **`bookrunners` normalization is wrong for real syndicate lists.** SpaceX moves the B&D marker per
   tranche: `JPM(B&D), MS` on one, `CITI(B&D), GS` on another. `list_set` splits on `[,/;]` and
   uppercases, so `JPM(B&D)` and `JPM` are different tokens and the same bank reads as a mismatch.
   The fix is not a normalizer hack: the emails literally say `Active: USB(B&D), JPM, RBCCM`, so the
   label puts the clean list in `bookrunners` and the B&D bank in **`bnd_bank`** (tier 2, `ci_text`,
   already in the map) — which is what those two columns are for.

**Gaps in the reference data these expose** — all hand-edits to two CSVs, no code:

| Gap | Detail |
|---|---|
| `registration_type` vocabulary | Real values include **`3a2`** (USB) and `144A/REGS with Registation Rights` (SpaceX — **typo is in the source email**). `vocab_map.csv` has only `Reg S`, `144A`, `144A/Reg S`. |
| `coupon_type` is `ci_text`, not `vocab` | It is **tier 1**, and real values are `Fixed to Float`, `Floating`, `Fixed Rate Reset`, `Fixed`, `Fixed-to-Fixed Reset Rate`. Case-insensitive text matching against whatever the app stores is a high-mismatch bet on a tier-1 field; it probably needs to become `vocab` with a map. |
| `use_of_proceeds` is genuinely ambiguous | ING prints "General Corporate Purposes" **and** a full Green Bond Framework paragraph; SUMIBK prints TLAC on-lending **and** general corporate purposes. Two defensible answers each — flag and agree a rule, or leave blank. |
| ESG unscored | `is_esg` · `esg_type` · `esg_comments` are all tier `x`, source `null`, yet ING leads with `ESG Theme: Green`. |
| Non-call unscored | **All seven** state a non-call structure (`3-NC2`, `30NC5`, `5-Years`, `6-NC5`, `15-NC10`, `40NC20`, Make Whole / Par Call). `nc_till` exists and is tier `x`. |
| Per-tranche ratings unscored | `tranche_rating` and `issue_expected_rating` are `x`; only `issuer_rating` (tier 2) is scored. But USB varies ratings **per tranche**, and NextEra and SUMIBK distinguish issuer from instrument/security ratings — so the rating that actually varies is the one not measured. |
| No guarantor name column | Only `guarantors_*_rating*` exist, all `x`. NextEra names a guarantor and there is nowhere to put it. |
| No `denominations`, no `listing` | Every email states denominations; SUMIBK and ING state a listing. No column exists — **nothing to score, and the form must not offer them.** |
| `tenor` vs `3-NC2` | The `tenor` normalizer handles `5Y` ≡ `5 Year` ≡ `5`. What `tenor` should hold for `3-NC2` (3? `3Y`?) is undecided, and every callable sample needs it. |

**Perpetuals work as specified.** SYF states `Maturity: Perpetual`, and the map has `is_perpetual`
(tier 3, `bool`) with `maturity_date` simply blank. Open question: SYF is **preferred stock** and
NextEra is **junior subordinated debentures** — whether those belong to `asset_class = bonds` and
what `issuance_bond_type` should hold is a business call, not a schema one.

**The payoff for B2.** Of the 69 compared concepts, these seven emails speak to roughly **25**:
`issuer_name` · `issuer_ticker` · `registration_type` · `tranche_reg_type` · `deal_currencies` ·
`db_number_of_tranches` · `currency_code` / `tranche_currency` · `tenor` ·
`preliminary_security_tenor` (pairing) · `maturity_date` · `is_perpetual` · `coupon_type` ·
`bond_seniority` · `ipts` · `tranche_settlement_date` · `total_issued_amount` (rare) ·
`issuer_rating` · `rating_outlook` · `bond_class` · `use_of_proceeds` · `bookrunners` · `bnd_bank` ·
`call_indicator` · `sec_144a_isin/cusip` · `sec_regs_isin/cusip` · `isin` / `cusip`.

**So the form is ~25 fields with an escape hatch, not 145.** That is the difference between a
labelling pass that takes minutes per email and one nobody finishes.

### 19.18 Corrections from the user on the sample findings (2026-08-10)

Five rulings on §19.17's open items. Four correct a wrong assumption in that section; the fourth
establishes a principle that outranks it.

**1 · `3a2` is `regulation_subcategory`, not `registration_type`.** The column exists on **all four**
tables and is already in `field_map.csv` — tier `x`, source `null`, noted *"bonds generator has no
source for this column"*, which is true of generated emails and **not** of real ones. §19.17's claim
that `3a2` was a missing vocabulary entry was wrong: it is a **different column**.

This makes the USB email's `Format` row **value-dependent**: `3a2` fills
`regulation_subcategory`, while `SEC Registered` (the other entity in the same email) fills
`registration_type`. One printed row, two possible destinations. The labelling form must present
both and cannot infer which from the row label alone — see the open input below.

**2 · `coupon_type` has an internal conversion mechanism in the app**, which the utility must carry
so the comparison is judged on the app's own terms. Consequences: `coupon_type` moves from
`ci_text` to **`vocab`** (it is **tier 1**, so this is the highest-value normalizer change in the
map), and the conversion table becomes `vocab_map.csv` rows. Pending the user's mapping (open input
below). Note `t_issuance.coupon_type_conflict` exists (tier `x`, source `system`) — the app itself
flags coupon-type conflicts, so a mismatch here may have a diagnosable cause rather than being a
flat wrong answer.

**3 · ESG is scored, and its values are given.** For ING's `ESG Theme: Green`: `is_esg` = **true**,
`esg_type` = **Green**. Both are currently tier `x` / source `null` on `issuance_data` and
`issuance`; both are promoted to compared and sourced from the label. `esg_comments` stays `x`
(free prose, no defensible expectation).

**4 · `use_of_proceeds`: "General Corporate Purposes" anywhere in the text wins**, mapping to
`GENERAL CORPORATE PURPOSES` — so ING (green framework paragraph *plus* that phrase) and SUMIBK
(TLAC on-lending *plus* that phrase) both resolve, and §19.17's "genuinely ambiguous" was wrong.

**The reason matters more than the ruling: the app's LLM is *prompted* with that mapping.** So the
rule is not ours to invent — it is a published contract we must copy. This generalises to a decision:

> **D16 — where the app's prompt specifies a mapping, that prompt is the authority for
> `vocab_map.csv`.** Any entry we guess at instead is a rule disagreement masquerading as an
> extraction error, and it will read as an agent failure in the diff. Obtain the prompt's mapping
> rules and transcribe them.

This refines §18.6.1 rather than contradicting it: the translation is still the agent's job, but the
*target* of the translation is defined by the app, not by us. **The honest limit:** an expectation
built from the app's own prompt measures whether the agent **complies with its specified mapping**,
not whether that mapping is correct business logic. Those are different questions and only the first
is in scope here.

**5 · SYF is a perpetual bond and NextEra is a bond; both are `asset_class = bonds`.** The user is
certain, and it is their platform's classification that governs — not the instrument's market
label. `is_perpetual` = true with `maturity_date` blank covers SYF, and NextEra's
`bond_seniority` = `Junior Subordinated`. No `issuance_bond_type` question remains.

**One labelling consequence worth building in:** the SYF email speaks in **dividend** language —
`Dividend Rate`, `Dividend Payment Dates`, `Liquidation Preference`, `per depositary share` — while
the columns are **coupon**-named (`coupon_type`, `coupon_frequency`, `coupon`). A labeller who does
not make that leap leaves the fields blank, and a blank is silently *excluded* from scoring (D12)
rather than flagged. So the form needs synonym hints on those fields — dividend ⇒ coupon. This is
the general hazard of D12: a labelling **omission** and a genuine *"the email didn't say it"* are
indistinguishable in the score. Hints on the fields where real emails use alternative vocabulary are
the cheap mitigation.

**Pending inputs from the user** — all three block `vocab_map.csv` / `field_map.csv` edits, none
block code:

| # | Needed | Why |
|---|---|---|
| i | **The app's LLM prompt mapping rules** (or the controlled vocabularies it is given) for `use_of_proceeds`, `registration_type`, `regulation_subcategory`, `esg_type`, `coupon_type` | D16. One artifact replaces most of the guesswork in `vocab_map.csv`, where 10 of 16 rows are currently `confirmed: no` |
| ii | **The `coupon_type` conversion table** | Tier 1, and `ci_text` will mismatch on every non-verbatim value |
| iii | **The `regulation_subcategory` vocabulary, and the rule for when a `Format` row fills it rather than `registration_type`** | The USB email needs both destinations from one row |

### 19.19 App vocabularies, supplied by the user (2026-08-10) — authoritative

Per **D16** these are copied from the application, not guessed, and supersede every `confirmed: no`
guess in `vocab_map.csv` for these three columns.

**`use_of_proceeds` — exactly three values:**

```
GENERAL CORPORATE PURPOSES · OTHER · REPAY OUTSTANDING BORROWINGS
```

**`registration_type` — eight values** (corrected 2026-08-10; the first list omitted `Reg S`):

```
144A · 144A/Reg S · Reg S · Global/IPO · Other · Sec Exempt · Sec Registered · Unknown
```

A tranche with only a Reg S set takes `Reg S` as its tranche registration type — the value is
carried up, not translated. The **same eight-value set applies at every grain**, so `144A` → `144A`
and `Reg S` → `Reg S` are identity mappings on the security row and on the tranche alike.

**`regulation_subcategory` — ten values:**

```
3(a)2 · Bearer · Category 1 · Category 2 · Category 3 ·
Dematerialized or Other TEFRA N/A · Registered TEFRA N/A ·
Without Reg Rights · With Reg Rights · YANKEE
```

`coupon_type` is **deferred** at the user's direction — the app's logic is complicated and will be
covered later. Until then it stays tier 1 / `ci_text` and **must be read as unreliable in any diff**:
it is the field most likely to show false mismatches (§19.18 item 2).

#### 19.19.1 Two bugs in the *existing* §18 feature, found by these lists

Both would mis-score a generated Bonds capture run **today**, on tier-1 and tier-2 columns, and both
would read in the diff as agent failures.

| # | Bug | Evidence |
|---|---|---|
| 1 | ~~**The generator emits a registration type the app cannot store**~~ — **withdrawn; there is no bug.** The user's first list of `registration_type` values omitted bare `Reg S`; the corrected list has **eight** values including it, and *"if only Reg S is there we just add it as the tranche registration type"* (2026-08-10). `bonds_engine`'s `["Reg S", "144A", "144A/Reg S"]` are all legal at every grain, and no generator change is needed. **The lesson is procedural: a vocabulary typed from memory is not the same artifact as the dropdown itself.** This one cost three reversals — ask for the control, not the recollection. | `bonds_engine.py:453` and `:473` |
| 2 | **`vocab_map.csv` maps `144A/Reg S` → `SEC Registered`, which is wrong twice.** `144A/Reg S` is *itself* a valid app value, so it maps to itself; and the app's spelling is `Sec Registered`, not `SEC Registered`. The row was a self-declared *"best guess - calibrate on first run"* — this is that calibration. | `vocab_map.csv`, `registration_type` and `tranche_reg_type` rows |

A third, smaller one: `use_of_proceeds` prose *"To refinance short-term debt…"* is mapped to
**`REFINANCING`**, which is **not** among the three allowed values. `REPAY OUTSTANDING BORROWINGS`
is the likely intent; `OTHER` is the safe alternative. The generator emits that prose 1 in 3 times
(`email_builder.py:110`).

**Open question these raise, for the app owners:** what *does* the app store for a **Reg S-only**
deal, given bare `Reg S` is not a legal value? Candidates are `Sec Exempt`, `Other`, or
`144A/Reg S` with the Reg S detail carried in `regulation_subcategory` (`Category 1/2/3` are Reg S
selling-restriction categories, which is suggestive). This also decides what
`expected_writer._security_rows` should put in a Reg S security row's `registration_type`
(`expected_writer.py:389` calls `vocab_value("registration_type", flavour)` with `flavour = "Reg S"`).

#### 19.19.2 The `Format` row resolves — one row, two columns

§19.18's open input (iii) is largely answered by the subcategory list itself, which contains
`With Reg Rights` / `Without Reg Rights` — qualifiers, not registration types. So a compound
`Format` string **splits across both columns**:

| Email prints | `registration_type` | `regulation_subcategory` |
|---|---|---|
| `144A/REGS with Registation Rights` (SpaceX — typo in source) | `144A/Reg S` | `With Reg Rights` |
| `144A/REGS` (ING) | `144A/Reg S` | — (not stated) |
| `SEC Registered` (US Bancorp, SYF, SUMIBK) · `SEC-Registered` (NextEra) | `Sec Registered` | — |
| `3a2` (US Bank NA) | **`Sec Exempt`?** — unconfirmed | `3(a)2` |
| not stated (Amazon CAD) | — | — |

Note the agent must also **renormalize the spelling**: emails write `3a2`, the app stores `3(a)2`;
emails write `REGS`, the app stores `Reg S`. That is exactly the kind of work §18.6.1 says is the
agent's job, so the expectation holds the app's spelling.

**One cell remains unconfirmed** — `3a2` → `Sec Exempt` is inference (3(a)(2) is an exemption from
registration and `Sec Exempt` is in the list), not something the user stated. Confirm before relying
on it; until then the USB email's first entity should have `registration_type` **left blank** rather
than guessed, since a blank is excluded from scoring and a wrong guess is a false mismatch (D12).

#### 19.19.3 Reference-data edits — ☑ applied 2026-08-10

Approved by the user and applied. `vocab_map.csv` **16 → 43 rows**; `field_map.csv` 391 rows
unchanged in count with **8 tier promotions**, taking the compared surface from **133 rows / 69
concepts to 141 / 72**. Originals backed up beside them as `*.csv.bak-20260810` (these files are
untracked by git, so there is no other way back). What was applied:

1. `vocab_map.csv` — replace the `registration_type` / `tranche_reg_type` guesses with identity
   mappings onto the seven real values; drop `SEC Registered` as a target.
2. `vocab_map.csv` — retarget the refinance prose from `REFINANCING` to `REPAY OUTSTANDING BORROWINGS`.
3. `vocab_map.csv` — add the ten `regulation_subcategory` values and the three `use_of_proceeds`
   values as a closed vocabulary, so an unmapped value is reported as a named pair rather than
   silently compared as text (§18.8, `vocab` normalizer).
4. `field_map.csv` — `regulation_subcategory` (all four tables) from tier `x` → compared; `is_esg`
   and `esg_type` from `x` → compared (§19.18 item 3).
5. ~~`bonds_engine` should stop emitting bare `Reg S`~~ — **not needed**, `Reg S` is a legal value at
   every grain (§19.19.4). No code change.

Also added, beyond the four planned: the email spellings the agent must renormalize —
`REGS` → `Reg S`, `144A/REGS` → `144A/Reg S`, `3a2` → `3(a)2`,
`with Registration Rights` → `With Reg Rights` — since real samples print all four (§19.19.2).

**Verified** by 17 assertions through `compare_field` with the real maps loaded: every fix matches as
intended, the old wrong target (`144A/Reg S` → `SEC Registered`) now correctly *mismatches*, and the
three newly-compared columns behave safely on a generated run — expected NULL against actual NULL
yields `not_compared`, not a penalty. `import main` and every compare-path module import clean.

**One code behaviour worth recording, found while verifying:** an **unmapped `vocab` value does not
force a mismatch.** `canon_equal` runs first, so two identical values that are absent from the map
still `match` via `_loose`; `unmapped` only appends the *"add to vocab_map.csv"* note to a mismatch
that had already happened (`comparators.py:468-476`). §18.8's phrase *"an unmapped pair is a
`mismatch` that names the pair"* overstates it — the pair must differ **and** be unmapped. This makes
the closed-vocabulary rows above a **quality** improvement (confirmed flags, phase-B dropdowns, less
note noise), not a correctness fix.

#### 19.19.4 `registration_type` is grain-dependent — and `vocab_map.csv` cannot express that

A user screenshot of the app's **Securities** section (2026-08-10) shows a 144A/Reg S issuance
fanned out into two security rows:

| Registration Type | Security Type | Sec Cusip | Sec Isin |
|---|---|---|---|
| `144A` | `144A` | `29261AAF7` | `US29261AAF75` |
| `Reg S` | `REGS` | `DO3810185` | *(empty)* |

**Three things this settles.**

1. **`expected_writer._security_rows` is already correct.** It iterates
   `(("Reg S", "SEC_REGS"), ("144A", "SEC_144A"))` and sets
   `security_type = "REGS" if flavour == "Reg S" else "144A"` (`expected_writer.py:382,396`) —
   which is exactly the two rows above. D2's two-rows-per-tranche and both `security_type` values are
   now confirmed against the real application, not inferred from a sample export. The earlier
   `SEC_REGISTERED` guess in that code comment is dead.
2. **The security row carries the identifier set's flavour** — `144A` or `Reg S` — and the tranche
   value is the same vocabulary (§19.19, eight values), so `144A` → `144A` and `Reg S` → `Reg S` hold
   at both grains. *An earlier draft of this section claimed the two grains had different closed sets
   and that `vocab_map.csv` needed a `table` key to express it. Both were wrong, on the strength of a
   value list that turned out to be incomplete — `vocab_map.csv` needs no schema change.*
3. **A Reg S security may have a CUSIP and no ISIN.** Matching already accepts either (§19.8 key 1),
   but a tranche where only the 144A set has an ISIN is normal, not defective data.

**Worth keeping from the wrong turn:** `vocab_map.csv` *is* keyed on `(column, generated_value)` with
no table — `vocab.get((column, text.casefold()))` (`comparators.py:276`), built as
`Dict[Tuple[str, str], Dict[str, str]]` (`expected_store.py:130`). No column needs a per-table
vocabulary **today**, so nothing must change; but if one ever does, the map cannot express it and the
fix is an optional `table` key (blank = all tables, specific wins over blank) touching
`load_vocab_map` and `_vocab_lookup`. Recorded so the constraint is not rediscovered.

**Nothing further blocks the §19.19.3 reference-data edits.**

**`3a2` → `Sec Exempt` remains unconfirmed** and the user does not know. Standing decision: the USB
email's first entity gets `registration_type` **left blank**, which is excluded from scoring (D12)
and costs nothing, while `regulation_subcategory = 3(a)2` is still labelled — so no information is
lost, only one unscored cell. A question for the app owners, not a blocker.

### 19.20 Derived fields measure the app, not the agent

Tranche-level `registration_type` is **derived by the application** (user, 2026-08-10). With the
corrected eight-value vocabulary (§19.19) the rule is fully known and has **no unknown case**:

| Security sets present | Tranche `registration_type` |
|---|---|
| 144A **and** Reg S | `144A/Reg S` |
| 144A only | `144A` |
| Reg S only | `Reg S` — carried up as-is |

Nothing needs to be left blank, no generator change is needed, and the flavour is *also* carried by
`is_144a` / `is_reg_s` (both tier 2, already in the map). But the fact that the app **derives** it
raises a question about what the accuracy figure means, and that question is not specific to this
column.

#### 19.20.1 The accuracy figure blends three different questions

Of the 133 compared rows, `field_map.csv`'s own `source_kind` splits them:
**83 `payload` · 27 `derived` · 15 `const` · 5 `pool` · 3 `email_meta`**. Those categories describe how
*the utility* projects its expectation, not how the app produces the value — but they are a strong
hint, and they sort the compared surface into three questions that are **not** the same question:

1. **Did the agent extract what the email said?** The real target, and the only one a prompt change
   can fix. `issuer_name`, `maturity_date`, `ipts`, the identifiers, `coupon_type`, `bond_seniority`.
2. **Did the app derive correctly from what the agent extracted?** Confirmed for `registration_type`;
   strongly implied for `db_number_of_tranches` (tier 1 — a count), `deal_currencies` (tier 1 — a
   rollup), `total_issuances`, `is_144a` / `is_reg_s`, `tenor`, `rating_outlook`, `is_unsecured`,
   `call_indicator`, `put_indicator`, `coupon_index`, `benchmark_for_pricing`,
   `tranche_settlement_period`, `bookrunners`. **15 distinct columns, two of them tier 1.**
3. **Did the pipeline stamp the right constants?** A plumbing check, not extraction at all —
   `datasource_id`, `security_status`, `book_status`, `tranche_state`, `pbi_deal_status`,
   `blast_status`, `priced`, `investor_status`, `issuance_created_by`, `deal_id_datasource`,
   `issuance_bond_type`, `security_type`. **~12 columns.**

So roughly **27 of the 69 compared concepts are not agent extractions.** §18.8 names the purpose of
per-column results as *"which field does the agent get wrong most often — the prompt-improvement
backlog"*, and only category 1 belongs in that backlog. Mixed together, the headline number moves for
reasons a prompt change can never address, and a derived-field mismatch sends someone editing a
prompt that was never involved.

**Recommendation: split the reported accuracy by provenance** — *extracted · derived · stamped* —
keeping one headline figure but showing the three beside it. This is cheap: `source_kind` is already
in `field_map.csv` and already loaded per request, so it is a group-by over findings the comparator
already produces, plus a provenance label in the diff. No new data, no new endpoint.

It also makes a derived mismatch **read correctly**: not "the agent got registration type wrong" but
"the app's derivation disagreed with ours", which is a different bug in a different place — and
sometimes it will be *our* derivation that is wrong, which is exactly the case §18.6.1 says to fix by
hand-editing the map.

**Open, one question per field and not urgent:** which of the 15 category-2 columns the app truly
derives versus copies. `registration_type` is confirmed. The rest can be settled cheaply from the
first real agent run by comparing the security-level input against the tranche-level output, without
asking anyone.

### 19.21 A1 built — what the 10-file corpus taught

`backend/email_ingest.py` (parse + extract, pure apart from reading the named file) and
`backend/tests/test_email_ingest.py` (**148 assertions**, committed). The user supplied **10 real
`.msg` files** — `backend/expected/samples/`, gitignored — covering ORCL · USB · SYF · *(no-identifier
format)* · NEE · ES · SPCX · SUMIBK · INTNED · one more US issuer.

**Result: 37 ISINs, 37 CUSIPs, 0 false positives, 0 spurious rejections.** Nine of ten emails are
matchable by identifier; the tenth states none and is flagged for a manual pin rather than guessed at.
ISIN and CUSIP pair 1:1 in every file, which is the expected structure — a US ISIN embeds its CUSIP.

**The screenshots were right about the design and wrong in their detail.** Three identifiers
transcribed from the screenshots failed their check digit; all three were **transcription errors**, not
bad data — the real values differ by one or two characters. The files were needed, and confirm the
samples are clean.

#### 19.21.1 Three bugs found by running against real files, in order of severity

1. **A silent loss — the worst kind.** The first draft used one whitespace-tolerant regex pass. A
   regex permissive enough to rejoin `86562M EN6` also **consumes what it scanned**, and `finditer`
   never revisits an overlapping start — so a match beginning at the *label* `ISIN` swallowed the
   first identifier on the line. Format 8 yielded **4 of its 5** tranches with nothing to indicate a
   loss. Fixed by **tokenising**: every token, and every run of 2–3 whitespace-adjacent tokens, is
   proposed as a candidate independently. Tokens joined only across whitespace, so `T+120-125` never
   fuses across its punctuation.
2. **False identifiers from prose.** Permitting internal whitespace let patterns run across word
   boundaries, and roughly **1 in 10** such strings passes a check digit by luck: `date August 15`
   and `and January 15` were accepted as **ISINs**, `2029 May 20` as a **CUSIP**. This is the exact
   failure §19.5 warns about — a false ISIN pairs the email to somebody else's deal. Two independent
   guards: **every fragment of a multi-token candidate must contain a digit** (`DATE` and `AND` do
   not; both halves of a broken CUSIP do), and an ISIN's first two characters must be a **current ISO
   3166-1 alpha-2 code** plus `XS`. Retired codes are excluded deliberately — `AN`, Netherlands
   Antilles, is what let `and January 15` through.
3. **Ordinary words reported as rejected identifiers.** `INDEBTEDNESS`, `REGISTRATION`,
   `COORDINATING`, `PARTICIPANTS`, `PRESENTATION`, `CONNECTIVITY`, `MANUFACTURES` — twelve letters
   whose first two are live country codes (`IN`, `RE`, `CO`, `PA`, `PR`, `BB`…). Nothing before the
   check digit excluded them, and they landed in the *rejected* list as noise. Fixed by requiring a
   candidate to be identifier-**shaped** before it can be reported as a failure: an ISIN ends in a
   digit. The CUSIP lane already had that guard; the ISIN lane did not.

#### 19.21.2 Design points confirmed or corrected by the corpus

- **The whitespace fix is necessary and sufficient.** SUMIBK's `86562M EN6` / `86562M EP1` /
  `86562M EQ9` are valid CUSIPs broken only by formatting; all three fail as raw tokens and validate
  when squeezed. Without it that email loses three of five tranches.
- **FIGIs must be excluded from the ISIN lane explicitly.** A FIGI is `BBG` + 8 + check digit — shaped
  exactly like an ISIN beginning with `BB` (Barbados), and its check digit is a different scheme, so
  ~1 in 10 would pass the ISIN test. They are matched first and kept in their own lane. `field_map`
  has `sec_144a_figi` / `sec_regs_figi` at **tier 1**, so extracting them is useful in itself.
- **CINS validates on the CUSIP algorithm** — letter-prefixed Reg S legs (`N45780DA3`, `U8531HAA8`)
  come through the same path, so the Reg S leg of a dual-set tranche needs no special case.
- **The CUSIP inside a US ISIN is derived** (`US90331HPV95` → `90331HPV9`), switchable off. Some
  formats print one and not the other, and an extra exact matching key costs nothing.
- **`(Exp)` and `(B&D)` are not tickers.** Ratings print `Moody's (Exp): A2` and syndicates print
  `BofA (B&D)`, both of which a naive parenthesis rule takes as tickers. Excluded by requiring all-caps
  (kills `Exp`) and an alphanumeric-only charset (kills `B&D`), plus a small stoplist.
- **Two of ten emails yield no ticker at all**, and one of those two also has no identifier. Editable
  identifiers and tickers (§19.5) are load-bearing, not a convenience.
- **The sent timestamp is useless as an ingest signal, as §19.8 assumed.** All ten were sent by the
  same person within 80 seconds. Only one has a real subject line; the rest are `2`…`10`, so
  **nothing can be inferred from the subject** either — an issuer name in the subject (which one
  screenshot suggested) is a property of forwarded mail, not of the corpus.
- **`html_to_text` must preserve cell boundaries.** Two table cells reading `USD Benchmark` must not
  become `USD BenchmarkUSD Benchmark`; block and cell tags become whitespace before tags are stripped.

#### 19.21.3 The test suite is committed, and that is the point

Every earlier assertion suite in this project — 16, 50, 167, 224 assertions per `implementation.md` —
was written in a scratchpad and **deleted**, leaving every historical verification claim resting on
code that no longer exists. `backend/tests/test_email_ingest.py` is committed and runs with plain
`python`, no pytest and no new dependency:

```
cd backend
.\.venv\Scripts\python.exe tests\test_email_ingest.py     # 148 passed, 0 failed
```

The corpus half **skips cleanly** when `backend/expected/samples/` is absent — it is gitignored, so a
fresh clone must not fail — and `PBI_SAMPLES_DIR` overrides the location, which is how the skip path
itself is verified (63 assertions pass, 1 skip, exit 0). Every string in the false-positive section is
one the extractor genuinely accepted at some point against the real files; they are regressions, not
hypotheticals.

**Dependency:** `extract-msg>=0.54` added to `requirements.txt` (present in the dev venv at 0.56.0).

### 19.22 A2–A4 and B1 built — and the plan trimmed first

The user challenged the design as over-built before this step, and was right about
part of it. **What was cut before any of it was written:**

| Planned | Outcome |
|---|---|
| `util_run.source` column | **Cut.** `util_run.tool = 'upload'` on an existing column does it. **Zero DDL for the whole of phase A.** |
| `util_email_label` table in A2 | **Cut from A2** — it belongs to phase B, not to an upload endpoint. |
| `upload_dir` and `identifier_match_only` config | **Cut.** The directory is derived from the store path; an upload always matches on identifiers. |
| Six endpoints (§19.10) | **Cut to one.** `GET /runs/{id}` already returns the emails, and `get_run_detail` already deserialises `params_json`, so the manifest reaches the UI with no new route. |
| `PUT …/ids` (edit identifiers) | **Deferred** until the UI needs it — one of ten samples needs a pin. |
| D17 provenance split (B5) | **Deferred** to after B3: there is no score to split yet. |

What is left is genuinely small: `POST /api/compare/uploads` saves the files, parses
them, and writes the same `util_run` + `util_email` rows a capture writes. Every
existing endpoint — `/runs`, `/runs/{id}`, `/fetch`, `/compare`, `/diff`, `/export`,
`DELETE` — then works on an upload run unchanged.

Two small store additions were needed: `new_run_id()` (an upload names its file
directory after the run id, so it needs the id first) and `set_run_params()`
(`update_run` allowlists scalar columns; the manifest is a blob).

#### 19.22.1 A3: the join path was wrong in this spec, and the database said so

§19.8 claimed deal rows would be *"reached through their matched tranches' `deal_id`"*
and that securities lead to issuance which leads to the audit rows. Probing the live
Automation database (read-only, 2026-08-10) corrected three things:

| Claim | Reality |
|---|---|
| `t_issuance_deal` joins on `deal_id` | It has **no `deal_id`**. It keys on **`first_deal_id`**, and `t_issuance.deal_id` joins to it — 1,402 of 1,402 rows. (`master_deal_id` matches identically; the primary-looking one is used.) |
| audit rows reachable via `tranche_id` | **`tranche_id` is all but unused** — 31 of 1,402 issuance rows, 70 of 3,623 audit rows. On its own it found **zero** audit rows for a real identifier. |
| — | **`deal_id` is the working route to audit rows**: 255 reachable where `tranche_id` found none. Deliberately broader (every tranche of a matched deal) because row matching narrows a candidate set, and too few candidates cannot be narrowed at all. |

A fourth finding shaped the whole query: **both `t_issuance` and `t_issuance_data`
carry the identifier columns themselves** (`sec_144a_isin`, `sec_regs_isin`,
`sec_144a_cusip`, `sec_regs_cusip` — all tier 1), but populated on only a *minority*
of rows (40 of 1,402 issuance rows have a 144A ISIN), because a pre-pricing
announcement has no identifiers to state yet. So no single route is complete and
`build_identifier_select` ORs them together: the table's own identifier columns, the
`issuance_key` of a matched security, `tranche_id`, and `deal_id`.

`fetch_rows_by_identifier` is a **sibling of `fetch_rows`, not a flag on it** — the
two share nothing but their return shape. It applies **no ticker filter, no window and
no datasource filter**, and every read-only rail is unchanged: `SELECT` only,
whitelisted identifiers, every value bound, `SET TRANSACTION READ ONLY`, statement
timeout, row cap. Asking with no identifiers raises rather than scanning.

**Verified end-to-end** with identifiers read out of the database itself: 3 securities
→ 3 issuance → 2 audit → 3 deal rows. A fabricated ISIN returns nothing rather than
everything. Run against the ten real samples it returns **0 rows and explains why**,
which is the correct answer until §19.3's deployment happens.

#### 19.22.2 B1: `from_label`

Pure, and separate from `build_expected` because the two have nothing in common but
their output shape: one derives values from a payload it generated, the other is
*told* them and runs no resolver at all.

Verified against the real **USB** label — one email, two legal issuers under one
ticker: 2 deal rows, 2 tranches, 3 security rows (one a Reg S leg with a CUSIP and no
ISIN), `regulation_subcategory` translated `3a2` → `3(a)2`, prose mapped to
`GENERAL CORPORATE PURPOSES`, and `t_issuance` correctly omitting `issuer_name` and the
other six columns it does not have. It returns **`rendered_fields`** — the columns the
label actually filled — which is the accuracy denominator (§19.7). A blank, `None` or
whitespace value is not written and not counted (D12); a column outside the field map
is **warned about, never silently dropped**; a value outside a closed vocabulary is kept
as stated *and* flagged, so a wrong expectation announces itself rather than quietly
scoring the agent down.

#### 19.22.3 A4, and the honest limit on it

The tab gains a drop zone in panel 1, an `uploaded` marker and email count on upload
rows, and an **Emails** panel listing subject · sent · ticker · identifier counts ·
labelled, expanding to the body in a **sandboxed iframe** (`sandbox=""`, `srcDoc`, never
`dangerouslySetInnerHTML`). The iframe is given a white surface deliberately: broker
mail assumes a light background and sets its own colours, so inheriting the dark theme
would leave dark text on dark. `/fetch` messages branch on `matched_on` so an upload is
never told about a ticker and a window the query did not use.

**`npm run build` is clean, and the panel was not seen rendering** — no browser was
available in the session that wrote it. That is a real gap and is recorded rather than
glossed: what *is* asserted is the **data contract**, every path the component reads
(`run.tool`, `run.params.upload.emails[].identifiers.isins`, the manifest-to-email join
on `util_email_id`, `body_html`, `rendered_fields`) against a live API response, so a
rename on either side fails a test instead of rendering an empty panel. First run
against a real browser should be treated as the actual verification of A4.

#### 19.22.4 Tests

Both suites are **committed** and run with plain `python`, no pytest:

```
cd backend
.\.venv\Scripts\python.exe tests\test_email_ingest.py    # 148 passed
.\.venv\Scripts\python.exe tests\test_upload_flow.py     # 103 passed
```

`test_upload_flow.py` is deliberately layered: A3 asserts SQL *shape* so it needs no
database, B1 needs nothing at all, and only A2/A4 need the gitignored samples — those
skip cleanly without them. The A2 section creates a real run through `TestClient` and
deletes it in a `finally`, asserting the cleanup counts.

### 19.23 B2 built — the labelling form, and the 5% that should have been 100%

`backend/label_form.py` (form model + prefill, pure), `frontend/src/components/LabelForm.jsx`,
`GET`/`PUT`/`DELETE /api/compare/uploads/{run}/emails/{email}/label`, and one new table,
`util_email_label`. **B3 fell out of it** and is verified in the same pass.

#### 19.23.1 The field list is measured, not guessed

§19.6.2 said to derive the form's default columns from the real emails before building it. Done by
counting the samples' **own row labels** — `html_tables` gives `label, value, value…` per row, so
the emails state their own vocabulary. Labels appearing in **two or more** of the ten:

```
settlement 9 · use of proceeds 8 · cusip 7 · denominations 7 · format 7 · isin 7 ·
issuer/ticker 7 · optional redemption 7 · sale into canada 7 · timing 7 ·
book runner(s) 6 · ipt 6 · ratings 6 · tenor 6 · total size 6 · maturity date 5 ·
ranking 5 · coupon type 4 · par call 4 · size 3 · interest payment dates 2 ·
interest rate 2 · tranche size 2
```

The default form is **33 fields** — 11 deal, 17 tranche, 5 per identifier set — against 72 compared
concepts, with *show every scored field* as the escape hatch. Close to the ~25 guessed from the
screenshots, but now grounded.

Three of those labels have **no column to fill**: `sale into canada` and `timing` (nothing in the
schema), and `denominations` — which turns out to be `minimum_piece` + `minimum_increment`, **both
tier `x`**, so §19.17's "no denominations column" was wrong. Seven of ten emails state it, which
makes those two a promotion candidate alongside the §19.19.3 batch.

#### 19.23.2 The perfect-agent round trip, and why it earned its place

Label the emails, export the expected lane, re-import it as `actual`, compare. A perfect agent must
score 100%; anything less is the harness's fault.

**The first run scored 5%** — `rows_matched: 2` of 22, with 276 fields reported `missing`. Nothing
was wrong with the comparator: matching key 2 is `issuer_ticker + currency_code +
preliminary_security_tenor` (18.7), and **nothing suggested a currency**. These emails have no
"Currency" row — it is embedded in the size (`USD Benchmark`, `C$ Benchmark`, `Tranche Size: USD`),
which the prefill was reading only for an amount and discarding when it found a placeholder.

Reading the currency out of the size row takes it to **100%, 22 of 22 rows paired, 333 field
matches, zero mismatches.**

Worth stating plainly: **without this test the first real comparison would have reported ~5% and
looked like a catastrophically bad agent.** A harness that silently fails to pair rows produces a
number that is not merely wrong but wrong in the direction that gets someone blamed. The round trip
is now a permanent assertion, and `C$` is tested against being read as USD.

#### 19.23.3 What the form does, and what it refuses to do

* **Blank is a legitimate answer**, said once at the top rather than nagged for — a blank is excluded
  from scoring, not counted against the agent (D12).
* **Suggestions are chips, not values.** They are returned under their own key, shown only on an
  *empty* field (overwriting a stated value would put the machine's reading above the human's), and
  committed only by Save (D11).
* **Pick, don't type** wherever the application has a closed vocabulary — the eight registration
  types, the three uses of proceeds, the ten sub-categories.
* **Hints where the email's words differ from the column's** (§19.18): dividend ⇒ coupon, Ranking ⇒
  `bond_seniority`, and `total_issued_amount` explicitly told to stay blank for a placeholder.
* **Pairing-only fields are labelled as such** — `preliminary_security_tenor` is tier `x` and shown
  as *pairing*, so nobody wonders why an unscored field is being asked for.
* **The multi-issuer email flags itself.** Prefill cannot split the USB email into two deals — how
  many deals an email describes is a labelling input — but it must not merge them silently either, so
  it counts the distinct issuers and says so.
* **Length limits are shown** against the column's own `character_maximum_length`.

#### 19.23.4 Prefill defects found against the real emails

Every one of these is now a regression test:

| Defect | Cause |
|---|---|
| `A2/A+/AA-` read as `A2/A/AA` | The rating pattern ended in `\b`. After matching `A+` the position sits between two non-word characters, so the boundary fails and the match backtracks to a bare `A` — silently dropping every suffix. |
| `Tranche Size: USD` became an amount | An amount with no digits is not an amount. |
| F4 collapsed 5 tranches into 1 | The CAD sample repeats `Tenor:`/`Size:` blocks per tranche rather than using columns. The *n*-th occurrence of a label is now the *n*-th tranche. |
| F8 yielded 1 field | The messy sample writes `Issuer/Ticker Sumitomo…` with no colon. Matching a *known* label at line start is safe because the label list is closed. It reaches 15 fields; its tranche split is still 1 of 5, which is the D10 case and is recorded rather than hidden. |
| `with Registation Rights` unmatched | **The typo is in the source email.** `reg\w*` rather than `reg(istration)?`. |
| No currency suggested | §19.23.2 — the 5% bug. |

`html_to_text` was also fixed: it converted cells to tabs and then a cleanup rule stripped whitespace
around newlines and ate them. **The A1 test missed it** because it used a minimal table with no
whitespace between tags; real Outlook markup is indented. A tab now wins over an adjacent newline, and
`html_tables()` was added because a flat string cannot represent a cell holding several paragraphs —
`Moody's (Exp): A2` and `Stable` are two `<p>` inside one cell.

#### 19.23.5 Storage

`util_email_label` — `util_email_id`, `util_run_id`, `label_json`, `label_status`, `rendered_fields`,
`updated_at`. The label is kept as a blob rather than reverse-engineered out of the mirror rows, so it
can be reopened, edited, and (B4) exported.

Saving **replaces** that email's expectation, scoped by `util_email_id`, so relabelling one email of
thirty cannot disturb the other twenty-nine. Deal sequences are offset by the email's own sequence
(`email_seq * 100 + n`) because the diff groups by `(deal_seq, tranche_seq)` and two emails both
claiming deal 1 would merge into one tree.

**Tests:** `test_label_form.py`, 92 assertions. Total across the three committed suites: **343**.

### 19.24 B4 and B5 built — the feature is complete

**B5 — the provenance split (D17).** `field_map.source_kind` sorts every finding into
`extracted` (the agent read it), `derived` (the application computed it) or `stamped` (a
pipeline constant). `score()` reports the three beside the headline; the diff tags each row.

Computed **on read, never stored**: provenance is a property of `field_map.csv`, not of a
pass, so no migration was needed and correcting the map fixes passes already scored. Only
non-extracted rows are tagged in the diff — labelling every row `extracted` would be noise,
and the default reading of a field is that the agent produced it.

Measured on a labelled run: **273 extracted, 58 derived, 0 stamped**. So a quarter of what
the headline blends is not an agent extraction, which is exactly what D17 was for.

**B4 — labels survive the store.** `GET /uploads/{run}/labels` writes a versioned document;
`POST …/labels/import` restores it. Entries match on **identifiers first**, then subject,
then file name — row ids change when the same emails are uploaded into a different store,
so an export keyed on them would only round-trip on the machine that made it.

Verified the hard way rather than in place: label three emails, export, **delete the run
entirely**, upload the same files again, restore, re-score — **identical accuracy over
identical field counts** (448 both times). An entry matching no email is reported, not
dropped; a partial restore that names what it could not place is useful, a silent one is not.

`/import` also now accepts `lane=expected` **for upload runs only**. A capture run's expected
lane is generated by the utility, so importing one would overwrite ground truth with a file;
an uploaded set's is hand-authored, and restoring it is the point.

#### 19.24.1 Two leaks the tests found

* **Deleting a run left its files behind.** The store knows nothing about the filesystem, so
  nothing cleaned up the saved `.msg` copies — **41 orphaned directories** accumulated in one
  session of testing. `DELETE /runs/{id}` now removes the directory and reports the count.
* **A rejected upload orphaned an empty directory**, because the directory is created before
  parsing and the "nothing readable" path raised without unwinding it.

Both are regressions now. The second was caught only because the suite asserts the store is
clean *after* it runs — worth keeping as a habit: a test that tidies up but never checks it
tidied up will not notice a leak.

#### 19.24.2 Status

Phase A and phase B are complete: **A1–A4, B1–B5**. What remains is not code —
§19.3's deployment, `coupon_type`'s conversion table, and the `3a2` → registration-type
confirmation. Phase C (update sequences, §19.16) stays deferred until samples exist.

**363 assertions** across three committed suites, `import main` clean, `npm run build` clean.
The frontend has **still not been seen rendering** — no browser in any session that wrote it.

### 19.25 Coverage is not accuracy — found on the first real comparison

**The parsing agent has run.** `pbi.t_issuance_data` on TRP - QA - Automation carries **15
`APPROVED_LLM` rows** (2026-08-11), against 3,754 `BBG`. Fetching the ten samples' identifiers
returns 9 audit / 14 current-state / 24 security rows, and **every one is `APPROVED_LLM`** — the
agent's own output, for ORCL and USB. §19.3's blocker has lifted for two of the ten emails, and
the identifier fetch (§19.22.1) finds exactly the right rows with no ticker and no window.

**The first real comparison scored 27.5%, and that number was wrong.** Of ten labelled emails the
agent had processed two, so eight had no application rows at all. Every field of those eight
emails' expected rows was trivially `missing` — there was no row to hold a value — and all of it
counted against the score. Excluding them: **54.6%**.

The principle was already in the spec. §18.7: *"0 rows matched is surfaced as a matching/template
problem, never as 0% accuracy."* Partial coverage is the same fact arriving in a subtler form, and
it was not handled.

**Fix:** a `Finding` records whether its expected row **paired with anything**. Fields of an
unpaired row keep their place in the diff — you still want to see what the application never
created — but leave the accuracy denominator, and are reported as `fields_in_unmatched_rows`
beside `rows_missing`. The panel states the exclusion in words, because a silent exclusion is its
own kind of lie.

After the fix the figure stops moving with coverage: **61.2%** over ten emails against **60.3%**
over the two the agent had processed, with 501 fields reported as belonging to rows the
application never created.

**This is the second bug of exactly this shape**, after the 5% currency bug in §19.23.2. Both
produced a plausible number that was wrong in the direction that blames the agent, and neither was
visible without running real data through the whole loop. Worth stating as a rule: **an accuracy
harness fails silently by default.** Every figure it reports needs a construction where the right
answer is known — a perfect agent, or a deliberate coverage gap — or it will be believed.

> **The 61% is not a measurement of the agent.** Those labels were the machine's own suggestions,
> confirmed by nobody. It measures that the loop works end to end against real output. The real
> number needs a human to confirm the labels — which is what the form is for.

**Also fixed:** `DELETE /runs/{id}` now removes the run's saved `.msg` directory (41 orphans had
accumulated), and a rejected upload no longer leaves an empty one behind.

### 19.26 First browser render, and two bugs it showed (2026-08-11)

The user sent a screenshot of the labelling form in the running app — **the first time any of
this frontend had been seen rendering.** The layout is as §19.6 specified: email left, form right,
prefilled, hints beneath the fields, tier badges, pick-lists on the closed vocabularies. A4's
outstanding visual verification is met.

The screenshot itself exposed a bug that the diff had already hinted at:

* **`issuer_rating` was deduplicated.** The ORCL email states Moody's `Baa2`, S&P `BBB`,
  Fitch `BBB`; the prefill produced `Baa2/BBB`. Two agencies routinely assign the same grade, and
  the `ratings` normalizer compares a **multiset**, so dropping the repeat made a correct
  expectation unequal to the application's `Baa2/BBB/BBB` — a mismatch on every row carrying a
  rating. **71.0% → 75.6%.**
* **`deal_currencies` was blank** though every tranche states its currency. Along with
  `db_number_of_tranches` it is stated by the label's own *shape* rather than by the labeller, so
  `from_label` now derives it. Both are tier 1, and asking someone to retype what they have
  already entered per tranche is how a form earns a reputation for being tedious.

Running total on the two emails the agent has processed: **27.5% → 54.6% → 61.2% → 71.0% →
75.6%**, every step a defect in the harness rather than a change in the agent.

**Still open from the same screenshot**, needing the user rather than code: the ORCL email states
three *different* outlooks (Negative, Negative, Stable) and `rating_outlook` is a single column.
The prefill takes the first, and the diff shows it right 1 of 11 — so whichever agency's outlook
the application keeps, it is probably not the first. One answer settles the column.

#### 19.26.1 The shape of every bug found by real data

Five now, and they are all the same shape: **the harness reporting a number that was wrong in the
direction that blames the agent.**

| Found | Cause | Cost if unnoticed |
|---|---|---|
| 5% | no currency suggested, so rows could not pair | agent looks catastrophic |
| 27.5% | coverage counted as accuracy | agent blamed for emails it never saw |
| 0 of 11 dates | `May 20, 2029` unparseable | agent looks unable to read a date |
| ratings deduplicated | multiset comparison | agent blamed for our arithmetic |
| tranche lost in Format 8 | regex consumed its own line | silent under-count |

None was visible from unit tests on synthetic input; every one needed real emails and real
application rows. The lesson is not "test more" but **"an accuracy harness fails silently by
default"** — a wrong number looks exactly like a right one. Each is now a construction where the
answer is known: a perfect agent must score 100%, a deliberate coverage gap must not move the
figure, an email's own date wording must equal an epoch.

## 20. Securitized (ABS) Issuance

> Status: **built, live-verified and complete (2026-08-13)** — three slices, each in its own chat per
> the §18.17 protocol. This section was drafted as a standalone file (`master/abs_spec.md`), built
> against, corrected six times by contact with reality, and merged here at §20.18 step 1; that file
> is now deleted and **this is the authoritative record**.
>
> **Source sample:** `backend/tests/fixtures/oakhurst_deal.json` — 1 deal · 1 series · 3 tranches ·
> 2 securities per tranche, all `Fixed` coupon, USD. (It lived in `master/` during the build; §20.18
> step 3 moved it, because `test_abs_slice1.py` reads it.)
> **Vocabularies:** `backend/reference/securitized/_source/ABS_DropDowns.xlsx`, sheet
> `Drop-down Lists` — 56 asset types, 28 rankings, 17 coupon types, 12 accrual methods, 8 day
> counts, 5 business-day conventions. (Also moved out of `master/`; `_generate.py` reads it.)
>
> Items marked **[RESOLVED]**, **[CORRECTED]**, **[ADDED]** or **[CONFIRMED]** carry the date and the
> slice that settled them. Those notes are the reasoning behind what the section says and are kept
> deliberately — six of them record places where this spec was **wrong** before the code proved it.

### 20.1 Overview

A fourth generator tool — **Securitized (ABS)** — sitting alongside Bonds, Loans and Interest
Capture. It generates internally-consistent structured-finance deals and posts them as a single
event per deal.

The defining difference from every existing tool: **the payload is a four-level nested tree, posted
as one request.**

```
Deal                          ← DETAILS.*                      (1 POST)
└── Series                    ← DETAILS.SERIES{}               (1..N)
    └── Tranche               ← SERIES[k].TRANCHES{}           (1..N)
        └── Security          ← TRANCHES[k].SECURITIES{}       (1..2)
```

Bonds and Loans post one request *per tranche* and stitch the deal together server-side (Loans via
the `ALL_LOAN_DEAL` / `MASTER_LOAN_ID` round-trip, §6.4). ABS does not: the whole tree goes up in
one body, so there is **no linkage query, no `deal_query_wait`, and no "tranche 1 failed → skip the
rest" logic**. One deal = one POST = one ACK/NACK.

#### 20.1.1 Tool identity

| Thing | Value |
|---|---|
| Tool id (backend/router/history/localStorage) | `securitized` |
| Tab label | `Securitized` |
| Router prefix | `/api/securitized` |
| Engine | `backend/engines/securitized_engine.py`, `run_securitized(params, env, stop_event, log_queue)` |
| Reference dir | `backend/reference/securitized/` |

Tab order in `App.jsx`: `Bonds · Loans · Securitized · Interest Capture · TIG Orders · CSV Upload ·
Email Compare · History · Settings`.

---

### 20.2 Endpoint

```
POST https://{host_name}/gwf//EVENT_CREATE_NEW_SECURITIZED_ISSUANCE
```

The **double slash after `/gwf`** is intentional — it matches Bonds (§5.1) and Loans (§6.1) and is
what the gateway expects. Do not normalise it. **[CONFIRMED live 2026-08-13, slice 3]** — every one
of the fifteen live posts in §20.21 went to exactly this URL and was accepted.

#### 20.2.1 Authentication

**Identical to Bonds/Loans (§3) in every respect.** `POST https://{host}/sm/event-login-auth` with
`MESSAGE_TYPE: TXN_LOGIN_AUTH` / `SERVICE_NAME: AUTH_MANAGER`, read `SESSION_AUTH_TOKEN` from the
response, then send it as a **header** on the publish call. The header block is exactly
`loans_engine`'s — including the constant `SOURCE_REF: 12345`:

```
Content-Type: application/json   Accept: */*        SOURCE_REF: 12345
Cache-Control: no-cache          Connection: keep-alive
User-Agent: PostmanRuntime/7.48.0
SESSION_AUTH_TOKEN: <token>
```

Nothing about ABS auth is special: the token is a header, not a body field, and `SOURCE_REF` is the
same constant the other tools send. (The ACK in §20.2.3 echoes `"SOURCE_REF": "12345"`, which
corroborates this.)

Credentials come from the active environment in Settings. **[RESOLVED 2026-08-13]** ABS reads
`credentials.bonds_loans` — the same issuance user — so no new credential set, no
`environments.json` change and no Settings change are needed for auth. **[CONFIRMED live
2026-08-13, slice 3]** — auth returned HTTP 200 and a usable token on every run.

Auth is skipped entirely in dry-run (§20.11), exactly as in the other engines.

#### 20.2.2 Envelope — **[RESOLVED 2026-08-13]**

Three top-level keys, the same shape Bonds and Loans use:

```json
{
  "MESSAGE_TYPE": "EVENT_CREATE_NEW_SECURITIZED_ISSUANCE",
  "SERVICE_NAME": "ISSUANCE_EVENT_HANDLER",
  "DETAILS": { ... }
}
```

`oakhurst_deal.json` shows only the `DETAILS` object because the capture had the rest collapsed;
nothing in it contradicts the above. **[CONFIRMED live 2026-08-13, slice 3]**.

#### 20.2.3 Response — **[RESOLVED 2026-08-13, CONFIRMED live 2026-08-13]**

The predicted ACK body, and the body observed verbatim on the wire, are the same string:

```json
{"GENERATED":[],"MESSAGE_TYPE":"EVENT_ACK","SOURCE_REF":"12345"}
```

Two consequences:

- **Success is `MESSAGE_TYPE == "EVENT_ACK"`**, failure is `EVENT_NACK` with an `ERROR` array.
  `loans_engine._publish` already tests exactly this and needs no change beyond the URL.
- **`GENERATED` comes back empty**, so the ACK does **not** return the server-assigned UUIDs that
  replaced the `TEMP-*` keys. There is therefore no id mapping to log, and no way for the engine to
  tell the user which deal it just created beyond the issuer name and ticker it generated. The log
  line (§20.10.1) leads with `ISSUER_NAME (ISSUER_TICKER)` for exactly this reason — that pair is
  the only handle back into the UI. If a query service equivalent to `ALL_LOAN_DEAL` turns up later,
  resolving and logging the real deal id is a small, self-contained addition.

The NACK body carries more than the spec first assumed, and its shape matters when reading §20.10.2.
Two real ones, both observed in slice 3:

```json
{"WARNING":[],"ERROR":[{"@type":"StandardError","CODE":"INTERNAL_ERROR",
  "TEXT":"Mandate Text is required","STATUS_CODE":"500 Internal Server Error"}],
  "MESSAGE_TYPE":"EVENT_NACK","SOURCE_REF":"12345"}

{"WARNING":[],"ERROR":[{"@type":"FieldError","CODE":"VALIDATION_ERROR",
  "TEXT":"$.DETAILS: property 'MANDATE' is not defined in the schema and the schema does not
   allow additional properties","STATUS_CODE":"400 Bad Request","FIELD":"DETAILS",
  "PATH":"$.DETAILS"}],"MESSAGE_TYPE":"EVENT_NACK","SOURCE_REF":"12345"}
```

**[ADDED 2026-08-13, slice 3] — the two `@type`s are different failure classes and it is worth
telling them apart.** A `FieldError` / `VALIDATION_ERROR` is the JSON-schema layer: it carries
`FIELD` and `PATH`, arrives as `400`, and names the exact JSON path. A `StandardError` /
`INTERNAL_ERROR` is a *business* rule evaluated after schema validation passes: it arrives as `500`,
has no `PATH`, and its `TEXT` is prose. The engine logs `TEXT` for both, which is right — but when a
NACK has no path in it, do not go looking for a malformed field. Look for a rule.

---

### 20.3 Payload structure

#### 20.3.1 Nesting is by map, not array

Unlike Loans (`SECURITIES` is a JSON **array**), every ABS child collection is a **JSON object keyed
by a temporary id**:

```json
"SERIES": {
  "TEMP-Series-0": {
    "SERIES_ID": "TEMP-Series-0",
    "TRANCHES": {
      "TEMP-Tranches-0": {
        "TRANCHE_ID": "TEMP-Tranches-0",
        "SERIES_ID": "TEMP-Series-0",
        "SECURITIES": {
          "TEMP-Securities-0": {
            "SECURITY_ID": "TEMP-Securities-0",
            "TRANCHE_ID": "TEMP-Tranches-0",
            "SERIES_ID": "TEMP-Series-0"
          }
        }
      }
    }
  }
}
```

Four rules, all observed in the sample, all mandatory:

1. **Key = own id.** The map key and the node's own `*_ID` field are the same string.
   `TEMP-Series-0` ↔ `SERIES_ID`, `TEMP-Tranches-0` ↔ `TRANCHE_ID`, `TEMP-Securities-0` ↔
   `SECURITY_ID`.
2. **Note the plural.** Tranche keys are `TEMP-Tranches-<n>` (plural "Tranches"), series keys are
   `TEMP-Series-<n>` (already plural), securities are `TEMP-Securities-<n>`. Inconsistent, but
   verbatim from the sample — do not tidy it.
3. **The keys are placeholders — only their internal agreement matters. [RESOLVED 2026-08-13]** The
   `TEMP-*` strings exist solely to express the tree shape in a flat map. On submission the app
   discards them and assigns real UUIDs, by design. So the engine numbers each collection from `0`
   and **restarts inside each parent** (matching the sample: every tranche's securities begin at
   `TEMP-Securities-0`). Because the server only uses these to rebuild the tree before replacing
   them, a deal-wide numbering would be equally valid — the one thing that must hold is rule 4.
4. **Children carry every ancestor id.** A tranche repeats `SERIES_ID`; a security repeats both
   `TRANCHE_ID` and `SERIES_ID`. These are the join keys the handler uses to rebuild the tree, so
   they must agree with the enclosing keys exactly.

The engine builds this from an internal list-of-lists and serialises to maps at the end, so
generation logic stays readable.

#### 20.3.2 Field inventory — Deal level (`DETAILS`)

| Field | Type | Source |
|---|---|---|
| `ANNOUNCEMENT_DT` | epoch ms | generated (§20.7) |
| `AUDITORS` | string | `ref/auditors.csv` |
| `BACKUP_SERVICER` | string | `ref/agents.csv` |
| `BOOKRUNNERS` | string, ` / `-joined | 2–4 from `ref/underwriters.csv` |
| `BUSINESS` | string | `ref/asset_types.csv` → `BUSINESS` |
| `CALCULATION_AGENT` | string | `ref/agents.csv` |
| `COUNTRY` | ISO2 | `ref/currencies.csv` → `COUNTRY` |
| `COVENANT` | string | `"Standard ABS"` |
| `CO_MANAGERS` | string, ` / `-joined | 1–3 from `ref/underwriters.csv`, disjoint from bookrunners |
| `CREDIT_ENHANCEMENT_PROVIDERS` | string | composed from the CE features actually used (§20.6.3) |
| `CURRENCY_CODE` | string | run param or `ref/currencies.csv` |
| `CUSTODIAN` | string | `ref/agents.csv` |
| `DEAL_SIZE` | **number** | Σ series sizes (§20.6.1) |
| `DEAL_TYPE` | string | run param or random — one of `ABS` / `CLO` / `CMBS` / `RMBS`; **drives asset selection, the issuer sector (`INDUSTRY`) and `SUB_INDUSTRY` (§20.3.2)** |
| `DEPOSITOR` | string | `ref/entities.csv` → `DEPOSITOR` |
| `INDUSTRY` | string | **carries the issuer sector** drawn from the `DEAL_TYPE`'s sector list (§20.3.2) — no longer the `asset_types.csv` industry; the schema has no `ISSUER_SECTOR` property |
| `INVESTOR_STATUS` | string | `"OPEN"` |
| `ISSUER_CIK` | **string** | `ref/entities.csv` |
| `ISSUER_NAME` | string | `ref/entities.csv` (§20.5.2) |
| `ISSUER_TICKER` | string | `ref/entities.csv` |
| `IS_ROADSHOW` | bool | random, weighted false — **drives `MANDATE_TEXT`, §20.3.8** |
| `LEGAL_ADVISORS` | string | `"<X> (issuer) / <Y> (underwriters)"` from `ref/legal_advisors.csv` |
| `LIQUIDITY_FACILITY_PROVIDER` | string | `"None (reserve account funded)"` or a bank |
| `MANDATE_TEXT` | string | **conditional — emitted only when `IS_ROADSHOW` is true (§20.3.8)** |
| `ORIGINATOR` | string | `ref/entities.csv` — **mandatory**; name discrepancy, see below |
| `ORIGINATOR_CIK` | **string** | `ref/entities.csv` |
| `ORIGINATOR_FITCH_RATING` | string | `ref/ratings.csv` |
| `ORIGINATOR_MOODY_RATING` | string | `ref/ratings.csv` — note singular **`MOODY`** |
| `ORIGINATOR_SANDP_RATING` | string | `ref/ratings.csv` — note **`SANDP`**, not `SP` |
| `ORIGINATOR_TICKER` | string | `ref/entities.csv` |
| `PAYING_AGENT` | string | `ref/agents.csv` |
| `RATING_AGENCIES` | string | `"Moody's / S&P / Fitch"` |
| `REGISTRAR` | string | `ref/agents.csv` |
| `SERIES` | **object** | §20.3.1 |
| `SERVICER` | string | `ref/entities.csv` — usually == `ORIGINATOR` |
| `SPONSOR_CIK` | **string** | `ref/entities.csv` |
| `SPONSOR_NAME` | string | `ref/entities.csv` |
| `SPONSOR_TICKER` | string | `ref/entities.csv` |
| `SUB_INDUSTRY` | string | **conditional** — nested under the issuer sector carried in `INDUSTRY` (§20.3.2); omitted when the chosen sector has no sub-industries |
| `TRADE_DESK` | string | `"Securitized"` (constant) |
| `TRUSTEE` | string | `ref/agents.csv` |
| `UNDERWRITER` | string | `ref/underwriters.csv` — the lead |
| `USER_OF_PROCEEDS` | string | `ref/asset_types.csv` — note **`USER_`**, not `USE_` |

Three field names are misspelled or non-obvious **in the server's schema** and must be reproduced
character-for-character: `ORIGINATOR_MOODY_RATING` (singular), `ORIGINATOR_SANDP_RATING`,
`USER_OF_PROCEEDS`. A fourth appears at tranche level — see below.

**`ORIGINATOR`, not `ORIGINATOR_NAME`. [RESOLVED 2026-08-13, CONFIRMED live 2026-08-13]** The
mandatory-field list in §20.16 Q5 called it `ORIGINATOR_NAME`; the payload field is **`ORIGINATOR`**
and that is what the engine sends. The very first live post (§20.21 step 7.1) ACKed, which settles
it — and had it been wrong, the closed schema described in §20.3.7 would have said so by name. The
discrepancy is a **UI label vs payload field** difference, not a contradiction — the app's form
labels the input *"Originator Name"* (with *"Originator Ticker"* beside it) while the underlying
fields are `ORIGINATOR` and `ORIGINATOR_TICKER`. Worth remembering when reading any future field
list sourced from the screen rather than the wire: the same applies to `ORIGINATOR_MOODY_RATING` and
friends, whose labels will read "Moody's Rating".

**`DEAL_TYPE`, issuer sector (`INDUSTRY`), `SUB_INDUSTRY` — [ADDED 2026-09-09]** These deal-level
fields replace the previous asset-type-centric model. `ASSET_TYPE` is **no longer emitted on the
Series** (and the Asset Type UI control is gone); the tab now offers a **Deal Type** selector
(`ABS` / `CLO` / `CMBS` / `RMBS`, or blank = random per deal). `DEAL_TYPE` is authoritative:

1. It constrains the internal asset-type pick to the `asset_types.csv` rows whose `PRODUCT_GROUP`
   equals it (`ABS` has 51 candidates; `CLO`/`CMBS`/`RMBS` have exactly one each, so those deal
   types resolve to a fixed asset profile). What the asset row still drives — `BUSINESS`,
   `POOL_PROFILE`, `PREPAYMENT_TYPE`, `PRICING_SPEED`, `USER_OF_PROCEEDS`, and the Series
   `PRODUCT_GROUP` — therefore stays consistent with `DEAL_TYPE`.
2. The **issuer sector** is drawn from the deal type's sector list and **carried in the `INDUSTRY`
   field** — the closed schema has **no `ISSUER_SECTOR` property** (a live post NACKed *"property
   'ISSUER_SECTOR' is not defined in the schema"*, 2026-09-09), so the sector value reuses the
   existing schema-valid `INDUSTRY` field, displacing the asset row's industry there. **`SUB_INDUSTRY`
   is nested under the chosen sector**, and is **omitted** when the sector has no sub-industries (per
   the operator's decision, 2026-09-09).

| `DEAL_TYPE` | issuer-sector values (emitted in `INDUSTRY`) |
|---|---|
| `ABS` | Aircraft, Autos, Cards, Consumer Loans, Data Centers, Equipment, Student Loan, Timeshares, Utility, Whole Biz |
| `CMBS` | Conduit, SASB, Freddie K |
| `RMBS` | CRT, Non-QM, Prime 2.0, RPL, Freddie K |
| `CLO` | CRE CLO |

`Freddie K` is deliberately valid under **both** RMBS and CMBS (agency multifamily).

| issuer sector (in `INDUSTRY`) | `SUB_INDUSTRY` values (nested) |
|---|---|
| Autos | Auto Leases, Auto Prime Loans, Auto Subprime Loans, Motorcycle Prime Loans, Rental Car, Fleet Lease, Dealer Floorplan |
| Cards | Bank Cards, Retail Cards |
| Consumer Loans | Mobile Phone |
| Equipment | Equipment Loans/Leases |
| Student Loan | FFELP, PSL |
| CRE CLO | 3.0 Double-A, 3.0 Duper A1/A2, 3.0 Duper LCF, 3.0 Junior AAA, 3.0 Single-A |

All other sectors (Aircraft, Conduit, CRT, Data Centers, Freddie K, Non-QM, Prime 2.0, RPL, SASB,
Timeshares, Utility, Whole Biz) have no nested sub-industry and omit the field. `check_payload`
warns (does not block) when the issuer sector in `INDUSTRY` is not valid for `DEAL_TYPE`, or
`SUB_INDUSTRY` is not nested under it — the same plausible-but-wrong-data posture used for
`PRODUCT_GROUP`. **`DEAL_TYPE`'s wire acceptance is still unconfirmed** — the 2026-09-09 live NACK
named only `ISSUER_SECTOR`, and the validator may stop at the first unknown property, so a re-post is
needed to prove `DEAL_TYPE` is a schema property; if it NACKs, it moves into `INDUSTRY`-style reuse
or is dropped (operator's call).

#### 20.3.3 Field inventory — Series level

| Field | Type | Notes |
|---|---|---|
| `SERIES_ID` | string | == map key |
| `SERIES_NAME` | string | `"<ISSUER_TICKER-stem> <vintage>-<n>"`, e.g. `OART 2026-1` |
| `SERIES_SIZE` | **number** | Σ tranche sizes |
| `POOL_SIZE` | **number** | `SERIES_SIZE × (1 + OC%)` (§20.6.2) |
| `PRODUCT_GROUP` | string | `ref/asset_types.csv`; the deal-type-constrained pick makes it equal `DEAL_TYPE` (§20.3.2). *(`ASSET_TYPE` was removed from the Series payload — 2026-09-09; see §20.3.2)* |
| `CURRENCY_CODE` | string | == deal currency |
| `COUPON_FREQUENCY` | string | `Monthly` / `Quarterly` / `Semi-Annual` — **not** in the dropdown workbook |
| `DAY_COUNT` | string | **controlled, §20.5.5** — `30/360`, `ACT/360`, … (**not** `Actual/360`) |
| `ACCRUAL_METHOD` | string | **controlled, §20.5.5** — sample uses `Actual balance accrual` |
| `BUSINESS_DAY_CONVENTION` | string | **controlled, §20.5.5** — `Following`, `Modified Following`, … |
| `GOVERNING_LAW` | string | currency-driven (§20.5.4) |
| `LISTING_EXCHANGE` | string | currency-driven |
| `REGISTRAR` | string | agent |
| `SETTLEMENT_PERIOD` | string | `T+2` / `T+3` / `T+5` |
| `COVENANT` | string | `"Standard securitization covenants"` |
| `AUDIENCE` | string | e.g. `"Securitized investors (144A / Reg S)"` — reflects the reg types actually generated |
| `ANNOUNCEMENT`→`MATURITY` dates | epoch ms | `EXPECTED_PRICING_DATE`, `SETTLEMENT_DATE`, `FIRST_COUPON_DT`, `MATURITY_DATE` (§20.7) |
| **Pool statistics** | — | `AOLS`, `FICO_SCORE`, `LTV`, `PERCENT_CA`, `WALA`, `WA_COUPON`, `WA_MATURITY_MONTHS` — **asset-type dependent**, see §20.5.3 |

#### 20.3.4 Field inventory — Tranche level

| Field | Type | Notes |
|---|---|---|
| `TRANCHE_ID`, `SERIES_ID` | string | ids |
| `TRANCHE_CLASS` | string | `A`, `B`, `C`, … by seniority |
| `TRANCHE_DESCRIPTION` | string | `"<Senior\|Mezzanine\|Subordinate> <fixed\|floating>-rate class"` |
| `TRANCHE_SIZE` | **number** | §20.6.1 |
| `INITIAL_NOTE_BALANCE` | **number** | == `TRANCHE_SIZE` |
| `NET_PROCEEDS` | **number** | `TRANCHE_SIZE × (1 − UNDERWRITING_DISC_AND_COMMISIONS/100)` |
| `UNDERWRITING_DISC_AND_COMMISIONS` | **number** | percent. **Misspelled in the schema — one `S` in COMMISIONS.** Reproduce verbatim |
| `CREDIT_ENHANCEMENT_PCT` | **number** | derived, §20.6.3 |
| `CCY` | string | == deal currency. Note the field is `CCY` here, `CURRENCY_CODE` at deal/series |
| `COUPON_TYPE` | string | `Fixed` \| `Float` — **drives §20.4** |
| `INT_RATE` | **number** | **Fixed only** — omitted on float tranches |
| `YIELD` | **number** | **Fixed only** — == `INT_RATE` when priced at par; omitted on float |
| `BENCHMARK` | string | **Float only** — `SOFR` / `EURIBOR 3M` / `SONIA`; omitted on fixed |
| `SPREAD_BPS` | **number** | both; different ladders per coupon type (§20.4.3) |
| `CURVE_REFERENCE` | string | Fixed `"I-Curve + <n> bps"` · Float `"<BENCHMARK> + <n> bps"` — `<n>` must equal `SPREAD_BPS` |
| `INTEREST_DUE` | **string** | total interest to maturity — derived, §20.6.5 |
| `IPO_PRICE` | **string** | `"100"` |
| `PRICE_TYPE` | string | `"Fixed price re-offer"` |
| `PRICING_SPEED` | string | e.g. `"1.30% ABS"` — prepayment-speed convention, asset-type driven; **omitted** where inapplicable (§20.5.3) |
| `PREPAYMENT_TYPE` | string | `ABS` / `CPR` / `PSA` / `SMM`, from `asset_types.csv`; **omitted** where inapplicable (§20.5.3) |
| `RANKING` | string | `Senior Secured` / `Secured` / `Subordinated`, by class |
| `TENOR` | string | `"3Y"` |
| `WAL` | **string** | `"1.40"` — weighted average life in years |
| `MINIMUM_PIECE`, `MINIMUM_INCREMENT` | **number** | `1000` / `1000` |
| `TRANCHE_FITCH`, `TRANCHE_MOODYS`, `TRANCHE_SP` | string | descending by class (§20.6.4) |
| `CREDIT_ANALYST` | string | email from `ref/analysts.csv` |
| `MATURITY_DATE`, `ANT_REDEMPTION_DATE`, `TRANCHE_SETTLEMENT_DATE` | epoch ms | §20.7 |
| `SECURITIES` | **object** | §20.3.1 |

#### 20.3.5 Field inventory — Security level

| Field | Type | Notes |
|---|---|---|
| `SECURITY_ID`, `TRANCHE_ID`, `SERIES_ID` | string | ids |
| `SECURITY_CLASS` | string | **== parent `TRANCHE_CLASS`, always** — see below |
| `REG_TYPE` | string | `144A` \| `Reg S` \| `Reg D` \| `Public` |
| `FORMAT` | string | `"Book Entry"` |
| `SETTLEMENT_TYPE` | string | `"DVP"` |
| `CLEARING_SYSTEM` | string | `DTC` (144A/US) \| `Euroclear` \| `Clearstream` |
| `DEPOSITORY` | string | `DTC` \| `"Euroclear / Clearstream"` |
| `CUSIP` | string | 144A/US only (§20.8) |
| `ISIN` | string | always |
| `FIGI` | string | optional, often `""` |

**`SECURITY_CLASS` always mirrors the parent `TRANCHE_CLASS`. [RESOLVED 2026-08-13]** It is not a
discriminator between the securities of one tranche — if tranche class `A` has two securities,
**both** carry `SECURITY_CLASS: "A"`; if the senior is split, both securities of tranche `A-1` carry
`"A-1"`. What distinguishes the two securities is `REG_TYPE` / `CLEARING_SYSTEM` / identifiers, never
the class. The sample already shows this (both securities of `TEMP-Tranches-0` are class `A`), and it
means the assertion pass (§20.6.6) can treat any mismatch as a hard error rather than a tolerance.

**Field presence varies by security.** In the sample, the 144A securities carry `CUSIP`; the Reg S
security on tranches 0 and 1 **omits `CUSIP` entirely** while the one on tranche 2 carries
`CUSIP: ""`. `FIGI` appears once, empty. The engine omits `CUSIP`/`FIGI` on non-CUSIP-bearing
securities rather than sending `""`. **[RESOLVED 2026-08-13]** — neither is in the mandatory set
(§20.3.7), so omission is safe and the `""` in the sample is incidental.

#### 20.3.6 String-typed numeric fields

Same class of trap as Loans §6.3. Several numeric-looking fields are JSON **strings** in the sample
and must be sent as strings; the rest are JSON numbers. Sending the wrong side produces the familiar
NACK `$.DETAILS.<PATH>: must be valid to one and only one schema, but 0 are valid`.

| Sent as **string** | Sent as **number** |
|---|---|
| `ISSUER_CIK`, `ORIGINATOR_CIK`, `SPONSOR_CIK` | `DEAL_SIZE`, `SERIES_SIZE`, `POOL_SIZE`, `TRANCHE_SIZE` |
| `PERCENT_CA` (`"17"`) | `AOLS`, `FICO_SCORE`, `LTV`, `WA_COUPON`, `WA_MATURITY_MONTHS` |
| `WALA` (`"10 Months"`) | `INITIAL_NOTE_BALANCE`, `NET_PROCEEDS` |
| `WAL` (`"1.40"`) | `INT_RATE`, `YIELD`, `SPREAD_BPS`, `CREDIT_ENHANCEMENT_PCT` |
| `INTEREST_DUE` (`"35000000"`) | `UNDERWRITING_DISC_AND_COMMISIONS`, `MINIMUM_PIECE`, `MINIMUM_INCREMENT` |
| `IPO_PRICE` (`"100"`) | all epoch-ms dates |
| | `IS_ROADSHOW` (bool) |

Note the two near-misses that make this easy to get wrong: `WAL` is a string but `WA_COUPON` is a
number; `INTEREST_DUE` is a string but `NET_PROCEEDS` is a number. The engine keeps a single explicit
`_STRING_FIELDS` set and coerces at serialisation time so this cannot drift.

Unlike the *values* discussed in §20.5.5, these **are** enforced: the type layer is the part of the
schema that actually rejects things.

#### 20.3.7 Mandatory fields — **[RESOLVED 2026-08-13]**

| Level | Required |
|---|---|
| Deal | `ORIGINATOR` (see the naming note in §20.3.2), `ISSUER_NAME`, `ISSUER_TICKER`, `ISSUER_CIK`, `CURRENCY_CODE` |
| Series | `SERIES_NAME`, `CURRENCY_CODE`, `MATURITY_DATE` |
| Tranche | `TRANCHE_CLASS`, `CCY`, `TENOR`, `COUPON_TYPE`, `MATURITY_DATE` |
| Security | `REG_TYPE` (+ `ISIN` wherever one can be produced) |

The generator emits far more than this — the required set is small and the sample is rich — so the
list matters for two things rather than for trimming the payload:

1. **A pre-flight assertion.** The engine checks the required set is present and non-empty on every
   node before posting, and logs an `error` naming the path if not. That converts a class of NACK
   into a local failure with a better message. **[CLARIFIED 2026-08-13, slice 2]** the deal is then
   **skipped** — not posted, not dumped in dry-run — and counted `INVALID` in the summary's `failed`;
   the run continues with the next deal. The §20.6.6 consistency findings are separate and softer:
   they log a `warn` and the deal still posts, because a wrong `WAL` is worth seeing on the wire
   while a missing `ISSUER_TICKER` is only worth a NACK. `securitized_engine.check_payload` returns
   the two lists and is exported for the test harness.
2. **It confirms the identifier fields are optional.** `CUSIP` and `FIGI` are absent from the
   security list, which settles §20.3.5 — omitting them on Reg S securities is correct, and the `""`
   on one sample security is incidental. `ISIN` is *"if possible"*, i.e. wanted but not enforced; the
   engine always produces one (§20.8.2), so this never binds.

Note `CURRENCY_CODE` is required at **both** deal and series level, and the tranche needs `CCY` — the
same value under two different field names across three levels. §20.6.6 asserts they agree.

**[ADDED 2026-08-13, slice 3] — the schema is closed.** `additionalProperties` is `false` at
`$.DETAILS`, so an unrecognised property name is rejected outright:

```
$.DETAILS: property 'MANDATE' is not defined in the schema and the schema does not allow
additional properties
```

Two useful consequences. First, the field inventory in §20.3.2–20.3.5 cannot be extended by guessing
— a wrong guess is a hard NACK, which is why §20.14's "take field names from a captured payload, not
a screenshot" is a rule rather than a preference. Second, that same strictness makes the closed
schema a **field-name oracle**: probing one candidate key per post identifies a field exactly, which
is how `MANDATE_TEXT` was found in §20.3.8.

#### 20.3.8 Conditionally mandatory fields — **[ADDED 2026-08-13, slice 3]**

`MANDATE_TEXT` is required **if and only if `IS_ROADSHOW` is true.** This was not in the spec, not in
the §20.16 Q5 mandatory-field answer, and not in the sample — the sample deal is not a roadshow deal,
so its absence there is invisible. It surfaced on the second live post:

```
{"WARNING":[],"ERROR":[{"@type":"StandardError","CODE":"INTERNAL_ERROR",
  "TEXT":"Mandate Text is required","STATUS_CODE":"500 Internal Server Error"}],
  "MESSAGE_TYPE":"EVENT_NACK","SOURCE_REF":"12345"}
```

Established behaviour, each leg posted live:

| `IS_ROADSHOW` | `MANDATE_TEXT` | Result |
|---|---|---|
| `true` | absent | **NACK** — `"Mandate Text is required"` |
| `true` | present | ACK |
| `false` | absent | ACK |
| `false` | present | ACK — allowed, just not required |

So the field is *permitted* unconditionally and *required* conditionally. The engine emits it only on
roadshow deals, composed from the lead bookrunner, issuer and asset type; on a non-roadshow deal it
is omitted rather than sent empty, consistent with how §20.3.5 treats `CUSIP`/`FIGI`. `check_payload`
carries the rule as a hard **error** (not a warning), because the handler rejects the whole deal — so
the pre-flight turns a NACK into a named local failure, which is the whole point of §20.3.7 item 1.

**Two things worth carrying forward from how this was diagnosed.** The first NACK looked like it
belonged to the Float branch, because step 7.2 was the Float step — but Float was incidental. What
actually differed between the ACKing step 7.1 and the NACKing step 7.2 was `IS_ROADSHOW`, which the
generator draws at random with a ~20% weight (§20.3.2). **A randomised field turns a deterministic
schema rule into an intermittent one**: had the coin landed the other way, this would have been found
weeks later, by someone else, on a run that had "worked before". When a NACK appears on a step whose
axis should not touch the failing rule, diff the two payloads before believing the step label — and
prefer diffing to re-running, because a re-run of a randomised generator is not a controlled
experiment. (Step 7.2's re-run did in fact ACK, which would have been the wrong conclusion.)

The second: the `500` / no-`PATH` shape of the error was itself the clue that this was a business
rule and not a malformed field (§20.2.3).

---

### 20.4 Coupon type — Fixed vs Float

The requirement: *if the coupon type is FIXED populate the fixed fields; if FLOAT, populate the float
fields.*

**`COUPON_TYPE` is a tranche-level field**, so a single series can mix fixed and floating tranches —
which is what real ABS looks like (senior floating off SOFR, subordinate fixed). The run parameter
`coupon_type` (§20.9) allows `Fixed`, `Float`, or `Mixed`.

#### 20.4.1 Fixed

Observed in the sample. Populate:

| Field | Rule |
|---|---|
| `INT_RATE` | 3.5–8.0, rising with subordination |
| `YIELD` | == `INT_RATE` (par re-offer) |
| `IPO_PRICE` | `"100"` |
| `PRICE_TYPE` | `"Fixed price re-offer"` |
| `SPREAD_BPS` | spread to the interpolated curve |
| `CURVE_REFERENCE` | `"I-Curve + <SPREAD_BPS> bps"` |

Float-only fields are **omitted**, not sent as null.

#### 20.4.2 Float — **[RESOLVED 2026-08-13, CONFIRMED live 2026-08-13]**

A floating tranche is priced off a benchmark, so **`INT_RATE` and `YIELD` carry no meaning and are
omitted entirely** — not sent as `0`, not sent as `null`. The rate is expressed as *benchmark +
spread*:

| Field | Rule |
|---|---|
| `BENCHMARK` | the float index — `SOFR` (USD), `EURIBOR 3M` (EUR), `SONIA` (GBP). Currency-driven, from `ref/currencies.csv → FLOAT_BENCHMARK` |
| `SPREAD_BPS` | **round steps of 10** — 20, 30, 40, … rising with subordination (§20.4.3) |
| `CURVE_REFERENCE` | `"<BENCHMARK> + <SPREAD_BPS> bps"`, e.g. `"SOFR + 30 bps"` |
| `IPO_PRICE` | `"100"` |
| `PRICE_TYPE` | `"Fixed price re-offer"` |
| `INT_RATE` | **omitted** |
| `YIELD` | **omitted** |

Everything else on the tranche (sizes, `WAL`, `TENOR`, ratings, `CREDIT_ENHANCEMENT_PCT`,
`NET_PROCEEDS`, dates, `SECURITIES`) is coupon-type agnostic and generated identically.

**A9 is closed: the field is literally `BENCHMARK`.** This was the last unresolved assumption in the
spec (§20.15) and the thing build step 7.2 existed to settle. Eleven live float tranches across
§20.21 posted `BENCHMARK` with no `INT_RATE` and no `YIELD`, and every one ACKed. `BENCHMARK_RATE` and
`FLOAT_INDEX` — the two alternatives the answer to Q1 left room for — would each have produced the
closed-schema NACK of §20.3.7 naming the offending property. They did not.

**`CURVE_REFERENCE` is the one field that changes shape between the two branches.** Fixed emits
`"I-Curve + 28 bps"` (per the sample); Float emits `"SOFR + 30 bps"`. In both cases the bps number
must equal `SPREAD_BPS` — the engine composes the string from the field rather than drawing it
separately, so the two cannot drift.

#### 20.4.3 The two spread ladders differ

Fixed and Float draw `SPREAD_BPS` from **different ladders**, which is why the requirement calls the
float values out explicitly:

| | Fixed | Float |
|---|---|---|
| Granularity | fine, any integer | **multiples of 10** |
| Sample / target | 28 · 85 · 210 (observed) | 20 · 30 · 40 … (specified) |
| Senior class start | 15–40 | 20 |
| Step per class down | ×2 to ×3 | +10 to +40 |

Float senior classes start at 20 bps and step up in multiples of 10 with subordination — so a
three-class float series looks like **20 / 40 / 90**, and a two-class one like **20 / 50**. Values
stay round; a float tranche never carries `SPREAD_BPS: 37`. The monotonic-rise-with-subordination
rule of §20.6.4 holds for both ladders.

#### 20.4.4 Mixed

`coupon_type: "Mixed"` randomises per tranche within a series, which is what real ABS looks like — a
floating senior off SOFR with fixed subordinate classes. `COUPON_TYPE` is a **tranche-level** field,
so no extra structure is needed to support this. Under `Mixed`, the *rise with subordination* rule
(§20.6.4) is enforced **within each coupon type independently** — comparing a fixed tranche's 4.7%
coupon against a float tranche's 30 bps spread is meaningless, so the engine ranks fixed tranches
against fixed and float against float.

---

### 20.5 Reference data

New directory `backend/reference/securitized/`, registered as `reference_dirs.securitized` in
`environments.json` and exposed in Settings alongside the Bonds/Loans dirs. Every file is
**optional** with a hardcoded fallback, matching §5.4 / §6.5.

#### 20.5.1 Files

| File | Columns | Rows |
|---|---|---|
| `entities.csv` | `ISSUER_NAME, ISSUER_TICKER, ISSUER_CIK, ORIGINATOR, ORIGINATOR_TICKER, ORIGINATOR_CIK, SPONSOR_NAME, SPONSOR_TICKER, SPONSOR_CIK, DEPOSITOR, SERVICER, ASSET_FAMILY` | **≥ 2000** — built: **2250** |
| `vocabularies.csv` | `FIELD, VALUE, WEIGHT` — the six controlled lists (§20.5.5) | ~~127~~ **126** |
| `asset_types.csv` | `ASSET_TYPE, PRODUCT_GROUP, INDUSTRY, SUB_INDUSTRY, BUSINESS, PREPAYMENT_TYPE, PRICING_SPEED, POOL_PROFILE, USER_OF_PROCEEDS, ASSET_FAMILY` | ~~57~~ **56** — one row per `ASSET_TYPE` in the workbook |
| `currencies.csv` | `CODE, COUNTRY, COUNTRY_NAME, FLOAT_BENCHMARK, INDEX_LEVEL, CLEARING_SYSTEM, DEPOSITORY, ISIN_PREFIX, GOVERNING_LAW, LISTING_EXCHANGE, DEFAULT_REG_TYPES, DAY_COUNT` | 3 (USD/EUR/GBP) |
| `agents.csv` | `NAME, ROLES` (`trustee\|paying_agent\|registrar\|calculation_agent\|custodian\|backup_servicer`) | 40 |
| `underwriters.csv` | `NAME, TICKER` | 60 |
| `legal_advisors.csv` | `NAME` | 40 |
| `auditors.csv` | `NAME` | 10 |
| `ratings.csv` | `RANK, FITCH, MOODYS, SP` — one row per notch, ordered | 22 |
| `analysts.csv` | `EMAIL` | 30 |

`_generate.py` is the one-off, checked-in builder (`SEED = 20260813`); it is **never called at
runtime** — the engine reads the CSVs only, and the engine itself is deliberately *not* seeded so
consecutive runs differ. Regeneration is byte-stable: re-running it after the §20.18 step-3 workbook
move reproduced all ten files identically.

**[CORRECTED 2026-08-13, slice 1]** three edits above, all forced by the source data:

1. **56 asset types, not 57**, so `vocabularies.csv` is **126** rows, not 127. `ABS_DropDowns.xlsx`
   holds 56 values under `Asset Type` (rows 2–57 of a 137-row sheet); the earlier count was off by
   one. The workbook is the authority and is transcribed as-is. The other five counts (28/17/12/8/5)
   were correct.
2. **`asset_types.csv` gains `ASSET_FAMILY`**, so the file can be joined to `entities.csv` — §20.5.2
   makes the family constrain the asset-type pick but gave the key no home on this side of the join.
3. **`currencies.csv` gains `DAY_COUNT`.** §20.5.4 makes day count currency-driven but the column
   list omitted it and there is nowhere else to put it.

#### 20.5.2 The ≥ 2000-row entity file, and why it is one file

The requirement asks for at least 2000 issuers, originators and sponsors. Drawing those from three
*independent* files would produce deals like "Oakhurst Auto Receivables Trust 2026-1, originated by
Brightline Equipment Finance, sponsored by Cascadia Card Services" — random-looking, but wrong, and
useless for eyeballing a UI or a downstream email. In a real securitization the four names are **one
corporate family**:

```
ORIGINATOR    Oakhurst Auto Finance LLC
SPONSOR       Oakhurst Capital Markets LLC
DEPOSITOR     Oakhurst Auto Receivables Depositor LLC
ISSUER        Oakhurst Auto Receivables Trust 2026-1     ← + vintage
SERVICER      Oakhurst Auto Finance LLC                  ← == originator
TICKERS       OAKAF / OAKCM / OART261
```

So `entities.csv` holds **one row per family, ≥ 2000 rows**, giving ≥ 2000 distinct issuers, ≥ 2000
originators and ≥ 2000 sponsors as asked, but *coherently*. The CSV is generated once by a one-off
script from a brand list × asset-family list, checked in, and never regenerated at runtime.

Two details:

- **`ISSUER_NAME` carries no vintage in the CSV** — it is stored as the shelf stem (`Oakhurst Auto
  Receivables Trust`) and the engine appends the vintage `<year>-<n>` at generation time, so
  re-running the tool in 2027 produces 2027 deals without touching reference data. Likewise
  `ISSUER_TICKER` is stored as the stem `OART` and suffixed to `OART261`.
- **`ASSET_FAMILY`** ties the family to the asset types it plausibly issues (an auto finance company
  does not issue CMBS), and constrains the `asset_types.csv` pick. Built with 9 families × 250 rows;
  every one of the 56 asset types maps to one of the same 9, so there are **0 orphans** and
  constraining the pick still reaches all 56.

**[A]** `SERVICER == ORIGINATOR` unless the row overrides it; ~10% of rows carry a third-party
servicer.

#### 20.5.3 Pool statistics are asset-type dependent

`AOLS`, `FICO_SCORE`, `LTV`, `PERCENT_CA`, `WALA`, `WA_COUPON`, `WA_MATURITY_MONTHS` are a
**consumer/mortgage** vocabulary. `FICO_SCORE: 750` is meaningful for auto and card ABS and
meaningless for CLO or aircraft ABS. `asset_types.csv.POOL_PROFILE` names which statistics apply and
their plausible ranges:

| Profile | Applies | Example ranges |
|---|---|---|
| `consumer_auto` | all | FICO 620–800, LTV 85–110, AOLS 18k–42k, WA_COUPON 5–12, WAM 48–75 |
| `consumer_card` | FICO, PERCENT_CA, WA_COUPON | FICO 660–780, no LTV/AOLS |
| `mortgage` | FICO, LTV, WALA, WA_COUPON, WAM | LTV 60–90, WAM 240–360 |
| `commercial` | LTV, WA_COUPON, WAM | no FICO |
| `corporate_credit` (CLO) | WA_COUPON, WAM | no FICO/LTV/AOLS |
| `esoteric` | WA_COUPON, WAM | aircraft, timeshare, franchise, solar |

Statistics outside the profile are **omitted from the series object**. `PERCENT_CA` (California
geographic concentration) is US-only and is omitted for EUR/GBP deals — the field name hard-codes a
US state, so there is no honest European value to put in it.

**[ADDED 2026-08-13, slice 2] — the same rule extends to `PREPAYMENT_TYPE`/`PRICING_SPEED`.** Slice 1
built `asset_types.csv` with the sentinel pair `PREPAYMENT_TYPE = "None"` / `PRICING_SPEED =
"0% CPR"` on the **20** asset types that have no prepayment convention (aircraft lease, cell tower,
CMBS, WBS, CFO …). Emitting the literal string `"None"` would be plausible-but-wrong data of exactly
the kind §20.6 exists to prevent, so the engine **omits both fields** on those asset types. Neither
is in the §20.3.7 mandatory set, so omission is safe. The alternative — correcting the 20 rows in the
CSV — is a slice-1 data change and was not taken here.

#### 20.5.4 Currency drives more than `CURRENCY_CODE`

Supported: **USD, EUR, GBP**. Currency is chosen per deal (never mixed within a deal — a
multi-currency ABS deal is a different product) and determines:

| | USD | EUR | GBP |
|---|---|---|---|
| `COUNTRY` | `US` | `ES` / `IT` / `FR` / `DE` / `NL` / `IE` | `GB` |
| Reg types | `144A` + `Reg S` | `Reg S` | `Reg S` |
| `CLEARING_SYSTEM` | `DTC` (144A), `Euroclear` (Reg S) | `Euroclear` / `Clearstream` | `Euroclear` / `Clearstream` |
| `DEPOSITORY` | `DTC` / `Euroclear / Clearstream` | `Euroclear / Clearstream` | `Euroclear / Clearstream` |
| ISIN prefix | `US` (144A), `USU` (Reg S) | `XS` | `XS` |
| CUSIP | yes | no | no |
| `GOVERNING_LAW` | `NY Law` | `Irish Law` / `Dutch Law` / `English Law` | `English Law` |
| `LISTING_EXCHANGE` | `Unlisted` | `Euronext Dublin` / `LuxSE` | `London Stock Exchange` |
| `FLOAT_BENCHMARK` → `BENCHMARK` | `SOFR` | `EURIBOR 3M` / `ESTR` | `SONIA` |
| `DAY_COUNT` | `30/360` | ~~`Actual/360`~~ `ACT/360` | ~~`Actual/365`~~ `ACT/36S` |

**[CORRECTED 2026-08-13, slice 1]** the `DAY_COUNT` row had escaped §20.5.5 trap 1. `Actual/360` and
`Actual/365` are not vocabulary members. `ACT/360` is; **`ACT/365` is not** — the workbook's ACT/365
is spelled `ACT/36S` (trap 2), so that is what a GBP deal carries. `currencies.csv.DAY_COUNT` holds
these three values and slice 1 asserts each is a member of the `DAY_COUNT` vocabulary.

**[RESOLVED 2026-08-13]** for the two that mattered: **a EUR deal takes the ISO2 of a euro-area
country** — `ES`, `IT`, `FR`, `DE`, `NL`, `IE` — and **no CUSIP is expected on a EUR deal**.

**[CLARIFIED 2026-08-13, slice 3] — A8 is *not* closed by the live run, and it is worth being
precise about why.** A EUR deal and a GBP deal both ACKed (§20.21 steps 7.6b and 7.6c). That proves
they post; it does **not** confirm the GBP listing venue, governing law or ISIN prefix are right,
because §20.5.5 establishes that the handler does not validate string values at all. An ACK on a
non-schema-controlled string is evidence of nothing beyond well-formedness. A8 therefore remains an
assumption, and the way to close it is still a real GBP sample payload — not another ACK.

#### 20.5.5 Controlled vocabularies — `vocabularies.csv`

`ABS_DropDowns.xlsx` (sheet `Drop-down Lists`) is the authority for six fields. Its values are
transcribed **verbatim** into `backend/reference/securitized/vocabularies.csv` as
`FIELD, VALUE, WEIGHT`, and the engine draws only from it. Free-text fields keep their generators.

| Field | Values | Notes |
|---|---|---|
| `ASSET_TYPE` | ~~57~~ **56** | `Auto Loan ABS` (the sample) … `Re-Performing Loan (RPL) Securitization`. Long, parenthesised names are exact: `Federal Student Loan (FFELP) ABS`, `Buy Now Pay Later (BNPL) ABS`, `Solar Power Purchase Agreement (PPA) ABS`, `Whole Business Securitization (WBS)`. **[CORRECTED 2026-08-13, slice 1]** — the workbook holds 56, not 57 |
| `RANKING` | **28** | `1st Lien` … `Unsecured`. The sample's `Senior Secured` / `Secured` / `Subordinated` are all members ✔ |
| `COUPON_TYPE` | **17** | `Auction, Defaulted, Exchanged, Fixed, Fixed to Fixed, Fixed to Float, Flat Trading, Float, Funged, Hybrid, OID, Pay-In-Kind, Step, Structured, Tax Credit, When Issued, Zero` |
| `ACCRUAL_METHOD` | **12** | `Simple interest accrual` … `Excess spread accrual / release`. The workbook documents each one's meaning; `Actual balance accrual` (the sample) = interest on the actual balance after payments/prepayments/charge-offs |
| `DAY_COUNT` | **8** | `30/360, 30/365, 30/ACT, ACT/ACT, ACT/ACT (ICMA), ACT/360, ACT/36S, ACT/36S (FIXED)` |
| `BUSINESS_DAY_CONVENTION` | **5** | `Following, Modified Following, Preceding, Modified Preceding, No Adjustment (Unadjusted)` |

Three traps in that table:

1. **`ACT/`, not `Actual/`.** The first draft of this spec proposed `Actual/360` and `Actual/Actual`;
   neither exists. It is `ACT/360` and `ACT/ACT`.
2. **`ACT/36S` is spelled with an `S`, twice** (`ACT/36S` and `ACT/36S (FIXED)`). Almost certainly a
   typo for `ACT/365` in the source system — **transcribe it as-is**. Same rule as
   `UNDERWRITING_DISC_AND_COMMISIONS`: this spec reproduces the platform's spellings, it does not
   correct them.
3. **`COUPON_TYPE` has 17 members, and v1 generates 2 of them.** `Fixed` and `Float` are what §20.4
   covers, and the two confirm the sample's title-case exactly. The other 15 are valid platform
   values that the generator does not produce — `Fixed to Float`, `Step`, `Pay-In-Kind` and `Zero`
   each imply their own field set (step schedules, PIK accruals, no coupon at all), so quietly
   emitting them with fixed-rate fields would generate invalid deals. They are **out of scope for
   v1** (§20.12), listed here so the boundary is explicit rather than accidental. `vocabularies.csv`
   carries all 17 with `WEIGHT=0` on the unsupported ones, so enabling one later is a data change
   plus a generator branch.

##### The vocabularies are for data quality, not for passing ingest — **[ADDED 2026-08-13, slice 3]**

**The handler does not validate string values. At all.** This was measured, not inferred. Two
negative controls, each a single live post:

| Sent | Result |
|---|---|
| `SERIES.PRODUCT_GROUP = "ZZ_NOT_A_PRODUCT_GROUP"` | **ACK** |
| `SERIES.DAY_COUNT = "Actual/360"` — a known non-member, trap 1 above | **ACK** |

The schema enforces property **names** (§20.3.7, closed schema), **types** (§20.3.6, `oneOf` on the
string-typed numerics) and **presence** (§20.3.7, plus the conditional rule in §20.3.8). Enumerated
values it does not touch.

This reframes the whole of §20.5.5, and two consequences follow.

First, **the six vocabularies matter more than an ACK-based test can show, not less.** Transcribing
`ACT/36S` verbatim buys nothing at ingest — `Actual/360` would have posted just as happily. It buys
correctness in every consumer downstream of ingest: the UI dropdown that has to match the stored
value, the report that groups by day count, the §18-style comparison that would score a mismatch. A
generator whose only bar is "it ACKed" produces data that is uniformly well-formed and quietly wrong,
which is precisely the failure mode §20.6 exists to prevent.

Second, **an ACK is not evidence that a value is right.** That is the single most important thing
this section now says, and it retires a test design that looked reasonable: build step 7.7 was
written to settle [Q14] by posting a non-`ABS` `PRODUCT_GROUP` and reading the outcome — ACK meaning
"the platform accepts this vocabulary", NACK meaning "it is constrained". The negative control shows
the experiment has no power: everything ACKs, so the outcome carries no information either way. Any
future question of the form "is this value legal?" needs a different instrument — a captured payload,
the application's own dropdown, or a downstream read — because the ingest endpoint will not answer
it.

##### `PRODUCT_GROUP` — **[Q14], resolved as far as it can be, 2026-08-13, slice 3**

Given the above, the `PRODUCT_GROUP` decision rests on data quality rather than on the step-7.7 ACK.
`asset_types.csv` now carries `ABS` on 51 of 56 rows, with the five asset types that **name their own
product group** carrying it:

| `ASSET_TYPE` | `PRODUCT_GROUP` |
|---|---|
| `Residential Mortgage-Backed Securities (RMBS)` | `RMBS` |
| `Commercial Mortgage-Backed Securities (CMBS)` | `CMBS` |
| `Collateralized Loan Obligation (CLO)` | `CLO` |
| `Collateralized Debt Obligation (CDO)` | `CDO` |
| `Collateralized Bond Obligation (CBO)` | `CBO` |

`PRODUCT_GROUP: "ABS"` on an RMBS deal is exactly the plausible-but-wrong data this spec keeps
warning about, and `RMBS` posted live and ACKed (§20.21 step 7.7). Deliberately **not** overridden:
`Collateralized Fund Obligation (CFO)`, which market usage puts inside the CDO family rather than
giving a group of its own, and the NPL/RPL securitizations, which are mortgage-adjacent but not
marketed as RMBS — a guess in either case would be fabrication rather than test data. The override
table lives in `_generate.py::PRODUCT_GROUP_OVERRIDES`, so reverting to `ABS` everywhere is a
one-line edit plus a regeneration.

**This remains the weakest-evidenced choice in §20** and is flagged as such: nothing observed proves
the platform *recognises* `RMBS` as a product group, only that it does not reject it. If a downstream
consumer turns out to branch on a closed set of `PRODUCT_GROUP` values, revert the table.

The other twelve vocabularies not in the workbook — `REG_TYPE`, `FORMAT`, `SETTLEMENT_TYPE`,
`CLEARING_SYSTEM`, `DEPOSITORY`, `PREPAYMENT_TYPE`, `PRICE_TYPE`, `INVESTOR_STATUS`,
`SETTLEMENT_PERIOD`, `COUPON_FREQUENCY`, `GOVERNING_LAW`, `LISTING_EXCHANGE` — are generated from the
sample's observed values plus market convention. The sample is a real payload the platform accepted,
so its values (`144A`, `Reg S`, `Book Entry`, `DVP`, `DTC`, `Euroclear`, `ABS`, `Fixed price
re-offer`, `OPEN`, `T+3`, `Monthly`, `NY Law`, `Unlisted`) are the best available evidence — which,
per the paragraph above, is *provenance* evidence and not ACK evidence. A second workbook tab would
still close this properly.

---

### 20.6 Internal consistency — the part that makes the data useful

Random values that do not add up are worse than useless for testing an issuance platform: they pass
ingest and then fail every downstream validation, aggregate and report. The generator therefore
derives rather than randomises wherever the sample shows a relationship. **Every rule below was
verified arithmetically against `oakhurst_deal.json`.**

Since §20.5.5 established that ingest validates nothing about values, this section is not belt and
braces — it is the *only* thing standing between the generator and uniformly well-formed nonsense.

#### 20.6.1 Sizes roll up exactly

```
TRANCHE_SIZE           ← drawn per tranche
INITIAL_NOTE_BALANCE   = TRANCHE_SIZE
SERIES_SIZE            = Σ TRANCHE_SIZE over the series
DEAL_SIZE              = Σ SERIES_SIZE over the deal
```

Sample: 420,000,000 + 55,000,000 + 25,000,000 = 500,000,000 = `SERIES_SIZE` = `DEAL_SIZE`. ✔

The engine draws the *deal* size first from a plausible ladder (250M–2B), splits it across series,
then splits each series across its tranches on a senior-heavy waterfall (~84% / 11% / 5% for three
classes), rounding to the nearest 100,000 and giving the rounding remainder to the senior class so
the sum stays exact.

#### 20.6.2 Pool over-collateralises the notes

```
POOL_SIZE = SERIES_SIZE × (1 + OC),  OC ∈ [0.03, 0.08]
```

Sample: 500,000,000 × 1.06 = 530,000,000. ✔ `POOL_SIZE > SERIES_SIZE` always.

#### 20.6.3 Credit enhancement = subordination below you

```
CREDIT_ENHANCEMENT_PCT(t) = 100 × (Σ TRANCHE_SIZE of every tranche junior to t's CREDIT CLASS) / SERIES_SIZE
```

Sample: class A → (55M + 25M)/500M = **16** ✔ · class B → 25M/500M = **5** ✔ · class C → most junior,
gets a small residual from the reserve account/OC = **1** ✔.

**"Credit class", not "tranche"** — this is the detail the `A-1…A-4` split (§20.6.4) turns on.
Sequential-pay senior tranches are *pari passu in credit* and differ only in payment timing, so
`A-1`, `A-2` and `A-3` all sit above the same subordination and therefore all carry the **same**
`CREDIT_ENHANCEMENT_PCT`. Computing CE per tranche instead of per class would hand `A-1` a higher
number than `A-2`, which is wrong — nothing is subordinated to `A-1` that is not equally subordinated
to `A-2`. (Observed holding on the wire: §20.21 step 7.4 posted `A-1`/`A-2`/`A-3` all at CE 17.1%.)

So CE falls monotonically **across credit classes** (A → B → C → D), is flat within one, and the most
junior class gets `reserve_pct` (0.5–2.0). `CREDIT_ENHANCEMENT_PROVIDERS` at deal level is composed
from the features actually used — `"Overcollateralization + subordination + reserve account"` when
OC > 0, more than one class, and a reserve exists.

**[CORRECTED 2026-08-13, slice 2] — the reserve must be capped, or the monotonic rule breaks.** The
two rules above conflict on a deep stack. `reserve_pct` is drawn independently of the structure, but
the *second*-most-junior class's CE is `class_size(most junior) / SERIES_SIZE × 100` — and on a
5-class series that class is thin, so its CE routinely lands **below** a reserve drawn near the top of
the 0.5–2.0 range. Observed on the first 5-series/8-tranche run: class `D` → 0.80, class `E` → 1.67,
i.e. CE *rose* at the bottom of the stack. The engine therefore caps the reserve at **half the
thinnest computed CE** (floor 0.05) whenever there is more than one class. A single-class series keeps
the drawn value, since there is nothing to be monotone against. `securitized_engine.py::
_generate_series` carries the same note.

#### 20.6.4 The capital structure, and how ratings and pricing descend it

**[REVISED 2026-08-13 per Q9]** — a real ABS series is 3–6 tranches on a standard ladder, and the
senior class is routinely **split by maturity** into `A-1`, `A-2`, `A-3`, `A-4`. `TRANCHE_CLASS`
therefore is not always a bare letter. The generator lays out classes by tranche count:

| Tranches | `TRANCHE_CLASS` sequence |
|---|---|
| 1 | `A` |
| 2 | `A`, `B` |
| 3 | `A`, `B`, `C` ← the sample |
| 4 | `A-1`, `A-2`, `B`, `C` |
| 5 | `A-1`, `A-2`, `A-3`, `B`, `C` |
| 6 | `A-1`, `A-2`, `A-3`, `B`, `C`, `D` |
| 7 | `A-1`, `A-2`, `A-3`, `A-4`, `B`, `C`, `D` |

`A` splits first because that is where the size is; the senior stack stays ~80–88% of the series
regardless of how many pieces it is cut into. `D` is the subordinate / first-loss class. The
residual/equity piece is **not** emitted as a tranche — it is frequently not formally labelled as one,
and inventing a size and rating for it would be fabrication rather than test data.

##### Within the senior stack (`A-1` … `A-4`)

These are **sequential-pay**: same credit, different timing. So they share and differ on exactly this
split:

| Shared across all `A-x` | Differs across `A-x` |
|---|---|
| `TRANCHE_FITCH` / `TRANCHE_MOODYS` / `TRANCHE_SP` (all AAA/Aaa/AAA) | `TENOR` — strictly ascending |
| `CREDIT_ENHANCEMENT_PCT` (§20.6.3) | `WAL`, `MATURITY_DATE`, `ANT_REDEMPTION_DATE` — ascending |
| `RANKING` (`Senior Secured`) | `TRANCHE_SIZE` |
| `PREPAYMENT_TYPE`, `PRICING_SPEED` | `SPREAD_BPS` — rises, but only by a **term premium** |
| `TRANCHE_DESCRIPTION` stem (`Senior …`) | `INT_RATE`, `UNDERWRITING_DISC_AND_COMMISIONS` |

The term premium is small — a few bp per step (fixed `+2…8`, float `+10`, keeping the round-number
rule of §20.4.3) — because you are being paid for duration, not for credit. A 40 bp jump from `A-1` to
`A-2` would imply a rating difference that is not there. The real jumps happen at class boundaries.

**[ADDED 2026-08-13, slice 2] — the class step must be floored against the accumulated term
premium.** Drawing the class step and the term premium independently, as the two lists above imply,
lets the bottom of a 4-piece senior stack overtake the class below it: a fixed senior starting at
15 bp with `+8` per step reaches 39 bp at `A-4`, while a `×2` class step puts `B` at 30. The engine
therefore computes the class base as `max(drawn_step, last_value_in_the_class_above + margin)` —
margin `+10 bp` for both spread ladders, `+0.10 %` for `INT_RATE`, and a fixed `+0.005 %` per senior
step for `UNDERWRITING_DISC_AND_COMMISIONS` against a class step of `+0.05…0.15`. The stated *ranges*
are unchanged; only their floor is. Without this the monotonic-rise rule fails on roughly one
5-tranche series in fifteen.

##### Across credit classes (A → B → C → D)

Moving down the stack, **monotonically**:

- `TRANCHE_FITCH` / `TRANCHE_MOODYS` / `TRANCHE_SP` step down `ratings.csv` by 2–5 notches per class,
  starting at AAA/Aaa/AAA. All three agencies stay within one notch of each other (sample:
  AAA/Aaa/AAA → AA/Aa2/AA → BBB/Baa2/BBB ✔).
- `INT_RATE` **rises** (fixed tranches): 4.7 → 5.4 → 6.5 ✔
- `SPREAD_BPS` **rises**: fixed 28 → 85 → 210 ✔ · float 20 → 40 → 90 (§20.4.3)
- `UNDERWRITING_DISC_AND_COMMISIONS` **rises**: 0.30 → 0.35 → 0.45 ✔
- `WAL` **rises**: 1.40 → 3.2 → 3.9 ✔
- `RANKING`: `Senior Secured` → `Secured` → `Subordinated` ✔ (all three are members of the 28-value
  vocabulary, §20.5.5)
- `TRANCHE_DESCRIPTION`: `Senior …` → `Mezzanine …` → `Junior mezzanine …` → `Subordinate …` ✔

`SECURITY_CLASS` follows `TRANCHE_CLASS` verbatim, so a split senior yields `SECURITY_CLASS: "A-1"`
on **every** security under it (§20.3.5). **[RESOLVED 2026-08-13]**

#### 20.6.5 Derived scalars

```
NET_PROCEEDS    = TRANCHE_SIZE × (1 − UNDERWRITING_DISC_AND_COMMISIONS / 100)
YIELD           = INT_RATE                                    (Fixed only; par re-offer, IPO_PRICE = "100")
CURVE_REFERENCE = "I-Curve + "   + SPREAD_BPS + " bps"        (Fixed)
                = BENCHMARK + " + " + SPREAD_BPS + " bps"     (Float)
```

`NET_PROCEEDS` verified on all three sample tranches: 420,000,000 × 0.997 = **418,740,000** ✔ ·
55,000,000 × 0.9965 = **54,807,500** ✔ · 25,000,000 × 0.9955 = **24,887,500** ✔.
`YIELD == INT_RATE` on all three ✔. `CURVE_REFERENCE` matches `SPREAD_BPS` on all three ✔.

**`INTEREST_DUE` is total interest to maturity. [RESOLVED 2026-08-13]** For an amortizing structure
that is

```
INTEREST_DUE = str(round(TRANCHE_SIZE × rate / 100 × WAL))
```

— `WAL` rather than `TENOR`, because weighted average life *is* the average time principal is
outstanding, which is exactly what total interest integrates over. Using `TENOR` would overstate it
by 2–3× on a fast-amortizing pool.

On a **float** tranche there is no `INT_RATE`, so `rate` is the assumed all-in coupon
`INDEX_LEVEL + SPREAD_BPS/100`, where `INDEX_LEVEL` is a current-market constant per benchmark in
`ref/currencies.csv` (SOFR ≈ 4.3, EURIBOR 3M ≈ 2.4, SONIA ≈ 4.0) used **only** for this estimate — it
is never sent as a field.

Note that **the sample's own values do not follow this** (420M × 4.7% × 1.40 = 27.6M, not the
`"35000000"` shown, and the other two tranches miss by more in both directions). The formula follows
the stated definition; the sample appears to be hand-entered. Generated data will be self-consistent
where the sample is not — which is the desirable direction for a test-data generator.

#### 20.6.6 Cross-level agreement

`SECURITY_CLASS == TRANCHE_CLASS`; `CCY == SERIES.CURRENCY_CODE == DETAILS.CURRENCY_CODE`; every
child's `SERIES_ID`/`TRANCHE_ID` matches its enclosing key; `AUDIENCE` names only reg types that were
actually generated; `SERVICER` appears in both the deal fields and (implicitly) the family. The engine
runs an internal assertion pass over the built tree before serialising and logs a `warn` on any
violation rather than posting silently-broken data.

---

### 20.7 Dates

All dates are **epoch milliseconds, UTC midnight**, as in Loans (`loan_utils/dates.py::to_epoch_ms`).
The existing `_roll_to_weekday` / `_advance` helpers are reused; ABS-specific sequencing lives in
`engines/abs_utils/dates.py`.

#### 20.7.1 Ordering (verified against the sample)

```
ANNOUNCEMENT_DT  →  EXPECTED_PRICING_DATE  →  SETTLEMENT_DATE  →  FIRST_COUPON_DT
      2026-08-05          2026-08-12 (+7d)       2026-08-17 (+5d)      2026-09-17 (+31d)
```

Then per tranche:

```
TRANCHE_SETTLEMENT_DATE  ≥ series SETTLEMENT_DATE
ANT_REDEMPTION_DATE      ≈ SETTLEMENT_DATE + WAL years      (expected call/redemption)
MATURITY_DATE            = ANNOUNCEMENT_DT + TENOR years    (legal final for the class)
ANT_REDEMPTION_DATE      <  MATURITY_DATE                   (always)
```

Verified: tranche tenors 3Y/5Y/6Y land at announcement + 1106 / 1836 / 2202 days ✔.

**[ADDED 2026-08-13, slice 1]** two rules had to be recovered from the sample to reproduce those
day-counts and settlement dates. Both live in `engines/abs_utils/dates.py`:

- **Payment day 15.** A bare anniversary gives 1096/1826/2192. The sample's maturities are the
  anniversary **rolled forward to the 15th** — the monthly ABS payment date — giving 2029-08-15 /
  2031-08-15 / 2032-08-15 and the stated 1106/1836/2202. There is no business-day adjustment on
  maturity: 2032-08-15 is a Sunday in the sample and stays there. `ANT_REDEMPTION_DATE` lands on the
  same payment day.
- **Tranche settlement steps one calendar month per tranche**, then rolls to a weekday. The sample's
  three tranches settle 2026-08-17, 2026-09-17 and 2026-10-19 — the third is Saturday 2026-10-17
  rolled to Monday.

And at series level:

```
SERIES.MATURITY_DATE = max(tranche MATURITY_DATE) + 1 day    ← legal final maturity of the series
```

Verified: 1,976,227,200,000 − 1,976,140,800,000 = 86,400,000 ms = exactly one day ✔.

#### 20.7.2 Tenor ladder

Tranche tenors within a series are drawn **without replacement, strictly ascending down the stack** —
the most senior tranche is always the shortest. Pool: `1Y, 2Y, 3Y, 4Y, 5Y, 6Y, 7Y, 10Y`. `WAL <
tenor` always, and `WAL` ascends with it.

This ordering is what makes an `A-1`…`A-4` split meaningful (§20.6.4): those tranches are
distinguished *only* by timing, so the tenor ladder is the thing separating them. `A-1` takes the
shortest tenor in the pool, `A-2` the next, and the mezzanine/subordinate classes take the long end.

#### 20.7.3 Multi-series

Series *n+1* is announced 1–4 weeks after series *n* within the same deal, and its whole date chain
shifts accordingly. `ANNOUNCEMENT_DT` at deal level is the **earliest** series announcement.

---

### 20.8 Security identifiers

#### 20.8.1 CUSIP

The sample's three 144A CUSIPs are `67543MAA4`, `67543MAB2`, `67543MAC0` — a **shared 6-character
issuer base (`67543M`)** with a 2-character issue suffix that increments per class (`AA`, `AB`, `AC`)
and a check digit. **The structure is right and the check digits are not** — see the correction in
§20.19.3; the generator computes them rather than copying the sample. The issue suffix skips `I` and
`O` by market convention.

So: one CUSIP base per **deal**, and the 2-character suffix advances `AA`, `AB`, `AC`, … **once per
tranche** in stack order — not per credit class, since `A-1` and `A-2` are separate instruments
needing separate identifiers. Check digit via the existing `loan_utils/cusip.py::check_digit` (the
modulus-10 double-add-double already implemented for Loans — no new algorithm needed).

#### 20.8.2 ISIN

```
144A / US:  ISIN = "US" + CUSIP + check      →  67543MAA4  →  US67543MAA48   ✔
Reg S / US: ISIN = "US" + "U" + base5 + suffix + check  →  USU67543AA55     ✔
EUR / GBP:  ISIN = "XS" + 9 digits + check
```

Both US forms verified against the sample. The ISIN check digit uses the existing
`bonds_engine::_isin_check`. The Reg S "U"-prefixed form is the standard CINS convention for Reg S
tranches of a US deal.

#### 20.8.3 FIGI

12 characters, `BBG` + 8 alphanumerics + check, via the existing `bonds_engine::_gen_figi`. Emitted on
~30% of securities; omitted otherwise (§20.3.5).

#### 20.8.4 Uniqueness

The engine tracks issued CUSIP bases and ISINs **for the lifetime of the run** and redraws on
collision, so a 50-deal run never posts a duplicate identifier. This is
`abs_utils/identifiers.py::IdentifierPool`; **one instance per run**, and everything is drawn from it
rather than from the module-level generators.

---

### 20.9 Run parameters (UI)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `deals` | int | 1 | Number of deals to generate and post |
| `series_per_deal` | list[int] | `[1]` | Series count per deal — cycles through the list across deals. Realistic max **4** |
| `tranches_per_series` | list[int] | `[3]` | Tranche count per series — cycles across all series in the run. Realistic range **3–6** (§20.6.4) |
| `securities_per_tranche` | list[int] | `[2]` | 1 or 2 — cycles across all tranches |
| `currency` | string | `""` | `USD` \| `EUR` \| `GBP`; blank = random per deal |
| `coupon_type` | string | `Fixed` | `Fixed` \| `Float` \| `Mixed` (per-tranche random). The other 14 platform values are out of scope (§20.5.5) |
| `deal_type` | string | `""` | Force one of `ABS` / `CLO` / `CMBS` / `RMBS`; blank = random per deal. Drives asset selection, `ISSUER_SECTOR` and `SUB_INDUSTRY` (§20.3.2) |
| `delay` | float | `1.0` | Seconds between deal POSTs |
| `dry_run` | bool | `false` | Generate and log payloads; no auth, no POST |

Values beyond the realistic ranges are **allowed, not clamped** — this is a test utility, and pushing
8 series × 10 tranches at the endpoint to see what it does is a legitimate thing to want. The engine
logs a `warn` when a run exceeds 4 series or 6 tranches per series so an accidental fat-finger is
visible, then proceeds.

**Cycling lists** follow the established `tranches_per_multi` convention (§5.2, §6.2): the UI takes a
comma-separated string (`"1, 2, 3"`), the engine accepts a bare int and normalises it to a
single-element list, and index `i % len(list)` selects. This is how the requirement *"the user needs
the ability to specify how many series, tranches and securities they want for each deal"* is met while
keeping a run heterogeneous — `deals=6, series_per_deal="1,2,3"` produces two each of 1-, 2- and
3-series deals in one run.

Both shapes called out in the requirement are expressible:

- *single series, single tranche, 1–2 securities* → `series_per_deal="1"`,
  `tranches_per_series="1"`, `securities_per_tranche="1, 2"`
- *two or more series, single or multiple tranches each, 1–2 securities* → `series_per_deal="2, 3"`,
  `tranches_per_series="1, 3, 2"`, `securities_per_tranche="2"`

`securities_per_tranche` values outside `{1, 2}` are clamped, with a `warn`. An unknown `deal_type`
(not one of `ABS`/`CLO`/`CMBS`/`RMBS`) is ignored with a `warn` and one is drawn at random per deal;
an unknown `currency` is a hard `error` that ends the run, because currency drives too much (§20.5.4)
to guess at.

#### 20.9.1 `tool_defaults.securitized`

```json
"securitized": {
  "deals": 1,
  "series_per_deal": [1],
  "tranches_per_series": [3],
  "securities_per_tranche": [2],
  "currency": "",
  "coupon_type": "Fixed",
  "deal_type": "",
  "delay": 1.0
}
```

**[CORRECTED 2026-08-13, slice 3]** — the original wording said this block is "editable in Settings,
alongside the existing per-tool default blocks". There were no such blocks: `SettingsTab.jsx` edited
environments, `reference_dirs`, `email` and `compare`, and passed `tool_defaults` through untouched
via the `...config` spread. Bonds', Loans', Interest's and TIG's defaults are still only editable by
hand in `environments.json`. Slice 3 added the **first** `tool_defaults` editor — a *Securitized (ABS)
defaults* section, plus the *Securitized reference dir* field — so the sentence is now true of
`securitized` and remains false of the other four. Anyone extending this should note the pattern the
new section establishes: hold the whole `tool_defaults` object in state, edit one tool's sub-object,
write the whole thing back, so an unedited tool's block cannot be dropped.

#### 20.9.2 Form state and history

Standard tab behaviour (§13, §14): parameters persist to `localStorage` under `pbi.securitized`; the
tab POSTs a history record on completion via `useRunState('securitized', recordRun)` with

```
params_summary: "3 deals · 2 series · 3 tranches · RMBS · USD · Fixed"
```

and History re-run prefills (never auto-fires). The tab id, the `TABS` entry id, the router prefix and
the history `tool` value are all the single string `securitized`, which is what lets
`App.jsx::rerunFromHistory` switch straight to the tab by name.

#### 20.9.3 The deal-type dropdown — **[REPLACED 2026-09-09; was the asset-type dropdown]**

The tab offers a **Deal Type** select — `ABS` / `CLO` / `CMBS` / `RMBS`, plus a blank "random per
deal" default. The four values are a small static list in the JSX (`DEAL_TYPES`), so — unlike the old
56-value asset-type dropdown — there is **no supporting endpoint**. The earlier `GET
/api/securitized/asset_types` route and its `getSecuritizedAssetTypes` client helper were removed
along with the Asset Type control. `routers/securitized.py` is once again exactly "`routers/loans.py`
with the tool name swapped" (§20.10) — no extra routes.

---

### 20.10 Engine contract and logging

`run_securitized(params, env, stop_event, log_queue)` follows the engine contract in `CLAUDE.md`
exactly — `log_queue` dicts only (no `print`), `stop_event` checked before every HTTP call and inside
every delay via `_interruptible_sleep`, a `{"type":"summary", …}` then the `None` sentinel at the end.
`routers/securitized.py` is `routers/loans.py` with the tool name swapped (the read-only
`/asset_types` route was removed 2026-09-09, see §20.9.3); `_shared.start_tool_run` gains one line:

```python
elif tool == "securitized":
    params.setdefault("ref_dir", ref_dirs.get("securitized", ""))
```

No other router change is needed — per-tool params flow through the `params` dict. Because the
contract is identical to `/api/loans`, the frontend's `useRunState` hook works unchanged.

#### 20.10.1 Log shape

One deal is one POST, so the per-event prefix is `[D<n>]` — there is no `/T<n>` component. The
structure is logged **before** the POST so a NACK can be read against the tree that produced it:

```
Loading reference data from …\reference\securitized ...
Reference data ready — 2250 entity families, 56 asset types, 3 currencies.
Plan: 2 deals = 2 POST(s). dry_run=False
[D1] Hartwell Auto Receivables Trust 2026-2 (HRTA262) — USD · Auto Loan ABS · 600,000,000
[D1]   Series TEMP-Series-0 "HRTA 2026-2" — 600,000,000 / pool 637,400,000 · 3 tranche(s)
[D1]     A    490,800,000  Fixed      4.61%    +26bp  AAA/Aaa/AAA     CE  18.2%  2 sec
[D1]     B     75,300,000  Fixed      5.62%    +59bp  A/A2/A          CE   5.7%  2 sec
[D1]     C     33,900,000  Fixed      6.70%   +169bp  BBB/Baa2/BBB    CE   1.6%  2 sec
[D1] ACK — 1 series · 3 tranches · 6 securities
```

A float tranche prints its benchmark where a fixed one prints its coupon, so a `Mixed` run is
readable at a glance:

```
[D1]     A    327,900,000  Float       SOFR    +20bp  AAA/Aaa/AAA     CE  18.0%  2 sec
[D1]     B     49,700,000  Float       SOFR    +40bp  AA-/Aa3/AA-     CE   5.6%  2 sec
[D1]     C     22,400,000  Float       SOFR    +80bp  BBB/Baa2/BBB    CE   1.9%  2 sec
```

Both blocks above are verbatim from live runs (§20.21).

#### 20.10.2 NACK logging

The **full `ERROR` array** is logged, not just the first entry — same rule as Loans §10.1, and more
important here: one ABS POST carries a whole tree, so a single NACK can name a dozen field paths at
once and the first line is rarely the whole story.

```
[D1] NACK (3 error(s)) — $.DETAILS.SERIES.TEMP-Series-0.TRANCHES.TEMP-Tranches-0.WAL: must be valid to one and only one schema, but 0 are valid
[D1]      also — $.DETAILS.…
[D1]      also — $.DETAILS.…
```

Because the error paths are deep, the engine logs the **full JSON path** verbatim rather than
truncating — that path is how you find which tranche of which series is wrong. Note from §20.2.3 that
only `FieldError` entries carry a path at all; a `StandardError` is a business rule and its `TEXT` is
prose.

A failed deal does not stop the run; the next deal is attempted. There is no cross-deal state.

---

### 20.11 Dry run

Same contract as the other three tools (§9.1): **no auth, no POST**, the fully-built payload
pretty-printed to the log with `json.dumps(payload, indent=2)`, and counted as `DRY_RUN` in the
summary.

ABS dry-run is materially more useful than the others — it is the only way to eyeball the whole
nested tree, and it has **no missing-field caveat** (Loans dry-run cannot show `MASTER_LOAN_ID` on
tranches 2+ because that value comes from a live query; ABS has no such dependency, so a dry-run
payload is byte-identical to what would be posted).

---

### 20.12 Out of scope for v1

Stated explicitly so the boundary is not ambiguous:

- **Email generation** (§17) — no ABS broker-email formats. `build_email` is not touched.
- **Expectation capture / Email Compare** (§18) — no `util_*` mirror writes, no field map, no
  comparator vocabulary for ABS. Adding it later is a self-contained follow-up: an ABS field map in
  §18.5 terms plus the four mirror tables at deal/series/tranche/security grain.
- **CSV upload** of ABS deals (§8) — the nested structure does not fit a flat CSV row without a
  parent-key convention.
- **Amend/update events** — creation only. No `EVENT_UPDATE_*`, no adding a series to an existing
  deal.
- **Interest Capture / TIG orders against ABS tranches** — untested; the tranche id format may
  differ.
- **15 of the 17 `COUPON_TYPE` values** — only `Fixed` and `Float` are generated. `Fixed to Float`,
  `Step`, `Pay-In-Kind`, `Zero` and the rest each imply their own field set, and emitting them with
  fixed-rate fields would produce invalid deals. They sit in `vocabularies.csv` at `WEIGHT=0`
  (§20.5.5).
- **The residual / equity piece** — real deals often carry one and often do not label it as a tranche
  (§20.6.4). Inventing a size and rating for it would be fabrication, not test data.

---

### 20.13 Build order

All eight steps are complete. Kept as the record of what was built in what order, and because step 7
is the shape any future ABS schema question should be tested in.

1. `backend/reference/securitized/` — a one-off script that (a) transcribes `ABS_DropDowns.xlsx` into
   `vocabularies.csv` verbatim, (b) builds `asset_types.csv` by attaching `PRODUCT_GROUP`/`INDUSTRY`/
   `POOL_PROFILE`/etc. to each of the 56 asset types, and (c) generates `entities.csv` (≥ 2000
   families). All checked in, generated once, never regenerated at runtime.
2. `engines/abs_utils/{dates.py, identifiers.py}` — date sequencing and CUSIP/ISIN/FIGI, reusing
   `loan_utils/cusip.py` and `bonds_engine._isin_check`.
3. `engines/securitized_engine.py` — `ReferenceData` loader → `DealGenerator` (deal → series →
   tranche → security) → consistency assertions → tree serialiser → auth/publish loop.
4. `routers/securitized.py` + `main.py` mount + the one `_shared.py` line + `environments.json`
   (`reference_dirs.securitized`, `tool_defaults.securitized`).
5. `frontend/src/tabs/SecuritizedTab.jsx` + `App.jsx` tab registration + Settings fields for the new
   reference dir and defaults.
6. Smoke test in dry-run per `CLAUDE.md` (queue + event + `dry_run: True`), then `npm run build`, then
   `graphify update .`.
7. Live run, smallest tree first, scaling one axis at a time so a NACK is attributable:
   1. 1 deal · 1 series · 1 tranche · 1 security · **Fixed** · USD — the minimum tree
   2. same, **Float** — exercises the §20.4.2 branch (`BENCHMARK`, no `INT_RATE`/`YIELD`) and the
      field name in **A9**
   3. 1 deal · 1 series · 3 tranches · 2 securities — the sample's own shape; should ACK if anything
      does
   4. 1 deal · 1 series · **5 tranches** — first `A-1`/`A-2`/`A-3` split
   5. 1 deal · **2 series** — the only wholly untested structural axis
   6. `Mixed`, then EUR and GBP
   7. **[ADDED 2026-08-13, slice 3]** one deal with `asset_type` = RMBS and `PRODUCT_GROUP` forced to
      `RMBS` — a deliberate probe of [Q14], with a negative control. Outcomes in §20.5.5 and §20.21.
8. Merge into `spec.md` §20, changelog `implementation.md`, update `CLAUDE.md`, delete `abs_spec.md`,
   and **move** `oakhurst_deal.json` / `ABS_DropDowns.xlsx` into `backend/` (§20.18 step 3 — an
   earlier draft said delete, which would have broken the build).

**The staged design earned its keep, but not in the way expected.** Step 7.2 was written to isolate
**A9**; A9 turned out to be correct, and the step instead caught an entirely unrelated,
undocumented, *conditionally* mandatory field (§20.3.8). One axis at a time is what made that legible
— and diffing 7.1's payload against 7.2's is what made it correct, since the step label pointed at the
wrong cause. Step 7.5 (multi-series) ACKed first time. Steps 7.1/7.3/7.4/7.6 ACKed first time.

---

### 20.14 CLAUDE.md additions — **done 2026-08-13, slice 3**

- Tab list gains **Securitized**.
- New bullet under *Architecture & conventions*:
  > **Securitized (ABS)** — the only tool whose payload is a four-level tree (`DETAILS → SERIES{} →
  > TRANCHES{} → SECURITIES{}`) posted as **one request per deal**, so there is no linkage query and
  > no per-tranche loop. Children are **maps keyed by `TEMP-<Level>-<n>`**; the keys are placeholders
  > the app swaps for UUIDs, so what matters is that each key equals the node's own `*_ID` and every
  > child repeats its ancestors' ids. Sizes, credit enhancement, net proceeds and ratings are
  > **derived, not randomised** (spec §20.6) — a change that breaks the roll-up will pass ingest and
  > fail downstream, so run the consistency assertions. Note credit enhancement is per **credit
  > class**, not per tranche: `A-1`…`A-4` are one class and share a CE value.
- New bullet under *Gotchas*:
  > **ABS schema spellings are wrong on purpose:** `UNDERWRITING_DISC_AND_COMMISIONS` (one `S`),
  > `ORIGINATOR_MOODY_RATING` (singular), `ORIGINATOR_SANDP_RATING` (not `SP`), `USER_OF_PROCEEDS`
  > (not `USE_`), and `ACT/36S` in the day-count vocabulary. Do not "fix" them. Day counts are
  > `ACT/360`, **not** `Actual/360`. The string-typed set also differs from Loans:
  > `WAL`/`INTEREST_DUE`/`IPO_PRICE`/`PERCENT_CA`/`WALA`/the three `*_CIK`s are **strings**, while
  > `WA_COUPON`/`NET_PROCEEDS`/`SPREAD_BPS`/`CREDIT_ENHANCEMENT_PCT` are **numbers** — see spec
  > §20.3.6.
- Second new bullet under *Gotchas*:
  > **ABS field lists sourced from the UI will not match the wire.** The form labels the originator
  > input *"Originator Name"* but the payload field is `ORIGINATOR` (spec §20.3.2). When adding
  > fields, take names from a captured payload, not a screenshot — the schema is closed, so a wrong
  > name is a hard NACK.
- Third new bullet under *Gotchas* **[ADDED slice 3]**:
  > **An ABS ACK does not mean the data is right.** The handler validates property names (closed
  > schema), types, and presence — including one conditional rule, `MANDATE_TEXT` when
  > `IS_ROADSHOW` is true. It does **not** validate string *values*: `DAY_COUNT: "Actual/360"` and
  > `PRODUCT_GROUP: "ZZ_NOT_A_PRODUCT_GROUP"` both ACK. So never use an ACK as evidence that a
  > vocabulary value is legal (spec §20.5.5), and never let a green run stand in for the §20.6
  > consistency assertions.
- Fourth bullet, in the **graphify** section (not ABS-specific, found 2026-08-13):
  > **Run `graphify update .` from the repo root only.** From a subdirectory it rebuilds the graph
  > scoped to that subtree and silently discards everything else — running it in `backend/` took the
  > graph from 1350 nodes to 864, dropping the frontend and all of `master/*.md`, with no error and a
  > cheerful "Rebuilt" line. Re-running from the root restores it.

---

### 20.15 Assumptions taken

All eleven are now resolved, specified, or reduced to a cosmetic residue.

| | Assumption | Outcome |
|---|---|---|
| ~~A1~~ | ~~Envelope~~ | **resolved** — observed, §20.2.2; confirmed live |
| ~~A2~~ | ~~Auth uses `credentials.bonds_loans`~~ | **resolved** — "same creds", §20.16 Q7; confirmed live |
| ~~A3~~ | ~~`TEMP-*` ids restart per parent~~ | **resolved** — they are placeholders, §20.3.1 |
| ~~A4~~ | ~~Absent `CUSIP`/`FIGI` beats `""`~~ | **resolved** — neither is mandatory, §20.3.7 |
| ~~A5~~ | ~~`INTEREST_DUE` meaning~~ | **resolved** — total interest to maturity, §20.6.5 |
| A6 | `SERVICER == ORIGINATOR` in ~90% of deals | cosmetic; unchanged |
| ~~A7~~ | ~~Float field names~~ | **resolved** — specified, §20.4.2 |
| A8 | GBP uses `XS` ISINs and no CUSIP | **still open, and an ACK cannot close it** — see §20.5.4. GBP deals post; whether their listing venue and governing law are *right* needs a real GBP sample |
| ~~A9~~ | ~~The float benchmark field is named exactly `BENCHMARK`~~ | **resolved 2026-08-13, slice 3** — eleven live float tranches ACKed with `BENCHMARK`. This was the one that could have NACKed every float tranche; it did not |
| ~~A10~~ | ~~Token in body and header~~ | **resolved** — header only, standard envelope, §20.2.1 |
| ~~A11~~ | ~~`SECURITY_CLASS` mirrors a split `TRANCHE_CLASS`~~ | **resolved** — confirmed, §20.3.5 |

**A9 was the one to watch, and it was fine.** What actually bit was the thing not on this list at all
(§20.3.8) — worth remembering next time a risk register feels complete. A register enumerates the
things you thought of; the live run is what finds the rest, which is why §20.13 step 7 exists.

---

### 20.16 Questions and answers

All twelve original questions were answered on **2026-08-13**; the answers are preserved verbatim
below as the design record, each followed by where it landed in the spec. Q13–Q15 were raised *by*
those answers.

#### Answered

**Q1 — Float coupon fields.** *(was blocking)*
> If the Coupon Type is Float you can ignore Rate and Yield values. Use (1) Benchmark for BENCHMARK
> RATEs (Float Index), (2) SPREAD_BPS — which should be like 20, 30, 40 etc. on top of the benchmark
> rates.

→ **§20.4.2 / §20.4.3.** `INT_RATE` and `YIELD` omitted entirely; `BENCHMARK` + `SPREAD_BPS` in round
steps of 10. The residual risk on the literal field name (**A9**) is now closed: it is `BENCHMARK`.

**Q2 — Envelope.** *(was blocking)*
> `MESSAGE_TYPE: "EVENT_CREATE_NEW_SECURITIZED_ISSUANCE"`, `SERVICE_NAME: "ISSUANCE_EVENT_HANDLER"`,
> `SESSION_AUTH_TOKEN: "QGF7328y0Us3kJyeeTEnvOoenrCUlEbt"`,
> `SOURCE_REF: "188ea743-1f31-41cc-a2b2-89dfda4f3db8"`

…and, on review: *"we can have the message like how we have for bonds and loans — `MESSAGE_TYPE`,
`SERVICE_NAME`, `DETAILS`."*

→ **§20.2.2.** **Three** top-level keys, identical to the other issuance tools. My first reading of
the paste took `SESSION_AUTH_TOKEN` and `SOURCE_REF` for *body* fields and inferred that ABS auth was
special; it is not — both are **headers**, and `SOURCE_REF` is the same constant `12345` the other
engines send. The ACK echoing `"SOURCE_REF": "12345"` (Q6) was the corroborating detail sitting in
plain sight. ABS auth needs no new code at all.

**Q3 — Controlled vocabularies.**
> Refer the spreadsheet dropdowns which has the dropdown lists.

→ **§20.5.5**, from `ABS_DropDowns.xlsx`. Six fields are now exact: `ASSET_TYPE` (56 — the answer's
57 was off by one), `RANKING` (28), `COUPON_TYPE` (17), `ACCRUAL_METHOD` (12), `DAY_COUNT` (8),
`BUSINESS_DAY_CONVENTION` (5). It corrected a real error — the draft proposed `Actual/360`, the
platform says `ACT/360`. The other twelve vocabularies are **[Q14]**.

**Q4 — Multi-series id uniqueness.**
> The `TEMP-…` IDs are placeholders when creating the deal/series/tranche/security structure. You can
> use them in incremental form when generating the payload starting from 0. Once submitted, the app
> creates unique UUIDs and assigns them. This is by design.

→ **§20.3.1 rule 3.** Numbering is free; only parent↔child agreement (rule 4) is load-bearing.
Whether the handler accepts **more than one series in one event** was the open half of this, and
**build step 7.5 settled it: yes.** A deal with two series, six tranches and twelve securities in one
POST ACKed first time (§20.21).

**Q5 — Required vs optional fields.**
> Deal — `ORIGINATOR_NAME`, `ISSUER_NAME`, `ISSUER_TICKER`, `ISSUER_CIK`, `CURRENCY_CODE`. Series —
> `SERIES_NAME`, `CURRENCY_CODE`, `MATURITY_DATE`. Tranche — `TRANCHE_CLASS`, `CCY`, `TENOR`,
> `COUPON_TYPE`, `MATURITY_DATE`. Security — `REG_TYPE`, add `ISIN` if possible.

→ **§20.3.7**, and it becomes a pre-flight assertion rather than a reason to trim the payload. It also
settles that `CUSIP`/`FIGI` are optional. It raised Q13, since answered. **The list was incomplete in
a way no one could have known**: `MANDATE_TEXT` is mandatory too, but only when `IS_ROADSHOW` is true
(§20.3.8), and the sample is not a roadshow deal so nothing in the source material hinted at it.
Unconditional-mandatory was the wrong mental model, not the wrong list.

**Q6 — Response shape.**
> `{"GENERATED": [], "MESSAGE_TYPE": "EVENT_ACK", "SOURCE_REF": "12345"}`

→ **§20.2.3**, and the live ACK is byte-for-byte this. Success is `MESSAGE_TYPE == "EVENT_ACK"`, which
`loans_engine._publish` already tests. `GENERATED` is **empty**, so the real UUIDs never come back —
there is no id mapping to log, and `ISSUER_NAME (ISSUER_TICKER)` is the only handle back into the UI.

**Q7 — Credentials.**
> Same creds.

→ **§20.2.1.** `credentials.bonds_loans`; no new credential set, no Settings change.

**Q8 — `INTEREST_DUE`.**
> Total interest to maturity.

→ **§20.6.5.** `size × rate × WAL` — WAL, not TENOR, since WAL is the average time principal is
outstanding. Worth noting the sample's own values do not satisfy this on any of the three tranches;
generated data will be self-consistent where the sample is not.

**Q9 — Realistic scale.**
> Realistically 4 series in a deal. Most ABS series have 3 to 6 tranches, following a standard capital
> structure: Class A (senior, sometimes split into A-1, A-2, A-3, A-4 by maturity or currency), Class
> B (mezzanine), Class C (junior mezzanine), Class D (subordinate / first-loss), residual/equity piece
> (sometimes not formally labelled as a tranche).

→ **§20.6.4**, and this was the answer that **changed the design**. `TRANCHE_CLASS` is not always a
bare letter, so the generator needed a class ladder rather than `chr(65+i)`, and credit enhancement
had to move from per-tranche to **per credit class** (§20.6.3) — `A-1` and `A-2` sit above identical
subordination and must carry identical CE. Defaults also shifted to a 3–6 tranche range. The residual
piece is deliberately **not** emitted.

**Q10 — EUR and GBP.**
> For EUR deal pick some countries that use EUR as currency — SPAIN, ITALY, FRANCE, etc. CUSIP is not
> expected for EUR deal.

→ **§20.5.4.** `COUNTRY` ∈ `ES/IT/FR/DE/NL/IE`, no CUSIP. GBP conventions remain assumed (**A8**);
both a EUR and a GBP deal ACKed live, which — per §20.5.5 — is not the same as being confirmed.

**Q11 — Asset types.**
> Attached in the DROPDOWN excel spreadsheet.

→ **§20.5.5.** 56 values, replacing the ~25 proposed. `asset_types.csv` gets one row per value.

**Q12 — Downstream (Email Compare).**
> I don't have the sample data yet for this. But is it important to wire this right now?

→ **No — and the answer is cleaner than when I asked.** I flagged it because §18's expectation writer
wants `rendered_fields`-style provenance, and retrofitting provenance is usually worse than building
it in. But §18 scores *emails against the DB*, and there are no ABS broker-email formats and no ABS
sample emails — so there is nothing to render, nothing to label, and nothing to compare. Wiring
provenance now would be building a producer with no consumer, against a field map that can only be
guessed at.

What actually protects the option is smaller and free: the generator already returns the built tree,
and §20.6 makes every derived value reproducible from it. When ABS emails exist, the expectation
writer can be handed the same tree the POST used. **Nothing is being closed off, so this stays out of
v1** (§20.12).

#### Raised by the answers, then answered

**Q13 — `ORIGINATOR` or `ORIGINATOR_NAME`? [RESOLVED 2026-08-13]**
> Keep it `ORIGINATOR`.

→ **§20.3.2.** The explanation is that `ORIGINATOR_NAME` is the **UI label**, not the field — a
screenshot of the form shows inputs captioned *"Originator Name"* and *"Originator Ticker"* over the
values `Oakhurst Auto Finance LLC` / `OAKAF`, while the wire fields are `ORIGINATOR` and
`ORIGINATOR_TICKER`. Generalised into a `CLAUDE.md` gotcha (§20.14): take field names from a captured
payload, never from the screen. Confirmed by the first live ACK, and by §20.3.7's closed schema —
which would have named `ORIGINATOR` as an undefined property had the answer been wrong.

**Q15 — Does `SECURITY_CLASS` take `"A-1"`? [RESOLVED 2026-08-13]**
> Yes, it should accept and match the tranche class. For example Tranche Class A: if it has a single
> security it will have class A, and if it has two securities both will have class A.

→ **§20.3.5.** So `SECURITY_CLASS` is a straight copy of the parent's `TRANCHE_CLASS`, including a
split `"A-1"`, and it is explicitly **not** a discriminator between sibling securities — two
securities under one tranche share a class and differ by `REG_TYPE`/identifiers. That makes it a hard
assertion in §20.6.6 rather than something to be lenient about.

#### Answered by measurement rather than by asking

**Q14 — The other vocabularies not in the workbook. [PARTIALLY RESOLVED 2026-08-13, slice 3]**
`PRODUCT_GROUP`, `REG_TYPE`, `FORMAT`, `SETTLEMENT_TYPE`, `CLEARING_SYSTEM`, `DEPOSITORY`,
`PREPAYMENT_TYPE`, `PRICE_TYPE`, `INVESTOR_STATUS`, `SETTLEMENT_PERIOD`, `COUPON_FREQUENCY`,
`GOVERNING_LAW`, `LISTING_EXCHANGE`.

The question as originally posed — *are these constrained?* — is **answered: no, not at ingest.** The
handler accepts any string in any of them, including deliberate nonsense (§20.5.5). What that closes
is the NACK risk, which was the reason it was on the risk list at all.

What it does **not** close is whether the values are *correct* for consumers downstream of ingest, and
that question now needs a different instrument than a POST — a second workbook tab, the application's
own dropdowns, or a read of stored rows. `PRODUCT_GROUP` has been given the best answer available
(§20.5.5) and is flagged as the weakest-evidenced choice in this section. The remaining twelve keep
the sample's observed values, which are good *provenance* evidence: they came off a real payload the
platform accepted and presumably rendered.

---

### 20.17 Status

**Built, live-verified, documented. Complete.**

Fifteen questions asked, fifteen answered — Q14 by measurement rather than by reply, and partially
(see above). Ten of eleven assumptions resolved; the eleventh (**A8**, GBP conventions) is cosmetic
and cannot be closed by any ACK.

What remains genuinely open, all of it non-blocking:

| | Item | What would settle it |
|---|---|---|
| **A8** | GBP ISIN prefix / listing venue / governing law | a real GBP sample payload. Not an ACK — §20.5.5 |
| **[Q14]** | Are the twelve non-workbook vocabularies *correct* downstream? | a second workbook tab, or a read of stored rows |
| — | `PRODUCT_GROUP` on the five overridden asset types | a downstream consumer, or a captured non-`ABS` payload. Reverting is one line (§20.5.5) |
| — | ABS in Email Compare (§18) | ABS broker-email formats existing at all (§20.12, Q12) |
| — | The named `TRP - QA - Automation` environment | it was unreachable on 2026-08-13; the live run used `TRP - QA2`. See §20.21 |

---

### 20.18 Definition of done

This feature is **not** done when the tab posts a deal. It is done when the four records below are
true. All four are, as of 2026-08-13.

1. ☑ **`master/spec.md` carries §20.** Everything from the draft is merged in as section **§20**,
   slotting after §19, with the §20.x numbering preserved exactly (every internal cross-reference
   assumes it). The Q&A record (§20.16) merged too — it is the design rationale, and future sessions
   need to know *why* `ORIGINATOR` beat `ORIGINATOR_NAME` and why the residual piece is not emitted.
2. ☑ **`CLAUDE.md` is updated** per §20.14 — the tab list, one *Architecture & conventions* bullet,
   three *Gotchas* bullets (the third was added by slice 3's findings).
3. ☑ **`master/abs_spec.md` is deleted.** **[CORRECTED 2026-08-13, after slice 2]** — the other two
   source files are **relocated, not deleted**. The original wording said to delete all three on the
   theory that the workbook's content lives in `vocabularies.csv` and the sample's in §20.3. That is
   wrong: both are still **read at runtime**, so deleting them breaks the build.

   | File | Read by | Action |
   |---|---|---|
   | `master/abs_spec.md` | nothing | **deleted** — it has become §20 of `spec.md` |
   | `master/oakhurst_deal.json` | `backend/tests/test_abs_slice1.py` | **moved** → `backend/tests/fixtures/oakhurst_deal.json` |
   | `master/ABS_DropDowns.xlsx` | `backend/reference/securitized/_generate.py` | **moved** → `backend/reference/securitized/_source/ABS_DropDowns.xlsx` |

   Deleting the workbook would leave `_generate.py` unable to run, silently voiding its byte-stable
   regeneration guarantee; deleting the sample would break the slice-1 suite outright. Both belong in
   `backend/` anyway — `master/` is documentation, and these two are a build input and a test fixture.
   The two path constants were updated, along with the `master/oakhurst_deal.json` references in the
   `abs_utils/{dates,identifiers}.py` docstrings and the `_generate.py` header;
   `grep -rn "abs_spec\|master/oakhurst\|master.*ABS_DropDowns" backend/ frontend/src/` comes back
   empty over source, `_generate.py` reproduces all ten CSVs byte-identically from the new location,
   and all four suites pass.
4. ☑ **`master/implementation.md`** has a dated changelog entry per `CLAUDE.md`: files touched,
   decisions taken, deviations from this spec, and anything still open.

Until step 1 was done, `abs_spec.md` was the authoritative spec and `spec.md` did not mention ABS at
all. **That was a real hazard for a multi-session build** — a session reading only `spec.md` would not
have known the feature existed. Every hand-off prompt named `master/abs_spec.md` explicitly for that
reason, and the final slice was the one that flipped the authority over. It has.

---

### 20.19 Build slices

#### 20.19.1 Why three, not one

One session was not viable. Rough sizing of new material:

| Part | Approx. |
|---|---|
| Reference data builder + 10 CSVs (incl. a ≥ 2000-row file) | ~400 lines + data |
| `abs_utils/{dates,identifiers}.py` | ~180 lines |
| `securitized_engine.py` | ~700 lines (for scale: `bonds_engine.py` is 852) |
| Router + `main.py` + `_shared.py` + `environments.json` | ~50 lines |
| `SecuritizedTab.jsx` + `App.jsx` + Settings | ~320 lines |
| Doc merge into `spec.md` §20 + `CLAUDE.md` + changelog | ~900 lines moved |

That is ~1,650 lines of new code plus a 2,000-row dataset plus a 900-line documentation merge.
Attempting it in one pass risks exhausting context **mid-engine**, which is the worst possible
stopping point — a half-written generator is not verifiable and cannot be handed off. Three slices,
with state in the repo rather than in chat history, per the protocol in §18.17.

The slice boundaries were drawn on one test: **can this be verified without the next slice existing?**
All three passed. In the event, each slice also found spec errors the next one would otherwise have
inherited — six across the three — which is the argument for the boundaries independent of context
budget.

#### 20.19.2 The slices

| Slice | Deliverable | Verification | Status |
|---|---|---|---|
| **1** | `backend/reference/securitized/` — `_generate.py` plus `vocabularies.csv`, `asset_types.csv`, `entities.csv`, `currencies.csv`, `agents.csv`, `underwriters.csv`, `legal_advisors.csv`, `auditors.csv`, `ratings.csv`, `analysts.csv`. Plus `backend/engines/abs_utils/{__init__,dates,identifiers}.py` | Offline, no server: row counts per §20.5.1; the six vocabularies match §20.5.5 counts exactly (**56**/28/17/12/8/5); identifiers reproduce the **golden values** in §20.19.3; date sequencing satisfies §20.7.1 ordering | ☑ **Done (2026-08-13)** — `backend/tests/test_abs_slice1.py`, 224/224 pass |
| **2** | `backend/engines/securitized_engine.py`, `backend/routers/securitized.py`, `main.py` mount, the one `_shared.py` line, `environments.json` (`reference_dirs.securitized`, `tool_defaults.securitized`) | Dry-run harness per `CLAUDE.md` over 6 shapes (§20.19.4), each asserted against the §20.6 arithmetic and §20.3.6 types programmatically. `python -c "import main"` from `backend/`. **No live POST** | ☑ **Done (2026-08-13)** — `backend/tests/test_abs_slice2.py`, 20,311/20,311 pass (111,567/111,567 at `ABS_SLICE2_REPEATS=80`) |
| **3** | `frontend/src/tabs/SecuritizedTab.jsx`, `App.jsx` registration, `SettingsTab.jsx` fields. Then the staged live run (§20.13 step 7). Then §20.18 | `npm run build`; the tab driven end-to-end over HTTP; live ACK at each staged shape; `graphify update .`; §20.18 items 1–4 all true | ☑ **Done (2026-08-13)** — 15 live posts, §20.21; one engine fix (§20.3.8) |

Slice 1 touched nothing the running app imports, so it could not break Bonds/Loans/Interest. Slice 2
added a route but no UI reached it. Slice 3 is the only one that changed what a user sees.

#### 20.19.3 Golden values for slice 1

`oakhurst_deal.json` supplies the identifiers. Slice 1 asserts against them, which is why the sample
file is a **checked-in test fixture** rather than something §20.18 step 3 could delete.

> **[CORRECTED 2026-08-13, slice 1] — the sample's check digits are wrong; its structure is right.**
>
> This section previously asserted the sample's literal check digits. They do not survive contact with
> the algorithm. `loan_utils/cusip.py::check_digit` and `bonds_engine::_isin_check` were validated
> first against ten real, publicly verifiable identifiers and both are correct: CUSIPs `037833100`
> (Apple), `594918104` (Microsoft), `88160R101` (Tesla), `02079K305` (Alphabet C), `46625H100`
> (JPMorgan), and ISINs `US0378331005`, `US5949181045`, `GB0002634946`, `DE0005557508`,
> `XS0629974352` — 10/10. Tesla's is the one that matters most here: `88160R101` carries a letter in
> the same doubled 6th position as `67543M`, so the case the sample would have to be exercising
> differently is itself confirmed.
>
> Against that, **every sample CUSIP is exactly +2 off and every sample 144A ISIN exactly +5 off** — a
> constant offset across all three tranches, which is the signature of a fabricated identifier rather
> than a different algorithm. A platform accepting the payload proves nothing about the check digits:
> nothing in the ingest path validates them. (Slice 3 generalised that last sentence considerably —
> see §20.5.5.)
>
> So the goldens below are the **corrected** values, and the assertion the sample genuinely does fix —
> the *structure* — is asserted separately and unchanged: shared 6-character base, suffix advancing
> `AA`/`AB`/`AC` per tranche, `US` + CUSIP for 144A, `US` + `U` + base5 + suffix + CUSIP-check for
> Reg S, 9/12/12 characters. That structure is what slice 2 consumes.

| Input | Expected | Sample says | Rule |
|---|---|---|---|
| CUSIP base `67543M`, suffix `AA` | `67543MAA2` | ~~`67543MAA4`~~ | §20.8.1 check digit |
| CUSIP base `67543M`, suffix `AB` | `67543MAB0` | ~~`67543MAB2`~~ | " |
| CUSIP base `67543M`, suffix `AC` | `67543MAC8` | ~~`67543MAC0`~~ | " |
| CUSIP `67543MAA2`, 144A | ISIN `US67543MAA27` | ~~`US67543MAA48`~~ | §20.8.2, `"US" + CUSIP + check` |
| CUSIP `67543MAB0`, 144A | ISIN `US67543MAB00` | ~~`US67543MAB21`~~ | " |
| base `67543`, suffix `AA`, Reg S | ISIN `USU67543AA38` | ~~`USU67543AA55`~~ | §20.8.2 CINS form |
| base `67543`, suffix `AB`, Reg S | ISIN `USU67543AB11` | ~~`USU67543AB39`~~ | " |

Also assertable from the sample, and **all confirmed exactly** (§20.7.1): series legal final maturity
− latest tranche maturity = **86,400,000 ms**; tranche tenors 3Y/5Y/6Y land at announcement +
**1106/1836/2202** days. Slice 1 goes further and reproduces the sample's raw epochs —
`ANNOUNCEMENT_DT` 1785888000000, `EXPECTED_PRICING_DATE` 1786492800000, `SETTLEMENT_DATE`
1786924800000, `FIRST_COUPON_DT` 1789603200000, the three `MATURITY_DATE`s, the three
`TRANCHE_SETTLEMENT_DATE`s and the series legal final 1976227200000. Unlike the identifiers, **every
date in the sample is internally consistent**, which is what let two undocumented rules be recovered
from it (§20.7).

#### 20.19.4 The six shapes slice 2 must dry-run

Each isolates one thing, and each has assertions that can fail loudly:

| # | Shape | What it proves |
|---|---|---|
| 1 | 1 deal · 1 series · 1 tranche · 1 security · Fixed · USD | minimum tree; mandatory set present (§20.3.7) |
| 2 | same, **Float** | `BENCHMARK` + `SPREAD_BPS` present, `INT_RATE`/`YIELD` **absent** (§20.4.2) |
| 3 | 1 · 1 · 3 · 2 · Fixed · USD | the sample's shape — sizes roll up, CE = 16/5/1 pattern, `NET_PROCEEDS` exact (§20.6.1–20.6.5) |
| 4 | 1 · 1 · **5** · 2 · Fixed | `A-1`/`A-2`/`A-3`/`B`/`C` ladder; **CE equal across the A-x classes** (§20.6.3) — the bug this spec was revised to prevent |
| 5 | 1 · **2 series** · 3 · 2 · Mixed | per-parent `TEMP-*` numbering; fixed and float ranked independently (§20.4.4) |
| 6 | 1 · 1 · 3 · 2 · Fixed · **EUR** | no `CUSIP`, `XS` ISINs, euro-area `COUNTRY`, `PERCENT_CA` omitted (§20.5.4) |

Shape 4 is the one worth writing the assertion for first. Computing credit enhancement per *tranche*
instead of per *credit class* is the single most likely way to get this feature subtly wrong — it
produces data that ingests cleanly and is financially nonsense.

The same six shapes became the live sequence of §20.13 step 7, which is deliberate: a shape asserted
offline and then posted live is the only pairing that distinguishes "the generator is wrong" from "the
schema is not what we thought".

---

### 20.20 Multi-session build — what the protocol bought

Each slice ran in a **fresh chat** per §18.17, with state in the repo (this section's status tables,
`implementation.md`, and the code) rather than in chat history. The three hand-off prompts lived in
this section during the build; they have served their purpose and are not reproduced — the reusable
template is §18.17.3, and every substantive fact they carried is in the sections above. What is worth
keeping is why the arrangement worked.

#### 20.20.1 Token economy

Three facts, each verified on 2026-08-13:

- **Long markdown specs are indexed in graphify** (this section was 72 nodes as a standalone file, one
  per heading, with line numbers). `graphify explain "20.6 Internal consistency"` returns a section's
  children and their `:L<n>` anchors, so a session can jump to the ~40 lines it needs instead of
  reading all ~1,300. This replaces *scanning* a large spec, not reading the section you land on.
- **Run `graphify update .` at the end of every slice.** It is AST-only, free, and costs **0 LLM
  tokens** — and without it, slice 3 could not query the engine slice 2 wrote.
- **Two caveats**, both also in `CLAUDE.md`: `graphify affected "X"` does **not** work in this repo
  (cross-file call edges are unresolved — it reports "No affected nodes found" even for functions with
  real importers), and **JSX coverage is thin** (4–8 nodes per component), so frontend work needs real
  file reads. Slice 3's prompt said so explicitly and it was correct to.

#### 20.20.2 Naming the spec section a slice needs

Each hand-off prompt named the specific §20.x subsections that slice required. That was the difference
between reading ~150 lines and ~1,300, and it is the single cheapest thing to carry into the next
multi-session build in this repo. The corollary is that the numbering is load-bearing: §20.x
cross-references appear throughout the code comments as well as this document, which is why §20.18
step 1 insists the numbering survive the merge intact.

---

### 20.21 Live verification record — 2026-08-13

Fifteen posts, every response body captured verbatim. This is the evidence behind every
**[CONFIRMED live]** in the sections above.

**Environment.** The build plan named `TRP - QA - Automation`
(`qa-trowe-qaautomationpbitrowe.cddev.genesis.global`). **It was unreachable** — DNS resolved to
`172.21.127.240` but TCP 443 did not connect, on repeated attempts. `TRP - QA1` and `TRP - QA2` were
both reachable from the same machine at the same time, so this was the host being down rather than a
network restriction. The sequence therefore ran against **`TRP - QA2`**
(`qa-trowe-pbi2.cddev.genesis.global`), same platform, same `credentials.bonds_loans` user. Every
schema question below is a property of the handler, not of the environment, so the substitution does
not weaken the findings — but the named environment has still never seen an ABS deal, and that is
recorded in §20.17.

| Step | Shape | Result |
|---|---|---|
| 7.1 | 1·1·1·1 · Fixed · USD | **ACK** — settles Q13 (`ORIGINATOR`), the envelope, auth, and the §20.2.3 response |
| 7.2 | 1·1·1·1 · **Float** · USD | **NACK** — `"Mandate Text is required"`. Not the Float branch: the deal drew `IS_ROADSHOW: true`. → §20.3.8 |
| 7.2b | same, re-run | **ACK** — `IS_ROADSHOW: false`. **A9 confirmed:** `BENCHMARK`, no `INT_RATE`/`YIELD` |
| — | 4 probes, `MANDATE_TEXT` / `MANDATE` / `MANDATE_TXT` / `ROADSHOW_MANDATE_TEXT` | `MANDATE_TEXT` **ACK**; the other three **NACK** with `property '<X>' is not defined in the schema` → the schema is closed (§20.3.7) |
| — | 2 probes, `IS_ROADSHOW: false` ± `MANDATE_TEXT` | both **ACK** → the requirement is conditional, the field is not (§20.3.8) |
| 7.2c | **8 deals** · Float · USD, after the engine fix | **8× ACK**. Two drew `IS_ROADSHOW: true` and carried engine-generated `MANDATE_TEXT`; the fix is verified on the wire, not just in assertions |
| 7.3 | 1·1·3·2 · Fixed · USD · Auto Loan ABS | **ACK** — the sample's own shape |
| 7.4 | 1·1·**5**·2 · Fixed · USD | **ACK** — `A-1`/`A-2`/`A-3`/`B`/`C`; the three `A-x` posted **CE 17.1% each** (§20.6.3) |
| 7.5 | 1·**2 series**·3·2 · Fixed · USD | **ACK** — 2 series, 6 tranches, 12 securities in one event. Closes the open half of Q4 |
| 7.6a | 1·1·3·2 · **Mixed** · USD | **ACK** — float senior + fixed subordinate in one series |
| 7.6b | 1·1·3·2 · Fixed · **EUR** | **ACK** |
| 7.6c | 1·1·3·2 · Fixed · **GBP** | **ACK** — but see §20.5.4 on why this does not close A8 |
| 7.7 | 1·1·3·2 · Fixed · USD · RMBS, `PRODUCT_GROUP: "RMBS"` | **ACK** |
| — | negative control: `PRODUCT_GROUP: "ZZ_NOT_A_PRODUCT_GROUP"` | **ACK** → values are not validated (§20.5.5) |
| — | negative control: `DAY_COUNT: "Actual/360"` | **ACK** → same |
| — | one live deal driven through `POST /api/securitized/run` + the SSE stream | **ACK** — the router, engine and stream wiring, exercised over HTTP rather than in-process |

**The two negative controls are the most valuable rows in this table**, and they were not in the build
plan. Without them, step 7.7's ACK reads as "the platform accepts `RMBS` as a product group"; with
them it reads as "the platform accepts any string", which is a different and considerably more useful
fact — it retires ACK-based vocabulary testing entirely (§20.5.5) and reframes §20.6 from a
nice-to-have into the only line of defence. **A test whose positive result has no negative control is
not a test.** Both controls cost one POST each.

---

## 21. Munis Issuance

### 21.1 Purpose

The fourth issuance tool. Posts `EVENT_CREATE_NEW_MUNIS_ISSUANCE` — one US municipal bond deal per
POST, carrying the whole `DETAILS → SERIES → TRANCHES → SECURITIES` tree.

It is the first tool whose reference data was derived by **analysing production-shaped data** rather
than transcribing a drop-down list. The platform's own lookup export
(`master/LIST_LOOKUP_TROWE-209_MUNIS_INSERT.csv`) defines only four vocabularies; everything else —
and every dependency between fields — comes from `master/MUNIS_DATA.csv` (95 deals / 237 series /
2,142 maturities). §21.6 records what that analysis found, with the counts, because those
dependencies are the whole value of the tool: a generator that draws each field independently
produces data that ingests cleanly and is wrong in ways only a downstream report reveals.

### 21.2 Authentication and envelope

Auth is identical to Bonds/Loans/Securitized: `TXN_LOGIN_AUTH` against `/sm/event-login-auth`,
publish to `https://{host}/gwf//EVENT_CREATE_NEW_MUNIS_ISSUANCE`.

The **envelope differs**. Munis carries the token and the source ref in the *body* as well as the
headers, and `SOURCE_REF` is a **per-deal UUID**, not the constant `12345` the other tools send:

```json
{ "DETAILS": {}, "MESSAGE_TYPE": "EVENT_CREATE_NEW_MUNIS_ISSUANCE",
  "SESSION_AUTH_TOKEN": "<token>", "SOURCE_REF": "<uuid4>" }
```

There is no `SERVICE_NAME` key. This is taken from a captured payload
(`backend/tests/fixtures/munis_sample_deal.json`), not inferred.

### 21.3 Payload

#### 21.3.1 Shape

`DETAILS.SERIES[]` → `TRANCHES[]` → `SECURITIES[]` — every child collection is a plain **array**.
Unlike ABS (§20.3.1) there are **no `TEMP-*` map keys and no id fields at all** on any node; the
platform assigns ids. A `TRANCHE` in Munis is a **maturity** on a serial ladder, not a credit class.

#### 21.3.2 Fields

Taken from the captured payload and asserted against it by `tests/test_munis.py`. Every level
matches the capture exactly, with the single documented exception in §21.3.5.

- **DETAILS** — `ANNOUNCEMENT_DT`, `BND_BANK`, `CHANGE_FLAGS`, `DEAL_DESCRIPTION`, `DEAL_SIZE`,
  `DEAL_STATUS`, `DEAL_TYPE`, `EXPECTED_PRICING_DATE`, `ISSUER_COUNTRY`, `IS_ROADSHOW`,
  `ORDER_PERIOD_BEGIN_DATE`, `ORDER_PERIOD_END_DATE`, `RISK_COUNTRY`, `SERIES`, `STATE`,
  `SYNDICATE`, `USE_OF_PROCEEDS`, `WIRE_TYPE`
- **SERIES** — `AWARD_DATE`, `CALL_FEATURE_DESCRIPTION`, `CHANGE_FLAGS`, `DATED_DATE`,
  `DELIVERY_DATE`, `ENHANCEMENT`, `FIRST_COUPON_DATE`, `INTEREST_TYPE`, `INVESTOR_STATUS`,
  `PURPOSE`, `SECTOR`, `SERIES_CODE`, `SERIES_DESCRIPTION`, `SERIES_FITCH_RATING`,
  `SERIES_MONEY_TYPE`, `SERIES_MOODYS_RATING`, `SERIES_SANDP_RATING`, `SERIES_SIZE`,
  `SERIES_STATUS`, `SOURCE_OF_REPAYMENT`, `TAX_STATUS`, `TRADE_DATE`, `TRADE_DESK`, `TRANCHES`
- **TRANCHE** — `CHANGE_FLAGS`, `COUPON`, `COUPON_TYPE`, `MATURITY_AMOUNT`, `MATURITY_DATE`,
  `MATURITY_DESCRIPTION`, `MATURITY_STATUS`, `MINIMUM_INCREMENT`, `MINIMUM_PIECE`, `MULTIPLES`,
  `PRICE`, `SECURITIES`, `SPREAD`, `TRANCHE_CURRENCY`, `YIELD`
- **SECURITY** — `SECURITY_STATUS`, `CUSIP`, `CHANGE_FLAGS`

`SYNDICATE` is a single **semicolon-joined string**, not an array. `ENHANCEMENT` is the literal
string `None` (as text) when absent, not null and not omitted.

#### 21.3.3 Types

Everything numeric-looking here is sent as a **number**, unlike Loans (§6) and ABS (§20.3.6) which
have string-typed numeric fields. `COUPON`, `PRICE`, `YIELD` are floats; `SPREAD`, `MULTIPLES`,
`MATURITY_AMOUNT`, `SERIES_SIZE`, `DEAL_SIZE` are integers. `IS_ROADSHOW` is a real boolean.
No string-typed-numeric trap is known for this tool — if a NACK says otherwise, record it here.

#### 21.3.4 Dates

All dates are **UTC-midnight epoch milliseconds**. Every date in the capture is exactly UTC
midnight; building the timestamp from a naive local datetime shifts it across the dateline and
stores the wrong business date. `munis_engine._epoch_ms` is the only place this is done.

#### 21.3.5 Omitted field

`SOURCE_OF_REPAYMENT` is emitted **only on General Purposes series** (§21.6.3). The capture carries
it on an Education series, but that capture was hand-filled in the UI — its `SERIES_DESCRIPTION`
says Multi-Family Housing while its `SECTOR` says Education — so it is not evidence of a rule.
2,129 of 2,142 real rows leave it null and all 13 that populate it are General Purposes. **This is
the one field where the engine deviates from the capture**; if the schema requires the key
unconditionally, the NACK will name it and the fix is to emit it always.

#### 21.3.7 Mandatory fields (pre-flight)

Errors (skip the POST): deal `DEAL_DESCRIPTION`, `DEAL_SIZE`, `DEAL_STATUS`, `DEAL_TYPE`, `STATE`;
series `SERIES_CODE`, `SERIES_SIZE`, `SERIES_STATUS`, `TAX_STATUS`; maturity `MATURITY_DATE`,
`MATURITY_AMOUNT`, `MATURITY_STATUS`, `COUPON`, `PRICE`; and non-empty `SERIES`, `TRANCHES`,
`SECURITIES` at each level.

### 21.4 Parameters

| Param | Default | Meaning |
|---|---|---|
| `deals` | 1 | one deal = one POST |
| `series_per_deal` | `[2]` | cycling list (§21.9); observed max 7 |
| `maturities_per_series` | `[8]` | cycling list; the serial ladder; observed max 31 |
| `securities_per_maturity` | `[1]` | CUSIPs per maturity |
| `state` | empty | filters the **issuer pool** |
| `sector` | empty | filters the issuer pool |
| `tax_status` | empty | overrides the issuer's own |
| `deal_status` | empty | constrains `WIRE_TYPE` (§21.6.4) |
| `roadshow` | empty | empty/`random` = ~20% draw; `yes`/`no` force it (§21.6.13) |
| `delay` | 1.0 | seconds between deal POSTs |
| `dry_run` | false | generate and log the payload, no auth, no POST |

`state`/`sector` select an **issuer**, and the issuer then supplies purpose, tax status, ratings,
repayment source and enhancement. A combination no issuer matches is reported as an error listing
the known states rather than silently falling back to an unfiltered draw.

### 21.5 Reference data

`backend/reference/munis/` — 7 CSVs, all optional with hardcoded fallbacks, built by the checked-in
one-off `_generate.py` (deterministic, **never called at runtime**).

| File | Rows | Source |
|---|---|---|
| `vocabularies.csv` | 93 | 4 lists verbatim from the platform lookup (`SOURCE=lookup`); the rest observed frequency (`SOURCE=observed`) |
| `issuers.csv` | 89 | one row per real deal — the join that makes §21.6.1 work |
| `purposes.csv` | 31 | purpose code to home sector |
| `ratings.csv` | 10 | aligned Moody's/S&P/Fitch ladder, weighted by observed frequency |
| `syndicate.csv` | 67 | firms split out of real `SYNDICATE` strings |
| `bnd_banks.csv` | 19 | lead managers (first name in the syndicate) |
| `call_features.csv` | 97 | real `CALL_FEATURE_DESCRIPTION` prose |

#### 21.5.4 The two sources disagree, deliberately

The lookup spells it `Tax-Exempt`; 1,408 of 1,421 observed rows spell it `Tax Exempt`. The lookup's
`General Purposes` is `General Purpose` in the data. The **lookup spelling wins on the wire** — it
is what the platform validates against — and `_generate.py::CANON` folds the observed spelling into
it so the frequency weights survive the rename. Do not "fix" either file to match the other.

#### 21.5.5 Vocabulary values are not validated by an ACK

As with ABS (§20.5.5), assume the handler checks names and types, not string values. An ACK is not
evidence that a vocabulary value is legal.

### 21.6 Internal consistency — what the data analysis found

This section is the specification of the tool's value. Each rule cites the count that establishes
it. `munis_engine.check_payload` asserts the mechanical ones before every POST and
`tests/test_munis.py` re-derives all of them independently.

#### 21.6.1 Issuer-dependence (the organising rule)

State, sector, purpose, tax status, ratings, repayment source, enhancement and typical deal size are
**one issuer's profile**, not eight independent draws. The engine draws an `issuers.csv` row once
per deal and reads every one of those fields off it. Drawing them independently is what produces a
Pennsylvania state-aid programme guaranteeing a California deal.

#### 21.6.2 PURPOSE to SECTOR

Each purpose code lives under one sector (`EDU`/`SCH` to Education, `HSG`/`MFH`/`SFH` to Housing,
`AIR`/`BRI`/`HWY`/`POR` to Transportation, and so on). `Various` is **excluded** as a home sector
when the map is built: it is an aggregate, and taking it as a mode gave `PIT` a home sector of
`Various` which then contradicted every `PIT` issuer's own General Purposes sector.

A **series** never carries `SECTOR: Various` — like the rating rule below, `Various` is a
deal-level roll-up across differing children. An issuer whose profile says `Various` has its series
sector resolved from the purpose code.

#### 21.6.3 SOURCE_OF_REPAYMENT

Populated on 13 of 2,142 rows, and **all 13 are General Purposes** (General Fund / Personal Income
Tax Revenue / Sales Tax Revenue). It is a GO-style deal's field. See §21.3.5.

#### 21.6.4 DEAL_STATUS and WIRE_TYPE

Not independent. `Priced` pairs with `FINAL PRICING`/`ALLOTS`; `Not Published`/`Expected` with
`PREL PRICING`; and so on — `munis_engine.STATUS_WIRE_TYPES` holds the observed pairings.
`Priced` and `Expected` appear in the data but **not** in the platform's own drop-down, so they are
read as derived states and are not offered as a parameter.

#### 21.6.5 MATURITY_DESCRIPTION has three real forms, chosen by tax status

1. **Treasury spread** — `{bps}.00 bps over yld  of {coupon} cpn {MM/YYYY} tsy`. All 61 observed
   instances sit on a `Taxable` or `Various` series and **none** on a tax-exempt one, because only a
   taxable muni is quoted against a treasury. The **double space after `yld`** is in the source on
   all 61 rows and is reproduced deliberately.
2. **Series form** — `{SERIES_DESCRIPTION} Maturity {year(MATURITY_DATE)}`, verified exact on
   every observed instance.
3. **Filler** — empty, `.`, `NMO.`, `**MWC**.` (2,066 of 2,142 rows are one of these).

The capture's `On Par Price with 2 year maturity` matches none of these; it is hand-typed UI text,
so the engine does not reproduce it.

#### 21.6.6 ENHANCEMENT is state-dependent

`Pennsylvania State Aid Intercept Program` appears only on PA deals (41/41); `Assured Guaranty Inc`
only on CA (53/53). `munis_engine.ENHANCEMENT_STATES` gates each value by state and drops an
enhancement that is not legal in the deal's state.

#### 21.6.13 IS_ROADSHOW and MANDATE_TEXT

`MANDATE_TEXT` is **conditionally mandatory**: a deal with `IS_ROADSHOW: true` and no mandate text
NACKs `Mandate text is required for Roadshow.` — a `StandardError` business rule, so it arrives
with **no PATH** (contrast the `FieldError` schema layer, §20.14). Confirmed live 2026-09-15. The
same rule exists for ABS (§20.3.8).

`MANDATE_TEXT` is omitted, not sent empty, when `IS_ROADSHOW` is false. The engine asserts the
**equivalence** (present iff roadshow), since sending it on a non-roadshow deal is the other way to
get this wrong, and `check_payload` fails the deal pre-flight rather than burning a POST.

The `roadshow` parameter exists because a 20% draw makes this deterministic rule look intermittent
— it surfaced on one live deal and would have vanished on a re-run. `yes`/`no` pin the branch so it
can be tested on demand; that is also how `tests/test_munis.py` covers both sides.

#### 21.6.8 Date ordering

Anchored on `TRADE_DATE` (T), from the capture:

```
ANNOUNCEMENT T-12..-24 | EXPECTED_PRICING T-2 | ORDER_BEGIN T-1 | TRADE T
ORDER_END T+1 | AWARD T+1 | DATED T+5..14 | DELIVERY DATED+0..7 | FIRST_COUPON DATED+4..7mo
```

`DELIVERY_DATE` is **derived from `DATED_DATE`**, not drawn beside it — settlement follows dating,
and 232 of 234 real series have DATED before or on DELIVERY. Drawing them independently produced the
out-of-order calendar that the pre-flight check caught during development.

`MUNIS_DATA.csv` agrees on `EXPECTED_PRICING` (T-2 exactly, 139/139) but stores `AWARD_DATE`
*before* the trade date in all 81 populated rows, contradicting the capture. Treated as a DB-side
artefact; the capture wins, because it is what the platform received.

First coupon falls on the 1st or 15th, at least four months after dating.

#### 21.6.9 The two roll-ups

- **`MATURITY_AMOUNT` is denominated in thousands.** `SERIES_SIZE = 1000 x sum(MATURITY_AMOUNT)` —
  234 of 235 real series agree exactly. Emitting both in the same unit ingests cleanly and produces
  a deal 1,000x the size it claims.
- `DEAL_SIZE = sum(SERIES_SIZE)` — 93 of 94 exact.

Both hold **by construction**: the deal size is scaled from the issuer's `TYPICAL_SIZE`, then
allocated *down* the tree by `_split_quantised`, which splits exactly into $5k multiples. Nothing is
re-normalised afterwards.

#### 21.6.10 Rating roll-up

Deal rating = the series rating when all series agree, `Various` when they differ — 100% of the 18
disagreeing deals carry `Various` and no agreeing deal does (`deal_ratings()`).

Whether series may diverge is a **deal-level** decision (15%). Notching each series independently
makes a 3-series deal `Various` about 44% of the time; only 10 of 95 real deals are. The test
asserts the observed rate stays under 35%.

#### 21.6.11 The maturity ladder

Munis are issued as a **serial ladder**, not a credit stack: one maturity a year, strictly
ascending, with yield rising and flattening along the curve. Every maturity must outlive delivery.
Maturity dates fall on the 1st, 15th or 30th (879/528/383 of the observed day-of-month counts).

#### 21.6.12 Price follows from coupon and yield

Price is **never drawn**. A 5% coupon is the muni convention (1,221 of 2,142); when the market
yields less, the bond prices at a premium computed as the semi-annual present value, and
`YIELD < COUPON`. At the long end the coupon is set to the yield and it prices at par with
`YIELD == COUPON`. Observed: 1,323 premium rows all have yield below coupon, and all 433 par rows
have yield equal to coupon.

### 21.8 Identifiers

One CUSIP base per **deal**; the 2-character suffix advances once per maturity across the whole
deal, which is how a real issuer's maturities are numbered. Reuses
`engines/abs_utils/identifiers.py` (§20.8) unchanged.

### 21.9 Cycling lists

`series_per_deal`, `maturities_per_series` and `securities_per_maturity` accept `3`, `1, 2, 3` or
`[1,2]` and cycle, exactly as ABS (§20.9). Values beyond the observed range are **logged, not
clamped**.

### 21.19 Validation

`backend/tests/test_munis.py` — no pytest, no server, no network. Seven shapes x 6 repeats
(78 payloads a run, 624 verified across 8 runs during development), each asserted against every
§21.6 rule recomputed independently, plus `check_payload` required to be silent so a bug in the
checker cannot hide a bug in the generator.

The payload's field set is diffed against `tests/fixtures/munis_sample_deal.json` and matches at
every level (envelope, DETAILS, SERIES, TRANCHE, SECURITY) with the single §21.3.5 exception.

**Not yet verified live.** No Munis deal has been posted to a real environment from this tool, so
`EVENT_CREATE_NEW_MUNIS_ISSUANCE`'s wire acceptance, the §21.3.5 omission and every vocabulary value
remain unconfirmed. First live run should use `deals=1, series_per_deal=1, maturities_per_series=2`
in dry-run, then live.
