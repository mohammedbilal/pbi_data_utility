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
│  Tabs: Bonds | Loans | Interest | CSV Upload | History | Settings │
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
│  /api/bonds/run           POST → starts bonds engine thread  │
│  /api/bonds/stream/:id    GET  → SSE log stream              │
│  /api/bonds/stop/:id      POST → signals stop event          │
│  (same pattern for /api/loans, /api/interest, /api/csv_upload)│
│  /api/csv_upload/run      POST (multipart) → file + params   │
│  /api/history             GET / POST  history.json           │
│                                                             │
│  engines/                                                   │
│    bonds_engine.py    (adapted deal_poster_url_auth_v3.py)  │
│    loans_engine.py    (adapted loan multi-module project)   │
│    interest_engine.py (adapted capture_interest.py)         │
│    csv_engine.py      (adapted csv_to_json_publisher_V4.py) │
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
3. `src/api.js` — thin fetch wrappers: `getConfig`, `saveConfig`, `startRun`, `startCsvRun` (multipart), `stopRun`, `createEventSource`, `getHistory`, `addHistory`
4. `src/hooks/useRunState.js` — shared hook: manages `running`, `logs`, `summary`, `error`, `runId`, `status`; opens/closes EventSource; fires `onFinish` to record history; accepts optional `startFn` override for non-JSON start calls
5. `src/components/Header.jsx` — logo mark + subtitle + environment dropdown + host badge
6. `src/components/LogViewer.jsx` — monospace terminal panel, auto-scrolls to bottom
7. `src/components/LiveOutput.jsx` — right-hand console card: run-id + status chip + summary stats + LogViewer
8. `src/tabs/BondsTab.jsx` — bonds parameter form (force-currency override + dry-run toggle)
9. `src/tabs/LoansTab.jsx` — loans parameter form (dry-run toggle, currency override; Delay / Deal-query-wait stacked vertically)
10. `src/tabs/InterestTab.jsx` — interest capture form (sizing rules, quantity ranges, strategy mode, dry-run toggle)
11. `src/tabs/CsvUploadTab.jsx` — CSV/Excel upload tab: drop zone, row range, delay, simulation/time-scale, dry-run; multipart POST
12. `src/tabs/HistoryTab.jsx` — run-history table; re-run (↻) prefills the tool's params
13. `src/tabs/SettingsTab.jsx` — environment CRUD, credentials, reference data paths
14. `src/App.jsx` — tab router, loads config on mount, persists env switch, wires re-run prefill + toast

### Phase 4 — Startup scripts

The original `start_backend.bat` / `start_frontend.bat` pair was replaced by a no-terminal launch flow driven from the project root:

- `Setup.bat` — one-time: creates `backend\.venv`, installs `requirements.txt`, runs `npm install`
- `Launch.vbs` — double-click entry point; runs `Start-App.ps1` hidden (no console)
- `Start-App.ps1` — starts uvicorn + Vite hidden, waits for both ports, opens the browser (see spec §12.1 for PATH/npm/IPv6 robustness guards)
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

### Form state persisted to localStorage (added 2026-06-26)

All four run-parameter tabs (Bonds, Loans, Interest, CSV Upload) save their field values to `localStorage` on every change and read them back on mount, between the history-prefill priority and the `environments.json` defaults priority. File selections on CSV Upload are not persisted (a `File` object is not serialisable).

### Re-run prefills, never auto-fires (added 2026-06-22)

The History ↻ action switches to the tool's tab and prefills the stored params but does **not** start the run — re-running posts real test data, so the user must click Run deliberately. (The prototype auto-fired.)

### Broker-email generation via Outlook COM (added 2026-07-21)

Bonds & Loans can render each generated deal as a broker-style email and send it to a Settings-configured recipient, to feed the Genesis email-parsing POC (see spec §17). Decisions:

- **Outlook Classic COM (`pywin32`) over SMTP.** The near-term workflow is "email lands in my inbox → I save the `.msg` and upload it", and there is no monitored mailbox yet. Sending through the user's logged-in Outlook profile needs no SMTP relay/allow-listing and produces a genuine Outlook message. COM is initialised per-thread since engines run on a background thread. Falls back to a skip-with-warning if Outlook/pywin32 is absent.
- **Three modes threaded through `params`** (`email_mode` = `off`/`both`/`email_only`, plus `email_format`). No new endpoints or router logic — email config is injected server-side in `_shared.py` from the top-level `email` block, exactly like `ref_dir`. `email_only` skips auth and POST.
- **Template library, one renderer per bank/desk format** (`email_builder.py`), because the POC samples vary widely by sender. Gap fields use fixed **boilerplate** and small **random pools** (per the user's choice). Multi-deal digest formats (bank calendars) are deferred.
- One email per **deal** (multi-tranche deals produce one email covering all tranches).

### Email comparison / LLM accuracy harness (specified 2026-08-06, not built)

The utility becomes the **source of ground truth** for the email-parsing agent: when it generates an email (§17) it also writes the *expected* database state for that email into its own mirror tables, so the rows the agent actually produced can be diffed against it and scored. See spec §18. Decisions:

- **Mirror the four app tables, keep the column names identical** — `util_issuance_deal`, `util_issuance_data`, `util_issuance`, `util_issuance_security`. Prefix (not a `_util` suffix) so they sort together, never match a `t_%` pattern used by app tooling, and diff column-for-column against `t_*` with no aliasing. Metadata rides in `util_`-prefixed extra columns (`util_run_id`, `util_source`, `util_match_key`, …).
- **One set of tables, three lanes** — `util_source` ∈ `expected` (utility) / `baseline` (current pipeline, the diagram's top lane) / `actual` (agent). Every comparison is then a self-join, and any pair can be scored by the same code.
> **Superseded 2026-08-07 (decision D9, spec §18.14).** The bullets below describe the design as
> built across sessions S1–S8. On seeing the finished tab the user's verdict was *"the entire design
> is a complete mess … I am not sure how to use it and it looks very complicated"*, and step 15 cuts
> it back to expected-vs-actual with one accuracy figure and a four-word verdict set. **Removed:**
> the baseline lane and three-way verdicts, the calibration / override / known-differences loop, LLM
> run metadata and the cost leaderboard, the relational assertions, and seven of the eight scores.
> What survives: capture, the field map, score-only-what-the-email-said, row matching, the
> normalizers, the read-only DB fetch and CSV import. The bullets are kept because each records
> *why* a piece existed, which is what makes it safe to delete — and because the failure is worth
> remembering: every addition was individually defensible and individually agreed, and the total was
> still unusable. An accuracy harness earns its complexity only when someone can read its output
> without being taught it.

- **All three lanes ship in v1.** The payoff is **three-way verdicts** — `AGENT_ERROR` (both references agree, the model is wrong) vs `CALIBRATION` (pipeline and agent agree, *our expectation* is wrong) vs `AGENT_BETTER`. Without the baseline every disagreement is an undifferentiated MISMATCH, and early on the wrong-expectation kind dominates, so the harness would read as model failure when it is really spec drift.
- **Same ticker in every lane; `datasource_id` does the separating.** A ticker identifies the issuer, so email and API POST must carry the *same* one — different tickers would be different issuers and misbehave in the app. Lanes are told apart by `datasource_id` (`LLM` = agent, `BBG` = current pipeline; confirmed the app does not overwrite it). Unique-per-run tickers still apply: one fresh ticker per run, shared by both lanes, so repeat runs of an issuer don't contaminate each other.
- **`t_issuance_data` is the scoring surface, by design.** Because it is append-only per update with a `datasource_id` on every row, both lanes stay attributable there even if the app associates the two ingests into one deal. That is not a compromise — it is the widest of the four tables and carries every field in the comparison map, so three-lane scoring and the whole calibration loop run on it with nothing omitted. `t_issuance` / `t_issuance_deal` / `t_issuance_security` hold current state and rollups, so a blended row belongs to neither lane; they are scored two-way from email-only runs, and blend detection restricts baseline scoring with a warning naming the excluded tables.
- **Association / merge testing is backlog.** Email first, *then* a `BBG` POST for the same deal, to check the app associates rather than duplicates (`deal_closest_match`), is its own scenario: sequenced emission with a wait, per-column merge-precedence expectations, and its own assertions (one `deal_id`, tranche count didn't double, both audit rows retained). v1 neither tests nor prevents association — blend detection keeps it from contaminating the numbers.
- **Calibration is a loop, not a report.** Each `CALIBRATION` finding is resolvable in the UI: *accept baseline* writes a reversible, audited override (`util_map_override`) so future expectations use the app's value, or *keep expectation* records a suspected app defect. The effective map = seed CSVs + active overrides, and re-scoring applies corrections to historical runs — so calibrating today improves yesterday's numbers instead of invalidating them.
- **Actual rows come from a read-only DB query in v1** (credentials are available): `SELECT` by ticker + ingest window, lanes assigned by `datasource_id`, bound parameters only, statement timeout and row cap, no write path. CSV import stays as the offline/hand-off route.
- **SQLite (`backend/expected/pbi_util.db`) as the system of record**, with an optional Postgres mirror. Capture needs no credentials and no network, so it works in `dry_run` / `email_only`, and the utility can never write to the environment under test. Actual rows come in either from a read-only Postgres connection or from a CSV upload in the same shape as the `Sample_issuance*.csv` exports.
- **Schema is data, not code** — the four `information_schema` exports (`*_fields_size.csv`) ship as reference data and drive DDL generation, column-length validation of generated values, and the comparison surface. The payload→column mapping lives in `field_map.csv` (tier + normalizer per column), so ABS/Munis are added by authoring rows, not by editing Python.
- **Score only what the email rendered.** Each renderer declares its rendered field set; the headline *extraction accuracy* is measured over that set, with a second *coverage accuracy* over everything the utility intended. The gap between them is a template bug, not a model failure.
- **Never simplify the email to make the diff easier.** Where the email states prose and the app stores a code (use-of-proceeds classification, splitting `Baa2/Stable` into rating + outlook), the translation *is* the capability under test. The templates keep their prose and the **expectation** holds the post-normalization value, with `vocab_map.csv` as the generated-text → expected-DB-value authority. (Decision D3 — reverses an earlier "restrict the pools" recommendation.)
- **Email-ingested rows are stamped `LLM`** — `datasource_id` / `deal_id_datasource` / `issuance_created_by`, which also filters match candidates.
- **Business-key row matching** (ISIN/CUSIP → ticker+currency+tenor → ticker+maturity+size), because the agent's rows carry app-assigned ids. A new "unique ticker per run" option on the Bonds tab makes this unambiguous.
- **`expected_writer.py` and `comparators.py` are pure** (no I/O), mirroring `email_builder.py`, so both are testable in dry run. Capture failures are logged as warnings and never fail a run.
- **LLM run metadata is first-class** (`util_llm_run`: provider / model / prompt version / method / tokens / cost / latency), entered manually or imported, so accuracy can be ranked against cost and latency across configurations.

- **Built across fresh chats, with the repo as the only shared state.** 11 steps grouped into 7 sessions (§18.16). Each build chat reads the spec, builds only its named steps, verifies them, flips the status rows, adds an `implementation.md` entry, and emits a completion report ending with a self-contained hand-off prompt for the next session (§18.17). Nothing relies on chat history, so a session can be re-run or picked up cold. Deviations must be written back into §18 in the same session — an unrecorded deviation silently breaks every later session that trusted the spec.

Build order, per-step definition of done, and decisions: spec §18.16, §18.17 and §18.14.

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
│   ├── expected_store.py      ← util_* mirror tables (SQLite) — DDL/insert/export (added 2026-08-06)
│   ├── comparators.py         ← [planned §18] normalizers, row matching, scoring, assertions
│   ├── db_reader.py           ← [planned §18] read-only Postgres fetch of the four app tables
│   ├── environments.json
│   ├── history.json           ← persisted run records (created on first run)
│   ├── requirements.txt
│   ├── expected\              ← pbi_util.db (created on first capture)
│   ├── reference\
│   │   ├── bonds\ · loans\
│   │   └── db_schema\             ← *_fields_size.csv + field_map.csv + vocab_map.csv (added 2026-08-06)
│   ├── engines\
│   │   ├── bonds_engine.py
│   │   ├── loans_engine.py
│   │   ├── interest_engine.py
│   │   ├── csv_engine.py          ← CSV/Excel → JSON publisher (added 2026-06-26)
│   │   ├── email_builder.py       ← broker-email HTML renderers + boilerplate/random pools (added 2026-07-21)
│   │   ├── outlook_sender.py      ← Outlook COM send / .msg save (added 2026-07-21)
│   │   ├── expected_writer.py     ← [planned §18] payload → expected table rows (pure)
│   │   └── loan_utils\
│   │       ├── cusip.py
│   │       └── dates.py
│   └── routers\
│       ├── _shared.py
│       ├── bonds.py
│       ├── loans.py
│       ├── interest.py
│       ├── csv_upload.py          ← multipart upload endpoint (added 2026-06-26)
│       ├── config_router.py
│       ├── compare_router.py      ← [planned §18] /api/compare
│       └── history_router.py      ← GET / POST /api/history
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
│           ├── CsvUploadTab.jsx   ← CSV Upload tab (added 2026-06-26)
│           ├── EmailCompareTab.jsx ← [planned §18] Email Compare tab (Bonds · Loans · ABS · Munis)
│           ├── HistoryTab.jsx
│           └── SettingsTab.jsx
└── (root launch scripts listed at top)
```
