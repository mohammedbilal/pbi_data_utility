# PBI Test Utility — Implementation Plan

## Goals

1. Combine three standalone scripts into one unified application
2. Provide a React UI so non-technical users can run each tool without touching the CLI
3. Centralise environment/credential management — one place to switch between QA, UAT, etc.
4. Stream live output back to the browser so users can see progress event-by-event

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (React / Vite)                                     │
│                                                             │
│  Header [logo · env dropdown · host badge]                  │
│  Tabs: Bonds | Loans | Interest | History | Settings        │
│                                                             │
│  Run tabs:                                                  │
│    Left — parameter form + Run/Stop buttons                 │
│    Right — live output: run-id + status chip + summary +    │
│            log viewer (SSE stream)                          │
│  History tab — table of past runs (re-run prefills params)  │
└───────────────────────┬─────────────────────────────────────┘
                        │ HTTP + SSE  (proxied via Vite → :8000)
┌───────────────────────┴─────────────────────────────────────┐
│  FastAPI backend  (:8000)                                   │
│                                                             │
│  /api/config           GET / PUT  environments.json         │
│  /api/bonds/run        POST → starts bonds engine thread    │
│  /api/bonds/stream/:id GET  → SSE log stream                │
│  /api/bonds/stop/:id   POST → signals stop event            │
│  (same pattern for /api/loans and /api/interest)            │
│  /api/history          GET / POST  history.json             │
│                                                             │
│  engines/                                                   │
│    bonds_engine.py    (adapted deal_poster_url_auth_v3.py)  │
│    loans_engine.py    (adapted loan multi-module project)   │
│    interest_engine.py (adapted capture_interest.py)         │
└─────────────────────────────────────────────────────────────┘
```

---

## Build Order

### Phase 1 — Backend skeleton

1. Create `C:\python\PBI_Test_Utility\` directory structure
2. Write `config_manager.py` — load/save `environments.json`
3. Write `run_manager.py` — track active runs (queue + stop event per run)
4. Write `main.py` — FastAPI app with CORS, mount routers
5. Write `routers/_shared.py` — reusable `start_tool_run`, `stream_tool_run`, `stop_tool_run`
6. Write `routers/bonds.py`, `routers/loans.py`, `routers/interest.py`, `routers/config_router.py`
7. Write `requirements.txt`
8. Create `.venv` and install deps

### Phase 2 — Engines

Each engine is a standalone function `run_<tool>(params, env, stop_event, log_queue)`.  
It puts `{"type": "log", "level": "...", "msg": "..."}` dicts into `log_queue` instead of printing.  
It checks `stop_event.is_set()` before each HTTP call and between delay sleeps.  
It puts `None` as a sentinel when done.

**Bonds engine** — adapted from `deal_poster_url_auth_v3.py`:
- Add login step (original used a hardcoded token)
- Make `RefData` take an explicit `ref_dir: Path` parameter
- Replace `print()` → `log_queue.put()`
- Replace `time.sleep()` → `_interruptible_sleep()` with stop event checks

**Loans engine** — inlined from all loan modules:
- Inline `DataGenerator`, auth, publish, deal_query into one file
- Same logging and stop-event pattern
- Dry-run support preserved

**Interest engine** — adapted from `capture_interest.py`:
- Extract all pure functions unchanged
- Add both `SESSION_AUTH_TOKEN` and `REFRESH_AUTH_TOKEN` extraction
- Replace `print()` → `log_queue.put()`
- Replace `time.sleep()` → `_interruptible_sleep()`

### Phase 3 — Frontend

1. `package.json` + `vite.config.js` — Vite + React, proxy `/api` → `localhost:8000`
2. `index.css` — Operator design system (dark, mono-forward), colour-coded log levels, toggle switches
3. `src/api.js` — thin fetch wrappers: `getConfig`, `saveConfig`, `startRun`, `stopRun`, `createEventSource`, `getHistory`, `addHistory`
4. `src/hooks/useRunState.js` — shared hook: manages `running`, `logs`, `summary`, `error`, `runId`, `status`; opens/closes EventSource; fires `onFinish` to record history
5. `src/components/Header.jsx` — logo mark + subtitle + environment dropdown + host badge
6. `src/components/LogViewer.jsx` — monospace terminal panel, auto-scrolls to bottom
7. `src/components/LiveOutput.jsx` — right-hand console card: run-id + status chip + summary stats + LogViewer
8. `src/tabs/BondsTab.jsx` — bonds parameter form (force-currency override + dry-run toggle)
9. `src/tabs/LoansTab.jsx` — loans parameter form (dry-run toggle, currency override; Delay / Deal-query-wait stacked vertically)
10. `src/tabs/InterestTab.jsx` — interest capture form (sizing rules, quantity ranges, strategy mode, dry-run toggle)
11. `src/tabs/HistoryTab.jsx` — run-history table; re-run (↻) prefills the tool's params
12. `src/tabs/SettingsTab.jsx` — environment CRUD, credentials, reference data paths
13. `src/App.jsx` — tab router, loads config on mount, persists env switch, wires re-run prefill + toast

### Phase 4 — Startup scripts

The original `start_backend.bat` / `start_frontend.bat` pair was replaced by a no-terminal launch flow driven from the project root:

- `Setup.bat` — one-time: creates `backend\.venv`, installs `requirements.txt`, runs `npm install`
- `Launch.vbs` — double-click entry point; runs `Start-App.ps1` hidden (no console)
- `Start-App.ps1` — starts uvicorn + Vite hidden, waits for both ports, opens the browser (see spec §11.1 for PATH/npm/IPv6 robustness guards)
- `Stop-App.vbs` — double-click to stop both servers

---

## Key Design Decisions

### Credentials: two sets, not one

Bonds and loans authenticate as `RahulK`; interest capture authenticates as `TRPUser_QA`. This mirrors the existing script behaviour and is explicit in the Settings tab with two separate credential groups per environment.

### No `.env` file

The loans project originally used a `.env` file for secrets. The new utility stores everything in `environments.json` and exposes it through the Settings tab UI. This makes environment switching a one-click action rather than editing hidden files.

### SSE over WebSockets

Server-Sent Events (one-way server→client stream) is sufficient for log streaming and simpler to implement than WebSockets. The browser's native `EventSource` API handles reconnection automatically.

### One run per tool at a time

Prevents accidental flooding of the test environment. Attempting to start a second run returns HTTP 409. The Stop button lets the user cancel mid-run cleanly.

### Interruptible sleep

`time.sleep(delay)` is replaced with a loop that sleeps 0.1s at a time and checks `stop_event.is_set()`. This ensures a Stop click is honoured within 0.1s rather than waiting for the full delay to expire.

### Absolute imports in backend

All backend files use absolute imports (e.g. `from engines.bonds_engine import run_bonds`) rather than relative imports. This allows `uvicorn main:app` to be run directly from the `backend/` directory without needing a parent package.

### Reference data stays in original locations

The bonds and loans reference CSV files remain in their original directories. The paths are configurable in the Settings tab. No data migration required.

### Run history persisted server-side (added 2026-06-22)

The redesign added a History tab. History could live in browser `localStorage` (fastest, zero backend) or in a backend store (shared, durable). We chose a **backend JSON store** (`history.json` via `history_manager.py` + `/api/history`) so the QA team shares one history and records survive browser/cache resets. Records are still *written* from the frontend on run completion (the engine layer doesn't know the env name or a params summary); the backend only persists and prunes them. See spec §12.

### Re-run prefills, never auto-fires (added 2026-06-22)

The History ↻ action switches to the tool's tab and prefills the stored params but does **not** start the run — re-running posts real test data, so the user must click Run deliberately. (The prototype auto-fired.)

### Env switch persists (added 2026-06-22)

Switching environment in the header now writes `active` back to `environments.json` (`PUT /api/config`), so the selection survives a reload — previously it was local React state only.

---

## SSE Flow (detailed)

```
Frontend                           Backend
   |                                  |
   | POST /api/bonds/run {params}      |
   |--------------------------------->|
   |                                  | create run_id
   |                                  | start Thread(engine)
   | <-- {run_id}                      |
   |                                  |
   | GET /api/bonds/stream/{run_id}   |
   |--------------------------------->|
   |                                  | loop: q.get(timeout=0.5)
   | <-- data: {"type":"log",...}      |   engine puts log messages
   | <-- data: {"type":"ping"}        |   (timeout → ping)
   | <-- data: {"type":"log",...}      |
   | <-- data: {"type":"summary",...} |   engine puts summary
   | <-- data: {"type":"done"}        |   engine puts None sentinel
   |                                  |
   | [EventSource closes]             |
```

---

## File Map

```
C:\python\PBI_Test_Utility\
├── CLAUDE.md              ← guidance for Claude Code / contributors (project root)
├── Setup.bat              ← one-time setup (venv + pip + npm install)
├── Launch.vbs             ← double-click to start (hidden)
├── Start-App.ps1          ← backend + frontend launcher (called by Launch.vbs)
├── Stop-App.vbs           ← double-click to stop
├── master\
│   ├── spec.md            ← authoritative requirements
│   ├── plan.md            ← this file
│   └── implementation.md  ← what was built, decisions, open items
├── backend\
│   ├── main.py
│   ├── config_manager.py
│   ├── run_manager.py
│   ├── history_manager.py     ← run-history JSON store (add/list + retention)
│   ├── environments.json
│   ├── history.json           ← persisted run records (created on first run)
│   ├── requirements.txt
│   ├── engines\
│   │   ├── bonds_engine.py
│   │   ├── loans_engine.py
│   │   ├── interest_engine.py
│   │   └── loan_utils\
│   │       ├── cusip.py
│   │       └── dates.py
│   └── routers\
│       ├── _shared.py
│       ├── bonds.py
│       ├── loans.py
│       ├── interest.py
│       ├── config_router.py
│       └── history_router.py   ← GET / POST /api/history
├── frontend\
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── src\
│       ├── main.jsx
│       ├── App.jsx
│       ├── index.css
│       ├── api.js
│       ├── hooks\
│       │   └── useRunState.js
│       ├── components\
│       │   ├── Header.jsx
│       │   ├── LogViewer.jsx
│       │   └── LiveOutput.jsx
│       └── tabs\
│           ├── BondsTab.jsx
│           ├── LoansTab.jsx
│           ├── InterestTab.jsx
│           ├── HistoryTab.jsx
│           └── SettingsTab.jsx
└── (root launch scripts listed at top)
```
