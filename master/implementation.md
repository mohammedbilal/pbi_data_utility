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
