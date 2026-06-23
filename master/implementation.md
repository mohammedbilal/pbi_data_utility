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
| `engines/loan_utils/cusip.py` | Done | Copied from loan project |
| `engines/loan_utils/dates.py` | Done | Copied from loan project |
| `routers/_shared.py` | Done | Reusable start/stream/stop logic |
| `routers/bonds.py` | Done | |
| `routers/loans.py` | Done | |
| `routers/interest.py` | Done | |
| `routers/config_router.py` | Done | GET + PUT /api/config |
| `routers/history_router.py` | Done | GET + POST /api/history (added 2026-06-22) |
| `history_manager.py` | Done | JSON-persisted run history (`history.json`); add/list with retention (added 2026-06-22) |

### Frontend (Vite + React)

| File | Status | Notes |
|---|---|---|
| `package.json` | Done | React 18, Vite 5 |
| `vite.config.js` | Done | Proxy `/api` → `localhost:8000` |
| `src/index.css` | Done | Operator design system — dark, mono-forward theme (rewritten 2026-06-22) |
| `src/App.jsx` | Done | Tab router; History tab, env-switch persistence, re-run prefill, toast (2026-06-22) |
| `src/api.js` | Done | Thin fetch wrappers; `getHistory`/`addHistory` added (2026-06-22) |
| `src/hooks/useRunState.js` | Done | Run/stop/SSE state hook; exposes `runId`/`status` + `onFinish` callback (2026-06-22) |
| `src/components/Header.jsx` | Done | Logo mark + "DATA INSERTION CONSOLE" subtitle, env dropdown + host badge (2026-06-22) |
| `src/components/LogViewer.jsx` | Done | Terminal log treatment, auto-scroll, colour-coded levels |
| `src/components/LiveOutput.jsx` | Done | Right-hand console card: run-id + status chip + stats strip + LogViewer (added 2026-06-22) |
| `src/tabs/BondsTab.jsx` | Done | Force-currency on its own row above Delay, with hint (2026-06-22); dry-run toggle (2026-06-19) |
| `src/tabs/LoansTab.jsx` | Done | Dry-run toggle + currency override; Delay / Deal-query-wait stacked vertically (2026-06-19) |
| `src/tabs/InterestTab.jsx` | Done | Sizing rules + quantity ranges + strategy mode + dry-run toggle (2026-06-19) |
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
- See spec §8.1 for behaviour and the Loans `MASTER_LOAN_ID` caveat.

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

Measured end-to-end from a hidden launch: both ports up at ~1.6s, browser opens at ~1.7s (verified HTTP 200 on both). See spec §11.2.

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
