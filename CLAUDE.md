# CLAUDE.md

Guidance for Claude Code (and human contributors) working in this repository.

## What this is

**PBI Test Utility** — a test-data generator for the Genesis Global TrOWE Primary Bond Issuance (PBI) platform. It unifies three previously separate scripts behind one app:

- **Bonds** — posts `EVENT_NEW_ISSUANCE_DATA` events
- **Loans** — posts `EVENT_CREATE_NEW_LOAN_ISSUANCE` events (with multi-tranche linkage via `ALL_LOAN_DEAL`)
- **Securitized (ABS)** — posts `EVENT_CREATE_NEW_SECURITIZED_ISSUANCE` events (one four-level tree per deal)
- **Munis** — posts `EVENT_CREATE_NEW_MUNIS_ISSUANCE` events (one deal tree per POST)
- **Interest Capture** — posts `EVENT_INTEREST_CAPTURE` events

It is a **FastAPI backend** (`:8000`) + **Vite/React frontend** (`:5173`) that stream live per-event logs to the browser over SSE. The UI uses the **Operator design system** (dark, mono-forward terminal aesthetic, sky accent — tokens in `frontend/src/index.css`). Tabs: **Bonds · Loans · Securitized · Munis · Interest Capture · TIG Orders · CSV Upload · Email Compare · History · Settings**. The **History** tab lists past runs (persisted server-side) and re-runs them by prefilling params.

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

**macOS** — double-click `Launch.command` (Finder runs `.command` files in Terminal); `Stop.command`
stops it. It is `Setup.bat` + `Launch.vbs` + `Start-App.ps1` in one file — first run builds the venv
and installs packages, then launches; later runs skip straight to launching. Same two servers, same
ports, same `http://localhost:5173` as Windows. Two things keep it working from a Windows checkout,
both in `.gitattributes` / the git index: **LF endings** (`*.command text eol=lf` — CRLF gives
`bad interpreter: /bin/bash^M`) and the **exec bit** (mode `100755`, since `core.filemode=false`
here). See spec §12.3.

**Docker (macOS / Linux / any OS)** — `docker compose up --build`, then `http://localhost:8000`.
One container, one process, one port: the frontend is built at image-build time and served by
FastAPI (`main.py` mounts `frontend/dist` at `/` *after* the routers, only if it exists), so there
is no Vite and no `/api` proxy. Mutable state (`environments.json`, `history.json`, `expected/`)
resolves through `backend/paths.py` → `$PBI_STATE_DIR`, which **defaults to the backend folder**,
so the Windows flow is unchanged; compose points it at bind-mounted `./data`. **Outlook email
sending does not work in the container** (Windows COM) — the engines log `✉ Email skipped` and
carry on, and expectation capture still fires. See spec §12.4.

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
- **Config** — everything (host, credentials, `verify_ssl`, reference dirs, tool defaults) lives in `backend/environments.json`, edited via the Settings tab / `PUT /api/config`. No `.env` files. Passwords are plain text (test envs only). The header env switch persists `active` via `PUT /api/config`. **`environments.json` is git-ignored** (2026-09-24) — the tracked file is `environments.example.json` (no passwords, ships a `Local` entry on `http://localhost:8080`), and `config_manager.SEEDS` seeds the real one on first start from: existing local copy → example → `_defaults()`. Reference dirs use the `{BACKEND_DIR}` placeholder, never absolute paths.
- **URLs** — `host_name` **carries the scheme**: an explicit `http://`/`https://` wins, https is only the default. One implementation, `engines/url_utils.py` (`normalize_host` / `join_url`); never write `f"https://{host}"` again. Two invariants: it **raises on an empty host**, so engines must build publish URLs only on the non-dry-run path (a dry run has to work with no environment — this is what broke `test_munis` when the helper landed); and `join_url` **passes the path through as written**, so `/gwf//EVENT_X` keeps its doubled slash. See spec §4.1.1.
- **Hot reload** — Vite HMR (frontend) has always been on; the backend now launches `uvicorn --reload` from both launchers, `PBI_NO_RELOAD=1` to opt out. Safe as a default because uvicorn watches `*.py` only, so Settings saves, history writes and expectation-DB writes cannot bounce the server mid-run. See spec §12.5.
- **Reference data** — CSVs under `backend/reference/{bonds,loans,securitized}/`; all optional, with hardcoded fallbacks if missing.
- **Expectation capture** (spec §18, Bonds only) — when email generation is on, `bonds_engine.emit_email` also writes the *expected* DB state into the `util_*` SQLite mirror (`expected_store.py` + `engines/expected_writer.py`). It is SQLite-only, works in `dry_run`/`email_only`, fires whether or not the send succeeded, and **must never fail a run** — every path is wrapped in `try/except` that logs a `warn`. `build_email` therefore returns **three** values: `(subject, body_html, meta)`, where `meta` carries `rendered_fields` (what the format actually printed) and `gap_fill` (the template's random picks, which the expectation needs).
- **Comparison surface** (spec §18.11) — `routers/compare_router.py` at `/api/compare` scores a capture run: `db_reader.py` pulls the rows the pipeline actually produced (**read-only** — `SELECT` with bound params, `SET TRANSACTION READ ONLY`, statement timeout, row cap, and a lazy `psycopg` import so a missing driver degrades to CSV import rather than erroring), `comparators.compare_run` judges them, and the pass is persisted to `util_comparison` / `util_comparison_field`. Two rules when extending it: load the field/vocab maps **once per request** with `effective_map()` and pass them as `maps=` (the comparator runs per row pair), and **never coerce a value** — everything is TEXT on both sides and the comparator absorbs `'True'`/`'true'`, `1000.00000`/`1000` and epoch-ms/ISO dates. `/compare` is idempotent on `(util_run_id, llm_run_id, against)`. The **Email Compare** tab (`EmailCompareTab.jsx`) is the face of it — plain REST, no SSE, since a compare pass is one request rather than a run.
- **Calibration loop** (spec §18.8.2) — a `CALIBRATION`/`INVESTIGATE` verdict means the expectation, not the agent, may be wrong. The queue and the override view are `components/compare/{CalibrationPanel,OverridesPanel}.jsx`; both are cross-run, so they live at the top level of the tab, not in the selected-run branch. Two invariants when touching this: **accept-baseline writes two overrides** on a non-`vocab` column (the vocab entry plus a `normalizer → vocab` override — the entry alone is inert), so they must be shown and reverted together; and **nothing re-scores by itself** — the UI offers `POST /compare` per affected run. Because a pass *replaces* its findings, `expected_store.add_comparison` snapshots and replays `keep_expectation` decisions onto the new ones (`_collect_decisions` / `_apply_decisions`); `accept_baseline` is deliberately not replayed — its durability comes from the override. The way back out is **panel 4c** (`KnownDifferencesPanel.jsx`, `GET /decisions` + `POST /decisions/{finding_id}/unkeep`): un-keeping clears the whole `(table, column, expected, baseline)` pair — clearing one sighting would just be undone by the replay — and needs **no re-score**, since the findings already exist.
- **LLM runs, leaderboard, artifact, export** (spec §18.12, §18.15 panels 5–6) — `util_llm_run` rows record one agent pass (provider · model · prompt_version · method + tokens/cost/latency) via `POST /api/compare/runs/{id}/llm_run`; a compare pass stores the `llm_run_id` it was scored under, and because `/compare` is idempotent on `(run, llm_run, against)` two configurations over one email set **coexist as two passes** — that is the leaderboard's axis, so any view of a run must say *which* pass it is showing (the tab pins the `comparison_id` rather than trusting `latest_comparison`). There is **no leaderboard endpoint**: `components/compare/shared.js` composes it from `GET /runs` + `GET /runs/{id}`. Panel 6 renders the stored email in a **sandboxed iframe** (`srcDoc`, no `allow-scripts`) — never `dangerouslySetInnerHTML`. `DELETE …/llm_run/{llm_run_id}` **refuses with 409** while passes are scored under the row (orphaning them would silently move them to the "no LLM run" bucket) — `?cascade=true` deletes those passes too. Export is `GET /api/compare/export/{id}`; a lane CSV needs a `table` and re-imports through the panel-4 drop zone into any lane, while **override export is client-side** from `GET /map` (no endpoint exists for it).
- **Securitized (ABS)** (spec §20) — the only tool whose payload is a four-level tree (`DETAILS → SERIES{} → TRANCHES{} → SECURITIES{}`) posted as **one request per deal**, so there is no linkage query and no per-tranche loop. Children are **maps keyed by `TEMP-<Level>-<n>`**; the keys are placeholders the app swaps for UUIDs, so what matters is that each key equals the node's own `*_ID` and every child repeats its ancestors' ids. Sizes, credit enhancement, net proceeds and ratings are **derived, not randomised** (§20.6) — a change that breaks the roll-up will pass ingest and fail downstream, so run the consistency assertions. Note credit enhancement is per **credit class**, not per tranche: `A-1`…`A-4` are one class and share a CE value. Reference data is 10 CSVs under `backend/reference/securitized/`, built by the checked-in one-off `_generate.py` (seeded, byte-stable, **never called at runtime**) from `_source/ABS_DropDowns.xlsx`; the engine itself is deliberately *not* seeded. `routers/securitized.py` is `routers/loans.py` with the tool renamed — nothing more (the read-only `GET /asset_types` route was removed 2026-09-09). **Deal identity is `DEAL_TYPE` (`ABS`/`CLO`/`CMBS`/`RMBS`), not asset type (changed 2026-09-09, spec §20.3.2):** `ASSET_TYPE` is no longer emitted on the Series or shown in the UI; the user picks `DEAL_TYPE`, which constrains the internal asset pick to matching `PRODUCT_GROUP` rows and drives the deal's **issuer sector — carried in the `INDUSTRY` field, not a new `ISSUER_SECTOR` property** (the closed schema NACKed `ISSUER_SECTOR`; live 2026-09-09) — plus nested `SUB_INDUSTRY`. Mappings are `securitized_engine.py::{DEAL_TYPE_SECTORS, SECTOR_SUB_INDUSTRY}`; a sector with no nested sub omits `SUB_INDUSTRY`. `DEAL_TYPE`'s own wire acceptance is not yet confirmed live.
- **Munis** (spec §21) — the fourth issuance tool, and the first whose reference data was **derived by analysing data** rather than transcribing a drop-down. The platform lookup defines only 4 vocabularies; everything else comes from `master/MUNIS_DATA.csv` (95 deals / 237 series / 2,142 maturities), and every rule in §21.6 carries the count that establishes it. The tree is `DETAILS → SERIES[] → TRANCHES[] → SECURITIES[]` — plain **arrays**, no `TEMP-*` keys and **no id fields at all** (contrast ABS §20.3.1); a `TRANCHE` is a **maturity on a serial ladder**, not a credit class. The envelope is its own: `SESSION_AUTH_TOKEN` and a **per-deal UUID `SOURCE_REF`** go in the **body** (the other three send a constant `12345` in headers only), and there is no `SERVICE_NAME`. The organising rule is **issuer-dependence** — state, sector, purpose, tax status, ratings, repayment source, enhancement and typical size are one `issuers.csv` row, drawn **once per deal** and then read off, never re-drawn. `_generate.py` rebuilds `reference/munis/` from the two `master/` CSVs (seeded, checked in, **never called at runtime**); the engine itself is deliberately not seeded.

- **Run history** — persisted server-side in `backend/history.json` (`history_manager.py` + `routers/history_router.py`, `GET`/`POST /api/history`). The record is **written by the frontend** on run completion (the engine layer doesn't know the env name or a params summary): `useRunState(tool, onFinish)` fires `onFinish` once on `done`/`stop`, and each tab POSTs the record via `addHistory`. Re-run is prefill-only (never auto-fires). See spec §12.

## Gotchas (learned the hard way — see implementation.md 2026-06-19)

- **Launch / PATH:** `Start-App.ps1` refreshes `$env:Path` from the registry and resolves `node.exe`/`npm.cmd` by absolute path. A double-click inherits the Windows session's PATH, which may be stale (e.g. Node installed after login) — never assume `node`/`npm` is on PATH. For speed the frontend is launched as `node …\vite\bin\vite.js` directly (npm is only a fallback), and both ports are awaited concurrently at 150ms granularity — reaching the browser in ~1.5–2s. See spec §11.2.
- **IPv6 port check:** Vite binds `[::1]:5173` (IPv6), uvicorn binds `127.0.0.1` (IPv4). Any port-readiness probe must try **both** addresses.
- **String-typed payload fields:** the server validates several numeric-looking fields with a strict `oneOf: [string, null]`. Send them as **strings**, not numbers. Currently stringified in `loans_engine`: `SPREAD`, `FINAL_SPREAD`, `ORIGINAL_ISSUE_DISCOUNT` (OID), `FINAL_OID`, `YIELD`, `FINAL_YIELD`. `ISSUE_SIZE`/`FINAL_SIZE` stay numbers. A NACK like `$.DETAILS.X: must be valid to one and only one schema, but 0 are valid` means field `X` needs the string treatment.
- **NACK logging (Loans):** the engine logs the full `ERROR` array, not just the first — read all `also —` lines before concluding a fix is complete.
- **Dry-run** (all three tabs): skips auth **and** POST, and dumps the full generated payload as pretty JSON to the log. Use it to inspect payloads without credentials/network. Loans multi-tranche dry-run won't show `MASTER_LOAN_ID` on tranches 2+ (it normally comes from the live `ALL_LOAN_DEAL` query). ABS dry-run has no such caveat — its payload is byte-identical to what would be posted.
- **ABS schema spellings are wrong on purpose:** `UNDERWRITING_DISC_AND_COMMISIONS` (one `S`), `ORIGINATOR_MOODY_RATING` (singular), `ORIGINATOR_SANDP_RATING` (not `SP`), `USER_OF_PROCEEDS` (not `USE_`), and `ACT/36S` in the day-count vocabulary. Do not "fix" them. Day counts are `ACT/360`, **not** `Actual/360`. The string-typed set also differs from Loans: `WAL`/`INTEREST_DUE`/`IPO_PRICE`/`PERCENT_CA`/`WALA`/the three `*_CIK`s are **strings**, while `WA_COUPON`/`NET_PROCEEDS`/`SPREAD_BPS`/`CREDIT_ENHANCEMENT_PCT` are **numbers** — see spec §20.3.6.
- **ABS field lists sourced from the UI will not match the wire.** The form labels the originator input *"Originator Name"* but the payload field is `ORIGINATOR` (spec §20.3.2). Take names from a captured payload, not a screenshot — the ABS schema is **closed** (`additionalProperties: false`), so a wrong name is a hard NACK naming the property. That same strictness makes it a field-name oracle: one candidate key per post identifies a field exactly, which is how `MANDATE_TEXT` was found.
- **An ABS ACK does not mean the data is right.** The handler validates property names, types, and presence — including one *conditional* rule: `MANDATE_TEXT` is mandatory when `IS_ROADSHOW` is true, and a roadshow deal without it NACKs `"Mandate Text is required"` (spec §20.3.8). It does **not** validate string *values*: `DAY_COUNT: "Actual/360"` and `PRODUCT_GROUP: "ZZ_NOT_A_PRODUCT_GROUP"` both ACK. So never use an ACK as evidence a vocabulary value is legal (§20.5.5), and never let a green run stand in for the §20.6 consistency assertions. Two NACK shapes to tell apart: `@type: FieldError` carries a `PATH` and is the schema layer; `@type: StandardError` is a business rule, arrives as 500, and has no path — if there's no path, look for a rule, not a malformed field.
- **Munis: `MATURITY_AMOUNT` is denominated in thousands.** `SERIES_SIZE == 1000 * sum(MATURITY_AMOUNT)` (234/235 real series exact) and `DEAL_SIZE == sum(SERIES_SIZE)`. Emitting both in the same unit **ACKs fine** and produces a deal 1,000x the size it claims — the first draft did exactly this. Both roll-ups now hold *by construction*: size is scaled from the issuer's `TYPICAL_SIZE` and allocated **down** the tree by `_split_quantised`, never adjusted afterwards. Run `tests/test_munis.py` after touching any of it.
- **Munis: derived fields that look drawable.** `PRICE` follows from coupon vs yield (premium ⇒ `yield < coupon`, par ⇒ `yield == coupon`; 1,323 and 433 observed rows, zero exceptions), `DELIVERY_DATE` follows from `DATED_DATE` (232/234), the deal rating is `Various` **iff** the series disagree (18/18), and `MATURITY_DESCRIPTION`'s *form* is chosen by tax status — all 61 `bps over yld` descriptions sit on a Taxable/Various series, none on a tax-exempt one. The **double space after `yld`** is in the source on all 61 rows; do not "fix" it. Drawing any of these independently is the failure mode the tool exists to avoid.
- **Munis: `Various` is a roll-up, never a child's value.** A *series* never carries `SECTOR: "Various"` or a `Various` rating — like the rating rule, it is what the **deal** shows when its children differ. Letting it propagate down gave `PURPOSE: PIT` a sector of `Various` that contradicted every PIT issuer's own General Purposes sector. Same trap as the enhancement one below: an aggregate taken as a mode.
- **Munis: `ENHANCEMENT` and `SOURCE_OF_REPAYMENT` are gated, not free.** "Pennsylvania State Aid Intercept Program" is 41/41 PA and "Assured Guaranty Inc" 53/53 CA (`ENHANCEMENT_STATES`); `SOURCE_OF_REPAYMENT` is populated on 13/2,142 rows and **all 13 are General Purposes**, so it is **omitted** elsewhere — the one field where the payload deviates from the captured sample (§21.3.5), because that sample was hand-filled in the UI. If it turns out to be unconditionally mandatory the NACK will name it.
- **Munis: `MANDATE_TEXT` is mandatory when `IS_ROADSHOW` is true** — NACKs `Mandate text is required for Roadshow.` (a `StandardError`, no PATH). Exactly the ABS §20.3.8 rule, and it reached a live NACK here because the 20% `IS_ROADSHOW` draw was copied from `securitized_engine` **without** the conditional rule attached to it. When porting a randomised flag between engines, port its rules first. The `roadshow` param (`yes`/`no`/blank) exists so the branch can be pinned instead of hit 1-in-5; the engine asserts presence as an **equivalence**, not an implication.
- **Munis dates are UTC-midnight epoch ms.** Every date in the capture is exactly UTC midnight; a naive local datetime shifts it across the dateline and stores the wrong business date. `_epoch_ms` is the only place this conversion happens.

- **A randomised field makes a deterministic schema rule look intermittent.** `IS_ROADSHOW` is drawn at ~20%, which is why the `MANDATE_TEXT` requirement surfaced on one live step and vanished on its re-run. When a NACK appears on a step whose axis shouldn't touch the failing rule, **diff the two payloads** rather than re-running — a re-run of an unseeded generator is not a controlled experiment.

## When making changes

- Keep `master/*.md` in sync — especially `implementation.md` (add to the dated changelog) and `spec.md` (parameters/behaviour).
- After frontend edits, run `npm run build` to catch JSX errors.
- After any Python/JSX edit, run `graphify update .` (free, AST-only) so the knowledge graph does not go stale.
- After engine edits, smoke-test in **dry-run** (no server needed): instantiate the engine with a `queue.Queue` + `threading.Event` and `dry_run: True`, then inspect the emitted payloads.
- Prefer threading new behaviour through the existing `params` dict over adding endpoints.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

Scope in this repo — 66 files, 1367 nodes, 2670 edges, 64 communities, **0 LLM tokens** to build or refresh:
- **Code** — `backend/**/*.py` and `frontend/src/**/*.jsx`, parsed by tree-sitter. Backend coverage
  is dense (`securitized_engine.py` and `comparators.py` are the two largest, 81+ nodes each,
  then `expected_store.py` 74, `email_builder.py` 65); **JSX coverage is thin** (4–8 nodes per
  panel), so frontend questions still want a real read.
- **Docs** — `master/{spec,plan,implementation}.md` and this file are indexed *structurally*
  (heading hierarchy + line numbers, `_origin: ast`). `graphify explain "18.8 Comparison and
  scoring"` gives you the section's children and `spec.md:L984` to jump to. This replaces *scanning*
  the 240 KB spec, not reading the section you land on. `spec.md` §20 (ABS) alone is 67 heading
  nodes, so `graphify explain "20.6 Internal consistency"` is much cheaper than reading it.
- **Refresh with `graphify update .`, not `extract --code-only`** — `update` re-indexes code *and*
  markdown for free. `--code-only` skips the docs (it is how the first build was made, and why the
  graph was worse).
- Community labels are placeholders (`Community 3`, …) where a filename or heading didn't supply
  one; `graphify label .` names them via an LLM backend.
- `.graphifyignore` excludes `frontend/dist/`, `__pycache__/`, `.venv/` and runtime state
  (`history.json`, `expected/`). Keep it current or the graph fills with bundled output.
- `graphify-out/` is generated (~2 MB) and git-ignored — never hand-edit it, rebuild instead.
- **`graphify affected "X"` does not work here** — cross-file call edges are unresolved, so it
  reports "No affected nodes found" even for functions with known importers. Use `explain` (accurate,
  within-file) or `query`, and fall back to grep for true impact analysis.
- **Run `graphify update .` from the repo root only.** From a subdirectory it rebuilds the graph
  scoped to that subtree and silently discards everything else — running it in `backend/` took the
  graph from 1350 nodes to 864, dropping the frontend and all of `master/*.md`, with no error and a
  cheerful "Rebuilt" line. Re-running from the root restores it. A stale `backend/graphify-out/` is
  the fingerprint of someone having done this; it is safe to delete.
