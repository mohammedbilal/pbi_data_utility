# PBI Test Utility — Implementation Record

## What Was Built

### Backend (FastAPI, Python)

| File | Status | Notes |
|---|---|---|
| `main.py` | Done | FastAPI app, CORS for localhost:5173 |
| `config_manager.py` | Done | Load/save `environments.json`; path resolved relative to `__file__` |
| `run_manager.py` | Done | Per-run dict: queue + stop_event + status + thread |
| `engines/bonds_engine.py` | Done | Full adaptation of `deal_poster_url_auth_v3.py` |
| `engines/loans_engine.py` | Done | Inlined `DataGenerator`, auth, publish, deal_query |
| `engines/interest_engine.py` | Done | Adaptation of `capture_interest.py` |
| `engines/csv_engine.py` | Done | CSV/Excel → JSON publisher; port of `csv_to_json_publisher_V4.py` (added 2026-06-26) |
| `engines/tig_engine.py` | Done | `EVENT_TIG_CREATE_ORDER` publisher; session-token auth, N orders, aligned qty, auto account codes (added 2026-06-26) |
| `engines/email_builder.py` | Done | Broker-email HTML renderers (7 formats) + boilerplate/random-pool gap-fill (added 2026-07-21) |
| `engines/outlook_sender.py` | Done | Outlook COM (`pywin32`) send + optional `.msg` save; per-thread CoInitialize (added 2026-07-21) |
| `expected_store.py` | Done | `util_*` mirror tables (SQLite); DDL from the schema CSVs, length validation, map overrides (added 2026-08-06, spec §18) |
| `engines/expected_writer.py` | Done | Pure payload→expected-row projection, map-driven, vocab-normalized (added 2026-08-06, spec §18.5) |
| `reference/db_schema/` | Done | Four `*_fields_size.csv` schema exports + `field_map.csv` (391 rows) + `vocab_map.csv` (added 2026-08-06) |
| `engines/loan_utils/cusip.py` | Done | Copied from loan project |
| `engines/loan_utils/dates.py` | Done | Copied from loan project |
| `routers/_shared.py` | Done | Reusable start/stream/stop logic |
| `routers/bonds.py` | Done | |
| `routers/loans.py` | Done | |
| `routers/interest.py` | Done | |
| `routers/csv_upload.py` | Done | Multipart `/run`, SSE `/stream`, `/stop` (added 2026-06-26) |
| `routers/tig_orders.py` | Done | Thin router (mirrors `interest.py`); registered at `/api/tig_orders` (added 2026-06-26) |
| `routers/config_router.py` | Done | GET + PUT /api/config |
| `routers/history_router.py` | Done | GET + POST /api/history (added 2026-06-22) |
| `history_manager.py` | Done | JSON-persisted run history (`history.json`); add/list with retention (added 2026-06-22) |

### Frontend (Vite + React)

| File | Status | Notes |
|---|---|---|
| `package.json` | Done | React 18, Vite 5 |
| `vite.config.js` | Done | Proxy `/api` → `localhost:8000` |
| `src/index.css` | Done | Operator design system — dark, mono-forward theme (rewritten 2026-06-22); `.drop-zone` styles added 2026-06-26 |
| `src/App.jsx` | Done | Tab router; CSV Upload tab added (2026-06-26); History tab, env-switch persistence, re-run prefill, toast (2026-06-22) |
| `src/api.js` | Done | `startCsvRun` (multipart) added (2026-06-26); `getHistory`/`addHistory` added (2026-06-22) |
| `src/hooks/useRunState.js` | Done | Optional `startFn` param added for multipart callers (2026-06-26); `runId`/`status` + `onFinish` callback (2026-06-22) |
| `src/components/Header.jsx` | Done | Logo mark + "DATA INSERTION CONSOLE" subtitle, env dropdown + host badge (2026-06-22) |
| `src/components/LogViewer.jsx` | Done | Terminal log treatment, auto-scroll, colour-coded levels |
| `src/components/LiveOutput.jsx` | Done | Right-hand console card: run-id + status chip + stats strip + LogViewer (added 2026-06-22) |
| `src/tabs/BondsTab.jsx` | Done | localStorage persistence added (2026-06-26); force-currency + dry-run (2026-06-19/22) |
| `src/tabs/LoansTab.jsx` | Done | localStorage persistence added (2026-06-26); dry-run + currency override (2026-06-19) |
| `src/tabs/InterestTab.jsx` | Done | localStorage persistence added (2026-06-26); sizing + quantity ranges + strategy mode (2026-06-19) |
| `src/tabs/CsvUploadTab.jsx` | Done | CSV/Excel upload tab: drop zone, row range, delay, simulation, time_scale, dry-run (added 2026-06-26) |
| `src/tabs/TigOrdersTab.jsx` | Done | TIG Orders tab: issuance key, market type, registration/trade-desk free text, PM, count, qty range + sizing, dry-run (added 2026-06-26) |
| `src/tabs/HistoryTab.jsx` | Done | Run-history table; re-run (↻) prefills the tool's params (added 2026-06-22) |
| `src/tabs/SettingsTab.jsx` | Done | Full environment CRUD + reference dir paths; restyled, toast on save (2026-06-22) |

### Startup Scripts

| File | Status |
|---|---|
| `Setup.bat` | Done — one-time: creates `.venv`, installs `requirements.txt`, runs `npm install` |
| `Launch.vbs` | Done — double-click entry point; runs `Start-App.ps1` hidden |
| `Start-App.ps1` | Done — starts uvicorn + Vite hidden, waits for ports, opens browser (hardened — see 2026-06-19 changes) |
| `Stop-App.vbs` | Done — double-click to stop both servers |

> The earlier `start_backend.bat` / `start_frontend.bat` pair was superseded by the no-terminal `Setup.bat` + `Launch.vbs` flow above.

---

## Decisions Made During Implementation

### Bonds auth changed to login flow

The original `deal_poster_url_auth_v3.py` used a hardcoded `SESSION_AUTH_TOKEN` in `config.json`. The new utility adds a proper `TXN_LOGIN_AUTH` login step for bonds, consistent with loans and interest. This removes the need to manually copy tokens.

### Relative imports converted to absolute

The backend originally used relative imports (`from .routers import ...`). These fail when uvicorn runs `main:app` directly from the `backend/` directory. All imports were converted to absolute (`from routers import ...`, `from engines.bonds_engine import ...`). Uvicorn must be started from within `backend/`.

### Walrus operator removed from interest engine

The interest engine had `for w in (ws := [...])` which is a Python syntax restriction — walrus cannot be used in a comprehension iterable expression. Fixed by splitting into three lines.

### RefData made path-aware

The original bonds script loaded reference data from `./reference/` relative to the script file using a module-level constant. In the new utility, `RefData.__init__` takes an explicit `ref_dir: Path` argument passed in from the run parameters. The path defaults to the original location and is overridable from the Settings tab.

### Loans reference data is fault-tolerant

If `issuers.csv` or `currencies.csv` are missing or empty, the loans engine falls back to a hardcoded single issuer / 10-country dataset rather than raising an error. This lets the tool run even if the reference data directory is unavailable.

### `environments.json` is the single source of truth

No `.env` file, no per-script `config.json` files. All environment-specific configuration (host, credentials, SSL) lives in one file and is editable through the Settings tab. The `active` key records which environment is currently selected.

---

## Fixes Applied

| Issue | Fix |
|---|---|
| Walrus operator in comprehension iterable | Extracted to separate variable assignment |
| Relative imports breaking uvicorn startup | Converted all backend imports to absolute |
| `config_manager.py` — `ENVIRONMENTS_PATH` must resolve relative to the file, not CWD | Used `Path(__file__).parent / "environments.json"` |
| Reference data not included in new utility | Copied all CSVs to `backend/reference/bonds/` and `backend/reference/loans/`; `environments.json` now uses `{BACKEND_DIR}` token resolved at load time |

---

## Known Limitations & Open Items

### 1. No session persistence across browser refresh

Run state (logs, summary, running status) is stored in React component state. A browser refresh clears it. The backend run continues in its thread but the SSE connection is lost. The frontend has no mechanism to re-attach to an in-progress run after reconnecting.

**Workaround:** don't refresh mid-run. The live log output is not stored anywhere — though as of 2026-06-22 a *completed-run summary record* (tool, env, params, ok/fail/total, status, duration) is persisted to `history.json` and shown in the History tab. The full per-event log is still ephemeral; only the summary survives.

### 2. Passwords stored in plain text

`environments.json` stores passwords as plain text strings. This is acceptable for test environments but should not be used with production credentials.

**Future:** consider OS keychain integration or at minimum base64 obfuscation with a warning.

### 3. Interest capture — `REFRESH_AUTH_TOKEN` not verified independently

The interest engine uses both tokens in event POST headers. If the server rejects the refresh token but not the session token, the error will appear as an event NACK rather than a clear auth error message.

### 4. Loans multi-tranche — live server validation not yet done

Multi-tranche loans were implemented and tested in dry-run mode. The `ALL_LOAN_DEAL` query and `MASTER_LOAN_ID` linkage follow the spec exactly but have not been validated end-to-end against the live QA server in the new utility.

**Action:** run a multi-tranche loan with `--multi 1` and observe that tranche 2 carries `MASTER_LOAN_ID`.

### 5. Bonds tool — `BOND_CLASS` typo

The original script had `"Invetment Grade"` (typo). The new bonds engine corrects this to `"Investment Grade"`.

### 6. Node.js not installed on the development machine — RESOLVED (2026-06-19)

Node.js was subsequently installed and `node_modules` populated via `Setup.bat`. A follow-on launch failure was traced to a **stale PATH**: Node was added to the Machine PATH *after* the Windows session started, so the inherited PATH (used by `Launch.vbs` → `Start-App.ps1` → `npm`) did not contain `C:\Program Files\nodejs`, and the frontend silently failed to start. Fixed by having `Start-App.ps1` refresh PATH from the registry and resolve `npm.cmd` by absolute path (see 2026-06-19 changes).

### 7. Config changes require restart to affect running defaults

Changes saved in the Settings tab are written to `environments.json` immediately. However, the parameter form defaults in the Bonds/Loans/Interest tabs are only loaded when the React app first mounts. If you change `tool_defaults` in the Settings tab, refresh the browser to see the updated defaults in the forms.

### 8. No per-tab connection status indicator

The header shows which environment is active but does not show whether the last run to that environment succeeded or failed. A per-tab "last run" status badge would be a useful addition.

---

## Validation Performed

| Check | Result |
|---|---|
| All three engine files import without errors | Pass |
| FastAPI app imports without errors | Pass |
| uvicorn can start and bind to port 8000 | Pass (smoke tested — process started and killed cleanly) |
| `environments.json` loads with correct defaults | Pass |
| Interest engine quantity alignment logic | Inherited from `capture_interest.py` (previously validated against live QA) |
| Bonds SEC identifier checksums (CUSIP, ISIN, FIGI) | Inherited from `deal_poster_url_auth_v3.py` (previously validated) |
| Loans CUSIP check digit | Inherited from loan project (validated against Apple, IBM vectors) |
| Loans weekday date generation | Inherited from loan project (200-sample validated) |

---

## Changes — 2026-06-19

### 1. Launch reliability (`Start-App.ps1`)

"Launch App" was failing because the hidden launcher couldn't start the frontend. Root cause: `npm` was not on the PATH inherited by the launched process (Node was added to the Machine PATH after the session started). Fixes applied to `Start-App.ps1`:

- Refresh `$env:Path` from Machine + User registry values at startup.
- `Resolve-Npm` — locate `npm.cmd` via `Get-Command`, fall back to `%ProgramFiles%\nodejs` / `%APPDATA%\npm`, and invoke it by full path.
- `Test-Port` now probes both `127.0.0.1` and `::1` (Vite binds IPv6 `[::1]:5173`; uvicorn binds IPv4) — removes a false 45s readiness timeout.
- `Add-Type -AssemblyName System.Windows.Forms` added so the error message boxes work under the non-interactive host.

Verified end-to-end: backend `127.0.0.1:8000` and frontend `[::1]:5173` both reach HTTP 200 from a hidden launch.

### 2. String-typed numeric fields (Loans payload)

The server schema for several numeric fields was changed to a `oneOf: [string, null]` (DB columns migrated to string). Sending raw numbers produced `... must be valid to one and only one schema, but 0 are valid` NACKs. Converted to strings in `loans_engine._generate_tranche_details`:

- `FINAL_OID`, `ORIGINAL_ISSUE_DISCOUNT` (the OID fields) — were ints.
- `YIELD`, `FINAL_YIELD` — were floats.

(`SPREAD` / `FINAL_SPREAD` were already strings. `ISSUE_SIZE` / `FINAL_SIZE` remain numbers — their schema still accepts numbers.)

### 3. Full NACK error logging (Loans)

`loans_engine` previously logged only `ERROR[0]`. It now logs every entry in the `ERROR` array (`NACK (N error(s)) — ...` plus `also —` lines). This surfaced the `YIELD`/`FINAL_YIELD` failures that were hidden behind the `FINAL_OID` error.

### 4. Dry-run for all three tabs + payload preview

- Added a **Dry run** toggle to Bonds and Interest tabs (Loans already had one).
- In all three engines, dry-run now **skips auth and POST** and **logs the full generated payload** as pretty-printed JSON to the Live Output panel. Loans dry-run previously logged only issuer/ticker; it now also dumps the payload.
- See spec §9.1 for behaviour and the Loans `MASTER_LOAN_ID` caveat.

### 5. Force-currency for Bonds

Added a "Force currency (optional)" field to the Bonds tab, mirroring Loans. When set, `bonds_engine._build_tranche` uses it for every tranche's `CURRENCY_CODE` / `TRANCHE_CURRENCY` instead of `random.choice(ref.currencies)`. Blank = random as before.

### 6. Loans tab layout

"Delay between POSTs" and "Deal query wait" were side-by-side in one row; they are now stacked vertically (Delay first, then Deal query wait).

**Files touched:** `Start-App.ps1`; `backend/engines/{loans_engine,bonds_engine,interest_engine}.py`; `frontend/src/tabs/{BondsTab,LoansTab,InterestTab}.jsx`. No router/plumbing changes — `dry_run` and `currency` flow through the existing params dict. Frontend `vite build` passes.

### 7. Launch speed optimization (`Start-App.ps1`)

The launcher felt slow to reach the browser. The servers themselves are fast (backend import ~0.85s; Vite warm start ~0.4s) — the overhead was structural. Fixes:

- **Direct Vite invocation** — `node node_modules\vite\bin\vite.js` instead of `cmd /c npm run dev`, skipping the npm/cmd wrapper layers (~1s). Falls back to npm if node/vite.js isn't found.
- **Concurrent + fast port wait** — `Wait-ForPorts @(8000,5173)` polls both ports together at 150ms granularity, replacing two sequential `Wait-ForPort` calls that polled every 1s.
- **Lazy WinForms** — `Add-Type System.Windows.Forms` moved into the `Show-Error` helper (only on failure) instead of at the top of every launch.

Measured end-to-end from a hidden launch: both ports up at ~1.6s, browser opens at ~1.7s (verified HTTP 200 on both). See spec §12.2.

---

## Changes — 2026-06-22

UI redesign onto the **Operator design system** plus a new **Run History** feature. Driven by the handoff in the repo root (`README.md` + `PBI Test Utility*.dc.html` prototypes + `screenshots/`). The chosen direction was **A · Split** (top tabs + params-left / live-log-right), re-skinned dark/mono-forward.

### 1. Operator design-system restyle

`frontend/src/index.css` was rewritten from the old light theme to the Operator tokens (surfaces `#08090d`→`#161c28`, sky accent `#7dd3fc`, semantic emerald/rose/attention/indigo, DM Sans + JetBrains Mono via Google Fonts `@import`). Every existing class was restyled in place; class names were largely kept. Markup changes:

- **Header** — added a 30×30 glowing logo mark (mono `P`) and a "DATA INSERTION CONSOLE" subtitle; env `<select>` + host badge restyled.
- **Tabs** — Title Case labels; sky underline on the active tab; **History** tab added between Interest Capture and Settings.
- **Live output** — extracted to a new `LiveOutput.jsx` component. The success/failed/total stats moved from the params card (left) into the live-output card (right), under a header that shows the short run id and a **status chip** (idle / running / done / done·N failed / stopped) with a glowing, pulsing dot.
- **Run view layout** — params grid column widened to `372px 1fr`.
- **Settings** — restyled to the 248px sidebar + detail layout; success feedback is now a bottom-center **toast** (`Saved · <env>`).

`npm run build` passes.

### 2. Run History (new feature)

**Backend persistence chosen over client `localStorage`** (the handoff offered both) so history is shared across the QA team and survives browser/cache resets.

- `backend/history_manager.py` — a thread-locked JSON store (`backend/history.json`). `add_run` whitelists fields, coerces `ok`/`fail`/`total` to ints, validates `status` ∈ {done, failed, stopped}, stamps `id`/`ts`, and prepends. `list_runs` prunes by age (30 days) and count (newest 500). Both round-trip-tested.
- `backend/routers/history_router.py` — `GET /api/history` (list, newest-first) and `POST /api/history` (append). Registered in `main.py` (`prefix="/api/history"`).
- `frontend/src/tabs/HistoryTab.jsx` — reads `GET /api/history`, renders the table (time · tool · env · params · result · status · dur · ↻). Tool is colour-coded (Bonds sky / Loans teal / Interest indigo); stopped rows are dimmed.
- **Recording** — `useRunState(tool, onFinish)` now tracks start time + final summary and fires `onFinish({ok, fail, total, status, durSeconds})` exactly once per run (guarded against the `done`-after-`stop` double-fire). Each run tab composes a record (env name, params summary + raw params) and POSTs it via `addHistory`. The record is written client-side because the engine/SSE layer doesn't know the env *name* or a params summary; the backend just persists it.
- **Re-run (↻)** is **prefill-only**: it switches to the tool's tab and populates the params from `params_raw` but does **not** auto-fire (re-running posts real test data). Implemented via a `pendingRun` state in `App.jsx`, consumed by the tab's `useState` initializers on mount.

### 3. Environment switch now persists

Previously the header dropdown only set local React state (`switchEnv` reset on refresh). It now optimistically updates state **and** writes `active` back via `PUT /api/config`, so the selection survives a reload. See spec §4.3.

### 4. Bonds — Force currency layout

"Force currency" moved out of the shared row with Delay (ms) onto its own full-width row **above** Delay, relabelled "Force currency (optional)" with a contextual hint ("e.g., USD, EUR, GBP"). Cosmetic only — `buildParams` unchanged.

**Files touched:** `backend/main.py`, `backend/history_manager.py` (new), `backend/routers/history_router.py` (new); `frontend/src/index.css`, `App.jsx`, `api.js`, `hooks/useRunState.js`, `components/{Header,LogViewer,LiveOutput}.jsx`, `tabs/{BondsTab,LoansTab,InterestTab,HistoryTab,SettingsTab}.jsx`. No engine or SSE-plumbing changes. `vite build` passes; backend imports + history add/list round-trip verified.

> Note: the HTTP layer for `/api/history` was not exercised via FastAPI `TestClient` because `httpx` is not in the venv; the router mirrors the working `config_router` and the storage layer underneath it is tested. For a live check, launch the app and hit `GET /api/history`.

---

## Changes — 2026-06-26

### 1. Form value persistence (localStorage)

All three existing run-parameter tabs — BondsTab, LoansTab, InterestTab — now persist their field values to `localStorage` on every change and restore them on mount. This means the form shows the last values the user entered rather than the `environments.json` defaults on every reload.

**Priority chain** (lowest-to-highest wins):

```
hardcoded fallback  →  environments.json tool_defaults  →  localStorage  →  history re-run prefill
```

Each tab uses a distinct key (`pbi.bonds`, `pbi.loans`, `pbi.interest`). A `useEffect` dependent on all form state fields writes the current values; a module-level `lsRead()` helper reads them during component initialisation. No changes to routers or engines.

**Files touched:** `frontend/src/tabs/{BondsTab,LoansTab,InterestTab}.jsx`. Frontend `vite build` passes.

---

### 2. CSV Upload tab (new feature)

Integrates `pbi_csv_to_json_publisher_v2/csv_to_json_publisher_V4.py` into the utility as a new **CSV Upload** tab, positioned between Interest Capture and History.

#### Backend

**`backend/engines/csv_engine.py`** (new) — Ports `csv_to_json_publisher_V4.py` into the standard engine contract (`run_csv_upload(params, env, stop_event, log_queue)`):

- Reads the uploaded file from `params["file_bytes"]` (a `bytes` object) into a `BytesIO` buffer. Supports `.csv`, `.xlsx`, `.xls` via pandas `read_csv` / `read_excel`.
- **Auto-uppercases all column headers** on load. A file with `issuer_name` or `Issuer_Name` resolves to `ISSUER_NAME` in the payload.
- Row range parsing (`"2-5"` → `[2,3,4,5]`, blank → all), duplicate-column grouping (`.1`/`.2` suffixes → first non-null wins), value normalisation (dates, scientific notation, `00-01-1900` sentinel).
- Auth uses `env["credentials"]["bonds_loans"]` — same login flow as Bonds and Loans.
- Posts to `https://{host_name}/gwf//event_new_issuance_data` with the same headers pattern.
- Simulation mode: reads `INSERT_TIME` column, computes `delta / time_scale` sleep between rows; falls back to `delay_seconds` when a value is unparseable.
- Fixed-delay mode: sleeps `delay_seconds` between each row (skips the first).
- Dry run: logs a truncated JSON preview of each payload; skips auth and POST.
- Emits standard `log`/`summary`/`None` sentinel sequence.

**`backend/routers/csv_upload.py`** (new) — Thin FastAPI router registered at `/api/csv_upload`. The `/run` endpoint accepts `multipart/form-data` (`file: UploadFile` + `params_json: str`) rather than JSON, because the file must travel in the same request. File bytes are read async in the route handler, injected into the params dict, and handed to `start_tool_run("csv_upload", params, run_csv_upload)`. Stream / stop / status endpoints are identical to the other tools.

**`backend/main.py`** — `csv_upload` router imported and registered (`prefix="/api/csv_upload"`).

**`backend/requirements.txt`** — Added `pandas>=2.0.0`, `numpy>=1.26.0`, `openpyxl>=3.1.0`, `python-multipart>=0.0.9`. The latter three were installed manually into the venv for this session; `Setup.bat` will install them on fresh setups.

#### Frontend

**`frontend/src/api.js`** — Added `startCsvRun(file, params)`. Builds a `FormData` with `file` and `params_json` (JSON-stringified params), POSTs to `/api/csv_upload/run` without a `Content-Type` header (browser sets the correct `multipart/form-data; boundary=...` automatically).

**`frontend/src/hooks/useRunState.js`** — Added an optional third parameter `startFn`. When provided, `start()` calls `startFn(params)` instead of the default `startRun(tool, params)`. Backward-compatible: all existing callers pass nothing and get the old behaviour. The CSV Upload tab passes a `useCallback`-wrapped function that closes over the selected `File` object and calls `startCsvRun`.

**`frontend/src/tabs/CsvUploadTab.jsx`** (new):

- **Drop zone** — a dashed-border `.drop-zone` panel. Accepts drag-and-drop or click-to-browse for `.csv`, `.xlsx`, `.xls` files. Shows the file name + a ✕ clear button once a file is selected. An absolutely-positioned hidden `<input type="file">` is overlaid so native file-dialog behaviour is preserved while the whole panel is clickable.
- **Form fields:** row range (text, optional), delay seconds (number), simulation toggle (with time-scale field shown conditionally), dry-run toggle.
- **localStorage** persistence for all non-file fields under key `pbi.csv_upload`.
- `useRunState('csv_upload', recordRun, startFn)` — tool name `'csv_upload'` routes SSE/stop calls to `/api/csv_upload/stream/:id` and `/api/csv_upload/stop/:id`.
- **Run button** disabled until a file is selected.
- **History recording:** `params_raw` stores `{file_name, rows, delay_seconds, simulation, time_scale, dry_run}` — no file bytes. History re-run prefills these params but requires the user to re-select the file.

**`frontend/src/App.jsx`** — `CsvUploadTab` imported; `csv_upload` added to the `TABS` array between `interest` and `history`; rendered in the view switch with `prefill` / `onPrefillConsumed` wired the same as other tool tabs.

**`frontend/src/index.css`** — `.drop-zone`, `.drop-zone.drag-over`, `.drop-zone-icon`, `.drop-zone-label`, `.drop-zone-file` styles added before the scrollbar block.

**Files touched:** `backend/main.py`, `backend/requirements.txt`, `backend/engines/csv_engine.py` (new), `backend/routers/csv_upload.py` (new); `frontend/src/api.js`, `frontend/src/hooks/useRunState.js`, `frontend/src/index.css`, `frontend/src/App.jsx`, `frontend/src/tabs/CsvUploadTab.jsx` (new). `vite build` passes; backend imports verified (`python -c "from engines.csv_engine import run_csv_upload; from routers.csv_upload import router"`).

#### Caveats

- `insert_time_column` is fixed to `"INSERT_TIME"` in the engine (not user-configurable from the UI). If a file uses a different column name for timestamps, simulation mode will fall back to `delay_seconds` for every row.
- `max_sleep_seconds` defaults to `None` (no cap). Not exposed in the UI.
- `message_type` and `service_name` are fixed to `EVENT_NEW_ISSUANCE_DATA` / `ISSUANCE_EVENT_HANDLER`. Not exposed in the UI.

---

### 3. TIG Orders tab (new feature)

Adds a fifth tool tab — **TIG Orders** — that posts `EVENT_TIG_CREATE_ORDER` events for a different client. It sits between Interest Capture and CSV Upload. Decision: kept as a **separate tab** rather than a toggle inside Interest Capture, since the two events serve different purposes (rate capture vs. order placement) and have structurally different payloads. See spec §16.

**Requirements (confirmed with the user before building):**

- **Auth:** session-token only (like Bonds/Loans), using a **new dedicated `tig_orders` credential set**. A double check was made vs. Interest's session+refresh model — TIG uses session only.
- **Run loop:** user sets a `count`; each order gets an **independent random quantity**, increment-aligned (min-piece / increment, same approach as Interest).
- **`ACCOUNT_CODE`:** auto-generated per order — 6-char uppercase alphanumeric, **guaranteed ≥1 letter and ≥1 digit**. (The sample payload's `"Test 16"` was illustrative only and contradicts the stated 6-char rule, so the rule won.)
- **Fields exposed:** `ISSUANCE_KEY` (manual), `REGISTRATION_TYPE` (free text), `TRADE_DESK` (free text), `PORTFOLIO_MANAGER` (optional, defaults to `John Doe`, editable), `MARKET_TYPE` (dropdown, default `Market`; `Limit` is a stub for later limit-type/spread fields).
- **Constant fields** (hardcoded, not in UI): `ORDER_STATUS_FIELD` = `""`, `SECURITY_ID` = `null`, `REGULATION_SUBCATEGORY` = `null`.

#### Backend

**`backend/engines/tig_engine.py`** (new) — `run_tig_orders(params, env, stop_event, log_queue)` following the standard engine contract. Session-token auth via `credentials.tig_orders`; posts to `https://{host}/gwf/EVENT_TIG_CREATE_ORDER` (note the **single** `/gwf/`). Helpers: `_gen_account_code()` (6-char, letter+digit guaranteed) and `_aligned_quantity()` (increment-snapped random in range). Detects `MESSAGE_TYPE` NACK and HTTP errors; supports dry-run (skip auth+POST, dump payload JSON). `_interruptible_sleep` honours `stop_event` inside the inter-order delay.

**`backend/routers/tig_orders.py`** (new) — thin router mirroring `interest.py`. Registered in `main.py` at `prefix="/api/tig_orders"` — so the frontend's generic `startRun`/SSE/stop calls (keyed on tool name `tig_orders`) route correctly with no `api.js` changes.

**`backend/environments.json`** — added `credentials.tig_orders` (`username`/`password`, blank by default) to every environment, and a `tool_defaults.tig_orders` block (issuance_key, market_type, registration_type=`144a`, trade_desk=`GLOBALFI`, portfolio_manager=`John Doe`, count=5, delay_seconds=1, quantity_ranges 100k–1M, sizing 50k/50k).

#### Frontend

**`frontend/src/tabs/TigOrdersTab.jsx`** (new) — mirrors `InterestTab` (localStorage key `pbi.tig_orders`, prefill chain, `useRunState('tig_orders', recordRun)`, history recording). Fields per spec §16.3; a hint line explains the auto account-code + aligned-quantity behaviour.

**`frontend/src/App.jsx`** — imported `TigOrdersTab`; added `{ id: 'tig_orders', label: 'TIG Orders' }` to `TABS` between `interest` and `csv_upload`; rendered with the usual `prefill` / `onPrefillConsumed` wiring.

**`frontend/src/tabs/SettingsTab.jsx`** — added a **"Create TIG Orders credentials"** section (username/password) and the `tig_orders` default in `addEnvironment`.

**`frontend/src/tabs/HistoryTab.jsx`** — added `tig_orders` (and `csv_upload`) to `TOOL_LABEL`.

**Files touched:** `backend/main.py`, `backend/environments.json`, `backend/engines/tig_engine.py` (new), `backend/routers/tig_orders.py` (new); `frontend/src/App.jsx`, `frontend/src/tabs/{TigOrdersTab (new),SettingsTab,HistoryTab}.jsx`.

**Validation:** backend imports + `environments.json` parse verified; engine unit-checked (2000 account codes match `[A-Z0-9]{6}` with letter+digit guaranteed; 2000 quantities all 50k-aligned within 100k–1M) and dry-run emits a payload field-identical to the client's sample. `vite build` passes.

#### Caveats

- **`MARKET_TYPE = "Limit"`** currently just sets the field to `"Limit"`; the additional Limit inputs (limit type, spread, etc.) are a deliberate stub to be added later.
- **TIG NACK shape is assumed** to mirror the other tools (`MESSAGE_TYPE` containing `NACK`, reason at `DETAILS.TEXT`/`DETAILS.ERROR`). Not yet validated end-to-end against a live server — the engine has only been exercised in dry-run. First live run should confirm the success/NACK branches log correctly.
- **POST headers** mirror the Bonds pattern (`SESSION_AUTH_TOKEN` + standard headers). If the TIG endpoint needs different/extra headers, adjust `headers` in `tig_engine._run`.

---

## Changes — 2026-07-21

### Broker-email generation for Bonds & Loans (new feature)

Bonds and Loans can now render each generated deal as a **broker-style email** and send it (via Outlook Classic) to a recipient set in Settings, feeding the Genesis email-parsing POC so an email-parsed issuance can be compared/merged with the API-payload issuance. Requirements were confirmed with the user across four decision points (Outlook Classic present; inbox delivery is sufficient; `.msg`-only manual upload; multiple per-bank formats; boilerplate **and** random-pool gap-filling). See spec §17 and plan.md.

**Formats catalogued** from the `Genesis_Email_Parsing_POC-2026-07-10` samples: bonds are labelled vertical blocks (+ a multi-tranche table variant and a compact syndicate summary); loans are per-bank labelled blocks (JPM / Barclays / Citi / RBC). Multi-*deal* digest emails (bank calendars) are deferred.

#### Backend

- **`engines/email_builder.py`** (new) — pure, dependency-free HTML renderers, one per format, dispatched by `build_email(tool, payloads, fmt)`. One email per deal (multi-tranche deals render all tranches). Gap fields filled from `_BOILERPLATE` (fixed) and `_POOLS` (random pick): Call Protection, Non-Call, OID talk, floor, settlement, business blurb. Epoch-ms dates → "November 2032" / long dates for "Commitments due".
- **`engines/outlook_sender.py`** (new) — `send_via_outlook(subject, html, recipient, save_dir)` using `win32com` + per-thread `pythoncom.CoInitialize`. Creates an Outlook mail item, sets `HTMLBody`, optionally `SaveAs(..., olMSG)`, then `Send()`. Raises `OutlookUnavailable` (skip-with-warning) if pywin32/Outlook is missing.
- **`engines/bonds_engine.py` / `loans_engine.py`** — added `email_mode` (`off`/`both`/`email_only`) + `email_format`. `email_only` skips auth and POST; `both` POSTs and also emails per deal; an `emit_email` helper logs the result. In `email_only` the summary counts emails per deal.
- **`routers/_shared.py`** — injects the top-level `email` config block into bonds/loans `params` (same pattern as `ref_dir`), so no router/endpoint changes were needed.
- **`environments.json`** — new top-level `email` block (`recipient`, `save_copy_dir`).
- **`requirements.txt`** — added `pywin32>=306 ; sys_platform == "win32"`. **Re-run `Setup.bat`** (or `pip install -r requirements.txt`) before first use.

#### Frontend

- **`tabs/BondsTab.jsx` / `LoansTab.jsx`** — an **Email** slider (mirrors Dry run) + an **Email only** sub-toggle + a **format** dropdown (shown when Email is on). State persisted to localStorage and included in the re-run prefill chain. `email_mode` is derived (`off`/`both`/`email_only`) and sent in params.
- **`tabs/SettingsTab.jsx`** — a new **Email** section (recipient + optional save-copy folder), persisted into the top-level `email` block on Save.

**Files touched:** `backend/engines/{email_builder,outlook_sender}.py` (new), `backend/engines/{bonds_engine,loans_engine}.py`, `backend/routers/_shared.py`, `backend/environments.json`, `backend/requirements.txt`; `frontend/src/tabs/{BondsTab,LoansTab,SettingsTab}.jsx`.

**Validation:** all seven format renderers build valid HTML from representative payloads; both engines exercised end-to-end in `email_only` (with an empty recipient so no real mail was sent) — build → emit → summary flow and counts verified. Backend imports load; `vite build` passes.

### Bond formats reworked from real samples (2026-07-22)

The first-pass bond templates were generic and repetitive. Parsed all 10 renamed `Format 1–10.msg` samples with `extract-msg` (added as a dev/parse dependency) and rebuilt the bonds side of `email_builder.py` as **5 genuinely distinct structural layouts** (confirmed with the user: structural variants only, random+boilerplate gap-fill, generic placeholder signature + `TD` lead line):

| Format id | Structure | Sample |
|---|---|---|
| `bond_stacked` (default) | Deal header + per-tranche table (label │ T1 │ T2 …) | 1/2/7/9 |
| `bond_single` | Single labelled block per tranche | 3/6 |
| `bond_colon` | Compact `Label: value` + repeated per-tenor block | 4 (Amazon CAD) |
| `bond_inline` | `*** IPTs … ***` + `Label value value` single-line rows | 8 (SMBC) |
| `bond_narrative` | Investor-call/mandate prose + stacked table | 7 (SpaceX) |

New helpers: `_short_date` (`Feb 04, 2029`), `_settlement` (`T+n (date)`), `_size_line`, `_tenor_words`, `_bond_ratings_inline`, `_td_lead`, `_signature_html`; new gap pools (`optional_redemption`, `ranking`, `uop_bond`, `canada`, `settle_days`). `BondsTab.jsx` dropdown + default (`bond_stacked`) and spec §17.3 updated. All 5 render distinctly for single + multi tranche with no `None` leaks; `vite build` passes. Loan formats unchanged (the user only supplied bond samples).

#### Caveats / open items (email generation)

- **Not yet sent through a live Outlook** in this session (would deliver a real email). First real `both`/`email_only` run should confirm the Outlook `Send()` path and, if applicable, dismiss the one-time Outlook "send on your behalf" guard prompt.
- **Parser fidelity is the real risk** — the generated templates are modelled on the samples but the actual Genesis parser's field extraction hasn't been validated against them. Expect to iterate on `email_builder.py` templates after the first upload/compare.
- **Multi-deal digest formats** (bank calendars bundling several borrowers) are not implemented.
- Emails represent one deal; in `both` mode email issues are logged but don't affect the POST-based summary counts.

---

## Changes — 2026-08-06

### Email Comparison (LLM accuracy harness) — session S1: steps 1–2

First build session for spec §18. Establishes the schema reference data, the payload→column
field map, and the SQLite expectation store. No existing tool behaviour was touched; nothing
is wired into an engine yet (that is step 4).

#### Step 1 — schema reference data + maps

**`backend/reference/db_schema/`** (new directory):

| File | Contents |
|---|---|
| `issuance_deal_fields_size.csv` | 53 columns |
| `issuance_data_fields_size.csv` | 180 columns |
| `issuance_fields_size.csv` | 146 columns |
| `issuance_security_fields_size.csv` | 12 columns |
| `field_map.csv` | 391 rows — every column of all four tables, exactly once |
| `vocab_map.csv` | 34 seed translations (generated value → DB value) |

`field_map.csv` columns: `asset_class, table, column, source_kind, source, tier, normalizer, notes`.
Coverage by tier:

| Table | Columns | T1 | T2 | T3 | x (not scored) | Scored |
|---|---|---|---|---|---|---|
| `issuance_deal` | 53 | 5 | 13 | 4 | 31 | 22 |
| `issuance_data` | 180 | 20 | 28 | 13 | 119 | 61 |
| `issuance` | 146 | 19 | 27 | 10 | 90 | 56 |
| `issuance_security` | 12 | 2 | 2 | 2 | 6 | 6 |
| **Total** | **391** | **46** | **70** | **29** | **246** | **145** |

Generated by a one-off scratchpad script from the §18.5 decisions, which asserted that every
mapping key names a real column (catching typos) and that every column is covered exactly
once. **`field_map.csv` is now the hand-editable source of truth** — the generator is not part
of the repo and is not needed again.

`vocab_map.csv` gained a **`confirmed`** column beyond the spec's four (`asset_class, column,
generated_value, db_value`): 9 of the 34 seeds are confirmed from the sample exports, the rest
are best guesses flagged `no` so the calibration loop (§18.8.2) knows which to verify rather
than trust. Spec §18.8 updated.

#### Step 2 — `backend/expected_store.py` (new, ~600 lines)

SQLite store at `backend/expected/pbi_util.db`. DDL is generated **from the schema CSVs**, so a
refreshed export picks up new app columns with no code change. Mirror tables carry the app's
column names byte-identically plus nine `util_`-prefixed metadata columns.

- Tables: four `util_issuance*` mirrors + `util_run`, `util_email`, `util_llm_run`,
  `util_comparison`, `util_comparison_field`, `util_map_override`. All six support tables were
  created now (DDL only, no business logic) to avoid a mid-build schema migration.
- All values stored as `TEXT`, bools as `true`/`false`: the store keeps what it was given and
  leaves typing to the comparator, so a Postgres numeric round-trips without silent coercion.
- **`insert_rows` rejects unknown column names** rather than dropping them — a typo would
  otherwise silently lose an expectation.
- **Length validation** against `character_maximum_length` returns warnings; values are stored
  intact, never truncated, and an over-length value is never fatal.
- Thread-safe via a module `RLock` + per-operation connection in WAL mode (engines run on
  background threads), mirroring `history_manager.py`.
- `effective_map()` = seed CSVs + active overrides, with `overridden` / `override_id` stamped
  on every touched entry; `deactivate_override()` reverts cleanly.

**`backend/environments.json`** — added the top-level `compare` block (§18.10): `store_path`
(blank = default), `ingest_window_minutes` 60, `expected_datasource` `LLM`,
`baseline_datasource` `BBG`, `auto_capture` true, `tier_weights`, `date_tolerance_days`.

#### Findings written back into the spec

1. **Row counts were guessed in the spec** as 12/52/145/181; the real counts are
   **53/180/146/12 = 391**. §18.16 verification column corrected.
2. **`t_issuance` is a projection, not a superset of `t_issuance_data`.** Seven *scored* columns
   do not exist on it: `issuer_name`, `datasource_id`, `bookrunners_as_supplied`,
   `bnd_bank_as_supplied`, `rating_outlook`, `issuance_bond_type`, `security_status`. So the
   issuer is identified there by `issuer_ticker`, and **the lane discriminator for `t_issuance`
   is `issuance_created_by`**, not `datasource_id`. §18.5.2 and §18.8.3 updated (the §18.8.3
   lane table now names the discriminator per table: `datasource_id` / `issuance_created_by` /
   `deal_id_datasource` / none).

#### Validation

50-assertion suite, all passing: schema counts and lengths · field_map coverage (391) and
tier/normalizer lookups · idempotent DDL · run/email round-trip incl. `rendered_fields` JSON ·
insert + query on all four mirror tables · three lanes coexisting and separable · unknown-column
and bad-`util_source` rejection · over-length warning with the value stored intact · vocab and
`expect_null` overrides applied, flagged and reverted · CSV export matching app column order
and `NULL` convention · **120 concurrent inserts across 3 threads with no errors or loss** ·
`delete_run` cascade leaving other runs untouched. `python -c "import main"` from `backend/`
passes; `environments.json` parses.

#### Corrections from the S2 review pass

Two defects in the S1 artefacts, found by an independent probe during review of S2 and fixed
in place:

1. **`expected_store` read paths required `init_store()` to have run.** `effective_map()` →
   `list_overrides()` raised `sqlite3.OperationalError: no such table: util_map_override` on a
   store that had never been created — so `build_expected()` blew up whenever it was called
   outside an engine run (any test, and the comparator in step 5). All reads now go through a
   `_read()` helper that returns `[]` when the store file is absent, and tolerates a missing
   table (a file left by an interrupted first run). **Only writes create the store**, which also
   keeps a pure projection from creating a database as a side effect of reading. Spec §18.11
   updated.
2. **`vocab` was applied to columns the app does not translate.** `issuer_sector`, `bond_class`,
   `bond_seniority` and `tranche_status` are stored as supplied, but were marked `vocab` in
   `field_map.csv` — which demanded a seed row per reference value (`sectors.csv` has dozens
   beyond the eight hardcoded fallbacks) and produced an unmapped-vocab `warn` per deal for no
   benefit. Those four are now `ci_text`, and the 18 identity rows were dropped from
   `vocab_map.csv` (34 → 16). `vocab` now covers only the six columns where a real translation
   happens or the mapping is unknown: `registration_type`, `tranche_reg_type`, `coupon_frequency`,
   `day_count`, `use_of_proceeds`, `rating_classification`. Spec §18.8 updated with the table and
   the capture-time vs compare-time warning semantics. This resolves S2's first open question.

Verified after both fixes: all five bond formats project 1 deal / 3 data / 3 issuance / 3
security rows from a 3-tranche 144A deal with **zero warnings**; format-dependent NULLs behave
(`bond_colon` → no UoP, outlook or call indicator, 13 rendered fields vs 26 for `bond_stacked`;
`bond_inline` → outlook NULL because it prints ratings without one); `t_issuance` agrees with
`t_issuance_data` on all 133 shared columns; `issuance_created_by = LLM`. S1's 50-assertion
store suite re-run clean.

#### Caveats

- The store is created lazily by `init_store()`; nothing calls it yet (step 4 wires it in).
- `util_comparison` / `util_comparison_field` / `util_llm_run` have DDL but no accessors yet —
  they arrive with steps 7 and 10.
- 25 of the 34 `vocab_map.csv` seeds are unconfirmed guesses (`confirmed=no`), most notably the
  whole `registration_type` mapping. Expect the first live run to correct them through the
  calibration loop.

### Email Comparison — session S2: steps 3–4

Ground-truth capture is now live: every generated bond email also writes the database state it
*should* produce into the `util_*` mirror tables. Nothing about the Bonds POST path, Loans,
Interest, TIG or CSV Upload changed; the only behavioural change to an existing tool is that
`build_email` returns a third value (see below) and the Bonds engine can mint a run-scoped
ticker.

#### Step 3 — `backend/engines/expected_writer.py` (new, ~400 lines)

Pure projection: `(payloads, email_meta) -> {issuance_deal, issuance_data, issuance,
issuance_security}` row dicts. No network, no DB, nothing written — the only read is
`expected_store.effective_map()` for the field/vocab maps, so calibration overrides apply
automatically.

- **The map decides which columns exist.** The writer walks `field_map.csv` and projects only
  `source_kind ∈ {payload, derived, const, pool, email_meta}`; `system` / `null` columns are
  left NULL and not scored. A `payload` column needs no code — the DETAILS key is read out of
  the map's `source` text — so adding a straight copy is a CSV edit. Anything derived needs a
  resolver in `_RESOLVERS`, and a **missing resolver is reported as a warning**, never silently
  dropped.
- **Expectations carry the normalized value, never the rendered prose** (§18.6.1): the `vocab`
  normalizer is applied at write time, so `use_of_proceeds` holds `GENERAL CORPORATE PURPOSES`
  rather than the 150-character sentence the email printed, `rating_classification` holds `IG`,
  and `rating_outlook` holds the `Stable` that `_bond_ratings_rows` glued onto each agency
  rating. An **unmapped** vocab pair keeps the generated value and emits a warning naming the
  pair, so the map fills in through the calibration loop instead of the expectation quietly
  asserting that the prose is what the DB stores.
- `t_issuance` is projected from the same resolvers, honouring the S1 finding that it is a
  projection and not a superset — no `issuer_name` / `datasource_id` / `rating_outlook` /
  `*_as_supplied` / `issuance_bond_type` / `security_status`, and `issuance_created_by` as the
  lane discriminator. Verified: all 53 shared columns match the audit row exactly.
- A `144A/Reg S` tranche produces **two** `issuance_security` rows, one per identifier set (D2).
- Rows carry `util_deal_seq` / `util_tranche_seq` / `util_match_key`, ready for `insert_rows`.
  The match key is §18.7 key 2 (`TICKER|CCY|TENOR`) on tranche rows, the ticker on the deal row,
  and the ISIN on security rows.
- Columns resolving to NULL are **omitted** rather than written as explicit NULLs — the store
  treats absent and NULL identically, and an omitted key keeps the stored row readable.

#### Step 4 — engine wiring

**`backend/engines/email_builder.py`** — `build_email` now returns `(subject, body_html, meta)`:

| `meta` key | Contents |
|---|---|
| `rendered_fields` | Sorted **app column names** the chosen format actually printed (§18.6). Same vocabulary as `field_map.csv`, so the comparator intersects by column name. |
| `gap_fill` | The values the *template* invented: the `uop_bond` prose picked, the `T+n` settlement number, the per-tranche `optional_redemption` line, the rating outlook. Only the renderer knows these; `expected_writer` needs them. |

Recording is thread-local (`_state.rec`), live only for the duration of one `build_email` call,
so concurrent engine threads cannot cross-contaminate. `_pick()` records every pool draw
automatically; the format functions and the shared helpers (`_settlement`, `_size_line`,
`_tenor_words`, `_cusip_of`, `_isin_of`, both ratings renderers) call `_mark(...)`.
`loans_engine` was updated for the new arity; loan renderers are not instrumented (Bonds is the
v1 comparison scope).

**`backend/engines/bonds_engine.py`**:

- `capture_expected` (default `compare.auto_capture && email_mode != "off"`) and
  `unique_ticker` (default = `capture_expected`) params.
- `_gen_run_ticker()` mints one 6-char ticker per run (letter + 5 base-36), threaded through
  `_build_single` / `_build_multi` / `_pick_issuer` so **every deal in the run and both lanes**
  share it (D8). Issuer names still vary per deal — the ticker is the match key, the name is not.
  *(Superseded 2026-08-10: one ticker per deal, derived from the issuer name. See the changelog.)*
- `emit_email` was split: build → send → capture. Capture fires **whether or not the send
  succeeded**, with `util_email.send_status` = `sent` / `skipped` / `failed`, and works in
  `dry_run` and `email_only` (SQLite only, no network or credentials).
- **Capture can never fail a run.** Store init, `add_email`, projection and insert are all inside
  `try/except Exception` that logs a `warn`, exactly like the `OutlookUnavailable` path. A store
  that will not open just turns capture off for the run with one warning.
- `util_run` is created up front and finalised with deal/tranche counts at the end.

**`backend/routers/_shared.py`** — injects the top-level `compare` block and `env_name` into the
bonds `params` dict, mirroring how the `email` block is already injected.

#### Verification

167-assertion suite (scratchpad, not committed), all passing, against a real 3-tranche generated
deal with one tranche forced to `144A/Reg S` + `Float`:

unique-ticker shape/sharing/freshness · all five bond formats return non-empty
`rendered_fields` with **no field outside `field_map.csv`** · format-specific expectations
(`bond_colon` renders no ticker and no UoP; only `_bond_ratings_rows` formats record an outlook)
· 1 deal / 3 data / 3 issuance / 4 security rows with matching counts · **no resolver gaps and no
unparsed payload sources on any of the five formats** · no NULL, empty-string or untrimmed value
written · vocab normalization (UoP prose → code, `Semi-Annual` → `Semi Annual`, `144A/Reg S` →
`SEC Registered`, `Investment Grade` → `IG`) · derived columns (`is_144a`, `is_reg_s`, `tenor`,
`years_to_maturity`, `coupon_index` from float IPTS and NULL on fixed, `announcement_dt`,
`call_indicator`, bookrunner union, `deal_currencies`) · `t_issuance` projection identical on all
53 shared columns · D2 two-security rule · match keys · **length validation clean on every row of
every format** · store round-trip incl. `rendered_fields` JSON · **end-to-end `email_only` run
with an empty recipient** (send fails, both deals still captured, `send_status = failed`, run
finishes green with a summary) · **`dry_run` + `both`** (captured, no auth attempted) ·
**forced store exception** (logged as `warn`, run still green with a summary) · capture and
unique tickers correctly off when `email_mode = off`.

`python -c "import main"` from `backend/` passes; `npm run build` passes (frontend untouched).

#### Findings written back into the spec

1. **No bond format renders `_POOLS["business_bond"]`** — §18.5.1 claimed `bond_narrative` did.
   `_bond_narrative`'s prose is hardcoded; only the loan templates draw from that pool. So
   `tranche_issuer_business_description` is always NULL for bonds. §18.5.1 corrected.
2. **Three gap-fill columns are format-dependent** — `rating_outlook` (NULL for `bond_inline`,
   which prints ratings without an outlook, and `bond_colon`, which prints none),
   `call_indicator` and `tranche_settlement_period` (NULL where the template prints no Optional
   Redemption or `T+n` line). The expectation is NULL there rather than a guess, which is the
   whole point of `rendered_fields`. Added as a table in §18.5.1.
3. **`build_email`'s third return value carries `gap_fill` as well as `rendered_fields`** — §18.6
   only specified the latter, but the expectation cannot be built without the render-time picks.
   §18.6 now documents the full return shape.

#### Caveats

- **No Bonds-tab UI yet** for `capture_expected` / `unique_ticker`. Both default on whenever
  email generation is on, and can only be overridden through the API today. Folded into step 8.
- **`vocab_map.csv` under-covers `issuer_sector`**: the seeds carry the eight hardcoded fallback
  sectors, but `reference/bonds/sectors.csv` has many more (`Agriculture`, `Automotive`,
  `Transportation`, …), so most runs log one unmapped-vocab warning per deal. Behaviour is
  correct — the expectation keeps the generated value and flags the pair — but the first live
  runs will be noisy until the calibration loop (step 9) or a hand-edit fills the map. The same
  applies to any `BOND_SENIORITY` / `DAY_COUNT` value outside the seeds.
- `send_status` is `failed` (not `skipped`) when no recipient is configured, because
  `send_via_outlook` raises `RuntimeError` for that case. The reason is in `send_note`.
- Nothing reads the captured rows yet — the comparator is step 5 and the endpoints are step 7.

### Email Comparison — session S3: step 5

The scoring engine. Given the expected rows captured at email-generation time and the rows the
agent actually produced, it pairs them, judges every mapped column and produces the run's
scores. Nothing existing was touched — this is one new module plus four documentation edits to
§18.

#### `backend/comparators.py` (new, ~1,150 lines)

**Pure**: no database, no network, no writes. Rows are dicts of app column names, the
field/vocab maps arrive as `expected_store.effective_map()` output (injectable via `maps=`, so a
router comparing 50 row pairs loads them once), and `effective_map`'s read-degrades-to-seeds
behaviour means importing and using the comparator never creates the store.

| Layer | What it does |
|---|---|
| **Normalisation** | All ten normalizers of §18.8. Every one copes with both storage shapes, because the store keeps `'true'` / `'500000000'` / epoch-ms strings while a Postgres fetch returns `'True'` / `'1000.00000'` / `'2026-08-06 00:00:00+00'`. Unparseable input falls back to case-insensitive text rather than raising, so one malformed cell cannot kill a compare pass. |
| **Row matching** | §18.7 keys 1–4 in priority order, each actual row consumed once; every pair carries its key, a confidence (`exact` / `high` / `low`) and an `ambiguous` flag; `unmatched_expected` / `unmatched_actual` are reported separately (recall vs precision). |
| **Verdicts** | Two-way and the full §18.8.1 three-way table, with `EXTRA` reported for populated tier-`x` columns and `NOT_COMPARED` dropped from the diff (246 of the 391 columns are tier `x`). |
| **Scoring** | `field_accuracy`, `weighted_accuracy` (T1 = 5, T2 = 2, T3 = 0 from `compare.tier_weights`), `extraction_accuracy`, `coverage_accuracy`, `tier1_pass_rate`, `row_recall` / `row_precision`, `tranche_count_accuracy`, per-tier and per-table breakdowns, and a per-`table.column` leaderboard sorted worst-first. |
| **Assertions** | All seven of §18.9 as `pass` / `fail` / `skipped` checks with the offending rows named. |

Entry point for step 7: `compare_run(expected, actual, baseline=None, *, maps, config,
rendered_fields, three_way, assertions_lane)` → `{scores, tables, findings, assertions,
field_leaderboard, calibration}`, where `findings` already has the column shape
`util_comparison_field` stores. `lane_of(table, row)` is exposed for `db_reader` (step 6) — it
knows the per-table discriminators from §18.8.3 (`datasource_id` / `issuance_created_by` /
`deal_id_datasource` / none).

#### Decisions taken while building (all written back into §18)

1. **NULL is a value in the three-way table** — `expected A / baseline NULL / actual NULL` is
   `CALIBRATION`, not `MISSING`. Both references say the column is empty, so the expectation is
   the outlier; charging it to the agent is precisely what three-way scoring exists to prevent,
   and "we expect a field the app never populates" is the most common early failure.
2. **`AGENT_ERROR` with a null actual is reported as `MISSING`** — same scoring effect, but
   *dropped the field* and *invented a different value* are different defects.
3. **Lane discriminators are always compared two-way.** The baseline row carries `BBG` *by
   definition*, so three-way scored `datasource_id` / `issuance_created_by` /
   `deal_id_datasource` as `AGENT_BETTER` on **every row** — an inflated headline. Caught by the
   test suite, fixed with `LANE_COLUMNS`; two-way they remain a real Tier-2 check on D1.
4. **Match key 2 requires all three parts.** With the tenor missing it degenerates to
   ticker + currency, which every tranche of a single-currency deal shares — it paired rows
   arbitrarily and called it `high` confidence. Also caught by the suite; such a row now falls
   through to key 3.
5. **Denominators pinned down** (§18.8 note): both-null is `NOT_COMPARED` and `EXTRA` is never
   scored, so neither reaches `comparable_fields`; extraction is scored over matched pairs
   (an unproduced row is a *matching* failure, reported by `row_recall`); coverage is scored over
   every Tier 1–2 field the utility actually **populated**, counting an unmatched expected row as
   a total loss; `tier1_pass_rate` is per matched row pair, which per table on `issuance_data` is
   exactly the spec's "tranches".
6. **Assertions can be `skipped`.** They run over one lane's rows, so a CSV export missing
   `issuance_data_id` is missing evidence, not a defect — failing it would train people to ignore
   the chips.

#### Verification

224-assertion suite (scratchpad, not committed), green on five consecutive runs against freshly
generated random deals:

- **Every normalizer in §18.8**, both storage shapes and both directions: `1000` == `1000.00000`
  and the 1e-6 epsilon · `750mm` / `1.5bn` / `500k` / `USD 200.4mm` · epoch-ms == epoch-s == ISO
  == postgres timestamp == `dd-MM-yyyy`, day granularity, tolerance 0 vs 3 (accepts +3d, rejects
  +4d) · `true/TRUE/1/Y/Yes/t/True` and their falses, sqlite `'true'` == postgres `'True'` ·
  `"Barclays/BNP Paribas"` == `"BNP PARIBAS, BARCLAYS"` · ratings order- and `(Exp)`-insensitive ·
  `5Y` == `5 Y` == `5 Year` == `5` == `5yr`, `12M` == `1Y` · vocab `Semi-Annual` → `Semi Annual`,
  UoP prose → code, `144A/Reg S` → `SEC Registered`, unmapped pair flagged, identical unmapped
  values still equal · `NULL` / `''` / `None` all read as null.
- **Every row of the §18.8.1 table** plus the two-way set and the null/lane refinements above.
- **Identical-copy = 100%** on a real 3-tranche capture (one tranche forced to `144A/Reg S`, so
  4 security rows) — 375 comparable fields, every one `MATCH`, all eight scores 1.0 — and
  repeated for **all five bond formats**. Also 100% for native-vs-TEXT rows and for a
  Postgres-shaped actual (`'True'`, `1000.00000`, ISO timestamps), where the matches arrive as
  `NORMALIZED_MATCH` instead.
- **Corrupted copies move the score correctly**: a Tier-1 corruption → one `MISMATCH` and a
  dropped `tier1_pass_rate`; a nulled Tier-2 field → one `MISSING`; a populated tier-`x` column →
  `EXTRA` that leaves `comparable_fields` unchanged; a Tier-3 miss leaves `weighted_accuracy` at
  1.0 while `field_accuracy` falls.
- **Matching**: key 1 pairs rows whose ticker and tenor were falsified; key 2 when identifiers
  are stripped; key 3 when tenor is stripped too (maturity ±3d); key 4 on a lone ticker candidate
  at `low`; missing tranche → recall 2/3 and `tranche_count_accuracy` 0; invented tranche →
  precision 3/4; `issuance_security` row counts reported but excluded from the run's recall (D2).
- **Extraction vs coverage**: nulling `day_count` (never rendered by `bond_stacked`) leaves
  extraction at 1.0 while coverage falls; nulling `issuer_rating` (rendered) moves extraction.
  `bond_colon` renders 13 fields against `bond_stacked`'s 24–26, and every rendered field name
  resolves to a real `field_map.csv` column.
- **Assertions**: all seven pass on well-formed application rows and each fails on its own
  induced defect (stale `issuance_data_id`, stale `latest_issuance_data_id`, wrong
  `db_number_of_tranches`, wrong `total_issuances`, a tranche with no security row, three
  `deal_id`s under one ticker, a duplicated `issuance` row); missing columns skip rather than
  fail.
- **Purity**: comparing without `maps=` does not create the store, `compare_run` does not mutate
  its inputs, empty inputs return `None` scores rather than raising, and the findings are
  JSON-serialisable for `util_comparison_field`.

`python -c "import comparators; import main"` from `backend/` passes. Frontend untouched.

#### Caveats

- Nothing calls the comparator yet — `db_reader` (step 6) and `compare_router` (step 7) are the
  next session. `compare_run` is the contract they should use.
- The three-way lanes have only ever been exercised against **synthetic** baseline rows. The
  first real `both`-mode run is what will show whether the app blends the two ingests (§18.8.3),
  and the §18.9 assertion `skipped` paths only trigger on a real fetch.
- `tranche_count_accuracy` attributes an unmatched actual row to a deal by its `deal_id` (or, if
  absent, by ticker). With one deal per run that is exact; with several deals per run and no
  `deal_id`, an invented row is charged to the first deal sharing its ticker.
- `compare.date_tolerance_days` is honoured per column, and the `maturity_date` ±3 tolerance also
  applies inside match key 3.

#### Corrections from the S3 review pass

An independent probe modelled the case the S3 suite did not — a **perfect but faithful agent**,
one that extracts exactly what the email printed and nothing more. Expected rows are projected
from the *payload*, so they hold fields no template renders; that probe found two defects, both
fixed and re-verified.

1. **`bond_colon` could not be matched at all.** Every matching key in §18.7 is either
   identifier-based (key 1) or ticker-rooted (keys 2–4), and that template printed neither — so a
   faithfully-extracted deal had nothing to join on. All rows came back unmatched and the format
   scored **0%** regardless of agent quality. Fixed by adding an `Issuer: <name> ( <ticker> )`
   line to `_bond_colon` (realistic for broker mail, and what the other four formats already do);
   `rendered_fields` for that format goes 13 → 14 and matching now succeeds. Spec §18.7 gained a
   warning that any new format must render the ticker or an identifier, and §17.3's format table
   was updated.
2. **`tier1_pass_rate` was not gated on `in_email`.** It counted Tier-1 columns the template never
   printed, so a perfect agent scored **30%** on `bond_stacked` and the metric was unusable on the
   leaner formats. Now gated exactly like `extraction_accuracy`, per the §18.6 premise that an
   un-rendered field cannot be charged to the agent — un-rendered fields show up in
   `coverage_accuracy` instead. Spec §18.8 updated.

After both fixes, a perfect-but-faithful agent scores `extraction_accuracy` 1.0 and
`tier1_pass_rate` 1.0 on all five formats, while `coverage_accuracy` separates them honestly
(`bond_stacked` 47.8%, `bond_inline` 47.1%, `bond_colon` 31.0%) — that spread is the
template-improvement backlog. Identical-copy still scores 100%; three-way with a `BBG`-stamped
baseline still yields zero `AGENT_BETTER` on the lane columns. S1's 50-assertion store suite and
the S2 projection probe both re-run clean; `import comparators, main` passes.

**Directed for session S4** (answering S3's open question): `tranche_count_accuracy` must not
charge an arbitrary deal. Because unique-per-run tickers are shared by *every* deal in a run
(D8), the ticker fallback is ambiguous for any multi-deal run, not just an edge case. Attribute
by `deal_id`; failing that, by the deal of unambiguously matched sibling rows; otherwise count
the row as `unattributed_actual` in the scores and leave it out of `tranche_count_accuracy` —
it is already reflected in `row_precision`. *(Implemented in S4 and confirmed in review: a
single-deal run charges the invented row, because there the ticker is unambiguous; a multi-deal
run sharing one run ticker leaves it `unattributed_actual = 1` with `tranche_count_accuracy`
untouched, while `row_precision` still reflects it.)*

#### Notes from the S4 review pass

No defects found. Two gaps in the reported verification were closed independently, and one
dependency question settled:

1. **Export → import round-trip was not in S4's verified list.** Confirmed here end to end:
   `expected_store.export_csv` output for all four tables feeds straight back through
   `POST /runs/{id}/import`, each file's table correctly inferred from its header row alone,
   identical row counts, zero unknown-column warnings, and a subsequent compare pass scoring
   **1.0 on all eight scores** over 361 comparable fields. The CSV fallback route (step 11) is
   therefore already exercised.
2. **Compare idempotency confirmed:** a repeated pass for the same `(run, llm_run)` leaves one
   `util_comparison` row with 361 findings — not two — while a *different* `llm_run_id` correctly
   creates its own pass. Note the `/compare` response returns a findings **count**, not the list
   (the list comes from `/diff`), which keeps the response small.
3. **`httpx` is now declared** in `requirements.txt`, marked test-only. It is needed by
   `fastapi.testclient`; leaving it installed-but-undeclared meant a fresh `Setup.bat` would
   produce an environment where a later session silently skips those tests and reports a false
   pass. (A duplicate `psycopg[binary]` line introduced while editing was removed.)

The `db_reader` SQL rails were read directly rather than taken on trust: every value is bound,
every identifier passes an anchored `^[A-Za-z_][A-Za-z0-9_$]*$` whitelist, table names resolve
through `MIRROR_TABLES`, the chunked `IN (...)` can never be built empty (the chunk loop does not
execute for zero keys), the transaction is `READ ONLY` with a statement timeout, and the file
contains no write statement. **A live fetch remains unverified** — it needs the user's
credentials and a real `both`-mode run, exactly as the status row records.

One behaviour worth knowing for later sessions: `compare_router._envs()` re-resolves the store
path from `compare.store_path` on **every request**, so a programmatic `set_store_path()` is
overridden. Config is authoritative, which is right; a test must point config at its temp store. **Done in S4** — see below.

### Email Comparison — session S4: steps 6–7

The harness becomes usable end to end: the rows the pipeline actually produced can now be pulled
from the app database (or imported as CSV), scored against the expectations captured in S2, and
the result persisted, diffed and calibrated over HTTP. Nothing about Bonds/Loans/Interest/TIG/CSV
Upload changed; the only edits to existing modules are additive.

#### Step 6 — `backend/db_reader.py` (new, ~430 lines)

Read-only fetch of the four application tables, filtered by ticker + time window and split into
lanes by datasource.

- **Safety rails, all verifiable by reading the file**: no `INSERT` / `UPDATE` / `DELETE` /
  `CREATE` / `ALTER` anywhere; every transaction opens with `SET TRANSACTION READ ONLY` and
  `SET LOCAL statement_timeout` (15s default); a per-table row cap (5,000) is fetched as
  `cap + 1` so truncation is *detected and reported* rather than silently returned; identifiers
  come from the schema exports and a `^[A-Za-z_][A-Za-z0-9_$]*$` check, and every value — ticker,
  window bounds, issuance keys, the cap itself — is a bound parameter. `build_select` is pure,
  so all of that is unit-testable with no driver and no database.
- **Time column per table**, since they do not share one: `insert_time` (`issuance_data`),
  `update_time` (`issuance`), `deal_created_on` (`issuance_deal`). `t_issuance_security` has
  neither a timestamp nor an issuer, so it is fetched by its parent `issuance_key` — which is
  also how it is attributed to a lane (§18.8.3).
- **Rows whose time column is NULL are kept.** The ticker is the primary filter (§18.7) and a
  unique-per-run ticker makes it near-conclusive; dropping a row because the app left a timestamp
  empty would lose evidence.
- **Lane split delegates to `comparators.lane_of`** rather than re-deriving §18.8.3. A row that
  belongs to neither lane (a `DRB` feed row, a null datasource) is reported as unassigned, never
  guessed at. Blend detection restricts baseline scoring to `issuance_data` and drops the
  baseline rows of the other three, naming them in the warning.
- **Lazy `psycopg` import**, with `availability(env)` answering *can this be queried, and if not
  why* for the Fetch button. Configuration problems are reported ahead of the missing driver —
  they are what the operator has to fix either way.
- `psycopg[binary]>=3.1` added to `backend/requirements.txt`; the `db` block (§18.10) added to
  every environment in `environments.json`, disabled and blank.

#### Step 7 — `backend/routers/compare_router.py` (new, ~600 lines)

All fourteen `/api/compare` endpoints of §18.11, plus `GET /db` and `POST /db/test` (panel 4 and
the Settings **Test connection** button have nowhere else to ask). The maps are loaded **once per
request** via `effective_map()` and passed to the comparator as `maps=`; the `compare` block of
`environments.json` goes in as `config=`; the union of `util_email.rendered_fields` as
`rendered_fields=`. Nothing coerces a value on the way in — the comparator absorbs `'True'` vs
`'true'`, `1000.00000` vs `1000` and epoch-ms vs ISO, so coercion here would only lose
information.

- **`/fetch` never 500s.** Driver missing, `db.enabled` false or host unreachable all answer
  `200 {"ok": false, "reason": …, "fallback": "import"}`.
- **CSV import infers the table from the header**, scoring on the columns unique to each of the
  four (`issuance` and `issuance_data` share 133, so plain overlap is not enough); a `tables=`
  form field overrides it. Unknown columns are ignored with a warning, matched
  case-insensitively, and `NULL` / empty become SQL NULL. Every row is projected onto
  `expected_store.app_columns(table)` before insert, because `insert_rows` rejects unknown
  columns by design.
- **Both fetch and import replace their lane** for the run, so re-fetching does not duplicate.
- **`/compare` is idempotent on `(util_run_id, llm_run_id, against)`** — a re-run replaces the
  pass and its findings. Passes under different `llm_run_id`s coexist, which is what the §18.12
  leaderboard needs.
- **Calibration `accept_baseline` writes two overrides when the column is not already `vocab`**
  (the vocab entry plus a `normalizer` override), because the vocab map is only consulted by the
  `vocab` normalizer — the entry alone would be inert and the finding would resurface unchanged.
  A null baseline writes `expect_null` instead. `keep_expectation` records a suspected app defect
  and writes nothing. Both resolve every open finding for that `table.column` + value pair.

#### Supporting edits

**`backend/expected_store.py`** — the accessors S1 deferred to this step: `add_comparison` (with
its findings, replacing the previous pass), `list_comparisons` / `get_comparison` /
`latest_comparison`, `list_findings` / `get_finding` / `resolve_findings`, `add_llm_run` /
`list_llm_runs`, plus `delete_rows(table, run, source)` and `row_counts(run)` — the runs list
needs a count per lane per run, and counting through `query_rows` would fetch every column of
every row. `util_comparison_field` gained `normalizer` and `note` columns, with a `_migrate()`
step in `init_store()` that `ALTER`s them into a store an earlier session created (verified: rows
survive, and it is idempotent).

**`backend/comparators.py`** — the S3 directive above. `_tranche_counts` now returns
`(deals, right, unattributed)` and attribution is conservative: by `deal_id` when exactly one deal
owns it, else by ticker when exactly one deal claims it, else `unattributed_actual` in the scores
and out of `tranche_count_accuracy`.

**`backend/main.py`** — router registered at `/api/compare`, like the others.

#### Verification

**149-assertion suite** (scratchpad, not committed), green on three consecutive runs against
freshly generated random deals:

- **db_reader without a driver**: missing `db` block reads as disabled with defaults · `db.enabled
  false`, unconfigured host, non-identifier schema and missing `psycopg` each produce their own
  reason · `fetch_rows` raises `DbUnavailable` (never a 500) and refuses a run with no ticker.
- **SQL**: `SELECT`-only, schema + prefix quoted, every column of the export selected, ticker
  upper-cased into a *bound* parameter and absent from the SQL text, window bound, `LIMIT cap+1`,
  placeholder count equal to parameter count, NULL timestamps kept, securities fetched by parent
  key with no ticker/time predicate, an injected schema name refused with `ValueError`, and the
  file itself free of any write statement.
- **Lane split**: `issuance_data` by `datasource_id`, `issuance` by `issuance_created_by`,
  `issuance_deal` by `deal_id_datasource`, securities through their parent `issuance_key`, a
  `DRB` row and an orphan security reported as unassigned · a shared `deal_id` detected as a
  blend, baseline restricted to `issuance_data`, the other three baselines dropped and named,
  actual untouched.
- **Endpoints**: all fourteen respond; unknown run / table / lane / override / finding / action
  return 404 or 400 rather than a stack trace.
- **Import a copy of the expected rows as `actual` → 100%** (the §18.16 verification): every one
  of the eight scores 1.0 over 361 comparable fields, zero `MISMATCH` / `MISSING` /
  `AGENT_ERROR`; the four files route to the right tables from their headers alone (filenames
  scrambled); re-import replaces rather than duplicates; an added bogus column is ignored with a
  warning and the rest of the row still lands.
- **Corrupted copies move the score the right way**: a Tier-1 corruption → exactly one `MISMATCH`
  and `tier1_pass_rate` below 1.0 with the denominator unchanged · a nulled Tier-2 field → exactly
  one `MISSING` · a populated tier-`x` column → `EXTRA` that does not reach `comparable_fields` ·
  a missing tranche → `row_recall` < 1 and `tranche_count_accuracy` 0 · an invented tranche →
  `row_precision` < 1, attributed to its deal.
- **Persistence**: the pass and >300 findings are stored with the `util_comparison_field` shape;
  re-running leaves one pass and the same findings; a different `llm_run_id` adds a second.
- **Three-way + calibration**: a baseline agreeing with a corrupted actual yields `CALIBRATION`,
  excluded from the score (still 1.0) · the queue groups by `table.column` with an occurrence
  count · `accept_baseline` writes the vocab **and** normalizer overrides, resolves every open
  finding for the column, and a re-score turns the verdict into a match · `GET /map` shows the
  entry `overridden` · `DELETE /map/override` reverts it, and a fresh pass re-raises the finding ·
  `keep_expectation` writes no override.
- **llm_run / export / delete**: metadata round-trips and can be referenced by a pass · JSON and
  CSV export for a lane and for the diff, `Content-Disposition` set, `format=csv` without a table
  rejected · `DELETE /runs/{id}` cascades to rows, emails and comparisons and leaves other runs
  alone.
- **Directed S3 fix, separately**: a clean two-deal run scores 1.0 with 0 unattributed · an
  invented row *with* a `deal_id` is charged to that deal · the same row *without* one, in a
  two-deal run sharing a ticker, is `unattributed_actual = 1` with `tranche_count_accuracy`
  untouched and the row still visible in `row_precision` · a single-deal run keeps the old ticker
  attribution.
- **Regressions**: a `dry_run` + `both` Bonds run still captures both deals into the store with
  the right row counts and a green summary; `effective_map` still resolves 391 fields;
  `environments.json` parses with the `compare` block intact and a `db` block on every
  environment; the store migration keeps existing rows.

`python -c "import main, db_reader, comparators, expected_store; from routers import
compare_router"` passes from `backend/`, and all fourteen endpoints appear in the OpenAPI schema.
Frontend untouched (step 8).

#### Caveats / open items

- **No live database has been queried.** No environment has DB credentials and `psycopg` is not
  installed, so the *degradation* path is what was verified, not a real fetch. §18.16's step-6
  verification — both lanes present on `t_issuance_data` after a `both`-mode run, and whether the
  app associates the two ingests or dedupes identifiers (§18.8.3) — **remains open** and is the
  first thing to do once credentials exist. The lane split, blend detection and SQL are all
  exercised against synthetic rows.
- `httpx` was installed into the venv so `fastapi.testclient` could drive the endpoints. It is a
  test-only dependency and was deliberately **not** added to `requirements.txt`.
- The calibration endpoint writes overrides but does not re-score; the caller re-POSTs
  `/compare`. Wiring that into a button, and the queue UI itself, is step 9.
- `POST /runs/{id}/import` accepts `lane=expected` as well as `actual` / `baseline`. That is the
  offline round-trip of step 11, not a capture path — it will overwrite a captured expectation if
  someone points it there.
- The compare pass is Bonds-only in practice: `asset_class` flows through every endpoint, but
  `field_map.csv` only has bonds rows.

### Email Comparison — session S5: step 8

The harness gets its face. Everything the previous four sessions built is now reachable without
curl: capture runs list, score, diff, import and fetch from one tab, and the Bonds tab finally
exposes the two capture toggles that had been API-only since S2.

#### `frontend/src/tabs/EmailCompareTab.jsx` (new, ~560 lines)

Panels 1–4 of §18.15, Bonds only, behind an asset-class segmented control (Loans / ABS / Munis
render disabled with a *not yet available* tooltip). The selection, the chosen table, the diff
filters and the import lane persist to `localStorage` under `pbi.compare`.

- **Panel 1 — capture runs.** Full-width table: time · env · ticker · format · deals/tranches ·
  expected rows · send status · actual (with a `+Nb` suffix when a baseline lane is present) ·
  latest extraction accuracy. Clicking a run opens it and **collapses the list** so the detail has
  the height; the panel header toggles it back. An empty store renders "no capture runs yet" with
  the instruction to turn Email + Capture expectations on — not an error, which is what a
  never-used store would otherwise look like.
- **Panel 2 — run detail.** Score header in the `LiveOutput` stats-strip treatment: extraction
  accuracy as the headline in sky, coverage and Tier-1 pass beside it, then a chip row (field,
  weighted, recall, precision, tranche count, comparable fields, fields in email, `unattributed`
  when non-zero, a `three-way` marker), a verdict-count row in the §18.15 colours, and the seven
  relational assertions as pass/fail/**skipped** chips carrying their detail and failures in the
  title. Row actions sit in the header. Below: table sub-tabs showing `expected/actual` row counts,
  and the matched pairs with their `exact`/`high`/`low` confidence badge and right/wrong tallies.
- **"0 rows matched" is called out, not scored.** When every expected row of a table is unmatched
  the tab shows a warning that this is a matching/template problem — the format must render the
  ticker or an identifier for any §18.7 key to work — rather than letting a 0% read as an agent
  failure.
- **Panel 3 — field diff.** `field · tier · in email · expected · baseline · actual · verdict`,
  three value columns from v1, defaulting to "problems only", filterable by tier. Values are
  truncated with the full text in the title, nulls render as a faint italic `null`, an
  `AGENT_BETTER` verdict carries a ★, and a finding's `note` (where the comparator names an
  unmapped vocab pair) shows as an ⓘ.
- **Panel 4 — actual / baseline source.** Fetch-from-DB with a window input and the resolved
  read-only target, **disabled with the server's reason printed underneath** when the environment
  cannot be queried; and the CSV drop zone (reusing `.drop-zone` from CSV Upload) taking 1–4
  exports with a lane selector and replace/append. Import warnings — unknown columns, in
  particular — are rendered, not swallowed. A detected blend prints its inline warning.

Two shapes worth knowing for the next session:

1. **The matched-pair list is derived from the persisted findings**, because only `POST /compare`
   returns the `tables` block and a page re-open must look identical to a fresh compare. Every
   finding carries `util_deal_seq` / `util_tranche_seq` / `match_confidence`, so grouping is
   enough; a group with no confidence is an expected row the agent never produced.
2. **Panel 3 filters in the browser.** The tab fetches the selected table's findings unfiltered
   once, so toggling "problems only" is instant and panel 2 still sees the rows a filter would
   hide. `GET …/diff`'s `verdict` / `tier` / `problems_only` parameters are consequently unused by
   the UI.

#### Supporting edits

- **`frontend/src/api.js`** — twelve compare wrappers plus `compareExportUrl`. `_json` now attaches
  `err.status` / `err.detail`, so the tab can tell "no comparison pass yet" (404) from a real
  failure instead of pattern-matching the message. Nothing else reads those fields, so the change
  is additive.
- **`frontend/src/App.jsx`** — `Email Compare` registered between CSV Upload and History.
- **`frontend/src/index.css`** — the compare view, segmented control, collapsible panels, runs
  grid, score strip, verdict/assertion/confidence chips, diff grid and source split. Existing
  tokens only; `.alert-warn` and `.alert-line` are the only additions outside the compare
  namespace.
- **`frontend/src/tabs/SettingsTab.jsx`** — a **Database (read-only)** section per environment
  (enabled · host · port · database · schema · user · password · sslmode · mirror) with a **Test
  connection** button, and a **Comparison** section for the top-level `compare` block (store path,
  ingest window, expected/baseline datasource, tier weights, maturity-date tolerance,
  auto-capture). `compare` now round-trips through Save, and a newly added environment is created
  with a disabled `db` block. Test connection **saves first**, because the probe runs server-side
  against the stored settings.
- **`frontend/src/tabs/BondsTab.jsx`** — **Capture expectations** and **Unique ticker per run**,
  shown when Email is on, defaulting from `compare.auto_capture`, persisted to `localStorage` and
  fed by the prefill chain like every other field. They are sent **only when Email is on** — the
  engine's own default is "capture when email generation is on", and an explicit `true` with email
  off would enable capture for a run that generates no email. Unticking capture hides the ticker
  toggle and forces `unique_ticker` false; leaving unique tickers off shows the §18.7 warning about
  cross-run contamination.

#### Verification

`npm run build` passes (44 modules, 215 kB). Beyond that the tab was **driven against a live
backend**, not just compiled: two real capture runs were generated through `bonds_engine`
(`dry_run` + `email_mode=both`, capture on) into the default store, their expected rows exported
through `GET /export`, one copy re-imported clean as `actual` and one corrupted copy imported
alongside a `BBG`-stamped baseline, both compared through `POST /compare` — then the real
component was mounted in jsdom with `fetch` rebased at that backend and every panel's DOM read
back. What rendered:

| Panel | Rendered |
|---|---|
| 1 | `Bonds* Loans(disabled) ABS(disabled) Munis(disabled)`, `store 376 kB`, and two rows — `21:15 │ TRP - QA - Automation │ UC4RYL │ bond_stacked │ 1/3 │ 10 │ sent │ 10 +3b │ 99.3%` and `… CA8WFB │ bond_inline │ 2/3 │ 11 │ sent │ 11 │ 100.0%` |
| 2 | `UC4RYL · bond_stacked · 21:15 · 24 fields rendered`; extraction **99.3%** / coverage 99.3% / tier-1 pass **90.0%**; chips `field 99.4% · weighted 99.2% · recall 100% · precision 100% · tranche count 100% · compared 348 · in email 140 · three-way`; verdicts `match 346 · agent error 1 · missing 1 · calibration 1 · extra 1`; 3 pass / 4 skipped assertion chips; sub-tabs `deal 1/1 · data 3/3 · issuance 3/3 · security 3/3`; three `exact` pairs with 53/1, 53/1, 53/0 |
| 3 | `issuance_data · 4 of 163` — `issuer_name │ 1 │ yes │ ZYRARASOLUTIONS PARTNERS │ ZYRARASOLUTIONS PARTNERS │ WRONG ISSUER PLC │ agent error` (v-wrong), `bnd_bank … │ missing` (v-missing), `exch_names │ LSE │ Luxembourg SE │ Luxembourg SE │ calibration` (v-calibration), `tranche_name │ x │ … │ extra` (v-extra). Unticking "problems only" → 163 of 163; tier=1 → 48 rows, all tier 1 |
| 4 | `expected 10 · actual 10 · baseline 3`; Fetch button **disabled** with *"db.enabled is false for this environment — enable it in Settings."* underneath and `— not configured —` as the target; drop zone and both selectors present |
| 2/3 again | Switching to the clean run: 100.0% / 100.0% / 100.0%, `match 372`, diff shows *"No problems in this table — every compared field matched."*, three `exact` pairs at 54/0 |

Settings rendered both new sections with every field, the sslmode list, and **Test connection**
returning the live reason (`db.enabled is false…`). Bonds was exercised in four scenarios with a
fresh mount each (the run hook stays busy after a start, so re-clicking the same instance silently
re-reports the first payload — an early version of the harness was fooled by exactly that):

| Scenario | Params sent |
|---|---|
| email off | `email_mode=off`, both keys **absent** |
| email on, defaults | `capture_expected=true`, `unique_ticker=true` |
| email on, capture off | `capture_expected=false`, `unique_ticker=false`, ticker row hidden |
| email on, unique ticker off | `capture_expected=true`, `unique_ticker=false` + the §18.7 warning |

Backend untouched and still importing with all 14 compare paths registered.

#### Caveats / open items

- **The Fetch-from-DB success path has never run.** No environment has credentials and `psycopg`
  is not installed, so only the disabled state is verified — as in S4.
- **The jsdom walk-through is not a screenshot.** It proves the data, the DOM, the filters and the
  handlers; it says nothing about pixels. Someone should look at the tab in a browser before this
  is called finished.
- **The default store now contains two demo capture runs** (`UC4RYL`, `CA8WFB`) with imported
  actual/baseline lanes and comparison passes, left in deliberately so the tab has something to
  show. Delete them from panel 1 when they stop being useful.
- **Generating them sent three broker emails** to the configured Outlook recipient
  (`email.recipient` in `environments.json`) — `email_mode=both` sends even under `dry_run`, which
  suppresses only the API POST. Use an empty recipient when generating test data.
- A backend instance predating S4 was running on :8000 during this session, so the verification
  backend ran on :8010. The app must be restarted to serve `/api/compare` — an old instance answers
  404 and the tab will show its error banner.
- `jsdom` was installed with `--no-save` for the walk-through; `package.json` is unchanged.

### Email Comparison — session S6: step 9

The calibration loop closes. Panel 4b turns every `CALIBRATION` / `INVESTIGATE` verdict into a
two-button decision, and the overrides it writes are visible and reversible in the same tab, so
accepting the application's value is never a one-way door. The backend was in scope only for a
defect — one was found, see below; Bonds/Loans/Interest/TIG/CSV Upload are untouched.

#### `frontend/src/components/compare/` (new)

Panel 4b is cross-run, so it does not belong inside `EmailCompareTab`'s selected-run branch. The
tab keeps panels 1–4; the new panels sit at the top level of the compare view, collapsed by
default, in their own components.

- **`CalibrationPanel.jsx` (~330 lines)** — the queue. `GET /calibration` already groups by
  `table.column` (occurrences, verdict counts, run count, ≤5 examples), so there is no client-side
  grouping. Each **example row** — not the group — carries **Accept baseline** and **Keep
  expectation**, because a group can hold several expected/baseline pairs (`exch_names` held both
  `LSE` and `MUNICH`) and a resolution closes only the matching pair. Clicking either opens an
  inline form with an optional reason that states what is about to happen: the value that replaces
  the expectation, that **two** overrides are written on a non-`vocab` column and why the vocab
  entry alone would be inert, and that one click closes *up to n findings in this group*. The
  response's `findings_resolved` is then reported as the exact number. A group whose examples the
  backend truncated at five says so.
- **`OverridesPanel.jsx` (~230 lines)** — the reverse gear. `GET /map`'s override rows grouped by
  `(table, column, source run, active)` — the unit *Accept baseline* actually wrote — with every
  member listed (`kind`, `from → to`, why it exists) and one **`Revert (2)`** that deletes both,
  because a vocab entry without the vocab normalizer is inert and a vocab normalizer without the
  entry passes the value straight through. Reverted rows are kept, dimmed, behind a **show
  reverted** toggle: the audit trail is the point.
- **`shared.js`** — the verdict classes and formatters `EmailCompareTab` had inline, plus
  `rescoreRuns(assetClass, ids)`. Re-scoring is `POST /compare` per run (idempotent on
  `(run, llm_run, against)`, so it replaces rather than stacks) and reports each run as
  `extraction before → after · compared · calibration · investigate` — a failed run gets its own
  line instead of aborting the rest.

**Nothing re-scores by itself**, per §18.8.2. Both panels *offer* it: the affected runs (the
group's examples, or the override's source run) and *Re-score all*, with the truncation stated
wherever a group reaches further than the examples it shows.

#### Supporting edits

- **`frontend/src/api.js`** — `getCalibrationQueue` and `resolveCalibration`.
- **`frontend/src/tabs/EmailCompareTab.jsx`** — mounts both panels; drops its local copies of the
  formatters; adds a `passToken` bumped by anything that rewrites findings or the effective map (a
  compare pass, a run delete, a child's resolution or revert) which both panels reload on, and an
  `onCrossRunChange` that refreshes the runs list, the open run and its diff — otherwise panel 2
  would keep showing the score the calibration just changed.
- **`frontend/src/App.jsx`** — a count badge on the **Email Compare** tab label while the queue is
  non-empty (§18.15). Read once at startup, then pushed live by the tab as findings resolve, so a
  wrong expectation is visible from whichever tool you are working in.
- **`frontend/src/index.css`** — `.cal-*` / `.ovr-*` / `.tab-badge`, existing tokens only.
  `CALIBRATION` keeps its sky treatment: it is an action, not a failure.

#### Backend defect found and fixed

**`keep_expectation` did not survive a re-score.** A compare pass replaces its findings, and the
resolution was stored on the finding row — so the pass wiped it and the "suspected app defect" came
straight back into the queue, one click after being recorded. Step 9 makes that immediately visible
because the UI offers the re-score right there. Fixed in **`backend/expected_store.py`**:
`add_comparison` now snapshots recorded decisions *before* replacing a pass (`_collect_decisions`)
and replays them onto the new findings (`_apply_decisions`), matched on
`(table, column, expected, baseline)` across the store and restricted to `CALIBRATION` /
`INVESTIGATE` verdicts — so a defect recorded on one run stays recorded for the next run that hits
it, and an `AGENT_ERROR` can never inherit a decision. **Only `keep_expectation` is replayed**:
`accept_baseline` draws its durability from the override, and a finding that comes back is the queue
correctly reporting that the override did not work — which is also what makes the revert path
observable. `RESOLUTION_KEEP` / `RESOLUTION_ACCEPT` are now constants in `expected_store`, used by
`compare_router` when it composes the resolution string, so the prefix contract lives in code rather
than being implied. Recorded in spec §18.8.2 (*Decision durability, fixed in S6*).

#### Verification

The §18.16 round trip, driven end to end against the real store through the app's own backend on
:8000 — which had to be restarted first: the instance still running predated S4 and answered 404 on
`/api/compare`, exactly as S5 warned.

**Inducing the findings.** `CA8WFB`'s expected rows were exported per table, `exch_names` and
`issuer_country` edited, and the edited copies imported into **both** the `actual` and `baseline`
lanes (`exch_names` identical in both → `CALIBRATION`; `issuer_country` different in each → all
three differ → `INVESTIGATE`). After re-comparing, the queue held **15 open findings in 5 groups**,
including one group spanning two runs and two value pairs — `issuance_data.exch_names` ×4
(`LSE`→`Luxembourg SE` in both runs, plus `MUNICH`→`Luxembourg SE` ×2).

**50 DOM + REST assertions, all green** (scratchpad, not committed): the real `App` mounted in
jsdom with `fetch` rebased at :8000, every step driven through the rendered DOM and cross-checked
against the API.

| Step | Observed |
|---|---|
| badge | `Email Compare` tab label reads `15`; panel head `15 open · 5 columns` plus its own badge; panel collapsed by default |
| groups | 5 cards, e.g. `issuance_data.exch_names · tier 2 · ci_text · ×4 occurrences · 2 runs · calibration 4`; an `INVESTIGATE` group in the indigo token; each example row shows `exp / base / act` and the example run's ticker |
| accept | form warns *two* overrides and *up to 4 in this group* → `Accept baseline — issuance_data.exch_names · 2 open findings closed · 2 reversible override(s)`, both listed (`vocab: MUNICH → Luxembourg SE`, `normalizer → vocab`) |
| overrides | `GET /map` shows both active with `decided_by`; the vocab entry marked `overridden`; the queue group drops to ×2 and the tab badge to 13 |
| re-score | `CA8WFB — extraction 100.0% → 100.0% · compared 358 → 360 · calibration 6 → 4 · investigate 8 → 8`; `GET /diff` shows both `MUNICH` rows now `NORMALIZED_MATCH`; the resolved pair does not return |
| revert | one card, two members, `Revert (2)` → both deactivated, kept inactive for the audit trail, vocab entry gone from the effective map |
| re-score | `calibration 4 → 6`, both rows `CALIBRATION` again, queue back to 15 open and the group back to ×4 |
| keep | `Keep expectation — issuance.issuer_country · 1 open finding closed`, *no override written*, and after a re-score it **stays** out of the queue (the defect above: this failed before the fix, and was re-verified after) |

`npm run build` passes (47 modules, 233 kB). `python -c "import main, db_reader, comparators,
expected_store; from routers import compare_router"` passes from `backend/`.

#### Caveats / open items

- **No way to un-keep a decision from the UI.** `keep_expectation` is now durable, and the only way
  back is a store edit (`UPDATE util_comparison_field SET resolved = 0 …`). A *known differences*
  view with an un-keep action — plus the defect-list export §18.8.2 mentions — is worth a later
  step; it was not in step 9's scope.
- **`accept_baseline` closes only its own value pair**, by design, so a column whose group holds
  several pairs needs a click per pair. The UI says so; there is no "accept every pair in this
  group" shortcut.
- **The demo store now carries induced findings.** `CA8WFB`'s `actual` / `baseline` lanes hold the
  edited copies described above, deliberately left so the queue has something to show: 15 open
  findings, 5 groups, no active overrides, no recorded decisions. Re-import a clean copy, or delete
  the run from panel 1, when they stop being useful. `UC4RYL`'s single `exch_names` calibration is
  the original S5 artifact and is untouched.
- **Still not a screenshot.** As in S5, the walk-through proves data, DOM, filters and handlers, not
  pixels; the two new panels should be looked at in a browser.
- The re-score loop is sequential and unbounded — *Re-score all* on a store with dozens of runs
  takes as long as that many compare passes. Fine at today's scale.
- `jsdom` and `esbuild` (both already in `node_modules`) drove the harness; `package.json` is
  unchanged.

### Email Comparison — session S7: steps 10–11

The cost/performance lane and the round trip. The harness can now answer *which configuration should
we ship?* — accuracy against cost and latency per provider/model/prompt/method — show the email a
"miss" was or was not in, and export every lane in a shape that imports straight back. Steps 1–11
are complete; the two standing caveats are unchanged (no credentialed DB, no visual pass).

#### `frontend/src/components/compare/` (four new components)

- **`LlmRunsPanel.jsx` (~430 lines) — panel 5a, per run.** The §18.12 metadata form (provider ·
  model · prompt_version · method · tokens in/out/cached · requests · cost_usd · latency
  total/p50/max · started/finished · notes · scope) and a **paste JSON** tab, both posting the same
  `POST /runs/{id}/llm_run`; recorded runs list with their tokens/cost/latency and whether a pass
  exists under each. Blank fields are **omitted**, not sent as `''` — the store is all TEXT, and an
  empty string would make "not measured" read as zero. Provider and model are required, because they
  are what name a configuration. **Use for compare** selects the `llm_run_id` the next pass is
  scored under.
- **`LeaderboardPanel.jsx` (~300 lines) — panel 5b, cross-run, top level.** One row per
  `util_llm_run`, joined to the pass recorded under its id: provider · model · prompt · method · run
  · accuracy · tier-1 · cost · tokens · p50 · max, **every column a sort button** (nulls always
  last), best row highlighted, CSV export, and a **group by configuration** toggle that collapses
  rows sharing all four axes (means for accuracy/tier-1/p50, sums for cost/tokens, worst case for
  max — stated in the panel, since a mean is not a pooled score). Clicking a row's ticker opens that
  capture run.
- **`EmailArtifactPanel.jsx` (~180 lines) — panel 6, per run.** Subject, send status and the stored
  HTML in a **sandboxed iframe** (`srcDoc`, `sandbox=""`, `referrerPolicy="no-referrer"`), a
  **source** view, **Copy HTML**, and the email's `rendered_fields` as chips beside it. The chips are
  cross-checked against the loaded diff table: green when the comparator marked the column
  `in_email`, rose when it did not, plain when the column belongs to one of the other three tables —
  with a line saying how many that is, because `rendered_fields` is per email while `in_email` is
  scored on the run's union (§18.6).
- **`ExportPanel.jsx` (~150 lines) — per run.** Lane × format × table over `GET /export`, with the
  `table` selector disabled exactly when the endpoint ignores it, per-table row counts, `Copy URL`,
  and one link per table for a whole-lane export. It replaces the `<table>.csv` link S5 put in the
  panel-2 header; `Export diff` stays there.

#### Supporting edits

- **`frontend/src/api.js`** — `addLlmRun(runId, body)`.
- **`frontend/src/components/compare/shared.js`** — `num` / `fmtNum` / `fmtCost` / `fmtMs` /
  `llmConfig`, the client-side `toCsv` + `downloadText`, and **`loadLeaderboard`**, which composes
  the board from `GET /runs` + `GET /runs/{id}` (each run detail already carries `llm_runs` *and*
  `comparisons` with their `llm_run_id`). No leaderboard endpoint was added; it reads at most the 60
  most recent capture runs and reports when it truncates.
- **`frontend/src/tabs/EmailCompareTab.jsx`** — mounts panels 5, 6 and export inside the selected-run
  branch and the leaderboard at the top level; adds `selectedLlm` and a `boardToken`. The important
  wiring: a run scored under two configurations has **two coexisting passes**, so the tab pins the
  diff to a `comparison_id` instead of letting `latest_comparison` (newest by timestamp) decide.
  Selecting a configuration switches panels 2–3 to its pass, the panel-2 subtitle names it, and
  comparing with nothing selected says in the notice that the pass cannot reach the leaderboard.
- **`frontend/src/components/compare/OverridesPanel.jsx`** — CSV / JSON export of the override set
  (§18.8.2), written in the browser in `util_map_override` column order including reverted rows and
  their `active` flag. There is no server export lane for overrides.
- **`frontend/src/index.css`** — `.llm-*`, `.lb-*`, `.mail-*`, `.exp-*`, `.btn.disabled`; existing
  tokens only.

#### Two backend defects found by the verification, and fixed

1. **A header-only CSV could not identify its table** (`routers/compare_router.py`). `POST /import`
   read the header from the first *data* row, so a lane that is empty for one table — which exports
   as a perfectly valid header-only file — came back *"could not tell which table this is"*. Step 11
   walks into this immediately: `UC4RYL`'s baseline lane only has `issuance_data` rows, so three of
   its four export files are headers only. It now reads `DictReader.fieldnames`, and a whole-lane
   export re-imports with **zero warnings**.
2. **The calibration queue double-counted** (same file). A run scored under two configurations has
   two passes over the *same* three lanes, so each expectation gap was reported once per pass: the
   queue and the tab badge jumped from 15 to 30 the moment step 10's second pass existed — a number
   that has nothing to do with the expectation. `GET /calibration` now collapses findings identical
   on `(run, table, column, verdict, expected, baseline, actual, deal_seq, tranche_seq)` and reports
   how many it suppressed as `duplicate_findings`. Nothing is hidden: a genuinely different finding
   differs in at least one of those, and resolving already closes every matching pair across the
   store, so the retained `finding_id` stands for its duplicates. Recorded in spec §18.11 (*Two fixes
   in S7*).

Both were caught by the harnesses, not by reading the code — the second only because the DOM
walk-through asserts the badge count that S6 established.

#### Verification

Driven end to end against the app's own backend on `:8000` (restarted twice, since the fixes above
are server-side), against the real store — the two demo capture runs with their induced findings.

**42 REST assertions, all green** (`scratchpad/s7_rest.py`):

| Step | Observed |
|---|---|
| llm_run | two configurations recorded — `anthropic · claude-sonnet-4-5 · v3 · single_pass` on `UC4RYL`, `openai · gpt-4.1 · v2 · split_body` on `CA8WFB`; `tokens_in=18420`, `cost=0.0821`, `p50=4300` round-trip as numbers; an unsupplied field comes back NULL; re-pushing the same `llm_run_id` **replaces** the row |
| compare | each run scored under its own id (`0.9929` / `1.0000`); `UC4RYL` then holds **two passes** — one under `llmA`, the S5 one under no id — and re-running the same triple replaces rather than stacks |
| leaderboard | two rows differing on **every** axis (provider, model, prompt, method, run, accuracy, tier-1, cost, tokens, p50, max); each column sorts to a different first row |
| panel 6 data | 4157 chars of HTML, 24 rendered fields; **21 of the 24 exist on `issuance_data` and all 21 are marked `in_email`**; nothing is marked `in_email` that no email rendered |
| export | JSON of each lane returns all four tables (`expected` 1/3/3/3, `baseline` 0/3/0/0); CSV per lane per table; diff JSON and CSV both 350 findings; a lane CSV without `table` is refused 400 |
| round trip | the `actual` and the `baseline` exports re-imported **warning-free**, every table identified from its header, and the re-score is identical — extraction `0.9929 → 0.9929`, no score and no verdict count changed; row counts before = after |

**53 DOM assertions, all green** (`scratchpad/s7_dom.mjs`): the real `EmailCompareTab` mounted in
jsdom with `fetch` rebased at `:8000` and every step driven through the rendered DOM.

| Panel | Rendered |
|---|---|
| 5 | `1 recorded · 1 scored`; the row reads `anthropic claude-sonnet-4-5 v3 single_pass 18,420 / 2,140 (+12,000c) 3 $0.0821 4300ms / 6100ms 99.3%`; all 16 form fields present; scope offers *whole run* or the deal-1 email; a nameless entry is refused; a bad paste reports *Not valid JSON*; a good paste imports (`Imported 1 of 1`), is auto-selected, and reads **not yet** rather than 0% |
| 5 → 2 | the panel-2 selector lists `no LLM run` + both configurations; Compare reports *scored under anthropic · claude-haiku-4-5 · v3 · per_section*, panel 2's subtitle says *pass under …*, and switching the selector switches panels 2–3 to the other pass |
| 6 | subject `[EXTERNAL] New Issue — ZYRARASOLUTIONS PARTNERS ( UC4RYL )`; `sandbox=""` iframe with 4346 chars of `srcdoc` carrying the issuer and the ratings, and **no** unsandboxed injection point; 24 field chips, 21 green / 0 rose, `✓ all 21 of these that exist on issuance_data are marked "in email"`; the source view shows the raw markup |
| export | all four lanes offered; `…?format=csv&lane=expected&table=issuance_data`; JSON drops the table and disables the selector; `lane=diff` needs none; four per-table links with counts `1 / 3 / 3 / 3` |
| leaderboard | `3 llm runs · 3 scored · best 100.0% — openai gpt-4.1`; 11 sort buttons; default accuracy-descending; **every column sorts both ways** (this is where the `run` column was found not to sort at all — the ungrouped rows carried `ticker` but not the column's sort key); grouping collapses to configurations and the run column becomes `1 llm run`; clicking a row's ticker opens that capture run |
| 4b | the queue is still `15` and the tab badge count is pushed as before — the dedupe fix restored the S6 number |

`npm run build` passes (51 modules, 268 kB JS / 28 kB CSS). `python -c "import main, db_reader,
comparators, expected_store; from routers import compare_router"` passes from `backend/`.

#### Caveats / open items

- **No way to delete a `util_llm_run` row.** §18.11's endpoint list has no delete for one, so a
  mistyped configuration can only be corrected by re-pushing the same `llm_run_id` (the store
  inserts-or-replaces) or by a store edit. The DOM harness's probe row was removed with
  `scratchpad/s7_clean.py` for exactly this reason.
- **Leaderboard accuracy is per pass, not pooled.** Grouping by configuration takes arithmetic means;
  two runs of very different sizes weigh the same. A pooled score would need the comparator to merge
  passes, which is not in the spec.
- **The board reads one request per capture run** (`GET /runs/{id}`), capped at 60 runs. Fine at
  today's scale; a store with hundreds of runs would want a real endpoint.
- **The demo store now carries the two S7 configurations** and their passes, deliberately, so the
  leaderboard has something to show: `UC4RYL` has two passes (one under `llmA`, the S5 one under no
  id) and `CA8WFB` likewise. Its 15 open calibration findings from S6 are untouched.
- **Still not a screenshot**, as in S5 and S6 — the walk-through proves data, DOM and handlers, not
  pixels. Panels 5, 6 and the export panel should be looked at in a browser.
- `jsdom` and `esbuild` (already in `node_modules`) drove the harness; `package.json` is unchanged.
  The harness bundles through esbuild's `stdin` with `resolveDir` at the frontend root — resolving
  the entry's React separately from the tab's yields two React copies and a null hook dispatcher.

---

## Changes — 2026-08-07

### graphify knowledge graph (tooling, not product behaviour)

Installed [graphify](https://github.com/Graphify-Labs/graphify) (`graphifyy` 0.9.35) to cut the
tokens spent re-discovering this codebase every session. Nothing in the app changed — no backend,
frontend, engine or spec behaviour is affected. `spec.md` is deliberately untouched.

**Install.** `uv tool install graphifyy` — installed as an isolated uv tool, **not** into
`backend/.venv`, so `backend/requirements.txt` and the app's runtime environment are unchanged.
Then `graphify claude install`.

**What it wrote.**

- `CLAUDE.md` — a `## graphify` section (query-before-grep rules), since extended with the
  repo-specific scope notes below.
- `.claude/settings.json` — **new file**, two `PreToolUse` hooks (`Bash|Grep`, `Read|Glob`) that
  inject a reminder to query the graph first.
- `.graphifyignore` — **new**, excludes `frontend/dist/`, nested `__pycache__/`, `.venv/`,
  `history.json`, `expected/`. Needed because `frontend/dist/` is tracked in git and would
  otherwise be indexed as bundled output.
- `.gitignore` — `/graphify-out` added; the graph is generated (~2 MB) and rebuildable.

**The graph.** First build was `graphify extract . --code-only` → 57 files, 734 nodes, 1845 edges,
4.5x benchmark. That was a mistake: `--code-only` skips markdown. Re-running plain **`graphify
update .`** indexed the docs too — **58 files, 972 nodes, 2079 edges, 46 communities, 17.0x
reduction** (~64,800 tokens naive vs ~3,803 per query). Still **0 LLM tokens**: markdown is indexed
*structurally* (heading hierarchy + line numbers, `_origin: ast`), not semantically. `spec.md`
contributes 105 nodes, this file 100, `plan.md` 25, `CLAUDE.md` 8.

**So the planned paid "doc pass" was mostly unnecessary.** A semantic pass would add `INFERRED`
concept edges on top of the structural index; the navigation win — `graphify explain "18.8
Comparison and scoring"` → children + `spec.md:L984` — is already there for free. Deferred.

**Refresh with `graphify update .` after edits.** Do not re-run with `--code-only`.

#### Caveats / open items

- **`graphify affected "X"` returns nothing useful.** Cross-file call edges are unresolved, so
  `affected "compare_run()"` reports "No affected nodes found" despite `compare_router.py` importing
  it. Impact analysis still needs grep. `explain` is accurate but within-file.
- **JSX coverage is thin.** `EmailCompareTab.jsx` yields 7 nodes and the `components/compare/`
  panels 4–8 each, against 30–81 for comparable backend modules. Frontend work gets much less from
  the graph than backend work does.
- **Community labels are partly placeholders** (`Community 3`, …) where no filename or heading
  supplied one. `graphify label .` names them via an LLM backend; not run, to keep the build free.
- **Structural ≠ semantic for docs.** The graph knows §18.8 exists at `spec.md:L984` and what its
  subsections are. It does not know what §18.8 *says* — that still needs reading the section.
- **The hooks are not free.** Every `Bash`/`Grep`/`Read`/`Glob` call now carries an injected
  reminder — a per-tool-call overhead paid against the per-query saving.
- **Query quality is mixed.** Node extraction picks up docstring lines as nodes (e.g. a comment in
  `expected_store.py` surfaced as a node label), so results need reading with judgement rather than
  trusted as a symbol index.
- Three JSON files (`settings.json`, `settings.local.json`, `environments.json`) produced zero
  nodes — a known upstream issue (#1666), harmless here.

### Email Comparison — session S8: steps 12–14 (close-out)

The feature closes. Steps 1–11 were complete; S8 adds the three things their caveats named — a real
browser pass over the tab, the way back out of a `keep_expectation` decision, and a delete for a
`util_llm_run` row — and nothing else. Bonds/Loans/Interest/TIG/CSV Upload are untouched.
Steps 12–14 were **added to §18.16 before they were built**, and their endpoints specced into
§18.11 / §18.8.2 / §18.12 first.

#### Step 12 — the visual pass

Three sessions verified this tab through jsdom: data, DOM and handlers, never pixels. It was walked
in **Edge headless driven over CDP** (a ~90-line scratchpad harness — node 24's built-in
`WebSocket`, no new dependency, nothing added to `package.json`) at **1600×1100 and 1280×900**, with
every panel expanded and screenshotted, plus a detector that reports any element whose `scrollWidth`
exceeds its `clientWidth`.

**The two fixed 11-column grids fit at 1280 without scrolling**, so neither needed the scroll
container this step was braced for. Six defects were found; all six are fixed:

| # | Defect | Fix |
|---|---|---|
| 1 | **Panel 2's header contradicted itself on open.** The subtitle read *pass under anthropic · claude-sonnet-4-5 · v3 · single_pass* while the selector beside it read *no LLM run* — `openRun` fetched the diff with no `comparison_id`, so the backend answered with `latest_comparison` while `selectedLlm` stayed null. Exactly the ambiguity §18.12 warns about, in the one place S7 did not pin. | `openRun` now pins the newest pass explicitly and points the selector at its `llm_run_id`. |
| 2 | `fmtMs` switched to seconds at 10 s, so the leaderboard's p50/max column read `6100ms` above `11.2s` — two units in one sortable column. | Threshold lowered to 1 s: the column reads uniformly `3.9s / 11.2s`. |
| 3 | Panel 6's cross-check subline was `text-faint` on the emerald glow — effectively invisible. | `.mail-check .cal-note` reads `text-secondary`. |
| 4 | `.history-empty`'s 64px padding is right when it is the whole page, but inside a stack of collapsible panels three ~250px voids read as a rendering fault. | 26px inside `.panel-body`, and on the *select a run* placeholder. |
| 5 | Panel 5's **Use for compare** wrapped to two lines once the delete `✕` joined its 116px cell; the tokens cell wrapped `(+12,000c)` under the row. | Last column 116→152px, tokens 128→150px. |
| 6 | `.panel-title` wrapped before the subtitle did — *RUN / DETAIL* at 1280px. | `white-space: nowrap` on the label; the subtitle wraps instead. |

#### Step 13 — panel 4c, known differences (§18.8.2)

`keep_expectation` became durable in S6, which made it the one piece of state in the harness with no
way back: an override reverts from the overrides panel, a pass re-scores, but a recorded defect
stayed recorded until somebody edited the store. §18.8.2's *"exportable as a defect list"* had no
surface either. One view serves both.

- **`GET /decisions`** (`compare_router`) — resolved `keep_expectation` findings grouped by
  `(table, column, expected, baseline)`, the unit a decision is recorded **and replayed** on, with
  the reason parsed back off the resolution string (`_decision_reason`), occurrence and run counts
  and ≤5 examples. The group is the defect; its findings are sightings of it, so one judgement reads
  as one row however many runs and passes have hit it.
- **`POST /decisions/{finding_id}/unkeep`** → `expected_store.unkeep_findings`. Clears the **whole
  pair**, never one sighting: reopening a single finding would leave its siblings closed and
  `_apply_decisions` would resurrect it on the next pass. Only rows whose resolution carries the
  `keep_expectation` prefix are touched, so an `accept_baseline` resolution — whose durability is the
  override — is never disturbed. **No re-score is needed**, and the response says so
  (`rescore_required: false`): the findings already exist and are simply open again.
- **`KnownDifferencesPanel.jsx` (~190 lines)** — top-level and collapsed, beside 4b and the
  overrides, since a defect recorded on one run is a standing fact about every run. Amber badge, not
  sky: this is a fact to hand onward, not an item awaiting a decision. **Un-keep** per group, and a
  **client-side CSV/JSON export** of the defect list, like the override export — there is no server
  export lane for decisions and this flat shape is the whole artifact.
- `expected_store.list_findings` gained `resolved_only` and `resolution_prefix`.

#### Step 14 — deleting an LLM run (§18.12)

`DELETE /runs/{util_run_id}/llm_run/{llm_run_id}`. A `util_llm_run` row is the axis a compare pass is
scored on, so deleting one with passes under it would orphan them into the *no LLM run* bucket and
quietly change what the leaderboard reports. It therefore **refuses with 409** while any pass
references it, naming those passes and their scores, and `?cascade=true` drops them and their
findings too — safe in a way deleting an expectation is not, because a pass is re-derivable by
pressing Compare, but it has to be asked for. The row must belong to the run in the path, so a
mistyped id cannot reach another run's configuration. New store calls: `get_llm_run`,
`delete_llm_run`, `delete_comparison`.

In panel 5 the `✕` sits beside **Use for compare**; the 409 surfaces as the cascade prompt, which is
the only place the affected-pass count is known. A cascade can delete the pass panels 2–3 are
showing, so the tab gained `onLlmRunRemoved` — re-read the run, drop the selection, re-pin whatever
pass is left (or none, which `loadDiff` already reports as *not scored yet*).

#### Verification

Driven against the app's own backend on `:8000` — restarted first, since the instance holding the
port predated the edits and answered 404 on `/api/compare/decisions`, exactly as S5 warned.

**31 REST assertions** (`scratchpad/s8_rest.py`, `s8_llm.py`):

| Step | Observed |
|---|---|
| keep → 4c | a keep on `issuance_data.exch_names` closed 4 findings and wrote no override; `/decisions` shows one group, reason parsed clean, occurrences = findings resolved, the pair and an example run carried |
| queue | 15 → 13 open — 4 raw findings, **2 unique queue entries**, because the queue de-duplicates across a run's two coexisting passes (the S7 fix). Raw and deduped counts are not interchangeable |
| un-keep | reopened all 4, `rescore_required: false`, the queue and the group returned to exactly their pre-keep numbers, the decision disappeared |
| guards | unknown finding → 404; un-keeping an *open* finding → 400 naming the overrides panel as the route for an accepted baseline |
| llm delete | an unscored row deletes; a scored one refuses **409** naming its passes and their scores and hinting at cascade, and survives the refusal; `?cascade=true` removed 1 pass and 350 findings; the other run's passes and configurations untouched; unknown id, another run's id and an unknown run all 404 |

**18 DOM assertions in a real browser** (`scratchpad/walk4.mjs`, 1280×900): panel 2's selector now
agrees with its subtitle; the panel title does not wrap; the p50/max column is one unit; no empty
state exceeds 90px; panel 6's cross-check subline is legible; 4c lists both decisions with their
reasons and de-duplicated run names; un-keeping moved the queue `12 → 14` and the **tab badge with
it, with no compare pass in between**; the delete's cascade prompt states that Compare rebuilds the
pass; the row and its pass vanish and the leaderboard drops to `1 llm run`.

`npm run build` passes (52 modules, 275 kB JS / 28.8 kB CSS). `python -c "import main, db_reader,
comparators, expected_store; from routers import compare_router"` passes from `backend/`.

#### Caveats / open items

- **The live DB fetch is still the one unexercised path.** No environment has a `db` block filled in
  and `psycopg` is not installed, so the §18.8.3 association/dedupe question — *does the app
  associate the two ingests, and is a repeated ISIN/CUSIP deduped?* — remains the last open design
  question in v1. It needs a credentialed environment, not more code.
- **The demo store's queue now reads 12 open, not 15.** Three of S6's induced findings are recorded
  as known differences (`issuance_data.exch_names` ×4 and `issuance.issuer_country` ×2, 2 groups),
  deliberately, so panel 4c has something to show. Un-keep them from the panel to get the 15 back.
  `UC4RYL`'s configuration and both its passes were restored after the delete test — same values,
  a new `llm_run_id`.
- **A cascade delete is not undoable in one click.** The pass comes back by pressing Compare, and the
  expectation and lanes are untouched, but the prompt is the only guard.
- **`accept_baseline` decisions are not in panel 4c**, by design — they are overrides, and the
  overrides panel is their view. 4c is the `keep_expectation` half only.
- The visual pass covered 1280 and 1600. Below ~1100px the panel-2 header and the two 11-column
  grids will want a scroll container; the app is not built for that width today.
- Edge headless and node's built-in `WebSocket` drove the browser harness; `package.json` is
  unchanged and nothing was installed.

#### Close-out review pass (2026-08-07)

All four earlier review probes were re-run as a regression suite across the eleven steps of
backend change since they were written — **S1's 50-assertion store suite, the S2 projection probe,
the S3 scoring probe and the S4 router probe all still pass unchanged**, as does
`npm run build` (52 modules) and the backend import check. All **17** `/api/compare` paths are
registered (14 from S4 + `GET /decisions`, `POST /decisions/{id}/unkeep`,
`DELETE …/llm_run/{id}`). Endpoint smoke against the real store returns sane shapes for `/runs`,
`/db` and `/map`; the store holds 2 capture runs, 3 emails, 2 LLM configurations, 4 comparison
passes, 1,444 findings, 5 calibration groups and 2 known-difference groups.

**One gap none of the eight sessions caught, fixed here:** `backend/expected/` was untracked *and
un-ignored*, so the next `git add -A` would have committed the 676 kB binary demo store (plus its
`-wal`/`-shm` siblings) and then grown it with every capture run. Added to `.gitignore` — the store
is runtime data and `init_store()` rebuilds it. Verified with `git check-ignore`; only
`expected_store.py` and `engines/expected_writer.py` remain tracked, as they should be.

The two open questions S7 raised and S8 did not close are recorded as accepted limitations rather
than defects: there is no server-side leaderboard endpoint (the client fan-out over `GET /runs` +
`GET /runs/{id}` is fine at today's scale, capped at 60 and stated in the UI when truncated), and
grouped leaderboard accuracy is an arithmetic mean rather than pooled by field count — which
matters only if someone quotes a grouped figure as a single model score. Pooling would require the
comparator to merge passes.

---

## Changes — 2026-08-07 (later)

### Email Comparison — simplification agreed (spec §18 step 15, decision D9)

The feature was reviewed by the user against the running app and rejected as too complex:
*"The entire design is a complete mess. I am really left confused and I am not sure how to use it
and it looks very complicated."* Specific objections: the score header was unreadable (three
percentages, eight chips, seven assertion chips), the baseline lane was not understood, and the LLM
runs panel was unwanted — *"The LLM run will happen on a different application it will NOT happen on
the utility."*

Two clarifications worth recording, because they were misunderstandings rather than defects:

- **There is no LLM connectivity in the utility and never was.** Nothing calls a model. The LLM
  runs panel was a manual metadata form (type or paste provider/model/tokens/cost) whose only
  purpose was ranking accuracy against cost on the leaderboard. It traces to the user's own
  agent-testing diagram (*"monitor token spent on different models and methods"*, *"compare
  performance and cost against other runs"*) — but if the agent harness records that already,
  entering it twice is busywork. Cut.
- **Every number in the screenshots was demo data** generated to exercise the panels, including the
  `anthropic · claude-sonnet-4-5 · v3 · single_pass` configuration and the 99.3%. Looking at
  synthetic content in an unfamiliar layout accounted for much of the confusion.

**Decisions taken:** cut the UI *and* remove the now-dead backend (the user chose the deeper cut
over a UI-only strip), and clear the demo store.

**Done in this pass:**

- Demo store deleted (2 capture runs, 3 emails, 2 LLM configurations, 4 passes, 1,444 findings,
  692 kB). `GET /runs` on a machine with no store file at all returns `{"runs": []}` — the S1
  read-degradation fix earning its keep.
- `backend/expected/` added to `.gitignore`. It was untracked *and un-ignored*, so the next
  `git add -A` would have committed the binary store and then grown it with every capture run.
- **Spec §18 rewritten: 865 → 425 lines.** Surviving section numbers are unchanged so this
  changelog still resolves; §18.9 (relational assertions) and §18.12 (LLM runs) are simply absent,
  as are old §18.8.1 (three-way verdicts) and §18.8.2 (the calibration loop). The header lists
  exactly what went. Git history holds the full earlier design.
- §18.14 gained **D9** with the reasoning, and D5 (the baseline lane) is marked withdrawn.
- §18.16 collapsed to one row per session plus **step 15**, the simplification, `☐ Not started`.
- `plan.md`'s design-decision block is marked superseded rather than deleted — each bullet records
  *why* a piece existed, which is what makes it safe to remove.

**Not yet done — step 15 is the build.** The code still contains everything: the baseline lane,
three-way verdicts, the calibration/override/decision endpoints, LLM run tables and the leaderboard.
Nothing is broken; it is simply more than is wanted.

**The lesson.** Every addition was individually defensible, individually proposed with its
trade-off, and individually agreed — and the sum was still a tool that needed a manual. Asking "is
this piece justified?" eleven times is not the same as asking "is the whole thing usable?" once. An
accuracy harness earns its complexity only when someone can read its output without being taught it.

### Email Comparison — step 15 built: the simplification

Executed the cut agreed above. Nothing that already worked was rewritten: capture, the field map,
the normalizers, row matching and the read-only DB fetch are the same code.

**Removed**

| | |
|---|---|
| Frontend | `CalibrationPanel`, `OverridesPanel`, `KnownDifferencesPanel`, `LlmRunsPanel`, `LeaderboardPanel`, `EmailArtifactPanel`, `ExportPanel`, `shared.js` — the whole `components/compare/` directory. `EmailCompareTab.jsx` rewritten from scratch (794 → 300 lines) as the three panels of §18.15. Tab badge removed from `App.jsx`; nine wrappers removed from `api.js`; the compare CSS block rewritten (594 → 118 lines) |
| Backend | 7 of 17 endpoints (calibration, resolve, decisions, unkeep, map, map override, llm_run add/delete). `util_llm_run` and `util_map_override` tables and every accessor; the decision snapshot/replay; `effective_map`'s override layer. In `comparators`: three-way comparison, `AGENT_ERROR`/`AGENT_BETTER`/`CALIBRATION`/`INVESTIGATE`, `lane_of`, the relational assertions, seven of eight scores. In `db_reader`: lane splitting and blend detection. `tier_weights` and `baseline_datasource` from `environments.json` and the Settings tab |

| | Before | After |
|---|---|---|
| `comparators.py` | 1,249 | 790 |
| `expected_store.py` | 1,104 | 777 |
| `compare_router.py` | 869 | 508 |
| `db_reader.py` | 480 | 440 |
| `index.css` | 1,251 | 775 |
| JS bundle | 275 kB | 204 kB |
| Compare endpoints | 17 | 10 |
| Verdicts | 10 | 4 |
| Scores | 8 | 1 |

**Two fixes that were the point of the exercise**

1. **`ci_text` now flattens formatting.** The user's own example — *"email had coupon frequency as
   Semi annual, the application stores Semi-annual, it is still a success"* — passed only for
   `coupon_frequency`, and only by luck, because `vocab_map.csv` happened to carry the hyphenated
   spelling. Every other text column failed on a hyphen: `Senior Unsecured` vs `Senior-Unsecured`
   was a mismatch. `_loose()` now folds case, hyphens, underscores and whitespace around a slash,
   and the vocab lookup uses the same key. Deliberately **not** folded: all whitespace (so
   `SemiAnnual` stays distinct) and parenthetical qualifiers (so `Actual/Actual (ICMA)` stays
   distinct from `Actual/Actual` — different day-count conventions, and collapsing them would hide
   a real defect). Those are synonyms, not formatting, and belong in `vocab_map.csv`; the mismatch
   note now prints the exact line to add.
2. **`db_reader` bound a datetime to a bigint column.** The app stores epoch-milliseconds in
   `insert_time` / `update_time` / `deal_created_on` (`1785975352312` in its own exports), so
   `BETWEEN` against a timestamp would have failed with a type error on the very first live fetch.
   The window moved out of SQL into Python, where the parser handles epoch-ms, epoch-seconds and
   ISO alike; SQL now filters on ticker alone, which is the selective predicate anyway. Rows whose
   timestamp will not parse are kept rather than silently dropped.

**Two defects introduced during the cut, caught by the verification**

- A regex used to strip the decision-replay logic ate the body of `add_comparison`, so passes and
  findings were silently not persisted and the diff 404'd. Rewritten explicitly.
- The new `compare_row` skipped tier-`x` columns before `compare_field` could report a populated
  one as `extra`. Both found by the end-to-end suite, not by reading the code.

**Verified** — 16 assertions, all passing: a perfect-but-faithful agent scores **100% on all five
bond formats** (`bond_stacked` 140 fields compared / 221 excluded as not-in-email; `bond_colon`
85 / 247); export → import → compare round-trips at 100% with 361 matches; a corrupted field is
the *only* thing flagged while `Junior Subordinated` vs `JUNIOR-SUBORDINATED` and `Annual` vs
`annual` both match; `extra` is reported and does not move the score; a repeated compare replaces
its pass; a missed tranche shows as 9 matched / 1 missing. Only the four verdicts ever appear.
`import main` passes; `npm run build` passes (44 modules).

**Still open, unchanged:** the live DB fetch has never run against a real database. It needs a `db`
block filled in for one environment and `pip install psycopg[binary]` — the queries are written and
their SQL was reviewed against the application's real table names.

### Email Comparison — live database connected (TRP - QA2)

`psycopg[binary]` 3.3.4 installed into `backend\.venv`. The QA2 `db` block now resolves and
**Test connection returns `ok` with all four tables visible**.

| Setting | Value | Note |
|---|---|---|
| host / port | `qa-trowe-pbi2.cddev.genesis.global` : 5432 | PostgreSQL 15.14 |
| database | `postgres` | was blank — the server has exactly one non-template database |
| schema | `pbi` | 24 `%issuance%` tables live there |
| user | `postgres` | |

**The schema files are correct.** The live tables have exactly the column counts the
`*_fields_size.csv` exports claim — `t_issuance` 146, `t_issuance_data` 180, `t_issuance_deal` 53,
`t_issuance_security` 12 — and all four of the utility's generated `SELECT`s ran against the real
database returning those column counts, which also proves every column *name* in the field map
exists. `t_issuance_data` holds 179,185 rows.

**The epoch-ms fix was necessary, not theoretical.** `insert_time` comes back as a Python `int`
(`1778594997339`). The pre-fix query bound a `datetime` to `BETWEEN` against that column and would
have failed with a type error on the first real fetch.

**D1 is contradicted by the live data.** No row anywhere carries `datasource_id = 'LLM'`. The real
values are `BBG` (172,302), `DB` (4,042), `MAN` (2,828), `DRBK` (5), `DRB` (2);
`issuance_created_by` is `BBG`/`MAN`/`DB` and `deal_id_datasource` adds `AUTO`. And
`pbi.t_parsed_email_issuance_data` is **empty (0 rows)** — the email-parsing agent has not written
into this environment yet. `compare.expected_datasource` is therefore set to `""` (no source
filter): left as `LLM` the fetch would have matched the ticker and window and then discarded every
row, reporting nothing found. Once a real agent run lands, read the value it stamps and set it.

**Live end-to-end fetch verified** on ticker `HTSC`: 130 `t_issuance_data` rows matched the ticker,
1 fell inside the 60-minute window, and the utility stored 1 data + 1 issuance + 1 security row
into the `util_` tables — with the 146 out-of-window rows reported as a warning rather than
silently dropped. Sample stored row: `HTSC · HKD · 3 Y · Fixed · BBG`. The test run was deleted
afterwards; the store is empty.

**One fix from that run:** securities were fetched from the *unfiltered* issuance keys, so a ticker
with months of history returned every security it ever had (15 rows for 1 in-window tranche).
They are now reached only through parents that survived the window — 1 for 1.

The 16-assertion suite still passes and `import main` is clean.

## Changes — 2026-08-10

### One ticker per deal, derived from the issuer name (D8 revised)

**Reported:** two emails from one run, for two different issuers (`FINACOVENTURES RESOURCES` and
`BREOTASOLUTIONS INDUSTRIES`), both showing ticker `N2J4QF`. "Each issuer gets a different ticker."

**Cause — not a bug but a wrong model.** The original D8 minted one random 6-character ticker per
*run* (`_gen_run_ticker`) and forced it onto every deal via `force_ticker`, because the ticker was
the run's isolation key for the DB fetch. A ticker identifies an issuer, so sharing one across
issuers was wrong in the emails — and quietly wrong in the harness too: `t_issuance_deal` has no
tranche key, so deal rows match on ticker alone (§18.7 key 4) and `comparators._key_ticker` only
matches when *exactly one* candidate carries the ticker. With several deals under one ticker, only
the first could ever match at deal level; the rest were reported unmatched. `EmailCompareTab` had
grown a warning telling users to "use one deal per run for a clean read" — a workaround for this.

**What changed**

- **The reference ticker is used.** `reference/bonds/issuers.csv` holds 4005 issuers, every one
  with a ticker, and all 4005 are distinct — the data needed to give each issuer its own ticker was
  there all along, and the run ticker was overriding it. The two issuers in the report are in that
  file: `FINACOVENTURES RESOURCES,VRMUS` and `BREOTASOLUTIONS INDUSTRIES,IQHNA`. `_TickerMinter.mint`
  now takes `preferred=` (the CSV's ticker) and uses it whenever it is free.
- `_ticker_stem(name)` is the **fallback** for when there is no reference ticker to take — an empty
  `TICKER` column, a `_gen_company()` name because the CSV is missing, or a reference ticker already
  used by an earlier run. It reads like a ticker rather than a random string: first word's leading
  letter plus its next consonants, then each later word's initial, capped at 5 and topped up from
  the name's letters if a short name (`3M Co` → `MCO`) leaves fewer than 4. `BREOTASOLUTIONS
  INDUSTRIES` → `BRTSI`, `TIGER GLOBAL HOLDINGS PLC` → `TIGEG`.
- `_TickerMinter` guarantees uniqueness — within the run, and against
  `expected_store.known_tickers()` (every ticker any earlier run used), so the cross-run
  contamination D8 originally guarded against still cannot happen. A clash re-spells the stem
  (`FNCVR` → `FNCI` → `FNCN`).
- `_pick_issuer` / `_build_single` / `_build_multi` take the minter instead of `force_ticker`. The
  tranches of one multi-tranche deal still share their deal's ticker — that is one issuer.
- **A run is now scoped by its ticker *set*.** `util_run.ticker` holds it comma-separated, written
  when the run ends (the tickers do not exist until their deals are built); `util_email.ticker`
  already held the per-deal value and now differs per email. `db_reader.split_tickers` normalises
  one/comma-string/list, and `build_select` binds an `IN` list — still an exact, index-usable match
  on `issuer_ticker` (`index_2125_4`), keeping `=` for the single-ticker case.
- `/runs/{id}/fetch` prefers `util_run.ticker`, falling back to the emails' own tickers: that
  covers a run stopped before it wrote the set back, and every run captured before this change.
- UI: the Bonds toggle is now "Unique ticker per deal"; the obsolete "one deal per run" advice in
  `EmailCompareTab` is now scoped to runs that pre-date this change.

**`unique_ticker` kept its meaning as an escape hatch:** on (default) mints per-deal tickers, off
uses the reference CSV's own — which is what the §18.7 warning about cross-run contamination now
refers to.

**Verified by dry-run smoke tests** (no server): a 7-deal run where all 7 tickers came from
`issuers.csv` and all 7 were distinct; the two reported issuers resolving to `VRMUS` / `IQHNA`; both
fallbacks (no reference ticker, and reference ticker already taken) yielding `FNCVR`; the minter
resolving four collisions on one name; `build_select` emitting `IN (%s, %s)` with two bound params
and `= %s` with one. Then two capture runs against a throwaway store: `util_run.ticker` matched the
run's `util_email.ticker` values exactly, and all 8 tickers across the two runs were distinct.
`npm run build` clean.

**The review question that caught this:** the first cut of the fix derived *every* ticker from the
issuer name, which was still overriding `issuers.csv` — the same mistake as the run ticker, just
with a nicer-looking output. Asking where the ticker *should* come from before asking what it
should look like would have got there directly.

**One bug caught by the smoke test, worth remembering:** the `IN` placeholders were first written
`', '.join('%s' * len(tickers))` — `'%s' * 2` is the *string* `'%s%s'`, and joining a string joins
its characters, producing `IN (%, s, %, s)`. It needs `['%s'] * len(tickers)`.

### Uploaded email sets — design agreed, nothing built (spec §19)

Discussion only, no code. The user asked whether the Email Compare tab could work from a **static
sample of real client emails they already hold** (20–50 `.msg` files, mixed broker formats, most
carrying an ISIN or CUSIP) instead of from emails the utility generates: upload them, populate the
`util_` mirror tables, fetch the agent's rows for the same emails, compare. Design written up as
**spec §19** with decisions D10–D15 and an A1–A4 / B1–B4 status table.

**The one thing that decides the shape of the feature.** In §18 the email is downstream of the
truth — the utility invents the deal, so `expected_writer` gets ground truth for free. An uploaded
email inverts that arrow, and nothing in the codebase can turn real broker prose into 145 typed
column values. Of the four routes, a per-format parser dies on "mixed formats" and an LLM would make
the ground truth as fallible as the subject under test (a parser marking a parser). The user has no
sidecar truth file. So the expectation must be **authored by a human in the utility** (D10), and the
feature's real content is making that cheap: blank = "the email didn't say it" and is excluded from
scoring (D12), so effort is proportional to the email and partial labelling still yields a valid
narrower score; prefill from the email text only, never from the fetched rows (D11); vocab dropdowns
so normalization is never in play; and the label exports as an importable CSV (D14) so the effort is
one-time. Everything downstream — `comparators`, the four verdicts, the accuracy figure, the diff,
the export — is reused untouched.

**Phased A/B on purpose.** Phase A (upload · parse · display · fetch, no score) exists to prove
*pairing* before any labelling time is spent. The live fetch is verified, but only against a
**minted** ticker with no history; a real ticker has years of deals in a 179,185-row table, so
ticker + window is not a usable filter and §19.8 replaces it with identifier matching. That is the
untested part.

**Two stale doc facts corrected while reading, both of which had misled the discussion:**

- §18.16 said the live fetch was "unexercised (no credentials)". It was verified end-to-end on
  2026-08-07 (ticker `HTSC`, recorded in this file) and both TRP - QA2 and TRP - QA - Automation have
  filled-in `db` blocks today. Row now states the verification.
- **D1 (`datasource_id = 'LLM'`) was already contradicted by the live data** and the note lived only
  in this changelog, not in the decision itself, so the spec still asserted it. D1 now records that
  no such row exists, that `compare.expected_datasource` is `""`, and that the real value must be
  read off the agent's first run.

**The blocker is not code (spec §19.3).** `pbi.t_parsed_email_issuance_data` in QA2 is empty and no
issuance row carries an email-ish `datasource_id` — the parsing agent has never written into that
environment. Until the sample emails have been through it, A3 and all of phase B can be built but
not validated: the fetch will correctly return nothing. Also unknown until observed: whether the
agent's output lands in the four `t_issuance*` tables or in `t_parsed_email_issuance_data` (the
utility reads only the former).

**Deliberately out of scope**, and worth holding the line on: multi-update sequences (announce →
guidance → priced). The user expects samples from the client. They are the §18.13 backlog item —
`t_issuance_data` is append-only, so one email would pair against several audit rows and the score
would be *wrong* rather than absent. An upload set must not quietly accept them.

### Reference data recalibrated against the real app vocabularies (spec §19.19.3)

First **code-adjacent** change of this thread — two reference CSVs, no Python. The user supplied the
app's own dropdown values for `use_of_proceeds`, `registration_type` and `regulation_subcategory`,
which per **D16** are authority rather than input to a guess.

**Applied:** `vocab_map.csv` **16 → 43 rows**, `field_map.csv` **8 tier promotions** (compared surface
**133 rows / 69 concepts → 141 / 72**). Originals backed up as `*.csv.bak-20260810` — these files are
**untracked by git**, so that backup is the only way back.

| Change | Detail |
|---|---|
| Fixed | `registration_type` and `tranche_reg_type` `144A/Reg S` → **itself**, was wrongly `SEC Registered` (a self-declared *"best guess - calibrate on first run"*; this was that calibration) |
| Fixed | `use_of_proceeds` refinance prose → `REPAY OUTSTANDING BORROWINGS`; the old target `REFINANCING` **is not one of the app's three allowed values**. Left `confirmed: no` — the user's stated rule only covers the "general corporate purposes" phrase, so this target is still inference |
| Added | The full closed vocabularies as identity rows — 8 `registration_type` (×2 with `tranche_reg_type`), 3 `use_of_proceeds`, 10 `regulation_subcategory`, `esg_type: Green` |
| Added | Email spellings the agent must renormalize, all four seen in real samples: `REGS` → `Reg S`, `144A/REGS` → `144A/Reg S`, `3a2` → `3(a)2`, `with Registration Rights` → `With Reg Rights` |
| Promoted | `regulation_subcategory` (4 tables), `is_esg` and `esg_type` (2 each) from tier `x` to tier 2, with `vocab`/`bool` normalizers. `source_kind` deliberately left `null`: nothing projects them on a generated run, and the phase-B label supplies them directly |

**Verified** — 17 assertions through `compare_field` with the real maps loaded (all pass): every fix
matches as intended; the old wrong target now correctly *mismatches*; and the promoted columns are
safe on existing generated runs, where expected NULL vs actual NULL yields `not_compared` rather than
a penalty. `import main` and all compare-path modules import clean. There was **no suite to re-run** —
the 16/50/167/224-assertion suites named in earlier entries were all *"scratchpad, not committed"* and
no longer exist. Worth fixing at some point: every verification claim in this file rests on code that
was deleted.

**Two code facts learned while verifying, both worth keeping:**

- **An unmapped `vocab` value does not force a mismatch.** `canon_equal` runs first, so two identical
  values absent from the map still `match` via `_loose`; `unmapped` only appends the *"add to
  vocab_map.csv"* note to a mismatch that had already happened (`comparators.py:468`). Spec §18.8's
  *"an unmapped pair is a `mismatch` that names the pair"* overstates it — the pair must differ **and**
  be unmapped. So closing the vocabularies is a quality win (confirmed flags, phase-B dropdowns, less
  note noise), not a correctness fix, and the entry was written to say so.
- **`tier` is the only compare switch; `source_kind` is not consulted by the comparator**
  (`comparators.py:453-460`). §18.5's *"`system` and `null` … are not compared"* is true only
  incidentally — those rows happen to carry tier `x`. Promoting a `source_kind: null` row to tier 2 does
  compare it, which is exactly what the ESG promotion relies on.

**Three reversals on one question, worth the lesson.** Whether the generator emitted an illegal
`registration_type` went: confirmed bug → withdrawn → reinstated → withdrawn for good. Root cause: the
first vocabulary list was **typed from memory and omitted `Reg S`**, while the `use_of_proceeds` list
arrived as a **screenshot of the dropdown** and was right first time. Two derived conclusions were
also built and discarded on the way — that `registration_type` had different vocabularies per grain,
and that `vocab_map.csv` therefore needed a `table` key. **Ask for the control, not the recollection.**
(The `table`-key constraint is real if a per-table vocabulary is ever needed, and is recorded in
§19.19.4 so it is not rediscovered.)

**Also agreed this session — D17, the provenance split** (spec §19.20.1, build step B5). Tranche-level
`registration_type` being *app-derived* generalises: of the 72 compared concepts, ~15 are derived by
the app and ~12 are pipeline-stamped constants, so **~27 are not agent extractions at all**. §18.8
says per-column results exist to feed a prompt-improvement backlog, and only the extracted ones belong
there. Accuracy will be reported as `extracted` / `derived` / `stamped` beside one headline figure,
with a provenance label on every diff row — a group-by on `source_kind`, no new data or endpoint.

### Uploaded email sets — A1 built: parsing and identifier extraction (spec §19.4, §19.21)

First code of the feature. **New:** `backend/email_ingest.py`, `backend/tests/test_email_ingest.py`
(**148 assertions, committed**), `extract-msg>=0.54` in `requirements.txt`. Nothing existing was
touched — no engine, router or frontend change.

The user supplied **10 real `.msg` files** in `backend/expected/samples/` (gitignored). Result:
**37 ISINs, 37 CUSIPs, 0 false positives, 0 spurious rejections**; 9 of 10 emails matchable by
identifier, the tenth flagged for a manual pin rather than guessed at.

**Three bugs the real files found that the screenshots could not**, worst first:

1. **A silent loss.** One whitespace-tolerant regex pass *consumes what it scans*, and `finditer` never
   revisits an overlapping start — so a match beginning at the **label** `ISIN` swallowed the first
   identifier on the line. Format 8 returned 4 of its 5 tranches **with nothing to indicate a loss**.
   Fixed by tokenising: every token, and every run of 2–3 whitespace-adjacent tokens, is proposed
   independently. This is the failure mode to fear — a false positive announces itself, a miss does not.
2. **False identifiers from prose.** Allowing internal whitespace let patterns run across word
   boundaries, and ~1 in 10 such strings passes a check digit by luck: `date August 15` and
   `and January 15` were accepted as ISINs, `2029 May 20` as a CUSIP. Two independent guards — every
   fragment of a multi-token candidate must contain a digit, and an ISIN's first two characters must be
   a current ISO 3166-1 code (+`XS`). Retired codes are excluded deliberately: `AN` (Netherlands
   Antilles) is what let `and January 15` through.
3. **Ordinary words reported as failed identifiers.** `INDEBTEDNESS`, `REGISTRATION`, `COORDINATING`,
   `PARTICIPANTS` — twelve letters whose first two are live country codes. Fixed by requiring a
   candidate to be identifier-*shaped* (an ISIN ends in a digit) before it can be reported as a
   rejection. The CUSIP lane already had that guard; the ISIN lane did not.

**Worth keeping:** a FIGI is `BBG` + 8 + check digit, structurally an ISIN starting `BB` (Barbados)
with a *different* check scheme, so ~1 in 10 would pass the ISIN test — matched first and kept in its
own lane (`sec_144a_figi` / `sec_regs_figi` are tier 1, so this is useful anyway). CINS validates on the
CUSIP algorithm, so letter-prefixed Reg S legs need no special case. `(Exp)` and `(B&D)` are excluded
from tickers by an all-caps requirement plus an alphanumeric charset — ratings and syndicate lines
otherwise read as tickers.

**Two corpus facts that matter downstream:** the sent timestamps are all within 80 seconds of each other
from one sender, confirming §19.8's assumption that ingest time is unknowable from the file; and only
one of the ten has a real subject line (the rest are `2`…`10`), so **nothing can be inferred from the
subject** — the issuer-in-subject case one screenshot suggested is a property of forwarded mail, not of
this corpus. Two of ten yield no ticker, one of those also no identifier: editable identifiers are
load-bearing, not a convenience.

**Screenshots got the design right and the data wrong.** Three identifiers transcribed from the
screenshots failed their check digit; all three were my transcription errors, not bad data. The design
findings drawn from those images (multi-issuer emails, dual 144A/RegS sets, the mangled-CUSIP case, three
wrong vocab entries) all held.

**The suite is committed, deliberately.** Every earlier suite named in this file — 16, 50, 167, 224
assertions — was scratchpad-only and deleted, so every historical verification claim here rests on code
that no longer exists. This one runs with plain `python` (no pytest, no new dependency):
`.\.venv\Scripts\python.exe tests\test_email_ingest.py` → 148 passed. The corpus half **skips cleanly**
when the gitignored samples are absent (63 passed, 1 skip, exit 0), verified by the `PBI_SAMPLES_DIR`
override rather than asserted. Every string in its false-positive section is one the extractor genuinely
accepted at some point against the real files.

**Next:** A2 — `POST /uploads`, the upload run and `util_email` rows, `util_run.source`,
`util_email_label` DDL.

### Uploaded email sets — A2, A3, A4, B1 built (spec §19.22)

Phase A of uploaded email sets, plus the pure half of phase B. **No schema change at
all** — `util_run.tool = 'upload'` marks an upload run, and `add_run`/`add_email`
already existed.

**Files:** new `backend/tests/test_upload_flow.py` (103 assertions, committed);
modified `backend/routers/compare_router.py` (+1 endpoint, `/fetch` branch),
`backend/db_reader.py` (`fetch_rows_by_identifier`, `build_identifier_select`),
`backend/engines/expected_writer.py` (`from_label`), `backend/expected_store.py`
(`new_run_id`, `set_run_params`), `frontend/src/api.js`,
`frontend/src/tabs/EmailCompareTab.jsx`, `frontend/src/index.css`.

**The user pushed back on over-building before this step, and was half right.** Cut
before any code was written: the `util_run.source` column, the `util_email_label` table
(phase B, not A2), two config knobs, five of six planned endpoints, and the `PUT …/ids`
editor. D17's provenance split was deferred to after B3 — there is no score to split
yet. What survived is one endpoint that saves files, parses them, and writes the rows a
capture writes; every existing compare endpoint then works on an upload run untouched.

**A3 — three claims in the spec were wrong, and the database said so.** Probing the live
Automation environment read-only:

- `t_issuance_deal` has **no `deal_id`**. It keys on `first_deal_id`, which
  `t_issuance.deal_id` joins to (1,402 of 1,402).
- **`tranche_id` is all but unused** — 31 of 1,402 issuance rows, 70 of 3,623 audit rows
  — and found **zero** audit rows for a real identifier. `deal_id` reaches 255.
  Deliberately broader (every tranche of a matched deal), because row matching narrows a
  candidate set and too few candidates cannot be narrowed at all.
- Both `t_issuance` and `t_issuance_data` **carry the identifier columns themselves**
  (all tier 1) but populated on a minority of rows — 40 of 1,402 issuance rows have a
  144A ISIN, because a pre-pricing announcement has none to state. So no route is
  complete and they are OR-ed: own columns, security `issuance_key`, `tranche_id`,
  `deal_id`.

Worth internalising: **the spec's join path was invented at design time and three of its
four assumptions were wrong.** Twenty minutes of read-only probing settled all of them.
Ask the database before writing the query.

Verified with identifiers read out of the database itself (3 securities → 3 issuance →
2 audit → 3 deal). A fabricated ISIN returns nothing rather than everything. Against the
ten real samples it returns 0 rows *and explains why* — correct until §19.3's deploy.

**B1 — `from_label`** is pure and separate from `build_expected`; the two share only an
output shape. Verified on the real multi-issuer USB label: 2 deals from one email, 3
security rows including a Reg S leg with a CUSIP and no ISIN, vocab applied, `t_issuance`
omitting the seven columns it lacks. It returns `rendered_fields` — the accuracy
denominator. An unknown column is warned about, never dropped; an off-vocabulary value is
kept *and* flagged so a wrong expectation announces itself.

**A4 — and one honest gap.** Drop zone, `uploaded` marker on run rows, and an Emails
panel expanding to the body in a sandboxed iframe (`sandbox=""`, `srcDoc`). `/fetch`
messages branch on `matched_on` so an upload is never told about a ticker and window the
query never used. `npm run build` is clean **but the panel was never seen rendering** —
no browser in this session. Instead every path the component reads is asserted against a
live API response, so a rename fails a test rather than rendering an empty panel. **The
first real browser run is the actual verification of A4.**

**Tests:** 148 (`test_email_ingest`) + 103 (`test_upload_flow`) = **251 assertions, all
passing**, both committed, plain `python`, no pytest. `test_upload_flow` is layered so
most of it needs neither a database nor the gitignored samples: A3 asserts SQL shape, B1
needs nothing, and the A2/A4 sections skip cleanly when samples are absent. The A2
section creates a real run and deletes it in a `finally`, asserting the cleanup counts.

**Next:** B2 — the labelling form. Recompute its default column list from the ten real
emails first (§19.6.2), rather than from the seven screenshots it was guessed from.

### Uploaded email sets — B2 (and B3) built: the labelling form (spec §19.23)

**New:** `backend/label_form.py`, `frontend/src/components/LabelForm.jsx`,
`backend/tests/test_label_form.py` (92 assertions), `util_email_label`, and
`GET`/`PUT`/`DELETE /api/compare/uploads/{run}/emails/{email}/label`. **B3 fell out of B2** and is
verified in the same pass.

**The field list was measured, not guessed.** Counting the ten samples' own table row labels gave the
vocabulary real broker mail uses (`settlement` 9 emails, `use of proceeds` 8, `format`/`isin`/`cusip`
7, …). The default form is **33 fields** of 72 compared concepts, with *show all* behind a checkbox.
Also corrected: `denominations` **does** have columns — `minimum_piece` + `minimum_increment`, both
tier `x` — so §19.17's claim that it had none was wrong, and 7 of 10 emails state it.

**The finding that justifies the whole exercise.** Label the emails, export the expected lane,
re-import it as `actual`, compare: a perfect agent must score 100%. **The first run scored 5%** —
2 of 22 rows paired. Matching key 2 is `ticker + currency + tenor`, and nothing suggested a currency:
these emails have no Currency row, it is inside the size (`USD Benchmark`, `C$ Benchmark`), which the
prefill read only for an amount and threw away when it found a placeholder. Reading the currency out
of the size row takes it to **100%, 22/22 rows, 333 field matches, 0 mismatches**.

Without that round trip the first real comparison would have reported ~5% and looked like a
catastrophically bad agent. **A harness that silently fails to pair rows is worse than one that
crashes** — the number it produces is wrong in the direction that gets somebody blamed. It is now a
permanent assertion.

**Other defects found only by running against the real files**, all now regressions: the rating
pattern ended in ``, so after matching `A+` the boundary failed and it backtracked to a bare `A`,
silently dropping every +/- suffix (`A2/A+/AA-` → `A2/A/AA`); `Tranche Size: USD` was read as an
amount; the CAD sample's repeated `Tenor:` blocks collapsed 5 tranches into 1; the messy sample uses
`Label value` with no colon and yielded 1 field; and `with Registation Rights` went unmatched because
**the typo is in the source email**.

`html_to_text` was fixed too — it converted cells to tabs and then a cleanup rule ate them. **The A1
test missed it** because it used a minimal table with no whitespace between tags; real Outlook markup
is indented. `html_tables()` was added alongside, because a flat string cannot represent a cell
holding two paragraphs.

**Form behaviour worth keeping:** blank is a legitimate answer (said once, never nagged); suggestions
are chips shown only on empty fields and committed only by Save; closed vocabularies are pick-lists;
pairing-only fields are labelled *pairing* so nobody wonders why an unscored field is asked for; and
the two-issuer email flags itself rather than merging two deals silently.

Saving replaces an email's expectation scoped by `util_email_id`, so relabelling one of thirty leaves
the rest alone. Deal sequences are offset by the email's sequence, because the diff groups by
`(deal_seq, tranche_seq)` and two emails both claiming deal 1 would merge.

**343 assertions** now across three committed suites. `import main` clean, `npm run build` clean.
**Not visually verified** — still no browser in the session.

**Next:** B4 (label export/import round-trip) and B5 (the D17 provenance split).

### Uploaded email sets — B4 and B5 built; phases A and B complete (spec §19.24)

**B5, the provenance split (D17):** `field_map.source_kind` sorts findings into `extracted` /
`derived` / `stamped`; `score()` reports the three beside the headline and the diff tags each
row. Computed **on read, never stored** — provenance belongs to the field map, not to a pass,
so there was no migration and a map correction fixes passes already scored. A labelled run
measured **273 extracted, 58 derived**: a quarter of the headline is not agent extraction.

**B4, label export/import:** a versioned document, matched back to emails by **identifier**
rather than row id — row ids change on re-upload, so an export keyed on them would only work
on the machine that made it. Verified by deleting the run outright, re-uploading the same
emails and restoring: identical accuracy over identical field counts. `/import` now also takes
`lane=expected` for upload runs only; a capture run's expected lane is generated and must not
be overwritten by a file.

**Two leaks the tests found**, both now regressions: deleting a run left its saved `.msg`
copies behind (**41 orphaned directories** from one session — the store knows nothing about the
filesystem), and a *rejected* upload orphaned an empty directory because the directory is
created before parsing. The second surfaced only because the suite asserts the store is clean
**after** it runs. Worth keeping: a test that tidies up but never checks it tidied up will not
notice a leak.

**Phases A and B are complete** — A1–A4, B1–B5. **363 assertions** across three committed
suites; `import main` and `npm run build` clean. What is left is not code: §19.3's deployment,
the `coupon_type` conversion table, and confirming `3a2` -> registration type. Phase C stays
deferred until update-sequence samples exist.

**Still not visually verified.** No browser has been available in any session that wrote the
frontend, so the first real render remains the outstanding check.

### First browser render; ratings dedup and deal_currencies fixed (spec §19.26)

The user sent a screenshot of the labelling form running in the app — **the first visual
verification of any of this frontend**, and it looks as designed. A4's outstanding gap is closed.

Two bugs the screenshot showed:

* **`issuer_rating` was deduplicated** — ORCL states Moody's Baa2 / S&P BBB / Fitch BBB and the
  prefill produced `Baa2/BBB`. Two agencies often assign the same grade and the `ratings` normalizer
  compares a multiset, so the repeat matters: **71.0% → 75.6%**.
* **`deal_currencies` was left blank** though every tranche states its currency. `from_label` now
  derives it, alongside `db_number_of_tranches` — both are stated by the label's *shape*, not by the
  labeller, and both are tier 1.

Running total on the two emails the agent has processed: **27.5% → 54.6% → 61.2% → 71.0% → 75.6%**,
every step a harness defect rather than a change in the agent.

**Five bugs have now been found by real data, all the same shape:** the harness reporting a number
wrong in the direction that blames the agent — the 5% currency bug, coverage-as-accuracy, unparseable
month-name dates, deduplicated ratings, and the silently lost tranche. None was visible from unit
tests on synthetic input. **An accuracy harness fails silently by default**: a wrong number looks
exactly like a right one, so every figure needs a construction where the answer is known.

Open and needing the user, not code: the ORCL email states three different rating outlooks
(Negative, Negative, Stable) into a single `rating_outlook` column. The prefill takes the first and
the diff shows it right 1 of 11, so the application probably keeps a different one.

---

## Changes — 2026-08-13

### ABS slice 1 — securitized reference data and pure utils (abs_spec §20.19.2)

First of the three ABS build slices (§20.19.1). It touches nothing the running app imports:
no router, no engine, no `main.py` mount, no frontend. Bonds/Loans/Interest/TIG/CSV Upload
are byte-identical.

**Built**

- `backend/reference/securitized/_generate.py` (~640 lines) — the one-off, deterministic
  (`SEED = 20260813`), idempotent builder for all ten CSVs. Never called at runtime; the
  CSVs are the artifact and the engine only reads those.
- `backend/reference/securitized/*.csv` — `entities.csv` **2250**, `vocabularies.csv` 126,
  `asset_types.csv` 56, `currencies.csv` 3, `agents.csv` 40, `underwriters.csv` 60,
  `legal_advisors.csv` 40, `auditors.csv` 10, `ratings.csv` 22, `analysts.csv` 30.
- `backend/engines/abs_utils/{__init__,dates,identifiers}.py` (~300 lines) — reusing
  `loan_utils/cusip.py::check_digit`, `bonds_engine::_isin_check` and `::_gen_figi`
  rather than reimplementing any of them, per the slice-1 prompt.
- `backend/tests/test_abs_slice1.py` (~330 lines) — 224 assertions, no pytest and no
  server, matching the other files in `tests/`.

### The sample's check digits are wrong. Its structure and its dates are right.

§20.19.3 named seven identifier "golden values" taken from `master/oakhurst_deal.json`,
with the instruction that a disagreement would be the implementation's fault. It is not.

`check_digit` and `_isin_check` were validated **first**, against ten real, publicly
verifiable identifiers — CUSIPs `037833100` (Apple), `594918104` (Microsoft), `88160R101`
(Tesla), `02079K305` (Alphabet C), `46625H100` (JPMorgan); ISINs `US0378331005`,
`US5949181045`, `GB0002634946`, `DE0005557508`, `XS0629974352`. **10/10.** Tesla's is the
load-bearing one: `88160R101` puts a letter in the same doubled 6th position as the
sample's `67543M`, so the only case the sample could plausibly be exercising differently
is itself confirmed.

Against that baseline **every sample CUSIP is exactly +2 off and every sample 144A ISIN
exactly +5 off** — a constant offset across all three tranches, which is a fabricated
identifier rather than a different algorithm. The platform accepting the payload proves
nothing here: nothing in the ingest path validates a check digit.

So slice 1 asserts the **corrected** values (`67543MAA2` / `AB0` / `AC8`, `US67543MAA27`,
`US67543MAB00`, `USU67543AA38`, `USU67543AB11`) and asserts the sample's **structure**
separately and unchanged — shared 6-character base, suffix advancing per tranche, the two
US ISIN forms, 9/12/12 characters. The structure is what slice 2 consumes; only the last
one or two characters of each identifier moved. §20.8.1 and §20.19.3 record this.

**The dates are the opposite story** — every one is internally consistent, which is what
let two undocumented rules be recovered from the sample rather than guessed:

- **Payment day 15.** A bare tenor anniversary gives 1096/1826/2192 days; the spec's
  golden is 1106/1836/2202. The difference is the anniversary rolled forward to the 15th,
  the monthly ABS payment date. No business-day adjustment on maturity — 2032-08-15 is a
  Sunday in the sample and stays there.
- **Tranche settlement steps one calendar month per tranche**, then rolls to a weekday.
  The sample's 2026-08-17 / 2026-09-17 / **2026-10-19** is Saturday 2026-10-17 rolled to
  Monday.

With both, `abs_utils/dates.py` reproduces the sample's raw epochs exactly — all four
series dates, all three maturities, all three tranche settlements, and the series legal
final at 1976227200000, which is 86,400,000 ms after the latest tranche maturity.

### Other deviations, all recorded in abs_spec.md this session

1. **56 asset types, not 57**, so `vocabularies.csv` is 126 rows and not 127. The workbook
   holds 56 values under `Asset Type` (rows 2–57 of a 137-row sheet). The other five counts
   (28/17/12/8/5) were right. The workbook is the authority; it is transcribed verbatim,
   `ACT/36S` and `ACT/36S (FIXED)` included, and the test asserts that `ACT/365` was *not*
   silently introduced.
2. **§20.5.4's `DAY_COUNT` row had escaped §20.5.5 trap 1.** `Actual/360` and `Actual/365`
   are not vocabulary members. `ACT/360` is; **`ACT/365` is not** — the workbook spells it
   `ACT/36S`, so that is what a GBP deal carries.
3. **Two columns added.** `currencies.csv` gains `DAY_COUNT` (§20.5.4 makes it
   currency-driven; the column list omitted it). `asset_types.csv` gains `ASSET_FAMILY`, so
   it can be joined to `entities.csv` — §20.5.2 makes the family constrain the asset-type
   pick but gave the key no home on this side of the join.
4. **`PRODUCT_GROUP` is `ABS` on all 56 rows.** It is a [Q14] field with no schema
   confirmation and the sample's `ABS` is the only known-good value, so a later refinement
   (`RMBS`, `CMBS`, `CLO`…) is a data edit rather than a code change. Deliberate, not an
   oversight — slice 3's live run is what would license anything else.

### Why entities.csv is generated the way it is

2250 rows = 250 brand stems × 9 asset families, comfortably over the ≥ 2000 floor, and
every one of `ISSUER_NAME` / `ORIGINATOR` / `SPONSOR_NAME` / `DEPOSITOR` and all three
tickers and all three CIKs is distinct across the whole file. The four names in a row share
a brand stem, per §20.5.2 — the point of the single file.

**Row 0 is the sample's own family**, reproduced exactly: `Oakhurst Auto Receivables Trust`
/ `OART` / `Oakhurst Auto Finance LLC` / `OAKAF` / `Oakhurst Capital Markets LLC` / `OAKCM`
/ `Oakhurst Auto Receivables Depositor LLC`, with CIKs 1701472 / 1701455 / 1701460 straight
from `oakhurst_deal.json`. That falls out of the naming scheme rather than being special-cased
(issuer ticker = the shelf's initials, originator = brand code + family code, sponsor = brand
code + `CM`), so it is a live regression test on the scheme: change how names are built and
row 0 stops matching the sample. `ISSUER_NAME` and `ISSUER_TICKER` are stored as **stems**
with no vintage, so re-running in 2027 produces 2027 deals without touching reference data.

### Caveats for slice 2

- `PRICING_SPEED` in `asset_types.csv` is a representative quote (`1.30% ABS`, `22% MPR`,
  `8% CPR`, `0% CPR`); the engine is expected to vary the numeric part and keep the
  convention suffix. The sample's tranches all read `1.30% ABS`.
- `vocabularies.csv` carries `WEIGHT=0` on the 15 unsupported `COUPON_TYPE` values, on the
  bank-capital/insurance `RANKING` values that are not ABS rankings, and on the two
  `ACCRUAL_METHOD` values (PIK, Z-tranche) that imply a coupon type v1 does not generate.
  A weighted draw must therefore filter `WEIGHT > 0` rather than assuming every row is
  drawable.
- Multi-valued cells in `currencies.csv` are pipe-separated, and `COUNTRY`/`COUNTRY_NAME`
  and `FLOAT_BENCHMARK`/`INDEX_LEVEL` are index-aligned pairs.
- `ant_redemption_date()` takes the tranche maturity and steps back a payment period if it
  would otherwise land on or after it, so `ANT_REDEMPTION_DATE < MATURITY_DATE` holds by
  construction rather than by the caller getting WAL and tenor right.

**Verified:** `python tests\test_abs_slice1.py` → **224 passed, 0 failed**;
`python -c "import main"` → OK; `_generate.py` run twice produces byte-identical CSVs.

---

### ABS slice 2 — the securitized engine and backend wiring (abs_spec §20.19.2)

Second of the three slices. It adds a route but no UI reaches it, and **no live POST was
made** — that is slice 3. Bonds/Loans/Interest/TIG/CSV Upload behaviour is unchanged; the
only shared files touched are `main.py` (one import, one `include_router`), `_shared.py`
(one `elif`) and `environments.json` (two additive blocks).

**Built**

- `backend/engines/securitized_engine.py` (~910 lines) — `ReferenceData` (ten CSVs, all
  optional with fallbacks) → `DealGenerator` (deal → series → tranche → security as nested
  lists) → `check_payload` (pre-flight assertions) → `_serialise` (the `TEMP-*` map form
  plus the one string-coercion point) → auth/publish loop. Auth, headers and envelope are
  Loans' verbatim; `_login`/`_publish`/`_interruptible_sleep` keep its shapes.
- `backend/routers/securitized.py` (28 lines) — `routers/loans.py` with the tool renamed.
- `backend/tests/test_abs_slice2.py` (~470 lines) — the dry-run harness: the six §20.19.4
  shapes plus a multi-deal cycling run, the oversized fat-finger case, the param-validation
  paths and four negative tests that prove `check_payload` fires.
- `main.py`, `routers/_shared.py`, `environments.json` — the mount, the `ref_dir` line, and
  `reference_dirs.securitized` + `tool_defaults.securitized` per §20.9.1 (slice 1 had
  registered neither).

### Three spec rules that contradict each other on a deep stack

All three produce data that ingests cleanly and is financially wrong, which is what §20.6
exists to prevent. Each was found by the harness, not by reading — the generator is
deliberately unseeded, so the harness runs every shape N times (`ABS_SLICE2_REPEATS`,
default 12) rather than trusting one draw.

1. **The reserve can exceed the class above it (§20.6.3, now corrected in the spec).**
   `reserve_pct` is drawn 0.5–2.0 independently of the structure, but the second-most-junior
   class's CE is the *most* junior class's share of the series — thin on a 5-class stack.
   First 5-series/8-tranche run: class `D` → 0.80, class `E` → 1.67. CE **rose** at the
   bottom of the stack, breaking the monotonic rule stated two sentences earlier in the same
   section. The reserve is now capped at half the thinnest computed CE (floor 0.05); a
   single-class series keeps the drawn value.
2. **The A-x term premium can overtake the next credit class (§20.6.4, now added).** A fixed
   senior starting at 15 bp with the stated `+2…8` per step reaches 39 bp at `A-4`, while
   the stated `×2` class step puts `B` at 30. Class bases are now floored at
   `last_value_in_the_class_above + margin` (+10 bp on both spread ladders, +0.10 % on
   `INT_RATE`). The stated ranges are unchanged — only their floor is.
3. **`PREPAYMENT_TYPE = "None"` is a sentinel, not a value (§20.5.3, now added).** Slice 1
   wrote `None` / `0% CPR` on the 20 asset types with no prepayment convention. Emitting the
   literal string `"None"` over the wire would be exactly the plausible-but-wrong data this
   spec is built to avoid, so both fields are omitted on those asset types — the same rule
   §20.5.3 already applies to out-of-profile pool statistics. Neither field is mandatory
   (§20.3.7), so omission is safe. The alternative, fixing the 20 CSV rows, is a slice-1 data
   change and was not taken.

### Decisions worth knowing in slice 3

- **`check_payload` splits its findings two ways.** Mandatory-field failures (§20.3.7) are
  `error` and **skip the deal** — not posted, not dumped in dry-run, counted `INVALID` in the
  summary's `failed`, run continues. Consistency failures (§20.6.6) are `warn` and the deal
  still posts: a wrong `WAL` is worth seeing on the wire, a missing `ISSUER_TICKER` is only
  worth a NACK. §20.3.7 was clarified to say so.
- **The harness recomputes the §20.6 arithmetic independently** rather than calling
  `check_payload`, then runs `check_payload` as well and requires it to be silent. A bug in
  the checker cannot hide a bug in the generator, or the reverse.
- **CUSIP suffixes advance deal-wide, not per series.** §20.8.1 says "once per tranche in
  stack order"; with two series, restarting at `AA` per series would collide on the derived
  ISINs. `IdentifierPool.claim_isin` would have raised, but the ordinal is threaded through
  `_generate_series` so it never gets the chance.
- **`PRICING_SPEED` varies its number and keeps its suffix** (`1.30% ABS` → `1.24% ABS`),
  per the slice-1 caveat above; it is drawn once per series, since §20.6.4 lists it among the
  fields the senior stack shares.
- **`_class_layout` extends past §20.6.4's 7-row table** (`A-1…A-4` then `B, C, D, E…`) and
  `_tenor_ladder` extends past the 8-value pool, because §20.9 says out-of-range values are
  allowed and only warned about. An 8-tranche run generates rather than raising.
- **One thing slice 1's data does not deliver:** `PRODUCT_GROUP` is `ABS` on all 56 rows of
  `asset_types.csv`, so §20.3.3's `RMBS`/`CMBS`/`CLO` variety never appears. The engine reads
  the column as specified; correcting it is a slice-1 data edit and was left alone.
- Series objects carry only the four dates §20.3.3 lists (`EXPECTED_PRICING_DATE`,
  `SETTLEMENT_DATE`, `FIRST_COUPON_DT`, `MATURITY_DATE`). A second series' own announcement
  date shifts its whole chain (§20.7.3) but is not itself an emitted field, so `ANNOUNCEMENT_DT`
  appears only at deal level, as the earliest series announcement.

**Verified** (all offline, no server, no network — `queue.Queue` + `threading.Event` +
`dry_run: True`):

- `python tests\test_abs_slice2.py` → **20,322 / 20,322 pass**
- `ABS_SLICE2_REPEATS=60 python tests\test_abs_slice2.py` → **84,787 / 84,787 pass**
- 300 randomised runs (1–3 deals × 1–4 series × 1–7 tranches × 1–2 securities × all three
  coupon types × all three currencies + blank) → 604 payloads, **0 `check_payload` findings**
- `python tests\test_abs_slice1.py` → **224 passed, 0 failed** (no regression)
- `test_email_ingest` 148, `test_label_form` 125, `test_upload_flow` 106 → all pass
- `python -c "import main"` → OK; `app.openapi()` shows the four `/api/securitized/*` paths
  and Loans' four unchanged
- **No live POST.** Slice 3 owns that.

## Changes — 2026-08-13 (later) — ABS slice 3: frontend, live run, doc merge

Spec §20 (formerly `master/abs_spec.md`), slice 3 of 3. **The ABS feature is complete**: the tab
exists, fifteen deals posted live, and `abs_spec.md` is retired into `spec.md` §20.

### Files touched

| File | Status | Notes |
|---|---|---|
| `frontend/src/tabs/SecuritizedTab.jsx` | **new** (~205 lines) | Modelled on `LoansTab.jsx`. `useRunState('securitized', recordRun)`, `localStorage` under `pbi.securitized`, history POST with `params_summary`. Three cycling-list inputs parsed comma-separated exactly as `tranches_per_multi`. Selects for currency / coupon type; asset type is a select over the live vocabulary, falling back to free text |
| `frontend/src/App.jsx` | modified | `securitized` tab registered after Loans, label "Securitized", + import and render branch |
| `frontend/src/tabs/SettingsTab.jsx` | modified | *Securitized reference dir* field, and a *Securitized (ABS) defaults* section — the **first** `tool_defaults` editor in Settings (see deviation 1) |
| `frontend/src/api.js` | modified | `getSecuritizedAssetTypes()` |
| `backend/routers/securitized.py` | modified | `GET /asset_types` — reads `asset_types.csv` from the configured ref dir so the 56 values aren't duplicated in JSX; any failure answers `{"asset_types": []}` (see deviation 2) |
| `backend/engines/securitized_engine.py` | modified | **The one live NACK fix.** Emits `MANDATE_TEXT` when `IS_ROADSHOW` is true; `check_payload` gains the conditional rule as a hard error (see deviation 3) |
| `backend/reference/securitized/_generate.py` | modified | `PRODUCT_GROUP_OVERRIDES` — five asset types that name their own product group; header deviation 3 rewritten; `WORKBOOK` repointed to `_source/` |
| `backend/reference/securitized/asset_types.csv` | regenerated | 5 of 56 rows changed. The other nine CSVs regenerate **byte-identically** (SHA-256 unchanged) |
| `backend/reference/securitized/_source/ABS_DropDowns.xlsx` | **moved** | from `master/` — `_generate.py` reads it |
| `backend/tests/fixtures/oakhurst_deal.json` | **moved** | from `master/` — `test_abs_slice1.py` reads it |
| `backend/tests/test_abs_slice1.py` | modified | `SAMPLE` path constant + one comment |
| `backend/engines/abs_utils/{dates,identifiers}.py` | modified | docstring path references only |
| `master/spec.md` | modified | **+1,735 lines: §20 Securitized (ABS) Issuance**, §20.1–20.21, numbering preserved exactly |
| `master/abs_spec.md` | **deleted** | it is now §20 |
| `CLAUDE.md` | modified | tab list, tool list, one *Architecture* bullet, four new *Gotchas* bullets, one graphify bullet |

Bonds / Loans / Interest / TIG / CSV Upload behaviour untouched.

### What the live run settled

Fifteen posts, every response body captured verbatim — the table is spec §20.21.

- **A9 is closed.** The float benchmark field is literally `BENCHMARK`. This was the last open
  assumption and the thing build step 7.2 existed to test. Eleven live float tranches ACKed.
- **Multi-series works** (step 7.5, the open half of Q4): 2 series / 6 tranches / 12 securities in one
  event, ACK first time.
- **Q13 closed** by the first ACK: `ORIGINATOR`, not `ORIGINATOR_NAME`.
- **CE-per-credit-class holds on the wire**: step 7.4's `A-1`/`A-2`/`A-3` all posted at 17.1%.
- **The ABS schema is closed** (`additionalProperties: false`), which makes it a field-name oracle.
- **[Q14] answered, and the question was the wrong one** — see deviation 4.

### Deviations from spec §20 — all four recorded in §20 in this session

1. **§20.9.1 said `tool_defaults.securitized` would be editable "alongside the existing per-tool
   default blocks". There were none.** `SettingsTab.jsx` edited environments, `reference_dirs`, `email`
   and `compare`, and passed `tool_defaults` straight through via `...config`. Bonds', Loans',
   Interest's and TIG's defaults are still hand-edited in `environments.json`. Slice 3 added the first
   such editor, for `securitized` only — widening it to the other four was out of scope. The pattern
   to copy: hold the whole `tool_defaults` object in state, edit one sub-object, write the whole thing
   back, so an unedited tool's block can't be dropped. **§20.9.1 rewritten.**

2. **One route beyond "`routers/loans.py` with the tool renamed" (§20.10).** `GET
   /api/securitized/asset_types`, ~15 lines, read-only. The alternative was hardcoding 56 exact
   strings in the JSX, where a single typo silently degrades to a random asset type (the engine warns
   and redraws). Honours the reference-data contract: any failure answers an empty list and the tab
   falls back to free text. **New §20.9.3.**

3. **A conditionally mandatory field nobody knew about: `MANDATE_TEXT`.** Required if and only if
   `IS_ROADSHOW` is true; a roadshow deal without it NACKs `"Mandate Text is required"`. Absent from
   the spec, from the §20.16 Q5 mandatory-field answer, and from the sample — the sample deal isn't a
   roadshow deal, so nothing in the source material hinted at it. Found by field-name probing against
   the closed schema (`MANDATE_TEXT` ACK; `MANDATE` / `MANDATE_TXT` / `ROADSHOW_MANDATE_TEXT` each
   NACK naming the property). All four legs of the truth table posted live. Engine now emits it on
   roadshow deals only, and `check_payload` carries the rule as a hard error. **New §20.3.8.**

   Worth carrying forward: the NACK arrived on the *Float* step and had nothing to do with Float.
   `IS_ROADSHOW` is drawn at ~20%, so a deterministic schema rule presented as an intermittent one —
   and the step's re-run ACKed, which would have been the wrong conclusion. Diffing 7.1's payload
   against 7.2's is what found it. The `500` / no-`PATH` error shape was the second clue (§20.2.3).

4. **[Q14]: the handler does not validate string values at all, so step 7.7's test design had no
   power.** Step 7.7 was written to settle whether `PRODUCT_GROUP` is a constrained vocabulary by
   posting `RMBS` and reading the outcome. It ACKed — but so did the negative controls
   `PRODUCT_GROUP: "ZZ_NOT_A_PRODUCT_GROUP"` and `DAY_COUNT: "Actual/360"` (a known non-member). The
   schema enforces property names, types and presence; enumerated values it never touches.

   This reframes §20.5.5: the six workbook vocabularies buy nothing at ingest and everything
   downstream, and **an ACK is never evidence that a value is right**. Two negative controls, one POST
   each, and they are the most valuable rows in §20.21.

   `PRODUCT_GROUP` was nonetheless changed on the five asset types that *name* their own product group
   (RMBS, CMBS, CLO, CDO, CBO), because `"ABS"` on an RMBS deal is exactly the plausible-but-wrong data
   §20.6 exists to prevent — but on **data-quality grounds, not on the ACK**, and it is flagged in
   §20.5.5 as the weakest-evidenced choice in the section. Reverting is one line
   (`_generate.py::PRODUCT_GROUP_OVERRIDES`) plus a regeneration. CFO and the NPL/RPL securitizations
   were deliberately left at `ABS` rather than guessed at.

### Verified

- `npm run build` → ✓ built in 1.83s, 46 modules, no warnings
- `python -c "import main"` → OK
- `python tests\test_abs_slice1.py` → **224 passed, 0 failed** (after the fixture move)
- `python tests\test_abs_slice2.py` → **20,311 / 20,311 pass** (after the engine change)
- `python tests\test_label_form.py` → **125 passed, 0 failed**
- `python tests\test_upload_flow.py` → **106 passed, 0 failed**
- 400-deal offline assertion: `MANDATE_TEXT` present on all 74 roadshow deals, absent on all 326
  others, 0 pre-flight errors; plus a negative control proving the new `check_payload` guard fires
- `_generate.py` re-run from the new `_source/` location → all ten CSVs byte-identical by SHA-256
  except the intended `asset_types.csv`
- `grep -rn "abs_spec\|master/oakhurst\|master.*ABS_DropDowns" backend/ frontend/src/` → **0 matches
  in source** (the only remaining hits are inside the stale generated `backend/graphify-out/`, which
  is itself the fingerprint of a past `graphify update` run from a subdirectory)
- Live: 15 posts, 13 ACK + 2 diagnostic NACK, every body captured verbatim (§20.21)
- Tab wiring driven end-to-end over HTTP through the Vite proxy — `GET /asset_types` → 56 values,
  `POST /run` → 200, SSE stream → logs + summary + done, both dry and live

### Caveats / open

- **The live run used `TRP - QA2`, not the named `TRP - QA - Automation`.** That host was unreachable
  — DNS resolved to `172.21.127.240`, TCP 443 refused, repeatedly, while `TRP - QA1` and `TRP - QA2`
  connected from the same machine at the same moment. Same platform and same
  `credentials.bonds_loans` user, and every finding above is a property of the handler rather than of
  the environment — but the named environment has still never seen an ABS deal. Recorded in §20.17.
- **No browser-automation tool was available in this session**, so "open the app and run one dry-run
  from the browser" was met by driving the browser's exact HTTP path through the Vite dev-server proxy
  on `:5173` (the same three requests `useRunState` makes) and by fetching
  `/src/tabs/SecuritizedTab.jsx` from the dev server to confirm it transforms. `npm run build` covers
  compilation. **What remains unverified is visual**: that the tab renders and looks right. A
  30-second human look at `http://localhost:5173` → Securitized closes it.
- `PRODUCT_GROUP` on the five overridden rows is the weakest-evidenced choice in §20 (deviation 4).
- **A8 (GBP conventions) is not closed**, and no ACK can close it — a GBP deal ACKed, but §20.5.5
  establishes that acceptance of a non-schema-controlled string means nothing. It needs a real GBP
  sample payload.
- `master/plan.md` still describes the three-tool app and does not mention ABS. It is a build-order
  document for the original build and was not in slice 3's scope; `spec.md` §20 and `CLAUDE.md` are
  the current records.

## Changes — 2026-09-09 — ABS deal-type model (DEAL_TYPE / ISSUER_SECTOR / SUB_INDUSTRY)

Reworked the Securitized (ABS) deal identity at the operator's request. Three changes:

1. **`ASSET_TYPE` removed from the Series payload and the UI.** The Series no longer carries
   `ASSET_TYPE`; the tab's Asset Type control (and the Settings default) are gone. The internal
   asset-type pick still happens — it drives `INDUSTRY`, `BUSINESS`, `POOL_PROFILE`,
   `PREPAYMENT_TYPE`, `PRICING_SPEED`, `USER_OF_PROCEEDS` and the Series `PRODUCT_GROUP` — but the
   asset type is no longer selectable and no longer emitted by name.
2. **`DEAL_TYPE` added at deal level**, user-selectable (`ABS` / `CLO` / `CMBS` / `RMBS`) with a
   blank "random per deal" default. Per the operator's decision, **`DEAL_TYPE` drives asset
   selection**: the internal pick is constrained to `asset_types.csv` rows whose `PRODUCT_GROUP`
   equals the deal type. `ABS` has 51 candidate rows; `CLO`/`CMBS`/`RMBS` have one each, so those
   deal types resolve to a fixed asset profile (noted as a data limitation — could add more non-ABS
   rows later). `PRODUCT_GROUP` now always equals `DEAL_TYPE`.
3. **`ISSUER_SECTOR` + `SUB_INDUSTRY` added at deal level**, randomised from `DEAL_TYPE`.
   `ISSUER_SECTOR` is drawn from the deal type's sector list; **`SUB_INDUSTRY` is nested under the
   chosen sector** (operator chose nesting over independent draws) and is **omitted** when the sector
   has none. The old asset-derived deal-level `SUB_INDUSTRY` was replaced. Mappings live in
   `securitized_engine.py::{DEAL_TYPE_SECTORS, SECTOR_SUB_INDUSTRY}`; the full tables are in spec
   §20.3.2.

**Operator decisions captured** (asked because the closed ABS schema + "don't assume"): server
schema already accepts the three new fields under exactly these names and no longer requires
`Series.ASSET_TYPE`; SUB_INDUSTRY nested under sector; sectors with no sub-industry omit the field;
`CRE CLO` is the CLO deal type's sector (the `3.0 *` sub-industries nest under it); `Freddie K` is
valid under **both** RMBS and CMBS.

**Also removed:** the read-only `GET /api/securitized/asset_types` route (`routers/securitized.py`)
and its `getSecuritizedAssetTypes` client helper (`api.js`) — both existed only to feed the deleted
Asset Type dropdown. `routers/securitized.py` is once again exactly "loans with the tool name
swapped". `check_payload` adds `DEAL_TYPE`/`ISSUER_SECTOR` to the deal mandatory set and warns (does
not block) on a sector/sub-industry that is inconsistent with its parent — matching the
plausible-but-wrong-data posture used for `PRODUCT_GROUP`.

**Verified (dry-run, offline):** 400-deal random sweep — all four deal types produced; all 18 sectors
and 17/18 sub-industries sampled (Fleet Lease is valid but low-probability in the Autos bucket); zero
mandatory-field errors; zero sector/sub-industry consistency warnings; no `ASSET_TYPE` leaked onto any
Series; `PRODUCT_GROUP == DEAL_TYPE` on every Series. End-to-end `run_securitized` dry-run for a forced
CLO deal confirmed the emitted `DETAILS` (`DEAL_TYPE=CLO`, `ISSUER_SECTOR=CRE CLO`, nested
`SUB_INDUSTRY`, no `ASSET_TYPE`). `npm run build` clean. **Not yet run live** — a live post should
confirm the server truly accepts the three fields and the `ASSET_TYPE` removal (schema acceptance was
asserted by the operator, not observed on the wire).

**Live NACK + fix (same day).** The first live post NACKed: *"$.DETAILS: property 'ISSUER_SECTOR' is
not defined in the schema and the schema does not allow additional properties"* — i.e. the operator's
"schema already accepts these names" was wrong for `ISSUER_SECTOR`. The closed schema behaved as the
field-name oracle §20 documents. Fix, at the operator's direction: **carry the issuer sector in the
existing schema-valid `INDUSTRY` field** and drop the `ISSUER_SECTOR` property entirely. `INDUSTRY`
now holds the sector (e.g. "Autos", "CRE CLO") instead of the asset row's industry ("Consumer
Finance"). `check_payload` updated to read the sector from `INDUSTRY`; `DEAL_TYPE` kept and
`INDUSTRY` added to the deal mandatory set. Re-verified with the same 400-deal dry-run sweep: 0
errors, 0 `ISSUER_SECTOR` leaks, `INDUSTRY` always a valid sector for its `DEAL_TYPE`, `SUB_INDUSTRY`
nesting intact. **`DEAL_TYPE` acceptance still unconfirmed** — the NACK named only `ISSUER_SECTOR`,
and the validator may report just the first unknown property, so the next live post is the real test
of `DEAL_TYPE`; the operator said to keep it and, if it NACKs, remove it (or reuse another field).

---

## Changes — 2026-09-15 — Munis: the fourth asset class

Adds a **Munis** tool posting `EVENT_CREATE_NEW_MUNIS_ISSUANCE`, alongside Bonds, Loans and
Securitized. Spec §21.

### What was built

| Path | |
|---|---|
| `backend/engines/munis_engine.py` | new — generator, pre-flight checks, transport |
| `backend/routers/munis.py` | new — `routers/securitized.py` with the tool renamed |
| `backend/reference/munis/` | new — 7 CSVs + checked-in `_generate.py` |
| `backend/tests/test_munis.py` | new — 7 shapes x 6 repeats, all §21.6 rules |
| `backend/tests/fixtures/munis_sample_deal.json` | new — the captured payload |
| `frontend/src/tabs/MunisTab.jsx` | new |
| `backend/main.py`, `routers/_shared.py`, `environments.json` | wired (router, `ref_dir`, defaults) |
| `frontend/src/App.jsx`, `tabs/HistoryTab.jsx`, `index.css` | tab, history label, accent |

`HistoryTab`'s `TOOL_LABEL` was missing `securitized` as well as `munis`; both were added, since the
gap rendered the raw tool id next to properly-labelled siblings.

### The part that mattered: deriving the rules from data

The user supplied one captured payload, a lookup export with **only four** vocabularies, and
`master/MUNIS_DATA.csv` — 95 deals / 237 series / 2,142 maturities. The brief was that the tool
should reproduce whatever is *issuer-dependent* and *upstream-dependent*. So the reference data was
built by analysing that file rather than transcribing a list, and every rule in spec §21.6 carries
the count that establishes it. The ones that change emitted data:

1. **`MATURITY_AMOUNT` is denominated in thousands** — `SERIES_SIZE = 1000 x sum(MATURITY_AMOUNT)`,
   234/235 exact. This is the single most consequential finding: emitting both in the same unit
   ingests cleanly and produces a deal 1,000x the size it claims. The first draft did exactly that
   and the deals came out at $2.6M.
2. **Issuer-dependence** — state, sector, purpose, tax status, ratings, repayment source,
   enhancement and typical size are one issuer's profile. `issuers.csv` is one row per real deal so
   they travel together; the engine draws the row once and reads the rest off it.
3. **`ENHANCEMENT` is state-gated** — "Pennsylvania State Aid Intercept Program" is 41/41 PA,
   "Assured Guaranty Inc" 53/53 CA.
4. **`MATURITY_DESCRIPTION` has three forms and the form is chosen by tax status** — all 61
   treasury-spread descriptions sit on Taxable/Various series, none on tax-exempt. The double space
   after `yld` is in the source on all 61 rows and is reproduced.
5. **`SOURCE_OF_REPAYMENT` is a General Purposes field** — 13/2,142 populated, all General Purposes.
6. **Price is derived, never drawn** — 1,323 premium rows all have yield < coupon; all 433 par rows
   have yield == coupon.
7. **Rating roll-up** — `Various` iff the series disagree, 18/18.

### Three bugs the pre-flight checks caught during development

Worth recording because each was invisible in a green dry run, which is the §20.5.5 lesson applied
to a new tool:

- **`DATED_DATE` after `DELIVERY_DATE`.** Both were drawn as independent offsets from the trade
  date. Settlement follows dating (232/234 real series), so `DELIVERY` is now derived from `DATED`.
- **Deal sizes ~100x too small.** Fixed by scaling from the issuer's `TYPICAL_SIZE` and allocating
  *down* the tree with `_split_quantised`, so both roll-ups close by construction rather than by
  adjustment.
- **`PURPOSE: PIT` mapped to `SECTOR: Various`.** Taking the modal sector per purpose code let the
  aggregate `Various` win for `PIT`, which then contradicted every PIT issuer's own General Purposes
  sector. `Various` is now excluded as a home sector, and — the deeper fix — a *series* never
  carries `Various` at all: like the rating rule, it is a deal-level roll-up across differing
  children, so a `Various` issuer has its series sector resolved from the purpose code.

The third one is the interesting one: the test caught a *modelling* error, not a coding error, and
the fix changed the spec rather than the code alone.

### Decisions

- **Envelope follows the capture, not the house style.** Munis puts `SESSION_AUTH_TOKEN` and a
  per-deal **UUID** `SOURCE_REF` in the body; the other three engines send a constant `12345` in the
  headers only. The capture is what the platform actually received, so it wins. Both are sent.
- **No `GET` route.** The state/sector/tax/status options are static lists in `MunisTab.jsx`,
  following the 2026-09-09 precedent that removed ABS's read-only `GET /asset_types`.
- **`SOURCE_OF_REPAYMENT` is omitted on non-General-Purposes series** — the one place the payload
  deviates from the capture (§21.3.5). The capture carries it on an Education series, but that
  capture was hand-filled in the UI (its `SERIES_DESCRIPTION` says Multi-Family Housing while its
  `SECTOR` says Education), so it is not evidence of a rule; 2,129/2,142 real rows leave it null.
  If the schema requires the key unconditionally the NACK will name it.
- **`Priced` and `Expected` are not offered as `deal_status`.** They occur in the data but not in
  the platform's own drop-down, so they are read as derived states.
- **The engine is not seeded**, matching the other three; `_generate.py` is deterministic and never
  runs at runtime.

### Validation

- `tests/test_munis.py` — 78 payloads a run, 624 asserted clean across 8 consecutive runs (the
  generator is unseeded, so one pass proves one draw). Every §21.6 rule is recomputed independently
  of `check_payload`, and `check_payload` is then required to be silent.
- Payload field set diffed against the capture: **identical at every level** (envelope, DETAILS,
  SERIES, TRANCHE, SECURITY), with the single §21.3.5 exception.
- `npm run build` clean. `/api/munis/{run,stream,stop,status}` present in the OpenAPI schema.
- Observed `Various` deal-rating rate 4–13% across runs, against 10.5% (10/95) in the source.

### Open items

- **Nothing has been posted live.** `EVENT_CREATE_NEW_MUNIS_ISSUANCE` wire acceptance, the §21.3.5
  omission, and every vocabulary value are unconfirmed. An ACK would not confirm the vocabulary
  values in any case (§21.5.5) — only the §21.6 assertions speak to whether the data is right.
- Munis is not wired into email generation (§17) or expectation capture (§18); those remain
  Bonds-only and Bonds/Loans-only respectively.
- `SECURITY_TYPE`, `PUT_DATE`, `MANDATORY_TENDER`, `SINKING_FUND_SCHEDULE` and the four
  `MATURITY_OVERRIDE_*` columns exist in `MUNIS_DATA.csv` but not in the captured payload, so they
  are not emitted. If the schema accepts them they are the obvious next increment — a sinking-fund
  schedule in particular is derivable from the ladder already being generated.

### Changes — 2026-09-15 (later) — Munis roadshow control + MANDATE_TEXT

**Live NACK:** `Mandate text is required for Roadshow.` — a `StandardError` business rule (no PATH),
on the first live Munis run.

Cause: `IS_ROADSHOW` was carried over from ABS as a bare 20% draw without the rule that goes with
it. ABS §20.3.8 already records that `MANDATE_TEXT` is conditionally mandatory on a roadshow deal,
and CLAUDE.md already warns that *a randomised field makes a deterministic schema rule look
intermittent*. Both applied here and neither was applied. The lesson generalises: **when copying a
randomised flag between engines, copy its conditional rules first** — the flag is the cheap half.

Fixes:

- `MANDATE_TEXT` is emitted whenever `IS_ROADSHOW` is true, and omitted (not sent empty) otherwise,
  mirroring `securitized_engine`.
- New **`roadshow`** parameter — `""`/`random` (draw ~20%), `yes`, `no`; accepts real booleans too.
  Exposed as a Munis tab dropdown. This is what makes the rule testable on demand rather than
  1-in-5.
- `check_payload` gains the conditional check, so a roadshow deal missing mandate text fails
  pre-flight instead of burning a POST.
- `tests/test_munis.py` gains two shapes pinning both branches, and asserts presence as an
  **equivalence** (`MANDATE_TEXT` present *iff* `IS_ROADSHOW`) — sending it on a non-roadshow deal
  is the other way to get this wrong. 9 shapes x 6 repeats = 102 payloads a run, 5 runs clean.

Spec §21.6.13 records the rule; §21.4 gains the parameter.
