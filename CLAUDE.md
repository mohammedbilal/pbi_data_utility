# CLAUDE.md

Guidance for Claude Code (and human contributors) working in this repository.

## What this is

**PBI Test Utility** — a test-data generator for the Genesis Global TrOWE Primary Bond Issuance (PBI) platform. It unifies three previously separate scripts behind one app:

- **Bonds** — posts `EVENT_NEW_ISSUANCE_DATA` events
- **Loans** — posts `EVENT_CREATE_NEW_LOAN_ISSUANCE` events (with multi-tranche linkage via `ALL_LOAN_DEAL`)
- **Interest Capture** — posts `EVENT_INTEREST_CAPTURE` events

It is a **FastAPI backend** (`:8000`) + **Vite/React frontend** (`:5173`) that stream live per-event logs to the browser over SSE. The UI uses the **Operator design system** (dark, mono-forward terminal aesthetic, sky accent — tokens in `frontend/src/index.css`). Tabs: **Bonds · Loans · Interest Capture · History · Settings**. The **History** tab lists past runs (persisted server-side) and re-runs them by prefilling params.

> The authoritative docs live in `master/` — read these first:
> - `master/spec.md` — requirements, payloads, parameters, launch behaviour
> - `master/plan.md` — architecture and build order
> - `master/implementation.md` — what was built, decisions, open items, and a dated changelog

## Run / setup

This is a Windows, no-terminal app launched from this folder by double-click:

| Action | Script |
|---|---|
| First-time setup (venv + pip + `npm install`) | `Setup.bat` |
| Start the app (hidden, opens browser) | `Launch.vbs` |
| Stop the app | `Stop-App.vbs` |

`Launch.vbs` runs `Start-App.ps1` hidden, which starts both servers, waits for ports, and opens `http://localhost:5173`.

**Manual dev (for debugging):**
```powershell
# Backend — MUST run from backend/ (absolute imports; app is main:app)
cd backend
.\.venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000

# Frontend
cd frontend
npm run dev        # Vite dev server on 5173, proxies /api -> :8000
npm run build      # production build sanity check (no terminal use in normal flow)
```

## Architecture & conventions

- **Backend imports are absolute** (`from engines.bonds_engine import run_bonds`), so uvicorn must be started from inside `backend/`. Do not reintroduce relative imports.
- **Engine contract** — each engine exposes `run_<tool>(params, env, stop_event, log_queue)`. It:
  - puts `{"type":"log","level":..., "msg":...}` dicts on `log_queue` (never `print()`),
  - checks `stop_event.is_set()` before each HTTP call and inside delays (use `_interruptible_sleep`),
  - emits `{"type":"summary","success","failed","total"}` then `None` (sentinel) when done.
- **Routers are thin** — `routers/_shared.py` handles run creation, the SSE stream, and stop. New per-tool params just flow through the `params` dict; no router changes needed.
- **Log levels** rendered by `LogViewer`: `info`, `success`, `warn`, `error` (mapped to Operator colours in `index.css`).
- **Config** — everything (host, credentials, `verify_ssl`, reference dirs, tool defaults) lives in `backend/environments.json`, edited via the Settings tab / `PUT /api/config`. No `.env` files. Passwords are plain text (test envs only). The header env switch persists `active` via `PUT /api/config`.
- **Reference data** — CSVs under `backend/reference/{bonds,loans}/`; all optional, with hardcoded fallbacks if missing.
- **Run history** — persisted server-side in `backend/history.json` (`history_manager.py` + `routers/history_router.py`, `GET`/`POST /api/history`). The record is **written by the frontend** on run completion (the engine layer doesn't know the env name or a params summary): `useRunState(tool, onFinish)` fires `onFinish` once on `done`/`stop`, and each tab POSTs the record via `addHistory`. Re-run is prefill-only (never auto-fires). See spec §12.

## Gotchas (learned the hard way — see implementation.md 2026-06-19)

- **Launch / PATH:** `Start-App.ps1` refreshes `$env:Path` from the registry and resolves `node.exe`/`npm.cmd` by absolute path. A double-click inherits the Windows session's PATH, which may be stale (e.g. Node installed after login) — never assume `node`/`npm` is on PATH. For speed the frontend is launched as `node …\vite\bin\vite.js` directly (npm is only a fallback), and both ports are awaited concurrently at 150ms granularity — reaching the browser in ~1.5–2s. See spec §11.2.
- **IPv6 port check:** Vite binds `[::1]:5173` (IPv6), uvicorn binds `127.0.0.1` (IPv4). Any port-readiness probe must try **both** addresses.
- **String-typed payload fields:** the server validates several numeric-looking fields with a strict `oneOf: [string, null]`. Send them as **strings**, not numbers. Currently stringified in `loans_engine`: `SPREAD`, `FINAL_SPREAD`, `ORIGINAL_ISSUE_DISCOUNT` (OID), `FINAL_OID`, `YIELD`, `FINAL_YIELD`. `ISSUE_SIZE`/`FINAL_SIZE` stay numbers. A NACK like `$.DETAILS.X: must be valid to one and only one schema, but 0 are valid` means field `X` needs the string treatment.
- **NACK logging (Loans):** the engine logs the full `ERROR` array, not just the first — read all `also —` lines before concluding a fix is complete.
- **Dry-run** (all three tabs): skips auth **and** POST, and dumps the full generated payload as pretty JSON to the log. Use it to inspect payloads without credentials/network. Loans multi-tranche dry-run won't show `MASTER_LOAN_ID` on tranches 2+ (it normally comes from the live `ALL_LOAN_DEAL` query).

## When making changes

- Keep `master/*.md` in sync — especially `implementation.md` (add to the dated changelog) and `spec.md` (parameters/behaviour).
- After frontend edits, run `npm run build` to catch JSX errors.
- After engine edits, smoke-test in **dry-run** (no server needed): instantiate the engine with a `queue.Queue` + `threading.Event` and `dry_run: True`, then inspect the emitted payloads.
- Prefer threading new behaviour through the existing `params` dict over adding endpoints.
