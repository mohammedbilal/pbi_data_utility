# PBI Test Utility — Specification

## 1. Overview

A unified test data utility for the Genesis Global TrOWE Primary Bond Issuance (PBI) platform. It combines three previously separate scripts into a single application with a React UI and a FastAPI backend. The utility supports:

- **Bond data insertion** — generates and posts `EVENT_NEW_ISSUANCE_DATA` events
- **Loan data insertion** — generates and posts `EVENT_CREATE_NEW_LOAN_ISSUANCE` events
- **Interest capture** — generates and posts `EVENT_INTEREST_CAPTURE` events (common to both bonds and loans)

---

## 2. Source Scripts

| Tool | Source | Event type |
|---|---|---|
| Bonds | `C:\python\LATEST_PBI_JSON\deal_poster_url_auth_v3.py` | `EVENT_NEW_ISSUANCE_DATA` |
| Loans | `C:\python\PBI_LOAN_JSON_PUBLISHER_V1\` (multi-module) | `EVENT_CREATE_NEW_LOAN_ISSUANCE` |
| Interest | `C:\python\Capture_Interest\capture_interest.py` | `EVENT_INTEREST_CAPTURE` |

---

## 3. Authentication

All three tools authenticate via the same Genesis Global auth service using `TXN_LOGIN_AUTH`.

### 3.1 Credential sets

Two separate credential sets are required:

| Credential set | Used by | Fields |
|---|---|---|
| `bonds_loans` | Bonds tab, Loans tab | `username`, `password` |
| `interest` | Interest Capture tab | `username`, `password` |

Both sets are stored per environment in `environments.json` and are editable in the Settings tab.

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
| `host_name` | string | Hostname only (no protocol). e.g. `qa-trowe-pbi2.cddev.genesis.global` |
| `verify_ssl` | bool | Whether to verify SSL certificates |
| `credentials.bonds_loans.username` | string | Username for bonds and loans auth |
| `credentials.bonds_loans.password` | string | Password for bonds and loans auth |
| `credentials.interest.username` | string | Username for interest capture auth |
| `credentials.interest.password` | string | Password for interest capture auth |

### 4.2 Reference directories

Stored at the top level of `environments.json` (not per-environment — they are filesystem paths):

| Key | Default | Description |
|---|---|---|
| `reference_dirs.bonds` | `C:\python\LATEST_PBI_JSON\reference` | Bonds CSV reference data |
| `reference_dirs.loans` | `C:\python\PBI_LOAN_JSON_PUBLISHER_V1\reference_data` | Loans CSV reference data |

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
| `dry_run` | bool | false | Generate payloads but do not authenticate or POST |

When `currency` is supplied, both `CURRENCY_CODE` and `TRANCHE_CURRENCY` on every tranche are set to that value instead of being drawn from `ref.currencies`.

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

## 8. Run Lifecycle

1. User clicks **Run** → frontend POSTs params to `/api/{tool}/run`
2. Backend creates a `run_id`, starts engine in a background thread, returns `run_id`
3. Frontend opens an SSE connection to `/api/{tool}/stream/{run_id}`
4. Engine emits log messages via a `queue.Queue`; SSE endpoint drains the queue to the client
5. When the engine finishes, it puts `None` (sentinel) in the queue; backend sends `{"type":"done"}`; frontend closes EventSource and re-enables Run button
6. User may click **Stop** at any time → POSTs to `/api/{tool}/stop/{run_id}` → sets a `threading.Event` that the engine checks before each HTTP request and between delay sleeps

Only one run per tool is permitted at a time. Attempting to start a second run while one is active returns HTTP 409.

### 8.1 Dry-run mode (all three tools)

Every tab (Bonds, Loans, Interest) has a **Dry run** toggle. When enabled the engine:

1. **Skips authentication** — no `TXN_LOGIN_AUTH` call, so a dry run works without valid credentials or network access.
2. **Skips every POST** — nothing is sent to the server.
3. **Logs the full generated payload** for each item as pretty-printed JSON (`json.dumps(payload, indent=2)`), so the exact `DETAILS` that *would* be sent is visible in the Live Output panel.
4. Counts each generated item as a "success" in the summary and skips inter-request delays so the preview is near-instant.

Caveat — Loans multi-tranche: in dry run, tranches 2..N do **not** carry `MASTER_LOAN_ID`, because that value is normally fetched from the live `ALL_LOAN_DEAL` query, which a dry run skips. The tranche-1 payload is fully accurate.

---

## 9. Log Message Format

Each SSE message carries a JSON object:

| `type` | Fields | Description |
|---|---|---|
| `log` | `level`, `msg` | A single log line. `level` ∈ `info`, `success`, `warn`, `error` |
| `summary` | `success`, `failed`, `total` | Final counts after run completes |
| `done` | — | Signals the run is finished; frontend closes SSE |
| `ping` | — | Keep-alive; frontend ignores |

On `done` (or a user **Stop**), the frontend computes the run duration and final status and records a completed-run summary to history (see §12).

### 9.1 NACK error logging (Loans)

When the loans server returns an `EVENT_NACK`, the engine logs **every** entry in the response `ERROR` array, not just the first. The first line shows the error count and the first message; subsequent failing fields are logged on `also —` lines. This makes schema/type mismatches (e.g. several columns at once needing string values) visible in a single run instead of one-per-rerun. Example:

```
[D1/T1/1] NACK (2 error(s)) — $.DETAILS.FINAL_OID: must be valid to one and only one schema, but 0 are valid
[D1/T1/1]      also — $.DETAILS.YIELD: number found, string expected
```

---

## 10. Settings UI

The Settings tab allows:
- Viewing, editing, adding, and deleting environment profiles
- Editing `host_name`, `verify_ssl`, both credential sets per environment
- Editing `reference_dirs.bonds` and `reference_dirs.loans` paths
- Saving all changes back to `environments.json` via `PUT /api/config`

Passwords are stored in plain text in `environments.json` (test environments only — not for production use).

---

## 11. Launch & Setup

The app is launched from the project root (`C:\python\PBI_Test_Utility\`) — no terminal required.

| Script | Purpose |
|---|---|
| `Setup.bat` | One-time setup. Creates `backend\.venv`, installs `requirements.txt`, then runs `npm install` in `frontend`. Must be run once before first launch (and after pulling new dependencies). |
| `Launch.vbs` | Double-click to start the app. Runs `Start-App.ps1` completely hidden (no console windows). |
| `Start-App.ps1` | Starts the backend (`uvicorn main:app` on `127.0.0.1:8000`) and frontend (Vite on `5173`) hidden, waits for both ports concurrently, then opens the browser at `http://localhost:5173`. Typically ready in ~1.5–2s. |
| `Stop-App.vbs` | Double-click to stop. Kills `uvicorn.exe` and the Vite `node.exe` process bound to port 5173. |

### 11.1 Launch robustness (Start-App.ps1)

`Start-App.ps1` includes guards learned from real launch failures:

- **PATH refresh** — rebuilds `$env:Path` from the Machine + User registry values at startup. Without this, if Node.js was installed *after* the current Windows session began, the inherited PATH is stale and `npm` is not found, so the frontend silently fails to start.
- **npm resolution** — resolves `npm.cmd` via `Get-Command`, falling back to known install locations (`%ProgramFiles%\nodejs`, `%APPDATA%\npm`), and invokes it by full path. A clear message box is shown if Node/npm is genuinely absent.
- **IPv6-aware readiness check** — the port probe tries both `127.0.0.1` and `::1`. Vite binds to `localhost` which resolves to IPv6 `[::1]`, while uvicorn binds IPv4 `127.0.0.1`; checking both prevents a false 45-second timeout waiting for the frontend.
- **`System.Windows.Forms` loaded lazily** — the assembly is loaded only inside the error-dialog helper (`Show-Error`), so a successful launch doesn't pay the WinForms load cost.

### 11.2 Launch speed optimizations

The launcher is tuned to reach a usable browser in ~1.5–2s (both servers individually bind in ~1s):

- **Direct Vite, no npm wrapper** — the frontend is started as `node node_modules\vite\bin\vite.js` instead of `cmd /c npm run dev`. This skips the `cmd → npm.cmd → node npm-cli → node vite` chain (~1s of process startup). Falls back to `npm` if `node`/`vite.js` can't be located.
- **Concurrent port wait** — `Wait-ForPorts` polls 8000 and 5173 together (previously it waited for the backend fully, then the frontend) so total wait ≈ the slower server, not the sum.
- **Fine poll granularity** — readiness is polled every 150ms (was every 1s), so a server that binds in ~0.3s is detected almost immediately instead of at the next whole second.
- **Lazy WinForms load** — see §11.1.

---

## 12. Run History

The **History** tab (between Interest Capture and Settings) lists recent runs across all three tools and lets the user re-run any of them.

### 12.1 Storage

Run records are persisted **server-side** in `backend/history.json` (managed by `history_manager.py`), so history is shared across the QA team and survives browser refreshes and cache clears. Retention: newest **500** records, dropping anything older than **30 days**.

### 12.2 Endpoints

```
GET  /api/history    → list of records, newest first
POST /api/history    → append one completed-run record (returns it with assigned id)
```

### 12.3 Record shape

| Field | Type | Description |
|---|---|---|
| `id` | string | 8-char hex, assigned server-side |
| `ts` | string | ISO-8601 timestamp, assigned if absent |
| `tool` | string | `bonds` \| `loans` \| `interest` |
| `env` | string | Active environment **name** (e.g. `QA`) at run time |
| `params_summary` | string | Human-readable summary (e.g. `2 single · 1 multi · 3, 8`) |
| `params_raw` | object | Full params object used for the run — drives re-run prefill |
| `ok` / `fail` / `total` | int | Final counts (coerced to int) |
| `status` | string | `done` \| `failed` \| `stopped` (validated; defaults to `done`) |
| `dur_seconds` | number | Wall-clock run duration in seconds |

### 12.4 Recording

History is written from the **frontend** when a run ends, because the engine/SSE layer does not know the environment *name* or a params summary. `useRunState(tool, onFinish)` tracks the run's start time and last `summary`, then fires `onFinish` exactly once on `done` / `stop` (guarded against the `done`-after-`stop` double-fire). Status is derived: `stopped` if the user clicked Stop, else `failed` if any item failed, else `done`. Each tab composes the record and POSTs it via `addHistory`. A failed POST is swallowed — history is best-effort and never blocks a run.

### 12.5 Re-run (↻)

The re-run action is **prefill-only**: it switches to that tool's tab and populates the parameter form from `params_raw`, but does **not** auto-start the run. This is deliberate — re-running posts real test data to the active environment, so the user reviews the prefilled params and clicks Run themselves. (The original prototype auto-fired; this was changed to prefill for safety.)

---

## 13. UI Design System

The interface uses the **Operator** design system: a dark, mono-forward "terminal-as-product" aesthetic with a sky-blue accent, suited to a tool whose job is firing requests and streaming live output. Tokens live in `frontend/src/index.css` `:root`.

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
