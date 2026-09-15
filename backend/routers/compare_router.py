"""The ``/api/compare`` surface of spec section 18.11 — the Email Comparison
(LLM accuracy) harness.

Everything the Email Compare tab does goes through here:

* **runs** — the capture runs ``bonds_engine`` wrote at email-generation time,
  with their row counts and latest scores;
* **fetch / import** — pull the rows the pipeline actually produced from the app
  DB (``db_reader``), or take them as CSV exports when there is no connection;
* **compare** — one pass of ``comparators.compare_run``, persisted to
  ``util_comparison`` / ``util_comparison_field`` and re-runnable in place;
* **diff** — read the persisted findings back for the result panel.

Three conventions worth knowing before editing:

* The **maps are loaded once per request** (``effective_map``) and passed into
  the comparator as ``maps=``. The comparator is called per row pair and would
  otherwise re-read the seed CSVs on every row.
* **Nothing coerces a value.** Rows are text on both sides; the comparator
  absorbs ``'True'`` vs ``'true'``, ``1000.00000`` vs ``1000`` and epoch-ms vs
  ISO dates. Coercing here would only lose information.
* A row that is not a known app column is **dropped with a warning**, never
  rejected: ``expected_store.insert_rows`` refuses unknown columns by design
  (a typo must not silently lose an expectation), but a real export may
  legitimately carry columns the schema snapshot predates.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import APIRouter, Body, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import PlainTextResponse

import comparators
import db_reader
import email_ingest
import expected_store
import label_form
from engines import expected_writer
from config_manager import get_active_env, load_environments

router = APIRouter()

TABLES: Tuple[str, ...] = comparators.TABLES
LANES = ("expected", "actual")

# Columns of one table that no *other* table has — the strongest signal for
# working out which of the four an uploaded CSV is.
_UNIQUE_COLUMNS: Dict[str, set] = {}


def _unique_columns() -> Dict[str, set]:
    if not _UNIQUE_COLUMNS:
        sets = {t: {c.casefold() for c in expected_store.app_columns(t)} for t in TABLES}
        for table in TABLES:
            others: set = set()
            for other in TABLES:
                if other != table:
                    others |= sets[other]
            _UNIQUE_COLUMNS[table] = sets[table] - others
    return _UNIQUE_COLUMNS


# ── shared helpers ───────────────────────────────────────────────────────────

def _envs() -> Dict[str, Any]:
    """Config, with the store path applied. Reads never create the store."""
    envs = load_environments()
    expected_store.set_store_path((envs.get("compare") or {}).get("store_path") or None)
    return envs


def _compare_config(envs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return ((envs or _envs()).get("compare") or {})


def _require_run(util_run_id: str) -> Dict[str, Any]:
    run = expected_store.get_run(util_run_id)
    if not run:
        raise HTTPException(404, f"No capture run {util_run_id!r} in "
                                 f"{expected_store.store_path()}")
    return run


def _asset_class(run: Dict[str, Any]) -> str:
    return run.get("util_asset_class") or "bonds"


def _env_for_run(run: Dict[str, Any], envs: Dict[str, Any]) -> Dict[str, Any]:
    """The environment the run was captured against, falling back to the active
    one (the run may name an environment that has since been renamed)."""
    named = (envs.get("environments") or {}).get(run.get("env") or "")
    return named or get_active_env(envs)


def _is_upload(run: Dict[str, Any]) -> bool:
    """Whether this run came from uploaded emails rather than a capture (19.5).

    ``tool`` is the marker, so no schema change was needed to introduce uploads.
    """
    return (run.get("tool") or "").strip().lower() == "upload"


def _run_params(run: Dict[str, Any]) -> Dict[str, Any]:
    raw = run.get("params_json") or run.get("params") or {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) or {}
    except (TypeError, ValueError):
        return {}


def upload_identifiers(run: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Every ISIN and CUSIP across an upload run's emails, from its manifest.

    Read from ``params_json`` rather than re-parsed from the saved files: that is
    the one place a hand-pinned identifier will be written, so re-deriving here
    would quietly ignore a correction.
    """
    upload = (_run_params(run).get("upload") or {})
    isins: List[str] = []
    cusips: List[str] = []
    for email in (upload.get("emails") or []):
        ids = email.get("identifiers") or {}
        isins.extend(ids.get("isins") or [])
        cusips.extend(ids.get("cusips") or [])
    return sorted(set(isins)), sorted(set(cusips))


def _lane_rows(util_run_id: str, source: str) -> Dict[str, List[Dict[str, Any]]]:
    return {t: expected_store.query_rows(t, util_run_id=util_run_id, util_source=source)
            for t in TABLES}


def _has_rows(rows: Dict[str, List[Dict[str, Any]]]) -> bool:
    return any(rows.get(t) for t in TABLES)


def _rendered_fields(util_run_id: str) -> List[str]:
    """Union of every email's ``rendered_fields`` — the extraction denominator."""
    out: set = set()
    for email in expected_store.list_emails(util_run_id):
        out |= set(email.get("rendered_fields") or [])
    return sorted(out)


def _email_window(util_run_id: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    stamps: List[datetime] = []
    for email in expected_store.list_emails(util_run_id):
        parsed = _parse_ts(email.get("ts"))
        if parsed:
            stamps.append(parsed)
    if not stamps:
        parsed = _parse_ts((expected_store.get_run(util_run_id) or {}).get("ts"))
        return (parsed, parsed)
    return (min(stamps), max(stamps))


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _project(table: str, raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Keep only real app columns, matched case-insensitively (18.11 CSV rules).

    Returns ``(row, unknown header names)``. ``NULL`` and empty strings become
    ``None`` so an export round-trips through the store unchanged.
    """
    canon = {c.casefold(): c for c in expected_store.app_columns(table)}
    row: Dict[str, Any] = {}
    unknown: List[str] = []
    for key, value in raw.items():
        if key is None:
            continue
        col = canon.get(str(key).strip().casefold())
        if col is None:
            unknown.append(str(key))
            continue
        if isinstance(value, str) and value.strip().upper() in ("", "NULL"):
            value = None
        row[col] = value
    return row, unknown


def _store_lane(util_run_id: str, asset_class: str, lane: str,
                rows_by_table: Dict[str, Sequence[Dict[str, Any]]],
                *, replace: bool = True) -> Dict[str, Any]:
    """Write one lane of fetched/imported rows, replacing what was there."""
    expected_store.init_store()
    inserted: Dict[str, int] = {}
    warnings: List[str] = []
    for table in TABLES:
        rows = list(rows_by_table.get(table) or [])
        if replace:
            expected_store.delete_rows(table, util_run_id=util_run_id, util_source=lane)
        projected: List[Dict[str, Any]] = []
        unknown: set = set()
        for raw in rows:
            row, extra = _project(table, raw)
            unknown |= set(extra)
            if row:
                projected.append(row)
        if unknown:
            warnings.append(f"{table}: ignored {len(unknown)} unknown column(s) — "
                            f"{', '.join(sorted(unknown)[:8])}"
                            f"{' …' if len(unknown) > 8 else ''}")
        count, length_warnings = expected_store.insert_rows(
            table, projected, util_run_id=util_run_id, util_source=lane,
            util_asset_class=asset_class, validate=False)
        warnings.extend(length_warnings)
        inserted[table] = count
    return {"inserted": inserted, "warnings": warnings,
            "total": sum(inserted.values())}


# ── runs ─────────────────────────────────────────────────────────────────────

@router.get("/runs")
async def list_runs(asset_class: str = Query("bonds"),
                    limit: int = Query(200, ge=1, le=1000)) -> Dict[str, Any]:
    """Capture runs for the asset class, with row counts and latest scores."""
    _envs()
    runs = []
    for run in expected_store.list_runs(asset_class, limit=limit):
        counts = expected_store.row_counts(run["util_run_id"])
        latest = expected_store.latest_comparison(run["util_run_id"])
        lanes = {lane: sum(counts[t][lane] for t in TABLES) for lane in LANES}
        emails = expected_store.list_emails(run["util_run_id"])
        run.pop("params_json", None)
        runs.append({
            **run,
            "rows": lanes,
            "rows_by_table": counts,
            "emails": len(emails),
            "send_status": sorted({e.get("send_status") or "" for e in emails} - {""}),
            "has_actual": lanes["actual"] > 0,
            "latest_comparison": None if not latest else {
                "comparison_id": latest["comparison_id"], "ts": latest["ts"],
                "scores": latest["scores"]},
        })
    return {"asset_class": asset_class, "runs": runs,
            "store": expected_store.store_info()}


@router.get("/runs/{util_run_id}")
async def get_run_detail(util_run_id: str) -> Dict[str, Any]:
    """Everything hanging off one capture run: emails, rows, LLM runs, passes."""
    envs = _envs()
    run = _require_run(util_run_id)
    counts = expected_store.row_counts(util_run_id)
    try:
        run["params"] = json.loads(run.pop("params_json", "") or "{}")
    except (TypeError, ValueError):
        run["params"] = {}
    return {
        "run": run,
        "asset_class": _asset_class(run),
        "emails": expected_store.list_emails(util_run_id),
        "rendered_fields": _rendered_fields(util_run_id),
        "rows_by_table": counts,
        "rows": {lane: sum(counts[t][lane] for t in TABLES) for lane in LANES},
        "comparisons": expected_store.list_comparisons(util_run_id),
        "db": db_reader.availability(_env_for_run(run, envs)),
    }


@router.delete("/runs/{util_run_id}")
async def delete_run(util_run_id: str) -> Dict[str, Any]:
    _envs()
    _require_run(util_run_id)
    deleted = expected_store.delete_run(util_run_id)
    # The store knows nothing about the saved .msg files, so deleting a run left
    # its upload directory behind — 41 orphans accumulated during one session of
    # testing. Best effort: a run must still delete if the files are locked or gone.
    removed_files = 0
    try:
        directory = expected_store.store_path().parent / "uploads" / util_run_id
        if directory.is_dir():
            removed_files = sum(1 for _ in directory.iterdir())
            shutil.rmtree(directory, ignore_errors=True)
    except Exception:
        pass
    return {"deleted": deleted, "files_removed": removed_files}


# ── the database connection (panel 4 / Settings "Test connection") ───────────

@router.get("/db")
async def db_status(env: Optional[str] = Query(None)) -> Dict[str, Any]:
    """Whether "Fetch from DB" can be offered, and against what."""
    envs = _envs()
    target = (envs.get("environments") or {}).get(env or "") or get_active_env(envs)
    return {"env": env or envs.get("active", ""), **db_reader.availability(target)}


@router.post("/db/test")
async def db_test(body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """``SELECT 1`` + a visibility probe of the four tables. Read-only."""
    envs = _envs()
    name = body.get("env")
    target = (envs.get("environments") or {}).get(name or "") or get_active_env(envs)
    return {"env": name or envs.get("active", ""), **db_reader.test_connection(target)}


# ── loading the application's rows: fetch and import ────────────────────────

@router.post("/runs/{util_run_id}/fetch")
async def fetch_from_db(util_run_id: str,
                        body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Pull the agent's rows for this run from the application database.

    A missing driver or a disabled connection is **not** an error: the response
    says so and points at the CSV import route, which is what the UI shows.
    """
    envs = _envs()
    run = _require_run(util_run_id)

    # An uploaded set is matched on identifiers instead: a real issuer's ticker
    # has years of history in these tables, and the agent's ingest time is
    # unknowable from a saved file (19.8).
    if _is_upload(run):
        isins, cusips = upload_identifiers(run)
        try:
            result = db_reader.fetch_rows_by_identifier(
                _env_for_run(run, envs), isins=isins, cusips=cusips)
        except db_reader.DbUnavailable as exc:
            return {"ok": False, "reason": str(exc), "fallback": "import",
                    "message": f"Cannot query the database — {exc}"}
        stored = _store_lane(util_run_id, _asset_class(run), "actual", result["rows"])
        return {"ok": True, "target": result["target"],
                "matched_on": "identifier",
                "identifiers": {"isins": len(result["isins"]),
                                "cusips": len(result["cusips"])},
                "matched": result["matched"], "counts": result["counts"],
                "truncated": result["truncated"],
                "stored": stored["inserted"], "total": stored["total"],
                "warnings": list(result["warnings"]) + stored["warnings"]}

    cfg = _compare_config(envs)
    window_minutes = int(body.get("window_minutes")
                         or cfg.get("ingest_window_minutes")
                         or db_reader.DEFAULT_WINDOW_MINUTES)
    first, last = _email_window(util_run_id)

    # A run mints one ticker per deal. `util_run.ticker` holds the whole set,
    # but fall back to the emails' own tickers — that covers a run stopped
    # before it could write the set back, and any run captured earlier.
    tickers = db_reader.split_tickers(run.get("ticker") or "")
    if not tickers:
        tickers = db_reader.split_tickers(
            [e.get("ticker") for e in expected_store.list_emails(util_run_id)])

    try:
        result = db_reader.fetch_rows(
            _env_for_run(run, envs), tickers,
            sent_at=first, last_sent_at=last, window_minutes=window_minutes)
    except db_reader.DbUnavailable as exc:
        return {"ok": False, "reason": str(exc), "fallback": "import",
                "message": f"Cannot query the database — {exc} "
                           f"Upload the CSV exports instead."}

    stored = _store_lane(util_run_id, _asset_class(run), "actual", result["rows"])
    return {"ok": True, "target": result["target"], "ticker": result["ticker"],
            "tickers": result["tickers"],
            "window": result["window"], "counts": result["counts"],
            "truncated": result["truncated"],
            "stored": stored["inserted"], "total": stored["total"],
            "warnings": list(result["warnings"]) + stored["warnings"]}


@router.post("/runs/{util_run_id}/import")
async def import_csv(util_run_id: str,
                     files: List[UploadFile] = File(...),
                     lane: str = Form("actual"),
                     tables: Optional[str] = Form(None),
                     replace: bool = Form(True)) -> Dict[str, Any]:
    """CSV fallback — 1 to 4 exports in the ``Sample_issuance*.csv`` shape.

    The table each file belongs to is inferred from its header (every one of the
    four has columns no other has); ``tables`` overrides that with a
    comma-separated list parallel to ``files``.
    """
    _envs()
    run = _require_run(util_run_id)
    if lane != "actual" and not (lane == "expected" and _is_upload(run)):
        # A capture run's expected lane is generated by the utility, so importing
        # one would overwrite ground truth with a file. An **upload** run's expected
        # lane is hand-authored, and restoring it from an export is the whole point
        # of B4 — so that lane, and only that lane, may be imported.
        raise HTTPException(400, "lane must be 'actual'. The expected rows of a "
                                 "capture run are written by the utility when it "
                                 "generates the email; only an uploaded set's "
                                 "expected lane can be imported.")
    named = [t.strip() for t in (tables or "").split(",") if t.strip()]
    if named and len(named) != len(files):
        raise HTTPException(400, "tables must have one entry per uploaded file")

    by_table: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TABLES}
    detected: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for i, upload in enumerate(files):
        raw = await upload.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        # Headers come from the reader, not from the first row: a lane that is
        # empty for one table exports as a header-only file, and that file must
        # still identify its table so a full-lane export re-imports without a
        # "could not tell which table this is" warning (found in S7).
        headers = list(reader.fieldnames or [])
        table = named[i] if named else _infer_table(headers)
        if table not in TABLES:
            warnings.append(f"{upload.filename}: could not tell which table this is "
                            f"(headers: {', '.join(headers[:6])}…) — skipped.")
            detected.append({"file": upload.filename, "table": None, "rows": 0})
            continue
        by_table[table].extend(rows)
        detected.append({"file": upload.filename, "table": table, "rows": len(rows)})

    stored = _store_lane(util_run_id, _asset_class(run), lane, by_table,
                         replace=bool(replace))
    return {"ok": True, "lane": lane, "files": detected,
            "stored": stored["inserted"], "total": stored["total"],
            "warnings": warnings + stored["warnings"]}


MAX_UPLOAD_FILES = 200
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("/uploads")
async def create_upload(files: List[UploadFile] = File(...),
                        label: Optional[str] = Form(None),
                        asset_class: str = Form("bonds")) -> Dict[str, Any]:
    """Create a run from a set of **already-existing** emails (spec 19.5).

    Deliberately small: it saves the files, parses each one, and writes the same
    ``util_run`` + ``util_email`` rows a capture run writes. **No new table, no new
    column, no new config** — ``util_run.tool = 'upload'`` is what distinguishes it,
    and every existing endpoint (``/runs``, ``/runs/{id}``, ``/fetch``, ``/compare``,
    ``/diff``, ``/export``, ``DELETE``) then works on it unchanged.

    No expected rows are written here. Ground truth for an uploaded email is
    authored by a human later (D10), so a fresh upload run is *unlabelled* and
    ``/compare`` has nothing to score yet — which it reports as a reason rather
    than as 0%.

    The extracted identifiers ride in ``params_json`` under ``upload``, which
    ``get_run_detail`` already deserialises, so the UI needs no new endpoint. That
    is also the single place a later hand-pinned identifier will be written.
    """
    _envs()
    if not files:
        raise HTTPException(400, "no files uploaded")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(400, f"{len(files)} files — the limit is "
                                 f"{MAX_UPLOAD_FILES} per upload")

    run_id = expected_store.new_run_id()
    target_dir = expected_store.store_path().parent / "uploads" / run_id
    target_dir.mkdir(parents=True, exist_ok=True)

    manifest: List[Dict[str, Any]] = []
    warnings: List[str] = []
    tickers: List[str] = []
    parsed_emails: List[Tuple[Any, Path]] = []

    for index, upload in enumerate(files, start=1):
        original = upload.filename or f"email-{index}"
        suffix = Path(original).suffix.lower()
        if suffix not in email_ingest.SUPPORTED_SUFFIXES:
            warnings.append(f"{original}: unsupported file type {suffix or '(none)'} "
                            f"— skipped.")
            continue
        raw = await upload.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            warnings.append(f"{original}: {len(raw) // 1024}kB exceeds the "
                            f"{MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit — skipped.")
            continue
        # Saved under an index-derived name, never the uploaded one: a filename is
        # attacker-controlled and this writes to disk. The original is kept in the
        # manifest for display.
        saved = target_dir / f"{index:03d}{suffix}"
        saved.write_bytes(raw)
        try:
            parsed = email_ingest.parse_email(saved, name=original)
        except Exception as exc:                     # one bad file must not fail 30
            warnings.append(f"{original}: could not be parsed — {exc}")
            continue
        parsed_emails.append((parsed, saved))
        tickers.extend(parsed.identifiers.tickers[:1])

    if not parsed_emails:
        # The directory is created before parsing, so a rejected upload would leave
        # an empty orphan behind — no run exists to ever clean it up.
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(400, "none of the uploaded files could be read. "
                                + " ".join(warnings))

    expected_store.add_run({
        "util_run_id": run_id,
        "tool": "upload",
        "util_asset_class": asset_class or "bonds",
        "email_format": "uploaded",
        "email_mode": "none",
        # One ticker per email where there is one, deduplicated — the same shape
        # `/fetch` already reads (19.8), so ticker matching still works as a
        # fallback for an email whose identifiers are missing.
        "ticker": ",".join(sorted({t for t in tickers if t})),
        "notes": (label or "").strip() or f"{len(parsed_emails)} uploaded emails",
        # `deals` and `tranches` stay unset on purpose: how many deals an email
        # describes is a labelling input, not something the upload can know (19.17).
        "params": {"upload": {"label": (label or "").strip(),
                              "files": len(parsed_emails)}},
    })

    for seq, (parsed, saved) in enumerate(parsed_emails, start=1):
        ids = parsed.identifiers
        email_id = expected_store.add_email({
            "util_run_id": run_id,
            "util_deal_seq": seq,
            # The email's own sent date, not now(). It says nothing about when the
            # agent ingested it (19.8) but it is the only true timestamp here.
            "ts": parsed.sent_at.isoformat() if parsed.sent_at else None,
            "subject": parsed.subject or parsed.source_name,
            "body_html": parsed.body_html,
            "email_format": "uploaded",
            "recipient": "",
            "send_status": "uploaded",
            "send_note": parsed.sender,
            "msg_path": str(saved),
            "ticker": (ids.tickers[0] if ids.tickers else None),
            # rendered_fields is left empty: it is the set of columns the *label*
            # fills in (19.7), and nothing is labelled yet.
        })
        manifest.append({
            "util_email_id": email_id, "seq": seq, "file": parsed.source_name,
            "subject": parsed.subject, "sender": parsed.sender,
            "sent_at": parsed.sent_at.isoformat() if parsed.sent_at else None,
            "identifiers": ids.as_dict(), "matchable": ids.matchable,
            "warnings": parsed.warnings,
        })
        warnings.extend(f"{parsed.source_name}: {w}" for w in parsed.warnings)

    # Written after the emails so the per-email ids are in it.
    expected_store.set_run_params(run_id, {"upload": {
        "label": (label or "").strip(), "files": len(parsed_emails),
        "dir": str(target_dir), "emails": manifest}})

    return {"ok": True, "util_run_id": run_id, "asset_class": asset_class,
            "emails": manifest, "labelled": 0,
            "matchable": sum(1 for m in manifest if m["matchable"]),
            "warnings": warnings}


def _require_upload_email(util_run_id: str, util_email_id: str
                          ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """The run, the stored email row, and its manifest entry — or a 404."""
    run = _require_run(util_run_id)
    if not _is_upload(run):
        raise HTTPException(400, "labelling applies to uploaded email sets. A capture "
                                 "run already holds the expectation it generated.")
    email = next((e for e in expected_store.list_emails(util_run_id)
                  if e.get("util_email_id") == util_email_id), None)
    if not email:
        raise HTTPException(404, f"no email {util_email_id!r} in run {util_run_id!r}")
    manifest = next((m for m in (_run_params(run).get("upload") or {}).get("emails", [])
                     if m.get("util_email_id") == util_email_id), {})
    return run, email, manifest


@router.get("/uploads/{util_run_id}/emails/{util_email_id}/label")
async def get_label(util_run_id: str, util_email_id: str,
                    full: bool = Query(False)) -> Dict[str, Any]:
    """The labelling form for one uploaded email (spec 19.6).

    Returns the field list, whatever has already been stated, and **suggestions**
    read from the email itself. Suggestions are kept in their own key rather than
    merged into the label: a suggestion is not an answer, and only a save commits
    one (D11).
    """
    _envs()
    run, email, manifest = _require_upload_email(util_run_id, util_email_id)
    maps = expected_store.effective_map(_asset_class(run))
    stored = expected_store.get_email_label(util_email_id) or {}
    identifiers = manifest.get("identifiers") or {}
    return {
        "util_run_id": util_run_id,
        "util_email_id": util_email_id,
        "subject": email.get("subject"),
        "body_html": email.get("body_html"),
        "identifiers": identifiers,
        "groups": label_form.build_form(maps, full=bool(full)),
        "label": stored.get("label") or {},
        "status": stored.get("label_status") or "unlabelled",
        "rendered_fields": stored.get("rendered_fields") or [],
        "suggestions": label_form.suggest(email.get("body_html") or "",
                                          email.get("body_text") or "",
                                          identifiers),
    }


@router.put("/uploads/{util_run_id}/emails/{util_email_id}/label")
async def put_label(util_run_id: str, util_email_id: str,
                    body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Save a label and write the expected rows it implies (spec 19.6, 19.7).

    Idempotent by replacement: an email's previous expectation is deleted first, so
    relabelling cannot leave half of an older answer behind. Scoped to the email, so
    it never disturbs the other twenty-nine.

    ``rendered_fields`` is set from what the label actually filled — that is the
    accuracy denominator (19.7), and it is why leaving a field blank costs the agent
    nothing rather than scoring as a miss.
    """
    _envs()
    run, email, _manifest = _require_upload_email(util_run_id, util_email_id)
    asset_class = _asset_class(run)
    label = body.get("label") or body
    if not isinstance(label, dict) or not label.get("deals"):
        raise HTTPException(400, "a label needs at least one deal — "
                                 '{"deals": [{"deal": {...}, "tranches": [...]}]}')

    try:
        return _apply_label(util_run_id, email, label, asset_class)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _apply_label(util_run_id: str, email: Dict[str, Any], label: Dict[str, Any],
                 asset_class: str) -> Dict[str, Any]:
    """Project a label, replace that email's expectation, and store both.

    Shared by the form's save and by the label import (B4), so a restored label
    produces byte-identical rows to the one that was exported. Raises ``ValueError``
    for anything the caller should report as a bad request.
    """
    util_email_id = email["util_email_id"]
    maps = expected_store.effective_map(asset_class)
    try:
        built = expected_writer.from_label(label, asset_class=asset_class, maps=maps)
    except Exception as exc:
        raise ValueError(f"could not project this label — {exc}") from exc

    expected_store.delete_rows_for_email(util_email_id, "expected")

    # Deal sequences are offset by the email's own sequence so two emails cannot
    # both claim deal 1 — the diff groups by (deal_seq, tranche_seq), and a
    # collision would merge two different deals into one tree.
    offset = int(email.get("util_deal_seq") or 1) * 100
    inserted: Dict[str, int] = {}
    warnings: List[str] = list(built["warnings"])
    for table, rows in built["rows"].items():
        for row in rows:
            row["util_deal_seq"] = offset + int(row.get("util_deal_seq") or 1)
        if not rows:
            continue
        try:
            count, length_warnings = expected_store.insert_rows(
                table, rows, util_run_id=util_run_id, util_source="expected",
                util_asset_class=asset_class, util_email_id=util_email_id)
        except KeyError as exc:
            raise ValueError(f"{table}: {exc}") from exc
        inserted[table] = count
        warnings.extend(length_warnings)

    rendered = built["rendered_fields"]
    expected_store.set_email_label(util_email_id, util_run_id, label,
                                   rendered_fields=rendered)
    # `rendered_fields` also lives on util_email, which is where the comparator
    # reads the denominator from (`_rendered_fields`).
    expected_store.add_email({**{k: v for k, v in email.items()
                                 if k not in ("rendered_fields",)},
                              "rendered_fields": rendered})

    return {"ok": True, "util_email_id": util_email_id,
            "counts": built["counts"], "inserted": inserted,
            "rendered_fields": rendered, "warnings": warnings}


LABEL_EXPORT_VERSION = 1


@router.get("/uploads/{util_run_id}/labels")
async def export_labels(util_run_id: str) -> Dict[str, Any]:
    """Every label in a run, as a portable document (spec 19.9, build step B4).

    **This is the sidecar file the user does not have today, coming back out of the
    feature.** Twenty to fifty emails are labelled once; the export is versioned, and
    re-importing it into a fresh store rebuilds the whole expectation without anyone
    reading an email again.

    Each entry carries its **identifiers** as the match key, not the store's row ids:
    those change when the same emails are uploaded into a different store, and an
    export that only round-trips on the machine that made it is not a backup.
    """
    _envs()
    run = _require_run(util_run_id)
    if not _is_upload(run):
        raise HTTPException(400, "only an uploaded email set has labels — a capture "
                                 "run's expectation is generated, not authored.")
    manifest = {m.get("util_email_id"): m
                for m in (_run_params(run).get("upload") or {}).get("emails", [])}
    entries = []
    for stored in expected_store.list_email_labels(util_run_id):
        entry = manifest.get(stored["util_email_id"], {})
        identifiers = entry.get("identifiers") or {}
        entries.append({
            "file": entry.get("file"),
            "subject": entry.get("subject"),
            "match": {"isins": identifiers.get("isins") or [],
                      "cusips": identifiers.get("cusips") or []},
            "rendered_fields": stored.get("rendered_fields") or [],
            "label": stored.get("label") or {},
        })
    return {"version": LABEL_EXPORT_VERSION,
            "asset_class": _asset_class(run),
            "util_run_id": util_run_id,
            "run_label": run.get("notes") or "",
            "labels": entries}


@router.post("/uploads/{util_run_id}/labels/import")
async def import_labels(util_run_id: str,
                        body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Restore labels exported from any run (B4).

    Emails are matched by **shared identifier** first, then by subject, then by file
    name — in that order, because identifiers survive a re-upload and row ids do not.
    An entry that matches nothing is reported rather than dropped: a partial restore
    that says which emails it could not place is useful; a silent one is not.
    """
    _envs()
    run = _require_run(util_run_id)
    if not _is_upload(run):
        raise HTTPException(400, "labels can only be imported into an uploaded set.")
    document = body.get("labels") if isinstance(body, dict) else None
    if document is None and isinstance(body, list):
        document = body
    if not isinstance(document, list) or not document:
        raise HTTPException(400, 'expected {"labels": [...]} from a label export.')
    version = int((body or {}).get("version") or LABEL_EXPORT_VERSION)
    if version > LABEL_EXPORT_VERSION:
        raise HTTPException(400, f"this export is version {version}; this build "
                                 f"understands {LABEL_EXPORT_VERSION}.")

    asset_class = _asset_class(run)
    emails = expected_store.list_emails(util_run_id)
    manifest = {m.get("util_email_id"): m
                for m in (_run_params(run).get("upload") or {}).get("emails", [])}

    applied, skipped, warnings = [], [], []
    used: set = set()
    for entry in document:
        label = entry.get("label") or {}
        if not label.get("deals"):
            skipped.append({"file": entry.get("file"), "reason": "no deals in label"})
            continue
        target = _match_email(entry, emails, manifest, used)
        if not target:
            skipped.append({"file": entry.get("file"),
                            "reason": "no email in this run shares its identifiers, "
                                      "subject or file name"})
            continue
        used.add(target["util_email_id"])
        try:
            result = _apply_label(util_run_id, target, label, asset_class)
        except ValueError as exc:
            skipped.append({"file": entry.get("file"), "reason": str(exc)})
            continue
        applied.append({"file": entry.get("file"),
                        "util_email_id": target["util_email_id"],
                        "rendered_fields": len(result["rendered_fields"])})
        warnings.extend(result["warnings"])

    return {"ok": True, "applied": applied, "skipped": skipped,
            "warnings": sorted(set(warnings))[:10]}


def _match_email(entry: Dict[str, Any], emails: List[Dict[str, Any]],
                 manifest: Dict[str, Dict[str, Any]], used: set
                 ) -> Optional[Dict[str, Any]]:
    """Which email in this run an exported label belongs to."""
    wanted = {str(v).strip().upper()
              for key in ("isins", "cusips")
              for v in (entry.get("match") or {}).get(key, []) if str(v).strip()}
    candidates = [e for e in emails if e["util_email_id"] not in used]

    if wanted:
        for email in candidates:
            ids = (manifest.get(email["util_email_id"], {}).get("identifiers") or {})
            have = {str(v).strip().upper()
                    for key in ("isins", "cusips") for v in ids.get(key, [])}
            if have & wanted:
                return email
    subject = (entry.get("subject") or "").strip()
    if subject:
        for email in candidates:
            if (email.get("subject") or "").strip() == subject:
                return email
    filename = (entry.get("file") or "").strip()
    if filename:
        for email in candidates:
            if (manifest.get(email["util_email_id"], {}).get("file") or "") == filename:
                return email
    return None


@router.delete("/uploads/{util_run_id}/emails/{util_email_id}/label")
async def clear_label(util_run_id: str, util_email_id: str) -> Dict[str, Any]:
    """Unlabel an email — drops its expectation and its stored label."""
    _envs()
    _require_upload_email(util_run_id, util_email_id)
    removed = expected_store.delete_rows_for_email(util_email_id, "expected")
    expected_store.delete_email_label(util_email_id)
    email = next((e for e in expected_store.list_emails(util_run_id)
                  if e.get("util_email_id") == util_email_id), None)
    if email:
        expected_store.add_email({**{k: v for k, v in email.items()
                                     if k != "rendered_fields"},
                                  "rendered_fields": []})
    return {"ok": True, "rows_removed": removed}


def _infer_table(headers: Sequence[str]) -> Optional[str]:
    """Which of the four tables an export is, from its header row.

    Scored on the columns unique to each table first (``datasource_id`` only
    exists on ``issuance_data``, ``issuance_created_by`` only on ``issuance``,
    and so on), falling back to plain overlap. ``issuance`` and
    ``issuance_data`` share 133 columns, so the overlap alone is not enough.
    """
    have = {str(h).strip().casefold() for h in headers if h}
    if not have:
        return None
    best, best_score = None, (0, 0.0)
    for table in TABLES:
        cols = {c.casefold() for c in expected_store.app_columns(table)}
        unique = len(have & _unique_columns()[table])
        overlap = len(have & cols) / max(1, len(have))
        if (unique, overlap) > best_score:
            best, best_score = table, (unique, overlap)
    return best if best_score[1] >= 0.5 else None


# ── the compare pass ─────────────────────────────────────────────────────────

@router.post("/runs/{util_run_id}/compare")
async def run_compare(util_run_id: str,
                      body: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    """Score one capture run and persist the pass (18.8).

    Idempotent: re-running replaces the previous pass and its findings rather
    than accumulating them, so fixing a vocab_map entry and comparing again
    improves the stored number instead of duplicating it.
    """
    envs = _envs()
    run = _require_run(util_run_id)
    asset_class = _asset_class(run)

    expected = _lane_rows(util_run_id, "expected")
    actual = _lane_rows(util_run_id, "actual")
    if not _has_rows(expected):
        raise HTTPException(400, "This run captured no expected rows — nothing to "
                                 "compare against.")
    if not _has_rows(actual):
        raise HTTPException(400, "No rows from the application yet. Fetch from the "
                                 "database or import the CSV exports first.")

    result = comparators.compare_run(
        expected, actual,
        maps=expected_store.effective_map(asset_class),   # loaded once per pass
        asset_class=asset_class, config=_compare_config(envs),
        rendered_fields=_rendered_fields(util_run_id))

    expected_store.init_store()
    comparison_id = expected_store.add_comparison({
        "util_run_id": util_run_id,
        "scores": result["scores"],
        "notes": body.get("notes"),
    }, findings=result["findings"])

    return {"comparison_id": comparison_id, "util_run_id": util_run_id,
            "asset_class": asset_class,
            "scores": result["scores"], "tables": result["tables"],
            "worst_columns": result["worst_columns"],
            "findings": len(result["findings"])}


@router.get("/runs/{util_run_id}/diff")
async def get_diff(util_run_id: str,
                   table: Optional[str] = Query(None),
                   comparison_id: Optional[str] = Query(None),
                   verdict: Optional[str] = Query(None),
                   tier: Optional[str] = Query(None),
                   problems_only: bool = Query(False),
                   limit: int = Query(5000, ge=1, le=50000)) -> Dict[str, Any]:
    """Field-level diff rows of a persisted pass. Filters mirror panel 3."""
    _envs()
    _require_run(util_run_id)
    if table and table not in TABLES:
        raise HTTPException(400, f"table must be one of {TABLES}")
    pass_ = (expected_store.get_comparison(comparison_id) if comparison_id
             else expected_store.latest_comparison(util_run_id))
    if not pass_:
        raise HTTPException(404, "No comparison pass for this run yet — run a "
                                 "compare first.")
    verdicts = [v.strip() for v in verdict.split(",")] if verdict else None
    if problems_only:
        problems = [v for v in comparators.VERDICTS
                    if v not in (comparators.MATCH, comparators.NOT_COMPARED)]
        verdicts = [v for v in (verdicts or problems) if v in problems]
    rows = expected_store.list_findings(
        comparison_id=pass_["comparison_id"], table=table, verdicts=verdicts,
        limit=limit)
    if tier:
        wanted = {t.strip() for t in tier.split(",")}
        rows = [r for r in rows if (r.get("tier") or "") in wanted]

    # Provenance (D17) is attached on read rather than stored: it is a property of
    # `field_map.csv`, not of the pass, so correcting the map fixes old passes too
    # and no migration is needed. Without it a derived mismatch reads as "the agent
    # got this wrong" when the agent was never involved (19.20.1).
    fields = expected_store.effective_map(_asset_class(_require_run(util_run_id)))["fields"]
    for row in rows:
        entry = fields.get((row.get("table") or "", row.get("column_name")
                            or row.get("column") or ""))
        row["provenance"] = comparators.provenance_of(
            (entry or {}).get("source_kind"))

    return {"comparison_id": pass_["comparison_id"], "ts": pass_["ts"],
            "scores": pass_["scores"], "count": len(rows), "rows": rows,
            "provenance": comparators.PROVENANCE}



@router.get("/export/{util_run_id}")
async def export(util_run_id: str,
                 format: str = Query("json", pattern="^(csv|json)$"),
                 lane: str = Query("expected"),
                 table: Optional[str] = Query(None),
                 comparison_id: Optional[str] = Query(None)):
    """Export one lane's rows, or the diff, as JSON or CSV.

    CSV of a *lane* is per table — the four have different column sets, so one
    flat file would be meaningless; ``table`` is therefore required there. CSV
    of the ``diff`` is a single grid and needs no table.
    """
    _envs()
    _require_run(util_run_id)
    if lane not in (*LANES, "diff"):
        raise HTTPException(400, f"lane must be one of {(*LANES, 'diff')}")

    if lane == "diff":
        pass_ = (expected_store.get_comparison(comparison_id) if comparison_id
                 else expected_store.latest_comparison(util_run_id))
        if not pass_:
            raise HTTPException(404, "No comparison pass for this run yet.")
        rows = expected_store.list_findings(comparison_id=pass_["comparison_id"],
                                            table=table, limit=50000)
        if format == "json":
            return {"util_run_id": util_run_id, "comparison_id": pass_["comparison_id"],
                    "scores": pass_["scores"], "rows": rows}
        return _csv_response(rows, f"diff_{util_run_id}.csv")

    if format == "json":
        return {"util_run_id": util_run_id, "lane": lane,
                "rows": _lane_rows(util_run_id, lane)}
    if not table:
        raise HTTPException(400, f"format=csv needs a table — one of {TABLES}")
    if table not in TABLES:
        raise HTTPException(400, f"table must be one of {TABLES}")
    text = expected_store.export_csv(table, util_run_id=util_run_id, util_source=lane)
    return PlainTextResponse(text, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{lane}_{table}_{util_run_id}.csv"'})


def _csv_response(rows: Sequence[Dict[str, Any]], filename: str) -> PlainTextResponse:
    columns: List[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["NULL" if row.get(c) is None else row.get(c) for c in columns])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'})
